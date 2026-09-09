"""M2.5A.1 — Playback-time false-VAD / self-echo state investigation.

RESEARCH SPIKE ONLY (disposable). **No operator interaction required** — the
machine plays NeXa's own Piper voice and observes whether the reSpeaker +
Silero VAD falsely enter ``USER_SPEAKING`` while the operator is silent.

Why this spike exists
---------------------
SPIKE B-live v2 (`spike_bargein_live_20260909_142619.json`) — the first run
where playback was *confirmed genuinely active* — showed pre-arm
``VADUserStartedSpeakingFrame`` events in **2 / 5** trials, with the VAD
latched in speech state for ~10 s (trial 1) and ~3.5 s (trial 4). That
contradicts SPIKE A (`spike_self_echo.py`), which reported **0** false
``USER_SPEAKING`` during playback.

**The contradiction is a measurement artifact of SPIKE A, confirmed by
code inspection:** `spike_self_echo.py` AND `spike_interruption_latency.py`
both run a full ``VoiceRuntime(LocalAudioConfig())``, whose audio-output
transport (`audio_out_enabled=True`, hard-wired in `_build_pipeline`) holds
``plug:usb_speaker`` open. Their trial ``aplay -D plug:usb_speaker`` then
hits ``Device or resource busy`` and exits in milliseconds (stderr →
``DEVNULL``). **So SPIKE A never actually played sound** — its "0 false
barge-in (A1/A2)" and the headless spike's "0/8 detections" are
no-playback artifacts, not evidence of self-echo safety. Only SPIKE B-live
v2 (VAD-only pipeline, `audio_out_enabled=False`, speaker free for
``aplay``) actually drove the speaker while the VAD listened.

What this spike measures
------------------------
Same production VAD path as v2 (real reSpeaker input, real
``SileroVADAnalyzer`` with ``DEFAULT_VAD_PARAMS`` from
``nexa.voice.runtime``, real ``VADProcessor``), ``audio_out_enabled=False``
so the trial ``aplay`` genuinely drives ``plug:usb_speaker`` at the normal
level. NO ``ConversationSession``, NO STT, NO LLM.

Per-VAD-frame telemetry via a spike-only ``SileroVADAnalyzer`` subclass
(`_ProbedSilero`) that records — using the **real** production
computations, no logic duplicated — every 512-sample frame's Silero
speech ``confidence``, smoothed ``volume`` (the ``min_volume`` gate input),
the ``speaking`` boolean (`confidence >= 0.7 AND volume >= 0.6`) and a
monotonic timestamp. ``VADState`` start/stop is taken from the real
``VADProcessor`` output frames.

Blocks
------
* ``BASE``      — persistent pipeline, NO playback: the idle-room
  confidence/volume floor (control).
* ``PERSIST``   — one persistent pipeline (like production ``VoiceRuntime``),
  N playback trials. The primary evidence.
* ``FRESH``     — a brand-new analyzer + pipeline per trial: does Silero
  carry stale speech state across trials?
* ``AEC_REF``   — persistent pipeline, playback ALSO duplicated to
  ``plug:respeaker`` (the XVF3800 USB playback endpoint = its AEC far-end
  reference). Does feeding the hardware AEC its reference remove the false
  VAD? (near-field detectability is NOT testable here — operator silent.)

Per trial: playback start/stop, every VAD speaking interval (start/end/dur,
began before|during|after playback), count of ``USER_SPEAKING`` starts, max
continuous speaking-state duration, and per-phase (`quiet_before` /
`playback` / `after`) confidence & volume stats + fraction of `speaking`
frames.

No user speech/audio is persisted — only per-frame scalars (conf, vol) and
timestamps.

Writes spike_playback_false_vad_<ts>.json.

Run:  .venv/bin/python docs/research/m2_5_bargein/spike_playback_false_vad.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

import numpy as np  # noqa: E402
import pyaudio  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    Frame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.audio.vad_processor import VADProcessor  # noqa: E402
from pipecat.processors.frame_processor import (  # noqa: E402
    FrameDirection,
    FrameProcessor,
)
from pipecat.transports.local.audio import (  # noqa: E402
    LocalAudioTransport,
    LocalAudioTransportParams,
)
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402
from nexa.voice import LocalAudioConfig  # noqa: E402
from nexa.voice.device import find_device_index  # noqa: E402
from nexa.voice.runtime import DEFAULT_VAD_PARAMS  # noqa: E402

# Same phrase SPIKE B-live v2 used, so the false-VAD finding is reproduced
# under the identical stimulus (~19 s, 3 sentence boundaries).
PHRASE = (
    "Wszechświat rozszerza się od około trzynastu miliardów ośmiuset "
    "milionów lat, a tempo tego rozszerzania opisuje stała Hubble'a. "
    "Galaktyki oddalają się od siebie tym szybciej, im większa jest "
    "dzieląca je odległość, co odkrył Edwin Hubble w tysiąc dziewięćset "
    "dwudziestym dziewiątym roku, obserwując przesunięcie ku czerwieni."
)
SPEAKER_PCM = "plug:usb_speaker"
AEC_REF_PCM = "plug:respeaker"  # XVF3800 USB playback endpoint = AEC far-end ref

WARMUP_S = 3.0
QUIET_BEFORE_S = 2.5
PLAYBACK_LEADIN_S = 1.0
TAIL_S = 3.0
GAP_S = 1.5

N_BASE = 3
N_PERSIST = 10
N_FRESH = 4
N_AEC = 4

# A VAD frame is 512 samples @ 16 kHz = 32 ms; DEFAULT_VAD_PARAMS.stop_secs=1.0
# => ~31 sub-threshold frames needed to release SPEAKING. start_secs=0.2 => ~6.
FRAME_MS = 512 / 16000 * 1000


class _ProbedSilero(SileroVADAnalyzer):
    """Records (conf, vol, speaking, t) for every VAD frame using the real
    production computations — no logic copied. ``_run_analyzer`` calls
    ``voice_confidence`` then ``_get_smoothed_volume`` once per 512-sample
    frame; we hook both."""

    def __init__(self, *, sample_rate=None, params=None, sink: list | None = None):
        super().__init__(sample_rate=sample_rate, params=params)
        self._sink = sink if sink is not None else []
        self._last_conf = 0.0

    def voice_confidence(self, buffer):  # type: ignore[override]
        # Return the parent value UNCHANGED (it may be a 1-elem ndarray that
        # the base _run_analyzer compares directly); only coerce a copy for
        # telemetry.
        c = super().voice_confidence(buffer)
        try:
            self._last_conf = float(np.asarray(c).reshape(-1)[0])
        except (ValueError, TypeError, IndexError):
            self._last_conf = float(c) if isinstance(c, (int, float)) else 0.0
        return c

    def _get_smoothed_volume(self, audio: bytes) -> float:  # type: ignore[override]
        v = float(super()._get_smoothed_volume(audio))
        c = self._last_conf
        self._sink.append({
            "t": time.monotonic(),
            "conf": round(c, 4),
            "vol": round(v, 4),
            "speaking": bool(c >= self._params.confidence and v >= self._params.min_volume),
        })
        return v


class _VadWatcher(FrameProcessor):
    """Timestamps every VAD start/stop frame. Forwards all frames."""

    def __init__(self, sink: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sink = sink

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._sink.append({"t": time.monotonic(), "kind": "start"})
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._sink.append({"t": time.monotonic(), "kind": "stop"})
        await self.push_frame(frame, direction)


# --------------------------------------------------------------------------
# pure analysis helpers (unit-tested offline, no pipecat/pyaudio import)
# --------------------------------------------------------------------------
def speaking_intervals(vad_events: list[dict]) -> list[tuple[float, float | None]]:
    """Collapse a start/stop event list into (start_t, stop_t|None) spans.
    A start with no matching stop yields (start_t, None). Extra stops are
    ignored."""
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


def classify_interval(start_t: float, playback_start: float, playback_end: float) -> str:
    """Where a VAD speaking interval began, relative to playback."""
    if start_t < playback_start:
        return "before"
    if start_t <= playback_end:
        return "during"
    return "after"


def summarize_trial(
    *,
    playback_started: bool,
    playback_start: float,
    playback_end: float,
    after_end: float,
    vad_events: list[dict],
    frames: list[dict],
) -> dict:
    """Turn one trial's raw capture into reported metrics. Pure."""
    spans = speaking_intervals(vad_events)
    span_out = []
    max_cont = 0.0
    n_during = 0
    for s, e in spans:
        end = e if e is not None else after_end
        dur = round(end - s, 3)
        where = classify_interval(s, playback_start, playback_end)
        if where in ("before", "during"):
            n_during += 1 if where == "during" else 0
        span_out.append({
            "rel_playback_start_s": round(s - playback_start, 3),
            "duration_s": dur,
            "began": where,
            "closed": e is not None,
        })
        max_cont = max(max_cont, dur)

    def _phase(lo: float, hi: float) -> dict:
        xs = [f for f in frames if lo <= f["t"] < hi]
        if not xs:
            return {"n_frames": 0}
        conf = [f["conf"] for f in xs]
        vol = [f["vol"] for f in xs]
        spk = [f for f in xs if f["speaking"]]
        return {
            "n_frames": len(xs),
            "conf_mean": round(statistics.fmean(conf), 4),
            "conf_p95": round(sorted(conf)[min(len(conf) - 1, int(0.95 * len(conf)))], 4),
            "conf_max": round(max(conf), 4),
            "vol_mean": round(statistics.fmean(vol), 4),
            "vol_p95": round(sorted(vol)[min(len(vol) - 1, int(0.95 * len(vol)))], 4),
            "vol_max": round(max(vol), 4),
            "speaking_frame_frac": round(len(spk) / len(xs), 4),
        }

    n_starts = sum(1 for ev in vad_events if ev["kind"] == "start")
    return {
        "playback_started": playback_started,
        "user_speaking_starts": n_starts,
        "user_speaking_starts_during_playback": n_during
        + sum(1 for sp in span_out if sp["began"] == "before"),
        "false_vad": n_starts > 0,
        "max_continuous_speaking_s": round(max_cont, 3),
        "speaking_intervals": span_out,
        "phase_quiet_before": _phase(playback_start - QUIET_BEFORE_S, playback_start),
        "phase_playback": _phase(playback_start, playback_end),
        "phase_after": _phase(playback_end, after_end),
    }


def verdict_300ms_safe(trials: list[dict]) -> dict:
    """Can a naive 'sustained VAD >= 300 ms while responding' trigger fire
    safely? It is UNSAFE if any silent-playback trial sustained the VAD
    speaking state for >= 0.3 s."""
    offenders = [
        i + 1 for i, t in enumerate(trials)
        if t.get("playback_started") and t["max_continuous_speaking_s"] >= 0.3
    ]
    worst = max((t["max_continuous_speaking_s"] for t in trials
                 if t.get("playback_started")), default=0.0)
    return {
        "safe": not offenders,
        "offending_trials": offenders,
        "worst_continuous_speaking_s": round(worst, 3),
    }


def _agg(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "max": None}
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 3),
            "median": round(statistics.median(xs), 3), "max": round(max(xs), 3)}


# --------------------------------------------------------------------------
def _mk_pipeline(pa: pyaudio.PyAudio, frame_sink: list, vad_sink: list):
    cfg = LocalAudioConfig()
    input_index = find_device_index(pa, cfg.input_device_name, require_input=True)
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=False,
            audio_in_sample_rate=cfg.sample_rate,
            audio_in_channels=cfg.channels,
            input_device_index=input_index,
        )
    )
    analyzer = _ProbedSilero(
        sample_rate=cfg.sample_rate,
        params=VADParams(
            confidence=DEFAULT_VAD_PARAMS.confidence,
            start_secs=DEFAULT_VAD_PARAMS.start_secs,
            stop_secs=DEFAULT_VAD_PARAMS.stop_secs,
            min_volume=DEFAULT_VAD_PARAMS.min_volume,
        ),
        sink=frame_sink,
    )
    pipeline = Pipeline(
        [transport.input(), VADProcessor(vad_analyzer=analyzer), _VadWatcher(vad_sink)]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=cfg.sample_rate),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    return worker


async def _playback_trial(
    wav_path: Path, audio_s: float, frame_sink: list, vad_sink: list,
    *, play: bool, aec_ref: bool, loop: asyncio.AbstractEventLoop,
) -> dict:
    ev_lo = len(vad_sink)
    quiet_start = time.monotonic()
    await asyncio.sleep(QUIET_BEFORE_S)

    proc = None
    ref_proc = None
    playback_start = time.monotonic()
    if play:
        proc = subprocess.Popen(
            ["aplay", "-q", "-D", SPEAKER_PCM, str(wav_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if aec_ref:
            ref_proc = subprocess.Popen(
                ["aplay", "-q", "-D", AEC_REF_PCM, str(wav_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    await asyncio.sleep(PLAYBACK_LEADIN_S)
    playback_started = True
    playback_error = None
    if play:
        if proc.poll() is not None:
            playback_started = False
            err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            playback_error = err or f"aplay rc={proc.returncode}"
    else:
        playback_started = False  # BASE: no playback by design

    await asyncio.sleep(max(0.0, audio_s - PLAYBACK_LEADIN_S) + TAIL_S)
    playback_end = playback_start + (audio_s if play else 0.0)
    for p in (proc, ref_proc):
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                await asyncio.wait_for(loop.run_in_executor(None, p.wait), timeout=2.0)
            except TimeoutError:
                p.kill()
    after_end = time.monotonic()
    await asyncio.sleep(GAP_S)

    trial_frames = [f for f in frame_sink if quiet_start <= f["t"] <= after_end]
    trial_events = vad_sink[ev_lo:]
    summ = summarize_trial(
        playback_started=playback_started if play else True,  # BASE analysed as "ran"
        playback_start=playback_start,
        playback_end=playback_end,
        after_end=after_end,
        vad_events=trial_events,
        frames=trial_frames,
    )
    summ["playback_error"] = playback_error
    if not play:
        summ["playback_started"] = False
        summ["note"] = "BASE control — no playback"
    return summ


async def _run_persistent_block(
    name: str, n: int, wav_path: Path, audio_s: float,
    *, play: bool, aec_ref: bool, loop,
) -> list[dict]:
    pa = pyaudio.PyAudio()
    frame_sink: list = []
    vad_sink: list = []
    worker = _mk_pipeline(pa, frame_sink, vad_sink)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(WARMUP_S)
    out = []
    try:
        for i in range(1, n + 1):
            print(f"  [{name}] trial {i}/{n} …", flush=True)
            r = await _playback_trial(wav_path, audio_s, frame_sink, vad_sink,
                                      play=play, aec_ref=aec_ref, loop=loop)
            r["trial"] = i
            print(f"      false_vad={r['false_vad']} "
                  f"starts={r['user_speaking_starts']} "
                  f"max_cont_speaking={r['max_continuous_speaking_s']}s "
                  f"playback_ok={r['playback_started']}")
            out.append(r)
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
        pa.terminate()
    return out


async def _run_fresh_block(
    name: str, n: int, wav_path: Path, audio_s: float, *, loop
) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        print(f"  [{name}] trial {i}/{n} (fresh pipeline) …", flush=True)
        pa = pyaudio.PyAudio()
        frame_sink: list = []
        vad_sink: list = []
        worker = _mk_pipeline(pa, frame_sink, vad_sink)
        runner = WorkerRunner()
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await asyncio.sleep(WARMUP_S)
        try:
            r = await _playback_trial(wav_path, audio_s, frame_sink, vad_sink,
                                      play=True, aec_ref=False, loop=loop)
            r["trial"] = i
            print(f"      false_vad={r['false_vad']} "
                  f"starts={r['user_speaking_starts']} "
                  f"max_cont_speaking={r['max_continuous_speaking_s']}s "
                  f"playback_ok={r['playback_started']}")
            out.append(r)
        finally:
            run_task.cancel()
            try:
                await run_task
            except (asyncio.CancelledError, Exception):
                pass
            pa.terminate()
    return out


def _block_summary(trials: list[dict]) -> dict:
    ran = [t for t in trials if t.get("playback_started")]
    base = trials if not ran else ran
    false_trials = [t for t in base if t["false_vad"]]
    return {
        "trials": len(trials),
        "playback_confirmed_active": len(ran),
        "false_vad_trials": len(false_trials),
        "false_vad_rate": round(len(false_trials) / len(base), 3) if base else None,
        "total_user_speaking_starts": sum(t["user_speaking_starts"] for t in base),
        "max_continuous_speaking_s": _agg([t["max_continuous_speaking_s"] for t in base]),
        "playback_phase_conf_p95": _agg(
            [t["phase_playback"].get("conf_p95") for t in base
             if t["phase_playback"].get("n_frames")]
        ),
        "playback_phase_vol_p95": _agg(
            [t["phase_playback"].get("vol_p95") for t in base
             if t["phase_playback"].get("n_frames")]
        ),
        "playback_phase_speaking_frac": _agg(
            [t["phase_playback"].get("speaking_frame_frac") for t in base
             if t["phase_playback"].get("n_frames")]
        ),
        "verdict_300ms": verdict_300ms_safe(base),
    }


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    loop = asyncio.get_running_loop()
    print(f"M2.5A.1 playback-time false-VAD investigation — {ts}")
    print("Operator: nothing to do. NeXa speaks; the machine watches the VAD.")

    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    wav_bytes = await piper.synthesize(PHRASE, voice=PL_VOICE)
    await piper.stop()
    wav_path = HERE / f"_false_vad_{ts}.wav"
    wav_path.write_bytes(wav_bytes)
    with wave.open(str(wav_path), "rb") as w:
        audio_s = w.getnframes() / w.getframerate()
    print(f"  Piper phrase: {audio_s:.1f}s  (VAD frame = {FRAME_MS:.0f} ms; "
          f"stop_secs={DEFAULT_VAD_PARAMS.stop_secs}s)")

    blocks: dict[str, list[dict]] = {}
    try:
        blocks["BASE"] = await _run_persistent_block(
            "BASE", N_BASE, wav_path, audio_s, play=False, aec_ref=False, loop=loop)
        blocks["PERSIST"] = await _run_persistent_block(
            "PERSIST", N_PERSIST, wav_path, audio_s, play=True, aec_ref=False, loop=loop)
        blocks["FRESH"] = await _run_fresh_block(
            "FRESH", N_FRESH, wav_path, audio_s, loop=loop)
        blocks["AEC_REF"] = await _run_persistent_block(
            "AEC_REF", N_AEC, wav_path, audio_s, play=True, aec_ref=True, loop=loop)
    finally:
        wav_path.unlink(missing_ok=True)

    summary = {name: _block_summary(tr) for name, tr in blocks.items()}
    out = {
        "ts": ts, "phrase": PHRASE, "audio_s": round(audio_s, 2),
        "vad_params": {
            "confidence": DEFAULT_VAD_PARAMS.confidence,
            "start_secs": DEFAULT_VAD_PARAMS.start_secs,
            "stop_secs": DEFAULT_VAD_PARAMS.stop_secs,
            "min_volume": DEFAULT_VAD_PARAMS.min_volume,
        },
        "params": {"WARMUP_S": WARMUP_S, "QUIET_BEFORE_S": QUIET_BEFORE_S,
                   "PLAYBACK_LEADIN_S": PLAYBACK_LEADIN_S, "TAIL_S": TAIL_S},
        "blocks": blocks, "summary": summary,
    }
    (HERE / f"spike_playback_false_vad_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print(f"\n-> spike_playback_false_vad_{ts}.json")
    print("=" * 64)
    for name in ("BASE", "PERSIST", "FRESH", "AEC_REF"):
        s = summary[name]
        v = s["verdict_300ms"]
        print(f"{name:8s} active={s['playback_confirmed_active']}/{s['trials']} "
              f"false-VAD {s['false_vad_trials']}/{s['trials']} "
              f"(rate {s['false_vad_rate']})  "
              f"max-cont-speaking mean={s['max_continuous_speaking_s']['mean']}s "
              f"max={s['max_continuous_speaking_s']['max']}s  "
              f"300ms-safe={v['safe']}")
        print(f"         playback-phase conf_p95={s['playback_phase_conf_p95']['mean']} "
              f"vol_p95={s['playback_phase_vol_p95']['mean']} "
              f"speaking-frac={s['playback_phase_speaking_frac']['mean']}")
    print("=" * 64)
    persist_safe = summary["PERSIST"]["verdict_300ms"]["safe"]
    aec_false = summary["AEC_REF"]["false_vad_trials"]
    print(f"CAN 300 ms SUSTAINED VAD SAFELY TRIGGER BARGE-IN?  "
          f"{'YES' if persist_safe else 'NO'}")
    print(f"AEC far-end reference fed → false-VAD trials: {aec_false}/"
          f"{summary['AEC_REF']['trials']}")


if __name__ == "__main__":
    asyncio.run(main())
