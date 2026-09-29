#pragma once

// Copy to wifi_secrets.h (ignored by Git) and fill in locally.
#define POPO_WIFI_SSID ""
#define POPO_WIFI_PASSWORD ""

// Leave empty to discover the PC server through UDP broadcast.
#define POPO_PC_HOST ""
#define POPO_PC_PORT 39101
#define POPO_DISCOVERY_PORT 39100

// Audio capture format and basic VAD configuration.
#define POPO_AUDIO_SAMPLE_RATE 16000
#define POPO_VAD_THRESHOLD 800
#define POPO_SILENCE_TIMEOUT_MS 800
#define POPO_MAX_RECORDING_MS 12000
#define POPO_PREROLL_MS 250
