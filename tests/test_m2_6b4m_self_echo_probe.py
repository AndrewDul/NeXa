"""M2.6B.4M / R0052 — deterministic tests for
``docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py``.

Offline, pure Python, no audio hardware, no Pipecat, no Gemini. Tests
ONLY the probe's pure, offline-testable logic: interval collapsing,
windowed RMS/peak/confidence/volume aggregation, and trial
summarization. Never the real-hardware numbers themselves (those come
from real operator runs, git-ignored under ``self_echo_captures/``).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice"
if str(PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(PROBE_DIR))

import m2_6b4m_self_echo_probe as probe  # noqa: E402


class TestPcmHelpers(unittest.TestCase):
    def test_pcm_ms_computes_duration(self) -> None:
        # 16000 samples/sec, 2 bytes/sample -> 1 second = 32000 bytes
        self.assertAlmostEqual(probe._pcm_ms(32000, 16000), 1000.0)

    def test_pcm_ms_zero_bytes_is_zero(self) -> None:
        self.assertEqual(probe._pcm_ms(0, 16000), 0.0)

    def test_rms_of_silence_is_zero(self) -> None:
        import struct

        silence = struct.pack("<4h", 0, 0, 0, 0)
        self.assertEqual(probe._rms(silence), 0.0)

    def test_rms_of_constant_amplitude(self) -> None:
        import struct

        chunk = struct.pack("<4h", 100, -100, 100, -100)
        self.assertAlmostEqual(probe._rms(chunk), 100.0)

    def test_peak_finds_largest_absolute_sample(self) -> None:
        import struct

        chunk = struct.pack("<4h", 10, -500, 300, -20)
        self.assertEqual(probe._peak(chunk), 500)

    def test_peak_of_empty_is_zero(self) -> None:
        self.assertEqual(probe._peak(b""), 0)


class TestSpeakingIntervals(unittest.TestCase):
    def test_single_closed_span(self) -> None:
        events = [{"t": 1.0, "kind": "start"}, {"t": 1.5, "kind": "stop"}]
        spans = probe.speaking_intervals(events)
        self.assertEqual(spans, [(1.0, 1.5)])

    def test_multiple_closed_spans(self) -> None:
        events = [
            {"t": 1.0, "kind": "start"},
            {"t": 1.2, "kind": "stop"},
            {"t": 2.0, "kind": "start"},
            {"t": 2.3, "kind": "stop"},
        ]
        spans = probe.speaking_intervals(events)
        self.assertEqual(spans, [(1.0, 1.2), (2.0, 2.3)])

    def test_open_span_with_no_stop(self) -> None:
        events = [{"t": 1.0, "kind": "start"}]
        spans = probe.speaking_intervals(events)
        self.assertEqual(spans, [(1.0, None)])

    def test_extra_stop_with_no_open_start_is_ignored(self) -> None:
        events = [{"t": 1.0, "kind": "stop"}]
        spans = probe.speaking_intervals(events)
        self.assertEqual(spans, [])

    def test_duplicate_start_does_not_reopen(self) -> None:
        events = [
            {"t": 1.0, "kind": "start"},
            {"t": 1.1, "kind": "start"},
            {"t": 1.5, "kind": "stop"},
        ]
        spans = probe.speaking_intervals(events)
        self.assertEqual(spans, [(1.0, 1.5)])

    def test_empty_events_yields_no_spans(self) -> None:
        self.assertEqual(probe.speaking_intervals([]), [])


class TestRmsWindowsFromEvents(unittest.TestCase):
    def test_empty_window_reports_zero_frames(self) -> None:
        result = probe.rms_windows_from_events([], lo=0.0, hi=1.0)
        self.assertEqual(result, {"n_frames": 0})

    def test_aggregates_only_frames_inside_window(self) -> None:
        frames = [
            {"t": 0.5, "conf": 0.9, "vol": 0.8, "speaking": True},
            {"t": 1.5, "conf": 0.1, "vol": 0.05, "speaking": False},  # outside [2,3)
            {"t": 2.2, "conf": 0.7, "vol": 0.6, "speaking": True},
            {"t": 2.8, "conf": 0.3, "vol": 0.2, "speaking": False},
        ]
        result = probe.rms_windows_from_events(frames, lo=2.0, hi=3.0)
        self.assertEqual(result["n_frames"], 2)
        self.assertAlmostEqual(result["conf_mean"], 0.5)
        self.assertAlmostEqual(result["conf_max"], 0.7)
        self.assertAlmostEqual(result["vol_mean"], 0.4)
        self.assertAlmostEqual(result["vol_max"], 0.6)
        self.assertAlmostEqual(result["speaking_frame_frac"], 0.5)

    def test_window_is_half_open_hi_exclusive(self) -> None:
        frames = [{"t": 3.0, "conf": 1.0, "vol": 1.0, "speaking": True}]
        result = probe.rms_windows_from_events(frames, lo=2.0, hi=3.0)
        self.assertEqual(result, {"n_frames": 0})


class TestRawRmsWindowsFromEvents(unittest.TestCase):
    def test_empty_window_reports_zero_frames(self) -> None:
        result = probe.raw_rms_windows_from_events([], lo=0.0, hi=1.0)
        self.assertEqual(result, {"n_frames": 0})

    def test_aggregates_rms_and_peak(self) -> None:
        frames = [
            {"t": 1.0, "rms": 100.0, "peak": 500},
            {"t": 1.1, "rms": 300.0, "peak": 900},
        ]
        result = probe.raw_rms_windows_from_events(frames, lo=0.0, hi=2.0)
        self.assertEqual(result["n_frames"], 2)
        self.assertAlmostEqual(result["rms_mean"], 200.0)
        self.assertAlmostEqual(result["rms_max"], 300.0)
        self.assertEqual(result["peak_max"], 900)


class TestSummarizeTrial(unittest.TestCase):
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            level="normal",
            playback_start=1.0,
            playback_end=2.0,
            trial_end=3.0,
            vad_events=[],
            mic_frames=[],
            bargein_events=[],
        )
        kwargs.update(overrides)
        return kwargs

    def test_no_vad_no_bargein_is_a_clean_pass(self) -> None:
        result = probe.summarize_trial(**self._base_kwargs())
        self.assertEqual(result["vad_start_count"], 0)
        self.assertEqual(result["bargein_confirmed_count"], 0)
        self.assertFalse(result["false_confirmed_barge_in"])
        self.assertEqual(result["playback_duration_ms"], 1000.0)

    def test_confirmed_barge_in_during_playback_is_flagged(self) -> None:
        result = probe.summarize_trial(
            **self._base_kwargs(
                vad_events=[{"t": 1.3, "kind": "start"}, {"t": 1.6, "kind": "stop"}],
                bargein_events=[
                    {"t": 1.3, "kind": "candidate"},
                    {"t": 1.6, "kind": "confirmed"},
                ],
            )
        )
        self.assertTrue(result["false_confirmed_barge_in"])
        self.assertEqual(result["bargein_confirmed_count"], 1)
        self.assertEqual(result["bargein_candidate_count"], 1)
        self.assertEqual(result["bargein_confirmed_t"], [1.6])
        self.assertEqual(len(result["vad_spans"]), 1)
        self.assertEqual(result["vad_spans"][0]["vad_duration_ms"], 300.0)
        self.assertTrue(result["vad_spans"][0]["closed"])

    def test_rejected_candidate_is_not_a_false_confirm(self) -> None:
        result = probe.summarize_trial(
            **self._base_kwargs(
                vad_events=[{"t": 1.3, "kind": "start"}, {"t": 1.4, "kind": "stop"}],
                bargein_events=[
                    {"t": 1.3, "kind": "candidate"},
                    {"t": 1.4, "kind": "rejected"},
                ],
            )
        )
        self.assertFalse(result["false_confirmed_barge_in"])
        self.assertEqual(result["bargein_rejected_count"], 1)
        self.assertEqual(result["bargein_confirmed_count"], 0)

    def test_control_trial_with_no_playback_bounds_is_handled(self) -> None:
        # e.g. _ResponseLifecycle never observed BotStartedSpeaking (device
        # issue) -- must not raise, must report n_frames=0 windows.
        result = probe.summarize_trial(
            **self._base_kwargs(playback_start=None, playback_end=None)
        )
        self.assertIsNone(result["playback_start_t"])
        self.assertIsNone(result["playback_duration_ms"])
        self.assertEqual(result["mic_phase_playback"], {"n_frames": 0})
        self.assertEqual(result["mic_raw_rms_phase_playback"], {"n_frames": 0})
        self.assertEqual(result["ref_raw_rms_phase_playback"], {"n_frames": 0})

    def test_raw_rms_windows_populate_when_provided(self) -> None:
        result = probe.summarize_trial(
            **self._base_kwargs(
                mic_rms_frames=[{"t": 1.5, "rms": 50.0, "peak": 200}],
                ref_rms_frames=[{"t": 1.5, "rms": 8000.0, "peak": 30000}],
            )
        )
        self.assertEqual(result["mic_raw_rms_phase_playback"]["n_frames"], 1)
        self.assertEqual(result["ref_raw_rms_phase_playback"]["n_frames"], 1)
        self.assertAlmostEqual(
            result["ref_raw_rms_phase_playback"]["rms_mean"], 8000.0
        )

    def test_missing_raw_rms_frames_defaults_to_empty(self) -> None:
        # summarize_trial must not require mic_rms_frames/ref_rms_frames
        # (older call sites / a --control trial with a device that never
        # reported raw RMS) -- must not raise.
        result = probe.summarize_trial(**self._base_kwargs())
        self.assertEqual(result["mic_raw_rms_phase_playback"]["n_frames"], 0)


if __name__ == "__main__":
    unittest.main()
