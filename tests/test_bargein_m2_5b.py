"""M2.5B — deterministic tests for production barge-in / interruption.

Offline, no audio hardware, no real model, no network. Numbered to the
R0029 acceptance matrix where they map 1:1; the live probe covers the
cases that need real Piper / a person (16-deep, 17-deep, 24/25 audible).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation import (  # noqa: E402
    ConversationSession,
    InterruptedTurnOutcome,
    ResponseMode,
    Role,
)
from nexa.conversation.context import (  # noqa: E402
    INTERRUPTED_WIRE_SUFFIX,
    ConversationContext,
)
from nexa.providers.base import (  # noqa: E402
    GenerationOptions,
    ModelProvider,
    ProviderDescription,
)
from nexa.voice.aec import AecReferenceHealth  # noqa: E402
from nexa.voice.gate import HalfDuplexGate  # noqa: E402
from nexa.voice.interruption import (  # noqa: E402
    InterruptionEvent,
    InterruptionState,
    InterruptionStateMachine,
)
from nexa.voice_conversation.adapter import VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts.spoken_text import SpokenTextTracker  # noqa: E402


# ============================================================ helpers
class _ScriptedProvider(ModelProvider):
    """Models the real Ollama provider: a worker THREAD produces chunks into
    a queue and checks ``cancel_token.is_cancelled`` between chunks; the
    async ``generate`` just drains the queue. So cancelling the *consumer*
    task does not stop the worker — only the token does (R0028)."""

    _DONE = object()

    def __init__(self, chunks: list[str], *, per_chunk_delay: float = 0.02) -> None:
        self._chunks = chunks
        self._delay = per_chunk_delay
        self.cancel_observed = False
        self._thread = None

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="scripted", model="test")

    def join_worker(self, timeout: float = 1.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    async def generate(self, messages, options, *, cancel_token=None):
        import threading
        import time as _t

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _post(item) -> None:
            try:
                loop.call_soon_threadsafe(q.put_nowait, item)
            except RuntimeError:
                pass  # loop closed (test torn down) — nothing to deliver

        def worker() -> None:
            try:
                for c in self._chunks:
                    # poll the token frequently (like Ollama's per-line check)
                    slept = 0.0
                    while slept < self._delay:
                        if cancel_token is not None and cancel_token.is_cancelled:
                            self.cancel_observed = True
                            return
                        _t.sleep(0.005)
                        slept += 0.005
                    if cancel_token is not None and cancel_token.is_cancelled:
                        self.cancel_observed = True
                        return
                    _post(c)
            finally:
                _post(self._DONE)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        while True:
            item = await q.get()
            if item is self._DONE:
                return
            yield item


def _session(chunks: list[str], **kw) -> tuple[ConversationSession, _ScriptedProvider]:
    p = _ScriptedProvider(chunks, **kw)
    return (
        ConversationSession(provider=p, system_prompt="sys", options=GenerationOptions()),
        p,
    )


# ============================================================ 3-6, 29: state machine
class TestInterruptionStateMachine(unittest.TestCase):
    def test_case3_short_speech_then_stop_rejects_candidate(self) -> None:
        sm = InterruptionStateMachine(confirm_hold_secs=0.3)
        sm.notify_response_dispatched()
        self.assertEqual(sm.speech_started(now=10.0), InterruptionEvent.CANDIDATE_STARTED)
        self.assertEqual(sm.state, InterruptionState.INTERRUPT_CANDIDATE)
        # stop after 0.1 s — before the 0.3 s hold
        self.assertEqual(sm.speech_stopped(now=10.1), InterruptionEvent.CANDIDATE_REJECTED)
        self.assertEqual(sm.state, InterruptionState.RESPONDING)
        self.assertEqual(sm.rejected_candidates, 1)
        self.assertEqual(sm.poll(now=10.5), InterruptionEvent.NONE)  # nothing to confirm

    def test_case4_sustained_speech_confirms_exactly_once(self) -> None:
        sm = InterruptionStateMachine(confirm_hold_secs=0.3)
        rid = sm.notify_response_dispatched() or sm.active_response_id
        sm.speech_started(now=10.0)
        self.assertEqual(sm.poll(now=10.29), InterruptionEvent.NONE)  # not yet
        self.assertEqual(sm.poll(now=10.30), InterruptionEvent.INTERRUPT_CONFIRMED)
        self.assertEqual(sm.state, InterruptionState.INTERRUPTING)
        self.assertEqual(sm.confirmed_interruptions, 1)
        self.assertIsNone(sm.active_response_id)  # the old id is invalidated
        self.assertFalse(sm.is_current_response(rid))
        self.assertEqual(sm.poll(now=10.9), InterruptionEvent.NONE)  # idempotent

    def test_case5_second_speech_start_during_candidate_is_ignored(self) -> None:
        sm = InterruptionStateMachine(confirm_hold_secs=0.3)
        sm.notify_response_dispatched()
        sm.speech_started(now=10.0)
        self.assertEqual(sm.speech_started(now=10.1), InterruptionEvent.IGNORED_SPEECH)
        self.assertEqual(sm.ignored_speech_starts, 1)
        self.assertEqual(sm.state, InterruptionState.INTERRUPT_CANDIDATE)
        self.assertEqual(sm.candidate_started_at, 10.0)  # unchanged

    def test_case6_second_speech_start_during_interrupting_is_ignored(self) -> None:
        sm = InterruptionStateMachine(confirm_hold_secs=0.3)
        sm.notify_response_dispatched()
        sm.speech_started(now=10.0)
        sm.poll(now=10.3)  # -> INTERRUPTING
        self.assertEqual(sm.speech_started(now=10.4), InterruptionEvent.IGNORED_SPEECH)
        self.assertEqual(sm.speech_started(now=10.5), InterruptionEvent.IGNORED_SPEECH)
        self.assertEqual(sm.ignored_speech_starts, 2)

    def test_case29_nested_interruption_gets_a_fresh_monotonic_id(self) -> None:
        sm = InterruptionStateMachine(confirm_hold_secs=0.3)
        sm.notify_response_dispatched()
        first = sm.active_response_id
        sm.speech_started(now=1.0)
        sm.poll(now=1.3)  # confirm #1
        sm.notify_interruption_complete()
        sm.notify_response_dispatched()  # the interrupting turn's own reply
        second = sm.active_response_id
        self.assertEqual(second, first + 1)
        sm.speech_started(now=5.0)
        self.assertEqual(sm.poll(now=5.3), InterruptionEvent.INTERRUPT_CONFIRMED)
        self.assertEqual(sm.confirmed_interruptions, 2)

    def test_speech_while_idle_is_a_noop(self) -> None:
        sm = InterruptionStateMachine()
        self.assertEqual(sm.speech_started(now=1.0), InterruptionEvent.NONE)
        self.assertEqual(sm.state, InterruptionState.IDLE)

    def test_normal_finish_returns_to_idle_without_interrupting(self) -> None:
        sm = InterruptionStateMachine()
        sm.notify_response_dispatched()
        self.assertEqual(sm.notify_response_finished(), InterruptionEvent.RESPONSE_FINISHED)
        self.assertEqual(sm.state, InterruptionState.IDLE)
        self.assertEqual(sm.confirmed_interruptions, 0)

    def test_reset_clears_any_latched_state(self) -> None:
        sm = InterruptionStateMachine()
        sm.notify_response_dispatched()
        sm.speech_started(now=1.0)
        sm.reset()
        self.assertEqual(sm.state, InterruptionState.IDLE)
        self.assertIsNone(sm.active_response_id)


# ============================================================ 1, 2, 7, 28: gate
class TestHalfDuplexGateBargeIn(unittest.TestCase):
    def test_case28_default_gate_is_byte_for_byte_r0026(self) -> None:
        g = HalfDuplexGate()  # bargein_enabled defaults False
        self.assertFalse(g.bargein_active)
        g.notify_response_dispatched()
        self.assertTrue(g.mic_suppressed)  # whole-response suppression (R0026)
        self.assertTrue(g.should_drop_busy_utterance())

    def test_case1_bargein_active_keeps_mic_hot_during_a_reply(self) -> None:
        aec = AecReferenceHealth()
        aec.mark_started()
        g = HalfDuplexGate(bargein_enabled=True, aec_health=aec)
        g.notify_response_dispatched()
        self.assertTrue(g.bargein_active)
        self.assertFalse(g.mic_suppressed)  # HOT — the operator can interrupt

    def test_case2_aec_reference_down_falls_back_to_r0026_suppression(self) -> None:
        aec = AecReferenceHealth()  # never started -> not safe
        g = HalfDuplexGate(bargein_enabled=True, aec_health=aec)
        g.notify_response_dispatched()
        self.assertFalse(g.bargein_active)
        self.assertTrue(g.mic_suppressed)  # UNSAFE hot mic avoided
        aec.mark_started()
        self.assertFalse(g.mic_suppressed)  # reference back -> hot again
        aec.mark_failed()
        self.assertTrue(g.mic_suppressed)  # died mid-response -> safe mode
        self.assertEqual(aec.failure_count, 1)

    def test_case7_admit_one_utterance_lets_exactly_the_interruption_through(self) -> None:
        aec = AecReferenceHealth()
        aec.mark_started()
        g = HalfDuplexGate(bargein_enabled=True, aec_health=aec)
        g.notify_response_dispatched()
        self.assertTrue(g.response_in_flight)
        g.admit_next_utterance()
        self.assertFalse(g.should_drop_busy_utterance())  # the interruption passes
        # one-shot consumed: the next busy utterance is dropped again
        self.assertTrue(g.should_drop_busy_utterance())


# ============================================================ AEC health
class TestAecReferenceHealth(unittest.TestCase):
    def test_lifecycle_and_failure_counting(self) -> None:
        h = AecReferenceHealth()
        self.assertFalse(h.barge_in_safe)
        h.mark_started()
        self.assertTrue(h.barge_in_safe)
        h.mark_failed()
        self.assertFalse(h.barge_in_safe)
        self.assertEqual(h.failure_count, 1)
        h.mark_started()
        h.mark_stopped()  # a normal shutdown is NOT a failure
        self.assertEqual(h.failure_count, 1)
        self.assertFalse(h.barge_in_safe)


# ============================================================ 19: spoken-text high-water mark
class TestSpokenTextTracker(unittest.TestCase):
    def test_accumulates_synthesized_sentences_for_the_active_reply(self) -> None:
        t = SpokenTextTracker()
        t.start_response(7)
        t.add_synthesized_sentence(7, "Czarna dziura to obszar.")
        t.add_synthesized_sentence(7, "Powstaje z gwiazdy.")
        self.assertEqual(
            t.spoken_prefix(7), "Czarna dziura to obszar. Powstaje z gwiazdy."
        )

    def test_late_sentence_from_an_invalidated_reply_is_dropped(self) -> None:
        t = SpokenTextTracker()
        t.start_response(7)
        t.add_synthesized_sentence(7, "Spoken.")
        t.start_response(8)  # new reply after an interruption
        t.add_synthesized_sentence(7, "Straggler from the killed reply.")  # dropped
        self.assertEqual(t.spoken_prefix(8), "")
        self.assertEqual(t.spoken_prefix(7), "")  # not the active id


# ============================================================ 18-21, 27: interrupted history
class TestCommitInterruptedTurn(unittest.IsolatedAsyncioTestCase):
    async def test_case18_think_window_rollback_no_consecutive_user_turns(self) -> None:
        s, _ = _session(["never", "reaches"])
        # emulate send() cut before any chunk: append user turn, do not stream
        s._history.append(  # noqa: SLF001
            __import__("nexa.conversation.turn", fromlist=["ConversationTurn"]).ConversationTurn(
                role=Role.USER, content="Tell me about black holes."
            )
        )
        s._response_languages.append("en")  # noqa: SLF001
        out = s.commit_interrupted_turn("")
        self.assertEqual(out, InterruptedTurnOutcome.ROLLED_BACK_USER_TURN)
        self.assertEqual(len(s.history), 0)
        self.assertEqual(len(s._response_languages), 0)  # noqa: SLF001  aligned

    async def test_case19_spoken_prefix_commits_interrupted_assistant_turn(self) -> None:
        s, _ = _session(["one two ", "three four"])
        s._history.append(  # noqa: SLF001
            __import__("nexa.conversation.turn", fromlist=["ConversationTurn"]).ConversationTurn(
                role=Role.USER, content="q"
            )
        )
        s._response_languages.append(None)  # noqa: SLF001
        out = s.commit_interrupted_turn("Czarna dziura to obszar.")
        self.assertEqual(out, InterruptedTurnOutcome.COMMITTED_SPOKEN_PREFIX)
        self.assertEqual(s.history[-1].role, Role.ASSISTANT)
        self.assertTrue(s.history[-1].interrupted)
        self.assertEqual(s.history[-1].content, "Czarna dziura to obszar.")  # clean prefix
        self.assertEqual(len(s._history), len(s._response_languages))  # noqa: SLF001

    async def test_case20_no_double_commit_when_reply_already_finished(self) -> None:
        s, _ = _session(["done"])
        async for _ in s.send("q", response_mode=ResponseMode.VOICE):
            pass
        n = len(s.history)
        out = s.commit_interrupted_turn("done")
        self.assertEqual(out, InterruptedTurnOutcome.NOTHING_TO_COMMIT)
        self.assertEqual(len(s.history), n)  # unchanged

    def test_case21_wire_suffix_only_on_interrupted_assistant_turn(self) -> None:
        Turn = __import__(
            "nexa.conversation.turn", fromlist=["ConversationTurn"]
        ).ConversationTurn
        ctx = ConversationContext(
            system_prompt="s",
            turns=(
                Turn(role=Role.USER, content="hi"),
                Turn(role=Role.ASSISTANT, content="Partial answer", interrupted=True),
                Turn(role=Role.USER, content="go on"),
            ),
        )
        msgs = ctx.to_provider_messages()
        asst = [m for m in msgs if m.role == "assistant"][0]
        self.assertEqual(asst.content, "Partial answer" + INTERRUPTED_WIRE_SUFFIX)
        # a normal assistant turn is untouched
        ctx2 = ConversationContext(
            system_prompt="s",
            turns=(Turn(role=Role.ASSISTANT, content="Full answer"),),
        )
        self.assertEqual(
            [m for m in ctx2.to_provider_messages() if m.role == "assistant"][0].content,
            "Full answer",
        )

    def test_case27_typed_chat_send_is_unchanged_by_the_interrupted_field(self) -> None:
        # a plain TEXT send never sets interrupted; wire output identical to pre-M2.5B
        Turn = __import__(
            "nexa.conversation.turn", fromlist=["ConversationTurn"]
        ).ConversationTurn
        t = Turn(role=Role.ASSISTANT, content="x")
        self.assertFalse(t.interrupted)
        ctx = ConversationContext(system_prompt="s", turns=(t,))
        self.assertEqual(
            [m for m in ctx.to_provider_messages() if m.role == "assistant"][0].content, "x"
        )


# ============================================================ 7,10,11,12,19,20,26: adapter
class TestAdapterInterruption(unittest.IsolatedAsyncioTestCase):
    def _adapter(self, chunks, *, bargein: bool, spoken=None, delay=0.02):
        s, p = _session(chunks, per_chunk_delay=delay)
        rid_box = {"v": 0}
        tokens: list[str] = []
        completes: list[str] = []
        interrupts: list = []

        def rid_source():
            rid_box["v"] += 1
            return rid_box["v"]

        a = VoiceConversationAdapter(
            s,
            on_assistant_token=tokens.append,
            on_assistant_complete=completes.append,
            on_turn_interrupted=interrupts.append,
            response_id_source=(rid_source if bargein else None),
            spoken_prefix_source=(spoken if bargein else None),
        )
        return a, s, p, tokens, completes, interrupts

    async def test_case26_bargein_off_path_is_the_pre_m2_5b_path(self) -> None:
        a, s, p, tokens, completes, interrupts = self._adapter(
            ["hel", "lo"], bargein=False
        )
        await a._run_turn_inner("Cześć")  # noqa: SLF001
        self.assertEqual("".join(tokens), "hello")
        self.assertEqual(completes, ["hello"])
        self.assertEqual(interrupts, [])
        self.assertEqual(s.history[-1].role, Role.ASSISTANT)
        self.assertFalse(s.history[-1].interrupted)
        self.assertIsNone(a.active_response_id)

    async def test_case11_12_interrupt_cancels_stream_and_skips_send_append(self) -> None:
        a, s, p, tokens, completes, interrupts = self._adapter(
            ["Czarna ", "dziura ", "to ", "obszar ", "czasoprzestrzeni."],
            bargein=True,
            spoken=lambda rid: "Czarna dziura to obszar.",
        )
        task = asyncio.ensure_future(a._run_turn_inner("Opowiedz o czarnych dziurach"))  # noqa: SLF001
        await asyncio.sleep(0.05)  # let a couple of chunks stream
        did = a.interrupt_active_turn()
        self.assertTrue(did)
        await task
        p.join_worker()  # case 30 — the worker thread exits, no leak
        self.assertTrue(p.cancel_observed)  # provider saw the cancel token (case 11)
        # send() did NOT append its own assistant turn; we committed the prefix
        self.assertEqual(s.history[-1].role, Role.ASSISTANT)
        self.assertTrue(s.history[-1].interrupted)  # case 19
        self.assertEqual(s.history[-1].content, "Czarna dziura to obszar.")
        self.assertEqual(completes, [])  # not a normal completion
        self.assertEqual(len(interrupts), 1)
        self.assertEqual(
            interrupts[0].outcome, InterruptedTurnOutcome.COMMITTED_SPOKEN_PREFIX.value
        )
        self.assertTrue(interrupts[0].llm_cancel_completed)
        self.assertEqual(a.interrupted_turns, 1)

    async def test_case18_adapter_think_window_interrupt_rolls_back_user_turn(self) -> None:
        a, s, p, tokens, completes, interrupts = self._adapter(
            ["slow"], bargein=True, spoken=lambda rid: "", delay=0.2
        )
        task = asyncio.ensure_future(a._run_turn_inner("Tell me about X"))  # noqa: SLF001
        await asyncio.sleep(0.03)  # still "thinking" — no chunk delivered yet
        a.interrupt_active_turn()
        await task
        self.assertEqual(list(s.history), [])  # user turn rolled back (no orphan)
        self.assertEqual(
            interrupts[0].outcome, InterruptedTurnOutcome.ROLLED_BACK_USER_TURN.value
        )

    async def test_case10_double_interrupt_call_is_idempotent(self) -> None:
        a, s, p, *_ = self._adapter(["a ", "b ", "c"], bargein=True, spoken=lambda r: "a")
        task = asyncio.ensure_future(a._run_turn_inner("q"))  # noqa: SLF001
        await asyncio.sleep(0.03)
        self.assertTrue(a.interrupt_active_turn())
        self.assertFalse(a.interrupt_active_turn())  # second call: nothing to do
        await task

    async def test_provider_error_still_reaches_on_conversation_error(self) -> None:
        class _Boom(ModelProvider):
            def describe(self):
                return ProviderDescription(provider_name="boom", model="x")

            async def generate(self, m, o, *, cancel_token=None):
                raise RuntimeError("provider down")
                yield  # pragma: no cover

        s = ConversationSession(provider=_Boom(), system_prompt="s")
        errs: list = []
        a = VoiceConversationAdapter(s, on_conversation_error=errs.append)
        await a._run_turn_inner("q")  # noqa: SLF001
        self.assertEqual(len(errs), 1)


# ============================================================ 8, 9: queue safety
class TestQueueSafetyAfterInterruption(unittest.IsolatedAsyncioTestCase):
    async def test_case8_9_exactly_one_interrupt_utterance_queues_are_clean(self) -> None:
        s, p = _session(["a ", "b ", "c ", "d ", "e"], per_chunk_delay=0.02)
        a = VoiceConversationAdapter(
            s,
            response_id_source=lambda: 1,
            spoken_prefix_source=lambda rid: "a b",
        )
        a.start()
        try:
            a.handle_transcription("Pierwsze pytanie")
            await asyncio.sleep(0.05)
            self.assertTrue(a.turn_in_flight)
            # a second utterance arrives mid-reply -> DROP_BUSY, never queued
            a.handle_transcription("Drugie w trakcie")
            a.handle_transcription("Trzecie w trakcie")
            self.assertEqual(a.conversation_queue_depth, 0)
            self.assertGreaterEqual(a.dropped_busy_turns, 2)
            # now interrupt
            a.interrupt_active_turn()
            await asyncio.sleep(0.1)
            self.assertFalse(a.turn_in_flight)
            self.assertEqual(a.conversation_queue_depth, 0)  # clean
            self.assertLessEqual(a.max_observed_conversation_concurrency, 1)
        finally:
            await a.shutdown()


# ============================================================ 22, 23, 24, 25: bilingual survives
class TestBilingualSurvivesInterruption(unittest.IsolatedAsyncioTestCase):
    async def test_case22_23_one_turn_override_does_not_leak_sticky_survives(self) -> None:
        from nexa.conversation import ResponseLanguageResolver

        resolver = ResponseLanguageResolver()
        s, p = _session(["ok"], per_chunk_delay=0.05)
        langs: list = []
        a = VoiceConversationAdapter(
            s,
            response_language_resolver=resolver,
            on_turn_language=langs.append,
            response_id_source=lambda: 1,
            spoken_prefix_source=lambda rid: "",
        )
        # turn 1: sticky switch to EN
        await a._run_turn_inner("From now on speak English.")  # noqa: SLF001
        self.assertEqual(langs[-1].sticky_preference, "en")
        # turn 2: a one-turn override to PL, interrupted mid-stream
        s2, p2 = _session(["a ", "b ", "c"], per_chunk_delay=0.05)
        a._session = s2  # noqa: SLF001
        task = asyncio.ensure_future(
            a._run_turn_inner("Odpowiedz po polsku.")  # noqa: SLF001  one-turn override
        )
        await asyncio.sleep(0.06)
        a.interrupt_active_turn()
        await task
        self.assertEqual(langs[-1].response_language, "pl")  # this turn only
        # turn 3: plain -> sticky EN still in force, override did NOT leak
        s3, p3 = _session(["y"], per_chunk_delay=0.0)
        a._session = s3  # noqa: SLF001
        await a._run_turn_inner("Co u ciebie?")  # noqa: SLF001
        self.assertEqual(langs[-1].response_language, "en")
        self.assertEqual(langs[-1].sticky_preference, "en")


# ============================================================ 1,2,5,6: BargeInController
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    StartFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from nexa.voice.bargein import BargeInController, InterruptContext  # noqa: E402
from nexa.voice_tts.aec_reference import AecReferenceFeeder  # noqa: E402
from nexa.voice_tts.bridge import AssistantSpeechBridge  # noqa: E402


class _FpHarness(unittest.IsolatedAsyncioTestCase):
    """Drives a bare FrameProcessor without a real pipeline: patched
    push_frame / create_task, frames fed straight to process_frame."""

    def _instrument(self, fp):
        pushed: list[Frame] = []

        async def _push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        fp.push_frame = _push
        tasks: list[asyncio.Task] = []

        def _mk_task(coro, name=None, context=None):
            t = asyncio.ensure_future(coro)
            tasks.append(t)
            return t

        fp.create_task = _mk_task
        self._tasks = getattr(self, "_tasks", []) + tasks
        return pushed

    async def asyncTearDown(self) -> None:
        for t in getattr(self, "_tasks", []):
            if not t.done():
                t.cancel()


class TestBargeInController(_FpHarness):
    def _controller(self, *, aec_ok=True, hold=0.3):
        aec = AecReferenceHealth()
        if aec_ok:
            aec.mark_started()
        self.clock = {"t": 100.0}
        confirmed: list[InterruptContext] = []
        candidates: list = []
        rejects: list[int] = []
        c = BargeInController(
            aec_health=aec,
            on_confirmed=confirmed.append,
            on_candidate=candidates.append,
            on_candidate_rejected=lambda: rejects.append(1),
            confirm_hold_secs=hold,
            time_source=lambda: self.clock["t"],
        )
        broadcasts: list[int] = []

        async def _bcast():
            broadcasts.append(1)

        c.broadcast_interruption = _bcast
        pushed = self._instrument(c)
        return c, aec, confirmed, candidates, rejects, broadcasts, pushed

    async def _f(self, c, frame):
        await c.process_frame(frame, FrameDirection.DOWNSTREAM)

    async def test_case7_nexa_speaks_operator_silent_zero_candidates(self) -> None:
        c, *_rest, broadcasts, _ = self._controller()
        confirmed = _rest[1]
        await self._f(c, StartFrame())
        c.notify_response_dispatched()
        # no VAD frames at all — just time passing and unrelated frames
        for _ in range(5):
            self.clock["t"] += 1.0
            await self._f(c, LLMTextFrame("blah"))
        self.assertEqual(c.telemetry.candidate_started, 0)
        self.assertEqual(confirmed, [])
        self.assertEqual(broadcasts, [])

    async def test_case4_sustained_vad_confirms_and_broadcasts_once(self) -> None:
        c, aec, confirmed, candidates, rejects, broadcasts, _ = self._controller()
        await self._f(c, StartFrame())
        rid = c.notify_response_dispatched()
        await self._f(c, VADUserStartedSpeakingFrame())
        self.assertEqual(candidates, [rid])
        self.assertEqual(c.telemetry.candidate_started, 1)
        self.clock["t"] += 0.3  # hold elapsed
        await self._f(c, LLMTextFrame("tick"))  # any frame drives poll()
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].invalidated_response_id, rid)
        self.assertEqual(confirmed[0].reason, "sustained_vad")
        self.assertEqual(broadcasts, [1])
        self.assertEqual(c.telemetry.interrupt_confirmed, 1)
        # idempotent — no second confirm/broadcast
        self.clock["t"] += 1.0
        await self._f(c, LLMTextFrame("tick"))
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(broadcasts, [1])

    async def test_case3_short_vad_then_stop_rejects_no_broadcast(self) -> None:
        c, aec, confirmed, candidates, rejects, broadcasts, _ = self._controller()
        await self._f(c, StartFrame())
        c.notify_response_dispatched()
        await self._f(c, VADUserStartedSpeakingFrame())
        self.clock["t"] += 0.1
        await self._f(c, VADUserStoppedSpeakingFrame())
        self.assertEqual(rejects, [1])
        self.assertEqual(c.telemetry.candidate_rejected, 1)
        self.clock["t"] += 1.0
        await self._f(c, LLMTextFrame("x"))
        self.assertEqual(confirmed, [])
        self.assertEqual(broadcasts, [])

    async def test_case5_6_single_candidate_invariant(self) -> None:
        c, aec, confirmed, candidates, rejects, broadcasts, _ = self._controller()
        await self._f(c, StartFrame())
        c.notify_response_dispatched()
        await self._f(c, VADUserStartedSpeakingFrame())
        await self._f(c, VADUserStartedSpeakingFrame())  # during CANDIDATE
        await self._f(c, VADUserStartedSpeakingFrame())
        self.assertEqual(c.telemetry.candidate_started, 1)
        self.assertGreaterEqual(c.telemetry.ignored_speech_starts, 2)
        self.clock["t"] += 0.3
        await self._f(c, LLMTextFrame("x"))  # confirm
        await self._f(c, VADUserStartedSpeakingFrame())  # during INTERRUPTING
        self.assertEqual(len(confirmed), 1)

    async def test_case2_aec_reference_down_ignores_the_interruption(self) -> None:
        c, aec, confirmed, candidates, rejects, broadcasts, _ = self._controller(
            aec_ok=False
        )
        await self._f(c, StartFrame())
        c.notify_response_dispatched()
        await self._f(c, VADUserStartedSpeakingFrame())
        self.clock["t"] += 1.0
        await self._f(c, LLMTextFrame("x"))
        self.assertEqual(c.telemetry.candidate_started, 0)
        self.assertGreaterEqual(c.telemetry.unsafe_speech_ignored, 1)
        self.assertEqual(confirmed, [])
        self.assertEqual(broadcasts, [])

    async def test_end_frame_resets_state(self) -> None:
        c, *_ = self._controller()
        await self._f(c, StartFrame())
        c.notify_response_dispatched()
        await self._f(c, VADUserStartedSpeakingFrame())
        await self._f(c, EndFrame())
        self.assertEqual(c.state_machine.state.value, "idle")


# ============================================================ 13: bridge InterruptionFrame drain
class TestAssistantSpeechBridgeInterruption(_FpHarness):
    async def test_case13_interruption_frame_drains_queued_llm_frames(self) -> None:
        interruptions: list[int] = []
        b = AssistantSpeechBridge(
            en_voice="en", pl_voice="pl",
            on_interruption=lambda: interruptions.append(1),
        )
        self._instrument(b)
        # simulate queued-but-not-yet-pushed LLM frames from the killed reply
        b._queue.put_nowait(LLMTextFrame("stale one "))  # noqa: SLF001
        b._queue.put_nowait(LLMTextFrame("stale two"))  # noqa: SLF001
        b._queue.put_nowait(LLMFullResponseEndFrame())  # noqa: SLF001
        await b.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(b._queue.qsize(), 0)  # noqa: SLF001  drained
        self.assertEqual(b.interrupted_frames_dropped, 3)
        self.assertEqual(interruptions, [1])


# ============================================================ 14: planner resets on interruption
class TestPlannerResetsOnInterruption(_FpHarness):
    async def test_case14_planner_clears_partial_buffer_on_interruption(self) -> None:
        from nexa.voice_tts.speech_planner import NexaSpeechPlanner

        p = NexaSpeechPlanner(en_voice="en", pl_voice="pl")
        pushed = self._instrument(p)
        await p.process_frame(LLMTextFrame("Czarna dziura to obszar "), FrameDirection.DOWNSTREAM)
        await p.process_frame(LLMTextFrame("czasoprzestrzeni bez końca"), FrameDirection.DOWNSTREAM)
        await p.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        # after the interruption a fresh response starts clean — no leaked tail
        await p.process_frame(LLMTextFrame("Nowe zdanie."), FrameDirection.DOWNSTREAM)
        await p.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        spoken = "".join(
            getattr(f, "text", "") for f in pushed if type(f).__name__ == "AggregatedTextFrame"
        )
        self.assertNotIn("Czarna dziura", spoken)
        self.assertIn("Nowe zdanie", spoken)


# ============================================================ 17: AEC reference feeder health
class _FakeSink:
    def __init__(self, *, die_after: int | None = None) -> None:
        self.written = b""
        self.closed = False
        self._n = 0
        self._die_after = die_after

    @property
    def alive(self) -> bool:
        return not self.closed and (self._die_after is None or self._n < self._die_after)

    def write(self, pcm: bytes) -> None:
        if not self.alive:
            raise BrokenPipeError("sink dead")
        self._n += 1
        self.written += pcm

    def close(self) -> None:
        self.closed = True


class TestAecReferenceFeeder(_FpHarness):
    async def _feeder(self, **kw):
        health = AecReferenceHealth()
        sinks: list[_FakeSink] = []

        def factory():
            s = _FakeSink(**kw)
            sinks.append(s)
            return s

        f = AecReferenceFeeder(
            aec_health=health, sample_rate=16000, sink_factory=factory
        )
        self._instrument(f)
        f.begin()
        return f, health, sinks

    async def test_case17_healthy_feed_mirrors_pcm_and_marks_active(self) -> None:
        f, health, sinks = await self._feeder()
        self.assertTrue(health.barge_in_safe)
        pcm = b"\x01\x02" * 160
        await f.process_frame(TTSAudioRawFrame(pcm, 16000, 1), FrameDirection.DOWNSTREAM)
        await f.process_frame(TTSAudioRawFrame(pcm, 16000, 1), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)  # let the writer task drain
        self.assertEqual(sinks[0].written, pcm * 2)
        self.assertEqual(f.frames_mirrored, 2)
        await f.end()
        self.assertFalse(health.barge_in_safe)  # stopped on shutdown

    async def test_case17_sink_death_marks_unsafe_and_attempts_respawn(self) -> None:
        f, health, sinks = await self._feeder(die_after=1)
        pcm = b"\x00" * 320
        await f.process_frame(TTSAudioRawFrame(pcm, 16000, 1), FrameDirection.DOWNSTREAM)
        await f.process_frame(TTSAudioRawFrame(pcm, 16000, 1), FrameDirection.DOWNSTREAM)
        await f.process_frame(TTSAudioRawFrame(pcm, 16000, 1), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.08)
        self.assertGreaterEqual(health.failure_count, 1)
        # a respawn was attempted (a second sink was created)
        self.assertGreaterEqual(len(sinks), 2)
        await f.end()

    async def test_frames_are_always_forwarded(self) -> None:
        f, health, sinks = await self._feeder()
        pushed = f.push_frame.__self__ if False else None  # noqa: F841
        captured: list = []
        f.push_frame = lambda fr, d=FrameDirection.DOWNSTREAM: captured.append(fr) or _noop()
        await f.process_frame(LLMTextFrame("x"), FrameDirection.DOWNSTREAM)
        self.assertTrue(any(isinstance(x, LLMTextFrame) for x in captured))
        await f.end()


async def _noop():
    return None


if __name__ == "__main__":
    unittest.main()
