"""Pure audio helpers for the M2.5A interruption-latency spike.

No nexa / pipecat / aiohttp imports — just numpy + stdlib wave — so the
maths is unit-testable offline (``tests/test_bargein_spike.py``).
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_wav_i16(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV as a mono int16 array + sample rate. A
    multi-channel file is downmixed by averaging."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        sr = w.getframerate()
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype("<i2")
    return data, sr


def write_wav_i16(path: Path, data: np.ndarray, sr: int) -> None:
    """Write a mono int16 array as a 16-bit PCM WAV."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(data, dtype="<i2").tobytes())


def mix_overlay(
    base: np.ndarray,
    overlay: np.ndarray,
    sr: int,
    start_s: float,
    gain: float = 1.0,
) -> np.ndarray:
    """Add ``overlay`` into ``base`` (both mono int16, same ``sr``) starting
    at ``start_s`` seconds.

    The result is extended if the overlay runs past the end of ``base`` and
    is clipped to the int16 range. Summation is done in int32/float64 so a
    loud overlay cannot wrap around. Pure — no I/O.
    """
    if start_s < 0:
        raise ValueError("start_s must be >= 0")
    start = int(round(start_s * sr))
    end = start + overlay.size
    out_len = max(base.size, end)
    acc = np.zeros(out_len, dtype=np.int32)
    acc[: base.size] += base.astype(np.int32)
    acc[start:end] += np.rint(overlay.astype(np.float64) * gain).astype(np.int32)
    return np.clip(acc, -32768, 32767).astype("<i2")
