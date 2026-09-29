"""Standalone Wi-Fi conversation app; main.py remains hardware diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

from ai.client import GroqClient
from hardware.connection import ESP32Connection
from hardware.controller import HardwareController
from hardware.wifi_audio import WiFiAudioServer
from protocol.protocol import FACE_CONTROL_CAPABILITY, PING
from robot.identity import identity_context, load_identity
from robot.memory import PopoteMemory
from robot.state import RobotState, RobotStateMachine
from voice.speech_to_text import SpeechToText
from voice.text_to_speech import TextToSpeech
from voice.piper_tts import PiperTTSClient
from voice.wifi_listener import WiFiAudioListener


ROOT = Path(__file__).resolve().parent
PERSONALITY_FILE = ROOT / "config" / "personality.json"
TRANSPORT_FILE = ROOT / "config" / "transport.json"
EMOTION_COMMANDS = {
    "ponete feliz": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "se feliz": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "cara feliz": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "pone una cara feliz": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "hace una cara feliz": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "pone cara feliz": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "alegrate": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "sonrei": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "ponete alegre": ("HAPPY", "¡Listo, hermano! Estoy feliz."),
    "ponete cansado": ("SLEEPY", "Se me están cerrando los ojos..."),
    "ponete con sueno": ("SLEEPY", "Se me están cerrando los ojos..."),
    "ponete triste": ("SAD", "Uy, qué tristeza. Ya estoy triste."),
    "cara triste": ("SAD", "Uy, qué tristeza. Ya estoy triste."),
    "pone una cara triste": ("SAD", "Bueno, hermano... ahí va la cara triste."),
    "hace una cara triste": ("SAD", "Bueno, hermano... ahí va la cara triste."),
    "pone cara triste": ("SAD", "Bueno, hermano... ahí va la cara triste."),
    "entristecete": ("SAD", "Uy, qué tristeza. Ya estoy triste."),
    "enojate": ("ANGRY", "Bueno, bueno... me enojé."),
    "ponete enojado": ("ANGRY", "Bueno, bueno... me enojé."),
    "cara de enojado": ("ANGRY", "Bueno, bueno... me enojé."),
    "sorprendete": ("SURPRISED", "¡Apa! Me sorprendí."),
    "ponete sorprendido": ("SURPRISED", "¡Apa! Me sorprendí."),
    "cara de sorpresa": ("SURPRISED", "¡Apa! Me sorprendí."),
    "ponete confundido": ("CONFUSED", "Mmm... me dejaste pensando."),
    "cara de confundido": ("CONFUSED", "Mmm... me dejaste pensando."),
    "confundite": ("CONFUSED", "Mmm... me dejaste pensando."),
    "tenes miedo": ("SURPRISED", "¡Uy! Me asusté."),
    "ponete asustado": ("SURPRISED", "¡Uy! Me asusté."),
    "ponete serio": ("NORMAL", "Listo, activo mi modo serio."),
    "cara seria": ("NORMAL", "Listo, activo mi modo serio."),
    "dormite": ("SLEEPING", "Bueno, me duermo. Despertame cuando me necesites."),
    "anda a dormir": ("SLEEPING", "Bueno, me duermo. Despertame cuando me necesites."),
    "dormi": ("SLEEPING", "Bueno, me duermo. Despertame cuando me necesites."),
    "despertate": ("NORMAL", "¡Ya estoy despierto!"),
    "desperta": ("NORMAL", "¡Ya estoy despierto!"),
    "levantate": ("NORMAL", "¡Ya estoy despierto!"),
    "ponete normal": ("NORMAL", "Listo, vuelvo a la normalidad."),
    "cara normal": ("NORMAL", "Listo, vuelvo a la normalidad."),
    "volve a la normalidad": ("NORMAL", "Listo, vuelvo a la normalidad."),
    "reite": ("LAUGHING", "¡Ja, ja! Me agarró la risa."),
    "ponete a reir": ("LAUGHING", "¡Ja, ja! Me agarró la risa."),
    "hacete el gracioso": ("LAUGHING", "¡Ja, ja! Me agarró la risa."),
}


class _DialogueOnlyStdout:
    """Hide operational chatter while preserving the two conversation lines."""

    def __init__(self, output):
        self.output = output
        self.forward_newline = False

    def write(self, value):
        if value == "\n" and self.forward_newline:
            self.output.write(value)
            self.forward_newline = False
            return len(value)
        if value.startswith((
            "POPOTE EN MARCHA",
            "Ahora podés hablar.",
            "Usuario: ",
            "Popote: ",
            "[VAD] ",
            "[STT] ",
        )):
            self.output.write(value)
            self.forward_newline = not value.endswith("\n")
        else:
            self.forward_newline = False
        return len(value)

    def flush(self):
        self.output.flush()

    def isatty(self):
        return self.output.isatty()

    @property
    def encoding(self):
        return self.output.encoding


def load_personality(path=PERSONALITY_FILE):
    with Path(path).open("r", encoding="utf-8") as source:
        profile = json.load(source)
    for key in ("name", "system_instructions", "microphone", "voice", "idle", "ai"):
        if key not in profile:
            raise ValueError(f"Falta la sección {key!r} en {path}.")
    return profile


def load_transport(path=TRANSPORT_FILE):
    with Path(path).open("r", encoding="utf-8") as source:
        return json.load(source)


def build_system_instructions(profile, identity=None, memory_context=None):
    """Compose the editable personality fields into the provider prompt."""
    lines = [f"Sos {profile['name']}. "]
    for key, label in (
        ("personality", "Personalidad"),
        ("humor", "Humor"),
        ("speaking_style", "Forma de hablar"),
        ("behavior", "Comportamiento"),
        ("system_instructions", "Instrucciones adicionales"),
    ):
        value = profile.get(key)
        if value:
            lines.append(f"{label}: {value}")
    if identity is not None:
        lines.append(identity_context(identity))
    if memory_context:
        lines.append(memory_context)
    return "\n".join(lines)


class ConversationApp:
    def __init__(self, profile, listener, ai_client, stt, tts,
                 connection=None, controller=None, wifi_server=None,
                 transport_settings=None, pc_mic_fallback=False,
                 identity=None, memory=None):
        self.profile = profile
        self.identity = identity
        self.memory = memory or PopoteMemory()
        self.listener = listener
        self.ai = ai_client
        self.stt = stt
        self.tts = tts
        self.usb_connection = connection or ESP32Connection()
        self.connection = self.usb_connection
        self.wifi_server = wifi_server
        self.transport_settings = transport_settings or {}
        self.pc_mic_fallback = pc_mic_fallback
        self.controller = controller
        self.face_enabled = False
        self._face_command_lock = threading.Lock()
        self._emotion_lock = threading.RLock()
        self._emotion_override = None
        self._emotion_timer = None
        self._emotion_duration = max(
            1.0, float(profile.get("emotion_command_duration_seconds", 8))
        )
        self.history = []
        self.state = RobotStateMachine(profile["idle"], self._state_changed)

    def _state_changed(self, state):
        face_state = self._face_for_state(state)
        if self.face_enabled and self.controller:
            if state is RobotState.LISTENING:
                threading.Thread(
                    target=self._send_face_state,
                    args=(state,),
                    name="popote-face-listening",
                    daemon=True,
                ).start()
                print(f"[POPOTE] {state.value}")
                return
            try:
                accepted = self._send_face_command(face_state)
            except (ConnectionError, OSError) as error:
                print(f"[FACE] Enlace ESP32 interrumpido ({error}); continúo sin OLED.")
                self.face_enabled = False
                accepted = True
            if not accepted:
                print("[FACE] El firmware no aceptó el estado; continúo sin control OLED.")
                self.face_enabled = False
        print(f"[POPOTE] {state.value}")

    def _send_face_state(self, state):
        try:
            if self.state.current is not state:
                return
            face_state = self._face_for_state(state)
            accepted = self._send_face_command(face_state)
        except (ConnectionError, OSError) as error:
            print(f"[FACE] Enlace ESP32 interrumpido ({error}); continÃºo sin OLED.")
            self.face_enabled = False
            return
        if not accepted:
            print("[FACE] El firmware no aceptÃ³ el estado; continÃºo sin control OLED.")
            self.face_enabled = False

    def _face_for_state(self, state):
        with self._emotion_lock:
            if state is RobotState.LISTENING and self._emotion_override in {"SLEEPING", "LAUGHING"}:
                self._clear_emotion_override_locked()
            if state is RobotState.LISTENING:
                return "LISTENING"
            if state in {RobotState.THINKING, RobotState.ERROR}:
                return state.value
            if state is RobotState.SPEAKING:
                return "LAUGHING" if self._emotion_override == "LAUGHING" else "SPEAKING"
            return self._emotion_override or state.value

    def _send_face_command(self, face_state):
        with self._face_command_lock:
            if not self.face_enabled or not self.controller:
                return False
            try:
                return self.controller.set_face(face_state)
            except (ConnectionError, OSError):
                self.face_enabled = False
                return False

    def _clear_emotion_override_locked(self):
        self._emotion_override = None
        timer = self._emotion_timer
        self._emotion_timer = None
        if timer:
            timer.cancel()

    def _expire_emotion(self, expression):
        with self._emotion_lock:
            if self._emotion_override != expression:
                return
            self._clear_emotion_override_locked()
        if self.face_enabled and self.controller:
            return_state = self.state.current.value if self.state else "NORMAL"
            self._send_face_command(return_state)

    def _activate_emotion(self, expression, defer=False):
        with self._emotion_lock:
            self._clear_emotion_override_locked()
            self._emotion_override = expression
            if expression != "SLEEPING":
                duration = self._emotion_duration
                if expression == "LAUGHING":
                    duration = min(duration, 2.8)
                elif expression == "SURPRISED":
                    duration = min(duration, 2.6)
                self._emotion_timer = threading.Timer(
                    duration, self._expire_emotion, args=(expression,)
                )
                self._emotion_timer.daemon = True
                self._emotion_timer.start()
            if defer and self.state.current in {
                RobotState.LISTENING, RobotState.THINKING,
                RobotState.SPEAKING, RobotState.ERROR,
            }:
                return True
        try:
            return self._send_face_command(expression)
        except (ConnectionError, OSError):
            self.face_enabled = False
            return False

    def initialize(self, connect_hardware=True):
        """Initialize local components; optionally attach an ESP32 face."""
        if connect_hardware:
            wifi_settings = self.transport_settings.get("wifi", {})
            if wifi_settings.get("enabled", True) and self.wifi_server:
                try:
                    self.wifi_server.start()
                    timeout = float(wifi_settings.get("device_wait_seconds", 10))
                    while not self.wifi_server.wait_for_device(timeout):
                        print(
                            "[WIFI] ESP32 todavía no conectada; el servidor sigue "
                            "activo y volverá a aceptar el dispositivo."
                        )
                    if self.wifi_server.wait_for_device(timeout):
                        self.connection = self.wifi_server
                        self.controller = HardwareController(self.connection)
                        if self.controller.command(
                            PING, expected="PONG", timeout=2
                        ) != "PONG":
                            raise ConnectionError("El ESP32 Wi-Fi no respondiÃ³ PONG.")
                        self.controller.query_device_info()
                        caps = self.controller.capabilities or set()
                        required = {"WIFI", "AUDIO_STREAM", "MIC", "AUDIO"}
                        if not required.issubset(caps):
                            raise ConnectionError(
                                "El ESP32 no anuncia todas las capacidades de audio Wi-Fi."
                            )
                        self.face_enabled = FACE_CONTROL_CAPABILITY in caps
                        self.listener = WiFiAudioListener(
                            self.wifi_server,
                            self.profile["microphone"],
                            self.transport_settings.get("audio", {}),
                        )
                        self.tts.client.prepare()
                        if self.face_enabled:
                            self._state_changed(self.state.current)
                        print("[AUDIO] MicrÃ³fono de la ESP32 por Wi-Fi; salida de voz local.")
                        self._initialized = True
                        return
                    raise TimeoutError(
                        "el servidor Wi-Fi no recibió el HELLO de la ESP32 "
                        f"en {timeout:g} segundos"
                    )
                except Exception as error:
                    self.wifi_server.stop()
                    self.connection = self.usb_connection
                    self.controller = None
                    if not self.pc_mic_fallback:
                        raise RuntimeError(
                            "Captura MAX9814 por Wi-Fi no disponible; "
                            "no se abrirá el micrófono local del PC. "
                            f"Error de transporte: {error}"
                        ) from error
                    print(
                        f"[WARNING] Audio Wi-Fi no disponible ({error}); "
                        "se solicitó explícitamente el micrófono local del PC."
                    )

            if self.listener is None and self.pc_mic_fallback:
                from voice.listener import AudioListener
                self.listener = AudioListener(self.profile["microphone"])

            try:
                self.usb_connection.connect()
                self.connection = self.usb_connection
                self.controller = HardwareController(self.usb_connection)
                if self.controller.command(PING, expected="PONG", timeout=2) != "PONG":
                    raise ConnectionError("El dispositivo no respondió PONG.")
                self.controller.query_device_info()
                self.face_enabled = (
                    self.controller.capabilities is not None
                    and FACE_CONTROL_CAPABILITY in self.controller.capabilities
                )
                if not self.face_enabled:
                    print("[FACE] Firmware sin FACE_CONTROL.")
                else:
                    self._state_changed(self.state.current)
            except Exception as error:
                self.connection.disconnect()
                self.controller = None
                self.face_enabled = False
                print(f"[WARNING] ESP32 por USB no disponible ({error}).")
        if connect_hardware and self.listener is None:
            raise RuntimeError(
                "No hay captura de voz configurada. Se requiere el stream Wi-Fi "
                "del MAX9814; para usar el micrófono del PC, iniciá con "
                "--pc-mic-fallback de forma explícita."
            )
        self._initialized = True

    def _on_speech_start(self):
        self.state.set(RobotState.LISTENING, interaction=True)

    def _on_idle_tick(self):
        self.state.update_idle()

    def _on_capture_cancelled(self):
        self.state.cancel_listening()

    @staticmethod
    def _normalize_command(text):
        normalized = unicodedata.normalize("NFD", text.lower())
        normalized = "".join(
            character for character in normalized
            if unicodedata.category(character) != "Mn"
        )
        return re.sub(r"[^a-z0-9]+", " ", normalized).strip()

    @classmethod
    def _emotion_command(cls, text):
        words = cls._normalize_command(text).split()
        while words and words[0] in {
            "popote", "papote", "ahora", "che", "y", "no", "bueno", "dale"
        }:
            words.pop(0)
        words = [word for word in words if word not in {"popote", "papote"}]
        if len(words) >= 2 and words[-2:] == ["por", "favor"]:
            words = words[:-2]
        # Whisper sometimes repeats the complete utterance. Collapse only an
        # exact repetition so longer conversational sentences still go to chat.
        for width in range(1, len(words) // 2 + 1):
            if len(words) % width == 0 and all(
                word == words[index % width]
                for index, word in enumerate(words)
            ):
                words = words[:width]
                break
        normalized = " ".join(words)
        command = EMOTION_COMMANDS.get(normalized)
        if command:
            return command

        # Whisper varies voseo imperatives and often drops "cara" or changes it
        # to "cada". Accept those forms only as a short imperative, so ordinary
        # conversation about an emotion still goes to chat.
        match = re.fullmatch(
            r"(?:pone|pon|ponete|hace|hacete|hacer|poner|poned) "
            r"(?:(?:la|una|un) )?(?:(?:cara|caras|cada)(?: de )? )?"
            r"(feliz|alegre|triste|enojado|enojada|sorprendido|sorprendida|"
            r"confundido|confundida|serio|seria|normal)",
            normalized,
        )
        if not match:
            return None
        emotion = match.group(1)
        expression = {
            "feliz": "HAPPY",
            "alegre": "HAPPY",
            "triste": "SAD",
            "enojado": "ANGRY",
            "enojada": "ANGRY",
            "sorprendido": "SURPRISED",
            "sorprendida": "SURPRISED",
            "confundido": "CONFUSED",
            "confundida": "CONFUSED",
            "serio": "NORMAL",
            "seria": "NORMAL",
            "normal": "NORMAL",
        }[emotion]
        answers = {
            "HAPPY": "¡Listo, hermano! Estoy feliz.",
            "SAD": "Uy, qué tristeza. Ya estoy triste.",
            "ANGRY": "Bueno, bueno... me enojé.",
            "SURPRISED": "¡Apa! Me sorprendí.",
            "CONFUSED": "Mmm... me dejaste pensando.",
            "NORMAL": "Listo, vuelvo a la normalidad.",
        }
        return expression, answers[expression]

    def _handle_emotion_command(self, transcript):
        command = self._emotion_command(transcript)
        if command is None:
            return False
        expression, answer = command
        if not self.face_enabled or not self.controller:
            answer = "Ahora no puedo cambiar la cara porque no tengo conexión con la OLED."
        elif not self._activate_emotion(expression):
            answer = "No pude cambiar la expresión de la OLED, hermano."
        print(f"Popote: {answer}")
        self.state.set(RobotState.SPEAKING)
        if self._speak_with_interrupt(answer):
            self.state.set(RobotState.LISTENING, interaction=True)
        else:
            self.state.set(RobotState.IDLE, interaction=True)
        return True

    def _watch_stop_command(self, stop_event, interrupted_event):
        while not stop_event.is_set():
            try:
                recording = self.listener.listen_for_utterance(
                    stop_event=stop_event, silence_seconds=0.35
                )
                if recording is None:
                    continue
                heard = self.stt.transcribe(recording, language="es")
                if self._normalize_command(heard) == "para popote":
                    interrupted_event.set()
                    stop_event.set()
                    return
            except Exception:
                return

    def _speak_with_interrupt(self, text):
        stop_event = threading.Event()
        interrupted_event = threading.Event()
        monitor = []

        def start_monitor():
            if not isinstance(self.listener, WiFiAudioListener):
                return
            worker = threading.Thread(
                target=self._watch_stop_command,
                args=(stop_event, interrupted_event),
                name="popote-voice-interrupt",
                daemon=True,
            )
            monitor.append(worker)
            worker.start()

        try:
            self.tts.speak(
                text, stop_event=stop_event,
                on_playback_start=start_monitor,
            )
        finally:
            stop_event.set()
            if monitor:
                monitor[0].join()
        return interrupted_event.is_set()

    def _face_reaction_to_text(self, text):
        if not self.face_enabled or not self.controller:
            return
        with self._emotion_lock:
            if self._emotion_override:
                return
        words = set(self._normalize_command(text).split())
        if words.intersection({"gracias", "genial", "buenisimo", "excelente"}):
            expression = "HAPPY"
        else:
            return
        try:
            self._activate_emotion(expression, defer=True)
        except (ConnectionError, OSError):
            self.face_enabled = False

    def run(self):
        if not getattr(self, "_initialized", False):
            self.initialize()
        print("POPOTE EN MARCHA\nAhora podés hablar.")
        try:
            welcome = (
                "Popote iniciado. Soy un invento de Agus; a partir de ahora "
                "soy tu compañero, amigo y te puedo ayudar en lo que quieras."
            )
            print(f"Popote: {welcome}")
            self.state.set(RobotState.SPEAKING)
            self._speak_with_interrupt(welcome)
            self.state.set(RobotState.IDLE, interaction=True)
            while True:
                self.state.update_idle()
                try:
                    wav_audio = self.listener.listen_for_utterance(
                        on_speech_start=self._on_speech_start,
                        on_idle_tick=self._on_idle_tick,
                        on_capture_cancelled=self._on_capture_cancelled,
                    )
                except (TimeoutError, ConnectionError) as error:
                    self.state.set(RobotState.ERROR)
                    print(
                        "[WIFI AUDIO] Captura interrumpida: "
                        f"{error}. Se conserva la conexión y se reintentará."
                    )
                    time.sleep(0.5)
                    self.state.set(RobotState.IDLE)
                    continue
                if wav_audio is None:
                    self.state.cancel_listening()
                    continue
                self.state.set(RobotState.THINKING)
                stage = "Groq Whisper/STT"
                try:
                    print("[STT] procesando audio...")
                    transcript = self.stt.transcribe(wav_audio, language="es")
                    if not transcript:
                        print("[STT] resultado vacío; se descarta")
                        self.state.cancel_listening()
                        continue
                    print(f"[STT] resultado: {transcript}")
                    print(f"Usuario: {transcript}")
                    if self._handle_emotion_command(transcript):
                        continue
                    save_request = self.memory.capture_request(transcript)
                    if save_request:
                        memory_type, memory_content = save_request
                        self.memory.add(memory_type, memory_content, importance="high")
                    if (transcript.rstrip().endswith("?")
                            or transcript.lstrip().startswith("¿")):
                        self.state.set(RobotState.CURIOUS)
                        self.state.set(RobotState.THINKING)
                    self._face_reaction_to_text(transcript)
                    stage = "Groq Chat"
                    answer = self.ai.respond(
                        transcript,
                        build_system_instructions(
                            self.profile,
                            self.identity,
                            self.memory.context_for(transcript),
                        ),
                        self.history,
                    )
                    print(f"Popote: {answer}")
                    self.history.extend((
                        {"role": "user", "content": transcript},
                        {"role": "assistant", "content": answer},
                    ))
                    self.history = self.history[-8:]
                    self.state.set(RobotState.SPEAKING)
                    stage = "TTS local / reproducción Windows"
                    if self._speak_with_interrupt(answer):
                        self.state.set(RobotState.LISTENING, interaction=True)
                        continue
                    self.state.set(RobotState.IDLE, interaction=True)
                except Exception as error:
                    self.state.set(RobotState.ERROR)
                    details = []
                    current_error = error
                    secret = os.environ.get("GROQ_API_KEY")
                    while current_error is not None:
                        message = str(current_error)
                        if secret:
                            message = message.replace(secret, "[REDACTED]")
                        details.append(f"{type(current_error).__name__}: {message}")
                        current_error = current_error.__cause__ or current_error.__context__
                    print(
                        f"[ERROR] Etapa: {stage}. No pude completar este turno: "
                        + " <- ".join(details),
                        file=sys.stderr,
                    )
                    time.sleep(0.8)
                    self.state.set(RobotState.IDLE)
        finally:
            with self._emotion_lock:
                self._clear_emotion_override_locked()
            self.connection.disconnect()
            if self.wifi_server:
                self.wifi_server.stop()


def build_app(profile=None, pc_mic_fallback=False):
    profile = profile or load_personality()
    identity = load_identity()
    transport_settings = load_transport()
    ai_client = GroqClient(
        chat_model=profile["ai"].get("chat_model"),
        transcription_model=profile["ai"].get("transcription_model"),
    )
    stt = SpeechToText(ai_client)
    tts = TextToSpeech(
        PiperTTSClient(voice=profile["voice"].get("voice", PiperTTSClient.VOICE_NAME)),
        profile["voice"],
    )
    wifi_server = WiFiAudioServer(transport_settings.get("wifi", {}))
    return ConversationApp(
        profile, None, ai_client, stt, tts,
        wifi_server=wifi_server,
        transport_settings=transport_settings,
        pc_mic_fallback=pc_mic_fallback,
        identity=identity,
        memory=PopoteMemory(),
    )


def main():
    parser = argparse.ArgumentParser(description="Conversación de Popote usando el MAX9814 por Wi-Fi.")
    parser.add_argument(
        "--check", action="store_true",
        help="validar SDK, clave y configuración sin abrir serial ni llamar a Groq",
    )
    parser.add_argument(
        "--pc-mic-fallback", action="store_true",
        help="usar explícitamente el micrófono local del PC si falla el audio Wi-Fi",
    )
    args = parser.parse_args()
    try:
        if args.check:
            try:
                import groq  # noqa: F401
            except ImportError:
                raise RuntimeError(
                    "SDK groq no instalado; ejecutá python -m pip install -r requirements.txt."
                ) from None
            if not os.environ.get("GROQ_API_KEY"):
                raise RuntimeError("GROQ_API_KEY no configurada")
            piper_client = PiperTTSClient()
            if not piper_client.available():
                raise RuntimeError(
                    "Piper TTS o su modelo local no está disponible. "
                    "Se requiere piper-tts y el modelo de voz configurado."
                )
        else:
            sys.stdout = _DialogueOnlyStdout(sys.stdout)
        app = build_app(pc_mic_fallback=args.pc_mic_fallback)
        if args.check:
            app.initialize(connect_hardware=False)
            print(
                "Inicialización local OK; configuración y backend de audio "
                "disponibles (sin abrir dispositivos ni llamar a Groq)."
            )
            return 0
        app.run()
    except KeyboardInterrupt:
        print("\nConversación finalizada.", file=sys.stderr)
        return 0
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
