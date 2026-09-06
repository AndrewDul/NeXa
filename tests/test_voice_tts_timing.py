"""M2.4 `TurnTimingTracker` tests — the real-hardware-testing-driven fix for
a genuine instrumentation bug: a later turn's conversation processing can
start while an earlier turn's TTS audio is still playing
(`SerialConversationQueue` only serializes LLM generation, not how long
TTS takes to *play*), so a single shared "current turn" timing dict gets
overwritten before the earlier turn ever reports its own timings.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice_tts import TurnTimingTracker  # noqa: E402


class TestSingleTurnLifecycle(unittest.TestCase):
    def test_full_lifecycle_records_every_timestamp(self) -> None:
        tracker = TurnTimingTracker()
        tracker.start_turn(end_of_turn=1.0, stt_result=1.5)
        tracker.first_token()
        tracker.assistant_complete()
        tracker.tts_started()
        tracker.tts_first_audio()
        finished = tracker.tts_stopped()

        self.assertIsNotNone(finished)
        self.assertEqual(finished.end_of_turn, 1.0)
        self.assertEqual(finished.stt_result, 1.5)
        self.assertIsNotNone(finished.first_token)
        self.assertIsNotNone(finished.assistant_complete)
        self.assertIsNotNone(finished.tts_started)
        self.assertIsNotNone(finished.tts_first_audio)
        self.assertIsNotNone(finished.tts_stopped)

    def test_first_token_is_set_only_once(self) -> None:
        tracker = TurnTimingTracker()
        tracker.start_turn()
        tracker.first_token()
        first_value = tracker._active.first_token
        tracker.first_token()  # a second token must not move it
        self.assertEqual(tracker._active.first_token, first_value)

    def test_tts_first_audio_is_set_only_once_per_turn(self) -> None:
        """A second (or third) sentence's audio within the same response
        must never overwrite the first sentence's timestamp."""
        tracker = TurnTimingTracker()
        tracker.start_turn()
        tracker.assistant_complete()
        tracker.tts_started()
        tracker.tts_first_audio()
        first_value = tracker._queue[0].tts_first_audio
        tracker.tts_first_audio()  # second sentence's audio
        tracker.tts_first_audio()  # third sentence's audio
        self.assertEqual(tracker._queue[0].tts_first_audio, first_value)

    def test_tts_started_is_set_only_once_per_turn(self) -> None:
        """`first_sentence_ready_at`: a later sentence's `TTSStartedFrame`
        (or a duplicate one after an idle-timeout stop) must not move it."""
        tracker = TurnTimingTracker()
        tracker.start_turn()
        tracker.assistant_complete()
        tracker.tts_started()
        first_value = tracker._queue[0].tts_started
        tracker.tts_started()  # second sentence
        tracker.tts_started()  # duplicate started frame
        self.assertEqual(tracker._queue[0].tts_started, first_value)

    def test_pending_tts_count_reflects_queue_depth(self) -> None:
        tracker = TurnTimingTracker()
        self.assertEqual(tracker.pending_tts_count, 0)
        tracker.start_turn()
        tracker.assistant_complete()
        self.assertEqual(tracker.pending_tts_count, 1)
        tracker.tts_stopped()
        self.assertEqual(tracker.pending_tts_count, 0)


class TestStreamingOverlapProof(unittest.TestCase):
    def test_positive_overlap_when_audio_precedes_completion(self) -> None:
        from nexa.voice_tts.timing import TurnTiming

        turn = TurnTiming(assistant_complete=15.0, tts_first_audio=12.0)
        self.assertAlmostEqual(turn.streaming_overlap_seconds, 3.0)

    def test_none_when_either_timestamp_missing(self) -> None:
        from nexa.voice_tts.timing import TurnTiming

        self.assertIsNone(TurnTiming(assistant_complete=1.0).streaming_overlap_seconds)
        self.assertIsNone(TurnTiming(tts_first_audio=1.0).streaming_overlap_seconds)
        self.assertIsNone(TurnTiming().streaming_overlap_seconds)


class TestStreamingOverlapConfirmed(unittest.TestCase):
    """The PASS condition, stated exactly: `first_tts_audio_at <
    assistant_complete_at`."""

    def test_true_when_first_audio_precedes_completion(self) -> None:
        from nexa.voice_tts.timing import TurnTiming

        turn = TurnTiming(assistant_complete=15.0, tts_first_audio=12.0)
        self.assertTrue(turn.streaming_overlap_confirmed)

    def test_false_when_first_audio_after_completion(self) -> None:
        from nexa.voice_tts.timing import TurnTiming

        turn = TurnTiming(assistant_complete=12.0, tts_first_audio=15.0)
        self.assertFalse(turn.streaming_overlap_confirmed)

    def test_false_when_equal(self) -> None:
        from nexa.voice_tts.timing import TurnTiming

        turn = TurnTiming(assistant_complete=12.0, tts_first_audio=12.0)
        self.assertFalse(turn.streaming_overlap_confirmed)

    def test_false_not_none_when_a_timestamp_is_missing(self) -> None:
        from nexa.voice_tts.timing import TurnTiming

        self.assertFalse(TurnTiming(assistant_complete=1.0).streaming_overlap_confirmed)
        self.assertFalse(TurnTiming(tts_first_audio=1.0).streaming_overlap_confirmed)
        self.assertFalse(TurnTiming().streaming_overlap_confirmed)

    def test_later_sentences_do_not_flip_a_confirmed_pass(self) -> None:
        """The known instrumentation bug, as a regression: sentence 1's
        audio starts before generation completes (real streaming overlap);
        later sentences of the same reply then produce more TTS-audio
        callbacks. Those must neither move `first_tts_audio` nor flip the
        PASS verdict."""
        import time

        tracker = TurnTimingTracker()
        tracker.start_turn(end_of_turn=100.0)
        tracker.first_token()
        tracker.tts_started()
        tracker.tts_first_audio()  # sentence 1 audio — BEFORE completion
        recorded_first_audio = tracker._queue[0].tts_first_audio

        time.sleep(0.01)
        tracker.assistant_complete()  # generation finishes after audio began

        # Later sentences keep arriving while turn 1 audio plays on.
        for _ in range(5):
            time.sleep(0.005)
            tracker.tts_started()
            tracker.tts_first_audio()

        finished = tracker.tts_stopped()
        self.assertEqual(finished.tts_first_audio, recorded_first_audio)
        self.assertLess(finished.tts_first_audio, finished.assistant_complete)
        self.assertTrue(finished.streaming_overlap_confirmed)


class TestCrossTurnCorrelationRegression(unittest.TestCase):
    """The real-hardware-found bug: a second turn's conversation processing
    starting (and completing) while the first turn's TTS is still playing
    must never corrupt the first turn's own timing record — and the first
    turn's `tts_first_audio` must be reported against *its own*
    `assistant_complete`, not the second turn's."""

    def test_second_turn_starting_during_first_turns_tts_does_not_corrupt_either(self) -> None:
        tracker = TurnTimingTracker()

        # Turn 1: conversation completes...
        tracker.start_turn(end_of_turn=100.0, stt_result=101.0)
        tracker.first_token()
        tracker.assistant_complete()

        # ...and its TTS starts speaking (sentence 1 of a multi-sentence reply).
        tracker.tts_started()
        tracker.tts_first_audio()
        turn1_first_audio = tracker._queue[0].tts_first_audio

        # Turn 2's conversation starts AND completes while turn 1 is still
        # speaking (SerialConversationQueue only serializes generation).
        tracker.start_turn(end_of_turn=110.0, stt_result=111.0)
        tracker.first_token()
        tracker.assistant_complete()

        # Turn 1's TTS finally finishes — must report turn 1's own values.
        finished1 = tracker.tts_stopped()
        self.assertEqual(finished1.end_of_turn, 100.0)
        self.assertEqual(finished1.tts_first_audio, turn1_first_audio)
        self.assertIsNotNone(finished1.streaming_overlap_seconds)

        # Turn 2's TTS then starts/stops — must not inherit turn 1's values.
        tracker.tts_started()
        tracker.tts_first_audio()
        finished2 = tracker.tts_stopped()
        self.assertEqual(finished2.end_of_turn, 110.0)
        self.assertNotEqual(finished2.tts_first_audio, finished1.tts_first_audio)

    def test_three_rapid_turns_each_keep_their_own_record(self) -> None:
        tracker = TurnTimingTracker()
        for i in range(3):
            tracker.start_turn(end_of_turn=float(i))
            tracker.assistant_complete()
            tracker.tts_started()
            tracker.tts_first_audio()

        results = [tracker.tts_stopped() for _ in range(3)]
        self.assertEqual([r.end_of_turn for r in results], [0.0, 1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
