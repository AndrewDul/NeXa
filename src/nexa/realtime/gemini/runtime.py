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

M2.6B.4 (R0038) — the FIRST real operator hardware/Gemini run FAILED with
three regressions, fixed here (see
``docs/reports/R0038_m2_6b_4_hardware_acceptance_attempt1_fail_20260911.md``):

1. **`_VadToProviderBridge` setup/cleanup crashed.** Root cause: Pipecat's
   own ``FrameProcessor.__init__`` already assigns
   ``self._metrics = metrics or FrameProcessorMetrics()`` and its
   ``setup()``/``cleanup()`` call ``self._metrics.setup(...)``/
   ``self._metrics.cleanup()`` (installed source,
   ``frame_processor.py:256,653,669``) — NeXa's own ``__init__`` stored
   ``RuntimeMetrics`` under that SAME attribute name, silently clobbering
   Pipecat's real metrics object, so Pipecat's own lifecycle went on to
   call ``.setup()``/``.cleanup()`` on NeXa's telemetry instead. Fixed:
   renamed to ``self._nexa_metrics`` — never share an attribute name with
   any Pipecat-reserved one on a ``FrameProcessor`` subclass.
2. **Local playback did not stop on barge-in; the interrupted reply kept
   playing while the new one was already generating.**
   ``BargeInController.broadcast_interruption()`` (unmodified) only clears
   what ``BaseOutputTransport``'s own audio queue held at that instant —
   it does nothing about a provider event already sitting in
   ``provider.events()``'s own queue, or audio Gemini keeps generating for
   a moment after the cancel signal. Fixed with ``_ResponseGenerationGuard``:
   every ``AssistantAudioEvent`` is checked against the currently VALID
   response-generation id before it is ever handed to
   ``hw_worker.queue_frames()``; a confirmed local interruption
   invalidates the current generation immediately (synchronously, so
   there is no race on the single-threaded event loop) — "hard local
   output clear (``broadcast_interruption``) + generation invalidation
   (this guard)", never either alone.
3. **Native language mirroring (ADR-0004 Option A) failed live** — two
   English questions both answered in Polish. Reconstructed the exact
   production snapshot: ``apps/nexa_cloud_voice_app.py`` never passed
   ``language_preference`` to the snapshot builder (defaults to ``None``)
   and ``build_default_session()`` starts with empty history — so the
   snapshot IS neutral (no forced Polish bias anywhere in
   ``system_instruction``/``recent_turns``); this is a genuine Gemini
   native-mirroring reliability gap, not a NeXa-side bug, and activates
   ADR-0004's own documented Option-B fallback. Added optional, local,
   offline, same-turn PL/EN diagnostics — reusing the ALREADY-ACCEPTED
   local-voice mechanisms verbatim: ``nexa.stt.WhisperCppLanguageDetector``
   (R0024's ``argmax(p_pl, p_en)`` LID) and
   ``nexa.conversation.ResponseLanguageResolver`` (sticky preference set
   ONLY on an explicit directive, never from the language merely spoken).
   This updates ``self._recovery_language_preference`` for any FUTURE
   fresh-session snapshot (ADR-0004 Amendment 1 §2's own documented
   mechanism) and logs the charter's exact diagnostic keys
   (``SNAPSHOT_LANGUAGE_PREFERENCE``/``TURN_INPUT_LANGUAGE``/
   ``TURN_INPUT_TRANSCRIPT``/``LANGUAGE_ROUTING_MODE``) — it does **not**
   retroactively steer the response Gemini is already generating for the
   CURRENT turn (native mirroring remains the active per-turn mechanism);
   no verified, low-risk same-turn steering primitive was found reachable
   through the installed stack without a live call to test it, so that
   remains an explicit, honestly-documented open gap, not attempted here.

Construction only (``dry=True``): builds every object EXCEPT the audio
device / network — no ``pyaudio.PyAudio()``, no device index lookup, no
Gemini connection. This module has NOT been validated against real
hardware — see ``docs/reports/R0035_...``/``R0036_...``/``R0038_...`` for
exactly what remains for a real operator run.
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
    UserTranscriptionEvent,
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


class _ResponseGenerationGuard:
    """M2.6B.4 (R0038 FAILURE 2) — the single source of truth for which
    local response generation may still produce audible output.

    Root cause this exists to close: ``BargeInController.broadcast_interruption()``
    (Pipecat's own mechanism, unchanged) clears only what
    ``BaseOutputTransport``'s audio queue held at the instant it ran
    (installed source: cancels/recreates the output audio task, or resets
    the queue) — it does nothing about a provider event still sitting in
    ``provider.events()``'s own queue, or audio Gemini keeps generating
    for a few moments after receiving the cancel signal. Confirmed live in
    the first real operator run: an interrupted reply's audio kept
    playing to completion while the NEW reply was already being
    generated. This guard is what stops those late frames from ever
    reaching ``hw_worker.queue_frames()`` in the first place — "hard local
    output clear (``broadcast_interruption``) + generation invalidation
    (this guard)", never either alone, per the charter.

    Deliberately does NOT decide *when* a new generation is dispatched —
    an earlier draft tried to fold "the next assistant output needs a
    fresh dispatch" into this guard too, keyed off the same
    interrupt-vs-valid state, and that conflated two different signals:
    "may this chunk play" (this class) and "has a genuinely NEW local
    user turn started" (only a fresh, final ``UserTranscriptionEvent`` --
    the interrupting utterance's own -- proves that; R0034's own proven
    message-ordering guarantee is what makes this reliable). Trailing
    audio for an invalidated generation and the interrupting utterance's
    own brand-new reply both arrive as plain ``AssistantAudioEvent``s
    with no marker distinguishing them, so conflating the two signals
    made a late, invalid chunk look like a fresh, valid dispatch. Kept
    deliberately dumb: only ``is_valid``/``start_new_generation``/
    ``interrupt`` — the caller (``_consume_provider_events``) decides the
    dispatch boundary from ``UserTranscriptionEvent(final=True)``.
    """

    def __init__(self) -> None:
        self._current_id = 0
        self._valid_id = 0

    def start_new_generation(self) -> int:
        self._current_id += 1
        self._valid_id = self._current_id
        return self._current_id

    def is_valid(self, generation_id: int) -> bool:
        return generation_id == self._valid_id

    def interrupt(self) -> None:
        """The CURRENT generation can never produce audible output again.
        Called synchronously from ``_on_confirmed`` (no ``await`` before
        it in that function), so this always completes before any other
        coroutine gets a chance to run on this single-threaded event
        loop — no race with ``_consume_provider_events``'s own check."""
        self._valid_id = 0  # 0 is never a real id (the counter starts at 1)


class _PendingUtteranceAudio:
    """M2.6B.4 FAILURE 3 — a tiny FIFO correlating each closed user
    utterance's locally-buffered PCM with the FINAL transcription that
    later arrives for it from the provider's own event stream. Safe
    because turn-taking here is strictly sequential (R0034's own proven
    message-ordering guarantee): one utterance closes locally, then its
    transcription arrives, before the next utterance can open."""

    def __init__(self) -> None:
        self._pending: list[bytes] = []

    def push(self, pcm: bytes) -> None:
        self._pending.append(pcm)

    def pop_oldest(self) -> bytes | None:
        if not self._pending:
            return None
        return self._pending.pop(0)


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
            pending_utterance_audio: _PendingUtteranceAudio | None = None,
        ) -> None:
            super().__init__()
            self._handle = provider_handle
            # M2.6B.4 (R0038 FAILURE 1) -- ``FrameProcessor.__init__`` itself
            # already sets ``self._metrics = metrics or FrameProcessorMetrics()``
            # (installed source, frame_processor.py:256) and its own
            # ``setup()``/``cleanup()`` call ``self._metrics.setup(...)``/
            # ``self._metrics.cleanup()`` (frame_processor.py:653/669) on
            # WHATEVER object holds that name. Storing NeXa's RuntimeMetrics
            # under the SAME attribute name silently clobbered Pipecat's own
            # ``FrameProcessorMetrics`` instance, so Pipecat's real lifecycle
            # went on to call ``.setup()``/``.cleanup()`` on NeXa's telemetry
            # object instead -- confirmed live in the first real operator
            # run ("'RuntimeMetrics' object has no attribute 'setup'"),
            # reproduced deterministically in
            # ``TestVadBridgeProcessorLifecycle``. RuntimeMetrics is NeXa
            # runtime telemetry, never a Pipecat processor-metrics object --
            # a distinctly NeXa-prefixed attribute name is required for
            # every NeXa-owned field on a Pipecat FrameProcessor subclass.
            self._nexa_metrics = metrics
            self._lifecycle = lifecycle
            self._turn_open = False
            # M2.6B.4 FAILURE 3 -- a PARALLEL local copy of the utterance
            # audio, accumulated purely for offline same-turn language
            # detection (never delays or alters what streams live to
            # Gemini via ``send_user_audio`` below).
            self._pending_utterance_audio = pending_utterance_audio
            self._utterance_buffer = bytearray()

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["VADUserStartedSpeakingFrame"]):
                self._turn_open = True
                self._utterance_buffer = bytearray()
                self._nexa_metrics.local_vad_start()
                await self._handle.current.user_turn_start()
            elif isinstance(frame, P["InputAudioRawFrame"]) and self._turn_open:
                self._utterance_buffer.extend(frame.audio)
                await self._handle.current.send_user_audio(frame.audio)
            elif isinstance(frame, P["VADUserStoppedSpeakingFrame"]):
                self._turn_open = False
                self._nexa_metrics.local_vad_eot()
                await self._handle.current.user_turn_end()
                if self._pending_utterance_audio is not None and self._utterance_buffer:
                    self._pending_utterance_audio.push(bytes(self._utterance_buffer))
                self._utterance_buffer = bytearray()
            elif isinstance(frame, P["BotStartedSpeakingFrame"]):
                self._lifecycle.observe_bot_started()
                self._nexa_metrics.first_assistant_audio_played()
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

    def dropped_invalidated_generation_audio(self, *, generation_id: int) -> None:
        # M2.6B.4 FAILURE 2 -- visibility into the generation guard
        # actually doing its job: a chunk belonging to an interrupted
        # generation was correctly kept off the hardware pipeline.
        logger.info(
            "nexa.realtime.metrics: dropped invalidated-generation audio generation_id=%d",
            generation_id,
        )

    def language_diagnostics(
        self,
        *,
        snapshot_language_preference: str | None,
        turn_input_language: str,
        turn_input_transcript: str,
        routing_mode: str,
    ) -> None:
        # M2.6B.4 FAILURE 3 -- the exact diagnostic keys the R0038 charter
        # asked for, safe (no credentials, transcript is the operator's
        # own already-printed speech, never raw audio).
        logger.info(
            "nexa.realtime.metrics: SNAPSHOT_LANGUAGE_PREFERENCE=%s "
            "TURN_INPUT_LANGUAGE=%s TURN_INPUT_TRANSCRIPT=%r LANGUAGE_ROUTING_MODE=%s",
            snapshot_language_preference or "none",
            turn_input_language,
            turn_input_transcript,
            routing_mode,
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
    generation_guard: _ResponseGenerationGuard
    pending_utterance_audio: _PendingUtteranceAudio
    #: ``None`` when local whisper.cpp LID is unavailable on this machine
    #: (optional enhancement, never a hard dependency of cloud voice).
    language_detector: Any = None
    language_resolver: Any = None
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
        hardware pipeline -- but ONLY for the CURRENTLY VALID response
        generation (M2.6B.4 FAILURE 2: ``_ResponseGenerationGuard`` drops
        anything belonging to a generation a confirmed local interruption
        already invalidated, no matter how many more events for it were
        already in flight), (4) drive mid-turn fresh-session recovery the
        instant ``provider.needs_fresh_session`` is observed true — never
        a second concurrent ``provider.events()`` reader; the OLD
        provider's queue is abandoned (never read again) the moment
        recovery begins, and (5) correlate each closed user utterance's
        locally-buffered audio with its final transcription for offline
        same-turn language diagnostics (M2.6B.4 FAILURE 3) -- fire-and-
        forget, never on the critical audio path."""
        P = _pipecat_hw_imports()
        first_audio_seen = False
        current_gid = 0
        # True once the CURRENT open user turn's assistant output has
        # already been dispatched -- reset specifically on a fresh, FINAL
        # UserTranscriptionEvent (a genuinely NEW local user turn was just
        # heard), never merely because a generation was interrupted. This
        # is what correctly tells apart "late trailing audio for the
        # generation a barge-in just invalidated" (arrives while this is
        # still True -- no new final transcription has arrived yet) from
        # "the interrupting utterance's own brand-new reply" (arrives
        # only after ITS OWN final transcription resets this to False;
        # R0034's proven ordering guarantees input transcription always
        # precedes that turn's own assistant content).
        dispatched_for_turn = False

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
                ) and not dispatched_for_turn:
                    dispatched_for_turn = True
                    current_gid = self.generation_guard.start_new_generation()
                    self.bargein.notify_response_dispatched()
                    self.lifecycle.mark_dispatched()

                outcome = self.router.handle_provider_event(event)

                if isinstance(event, UserTranscriptionEvent) and event.final:
                    dispatched_for_turn = False
                    self.metrics.input_transcription_final(event.text)
                    pcm = self.pending_utterance_audio.pop_oldest()
                    if pcm is not None:
                        asyncio.create_task(self._analyze_turn_language(pcm, event.text))
                elif isinstance(event, GenerationCompleteEvent):
                    self.lifecycle.mark_generation_done()
                    if self.generation_guard.is_valid(current_gid):
                        # Deterministic BotStopped confirmation (M2.6B.3A
                        # §1): queued strictly after every audio chunk of
                        # this generation (same FIFO the audio chunks
                        # went through), so the output transport fires a
                        # real BotStoppedSpeakingFrame once (and only
                        # once) that whole queue has actually drained --
                        # never a multi-second silence-fallback guess. A
                        # late generation-complete for an already-
                        # invalidated generation never re-stops anything.
                        await self.hw_worker.queue_frames([P["TTSStoppedFrame"]()])
                elif isinstance(event, RealtimeProviderFailedError):
                    dispatched_for_turn = False
                    self.generation_guard.interrupt()

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
                    if self.generation_guard.is_valid(current_gid):
                        frame = P["TTSAudioRawFrame"](
                            audio=event.pcm, sample_rate=OUTPUT_SAMPLE_RATE_HZ,
                            num_channels=1,
                        )
                        await self.hw_worker.queue_frames([frame])
                    else:
                        # A confirmed local interruption already invalidated
                        # this generation -- this chunk was already in
                        # flight (queued on provider.events() before the
                        # interruption, or produced by Gemini in the brief
                        # window before it honoured the cancel signal) and
                        # must NEVER reach the speaker or the AEC reference.
                        self.metrics.dropped_invalidated_generation_audio(
                            generation_id=current_gid
                        )

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
                    dispatched_for_turn = False
                    self.generation_guard.interrupt()
                    recovered = True
                    break  # stop reading the OLD provider's queue

            if not recovered:
                return  # the provider's own events() ended (stop()/cancel)

    async def _analyze_turn_language(self, pcm: bytes, transcript: str) -> None:
        """M2.6B.4 FAILURE 3 -- offline, same-turn PL/EN diagnostics.
        Fire-and-forget (``asyncio.create_task``, never awaited inline
        from the event loop) so a slow or failing detector never adds
        latency to, or breaks, the live turn-taking path. Reuses the
        SAME mechanism already accepted for local voice
        (``nexa.stt.WhisperCppLanguageDetector`` — R0024's own
        ``argmax(p_pl, p_en)`` method, proven to classify every
        monolingual corpus item correctly) and the SAME
        provider-agnostic sticky/one-turn resolver already accepted for
        local voice (``nexa.conversation.ResponseLanguageResolver`` — it
        sets a sticky preference ONLY on an explicit directive like
        "always answer in English"/"odpowiadaj mi po polsku", never from
        the language merely spoken). Updates
        ``self._recovery_language_preference`` ONLY on a sticky decision
        -- ADR-0004 Amendment 1 §2: a sticky command reaches the provider
        only on the next new/resumed session, never retroactively steers
        the response already in flight for THIS turn (native mirroring,
        Option A, remains the active per-turn mechanism; see R0038 for
        why a same-turn steering mechanism is not implemented here)."""
        if self.language_detector is None:
            return
        try:
            result = await self.language_detector.detect(pcm)
        except Exception:  # noqa: BLE001 - LID is an optional enhancement
            logger.exception("nexa.realtime.gemini.runtime: language detection failed")
            return
        input_language = "pl" if result.p_pl >= result.p_en else "en"
        decision = None
        if self.language_resolver is not None:
            decision = self.language_resolver.resolve(transcript, input_language=input_language)
            if decision.preference_changed:
                self._recovery_language_preference = decision.sticky_after
        self.metrics.language_diagnostics(
            snapshot_language_preference=self._recovery_language_preference,
            turn_input_language=input_language,
            turn_input_transcript=transcript,
            routing_mode="native",
        )

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
    generation_guard = _ResponseGenerationGuard()
    pending_utterance_audio = _PendingUtteranceAudio()

    # M2.6B.4 FAILURE 3 -- local same-turn language LID is an optional
    # enhancement (diagnostics + future-session sticky-preference
    # persistence only, never a hard dependency of cloud voice: if
    # whisper.cpp isn't installed/configured on this machine, cloud voice
    # still runs exactly as before, just without this diagnostic).
    language_detector: Any = None
    try:
        from nexa.stt import WhisperCppLanguageDetector

        language_detector = WhisperCppLanguageDetector()
    except Exception as exc:  # noqa: BLE001 - optional enhancement
        logger.warning(
            "nexa.realtime.gemini.runtime: local language detector unavailable "
            "(%r) -- cloud voice continues without same-turn language "
            "diagnostics", exc,
        )
    language_resolver: Any = None
    if language_detector is not None:
        from nexa.conversation import ResponseLanguageResolver

        language_resolver = ResponseLanguageResolver()

    def _on_confirmed(ctx: InterruptContext) -> None:
        metrics.local_interruption_confirmed()
        # M2.6B.4 FAILURE 2 -- invalidate the CURRENT response generation
        # BEFORE anything else below (see _ResponseGenerationGuard's own
        # docstring for why this is race-free on a single-threaded event
        # loop): broadcast_interruption() (already called by
        # BargeInController just before this hook, per its own contract)
        # only clears what the hardware output queue held at this
        # instant -- this guard is what stops any MORE audio for this
        # same generation, already in flight on provider.events()'s own
        # queue, from ever reaching hw_worker.queue_frames() afterward.
        generation_guard.interrupt()
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
            generation_guard=generation_guard,
            pending_utterance_audio=pending_utterance_audio,
            language_detector=language_detector,
            language_resolver=language_resolver,
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
    bridge = bridge_cls(
        provider_handle=provider_handle,
        metrics=metrics,
        lifecycle=lifecycle,
        pending_utterance_audio=pending_utterance_audio,
    )
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
        generation_guard=generation_guard,
        pending_utterance_audio=pending_utterance_audio,
        language_detector=language_detector,
        language_resolver=language_resolver,
        on_event=on_event,
    )
