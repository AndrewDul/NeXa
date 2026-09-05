#!/usr/bin/env python3
"""Supplemental run: whisper.cpp with beam_size=1 (matched to legacy's beam1
faster-whisper configs) to isolate decode-strategy effects from runtime
effects. Reuses run_whisper_cpp_bench's helpers."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "/home/devdul/nexa_m2_spike_build")
from run_whisper_cpp_bench import FIXTURES, INDEX, MODELS_DIR, peak_rss_kb, word_error_rate  # noqa: E402

WHISPER_CLI = "/home/devdul/nexa_m2_spike_build/whisper.cpp/build/bin/whisper-cli"
CONFIGS = [
    {"label": "tiny/fp16/bs1", "model": MODELS_DIR / "ggml-tiny.bin"},
    {"label": "base/fp16/bs1", "model": MODELS_DIR / "ggml-base.bin"},
]
THREADS = 4


def run_one(model_path: Path, wav_path: Path, lang: str) -> dict:
    cmd = [
        WHISPER_CLI, "-m", str(model_path), "-f", str(wav_path), "-l", lang,
        "-t", str(THREADS), "-nt", "-np", "-bs", "1", "-bo", "1",
    ]
    rss_result: dict = {"peak_kb": 0}
    stop_event = threading.Event()
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sampler = threading.Thread(target=peak_rss_kb, args=(proc.pid, stop_event, rss_result))
    sampler.start()
    stdout, _ = proc.communicate()
    stop_event.set()
    sampler.join()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = " ".join(
        line.strip() for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith("read_audio_data:")
    )
    return {"text": text, "elapsed_ms": elapsed_ms, "peak_rss_kb": rss_result["peak_kb"]}


def main() -> None:
    results = []
    for cfg in CONFIGS:
        for item in INDEX:
            wav = FIXTURES / item["file"]
            r = run_one(cfg["model"], wav, item["language"])
            wer = word_error_rate(item["expected_text"], r["text"])
            row = {
                "config": cfg["label"], "file": item["file"], "language": item["language"],
                "expected_text": item["expected_text"], "recognized_text": r["text"],
                "wer": round(wer, 4), "elapsed_ms": round(r["elapsed_ms"], 1),
                "peak_rss_mb": round(r["peak_rss_kb"] / 1024, 1),
            }
            results.append(row)
            print(f"{cfg['label']:14} {item['file']:40} wer={wer:.2f} t={r['elapsed_ms']:.0f}ms -> {r['text']!r}", file=sys.stderr)

    out_path = Path(sys.argv[1])
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
