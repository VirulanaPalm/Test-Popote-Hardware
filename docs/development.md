# Development, diagnostics, and adding components

## Python diagnostic

Run `python main.py` with the board connected. The host searches USB descriptors
for CP210x, CH340, and Espressif devices, opens the best candidate, then requires
`PING` → `PONG` before issuing hardware tests. USB description matching alone
does not prove a device is an ESP32; the protocol handshake does.
When no candidate port appears, the diagnostic lists the COM ports Windows
reported so cable, driver, and port-selection problems can be separated from
protocol failures.

The five checks are handshake, aggregate status, OLED, microphone ADC response,
and audio playback command completion. Results are internally represented as
`OK`, `FAIL`, `NOT_AVAILABLE`, `TIMEOUT`, or `ERROR`. The audio test confirms the
firmware completed the playback call and returned `AUDIO_OK`; it cannot verify
speaker acoustics without a person listening.

## Tests

Run `python -m unittest discover -s tests -v`. These tests use simulated
connections and need no hardware. The five real-device checks require the ESP32
and attached components. No automated test pretends to validate physical sound,
display pixels, or microphone response quality.

## Compile and upload

Compile for the classic ESP32 Dev Module with Arduino-ESP32 3.3.12:

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 esp32/popote_hardware
```

An upload is a separate hardware operation. Confirm the physical target is the
classic ESP32 Dev Module and the intended serial port before invoking
`arduino-cli upload`; `main.py` has no flash/upload option.

## Add a component

1. Define its electrical interface and pins before implementation.
2. Add a firmware capability only when its driver is present and initialization
   state can be reported honestly.
3. Add protocol constants and document request/reply lines without changing
   existing v1 replies.
4. Add a focused Python controller method and typed diagnostic outcome.
5. Test parsing, timeout, failure, and unavailable cases with simulated serial.
6. Add a hardware procedure documenting wiring and which checks require the
   physical device.
7. Compile and review the diff. Do not infer successful physical behavior from
   compilation alone.
