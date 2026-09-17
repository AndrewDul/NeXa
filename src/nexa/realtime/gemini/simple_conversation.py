"""``CloudRealtimeConversationAdapter`` — the simplified, golden-M2.6A-
derived production cloud voice path (ADR-0004 Amendment 2 / R0071).

## Why this module exists, separate from ``runtime.py``

``nexa.realtime.gemini.runtime.build_gemini_voice_runtime`` (M2.6B.3+) is a
DUAL-PIPELINE architecture: the provider's own headless Pipecat pipeline is
bridged to a separate hardware pipeline via an ``asyncio.Queue`` and a
``BargeInController``/candidate-ownership state machine NeXa built on top of
Gemini's own turn handling. R0071 (real-hardware acceptance, 2026-09-16)
found that architecture produces sustained self-echo/self-conversation on
the current Pi topology that a same-day, same-hardware run of the
ORIGINAL, OPERATOR-CONFIRMED M2.6A probe (``docs/research/
m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py``, commit ``7dd6b87``)
did not exhibit — a controlled reference-gain A/B on the dual-pipeline path
did not reproduce golden behavior either. That investigation is PAUSED
(not abandoned — see ``docs/reports/R0071_...md``), and the product
decision (ADR-0004 Amendment 2) is: stop trying to out-engineer Gemini
Live's own realtime conversation handling in a second, NeXa-owned
dual-pipeline/interruption-authority architecture. Use the SIMPLE
architecture that is already operator-confirmed 10/10, extracted as
BEHAVIOR (one Pipecat pipeline, Gemini's own native VAD-driven turn and
interruption handling), not as a research probe with its own diagnostics.

## What this module reuses vs. what it owns

Reuses, unchanged, from the existing NeXa Core / ADR-0004 boundary:
``ConversationSession`` (via ``ConversationRouter``), ``CloudContextSnapshot``
(the ONLY thing besides live mic audio that ever reaches the provider),
``ConversationPolicy``, the SAME ``nexa.realtime.provider`` event types
``GeminiLiveProvider`` already uses, and the SAME, already-tested
``ConversationRouter.handle_provider_event`` canonical write path.

Reuses, unchanged, from the accepted local-hardware boundary:
``nexa.voice.config.LocalAudioConfig``, ``nexa.voice.device.find_device_index``,
``nexa.voice_tts.aec_reference.AecReferenceFeeder`` fed WITHOUT
``gain_source`` (``None`` — byte-for-byte golden's own unscaled behavior;
R0071's own reference-gain A/B on the dual-pipeline path showed scaling
does not fix self-echo there, and golden never needed it).

Does NOT reuse: ``BargeInController``, the candidate/reject ownership
state machine, ``_ResponseGenerationGuard``, atomic provider replacement,
the hardware/provider dual-pipeline bridge. Interruption is handled the
way M2.6A proved works: Pipecat's own ``LLMContextAggregatorPair`` (in
realtime-service mode) broadcasts ``InterruptionFrame`` natively on a new
``UserStartedSpeakingFrame`` while a reply is in flight — NeXa's own local
speaker-stop authority is Pipecat's, not a second NeXa-owned state
machine layered on top of it.

This module owns exactly one thing: translating the SAME Pipecat frames
the golden probe already proved sufficient into the SAME
``nexa.realtime.provider`` event vocabulary the (paused) dual-pipeline
runtime already uses, and handing them to ``ConversationRouter`` — zero
new conversation-state logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ...voice.aec import AecReferenceHealth
from ...voice.aec_gain import CoherentReferenceGain
from ...voice.config import LocalAudioConfig
from ...voice.device import find_device_index
from ...voice_tts.aec_reference import AecReferenceFeeder
from ..policy import ConversationPolicy
from ..provider import (
    AssistantTranscriptionEvent,
    GenerationCompleteEvent,
    ProviderInterruptionEvent,
    UserTranscriptionEvent,
)
from ..router import ConversationRouter
from ..snapshot import CloudContextSnapshot
from .core_recall_tool import (
    RECALL_TOOL_USE_INSTRUCTION,
    RecallExecutor,
    recall_tool_schema,
    register_recall_tool,
)
from .voice import DEFAULT_VOICE_PREFERENCE, gemini_voice_for_preference

logger = logging.getLogger(__name__)

#: Gemini's own native audio rates (matches golden M2.6A exactly).
INPUT_SAMPLE_RATE_HZ = 16000
OUTPUT_SAMPLE_RATE_HZ = 24000
#: golden M2.6A's own Silero stop_secs (> Pipecat's 0.2s default).
VAD_STOP_SECS = 0.5
#: golden M2.6A's own kickoff timeout/settle (R0031 fix for the missing
#: LLMRunFrame -- see ``_kickoff`` below).
KICKOFF_TIMEOUT_SECS = 15.0
KICKOFF_SETTLE_SECS = 0.3

#: R0080 §5/§6 -- explicitly pinned. Before R0080 this path silently
#: inherited Pipecat 1.8.1's own hardcoded ``GeminiLiveLLMService`` default
#: (``models/gemini-2.5-flash-native-audio-preview-12-2025``, set in
#: ``GeminiLiveLLMService.__init__``'s ``default_settings``) -- an
#: invisible third-party dependency, not a NeXa decision. Pinning it here
#: makes today's already-accepted (R0071) production model explicit and
#: reproducible; it is NOT a migration. Confirmed both by Pipecat's own
#: source (``llm.py``, R0079 audit) and by Google's own official docs
#: (Live API + Function calling both listed "Supported" for this exact
#: model, https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-native-audio-preview-12-2025,
#: accessed 2026-09-17) that this model exists and is current -- distinct
#: from, and unrelated to, the paused M2.6B dual-pipeline's own
#: ``gemini-3.1-flash-live-preview`` string (``nexa.realtime.gemini.service``,
#: never imported by this module). Do not change this string to migrate
#: models without a dedicated evaluation milestone (R0080 §15).
GEMINI_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"


def _pipecat_imports() -> dict[str, Any]:
    """Deferred import (never at module import time -- this module stays
    importable without the optional ``cloud-gemini`` extra, matching
    ``runtime.py``'s/``service.py``'s own convention)."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InterruptionFrame,
        LLMFullResponseEndFrame,
        LLMRunFrame,
        TranscriptionFrame,
        TTSStartedFrame,
        TTSStoppedFrame,
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
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.google.gemini_live.llm import (
        ContextWindowCompressionParams,
        GeminiLiveLLMService,
        GeminiModalities,
        GeminiVADParams,
    )
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from pipecat.workers.runner import WorkerRunner

    return {
        "SileroVADAnalyzer": SileroVADAnalyzer,
        "VADParams": VADParams,
        "InputAudioRawFrame": InputAudioRawFrame,
        "InterruptionFrame": InterruptionFrame,
        "LLMFullResponseEndFrame": LLMFullResponseEndFrame,
        "LLMRunFrame": LLMRunFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "TTSStartedFrame": TTSStartedFrame,
        "TTSStoppedFrame": TTSStoppedFrame,
        "TTSTextFrame": TTSTextFrame,
        "UserStartedSpeakingFrame": UserStartedSpeakingFrame,
        "UserStoppedSpeakingFrame": UserStoppedSpeakingFrame,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "LLMContext": LLMContext,
        "LLMContextAggregatorPair": LLMContextAggregatorPair,
        "LLMUserAggregatorParams": LLMUserAggregatorParams,
        "FrameProcessor": FrameProcessor,
        "ContextWindowCompressionParams": ContextWindowCompressionParams,
        "GeminiLiveLLMService": GeminiLiveLLMService,
        "GeminiModalities": GeminiModalities,
        "GeminiVADParams": GeminiVADParams,
        "LocalAudioTransport": LocalAudioTransport,
        "LocalAudioTransportParams": LocalAudioTransportParams,
        "WorkerRunner": WorkerRunner,
    }


def _make_event_tap_class(P: dict[str, Any]) -> type:
    """The one new FrameProcessor this module adds: translates golden
    M2.6A's own proven frame vocabulary into ``nexa.realtime.provider``
    events and drives ``ConversationRouter`` -- no other logic."""

    class _ConversationEventTap(P["FrameProcessor"]):
        def __init__(
            self,
            *,
            router: ConversationRouter,
            on_event: Any = None,
            on_diagnostic: Any = None,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self._router = router
            #: Optional observability hook (e.g. the operator app printing
            #: live progress) -- called with the SAME event just handed to
            #: the router, never a second, independent read of anything.
            #: Never allowed to break the conversation: an exception from
            #: it is logged and swallowed.
            self._on_event = on_event
            #: R0081 §4/§5 -- optional, narrow diagnostic-timeline hook.
            #: Called with a plain label string (e.g. "LOCAL_VAD_START")
            #: for frames this tap already observes/forwards unchanged --
            #: purely additive observation, never changes routing/
            #: interruption logic. None (the default) is byte-for-byte
            #: R0080 behavior. Never carries Memory/recall content -- only
            #: frame-type labels and (for the real user transcript line
            #: only, since it is the operator's own speech, locally
            #: printed, opted into explicitly) the transcript text itself.
            self._on_diagnostic = on_diagnostic
            #: R0081 (continued) -- local bookkeeping only, never sent
            #: anywhere but the diagnostic sink: whether a LOCAL_VAD_START
            #: has fired without a matching LOCAL_VAD_STOP yet. Lets the
            #: diagnostic distinguish an InterruptionFrame arriving WHILE
            #: local VAD is actively detecting speech (the synchronous,
            #: local-aggregator path) from one arriving with no local VAD
            #: activity in progress (consistent with the installed SDK's
            #: OWN, separate broadcast_interruption() call site --
            #: GeminiLiveLLMService itself, triggered by a DELAYED
            #: serverContent.interrupted acknowledgement from Gemini's
            #: server, confirmed by direct source read, see R0081's
            #: report). Never influences routing/turn logic -- read-only.
            self._vad_active = False

        def _publish(self, event: Any) -> None:
            self._router.handle_provider_event(event)
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception:  # noqa: BLE001 - an observer must never break audio
                    logger.exception(
                        "nexa.realtime.gemini.simple_conversation: on_event hook raised"
                    )

        def _diag(self, label: str) -> None:
            if self._on_diagnostic is None:
                return
            try:
                self._on_diagnostic(label)
            except Exception:  # noqa: BLE001 - diagnostics must never break audio
                logger.exception(
                    "nexa.realtime.gemini.simple_conversation: on_diagnostic hook raised"
                )

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["UserStartedSpeakingFrame"]):
                self._vad_active = True
                self._diag("LOCAL_VAD_START")
                # Mirrors the (paused) dual-pipeline runtime's own R0046
                # guard: a genuinely new local turn opens one canonical
                # turn; a turn already awaiting its own assistant reply is
                # never abandoned by this same check (the interrupting
                # utterance gets its own turn explicitly, below, once the
                # interrupted one is committed).
                if not self._router.has_turn_awaiting_assistant():
                    self._diag("USER_TURN_START")
                    self._router.begin_cloud_turn()
            elif isinstance(frame, P["UserStoppedSpeakingFrame"]):
                # R0081: not previously observed at all -- purely additive,
                # never routed anywhere, matches golden's own behavior of
                # not reacting to this frame.
                self._vad_active = False
                self._diag("LOCAL_VAD_STOP")
            elif isinstance(frame, P["TTSStartedFrame"]):
                self._diag("BOT_AUDIO_STARTED")
            elif isinstance(frame, P["TTSStoppedFrame"]):
                self._diag("BOT_AUDIO_STOPPED")
            elif isinstance(frame, P["TranscriptionFrame"]):
                text = getattr(frame, "text", "") or ""
                final = bool(getattr(frame, "finalized", True))
                if final:
                    self._diag(f"USER_TRANSCRIPT:{text}")
                self._publish(UserTranscriptionEvent(text=text, final=final))
            elif isinstance(frame, P["TTSTextFrame"]):
                text = getattr(frame, "text", "") or ""
                self._publish(AssistantTranscriptionEvent(text=text, final=False))
            elif isinstance(frame, P["LLMFullResponseEndFrame"]):
                self._diag("USER_TURN_END")
                self._publish(GenerationCompleteEvent())
            elif isinstance(frame, P["InterruptionFrame"]):
                # R0081 (corrected -- see the report's "not every
                # InterruptionFrame is directly paired with a fresh local
                # VAD onset" finding): TWO INDEPENDENT call sites can
                # invoke Pipecat's broadcast_interruption() in this
                # pipeline, confirmed by direct installed-source read
                # (pipecat 1.8.1) -- (1) LLMContextAggregatorPair's own
                # VAD-driven aggregator (llm_response_universal.py:1292),
                # firing synchronously with a fresh local
                # UserStartedSpeakingFrame; (2) GeminiLiveLLMService
                # ITSELF (gemini_live/llm.py:1333), firing when Gemini's
                # own server sends `serverContent.interrupted=True` -- a
                # DELAYED acknowledgement of the client-sent activity_start
                # NeXa already issued when local VAD fired, arriving over
                # the network on Gemini's own schedule, not paired with any
                # new local VAD event (its own code comment: "it does *not*
                # emit UserStarted/StoppedSpeakingFrames"). Each call site's
                # own broadcast_interruption() ALSO fans out an upstream +
                # downstream instance (R0043's original finding, still
                # true) -- so up to 4 total InterruptionFrame sightings can
                # legitimately correspond to ONE physical interruption.
                # `frame.id`/`frame.broadcast_sibling_id` (plain integers,
                # Pipecat's own frame-debugging fields) let the operator's
                # log distinguish "these two are fan-out siblings from the
                # SAME call" from "these are from two independent calls" --
                # `vad_active` (this tap's own bookkeeping, above) further
                # distinguishes "arrived while local VAD is actively
                # detecting speech" (source 1) from "arrived with no local
                # VAD activity in progress" (consistent with source 2).
                frame_id = getattr(frame, "id", None)
                sibling_id = getattr(frame, "broadcast_sibling_id", None)
                self._diag(
                    f"INTERRUPTION_FRAME_{direction.name} id={frame_id} "
                    f"sibling={sibling_id} vad_active={self._vad_active} "
                    f"awaiting_assistant={self._router.has_turn_awaiting_assistant()}"
                )
                # R0081 §7: broadcast_interruption() (see below) has
                # already stopped playback BEFORE this frame reaches any
                # processor, so this marker's timestamp is a lower-bound
                # proxy for when playback actually stopped, not the
                # literal physical moment -- the deeper transport-level
                # instrumentation that would give the exact moment is
                # exactly the kind of architectural change this milestone
                # must not make.
                self._diag("PLAYBACK_STOPPED")
                # M2.6B.3B's own conservative rule, reused verbatim: no
                # deterministic assistant-text/audio alignment exists in
                # the installed Pipecat/google-genai stack, so an
                # interrupted turn's assistant text is always empty
                # (under-crediting is acceptable; crediting unspoken
                # words is not). `broadcast_interruption()` (Pipecat's
                # own mechanism) has ALREADY stopped local playback by
                # the time this frame reaches any processor -- this tap
                # only updates canonical history, never speaker state.
                #
                # `InterruptionFrame` can legitimately arrive more than
                # once (or more than twice -- see above) for ONE
                # interruption. Calling this sequence twice is safe, not
                # just tolerated: `commit_cloud_turn()` on an already-empty
                # just-opened turn is a documented no-op
                # (CloudTurnAccumulator guarantees at most one commit per
                # turn), and `start_turn()` on a non-terminal empty turn
                # just abandons it and allocates the next generation -- no
                # corruption, no new state machine needed to de-duplicate
                # it.
                self._router.set_spoken_prefix("")
                self._publish(ProviderInterruptionEvent(source="native_pipecat"))
                self._diag("USER_TURN_END")
                self._router.commit_cloud_turn()
                self._diag("USER_TURN_START")
                self._router.begin_cloud_turn()
            await self.push_frame(frame, direction)

    return _ConversationEventTap


def _make_mic_level_tap_class(P: dict[str, Any]) -> type:
    """R0081 §"ADD SIGNAL-LEVEL DIAGNOSTICS" -- a second, narrow,
    purely-observational FrameProcessor: numeric-only, throttled mic RMS,
    filtered to ``InputAudioRawFrame`` -- the exact same frame type
    Pipecat's own VAD analysis gates on (confirmed, R0053's own finding,
    re-cited here since it is the same discipline). Every frame is
    forwarded unchanged; never terminates/rewrites the pipeline. Only
    ever inserted into the pipeline when a diagnostic callback is
    actually supplied (see ``build_cloud_realtime_conversation_adapter``)
    -- the frozen R0071 pipeline shape is completely unchanged when
    diagnostics are off."""

    class _MicLevelTap(P["FrameProcessor"]):
        def __init__(
            self,
            *,
            on_diagnostic: Any,
            diagnostic_interval_s: float = 0.25,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self._on_diagnostic = on_diagnostic
            self._diagnostic_interval_s = diagnostic_interval_s
            self._last_diagnostic_at = 0.0

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["InputAudioRawFrame"]) and frame.audio:
                now = time.monotonic()
                if now - self._last_diagnostic_at >= self._diagnostic_interval_s:
                    self._last_diagnostic_at = now
                    try:
                        import audioop

                        rms = audioop.rms(frame.audio, 2)
                        self._on_diagnostic(f"MIC_RMS:{rms}")
                    except Exception:  # noqa: BLE001 - diagnostics must never break audio
                        logger.exception(
                            "nexa.realtime.gemini.simple_conversation: "
                            "mic-level on_diagnostic raised"
                        )
            await self.push_frame(frame, direction)

    return _MicLevelTap


async def _kickoff(worker: Any, llm: Any, *, timeout_secs: float = KICKOFF_TIMEOUT_SECS) -> bool:
    """R0031's own proven fix, reused verbatim: with server VAD disabled,
    ``GeminiLiveLLMService`` only sends ``activity_start``/audio/
    ``activity_end`` once ``_ready_for_realtime_input`` is True, and that
    flag only flips once an ``LLMContextFrame`` reaches it -- which nothing
    pushes until an ``LLMRunFrame`` is queued (the upstream Pipecat example
    does this from a transport's ``on_client_connected``; ``LocalAudioTransport``
    has none). Returns whether the socket was observed connected before
    ``timeout_secs``."""
    P = _pipecat_imports()
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        if getattr(llm, "_session", None) is not None:
            break
        await asyncio.sleep(0.1)
    connected = getattr(llm, "_session", None) is not None
    # Small settle so StartFrame has propagated through the aggregator
    # before the kickoff frame -- golden M2.6A's own proven timing.
    await asyncio.sleep(KICKOFF_SETTLE_SECS)
    await worker.queue_frames([P["LLMRunFrame"]()])
    return connected


@dataclass
class CloudRealtimeConversationAdapter:
    """Holds every object this module constructs. ``start``/``stop`` mirror
    ``GeminiVoiceRuntime``'s own shape for familiarity, but there is no
    provider/hardware bridge here -- one Pipecat pipeline, start to stop."""

    router: ConversationRouter
    hw_worker: Any
    hw_runner: Any
    llm: Any
    aec_health: AecReferenceHealth
    _run_task: asyncio.Task | None = None

    async def start(self) -> bool:
        """Start the pipeline AND run the R0031 kickoff to completion
        (socket connect + one ``LLMRunFrame``) before returning -- so a
        caller that has awaited this knows the session is genuinely ready
        for the operator to speak, not just that the pipeline object graph
        exists. Returns whether the Gemini socket was observed connected
        before the kickoff timeout (mirrors the golden probe's own
        ``KICKOFF_SOCKET_CONNECTED``/``KICKOFF_SOCKET_TIMEOUT`` outcomes)."""
        if self.hw_runner is None:
            return False  # dry construction -- nothing to run
        await self.hw_runner.add_workers(self.hw_worker)
        self._run_task = asyncio.create_task(self.hw_runner.run())
        return await _kickoff(self.hw_worker, self.llm)

    async def stop(self, *, reason: str) -> None:
        if self.hw_runner is not None:
            try:
                await self.hw_runner.end(reason=reason)
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                logger.warning(
                    "nexa.realtime.gemini.simple_conversation: error stopping: %r", exc
                )


def build_cloud_realtime_conversation_adapter(
    *,
    session,
    api_key: str,
    snapshot: CloudContextSnapshot,
    audio_config: LocalAudioConfig | None = None,
    voice_preference: str = DEFAULT_VOICE_PREFERENCE,
    policy: ConversationPolicy = ConversationPolicy.CLOUD_PREFERRED,
    on_event: Any = None,
    on_aec_change: Any = None,
    dry: bool = False,
    recall_executor: RecallExecutor | None = None,
    on_diagnostic: Any = None,
    diagnostic_audio_levels: bool = False,
    coherent_reference_gain: bool = False,
) -> CloudRealtimeConversationAdapter:
    """Construct the simplified, golden-M2.6A-derived cloud voice adapter.

    ``diagnostic_audio_levels`` (R0081, optional, default ``False``): a
    SEPARATE, heavier-verbosity opt-in on top of ``on_diagnostic`` -- only
    when both this is ``True`` AND ``on_diagnostic`` is not ``None`` does
    a second, purely-observational mic-level tap get inserted into the
    pipeline (``MIC_RMS:...``) and does ``AecReferenceFeeder`` emit
    reference-signal telemetry (``REF_RMS:.../REF_QUEUE_DEPTH:.../
    REF_DROPPED:...``). Decoupled from the event-timeline markers
    (``on_diagnostic`` alone) so an operator can watch discrete events
    without the more continuous, numeric-level stream, or both together.
    Never logs/stores raw PCM. ``False`` (the default) means the pipeline
    shape is exactly the R0071/pre-R0081 stage list -- the mic-level tap
    is not constructed at all, not merely silent.

    ``coherent_reference_gain`` (R0081 §13/§B, optional, default
    ``False``): opt-in fix for a CONFIRMED, real defect (R0053, real
    ALSA-mixer system audit, never fixed on this specific code path --
    only on the paused M2.6B `runtime.py`): the AEC far-end reference fed
    to the XVF3800 is otherwise always unscaled, full-digital-amplitude
    PCM, completely disconnected from the audible output device's own
    independent ALSA hardware mixer -- so real speaker volume changes
    never change the reference amplitude a hardware AEC's adaptive filter
    models against, degrading cancellation. When ``True``, reads the
    audible device's real, current mixer gain (bounded-cost, cached --
    see ``CoherentReferenceGain``) and scales the reference PCM to match,
    mirroring EXACTLY the fix `runtime.py` already carries (same class,
    same construction pattern) -- never a new/invented mechanism.
    **Not proven sufficient alone** (R0054's own erratum: this fix
    reduced but did not eliminate false self-barge-in on the OTHER
    pipeline it was tested against, 5/5 -> 2/3 at MAX volume) -- an
    evidence-backed partial fix for a confirmed defect, not a claimed
    resolution. Default ``False`` preserves the exact R0071/R0080
    unscaled behavior byte-for-byte, so this is opt-in for A/B
    comparison, never a silent change to the frozen baseline's default.

    ``on_diagnostic`` (R0081 §4/§5, optional, default ``None``): a narrow,
    purely-additive diagnostic-timeline hook -- called with plain event
    labels (``"LOCAL_VAD_START"``, ``"BOT_AUDIO_STARTED"``,
    ``"TOOL_CALL_START"``, etc., see ``_ConversationEventTap._diag`` and
    ``core_recall_tool.make_recall_handler``) for frames/events this
    module already observes/handles unchanged. Never changes routing,
    interruption, or turn logic -- ``None`` (the default) is byte-for-byte
    R0080 behavior.

    ``dry=True`` builds the cloud-side objects (Settings, LLMContext,
    aggregator pair, GeminiLiveLLMService) without opening an audio device
    or a network connection -- mirrors the golden probe's own ``--dry`` and
    ``build_gemini_voice_runtime``'s own ``dry=True`` convention.

    ``recall_executor`` (R0079 / R0078 Revision 2 §5, optional, default
    ``None``): when supplied, registers the ``recall_context`` tool
    (``nexa.realtime.gemini.core_recall_tool``) on the constructed
    ``GeminiLiveLLMService`` and appends the tool-use instruction to the
    system instruction -- Pattern B turn-dynamic Core recall. ``None`` (the
    default) reproduces byte-for-byte pre-R0079 behavior: no tool
    registered, no instruction text appended, identical wire construction
    to every existing caller/test. This does NOT touch the audio/Pipecat
    pipeline, VAD, barge-in, AEC, or Gemini's own audio streaming -- it
    only adds ``tools=``/``register_function()`` to the SAME
    ``GeminiLiveLLMService`` construction that already exists here.
    """
    cfg = audio_config or LocalAudioConfig()
    router = ConversationRouter(session, policy=policy)
    P = _pipecat_imports()

    # Empty initial context on purpose (golden M2.6A's own proven choice):
    # the system instruction (built from the snapshot, below) is supplied
    # only via `system_instruction=`; recent turns are seeded as real
    # context messages instead, since -- unlike the spike, which
    # deliberately carried no NeXa history at all -- production conversation
    # must continue across a fresh cloud session. `inference_on_context_
    # initialization=False` still means no unprompted opening greeting is
    # generated from this seed.
    context_messages = [
        {"role": t.role.value, "content": t.content} for t in snapshot.recent_turns
    ]
    context = P["LLMContext"](messages=context_messages)
    user_agg, _asst_agg = P["LLMContextAggregatorPair"](
        context,
        user_params=P["LLMUserAggregatorParams"](
            vad_analyzer=P["SileroVADAnalyzer"](
                sample_rate=INPUT_SAMPLE_RATE_HZ,
                params=P["VADParams"](stop_secs=VAD_STOP_SECS),
            ),
        ),
        realtime_service_mode=True,
    )
    gemini_voice = gemini_voice_for_preference(voice_preference)
    system_instruction = snapshot.system_instruction
    tools = None
    if recall_executor is not None:
        system_instruction = f"{system_instruction}\n\n{RECALL_TOOL_USE_INSTRUCTION}"
        tools = [recall_tool_schema()]
    llm = P["GeminiLiveLLMService"](
        api_key=api_key,
        system_instruction=system_instruction,
        tools=tools,
        settings=P["GeminiLiveLLMService"].Settings(
            model=GEMINI_MODEL,
            modalities=P["GeminiModalities"].AUDIO,
            voice=gemini_voice,
            vad=P["GeminiVADParams"](disabled=True),
            context_window_compression=P["ContextWindowCompressionParams"](enabled=True),
            system_instruction=system_instruction,
        ),
        inference_on_context_initialization=False,
        user_audio_preroll_secs=None,
    )
    if recall_executor is not None:
        register_recall_tool(llm, recall_executor, on_diagnostic=on_diagnostic)

    if dry:
        return CloudRealtimeConversationAdapter(
            router=router, hw_worker=None, hw_runner=None, llm=llm,
            aec_health=AecReferenceHealth(),
        )

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
    aec_health = AecReferenceHealth(on_change=on_aec_change)
    # R0071: NOT gain-scaled BY DEFAULT. `AecReferenceFeeder` without
    # `gain_source` is byte-for-byte golden M2.6A's own reference path --
    # the required default behavior for this baseline.
    #
    # R0081 §13/§B: `coherent_reference_gain=True` opts into the SAME
    # confirmed-real-defect fix `runtime.py` already carries (R0053) --
    # never applied to THIS path before. See this function's own
    # docstring for the full evidence trail and the explicit "not proven
    # sufficient alone" caveat.
    gain_source = None
    if coherent_reference_gain:
        reference_gain = CoherentReferenceGain(card=cfg.output_alsa_mixer_card)
        gain_source = reference_gain.current_gain
    # R0081 §"ADD SIGNAL-LEVEL DIAGNOSTICS": audio-level telemetry
    # (mic-level tap insertion, AecReferenceFeeder RMS emission) is a
    # SEPARATE, heavier-verbosity opt-in from the event-timeline markers
    # -- both require a real sink (`on_diagnostic is not None`), but only
    # `diagnostic_audio_levels=True` additionally requests them.
    emit_audio_levels = on_diagnostic is not None and diagnostic_audio_levels
    aec_feeder = AecReferenceFeeder(
        aec_health=aec_health, sample_rate=OUTPUT_SAMPLE_RATE_HZ, channels=1,
        gain_source=gain_source,
        on_diagnostic=on_diagnostic if emit_audio_levels else None,
    )
    tap_cls = _make_event_tap_class(P)
    tap = tap_cls(router=router, on_event=on_event, on_diagnostic=on_diagnostic)

    # The mic-level tap is only ever inserted into the pipeline when
    # audio-level diagnostics are actually requested -- the frozen R0071
    # pipeline shape ([transport.input(), user_agg, llm, tap, aec_feeder,
    # transport.output(), _asst_agg]) is completely unchanged otherwise.
    stages = [transport.input()]
    if emit_audio_levels:
        mic_tap_cls = _make_mic_level_tap_class(P)
        stages.append(mic_tap_cls(on_diagnostic=on_diagnostic))
    stages.extend([user_agg, llm, tap, aec_feeder, transport.output(), _asst_agg])
    pipeline = P["Pipeline"](stages)
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

    return CloudRealtimeConversationAdapter(
        router=router, hw_worker=hw_worker, hw_runner=hw_runner, llm=llm,
        aec_health=aec_health,
    )
