#!/usr/bin/env python3
# ruff: noqa: E501  (research spike -- long strings, docstrings, wide lines)
"""M2.6B.4M / R0052 -- local-only self-echo / false-barge-in probe.

DIAGNOSTIC ONLY. No Gemini, no cloud API, no credential, no STT, no LLM.
Reuses the REAL production classes verbatim: ``nexa.voice_tts.
aec_reference.AecReferenceFeeder``, ``nexa.voice.aec.AecReferenceHealth``,
``nexa.voice.bargein.BargeInController``, ``nexa.realtime.gemini.
runtime._ResponseLifecycle`` (the exact playback-lifecycle combinator
production uses to decide when a response is "finished"), the SAME
``SileroVADAnalyzer``/``VADParams(stop_secs=0.5)`` construction the cloud
runtime (``build_gemini_voice_runtime``) uses, and -- since R0053 --
the SAME ``nexa.voice.aec_gain.CoherentReferenceGain`` +
``LocalAudioConfig.output_alsa_mixer_card`` gain-coherence wiring
production uses. None of these are reimplemented differently here.

R0053 CONTRACT FIX: an earlier revision of this probe (R0052) built a
bare, unscaled ``AecReferenceFeeder`` with no ``gain_source`` at all --
so a "0 false barge-ins" result from it would have proven nothing about
R0053's actual production fix (which lives entirely in
``gain_source``). This probe now constructs the identical
``CoherentReferenceGain(card=cfg.output_alsa_mixer_card)`` production
itself constructs and passes its ``current_gain`` method as
``AecReferenceFeeder``'s ``gain_source`` -- reused, not duplicated. It
prints the resulting ``audible_mixer_card``/``audible_gain_db``/
``audible_linear_gain``/``reference_gain_applied`` at startup and
records ``reference_gain_applied`` in every trial's own JSON summary,
so a run's own output is itself the proof the fix was active, not an
assertion.

Purpose: reproduce the REAL production audio path —

    assistant PCM -> transport.output() (audible ``plug:usb_speaker``)
                  -> AecReferenceFeeder (XVF3800 far-end reference,
                     ``plug:respeaker``)
    acoustic room path
                  -> reSpeaker XVF3800 (hardware AEC) -> mic capture
                  -> transport.input() -> SileroVADAnalyzer -> VADProcessor
                  -> BargeInController

— while NeXa "speaks" a real, known local speech fixture through the
real USB speaker, with the operator SILENT, at LOW / NORMAL / MAXIMUM
real playback volume (the operator sets the REAL volume themselves
between runs — this probe does not, and cannot from software alone,
know which physical/ALSA/system volume mechanism the operator's speaker
actually uses; see the R0052 report's own SOURCE AUDIT for why). Then
one deliberate human control trial ("przerwij") proves real user speech
still confirms a barge-in.

Usage::

    # config-only, no audio device:
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --dry

    # silent-operator trial at a given volume LABEL (operator sets the
    # REAL speaker volume to this level themselves, first):
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level low --repeats 5
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level normal --repeats 5
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level max --repeats 5

    # human control trial -- say "przerwij" once assistant playback starts:
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --control

Writes ``self_echo_probe_<timestamp>.json`` under
``self_echo_captures/`` next to this script (git-ignored — real audio
telemetry, never committed) and prints a terminal verdict.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import statistics
import struct
import sys
import time
import traceback
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT_DIR = Path(__file__).resolve().parent / "self_echo_captures"

#: Default local fixture: a real, already-committed speech recording
#: (any sustained natural speech is acoustically equivalent for testing
#: the room-echo/AEC mechanism this probe exercises -- the physics of
#: acoustic feedback does not depend on WHO is speaking or in what
#: synthesized voice). Avoids any new dependency on a running Piper HTTP
#: server for the default path; ``--synthesize-piper`` below is the
#: charter's own documented fallback for a TRUE assistant-voice fixture.
DEFAULT_WAV = (
    REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"
    / "en_explain_gravity.wav"
)

WARMUP_S = 3.0
QUIET_BEFORE_S = 2.0
TAIL_S = 3.0
GAP_S = 1.0
#: Hard stop if _ResponseLifecycle never fires "finished" (should not
#: happen in ordinary use -- a safety bound only, matching every other
#: hard-cap convention in this codebase, never a normal-path timer).
FINISH_TIMEOUT_S = 20.0
CONTROL_TIMEOUT_S = 15.0

#: Real InputAudioRawFrame chunk size the reSpeaker capture path uses in
#: production (Pipecat's own default 20ms @ 16kHz mono = 640 bytes) --
#: irrelevant to construct here; only referenced for documentation. The
#: assistant PCM chunk size below is chosen to resemble a realistic
#: streamed-TTS chunk cadence, not a magic number: ~100ms per chunk.
ASSISTANT_CHUNK_MS = 100


def _pcm_ms(nbytes: int, sample_rate: int) -> float:
    return 1000.0 * (nbytes / 2) / max(1, sample_rate)


def _gain_to_db_text(gain: float) -> str:
    """Display-only inverse of the linear gain
    ``nexa.voice.aec_gain.CoherentReferenceGain.current_gain()`` reports
    -- never a second gain computation, just ``20*log10`` for a
    human-readable print. ``gain <= 0.0`` (muted) prints as such rather
    than raising on ``log10(0)``."""
    if gain <= 0.0:
        return "-inf (muted)"
    return f"{20.0 * math.log10(gain):.2f}"


def _rms(pcm_chunk: bytes) -> float:
    n = len(pcm_chunk) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_chunk[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


def _peak(pcm_chunk: bytes) -> int:
    n = len(pcm_chunk) // 2
    if n == 0:
        return 0
    samples = struct.unpack(f"<{n}h", pcm_chunk[: n * 2])
    return max(abs(s) for s in samples)


# ---- pure, offline-testable analysis helpers ------------------------- #
def speaking_intervals(vad_events: list[dict]) -> list[tuple[float, float | None]]:
    """Collapse a start/stop event list into (start_t, stop_t|None)
    spans. A start with no matching stop yields (start_t, None). Extra
    stops are ignored. Mirrors ``spike_playback_false_vad.py``'s own
    already-proven helper (M2.5A.1) -- not reimplemented differently."""
    spans: list[tuple[float, float | None]] = []
    open_start: float | None = None
    for ev in vad_events:
        if ev["kind"] == "start" and open_start is None:
            open_start = ev["t"]
        elif ev["kind"] == "stop" and open_start is not None:
            spans.append((open_start, ev["t"]))
            open_start = None
    if open_start is not None:
        spans.append((open_start, None))
    return spans


def rms_windows_from_events(
    frames: list[dict], *, lo: float, hi: float
) -> dict[str, Any]:
    """Aggregate windowed RMS/peak/confidence/volume telemetry already
    captured (as one dict per analysis frame) into the [lo, hi) window."""
    xs = [f for f in frames if lo <= f["t"] < hi]
    if not xs:
        return {"n_frames": 0}
    return {
        "n_frames": len(xs),
        "conf_mean": round(statistics.fmean(f["conf"] for f in xs), 4),
        "conf_max": round(max(f["conf"] for f in xs), 4),
        "vol_mean": round(statistics.fmean(f["vol"] for f in xs), 4),
        "vol_max": round(max(f["vol"] for f in xs), 4),
        "speaking_frame_frac": round(
            sum(1 for f in xs if f["speaking"]) / len(xs), 4
        ),
    }


def cross_correlate_pcm(
    reference_pcm: bytes,
    mic_pcm: bytes,
    *,
    sample_rate: int,
    max_lag_ms: float = 200.0,
) -> dict[str, Any]:
    """R0053 / Step 4 -- offline, bounded-lag normalized cross-correlation
    between a far-end reference PCM window and a post-hardware-AEC mic
    PCM window. Diagnostic only: this function does not decide anything
    by itself and is never called from the production pipeline.

    A positive ``best_lag_ms`` means the mic signal best matches the
    reference delayed by that many ms (the acoustically/hardware-expected
    direction: mic after reference). ``normalized_correlation`` is in
    [-1, 1]; a high correlation at a stable, small lag during an
    assistant-only false barge-in is direct evidence the local VAD
    triggered on genuine echo of the assistant's own signal rather than
    on unrelated noise; a comparably high correlation during REAL human
    "przerwij" double-talk (over the assistant's own audio, which is
    still playing) would show ``normalized_correlation`` is not by
    itself a safe confirm/reject gate.

    Bounded lag search (never a full O(n^2) correlation) keeps this
    Raspberry-Pi-appropriate even for multi-second windows: cost is
    O(n * max_lag_samples), each term a vectorized numpy dot product.

    KNOWN LIMITATION (documented, not fixed, by R0055): ``mic_pcm`` spans
    the WHOLE trial continuously from ``reset_trial()`` (silence + playback
    + tail), but ``ref_pcm`` only ever contains the assistant fixture's own
    samples, which start ``QUIET_BEFORE_S`` (2.0s) LATER in the same
    timeline -- this function has no knowledge of that fixed offset and
    searches lag around a "both start at t=0" assumption. With
    ``max_lag_ms`` bounded well under 2000ms (500ms in practice), the
    search can never actually find the true alignment; R0055 found this
    produces a `best_lag_ms` saturated at (or very near) `-max_lag_ms` and
    a near-zero `normalized_correlation` for every real trial captured so
    far -- a saturated-at-the-search-boundary result is itself the
    diagnostic signature of this mismatch, not evidence of low real
    echo correlation. R0055's own report
    (`docs/reports/R0055_..._20260913.md`) computed the CORRECT,
    ref-offset-aware bounded sliding-window correlation externally,
    working from this same PCM. A future checkpoint could fix this
    function to accept and apply that known offset directly; not done
    here to keep this diagnostic-fidelity fix minimal and separate from
    R0055's own read-only analysis."""
    ref = np.frombuffer(reference_pcm, dtype="<i2").astype(np.float64)
    mic = np.frombuffer(mic_pcm, dtype="<i2").astype(np.float64)
    result: dict[str, Any] = {
        "n_ref_samples": int(ref.size),
        "n_mic_samples": int(mic.size),
        "max_lag_ms": max_lag_ms,
        "best_lag_ms": None,
        "best_lag_samples": None,
        "normalized_correlation": None,
    }
    if ref.size == 0 or mic.size == 0:
        return result
    ref = ref - ref.mean()
    mic = mic - mic.mean()
    max_lag = max(1, int(sample_rate * max_lag_ms / 1000.0))
    best_lag = 0
    best_corr = -2.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a = ref[: ref.size - lag] if lag > 0 else ref
            b = mic[lag:]
        else:
            a = ref[-lag:]
            b = mic[: mic.size + lag]
        m = min(a.size, b.size)
        if m <= 1:
            continue
        a = a[:m]
        b = b[:m]
        denom = float(np.sqrt(np.sum(a * a)) * np.sqrt(np.sum(b * b)))
        if denom == 0.0:
            continue
        corr = float(np.dot(a, b) / denom)
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    if best_corr <= -2.0:
        return result  # every candidate window was degenerate (silence)
    result["best_lag_samples"] = best_lag
    result["best_lag_ms"] = round(1000.0 * best_lag / sample_rate, 2)
    result["normalized_correlation"] = round(best_corr, 4)
    return result


def raw_rms_windows_from_events(
    frames: list[dict], *, lo: float, hi: float
) -> dict[str, Any]:
    """Aggregate windowed raw-PCM RMS/peak telemetry (mic or far-end
    reference) into the [lo, hi) window."""
    xs = [f for f in frames if lo <= f["t"] < hi]
    if not xs:
        return {"n_frames": 0}
    return {
        "n_frames": len(xs),
        "rms_mean": round(statistics.fmean(f["rms"] for f in xs), 2),
        "rms_max": round(max(f["rms"] for f in xs), 2),
        "peak_max": max(f["peak"] for f in xs),
    }


def summarize_trial(
    *,
    level: str,
    playback_start: float | None,
    playback_end: float | None,
    trial_end: float,
    vad_events: list[dict],
    mic_frames: list[dict],
    bargein_events: list[dict],
    mic_rms_frames: list[dict] | None = None,
    ref_rms_frames: list[dict] | None = None,
) -> dict[str, Any]:
    """Pure summarization of one trial's raw telemetry -- no Pipecat, no
    I/O, fully offline-testable."""
    mic_rms_frames = mic_rms_frames or []
    ref_rms_frames = ref_rms_frames or []
    spans = speaking_intervals(vad_events)
    span_out = []
    for s, e in spans:
        span_out.append(
            {
                "vad_start_t": round(s, 4),
                "vad_end_t": round(e, 4) if e is not None else None,
                "vad_duration_ms": round((e - s) * 1000.0, 1) if e is not None else None,
                "closed": e is not None,
            }
        )
    confirmed = [e for e in bargein_events if e["kind"] == "confirmed"]
    candidates = [e for e in bargein_events if e["kind"] == "candidate"]
    rejected = [e for e in bargein_events if e["kind"] == "rejected"]
    return {
        "level": level,
        "playback_start_t": round(playback_start, 4) if playback_start is not None else None,
        "playback_end_t": round(playback_end, 4) if playback_end is not None else None,
        "playback_duration_ms": (
            round((playback_end - playback_start) * 1000.0, 1)
            if (playback_start is not None and playback_end is not None)
            else None
        ),
        "vad_spans": span_out,
        "vad_start_count": len(spans),
        "bargein_candidate_count": len(candidates),
        "bargein_confirmed_count": len(confirmed),
        "bargein_rejected_count": len(rejected),
        "bargein_confirmed_t": [round(e["t"], 4) for e in confirmed],
        "false_confirmed_barge_in": len(confirmed) > 0,
        "mic_phase_quiet_before": (
            rms_windows_from_events(mic_frames, lo=0.0, hi=playback_start)
            if playback_start is not None
            else {"n_frames": 0}
        ),
        "mic_phase_playback": (
            rms_windows_from_events(mic_frames, lo=playback_start, hi=playback_end)
            if (playback_start is not None and playback_end is not None)
            else {"n_frames": 0}
        ),
        "mic_phase_after": (
            rms_windows_from_events(mic_frames, lo=playback_end, hi=trial_end)
            if playback_end is not None
            else {"n_frames": 0}
        ),
        "mic_raw_rms_phase_playback": (
            raw_rms_windows_from_events(mic_rms_frames, lo=playback_start, hi=playback_end)
            if (playback_start is not None and playback_end is not None)
            else {"n_frames": 0}
        ),
        "mic_raw_rms_phase_quiet_before": (
            raw_rms_windows_from_events(mic_rms_frames, lo=0.0, hi=playback_start)
            if playback_start is not None
            else {"n_frames": 0}
        ),
        "ref_raw_rms_phase_playback": (
            raw_rms_windows_from_events(ref_rms_frames, lo=playback_start, hi=playback_end)
            if (playback_start is not None and playback_end is not None)
            else {"n_frames": 0}
        ),
        # R0054: mirrors mic's own quiet_before/after phases so a run
        # immediately reveals WHERE reference-frame timestamps actually
        # land relative to the real playback window, without needing a
        # full --capture-pcm run just to notice a timing mismatch (see
        # R0054's own confirmed finding: an unpaced burst injection put
        # every reference frame here, in "before", rather than in
        # "playback" at all).
        "ref_raw_rms_phase_quiet_before": (
            raw_rms_windows_from_events(ref_rms_frames, lo=0.0, hi=playback_start)
            if playback_start is not None
            else {"n_frames": 0}
        ),
        "ref_raw_rms_phase_after": (
            raw_rms_windows_from_events(ref_rms_frames, lo=playback_end, hi=trial_end)
            if playback_end is not None
            else {"n_frames": 0}
        ),
    }


# ---- pipecat wiring (deferred import; --dry works without pipecat) --- #
def _pipecat_imports() -> dict[str, Any]:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
        TTSStoppedFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from pipecat.workers.runner import WorkerRunner

    return {
        "SileroVADAnalyzer": SileroVADAnalyzer,
        "VADParams": VADParams,
        "BotStartedSpeakingFrame": BotStartedSpeakingFrame,
        "BotStoppedSpeakingFrame": BotStoppedSpeakingFrame,
        "Frame": Frame,
        "InputAudioRawFrame": InputAudioRawFrame,
        "TTSAudioRawFrame": TTSAudioRawFrame,
        "TTSStoppedFrame": TTSStoppedFrame,
        "VADUserStartedSpeakingFrame": VADUserStartedSpeakingFrame,
        "VADUserStoppedSpeakingFrame": VADUserStoppedSpeakingFrame,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "VADProcessor": VADProcessor,
        "FrameDirection": FrameDirection,
        "FrameProcessor": FrameProcessor,
        "LocalAudioTransport": LocalAudioTransport,
        "LocalAudioTransportParams": LocalAudioTransportParams,
        "WorkerRunner": WorkerRunner,
    }


@dataclass
class Recorder:
    """Shared telemetry sink -- no Pipecat dependency, testable in
    isolation."""

    vad_events: list[dict] = field(default_factory=list)
    mic_frames: list[dict] = field(default_factory=list)
    mic_rms_frames: list[dict] = field(default_factory=list)
    ref_rms_frames: list[dict] = field(default_factory=list)
    bargein_events: list[dict] = field(default_factory=list)
    playback_start_t: float | None = None
    playback_end_t: float | None = None
    #: M2.6B.4N / R0053 -- Step 4's "minimum additional data": bounded raw
    #: PCM windows for offline cross-correlation. Only populated when
    #: ``capture_pcm=True`` (kept off by default -- these accumulate real
    #: audio bytes, not aggregate stats, so they must stay opt-in).
    capture_pcm: bool = False
    mic_pcm_chunks: list[bytes] = field(default_factory=list)
    ref_pcm_chunks: list[bytes] = field(default_factory=list)
    #: R0055 CONFIRMED BUG FIX -- set the instant a real confirmed barge-in
    #: fires (mirrors production's own ``lifecycle.mark_interrupted()``
    #: trigger, ``nexa.realtime.gemini.runtime``'s ``_on_confirmed``).
    #: ``_play_assistant_phrase`` polls this every chunk and stops
    #: injecting the REST of the synthetic fixture once it is set -- this
    #: probe has no ``_ResponseGenerationGuard`` (a diagnostic harness, not
    #: production), so this flag is the smallest equivalent: never queue
    #: another chunk of an already-interrupted response.
    confirmed_event: asyncio.Event = field(default_factory=asyncio.Event)
    #: R0057 CONFIRMED BUG FIX -- a byte counted by ``worker.queue_frames()``
    #: returning is only proof the chunk reached the WORKER's OWN push
    #: queue (confirmed by reading ``PipelineWorker.queue_frame``'s source
    #: this checkpoint: it does ``await self._push_queue.put(frame)`` and
    #: returns immediately -- no wait for any downstream processor). Each
    #: ``FrameProcessor`` (confirmed by reading the installed
    #: ``pipecat.processors.frame_processor`` source) ALSO owns its own
    #: internal input/process queues, and ``_start_interruption()``
    #: (triggered by an ``InterruptionFrame``, which
    #: ``BargeInController._do_confirm`` broadcasts) resets those queues --
    #: discarding any not-yet-processed frame still waiting there. A chunk
    #: already counted by the OLD (R0055-era) warm-up byte counter could
    #: therefore still be lost before ever reaching ``AecReferenceFeeder``.
    #: This counter is incremented by ``_PlaybackWatcher``, positioned
    #: (already, unchanged) immediately AFTER ``aec_feeder`` in the
    #: pipeline -- a frame counted here has, by construction, already run
    #: all the way through ``aec_feeder.process_frame()`` (which enqueues
    #: it to its own real writer queue before ever calling
    #: ``push_frame()`` onward) -- proof of ACCEPTANCE, not mere
    #: submission.
    ref_accepted_bytes: int = 0
    #: Counts (not overwrites, unlike ``playback_start_t``/``playback_end_t``
    #: above) every real ``BotStartedSpeakingFrame``/``BotStoppedSpeakingFrame``
    #: -- used by warm-up to prove every whole-fixture repeat reached a
    #: clean start-then-stop completion, with no hidden mid-fixture
    #: restart (R0055's own "Bot started speaking again" symptom would
    #: show up here as more starts than repeats run).
    playback_start_count: int = 0
    playback_stop_count: int = 0

    def mark_vad(self, kind: str) -> None:
        self.vad_events.append({"t": time.monotonic(), "kind": kind})

    def mark_mic_frame(self, *, conf: float, vol: float, speaking: bool) -> None:
        self.mic_frames.append(
            {"t": time.monotonic(), "conf": round(conf, 4), "vol": round(vol, 4), "speaking": speaking}
        )

    def mark_mic_rms(self, *, rms: float, peak: int) -> None:
        """Raw mic PCM RMS/peak -- the ACTUAL residual signal reaching
        ``transport.input()`` after the XVF3800's own hardware AEC has
        already run, independent of Silero's own smoothed ``vol``
        proxy above (which the charter also asks to log, but which is
        Silero's own internal metric, not a raw-signal measurement)."""
        self.mic_rms_frames.append({"t": time.monotonic(), "rms": round(rms, 2), "peak": peak})

    def mark_mic_pcm(self, pcm: bytes) -> None:
        if self.capture_pcm:
            self.mic_pcm_chunks.append(pcm)

    def mark_ref_pcm(self, pcm: bytes) -> None:
        if self.capture_pcm:
            self.ref_pcm_chunks.append(pcm)

    def mark_ref_rms(self, *, rms: float, peak: int) -> None:
        """Far-end reference PCM RMS/peak -- the exact bytes handed to
        ``AecReferenceFeeder`` (and therefore to ``plug:respeaker``),
        tapped immediately before the feeder so the recorded value can
        never differ from what the feeder itself queues."""
        self.ref_rms_frames.append({"t": time.monotonic(), "rms": round(rms, 2), "peak": peak})

    def mark_bargein(self, kind: str) -> None:
        self.bargein_events.append({"t": time.monotonic(), "kind": kind})
        if kind == "confirmed":
            self.confirmed_event.set()

    def mark_playback_start(self) -> None:
        self.playback_start_t = time.monotonic()
        self.playback_start_count += 1

    def mark_playback_end(self) -> None:
        self.playback_end_t = time.monotonic()
        self.playback_stop_count += 1

    def mark_ref_accepted(self, pcm: bytes) -> None:
        self.ref_accepted_bytes += len(pcm)

    def reset_trial(self) -> None:
        self.vad_events = []
        self.mic_frames = []
        self.mic_rms_frames = []
        self.ref_rms_frames = []
        self.bargein_events = []
        self.playback_start_t = None
        self.playback_end_t = None
        self.mic_pcm_chunks = []
        self.ref_pcm_chunks = []
        self.confirmed_event = asyncio.Event()
        self.ref_accepted_bytes = 0
        self.playback_start_count = 0
        self.playback_stop_count = 0


def build_probe_pipeline(P: dict[str, Any], *, recorder: Recorder, assistant_sample_rate: int):
    """Builds the REAL production self-echo path: real
    ``AecReferenceFeeder``/``BargeInController``/``SileroVADAnalyzer``
    (``VADParams(stop_secs=0.5)``, matching ``build_gemini_voice_runtime``
    exactly) + real ``LocalAudioTransport`` input/output. Two
    diagnostic-only taps (mic RMS + playback lifecycle) never mutate or
    drop a frame.

    ``assistant_sample_rate`` is the ACTUAL rate of the assistant-speech
    fixture being played (the loaded WAV's own rate, or Piper's own output
    rate) -- production instead hardcodes ``OUTPUT_SAMPLE_RATE_HZ``
    (Gemini's own fixed 24kHz TTS rate) here, because in production the
    audio always comes from Gemini at that fixed rate; this probe's
    fixture is not Gemini audio, so its declared output/reference rate
    must match the fixture's own real rate instead, or resampling
    inside the transport/feeder would corrupt the very timing/parity
    this probe exists to measure."""
    import pyaudio

    from nexa.realtime.gemini.runtime import _ResponseLifecycle
    from nexa.realtime.gemini.service import INPUT_SAMPLE_RATE_HZ
    from nexa.voice.aec import AecReferenceHealth
    from nexa.voice.aec_gain import CoherentReferenceGain
    from nexa.voice.bargein import BargeInController
    from nexa.voice.config import LocalAudioConfig
    from nexa.voice.device import find_device_index
    from nexa.voice_tts.aec_reference import AecReferenceFeeder

    cfg = LocalAudioConfig()
    pa = pyaudio.PyAudio()
    in_idx = find_device_index(pa, cfg.input_device_name, require_input=True)
    out_idx = find_device_index(pa, cfg.output_device_name, require_output=True)

    class _MicRmsTap(P["FrameProcessor"]):
        """DIAGNOSTIC ONLY -- sits before the VAD stage. Never drops or
        mutates a frame; purely observes raw mic PCM for RMS/peak
        telemetry (separate from Silero's own confidence/volume, which
        the probed analyzer subclass below records from the REAL
        production computation).

        R0053 CONFIRMED BUG FIX: this pipeline is ONE linear, bidirectional
        Pipecat chain -- frames the probe injects via
        ``worker.queue_frames()`` (the assistant fixture, as
        ``TTSAudioRawFrame``) enter at the pipeline's own Source and so
        ALSO pass through this tap (positioned right after
        ``transport.input()``) on their way to ``aec_feeder``/
        ``transport.output()``, alongside the real, separately-arriving
        ``InputAudioRawFrame`` frames from the actual microphone. R0052's
        original tap recorded RMS/peak for ANY frame with an ``.audio``
        attribute, so its "mic" telemetry during a playback window was
        contaminated with the raw, un-attenuated assistant PCM in transit
        -- not a measurement of real post-hardware-AEC residual echo.
        Filtering to ``InputAudioRawFrame`` (the SAME type
        ``VADController.process_frame`` itself gates real VAD analysis
        on, confirmed in Pipecat's own source) makes this tap measure
        exactly, and only, what Silero itself analyzes."""

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["InputAudioRawFrame"]) and frame.audio:
                recorder.mark_mic_rms(rms=_rms(frame.audio), peak=_peak(frame.audio))
                recorder.mark_mic_pcm(frame.audio)
            await self.push_frame(frame, direction)

    class _RefRmsTap(P["FrameProcessor"]):
        """DIAGNOSTIC ONLY -- sits immediately before ``aec_feeder``, so
        it observes the EXACT bytes the feeder itself will enqueue to
        ``plug:respeaker`` (the far-end reference). Never drops or
        mutates a frame.

        R0053 CONFIRMED BUG FIX: filters to ``TTSAudioRawFrame`` -- the
        SAME type ``AecReferenceFeeder.process_frame`` itself gates on
        (confirmed in its source) -- for the same reason as
        ``_MicRmsTap`` above: real ``InputAudioRawFrame`` frames also
        pass this point (Pipecat forwards frame types it doesn't act on
        unchanged), and R0052's original untyped tap counted them into
        "reference" RMS too."""

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["TTSAudioRawFrame"]) and frame.audio:
                recorder.mark_ref_rms(rms=_rms(frame.audio), peak=_peak(frame.audio))
                recorder.mark_ref_pcm(frame.audio)
            await self.push_frame(frame, direction)

    class _ProbedSilero(P["SileroVADAnalyzer"]):
        """Records (conf, vol, speaking) for every real VAD analysis
        frame using the REAL production computation -- mirrors
        ``spike_playback_false_vad.py``'s own already-proven
        ``_ProbedSilero`` (M2.5A.1), not reimplemented differently."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._last_conf = 0.0

        def voice_confidence(self, buffer):  # type: ignore[override]
            c = super().voice_confidence(buffer)
            # R0053 CONFIRMED BUG FIX: Silero's real ``voice_confidence``
            # (pipecat-ai 1.8.1, ``SileroOnnxModel.__call__``) returns a
            # shape-(1,) numpy array, not a bare scalar. `float(arr)` on a
            # 1-element ndarray raises `TypeError: only 0-dimensional
            # arrays can be converted to Python scalars` under NumPy 2.x
            # (verified directly against this repo's own installed numpy
            # 2.5.2) -- the original R0052 probe's bare `float(c)` silently
            # hit this exception on EVERY frame and recorded 0.0 always,
            # even during real confirmed barge-ins. `np.asarray(c).reshape(-1)[0]`
            # (or `.item()`) works for both a bare scalar and a shape-(1,)
            # array; production itself never hit this because it only
            # ever compares/bool()s the array (`confidence >= x`, which
            # numpy permits for a single-element array), never calls
            # `float()` on it.
            try:
                self._last_conf = float(np.asarray(c).reshape(-1)[0])
            except (TypeError, ValueError, IndexError):
                self._last_conf = 0.0
            return c

        def _get_smoothed_volume(self, audio: bytes) -> float:  # type: ignore[override]
            v = float(super()._get_smoothed_volume(audio))
            recorder.mark_mic_frame(
                conf=self._last_conf,
                vol=v,
                speaking=bool(
                    self._last_conf >= self._params.confidence and v >= self._params.min_volume
                ),
            )
            return v

    class _VadWatcher(P["FrameProcessor"]):
        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["VADUserStartedSpeakingFrame"]):
                recorder.mark_vad("start")
            elif isinstance(frame, P["VADUserStoppedSpeakingFrame"]):
                recorder.mark_vad("stop")
            await self.push_frame(frame, direction)

    class _PlaybackWatcher(P["FrameProcessor"]):
        """DIAGNOSTIC ONLY -- observes the REAL playback lifecycle
        (``BotStartedSpeakingFrame``/``BotStoppedSpeakingFrame``,
        travelling upstream from ``transport.output()``, exactly like
        production's own ``_VadToProviderBridge``) and drives the SAME
        ``_ResponseLifecycle`` combinator production uses.

        R0057 EXTENSION -- this processor already sits (unchanged
        position) immediately AFTER ``aec_feeder`` and BEFORE
        ``transport.output()``, so it also taps ``TTSAudioRawFrame``
        bytes here: a frame reaching THIS point has, by construction,
        already been run through ``aec_feeder.process_frame()`` in full
        (which enqueues it to its own real writer queue before ever
        calling ``push_frame()``) -- proof the reference was ACCEPTED,
        not merely that ``worker.queue_frames()`` returned (see
        ``Recorder.ref_accepted_bytes``'s own docstring for the full
        source-audited reasoning). Never drops or mutates a frame."""

        def __init__(self, *, lifecycle, **kwargs) -> None:
            super().__init__(**kwargs)
            self._lifecycle = lifecycle

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, P["TTSAudioRawFrame"]) and frame.audio:
                recorder.mark_ref_accepted(frame.audio)
            elif isinstance(frame, P["BotStartedSpeakingFrame"]):
                recorder.mark_playback_start()
                self._lifecycle.observe_bot_started()
            elif isinstance(frame, P["BotStoppedSpeakingFrame"]):
                recorder.mark_playback_end()
                self._lifecycle.observe_bot_stopped()
            await self.push_frame(frame, direction)

    aec_health = AecReferenceHealth(
        on_change=lambda active: recorder.mark_bargein(
            "aec_ref_active" if active else "aec_ref_down"
        )
    )

    def _on_confirmed(ctx) -> None:  # noqa: ANN001
        recorder.mark_bargein("confirmed")

    def _on_candidate(_response_id) -> None:  # noqa: ANN001
        recorder.mark_bargein("candidate")

    def _on_candidate_rejected() -> None:
        recorder.mark_bargein("rejected")

    bargein = BargeInController(
        aec_health=aec_health,
        on_confirmed=_on_confirmed,
        on_candidate=_on_candidate,
        on_candidate_rejected=_on_candidate_rejected,
    )
    lifecycle = _ResponseLifecycle(on_finished=lambda: bargein.notify_response_finished())

    transport = P["LocalAudioTransport"](
        P["LocalAudioTransportParams"](
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ,
            audio_out_sample_rate=assistant_sample_rate,
            audio_in_channels=1,
            audio_out_channels=1,
            input_device_index=in_idx,
            output_device_index=out_idx,
        )
    )
    vad_analyzer = _ProbedSilero(
        sample_rate=INPUT_SAMPLE_RATE_HZ, params=P["VADParams"](stop_secs=0.5)
    )
    vad_processor = P["VADProcessor"](vad_analyzer=vad_analyzer)
    # R0053 CONTRACT FIX: the probe must exercise the SAME gain-coherence
    # component and config field production actually uses
    # (``build_gemini_voice_runtime``), not a bare, unscaled
    # ``AecReferenceFeeder`` -- otherwise a "0 false barge-ins" result
    # here would prove nothing about the R0053 fix. Reused verbatim, not
    # reimplemented: same class, same ``cfg.output_alsa_mixer_card``
    # field, same ``gain_source=reference_gain.current_gain`` wiring.
    reference_gain = CoherentReferenceGain(card=cfg.output_alsa_mixer_card)
    aec_feeder = AecReferenceFeeder(
        aec_health=aec_health,
        sample_rate=assistant_sample_rate,
        channels=1,
        gain_source=reference_gain.current_gain,
    )

    pipeline = P["Pipeline"](
        [
            transport.input(),
            _MicRmsTap(),
            vad_processor,
            _VadWatcher(),
            bargein,
            _RefRmsTap(),
            aec_feeder,
            _PlaybackWatcher(lifecycle=lifecycle),
            transport.output(),
        ]
    )
    worker = P["PipelineWorker"](
        pipeline,
        params=P["PipelineParams"](
            audio_in_sample_rate=INPUT_SAMPLE_RATE_HZ, audio_out_sample_rate=assistant_sample_rate
        ),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    return worker, P["WorkerRunner"], bargein, lifecycle, aec_health, reference_gain, cfg


async def _play_assistant_phrase(
    P: dict[str, Any], worker, *, lifecycle, bargein, pcm: bytes, sample_rate: int,
    confirmed_event: asyncio.Event | None = None, arm_bargein: bool = True,
) -> None:
    """Simulates exactly what ``_consume_provider_events`` does for a
    real Gemini response: marks the response dispatched (barge-in
    admission requires ``response_in_flight``), streams the assistant
    PCM as chunked ``TTSAudioRawFrame``s, one at a time, each followed
    by ``lifecycle.mark_audio_produced()`` -- production's own exact
    per-chunk ordering (``self.lifecycle.mark_audio_produced()`` then
    ``await self.hw_worker.queue_frames([frame])`` for every
    ``AssistantAudioEvent``, confirmed in
    ``nexa.realtime.gemini.runtime``) -- then a ``TTSStoppedFrame`` +
    ``mark_generation_done()`` so the REAL ``_ResponseLifecycle``
    combinator fires ``on_finished`` off genuine
    ``BotStoppedSpeakingFrame`` truth, never a timer.

    R0054 CONFIRMED BUG FIX: this function's own docstring already
    claimed "a realistic ~100ms cadence... never all-at-once", but the
    implementation built the ENTIRE frame list and pushed it through
    ``worker.queue_frames(frames)`` in ONE call -- Pipecat's own
    ``BaseOutputTransport._audio_queue`` is an UNBOUNDED
    ``FrameQueue()`` (confirmed in its installed source: no ``maxsize``
    passed), so nothing anywhere in the pipeline throttled that burst
    to real time. Every chunk of an entire ~3.5s phrase landed at
    ``_RefRmsTap`` within milliseconds of each other, all clustered near
    ``trial_start`` -- entirely BEFORE the real, wall-clock-paced
    ``playback_start_t``/``playback_end_t`` window
    (``BotStartedSpeakingFrame``/``BotStoppedSpeakingFrame``, which only
    fire once the real PortAudio device has actually begun/finished
    producing sound). This explains R0054's real-hardware capture
    showing ``ref_raw_rms_phase_playback: {"n_frames": 0}`` in every
    trial: the reference frames were real and correctly tapped, just
    all timestamped well outside the window this probe reports them
    against -- a diagnostic-fidelity defect in THIS probe's own
    simulation of assistant speech, not evidence about the real
    hardware/AEC path. Real production never does this: it queues
    exactly one frame per ``AssistantAudioEvent``, as they arrive from
    Gemini's own event stream (paced by the network/model, not a
    Python list built and drained in one call). Pacing each chunk with
    a real ``asyncio.sleep`` at the SAME ``ASSISTANT_CHUNK_MS`` this
    probe already uses to SIZE its chunks (not a new, invented number)
    makes the simulation match that real per-event cadence.

    R0055 CONFIRMED BUG FIX: this loop had no awareness of a confirmed
    local barge-in at all -- ``BargeInController._do_confirm`` (the REAL,
    unmodified production class this probe also uses) calls
    ``on_confirmed`` then unconditionally ``await self.broadcast_interruption()``,
    which clears the output transport's queue at that instant, but this
    loop kept right on calling ``worker.queue_frames([frame])`` for every
    REMAINING chunk of the same ~3.5s fixture on its own 100ms schedule,
    oblivious to the interruption -- re-populating the just-cleared queue
    and producing a SECOND, later ``BotStartedSpeakingFrame``/
    ``BotStoppedSpeakingFrame`` pair. Confirmed both by direct real-hardware
    operator observation ("Bot started speaking again" ~60ms after a
    confirmed interruption) and by JSON self-consistency: every captured
    trial's own (now known-corrupted) ``playback_start_t`` landed near
    ``bargein_confirmed_t + ~0.06s``, not near the true dispatch time --
    because ``Recorder.mark_playback_start()``/``mark_playback_end()``
    unconditionally OVERWRITE on every ``BotStartedSpeakingFrame``/
    ``BotStoppedSpeakingFrame``, so the LAST (post-restart) pair, not the
    true original one, is what every prior report's
    ``mic_phase_playback``/``ref_raw_rms_phase_playback`` windows were
    built from -- a second diagnostic-fidelity gap, on top of R0054's own,
    in the SAME function. Real production has no equivalent gap: a
    confirmed local barge-in invalidates the CURRENT response generation
    (``_ResponseGenerationGuard.interrupt()``) and calls
    ``lifecycle.mark_interrupted()`` (``nexa.realtime.gemini.runtime``'s
    own ``_on_confirmed``) BEFORE any further ``AssistantAudioEvent`` for
    that response can reach ``hw_worker.queue_frames()`` -- this probe has
    no generation guard (a diagnostic harness, not production), so the
    smallest faithful equivalent is: stop injecting the REST of an
    already-interrupted phrase, and call the SAME ``lifecycle.mark_interrupted()``
    primitive production calls, rather than the normal
    ``TTSStoppedFrame``/``mark_generation_done()`` tail. This does NOT
    change ``BargeInController``/production playback semantics -- only
    this diagnostic probe's own synthetic-phrase injection loop.

    R0057 EXTENSION -- this function now returns the number of PCM bytes
    actually queued before it stopped (whether by full completion or an
    early return on ``confirmed_event``), so a caller can measure real
    delivered-audio duration instead of assuming ``len(pcm)`` was always
    reached (that assumption is exactly what a confirmed interruption
    can invalidate -- see ``_run_warmup``'s own docstring).

    R0057 CORRECTION 5 -- new ``arm_bargein`` (default ``True``,
    preserves EXACT prior behavior for every existing call site):
    source-audited this checkpoint that ``confirmed_event`` alone does
    NOT prevent a REAL confirmed self-barge-in from occurring during
    warm-up -- ``bargein.notify_response_dispatched()`` below is what
    actually arms ``BargeInController``'s own admission gate
    (``InterruptionStateMachine.response_in_flight``); with it armed,
    real VAD activity can still reach ``BargeInController._do_confirm()``
    and call the REAL, unmodified ``broadcast_interruption()`` --
    installed Pipecat source confirms (``pipecat.processors
    .frame_processor.FrameProcessor._start_interruption()``) this resets
    EVERY downstream processor's own internal frame queue, discarding
    any ``TTSAudioRawFrame`` still waiting there -- a frame this
    function already counted as "queued" (via ``worker.queue_frames()``
    returning) could still be lost before ever reaching
    ``AecReferenceFeeder``. ``arm_bargein=False`` skips the
    ``notify_response_dispatched()`` call below entirely, which --
    proven directly from ``nexa.voice.bargein.BargeInController
    ._handle_speech_started``'s own first line,
    ``if not self._sm.response_in_flight: return`` -- makes
    ``_do_confirm``/``broadcast_interruption`` structurally unreachable
    for as long as it stays unset, regardless of any real VAD activity.
    This does NOT touch ``BargeInController`` itself (its source is
    unmodified) and does NOT change any existing call site (both
    measured-trial functions keep the default, unchanged)."""
    if arm_bargein:
        bargein.notify_response_dispatched()
    lifecycle.mark_dispatched()
    chunk_bytes = int(sample_rate * (ASSISTANT_CHUNK_MS / 1000.0) * 2)
    chunk_secs = ASSISTANT_CHUNK_MS / 1000.0
    for i in range(0, len(pcm), chunk_bytes):
        if confirmed_event is not None and confirmed_event.is_set():
            lifecycle.mark_interrupted()
            return i
        frame = P["TTSAudioRawFrame"](
            audio=pcm[i : i + chunk_bytes], sample_rate=sample_rate, num_channels=1
        )
        lifecycle.mark_audio_produced()
        await worker.queue_frames([frame])
        await asyncio.sleep(chunk_secs)
    if confirmed_event is not None and confirmed_event.is_set():
        lifecycle.mark_interrupted()
        return len(pcm)
    await worker.queue_frames([P["TTSStoppedFrame"]()])
    lifecycle.mark_generation_done()
    return len(pcm)


async def _run_warmup(
    P: dict[str, Any], worker, recorder: Recorder, *, lifecycle, bargein, pcm: bytes,
    sample_rate: int, warmup_seconds: float, poll_timeout_s: float = 5.0,
) -> dict[str, Any]:
    """R0057 -- deterministic, PCM-PROVEN AEC warm-up, reusing the SAME
    real hardware path every measured trial uses
    (``TTSAudioRawFrame`` -> ``AecReferenceFeeder`` -> ``plug:respeaker``
    reference -> ``transport.output()`` -> the real audible speaker) --
    never a sleep-only simulation, never a bypass of the real
    ``AecReferenceFeeder``/output transport.

    CORRECTION 4 (confirmed bug, fixed): a naive ``--repeats N`` warm-up
    assumes each repeat delivers the FULL ``len(pcm)`` (~3.5s) of
    reference PCM. That assumption is exactly what R0055's own
    confirmed-bug fix in ``_play_assistant_phrase`` (the
    ``confirmed_event`` check) can invalidate: at MAX volume a real
    confirmed self-barge-in fires ~1.1-1.5s into playback (R0055's own
    measured figures), truncating a repeat to a fraction of its nominal
    length -- and since the false-confirm RATE is exactly what an
    R0057-style gain experiment changes between conditions,
    ``--repeats N`` could deliver a DIFFERENT amount of real warm-up PCM
    to each one. Fixed by passing ``confirmed_event=None``: every call
    is then guaranteed (the same source-audited early-return conditions
    in ``_play_assistant_phrase``) to deliver the full ``len(pcm)``
    bytes, counted from what each call actually returns.

    CORRECTION 5 (a second, deeper confirmed bug, fixed here):
    Correction 4's own reasoning claimed "``AecReferenceFeeder`` itself
    never reacts to ``InterruptionFrame`` at all... so the far-end
    reference delivery this warm-up cares about is unaffected by any
    interruption regardless." **That claim is WRONG, confirmed by
    reading the installed ``pipecat.processors.frame_processor`` source
    this checkpoint, not merely re-asserted.** ``confirmed_event=None``
    only stops THIS function's OWN injection loop from giving up early
    -- it does nothing about the REAL ``BargeInController``, which stays
    fully armed (``bargein.notify_response_dispatched()`` was still
    called every repeat) and can still genuinely confirm a false
    self-barge-in and call the REAL, unmodified
    ``await self.broadcast_interruption()``. Reading
    ``FrameProcessor.process_frame``'s own base-class handling of
    ``InterruptionFrame`` (``_start_interruption()`` ->
    ``__reset_process_queue()``) shows EVERY processor in the pipeline
    -- ``AecReferenceFeeder`` included, since its own overridden
    ``process_frame`` calls ``await super().process_frame(...)`` FIRST,
    confirmed by reading its source directly -- discards any
    not-yet-processed frame still sitting in ITS OWN internal queue the
    instant an interruption is broadcast. Separately, reading
    ``PipelineWorker.queue_frame``'s own source shows
    ``worker.queue_frames()`` returning means ONLY that a frame reached
    the WORKER's own push queue (``await self._push_queue.put(frame)``)
    -- nothing about whether it has yet reached, let alone been accepted
    by, ``AecReferenceFeeder``. **Together this means a byte already
    counted by Correction 4's own counter could still be discarded,
    mid-flight, by a genuine confirmed interruption during warm-up --
    the exact downstream-contamination gap this correction fixes.**

    Fix: ``_play_assistant_phrase`` is now called with
    ``arm_bargein=False`` in addition to ``confirmed_event=None`` --
    this skips ``bargein.notify_response_dispatched()`` entirely, which
    (proven directly from ``nexa.voice.bargein.BargeInController
    ._handle_speech_started``'s own first line,
    ``if not self._sm.response_in_flight: return``) makes
    ``_do_confirm``/``broadcast_interruption`` structurally unreachable
    for the whole warm-up -- VAD/Silero keep running and any VAD start/
    stop is still recorded by this probe's own ``_VadWatcher`` telemetry
    (satisfying "VAD may continue running for telemetry"), but
    ``BargeInController`` itself never acts on it. Verified, not merely
    argued, three ways per call:

    1. ``bargein.telemetry.interrupt_confirmed`` (the REAL production
       counter, incremented exactly once per real
       ``BargeInController._do_confirm()`` call) is read before and
       after the whole warm-up -- any non-zero delta means a
       confirmation slipped through and the warm-up result is
       untrustworthy.
    2. ``recorder.ref_accepted_bytes`` -- incremented by
       ``_PlaybackWatcher``, positioned (unchanged) immediately AFTER
       ``aec_feeder`` -- proves each repeat's PCM was actually ACCEPTED
       by ``AecReferenceFeeder`` (survived its own per-processor queue),
       not merely submitted to the worker's push queue. This function
       polls it (bounded, 5s) after the submission loop finishes, since
       Pipecat's own per-processor queues mean acceptance can lag
       submission by a small, non-zero amount.
    3. ``recorder.playback_start_count``/``playback_stop_count`` --
       incremented once per real ``BotStartedSpeakingFrame``/
       ``BotStoppedSpeakingFrame`` -- must equal the number of whole
       repeats actually run, proving every repeat reached a clean,
       uninterrupted start-then-stop completion (a hidden mid-fixture
       restart, R0055's own "Bot started speaking again" symptom, would
       show up as MORE starts than repeats).

    Returns a dict with all of the above so the caller
    (``_run()``) can assert on them and print/record the real evidence
    -- this function itself never silently swallows a contradiction.

    Measured trials (``_run_silent_trial``/``_run_control_trial``) are
    completely unchanged by this function's existence: they still pass
    their own ``recorder.confirmed_event`` and the default
    ``arm_bargein=True``, so a confirmed interruption during an ACTUAL
    measured trial still truncates and broadcasts exactly as R0055's
    own fix intends -- only this dedicated warm-up path disarms
    confirmation, and only here."""
    target_bytes = int(round(warmup_seconds * sample_rate)) * 2  # int16 mono
    interrupt_confirmed_before = bargein.telemetry.interrupt_confirmed
    ref_accepted_before = recorder.ref_accepted_bytes
    playback_start_before = recorder.playback_start_count
    playback_stop_before = recorder.playback_stop_count

    delivered_bytes = 0
    repeats_run = 0
    while delivered_bytes < target_bytes:
        delivered_bytes += await _play_assistant_phrase(
            P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
            sample_rate=sample_rate, confirmed_event=None, arm_bargein=False,
        )
        repeats_run += 1

    # Bounded wait for the async pipeline to finish propagating the
    # already-submitted frames through to the post-aec_feeder
    # acceptance tap -- `queue_frames()` returning does not mean the
    # frame has yet reached `aec_feeder` (see the docstring above).
    # ``poll_timeout_s`` is injectable so a test can prove the give-up
    # path itself without a real 5-second wait.
    deadline = time.monotonic() + poll_timeout_s
    while (recorder.ref_accepted_bytes - ref_accepted_before) < delivered_bytes:
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(0.02)

    return {
        "requested_seconds": warmup_seconds,
        "delivered_bytes": delivered_bytes,
        "ref_accepted_bytes": recorder.ref_accepted_bytes - ref_accepted_before,
        "playback_start_count": recorder.playback_start_count - playback_start_before,
        "playback_stop_count": recorder.playback_stop_count - playback_stop_before,
        "repeats_run": repeats_run,
        "interrupt_confirmed_delta": (
            bargein.telemetry.interrupt_confirmed - interrupt_confirmed_before
        ),
    }


async def _wait_for_finish(lifecycle, *, timeout: float) -> bool:
    """Polls ``_ResponseLifecycle``'s own private ``_finished_fired``
    flag (read-only observation, matching this whole codebase's
    established same-package reach-in convention) -- no public API
    exists for this on a pure combinator, and adding one to production
    code for a diagnostic probe is out of scope here."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lifecycle._finished_fired:  # noqa: SLF001
            return True
        await asyncio.sleep(0.05)
    return False


async def _run_silent_trial(
    P: dict[str, Any], worker, recorder: Recorder, *, lifecycle, bargein, pcm: bytes,
    sample_rate: int, level: str, trial_index: int, max_lag_ms: float,
) -> dict[str, Any]:
    recorder.reset_trial()
    trial_start = time.monotonic()
    await asyncio.sleep(QUIET_BEFORE_S)
    await _play_assistant_phrase(
        P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm, sample_rate=sample_rate,
        confirmed_event=recorder.confirmed_event,
    )
    finished = await _wait_for_finish(lifecycle, timeout=FINISH_TIMEOUT_S)
    if not finished:
        print("  WARNING: _ResponseLifecycle never fired 'finished' within timeout")
    await asyncio.sleep(TAIL_S)
    trial_end = time.monotonic()
    result = summarize_trial(
        level=level,
        playback_start=recorder.playback_start_t,
        playback_end=recorder.playback_end_t,
        trial_end=trial_end,
        vad_events=recorder.vad_events,
        mic_frames=recorder.mic_frames,
        bargein_events=recorder.bargein_events,
        mic_rms_frames=recorder.mic_rms_frames,
        ref_rms_frames=recorder.ref_rms_frames,
    )
    result["trial_start_t"] = round(trial_start, 4)
    corr = _save_pcm_and_correlate(
        recorder, sample_rate=sample_rate, label=f"{level}_trial{trial_index}", max_lag_ms=max_lag_ms
    )
    if corr is not None:
        result["cross_correlation"] = corr
    await asyncio.sleep(GAP_S)
    return result


async def _run_control_trial(
    P: dict[str, Any], worker, recorder: Recorder, *, lifecycle, bargein, pcm: bytes,
    sample_rate: int, control_index: int, max_lag_ms: float,
) -> dict[str, Any]:
    """The human control: assistant plays, operator deliberately says
    "przerwij" -- proves whatever the eventual fix is does NOT also
    defeat real user-over-assistant barge-in."""
    recorder.reset_trial()
    trial_start = time.monotonic()
    print("\n  >>> Assistant will now speak. Say \"przerwij\" clearly once it starts. <<<\n")
    await asyncio.sleep(1.0)
    await _play_assistant_phrase(
        P, worker, lifecycle=lifecycle, bargein=bargein, pcm=pcm, sample_rate=sample_rate,
        confirmed_event=recorder.confirmed_event,
    )
    await _wait_for_finish(lifecycle, timeout=CONTROL_TIMEOUT_S)
    await asyncio.sleep(1.0)
    trial_end = time.monotonic()
    result = summarize_trial(
        level="control_real_user_interrupt",
        playback_start=recorder.playback_start_t,
        playback_end=recorder.playback_end_t,
        trial_end=trial_end,
        vad_events=recorder.vad_events,
        mic_frames=recorder.mic_frames,
        bargein_events=recorder.bargein_events,
        mic_rms_frames=recorder.mic_rms_frames,
        ref_rms_frames=recorder.ref_rms_frames,
    )
    result["trial_start_t"] = round(trial_start, 4)
    corr = _save_pcm_and_correlate(
        recorder, sample_rate=sample_rate, label=f"control{control_index}", max_lag_ms=max_lag_ms
    )
    if corr is not None:
        result["cross_correlation"] = corr
    return result


def _write_wav(path: Path, pcm: bytes, *, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def _save_pcm_and_correlate(
    recorder: Recorder, *, sample_rate: int, label: str, max_lag_ms: float
) -> dict[str, Any] | None:
    """R0053 Step 4: if ``--capture-pcm`` was requested and this trial
    actually captured any raw PCM, write the bounded mic/reference
    windows to WAV (git-ignored, never committed) and run the offline
    cross-correlation between them. Returns ``None`` (not an empty dict)
    when nothing was captured, so callers can tell "not requested" apart
    from "captured, degenerate result"."""
    if not recorder.capture_pcm:
        return None
    mic_pcm = b"".join(recorder.mic_pcm_chunks)
    ref_pcm = b"".join(recorder.ref_pcm_chunks)
    if not mic_pcm and not ref_pcm:
        return None
    pcm_dir = OUT_DIR / "pcm"
    mic_path = pcm_dir / f"{label}_mic.wav"
    ref_path = pcm_dir / f"{label}_ref.wav"
    _write_wav(mic_path, mic_pcm, sample_rate=sample_rate)
    _write_wav(ref_path, ref_pcm, sample_rate=sample_rate)
    corr = cross_correlate_pcm(
        ref_pcm, mic_pcm, sample_rate=sample_rate, max_lag_ms=max_lag_ms
    )
    corr["mic_wav"] = str(mic_path)
    corr["ref_wav"] = str(ref_path)
    return corr


def _load_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise ValueError(f"{path} must be 16-bit mono PCM")
        return wf.readframes(wf.getnframes()), wf.getframerate()


async def _synthesize_piper_wav(text: str, *, voice: str) -> tuple[bytes, int]:
    """Charter's own documented fallback: generate a TRUE assistant-voice
    fixture via the existing, frozen local Piper HTTP path -- never
    modifies it, only calls its already-public API."""
    from nexa.tts import EN_VOICE, PL_VOICE, PiperHttpConfig, PiperHttpServer

    voice_id = PL_VOICE if voice == "pl" else EN_VOICE
    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    wav_bytes = await piper.synthesize(text, voice=voice_id)
    await piper.stop()
    tmp = OUT_DIR / "_piper_fixture.wav"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(wav_bytes)
    pcm, rate = _load_wav(tmp)
    tmp.unlink(missing_ok=True)
    return pcm, rate


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="validate construction only; no audio device")
    p.add_argument(
        "--level", choices=["low", "normal", "max"], default=None,
        help="label for the REAL speaker volume the operator has already set (silent-operator trial)",
    )
    p.add_argument("--repeats", type=int, default=5, help="number of silent playback repeats at --level")
    p.add_argument("--control", action="store_true", help="run the human 'przerwij' control trial instead")
    p.add_argument("--wav", type=Path, default=None, help="path to a 16-bit mono WAV fixture (default: a real committed recording)")
    p.add_argument("--synthesize-piper", type=str, default=None, metavar="TEXT", help="generate a real assistant-voice fixture via the local Piper HTTP path instead of --wav")
    p.add_argument(
        "--voice", choices=["pl", "en"], default="pl",
        help="Piper voice for --synthesize-piper (maps to nexa.tts.PL_VOICE/EN_VOICE)",
    )
    p.add_argument(
        "--capture-pcm", action="store_true",
        help=(
            "R0053 Step 4: also save bounded raw PCM (mic + far-end reference) "
            "for each trial as WAV pairs under self_echo_captures/pcm/, and run "
            "an offline normalized cross-correlation between them"
        ),
    )
    p.add_argument(
        "--max-lag-ms", type=float, default=200.0,
        help="max lag searched by --capture-pcm's cross-correlation (default 200ms)",
    )
    p.add_argument(
        "--warmup-seconds", type=float, default=None,
        help=(
            "R0057: run a deterministic, PCM-proven AEC warm-up (real hardware "
            "path, confirmed interruptions never truncate it) for at least this "
            "many seconds of reference PCM BEFORE the normal --level/--control "
            "trial(s) below. Composes with --level/--repeats/--capture-pcm in "
            "the SAME invocation -- does not replace them."
        ),
    )
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    P = _pipecat_imports()
    print("=" * 74)
    print("M2.6B.4M / R0052 -- local-only self-echo / false-barge-in probe (NO GEMINI)")
    print("=" * 74)

    if args.dry:
        from nexa.realtime.gemini.service import INPUT_SAMPLE_RATE_HZ

        vad_analyzer = P["SileroVADAnalyzer"](
            sample_rate=INPUT_SAMPLE_RATE_HZ, params=P["VADParams"](stop_secs=0.5)
        )
        print(f"  sample_rate              {INPUT_SAMPLE_RATE_HZ}")
        print(f"  vad_start_secs           {vad_analyzer.params.start_secs}")
        print(f"  vad_stop_secs            {vad_analyzer.params.stop_secs}")
        print("DRY RUN: no audio device opened, no results written.")
        return 0

    if not args.control and args.level is None:
        print("error: pass --level {low,normal,max} for a silent trial, or --control", file=sys.stderr)
        return 2

    if args.synthesize_piper:
        pcm, rate = await _synthesize_piper_wav(args.synthesize_piper, voice=args.voice)
    else:
        wav_path = args.wav or DEFAULT_WAV
        pcm, rate = _load_wav(wav_path)
    print(f"  assistant fixture        {_pcm_ms(len(pcm), rate) / 1000:.2f}s @ {rate}Hz")

    recorder = Recorder(capture_pcm=args.capture_pcm)
    if args.capture_pcm:
        print(f"  capture-pcm              ON (max_lag_ms={args.max_lag_ms})")
    worker, runner_cls, bargein, lifecycle, aec_health, reference_gain, cfg = build_probe_pipeline(
        P, recorder=recorder, assistant_sample_rate=rate
    )
    runner = runner_cls()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(WARMUP_S)
    print(f"  AEC_REF_ACTIVE           {aec_health.active}")

    # R0053 CONTRACT FIX: prove the fix is actually active in THIS run --
    # never assert it, print the SAME production gain component's own
    # live reading.
    startup_gain = reference_gain.current_gain()
    print(f"  audible_mixer_card       {cfg.output_alsa_mixer_card}")
    print(f"  audible_gain_db          {_gain_to_db_text(startup_gain)}")
    print(f"  audible_linear_gain      {startup_gain:.4f}")
    print(f"  reference_gain_applied   {startup_gain:.4f}  (fed to AecReferenceFeeder.gain_source)")

    warmup_result: dict[str, Any] | None = None
    if args.warmup_seconds:
        print(f"\n  warmup: requesting >= {args.warmup_seconds:.1f}s of real, ACCEPTED "
              "reference PCM (BargeInController disarmed -- confirmed interruptions "
              "are structurally impossible during warmup, not merely ignored) ...",
              flush=True)
        warmup_result = await _run_warmup(
            P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
            sample_rate=rate, warmup_seconds=args.warmup_seconds,
        )
        # R0057 TEMPORARY DIAGNOSTIC INSTRUMENTATION (pre-execution
        # root-cause audit, not a functional fix -- remove once the real
        # cause of the observed hardware cancellation is confirmed and
        # fixed). Every print here uses flush=True deliberately: the
        # existing prints below it do NOT, and Python's stdout is fully
        # block-buffered when redirected (as any backgrounded run is) --
        # so on an abrupt process kill, un-flushed output is silently
        # lost. That gap is itself one of this checkpoint's confirmed
        # findings, not assumed.
        print(f"WARMUP_RETURNED {warmup_result}", flush=True)
        queued_s = warmup_result["delivered_bytes"] / 2 / rate
        accepted_s = warmup_result["ref_accepted_bytes"] / 2 / rate
        print(
            f"  warmup: repeats_run={warmup_result['repeats_run']} "
            f"queued_s={queued_s:.2f} accepted_s={accepted_s:.2f} "
            f"(requested >= {args.warmup_seconds:.1f}s) "
            f"playback_start_count={warmup_result['playback_start_count']} "
            f"playback_stop_count={warmup_result['playback_stop_count']} "
            f"interrupt_confirmed_delta={warmup_result['interrupt_confirmed_delta']}",
            flush=True,
        )
        # R0057 TEMPORARY DIAGNOSTIC INSTRUMENTATION (see note above) --
        # BEFORE/AFTER markers around each assert. A future real hardware
        # run showing "..._1_BEFORE" with no matching "..._1_AFTER" (etc.)
        # pinpoints EXACTLY which assertion raised, without relying on the
        # exception's own traceback ever getting a chance to print (this
        # checkpoint's own source audit found a plausible mechanism -- an
        # uncaught exception here propagates outside the try/finally below,
        # letting asyncio.run()'s own internal task-cancellation cleanup
        # forcibly cancel the still-running Pipecat pipeline; if THAT
        # cleanup then blocks, the original exception's traceback never
        # gets a chance to print at all).
        #
        # R0057 CORRECTION 5 -- the authoritative proof, straight from the
        # REAL BargeInController's own telemetry: if this is ever nonzero,
        # a genuine confirmed interruption slipped through during warmup
        # despite arm_bargein=False, and NOTHING below can be trusted.
        print("WARMUP_ASSERT_1_BEFORE", flush=True)
        assert warmup_result["interrupt_confirmed_delta"] == 0, (
            "a real confirmed barge-in occurred during warmup -- this should "
            "be structurally impossible with arm_bargein=False; treat as a "
            "bug in BargeInController or in this probe's wiring, not a "
            "passable warmup"
        )
        print("WARMUP_ASSERT_1_AFTER", flush=True)
        print("WARMUP_ASSERT_2_BEFORE", flush=True)
        assert accepted_s >= args.warmup_seconds, (
            "reference PCM ACCEPTED past AecReferenceFeeder under-delivered "
            "-- some already-queued PCM was lost in transit; treat as a bug"
        )
        print("WARMUP_ASSERT_2_AFTER", flush=True)
        print("WARMUP_ASSERT_3_BEFORE", flush=True)
        assert (
            warmup_result["playback_start_count"]
            == warmup_result["playback_stop_count"]
            == warmup_result["repeats_run"]
        ), (
            "playback start/stop count does not match repeats run -- a "
            "hidden mid-fixture restart occurred during warmup; treat as a bug"
        )
        print("WARMUP_ASSERT_3_AFTER", flush=True)

    print("ENTERING_TRIAL_LOOP", flush=True)
    trials: list[dict[str, Any]] = []
    try:
        if args.control:
            r = await _run_control_trial(
                P, worker, recorder, lifecycle=lifecycle, bargein=bargein, pcm=pcm,
                sample_rate=rate, control_index=1, max_lag_ms=args.max_lag_ms,
            )
            # R0053 CONTRACT FIX: re-read the SAME live gain per trial
            # (not just once at startup) -- proves the fix stays active,
            # and would visibly change if the operator adjusted the real
            # mixer mid-run.
            r["reference_gain_applied"] = reference_gain.current_gain()
            trials.append(r)
            print(
                f"  control: confirmed_barge_ins={r['bargein_confirmed_count']} "
                f"(expect exactly 1) reference_gain_applied={r['reference_gain_applied']:.4f}"
            )
        else:
            print(
                f"\nOperator: set the REAL speaker volume to '{args.level.upper()}' now, "
                "then remain completely SILENT.\n"
            )
            await asyncio.sleep(3.0)
            for i in range(1, args.repeats + 1):
                print(f"  [{args.level}] trial {i}/{args.repeats} ...", flush=True)
                r = await _run_silent_trial(
                    P, worker, recorder, lifecycle=lifecycle, bargein=bargein,
                    pcm=pcm, sample_rate=rate, level=args.level, trial_index=i,
                    max_lag_ms=args.max_lag_ms,
                )
                r["reference_gain_applied"] = reference_gain.current_gain()
                trials.append(r)
                print(
                    f"      vad_starts={r['vad_start_count']} "
                    f"confirmed={r['bargein_confirmed_count']} "
                    f"candidates={r['bargein_candidate_count']} "
                    f"rejected={r['bargein_rejected_count']} "
                    f"reference_gain_applied={r['reference_gain_applied']:.4f}"
                )
    finally:
        # R0057 TEMPORARY DIAGNOSTIC INSTRUMENTATION (pre-execution
        # root-cause audit for a confirmed real-hardware cancellation/hang
        # -- not a functional fix; remove once the real cause is confirmed
        # and fixed). Every print flushes immediately.
        print("ENTERING_FINALLY", flush=True)
        run_task_done = run_task.done()
        run_task_cancelled = run_task.cancelled() if run_task_done else "n/a (not done yet)"
        print(f"run_task.done()={run_task_done} run_task.cancelled()={run_task_cancelled}", flush=True)
        if run_task_done and run_task_cancelled is not True:
            with contextlib.suppress(Exception):
                print(f"run_task.exception()={run_task.exception()!r}", flush=True)

        teardown_done = asyncio.Event()

        async def _dump_stacks_if_blocked() -> None:
            await asyncio.sleep(5.0)
            if teardown_done.is_set():
                return
            print("TEARDOWN_STALL_DETECTED_AFTER_5s -- dumping all task stacks", flush=True)
            for t in asyncio.all_tasks():
                print(f"--- task name={t.get_name()!r} done={t.done()} ---", flush=True)
                t.print_stack(file=sys.stdout)
                sys.stdout.flush()

        watchdog = asyncio.ensure_future(_dump_stacks_if_blocked())

        # R0057 TEMPORARY DIAGNOSTIC: the two blocks below previously used
        # `contextlib.suppress(...)`, which absorbs an exception with ZERO
        # visibility into what was absorbed. A RETURNED marker alone does
        # not distinguish "completed cleanly" from "an exception was
        # silently swallowed here." Replaced with explicit try/except that
        # logs exactly one of four distinguishable outcomes
        # (clean / timeout / cancelled / exception_suppressed) before
        # continuing -- the ABSORPTION behavior itself (nothing propagates
        # further than before) is unchanged; only its visibility is added.
        print("RUNNER_END_BEGIN", flush=True)
        try:
            await runner.end(reason="probe done")
            print("RUNNER_END_OUTCOME=clean", flush=True)
        except asyncio.CancelledError as e:
            print(f"RUNNER_END_OUTCOME=cancelled repr={e!r}", flush=True)
        except Exception as e:
            print(
                f"RUNNER_END_OUTCOME=exception_suppressed type={type(e).__name__} repr={e!r}",
                flush=True,
            )
        print("RUNNER_END_RETURNED", flush=True)

        print("RUN_TASK_AWAIT_BEGIN", flush=True)
        try:
            await asyncio.wait_for(run_task, timeout=10)
            print("RUN_TASK_AWAIT_OUTCOME=clean", flush=True)
        except TimeoutError:
            print("RUN_TASK_AWAIT_OUTCOME=timeout_10s", flush=True)
        except asyncio.CancelledError as e:
            print(f"RUN_TASK_AWAIT_OUTCOME=cancelled repr={e!r}", flush=True)
        except Exception as e:
            print(
                f"RUN_TASK_AWAIT_OUTCOME=exception_suppressed type={type(e).__name__} repr={e!r}",
                flush=True,
            )
        print("RUN_TASK_AWAIT_RETURNED", flush=True)

        teardown_done.set()
        watchdog.cancel()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "report": "M2.6B.4M / R0052, gain fix wiring R0053, self-echo probe",
        "generated_utc": datetime.now(UTC).isoformat(),
        "mode": "control" if args.control else f"silent_level_{args.level}",
        "aec_ref_active_at_start": aec_health.active,
        "audible_mixer_card": cfg.output_alsa_mixer_card,
        "audible_gain_db_at_startup": _gain_to_db_text(startup_gain),
        "audible_linear_gain_at_startup": startup_gain,
        "warmup_result": warmup_result,
        "trials": trials,
    }
    path = OUT_DIR / f"self_echo_probe_{ts}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    total_confirmed = sum(t["bargein_confirmed_count"] for t in trials)
    print("\n" + "=" * 74)
    if args.control:
        print(f"CONTROL RESULT: {total_confirmed} confirmed barge-in(s) (expect exactly 1)")
    else:
        print(
            f"SILENT '{args.level.upper()}' RESULT: {total_confirmed} false confirmed "
            f"barge-in(s) across {len(trials)} trials (expect 0)"
        )
    print(f"results JSON: {path}")
    print("This probe does NOT choose a fix -- it only measures.")
    return 0


async def _run_with_initiating_exception_report(args: argparse.Namespace) -> int:
    """R0057 TEMPORARY DIAGNOSTIC (reproduction checkpoint, not a fix).

    Wraps the REAL, unmodified ``_run(args)`` coroutine -- zero change to
    its own control flow, timing, assertion positions, or completion
    waits. This wrapper exists ONLY to observe whatever exception (if
    any) first escapes ``_run()``, and to do so from INSIDE the task
    ``asyncio.run()`` itself creates and awaits.

    Why "inside the asyncio.run() boundary" matters (confirmed from the
    installed CPython 3.13.5 ``asyncio/runners.py`` source this
    checkpoint's own prior audit read directly): ``asyncio.run(coro)`` is
    ``with Runner(...) as runner: return runner.run(coro)``;
    ``Runner.run()`` does ``task = loop.create_task(coro); return
    loop.run_until_complete(task)``. If ``coro`` (here, this wrapper)
    raises, that exception propagates out of ``run_until_complete``
    -- but ``Runner.close()`` (``_cancel_all_tasks(loop)``, which forcibly
    cancels every OTHER still-pending task, including the probe's own
    ``run_task``) only runs afterward, in the ``with`` block's own
    ``__exit__``. Anything printed and flushed **inside** this wrapper's
    own ``except`` clause therefore executes, and is guaranteed to reach
    disk, strictly BEFORE that later cleanup ever begins -- regardless of
    whether that later cleanup itself goes on to hang. This is the exact
    mechanism the prior read-only audit identified as the leading,
    unconfirmed hypothesis for why neither of the two failed real-hardware
    runs ever showed a traceback.

    ``asyncio.CancelledError`` is handled separately from other
    exceptions per this checkpoint's own instruction: it is not a normal
    application error and must never be conflated with one in the report
    this produces. Both branches unconditionally re-raise -- this wrapper
    only observes, it never suppresses or changes what ultimately
    happens to the real exception."""
    try:
        return await _run(args)
    except asyncio.CancelledError:
        print(
            f"INITIATING_CANCELLED_ERROR t_monotonic={time.monotonic():.6f}",
            flush=True,
        )
        raise
    except Exception as e:
        print(
            f"INITIATING_EXCEPTION t_monotonic={time.monotonic():.6f} "
            f"type={type(e).__name__} message={e!r}",
            flush=True,
        )
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        raise


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(_run_with_initiating_exception_report(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
