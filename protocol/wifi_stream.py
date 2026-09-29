"""Length-prefixed frames for the persistent ESP32 audio TCP connection."""

from __future__ import annotations

import socket
import struct

MAGIC = b"POP1"
HEADER = struct.Struct("!4sBI")
CONTROL = 1
AUDIO_UP = 2
AUDIO_DOWN = 3
MAX_CONTROL_BYTES = 2048
MAX_AUDIO_BYTES = 4096


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    if frame_type not in (CONTROL, AUDIO_UP, AUDIO_DOWN):
        raise ValueError(f"Tipo de trama Wi-Fi desconocido: {frame_type}")
    return HEADER.pack(MAGIC, frame_type, len(payload)) + payload


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("El ESP32 cerró la conexión Wi-Fi.")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_frame(connection: socket.socket) -> tuple[int, bytes]:
    header = _recv_exact(connection, HEADER.size)
    magic, frame_type, size = HEADER.unpack(header)
    if magic != MAGIC:
        raise ValueError("Cabecera inválida en el flujo Wi-Fi de Popote.")
    maximum = MAX_CONTROL_BYTES if frame_type == CONTROL else MAX_AUDIO_BYTES
    if frame_type not in (CONTROL, AUDIO_UP, AUDIO_DOWN) or size > maximum:
        raise ValueError("Trama Wi-Fi inválida o demasiado grande.")
    return frame_type, _recv_exact(connection, size)
