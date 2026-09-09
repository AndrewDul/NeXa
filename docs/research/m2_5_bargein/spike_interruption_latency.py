"""M2.5A — SPIKE B (headless): interruption-detection latency.

RESEARCH SPIKE ONLY (disposable). This is the *headless* approximation of
the operator barge-in test: instead of a person speaking into the room
while NeXa talks, it plays a single pre-mixed WAV through the configured
speaker (``plug:usb_speaker``) that contains

    NeXa's Piper TTS  +  a real recorded operator utterance overlaid
    starting at ``OVERLAY_START_S`` seconds

and measures, on the reSpeaker mic + Silero VAD, how long after the
overlaid human speech begins the runtime first reports
``USER_SPEAKING`` — i.e. the ``speech onset -> VAD accepts`` term that
dominates real interruption latency.

It does NOT measure the ``VAD -> old audio actually stops`` tail; in the
recommended M2.5B architecture that tail is Pipecat
``base_output.handle_interruptions()`` cancelling + recreating the audio
task, bounded by one output chunk (~20-40 ms) plus task-cancel latency.
The report adds that as a constant.

The true acoustic operator test (a person, a live room) stays SPIKE B-live
and is prepared as a single command for the operator.

Writes spike_interruption_latency_<ts>.json.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))
sys.path.insert(0, str(HERE))

from audio_mix import mix_overlay, read_wav_i16, write_wav_i16  # noqa: E402

from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402
from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402

TTS_PHRASE = (
    "Czarna dziura to obszar czasoprzestrzeni o tak ogromnej grawitacji, "
    "że nic, nawet światło, nie jest w stanie się z niej wydostać. "
    "Powstaje, gdy bardzo masywna gwiazda zapada się pod własnym ciężarem, "
    "a jej jądro nie może już dłużej opierać się grawitacyjnemu zapadaniu."
)
# a real recorded operator utterance = the "interruption"
INTERRUPT_WAV = (
    HERE.parents[0]
    / "m2_voice_spikes"
    / "asr_test_samples"
    / "en_what_is_the_speed_of_light.wav"
)
OVERLAY_START_S = 2.0
INTERRUPT_GAIN = 3.0  # overlay boosted +9.5 dB to probe the VAD-through-speaker floor
N_RUNS = 3


def _resample_i16(path_in: Path, path_out: Path, target_sr: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path_in),
         "-ar", str(target_sr), "-ac", "1", str(path_out)],
        check=True,
    )


async def _one_run(mixed_wav: Path, total_s: float) -> dict:
    first_speaking: dict[str, float | None] = {"t": None}
    t_ref: dict[str, float | None] = {"v": None}
    events: list[dict] = []

    def on_event(ev: VoiceEvent) -> None:
        now = time.monotonic()
        rel = None if t_ref["v"] is None else round(now - t_ref["v"], 3)
        events.append({"to": ev.to_state.value, "rel_play_s": rel})
        if ev.to_state == VoiceState.USER_SPEAKING and first_speaking["t"] is None:
            first_speaking["t"] = now

    runtime = VoiceRuntime(LocalAudioConfig(), on_event=on_event)
    run_task = asyncio.create_task(runtime.run())
    await asyncio.sleep(3.0)  # settle / room baseline

    t_ref["v"] = time.monotonic()
    proc = subprocess.Popen(
        ["aplay", "-q", "-D", "plug:usb_speaker", str(mixed_wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    await asyncio.sleep(total_s + 3.0)
    if proc.poll() is None:
        proc.terminate()

    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass

    onset_rel = (
        None if first_speaking["t"] is None
        else round(first_speaking["t"] - t_ref["v"], 3)
    )
    # latency from the interruption speech beginning to VAD accepting it
    detect_latency = None if onset_rel is None else round(onset_rel - OVERLAY_START_S, 3)
    return {
        "user_speaking_onset_rel_play_s": onset_rel,
        "interrupt_detect_latency_s": detect_latency,
        "detected": onset_rel is not None and detect_latency is not None and detect_latency > -0.5,
        "events": events,
    }


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5A SPIKE B (headless) interruption-detection latency — {ts}")

    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    tts_bytes = await piper.synthesize(TTS_PHRASE, voice=PL_VOICE)
    await piper.stop()

    tts_wav = HERE / f"_b_tts_{ts}.wav"
    tts_wav.write_bytes(tts_bytes)
    base, sr = read_wav_i16(tts_wav)
    total_s = base.size / sr
    print(f"  TTS: {total_s:.1f}s @ {sr}Hz")

    rs_wav = HERE / f"_b_interrupt_{sr}_{ts}.wav"
    _resample_i16(INTERRUPT_WAV, rs_wav, sr)
    overlay, osr = read_wav_i16(rs_wav)
    assert osr == sr
    print(f"  interrupt clip: {overlay.size / sr:.1f}s ({INTERRUPT_WAV.name})")

    mixed = mix_overlay(base, overlay, sr, OVERLAY_START_S, INTERRUPT_GAIN)
    mixed_wav = HERE / f"_b_mixed_{ts}.wav"
    write_wav_i16(mixed_wav, mixed, sr)
    mixed_total_s = mixed.size / sr

    runs = []
    for i in range(N_RUNS):
        r = await _one_run(mixed_wav, mixed_total_s)
        runs.append(r)
        print(f"  run {i + 1}/{N_RUNS}: onset@{r['user_speaking_onset_rel_play_s']}s "
              f"-> detect latency {r['interrupt_detect_latency_s']}s "
              f"detected={r['detected']}")
        await asyncio.sleep(2.0)

    for p in (tts_wav, rs_wav, mixed_wav):
        p.unlink(missing_ok=True)

    lat = [r["interrupt_detect_latency_s"] for r in runs if r["detected"]]
    summary = {
        "runs": N_RUNS,
        "detected_count": len(lat),
        "detect_latency_s": {
            "mean": round(float(np.mean(lat)), 3) if lat else None,
            "median": round(float(np.median(lat)), 3) if lat else None,
            "max": round(float(np.max(lat)), 3) if lat else None,
            "min": round(float(np.min(lat)), 3) if lat else None,
        },
    }
    out = {
        "ts": ts,
        "tts_phrase": TTS_PHRASE,
        "interrupt_clip": INTERRUPT_WAV.name,
        "overlay_start_s": OVERLAY_START_S,
        "interrupt_gain": INTERRUPT_GAIN,
        "note": "headless: pre-mixed TTS+recorded-utterance played to plug:usb_speaker; "
                "measures speech-onset -> VAD USER_SPEAKING only, not the audio-stop tail",
        "runs_detail": runs,
        "summary": summary,
    }
    (HERE / f"spike_interruption_latency_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print(f"\n-> spike_interruption_latency_{ts}.json")
    print(f"VERDICT: detected {len(lat)}/{N_RUNS} runs; "
          f"detect latency mean={summary['detect_latency_s']['mean']}s "
          f"median={summary['detect_latency_s']['median']}s "
          f"max={summary['detect_latency_s']['max']}s")


if __name__ == "__main__":
    asyncio.run(main())
