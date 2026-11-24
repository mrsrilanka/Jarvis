# Jarvis — home wall assistant

A "Hey Jarvis" voice assistant with a wall dashboard for everyday family questions:
weather, live commute times (school drop-off, gym), news headlines, and anything
else you want to ask. The brain is **Gemini or Claude** — switch with one line.

This is a home project only. It has no access to work systems, clusters or accounts.

## How it works

```
 "Hey Jarvis"            ┌──────────────── jarvis-server (Podman) ────────────────┐
   │                     │  FastAPI                                                │
   ▼                     │   ├─ /api/ask ──► Brain (Gemini or Claude + tools)      │
 voice loop ── text ───► │   │                 ├─ get_weather  → Open-Meteo        │
 (wake word, Whisper,    │   │                 ├─ get_commute  → Google Routes API │
  text-to-speech)        │   │                 └─ get_news     → RSS feeds         │
   ▲      │              │   ├─ /api/dashboard ─► cached weather, commute, news    │
   │      └─ state ────► │   └─ /ws ──► pushes orb state + answers live            │
 speaker                 └─────────────────────────────┬───────────────────────────┘
                                                       ▼
                                   Dashboard (Chromium kiosk on the wall monitor)
```

1. **Wake word** — openWakeWord listens locally for "Hey Jarvis". Nothing leaves the house until then.
2. **Speech to text** — faster-whisper transcribes your question on the device.
3. **Brain** — the server sends the question to Gemini or Claude with three tools, so weather,
   traffic and news always come from live data instead of a guess.
4. **Voice** — the answer is spoken (macOS `say` on the Mac, Piper on Linux).
5. **Dashboard** — the server pushes each step to the wall screen over a WebSocket, so the orb
   listens, thinks and speaks in sync, and the cards refresh every few minutes.

```
jarvis/
├── server/          FastAPI app, data sources, LLM brain, Containerfile
├── voice/           wake word + Whisper + TTS loop, Containerfile (Linux)
├── web/index.html   wall dashboard (1080p landscape, no touch needed)
├── deploy/          mini PC kiosk service and setup script
├── compose.yaml     Podman Compose for both containers
├── config.example.yaml
└── .env.example
```

## 1. Configure

```bash
cp config.example.yaml config.yaml   # your addresses, feeds, provider
cp .env.example .env                 # your API keys
```

Both files are in `.gitignore`, so your addresses and keys never reach GitHub.

In `config.yaml`, set:
- `home.address`, `home.latitude`, `home.longitude`
- each place under `places` (school, gym — add more if you like)
- `llm.provider`: `gemini` or `claude`

### Keys you need

| What | Key | Notes |
|---|---|---|
| Gemini brain | `GEMINI_API_KEY` | Create in Google AI Studio. To bill the Google AI Pro monthly Cloud credit instead, set `use_vertex: true` and your project in `config.yaml`, then run `gcloud auth application-default login`. |
| Claude brain | `ANTHROPIC_API_KEY` | Create in the Claude Console. API use is billed separately from a Claude chat subscription. |
| Live traffic | `GOOGLE_MAPS_API_KEY` | Google Cloud project with the **Routes API** enabled. Restrict the key to that API. |
| Weather, news | none | Open-Meteo and RSS are free. |

Pricing, free tiers and model names change often — check the providers before relying on them.

## 2. Run on your Mac (development)

Podman on macOS runs containers in a VM that can't reach the Mac's microphone,
so on the Mac the **server runs in Podman and the voice loop runs natively**.

```bash
# server + dashboard
podman machine init && podman machine start      # first time only
podman compose up -d --build
open http://localhost:8080

# try it without a mic
curl -s localhost:8080/api/ask -H 'content-type: application/json' \
  -d '{"text":"How long to get to school?"}'

# voice loop (native)
brew install portaudio
python3 -m venv .venv && source .venv/bin/activate
pip install -r voice/requirements.txt
python voice/voice.py          # say "Hey Jarvis"
```

The first voice run downloads the wake-word and Whisper models (a few hundred MB).
macOS will ask for microphone permission for your terminal — allow it.

## 3. Run on the mini PC (the wall)

Tested target: Ubuntu Server 24.04, monitor on HDMI or USB-C, USB speakerphone.

```bash
sudo apt install -y git
git clone <your GitHub repo URL> ~/jarvis && cd ~/jarvis
cp config.example.yaml config.yaml && cp .env.example .env   # fill them in
sudo ./deploy/setup-minipc.sh "$USER"
podman compose --profile voice up -d --build
sudo reboot
```

After the reboot, the containers start on their own and the dashboard opens full-screen.

Useful commands:

```bash
podman compose logs -f server          # what Jarvis heard and answered
podman compose logs -f voice           # wake word / transcription
arecord -l && aplay -l                 # check the mic and speaker are detected
git pull && podman compose --profile voice up -d --build   # update
```

## Tuning

| Problem | Change in `config.yaml` |
|---|---|
| Triggers by accident | raise `voice.wake_threshold` (e.g. 0.6) |
| Doesn't hear you | lower `voice.wake_threshold` (e.g. 0.4) |
| Cuts you off | raise `voice.silence_seconds` |
| Slow transcription | `whisper_model: tiny.en` |
| Mishears words | `whisper_model: small.en` |
| Screen too bright at night | adjust `display.night_start` / `night_end` |

## Swapping the brain

Change `llm.provider` to `gemini` or `claude` and restart the server:

```bash
podman compose restart server
```

Both providers get the same tools and the same spoken-reply instructions in `server/llm.py`.

## Privacy

- Audio is only recorded after the wake word, and transcription happens on the device.
- Only the transcribed question text is sent to Gemini or Claude.
- Addresses live only in your local `config.yaml`.

## Roadmap

- [ ] Web search tool for current questions beyond weather, traffic and news
- [ ] Google Calendar card ("drop-off in 20 minutes, leave now")
- [ ] Morning briefing on a schedule
- [ ] Hardware list and wall-mount notes
