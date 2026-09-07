"""M2.4B.3.2 — NexaSpeechContinuityController: short-reply speech continuity.

Deterministic, offline. No microphone / Piper / model / network. Drives the
real ``NexaSpeechContinuityController`` FrameProcessor through real Pipecat
frames with a captured ``push_frame`` sink + a fake monotonic clock — the
same pattern ``test_voice_tts_bridge.py`` / ``test_voice_tts_speech_planner.py``
use.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipecat.frames.frames import (  # noqa: E402
    AggregatedTextFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402
from pipecat.utils.text.base_text_aggregator import AggregationType  # noqa: E402

from nexa.voice_tts import (  # noqa: E402
    ControllerRelease,
    MetricsCollector,
    NexaSpeechContinuityController,
    ReleaseReason,
    decide_release,
)
from nexa.voice_tts import continuity as C  # noqa: E402

CONTINUITY_SRC = SRC / "nexa" / "voice_tts" / "continuity.py"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _phrase(text: str) -> AggregatedTextFrame:
    return AggregatedTextFrame(text=text, aggregated_by=AggregationType.SENTENCE, raw_text=text)


def _spoken(pushed: list[Frame]) -> list[str]:
    return [f.text for f in pushed if isinstance(f, AggregatedTextFrame)]


class _Harness(unittest.IsolatedAsyncioTestCase):
    def _ctl(self, *, on_release=None, **kw):
        clock = _Clock()
        self._orig_now = C._now_s
        self._orig_sleep = C._sleep
        C._now_s = clock  # patched for the whole module (restored in tearDown)

        async def _instant_sleep(_delay):  # the bounded hold fires deterministically
            return

        C._sleep = _instant_sleep
        releases: list[ControllerRelease] = []
        c = NexaSpeechContinuityController(
            on_release=(on_release if on_release is not None else releases.append), **kw
        )
        pushed: list[Frame] = []

        async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        c.push_frame = fake_push_frame
        self._tasks: list[asyncio.Task] = []

        def fake_create_task(coro, name=None, context=None):
            t = asyncio.ensure_future(coro)
            self._tasks.append(t)
            return t

        async def fake_cancel_task(task, timeout=None):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        c.create_task = fake_create_task
        c.cancel_task = fake_cancel_task
        return c, pushed, releases, clock

    async def asyncTearDown(self) -> None:
        if hasattr(self, "_orig_now"):
            C._now_s = self._orig_now
        if hasattr(self, "_orig_sleep"):
            C._sleep = self._orig_sleep
        for t in getattr(self, "_tasks", []):
            if not t.done():
                t.cancel()

    async def _start(self, c) -> None:
        await c.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)

    async def _feed_audio(self, c, seconds: float, clock: _Clock) -> None:
        # 16 kHz mono s16le
        n = int(seconds * 16000) * 2
        c.note_tts_audio(n, 16000, 1)

    async def _phrase_frame(self, c, text: str) -> None:
        await c.process_frame(_phrase(text), FrameDirection.DOWNSTREAM)

    async def _end(self, c) -> None:
        await c.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)


# --------------------------------------------------------------------------- #
# 1..6  first/following phrase policy
# --------------------------------------------------------------------------- #


class TestPhrasePolicy(_Harness):
    async def test_1_first_phrase_passes_immediately(self) -> None:
        c, pushed, rel, clock = self._ctl()
        await self._start(c)
        f = _phrase("Pierwsze zdanie odpowiedzi asystenta.")
        await c.process_frame(f, FrameDirection.DOWNSTREAM)
        self.assertIs(pushed[-1], f)  # same object, immediately, unchanged
        self.assertEqual(rel[-1].reason, ReleaseReason.FIRST_PHRASE)
        self.assertEqual(rel[-1].hold_s, 0.0)

    async def test_2_no_first_phrase_prebuffer_even_with_huge_reserve(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        await self._feed_audio(c, 30.0, clock)  # pretend 30 s of audio already queued
        await self._phrase_frame(c, "Zdanie pierwsze mimo dużego bufora.")
        self.assertEqual(len(_spoken(pushed)), 1)
        self.assertEqual(rel[-1].reason, ReleaseReason.FIRST_PHRASE)
        self.assertEqual(rel[-1].hold_s, 0.0)

    async def test_3_healthy_buffer_holds_phrase_2_then_releases(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0, max_hold_s=1.0)
        await self._start(c)
        await self._phrase_frame(c, "Zdanie zero.")           # phrase 0 -> immediate
        await self._feed_audio(c, 4.0, clock)                 # 4 s reserve
        await self._phrase_frame(c, "Zdanie jeden trzymane.")  # phrase 1, reserve 4 >= 2 -> HELD
        self.assertEqual(len(_spoken(pushed)), 1, "phrase 1 must be held, not pushed yet")
        self.assertIsNotNone(c._held)
        # the bounded hold deadline is min(4-2, 1) = 1.0 s; advance the clock,
        # then let the (instant-sleep-patched) hold task run.
        clock.advance(1.0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(_spoken(pushed), ["Zdanie zero.", "Zdanie jeden trzymane."])
        self.assertEqual(rel[-1].reason, ReleaseReason.HOLD_EXPIRED)
        self.assertAlmostEqual(rel[-1].hold_s, 1.0, places=2)
        self.assertIsNone(c._held)

    async def test_4_low_buffer_releases_phrase_immediately(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        await self._phrase_frame(c, "Zdanie zero.")
        await self._feed_audio(c, 4.0, clock)
        clock.advance(3.0)  # reserve now ~1.0 s -> LOW
        await self._phrase_frame(c, "Zdanie jeden natychmiast.")
        self.assertEqual(_spoken(pushed), ["Zdanie zero.", "Zdanie jeden natychmiast."])
        self.assertEqual(rel[-1].reason, ReleaseReason.BUFFER_LOW)
        self.assertEqual(rel[-1].hold_s, 0.0)

    async def test_5_near_empty_releases_immediately(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0, near_empty_s=0.5)
        await self._start(c)
        await self._phrase_frame(c, "Zdanie zero.")
        await self._feed_audio(c, 3.0, clock)
        clock.advance(2.8)  # reserve ~0.2 s -> NEAR_EMPTY
        await self._phrase_frame(c, "Zdanie jeden pilnie.")
        self.assertEqual(rel[-1].reason, ReleaseReason.BUFFER_NEAR_EMPTY)
        self.assertEqual(len(_spoken(pushed)), 2)

    async def test_6_never_waits_to_grow_a_batch_while_buffer_low(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        await self._phrase_frame(c, "Zero.")
        await self._feed_audio(c, 3.0, clock)
        clock.advance(2.5)  # reserve ~0.5 -> LOW
        await self._phrase_frame(c, "Jeden.")
        await self._phrase_frame(c, "Dwa.")
        await self._phrase_frame(c, "Trzy.")
        # all released immediately, in order — nothing held to accumulate a batch
        self.assertEqual(_spoken(pushed), ["Zero.", "Jeden.", "Dwa.", "Trzy."])
        self.assertTrue(all(r.hold_s == 0.0 for r in rel))
        self.assertEqual([r.reason for r in rel[1:]], [ReleaseReason.BUFFER_LOW] * 3)


# --------------------------------------------------------------------------- #
# 7..11  ordering, flush, no dup / loss / mutation
# --------------------------------------------------------------------------- #


class TestOrderingAndIntegrity(_Harness):
    async def test_7_multiple_ready_phrases_preserve_order(self) -> None:
        c, pushed, rel, clock = self._ctl()
        await self._start(c)
        for t in ("A pierwsze.", "B drugie.", "C trzecie.", "D czwarte."):
            await self._phrase_frame(c, t)
        self.assertEqual(_spoken(pushed), ["A pierwsze.", "B drugie.", "C trzecie.", "D czwarte."])
        self.assertEqual([r.phrase_index for r in rel], [0, 1, 2, 3])

    async def test_8_end_of_turn_flush_releases_held_exactly_once(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        await self._phrase_frame(c, "Zero.")
        await self._feed_audio(c, 5.0, clock)
        await self._phrase_frame(c, "Jeden trzymane do konca tury.")  # HELD (reserve 5 >= 2)
        self.assertEqual(len(_spoken(pushed)), 1)
        await self._end(c)  # LLMFullResponseEndFrame
        self.assertEqual(_spoken(pushed), ["Zero.", "Jeden trzymane do konca tury."])
        self.assertEqual(rel[-1].reason, ReleaseReason.TURN_COMPLETE)
        self.assertTrue(any(isinstance(f, LLMFullResponseEndFrame) for f in pushed))
        # exactly once
        self.assertEqual(_spoken(pushed).count("Jeden trzymane do konca tury."), 1)

    async def test_9_10_no_duplicate_and_no_lost_phrases(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        fed = ["Z0.", "Z1.", "Z2.", "Z3.", "Z4."]
        for i, t in enumerate(fed):
            await self._phrase_frame(c, t)
            if i == 0:
                await self._feed_audio(c, 6.0, clock)  # make some held
        await self._end(c)
        clock.advance(2.0)
        await asyncio.sleep(0)
        self.assertEqual(sorted(_spoken(pushed)), sorted(fed))  # no loss, no dup
        self.assertEqual(len(_spoken(pushed)), len(fed))

    async def test_11_frame_text_is_never_mutated(self) -> None:
        c, pushed, rel, clock = self._ctl()
        await self._start(c)
        f = _phrase("**Oryginał** z formatowaniem $\\text{H}$ i przecinkami, itd.")
        original = f.text
        await c.process_frame(f, FrameDirection.DOWNSTREAM)
        self.assertIs(pushed[-1], f)
        self.assertEqual(pushed[-1].text, original)  # byte-identical, same object


# --------------------------------------------------------------------------- #
# 12..20  architecture / reset / safety
# --------------------------------------------------------------------------- #


class TestArchitectureAndReset(_Harness):
    async def test_12_holds_no_session_or_history_reference(self) -> None:
        c, *_ = self._ctl()
        for attr in vars(c):
            low = attr.lower()
            self.assertNotIn("session", low)
            self.assertNotIn("history", low)
            self.assertNotIn("provider", low)

    async def test_19_controller_resets_between_turns(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        await self._phrase_frame(c, "T1 zero.")
        await self._feed_audio(c, 5.0, clock)
        await self._phrase_frame(c, "T1 jeden trzymane.")  # HELD
        self.assertEqual(len(_spoken(pushed)), 1)
        # a new turn starts before the held phrase's deadline
        await self._start(c)  # LLMFullResponseStartFrame
        self.assertEqual(_spoken(pushed), ["T1 zero.", "T1 jeden trzymane."])  # flushed on reset
        self.assertEqual(rel[-1].reason, ReleaseReason.RESET)
        # turn 2: its first phrase is FIRST_PHRASE again (index reset), audio counter reset
        await self._phrase_frame(c, "T2 zero.")
        self.assertEqual(rel[-1].reason, ReleaseReason.FIRST_PHRASE)
        self.assertEqual(rel[-1].phrase_index, 0)
        self.assertIsNone(c.reserve_estimate_s())

    async def test_20_interruption_flushes_held_and_is_safe(self) -> None:
        c, pushed, rel, clock = self._ctl(target_reserve_s=2.0)
        await self._start(c)
        await self._phrase_frame(c, "Zero.")
        await self._feed_audio(c, 5.0, clock)
        await self._phrase_frame(c, "Jeden trzymane.")  # HELD
        await c.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(_spoken(pushed), ["Zero.", "Jeden trzymane."])
        self.assertEqual(rel[-1].reason, ReleaseReason.RESET)
        self.assertTrue(any(isinstance(f, InterruptionFrame) for f in pushed))

    async def test_20b_on_release_callback_raising_does_not_break_speech(self) -> None:
        def boom(_rel):
            raise RuntimeError("metrics sink is broken")

        c, pushed, _rel, clock = self._ctl(on_release=boom)
        await self._start(c)
        await self._phrase_frame(c, "Nadal mówię mimo zepsutych metryk.")
        self.assertEqual(_spoken(pushed), ["Nadal mówię mimo zepsutych metryk."])

    async def test_disabled_is_pure_passthrough(self) -> None:
        c, pushed, rel, clock = self._ctl(enabled=False, target_reserve_s=2.0)
        await self._start(c)
        await self._feed_audio(c, 10.0, clock)
        for t in ("A.", "B.", "C."):
            await self._phrase_frame(c, t)
        self.assertEqual(_spoken(pushed), ["A.", "B.", "C."])  # order, no hold
        self.assertTrue(all(r.hold_s == 0.0 for r in rel))
        self.assertTrue(all(r.reason == ReleaseReason.CONTROLLER_DISABLED for r in rel))

    async def test_unknown_frames_pass_through(self) -> None:
        c, pushed, rel, clock = self._ctl()
        await self._start(c)
        marker = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)
        await c.process_frame(marker, FrameDirection.DOWNSTREAM)
        self.assertIs(pushed[-1], marker)


class TestSourceArchitectureGuards(unittest.TestCase):
    def _imports(self) -> set[str]:
        tree = ast.parse(CONTINUITY_SRC.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_14_no_model_provider_session_or_planner_normalizer_import(self) -> None:
        imported = self._imports()
        for bad in ("nexa.conversation", "nexa.bootstrap", "nexa.config", "nexa.providers"):
            self.assertNotIn(bad, imported)
            for m in imported:
                self.assertFalse(m.startswith(bad + "."), m)
        # 13: does not import the planner's normaliser — it is not a second
        # normalization authority.
        self.assertNotIn("nexa.voice_tts.speech_planner", imported)

    def test_13_defines_no_text_normalization(self) -> None:
        tree = ast.parse(CONTINUITY_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self.assertNotIn(node.name, (
                    "normalize_for_speech", "find_phrase_cut", "_strip_math",
                    "_normalize", "_tidy_spoken", "_join_items",
                ))

    def test_15_16_18_no_speed_control_no_filler_no_transport(self) -> None:
        src = CONTINUITY_SRC.read_text(encoding="utf-8")
        for bad in ("length_scale", "noise_scale", "noise_w",  # 15 no speech-speed
                    "filler", "interstitial",                   # 16 no fillers
                    "LocalAudioTransport", "pyaudio", "transports",  # 18 no new transport
                    "renice", "taskset",                        # not our layer
                    "num_predict", "keep_alive"):               # not our layer
            self.assertNotIn(bad, src, f"continuity.py must not reference {bad!r}")

    def test_17_the_only_sleep_is_the_bounded_hold(self) -> None:
        tree = ast.parse(CONTINUITY_SRC.read_text(encoding="utf-8"))
        src = CONTINUITY_SRC.read_text(encoding="utf-8")
        self.assertNotIn("time.sleep", src)
        self.assertNotIn("asyncio.sleep", src)  # imported as _sleep, used once
        sleep_funcs: list[str] = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_sleep":
                    sleep_funcs.append(fn.name)
        self.assertEqual(sleep_funcs, ["_hold_then_release"],
                         "the bounded-hold wait may appear only inside _hold_then_release")

    def test_module_imports_only_stdlib_loguru_pipecat(self) -> None:
        for m in self._imports():
            top = m.split(".")[0]
            self.assertIn(top, {"__future__", "asyncio", "time", "collections",
                                "dataclasses", "loguru", "pipecat"}, m)


# --------------------------------------------------------------------------- #
# decide_release (pure) + metric math
# --------------------------------------------------------------------------- #


class TestDecideReleasePure(unittest.TestCase):
    KW = dict(target_s=2.0, near_empty_s=0.5, max_hold_s=1.0)

    def test_first_is_always_immediate(self) -> None:
        self.assertEqual(decide_release(99.0, is_first=True, **self.KW),
                         (ReleaseReason.FIRST_PHRASE, 0.0))
        self.assertEqual(decide_release(None, is_first=True, **self.KW),
                         (ReleaseReason.FIRST_PHRASE, 0.0))

    def test_no_estimate_releases_now(self) -> None:
        self.assertEqual(decide_release(None, is_first=False, **self.KW),
                         (ReleaseReason.BUFFER_LOW, 0.0))

    def test_thresholds(self) -> None:
        self.assertEqual(decide_release(0.2, is_first=False, **self.KW)[0],
                         ReleaseReason.BUFFER_NEAR_EMPTY)
        self.assertEqual(decide_release(1.4, is_first=False, **self.KW)[0],
                         ReleaseReason.BUFFER_LOW)
        reason, hold = decide_release(2.6, is_first=False, **self.KW)
        self.assertEqual(reason, C.PLAN_HOLD)
        self.assertAlmostEqual(hold, 0.6)

    def test_hold_is_capped_at_max_hold(self) -> None:
        _, hold = decide_release(50.0, is_first=False, **self.KW)
        self.assertEqual(hold, 1.0)  # min(50-2, 1.0)

    def test_exactly_at_target_does_not_hold(self) -> None:
        # reserve == target -> hold would be 0 -> release now
        self.assertEqual(decide_release(2.0, is_first=False, **self.KW),
                         (ReleaseReason.BUFFER_LOW, 0.0))


class TestControllerMetricMath(unittest.TestCase):
    def _rel(self, recv: float, rel: float, reason: str) -> ControllerRelease:
        return ControllerRelease(
            phrase_index=1, text_len=40, received_at=recv, released_at=rel,
            reserve_at_receive_s=2.4, reserve_at_release_s=1.9, reason=reason,
        )

    def test_hold_s_is_released_minus_received_clamped(self) -> None:
        self.assertAlmostEqual(self._rel(100.0, 100.8, "X").hold_s, 0.8)
        self.assertEqual(self._rel(100.0, 99.5, "X").hold_s, 0.0)  # clamp >= 0

    def test_collector_attributes_releases_fifo_and_aggregates(self) -> None:
        col = MetricsCollector()
        col.start_turn(stt_result=0.0, stt_text="q")
        col.controller_release(self._rel(0.0, 0.0, ReleaseReason.FIRST_PHRASE))
        col.controller_release(self._rel(0.0, 0.7, ReleaseReason.HOLD_EXPIRED))
        col.controller_release(self._rel(0.0, 0.0, ReleaseReason.BUFFER_LOW))
        tm = col._queue[0]
        self.assertEqual(len(tm.controller_releases), 3)
        self.assertEqual(tm.controller_held_count, 1)          # only the 0.7 s one
        self.assertAlmostEqual(tm.controller_max_hold_s, 0.7)
        self.assertAlmostEqual(tm.controller_mean_hold_s, 0.7 / 3)
        d = tm.to_dict()["speech_continuity_controller"]
        self.assertEqual(d["release_count"], 3)
        self.assertEqual(d["held_count"], 1)
        self.assertEqual(d["releases"][1]["reason"], ReleaseReason.HOLD_EXPIRED)

    def test_no_releases_means_none_aggregates(self) -> None:
        col = MetricsCollector()
        col.start_turn(stt_result=0.0, stt_text="q")
        tm = col._queue[0]
        self.assertEqual(tm.controller_held_count, 0)
        self.assertIsNone(tm.controller_max_hold_s)
        self.assertEqual(tm.to_dict()["speech_continuity_controller"]["release_count"], 0)


if __name__ == "__main__":
    unittest.main()
