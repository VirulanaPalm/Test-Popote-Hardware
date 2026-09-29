import unittest

from protocol.protocol import (
    AUDIO_LOOP,
    AUDIO_STOP,
    AUDIO_TEST,
    CAPABILITIES,
    EXPECTED_FIRMWARE_VERSION,
    HELP,
    IDENTIFY,
    LEGACY_COMMANDS,
    MIC_READ,
    OLED_TEST,
    PING,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_NUMBER,
    STATUS,
    VERSION,
)


class ProtocolConstantsTests(unittest.TestCase):
    def test_existing_command_names_are_preserved(self):
        self.assertEqual(
            LEGACY_COMMANDS,
            (PING, STATUS, OLED_TEST, MIC_READ, AUDIO_TEST, AUDIO_LOOP,
             AUDIO_STOP, HELP),
        )

    def test_protocol_metadata_is_explicit(self):
        self.assertEqual(PROTOCOL_VERSION_NUMBER, 1)
        self.assertEqual(EXPECTED_FIRMWARE_VERSION, "0.1.6")
        self.assertEqual(
            (IDENTIFY, VERSION, PROTOCOL_VERSION, CAPABILITIES),
            ("IDENTIFY", "VERSION", "PROTOCOL_VERSION", "CAPABILITIES"),
        )


if __name__ == "__main__":
    unittest.main()
