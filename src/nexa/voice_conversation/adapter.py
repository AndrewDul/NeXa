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
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine

from loguru import logger

from nexa.conversation.response_mode import ResponseMode
from nexa.conversation.session import ConversationSession
from nexa.stt.transcriber import TranscriptionResult

from .queue import DEFAULT_MAX_QUEUE_SIZE, ConversationQueueOverflowError, SerialConversationQueue


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
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        response_mode: ResponseMode = ResponseMode.VOICE,
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
        self._queue = SerialConversationQueue(self._run_turn, max_queue_size=max_queue_size)

    @property
    def max_observed_conversation_concurrency(self) -> int:
        """Peak number of simultaneous ``ConversationSession`` turns ever
        observed this session. Must never exceed 1."""
        return self._queue.max_observed_concurrency

    def start(self, task_factory: Callable[[Coroutine], object] | None = None) -> None:
        self._queue.start(task_factory)

    async def shutdown(self) -> None:
        await self._queue.shutdown()

    def handle_transcription(self, result: TranscriptionResult) -> None:
        """Pass directly as ``VoiceRuntime(on_transcription=...)``.

        An empty/whitespace-only transcript never becomes a conversation
        turn — explicit, not a silently-ignored edge case.
        """
        text = result.text.strip()
        if not text:
            logger.debug(
                "nexa.voice_conversation: empty/whitespace transcript — no conversation turn"
            )
            return
        try:
            self._queue.submit(text)
        except ConversationQueueOverflowError as exc:
            logger.error(f"nexa.voice_conversation: {exc}")
            if self._on_conversation_error is not None:
                self._on_conversation_error(exc)

    def handle_transcription_error(self, exc: Exception) -> None:
        """Pass directly as ``VoiceRuntime(on_transcription_error=...)`` —
        an STT failure must never produce a fabricated conversation turn."""
        logger.error(f"nexa.voice_conversation: STT failed, no conversation turn: {exc}")

    async def _run_turn(self, text: str) -> None:
        if self._on_user_transcript is not None:
            self._on_user_transcript(text)
        chunks: list[str] = []
        try:
            async for chunk in self._session.send(text, response_mode=self._response_mode):
                chunks.append(chunk)
                if self._on_assistant_token is not None:
                    self._on_assistant_token(chunk)
        except Exception as exc:  # ConversationSession/provider failures must reach the caller
            logger.error(f"nexa.voice_conversation: conversation turn failed: {exc}")
            if self._on_conversation_error is not None:
                self._on_conversation_error(exc)
            return
        if self._on_assistant_complete is not None:
            self._on_assistant_complete("".join(chunks))
