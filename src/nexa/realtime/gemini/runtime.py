"""``GeminiVoiceRuntime`` — the production HYBRID audio wiring for cloud
realtime voice (M2.6B.3, ADR-0004 Decisions B/C/H).

Wires the accepted, unchanged local-hardware components (reSpeaker input,
XVF3800 AEC far-end reference, Silero VAD as the local turn authority,
``BargeInController`` as the local barge-in authority) to the headless
``GeminiLiveProvider`` + ``ConversationRouter`` built in M2.6B.1/.2/.2A —
never a parallel architecture, never a second AEC/barge-in implementation.

Two independent Pipecat pipelines run side by side, bridged only by plain
async calls / an event queue — never by sharing frame-processor state:

* the provider's OWN headless pipeline (built inside
  ``GeminiLiveProvider.start()`` — aggregators + the Gemini service, no
  transport of its own, exactly as in M2.6B.2);
* this module's HARDWARE pipeline: ``[transport.input(), vad_processor,
  bargein_controller, _VadToProviderBridge, aec_feeder, transport.output()]``
  — real mic capture, local Silero VAD (server VAD stays OFF on the
  Gemini side, per the frozen M2.6A baseline), the existing
  ``BargeInController`` (unmodified, same class as the local voice path),
  and the existing ``AecReferenceFeeder`` (unmodified) teeing whatever
  audio reaches ``transport.output()`` to the XVF3800 far-end reference —
  identical mechanism to Piper's local TTS output, just fed Gemini's PCM
  instead.

``_VadToProviderBridge`` is the one new piece: it forwards
``VADUserStartedSpeakingFrame`` / ``InputAudioRawFrame`` /
``VADUserStoppedSpeakingFrame`` into ``GeminiLiveProvider.user_turn_start``
/ ``send_user_audio`` / ``user_turn_end`` — and only forwards audio to the
provider *while a locally-detected turn is open*, so the cloud never sees
audio local Silero didn't bracket as speech (ADR-0004 Decision C: local
Silero is the turn authority, not Gemini's own — disabled — server VAD).

Cloud audio is injected into the SAME hardware pipeline via
``worker.queue_frames([TTSAudioRawFrame(...)])`` from a background task
that consumes ``provider.events()`` — exactly the same injection mechanism
(``PipelineWorker.queue_frames``) already used for the one-time
``LLMRunFrame`` kickoff (R0031/M2.6B.2), so it reaches ``aec_feeder`` and
``transport.output()`` like any other TTS audio.

Local barge-in wiring: ``BargeInController.on_confirmed`` -> stop is
already handled by Pipecat's own ``broadcast_interruption()`` (called
internally by ``BargeInController`` — unchanged); the hook here additionally
tells the ROUTER (``router.on_interruption()``) and the PROVIDER
(``provider.cancel()``) — local speaker-stop authority never waits for
either. Spoken-prefix authority (ADR-0004): the assistant text
``CloudTurnAccumulator`` has accumulated up to the moment of interruption
*is* the v1 spoken-prefix truth (the R0034 fix already freezes it there) —
the same precision the accepted local mechanism uses (``SpokenTextTracker``
tracks text *handed to* the output stage, not a sample-accurate DAC
position either); no new tracker was needed or built.

Construction only (``dry=True``): builds every object EXCEPT the audio
device / network — no ``pyaudio.PyAudio()``, no device index lookup, no
Gemini connection. This module has NOT been validated against real
hardware — see ``docs/reports/R0035_...`` for exactly what remains for a
real operator run.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ...voice.aec import AecReferenceHealth
from ...voice.bargein import BargeInController, InterruptContext
from ...voice.config import LocalAudioConfig
from ...voice.device import find_device_index
from ...voice_tts.aec_reference import AecReferenceFeeder
from ..policy import ConversationPolicy
from ..provider import (
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    GenerationCompleteEvent,
    ProviderReadiness,
    RealtimeProviderFailedError,
)
from ..router import ConversationRouter
from ..snapshot import CloudContextSnapshot
from .service import (
    INPUT_SAMPLE_RATE_HZ,
    OUTPUT_SAMPLE_RATE_HZ,
    GeminiLiveProvider,
)
from .voice import DEFAULT_VOICE_PREFERENCE

logger = logging.getLogger(__name__)


def _pipecat_hw_imports() -> dict[str, Any]:
    """Deferred import of the hardware-pipeline-only Pipecat symbols (the
    provider's own imports live in ``service._pipecat_imports``). Never
    called at module import time — this module still requires only the
    optional ``cloud-gemini`` extra at *use* time, same as ``service.py``."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        TTSAudioRawFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from pipecat.workers.runner import WorkerRunner

    return {
        "SileroVADAnalyzer": SileroVADAnalyzer,
        "VADParams": VADParams,
        "InputAudioRawFrame": InputAudioRawFrame,
        "TTSAudioRawFrame": TTSAudioRawFrame,
        "VADUserStartedSpeakingFrame": VADUserStartedSpeakingFrame,
        "VADUserStoppedSpeakingFrame": VADUserStoppedSpeakingFrame,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "VADProcessor": VADProcessor,
        "FrameDirection": FrameDirection,
        "FrameProcessor": FrameProcessor,
        "LocalAudioTransport": LocalAudioTransport,
        "LocalAudioTransportParams": LocalAudioTransportParams,
        "WorkerRunner": WorkerRunner,
    }


def _make_vad_bridge_class(P: dict[str, Any]) -> type:
    """The one new FrameProcessor: local VAD frames -> provider turn I/O.
    Built lazily (needs ``FrameProcessor``, only available post-import)."""

    class _VadToProviderBridge(P["FrameProcessor"]):
        def __init__(self, *, provider: GeminiLiveProvider, metrics: RuntimeMetrics) -> None:
            super().__init__()
            self._provider = provider
            self._metrics = metrics
            self._turn_open = False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["VADUserStartedSpeakingFrame"]):
                self._turn_open = True
                self._metrics.local_vad_start()
                await self._provider.user_turn_start()
            elif isinstance(frame, P["InputAudioRawFrame"]) and self._turn_open:
                await self._provider.send_user_audio(frame.audio)
            elif isinstance(frame, P["VADUserStoppedSpeakingFrame"]):
                self._turn_open = False
                self._metrics.local_vad_eot()
                await self._provider.user_turn_end()
            await self.push_frame(frame, direction)

    return _VadToProviderBridge


@dataclass
class RuntimeMetrics:
    """Lightweight production metrics (ADR-0004 M2.6B charter, Phase 5) —
    logged only, no raw audio retained, no credential ever logged."""

    _first_audio_received_at: float | None = None
    _first_audio_played_marked: bool = False

    def provider_readiness(self, readiness: ProviderReadiness) -> None:
        logger.info("nexa.realtime.metrics: provider readiness -> %s", readiness.value)

    def aec_reference(self, active: bool) -> None:
        logger.info("nexa.realtime.metrics: AEC reference %s", "ACTIVE" if active else "DOWN")

    def local_vad_start(self) -> None:
        logger.info("nexa.realtime.metrics: local VAD start (turn open)")

    def local_vad_eot(self) -> None:
        logger.info("nexa.realtime.metrics: local VAD end-of-turn")

    def input_transcription_final(self, text: str) -> None:
        logger.info(
            "nexa.realtime.metrics: input transcription final (%d chars)", len(text)
        )

    def first_assistant_audio_received(self) -> None:
        logger.info("nexa.realtime.metrics: first assistant audio received")

    def first_assistant_audio_played(self) -> None:
        logger.info("nexa.realtime.metrics: first assistant audio played")

    def local_interruption_confirmed(self) -> None:
        logger.info("nexa.realtime.metrics: local interruption CONFIRMED")

    def provider_interruption_ack(self) -> None:
        logger.info("nexa.realtime.metrics: provider (server) interruption ACK received")

    def canonical_turn_committed(self, outcome: Any, generation: int | None) -> None:
        logger.info(
            "nexa.realtime.metrics: canonical cloud turn committed outcome=%s generation=%s",
            outcome, generation,
        )

    def reconnecting(self, reason: str) -> None:
        logger.info("nexa.realtime.metrics: reconnecting (%s)", reason)

    def resumed(self) -> None:
        logger.info("nexa.realtime.metrics: resumed")

    def inbound_buffer_state(self, *, buffered: int, dropped: int) -> None:
        logger.info(
            "nexa.realtime.metrics: inbound buffer buffered=%d dropped=%d", buffered, dropped
        )

    def usage(self, event: Any) -> None:
        logger.info(
            "nexa.realtime.metrics: usage in_text=%s in_audio=%s out_text=%s out_audio=%s",
            event.input_text_tokens, event.input_audio_tokens,
            event.output_text_tokens, event.output_audio_tokens,
        )


@dataclass
class GeminiVoiceRuntime:
    """Holds every object this module constructs — the app starts/stops
    it as a unit."""

    provider: GeminiLiveProvider
    router: ConversationRouter
    hw_worker: Any
    hw_runner: Any
    aec_health: AecReferenceHealth
    bargein: BargeInController
    metrics: RuntimeMetrics
    #: Optional operator/observability hook, called once per event
    #: *from the same single consumption loop* that drives the canonical
    #: write path -- ``provider.events()`` is backed by ONE
    #: ``asyncio.Queue`` (single-consumer by construction); a second
    #: independent reader would race this loop for events and could steal
    #: ones the router needs to commit a turn. Never add a second
    #: ``provider.events()`` consumer -- hook in here instead.
    on_event: Any = None
    _events_task: asyncio.Task | None = None
    _hw_run_task: asyncio.Task | None = None

    async def start(self, snapshot: CloudContextSnapshot) -> None:
        await self.provider.start(snapshot)
        self.metrics.provider_readiness(self.provider.readiness)
        await self.hw_runner.add_workers(self.hw_worker)
        self._hw_run_task = asyncio.create_task(self.hw_runner.run())
        self._events_task = asyncio.create_task(self._consume_provider_events())

    async def _consume_provider_events(self) -> None:
        """Drain ``provider.events()`` and (1) drive the canonical write
        path via the router's one true entry point, (2) keep
        ``BargeInController``'s own state machine in sync so it actually
        admits a candidate (it only does so while
        ``response_in_flight`` — i.e. between a ``notify_response_dispatched``
        and a ``notify_response_finished``/``notify_interruption_complete``,
        exactly like the local voice path), and (3) inject assistant audio
        into the hardware pipeline for playback + the AEC tee."""
        P = _pipecat_hw_imports()
        first_audio_seen = False
        response_dispatched = False
        async for event in self.provider.events():
            if self.on_event is not None:
                try:
                    self.on_event(event)
                except Exception:  # noqa: BLE001 - an observer must never break audio
                    logger.exception("nexa.realtime.gemini.runtime: on_event hook raised")

            if isinstance(
                event, (AssistantAudioEvent, AssistantTranscriptionEvent)
            ) and not response_dispatched:
                response_dispatched = True
                self.bargein.notify_response_dispatched()

            outcome = self.router.handle_provider_event(event)

            if isinstance(event, GenerationCompleteEvent):
                response_dispatched = False
                self.bargein.notify_response_finished()
            elif isinstance(event, RealtimeProviderFailedError):
                response_dispatched = False

            if outcome is not None:
                turn = self.router._turn.current  # noqa: SLF001 - metrics only
                self.metrics.canonical_turn_committed(
                    outcome, turn.generation if turn else None
                )
            if isinstance(event, AssistantAudioEvent):
                if not first_audio_seen:
                    first_audio_seen = True
                    self.metrics.first_assistant_audio_received()
                frame = P["TTSAudioRawFrame"](
                    audio=event.pcm, sample_rate=OUTPUT_SAMPLE_RATE_HZ, num_channels=1
                )
                await self.hw_worker.queue_frames([frame])

    async def stop(self, *, reason: str) -> None:
        if self._events_task is not None:
            self._events_task.cancel()
            self._events_task = None
        await self.provider.stop(reason=reason)
        try:
            await self.hw_runner.end(reason=reason)
        except Exception as exc:  # noqa: BLE001 — best-effort teardown
            logger.warning(
                "nexa.realtime.gemini.runtime: error stopping hardware pipeline: %r", exc
            )


def build_gemini_voice_runtime(
    *,
    session,
    api_key: str,
    audio_config: LocalAudioConfig | None = None,
    voice_preference: str = DEFAULT_VOICE_PREFERENCE,
    policy: ConversationPolicy = ConversationPolicy.CLOUD_PREFERRED,
    on_event: Any = None,
    dry: bool = False,
) -> GeminiVoiceRuntime:
    """Construct the full HYBRID cloud-voice runtime.

    ``dry=True`` builds every object EXCEPT the audio device / network:
    validates the object graph (mirrors the M2.6A probe's own ``--dry``),
    no ``pyaudio.PyAudio()`` call, no device index lookup, no Gemini
    connection. Use it to catch construction errors before ever touching
    hardware or spending Gemini quota.
    """
    cfg = audio_config or LocalAudioConfig()
    metrics = RuntimeMetrics()

    provider = GeminiLiveProvider(api_key=api_key, voice_preference=voice_preference)

    def _cloud_factory() -> GeminiLiveProvider:
        return GeminiLiveProvider(api_key=api_key, voice_preference=voice_preference)

    router = ConversationRouter(
        session, policy=policy, cloud_provider_factory=_cloud_factory
    )

    aec_health = AecReferenceHealth(on_change=metrics.aec_reference)

    def _on_confirmed(ctx: InterruptContext) -> None:
        metrics.local_interruption_confirmed()
        # Local speaker-stop authority already happened: BargeInController
        # calls Pipecat's own broadcast_interruption() (which tears down
        # queued/playing output audio) BEFORE this hook runs, and that call
        # never waits on anything below -- the R0034 late-server-event
        # protections stay in force regardless of what the cloud side does.
        router.on_interruption()
        # The router's CloudTurnAccumulator has just frozen assistant_text
        # (whatever transcription deltas were handed to output before this
        # moment, i.e. the R0034 spoken-prefix mechanism) and will refuse
        # any further assistant-transcription deltas for this turn --
        # architecturally the same precision as the local SpokenTextTracker,
        # reused rather than forked (ADR-0004).
        # provider.cancel() is async; this hook is sync (BargeInController's
        # contract) -- fire-and-forget is correct: cancellation reaching the
        # cloud side is not on the critical path for local speaker-stop.
        asyncio.create_task(provider.cancel())
        # Cloud turns need no segment-coalescing capture phase (unlike the
        # local path): the interrupting utterance is simply the next local
        # VAD turn the bridge opens. Exit INTERRUPTING immediately so
        # BargeInController is ready to admit a future interruption again.
        bargein.notify_interruption_complete()

    bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)

    if dry:
        return GeminiVoiceRuntime(
            provider=provider,
            router=router,
            hw_worker=None,
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            on_event=on_event,
        )

    P = _pipecat_hw_imports()
    import pyaudio

    pa = pyaudio.PyAudio()
    in_idx = find_device_index(pa, cfg.input_device_name, require_input=True)
    out_idx = find_device_index(pa, cfg.output_device_name, require_output=True)

    transport = P["LocalAudioTransport"](
        P["LocalAudioTransportParams"](
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ,
            audio_out_sample_rate=OUTPUT_SAMPLE_RATE_HZ,
            audio_in_channels=1,
            audio_out_channels=1,
            input_device_index=in_idx,
            output_device_index=out_idx,
        )
    )
    vad_analyzer = P["SileroVADAnalyzer"](
        sample_rate=INPUT_SAMPLE_RATE_HZ, params=P["VADParams"](stop_secs=0.5)
    )
    vad_processor = P["VADProcessor"](vad_analyzer=vad_analyzer)
    bridge_cls = _make_vad_bridge_class(P)
    bridge = bridge_cls(provider=provider, metrics=metrics)
    aec_feeder = AecReferenceFeeder(
        aec_health=aec_health, sample_rate=OUTPUT_SAMPLE_RATE_HZ, channels=1
    )

    pipeline = P["Pipeline"](
        [
            transport.input(),
            vad_processor,
            bargein,
            bridge,
            aec_feeder,
            transport.output(),
        ]
    )
    hw_worker = P["PipelineWorker"](
        pipeline,
        params=P["PipelineParams"](
            audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ,
            audio_out_sample_rate=OUTPUT_SAMPLE_RATE_HZ,
        ),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    hw_runner = P["WorkerRunner"]()

    return GeminiVoiceRuntime(
        provider=provider,
        router=router,
        hw_worker=hw_worker,
        hw_runner=hw_runner,
        aec_health=aec_health,
        bargein=bargein,
        metrics=metrics,
        on_event=on_event,
    )
