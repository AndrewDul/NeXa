"""M2.6B.4K / R0050 — deterministic tests for
``docs/research/m2_6_cloud_realtime_voice/wav_alignment.py``.

Offline, pure Python, no audio hardware, no Gemini. Proves the
byte/sample-exact alignment helper and the plain PCM-energy (RMS) helper
used to forensically analyze the real R0048/R0049 hardware captures in
the R0050 report — never the report's own real-WAV numbers (those come
from real operator recordings, not committed; this file tests the TOOL
only, on synthetic PCM).
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice"
if str(PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(PROBE_DIR))

import wav_alignment as wa  # noqa: E402


def _pcm(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


class TestFindAlignment(unittest.TestCase):
    def test_exact_prefix_and_suffix_computed(self) -> None:
        raw = _pcm(1, 2, 3, 4, 5, 6, 7, 8)
        forwarded = _pcm(3, 4, 5, 6)  # raw[2:6] by sample -> bytes [4:12]
        a = wa.find_alignment(raw, forwarded)
        self.assertTrue(a.found)
        self.assertEqual(a.prefix_bytes, 4)  # 2 samples * 2 bytes
        self.assertEqual(a.suffix_bytes, 4)  # 2 samples * 2 bytes
        self.assertEqual(a.prefix_ms(sample_rate=1000), 2.0)  # 2 samples @ 1kHz = 2ms
        self.assertEqual(a.suffix_ms(sample_rate=1000), 2.0)

    def test_forwarded_equal_to_raw_has_zero_prefix_and_suffix(self) -> None:
        raw = _pcm(1, 2, 3)
        a = wa.find_alignment(raw, raw)
        self.assertTrue(a.found)
        self.assertEqual(a.prefix_bytes, 0)
        self.assertEqual(a.suffix_bytes, 0)

    def test_empty_forwarded_is_trivially_aligned(self) -> None:
        raw = _pcm(1, 2, 3)
        a = wa.find_alignment(raw, b"")
        self.assertTrue(a.found)
        self.assertEqual(a.prefix_bytes, 0)
        self.assertEqual(a.suffix_bytes, len(raw))

    def test_non_subsequence_is_not_found(self) -> None:
        """Never fuzzy-matched: a forwarded buffer that is NOT a
        byte-exact contiguous subsequence of raw must be reported as
        such, never approximated."""
        raw = _pcm(1, 2, 3, 4)
        forwarded = _pcm(9, 9, 9)
        a = wa.find_alignment(raw, forwarded)
        self.assertFalse(a.found)
        self.assertIsNone(a.prefix_bytes)
        self.assertIsNone(a.suffix_bytes)
        self.assertIsNone(a.prefix_ms(sample_rate=1000))


class TestRmsWindows(unittest.TestCase):
    def test_silence_window_has_zero_rms(self) -> None:
        pcm = _pcm(0, 0, 0, 0, 0)  # 5 samples
        windows = wa.rms_windows(pcm, sample_rate=1000, window_ms=5)  # 1 window of 5 samples
        self.assertEqual(windows, [0.0])

    def test_constant_amplitude_rms_equals_that_amplitude(self) -> None:
        pcm = _pcm(100, -100, 100, -100)
        windows = wa.rms_windows(pcm, sample_rate=1000, window_ms=4)
        self.assertAlmostEqual(windows[0], 100.0, places=6)

    def test_windows_split_in_correct_fixed_size_chunks(self) -> None:
        # 8 samples @ 1kHz, 2ms windows -> 2-sample windows -> 4 windows
        pcm = _pcm(1000, 1000, 0, 0, 500, 500, 0, 0)
        windows = wa.rms_windows(pcm, sample_rate=1000, window_ms=2)
        self.assertEqual(len(windows), 4)
        self.assertAlmostEqual(windows[0], 1000.0, places=6)
        self.assertAlmostEqual(windows[1], 0.0, places=6)
        self.assertAlmostEqual(windows[2], 500.0, places=6)
        self.assertAlmostEqual(windows[3], 0.0, places=6)

    def test_trailing_short_window_still_computed(self) -> None:
        pcm = _pcm(1000, 1000, 1000)  # 3 samples, window of 2 -> [2 samples][1 sample]
        windows = wa.rms_windows(pcm, sample_rate=1000, window_ms=2)
        self.assertEqual(len(windows), 2)
        self.assertAlmostEqual(windows[0], 1000.0, places=6)
        self.assertAlmostEqual(windows[1], 1000.0, places=6)

    def test_rising_energy_profile_is_reported_faithfully(self) -> None:
        """The exact shape this module is used for in R0050: a synthetic
        quiet-then-rising signal must show a monotonically-consistent
        RMS trend, with no threshold/detector logic invented here."""
        quiet = (5,) * 10
        rising = (50,) * 10 + (500,) * 10 + (2000,) * 10
        pcm = _pcm(*quiet, *rising)
        windows = wa.rms_windows(pcm, sample_rate=1000, window_ms=10)
        self.assertEqual(len(windows), 4)
        self.assertLess(windows[0], windows[1])
        self.assertLess(windows[1], windows[2])
        self.assertLess(windows[2], windows[3])

    def test_only_16_bit_pcm_supported(self) -> None:
        with self.assertRaises(ValueError):
            wa.rms_windows(b"\x00" * 4, sample_rate=1000, sample_width_bytes=1)


if __name__ == "__main__":
    unittest.main()
