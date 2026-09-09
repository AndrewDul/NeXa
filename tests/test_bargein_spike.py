"""M2.5A — deterministic tests for the barge-in research tooling.

Offline, no audio devices, no pipecat, no model. Covers the pure audio
maths in ``docs/research/m2_5_bargein/audio_mix.py`` and re-asserts the
spike scripts stay research-only (no production-state imports, no
``ConversationSession`` mutation).
"""
from __future__ import annotations

import ast
import statistics
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
        "spike_playback_false_vad.py",
        "spike_aec_nearfield_voice.py",
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


def _load_false_vad_pure() -> dict:
    """Load the pure analysis helpers from spike_playback_false_vad.py in an
    isolated namespace (no pyaudio/pipecat/nexa import). Also carries the
    ALL-CAPS module constants those helpers close over, plus `statistics`."""
    src = (SPIKE_DIR / "spike_playback_false_vad.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted_fns = {"speaking_intervals", "classify_interval", "summarize_trial",
                  "verdict_300ms_safe", "_agg"}
    wanted_consts = {"QUIET_BEFORE_S"}  # the only module const the helpers close over
    picked: list = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in wanted_fns:
            picked.append(n)
        elif isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in wanted_consts for t in n.targets
        ):
            picked.append(n)
    ns: dict = {"statistics": statistics}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<false_vad_pure>", "exec"), ns)  # noqa: S102
    return ns


class TestPlaybackFalseVadAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ns = _load_false_vad_pure()

    def test_speaking_intervals_pairs_starts_and_stops(self) -> None:
        f = self.ns["speaking_intervals"]
        evs = [{"kind": "start", "t": 1.0}, {"kind": "stop", "t": 2.0},
               {"kind": "start", "t": 5.0}, {"kind": "stop", "t": 5.4}]
        self.assertEqual(f(evs), [(1.0, 2.0), (5.0, 5.4)])

    def test_speaking_intervals_open_start_has_none_stop(self) -> None:
        f = self.ns["speaking_intervals"]
        self.assertEqual(f([{"kind": "start", "t": 3.0}]), [(3.0, None)])

    def test_speaking_intervals_ignores_orphan_stop_and_dup_start(self) -> None:
        f = self.ns["speaking_intervals"]
        evs = [{"kind": "stop", "t": 0.1}, {"kind": "start", "t": 1.0},
               {"kind": "start", "t": 1.2}, {"kind": "stop", "t": 2.0}]
        self.assertEqual(f(evs), [(1.0, 2.0)])

    def test_classify_interval_before_during_after(self) -> None:
        f = self.ns["classify_interval"]
        self.assertEqual(f(8.0, 10.0, 30.0), "before")
        self.assertEqual(f(15.0, 10.0, 30.0), "during")
        self.assertEqual(f(31.0, 10.0, 30.0), "after")

    def test_summarize_trial_max_continuous_and_phase_stats(self) -> None:
        summarize = self.ns["summarize_trial"]
        pstart, pend, after = 100.0, 119.0, 122.0
        vad = [{"kind": "start", "t": 100.5}, {"kind": "stop", "t": 110.7}]  # 10.2 s
        frames = [
            {"t": 98.0, "conf": 0.05, "vol": 0.02, "speaking": False},   # quiet_before
            {"t": 101.0, "conf": 0.95, "vol": 0.83, "speaking": True},   # playback
            {"t": 105.0, "conf": 0.91, "vol": 0.72, "speaking": True},   # playback
            {"t": 120.0, "conf": 0.10, "vol": 0.05, "speaking": False},  # after
        ]
        s = summarize(playback_started=True, playback_start=pstart,
                      playback_end=pend, after_end=after,
                      vad_events=vad, frames=frames)
        self.assertTrue(s["false_vad"])
        self.assertEqual(s["user_speaking_starts"], 1)
        self.assertAlmostEqual(s["max_continuous_speaking_s"], 10.2, places=3)
        self.assertEqual(s["speaking_intervals"][0]["began"], "during")
        self.assertEqual(s["phase_playback"]["n_frames"], 2)
        self.assertEqual(s["phase_playback"]["speaking_frame_frac"], 1.0)
        self.assertEqual(s["phase_quiet_before"]["speaking_frame_frac"], 0.0)

    def test_verdict_300ms_unsafe_when_any_silent_trial_sustains_speaking(self) -> None:
        f = self.ns["verdict_300ms_safe"]
        trials = [
            {"playback_started": True, "max_continuous_speaking_s": 0.0},
            {"playback_started": True, "max_continuous_speaking_s": 3.5},
        ]
        v = f(trials)
        self.assertFalse(v["safe"])
        self.assertEqual(v["offending_trials"], [2])
        self.assertEqual(v["worst_continuous_speaking_s"], 3.5)

    def test_verdict_300ms_safe_when_all_silent_trials_stay_quiet(self) -> None:
        f = self.ns["verdict_300ms_safe"]
        trials = [{"playback_started": True, "max_continuous_speaking_s": 0.0},
                  {"playback_started": True, "max_continuous_speaking_s": 0.12}]
        self.assertTrue(f(trials)["safe"])

    def test_verdict_ignores_trials_where_playback_never_started(self) -> None:
        f = self.ns["verdict_300ms_safe"]
        trials = [{"playback_started": False, "max_continuous_speaking_s": 9.9}]
        self.assertTrue(f(trials)["safe"])


def _load_aec_nearfield_pure() -> dict:
    """Load the pure analysis helpers from spike_aec_nearfield_voice.py in an
    isolated namespace (no pyaudio/pipecat/nexa import)."""
    src = (SPIKE_DIR / "spike_aec_nearfield_voice.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"partition_vad_starts", "phase_stats", "summarize_trial",
              "close_criteria", "_agg"}
    picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ns: dict = {"statistics": statistics}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<aec_nf_pure>", "exec"), ns)  # noqa: S102
    return ns


class TestAecNearfieldAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ns = _load_aec_nearfield_pure()

    def test_partition_quiet_prearm_accepted_postaccept(self) -> None:
        f = self.ns["partition_vad_starts"]
        # quiet window [10,15]; armed at 20.
        r = f([12.0, 18.0, 21.0, 23.5], quiet_start=10.0, quiet_end=15.0, armed_at=20.0)
        self.assertEqual(r["quiet_false_vad_count"], 1)   # 12.0
        self.assertEqual(r["prearm_vad_count"], 1)        # 18.0 (not in quiet, before arm)
        self.assertEqual(r["accepted_at"], 21.0)          # first >= armed_at
        self.assertEqual(r["post_accept_vad_count"], 1)   # 23.5

    def test_partition_never_promotes_a_quiet_or_prearm_start_to_accepted(self) -> None:
        f = self.ns["partition_vad_starts"]
        r = f([12.0, 18.0], quiet_start=10.0, quiet_end=15.0, armed_at=20.0)
        self.assertIsNone(r["accepted_at"])              # no post-arm start -> not detected
        self.assertEqual(r["quiet_false_vad_count"], 1)
        self.assertEqual(r["prearm_vad_count"], 1)

    def test_partition_boundary_start_exactly_at_armed_at_is_accepted(self) -> None:
        f = self.ns["partition_vad_starts"]
        r = f([20.0], quiet_start=10.0, quiet_end=15.0, armed_at=20.0)
        self.assertEqual(r["accepted_at"], 20.0)

    def test_phase_stats_empty_and_populated(self) -> None:
        f = self.ns["phase_stats"]
        self.assertEqual(f([], 0.0, 1.0), {"n_frames": 0})
        frames = [
            {"t": 0.1, "conf": 0.2, "vol": 0.4, "speaking": False},
            {"t": 0.2, "conf": 0.9, "vol": 0.8, "speaking": True},
        ]
        s = f(frames, 0.0, 1.0)
        self.assertEqual(s["n_frames"], 2)
        self.assertEqual(s["conf_max"], 0.9)
        self.assertEqual(s["vol_max"], 0.8)
        self.assertEqual(s["speaking_frame_frac"], 0.5)

    def test_summarize_trial_detected_reports_informational_latency_only(self) -> None:
        summ = self.ns["summarize_trial"]
        raw = {
            "trial": 1, "phrase": "Czekaj.",
            "main_playback_active": True, "aec_reference_active": True,
            "aec_ref_spawn_delta_ms": 0.3,
            "quiet_start": 100.0, "quiet_end": 105.0,
            "prompt_printed": 105.0, "armed_at": 105.1,
            "start_times": [106.0],
            "aec_reference_active_at_detection": True,
            "main_playback_active_at_detection": True,
            "vad_events": [{"kind": "start", "t": 106.0}, {"kind": "stop", "t": 106.9}],
            "frames": [
                {"t": 101.0, "conf": 0.72, "vol": 0.52, "speaking": False},  # aec quiet
                {"t": 106.1, "conf": 0.95, "vol": 0.80, "speaking": True},   # operator
            ],
        }
        s = summ(raw)
        self.assertTrue(s["post_arm_vad_detected"])
        self.assertEqual(s["quiet_false_vad_count"], 0)
        self.assertIn("prompt_to_vad_s_INFORMATIONAL", s)
        self.assertAlmostEqual(s["prompt_to_vad_s_INFORMATIONAL"], 1.0, places=3)
        self.assertTrue(s["trial_valid"])
        self.assertEqual(s["conf_vol_operator_speech"]["n_frames"], 1)
        self.assertEqual(s["conf_vol_aec_quiet"]["n_frames"], 1)

    def test_summarize_trial_not_detected_has_none_latency(self) -> None:
        summ = self.ns["summarize_trial"]
        raw = {
            "trial": 2, "phrase": "x",
            "main_playback_active": True, "aec_reference_active": True,
            "aec_ref_spawn_delta_ms": 0.2,
            "quiet_start": 0.0, "quiet_end": 5.0,
            "prompt_printed": 5.0, "armed_at": 5.1,
            "start_times": [], "aec_reference_active_at_detection": None,
            "main_playback_active_at_detection": None,
            "vad_events": [], "frames": [],
        }
        s = summ(raw)
        self.assertFalse(s["post_arm_vad_detected"])
        self.assertIsNone(s["prompt_to_vad_s_INFORMATIONAL"])
        self.assertIsNone(s["aec_reference_active_at_detection"])

    def test_close_criteria_met_when_quiet_clean_and_operator_detected(self) -> None:
        f = self.ns["close_criteria"]
        trials = [
            {"trial": 1, "trial_valid": True, "quiet_false_vad_count": 0,
             "post_arm_vad_detected": True, "aec_reference_active_at_detection": True},
            {"trial": 2, "trial_valid": True, "quiet_false_vad_count": 0,
             "post_arm_vad_detected": True, "aec_reference_active_at_detection": True},
        ]
        r = f(trials)
        self.assertTrue(r["m2_5a_close_criteria_met"])
        self.assertEqual(r["operator_detected"], 2)
        self.assertEqual(r["blocking_reasons"], [])

    def test_close_criteria_blocks_on_quiet_false_vad(self) -> None:
        f = self.ns["close_criteria"]
        trials = [{"trial": 1, "trial_valid": True, "quiet_false_vad_count": 1,
                   "post_arm_vad_detected": True,
                   "aec_reference_active_at_detection": True}]
        r = f(trials)
        self.assertFalse(r["m2_5a_close_criteria_met"])
        self.assertTrue(any("QUIET_AEC" in x for x in r["blocking_reasons"]))

    def test_close_criteria_blocks_when_operator_not_detected(self) -> None:
        f = self.ns["close_criteria"]
        trials = [{"trial": 1, "trial_valid": True, "quiet_false_vad_count": 0,
                   "post_arm_vad_detected": False,
                   "aec_reference_active_at_detection": None}]
        r = f(trials)
        self.assertFalse(r["m2_5a_close_criteria_met"])
        self.assertTrue(any("NOT detected" in x for x in r["blocking_reasons"]))

    def test_close_criteria_ignores_invalid_trials_but_flags_them(self) -> None:
        f = self.ns["close_criteria"]
        trials = [
            {"trial": 1, "trial_valid": False, "quiet_false_vad_count": 0,
             "post_arm_vad_detected": False, "aec_reference_active_at_detection": None},
        ]
        r = f(trials)
        self.assertFalse(r["m2_5a_close_criteria_met"])   # no valid trials
        self.assertTrue(any("dead playback endpoint" in x for x in r["blocking_reasons"]))


if __name__ == "__main__":
    unittest.main()
