"""M2.5A — deterministic tests for the barge-in research tooling.

Offline, no audio devices, no pipecat, no model. Covers the pure audio
maths in ``docs/research/m2_5_bargein/audio_mix.py`` and re-asserts the
spike scripts stay research-only (no production-state imports, no
``ConversationSession`` mutation).
"""
from __future__ import annotations

import ast
import sys
import unittest
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE_DIR = REPO_ROOT / "docs" / "research" / "m2_5_bargein"
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

import audio_mix as AM  # noqa: E402


class TestMixOverlay(unittest.TestCase):
    def test_overlay_added_at_offset(self) -> None:
        sr = 1000
        base = np.zeros(sr, dtype="<i2")  # 1 s of silence
        overlay = np.full(100, 1000, dtype="<i2")
        out = AM.mix_overlay(base, overlay, sr, start_s=0.5)
        self.assertEqual(out.size, sr)
        self.assertTrue(np.all(out[:500] == 0))
        self.assertTrue(np.all(out[500:600] == 1000))
        self.assertTrue(np.all(out[600:] == 0))

    def test_result_is_extended_when_overlay_runs_past_base(self) -> None:
        sr = 1000
        base = np.zeros(200, dtype="<i2")
        overlay = np.full(100, 5, dtype="<i2")
        out = AM.mix_overlay(base, overlay, sr, start_s=0.15)  # 150..250
        self.assertEqual(out.size, 250)
        self.assertTrue(np.all(out[150:250] == 5))

    def test_sum_is_clipped_not_wrapped(self) -> None:
        sr = 1000
        base = np.full(100, 30000, dtype="<i2")
        overlay = np.full(100, 30000, dtype="<i2")
        out = AM.mix_overlay(base, overlay, sr, start_s=0.0)
        self.assertTrue(np.all(out == 32767))  # clipped, no int16 wrap

    def test_gain_scales_overlay_only(self) -> None:
        sr = 1000
        base = np.full(100, 100, dtype="<i2")
        overlay = np.full(100, 100, dtype="<i2")
        out = AM.mix_overlay(base, overlay, sr, start_s=0.0, gain=3.0)
        self.assertTrue(np.all(out == 400))  # 100 base + 3*100 overlay

    def test_negative_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AM.mix_overlay(np.zeros(10, dtype="<i2"), np.zeros(2, dtype="<i2"), 100, -0.1)

    def test_wav_roundtrip(self) -> None:
        import tempfile

        sr = 16000
        data = (np.sin(np.arange(sr) / 20.0) * 10000).astype("<i2")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.wav"
            AM.write_wav_i16(p, data, sr)
            back, back_sr = AM.read_wav_i16(p)
            self.assertEqual(back_sr, sr)
            np.testing.assert_array_equal(back, data)

    def test_read_downmixes_stereo(self) -> None:
        import tempfile

        sr = 8000
        left = np.full(50, 100, dtype="<i2")
        right = np.full(50, 300, dtype="<i2")
        interleaved = np.empty(100, dtype="<i2")
        interleaved[0::2] = left
        interleaved[1::2] = right
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(interleaved.tobytes())
            mono, _ = AM.read_wav_i16(p)
            self.assertEqual(mono.size, 50)
            self.assertTrue(np.all(mono == 200))


class TestSpikesAreResearchOnly(unittest.TestCase):
    SPIKES = (
        "spike_self_echo.py",
        "spike_interruption_latency.py",
        "spike_bargein_live.py",
        "audio_mix.py",
    )

    def _imports(self, src: str) -> set[str]:
        mods: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        return mods

    def test_no_conversation_or_provider_imports(self) -> None:
        for name in self.SPIKES:
            src = (SPIKE_DIR / name).read_text(encoding="utf-8")
            mods = self._imports(src)
            offenders = {
                m for m in mods
                if m.startswith(("nexa.conversation", "nexa.providers",
                                 "nexa.voice_conversation", "nexa.memory",
                                 "nexa.bootstrap"))
            }
            self.assertFalse(offenders, f"{name} imports production brain: {offenders}")

    def test_no_history_mutation_text(self) -> None:
        for name in self.SPIKES:
            src = (SPIKE_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("_history", src, f"{name} touches conversation history")
            self.assertNotIn(".send(", src, f"{name} calls a session .send()")

    def test_audio_mix_is_pure_stdlib_numpy_only(self) -> None:
        mods = self._imports((SPIKE_DIR / "audio_mix.py").read_text(encoding="utf-8"))
        self.assertTrue(mods <= {"__future__", "wave", "pathlib", "numpy"}, mods)


def _load_bargein_pure() -> tuple:
    """Load only the pure helpers from spike_bargein_live.py without running
    its heavy module-level imports (pyaudio / pipecat / nexa). We exec the
    function sources in an isolated namespace."""
    import ast

    src = (SPIKE_DIR / "spike_bargein_live.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"derive_trial_metrics", "_agg"}
    picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ns: dict = {}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<bargein_pure>", "exec"), ns)  # noqa: S102
    return ns["derive_trial_metrics"], ns["_agg"]


class TestBargeinLiveMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self.derive, self.agg = _load_bargein_pure()

    def _raw(self, **over) -> dict:
        base = {
            "playback_started": True,
            "prompt_printed": 100.0,
            "trial_armed": 100.2,
            "vad_user_started": 101.0,
            "playback_stop_requested": 101.004,
            "playback_actually_stopped": 101.041,
            "prearm_vad_count": 0,
            "post_accept_vad_count": 0,
        }
        base.update(over)
        return base

    def test_happy_path_metrics(self) -> None:
        m = self.derive(self._raw())
        self.assertTrue(m["detected"])
        self.assertTrue(m["playback_started"])
        self.assertEqual(m["prompt_to_vad_s"], 1.0)          # incl. human reaction
        self.assertEqual(m["arm_to_vad_s"], 0.8)
        self.assertEqual(m["vad_to_stop_request_ms"], 4.0)   # control-plane
        self.assertEqual(m["vad_to_playback_stopped_ms"], 41.0)  # PLAYBACK TASK STOPPED
        self.assertFalse(m["prearm_contaminated"])

    def test_no_detection_yields_none_latencies_not_zero(self) -> None:
        m = self.derive(self._raw(vad_user_started=None, playback_stop_requested=None,
                                  playback_actually_stopped=None))
        self.assertFalse(m["detected"])
        self.assertIsNone(m["prompt_to_vad_s"])
        self.assertIsNone(m["vad_to_stop_request_ms"])
        self.assertIsNone(m["vad_to_playback_stopped_ms"])

    def test_playback_failed_still_reports_control_latency_but_no_media_stop(self) -> None:
        m = self.derive(self._raw(playback_started=False, playback_actually_stopped=None))
        self.assertTrue(m["detected"])
        self.assertFalse(m["playback_started"])
        self.assertEqual(m["vad_to_stop_request_ms"], 4.0)
        self.assertIsNone(m["vad_to_playback_stopped_ms"])

    def test_prearm_vad_events_flag_contamination_and_are_not_the_measurement(self) -> None:
        # A pre-arm VAD event must never silently become the interruption:
        # vad_user_started is still the post-arm timestamp; the trial is flagged.
        m = self.derive(self._raw(prearm_vad_count=2))
        self.assertTrue(m["prearm_contaminated"])
        self.assertEqual(m["prearm_vad_count"], 2)
        self.assertEqual(m["arm_to_vad_s"], 0.8)  # unchanged — post-arm event used

    def test_negative_prompt_to_vad_is_impossible_but_not_silently_hidden(self) -> None:
        # If the accepted event somehow predates the prompt, the number is
        # simply reported negative (a red flag), never clamped to 0.
        m = self.derive(self._raw(vad_user_started=99.5))
        self.assertLess(m["prompt_to_vad_s"], 0)

    def test_agg_ignores_none_and_reports_n(self) -> None:
        self.assertEqual(self.agg([]), {"n": 0, "mean": None, "median": None,
                                        "max": None, "min": None})
        a = self.agg([10.0, None, 20.0, 30.0])
        self.assertEqual((a["n"], a["mean"], a["max"], a["min"]), (3, 20.0, 30.0, 10.0))


if __name__ == "__main__":
    unittest.main()
