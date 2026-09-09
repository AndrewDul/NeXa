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
class InterruptedTurn:
    """Telemetry for a reply cut short by a confirmed barge-in (M2.5B)."""

    reason: str
    at: float  # time.monotonic()
    at_wall: str  # ISO-8601 UTC
    invalidated_response_id: int | None
    outcome: str  # InterruptedTurnOutcome value
    spoken_chars: int
    llm_cancel_completed: bool
    interrupted_count_this_session: int


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
        response_language_resolver: ResponseLanguageResolver | None = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        response_mode: ResponseMode = ResponseMode.VOICE,
        response_id_source: Callable[[], int | None] | None = None,
        spoken_prefix_source: Callable[[int | None], str] | None = None,
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
        # M2.4B.5: optional. Without it, the response language stays the
        # pre-B.5 R0009 per-turn text detection (byte-for-byte).
        self._resolver = response_language_resolver
        # M2.5B — barge-in. Both None (the default) = R0026 behaviour,
        # byte-for-byte: no per-turn CancelToken is created, ``interrupt_
        # active_turn`` is inert. Wired only when the pipeline is built with
        # ``bargein_enabled=True`` (BargeInController present).
        self._response_id_source = response_id_source
        self._spoken_prefix_source = spoken_prefix_source
        self._active_response_id: int | None = None
        self._active_cancel_token: CancelToken | None = None
        self._consume_task: asyncio.Task | None = None
        self._interrupt_requested = False
        self._llm_cancel_completed = False
        self._interrupted_count = 0
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
        self._active_cancel_token.cancel()
        if self._consume_task is not None and not self._consume_task.done():
            self._consume_task.cancel()
        return True

    def start(self, task_factory: Callable[[Coroutine], object] | None = None) -> None:
        self._queue.start(task_factory)

    async def shutdown(self) -> None:
        await self._queue.shutdown()

    def handle_transcription(self, result: TranscriptionResult) -> None:
        """Pass directly as ``VoiceRuntime(on_transcription=...)``.

        An empty/whitespace-only transcript never becomes a conversation
        turn — explicit, not a silently-ignored edge case.
        """
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
            self._llm_cancel_completed = (
                cancel_token is not None and cancel_token.is_cancelled
            )
            self._active_cancel_token = None
            self._commit_interrupted(chunks)
            return

        self._active_cancel_token = None
        if self._on_assistant_complete is not None:
            self._on_assistant_complete("".join(chunks))

    def _commit_interrupted(self, chunks: list[str]) -> None:
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
        record = InterruptedTurn(
            reason="sustained_vad",
            at=time.monotonic(),
            at_wall=datetime.now(UTC).isoformat(timespec="milliseconds"),
            invalidated_response_id=rid,
            outcome=outcome.value,
            spoken_chars=len(prefix.strip()) if outcome
            == InterruptedTurnOutcome.COMMITTED_SPOKEN_PREFIX else 0,
            llm_cancel_completed=self._llm_cancel_completed,
            interrupted_count_this_session=self._interrupted_count,
        )
        logger.info(
            f"nexa.voice_conversation: turn interrupted — response_id={rid}, "
            f"outcome={outcome.value}, spoken_chars={record.spoken_chars}, "
            f"llm_cancel_completed={self._llm_cancel_completed}"
        )
        if self._on_turn_interrupted is not None:
            self._on_turn_interrupted(record)
        if outcome == InterruptedTurnOutcome.NOTHING_TO_COMMIT:
            # the reply had actually finished as the interrupt landed
            if self._on_assistant_complete is not None:
                self._on_assistant_complete("".join(chunks))
