"""``TurnTimingTracker`` — correlates per-turn latency timestamps across the
fully async STT -> ConversationSession -> TTS pipeline (M2.4).

Real hardware testing found a genuine instrumentation bug (not a product
bug): a single shared mutable timing dict in the probe app was overwritten
by a *later* turn's `on_user_transcript` before an *earlier* turn's TTS had
finished speaking and reported its own timings — because
`SerialConversationQueue` only serializes LLM *generation*, not how long
the resulting audio takes to *play*. A later turn's conversation processing
can genuinely start while an earlier turn's TTS audio is still playing.
This class fixes that by giving each turn its own record, correlated by
FIFO position rather than a single shared "current turn" dict.

Conversation-side events (`start_turn`, `first_token`, `assistant_complete`)
are guaranteed single-turn-at-a-time by `SerialConversationQueue`, so they
always apply to whichever turn is currently "active" for conversation
purposes. TTS-side events (`tts_started`, `tts_first_audio`, `tts_stopped`)
apply to the *oldest* turn whose TTS lifecycle has not yet completed —
Pipecat's `TTSService`/`AssistantSpeechBridge` process turns' audio in
strict submission order (FIFO), the same guarantee `SerialConversationQueue`
already gives for LLM calls.

Pure Python, no Pipecat/asyncio dependency — directly unit testable.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class TurnTiming:
    """One turn's latency record. Every field is `None` until that event
    actually happens — never guessed, never backfilled.

    Field names map to the streaming-metric contract as:

    ================================  ===========================
    contract name                     field
    ================================  ===========================
    ``assistant_first_token_at``      ``first_token``
    ``assistant_complete_at``         ``assistant_complete``
    ``first_sentence_ready_at``       ``tts_started``
    ``first_tts_audio_at``            ``tts_first_audio``
    ``tts_complete_at``               ``tts_stopped``
    ================================  ===========================

    ``tts_started`` and ``tts_first_audio`` are each written **once per
    assistant response** (see ``TurnTimingTracker``): a multi-sentence reply
    fires Pipecat's TTS lifecycle callbacks again for each later sentence,
    and those later firings must never overwrite the first sentence's
    timestamps.
    """

    end_of_turn: float | None = None
    stt_result: float | None = None
    first_token: float | None = None
    assistant_complete: float | None = None
    tts_started: float | None = None
    tts_first_audio: float | None = None
    tts_stopped: float | None = None
    assistant_text_chars: int | None = None
    """Length of the assistant reply, in characters (M2.4B.1). A real count
    of the streamed text — never a token estimate (Ollama's token count is
    in the ``done`` chunk the provider discards; see
    ``nexa.voice_tts.metrics`` / R0013)."""

    @property
    def streaming_overlap_seconds(self) -> float | None:
        """Positive means TTS audio started before the assistant finished
        generating — the streaming-overlap proof. `None` if either
        timestamp is missing (nothing to compare yet)."""
        if self.assistant_complete is None or self.tts_first_audio is None:
            return None
        return self.assistant_complete - self.tts_first_audio

    @property
    def streaming_overlap_confirmed(self) -> bool:
        """The streaming-TTS PASS condition, stated exactly:
        ``first_tts_audio_at < assistant_complete_at``. `False` (never
        `None`) until both timestamps exist, so a caller can treat it as a
        plain pass/fail flag."""
        if self.assistant_complete is None or self.tts_first_audio is None:
            return False
        return self.tts_first_audio < self.assistant_complete


class TurnTimingTracker:
    """FIFO-correlated per-turn timing tracker. See module docstring for
    why a single shared dict is wrong here."""

    def __init__(self) -> None:
        self._queue: deque[TurnTiming] = deque()
        self._active: TurnTiming | None = None

    @property
    def pending_tts_count(self) -> int:
        """How many turns are still waiting for/undergoing TTS playback."""
        return len(self._queue)

    def start_turn(
        self, *, end_of_turn: float | None = None, stt_result: float | None = None
    ) -> TurnTiming:
        """Call from `on_user_transcript` — begins a new turn's record."""
        turn = TurnTiming(end_of_turn=end_of_turn, stt_result=stt_result)
        self._queue.append(turn)
        self._active = turn
        return turn

    def first_token(self) -> None:
        """Call from `on_assistant_token`, only the first time per turn."""
        if self._active is not None and self._active.first_token is None:
            self._active.first_token = time.monotonic()

    def assistant_complete(self, text: str | None = None) -> TurnTiming | None:
        """Call from `on_assistant_complete`. Returns the completed turn's
        record (still in the TTS queue — not popped until `tts_stopped`).

        ``text`` (M2.4B.1, optional) records the real character length of the
        reply; passing nothing keeps the exact M2.4 behaviour."""
        turn = self._active
        if turn is not None:
            turn.assistant_complete = time.monotonic()
            if text is not None:
                turn.assistant_text_chars = len(text)
            self._active = None
        return turn

    def tts_started(self) -> None:
        """Call from `on_tts_started` — attributed to the oldest turn still
        awaiting/undergoing TTS (FIFO), never the "currently active"
        conversation turn, which may already be a later one.

        Set only once per turn: this is ``first_sentence_ready_at``. A
        later sentence within the same response (or a duplicate
        ``TTSStartedFrame`` after an idle-timeout stop) must not move it."""
        if self._queue and self._queue[0].tts_started is None:
            self._queue[0].tts_started = time.monotonic()

    def tts_first_audio(self) -> None:
        """Call from `on_tts_first_audio`. Set only once per turn — a
        second sentence's audio within the same response must never
        overwrite the first sentence's timestamp."""
        if self._queue and self._queue[0].tts_first_audio is None:
            self._queue[0].tts_first_audio = time.monotonic()

    def tts_stopped(self) -> TurnTiming | None:
        """Call from `on_tts_stopped` — the oldest turn's TTS lifecycle is
        now complete; pop and return its full record for reporting."""
        if not self._queue:
            return None
        turn = self._queue.popleft()
        turn.tts_stopped = time.monotonic()
        return turn
