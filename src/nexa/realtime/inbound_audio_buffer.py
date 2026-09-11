"""NeXa-owned inbound audio buffer — the production protection for Pipecat
issue #5465 (``GeminiLiveLLMService`` silently drops user audio / text /
tool-results during the reconnect / not-ready window) (ADR-0004
Decision H).

Lives at the ``RealtimeVoiceProvider`` boundary, not as a patch to Pipecat
internals (issue #5465 is OPEN, its fix PR #5497 is unmerged, as of
2026-09-10 — see ADR-0004 Context / R0030 / R0031). While the active
provider is not ``READY``, incoming (already AEC-processed) mic PCM is
captured here instead of being sent — bounded by wall-clock age AND total
bytes — and flushed in order once ``READY``. Overflow drops the OLDEST
frame (preserves the freshest audio) and is always counted and logged,
never silent. Each frame carries a monotonic sequence id; ``flush`` tracks
what has actually been handed to the provider so a later reconnect never
re-delivers already-delivered audio (no unbounded replay, no duplicated
utterance).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: ADR-0004 Decision H defaults — "bounded by wall-time (~3-5s) and bytes".
DEFAULT_MAX_BUFFER_SECONDS = 4.0
#: ~4s of 16kHz, 16-bit, mono PCM (the M2.6A/ADR-0004 mic format).
DEFAULT_MAX_BUFFER_BYTES = 4 * 16_000 * 2


@dataclass(frozen=True, slots=True)
class BufferedAudioFrame:
    sequence: int
    pcm: bytes
    captured_at: float


@dataclass
class InboundAudioBufferStats:
    """Telemetry (ADR-0004 Decision H) — never silent."""

    buffered_frames: int = 0
    flushed_frames: int = 0
    dropped_frames: int = 0
    dropped_bytes: int = 0


class InboundAudioBuffer:
    """Bounded FIFO capture for mic audio while the provider is not READY.

    ``now`` is injected (defaults to ``time.monotonic``) so behaviour is
    fully deterministic in tests.
    """

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
        self._frames: deque[BufferedAudioFrame] = deque()
        self._total_bytes = 0
        self._next_sequence = 0
        #: highest sequence already handed out by ``flush`` — never
        #: re-delivered (protects against duplication across a reconnect
        #: that reuses the same buffer instance).
        self._delivered_up_to = -1
        self.stats = InboundAudioBufferStats()

    def capture(self, pcm: bytes) -> BufferedAudioFrame:
        """Record one frame of mic PCM while not READY."""
        seq = self._next_sequence
        self._next_sequence += 1
        frame = BufferedAudioFrame(sequence=seq, pcm=pcm, captured_at=self._now())
        self._frames.append(frame)
        self._total_bytes += len(pcm)
        self.stats.buffered_frames += 1
        self._evict_stale()
        self._evict_overflow()
        return frame

    def _evict_stale(self) -> None:
        cutoff = self._now() - self._max_seconds
        while self._frames and self._frames[0].captured_at < cutoff:
            self._drop_oldest()

    def _evict_overflow(self) -> None:
        while self._frames and self._total_bytes > self._max_bytes:
            self._drop_oldest()

    def _drop_oldest(self) -> None:
        dropped = self._frames.popleft()
        self._total_bytes -= len(dropped.pcm)
        self.stats.dropped_frames += 1
        self.stats.dropped_bytes += len(dropped.pcm)
        logger.warning(
            "nexa.realtime: inbound audio buffer overflow — dropped frame "
            "seq=%d (%d bytes); %d frame(s) / %d byte(s) retained",
            dropped.sequence,
            len(dropped.pcm),
            len(self._frames),
            self._total_bytes,
        )

    def flush(self) -> list[BufferedAudioFrame]:
        """Return buffered frames not already delivered, in order, and mark
        them delivered. Call once on the ``READY`` transition. Idempotent:
        calling again with nothing newly captured returns ``[]`` — safe to
        call again after a reconnect without re-delivering anything."""
        self._evict_stale()
        pending = [f for f in self._frames if f.sequence > self._delivered_up_to]
        if pending:
            self._delivered_up_to = pending[-1].sequence
            self.stats.flushed_frames += len(pending)
        return pending

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def buffered_bytes(self) -> int:
        return self._total_bytes
