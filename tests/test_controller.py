import unittest
from unittest.mock import patch

from hardware.controller import (
    DiagnosticStatus,
    HardwareController,
)
from main import DIAGNOSTIC_NAMES, _record_setup_failure


class FakeConnection:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.sent = []

    def send(self, command):
        self.sent.append(command)
        if self.error:
            raise self.error

    def read_line(self):
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else ""


class HardwareControllerTests(unittest.TestCase):
    def test_handshake_success(self):
        controller = HardwareController(FakeConnection(["PONG"]))
        self.assertEqual(controller.test_connection().status, DiagnosticStatus.OK)

    def test_status_failure_is_distinct_from_timeout(self):
        controller = HardwareController(
            FakeConnection(["POPOTE_HARDWARE_FAIL"])
        )
        self.assertEqual(controller.test_status().status, DiagnosticStatus.FAIL)

    def test_invalid_microphone_value_is_failure(self):
        controller = HardwareController(FakeConnection(["MIC:bad"]))
        self.assertEqual(controller.test_microphone().status, DiagnosticStatus.FAIL)

    def test_transport_exception_is_error(self):
        controller = HardwareController(
            FakeConnection(error=OSError("serial disconnected"))
        )
        self.assertEqual(controller.test_connection().status, DiagnosticStatus.ERROR)

    def test_unadvertised_hardware_is_not_available(self):
        connection = FakeConnection()
        controller = HardwareController(connection)
        controller.capabilities = {"MIC"}
        result = controller.test_oled()
        self.assertEqual(result.status, DiagnosticStatus.NOT_AVAILABLE)
        self.assertEqual(connection.sent, [])

    def test_connection_failure_is_not_reported_as_five_component_failures(self):
        results = []
        _record_setup_failure(results, ConnectionError("no serial device"))
        self.assertEqual(len(results), len(DIAGNOSTIC_NAMES))
        self.assertEqual(results[0].status, DiagnosticStatus.ERROR)
        self.assertTrue(
            all(result.status is DiagnosticStatus.NOT_AVAILABLE for result in results[1:])
        )

    def test_silent_device_is_timeout(self):
        controller = HardwareController(FakeConnection())
        ticks = iter((0.0, 0.0, 6.0))
        with patch("hardware.controller.time.monotonic", side_effect=lambda: next(ticks)):
            result = controller.test_connection()
        self.assertEqual(result.status, DiagnosticStatus.TIMEOUT)

    def test_metadata_commands_are_optional_for_legacy_firmware(self):
        connection = FakeConnection([
            "UNKNOWN_COMMAND:IDENTIFY",
            "UNKNOWN_COMMAND:VERSION",
            "UNKNOWN_COMMAND:PROTOCOL_VERSION",
            "UNKNOWN_COMMAND:CAPABILITIES",
        ])
        controller = HardwareController(connection)
        clock = [0.0]

        def advance_clock():
            clock[0] += 0.1
            return clock[0]

        with patch("hardware.controller.time.monotonic", side_effect=advance_clock):
            info = controller.query_device_info()
        self.assertEqual(info, {
            "device": None,
            "firmware": None,
            "protocol": None,
            "capabilities": None,
        })

    def test_metadata_capabilities_are_parsed(self):
        connection = FakeConnection([
            "DEVICE:POPOTE_HARDWARE",
            "FIRMWARE_VERSION:0.1.3",
            "PROTOCOL_VERSION:1",
            "CAPABILITIES:OLED,MIC,AUDIO,AUDIO_LOOP",
        ])
        controller = HardwareController(connection)
        info = controller.query_device_info()
        self.assertEqual(info["device"], "DEVICE:POPOTE_HARDWARE")
        self.assertEqual(
            controller.capabilities, {"OLED", "MIC", "AUDIO", "AUDIO_LOOP"}
        )


if __name__ == "__main__":
    unittest.main()
