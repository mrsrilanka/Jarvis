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

## Prerequisites

### Equipment

| Item | What to use | Notes |
|---|---|---|
| Backend box | A Linux laptop running **Linux Mint 22.x**, or a mini PC with Ubuntu Server 24.04 | Hidden out of sight (for example on top of a kitchen cabinet). Leave a few inches of air around it and keep it away from the stove. |
| Wall screen | Any 1080p monitor with HDMI or USB-C input | No touch needed. |
| Video cable | HDMI (or USB-C) long enough to reach from the box to the screen | Passive HDMI is usually fine to about 5 m; use an active cable for longer runs. |
| Microphone + speaker | **One device by the screen**, not on the box | See the options below. The laptop's own mic and speakers are too far away and muffled with the lid shut. |
| USB extension | Active (powered/repeater) USB extension if the box is more than ~5 m from the screen | A plain USB 2.0 cable is reliable to about 5 m. |
| Network | Wi-Fi or Ethernet with internet access | Needed for the AI brain, weather, traffic, news and clock sync. |

#### Mic and speaker options

Pick one. All are standard USB audio devices, so no driver is needed on Linux.

| Option | Why | Trade-off | Price seen (Nov 2026) |
|---|---|---|---|
| **Jabra Speak2 40** (recommended) | Corded, no battery, 4 mics, ~2.3 m pickup, echo cancellation, small (245 g) | Speakerphone volume, not room-filling music volume | About $145–177 new at Amazon and Walmart; has dropped to about $79 on Amazon sales |
| Anker PowerConf S3 | 6 mics, lower price | Built-in battery stays plugged in permanently | About $80–130 |
| ReSpeaker XVF3800 USB 4-mic array **with case** + small powered speaker | Best far-field pickup (up to 5 m), made for wake words | Two pieces; plug the speaker into the array's audio-out jack so echo cancellation works | About $56–62 for the array, plus a speaker |

Prices change daily; check before buying.

### Prepare Linux Mint (laptop target)

Do this once, before installing Jarvis.

**1. Check the Mint version.** Jarvis needs Podman 4.7 or newer for the `podman compose` command. Mint 22.x includes it; Mint 21.x does not.

```bash
cat /etc/os-release        # want VERSION="22.x"
```

If you are on Mint 21.x, back up your files and upgrade to Mint 22 with Mint's official upgrade tool first.

**2. Update everything and reboot.**

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt autoremove -y
sudo reboot
```

**3. Install the tools and confirm the Podman version.**

```bash
sudo apt install -y git podman podman-compose curl alsa-utils
podman --version           # must be 4.7 or newer
```

**4. Keep the clock correct.** If the laptop's clock battery is dead, the time resets after a long power cut. Turn on internet time sync:

```bash
sudo apt install -y systemd-timesyncd     # skip if already installed
sudo timedatectl set-ntp true
sudo systemctl enable systemd-time-wait-sync.service
timedatectl                # want "System clock synchronized: yes"
```

Then make Wi-Fi connect before anyone logs in: open **Network Connections**, edit your Wi-Fi network, and on the **General** tab tick **All users may connect to this network**.

**5. Keep it running with the lid closed.** Edit `/etc/systemd/logind.conf` and set:

```
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
```

Then, in **Power Management**, set the laptop to never suspend on AC power, and reboot.

**6. Power on after an outage.** In the laptop's BIOS, turn on the option that powers the laptop on when AC power is connected (on ThinkPads usually **Power On with AC Attach**), if your model has it.

**7. Optional: protect the battery.** A battery kept at 100% on a warm cabinet can swell. Many ThinkPads can cap charging at about 80% with TLP:

```bash
sudo apt install -y tlp
```

Set `START_CHARGE_THRESH_BAT0=75` and `STOP_CHARGE_THRESH_BAT0=80` in `/etc/tlp.conf`, then run `sudo tlp start`. Check the battery for bulging every few months.

**8. Check the mic and speaker.** Plug in the speakerphone, then:

```bash
arecord -l                 # note the card name of the USB device
aplay -l
```

The voice loop currently uses the default sound card, which is often the laptop's built-in card. Until a device setting is added to `config.yaml`, point it at the USB device by adding the card name from `arecord -l` to the `voice` service's `environment` in `compose.yaml`, for example `ALSA_CARD: "Speak2"` (use your device's actual name). Then check `podman compose logs -f voice` to confirm it hears you.

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

## 4. Run on a Linux Mint laptop (the wall)

Finish [Prepare Linux Mint](#prepare-linux-mint-laptop-target) first.

The setup script was written for a mini PC with no desktop. Its kiosk takes over the screen directly, so Mint's login screen has to be turned off first:

```bash
git clone <your GitHub repo URL> ~/jarvis && cd ~/jarvis
cp config.example.yaml config.yaml && cp .env.example .env   # fill them in
sudo systemctl disable lightdm        # boot to the kiosk instead of the Mint desktop
sudo ./deploy/setup-minipc.sh "$USER"
podman compose --profile voice up -d --build
```

Make Jarvis wait for the correct time before it starts, so weather, traffic and news don't fail on a wrong date after a power cut:

```bash
systemctl --user edit jarvis.service
```

Add these lines in the editor, save, and close:

```
[Service]
ExecStartPre=/bin/sh -c 'until timedatectl show -p NTPSynchronized --value | grep -qx yes; do sleep 2; done'
```

Then reboot:

```bash
sudo reboot
```

To get the Mint desktop back later, run `sudo systemctl enable lightdm` and reboot.

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
- [x] Hardware list
- [ ] Wall-mount notes
- [ ] `voice.input_device` / `voice.output_device` settings in `config.yaml`
- [ ] Dedicated Linux Mint setup script
