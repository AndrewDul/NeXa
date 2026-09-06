"""M2.4B.1A — measurement-only timing wrapper around Pipecat's
``PiperHttpTTSService``.

``TimedPiperHttpTTSService`` records the **true HTTP synthesis timing** of
each ``run_tts`` call (one per synthesized sentence): request start → whole
WAV returned. R0013's ``TtsSegment`` measured only the Pipecat *audio
context* lifecycle span (``TTSStartedFrame`` → ``TTSStoppedFrame``), which
is inflated by playback drain and the 3 s ``stop_frame_timeout_s`` — so its
"RTF" read ~0.7–0.9 when the real Piper synthesis RTF is ~0.14–0.24 (R0013
"B.1 METRIC CORRECTIONS").

This subclass **changes nothing about the audio**: it iterates
``super().run_tts(...)`` and yields every frame in the same order,
unmodified. It only wraps a stopwatch around that generator and reports the
result via an optional callback. Used only by the probe's ``--report``
mode.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass

from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.services.piper.tts import PiperHttpTTSService

# Pipecat local audio + Piper medium: s16le, 2 bytes/sample/channel.
_BYTES_PER_SAMPLE = 2


@dataclass
class HttpSynthCall:
    """One real ``run_tts`` (one HTTP request to the Piper server)."""

    text_len: int
    http_wall_s: float
    """Request issued → whole WAV received (Piper returns it in one body)."""
    ttfb_s: float | None
    """Request issued → first ``TTSAudioRawFrame`` yielded. For the Piper
    HTTP server (no chunked transfer) this ≈ ``http_wall_s``."""
    audio_bytes: int
    audio_s: float | None
    http_rtf: float | None
    """``http_wall_s / audio_s`` — the TRUE synthesis real-time factor.
    < 1 means faster than real time."""


class TimedPiperHttpTTSService(PiperHttpTTSService):
    """``PiperHttpTTSService`` that times each ``run_tts`` HTTP request.

    Args:
        on_http_call: optional callback, invoked once per completed
            ``run_tts`` with the :class:`HttpSynthCall`. Never raises into
            the pipeline (exceptions in the callback are swallowed).
        **kwargs: forwarded verbatim to ``PiperHttpTTSService``.
    """

    def __init__(
        self, *, on_http_call: Callable[[HttpSynthCall], None] | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._on_http_call = on_http_call
        self.http_calls: list[HttpSynthCall] = []

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        t0 = time.monotonic()
        first_audio_at: float | None = None
        audio_bytes = 0
        try:
            async for frame in super().run_tts(text, context_id):
                if isinstance(frame, TTSAudioRawFrame):
                    if first_audio_at is None:
                        first_audio_at = time.monotonic()
                    audio_bytes += len(frame.audio)
                yield frame  # identical stream, identical order — no change
        finally:
            wall = time.monotonic() - t0
            audio_s = (
                audio_bytes / (16000 * _BYTES_PER_SAMPLE) if audio_bytes else None
            )
            call = HttpSynthCall(
                text_len=len(text),
                http_wall_s=wall,
                ttfb_s=(first_audio_at - t0) if first_audio_at is not None else None,
                audio_bytes=audio_bytes,
                audio_s=audio_s,
                http_rtf=(wall / audio_s) if audio_s else None,
            )
            self.http_calls.append(call)
            if self._on_http_call is not None:
                try:
                    self._on_http_call(call)
                except Exception:
                    pass


__all__ = ["TimedPiperHttpTTSService", "HttpSynthCall"]
