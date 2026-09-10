# ruff: noqa: E501  (disposable research/benchmark spike — long print/format lines)
"""M2.5B.2 — real-Pi long-session benchmark for ``ProviderWindow``.

Proves the long-session provider-context / KV-cache cliff is eliminated:
every ordinary turn stays warm and each *rollover* (window-maintenance
boundary crossing) is bounded, not the permanent 78-164 s regime the
sliding cap caused.

Real ``gemma4:e4b`` (num_thread=2, keep_alive=30m). No operator, no audio
— drives ``ConversationSession.send`` directly. A deliberately SMALL
``ProviderWindow`` (keep=6 / soft=16 / hard=22 entries) so ~100 turns
cross the rollover boundary ~12 times.

Per turn records: canonical history count + chars, provider message
count, provider prompt tokens (Ollama ``prompt_eval_count``, includes
cached), ``prompt_eval_duration``, TTFT, ``load_duration``, and any
rollover event (background / sync). Mix: normal PL, normal EN, CASE A
interrupt rollback, CASE B interrupted spoken-prefix commit.

Run:  .venv/bin/python -u docs/research/m2_5_bargein/bench_long_session_provider_window.py
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

N_TURNS = int(__import__("os").environ.get("BENCH_N_TURNS", "110"))
COLD_RATE = 11.5  # tok/s prefill @ num_thread=2 (R0029 §M2.5B.2 research)

_PL = [
    "Co to jest {}?", "Dlaczego {} jest ważne?", "Jak działa {}?",
    "Opowiedz krótko o {}.", "Czym różni się {} od innych rzeczy?",
    "Podaj przykład {} z życia.", "Co warto wiedzieć o {}?",
]
_EN = [
    "What is {}?", "Why does {} matter?", "How does {} work?",
    "Tell me briefly about {}.", "Give one everyday example of {}.",
]
_TOPICS = ["czas", "światło", "dźwięk", "woda", "sen", "pamięć", "ruch", "ciepło",
           "kolor", "roślina", "pogoda", "liczba", "mowa", "muzyka", "gwiazda",
           "mrówka", "chleb", "most", "rzeka", "góra", "lód", "sól", "miód",
           "szkło", "papier", "ogień", "wiatr", "cień", "echo", "kurz", "deszcz",
           "śnieg", "burza", "tęcza", "mgła", "piasek", "glina", "metal", "drewno",
           "wełna", "kamień", "liść", "ziarno", "chmura", "fala", "iskra"]

# turns where we simulate a barge-in instead of a normal turn
INTERRUPT_AT = {13: "A", 21: "B", 37: "A", 53: "B", 68: "A", 84: "B", 97: "A"}


def _q(i: int) -> tuple[str, str]:
    topic = _TOPICS[i % len(_TOPICS)]
    if i % 3 == 0:
        return _EN[i % len(_EN)].format(topic), "en"
    return _PL[i % len(_PL)].format(topic), "pl"


async def _turn(session, i: int, kind: str | None) -> dict:
    prov = session.provider
    w = session.provider_window
    prov.last_metrics = None
    text, lang = _q(i)
    roll_bg_before = w.rollovers_background
    roll_sync_before = w.rollovers_sync
    base_before = w.base

    t0 = time.monotonic()
    first = None
    chunks: list[str] = []
    tok = CancelToken() if kind else None
    gen = session.send(text, response_mode=ResponseMode.VOICE,
                       response_language=lang, cancel_token=tok)
    async for ch in gen:
        chunks.append(ch)
        if first is None:
            first = time.monotonic() - t0
        if kind and len(chunks) >= 4:
            break
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
    else:
        outcome = None

    m = prov.last_metrics or {}
    pe_ms = round(m.get("prompt_eval_duration", 0) / 1e6, 1)
    pec = m.get("prompt_eval_count")
    ld_ms = round(m.get("load_duration", 0) / 1e6, 1)
    cold_tokens_est = round(pe_ms / 1000 * COLD_RATE) if pe_ms else None
    reused_est = (pec - cold_tokens_est) if (pec and cold_tokens_est is not None) else None
    hist_chars = sum(len(t.content) for t in session.history)
    rollover = None
    if w.rollovers_sync > roll_sync_before:
        rollover = "sync"
    elif w.rollovers_background > roll_bg_before:
        rollover = "background"
    rec = {
        "turn": i, "kind": kind or "normal", "lang": lang,
        "interrupt_outcome": outcome,
        "canonical_history_entries": len(session.history),
        "canonical_history_chars": hist_chars,
        "provider_base_before": base_before, "provider_base_after": w.base,
        "provider_window_entries": w.window_entries(len(session.history)),
        "provider_prompt_tokens": pec,          # Ollama prompt_eval_count (incl cached)
        "prompt_eval_ms": pe_ms,
        "reused_prefix_tokens_est": reused_est,
        "cold_tokens_est": cold_tokens_est,
        "ttft_s": round(first, 2) if first else None,
        "load_ms": ld_ms,
        "rollover": rollover,
    }
    return rec


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5B.2 long-session provider-window benchmark — {ts}  (N_TURNS={N_TURNS})")
    session = build_default_session()
    session.provider_window = ProviderWindow(keep_entries=6, soft_entries=16, hard_entries=22)
    p = session.provider
    print(f"model={p.describe().model} num_thread={p._num_thread} keep_alive={p._keep_alive}")
    print(f"window: keep={session.provider_window.keep_entries} "
          f"soft={session.provider_window.soft_entries} "
          f"hard={session.provider_window.hard_entries}")
    await warm_up_session(session)
    print("warm-up done\n")

    rows: list[dict] = []
    for i in range(1, N_TURNS + 1):
        kind = INTERRUPT_AT.get(i)
        r = await _turn(session, i, kind)
        rows.append(r)
        tag = f" [INT:{kind}->{r['interrupt_outcome']}]" if kind else ""
        roll = f"  <<{r['rollover'].upper()} ROLLOVER base {r['provider_base_before']}->{r['provider_base_after']}>>" if r["rollover"] else ""
        print(f"  t{i:3d}{tag:22s} ttft={r['ttft_s']}s  pe={r['provider_prompt_tokens']} "
              f"({r['prompt_eval_ms']}ms, ~{r['reused_prefix_tokens_est']} reused)  "
              f"histE={r['canonical_history_entries']} winE={r['provider_window_entries']} "
              f"load={r['load_ms']}ms{roll}")

    # analysis
    warm = [r for r in rows if not r["rollover"] and r["kind"] == "normal" and r["ttft_s"]]
    roll_rows = [r for r in rows if r["rollover"]]
    third = max(1, len(rows) // 3)

    def _ttfts(rr):
        return [r["ttft_s"] for r in rr if r["ttft_s"]]

    def _stat(xs):
        xs = [x for x in xs if x is not None]
        if not xs:
            return None
        return {"n": len(xs), "mean": round(statistics.mean(xs), 2),
                "p50": round(statistics.median(xs), 2), "max": round(max(xs), 2)}

    over_15 = [r for r in rows if r["ttft_s"] and r["ttft_s"] > 15]
    analysis = {
        "turns": len(rows),
        "rollovers": {"sync": session.provider_window.rollovers_sync,
                      "background": session.provider_window.rollovers_background,
                      "turns": [r["turn"] for r in roll_rows]},
        "ttft_early_warm": _stat(_ttfts(warm[:third])),
        "ttft_mid_warm": _stat(_ttfts(warm[third:2 * third])),
        "ttft_late_warm": _stat(_ttfts(warm[2 * third:])),
        "ttft_all_warm": _stat(_ttfts(warm)),
        "ttft_rollover_turns": _stat(_ttfts(roll_rows)),
        "max_warm_ttft_s": max(_ttfts(warm)) if warm else None,
        "turns_over_15s": [{"turn": r["turn"], "ttft_s": r["ttft_s"], "rollover": r["rollover"],
                            "kind": r["kind"]} for r in over_15],
        "canonical_history_final_entries": len(session.history),
        "canonical_history_final_chars": sum(len(t.content) for t in session.history),
        "provider_window_final": session.provider_window.snapshot(),
    }
    out = {"ts": ts, "n_turns": N_TURNS, "rows": rows, "analysis": analysis}
    (HERE / f"bench_long_session_provider_window_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print("\n" + "=" * 70)
    for k, v in analysis.items():
        print(f"  {k}: {v}")
    print(f"-> bench_long_session_provider_window_{ts}.json")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
