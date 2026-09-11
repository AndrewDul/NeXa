"""``GeminiVoiceRuntime`` — the production HYBRID audio wiring for cloud
realtime voice (M2.6B.3/M2.6B.3A, ADR-0004 Decisions B/C/H).

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
It also observes ``BotStartedSpeakingFrame``/``BotStoppedSpeakingFrame``
(the real output-transport playback lifecycle, travelling upstream from
``transport.output()``) and feeds them to ``_ResponseLifecycle`` — see
M2.6B.3A below. It never holds a raw ``GeminiLiveProvider`` reference
directly; it holds a ``_ProviderHandle`` box so a mid-turn fresh-session
swap (M2.6B.3A §3) reaches it immediately.

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
either.

M2.6B.3A — three production-significant seams hardened after R0035, before
any real Gemini/hardware operator run (see
``docs/reports/R0036_m2_6b_3a_pre_live_hardening_20260911.md``):

1. **Generation-complete is not playback-complete.** ``_ResponseLifecycle``
   is the cloud analogue of ``nexa.voice.gate.HalfDuplexGate``'s own
   generation+playback combinator: ``bargein.notify_response_finished()``
   now fires only once BOTH the model's own generation is complete AND
   real ``BotStoppedSpeakingFrame`` playback truth confirms every
   already-queued chunk has drained (never a timer). A ``TTSStoppedFrame``
   is injected into the hardware pipeline, in FIFO order right after every
   audio chunk of a generation, so that confirmation is deterministic
   rather than depending on Pipecat's multi-second silence-fallback timer
   (installed source: ``BaseOutputTransport`` only fires
   ``BotStoppedSpeakingFrame`` off a genuine ``TTSStoppedFrame`` — if the
   audio actually arrived — or, absent one, a ``BOT_VAD_STOP_FALLBACK_SECS``
   (3s) silence timeout).
2. **Interrupted-turn assistant text is never trusted from raw
   transcription.** The installed Pipecat source
   (``gemini_live/llm.py:_handle_msg_output_transcription``) documents,
   verbatim, that Gemini's own output-transcription messages can arrive
   *before*, and contain *more text than*, the corresponding audio — "on an
   interruption our recorded context will contain some text that was
   actually never spoken" is the library's own comment. So
   ``CloudTurnAccumulator.assistant_text`` is never trusted directly as an
   interrupted turn's prefix.

   M2.6B.3A first tried a one-audio-chunk-lag "high-water" mechanism
   (promote text-as-of-the-previous-chunk once a new chunk arrives).
   **M2.6B.3B (see
   ``docs/reports/R0037_m2_6b_3b_interrupted_cloud_history_safety_20260911.md``)
   found that mechanism still overclaimed**: a later chunk's mere
   existence proves nothing about how much of an *earlier* text snapshot
   that chunk's own audio actually covers (the audio could be "abc" while
   the snapshot already reads "abcdef ghijkl..."), so it could still credit
   unspoken future text. A further source check (``google.genai.types.
   Transcription.words`` / ``WordInfo.start_offset``/``end_offset`` DOES
   exist in the underlying SDK's schema) found Pipecat's installed
   ``_handle_msg_output_transcription`` **never reads or forwards
   ``words``** — only the concatenated ``.text`` reaches any frame NeXa's
   provider can see — so no real, already-wired alignment data is
   reachable through this stack without bypassing Pipecat's own service
   entirely (out of scope; ADR-0004 already forbids a second raw Gemini
   client).

   **Conclusion: no deterministic alignment exists in the integrated
   stack. v1 rule, fully conservative:** an interrupted cloud turn's
   assistant text is always empty — ``router.set_spoken_prefix("")`` is
   called unconditionally on every confirmed interruption, then
   ``router.on_interruption()``. Under-crediting (committing no assistant
   text for an interrupted reply that did partly play) is acceptable;
   crediting text that was never actually spoken is not. Normal,
   non-interrupted completions are entirely unaffected — they still store
   the full final assistant transcription via ``GenerationCompleteEvent``,
   exactly as before.
3. **Mid-turn fresh-session recovery is actually driven.**
   ``ConversationRouter.recover_from_mid_turn_loss()`` existed since R0035
   but had no caller. ``_consume_provider_events`` now polls
   ``provider.needs_fresh_session`` after every event; once true it stops
   reading the OLD provider's queue (single-consumer preserved — never a
   second concurrent reader), invokes the recovery path, and — on success —
   atomically swaps ``self.provider`` **and** the shared ``_ProviderHandle``
   the VAD bridge reads from, then resumes consuming the NEW provider's
   queue. The old, already-``stop()``'d provider's queue is never read
   again, so it can never commit a late turn.

Construction only (``dry=True``): builds every object EXCEPT the audio
device / network — no ``pyaudio.PyAudio()``, no device index lookup, no
Gemini connection. This module has NOT been validated against real
hardware — see ``docs/reports/R0035_...``/``R0036_...`` for exactly what
remains for a real operator run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
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
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
        TTSStoppedFrame,
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
        "BotStartedSpeakingFrame": BotStartedSpeakingFrame,
        "BotStoppedSpeakingFrame": BotStoppedSpeakingFrame,
        "InputAudioRawFrame": InputAudioRawFrame,
        "TTSAudioRawFrame": TTSAudioRawFrame,
        "TTSStoppedFrame": TTSStoppedFrame,
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


class _ProviderHandle:
    """A tiny mutable box holding the CURRENTLY active ``GeminiLiveProvider``
    (M2.6B.3A §3). The VAD bridge holds this box, never a raw provider
    reference, so a mid-turn fresh-session swap
    (``GeminiVoiceRuntime._consume_provider_events``) reaches it the
    instant ``self.current`` is reassigned — the bridge can never keep
    sending audio to a dead, already-``stop()``'d provider instance."""

    def __init__(self, provider: GeminiLiveProvider) -> None:
        self.current = provider


class _ResponseLifecycle:
    """Cloud analogue of ``nexa.voice.gate.HalfDuplexGate``'s own
    generation+playback combinator (M2.6B.3A §1): a response is finished
    — safe to call ``on_finished()`` — only once BOTH the model's own
    generation is complete AND, if any audio was actually produced, a real
    ``BotStoppedSpeakingFrame`` confirms every already-queued chunk has
    drained. Never a timer; never "generation complete" alone.

    Unlike ``HalfDuplexGate`` (a passive property queried on demand) this
    actively fires ``on_finished()`` exactly once per response, the moment
    the combined state settles — ``BargeInController.notify_response_finished()``
    is event-driven, not polled.
    """

    def __init__(self, *, on_finished: Callable[[], None]) -> None:
        self._generating = False
        self._produced_audio = False
        self._bot_speaking = False
        self._playback_confirmed_drained = False
        self._finished_fired = False
        self._on_finished = on_finished

    def mark_dispatched(self) -> None:
        """A new response has started generating."""
        self._generating = True
        self._produced_audio = False
        self._bot_speaking = False
        self._playback_confirmed_drained = False
        self._finished_fired = False

    def mark_audio_produced(self) -> None:
        """At least one ``AssistantAudioEvent`` was seen for this response
        (known directly from the provider event stream — never inferred
        from ``BotStartedSpeakingFrame``, which can lag real delivery by a
        full pipeline hop)."""
        self._produced_audio = True

    def mark_generation_done(self) -> None:
        self._generating = False
        self._maybe_fire()

    def observe_bot_started(self) -> None:
        self._bot_speaking = True

    def observe_bot_stopped(self) -> None:
        self._bot_speaking = False
        if not self._generating:
            self._playback_confirmed_drained = True
        self._maybe_fire()

    def mark_interrupted(self) -> None:
        """A confirmed local interruption ends this response's lifecycle
        immediately (``BargeInController.notify_interruption_complete()``
        is called directly by the caller) — suppress a stale
        ``on_finished`` from a generation-complete/bot-stopped signal that
        arrives later for the response that was just talked over."""
        self._generating = False
        self._bot_speaking = False
        self._finished_fired = True

    def _maybe_fire(self) -> None:
        if self._finished_fired or self._generating:
            return
        if self._produced_audio and not self._playback_confirmed_drained:
            return  # audio was produced; still waiting for real BotStopped truth
        if self._bot_speaking:
            return  # defensive; should not combine with the above
        self._finished_fired = True
        self._on_finished()


#: M2.6B.3B — no deterministic assistant-text/audio alignment is reachable
#: through the installed Pipecat/google-genai stack (see the module
#: docstring's §2 for the source evidence: ``WordInfo.start_offset``/
#: ``end_offset`` exist in the SDK's own type schema, but Pipecat's
#: installed ``_handle_msg_output_transcription`` never reads or forwards
#: them). An interrupted cloud turn's committed assistant text is
#: therefore always this constant — never a partial-credit approximation
#: built from audio-chunk count, which cannot logically prove how much of
#: an earlier transcription snapshot a later chunk's own audio covers.
#: Under-crediting is acceptable; crediting unspoken words is not.
CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""


def _make_vad_bridge_class(P: dict[str, Any]) -> type:
    """The one new FrameProcessor: local VAD frames -> provider turn I/O,
    and real playback-lifecycle frames -> ``_ResponseLifecycle``. Built
    lazily (needs ``FrameProcessor``, only available post-import)."""

    class _VadToProviderBridge(P["FrameProcessor"]):
        def __init__(
            self,
            *,
            provider_handle: _ProviderHandle,
            metrics: RuntimeMetrics,
            lifecycle: _ResponseLifecycle,
        ) -> None:
            super().__init__()
            self._handle = provider_handle
            self._metrics = metrics
            self._lifecycle = lifecycle
            self._turn_open = False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["VADUserStartedSpeakingFrame"]):
                self._turn_open = True
                self._metrics.local_vad_start()
                await self._handle.current.user_turn_start()
            elif isinstance(frame, P["InputAudioRawFrame"]) and self._turn_open:
                await self._handle.current.send_user_audio(frame.audio)
            elif isinstance(frame, P["VADUserStoppedSpeakingFrame"]):
                self._turn_open = False
                self._metrics.local_vad_eot()
                await self._handle.current.user_turn_end()
            elif isinstance(frame, P["BotStartedSpeakingFrame"]):
                self._lifecycle.observe_bot_started()
                self._metrics.first_assistant_audio_played()
            elif isinstance(frame, P["BotStoppedSpeakingFrame"]):
                self._lifecycle.observe_bot_stopped()
            await self.push_frame(frame, direction)

    return _VadToProviderBridge


@dataclass
class RuntimeMetrics:
    """Lightweight production metrics (ADR-0004 M2.6B charter, Phase 5) —
    logged only, no raw audio retained, no credential ever logged."""

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
        # Fires once per BotStartedSpeakingFrame; only the FIRST one this
        # session is a distinct milestone worth its own line.
        if not self._first_audio_played_marked:
            self._first_audio_played_marked = True
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

    def mid_turn_recovery(self, *, succeeded: bool) -> None:
        logger.info(
            "nexa.realtime.metrics: mid-turn fresh-session recovery %s",
            "SUCCEEDED" if succeeded else "FAILED (fell back to LOCAL)",
        )

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
    provider_handle: _ProviderHandle
    router: ConversationRouter
    hw_worker: Any
    hw_runner: Any
    aec_health: AecReferenceHealth
    bargein: BargeInController
    metrics: RuntimeMetrics
    lifecycle: _ResponseLifecycle
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
    _recovery_language_preference: str | None = field(default=None, repr=False)

    async def start(self, snapshot: CloudContextSnapshot) -> None:
        await self.provider.start(snapshot)
        self.provider_handle.current = self.provider
        self._recovery_language_preference = snapshot.language_preference
        self.metrics.provider_readiness(self.provider.readiness)
        await self.hw_runner.add_workers(self.hw_worker)
        self._hw_run_task = asyncio.create_task(self.hw_runner.run())
        self._events_task = asyncio.create_task(self._consume_provider_events())

    async def _consume_provider_events(self) -> None:
        """Drain ``provider.events()`` and (1) drive the canonical write
        path via the router's one true entry point, (2) keep
        ``BargeInController``'s own state machine in sync via
        ``_ResponseLifecycle`` (real generation+playback truth, never a
        naive generation-complete==finished conflation), (3) inject
        assistant audio (and a deterministic ``TTSStoppedFrame``) into the
        hardware pipeline, and (4) drive mid-turn fresh-session recovery
        the instant ``provider.needs_fresh_session`` is observed true —
        never a second concurrent ``provider.events()`` reader; the OLD
        provider's queue is abandoned (never read again) the moment
        recovery begins."""
        P = _pipecat_hw_imports()
        first_audio_seen = False
        response_dispatched = False

        while True:
            provider = self.provider
            recovered = False
            async for event in provider.events():
                if self.on_event is not None:
                    try:
                        self.on_event(event)
                    except Exception:  # noqa: BLE001 - an observer must never break audio
                        logger.exception(
                            "nexa.realtime.gemini.runtime: on_event hook raised"
                        )

                if isinstance(
                    event, (AssistantAudioEvent, AssistantTranscriptionEvent)
                ) and not response_dispatched:
                    response_dispatched = True
                    self.bargein.notify_response_dispatched()
                    self.lifecycle.mark_dispatched()

                outcome = self.router.handle_provider_event(event)

                if isinstance(event, GenerationCompleteEvent):
                    self.lifecycle.mark_generation_done()
                    # Deterministic BotStopped confirmation (M2.6B.3A §1):
                    # queued strictly after every audio chunk of this
                    # generation (same FIFO the audio chunks went through),
                    # so the output transport fires a real
                    # BotStoppedSpeakingFrame once (and only once) that
                    # whole queue has actually drained -- never a
                    # multi-second silence-fallback guess.
                    await self.hw_worker.queue_frames([P["TTSStoppedFrame"]()])
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
                    self.lifecycle.mark_audio_produced()
                    frame = P["TTSAudioRawFrame"](
                        audio=event.pcm, sample_rate=OUTPUT_SAMPLE_RATE_HZ, num_channels=1
                    )
                    await self.hw_worker.queue_frames([frame])

                if provider.needs_fresh_session:
                    new_provider = await self.router.recover_from_mid_turn_loss(
                        old_provider=provider,
                        language_preference=self._recovery_language_preference,
                    )
                    if new_provider is None:
                        # Router already fell back to LOCAL (Decision J
                        # notice) -- nothing more to consume on the cloud
                        # side; the old provider is stopped and its queue
                        # is abandoned here, never read again.
                        self.metrics.mid_turn_recovery(succeeded=False)
                        return
                    self.metrics.mid_turn_recovery(succeeded=True)
                    self.provider = new_provider
                    self.provider_handle.current = new_provider
                    response_dispatched = False
                    recovered = True
                    break  # stop reading the OLD provider's queue

            if not recovered:
                return  # the provider's own events() ended (stop()/cancel)

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
    on_aec_change: Callable[[bool], None] | None = None,
    dry: bool = False,
) -> GeminiVoiceRuntime:
    """Construct the full HYBRID cloud-voice runtime.

    ``dry=True`` builds every object EXCEPT the audio device / network:
    validates the object graph (mirrors the M2.6A probe's own ``--dry``),
    no ``pyaudio.PyAudio()`` call, no device index lookup, no Gemini
    connection. Use it to catch construction errors before ever touching
    hardware or spending Gemini quota.

    ``on_aec_change`` (M2.6B.3A §5) is composed with the metrics logger —
    both fire on every real ``AecReferenceHealth`` transition; this is the
    ONE supported way for a caller (e.g. the operator app) to observe AEC
    status, rather than reaching into ``aec_health``'s private callback
    attribute after construction (which would silently replace, not
    compose with, the metrics logging).
    """
    cfg = audio_config or LocalAudioConfig()
    metrics = RuntimeMetrics()

    provider = GeminiLiveProvider(api_key=api_key, voice_preference=voice_preference)
    provider_handle = _ProviderHandle(provider)

    def _cloud_factory() -> GeminiLiveProvider:
        return GeminiLiveProvider(api_key=api_key, voice_preference=voice_preference)

    router = ConversationRouter(
        session, policy=policy, cloud_provider_factory=_cloud_factory
    )

    def _combined_aec_change(active: bool) -> None:
        metrics.aec_reference(active)
        if on_aec_change is not None:
            on_aec_change(active)

    aec_health = AecReferenceHealth(on_change=_combined_aec_change)
    lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())

    def _on_confirmed(ctx: InterruptContext) -> None:
        metrics.local_interruption_confirmed()
        # Local speaker-stop authority already happened: BargeInController
        # calls Pipecat's own broadcast_interruption() (which tears down
        # queued/playing output audio) BEFORE this hook runs, and that call
        # never waits on anything below -- the R0034 late-server-event
        # protections stay in force regardless of what the cloud side does.
        #
        # M2.6B.3B -- the interrupted spoken prefix is UNCONDITIONALLY
        # empty, never the raw running CloudTurnAccumulator.assistant_text
        # and never a partial-credit approximation. Installed Pipecat
        # source proves output-transcription can arrive ahead of, and
        # contain more text than, the audio actually produced, and a
        # one-audio-chunk-lag mechanism (M2.6B.3A's first attempt) still
        # cannot prove how much of an EARLIER text snapshot a LATER
        # chunk's own audio actually covers. No deterministic
        # assistant-text/audio alignment is reachable through the
        # installed Pipecat/google-genai stack (see the module docstring
        # §2) -- so under-crediting (no assistant text at all for an
        # interrupted reply) is the only safe choice; crediting unspoken
        # words is not acceptable. Set BEFORE on_interruption() per the
        # charter's own example ordering (functionally either order is
        # safe -- neither accumulator method depends on the other having
        # already run).
        router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)
        router.on_interruption()
        lifecycle.mark_interrupted()
        # provider_handle.current (never the construction-time `provider`
        # local, which could be a stale, already-swapped-out instance
        # after a mid-turn fresh-session recovery) -- cancel() is async;
        # this hook is sync (BargeInController's contract) -- fire-and-
        # forget is correct: cancellation reaching the cloud side is not
        # on the critical path for local speaker-stop.
        asyncio.create_task(provider_handle.current.cancel())
        # Cloud turns need no segment-coalescing capture phase (unlike the
        # local path): the interrupting utterance is simply the next local
        # VAD turn the bridge opens. Exit INTERRUPTING immediately so
        # BargeInController is ready to admit a future interruption again.
        bargein.notify_interruption_complete()

    bargein = BargeInController(aec_health=aec_health, on_confirmed=_on_confirmed)

    if dry:
        return GeminiVoiceRuntime(
            provider=provider,
            provider_handle=provider_handle,
            router=router,
            hw_worker=None,
            hw_runner=None,
            aec_health=aec_health,
            bargein=bargein,
            metrics=metrics,
            lifecycle=lifecycle,
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
    bridge = bridge_cls(provider_handle=provider_handle, metrics=metrics, lifecycle=lifecycle)
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
        provider_handle=provider_handle,
        router=router,
        hw_worker=hw_worker,
        hw_runner=hw_runner,
        aec_health=aec_health,
        bargein=bargein,
        metrics=metrics,
        lifecycle=lifecycle,
        on_event=on_event,
    )
