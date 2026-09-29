import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hardware.connection import ESP32Connection


class PortDetectionTests(unittest.TestCase):
    def test_prefers_known_cp210x_bridge(self):
        generic = SimpleNamespace(
            device="COM3", description="USB Serial Port", manufacturer="Unknown",
            vid=0x1234, pid=0x5678,
        )
        cp210x = SimpleNamespace(
            device="COM5", description="CP210x USB to UART Bridge",
            manufacturer="Silicon Labs", vid=0x10C4, pid=0xEA60,
        )
        with patch(
            "hardware.connection.serial.tools.list_ports.comports",
            return_value=[generic, cp210x],
        ):
            self.assertEqual(ESP32Connection().detect_port(), "COM5")

    def test_returns_none_without_supported_descriptor(self):
        generic = SimpleNamespace(
            device="COM3", description="USB Serial Port", manufacturer="Unknown",
            vid=0x1234, pid=0x5678,
        )
        with patch(
            "hardware.connection.serial.tools.list_ports.comports",
            return_value=[generic],
        ):
            self.assertIsNone(ESP32Connection().detect_port())

    def test_connection_error_lists_visible_ports(self):
        bluetooth = SimpleNamespace(
            device="COM3", description="Bluetooth Serial Port",
            manufacturer="Microsoft", vid=None, pid=None,
        )
        connection = ESP32Connection()
        with patch(
            "hardware.connection.serial.tools.list_ports.comports",
            return_value=[bluetooth],
        ):
            with self.assertRaisesRegex(ConnectionError, "COM3.*Bluetooth"):
                connection.connect()


if __name__ == "__main__":
    unittest.main()
