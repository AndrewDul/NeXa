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

    def test_late_assistant_transcription_after_interruption_is_ignored(self) -> None:
        """M2.6B.2A — local interruption authority wins over a late server
        ACK/transcription delta: once interrupted, further assistant text
        must never grow past the actually-spoken prefix."""
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("tell me a story", final=True)
        acc.on_assistant_transcription("Once upon a ti", final=False)
        acc.on_interruption()
        acc.set_spoken_prefix("Once upon a ti")
        acc.on_assistant_transcription(" the end", final=True)  # late, must be ignored
        committed = acc.on_turn_complete()
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


class TestForeignTranscriptCannotOverwriteFinalizedUserText(unittest.TestCase):
    """M2.6B.4H (R0046) — the second half of the interruption-candidate
    overlap-hazard fix (the first half is
    ``ConversationRouter.has_turn_awaiting_assistant()``, which stops a
    candidate's VAD start from ever calling ``begin_cloud_turn()`` in the
    first place — see ``nexa.realtime.gemini.runtime``'s module
    docstring). Even so, this guard is independently necessary: an
    interruption candidate's audio is still sent LIVE to the not-yet-
    quarantined provider before confirmation (R0045), so its own
    transcript can physically arrive on ``ConversationRouter.on_user_
    transcription`` while the STILL-OPEN, already-finalized turn N is
    ``self._current`` — this is the one call site that must never let it
    through."""

    def test_second_final_transcript_for_same_turn_is_ignored(self) -> None:
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("tell me about black holes", final=True)
        # a later, foreign transcript for an overlapping interruption
        # candidate — must NOT overwrite the already-finalized user text.
        acc.on_user_transcription("what about the second case", final=True)
        acc.on_assistant_transcription("Black holes are...", final=True)
        committed = acc.on_turn_complete()
        self.assertEqual(committed.user_text, "tell me about black holes")

    def test_interim_candidate_transcript_after_final_is_also_ignored(self) -> None:
        """Not just a second FINAL — an INTERIM candidate transcript must
        be blocked too (Gemini's own interim/final message ordering is not
        something NeXa controls or should rely on for this guarantee)."""
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("hello there", final=True)
        acc.on_user_transcription("uh", final=False)  # candidate's interim
        committed = acc.on_turn_complete()
        self.assertEqual(committed.user_text, "hello there")

    def test_before_its_own_final_a_turns_own_transcript_still_updates_live(self) -> None:
        """The guard must not block a turn's OWN legitimate interim ->
        final progression — only a SECOND write after user_final is
        already True."""
        acc = CloudTurnAccumulator()
        acc.start_turn()
        acc.on_user_transcription("tell", final=False)
        acc.on_user_transcription("tell me", final=False)
        acc.on_user_transcription("tell me a story", final=True)
        committed = acc.on_turn_complete()
        self.assertEqual(committed.user_text, "tell me a story")


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
