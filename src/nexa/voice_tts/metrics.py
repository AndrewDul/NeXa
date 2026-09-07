"""M2.4B.1 — realtime speech-flow instrumentation (measure-only).

This module **observes** the existing, frozen M2.4 pipeline
(`AssistantSpeechBridge` -> `PiperHttpTTSService` -> `TtsStatusObserver` ->
`LocalAudioOutputTransport`). It adds:

- no scheduling, no batching, no phrase planner
- no second conversation session, model client, TTS scheduler, or audio queue
- no extra request to Ollama or Piper
- no `VoiceState` transitions

Every recorded value is a real measured timestamp/count, or ``None`` — never
fabricated. The single derived quantity that is an *estimate*
(``buffered_audio_seconds``) is labelled ``ESTIMATE`` everywhere it appears
and is validated against real ``BotStoppedSpeaking`` events in R0013; it is
**not** used to drive any runtime behaviour in this stage.

Pure metric math + a report-mode-only OS resource sampler. The math classes
have no threads and no subprocess; the sampler is opt-in and confined to the
bottom of this module.

Ollama per-generation metadata (``load_duration``, ``prompt_eval_*``,
``eval_*``) is **not** collected here: `nexa.providers.ollama` parses and
then discards Ollama's final ``done`` chunk, and threading a callback out to
the probe would touch the canonical `ConversationSession` path (4+ files) —
outside instrumentation-only scope. See R0013 "OLLAMA METADATA FINDINGS"
for the minimal additive design deferred to a separate task. B.1's
first-token/contention numbers come from the in-tree wall-clock timings
here plus the standalone experiment in
``docs/research/m2_4b_speech_flow/first_token_contention.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .timing import TurnTiming

# Pipecat local audio + Piper medium are s16le; 2 bytes per sample per channel.
_BYTES_PER_SAMPLE = 2

# M2.4B.2: a TTSTextFrame shorter than this many characters is a "pathological
# tiny chunk" — the fragmented-speech symptom the speech planner exists to
# remove (e.g. "1.", "np.", "tzw."). Reported per turn for the B.2 baseline
# comparison; not a control signal.
TINY_TEXT_CHUNK_CHARS = 12

BUFFER_ESTIMATE_NOTE = (
    "ESTIMATE (R0012 INFERENCE): sum(TTS audio bytes seen) / (sample_rate * 2 * "
    "channels) minus wall-clock since this turn's first audio. Ignores the "
    "output transport's own small internal buffering and resample rounding. "
    "Validated against real BotStoppedSpeaking in R0013; NOT a control signal."
)


def audio_bytes_to_seconds(
    audio_bytes: int, sample_rate: int | None, num_channels: int | None
) -> float | None:
    """s16le byte count -> seconds of audio. ``None`` if the rate is unknown
    or non-positive; never raises, never returns NaN."""
    if not sample_rate or sample_rate <= 0:
        return None
    channels = num_channels if num_channels and num_channels > 0 else 1
    denom = sample_rate * _BYTES_PER_SAMPLE * channels
    if denom <= 0 or audio_bytes < 0:
        return None
    return audio_bytes / denom


# --------------------------------------------------------------------------- #
# Per-turn records
# --------------------------------------------------------------------------- #


@dataclass
class TtsTextChunk:
    """One ``TTSTextFrame`` — a sentence the TTS service actually synthesized
    (current M2.4 aggregation; B.1 introduces no new phrase semantics)."""

    index: int
    text_len: int
    text_preview: str
    ready_at: float
    since_prev_s: float | None
    after_assistant_complete: bool


@dataclass
class TtsSegment:
    """One ``TTSStartedFrame`` -> ``TTSStoppedFrame`` span (a Pipecat audio
    context / synthesis segment). A single M2.4 reply is normally one
    segment; the 3 s ``stop_frame_timeout_s`` split (R0012 §F) produces
    more than one — counting them is a goal of this stage."""

    index: int
    started_at: float
    first_audio_at: float | None = None
    stopped_at: float | None = None
    audio_bytes: int = 0
    sample_rate: int | None = None
    num_channels: int | None = None
    sample_width_bytes: int = _BYTES_PER_SAMPLE

    @property
    def context_span_s(self) -> float | None:
        """Wall time of the Pipecat **audio context** span
        (``TTSStartedFrame`` → ``TTSStoppedFrame``). This is NOT the HTTP
        synthesis time — it is inflated by playback drain and the 3 s
        ``stop_frame_timeout_s`` (R0013 "B.1 METRIC CORRECTIONS"). Use
        :class:`~nexa.voice_tts.timed_tts.HttpSynthCall` /
        ``TurnMetrics.mean_http_rtf`` for the true synthesis speed."""
        if self.stopped_at is None:
            return None
        return self.stopped_at - self.started_at

    @property
    def time_to_first_audio_s(self) -> float | None:
        if self.first_audio_at is None:
            return None
        return self.first_audio_at - self.started_at

    @property
    def audio_duration_s(self) -> float | None:
        return audio_bytes_to_seconds(self.audio_bytes, self.sample_rate, self.num_channels)

    @property
    def context_span_rtf(self) -> float | None:
        """``context_span_s / audio_duration_s`` — the *context lifecycle*
        ratio, NOT true synthesis RTF (see ``context_span_s``). Kept for
        diagnostics only."""
        dur = self.context_span_s
        aud = self.audio_duration_s
        if dur is None or aud is None or aud <= 0:
            return None
        return dur / aud

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "started_at": self.started_at,
            "first_audio_at": self.first_audio_at,
            "stopped_at": self.stopped_at,
            "audio_bytes": self.audio_bytes,
            "sample_rate": self.sample_rate,
            "num_channels": self.num_channels,
            "context_span_s": _round(self.context_span_s),
            "time_to_first_audio_s": _round(self.time_to_first_audio_s),
            "audio_duration_s": _round(self.audio_duration_s),
            "context_span_rtf": _round(self.context_span_rtf, 3),
        }


@dataclass
class PlaybackSpan:
    """One ``BotStartedSpeakingFrame`` -> ``BotStoppedSpeakingFrame`` span."""

    started_at: float
    stopped_at: float | None = None

    @property
    def duration_s(self) -> float | None:
        if self.stopped_at is None:
            return None
        return self.stopped_at - self.started_at

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "duration_s": _round(self.duration_s),
        }


@dataclass
class BufferEstimate:
    """The labelled ``buffered_audio_seconds`` estimate for one turn.

    ``on_audio`` accumulates real audio seconds seen; ``value_at`` subtracts
    wall-clock elapsed since the turn's first audio. All arithmetic is
    guarded so the result is always a finite float or ``None`` — never NaN,
    never an exception.
    """

    note: str = BUFFER_ESTIMATE_NOTE
    total_audio_seconds: float = 0.0
    first_audio_at: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    samples: list[tuple[float, float, str]] = field(default_factory=list)  # (at, value, label)
    underrun_events: list[float] = field(default_factory=list)  # wall time of each <=0 crossing
    _below_zero: bool = field(default=False, repr=False)

    def on_audio(
        self, audio_bytes: int, sample_rate: int | None, num_channels: int | None
    ) -> None:
        secs = audio_bytes_to_seconds(audio_bytes, sample_rate, num_channels)
        if secs is None:
            return
        self.total_audio_seconds += secs

    def value_at(self, now: float) -> float | None:
        if self.first_audio_at is None:
            return None
        return self.total_audio_seconds - (now - self.first_audio_at)

    def sample(self, now: float, label: str) -> float | None:
        """Record a labelled buffer sample and track min/max + underruns."""
        v = self.value_at(now)
        if v is None:
            return None
        self.samples.append((now, v, label))
        self.min_value = v if self.min_value is None else min(self.min_value, v)
        self.max_value = v if self.max_value is None else max(self.max_value, v)
        if v <= 0.0:
            if not self._below_zero:
                self.underrun_events.append(now)
            self._below_zero = True
        else:
            self._below_zero = False
        return v

    def to_dict(self) -> dict:
        return {
            "note": self.note,
            "total_audio_seconds": _round(self.total_audio_seconds),
            "min_value_s": _round(self.min_value),
            "max_value_s": _round(self.max_value),
            "underrun_events": len(self.underrun_events),
            "sample_count": len(self.samples),
            "samples_at_labels": [
                {"at": _round(at, 3), "value_s": _round(v), "label": lab}
                for at, v, lab in self.samples
            ],
        }


@dataclass
class ResourceWindowSummary:
    """OS-resource summary over one turn's wall-clock window (report mode)."""

    sample_count: int = 0
    cpu_total_pct_mean: float | None = None
    cpu_total_pct_peak: float | None = None
    llama_server_cpu_pct_mean: float | None = None
    llama_server_cpu_pct_peak: float | None = None
    temp_c_max: float | None = None
    throttled_any_hex: str | None = None
    mem_available_mb_min: float | None = None
    swap_used_mb_max: float | None = None
    stt_overlap: bool = False
    tts_overlap: bool = False
    generation_overlap: bool = False
    sampler_overhead_ms_mean: float | None = None

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "cpu_total_pct_mean": _round(self.cpu_total_pct_mean, 1),
            "cpu_total_pct_peak": _round(self.cpu_total_pct_peak, 1),
            "llama_server_cpu_pct_mean": _round(self.llama_server_cpu_pct_mean, 1),
            "llama_server_cpu_pct_peak": _round(self.llama_server_cpu_pct_peak, 1),
            "temp_c_max": _round(self.temp_c_max, 1),
            "throttled_any_hex": self.throttled_any_hex,
            "mem_available_mb_min": _round(self.mem_available_mb_min, 1),
            "swap_used_mb_max": _round(self.swap_used_mb_max, 1),
            "stt_overlap": self.stt_overlap,
            "tts_overlap": self.tts_overlap,
            "generation_overlap": self.generation_overlap,
            "sampler_overhead_ms_mean": _round(self.sampler_overhead_ms_mean, 3),
        }


@dataclass
class TurnMetrics:
    """The full per-turn instrumentation record. Composes the existing
    lean :class:`~nexa.voice_tts.timing.TurnTiming` rather than duplicating
    its fields."""

    turn_index: int
    timing: TurnTiming
    stt_text_preview: str = ""
    stt_text_len: int = 0
    stt_wall_latency_s: float | None = None
    assistant_text_preview: str = ""
    assistant_text_chars: int | None = None
    tts_segments: list[TtsSegment] = field(default_factory=list)
    text_chunks: list[TtsTextChunk] = field(default_factory=list)
    http_calls: list = field(default_factory=list)  # list[HttpSynthCall] (M2.4B.1A)
    controller_releases: list = field(default_factory=list)  # list[ControllerRelease] (M2.4B.3.2)
    playback_spans: list[PlaybackSpan] = field(default_factory=list)
    buffer: BufferEstimate = field(default_factory=BufferEstimate)
    resources: ResourceWindowSummary | None = None
    error: str | None = None
    started_wall: float | None = None
    finalized_wall: float | None = None
    _first_audio_sampled: bool = field(default=False, repr=False)
    _response_end_seen: bool = field(default=False, repr=False)

    # -- LLM-side derived ------------------------------------------------- #

    @property
    def first_token_latency_s(self) -> float | None:
        """Time from the moment there was something to answer to the first
        assistant token. Uses ``stt_result`` as the base when available,
        else ``end_of_turn``; ``None`` if neither or no first token."""
        ft = self.timing.first_token
        if ft is None:
            return None
        base = self.timing.stt_result
        if base is None:
            base = self.timing.end_of_turn
        if base is None:
            return None
        return ft - base

    @property
    def first_token_latency_base(self) -> str | None:
        if self.timing.first_token is None:
            return None
        if self.timing.stt_result is not None:
            return "stt_result"
        if self.timing.end_of_turn is not None:
            return "end_of_turn"
        return None

    @property
    def assistant_generation_duration_s(self) -> float | None:
        ft, ac = self.timing.first_token, self.timing.assistant_complete
        if ft is None or ac is None:
            return None
        return ac - ft

    @property
    def llm_chars_per_s(self) -> float | None:
        dur = self.assistant_generation_duration_s
        if dur is None or dur <= 0 or self.assistant_text_chars is None:
            return None
        return self.assistant_text_chars / dur

    # -- Text -> TTS (post speech-planner) ----------------------------- #

    @property
    def tiny_text_chunk_count(self) -> int:
        """How many ``TTSTextFrame`` chunks this turn were shorter than
        :data:`TINY_TEXT_CHUNK_CHARS` — the fragmented-speech symptom
        M2.4B.2's planner targets. 0 is the goal."""
        return sum(1 for c in self.text_chunks if c.text_len < TINY_TEXT_CHUNK_CHARS)

    @property
    def mean_text_chunk_chars(self) -> float | None:
        if not self.text_chunks:
            return None
        return sum(c.text_len for c in self.text_chunks) / len(self.text_chunks)

    # -- Speech continuity controller (M2.4B.3.2) --------------------- #

    @property
    def controller_held_count(self) -> int:
        """Phrases the continuity controller actually held (hold > 50 ms).
        The controller must add no *avoidable* latency — this and
        ``controller_max_hold_s`` are how we prove it."""
        return sum(1 for c in self.controller_releases if getattr(c, "hold_s", 0.0) > 0.05)

    @property
    def controller_max_hold_s(self) -> float | None:
        holds = [getattr(c, "hold_s", 0.0) for c in self.controller_releases]
        return max(holds) if holds else None

    @property
    def controller_mean_hold_s(self) -> float | None:
        holds = [getattr(c, "hold_s", 0.0) for c in self.controller_releases]
        return (sum(holds) / len(holds)) if holds else None

    # -- Playback / gaps ------------------------------------------------- #

    @property
    def bot_started_count(self) -> int:
        return len(self.playback_spans)

    @property
    def bot_stopped_count(self) -> int:
        return sum(1 for s in self.playback_spans if s.stopped_at is not None)

    @property
    def first_playback_started_at(self) -> float | None:
        return self.playback_spans[0].started_at if self.playback_spans else None

    @property
    def final_playback_stopped_at(self) -> float | None:
        stops = [s.stopped_at for s in self.playback_spans if s.stopped_at is not None]
        return stops[-1] if stops else None

    @property
    def silence_gaps_ms(self) -> list[float]:
        """Silence between consecutive playback spans: span[i+1].start -
        span[i].stop, in ms, only where both timestamps exist and the gap is
        positive."""
        gaps: list[float] = []
        for a, b in zip(self.playback_spans, self.playback_spans[1:], strict=False):
            if a.stopped_at is None:
                continue
            gap = b.started_at - a.stopped_at
            if gap > 0:
                gaps.append(gap * 1000.0)
        return gaps

    @property
    def max_gap_ms(self) -> float | None:
        gaps = self.silence_gaps_ms
        return max(gaps) if gaps else None

    @property
    def mean_gap_ms(self) -> float | None:
        gaps = self.silence_gaps_ms
        return sum(gaps) / len(gaps) if gaps else None

    @property
    def output_underrun_count(self) -> int:
        return len(self.buffer.underrun_events)

    @property
    def mean_http_rtf(self) -> float | None:
        """TRUE mean Piper synthesis real-time factor — one HTTP request per
        sentence, request issued → whole WAV received (M2.4B.1A). Falls back
        to ``None`` (never to the inflated context-span ratio) when no
        :class:`~nexa.voice_tts.timed_tts.TimedPiperHttpTTSService` was used."""
        rtfs = [c.http_rtf for c in self.http_calls if getattr(c, "http_rtf", None) is not None]
        return sum(rtfs) / len(rtfs) if rtfs else None

    @property
    def mean_http_synthesis_s(self) -> float | None:
        walls = [c.http_wall_s for c in self.http_calls if getattr(c, "http_wall_s", None)]
        return sum(walls) / len(walls) if walls else None

    @property
    def mean_context_span_rtf(self) -> float | None:
        """Mean of the Pipecat *context-span* ratio — diagnostic only, NOT
        true synthesis RTF (R0013 correction)."""
        rtfs = [s.context_span_rtf for s in self.tts_segments if s.context_span_rtf is not None]
        return sum(rtfs) / len(rtfs) if rtfs else None

    @property
    def total_audio_seconds(self) -> float:
        return self.buffer.total_audio_seconds

    @property
    def audio_seconds_per_wall_second(self) -> float | None:
        """Audio produced ÷ the turn's spoken wall window (first playback
        start → final playback stop). ~1.0 = kept up with real time; < 1.0
        = the pipeline ran dry for part of the reply."""
        start = self.first_playback_started_at
        end = self.final_playback_stopped_at
        if start is None or end is None or end <= start:
            return None
        return self.total_audio_seconds / (end - start)

    # -- Buffer-estimate validation ----------------------------------------- #

    @property
    def buffer_drain_to_stop_lag_s(self) -> float | None:
        """For each buffer-underrun event (estimate crossed ≤ 0), the delay
        until the **next** ``BotStoppedSpeaking``. Mean over events that had
        a following stop. A healthy value is ≈ the transport's ~3 s
        ``BOT_VAD_STOP_FALLBACK_SECS`` — the estimate correctly predicts the
        drain a few seconds before the transport declares it. A large value
        (or many ``underruns_without_following_stop``) means the estimate
        and reality disagree — do NOT use the estimate as a control signal
        until that is understood (R0013 / R0014). ``None`` if no underrun
        event had a following stop."""
        stops = sorted(s.stopped_at for s in self.playback_spans if s.stopped_at is not None)
        lags: list[float] = []
        for ev in self.buffer.underrun_events:
            later = [st - ev for st in stops if st >= ev - 0.05]
            if later:
                lags.append(min(later))
        return sum(lags) / len(lags) if lags else None

    @property
    def underruns_without_following_stop(self) -> int:
        stops = [s.stopped_at for s in self.playback_spans if s.stopped_at is not None]
        return sum(
            1 for ev in self.buffer.underrun_events if not any(st >= ev - 0.05 for st in stops)
        )

    # Back-compat alias for the JSONL key; now points at the corrected metric.
    @property
    def estimate_vs_real_stop_error_s(self) -> float | None:
        return self.buffer_drain_to_stop_lag_s

    # -- Serialisation / rendering ---------------------------------------- #

    def to_dict(self, *, include_text: bool = False) -> dict:
        d = {
            "turn_index": self.turn_index,
            "error": self.error,
            "timing": {
                "end_of_turn_at": self.timing.end_of_turn,
                "stt_result_at": self.timing.stt_result,
                "assistant_first_token_at": self.timing.first_token,
                "assistant_complete_at": self.timing.assistant_complete,
                "tts_started_at": self.timing.tts_started,
                "first_tts_audio_at": self.timing.tts_first_audio,
                "tts_stopped_at": self.timing.tts_stopped,
            },
            "llm": {
                "first_token_latency_s": _round(self.first_token_latency_s),
                "first_token_latency_base": self.first_token_latency_base,
                "assistant_generation_duration_s": _round(self.assistant_generation_duration_s),
                "assistant_text_chars": self.assistant_text_chars,
                "assistant_text_tokens": None,  # not available without provider change (R0013)
                "llm_chars_per_s": _round(self.llm_chars_per_s, 2),
                "llm_tokens_per_s": None,
                "ollama_load_duration_s": None,
                "ollama_prompt_eval_count": None,
                "ollama_prompt_eval_duration_s": None,
                "ollama_eval_count": None,
                "ollama_eval_duration_s": None,
            },
            "stt": {
                "text_len": self.stt_text_len,
                "wall_latency_s": _round(self.stt_wall_latency_s),
            },
            "text_chunks": [
                {
                    "index": c.index,
                    "text_len": c.text_len,
                    "ready_at": c.ready_at,
                    "since_prev_s": _round(c.since_prev_s),
                    "after_assistant_complete": c.after_assistant_complete,
                }
                for c in self.text_chunks
            ],
            "text_to_tts": {
                "chunk_count": len(self.text_chunks),
                "mean_chunk_chars": _round(self.mean_text_chunk_chars, 1),
                "tiny_chunk_count": self.tiny_text_chunk_count,
                "tiny_chunk_threshold_chars": TINY_TEXT_CHUNK_CHARS,
            },
            "tts_http_synthesis": {
                "true_request_count": len(self.http_calls),
                "mean_http_wall_s": _round(self.mean_http_synthesis_s),
                "mean_http_rtf": _round(self.mean_http_rtf, 3),
                "calls": [
                    {
                        "text_len": getattr(c, "text_len", None),
                        "http_wall_s": _round(getattr(c, "http_wall_s", None)),
                        "ttfb_s": _round(getattr(c, "ttfb_s", None)),
                        "audio_s": _round(getattr(c, "audio_s", None)),
                        "http_rtf": _round(getattr(c, "http_rtf", None), 3),
                    }
                    for c in self.http_calls
                ],
            },
            "speech_continuity_controller": {
                "release_count": len(self.controller_releases),
                "held_count": self.controller_held_count,
                "max_hold_s": _round(self.controller_max_hold_s),
                "mean_hold_s": _round(self.controller_mean_hold_s),
                "releases": [
                    {
                        "phrase_index": getattr(c, "phrase_index", None),
                        "text_len": getattr(c, "text_len", None),
                        "hold_s": _round(getattr(c, "hold_s", None)),
                        "reserve_at_receive_s": _round(getattr(c, "reserve_at_receive_s", None)),
                        "reserve_at_release_s": _round(getattr(c, "reserve_at_release_s", None)),
                        "reason": getattr(c, "reason", None),
                    }
                    for c in self.controller_releases
                ],
            },
            "tts_segments": [s.to_dict() for s in self.tts_segments],
            "tts_context_span_rtf_mean": _round(self.mean_context_span_rtf, 3),
            "playback": {
                "first_started_at": self.first_playback_started_at,
                "final_stopped_at": self.final_playback_stopped_at,
                "bot_started_count": self.bot_started_count,
                "bot_stopped_count": self.bot_stopped_count,
                "silence_gaps_ms": [_round(g, 1) for g in self.silence_gaps_ms],
                "max_gap_ms": _round(self.max_gap_ms, 1),
                "mean_gap_ms": _round(self.mean_gap_ms, 1),
                "output_underrun_count": self.output_underrun_count,
                "audio_seconds_per_wall_second": _round(self.audio_seconds_per_wall_second),
            },
            "buffer_estimate": self.buffer.to_dict(),
            "buffer_estimate_validation": {
                "buffer_drain_to_stop_lag_s": _round(self.buffer_drain_to_stop_lag_s),
                "underruns_without_following_stop": self.underruns_without_following_stop,
                "estimate_vs_real_stop_error_s": _round(self.estimate_vs_real_stop_error_s),
            },
            "diagnosis": diagnose_dominant_wait(self),
        }
        if self.resources is not None:
            d["resources"] = self.resources.to_dict()
        if include_text:
            d["stt_text_preview"] = self.stt_text_preview
            d["assistant_text_preview"] = self.assistant_text_preview
        return d


def _round(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(x, n)


# --------------------------------------------------------------------------- #
# Diagnosis — only a category the measured numbers support, else UNKNOWN
# --------------------------------------------------------------------------- #

DOMINANT_WAIT_CATEGORIES = (
    "FIRST TOKEN",
    "LLM TEXT PRODUCTION",
    "TTS SYNTHESIS",
    "OUTPUT UNDERRUN",
    "UNKNOWN",
)


def diagnose_dominant_wait(tm: TurnMetrics) -> str:
    """Pick the dominant source of speech-flow wait for one turn, or
    ``"UNKNOWN"``. Heuristic thresholds (documented in R0013) — deliberately
    conservative: only names a cause when the measured numbers clearly
    support it."""
    ftl = tm.first_token_latency_s
    gaps = tm.silence_gaps_ms
    gap_total_s = sum(gaps) / 1000.0 if gaps else 0.0
    # TRUE synthesis RTF (M2.4B.1A) — the context-span ratio is NOT synthesis
    # speed and must never drive this classification.
    rtf = tm.mean_http_rtf
    underruns = tm.output_underrun_count

    # Span of the turn's spoken part, for proportion checks.
    start = tm.timing.stt_result or tm.timing.end_of_turn
    end = tm.final_playback_stopped_at
    total = (end - start) if (start is not None and end is not None and end > start) else None

    # 1. FIRST TOKEN — a long, dominant wait before any assistant text.
    if ftl is not None and ftl >= 3.0 and (total is None or ftl >= 0.4 * total):
        return "FIRST TOKEN"

    # 2. LLM TEXT PRODUCTION — audible gaps, buffer underran, synth was fast.
    if gap_total_s >= 1.0 and underruns >= 1 and (rtf is None or rtf < 0.6):
        return "LLM TEXT PRODUCTION"

    # 3. TTS SYNTHESIS — synth itself is not keeping up (RTF near/over 1).
    if rtf is not None and rtf >= 0.85 and gap_total_s >= 1.0:
        return "TTS SYNTHESIS"

    # 4. OUTPUT UNDERRUN — buffer ran dry although LLM and synth were fast.
    if underruns >= 1 and gap_total_s >= 1.0 and (rtf is not None and rtf < 0.6):
        return "OUTPUT UNDERRUN"

    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# Collector — FIFO turn correlation, fed by the probe's existing callbacks
# --------------------------------------------------------------------------- #


class MetricsCollector:
    """Observes the M2.4 pipeline via the probe's existing callbacks and
    emits one :class:`TurnMetrics` per turn.

    Conversation-side calls (``start_turn``/``first_token``/``assistant_token``/
    ``assistant_complete``/``conversation_error``) are single-turn-at-a-time
    (``SerialConversationQueue``). TTS-side calls are attributed FIFO to the
    oldest turn whose TTS lifecycle has not yet ended — the same rule the
    existing ``TurnTimingTracker`` uses. A turn is finalised on the
    downstream ``LLMFullResponseEndFrame`` (arrives after that turn's final
    audio), or defensively when the next turn starts / at ``close()``.
    """

    def __init__(
        self,
        *,
        on_turn_finalized: Callable[[TurnMetrics], None] | None = None,
        resource_summary_fn: Callable[[float, float], ResourceWindowSummary | None] | None = None,
        activity: ActivityFlags | None = None,
    ) -> None:
        self._on_turn_finalized = on_turn_finalized
        self._resource_summary_fn = resource_summary_fn
        self._activity = activity
        self._active: TurnMetrics | None = None
        self._queue: list[TurnMetrics] = []
        self._counter = 0

    # -- conversation side --------------------------------------------------- #

    def start_turn(
        self,
        *,
        end_of_turn: float | None = None,
        stt_result: float | None = None,
        stt_text: str = "",
        stt_wall_latency_s: float | None = None,
    ) -> TurnMetrics:
        # Defensive: a prior turn that never got its LLMFullResponseEndFrame.
        if self._active is not None:
            self._finalize(self._active)
        self._counter += 1
        tm = TurnMetrics(
            turn_index=self._counter,
            timing=TurnTiming(end_of_turn=end_of_turn, stt_result=stt_result),
            stt_text_preview=_preview(stt_text),
            stt_text_len=len(stt_text),
            stt_wall_latency_s=stt_wall_latency_s,
            started_wall=time.monotonic(),
        )
        self._active = tm
        self._queue.append(tm)
        if self._activity is not None:
            self._activity.generation_active = True
        return tm

    def first_token(self) -> None:
        if self._active is not None and self._active.timing.first_token is None:
            self._active.timing.first_token = time.monotonic()

    def assistant_token(self, token: str) -> None:
        tm = self._active
        if tm is None:
            return
        if tm.assistant_text_chars is None:
            tm.assistant_text_chars = 0
        tm.assistant_text_chars += len(token)
        if len(tm.assistant_text_preview) < _PREVIEW_LEN:
            tm.assistant_text_preview = _preview(tm.assistant_text_preview + token)

    def assistant_complete(self, text: str | None = None) -> None:
        tm = self._active
        if tm is None:
            return
        tm.timing.assistant_complete = time.monotonic()
        if text is not None:
            tm.assistant_text_chars = len(text)
            tm.assistant_text_preview = _preview(text)
        self._active = None
        if self._activity is not None:
            self._activity.generation_active = False

    def conversation_error(self, exc: BaseException) -> None:
        tm = self._active
        if tm is None:
            return
        tm.error = f"{type(exc).__name__}: {exc}"
        tm.timing.assistant_complete = time.monotonic()
        self._active = None
        if self._activity is not None:
            self._activity.generation_active = False

    # -- TTS side (FIFO: oldest turn still speaking) ----------------------- #

    def _head(self) -> TurnMetrics | None:
        """The oldest turn whose TTS lifecycle has not finished. Never a
        turn that has already been finalised (defensive against a stray late
        frame — M2.4B.1A)."""
        for tm in self._queue:
            if tm.finalized_wall is None:
                return tm
        return None

    def http_synthesis(self, call) -> None:
        """One real ``run_tts`` HTTP request completed
        (:class:`~nexa.voice_tts.timed_tts.HttpSynthCall`) — the TRUE Piper
        synthesis timing. Attributed FIFO like the other TTS-side events."""
        tm = self._head()
        if tm is not None:
            tm.http_calls.append(call)

    def controller_release(self, rel) -> None:
        """One phrase released by ``NexaSpeechContinuityController``
        (:class:`~nexa.voice_tts.continuity.ControllerRelease`). Attributed
        FIFO like the other TTS-side events (M2.4B.3.2)."""
        tm = self._head()
        if tm is not None:
            tm.controller_releases.append(rel)

    def tts_started(self) -> None:
        tm = self._head()
        if tm is None:
            return
        if tm.timing.tts_started is None:
            tm.timing.tts_started = time.monotonic()
        tm.tts_segments.append(TtsSegment(index=len(tm.tts_segments), started_at=time.monotonic()))
        if self._activity is not None:
            self._activity.tts_active = True

    def tts_first_audio(self) -> None:
        tm = self._head()
        if tm is None:
            return
        now = time.monotonic()
        if tm.timing.tts_first_audio is None:
            tm.timing.tts_first_audio = now
        if tm.tts_segments and tm.tts_segments[-1].first_audio_at is None:
            tm.tts_segments[-1].first_audio_at = now
        if tm.buffer.first_audio_at is None:
            tm.buffer.first_audio_at = now
        # No buffer sample here: the observer fires on_tts_first_audio *before*
        # on_tts_audio for the very first frame, so sampling now would read
        # total_audio_seconds == 0 and log a spurious underrun. The first
        # buffer sample is taken in tts_audio() once real bytes are counted.

    def tts_audio(
        self, audio_bytes: int, sample_rate: int | None, num_channels: int | None
    ) -> None:
        tm = self._head()
        if tm is None:
            return
        if tm.tts_segments:
            seg = tm.tts_segments[-1]
            seg.audio_bytes += max(0, audio_bytes)
            if seg.sample_rate is None and sample_rate:
                seg.sample_rate = sample_rate
            if seg.num_channels is None and num_channels:
                seg.num_channels = num_channels
        tm.buffer.on_audio(audio_bytes, sample_rate, num_channels)
        if not tm._first_audio_sampled and tm.buffer.first_audio_at is not None:
            tm._first_audio_sampled = True
            tm.buffer.sample(time.monotonic(), "first_audio")

    def tts_text(self, text: str) -> None:
        tm = self._head()
        if tm is None:
            return
        now = time.monotonic()
        prev = tm.text_chunks[-1].ready_at if tm.text_chunks else None
        tm.text_chunks.append(
            TtsTextChunk(
                index=len(tm.text_chunks),
                text_len=len(text),
                text_preview=_preview(text),
                ready_at=now,
                since_prev_s=(now - prev) if prev is not None else None,
                after_assistant_complete=tm.timing.assistant_complete is not None,
            )
        )

    def tts_stopped(self) -> None:
        tm = self._head()
        if tm is None:
            return
        now = time.monotonic()
        if tm.timing.tts_stopped is None:
            tm.timing.tts_stopped = now
        for seg in tm.tts_segments:
            if seg.stopped_at is None:
                seg.stopped_at = now
        tm.buffer.sample(now, "tts_stopped")
        if self._activity is not None:
            self._activity.tts_active = False

    def bot_started_speaking(self) -> None:
        tm = self._head()
        if tm is None:
            return
        tm.playback_spans.append(PlaybackSpan(started_at=time.monotonic()))

    def bot_stopped_speaking(self) -> None:
        tm = self._head()
        if tm is None:
            return
        now = time.monotonic()
        for span in reversed(tm.playback_spans):
            if span.stopped_at is None:
                span.stopped_at = now
                break
        tm.buffer.sample(now, "bot_stopped")
        # The downstream LLMFullResponseEndFrame can arrive *before* the last
        # BotStoppedSpeakingFrame (observed on real hardware, R0013). When it
        # did, finalise now that playback has actually settled.
        if tm._response_end_seen and not _has_open_span(tm):
            self._pop_and_finalize(tm)

    def tts_response_end(self) -> TurnMetrics | None:
        """Downstream ``LLMFullResponseEndFrame`` — generation + this turn's
        Pipecat audio context are done. Finalise now *if* playback has also
        settled; otherwise defer to the next ``bot_stopped_speaking``."""
        tm = self._head()
        if tm is None:
            return None
        tm._response_end_seen = True
        if _has_open_span(tm):
            return None  # a BotStoppedSpeakingFrame is still owed — wait for it
        return self._pop_and_finalize(tm)

    def _pop_and_finalize(self, tm: TurnMetrics) -> TurnMetrics | None:
        if tm in self._queue:
            self._queue.remove(tm)
        return self._finalize(tm)

    def periodic_buffer_sample(self) -> None:
        tm = self._head()
        if tm is not None:
            tm.buffer.sample(time.monotonic(), "periodic")

    def close(self) -> list[TurnMetrics]:
        """Finalise any turn still in flight (missing LLMFullResponseEndFrame /
        session shutdown). Returns the finalised records."""
        out: list[TurnMetrics] = []
        if self._active is not None and self._active not in self._queue:
            out.append(self._finalize(self._active))
        while self._queue:
            out.append(self._finalize(self._queue.pop(0)))
        self._active = None
        return out

    def _finalize(self, tm: TurnMetrics) -> TurnMetrics:
        if tm.finalized_wall is not None:
            return tm
        tm.finalized_wall = time.monotonic()
        if self._resource_summary_fn is not None and tm.started_wall is not None:
            try:
                tm.resources = self._resource_summary_fn(tm.started_wall, tm.finalized_wall)
            except Exception:
                tm.resources = None
        if self._on_turn_finalized is not None:
            try:
                self._on_turn_finalized(tm)
            except Exception:
                pass
        return tm


_PREVIEW_LEN = 60


def _has_open_span(tm: TurnMetrics) -> bool:
    """True if a ``BotStartedSpeaking`` has no matching ``BotStoppedSpeaking``
    yet — playback for this turn has not settled."""
    return any(s.stopped_at is None for s in tm.playback_spans)


def _preview(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= _PREVIEW_LEN else t[: _PREVIEW_LEN - 1] + "…"


# --------------------------------------------------------------------------- #
# Human-readable per-turn report
# --------------------------------------------------------------------------- #


def render_turn_report(tm: TurnMetrics) -> str:
    L: list[str] = []
    L.append("=" * 50)
    L.append(f"M2.4B.1 TURN REPORT  (turn #{tm.turn_index})")
    L.append("=" * 50)

    L.append("\nUSER")
    L.append(f"  stt text:      {tm.stt_text_preview or '-'}  ({tm.stt_text_len} chars)")
    L.append(f"  stt latency:   {_fmt(tm.stt_wall_latency_s, 's')}")

    L.append("\nLLM")
    L.append(f"  first token:         {_fmt(tm.first_token_latency_s, 's')}"
             f"  (base: {tm.first_token_latency_base or 'n/a'})")
    L.append(f"  generation duration: {_fmt(tm.assistant_generation_duration_s, 's')}")
    _chars = tm.assistant_text_chars if tm.assistant_text_chars is not None else "-"
    L.append(f"  chars:               {_chars}")
    L.append(f"  chars/s:             {_fmt(tm.llm_chars_per_s, '', 1)}")
    L.append("  prompt eval:         n/a  (Ollama done-chunk metadata not exposed — R0013)")
    L.append("  generation tok/s:    n/a  (same)")
    L.append("  load duration:       n/a  (same)")
    if tm.error:
        L.append(f"  ERROR:               {tm.error}")

    L.append("\nTEXT -> TTS  (post speech-planner — M2.4B.2)")
    L.append(f"  text chunks (TTSTextFrame): {len(tm.text_chunks)}  "
             f"mean chars {_fmt(tm.mean_text_chunk_chars, '', 1)}  "
             f"tiny (<{TINY_TEXT_CHUNK_CHARS}): {tm.tiny_text_chunk_count}")
    for c in tm.text_chunks:
        L.append(f"    #{c.index}  chars={c.text_len:<4} "
                 f"since_prev={_fmt(c.since_prev_s, 's')}  "
                 f"{'(after gen complete)' if c.after_assistant_complete else ''}")
    if tm.controller_releases:
        L.append(f"  CONTINUITY CONTROLLER (M2.4B.3.2): {len(tm.controller_releases)} releases  "
                 f"held {tm.controller_held_count}  "
                 f"max hold {_fmt(tm.controller_max_hold_s, 's')}  "
                 f"mean hold {_fmt(tm.controller_mean_hold_s, 's')}")
        for c in tm.controller_releases:
            L.append(f"    #{getattr(c, 'phrase_index', '?')}  "
                     f"reason={getattr(c, 'reason', '?')}  "
                     f"hold={_fmt(getattr(c, 'hold_s', None), 's')}  "
                     f"reserve@recv={_fmt(getattr(c, 'reserve_at_receive_s', None), 's')}  "
                     f"reserve@rel={_fmt(getattr(c, 'reserve_at_release_s', None), 's')}  "
                     f"(reserve = ESTIMATE)")
    L.append(f"  TRUE Piper HTTP synthesis (one request per sentence): "
             f"{len(tm.http_calls)} requests")
    for i, c in enumerate(tm.http_calls):
        L.append(f"    #{i}  http_wall={_fmt(getattr(c, 'http_wall_s', None), 's')}  "
                 f"ttfb={_fmt(getattr(c, 'ttfb_s', None), 's')}  "
                 f"audio={_fmt(getattr(c, 'audio_s', None), 's')}  "
                 f"RTF={_fmt(getattr(c, 'http_rtf', None), '', 3)}")
    L.append(f"  mean TRUE synthesis RTF: {_fmt(tm.mean_http_rtf, '', 3)}  "
             f"(mean http wall {_fmt(tm.mean_http_synthesis_s, 's')})")
    L.append(f"  tts context spans (TTSStarted->Stopped): {len(tm.tts_segments)}  "
             f"-- context-span 'RTF' mean {_fmt(tm.mean_context_span_rtf, '', 3)} "
             f"(NOT synthesis speed; inflated by playback drain + 3s timeout)")
    for s in tm.tts_segments:
        L.append(f"    #{s.index}  context_span={_fmt(s.context_span_s, 's')}  "
                 f"first_audio={_fmt(s.time_to_first_audio_s, 's')}  "
                 f"audio={_fmt(s.audio_duration_s, 's')}")

    L.append("\nPLAYBACK")
    L.append(f"  first audio:      {_fmt(_rel(tm, tm.timing.tts_first_audio), 's')}")
    L.append(f"  final stop:       {_fmt(_rel(tm, tm.final_playback_stopped_at), 's')}")
    L.append(f"  BotStarted count: {tm.bot_started_count}")
    L.append(f"  BotStopped count: {tm.bot_stopped_count}")
    L.append(f"  silence gaps ms:  {[round(g, 1) for g in tm.silence_gaps_ms] or '-'}")
    L.append(f"  max gap:          {_fmt(tm.max_gap_ms, 'ms', 1)}")
    L.append(f"  mean gap:         {_fmt(tm.mean_gap_ms, 'ms', 1)}")
    L.append(f"  underruns (est):  {tm.output_underrun_count}")
    L.append(f"  audio-s per wall-s: {_fmt(tm.audio_seconds_per_wall_second, '', 2)}  "
             f"(~1.0 = kept up; <1.0 = ran dry)")

    L.append("\nBUFFER ESTIMATE")
    L.append(f"  {BUFFER_ESTIMATE_NOTE}")
    L.append(f"  max:              {_fmt(tm.buffer.max_value, 's')}")
    L.append(f"  min:              {_fmt(tm.buffer.min_value, 's')}")
    chunk_samples = [round(v, 2) for at, v, lab in tm.buffer.samples if lab == "first_audio"]
    L.append(f"  at chunk arrivals:{chunk_samples or '-'}")
    L.append(f"  underrun estimate events: {len(tm.buffer.underrun_events)}  "
             f"(without a following BotStopped: {tm.underruns_without_following_stop})")
    L.append(f"  buffer-drain -> BotStopped lag: {_fmt(tm.buffer_drain_to_stop_lag_s, 's')}  "
             f"(healthy ~= 3 s transport fallback; large => estimate & reality disagree)")

    if tm.resources is not None:
        r = tm.resources
        L.append("\nRESOURCES")
        L.append(f"  CPU total:        mean {_fmt(r.cpu_total_pct_mean, '%', 1)}  "
                 f"peak {_fmt(r.cpu_total_pct_peak, '%', 1)}")
        L.append(f"  llama-server CPU: mean {_fmt(r.llama_server_cpu_pct_mean, '%', 1)}  "
                 f"peak {_fmt(r.llama_server_cpu_pct_peak, '%', 1)}")
        L.append(f"  temperature:      {_fmt(r.temp_c_max, 'C', 1)} max")
        L.append(f"  throttled:        {r.throttled_any_hex or '-'}")
        L.append(f"  MemAvailable min: {_fmt(r.mem_available_mb_min, 'MB', 0)}")
        L.append(f"  swap used max:    {_fmt(r.swap_used_mb_max, 'MB', 0)}")
        L.append(f"  STT overlap:      {r.stt_overlap}")
        L.append(f"  TTS overlap:      {r.tts_overlap}")
        L.append(f"  sampler overhead: {_fmt(r.sampler_overhead_ms_mean, 'ms', 3)}/sample "
                 f"({r.sample_count} samples)")
    else:
        L.append("\nRESOURCES\n  (not sampled — --report resource sampler inactive)")

    L.append("\nDIAGNOSIS")
    L.append(f"  Dominant wait for this turn: {diagnose_dominant_wait(tm)}")

    L.append("=" * 50)
    return "\n".join(L)


def _rel(tm: TurnMetrics, t: float | None) -> float | None:
    base = tm.timing.end_of_turn or tm.timing.stt_result or tm.started_wall
    if t is None or base is None:
        return None
    return t - base


def _fmt(x: float | None, unit: str = "", n: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:.{n}f}{unit}"


# --------------------------------------------------------------------------- #
# Report-mode-only OS resource sampler (threading + /proc + vcgencmd)
# --------------------------------------------------------------------------- #


@dataclass
class ActivityFlags:
    """Shared flags the probe/collector update so a resource sample can
    record what was running concurrently. Plain bools — GIL-atomic."""

    stt_active: bool = False
    tts_active: bool = False
    generation_active: bool = False


@dataclass
class _RawProcSnapshot:
    at: float
    cpu_total_jiffies: int | None
    cpu_idle_jiffies: int | None
    llama_proc_jiffies: int | None
    mem_available_kb: int | None
    swap_total_kb: int | None
    swap_free_kb: int | None
    temp_c: float | None
    throttled_hex: str | None
    read_wall_s: float


@dataclass
class ResourceSample:
    at: float
    cpu_total_pct: float | None
    llama_server_cpu_pct: float | None
    mem_available_mb: float | None
    swap_used_mb: float | None
    temp_c: float | None
    throttled_hex: str | None
    stt_active: bool
    tts_active: bool
    generation_active: bool
    read_overhead_ms: float


def find_llama_server_pid(_run: Callable[[list[str]], str] | None = None) -> int | None:
    """Best-effort: the PID of Ollama's ``llama-server`` inference backend.
    Returns ``None`` if not found (e.g. a different serving setup)."""
    runner = _run or _run_cmd
    try:
        out = runner(["pgrep", "-f", "llama-server"]).strip()
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def _run_cmd(cmd: list[str], timeout: float = 1.0) -> str:
    return subprocess.run(  # noqa: S603 - fixed arg lists, no shell
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    ).stdout


def read_raw_proc_snapshot(
    llama_pid: int | None,
    *,
    read_throttled: bool = True,
    _read: Callable[[str], str] | None = None,
    _run: Callable[[list[str]], str] | None = None,
) -> _RawProcSnapshot:
    """Read one raw snapshot from ``/proc`` + sysfs (+ ``vcgencmd
    get_throttled`` when ``read_throttled``). Injectable readers make it
    unit-testable without real hardware. Never raises."""
    read = _read or _read_file
    run = _run or _run_cmd
    t0 = time.monotonic()
    cpu_total = cpu_idle = None
    llama_j = None
    mem_avail = swap_total = swap_free = None
    temp_c = None
    throttled = None

    try:
        parts = read("/proc/stat").splitlines()[0].split()
        if parts and parts[0] == "cpu":
            nums = [int(x) for x in parts[1:]]
            cpu_total = sum(nums)
            cpu_idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
    except Exception:
        pass

    if llama_pid is not None:
        try:
            fields = read(f"/proc/{llama_pid}/stat").split()
            llama_j = int(fields[13]) + int(fields[14])  # utime + stime (clock ticks)
        except Exception:
            llama_j = None

    try:
        for ln in read("/proc/meminfo").splitlines():
            if ln.startswith("MemAvailable:"):
                mem_avail = int(ln.split()[1])
            elif ln.startswith("SwapTotal:"):
                swap_total = int(ln.split()[1])
            elif ln.startswith("SwapFree:"):
                swap_free = int(ln.split()[1])
    except Exception:
        pass

    # Temperature: sysfs milli-degC — a plain file read, no subprocess spawn
    # (the ``vcgencmd measure_temp`` fork/exec was ~5 ms/sample on this Pi).
    try:
        temp_c = int(read("/sys/class/thermal/thermal_zone0/temp").strip()) / 1000.0
    except Exception:
        temp_c = None

    # Throttle state has no sysfs equivalent; ``vcgencmd get_throttled`` is a
    # subprocess, so the sampler only asks for it every few seconds (it
    # changes slowly). ``read_throttled=False`` skips it entirely this tick.
    if read_throttled:
        try:
            raw = run(["vcgencmd", "get_throttled"]).strip()
            if "=" in raw:
                throttled = raw.split("=", 1)[1].strip()
        except Exception:
            pass

    return _RawProcSnapshot(
        at=time.monotonic(),
        cpu_total_jiffies=cpu_total,
        cpu_idle_jiffies=cpu_idle,
        llama_proc_jiffies=llama_j,
        mem_available_kb=mem_avail,
        swap_total_kb=swap_total,
        swap_free_kb=swap_free,
        temp_c=temp_c,
        throttled_hex=throttled,
        read_wall_s=time.monotonic() - t0,
    )


def _sample_from_deltas(
    prev: _RawProcSnapshot | None,
    cur: _RawProcSnapshot,
    activity: ActivityFlags,
    clk_tck: int,
) -> ResourceSample:
    cpu_pct = llama_pct = None
    have_cpu = (
        prev is not None
        and cur.cpu_total_jiffies is not None
        and prev.cpu_total_jiffies is not None
    )
    if have_cpu:
        dt_total = cur.cpu_total_jiffies - prev.cpu_total_jiffies
        dt_idle = (cur.cpu_idle_jiffies or 0) - (prev.cpu_idle_jiffies or 0)
        if dt_total > 0:
            cpu_pct = max(0.0, min(100.0, 100.0 * (dt_total - dt_idle) / dt_total))
        if (
            cur.llama_proc_jiffies is not None
            and prev.llama_proc_jiffies is not None
            and (cur.at - prev.at) > 0
        ):
            proc_secs = (cur.llama_proc_jiffies - prev.llama_proc_jiffies) / clk_tck
            llama_pct = max(0.0, 100.0 * proc_secs / (cur.at - prev.at))

    mem_mb = cur.mem_available_kb / 1024.0 if cur.mem_available_kb is not None else None
    swap_used_mb = None
    if cur.swap_total_kb is not None and cur.swap_free_kb is not None:
        swap_used_mb = (cur.swap_total_kb - cur.swap_free_kb) / 1024.0

    return ResourceSample(
        at=cur.at,
        cpu_total_pct=cpu_pct,
        llama_server_cpu_pct=llama_pct,
        mem_available_mb=mem_mb,
        swap_used_mb=swap_used_mb,
        temp_c=cur.temp_c,
        throttled_hex=cur.throttled_hex,
        stt_active=activity.stt_active,
        tts_active=activity.tts_active,
        generation_active=activity.generation_active,
        read_overhead_ms=cur.read_wall_s * 1000.0,
    )


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class ResourceSampler:
    """A low-frequency daemon-thread OS sampler. **Report mode only.** Keeps
    all samples in memory; the probe asks for a per-turn window summary at
    turn finalisation. Never touches the pipeline or the model."""

    def __init__(self, *, period_s: float = 0.4, activity: ActivityFlags | None = None) -> None:
        self.period_s = period_s
        self.activity = activity or ActivityFlags()
        self._samples: list[ResourceSample] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._llama_pid: int | None = None
        self._clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def start(self) -> None:
        if self._thread is not None:
            return
        self._llama_pid = find_llama_server_pid()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nexa-m24b1-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        prev: _RawProcSnapshot | None = None
        last_throttled: str | None = None
        # Ask vcgencmd get_throttled (the one subprocess) at most every ~2 s.
        throttle_every = max(1, round(2.0 / max(0.05, self.period_s)))
        tick = 0
        missed_llama = 0
        while not self._stop.is_set():
            want_throttled = tick % throttle_every == 0
            cur = read_raw_proc_snapshot(self._llama_pid, read_throttled=want_throttled)
            # Ollama respawns its llama-server backend on a model reload — the
            # PID changes. If we lose it for a few samples, look it up again.
            if self._llama_pid is not None and cur.llama_proc_jiffies is None:
                missed_llama += 1
                if missed_llama >= 3:
                    self._llama_pid = find_llama_server_pid()
                    missed_llama = 0
            else:
                missed_llama = 0
            if cur.throttled_hex is None:
                cur.throttled_hex = last_throttled  # carry forward between reads
            else:
                last_throttled = cur.throttled_hex
            sample = _sample_from_deltas(prev, cur, self.activity, self._clk_tck)
            with self._lock:
                self._samples.append(sample)
            prev = cur
            tick += 1
            self._stop.wait(self.period_s)

    def window_summary(self, start: float, end: float) -> ResourceWindowSummary:
        with self._lock:
            window = [s for s in self._samples if start <= s.at <= end]
        return summarize_resource_window(window)


def summarize_resource_window(samples: list[ResourceSample]) -> ResourceWindowSummary:
    """Pure — turn a list of resource samples into a per-turn summary."""
    if not samples:
        return ResourceWindowSummary(sample_count=0)

    def _vals(attr: str) -> list[float]:
        return [getattr(s, attr) for s in samples if getattr(s, attr) is not None]

    cpu = _vals("cpu_total_pct")
    llama = _vals("llama_server_cpu_pct")
    temps = _vals("temp_c")
    mems = _vals("mem_available_mb")
    swaps = _vals("swap_used_mb")
    overs = _vals("read_overhead_ms")

    throttled_hexes = {s.throttled_hex for s in samples if s.throttled_hex}
    throttled_any = None
    if throttled_hexes:
        # OR all observed masks; if all 0x0, that's the reported value.
        try:
            merged = 0
            for h in throttled_hexes:
                merged |= int(h, 16)
            throttled_any = hex(merged)
        except ValueError:
            throttled_any = sorted(throttled_hexes)[-1]

    return ResourceWindowSummary(
        sample_count=len(samples),
        cpu_total_pct_mean=(sum(cpu) / len(cpu)) if cpu else None,
        cpu_total_pct_peak=max(cpu) if cpu else None,
        llama_server_cpu_pct_mean=(sum(llama) / len(llama)) if llama else None,
        llama_server_cpu_pct_peak=max(llama) if llama else None,
        temp_c_max=max(temps) if temps else None,
        throttled_any_hex=throttled_any,
        mem_available_mb_min=min(mems) if mems else None,
        swap_used_mb_max=max(swaps) if swaps else None,
        stt_overlap=any(s.stt_active for s in samples),
        tts_overlap=any(s.tts_active for s in samples),
        generation_overlap=any(s.generation_active for s in samples),
        sampler_overhead_ms_mean=(sum(overs) / len(overs)) if overs else None,
    )


# --------------------------------------------------------------------------- #
# JSONL writer — one record per turn, written at turn completion only
# --------------------------------------------------------------------------- #


class TurnReportJsonlWriter:
    """Append one JSON object per finalised turn. Opens the file once,
    writes + flushes per turn (one small line), closes at the end. No
    per-audio-frame I/O. Does not store raw audio or (by default) full text."""

    def __init__(self, path: str, *, include_text: bool = False) -> None:
        self._path = path
        self._include_text = include_text
        self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 - closed in close()

    def write(self, tm: TurnMetrics) -> None:
        rec = tm.to_dict(include_text=self._include_text)
        rec["_wall_epoch"] = time.time()
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


__all__ = [
    "BUFFER_ESTIMATE_NOTE",
    "audio_bytes_to_seconds",
    "TtsTextChunk",
    "TtsSegment",
    "PlaybackSpan",
    "BufferEstimate",
    "ResourceWindowSummary",
    "TurnMetrics",
    "diagnose_dominant_wait",
    "DOMINANT_WAIT_CATEGORIES",
    "MetricsCollector",
    "render_turn_report",
    "ActivityFlags",
    "ResourceSample",
    "ResourceSampler",
    "summarize_resource_window",
    "read_raw_proc_snapshot",
    "find_llama_server_pid",
    "TurnReportJsonlWriter",
]
