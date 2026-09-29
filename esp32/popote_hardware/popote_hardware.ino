#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ESP_I2S.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_adc/adc_continuous.h"
#include "popote_voice.h"

#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#else
#define POPO_WIFI_SSID ""
#define POPO_WIFI_PASSWORD ""
#define POPO_PC_HOST ""
#define POPO_PC_PORT 39101
#define POPO_DISCOVERY_PORT 39100
#define POPO_AUDIO_SAMPLE_RATE 16000
#define POPO_ADC_SAMPLE_RATE 20000
#define POPO_VAD_THRESHOLD 800
#define POPO_SILENCE_TIMEOUT_MS 800
#define POPO_MAX_RECORDING_MS 12000
#define POPO_PREROLL_MS 250
#endif

#ifndef POPO_WIFI_SSID
#define POPO_WIFI_SSID ""
#endif
#ifndef POPO_WIFI_PASSWORD
#define POPO_WIFI_PASSWORD ""
#endif
#ifndef POPO_PC_HOST
#define POPO_PC_HOST ""
#endif
#ifndef POPO_PC_PORT
#define POPO_PC_PORT 39101
#endif
#ifndef POPO_DISCOVERY_PORT
#define POPO_DISCOVERY_PORT 39100
#endif
#ifndef POPO_AUDIO_SAMPLE_RATE
#define POPO_AUDIO_SAMPLE_RATE 16000
#endif
#ifndef POPO_ADC_SAMPLE_RATE
#define POPO_ADC_SAMPLE_RATE 20000
#endif
#ifndef POPO_VAD_THRESHOLD
#define POPO_VAD_THRESHOLD 800
#endif
#ifndef POPO_SILENCE_TIMEOUT_MS
#define POPO_SILENCE_TIMEOUT_MS 800
#endif
#ifndef POPO_MAX_RECORDING_MS
#define POPO_MAX_RECORDING_MS 12000
#endif
#ifndef POPO_PREROLL_MS
#define POPO_PREROLL_MS 250
#endif

// Additive protocol metadata; legacy commands and replies remain unchanged.
static constexpr char FIRMWARE_VERSION[] = "0.1.6";
static constexpr uint8_t PROTOCOL_VERSION_NUMBER = 1;
static constexpr char DEVICE_CAPABILITIES[] =
    "OLED,MIC,AUDIO,AUDIO_LOOP,FACE_CONTROL,WIFI,AUDIO_STREAM";

// ===============================
// OLED
// ===============================

#define OLED_WIDTH 128
#define OLED_HEIGHT 64
#define OLED_ADDR 0x3C

#define OLED_SDA 21
#define OLED_SCL 22

Adafruit_SSD1306 oled(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);

// ===============================
// MICROFONO
// ===============================

#define MIC_PIN 34

// ===============================
// MAX98357A
// ===============================

#define I2S_BCLK 26
#define I2S_LRC  25
#define I2S_DIN  27

// ADC continuous capture reserves I2S0 for DMA; keep modern I2S TX on I2S1.
I2SClass I2S(I2S_NUM_1);

// ===============================
// ESTADO
// ===============================

bool oledOK = false;
bool i2sOK = false;
bool audioLoop = false;

unsigned long ultimoMensaje = 0;
String commandBuffer;
bool descartandoComandoLargo = false;

WiFiClient wifiClient;
WiFiUDP discoveryUdp;
bool wifiEverConnected = false;
bool wifiAudioReceiving = false;
bool wifiAudioDrainPending = false;
unsigned long nextWifiConnectAt = 0;
unsigned long wifiAudioDrainAt = 0;

static constexpr uint8_t WIFI_FRAME_CONTROL = 1;
static constexpr uint8_t WIFI_FRAME_AUDIO_UP = 2;
static constexpr uint8_t WIFI_FRAME_AUDIO_DOWN = 3;
static constexpr uint8_t WIFI_FRAME_HEADER_SIZE = 9;
static constexpr size_t WIFI_AUDIO_CHUNK_BYTES = 1024;
static constexpr size_t ADC_DMA_BUFFER_BYTES = 512;

uint8_t wifiFrameHeader[WIFI_FRAME_HEADER_SIZE];
uint8_t wifiFrameHeaderRead = 0;
uint8_t wifiFrameType = 0;
uint32_t wifiFrameRemaining = 0;
uint32_t wifiControlLength = 0;
char wifiControlBuffer[2049];
uint8_t wifiAudioBuffer[WIFI_AUDIO_CHUNK_BYTES];

adc_continuous_handle_t micAdcHandle = nullptr;
bool micAdcRunning = false;
uint16_t latestMicRaw = 0;
int32_t micDcBaseline = 2048;
int16_t micWindowSamples[POPO_ADC_SAMPLE_RATE / 50];
uint16_t micWindowCount = 0;

// ===============================
// MOTOR REUTILIZABLE DE CARA
// ===============================

enum FaceExpression : uint8_t
{
    FACE_NORMAL,
    FACE_HAPPY,
    FACE_SAD,
    FACE_ANGRY,
    FACE_SURPRISED,
    FACE_SLEEPY,
    FACE_SLEEPING,
    FACE_THINKING,
    FACE_CONFUSED,
    FACE_LOVE,
    FACE_LAUGHING,
    FACE_LISTENING,
    FACE_SPEAKING,
    FACE_ERROR,
    FACE_LOADING,
    FACE_BOOT,
    FACE_BORED,
    FACE_CURIOUS,
    FACE_ANNOYED,
    FACE_EXCITED
};

enum FaceLook : uint8_t
{
    LOOK_CENTER,
    LOOK_LEFT,
    LOOK_RIGHT,
    LOOK_UP,
    LOOK_DOWN
};

enum MouthStyle : uint8_t
{
    MOUTH_NEUTRAL,
    MOUTH_SMILE,
    MOUTH_SAD,
    MOUTH_SURPRISED,
    MOUTH_SPEAKING,
    MOUTH_LAUGHING,
    MOUTH_CONFUSED,
    MOUTH_ATTENTIVE,
    MOUTH_ERROR
};

struct FaceEye
{
    int16_t x;
    int16_t y;
    uint8_t width;
    uint8_t height;
    uint8_t openness;
    int8_t gazeX;
    int8_t gazeY;
    uint8_t scale;
    uint8_t lid;
};

class FaceEngine
{
public:
    explicit FaceEngine(Adafruit_SSD1306& screen) : display(screen) {}

    void begin(bool available)
    {
        ready = available;
        if (!ready) return;

        randomState = millis() ^ 0x6D2B79F5UL;
        if (randomState == 0) randomState = 1;
        lastLookEaseAt = millis();
        startBoot();
    }

    void startBoot()
    {
        if (!ready) return;
        startupMode = STARTUP_BOOT;
        startupStarted = millis();
        bootStage = 0;
        expression = FACE_BOOT;
        applyExpression(FACE_BOOT);
        forceRender = true;
    }

    void setExpression(FaceExpression next, uint32_t durationMs = 0)
    {
        if (!ready) return;
        if (next != expression) previousExpression = expression;
        startupMode = STARTUP_IDLE;
        expression = next;
        expressionStarted = millis();
        expressionDuration = durationMs;
        applyExpression(next);
        forceRender = true;
        if (next == FACE_NORMAL) scheduleIdle(millis());
    }

    void setEyeState(bool left, const FaceEye& state)
    {
        if (!ready) return;
        FaceEye safe = state;
        if (safe.x < 0) safe.x = 0;
        if (safe.x > 88) safe.x = 88;
        if (safe.y < 0) safe.y = 0;
        if (safe.y > 38) safe.y = 38;
        if (safe.width < 4) safe.width = 4;
        if (safe.width > 60) safe.width = 60;
        if (safe.height < 4) safe.height = 4;
        if (safe.height > 56) safe.height = 56;
        if (safe.openness > 100) safe.openness = 100;
        if (safe.scale < 50) safe.scale = 50;
        if (safe.scale > 140) safe.scale = 140;
        if (safe.lid > 100) safe.lid = 100;
        int16_t maxX = OLED_WIDTH -
            (static_cast<uint16_t>(safe.width) * safe.scale) / 100;
        int16_t maxY = OLED_HEIGHT -
            (static_cast<uint16_t>(safe.height) * safe.scale) / 100;
        if (maxX < 0) maxX = 0;
        if (maxY < 0) maxY = 0;
        if (safe.x > maxX) safe.x = maxX;
        if (safe.y > maxY) safe.y = maxY;
        if (left) leftEye = safe;
        else rightEye = safe;
        forceRender = true;
    }

    void setLook(FaceLook look)
    {
        switch (look)
        {
            case LOOK_LEFT:  lookTargetX = -5; lookTargetY = 0; break;
            case LOOK_RIGHT: lookTargetX = 5;  lookTargetY = 0; break;
            case LOOK_UP:    lookTargetX = 0;  lookTargetY = -4; break;
            case LOOK_DOWN:  lookTargetX = 0;  lookTargetY = 4; break;
            default:         lookTargetX = 0;  lookTargetY = 0; break;
        }
        forceRender = true;
    }

    void update()
    {
        if (!ready) return;

        const unsigned long now = millis();
        updateStartup(now);

        if (expressionDuration > 0 && now - expressionStarted >= expressionDuration)
        {
            expression = previousExpression == FACE_ERROR ? FACE_NORMAL : previousExpression;
            expressionDuration = 0;
            applyExpression(expression);
            if (expression == FACE_NORMAL) scheduleIdle(now);
            forceRender = true;
        }

        updateAnimation(now);
        const unsigned long frameInterval =
            expression == FACE_SLEEPING ? SLEEP_FRAME_INTERVAL_MS : FRAME_INTERVAL_MS;
        if (forceRender || now - lastFrameAt >= frameInterval)
        {
            renderFace(now);
            lastFrameAt = now;
            forceRender = false;
        }
    }

private:
    enum StartupMode : uint8_t
    {
        STARTUP_IDLE,
        STARTUP_BOOT
    };

    static constexpr unsigned long FRAME_INTERVAL_MS = 40;
    static constexpr unsigned long SLEEP_FRAME_INTERVAL_MS = 450;
    static constexpr unsigned long SLEEPY_RAMP_MS = 45000;

    Adafruit_SSD1306& display;
    bool ready = false;
    bool forceRender = true;
    FaceExpression expression = FACE_NORMAL;
    FaceExpression previousExpression = FACE_NORMAL;
    MouthStyle mouth = MOUTH_NEUTRAL;
    StartupMode startupMode = STARTUP_IDLE;
    FaceEye leftEye = {13, 11, 40, 38, 100, 0, 0, 100, 0};
    FaceEye rightEye = {75, 11, 40, 38, 100, 0, 0, 100, 0};
    unsigned long startupStarted = 0;
    unsigned long expressionStarted = 0;
    unsigned long expressionDuration = 0;
    unsigned long lastFrameAt = 0;
    unsigned long nextBlinkAt = 0;
    unsigned long blinkStarted = 0;
    unsigned long lastMouthAt = 0;
    unsigned long nextErrorMotionAt = 0;
    unsigned long nextSleepBreathAt = 0;
    unsigned long nextSleepTwitchAt = 0;
    unsigned long sleepTwitchEndAt = 0;
    unsigned long nextSleepyBlinkAt = 0;
    unsigned long nextSleepyLookAt = 0;
    unsigned long nextLookAt = 0;
    uint32_t randomState = 1;
    uint8_t bootStage = 0;
    uint8_t blinkClosure = 0;
    uint8_t mouthFrame = 0;
    int8_t lookTargetX = 0;
    int8_t lookTargetY = 0;
    int8_t lookCurrentX = 0;
    int8_t lookCurrentY = 0;
    int16_t lookCurrentX8 = 0;
    int16_t lookCurrentY8 = 0;
    unsigned long lastThinkingMotionAt = 0;
    unsigned long lastListeningMotionAt = 0;
    unsigned long nextExpressionMotionAt = 0;
    unsigned long lastLookEaseAt = 0;
    unsigned long transitionStarted = 0;
    unsigned long transitionDuration = 150;
    FaceEye transitionFromLeft = {13, 11, 40, 38, 100, 0, 0, 100, 0};
    FaceEye transitionFromRight = {75, 11, 40, 38, 100, 0, 0, 100, 0};
    FaceEye transitionToLeft = {13, 11, 40, 38, 100, 0, 0, 100, 0};
    FaceEye transitionToRight = {75, 11, 40, 38, 100, 0, 0, 100, 0};
    uint8_t thinkingMotionStep = 0;
    uint8_t listeningMotionStep = 0;
    uint8_t errorMotionStep = 0;
    uint8_t expressionMotionStep = 0;
    int8_t sleepBreathOffset = 0;
    uint8_t sleepyFinalLookStep = 0;

    uint8_t nextRandom()
    {
        randomState ^= randomState << 13;
        randomState ^= randomState >> 17;
        randomState ^= randomState << 5;
        return static_cast<uint8_t>(randomState & 0xFF);
    }

    uint16_t randomRange(uint16_t minimum, uint16_t maximumExclusive)
    {
        if (maximumExclusive <= minimum) return minimum;
        const uint32_t value =
            (static_cast<uint32_t>(nextRandom()) << 24) |
            (static_cast<uint32_t>(nextRandom()) << 16) |
            (static_cast<uint32_t>(nextRandom()) << 8) |
            static_cast<uint32_t>(nextRandom());
        return minimum + value % (maximumExclusive - minimum);
    }

    static int16_t interpolate(int16_t from, int16_t to, uint8_t progress)
    {
        return from + (static_cast<int32_t>(to - from) * progress) / 255;
    }

    FaceEye interpolateEye(const FaceEye& from, const FaceEye& to, uint8_t progress)
    {
        FaceEye result = to;
        result.x = interpolate(from.x, to.x, progress);
        result.y = interpolate(from.y, to.y, progress);
        result.width = interpolate(from.width, to.width, progress);
        result.height = interpolate(from.height, to.height, progress);
        result.openness = interpolate(from.openness, to.openness, progress);
        result.gazeX = interpolate(from.gazeX, to.gazeX, progress);
        result.gazeY = interpolate(from.gazeY, to.gazeY, progress);
        result.scale = interpolate(from.scale, to.scale, progress);
        result.lid = interpolate(from.lid, to.lid, progress);
        return result;
    }

    void startEyeTransition(const FaceEye& fromLeft, const FaceEye& fromRight)
    {
        transitionFromLeft = fromLeft;
        transitionFromRight = fromRight;
        transitionToLeft = leftEye;
        transitionToRight = rightEye;
        transitionStarted = millis();
        leftEye = fromLeft;
        rightEye = fromRight;
    }

    void updateEyeTransition(unsigned long now)
    {
        if (transitionStarted == 0) return;
        const unsigned long elapsed = now - transitionStarted;
        if (elapsed >= transitionDuration)
        {
            leftEye = transitionToLeft;
            rightEye = transitionToRight;
            transitionStarted = 0;
            forceRender = true;
            return;
        }
        // Smoothstep easing keeps the short transition gentle at both ends.
        const uint32_t linear = (elapsed * 255) / transitionDuration;
        const uint8_t eased = static_cast<uint8_t>(
            (linear * linear * (765 - 2 * linear)) / (255UL * 255UL)
        );
        leftEye = interpolateEye(transitionFromLeft, transitionToLeft, eased);
        rightEye = interpolateEye(transitionFromRight, transitionToRight, eased);
    }

    void scheduleIdle(unsigned long now)
    {
        blinkStarted = 0;
        blinkClosure = 0;
        nextBlinkAt = now + randomRange(2800, 6600);
        nextLookAt = now + randomRange(1800, 4200);
        lookTargetX = 0;
        lookTargetY = 0;
    }

    void updateStartup(unsigned long now)
    {
        if (startupMode == STARTUP_BOOT)
        {
            const unsigned long elapsed = now - startupStarted;
            uint8_t stage = 0;
            if (elapsed >= 220) stage = 1;
            if (elapsed >= 430) stage = 2;
            if (elapsed >= 760) stage = 3;

            if (elapsed >= 1050)
            {
                startupMode = STARTUP_IDLE;
                expression = FACE_NORMAL;
                applyExpression(FACE_NORMAL);
                scheduleIdle(now);
                forceRender = true;
                return;
            }

            if (stage != bootStage)
            {
                bootStage = stage;
                const FaceExpression bootExpressions[] = {
                    FACE_BOOT, FACE_SLEEPY, FACE_HAPPY, FACE_NORMAL
                };
                expression = bootExpressions[bootStage];
                applyExpression(expression);
                if (expression == FACE_HAPPY) lookTargetX = -2;
                forceRender = true;
            }
            return;
        }
    }

    void applyExpression(FaceExpression next)
    {
        const FaceEye fromLeft = leftEye;
        const FaceEye fromRight = rightEye;
        blinkStarted = 0;
        blinkClosure = 0;
        leftEye = {13, 11, 40, 38, 100, 0, 0, 100, 0};
        rightEye = {75, 11, 40, 38, 100, 0, 0, 100, 0};
        mouth = MOUTH_NEUTRAL;
        lookTargetX = 0;
        lookTargetY = 0;

        switch (next)
        {
            case FACE_HAPPY:
                // A clearer cheerful signature without changing the eye style:
                // slightly wider eyes with a relaxed, smiling aperture.
                leftEye.width = 42; rightEye.width = 42;
                leftEye.height = 40; rightEye.height = 40;
                leftEye.x = 12; rightEye.x = 74;
                leftEye.openness = 86; rightEye.openness = 86;
                leftEye.gazeY = -2; rightEye.gazeY = -2;
                mouth = MOUTH_SMILE;
                break;
            case FACE_SAD:
                leftEye.y += 2; rightEye.y += 2;
                leftEye.openness = 82; rightEye.openness = 82;
                leftEye.gazeY = 4; rightEye.gazeY = 4;
                nextExpressionMotionAt = millis() + randomRange(1200, 2100);
                expressionMotionStep = 0;
                mouth = MOUTH_SAD;
                break;
            case FACE_ANGRY:
                leftEye.lid = 20; rightEye.lid = 20;
                nextExpressionMotionAt = millis() + randomRange(650, 1050);
                expressionMotionStep = 0;
                mouth = MOUTH_NEUTRAL;
                break;
            case FACE_SURPRISED:
                leftEye.y = 9; rightEye.y = 9;
                leftEye.height = 40; rightEye.height = 40;
                leftEye.scale = 104; rightEye.scale = 104;
                nextExpressionMotionAt = millis() + randomRange(350, 650);
                expressionMotionStep = 0;
                mouth = MOUTH_SURPRISED;
                break;
            case FACE_SLEEPY:
                leftEye.lid = 30; rightEye.lid = 30;
                nextSleepyBlinkAt = millis() + randomRange(3500, 7500);
                nextSleepyLookAt = millis() + SLEEPY_RAMP_MS - 7000;
                sleepyFinalLookStep = 0;
                mouth = MOUTH_NEUTRAL;
                break;
            case FACE_SLEEPING:
                leftEye.openness = 0; rightEye.openness = 0;
                mouth = MOUTH_NEUTRAL;
                nextSleepBreathAt = millis() + randomRange(850, 1450);
                nextSleepTwitchAt = millis() + randomRange(9000, 18000);
                sleepTwitchEndAt = 0;
                sleepBreathOffset = 0;
                break;
            case FACE_BOOT:
                leftEye.openness = 0; rightEye.openness = 0;
                mouth = MOUTH_NEUTRAL;
                break;
            case FACE_THINKING:
                leftEye.gazeX = 3; rightEye.gazeX = 3;
                leftEye.gazeY = -4; rightEye.gazeY = -4;
                mouth = MOUTH_CONFUSED;
                break;
            case FACE_CONFUSED:
                leftEye.y -= 1; rightEye.y += 1;
                rightEye.openness = 76;
                leftEye.gazeX = -2; rightEye.gazeX = 2;
                nextExpressionMotionAt = millis() + randomRange(650, 1150);
                expressionMotionStep = 0;
                mouth = MOUTH_CONFUSED;
                break;
            case FACE_LOVE:
                mouth = MOUTH_SMILE;
                break;
            case FACE_LAUGHING:
                leftEye.openness = 78; rightEye.openness = 78;
                mouth = MOUTH_LAUGHING;
                mouthFrame = 0;
                lastMouthAt = millis() + randomRange(110, 190);
                break;
            case FACE_LISTENING:
                leftEye.scale = 106; rightEye.scale = 106;
                leftEye.gazeY = -3; rightEye.gazeY = -3;
                if (fromLeft.openness < 10)
                {
                    lookTargetX = -2;
                    lastListeningMotionAt = millis() + 450;
                }
                mouth = MOUTH_ATTENTIVE;
                break;
            case FACE_BORED:
                leftEye.openness = 83; rightEye.openness = 83;
                leftEye.gazeX = -3; rightEye.gazeX = -3;
                leftEye.gazeY = 2; rightEye.gazeY = 2;
                break;
            case FACE_CURIOUS:
                leftEye.y = 10; rightEye.y = 12;
                leftEye.openness = 100; rightEye.openness = 94;
                leftEye.gazeX = 2; rightEye.gazeX = 2;
                break;
            case FACE_ANNOYED:
                leftEye.lid = 24; rightEye.lid = 24;
                mouth = MOUTH_NEUTRAL;
                break;
            case FACE_EXCITED:
                leftEye.scale = 105; rightEye.scale = 105;
                leftEye.openness = 100; rightEye.openness = 100;
                break;
            case FACE_SPEAKING:
                mouth = MOUTH_SPEAKING;
                mouthFrame = 0;
                lastMouthAt = millis() + randomRange(80, 175);
                break;
            case FACE_ERROR:
                leftEye.openness = 72;
                rightEye.openness = 96;
                leftEye.gazeX = -2; rightEye.gazeX = 2;
                mouth = MOUTH_ERROR;
                nextErrorMotionAt = millis() + randomRange(280, 520);
                break;
            case FACE_LOADING:
                mouth = MOUTH_NEUTRAL;
                lookTargetY = -2;
                break;
            case FACE_NORMAL:
            default:
                mouth = MOUTH_NEUTRAL;
                break;
        }
        transitionDuration =
            next == FACE_LISTENING && fromLeft.openness < 10 ? 420 : 150;
        if (next == FACE_SPEAKING || next == FACE_LAUGHING)
        {
            nextBlinkAt = millis() + randomRange(2600, 5900);
            nextLookAt = millis() + randomRange(1800, 4200);
        }
        startEyeTransition(fromLeft, fromRight);
        forceRender = true;
    }

    void updateAnimation(unsigned long now)
    {
        if (startupMode == STARTUP_IDLE &&
            (expression == FACE_NORMAL || expression == FACE_LISTENING ||
             expression == FACE_HAPPY || expression == FACE_BORED || expression == FACE_CURIOUS ||
             expression == FACE_EXCITED || expression == FACE_SLEEPY ||
             expression == FACE_SPEAKING || expression == FACE_LAUGHING))
        {
            if (static_cast<int32_t>(now - nextLookAt) >= 0)
            {
                const int8_t options[] = {-4, 0, 4};
                lookTargetX = options[nextRandom() % 3];
                lookTargetY = options[nextRandom() % 3] / 2;
                nextLookAt = now + randomRange(1700, 4100);
            }

            const bool sleepy = expression == FACE_SLEEPY;
            if (sleepy && static_cast<int32_t>(now - nextSleepyBlinkAt) >= 0)
            {
                blinkStarted = now;
                nextSleepyBlinkAt = now + randomRange(5200, 9800);
            }
            else if (!sleepy && blinkStarted == 0 &&
                     static_cast<int32_t>(now - nextBlinkAt) >= 0)
            {
                blinkStarted = now;
            }

            if (blinkStarted != 0)
            {
                const unsigned long elapsed = now - blinkStarted;
                uint8_t closure = 0;
                const unsigned long closeMs = sleepy ? 150 : 65;
                const unsigned long holdUntil = sleepy ? 230 : 105;
                const unsigned long openUntil = sleepy ? 430 : 190;
                if (elapsed < closeMs) closure = (elapsed * 100) / closeMs;
                else if (elapsed < holdUntil) closure = 100;
                else if (elapsed < openUntil)
                    closure = 100 - ((elapsed - holdUntil) * 100) / (openUntil - holdUntil);
                else
                {
                    blinkStarted = 0;
                    if (!sleepy) nextBlinkAt = now + randomRange(2800, 6600);
                }
                if (closure != blinkClosure)
                {
                    blinkClosure = closure;
                    forceRender = true;
                }
            }
        }
        else
        {
            blinkClosure = 0;
        }

        if (expression == FACE_THINKING &&
            static_cast<int32_t>(now - lastThinkingMotionAt) >= 0)
        {
            const int8_t x[] = {3, -2, 1, 0};
            const int8_t y[] = {-4, -2, -3, 0};
            thinkingMotionStep = (thinkingMotionStep + 1) % 4;
            lookTargetX = x[thinkingMotionStep];
            lookTargetY = y[thinkingMotionStep];
            lastThinkingMotionAt = now + randomRange(520, 920);
        }

        if (expression == FACE_LISTENING &&
            static_cast<int32_t>(now - lastListeningMotionAt) >= 0)
        {
            const int8_t x[] = {0, -2, 0, 2};
            listeningMotionStep = (listeningMotionStep + 1) % 4;
            lookTargetX = x[listeningMotionStep];
            lookTargetY = -2;
            lastListeningMotionAt = now + randomRange(950, 1650);
        }

        if ((expression == FACE_SAD || expression == FACE_ANGRY ||
             expression == FACE_SURPRISED || expression == FACE_CONFUSED) &&
            static_cast<int32_t>(now - nextExpressionMotionAt) >= 0)
        {
            ++expressionMotionStep;
            if (expression == FACE_SAD)
            {
                const int8_t x[] = {0, -1, 1, 0};
                lookTargetX = x[expressionMotionStep % 4];
                lookTargetY = (expressionMotionStep % 3 == 0) ? 4 : 3;
                nextExpressionMotionAt = now + randomRange(1250, 2300);
            }
            else if (expression == FACE_ANGRY)
            {
                const int8_t x[] = {-1, 1, 0};
                lookTargetX = x[expressionMotionStep % 3];
                lookTargetY = 0;
                nextExpressionMotionAt = now + randomRange(650, 1100);
            }
            else if (expression == FACE_SURPRISED)
            {
                const int8_t x[] = {1, 0, -1, 0};
                lookTargetX = x[expressionMotionStep % 4];
                lookTargetY = (expressionMotionStep % 2 == 0) ? -1 : 0;
                nextExpressionMotionAt = now + randomRange(400, 750);
            }
            else
            {
                const int8_t x[] = {3, -2, 0, 1};
                lookTargetX = x[expressionMotionStep % 4];
                lookTargetY = (expressionMotionStep % 2 == 0) ? -1 : 1;
                nextExpressionMotionAt = now + randomRange(650, 1200);
            }
            forceRender = true;
        }

        if (expression == FACE_LOADING)
        {
            const int8_t positions[] = {-4, 0, 4, 0};
            lookTargetX = positions[(now / 350) % 4];
        }

        if (expression == FACE_SLEEPY)
        {
            const unsigned long elapsed = now - expressionStarted;
            const uint8_t lid = 30 + min(62UL, (elapsed * 62UL) / SLEEPY_RAMP_MS);
            if (transitionStarted == 0 && leftEye.lid != lid)
            {
                leftEye.lid = lid;
                rightEye.lid = lid;
                forceRender = true;
            }
            if (sleepyFinalLookStep == 0 &&
                static_cast<int32_t>(now - nextSleepyLookAt) >= 0)
            {
                lookTargetX = 3;
                sleepyFinalLookStep = 1;
                nextSleepyLookAt = now + 900;
            }
            else if (sleepyFinalLookStep == 1 &&
                     static_cast<int32_t>(now - nextSleepyLookAt) >= 0)
            {
                lookTargetX = 0;
                sleepyFinalLookStep = 2;
            }
        }

        if (expression == FACE_ERROR && static_cast<int32_t>(now - nextErrorMotionAt) >= 0)
        {
            const int8_t positions[] = {-3, 2, 0, -1};
            errorMotionStep = (errorMotionStep + 1) % 4;
            lookTargetX = positions[errorMotionStep];
            lookTargetY = (errorMotionStep == 1) ? -1 : 1;
            nextErrorMotionAt = now + randomRange(280, 520);
        }

        if (expression == FACE_SLEEPING && static_cast<int32_t>(now - nextSleepBreathAt) >= 0)
        {
            sleepBreathOffset = (nextRandom() & 1) ? 1 : 0;
            nextSleepBreathAt = now + randomRange(850, 1450);
            forceRender = true;
        }

        if (expression == FACE_SLEEPING)
        {
            if (sleepTwitchEndAt != 0 && static_cast<int32_t>(now - sleepTwitchEndAt) >= 0)
            {
                lookTargetX = 0;
                sleepTwitchEndAt = 0;
                nextSleepTwitchAt = now + randomRange(9000, 18000);
            }
            else if (sleepTwitchEndAt == 0 &&
                     static_cast<int32_t>(now - nextSleepTwitchAt) >= 0)
            {
                lookTargetX = (nextRandom() & 1) ? 1 : -1;
                sleepTwitchEndAt = now + randomRange(250, 500);
            }
        }

        if (expression == FACE_SPEAKING && static_cast<int32_t>(now - lastMouthAt) >= 0)
        {
            mouthFrame = randomRange(0, 6);
            lastMouthAt = now + randomRange(75, 175);
            forceRender = true;
        }

        if (expression == FACE_LAUGHING && static_cast<int32_t>(now - lastMouthAt) >= 0)
        {
            mouthFrame = (mouthFrame + 1) % 3;
            lastMouthAt = now + randomRange(110, 190);
            forceRender = true;
        }

        if (now - lastLookEaseAt >= 25)
        {
            lastLookEaseAt = now;
            const int16_t targetX8 = static_cast<int16_t>(lookTargetX) * 8;
            const int16_t targetY8 = static_cast<int16_t>(lookTargetY) * 8;
            const int16_t deltaX8 = targetX8 - lookCurrentX8;
            const int16_t deltaY8 = targetY8 - lookCurrentY8;
            if (deltaX8 != 0) lookCurrentX8 += deltaX8 / 4 == 0 ?
                (deltaX8 > 0 ? 1 : -1) : deltaX8 / 4;
            if (deltaY8 != 0) lookCurrentY8 += deltaY8 / 4 == 0 ?
                (deltaY8 > 0 ? 1 : -1) : deltaY8 / 4;
            lookCurrentX = lookCurrentX8 / 8;
            lookCurrentY = lookCurrentY8 / 8;
        }
        updateEyeTransition(now);
    }

    void drawEye(FaceEye eye, bool heart, uint8_t closure)
    {
        uint16_t width = (static_cast<uint16_t>(eye.width) * eye.scale) / 100;
        uint16_t height = (static_cast<uint16_t>(eye.height) * eye.scale) / 100;
        if (width < 4) width = 4;
        if (height < 4) height = 4;
        const int16_t x = eye.x - (static_cast<int16_t>(width) - eye.width) / 2 +
            lookCurrentX + eye.gazeX;
        const int16_t y = eye.y - (static_cast<int16_t>(height) - eye.height) / 2 +
            lookCurrentY + eye.gazeY;

        uint16_t openness = eye.openness;
        openness = (openness * (100 - eye.lid)) / 100;
        openness = (openness * (100 - closure)) / 100;

        if (openness < 12)
        {
            const int16_t centerY = y + height / 2;
            const int16_t bend = expression == FACE_LAUGHING ? -2 : 3;
            display.drawLine(x + 4, centerY, x + width / 2, centerY + bend, SSD1306_WHITE);
            display.drawLine(x + width / 2, centerY + bend, x + width - 4, centerY, SSD1306_WHITE);
            return;
        }

        uint16_t visibleHeight = (height * openness) / 100;
        if (visibleHeight < 4) visibleHeight = 4;
        const int16_t visibleY = y + (height - visibleHeight) / 2;
        if (heart)
        {
            const int16_t heartX = x + width / 2;
            const int16_t heartY = visibleY + visibleHeight / 2;
            display.fillCircle(heartX - 4, heartY - 2, 4, SSD1306_WHITE);
            display.fillCircle(heartX + 4, heartY - 2, 4, SSD1306_WHITE);
            display.fillTriangle(heartX - 8, heartY, heartX + 8, heartY,
                                 heartX, heartY + 9, SSD1306_WHITE);
        }
        else
        {
            uint8_t radius = visibleHeight / 3;
            if (radius > width / 3) radius = width / 3;
            display.fillRoundRect(x, visibleY, width, visibleHeight, radius, SSD1306_WHITE);
        }
    }

    void drawMouth()
    {
        if (expression != FACE_SPEAKING && expression != FACE_LAUGHING) return;

        const int16_t center = 64;
        const int16_t y = expression == FACE_LAUGHING ? 54 : 56;
        switch (mouth)
        {
            case MOUTH_SMILE:
                display.drawLine(51, y - 3, 56, y + 1, SSD1306_WHITE);
                display.drawLine(56, y + 1, center, y + 4, SSD1306_WHITE);
                display.drawLine(center, y + 4, 72, y + 1, SSD1306_WHITE);
                display.drawLine(72, y + 1, 77, y - 3, SSD1306_WHITE);
                break;
            case MOUTH_SAD:
                display.drawLine(51, y + 2, 56, y - 2, SSD1306_WHITE);
                display.drawLine(56, y - 2, center, y - 4, SSD1306_WHITE);
                display.drawLine(center, y - 4, 72, y - 2, SSD1306_WHITE);
                display.drawLine(72, y - 2, 77, y + 2, SSD1306_WHITE);
                break;
            case MOUTH_SURPRISED:
                display.drawRoundRect(center - 6, y - 5, 12, 10, 5, SSD1306_WHITE);
                break;
            case MOUTH_SPEAKING:
                if (mouthFrame == 0)
                {
                    display.drawLine(center - 8, y - 2, center - 4, y + 1, SSD1306_WHITE);
                    display.drawLine(center - 4, y + 1, center, y + 2, SSD1306_WHITE);
                    display.drawLine(center, y + 2, center + 4, y + 1, SSD1306_WHITE);
                    display.drawLine(center + 4, y + 1, center + 8, y - 2, SSD1306_WHITE);
                }
                else if (mouthFrame == 1 || mouthFrame == 4)
                    display.fillRoundRect(center - 5, y - 1, 10, 5, 2, SSD1306_WHITE);
                else if (mouthFrame == 2)
                {
                    display.fillRoundRect(center - 7, y - 3, 14, 8, 4, SSD1306_WHITE);
                    display.fillRoundRect(center - 4, y - 2, 8, 4, 2, SSD1306_BLACK);
                    display.drawFastHLine(center - 3, y + 2, 6, SSD1306_WHITE);
                }
                else if (mouthFrame == 3)
                {
                    display.fillRoundRect(center - 8, y - 5, 16, 11, 5, SSD1306_WHITE);
                    display.fillRoundRect(center - 5, y - 3, 10, 7, 3, SSD1306_BLACK);
                    display.drawFastHLine(center - 3, y + 3, 6, SSD1306_WHITE);
                }
                else if (mouthFrame == 5)
                    display.drawRoundRect(center - 6, y - 4, 12, 8, 4, SSD1306_WHITE);
                break;
            case MOUTH_LAUGHING:
                if (mouthFrame == 0)
                {
                    display.drawLine(51, y - 3, 56, y + 1, SSD1306_WHITE);
                    display.drawLine(56, y + 1, center, y + 3, SSD1306_WHITE);
                    display.drawLine(center, y + 3, 72, y + 1, SSD1306_WHITE);
                    display.drawLine(72, y + 1, 77, y - 3, SSD1306_WHITE);
                }
                else if (mouthFrame == 1)
                {
                    display.fillRoundRect(center - 7, y - 3, 14, 9, 4, SSD1306_WHITE);
                    display.fillRoundRect(center - 4, y - 1, 8, 5, 2, SSD1306_BLACK);
                }
                else
                    display.fillRoundRect(center - 5, y - 1, 10, 6, 3, SSD1306_WHITE);
                break;
            case MOUTH_CONFUSED:
                display.drawLine(54, y, 59, y - 2, SSD1306_WHITE);
                display.drawLine(59, y - 2, 64, y + 1, SSD1306_WHITE);
                display.drawLine(64, y + 1, 69, y - 2, SSD1306_WHITE);
                display.drawLine(69, y - 2, 74, y, SSD1306_WHITE);
                break;
            case MOUTH_ATTENTIVE:
                display.drawRoundRect(center - 4, y - 1, 8, 3, 1, SSD1306_WHITE);
                break;
            case MOUTH_ERROR:
                display.drawLine(53, y - 1, 58, y + 2, SSD1306_WHITE);
                display.drawLine(58, y + 2, 62, y - 2, SSD1306_WHITE);
                display.drawLine(62, y - 2, 67, y + 2, SSD1306_WHITE);
                display.drawLine(67, y + 2, 75, y - 1, SSD1306_WHITE);
                break;
            case MOUTH_NEUTRAL:
            default:
                display.drawFastHLine(center - 8, y, 16, SSD1306_WHITE);
                break;
        }
    }

    void renderFace(unsigned long)
    {
        display.clearDisplay();

        const bool angry = expression == FACE_ANGRY || expression == FACE_ANNOYED;
        if (angry)
        {
            display.drawLine(14, 8, 49, 10, SSD1306_WHITE);
            display.drawLine(79, 10, 114, 8, SSD1306_WHITE);
        }

        const bool hearts = expression == FACE_LOVE;
        const uint8_t sleepClosure = expression == FACE_SLEEPING ? 100 : blinkClosure;
        if (expression == FACE_SLEEPING && sleepBreathOffset != 0)
        {
            FaceEye breathingLeft = leftEye;
            FaceEye breathingRight = rightEye;
            breathingLeft.y += sleepBreathOffset;
            breathingRight.y += sleepBreathOffset;
            drawEye(breathingLeft, hearts, sleepClosure);
            drawEye(breathingRight, hearts, sleepClosure);
        }
        else
        {
            drawEye(leftEye, hearts, sleepClosure);
            drawEye(rightEye, hearts, sleepClosure);
        }
        drawMouth();
        display.display();
    }
};

FaceEngine face(oled);

bool enviarWiFiFrame(uint8_t frameType, const uint8_t* payload, uint32_t length)
{
    if (!wifiClient.connected()) return false;
    uint8_t header[WIFI_FRAME_HEADER_SIZE] = {
        'P', 'O', 'P', '1', frameType,
        static_cast<uint8_t>(length >> 24),
        static_cast<uint8_t>(length >> 16),
        static_cast<uint8_t>(length >> 8),
        static_cast<uint8_t>(length)
    };
    if (wifiClient.write(header, sizeof(header)) != sizeof(header)) return false;
    uint32_t sent = 0;
    while (sent < length)
    {
        const size_t count = wifiClient.write(payload + sent, length - sent);
        if (count == 0) return false;
        sent += count;
        yield();
    }
    return true;
}

bool enviarControlWiFi(const String& message)
{
    return enviarWiFiFrame(
        WIFI_FRAME_CONTROL,
        reinterpret_cast<const uint8_t*>(message.c_str()),
        message.length()
    );
}

bool enviarAudioWiFi(const int16_t* samples, size_t count)
{
    return enviarWiFiFrame(
        WIFI_FRAME_AUDIO_UP,
        reinterpret_cast<const uint8_t*>(samples),
        count * sizeof(int16_t)
    );
}

class WiFiLinePrinter : public Print
{
public:
    size_t write(uint8_t value) override
    {
        if (value == '\r') return 1;
        if (value == '\n')
        {
            enviarControlWiFi(line);
            line = "";
            return 1;
        }
        if (line.length() < 200) line += static_cast<char>(value);
        return 1;
    }

    void flush() override
    {
        if (line.length() > 0)
        {
            enviarControlWiFi(line);
            line = "";
        }
    }

private:
    String line;
};

void procesarComando(String comando, Print& output);

void iniciarADCdeMicrofono()
{
    if (!micAdcHandle)
    {
        adc_continuous_handle_cfg_t handleConfig = {};
        handleConfig.max_store_buf_size = 16384;
        handleConfig.conv_frame_size = 256;
        if (adc_continuous_new_handle(&handleConfig, &micAdcHandle) != ESP_OK)
        {
            Serial.println("MIC_ADC_INIT_FAIL");
            micAdcHandle = nullptr;
            return;
        }

        adc_digi_pattern_config_t pattern = {};
        pattern.atten = ADC_ATTEN_DB_12;
        pattern.channel = ADC_CHANNEL_6;  // GPIO34 / ADC1_CHANNEL_6
        pattern.unit = ADC_UNIT_1;
        pattern.bit_width = ADC_BITWIDTH_12;

        adc_continuous_config_t adcConfig = {};
        // Classic ESP32 continuous ADC rejects rates below 20 kHz.
        // The host resamples this stream to 16 kHz for Whisper.
        adcConfig.sample_freq_hz = POPO_ADC_SAMPLE_RATE;
        adcConfig.conv_mode = ADC_CONV_SINGLE_UNIT_1;
        adcConfig.format = ADC_DIGI_OUTPUT_FORMAT_TYPE1;
        adcConfig.pattern_num = 1;
        adcConfig.adc_pattern = &pattern;
        if (adc_continuous_config(micAdcHandle, &adcConfig) != ESP_OK)
        {
            Serial.println("MIC_ADC_CONFIG_FAIL");
            adc_continuous_deinit(micAdcHandle);
            micAdcHandle = nullptr;
        }
    }
}

void setMicADCEnabled(bool enabled)
{
    if (!micAdcHandle) return;
    if (enabled && !micAdcRunning)
    {
        const esp_err_t startResult = adc_continuous_start(micAdcHandle);
        if (startResult == ESP_OK)
        {
            micAdcRunning = true;
            micWindowCount = 0;
        }
    }
    else if (!enabled && micAdcRunning)
    {
        adc_continuous_stop(micAdcHandle);
        micAdcRunning = false;
        micWindowCount = 0;
    }
}

void processMicSample(uint16_t raw)
{
    latestMicRaw = raw;
    micDcBaseline += (static_cast<int32_t>(raw) - micDcBaseline) / 256;

    int32_t centered = (static_cast<int32_t>(raw) - micDcBaseline) * 16;
    if (centered > 32767) centered = 32767;
    if (centered < -32768) centered = -32768;
    micWindowSamples[micWindowCount++] = static_cast<int16_t>(centered);
    if (micWindowCount < sizeof(micWindowSamples) / sizeof(micWindowSamples[0]))
        return;

    // Stream continuous PCM to the host; the host owns voice detection and
    // decides when a user turn starts and ends.
    if (wifiClient.connected() && !wifiAudioReceiving &&
        !wifiAudioDrainPending && !audioLoop)
    {
        enviarAudioWiFi(micWindowSamples, micWindowCount);
    }

    micWindowCount = 0;
}

void serviceMicCapture()
{
    const bool shouldCapture = wifiClient.connected() &&
        !audioLoop;
    setMicADCEnabled(shouldCapture);
    if (!micAdcHandle || !micAdcRunning) return;

    uint8_t adcBytes[ADC_DMA_BUFFER_BYTES];
    uint32_t bytesRead = 0;
    const esp_err_t result = adc_continuous_read(
        micAdcHandle, adcBytes, sizeof(adcBytes), &bytesRead, 0
    );
    if (result != ESP_OK)
    {
        return;
    }
    for (uint32_t offset = 0;
         offset + SOC_ADC_DIGI_RESULT_BYTES <= bytesRead;
         offset += SOC_ADC_DIGI_RESULT_BYTES)
    {
        const adc_digi_output_data_t* sample =
            reinterpret_cast<const adc_digi_output_data_t*>(adcBytes + offset);
        if (sample->type1.channel == ADC_CHANNEL_6)
        {
            processMicSample(sample->type1.data);
        }
    }
}

void dispatchWiFiControl(const String& message)
{
    if (message.startsWith("AUDIO_OUT_START:"))
    {
        const bool formatOk = message == "AUDIO_OUT_START:16000:1:16";
        if (!formatOk || !i2sOK)
        {
            enviarControlWiFi("AUDIO_OUT_ERROR:FORMAT_OR_I2S");
            wifiAudioReceiving = false;
            return;
        }
        wifiAudioReceiving = true;
        face.setExpression(FACE_SPEAKING);
        return;
    }
    if (message == "AUDIO_OUT_END")
    {
        wifiAudioReceiving = false;
        wifiAudioDrainPending = true;
        wifiAudioDrainAt = millis() + 100;
        return;
    }

    WiFiLinePrinter output;
    procesarComando(message, output);
    output.flush();
}

void resetWiFiFrameParser()
{
    wifiFrameHeaderRead = 0;
    wifiFrameType = 0;
    wifiFrameRemaining = 0;
    wifiControlLength = 0;
}

bool beginWiFiFrame()
{
    if (memcmp(wifiFrameHeader, "POP1", 4) != 0) return false;
    wifiFrameType = wifiFrameHeader[4];
    wifiFrameRemaining =
        (static_cast<uint32_t>(wifiFrameHeader[5]) << 24) |
        (static_cast<uint32_t>(wifiFrameHeader[6]) << 16) |
        (static_cast<uint32_t>(wifiFrameHeader[7]) << 8) |
        static_cast<uint32_t>(wifiFrameHeader[8]);
    if (wifiFrameType < WIFI_FRAME_CONTROL || wifiFrameType > WIFI_FRAME_AUDIO_DOWN)
        return false;
    if ((wifiFrameType == WIFI_FRAME_CONTROL && wifiFrameRemaining > 2048) ||
        (wifiFrameType != WIFI_FRAME_CONTROL && wifiFrameRemaining > 4096))
        return false;
    if (wifiFrameType == WIFI_FRAME_CONTROL) wifiControlLength = 0;
    return true;
}

void serviceWiFiFrames()
{
    if (!wifiClient.connected()) return;
    while (wifiClient.available() > 0)
    {
        if (wifiFrameHeaderRead < WIFI_FRAME_HEADER_SIZE)
        {
            const int value = wifiClient.read();
            if (value < 0) return;
            wifiFrameHeader[wifiFrameHeaderRead++] = static_cast<uint8_t>(value);
            if (wifiFrameHeaderRead < WIFI_FRAME_HEADER_SIZE) continue;
            if (!beginWiFiFrame())
            {
                wifiClient.stop();
                resetWiFiFrameParser();
                return;
            }
            if (wifiFrameRemaining == 0)
            {
                if (wifiFrameType == WIFI_FRAME_CONTROL)
                {
                    wifiControlBuffer[0] = '\0';
                    dispatchWiFiControl(String(wifiControlBuffer));
                }
                resetWiFiFrameParser();
                continue;
            }
        }

        const size_t available = static_cast<size_t>(wifiClient.available());
        const size_t count = min(
            min(available, sizeof(wifiAudioBuffer)),
            static_cast<size_t>(wifiFrameRemaining)
        );
        if (count == 0) return;
        const int received = wifiClient.read(wifiAudioBuffer, count);
        if (received <= 0) return;
        const size_t receivedCount = static_cast<size_t>(received);

        if (wifiFrameType == WIFI_FRAME_CONTROL)
        {
            if (wifiControlLength + receivedCount >= sizeof(wifiControlBuffer))
            {
                wifiClient.stop();
                resetWiFiFrameParser();
                return;
            }
            memcpy(wifiControlBuffer + wifiControlLength, wifiAudioBuffer, receivedCount);
            wifiControlLength += receivedCount;
        }
        else if (wifiFrameType == WIFI_FRAME_AUDIO_DOWN && wifiAudioReceiving && i2sOK)
        {
            size_t written = 0;
            while (written < receivedCount)
            {
                const size_t countWritten = I2S.write(
                    wifiAudioBuffer + written, receivedCount - written
                );
                if (countWritten == 0) break;
                written += countWritten;
                face.update();
            }
        }

        wifiFrameRemaining -= receivedCount;
        if (wifiFrameRemaining == 0)
        {
            if (wifiFrameType == WIFI_FRAME_CONTROL)
            {
                wifiControlBuffer[wifiControlLength] = '\0';
                dispatchWiFiControl(String(wifiControlBuffer));
            }
            resetWiFiFrameParser();
        }
        procesarSerialDisponible();
    }
}

bool connectWiFiToPC()
{
    IPAddress serverAddress;
    uint16_t serverPort = POPO_PC_PORT;
    if (strlen(POPO_PC_HOST) > 0)
    {
        if (!serverAddress.fromString(POPO_PC_HOST)) return false;
    }
    else
    {
        const int packetSize = discoveryUdp.parsePacket();
        if (packetSize <= 0) return false;
        char beacon[64] = {};
        const int count = discoveryUdp.read(beacon, sizeof(beacon) - 1);
        if (count <= 0) return false;
        beacon[count] = '\0';
        const String announcement(beacon);
        const String prefix = "POPOTE_DISCOVERY_V1:";
        if (!announcement.startsWith(prefix)) return false;
        serverAddress = discoveryUdp.remoteIP();
        serverPort = static_cast<uint16_t>(announcement.substring(prefix.length()).toInt());
    }
    if (serverPort == 0) return false;
    if (!wifiClient.connect(serverAddress, serverPort, 1000)) return false;
    wifiClient.setNoDelay(true);
    resetWiFiFrameParser();
    const String hello = String("HELLO\tPOPOTE_HARDWARE\t") + FIRMWARE_VERSION +
        "\t" + PROTOCOL_VERSION_NUMBER + "\t" + DEVICE_CAPABILITIES;
    enviarControlWiFi(hello);
    Serial.println("WIFI_PC_CONNECTED");
    return true;
}

void serviceWiFiConnection()
{
    if (strlen(POPO_WIFI_SSID) == 0 || WiFi.status() != WL_CONNECTED) return;
    if (!wifiClient.connected() && static_cast<int32_t>(millis() - nextWifiConnectAt) >= 0)
    {
        nextWifiConnectAt = millis() + 2000;
        connectWiFiToPC();
    }
    if (wifiClient.connected()) serviceWiFiFrames();
    if (wifiAudioDrainPending && static_cast<int32_t>(millis() - wifiAudioDrainAt) >= 0)
    {
        wifiAudioDrainPending = false;
        enviarControlWiFi("AUDIO_OUT_DONE");
    }
    serviceMicCapture();
}

// ===============================
// INICIAR AUDIO CONTINUO
// ===============================

void iniciarAudioLoop(Print& output)
{
    if (!i2sOK)
    {
        output.println("AUDIO_NOT_READY");
        return;
    }

    audioLoop = true;
    setMicADCEnabled(false);
    face.setExpression(FACE_SPEAKING);

    output.println("AUDIO_LOOP_START");
}

// ===============================
// DETENER AUDIO
// ===============================

void detenerAudioLoop(Print& output)
{
    audioLoop = false;
    face.setExpression(FACE_NORMAL);

    output.println("AUDIO_LOOP_STOP");
}

// ===============================
// PROCESAR COMANDOS
// ===============================

void procesarComando(String comando, Print& output)
{
    comando.trim();

    if (comando == "PING")
    {
        output.println("PONG");
    }

    else if (comando == "STATUS")
    {
        if (oledOK && i2sOK)
            output.println("POPOTE_HARDWARE_OK");
        else
            output.println("POPOTE_HARDWARE_FAIL");
    }

    else if (comando == "IDENTIFY")
    {
        output.print("DEVICE:");
        output.println("POPOTE_HARDWARE");
    }

    else if (comando == "VERSION")
    {
        output.print("FIRMWARE_VERSION:");
        output.println(FIRMWARE_VERSION);
    }

    else if (comando == "PROTOCOL_VERSION")
    {
        output.print("PROTOCOL_VERSION:");
        output.println(PROTOCOL_VERSION_NUMBER);
    }

    else if (comando == "CAPABILITIES")
    {
        output.print("CAPABILITIES:");
        output.println(DEVICE_CAPABILITIES);
    }

    else if (comando == "FACE" || comando.startsWith("FACE "))
    {
        String state = comando.length() > 4 ? comando.substring(5) : "";
        state.trim();
        state.toUpperCase();
        FaceExpression next;
        bool valid = true;
        if      (state == "NORMAL" || state == "NEUTRAL" || state == "IDLE") next = FACE_NORMAL;
        else if (state == "LISTENING") next = FACE_LISTENING;
        else if (state == "THINKING")  next = FACE_THINKING;
        else if (state == "HAPPY")     next = FACE_HAPPY;
        else if (state == "SAD")       next = FACE_SAD;
        else if (state == "ANGRY")     next = FACE_ANGRY;
        else if (state == "SURPRISED") next = FACE_SURPRISED;
        else if (state == "SPEAKING")  next = FACE_SPEAKING;
        else if (state == "BORED")     next = FACE_BORED;
        else if (state == "SLEEPY")    next = FACE_SLEEPY;
        else if (state == "SLEEPING")  next = FACE_SLEEPING;
        else if (state == "CONFUSED")  next = FACE_CONFUSED;
        else if (state == "LOVE")      next = FACE_LOVE;
        else if (state == "LAUGHING")  next = FACE_LAUGHING;
        else if (state == "LOADING")   next = FACE_LOADING;
        else if (state == "BOOT")      next = FACE_BOOT;
        else if (state == "CURIOUS")   next = FACE_CURIOUS;
        else if (state == "ANNOYED")   next = FACE_ANNOYED;
        else if (state == "EXCITED")   next = FACE_EXCITED;
        else if (state == "ERROR")     next = FACE_ERROR;
        else valid = false;

        if (valid)
        {
            if (next == FACE_BOOT) face.startBoot();
            else face.setExpression(next);
            output.print("FACE_OK:");
            output.println(state);
        }
        else
        {
            output.println("FACE_ERROR:UNKNOWN_STATE");
        }
    }

    else if (comando == "AUDIO_LOOP")
    {
        iniciarAudioLoop(output);
    }

    else if (comando == "AUDIO_STOP")
    {
        detenerAudioLoop(output);
    }

    else if (comando == "MIC_READ")
    {
        face.setExpression(FACE_LISTENING, 450);
        int valor = micAdcRunning ? latestMicRaw : analogRead(MIC_PIN);

        output.print("MIC:");
        output.println(valor);
    }

    else if (comando == "OLED_TEST")
    {
        output.println("OLED_TEST_START");
        if (oledOK)
        {
            face.setExpression(FACE_HAPPY, 1000);
            output.println("OLED_OK");
        }
        else
        {
            output.println("OLED_FAIL");
        }
    }

    else if (comando == "AUDIO_TEST")
    {
        setMicADCEnabled(false);
        output.println("AUDIO_TEST_START");
        face.setExpression(FACE_SPEAKING);
        face.update();
        I2S.playWAV(popoteVoiceWav, popoteVoiceWavSize);
        face.setExpression(FACE_NORMAL);
        output.println("AUDIO_OK");
    }

    else if (comando == "HELP")
    {
        output.println("PING");
        output.println("STATUS");
        output.println("AUDIO_TEST");
        output.println("AUDIO_LOOP");
        output.println("AUDIO_STOP");
        output.println("MIC_READ");
        output.println("OLED_TEST");
        output.println("IDENTIFY");
        output.println("VERSION");
        output.println("PROTOCOL_VERSION");
        output.println("CAPABILITIES");
        output.println("FACE <NORMAL|IDLE|LISTENING|THINKING|HAPPY|SAD|ANGRY|SURPRISED|SLEEPY|SLEEPING|CONFUSED|LOVE|LAUGHING|SPEAKING|ERROR|LOADING|BOOT|BORED|CURIOUS|ANNOYED|EXCITED>");
    }

    else
    {
        output.print("UNKNOWN_COMMAND:");
        output.println(comando);
    }
}

// Procesamiento por bytes: mantiene el protocolo de líneas sin bloquear la cara.
void procesarSerialDisponible()
{
    while (Serial.available() > 0)
    {
        const char caracter = static_cast<char>(Serial.read());
        if (caracter == '\r') continue;

        if (caracter == '\n')
        {
            if (!descartandoComandoLargo && commandBuffer.length() > 0)
                procesarComando(commandBuffer, Serial);
            commandBuffer = "";
            descartandoComandoLargo = false;
        }
        else if (!descartandoComandoLargo)
        {
            if (commandBuffer.length() < 63)
                commandBuffer += caracter;
            else
            {
                commandBuffer = "";
                descartandoComandoLargo = true;
            }
        }
    }
}

// ===============================
// SETUP
// ===============================

void setup()
{
    Serial.begin(115200);

    delay(1000);

    // -------------------------------
    // OLED
    // -------------------------------

    Wire.begin(OLED_SDA, OLED_SCL);

    if (oled.begin(
        SSD1306_SWITCHCAPVCC,
        OLED_ADDR
    ))
    {
        oledOK = true;
        oled.clearDisplay();
        oled.display();
        Serial.println("OLED_INIT_OK");
    }
    else
    {
        Serial.println("OLED_INIT_FAIL");
    }

    // -------------------------------
    // MICROFONO
    // -------------------------------

    pinMode(MIC_PIN, INPUT);

    // -------------------------------
    // I2S
    // -------------------------------

    I2S.setPins(
        I2S_BCLK,
        I2S_LRC,
        I2S_DIN,
        -1,
        -1
    );

    if (
        I2S.begin(
            I2S_MODE_STD,
            16000,
            I2S_DATA_BIT_WIDTH_16BIT,
            I2S_SLOT_MODE_MONO
        )
    )
    {
        i2sOK = true;
        Serial.println("I2S_INIT_OK");
    }
    else
    {
        Serial.println("I2S_INIT_FAIL");
    }

    commandBuffer.reserve(64);
    face.begin(oledOK);
    iniciarADCdeMicrofono();

    if (strlen(POPO_WIFI_SSID) > 0)
    {
        WiFi.mode(WIFI_STA);
        WiFi.setAutoReconnect(true);
        WiFi.begin(POPO_WIFI_SSID, POPO_WIFI_PASSWORD);
        discoveryUdp.begin(POPO_DISCOVERY_PORT);
        Serial.println("WIFI_CONNECTING");
    }
    else
    {
        Serial.println("WIFI_NOT_CONFIGURED");
    }

    Serial.println("POPOTE_HARDWARE_READY");
}

// ===============================
// LOOP
// ===============================

void loop()
{
    // -------------------------------
    // COMANDOS SERIAL
    // -------------------------------

    serviceWiFiConnection();
    procesarSerialDisponible();

    // -------------------------------
    // AUDIO CONTINUO
    // -------------------------------

    while (audioLoop && i2sOK)
    {
        face.update();
        I2S.playWAV(popoteVoiceWav, popoteVoiceWavSize);
        face.update();
        procesarSerialDisponible();
        serviceWiFiConnection();
    }

    face.update();

    // -------------------------------
    // MENSAJE DE VIDA
    // -------------------------------

    if (!audioLoop && millis() - ultimoMensaje >= 5000)
    {
        ultimoMensaje = millis();

        Serial.println("POPOTE_ALIVE");
    }
}
