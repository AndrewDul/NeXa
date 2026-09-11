"""``CloudTurnAccumulator`` — one logical cloud turn -> at most one canonical
commit (ADR-0004 Decision A, M2.6B.2).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.turn import CloudTurnAccumulator, CloudTurnState  # noqa: E402


class TestNormalTurn(unittest.TestCase):
    def test_normal_exchange(self) -> None:
        acc = CloudTurnAccumulator()
        turn = acc.start_turn()
        self.assertEqual(turn.generation, 1)
        acc.on_user_transcription("hej", final=True)
        acc.on_assistant_transcription("cześć", final=True)
        committed = acc.on_turn_complete()
        self.assertIsNotNone(committed)
        self.assertEqual(committed.user_text, "hej")
        self.assertEqual(committed.assistant_text, "cześć")
        self.assertFalse(committed.interrupted)
        self.assertEqual(committed.state, CloudTurnState.COMMITTED)

    def test_assistant_text_accumulates_deltas(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("hi", final=True)
        acc.on_assistant_transcription("Hel", final=False)
        acc.on_assistant_transcription("lo!", final=True)
        committed = acc.on_turn_complete()
        self.assertEqual(committed.assistant_text, "Hello!")


class TestUserOnly(unittest.TestCase):
    def test_session_lost_before_assistant_spoke(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("hej", final=True)
        committed = acc.on_session_lost()
        self.assertEqual(committed.user_text, "hej")
        self.assertEqual(committed.assistant_text, "")


class TestInterruption(unittest.TestCase):
    def test_interrupted_with_spoken_prefix(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("tell me a story", final=True)
        acc.on_assistant_transcription("Once upon a ti", final=False)
        acc.on_interruption()
        acc.set_spoken_prefix("Once upon a ti")
        committed = acc.on_turn_complete()
        self.assertTrue(committed.interrupted)
        self.assertEqual(committed.assistant_text, "Once upon a ti")

    def test_interrupted_before_any_audio(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("hej", final=True)
        acc.on_interruption()
        committed = acc.on_turn_complete()
        self.assertTrue(committed.interrupted)
        self.assertEqual(committed.assistant_text, "")


class TestAtMostOneCommit(unittest.TestCase):
    def test_late_frames_after_commit_are_ignored(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("hej", final=True)
        acc.on_assistant_transcription("hi", final=True)
        first = acc.on_turn_complete()
        self.assertIsNotNone(first)

        acc.on_assistant_transcription(" there", final=True)  # late frame
        acc.on_interruption()  # late frame
        second = acc.on_turn_complete()  # already committed
        self.assertIsNone(second)
        self.assertEqual(first.assistant_text, "hi")  # unchanged by the late frames
        self.assertFalse(first.interrupted)

    def test_new_turn_supersedes_unfinished_previous_turn(self) -> None:
        acc = CloudTurnAccumulator()
        first = acc.start_turn()
        acc.on_user_transcription("first", final=True)
        # no commit — a new turn starts (e.g. a fast follow-up utterance)
        second = acc.start_turn()
        self.assertEqual(first.state, CloudTurnState.ABANDONED)
        self.assertEqual(second.generation, first.generation + 1)
        # the old turn can never be committed after being superseded
        acc.on_user_transcription("second", final=True)
        committed = acc.on_turn_complete()
        self.assertEqual(committed.generation, second.generation)
        self.assertEqual(committed.user_text, "second")

    def test_generation_ids_are_local_never_from_the_provider(self) -> None:
        acc = CloudTurnAccumulator()
        t1 = acc.start_turn()
        acc.on_turn_complete()
        t2 = acc.start_turn()
        acc.on_turn_complete()
        t3 = acc.start_turn()
        self.assertEqual([t1.generation, t2.generation, t3.generation], [1, 2, 3])


class TestEmptyOrNoTurn(unittest.TestCase):
    def test_events_before_any_start_turn_are_ignored(self) -> None:
        acc = CloudTurnAccumulator()
        acc.on_user_transcription("orphan", final=True)  # no current turn
        acc.on_interruption()
        self.assertIsNone(acc.on_turn_complete())

    def test_turn_complete_with_no_user_transcription_yet(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()  # started, but never got a user transcription
        committed = acc.on_turn_complete()
        self.assertIsNotNone(committed)
        self.assertIsNone(committed.user_text)


if __name__ == "__main__":
    unittest.main()
