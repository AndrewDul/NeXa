#!/usr/bin/env python3
"""M2.6B.4A — offline benchmark of the EXISTING ``WhisperCppLanguageDetector``
(base/q8_0, the same model local voice already uses) against the real PL/EN
audio fixtures already in the repo (used again from R0038).

No Gemini call, no hardware. Answers, with real numbers: how much does a
"hold activityEnd until local LID completes" gate (ADR-0004 Decision F,
Option B) actually cost using the tool already accepted for local voice,
and how much speech is needed for it to reliably distinguish PL from EN?

Usage::

    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt import WhisperCppLanguageDetector  # noqa: E402

FIXTURES = REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"
PAIRS = {
    "speed_of_light": (
        FIXTURES / "en_what_is_the_speed_of_light.wav",
        FIXTURES / "pl_jaka_jest_prędkość_światła.wav",
    ),
    "colors": (
        FIXTURES / "en_what_are_colors.wav",
        FIXTURES / "pl_co_to_są_kolory.wav",
    ),
    "gravity": (
        FIXTURES / "en_explain_gravity.wav",
        FIXTURES / "pl_wyjaśnij_grawitację.wav",
    ),
}


def _load(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


def _classify(p_pl: float, p_en: float) -> str:
    return "pl" if p_pl >= p_en else "en"


async def main() -> None:
    detector = WhisperCppLanguageDetector()
    results: dict = {"full_utterance": [], "truncated": [], "cold_start_ms": None}

    loaded = {name: {"en": _load(en), "pl": _load(pl)} for name, (en, pl) in PAIRS.items()}

    # cold start (first call ever this process)
    first_pcm, _ = loaded["speed_of_light"]["en"]
    t0 = time.monotonic()
    r = await detector.detect(first_pcm)
    results["cold_start_ms"] = round(1000 * (time.monotonic() - t0), 1)

    print(f"cold start (first call, full utterance): {results['cold_start_ms']} ms")
    print()
    print("=== warm invocations, full utterance ===")
    for name, langs in loaded.items():
        for lang, (pcm, sr) in langs.items():
            duration_s = len(pcm) / 2 / sr
            t0 = time.monotonic()
            r = await detector.detect(pcm)
            elapsed_ms = round(1000 * (time.monotonic() - t0), 1)
            got = _classify(r.p_pl, r.p_en)
            row = {
                "fixture": name, "expected": lang, "duration_s": round(duration_s, 2),
                "elapsed_ms": elapsed_ms, "p_pl": round(r.p_pl, 4), "p_en": round(r.p_en, 4),
                "raw_language": r.raw_language, "got": got, "correct": got == lang,
            }
            results["full_utterance"].append(row)
            print(
                f"{name}/{lang}: dur={row['duration_s']}s {elapsed_ms}ms "
                f"p_pl={row['p_pl']} p_en={row['p_en']} raw={row['raw_language']} "
                f"-> {got} ({'OK' if row['correct'] else 'MISMATCH'})"
            )

    print()
    print("=== truncated durations (speed_of_light pair) ===")
    en_pcm, sr = loaded["speed_of_light"]["en"]
    pl_pcm, _ = loaded["speed_of_light"]["pl"]
    for dur_s in (0.5, 1.0, 1.5, 2.0):
        for lang, pcm in (("en", en_pcm), ("pl", pl_pcm)):
            n_bytes = int(dur_s * sr) * 2
            truncated = pcm[:n_bytes]
            if len(truncated) < 1000:
                continue
            t0 = time.monotonic()
            r = await detector.detect(truncated)
            elapsed_ms = round(1000 * (time.monotonic() - t0), 1)
            got = _classify(r.p_pl, r.p_en)
            row = {
                "truncated_to_s": dur_s, "expected": lang, "elapsed_ms": elapsed_ms,
                "p_pl": round(r.p_pl, 4), "p_en": round(r.p_en, 4), "got": got,
                "correct": got == lang, "margin": round(abs(r.p_pl - r.p_en), 4),
            }
            results["truncated"].append(row)
            print(
                f"{lang} @ {dur_s}s: {elapsed_ms}ms p_pl={row['p_pl']} p_en={row['p_en']} "
                f"margin={row['margin']} -> {got} ({'OK' if row['correct'] else 'MISMATCH'})"
            )

    full_ms = [r["elapsed_ms"] for r in results["full_utterance"]]
    trunc_ms = [r["elapsed_ms"] for r in results["truncated"]]
    all_ms = full_ms + trunc_ms
    results["summary"] = {
        "n_calls": len(all_ms),
        "min_ms": min(all_ms), "max_ms": max(all_ms),
        "mean_ms": round(sum(all_ms) / len(all_ms), 1),
        "full_utterance_accuracy": sum(r["correct"] for r in results["full_utterance"])
        / len(results["full_utterance"]),
        "truncated_accuracy_by_duration": {
            dur: sum(
                r["correct"] for r in results["truncated"] if r["truncated_to_s"] == dur
            ) / max(1, sum(1 for r in results["truncated"] if r["truncated_to_s"] == dur))
            for dur in sorted({r["truncated_to_s"] for r in results["truncated"]})
        },
    }
    print()
    print("=== summary ===")
    print(json.dumps(results["summary"], indent=2))

    out_path = REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice" / (
        f"m2_6b4a_lid_benchmark_results_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
