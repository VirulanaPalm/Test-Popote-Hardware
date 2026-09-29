"""Groq Orpheus TTS adapter returning WAV bytes for local playback."""

from __future__ import annotations

import io
import os
import wave


class GroqTTSClient:
    DEFAULT_MODEL = "canopylabs/orpheus-v1-english"
    MAX_INPUT_CHARACTERS = 200

    def __init__(self, client, model=None, sample_rate=16000):
        self.client = client
        self.model = os.getenv("GROQ_TTS_MODEL") or model or self.DEFAULT_MODEL
        self.sample_rate = int(sample_rate)

    def synthesize(self, text: str, voice: str, instructions: str = "") -> bytes:
        # Orpheus takes vocal directions inline and caps each input at 200 chars.
        direction = instructions.strip()
        direction_prefix = f"{direction} " if direction else ""
        text_limit = self.MAX_INPUT_CHARACTERS - len(direction_prefix)
        if text_limit < 1:
            raise ValueError("La dirección de voz supera el límite de entrada de Orpheus.")

        clips = [
            self._request(f"{direction_prefix}{chunk}", voice)
            for chunk in self._split_text(text, text_limit)
        ]
        if len(clips) == 1:
            return clips[0]
        return self._join_wavs(clips)

    @staticmethod
    def _split_text(text: str, limit: int) -> list[str]:
        chunks = []
        current = ""
        for word in text.split():
            while len(word) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(word[:limit])
                word = word[limit:]
            candidate = f"{current} {word}".strip()
            if len(candidate) > limit:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [""]

    def _request(self, text: str, voice: str) -> bytes:
        response = self.client.audio.speech.create(
            model=self.model,
            voice=voice,
            input=text,
            response_format="wav",
            sample_rate=self.sample_rate,
        )
        return response.read()

    @staticmethod
    def _join_wavs(clips: list[bytes]) -> bytes:
        frame_data = []
        expected = None
        for clip in clips:
            with wave.open(io.BytesIO(clip), "rb") as wav_file:
                params = (
                    wav_file.getnchannels(),
                    wav_file.getsampwidth(),
                    wav_file.getframerate(),
                    wav_file.getcomptype(),
                )
                if expected is None:
                    expected = params
                elif params != expected:
                    raise ValueError("Groq devolvió segmentos WAV incompatibles.")
                frame_data.append(wav_file.readframes(wav_file.getnframes()))

        channels, sample_width, sample_rate, compression = expected
        if compression != "NONE" or sample_width != 2:
            raise ValueError("Groq TTS debe devolver WAV PCM de 16 bits.")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"".join(frame_data))
        return output.getvalue()
