"""M2.6B.4K / R0050 — byte/sample-exact WAV alignment + PCM energy helpers.

DIAGNOSTIC ONLY. Pure Python, no Pipecat, no Gemini, no audio hardware —
used to forensically compare a ``raw_with_context.wav`` /
``production_forwarded.wav`` pair the R0048 ingress-parity probe
(``m2_6b4i_audio_ingress_parity_probe.py``) already produced. Never
invents a speech-onset detector: ``rms_windows`` reports plain PCM energy
per fixed-size window only — any interpretation of "is this speech" is
left to the report text (and ultimately the operator's own ears), never
to an arbitrary threshold baked into this module.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Alignment:
    """Result of locating ``forwarded`` as an exact byte subsequence of
    ``raw``. ``found=False`` means ``forwarded`` is NOT a contiguous,
    byte-identical subsequence of ``raw`` at all — a real divergence,
    never silently approximated."""

    found: bool
    prefix_bytes: int | None
    suffix_bytes: int | None

    def prefix_ms(
        self, *, sample_rate: int, sample_width_bytes: int = 2, channels: int = 1
    ) -> float | None:
        if self.prefix_bytes is None:
            return None
        bytes_per_ms = sample_rate * sample_width_bytes * channels / 1000.0
        return self.prefix_bytes / bytes_per_ms

    def suffix_ms(
        self, *, sample_rate: int, sample_width_bytes: int = 2, channels: int = 1
    ) -> float | None:
        if self.suffix_bytes is None:
            return None
        bytes_per_ms = sample_rate * sample_width_bytes * channels / 1000.0
        return self.suffix_bytes / bytes_per_ms


def find_alignment(raw: bytes, forwarded: bytes) -> Alignment:
    """Locate ``forwarded`` as an exact byte subsequence of ``raw``.

    Never fuzzy: uses ``bytes.find`` (exact substring match) only. If
    ``forwarded`` is empty, treats it as trivially aligned at offset 0
    (an empty forwarded capture has no meaningful prefix/suffix split).
    """
    if not forwarded:
        return Alignment(found=True, prefix_bytes=0, suffix_bytes=len(raw))
    idx = raw.find(forwarded)
    if idx == -1:
        return Alignment(found=False, prefix_bytes=None, suffix_bytes=None)
    return Alignment(found=True, prefix_bytes=idx, suffix_bytes=len(raw) - (idx + len(forwarded)))


def rms_windows(
    pcm: bytes, *, sample_rate: int, window_ms: int = 20, sample_width_bytes: int = 2
) -> list[float]:
    """Split ``pcm`` (signed 16-bit mono PCM) into consecutive
    ``window_ms``-long windows and return each window's RMS (root mean
    square amplitude) — plain PCM energy, nothing else. The final,
    possibly-short trailing window (if ``pcm``'s length isn't an exact
    multiple of the window size) is still included, computed over
    whatever samples it actually has."""
    if sample_width_bytes != 2:
        raise ValueError("only 16-bit PCM is supported")
    window_bytes = int(sample_rate * (window_ms / 1000.0) * sample_width_bytes)
    if window_bytes <= 0:
        raise ValueError("window_ms too small for this sample_rate")
    out: list[float] = []
    for start in range(0, len(pcm), window_bytes):
        chunk = pcm[start : start + window_bytes]
        out.append(_rms(chunk))
    return out


def _rms(pcm_chunk: bytes) -> float:
    n = len(pcm_chunk) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_chunk[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5
