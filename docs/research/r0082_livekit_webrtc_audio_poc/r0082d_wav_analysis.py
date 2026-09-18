#!/usr/bin/env python3
"""R0082-D -- offline analysis of the four-run counterbalanced speech
AEC OFF/ON sequence (A1=OFF, B1=ON, B2=ON, A2=OFF).

**Research/analysis only.** No `nexa.*`/`pipecat.*`/`google.*`/`loguru`
imports, no LiveKit, no hardware access, no production dependency
surface. Run with plain system `python3` (numpy + scipy already
available there, confirmed R0082-C) -- NOT NeXa's own `.venv` (no
scipy) and NOT the isolated livekit probe venv (unrelated).

Reads the WAVs directly (never modifies them) and performs:
  - file verification (path, SHA256, format) for all 4 runs
  - lag-tolerant cross-correlation alignment of each mic recording
    against the canonical speech signal (real speech is non-periodic,
    unlike R0082-C's stationary_multitone -- no 2ms ambiguity)
  - full-duration (not runtime-truncated) speech-region metrics per run
  - stimulus-correlated residual vs. non-correlated residual, via a
    best-lag linear fit (mic ~= alpha * shifted_signal + noise)
  - A1 pre-roll anomaly investigation vs. B1/B2/A2 pre-roll
  - pairwise OFF/ON comparisons
  - run-order trend
  - time-resolved (250ms/500ms/1s) RMS tables, lag-aligned across runs
  - STFT-derived power in 4 named frequency bands

All RMS values are reported on the ORIGINAL int16 PCM scale -- no
normalization of the recordings themselves.
"""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import numpy as np
from scipy.signal import correlate, stft

CAPTURES = Path(__file__).resolve().parent / "r0082d_aec_captures"
CANONICAL_SIG = (
    Path(__file__).resolve().parent
    / "r0082d_speech_stimulus"
    / "r0082d_speech_en_pl_v1.wav"
)
SAMPLE_RATE = 48000
PRE_ROLL_S = 1.0
STIM_DURATION_S = 23.181416666666667  # exact, from the canonical WAV header

RUNS = {
    "A1_OFF": ("r0082d_aecoff_20260918T144322Z_speech_signal.wav",
               "r0082d_aecoff_20260918T144322Z_speech_mic.wav"),
    "B1_ON":  ("r0082d_aecon_20260918T144740Z_speech_signal.wav",
               "r0082d_aecon_20260918T144740Z_speech_mic.wav"),
    "B2_ON":  ("r0082d_aecon_20260918T144932Z_speech_signal.wav",
               "r0082d_aecon_20260918T144932Z_speech_mic.wav"),
    "A2_OFF": ("r0082d_aecoff_20260918T145221Z_speech_signal.wav",
               "r0082d_aecoff_20260918T145221Z_speech_mic.wav"),
}
EXTRA_UNLISTED_RUN = "r0082d_aecoff_20260918T145123Z_speech_{signal,mic}.wav"

BANDS = [(300.0, 1000.0), (1000.0, 2000.0), (2000.0, 4000.0), (4000.0, 8000.0)]


def load_wav(path: Path) -> tuple[np.ndarray, int, int, int]:
    with wave.open(str(path), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        fr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    assert nch == 1 and sw == 2, f"{path}: expected mono 16-bit, got nch={nch} sw={sw}"
    return np.frombuffer(raw, dtype="<i2").astype(np.float64), fr, nframes, len(raw)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0


def peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if len(x) else 0.0


def find_lag_and_corr(sig: np.ndarray, mic_region: np.ndarray, sr: int, max_lag_ms: float):
    """Cross-correlate `sig` against a WIDER `mic_region` that already
    contains some slack before/after the expected onset, searching for
    the lag (in samples, relative to mic_region's own start) that best
    aligns sig's copy inside mic_region. Returns (lag_samples, peak_corr)."""
    sig_c = sig - np.mean(sig)
    mic_c = mic_region - np.mean(mic_region)
    sig_norm = np.sqrt(np.sum(sig_c**2))
    if sig_norm < 1e-9:
        return 0, 0.0
    full = correlate(mic_c, sig_c, mode="full", method="fft")
    # normalize each lag position by the LOCAL mic energy under the sig window
    # (a proper sliding RMS normalization) for a fair peak comparison
    mic_energy = np.cumsum(np.concatenate(([0.0], mic_c**2)))

    def local_mic_norm(lag):
        start = max(lag, 0)
        end = min(lag + len(sig_c), len(mic_c))
        if end <= start:
            return 1e-9
        return np.sqrt(mic_energy[end] - mic_energy[start])

    max_lag_samples = int(max_lag_ms * sr / 1000)
    # Bug fix (found empirically this round): initializing best_corr to -1.0
    # (the theoretical maximum |correlation|) meant `abs(c) > abs(best_corr)`
    # could never be satisfied by real, weakly-correlated audio -- the loop
    # silently never updated best_lag/best_corr from their defaults. Must
    # initialize below any achievable real magnitude (0.0 is safe: any
    # nonzero correlation beats it).
    best_lag, best_corr = 0, 0.0
    for lag in range(-max_lag_samples, max_lag_samples + 1):
        idx = lag + (len(sig_c) - 1)
        if idx < 0 or idx >= len(full):
            continue
        denom = sig_norm * local_mic_norm(lag)
        if denom < 1e-9:
            continue
        c = full[idx] / denom
        if abs(c) > abs(best_corr):
            best_corr, best_lag = c, lag
    return best_lag, best_corr


def aligned_mic_region(mic: np.ndarray, lag_samples: int, n_sig: int) -> np.ndarray:
    start = PRE_ROLL_S_SAMPLES + lag_samples
    end = start + n_sig
    start = max(start, 0)
    end = min(end, len(mic))
    return mic[start:end]


PRE_ROLL_S_SAMPLES = int(PRE_ROLL_S * SAMPLE_RATE)


def best_fit_residual(sig: np.ndarray, mic_aligned: np.ndarray):
    """Least-squares alpha such that mic_aligned ~= alpha*sig minimizes
    residual; returns (alpha, r_squared, stimulus_component_rms,
    residual_rms)."""
    n = min(len(sig), len(mic_aligned))
    s = sig[:n] - np.mean(sig[:n])
    m = mic_aligned[:n] - np.mean(mic_aligned[:n])
    denom = np.sum(s**2)
    alpha = float(np.sum(s * m) / denom) if denom > 1e-9 else 0.0
    predicted = alpha * s
    residual = m - predicted
    ss_tot = np.sum(m**2)
    ss_res = np.sum(residual**2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
    return alpha, float(r_squared), rms(predicted), rms(residual)


def band_power(x: np.ndarray, sr: int, band: tuple[float, float]) -> float:
    if len(x) < 256:
        return 0.0
    f, t, Z = stft(x, fs=sr, nperseg=1024, noverlap=512)
    mask = (f >= band[0]) & (f < band[1])
    power = np.mean(np.abs(Z[mask, :]) ** 2)
    return float(np.sqrt(power))


def fft_top_peaks(
    x: np.ndarray, sr: int, n_peaks: int = 6, fmin: float = 20.0, fmax: float = 4000.0
):
    if len(x) < 8:
        return []
    window = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * window)) / (np.sum(window) / 2.0)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / sr)
    mask = (freqs >= fmin) & (freqs <= fmax)
    freqs, spec = freqs[mask], spec[mask]
    idx = np.argsort(spec)[::-1]
    peaks, used = [], []
    for i in idx:
        f = freqs[i]
        if any(abs(f - uf) < 15.0 for uf in used):
            continue
        peaks.append((float(f), float(spec[i])))
        used.append(f)
        if len(peaks) >= n_peaks:
            break
    return peaks


def main() -> None:
    print("=" * 70)
    print("SECTION 1: file verification")
    print("=" * 70)
    canon_pcm, canon_sr, canon_n, _ = load_wav(CANONICAL_SIG)
    canon_hash = sha256_of(CANONICAL_SIG)
    print(
        f"canonical signal: {CANONICAL_SIG.name} sha256={canon_hash} "
        f"n={canon_n} dur={canon_n / canon_sr:.3f}s"
    )

    mic_pcm = {}
    for label, (sig_name, mic_name) in RUNS.items():
        sig_path, mic_path = CAPTURES / sig_name, CAPTURES / mic_name
        sig_pcm, sig_sr, sig_n, _ = load_wav(sig_path)
        m_pcm, m_sr, m_n, _ = load_wav(mic_path)
        sig_hash = sha256_of(sig_path)
        mic_hash = sha256_of(mic_path)
        identical = sig_hash == canon_hash
        print(f"\n{label}:")
        print(f"  signal: {sig_path.name}  sha256={sig_hash}  identical_to_canonical={identical}")
        print(f"  mic:    {mic_path.name}  sha256={mic_hash}  n={m_n}  dur={m_n/m_sr:.3f}s")
        assert identical, f"{label} signal WAV differs from canonical -- STOP"
        mic_pcm[label] = m_pcm

    extra = CAPTURES / "r0082d_aecoff_20260918T145123Z_speech_mic.wav"
    print(f"\nNOTE: an extra, UNLISTED OFF run exists in the capture directory: {extra.name}")
    print(
        "      NOT part of the specified A1/B1/B2/A2 sequence -- excluded "
        "from analysis below, flagged for transparency only."
    )

    print("\n" + "=" * 70)
    print("SECTION 3: lag-tolerant alignment (real speech -- non-periodic, no 2ms ambiguity)")
    print("=" * 70)
    lags, corrs, aligned = {}, {}, {}
    for label, mic in mic_pcm.items():
        # search region: pre-roll start .. pre-roll+duration+2s slack, wide enough for +-300ms lag
        search_start = max(PRE_ROLL_S_SAMPLES - int(0.3 * SAMPLE_RATE), 0)
        search_end = PRE_ROLL_S_SAMPLES + canon_n + int(0.3 * SAMPLE_RATE)
        mic_region = mic[search_start:search_end]
        lag_rel, corr = find_lag_and_corr(canon_pcm, mic_region, SAMPLE_RATE, max_lag_ms=300.0)
        # lag_rel is relative to mic_region's own start; convert to absolute
        # offset from PRE_ROLL_S_SAMPLES
        lag_abs = (search_start + lag_rel) - PRE_ROLL_S_SAMPLES
        lags[label] = lag_abs
        corrs[label] = corr
        aligned[label] = aligned_mic_region(mic, lag_abs, canon_n)
        print(f"{label}: best_lag={lag_abs/SAMPLE_RATE*1000:+8.2f}ms  peak_corr_coeff={corr:+.4f}")
    print(
        "\nConfidence/limitation: real speech's own autocorrelation is NOT "
        "perfectly flat (some self-similarity within repeated phonemes/words "
        "exists), so a small residual ambiguity around the true acoustic "
        "delay is possible, but nowhere near R0082-C's exact 2ms periodic "
        "ambiguity -- these lag estimates are treated as usable single "
        "values, not just coarse bins."
    )

    print("\n" + "=" * 70)
    print("SECTION 4: full-duration per-run metrics (recomputed, not runtime-truncated)")
    print("=" * 70)
    results = {}
    for label, mic in mic_pcm.items():
        quiet = mic[:PRE_ROLL_S_SAMPLES]
        full_region = aligned[label]
        alpha, r2, stim_rms, resid_rms = best_fit_residual(canon_pcm, full_region)
        results[label] = {
            "quiet_rms": rms(quiet), "quiet_peak": peak(quiet),
            "full_rms": rms(full_region), "full_peak": peak(full_region),
            "lag_ms": lags[label] / SAMPLE_RATE * 1000, "corr": corrs[label],
            "alpha": alpha, "r2": r2, "stim_component_rms": stim_rms, "residual_rms": resid_rms,
        }
        r = results[label]
        dur_s = len(full_region) / SAMPLE_RATE
        print(f"\n{label}:")
        print(f"  pre-roll: rms={r['quiet_rms']:.2f} peak={r['quiet_peak']:.0f}")
        print(f"  full speech region (aligned, {len(full_region)} samples = {dur_s:.3f}s):")
        print(f"    rms={r['full_rms']:.2f}  peak={r['full_peak']:.0f}")
        print(
            f"    best_lag={r['lag_ms']:+.2f}ms  corr_coeff={r['corr']:+.4f}  "
            f"r_squared={r['r2']:.4f}"
        )
        print(
            f"    stimulus-correlated component rms={r['stim_component_rms']:.2f}  "
            f"(alpha={r['alpha']:.4f})"
        )
        print(f"    non-correlated residual rms={r['residual_rms']:.2f}")

    print("\n" + "=" * 70)
    print("SECTION 5: A1 pre-roll anomaly investigation")
    print("=" * 70)
    for label, mic in mic_pcm.items():
        quiet = mic[:PRE_ROLL_S_SAMPLES]
        peaks = fft_top_peaks(quiet, SAMPLE_RATE)
        print(f"\n{label} pre-roll ({len(quiet)} samples):")
        print(f"  mean(DC)={np.mean(quiet):.3f}  rms={rms(quiet):.2f}  peak={peak(quiet):.0f}")
        window = np.hanning(len(quiet))
        spec = np.abs(np.fft.rfft(quiet * window)) / (np.sum(window) / 2.0)
        print(f"  broadband floor (median FFT magnitude): {np.median(spec):.4f}")
        print("  top spectral peaks:")
        for f, mgn in peaks:
            print(f"    {f:8.1f}Hz  mag={mgn:8.2f}")
        # zero-run / transient scan
        n = len(quiet)
        halves = [quiet[: n // 2], quiet[n // 2 :]]
        print(
            f"  first-half rms={rms(halves[0]):.2f}  second-half rms={rms(halves[1]):.2f}  "
            "(transient-onset check)"
        )

    print("\n" + "=" * 70)
    print("SECTION 6: pairwise comparisons (full-region rms and stimulus-correlated residual)")
    print("=" * 70)
    pairs = [("A1_OFF", "B1_ON"), ("B2_ON", "A2_OFF"), ("A1_OFF", "A2_OFF"), ("B1_ON", "B2_ON")]
    for a, b in pairs:
        ra, rb = results[a], results[b]
        ratio_rms = rb["full_rms"] / ra["full_rms"] if ra["full_rms"] > 0 else float("inf")
        db_rms = 20 * np.log10(ratio_rms) if ratio_rms > 0 else float("-inf")
        a_stim, b_stim = ra["stim_component_rms"], rb["stim_component_rms"]
        ratio_stim = b_stim / a_stim if a_stim > 0 else float("inf")
        db_stim = 20 * np.log10(ratio_stim) if ratio_stim > 0 else float("-inf")
        print(f"\n{a} vs {b}:")
        print(
            f"  full-region rms: {ra['full_rms']:.2f} -> {rb['full_rms']:.2f}  "
            f"ratio={ratio_rms:.3f}x  {db_rms:+.2f}dB"
        )
        print(
            f"  stimulus-correlated component rms: {ra['stim_component_rms']:.2f} -> "
            f"{rb['stim_component_rms']:.2f}  ratio={ratio_stim:.3f}x  {db_stim:+.2f}dB"
        )
        print(f"  r_squared: {ra['r2']:.4f} -> {rb['r2']:.4f}")

    print("\n" + "=" * 70)
    print("SECTION 7: run-order trend (full-duration recomputed, run order A1->B1->B2->A2)")
    print("=" * 70)
    order = ["A1_OFF", "B1_ON", "B2_ON", "A2_OFF"]
    for label in order:
        r = results[label]
        print(
            f"  {label}: full_rms={r['full_rms']:7.2f}  "
            f"stim_component_rms={r['stim_component_rms']:7.2f}  "
            f"quiet_rms={r['quiet_rms']:7.2f}"
        )

    print("\n" + "=" * 70)
    print("SECTION 8: time-resolved RMS (250ms/1s), lag-aligned, first 10s of speech region")
    print("=" * 70)
    for win_s, label_name in ((0.25, "250ms"), (1.0, "1s")):
        print(f"\n-- {label_name} windows --")
        n_win = int(10.0 / win_s)
        header = "  t(s)   " + "".join(f"{lbl:>10}" for lbl in order)
        print(header)
        win_samples = int(win_s * SAMPLE_RATE)
        for i in range(n_win):
            row = f"  {i*win_s:5.2f}  "
            for lbl in order:
                seg = aligned[lbl][i * win_samples : (i + 1) * win_samples]
                row += f"{rms(seg):10.1f}"
            print(row)

    print("\n" + "=" * 70)
    print("SECTION 9: STFT-derived band power (aligned full speech region)")
    print("=" * 70)
    for label in order:
        region = aligned[label]
        print(f"\n{label}:")
        for lo, hi in BANDS:
            bp = band_power(region, SAMPLE_RATE, (lo, hi))
            print(f"  {lo:5.0f}-{hi:5.0f}Hz: power_rms={bp:8.3f}")


if __name__ == "__main__":
    main()
