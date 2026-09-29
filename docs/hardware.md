# Hardware and pin map

Target: classic ESP32 NodeMCU 38-pin USB-C board using CP210x USB-serial, Arduino
ESP32 Dev Module. It is not an ESP32-S2, ESP32-S3, or ESP32-C3.

| Function | Connection |
| --- | --- |
| OLED 128x64 I2C, address 0x3C | SDA GPIO21, SCL GPIO22 |
| MAX9814 microphone output | GPIO34 (ADC input) |
| MAX98357A BCLK | GPIO26 |
| MAX98357A LRC/WS | GPIO25 |
| MAX98357A DIN | GPIO27 |
| MAX98357A SD | 3.3 V |
| MAX98357A VIN | 5 V |
| Speaker | 4 Ω, 3 W on amplifier output |

Audio: I2S, 16 kHz, 16-bit, mono. Firmware uses `ESP_I2S.h` / `I2SClass`; do not
replace it with legacy `driver/i2s.h` without a separately approved migration.
The voice WAV is embedded as constant bytes in `popote_voice.h` in flash.

`MIC_READ` returns the ESP32 ADC reading; it is not a calibrated sound-pressure
measurement. `STATUS` reports the OLED and I2S initialization flags.
