"""M2.4B.3.5 — focused Piper-active validation of Ollama ``num_thread=2``
on ``gemma4:e4b`` before the operator blind A/B.

R0021 measured `num_thread=2` = +12-14% decode tok/s *uncontended*. This
checks the one thing R0021 did not: that it still behaves under a REAL
concurrent Piper (`nice +10`) load, i.e. the realtime-voice condition.

For each of {default threads, num_thread=2}:
  * a background loop keeps Piper synthesising `pl_PL-gosia-medium`
    continuously (the TTS-active contention);
  * 4 warm realistic voice-policy LLM turns run through the real B.3.3
    wire path (`ConversationContext` + ``ResponseMode.VOICE``);
  * records LLM tok/s + generated chars/s + TTFT, Piper true RTF,
    per-core CPU, temp, throttled, MemAvailable.

PASS if `num_thread=2` keeps the directional decode gain AND Piper RTF
stays well under 1.0 (no TTS regression) AND no throttling.

Not a production change. Writes nt2_piper_validation_20260908.json.
"""
from __future__ import annotations

import asyncio
import json
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path.insert(0, str(B.parents[2] / "src"))
sys.path.insert(0, str(B))

from bench_llm import chat, ollama_stop, warm_up, wire_messages  # noqa: E402

from nexa.conversation.response_mode import ResponseMode  # noqa: E402
from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402

MODEL = "gemma4:e4b"
PIPER_TEXT = (
    "Czarna dziura to obszar czasoprzestrzeni o tak silnej grawitacji, "
    "że nic, nawet światło, nie może się z niej wydostać."
)
LLM_PROMPTS = [
    "Co to jest czarna dziura?",
    "Z czego składa się gwiazda?",
    "Po co człowiekowi sen?",
]
LLM_NUM_PREDICT = 70          # a realistic 1-3 sentence voice reply
PIPER_PLAYBACK_PAUSE_S = 3.0  # simulate audio drain between sentences (realistic TTS cadence)
VARIANTS = [("default", {}), ("num_thread=2", {"num_thread": 2})]
_WAV_BYTES_PER_S = 22050 * 2  # pl_PL-gosia-medium: 22050 Hz, 16-bit mono


def per_core_cpu(sample_s: float = 1.0) -> list[float]:
    def snap() -> list[int]:
        rows = []
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("cpu") and line[3].isdigit():
                rows.append([int(x) for x in line.split()[1:]])
        return rows

    a = snap()
    time.sleep(sample_s)
    b = snap()
    out = []
    for xa, xb in zip(a, b, strict=False):
        idle = (xb[3] + xb[4]) - (xa[3] + xa[4])
        total = sum(xb) - sum(xa)
        out.append(round(100.0 * (1 - idle / total), 1) if total else 0.0)
    return out


def temp_c() -> float:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
    except Exception:
        return -1.0


def throttled() -> str:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return out.split("=")[-1] if "=" in out else out
    except Exception:
        return "n/a"


def mem_avail_mb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return round(int(line.split()[1]) / 1024.0, 1)
    return -1.0


async def piper_loop(server: PiperHttpServer, stop: asyncio.Event, rtfs: list[float]) -> None:
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            wav = await server.synthesize(PIPER_TEXT, voice=PL_VOICE)
        except Exception as exc:  # noqa: BLE001
            rtfs.append(-1.0)
            print(f"  piper synth error: {exc}", flush=True)
            continue
        wall = time.monotonic() - t0
        audio_s = max(len(wav) - 44, 0) / _WAV_BYTES_PER_S
        if audio_s > 0:
            rtfs.append(round(wall / audio_s, 3))
        # realistic voice cadence: one sentence then wait ~ its playback,
        # not a back-to-back synthesis hammer.
        await asyncio.sleep(PIPER_PLAYBACK_PAUSE_S)


async def run_variant(name: str, opts: dict) -> dict:
    print(f"\n===== variant: {name} =====", flush=True)
    server = PiperHttpServer(PiperHttpConfig(nice=10))
    await server.start()
    await server.prewarm()
    stop = asyncio.Event()
    rtfs: list[float] = []
    loop_task = asyncio.create_task(piper_loop(server, stop, rtfs))
    await asyncio.sleep(2.0)  # let Piper contention ramp

    turns = []
    cpu_samples = []
    for i, p in enumerate(LLM_PROMPTS):
        msgs = wire_messages([], p, ResponseMode.VOICE)
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(
            None,
            lambda m=msgs, o=opts: chat(MODEL, m, keep_alive="10m",
                                        num_predict=LLM_NUM_PREDICT, extra_opts=o),
        )
        cpu = per_core_cpu(0.8)
        cpu_samples.append(cpu)
        turns.append({"prompt": p, "tok_s": r["tok_s"], "gen_chars_s": r["gen_chars_s"],
                      "gen_chars": r["gen_chars"], "ttft_s": r["ttft_s"],
                      "eval_count": r["eval_count"]})
        print(f"  turn{i+1}: tok/s={r['tok_s']} ch/s={r['gen_chars_s']} "
              f"chars={r['gen_chars']} ttft={r['ttft_s']}s  cpu={cpu} "
              f"temp={temp_c()}C thr={throttled()}", flush=True)

    stop.set()
    await loop_task
    await server.stop()

    good_rtf = [x for x in rtfs if x > 0]
    tk = [t["tok_s"] for t in turns if t["tok_s"]]
    ch = [t["gen_chars_s"] for t in turns if t["gen_chars_s"]]
    summary = {
        "variant": name, "opts": opts, "turns": turns,
        "llm_tok_s_mean": round(st.mean(tk), 3) if tk else None,
        "llm_chars_s_mean": round(st.mean(ch), 3) if ch else None,
        "piper_rtf_n": len(good_rtf),
        "piper_rtf_mean": round(st.mean(good_rtf), 3) if good_rtf else None,
        "piper_rtf_max": round(max(good_rtf), 3) if good_rtf else None,
        "piper_synth_errors": sum(1 for x in rtfs if x < 0),
        "per_core_cpu_last": cpu_samples[-1] if cpu_samples else None,
        "temp_c": temp_c(), "throttled": throttled(), "mem_avail_mb": mem_avail_mb(),
    }
    print(f"  => {name}: LLM tok/s {summary['llm_tok_s_mean']} "
          f"chars/s {summary['llm_chars_s_mean']} | Piper RTF mean "
          f"{summary['piper_rtf_mean']} max {summary['piper_rtf_max']} "
          f"(n={summary['piper_rtf_n']}, err={summary['piper_synth_errors']}) | "
          f"temp {summary['temp_c']}C thr {summary['throttled']}", flush=True)
    return summary


async def main() -> None:
    print("M2.4B.3.5 num_thread=2 Piper-active validation (gemma4:e4b)\n", flush=True)
    warm_up(MODEL)
    results = [await run_variant(name, opts) for name, opts in VARIANTS]
    ollama_stop(MODEL)

    d, n2 = results[0], results[1]
    verdict = []
    if d["llm_tok_s_mean"] and n2["llm_tok_s_mean"]:
        delta = (n2["llm_tok_s_mean"] - d["llm_tok_s_mean"]) / d["llm_tok_s_mean"] * 100
        verdict.append(f"decode tok/s {d['llm_tok_s_mean']} -> {n2['llm_tok_s_mean']} "
                       f"({delta:+.1f}%)")
    verdict.append(f"Piper RTF max: default {d['piper_rtf_max']} / nt2 {n2['piper_rtf_max']} "
                   f"(<1.0 => no TTS regression)")
    verdict.append(f"throttled: default {d['throttled']} / nt2 {n2['throttled']}")
    verdict.append(f"piper synth errors: default {d['piper_synth_errors']} / "
                   f"nt2 {n2['piper_synth_errors']}")
    out = {"model": MODEL, "results": results, "verdict_lines": verdict,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (B / "nt2_piper_validation_20260908.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    print("\n--- VERDICT ---")
    for line in verdict:
        print("  " + line, flush=True)
    print("\nNT2_PIPER_VALIDATION DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
