"""``GeminiLiveProvider`` — the production ``RealtimeVoiceProvider``
implementation wrapping Pipecat 1.8.1's ``GeminiLiveLLMService`` (ADR-0004
Decision C, "Option C").

Frozen v1 baseline (unchanged from the M2.6A spike, `R0031`):
model ``gemini-3.1-flash-live-preview``, voice via the provider-agnostic
voice-preference mapping (default `Sulafat`), 16 kHz PCM in / 24 kHz PCM
out, server VAD OFF (NeXa's local Silero stays the turn authority), context
window compression enabled.

Startup sequencing follows the M2.6B.1 source audit exactly
(`docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`):
``system_instruction`` is supplied once, at construction
(``CloudContextSnapshot.system_instruction`` — never mutated on an open
connection, Amendment 1 §2); the initial ``LLMContext`` is seeded from
``snapshot.recent_turns``; ``inference_on_context_initialization=False``
so no unwanted greeting is generated; exactly ONE ``LLMRunFrame`` is
queued after the pipeline starts (the R0031 kickoff) — Pipecat's own
``_create_initial_response()`` then performs the Gemini-3.1 one-time
``clientContent`` history seed (Amendment 1 §3) via its own
unconditional ``HistoryConfig(initial_history_in_client_content=True)``
default. This module never re-implements or duplicates that seeding.

This module imports **no** Pipecat/Gemini symbol at module load time
(``_pipecat_imports()`` is called only from ``start()``) — importing
``nexa.realtime.gemini.service`` itself does not require the
``cloud-gemini`` optional dependency extra to be installed; only
*constructing and starting* a ``GeminiLiveProvider`` does.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..provider import (
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    CancellationCompleteEvent,
    ProviderEvent,
    ProviderInterruptionEvent,
    ProviderReadiness,
    ReadinessChangedEvent,
    RealtimeProviderCapabilities,
    RealtimeProviderError,
    RealtimeProviderFailedError,
    RealtimeVoiceProvider,
    UserTranscriptionEvent,
)
from ..reconnect import SessionResumptionHandle
from ..snapshot import CloudContextSnapshot
from ..turn_framing import EnvelopeEventKind, UtteranceFramer
from ..usage import ProviderUsageEvent
from .voice import DEFAULT_VOICE_PREFERENCE, gemini_voice_for_preference

logger = logging.getLogger(__name__)

#: ADR-0004 Decision C — frozen v1 baseline, unchanged from M2.6A.
MODEL = "gemini-3.1-flash-live-preview"
INPUT_SAMPLE_RATE_HZ = 16_000
OUTPUT_SAMPLE_RATE_HZ = 24_000
DEFAULT_READY_TIMEOUT_S = 15.0
DEFAULT_READY_POLL_INTERVAL_S = 0.05


def _pipecat_imports() -> dict[str, Any]:
    """Deferred import of every Pipecat/Gemini symbol this module needs, as
    a name->object map (mirrors the proven M2.6A research-probe pattern,
    `docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py`).
    Never called at module import time."""
    from pipecat.frames.frames import (
        EndFrame,
        InputAudioRawFrame,
        InterruptionFrame,
        LLMFullResponseEndFrame,
        LLMRunFrame,
        TranscriptionFrame,
        TTSAudioRawFrame,
        TTSTextFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.services.google.gemini_live.llm import (
        ContextWindowCompressionParams,
        GeminiLiveLLMService,
        GeminiModalities,
        GeminiVADParams,
    )
    from pipecat.workers.runner import WorkerRunner

    return {
        "EndFrame": EndFrame,
        "InputAudioRawFrame": InputAudioRawFrame,
        "InterruptionFrame": InterruptionFrame,
        "LLMFullResponseEndFrame": LLMFullResponseEndFrame,
        "LLMRunFrame": LLMRunFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "TTSAudioRawFrame": TTSAudioRawFrame,
        "TTSTextFrame": TTSTextFrame,
        "UserStartedSpeakingFrame": UserStartedSpeakingFrame,
        "UserStoppedSpeakingFrame": UserStoppedSpeakingFrame,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "LLMContext": LLMContext,
        "LLMContextAggregatorPair": LLMContextAggregatorPair,
        "LLMUserAggregatorParams": LLMUserAggregatorParams,
        "FrameDirection": FrameDirection,
        "FrameProcessor": FrameProcessor,
        "ContextWindowCompressionParams": ContextWindowCompressionParams,
        "GeminiLiveLLMService": GeminiLiveLLMService,
        "GeminiModalities": GeminiModalities,
        "GeminiVADParams": GeminiVADParams,
        "WorkerRunner": WorkerRunner,
    }


def _make_event_tap_class(P: dict[str, Any]) -> type:
    """Build the terminal ``FrameProcessor`` subclass that translates
    Pipecat/Gemini frames into NeXa's typed ``ProviderEvent`` stream.

    Built lazily (needs ``FrameProcessor``, only available after
    ``_pipecat_imports()``) rather than defined at module scope, so this
    module still imports with zero Pipecat dependency.
    """

    class _EventTap(P["FrameProcessor"]):
        def __init__(self, *, queue: asyncio.Queue, on_frame: Callable[[Any], None]) -> None:
            super().__init__()
            self._queue = queue
            self._on_frame = on_frame

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            self._on_frame(frame)
            await self.push_frame(frame, direction)

    return _EventTap


def _render_snapshot_messages(snapshot: CloudContextSnapshot) -> list[dict[str, str]]:
    """Render ``snapshot.recent_turns`` as the initial ``LLMContext``
    message list. The role card (``snapshot.system_instruction``) is
    deliberately NOT included here — it goes to the service's own
    ``system_instruction=`` parameter; duplicating it as a context message
    makes Pipecat warn and convert it into a spurious user line (observed
    in the M2.6A probe, R0031)."""
    return [{"role": turn.role.value, "content": turn.content} for turn in snapshot.recent_turns]


class GeminiLiveProvider(RealtimeVoiceProvider):
    """Wraps Pipecat 1.8.1's ``GeminiLiveLLMService`` behind the
    ``RealtimeVoiceProvider`` boundary (ADR-0004 Decision C).

    ``service_class`` is injectable (default: the real
    ``GeminiLiveLLMService``, imported lazily) so deterministic tests can
    substitute a fake ``FrameProcessor`` in its place and exercise the
    REAL Pipecat pipeline/worker/aggregator machinery around it — no
    network call, no real Gemini connection — while production code
    always gets the real class.
    """

    def __init__(
        self,
        *,
        api_key: str,
        voice_preference: str = DEFAULT_VOICE_PREFERENCE,
        service_class: type | None = None,
        ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S,
    ) -> None:
        self._api_key = api_key
        self._voice = gemini_voice_for_preference(voice_preference)
        self._service_class_override = service_class
        self._ready_timeout_s = ready_timeout_s

        self._readiness = ProviderReadiness.CONNECTING
        self._events: asyncio.Queue[ProviderEvent] = asyncio.Queue()
        self._P: dict[str, Any] | None = None
        self._llm: Any = None
        self._worker: Any = None
        self._runner: Any = None
        self._run_task: asyncio.Task | None = None
        self._reconnect_handle: SessionResumptionHandle | None = None
        self._turn_open = False
        #: ADR-0004 Decision H — while ``readiness != READY`` (including a
        #: future reconnect's DEGRADED/RECONNECTING window, M2.6B.3), the
        #: local turn envelope (activityStart / audio / activityEnd) is
        #: captured here instead of being sent, then flushed in order once
        #: READY again. Never bypassed: ``send_user_audio`` etc. always
        #: check ``readiness`` first.
        self._framer = UtteranceFramer()

    def capabilities(self) -> RealtimeProviderCapabilities:
        return RealtimeProviderCapabilities(
            provider_name="gemini_live",
            supports_interruption=True,
            supports_session_resumption=True,
            supports_server_transcription=True,
            supports_function_calling=False,  # OFF in v1 (ADR-0004 Decision N)
            input_sample_rate_hz=INPUT_SAMPLE_RATE_HZ,
            output_sample_rate_hz=OUTPUT_SAMPLE_RATE_HZ,
            max_session_seconds=15 * 60,
        )

    @property
    def readiness(self) -> ProviderReadiness:
        return self._readiness

    def _set_readiness(self, readiness: ProviderReadiness) -> None:
        if readiness == self._readiness:
            return
        self._readiness = readiness
        self._events.put_nowait(ReadinessChangedEvent(readiness=readiness))

    async def _flush_framer(self) -> None:
        """ADR-0004 Decision H — deliver every buffered turn-envelope event
        in order: exactly one ``activity_start``, its audio in order, then
        ``activity_end`` (if it was seen) — never audio before its start,
        never a duplicate start/end, never dropped silently (the framer's
        own drop-oldest overflow is already logged). Called once ``READY``
        is reached — at ``start()`` (nothing can have been captured yet,
        so this is a no-op there) and, from M2.6B.3, again after every
        successful reconnect."""
        if self._worker is None or self._P is None:
            return
        for event in self._framer.flush():
            if event.kind is EnvelopeEventKind.ACTIVITY_START:
                await self._worker.queue_frames([self._P["UserStartedSpeakingFrame"]()])
            elif event.kind is EnvelopeEventKind.AUDIO:
                frame = self._P["InputAudioRawFrame"](
                    audio=event.pcm, sample_rate=INPUT_SAMPLE_RATE_HZ, num_channels=1
                )
                await self._worker.queue_frames([frame])
            elif event.kind is EnvelopeEventKind.ACTIVITY_END:
                await self._worker.queue_frames([self._P["UserStoppedSpeakingFrame"]()])

    # -- lifecycle -----------------------------------------------------------
    async def start(self, snapshot: CloudContextSnapshot) -> None:
        self._set_readiness(ProviderReadiness.CONNECTING)
        P = self._P = _pipecat_imports()
        service_class = self._service_class_override or P["GeminiLiveLLMService"]

        messages = _render_snapshot_messages(snapshot)
        context = P["LLMContext"](messages=messages)
        user_agg, asst_agg = P["LLMContextAggregatorPair"](
            context,
            user_params=P["LLMUserAggregatorParams"](),
            realtime_service_mode=True,
        )

        llm_kwargs: dict[str, Any] = dict(
            api_key=self._api_key,
            system_instruction=snapshot.system_instruction,
            # Amendment 1 §2: system_instruction is supplied here, at
            # construction, and is NEVER mutated on this open connection.
            inference_on_context_initialization=False,
        )
        settings_cls = getattr(service_class, "Settings", None)
        if settings_cls is not None:
            llm_kwargs["settings"] = settings_cls(
                model=MODEL,
                modalities=P["GeminiModalities"].AUDIO,
                voice=self._voice,
                # server VAD OFF; local Silero stays the turn authority.
                vad=P["GeminiVADParams"](disabled=True),
                context_window_compression=P["ContextWindowCompressionParams"](enabled=True),
                system_instruction=snapshot.system_instruction,
            )
        self._llm = service_class(**llm_kwargs)

        event_tap_cls = _make_event_tap_class(P)
        down_tap = event_tap_cls(queue=self._events, on_frame=self._translate_frame)

        pipeline = P["Pipeline"]([user_agg, self._llm, down_tap, asst_agg])
        self._worker = P["PipelineWorker"](
            pipeline,
            params=P["PipelineParams"](
                audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ,
                audio_out_sample_rate=OUTPUT_SAMPLE_RATE_HZ,
                enable_usage_metrics=True,
            ),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        self._runner = P["WorkerRunner"]()
        await self._runner.add_workers(self._worker)
        # ``WorkerRunner.run()`` only actually starts each added worker's
        # background task once it is itself running (`add_workers` before
        # `run()` just registers the worker) — launch it as a background
        # task, never awaited directly, exactly like the M2.6A research
        # probe's proven `run_task = asyncio.create_task(runner.run())`.
        self._run_task = asyncio.create_task(self._runner.run())

        # The one-time kickoff (R0031): without this, no LLMContextFrame is
        # ever produced, self._llm._context stays None, and
        # _ready_for_realtime_input never flips (see the source audit).
        await self._worker.queue_frames([P["LLMRunFrame"]()])

        await self._wait_until_ready()

    async def _wait_until_ready(self) -> None:
        P = self._P
        assert P is not None
        elapsed = 0.0
        while elapsed < self._ready_timeout_s:
            if getattr(self._llm, "_ready_for_realtime_input", False):
                self._set_readiness(ProviderReadiness.READY)
                await self._flush_framer()
                return
            await asyncio.sleep(DEFAULT_READY_POLL_INTERVAL_S)
            elapsed += DEFAULT_READY_POLL_INTERVAL_S
        self._set_readiness(ProviderReadiness.FAILED)
        raise RealtimeProviderFailedError(
            f"GeminiLiveProvider did not become ready within {self._ready_timeout_s}s"
        )

    async def stop(self, *, reason: str) -> None:
        if self._runner is None:
            self._set_readiness(ProviderReadiness.FAILED)
            return
        P = self._P
        assert P is not None
        try:
            if self._worker is not None:
                await self._worker.queue_frames([P["EndFrame"]()])
            await self._runner.end(reason=reason)
            if self._run_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(self._run_task, timeout=5.0)
        except (TimeoutError, Exception) as exc:  # noqa: BLE001 — best-effort teardown
            logger.warning("nexa.realtime.gemini: error stopping provider: %r", exc)
        finally:
            self._worker = None
            self._runner = None
            self._run_task = None
            self._llm = None
            self._set_readiness(ProviderReadiness.FAILED)

    # -- turn I/O --------------------------------------------------------------
    # ADR-0004 Decision H: while not READY (including a future reconnect's
    # DEGRADED/RECONNECTING window, M2.6B.3), the turn envelope is captured
    # by ``self._framer`` instead of being sent — never dropped, never sent
    # out of order. ``_flush_framer`` delivers it once READY is reached.
    async def user_turn_start(self) -> None:
        self._turn_open = True
        if self._readiness is not ProviderReadiness.READY:
            self._framer.user_turn_start()
            return
        if self._worker is None or self._P is None:
            return
        await self._worker.queue_frames([self._P["UserStartedSpeakingFrame"]()])

    async def send_user_audio(self, pcm: bytes) -> None:
        if self._readiness is not ProviderReadiness.READY:
            self._framer.capture_audio(pcm)
            return
        if self._worker is None or self._P is None:
            return
        frame = self._P["InputAudioRawFrame"](
            audio=pcm, sample_rate=INPUT_SAMPLE_RATE_HZ, num_channels=1
        )
        await self._worker.queue_frames([frame])

    async def user_turn_end(self) -> None:
        self._turn_open = False
        if self._readiness is not ProviderReadiness.READY:
            self._framer.user_turn_end()
            return
        if self._worker is None or self._P is None:
            return
        await self._worker.queue_frames([self._P["UserStoppedSpeakingFrame"]()])

    async def cancel(self) -> None:
        if self._worker is None or self._P is None:
            return
        await self._worker.queue_frames([self._P["InterruptionFrame"]()])
        self._events.put_nowait(CancellationCompleteEvent())

    async def events(self) -> AsyncIterator[ProviderEvent]:
        while True:
            event = await self._events.get()
            yield event

    # -- frame -> typed-event translation --------------------------------------
    def _translate_frame(self, frame: Any) -> None:
        P = self._P
        assert P is not None
        try:
            if isinstance(frame, P["TranscriptionFrame"]):
                text = getattr(frame, "text", "") or ""
                self._events.put_nowait(UserTranscriptionEvent(text=text, final=True))
            elif isinstance(frame, P["TTSTextFrame"]):
                text = getattr(frame, "text", "") or ""
                self._events.put_nowait(AssistantTranscriptionEvent(text=text, final=False))
            elif isinstance(frame, P["TTSAudioRawFrame"]):
                pcm = getattr(frame, "audio", b"") or b""
                self._events.put_nowait(AssistantAudioEvent(pcm=pcm))
            elif isinstance(frame, P["InterruptionFrame"]):
                self._events.put_nowait(ProviderInterruptionEvent())
            elif isinstance(frame, P["LLMFullResponseEndFrame"]):
                self._events.put_nowait(AssistantTranscriptionEvent(text="", final=True))
            resumption = getattr(frame, "session_resumption_update", None)
            if resumption is not None:
                self._observe_resumption_update(resumption)
            usage = getattr(frame, "usage_metadata", None)
            if usage is not None:
                self._observe_usage(usage)
        except Exception as exc:  # noqa: BLE001 — a translation bug must not crash the pipeline
            logger.warning("nexa.realtime.gemini: frame translation error: %r", exc)
            self._events.put_nowait(RealtimeProviderError(f"frame translation error: {exc}"))

    def _observe_resumption_update(self, update: Any) -> None:
        resumable = bool(getattr(update, "resumable", False))
        new_handle = getattr(update, "new_handle", None)
        if resumable and new_handle:
            self._reconnect_handle = SessionResumptionHandle(value=new_handle, resumable=True)

    def _observe_usage(self, usage: Any) -> None:
        self._events.put_nowait(
            ProviderUsageEvent(
                provider="gemini_live",
                session_id=str(id(self._llm)),
                input_text_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                output_text_tokens=int(getattr(usage, "response_token_count", 0) or 0),
            )
        )

    @property
    def latest_resumption_handle(self) -> SessionResumptionHandle | None:
        return self._reconnect_handle
