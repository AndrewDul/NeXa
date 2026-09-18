#!/usr/bin/env python3
"""R0082-F -- independent validation of `StreamingResampler` against a
mathematically identical whole-array reference, BEFORE any hardware
test.

**Research/analysis only. No hardware, no LiveKit room, no
PlatformAudio, no audio device of any kind.** Imports `StreamingResampler`
directly from `r0082f_live_vad_self_echo_poc.py` (not a reimplementation)
and validates that chunking (regular 480-sample LiveKit-sized chunks,
and irregular deterministic chunk sizes) does not change the numerical
result relative to processing the whole input array in one operation
with the SAME FIR coefficients and the SAME 3:1 decimation phase
convention.

Run with the isolated probe venv (needs scipy, numpy, and the real
`StreamingResampler`/`LiveVadChain` classes -- this script does import
`livekit` transitively via `r0082f_live_vad_self_echo_poc.py`'s own
module-level import, but performs no room/network/hardware operations
of any kind):

    <probe-venv>/bin/python3 r0082f_resampler_validation.py
"""

from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location(
    "r0082f_poc", str(HERE / "r0082f_live_vad_self_echo_poc.py")
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["r0082f_poc"] = _mod
spec.loader.exec_module(_mod)

StreamingResampler = _mod.StreamingResampler
LiveVadChain = _mod.LiveVadChain
VAD_FRAME_SAMPLES = _mod.VAD_FRAME_SAMPLES
VAD_SAMPLE_RATE = _mod.VAD_SAMPLE_RATE
LIVE_SAMPLE_RATE = _mod.LIVE_SAMPLE_RATE

SPEECH_WAV = HERE / "r0082d_speech_stimulus" / "r0082d_speech_en_pl_v1.wav"


def whole_array_reference(pcm_int16: np.ndarray) -> np.ndarray:
    """The SAME FIR coefficients and the SAME 3:1 decimation phase
    convention as `StreamingResampler`, applied to the ENTIRE array in
    ONE `lfilter` call (zi starts at zero -- same silent-start
    assumption `StreamingResampler.__init__` makes), decimating from
    index 0 (matching `_total_in_samples=0` at the start of a fresh
    `StreamingResampler`)."""
    r = StreamingResampler()  # only used to obtain the exact same `b` coefficients
    x = pcm_int16.astype(np.float64)
    y, _ = lfilter(r.b, [1.0], x, zi=np.zeros(len(r.b) - 1))
    out = y[0 :: r.decim]
    return np.clip(np.round(out), -32768, 32767).astype(np.int16)


def streamed_with_chunks(pcm_int16: np.ndarray, chunk_sizes) -> np.ndarray:
    """Feeds `pcm_int16` through a FRESH `StreamingResampler` instance in
    the given sequence of chunk sizes (cycled if shorter than the input),
    concatenating each call's output -- exactly how the live harness
    consumes `rtc.AudioStream` frames."""
    r = StreamingResampler()
    out_chunks = []
    i = 0
    ci = 0
    n = len(pcm_int16)
    while i < n:
        size = chunk_sizes[ci % len(chunk_sizes)]
        ci += 1
        chunk = pcm_int16[i : i + size]
        if len(chunk) == 0:
            break
        out_chunks.append(r.push(chunk))
        i += size
    return np.concatenate(out_chunks) if out_chunks else np.zeros(0, dtype=np.int16)


def compare(label: str, ref: np.ndarray, test: np.ndarray) -> dict:
    n_ref, n_test = len(ref), len(test)
    n = min(n_ref, n_test)
    a = ref[:n].astype(np.float64)
    b = test[:n].astype(np.float64)

    if n > 1 and np.std(a) > 0 and np.std(b) > 0:
        pearson = float(np.corrcoef(a, b)[0, 1])
    else:
        pearson = float("nan")

    # bounded lag search (+-5 samples) purely as a sanity check -- for a
    # truly identical filter+phase implementation this should be 0.
    best_lag, best_corr = 0, -1.0
    for lag in range(-5, 6):
        if lag >= 0:
            aa, bb = a[: n - lag], b[lag:n]
        else:
            aa, bb = a[-lag:n], b[: n + lag]
        if len(aa) < 2 or np.std(aa) == 0 or np.std(bb) == 0:
            continue
        c = float(np.corrcoef(aa, bb)[0, 1])
        if c > best_corr:
            best_corr, best_lag = c, lag

    diff = a - b
    max_abs_diff = float(np.max(np.abs(diff))) if n else float("nan")
    rms_diff = float(np.sqrt(np.mean(diff**2))) if n else float("nan")
    first_diff_idx = None
    if n:
        nz = np.nonzero(diff)[0]
        if len(nz):
            first_diff_idx = int(nz[0])

    print(f"-- {label} --")
    print(f"  ref_samples={n_ref}  test_samples={n_test}  length_diff={n_test - n_ref}")
    print(f"  pearson_at_lag0={pearson:.8f}  best_lag={best_lag} (corr={best_corr:.8f})")
    print(f"  max_abs_diff={max_abs_diff:.6f}  rms_diff={rms_diff:.6f}")
    print(f"  first_differing_sample_index={first_diff_idx}")
    return {
        "length_diff": n_test - n_ref,
        "pearson": pearson,
        "max_abs_diff": max_abs_diff,
        "rms_diff": rms_diff,
        "first_diff_idx": first_diff_idx,
    }


def load_speech_pcm() -> np.ndarray:
    with wave.open(str(SPEECH_WAV), "rb") as wf:
        assert wf.getframerate() == 48000
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype="<i2")


IRREGULAR_CHUNKS = [479, 481, 137, 997, 211, 503, 1, 2999, 50, 480, 17, 4001, 333]


def section_identical_filter_test() -> None:
    print("=" * 70)
    print("SECTION 3/4: whole-array reference vs. streamed (regular + irregular chunks)")
    print("=" * 70)
    pcm = load_speech_pcm()
    print(f"input: {len(pcm)} samples @ 48kHz ({len(pcm) / 48000:.3f}s)")

    ref = whole_array_reference(pcm)
    b_regular = streamed_with_chunks(pcm, [480])
    c_irregular = streamed_with_chunks(pcm, IRREGULAR_CHUNKS)

    compare("A vs B: whole-array reference vs 480-sample streaming", ref, b_regular)
    compare("A vs C: whole-array reference vs irregular-chunk streaming", ref, c_irregular)
    compare("B vs C: 480-sample streaming vs irregular-chunk streaming", b_regular, c_irregular)


def section_impulse_test() -> None:
    print()
    print("=" * 70)
    print("SECTION 6: impulse response / group delay / decimation phase")
    print("=" * 70)
    n = 4800  # 100ms @ 48kHz
    impulse = np.zeros(n, dtype="<i2")
    impulse[0] = 32767

    ref = whole_array_reference(impulse)
    reg = streamed_with_chunks(impulse, [480])
    irr = streamed_with_chunks(impulse, IRREGULAR_CHUNKS)

    def peak_info(x: np.ndarray) -> str:
        idx = int(np.argmax(np.abs(x)))
        peak_time_ms = idx / VAD_SAMPLE_RATE * 1000
        return f"peak_index={idx} peak_value={int(x[idx])} (peak_time={peak_time_ms:.3f}ms)"

    print(f"  reference: {peak_info(ref)}")
    print(f"  480-chunk streaming: {peak_info(reg)}")
    print(f"  irregular-chunk streaming: {peak_info(irr)}")
    compare("impulse: reference vs 480-chunk", ref, reg)
    compare("impulse: reference vs irregular-chunk", ref, irr)


def goertzel_mag(x: np.ndarray, freq: float, sr: int) -> float:
    n_arr = np.arange(len(x))
    re = np.sum(x * np.cos(2 * np.pi * freq * n_arr / sr))
    im = np.sum(x * np.sin(2 * np.pi * freq * n_arr / sr))
    return float(np.hypot(re, im) / len(x)) if len(x) else 0.0


def section_frequency_test() -> None:
    print()
    print("=" * 70)
    print("SECTION 5: frequency response / alias check (48kHz in -> 16kHz out)")
    print("=" * 70)
    sr_in = 48000
    duration_s = 2.0
    n = int(sr_in * duration_s)
    t = np.arange(n) / sr_in
    amplitude = 8000.0  # well below int16 full scale, comparable to real speech-like levels

    test_freqs = [500, 1000, 3000, 6000, 7500, 9000, 12000]
    for f in test_freqs:
        x = (amplitude * np.sin(2 * np.pi * f * t)).astype("<i2")
        y = streamed_with_chunks(x, [480])
        in_mag_at_f = goertzel_mag(x.astype(np.float64), f, sr_in)
        nyquist_out = VAD_SAMPLE_RATE / 2
        out_mag_at_f = None
        if f < nyquist_out:
            out_mag_at_f = goertzel_mag(y.astype(np.float64), f, VAD_SAMPLE_RATE)
        # for frequencies >= output Nyquist, check the ALIASED location instead
        if f >= nyquist_out:
            # classic alias formula for a decimator without adequate anti-aliasing:
            alias_f = abs(f - round(f / VAD_SAMPLE_RATE) * VAD_SAMPLE_RATE)
            alias_mag = goertzel_mag(y.astype(np.float64), alias_f, VAD_SAMPLE_RATE)
            suppression = in_mag_at_f / alias_mag if alias_mag > 1e-6 else float("inf")
            print(
                f"  {f:6d}Hz (>= output Nyquist): in_mag={in_mag_at_f:9.2f}  "
                f"potential_alias_at={alias_f:.0f}Hz alias_mag={alias_mag:9.2f}  "
                f"suppression_ratio={suppression:8.1f}x"
            )
        else:
            print(
                f"  {f:6d}Hz: in_mag={in_mag_at_f:9.2f}  out_mag_same_freq={out_mag_at_f:9.2f}  "
                f"ratio(out/in)={out_mag_at_f / in_mag_at_f:.4f}"
            )

    # stationary multitone (500+1000+3000+6000+7500Hz, all below output Nyquist)
    print("\n  -- stationary multitone (500+1000+3000+6000+7500Hz) --")
    mix_freqs = [500, 1000, 3000, 6000, 7500]
    mix = np.zeros(n)
    for f in mix_freqs:
        mix += np.sin(2 * np.pi * f * t)
    mix = (mix / len(mix_freqs) * amplitude).astype("<i2")
    y = streamed_with_chunks(mix, [480])
    for f in mix_freqs:
        in_mag = goertzel_mag(mix.astype(np.float64), f, sr_in)
        out_mag = goertzel_mag(y.astype(np.float64), f, VAD_SAMPLE_RATE)
        print(
            f"    {f:6d}Hz: in_mag={in_mag:9.2f}  out_mag={out_mag:9.2f}  "
            f"ratio={out_mag / in_mag:.4f}"
        )


def section_controls() -> None:
    print()
    print("=" * 70)
    print("SECTION 8: repeat positive/negative VAD controls with the unchanged resampler")
    print("=" * 70)
    pcm = load_speech_pcm()
    chain = LiveVadChain(csv_writer=None)
    resampler = StreamingResampler()
    vad_buffer = np.zeros(0, dtype=np.int16)
    total_16k = 0
    CHUNK = _mod.FRAME_SAMPLES_LIVE
    for i in range(0, len(pcm), CHUNK):
        chunk = pcm[i : i + CHUNK]
        if len(chunk) < CHUNK:
            break
        resampled = resampler.push(chunk)
        vad_buffer = np.concatenate([vad_buffer, resampled])
        while len(vad_buffer) >= VAD_FRAME_SAMPLES:
            vf = vad_buffer[:VAD_FRAME_SAMPLES]
            vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
            chain.process_frame(vf, total_16k / VAD_SAMPLE_RATE)
            total_16k += VAD_FRAME_SAMPLES
    print(
        f"  POSITIVE CONTROL: n_frames={chain.n_frames} max_prob={chain.max_prob:.4f} "
        f"started_events={chain.started_events} confirmed_events={chain.confirmed_events}"
    )
    assert len(chain.started_events) >= 1, "POSITIVE CONTROL FAILED"
    assert len(chain.confirmed_events) >= 1, "POSITIVE CONTROL FAILED"
    print("  POSITIVE CONTROL: PASS")

    silence = np.zeros(48000 * 26, dtype="<i2")
    chain2 = LiveVadChain(csv_writer=None)
    resampler2 = StreamingResampler()
    vad_buffer = np.zeros(0, dtype=np.int16)
    total_16k = 0
    for i in range(0, len(silence), CHUNK):
        chunk = silence[i : i + CHUNK]
        if len(chunk) < CHUNK:
            break
        resampled = resampler2.push(chunk)
        vad_buffer = np.concatenate([vad_buffer, resampled])
        while len(vad_buffer) >= VAD_FRAME_SAMPLES:
            vf = vad_buffer[:VAD_FRAME_SAMPLES]
            vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
            chain2.process_frame(vf, total_16k / VAD_SAMPLE_RATE)
            total_16k += VAD_FRAME_SAMPLES
    print(
        f"  NEGATIVE CONTROL: n_frames={chain2.n_frames} max_prob={chain2.max_prob:.4f} "
        f"started_events={chain2.started_events} confirmed_events={chain2.confirmed_events}"
    )
    assert len(chain2.started_events) == 0, "NEGATIVE CONTROL FAILED"
    assert len(chain2.confirmed_events) == 0, "NEGATIVE CONTROL FAILED"
    print("  NEGATIVE CONTROL: PASS")


def main() -> None:
    section_identical_filter_test()
    section_impulse_test()
    section_frequency_test()
    section_controls()


if __name__ == "__main__":
    main()
