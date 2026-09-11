"""NeXa-owned inbound audio buffer — production protection for Pipecat
issue #5465 (ADR-0004 Decision H).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.inbound_audio_buffer import InboundAudioBuffer  # noqa: E402


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestInboundAudioBufferConstruction(unittest.TestCase):
    def test_rejects_non_positive_bounds(self) -> None:
        with self.assertRaises(ValueError):
            InboundAudioBuffer(max_seconds=0)
        with self.assertRaises(ValueError):
            InboundAudioBuffer(max_bytes=0)


class TestStartupNotReady(unittest.TestCase):
    def test_frames_captured_while_not_ready_are_all_flushed_in_order(self) -> None:
        clock = _FakeClock()
        buf = InboundAudioBuffer(now=clock)
        buf.capture(b"frame-0")
        clock.advance(0.05)
        buf.capture(b"frame-1")
        clock.advance(0.05)
        buf.capture(b"frame-2")

        flushed = buf.flush()  # the READY transition

        self.assertEqual([f.pcm for f in flushed], [b"frame-0", b"frame-1", b"frame-2"])
        self.assertEqual(buf.stats.flushed_frames, 3)
        self.assertEqual(buf.stats.dropped_frames, 0)


class TestOverflow(unittest.TestCase):
    def test_byte_overflow_drops_oldest_and_preserves_freshest(self) -> None:
        clock = _FakeClock()
        buf = InboundAudioBuffer(max_seconds=100.0, max_bytes=10, now=clock)
        buf.capture(b"12345")  # 5 bytes
        buf.capture(b"12345")  # 5 bytes -> total 10, at the limit, no drop
        self.assertEqual(len(buf), 2)
        buf.capture(b"1")  # 1 more byte -> overflow -> drop oldest
        self.assertEqual(len(buf), 2)  # one dropped, one just added, one remained
        self.assertEqual(buf.stats.dropped_frames, 1)
        self.assertGreater(buf.stats.dropped_bytes, 0)
        remaining = [f.pcm for f in buf.flush()]
        # freshest frames preserved, oldest ("12345" #1) gone
        self.assertEqual(remaining, [b"12345", b"1"])

    def test_time_overflow_evicts_stale_frames(self) -> None:
        clock = _FakeClock()
        buf = InboundAudioBuffer(max_seconds=1.0, max_bytes=10_000, now=clock)
        buf.capture(b"stale")
        clock.advance(1.5)  # older than max_seconds
        buf.capture(b"fresh")
        flushed = buf.flush()
        self.assertEqual([f.pcm for f in flushed], [b"fresh"])
        self.assertGreaterEqual(buf.stats.dropped_frames, 1)

    def test_overflow_is_never_silent(self) -> None:
        clock = _FakeClock()
        buf = InboundAudioBuffer(max_seconds=100.0, max_bytes=1, now=clock)
        with self.assertLogs("nexa.realtime.inbound_audio_buffer", level="WARNING") as cm:
            buf.capture(b"ab")
            buf.capture(b"cd")
        self.assertTrue(any("overflow" in message for message in cm.output))
        self.assertGreater(buf.stats.dropped_frames, 0)


class TestNoUnboundedGrowth(unittest.TestCase):
    def test_buffered_bytes_never_exceeds_max_bytes(self) -> None:
        clock = _FakeClock()
        buf = InboundAudioBuffer(max_seconds=100.0, max_bytes=100, now=clock)
        for _ in range(1000):
            buf.capture(b"x" * 10)
        self.assertLessEqual(buf.buffered_bytes, 100)
        self.assertLessEqual(len(buf), 10)


class TestRepeatedReadyNotReadyAndReconnect(unittest.TestCase):
    def test_flush_never_redelivers_already_delivered_frames(self) -> None:
        clock = _FakeClock()
        buf = InboundAudioBuffer(now=clock)
        buf.capture(b"a")
        buf.capture(b"b")
        first = buf.flush()
        self.assertEqual([f.pcm for f in first], [b"a", b"b"])

        second = buf.flush()  # nothing new captured
        self.assertEqual(second, [])

        buf.capture(b"c")  # new NOT_READY window (e.g. after a reconnect)
        third = buf.flush()
        self.assertEqual([f.pcm for f in third], [b"c"])  # only the new one

    def test_reconnect_during_flush_never_duplicates(self) -> None:
        """Simulates: flush at READY, more audio arrives, a reconnect drops
        back to NOT_READY, more audio buffers, then flush again on the new
        READY — no frame is ever delivered twice."""
        clock = _FakeClock()
        buf = InboundAudioBuffer(now=clock)
        buf.capture(b"pre-reconnect-1")
        buf.capture(b"pre-reconnect-2")
        delivered_1 = buf.flush()

        buf.capture(b"during-reconnect")
        delivered_2 = buf.flush()

        all_delivered = [f.pcm for f in delivered_1] + [f.pcm for f in delivered_2]
        self.assertEqual(
            all_delivered, [b"pre-reconnect-1", b"pre-reconnect-2", b"during-reconnect"]
        )
        self.assertEqual(len(all_delivered), len(set(all_delivered)))  # no duplicates


if __name__ == "__main__":
    unittest.main()
