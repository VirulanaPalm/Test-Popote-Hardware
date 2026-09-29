# Architecture

```text
main.py -- hardware diagnostic (unchanged)

conversation.py
  |-- voice/wifi_listener.py -- ESP32 MAX9814 stream
  |-- voice/speech_to_text.py -- Groq Whisper
  |-- ai/client.py -- Groq chat
  |-- voice/piper_tts.py -- local TTS
  |-- voice/text_to_speech.py -- default Windows output
  |-- robot/identity.py -- config/popote_identity.json
  |-- robot/memory.py -- local persistent JSON memory
  `-- robot/state.py -- HardwareController -- ESP32 FaceEngine -- OLED
```

Python owns host-side discovery, serial lifecycle, command timeouts, reporting,
and diagnostics. The ESP32 owns GPIO, OLED, microphone sampling, and I2S/audio.
The protocol module is the Python source of command names and version constants;
matching protocol definitions are implemented in the sketch and described in
`protocol.md`. Face-state commands are additive; legacy diagnostics retain the
same replies.

The current code stays intentionally small. Future components should be added
behind protocol commands/capability identifiers and a focused firmware driver,
with hardware-independent Python tests for parsing and diagnostic outcomes.
Physical-device behavior remains a separate manual hardware test.

Conversation audio is captured by the ESP32 ADC on GPIO34, sent to the PC as
mono PCM s16le at 16 kHz, wrapped as an in-memory WAV, and transcribed with
Groq Whisper. Groq chat produces the response; local Piper streams PCM to the
default Windows playback device. The short conversation history stays in PC
memory. `robot/identity.py` loads a separate, curated JSON profile so identity
and known facts are not tied to the ESP32. `robot/memory.py` stores requested
memories and clear first-person preferences in
`%LOCALAPPDATA%/Popote/memory.json` (override with `POPO_MEMORY_PATH`), retrieves
a few relevant matches, and supports JSON backup import/export. USB serial
remains available for programming, diagnostics, and explicit PC-microphone
fallback.

The dynamic memory is body-independent. New conversation turns are not saved
automatically; Popote saves explicit remember requests and clear declared
preferences, then uses relevant matches in future prompts. Music, camera,
person recognition, and motors are not implemented capabilities.

The persistent TCP stream uses a 9-byte header (`POP1`, type byte, big-endian
payload length) so control, ESP32-to-PC PCM, and PC-to-ESP32 PCM cannot be
confused. UDP broadcast advertises the PC TCP port; an optional `POPO_PC_HOST`
in the ignored local ESP32 settings can bypass discovery. No audio files are
persisted. The ESP32 runs I2S TX on I2S1 while ADC continuous capture reserves
I2S0 for DMA; both use the modern `ESP_I2S.h` TX API and Espressif ADC
continuous capture, not the legacy I2S driver.

## Identification and capabilities

`PING`/`PONG` is the handshake. Additive `IDENTIFY`, `VERSION`,
`PROTOCOL_VERSION`, and `CAPABILITIES` queries report device identity and
supported current features. Python treats these metadata replies as optional so
older firmware can still run the original five diagnostics.

The Wi-Fi firmware capabilities include `WIFI,AUDIO_STREAM`. Configure the
network with the ignored local `wifi_secrets.h`; its checked-in example has no
credentials. PC listening/discovery ports and PCM settings are in
`config/transport.json`.

## Audio loop and stop latency

`I2S.playWAV()` is synchronous/blocking in the installed Arduino-ESP32 API. The
firmware checks the serial input between complete WAV playbacks. Therefore
`AUDIO_STOP` stops the loop after the current 2.881-second clip completes; it is
not an immediate interruption. Immediate stopping would require a different,
verified streaming/task architecture and is not claimed here.

## Board-selection incident

The project sketch build metadata inspected for this update specifies
`esp32:esp32:esp32`, and generated sdkconfig identifies `esp32`. The repository
contains no S2 target, board-selection script, or Arduino project setting. The
prior report that esptool was invoked with an ESP32-S2 chip argument is therefore
not attributable to a source file here. It likely came from the external IDE or
an earlier upload command, but the exact source was not available for
verification. The later explicit Dev Module upload succeeded and verified flash
hashes. Check the selected board/FQBN in Arduino IDE before a future GUI upload;
this project does not alter global Arduino settings.
