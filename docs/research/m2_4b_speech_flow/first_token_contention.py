#!/usr/bin/env python3
"""M2.4B.1 — controlled first-token latency vs. CPU contention.

Confirms (or refutes) R0012's INFERENCE that the huge first-token stalls
come from CPU contention on the 4-core Pi 5, WITHOUT changing the product
model. Standalone research harness — NOT wired into NeXa's runtime.

Scenarios (each: N warm `gemma4:e4b` first-token measurements via raw
Ollama `/api/chat`, plus the `done`-chunk metadata Ollama returns on the
same request; `nexa.voice_tts.metrics.ResourceSampler` records llama-server
+ system CPU during each):

  A  idle system
  B  Piper HTTP server running but idle
  C  Piper HTTP server synthesising in a tight loop (real CPU contention)

Stops early if `vcgencmd get_throttled` reports anything non-zero.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path("/home/devdul/Projects/NeXa_IkiGai")
sys.path.insert(0, str(REPO / "src"))

from nexa.tts import PL_VOICE, PiperHttpServer  # noqa: E402
from nexa.voice_tts.metrics import ResourceSampler  # noqa: E402

PROMPT = "Krotko, jednym zdaniem: czym jest czarna dziura?"
N = 4


def one_first_token() -> dict:
    body = json.dumps({
        "model": "gemma4:e4b",
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "options": {"temperature": 0.7, "num_predict": 60},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.monotonic()
    first = None
    meta = {}
    with urllib.request.urlopen(req, timeout=180) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("message", {}).get("content") and first is None:
                first = time.monotonic() - t0
            if d.get("done"):
                meta = {
                    "load_s": d.get("load_duration", 0) / 1e9,
                    "prompt_eval_count": d.get("prompt_eval_count"),
                    "prompt_eval_s": d.get("prompt_eval_duration", 0) / 1e9,
                    "eval_count": d.get("eval_count"),
                    "eval_s": d.get("eval_duration", 0) / 1e9,
                    "total_s": d.get("total_duration", 0) / 1e9,
                }
                break
    meta["first_token_wall_s"] = first
    if meta.get("eval_count") and meta.get("eval_s"):
        meta["gen_tok_s"] = meta["eval_count"] / meta["eval_s"]
    if meta.get("prompt_eval_count") and meta.get("prompt_eval_s"):
        meta["prompt_tok_s"] = meta["prompt_eval_count"] / meta["prompt_eval_s"]
    return meta


async def piper_load(server: PiperHttpServer, stop: asyncio.Event) -> None:
    text = ("Czarna dziura to obszar w przestrzeni, w ktorym grawitacja jest "
            "tak silna, ze nic nie moze uciec, nawet swiatlo.")
    while not stop.is_set():
        try:
            await server.synthesize(text, voice=PL_VOICE)
        except Exception:
            await asyncio.sleep(0.2)


def summarize(rows: list[dict], res) -> str:
    fts = [r["first_token_wall_s"] for r in rows if r.get("first_token_wall_s")]
    pes = [r["prompt_eval_s"] for r in rows if r.get("prompt_eval_s")]
    gts = [r["gen_tok_s"] for r in rows if r.get("gen_tok_s")]
    def _f(xs):
        return (f"min={min(xs):.2f} mean={statistics.mean(xs):.2f} max={max(xs):.2f}"
                if xs else "n/a")
    return (f"  first_token_wall_s : {_f(fts)}\n"
            f"  prompt_eval_s      : {_f(pes)}\n"
            f"  gen_tok_s          : {_f(gts)}\n"
            f"  llama-server CPU%  : mean {res.llama_server_cpu_pct_mean} "
            f"peak {res.llama_server_cpu_pct_peak}\n"
            f"  system CPU%        : mean {res.cpu_total_pct_mean} peak {res.cpu_total_pct_peak}\n"
            f"  temp max / throttled: {res.temp_c_max}C / {res.throttled_any_hex}\n"
            f"  MemAvailable min / swap max: {res.mem_available_mb_min}MB / {res.swap_used_mb_max}MB")


async def scenario(name: str, *, piper: PiperHttpServer | None, loop_piper: bool) -> None:
    print(f"\n===== SCENARIO {name} =====")
    sampler = ResourceSampler(period_s=0.4)
    sampler.start()
    stop = asyncio.Event()
    load_task = None
    if loop_piper and piper is not None:
        load_task = asyncio.create_task(piper_load(piper, stop))
        await asyncio.sleep(1.0)  # let the load ramp up

    start = time.monotonic()
    rows: list[dict] = []
    for i in range(N):
        row = await asyncio.to_thread(one_first_token)
        rows.append(row)
        print(f"  run {i+1}: first_token={row['first_token_wall_s']}s "
              f"prompt_eval={row.get('prompt_eval_s', 0):.2f}s "
              f"gen_tok_s={row.get('gen_tok_s', 0):.2f} "
              f"load={row.get('load_s', 0):.2f}s")
        # bail on throttling
        w = sampler.window_summary(start, time.monotonic())
        if w.throttled_any_hex and w.throttled_any_hex not in ("0x0", None):
            print(f"  !! throttled={w.throttled_any_hex} — stopping scenario early")
            break
    end = time.monotonic()
    if load_task is not None:
        stop.set()
        await load_task
    sampler.stop()
    print(summarize(rows, sampler.window_summary(start, end)))


async def main() -> None:
    await scenario("A — idle system", piper=None, loop_piper=False)

    server = PiperHttpServer()
    await server.start()
    await server.prewarm()
    await scenario("B — Piper server up, idle", piper=server, loop_piper=False)
    await scenario("C — Piper synthesising concurrently", piper=server, loop_piper=True)
    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
