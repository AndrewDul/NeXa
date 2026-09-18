"""R0082 -- self-contained, standard-library-only diagnostic primitives.

This module exists to remove an accidental runtime dependency found while
attempting the FIRST R0082-B hardware run: the R0082 PoC previously
imported ``build_signal`` from ``r0081_direct_aec_diagnostic.py``, which
itself imports ``nexa.voice.aec_gain`` at module scope -- and importing
``nexa.voice`` (the package's own ``__init__.py``) transitively loads
``nexa.voice.bargein``, which imports ``loguru``. ``loguru`` (and the rest
of NeXa's production runtime dependency surface) is not, and must not be,
installed into R0082's isolated probe venv -- that would defeat the whole
point of R0082 testing the LiveKit/WebRTC audio path in isolation from
NeXa Core, Pipecat, Gemini, Silero, and barge-in.

**Every function below is a verbatim copy of an existing R0081/R005x
diagnostic algorithm, not a redesign.** Provenance is noted per function.
Only Python standard library modules are used here (``array``, ``math``,
``struct``, ``wave``, ``pathlib``) -- deliberately no ``numpy``, no
``nexa.*``, no ``pipecat.*``, no ``google.*``, no ``loguru``.
"""

from __future__ import annotations

import array
import math
import struct
import wave
from pathlib import Path

#: Copied from ``docs/research/m2_6_cloud_realtime_voice/
#: r0081_direct_aec_diagnostic.py``'s own ``SAMPLE_RATE`` module constant
#: (there = 16000, R0081's own default). R0082's own PoC script always
#: passes ``sample_rate`` explicitly (48000, ``MediaDevices``'s own
#: default), so this default is only a fallback, kept for drop-in call
#: compatibility with the original ``build_test_signal``/``build_mls_signal``
#: signatures.
DEFAULT_SAMPLE_RATE = 16000


def build_test_signal(
    *, duration_s: float = 3.0, sample_rate: int = DEFAULT_SAMPLE_RATE, amplitude: float = 0.5
) -> bytes:
    """Verbatim copy of ``r0081_direct_aec_diagnostic.build_test_signal``
    (three pure tones -- 500Hz, 1000Hz, 2000Hz -- in sequence, each with a
    linear 20ms fade-in/out). Algorithm and amplitude semantics unchanged
    so the R0082 stimulus remains directly comparable to R0081's own."""
    tones_hz = (500.0, 1000.0, 2000.0)
    n_total = int(duration_s * sample_rate)
    n_per_tone = n_total // len(tones_hz)
    fade_n = max(1, int(0.02 * sample_rate))  # 20ms fade
    samples = array.array("h")
    for tone_hz in tones_hz:
        for i in range(n_per_tone):
            t = i / sample_rate
            amp = amplitude
            if i < fade_n:
                amp *= i / fade_n
            elif i > n_per_tone - fade_n:
                amp *= (n_per_tone - i) / fade_n
            value = int(amp * 32767 * math.sin(2 * math.pi * tone_hz * t))
            samples.append(value)
    return samples.tobytes()


#: Copied from ``r0081_direct_aec_diagnostic.py`` -- 16-bit maximal-length
#: Fibonacci LFSR (period 2**16-1 = 65535 chips), taps 16/14/13/11, fixed
#: non-zero seed for full run-to-run reproducibility.
_MLS16_SEED = 0xACE1
_MLS16_PERIOD = 65535  # 2**16 - 1


def _mls16_bits(n_bits: int) -> list[int]:
    """Verbatim copy of ``r0081_direct_aec_diagnostic._mls16_bits``."""
    if n_bits > _MLS16_PERIOD:
        raise ValueError(
            f"n_bits={n_bits} exceeds the MLS period ({_MLS16_PERIOD}) -- a single "
            "capture window would wrap around and reintroduce periodicity, defeating "
            "the point of this stimulus. Lower --duration (max ~4.09s at 16kHz)."
        )
    lfsr = _MLS16_SEED
    bits = []
    for _ in range(n_bits):
        bits.append(lfsr & 1)
        fb = ((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5)) & 1
        lfsr = (lfsr >> 1) | (fb << 15)
    return bits


def build_mls_signal(
    *, duration_s: float = 3.0, sample_rate: int = DEFAULT_SAMPLE_RATE, amplitude: float = 0.5
) -> bytes:
    """Verbatim copy of ``r0081_direct_aec_diagnostic.build_mls_signal``."""
    n_total = int(duration_s * sample_rate)
    bits = _mls16_bits(n_total)
    fade_n = max(1, int(0.005 * sample_rate))
    samples = array.array("h")
    for i, bit in enumerate(bits):
        amp = amplitude
        if i < fade_n:
            amp *= i / fade_n
        elif i > n_total - fade_n:
            amp *= (n_total - i) / fade_n
        value = int(amp * 32767 * (1 if bit else -1))
        samples.append(value)
    return samples.tobytes()


#: R0082-C (this round) -- the frequencies mixed by
#: ``build_stationary_multitone_signal``, matching the same three
#: frequencies ``build_test_signal`` plays SEQUENTIALLY (500/1000/2000Hz)
#: so the two stimuli remain directly comparable in spectral content,
#: differing only in whether that content is segmented over time or
#: present simultaneously throughout.
STATIONARY_MULTITONE_HZ = (500.0, 1000.0, 2000.0)


def build_stationary_multitone_signal(
    *, duration_s: float = 3.0, sample_rate: int = DEFAULT_SAMPLE_RATE, amplitude: float = 0.5,
    tones_hz: tuple[float, ...] = STATIONARY_MULTITONE_HZ,
) -> bytes:
    """R0082-C -- a STATIONARY multitone stimulus: all of ``tones_hz``
    (default 500/1000/2000Hz, matching ``build_test_signal``'s own three
    frequencies) play SIMULTANEOUSLY for the entire ``duration_s``, with
    no segment transitions -- spectral content is identical from the
    first sample to the last. This exists to remove a confound R0082-B's
    WAV sanity check found: ``build_test_signal``'s sequential
    500->1000->2000Hz segments meant a late-window RMS rise could not be
    distinguished from a frequency-dependent acoustic/electrical
    response. A per-3s-window RMS/peak comparison across a stationary
    stimulus has no such ambiguity -- any systematic window-to-window
    change can only reflect a time/stream-age effect, not a change in
    what was played.

    Deterministic fixed phases (all zero) -- every component's own
    sin(2*pi*hz*t) is exactly zero at t=0, so the unfaded signal itself
    already starts at zero amplitude; the same 20ms linear fade
    envelope ``build_test_signal`` uses is still applied for consistency
    and to avoid any edge discontinuity from the fade window itself.

    ``amplitude`` here means the SAME thing it means for
    ``build_test_signal``/``build_mls_signal``: the peak amplitude (as a
    fraction of full scale) a SINGLE tone at this amplitude would have.
    Naively giving each of the ``len(tones_hz)`` components that same
    per-tone amplitude would roughly TRIPLE the total drive level (RMS
    power adds across uncorrelated frequencies) -- normalized instead so
    the COMBINED signal's total RMS approximately equals a single tone's
    RMS at ``amplitude``. See the exact derivation below."""
    n_tones = len(tones_hz)
    # Normalization math (exact):
    #   A single sinusoid of peak amplitude `A` has RMS = A / sqrt(2).
    #   N uncorrelated sinusoids (different frequencies -> ~zero
    #   cross-correlation over any window spanning many cycles) of EQUAL
    #   per-tone peak amplitude `a` sum to a combined signal whose power
    #   (mean-square) is the SUM of each component's own power (variances
    #   add for uncorrelated signals): combined_meansq = N * (a^2 / 2),
    #   so combined_RMS = a * sqrt(N / 2).
    #   We want combined_RMS == single_tone_RMS == amplitude / sqrt(2):
    #       a * sqrt(N / 2) = amplitude / sqrt(2)
    #       a = amplitude / sqrt(2) / sqrt(N / 2)
    #       a = amplitude / sqrt(N)
    #   (sqrt(2) cancels exactly.) Confirmed algebraically self-consistent:
    #   combined_RMS = (amplitude/sqrt(N)) * sqrt(N/2) = amplitude/sqrt(2),
    #   i.e. EXACTLY the single-tone RMS at the same `amplitude`, for any N.
    per_tone_amplitude = amplitude / math.sqrt(n_tones)

    n_total = int(duration_s * sample_rate)
    fade_n = max(1, int(0.02 * sample_rate))  # 20ms fade, matches build_test_signal
    samples = array.array("h")
    for i in range(n_total):
        t = i / sample_rate
        env = 1.0
        if i < fade_n:
            env = i / fade_n
        elif i > n_total - fade_n:
            env = (n_total - i) / fade_n
        mix = sum(math.sin(2 * math.pi * hz * t) for hz in tones_hz)
        value = int(env * per_tone_amplitude * 32767 * mix)
        # Defensive clamp: worst-case perfect constructive interference of
        # all N components reaches per_tone_amplitude*32767*N, which for
        # amplitude<=1.0 and N=3 is well under the int16 range (32767) --
        # verified empirically (R0082-C offline validation) to never
        # actually trigger; kept only as a safety bound, not a sign this
        # stimulus is expected to clip.
        value = max(-32768, min(32767, value))
        samples.append(value)
    return samples.tobytes()


def build_signal(
    stimulus: str, *, duration_s: float, amplitude: float, sample_rate: int = DEFAULT_SAMPLE_RATE
) -> bytes:
    """Verbatim copy of ``r0081_direct_aec_diagnostic.build_signal``'s
    dispatch logic, extended (R0082-C) with ``"stationary_multitone"``."""
    if stimulus == "tones":
        return build_test_signal(
            duration_s=duration_s, sample_rate=sample_rate, amplitude=amplitude
        )
    if stimulus == "mls":
        return build_mls_signal(
            duration_s=duration_s, sample_rate=sample_rate, amplitude=amplitude
        )
    if stimulus == "stationary_multitone":
        return build_stationary_multitone_signal(
            duration_s=duration_s, sample_rate=sample_rate, amplitude=amplitude
        )
    raise ValueError(f"unknown stimulus: {stimulus!r}")


def _rms(pcm_chunk: bytes) -> float:
    """Verbatim copy of ``m2_6b4m_self_echo_probe._rms`` (already
    stdlib-only -- ``struct``, not ``numpy``)."""
    n = len(pcm_chunk) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_chunk[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


def _peak(pcm_chunk: bytes) -> int:
    """Verbatim copy of ``m2_6b4m_self_echo_probe._peak``."""
    n = len(pcm_chunk) // 2
    if n == 0:
        return 0
    samples = struct.unpack(f"<{n}h", pcm_chunk[: n * 2])
    return max(abs(s) for s in samples)


def _write_wav(path: Path, pcm: bytes, *, sample_rate: int) -> None:
    """Verbatim copy of ``m2_6b4m_self_echo_probe._write_wav`` (mono,
    16-bit PCM S16_LE, via the stdlib ``wave`` module)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
