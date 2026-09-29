# Test-Popote-Hardware

Python and firmware for the Popote physical hardware controller.

## Current target

- Board: classic ESP32, Arduino **ESP32 Dev Module** (`esp32:esp32:esp32`).
- Arduino-ESP32 core: 3.3.12.
- Serial: 115200 baud; the usual CP210x port is COM5.
- Source firmware version: 0.1.6; line-oriented protocol: v1.
- I2S: modern `ESP_I2S.h` API. Legacy `driver/i2s.h` is not used.

The generated `esp32/popote_hardware/build/` directory is build output, not the
source of board selection. Its current `build.options.json` selects
`esp32:esp32:esp32` and its generated sdkconfig says `CONFIG_IDF_TARGET="esp32"`.
No ESP32-S2 selection appears in the project files inspected. This checkout
does not currently contain Git metadata, so Git status/diff cannot be used here.

## Run the software diagnostic

Create/activate a Python environment, install `requirements.txt`, then run:

```powershell
python -m pip install -r requirements.txt
python main.py
```

The program searches USB serial descriptors for CP210x, CH340, or Espressif
devices, then performs a PING/PONG handshake before querying identity and
running five hardware checks. Descriptor matching finds candidate ports; PING
confirms that a candidate speaks the Popote protocol.

## Test and build

Unit tests use simulated serial responses and do not require a connected ESP32:

```powershell
python -m unittest discover -s tests -v
```

Compile only (no upload):

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 esp32/popote_hardware
```

Uploading remains a separate, deliberate operation. No upload automation is
implemented by `main.py`.

## Run Popote conversation

The separate `conversation.py` captures the MAX9814 through the ESP32 Wi-Fi
stream, uses Groq Whisper and chat, and speaks with local Piper through the
default Windows playback device. The PC microphone fallback is explicit and is
not enabled automatically. The ESP32 continues to control the OLED face and
retains its audio capability for future use.

Install dependencies and set the API key in the current PowerShell session
(never in source files):

```powershell
python -m pip install -r requirements.txt
$env:GROQ_API_KEY = "your-groq-key"
python conversation.py --check
python conversation.py
```

Personality, voice, and idle timings live in `config/personality.json`.
Curated identity, relationships, likes, project history, goals, and known
capabilities live separately in `config/popote_identity.json`. Persistent
memories are stored outside the project at `%LOCALAPPDATA%\Popote\memory.json`
(or at `POPO_MEMORY_PATH` when set); the short conversation history remains
session-only. Popote saves explicit remember requests and clear first-person
preferences, deduplicates exact repeats, and retrieves a few matching memories
per turn. `PopoteMemory.export_memory()` and `import_memory()` support JSON
backups. Wi-Fi ports and PCM settings live in `config/transport.json`. To enable the ESP32 Wi-Fi link, copy
`esp32/popote_hardware/wifi_secrets.example.h` to the ignored local file
`esp32/popote_hardware/wifi_secrets.h` and set `POPO_WIFI_SSID` and
`POPO_WIFI_PASSWORD` there. Leave `POPO_PC_HOST` empty for UDP discovery, or
set it to the PC's current LAN IPv4 address. Do not commit the local header;
the firmware binary contains its Wi-Fi credentials. Windows Firewall must
allow private-network TCP port 39101 and UDP discovery port 39100.

The microphone stream is mono PCM s16le at 16 kHz. The host wraps it in an
in-memory WAV for Whisper. No recording is kept on disk. Local Piper streams
generated PCM to the default Windows playback device. The current chat model
is `openai/gpt-oss-20b` and transcription model is `whisper-large-v3-turbo`.
`GROQ_CHAT_MODEL` and `GROQ_TRANSCRIPTION_MODEL` can override those IDs. The
Groq Orpheus adapter remains available, but conversation currently uses local
Piper with `es_ES-davefx-medium`.
The ESP32 needs firmware with `FACE_CONTROL`; otherwise the PC voice flow still
runs and OLED control is skipped.

See [`docs/architecture.md`](docs/architecture.md),
[`docs/protocol.md`](docs/protocol.md),
[`docs/hardware.md`](docs/hardware.md), and
[`docs/development.md`](docs/development.md).
