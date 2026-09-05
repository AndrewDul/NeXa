#!/usr/bin/env python3
"""Spike 1 harness: whisper.cpp vs. legacy faster-whisper, same audio/material.

Throwaway research script (not NeXa product code). Runs whisper-cli against
the legacy asr_test_samples fixtures, measuring latency, peak RSS, and a
simple word-error-rate, for a handful of practical model/quant configs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

WHISPER_CLI = "/home/devdul/nexa_m2_spike_build/whisper.cpp/build/bin/whisper-cli"
MODELS_DIR = Path("/home/devdul/nexa_m2_spike_build/whisper.cpp/models")
FIXTURES = Path("/home/devdul/Projects/NeXa_IkiGai/docs/research/m2_voice_spikes/asr_test_samples")
INDEX = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))

CONFIGS = [
    {"label": "tiny/fp16", "model": MODELS_DIR / "ggml-tiny.bin"},
    {"label": "tiny/q8_0", "model": MODELS_DIR / "ggml-tiny-q8_0.bin"},
    {"label": "base/fp16", "model": MODELS_DIR / "ggml-base.bin"},
    {"label": "base/q8_0", "model": MODELS_DIR / "ggml-base-q8_0.bin"},
]

THREADS = 4


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return text.split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[n][m] / n


def peak_rss_kb(pid: int, stop: threading.Event, result: dict) -> None:
    peak = 0
    status_path = f"/proc/{pid}/status"
    while not stop.is_set():
        try:
            with open(status_path) as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        peak = max(peak, kb)
                        break
        except (FileNotFoundError, ProcessLookupError):
            break
        time.sleep(0.02)
    result["peak_kb"] = peak


def run_one(model_path: Path, wav_path: Path, lang: str) -> dict:
    cmd = [
        WHISPER_CLI,
        "-m", str(model_path),
        "-f", str(wav_path),
        "-l", lang,
        "-t", str(THREADS),
        "-nt",
        "-np",
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
    # whisper-cli emits "read_audio_data: ..." decoder-log lines to stdout even
    # with -np; the actual transcript is the remaining non-log, non-empty lines.
    text = " ".join(
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith("read_audio_data:")
    )
    return {"text": text, "elapsed_ms": elapsed_ms, "peak_rss_kb": rss_result["peak_kb"], "raw": stdout}


def main() -> None:
    results = []
    for cfg in CONFIGS:
        for item in INDEX:
            wav = FIXTURES / item["file"]
            r = run_one(cfg["model"], wav, item["language"])
            wer = word_error_rate(item["expected_text"], r["text"])
            row = {
                "config": cfg["label"],
                "file": item["file"],
                "language": item["language"],
                "expected_text": item["expected_text"],
                "recognized_text": r["text"],
                "wer": round(wer, 4),
                "elapsed_ms": round(r["elapsed_ms"], 1),
                "peak_rss_mb": round(r["peak_rss_kb"] / 1024, 1),
            }
            results.append(row)
            print(
                f"{cfg['label']:12} {item['file']:40} wer={wer:.2f} "
                f"t={r['elapsed_ms']:.0f}ms rss={r['peak_rss_kb']/1024:.0f}MB "
                f"-> {r['text']!r}",
                file=sys.stderr,
            )

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("whisper_cpp_bench_results.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(results)} rows to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
