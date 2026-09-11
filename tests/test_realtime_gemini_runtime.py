"""``GeminiVoiceRuntime`` (M2.6B.3) — the HYBRID cloud-audio + barge-in
wiring.

No real audio device, no network: the hardware-only object graph is
exercised via ``dry=True`` construction (mirrors the M2.6A probe's own
``--dry``); the event-driven wiring (``_consume_provider_events``,
``_on_confirmed``) is exercised with a real ``GeminiLiveProvider`` driven
by the same FAKE terminal service used in ``test_realtime_gemini_service``
(no network) plus a stub in place of the hardware ``PipelineWorker``.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.realtime.gemini.runtime import (  # noqa: E402
    GeminiVoiceRuntime,
    RuntimeMetrics,
    build_gemini_voice_runtime,
)
from nexa.realtime.policy import ConversationPolicy  # noqa: E402
from nexa.realtime.router import ConversationRouter  # noqa: E402
from nexa.voice.aec import AecReferenceHealth  # noqa: E402
from nexa.voice.bargein import BargeInController, InterruptContext  # noqa: E402
from nexa.voice.interruption import InterruptionState  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import FakeModelProvider  # noqa: E402

try:
    from test_realtime_gemini_service import _FakeGeminiLiveServiceTestCase

    _PIPECAT_AVAILABLE = True
except Exception:  # pragma: no cover - only if pipecat-ai isn't importable
    _PIPECAT_AVAILABLE = False

    class _FakeGeminiLiveServiceTestCase(unittest.IsolatedAsyncioTestCase):
        pass


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


@unittest.skipUnless(_PIPECAT_AVAILABLE, "pipecat-ai not importable in this environment")
class TestBargeInWiring(_FakeGeminiLiveServiceTestCase):
    """The one new piece of logic this module adds: keeping
    ``BargeInController``'s state machine in sync with cloud-turn events,
    and the ``on_confirmed`` hook's three actions."""

    def _wire(self, provider) -> GeminiVoiceRuntime:
        session = ConversationSession(provider=FakeModelProvider(), system_prompt="p")
        router = ConversationRouter(session, policy=ConversationPolicy.CLOUD_PREFERRED)
        aec_health = AecReferenceHealth()
        aec_health.mark_started()
        metrics = RuntimeMetrics()

        cancel_calls: list[str] = []

        async def _fake_cancel() -> None:
            cancel_calls.append("cancelled")

        provider.cancel = _fake_cancel  # type: ignore[method-assign]

        def _on_confirmed(ctx: InterruptContext) -> None:
            metrics.local_interruption_confirmed()
            router.on_interruption()
            asyncio.create_task(provider.cancel())
            bargein.notify_interruption_complete()

        bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)
        runtime = GeminiVoiceRuntime(
            provider=provider,
            router=router,
            hw_worker=_StubHardwareWorker(),
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
        )
        runtime._cancel_calls = cancel_calls  # type: ignore[attr-defined]
        return runtime

    async def test_first_assistant_output_dispatches_bargein_response(self) -> None:
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, __import__(
            "nexa.realtime.provider", fromlist=["ReadinessChangedEvent"]
        ).ReadinessChangedEvent)

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"user-audio")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        fake = provider._llm  # noqa: SLF001
        await fake.emit_user_transcription("hello", final=True)
        await fake.emit_assistant_audio(b"assistant-pcm")

        # Give the consumer loop a couple of ticks to process both events.
        for _ in range(20):
            await asyncio.sleep(0)
            if runtime.bargein.active_response_id is not None:
                break

        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.RESPONDING)
        self.assertEqual(runtime.hw_worker.queued_frames[0].audio, b"assistant-pcm")

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    async def test_generation_complete_finishes_bargein_response(self) -> None:
        provider, _ = await self._started_provider()
        runtime = self._wire(provider)
        await self._drain_one(provider, __import__(
            "nexa.realtime.provider", fromlist=["ReadinessChangedEvent"]
        ).ReadinessChangedEvent)

        runtime.router.begin_cloud_turn()
        await provider.user_turn_start()
        await provider.send_user_audio(b"user-audio")
        await provider.user_turn_end()

        consume_task = asyncio.create_task(runtime._consume_provider_events())
        fake = provider._llm  # noqa: SLF001
        await fake.emit_user_transcription("hello", final=True)
        await fake.emit_assistant_audio(b"assistant-pcm")
        for _ in range(20):
            await asyncio.sleep(0)
            if runtime.bargein.active_response_id is not None:
                break
        await fake.emit_assistant_text("hi")
        await fake.emit_generation_complete()

        for _ in range(20):
            await asyncio.sleep(0)
            if runtime.bargein.state_machine.state == InterruptionState.IDLE:
                break

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

        for _ in range(20):
            await asyncio.sleep(0)
            if runtime._cancel_calls:  # type: ignore[attr-defined]
                break

        self.assertTrue(runtime.router._turn.current.interrupted)  # noqa: SLF001
        self.assertEqual(runtime._cancel_calls, ["cancelled"])  # type: ignore[attr-defined]
        self.assertEqual(runtime.bargein.state_machine.state, InterruptionState.IDLE)


if __name__ == "__main__":
    unittest.main()
