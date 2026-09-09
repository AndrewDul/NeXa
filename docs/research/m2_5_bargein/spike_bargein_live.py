"""M2.5A — SPIKE B-live: operator barge-in latency (the confirmatory test).

RESEARCH SPIKE ONLY (disposable). Needs the operator in the room.

The headless variant (``spike_interruption_latency.py``) could NOT measure
barge-in latency: any signal routed speaker -> mic is on the exact path
the reSpeaker XVF3800 rejects (SPIKE A / headless SPIKE B both = 0
detections). A person speaking near-field at the array is a different,
much stronger signal, so the latency must be measured with the operator.

Per trial this harness:
  1. runs a VAD-only ``VoiceRuntime`` on the ``respeaker`` mic,
  2. plays ~18 s of NeXa's own Polish Piper speech to ``plug:usb_speaker``
     (NeXa is "speaking"),
  3. prints ``>>> SPEAK NOW <<<`` ~2 s into playback,
  4. on the FIRST ``USER_SPEAKING`` transition, immediately kills the
     ``aplay`` process — this stands in for the M2.5B interruption path
     stopping the old TTS,
  5. records: playback start, prompt time, VAD-onset time, playback-kill
     time. Barge-in latency = kill - onset is ~0 (same callback); the
     meaningful number is onset - (operator's spoken start). Since we
     cannot timestamp the operator's mouth, we report onset - prompt as an
     upper bound and onset - playback_start for the record.

5 trials. Writes spike_bargein_live_<ts>.json.

Run:  .venv/bin/python docs/research/m2_5_bargein/spike_bargein_live.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402
from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402

PHRASE = (
    "Wszechświat rozszerza się od około trzynastu miliardów ośmiuset "
    "milionów lat, a tempo tego rozszerzania opisuje stała Hubble'a. "
    "Galaktyki oddalają się od siebie tym szybciej, im większa jest "
    "dzieląca je odległość, co odkrył Edwin Hubble w tysiąc dziewięćset "
    "dwudziestym dziewiątym roku, obserwując przesunięcie ku czerwieni."
)
PROMPT_DELAY_S = 2.0
N_TRIALS = 5


async def _trial(n: int, wav_path: Path, audio_s: float) -> dict:
    onset: dict[str, float | None] = {"t": None}
    killed: dict[str, float | None] = {"t": None}
    t0: dict[str, float | None] = {"v": None}
    proc_box: dict[str, subprocess.Popen | None] = {"p": None}
    events: list[dict] = []

    def on_event(ev: VoiceEvent) -> None:
        now = time.monotonic()
        rel = None if t0["v"] is None else round(now - t0["v"], 3)
        events.append({"to": ev.to_state.value, "rel_play_s": rel})
        if ev.to_state == VoiceState.USER_SPEAKING and onset["t"] is None:
            onset["t"] = now
            p = proc_box["p"]
            if p is not None and p.poll() is None:
                p.kill()
                killed["t"] = time.monotonic()

    runtime = VoiceRuntime(LocalAudioConfig(), on_event=on_event)
    run_task = asyncio.create_task(runtime.run())
    await asyncio.sleep(3.0)

    print(f"\n--- trial {n}/{N_TRIALS} --- NeXa starts speaking now, "
          f"wait for the prompt, then say a short phrase.")
    t0["v"] = time.monotonic()
    proc_box["p"] = subprocess.Popen(
        ["aplay", "-q", "-D", "plug:usb_speaker", str(wav_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    await asyncio.sleep(PROMPT_DELAY_S)
    prompt_t = time.monotonic()
    print("    >>> SPEAK NOW <<<   (e.g.  \"Stop.\"  /  \"Czekaj.\"  /  "
          "\"Actually, tell me about black holes instead.\")")

    await asyncio.sleep(audio_s + 4.0)
    p = proc_box["p"]
    if p is not None and p.poll() is None:
        p.terminate()

    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass

    onset_rel = None if onset["t"] is None else round(onset["t"] - t0["v"], 3)
    onset_after_prompt = None if onset["t"] is None else round(onset["t"] - prompt_t, 3)
    stop_after_onset = (
        None if (onset["t"] is None or killed["t"] is None)
        else round(killed["t"] - onset["t"], 3)
    )
    detected = onset["t"] is not None
    print(f"    -> detected={detected}  onset {onset_rel}s after playback "
          f"({onset_after_prompt}s after prompt); playback killed "
          f"{stop_after_onset}s after VAD onset")
    return {
        "trial": n,
        "detected": detected,
        "onset_after_playback_start_s": onset_rel,
        "onset_after_prompt_s": onset_after_prompt,
        "playback_stop_after_onset_s": stop_after_onset,
        "events": events,
    }


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5A SPIKE B-live — operator barge-in latency — {ts}")
    print("Keep normal speaking volume. One short phrase per trial, only "
          "after the SPEAK NOW prompt.")

    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    wav_bytes = await piper.synthesize(PHRASE, voice=PL_VOICE)
    await piper.stop()

    wav_path = HERE / f"_bargein_{ts}.wav"
    wav_path.write_bytes(wav_bytes)
    import wave

    with wave.open(str(wav_path), "rb") as w:
        audio_s = w.getnframes() / w.getframerate()
    print(f"  TTS phrase: {audio_s:.1f}s")

    trials = []
    for i in range(1, N_TRIALS + 1):
        trials.append(await _trial(i, wav_path, audio_s))
        await asyncio.sleep(2.5)

    wav_path.unlink(missing_ok=True)

    det = [t for t in trials if t["detected"]]
    onsets = [t["onset_after_prompt_s"] for t in det
              if t["onset_after_prompt_s"] is not None]
    summary = {
        "trials": N_TRIALS,
        "detected": len(det),
        "onset_after_prompt_s": {
            "mean": round(sum(onsets) / len(onsets), 3) if onsets else None,
            "median": round(sorted(onsets)[len(onsets) // 2], 3) if onsets else None,
            "max": round(max(onsets), 3) if onsets else None,
            "min": round(min(onsets), 3) if onsets else None,
        },
        "note": "onset_after_prompt is an upper bound on VAD detection latency "
                "(includes the operator's own reaction time to the prompt).",
    }
    out = {"ts": ts, "phrase": PHRASE, "prompt_delay_s": PROMPT_DELAY_S,
           "trials_detail": trials, "summary": summary}
    (HERE / f"spike_bargein_live_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print(f"\n-> spike_bargein_live_{ts}.json")
    print(f"VERDICT: detected {len(det)}/{N_TRIALS}; onset-after-prompt "
          f"mean={summary['onset_after_prompt_s']['mean']}s "
          f"median={summary['onset_after_prompt_s']['median']}s "
          f"max={summary['onset_after_prompt_s']['max']}s")


if __name__ == "__main__":
    asyncio.run(main())
