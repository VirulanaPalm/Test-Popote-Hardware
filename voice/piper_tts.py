"""Local Piper speech synthesis adapter with the existing TTS interface."""

from __future__ import annotations

import io
import os
import wave
from pathlib import Path


VOICE_NAME = "es_ES-davefx-medium"


def model_path() -> Path:
    configured = os.environ.get("POPO_PIPER_MODEL")
    if configured:
        return Path(configured).expanduser()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return local_app_data / "Popote" / "models" / f"{VOICE_NAME}.onnx"


class PiperTTSClient:
    """Synthesize locally and return a WAV, compatible with TextToSpeech."""

    VOICE_NAME = VOICE_NAME

    def __init__(self, voice: str = VOICE_NAME):
        if voice != VOICE_NAME:
            raise ValueError(f"Voz Piper no configurada: {voice}")
        self.voice = voice
        self._engine = None

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return model_path().is_file() and model_path().with_suffix(
            model_path().suffix + ".json"
        ).is_file()

    def prepare(self) -> None:
        """Load the local model before listening so the first reply starts sooner."""
        if self._engine is not None:
            return
        path = model_path()
        config_path = path.with_suffix(path.suffix + ".json")
        if not path.is_file() or not config_path.is_file():
            raise RuntimeError(
                f"No se encontró el modelo local Piper {self.voice}: {path}."
            )
        try:
            from piper import PiperVoice
            self._engine = PiperVoice.load(str(path), config_path=str(config_path))
        except Exception as error:
            raise RuntimeError(f"No se pudo cargar Piper local: {error}") from error

    @property
    def sample_rate(self) -> int:
        if self._engine is None:
            self.prepare()
        return self._engine.config.sample_rate

    def synthesize_chunks(self, text: str, voice: str = VOICE_NAME,
                          instructions: str = ""):
        """Yield PCM chunks as Piper produces them, avoiding a full WAV wait."""
        del instructions
        if voice != self.voice:
            raise ValueError(f"Voz local solicitada no disponible: {voice}")
        if self._engine is None:
            self.prepare()
        try:
            for chunk in self._engine.synthesize(text):
                if chunk.audio_int16_bytes:
                    yield chunk.audio_int16_bytes
        except Exception as error:
            raise RuntimeError(f"Piper local no pudo sintetizar el texto: {error}") from error

    def synthesize(self, text: str, voice: str = VOICE_NAME,
                   instructions: str = "") -> bytes:
        del instructions  # Piper has no prompt/instructions parameter.
        if voice != self.voice:
            raise ValueError(f"Voz local solicitada no disponible: {voice}")
        path = model_path()
        config_path = path.with_suffix(path.suffix + ".json")
        if not path.is_file() or not config_path.is_file():
            raise RuntimeError(
                f"No se encontró el modelo local Piper {self.voice}: {path}. "
                "Instalá/descargá ese modelo para habilitar la voz local."
            )
        if self._engine is None:
            try:
                from piper import PiperVoice
                self._engine = PiperVoice.load(str(path), config_path=str(config_path))
            except Exception as error:
                raise RuntimeError(f"No se pudo cargar Piper local: {error}") from error

        pcm = bytearray()
        try:
            for chunk in self._engine.synthesize(text):
                pcm.extend(chunk.audio_int16_bytes)
        except Exception as error:
            raise RuntimeError(f"Piper local no pudo sintetizar el texto: {error}") from error

        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._engine.config.sample_rate)
            wav_file.writeframes(pcm)
        return output.getvalue()
