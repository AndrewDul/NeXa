"""``VoiceConversationAdapter`` — M2.3's cross-boundary integration layer
(ADR-0003 D2): the only new wiring between voice/STT and the existing,
unchanged ``ConversationSession``.

ADR-0003 D2 sketched this as a Pipecat ``FrameProcessor`` (illustratively
named ``NeXaConversationFrameProcessor``) receiving a ``TextFrame`` and
pushing streamed chunks back downstream toward TTS. That shape assumed TTS
(M2.4) already existed to receive those frames. M2.3 has no TTS yet — M2.2
already established a simpler, non-Pipecat seam (`VoiceRuntime`'s plain
``on_transcription``/``on_transcription_error`` callbacks), so this adapter
is a plain Python class driven by those callbacks, not a ``FrameProcessor``.
D2's actual requirement — ``ConversationSession`` stays the sole
conversation authority, no second history/persona/model/provider choice —
is fully honored; only the illustrative mechanism differs from what D2
sketched, which the ADR text explicitly left open for M2.3 to decide.

This adapter owns nothing conversation-shaped itself: no history, no
context, no persona, no model/provider choice. It receives one
``nexa.stt.TranscriptionResult`` at a time and calls the exact same
``ConversationSession.send()`` typed chat (`apps/nexa_chat.py`) already
uses — literally the same session instance, not a second one.

M2.4B.5: when constructed with a ``ResponseLanguageResolver``, the adapter
also resolves the *response* language for each turn (mirror the spoken
input language by default; honour an explicit request / sticky session
preference) and passes it to ``ConversationSession.send(response_language=…)``.
Input-language detection + the re-decode guard live entirely in
``nexa.stt`` (``BilingualSpeechTranscriber``); the adapter only reads the
already-resolved ``TranscriptionResult.language`` and its
``language_decision`` telemetry.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from nexa.conversation.response_language import ResponseLanguageResolver
from nexa.conversation.response_mode import ResponseMode
from nexa.conversation.session import ConversationSession, InterruptedTurnOutcome
from nexa.providers.base import CancelToken
from nexa.stt.bilingual import LanguageDecision
from nexa.stt.transcriber import TranscriptionResult

from .queue import DEFAULT_MAX_QUEUE_SIZE, ConversationQueueOverflowError, SerialConversationQueue

#: M2.4B.5A: reason logged when an STT result arrives while a conversation
#: turn is already in flight and is therefore dropped (not enqueued).
DROP_BUSY_RESPONSE_IN_FLIGHT = "DROP_BUSY_RESPONSE_IN_FLIGHT"

#: M2.5B.3 — an STT result arriving within this window after an interruption
#: capture finalised is treated as a late segment of that same interruption
#: (its VAD segment-END was delivered late / flushed by
#: ``broadcast_interruption()``), discarded quietly rather than counted as a
#: busy-drop. Sized to cover one whisper.cpp decode.
_LATE_INTERRUPT_RESULT_GRACE_S = 6.0


@dataclass(frozen=True, slots=True)
class DroppedTurn:
    """Telemetry for an STT result dropped because a response was already in
    flight. It never became a `ConversationSession.send()` call; history is
    untouched."""

    reason: str
    at: float  # time.monotonic()
    at_wall: str  # ISO-8601 UTC
    transcript: str
    input_speech_language: str | None
    conversation_queue_depth: int
    dropped_count_this_session: int


@dataclass(frozen=True, slots=True)
class CoalescedInterruptTurn:
    """M2.5B.1 — one confirmed interruption, assembled from ALL its VAD
    segments into a single canonical user turn before any conversation
    dispatch. ``_run_turn_inner`` reads it exactly like a
    ``TranscriptionResult``."""

    text: str
    input_language: str | None
    language_decision: LanguageDecision | None
    segment_count: int


@dataclass(frozen=True, slots=True)
class InterruptedTurn:
    """Telemetry for a reply cut short by a confirmed barge-in (M2.5B)."""

    reason: str
    at: float  # time.monotonic()
    at_wall: str  # ISO-8601 UTC
    invalidated_response_id: int | None
    outcome: str  # InterruptedTurnOutcome value
    spoken_chars: int
    #: ``cancel_token.cancel()`` was called (NOT "the worker stopped" — see
    #: ``CancelCompletion`` for that; the old name conflated the two).
    llm_cancel_requested: bool
    #: worker-stop state *at commit time* — usually not yet final; the
    #: authoritative measurement arrives later via ``on_cancel_completed``.
    provider_worker_stopped_at_commit: bool
    interrupted_count_this_session: int
    # back-compat alias (kept so nothing downstream breaks)
    llm_cancel_completed: bool = False


@dataclass(frozen=True, slots=True)
class CancelCompletion:
    """M2.5B.1 — measured after ``interrupt_active_turn``: how long the
    provider worker took to actually stop once cancelled. On a 4-core Pi an
    old worker overlapping the new whisper.cpp decode multiplies STT
    latency, so this is the number that matters for a cancellation
    barrier/priority policy (R0029)."""

    invalidated_response_id: int | None
    cancel_requested_at: float  # time.monotonic()
    cancel_observed: bool       # the worker saw is_cancelled
    worker_stopped: bool        # the worker thread exited within the watch window
    cancel_to_worker_stop_ms: float | None


@dataclass(frozen=True, slots=True)
class TurnLanguage:
    """Per-turn language summary handed to ``on_turn_language`` — for
    terminal debug + downstream TTS voice selection. Keeps the five
    concepts distinct."""

    transcript: str
    input_speech_language: str | None  # what the user spoke (STT-decoded)
    response_language: str  # what NeXa will reply in
    response_reason: str
    preference_changed: bool
    sticky_preference: str | None
    language_decision: LanguageDecision | None  # full STT/guard telemetry


class VoiceConversationAdapter:
    """Bridges one M2.2 ``TranscriptionResult`` stream into one, existing
    ``ConversationSession``. Conversation turns are serialized FIFO
    (``SerialConversationQueue``): audio/VAD/STT keep running independently
    while a turn is in flight, but only one ``ConversationSession.send()``
    call ever executes at a time.
    """

    def __init__(
        self,
        session: ConversationSession,
        *,
        on_user_transcript: Callable[[str], None] | None = None,
        on_assistant_token: Callable[[str], None] | None = None,
        on_assistant_complete: Callable[[str], None] | None = None,
        on_conversation_error: Callable[[Exception], None] | None = None,
        on_turn_language: Callable[[TurnLanguage], None] | None = None,
        on_turn_dropped: Callable[[DroppedTurn], None] | None = None,
        on_turn_interrupted: Callable[[InterruptedTurn], None] | None = None,
        on_cancel_completed: Callable[[CancelCompletion], None] | None = None,
        response_language_resolver: ResponseLanguageResolver | None = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        response_mode: ResponseMode = ResponseMode.VOICE,
        response_id_source: Callable[[], int | None] | None = None,
        spoken_prefix_source: Callable[[int | None], str] | None = None,
        interruption_complete_hook: Callable[[], None] | None = None,
        interrupt_capture_timeout_s: float = 15.0,
        cancel_watch_timeout_s: float = 5.0,
        context_provider: Callable[[ConversationSession], str | None] | None = None,
    ) -> None:
        # M2.4B.3.3: this adapter *is* the voice surface, so it defaults to
        # ``ResponseMode.VOICE`` — a transient per-request hint the same
        # canonical ``ConversationSession`` receives. It never changes
        # history/persona/model/language authority. Pass ``ResponseMode.TEXT``
        # to run the voice path with the plain typed-chat policy (A/B).
        self._session = session
        self._response_mode = response_mode
        self._on_user_transcript = on_user_transcript
        self._on_assistant_token = on_assistant_token
        self._on_assistant_complete = on_assistant_complete
        self._on_conversation_error = on_conversation_error
        self._on_turn_language = on_turn_language
        self._on_turn_dropped = on_turn_dropped
        self._on_turn_interrupted = on_turn_interrupted
        self._on_cancel_completed = on_cancel_completed
        # M2.4B.5: optional. Without it, the response language stays the
        # pre-B.5 R0009 per-turn text detection (byte-for-byte).
        self._resolver = response_language_resolver
        # M2.5B — barge-in. Both None (the default) = R0026 behaviour,
        # byte-for-byte: no per-turn CancelToken is created, ``interrupt_
        # active_turn`` is inert. Wired only when the pipeline is built with
        # ``bargein_enabled=True`` (BargeInController present).
        self._response_id_source = response_id_source
        self._spoken_prefix_source = spoken_prefix_source
        self._interruption_complete_hook = interruption_complete_hook
        self._interrupt_capture_timeout_s = interrupt_capture_timeout_s
        self._cancel_watch_timeout_s = cancel_watch_timeout_s
        # R0079: optional Context Engine hook, threaded straight through to
        # ConversationSession.send() -- default None is byte-for-byte the
        # pre-R0079 path (no Context Engine participation in local voice).
        self._context_provider = context_provider
        self._active_response_id: int | None = None
        self._active_cancel_token: CancelToken | None = None
        self._consume_task: asyncio.Task | None = None
        self._interrupt_requested = False
        self._llm_cancel_completed = False
        self._cancel_requested_at: float | None = None
        self._cancel_watch_task: asyncio.Task | None = None
        self._interrupted_count = 0
        # M2.5B.1 — interruption-utterance capture / coalesce phase. While
        # ``_capturing_interrupt`` every STT result belongs to the ONE
        # confirmed interruption: it is accumulated, never dropped, never
        # dispatched piecemeal. One canonical turn is submitted when the
        # capture has settled AND every owed segment result has arrived.
        self._capturing_interrupt = False
        self._interrupt_segments: list[str] = []
        self._interrupt_pending_results = 0   # segments ended, result not yet in
        # M2.5B.3 — diagnostic only; no longer gates finalisation. VAD
        # segment start/end frames are not reliably paired across a
        # ``broadcast_interruption()`` frame-queue flush on the Pi, so a
        # non-zero "open" count must never be able to hang the capture.
        self._interrupt_open_segments = 0
        self._interrupt_capture_settled = False
        #: M2.5B.3 — monotonic when the last interruption capture finalised;
        #: a late STT result for one of its segments arriving just after is
        #: an expected artefact (logged at INFO), not a busy-drop.
        self._last_capture_finalized_at: float | None = None
        self._late_interrupt_results = 0
        self._interrupt_last_decision: LanguageDecision | None = None
        self._interrupt_last_language: str | None = None
        self._interrupt_reason: str = "sustained_vad"
        self._interrupt_capture_timeout_task: asyncio.Task | None = None
        self._coalesced_interrupt_turns = 0
        self._queue = SerialConversationQueue(self._run_turn, max_queue_size=max_queue_size)
        # M2.4B.5A: strict pre-M2.5 half-duplex — True from the instant a
        # turn starts being handled until its assistant reply's generation
        # is complete. While True, an incoming STT result is DROPPED (with
        # telemetry), never enqueued — no conversation-queue backlog can
        # form from busy-period audio (R0026). TTS playback after generation
        # is still covered by the HalfDuplexGate upstream.
        self._turn_in_flight = False
        self._dropped_busy = 0

    @property
    def max_observed_conversation_concurrency(self) -> int:
        """Peak number of simultaneous ``ConversationSession`` turns ever
        observed this session. Must never exceed 1."""
        return self._queue.max_observed_concurrency

    @property
    def conversation_queue_depth(self) -> int:
        return self._queue.queue_size

    @property
    def conversation_in_flight(self) -> int:
        """1 while a ``ConversationSession.send()`` turn is executing (M2.5B.1
        — ``conversation_queue_depth`` alone hides in-flight work)."""
        return self._queue.in_flight

    @property
    def capturing_interrupt(self) -> bool:
        """M2.5B.1 — True while a confirmed interruption's VAD segments are
        still being collected into one canonical turn."""
        return self._capturing_interrupt

    @property
    def coalesced_interrupt_turns(self) -> int:
        return self._coalesced_interrupt_turns

    @property
    def turn_in_flight(self) -> bool:
        return self._turn_in_flight

    @property
    def dropped_busy_turns(self) -> int:
        """STT results dropped this session because a turn was already in
        flight (R0026 / strict pre-M2.5 half-duplex)."""
        return self._dropped_busy

    @property
    def interrupted_turns(self) -> int:
        """Replies cut short by a confirmed barge-in this session (M2.5B)."""
        return self._interrupted_count

    @property
    def active_response_id(self) -> int | None:
        return self._active_response_id

    def interrupt_active_turn(self) -> bool:
        """M2.5B — the ``BargeInController.on_confirmed`` hook calls this
        synchronously the instant an interruption is confirmed. It:

        1. flips ``_interrupt_requested`` so the streaming loop stops
           consuming and takes the interruption-history path;
        2. calls ``CancelToken.cancel()`` — NOT merely closing the async
           generator — so the Ollama worker thread observes it, ``return``s
           and drops the HTTP stream (R0028: closing the generator alone
           leaves the worker draining into a queue nobody reads);
        3. cancels the consume task so a parked ``await`` unblocks
           immediately and ``send()`` never records its own assistant turn.

        Returns True iff there was an active turn to interrupt. Fast +
        non-blocking by contract; the actual teardown/commit runs in
        ``_run_turn_inner``."""
        if self._active_cancel_token is None or self._interrupt_requested:
            return False
        self._interrupt_requested = True
        # Capture the delivered-text high-water mark NOW, on the event loop,
        # the instant the interruption confirms — before any late
        # ``TTSTextFrame`` or the next turn's ``start_response`` can touch
        # the tracker. The committed interrupted-turn history uses this
        # captured value, not a later re-read.
        if self._spoken_prefix_source is not None:
            try:
                self._captured_interrupt_prefix = (
                    self._spoken_prefix_source(self._active_response_id) or ""
                )
            except Exception:
                self._captured_interrupt_prefix = ""
        tok = self._active_cancel_token
        self._cancel_requested_at = time.monotonic()
        tok.cancel()
        if self._consume_task is not None and not self._consume_task.done():
            self._consume_task.cancel()
        # M2.5B.1 — measure how long the provider worker takes to actually
        # stop (off the loop; a thread-Event wait). Fires on_cancel_completed.
        try:
            self._cancel_watch_task = asyncio.ensure_future(
                self._watch_cancel_completion(tok, self._active_response_id)
            )
        except RuntimeError:
            self._cancel_watch_task = None
        # M2.5B.1 — enter the interruption-capture phase. Segment-1's start
        # already happened (it is what confirmed); its end + STT result are
        # still owed, so pre-count one pending result + one open segment.
        self._begin_interrupt_capture(open_segments=1, pending_results=0)
        return True

    async def _watch_cancel_completion(self, tok: CancelToken, rid: int | None) -> None:
        loop = asyncio.get_running_loop()
        t0 = self._cancel_requested_at or time.monotonic()
        stopped = await loop.run_in_executor(
            None, tok.wait_worker_stopped, self._cancel_watch_timeout_s
        )
        ms = round((time.monotonic() - t0) * 1000, 1) if stopped else None
        self._llm_cancel_completed = stopped
        rec = CancelCompletion(
            invalidated_response_id=rid,
            cancel_requested_at=t0,
            cancel_observed=tok.cancel_observed,
            worker_stopped=stopped,
            cancel_to_worker_stop_ms=ms,
        )
        logger.info(
            f"nexa.voice_conversation: provider worker for response_id={rid} "
            f"cancel_observed={tok.cancel_observed} stopped={stopped} "
            f"cancel_to_worker_stop_ms={ms}"
        )
        if self._on_cancel_completed is not None:
            self._on_cancel_completed(rec)

    # -- M2.5B.1 interruption capture / coalesce -------------------------- #
    def _begin_interrupt_capture(self, *, open_segments: int, pending_results: int) -> None:
        self._capturing_interrupt = True
        self._interrupt_segments = []
        self._interrupt_pending_results = pending_results
        self._interrupt_open_segments = open_segments
        self._interrupt_capture_settled = False
        self._late_interrupt_results = 0
        self._interrupt_last_decision = None
        self._interrupt_last_language = None
        self._cancel_capture_timeout()
        try:
            self._interrupt_capture_timeout_task = asyncio.ensure_future(
                self._capture_timeout_guard()
            )
        except RuntimeError:
            self._interrupt_capture_timeout_task = None

    async def _capture_timeout_guard(self) -> None:
        try:
            await asyncio.sleep(self._interrupt_capture_timeout_s)
        except asyncio.CancelledError:
            return
        if self._capturing_interrupt:
            logger.warning(
                "nexa.voice_conversation: interruption capture timed out after "
                f"{self._interrupt_capture_timeout_s}s — finalising with what we have "
                "(this should not happen in ordinary use; see R0029 §M2.5B.3). "
                f"open(diag)={self._interrupt_open_segments}, "
                f"pending={self._interrupt_pending_results}"
            )
            self._interrupt_capture_settled = True
            # any still-owed / unmatched result may arrive after this
            self._late_interrupt_results += max(
                self._interrupt_open_segments, self._interrupt_pending_results
            )
            self._interrupt_open_segments = 0
            self._interrupt_pending_results = 0
            self._finalize_interrupt_turn()

    def _cancel_capture_timeout(self) -> None:
        t = self._interrupt_capture_timeout_task
        if t is not None and not t.done():
            t.cancel()
        self._interrupt_capture_timeout_task = None

    def note_interrupt_segment_started(self) -> None:
        """Called by ``BargeInController`` — a later VAD segment of the same
        interruption utterance began."""
        if self._capturing_interrupt:
            self._interrupt_open_segments += 1

    def note_interrupt_segment_ended(self) -> None:
        """Called by ``BargeInController`` — one interruption segment ended;
        an STT result is now owed for it."""
        if self._capturing_interrupt:
            if self._interrupt_open_segments > 0:
                self._interrupt_open_segments -= 1
            self._interrupt_pending_results += 1

    def abandon_interrupt_capture(self) -> None:
        """M2.5B.3 v2 — called by ``BargeInController`` when it force-ends the
        capture phase (a reply was dispatched while still INTERRUPTING, or the
        pipeline stopped). Finalise now with whatever was captured (so the
        operator's interruption text is not silently lost and the 15 s
        adapter timeout is cancelled) — the coalesced turn, if any, just
        queues behind the reply that ended the phase."""
        if not self._capturing_interrupt:
            return
        logger.info(
            "nexa.voice_conversation: interruption capture ABANDONED by the "
            f"controller ({len(self._interrupt_segments)} segment(s) captured, "
            f"pending={self._interrupt_pending_results}) — finalising now"
        )
        self._interrupt_capture_settled = True
        self._late_interrupt_results += max(
            self._interrupt_open_segments, self._interrupt_pending_results
        )
        self._interrupt_open_segments = 0
        self._interrupt_pending_results = 0
        self._finalize_interrupt_turn()

    def note_interrupt_capture_settled(self) -> None:
        """Called by ``BargeInController`` — ``settle_secs`` elapsed with no
        VAD activity of any kind. The interruption utterance is definitively
        over: any lingering "open segment" count is frame-pairing drift
        (M2.5B.3), so reconcile it to zero. Finalise once every *owed STT
        result* is also in."""
        if self._capturing_interrupt:
            self._interrupt_capture_settled = True
            if self._interrupt_open_segments:
                logger.info(
                    "nexa.voice_conversation: capture settled with "
                    f"{self._interrupt_open_segments} unmatched segment-start(s) — "
                    "reconciling to 0 (VAD silent for the settle window); "
                    "their STT results, if any, will arrive after finalise"
                )
                # each unmatched start may still yield one late STT result
                self._late_interrupt_results += self._interrupt_open_segments
                self._interrupt_open_segments = 0
            self._maybe_finalize_interrupt()

    def _accumulate_interrupt_segment(self, result: object) -> None:
        if isinstance(result, TranscriptionResult):
            seg = result.text.strip()
            self._interrupt_last_language = result.language.value
            self._interrupt_last_decision = result.language_decision
        else:
            seg = str(result).strip()
            self._interrupt_last_language = None
        if seg:
            self._interrupt_segments.append(seg)
        if self._interrupt_pending_results > 0:
            self._interrupt_pending_results -= 1
        logger.info(
            f"nexa.voice_conversation: interruption segment captured "
            f"({seg[:40]!r}); segments={len(self._interrupt_segments)}, "
            f"open={self._interrupt_open_segments}, "
            f"pending_results={self._interrupt_pending_results}, "
            f"settled={self._interrupt_capture_settled}"
        )
        self._maybe_finalize_interrupt()

    def _maybe_finalize_interrupt(self) -> None:
        if not self._capturing_interrupt:
            return
        if not self._interrupt_capture_settled:
            return
        # M2.5B.3 — gate on the settle + owed STT results ONLY. The "open
        # segments" count is diagnostic; a lost VAD segment-END must never
        # keep this from finalising.
        if self._interrupt_pending_results > 0:
            return
        self._finalize_interrupt_turn()

    def _finalize_interrupt_turn(self) -> None:
        if not self._capturing_interrupt:
            return
        self._capturing_interrupt = False
        self._last_capture_finalized_at = time.monotonic()
        self._cancel_capture_timeout()
        text = " ".join(s for s in self._interrupt_segments if s).strip()
        n = len(self._interrupt_segments)
        logger.info(
            f"nexa.voice_conversation: interruption capture ready — {n} segment(s), "
            f"settled={self._interrupt_capture_settled}, "
            f"open(diag)={self._interrupt_open_segments}, "
            f"pending={self._interrupt_pending_results}"
        )
        if not text:
            logger.info(
                "nexa.voice_conversation: interruption produced no transcript — "
                "reply already cancelled, no replacement turn"
            )
            if self._interruption_complete_hook is not None:
                self._interruption_complete_hook()
            return
        coalesced = CoalescedInterruptTurn(
            text=text,
            input_language=self._interrupt_last_language,
            language_decision=self._interrupt_last_decision,
            segment_count=n,
        )
        self._coalesced_interrupt_turns += 1
        logger.info(
            f"nexa.voice_conversation: ONE interruption turn from {n} segment(s): "
            f"{text[:80]!r}"
        )
        try:
            self._queue.submit(coalesced)
        except ConversationQueueOverflowError as exc:
            logger.error(f"nexa.voice_conversation: {exc}")
            if self._on_conversation_error is not None:
                self._on_conversation_error(exc)
        if self._interruption_complete_hook is not None:
            self._interruption_complete_hook()

    def start(self, task_factory: Callable[[Coroutine], object] | None = None) -> None:
        self._queue.start(task_factory)

    async def shutdown(self) -> None:
        await self._queue.shutdown()

    def handle_transcription(self, result: TranscriptionResult) -> None:
        """Pass directly as ``VoiceRuntime(on_transcription=...)``.

        An empty/whitespace-only transcript never becomes a conversation
        turn — explicit, not a silently-ignored edge case.
        """
        # M2.5B.1 — while a confirmed interruption is being captured, EVERY
        # STT result belongs to that ONE interruption utterance: accumulate
        # it (coalesced into a single turn later), never drop, never dispatch
        # piecemeal. This check MUST come before the DROP_BUSY / turn-in-
        # flight checks below — otherwise a second interruption segment that
        # arrives while the interrupted reply is still tearing down would be
        # wrongly DROP_BUSY'd (the live-acceptance fragmentation bug).
        if self._capturing_interrupt:
            self._accumulate_interrupt_segment(result)
            return

        if isinstance(result, TranscriptionResult):
            text = result.text.strip()
            input_language = result.language.value
        else:
            text = str(result).strip()
            input_language = None
        if not text:
            logger.debug(
                "nexa.voice_conversation: empty/whitespace transcript — no conversation turn"
            )
            return

        # M2.5B.3 — an STT result for a segment of the interruption we JUST
        # finalised whose VAD segment-END frame arrived late / was flushed by
        # ``broadcast_interruption()`` (so it was never a matched, owed
        # result). We only expect these when a segment-start went unmatched
        # (``_late_interrupt_results`` > 0) AND within one decode of finalise.
        # Discard quietly — this is NOT a busy-drop.
        if (
            self._late_interrupt_results > 0
            and self._last_capture_finalized_at is not None
            and (time.monotonic() - self._last_capture_finalized_at)
            < _LATE_INTERRUPT_RESULT_GRACE_S
        ):
            self._late_interrupt_results -= 1
            logger.info(
                "nexa.voice_conversation: late interruption-segment STT result "
                f"({text[:40]!r}) discarded — capture already finalised "
                f"{round(time.monotonic() - self._last_capture_finalized_at, 1)}s ago "
                f"({self._late_interrupt_results} more expected)"
            )
            return
        # M2.4B.5A: strict pre-M2.5 half-duplex — if a turn is already in
        # flight, DROP this result explicitly (telemetry) rather than
        # enqueue it. This is the belt for an STT job that was already
        # running when the previous turn dispatched; the upstream mic gate
        # normally stops such audio ever being captured. No barge-in.
        if self._turn_in_flight:
            self._drop_busy(text, input_language)
            return
        try:
            self._queue.submit(result)
        except ConversationQueueOverflowError as exc:
            logger.error(f"nexa.voice_conversation: {exc}")
            if self._on_conversation_error is not None:
                self._on_conversation_error(exc)

    def _drop_busy(self, text: str, input_language: str | None) -> None:
        self._dropped_busy += 1
        record = DroppedTurn(
            reason=DROP_BUSY_RESPONSE_IN_FLIGHT,
            at=time.monotonic(),
            at_wall=datetime.now(UTC).isoformat(timespec="milliseconds"),
            transcript=text,
            input_speech_language=input_language,
            conversation_queue_depth=self._queue.queue_size,
            dropped_count_this_session=self._dropped_busy,
        )
        logger.info(
            f"nexa.voice_conversation: {DROP_BUSY_RESPONSE_IN_FLIGHT} — dropped an STT "
            f"result ({text[:40]!r}) that arrived while a turn is in flight "
            f"(session total {self._dropped_busy}); conversation_queue_depth="
            f"{record.conversation_queue_depth}"
        )
        if self._on_turn_dropped is not None:
            self._on_turn_dropped(record)

    def handle_transcription_error(self, exc: Exception) -> None:
        """Pass directly as ``VoiceRuntime(on_transcription_error=...)`` —
        an STT failure must never produce a fabricated conversation turn."""
        logger.error(f"nexa.voice_conversation: STT failed, no conversation turn: {exc}")

    async def _run_turn(self, item: object) -> None:
        # M2.4B.5A: from here until this reply's generation completes, any
        # further STT result is dropped by handle_transcription (strict
        # pre-M2.5 half-duplex). Cleared in the finally below.
        self._turn_in_flight = True
        try:
            await self._run_turn_inner(item)
        finally:
            self._turn_in_flight = False

    async def _run_turn_inner(self, item: object) -> None:
        if isinstance(item, TranscriptionResult):
            text = item.text.strip()
            input_language = item.language.value
            decision_telemetry = item.language_decision
        elif isinstance(item, CoalescedInterruptTurn):
            # M2.5B.1 — one canonical interruption turn, already assembled
            # from all its VAD segments. Treated exactly like a normal turn
            # from here (STT / resolver / ConversationSession).
            text = item.text.strip()
            input_language = item.input_language
            decision_telemetry = item.language_decision
        else:  # a bare transcript string (M2.3-style callers / tests)
            text = str(item).strip()
            input_language = None
            decision_telemetry = None

        response_language: str | None = None
        turn_lang: TurnLanguage | None = None
        if self._resolver is not None:
            decision = self._resolver.resolve(
                text, input_language=input_language or "en"
            )
            response_language = decision.response_language
            turn_lang = TurnLanguage(
                transcript=text,
                input_speech_language=input_language,
                response_language=response_language,
                response_reason=decision.reason,
                preference_changed=decision.preference_changed,
                sticky_preference=decision.sticky_after,
                language_decision=decision_telemetry,
            )
        elif decision_telemetry is not None:
            turn_lang = TurnLanguage(
                transcript=text,
                input_speech_language=input_language,
                response_language=input_language or "en",
                response_reason="mirror input speech language (no resolver)",
                preference_changed=False,
                sticky_preference=None,
                language_decision=decision_telemetry,
            )

        if turn_lang is not None and self._on_turn_language is not None:
            self._on_turn_language(turn_lang)
        if self._on_user_transcript is not None:
            # This drives AssistantSpeechBridge.on_user_transcript ->
            # gate.notify_response_dispatched() + (M2.5B) bargein.notify_
            # response_dispatched(), which allocates this turn's response_id.
            self._on_user_transcript(text)

        # M2.5B — per-turn cancellation identity. None sources = R0026: no
        # token, interrupt_active_turn inert, path below is byte-for-byte the
        # pre-M2.5B path.
        self._interrupt_requested = False
        self._llm_cancel_completed = False
        self._captured_interrupt_prefix: str | None = None
        self._active_response_id = (
            self._response_id_source() if self._response_id_source is not None else None
        )
        cancel_token: CancelToken | None = None
        if self._response_id_source is not None:
            cancel_token = CancelToken()
        self._active_cancel_token = cancel_token

        chunks: list[str] = []
        gen = self._session.send(
            text,
            response_mode=self._response_mode,
            response_language=response_language,
            cancel_token=cancel_token,
            context_provider=self._context_provider,
        )

        async def _consume() -> None:
            async for chunk in gen:
                chunks.append(chunk)
                if self._on_assistant_token is not None:
                    self._on_assistant_token(chunk)

        normal_complete = False
        try:
            self._consume_task = asyncio.ensure_future(_consume())
            try:
                await self._consume_task
                normal_complete = True  # send() recorded its own assistant turn
            except asyncio.CancelledError:
                normal_complete = False  # interrupted — see below
        except Exception as exc:  # ConversationSession/provider failures reach the caller
            logger.error(f"nexa.voice_conversation: conversation turn failed: {exc}")
            self._active_cancel_token = None
            self._consume_task = None
            if self._on_conversation_error is not None:
                self._on_conversation_error(exc)
            return
        finally:
            self._consume_task = None

        if self._interrupt_requested and not normal_complete:
            # GeneratorExit at send()'s yield -> it does NOT append its own
            # assistant turn; we own the interrupted-history commit instead.
            try:
                await gen.aclose()
            except Exception:
                logger.debug("nexa.voice_conversation: aclose after interrupt raised")
            self._active_cancel_token = None
            self._commit_interrupted(chunks, cancel_token)
            return

        self._active_cancel_token = None
        if self._on_assistant_complete is not None:
            self._on_assistant_complete("".join(chunks))

    def _commit_interrupted(
        self, chunks: list[str], cancel_token: CancelToken | None = None
    ) -> None:
        rid = self._active_response_id
        # Prefer the value captured on the loop at confirm time
        # (interrupt_active_turn); only re-read if it was never captured
        # (e.g. interrupt requested without a spoken_prefix_source).
        prefix = self._captured_interrupt_prefix
        if prefix is None and self._spoken_prefix_source is not None:
            prefix = self._spoken_prefix_source(rid) or ""
        if not prefix:
            # fall back to the raw delivered tokens if no synthesized-sentence
            # high-water mark is wired (deterministic-test / degraded path)
            prefix = "".join(chunks)
        outcome = self._session.commit_interrupted_turn(prefix)
        self._interrupted_count += 1
        worker_stopped_now = cancel_token is not None and cancel_token.worker_stopped
        record = InterruptedTurn(
            reason="sustained_vad",
            at=time.monotonic(),
            at_wall=datetime.now(UTC).isoformat(timespec="milliseconds"),
            invalidated_response_id=rid,
            outcome=outcome.value,
            spoken_chars=len(prefix.strip()) if outcome
            == InterruptedTurnOutcome.COMMITTED_SPOKEN_PREFIX else 0,
            llm_cancel_requested=(cancel_token is not None and cancel_token.is_cancelled),
            provider_worker_stopped_at_commit=worker_stopped_now,
            interrupted_count_this_session=self._interrupted_count,
            llm_cancel_completed=worker_stopped_now,  # back-compat alias
        )
        logger.info(
            f"nexa.voice_conversation: turn interrupted — response_id={rid}, "
            f"outcome={outcome.value}, spoken_chars={record.spoken_chars}, "
            f"llm_cancel_requested={record.llm_cancel_requested}, "
            f"worker_stopped_at_commit={worker_stopped_now}"
        )
        if self._on_turn_interrupted is not None:
            self._on_turn_interrupted(record)
        if outcome == InterruptedTurnOutcome.NOTHING_TO_COMMIT:
            # the reply had actually finished as the interrupt landed
            if self._on_assistant_complete is not None:
                self._on_assistant_complete("".join(chunks))
