"""M2.5A.2 — Near-field operator voice detection WITH the XVF3800 AEC
far-end reference fed.  The final M2.5A operator confirmation.

RESEARCH SPIKE ONLY (disposable).  ONE command runs everything; the
operator only has to say one short phrase per trial when prompted.

Background (all accepted, NOT re-tested here)
--------------------------------------------
* Current `plug:usb_speaker`-only route: NeXa's own Piper voice trips the
  reSpeaker + Silero VAD on **14/14** silent-playback trials
  (`spike_playback_false_vad.py`).
* Feeding the XVF3800 its AEC far-end reference (playback PCM also sent to
  `plug:respeaker`): false-VAD **0/4**.
* SPIKE B-live v2: VAD start -> PLAYBACK TASK STOPPED = 28.5 ms mean /
  29.4 ms median / 37.4 ms max.

The one open question
--------------------
Does a **real near-field operator voice remain detectable** while that same
audible playback **and** AEC far-end reference are active?  i.e. does the
XVF3800 AEC suppress the operator together with the echo?

Per trial this harness proves BOTH at once:
  A.  QUIET_AEC phase — playback + AEC reference active, operator silent:
      expect **no** `VADUserStartedSpeakingFrame` (0 false-VAD).
  B.  SPEAK phase — after `>>> SPEAK NOW <<<` the operator says one short
      phrase: expect the first post-arm `VADUserStartedSpeakingFrame`
      within a few hundred ms, **while the AEC reference is still playing**.

AEC-reference routing
---------------------
The audible signal goes to `plug:usb_speaker` (Jieli DAC); the identical
PCM also goes to `plug:respeaker` (the XVF3800 USB *playback* endpoint =
its AEC far-end reference; NeXa attaches no speaker there, so it makes no
extra sound).  A single-writer ALSA `type multi` tee over the two devices
was tried and **rejected** — the two independent USB audio clocks fail
ALSA slave param negotiation (`snd_pcm_hw_refine_slave: Slave PCM not
usable`).  So the reference is fed by a **second `aplay`, spawned
back-to-back** with the first (sub-ms apart; the spawn delta is recorded).
An adaptive AEC estimates and tracks the bulk echo-path delay by design,
so a few ms of start jitter is immaterial — and `spike_playback_false_vad.py`
already showed this exact two-process routing takes false-VAD to 0/4.
Full-duplex on the XVF3800 (capture held by the VAD pipeline while
`aplay` drives `plug:respeaker`) is verified explicitly at start-up and
per trial.  No change to `/etc/asound.conf`, no `src/` change.

3 trials.  Writes spike_aec_nearfield_voice_<ts>.json.

Run:  .venv/bin/python docs/research/m2_5_bargein/spike_aec_nearfield_voice.py
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

PHRASE = (
    "Wszechświat rozszerza się od około trzynastu miliardów ośmiuset "
    "milionów lat, a tempo tego rozszerzania opisuje stała Hubble'a. "
    "Galaktyki oddalają się od siebie tym szybciej, im większa jest "
    "dzieląca je odległość, co odkrył Edwin Hubble w tysiąc dziewięćset "
    "dwudziestym dziewiątym roku, obserwując przesunięcie ku czerwieni."
)
SPEAKER_PCM = "plug:usb_speaker"     # audible Jieli DAC
AEC_REF_PCM = "plug:respeaker"       # XVF3800 USB playback endpoint = AEC far-end ref

N_TRIALS = 3
PHRASES = ["Czekaj.", "Stop, mam pytanie.", "Actually, tell me something else."]

WARMUP_S = 3.0
PLAYBACK_LEADIN_S = 1.5      # after spawning both aplay: settle, then confirm alive
QUIET_AEC_S = 5.0            # operator SILENT; any accepted VAD start here = failure
DETECT_TIMEOUT_S = 15.0      # give up waiting for the post-arm operator VAD start
POST_ACCEPT_LISTEN_S = 2.0
GAP_S = 2.0
FRAME_MS = 512 / 16000 * 1000


# ------------------------------------------------------------------ VAD probe
class _ProbedSilero(SileroVADAnalyzer):
    """Records (conf, vol, speaking, t) for every VAD frame using the real
    production computations — no logic copied."""

    def __init__(self, *, sample_rate=None, params=None, sink: list | None = None):
        super().__init__(sample_rate=sample_rate, params=params)
        self._sink = sink if sink is not None else []
        self._last_conf = 0.0

    def voice_confidence(self, buffer):  # type: ignore[override]
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


# ---------------------------------------------- pure helpers (unit-tested)
def partition_vad_starts(
    start_times: list[float],
    *,
    quiet_start: float,
    quiet_end: float,
    armed_at: float,
) -> dict:
    """Classify VAD speech-start timestamps for one trial. Pure.

    - ``quiet_false``  : start inside [quiet_start, quiet_end]  (operator was
                         told to be silent — any of these fails the trial)
    - ``prearm``       : start before ``armed_at`` and NOT in the quiet
                         window (settling / lead-in)
    - ``accepted``     : the FIRST start at/after ``armed_at`` (the operator)
    - ``post_accept``  : any later start at/after ``armed_at``

    A quiet-window / pre-arm start is NEVER promoted to ``accepted``.
    """
    quiet_false = [t for t in start_times if quiet_start <= t <= quiet_end]
    prearm = [t for t in start_times if t < armed_at and not (quiet_start <= t <= quiet_end)]
    post_arm = sorted(t for t in start_times if t >= armed_at)
    accepted = post_arm[0] if post_arm else None
    post_accept = post_arm[1:] if post_arm else []
    return {
        "quiet_false_vad_count": len(quiet_false),
        "prearm_vad_count": len(prearm),
        "accepted_at": accepted,
        "post_accept_vad_count": len(post_accept),
        "quiet_false_times": quiet_false,
    }


def phase_stats(frames: list[dict], lo: float, hi: float) -> dict:
    """Silero confidence / smoothed-volume summary for frames in [lo, hi). Pure."""
    xs = [f for f in frames if lo <= f["t"] < hi]
    if not xs:
        return {"n_frames": 0}
    conf = sorted(f["conf"] for f in xs)
    vol = sorted(f["vol"] for f in xs)
    spk = sum(1 for f in xs if f["speaking"])

    def p95(v: list[float]) -> float:
        return round(v[min(len(v) - 1, int(0.95 * len(v)))], 4)

    return {
        "n_frames": len(xs),
        "conf_mean": round(statistics.fmean(conf), 4),
        "conf_p95": p95(conf),
        "conf_max": round(conf[-1], 4),
        "vol_mean": round(statistics.fmean(vol), 4),
        "vol_p95": p95(vol),
        "vol_max": round(vol[-1], 4),
        "speaking_frame_frac": round(spk / len(xs), 4),
    }


def summarize_trial(t: dict) -> dict:
    """Derive the reported per-trial metrics from raw capture. Pure.

    ``prompt_to_vad_s`` is reported ONLY as informational — it contains the
    operator's human reaction time and is NOT system latency.
    """
    part = partition_vad_starts(
        t["start_times"],
        quiet_start=t["quiet_start"],
        quiet_end=t["quiet_end"],
        armed_at=t["armed_at"],
    )
    acc = part["accepted_at"]
    detected = acc is not None
    prompt_to_vad = round(acc - t["prompt_printed"], 3) if detected else None
    arm_to_vad = round(acc - t["armed_at"], 3) if detected else None
    return {
        "trial": t["trial"],
        "phrase": t.get("phrase"),
        "main_playback_active": bool(t["main_playback_active"]),
        "aec_reference_active": bool(t["aec_reference_active"]),
        "aec_ref_spawn_delta_ms": t.get("aec_ref_spawn_delta_ms"),
        "quiet_false_vad_count": part["quiet_false_vad_count"],
        "prearm_vad_count": part["prearm_vad_count"],
        "post_arm_vad_detected": detected,
        "post_accept_vad_count": part["post_accept_vad_count"],
        "prompt_to_vad_s_INFORMATIONAL": prompt_to_vad,
        "arm_to_vad_s_INFORMATIONAL": arm_to_vad,
        "aec_reference_active_at_detection": (
            bool(t["aec_reference_active_at_detection"]) if detected else None
        ),
        "main_playback_active_at_detection": (
            bool(t["main_playback_active_at_detection"]) if detected else None
        ),
        "conf_vol_aec_quiet": phase_stats(t["frames"], t["quiet_start"], t["quiet_end"]),
        "conf_vol_operator_speech": (
            phase_stats(t["frames"], acc - 0.25, acc + 1.75) if detected else {"n_frames": 0}
        ),
        "vad_events_rel_arm": [
            {"kind": e["kind"], "rel_arm_s": round(e["t"] - t["armed_at"], 3)}
            for e in t["vad_events"]
        ],
        "trial_valid": bool(t["main_playback_active"] and t["aec_reference_active"]),
    }


def close_criteria(trials: list[dict]) -> dict:
    """Does the run close M2.5A? Pure. Only counts trials where BOTH playback
    endpoints were confirmed active."""
    valid = [t for t in trials if t["trial_valid"]]
    reasons: list[str] = []
    if len(valid) < len(trials):
        reasons.append(
            f"{len(trials) - len(valid)}/{len(trials)} trials had a dead playback "
            f"endpoint (not counted)"
        )
    quiet_bad = [t["trial"] for t in valid if t["quiet_false_vad_count"] > 0]
    if quiet_bad:
        reasons.append(f"QUIET_AEC false-VAD in trials {quiet_bad}")
    not_detected = [t["trial"] for t in valid if not t["post_arm_vad_detected"]]
    if not_detected:
        reasons.append(f"operator voice NOT detected in trials {not_detected}")
    ref_dropped = [
        t["trial"] for t in valid
        if t["post_arm_vad_detected"] and t["aec_reference_active_at_detection"] is False
    ]
    if ref_dropped:
        reasons.append(f"AEC reference not active at detection in trials {ref_dropped}")
    met = bool(valid) and not reasons
    return {
        "m2_5a_close_criteria_met": met,
        "valid_trials": len(valid),
        "quiet_false_vad_total": sum(t["quiet_false_vad_count"] for t in valid),
        "operator_detected": sum(1 for t in valid if t["post_arm_vad_detected"]),
        "blocking_reasons": reasons,
    }


def _agg(xs: list) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "max": None}
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 3),
            "median": round(statistics.median(xs), 3), "max": round(max(xs), 3)}


# ------------------------------------------------------------------ pipeline
def _mk_pipeline(pa: pyaudio.PyAudio, frame_sink: list, vad_sink: list):
    cfg = LocalAudioConfig()
    input_index = find_device_index(pa, cfg.input_device_name, require_input=True)
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=False,   # hold no speaker; both aplay endpoints stay free
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


def _spawn_aplay(dev: str, wav: Path) -> subprocess.Popen:
    return subprocess.Popen(
        ["aplay", "-q", "-D", dev, str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def _poll_err(proc: subprocess.Popen) -> str | None:
    if proc.poll() is None:
        return None
    err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip() if proc.stderr else ""
    return err or f"aplay exited rc={proc.returncode}"


async def _verify_full_duplex(wav: Path, loop: asyncio.AbstractEventLoop) -> dict:
    """Explicitly confirm: while capture is (about to be) held by the VAD
    pipeline, `aplay -D plug:respeaker` opens and runs.  Run BEFORE the
    pipeline exists is not enough — but the pipeline warm-up already holds
    capture when this is called."""
    p = _spawn_aplay(AEC_REF_PCM, wav)
    await asyncio.sleep(1.0)
    err = _poll_err(p)
    ok = err is None
    if p.poll() is None:
        p.terminate()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, p.wait), timeout=2.0)
        except TimeoutError:
            p.kill()
    return {"xvf3800_full_duplex_ok": ok, "error": err}


async def _trial(
    n: int, phrase_hint: str, wav: Path, audio_s: float,
    frame_sink: list, vad_sink: list, loop: asyncio.AbstractEventLoop,
) -> dict:
    ev_lo = len(vad_sink)
    trial_start = time.monotonic()
    print(f"\n--- trial {n}/{N_TRIALS} ---  (stay SILENT until the prompt)")

    # spawn BOTH aplay back-to-back: audible first, AEC reference immediately after
    t_spk = time.monotonic()
    proc_spk = _spawn_aplay(SPEAKER_PCM, wav)
    t_ref = time.monotonic()
    proc_ref = _spawn_aplay(AEC_REF_PCM, wav)
    spawn_delta_ms = round((t_ref - t_spk) * 1000, 2)

    await asyncio.sleep(PLAYBACK_LEADIN_S)
    err_spk = _poll_err(proc_spk)
    err_ref = _poll_err(proc_ref)
    main_active = err_spk is None
    aec_active = err_ref is None
    if not main_active:
        print(f"    !! AUDIBLE PLAYBACK ({SPEAKER_PCM}) DID NOT START: {err_spk}")
    if not aec_active:
        print(f"    !! AEC REFERENCE ({AEC_REF_PCM}) DID NOT START: {err_ref}")
    if main_active and aec_active:
        print(f"    playback + AEC reference active (spawn delta {spawn_delta_ms} ms)")

    # ---- QUIET_AEC phase: operator silent, watch for false VAD
    quiet_start = time.monotonic()
    await asyncio.sleep(QUIET_AEC_S)
    quiet_end = time.monotonic()
    q_now = sum(1 for e in vad_sink[ev_lo:]
                if e["kind"] == "start" and quiet_start <= e["t"] <= quiet_end)
    if q_now:
        print(f"    !! {q_now} VAD start(s) during QUIET_AEC (operator was silent) "
              f"— FALSE-VAD")

    # ---- SPEAK phase
    prompt_printed = time.monotonic()
    print(f'    >>> SPEAK NOW <<<   say:  "{phrase_hint}"  (normal volume, then stop)')
    armed_at = time.monotonic()

    accepted_at: float | None = None
    deadline = armed_at + DETECT_TIMEOUT_S
    while accepted_at is None and time.monotonic() < deadline:
        for e in vad_sink[ev_lo:]:
            if e["kind"] == "start" and e["t"] >= armed_at:
                accepted_at = e["t"]
                break
        await asyncio.sleep(0.01)

    aec_at_detect = main_at_detect = None
    if accepted_at is not None:
        aec_at_detect = proc_ref.poll() is None
        main_at_detect = proc_spk.poll() is None
        print(f"    -> operator VAD start accepted {round(accepted_at - armed_at, 3)}s "
              f"after arming; AEC ref still playing = {aec_at_detect}")
        await asyncio.sleep(POST_ACCEPT_LISTEN_S)
    else:
        print(f"    -> NO post-arm VAD start within {DETECT_TIMEOUT_S}s "
              f"(operator voice NOT detected)")

    # tidy
    for p in (proc_spk, proc_ref):
        if p.poll() is None:
            p.terminate()
            try:
                await asyncio.wait_for(loop.run_in_executor(None, p.wait), timeout=2.0)
            except TimeoutError:
                p.kill()
    after_end = time.monotonic()
    await asyncio.sleep(GAP_S)

    raw = {
        "trial": n,
        "phrase": phrase_hint,
        "trial_start": trial_start,
        "audio_s": audio_s,
        "aec_ref_spawn_delta_ms": spawn_delta_ms,
        "main_playback_active": main_active,
        "aec_reference_active": aec_active,
        "main_playback_error": err_spk,
        "aec_reference_error": err_ref,
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
        "prompt_printed": prompt_printed,
        "armed_at": armed_at,
        "accepted_at": accepted_at,
        "aec_reference_active_at_detection": aec_at_detect,
        "main_playback_active_at_detection": main_at_detect,
        "vad_events": vad_sink[ev_lo:],
        "start_times": [e["t"] for e in vad_sink[ev_lo:] if e["kind"] == "start"],
        "frames": [f for f in frame_sink if trial_start <= f["t"] <= after_end],
    }
    return {**raw, **summarize_trial(raw)}


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    loop = asyncio.get_running_loop()
    print(f"M2.5A.2 — near-field voice WITH XVF3800 AEC reference — {ts}")
    print("You only speak when a trial prints '>>> SPEAK NOW <<<'. Otherwise SILENT.")

    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    wav_bytes = await piper.synthesize(PHRASE, voice=PL_VOICE)
    await piper.stop()
    wav_path = HERE / f"_aec_nf_{ts}.wav"
    wav_path.write_bytes(wav_bytes)
    with wave.open(str(wav_path), "rb") as w:
        audio_s = w.getnframes() / w.getframerate()
    print(f"  Piper phrase: {audio_s:.1f}s  (VAD frame {FRAME_MS:.0f} ms; "
          f"params {DEFAULT_VAD_PARAMS})")

    pa = pyaudio.PyAudio()
    frame_sink: list = []
    vad_sink: list = []
    worker = _mk_pipeline(pa, frame_sink, vad_sink)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(WARMUP_S)  # capture is now held by the pipeline

    fd = await _verify_full_duplex(wav_path, loop)
    print(f"  XVF3800 full-duplex (capture held + aplay to {AEC_REF_PCM}): "
          f"{'OK' if fd['xvf3800_full_duplex_ok'] else 'FAILED — ' + str(fd['error'])}")

    trials: list[dict] = []
    try:
        if not fd["xvf3800_full_duplex_ok"]:
            print("  !! Cannot feed the AEC reference while capturing — aborting. "
                  "Investigate the ALSA error above before re-running.")
        else:
            for i in range(1, N_TRIALS + 1):
                trials.append(await _trial(
                    i, PHRASES[(i - 1) % len(PHRASES)], wav_path, audio_s,
                    frame_sink, vad_sink, loop))
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
        pa.terminate()
        wav_path.unlink(missing_ok=True)

    clean = [{k: v for k, v in t.items() if k != "frames"} for t in trials]
    valid = [t for t in trials if t["trial_valid"]]
    summary = {
        "trials": len(trials),
        "xvf3800_full_duplex": fd,
        "aec_routing": "dual_aplay_time_adjacent (single-writer ALSA 'type multi' "
                       "tee rejected: independent USB clocks fail slave negotiation)",
        "main_playback_active": sum(1 for t in trials if t["main_playback_active"]),
        "aec_reference_active": sum(1 for t in trials if t["aec_reference_active"]),
        "quiet_false_vad_total": sum(t["quiet_false_vad_count"] for t in valid),
        "post_arm_vad_detected": sum(1 for t in valid if t["post_arm_vad_detected"]),
        "prearm_vad_total": sum(t["prearm_vad_count"] for t in valid),
        "post_accept_vad_total": sum(t["post_accept_vad_count"] for t in valid),
        "aec_ref_spawn_delta_ms": _agg([t["aec_ref_spawn_delta_ms"] for t in trials]),
        "prompt_to_vad_s_INFORMATIONAL": _agg(
            [t["prompt_to_vad_s_INFORMATIONAL"] for t in valid]),
        "conf_vol_separation": {
            "aec_quiet_conf_p95": _agg(
                [t["conf_vol_aec_quiet"].get("conf_p95") for t in valid
                 if t["conf_vol_aec_quiet"].get("n_frames")]),
            "aec_quiet_vol_p95": _agg(
                [t["conf_vol_aec_quiet"].get("vol_p95") for t in valid
                 if t["conf_vol_aec_quiet"].get("n_frames")]),
            "operator_speech_conf_p95": _agg(
                [t["conf_vol_operator_speech"].get("conf_p95") for t in valid
                 if t["conf_vol_operator_speech"].get("n_frames")]),
            "operator_speech_vol_p95": _agg(
                [t["conf_vol_operator_speech"].get("vol_p95") for t in valid
                 if t["conf_vol_operator_speech"].get("n_frames")]),
        },
        **close_criteria(trials),
    }
    out = {"ts": ts, "phrase": PHRASE, "audio_s": round(audio_s, 2),
           "vad_params": {
               "confidence": DEFAULT_VAD_PARAMS.confidence,
               "start_secs": DEFAULT_VAD_PARAMS.start_secs,
               "stop_secs": DEFAULT_VAD_PARAMS.stop_secs,
               "min_volume": DEFAULT_VAD_PARAMS.min_volume},
           "params": {"WARMUP_S": WARMUP_S, "PLAYBACK_LEADIN_S": PLAYBACK_LEADIN_S,
                      "QUIET_AEC_S": QUIET_AEC_S, "DETECT_TIMEOUT_S": DETECT_TIMEOUT_S},
           "trials_detail": clean, "summary": summary}
    (HERE / f"spike_aec_nearfield_voice_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n-> spike_aec_nearfield_voice_{ts}.json")
    print("=" * 64)
    print(f"VERDICT ({ts}):")
    print(f"  audible playback active   : {summary['main_playback_active']}/{len(trials)}")
    print(f"  AEC reference active      : {summary['aec_reference_active']}/{len(trials)}")
    print(f"  valid trials (both active): {summary['valid_trials']}/{len(trials)}")
    print(f"  QUIET_AEC false-VAD       : {summary['quiet_false_vad_total']}  "
          f"(target 0)")
    print(f"  operator voice detected   : {summary['operator_detected']}/"
          f"{summary['valid_trials']}  (target {summary['valid_trials']})")
    s = summary["conf_vol_separation"]
    print(f"  Silero sep  AEC-quiet  conf_p95={s['aec_quiet_conf_p95']['mean']} "
          f"vol_p95={s['aec_quiet_vol_p95']['mean']}")
    print(f"              operator     conf_p95={s['operator_speech_conf_p95']['mean']} "
          f"vol_p95={s['operator_speech_vol_p95']['mean']}")
    p = summary["prompt_to_vad_s_INFORMATIONAL"]
    print(f"  prompt->VAD [INFORMATIONAL, incl. human reaction]: "
          f"mean={p['mean']} median={p['median']} max={p['max']} s")
    print(f"  M2.5A CLOSE CRITERIA MET  : {summary['m2_5a_close_criteria_met']}")
    if summary["blocking_reasons"]:
        for r in summary["blocking_reasons"]:
            print(f"      - {r}")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
