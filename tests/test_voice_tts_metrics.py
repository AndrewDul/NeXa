"""M2.4B.1 — deterministic tests for the realtime speech-flow instrumentation
(`nexa.voice_tts.metrics`). Pure metric math + collector correlation +
resource-sample summarisation. No hardware, no network, no Pipecat pipeline,
no sleeps — `time.monotonic` is monkeypatched where a wall clock is needed.

Covers the 19 required areas from the M2.4B.1 brief (see the numbered
comments), plus structural guards that the instrumentation adds no second
model path / TTS scheduler / VoiceState transition.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice_tts import metrics as M  # noqa: E402
from nexa.voice_tts.metrics import (  # noqa: E402
    ActivityFlags,
    BufferEstimate,
    MetricsCollector,
    PlaybackSpan,
    ResourceSample,
    TtsSegment,
    TurnMetrics,
    audio_bytes_to_seconds,
    diagnose_dominant_wait,
    render_turn_report,
    summarize_resource_window,
)
from nexa.voice_tts.timing import TurnTiming  # noqa: E402


class _Clock:
    """Deterministic monkeypatch for time.monotonic()."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _mk_turn(**kw) -> TurnMetrics:
    return TurnMetrics(turn_index=1, timing=TurnTiming(**kw))


# 5. audio byte -> duration
class TestAudioBytesToSeconds(unittest.TestCase):
    def test_16k_mono_s16le(self) -> None:
        # 16000 Hz * 2 bytes * 1 ch = 32000 bytes/s
        self.assertAlmostEqual(audio_bytes_to_seconds(32000, 16000, 1), 1.0)
        self.assertAlmostEqual(audio_bytes_to_seconds(16000, 16000, 1), 0.5)

    def test_22050_stereo(self) -> None:
        self.assertAlmostEqual(audio_bytes_to_seconds(22050 * 2 * 2, 22050, 2), 1.0)

    def test_none_rate_returns_none_never_raises(self) -> None:
        self.assertIsNone(audio_bytes_to_seconds(1000, None, 1))
        self.assertIsNone(audio_bytes_to_seconds(1000, 0, 1))

    def test_negative_bytes_returns_none(self) -> None:
        self.assertIsNone(audio_bytes_to_seconds(-1, 16000, 1))

    def test_channels_default_to_one(self) -> None:
        self.assertAlmostEqual(audio_bytes_to_seconds(32000, 16000, None), 1.0)
        self.assertAlmostEqual(audio_bytes_to_seconds(32000, 16000, 0), 1.0)


# 1. first-token latency  2. generation duration  3. chars/sec
class TestLlmMath(unittest.TestCase):
    def test_first_token_latency_from_stt_result(self) -> None:
        tm = _mk_turn(end_of_turn=10.0, stt_result=12.0, first_token=15.5)
        self.assertAlmostEqual(tm.first_token_latency_s, 3.5)
        self.assertEqual(tm.first_token_latency_base, "stt_result")

    def test_first_token_latency_falls_back_to_end_of_turn(self) -> None:
        tm = _mk_turn(end_of_turn=10.0, first_token=14.0)
        self.assertAlmostEqual(tm.first_token_latency_s, 4.0)
        self.assertEqual(tm.first_token_latency_base, "end_of_turn")

    def test_first_token_latency_none_when_no_base_or_no_token(self) -> None:
        self.assertIsNone(_mk_turn(first_token=14.0).first_token_latency_s)
        self.assertIsNone(_mk_turn(stt_result=12.0).first_token_latency_s)
        self.assertIsNone(_mk_turn(stt_result=12.0).first_token_latency_base)

    def test_generation_duration(self) -> None:
        tm = _mk_turn(first_token=15.0, assistant_complete=27.0)
        self.assertAlmostEqual(tm.assistant_generation_duration_s, 12.0)

    def test_generation_duration_none_if_incomplete(self) -> None:
        self.assertIsNone(_mk_turn(first_token=15.0).assistant_generation_duration_s)

    def test_chars_per_s(self) -> None:
        tm = _mk_turn(first_token=15.0, assistant_complete=27.0)
        tm.assistant_text_chars = 240
        self.assertAlmostEqual(tm.llm_chars_per_s, 20.0)

    def test_chars_per_s_none_on_zero_duration_or_missing_chars(self) -> None:
        tm = _mk_turn(first_token=15.0, assistant_complete=15.0)
        tm.assistant_text_chars = 100
        self.assertIsNone(tm.llm_chars_per_s)  # zero duration
        tm2 = _mk_turn(first_token=15.0, assistant_complete=27.0)
        self.assertIsNone(tm2.llm_chars_per_s)  # no char count


# 4. TTS synthesis RTF
class TestTtsSegmentRtf(unittest.TestCase):
    def test_rtf_fast_synth(self) -> None:
        seg = TtsSegment(index=0, started_at=100.0, stopped_at=100.8)
        seg.audio_bytes = 32000 * 6  # 6 s of 16k mono
        seg.sample_rate, seg.num_channels = 16000, 1
        self.assertAlmostEqual(seg.audio_duration_s, 6.0)
        self.assertAlmostEqual(seg.synthesis_duration_s, 0.8)
        self.assertAlmostEqual(seg.synthesis_rtf, 0.8 / 6.0, places=4)

    def test_rtf_none_when_no_audio_or_not_stopped(self) -> None:
        self.assertIsNone(TtsSegment(index=0, started_at=100.0).synthesis_rtf)
        seg = TtsSegment(index=0, started_at=100.0, stopped_at=101.0)
        self.assertIsNone(seg.synthesis_rtf)  # no audio bytes / rate

    def test_time_to_first_audio(self) -> None:
        seg = TtsSegment(index=0, started_at=100.0, first_audio_at=100.9)
        self.assertAlmostEqual(seg.time_to_first_audio_s, 0.9)


# 6. multiple TTS segments correlated to one assistant turn
# 13. overlapping later conversation turn does not overwrite prior turn metrics
class TestCollectorCorrelation(unittest.TestCase):
    def setUp(self) -> None:
        self.clk = _Clock()
        self._orig = M.time.monotonic
        M.time.monotonic = self.clk

    def tearDown(self) -> None:
        M.time.monotonic = self._orig

    def test_multi_segment_single_turn(self) -> None:
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        c.start_turn(end_of_turn=0.0, stt_result=1.0, stt_text="pytanie")
        self.clk.advance(2.0)
        c.first_token()
        c.assistant_token("Ala ")
        c.assistant_token("ma kota.")
        # segment 1
        c.tts_started()
        self.clk.advance(0.5)
        c.tts_first_audio()
        c.tts_audio(32000 * 3, 16000, 1)  # 3 s audio
        self.clk.advance(0.3)
        c.tts_stopped()
        # 3 s idle -> Pipecat context recreate -> segment 2 (same turn)
        self.clk.advance(3.2)
        c.tts_started()
        self.clk.advance(0.4)
        c.tts_first_audio()
        c.tts_audio(32000 * 4, 16000, 1)
        self.clk.advance(0.2)
        c.tts_stopped()
        self.clk.advance(0.1)
        c.assistant_complete("Ala ma kota.")
        c.tts_response_end()

        self.assertEqual(len(finals), 1)
        tm = finals[0]
        self.assertEqual(len(tm.tts_segments), 2)
        self.assertEqual(tm.assistant_text_chars, 12)
        self.assertAlmostEqual(tm.tts_segments[0].audio_duration_s, 3.0)
        self.assertAlmostEqual(tm.tts_segments[1].audio_duration_s, 4.0)
        self.assertAlmostEqual(tm.total_audio_seconds, 7.0)

    def test_later_turn_does_not_overwrite_prior(self) -> None:
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        # turn 1 conversation completes
        c.start_turn(end_of_turn=0.0, stt_result=1.0, stt_text="one")
        self.clk.advance(1.0)
        c.first_token()
        c.assistant_token("x" * 30)
        c.assistant_complete("x" * 30)
        # turn 1 TTS begins
        c.tts_started()
        c.tts_first_audio()
        c.tts_audio(32000 * 5, 16000, 1)
        turn1_chars = 30
        # turn 2 conversation starts+completes while turn 1 still speaking
        c.start_turn(end_of_turn=10.0, stt_result=11.0, stt_text="two")
        self.clk.advance(1.0)
        c.first_token()
        c.assistant_token("y" * 60)
        c.assistant_complete("y" * 60)
        # turn 1 TTS ends -> finalises turn 1 with ITS OWN numbers
        c.tts_stopped()
        c.tts_response_end()
        self.assertEqual(finals[-1].turn_index, 1)
        self.assertEqual(finals[-1].assistant_text_chars, turn1_chars)
        self.assertAlmostEqual(finals[-1].total_audio_seconds, 5.0)
        # turn 2 TTS then runs and finalises independently
        c.tts_started()
        c.tts_first_audio()
        c.tts_audio(32000 * 8, 16000, 1)
        c.tts_stopped()
        c.tts_response_end()
        self.assertEqual(finals[-1].turn_index, 2)
        self.assertEqual(finals[-1].assistant_text_chars, 60)
        self.assertAlmostEqual(finals[-1].total_audio_seconds, 8.0)

    def test_response_end_before_final_bot_stopped_defers_finalization(self) -> None:
        # Real-hardware ordering (R0013): the downstream LLMFullResponseEndFrame
        # can arrive while a BotStarted span is still open.
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        c.start_turn(stt_result=0.0, stt_text="q")
        c.first_token()
        c.assistant_complete("done")
        c.tts_started()
        c.tts_first_audio()
        c.tts_audio(32000 * 5, 16000, 1)
        c.bot_started_speaking()
        c.tts_stopped()
        c.tts_response_end()          # end frame arrives — span still open
        self.assertEqual(finals, [])  # NOT finalised yet
        self.clk.advance(4.0)
        c.bot_stopped_speaking()      # the late BotStopped settles playback
        self.assertEqual(len(finals), 1)
        tm = finals[0]
        self.assertEqual(tm.bot_started_count, 1)
        self.assertEqual(tm.bot_stopped_count, 1)
        self.assertAlmostEqual(tm.playback_spans[0].duration_s, 4.0)

    def test_response_end_after_playback_settled_finalizes_now(self) -> None:
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        c.start_turn(stt_result=0.0, stt_text="q")
        c.assistant_complete("done")
        c.bot_started_speaking()
        c.tts_first_audio()
        c.tts_audio(32000, 16000, 1)
        c.bot_stopped_speaking()      # playback already settled
        c.tts_response_end()
        self.assertEqual(len(finals), 1)

    def test_first_audio_sample_not_spurious_underrun(self) -> None:
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        c.start_turn(stt_result=0.0, stt_text="q")
        c.assistant_complete("x")
        c.tts_started()
        c.tts_first_audio()           # sets first_audio_at, does NOT sample
        c.tts_audio(32000 * 4, 16000, 1)  # first sample taken here, after 4 s of audio
        c.tts_stopped()
        c.tts_response_end()
        tm = finals[0]
        first_samples = [v for _, v, lab in tm.buffer.samples if lab == "first_audio"]
        self.assertEqual(len(first_samples), 1)
        self.assertGreater(first_samples[0], 0.0)  # not a 0.0 underrun
        self.assertEqual(tm.output_underrun_count, 0)

    def test_text_chunk_since_prev_and_after_complete(self) -> None:
        c = MetricsCollector()
        c.start_turn(stt_result=0.0, stt_text="q")
        self.clk.advance(1.0)
        c.first_token()
        c.tts_started()
        c.tts_text("Zdanie pierwsze.")
        self.clk.advance(2.5)
        c.tts_text("Zdanie drugie.")
        self.clk.advance(0.5)
        c.assistant_complete("Zdanie pierwsze. Zdanie drugie. Trzecie")
        self.clk.advance(1.0)
        c.tts_text("Trzecie")
        tm = c._queue[0]
        self.assertEqual([ch.index for ch in tm.text_chunks], [0, 1, 2])
        self.assertIsNone(tm.text_chunks[0].since_prev_s)
        self.assertAlmostEqual(tm.text_chunks[1].since_prev_s, 2.5)
        self.assertFalse(tm.text_chunks[0].after_assistant_complete)
        self.assertTrue(tm.text_chunks[2].after_assistant_complete)


# 7. BotStarted/BotStopped span correlation  8. silence gap calculation
class TestPlaybackGaps(unittest.TestCase):
    def _turn_with_spans(self, spans: list[tuple[float, float | None]]) -> TurnMetrics:
        tm = _mk_turn()
        tm.playback_spans = [PlaybackSpan(started_at=s, stopped_at=e) for s, e in spans]
        return tm

    def test_gap_between_two_spans(self) -> None:
        tm = self._turn_with_spans([(100.0, 106.0), (110.5, 114.0)])
        self.assertEqual(tm.bot_started_count, 2)
        self.assertEqual(tm.bot_stopped_count, 2)
        self.assertAlmostEqual(tm.silence_gaps_ms[0], 4500.0)
        self.assertAlmostEqual(tm.max_gap_ms, 4500.0)
        self.assertAlmostEqual(tm.mean_gap_ms, 4500.0)

    def test_no_gap_when_single_span(self) -> None:
        tm = self._turn_with_spans([(100.0, 106.0)])
        self.assertEqual(tm.silence_gaps_ms, [])
        self.assertIsNone(tm.max_gap_ms)
        self.assertIsNone(tm.mean_gap_ms)

    def test_overlapping_spans_produce_no_negative_gap(self) -> None:
        tm = self._turn_with_spans([(100.0, 108.0), (107.0, 112.0)])
        self.assertEqual(tm.silence_gaps_ms, [])  # negative gap dropped

    def test_missing_stop_skips_that_gap(self) -> None:
        tm = self._turn_with_spans([(100.0, None), (110.0, 114.0)])
        self.assertEqual(tm.silence_gaps_ms, [])
        self.assertEqual(tm.bot_stopped_count, 1)


# 9. zero/negative estimated buffer -> underrun detection
# 10. buffered-audio estimate never becomes invalid/NaN
class TestBufferEstimate(unittest.TestCase):
    def test_none_before_first_audio(self) -> None:
        b = BufferEstimate()
        self.assertIsNone(b.value_at(100.0))
        self.assertIsNone(b.sample(100.0, "x"))

    def test_accumulates_and_drains(self) -> None:
        b = BufferEstimate()
        b.first_audio_at = 100.0
        b.on_audio(32000 * 3, 16000, 1)  # +3 s
        self.assertAlmostEqual(b.sample(100.0, "first_audio"), 3.0)
        self.assertAlmostEqual(b.sample(101.0, "periodic"), 2.0)  # 1 s elapsed
        b.on_audio(32000 * 2, 16000, 1)  # +2 s more
        self.assertAlmostEqual(b.sample(101.0, "chunk"), 4.0)

    def test_underrun_event_once_per_crossing(self) -> None:
        b = BufferEstimate()
        b.first_audio_at = 100.0
        b.on_audio(32000 * 1, 16000, 1)  # 1 s of audio
        b.sample(100.0, "a")  # +1.0
        b.sample(101.5, "b")  # -0.5 -> underrun #1
        b.sample(102.0, "c")  # -1.0 -> still below, no new event
        self.assertEqual(len(b.underrun_events), 1)
        b.on_audio(32000 * 5, 16000, 1)  # refill
        b.sample(102.0, "d")  # +4.0 -> back above
        b.sample(107.0, "e")  # -1.0 -> underrun #2
        self.assertEqual(len(b.underrun_events), 2)

    def test_never_nan_with_bad_audio(self) -> None:
        b = BufferEstimate()
        b.first_audio_at = 100.0
        b.on_audio(999, None, 1)  # unknown rate -> ignored
        b.on_audio(-50, 16000, 1)  # negative -> ignored
        v = b.sample(100.0, "x")
        self.assertEqual(v, 0.0)
        self.assertFalse(v != v)  # not NaN

    def test_min_max_tracking(self) -> None:
        b = BufferEstimate()
        b.first_audio_at = 100.0
        b.on_audio(32000 * 4, 16000, 1)
        b.sample(100.0, "a")  # 4.0
        b.sample(103.0, "b")  # 1.0
        b.sample(101.0, "c")  # 3.0
        self.assertAlmostEqual(b.max_value, 4.0)
        self.assertAlmostEqual(b.min_value, 1.0)


# buffer-estimate validation (INFERENCE label + estimate-vs-real error)
class TestBufferValidation(unittest.TestCase):
    def test_note_is_labelled_estimate(self) -> None:
        self.assertIn("ESTIMATE", BufferEstimate().note)
        self.assertIn("NOT a control signal", BufferEstimate().note)

    def test_estimate_vs_real_stop_error(self) -> None:
        tm = _mk_turn()
        tm.playback_spans = [PlaybackSpan(started_at=100.0, stopped_at=106.0)]
        tm.buffer.underrun_events = [106.3, 105.5]  # 0.3 and 0.5 off the real stop
        self.assertAlmostEqual(tm.estimate_vs_real_stop_error_s, 0.4)

    def test_error_none_without_underruns_or_stops(self) -> None:
        tm = _mk_turn()
        tm.playback_spans = [PlaybackSpan(started_at=100.0, stopped_at=106.0)]
        self.assertIsNone(tm.estimate_vs_real_stop_error_s)  # no underruns
        tm2 = _mk_turn()
        tm2.buffer.underrun_events = [10.0]
        self.assertIsNone(tm2.estimate_vs_real_stop_error_s)  # no stops


# 11. missing timestamps handled explicitly, never fabricated
class TestMissingTimestamps(unittest.TestCase):
    def test_empty_turn_dict_is_all_none_not_zero(self) -> None:
        tm = _mk_turn()
        d = tm.to_dict()
        for k in ("assistant_first_token_at", "assistant_complete_at", "first_tts_audio_at"):
            self.assertIsNone(d["timing"][k])
        for k in ("first_token_latency_s", "assistant_generation_duration_s", "llm_chars_per_s"):
            self.assertIsNone(d["llm"][k])
        self.assertIsNone(d["playback"]["max_gap_ms"])

    def test_ollama_metadata_fields_are_explicit_none(self) -> None:
        d = _mk_turn(first_token=1.0, assistant_complete=2.0).to_dict()
        for k in (
            "assistant_text_tokens", "llm_tokens_per_s", "ollama_load_duration_s",
            "ollama_prompt_eval_count", "ollama_eval_count",
        ):
            self.assertIsNone(d["llm"][k])


# 12. error turn still finalizes a report safely
class TestErrorTurn(unittest.TestCase):
    def setUp(self) -> None:
        self.clk = _Clock()
        self._orig = M.time.monotonic
        M.time.monotonic = self.clk

    def tearDown(self) -> None:
        M.time.monotonic = self._orig

    def test_error_then_response_end_finalizes(self) -> None:
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        c.start_turn(stt_result=0.0, stt_text="q")
        self.clk.advance(0.5)
        c.conversation_error(RuntimeError("ollama down"))
        c.tts_response_end()  # bridge emits an end frame on error too
        self.assertEqual(len(finals), 1)
        self.assertIn("RuntimeError", finals[0].error)
        # renders without raising
        self.assertIn("ERROR", render_turn_report(finals[0]))

    def test_close_flushes_unfinished_turn(self) -> None:
        finals: list[TurnMetrics] = []
        c = MetricsCollector(on_turn_finalized=finals.append)
        c.start_turn(stt_result=0.0, stt_text="q")
        c.first_token()
        c.assistant_token("partial")
        flushed = c.close()
        self.assertEqual(len(flushed), 1)
        self.assertEqual(len(finals), 1)  # emitted exactly once
        self.assertEqual(finals[0].turn_index, 1)


# resource-window summarisation
class TestResourceSummary(unittest.TestCase):
    def _s(self, at, cpu=None, llama=None, mem=None, swap=None, temp=None, thr=None,
           stt=False, tts=False, gen=False, over=1.0) -> ResourceSample:
        return ResourceSample(
            at=at, cpu_total_pct=cpu, llama_server_cpu_pct=llama, mem_available_mb=mem,
            swap_used_mb=swap, temp_c=temp, throttled_hex=thr, stt_active=stt,
            tts_active=tts, generation_active=gen, read_overhead_ms=over,
        )

    def test_empty_window(self) -> None:
        r = summarize_resource_window([])
        self.assertEqual(r.sample_count, 0)
        self.assertIsNone(r.cpu_total_pct_mean)

    def test_means_peaks_flags(self) -> None:
        samples = [
            self._s(1.0, cpu=40.0, llama=100.0, mem=5000.0, swap=0.0, temp=60.0,
                    thr="0x0", gen=True, over=0.5),
            self._s(2.0, cpu=80.0, llama=300.0, mem=4000.0, swap=10.0, temp=66.0,
                    thr="0x0", tts=True, over=1.5),
        ]
        r = summarize_resource_window(samples)
        self.assertEqual(r.sample_count, 2)
        self.assertAlmostEqual(r.cpu_total_pct_mean, 60.0)
        self.assertAlmostEqual(r.cpu_total_pct_peak, 80.0)
        self.assertAlmostEqual(r.llama_server_cpu_pct_peak, 300.0)
        self.assertAlmostEqual(r.temp_c_max, 66.0)
        self.assertAlmostEqual(r.mem_available_mb_min, 4000.0)
        self.assertAlmostEqual(r.swap_used_mb_max, 10.0)
        self.assertEqual(r.throttled_any_hex, "0x0")
        self.assertTrue(r.generation_overlap)
        self.assertTrue(r.tts_overlap)
        self.assertFalse(r.stt_overlap)
        self.assertAlmostEqual(r.sampler_overhead_ms_mean, 1.0)

    def test_throttled_mask_is_ored(self) -> None:
        samples = [self._s(1.0, thr="0x0"), self._s(2.0, thr="0x50005")]
        r = summarize_resource_window(samples)
        self.assertEqual(r.throttled_any_hex, "0x50005")


# raw /proc snapshot parsing (injected readers — no real hardware)
class TestProcSnapshotParsing(unittest.TestCase):
    def test_parses_injected_proc(self) -> None:
        files = {
            "/proc/stat": "cpu  100 0 50 800 20 0 0 0 0 0\ncpu0 ...\n",
            "/proc/123/stat": " ".join(["x"] * 13 + ["400", "100"] + ["y"] * 30),
            "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 4096000 kB\n"
            "SwapTotal: 2000000 kB\nSwapFree: 1500000 kB\n",
            "/sys/class/thermal/thermal_zone0/temp": "61700\n",
        }
        cmds = {("vcgencmd", "get_throttled"): "throttled=0x0\n"}
        snap = M.read_raw_proc_snapshot(
            123,
            _read=lambda p: files[p],
            _run=lambda c: cmds[tuple(c)],
        )
        self.assertEqual(snap.cpu_total_jiffies, 970)  # 100+0+50+800+20
        self.assertEqual(snap.cpu_idle_jiffies, 820)  # idle 800 + iowait 20
        self.assertEqual(snap.llama_proc_jiffies, 500)  # 400 + 100
        self.assertEqual(snap.mem_available_kb, 4096000)
        self.assertEqual(snap.swap_total_kb, 2000000)
        self.assertEqual(snap.swap_free_kb, 1500000)
        self.assertAlmostEqual(snap.temp_c, 61.7)  # from sysfs milli-degC, no subprocess
        self.assertEqual(snap.throttled_hex, "0x0")

    def test_read_throttled_false_skips_the_subprocess(self) -> None:
        calls: list[list[str]] = []

        def _run(c: list[str]) -> str:
            calls.append(c)
            return "throttled=0x0\n"

        snap = M.read_raw_proc_snapshot(
            None,
            read_throttled=False,
            _read=lambda p: ("50000\n" if "thermal" in p else "cpu 1 2 3 4\nMemAvailable: 1 kB\n"),
            _run=_run,
        )
        self.assertEqual(calls, [])  # no vcgencmd spawned
        self.assertIsNone(snap.throttled_hex)
        self.assertAlmostEqual(snap.temp_c, 50.0)

    def test_missing_pid_stat_is_none_not_crash(self) -> None:
        snap = M.read_raw_proc_snapshot(
            None,
            _read=lambda p: "cpu  1 2 3 4\n" if p == "/proc/stat" else "50000\n",
            _run=lambda c: "throttled=0x0\n",
        )
        self.assertIsNone(snap.llama_proc_jiffies)

    def test_cpu_percent_from_deltas(self) -> None:
        prev = M._RawProcSnapshot(
            at=0.0, cpu_total_jiffies=1000, cpu_idle_jiffies=800,
            llama_proc_jiffies=1000, mem_available_kb=None, swap_total_kb=None,
            swap_free_kb=None, temp_c=None, throttled_hex=None, read_wall_s=0.0,
        )
        cur = M._RawProcSnapshot(
            at=1.0, cpu_total_jiffies=1100, cpu_idle_jiffies=820,
            llama_proc_jiffies=1300, mem_available_kb=4000000, swap_total_kb=2000000,
            swap_free_kb=1900000, temp_c=62.0, throttled_hex="0x0", read_wall_s=0.002,
        )
        s = M._sample_from_deltas(prev, cur, ActivityFlags(), clk_tck=100)
        # dt_total=100, dt_idle=20 -> busy 80/100 = 80%
        self.assertAlmostEqual(s.cpu_total_pct, 80.0)
        # llama 300 jiffies / 100 tck = 3.0 s over 1.0 s wall = 300%
        self.assertAlmostEqual(s.llama_server_cpu_pct, 300.0)
        self.assertAlmostEqual(s.mem_available_mb, 4000000 / 1024.0)
        self.assertAlmostEqual(s.swap_used_mb, 100000 / 1024.0)
        self.assertAlmostEqual(s.read_overhead_ms, 2.0)


# diagnosis: only a category the numbers support, else UNKNOWN
class TestDiagnosis(unittest.TestCase):
    def test_first_token_dominant(self) -> None:
        tm = _mk_turn(stt_result=0.0, first_token=8.0)
        tm.playback_spans = [PlaybackSpan(started_at=9.0, stopped_at=12.0)]
        self.assertEqual(diagnose_dominant_wait(tm), "FIRST TOKEN")

    def test_llm_text_production_dominant(self) -> None:
        tm = _mk_turn(stt_result=0.0, first_token=1.0, assistant_complete=20.0)
        tm.assistant_text_chars = 300
        tm.playback_spans = [
            PlaybackSpan(started_at=2.0, stopped_at=8.0),
            PlaybackSpan(started_at=13.0, stopped_at=19.0),  # 5 s gap
        ]
        seg = TtsSegment(index=0, started_at=2.0, stopped_at=2.8)
        seg.audio_bytes, seg.sample_rate, seg.num_channels = 32000 * 6, 16000, 1  # RTF ~0.13
        tm.tts_segments = [seg]
        tm.buffer.underrun_events = [8.5]
        self.assertEqual(diagnose_dominant_wait(tm), "LLM TEXT PRODUCTION")

    def test_tts_synthesis_dominant(self) -> None:
        tm = _mk_turn(stt_result=0.0, first_token=1.0, assistant_complete=6.0)
        tm.playback_spans = [
            PlaybackSpan(started_at=2.0, stopped_at=6.0),
            PlaybackSpan(started_at=9.0, stopped_at=13.0),  # 3 s gap
        ]
        seg = TtsSegment(index=0, started_at=2.0, stopped_at=6.0)
        seg.audio_bytes, seg.sample_rate, seg.num_channels = 32000 * 4, 16000, 1  # RTF 1.0
        tm.tts_segments = [seg]
        self.assertEqual(diagnose_dominant_wait(tm), "TTS SYNTHESIS")

    def test_unknown_when_smooth(self) -> None:
        tm = _mk_turn(stt_result=0.0, first_token=1.0, assistant_complete=6.0)
        tm.playback_spans = [PlaybackSpan(started_at=2.0, stopped_at=12.0)]
        self.assertEqual(diagnose_dominant_wait(tm), "UNKNOWN")

    def test_unknown_when_no_data(self) -> None:
        self.assertEqual(diagnose_dominant_wait(_mk_turn()), "UNKNOWN")


# JSONL serialisation + text policy (14: report mode must not alter content;
# machine-readable report keeps only previews by default)
class TestJsonlSerialisation(unittest.TestCase):
    def test_to_dict_round_trips_json(self) -> None:
        tm = _mk_turn(end_of_turn=0.0, stt_result=1.0, first_token=3.0, assistant_complete=15.0)
        tm.assistant_text_chars = 200
        tm.assistant_text_preview = "Ala ma kota i psa"
        tm.stt_text_preview = "opowiedz o kotach"
        s = json.dumps(tm.to_dict(), ensure_ascii=False)
        back = json.loads(s)
        self.assertEqual(back["turn_index"], 1)
        self.assertAlmostEqual(back["llm"]["first_token_latency_s"], 2.0)
        self.assertEqual(back["diagnosis"], "UNKNOWN")

    def test_full_text_excluded_by_default_present_on_request(self) -> None:
        tm = _mk_turn()
        tm.assistant_text_preview = "secret-ish preview"
        self.assertNotIn("assistant_text_preview", tm.to_dict())
        self.assertIn("assistant_text_preview", tm.to_dict(include_text=True))

    def test_jsonl_writer_one_line_per_turn(self) -> None:
        import tempfile

        path = Path(tempfile.mkdtemp()) / "m24b1.jsonl"
        w = M.TurnReportJsonlWriter(str(path))
        w.write(_mk_turn())
        w.write(TurnMetrics(turn_index=2, timing=TurnTiming()))
        w.close()
        lines = path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["turn_index"], 2)


# 15/16/17: structural — no fake VoiceState, no second model path, no TTS scheduler
class TestNoSecondAuthorityOrFakeState(unittest.TestCase):
    def _src(self, rel: str) -> str:
        return (SRC / rel).read_text(encoding="utf-8")

    def _names(self, rel: str) -> set[str]:
        tree = ast.parse(self._src(rel))
        return {
            getattr(n, "id", None) or getattr(n, "attr", None)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Name, ast.Attribute))
        }

    def _imports(self, rel: str) -> set[str]:
        tree = ast.parse(self._src(rel))
        names: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module)
        return names

    def test_metrics_has_no_voice_state(self) -> None:
        names = self._names("nexa/voice_tts/metrics.py")
        self.assertNotIn("VoiceState", names)
        self.assertNotIn("VoiceStateMachine", names)

    def test_metrics_makes_no_model_or_http_chat_call(self) -> None:
        imports = self._imports("nexa/voice_tts/metrics.py")
        for bad in ("nexa.providers", "nexa.conversation", "nexa.bootstrap", "aiohttp"):
            self.assertNotIn(bad, imports, f"metrics.py must not import {bad}")
        for bad in imports:
            self.assertNotIn("ollama", bad.lower())
        names = self._names("nexa/voice_tts/metrics.py")
        self.assertNotIn("urlopen", names)
        self.assertNotIn("ConversationSession", names)

    def test_metrics_constructs_no_pipeline_or_scheduler(self) -> None:
        names = self._names("nexa/voice_tts/metrics.py")
        for bad in ("Pipeline", "PipelineWorker", "PiperHttpTTSService",
                    "AssistantSpeechBridge", "create_task"):
            self.assertNotIn(bad, names, f"metrics.py must not reference {bad}")

    def test_probe_report_path_pushes_no_frames(self) -> None:
        # The probe's instrumentation callbacks only read/record — they never
        # emit a frame or touch the pipeline.
        src = (REPO_ROOT / "apps" / "nexa_voice_tts_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "push_frame":
                self.fail("probe must not push frames")

    def test_observer_extra_callbacks_are_optional_and_default_none(self) -> None:
        import inspect

        from nexa.voice_tts import TtsStatusObserver

        sig = inspect.signature(TtsStatusObserver.__init__)
        for name in (
            "on_tts_audio", "on_tts_text", "on_tts_response_end",
            "on_bot_started_speaking", "on_bot_stopped_speaking",
        ):
            self.assertIn(name, sig.parameters)
            self.assertIsNone(sig.parameters[name].default)


if __name__ == "__main__":
    unittest.main()
