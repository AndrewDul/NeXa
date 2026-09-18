#!/usr/bin/env python3
"""R0082-D -- ONE-TIME generation of the frozen speech stimulus WAV.

**Run this script exactly once, in TWO stages, each with a DIFFERENT
already-existing Python environment -- neither is modified by this
script:**

    stage 1 (Piper synthesis; needs `piper-tts` + its own `numpy`):
        /home/devdul/.local/share/nexa/tts/piper-http-venv/bin/python3 \\
            r0082d_generate_speech_stimulus.py --stage synthesize

    stage 2 (resample to the canonical 48kHz research WAV; needs numpy
    +scipy, both already present in plain system python3 on this
    machine, confirmed R0082-C):
        python3 r0082d_generate_speech_stimulus.py --stage resample

This two-stage split exists ONLY because the dedicated external Piper
venv (below) does not have `scipy` installed, and installing it there
would modify an existing NeXa infrastructure venv for a one-off research
need -- avoided entirely by doing the resample step in plain system
python3 instead, which already has both numpy and scipy. No environment
is modified by this script.

Its output (`r0082d_speech_stimulus/r0082d_speech_en_pl_v1.wav`) is the
immutable test asset every R0082-D OFF/ON run reuses byte-for-byte --
this script is NOT part of the recurring R0082-D test harness and is
not invoked at run time.

## Why Piper, and why the EXTERNAL piper venv specifically

R0082-D needs ~20-30s of real speech (voiced/unvoiced sounds, plosives,
sibilants, vowels, natural sentence rhythm and pauses) to test whether
PlatformAudio's WebRTC AEC suppresses a real self-playback signal, not
just a synthetic stationary multitone. Per instruction, no NEW TTS
system was installed and no cloud/Gemini TTS was used -- this audits
and reuses NeXa's own ALREADY-INSTALLED local Piper voices exactly as
configured in ``src/nexa/tts/config.py`` (``EN_VOICE =
"en_GB-jenny_dioco-medium"``, ``PL_VOICE = "pl_PL-gosia-medium"``, both
confirmed present as real ``.onnx``/``.onnx.json`` files under
``~/.local/share/nexa/tts/voices/``, native rate 22050Hz per each
voice's own config JSON).

This script does **NOT** import ``nexa.tts`` (or anything else
`nexa.*`) -- consistent with every other R0082 script's isolation
discipline -- and instead references the voice model files directly by
the SAME paths ``nexa.tts.config`` itself resolves to.

**A real, out-of-scope finding, not acted on here:** ``nexa.tts.config``'s
own docstring states `piper-tts` must be installed in the external venv
"deliberately NOT NeXa's own `.venv`, so the GPL-3.0-licensed `piper-tts`
package is never imported into NeXa's own process" -- but this round's
audit found `piper-tts` 1.8.0 IS ALSO currently importable from NeXa's
own `.venv` (`.venv/lib/python3.13/site-packages/piper/__init__.py`
exists). This appears to contradict that documented licensing
discipline. Recorded here as an observed fact for a future round to
investigate -- this script deliberately still uses ONLY the intended
external venv for synthesis, and no fix to NeXa's own `.venv` is
attempted (out of scope for R0082).

## What this generates

Two Piper syntheses (EN then PL, using each voice's own natural
prosody/pauses from punctuation -- no extra per-word tuning), with one
deliberate ~0.6s silence gap between them, concatenated, then resampled
ONCE (polyphase, `scipy.signal.resample_poly`, exact integer ratio
320/147 = 48000/22050 after reducing by gcd) to the canonical research
format: mono, 16-bit (S16_LE), 48000Hz. The result is written once and
frozen -- its SHA256 is printed and must never change.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import wave
from pathlib import Path

# Exactly the paths `nexa.tts.config` itself resolves to -- referenced
# directly, NOT via `import nexa.tts` (isolation discipline, see
# module docstring above).
VOICES_DIR = Path.home() / ".local" / "share" / "nexa" / "tts" / "voices"
EN_VOICE = "en_GB-jenny_dioco-medium"
PL_VOICE = "pl_PL-gosia-medium"

OUT_DIR = Path(__file__).resolve().parent / "r0082d_speech_stimulus"
OUT_WAV = OUT_DIR / "r0082d_speech_en_pl_v1.wav"
EN_TMP = OUT_DIR / "_tmp_en.wav"
PL_TMP = OUT_DIR / "_tmp_pl.wav"

EN_TEXT = (
    "Good afternoon. This is a fixed test recording, used only to measure "
    "microphone echo. The quick brown fox jumps over the lazy dog, "
    "testing plosive stops like pat, bat, kite, and goat."
)
PL_TEXT = (
    "Dzien dobry. To jest stale nagranie testowe, uzywane wylacznie do "
    "pomiaru echa mikrofonu. Szybki lis przeskakuje nad leniwym psem, "
    "testujac szumiace i zwarte spolgloski."
)

SILENCE_GAP_S = 0.6


def stage_synthesize() -> None:
    from piper import PiperVoice

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def synth(voice_name: str, text: str, out_path: Path) -> None:
        model_path = VOICES_DIR / f"{voice_name}.onnx"
        config_path = VOICES_DIR / f"{voice_name}.onnx.json"
        if not model_path.exists() or not config_path.exists():
            raise SystemExit(f"Voice model not found: {model_path} / {config_path}")
        voice = PiperVoice.load(model_path, config_path=config_path)
        with wave.open(str(out_path), "wb") as wf:
            voice.synthesize_wav(text, wf)

    print("Synthesizing EN segment...")
    synth(EN_VOICE, EN_TEXT, EN_TMP)
    print("Synthesizing PL segment...")
    synth(PL_VOICE, PL_TEXT, PL_TMP)
    print(f"Wrote {EN_TMP} and {PL_TMP} -- now run --stage resample with plain python3.")


def stage_resample() -> None:
    import numpy as np
    from scipy.signal import resample_poly

    if not EN_TMP.exists() or not PL_TMP.exists():
        raise SystemExit(
            f"{EN_TMP} / {PL_TMP} not found -- run --stage synthesize first, with "
            "the external Piper venv's python3."
        )

    def read_pcm(path: Path) -> tuple[np.ndarray, int]:
        with wave.open(str(path), "rb") as wf:
            assert wf.getnchannels() == 1, path
            assert wf.getsampwidth() == 2, path
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        return np.frombuffer(raw, dtype="<i2"), sr

    en_pcm, en_sr = read_pcm(EN_TMP)
    pl_pcm, pl_sr = read_pcm(PL_TMP)
    assert en_sr == pl_sr, f"voice sample rates differ: {en_sr} vs {pl_sr}"
    native_sr = en_sr
    print(f"Native Piper sample rate: {native_sr}Hz")

    gap = np.zeros(int(SILENCE_GAP_S * native_sr), dtype="<i2")
    combined = np.concatenate([en_pcm, gap, pl_pcm])

    g = math.gcd(48000, native_sr)
    up, down = 48000 // g, native_sr // g
    print(f"Resampling {native_sr}Hz -> 48000Hz via polyphase up={up} down={down}")
    resampled = resample_poly(combined.astype(np.float64), up, down)

    peak_before = np.max(np.abs(resampled))
    if peak_before > 32767:
        scale = 32760.0 / peak_before
        print(
            f"WARNING: resampled peak {peak_before:.1f} exceeds int16 range -- "
            f"applying a ONE-TIME safety scale of {scale:.4f} to avoid clipping "
            "(documented, not a per-run normalization)."
        )
        resampled = resampled * scale

    final_pcm = np.clip(np.round(resampled), -32768, 32767).astype("<i2")

    with wave.open(str(OUT_WAV), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(final_pcm.tobytes())

    EN_TMP.unlink()
    PL_TMP.unlink()

    n = len(final_pcm)
    rms = float(np.sqrt(np.mean(final_pcm.astype(np.float64) ** 2)))
    peak = int(np.max(np.abs(final_pcm)))
    duration_s = n / 48000

    print(f"\nWrote {OUT_WAV}")
    print(f"  n_samples={n}  duration={duration_s:.3f}s  rms={rms:.2f}  peak={peak}")
    print(f"  clipping (peak>=32767): {peak >= 32767}")

    digest = hashlib.sha256(OUT_WAV.read_bytes()).hexdigest()
    print(f"  sha256={digest}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["synthesize", "resample"], required=True)
    args = p.parse_args()
    if args.stage == "synthesize":
        stage_synthesize()
    else:
        stage_resample()


if __name__ == "__main__":
    main()
