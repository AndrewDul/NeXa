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

Run ``--condition off`` then ``--condition on`` back to back (same
physical speaker volume, same room, same mic position) for a directly
comparable pair. Repeat 3x per condition recommended — real acoustic
measurements vary run to run; a single sample is not conclusive.
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


def build_test_signal(*, duration_s: float = 3.0, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A short, deterministic, non-speech test signal: three pure tones
    in sequence (500Hz, 1000Hz, 2000Hz), each with a linear fade-in/out to
    avoid clicks. Nothing here could be mistaken for recorded human
    speech -- no privacy concern, fully reproducible."""
    tones_hz = (500.0, 1000.0, 2000.0)
    n_total = int(duration_s * sample_rate)
    n_per_tone = n_total // len(tones_hz)
    fade_n = max(1, int(0.02 * sample_rate))  # 20ms fade
    samples = array.array("h")
    for tone_hz in tones_hz:
        for i in range(n_per_tone):
            t = i / sample_rate
            amp = 0.5
            if i < fade_n:
                amp *= i / fade_n
            elif i > n_per_tone - fade_n:
                amp *= (n_per_tone - i) / fade_n
            value = int(amp * 32767 * math.sin(2 * math.pi * tone_hz * t))
            samples.append(value)
    return samples.tobytes()


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


async def run_trial(*, feed_reference: bool, gain: float, signal_duration_s: float) -> dict:
    signal_pcm = build_test_signal(duration_s=signal_duration_s)
    total_capture_s = PRE_ROLL_S + signal_duration_s + TAIL_MARGIN_S

    capture_task = asyncio.create_task(capture_pcm(duration_s=total_capture_s))
    await asyncio.sleep(PRE_ROLL_S)  # let arecord actually start before playback begins

    play_tasks = [asyncio.create_task(play_pcm(signal_pcm, device=SPEAKER_DEVICE))]
    if feed_reference:
        ref_pcm = apply_gain(signal_pcm, gain)
        play_tasks.append(asyncio.create_task(play_pcm(ref_pcm, device=REFERENCE_DEVICE)))
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

    return {
        "feed_reference": feed_reference,
        "gain": gain if feed_reference else None,
        "signal_pcm": signal_pcm,
        "mic_window": mic_window,
        "captured_full": captured,
        "quiet_before_rms": round(_rms(quiet_before), 1),
        "signal_rms": round(_rms(signal_pcm), 1),
        "mic_window_rms": round(_rms(mic_window), 1),
        "mic_window_peak": _peak(mic_window),
        "correlation": corr,
    }


def _attenuation_db(reference_rms: float, mic_rms: float) -> str:
    if reference_rms <= 0 or mic_rms <= 0:
        return "n/a (silence)"
    return f"{20.0 * math.log10(mic_rms / reference_rms):.2f} dB (mic relative to fed reference)"


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
        "simple_conversation.py behavior (gain_source=None). Test the default FIRST.",
    )
    p.add_argument(
        "--duration", type=float, default=3.0,
        help="Test signal duration in seconds (default 3.0).",
    )
    p.add_argument(
        "--label", default=None,
        help="Optional label for the saved WAV files (default: derived from --condition/--gain).",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    label = args.label or f"{args.condition}_gain{args.gain:g}"

    print(f"NeXa R0081 — direct AEC reference/capture diagnostic ({args.condition})")
    print(f"  capture device: {CAPTURE_DEVICE}")
    print(f"  speaker device: {SPEAKER_DEVICE}")
    if args.condition == "on":
        print(f"  reference device: {REFERENCE_DEVICE}  gain={args.gain}")
    print(f"  signal duration: {args.duration}s  pre-roll: {PRE_ROLL_S}s  "
          f"tail margin: {TAIL_MARGIN_S}s")
    print("  playing deterministic tone sequence (500/1000/2000 Hz) -- no speech, "
          "no privacy concern...")

    result = await run_trial(
        feed_reference=(args.condition == "on"),
        gain=args.gain,
        signal_duration_s=args.duration,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    signal_path = OUT_DIR / f"{label}_signal.wav"
    mic_path = OUT_DIR / f"{label}_mic.wav"
    _write_wav(signal_path, result["signal_pcm"], sample_rate=SAMPLE_RATE)
    _write_wav(mic_path, result["mic_window"], sample_rate=SAMPLE_RATE)

    print()
    print("RESULT")
    print(f"  quiet_before_rms (room noise floor)      : {result['quiet_before_rms']}")
    print(f"  signal_rms (fed to speaker, digital)      : {result['signal_rms']}")
    print(f"  mic_window_rms (captured, during signal)  : {result['mic_window_rms']}")
    print(f"  mic_window_peak                           : {result['mic_window_peak']}")
    print(f"  attenuation (ERLE-like, NOT a certified"
          f" AEC measurement) : "
          f"{_attenuation_db(result['signal_rms'], result['mic_window_rms'])}")
    corr = result["correlation"]
    print(f"  cross-correlation best_lag_ms              : {corr['best_lag_ms']}")
    print(f"  cross-correlation normalized_correlation    : {corr['normalized_correlation']}")
    print()
    print(f"  saved: {signal_path}")
    print(f"  saved: {mic_path}")
    print()
    print("Run the OTHER --condition (off vs on) with the SAME physical volume/room/mic "
          "position for a directly comparable pair. See this script's own module "
          "docstring for the full protocol.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
