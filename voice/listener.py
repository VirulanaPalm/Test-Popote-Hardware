"""Small block-based PC microphone listener with speech/silence detection."""

from __future__ import annotations

import io
import math
import struct
import time
import wave
from collections import deque


class AudioListener:
    def __init__(self, settings: dict, audio_backend=None):
        self.settings = settings
        if audio_backend is None:
            try:
                import sounddevice as audio_backend
            except ImportError as error:
                raise RuntimeError(
                    "Falta sounddevice. Instalá las dependencias con "
                    "python -m pip install -r requirements.txt."
                ) from error
        self.audio = audio_backend
        self.sample_rate = int(settings.get("sample_rate", 16000))
        self.block_ms = int(settings.get("block_ms", 20))
        self.threshold = int(settings.get("rms_threshold", 500))
        self.silence_seconds = float(settings.get("end_silence_seconds", 0.8))
        self.min_speech_seconds = float(settings.get("min_speech_seconds", 0.3))
        self.max_recording_seconds = float(settings.get("max_recording_seconds", 12))
        self.pre_roll_seconds = float(settings.get("pre_roll_seconds", 0.25))
        self.block_frames = max(1, self.sample_rate * self.block_ms // 1000)

    @staticmethod
    def _rms(pcm: bytes) -> float:
        samples = len(pcm) // 2
        if samples == 0:
            return 0.0
        total = sum(sample * sample for (sample,) in struct.iter_unpack("<h", pcm))
        return math.sqrt(total / samples)

    def _wav(self, pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm)
        return output.getvalue()

    def listen_for_utterance(self, on_speech_start=None, on_idle_tick=None,
                             on_capture_cancelled=None) -> bytes:
        """Wait for speech, include a short pre-roll, then return a PCM WAV."""
        pre_roll_blocks = max(1, round(self.pre_roll_seconds * 1000 / self.block_ms))
        pre_roll = deque(maxlen=pre_roll_blocks)
        frames = []
        speech_started_at = 0.0
        last_voice_at = 0.0
        last_idle_callback = 0.0
        speech_active = False
        speech_blocks = 0
        block_seconds = self.block_frames / self.sample_rate

        with self.audio.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_frames,
            channels=1,
            dtype="int16",
        ) as stream:
            while True:
                pcm, _overflowed = stream.read(self.block_frames)
                now = time.monotonic()
                level = self._rms(pcm)

                if not speech_active:
                    pre_roll.append(pcm)
                    if now - last_idle_callback >= 0.5:
                        if on_idle_tick:
                            on_idle_tick()
                        last_idle_callback = now
                    if level >= self.threshold:
                        speech_active = True
                        speech_started_at = now
                        last_voice_at = now
                        frames.extend(pre_roll)
                        speech_blocks = 1
                        if on_speech_start:
                            on_speech_start()
                    continue

                frames.append(pcm)
                if level >= self.threshold:
                    last_voice_at = now
                    speech_blocks += 1

                silence_done = now - last_voice_at >= self.silence_seconds
                max_length = now - speech_started_at >= self.max_recording_seconds
                if silence_done or max_length:
                    if speech_blocks * block_seconds >= self.min_speech_seconds:
                        return self._wav(b"".join(frames))
                    if on_capture_cancelled:
                        on_capture_cancelled()
                    frames.clear()
                    pre_roll.clear()
                    speech_active = False
                    speech_blocks = 0
