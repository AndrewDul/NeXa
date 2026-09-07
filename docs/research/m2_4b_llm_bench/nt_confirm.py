"""Focused confirmation: does num_thread < 4 speed up gemma4:e4b decode?
5 warm reps per variant, back to back, same 3 rotating prompts, voice
policy. Writes bench_serving_confirm_20260907.json."""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path.insert(0, str(B.parents[2] / "src"))
sys.path.insert(0, str(B))

from bench_llm import chat, ollama_stop, warm_up, wire_messages  # noqa: E402

from nexa.conversation.response_mode import ResponseMode  # noqa: E402

M = "gemma4:e4b"
PROMPTS = [
    wire_messages([], p, ResponseMode.VOICE)
    for p in (
        "Opowiedz krótko o fotosyntezie.",
        "Wyjaśnij krótko, czym jest inflacja.",
        "Podaj trzy ciekawostki o oceanach.",
    )
]
VARIANTS = [
    ("baseline", {}),
    ("num_thread=3", {"num_thread": 3}),
    ("num_thread=2", {"num_thread": 2}),
    ("num_thread=1", {"num_thread": 1}),
    ("baseline_again", {}),
]


def main() -> None:
    warm_up(M)
    out = []
    for name, opts in VARIANTS:
        reps = []
        for rep in range(5):
            r = chat(M, PROMPTS[rep % len(PROMPTS)], keep_alive="8m", extra_opts=opts)
            reps.append({"tok_s": r["tok_s"], "gen_chars_s": r["gen_chars_s"],
                         "gen_chars": r["gen_chars"], "ttft_s": r["ttft_s"]})
            print(f"{name} rep{rep}: tok/s={r['tok_s']} ch/s={r['gen_chars_s']} "
                  f"chars={r['gen_chars']}", flush=True)
        tk = st.mean(x["tok_s"] for x in reps if x["tok_s"])
        ch = st.mean(x["gen_chars_s"] for x in reps)
        out.append({"variant": name, "opts": opts, "tok_s_mean": round(tk, 2),
                    "chars_s_mean": round(ch, 2), "reps": reps})
        print(f"  => {name}: tok/s {tk:.2f}  chars/s {ch:.2f}", flush=True)
    (B / "bench_serving_confirm_20260907.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    ollama_stop(M)
    print("NT_CONFIRM DONE", flush=True)


if __name__ == "__main__":
    main()
