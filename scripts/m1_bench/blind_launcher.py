#!/usr/bin/env python3
"""M1 operator blind conversation test — interactive launcher (TEST TOOLING ONLY).

Not production architecture; deletable along with the rest of scripts/m1_bench/
at M1.1. Talks to the local Ollama server, keeps one independent transcript per
blind label (A/B/C), enforces one-model-loaded-at-a-time, and never prints the
real model tag except via the explicit `reveal` subcommand.

Usage (each call is one round-trip; the caller — the operator's chat session —
supplies text via a file to avoid shell-escaping natural-language input):

    python3 blind_launcher.py preflight
    python3 blind_launcher.py start   --label A
    python3 blind_launcher.py send    --label A --text-file msg.txt
    python3 blind_launcher.py end     --label A
    python3 blind_launcher.py reveal
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONA_CASE = os.path.join(ROOT, "scripts", "m1_bench", "cases", "conversation_pl.json")
RESULTS_DIR = os.path.join(ROOT, "docs", "testing", "m1_operator_blind_results")
OLLAMA_HOST = "http://127.0.0.1:11434"

# SEALED — matches docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md §6.
# Not printed anywhere except `reveal`.
SEALED_MAPPING = {
    "A": "gemma4:e4b",
    "B": "qwen3.5:2b",
    "C": "gemma4:e2b",
}


def transcript_path(label: str) -> str:
    return os.path.join(RESULTS_DIR, f"{label}_transcript.json")


def load_persona() -> dict:
    with open(PERSONA_CASE, "r", encoding="utf-8") as f:
        case = json.load(f)
    return {"system": case["system"], "options": dict(case["options"])}


def api_get(path: str) -> dict:
    with urllib.request.urlopen(f"{OLLAMA_HOST}{path}", timeout=15) as resp:
        return json.loads(resp.read())


def api_post(path: str, body: dict, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def loaded_models() -> list:
    try:
        return api_get("/api/ps").get("models", [])
    except (urllib.error.URLError, OSError):
        return []


def has_thinking(model: str) -> bool:
    try:
        info = api_post("/api/show", {"model": model}, timeout=15)
    except (urllib.error.URLError, OSError):
        return False
    return "thinking" in (info.get("capabilities") or [])


def free_mem_kb() -> int:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    return -1


def swap_used_kb() -> int:
    total = free = 0
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("SwapTotal:"):
                total = int(line.split()[1])
            elif line.startswith("SwapFree:"):
                free = int(line.split()[1])
    return total - free


def stale_bench_processes() -> list:
    try:
        out = subprocess.run(
            ["pgrep", "-af", "m1_bench/bench.py"], capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return []
    return [line for line in out.splitlines() if line]


def cmd_preflight(_args) -> int:
    problems = []
    loaded = loaded_models()
    if loaded:
        problems.append(f"model(s) already loaded: {[m.get('name') for m in loaded]}")
    mem = free_mem_kb()
    if mem >= 0 and mem < 2 * 1024 * 1024:
        problems.append(f"low available RAM: {mem // 1024} MB")
    swap = swap_used_kb()
    if swap > 512 * 1024:
        problems.append(f"swap in use: {swap // 1024} MB")
    stale = stale_bench_processes()
    if stale:
        problems.append(f"stale bench process(es): {stale}")
    result = {
        "ok": not problems,
        "problems": problems,
        "free_mem_mb": mem // 1024 if mem >= 0 else None,
        "swap_used_mb": swap // 1024,
    }
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def stop_model(model: str) -> None:
    subprocess.run(
        ["ollama", "stop", model], capture_output=True, text=True, timeout=30
    )


def stop_all_loaded() -> None:
    for m in loaded_models():
        name = m.get("name")
        if name:
            stop_model(name)


def cmd_start(args) -> int:
    label = args.label
    model = SEALED_MAPPING[label]
    os.makedirs(RESULTS_DIR, exist_ok=True)

    pf = json.loads(
        subprocess.run(
            [sys.executable, __file__, "preflight"], capture_output=True, text=True
        ).stdout
        or "{}"
    )
    stop_all_loaded()

    persona = load_persona()
    think_capable = has_thinking(model)

    # Warm the model in before showing "ready" so the operator's real first
    # turn measures normal TTFT, not cold model load.
    warm_body = {
        "model": model,
        "messages": [{"role": "system", "content": persona["system"]}],
        "stream": False,
        "options": persona["options"],
    }
    if think_capable:
        warm_body["think"] = False
    t0 = time.time()
    try:
        api_post("/api/chat", warm_body, timeout=180)
    except (urllib.error.URLError, OSError) as e:
        print(json.dumps({"ok": False, "error": f"warm load failed: {e}"}))
        return 1
    load_s = time.time() - t0

    state = {
        "label": label,
        "model": model,  # SEALED — never printed outside `reveal`
        "system": persona["system"],
        "options": persona["options"],
        "think_forced_false": think_capable,
        "started_at": time.time(),
        "warm_load_seconds": load_s,
        "preflight": pf,
        "messages": [],
        "turn_metrics": [],
    }
    with open(transcript_path(label), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print(json.dumps({"ok": True, "label": label}))
    return 0


def cmd_send(args) -> int:
    label = args.label
    path = transcript_path(label)
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)

    with open(args.text_file, "r", encoding="utf-8") as f:
        user_text = f.read()

    messages = [{"role": "system", "content": state["system"]}]
    for m in state["messages"]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_text})

    body = {
        "model": state["model"],
        "messages": messages,
        "stream": True,
        "options": state["options"],
    }
    if state["think_forced_false"]:
        body["think"] = False

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    t_start = time.time()
    ttft = None
    text_parts = []
    final = {}
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            content = chunk.get("message", {}).get("content", "")
            if content and ttft is None:
                ttft = time.time() - t_start
            if content:
                text_parts.append(content)
            if chunk.get("done"):
                final = chunk
    total_s = time.time() - t_start
    assistant_text = "".join(text_parts).strip()

    eval_count = final.get("eval_count", 0)
    eval_ns = final.get("eval_duration", 0)
    gen_tps = (eval_count / (eval_ns / 1e9)) if eval_ns else None

    state["messages"].append({"role": "user", "content": user_text})
    state["messages"].append({"role": "assistant", "content": assistant_text})
    state["turn_metrics"].append(
        {
            "ttft_s": ttft,
            "total_s": total_s,
            "eval_count": eval_count,
            "gen_tok_s": gen_tps,
        }
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    # Only the raw assistant text goes to stdout — no metrics, no model name.
    sys.stdout.write(assistant_text)
    return 0


def cmd_end(args) -> int:
    label = args.label
    path = transcript_path(label)
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    stop_model(state["model"])
    state["ended_at"] = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    print(json.dumps({"ok": True, "label": label, "turns": len(state["turn_metrics"])}))
    return 0


def cmd_reveal(_args) -> int:
    print(json.dumps(SEALED_MAPPING, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight")

    p_start = sub.add_parser("start")
    p_start.add_argument("--label", choices=["A", "B", "C"], required=True)

    p_send = sub.add_parser("send")
    p_send.add_argument("--label", choices=["A", "B", "C"], required=True)
    p_send.add_argument("--text-file", required=True)

    p_end = sub.add_parser("end")
    p_end.add_argument("--label", choices=["A", "B", "C"], required=True)

    sub.add_parser("reveal")

    args = ap.parse_args()
    return {
        "preflight": cmd_preflight,
        "start": cmd_start,
        "send": cmd_send,
        "end": cmd_end,
        "reveal": cmd_reveal,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
