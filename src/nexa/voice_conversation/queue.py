"""Serializes conversation turns arriving from voice input (M2.3).

Mirrors `nexa.stt.queue.SerialTranscriptionQueue`'s design one layer up:
STT results can keep arriving (audio/VAD/STT continue independently), but
only one `ConversationSession.send()` call may be in flight at a time —
concurrent turns against the same session could corrupt turn ordering,
history, and streamed-output ordering. Pure asyncio; this module has no
`ConversationSession` import — it only knows "process one item, await it"
(the handler passed in owns everything conversation-shaped). The item is
opaque: M2.3 submitted a plain transcript string; M2.4B.5 submits a small
turn record carrying the transcript plus its resolved language info.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from loguru import logger

# LLM turns are far slower and more expensive than STT calls (R0005 measured
# 27.6-54s for a full worst-case voice turn) — a much smaller backlog than
# nexa.stt's queue (8) is realistic headroom for a single-user conversational
# pace: nobody speaks 4+ utterances back-to-back while each one takes tens of
# seconds to answer.
DEFAULT_MAX_QUEUE_SIZE = 4

_SHUTDOWN = object()  # sentinel, distinct from any real submitted text


class ConversationQueueOverflowError(RuntimeError):
    """`SerialConversationQueue`'s bounded FIFO was full when a new turn was
    submitted. Raised explicitly — a transcript is never dropped silently."""


class SerialConversationQueue:
    """At most one conversation-turn handler call in flight at a time,
    processed strictly in the order turns were submitted."""

    def __init__(
        self,
        handler: Callable[[Any], Awaitable[None]],
        *,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        self._handler = handler
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: asyncio.Task | None = None
        self._in_flight = 0
        self._max_observed_in_flight = 0

    @property
    def max_observed_concurrency(self) -> int:
        """Peak number of simultaneous conversation-turn handler calls ever
        observed. Must never exceed 1 — proof, not inference, that
        conversation turns are serialized."""
        return self._max_observed_in_flight

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def in_flight(self) -> int:
        """1 while an item is currently being processed, else 0 (M2.5B.1 —
        queue_size alone hides in-flight work)."""
        return self._in_flight

    def start(self, task_factory: Callable[[Coroutine], asyncio.Task] | None = None) -> None:
        """Start the single background worker. Idempotent. `task_factory`
        defaults to a plain `asyncio.ensure_future`."""
        if self._worker_task is not None:
            return
        factory = task_factory or asyncio.ensure_future
        self._worker_task = factory(self._run())

    def submit(self, item: Any) -> None:
        """Enqueue one user turn (a transcript string, or a turn record).
        Synchronous and non-blocking — never waits on the LLM, so voice/STT
        capture is never slowed down by this call. Raises
        `ConversationQueueOverflowError` immediately if the bounded queue is
        full; a turn is never dropped silently."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise ConversationQueueOverflowError(
                f"conversation queue is full ({self._queue.maxsize} turns pending) — "
                f"the model is falling behind spoken input"
            ) from exc

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _SHUTDOWN:
                self._queue.task_done()
                return
            self._in_flight += 1
            self._max_observed_in_flight = max(self._max_observed_in_flight, self._in_flight)
            try:
                await self._handler(item)
            except Exception:
                # The handler (VoiceConversationAdapter._run_turn) already
                # catches and reports its own errors — this is a last-resort
                # guard so one truly unexpected exception can never kill the
                # worker loop and silently stop processing later turns.
                logger.exception("nexa.voice_conversation: unexpected error processing a turn")
            finally:
                self._in_flight -= 1
                self._queue.task_done()

    async def shutdown(self) -> None:
        """Stop accepting further work and wait for the worker to finish
        whatever it is currently processing (does not drop it), then exit.
        Idempotent; a no-op if `start()` was never called."""
        if self._worker_task is None:
            return
        await self._queue.put(_SHUTDOWN)
        await self._worker_task
        self._worker_task = None
