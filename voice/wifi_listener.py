"""Voice activity detection and PCM WAV capture from the ESP32 Wi-Fi stream."""

from __future__ import annotations

import io
import math
import struct
import time
import wave
from collections import deque

import webrtcvad


class WiFiAudioListener:
    def __init__(self, connection, settings: dict, audio_settings: dict):
        self.connection = connection
        self.settings = settings
        self.sample_rate = int(audio_settings.get("sample_rate", 16000))
        self.capture_sample_rate = int(
            audio_settings.get("capture_sample_rate", 20000)
        )
        self.channels = int(audio_settings.get("channels", 1))
        self.sample_width = int(audio_settings.get("sample_width", 2))
        if (self.sample_rate, self.channels, self.sample_width) != (16000, 1, 2):
            raise ValueError(
                "El stream del MAX9814 debe ser PCM 16 kHz, mono y 16 bits."
            )
        self.threshold = int(settings.get("rms_threshold", 500))
        self.block_ms = int(settings.get("block_ms", 20))
        self.silence_seconds = float(settings.get("end_silence_seconds", 0.8))
        self.min_speech_seconds = float(settings.get("min_speech_seconds", 0.3))
        self.max_recording_seconds = float(settings.get("max_recording_seconds", 12))
        self.pre_roll_seconds = float(settings.get("pre_roll_seconds", 0.25))
        self.block_seconds = self.block_ms / 1000.0
        self.noise_floor = float(settings.get("initial_noise_rms", self.threshold * 0.5))
        self.noise_multiplier = float(settings.get("vad_noise_multiplier", 1.35))
        self.noise_margin = int(settings.get("vad_noise_margin", 100))
        self.noise_rise_alpha = float(settings.get("vad_noise_rise_alpha", 0.08))
        self.noise_fall_alpha = float(settings.get("vad_noise_fall_alpha", 0.18))
        self.start_blocks_required = max(1, int(settings.get("vad_start_blocks", 2)))
        self.min_activity_ratio = float(settings.get("vad_min_activity_ratio", 0.25))
        self.max_continuous_ratio = float(
            settings.get("vad_max_continuous_ratio", 0.92)
        )
        self.vad = webrtcvad.Vad(int(settings.get("vad_aggressiveness", 2)))
        self.noise_window = deque(maxlen=max(10, int(
            settings.get("vad_noise_window_blocks", 50)
        )))
        cutoff = min(
            float(settings.get("high_pass_hz", 80)),
            self.capture_sample_rate * 0.2,
        )
        self.high_pass_alpha = math.exp(
            -2.0 * math.pi * cutoff / self.capture_sample_rate
        )
        self._hp_previous_input = 0.0
        self._hp_previous_output = 0.0
        self.stream_reported = False

    def _start_threshold(self) -> float:
        return max(self.threshold, self.noise_floor * self.noise_multiplier + self.noise_margin)

    def _release_threshold(self) -> float:
        return max(
            self.threshold * 0.65,
            self.noise_floor * 1.35 + self.noise_margin * 0.5,
            self._start_threshold() * 0.82,
        )

    def _track_noise(self, level: float) -> None:
        # A lower percentile follows steady room noise without treating brief
        # speech peaks or knocks as the new ambient level.
        self.noise_window.append(level)
        if len(self.noise_window) >= 10:
            ordered = sorted(self.noise_window)
            ambient = ordered[int((len(ordered) - 1) * 0.25)]
        else:
            ambient = level
        alpha = (
            self.noise_rise_alpha if ambient > self.noise_floor
            else self.noise_fall_alpha
        )
        self.noise_floor += alpha * (ambient - self.noise_floor)

    def _preprocess_pcm16(self, pcm: bytes) -> bytes:
        """Remove DC and low-frequency rumble with a gentle 80 Hz high-pass."""
        if len(pcm) < 2 or len(pcm) % 2:
            raise RuntimeError("El stream Wi-Fi contiene un bloque PCM incompleto.")
        output = bytearray(len(pcm))
        alpha = self.high_pass_alpha
        previous_input = self._hp_previous_input
        previous_output = self._hp_previous_output
        for index, (sample,) in enumerate(struct.iter_unpack("<h", pcm)):
            filtered = alpha * (previous_output + sample - previous_input)
            value = max(-32768, min(32767, round(filtered)))
            struct.pack_into("<h", output, index * 2, value)
            previous_input = sample
            previous_output = filtered
        self._hp_previous_input = previous_input
        self._hp_previous_output = previous_output
        return bytes(output)

    def _contains_voice(self, pcm: bytes) -> bool:
        frame = self._resample_pcm16(pcm, self.capture_sample_rate, 16000)
        expected_bytes = 16000 * self.block_ms // 1000 * 2
        if len(frame) != expected_bytes or self.block_ms not in (10, 20, 30):
            raise RuntimeError(
                "El VAD requiere bloques PCM mono de 10, 20 o 30 ms."
            )
        return self.vad.is_speech(frame, 16000)

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if len(pcm) < 2 or len(pcm) % 2:
            raise RuntimeError("El stream Wi-Fi contiene un bloque PCM incompleto.")
        total = sum(sample * sample for (sample,) in struct.iter_unpack("<h", pcm))
        return math.sqrt(total / (len(pcm) // 2))

    def _wav(self, pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(self.sample_width)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm)
        return output.getvalue()

    @staticmethod
    def _resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
        """Linearly resample signed little-endian mono PCM without extra deps."""
        if source_rate == target_rate:
            return pcm
        if source_rate <= 0 or target_rate <= 0 or len(pcm) % 2:
            raise ValueError("No se puede convertir el bloque PCM recibido.")
        samples = [sample[0] for sample in struct.iter_unpack("<h", pcm)]
        if not samples:
            return b""
        output_count = round(len(samples) * target_rate / source_rate)
        output = bytearray(output_count * 2)
        scale = source_rate / target_rate
        for out_index in range(output_count):
            source_pos = out_index * scale
            left = min(int(source_pos), len(samples) - 1)
            right = min(left + 1, len(samples) - 1)
            fraction = source_pos - left
            value = round(
                samples[left] + (samples[right] - samples[left]) * fraction
            )
            struct.pack_into("<h", output, out_index * 2,
                             max(-32768, min(32767, value)))
        return bytes(output)

    def listen_for_utterance(self, on_speech_start=None, on_idle_tick=None,
                             on_capture_cancelled=None, stop_event=None,
                             silence_seconds=None) -> bytes | None:
        # Discard stale PCM but keep the ambient estimate current between turns.
        while True:
            queued = self.connection.next_stream_item(0)
            if queued is None:
                break
            if queued[0] == "audio":
                filtered = self._preprocess_pcm16(queued[1])
                self._track_noise(self._rms(filtered))

        pre_roll_blocks = max(1, round(self.pre_roll_seconds / self.block_seconds))
        pre_roll = deque(maxlen=pre_roll_blocks)
        frames = []
        speech_active = False
        speech_started_at = 0.0
        last_voice_at = 0.0
        last_audio_at = time.monotonic()
        last_idle_callback = 0.0
        speech_blocks = 0
        speech_levels = []
        start_candidate_blocks = 0
        start_candidate_levels = []
        maximum = int(
            self.capture_sample_rate * self.channels * self.sample_width
            * self.max_recording_seconds
        )
        minimum_blocks = math.ceil(self.min_speech_seconds / self.block_seconds)

        silence_limit = (
            self.silence_seconds if silence_seconds is None
            else float(silence_seconds)
        )
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            item = self.connection.next_stream_item(0.25)
            now = time.monotonic()
            if item is None:
                if on_idle_tick and now - last_idle_callback >= 0.5:
                    on_idle_tick()
                    last_idle_callback = now
                if not self.connection.connected:
                    raise ConnectionError("Se perdió el stream Wi-Fi del MAX9814.")
                if now - last_audio_at >= 5.0:
                    raise TimeoutError(
                        "La conexión Wi-Fi sigue abierta, pero no llegan bloques PCM "
                        "del ADC GPIO34 de la ESP32."
                    )
                continue

            kind, payload = item
            if kind != "audio":
                continue
            if not self.stream_reported:
                print(
                    f"[WIFI AUDIO] Stream ADC activo: {self.capture_sample_rate} Hz, "
                    "mono, 16-bit."
                )
                self.stream_reported = True
            payload = self._preprocess_pcm16(payload)
            level = self._rms(payload)
            last_audio_at = now

            if not speech_active:
                pre_roll.append(payload)
                start_threshold = self._start_threshold()
                has_voice = self._contains_voice(payload)
                if level >= start_threshold and has_voice:
                    start_candidate_blocks += 1
                    start_candidate_levels.append(level)
                else:
                    start_candidate_blocks = 0
                    start_candidate_levels.clear()
                    self._track_noise(level)
                if start_candidate_blocks >= self.start_blocks_required:
                    speech_active = True
                    speech_started_at = now
                    last_voice_at = now
                    frames.extend(pre_roll)
                    speech_blocks = start_candidate_blocks
                    speech_levels.extend(start_candidate_levels)
                    print(
                        f"[VAD] voz detectada; ruido={self.noise_floor:.0f}, "
                        f"umbral={start_threshold:.0f}"
                    )
                    if on_speech_start:
                        on_speech_start()
                continue

            frames.append(payload)
            has_voice = self._contains_voice(payload)
            if level >= self._release_threshold() and has_voice:
                last_voice_at = now
                speech_blocks += 1
                speech_levels.append(level)
            else:
                self._track_noise(level)

            silence_done = now - last_voice_at >= silence_limit
            max_length = now - speech_started_at >= self.max_recording_seconds
            if not silence_done and not max_length:
                continue

            pcm = b"".join(frames)
            minimum_bytes = int(
                self.capture_sample_rate * self.channels * self.sample_width
                * self.min_speech_seconds
            )
            activity_ratio = speech_blocks / max(1, len(frames))
            continuous_noise_at_limit = (
                max_length and activity_ratio >= self.max_continuous_ratio
            )
            enough_activity = (
                speech_blocks >= minimum_blocks
                and activity_ratio >= self.min_activity_ratio
                and len(pcm) >= minimum_bytes
                and not continuous_noise_at_limit
            )
            if enough_activity:
                pcm = pcm[:maximum]
                duration = len(pcm) / (
                    self.capture_sample_rate * self.channels * self.sample_width
                )
                pcm = self._resample_pcm16(
                    pcm, self.capture_sample_rate, self.sample_rate
                )
                print(
                    f"[WIFI AUDIO] PCM convertido a {self.sample_rate} Hz, "
                    f"mono, 16-bit: {len(pcm)} bytes ({duration:.2f} s)."
                )
                print("[VAD] fin de frase; captura aceptada")
                return self._wav(pcm)

            if on_capture_cancelled:
                on_capture_cancelled()
            for captured_level in speech_levels:
                self._track_noise(captured_level)
            print(
                "[VAD] captura descartada: actividad insuficiente "
                "o ruido continuo"
            )
            print("[WIFI AUDIO] Se descartó una detección breve de ruido.")
            pre_roll.clear()
            frames.clear()
            speech_active = False
            speech_blocks = 0
            speech_levels.clear()
            start_candidate_blocks = 0
            start_candidate_levels.clear()
