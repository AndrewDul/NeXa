"""M2.5B — integration tests for the *actual* barge-in wiring.

These do not test a component in isolation — they build the stack through
``nexa.voice_tts.build_bargein_stack`` (the SAME construction path
``apps/nexa_bilingual_voice_probe.py --bargein`` uses) and assert the wires
between `AecReferenceFeeder`, `AecReferenceHealth`, `HalfDuplexGate`,
`BargeInController`, `SpokenTextTracker`, and `VoiceConversationAdapter`.

Letters map to the audit checklist in the M2.5B wiring-audit request.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipecat.frames.frames import (  # noqa: E402
    Frame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from nexa.conversation import ConversationSession, InterruptedTurnOutcome, Role  # noqa: E402
from nexa.providers.base import (  # noqa: E402
    GenerationOptions,
    ModelProvider,
    ProviderDescription,
)
from nexa.voice_conversation.adapter import VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts import AecReferenceFeeder, build_bargein_stack  # noqa: E402


# ------------------------------------------------------------- fakes
class _FakeSink:
    def __init__(self, *, die_after: int | None = None) -> None:
        self.written = b""
        self.closed = False
        self._n = 0
        self._die = die_after

    @property
    def alive(self) -> bool:
        return not self.closed and (self._die is None or self._n < self._die)

    def write(self, pcm: bytes) -> None:
        if not self.alive:
            raise BrokenPipeError
        self._n += 1
        self.written += pcm

    def close(self) -> None:
        self.closed = True


class _ScriptedProvider(ModelProvider):
    def __init__(self, chunks, delay=0.02) -> None:
        self._chunks = chunks
        self._delay = delay
        self.cancel_observed = False

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="scripted", model="t")

    async def generate(self, messages, options, *, cancel_token=None):
        import threading
        import time as _t

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        done = object()

        def worker():
            try:
                for c in self._chunks:
                    for _ in range(max(1, int(self._delay / 0.005))):
                        if cancel_token is not None and cancel_token.is_cancelled:
                            self.cancel_observed = True
                            return
                        _t.sleep(0.005)
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, c)
                    except RuntimeError:
                        return
            finally:
                if cancel_token is not None and hasattr(cancel_token, 'mark_worker_stopped'):
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


def _stack(*, enabled=True, sink_factory=None, on_confirmed=lambda ctx: None,
           on_candidate=None):
    return build_bargein_stack(
        enabled=enabled, sample_rate=16000, channels=1,
        on_confirmed=on_confirmed, on_candidate=on_candidate,
        aec_sink_factory=sink_factory,
    )


class _Instr(unittest.IsolatedAsyncioTestCase):
    def _instrument(self, fp) -> list[Frame]:
        pushed: list[Frame] = []

        async def _push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        fp.push_frame = _push
        self._tasks = getattr(self, "_tasks", [])

        def _mk(coro, name=None, context=None):
            t = asyncio.ensure_future(coro)
            self._tasks.append(t)
            return t

        fp.create_task = _mk
        return pushed

    async def asyncTearDown(self) -> None:
        for t in getattr(self, "_tasks", []):
            if not t.done():
                t.cancel()


# ============================================================ A, B, C
class TestStackWiring(unittest.TestCase):
    def test_A_bargein_build_creates_exactly_one_aec_feeder(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        self.assertIsInstance(s.aec_feeder, AecReferenceFeeder)
        self.assertIsNotNone(s.controller)
        # disabled build: no feeder, no controller, plain gate
        d = _stack(enabled=False)
        self.assertIsNone(d.aec_feeder)
        self.assertIsNone(d.controller)
        self.assertFalse(d.gate.bargein_active)

    def test_B_one_shared_aec_health_object(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        self.assertIs(s.gate._aec, s.aec_health)          # noqa: SLF001
        self.assertIs(s.controller._aec, s.aec_health)    # noqa: SLF001
        self.assertIs(s.aec_feeder._health, s.aec_health)  # noqa: SLF001

    def test_C_feeder_is_after_piper_and_before_the_observer(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        bridge, planner, continuity, tts, observer = (
            object(), object(), object(), "PIPER", "OBS"
        )
        stages = s.output_stages([bridge, planner, continuity], tts, observer)
        self.assertEqual(stages[:3], [bridge, planner, continuity])
        self.assertLess(stages.index(tts), stages.index(s.aec_feeder))
        self.assertLess(stages.index(s.aec_feeder), stages.index(observer))

    def test_C_disabled_output_stages_have_no_feeder(self) -> None:
        d = _stack(enabled=False)
        stages = d.output_stages(["a", "b"], "PIPER", "OBS")
        self.assertEqual(stages, ["a", "b", "PIPER", "OBS"])


# ============================================================ D, E, F
class TestAecFeederThroughStack(_Instr):
    async def test_D_tts_audio_frame_reaches_the_feeder_sink(self) -> None:
        sinks: list[_FakeSink] = []
        s = _stack(enabled=True, sink_factory=lambda: sinks.append(_FakeSink()) or sinks[-1])
        self._instrument(s.aec_feeder)
        s.aec_feeder.begin()
        pcm = b"\x11\x22" * 160
        await s.aec_feeder.process_frame(
            TTSAudioRawFrame(pcm, 16000, 1), FrameDirection.DOWNSTREAM
        )
        await asyncio.sleep(0.05)
        self.assertEqual(sinks[0].written, pcm)
        self.assertGreaterEqual(s.aec_feeder.frames_mirrored, 1)
        await s.aec_feeder.end()

    async def test_E_healthy_feeder_makes_barge_in_safe_and_mic_hot(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        self._instrument(s.aec_feeder)
        s.aec_feeder.begin()
        self.assertTrue(s.aec_health.barge_in_safe)
        s.gate.notify_response_dispatched()
        self.assertFalse(s.gate.mic_suppressed)  # HOT
        await s.aec_feeder.end()
        self.assertFalse(s.aec_health.barge_in_safe)

    async def test_F_failed_feeder_returns_gate_to_r0026_suppression(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink(die_after=0))
        self._instrument(s.aec_feeder)
        s.aec_feeder.begin()
        # first write fails -> health.mark_failed
        await s.aec_feeder.process_frame(
            TTSAudioRawFrame(b"\x00" * 320, 16000, 1), FrameDirection.DOWNSTREAM
        )
        await asyncio.sleep(0.08)
        self.assertFalse(s.aec_health.barge_in_safe)
        self.assertGreaterEqual(s.aec_health.failure_count, 1)
        s.gate.notify_response_dispatched()
        self.assertTrue(s.gate.mic_suppressed)  # R0026 safe mode
        self.assertFalseIfAttr(s.controller)
        await s.aec_feeder.end()

    def assertFalseIfAttr(self, controller) -> None:
        # controller must refuse a candidate while the AEC ref is down
        self.assertEqual(controller.telemetry.candidate_started, 0)


# ============================================================ G, H
class TestSpokenTextWiringThroughStack(_Instr):
    async def test_G_synthesized_sentence_credits_the_active_response_once(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        rid = s.note_response_dispatched()
        s.note_tts_sentence("Czarna dziura to obszar.")
        s.note_tts_sentence("Powstaje z gwiazdy.")
        self.assertEqual(
            s.spoken_prefix_source(rid),
            "Czarna dziura to obszar. Powstaje z gwiazdy.",
        )

    async def test_H_stale_sentence_after_invalidation_is_not_credited(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        rid = s.note_response_dispatched()
        s.note_tts_sentence("Spoken before the cut.")
        # confirm an interruption directly on the state machine
        s.controller.state_machine.speech_started(now=1.0)
        s.controller.state_machine.poll(now=1.4)  # -> INTERRUPTING, id -> None
        self.assertIsNone(s.controller.active_response_id)
        # a straggler TTSTextFrame from the killed reply
        s.note_tts_sentence("STRAGGLER — must not be credited.")
        self.assertEqual(s.spoken_prefix_source(rid), "Spoken before the cut.")
        # the next reply starts clean
        new_rid = s.note_response_dispatched()
        self.assertNotEqual(new_rid, rid)
        s.note_tts_sentence("Fresh reply sentence.")
        self.assertEqual(s.spoken_prefix_source(new_rid), "Fresh reply sentence.")
        self.assertEqual(s.spoken_prefix_source(rid), "")  # old id no longer tracked


# ============================================================ I, J
class TestAdapterCommitThroughStack(unittest.IsolatedAsyncioTestCase):
    def _adapter(self, chunks, stack, *, delay=0.03):
        prov = _ScriptedProvider(chunks, delay=delay)
        sess = ConversationSession(
            provider=prov, system_prompt="s", options=GenerationOptions()
        )
        interrupts: list = []
        a = VoiceConversationAdapter(
            sess,
            on_user_transcript=lambda t: stack.note_response_dispatched(),
            on_turn_interrupted=interrupts.append,
            response_id_source=stack.response_id_source,
            spoken_prefix_source=stack.spoken_prefix_source,
            cancel_watch_timeout_s=1.0,
        )
        return a, sess, prov, interrupts

    async def test_I_interrupted_reply_commits_the_released_spoken_prefix(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        a, sess, prov, interrupts = self._adapter(
            ["Czarna ", "dziura ", "to ", "obszar."], s
        )
        task = asyncio.ensure_future(a._run_turn_inner("Opowiedz o czarnych dziurach"))  # noqa: SLF001
        await asyncio.sleep(0.04)
        # a sentence was released to TTS before the cut
        s.note_tts_sentence("Czarna dziura to obszar czasoprzestrzeni.")
        a.interrupt_active_turn()  # what BargeInController.on_confirmed calls
        await task
        prov.join_worker() if hasattr(prov, "join_worker") else None
        self.assertEqual(sess.history[-1].role, Role.ASSISTANT)
        self.assertTrue(sess.history[-1].interrupted)
        self.assertEqual(
            sess.history[-1].content, "Czarna dziura to obszar czasoprzestrzeni."
        )
        self.assertEqual(
            interrupts[0].outcome, InterruptedTurnOutcome.COMMITTED_SPOKEN_PREFIX.value
        )
        self.assertEqual(len(sess._history), len(sess._response_languages))  # noqa: SLF001

    async def test_J_think_window_interrupt_rolls_back_the_user_turn(self) -> None:
        s = _stack(enabled=True, sink_factory=lambda: _FakeSink())
        a, sess, prov, interrupts = self._adapter(["slow"], s, delay=0.25)
        task = asyncio.ensure_future(a._run_turn_inner("Tell me about X"))  # noqa: SLF001
        await asyncio.sleep(0.03)  # no sentence released yet
        a.interrupt_active_turn()
        await task
        self.assertEqual(list(sess.history), [])  # no orphan USER turn
        self.assertEqual(
            interrupts[0].outcome, InterruptedTurnOutcome.ROLLED_BACK_USER_TURN.value
        )


# ============================================================ probe path parity
class TestProbeUsesTheStackBuilder(unittest.TestCase):
    def test_probe_builds_barge_in_through_build_bargein_stack(self) -> None:
        src = (
            Path(__file__).resolve().parents[1]
            / "apps" / "nexa_bilingual_voice_probe.py"
        ).read_text(encoding="utf-8")
        self.assertIn("build_bargein_stack(", src)
        self.assertIn("stack.output_stages(", src)
        self.assertIn("stack.note_response_dispatched()", src)
        self.assertIn("stack.note_tts_sentence", src)
        self.assertIn("stack.note_interruption", src)
        self.assertIn("stack.response_id_source", src)
        self.assertIn("stack.spoken_prefix_source", src)
        # the old hand-rolled construction is gone
        self.assertNotIn("AecReferenceFeeder(", src)
        self.assertNotIn("BargeInController(\n", src)


if __name__ == "__main__":
    unittest.main()
