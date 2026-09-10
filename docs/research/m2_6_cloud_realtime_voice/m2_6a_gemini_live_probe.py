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
class Timeline:
    """Append-only (event, monotonic_seconds) log on one clock."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.events: list[tuple[float, str, dict]] = []
        self.counters: dict[str, float] = {}

    def mark(self, event: str, **extra: object) -> float:
        t = time.monotonic() - self.t0
        self.events.append((round(t, 4), event, dict(extra)))
        return t

    def add(self, counter: str, n: float = 1) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + n

    # ---- derived latencies (best-effort; multiple turns -> list) --------- #
    def _pairs(self, a: str, b: str) -> list[float]:
        """For each `a` mark, the delta to the next `b` mark after it."""
        out: list[float] = []
        a_ts = [t for (t, e, _x) in self.events if e == a]
        b_ts = [t for (t, e, _x) in self.events if e == b]
        for ta in a_ts:
            nxt = [tb for tb in b_ts if tb >= ta]
            if nxt:
                out.append(round(nxt[0] - ta, 4))
        return out

    def derive(self) -> dict:
        return {
            "eot_to_first_server_content_s": self._pairs("LOCAL_VAD_EOT", "FIRST_SERVER_CONTENT"),
            "eot_to_first_audio_received_s": self._pairs("LOCAL_VAD_EOT", "FIRST_AUDIO_RECEIVED"),
            "eot_to_first_audio_played_s": self._pairs("LOCAL_VAD_EOT", "FIRST_AUDIO_PLAYED"),
            "eot_to_input_transcription_first_s": self._pairs("LOCAL_VAD_EOT", "INPUT_TRANSCRIPTION_FIRST"),
            "eot_to_input_transcription_flushed_s": self._pairs("LOCAL_VAD_EOT", "INPUT_TRANSCRIPTION_FLUSHED"),
            "input_transcription_first_to_first_audio_s": self._pairs("INPUT_TRANSCRIPTION_FIRST", "FIRST_AUDIO_RECEIVED"),
            "input_transcription_flushed_to_first_audio_s": self._pairs("INPUT_TRANSCRIPTION_FLUSHED", "FIRST_AUDIO_RECEIVED"),
            "vad_start_to_local_playback_stopped_s": self._pairs("LOCAL_VAD_START", "LOCAL_PLAYBACK_STOPPED"),
            "server_interrupted_to_playback_stopped_s": self._pairs("SERVER_INTERRUPTED", "LOCAL_PLAYBACK_STOPPED"),
            "reconnect_start_to_ready_s": self._pairs("RECONNECT_START", "RECONNECT_READY"),
        }


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
    }
    for _name in (
        "InputAudioRawFrame", "InterruptionFrame", "LLMFullResponseEndFrame",
        "StartFrame", "TranscriptionFrame", "TTSAudioRawFrame", "TTSStartedFrame",
        "TTSStoppedFrame", "TTSTextFrame", "UserStartedSpeakingFrame",
        "UserStoppedSpeakingFrame", "BotStartedSpeakingFrame", "BotStoppedSpeakingFrame",
    ):
        P[_name] = getattr(F, _name)
    return P


def _build(P: dict, tl: Timeline, key: str, *, dry: bool):
    """Construct the probe pipeline. Returns (worker, runner_cls, meta).

    ``dry=True`` builds the cloud-side objects (Settings, LLMContext,
    aggregator pair, GeminiLiveLLMService — validates the pipecat/Gemini API
    surface) but opens NO audio device and makes NO network connection.
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
    context = P["LLMContext"](messages=[{"role": "system", "content": SPIKE_SYSTEM_INSTRUCTION}])
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
            vad=P["GeminiVADParams"](disabled=True),
            context_window_compression=P["ContextWindowCompressionParams"](enabled=True),
            system_instruction=SPIKE_SYSTEM_INSTRUCTION,
        ),
        user_audio_preroll_secs=None,  # auto-size from Silero start_secs
    )
    meta["settings_ok"] = True

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

        def _ready(self) -> bool:
            return bool(getattr(self._llm, "_ready_for_realtime_input", False))

        async def process_frame(self, frame, direction) -> None:  # noqa: ANN001
            await super().process_frame(frame, direction)
            tl = self._tl

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
                if "INPUT_TRANSCRIPTION_FIRST" not in {e for (_t, e, _x) in tl.events} or True:
                    tl.mark("INPUT_TRANSCRIPTION_FIRST", text_len=len(getattr(frame, "text", "") or ""))
                tl.add("input_transcription_frames", 1)
                # A frame with a trailing sentence punctuation is treated as a flush.
                txt = (getattr(frame, "text", "") or "").strip()
                if txt.endswith((".", "!", "?", "…")):
                    tl.mark("INPUT_TRANSCRIPTION_FLUSHED", text_len=len(txt))
            elif isinstance(frame, fr["TTSTextFrame"]):
                tl.mark("OUTPUT_TRANSCRIPTION", text_len=len(getattr(frame, "text", "") or ""))
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
                tl.mark("FIRST_SERVER_CONTENT")
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
                    tl.mark("SERVER_INTERRUPTED")
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


async def _run(args) -> int:
    tl = Timeline()
    key = _load_key()
    P = _pipecat()

    if not args.dry and not key:
        print("BLOCKED: NEXA_GEMINI_API_KEY not set and no key in", SECRETS_FILE)
        return 2

    worker, _runner_cls, meta = _build(P, tl, key, dry=args.dry)

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

    cap_task = asyncio.create_task(_cap())
    run_task = asyncio.create_task(runner.run())
    tl.mark("SESSION_START")
    print("Listening. Speak to NeXa (cloud). Ctrl+C to stop.\n")

    await asyncio.wait({run_task, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
    cap_task.cancel()
    tl.mark("SESSION_STOP")

    with contextlib.suppress(Exception):
        await runner.stop_workers()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(run_task, timeout=10)

    path = _write_results(tl, meta, note=args.note or "operator live session")
    print("\n" + "=" * 74)
    print("SESSION ENDED — summary")
    print("=" * 74)
    d = tl.derive()
    for k, v in d.items():
        if v:
            print(f"  {k:44} {v}")
    c = tl.counters
    print(f"  mic audio presented (s)                      {c.get('mic_audio_ms_presented', 0) / 1000:.1f}")
    print(f"  mic audio while LLM NOT ready (s) [#5465]     {c.get('mic_audio_ms_while_not_ready', 0) / 1000:.2f}   windows={c.get('not_ready_window_count', 0)}")
    print(f"  cloud audio received (s)                      {c.get('cloud_audio_ms_received', 0) / 1000:.1f}")
    print(f"  observed mic input chunk (bytes)             {c.get('mic_chunk_bytes_sample', '?')}")
    ah = meta.get("_aec_health")
    if ah is not None:
        print(f"  AEC ref: ever_started={ah.ever_started} active_at_end={ah.active} failures={ah.failure_count}")
    print(f"\nresults JSON: {path}")
    print("This probe does NOT judge audio quality — that is the operator's call.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="M2.6A Gemini Live hardware feasibility probe")
    ap.add_argument("--dry", action="store_true", help="validate config only; no hardware, no cloud")
    ap.add_argument("--note", default="", help="free-text note stored in the results JSON")
    args = ap.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
