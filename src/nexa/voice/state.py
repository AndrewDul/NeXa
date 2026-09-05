"""Voice activity state machine (M2.1, ADR-0003).

Pure state logic — no audio, no Pipecat, no I/O — so it is fully testable
without hardware. Only states that are real in M2.1 exist here:
``IDLE``/``LISTENING``/``USER_SPEAKING``/``END_OF_TURN``/``ERROR``.
``UNDERSTANDING``/``THINKING``/``SPEAKING``/``INTERRUPTED`` are not modeled —
they become real only once STT/LLM/TTS/barge-in exist (M2.2+).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class VoiceState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    END_OF_TURN = "end_of_turn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    from_state: VoiceState
    to_state: VoiceState
    reason: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class VoiceStateMachine:
    """Deterministic voice-activity state machine.

    Noisy/duplicate inputs (a second speech-start while already speaking, a
    speech-stop with no matching start) are explicitly ignored rather than
    corrupting state — see the guards in each transition method.
    """

    def __init__(self, *, on_event: Callable[[VoiceEvent], None] | None = None) -> None:
        self._state = VoiceState.IDLE
        self._on_event = on_event
        self._history: list[VoiceEvent] = []

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def history(self) -> tuple[VoiceEvent, ...]:
        return tuple(self._history)

    def _transition(self, to_state: VoiceState, reason: str) -> None:
        event = VoiceEvent(from_state=self._state, to_state=to_state, reason=reason)
        self._state = to_state
        self._history.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def start_listening(self) -> None:
        """Runtime has started (or is recovering from ERROR) and is ready to listen."""
        if self._state not in (VoiceState.IDLE, VoiceState.ERROR):
            return
        self._transition(VoiceState.LISTENING, "runtime started")

    def speech_started(self) -> None:
        """VAD confirmed the user started speaking."""
        if self._state == VoiceState.USER_SPEAKING:
            return  # duplicate VAD start while already speaking — ignored, not corrupted
        if self._state not in (VoiceState.LISTENING, VoiceState.END_OF_TURN):
            return  # speech-start while IDLE/ERROR is noise — ignored explicitly
        self._transition(VoiceState.USER_SPEAKING, "VAD speech start")

    def speech_stopped(self) -> None:
        """VAD confirmed the user stopped speaking; the turn ends and listening resumes."""
        if self._state != VoiceState.USER_SPEAKING:
            return  # duplicate/noisy stop with no matching start — ignored explicitly
        self._transition(VoiceState.END_OF_TURN, "VAD speech stop")
        self._transition(VoiceState.LISTENING, "turn complete, resume listening")

    def error(self, reason: str) -> None:
        """An unrecoverable runtime error occurred. Always transitions, from any state."""
        self._transition(VoiceState.ERROR, reason or "unknown error")

    def shutdown(self) -> None:
        """Runtime is shutting down. Always transitions to IDLE, from any state."""
        self._transition(VoiceState.IDLE, "runtime shutdown")
