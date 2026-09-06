"""M2.2 `_UtteranceCaptureFrameProcessor` tests.

Exercises the real processor class directly against real Pipecat frame
types (no running pipeline/TaskManager needed — `push_frame` on an unlinked
processor is a documented no-op). The processor's internal
`SerialTranscriptionQueue` is started explicitly (bypassing Pipecat's
`setup()` handshake, which needs a real `FrameProcessorSetup`/task manager)
and drained via `shutdown()` at the end of each test, so results are
deterministic without sleeps. FIFO-ordering/concurrency guarantees of the
queue itself are covered separately in `test_stt_queue.py`.
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

from pipecat.frames.frames import (  # noqa: E402
    CancelFrame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from nexa.stt import Language, TranscriptionResult  # noqa: E402
from nexa.voice.runtime import _UtteranceCaptureFrameProcessor  # noqa: E402

CHUNK = b"\x00\x01" * 512


class _FakeTranscriber:
    def __init__(self, *, text: str = "hello", error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls: list[tuple[bytes, Language]] = []

    async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
        self.calls.append((audio, language))
        if self._error is not None:
            raise self._error
        return TranscriptionResult(
            text=self._text, language=language, audio_duration_s=0.1, wall_latency_s=0.01
        )


def _make_processor(transcriber, *, on_transcription=None, on_transcription_error=None):
    proc = _UtteranceCaptureFrameProcessor(
        sample_rate=16_000,
        transcriber=transcriber,
        language=Language.EN,
        on_transcription=on_transcription,
        on_transcription_error=on_transcription_error,
    )
    proc._queue.start(asyncio.ensure_future)
    return proc


async def _push(proc: _UtteranceCaptureFrameProcessor, frame) -> None:
    await proc.process_frame(frame, FrameDirection.DOWNSTREAM)


async def _push_audio(proc: _UtteranceCaptureFrameProcessor, chunk: bytes = CHUNK) -> None:
    await _push(proc, InputAudioRawFrame(audio=chunk, sample_rate=16_000, num_channels=1))


async def _push_speech_started(proc: _UtteranceCaptureFrameProcessor) -> None:
    await _push(proc, VADUserStartedSpeakingFrame(start_secs=0.2))


async def _push_speech_stopped(proc: _UtteranceCaptureFrameProcessor) -> None:
    await _push(proc, VADUserStoppedSpeakingFrame(stop_secs=1.0))


class TestUtteranceCaptureFrameProcessor(unittest.IsolatedAsyncioTestCase):
    async def test_end_of_turn_dispatches_transcription_with_captured_audio(self) -> None:
        transcriber = _FakeTranscriber(text="Cześć NeXa.")
        results: list[TranscriptionResult] = []
        proc = _make_processor(transcriber, on_transcription=results.append)

        await _push_audio(proc)
        await _push_speech_started(proc)
        await _push_audio(proc)
        await _push_speech_stopped(proc)
        await proc._queue.shutdown()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "Cześć NeXa.")
        self.assertEqual(transcriber.calls[0][1], Language.EN)
        self.assertGreaterEqual(len(transcriber.calls[0][0]), len(CHUNK))
        self.assertLessEqual(proc.max_observed_stt_concurrency, 1)

    async def test_transcription_error_reaches_error_callback_not_swallowed(self) -> None:
        transcriber = _FakeTranscriber(error=RuntimeError("boom"))
        errors: list[Exception] = []
        results: list[TranscriptionResult] = []
        proc = _make_processor(
            transcriber, on_transcription=results.append, on_transcription_error=errors.append
        )

        await _push_speech_started(proc)
        await _push_audio(proc)
        await _push_speech_stopped(proc)
        await proc._queue.shutdown()

        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    async def test_buffer_resets_between_turns_no_leak(self) -> None:
        transcriber = _FakeTranscriber()
        results: list[TranscriptionResult] = []
        proc = _make_processor(transcriber, on_transcription=results.append)

        for _ in range(2):
            await _push_speech_started(proc)
            await _push_audio(proc)
            await _push_speech_stopped(proc)
        await proc._queue.shutdown()

        self.assertEqual(len(transcriber.calls), 2)
        self.assertEqual(transcriber.calls[0][0], transcriber.calls[1][0])
        self.assertFalse(proc._buffer.is_capturing)
        self.assertLessEqual(proc.max_observed_stt_concurrency, 1)

    async def test_cancel_frame_resets_buffer_without_transcribing(self) -> None:
        transcriber = _FakeTranscriber()
        proc = _make_processor(transcriber)

        await _push_speech_started(proc)
        await _push_audio(proc)
        await _push(proc, CancelFrame())
        await proc._queue.shutdown()

        self.assertFalse(proc._buffer.is_capturing)
        self.assertEqual(transcriber.calls, [])

    async def test_no_speech_no_transcription_is_dispatched(self) -> None:
        transcriber = _FakeTranscriber()
        proc = _make_processor(transcriber)

        await _push_audio(proc)
        await proc._queue.shutdown()

        self.assertEqual(transcriber.calls, [])


if __name__ == "__main__":
    unittest.main()
