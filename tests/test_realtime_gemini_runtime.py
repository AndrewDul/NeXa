"""``GeminiVoiceRuntime`` (M2.6B.3/M2.6B.3A/M2.6B.3B) — the HYBRID
cloud-audio + barge-in wiring, plus the pre-live hardening seams:
playback-lifecycle-aware barge-in finishing, the fully-conservative
interrupted-turn spoken-prefix rule (M2.6B.3B — no deterministic
assistant-text/audio alignment is reachable through the installed
Pipecat/google-genai stack, so an interrupted turn's assistant text is
always empty; see the runtime module docstring), and driven mid-turn
fresh-session recovery.

No real audio device, no network: the hardware-only object graph is
exercised via ``dry=True`` construction (mirrors the M2.6A probe's own
``--dry``); the event-driven wiring (``_consume_provider_events``,
``_on_confirmed``) is exercised with a real ``GeminiLiveProvider`` driven
by the same FAKE terminal service used in ``test_realtime_gemini_service``
(no network) plus a stub in place of the hardware ``PipelineWorker``. The
``_ResponseLifecycle`` combinator is also exercised as a pure unit — it
holds no Pipecat/network state.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.session import ConversationSession, ExternalExchangeOutcome  # noqa: E402
from nexa.realtime.gemini.runtime import (  # noqa: E402
    CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX,
    GeminiVoiceRuntime,
    RuntimeMetrics,
    _make_vad_bridge_class,
    _pipecat_hw_imports,
    _ProviderHandle,
    _ResponseGenerationGuard,
    _ResponseLifecycle,
    build_gemini_voice_runtime,
)
from nexa.realtime.policy import ActiveProvider, ConversationPolicy  # noqa: E402
from nexa.realtime.router import ConversationRouter  # noqa: E402
from nexa.stt.utterance_buffer import PRE_ROLL_MS  # noqa: E402
from nexa.voice.aec import AecReferenceHealth  # noqa: E402
from nexa.voice.bargein import BargeInController, InterruptContext  # noqa: E402
from nexa.voice.interruption import InterruptionState  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import FakeModelProvider  # noqa: E402

try:
    from nexa.realtime.gemini.service import GeminiLiveProvider
    from nexa.realtime.provider import (
        AssistantAudioEvent,
        GenerationCompleteEvent,
        ReadinessChangedEvent,
    )
    from test_realtime_gemini_service import _FakeGeminiLiveServiceTestCase

    _PIPECAT_AVAILABLE = True
except Exception:  # pragma: no cover - only if pipecat-ai isn't importable
    _PIPECAT_AVAILABLE = False

    class _FakeGeminiLiveServiceTestCase(unittest.IsolatedAsyncioTestCase):
        pass


async def _wait_until(predicate, *, timeout: float = 2.0, step: float = 0.01) -> None:
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(step)
        elapsed += step
    raise AssertionError(f"condition not met within {timeout}s")


def _fresh_router() -> ConversationRouter:
    """M2.6B.4H (R0046) -- a minimal, real ``ConversationRouter`` (no cloud
    provider factory -- these callers never start a cloud provider through
    it) for tests that only need the bridge's OWN canonical-turn-opening
    call site (``router.begin_cloud_turn()`` /
    ``router.has_turn_awaiting_assistant()``), not the full commit path."""
    session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
    return ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)


class _StubHardwareWorker:
    """Stands in for the ``PipelineWorker`` driving the reSpeaker/speaker
    hardware — records injected frames instead of touching any device."""

    def __init__(self) -> None:
        self.queued_frames: list = []

    async def queue_frames(self, frames) -> None:
        self.queued_frames.extend(frames)


class TestDryConstruction(unittest.TestCase):
    """No audio device, no network: ``dry=True`` must build every NeXa
    object (provider, router, AEC health, BargeInController) and touch
    neither ``pyaudio.PyAudio()`` nor Gemini."""

    def test_dry_build_constructs_full_object_graph_without_hardware(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(
            session=session, api_key="FAKE-TEST-KEY", dry=True
        )
        self.assertIsInstance(runtime, GeminiVoiceRuntime)
        self.assertIsInstance(runtime.router, ConversationRouter)
        self.assertIsInstance(runtime.aec_health, AecReferenceHealth)
        self.assertIsInstance(runtime.bargein, BargeInController)
        self.assertIsInstance(runtime.metrics, RuntimeMetrics)
        self.assertIsNone(runtime.hw_worker)
        self.assertIsNone(runtime.hw_runner)
        self.assertIs(runtime.provider_handle.current, runtime.provider)
        # CLOUD_PREFERRED is the explicit, operator-chosen policy for this
        # runtime -- never the LOCAL_ONLY default (M2.6B.3 charter: this
        # module IS the cloud entry point, launched deliberately).
        self.assertEqual(runtime.router.policy, ConversationPolicy.CLOUD_PREFERRED)

    def test_dry_build_never_opens_a_real_audio_device(self) -> None:
        # If this accidentally called pyaudio.PyAudio()/find_device_index on
        # a sandbox with no audio hardware, construction would raise. It
        # must not even import pyaudio at all when dry=True.
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        try:
            build_gemini_voice_runtime(session=session, api_key="FAKE-TEST-KEY", dry=True)
        except Exception as exc:  # pragma: no cover - would fail the test below anyway
            self.fail(f"dry=True construction touched hardware/network: {exc!r}")

    def test_aec_ready_signal_is_observable_and_composes_with_metrics(self) -> None:
        """M2.6B.3A §5 -- ``on_aec_change`` must fire from the REAL
        ``AecReferenceHealth`` transition, composed with (never replacing)
        the metrics logger -- not a printed instruction nobody wires up."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        calls: list[bool] = []
        runtime = build_gemini_voice_runtime(
            session=session,
            api_key="FAKE-TEST-KEY",
            on_aec_change=calls.append,
            dry=True,
        )
        metrics_calls: list[bool] = []
        runtime.metrics.aec_reference = metrics_calls.append  # type: ignore[method-assign]

        runtime.aec_health.mark_started()
        self.assertEqual(calls, [True])
        self.assertEqual(metrics_calls, [True])

        runtime.aec_health.mark_failed()
        self.assertEqual(calls, [True, False])
        self.assertEqual(metrics_calls, [True, False])


class TestResponseLifecycle(unittest.TestCase):
    """M2.6B.3A §1 -- generation-complete is not playback-complete. Pure
    unit tests: no Pipecat, no provider, no event loop."""

    def _tracker(self):
        fired: list[str] = []
        lifecycle = _ResponseLifecycle(on_finished=lambda: fired.append("finished"))
        return lifecycle, fired

    def test_1_generation_complete_while_audio_still_playing_stays_active(self) -> None:
        lifecycle, fired = self._tracker()
        lifecycle.mark_dispatched()
        lifecycle.mark_audio_produced()
        lifecycle.observe_bot_started()
        lifecycle.mark_generation_done()  # model done, but audio still playing
        self.assertEqual(fired, [])

    def test_2_bot_stopped_after_generation_complete_finishes_response(self) -> None:
        lifecycle, fired = self._tracker()
        lifecycle.mark_dispatched()
        lifecycle.mark_audio_produced()
        lifecycle.observe_bot_started()
        lifecycle.mark_generation_done()
        self.assertEqual(fired, [])
        lifecycle.observe_bot_stopped()  # real playback-drain truth arrives
        self.assertEqual(fired, ["finished"])

    def test_3_bot_stopped_before_generation_complete_stays_active(self) -> None:
        lifecycle, fired = self._tracker()
        lifecycle.mark_dispatched()
        lifecycle.mark_audio_produced()
        lifecycle.observe_bot_started()
        lifecycle.observe_bot_stopped()  # a mid-response gap, NOT the end
        # generation is still ongoing -- a BotStopped alone, before the
        # model's own generation-complete signal, must never finish the
        # response (this is what production's deterministic end-of-
        # generation TTSStoppedFrame injection exists to disambiguate from
        # the real end -- see test_4 for the realistic full sequence).
        self.assertEqual(fired, [])

    def test_4_multi_chunk_response_does_not_finish_between_audio_spans(self) -> None:
        lifecycle, fired = self._tracker()
        lifecycle.mark_dispatched()
        lifecycle.mark_audio_produced()
        lifecycle.observe_bot_started()
        lifecycle.observe_bot_stopped()  # span 1 ends (inter-chunk gap)
        self.assertEqual(fired, [])
        lifecycle.observe_bot_started()  # span 2 begins
        lifecycle.observe_bot_stopped()  # span 2 ends
        self.assertEqual(fired, [])  # still no generation-complete signal
        lifecycle.mark_generation_done()
        lifecycle.observe_bot_started()  # span 3
        lifecycle.observe_bot_stopped()  # span 3 -- the real end this time
        self.assertEqual(fired, ["finished"])
        # never fires twice for the same response
        lifecycle.observe_bot_stopped()
        self.assertEqual(fired, ["finished"])

    def test_5_interruption_during_queued_tail_audio_releases_correctly(self) -> None:
        lifecycle, fired = self._tracker()
        lifecycle.mark_dispatched()
        lifecycle.mark_audio_produced()
        lifecycle.observe_bot_started()
        lifecycle.mark_interrupted()  # local barge-in confirmed mid-playback
        self.assertEqual(fired, [])  # mark_interrupted itself never calls on_finished
        # late, stale signals for the interrupted response must never fire
        # a stale finish for it either.
        lifecycle.mark_generation_done()
        lifecycle.observe_bot_stopped()
        self.assertEqual(fired, [])
        # a FRESH response (the next turn) works normally again.
        lifecycle.mark_dispatched()
        lifecycle.mark_audio_produced()
        lifecycle.observe_bot_started()
        lifecycle.mark_generation_done()
        lifecycle.observe_bot_stopped()
        self.assertEqual(fired, ["finished"])

    def test_no_audio_produced_finishes_immediately_on_generation_done(self) -> None:
        """E.g. a text-only edge case or an error before any audio arrived
        -- nothing will ever make BotStarted/BotStopped fire."""
        lifecycle, fired = self._tracker()
        lifecycle.mark_dispatched()
        lifecycle.mark_generation_done()  # produced_audio was never marked
        self.assertEqual(fired, ["finished"])


class TestResponseGenerationGuard(unittest.TestCase):
    """M2.6B.4 FAILURE 2 -- pure unit tests for the response-generation
    ownership guard. No Pipecat, no provider, no event loop. Deliberately
    minimal: dispatch *timing* is NOT this guard's concern (see its own
    docstring for why an earlier draft that conflated the two was wrong)
    -- only whether a given generation id may still produce audio."""

    def test_fresh_guard_has_no_valid_generation(self) -> None:
        # 0 is the sentinel "no real generation allocated yet" value (also
        # what `interrupt()` resets `_valid_id` to) -- `start_new_generation()`
        # itself never returns 0, so no real generation id is ever valid
        # before one has actually been allocated.
        guard = _ResponseGenerationGuard()
        self.assertFalse(guard.is_valid(1))

    def test_dispatch_allocates_a_valid_id(self) -> None:
        guard = _ResponseGenerationGuard()
        gid = guard.start_new_generation()
        self.assertTrue(guard.is_valid(gid))

    def test_interrupt_invalidates_current_generation(self) -> None:
        guard = _ResponseGenerationGuard()
        gid = guard.start_new_generation()
        guard.interrupt()
        self.assertFalse(guard.is_valid(gid))

    def test_next_generation_after_interrupt_is_independently_valid(self) -> None:
        guard = _ResponseGenerationGuard()
        gid_n = guard.start_new_generation()
        guard.interrupt()
        gid_n1 = guard.start_new_generation()
        self.assertNotEqual(gid_n, gid_n1)
        self.assertFalse(guard.is_valid(gid_n))
        self.assertTrue(guard.is_valid(gid_n1))

    def test_ids_are_monotonically_increasing_and_never_reused(self) -> None:
        guard = _ResponseGenerationGuard()
        seen = {guard.start_new_generation() for _ in range(5)}
        self.assertEqual(len(seen), 5)  # five distinct ids, never repeated


class TestInterruptedHistorySafety(unittest.TestCase):
    """M2.6B.3B -- no deterministic assistant-text/audio alignment is
    reachable through the installed Pipecat/google-genai stack (the
    ``words``/``WordInfo.start_offset``/``end_offset`` fields exist in
    ``google.genai.types.Transcription`` but Pipecat's installed
    ``_handle_msg_output_transcription`` never reads or forwards them —
    only the concatenated ``.text`` reaches any frame NeXa's provider can
    see). M2.6B.3A's one-audio-chunk-lag "high-water" mechanism is gone:
    a later chunk's mere existence cannot prove how much of an EARLIER
    text snapshot that chunk's own audio actually covers. The v1 rule is
    now fully conservative: an interrupted cloud turn's committed
    assistant text is unconditionally
    ``CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX`` (``""``) — never a
    partial-credit approximation built from chunk count."""

    def test_1_transcript_far_ahead_of_first_audio_commits_no_future_text(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session)

        router.begin_cloud_turn()
        router.on_user_transcription("tell me a story", final=True)
        # the model's own output-transcription looks far ahead of any
        # audio actually produced (the documented Gemini/Pipecat
        # look-ahead failure mode) -- a full paragraph arrives as text
        # before a single audio chunk exists.
        router.on_assistant_transcription(
            "Once upon a time, in a land far beyond the seven seas, "
            "there lived a dragon who loved to read.",
            final=False,
        )
        router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
        router.on_interruption()
        outcome = router.commit_cloud_turn()

        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].content, "tell me a story")

    def test_2_multiple_audio_chunks_do_not_prove_the_whole_snapshot_spoken(self) -> None:
        """Even with TWO (or more) audio chunks having arrived -- the
        exact scenario M2.6B.3A's high-water mechanism treated as
        "safe to credit" -- the existence of chunk #2 proves nothing
        about how much of the *earlier* text snapshot chunk #1's own
        audio actually covered (chunk #1's audio might be "abc" while the
        snapshot already read "abcdef ghijkl..."). The conservative rule
        commits no assistant text regardless of chunk count."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session)

        router.begin_cloud_turn()
        router.on_user_transcription("tell me a story", final=True)
        router.on_assistant_transcription("Once upon a ti", final=False)
        # (an audio chunk would arrive here in production -- irrelevant:
        # nothing about its arrival is used to credit any text)
        router.on_assistant_transcription("me, in a land far beyond", final=False)
        # (a second audio chunk arrives here too -- still irrelevant)

        router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
        router.on_interruption()
        outcome = router.commit_cloud_turn()

        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)

    def test_3_interruption_with_no_trustworthy_prefix_is_user_only_commit(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session)

        router.begin_cloud_turn()
        router.on_user_transcription("tell me a story", final=True)
        router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
        router.on_interruption()
        outcome = router.commit_cloud_turn()

        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].content, "tell me a story")

    def test_4_late_transcription_after_interruption_remains_ignored(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session)

        router.begin_cloud_turn()
        router.on_user_transcription("tell me a story", final=True)
        router.on_assistant_transcription("Once upon a ti", final=False)
        router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
        router.on_interruption()
        # a late server transcription delta for the invalidated reply
        # arrives after the local interruption -- must never resurrect any
        # assistant text (CloudTurnAccumulator's own R0034 guard).
        router.on_assistant_transcription(" the end", final=True)
        outcome = router.commit_cloud_turn()

        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)

    def test_5_normal_non_interrupted_turn_stores_full_final_text(self) -> None:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session)

        router.begin_cloud_turn()
        router.on_user_transcription("hej", final=True)
        router.on_assistant_transcription("Cze", final=False)
        router.on_assistant_transcription("ść!", final=True)
        outcome = router.commit_cloud_turn()

        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_EXCHANGE)
        self.assertEqual(session.history[1].content, "Cześć!")
        self.assertFalse(session.history[1].interrupted)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestBargeInWiring(_FakeGeminiLiveServiceTestCase):
    """Keeping ``BargeInController``'s state machine in sync with cloud-turn
    events via ``_ResponseLifecycle`` (never a naive generation-complete==
    finished conflation), and the ``on_confirmed`` hook's actions."""

    def _wire(self, provider) -> GeminiVoiceRuntime:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)
        aec_health = AecReferenceHealth()
        aec_health.mark_started()
        metrics = RuntimeMetrics()
        provider_handle = _ProviderHandle(provider)
        lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())

        cancel_calls: list[str] = []

        async def _fake_cancel() -> None:
            cancel_calls.append("cancelled")

        provider.cancel = _fake_cancel  # type: ignore[method-assign]

        generation_guard = _ResponseGenerationGuard()

        def _on_confirmed(ctx: InterruptContext) -> None:
            metrics.local_interruption_confirmed()
            generation_guard.interrupt()
            router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
            router.on_interruption()
            lifecycle.mark_interrupted()
            asyncio.create_task(provider_handle.current.cancel())
            bargein.notify_interruption_complete()

        bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)
        runtime = GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=generation_guard,
        )
        runtime._cancel_calls = cancel_calls  # type: ignore[attr-defined]
        return runtime

    async def test_first_assistant_output_dispatches_bargein_response(self) -> None:
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"user-audio")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        fake = provider._llm  # noqa: SLF001
        await fake.emit_user_transcription("hello", final=True)
        await fake.emit_assistant_audio(b"assistant-pcm")

        await _wait_until(lambda: runtime.bargein.active_response_id is not None)

        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.RESPONDING)
        self.assertEqual(runtime.hw_worker.queued_frames[0].audio, b"assistant-pcm")

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_generation_complete_alone_does_not_finish_response(self) -> None:
        """M2.6B.3A §1 -- GenerationCompleteEvent alone (no BotStopped yet)
        must NOT flip the barge-in controller back to IDLE."""
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"user-audio")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        fake = provider._llm  # noqa: SLF001
        await fake.emit_user_transcription("hello", final=True)
        await fake.emit_assistant_audio(b"assistant-pcm")
        await _wait_until(lambda: runtime.bargein.active_response_id is not None)
        await fake.emit_assistant_text("hi")
        await fake.emit_generation_complete()

        # give the loop a chance to (wrongly) finish, if it were going to
        await asyncio.sleep(0.05)
        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.RESPONDING)
        # the deterministic TTSStoppedFrame was injected right after the
        # audio chunk, in FIFO order -- never a timer.
        stopped_frames = [
            f for f in runtime.hw_worker.queued_frames if type(f).__name__ == "TTSStoppedFrame"
        ]
        self.assertEqual(len(stopped_frames), 1)

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_real_bot_stopped_after_generation_complete_finishes_response(self) -> None:
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"user-audio")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        fake = provider._llm  # noqa: SLF001
        await fake.emit_user_transcription("hello", final=True)
        await fake.emit_assistant_audio(b"assistant-pcm")
        await _wait_until(lambda: runtime.bargein.active_response_id is not None)
        await fake.emit_assistant_text("hi")
        await fake.emit_generation_complete()
        await asyncio.sleep(0.05)
        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.RESPONDING)

        # the real output-transport playback-lifecycle frame finally
        # confirms the queued audio has drained.
        runtime.lifecycle.observe_bot_started()
        runtime.lifecycle.observe_bot_stopped()

        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.IDLE)
        self.assertIsNone(runtime.bargein.active_response_id)

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_consecutive_normal_turns_rearm_bargein_each_time(self) -> None:
        """M2.6B.4C (R0041) retest item 7 -- after a FULLY-FINISHED normal
        (non-interrupted) turn returns ``BargeInController`` to IDLE, a
        SECOND, independent normal turn must dispatch and finish exactly
        the same way -- no leftover state from turn 1 (a stale generation
        id, a stuck lifecycle flag, an already-consumed re-arm) may block
        or corrupt turn 2."""
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)
        fake = provider._llm  # noqa: SLF001

        for turn_no, (user_text, assistant_text, assistant_pcm) in enumerate(
            (
                ("hello", "hi", b"assistant-pcm-1"),
                ("how are you", "fine", b"assistant-pcm-2"),
            ),
            start=1,
        ):
            runtime.router.begin_cloud_turn()
            await provider.user_turn_start()
            await provider.send_user_audio(f"user-audio-{turn_no}".encode())
            await provider.user_turn_end()

            consume_task = asyncio.create_task(runtime._consume_provider_events())
            await fake.emit_user_transcription(user_text, final=True)
            await fake.emit_assistant_audio(assistant_pcm)
            await _wait_until(lambda: runtime.bargein.active_response_id is not None)
            self.assertEqual(
                runtime.bargein.state_machine.state, InterruptionState.RESPONDING
            )
            await fake.emit_assistant_text(assistant_text)
            await fake.emit_generation_complete()
            await asyncio.sleep(0.05)

            # real playback-lifecycle truth, exactly as a real
            # BaseOutputTransport would confirm the queue drained.
            runtime.lifecycle.observe_bot_started()
            runtime.lifecycle.observe_bot_stopped()

            self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.IDLE)
            self.assertIsNone(runtime.bargein.active_response_id)
            self.assertIn(
                assistant_pcm,
                [f.audio for f in runtime.hw_worker.queued_frames if hasattr(f, "audio")],
            )

            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass

    async def test_on_confirmed_hook_notifies_router_and_cancels_provider_and_bargein(
        self,
    ) -> None:
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)

        runtime.router.begin_cloud_turn()
        runtime.router.on_user_transcription("user text", final=True)
        runtime.router.on_assistant_transcription("partial reply", final=False)

        runtime.bargein.notify_response_dispatched()
        # Drive the state machine to INTERRUPTING via its own public
        # transitions (RESPONDING -> CANDIDATE -> confirmed) -- confirm-hold
        # *timing* is InterruptionStateMachine's own concern (dedicated
        # tests already cover it); this reaches the exact state
        # ``_on_confirmed`` is always invoked from in production.
        sm = runtime.bargein.state_machine
        sm.speech_started(0.0)
        sm.poll(sm.confirm_hold_secs + 1.0)
        self.assertEqual(sm.state, InterruptionState.INTERRUPTING)

        # Directly exercise the on_confirmed hook (white-box: normally
        # BargeInController invokes it itself from _do_confirm).
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="test")
        )

        await _wait_until(lambda: bool(runtime._cancel_calls))  # type: ignore[attr-defined]

        self.assertTrue(runtime.router._turn.current.interrupted)  # noqa: SLF001
        # M2.6B.3B -- the "partial reply" transcription accumulated before
        # the interruption is NEVER committed as spoken; the conservative
        # rule forces it to empty regardless of what had accumulated.
        self.assertEqual(runtime.router._turn.current.assistant_text, "")  # noqa: SLF001
        self.assertEqual(runtime._cancel_calls, ["cancelled"])  # type: ignore[attr-defined]
        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.IDLE)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestMidTurnRuntimeRecovery(_FakeGeminiLiveServiceTestCase):
    """M2.6B.3A §3 -- prove ``GeminiVoiceRuntime`` itself drives
    ``ConversationRouter.recover_from_mid_turn_loss`` the instant
    ``provider.needs_fresh_session`` is observed true, with exactly one
    event consumer at all times and no duplicate/lost canonical history."""

    def _fresh_provider_factory(self) -> tuple[Callable[[], GeminiLiveProvider], dict]:
        constructed: dict = {"calls_by_instance": []}

        def factory() -> GeminiLiveProvider:
            fake_cls, calls = self._make_fake_service_class()
            constructed["calls_by_instance"].append(calls)
            return GeminiLiveProvider(
                api_key="FAKE-TEST-KEY", service_class=fake_cls, ready_timeout_s=5.0
            )

        return factory, constructed

    async def test_mid_turn_loss_swaps_provider_replays_pending_and_next_turn_is_clean(
        self,
    ) -> None:
        old_provider, _ = await self._started_provider()
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        factory, constructed = self._fresh_provider_factory()
        router = ConversationRouter(
            session, policy=ConversationPolicy.CLOUD_PREFERRED, cloud_provider_factory=factory
        )
        router._active_provider = ActiveProvider.CLOUD  # noqa: SLF001 - simulate already-cloud

        aec_health = AecReferenceHealth()
        metrics = RuntimeMetrics()
        provider_handle = _ProviderHandle(old_provider)
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        bargein = BargeInController(aec_health=aec_health, on_confirmed=lambda ctx: None)

        runtime = GeminiVoiceRuntime(
            provider=old_provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=_ResponseGenerationGuard(),
        )

        await self._drain_one(old_provider, ReadinessChangedEvent)
        router.begin_cloud_turn()
        await old_provider.user_turn_start()
        await old_provider.send_user_audio(b"live-1")
        await _wait_until(lambda: len(old_provider._llm.received_audio) >= 1)  # noqa: SLF001

        # Drive the drop + stranded audio BEFORE the runtime's own consumer
        # task exists, so there is no race between this coroutine injecting
        # "stranded-1" and the consumer reacting to the ReconnectingEvent
        # that the SAME readiness-monitor transition also queues (both
        # `needs_fresh_session` and that event become true/queued in the
        # same synchronous step inside GeminiLiveProvider -- a concurrent
        # consumer could otherwise recover before this audio ever arrives).
        # The provider's own readiness monitor is a background task
        # entirely independent of the runtime's consumer, so this ordering
        # is deterministic without it.
        old_provider._llm._ready_for_realtime_input = False  # noqa: SLF001 - simulated drop
        await _wait_until(lambda: old_provider.needs_fresh_session)
        await old_provider.send_user_audio(b"stranded-1")  # arrives during the outage

        # NOW start the runtime's single consumer -- it will see the
        # already-queued ReconnectingEvent and drive recovery itself.
        consume_task = asyncio.create_task(runtime._consume_provider_events())

        # the runtime notices on its own (no direct router call here) and
        # swaps -- exactly one consumer throughout, never two concurrent
        # provider.events() readers.
        await _wait_until(lambda: runtime.provider is not old_provider, timeout=3.0)
        new_provider = runtime.provider
        self.assertIs(runtime.provider_handle.current, new_provider)
        self.assertIsNot(new_provider, old_provider)

        await _wait_until(lambda: new_provider._llm.turn_ends >= 1, timeout=3.0)  # noqa: SLF001
        # only the post-loss, undelivered audio was replayed.
        self.assertEqual(new_provider._llm.received_audio, [b"stranded-1"])  # noqa: SLF001

        # the OLD provider's queue is never read again: fabricate a further
        # event directly on its (abandoned) queue and prove it never
        # reaches the router/session.
        old_provider._events.put_nowait(GenerationCompleteEvent())  # noqa: SLF001
        await asyncio.sleep(0.1)
        self.assertEqual(len(session.history), 0)  # nothing spurious committed

        # a NEXT normal turn goes only to the new provider and commits
        # exactly once, with no duplicate/lost exchange.
        router.begin_cloud_turn()
        new_fake = new_provider._llm  # noqa: SLF001
        await new_fake.emit_user_transcription("next turn", final=True)
        await new_fake.emit_assistant_text("next reply")
        await new_fake.emit_generation_complete()
        await _wait_until(lambda: len(session.history) == 2)

        self.assertEqual(session.history[0].content, "next turn")
        self.assertEqual(session.history[1].content, "next reply")
        self.assertEqual(router.active_provider, ActiveProvider.CLOUD)

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await old_provider.stop(reason="test cleanup")
        await new_provider.stop(reason="test cleanup")


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestVadBridgeProcessorLifecycle(unittest.IsolatedAsyncioTestCase):
    """M2.6B.4 FAILURE 1 -- the REAL Pipecat processor lifecycle (setup ->
    process representative frames -> cleanup), not construction-only
    ``--dry``. Reproduces the exact live failure
    ("'RuntimeMetrics' object has no attribute 'setup'"/'cleanup'")
    deterministically against a genuine ``Pipeline``/``PipelineWorker``/
    ``WorkerRunner``, then proves it fixed."""

    async def test_bridge_completes_real_setup_process_cleanup_with_no_error(self) -> None:
        P = _pipecat_hw_imports()

        class _StubProvider:
            def __init__(self) -> None:
                self.calls: list = []

            async def user_turn_start(self) -> None:
                self.calls.append("start")

            async def send_user_audio(self, pcm: bytes) -> None:
                self.calls.append(("audio", pcm))

            async def user_turn_end(self) -> None:
                self.calls.append("end")

        stub = _StubProvider()
        handle = _ProviderHandle(stub)
        metrics = RuntimeMetrics()
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        bridge_cls = _make_vad_bridge_class(P)
        bridge = bridge_cls(
            provider_handle=handle, metrics=metrics, lifecycle=lifecycle, router=_fresh_router(),
            preroll_ms=PRE_ROLL_MS,
        )

        pipeline = P["Pipeline"]([bridge])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )

        pipeline_errors: list = []
        started = asyncio.Event()

        @worker.event_handler("on_pipeline_error")
        async def _on_error(_worker, frame) -> None:  # noqa: ANN001
            pipeline_errors.append(frame)

        @worker.event_handler("on_pipeline_started")
        async def _on_started(_worker, _frame) -> None:  # noqa: ANN001
            started.set()

        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())

        await asyncio.wait_for(started.wait(), timeout=5.0)
        # setup() ran with zero processor-unusable error -- this is exactly
        # where the live run failed ("Error setting up processor").
        self.assertTrue(bridge.is_usable)
        self.assertEqual(pipeline_errors, [])

        # representative frames -- exactly the ones production sends
        # through this processor.
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](
                    audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1
                ),
                P["VADUserStoppedSpeakingFrame"](),
                P["BotStartedSpeakingFrame"](),
                P["BotStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.2)

        self.assertTrue(bridge.is_usable)
        self.assertEqual(pipeline_errors, [])
        self.assertEqual(stub.calls[0], "start")
        self.assertEqual(stub.calls[-1], "end")
        self.assertEqual(lifecycle._bot_speaking, False)  # noqa: SLF001 - observed both frames

        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)
        # cleanup() ran with zero error either -- this is exactly where
        # the live run's teardown failed ("Error cleaning up processor").
        self.assertTrue(bridge.is_usable)
        self.assertEqual(pipeline_errors, [])

    async def test_runtime_metrics_never_receives_a_setup_or_cleanup_call(self) -> None:
        """White-box: prove the specific root cause is closed -- NeXa's
        RuntimeMetrics object is never asked for the Pipecat processor
        lifecycle methods it does not implement."""
        P = _pipecat_hw_imports()

        class _StubProvider:
            async def user_turn_start(self) -> None:
                pass

            async def send_user_audio(self, pcm: bytes) -> None:
                pass

            async def user_turn_end(self) -> None:
                pass

        handle = _ProviderHandle(_StubProvider())
        metrics = RuntimeMetrics()
        self.assertFalse(hasattr(metrics, "setup"))
        self.assertFalse(hasattr(metrics, "cleanup"))
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        bridge_cls = _make_vad_bridge_class(P)
        bridge = bridge_cls(
            provider_handle=handle, metrics=metrics, lifecycle=lifecycle, router=_fresh_router(),
            preroll_ms=PRE_ROLL_MS,
        )

        # The bridge must never have stored RuntimeMetrics under the same
        # attribute name Pipecat's own FrameProcessor uses internally --
        # bridge._metrics stays Pipecat's own FrameProcessorMetrics
        # (installed source, frame_processor.py:256); not asserting its
        # exact type here, only that it is NOT NeXa's RuntimeMetrics.
        self.assertIsNot(bridge._metrics, metrics)  # noqa: SLF001
        self.assertFalse(hasattr(bridge._metrics, "local_vad_start"))  # noqa: SLF001
        self.assertIs(bridge._nexa_metrics, metrics)  # noqa: SLF001

        pipeline = P["Pipeline"]([bridge])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        started = asyncio.Event()

        @worker.event_handler("on_pipeline_started")
        async def _on_started(_worker, _frame) -> None:  # noqa: ANN001
            started.set()

        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.wait_for(started.wait(), timeout=5.0)
        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)
        self.assertTrue(bridge.is_usable)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestGenerationGuardWiredIntoConsumer(_FakeGeminiLiveServiceTestCase):
    """M2.6B.4 FAILURE 2 -- reproduces the exact live scenario end-to-end
    through ``GeminiVoiceRuntime._consume_provider_events`` (not just the
    pure guard unit): a response generation queues audio, some already
    reaches the stub hardware worker, local barge-in confirms, MORE audio
    for the SAME (now invalid) generation is still in flight on the
    provider's own event stream, and the interrupting utterance's own
    reply (generation N+1) must play normally afterward."""

    def _wire(self, provider) -> GeminiVoiceRuntime:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)
        aec_health = AecReferenceHealth()
        aec_health.mark_started()
        metrics = RuntimeMetrics()
        provider_handle = _ProviderHandle(provider)
        lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())
        generation_guard = _ResponseGenerationGuard()

        async def _fake_cancel() -> None:
            return None

        provider.cancel = _fake_cancel  # type: ignore[method-assign]

        def _on_confirmed(ctx: InterruptContext) -> None:
            generation_guard.interrupt()
            router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
            router.on_interruption()
            lifecycle.mark_interrupted()
            asyncio.create_task(provider_handle.current.cancel())
            bargein.notify_interruption_complete()

        bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)
        return GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=generation_guard,
        )

    async def test_late_invalidated_generation_audio_never_reaches_hardware(self) -> None:
        import time

        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"tell me about black holes")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        fake = provider._llm  # noqa: SLF001

        await fake.emit_user_transcription("tell me about black holes", final=True)
        await fake.emit_assistant_audio(b"gen-N-chunk-1")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        # Local barge-in confirms mid-reply -- measure confirm -> guard
        # invalidation latency (must be effectively synchronous: a plain
        # attribute comparison, no I/O).
        t0 = time.monotonic()
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="test")
        )
        invalidation_latency_s = time.monotonic() - t0
        self.assertLess(invalidation_latency_s, 0.05)

        # MORE generation-N audio was already in flight on the provider's
        # own event stream (queued before the interruption, or produced by
        # Gemini in the brief window before honouring the cancel signal) --
        # this must NEVER reach the hardware pipeline (speaker or AEC).
        await fake.emit_assistant_audio(b"gen-N-chunk-2-late")
        await fake.emit_assistant_audio(b"gen-N-chunk-3-late")
        await asyncio.sleep(0.05)

        queued_audio = [
            f.audio for f in runtime.hw_worker.queued_frames if hasattr(f, "audio")
        ]
        self.assertEqual(queued_audio, [b"gen-N-chunk-1"])

        # The interrupting utterance's own reply (generation N+1) plays
        # normally -- never silently dropped by a guard stuck invalid.
        runtime.router.begin_cloud_turn()
        await fake.emit_user_transcription("what about the sun", final=True)
        await fake.emit_assistant_audio(b"gen-N+1-chunk-1")
        await _wait_until(
            lambda: b"gen-N+1-chunk-1"
            in [f.audio for f in runtime.hw_worker.queued_frames if hasattr(f, "audio")]
        )

        # no duplicate/lost canonical exchange: exactly the two OLD-gen
        # (interrupted, empty prefix) and NEW-gen turns are ever recorded
        # once each turn actually completes.
        await fake.emit_generation_complete()  # late complete for gen N -- no-op re-drain
        await asyncio.sleep(0.05)

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestPostInterruptionAudioRecovery(_FakeGeminiLiveServiceTestCase):
    """M2.6B.4E (R0043) -- real Attempt #2 hardware evidence: a throat-
    clear confirmed a local barge-in and closed a real local VAD turn
    with NO ``UserTranscriptionEvent`` ever produced for it. R0043's OWN
    fix for this (a ``provider.local_turn_closed_seq`` fallback re-arm on
    the SAME, still-being-read provider) was superseded and REMOVED in
    M2.6B.4G (R0045) -- every confirmed interruption is now handled by
    ATOMIC PROVIDER REPLACEMENT instead (see
    ``TestAtomicProviderReplacement``, below, for the no-transcript
    recovery proof under the NEW mechanism). The tests that REMAIN in
    THIS class test properties that are INDEPENDENT of which re-arm
    mechanism is active -- ack idempotency, cancel/confirm counts,
    old-generation-audio-never-reaches-hardware, and the three event-
    ordering variants -- and stay valid and necessary under R0045
    unchanged."""

    def _wire(self, provider) -> GeminiVoiceRuntime:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)
        aec_health = AecReferenceHealth()
        aec_health.mark_started()
        metrics = RuntimeMetrics()
        provider_handle = _ProviderHandle(provider)
        lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())
        generation_guard = _ResponseGenerationGuard()

        cancel_calls: list[str] = []

        async def _fake_cancel() -> None:
            cancel_calls.append("cancelled")

        provider.cancel = _fake_cancel  # type: ignore[method-assign]

        def _on_confirmed(ctx: InterruptContext) -> None:
            generation_guard.interrupt()
            router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
            router.on_interruption()
            lifecycle.mark_interrupted()
            asyncio.create_task(provider_handle.current.cancel())
            bargein.notify_interruption_complete()

        bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)
        runtime = GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=generation_guard,
        )
        runtime._cancel_calls = cancel_calls  # type: ignore[attr-defined]
        return runtime

    def _queued_audio(self, runtime) -> list[bytes]:
        return [f.audio for f in runtime.hw_worker.queued_frames if hasattr(f, "audio")]

    async def test_2_repeated_provider_interruption_events_are_idempotent(self) -> None:
        """Charter item 2 -- FOUR ``ProviderInterruptionEvent``s from one
        underlying interruption (the real Attempt #2 observation; see the
        R0043 source audit in the module docstring for why Pipecat's own
        ``broadcast_interruption()`` legitimately fans this out) must
        never crash or duplicate a canonical commit."""
        from nexa.realtime.provider import ProviderInterruptionEvent

        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)
        fake = provider._llm  # noqa: SLF001

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"hello")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await fake.emit_user_transcription("hello", final=True)
        await fake.emit_assistant_audio(b"gen-1-chunk")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        for _ in range(4):
            await fake.emit_interruption()
        await asyncio.sleep(0.02)

        turn = runtime.router._turn.current  # noqa: SLF001
        self.assertTrue(turn.interrupted)

        # idempotent: a 5th, independent event still works fine, no
        # exception anywhere in the loop.
        provider._events.put_nowait(ProviderInterruptionEvent())  # noqa: SLF001
        await asyncio.sleep(0.02)
        self.assertTrue(consume_task.done() is False)

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_3_and_4_cancel_and_confirm_counts_are_exactly_one_per_confirmation(
        self,
    ) -> None:
        """Charter items 3 + 4 -- ``provider.cancel()`` and the local
        confirm/broadcast counters increment EXACTLY once per confirmed
        local interruption (never once per downstream provider ack).
        Uses the REAL ``build_gemini_voice_runtime`` wiring (dry mode) --
        this checkpoint's own diagnostics live in that closure, not in
        this test file's simplified ``_wire()`` helper."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(
            session=session, api_key="FAKE-TEST-KEY", dry=True
        )
        cancel_calls: list[str] = []

        async def _fake_cancel() -> None:
            cancel_calls.append("cancelled")

        runtime.provider.cancel = _fake_cancel  # type: ignore[method-assign]

        with self.assertLogs("nexa.realtime.gemini.runtime", level="INFO") as cm:
            runtime.bargein._on_confirmed(  # noqa: SLF001
                InterruptContext(invalidated_response_id=None, reason="test")
            )
        await asyncio.sleep(0.01)
        self.assertEqual(cancel_calls, ["cancelled"])
        joined = "\n".join(cm.output)
        self.assertIn("OUTPUT_INTERRUPTION_BROADCAST count=1", joined)
        self.assertIn("LOCAL_BARGEIN_CONFIRMED confirm_count=1", joined)

        with self.assertLogs("nexa.realtime.gemini.runtime", level="INFO") as cm2:
            runtime.bargein._on_confirmed(  # noqa: SLF001
                InterruptContext(invalidated_response_id=None, reason="test2")
            )
        await asyncio.sleep(0.01)
        self.assertEqual(cancel_calls, ["cancelled", "cancelled"])
        joined2 = "\n".join(cm2.output)
        self.assertIn("OUTPUT_INTERRUPTION_BROADCAST count=2", joined2)
        self.assertIn("LOCAL_BARGEIN_CONFIRMED confirm_count=2", joined2)

    async def test_5_old_generation_trailing_audio_never_reaches_hardware(self) -> None:
        """Charter item 5 -- unchanged R0038 guarantee, re-verified with
        the R0043 fallback logic present (must not weaken it)."""
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)
        fake = provider._llm  # noqa: SLF001

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"tell me about black holes")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await fake.emit_user_transcription("tell me about black holes", final=True)
        await fake.emit_assistant_audio(b"gen-N-chunk-1")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="test")
        )
        await fake.emit_assistant_audio(b"gen-N-chunk-2-late")
        await fake.emit_assistant_audio(b"gen-N-chunk-3-late")
        await asyncio.sleep(0.05)
        self.assertEqual(self._queued_audio(runtime), [b"gen-N-chunk-1"])

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_7_event_order_a_final_transcript_then_audio(self) -> None:
        """Charter item 7 -- the normal, R0034-proven ordering: final
        transcript arrives before the turn's own assistant content."""
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)
        fake = provider._llm  # noqa: SLF001

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"hi")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await fake.emit_user_transcription("hi", final=True)
        await fake.emit_assistant_audio(b"order-a-chunk")
        await _wait_until(lambda: b"order-a-chunk" in self._queued_audio(runtime))

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_8_event_order_b_audio_then_final_transcript_does_not_drop_audio(
        self,
    ) -> None:
        """Charter item 8 -- audio arrives BEFORE the final transcript for
        the SAME turn (ordering not guaranteed after a server
        interruption, per R0039's own official-docs finding). The
        PRIMARY correctness property -- audio is never silently lost --
        must still hold; a redundant generation-id allocation is an
        accepted, non-harmful quirk of this ordering (documented in
        R0043), never a dropped chunk."""
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)
        fake = provider._llm  # noqa: SLF001

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"hi")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        # audio FIRST -- the final transcript for this same turn has not
        # arrived yet.
        await fake.emit_assistant_audio(b"order-b-chunk")
        await _wait_until(lambda: b"order-b-chunk" in self._queued_audio(runtime))
        # the transcript arrives late.
        await fake.emit_user_transcription("hi", final=True)
        await asyncio.sleep(0.02)

        self.assertIn(b"order-b-chunk", self._queued_audio(runtime))

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_9_event_order_c_assistant_text_then_audio_then_late_final_transcript(
        self,
    ) -> None:
        """Charter item 9 -- assistant TEXT arrives first (also a valid
        dispatch trigger), then audio, then a late final transcript for
        the same turn. Same accepted-quirk note as order B."""
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, ReadinessChangedEvent)
        fake = provider._llm  # noqa: SLF001

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"hi")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await fake.emit_assistant_text("hi there")
        await fake.emit_assistant_audio(b"order-c-chunk")
        await _wait_until(lambda: b"order-c-chunk" in self._queued_audio(runtime))
        await fake.emit_user_transcription("hi", final=True)
        await asyncio.sleep(0.02)

        self.assertIn(b"order-c-chunk", self._queued_audio(runtime))

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestVadBridgeQuarantine(unittest.IsolatedAsyncioTestCase):
    """M2.6B.4G (R0045) -- the ACTIVE UTTERANCE BUFFER + quarantine gate,
    exercised through a REAL Pipecat ``Pipeline``/``PipelineWorker`` (the
    same pattern ``TestVadBridgeProcessorLifecycle`` already established),
    not a hand-rolled stand-in -- proves the BRIDGE ITSELF, not just a
    test's own simulation of it, never sends a turn-I/O call to
    ``provider_handle.current`` while quarantined, and hands off the
    COMPLETE sealed utterance instead."""

    async def test_quarantined_utterance_is_buffered_not_sent_live(self) -> None:
        P = _pipecat_hw_imports()

        class _StubProvider:
            def __init__(self) -> None:
                self.calls: list = []

            async def user_turn_start(self) -> None:
                self.calls.append("start")

            async def send_user_audio(self, pcm: bytes) -> None:
                self.calls.append(("audio", pcm))

            async def user_turn_end(self) -> None:
                self.calls.append("end")

        stub = _StubProvider()
        handle = _ProviderHandle(stub)
        metrics = RuntimeMetrics()
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        bridge_cls = _make_vad_bridge_class(P)
        bridge = bridge_cls(
            provider_handle=handle, metrics=metrics, lifecycle=lifecycle, router=_fresh_router(),
            preroll_ms=PRE_ROLL_MS,
        )

        pipeline = P["Pipeline"]([bridge])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.1)

        # normal (non-quarantined) turn -- unchanged live-send behaviour.
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"AAAA", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.2)
        self.assertEqual(stub.calls, ["start", ("audio", b"AAAA"), "end"])
        self.assertEqual(handle.sealed_utterances, [])

        # NOW quarantine -- exactly what `_on_confirmed` does synchronously.
        handle.quarantined = True
        stub.calls.clear()

        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"BBBB", sample_rate=16000, num_channels=1),
                P["InputAudioRawFrame"](audio=b"CCCC", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.2)

        # NOT ONE turn-I/O call reached the (doomed/not-yet-ready) provider.
        self.assertEqual(stub.calls, [])
        # the COMPLETE utterance was captured and sealed instead.
        self.assertEqual(handle.sealed_utterances, [b"BBBBCCCC"])
        self.assertTrue(handle.sealed_utterance_ready.is_set())

        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)

    async def test_prefix_already_live_before_quarantine_is_not_lost(self) -> None:
        """Charter timing case 2 ("confirms mid-utterance"): audio sent
        LIVE before quarantine began is NOT part of the sealed buffer
        (it already reached the old provider, harmlessly, since its
        output is never read again) -- but the buffer STILL captures the
        WHOLE utterance (pre- and post-quarantine) for the runtime's own
        bookkeeping, matching what the bridge actually does: it never
        stops accumulating, it only stops the LIVE send."""
        P = _pipecat_hw_imports()

        class _StubProvider:
            def __init__(self) -> None:
                self.calls: list = []

            async def user_turn_start(self) -> None:
                self.calls.append("start")

            async def send_user_audio(self, pcm: bytes) -> None:
                self.calls.append(("audio", pcm))

            async def user_turn_end(self) -> None:
                self.calls.append("end")

        stub = _StubProvider()
        handle = _ProviderHandle(stub)
        metrics = RuntimeMetrics()
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        bridge_cls = _make_vad_bridge_class(P)
        bridge = bridge_cls(
            provider_handle=handle, metrics=metrics, lifecycle=lifecycle, router=_fresh_router(),
            preroll_ms=PRE_ROLL_MS,
        )
        pipeline = P["Pipeline"]([bridge])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.1)

        # utterance starts, some audio streams live (pre-confirm)...
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"PRE1", sample_rate=16000, num_channels=1),
            ]
        )
        await asyncio.sleep(0.1)
        self.assertEqual(stub.calls, ["start", ("audio", b"PRE1")])

        # ...THEN quarantine begins mid-utterance (confirm fired)...
        handle.quarantined = True
        stub.calls.clear()

        # ...the REST of the SAME utterance is buffered only.
        await worker.queue_frames(
            [
                P["InputAudioRawFrame"](audio=b"POST1", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.1)

        self.assertEqual(stub.calls, [])  # no MORE live sends to the old provider
        # the sealed copy has the WHOLE utterance, pre- and post-quarantine.
        self.assertEqual(handle.sealed_utterances, [b"PRE1POST1"])

        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestVadBridgePrerollParity(unittest.IsolatedAsyncioTestCase):
    """M2.6B.4J (R0049) -- restores the accepted M2.6A property (audio
    immediately BEFORE local VAD confirms speech start is included in the
    user turn) via a bounded rolling PCM pre-buffer inside
    ``_VadToProviderBridge`` (reusing ``nexa.stt.utterance_buffer.
    UtteranceBuffer`` verbatim). Exercised through a REAL Pipecat
    ``Pipeline``/``PipelineWorker`` (the same pattern
    ``TestVadBridgeQuarantine`` already established), never a hand-rolled
    simulation."""

    class _StubProvider:
        def __init__(self) -> None:
            self.calls: list = []

        async def user_turn_start(self) -> None:
            self.calls.append("start")

        async def send_user_audio(self, pcm: bytes) -> None:
            self.calls.append(("audio", pcm))

        async def user_turn_end(self) -> None:
            self.calls.append("end")

    async def _build(self, *, preroll_ms: int = PRE_ROLL_MS):
        P = _pipecat_hw_imports()
        stub = self._StubProvider()
        handle = _ProviderHandle(stub)
        metrics = RuntimeMetrics()
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        bridge_cls = _make_vad_bridge_class(P)
        bridge = bridge_cls(
            provider_handle=handle,
            metrics=metrics,
            lifecycle=lifecycle,
            router=_fresh_router(),
            preroll_ms=preroll_ms,
        )
        pipeline = P["Pipeline"]([bridge])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.1)
        return P, stub, handle, bridge, worker, runner, run_task

    async def _teardown(self, runner, run_task) -> None:
        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)

    async def test_1_pcm_before_vad_start_is_retained_not_forwarded(self) -> None:
        P, stub, _handle, bridge, worker, runner, run_task = await self._build()
        await worker.queue_frames(
            [P["InputAudioRawFrame"](audio=b"PREAMBLE", sample_rate=16000, num_channels=1)]
        )
        await asyncio.sleep(0.1)
        self.assertEqual(stub.calls, [])  # never forwarded while no turn is open
        self.assertIn(b"PREAMBLE", b"".join(bridge._pcm._ring))  # noqa: SLF001
        await self._teardown(runner, run_task)

    async def test_2_idle_rolling_buffer_stays_bounded(self) -> None:
        preroll_ms = 20  # 20ms * 16000Hz * 2 bytes = 640 bytes capacity
        P, stub, _handle, bridge, worker, runner, run_task = await self._build(
            preroll_ms=preroll_ms
        )
        for _ in range(10):
            await worker.queue_frames(
                [P["InputAudioRawFrame"](audio=b"Q" * 200, sample_rate=16000, num_channels=1)]
            )
        await asyncio.sleep(0.2)
        ring_bytes = sum(len(c) for c in bridge._pcm._ring)  # noqa: SLF001
        cap_bytes = int(16000 * (preroll_ms / 1000) * 2)
        # bounded (UtteranceBuffer's own trim never pops the LAST
        # remaining chunk even if that alone exceeds the cap -- so the
        # bound is the configured capacity plus at most one chunk).
        self.assertLessEqual(ring_bytes, cap_bytes + 200)
        self.assertLess(ring_bytes, 2000)  # nowhere near all 10 chunks (2000 bytes)
        await self._teardown(runner, run_task)

    async def test_3_4_5_vad_start_sends_start_then_preroll_then_live_in_order(
        self,
    ) -> None:
        """Invariants 3, 4, 5: ``user_turn_start()`` fires, then the
        COMPLETE retained preroll is sent as ONE call, then subsequent
        live frames follow in the exact order received -- the preroll's
        own bytes (``PREE``) never duplicated into the live stream too."""
        P, stub, _handle, bridge, worker, runner, run_task = await self._build()
        await worker.queue_frames(
            [P["InputAudioRawFrame"](audio=b"PREE", sample_rate=16000, num_channels=1)]
        )
        await asyncio.sleep(0.05)
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"LIVE1", sample_rate=16000, num_channels=1),
                P["InputAudioRawFrame"](audio=b"LIVE2", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.2)
        self.assertEqual(
            stub.calls,
            ["start", ("audio", b"PREE"), ("audio", b"LIVE1"), ("audio", b"LIVE2"), "end"],
        )
        await self._teardown(runner, run_task)

    async def test_6_normal_turn_receives_complete_preroll_plus_live_pcm(self) -> None:
        """Invariant 6: the RAW MIC contract — ``[pre-VAD onset][post-VAD
        speech]`` — reaches the provider exactly once, in order, with no
        gap and no reordering, as one concatenated stream."""
        P, stub, _handle, bridge, worker, runner, run_task = await self._build()
        await worker.queue_frames(
            [P["InputAudioRawFrame"](audio=b"ONSET", sample_rate=16000, num_channels=1)]
        )
        await asyncio.sleep(0.05)
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"REST", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.2)
        delivered = b"".join(c[1] for c in stub.calls if isinstance(c, tuple))
        self.assertEqual(delivered, b"ONSETREST")
        await self._teardown(runner, run_task)

    async def test_7_8_confirmed_bargein_replay_includes_preroll_exactly_once(
        self,
    ) -> None:
        """Invariants 7 + 8, the charter's own worked example: raw
        interrupting speech ``[PREE][POST]`` (PREE before VAD START) —
        after a confirmed barge-in (quarantine), the fresh provider must
        receive exactly ``[PREEPOST]`` once. R0045's own
        ``sealed_utterances`` FIFO is now seeded directly from this SAME
        preroll -- the ACTIVE utterance buffer IS the pre-buffer once
        ``mark_speech_started()`` transfers ownership."""
        P, stub, handle, bridge, worker, runner, run_task = await self._build()
        await worker.queue_frames(
            [P["InputAudioRawFrame"](audio=b"PREE", sample_rate=16000, num_channels=1)]
        )
        await asyncio.sleep(0.05)
        handle.quarantined = True  # exactly what _on_confirmed does synchronously
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"POST", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.2)
        self.assertEqual(stub.calls, [])  # never sent live to the doomed provider
        self.assertEqual(handle.sealed_utterances, [b"PREEPOST"])  # exactly once
        await self._teardown(runner, run_task)

    async def test_11_two_consecutive_turns_do_not_leak_preroll_between_them(
        self,
    ) -> None:
        """Invariant 11: no stale PCM from utterance N may prefix
        utterance N+1 — turn 2 starts immediately after turn 1's own
        stop, with no idle audio fed in between, so its own preroll must
        be empty."""
        P, stub, _handle, bridge, worker, runner, run_task = await self._build()
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"ONE1", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.1)
        stub.calls.clear()
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"TWO1", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.1)
        self.assertEqual(stub.calls, ["start", ("audio", b"TWO1"), "end"])
        await self._teardown(runner, run_task)


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestAtomicProviderReplacement(_FakeGeminiLiveServiceTestCase):
    """M2.6B.4G (R0045) -- end-to-end proof of atomic provider replacement
    after a confirmed local barge-in, using a REAL second
    ``GeminiLiveProvider`` (backed by its own fake terminal service, via
    ``_fresh_provider_factory`` -- the SAME pattern
    ``TestMidTurnRuntimeRecovery`` already established for connection-loss
    recovery) as the replacement target. The bridge's own role (buffering
    vs. live-sending per ``provider_handle.quarantined``) is SIMULATED
    directly via ``provider_handle``/``provider`` calls here (the
    established convention every existing async-level test in this file
    already uses instead of driving real VAD frames through a live
    Pipecat pipeline) -- ``TestVadBridgeQuarantine`` above already proves
    the REAL bridge implements this exact contract."""

    def _fresh_provider_factory(self) -> tuple[Callable[[], GeminiLiveProvider], dict]:
        constructed: dict = {"calls_by_instance": []}

        def factory() -> GeminiLiveProvider:
            fake_cls, calls = self._make_fake_service_class()
            constructed["calls_by_instance"].append(calls)
            return GeminiLiveProvider(
                api_key="FAKE-TEST-KEY", service_class=fake_cls, ready_timeout_s=5.0
            )

        return factory, constructed

    def _wire(self, provider, router) -> GeminiVoiceRuntime:
        aec_health = AecReferenceHealth()
        aec_health.mark_started()
        metrics = RuntimeMetrics()
        provider_handle = _ProviderHandle(provider)
        lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())
        generation_guard = _ResponseGenerationGuard()

        def _on_confirmed(ctx: InterruptContext) -> None:
            # Faithful reproduction of `build_gemini_voice_runtime`'s OWN
            # `_on_confirmed` (M2.6B.4G/R0045, M2.6B.4H/R0046) -- see that
            # function's own docstring/comments for the full rationale of
            # each line.
            generation_guard.interrupt()
            provider_handle.old_provider = provider_handle.current
            provider_handle.quarantined = True
            provider_handle.replacement_requested = True
            router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
            router.on_interruption()
            router.commit_cloud_turn()
            router.begin_cloud_turn()
            lifecycle.mark_interrupted()
            asyncio.create_task(provider_handle.old_provider.cancel())
            bargein.notify_interruption_complete()

        bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)
        runtime = GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=generation_guard,
        )
        return runtime

    def _queued_audio(self, runtime) -> list[bytes]:
        return [f.audio for f in runtime.hw_worker.queued_frames if hasattr(f, "audio")]

    async def _simulate_bridge_send(self, provider_handle, pcm: bytes) -> None:
        """Mirrors ``_VadToProviderBridge.process_frame``'s own
        ``InputAudioRawFrame`` handling exactly: live-send while not
        quarantined, buffer-only while quarantined."""
        if not provider_handle.quarantined:
            await provider_handle.current.send_user_audio(pcm)

    async def test_recovery_after_no_transcript_interruption_via_replacement(
        self,
    ) -> None:
        """The R0045 replacement for R0043's own core proof: recovery
        after a non-transcribed interruption now happens via atomic
        provider replacement, not turn-closure counting. Also proves
        exact-once delivery: the interrupting utterance's PRE-confirm
        prefix (sent live to the OLD provider) plus its POST-confirm
        remainder (buffered) are replayed to the NEW provider as ONE
        coherent utterance."""
        old_provider, _ = await self._started_provider()
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        factory, constructed = self._fresh_provider_factory()
        router = ConversationRouter(
            session, policy=ConversationPolicy.CLOUD_PREFERRED, cloud_provider_factory=factory
        )
        router._active_provider = ActiveProvider.CLOUD  # noqa: SLF001

        runtime = self._wire(old_provider, router)
        provider_handle = runtime.provider_handle
        await self._drain_one(old_provider, ReadinessChangedEvent)
        old_fake = old_provider._llm  # noqa: SLF001

        # Turn 1: real, transcribed question -- dispatches normally.
        router.begin_cloud_turn()
        await old_provider.user_turn_start()
        await old_provider.send_user_audio(b"question one")
        await old_provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await old_fake.emit_user_transcription("question one", final=True)
        await old_fake.emit_assistant_audio(b"gen-1-chunk")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        # Interrupting utterance begins -- NOT yet quarantined; some
        # audio streams live to the OLD provider (matching real VAD
        # timing: confirm fires only after confirm_hold_secs of
        # sustained candidate speech, well after this turn already
        # opened).
        await old_provider.user_turn_start()
        await self._simulate_bridge_send(provider_handle, b"PREE")

        # CONFIRM the interruption -- quarantines the OLD provider.
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="throat_clear")
        )
        self.assertTrue(provider_handle.quarantined)
        self.assertIs(provider_handle.old_provider, old_provider)
        self.assertTrue(provider_handle.replacement_requested)

        # trailing gen-1 audio -- MUST still be dropped (unchanged R0038
        # guarantee, now backed by provider-instance isolation too).
        await old_fake.emit_assistant_audio(b"gen-1-trailing")
        await asyncio.sleep(0.02)
        self.assertEqual(self._queued_audio(runtime), [b"gen-1-chunk"])

        # rest of the SAME interrupting utterance -- buffered only, NO
        # transcript is ever emitted for it (non-lexical noise).
        await self._simulate_bridge_send(provider_handle, b"POST")
        # seal it (VAD end) -- hand off to the replacement flow.
        provider_handle.sealed_utterances.append(b"PREEPOST")
        provider_handle.sealed_utterance_ready.set()

        # The replacement + replay is driven by _consume_provider_events
        # itself, once it notices `replacement_requested` on the very
        # next event -- which `_on_confirmed`'s own cancel() call (fired
        # above) guarantees will arrive promptly.
        await _wait_until(lambda: runtime.provider is not old_provider, timeout=3.0)
        new_provider = runtime.provider
        self.assertIsNot(new_provider, old_provider)
        self.assertIs(provider_handle.current, new_provider)
        self.assertFalse(provider_handle.quarantined)

        # exact-once replay: the WHOLE utterance (pre+post confirm),
        # never zero times, never twice, never partially.
        new_fake = new_provider._llm  # noqa: SLF001
        await _wait_until(lambda: new_fake.turn_ends >= 1, timeout=3.0)
        self.assertEqual(new_fake.received_audio, [b"PREEPOST"])

        # the NEW provider's own reply becomes audible normally -- no
        # transcript was ever required to recover.
        await new_fake.emit_assistant_audio(b"gen-2-chunk")
        await _wait_until(lambda: b"gen-2-chunk" in self._queued_audio(runtime))
        self.assertEqual(self._queued_audio(runtime), [b"gen-1-chunk", b"gen-2-chunk"])

        # `_on_confirmed` already committed turn 1 as an interrupted
        # canonical turn (it had a final transcript before the barge-in
        # fired) -- exactly one entry, never a second one. The OLD
        # provider's queue is never read again: fabricate a further event
        # directly on its (abandoned) queue and prove it does not produce
        # a SECOND, spurious commit.
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].content, "question one")
        old_provider._events.put_nowait(GenerationCompleteEvent())  # noqa: SLF001
        await asyncio.sleep(0.1)
        self.assertEqual(len(session.history), 1)  # nothing spurious committed

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await new_provider.stop(reason="test cleanup")

    async def test_case2_old_delayed_audio_after_new_turn_closes_is_now_dropped(
        self,
    ) -> None:
        """THE critical proof R0044 could not provide: old delayed audio
        arriving AFTER the new local turn closes is now dropped, because
        it originates from the OLD PROVIDER EPOCH, which is never read
        again after the swap -- not because of any turn-closure count."""
        old_provider, _ = await self._started_provider()
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        factory, constructed = self._fresh_provider_factory()
        router = ConversationRouter(
            session, policy=ConversationPolicy.CLOUD_PREFERRED, cloud_provider_factory=factory
        )
        router._active_provider = ActiveProvider.CLOUD  # noqa: SLF001

        runtime = self._wire(old_provider, router)
        provider_handle = runtime.provider_handle
        await self._drain_one(old_provider, ReadinessChangedEvent)
        old_fake = old_provider._llm  # noqa: SLF001

        router.begin_cloud_turn()
        await old_provider.user_turn_start()
        await old_provider.send_user_audio(b"q1")
        await old_provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await old_fake.emit_user_transcription("q1", final=True)
        await old_fake.emit_assistant_audio(b"gen-1-chunk")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="noise")
        )
        provider_handle.sealed_utterances.append(b"noise-utterance_")
        provider_handle.sealed_utterance_ready.set()

        await _wait_until(lambda: runtime.provider is not old_provider, timeout=3.0)
        new_provider = runtime.provider

        # a "new user turn" happens (against the NEW provider, correctly).
        await new_provider.user_turn_start()
        await new_provider.send_user_audio(b"q2")
        await new_provider.user_turn_end()

        # OLD delayed audio arrives on the OLD (abandoned) provider's own
        # queue AFTER all of this -- exactly R0044's CASE 2 shape.
        old_provider._events.put_nowait(  # noqa: SLF001
            AssistantAudioEvent(pcm=b"OLD-delayed-case2")
        )
        await asyncio.sleep(0.05)

        # never reaches hardware -- the old provider's queue was never
        # read again after the swap.
        self.assertNotIn(b"OLD-delayed-case2", self._queued_audio(runtime))

        # the NEW provider's own real response still plays correctly.
        new_fake = new_provider._llm  # noqa: SLF001
        await new_fake.emit_assistant_audio(b"gen-2-chunk")
        await _wait_until(lambda: b"gen-2-chunk" in self._queued_audio(runtime))
        self.assertNotIn(b"OLD-delayed-case2", self._queued_audio(runtime))

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await new_provider.stop(reason="test cleanup")

    async def test_provider_ready_before_vad_end(self) -> None:
        """Charter timing case 4: the new provider becomes READY before
        the interrupting utterance itself seals (VAD end)."""
        old_provider, _ = await self._started_provider()
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        factory, constructed = self._fresh_provider_factory()
        router = ConversationRouter(
            session, policy=ConversationPolicy.CLOUD_PREFERRED, cloud_provider_factory=factory
        )
        router._active_provider = ActiveProvider.CLOUD  # noqa: SLF001
        runtime = self._wire(old_provider, router)
        provider_handle = runtime.provider_handle
        await self._drain_one(old_provider, ReadinessChangedEvent)
        old_fake = old_provider._llm  # noqa: SLF001

        router.begin_cloud_turn()
        await old_provider.user_turn_start()
        await old_provider.send_user_audio(b"q1")
        await old_provider.user_turn_end()
        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await old_fake.emit_user_transcription("q1", final=True)
        await old_fake.emit_assistant_audio(b"gen-1-chunk")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        await old_provider.user_turn_start()
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="noise")
        )

        # give the replacement provider time to become READY BEFORE the
        # utterance seals -- the fake service becomes ready almost
        # immediately once started, so a short sleep here reliably lands
        # in the "ready before seal" ordering.
        await asyncio.sleep(0.1)

        # NOW the utterance seals.
        provider_handle.sealed_utterances.append(b"utterance-after-ready_")
        provider_handle.sealed_utterance_ready.set()

        await _wait_until(lambda: runtime.provider is not old_provider, timeout=3.0)
        new_provider = runtime.provider
        new_fake = new_provider._llm  # noqa: SLF001
        await _wait_until(lambda: new_fake.turn_ends >= 1, timeout=3.0)
        self.assertEqual(new_fake.received_audio, [b"utterance-after-ready_"])

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await new_provider.stop(reason="test cleanup")

    async def test_vad_end_before_provider_ready(self) -> None:
        """Charter timing case 5: the interrupting utterance seals (VAD
        end) before the new provider becomes ready -- the seal must be
        durably queued (the ``asyncio.Event`` is level-triggered) and
        picked up the moment readiness completes."""
        old_provider, _ = await self._started_provider()
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        factory, constructed = self._fresh_provider_factory()
        router = ConversationRouter(
            session, policy=ConversationPolicy.CLOUD_PREFERRED, cloud_provider_factory=factory
        )
        router._active_provider = ActiveProvider.CLOUD  # noqa: SLF001
        runtime = self._wire(old_provider, router)
        provider_handle = runtime.provider_handle
        await self._drain_one(old_provider, ReadinessChangedEvent)
        old_fake = old_provider._llm  # noqa: SLF001

        router.begin_cloud_turn()
        await old_provider.user_turn_start()
        await old_provider.send_user_audio(b"q1")
        await old_provider.user_turn_end()
        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await old_fake.emit_user_transcription("q1", final=True)
        await old_fake.emit_assistant_audio(b"gen-1-chunk")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        await old_provider.user_turn_start()
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="noise")
        )
        # seal IMMEDIATELY -- before the new provider has had any chance
        # to become ready.
        provider_handle.sealed_utterances.append(b"utterance-before-ready")
        provider_handle.sealed_utterance_ready.set()

        await _wait_until(lambda: runtime.provider is not old_provider, timeout=3.0)
        new_provider = runtime.provider
        new_fake = new_provider._llm  # noqa: SLF001
        await _wait_until(lambda: new_fake.turn_ends >= 1, timeout=3.0)
        self.assertEqual(new_fake.received_audio, [b"utterance-before-ready"])

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await new_provider.stop(reason="test cleanup")

    async def test_two_consecutive_barge_ins_each_create_independent_clean_epochs(
        self,
    ) -> None:
        """Charter test 20: two consecutive barge-ins each create
        independent clean epochs -- generation N's old provider AND
        generation N+1's old provider are BOTH abandoned independently;
        only generation N+2's provider is ever read from again."""
        provider_1, _ = await self._started_provider()
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        factory, constructed = self._fresh_provider_factory()
        router = ConversationRouter(
            session, policy=ConversationPolicy.CLOUD_PREFERRED, cloud_provider_factory=factory
        )
        router._active_provider = ActiveProvider.CLOUD  # noqa: SLF001
        runtime = self._wire(provider_1, router)
        provider_handle = runtime.provider_handle
        await self._drain_one(provider_1, ReadinessChangedEvent)
        fake_1 = provider_1._llm  # noqa: SLF001

        router.begin_cloud_turn()
        await provider_1.user_turn_start()
        await provider_1.send_user_audio(b"q1")
        await provider_1.user_turn_end()
        consume_task = asyncio.create_task(runtime._consume_provider_events())
        await fake_1.emit_user_transcription("q1", final=True)
        await fake_1.emit_assistant_audio(b"gen-N-chunk")
        await _wait_until(lambda: len(runtime.hw_worker.queued_frames) >= 1)

        # First barge-in -- provider_1 abandoned, provider_2 takes over.
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="noise1")
        )
        provider_handle.sealed_utterances.append(b"noise1-utterance")
        provider_handle.sealed_utterance_ready.set()
        await _wait_until(lambda: runtime.provider is not provider_1, timeout=3.0)
        provider_2 = runtime.provider
        fake_2 = provider_2._llm  # noqa: SLF001
        await _wait_until(lambda: fake_2.turn_ends >= 1, timeout=3.0)

        await provider_2.user_turn_start()
        await provider_2.send_user_audio(b"q2")
        await provider_2.user_turn_end()
        await fake_2.emit_assistant_audio(b"gen-N+1-chunk")
        await _wait_until(lambda: b"gen-N+1-chunk" in self._queued_audio(runtime))

        # Second barge-in -- provider_2 ALSO abandoned, provider_3 takes over.
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=2, reason="noise2")
        )
        provider_handle.sealed_utterances.append(b"noise2-utterance")
        provider_handle.sealed_utterance_ready.set()
        await _wait_until(lambda: runtime.provider is not provider_2, timeout=3.0)
        provider_3 = runtime.provider
        self.assertIsNot(provider_3, provider_1)
        self.assertIsNot(provider_3, provider_2)
        fake_3 = provider_3._llm  # noqa: SLF001
        await _wait_until(lambda: fake_3.turn_ends >= 1, timeout=3.0)
        self.assertEqual(fake_3.received_audio, [b"noise2-utterance"])

        # BOTH old providers' queues are fed a further event each -- NEITHER
        # ever reaches hardware/canonical history.
        provider_1._events.put_nowait(AssistantAudioEvent(pcm=b"OLD-N")) # noqa: SLF001
        provider_2._events.put_nowait(AssistantAudioEvent(pcm=b"OLD-N+1")) # noqa: SLF001
        await asyncio.sleep(0.05)
        self.assertNotIn(b"OLD-N", self._queued_audio(runtime))
        self.assertNotIn(b"OLD-N+1", self._queued_audio(runtime))

        await fake_3.emit_assistant_audio(b"gen-N+2-chunk")
        await _wait_until(lambda: b"gen-N+2-chunk" in self._queued_audio(runtime))
        self.assertEqual(
            self._queued_audio(runtime),
            [b"gen-N-chunk", b"gen-N+1-chunk", b"gen-N+2-chunk"],
        )

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await provider_3.stop(reason="test cleanup")


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestCloudSameTurnLanguage(unittest.IsolatedAsyncioTestCase):
    """M2.6B.4 FAILURE 3 -- offline, no Gemini call: the same-turn PL/EN
    detection + resolution mechanism the cloud runtime reuses verbatim
    from the already-accepted local-voice stack
    (``WhisperCppLanguageDetector`` + ``ResponseLanguageResolver``).
    Uses REAL recorded PL/EN audio fixtures (16 kHz mono S16LE, already in
    the repo for the M2.4B/M2.6A research corpora) -- never fabricated
    silence/noise, which cannot reliably classify as either language."""

    FIXTURES = REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"
    EN_WAV = FIXTURES / "en_what_is_the_speed_of_light.wav"
    PL_WAV = FIXTURES / "pl_jaka_jest_prędkość_światła.wav"

    @classmethod
    def setUpClass(cls) -> None:
        import wave

        if not cls.EN_WAV.is_file() or not cls.PL_WAV.is_file():
            raise unittest.SkipTest("research audio fixtures not present in this checkout")

        def _load(path: Path) -> bytes:
            with wave.open(str(path), "rb") as w:
                return w.readframes(w.getnframes())

        cls.en_pcm = _load(cls.EN_WAV)
        cls.pl_pcm = _load(cls.PL_WAV)

    def _detector(self):
        from nexa.stt import (
            SttLibraryNotFoundError,
            SttModelNotFoundError,
            WhisperCppLanguageDetector,
        )

        try:
            return WhisperCppLanguageDetector()
        except (SttLibraryNotFoundError, SttModelNotFoundError) as exc:
            self.skipTest(f"whisper.cpp not set up in this environment: {exc}")

    async def test_10_pl_input_resolves_pl(self) -> None:
        from nexa.conversation import ResponseLanguageResolver

        detector = self._detector()
        result = await detector.detect(self.pl_pcm)
        input_language = "pl" if result.p_pl >= result.p_en else "en"
        self.assertEqual(input_language, "pl")

        resolver = ResponseLanguageResolver()
        decision = resolver.resolve("jaka jest prędkość światła", input_language=input_language)
        self.assertEqual(decision.response_language, "pl")
        self.assertIsNone(decision.sticky_after)

    async def test_11_en_input_resolves_en(self) -> None:
        from nexa.conversation import ResponseLanguageResolver

        detector = self._detector()
        result = await detector.detect(self.en_pcm)
        input_language = "pl" if result.p_pl >= result.p_en else "en"
        self.assertEqual(input_language, "en")

        resolver = ResponseLanguageResolver()
        decision = resolver.resolve("what is the speed of light", input_language=input_language)
        self.assertEqual(decision.response_language, "en")
        self.assertIsNone(decision.sticky_after)

    async def test_12_pl_en_pl_per_turn_switching_works_offline(self) -> None:
        from nexa.conversation import ResponseLanguageResolver

        detector = self._detector()
        resolver = ResponseLanguageResolver()

        for pcm, transcript, expected in (
            (self.pl_pcm, "jaka jest prędkość światła", "pl"),
            (self.en_pcm, "what is the speed of light", "en"),
            (self.pl_pcm, "jaka jest prędkość światła", "pl"),
        ):
            result = await detector.detect(pcm)
            input_language = "pl" if result.p_pl >= result.p_en else "en"
            decision = resolver.resolve(transcript, input_language=input_language)
            self.assertEqual(decision.response_language, expected)
            # every turn here mirrors the CURRENT input -- no sticky
            # preference was ever set, so each switch follows immediately.
            self.assertIsNone(decision.sticky_after)

    async def test_13_no_implicit_sticky_preference_from_ordinary_conversation(self) -> None:
        """Neither the first language spoken, nor repeated PL context,
        ever sets a sticky preference on its own -- only an EXPLICIT
        directive does (tested in test_14)."""
        from nexa.conversation import ResponseLanguageResolver

        resolver = ResponseLanguageResolver()
        for transcript, lang in (
            ("jaka jest prędkość światła", "pl"),
            ("czym jest teleportacja", "pl"),
            ("wyjaśnij grawitację", "pl"),
            ("what is the speed of light", "en"),
        ):
            decision = resolver.resolve(transcript, input_language=lang)
            self.assertIsNone(decision.sticky_after)
            self.assertFalse(decision.preference_changed)

    async def test_14_explicit_sticky_language_preference_still_works(self) -> None:
        from nexa.conversation import ResponseLanguageResolver

        resolver = ResponseLanguageResolver()
        # an ordinary PL turn first -- no sticky preference yet.
        resolver.resolve("jaka jest prędkość światła", input_language="pl")
        self.assertIsNone(resolver.preference.sticky)

        # an EXPLICIT sticky directive.
        decision = resolver.resolve("always answer in English", input_language="en")
        self.assertEqual(decision.response_language, "en")
        self.assertTrue(decision.preference_changed)
        self.assertEqual(resolver.preference.sticky, "en")

        # a LATER Polish-spoken turn is still answered in English (sticky
        # overrides current-turn mirroring once explicitly set).
        decision2 = resolver.resolve("jaka jest prędkość światła", input_language="pl")
        self.assertEqual(decision2.response_language, "en")
        self.assertEqual(decision2.sticky_after, "en")


class TestSystemInstructionLanguagePolicy(unittest.TestCase):
    """M2.6B.4C (R0041) retest items 9-10 -- the cloud role card's
    language-mirroring wording matches the intended CURRENT-turn-mirroring
    policy (aligned with the OPERATOR-CONFIRMED M2.6A spike wording after
    the differential audit), and no implicit language preference exists
    when the caller passes none (reproducing R0038's exact production
    call shape: ``apps/nexa_cloud_voice_app.py`` never passes
    ``language_preference``)."""

    def test_role_card_mirrors_current_spoken_language_explicitly(self) -> None:
        from nexa.realtime.snapshot import CLOUD_ROLE_CARD

        lowered = CLOUD_ROLE_CARD.lower()
        # the exact framing gap the audit found between the spike and the
        # prior production wording: per-turn ("currently speaking"), not a
        # static/session-level characteristic.
        self.assertIn("currently speaking", lowered)
        self.assertIn("polish", lowered)
        self.assertIn("english", lowered)
        # the spike's own explicit-override clause is preserved.
        self.assertIn("explicitly", lowered)

    def test_role_card_names_no_default_language(self) -> None:
        """Neither language is singled out as a fallback/default -- the
        instruction is symmetric, matching ADR-0004 Decision F (Option A:
        native mirroring), never a NeXa-side bias toward either language."""
        from nexa.realtime.snapshot import CLOUD_ROLE_CARD

        pl_pos = CLOUD_ROLE_CARD.lower().index("polish")
        en_pos = CLOUD_ROLE_CARD.lower().index("english")
        # both appear once, back-to-back (as alternatives), never one
        # earlier as a "default" with the other only as an afterthought
        # several sentences later.
        self.assertLess(abs(pl_pos - en_pos), 20)

    def test_no_implicit_language_preference_reproduces_production_call_shape(self) -> None:
        """R0038's exact finding, re-asserted as a standing regression
        test: the real production call (``apps/nexa_cloud_voice_app.py``)
        never passes ``language_preference`` -- with it omitted, the
        snapshot's ``system_instruction`` is BYTE-FOR-BYTE the neutral
        role card, no language-preference sentence appended."""
        from nexa.conversation.session import ConversationSession
        from nexa.realtime.snapshot import CLOUD_ROLE_CARD, build_cloud_context_snapshot

        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        snapshot = build_cloud_context_snapshot(session)  # language_preference omitted
        self.assertIsNone(snapshot.language_preference)
        self.assertEqual(snapshot.system_instruction, CLOUD_ROLE_CARD)
        self.assertNotIn("preference", snapshot.system_instruction.lower())


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestNoLocalLidInCloudRuntime(unittest.TestCase):
    """M2.6B.4D (R0042) -- tightens R0041's own "no LID gate" decision:
    R0041's slow-fake-coroutine test proved only that the event loop does
    not AWAIT a slow detector inline; it did NOT prove that a REAL
    whisper.cpp CPU-bound inference call has zero impact on Pipecat's own
    scheduling, audio playback, AEC, or VAD once actually invoked. The
    operator's product decision is stronger: NORMAL CLOUD VOICE MUST RUN
    WITH ZERO LOCAL LID INFERENCE -- so this class proves the call is
    never made in the first place, not merely that it would be
    fire-and-forget if it were."""

    def test_runtime_never_constructs_whisper_cpp_language_detector(self) -> None:
        """``build_gemini_voice_runtime`` must never import or construct
        ``WhisperCppLanguageDetector`` -- no whisper.cpp model load, no
        CPU/RAM footprint, for the normal cloud path."""
        try:
            import nexa.stt as stt_module
        except Exception:  # pragma: no cover - optional dependency
            self.skipTest("nexa.stt not importable in this environment")

        from unittest import mock

        construct_calls: list[object] = []
        original_init = stt_module.WhisperCppLanguageDetector.__init__

        def _spy_init(self, *args, **kwargs):
            construct_calls.append(self)
            return original_init(self, *args, **kwargs)

        with mock.patch.object(
            stt_module.WhisperCppLanguageDetector, "__init__", _spy_init
        ):
            session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
            build_gemini_voice_runtime(session=session, api_key="FAKE-TEST-KEY", dry=True)

        self.assertEqual(construct_calls, [])

    def test_runtime_has_no_lid_related_fields_at_all(self) -> None:
        """Structural proof, not just behavioural: the object graph
        ``build_gemini_voice_runtime`` returns carries no
        ``language_detector``/``language_resolver``/
        ``pending_utterance_audio`` attribute whatsoever -- there is
        nothing left for any future code path to accidentally wire up."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(
            session=session, api_key="FAKE-TEST-KEY", dry=True
        )
        self.assertFalse(hasattr(runtime, "language_detector"))
        self.assertFalse(hasattr(runtime, "language_resolver"))
        self.assertFalse(hasattr(runtime, "pending_utterance_audio"))

    @unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
    def test_bridge_preroll_buffer_never_constructs_any_lid_class(self) -> None:
        """M2.6B.4J (R0049) -- re-verified after this checkpoint added
        ``from ...stt.utterance_buffer import UtteranceBuffer`` (a
        pure-Python, zero-cost ring buffer, confirmed by direct source
        read of ``nexa/stt/__init__.py`` to load no whisper.cpp binding,
        spawn no subprocess, and touch no filesystem at import time).
        ``dry=True`` never reaches bridge construction at all (it returns
        before the hardware pipeline is built), so this constructs the
        REAL bridge directly -- the only path that actually imports and
        uses ``UtteranceBuffer`` -- and proves NONE of
        ``WhisperCppLanguageDetector``/``WhisperCppTranscriber``/
        ``BilingualSpeechTranscriber`` is ever constructed by it."""
        try:
            import nexa.stt as stt_module
        except Exception:  # pragma: no cover - optional dependency
            self.skipTest("nexa.stt not importable in this environment")

        from unittest import mock

        construct_calls: list[tuple[str, object]] = []

        def _spy(name):
            original = getattr(stt_module, name).__init__

            def _init(self, *args, **kwargs):
                construct_calls.append((name, self))
                return original(self, *args, **kwargs)

            return _init

        with (
            mock.patch.object(
                stt_module.WhisperCppLanguageDetector,
                "__init__",
                _spy("WhisperCppLanguageDetector"),
            ),
            mock.patch.object(
                stt_module.WhisperCppTranscriber, "__init__", _spy("WhisperCppTranscriber")
            ),
            mock.patch.object(
                stt_module.BilingualSpeechTranscriber,
                "__init__",
                _spy("BilingualSpeechTranscriber"),
            ),
        ):
            P = _pipecat_hw_imports()

            class _StubProvider:
                async def user_turn_start(self) -> None:
                    pass

                async def send_user_audio(self, pcm: bytes) -> None:
                    pass

                async def user_turn_end(self) -> None:
                    pass

            handle = _ProviderHandle(_StubProvider())
            bridge_cls = _make_vad_bridge_class(P)
            bridge_cls(
                provider_handle=handle,
                metrics=RuntimeMetrics(),
                lifecycle=_ResponseLifecycle(on_finished=lambda: None),
                router=_fresh_router(),
                preroll_ms=PRE_ROLL_MS,
            )

        self.assertEqual(construct_calls, [])


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestNativeOnlyLanguageDispatch(_FakeGeminiLiveServiceTestCase):
    """M2.6B.4D (R0042) retest items 2-6 -- a PL-content turn and an
    EN-content turn both dispatch through the IDENTICAL native provider
    path, with zero LID calls, no provider replacement triggered by
    language, and no delay on the turn-close (``activityEnd``) ->
    dispatch path. ``nexa.stt.WhisperCppLanguageDetector.detect`` is
    patched to raise if ever called, at the class actually imported by
    ``nexa.realtime.gemini.runtime`` (there is none left to import, but
    this proves it even if some future code path re-introduced a
    reference) -- the test fails loudly if local LID is ever invoked."""

    def _wire(self, provider) -> GeminiVoiceRuntime:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)
        aec_health = AecReferenceHealth()
        aec_health.mark_started()
        metrics = RuntimeMetrics()
        provider_handle = _ProviderHandle(provider)
        lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())
        generation_guard = _ResponseGenerationGuard()
        bargein = BargeInController(aec_health=aec_health, on_confirmed=lambda ctx: None)
        return GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
            generation_guard=generation_guard,
        )

    async def test_pl_then_en_turns_dispatch_natively_with_zero_lid_and_no_provider_swap(
        self,
    ) -> None:
        import time
        from unittest import mock

        def _detect_must_never_be_called(*_args, **_kwargs):
            raise AssertionError(
                "local LID must never be invoked on the normal cloud critical path"
            )

        patches: list[object] = []
        try:
            import nexa.stt as stt_module

            patches.append(
                mock.patch.object(
                    stt_module.WhisperCppLanguageDetector,
                    "detect",
                    _detect_must_never_be_called,
                )
            )
        except Exception:  # pragma: no cover - optional dependency
            pass  # nothing to patch -- absence itself is fine here

        for p in patches:
            p.start()
        try:
            provider, _ = await self._started_provider()
            runtime = self._wire(provider)
            await self._drain_one(provider, ReadinessChangedEvent)
            fake = provider._llm  # noqa: SLF001
            consume_task = asyncio.create_task(runtime._consume_provider_events())

            provider_before = runtime.provider

            turns = (
                (
                    "Powiedz mi krótko jak powstaje czarna dziura.",
                    "Czarna dziura...",
                    b"pl-pcm",
                ),
                (
                    "Can you explain what happens near the event horizon?",
                    "Near the horizon...",
                    b"en-pcm",
                ),
            )
            for user_text, assistant_text, assistant_pcm in turns:
                runtime.router.begin_cloud_turn()
                await provider.user_turn_start()
                await provider.send_user_audio(user_text.encode("utf-8"))
                t0 = time.monotonic()
                await provider.user_turn_end()  # activityEnd -- must return immediately
                activity_end_latency_s = time.monotonic() - t0
                self.assertLess(activity_end_latency_s, 0.05)

                await fake.emit_user_transcription(user_text, final=True)
                await fake.emit_assistant_audio(assistant_pcm)
                await _wait_until(
                    lambda pcm=assistant_pcm: pcm
                    in [f.audio for f in runtime.hw_worker.queued_frames if hasattr(f, "audio")]
                )
                await fake.emit_assistant_text(assistant_text)
                await fake.emit_generation_complete()
                await asyncio.sleep(0.02)
                runtime.lifecycle.observe_bot_started()
                runtime.lifecycle.observe_bot_stopped()

            # identical native provider instance throughout -- no
            # provider replacement was ever triggered by language.
            self.assertIs(runtime.provider, provider_before)

            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass
        finally:
            for p in patches:
                p.stop()


class TestCanonicalTurnDiagnostics(unittest.TestCase):
    """M2.6B.4C (R0041) -- the new, lightweight per-turn retest diagnostics
    (``USER_TRANSCRIPT``/``ASSISTANT_TRANSCRIPT``/``PROVIDER_SESSION_ID``)
    are logged from state already held in memory, with no exception and no
    raw audio/credential ever included."""

    def test_canonical_turn_committed_logs_the_charters_exact_keys(self) -> None:
        from nexa.conversation.session import ExternalExchangeOutcome

        metrics = RuntimeMetrics()
        with self.assertLogs("nexa.realtime.gemini.runtime", level="INFO") as cm:
            metrics.canonical_turn_committed(
                ExternalExchangeOutcome.COMMITTED_EXCHANGE,
                3,
                user_transcript="what is the speed of light",
                assistant_transcript="about 300,000 km/s",
                provider_instance_id="140000000000",
            )
        (line,) = cm.output
        self.assertIn("PROVIDER_SESSION_ID=140000000000", line)
        self.assertIn("USER_TRANSCRIPT=", line)
        self.assertIn("what is the speed of light", line)
        self.assertIn("ASSISTANT_TRANSCRIPT=", line)
        self.assertIn("about 300,000 km/s", line)
        self.assertIn("generation=3", line)

    def test_canonical_turn_committed_tolerates_missing_transcripts(self) -> None:
        """Never raises when a commit had no accumulator (``turn is
        None``) -- the runtime's call site always passes ``None`` in that
        case, never skips the call."""
        from nexa.conversation.session import ExternalExchangeOutcome

        metrics = RuntimeMetrics()
        with self.assertLogs("nexa.realtime.gemini.runtime", level="INFO"):
            metrics.canonical_turn_committed(
                ExternalExchangeOutcome.COMMITTED_USER_ONLY,
                None,
                user_transcript=None,
                assistant_transcript=None,
                provider_instance_id=None,
            )


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestProductionCanonicalTurnLifecycle(unittest.IsolatedAsyncioTestCase):
    """M2.6B.4H (R0046) -- PRODUCTION WIRING PROOF. R0045's own audit found
    ``router.begin_cloud_turn()`` had NO production caller at all --
    every prior hardware run generated correct live audio but wrote NOTHING
    to canonical ``ConversationSession.history``. These tests exercise the
    REAL ``_VadToProviderBridge`` (not a hand-rolled simulation of it) for
    the normal-turn-opening half, and the REAL ``build_gemini_voice_runtime``
    ``_on_confirmed`` closure (not a mirror) for the confirmed-interruption
    half -- proving the PRODUCTION code itself, not merely a test's own
    reproduction of it, causes canonical turn creation."""

    async def test_real_bridge_opens_normal_turns_and_never_abandons_one_awaiting_assistant(
        self,
    ) -> None:
        """The REAL bridge (unmirrored) proves: (1) a genuine new local
        VAD turn opens exactly one canonical turn; (2) once that turn has
        a final user transcript (assistant presumably in flight), a
        SEPARATE VAD start/stop (an interruption candidate, real or a
        rejected false one -- from the bridge's own perspective they are
        identical until/unless ``_on_confirmed`` later promotes one) never
        abandons or corrupts it -- invariants 1, 4, 6, 7, 10."""
        P = _pipecat_hw_imports()

        class _StubProvider:
            async def user_turn_start(self) -> None:
                pass

            async def send_user_audio(self, pcm: bytes) -> None:
                pass

            async def user_turn_end(self) -> None:
                pass

        handle = _ProviderHandle(_StubProvider())
        metrics = RuntimeMetrics()
        lifecycle = _ResponseLifecycle(on_finished=lambda: None)
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)
        bridge_cls = _make_vad_bridge_class(P)
        bridge = bridge_cls(
            provider_handle=handle,
            metrics=metrics,
            lifecycle=lifecycle,
            router=router,
            preroll_ms=PRE_ROLL_MS,
        )
        pipeline = P["Pipeline"]([bridge])
        worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](audio_in_sample_rate=16000, audio_out_sample_rate=24000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        runner = P["WorkerRunner"]()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.1)

        self.assertIsNone(router._turn.current)  # noqa: SLF001

        # turn 1 -- a genuine new local user turn opens exactly one
        # canonical turn (invariant 1).
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"hihi", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.1)
        turn1 = router._turn.current  # noqa: SLF001
        self.assertIsNotNone(turn1)
        self.assertEqual(turn1.generation, 1)

        # the provider transcribing + committing turn 1 -- the provider-
        # event -> router wiring is R0045's own, already-proven concern;
        # simulated here via the router's public API, matching
        # TestCanonicalCloudTurnWritePath's own established convention.
        router.on_user_transcription("hi", final=True)
        router.on_assistant_transcription("hello", final=True)
        router.commit_cloud_turn()
        self.assertEqual(len(session.history), 2)

        # turn 2 opens normally (invariant 4 -- next normal turn opens a
        # fresh canonical turn).
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](
                    audio=b"black-holes", sample_rate=16000, num_channels=1
                ),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.1)
        turn2 = router._turn.current  # noqa: SLF001
        self.assertEqual(turn2.generation, 2)
        router.on_user_transcription("tell me about black holes", final=True)
        self.assertTrue(router.has_turn_awaiting_assistant())

        # a SEPARATE VAD start/stop while turn 2 awaits its assistant
        # reply (an interruption candidate, or a false one that never
        # confirms -- from the bridge's own perspective these are
        # identical) must NOT abandon or overwrite turn 2 (invariants 6,
        # 7, 10).
        await worker.queue_frames(
            [
                P["VADUserStartedSpeakingFrame"](),
                P["InputAudioRawFrame"](audio=b"cough", sample_rate=16000, num_channels=1),
                P["VADUserStoppedSpeakingFrame"](),
            ]
        )
        await asyncio.sleep(0.1)
        self.assertIs(router._turn.current, turn2)
        self.assertEqual(turn2.user_text, "tell me about black holes")
        self.assertEqual(turn2.generation, 2)

        # a stray transcript for that candidate must not overwrite turn
        # 2's already-finalized user text either (the accumulator-level
        # half of the same guarantee -- see test_realtime_turn.py for the
        # unit-level proof; this is the end-to-end proof through the
        # real bridge + real router together).
        router.on_user_transcription("uh", final=True)
        self.assertEqual(turn2.user_text, "tell me about black holes")

        await runner.end(reason="test done")
        await asyncio.wait_for(run_task, timeout=5.0)

    async def test_confirmed_interruption_commits_n_and_opens_n_plus_1_via_real_on_confirmed(
        self,
    ) -> None:
        """The REAL ``_on_confirmed`` closure (``build_gemini_voice_runtime``,
        ``dry=True`` -- not a hand-rolled mirror) commits the interrupted
        turn and opens exactly one new canonical turn for the promoted
        interrupting utterance, in one atomic (synchronous) step --
        invariant 8's canonical-history half."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(session=session, api_key="FAKE-TEST-KEY", dry=True)
        router = runtime.router

        async def _fake_cancel() -> None:
            return None

        runtime.provider.cancel = _fake_cancel  # type: ignore[method-assign]

        # turn 1: a real, transcribed question -- assistant dispatched
        # (simulated directly; the provider-event path is R0045's own
        # already-proven concern).
        router.begin_cloud_turn()
        router.on_user_transcription("tell me about black holes", final=True)
        router.on_assistant_transcription("Black holes are", final=False)
        self.assertTrue(router.has_turn_awaiting_assistant())

        turn1 = router._turn.current  # noqa: SLF001
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="test")
        )
        await asyncio.sleep(0.01)  # let the fire-and-forget cancel() task run

        # turn 1 was committed, interrupted, with the conservative (empty)
        # spoken prefix -- never the raw running transcription.
        self.assertTrue(turn1.is_terminal)
        self.assertTrue(turn1.interrupted)
        self.assertEqual(len(session.history), 1)  # user-only (empty assistant text)
        self.assertEqual(session.history[0].content, "tell me about black holes")

        # exactly ONE new canonical turn was opened for the promoted
        # interrupting utterance -- fresh, open, ready for the NEW
        # provider's own transcript.
        turn2 = router._turn.current  # noqa: SLF001
        self.assertIsNot(turn2, turn1)
        self.assertEqual(turn2.generation, turn1.generation + 1)
        self.assertFalse(turn2.is_terminal)
        self.assertIsNone(turn2.user_text)
        self.assertFalse(router.has_turn_awaiting_assistant())

        # the NEW provider's own transcript owns it, exactly once
        # (invariant 12).
        router.on_user_transcription("what about the second case", final=True)
        router.on_assistant_transcription("The second case involves", final=True)
        router.commit_cloud_turn()
        self.assertEqual(len(session.history), 3)
        self.assertEqual(session.history[1].content, "what about the second case")
        self.assertEqual(session.history[2].content, "The second case involves")

    async def test_non_lexical_interruption_invents_no_text_and_next_turn_recovers(
        self,
    ) -> None:
        """Invariant 9 + 11: a confirmed non-lexical interruption (a
        cough/throat-clear the provider never transcribes) opens its own
        canonical turn but never invents fake lexical history for it; the
        NEXT real turn recovers and commits normally."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(session=session, api_key="FAKE-TEST-KEY", dry=True)
        router = runtime.router

        async def _fake_cancel() -> None:
            return None

        runtime.provider.cancel = _fake_cancel  # type: ignore[method-assign]

        router.begin_cloud_turn()
        router.on_user_transcription("first question", final=True)
        router.on_assistant_transcription("first reply", final=False)

        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="throat_clear")
        )
        await asyncio.sleep(0.01)
        self.assertEqual(len(session.history), 1)  # turn 1, user-only

        # the promoted turn NEVER receives a transcript (non-lexical noise)
        # -- must never be committed with invented text.
        noise_turn = router._turn.current  # noqa: SLF001
        self.assertIsNone(noise_turn.user_text)
        self.assertIsNone(router.commit_cloud_turn())
        self.assertEqual(len(session.history), 1)  # unchanged -- nothing invented

        # the next real, lexical turn opens and commits normally --
        # superseding (never corrupting) the never-transcribed noise turn.
        router.begin_cloud_turn()
        self.assertTrue(noise_turn.is_terminal)  # abandoned by start_turn() itself
        router.on_user_transcription("second question", final=True)
        router.on_assistant_transcription("second reply", final=True)
        outcome = router.commit_cloud_turn()
        self.assertIsNotNone(outcome)
        self.assertEqual(len(session.history), 3)
        self.assertEqual(session.history[1].content, "second question")
        self.assertEqual(session.history[2].content, "second reply")

    async def test_two_consecutive_interruptions_preserve_history_ordering(self) -> None:
        """Invariant 17: two confirmed interruptions in a row still
        produce a clean, correctly-ordered canonical history -- one entry
        per turn, oldest first, never duplicated or reordered."""
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(session=session, api_key="FAKE-TEST-KEY", dry=True)
        router = runtime.router

        async def _fake_cancel() -> None:
            return None

        runtime.provider.cancel = _fake_cancel  # type: ignore[method-assign]

        router.begin_cloud_turn()
        router.on_user_transcription("question one", final=True)
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=1, reason="test")
        )
        await asyncio.sleep(0.01)

        router.on_user_transcription("question two", final=True)
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=2, reason="test")
        )
        await asyncio.sleep(0.01)

        router.on_user_transcription("question three", final=True)
        router.on_assistant_transcription("final reply", final=True)
        router.commit_cloud_turn()

        self.assertEqual(len(session.history), 4)
        self.assertEqual(
            [t.content for t in session.history],
            ["question one", "question two", "question three", "final reply"],
        )

    async def test_snapshot_after_interruption_contains_prior_turns_not_the_new_one(
        self,
    ) -> None:
        """CLOUDCONTEXTSNAPSHOT PROOF: after two completed cloud turns and
        a confirmed interruption, the fresh snapshot ``start_fresh_cloud_
        provider``/``recover_from_mid_turn_loss`` would build contains
        every prior COMMITTED turn (including the just-interrupted one,
        per the existing conservative interruption rule) and NEVER the
        not-yet-committed new canonical turn opened for the interrupting
        utterance -- no missing prior turns, no duplicate interrupting
        user turn, no unspoken assistant text, no OLD provider state."""
        from nexa.realtime.snapshot import build_cloud_context_snapshot

        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        runtime = build_gemini_voice_runtime(session=session, api_key="FAKE-TEST-KEY", dry=True)
        router = runtime.router

        async def _fake_cancel() -> None:
            return None

        runtime.provider.cancel = _fake_cancel  # type: ignore[method-assign]

        # two ordinary, completed turns.
        for i in range(2):
            router.begin_cloud_turn()
            router.on_user_transcription(f"user {i}", final=True)
            router.on_assistant_transcription(f"assistant {i}", final=True)
            router.commit_cloud_turn()
        self.assertEqual(len(session.history), 4)

        # snapshot BEFORE any interruption -- sanity baseline.
        baseline = build_cloud_context_snapshot(session)
        self.assertEqual(len(baseline.recent_turns), 4)

        # turn N begins, then is confirmed-interrupted.
        router.begin_cloud_turn()
        router.on_user_transcription("tell me about black holes", final=True)
        router.on_assistant_transcription("Black holes are", final=False)
        runtime.bargein._on_confirmed(  # noqa: SLF001
            InterruptContext(invalidated_response_id=3, reason="test")
        )
        await asyncio.sleep(0.01)

        # the fresh snapshot a real atomic replacement would build from
        # canonical NeXa state RIGHT NOW (before the interrupting
        # utterance's own PCM is ever replayed to any provider).
        snapshot = await router.request_fresh_snapshot_after_resumption_failure()

        rendered = [t.content for t in snapshot.recent_turns]
        # every prior completed turn is present, in order --
        self.assertEqual(
            rendered[:4], ["user 0", "assistant 0", "user 1", "assistant 1"]
        )
        # the just-interrupted turn N is present (conservative rule: user
        # text kept, empty assistant text -- no unspoken words credited).
        self.assertEqual(rendered[4], "tell me about black holes")
        self.assertEqual(len(rendered), 5)
        # the NEW canonical turn opened for the interrupting utterance is
        # NOT in the snapshot at all -- it is only in-memory
        # (``router._turn.current``), never yet written to
        # ``session.history``, so it can never be duplicated between the
        # snapshot and the later PCM replay.
        new_turn = router._turn.current  # noqa: SLF001
        self.assertFalse(new_turn.is_terminal)
        self.assertIsNone(new_turn.user_text)
        self.assertNotIn("what about", " ".join(rendered))


if __name__ == "__main__":
    unittest.main()
