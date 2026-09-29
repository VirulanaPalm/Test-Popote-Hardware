"""Speech synthesis and playback for local or ESP32 audio output."""

from __future__ import annotations

import io
import threading
import wave


class TextToSpeech:
    def __init__(self, client, voice_settings: dict, audio_backend=None,
                 audio_output=None):
        self.client = client
        self.settings = voice_settings
        self.audio_output = audio_output
        if audio_backend is None and audio_output is None:
            try:
                import sounddevice as audio_backend
            except ImportError as error:
                raise RuntimeError(
                    "Falta sounddevice. Instalá las dependencias con "
                    "python -m pip install -r requirements.txt."
                ) from error
        self.audio = audio_backend

    def speak(self, text: str, stop_event: threading.Event | None = None,
              on_playback_start=None) -> bool:
        print("[ETAPA] TTS local")
        synthesize_chunks = getattr(self.client, "synthesize_chunks", None)
        if self.audio_output is None and callable(synthesize_chunks):
            sample_rate = self.client.sample_rate
            with self.audio.RawOutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            ) as stream:
                if on_playback_start:
                    on_playback_start()
                for chunk in synthesize_chunks(
                    text,
                    voice=self.settings.get("voice"),
                    instructions=self.settings.get("instructions", ""),
                ):
                    if stop_event is not None and stop_event.is_set():
                        return True
                    stream.write(chunk)
            return bool(stop_event and stop_event.is_set())
        try:
            wav_bytes = self.client.synthesize(
                text,
                voice=self.settings.get("voice"),
                instructions=self.settings.get("instructions", ""),
            )
        except Exception as error:
            raise RuntimeError(f"TTS local falló: {error}") from error
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
            if sample_width != 2 or wav_file.getcomptype() != "NONE":
                raise ValueError("El TTS devolvió audio que no es PCM de 16 bits.")
            audio_data = wav_file.readframes(wav_file.getnframes())

        if self.audio_output:
            if channels != 1:
                raise ValueError("El TTS Wi-Fi requiere WAV mono.")
            if sample_rate != 16000:
                audio_data = self._resample_mono_s16le(audio_data, sample_rate, 16000)
                sample_rate = 16000
            self.audio_output(audio_data, sample_rate, channels, sample_width * 8)
            return

        block_bytes = sample_rate * channels * sample_width // 5
        print("[ETAPA] Reproducción local WAV")
        if on_playback_start:
            on_playback_start()
        with self.audio.RawOutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
        ) as stream:
            for offset in range(0, len(audio_data), block_bytes):
                if stop_event is not None and stop_event.is_set():
                    return True
                stream.write(audio_data[offset:offset + block_bytes])
        return bool(stop_event and stop_event.is_set())

    @staticmethod
    def _resample_mono_s16le(audio_data: bytes, source_rate: int,
                            target_rate: int) -> bytes:
        """Convert mono signed 16-bit PCM to the ESP32 transport rate."""
        import numpy as np

        samples = np.frombuffer(audio_data, dtype="<i2")
        if samples.size == 0:
            return audio_data
        target_count = max(1, round(samples.size * target_rate / source_rate))
        source_positions = np.arange(samples.size, dtype=np.float64)
        target_positions = np.arange(target_count, dtype=np.float64) * (
            source_rate / target_rate
        )
        converted = np.interp(target_positions, source_positions, samples)
        return np.clip(np.rint(converted), -32768, 32767).astype("<i2").tobytes()
