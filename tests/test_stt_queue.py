"""M2.2 `SerialTranscriptionQueue` tests.

Real hardware testing (2026-09-05/06) showed the original per-turn
`create_task` dispatch could run two whisper.cpp subprocesses concurrently
if a short utterance followed quickly — R0006 already established CPU
contention as a real Raspberry Pi constraint. These tests prove, with a
fake slow transcriber, that STT execution is serialized and FIFO-ordered
while audio capture (`submit()`) is never blocked by it.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt import Language, SerialTranscriptionQueue, SttQueueOverflowError  # noqa: E402
from nexa.stt.transcriber import TranscriptionResult  # noqa: E402


class _SlowFakeTranscriber:
    """Tracks concurrent in-flight calls directly (independent of the
    queue's own bookkeeping) so the test does not just re-check the
    queue's internal counter against itself."""

    def __init__(self, delay_s: float = 0.05) -> None:
        self._delay_s = delay_s
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.calls.append(audio)
        try:
            await asyncio.sleep(self._delay_s)
        finally:
            self.in_flight -= 1
        return TranscriptionResult(
            text=audio.decode(), language=language, audio_duration_s=0.1,
            wall_latency_s=self._delay_s,
        )


class TestSerialTranscriptionQueue(unittest.IsolatedAsyncioTestCase):
    async def test_two_quick_utterances_are_transcribed_fifo(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=0.05)
        results: list[TranscriptionResult] = []
        q = SerialTranscriptionQueue(transcriber, on_result=results.append)
        q.start()

        q.submit(b"first", Language.EN)
        q.submit(b"second", Language.EN)
        await q.shutdown()

        self.assertEqual([r.text for r in results], ["first", "second"])

    async def test_only_one_transcriber_call_executes_at_a_time(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=0.05)
        q = SerialTranscriptionQueue(transcriber)
        q.start()

        q.submit(b"a", Language.EN)
        q.submit(b"b", Language.EN)
        q.submit(b"c", Language.EN)
        await q.shutdown()

        self.assertEqual(transcriber.max_in_flight, 1)
        self.assertEqual(q.max_observed_concurrency, 1)

    async def test_submit_does_not_block_while_a_transcription_is_in_flight(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=1.0)
        q = SerialTranscriptionQueue(transcriber)
        q.start()

        q.submit(b"slow", Language.EN)
        t0 = asyncio.get_running_loop().time()
        q.submit(b"queued-while-busy", Language.EN)  # must return immediately
        elapsed = asyncio.get_running_loop().time() - t0

        self.assertLess(elapsed, 0.1, "submit() must not block on an in-flight transcription")
        await q.shutdown()

    async def test_one_utterance_yields_exactly_one_result(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=0.01)
        results: list[TranscriptionResult] = []
        q = SerialTranscriptionQueue(transcriber, on_result=results.append)
        q.start()

        q.submit(b"solo", Language.EN)
        await q.shutdown()

        self.assertEqual(len(results), 1)

    async def test_result_ordering_matches_submission_ordering_under_varying_delay(self) -> None:
        class VaryingDelayTranscriber:
            def __init__(self) -> None:
                self.n = 0

            async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
                self.n += 1
                # first call is slower than the rest — if the queue were not
                # serial/FIFO this would be enough to reorder results.
                await asyncio.sleep(0.08 if self.n == 1 else 0.01)
                return TranscriptionResult(
                    text=audio.decode(), language=language, audio_duration_s=0.1, wall_latency_s=0.0
                )

        results: list[TranscriptionResult] = []
        q = SerialTranscriptionQueue(VaryingDelayTranscriber(), on_result=results.append)
        q.start()

        for label in (b"one", b"two", b"three"):
            q.submit(label, Language.EN)
        await q.shutdown()

        self.assertEqual([r.text for r in results], ["one", "two", "three"])

    async def test_buffers_do_not_leak_between_queued_turns(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=0.01)
        q = SerialTranscriptionQueue(transcriber)
        q.start()

        q.submit(b"\xAA" * 100, Language.EN)
        q.submit(b"\xBB" * 100, Language.EN)
        await q.shutdown()

        self.assertEqual(transcriber.calls, [b"\xAA" * 100, b"\xBB" * 100])
        self.assertNotIn(b"\xAA" * 100 + b"\xBB" * 100, transcriber.calls)

    async def test_shutdown_finishes_in_flight_work_and_leaves_no_orphan_task(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=0.05)
        results: list[TranscriptionResult] = []
        q = SerialTranscriptionQueue(transcriber, on_result=results.append)
        q.start()

        q.submit(b"in-flight-at-shutdown", Language.EN)
        await q.shutdown()

        self.assertEqual(len(results), 1, "shutdown must not drop work already queued")
        self.assertIsNone(q._worker_task)

    async def test_shutdown_without_start_is_a_safe_no_op(self) -> None:
        q = SerialTranscriptionQueue(_SlowFakeTranscriber())
        await q.shutdown()  # must not raise

    async def test_overflow_of_a_bounded_queue_raises_explicit_error(self) -> None:
        transcriber = _SlowFakeTranscriber(delay_s=1.0)
        q = SerialTranscriptionQueue(transcriber, max_queue_size=1)
        q.start()

        q.submit(b"a", Language.EN)  # picked up by the worker almost immediately
        await asyncio.sleep(0.01)
        q.submit(b"b", Language.EN)  # fills the one queue slot
        with self.assertRaises(SttQueueOverflowError):
            q.submit(b"c", Language.EN)

        await q.shutdown()

    async def test_transcription_exception_reaches_error_callback_not_result_callback(self) -> None:
        class FailingTranscriber:
            async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
                raise RuntimeError("boom")

        results: list[TranscriptionResult] = []
        errors: list[Exception] = []
        q = SerialTranscriptionQueue(
            FailingTranscriber(), on_result=results.append, on_error=errors.append
        )
        q.start()

        q.submit(b"x", Language.EN)
        await q.shutdown()

        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    async def test_worker_continues_processing_after_one_utterance_errors(self) -> None:
        class SometimesFailingTranscriber:
            def __init__(self) -> None:
                self.n = 0

            async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("first one fails")
                return TranscriptionResult(
                    text=audio.decode(), language=language, audio_duration_s=0.0, wall_latency_s=0.0
                )

        results: list[TranscriptionResult] = []
        errors: list[Exception] = []
        q = SerialTranscriptionQueue(
            SometimesFailingTranscriber(), on_result=results.append, on_error=errors.append
        )
        q.start()

        q.submit(b"fails", Language.EN)
        q.submit(b"succeeds", Language.EN)
        await q.shutdown()

        self.assertEqual(len(errors), 1)
        self.assertEqual([r.text for r in results], ["succeeds"])


if __name__ == "__main__":
    unittest.main()
