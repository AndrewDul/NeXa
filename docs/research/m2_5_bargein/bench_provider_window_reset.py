# ruff: noqa: E501
"""M2.5B.2 — real-Pi measurement of the ProviderWindow reset cost vs
``keep_entries``, and a long multi-reset session at the shipped default.

Real ``gemma4:e4b`` (num_thread=2, keep_alive=30m). Drives
``ConversationSession.send`` directly. Small window (soft=16 / hard=22) so
resets are frequent. Phases:

  Phase A  keep_entries=4  — grow ~20 turns, cross ~2 resets  (CANDIDATE B)
  Phase B  keep_entries=2  — continue ~16 turns, cross ~2 resets (CANDIDATE B)
  Phase C  keep_entries=0  — continue to >=100 total turns, several resets
                             (the SHIPPED default — the long-session proof)

num_predict=24 (2x the first benchmark's stub replies, keeps runtime sane).
Mix: normal PL / normal EN / CASE-A rollback / CASE-B spoken-prefix.

Per turn: canonical history entries+chars, provider base/window entries,
Ollama prompt_eval_count (incl. cached) + prompt_eval_duration, TTFT,
load_duration, reset event.

Run: .venv/bin/python -u docs/research/m2_5_bargein/bench_provider_window_reset.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from nexa.bootstrap import build_default_session, warm_up_session  # noqa: E402
from nexa.conversation import ProviderWindow, ResponseMode  # noqa: E402
from nexa.providers.base import CancelToken  # noqa: E402

COLD_RATE = 11.5

_PL = ["Co to jest {}?", "Dlaczego {} jest wazne?", "Jak dziala {}?",
       "Opowiedz krotko o {}.", "Czym rozni sie {} od reszty?", "Podaj przyklad {}."]
_EN = ["What is {}?", "Why does {} matter?", "How does {} work?", "Tell me briefly about {}."]
_TOPICS = ["czas", "swiatlo", "dzwiek", "woda", "sen", "pamiec", "ruch", "cieplo", "kolor",
           "roslina", "pogoda", "liczba", "mowa", "muzyka", "gwiazda", "mrowka", "chleb",
           "most", "rzeka", "gora", "lod", "sol", "miod", "szklo", "papier", "ogien",
           "wiatr", "cien", "echo", "kurz", "deszcz", "snieg", "burza", "tecza", "mgla",
           "piasek", "glina", "metal", "drewno", "welna", "kamien", "lisc", "ziarno"]


def _q(i: int) -> tuple[str, str]:
    t = _TOPICS[i % len(_TOPICS)]
    if i % 3 == 0:
        return _EN[i % len(_EN)].format(t), "en"
    return _PL[i % len(_PL)].format(t), "pl"


async def _turn(session, i: int, kind: str | None) -> dict:
    prov = session.provider
    w = session.provider_window
    prov.last_metrics = None
    text, lang = _q(i)
    base_before = w.base
    sync_before = w.rollovers_sync
    t0 = time.monotonic()
    first = None
    chunks: list[str] = []
    tok = CancelToken() if kind else None
    gen = session.send(text, response_mode=ResponseMode.VOICE, response_language=lang, cancel_token=tok)
    async for ch in gen:
        chunks.append(ch)
        if first is None:
            first = time.monotonic() - t0
        if kind and len(chunks) >= 4:
            break
    outcome = None
    if kind:
        tok.cancel()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, tok.wait_worker_stopped, 5.0)
        try:
            await gen.aclose()
        except Exception:  # noqa: BLE001
            pass
        spoken = "" if kind == "A" else "".join(chunks).strip()[:120]
        outcome = session.commit_interrupted_turn(spoken).value
    m = prov.last_metrics or {}
    pe_ms = round(m.get("prompt_eval_duration", 0) / 1e6, 1)
    pec = m.get("prompt_eval_count")
    ld = round(m.get("load_duration", 0) / 1e6, 1)
    cold_est = round(pe_ms / 1000 * COLD_RATE) if pe_ms else None
    reused_est = (pec - cold_est) if (pec and cold_est is not None) else None
    return {
        "turn": i, "kind": kind or "normal", "lang": lang, "keep": w.keep_entries,
        "interrupt_outcome": outcome,
        "canon_entries": len(session.history),
        "canon_chars": sum(len(t.content) for t in session.history),
        "base_before": base_before, "base_after": w.base,
        "window_entries": w.window_entries(len(session.history)),
        "prompt_eval_count": pec, "prompt_eval_ms": pe_ms,
        "reused_est": reused_est, "cold_est": cold_est,
        "ttft_s": round(first, 2) if first else None, "load_ms": ld,
        "reset": "sync" if w.rollovers_sync > sync_before else None,
    }


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5B.2 ProviderWindow reset-cost benchmark — {ts}")
    session = build_default_session()
    session.provider_window = ProviderWindow(keep_entries=4, soft_entries=16, hard_entries=22)
    p = session.provider
    print(f"model={p.describe().model} num_thread={p._num_thread} keep_alive={p._keep_alive}")
    await warm_up_session(session)
    print("warm-up done\n")

    rows: list[dict] = []
    interrupts = {13: "A", 19: "B", 34: "A", 47: "B", 61: "A", 78: "B", 95: "A"}
    phase_switches = {21: 2, 39: 0}   # at turn -> set keep_entries
    total = int(__import__("os").environ.get("BENCH_N_TURNS", "112"))
    for i in range(1, total + 1):
        if i in phase_switches:
            session.provider_window.keep_entries = phase_switches[i]
            print(f"  --- keep_entries -> {phase_switches[i]} ---")
        r = await _turn(session, i, interrupts.get(i))
        rows.append(r)
        tag = f"[INT:{r['kind']}->{r['interrupt_outcome']}]" if r["kind"] in ("A", "B") else ""
        rst = f"  <<RESET keep={r['keep']} base {r['base_before']}->{r['base_after']}>>" if r["reset"] else ""
        print(f"  t{i:3d} keep={r['keep']} {tag:26s} ttft={r['ttft_s']}s pe={r['prompt_eval_count']} "
              f"({r['prompt_eval_ms']}ms ~{r['reused_est']}reused) winE={r['window_entries']} "
              f"canonE={r['canon_entries']} load={r['load_ms']}ms{rst}")

    def _ttft(rr):
        return [r["ttft_s"] for r in rr if r["ttft_s"]]

    def _stat(xs):
        xs = [x for x in xs if x is not None]
        return None if not xs else {"n": len(xs), "mean": round(statistics.mean(xs), 2),
                                    "p50": round(statistics.median(xs), 2), "max": round(max(xs), 2)}

    def phase(keep_val, lo, hi):
        seg = [r for r in rows if lo <= r["turn"] <= hi]
        warm = [r for r in seg if not r["reset"] and r["kind"] == "normal"]
        resets = [r for r in seg if r["reset"]]
        return {"keep": keep_val, "turns": f"{lo}-{hi}", "warm_ttft": _stat(_ttft(warm)),
                "reset_ttft": _stat(_ttft(resets)), "reset_turns": [(r["turn"], r["ttft_s"]) for r in resets]}

    warm_all = [r for r in rows if not r["reset"] and r["kind"] == "normal"]
    over10 = [(r["turn"], r["ttft_s"], r["reset"] or r["kind"], r["keep"]) for r in rows if r["ttft_s"] and r["ttft_s"] > 10]
    over15 = [t for t in over10 if t[1] > 15]
    analysis = {
        "total_turns": len(rows),
        "phase_A_keep4": phase(4, 1, 20),
        "phase_B_keep2": phase(2, 21, 38),
        "phase_C_keep0": phase(0, 39, len(rows)),
        "warm_all": _stat(_ttft(warm_all)),
        "resets_total": session.provider_window.rollovers_sync,
        "turns_over_10s": over10,
        "turns_over_15s": over15,
        "max_warm_ttft_s": max(_ttft(warm_all)) if warm_all else None,
        "canon_final_entries": len(session.history),
        "canon_final_chars": sum(len(t.content) for t in session.history),
        "window_final": session.provider_window.snapshot(),
        "load_ms_max": max(r["load_ms"] for r in rows if r["load_ms"] is not None),
    }
    (HERE / f"bench_provider_window_reset_{ts}.json").write_text(json.dumps({"ts": ts, "rows": rows, "analysis": analysis}, ensure_ascii=False, indent=1))
    print("\n" + "=" * 72)
    for k, v in analysis.items():
        print(f"  {k}: {v}")
    print(f"-> bench_provider_window_reset_{ts}.json")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
