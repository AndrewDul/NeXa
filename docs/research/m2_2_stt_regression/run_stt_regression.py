#!/usr/bin/env python3
"""M2.2 same-corpus regression: run the pinned whisper.cpp base/q8_0 config
(the exact binary/model/thread-count `WhisperCppTranscriber` uses) against
the same 12 R0006 PL/EN fixtures, and compare against R0006's scratch
benchmark (~0.354 avg WER, ~1.69s avg latency, ~221MB peak RSS).

Also samples peak RSS and reads `vcgencmd measure_temp`/`get_throttled`
(Raspberry Pi) around each run, for the M2.2 report's CPU/RAM/thermal
section. Throwaway research script — not NeXa product code; the WER/
normalize functions are copied from
`docs/research/m2_voice_spikes/scripts/run_whisper_cpp_bench.py` so the
comparison uses an identical metric.
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

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexa.stt.config import (  # noqa: E402
    DEFAULT_THREADS,
    default_model_path,
    default_whisper_cli_path,
)

FIXTURES = REPO_ROOT / "docs" / "research" / "m2_voice_spikes" / "asr_test_samples"
INDEX = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))

WHISPER_CLI = default_whisper_cli_path()
MODEL = default_model_path()


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
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
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


def read_temp_c() -> float | None:
    try:
        out = subprocess.run(
            ["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2
        )
        return float(out.stdout.strip().split("=")[1].rstrip("'C\n"))
    except Exception:
        return None


def read_throttled() -> str | None:
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2
        )
        return out.stdout.strip()
    except Exception:
        return None


def run_one(wav_path: Path, lang: str, out_prefix: Path) -> dict:
    cmd = [
        str(WHISPER_CLI), "-m", str(MODEL), "-f", str(wav_path), "-l", lang,
        "-t", str(DEFAULT_THREADS), "-oj", "-of", str(out_prefix), "-np", "-nt",
    ]
    rss_result: dict = {"peak_kb": 0}
    stop_event = threading.Event()
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sampler = threading.Thread(target=peak_rss_kb, args=(proc.pid, stop_event, rss_result))
    sampler.start()
    proc.communicate()
    stop_event.set()
    sampler.join()
    elapsed_s = time.perf_counter() - t0

    data = json.loads(out_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    text = "".join(seg["text"] for seg in data["transcription"]).strip()
    return {"text": text, "elapsed_s": elapsed_s, "peak_rss_kb": rss_result["peak_kb"]}


def main() -> None:
    if not WHISPER_CLI.is_file() or not MODEL.is_file():
        print("error: pinned whisper.cpp not installed. Run scripts/setup_whisper_cpp.py",
              file=sys.stderr)
        sys.exit(1)

    print(f"binary: {WHISPER_CLI}", file=sys.stderr)
    print(f"model:  {MODEL}", file=sys.stderr)
    print(f"threads: {DEFAULT_THREADS}", file=sys.stderr)
    print(f"temp before: {read_temp_c()}C  throttled: {read_throttled()}", file=sys.stderr)

    results = []
    import tempfile

    with tempfile.TemporaryDirectory(prefix="nexa-stt-regression-") as tmpdir:
        for item in INDEX:
            wav = FIXTURES / item["file"]
            out_prefix = Path(tmpdir) / "out"
            r = run_one(wav, item["language"], out_prefix)
            wer = word_error_rate(item["expected_text"], r["text"])
            row = {
                "file": item["file"],
                "language": item["language"],
                "expected_text": item["expected_text"],
                "recognized_text": r["text"],
                "wer": round(wer, 4),
                "elapsed_s": round(r["elapsed_s"], 3),
                "peak_rss_mb": round(r["peak_rss_kb"] / 1024, 1),
            }
            results.append(row)
            print(
                f"{item['file']:40} wer={wer:.2f} t={r['elapsed_s']:.2f}s "
                f"rss={row['peak_rss_mb']}MB -> {r['text']!r}",
                file=sys.stderr,
            )

    print(f"temp after: {read_temp_c()}C  throttled: {read_throttled()}", file=sys.stderr)

    avg_wer = sum(r["wer"] for r in results) / len(results)
    avg_latency = sum(r["elapsed_s"] for r in results) / len(results)
    avg_rss = sum(r["peak_rss_mb"] for r in results) / len(results)
    print(
        f"\nAVG WER: {avg_wer:.4f}  AVG latency: {avg_latency:.3f}s  AVG peak RSS: {avg_rss:.1f}MB",
        file=sys.stderr,
    )

    out_path = Path(__file__).parent / "stt_regression_results.json"
    out_path.write_text(
        json.dumps(
            {
                "config": "base/q8_0",
                "threads": DEFAULT_THREADS,
                "results": results,
                "avg_wer": round(avg_wer, 4),
                "avg_latency_s": round(avg_latency, 3),
                "avg_peak_rss_mb": round(avg_rss, 1),
                "temp_before_c": read_temp_c(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
