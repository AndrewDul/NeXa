"""``UtteranceFramer`` — preserves the logical turn envelope through a
provider NOT_READY window (ADR-0004 Decision H, M2.6B.2).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.turn_framing import EnvelopeEventKind, UtteranceFramer  # noqa: E402


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _kinds(events) -> list[str]:
    return [e.kind.value for e in events]


class TestCompleteTurnCapturedWhileNotReady(unittest.TestCase):
    def test_full_start_audio_end_sequence_flushed_in_order(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.user_turn_start()
        framer.capture_audio(b"a")
        framer.capture_audio(b"b")
        framer.user_turn_end()

        flushed = framer.flush()
        self.assertEqual(
            _kinds(flushed),
            [
                EnvelopeEventKind.ACTIVITY_START.value,
                EnvelopeEventKind.AUDIO.value,
                EnvelopeEventKind.AUDIO.value,
                EnvelopeEventKind.ACTIVITY_END.value,
            ],
        )
        self.assertEqual([e.pcm for e in flushed if e.pcm], [b"a", b"b"])


class TestStartWhileReadyOutageMidTurnThenReady(unittest.TestCase):
    def test_in_progress_turn_flushes_what_happened_so_far(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.user_turn_start()
        framer.capture_audio(b"a")
        # outage continues — no end yet
        flushed = framer.flush()
        self.assertEqual(
            _kinds(flushed),
            [EnvelopeEventKind.ACTIVITY_START.value, EnvelopeEventKind.AUDIO.value],
        )
        # once truly READY, the caller sends the end live (not through the
        # framer) — but if it still comes through the framer (e.g. one more
        # NOT_READY blip), it must not duplicate the start.
        framer.capture_audio(b"b")
        framer.user_turn_end()
        flushed2 = framer.flush()
        self.assertEqual(
            _kinds(flushed2),
            [EnvelopeEventKind.AUDIO.value, EnvelopeEventKind.ACTIVITY_END.value],
        )


class TestReconnectBetweenStartAndEnd(unittest.TestCase):
    def test_two_utterances_across_a_reconnect_never_duplicate(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.user_turn_start()
        framer.capture_audio(b"u1-a")
        framer.user_turn_end()
        first = framer.flush()

        second_call = framer.flush()  # nothing new
        self.assertEqual(second_call, [])

        framer.user_turn_start()
        framer.capture_audio(b"u2-a")
        framer.user_turn_end()
        second = framer.flush()

        all_pcm = [e.pcm for e in first + second if e.pcm]
        self.assertEqual(all_pcm, [b"u1-a", b"u2-a"])
        self.assertEqual(len(all_pcm), len(set(all_pcm)))


class TestOverflowWithinATurn(unittest.TestCase):
    def test_overflow_drops_audio_never_the_activity_start(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(max_seconds=100.0, max_bytes=6, now=clock)
        framer.user_turn_start()  # 0 bytes
        framer.capture_audio(b"aaaaaa")  # 6 bytes — at the bound
        framer.capture_audio(b"b")  # overflow -> drop oldest AUDIO, not START
        self.assertGreater(framer.stats.events_dropped, 0)
        flushed = framer.flush()
        kinds = _kinds(flushed)
        self.assertEqual(kinds[0], EnvelopeEventKind.ACTIVITY_START.value)  # never dropped
        self.assertNotIn(b"aaaaaa", [e.pcm for e in flushed])  # oldest audio dropped
        self.assertIn(b"b", [e.pcm for e in flushed])  # freshest kept

    def test_overflow_drops_whole_completed_oldest_utterance_first(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(max_seconds=100.0, max_bytes=4, now=clock)
        framer.user_turn_start()
        framer.capture_audio(b"aaaa")  # 4 bytes, completed utterance below
        framer.user_turn_end()
        framer.user_turn_start()
        framer.capture_audio(b"bbbb")  # overflow: drop the WHOLE first utterance
        flushed = framer.flush()
        pcm_seen = [e.pcm for e in flushed if e.pcm]
        self.assertNotIn(b"aaaa", pcm_seen)
        self.assertIn(b"bbbb", pcm_seen)
        # the retained utterance still starts with its own ACTIVITY_START
        self.assertEqual(flushed[0].kind, EnvelopeEventKind.ACTIVITY_START)


class TestTwoConsecutiveTurnsDuringReconnect(unittest.TestCase):
    def test_two_full_turns_flushed_once_each_no_duplication(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.user_turn_start()
        framer.capture_audio(b"t1")
        framer.user_turn_end()
        framer.user_turn_start()
        framer.capture_audio(b"t2")
        framer.user_turn_end()

        flushed = framer.flush()
        self.assertEqual(
            _kinds(flushed),
            [
                EnvelopeEventKind.ACTIVITY_START.value,
                EnvelopeEventKind.AUDIO.value,
                EnvelopeEventKind.ACTIVITY_END.value,
                EnvelopeEventKind.ACTIVITY_START.value,
                EnvelopeEventKind.AUDIO.value,
                EnvelopeEventKind.ACTIVITY_END.value,
            ],
        )
        self.assertEqual(framer.flush(), [])  # no duplication on a second flush


class TestExactlyOnceDelivery(unittest.TestCase):
    def test_idempotent_start_end(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.user_turn_start()
        framer.user_turn_start()  # duplicate — ignored
        framer.user_turn_end()
        framer.user_turn_end()  # duplicate — ignored
        flushed = framer.flush()
        self.assertEqual(
            _kinds(flushed),
            [EnvelopeEventKind.ACTIVITY_START.value, EnvelopeEventKind.ACTIVITY_END.value],
        )

    def test_orphan_audio_with_no_open_turn_is_rejected_not_emitted(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.capture_audio(b"orphan")  # no user_turn_start() first
        self.assertEqual(framer.stats.orphan_audio_rejected, 1)
        self.assertEqual(framer.flush(), [])

    def test_stray_end_with_no_open_turn_is_ignored(self) -> None:
        clock = _FakeClock()
        framer = UtteranceFramer(now=clock)
        framer.user_turn_end()  # no matching start
        self.assertEqual(framer.flush(), [])


class TestConstruction(unittest.TestCase):
    def test_rejects_non_positive_bounds(self) -> None:
        with self.assertRaises(ValueError):
            UtteranceFramer(max_seconds=0)
        with self.assertRaises(ValueError):
            UtteranceFramer(max_bytes=0)


if __name__ == "__main__":
    unittest.main()
