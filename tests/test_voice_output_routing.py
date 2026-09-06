"""M2.4 input/output device routing — deterministic proof that the mic and
the speaker are selected **independently**, that selecting the UACDemoV1.0
USB DAC for output never disturbs the reSpeaker input selection, and that a
missing output device fails loudly instead of silently falling back to some
other speaker.

Mocked at the PyAudio boundary. The fake device list mirrors the real
post-reboot enumeration on this Pi (2026-09-06):

    idx 0  reSpeaker XVF3800 4-Mic Array: USB Audio (hw:2,0)   in=2  out=2
    idx 1  UACDemoV1.0: USB Audio (hw:3,0)                      in=0  out=2
    idx 2  usb_speaker   (ALSA plug -> hw:CARD=UACDemoV10)      in=0  out=128
    idx 3  respeaker      (ALSA plug -> hw:CARD=Array)          in=128 out=128
    idx 4  default        (asym: playback usb_speaker / capture respeaker)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice.config import LocalAudioConfig  # noqa: E402
from nexa.voice.device import AudioDeviceNotFoundError, find_device_index  # noqa: E402


class _FakePyAudio:
    def __init__(self, devices: list[dict]) -> None:
        self._devices = devices

    def get_device_count(self) -> int:
        return len(self._devices)

    def get_device_info_by_index(self, i: int) -> dict:
        return self._devices[i]


# Real post-reboot enumeration shape (see module docstring).
POST_REBOOT_DEVICES = [
    {
        "name": "reSpeaker XVF3800 4-Mic Array: USB Audio (hw:2,0)",
        "maxInputChannels": 2,
        "maxOutputChannels": 2,
    },
    {"name": "UACDemoV1.0: USB Audio (hw:3,0)", "maxInputChannels": 0, "maxOutputChannels": 2},
    {"name": "usb_speaker", "maxInputChannels": 0, "maxOutputChannels": 128},
    {"name": "respeaker", "maxInputChannels": 128, "maxOutputChannels": 128},
    {"name": "default", "maxInputChannels": 128, "maxOutputChannels": 128},
]


class TestIndependentInputOutputSelection(unittest.TestCase):
    def test_defaults_name_two_different_devices(self) -> None:
        config = LocalAudioConfig()
        self.assertEqual(config.input_device_name, "respeaker")
        self.assertEqual(config.output_device_name, "usb_speaker")
        self.assertNotEqual(config.input_device_name, config.output_device_name)

    def test_mic_and_speaker_resolve_to_different_indices(self) -> None:
        pa = _FakePyAudio(POST_REBOOT_DEVICES)
        config = LocalAudioConfig()
        input_index = find_device_index(pa, config.input_device_name, require_input=True)
        output_index = find_device_index(pa, config.output_device_name, require_output=True)
        self.assertEqual(input_index, 3)  # "respeaker" plug alias
        self.assertEqual(output_index, 2)  # "usb_speaker" plug alias -> UACDemoV10
        self.assertNotEqual(input_index, output_index)

    def test_selecting_usb_speaker_output_does_not_change_reSpeaker_input(self) -> None:
        """Resolving the output device must have no effect on what the input
        name resolves to — they share no state, no positional index."""
        pa = _FakePyAudio(POST_REBOOT_DEVICES)

        input_before = find_device_index(pa, "respeaker", require_input=True)
        _ = find_device_index(pa, "usb_speaker", require_output=True)
        input_after = find_device_index(pa, "respeaker", require_input=True)

        self.assertEqual(input_before, 3)
        self.assertEqual(input_after, input_before)

    def test_output_name_never_matches_the_input_only_reSpeaker_hw_device(self) -> None:
        """`require_output` still holds for the raw reSpeaker hw device
        (it reports out=2), but the configured output name ("usb_speaker")
        must not accidentally select it."""
        pa = _FakePyAudio(POST_REBOOT_DEVICES)
        output_index = find_device_index(pa, "usb_speaker", require_output=True)
        self.assertEqual(POST_REBOOT_DEVICES[output_index]["name"], "usb_speaker")


class TestMissingOutputFailsLoudly(unittest.TestCase):
    def test_missing_output_device_raises_explicitly(self) -> None:
        # UACDemoV1.0 / usb_speaker absent (e.g. USB DAC unplugged), but the
        # reSpeaker (which can also do output) is still present.
        devices = [
            {
                "name": "reSpeaker XVF3800 4-Mic Array: USB Audio (hw:2,0)",
                "maxInputChannels": 2,
                "maxOutputChannels": 2,
            },
            {"name": "respeaker", "maxInputChannels": 128, "maxOutputChannels": 128},
            {"name": "default", "maxInputChannels": 128, "maxOutputChannels": 128},
        ]
        pa = _FakePyAudio(devices)
        with self.assertRaises(AudioDeviceNotFoundError):
            find_device_index(pa, "usb_speaker", require_output=True)

    def test_no_silent_fallback_to_another_speaker(self) -> None:
        """Even though `respeaker`/`default` are output-capable, a missing
        `usb_speaker` must raise — never return one of them instead."""
        devices = [
            {"name": "respeaker", "maxInputChannels": 128, "maxOutputChannels": 128},
            {"name": "default", "maxInputChannels": 128, "maxOutputChannels": 128},
            {"name": "hdmi-something", "maxInputChannels": 0, "maxOutputChannels": 8},
        ]
        pa = _FakePyAudio(devices)
        with self.assertRaises(AudioDeviceNotFoundError):
            find_device_index(pa, "usb_speaker", require_output=True)

        # And the input side is unaffected — still resolvable.
        self.assertEqual(find_device_index(pa, "respeaker", require_input=True), 0)

    def test_missing_input_device_also_raises_explicitly(self) -> None:
        pa = _FakePyAudio(POST_REBOOT_DEVICES)
        with self.assertRaises(AudioDeviceNotFoundError):
            find_device_index(pa, "nonexistent-mic", require_input=True)


if __name__ == "__main__":
    unittest.main()
