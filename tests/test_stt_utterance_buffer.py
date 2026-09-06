"""M2.2 `UtteranceBuffer` tests — pure logic, no Pipecat/hardware needed.

Covers the pre-roll requirement directly: confirmed speech-start must
include the audio that arrived immediately before confirmation, no audio
may leak between turns, and trailing audio up to the stop event is never
truncated.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt.utterance_buffer import UtteranceBuffer  # noqa: E402

SAMPLE_RATE = 16_000
CHUNK = b"\x01\x02" * 512  # 512 samples = 1024 bytes = 32ms @ 16kHz mono16


class TestPreRollIsPreserved(unittest.TestCase):
    def test_pre_roll_audio_before_speech_start_is_retained(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        for _ in range(20):  # 640ms of pre-speech audio fed while LISTENING
            buf.append_audio(CHUNK)

        buf.mark_speech_started()
        buf.append_audio(CHUNK)
        audio = buf.mark_speech_stopped()

        # At least one full pre-roll chunk must have survived into the
        # utterance in addition to the one chunk appended after start.
        self.assertGreater(
            len(audio), len(CHUNK), "utterance must include pre-roll audio, not just post-start"
        )

    def test_ring_never_exceeds_configured_pre_roll_budget(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        for _ in range(200):  # feed far more than 500ms of audio
            buf.append_audio(CHUNK)
        self.assertLessEqual(buf._ring_bytes, buf._pre_roll_bytes)

    def test_short_pre_speech_audio_is_not_padded_or_dropped(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        buf.append_audio(CHUNK)  # only 32ms available, well under the 500ms budget
        buf.mark_speech_started()
        audio = buf.mark_speech_stopped()
        self.assertEqual(audio, CHUNK)


class TestTrailingAudioIsNotTruncated(unittest.TestCase):
    def test_all_audio_appended_while_capturing_is_returned(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        buf.mark_speech_started()
        chunks = [CHUNK, CHUNK, CHUNK]
        for c in chunks:
            buf.append_audio(c)
        audio = buf.mark_speech_stopped()
        self.assertEqual(audio, b"".join(chunks))


class TestUtteranceLifecycleAndIsolation(unittest.TestCase):
    def test_buffer_resets_after_each_turn(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        buf.mark_speech_started()
        buf.append_audio(CHUNK)
        buf.mark_speech_stopped()
        self.assertFalse(buf.is_capturing)

    def test_audio_from_previous_turn_cannot_leak_into_next(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)

        buf.mark_speech_started()
        buf.append_audio(b"\xAA" * 1024)
        first = buf.mark_speech_stopped()

        buf.mark_speech_started()
        buf.append_audio(b"\xBB" * 1024)
        second = buf.mark_speech_stopped()

        self.assertNotIn(b"\xAA" * 1024, second)
        self.assertEqual(first, b"\xAA" * 1024)
        self.assertEqual(second, b"\xBB" * 1024)

    def test_duplicate_speech_started_does_not_reset_in_progress_capture(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        buf.mark_speech_started()
        buf.append_audio(CHUNK)
        buf.mark_speech_started()  # duplicate/noisy event — must be ignored
        audio = buf.mark_speech_stopped()
        self.assertEqual(audio, CHUNK)

    def test_duplicate_speech_stopped_returns_empty_and_does_not_error(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        buf.mark_speech_started()
        buf.append_audio(CHUNK)
        buf.mark_speech_stopped()
        second = buf.mark_speech_stopped()
        self.assertEqual(second, b"")

    def test_reset_clears_ring_and_capture_state(self) -> None:
        buf = UtteranceBuffer(sample_rate=SAMPLE_RATE, pre_roll_ms=500)
        buf.append_audio(CHUNK)
        buf.mark_speech_started()
        buf.append_audio(CHUNK)
        buf.reset()
        self.assertFalse(buf.is_capturing)
        audio = buf.mark_speech_stopped()
        self.assertEqual(audio, b"")


if __name__ == "__main__":
    unittest.main()
