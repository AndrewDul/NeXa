#!/usr/bin/env python3
"""M2.6B.4A/4B — offline benchmark of ``WhisperCppLanguageDetector`` against
a candidate ggml model, using the real PL/EN audio fixtures already in the
repo. No Gemini call, no hardware.

M2.6B.4B extends the original (M2.6B.4A) single-model script:

* ``--model-path``/``--model-label`` make the model under test selectable
  — this is the SAME production ``WhisperCppConfig(model_path=...)`` seam
  ``WhisperCppLanguageDetector`` already supports; no ``src/nexa/**``
  change was needed to make this work.
* the full labelled PL/EN corpus from
  ``docs/research/m2_4b_bilingual_stt/corpus.py`` (15 PL + 15 EN, the same
  ground-truth corpus R0024/R0025 already established) is used for the
  accuracy sweep, instead of the original 3 pairs — "use every relevant
  real sample already available", never fabricated audio.
* the ``short``/``mixed`` groups (10 + 10) are run and reported too, but
  never counted against the pass/fail accuracy bar (per the corpus's own
  documented convention: ``short`` is soft-hinted, ``mixed`` has no single
  correct language).
* an EOT-visible-latency simulation: for utterances long enough, launches
  ONE background LID inference on the first 1.5s of buffered audio (never
  a new encoder invocation per chunk) and reports how much LID delay would
  actually be visible at the moment local VAD end-of-turn fires.
* peak RSS (``resource.getrusage``) is reported per run — run this script
  once per model (separate process each time) for a clean measurement.

Usage::

    .venv/bin/python m2_6b4a_lid_benchmark.py --model-label base_q8_0
    .venv/bin/python m2_6b4a_lid_benchmark.py \\
        --model-path ~/.local/share/nexa/research/lid/ggml-tiny.bin \\
        --model-label tiny
    .venv/bin/python m2_6b4a_lid_benchmark.py \\
        --model-path ~/.local/share/nexa/research/lid/ggml-tiny-q8_0.bin \\
        --model-label tiny_q8_0
    .venv/bin/python m2_6b4a_lid_benchmark.py \\
        --model-path ~/.local/share/nexa/research/lid/ggml-tiny-q5_1.bin \\
        --model-label tiny_q5_1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
BILINGUAL_RESEARCH = REPO_ROOT / "docs" / "research" / "m2_4b_bilingual_stt"
for p in (str(SRC), str(BILINGUAL_RESEARCH)):
    if p not in sys.path:
        sys.path.insert(0, p)

import corpus as C  # noqa: E402

from nexa.stt import WhisperCppLanguageDetector  # noqa: E402
from nexa.stt.config import WhisperCppConfig  # noqa: E402

AUDIO_DIR = BILINGUAL_RESEARCH / "fixtures" / "audio"

FIXTURES = REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"
TRUNCATION_PAIRS = {
    "speed_of_light": (
        FIXTURES / "en_what_is_the_speed_of_light.wav",
        FIXTURES / "pl_jaka_jest_prędkość_światła.wav",
    ),
    "colors": (
        FIXTURES / "en_what_are_colors.wav",
        FIXTURES / "pl_co_to_są_kolory.wav",
    ),
}


def _load(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


def _classify(p_pl: float, p_en: float) -> str:
    return "pl" if p_pl >= p_en else "en"


async def _run_corpus_accuracy(detector: WhisperCppLanguageDetector) -> dict:
    """Full-utterance accuracy over the ENTIRE labelled M2.4B corpus (50
    real recorded fixtures: 15 pl, 15 en, 10 short, 10 mixed) — never a
    fabricated sample."""
    rows: list[dict] = []
    for item in C.CORPUS:
        wav_path = AUDIO_DIR / f"{item.uid}.wav"
        if not wav_path.is_file():
            continue
        pcm, sr = _load(wav_path)
        duration_s = len(pcm) / 2 / sr
        t0 = time.monotonic()
        r = await detector.detect(pcm)
        elapsed_ms = round(1000 * (time.monotonic() - t0), 1)
        got = _classify(r.p_pl, r.p_en)
        row = {
            "uid": item.uid, "group": item.group,
            "expected_language": item.expected_language, "text": item.text,
            "duration_s": round(duration_s, 2), "elapsed_ms": elapsed_ms,
            "p_pl": round(r.p_pl, 4), "p_en": round(r.p_en, 4),
            "margin": round(abs(r.p_pl - r.p_en), 4),
            "raw_language": r.raw_language, "got": got,
        }
        # Only pl/en groups have a single unambiguous correct answer.
        if item.expected_language in ("pl", "en"):
            row["correct"] = got == item.expected_language
        else:
            row["correct"] = None  # not scored (ambiguous/mixed)
        rows.append(row)
    return {"rows": rows}


async def _run_truncation_sweep(detector: WhisperCppLanguageDetector) -> list[dict]:
    rows: list[dict] = []
    for name, (en_path, pl_path) in TRUNCATION_PAIRS.items():
        en_pcm, sr = _load(en_path)
        pl_pcm, _ = _load(pl_path)
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
                rows.append({
                    "pair": name, "truncated_to_s": dur_s, "expected": lang,
                    "elapsed_ms": elapsed_ms, "p_pl": round(r.p_pl, 4),
                    "p_en": round(r.p_en, 4), "got": got, "correct": got == lang,
                    "margin": round(abs(r.p_pl - r.p_en), 4),
                })
        # full utterance too, for the same pair (comparable to R0039).
        for lang, pcm in (("en", en_pcm), ("pl", pl_pcm)):
            t0 = time.monotonic()
            r = await detector.detect(pcm)
            elapsed_ms = round(1000 * (time.monotonic() - t0), 1)
            got = _classify(r.p_pl, r.p_en)
            rows.append({
                "pair": name, "truncated_to_s": round(len(pcm) / 2 / sr, 2),
                "expected": lang, "elapsed_ms": elapsed_ms,
                "p_pl": round(r.p_pl, 4), "p_en": round(r.p_en, 4),
                "got": got, "correct": got == lang,
                "margin": round(abs(r.p_pl - r.p_en), 4), "full_utterance": True,
            })
    return rows


async def _run_eot_visible_latency(detector: WhisperCppLanguageDetector) -> list[dict]:
    """Simulates the REAL architecture under consideration: ONE background
    LID inference launched on the first 1.5s of buffered speech while the
    (simulated) user keeps talking, never a new encoder invocation per
    audio chunk. Reports total LID time and how much of it would still be
    outstanding at the moment local VAD end-of-turn actually fires, for
    utterances of varying real length."""
    rows: list[dict] = []
    for name, (en_path, pl_path) in TRUNCATION_PAIRS.items():
        for lang, path in (("en", en_path), ("pl", pl_path)):
            pcm, sr = _load(path)
            full_duration_s = len(pcm) / 2 / sr
            lid_start_s = 1.5
            if full_duration_s <= lid_start_s:
                # utterance ends before 1.5s of speech even exists --
                # honestly report the LID must run fully AFTER EOT.
                rows.append({
                    "pair": name, "expected": lang,
                    "utterance_duration_s": round(full_duration_s, 2),
                    "lid_started_at_s": None, "note":
                        "utterance shorter than the 1.5s LID-start point -- "
                        "LID cannot start early; full LID latency is visible "
                        "at EOT",
                })
                continue
            n_bytes = int(lid_start_s * sr) * 2
            buffered_at_start = pcm[:n_bytes]

            # Record the ACTUAL completion timestamp from inside the task
            # itself -- checking `lid_task.done()` only after already
            # awaiting the full simulated remaining-speech sleep would
            # silently discard how much EARLIER than that the task really
            # finished (a real bug caught while writing this: the naive
            # version always reported `lid_total_ms` == the sleep duration
            # whenever the task finished before EOT, since it never
            # captured the earlier real completion time).
            done_at: dict[str, float] = {}

            async def _timed_detect(pcm_bytes: bytes, marker: dict) -> object:
                result = await detector.detect(pcm_bytes)
                marker["t"] = time.monotonic()
                return result

            t_lid_start = time.monotonic()
            lid_task = asyncio.create_task(_timed_detect(buffered_at_start, done_at))
            # simulate the user continuing to speak until full_duration_s
            remaining_speech_s = full_duration_s - lid_start_s
            await asyncio.sleep(remaining_speech_s)  # VAD end-of-turn fires here
            t_eot = time.monotonic()
            if not lid_task.done():
                await lid_task
            t_lid_done = done_at["t"]
            lid_total_ms = round(1000 * (t_lid_done - t_lid_start), 1)
            eot_visible_ms = max(0.0, round(1000 * (t_lid_done - t_eot), 1))
            r = lid_task.result()
            got = _classify(r.p_pl, r.p_en)
            rows.append({
                "pair": name, "expected": lang,
                "utterance_duration_s": round(full_duration_s, 2),
                "lid_started_at_s": lid_start_s,
                "eot_at_s": round(lid_start_s + remaining_speech_s, 2),
                "lid_total_ms": lid_total_ms,
                "eot_visible_latency_ms": eot_visible_ms,
                "got_from_first_1_5s": got, "correct": got == lang,
                "p_pl": round(r.p_pl, 4), "p_en": round(r.p_en, 4),
            })
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--model-path", type=str, default=None,
        help="path to a ggml model file; omit for the production default "
        "(base/q8_0, frozen local-voice model, used unmodified here as the "
        "comparison baseline)",
    )
    ap.add_argument(
        "--model-label", type=str, required=True,
        help="short label for this run, used in the output filename",
    )
    args = ap.parse_args()

    cfg = (
        WhisperCppConfig(model_path=Path(args.model_path).expanduser())
        if args.model_path else WhisperCppConfig()
    )
    print(f"model: {args.model_label}  path: {cfg.model_path}")
    t0 = time.monotonic()
    detector = WhisperCppLanguageDetector(cfg)
    load_ms = round(1000 * (time.monotonic() - t0), 1)
    print(f"model load time: {load_ms} ms")

    # cold start -- first call ever this process.
    warmup_item = next(it for it in C.CORPUS if it.uid == "016_en_what_is_a_black_hole")
    pcm, _ = _load(AUDIO_DIR / f"{warmup_item.uid}.wav")
    t0 = time.monotonic()
    await detector.detect(pcm)
    cold_ms = round(1000 * (time.monotonic() - t0), 1)
    print(f"cold start (first call): {cold_ms} ms")

    print("running corpus accuracy sweep (50 fixtures)...")
    corpus_result = await _run_corpus_accuracy(detector)

    print("running truncation sweep...")
    truncation_rows = await _run_truncation_sweep(detector)

    print("running EOT-visible-latency simulation...")
    eot_rows = await _run_eot_visible_latency(detector)

    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # Linux: KiB

    # Cold start was separately measured above on a held-out file (not part
    # of the scored corpus sweep), so every row in corpus_result is a warm
    # invocation.
    pl_en_rows = [r for r in corpus_result["rows"] if r["correct"] is not None]
    accuracy = sum(1 for r in pl_en_rows if r["correct"]) / len(pl_en_rows) if pl_en_rows else None
    warm_ms = [r["elapsed_ms"] for r in corpus_result["rows"]]

    results = {
        "model_label": args.model_label,
        "model_path": str(cfg.model_path),
        "model_size_bytes": (
            Path(cfg.model_path).stat().st_size if Path(cfg.model_path).is_file() else None
        ),
        "load_ms": load_ms,
        "cold_ms": cold_ms,
        "warm_mean_ms": round(sum(warm_ms) / len(warm_ms), 1) if warm_ms else None,
        "warm_min_ms": min(warm_ms) if warm_ms else None,
        "warm_max_ms": max(warm_ms) if warm_ms else None,
        "corpus_pl_en_accuracy": accuracy,
        "corpus_pl_en_n": len(pl_en_rows),
        "corpus_rows": corpus_result["rows"],
        "truncation_rows": truncation_rows,
        "eot_visible_rows": eot_rows,
        "peak_rss_kb": peak_rss_kb,
    }

    print()
    print("=== summary ===")
    print(f"corpus pl/en accuracy: {accuracy} ({len(pl_en_rows)} scored fixtures)")
    false_rows = [r for r in corpus_result["rows"] if r["correct"] is False]
    for r in false_rows:
        print(f"  MISCLASSIFIED: {r['uid']} expected={r['expected_language']} got={r['got']} "
              f"p_pl={r['p_pl']} p_en={r['p_en']}")
    print(
        f"warm mean/min/max ms: {results['warm_mean_ms']}/"
        f"{results['warm_min_ms']}/{results['warm_max_ms']}"
    )
    print(f"peak RSS: {peak_rss_kb} KiB ({peak_rss_kb / 1024:.1f} MiB)")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_path = (
        REPO_ROOT / "docs" / "research" / "m2_6_cloud_realtime_voice"
        / f"m2_6b4b_lid_benchmark_{args.model_label}_{stamp}.json"
    )
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
