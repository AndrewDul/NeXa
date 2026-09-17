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
This script isolates the hardware-level questions conversational evidence
cannot answer on its own:

    A. Is the "respeaker" ALSA capture endpoint actually being reduced by
       the XVF3800's onboard AEC when a far-end reference is fed to
       ``plug:respeaker``? **RESOLVED, CONFIRMED YES** (§ results below,
       and R0081's report §7/§7b) — OFF→ON gives real, reproducible,
       double-digit-percent RMS reduction at multiple signal levels.

    B. What is the approximate playback→capture and reference→capture
       lag? **STILL UNRESOLVED.** The original tone stimulus is periodic
       and produced ambiguous/boundary-saturated ``best_lag_ms`` values —
       see the dedicated docstring section below and ``--stimulus mls``,
       added this revision specifically to fix this.

    C. Quantify: capture RMS with reference OFF vs ON, attenuation in dB,
       cross-correlation peak/lag. Done, repeatedly, at two signal levels
       (§ below) — AEC effectiveness is confirmed but level-dependent.

    D. Does scaling the reference (R0053's gain-coherence idea, or a
       direct compensation for the firmware's confirmed ``AEC_FAR_EXTGAIN
       = -20dB``) help? A naive ×10 (+20dB) compensation was **tested
       twice**: once invalidated by clipping (prior revision), then
       retested cleanly at ``--amplitude 0.05`` (no clipping) and found to
       make the residual **dramatically WORSE** (~4.4× larger than
       gain=1.0, worse even than reference OFF). The blunt "invert the
       firmware's -20dB" hypothesis is **REFUTED**. ``--sweep`` then found
       a real, material candidate near baseline: **gain=0.5 gave the
       LOWEST residual RMS of the six sweep points** (~30% lower than
       gain=1.0) — but the same run showed a suspicious elevated
       ``quiet_before_rms`` on several points' FIRST trial only (recovering
       by the 2nd/3rd trial at that same gain), an open confound that
       could inflate or distort the sweep's own comparison. ``--confirm``
       runs a COUNTERBALANCED A/B design (default gain=0.5 vs gain=1.0,
       longer settle, optional post-settle gap): BOTH an A-first AND a
       B-first alternating sequence, so neither gain is stuck only in the
       "goes first" or "goes second" role — an earlier single-sequence
       version of this mode risked over-claiming an order-independent
       result from an inherently order-confounded design; fixed this
       revision, before that risk became live. **Not yet run live.**

    E. Timing: an offline audit of the 3 already-captured OFF-condition
       MLS WAVs (this revision, no new hardware access) found the 3
       trials' ``best_lag_ms`` values are genuinely BIMODAL (2 trials at
       ~134.4ms, 1 trial at ~102.5ms — an EXACT 512-sample/32.000ms
       separation, with essentially zero correlation at the "other"
       trial's peak lag in every case, not just a weaker secondary peak)
       — not simple noise/jitter around one true value. The correlation
       magnitude is also uniformly low (~0.06-0.07) across all 3 trials;
       an offline spectral comparison (this revision) shows the capture
       is heavily reshaped relative to the source (high-pass consistent
       with the confirmed ``AEC_HPFONOFF`` 125Hz filter, plus a shift of
       energy out of 4-8kHz into 1-4kHz, nearly IDENTICAL across all 3
       trials) — a plausible, evidence-backed explanation for the low
       correlation MAGNITUDE specifically (the device's own ASR-oriented
       processing measurably decorrelates a raw broadband source from its
       processed capture), while the exact-512-sample BIMODAL LAG split is
       a separate, still-open question, with a leading (not yet directly
       confirmed) hypothesis: an ALSA capture period/buffer-boundary
       quantization effect. See R0081's report for the full writeup and
       the exact read-only command that would directly confirm or refute
       the buffer-boundary hypothesis against this device's real
       configured ALSA parameters.

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

**Update (this revision): periodic-tone stimulus ambiguity, confirmed
live.** The original 500/1000/2000Hz tone stimulus is periodic within
each of its three segments; a periodic signal has MANY equally-valid
correlation peaks spaced by its own period, and a bounded lag search can
lock onto the WRONG one — exactly what live operator evidence showed
(``best_lag_ms`` jumping to values near the ±300ms search boundary, e.g.
approximately -299ms, a textbook signature of the search saturating at
its own boundary rather than finding a genuine acoustic delay).
``--stimulus mls`` (new, this revision) replaces the tone sequence with a
deterministic 16-bit maximum-length-sequence broadband pseudonoise chip
stream, whose autocorrelation is a single sharp peak with low sidelobes —
see ``_mls16_bits``'s own docstring. **Prefer ``--stimulus mls`` for ANY
claim about ``best_lag_ms``**; the default ``tones`` stimulus remains
fine for RMS/attenuation-only comparisons (e.g. ``--sweep``), where
periodicity does not matter.

## Method

Deterministic test signal (either a short sequence of pure tones, or an
MLS pseudonoise chip stream — see ``--stimulus`` — generated in Python,
no external fixture file, no speech, nothing that could be mistaken for a
real utterance): played to the REAL physical speaker (``plug:usb_speaker``)
while simultaneously capturing from ``plug:respeaker`` (the SAME endpoint
production VAD analyzes). In the "reference ON" condition, the identical
PCM is ALSO fed to ``plug:respeaker``'s *playback* direction at the same
time (exactly what ``AecReferenceFeeder`` does in production) — a
standard USB Audio Class device exposes independent playback and capture
directions over the same USB interface, so playing a reference INTO
``plug:respeaker`` while simultaneously recording FROM ``plug:respeaker``
is the same duplex pattern the accepted production pipeline already
relies on (``AecReferenceFeeder`` writes to ``plug:respeaker`` while
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
    # trial. Live result (this amplitude, non-clipped): gain=10.0 makes
    # the residual ~4.4x WORSE than gain=1.0 -- the naive "invert the
    # firmware's -20dB" hypothesis is REFUTED, do not repeat this exact
    # test expecting a different answer.
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --condition on --gain 10.0 --amplitude 0.05 --repeats 3

    # narrow gain sweep around baseline (off, 0.25x, 0.5x, 1.0x, 2.0x,
    # 4.0x), fixed amplitude=0.05 (safe across this whole range, no
    # clipping), 3 repeats per point, automated in ONE invocation instead
    # of many manual commands -- see --sweep's own help text and
    # run_condition()/​_run_sweep() for the settle/order rationale
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --sweep

    # timing-only stimulus (MLS pseudonoise instead of periodic tones) --
    # establish a STABLE acoustic speaker->capture lag in reference-OFF
    # mode FIRST, repeatably, before ever drawing an ON-condition timing
    # conclusion (the tone stimulus is not reliable for this -- see the
    # best_lag_ms docstring section above)
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --condition off --stimulus mls --repeats 3

    # counterbalanced A/B confirmation: gain=0.5 (the sweep's
    # lowest-residual point) vs gain=1.0 (today's production default).
    # Runs BOTH an ABABAB (A-first) AND a BABABA (B-first) sequence in
    # one invocation (3 cycles each = 6 trials per gain total), with a
    # longer settle (3s vs --sweep's 1.0s), to test whether the sweep's
    # own gain=0.5 advantage survives order/carryover control in BOTH
    # directions -- not just one alternating pass
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --confirm

    # same, plus an explicit post-settle silent gap -- opt in if a
    # settle point's first measured trial keeps showing an elevated
    # quiet_before_rms versus its own later trials (see this module's
    # own docstring section E and run_condition()'s own docstring)
    python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
        --confirm --post-settle-gap 0.5

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

#: The fixed, documented order and gain values for --sweep, per R0081's
#: explicit instruction: OFF, then 0.25x/0.5x/1.0x/2.0x/4.0x, ascending.
#: Fixed (not randomized) because each point gets its own settle/warmup
#: at ITS gain immediately before measurement (see run_condition), which
#: is what actually re-establishes steady AEC state -- a residual "memory"
#: from the previous point would only affect the discarded settle window,
#: not the measured trials, so a simple documented fixed order is
#: sufficient without adding randomization complexity.
SWEEP_GAIN_POINTS: list[tuple[str, bool, float | None]] = [
    ("off", False, None),
    ("gain0.25", True, 0.25),
    ("gain0.5", True, 0.5),
    ("gain1.0", True, 1.0),
    ("gain2.0", True, 2.0),
    ("gain4.0", True, 4.0),
]


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

    Periodic within each tone segment -- fine for RMS/attenuation
    comparisons, NOT reliable for best_lag_ms claims (see this module's
    own docstring section on this; use ``build_mls_signal``/
    ``--stimulus mls`` for timing instead).

    ``amplitude``: peak amplitude as a fraction of full scale (0.0-1.0),
    default 0.5 (unchanged from the original script). Lower this when
    testing a large ``--gain`` (see ``run_trial``'s own clip-detection
    diagnostic and this module's docstring) -- e.g. ``amplitude=0.05``
    leaves headroom for a full, undistorted ×10 (+20dB) reference boost
    without saturating int16 (0.05 * 10 = 0.5, the same peak the DEFAULT
    unboosted signal already uses safely). The digital signal fed to the
    physical speaker is generated at this SAME amplitude in every
    condition (off/on/on+gain) for one run, so the real acoustic stimulus
    stays comparable across a same-amplitude A/B pair -- only ``--gain``
    changes what's fed to the reference."""
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


#: 16-bit maximal-length Fibonacci LFSR (period 2**16-1 = 65535 chips),
#: taps 16/14/13/11 (feedback polynomial x^16+x^14+x^13+x^11+1) -- the
#: standard, independently-verifiable example from Wikipedia's "Linear-
#: feedback shift register" article, used here (rather than a tap set
#: picked ad hoc for this script) because its maximal-length property is
#: well established and checkable. Fixed non-zero seed makes the sequence
#: fully reproducible run to run -- any nonzero seed produces the same
#: cycle, just a different starting phase, which does not matter here.
_MLS16_SEED = 0xACE1
_MLS16_PERIOD = 65535  # 2**16 - 1


def _mls16_bits(n_bits: int) -> list[int]:
    """R0081 (new) -- deterministic broadband pseudonoise chip sequence
    (list of 0/1), used as a timing-diagnostic stimulus alternative to
    the original periodic 3-tone signal. A periodic tone has many
    equally-valid correlation peaks spaced by its own period, which can
    make a bounded lag search lock onto the WRONG cycle -- exactly what
    R0081's operator observed (best_lag_ms jumping to values near the
    search boundary). An MLS's own autocorrelation is a single sharp peak
    near zero lag with low, flat sidelobes elsewhere, so
    cross_correlate_pcm's bounded search has a much better chance of
    locking onto the TRUE acoustic delay instead of a spurious tone-period
    alias.

    ``n_bits`` must stay at or below ``_MLS16_PERIOD`` (65535) so a single
    capture window never wraps around and reintroduces the sequence's own
    periodicity -- raises ``ValueError`` rather than silently truncating
    or wrapping, since a caller relying on a longer, non-periodic window
    would otherwise get a silently invalid timing measurement."""
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
    *, duration_s: float = 3.0, sample_rate: int = SAMPLE_RATE, amplitude: float = 0.5
) -> bytes:
    """Deterministic broadband pseudonoise (16-bit MLS, one chip per
    sample) -- see ``_mls16_bits`` for why this exists. Same fade-in/out
    and amplitude convention as ``build_test_signal`` so the two are
    drop-in alternatives wherever ``run_trial``/``build_signal`` is used.
    A short 5ms fade suffices here (broadband signal, not a discrete
    tone) -- avoids only the hard on/off click at the very start/end."""
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


def build_signal(
    stimulus: str, *, duration_s: float, amplitude: float, sample_rate: int = SAMPLE_RATE
) -> bytes:
    """Dispatch to the requested deterministic stimulus generator. See
    this module's docstring for when to use which."""
    if stimulus == "tones":
        return build_test_signal(
            duration_s=duration_s, sample_rate=sample_rate, amplitude=amplitude
        )
    if stimulus == "mls":
        return build_mls_signal(
            duration_s=duration_s, sample_rate=sample_rate, amplitude=amplitude
        )
    raise ValueError(f"unknown stimulus: {stimulus!r}")


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
    *, feed_reference: bool, gain: float, signal_duration_s: float, amplitude: float = 0.5,
    stimulus: str = "tones",
) -> dict:
    signal_pcm = build_signal(
        stimulus, duration_s=signal_duration_s, amplitude=amplitude
    )
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
        "stimulus": stimulus,
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


def _mean_min_max(values: list[float]) -> str:
    if not values:
        return "n/a"
    mean = sum(values) / len(values)
    return f"mean={mean:.2f} min={min(values):.2f} max={max(values):.2f} (n={len(values)})"


async def run_condition(
    *,
    feed_reference: bool,
    gain: float,
    amplitude: float,
    duration_s: float,
    repeats: int,
    label: str,
    stimulus: str = "tones",
    settle_s: float = 0.0,
    post_settle_gap_s: float = 0.0,
) -> list[dict]:
    """Run ``repeats`` trials of ONE (feed_reference, gain) condition:
    an optional settle/warmup, an optional silent gap, then the measured
    repeat loop, printing a RESULT block per trial and a REPEATABILITY
    SUMMARY when repeats > 1.

    Extracted from the original single-condition ``main()`` body so
    ``--sweep``/``--confirm`` can call this once per point/block without
    duplicating the printing/aggregation logic (single-run, sweep, and
    confirm modes all produce output in exactly this same format).

    ``settle_s``: if > 0, plays the speaker signal (and, if
    ``feed_reference``, the reference at ``gain``) for this many seconds
    BEFORE the measured trials, without capturing/measuring anything --
    gives the XVF3800's adaptive filter time to reconverge to a NEWLY
    changed reference gain before the first measured trial, mitigating
    carryover from whatever condition ran immediately before this one in
    the same process (see SWEEP_GAIN_POINTS's own comment for why a
    fixed order plus this settle step was chosen over randomizing trial
    order).

    ``post_settle_gap_s`` (new): if > 0, an ADDITIONAL silent pause (no
    playback at all, on either device) inserted after the settle step and
    before the first measured trial's own pre-roll/capture begins. Added
    per R0081's own live evidence: the first measured trial after a
    settle at a NEWLY changed gain sometimes showed an elevated
    ``quiet_before_rms`` (room floor) relative to the 2nd/3rd trials at
    the SAME gain, consistent with (among other untested possibilities --
    this script does not assume which) acoustic reverberation or AEC
    adaptation residue from the settle step not having fully decayed
    within the fixed 1.0s pre-roll alone. Zero by default -- opt-in,
    never changes existing --sweep behavior unless explicitly passed."""
    if settle_s > 0.0:
        print(f"  (settling {settle_s:g}s at gain="
              f"{gain if feed_reference else 'n/a'} before measuring, to let the AEC "
              f"reconverge to this condition...)")
        settle_signal = build_signal(stimulus, duration_s=settle_s, amplitude=amplitude)
        settle_tasks = [asyncio.create_task(play_pcm(settle_signal, device=SPEAKER_DEVICE))]
        if feed_reference:
            settle_ref = apply_gain(settle_signal, gain)
            settle_tasks.append(asyncio.create_task(play_pcm(settle_ref, device=REFERENCE_DEVICE)))
        await asyncio.gather(*settle_tasks)

    if post_settle_gap_s > 0.0:
        print(f"  (post-settle silent gap: {post_settle_gap_s:g}s, no playback on either "
              f"device, before the first measured trial's own pre-roll...)")
        await asyncio.sleep(post_settle_gap_s)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i in range(repeats):
        if repeats > 1:
            print(f"\n  -- trial {i + 1}/{repeats} --")
        result = await run_trial(
            feed_reference=feed_reference,
            gain=gain,
            signal_duration_s=duration_s,
            amplitude=amplitude,
            stimulus=stimulus,
        )
        results.append(result)

        # R0081 -- ALWAYS suffix with the trial number (even for repeats=1),
        # not just when repeats > 1: a prior version omitted this suffix for
        # single-trial runs, which combined with the stimulus NOT being in
        # the label either meant a tones run and an mls run at the same
        # condition/gain/amplitude silently overwrote each other's WAVs.
        # `label` itself is now always built to include the stimulus (see
        # _run_single/_run_sweep/_run_confirm) so together these make every
        # saved filename encode condition/gain/amplitude/stimulus/trial.
        trial_label = f"{label}_trial{i + 1}"
        signal_path = OUT_DIR / f"{trial_label}_signal.wav"
        mic_path = OUT_DIR / f"{trial_label}_mic.wav"
        _write_wav(signal_path, result["signal_pcm"], sample_rate=SAMPLE_RATE)
        _write_wav(mic_path, result["mic_window"], sample_rate=SAMPLE_RATE)

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

    if repeats > 1:
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
        quiet_values = [r["quiet_before_rms"] for r in results]
        clip_pcts = [r["reference_clipped_percent"] for r in results] if feed_reference else []

        print()
        print(f"  REPEATABILITY SUMMARY ({repeats} trials, "
              f"{'on' if feed_reference else 'off'}, "
              f"gain={gain if feed_reference else 'n/a'}, amplitude={amplitude})")
        # R0081 -- quiet_before_rms per trial is ALWAYS printed above in each
        # trial's own RESULT block; also summarized here (new) because live
        # evidence showed it sometimes elevated specifically on a settle
        # point's first trial -- worth seeing at a glance, not just having
        # to scroll back through individual trial blocks.
        print(f"    quiet_before_rms        : {_mean_min_max(quiet_values)}")
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

    return results


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--condition", choices=["off", "on"], default=None,
        help="Required unless --sweep is passed. 'off': nothing fed to plug:respeaker "
        "(reference OFF, upper-bound leakage). 'on': the same deterministic signal is ALSO "
        "fed to plug:respeaker as a far-end reference (exactly what AecReferenceFeeder does "
        "in production).",
    )
    p.add_argument(
        "--gain", type=float, default=1.0,
        help="Linear gain applied to the reference PCM before feeding it (only meaningful "
        "with --condition on; ignored with --sweep, which uses its own fixed gain list). "
        "Default 1.0 = unscaled = exactly today's accepted simple_conversation.py behavior "
        "(gain_source=None). Test the default FIRST. A large gain WILL clip unless "
        "--amplitude is lowered correspondingly -- this script always reports "
        "reference_clipped_percent so clipping is never silent; if it is non-zero, the run's "
        "attenuation/correlation numbers are NOT valid evidence about the gain hypothesis "
        "(see this module's own docstring). Live, non-clipped result: gain=10.0 makes the "
        "residual dramatically WORSE than gain=1.0 -- the naive +20dB-compensation "
        "hypothesis is REFUTED; use --sweep to test smaller, more plausible gains instead.",
    )
    p.add_argument(
        "--amplitude", type=float, default=None,
        help="Peak amplitude of the test tone as a fraction of full scale. Default 0.5 in "
        "normal mode (the original script's fixed value), or 0.05 automatically with "
        "--sweep (safe across the whole 0.25x-4.0x sweep range without clipping). Lower this "
        "for a large --gain to keep the post-gain reference within int16 range -- e.g. "
        "--gain 10.0 needs --amplitude <= 0.05 for zero clipping headroom to the ceiling "
        "(0.05 * 10.0 = 0.5, the same peak the default unboosted case already uses).",
    )
    p.add_argument(
        "--duration", type=float, default=3.0,
        help="Test signal duration in seconds (default 3.0). With --stimulus mls, must stay "
        "under ~4.09s (65535 chips / 16kHz) or the sequence wraps and reintroduces "
        "periodicity -- raises an error rather than silently producing an invalid stimulus.",
    )
    p.add_argument(
        "--repeats", type=int, default=None,
        help="Run the trial this many times back to back (same condition/gain/amplitude) "
        "and report mean/min/max across the repeats, not just a single sample. Default 1 in "
        "normal mode, or 3 automatically with --sweep. Recommended >= 3 before treating "
        "small differences as diagnostic.",
    )
    p.add_argument(
        "--stimulus", choices=["tones", "mls"], default="tones",
        help="'tones' (default): the original periodic 500/1000/2000Hz sequence -- fine for "
        "level/RMS/attenuation comparisons (e.g. --sweep) but its periodicity can bias "
        "best_lag_ms (see this module's docstring). 'mls': deterministic 16-bit "
        "maximum-length-sequence broadband pseudonoise with a sharp, unambiguous "
        "autocorrelation peak -- use this for timing/lag measurements, especially "
        "--condition off repeatability runs establishing the acoustic speaker->capture delay.",
    )
    p.add_argument(
        "--sweep", action="store_true",
        help="Run the fixed narrow gain sweep (off, 0.25x, 0.5x, 1.0x, 2.0x, 4.0x) at a "
        "single fixed --amplitude in ONE invocation, automating the operator's requested "
        "protocol instead of many manual commands. Ignores --condition/--gain. Defaults "
        "--repeats to 3 and --amplitude to 0.05 if not explicitly given.",
    )
    p.add_argument(
        "--sweep-settle", type=float, default=1.0,
        help="Seconds of unmeasured playback at each sweep point's OWN gain immediately "
        "before its measured trials, to let the AEC reconverge -- only used with --sweep "
        "(default 1.0s).",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Run a COUNTERBALANCED A/B confirmation between two specific gains (default 0.5 "
        "vs 1.0 -- --sweep's own two lowest-residual points): BOTH an A-first (ABABAB...) AND "
        "a B-first (BABABA...) alternating sequence, in one invocation, under identical "
        "amplitude/settle/gap/duration (--confirm-cycles pairs per sequence, default 3 -- "
        "6 A trials + 6 B trials total across both sequences, each its own single measured "
        "trial after its own settle+gap). A single alternating sequence alone does NOT rule "
        "out order effects (one gain never gets the 'goes first' role) -- running both orders "
        "does. Ignores --condition/--gain/--sweep. Defaults --amplitude to 0.05 if not "
        "explicitly given.",
    )
    p.add_argument(
        "--confirm-gain-a", type=float, default=0.5,
        help="Gain for the 'A' side of --confirm (default 0.5 -- the sweep's lowest-residual "
        "point).",
    )
    p.add_argument(
        "--confirm-gain-b", type=float, default=1.0,
        help="Gain for the 'B' side of --confirm (default 1.0 -- today's production default, "
        "gain_source=None).",
    )
    p.add_argument(
        "--confirm-cycles", type=int, default=3,
        help="Number of A,B pairs to alternate in --confirm (default 3 -- ABABAB, i.e. 3 "
        "measured trials per gain, matching the requested 'at least 3 measured trials per "
        "condition' protocol).",
    )
    p.add_argument(
        "--confirm-settle", type=float, default=3.0,
        help="Seconds of unmeasured playback at EACH block's own gain before its one "
        "measured trial, in --confirm (default 3.0s -- raised from --sweep's 1.0s default "
        "per R0081's own instruction, since the sweep's first-trial-after-a-gain-change "
        "quiet-floor anomaly suggests 1.0s may not always be enough to reconverge/settle).",
    )
    p.add_argument(
        "--post-settle-gap", type=float, default=0.0,
        help="Seconds of ADDITIONAL silence (no playback at all) after the settle step and "
        "before the first measured trial's own pre-roll, for --sweep or --confirm. Default "
        "0.0 (off, unchanged prior behavior) -- opt in (e.g. 0.5) if a settle point's first "
        "measured trial keeps showing an elevated quiet_before_rms versus its own later "
        "trials, to test whether that is a simple decay-time effect.",
    )
    p.add_argument(
        "--label", default=None,
        help="Optional label for the saved WAV files (default: derived from "
        "--condition/--gain/--amplitude/--stimulus, ignored with --sweep/--confirm which "
        "label each point/block themselves).",
    )
    args = p.parse_args()

    if args.sweep and args.confirm:
        p.error("--sweep and --confirm are mutually exclusive")
    if args.repeats is None:
        args.repeats = 3 if args.sweep else 1
    if args.amplitude is None:
        args.amplitude = 0.05 if (args.sweep or args.confirm) else 0.5
    if not args.sweep and not args.confirm and args.condition is None:
        p.error("--condition is required unless --sweep or --confirm is passed")

    return args


async def _run_single(args: argparse.Namespace) -> int:
    feed_reference = args.condition == "on"
    label = (
        args.label
        or f"{args.condition}_gain{args.gain:g}_amp{args.amplitude:g}_{args.stimulus}"
    )

    print(f"NeXa R0081 — direct AEC reference/capture diagnostic ({args.condition})")
    print(f"  capture device: {CAPTURE_DEVICE}")
    print(f"  speaker device: {SPEAKER_DEVICE}")
    if feed_reference:
        print(f"  reference device: {REFERENCE_DEVICE}  gain={args.gain}  "
              f"amplitude={args.amplitude}")
    print(f"  signal duration: {args.duration}s  pre-roll: {PRE_ROLL_S}s  "
          f"tail margin: {TAIL_MARGIN_S}s  repeats: {args.repeats}  stimulus: {args.stimulus}")
    stimulus_desc = (
        "MLS pseudonoise" if args.stimulus == "mls" else "tone sequence (500/1000/2000 Hz)"
    )
    print(f"  playing deterministic {stimulus_desc} -- no speech, no privacy concern...")

    await run_condition(
        feed_reference=feed_reference,
        gain=args.gain,
        amplitude=args.amplitude,
        duration_s=args.duration,
        repeats=args.repeats,
        label=label,
        stimulus=args.stimulus,
    )

    print()
    print("Run the OTHER --condition (off vs on) with the SAME physical volume/room/mic "
          "position for a directly comparable pair. See this script's own module "
          "docstring for the full protocol, including the --amplitude guidance for a "
          "non-clipped high-gain test, --sweep for a narrow gain sweep, and --stimulus mls "
          "for timing/lag measurements.")
    return 0


async def _run_sweep(args: argparse.Namespace) -> int:
    print("NeXa R0081 — narrow reference-gain sweep around baseline (gain=1.0)")
    print(f"  fixed amplitude={args.amplitude} (same acoustic stimulus for every point)")
    print(f"  points, in order: {', '.join(p[0] for p in SWEEP_GAIN_POINTS)}")
    print(f"  repeats per point: {args.repeats}  settle before each point: "
          f"{args.sweep_settle}s  stimulus: {args.stimulus}")
    print("  order rationale: fixed ascending order (not randomized) -- each point gets its "
          "own settle/warmup at the NEW gain immediately before its measured trials, which "
          "is what actually re-establishes steady AEC state regardless of what the previous "
          "point was; a fixed order keeps the run fully reproducible. See SWEEP_GAIN_POINTS's "
          "own comment in this script for the full rationale.")
    print()

    sweep_means: list[tuple[str, float, float]] = []
    for point_label, feed_reference, gain in SWEEP_GAIN_POINTS:
        print(f"=== sweep point: {point_label} ===")
        results = await run_condition(
            feed_reference=feed_reference,
            gain=gain if gain is not None else 1.0,
            amplitude=args.amplitude,
            duration_s=args.duration,
            repeats=args.repeats,
            label=f"sweep_{point_label}_amp{args.amplitude:g}_{args.stimulus}",
            stimulus=args.stimulus,
            settle_s=args.sweep_settle,
            post_settle_gap_s=args.post_settle_gap,
        )
        rms_mean = sum(r["mic_window_rms"] for r in results) / len(results)
        clip_pct_max = max((r["reference_clipped_percent"] for r in results), default=0.0)
        sweep_means.append((point_label, rms_mean, clip_pct_max))
        print()

    print("=== SWEEP SUMMARY (mic_window_rms mean per point, ascending gain order) ===")
    best_label = min(sweep_means, key=lambda x: x[1])[0]
    for point_label, rms_mean, clip_pct_max in sweep_means:
        marker = "  <-- lowest residual" if point_label == best_label else ""
        clip_note = f"  (max clipped {clip_pct_max}%!)" if clip_pct_max > 0 else ""
        print(f"  {point_label:>10s} : mic_window_rms mean = {rms_mean:.2f}{marker}{clip_note}")
    print()
    print("Interpretation: if the minimum sits at or near gain=1.0 (unscaled, today's "
          "production default) and BOTH lower and higher gains are worse, reference "
          "amplitude mismatch is unlikely to be the primary remaining cause of the "
          "residual -- move to timing/alignment as the leading hypothesis (--stimulus mls). "
          "If a modest gain such as 0.5 or 2.0 gives a reproducible, material improvement, "
          "gain calibration remains a live contributor and should be quantified further "
          "before touching production code.")
    return 0


def _confirm_build_sequence(
    *, start: str, cycles: int, a_gain: float, b_gain: float
) -> list[tuple[str, float]]:
    """One alternating A/B sequence of length ``2 * cycles``, starting with
    ``start`` ('A' or 'B'). Used to build BOTH the A-first (ABABAB...) and
    B-first (BABABA...) sequences -- see ``_run_confirm``'s own docstring
    for why a single alternating sequence alone is NOT a counterbalanced
    design (every B is preceded by A and vice versa; there is no run where
    B is the FIRST/predecessor role)."""
    seq: list[tuple[str, float]] = []
    other = "B" if start == "A" else "A"
    for _ in range(cycles):
        seq.append((start, a_gain if start == "A" else b_gain))
        seq.append((other, a_gain if other == "A" else b_gain))
    return seq


async def _run_confirm_sequence(
    *, seq_id: str, sequence: list[tuple[str, float]], args: argparse.Namespace
) -> dict[str, list[dict]]:
    """Run ONE alternating sequence (all its blocks, in order) and return
    its own per-letter result lists. Extracted so ``_run_confirm`` can run
    the A-first and B-first sequences identically and keep their results
    separate as well as combined."""
    per_letter: dict[str, list[dict]] = {"A": [], "B": []}
    for i, (letter, gain) in enumerate(sequence):
        cycle = i // 2 + 1
        print(f"=== confirm {seq_id} block {i + 1}/{len(sequence)}: {letter} (gain={gain:g}, "
              f"cycle {cycle}) ===")
        results = await run_condition(
            feed_reference=True,
            gain=gain,
            amplitude=args.amplitude,
            duration_s=args.duration,
            repeats=1,
            label=f"confirm_{seq_id}_{letter}{cycle}_gain{gain:g}_amp{args.amplitude:g}_{args.stimulus}",
            stimulus=args.stimulus,
            settle_s=args.confirm_settle,
            post_settle_gap_s=args.post_settle_gap,
        )
        per_letter[letter].extend(results)
        print()
    return per_letter


def _print_confirm_block_summary(
    label: str, results: dict[str, list[dict]], a_gain: float, b_gain: float
) -> None:
    a_rms = [r["mic_window_rms"] for r in results["A"]]
    b_rms = [r["mic_window_rms"] for r in results["B"]]
    a_quiet = [r["quiet_before_rms"] for r in results["A"]]
    b_quiet = [r["quiet_before_rms"] for r in results["B"]]
    print(f"  --- {label} ---")
    print(f"    A (gain={a_gain:g}) mic_window_rms  : {_mean_min_max(a_rms)}")
    print(f"    B (gain={b_gain:g}) mic_window_rms  : {_mean_min_max(b_rms)}")
    print(f"    A quiet_before_rms               : {_mean_min_max(a_quiet)}")
    print(f"    B quiet_before_rms               : {_mean_min_max(b_quiet)}")
    print("    paired differences per cycle (A_rms - B_rms; negative = A lower/better):")
    for cycle in range(min(len(a_rms), len(b_rms))):
        diff = a_rms[cycle] - b_rms[cycle]
        print(f"      cycle {cycle + 1}: A={a_rms[cycle]:.2f}  B={b_rms[cycle]:.2f}  "
              f"diff={diff:+.2f}")


async def _run_confirm(args: argparse.Namespace) -> int:
    """Genuinely counterbalanced A/B gain confirmation: runs BOTH an
    A-first (ABABAB...) and a B-first (BABABA...) alternating sequence, in
    one invocation, under identical amplitude/settle/gap/duration and (as
    far as this script can control) identical physical hardware
    conditions. A single alternating sequence alone (the prior version of
    this mode) does NOT rule out order/transition effects: every B is
    always preceded by A, and every A except the very first is always
    preceded by B, so there is no run in which B occupies the
    'predecessor'/first role -- a claim that a result holds "regardless
    of order" was too strong from that design alone. Running the mirrored
    B-first sequence too gives both gains a turn in both the
    predecessor and successor role."""
    a_gain, b_gain = args.confirm_gain_a, args.confirm_gain_b
    seq_a_first = _confirm_build_sequence(
        start="A", cycles=args.confirm_cycles, a_gain=a_gain, b_gain=b_gain
    )
    seq_b_first = _confirm_build_sequence(
        start="B", cycles=args.confirm_cycles, a_gain=a_gain, b_gain=b_gain
    )

    print(f"NeXa R0081 — counterbalanced A/B gain confirmation "
          f"(A=gain{a_gain:g} vs B=gain{b_gain:g})")
    print(f"  fixed amplitude={args.amplitude} "
          f"(same acoustic stimulus for every block, both sequences)")
    print(f"  sequence 1 (A-first): {''.join(letter for letter, _ in seq_a_first)}")
    print(f"  sequence 2 (B-first): {''.join(letter for letter, _ in seq_b_first)}")
    print(f"  {args.confirm_cycles} cycles per sequence, {len(seq_a_first)} blocks per sequence, "
          f"1 measured trial per block -- {2 * args.confirm_cycles} trials per gain total")
    print(f"  settle before each block: {args.confirm_settle}s  "
          f"post-settle silent gap: {args.post_settle_gap}s  stimulus: {args.stimulus}")
    print("  counterbalancing rationale: running BOTH orders means each gain occupies the "
          "'goes first after a different gain's settle' role in one sequence and the "
          "'goes second, immediately after the OTHER gain's own trial' role in the other -- "
          "a claim that one gain is materially lower 'regardless of order' is only made below "
          "if it holds in BOTH sequences separately, not just in the combined pool.")
    print()

    print("### sequence 1: A-first (ABABAB...) ###")
    seq1_results = await _run_confirm_sequence(seq_id="s1Afirst", sequence=seq_a_first, args=args)
    print("### sequence 2: B-first (BABABA...) ###")
    seq2_results = await _run_confirm_sequence(seq_id="s2Bfirst", sequence=seq_b_first, args=args)

    combined = {
        "A": seq1_results["A"] + seq2_results["A"],
        "B": seq1_results["B"] + seq2_results["B"],
    }
    a_clip = max((r["reference_clipped_percent"] for r in combined["A"]), default=0.0)
    b_clip = max((r["reference_clipped_percent"] for r in combined["B"]), default=0.0)

    combined_a_rms = [r["mic_window_rms"] for r in combined["A"]]
    combined_b_rms = [r["mic_window_rms"] for r in combined["B"]]
    combined_a_quiet = [r["quiet_before_rms"] for r in combined["A"]]
    combined_b_quiet = [r["quiet_before_rms"] for r in combined["B"]]

    print("=== CONFIRM SUMMARY ===")
    _print_confirm_block_summary(
        "A-first sequence (ABABAB...) results", seq1_results, a_gain, b_gain
    )
    _print_confirm_block_summary(
        "B-first sequence (BABABA...) results", seq2_results, a_gain, b_gain
    )
    print("  --- combined (both sequences pooled) ---")
    print(f"    A (gain={a_gain:g}) mic_window_rms  : {_mean_min_max(combined_a_rms)}")
    print(f"    B (gain={b_gain:g}) mic_window_rms  : {_mean_min_max(combined_b_rms)}")
    print(f"    A quiet_before_rms               : {_mean_min_max(combined_a_quiet)}")
    print(f"    B quiet_before_rms               : {_mean_min_max(combined_b_quiet)}")
    print(f"    A max reference_clipped_percent  : {a_clip}%")
    print(f"    B max reference_clipped_percent  : {b_clip}%")
    if a_clip > 0 or b_clip > 0:
        print("    *** at least one block was CLIPPED -- do not use this run as gain evidence. ***")

    def _wins(results: dict[str, list[dict]]) -> tuple[int, int]:
        a_rms = [r["mic_window_rms"] for r in results["A"]]
        b_rms = [r["mic_window_rms"] for r in results["B"]]
        n = min(len(a_rms), len(b_rms))
        wins = sum(1 for i in range(n) if a_rms[i] < b_rms[i])
        return wins, n

    seq1_wins, seq1_n = _wins(seq1_results)
    seq2_wins, seq2_n = _wins(seq2_results)
    a_combined_mean = sum(combined_a_rms) / len(combined_a_rms) if combined_a_rms else None
    b_combined_mean = sum(combined_b_rms) / len(combined_b_rms) if combined_b_rms else None

    print()
    print(f"  A-first sequence: A beat B in {seq1_wins}/{seq1_n} cycles")
    print(f"  B-first sequence: A beat B in {seq2_wins}/{seq2_n} cycles")
    if a_combined_mean is not None and b_combined_mean is not None:
        print(f"  combined means: A={a_combined_mean:.2f}  B={b_combined_mean:.2f}")
        a_wins_both = seq1_wins == seq1_n and seq2_wins == seq2_n
        b_wins_both = seq1_wins == 0 and seq2_wins == 0
        if a_wins_both and a_combined_mean < b_combined_mean:
            print("  -> A was lower in EVERY cycle in BOTH the A-first AND the B-first "
                  "sequence: this DOES hold regardless of which gain went first/second, "
                  "consistent with a real effect rather than an ordering artifact. Still NOT "
                  "sufficient on its own to change production default -- see this script's "
                  "own docstring / R0081 report for what else is required first.")
        elif b_wins_both and a_combined_mean > b_combined_mean:
            print("  -> B was lower in EVERY cycle in BOTH sequences: the original sweep's "
                  "gain=0.5 advantage did not reproduce under counterbalanced order/settle "
                  "control -- treat the first sweep as confounded and de-prioritize gain "
                  "calibration versus timing.")
        else:
            print("  -> mixed result: the two sequences do NOT agree with each other (or one "
                  "sequence itself was mixed) -- this is NOT evidence of an order-independent "
                  "effect either way. Report both sequences' own numbers rather than the "
                  "combined pool alone, and do not describe this as holding 'regardless of "
                  "order.'")
    return 0


async def main() -> int:
    args = parse_args()
    if args.sweep:
        return await _run_sweep(args)
    if args.confirm:
        return await _run_confirm(args)
    return await _run_single(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
