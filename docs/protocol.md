# Serial protocol v1

Transport: UTF-8 command lines terminated with `\n`, 115200 baud. Replies are
UTF-8 lines terminated with `\n`. Existing command names and success replies
remain unchanged.

| Command | Current reply/behavior |
| --- | --- |
| `PING` | `PONG` |
| `STATUS` | `POPOTE_HARDWARE_OK` or `POPOTE_HARDWARE_FAIL` |
| `OLED_TEST` | `OLED_TEST_START`, then `OLED_OK` |
| `MIC_READ` | `MIC:<integer ADC reading>` |
| `AUDIO_TEST` | `AUDIO_TEST_START`, plays the voice once, then `AUDIO_OK` |
| `AUDIO_LOOP` | `AUDIO_LOOP_START`, repeats the voice WAV |
| `AUDIO_STOP` | Ends the loop after the current blocking WAV and replies `AUDIO_LOOP_STOP` |
| `HELP` | Lists available command names |
| `FACE <state>` | `FACE_OK:<state>` for a supported face/behavior state |

Additive metadata commands:

| Command | Reply |
| --- | --- |
| `IDENTIFY` | `DEVICE:POPOTE_HARDWARE` |
| `VERSION` | `FIRMWARE_VERSION:0.1.6` |
| `PROTOCOL_VERSION` | `PROTOCOL_VERSION:1` |
| `CAPABILITIES` | `CAPABILITIES:OLED,MIC,AUDIO,AUDIO_LOOP,FACE_CONTROL,WIFI,AUDIO_STREAM` |

Metadata commands are additive and do not change the original command contract.
The additive `FACE` command accepts `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`,
`BORED`, `SLEEPY`, `SLEEPING`, `CURIOUS`, `ANNOYED`, `EXCITED`, and `ERROR`.
It controls only the OLED face state; it does not send audio or change I2S.
An older firmware can reply `UNKNOWN_COMMAND:<command>`; Python treats that
metadata as unavailable and continues with the legacy diagnostic.

The firmware also emits startup messages and an idle `POPOTE_ALIVE` heartbeat.
Consumers must ignore lines that are not the expected reply for their current
request. There is no `AUDIO_ALIVE` message.

## Wi-Fi audio framing

Wi-Fi uses a separate persistent TCP connection, leaving serial protocol v1
unchanged. Each frame starts with 9 bytes: ASCII `POP1`, a one-byte type, and
an unsigned 32-bit big-endian payload length. Type `1` is a UTF-8 control line,
type `2` is PCM from ESP32 to PC, and type `3` is PCM from PC to ESP32. Audio
payloads are mono signed 16-bit little-endian PCM at 16 kHz; senders limit each
audio frame to 1024 bytes.

The PC broadcasts `POPOTE_DISCOVERY_V1:<tcp-port>` over UDP. The ESP32 connects
to the beacon sender; `POPO_PC_HOST` can instead supply the PC's current LAN
IPv4 address. On connect, ESP32 sends a `HELLO` control frame with identity,
firmware version, protocol version, and capabilities. `EVENT:AUDIO_START` and
`EVENT:AUDIO_END` bracket microphone PCM; `AUDIO_OUT_START:<rate>:<channels>:<bits>`
and `AUDIO_OUT_END` bracket playback PCM. The ESP32 responds `AUDIO_OUT_DONE`
after playback. All control and audio payloads are length-framed.

Wi-Fi TCP is intended for a trusted local network and is not encrypted or
authenticated. Serial protocol v1 remains available independently.
