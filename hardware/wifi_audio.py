"""PC-side persistent TCP server for ESP32 control and PCM audio."""

from __future__ import annotations

import queue
import socket
import threading
import time

from protocol.wifi_stream import AUDIO_DOWN, AUDIO_UP, CONTROL, encode_frame, recv_frame


DISCOVERY_MESSAGE = "POPOTE_DISCOVERY_V1"
MAX_QUEUED_AUDIO_FRAMES = 256


class WiFiAudioServer:
    def __init__(self, settings: dict):
        self.settings = settings
        self.host = settings.get("bind_host", "0.0.0.0")
        self.port = int(settings.get("pc_port", 39101))
        self.discovery_port = int(settings.get("discovery_port", 39100))
        self._server = None
        self._client = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._playback_result = queue.Queue()
        self._responses = queue.Queue()
        # Keep only a few seconds of recent audio while cloud stages run.
        # A slow STT/LLM/TTS request must not grow memory or replay stale PCM.
        self._stream = queue.Queue(maxsize=MAX_QUEUED_AUDIO_FRAMES)
        self._accept_thread = None
        self._beacon_thread = None
        self._reader_thread = None
        self.device_info = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self):
        if self._server:
            return
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # On Windows SO_REUSEADDR can let a second process bind the same port;
        # the ESP32 may then connect to the other listener and this one times out.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self._server.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        else:
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(1)
        self._server.settimeout(0.5)
        self._accept_thread = threading.Thread(
            target=self._accept_clients, name="popote-wifi-accept", daemon=True
        )
        self._beacon_thread = threading.Thread(
            target=self._broadcast_beacon, name="popote-wifi-discovery", daemon=True
        )
        self._accept_thread.start()
        self._beacon_thread.start()
        print(f"[WIFI] Servidor TCP escuchando en {self.host}:{self.port}")

    def wait_for_device(self, timeout: float) -> bool:
        return self._connected.wait(timeout)

    def _broadcast_beacon(self):
        beacon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        beacon.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = f"{DISCOVERY_MESSAGE}:{self.port}".encode("ascii")
        try:
            while not self._stop.is_set():
                try:
                    beacon.sendto(payload, ("255.255.255.255", self.discovery_port))
                except OSError as error:
                    print(f"[WIFI] Descubrimiento no disponible: {error}")
                self._stop.wait(1.0)
        finally:
            beacon.close()

    def _accept_clients(self):
        while not self._stop.is_set():
            try:
                client, address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._close_client()
            # recv_frame consumes a complete length-prefixed frame; a socket
            # timeout mid-header/body would discard bytes already read.
            # Blocking recv is safe because _close_client shuts the socket down.
            client.settimeout(None)
            with self._send_lock:
                self._client = client
                self._connected.clear()
                self.device_info = None
            self._reader_thread = threading.Thread(
                target=self._read_client,
                args=(client,),
                name="popote-wifi-reader",
                daemon=True,
            )
            self._reader_thread.start()
            print(f"[WIFI] Cliente TCP conectado desde {address[0]}")

    def _read_client(self, client):
        try:
            while not self._stop.is_set() and client is self._client:
                frame_type, payload = recv_frame(client)
                if frame_type == CONTROL:
                    message = payload.decode("utf-8", errors="replace").strip()
                    if message.startswith("HELLO\t"):
                        self.device_info = message.split("\t")
                        self._connected.set()
                        print("[WIFI] ESP32 POPOTE_HARDWARE identificado")
                    elif message.startswith("EVENT:"):
                        self._queue_stream_item(("event", message))
                    elif message.startswith("AUDIO_OUT_DONE"):
                        self._playback_result.put(message)
                    elif message.startswith("AUDIO_OUT_ERROR"):
                        self._playback_result.put(message)
                    else:
                        self._responses.put(message)
                elif frame_type == AUDIO_UP:
                    self._queue_stream_item(("audio", payload))
                else:
                    raise ValueError("El ESP32 envió una trama de audio descendente.")
        except (OSError, ConnectionError, ValueError) as error:
            if not self._stop.is_set():
                print(f"[WIFI] Conexión de audio cerrada: {error}")
        finally:
            with self._send_lock:
                if client is self._client:
                    self._connected.clear()
                    self._client = None
                try:
                    client.close()
                except OSError:
                    pass

    def _queue_stream_item(self, item):
        try:
            self._stream.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            self._stream.get_nowait()
        except queue.Empty:
            pass
        try:
            self._stream.put_nowait(item)
        except queue.Full:
            # A concurrent consumer/refill won the race; keep the TCP reader
            # nonblocking so it can continue servicing the persistent socket.
            pass

    def _send_frame(self, frame_type: int, payload: bytes):
        with self._send_lock:
            client = self._client
            if not self._connected.is_set() or client is None:
                raise ConnectionError("No hay un ESP32 conectado por Wi-Fi.")
            try:
                client.sendall(encode_frame(frame_type, payload))
            except OSError as error:
                if client is self._client:
                    self._client = None
                    self._connected.clear()
                try:
                    client.close()
                except OSError:
                    pass
                raise ConnectionError(
                    f"Falló el envío TCP al ESP32: {error}"
                ) from error

    def send(self, command: str):
        self._send_frame(CONTROL, command.encode("utf-8"))

    def read_line(self) -> str:
        try:
            return self._responses.get(timeout=0.25)
        except queue.Empty:
            return ""

    def next_stream_item(self, timeout: float):
        try:
            return self._stream.get(timeout=timeout)
        except queue.Empty:
            return None

    def send_pcm(self, pcm: bytes, sample_rate: int, channels: int, bits: int):
        if sample_rate != 16000 or channels != 1 or bits != 16:
            raise ValueError("El audio Wi-Fi requiere PCM mono de 16 kHz y 16 bits.")
        if not pcm or len(pcm) % 2:
            raise ValueError("El audio Wi-Fi requiere muestras PCM de 16 bits completas.")
        while not self._playback_result.empty():
            self._playback_result.get_nowait()
        self.send(f"AUDIO_OUT_START:{sample_rate}:{channels}:{bits}")
        for offset in range(0, len(pcm), 1024):
            self._send_frame(AUDIO_DOWN, pcm[offset:offset + 1024])
        self.send("AUDIO_OUT_END")
        deadline = time.monotonic() + max(8.0, len(pcm) / 32000 + 8.0)
        while time.monotonic() < deadline:
            if not self.connected:
                raise ConnectionError("El ESP32 se desconectó durante la reproducción.")
            try:
                result = self._playback_result.get(timeout=0.25)
            except queue.Empty:
                continue
            if result == "AUDIO_OUT_DONE":
                return
            raise RuntimeError(result)
        raise TimeoutError("El ESP32 no confirmó la reproducción Wi-Fi.")

    def disconnect(self):
        self._close_client()

    def _close_client(self):
        with self._send_lock:
            client = self._client
            self._client = None
            self._connected.clear()
            if client:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    client.close()
                except OSError:
                    pass

    def stop(self):
        self._stop.set()
        self._close_client()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
