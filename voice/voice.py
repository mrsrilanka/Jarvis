"""Jarvis voice loop: wake word -> listen -> transcribe -> ask server -> speak.

Runs next to the server. On Linux it runs in its own Podman container with the
sound card passed in. On macOS, Podman containers can't reach the Mac's
microphone, so run this one natively (see README).
"""
from __future__ import annotations

import logging
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
import yaml

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s voice %(message)s")
log = logging.getLogger("jarvis.voice")

cfg_path = Path(os.getenv("JARVIS_CONFIG", ROOT / "config.yaml"))
if not cfg_path.exists():
    cfg_path = ROOT / "config.example.yaml"
VCFG = yaml.safe_load(cfg_path.read_text()).get("voice", {})
SERVER = os.getenv("JARVIS_SERVER", VCFG.get("server_url", "http://localhost:8080")).rstrip("/")

RATE = 16000
FRAME = 1280  # 80 ms frames, what openWakeWord expects


def post(path: str, body: dict, timeout: float = 5):
    try:
        return httpx.post(f"{SERVER}{path}", json=body, timeout=timeout).json()
    except Exception as e:
        log.warning("server %s failed: %s", path, e)
        return {}


def set_state(state: str, text: str = ""):
    post("/api/state", {"state": state, "text": text})


# ---------------- text to speech ----------------
def pick_tts() -> str:
    choice = VCFG.get("tts", "auto")
    if choice != "auto":
        return choice
    if platform.system() == "Darwin" and shutil.which("say"):
        return "say"
    if shutil.which("piper") and Path(VCFG.get("piper_voice", "")).exists():
        return "piper"
    return "espeak"


TTS = pick_tts()


def speak(text: str):
    if not text:
        return
    if TTS == "say":
        subprocess.run(["say", text])
    elif TTS == "piper":
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            subprocess.run(["piper", "--model", VCFG["piper_voice"], "--output_file", f.name],
                           input=text.encode(), check=False, capture_output=True)
            play_wav(f.name)
    else:
        subprocess.run(["espeak-ng", "-s", "165", text], check=False)


def play_wav(path: str):
    import soundfile as sf
    data, sr = sf.read(path, dtype="float32")
    sd.play(data, sr)
    sd.wait()


def chime():
    """Short rising tone so you know Jarvis is listening."""
    t = np.linspace(0, 0.15, int(RATE * 0.15), False)
    tone = 0.2 * np.sin(2 * np.pi * np.where(t < 0.075, 660, 880) * t)
    sd.play(tone.astype(np.float32), RATE)
    sd.wait()


# ---------------- main loop ----------------
def main():
    from openwakeword.model import Model
    import openwakeword
    from faster_whisper import WhisperModel

    wake = VCFG.get("wake_word", "hey_jarvis")
    threshold = float(VCFG.get("wake_threshold", 0.5))
    log.info("loading wake word model '%s'", wake)
    try:
        openwakeword.utils.download_models([wake])
    except Exception as e:
        log.info("model download skipped: %s", e)
    oww = Model(wakeword_models=[wake], inference_framework="onnx")

    wmodel = VCFG.get("whisper_model", "base.en")
    log.info("loading whisper '%s' (first run downloads it)", wmodel)
    whisper = WhisperModel(wmodel, device="cpu", compute_type="int8")

    max_secs = float(VCFG.get("max_listen_seconds", 10))
    silence_secs = float(VCFG.get("silence_seconds", 1.2))

    audio_q: queue.Queue[np.ndarray] = queue.Queue()

    def cb(indata, frames, t, status):
        audio_q.put(indata[:, 0].copy())

    log.info("TTS engine: %s. Say '%s'…", TTS, wake.replace("_", " "))
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, callback=cb):
        while True:
            frame = audio_q.get()
            scores = oww.predict(frame)
            if max(scores.values()) < threshold:
                continue

            # ---- wake word heard ----
            log.info("wake word (%.2f)", max(scores.values()))
            set_state("listening")
            chime()
            while not audio_q.empty():  # drop audio captured during the chime
                audio_q.get_nowait()

            chunks, quiet, started = [], 0.0, time.time()
            noise_floor = None
            while time.time() - started < max_secs:
                f = audio_q.get()
                chunks.append(f)
                rms = float(np.sqrt(np.mean((f.astype(np.float32) / 32768) ** 2)))
                noise_floor = rms if noise_floor is None else min(noise_floor * 1.02 + 1e-5, rms + 0.002)
                speaking = rms > max(0.012, noise_floor * 3)
                quiet = 0.0 if speaking else quiet + FRAME / RATE
                if quiet >= silence_secs and time.time() - started > 1.0:
                    break

            audio = np.concatenate(chunks).astype(np.float32) / 32768
            set_state("thinking", "Transcribing")
            segments, _ = whisper.transcribe(audio, language="en", beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            log.info("heard: %r", text)
            if not text:
                set_state("idle")
                oww.reset()
                continue

            reply = post("/api/ask", {"text": text}, timeout=60).get("answer", "")
            set_state("speaking", reply)
            speak(reply)
            set_state("idle")
            oww.reset()
            while not audio_q.empty():  # ignore our own voice
                audio_q.get_nowait()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
