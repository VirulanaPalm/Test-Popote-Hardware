"""Speech transcription adapter, independent from the selected provider."""


class SpeechToText:
    def __init__(self, client):
        self.client = client

    def transcribe(self, wav_audio: bytes, language: str = "es") -> str:
        return self.client.transcribe(wav_audio, language=language).strip()
