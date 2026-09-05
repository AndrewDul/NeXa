"""Deterministic tests for PyAudio device-name resolution (M2.1).

Mocked at the PyAudio boundary — a fake device list, no real audio hardware.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice.device import AudioDeviceNotFoundError, find_device_index  # noqa: E402


class _FakePyAudio:
    def __init__(self, devices: list[dict]) -> None:
        self._devices = devices

    def get_device_count(self) -> int:
        return len(self._devices)

    def get_device_info_by_index(self, i: int) -> dict:
        return self._devices[i]


DEVICES = [
    {
        "name": "reSpeaker XVF3800 4-Mic Array: USB Audio (hw:2,0)",
        "maxInputChannels": 2,
        "maxOutputChannels": 2,
    },
    {"name": "respeaker", "maxInputChannels": 128, "maxOutputChannels": 128},
    {"name": "default", "maxInputChannels": 128, "maxOutputChannels": 0},
]


class TestFindDeviceIndex(unittest.TestCase):
    def test_exact_match_preferred_over_substring_match(self) -> None:
        pa = _FakePyAudio(DEVICES)
        # Both index 0 and 1 contain "respeaker" (case-insensitive); the
        # exact-name "respeaker" (index 1, the flexible ALSA plug alias)
        # must win over the raw hw device at index 0.
        idx = find_device_index(pa, "respeaker", require_input=True)
        self.assertEqual(idx, 1)

    def test_require_output_excludes_zero_output_channel_devices(self) -> None:
        pa = _FakePyAudio(DEVICES)
        # "default" has maxOutputChannels=0, so no device qualifies.
        with self.assertRaises(AudioDeviceNotFoundError):
            find_device_index(pa, "default", require_output=True)

    def test_substring_fallback_when_no_exact_match(self) -> None:
        pa = _FakePyAudio(DEVICES)
        idx = find_device_index(pa, "XVF3800", require_input=True)
        self.assertEqual(idx, 0)

    def test_no_match_raises_explicitly(self) -> None:
        pa = _FakePyAudio(DEVICES)
        with self.assertRaises(AudioDeviceNotFoundError):
            find_device_index(pa, "nonexistent-device", require_input=True)

    def test_require_input_excludes_zero_input_channel_devices(self) -> None:
        devices = [{"name": "output-only", "maxInputChannels": 0, "maxOutputChannels": 2}]
        pa = _FakePyAudio(devices)
        with self.assertRaises(AudioDeviceNotFoundError):
            find_device_index(pa, "output-only", require_input=True)


if __name__ == "__main__":
    unittest.main()
