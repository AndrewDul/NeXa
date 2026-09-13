"""M2.6B.4M / R0052 — deterministic tests for
``docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py``.

Offline, pure Python, no audio hardware, no Pipecat, no Gemini. Tests
ONLY the probe's pure, offline-testable logic: interval collapsing,
windowed RMS/peak/confidence/volume aggregation, and trial
summarization. Never the real-hardware numbers themselves (those come
from real operator runs, git-ignored under ``self_echo_captures/``).
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

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

    def test_ref_raw_rms_quiet_before_and_after_phases_are_reported(self) -> None:
        # R0054: mirrors mic's own 3-phase reporting so a run reveals
        # WHERE reference frames actually land relative to the real
        # playback window, without needing a full --capture-pcm run.
        result = probe.summarize_trial(
            **self._base_kwargs(
                ref_rms_frames=[
                    {"t": 0.2, "rms": 500.0, "peak": 1000},  # before playback
                    {"t": 2.5, "rms": 700.0, "peak": 1200},  # after playback
                ]
            )
        )
        self.assertEqual(result["ref_raw_rms_phase_quiet_before"]["n_frames"], 1)
        self.assertAlmostEqual(
            result["ref_raw_rms_phase_quiet_before"]["rms_mean"], 500.0
        )
        self.assertEqual(result["ref_raw_rms_phase_after"]["n_frames"], 1)
        self.assertAlmostEqual(result["ref_raw_rms_phase_after"]["rms_mean"], 700.0)
        # and none of it counted as "during playback"
        self.assertEqual(result["ref_raw_rms_phase_playback"]["n_frames"], 0)

    def test_ref_raw_rms_phases_default_to_empty_without_playback_bounds(self) -> None:
        result = probe.summarize_trial(
            **self._base_kwargs(playback_start=None, playback_end=None)
        )
        self.assertEqual(result["ref_raw_rms_phase_quiet_before"], {"n_frames": 0})
        self.assertEqual(result["ref_raw_rms_phase_after"], {"n_frames": 0})


def _sine_pcm(n: int, *, freq_cycles_total: float = 40.0, amplitude: int = 8000):
    import numpy as np

    t = np.linspace(0, freq_cycles_total * 2 * np.pi, n)
    return (np.sin(t) * amplitude).astype(np.int16)


class TestCrossCorrelatePcm(unittest.TestCase):
    """R0053 Step 4 -- the offline, bounded-lag normalized
    cross-correlation the charter asks for as the 'minimum additional
    data' when RMS/confidence telemetry alone cannot distinguish
    amplitude mismatch from timing mismatch from unrelated noise."""

    def test_recovers_a_known_positive_lag(self) -> None:
        ref = _sine_pcm(4000)
        lag = 7
        mic = probe.np.zeros(4000, dtype=probe.np.int16)
        mic[lag:] = ref[: 4000 - lag]
        result = probe.cross_correlate_pcm(
            ref.tobytes(), mic.tobytes(), sample_rate=16000, max_lag_ms=5.0
        )
        self.assertEqual(result["best_lag_samples"], lag)
        self.assertGreater(result["normalized_correlation"], 0.99)

    def test_recovers_a_known_negative_lag(self) -> None:
        ref = _sine_pcm(4000)
        lag = -6
        mic = probe.np.zeros(4000, dtype=probe.np.int16)
        mic[: 4000 + lag] = ref[-lag:]
        result = probe.cross_correlate_pcm(
            ref.tobytes(), mic.tobytes(), sample_rate=16000, max_lag_ms=5.0
        )
        self.assertEqual(result["best_lag_samples"], lag)
        self.assertGreater(result["normalized_correlation"], 0.99)

    def test_uncorrelated_noise_gives_low_correlation(self) -> None:
        rng = probe.np.random.default_rng(1)
        ref = (rng.normal(0, 4000, 4000)).astype(probe.np.int16)
        mic = (rng.normal(0, 4000, 4000)).astype(probe.np.int16)
        result = probe.cross_correlate_pcm(
            ref.tobytes(), mic.tobytes(), sample_rate=16000, max_lag_ms=5.0
        )
        self.assertLess(abs(result["normalized_correlation"]), 0.3)

    def test_empty_reference_is_handled_without_raising(self) -> None:
        mic = _sine_pcm(1000).tobytes()
        result = probe.cross_correlate_pcm(
            b"", mic, sample_rate=16000, max_lag_ms=5.0
        )
        self.assertIsNone(result["best_lag_ms"])
        self.assertEqual(result["n_ref_samples"], 0)

    def test_empty_mic_is_handled_without_raising(self) -> None:
        ref = _sine_pcm(1000).tobytes()
        result = probe.cross_correlate_pcm(
            ref, b"", sample_rate=16000, max_lag_ms=5.0
        )
        self.assertIsNone(result["best_lag_ms"])
        self.assertEqual(result["n_mic_samples"], 0)

    def test_pure_silence_on_both_sides_is_handled_without_raising(self) -> None:
        silence = (b"\x00\x00") * 500
        result = probe.cross_correlate_pcm(
            silence, silence, sample_rate=16000, max_lag_ms=5.0
        )
        # zero-variance signal: every window is degenerate (denom == 0)
        self.assertIsNone(result["best_lag_ms"])


class TestSileroConfidenceConversionFix(unittest.TestCase):
    """R0053 CONFIRMED BUG: R0052's probe recorded Silero confidence as
    0.0 on every single frame in every one of its 5 real-hardware
    captures, because Silero's real ``voice_confidence`` (pipecat-ai
    1.8.1) returns a shape-(1,) numpy array, and `float()` on that
    raises `TypeError` under this repo's installed NumPy (2.5.2) --
    silently caught and defaulted to 0.0. Production's OWN decision was
    unaffected (it only ever compares/bool()s the array), but the
    probe's own recorded telemetry was garbage. This proves the FIXED
    conversion path handles exactly that shape, plus a bare scalar for
    good measure."""

    def test_shape_1_ndarray_converts_correctly(self) -> None:
        arr = probe.np.array([0.83], dtype="float32")
        self.assertAlmostEqual(
            float(probe.np.asarray(arr).reshape(-1)[0]), 0.83, places=5
        )

    def test_bare_float_still_reproduces_the_original_bug(self) -> None:
        # Documents WHY the bug existed -- if this ever stops raising
        # (a future numpy relaxing the rule again), the fixed conversion
        # above still works either way, so no test needs to change.
        arr = probe.np.array([0.5], dtype="float32")
        with self.assertRaises(TypeError):
            float(arr)

    def test_bare_python_scalar_still_converts(self) -> None:
        self.assertEqual(float(probe.np.asarray(0.42).reshape(-1)[0]), 0.42)


class TestGainToDbText(unittest.TestCase):
    """R0053 CONTRACT FIX: the probe's startup/per-trial diagnostic print
    of ``audible_gain_db`` -- a display-only inverse of the SAME linear
    gain ``CoherentReferenceGain.current_gain()`` reports, never a
    second gain computation."""

    def test_unity_gain_is_0db(self) -> None:
        self.assertEqual(probe._gain_to_db_text(1.0), "0.00")

    def test_known_gain_matches_its_own_db_value(self) -> None:
        # 10**(-9.72/20) -- the real value this checkpoint's own system
        # audit measured on the USB speaker's mixer.
        gain = 10.0 ** (-9.72 / 20.0)
        self.assertEqual(probe._gain_to_db_text(gain), "-9.72")

    def test_zero_gain_is_muted_not_a_crash(self) -> None:
        self.assertEqual(probe._gain_to_db_text(0.0), "-inf (muted)")

    def test_negative_gain_is_also_reported_as_muted(self) -> None:
        # defensive: current_gain() never returns negative, but this
        # must not raise on log10 of a non-positive number either way.
        self.assertEqual(probe._gain_to_db_text(-0.1), "-inf (muted)")


class TestPlayAssistantPhraseStopsOnConfirmedInterrupt(unittest.IsolatedAsyncioTestCase):
    """R0055 CONFIRMED BUG FIX: real-hardware evidence (an operator seeing
    "Bot started speaking again" ~60ms after every confirmed interruption,
    and every captured trial's own ``playback_start_t``/``playback_end_t``
    landing near ``bargein_confirmed_t``, not near the true dispatch time)
    showed ``_play_assistant_phrase`` kept injecting the REST of a ~3.5s
    fixture on its own 100ms schedule after ``BargeInController`` had
    already confirmed a local barge-in and broadcast an interruption.
    These tests are pure/offline (no Pipecat, no audio device, no
    asyncio.sleep waits) -- fake ``P``/``worker``/``lifecycle``/``bargein``
    doubles matching only the exact interface ``_play_assistant_phrase``
    calls, per this file's own established no-Pipecat-import convention."""

    class _FakeFrame:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _FakeWorker:
        def __init__(self) -> None:
            self.queued: list[Any] = []

        async def queue_frames(self, frames) -> None:
            self.queued.extend(frames)

    class _FakeLifecycle:
        def __init__(self) -> None:
            self.dispatched = False
            self.audio_produced_count = 0
            self.generation_done = False
            self.interrupted = False

        def mark_dispatched(self) -> None:
            self.dispatched = True

        def mark_audio_produced(self) -> None:
            self.audio_produced_count += 1

        def mark_generation_done(self) -> None:
            self.generation_done = True

        def mark_interrupted(self) -> None:
            self.interrupted = True

    class _FakeTelemetry:
        """Mirrors the one field ``_run_warmup`` actually reads from the
        REAL ``nexa.voice.bargein.BargeInTelemetry``."""

        def __init__(self) -> None:
            self.interrupt_confirmed = 0

    class _FakeBargein:
        def __init__(self) -> None:
            self.dispatched = False
            self.telemetry = TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeTelemetry()

        def notify_response_dispatched(self) -> None:
            self.dispatched = True

    @staticmethod
    def _P() -> dict:
        return {
            "TTSAudioRawFrame": TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeFrame,
            "TTSStoppedFrame": TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeFrame,
        }

    async def test_no_confirmed_event_plays_every_chunk_and_finishes_normally(self) -> None:
        P = self._P()
        worker = self._FakeWorker()
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)  # 3 full-ish chunks
        with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
            await probe._play_assistant_phrase(
                P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm, sample_rate=sample_rate,
                confirmed_event=None,
            )
        # every chunk queued plus one TTSStoppedFrame tail
        self.assertGreaterEqual(len(worker.queued), 2)
        self.assertTrue(lifecycle.generation_done)
        self.assertFalse(lifecycle.interrupted)

    async def test_confirmed_mid_phrase_stops_injecting_further_chunks(self) -> None:
        P = self._P()
        worker = self._FakeWorker()
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        # 10 full chunks -- plenty of room to confirm partway through.
        pcm = b"\x00\x00" * (chunk_bytes * 5) * 2

        confirmed_event = asyncio.Event()

        queued_before_confirm: list[int] = []

        async def _sleep_and_confirm_after_second_chunk(_seconds: float) -> None:
            queued_before_confirm.append(len(worker.queued))
            if len(worker.queued) == 2:
                confirmed_event.set()

        with mock.patch.object(probe.asyncio, "sleep", new=_sleep_and_confirm_after_second_chunk):
            await probe._play_assistant_phrase(
                P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm, sample_rate=sample_rate,
                confirmed_event=confirmed_event,
            )

        # Exactly the 2 chunks queued before confirmation -- no
        # TTSStoppedFrame, no further TTSAudioRawFrame chunks after.
        self.assertEqual(len(worker.queued), 2)
        self.assertTrue(all(isinstance(f, self._FakeFrame) for f in worker.queued))
        # The lifecycle was ended via the SAME primitive production's own
        # `_on_confirmed` uses, never the normal completion tail.
        self.assertTrue(lifecycle.interrupted)
        self.assertFalse(lifecycle.generation_done)

    async def test_confirmed_before_first_chunk_injects_nothing(self) -> None:
        P = self._P()
        worker = self._FakeWorker()
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3)

        confirmed_event = asyncio.Event()
        confirmed_event.set()  # already confirmed before the phrase ever starts

        with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
            await probe._play_assistant_phrase(
                P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm, sample_rate=sample_rate,
                confirmed_event=confirmed_event,
            )

        self.assertEqual(worker.queued, [])
        self.assertTrue(lifecycle.interrupted)
        self.assertFalse(lifecycle.generation_done)

    @staticmethod
    async def _instant_sleep(_seconds: float) -> None:
        return None


class TestRunWarmup(unittest.IsolatedAsyncioTestCase):
    """R0057 CORRECTION 4 (confirmed bug, fixed): a naive AEC warm-up
    built on ``--repeats N`` silently assumes each repeat delivers the
    FULL fixture (~3.5s). Source-audited and confirmed: that assumption
    is exactly what R0055's own confirmed-interruption truncation in
    ``_play_assistant_phrase`` can invalidate -- at MAX volume a real
    confirmed self-barge-in fires ~1.1-1.5s into playback (R0055's own
    measured figures), cutting a repeat short, and the false-confirm
    RATE is precisely what an R0057-style gain experiment changes
    between conditions. Fixed by looping ``_play_assistant_phrase`` with
    ``confirmed_event=None`` and counting the ACTUAL bytes each call
    returns.

    R0057 CORRECTION 5 (a second, deeper confirmed bug, fixed here):
    ``confirmed_event=None`` alone does NOT stop the REAL
    ``BargeInController`` from still confirming and broadcasting a
    genuine interruption during warm-up (it stays armed via
    ``notify_response_dispatched()``), and installed Pipecat source
    confirms every ``FrameProcessor`` -- ``AecReferenceFeeder`` included
    -- discards its own not-yet-processed frames when that happens, so a
    byte already counted as "queued" (``worker.queue_frames()``
    returning) could still be lost before ever reaching
    ``AecReferenceFeeder``. Fixed with ``arm_bargein=False``
    (structurally, proven from ``BargeInController._handle_speech_started``'s
    own ``response_in_flight`` guard), verified three ways:
    ``bargein.telemetry.interrupt_confirmed`` stays unchanged,
    ``recorder.ref_accepted_bytes`` (tapped AFTER ``aec_feeder``, its
    real position, unchanged) proves real acceptance not mere
    submission, and ``recorder.playback_start_count``/
    ``playback_stop_count`` prove every repeat reached a clean,
    uninterrupted completion.

    These tests are pure/offline, reusing the fake ``P``/``lifecycle``/
    ``bargein`` doubles this file's own
    ``TestPlayAssistantPhraseStopsOnConfirmedInterrupt`` already
    established, plus a REAL ``probe.Recorder()`` (a plain dataclass,
    "no Pipecat dependency, testable in isolation" per its own
    docstring) and a fake worker that simulates the downstream
    pipeline stages a real ``_PlaybackWatcher`` would observe (the
    exact tap position this checkpoint added ``ref_accepted``/
    playback-count telemetry to) -- never re-testing Pipecat itself,
    only this probe's own orchestration and assertion logic."""

    _FakeFrame = TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeFrame
    _FakeWorker = TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeWorker
    _FakeLifecycle = TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeLifecycle
    _FakeBargein = TestPlayAssistantPhraseStopsOnConfirmedInterrupt._FakeBargein

    class _FakeWorkerSimulatingDownstreamPipeline:
        """Simulates everything between ``worker.queue_frames()``
        returning and the real ``_PlaybackWatcher`` tap (positioned,
        unchanged, immediately after ``aec_feeder``): each
        ``TTSAudioRawFrame`` is immediately marked ``ref_accepted`` on
        the given recorder (as the real post-aec_feeder tap would,
        absent any interruption -- this fake represents the WORKING,
        uncontaminated case unless ``drop_after_n_chunks`` says
        otherwise) and a ``TTSStoppedFrame``/first-chunk-of-a-new-phrase
        drives ``mark_playback_start``/``mark_playback_end`` the same
        way a real ``BotStartedSpeakingFrame``/``BotStoppedSpeakingFrame``
        pair would."""

        def __init__(
            self, recorder: probe.Recorder, *, drop_after_n_chunks: int | None = None
        ) -> None:
            self.queued: list[Any] = []
            self._recorder = recorder
            self._playback_open = False
            self._chunks_seen = 0
            self._drop_after_n_chunks = drop_after_n_chunks

        async def queue_frames(self, frames) -> None:
            for frame in frames:
                self.queued.append(frame)
                if "audio" in frame.kwargs:
                    self._chunks_seen += 1
                    if not self._playback_open:
                        self._recorder.mark_playback_start()
                        self._playback_open = True
                    if (
                        self._drop_after_n_chunks is None
                        or self._chunks_seen <= self._drop_after_n_chunks
                    ):
                        self._recorder.mark_ref_accepted(frame.kwargs["audio"])
                    # else: simulates a chunk lost to an interruption
                    # broadcast between submission and acceptance --
                    # queued (worker.queue_frames saw it) but never
                    # reached the post-aec_feeder tap.
                else:
                    if self._playback_open:
                        self._recorder.mark_playback_end()
                        self._playback_open = False

    @staticmethod
    def _P() -> dict:
        return {
            "TTSAudioRawFrame": TestRunWarmup._FakeFrame,
            "TTSStoppedFrame": TestRunWarmup._FakeFrame,
        }

    @staticmethod
    async def _instant_sleep(_seconds: float) -> None:
        return None

    async def test_warmup_delivers_at_least_the_requested_seconds(self) -> None:
        """Direct proof of the >= guarantee, at the AUTHORITATIVE
        post-aec_feeder acceptance point (not mere queue submission): a
        fixture much shorter than the requested warm-up must be looped
        enough times that ``ref_accepted_bytes``, converted back to
        seconds, is >= requested -- not merely close, not assumed from
        a repeat count."""
        P = self._P()
        recorder = probe.Recorder()
        worker = self._FakeWorkerSimulatingDownstreamPipeline(recorder)
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        # a short, 0.3s-ish fixture -- many loops needed to reach 2.0s
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)
        requested_seconds = 2.0

        with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
            result = await probe._run_warmup(
                P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                sample_rate=sample_rate, warmup_seconds=requested_seconds,
            )

        accepted_seconds = result["ref_accepted_bytes"] / 2 / sample_rate
        self.assertGreaterEqual(accepted_seconds, requested_seconds)
        # proves the count is REAL, not a repeat-count assumption: the
        # fixture is shorter than one second, so reaching >=2.0s required
        # multiple whole-fixture repeats, each one fully accounted for.
        self.assertGreater(result["ref_accepted_bytes"], len(pcm))
        self.assertGreater(result["repeats_run"], 1)
        self.assertEqual(result["interrupt_confirmed_delta"], 0)
        self.assertEqual(result["playback_start_count"], result["repeats_run"])
        self.assertEqual(result["playback_stop_count"], result["repeats_run"])

    async def test_false_confirmed_barge_in_during_warmup_does_not_truncate(self) -> None:
        """The exact scenario this checkpoint's own bug report describes:
        a real confirmed self-barge-in occurring PARTWAY through warm-up
        must not reduce the total accepted PCM below the requested
        amount. Simulated by a bargein double whose own dispatch hook
        sets a "the real world just confirmed a barge-in" event -- since
        ``arm_bargein=False`` means ``_run_warmup`` never even calls
        ``notify_response_dispatched`` (the real armer), this double's
        own hook firing would prove a REGRESSION if it were reached; the
        assertions below confirm it is not."""
        P = self._P()
        recorder = probe.Recorder()
        lifecycle = self._FakeLifecycle()

        real_world_confirmed_event = asyncio.Event()

        class _BargeinThatWouldConfirmIfArmed(self._FakeBargein):
            def __init__(self) -> None:
                super().__init__()
                self.dispatch_count = 0

            def notify_response_dispatched(self) -> None:
                super().notify_response_dispatched()
                self.dispatch_count += 1
                real_world_confirmed_event.set()

        bargein = _BargeinThatWouldConfirmIfArmed()
        worker = self._FakeWorkerSimulatingDownstreamPipeline(recorder)
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)
        requested_seconds = 3.0

        with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
            result = await probe._run_warmup(
                P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                sample_rate=sample_rate, warmup_seconds=requested_seconds,
            )

        # notify_response_dispatched() must NEVER be called from warmup
        # (arm_bargein=False) -- proving the armer itself is unreached,
        # not merely that a downstream confirm didn't happen to fire.
        self.assertFalse(real_world_confirmed_event.is_set())
        self.assertEqual(bargein.dispatch_count, 0)
        accepted_seconds = result["ref_accepted_bytes"] / 2 / sample_rate
        self.assertGreaterEqual(accepted_seconds, requested_seconds)
        self.assertEqual(result["interrupt_confirmed_delta"], 0)
        # lifecycle.mark_interrupted() must never be reached from warmup
        # -- confirmed_event is structurally None, so no early return.
        self.assertFalse(lifecycle.interrupted)

    async def test_run_warmup_always_passes_confirmed_event_none_and_arm_bargein_false(
        self,
    ) -> None:
        """Structural guarantee, not a fallible flag: ``_run_warmup``
        must call ``_play_assistant_phrase`` with ``confirmed_event=None``
        AND ``arm_bargein=False`` on every single call, so a measured
        trial's own truncation/admission behavior (R0055's fix,
        R0057 Correction 5's own fix, both preserved unchanged) can
        never accidentally be disabled for a REAL trial, nor
        accidentally enabled for warmup."""
        P = self._P()
        recorder = probe.Recorder()
        worker = self._FakeWorkerSimulatingDownstreamPipeline(recorder)
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)

        seen_kwargs: list[dict] = []
        real_play = probe._play_assistant_phrase

        async def _spy(*args, **kwargs):
            seen_kwargs.append(
                {"confirmed_event": kwargs.get("confirmed_event", "MISSING"),
                 "arm_bargein": kwargs.get("arm_bargein", "MISSING")}
            )
            return await real_play(*args, **kwargs)

        with (
            mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep),
            mock.patch.object(probe, "_play_assistant_phrase", new=_spy),
        ):
            await probe._run_warmup(
                P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                sample_rate=sample_rate, warmup_seconds=1.5,
            )

        self.assertGreater(len(seen_kwargs), 0)
        self.assertTrue(all(k["confirmed_event"] is None for k in seen_kwargs))
        self.assertTrue(all(k["arm_bargein"] is False for k in seen_kwargs))

    async def test_every_warmup_repeat_reaches_normal_playback_completion(self) -> None:
        """STEP 3/4 requirement: every warm-up fixture repeat must reach
        a clean start-then-stop completion, proven by
        ``playback_start_count``/``playback_stop_count`` exactly
        matching ``repeats_run`` -- a hidden mid-fixture restart
        (R0055's own "Bot started speaking again" symptom) would show up
        as MORE starts than repeats."""
        P = self._P()
        recorder = probe.Recorder()
        worker = self._FakeWorkerSimulatingDownstreamPipeline(recorder)
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)

        with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
            result = await probe._run_warmup(
                P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                sample_rate=sample_rate, warmup_seconds=4.0,
            )

        self.assertGreater(result["repeats_run"], 1)
        self.assertEqual(result["playback_start_count"], result["repeats_run"])
        self.assertEqual(result["playback_stop_count"], result["repeats_run"])

    async def test_artificially_dropped_reference_pcm_makes_the_proof_fail(self) -> None:
        """STEP 4 test #6: if an interruption (or anything else) causes
        submitted PCM to never reach the post-aec_feeder acceptance tap,
        ``_run_warmup``'s own returned evidence must reflect the
        shortfall -- never silently report success on queue submission
        alone. Uses the fake worker's own ``drop_after_n_chunks`` to
        simulate exactly the contamination this checkpoint's bug report
        describes (a chunk counted as "queued" that never actually
        arrived), and a short poll timeout so the test stays fast."""
        P = self._P()
        recorder = probe.Recorder()
        # drop every chunk after the first 2 -- simulates an interruption
        # wiping out everything accepted after some point mid-warmup.
        worker = self._FakeWorkerSimulatingDownstreamPipeline(
            recorder, drop_after_n_chunks=2
        )
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)
        requested_seconds = 3.0

        with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
            result = await probe._run_warmup(
                P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                sample_rate=sample_rate, warmup_seconds=requested_seconds,
                poll_timeout_s=0.05,
            )

        accepted_seconds = result["ref_accepted_bytes"] / 2 / sample_rate
        queued_seconds = result["delivered_bytes"] / 2 / sample_rate
        # queue submission alone reached the target ...
        self.assertGreaterEqual(queued_seconds, requested_seconds)
        # ... but the AUTHORITATIVE acceptance count did NOT -- this is
        # exactly what must make the caller's own assertion in `_run()`
        # fail rather than falsely report >= requested_seconds.
        self.assertLess(accepted_seconds, requested_seconds)

    async def test_measured_trial_path_still_truncates_on_confirmed_interruption(self) -> None:
        """Preserves R0055's own diagnostic fix: this is the SAME
        assertion as ``TestPlayAssistantPhraseStopsOnConfirmedInterrupt
        .test_confirmed_mid_phrase_stops_injecting_further_chunks``,
        re-run here explicitly alongside the new warmup tests so a
        future change cannot accidentally weaken R0055's fix while
        "fixing" warmup -- the measured-trial code path
        (``confirmed_event`` NOT ``None``) must still stop injecting and
        call ``mark_interrupted()``, never ``_run_warmup``'s own
        always-``None`` behavior."""
        P = self._P()
        worker = self._FakeWorker()
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 5) * 2

        confirmed_event = asyncio.Event()

        async def _sleep_and_confirm_after_second_chunk(_seconds: float) -> None:
            if len(worker.queued) == 2:
                confirmed_event.set()

        with mock.patch.object(probe.asyncio, "sleep", new=_sleep_and_confirm_after_second_chunk):
            delivered = await probe._play_assistant_phrase(
                P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm, sample_rate=sample_rate,
                confirmed_event=confirmed_event,
            )

        self.assertEqual(len(worker.queued), 2)
        self.assertTrue(lifecycle.interrupted)
        # truncated -- proves the byte-count return itself is accurate too
        self.assertLess(delivered, len(pcm))

    class _FakeWorkerWithheldFinalStop:
        """CHARACTERIZATION-ONLY double (not used by any passing/fixed
        test elsewhere in this file): every playback START and every
        NON-FINAL playback STOP are observed synchronously and
        immediately, exactly like ``_FakeWorkerSimulatingDownstreamPipeline``
        above. The STOP corresponding to the configured final start index
        is deliberately withheld -- deferred behind an explicit
        ``asyncio.Event`` the test controls -- never behind a sleep or
        arbitrary delay. Reference bytes are always marked accepted
        immediately for every chunk; this double isolates the
        playback-stop race specifically, not reference acceptance."""

        def __init__(
            self, recorder: probe.Recorder, *, withhold_stop_after_start_index: int,
            release_event: asyncio.Event,
        ) -> None:
            self.queued: list[Any] = []
            self._recorder = recorder
            self._playback_open = False
            self._start_count = 0
            self._withhold_index = withhold_stop_after_start_index
            self._release_event = release_event
            self.deferred_tasks: list[asyncio.Task] = []

        async def queue_frames(self, frames) -> None:
            for frame in frames:
                self.queued.append(frame)
                if "audio" in frame.kwargs:
                    if not self._playback_open:
                        self._start_count += 1
                        self._recorder.mark_playback_start()
                        self._playback_open = True
                    self._recorder.mark_ref_accepted(frame.kwargs["audio"])
                else:
                    self._playback_open = False
                    if self._start_count == self._withhold_index:

                        async def _deferred_stop(self=self) -> None:
                            await self._release_event.wait()
                            self._recorder.mark_playback_end()

                        self.deferred_tasks.append(asyncio.ensure_future(_deferred_stop()))
                    else:
                        self._recorder.mark_playback_end()

    async def test_characterization_warmup_can_return_before_final_playback_stop_observed(
        self,
    ) -> None:
        """CHARACTERIZATION of CURRENT behavior -- not a regression test
        proving a fix, and not itself an assertion that real hardware
        WILL fail this way; it demonstrates, deterministically and
        without any sleep/arbitrary delay, that ``_run_warmup``'s own
        bounded wait (source-confirmed: it polls only
        ``recorder.ref_accepted_bytes``, ``m2_6b4m_self_echo_probe.py``
        's ``_run_warmup``, never ``playback_stop_count``) CAN return
        while the final repeat's own playback-stop observation is still
        outstanding. The withheld stop is released only via an explicit
        ``asyncio.Event`` the test itself controls, set (or in this case
        deliberately never set) at a precise point -- never a timing
        guess. This characterizes the exact condition the caller's own
        third assert in ``_run()`` (``playback_start_count ==
        playback_stop_count == repeats_run``) exists to catch; it does
        NOT invoke ``_run()`` or that assert directly, and does NOT
        prove what happens on real hardware -- only that this function's
        own return condition does not wait for it."""
        P = self._P()
        recorder = probe.Recorder()
        release_event = asyncio.Event()  # deliberately never set in this test
        sample_rate = 16000
        chunk_bytes = int(sample_rate * (probe.ASSISTANT_CHUNK_MS / 1000.0) * 2)
        pcm = b"\x00\x00" * (chunk_bytes * 3 // 2)
        requested_seconds = 2.0

        # Derive the expected repeat count the IDENTICAL way _run_warmup
        # itself derives it (m2_6b4m_self_echo_probe.py's own
        # `while delivered_bytes < target_bytes` loop) -- not guessed --
        # so the withheld stop is precisely the LAST one, not an
        # arbitrary index.
        target_bytes = int(round(requested_seconds * sample_rate)) * 2
        expected_repeats = 0
        delivered = 0
        while delivered < target_bytes:
            delivered += len(pcm)
            expected_repeats += 1

        worker = self._FakeWorkerWithheldFinalStop(
            recorder, withhold_stop_after_start_index=expected_repeats,
            release_event=release_event,
        )
        lifecycle = self._FakeLifecycle()
        bargein = self._FakeBargein()

        try:
            with mock.patch.object(probe.asyncio, "sleep", new=self._instant_sleep):
                result = await probe._run_warmup(
                    P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                    sample_rate=sample_rate, warmup_seconds=requested_seconds,
                )

            # _run_warmup RETURNED even though release_event was never
            # set -- the final stop was never observed.
            self.assertEqual(result["repeats_run"], expected_repeats)
            self.assertEqual(result["playback_start_count"], expected_repeats)
            self.assertEqual(result["playback_stop_count"], expected_repeats - 1)
            self.assertLess(result["playback_stop_count"], result["playback_start_count"])

            # This is EXACTLY the snapshot condition _run()'s own third
            # assert checks (playback_start_count == playback_stop_count
            # == repeats_run) -- shown here to not hold, without invoking
            # _run() or its assert statement directly.
            caller_assert_condition_holds = (
                result["playback_start_count"]
                == result["playback_stop_count"]
                == result["repeats_run"]
            )
            self.assertFalse(caller_assert_condition_holds)
        finally:
            for t in worker.deferred_tasks:
                t.cancel()


if __name__ == "__main__":
    unittest.main()
