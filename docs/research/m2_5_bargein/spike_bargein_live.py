"""M2.5A — SPIKE B-live (v2): operator barge-in control-latency measurement.

RESEARCH SPIKE ONLY (disposable). Needs the operator in the room.

Why v2 exists
-------------
The first run (spike_bargein_live_20260909_134647.json) produced
``detected 5/5, onset-after-prompt mean=5.168 s`` — but that number is
**invalid** and must not be quoted as NeXa's barge-in latency:

1. It has **no arming window.** The script accepted the first-ever
   ``USER_SPEAKING`` transition, so trial 5 latched a VAD event that fired
   ~2.6 s into the pre-playback warm-up (recorded ``rel_play_s: null``,
   ``onset_after_prompt_s: -2.359``) — impossible as a reply to a prompt
   that had not been printed yet.
2. It reported ``playback killed None`` in **every** trial, i.e. it never
   once measured VAD-onset -> playback-stop. Root cause: the spike played
   NeXa's speech with a **second** ``aplay`` process on
   ``plug:usb_speaker``, which is a non-mixing raw-hw ALSA device
   (``hw:CARD=UACDemoV10,DEV=0``, no ``dmix`` — see /etc/asound.conf).
   The old harness ran a full ``VoiceRuntime``, whose audio-output
   transport (``audio_out_enabled=True``, hard-wired) already holds that
   device open. ``aplay`` therefore hit ``Device or resource busy`` and
   exited within milliseconds; its ``stderr`` was routed to ``DEVNULL`` so
   the failure was invisible. By the time any VAD onset fired, ``p.poll()``
   already returned the early exit code, so the ``p.poll() is None`` guard
   was false and ``p.kill()`` was never called. **NeXa was almost
   certainly silent for the whole first run**, so its "onset-after-prompt"
   figures are operator idle time against no audio.
3. ``prompt -> VAD`` bundles the operator's human reaction time. That is
   logged here for reference but is **not** system barge-in latency.

What v2 does differently
------------------------
* Builds a **VAD-only** Pipeline from the real production components
  (``SileroVADAnalyzer`` + ``VADProcessor`` + ``DEFAULT_VAD_PARAMS`` from
  ``nexa.voice.runtime``) with ``audio_out_enabled=False`` — so this
  harness does **not** hold ``usb_speaker`` and the trial ``aplay`` can
  actually open it. No production module is modified; ``VoiceRuntime``
  itself is not used because it cannot be told to release the speaker.
* ``aplay`` is spawned with ``stderr=PIPE``. Each trial verifies playback
  is genuinely running (``poll()`` after a lead-in) before arming; a
  device-busy / failed ``aplay`` is reported loudly, not silently ignored.
* Explicit per-trial measurement window (operator's A-J):
  start playback -> confirm active -> discard & count all pre-arm VAD
  events -> print SPEAK NOW -> arm -> accept ONLY the first
  ``VADUserStartedSpeakingFrame`` at/after ``trial_armed`` -> timestamp,
  request stop, ``proc.kill()`` -> ``proc.wait()`` -> timestamp -> ignore
  (count) all later VAD starts.
* Records: ``playback_started``, ``prompt_printed``, ``trial_armed``,
  ``vad_user_started``, ``playback_stop_requested``,
  ``playback_actually_stopped``, ``prearm_vad_count``,
  ``post_accept_vad_count``.
* Derives:
    - ``prompt_to_vad_s``            operator reaction + VAD — INFORMATIONAL
    - ``vad_to_stop_request_ms``     system control-plane latency
    - ``vad_to_playback_stopped_ms`` media-stop latency == **PLAYBACK TASK
      STOPPED** (the ``aplay`` process is reaped). This is NOT the last
      physical speaker sample — this isolated spike has no hardware-drain
      instrumentation and does not claim that precision.

5 trials. Writes spike_bargein_live_<ts>.json.

Run:  .venv/bin/python docs/research/m2_5_bargein/spike_bargein_live.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

import pyaudio  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
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
N_TRIALS = 5
WARMUP_S = 3.0            # pipeline warm-up before the first trial
PLAYBACK_LEADIN_S = 1.5   # after Popen, wait this long, then confirm aplay is alive
PROMPT_AFTER_LEADIN_S = 0.5  # extra beat between "playback confirmed" and SPEAK NOW
DETECT_TIMEOUT_S = 15.0   # give up waiting for a post-arm VAD start after this
POST_ACCEPT_LISTEN_S = 2.0  # keep counting later VAD starts this long after accept
SPEAKER_PCM = "plug:usb_speaker"


class _VadStartWatcher(FrameProcessor):
    """Timestamps every ``VADUserStartedSpeakingFrame`` /
    ``VADUserStoppedSpeakingFrame`` and, once a trial is armed, performs the
    interruption action (kill the trial ``aplay``) on the FIRST qualifying
    start. Forwards every frame unchanged."""

    def __init__(self, state: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        st = self._state
        if isinstance(frame, VADUserStartedSpeakingFrame):
            now = time.monotonic()
            st["vad_events"].append({"t": now, "kind": "start"})
            armed_at = st.get("armed_at")
            if armed_at is None or now < armed_at:
                st["prearm_vad_count"] += 1          # pre-arm: recorded, never used
            elif st.get("accepted_at") is None:
                st["accepted_at"] = now              # F: first valid post-arm start
                st["stop_requested_at"] = time.monotonic()  # G: request stop now
                proc: subprocess.Popen | None = st.get("proc")
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    st["kill_sent"] = True
            else:
                st["post_accept_vad_count"] += 1     # H: later starts ignored
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            st["vad_events"].append({"t": time.monotonic(), "kind": "stop"})
        await self.push_frame(frame, direction)


def derive_trial_metrics(t: dict) -> dict:
    """Pure: turn one trial's raw timestamps into the reported metrics.

    Kept import-free and side-effect-free so it is unit-tested offline
    (tests/test_bargein_spike.py). ``t`` keys (any may be ``None``):
    ``playback_started`` (bool), ``prompt_printed``, ``trial_armed``,
    ``vad_user_started``, ``playback_stop_requested``,
    ``playback_actually_stopped``, ``prearm_vad_count``,
    ``post_accept_vad_count``.
    """
    def ms(a, b):
        if a is None or b is None:
            return None
        return round((a - b) * 1000.0, 1)

    def s(a, b):
        if a is None or b is None:
            return None
        return round(a - b, 3)

    vad = t.get("vad_user_started")
    detected = vad is not None
    return {
        "detected": detected,
        "playback_started": bool(t.get("playback_started")),
        # INFORMATIONAL ONLY — includes the operator's human reaction time.
        "prompt_to_vad_s": s(vad, t.get("prompt_printed")),
        "arm_to_vad_s": s(vad, t.get("trial_armed")),
        # System control-plane latency: VAD start -> stop requested.
        "vad_to_stop_request_ms": ms(t.get("playback_stop_requested"), vad),
        # Media-stop latency: VAD start -> aplay process reaped.
        # LABEL: PLAYBACK TASK STOPPED (not last physical speaker sample).
        "vad_to_playback_stopped_ms": ms(t.get("playback_actually_stopped"), vad),
        "prearm_vad_count": int(t.get("prearm_vad_count") or 0),
        "post_accept_vad_count": int(t.get("post_accept_vad_count") or 0),
        "prearm_contaminated": int(t.get("prearm_vad_count") or 0) > 0,
    }


def _agg(values: list[float]) -> dict:
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "max": None, "min": None}
    xs_sorted = sorted(xs)
    return {
        "n": len(xs),
        "mean": round(sum(xs) / len(xs), 1),
        "median": round(xs_sorted[len(xs_sorted) // 2], 1),
        "max": round(max(xs), 1),
        "min": round(min(xs), 1),
    }


def _build_pipeline(state: dict) -> tuple[Pipeline, pyaudio.PyAudio]:
    """VAD-only pipeline on the real reSpeaker input, NO audio output — so
    this harness does not hold ``usb_speaker`` and the trial ``aplay`` can
    open it. Uses the same production VAD components as ``VoiceRuntime``."""
    cfg = LocalAudioConfig()
    pa = pyaudio.PyAudio()
    input_index = find_device_index(pa, cfg.input_device_name, require_input=True)
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=False,  # <-- the whole point of v2
            audio_in_sample_rate=cfg.sample_rate,
            audio_in_channels=cfg.channels,
            input_device_index=input_index,
        )
    )
    vad_analyzer = SileroVADAnalyzer(
        sample_rate=cfg.sample_rate, params=DEFAULT_VAD_PARAMS
    )
    pipeline = Pipeline(
        [transport.input(), VADProcessor(vad_analyzer=vad_analyzer),
         _VadStartWatcher(state)]
    )
    return pipeline, pa


async def _trial(n: int, wav_path: Path, state: dict, loop: asyncio.AbstractEventLoop) -> dict:
    # Reset per-trial shared state read/written by the watcher.
    state.update(
        vad_events=[], armed_at=None, accepted_at=None, stop_requested_at=None,
        kill_sent=False, prearm_vad_count=0, post_accept_vad_count=0, proc=None,
    )

    print(f"\n--- trial {n}/{N_TRIALS} ---")
    # A. start playback (stderr CAPTURED, not discarded)
    proc = subprocess.Popen(
        ["aplay", "-q", "-D", SPEAKER_PCM, str(wav_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    state["proc"] = proc
    playback_started_at = time.monotonic()

    # B. wait until playback is definitely active, then confirm it
    await asyncio.sleep(PLAYBACK_LEADIN_S)
    playback_ok = proc.poll() is None
    playback_error = None
    if not playback_ok:
        err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip() if proc.stderr else ""
        playback_error = err or f"aplay exited rc={proc.returncode} within {PLAYBACK_LEADIN_S}s"
        print(f"    !! PLAYBACK DID NOT START: {playback_error}")
        print("    !! (measuring VAD anyway; media-stop latency is N/A this trial)")
    else:
        print(f"    playback confirmed active (aplay pid={proc.pid})")

    # C. discard + count every VAD start seen so far; arm only after this
    await asyncio.sleep(PROMPT_AFTER_LEADIN_S)
    state["prearm_vad_count"] = sum(1 for e in state["vad_events"] if e["kind"] == "start")
    if state["prearm_vad_count"]:
        print(f"    (ignored {state['prearm_vad_count']} pre-arm VAD start(s) — "
              f"NOT used as the interruption)")

    # D. prompt  E. arm
    prompt_printed = time.monotonic()
    print('    >>> SPEAK NOW <<<   (one short phrase, normal volume, then stop)')
    trial_armed = time.monotonic()
    state["armed_at"] = trial_armed

    # F. wait for the first accepted post-arm VAD start (watcher sets accepted_at
    #    and, if playback is live, has already issued proc.kill())
    deadline = trial_armed + DETECT_TIMEOUT_S
    while state["accepted_at"] is None and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    vad_user_started = state["accepted_at"]
    stop_requested_at = state["stop_requested_at"]
    playback_actually_stopped = None

    if vad_user_started is not None:
        # I. wait until the playback process has actually exited, then timestamp
        if state.get("kill_sent"):
            await loop.run_in_executor(None, proc.wait)
            playback_actually_stopped = time.monotonic()
        # H. keep counting later VAD starts for a short window
        await asyncio.sleep(POST_ACCEPT_LISTEN_S)
        print(f"    -> VAD start accepted {round(vad_user_started - trial_armed, 3)}s "
              f"after arming")
        if playback_actually_stopped is not None:
            print(f"    -> PLAYBACK TASK STOPPED "
                  f"{round((playback_actually_stopped - vad_user_started) * 1000, 1)} ms "
                  f"after VAD start")
    else:
        print(f"    -> NO post-arm VAD start within {DETECT_TIMEOUT_S}s")

    # Tidy: make sure the aplay child is gone.
    if proc.poll() is None:
        proc.terminate()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, proc.wait), timeout=2.0)
        except TimeoutError:
            proc.kill()

    raw = {
        "trial": n,
        "playback_started": playback_ok,
        "playback_error": playback_error,
        "t_playback_started": playback_started_at,
        "prompt_printed": prompt_printed,
        "trial_armed": trial_armed,
        "vad_user_started": vad_user_started,
        "playback_stop_requested": stop_requested_at,
        "playback_actually_stopped": playback_actually_stopped,
        "prearm_vad_count": state["prearm_vad_count"],
        "post_accept_vad_count": state["post_accept_vad_count"],
        "vad_events_rel_arm": [
            {"kind": e["kind"], "rel_arm_s": round(e["t"] - trial_armed, 3)}
            for e in state["vad_events"]
        ],
    }
    return {**raw, **derive_trial_metrics(raw)}


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5A SPIKE B-live v2 — operator barge-in control latency — {ts}")
    print("NeXa speaks a ~19 s Polish sentence per trial. WAIT for '>>> SPEAK "
          "NOW <<<', then say ONE short phrase at normal volume and stop.")

    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    wav_bytes = await piper.synthesize(PHRASE, voice=PL_VOICE)
    await piper.stop()

    wav_path = HERE / f"_bargein_{ts}.wav"
    wav_path.write_bytes(wav_bytes)
    with wave.open(str(wav_path), "rb") as w:
        audio_s = w.getnframes() / w.getframerate()
    print(f"  TTS phrase: {audio_s:.1f}s")
    if audio_s < DETECT_TIMEOUT_S + PLAYBACK_LEADIN_S + 3:
        print(f"  NOTE: phrase ({audio_s:.1f}s) is short vs the "
              f"{DETECT_TIMEOUT_S}s detect timeout — reply promptly after the prompt.")

    state: dict = {}
    pipeline, pa = _build_pipeline(state)
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=LocalAudioConfig().sample_rate),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    runner = WorkerRunner()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    loop = asyncio.get_running_loop()
    await asyncio.sleep(WARMUP_S)  # let Silero settle before trial 1

    trials = []
    try:
        for i in range(1, N_TRIALS + 1):
            trials.append(await _trial(i, wav_path, state, loop))
            await asyncio.sleep(2.5)
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
        pa.terminate()
        wav_path.unlink(missing_ok=True)

    detected = [t for t in trials if t["detected"]]
    playback_live = [t for t in trials if t["playback_started"]]
    measurable = [t for t in detected if t["playback_started"]]
    summary = {
        "trials": N_TRIALS,
        "playback_confirmed_active": len(playback_live),
        "post_arm_vad_detected": len(detected),
        "fully_measurable_trials": len(measurable),
        "total_prearm_vad_count": sum(t["prearm_vad_count"] for t in trials),
        "total_post_accept_vad_count": sum(t["post_accept_vad_count"] for t in trials),
        "prearm_contaminated_trials": [t["trial"] for t in trials if t["prearm_contaminated"]],
        # System control-plane latency (VAD start -> stop requested).
        "vad_to_stop_request_ms": _agg([t["vad_to_stop_request_ms"] for t in measurable]),
        # Media-stop latency == PLAYBACK TASK STOPPED (aplay reaped);
        # NOT the last physical speaker sample.
        "vad_to_playback_stopped_ms": _agg([t["vad_to_playback_stopped_ms"] for t in measurable]),
        # INFORMATIONAL ONLY — bundles the operator's human reaction time.
        "prompt_to_vad_s_INFORMATIONAL": _agg([t["prompt_to_vad_s"] for t in detected]),
        "labels": {
            "vad_to_playback_stopped_ms": "PLAYBACK TASK STOPPED (aplay process reaped)",
            "last_physical_speaker_sample": "NOT MEASURED — no hardware-drain "
                                            "instrumentation in this spike",
            "prompt_to_vad_s": "operator reaction time + VAD detection — "
                               "informational, not system latency",
        },
    }
    out = {"ts": ts, "phrase": PHRASE, "audio_s": round(audio_s, 2),
           "params": {"WARMUP_S": WARMUP_S, "PLAYBACK_LEADIN_S": PLAYBACK_LEADIN_S,
                      "DETECT_TIMEOUT_S": DETECT_TIMEOUT_S,
                      "POST_ACCEPT_LISTEN_S": POST_ACCEPT_LISTEN_S},
           "trials_detail": trials, "summary": summary}
    (HERE / f"spike_bargein_live_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print(f"\n-> spike_bargein_live_{ts}.json")
    print("=" * 60)
    print(f"VERDICT ({ts}):")
    print(f"  playback confirmed active : {len(playback_live)}/{N_TRIALS} trials")
    print(f"  post-arm VAD detected     : {len(detected)}/{N_TRIALS} trials")
    print(f"  fully measurable          : {len(measurable)}/{N_TRIALS} trials")
    print(f"  pre-arm VAD starts (total): {summary['total_prearm_vad_count']}"
          f"  contaminated trials: {summary['prearm_contaminated_trials'] or 'none'}")
    print(f"  post-accept VAD starts    : {summary['total_post_accept_vad_count']}")
    c = summary["vad_to_stop_request_ms"]
    m = summary["vad_to_playback_stopped_ms"]
    p = summary["prompt_to_vad_s_INFORMATIONAL"]
    print(f"  VAD start -> stop requested (CONTROL) : "
          f"mean={c['mean']} median={c['median']} max={c['max']} ms  (n={c['n']})")
    print(f"  VAD start -> PLAYBACK TASK STOPPED    : "
          f"mean={m['mean']} median={m['median']} max={m['max']} ms  (n={m['n']})")
    print(f"  prompt -> VAD  [INFORMATIONAL, incl. human reaction] : "
          f"mean={p['mean']} median={p['median']} max={p['max']} s  (n={p['n']})")
    print("  LAST PHYSICAL SPEAKER SAMPLE: not measured by this spike.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
