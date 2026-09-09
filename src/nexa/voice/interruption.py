"""M2.5B — the pure barge-in / interruption state machine.

No audio, no Pipecat, no I/O, no timers of its own — time is always passed
in — so the whole thing is deterministically unit-testable (ADR-0003 style,
mirroring ``nexa.voice.state``).

States
------
``IDLE``                — no assistant reply in flight.
``RESPONDING``          — a reply is in flight (``response_in_flight``);
                          the mic is hot (only safe with the XVF3800 AEC
                          reference fed — see R0028 / ``AecReferenceHealth``);
                          VAD speech is watched for an interruption.
``INTERRUPT_CANDIDATE`` — the user has started speaking during a reply; not
                          yet confirmed. A ``VADUserStoppedSpeakingFrame``
                          before the hold elapses rejects it.
``INTERRUPTING``        — the interruption is confirmed; the old reply is
                          being torn down and the one interrupting utterance
                          captured. No second candidate is admitted here.

Invariants
----------
* **At most one candidate.** A second ``speech_started`` while
  ``INTERRUPT_CANDIDATE`` or ``INTERRUPTING`` is counted and ignored — it
  never creates a parallel candidate and never enqueues a second
  busy-period turn (the R0026 regression guard).
* **Monotonic ``response_id``.** Every ``RESPONDING`` entry allocates the
  next id; invalidated ids never come back. Nested interruptions get a
  fresh id, so late tokens/frames from any earlier reply are droppable by
  id comparison.
* Every transition is one small field assignment; not thread-safe by
  design (driven from the single Pipecat event loop, like
  ``HalfDuplexGate``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Default confirm hold: a ``VADUserStartedSpeakingFrame`` already implies
#: ``VADParams.start_secs`` (0.2 s) of confirmed speech; this adds the
#: ~100 ms guard from R0028 for a ~300 ms speech-onset → confirmed budget.
DEFAULT_CONFIRM_HOLD_SECS = 0.3


class InterruptionState(StrEnum):
    IDLE = "idle"
    RESPONDING = "responding"
    INTERRUPT_CANDIDATE = "interrupt_candidate"
    INTERRUPTING = "interrupting"


class InterruptionEvent(StrEnum):
    """What a call to the machine caused, for the controller + telemetry."""

    NONE = "none"
    RESPONSE_STARTED = "response_started"
    CANDIDATE_STARTED = "candidate_started"
    CANDIDATE_REJECTED = "candidate_rejected"
    INTERRUPT_CONFIRMED = "interrupt_confirmed"
    RESPONSE_FINISHED = "response_finished"
    IGNORED_SPEECH = "ignored_speech"


@dataclass
class InterruptionStateMachine:
    confirm_hold_secs: float = DEFAULT_CONFIRM_HOLD_SECS

    _state: InterruptionState = field(default=InterruptionState.IDLE, init=False)
    _response_id: int = field(default=0, init=False)
    _active_response_id: int | None = field(default=None, init=False)
    #: The id that ``poll`` most recently invalidated on a confirmed
    #: interruption — read by the controller when it fires ``on_confirmed``
    #: (``active_response_id`` is already ``None`` by then).
    _last_invalidated_response_id: int | None = field(default=None, init=False)
    _candidate_started_at: float | None = field(default=None, init=False)
    # telemetry counters
    _ignored_speech_starts: int = field(default=0, init=False)
    _rejected_candidates: int = field(default=0, init=False)
    _confirmed_interruptions: int = field(default=0, init=False)

    # -- read-only view ---------------------------------------------------- #
    @property
    def state(self) -> InterruptionState:
        return self._state

    @property
    def response_in_flight(self) -> bool:
        return self._state in (
            InterruptionState.RESPONDING,
            InterruptionState.INTERRUPT_CANDIDATE,
            InterruptionState.INTERRUPTING,
        )

    @property
    def active_response_id(self) -> int | None:
        """The id of the reply currently in flight, or ``None`` when idle /
        while an interruption is being processed."""
        return self._active_response_id

    @property
    def last_invalidated_response_id(self) -> int | None:
        """The reply id the most recent confirmation invalidated."""
        return self._last_invalidated_response_id

    @property
    def candidate_started_at(self) -> float | None:
        return self._candidate_started_at

    @property
    def ignored_speech_starts(self) -> int:
        return self._ignored_speech_starts

    @property
    def rejected_candidates(self) -> int:
        return self._rejected_candidates

    @property
    def confirmed_interruptions(self) -> int:
        return self._confirmed_interruptions

    def is_current_response(self, response_id: int | None) -> bool:
        """True iff ``response_id`` is the reply currently in flight. Use to
        drop late tokens / frames from an invalidated response."""
        return response_id is not None and response_id == self._active_response_id

    # -- transitions ----------------------------------------------------- #
    def notify_response_dispatched(self) -> InterruptionEvent:
        """A user turn has started generating an assistant reply. Allocates
        and returns via ``active_response_id`` the new monotonic id. Valid
        from ``IDLE`` (normal turn) or ``INTERRUPTING`` (the interrupting
        utterance's own reply — a nested-safe fresh id)."""
        self._response_id += 1
        self._active_response_id = self._response_id
        self._state = InterruptionState.RESPONDING
        self._candidate_started_at = None
        return InterruptionEvent.RESPONSE_STARTED

    def notify_response_finished(self) -> InterruptionEvent:
        """Generation + playback for the current reply completed normally
        (no interruption). Back to ``IDLE``. A no-op if not responding."""
        if self._state == InterruptionState.IDLE:
            return InterruptionEvent.NONE
        self._state = InterruptionState.IDLE
        self._active_response_id = None
        self._candidate_started_at = None
        return InterruptionEvent.RESPONSE_FINISHED

    def speech_started(self, now: float) -> InterruptionEvent:
        """A ``VADUserStartedSpeakingFrame`` was seen."""
        if self._state == InterruptionState.RESPONDING:
            self._state = InterruptionState.INTERRUPT_CANDIDATE
            self._candidate_started_at = now
            return InterruptionEvent.CANDIDATE_STARTED
        # IDLE / already a candidate / already interrupting → single-candidate
        # invariant: never a second candidate, never a second busy-period turn.
        if self._state in (
            InterruptionState.INTERRUPT_CANDIDATE,
            InterruptionState.INTERRUPTING,
        ):
            self._ignored_speech_starts += 1
            return InterruptionEvent.IGNORED_SPEECH
        return InterruptionEvent.NONE

    def speech_stopped(self, now: float) -> InterruptionEvent:  # noqa: ARG002
        """A ``VADUserStoppedSpeakingFrame`` was seen. Rejects an unconfirmed
        candidate; ignored otherwise (the confirmed utterance's own stop is
        handled by the controller, not here)."""
        if self._state == InterruptionState.INTERRUPT_CANDIDATE:
            self._state = InterruptionState.RESPONDING
            self._candidate_started_at = None
            self._rejected_candidates += 1
            return InterruptionEvent.CANDIDATE_REJECTED
        return InterruptionEvent.NONE

    def poll(self, now: float) -> InterruptionEvent:
        """Time-driven confirmation check. The controller calls this on every
        frame it sees *and* once ``confirm_hold_secs`` after a candidate
        starts (a scheduled wake-up), so confirmation fires even with no
        further VAD frames. Confirms iff a candidate has held for
        ``confirm_hold_secs`` with no intervening stop."""
        if (
            self._state == InterruptionState.INTERRUPT_CANDIDATE
            and self._candidate_started_at is not None
            # 1 ns slop so a poll at exactly ``candidate_started + hold`` is
            # not lost to float subtraction error (5.3 - 5.0 < 0.3).
            and now - self._candidate_started_at >= self.confirm_hold_secs - 1e-9
        ):
            self._state = InterruptionState.INTERRUPTING
            self._candidate_started_at = None
            self._confirmed_interruptions += 1
            # The interrupted reply's id is now invalid — late tokens/frames
            # stamped with it must be dropped.
            self._last_invalidated_response_id = self._active_response_id
            self._active_response_id = None
            return InterruptionEvent.INTERRUPT_CONFIRMED
        return InterruptionEvent.NONE

    def notify_interruption_complete(self) -> InterruptionEvent:
        """The old reply has been torn down and the interrupting utterance is
        ready to be submitted as the next turn. Back to ``IDLE`` — the new
        turn will call ``notify_response_dispatched`` for its own id."""
        if self._state != InterruptionState.INTERRUPTING:
            return InterruptionEvent.NONE
        self._state = InterruptionState.IDLE
        self._active_response_id = None
        self._candidate_started_at = None
        return InterruptionEvent.RESPONSE_FINISHED

    def reset(self) -> None:
        """Pipeline stop / error — never leave a latched state."""
        self._state = InterruptionState.IDLE
        self._active_response_id = None
        self._candidate_started_at = None
