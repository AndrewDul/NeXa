#!/usr/bin/env python3
"""R0082-C -- offline WAV analysis of the first complete PlatformAudio
AEC OFF/ON pair (stationary_multitone stimulus).

**Research/analysis only.** No `nexa.*`/`pipecat.*`/`google.*`/`loguru`
imports, no LiveKit, no hardware access, no production dependency
surface. Run with plain system `python3` (numpy 2.2.4 + scipy 1.17.1
already available there on this machine, confirmed this round) -- NOT
NeXa's own `.venv` (no scipy there) and NOT the isolated livekit probe
venv (unrelated to this script's purpose).

Reads the 4 WAVs directly (never modifies them) and performs:
  - capture-completeness / hash verification
  - 1s and 250ms time-domain RMS/peak tables
  - Goertzel-style narrow-band magnitude at 500/1000/2000Hz per time
    region, plus an FFT-based broadband/peak survey
  - lag-tolerant normalized cross-correlation between the known signal
    and each mic recording, per time region
  - quiet/pre-roll baseline structure (DC, RMS, peak, spectral peaks)

All RMS values are reported on the ORIGINAL int16 PCM scale -- no
normalization of the recordings themselves.
"""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import numpy as np
from scipy.signal import correlate

CAPTURES = Path(__file__).resolve().parent / "r0082c_aec_captures"
SAMPLE_RATE = 48000
PRE_ROLL_S = 1.0
DURATION_S = 30.0

OFF_MIC = CAPTURES / "r0082c_aecoff_20260918T134735Z_stationary_multitone_mic.wav"
OFF_SIG = CAPTURES / "r0082c_aecoff_20260918T134735Z_stationary_multitone_signal.wav"
ON_MIC = CAPTURES / "r0082c_aecon_20260918T135110Z_stationary_multitone_mic.wav"
ON_SIG = CAPTURES / "r0082c_aecon_20260918T135110Z_stationary_multitone_signal.wav"

TARGET_HZ = (500.0, 1000.0, 2000.0)
OFF_TARGET_HZ = (750.0, 1500.0, 3000.0, 4000.0)  # not in the stimulus -- noise-floor reference


def load_wav(path: Path) -> tuple[np.ndarray, int, int, int]:
    with wave.open(str(path), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        fr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    assert nch == 1 and sw == 2, f"{path}: expected mono 16-bit, got nch={nch} sw={sw}"
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    return data, fr, nframes, len(raw)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def rms(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def peak(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(np.max(np.abs(x)))


def goertzel_mag(x: np.ndarray, freq: float, sr: int) -> float:
    """Single-bin DFT correlation magnitude (normalized by window length),
    same method used in R0082-C's own stationary_multitone validation."""
    n = np.arange(len(x))
    re = np.sum(x * np.cos(2 * np.pi * freq * n / sr))
    im = np.sum(x * np.sin(2 * np.pi * freq * n / sr))
    return float(np.hypot(re, im) / len(x)) if len(x) else 0.0


def fft_top_peaks(
    x: np.ndarray, sr: int, n_peaks: int = 8, fmin: float = 20.0, fmax: float = 8000.0
):
    if len(x) < 8:
        return []
    window = np.hanning(len(x))
    spec = np.fft.rfft(x * window)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / sr)
    mag = np.abs(spec) / (np.sum(window) / 2.0)
    mask = (freqs >= fmin) & (freqs <= fmax)
    freqs, mag = freqs[mask], mag[mask]
    idx = np.argsort(mag)[::-1]
    peaks = []
    used = set()
    for i in idx:
        f = freqs[i]
        if any(abs(f - uf) < 15.0 for uf in used):
            continue
        peaks.append((float(f), float(mag[i])))
        used.add(f)
        if len(peaks) >= n_peaks:
            break
    return peaks


def lag_tolerant_xcorr(sig: np.ndarray, mic: np.ndarray, sr: int, max_lag_ms: float = 100.0):
    """Normalized cross-correlation, bounded lag search. Returns
    (best_lag_ms, peak_corr_coeff, note). `sig`/`mic` must be same length
    (caller aligns windows); this function searches lags within
    +/-max_lag_ms by SHIFTING `mic` relative to `sig`."""
    max_lag = int(max_lag_ms * sr / 1000)
    n = min(len(sig), len(mic))
    sig = sig[:n] - np.mean(sig[:n])
    mic = mic[:n] - np.mean(mic[:n])
    sig_norm = np.sqrt(np.sum(sig**2))
    mic_norm = np.sqrt(np.sum(mic**2))
    if sig_norm < 1e-9 or mic_norm < 1e-9:
        return 0.0, 0.0, "degenerate (near-silent window)"
    full = correlate(mic, sig, mode="full", method="fft")
    lags = np.arange(-(n - 1), n)
    center = len(full) // 2
    lo, hi = center - max_lag, center + max_lag + 1
    lo, hi = max(lo, 0), min(hi, len(full))
    window_corr = full[lo:hi] / (sig_norm * mic_norm)
    window_lags = lags[lo:hi]
    best_i = int(np.argmax(np.abs(window_corr)))
    best_lag_samples = window_lags[best_i]
    best_corr = float(window_corr[best_i])
    best_lag_ms = best_lag_samples / sr * 1000.0
    return best_lag_ms, best_corr, ""


def region_slice(data: np.ndarray, start_s: float, end_s: float) -> np.ndarray:
    s = int(round(start_s * SAMPLE_RATE))
    e = int(round(end_s * SAMPLE_RATE))
    return data[s:e]


def main() -> None:
    print("=" * 70)
    print("SECTION 3: file verification")
    print("=" * 70)
    files = {"OFF mic": OFF_MIC, "OFF signal": OFF_SIG, "ON mic": ON_MIC, "ON signal": ON_SIG}
    meta = {}
    for label, path in files.items():
        data, fr, nframes, nbytes = load_wav(path)
        digest = sha256_of(path)
        meta[label] = (data, fr, nframes, nbytes, digest)
        print(f"{label}: {path.name}")
        print(f"  size={path.stat().st_size} sha256={digest}")
        print(
            f"  channels=1 sampwidth=2 framerate={fr} nframes={nframes} "
            f"duration={nframes / fr:.3f}s"
        )
    sig_off_hash = meta["OFF signal"][4]
    sig_on_hash = meta["ON signal"][4]
    print(f"\nSignal WAVs identical: {sig_off_hash == sig_on_hash}")

    off_mic, sr, off_n, _, _ = meta["OFF mic"]
    on_mic, _, on_n, _, _ = meta["ON mic"]
    sig, _, sig_n, _, _ = meta["OFF signal"]  # identical to ON signal

    print("\n" + "=" * 70)
    print("SECTION 5a: 1-second time-domain table (stimulus-relative t=0 at pre-roll end)")
    print("=" * 70)
    for label, mic in (("OFF", off_mic), ("ON", on_mic)):
        print(f"\n-- {label} mic --")
        quiet = region_slice(mic, 0.0, PRE_ROLL_S)
        print(f"  pre-roll [0.0,{PRE_ROLL_S}s): rms={rms(quiet):.2f} peak={peak(quiet):.0f}")
        for i in range(30):
            t0, t1 = PRE_ROLL_S + i, PRE_ROLL_S + i + 1
            w = region_slice(mic, t0, t1)
            print(f"  [{i:>2}-{i+1:>2}s] rms={rms(w):7.2f} peak={peak(w):6.0f}")
        tail = region_slice(mic, PRE_ROLL_S + DURATION_S, len(mic) / sr)
        print(f"  tail: rms={rms(tail):.2f} peak={peak(tail):.0f} n={len(tail)}")

    print("\n" + "=" * 70)
    print("SECTION 5b: 250ms fine view of first 6s of stimulus")
    print("=" * 70)
    for label, mic in (("OFF", off_mic), ("ON", on_mic)):
        print(f"\n-- {label} mic, 250ms windows, t=0-6s (stimulus-relative) --")
        for i in range(24):
            t0, t1 = PRE_ROLL_S + i * 0.25, PRE_ROLL_S + (i + 1) * 0.25
            w = region_slice(mic, t0, t1)
            print(f"  [{i*0.25:5.2f}-{(i+1)*0.25:5.2f}s] rms={rms(w):7.2f} peak={peak(w):6.0f}")

    print("\n" + "=" * 70)
    print("SECTION 6: Goertzel narrow-band magnitude at target + off-target frequencies")
    print("=" * 70)
    regions = [("0-3s", 0.0, 3.0), ("3-6s", 3.0, 6.0), ("6-15s", 6.0, 15.0), ("15-30s", 15.0, 30.0)]
    for label, mic in (("OFF", off_mic), ("ON", on_mic)):
        print(f"\n-- {label} mic --")
        for rlabel, t0, t1 in regions:
            w = region_slice(mic, PRE_ROLL_S + t0, PRE_ROLL_S + t1)
            mags = {hz: goertzel_mag(w, hz, sr) for hz in TARGET_HZ}
            off_mags = {hz: goertzel_mag(w, hz, sr) for hz in OFF_TARGET_HZ}
            # Fraction of window RMS-power explained by the 3 target-frequency
            # sinusoid magnitudes (Parseval-style estimate: a pure sinusoid of
            # goertzel-magnitude m has RMS = m/sqrt(2), so power = m^2/2).
            target_power = sum(m**2 for m in mags.values()) / 2.0
            window_rms = rms(w)
            window_power = window_rms**2
            target_frac = (target_power / window_power) if window_power > 0 else 0.0
            print(
                f"  {rlabel:>7}: rms={window_rms:7.2f}  "
                f"500Hz={mags[500.0]:7.2f} 1000Hz={mags[1000.0]:7.2f} 2000Hz={mags[2000.0]:7.2f}  "
                f"target_frac_of_power={target_frac:5.1%}  "
                f"off-target(750/1500/3000/4000)={off_mags[750.0]:.2f}/{off_mags[1500.0]:.2f}/"
                f"{off_mags[3000.0]:.2f}/{off_mags[4000.0]:.2f}"
            )

    print("\n" + "=" * 70)
    print("SECTION 6b: FFT top-peak survey (post-convergence ON 15-30s, OFF 15-30s, quiet regions)")
    print("=" * 70)
    for label, mic in (
        ("OFF 15-30s", region_slice(off_mic, PRE_ROLL_S + 15.0, PRE_ROLL_S + 30.0)),
        ("ON 15-30s", region_slice(on_mic, PRE_ROLL_S + 15.0, PRE_ROLL_S + 30.0)),
        ("OFF quiet (pre-roll)", region_slice(off_mic, 0.0, PRE_ROLL_S)),
        ("ON quiet (pre-roll)", region_slice(on_mic, 0.0, PRE_ROLL_S)),
    ):
        peaks = fft_top_peaks(mic, sr)
        print(f"\n-- {label} -- top peaks (freq_hz, magnitude):")
        for f, m in peaks:
            near = min(TARGET_HZ, key=lambda hz: abs(hz - f))
            tag = " <- near target" if abs(f - near) < 20 else ""
            print(f"    {f:8.1f}Hz  mag={m:8.2f}{tag}")

    print("\n" + "=" * 70)
    print("SECTION 7: lag-tolerant normalized cross-correlation vs known signal")
    print("=" * 70)
    print(
        "NOTE: the stationary_multitone stimulus (500/1000/2000Hz, all exact "
        "harmonics of a 500Hz fundamental, period 2ms) is itself periodic "
        "with a 2ms period -- cross-correlation lag estimates are therefore "
        "ambiguous modulo ~2ms; only the coarse lag (mod 2ms bin) and the "
        "peak correlation COEFFICIENT are treated as reliable."
    )
    for label, mic in (("OFF", off_mic), ("ON", on_mic)):
        print(f"\n-- {label} mic vs known signal --")
        for rlabel, t0, t1 in regions:
            sig_w = region_slice(sig, t0, t1)
            mic_w = region_slice(mic, PRE_ROLL_S + t0, PRE_ROLL_S + t1)
            lag_ms, corr, note = lag_tolerant_xcorr(sig_w, mic_w, sr, max_lag_ms=100.0)
            print(f"  {rlabel:>7}: best_lag={lag_ms:7.2f}ms  peak_corr_coeff={corr:+.4f}  {note}")

    print("\n" + "=" * 70)
    print("SECTION 8: quiet/pre-roll baseline structure")
    print("=" * 70)
    for label, mic in (("OFF", off_mic), ("ON", on_mic)):
        q = region_slice(mic, 0.0, PRE_ROLL_S)
        print(f"\n-- {label} quiet/pre-roll ({len(q)} samples) --")
        print(f"  mean(DC)={np.mean(q):.3f}  rms={rms(q):.2f}  peak={peak(q):.0f}")
        peaks = fft_top_peaks(q, sr, n_peaks=6)
        print("  top spectral peaks:")
        for f, m in peaks:
            print(f"    {f:8.1f}Hz  mag={m:8.2f}")
        # broadband floor estimate: median magnitude across the spectrum
        window = np.hanning(len(q))
        spec = np.abs(np.fft.rfft(q * window)) / (np.sum(window) / 2.0)
        print(f"  broadband floor (median FFT magnitude across spectrum): {np.median(spec):.4f}")

    print("\n" + "=" * 70)
    print("SECTION 9: OFF physical-playout verification (SNR of target vs off-target)")
    print("=" * 70)
    off_full_stim = region_slice(off_mic, PRE_ROLL_S, PRE_ROLL_S + DURATION_S)
    for hz in TARGET_HZ:
        m = goertzel_mag(off_full_stim, hz, sr)
        print(f"  OFF full 30s target {hz:.0f}Hz: magnitude={m:.3f}")
    for hz in OFF_TARGET_HZ:
        m = goertzel_mag(off_full_stim, hz, sr)
        print(f"  OFF full 30s off-target {hz:.0f}Hz: magnitude={m:.3f}")
    off_quiet = region_slice(off_mic, 0.0, PRE_ROLL_S)
    for hz in TARGET_HZ:
        m = goertzel_mag(off_quiet, hz, sr)
        print(f"  OFF quiet (pre-roll) at target {hz:.0f}Hz: magnitude={m:.3f}")


if __name__ == "__main__":
    main()
