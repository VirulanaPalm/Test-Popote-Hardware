"""High-level device queries and typed hardware diagnostics."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from protocol.protocol import (
    AUDIO_TEST,
    CAPABILITIES,
    FACE,
    FACE_CONTROL_CAPABILITY,
    FACE_ERROR_UNKNOWN_STATE,
    IDENTIFY,
    MIC_READ,
    OLED_TEST,
    PING,
    PROTOCOL_VERSION,
    STATUS,
    VERSION,
)


class DiagnosticStatus(str, Enum):
    OK = "OK"
    FAIL = "FAIL"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


@dataclass(frozen=True)
class DiagnosticResult:
    name: str
    status: DiagnosticStatus
    detail: str = ""
    response: Optional[str] = None

    def __bool__(self) -> bool:
        """Keep existing truth checks equivalent to a successful diagnostic."""
        return self.status is DiagnosticStatus.OK


class HardwareController:
    """Control device commands and report diagnostic results consistently."""

    def __init__(self, connection):
        self.connection = connection
        self.capabilities = None

    def command(self, command: str, expected=None, timeout: float = 5):
        """Send a command and return its matching response, or None on timeout."""
        self.connection.send(command)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            response = self.connection.read_line()
            if not response:
                continue
            print(f"[ESP32] <- {response}")
            if expected is None:
                return response
            if callable(expected) and expected(response):
                return response
            if not callable(expected) and response == expected:
                return response
        return None

    def set_face(self, state: str, timeout: float = 1.0) -> bool:
        """Send an additive face-state command when firmware advertises support."""
        normalized = state.strip().upper()
        if not normalized or (
            self.capabilities is not None
            and FACE_CONTROL_CAPABILITY not in self.capabilities
        ):
            return False
        response = self.command(
            f"{FACE} {normalized}",
            expected=lambda line: (
                line == f"FACE_OK:{normalized}"
                or line == FACE_ERROR_UNKNOWN_STATE
            ),
            timeout=timeout,
        )
        return response == f"FACE_OK:{normalized}"

    def _diagnose(
        self,
        name: str,
        command: str,
        expected: Callable[[str], bool],
        failure: Callable[[str], bool] = lambda _response: False,
        timeout: float = 5,
    ) -> DiagnosticResult:
        try:
            self.connection.send(command)
            deadline = time.monotonic() + timeout

            while time.monotonic() < deadline:
                response = self.connection.read_line()
                if not response:
                    continue
                print(f"[ESP32] <- {response}")
                if expected(response):
                    return DiagnosticResult(
                        name, DiagnosticStatus.OK, response=response
                    )
                if failure(response):
                    return DiagnosticResult(
                        name, DiagnosticStatus.FAIL,
                        detail=response, response=response
                    )

            return DiagnosticResult(name, DiagnosticStatus.TIMEOUT)
        except Exception as error:
            return DiagnosticResult(
                name, DiagnosticStatus.ERROR, detail=str(error)
            )

    def query_device_info(self):
        """Read additive metadata commands; None indicates legacy firmware."""
        queries = (
            ("device", IDENTIFY, "DEVICE:"),
            ("firmware", VERSION, "FIRMWARE_VERSION:"),
            ("protocol", PROTOCOL_VERSION, "PROTOCOL_VERSION:"),
            ("capabilities", CAPABILITIES, "CAPABILITIES:"),
        )
        info = {}
        for key, command, prefix in queries:
            try:
                info[key] = self.command(
                    command,
                    expected=lambda line, expected_prefix=prefix: line.startswith(
                        expected_prefix
                    ),
                    timeout=0.6,
                )
            except Exception:
                info[key] = None
        capabilities_reply = info.get("capabilities")
        if capabilities_reply:
            names = capabilities_reply.partition(":")[2]
            self.capabilities = {
                item.strip().upper() for item in names.split(",") if item.strip()
            }
        return info

    def _availability(self, name: str, capability: str):
        if self.capabilities is not None and capability not in self.capabilities:
            return DiagnosticResult(
                name,
                DiagnosticStatus.NOT_AVAILABLE,
                detail=f"La capacidad {capability} no está anunciada",
            )
        return None

    def test_connection(self) -> DiagnosticResult:
        print("\n[TEST] Comunicación ESP32")
        result = self._diagnose(
            "PING/PONG", PING, lambda value: value == "PONG", timeout=5
        )
        if result:
            print("[OK] Handshake PING/PONG correcto.")
        return result

    def test_status(self) -> DiagnosticResult:
        print("\n[TEST] Estado del hardware")
        result = self._diagnose(
            "Estado del hardware",
            STATUS,
            lambda value: value == "POPOTE_HARDWARE_OK",
            lambda value: value == "POPOTE_HARDWARE_FAIL",
        )
        if result:
            print("[OK] Hardware reportado como OK.")
        return result

    def test_oled(self) -> DiagnosticResult:
        print("\n[TEST] OLED")
        unavailable = self._availability("OLED", "OLED")
        if unavailable is not None:
            return unavailable
        result = self._diagnose(
            "OLED",
            OLED_TEST,
            lambda value: value == "OLED_OK",
            lambda value: value in ("OLED_FAIL", "OLED_NOT_READY"),
        )
        if result:
            print("[OK] OLED funcionando.")
        return result

    def test_microphone(self) -> DiagnosticResult:
        print("\n[TEST] MAX9814")
        unavailable = self._availability("MAX9814", "MIC")
        if unavailable is not None:
            return unavailable

        def valid_reading(value: str) -> bool:
            if not value.startswith("MIC:"):
                return False
            try:
                return 0 <= int(value[4:]) <= 4095
            except ValueError:
                return False

        result = self._diagnose(
            "MAX9814",
            MIC_READ,
            valid_reading,
            lambda value: value.startswith("MIC:"),
        )
        if result:
            print(f"[OK] MAX9814 funcionando: {result.response}")
        return result

    def test_audio(self) -> DiagnosticResult:
        print("\n[TEST] MAX98357A + Parlante")
        unavailable = self._availability("MAX98357A + Parlante", "AUDIO")
        if unavailable is not None:
            return unavailable
        result = self._diagnose(
            "MAX98357A + Parlante",
            AUDIO_TEST,
            lambda value: value == "AUDIO_OK",
            lambda value: value in ("AUDIO_FAIL", "AUDIO_NOT_READY"),
        )
        if result:
            print("[OK] MAX98357A + parlante funcionando.")
        return result
