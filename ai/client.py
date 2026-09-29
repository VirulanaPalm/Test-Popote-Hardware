"""Groq adapter for conversational responses and speech transcription."""

from __future__ import annotations

import os

try:
    from groq import Groq
except ImportError:  # Keep --check able to report a useful dependency error.
    Groq = None


class GroqClient:
    """Provider adapter; the caller owns and supplies the conversation history."""

    DEFAULT_CHAT_MODEL = "openai/gpt-oss-120b"
    DEFAULT_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

    def __init__(self, chat_model=None, transcription_model=None):
        if Groq is None:
            raise RuntimeError(
                "Falta el SDK groq. Instalalo con "
                "python -m pip install -r requirements.txt."
            )
        try:
            self.client = Groq(api_key=os.environ["GROQ_API_KEY"])
        except KeyError:
            raise RuntimeError("GROQ_API_KEY no configurada") from None
        self.chat_model = (
            os.getenv("GROQ_CHAT_MODEL") or chat_model or self.DEFAULT_CHAT_MODEL
        )
        self.transcription_model = (
            os.getenv("GROQ_TRANSCRIPTION_MODEL") or transcription_model
            or self.DEFAULT_TRANSCRIPTION_MODEL
        )

    def transcribe(self, wav_audio: bytes, language: str = "es") -> str:
        result = self.client.audio.transcriptions.create(
            file=("popote.wav", wav_audio, "audio/wav"),
            model=self.transcription_model,
            language=language,
            response_format="json",
        )
        return (result.text or "").strip()

    def respond(self, text: str, instructions: str, history=None) -> str:
        messages = [{"role": "system", "content": instructions}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": text})
        request = {
            "model": self.chat_model,
            "messages": messages,
            "max_tokens": 160,
            "temperature": 0.7,
            "reasoning_effort": "low",
            "include_reasoning": False,
            "tool_choice": "none",
        }
        try:
            result = self.client.chat.completions.create(**request)
        except Exception as error:
            # GPT-OSS can occasionally emit an unsolicited tool-call token even
            # though this application supplies no tools. Retry that specific
            # Groq 400 once with a plain-text instruction and lower randomness.
            if (getattr(error, "status_code", None) != 400
                    or "tool_use_failed" not in str(error)):
                raise
            retry_messages = list(messages)
            retry_messages[0] = {
                "role": "system",
                "content": (
                    instructions + "\nRespondé únicamente con texto conversacional. "
                    "No intentes usar herramientas, funciones ni llamadas externas."
                ),
            }
            retry = {**request, "messages": retry_messages, "temperature": 0.1}
            result = self.client.chat.completions.create(**retry)
        answer = result.choices[0].message.content
        if not answer or not answer.strip():
            raise RuntimeError("Groq no devolvió texto para responder.")
        return answer.strip()
