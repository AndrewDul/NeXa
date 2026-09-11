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
    _ProviderHandle,
    _ResponseLifecycle,
    build_gemini_voice_runtime,
)
from nexa.realtime.policy import ActiveProvider, ConversationPolicy  # noqa: E402
from nexa.realtime.router import ConversationRouter  # noqa: E402
from nexa.voice.aec import AecReferenceHealth  # noqa: E402
from nexa.voice.bargein import BargeInController, InterruptContext  # noqa: E402
from nexa.voice.interruption import InterruptionState  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import FakeModelProvider  # noqa: E402

try:
    from nexa.realtime.gemini.service import GeminiLiveProvider
    from nexa.realtime.provider import GenerationCompleteEvent, ReadinessChangedEvent
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

        def _on_confirmed(ctx: InterruptContext) -> None:
            metrics.local_interruption_confirmed()
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


if __name__ == "__main__":
    unittest.main()
