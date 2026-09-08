"""M2.4B.5A — CPU-contention root-cause probe for the R0026 live slowdown.

RESEARCH TOOLING ONLY. Reproduces the two conditions on the real Pi and
measures bilingual STT (ctypes LID + whisper-cli decode) latency in each:

  A. ISOLATED  — LLM idle, Piper idle. The established ~2.8 s baseline.
  B. CONTENDED — a real ``gemma4:e4b`` generation running (num_thread=2,
     keep_alive=30m) + a real Piper (nice +10) synth loop, i.e. exactly
     what happens when TV audio backlogs the STT queue *while NeXa is
     answering*. Same STT calls; measure the degradation.

Per STT call: detect latency, decode latency, total. Per phase: per-process
CPU (whisper-cli / llama-server / piper), RSS, MemAvailable, temperature,
throttled, and the max concurrent ``whisper-cli`` process count seen.

This does NOT exercise the pipeline's queues — the fix for the backlog is
tested deterministically in ``tests/test_bilingual_voice_stability.py``.
This probe isolates the *contention* half of the root cause.

Writes ``b5a_contention_probe_<ts>.json``.
"""
from __future__ import annotations

import asyncio
import json
import statistics as st
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

from nexa.stt import (  # noqa: E402
    BilingualSpeechTranscriber,
    Language,
    LanguageIdGuard,
    WhisperCppLanguageDetector,
    WhisperCppTranscriber,
)
from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402

MODEL = "gemma4:e4b"
OLLAMA = "http://127.0.0.1:11434"
AUDIO = HERE / "fixtures" / "audio"
STT_CLIPS = [
    "001_pl_co_to_jest_czarna_dziura", "016_en_what_is_a_black_hole",
    "003_pl_z_czego_sklada_sie_gwiazda", "018_en_what_is_a_star_made_of",
    "014_pl_dlaczego_niebo_jest_niebieskie", "029_en_why_is_the_sky_blue",
]
PIPER_TEXT = ("Czarna dziura to obszar czasoprzestrzeni o tak silnej grawitacji, "
              "że nic, nawet światło, nie może się z niej wydostać.")
_WAV_BPS = 22050 * 2


def _audio(uid: str) -> bytes:
    w = wave.open(str(AUDIO / f"{uid}.wav"), "rb")
    d = w.readframes(w.getnframes())
    w.close()
    return d


def _pids(name: str) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True, timeout=5)
        return [int(x) for x in out.stdout.split()]
    except Exception:
        return []


def _proc_cpu_jiffies(pid: int) -> int | None:
    try:
        parts = Path(f"/proc/{pid}/stat").read_text().split()
        return int(parts[13]) + int(parts[14])
    except Exception:
        return None


def _mem_avail_mb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return -1.0


def _temp_c() -> float:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
    except Exception:
        return -1.0


def _throttled() -> str:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return out.split("=")[-1] if "=" in out else out
    except Exception:
        return "n/a"


def _total_cpu_jiffies() -> tuple[int, int]:
    p = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    v = [int(x) for x in p]
    return sum(v), v[3] + v[4]


class _Sampler(threading.Thread):
    """Per-process CPU%, RSS, whisper-cli count, temp — while a phase runs."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.samples: list[dict] = []
        self.max_whisper_procs = 0

    def run(self) -> None:
        prev: dict[str, int] = {}
        prev_tot, prev_idle = _total_cpu_jiffies()
        clk = 100
        while not self._stop.is_set():
            time.sleep(0.5)
            groups = {"whisper-cli": _pids("whisper-cli"),
                      "llama-server": _pids("llama-server"),
                      "piper": _pids("piper")}
            self.max_whisper_procs = max(self.max_whisper_procs, len(groups["whisper-cli"]))
            tot, idle = _total_cpu_jiffies()
            dtot = max(tot - prev_tot, 1)
            row: dict = {"t": time.monotonic(),
                         "cpu_total_pct": round(100 * (1 - (idle - prev_idle) / dtot), 1),
                         "whisper_procs": len(groups["whisper-cli"]),
                         "mem_avail_mb": round(_mem_avail_mb(), 1),
                         "temp_c": round(_temp_c(), 1)}
            for gname, pids in groups.items():
                j = sum(x for x in (_proc_cpu_jiffies(p) for p in pids) if x)
                pj = prev.get(gname, j)
                row[f"{gname}_cpu_pct"] = round(100.0 * clk * (j - pj) / dtot, 1)
                prev[gname] = j
            self.samples.append(row)
            prev_tot, prev_idle = tot, idle

    def stop(self) -> dict:
        self._stop.set()
        self.join(timeout=2)
        def col(k: str) -> list[float]:
            return [s[k] for s in self.samples if isinstance(s.get(k), (int, float))]
        return {
            "n": len(self.samples),
            "cpu_total_pct_mean": round(st.mean(col("cpu_total_pct")), 1) if self.samples else None,
            "whisper_cli_cpu_pct_mean": round(st.mean(col("whisper-cli_cpu_pct")), 1)
            if self.samples else None,
            "llama_server_cpu_pct_mean": round(st.mean(col("llama-server_cpu_pct")), 1)
            if self.samples else None,
            "piper_cpu_pct_mean": round(st.mean(col("piper_cpu_pct")), 1) if self.samples else None,
            "max_whisper_procs": self.max_whisper_procs,
            "mem_avail_mb_min": round(min(col("mem_avail_mb")), 1) if self.samples else None,
            "temp_c_max": round(max(col("temp_c")), 1) if self.samples else None,
        }


def _ollama_generate_blocking(prompt: str, stop: threading.Event) -> None:
    """Keep gemma4:e4b busy: fire long generations back to back until stop."""
    while not stop.is_set():
        body = {"model": MODEL, "prompt": prompt, "stream": False,
                "keep_alive": "30m", "think": False,
                "options": {"num_thread": 2, "num_predict": 220, "temperature": 0.7}}
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/generate",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=120).read()
        except Exception:
            time.sleep(0.5)


async def _piper_loop(server: PiperHttpServer, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await server.synthesize(PIPER_TEXT, voice=PL_VOICE)
        except Exception:
            pass
        await asyncio.sleep(3.0)


async def _run_stt_phase(bt: BilingualSpeechTranscriber, label: str) -> dict:
    sampler = _Sampler()
    sampler.start()
    rows = []
    for uid in STT_CLIPS:
        r = await bt.transcribe(_audio(uid))
        d = r.language_decision
        rows.append({"uid": uid, "detect_s": d.detect_latency_s,
                     "decode_s": d.decode_latency_s, "total_s": d.total_latency_s,
                     "selected": d.selected_language, "text": r.text})
        print(f"  [{label:9}] {uid:44} detect {d.detect_latency_s:.2f} "
              f"decode {d.decode_latency_s:.2f} total {d.total_latency_s:.2f}s  "
              f"-> {d.selected_language}", flush=True)
    res = sampler.stop()
    tot = [x["total_s"] for x in rows]
    det = [x["detect_s"] for x in rows]
    dec = [x["decode_s"] for x in rows]
    return {"label": label, "turns": rows, "resources": res,
            "detect_mean_s": round(st.mean(det), 3), "decode_mean_s": round(st.mean(dec), 3),
            "total_mean_s": round(st.mean(tot), 3), "total_max_s": round(max(tot), 3)}


async def main() -> None:
    base = WhisperCppTranscriber()
    detector = WhisperCppLanguageDetector()
    bt = BilingualSpeechTranscriber(base, detector, guard=LanguageIdGuard(),
                                    session_default_language=Language.PL)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.4B.5A contention probe — {ts}")
    print(f"start: temp {_temp_c()}C  mem_avail {_mem_avail_mb():.0f}MB  thr {_throttled()}")

    # warm the LLM prefix so a "generation" is representative
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{OLLAMA}/api/generate",
            data=json.dumps({"model": MODEL, "prompt": "Powiedz krótko: dzień dobry.",
                             "stream": False, "keep_alive": "30m",
                             "options": {"num_thread": 2, "num_predict": 8}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST"), timeout=120).read()
    except Exception as e:
        print("warm-up failed:", e)

    # ---- Phase A: isolated ----
    print("\n=== PHASE A — ISOLATED (LLM idle, Piper idle) ===")
    phase_a = await _run_stt_phase(bt, "isolated")

    # ---- Phase B: contended ----
    print("\n=== PHASE B — CONTENDED (gemma4:e4b generating + Piper nice+10 loop) ===")
    server = PiperHttpServer(PiperHttpConfig(nice=10))
    await server.start()
    await server.prewarm()
    pstop = asyncio.Event()
    piper_task = asyncio.create_task(_piper_loop(server, pstop))
    llm_stop = threading.Event()
    llm_threads = [threading.Thread(
        target=_ollama_generate_blocking,
        args=("Opowiedz szczegółowo o powstawaniu czarnych dziur i ewolucji gwiazd.", llm_stop),
        daemon=True) for _ in range(1)]
    for t in llm_threads:
        t.start()
    await asyncio.sleep(4.0)  # let contention ramp
    phase_b = await _run_stt_phase(bt, "contended")
    llm_stop.set()
    pstop.set()
    await piper_task
    await server.stop()
    detector.close()

    degradation = round(phase_b["total_mean_s"] - phase_a["total_mean_s"], 2)
    out = {"ts": ts, "phase_a_isolated": phase_a, "phase_b_contended": phase_b,
           "degradation_total_mean_s": degradation,
           "verdict": (
               f"STT total mean {phase_a['total_mean_s']}s isolated -> "
               f"{phase_b['total_mean_s']}s contended (+{degradation}s); "
               f"max whisper-cli procs isolated={phase_a['resources']['max_whisper_procs']} "
               f"contended={phase_b['resources']['max_whisper_procs']}")}
    (HERE / f"b5a_contention_probe_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print("\n--- VERDICT ---")
    print(" ", out["verdict"])
    print(f"  phase A resources: {phase_a['resources']}")
    print(f"  phase B resources: {phase_b['resources']}")
    print(f"-> b5a_contention_probe_{ts}.json")


if __name__ == "__main__":
    asyncio.run(main())
