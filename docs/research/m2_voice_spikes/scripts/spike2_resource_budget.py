#!/usr/bin/env python3
"""Spike 2: resource-budget interaction test (VAD/STT/LLM/TTS), Pi 5.

Throwaway research script. Stages A-H per M2.0A task spec. Uses:
- whisper.cpp (base/fp16, the Spike 1 winner) for STT
- whisper.cpp's native ggml Silero VAD for VAD
- piper-tts (scratch venv) with pl_PL-gosia-medium for TTS
- Ollama HTTP API for gemma4:e4b (the frozen M1.1 baseline)
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

WHISPER_CLI = "/home/devdul/nexa_m2_spike_build/whisper.cpp/build/bin/whisper-cli"
VAD_TOOL = "/home/devdul/nexa_m2_spike_build/whisper.cpp/build/bin/whisper-vad-speech-segments"
BASE_MODEL = "/home/devdul/nexa_m2_spike_build/whisper.cpp/models/ggml-base.bin"
VAD_MODEL = "/home/devdul/nexa_m2_spike_build/whisper.cpp/models/ggml-silero-v5.1.2.bin"
PIPER = "/home/devdul/nexa_m2_spike_build/spike2-venv/bin/piper"
PIPER_VOICE = "/home/devdul/nexa_m2_spike_build/voices/pl_PL-gosia-medium.onnx"
STT_WAV = "/home/devdul/Projects/NeXa_IkiGai/docs/research/m2_voice_spikes/asr_test_samples/pl_wyjaśnij_grawitację.wav"
VAD_WAV = STT_WAV  # same short clip is fine for VAD segmentation timing
TTS_TEXT = "Dzień dobry, sprawdzam jak długo trwa synteza mowy na tym urządzeniu."
OLLAMA = "http://127.0.0.1:11434"
MODEL = "gemma4:e4b"


class SystemSampler:
    """Background sampler: system-wide RAM/CPU/load/temp during a stage."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.samples: list[dict] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self._snapshot())
            time.sleep(0.5)

    @staticmethod
    def _snapshot() -> dict:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])
        loadavg = open("/proc/loadavg").read().split()[:3]
        temp = subprocess.run(
            ["vcgencmd", "measure_temp"], capture_output=True, text=True
        ).stdout.strip()
        throttled = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True
        ).stdout.strip()
        return {
            "t": time.time(),
            "mem_available_mb": mem.get("MemAvailable", 0) // 1024,
            "swap_used_mb": (mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) // 1024,
            "load1": float(loadavg[0]),
            "temp": temp,
            "throttled": throttled,
        }

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join()
        if not self.samples:
            return {}
        return {
            "min_mem_available_mb": min(s["mem_available_mb"] for s in self.samples),
            "max_swap_used_mb": max(s["swap_used_mb"] for s in self.samples),
            "max_load1": max(s["load1"] for s in self.samples),
            "temp_start": self.samples[0]["temp"],
            "temp_end": self.samples[-1]["temp"],
            "throttled_ever_nonzero": any(s["throttled"] != "throttled=0x0" for s in self.samples),
            "n_samples": len(self.samples),
        }


def run_vad() -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [VAD_TOOL, "-vm", VAD_MODEL, "-f", VAD_WAV],
        capture_output=True, text=True,
    )
    return {"elapsed_ms": (time.perf_counter() - t0) * 1000, "rc": proc.returncode}


def run_stt() -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [WHISPER_CLI, "-m", BASE_MODEL, "-f", STT_WAV, "-l", "pl", "-t", "4", "-nt", "-np"],
        capture_output=True, text=True,
    )
    text = " ".join(
        line.strip() for line in proc.stdout.splitlines()
        if line.strip() and not line.strip().startswith("read_audio_data:")
    )
    return {"elapsed_ms": (time.perf_counter() - t0) * 1000, "text": text}


def run_tts() -> dict:
    t0 = time.perf_counter()
    out = "/tmp/spike2_tts_out.wav"
    proc = subprocess.run(
        [PIPER, "-m", PIPER_VOICE, "-f", out],
        input=TTS_TEXT, capture_output=True, text=True,
    )
    return {"elapsed_ms": (time.perf_counter() - t0) * 1000, "rc": proc.returncode}


def run_llm_turn(keep_alive: str = "5m") -> dict:
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Jesteś NeXa. Odpowiadaj krótko."},
            {"role": "user", "content": "Opowiedz mi bardzo krótko, czym jest grawitacja."},
        ],
        "stream": True,
        "options": {"num_ctx": 8192, "num_predict": 120},
        "think": False,
        "keep_alive": keep_alive,
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.perf_counter()
    ttft = None
    chunks = []
    final = {}
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.strip()
            if not line:
                continue
            chunk = json.loads(line)
            content = chunk.get("message", {}).get("content", "")
            if content and ttft is None:
                ttft = time.perf_counter() - t0
            if content:
                chunks.append(content)
            if chunk.get("done"):
                final = chunk
    total_s = time.perf_counter() - t0
    eval_count = final.get("eval_count", 0)
    eval_ns = final.get("eval_duration", 0)
    tok_s = (eval_count / (eval_ns / 1e9)) if eval_ns else None
    return {
        "ttft_s": ttft, "total_s": total_s, "eval_count": eval_count,
        "tok_s": tok_s, "text": "".join(chunks),
    }


def stage(name: str, fn) -> dict:
    print(f"\n=== STAGE {name} ===", file=sys.stderr)
    sampler = SystemSampler()
    sampler.start()
    t0 = time.perf_counter()
    result = fn()
    wall_s = time.perf_counter() - t0
    sys_stats = sampler.stop()
    row = {"stage": name, "wall_s": round(wall_s, 2), "result": result, "system": sys_stats}
    print(json.dumps(row, indent=2, ensure_ascii=False, default=str), file=sys.stderr)
    return row


def run_concurrently(*fns):
    results = [None] * len(fns)

    def worker(i, fn):
        results[i] = fn()

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def main() -> None:
    results = []

    # A. VAD only
    results.append(stage("A_vad_only", run_vad))

    # B. STT only
    results.append(stage("B_stt_only", run_stt))

    # C. LLM only (loads fresh, no keep_alive across stages assumed)
    results.append(stage("C_llm_only", lambda: run_llm_turn(keep_alive="30s")))

    # D. TTS only
    results.append(stage("D_tts_only", run_tts))

    # E. VAD + STT concurrently
    results.append(stage(
        "E_vad_plus_stt",
        lambda: dict(zip(["vad", "stt"], run_concurrently(run_vad, run_stt))),
    ))

    # F. LLM + TTS concurrently
    results.append(stage(
        "F_llm_plus_tts",
        lambda: dict(zip(["llm", "tts"], run_concurrently(
            lambda: run_llm_turn(keep_alive="30s"), run_tts
        ))),
    ))

    # G. VAD + STT while LLM is resident (warm-loaded first, kept alive, but IDLE
    # during VAD/STT - tests RAM pressure from residency, not CPU contention)
    print("\n=== warming LLM for stage G (resident, idle) ===", file=sys.stderr)
    run_llm_turn(keep_alive="2m")  # warm it, keep resident for 2 minutes
    results.append(stage(
        "G_vad_stt_with_resident_idle_llm",
        lambda: dict(zip(["vad", "stt"], run_concurrently(run_vad, run_stt))),
    ))

    # H. Full representative workload: LLM actively generating WHILE VAD+STT+TTS
    # all run concurrently - the real CPU-contention stress test.
    results.append(stage(
        "H_full_concurrent_llm_generating_plus_vad_stt_tts",
        lambda: dict(zip(
            ["llm", "vad", "stt", "tts"],
            run_concurrently(
                lambda: run_llm_turn(keep_alive="10s"), run_vad, run_stt, run_tts
            ),
        )),
    ))

    out_path = "/home/devdul/Projects/NeXa_IkiGai/docs/research/m2_voice_spikes/spike2_resource_budget_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nWrote results to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
