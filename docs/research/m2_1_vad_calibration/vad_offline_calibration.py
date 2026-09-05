"""Deterministic offline Silero VAD calibration (M2.1 endpointing retune).

Uses the real production `pipecat.audio.vad.silero.SileroVADAnalyzer` (the
same class `nexa.voice.runtime.VoiceRuntime` wires into the live pipeline)
against real recorded Polish speech + real silence (the actual reSpeaker
room-noise floor extracted from the same recordings, not digital zeros),
with precisely inserted silence gaps of known duration.

No wall-clock timing, no human pause-length imprecision, no live microphone
needed: `VADAnalyzer`'s start_secs/stop_secs confirmation logic is purely
audio-frame-count-based (verified by reading
`pipecat/audio/vad/vad_analyzer.py` directly — `_vad_stop_frames =
round(stop_secs / (512/sample_rate))`), so this is a faithful, fast,
reproducible test of the exact same logic the live hardware uses, without
needing to reproduce it over a live microphone every time.
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

REPO_ROOT = Path("/home/devdul/Projects/NeXa_IkiGai")
sys.path.insert(0, str(REPO_ROOT / "src"))

FIXTURES = REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"
SR = 16000
FRAME_LEN = 512  # Silero's own required frame size at 16 kHz

GAPS_S = [0.4, 0.6, 0.8, 1.0, 1.2]
STOP_SECS_CANDIDATES = [0.2, 0.6, 0.8, 1.0]


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


async def run_one(clip: np.ndarray, stop_secs: float, gap_start_sample: int, speech_b_start_sample: int) -> dict:
    analyzer = SileroVADAnalyzer(
        sample_rate=SR,
        params=VADParams(start_secs=0.2, stop_secs=stop_secs, confidence=0.7, min_volume=0.6),
    )
    # Mirrors VADController.setup() in the real pipeline
    # (pipecat/audio/vad/vad_controller.py) — __init__ alone does not compute
    # the frame-count thresholds; set_sample_rate() does.
    analyzer.set_sample_rate(SR)

    confirmed_stop_sample = None
    state_before = VADState.QUIET

    for i in range(0, len(clip) - FRAME_LEN, FRAME_LEN):
        chunk = clip[i : i + FRAME_LEN].tobytes()
        state = await analyzer.analyze_audio(chunk)
        # SPEAKING -> STOPPING -> QUIET (and QUIET -> STARTING -> SPEAKING):
        # analyze_audio can return the intermediate STOPPING/STARTING states
        # too, so "is QUIET now and wasn't QUIET before" is the real edge,
        # not a direct SPEAKING -> QUIET jump.
        if state == VADState.QUIET and state_before != VADState.QUIET:
            if confirmed_stop_sample is None and i >= gap_start_sample:
                confirmed_stop_sample = i
        state_before = state

    stopped_during_gap = confirmed_stop_sample is not None and confirmed_stop_sample < speech_b_start_sample
    stop_latency_s = (confirmed_stop_sample - gap_start_sample) / SR if confirmed_stop_sample is not None else None
    return {"stopped_during_gap": stopped_during_gap, "stop_latency_s": stop_latency_s}


async def main() -> None:
    a = load_wav(FIXTURES / "pl_wyjaśnij_grawitację.wav")
    b = load_wav(FIXTURES / "pl_co_to_są_kolory.wav")

    # Real speech segments (found via RMS inspection) and real trailing
    # silence (the actual reSpeaker room-noise floor) — not synthetic zeros.
    speech_a = a[8192:30720]
    real_silence = a[33280:]  # ~1.46s of real captured silence
    speech_b = b[8192:26624]

    results = []
    for stop_secs in STOP_SECS_CANDIDATES:
        for gap_s in GAPS_S:
            gap_samples = int(gap_s * SR)
            clip = np.concatenate(
                [speech_a, real_silence[:gap_samples], speech_b, real_silence[: int(2.0 * SR)]]
            )
            gap_start_sample = len(speech_a)
            speech_b_start_sample = len(speech_a) + gap_samples

            outcome = await run_one(clip, stop_secs, gap_start_sample, speech_b_start_sample)
            row = {"stop_secs": stop_secs, "gap_s": gap_s, **outcome}
            results.append(row)
            print(
                f"stop_secs={stop_secs:>4} gap_s={gap_s:>4} "
                f"{'SPLIT' if row['stopped_during_gap'] else 'held as one':>12} "
                f"latency={row['stop_latency_s']}"
            )

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vad_calibration_results.json")
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} rows to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
