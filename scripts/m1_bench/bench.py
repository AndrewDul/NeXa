#!/usr/bin/env python3
"""THROWAWAY M1.0 conversation benchmark harness (stdlib only).

NOT production architecture. See scripts/m1_bench/README.md.

Measures, per model/backend:
  - model load time, time-to-first-token (TTFT), generation tokens/sec,
    prompt eval tokens/sec, total wall time per turn
  - RAM (MemAvailable) before/after, CPU temp before/peak, throttled flag
  - full assistant transcript for human quality review

Backends:
  - ollama   : HTTP API at $OLLAMA_HOST or http://127.0.0.1:11434
  - llamacpp : subprocess to a llama-cli binary (--llama-bin, --gguf)

Outputs JSON + Markdown into --out.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


# ----------------------------- system probes -----------------------------

def mem_available_mb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return -1.0


def cpu_temp_c() -> float:
    try:
        v = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return int(v) / 1000.0
    except Exception:
        return -1.0


def throttled_hex() -> str:
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return out.split("=")[-1] if "=" in out else out
    except Exception:
        return "n/a"


def loadavg() -> str:
    try:
        return Path("/proc/loadavg").read_text().split(" ")[0]
    except Exception:
        return "n/a"


# ----------------------------- ollama backend -----------------------------

def ollama_chat_stream(model: str, messages: list[dict], options: dict) -> dict:
    """One streamed /api/chat call. Returns dict with timing + text + peak_temp."""
    body = json.dumps(
        {"model": model, "messages": messages, "stream": True, "options": options}
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    ttft = None
    text_parts: list[str] = []
    final = {}
    peak_temp = cpu_temp_c()
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            if obj.get("message", {}).get("content"):
                if ttft is None:
                    ttft = time.perf_counter() - t0
                text_parts.append(obj["message"]["content"])
                t = cpu_temp_c()
                if t > peak_temp:
                    peak_temp = t
            if obj.get("done"):
                final = obj
    wall = time.perf_counter() - t0
    ns = 1e9
    load_s = final.get("load_duration", 0) / ns
    peval_n = final.get("prompt_eval_count", 0)
    peval_s = final.get("prompt_eval_duration", 0) / ns
    eval_n = final.get("eval_count", 0)
    eval_s = final.get("eval_duration", 0) / ns
    return {
        "text": "".join(text_parts),
        "wall_s": round(wall, 3),
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "load_s": round(load_s, 3),
        "prompt_eval_count": peval_n,
        "prompt_eval_tps": round(peval_n / peval_s, 2) if peval_s > 0 else None,
        "eval_count": eval_n,
        "gen_tps": round(eval_n / eval_s, 2) if eval_s > 0 else None,
        "peak_temp_c": round(peak_temp, 1),
    }


def ollama_unload(model: str) -> None:
    try:
        body = json.dumps({"model": model, "keep_alive": 0}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass
    time.sleep(2)


def ollama_model_info(model: str) -> dict:
    try:
        body = json.dumps({"model": model}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/show", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        det = d.get("details", {})
        return {
            "family": det.get("family"),
            "parameter_size": det.get("parameter_size"),
            "quantization_level": det.get("quantization_level"),
            "context_length": (d.get("model_info") or {}).get(
                f"{det.get('family','')}.context_length"
            ),
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------- llama.cpp backend ----------------------------

def llamacpp_chat(llama_bin: str, gguf: str, messages: list[dict], options: dict) -> dict:
    """Single-shot llama-cli run. Coarser timing than ollama (parses stderr)."""
    sys_txt = "\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = messages[-1]["content"] if messages else ""
    cmd = [
        llama_bin, "-m", gguf, "-no-cnv", "-st",
        "-n", str(options.get("num_predict", 200)),
        "-c", str(options.get("num_ctx", 4096)),
        "--temp", str(options.get("temperature", 0.7)),
        "--top-p", str(options.get("top_p", 0.8)),
        "--top-k", str(options.get("top_k", 20)),
        "-p", (sys_txt + "\n\n" + convo).strip(),
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    wall = time.perf_counter() - t0
    err = proc.stderr
    gen_tps = None
    m = re.search(r"eval time =.*?([\d.]+) tokens per second", err)
    if m:
        gen_tps = float(m.group(1))
    load_s = None
    m2 = re.search(r"load time =\s*([\d.]+) ms", err)
    if m2:
        load_s = round(float(m2.group(1)) / 1000.0, 3)
    return {
        "text": proc.stdout.strip(),
        "wall_s": round(wall, 3),
        "ttft_s": None,
        "load_s": load_s,
        "prompt_eval_count": None,
        "prompt_eval_tps": None,
        "eval_count": options.get("num_predict"),
        "gen_tps": gen_tps,
        "peak_temp_c": cpu_temp_c(),
        "_stderr_tail": err.strip().splitlines()[-25:],
    }


# ------------------------------- run logic -------------------------------

def run_cases(args) -> dict:
    cases = json.loads(Path(args.cases).read_text())
    system_prompt = cases.get("system", "")
    options = dict(cases.get("options", {}))
    if args.num_ctx:
        options["num_ctx"] = args.num_ctx

    result = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "backend": args.backend,
        "model": args.model or args.gguf,
        "case_file": args.cases,
        "case_id": cases.get("id"),
        "language": cases.get("language"),
        "options": options,
        "system_prompt": system_prompt,
        "env": {
            "mem_available_mb_start": round(mem_available_mb(), 1),
            "cpu_temp_c_start": cpu_temp_c(),
            "throttled_start": throttled_hex(),
            "loadavg_start": loadavg(),
        },
        "turns": [],
    }
    if args.backend == "ollama":
        result["model_info"] = ollama_model_info(args.model)
        if not args.keep_loaded:
            ollama_unload(args.model)
        result["env"]["mem_available_mb_before_load"] = round(mem_available_mb(), 1)

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    for i, user_msg in enumerate(cases["turns"], 1):
        messages.append({"role": "user", "content": user_msg})
        print(f"  turn {i}/{len(cases['turns'])}: {user_msg[:60]!r}", file=sys.stderr)
        if args.backend == "ollama":
            r = ollama_chat_stream(args.model, messages, options)
        else:
            r = llamacpp_chat(args.llama_bin, args.gguf, messages, options)
        messages.append({"role": "assistant", "content": r["text"]})
        r["turn"] = i
        r["user"] = user_msg
        r["mem_available_mb"] = round(mem_available_mb(), 1)
        result["turns"].append(r)

    result["env"]["mem_available_mb_end"] = round(mem_available_mb(), 1)
    result["env"]["cpu_temp_c_end"] = cpu_temp_c()
    result["env"]["throttled_end"] = throttled_hex()
    gens = [t["gen_tps"] for t in result["turns"] if t.get("gen_tps")]
    ttfts = [t["ttft_s"] for t in result["turns"] if t.get("ttft_s")]
    result["summary"] = {
        "turns": len(result["turns"]),
        "gen_tps_mean": round(sum(gens) / len(gens), 2) if gens else None,
        "gen_tps_min": min(gens) if gens else None,
        "gen_tps_max": max(gens) if gens else None,
        "ttft_s_mean": round(sum(ttfts) / len(ttfts), 3) if ttfts else None,
        "load_s_first": result["turns"][0]["load_s"] if result["turns"] else None,
        "peak_temp_c": max((t["peak_temp_c"] for t in result["turns"]), default=None),
        "ram_used_by_model_mb": (
            round(
                result["env"].get("mem_available_mb_before_load", 0)
                - min(t["mem_available_mb"] for t in result["turns"]),
                1,
            )
            if args.backend == "ollama" and result["turns"]
            else None
        ),
    }
    return result


def run_perf(args) -> dict:
    prompt = (
        "Explain in about 150 words, in natural English, why sleep matters for "
        "memory. Then give two practical tips."
    )
    options = {
        "temperature": 0.7, "top_p": 0.8, "top_k": 20,
        "num_predict": args.perf_tokens, "num_ctx": args.num_ctx or 4096,
    }
    runs = []
    if args.backend == "ollama" and not args.keep_loaded:
        ollama_unload(args.model)
    for n in range(1, args.perf_runs + 1):
        print(f"  perf run {n}/{args.perf_runs}", file=sys.stderr)
        msgs = [{"role": "user", "content": prompt}]
        if args.backend == "ollama":
            r = ollama_chat_stream(args.model, msgs, options)
        else:
            r = llamacpp_chat(args.llama_bin, args.gguf, msgs, options)
        r["run"] = n
        r["mem_available_mb"] = round(mem_available_mb(), 1)
        runs.append(r)
    gens = [r["gen_tps"] for r in runs if r.get("gen_tps")]
    return {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "perf",
        "backend": args.backend,
        "model": args.model or args.gguf,
        "options": options,
        "model_info": ollama_model_info(args.model) if args.backend == "ollama" else {},
        "runs": runs,
        "summary": {
            "gen_tps_mean": round(sum(gens) / len(gens), 2) if gens else None,
            "gen_tps_all": gens,
            "ttft_s_all": [r.get("ttft_s") for r in runs],
            "load_s_first": runs[0]["load_s"] if runs else None,
            "peak_temp_c": max((r["peak_temp_c"] for r in runs), default=None),
        },
    }


def write_outputs(result: dict, out_dir: str, tag: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tag = re.sub(r"[^A-Za-z0-9_-]", "-", tag)
    json_path = out / f"{safe_tag}_{stamp}.json"
    md_path = out / f"{safe_tag}_{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    lines = [f"# bench {tag}", "", f"- generated: {result.get('started_utc')}",
             f"- backend: {result.get('backend')}", f"- model: {result.get('model')}",
             f"- model_info: `{json.dumps(result.get('model_info', {}), ensure_ascii=False)}`",
             f"- options: `{json.dumps(result.get('options', {}), ensure_ascii=False)}`",
             f"- summary: `{json.dumps(result.get('summary', {}), ensure_ascii=False)}`", ""]
    if "env" in result:
        lines += [f"- env: `{json.dumps(result['env'], ensure_ascii=False)}`", ""]
    if result.get("system_prompt"):
        lines += ["## system prompt", "", "```", result["system_prompt"], "```", ""]
    if "turns" in result:
        lines.append("## transcript\n")
        for t in result["turns"]:
            lines += [
                f"### turn {t['turn']}",
                f"**user:** {t['user']}", "",
                f"**assistant:** {t['text']}", "",
                f"_ttft={t.get('ttft_s')}s gen_tps={t.get('gen_tps')} "
                f"eval_count={t.get('eval_count')} wall={t.get('wall_s')}s "
                f"mem_avail={t.get('mem_available_mb')}MB temp={t.get('peak_temp_c')}C_",
                "",
            ]
    if "runs" in result:
        lines.append("## perf runs\n")
        for r in result["runs"]:
            lines += [f"- run {r['run']}: gen_tps={r.get('gen_tps')} "
                      f"ttft={r.get('ttft_s')}s load={r.get('load_s')}s "
                      f"eval_count={r.get('eval_count')} temp={r.get('peak_temp_c')}C"]
        lines += ["", "### sample output (run 1)", "", "```",
                  result["runs"][0]["text"][:2000], "```", ""]
    md_path.write_text("\n".join(lines))
    print(f"wrote {json_path} and {md_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["ollama", "llamacpp"], default="ollama")
    ap.add_argument("--model", help="ollama model tag")
    ap.add_argument("--llama-bin", default="llama-cli")
    ap.add_argument("--gguf", help="path to gguf for llamacpp backend")
    ap.add_argument("--cases", help="path to a cases json file")
    ap.add_argument("--perf", action="store_true", help="run perf micro-benchmark")
    ap.add_argument("--perf-tokens", type=int, default=200)
    ap.add_argument("--perf-runs", type=int, default=3)
    ap.add_argument("--num-ctx", type=int, default=0)
    ap.add_argument("--keep-loaded", action="store_true")
    ap.add_argument("--out", default="docs/research/m1_bench_results")
    args = ap.parse_args()

    if args.perf:
        res = run_perf(args)
        tag = f"perf_{args.backend}_{(args.model or Path(args.gguf).stem)}".replace(
            ":", "-").replace("/", "-")
        write_outputs(res, args.out, tag)
        return 0
    if not args.cases:
        ap.error("--cases required unless --perf")
    res = run_cases(args)
    tag = f"{res.get('case_id','cases')}_{args.backend}_{(args.model or Path(args.gguf).stem)}".replace(
        ":", "-").replace("/", "-")
    write_outputs(res, args.out, tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
