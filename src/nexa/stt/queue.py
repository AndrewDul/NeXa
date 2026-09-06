"""Serializes whisper.cpp STT execution across utterances (M2.2 fix).

R0006 established CPU contention as a major Raspberry Pi constraint. The
first M2.2 cut dispatched one independent background task per completed
utterance — real hardware testing (2026-09-05/06) showed a short next
utterance can reach END_OF_TURN before the previous whisper.cpp subprocess
finishes, which could launch two concurrent 4-thread whisper.cpp processes.

`SerialTranscriptionQueue` fixes this: audio/VAD capture (owned by
``UtteranceBuffer``/the caller) is completely independent of this queue and
never blocks on it — only STT *execution* is serialized, one utterance at a
time, strictly in FIFO order. Pure asyncio, no Pipecat dependency, so it is
directly unit testable with a fake `SpeechTranscriber`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING

from .config import Language
from .errors import SttQueueOverflowError

if TYPE_CHECKING:
    from .transcriber import SpeechTranscriber, TranscriptionResult

# Generous relative to realistic single-user speech pacing (each utterance
# takes seconds to speak and ~1.5-2s to transcribe) — this is headroom
# against a burst, not a normal operating depth. Explicit and bounded so a
# genuine runaway (e.g. a stuck worker) fails loudly via
# `SttQueueOverflowError` instead of growing memory silently forever.
DEFAULT_MAX_QUEUE_SIZE = 8

_SHUTDOWN = object()  # sentinel, distinct from any real (audio, language) item


class SerialTranscriptionQueue:
    """At most one `transcriber.transcribe()` call in flight at a time,
    processed strictly in the order utterances were submitted."""

    def __init__(
        self,
        transcriber: SpeechTranscriber,
        *,
        on_result: Callable[[TranscriptionResult], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        self._transcriber = transcriber
        self._on_result = on_result
        self._on_error = on_error
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: asyncio.Task | None = None
        self._in_flight = 0
        self._max_observed_in_flight = 0

    @property
    def max_observed_concurrency(self) -> int:
        """Peak number of simultaneous `transcribe()` calls ever observed.
        Must never exceed 1 — proof, not inference, that STT is serialized."""
        return self._max_observed_in_flight

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def start(self, task_factory: Callable[[Coroutine], asyncio.Task] | None = None) -> None:
        """Start the single background worker. Idempotent. `task_factory`
        lets a Pipecat `FrameProcessor` pass its own `self.create_task` so
        the worker is tracked/cancellable by the pipeline's task manager;
        defaults to a plain `asyncio.ensure_future` for standalone use."""
        if self._worker_task is not None:
            return
        factory = task_factory or asyncio.ensure_future
        self._worker_task = factory(self._run())

    def submit(self, audio: bytes, language: Language) -> None:
        """Enqueue one completed utterance for transcription. Synchronous
        and non-blocking — never waits on STT, so audio capture is never
        slowed down by this call. Raises `SttQueueOverflowError` immediately
        if the bounded queue is full; an utterance is never dropped
        silently."""
        try:
            self._queue.put_nowait((audio, language))
        except asyncio.QueueFull as exc:
            raise SttQueueOverflowError(
                f"STT queue is full ({self._queue.maxsize} utterances pending) — "
                f"whisper.cpp is falling behind real-time speech"
            ) from exc

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _SHUTDOWN:
                self._queue.task_done()
                return
            audio, language = item
            self._in_flight += 1
            self._max_observed_in_flight = max(self._max_observed_in_flight, self._in_flight)
            try:
                result = await self._transcriber.transcribe(audio, language=language)
            except Exception as exc:  # STT failures must reach the caller explicitly
                if self._on_error is not None:
                    self._on_error(exc)
            else:
                if self._on_result is not None:
                    self._on_result(result)
            finally:
                self._in_flight -= 1
                self._queue.task_done()

    async def shutdown(self) -> None:
        """Stop accepting further work and wait for the worker to finish
        whatever it is currently transcribing (does not drop it), then
        exit. Idempotent; a no-op if `start()` was never called."""
        if self._worker_task is None:
            return
        await self._queue.put(_SHUTDOWN)
        await self._worker_task
        self._worker_task = None
