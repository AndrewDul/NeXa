"""M2.5A — SPIKE A: self-echo. Does NeXa's own Piper voice, played through
the configured speaker, trigger the reSpeaker's VAD (a false barge-in)?

RESEARCH SPIKE ONLY (disposable). Not a TV test — NeXa speaker → NeXa mic
only. No operator interaction: the machine observes VAD.

For each variant it:
  1. starts a VAD-only ``VoiceRuntime`` on the ``respeaker`` mic (no STT,
     no LLM, no TTS pipeline — just VAD → ``VoiceState`` events),
  2. synthesizes ~6 s of Polish Piper speech,
  3. plays it (``aplay``) to the chosen output device(s),
  4. records every ``USER_SPEAKING`` transition and its timing relative to
     playback start/stop.

Variants:
  A1  play to plug:usb_speaker (the current NeXa output — Jieli USB DAC).
      The reSpeaker XVF3800's on-chip AEC gets NO reference signal.
  A2  play to plug:usb_speaker AND simultaneously write the same PCM to
      plug:respeaker (the XVF3800's USB *playback* endpoint = its AEC
      far-end reference). Best-effort time alignment (both aplay started
      together). Tests whether feeding the hardware AEC its reference
      suppresses the self-echo.
  BASE  no playback at all — a control for room-noise false positives.

Writes spike_self_echo_<ts>.json.
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

from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402
from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402

PHRASE = (
    "Czarna dziura to obszar czasoprzestrzeni o tak ogromnej grawitacji, "
    "że nic, nawet światło, nie jest w stanie się z niej wydostać. "
    "Powstaje, gdy bardzo masywna gwiazda zapada się pod własnym ciężarem."
)


def _play(devices: list[str], wav_path: Path) -> list[subprocess.Popen]:
    procs = []
    for dev in devices:
        procs.append(subprocess.Popen(
            ["aplay", "-q", "-D", dev, str(wav_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
    return procs


async def run_variant(name: str, wav_path: Path, play_devices: list[str],
                      audio_s: float) -> dict:
    events: list[dict] = []
    speaking_starts: list[float] = []
    t_ref = {"v": None}

    def on_event(ev: VoiceEvent) -> None:
        now = time.monotonic()
        rel = None if t_ref["v"] is None else round(now - t_ref["v"], 3)
        events.append({"to": ev.to_state.value, "at_rel_playback_s": rel})
        if ev.to_state == VoiceState.USER_SPEAKING:
            speaking_starts.append(now)

    runtime = VoiceRuntime(LocalAudioConfig(), on_event=on_event)  # VAD only
    run_task = asyncio.create_task(runtime.run())
    await asyncio.sleep(3.0)  # let the pipeline settle / room baseline

    if play_devices:
        t_ref["v"] = time.monotonic()
        procs = _play(play_devices, wav_path)
        # wait for playback + a tail window for delayed VAD
        await asyncio.sleep(audio_s + 3.0)
        for p in procs:
            if p.poll() is None:
                p.terminate()
    else:
        t_ref["v"] = time.monotonic()
        await asyncio.sleep(audio_s + 3.0)

    # stop the runtime
    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass

    n_during = sum(1 for t in speaking_starts
                   if t_ref["v"] <= t <= t_ref["v"] + audio_s + 0.5)
    return {
        "variant": name,
        "play_devices": play_devices,
        "playback_audio_s": round(audio_s, 2),
        "user_speaking_events": len(speaking_starts),
        "user_speaking_during_playback": n_during,
        "self_echo_false_barge_in": n_during > 0,
        "events": events,
    }


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5A self-echo spike — {ts}\n"
          f"  mic = respeaker ; testing whether NeXa's own voice trips VAD")

    piper = PiperHttpServer(PiperHttpConfig(nice=10))
    await piper.start()
    await piper.prewarm()
    wav_bytes = await piper.synthesize(PHRASE, voice=PL_VOICE)
    await piper.stop()

    wav_path = HERE / f"_self_echo_{ts}.wav"
    wav_path.write_bytes(wav_bytes)
    with wave.open(str(wav_path), "rb") as w:
        audio_s = w.getnframes() / w.getframerate()
        print(f"  synthesized {audio_s:.1f}s @ {w.getframerate()}Hz {w.getnchannels()}ch")

    results = []
    results.append(await run_variant("BASE_no_playback", wav_path, [], audio_s))
    print(f"  BASE (no playback): USER_SPEAKING events = "
          f"{results[-1]['user_speaking_events']}")
    await asyncio.sleep(2.0)

    results.append(await run_variant("A1_usb_speaker_only", wav_path,
                                     ["plug:usb_speaker"], audio_s))
    r = results[-1]
    print(f"  A1 (usb_speaker, NO AEC ref): during-playback USER_SPEAKING = "
          f"{r['user_speaking_during_playback']}  -> self-echo false barge-in: "
          f"{r['self_echo_false_barge_in']}")
    await asyncio.sleep(2.0)

    results.append(await run_variant("A2_with_xvf3800_aec_reference", wav_path,
                                     ["plug:usb_speaker", "plug:respeaker"], audio_s))
    r = results[-1]
    print(f"  A2 (usb_speaker + reSpeaker playback as XVF3800 AEC ref): "
          f"during-playback USER_SPEAKING = {r['user_speaking_during_playback']} "
          f" -> self-echo false barge-in: {r['self_echo_false_barge_in']}")

    wav_path.unlink(missing_ok=True)
    out = {"ts": ts, "phrase": PHRASE, "audio_s": round(audio_s, 2),
           "results": results}
    (HERE / f"spike_self_echo_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n-> spike_self_echo_{ts}.json")
    a1 = results[1]["self_echo_false_barge_in"]
    a2 = results[2]["self_echo_false_barge_in"]
    print("\nVERDICT:")
    print(f"  current routing (A1): self-echo trips VAD = {a1}")
    print(f"  XVF3800 AEC reference fed (A2): self-echo trips VAD = {a2}")


if __name__ == "__main__":
    asyncio.run(main())
