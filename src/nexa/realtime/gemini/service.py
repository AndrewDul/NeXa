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

Fresh-session recovery mechanism (M2.6B.2A, finishing ADR-0004 Decision I):
when NeXa decides ``FRESH_SESSION_REQUIRED`` (no safe resumption handle, or
resumption failed), the mechanism is **destroy and recreate** — a brand
new ``GeminiLiveProvider`` instance is constructed with a freshly built
``CloudContextSnapshot``
(``ConversationRouter.request_fresh_snapshot_after_resumption_failure``),
never an in-place repair of the old instance. This is not a policy this
class enforces at runtime — it is a structural guarantee: a new instance
builds its own brand-new ``LLMContext`` from the fresh snapshot in
``start()`` and has no reference whatsoever to any previous instance's
``self._llm`` / ``self._llm._context``, so a stale Pipecat-owned context
can never seed the new session. The old instance's still-undelivered
buffered audio (if any) is retrievable via ``take_pending_audio()`` before
it is discarded, so nothing already captured is silently lost across the
hand-off (M2.6B.2A hardening — see the turn-I/O methods below).

M2.6B.4E (R0043) — real Attempt #2 hardware evidence found that a
non-lexical interrupting sound (a throat-clear/cough) can confirm a
local barge-in and close a real local VAD turn WITHOUT Gemini ever
producing a ``UserTranscriptionEvent`` for it. The runtime's dispatch
gate (``GeminiVoiceRuntime._consume_provider_events``) previously relied
solely on a fresh final ``UserTranscriptionEvent`` to know "a genuinely
new local turn has started, the next Assistant event may get a fresh
generation id" — with no transcript ever arriving, that gate stayed
permanently closed, and ALL future assistant audio (for every later
turn, regardless of language) was silently dropped as
belonging-to-an-invalidated-generation, while assistant TEXT kept
flowing normally (text commit does not go through the generation guard
at all). ``user_turn_end()`` now increments ``local_turn_closed_seq`` —
a NeXa/Gemini-independent, always-fires-once-per-local-VAD-turn
counter — which the runtime uses as a fallback re-arm signal alongside
the existing final-transcription one. See
``docs/reports/R0043_m2_6b_4e_attempt2_post_interruption_audio_loss_20260911.md``
for the full source audit and the residual-risk discussion (a bounded,
disclosed chance of one stray trailing-audio chunk at an interruption
boundary, versus the confirmed alternative of permanent silence).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..provider import (
    AssistantAudioEvent,
    AssistantTranscriptionEvent,
    CancellationCompleteEvent,
    GenerationCompleteEvent,
    ProviderEvent,
    ProviderInterruptionEvent,
    ProviderReadiness,
    ReadinessChangedEvent,
    RealtimeProviderCapabilities,
    RealtimeProviderError,
    RealtimeProviderFailedError,
    RealtimeVoiceProvider,
    ReconnectingEvent,
    ResumedEvent,
    UserTranscriptionEvent,
)
from ..reconnect import ReconnectController, SessionResumptionHandle
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
        ErrorFrame,
        FatalErrorFrame,
        InputAudioRawFrame,
        InterimTranscriptionFrame,
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
        "ErrorFrame": ErrorFrame,
        "FatalErrorFrame": FatalErrorFrame,
        "InputAudioRawFrame": InputAudioRawFrame,
        "InterimTranscriptionFrame": InterimTranscriptionFrame,
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
        reconnect_controller: ReconnectController | None = None,
        readiness_poll_interval_s: float = DEFAULT_READY_POLL_INTERVAL_S,
    ) -> None:
        self._api_key = api_key
        self._voice = gemini_voice_for_preference(voice_preference)
        self._service_class_override = service_class
        self._ready_timeout_s = ready_timeout_s
        self._readiness_poll_interval_s = readiness_poll_interval_s

        self._readiness = ProviderReadiness.CONNECTING
        self._events: asyncio.Queue[ProviderEvent] = asyncio.Queue()
        self._P: dict[str, Any] | None = None
        self._llm: Any = None
        self._worker: Any = None
        self._runner: Any = None
        self._run_task: asyncio.Task | None = None
        self._reconnect_handle: SessionResumptionHandle | None = None
        self._turn_open = False
        #: True from a LIVE ``activity_start`` until a LIVE
        #: ``activity_end`` — see the "mid-turn readiness loss" note on
        #: the turn-I/O methods below (M2.6B.2A hardening).
        self._live_turn_open = False

        # M2.6B.3 — real reconnect wiring (ADR-0004 Decision I, hardened by
        # the "mid-turn resumption unsafe" policy below).
        self._reconnect = reconnect_controller or ReconnectController()
        #: Set True the instant a connection drop is observed WHILE a turn
        #: had already gone live (``_live_turn_open`` at the moment of
        #: loss) — the caller (``ConversationRouter`` /
        #: ``recover_from_mid_turn_loss``) MUST discard this instance and
        #: build a fresh one; this provider will not try to resume that
        #: turn itself. Never cleared automatically — a fresh instance
        #: starts with this False.
        self._needs_fresh_session = False
        self._readiness_monitor_task: asyncio.Task | None = None
        #: ADR-0004 Decision H — while ``readiness != READY`` (including a
        #: future reconnect's DEGRADED/RECONNECTING window, M2.6B.3), the
        #: local turn envelope (activityStart / audio / activityEnd) is
        #: captured here instead of being sent, then flushed in order once
        #: READY again. Never bypassed: ``send_user_audio`` etc. always
        #: check ``readiness`` first.
        self._framer = UtteranceFramer()
        #: M2.6B.4E (R0043) — a NeXa-owned, Gemini-independent counter:
        #: incremented once per ``user_turn_end()`` call, i.e. once per
        #: local VAD-detected utterance closing, REGARDLESS of whether
        #: Gemini ever transcribes it. A non-lexical sound (a throat-
        #: clear/cough) closes a real local turn with NO
        #: ``UserTranscriptionEvent`` at all — this counter is the one
        #: signal the runtime consumer loop can rely on to know "at least
        #: one more local turn has genuinely closed" without depending on
        #: Gemini producing a transcript for it. See
        #: ``GeminiVoiceRuntime._consume_provider_events``'s own use of
        #: this for the exact failure this closes.
        self._local_turn_closed_seq = 0
        #: M2.6B.4F (R0044) -- ids of ``InterruptionFrame`` instances THIS
        #: provider itself constructed (in ``cancel()``), so
        #: ``_translate_frame`` can tag ``ProviderInterruptionEvent.source``
        #: as ``"local_cancel"`` vs ``"remote_server_ack"`` -- diagnostic
        #: only. Never cleared: bounded in practice by how many times
        #: ``cancel()`` is ever called in one provider's lifetime (one
        #: session-worth of confirmed interruptions -- at most tens of
        #: entries, negligible memory), never once per audio chunk.
        self._local_cancel_frame_ids: set[int] = set()

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
        # ``push_error()`` (used by GeminiLiveLLMService for both
        # recoverable and fatal errors) pushes its ErrorFrame/FatalErrorFrame
        # UPSTREAM, not downstream — a tap placed only after ``self._llm``
        # would never see it. ``up_tap`` mirrors the M2.6A research probe's
        # own upstream/downstream dual-tap pattern so both directions are
        # observed.
        up_tap = event_tap_cls(queue=self._events, on_frame=self._translate_frame)

        pipeline = P["Pipeline"]([up_tap, user_agg, self._llm, down_tap, asst_agg])
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
        self._reconnect.on_connected(now=self._now())
        self._readiness_monitor_task = asyncio.create_task(self._readiness_monitor())

    def _now(self) -> float:
        return time.monotonic()

    async def _wait_until_ready(self) -> None:
        P = self._P
        assert P is not None
        elapsed = 0.0
        while elapsed < self._ready_timeout_s:
            if getattr(self._llm, "_ready_for_realtime_input", False):
                self._set_readiness(ProviderReadiness.READY)
                await self._flush_framer()
                return
            await asyncio.sleep(self._readiness_poll_interval_s)
            elapsed += self._readiness_poll_interval_s
        self._set_readiness(ProviderReadiness.FAILED)
        raise RealtimeProviderFailedError(
            f"GeminiLiveProvider did not become ready within {self._ready_timeout_s}s"
        )

    @property
    def needs_fresh_session(self) -> bool:
        """M2.6B.3 — set once a connection drop was observed WHILE a turn
        had already gone live (mid-turn resumption is treated as UNSAFE by
        policy, per R0034/M2.6B.3: no strong evidence that Gemini resets an
        unmatched ``activity_start`` on resumption, so the safer v1 rule
        applies). The caller (``ConversationRouter.recover_from_mid_turn_loss``)
        must discard this instance and build a fresh one rather than trust
        any recovery this instance's underlying Pipecat service performs on
        its own."""
        return self._needs_fresh_session

    async def _readiness_monitor(self) -> None:
        """NeXa-owned OBSERVATION seam (ADR-0004 Decision P — no Pipecat
        patch): watches ``self._llm._ready_for_realtime_input`` for
        transitions. Pipecat's own ``GeminiLiveLLMService`` reconnects
        *automatically and internally* on a connection error
        (``_handle_connection_error`` -> ``_reconnect()`` — R0034/M2.6B.3
        source finding) with NO external hook to intervene beforehand; this
        monitor is how NeXa finds out a drop happened at all, and is what
        lets the mid-turn-unsafe policy actually take effect (without it,
        ``self._readiness`` would simply stay ``READY`` throughout a drop
        Pipecat recovered from on its own, and the R0034 abort-and-restart
        logic would never engage)."""
        was_ready = True
        while self._llm is not None and self._readiness != ProviderReadiness.FAILED:
            ready_now = bool(getattr(self._llm, "_ready_for_realtime_input", False))
            if was_ready and not ready_now:
                unsafe = self._live_turn_open
                self._needs_fresh_session = unsafe
                self._set_readiness(ProviderReadiness.RECONNECTING)
                self._events.put_nowait(
                    ReconnectingEvent(
                        reason="mid_turn_unsafe_loss" if unsafe else "safe_boundary_loss"
                    )
                )
                if unsafe:
                    logger.warning(
                        "nexa.realtime.gemini: connection lost mid-utterance — "
                        "per policy this session will NOT be trusted to resume "
                        "this turn; a fresh provider session is required"
                    )
                    return  # nothing more to observe from a doomed instance
            elif not was_ready and ready_now:
                self._set_readiness(ProviderReadiness.READY)
                await self._flush_framer()
                self._reconnect.on_connected(now=self._now())
                self._events.put_nowait(ResumedEvent(from_handle=True))
            was_ready = ready_now
            await asyncio.sleep(self._readiness_poll_interval_s)

    async def stop(self, *, reason: str) -> None:
        if self._readiness_monitor_task is not None:
            self._readiness_monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._readiness_monitor_task
            self._readiness_monitor_task = None
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
    # ADR-0004 Decision H, hardened (M2.6B.2A): while not READY (including a
    # reconnect's DEGRADED/RECONNECTING window), the turn envelope is
    # captured by ``self._framer`` instead of being sent — never dropped,
    # never sent out of order. ``_flush_framer`` delivers it once READY is
    # reached.
    #
    # Mid-turn readiness loss (M2.6B.2A hardening — this is the fix): a turn
    # whose ``activity_start`` already went out LIVE (``readiness`` was
    # READY at ``user_turn_start()``) tracks that with ``self._live_turn_open``.
    # If readiness then drops before ``user_turn_end()``, the framer would
    # previously reject any further audio as an "orphan" (no matching
    # ``activity_start`` had ever been recorded there) — silent user-speech
    # loss. The fix: on the FIRST piece of audio that arrives once not
    # ready, the stranded live segment is deterministically ABORTED (it
    # will never receive a matching live ``activity_end`` — Gemini/the
    # resumed session sees an incomplete first fragment) and every frame
    # from that point on becomes a NEW, self-contained buffered utterance
    # (its own ``activity_start``/audio/``activity_end``, flushed once
    # READY returns). This guarantees: no audio byte is ever silently
    # dropped, and no audio byte already sent live is ever replayed/
    # duplicated. The cost — accepted, documented, ADR-0004-compatible — is
    # that Gemini may perceive the split as two utterances instead of one
    # continuous one; this is a semantic degradation under a real
    # connectivity loss, not data loss.
    async def user_turn_start(self) -> None:
        self._turn_open = True
        if self._readiness is ProviderReadiness.READY:
            self._live_turn_open = True
            if self._worker is not None and self._P is not None:
                await self._worker.queue_frames([self._P["UserStartedSpeakingFrame"]()])
            return
        self._live_turn_open = False
        self._framer.user_turn_start()

    async def send_user_audio(self, pcm: bytes) -> None:
        if self._readiness is ProviderReadiness.READY:
            if self._live_turn_open:
                if self._worker is not None and self._P is not None:
                    frame = self._P["InputAudioRawFrame"](
                        audio=pcm, sample_rate=INPUT_SAMPLE_RATE_HZ, num_channels=1
                    )
                    await self._worker.queue_frames([frame])
                return
            # READY, but this utterance's start was never sent live (e.g.
            # captured before the READY transition and not yet flushed) —
            # buffer safely rather than guess at framing.
            self._framer.capture_audio(pcm)
            return
        # not READY:
        if self._live_turn_open:
            logger.warning(
                "nexa.realtime.gemini: provider not ready mid-utterance — "
                "aborting the live segment (no matching activity_end will "
                "be sent for it) and continuing as a new buffered "
                "utterance; no audio is dropped or duplicated"
            )
            self._live_turn_open = False
            self._framer.user_turn_start()
        self._framer.capture_audio(pcm)

    async def user_turn_end(self) -> None:
        # M2.6B.4E (R0043) -- unconditional: a local VAD turn has closed
        # regardless of readiness/liveness/transcription outcome. This is
        # what the runtime consumer loop uses to re-arm response dispatch
        # after an interruption whose own local turn never produced a
        # UserTranscriptionEvent (see class docstring above).
        self._local_turn_closed_seq += 1
        self._turn_open = False
        was_live = self._live_turn_open
        self._live_turn_open = False
        if self._readiness is ProviderReadiness.READY and was_live:
            if self._worker is not None and self._P is not None:
                await self._worker.queue_frames([self._P["UserStoppedSpeakingFrame"]()])
            return
        # Either not ready, or READY but nothing was ever live for this
        # utterance (it was captured/aborted into the framer above) —
        # close whatever is open there. A no-op if nothing is open.
        self._framer.user_turn_end()

    @property
    def local_turn_closed_seq(self) -> int:
        """M2.6B.4E (R0043) -- monotonic count of ``user_turn_end()``
        calls, independent of Gemini transcription. See ``__init__``."""
        return self._local_turn_closed_seq

    def take_pending_audio(self) -> list[bytes]:
        """Extract any user audio buffered but not yet delivered — e.g.
        because a fresh provider session is required (ADR-0004 Decision I)
        and this instance is about to be discarded. Returns raw PCM chunks
        only, in order; the caller supplies its own fresh
        ``user_turn_start``/``user_turn_end`` framing on the NEW provider —
        this never replays stale envelope markers from this (about-to-be-
        discarded) instance, so nothing is duplicated."""
        pending = self._framer.flush()
        return [event.pcm for event in pending if event.pcm is not None]

    async def cancel(self) -> None:
        if self._worker is None or self._P is None:
            return
        frame = self._P["InterruptionFrame"]()
        # M2.6B.4F (R0044) -- record this exact frame instance's id BEFORE
        # queuing it. It is the ONLY InterruptionFrame this provider ever
        # constructs itself; `broadcast_interruption()` (Gemini's own
        # ack path, service.py's receive loop) creates its own fresh
        # up/downstream instances instead (see `_translate_frame`). This
        # is diagnostic only -- see `ProviderInterruptionEvent.source`'s
        # own docstring for why neither origin is used as a barrier.
        self._local_cancel_frame_ids.add(frame.id)
        await self._worker.queue_frames([frame])
        self._events.put_nowait(CancellationCompleteEvent())

    async def events(self) -> AsyncIterator[ProviderEvent]:
        while True:
            event = await self._events.get()
            yield event

    # -- frame -> typed-event translation --------------------------------------
    def _translate_frame(self, frame: Any) -> None:
        """Provider -> NeXa event mapping (ADR-0004 provider interface
        contract). See
        ``docs/research/m2_6_cloud_realtime_voice/m2_6b_provider_event_mapping_20260911.md``
        for the full source-frame -> event -> router -> canonical-session
        table this implements."""
        P = self._P
        assert P is not None
        try:
            if isinstance(frame, P["InterimTranscriptionFrame"]):
                text = getattr(frame, "text", "") or ""
                self._events.put_nowait(UserTranscriptionEvent(text=text, final=False))
            elif isinstance(frame, P["TranscriptionFrame"]):
                text = getattr(frame, "text", "") or ""
                final = bool(getattr(frame, "finalized", True))
                self._events.put_nowait(UserTranscriptionEvent(text=text, final=final))
            elif isinstance(frame, P["TTSTextFrame"]):
                text = getattr(frame, "text", "") or ""
                self._events.put_nowait(AssistantTranscriptionEvent(text=text, final=False))
            elif isinstance(frame, P["TTSAudioRawFrame"]):
                pcm = getattr(frame, "audio", b"") or b""
                self._events.put_nowait(AssistantAudioEvent(pcm=pcm))
            elif isinstance(frame, P["InterruptionFrame"]):
                # M2.6B.4F (R0044) -- our own cancel() is the ONLY place
                # this provider ever constructs an InterruptionFrame; its
                # id is recorded there before queuing. Anything else
                # reaching here is, by elimination (confirmed exhaustively
                # from installed source -- see docs/reports/R0044_...),
                # Gemini's own serverContent.interrupted triggering
                # GeminiLiveLLMService's own broadcast_interruption(). A
                # membership check (never removed) tags BOTH expected
                # sightings of our own single queued instance (up_tap,
                # then down_tap) consistently as "local_cancel".
                frame_id = getattr(frame, "id", None)
                if frame_id is not None and frame_id in self._local_cancel_frame_ids:
                    source = "local_cancel"
                else:
                    source = "remote_server_ack"
                self._events.put_nowait(ProviderInterruptionEvent(source=source))
            elif isinstance(frame, P["LLMFullResponseEndFrame"]):
                # Generation/turn complete — the ConversationRouter's signal
                # to commit (ADR-0004 Decision A). Deliberately a distinct
                # event type, never conflated with "assistant said nothing"
                # (an earlier draft used AssistantTranscriptionEvent(text="",
                # final=True) for this, which was ambiguous — corrected).
                self._events.put_nowait(GenerationCompleteEvent())
            elif isinstance(frame, P["FatalErrorFrame"]):
                self._set_readiness(ProviderReadiness.FAILED)
                self._events.put_nowait(RealtimeProviderFailedError(str(frame)))
            elif isinstance(frame, P["ErrorFrame"]):
                self._events.put_nowait(RealtimeProviderError(str(frame)))
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

    def should_proactively_reconnect(self) -> bool:
        """ADR-0004 Decision I — the proactive age-timer decision
        (``ReconnectController.should_proactively_reconnect``), exposed for
        a caller to poll. Gemini Live's own `GoAway` message is NOT exposed
        by the installed Pipecat 1.8.1 (confirmed unchanged, R0032/R0034) —
        there is no frame or attribute to observe it from, so only the
        age-timer trigger is wired here. No live 8+ minute session is run
        in this checkpoint to exercise it; the decision logic itself is
        unit-tested with an injected clock."""
        return self._reconnect.should_proactively_reconnect(now=self._now())
