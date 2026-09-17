#!/usr/bin/env python3
"""R0081 — direct AEC reference/capture diagnostic.

**No Gemini, no LLM, no Pipecat pipeline, no conversation, no recorded
human speech.** A small, standalone, read-only-with-respect-to-device-
config measurement script, separate from the production voice pipeline
(``nexa.realtime.gemini.simple_conversation``) and from the heavy,
Gemini-connected M2.6B research probe
(``m2_6b4m_self_echo_probe.py``) — reuses that probe's own
already-proven, already-tested analysis primitives
(``cross_correlate_pcm``, ``_rms``, ``_peak``, ``_write_wav``,
``_load_wav``) rather than reimplementing them, per R0081's explicit
instruction.

## What this answers

R0081's live diagnostic-timeline evidence proved false local VAD onsets
occur on NeXa's own played-back speech, independent of volume level and
independent of the (never-before-applied-here) R0053 gain-coherence fix.
This script isolates the ONE remaining, hardware-level question that
conversational evidence cannot answer on its own:

    A. Is the "respeaker" ALSA capture endpoint
       (``nexa.voice.config.LocalAudioConfig.input_device_name``, the
       SAME endpoint the production pipeline's local Silero VAD analyzes)
       actually being reduced by the XVF3800's onboard AEC when a
       far-end reference is fed to ``plug:respeaker`` — or is the
       "AEC hardware capability" this project has only ever
       *documented as an unverified observation*
       (``docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`` §9)
       not actually reducing residual echo on this specific capture path?

    B. What is the approximate playback→capture and
       reference→capture lag, as far as this simple measurement can see it?

    C. Quantify: capture RMS with reference OFF vs ON, attenuation in dB,
       and the cross-correlation peak/lag between the fed reference and
       the captured residual.

    D. Test the accepted baseline (``gain_source`` equivalent to
       ``None``, i.e. an unscaled 1.0x reference — exactly what
       ``simple_conversation.py`` feeds today) FIRST. ``--gain`` lets a
       SECOND, separate run repeat the measurement with an explicit
       linear gain applied to the reference (the same
       ``nexa.voice.aec_gain.apply_gain`` production code, not a
       reimplementation) to see whether R0053's gain-coherence mechanism
       measurably changes cancellation on ITS OWN, isolated from a live
       Gemini conversation's non-deterministic response timing/content.

## What `best_lag_ms`/`normalized_correlation` actually measure (read this
## before interpreting a result)

``cross_correlate_pcm(signal_pcm, mic_window, ...)`` always correlates
the **original, un-gained source tone sequence** (the digital signal fed
to the physical speaker) against the **captured, post-XVF3800-processing
residual** — never against the actual post-gain reference bytes sent to
``plug:respeaker`` (which only differs from ``signal_pcm`` when
``--gain != 1.0``). For ``--condition off``, this is a reasonably direct
measure of the acoustic-only path delay (source → speaker → air → mic →
capture, no cancellation involved). For ``--condition on``, the *residual*
being correlated has already been reshaped by whatever cancellation
occurred — a shift in the reported lag between the off/on conditions is
**not** evidence of a changed physical delay; it reflects the correlation
search locking onto a different, now much-attenuated and reshaped
waveform. Treat ON-condition lag numbers as descriptive of the residual's
own correlation structure, not as a literal AEC processing delay.

This script cannot separately measure (2) reference-feed-write → XVF3800
internal processing/capture alignment, or (3) the far-end-reference vs.
real-acoustic-echo relative timing error the AEC's adaptive filter
actually sees internally — the USB Audio Class descriptor audit (R0081's
own report) confirms this device exposes exactly ONE capture stream,
already configured for ASR/beamformed processed output
(``AEC_ASROUTONOFF=1``); there is no standard-ALSA way to simultaneously
capture a "before AEC" and "after AEC" pair from this device as currently
configured. Only the net effect (the post-processing residual) is
observable this way.

## Method

Deterministic test signal (a short sequence of pure tones, generated in
Python — no external fixture file, no speech, nothing that could be
mistaken for a real utterance): played to the REAL physical speaker
(``plug:usb_speaker``) while simultaneously capturing from
``plug:respeaker`` (the SAME endpoint production VAD analyzes). In the
"reference ON" condition, the identical PCM is ALSO fed to
``plug:respeaker``'s *playback* direction at the same time (exactly what
``AecReferenceFeeder`` does in production) — a standard USB Audio Class
device exposes independent playback and capture directions over the same
USB interface, so playing a reference INTO ``plug:respeaker`` while
simultaneously recording FROM ``plug:respeaker`` is the same duplex
pattern the accepted production pipeline already relies on
(``AecReferenceFeeder`` writes to ``plug:respeaker`` while
``LocalAudioTransport`` reads from it for VAD/STT).

Capture starts BEFORE playback (a fixed pre-roll) so the analysis window
can be sliced to align the reference PCM and the corresponding mic-capture
PCM at the same t=0 — deliberately avoiding R0053/R0055's own documented
pitfall in ``cross_correlate_pcm``'s calling convention (that function
assumes both inputs start at the same instant; the OLD probe's own
``ref_pcm`` started 2s after its ``mic_pcm``, saturating the lag search —
not repeated here).

Nothing here writes to XVF3800 firmware/DSP registers, ALSA mixer state,
or ``/etc/asound.conf`` — read-only with respect to persistent device
configuration. Uses only the existing, already-verified ``respeaker``/
``usb_speaker`` ALSA aliases this project's own ``/etc/asound.conf``
already defines (R0053's own system audit).

## Usage

    # baseline: reference OFF (no cancellation at all -- upper bound on leakage)
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition off

    # baseline: reference ON, unscaled (gain=1.0 -- exactly what
    # simple_conversation.py feeds today, gain_source=None)
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition on

    # reference ON, with an explicit gain applied (compare against R0053's
    # CoherentReferenceGain mechanism in isolation, without a live,
    # nondeterministic Gemini conversation)
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --condition on --gain 0.5

    # repeatability: 3 trials each, OFF then ON gain=1.0, same physical
    # setup -- reports mean/min/max, not just one sample
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --condition off --repeats 3
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --condition on --repeats 3

    # VALID (non-clipped) +20dB reference-gain compensation test: lower
    # --amplitude so the post-gain reference has headroom instead of
    # saturating. 0.05 * 10.0 = 0.5, the same peak the default unboosted
    # case already uses safely -- the script prints
    # reference_clipped_samples/percent every run so clipping is never
    # silent; only trust this comparison if that percent is 0 for every
    # trial. This is the corrected version of the earlier --gain 10.0 run,
    # which used the default amplitude=0.5 and very likely clipped.
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --condition on --gain 10.0 --amplitude 0.05 --repeats 3

Run ``--condition off`` then ``--condition on`` back to back (same
physical speaker volume, same room, same mic position) for a directly
comparable pair. Repeat 3x per condition recommended — real acoustic
measurements vary run to run; a single sample is not conclusive. Use
``--repeats`` to get this automatically instead of invoking the script
multiple times by hand.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_6b4m_self_echo_probe import (  # noqa: E402  (reused, not reimplemented)
    _peak,
    _rms,
    _write_wav,
    cross_correlate_pcm,
)

from nexa.voice.aec_gain import apply_gain  # noqa: E402  (reused, not reimplemented)

SAMPLE_RATE = 16000
CAPTURE_DEVICE = "plug:respeaker"
SPEAKER_DEVICE = "plug:usb_speaker"
REFERENCE_DEVICE = "plug:respeaker"  # same endpoint AecReferenceFeeder targets

#: Fixed pre-roll before playback starts, so the capture is already
#: running and the two windows can be aligned by simple index slicing.
PRE_ROLL_S = 1.0
#: Trailing margin after playback ends, to catch any tail/reverberation.
TAIL_MARGIN_S = 1.0

OUT_DIR = Path(__file__).resolve().parent / "r0081_aec_captures"


#: The int16 PCM saturation ceiling/floor `audioop.mul` clips to --
#: confirmed directly (not assumed): `audioop.mul(pack('<h', 30000), 2,
#: 10.0)` returns `32767`, never wraps. A sample landing EXACTLY on this
#: boundary is the clipping signature this script's own diagnostic looks
#: for below.
_INT16_MAX = 32767
_INT16_MIN = -32768


def build_test_signal(
    *, duration_s: float = 3.0, sample_rate: int = SAMPLE_RATE, amplitude: float = 0.5
) -> bytes:
    """A short, deterministic, non-speech test signal: three pure tones
    in sequence (500Hz, 1000Hz, 2000Hz), each with a linear fade-in/out to
    avoid clicks. Nothing here could be mistaken for recorded human
    speech -- no privacy concern, fully reproducible.

    ``amplitude`` (R0081, new): peak amplitude as a fraction of full
    scale (0.0-1.0), default 0.5 (unchanged from the original script).
    Lower this when testing a large ``--gain`` (see ``run_trial``'s own
    clip-detection diagnostic and this module's docstring) -- e.g.
    ``amplitude=0.05`` leaves headroom for a full, undistorted ×10
    (+20dB) reference boost without saturating int16 (0.05 * 10 = 0.5,
    the same peak the DEFAULT unboosted signal already uses safely).
    The digital signal fed to the physical speaker is generated at this
    SAME amplitude in every condition (off/on/on+gain) for one run, so
    the real acoustic stimulus stays comparable across a same-amplitude
    A/B pair -- only ``--gain`` changes what's fed to the reference."""
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


def clip_stats(pcm: bytes) -> dict:
    """R0081 -- heuristic clipping detector: counts int16 samples sitting
    EXACTLY on the saturation ceiling/floor `audioop.mul` produces
    (confirmed by direct test, not assumed). For a synthetic sine tone
    generated well below full scale before any gain, a sample landing
    exactly on the boundary after gain is applied is, for all practical
    purposes, a clipped sample -- a genuine unclipped signal essentially
    never lands on that exact integer by chance. Diagnostic only; never
    changes what is actually played."""
    if not pcm:
        return {"n_samples": 0, "n_clipped": 0, "pct_clipped": 0.0}
    values = array.array("h")
    values.frombytes(pcm)
    n_clipped = sum(1 for v in values if v == _INT16_MAX or v == _INT16_MIN)
    n = len(values)
    return {
        "n_samples": n,
        "n_clipped": n_clipped,
        "pct_clipped": round(100.0 * n_clipped / n, 2) if n else 0.0,
    }


async def _run_subprocess(cmd: list[str], *, input_bytes: bytes | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate(input=input_bytes)
    return stdout or b""


async def play_pcm(pcm: bytes, *, device: str, sample_rate: int = SAMPLE_RATE) -> None:
    await _run_subprocess(
        ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate),
         "-c", "1", "-D", device, "-"],
        input_bytes=pcm,
    )


async def capture_pcm(*, duration_s: float, device: str = CAPTURE_DEVICE,
                       sample_rate: int = SAMPLE_RATE) -> bytes:
    return await _run_subprocess(
        ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate),
         "-c", "1", "-D", device, "-d", str(math.ceil(duration_s))],
    )


async def run_trial(
    *, feed_reference: bool, gain: float, signal_duration_s: float, amplitude: float = 0.5
) -> dict:
    signal_pcm = build_test_signal(duration_s=signal_duration_s, amplitude=amplitude)
    total_capture_s = PRE_ROLL_S + signal_duration_s + TAIL_MARGIN_S

    capture_task = asyncio.create_task(capture_pcm(duration_s=total_capture_s))
    await asyncio.sleep(PRE_ROLL_S)  # let arecord actually start before playback begins

    # R0081 -- what is ACTUALLY fed to plug:respeaker's playback side,
    # computed BEFORE playback so it can be measured/reported regardless
    # of what happens acoustically. speaker_pcm (played to the real
    # speaker) is NEVER gain-adjusted -- only the reference copy is, so
    # the real acoustic stimulus stays identical across --gain values for
    # a fixed --amplitude, which is what makes an off/on/on+gain
    # comparison meaningful in the first place.
    speaker_pcm = signal_pcm
    ref_pre_gain_pcm = signal_pcm  # what apply_gain() receives, before scaling
    ref_post_gain_pcm = apply_gain(signal_pcm, gain) if feed_reference else b""

    play_tasks = [asyncio.create_task(play_pcm(speaker_pcm, device=SPEAKER_DEVICE))]
    if feed_reference:
        play_tasks.append(
            asyncio.create_task(play_pcm(ref_post_gain_pcm, device=REFERENCE_DEVICE))
        )
    await asyncio.gather(*play_tasks)

    captured = await capture_task

    # Align: the signal starts PRE_ROLL_S into the capture -- slice the
    # matching window out, so cross_correlate_pcm's own "both start at
    # t=0" assumption (R0053/R0055's documented calling convention) is
    # actually satisfied here, deliberately avoiding the old probe's own
    # known pitfall.
    pre_roll_samples = int(PRE_ROLL_S * SAMPLE_RATE)
    window_samples = int((signal_duration_s + TAIL_MARGIN_S) * SAMPLE_RATE)
    start_byte = pre_roll_samples * 2
    end_byte = start_byte + window_samples * 2
    mic_window = captured[start_byte:end_byte]

    quiet_before = captured[: pre_roll_samples * 2]

    corr = cross_correlate_pcm(
        signal_pcm, mic_window, sample_rate=SAMPLE_RATE, max_lag_ms=300.0
    )

    # R0081 -- clipping/gain-chain visibility, requested explicitly:
    # exact peak/RMS of the speaker signal, the reference BEFORE gain
    # (identical to the speaker signal by construction), the reference
    # AFTER gain (what was actually sent to plug:respeaker), and how many
    # samples of that post-gain reference landed on the int16 saturation
    # boundary. Computed for every trial, not just gain != 1.0 ones, so a
    # future run can never silently miss this.
    ref_clip = clip_stats(ref_post_gain_pcm) if feed_reference else {
        "n_samples": 0, "n_clipped": 0, "pct_clipped": 0.0,
    }

    return {
        "feed_reference": feed_reference,
        "gain": gain if feed_reference else None,
        "amplitude": amplitude,
        "signal_pcm": signal_pcm,
        "mic_window": mic_window,
        "captured_full": captured,
        "quiet_before_rms": round(_rms(quiet_before), 1),
        "signal_rms": round(_rms(signal_pcm), 1),
        "mic_window_rms": round(_rms(mic_window), 1),
        "mic_window_peak": _peak(mic_window),
        "correlation": corr,
        # -- new: gain-chain / clipping diagnostics --
        "speaker_pcm_rms": round(_rms(speaker_pcm), 1),
        "speaker_pcm_peak": _peak(speaker_pcm),
        "reference_pre_gain_rms": round(_rms(ref_pre_gain_pcm), 1),
        "reference_pre_gain_peak": _peak(ref_pre_gain_pcm),
        "reference_post_gain_rms": round(_rms(ref_post_gain_pcm), 1) if feed_reference else None,
        "reference_post_gain_peak": _peak(ref_post_gain_pcm) if feed_reference else None,
        "reference_clipped_samples": ref_clip["n_clipped"],
        "reference_clipped_percent": ref_clip["pct_clipped"],
    }


def _attenuation_db_value(reference_rms: float, mic_rms: float) -> float | None:
    if reference_rms <= 0 or mic_rms <= 0:
        return None
    return 20.0 * math.log10(mic_rms / reference_rms)


def _attenuation_db(reference_rms: float, mic_rms: float) -> str:
    value = _attenuation_db_value(reference_rms, mic_rms)
    if value is None:
        return "n/a (silence)"
    return f"{value:.2f} dB (mic relative to fed reference)"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--condition", choices=["off", "on"], required=True,
        help="'off': nothing fed to plug:respeaker (reference OFF, upper-bound leakage). "
        "'on': the same deterministic signal is ALSO fed to plug:respeaker as a far-end "
        "reference (exactly what AecReferenceFeeder does in production).",
    )
    p.add_argument(
        "--gain", type=float, default=1.0,
        help="Linear gain applied to the reference PCM before feeding it (only meaningful "
        "with --condition on). Default 1.0 = unscaled = exactly today's accepted "
        "simple_conversation.py behavior (gain_source=None). Test the default FIRST. "
        "A large gain WILL clip unless --amplitude is lowered correspondingly -- this "
        "script always reports reference_clipped_percent so clipping is never silent; "
        "if it is non-zero, the run's attenuation/correlation numbers are NOT valid "
        "evidence about the gain hypothesis (see this module's own docstring).",
    )
    p.add_argument(
        "--amplitude", type=float, default=0.5,
        help="Peak amplitude of the test tone as a fraction of full scale (default 0.5, "
        "the original script's fixed value). Lower this for a large --gain to keep the "
        "post-gain reference within int16 range without clipping -- e.g. --gain 10.0 "
        "needs --amplitude <= 0.05 for zero clipping headroom to the exact ceiling "
        "(0.05 * 10.0 = 0.5, the same peak the default unboosted case already uses).",
    )
    p.add_argument(
        "--duration", type=float, default=3.0,
        help="Test signal duration in seconds (default 3.0).",
    )
    p.add_argument(
        "--repeats", type=int, default=1,
        help="Run the trial this many times back to back (same condition/gain/amplitude) "
        "and report mean/min/max across the repeats, not just a single sample. "
        "Recommended >= 3 before treating small differences as diagnostic.",
    )
    p.add_argument(
        "--label", default=None,
        help="Optional label for the saved WAV files (default: derived from --condition/--gain).",
    )
    return p.parse_args()


def _mean_min_max(values: list[float]) -> str:
    if not values:
        return "n/a"
    mean = sum(values) / len(values)
    return f"mean={mean:.2f} min={min(values):.2f} max={max(values):.2f} (n={len(values)})"


async def main() -> int:
    args = parse_args()
    label = args.label or f"{args.condition}_gain{args.gain:g}_amp{args.amplitude:g}"
    feed_reference = args.condition == "on"

    print(f"NeXa R0081 — direct AEC reference/capture diagnostic ({args.condition})")
    print(f"  capture device: {CAPTURE_DEVICE}")
    print(f"  speaker device: {SPEAKER_DEVICE}")
    if feed_reference:
        print(f"  reference device: {REFERENCE_DEVICE}  gain={args.gain}  "
              f"amplitude={args.amplitude}")
    print(f"  signal duration: {args.duration}s  pre-roll: {PRE_ROLL_S}s  "
          f"tail margin: {TAIL_MARGIN_S}s  repeats: {args.repeats}")
    print("  playing deterministic tone sequence (500/1000/2000 Hz) -- no speech, "
          "no privacy concern...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(args.repeats):
        if args.repeats > 1:
            print(f"\n  -- trial {i + 1}/{args.repeats} --")
        result = await run_trial(
            feed_reference=feed_reference,
            gain=args.gain,
            signal_duration_s=args.duration,
            amplitude=args.amplitude,
        )
        results.append(result)

        trial_label = label if args.repeats == 1 else f"{label}_trial{i + 1}"
        signal_path = OUT_DIR / f"{trial_label}_signal.wav"
        mic_path = OUT_DIR / f"{trial_label}_mic.wav"
        _write_wav(signal_path, result["signal_pcm"], sample_rate=SAMPLE_RATE)
        _write_wav(mic_path, result["mic_window"], sample_rate=SAMPLE_RATE)
        result["_signal_path"] = signal_path
        result["_mic_path"] = mic_path

        # Reference RMS for the attenuation calc: use the ACTUAL post-gain
        # reference sent to plug:respeaker when one was fed (more precise
        # than the speaker-fed signal once --gain != 1.0), falling back to
        # signal_rms for --condition off (no reference exists at all).
        atten_ref_rms = (
            result["reference_post_gain_rms"] if feed_reference else result["signal_rms"]
        )
        corr = result["correlation"]

        print()
        print("  RESULT")
        print(f"    quiet_before_rms (room noise floor)       : {result['quiet_before_rms']}")
        print(f"    speaker_pcm_rms / peak (actually played)  : "
              f"{result['speaker_pcm_rms']} / {result['speaker_pcm_peak']}")
        if feed_reference:
            print(f"    reference_pre_gain_rms / peak             : "
                  f"{result['reference_pre_gain_rms']} / {result['reference_pre_gain_peak']}")
            print(f"    reference_post_gain_rms / peak (sent)     : "
                  f"{result['reference_post_gain_rms']} / {result['reference_post_gain_peak']}")
            print(f"    reference_clipped_samples / percent       : "
                  f"{result['reference_clipped_samples']} / {result['reference_clipped_percent']}%")
            if result["reference_clipped_percent"] > 0:
                print("    *** WARNING: post-gain reference is CLIPPED. attenuation/"
                      "correlation numbers below are NOT valid evidence about the gain "
                      "hypothesis -- rerun with a lower --amplitude. ***")
        print(f"    mic_window_rms (captured, during signal)  : {result['mic_window_rms']}")
        print(f"    mic_window_peak                           : {result['mic_window_peak']}")
        atten_str = _attenuation_db(atten_ref_rms, result["mic_window_rms"])
        print(f"    attenuation vs {'reference' if feed_reference else 'speaker signal'} "
              f"(ERLE-like, NOT certified) : {atten_str}")
        print(f"    cross-correlation best_lag_ms              : {corr['best_lag_ms']}")
        print(f"    cross-correlation normalized_correlation   : {corr['normalized_correlation']}")
        print(f"    saved: {signal_path}")
        print(f"    saved: {mic_path}")

    if args.repeats > 1:
        atten_values = [
            v
            for r in results
            if (
                v := _attenuation_db_value(
                    (r["reference_post_gain_rms"] if feed_reference else r["signal_rms"]),
                    r["mic_window_rms"],
                )
            )
            is not None
        ]
        rms_values = [r["mic_window_rms"] for r in results]
        peak_values = [r["mic_window_peak"] for r in results]
        corr_values = [r["correlation"]["normalized_correlation"] for r in results]
        lag_values = [r["correlation"]["best_lag_ms"] for r in results]
        clip_pcts = [r["reference_clipped_percent"] for r in results] if feed_reference else []

        print()
        print(f"  REPEATABILITY SUMMARY ({args.repeats} trials, {args.condition}, "
              f"gain={args.gain}, amplitude={args.amplitude})")
        print(f"    mic_window_rms          : {_mean_min_max(rms_values)}")
        print(f"    mic_window_peak         : {_mean_min_max([float(v) for v in peak_values])}")
        print(f"    attenuation_db          : {_mean_min_max(atten_values)}")
        print(f"    normalized_correlation  : {_mean_min_max(corr_values)}")
        print(f"    best_lag_ms             : {_mean_min_max(lag_values)}")
        if feed_reference:
            print(f"    reference_clipped_pct   : {_mean_min_max(clip_pcts)}")
            if any(p > 0 for p in clip_pcts):
                print("    *** at least one trial in this repeat set was CLIPPED -- "
                      "do not use this set as gain-compensation evidence. ***")

    print()
    print("Run the OTHER --condition (off vs on) with the SAME physical volume/room/mic "
          "position for a directly comparable pair. See this script's own module "
          "docstring for the full protocol, including the --amplitude guidance for a "
          "non-clipped high-gain test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
