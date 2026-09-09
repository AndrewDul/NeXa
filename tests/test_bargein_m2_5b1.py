"""M2.5B.1 — interruption-utterance integrity + no-fragmentation + stress.

Reproduces and locks the live-acceptance bug: a confirmed barge-in whose
speech arrives as TWO VAD segments ("Czekaj." <pause> "tylko jak powstaje.")
must become ONE canonical user turn — segment A must NOT start a reply while
segment B is still being captured / transcribed, and segment B must NOT be
``DROP_BUSY``'d.

Offline, no audio hardware, no real model. Built through
``nexa.voice_tts.build_bargein_stack`` (the probe's construction path).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation import ConversationSession, Role  # noqa: E402
from nexa.providers.base import (  # noqa: E402
    GenerationOptions,
    ModelProvider,
    ProviderDescription,
)
from nexa.stt.bilingual import Language  # noqa: E402
from nexa.stt.transcriber import TranscriptionResult  # noqa: E402
from nexa.voice_conversation.adapter import (  # noqa: E402
    DROP_BUSY_RESPONSE_IN_FLIGHT,
    VoiceConversationAdapter,
)
from nexa.voice_tts import build_bargein_stack  # noqa: E402


class _Prov(ModelProvider):
    """Ollama-like: worker thread, per-chunk cancel-token check, marks the
    token when it observes the cancel and when it stops."""

    def __init__(self, chunks, delay=0.03) -> None:
        self._chunks = chunks
        self._delay = delay
        self.starts = 0

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="p", model="t")

    async def generate(self, messages, options, *, cancel_token=None):
        import threading
        import time as _t

        self.starts += 1
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        done = object()

        def worker() -> None:
            try:
                for c in self._chunks:
                    slept = 0.0
                    while slept < self._delay:
                        if cancel_token is not None and cancel_token.is_cancelled:
                            if hasattr(cancel_token, "mark_cancel_observed"):
                                cancel_token.mark_cancel_observed()
                            return
                        _t.sleep(0.005)
                        slept += 0.005
                    if cancel_token is not None and cancel_token.is_cancelled:
                        return
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, c)
                    except RuntimeError:
                        return
            finally:
                if cancel_token is not None and hasattr(cancel_token, "mark_worker_stopped"):
                    cancel_token.mark_worker_stopped()
                try:
                    loop.call_soon_threadsafe(q.put_nowait, done)
                except RuntimeError:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = await q.get()
            if item is done:
                return
            yield item


def _tr(text: str, lang: str = "pl") -> TranscriptionResult:
    return TranscriptionResult(
        text=text, language=Language(lang), audio_duration_s=1.0, wall_latency_s=0.1
    )


class _Fixture(unittest.IsolatedAsyncioTestCase):
    def _build(self, chunks=("odp1 ", "odp2 ", "odp3 ", "odp4"), *, settle=0.15,
               capture_timeout=3.0):
        prov = _Prov(list(chunks))
        sess = ConversationSession(
            provider=prov, system_prompt="s", options=GenerationOptions()
        )
        self.stack = build_bargein_stack(
            enabled=True, sample_rate=16000, channels=1,
            on_confirmed=lambda ctx: self.adapter.interrupt_active_turn(),
        )
        # shorten the settle window for the test
        self.stack.controller._settle_secs = settle  # noqa: SLF001
        # BargeInController is a bare FrameProcessor here (no pipeline task
        # manager) — give it a plain create_task.
        self._ctl_tasks: list = []

        def _mk(coro, name=None, context=None):
            t = asyncio.ensure_future(coro)
            self._ctl_tasks.append(t)
            return t

        self.stack.controller.create_task = _mk
        self.tokens: list[str] = []
        self.completes: list[str] = []
        self.dropped: list = []
        self.interrupts: list = []
        self.adapter = VoiceConversationAdapter(
            sess,
            on_assistant_token=self.tokens.append,
            on_assistant_complete=self.completes.append,
            on_turn_dropped=self.dropped.append,
            on_turn_interrupted=self.interrupts.append,
            on_user_transcript=lambda t: self.stack.note_response_dispatched(),
            response_id_source=self.stack.response_id_source,
            spoken_prefix_source=self.stack.spoken_prefix_source,
            interruption_complete_hook=self.stack.on_interruption_complete,
            interrupt_capture_timeout_s=capture_timeout,
        )
        self.stack.bind_adapter(self.adapter)
        self.adapter.start()
        self.sess = sess
        self.prov = prov
        return self.adapter

    async def asyncTearDown(self) -> None:
        for t in getattr(self, "_ctl_tasks", []):
            if not t.done():
                t.cancel()
        await self.adapter.shutdown()

    async def _confirm_barge_in(self):
        """Drive the controller to a confirmed interruption while a reply
        streams, exactly as VAD frames would."""
        ctl = self.stack.controller
        rid = ctl.notify_response_dispatched()  # the reply being interrupted
        self.adapter._active_response_id = rid  # noqa: SLF001
        # start a real streamed turn so there is something to cancel
        self._turn_task = asyncio.ensure_future(
            self.adapter._run_turn_inner(_tr("Opowiedz o czarnych dziurach"))  # noqa: SLF001
        )
        await asyncio.sleep(0.05)
        # segment 1 of the interruption: start (confirms), then it keeps going
        ctl._sm.speech_started(now=0.0)  # noqa: SLF001  RESPONDING -> CANDIDATE
        # confirm via poll
        ev = ctl._sm.poll(now=1.0)  # noqa: SLF001
        self.assertEqual(ev.value, "interrupt_confirmed")
        await ctl._do_confirm("sustained_vad")  # noqa: SLF001
        await self._turn_task  # the interrupted turn finishes its teardown


class TestNoFragmentation(_Fixture):
    async def test_1_and_5_split_A_B_becomes_ONE_canonical_turn(self) -> None:
        a = self._build()
        await self._confirm_barge_in()
        # segment 1 ("Czekaj.") ends -> STT result A owed
        self.stack.controller._sm.speech_stopped(now=2.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        self.assertTrue(a.capturing_interrupt)
        a.handle_transcription(_tr("Czekaj."))              # result A
        # NeXa must NOT have started a reply
        self.assertEqual(self.prov.starts, 1)  # only the (interrupted) original
        # segment 2 starts, then ends
        self.stack.controller._sm.speech_started(now=3.0)   # noqa: SLF001
        a.note_interrupt_segment_started()
        self.stack.controller._sm.speech_stopped(now=4.5)   # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr("Powiedz tylko jak powstaje."))  # result B
        # settle window elapses -> ONE turn dispatched
        a.note_interrupt_capture_settled()
        await asyncio.sleep(0.2)
        # exactly one replacement user turn, from BOTH segments
        self.assertEqual(a.coalesced_interrupt_turns, 1)
        user_turns = [t for t in self.sess.history if t.role == Role.USER]
        self.assertEqual(user_turns[-1].content, "Czekaj. Powiedz tylko jak powstaje.")
        # exactly one NEW provider call for the replacement reply
        self.assertEqual(self.prov.starts, 2)

    async def test_3_no_DROP_BUSY_for_a_segment_of_the_confirmed_interruption(self) -> None:
        a = self._build()
        await self._confirm_barge_in()
        self.stack.controller._sm.speech_stopped(now=2.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr("Sorry."))
        self.stack.controller._sm.speech_started(now=3.0)  # noqa: SLF001
        a.note_interrupt_segment_started()
        self.stack.controller._sm.speech_stopped(now=4.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr("I just meant colon."))
        a.note_interrupt_capture_settled()
        await asyncio.sleep(0.2)
        self.assertEqual(self.dropped, [])  # NOTHING dropped
        self.assertEqual(a.dropped_busy_turns, 0)

    async def test_4_unrelated_later_busy_speech_is_STILL_dropped(self) -> None:
        a = self._build(chunks=("r1 ", "r2 ", "r3 ", "r4 ", "r5 ", "r6"))
        await self._confirm_barge_in()
        self.stack.controller._sm.speech_stopped(now=2.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr("Krócej."))
        a.note_interrupt_capture_settled()
        await asyncio.sleep(0.15)  # the ONE coalesced turn dispatches + starts streaming
        self.assertTrue(a.turn_in_flight)
        # now a genuinely NEW utterance arrives mid-reply -> DROP_BUSY (normal)
        a.handle_transcription(_tr("Zupełnie nowe pytanie w trakcie."))
        self.assertEqual(len(self.dropped), 1)
        self.assertEqual(self.dropped[0].reason, DROP_BUSY_RESPONSE_IN_FLIGHT)

    async def test_5_exactly_one_user_turn_even_with_three_segments(self) -> None:
        a = self._build()
        await self._confirm_barge_in()
        for i, seg in enumerate(("No.", "I", "just meant this.")):
            if i > 0:
                self.stack.controller._sm.speech_started(now=10.0 + i)  # noqa: SLF001
                a.note_interrupt_segment_started()
            self.stack.controller._sm.speech_stopped(now=10.5 + i)  # noqa: SLF001
            a.note_interrupt_segment_ended()
            a.handle_transcription(_tr(seg, "en"))
        a.note_interrupt_capture_settled()
        await asyncio.sleep(0.2)
        self.assertEqual(a.coalesced_interrupt_turns, 1)
        user_turns = [t for t in self.sess.history if t.role == Role.USER]
        # the ORIGINAL "Opowiedz..." + exactly ONE coalesced interruption turn
        self.assertEqual(len(user_turns), 2)
        self.assertEqual(user_turns[-1].content, "No. I just meant this.")

    async def test_6_queues_and_in_flight_return_to_zero_after_the_interruption(self) -> None:
        a = self._build()
        await self._confirm_barge_in()
        self.stack.controller._sm.speech_stopped(now=2.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr("Czekaj."))
        a.note_interrupt_capture_settled()
        await asyncio.sleep(0.3)  # coalesced turn runs to completion
        self.assertEqual(a.conversation_queue_depth, 0)
        self.assertEqual(a.conversation_in_flight, 0)
        self.assertFalse(a.capturing_interrupt)
        self.assertFalse(a.turn_in_flight)
        self.assertLessEqual(a.max_observed_conversation_concurrency, 1)
        self.assertEqual(self.stack.controller.state_machine.state.value, "responding")

    async def test_capture_timeout_never_hangs(self) -> None:
        a = self._build(capture_timeout=0.3)
        await self._confirm_barge_in()
        self.stack.controller._sm.speech_stopped(now=2.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr("Czekaj."))
        # NEVER call note_interrupt_capture_settled — rely on the hard timeout
        await asyncio.sleep(0.6)
        self.assertFalse(a.capturing_interrupt)
        self.assertEqual(a.coalesced_interrupt_turns, 1)

    async def test_noise_only_interruption_produces_no_replacement_turn(self) -> None:
        a = self._build()
        await self._confirm_barge_in()
        self.stack.controller._sm.speech_stopped(now=2.0)  # noqa: SLF001
        a.note_interrupt_segment_ended()
        a.handle_transcription(_tr(""))  # empty transcript (cough / noise)
        a.note_interrupt_capture_settled()
        await asyncio.sleep(0.2)
        self.assertEqual(a.coalesced_interrupt_turns, 0)
        # the machine still returns to a clean state
        self.assertEqual(self.stack.controller.state_machine.state.value, "idle")


class TestRepeatedInterruptionStress(_Fixture):
    async def test_12_fifteen_interruptions_leave_no_accumulation(self) -> None:
        a = self._build(chunks=tuple(f"c{i} " for i in range(6)))
        ctl = self.stack.controller
        loop = asyncio.get_running_loop()
        base_tasks = len(asyncio.all_tasks(loop))
        first_latency = None
        for cycle in range(15):
            rid = ctl.notify_response_dispatched()
            a._active_response_id = rid  # noqa: SLF001
            t0 = loop.time()
            turn = asyncio.ensure_future(
                a._run_turn_inner(_tr(f"pytanie numer {cycle}"))  # noqa: SLF001
            )
            await asyncio.sleep(0.04)
            ctl._sm.speech_started(now=0.0)  # noqa: SLF001
            ctl._sm.poll(now=1.0)  # noqa: SLF001
            await ctl._do_confirm("sustained_vad")  # noqa: SLF001
            await turn
            ctl._sm.speech_stopped(now=2.0)  # noqa: SLF001
            a.note_interrupt_segment_ended()
            a.handle_transcription(_tr(f"krócej {cycle}"))
            a.note_interrupt_capture_settled()
            # let the coalesced turn run
            for _ in range(50):
                await asyncio.sleep(0.01)
                if not a.turn_in_flight and not a.capturing_interrupt:
                    break
            lat = loop.time() - t0
            if cycle == 0:
                first_latency = lat
            # per-cycle invariants
            self.assertEqual(a.conversation_queue_depth, 0)
            self.assertEqual(a.conversation_in_flight, 0)
            self.assertFalse(a.capturing_interrupt)
            self.assertFalse(a.turn_in_flight)
            self.assertLessEqual(a.max_observed_conversation_concurrency, 1)
        # monotonic response ids, exactly 15 coalesced turns
        self.assertEqual(a.coalesced_interrupt_turns, 15)
        self.assertEqual(a.interrupted_turns, 15)
        # no unbounded asyncio-task growth
        self.assertLessEqual(len(asyncio.all_tasks(loop)) - base_tasks, 3)
        # no progressive latency explosion (fake model: cycles are ~equal)
        self.assertLess(lat, first_latency * 3 + 0.5)


if __name__ == "__main__":
    unittest.main()
