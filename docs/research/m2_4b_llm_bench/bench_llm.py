"""M2.4B.3.4 — Local LLM serving & voice performance benchmark (throwaway).

Talks straight to Ollama ``/api/chat`` (so the ``done``-chunk metrics
``load_duration`` / ``prompt_eval_*`` / ``eval_*`` are captured — the
``LocalModelProvider`` drops them), but builds the wire messages with the
REAL production path: ``ConversationContext.build(...).to_provider_messages(
response_mode=ResponseMode.VOICE)``. So every turn carries the exact
persona + B.3.3 voice directive + per-turn language directive NeXa uses.

Per turn it records: TTFT (first content byte), wall, generated chars,
Ollama ``eval_count`` / ``eval_duration`` (→ tok/s), ``prompt_eval_*``,
``load_duration``, plus MemAvailable / CPU temp / throttled. tok/s is
``eval_count / eval_duration``; **generated chars/s** is
``generated_chars / eval_duration`` — the cross-language metric R0018's
15.9 chars/s target is stated in.

Sets:
  core     — SHORT / FOLLOW-UP / SCIENCE / REASONING + a 5-turn thread,
             PL and EN, voice policy. The realistic NeXa workload.
  fixed    — one fixed ``num_predict`` prompt, PL and EN, N repeats:
             raw generation-throughput comparison, answer length equal.
  serving  — gemma4:e4b only: baseline vs num_thread / num_batch /
             num_ctx / keep_alive variations. Serving-config sweep.
  science  — deterministic science-truth prompts (relativity/mass, boiling
             point, 10% brain, …), PL and EN, voice policy.

Usage:
  python bench_llm.py --models gemma4:e4b --set core --cold --out out.json
  python bench_llm.py --models gemma4:e2b,qwen3:4b-instruct --set core --out out.json
  python bench_llm.py --models gemma4:e4b --set serving --out serving.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path("/home/devdul/Projects/NeXa_IkiGai")
sys.path.insert(0, str(REPO / "src"))

from nexa.config import load_persona  # noqa: E402
from nexa.conversation.context import ConversationContext  # noqa: E402
from nexa.conversation.response_mode import ResponseMode  # noqa: E402
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
PERSONA = load_persona()
OPT = PERSONA.options  # num_ctx 8192, temp 0.7, top_p 0.8, top_k 20, rp 1.05, num_predict 200


# --------------------------------------------------------------------------- #
# system probes
# --------------------------------------------------------------------------- #

def mem_avail_mb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024.0
    return -1.0


def temp_c() -> float:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000.0
    except Exception:
        return -1.0


def throttled() -> str:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return out.split("=")[-1] if "=" in out else out
    except Exception:
        return "n/a"


def ollama_stop(model: str) -> None:
    subprocess.run(["ollama", "stop", model], capture_output=True, text=True, timeout=60)


# --------------------------------------------------------------------------- #
# wire messages — real production path
# --------------------------------------------------------------------------- #

def wire_messages(history: list[ConversationTurn], user_text: str,
                  response_mode: ResponseMode) -> list[dict]:
    turns = [*history, ConversationTurn(role=Role.USER, content=user_text)]
    ctx = ConversationContext.build(PERSONA.system, turns)
    pmsgs = ctx.to_provider_messages(response_mode=response_mode)
    return [{"role": m.role, "content": m.content} for m in pmsgs]


# --------------------------------------------------------------------------- #
# one Ollama call
# --------------------------------------------------------------------------- #

def chat(model: str, messages: list[dict], *, keep_alive: str = "8m",
         num_predict: int | None = None, extra_opts: dict | None = None) -> dict:
    opts = {
        "num_ctx": OPT.num_ctx, "temperature": OPT.temperature, "top_p": OPT.top_p,
        "top_k": OPT.top_k, "repeat_penalty": OPT.repeat_penalty,
        "num_predict": OPT.num_predict if num_predict is None else num_predict,
    }
    if extra_opts:
        opts.update(extra_opts)
    body = {"model": model, "messages": messages, "stream": True,
            "keep_alive": keep_alive, "think": False, "options": opts}
    req = urllib.request.Request(f"{OLLAMA}/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.monotonic()
    ttft = None
    parts: list[str] = []
    done: dict = {}
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.strip()
            if not line:
                continue
            ch = json.loads(line)
            c = ch.get("message", {}).get("content", "")
            if c:
                if ttft is None:
                    ttft = time.monotonic() - t0
                parts.append(c)
            if ch.get("done"):
                done = ch
    wall = time.monotonic() - t0
    text = "".join(parts)
    ev_n = done.get("eval_count") or 0
    ev_s = (done.get("eval_duration") or 0) / 1e9
    pe_n = done.get("prompt_eval_count") or 0
    pe_s = (done.get("prompt_eval_duration") or 0) / 1e9
    ld_s = (done.get("load_duration") or 0) / 1e9
    return {
        "text": text,
        "gen_chars": len(text),
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "wall_s": round(wall, 3),
        "load_duration_s": round(ld_s, 3),
        "prompt_eval_count": pe_n,
        "prompt_eval_duration_s": round(pe_s, 3),
        "prompt_eval_tok_s": round(pe_n / pe_s, 2) if pe_s > 0 else None,
        "eval_count": ev_n,
        "eval_duration_s": round(ev_s, 3),
        "tok_s": round(ev_n / ev_s, 2) if ev_s > 0 else None,
        # generated chars per second of pure generation (eval) time — the
        # cross-language metric. Falls back to wall-gen if eval_duration missing.
        "gen_chars_s": round(len(text) / ev_s, 2) if ev_s > 0
        else (round(len(text) / max(wall - (ttft or 0), 1e-6), 2)),
        "done_reason": done.get("done_reason"),
        "truncated_num_predict": done.get("done_reason") == "length",
    }


# --------------------------------------------------------------------------- #
# test sets
# --------------------------------------------------------------------------- #

CORE = {
    "pl": {
        "short":     ["Co to jest czarna dziura?"],
        "followup":  ["Co to jest czarna dziura?", "Jak ona powstaje?"],
        "science":   ["Dlaczego nic mającego masę nie może przekroczyć prędkości światła?"],
        "reasoning": ["Jest teraz 14:50. Spotkanie trwa 95 minut. O której się skończy? "
                      "Odpowiedz krótko i pokaż rozumowanie."],
        "thread": [
            "Planuję w sobotę upiec chleb pierwszy raz. Od czego zacząć?",
            "Mam mąkę pszenną chlebową i trochę żytniej. Której użyć?",
            "Nie mam wagi kuchennej. Poradzę sobie?",
            "Ile mniej więcej to zajmie od początku do końca?",
            "Przypomnij mi, jaką mąkę mam w domu?",
        ],
    },
    "en": {
        "short":     ["What is a black hole?"],
        "followup":  ["What is a black hole?", "How does it form?"],
        "science":   ["Why can't an object with mass exceed the speed of light?"],
        "reasoning": ["It's now 2:50 pm. A meeting lasts 95 minutes. What time does it end? "
                      "Answer briefly and show your reasoning."],
        "thread": [
            "I'm going to bake bread for the first time on Saturday. Where do I start?",
            "I have bread wheat flour and some rye flour. Which should I use?",
            "I don't own a kitchen scale. Will I manage?",
            "Roughly how long will the whole thing take start to finish?",
            "Remind me which flour I have at home?",
        ],
    },
}

# deterministic science-truth checks. `good` / `bad` are scoring notes only.
SCIENCE = [
    {"id": "relativity_mass",
     "pl": "Czy masa obiektu naprawdę rośnie, gdy przyspiesza on do prędkości bliskiej "
           "prędkości światła? Wyjaśnij dokładnie.",
     "en": "Does an object's mass really increase as it accelerates close to the speed "
           "of light? Explain in detail.",
     "good": "invariant/rest mass unchanged; energy & momentum grow without bound; "
             "'relativistic mass' is an outdated framing",
     "bad": "relativistic mass increases infinitely / mass becomes infinite"},
    {"id": "boiling_point",
     "pl": "Czy woda zawsze wrze w 100 stopniach Celsjusza?",
     "en": "Does water always boil at 100 degrees Celsius?",
     "good": "only at ~1 atm; lower pressure (altitude) lowers it; higher pressure raises it",
     "bad": "yes, always 100 C"},
    {"id": "kg_iron_feathers",
     "pl": "Co jest cięższe: kilogram żelaza czy kilogram pierza?",
     "en": "Which is heavier: a kilogram of iron or a kilogram of feathers?",
     "good": "equal — both one kilogram", "bad": "iron is heavier"},
    {"id": "ten_percent_brain",
     "pl": "Czy to prawda, że człowiek używa tylko 10 procent swojego mózgu?",
     "en": "Is it true that humans only use 10 percent of their brain?",
     "good": "myth / false; essentially all of the brain is used", "bad": "yes, true"},
    {"id": "sky_blue",
     "pl": "Dlaczego niebo jest niebieskie?",
     "en": "Why is the sky blue?",
     "good": "Rayleigh scattering; shorter (blue) wavelengths scatter more",
     "bad": "reflection of the oceans"},
    {"id": "sun_colour",
     "pl": "Jakiego koloru naprawdę jest Słońce?",
     "en": "What colour is the Sun really?",
     "good": "essentially white (emits all visible wavelengths); looks yellow/orange "
             "only through the atmosphere",
     "bad": "it is yellow"},
]

FIXED_PROMPT = {
    "pl": "Opowiedz mi krótko o historii i budowie Zamku Królewskiego na Wawelu.",
    "en": "Tell me briefly about the history and architecture of Wawel Royal Castle.",
}


# --------------------------------------------------------------------------- #
# runners
# --------------------------------------------------------------------------- #

def _snap() -> dict:
    return {"mem_avail_mb": round(mem_avail_mb(), 1), "temp_c": temp_c(),
            "throttled": throttled()}


def run_core(model: str, lang: str, *, keep_alive: str) -> list[dict]:
    rows = []
    for case, prompts in CORE[lang].items():
        history: list[ConversationTurn] = []
        for i, p in enumerate(prompts):
            msgs = wire_messages(history, p, ResponseMode.VOICE)
            pre = _snap()
            r = chat(model, msgs, keep_alive=keep_alive)
            r.update({"model": model, "set": "core", "lang": lang, "case": case,
                      "turn": i + 1, "prompt": p, "pre": pre, "post": _snap(),
                      "ts": datetime.now(UTC).isoformat()})
            rows.append(r)
            print(f"  [{model} {lang} {case} t{i+1}] ttft={r['ttft_s']}s "
                  f"tok/s={r['tok_s']} ch/s={r['gen_chars_s']} chars={r['gen_chars']} "
                  f"{'TRUNC' if r['truncated_num_predict'] else ''}", flush=True)
            history.append(ConversationTurn(role=Role.USER, content=p))
            history.append(ConversationTurn(role=Role.ASSISTANT, content=r["text"]))
    return rows


def run_science(model: str, lang: str, *, keep_alive: str) -> list[dict]:
    rows = []
    for c in SCIENCE:
        msgs = wire_messages([], c[lang], ResponseMode.VOICE)
        r = chat(model, msgs, keep_alive=keep_alive)
        r.update({"model": model, "set": "science", "lang": lang, "case": c["id"],
                  "turn": 1, "prompt": c[lang], "good": c["good"], "bad": c["bad"],
                  "pre": _snap(), "post": _snap(),
                  "ts": datetime.now(UTC).isoformat()})
        rows.append(r)
        print(f"  [{model} {lang} science {c['id']}] ch/s={r['gen_chars_s']} "
              f"chars={r['gen_chars']}", flush=True)
    return rows


def run_fixed(model: str, lang: str, *, reps: int, num_predict: int,
              keep_alive: str) -> list[dict]:
    rows = []
    for i in range(reps):
        msgs = wire_messages([], FIXED_PROMPT[lang], ResponseMode.VOICE)
        r = chat(model, msgs, keep_alive=keep_alive, num_predict=num_predict)
        r.update({"model": model, "set": "fixed", "lang": lang, "case": f"fixed{num_predict}",
                  "turn": i + 1, "prompt": FIXED_PROMPT[lang], "pre": _snap(),
                  "post": _snap(), "ts": datetime.now(UTC).isoformat()})
        rows.append(r)
        print(f"  [{model} {lang} fixed rep{i+1}] ttft={r['ttft_s']}s tok/s={r['tok_s']} "
              f"ch/s={r['gen_chars_s']} chars={r['gen_chars']}", flush=True)
    return rows


SERVING_VARIANTS = [
    ("baseline", {}),
    ("num_thread=4", {"num_thread": 4}),
    ("num_thread=3", {"num_thread": 3}),
    ("num_thread=2", {"num_thread": 2}),
    ("num_batch=256", {"num_batch": 256}),
    ("num_batch=1024", {"num_batch": 1024}),
    ("num_ctx=4096", {"num_ctx": 4096}),
    ("num_ctx=2048", {"num_ctx": 2048}),
]


def run_serving(model: str, *, keep_alive: str, reps: int) -> list[dict]:
    rows = []
    for name, opts in SERVING_VARIANTS:
        for lang in ("pl", "en"):
            for i in range(reps):
                msgs = wire_messages([], CORE[lang]["science"][0], ResponseMode.VOICE)
                r = chat(model, msgs, keep_alive=keep_alive, extra_opts=opts)
                r.update({"model": model, "set": "serving", "lang": lang,
                          "case": name, "turn": i + 1, "opts": opts,
                          "prompt": CORE[lang]["science"][0], "pre": _snap(),
                          "post": _snap(), "ts": datetime.now(UTC).isoformat()})
                rows.append(r)
                print(f"  [{model} serving {name} {lang} rep{i+1}] ttft={r['ttft_s']}s "
                      f"tok/s={r['tok_s']} ch/s={r['gen_chars_s']}", flush=True)
    return rows


def measure_cold(model: str) -> dict:
    ollama_stop(model)
    time.sleep(3)
    pre = _snap()
    msgs = wire_messages([], CORE["pl"]["short"][0], ResponseMode.VOICE)
    r = chat(model, msgs, keep_alive="8m")
    r.update({"model": model, "set": "cold", "lang": "pl", "case": "cold_load",
              "turn": 1, "pre": pre, "post": _snap(),
              "ts": datetime.now(UTC).isoformat()})
    print(f"  [{model} COLD] load={r['load_duration_s']}s ttft={r['ttft_s']}s "
          f"tok/s={r['tok_s']} mem_before={pre['mem_avail_mb']} "
          f"mem_after={r['post']['mem_avail_mb']}", flush=True)
    return r


def warm_up(model: str) -> dict:
    """Load the model AND prime the persona + voice-directive prompt prefix
    into the llama.cpp KV cache, twice, so the measured turns see a warm
    prefix (production reality — the prefix is byte-stable across turns,
    R0009). Returns the 2nd warm-up call's metrics (a clean warm TTFT with
    the prefix already cached but a fresh user tail)."""
    prime = wire_messages([], "Ile jest osiem razy siedem?", ResponseMode.VOICE)
    chat(model, prime, keep_alive="8m", num_predict=8)
    time.sleep(1)
    r = chat(model, wire_messages([], "Powiedz krótko: dzień dobry.",
                                  ResponseMode.VOICE), keep_alive="8m", num_predict=8)
    print(f"  [{model} warm-prime] ttft={r['ttft_s']}s prompt_eval={r['prompt_eval_duration_s']}s "
          f"({r['prompt_eval_count']} tok) tok/s={r['tok_s']}", flush=True)
    time.sleep(1)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="comma-separated ollama tags")
    ap.add_argument("--set", dest="sets", default="core",
                    help="comma list of: core,science,fixed,serving")
    ap.add_argument("--cold", action="store_true", help="measure a cold load first")
    ap.add_argument("--keep-alive", default="8m")
    ap.add_argument("--reps", type=int, default=3, help="fixed/serving repeats")
    ap.add_argument("--fixed-num-predict", type=int, default=180)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    out = Path(args.out)
    all_rows: list[dict] = []
    if out.exists():
        all_rows = json.loads(out.read_text())

    for model in models:
        print(f"\n===== {model} =====", flush=True)
        if args.cold:
            all_rows.append(measure_cold(model))
            out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
        wp = warm_up(model)
        wp.update({"model": model, "set": "warm_prime", "lang": "pl", "case": "warm_ttft",
                   "turn": 1, "pre": _snap(), "post": _snap(),
                   "ts": datetime.now(UTC).isoformat()})
        all_rows.append(wp)
        out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
        for s in sets:
            if s == "core":
                for lang in ("pl", "en"):
                    all_rows += run_core(model, lang, keep_alive=args.keep_alive)
                    out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
            elif s == "science":
                for lang in ("pl", "en"):
                    all_rows += run_science(model, lang, keep_alive=args.keep_alive)
                    out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
            elif s == "fixed":
                for lang in ("pl", "en"):
                    all_rows += run_fixed(model, lang, reps=args.reps,
                                          num_predict=args.fixed_num_predict,
                                          keep_alive=args.keep_alive)
                    out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
            elif s == "serving":
                all_rows += run_serving(model, keep_alive=args.keep_alive, reps=args.reps)
                out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
        ollama_stop(model)
        time.sleep(2)

    out.write_text(json.dumps(all_rows, indent=1, ensure_ascii=False))
    print(f"\nwrote {len(all_rows)} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
