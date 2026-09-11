"""``ConversationSession.record_external_exchange`` — the canonical cloud
turn write path (ADR-0004 Decision A, M2.6B.2). Purely additive:
``send()`` / ``commit_interrupted_turn()`` are covered by their own
existing test files and are not touched here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.session import ConversationSession, ExternalExchangeOutcome  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402


def _session() -> ConversationSession:
    return ConversationSession(provider=FakeModelProvider(), system_prompt="p")


class TestNormalExchange(unittest.TestCase):
    def test_user_and_assistant_turn_appended(self) -> None:
        session = _session()
        outcome = session.record_external_exchange("hej", "cześć")
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_EXCHANGE)
        self.assertEqual(len(session.history), 2)
        self.assertEqual(session.history[0].role, Role.USER)
        self.assertEqual(session.history[0].content, "hej")
        self.assertEqual(session.history[1].role, Role.ASSISTANT)
        self.assertEqual(session.history[1].content, "cześć")
        self.assertFalse(session.history[1].interrupted)

    def test_response_language_recorded_on_user_slot_only(self) -> None:
        session = _session()
        session.record_external_exchange("hej", "hi", response_language="pl")
        self.assertEqual(session._response_languages, ["pl", None])  # noqa: SLF001


class TestUserOnly(unittest.TestCase):
    def test_session_lost_before_assistant_spoke(self) -> None:
        session = _session()
        outcome = session.record_external_exchange("hej", None)
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0].role, Role.USER)

    def test_blank_assistant_text_treated_as_none(self) -> None:
        session = _session()
        outcome = session.record_external_exchange("hej", "   ")
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)


class TestInterrupted(unittest.TestCase):
    def test_interrupted_with_spoken_prefix_marks_assistant_turn(self) -> None:
        session = _session()
        outcome = session.record_external_exchange(
            "tell me a story", "Once upon a ti", interrupted=True
        )
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_EXCHANGE)
        self.assertEqual(len(session.history), 2)
        self.assertTrue(session.history[1].interrupted)
        self.assertEqual(session.history[1].content, "Once upon a ti")

    def test_no_spoken_assistant_means_no_empty_assistant_turn(self) -> None:
        session = _session()
        outcome = session.record_external_exchange("hej", None, interrupted=True)
        self.assertEqual(outcome, ExternalExchangeOutcome.COMMITTED_USER_ONLY)
        self.assertEqual(len(session.history), 1)  # never an empty assistant turn
        self.assertEqual(session.history[0].role, Role.USER)


class TestInvalid(unittest.TestCase):
    def test_blank_user_text_raises(self) -> None:
        session = _session()
        with self.assertRaises(ValueError):
            session.record_external_exchange("", "hi")
        with self.assertRaises(ValueError):
            session.record_external_exchange("   ", "hi")
        self.assertEqual(len(session.history), 0)  # never corrupts alignment


class TestIndexAlignmentAcrossMultipleTurns(unittest.TestCase):
    def test_history_and_response_languages_stay_aligned(self) -> None:
        session = _session()
        session.record_external_exchange("one", "1", response_language="en")
        session.record_external_exchange("two", None)  # user-only
        session.record_external_exchange("three", "3", interrupted=True)

        self.assertEqual(
            [t.content for t in session.history],
            ["one", "1", "two", "three", "3"],
        )
        self.assertEqual(
            [t.role for t in session.history],
            [Role.USER, Role.ASSISTANT, Role.USER, Role.USER, Role.ASSISTANT],
        )
        self.assertEqual(len(session.history), len(session._response_languages))  # noqa: SLF001
        self.assertTrue(session.history[4].interrupted)
        self.assertFalse(session.history[1].interrupted)


if __name__ == "__main__":
    unittest.main()
