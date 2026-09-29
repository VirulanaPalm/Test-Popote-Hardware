"""USB serial discovery and lifecycle for the Popote ESP32."""

from __future__ import annotations

import serial
import serial.tools.list_ports


class ESP32Connection:
    """Detect and manage the serial connection to an ESP32-class board."""

    CP210X = (0x10C4, 0xEA60)
    CH340 = (0x1A86, 0x7523)

    def __init__(self, baudrate: int = 115200, timeout: float = 0.25):
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial = None
        self.port = None
        self.available_ports = []

    @classmethod
    def _is_candidate(cls, port) -> bool:
        description = (port.description or "").lower()
        manufacturer = (port.manufacturer or "").lower()
        usb_id = (port.vid, port.pid)

        return (
            usb_id in (cls.CP210X, cls.CH340)
            or "cp210" in description
            or "cp210" in manufacturer
            or "ch340" in description
            or "ch340" in manufacturer
            or "esp32" in description
            or "espressif" in manufacturer
        )

    def detect_port(self):
        """Return the first serial port identified as a common ESP32 bridge."""
        self.available_ports = list(serial.tools.list_ports.comports())
        candidates = [port for port in self.available_ports if self._is_candidate(port)]
        if not candidates:
            return None

        # Prefer known USB bridge IDs over text-only descriptor matches.
        candidates.sort(
            key=lambda port: (port.vid, port.pid)
            not in (self.CP210X, self.CH340)
        )
        return candidates[0].device

    def connect(self):
        """Find and open a candidate port; PING performs the protocol handshake."""
        self.port = self.detect_port()
        if not self.port:
            visible = ", ".join(
                f"{port.device} ({port.description or 'sin descripción'})"
                for port in self.available_ports
            ) or "ninguno"
            raise ConnectionError(
                "No se encontró un puerto ESP32/CP210x/CH340. "
                f"Puertos COM visibles: {visible}."
            )

        try:
            self.serial = serial.Serial(
                self.port,
                self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
        except serial.SerialException as error:
            self.serial = None
            raise ConnectionError(
                f"No se pudo abrir {self.port}: {error}"
            ) from error

        print(f"[USB] Puerto abierto: {self.port}")

    def disconnect(self):
        """Close the serial port if it was opened."""
        serial_port = self.serial
        try:
            if serial_port and serial_port.is_open:
                serial_port.close()
                print("[USB] Conexión cerrada.")
        except (serial.SerialException, OSError) as error:
            print(f"[WARNING] No se pudo cerrar limpiamente el puerto: {error}")
        finally:
            self.serial = None

    def send(self, command: str):
        """Send one newline-terminated protocol command."""
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("El ESP32 no está conectado.")

        self.serial.reset_input_buffer()
        try:
            self.serial.write((command + "\n").encode("utf-8"))
            self.serial.flush()
        except (serial.SerialException, OSError) as error:
            raise ConnectionError(f"Falló el envío de {command}: {error}") from error

    def read_line(self) -> str:
        """Read one response line, returning an empty string on timeout."""
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("El ESP32 no está conectado.")

        try:
            return self.serial.readline().decode("utf-8", errors="replace").strip()
        except (serial.SerialException, OSError) as error:
            raise ConnectionError(f"Falló la lectura serial: {error}") from error
