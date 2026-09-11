"""``CloudTurnAccumulator`` — correlates ONE logical cloud turn and
guarantees at most ONE canonical commit for it (ADR-0004 Decision A).

A cloud turn is not identified by anything Gemini hands back — NeXa assigns
its own monotonic ``generation`` id and is the sole authority for when a
turn is "done" and safe to commit. Late provider frames arriving after a
turn has already been committed (or superseded by a new turn starting) are
discarded here, never producing a second history entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class CloudTurnState(StrEnum):
    #: user turn in progress, no final transcription yet.
    OPEN = "open"
    #: user turn ended (final transcription received); waiting on the
    #: assistant's reply / turn-complete / interruption / session loss.
    AWAITING_ASSISTANT = "awaiting_assistant"
    #: committed to canonical history — terminal, never re-entered.
    COMMITTED = "committed"
    #: superseded (a new turn started) or lost with nothing worth
    #: committing — terminal, never re-entered.
    ABANDONED = "abandoned"

_TERMINAL = (CloudTurnState.COMMITTED, CloudTurnState.ABANDONED)


@dataclass
class CloudTurn:
    """One logical cloud turn's accumulated state."""

    #: NeXa-local monotonic id — never a Gemini-provided turn id.
    generation: int
    user_text: str | None = None
    user_final: bool = False
    assistant_text: str = ""
    assistant_final: bool = False
    interrupted: bool = False
    state: CloudTurnState = CloudTurnState.OPEN

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL


class CloudTurnAccumulator:
    """Correlates provider events into one ``CloudTurn`` at a time.

    ``start_turn()`` always allocates a fresh, strictly increasing
    ``generation``; if the previous turn was never committed it is marked
    ``ABANDONED`` first (a new turn superseding an unfinished one is not an
    error — a barge-in or a provider hiccup can legitimately leave a turn
    unfinished — but it must never be committed after the fact).
    """

    def __init__(self) -> None:
        self._generation_counter = 0
        self._current: CloudTurn | None = None

    @property
    def current(self) -> CloudTurn | None:
        return self._current

    def start_turn(self) -> CloudTurn:
        if self._current is not None and not self._current.is_terminal:
            logger.info(
                "nexa.realtime: cloud turn generation=%d superseded before "
                "commit — marking ABANDONED",
                self._current.generation,
            )
            self._current.state = CloudTurnState.ABANDONED
        self._generation_counter += 1
        self._current = CloudTurn(generation=self._generation_counter)
        return self._current

    def on_user_transcription(self, text: str, *, final: bool) -> None:
        cur = self._current
        if cur is None or cur.is_terminal:
            return  # late frame after commit/abandonment — never re-opens it
        cur.user_text = text
        cur.user_final = final
        if final:
            cur.state = CloudTurnState.AWAITING_ASSISTANT

    def on_assistant_transcription(self, text: str, *, final: bool) -> None:
        cur = self._current
        if cur is None or cur.is_terminal:
            return
        cur.assistant_text += text
        cur.assistant_final = final

    def on_interruption(self) -> None:
        cur = self._current
        if cur is None or cur.is_terminal:
            return
        cur.interrupted = True

    def set_spoken_prefix(self, spoken_text: str) -> None:
        """Override the assistant text with the actually-spoken prefix
        (from the playback layer's high-water mark) — HYBRID audio wiring
        supplies this in M2.6B.3; without it, ``assistant_text`` is the
        transcription accumulated so far."""
        cur = self._current
        if cur is None or cur.is_terminal:
            return
        cur.assistant_text = spoken_text

    def on_turn_complete(self) -> CloudTurn | None:
        """The turn finished normally (or was interrupted, or the session
        was lost) — commit exactly once. Returns ``None`` if there is
        nothing to commit (no current turn, or it was already
        committed/abandoned)."""
        cur = self._current
        if cur is None or cur.is_terminal:
            return None
        cur.state = CloudTurnState.COMMITTED
        return cur

    #: session/provider loss before an explicit turn-complete signal is the
    #: same one-shot commit gate — whatever was accumulated is committed
    #: (or nothing, if there was nothing).
    on_session_lost = on_turn_complete
