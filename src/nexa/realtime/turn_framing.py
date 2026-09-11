"""``UtteranceFramer`` — preserves the logical turn envelope (activity
start / audio / activity end) through a provider NOT_READY window,
extending the M2.6B.1 ``InboundAudioBuffer`` (raw PCM only) with the
turn-boundary semantics ADR-0004 Decision H requires in production.

While ``readiness != READY``, NeXa's local turn authority (Silero) may
still see a complete user utterance (start, audio, end) or only part of
one. This buffer preserves that exact ordering so that once the provider
becomes ``READY`` it receives — for every utterance that happened during
the outage — exactly one ``activity_start``, its audio in order, then
exactly one ``activity_end`` (if the end was actually seen locally before
READY; an in-progress turn flushes only what has happened so far, and its
``activity_end`` follows live once the local turn authority reports it).

Bounded by wall-clock age and total audio bytes, same as
``InboundAudioBuffer``. Overflow prefers to drop a whole completed oldest
utterance rather than corrupt a turn boundary; if the buffer is dominated
by one still-open utterance, only oldest AUDIO within that utterance is
dropped — its ``activity_start`` is never dropped once queued, and no
audio is ever delivered without a preceding ``activity_start``.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)

DEFAULT_MAX_BUFFER_SECONDS = 4.0
DEFAULT_MAX_BUFFER_BYTES = 4 * 16_000 * 2


class EnvelopeEventKind(StrEnum):
    ACTIVITY_START = "activity_start"
    AUDIO = "audio"
    ACTIVITY_END = "activity_end"


@dataclass(frozen=True, slots=True)
class EnvelopeEvent:
    sequence: int
    kind: EnvelopeEventKind
    pcm: bytes | None
    captured_at: float


@dataclass
class UtteranceFramerStats:
    events_captured: int = 0
    events_flushed: int = 0
    events_dropped: int = 0
    bytes_dropped: int = 0
    orphan_audio_rejected: int = 0


class UtteranceFramer:
    """Bounded, turn-envelope-preserving capture for the provider
    NOT_READY window."""

    def __init__(
        self,
        *,
        max_seconds: float = DEFAULT_MAX_BUFFER_SECONDS,
        max_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
        now: Callable[[], float] | None = None,
    ) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be > 0")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        self._max_seconds = max_seconds
        self._max_bytes = max_bytes
        self._now = now or time.monotonic
        self._events: deque[EnvelopeEvent] = deque()
        self._audio_bytes = 0
        self._next_sequence = 0
        self._delivered_up_to = -1
        self._turn_open = False
        self.stats = UtteranceFramerStats()

    @property
    def turn_open(self) -> bool:
        return self._turn_open

    def user_turn_start(self) -> None:
        """Idempotent: a second start while one is already open is ignored
        (never a duplicate ``activity_start``)."""
        if self._turn_open:
            return
        self._turn_open = True
        self._append(EnvelopeEventKind.ACTIVITY_START, pcm=None)

    def capture_audio(self, pcm: bytes) -> None:
        """Audio arriving with no open turn is an orphan — reject it rather
        than silently emit audio with no preceding ``activity_start``."""
        if not self._turn_open:
            self.stats.orphan_audio_rejected += 1
            logger.warning(
                "nexa.realtime: rejected audio frame with no open turn "
                "(orphan) — %d bytes discarded",
                len(pcm),
            )
            return
        self._append(EnvelopeEventKind.AUDIO, pcm=pcm)

    def user_turn_end(self) -> None:
        """A stray end with no open turn is ignored (never a bare
        ``activity_end``)."""
        if not self._turn_open:
            return
        self._turn_open = False
        self._append(EnvelopeEventKind.ACTIVITY_END, pcm=None)

    def _append(self, kind: EnvelopeEventKind, *, pcm: bytes | None) -> None:
        seq = self._next_sequence
        self._next_sequence += 1
        event = EnvelopeEvent(sequence=seq, kind=kind, pcm=pcm, captured_at=self._now())
        self._events.append(event)
        if pcm:
            self._audio_bytes += len(pcm)
        self.stats.events_captured += 1
        self._evict_stale()
        self._evict_overflow()

    def _evict_stale(self) -> None:
        cutoff = self._now() - self._max_seconds
        while self._events and self._events[0].captured_at < cutoff:
            self._drop_oldest_completed_utterance_or_audio()

    def _evict_overflow(self) -> None:
        while self._events and self._audio_bytes > self._max_bytes:
            self._drop_oldest_completed_utterance_or_audio()

    def _drop_oldest_completed_utterance_or_audio(self) -> None:
        """Prefer dropping a whole COMPLETED oldest utterance (start..end)
        as one unit. If the front of the queue belongs to a still-open
        utterance (no ACTIVITY_END queued yet for it), drop only its
        oldest AUDIO frame instead — its ACTIVITY_START is never dropped,
        and no audio from a *different*, already-dropped utterance can
        ever be left dangling because eviction always proceeds strictly
        from the front."""
        if not self._events:
            return
        if self._events[0].kind == EnvelopeEventKind.ACTIVITY_START:
            end_index = None
            for i, ev in enumerate(self._events):
                if ev.kind == EnvelopeEventKind.ACTIVITY_END:
                    end_index = i
                    break
            if end_index is not None:
                # a complete oldest utterance — drop it whole.
                for _ in range(end_index + 1):
                    self._drop_one()
                return
            # oldest utterance has no ACTIVITY_END yet (still open, or its
            # end hasn't been captured): drop only its oldest AUDIO frame,
            # never its ACTIVITY_START.
            for i, ev in enumerate(self._events):
                if ev.kind == EnvelopeEventKind.AUDIO:
                    self._drop_at(i)
                    return
            # nothing droppable but the START itself (no audio queued yet) —
            # cannot safely drop without orphaning nothing, so drop it too
            # only as an absolute last resort (an utterance that produced
            # no audio at all before the bound was hit).
            self._drop_one()
            return
        # front is a bare AUDIO/ACTIVITY_END with no preceding START in the
        # queue — should not happen given the invariants above, but never
        # get stuck: drop it.
        self._drop_one()

    def _drop_one(self) -> None:
        dropped = self._events.popleft()
        if dropped.pcm:
            self._audio_bytes -= len(dropped.pcm)
        self.stats.events_dropped += 1
        self.stats.bytes_dropped += len(dropped.pcm or b"")
        logger.warning(
            "nexa.realtime: turn-envelope buffer overflow — dropped %s "
            "seq=%d; %d event(s) / %d byte(s) retained",
            dropped.kind.value,
            dropped.sequence,
            len(self._events),
            self._audio_bytes,
        )

    def _drop_at(self, index: int) -> None:
        # deque has no O(1) arbitrary removal; rebuild (buffers are small
        # and bounded, so this is cheap).
        items = list(self._events)
        dropped = items.pop(index)
        self._events = deque(items)
        if dropped.pcm:
            self._audio_bytes -= len(dropped.pcm)
        self.stats.events_dropped += 1
        self.stats.bytes_dropped += len(dropped.pcm or b"")
        logger.warning(
            "nexa.realtime: turn-envelope buffer overflow — dropped %s "
            "seq=%d (mid-utterance); %d event(s) / %d byte(s) retained",
            dropped.kind.value,
            dropped.sequence,
            len(self._events),
            self._audio_bytes,
        )

    def flush(self) -> list[EnvelopeEvent]:
        """Return not-yet-delivered events, in order, and mark them
        delivered. Idempotent — a repeat call with nothing new captured
        returns ``[]``, so a reconnect can never re-deliver the same
        ``activity_start`` / audio / ``activity_end`` twice."""
        self._evict_stale()
        pending = [e for e in self._events if e.sequence > self._delivered_up_to]
        if pending:
            self._delivered_up_to = pending[-1].sequence
            self.stats.events_flushed += len(pending)
        return pending

    def __len__(self) -> int:
        return len(self._events)

    @property
    def buffered_audio_bytes(self) -> int:
        return self._audio_bytes
