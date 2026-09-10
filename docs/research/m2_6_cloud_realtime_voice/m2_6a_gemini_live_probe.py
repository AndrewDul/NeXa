#!/usr/bin/env python3
# ruff: noqa: E501  (research spike — long strings, prints, wide metric lines)
"""R0031 / M2.6A — minimal Gemini Live real-hardware feasibility probe.

FEASIBILITY SPIKE. Proves ONLY:

    reSpeaker mic
      -> XVF3800 echo-controlled mic path (unchanged)
      -> Pipecat LocalAudioTransport.input (16 kHz)
      -> local Silero VAD (turn authority; Gemini server VAD OFF)
      -> Gemini Live  (gemini-3.1-flash-live-preview, AUDIO modality)
      -> native streamed 24 kHz cloud audio
      -> AecReferenceFeeder tee -> plug:respeaker (XVF3800 far-end reference)
      -> LocalAudioTransport.output (24 kHz)
      -> speaker

plus a one-clock event timeline, Pipecat-#5465 NOT_READY audio accounting,
AEC-reference health, and token/cost telemetry. Results -> timestamped JSON
under this directory.

NO production CloudVoiceProvider / ConversationRouter / SetConversationPolicy
/ memory / identity / tools / GUI. NO change to src/nexa/**. This probe
imports NeXa audio/AEC helpers READ-ONLY.

Credential: env ``NEXA_GEMINI_API_KEY`` (or the operator-local file
``~/.config/nexa/secrets/gemini.env`` — outside the repo). The value is
never printed, logged, or written to the JSON.

Usage:
    # config-only, no hardware, no cloud:
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py --dry

    # full real-hardware session (OPERATOR):
    set -a; . ~/.config/nexa/secrets/gemini.env; set +a
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py

Ctrl+C ends the session and writes the JSON + prints the summary.
A hard 15-minute wall-clock cap ends it automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# The Gemini API key is read from the env var NEXA_GEMINI_API_KEY (or the
# operator-local file below). Its VALUE is never printed / logged / written.
SECRETS_FILE = Path.home() / ".config" / "nexa" / "secrets" / "gemini.env"
OUT_DIR = Path(__file__).resolve().parent
MODEL = "gemini-3.1-flash-live-preview"
HARD_SESSION_CAP_S = 15 * 60

# Voice is a NeXa USER PREFERENCE, not part of the Gemini model/identity.
# Operator (R0031 retest) requested a female / cozy / pleasant / warm voice.
# Sulafat's official descriptor is "Warm" (ai.google.dev speech-generation
# voice table, 2026-09-10); Google/Firebase voice metadata identifies it as
# female (operator-supplied basis — the style table does not list gender).
DEFAULT_VOICE = "Sulafat"
# Recorded alternatives — NOT auto-tested (no multi-voice generation, no
# voice-comparison benchmark, no quota waste). Descriptors from the same
# official table.
VOICE_ALTERNATIVES = {
    "Vindemiatrix": "Gentle",
    "Achernar": "Soft",
    "Aoede": "Breezy",
}

SPIKE_SYSTEM_INSTRUCTION = (
    "You are the temporary realtime conversation engine for an assistant "
    "called NeXa. Speak naturally and concisely. Normally answer in the "
    "language the user is currently speaking. If explicitly asked to use "
    "Polish or English, follow that request. You do not hold long-term "
    "memory or the user's identity; the host system does."
)


def _load_key() -> str:
    key = os.environ.get("NEXA_GEMINI_API_KEY", "").strip()
    if not key and SECRETS_FILE.is_file():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("NEXA_GEMINI_API_KEY=") and not s.startswith("#"):
                key = s.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return key


# ---- one monotonic clock + timeline -------------------------------------- #
def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 4)


class Timeline:
    """Append-only (event, monotonic_seconds) log on one clock."""

    #: an InterruptionFrame at the downstream tap. It can be EITHER the local
    #: aggregator's broadcast_interruption() on user-turn-start OR Gemini's
    #: own serverContent.interrupted round-trip — the probe cannot tell them
    #: apart, so the metric that uses it says so.
    _INTERRUPTION_MARKS = ("INTERRUPTION_DOWNSTREAM", "SERVER_INTERRUPTED")
    _BOT_STOP_MARKS = ("LOCAL_PLAYBACK_STOPPED", "BOT_AUDIO_STOPPED", "TURN_COMPLETE")

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.events: list[tuple[float, str, dict]] = []
        self.counters: dict[str, float] = {}

    @classmethod
    def from_events(cls, events: list[dict]) -> Timeline:
        """Rebuild a Timeline from a results-JSON ``timeline`` list (for
        ``--recompute``). No clock is started."""
        tl = cls()
        tl.events = [
            (float(e["t"]), str(e["event"]), {k: v for k, v in e.items() if k not in ("t", "event")})
            for e in events
        ]
        return tl

    def mark(self, event: str, **extra: object) -> float:
        t = time.monotonic() - self.t0
        self.events.append((round(t, 4), event, dict(extra)))
        return t

    def add(self, counter: str, n: float = 1) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + n

    # ---- helpers -------------------------------------------------------- #
    def _ts(self, *names: str) -> list[float]:
        want = set(names)
        return sorted(t for (t, e, _x) in self.events if e in want)

    def _pairs(self, a: str, b: str) -> list[float]:
        """For each `a` mark, the delta to the next `b` mark at/after it."""
        out: list[float] = []
        b_ts = self._ts(b)
        for ta in self._ts(a):
            nxt = [tb for tb in b_ts if tb >= ta]
            if nxt:
                out.append(round(nxt[0] - ta, 4))
        return out

    def _next(self, ts: list[float], after: float) -> float | None:
        nxt = [t for t in ts if t >= after - 1e-6]
        return nxt[0] if nxt else None

    def _last_within(self, ts: list[float], start: float, window: float) -> float | None:
        hits = [t for t in ts if start - 1e-6 <= t <= start + window]
        return hits[-1] if hits else None

    # ---- barge-in (CORRECTED): only a VAD_START while the bot is speaking - #
    def barge_in_analysis(self) -> dict:
        """Track bot-playing state across the timeline; a ``LOCAL_VAD_START``
        is a real barge-in ONLY while the bot is actually playing audio.

        ``FIRST_AUDIO_PLAYED`` -> bot_is_speaking=True
        ``LOCAL_PLAYBACK_STOPPED`` / ``BOT_AUDIO_STOPPED`` / ``TURN_COMPLETE``
            -> bot_is_speaking=False
        """
        stop_ts = self._ts(*self._BOT_STOP_MARKS)
        botstop_ts = self._ts("BOT_AUDIO_STOPPED")
        intr_ts = self._ts(*self._INTERRUPTION_MARKS)
        vad_ts = self._ts("LOCAL_VAD_START")

        # build (t, is_speaking) transitions in time order
        trans = sorted(
            [(t, True) for t in self._ts("FIRST_AUDIO_PLAYED")]
            + [(t, False) for t in stop_ts],
            key=lambda x: x[0],
        )

        def speaking_at(t: float) -> bool:
            s = False
            for tt, val in trans:
                if tt <= t + 1e-6:
                    s = val
                else:
                    break
            return s

        candidates: list[dict] = []
        for tv in vad_ts:
            if not speaking_at(tv):
                continue
            pstop = self._next(stop_ts, tv)
            bstop = self._next(botstop_ts, tv)
            ack = self._last_within(intr_ts, tv, 0.5)
            candidates.append({
                "vad_start_t": round(tv, 4),
                "playback_stop_t": round(pstop, 4) if pstop is not None else None,
                "bot_audio_stopped_t": round(bstop, 4) if bstop is not None else None,
                "last_interruption_frame_t": round(ack, 4) if ack is not None else None,
                "vad_start_to_playback_stop_s": round(pstop - tv, 4) if pstop is not None else None,
                "vad_start_to_bot_audio_stopped_s": round(bstop - tv, 4) if bstop is not None else None,
                # negative => local speaker stopped BEFORE this interruption
                # frame (which, being the later one in the window, is most
                # likely Gemini's serverContent.interrupted round-trip).
                "playback_stop_minus_interruption_frame_s": (
                    round(pstop - ack, 4) if (pstop is not None and ack is not None) else None
                ),
            })

        def col(key: str) -> list[float]:
            return [c[key] for c in candidates if c.get(key) is not None]

        return {
            "barge_in_candidate_count": len(candidates),
            "barge_in_candidates": candidates,
            "bargein_vad_start_to_playback_stop_s": col("vad_start_to_playback_stop_s"),
            "bargein_vad_start_to_playback_stop_median_s": _median(col("vad_start_to_playback_stop_s")),
            "bargein_vad_start_to_bot_audio_stopped_s": col("vad_start_to_bot_audio_stopped_s"),
            "bargein_vad_start_to_bot_audio_stopped_median_s": _median(col("vad_start_to_bot_audio_stopped_s")),
            "local_playback_stop_minus_interruption_frame_s": col("playback_stop_minus_interruption_frame_s"),
            "local_playback_stop_minus_interruption_frame_median_s": _median(
                col("playback_stop_minus_interruption_frame_s")
            ),
            "_note": (
                "A LOCAL_VAD_START while the bot was NOT playing is an ordinary "
                "user turn, not a barge-in, and is excluded. "
                "local_playback_stop_minus_interruption_frame_s is negative when "
                "NeXa's local playback authority silenced the speaker BEFORE the "
                "(later, likely server-round-trip) InterruptionFrame."
            ),
        }

    # ---- turn-local reconstruction (fixes cross-turn / fragment pairing) - #
    def turns(self) -> list[dict]:
        """Reconstruct user turns from the timeline.

        A turn opens on ``LOCAL_VAD_START``. Consecutive VAD-starts that occur
        BEFORE this turn's first ``FIRST_AUDIO_RECEIVED`` are FRAGMENTS of the
        same utterance (the user paused, then resumed) — merged, and the turn
        is flagged ``fragmented``. Each turn is bounded by the next turn's
        first VAD-start (or the end of the timeline), so no event from one
        turn is ever paired with audio from another.
        """
        starts = self._ts("LOCAL_VAD_START")
        recv = self._ts("FIRST_AUDIO_RECEIVED")
        played = self._ts("FIRST_AUDIO_PLAYED")
        eots = self._ts("LOCAL_VAD_EOT")
        raws = self._ts("INPUT_TRANSCRIPTION_RAW_FIRST")
        pushes = self._ts("INPUT_TRANSCRIPTION_PUSHED")
        server1 = self._ts("FIRST_SERVER_CONTENT", "GEMINI_SERVER_CONTENT_FIRST")
        tcomplete = self._ts("TURN_COMPLETE")
        INF = float("inf")

        def first_after(ts: list[float], t: float) -> float | None:
            hits = [x for x in ts if x > t + 1e-6]
            return hits[0] if hits else None

        def in_win(ts: list[float], lo: float, hi: float) -> list[float]:
            return [x for x in ts if lo - 1e-6 <= x < hi - 1e-6]

        turns: list[dict] = []
        i = 0
        while i < len(starts):
            grp = [starts[i]]
            fr = first_after(recv, starts[i]) or INF
            j = i + 1
            while j < len(starts) and starts[j] < fr:
                grp.append(starts[j])
                fr = first_after(recv, starts[j]) or INF
                j += 1
            win_lo = grp[0]
            win_hi = starts[j] if j < len(starts) else INF
            frecv = min(in_win(recv, win_lo, win_hi), default=None)
            fplay = min(in_win(played, win_lo, win_hi), default=None)
            fserver = min(in_win(server1, win_lo, win_hi), default=None)
            win_eots = in_win(eots, win_lo, win_hi)
            # effective EOT = last EOT at/before first audio (the real end of
            # a fragmented utterance), else the last EOT in the window.
            cut = frecv if frecv is not None else win_hi
            eff_eot = max([e for e in win_eots if e <= cut + 1e-6], default=(win_eots[-1] if win_eots else None))
            win_raws = in_win(raws, win_lo, win_hi)
            raw_t = max([r for r in win_raws if frecv is None or r <= frecv + 0.10], default=(win_raws[-1] if win_raws else None))
            win_push = in_win(pushes, win_lo, win_hi)
            pcut = (fplay + 0.30) if fplay is not None else win_hi
            push_t = max([p for p in win_push if p <= pcut + 1e-6], default=None)
            tc = min(in_win(tcomplete, win_lo, win_hi), default=None)

            def d(a: float | None, b: float | None) -> float | None:
                return round(b - a, 4) if (a is not None and b is not None) else None

            turns.append({
                "index": len(turns) + 1,
                "vad_start_t": round(win_lo, 4),
                "n_vad_segments": len(grp),
                "fragmented": len(grp) > 1,
                "eff_eot_t": round(eff_eot, 4) if eff_eot is not None else None,
                "raw_transcript_t": round(raw_t, 4) if raw_t is not None else None,
                "pushed_transcript_t": round(push_t, 4) if push_t is not None else None,
                "first_server_content_t": round(fserver, 4) if fserver is not None else None,
                "first_audio_received_t": round(frecv, 4) if frecv is not None else None,
                "first_audio_played_t": round(fplay, 4) if fplay is not None else None,
                "turn_complete_t": round(tc, 4) if tc is not None else None,
                "answered": frecv is not None,
                "eot_to_raw_s": d(eff_eot, raw_t),
                "eot_to_pushed_s": d(eff_eot, push_t),
                "eot_to_first_audio_received_s": d(eff_eot, frecv),
                "eot_to_first_audio_played_s": d(eff_eot, fplay),
                "raw_to_first_audio_received_s": d(raw_t, frecv),
                "pushed_to_first_audio_received_s": d(push_t, frecv),
            })
            i = j
        return turns

    def c6_turnlocal(self) -> dict:
        """R0030 C6 — was the user transcription available BEFORE Gemini
        started returning response audio for the SAME turn? Turn-local; a
        fragmented (multi-VAD-segment) turn is EXCLUDED from the primary
        statistic and reported separately."""
        turns = self.turns()
        valid, excluded = [], []
        for t in turns:
            why = None
            if not t["answered"]:
                why = "no first-audio for this turn"
            elif t["fragmented"]:
                why = f"fragmented user speech ({t['n_vad_segments']} VAD segments) — merged utterance"
            elif t["raw_transcript_t"] is None:
                why = "no input-transcription mark (wrappers not installed for this run)"
            elif t["eff_eot_t"] is None:
                why = "no LOCAL_VAD_EOT"
            (valid if why is None else excluded).append(
                {**t, **({"excluded_reason": why} if why else {})}
            )

        def col(rows: list[dict], k: str) -> list[float]:
            return [r[k] for r in rows if r.get(k) is not None]

        raw_margins = col(valid, "raw_to_first_audio_received_s")
        push_margins = col(valid, "pushed_to_first_audio_received_s")
        return {
            "n_turns_total": len(turns),
            "n_valid": len(valid),
            "n_excluded": len(excluded),
            "n_valid_raw_before_first_audio": sum(1 for x in raw_margins if x > 0),
            "n_valid_pushed_before_first_audio": sum(1 for x in push_margins if x > 0),
            "raw_to_first_audio_margin_s": raw_margins,
            "raw_to_first_audio_margin_median_s": _median(raw_margins),
            "raw_to_first_audio_margin_min_s": min(raw_margins) if raw_margins else None,
            "raw_to_first_audio_margin_max_s": max(raw_margins) if raw_margins else None,
            "pushed_to_first_audio_margin_s": push_margins,
            "pushed_to_first_audio_margin_median_s": _median(push_margins),
            "pushed_to_first_audio_margin_min_s": min(push_margins) if push_margins else None,
            "pushed_to_first_audio_margin_max_s": max(push_margins) if push_margins else None,
            "valid_turns": valid,
            "excluded_turns": excluded,
            "_note": (
                "Positive margin => the transcript arrived at NeXa before the "
                "first response audio for the same turn. RAW = first raw "
                "input-transcription chunk; PUSHED = Pipecat's aggregated "
                "sentence (end-of-sentence OR 0.5 s timeout flush). "
                "Local availability in time is necessary but NOT sufficient to "
                "steer the SAME response — see R0031 C6 architectural conclusion."
            ),
        }

    def derive(self) -> dict:
        eot_played = self._pairs("LOCAL_VAD_EOT", "FIRST_AUDIO_PLAYED")
        eot_recv = self._pairs("LOCAL_VAD_EOT", "FIRST_AUDIO_RECEIVED")
        turns = self.turns()
        tl_played = [t["eot_to_first_audio_played_s"] for t in turns if t.get("eot_to_first_audio_played_s") is not None]
        tl_recv = [t["eot_to_first_audio_received_s"] for t in turns if t.get("eot_to_first_audio_received_s") is not None]
        d = {
            "eot_to_first_server_content_s": self._pairs("LOCAL_VAD_EOT", "FIRST_SERVER_CONTENT"),
            # raw _pairs (kept for continuity; can double-count a fragmented turn)
            "eot_to_first_audio_received_s": eot_recv,
            "eot_to_first_audio_received_median_s": _median(eot_recv),
            "eot_to_first_audio_played_s": eot_played,
            "eot_to_first_audio_played_median_s": _median(eot_played),
            # TURN-LOCAL latency (one value per reconstructed turn; a fragmented
            # utterance is measured from its FINAL VAD_EOT, not double-counted)
            "eot_to_first_audio_played_turnlocal_s": tl_played,
            "eot_to_first_audio_played_turnlocal_median_s": _median(tl_played),
            "eot_to_first_audio_received_turnlocal_s": tl_recv,
            "eot_to_first_audio_received_turnlocal_median_s": _median(tl_recv),
            # C6 raw _pairs (kept for continuity; cross-turn pairing possible)
            "eot_to_input_transcription_raw_first_s": self._pairs("LOCAL_VAD_EOT", "INPUT_TRANSCRIPTION_RAW_FIRST"),
            "eot_to_input_transcription_pushed_s": self._pairs("LOCAL_VAD_EOT", "INPUT_TRANSCRIPTION_PUSHED"),
            "input_transcription_pushed_to_first_audio_received_s": self._pairs(
                "INPUT_TRANSCRIPTION_PUSHED", "FIRST_AUDIO_RECEIVED"
            ),
            "input_transcription_pushed_to_first_audio_played_s": self._pairs(
                "INPUT_TRANSCRIPTION_PUSHED", "FIRST_AUDIO_PLAYED"
            ),
            # C6 TURN-LOCAL (the authoritative version)
            "c6": self.c6_turnlocal(),
            "reconnect_start_to_ready_s": self._pairs("RECONNECT_START", "RECONNECT_READY"),
            "barge_in": self.barge_in_analysis(),
            "turns": turns,
            "_removed_invalid_metrics": (
                "vad_start_to_local_playback_stopped_s and "
                "server_interrupted_to_playback_stopped_s (pre-2026-09-10): paired "
                "EVERY VAD_START with a later playback stop. Also: the raw "
                "_pairs-based eot_to_first_audio_* and "
                "input_transcription_pushed_to_first_audio_* can cross turn "
                "boundaries or double-count a fragmented utterance — use the "
                "*_turnlocal_* fields and the c6 block instead."
            ),
        }
        return d


# ---- pipecat imports (deferred so --help works without them) ------------- #
def _pipecat() -> dict:
    """Import the pipecat surface lazily and return it as a name->object map
    (so ``--help`` / ``--dry`` work even mid-refactor and the module has no
    import-time cloud dependency)."""
    import pipecat.frames.frames as F  # noqa: N811
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.google.gemini_live.llm import (
        ContextWindowCompressionParams,
        GeminiLiveLLMService,
        GeminiModalities,
        GeminiVADParams,
    )
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

    P = {
        "SileroVADAnalyzer": SileroVADAnalyzer,
        "VADParams": VADParams,
        "Pipeline": Pipeline,
        "PipelineParams": PipelineParams,
        "PipelineWorker": PipelineWorker,
        "LLMContext": LLMContext,
        "LLMContextAggregatorPair": LLMContextAggregatorPair,
        "LLMUserAggregatorParams": LLMUserAggregatorParams,
        "FrameProcessor": FrameProcessor,
        "ContextWindowCompressionParams": ContextWindowCompressionParams,
        "GeminiLiveLLMService": GeminiLiveLLMService,
        "GeminiModalities": GeminiModalities,
        "GeminiVADParams": GeminiVADParams,
        "LocalAudioTransport": LocalAudioTransport,
        "LocalAudioTransportParams": LocalAudioTransportParams,
        "LLMRunFrame": F.LLMRunFrame,
    }
    for _name in (
        "InputAudioRawFrame", "InterruptionFrame", "LLMFullResponseEndFrame",
        "LLMFullResponseStartFrame",
        "StartFrame", "TranscriptionFrame", "TTSAudioRawFrame", "TTSStartedFrame",
        "TTSStoppedFrame", "TTSTextFrame", "UserStartedSpeakingFrame",
        "UserStoppedSpeakingFrame", "BotStartedSpeakingFrame", "BotStoppedSpeakingFrame",
    ):
        P[_name] = getattr(F, _name)
    return P


def _build(P: dict, tl: Timeline, key: str, *, dry: bool, voice: str = DEFAULT_VOICE):
    """Construct the probe pipeline. Returns (worker, runner_cls, meta).

    ``dry=True`` builds the cloud-side objects (Settings, LLMContext,
    aggregator pair, GeminiLiveLLMService — validates the pipecat/Gemini API
    surface) but opens NO audio device and makes NO network connection.
    ``voice`` is a Gemini prebuilt voice name (see DEFAULT_VOICE / --voice).
    """
    from nexa.voice.aec import AecReferenceHealth
    from nexa.voice.config import LocalAudioConfig
    from nexa.voice.device import find_device_index
    from nexa.voice_tts.aec_reference import AEC_REFERENCE_PCM, AecReferenceFeeder

    cfg = LocalAudioConfig()
    IN_RATE, OUT_RATE = 16000, 24000  # Gemini native in / out
    INPUT_CHUNK_MS = 20  # best-practices baseline (20-40 ms)

    meta: dict = {
        "model": MODEL,
        "voice": voice,
        "in_sample_rate": IN_RATE,
        "out_sample_rate": OUT_RATE,
        "input_chunk_ms_target": INPUT_CHUNK_MS,
        "input_device_name": cfg.input_device_name,
        "output_device_name": cfg.output_device_name,
        "aec_reference_device": AEC_REFERENCE_PCM,
        "server_vad": "disabled (local Silero is turn authority)",
        "context_window_compression": "enabled",
        "transcription": "input + output (Pipecat enables both unconditionally)",
    }

    # -- cloud-side objects (built in both dry and live modes) --------------
    # Empty message list on purpose: the system instruction is supplied via the
    # service's `system_instruction=` / Settings only. Passing it ALSO as an
    # initial context message makes Pipecat warn and convert it to a spurious
    # "user" line. With an empty context + inference_on_context_initialization
    # =False, `_create_initial_response` early-returns and flips
    # `_ready_for_realtime_input` True without sending any seed at all.
    context = P["LLMContext"](messages=[])
    user_agg, asst_agg = P["LLMContextAggregatorPair"](
        context,
        user_params=P["LLMUserAggregatorParams"](
            vad_analyzer=P["SileroVADAnalyzer"](
                sample_rate=IN_RATE,
                params=P["VADParams"](stop_secs=0.5),  # > Pipecat's 0.2 s default; matches upstream example
            ),
        ),
        realtime_service_mode=True,
    )
    llm = P["GeminiLiveLLMService"](
        api_key=key or "DRY-NO-KEY",
        system_instruction=SPIKE_SYSTEM_INSTRUCTION,
        settings=P["GeminiLiveLLMService"].Settings(
            model=MODEL,
            modalities=P["GeminiModalities"].AUDIO,
            # Gemini prebuilt voice. Pipecat 1.8.1 exposes this as
            # `Settings(voice=...)` and, in `_connect()`, maps it to
            #   generation_config.speech_config.voice_config
            #       .prebuilt_voice_config.voice_name = <voice>
            # i.e. exactly Google's speech_config -> voice_config ->
            # prebuilt_voice_config -> voice_name. Voice is a NeXa USER
            # PREFERENCE, not part of the model / architecture.
            voice=voice,
            vad=P["GeminiVADParams"](disabled=True),
            context_window_compression=P["ContextWindowCompressionParams"](enabled=True),
            system_instruction=SPIKE_SYSTEM_INSTRUCTION,
        ),
        # M2.6A operator-attempt-1 FIX (R0031): with server VAD disabled, the
        # service only sends activity_start / user audio / activity_end once
        # `_ready_for_realtime_input` is True — and that flag only flips after
        # an `LLMContextFrame` reaches the service (via `_handle_context` ->
        # `_create_initial_response`). Nothing pushes that frame until an
        # `LLMRunFrame` is queued (the upstream example does it from the
        # transport's `on_client_connected`; `LocalAudioTransport` has no such
        # event). The probe now queues exactly one `LLMRunFrame` after the
        # socket connects (see `_kickoff` in `_run`).
        #   `inference_on_context_initialization=False` => the init seed is
        # sent with turn_complete=False, so the service becomes ready WITHOUT
        # generating an opening bot utterance. (A system-only context also
        # early-returns in `_create_initial_response` without inference, but
        # False makes the "no greeting" intent explicit.)
        inference_on_context_initialization=False,
        user_audio_preroll_secs=None,  # auto-size from Silero start_secs
    )
    meta["settings_ok"] = True
    meta["_context"] = context
    meta["_llm"] = llm

    if dry:
        meta["mode"] = "dry (cloud-side objects built; no hardware, no connect)"
        return None, None, meta

    # -- live: audio transport + AEC feed + metrics taps -------------------
    import pyaudio

    pa = pyaudio.PyAudio()
    in_idx = find_device_index(pa, cfg.input_device_name, require_input=True)
    out_idx = find_device_index(pa, cfg.output_device_name, require_output=True)
    meta["input_device_index"] = in_idx
    meta["output_device_index"] = out_idx

    transport = P["LocalAudioTransport"](
        P["LocalAudioTransportParams"](
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=IN_RATE,
            audio_out_sample_rate=OUT_RATE,
            audio_in_channels=1,
            audio_out_channels=1,
            input_device_index=in_idx,
            output_device_index=out_idx,
        )
    )

    aec_health = AecReferenceHealth(
        on_change=lambda active: tl.mark("AEC_REF_ACTIVE" if active else "AEC_REF_DOWN")
    )
    aec_feeder = AecReferenceFeeder(aec_health=aec_health, sample_rate=OUT_RATE, channels=1)

    _install_llm_diagnostics(llm, tl)  # SPIKE-ONLY (see the function docstring)

    up = _SpikeMetrics(P, "upstream", tl, llm)
    down = _SpikeMetrics(P, "downstream", tl, llm)

    pipeline = P["Pipeline"](
        [
            transport.input(),
            up,
            user_agg,
            llm,
            down,
            aec_feeder,
            transport.output(),
            asst_agg,
        ]
    )

    worker = P["PipelineWorker"](
        pipeline,
        params=P["PipelineParams"](
            audio_in_sample_rate=IN_RATE,
            audio_out_sample_rate=OUT_RATE,
            enable_usage_metrics=True,
        ),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    meta["mode"] = "live real-hardware"
    meta["_llm"] = llm
    meta["_aec_health"] = aec_health
    meta["_aec_feeder"] = aec_feeder
    return worker, meta.pop("_runner_placeholder", None), meta


def _install_llm_diagnostics(llm, tl: Timeline) -> None:
    """SPIKE-ONLY. Wrap a few *private* GeminiLiveLLMService methods on this
    one instance so the probe can record whether activity_start / activity_end
    / user audio were actually issued to Gemini (not just whether the frames
    reached the service). Pure observation — every wrapper calls the original
    and returns its result unchanged. Disposable research diagnostics; nothing
    here belongs in a production wrapper.
    """
    import functools

    orig_started = llm._handle_user_started_speaking
    orig_stopped = llm._handle_user_stopped_speaking
    orig_flush = llm._flush_user_audio_preroll
    orig_send_audio = llm._send_user_audio
    # C6 instrumentation: Gemini's input transcription is pushed UPSTREAM by
    # `_push_user_transcription` and CONSUMED by the user aggregator, so it
    # never reaches a metrics tap. Wrap the source instead.
    orig_msg_intr = getattr(llm, "_handle_msg_input_transcription", None)
    orig_push_utr = getattr(llm, "_push_user_transcription", None)
    _utr = {"raw_since_turn": False}

    @functools.wraps(orig_started)
    async def _started(frame):
        ready = bool(getattr(llm, "_ready_for_realtime_input", False))
        has_session = getattr(llm, "_session", None) is not None
        vad_off = bool(getattr(llm, "_vad_disabled", False))
        _utr["raw_since_turn"] = False  # arm C6 first-chunk mark for this turn
        tl.mark("USER_STARTED_FRAME_AT_GEMINI", ready=ready, session=has_session, vad_disabled=vad_off)
        if vad_off and has_session and ready:
            tl.mark("ACTIVITY_START_SENT")
        else:
            tl.mark("ACTIVITY_START_SKIPPED", reason=("not_ready" if not ready else "no_session" if not has_session else "vad_enabled"))
        return await orig_started(frame)

    @functools.wraps(orig_stopped)
    async def _stopped(frame):
        ready = bool(getattr(llm, "_ready_for_realtime_input", False))
        has_session = getattr(llm, "_session", None) is not None
        vad_off = bool(getattr(llm, "_vad_disabled", False))
        tl.mark("USER_STOPPED_FRAME_AT_GEMINI", ready=ready, session=has_session)
        if vad_off and has_session and ready:
            tl.mark("ACTIVITY_END_SENT")
        else:
            tl.mark("ACTIVITY_END_SKIPPED", reason=("not_ready" if not ready else "no_session" if not has_session else "vad_enabled"))
        return await orig_stopped(frame)

    @functools.wraps(orig_flush)
    async def _flush():
        buf = getattr(llm, "_user_audio_preroll_buffer", b"") or b""
        tl.mark("PREROLL_FLUSH_TO_GEMINI", bytes=len(buf))
        return await orig_flush()

    _sent = {"first": False}

    @functools.wraps(orig_send_audio)
    async def _send_audio(frame):
        ready = bool(getattr(llm, "_ready_for_realtime_input", False))
        speaking = bool(getattr(llm, "_user_is_speaking", False))
        will_send = ready and (not getattr(llm, "_vad_disabled", False) or speaking) \
            and not getattr(llm, "_audio_input_paused", False) \
            and not getattr(llm, "_disconnecting", False) \
            and getattr(llm, "_session", None) is not None
        if will_send:
            n = len(getattr(frame, "audio", b"") or b"")
            tl.add("audio_bytes_to_gemini", n)
            tl.add("audio_chunks_to_gemini", 1)
            if not _sent["first"]:
                _sent["first"] = True
                tl.mark("INPUT_AUDIO_TO_GEMINI_FIRST", bytes=n)
        return await orig_send_audio(frame)

    async def _msg_intr(message):
        # first raw input-transcription chunk of this user turn
        try:
            sc = getattr(message, "server_content", None)
            it = getattr(sc, "input_transcription", None)
            txt = (getattr(it, "text", "") or "") if it else ""
        except Exception:  # noqa: BLE001
            txt = ""
        if txt and not _utr["raw_since_turn"]:
            _utr["raw_since_turn"] = True
            tl.mark("INPUT_TRANSCRIPTION_RAW_FIRST", text_len=len(txt))
        return await orig_msg_intr(message)

    async def _push_utr(text, result=None):
        # the aggregated user sentence Pipecat forwards ("final" for this
        # segment — end-of-sentence or 0.5 s flush)
        tl.mark("INPUT_TRANSCRIPTION_PUSHED", text_len=len(text or ""))
        return await orig_push_utr(text, result)

    llm._handle_user_started_speaking = _started
    llm._handle_user_stopped_speaking = _stopped
    llm._flush_user_audio_preroll = _flush
    llm._send_user_audio = _send_audio
    if orig_msg_intr is not None:
        llm._handle_msg_input_transcription = _msg_intr
    if orig_push_utr is not None:
        llm._push_user_transcription = _push_utr


async def _state_poller(llm, tl: Timeline, stop: asyncio.Event) -> None:
    """SPIKE-ONLY. Poll the GeminiLiveLLMService's internal readiness flags at
    10 Hz and mark the first transition of each. No per-audio-frame logging."""
    seen: set[str] = set()

    def once(name: str, cond: bool) -> None:
        if cond and name not in seen:
            seen.add(name)
            tl.mark(name)

    while not stop.is_set():
        once("GEMINI_CONNECTED", getattr(llm, "_session", None) is not None)
        once("LLM_CONTEXT_INITIALIZED", getattr(llm, "_context", None) is not None)
        once("GEMINI_REALTIME_READY", bool(getattr(llm, "_ready_for_realtime_input", False)))
        if len(seen) == 3:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1)
        except TimeoutError:
            pass


async def _kickoff(worker, llm, tl: Timeline, P: dict, stop: asyncio.Event) -> None:
    """M2.6A FIX + diagnostic. Wait for the Gemini socket, then queue exactly
    ONE LLMRunFrame so the context aggregator pushes the initial LLMContextFrame
    into GeminiLiveLLMService (-> `_handle_context` -> `_create_initial_response`
    -> `_ready_for_realtime_input = True`). LocalAudioTransport has no
    `on_client_connected` event, so the probe drives this itself."""
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and not stop.is_set():
        if getattr(llm, "_session", None) is not None:
            break
        await asyncio.sleep(0.1)
    connected = getattr(llm, "_session", None) is not None
    tl.mark("KICKOFF_SOCKET_CONNECTED" if connected else "KICKOFF_SOCKET_TIMEOUT")
    # Small settle so StartFrame has propagated through the aggregator.
    await asyncio.sleep(0.3)
    await worker.queue_frames([P["LLMRunFrame"]()])
    tl.mark("LLM_RUN_FRAME_QUEUED")


def _make_metrics_cls(P: dict):
    FrameProcessor = P["FrameProcessor"]
    fr = P

    class _SpikeMetricsImpl(FrameProcessor):
        """Timestamps the M2.6A event timeline on one clock + #5465 accounting.

        Placed twice: ``upstream`` (before the LLM service — sees mic audio +
        the not-ready window) and ``downstream`` (after — sees cloud audio +
        transcription)."""

        def __init__(self, position: str, tl: Timeline, llm, **kw) -> None:
            super().__init__(**kw)
            self._pos = position
            self._tl = tl
            self._llm = llm
            self._last_ready: bool | None = None
            self._turn_audio_seen = False
            self._not_ready_since: float | None = None
            self._once_seen: set[str] = set()

        def _ready(self) -> bool:
            return bool(getattr(self._llm, "_ready_for_realtime_input", False))

        def _once(self, name: str, **extra) -> None:
            if name not in self._once_seen:
                self._once_seen.add(name)
                self._tl.mark(name, **extra)

        async def process_frame(self, frame, direction) -> None:  # noqa: ANN001
            await super().process_frame(frame, direction)
            tl = self._tl

            # first sign of ANY server content (downstream only): the earliest
            # of started / transcription / text / audio frames coming back.
            if self._pos == "downstream" and isinstance(
                frame,
                (
                    fr["TTSStartedFrame"], fr["LLMFullResponseStartFrame"],
                    fr["TranscriptionFrame"], fr["TTSTextFrame"], fr["TTSAudioRawFrame"],
                ),
            ):
                self._once("GEMINI_SERVER_CONTENT_FIRST", frame=type(frame).__name__)

            if self._pos == "upstream" and isinstance(frame, fr["InputAudioRawFrame"]):
                # #5465 accounting: is the LLM session ready to actually accept this?
                ready = self._ready()
                if self._last_ready is None or ready != self._last_ready:
                    tl.mark("LLM_READY" if ready else "LLM_NOT_READY")
                    if ready and self._not_ready_since is not None:
                        tl.add("not_ready_window_count", 1)
                        self._not_ready_since = None
                    elif not ready:
                        self._not_ready_since = time.monotonic()
                    self._last_ready = ready
                nbytes = len(frame.audio or b"")
                dur_ms = 1000.0 * (nbytes / 2) / max(1, getattr(frame, "sample_rate", 16000))
                tl.add("mic_audio_ms_presented", dur_ms)
                tl.add("mic_audio_bytes_presented", nbytes)
                tl.add("mic_chunks_presented", 1)
                if not ready:
                    tl.add("mic_audio_ms_while_not_ready", dur_ms)
                    tl.add("mic_chunks_while_not_ready", 1)
                if "mic_chunk_bytes_sample" not in tl.counters:
                    tl.counters["mic_chunk_bytes_sample"] = nbytes  # observed input chunk size

            if isinstance(frame, fr["UserStartedSpeakingFrame"]):
                self._turn_audio_seen = False
                tl.mark(f"LOCAL_VAD_START::{self._pos}")
                if self._pos == "upstream":
                    tl.mark("LOCAL_VAD_START")
            elif isinstance(frame, fr["UserStoppedSpeakingFrame"]):
                if self._pos == "upstream":
                    tl.mark("LOCAL_VAD_EOT")
            elif isinstance(frame, fr["TranscriptionFrame"]):
                # Gemini input transcription (aggregated by Pipecat)
                self._once("INPUT_TRANSCRIPTION_FIRST", text_len=len(getattr(frame, "text", "") or ""))
                tl.add("input_transcription_frames", 1)
                # A frame with a trailing sentence punctuation is treated as a flush.
                txt = (getattr(frame, "text", "") or "").strip()
                if txt.endswith((".", "!", "?", "…")):
                    tl.mark("INPUT_TRANSCRIPTION_FLUSHED", text_len=len(txt))
            elif isinstance(frame, fr["TTSTextFrame"]):
                self._once("OUTPUT_TRANSCRIPTION_FIRST", text_len=len(getattr(frame, "text", "") or ""))
                tl.add("output_transcription_frames", 1)
            elif isinstance(frame, fr["TTSAudioRawFrame"]):
                if self._pos == "downstream":
                    nbytes = len(frame.audio or b"")
                    tl.add("cloud_audio_bytes_received", nbytes)
                    tl.add("cloud_audio_chunks_received", 1)
                    dur_ms = 1000.0 * (nbytes / 2) / max(1, getattr(frame, "sample_rate", 24000))
                    tl.add("cloud_audio_ms_received", dur_ms)
                    if not self._turn_audio_seen:
                        self._turn_audio_seen = True
                        tl.mark("FIRST_AUDIO_RECEIVED")
            elif isinstance(frame, fr["TTSStartedFrame"]):
                self._once("FIRST_SERVER_CONTENT")
            elif isinstance(frame, fr["BotStartedSpeakingFrame"]):
                tl.mark("FIRST_AUDIO_PLAYED")
            elif isinstance(frame, fr["BotStoppedSpeakingFrame"]):
                tl.mark("BOT_AUDIO_STOPPED")
            elif isinstance(frame, fr["LLMFullResponseEndFrame"]):
                tl.mark("TURN_COMPLETE")
            elif isinstance(frame, fr["TTSStoppedFrame"]):
                tl.mark("LOCAL_PLAYBACK_STOPPED")
            elif isinstance(frame, fr["InterruptionFrame"]):
                tl.mark(f"INTERRUPTION::{self._pos}")
                if self._pos == "downstream":
                    # An InterruptionFrame past the LLM service. Could be the
                    # local aggregator's broadcast_interruption() on user-turn
                    # -start OR Gemini's serverContent.interrupted round-trip —
                    # indistinguishable here. Renamed from SERVER_INTERRUPTED.
                    tl.mark("INTERRUPTION_DOWNSTREAM", bot_responding=bool(getattr(self._llm, "_bot_is_responding", False)))
            elif isinstance(frame, fr["StartFrame"]):
                tl.mark(f"PIPELINE_START::{self._pos}")

            await self.push_frame(frame, direction)

    return _SpikeMetricsImpl


_SpikeMetrics_cls = None


def _SpikeMetrics(P, position, tl, llm):  # noqa: N802 - factory shim
    global _SpikeMetrics_cls
    if _SpikeMetrics_cls is None:
        _SpikeMetrics_cls = _make_metrics_cls(P)
    return _SpikeMetrics_cls(position, tl, llm)


def _write_results(tl: Timeline, meta: dict, note: str) -> Path:
    llm = meta.get("_llm")
    aec_health = meta.get("_aec_health")
    aec_feeder = meta.get("_aec_feeder")

    usage = {}
    if llm is not None:
        # best-effort: whatever the service exposes
        for attr in ("_session_resumption_handle",):
            if hasattr(llm, attr):
                usage["session_resumption_handle_present"] = bool(getattr(llm, attr))

    counters = dict(tl.counters)
    # cost estimate — OFFICIAL Gemini pricing (2026-09-10):
    #   input  audio $3.00 / 1M tokens (~$0.005/min);  audio tokens ~25 tok/s
    #   output audio $12.00 / 1M tokens (~$0.018/min)
    mic_ms = counters.get("mic_audio_ms_presented", 0.0)
    out_ms = counters.get("cloud_audio_ms_received", 0.0)
    in_audio_tokens_est = 25.0 * (mic_ms / 1000.0)
    out_audio_tokens_est = 25.0 * (out_ms / 1000.0)
    cost_est_usd = in_audio_tokens_est / 1e6 * 3.00 + out_audio_tokens_est / 1e6 * 12.00

    payload = {
        "report": "R0031 / M2.6A gemini-live hardware probe",
        "generated_utc": datetime.now(UTC).isoformat(),
        "note": note,
        "config": {k: v for k, v in meta.items() if not k.startswith("_")},
        "timeline": [{"t": t, "event": e, **x} for (t, e, x) in tl.events],
        "counters": counters,
        "derived_latencies_s": tl.derive(),
        "pipecat_5465": {
            "not_ready_windows": counters.get("not_ready_window_count", 0),
            "mic_audio_ms_while_not_ready": round(counters.get("mic_audio_ms_while_not_ready", 0.0), 1),
            "mic_chunks_while_not_ready": counters.get("mic_chunks_while_not_ready", 0),
            "note": "pipecat 1.8.1 bare-returns InputAudioRawFrame while _ready_for_realtime_input is False; any non-zero value here was NOT sent to Gemini",
        },
        "aec_reference": {
            "ever_started": bool(getattr(aec_health, "ever_started", False)) if aec_health else None,
            "active_at_end": bool(getattr(aec_health, "active", False)) if aec_health else None,
            "failure_count": getattr(aec_health, "failure_count", None) if aec_health else None,
            "frames_mirrored": getattr(aec_feeder, "frames_mirrored", None) if aec_feeder else None,
            "chunks_dropped": getattr(aec_feeder, "chunks_dropped", None) if aec_feeder else None,
        },
        "cost_estimate": {
            "mic_audio_seconds": round(mic_ms / 1000.0, 1),
            "cloud_audio_seconds": round(out_ms / 1000.0, 1),
            "input_audio_tokens_est": round(in_audio_tokens_est),
            "output_audio_tokens_est": round(out_audio_tokens_est),
            "usd_estimate": round(cost_est_usd, 4),
            "basis": "official 2026-09-10 pricing: in audio $3.00/1M (~25 tok/s), out audio $12.00/1M; usageMetadata not yet wired",
        },
        "session_notes": usage,
    }
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"m2_6a_probe_results_{ts}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _recompute(paths: list[str]) -> int:
    """Re-run derive() over the `timeline` in one or more existing results
    JSONs (repairs the barge-in metrics from history — no cloud call).
    Writes `<name>_recomputed_YYYY…json` next to each and prints a summary."""
    rc = 0
    for p in paths:
        src = Path(p)
        if not src.is_file():
            print("MISSING:", src)
            rc = 1
            continue
        if src.stem.endswith("_recomputed"):
            print("SKIP (already a recompute):", src.name)
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        if "timeline" not in data:
            print("SKIP (no `timeline`):", src.name)
            continue
        tl = Timeline.from_events(data.get("timeline", []))
        corrected = tl.derive()
        out = src.with_name(src.stem + "_recomputed.json")
        payload = {
            "recomputed_from": src.name,
            "recomputed_utc": datetime.now(UTC).isoformat(),
            "note": (
                "Recomputed with turn-local reconstruction: barge-in gates on "
                "bot_is_speaking; latency + C6 are measured per reconstructed "
                "turn (a fragmented multi-VAD-segment utterance is measured "
                "from its FINAL VAD_EOT and excluded from the C6 primary stat) "
                "so no event is paired across turn boundaries."
            ),
            "original_config": data.get("config"),
            "corrected_derived_latencies_s": corrected,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        bi = corrected["barge_in"]
        c6 = corrected["c6"]
        print(f"== {src.name} ==")
        print(f"   turns (reconstructed)        : {c6['n_turns_total']}  (valid for C6: {c6['n_valid']}, excluded: {c6['n_excluded']})")
        print(f"   EOT->first audible, turnlocal: {corrected['eot_to_first_audio_played_turnlocal_s']}  median={corrected['eot_to_first_audio_played_turnlocal_median_s']}")
        print(f"   EOT->first audible, raw pairs: {corrected['eot_to_first_audio_played_s']}  median={corrected['eot_to_first_audio_played_median_s']}  (may double-count a fragment)")
        print(f"   barge-in candidates          : {bi['barge_in_candidate_count']}")
        print(f"   vad_start->playback stop s    : {bi['bargein_vad_start_to_playback_stop_s']}  median={bi['bargein_vad_start_to_playback_stop_median_s']}")
        print(f"   playback stop - intr frame s  : {bi['local_playback_stop_minus_interruption_frame_s']}  median={bi['local_playback_stop_minus_interruption_frame_median_s']}")
        if c6["n_valid"]:
            print(f"   C6 RAW transcript -> first audio (valid turns): {c6['raw_to_first_audio_margin_s']}  median={c6['raw_to_first_audio_margin_median_s']}")
            print(f"   C6 PUSHED transcript -> first audio           : {c6['pushed_to_first_audio_margin_s']}  median={c6['pushed_to_first_audio_margin_median_s']}")
            print(f"   C6 RAW before first audio  : {c6['n_valid_raw_before_first_audio']}/{c6['n_valid']}   PUSHED before first audio: {c6['n_valid_pushed_before_first_audio']}/{c6['n_valid']}")
        else:
            print("   C6 : not instrumented in this run (no input-transcription marks)")
        print(f"   -> {out.name}")
    return rc


async def _run(args) -> int:
    tl = Timeline()
    key = _load_key()
    P = _pipecat()

    if not args.dry and not key:
        print("BLOCKED: NEXA_GEMINI_API_KEY not set and no key in", SECRETS_FILE)
        return 2

    worker, _runner_cls, meta = _build(P, tl, key, dry=args.dry, voice=args.voice)

    print("=" * 74)
    print("M2.6A — Gemini Live hardware probe")
    print("=" * 74)
    for k, v in meta.items():
        if not k.startswith("_"):
            print(f"  {k:28} {v}")
    print("-" * 74)

    if args.dry:
        print("DRY RUN: cloud-side objects constructed (Settings + LLMContext +")
        print("aggregator pair + GeminiLiveLLMService). No audio device opened,")
        print("no network connection, no results file written.")
        return 0

    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner()
    await runner.add_workers(worker)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def _cap():
        await asyncio.sleep(HARD_SESSION_CAP_S)
        print(f"\n[hard {HARD_SESSION_CAP_S // 60}-min session cap reached — stopping]")
        stop.set()

    llm = meta.get("_llm")
    cap_task = asyncio.create_task(_cap())
    run_task = asyncio.create_task(runner.run())
    tl.mark("SESSION_START")
    tl.mark("PIPELINE_READY")
    poll_task = asyncio.create_task(_state_poller(llm, tl, stop))
    kick_task = asyncio.create_task(_kickoff(worker, llm, tl, P, stop))
    print("Listening. Speak to NeXa (cloud). Ctrl+C to stop.")
    print("(kickoff LLMRunFrame is queued automatically once the socket connects)\n")

    await asyncio.wait({run_task, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
    cap_task.cancel()
    stop.set()
    for t in (poll_task, kick_task):
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    tl.mark("SESSION_STOP")

    with contextlib.suppress(Exception):
        await runner.stop_workers()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(run_task, timeout=10)

    path = _write_results(tl, meta, note=args.note or "operator live session")
    _print_summary(tl, meta)
    print(f"\nresults JSON: {path}")
    print("This probe does NOT judge audio quality — that is the operator's call.")
    return 0


def _print_summary(tl: Timeline, meta: dict) -> None:
    d = tl.derive()
    c = tl.counters
    print("\n" + "=" * 74)
    print("SESSION SUMMARY")
    print("=" * 74)
    c6 = d["c6"]
    print(f"  voice                                         {meta.get('voice', '?')}")
    print(f"  turns (reconstructed)                          {c6['n_turns_total']}  (C6 valid {c6['n_valid']}, excluded {c6['n_excluded']})")
    print(f"  EOT -> first audible, TURN-LOCAL  s            {d['eot_to_first_audio_played_turnlocal_s']}  median={d['eot_to_first_audio_played_turnlocal_median_s']}")
    print(f"  EOT -> first audible, raw pairs   s            {d['eot_to_first_audio_played_s']}  median={d['eot_to_first_audio_played_median_s']}  (may double-count a fragment)")
    bi = d["barge_in"]
    print(f"  barge-in candidates (VAD start while bot playing): {bi['barge_in_candidate_count']}")
    print(f"      vad_start -> playback stop   s             {bi['bargein_vad_start_to_playback_stop_s']}  median={bi['bargein_vad_start_to_playback_stop_median_s']}")
    print(f"      vad_start -> bot audio stop  s             {bi['bargein_vad_start_to_bot_audio_stopped_s']}  median={bi['bargein_vad_start_to_bot_audio_stopped_median_s']}")
    print(f"      playback stop - interruption frame  s      {bi['local_playback_stop_minus_interruption_frame_s']}  median={bi['local_playback_stop_minus_interruption_frame_median_s']}")
    print("      (negative => local speaker silenced BEFORE the interruption frame)")
    if c6["n_valid"]:
        print(f"  C6  RAW transcript -> first audio (valid turns) s  {c6['raw_to_first_audio_margin_s']}  median={c6['raw_to_first_audio_margin_median_s']}")
        print(f"  C6  PUSHED transcript -> first audio            s  {c6['pushed_to_first_audio_margin_s']}  median={c6['pushed_to_first_audio_margin_median_s']}")
        print(f"  C6  RAW before first audio {c6['n_valid_raw_before_first_audio']}/{c6['n_valid']} · PUSHED before first audio {c6['n_valid_pushed_before_first_audio']}/{c6['n_valid']}")
    else:
        print("  C6  not instrumented in this run (no input-transcription marks)")
    print(f"  mic audio presented (s)                       {c.get('mic_audio_ms_presented', 0) / 1000:.1f}")
    print(f"  mic audio while LLM NOT ready (s) [#5465]      {c.get('mic_audio_ms_while_not_ready', 0) / 1000:.2f}   windows={c.get('not_ready_window_count', 0)}")
    print(f"  cloud audio received (s)                      {c.get('cloud_audio_ms_received', 0) / 1000:.1f}")
    print(f"  observed mic input chunk (bytes)             {c.get('mic_chunk_bytes_sample', '?')}")
    ah = meta.get("_aec_health")
    if ah is not None:
        print(f"  AEC ref: ever_started={ah.ever_started} active_at_end={ah.active} failures={ah.failure_count}")


async def _lifecycle_smoke() -> int:
    """ONE minimal authenticated NO-MICROPHONE lifecycle check (R0031 fix
    proof). Builds GeminiLiveLLMService + LLMContext + aggregator pair on a
    tiny transport-less pipeline, queues one LLMRunFrame, and verifies
    `_ready_for_realtime_input` flips True. No mic, no speaker, no user turn,
    so the model generates nothing — near-zero tokens (just the connect +
    a turn_complete=False seed)."""
    tl = Timeline()
    key = _load_key()
    if not key:
        print("BLOCKED: no NEXA_GEMINI_API_KEY")
        return 2
    P = _pipecat()
    _worker, _rc, meta = _build(P, tl, key, dry=True)  # cloud-side objects only
    llm = meta["_llm"]
    # rebuild in live-ish mode manually (no _build live path -> no PyAudio)
    _install_llm_diagnostics(llm, tl)
    context = meta["_context"]
    user_agg, _asst = P["LLMContextAggregatorPair"](
        context, realtime_service_mode=True
    )
    pipeline = P["Pipeline"]([user_agg, llm])
    worker = P["PipelineWorker"](pipeline, enable_rtvi=False, idle_timeout_secs=None)

    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner()
    await runner.add_workers(worker)
    stop = asyncio.Event()
    run_task = asyncio.create_task(runner.run())
    poll_task = asyncio.create_task(_state_poller(llm, tl, stop))
    await _kickoff(worker, llm, tl, P, stop)

    ok = False
    for _ in range(150):  # up to ~15 s
        if getattr(llm, "_ready_for_realtime_input", False):
            ok = True
            break
        await asyncio.sleep(0.1)

    stop.set()
    poll_task.cancel()
    with contextlib.suppress(Exception):
        await runner.stop_workers()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(run_task, timeout=10)

    names = [e for (_t, e, _x) in tl.events]
    print("lifecycle events:", names)
    print("GEMINI_CONNECTED       :", "GEMINI_CONNECTED" in names)
    print("LLM_CONTEXT_INITIALIZED:", "LLM_CONTEXT_INITIALIZED" in names)
    print("GEMINI_REALTIME_READY  :", "GEMINI_REALTIME_READY" in names)
    print("RESULT:", "PASS — service reached realtime-ready" if ok else "FAIL — never became ready")
    return 0 if ok else 4


def main() -> int:
    ap = argparse.ArgumentParser(description="M2.6A Gemini Live hardware feasibility probe")
    ap.add_argument("--dry", action="store_true", help="validate config only; no hardware, no cloud")
    ap.add_argument(
        "--lifecycle-smoke", action="store_true",
        help="ONE no-microphone authenticated check that the LLMRunFrame kickoff makes "
        "GeminiLiveLLMService reach realtime-ready (proves the R0031 fix; near-zero tokens)",
    )
    ap.add_argument(
        "--recompute", nargs="+", metavar="RESULTS_JSON", default=None,
        help="re-derive metrics (incl. the corrected barge-in analysis) from existing "
        "results JSON timeline(s). No cloud call.",
    )
    ap.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Gemini prebuilt voice name (default {DEFAULT_VOICE!r}, 'Warm'; "
        f"alternatives, not auto-tested: {', '.join(VOICE_ALTERNATIVES)})",
    )
    ap.add_argument("--note", default="", help="free-text note stored in the results JSON")
    args = ap.parse_args()
    try:
        if args.recompute:
            return _recompute(args.recompute)
        if args.lifecycle_smoke:
            return asyncio.run(_lifecycle_smoke())
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
