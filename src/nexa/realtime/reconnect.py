"""``ReconnectController`` — deterministic, provider-agnostic reconnect
policy state machine (ADR-0004 Decision I, amended by Amendment 1 §4).

This module contains **no** socket / provider-specific choreography — it
only decides *when* a reconnect should be attempted and *what outcome* each
attempt produces, given inputs the caller supplies. A real provider (e.g.
``GeminiLiveProvider``, M2.6B.2) drives an actual WebSocket and calls into
this controller; this module never opens one itself. The clock and jitter
source are injected so behaviour is fully deterministic in tests.

Amendment 1 §4 rules encoded here (`VERIFIED FACT` against the installed
Gemini Live API reference and Pipecat 1.8.1's own
``_handle_msg_resumption_update`` — see the M2.6B.1 source-audit note):

* only a handle reported with ``resumable=True`` is ever retained — an
  empty or non-resumable handle is discarded, never used (this mirrors
  ``GeminiLiveLLMService._handle_msg_resumption_update``'s own
  ``if update.resumable and update.new_handle`` guard);
* a proactive reconnect prefers a safe turn boundary where the caller can
  report one, and a ``GoAway`` deadline is respected via ``timeLeft``;
* **make-before-break connection overlap is NOT assumed** — this
  controller only tracks state/outcomes; the actual socket sequencing
  belongs to ``GeminiLiveProvider`` (M2.6B.2).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum

#: "~8 min" — inside both the ~10-minute WebSocket lifetime and the 15-min
#: audio-session cap (ADR-0004 Decision I).
DEFAULT_PROACTIVE_RECONNECT_AGE_S = 8 * 60
DEFAULT_MAX_RECONNECT_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE_S = 0.5
DEFAULT_BACKOFF_MAX_S = 30.0
DEFAULT_GO_AWAY_MARGIN_S = 5.0


class ReconnectOutcome(StrEnum):
    """What one reconnect attempt produced."""

    #: The session was restored via a resumable handle — Gemini's own
    #: context is intact; NeXa touches nothing canonical.
    RESUMED = "resumed"
    #: No safe resumption occurred (no resumable handle, or resumption
    #: failed) — the caller must rebuild a fresh ``CloudContextSnapshot``.
    FRESH_SESSION_REQUIRED = "fresh_session_required"
    #: The attempt itself failed to connect; retries remain.
    FAILED_RETRY = "failed_retry"
    #: The attempt failed and ``max_attempts`` is exhausted — terminal;
    #: the caller applies the ADR-0004 Decision J failure/fallback table.
    FAILED_TERMINAL = "failed_terminal"


class ReconnectTrigger(StrEnum):
    AGE_TIMER = "age_timer"
    GO_AWAY = "go_away"
    CONNECTION_ERROR = "connection_error"


@dataclass(frozen=True, slots=True)
class SessionResumptionHandle:
    """One handle reported by the provider. Only ever retained by
    ``offer_resumption_handle`` when ``resumable`` is True (Amendment 1
    §4)."""

    value: str
    resumable: bool


@dataclass
class ReconnectController:
    proactive_age_s: float = DEFAULT_PROACTIVE_RECONNECT_AGE_S
    max_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S
    backoff_max_s: float = DEFAULT_BACKOFF_MAX_S
    #: injected for deterministic tests (default: a fresh, unseeded RNG).
    random_source: random.Random = field(default_factory=random.Random)

    _latest_resumable_handle: SessionResumptionHandle | None = field(default=None, init=False)
    _attempt_count: int = field(default=0, init=False)
    _connected_at: float | None = field(default=None, init=False)

    def on_connected(self, *, now: float) -> None:
        """Call once a session becomes READY — resets the age timer and the
        retry counter (a healthy connection forgives past failures)."""
        self._connected_at = now
        self._attempt_count = 0

    def offer_resumption_handle(self, handle: SessionResumptionHandle) -> None:
        """Record a handle reported by the provider.

        A non-resumable or empty-valued handle is discarded — never treated
        as usable (Amendment 1 §4).
        """
        if handle.resumable and handle.value:
            self._latest_resumable_handle = handle

    @property
    def latest_resumable_handle(self) -> SessionResumptionHandle | None:
        return self._latest_resumable_handle

    def should_proactively_reconnect(self, *, now: float) -> bool:
        if self._connected_at is None:
            return False
        return (now - self._connected_at) >= self.proactive_age_s

    def go_away_deadline(
        self, *, now: float, time_left_s: float, margin_s: float = DEFAULT_GO_AWAY_MARGIN_S
    ) -> float:
        """Amendment 1 §4 — use ``GoAway.timeLeft`` to schedule a
        controlled transition rather than waiting for the abrupt
        ``ABORTED`` close."""
        return now + max(0.0, time_left_s - margin_s)

    def attempt(
        self,
        *,
        trigger: ReconnectTrigger,  # noqa: ARG002 — kept for caller telemetry/tests
        safe_turn_boundary: bool,  # noqa: ARG002 — telemetry only; never forces overlap
        connect_succeeded: bool,
        resumption_succeeded: bool | None,
    ) -> ReconnectOutcome:
        """Record one reconnect attempt and return its outcome.

        ``safe_turn_boundary`` records whether the caller found a safe
        point (e.g. ``generationComplete``) to make the transition — this
        controller never *requires* overlap to honour it; the actual
        sequencing is the caller's (M2.6B.2) concern.

        ``resumption_succeeded`` is ``None`` when no resumable handle was
        available to attempt resumption with (a fresh session is
        required); ``True``/``False`` when an attempt was actually made.
        """
        self._attempt_count += 1
        if not connect_succeeded:
            if self._attempt_count >= self.max_attempts:
                return ReconnectOutcome.FAILED_TERMINAL
            return ReconnectOutcome.FAILED_RETRY
        if self._latest_resumable_handle is None or resumption_succeeded is None:
            return ReconnectOutcome.FRESH_SESSION_REQUIRED
        if resumption_succeeded:
            return ReconnectOutcome.RESUMED
        return ReconnectOutcome.FRESH_SESSION_REQUIRED

    def backoff_seconds(self) -> float:
        """Bounded exponential backoff + jitter (deterministic via the
        injected ``random_source``)."""
        raw = self.backoff_base_s * (2 ** max(0, self._attempt_count - 1))
        capped = min(raw, self.backoff_max_s)
        jitter = self.random_source.uniform(0.0, capped * 0.25)
        return capped + jitter

    @property
    def attempt_count(self) -> int:
        return self._attempt_count
