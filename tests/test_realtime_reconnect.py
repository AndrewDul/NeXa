"""``ReconnectController`` — deterministic reconnect policy (ADR-0004
Decision I, amended by Amendment 1 §4).
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.reconnect import (  # noqa: E402
    ReconnectController,
    ReconnectOutcome,
    ReconnectTrigger,
    SessionResumptionHandle,
)


class TestAgeTimerAndGoAway(unittest.TestCase):
    def test_proactive_reconnect_triggers_after_age(self) -> None:
        controller = ReconnectController(proactive_age_s=60.0)
        controller.on_connected(now=0.0)
        self.assertFalse(controller.should_proactively_reconnect(now=59.9))
        self.assertTrue(controller.should_proactively_reconnect(now=60.0))

    def test_not_triggered_before_any_connection(self) -> None:
        controller = ReconnectController()
        self.assertFalse(controller.should_proactively_reconnect(now=10_000.0))

    def test_go_away_deadline_respects_margin(self) -> None:
        controller = ReconnectController()
        deadline = controller.go_away_deadline(now=100.0, time_left_s=20.0, margin_s=5.0)
        self.assertEqual(deadline, 115.0)

    def test_go_away_deadline_never_negative_offset(self) -> None:
        controller = ReconnectController()
        deadline = controller.go_away_deadline(now=100.0, time_left_s=1.0, margin_s=5.0)
        self.assertEqual(deadline, 100.0)  # clamped, never schedules in the past


class TestResumptionHandleHygiene(unittest.TestCase):
    """Amendment 1 §4 — only a ``resumable=True`` handle is ever retained."""

    def test_non_resumable_handle_is_discarded(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="h1", resumable=False))
        self.assertIsNone(controller.latest_resumable_handle)

    def test_empty_handle_is_discarded(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="", resumable=True))
        self.assertIsNone(controller.latest_resumable_handle)

    def test_resumable_handle_is_retained(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="h1", resumable=True))
        self.assertEqual(controller.latest_resumable_handle.value, "h1")

    def test_only_the_latest_resumable_handle_is_kept(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="h1", resumable=True))
        controller.offer_resumption_handle(SessionResumptionHandle(value="h2", resumable=True))
        self.assertEqual(controller.latest_resumable_handle.value, "h2")

    def test_a_later_non_resumable_update_does_not_erase_the_last_good_handle(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="h1", resumable=True))
        controller.offer_resumption_handle(SessionResumptionHandle(value="", resumable=False))
        self.assertEqual(controller.latest_resumable_handle.value, "h1")


class TestAttemptOutcomes(unittest.TestCase):
    def test_connect_failure_retries_then_terminates(self) -> None:
        controller = ReconnectController(max_attempts=3)
        for _ in range(2):
            outcome = controller.attempt(
                trigger=ReconnectTrigger.CONNECTION_ERROR,
                safe_turn_boundary=False,
                connect_succeeded=False,
                resumption_succeeded=None,
            )
            self.assertEqual(outcome, ReconnectOutcome.FAILED_RETRY)
        terminal = controller.attempt(
            trigger=ReconnectTrigger.CONNECTION_ERROR,
            safe_turn_boundary=False,
            connect_succeeded=False,
            resumption_succeeded=None,
        )
        self.assertEqual(terminal, ReconnectOutcome.FAILED_TERMINAL)

    def test_connect_ok_no_handle_requires_fresh_session(self) -> None:
        controller = ReconnectController()
        outcome = controller.attempt(
            trigger=ReconnectTrigger.AGE_TIMER,
            safe_turn_boundary=True,
            connect_succeeded=True,
            resumption_succeeded=None,
        )
        self.assertEqual(outcome, ReconnectOutcome.FRESH_SESSION_REQUIRED)

    def test_connect_ok_with_handle_and_successful_resumption(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="h1", resumable=True))
        outcome = controller.attempt(
            trigger=ReconnectTrigger.GO_AWAY,
            safe_turn_boundary=True,
            connect_succeeded=True,
            resumption_succeeded=True,
        )
        self.assertEqual(outcome, ReconnectOutcome.RESUMED)

    def test_connect_ok_with_handle_but_failed_resumption_requires_fresh_session(self) -> None:
        controller = ReconnectController()
        controller.offer_resumption_handle(SessionResumptionHandle(value="h1", resumable=True))
        outcome = controller.attempt(
            trigger=ReconnectTrigger.GO_AWAY,
            safe_turn_boundary=True,
            connect_succeeded=True,
            resumption_succeeded=False,
        )
        self.assertEqual(outcome, ReconnectOutcome.FRESH_SESSION_REQUIRED)

    def test_on_connected_resets_attempt_count(self) -> None:
        controller = ReconnectController(max_attempts=2)
        controller.attempt(
            trigger=ReconnectTrigger.CONNECTION_ERROR,
            safe_turn_boundary=False,
            connect_succeeded=False,
            resumption_succeeded=None,
        )
        self.assertEqual(controller.attempt_count, 1)
        controller.on_connected(now=42.0)
        self.assertEqual(controller.attempt_count, 0)


class TestBackoff(unittest.TestCase):
    def test_backoff_is_bounded_and_deterministic_with_seeded_random(self) -> None:
        controller = ReconnectController(
            backoff_base_s=1.0, backoff_max_s=8.0, random_source=random.Random(1234)
        )
        for _ in range(6):
            controller.attempt(
                trigger=ReconnectTrigger.CONNECTION_ERROR,
                safe_turn_boundary=False,
                connect_succeeded=False,
                resumption_succeeded=None,
            )
            delay = controller.backoff_seconds()
            # capped exponential (<=8.0) + up to 25% jitter on top
            self.assertLessEqual(delay, 8.0 * 1.25)
            self.assertGreaterEqual(delay, 0.0)

    def test_same_seed_reproduces_same_backoff_sequence(self) -> None:
        c1 = ReconnectController(random_source=random.Random(7))
        c2 = ReconnectController(random_source=random.Random(7))
        for _ in range(4):
            c1.attempt(
                trigger=ReconnectTrigger.CONNECTION_ERROR,
                safe_turn_boundary=False,
                connect_succeeded=False,
                resumption_succeeded=None,
            )
            c2.attempt(
                trigger=ReconnectTrigger.CONNECTION_ERROR,
                safe_turn_boundary=False,
                connect_succeeded=False,
                resumption_succeeded=None,
            )
            self.assertEqual(c1.backoff_seconds(), c2.backoff_seconds())


if __name__ == "__main__":
    unittest.main()
