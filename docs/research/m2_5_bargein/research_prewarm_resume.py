# ruff: noqa: E501  (disposable research spike — long print/format lines)
"""M2.5B.2 research #2 — does a CANCELLED/partial background pre-warm
accumulate progress across attempts, or restart each time?

This is the linchpin for the M2.5B.2 design. Q4/Q5 (research #1) proved:
  - a completed pre-warm makes the next real turn ~7 s (cheap cutover)
  - a completed pre-warm SURVIVES an intervening request on a different
    window (Ollama 0.33.2 retains multiple prefixes while they fit in
    num_ctx)
Open question: a full pre-warm is ~110-270 s of cold prefill at ~11 tok/s
(num_thread=2). In a live conversation Ollama is only idle ~10-20 s
between turns. So background pre-warm is only viable if a pre-warm that
is cancelled after ~15-20 s and re-issued later RESUMES from where it
stopped (llama.cpp keeps the partial prefix) rather than restarting.

Plan (real gemma4:e4b, num_thread=2, keep_alive=30m):
  0. warm up a real session; 3 real ``session.send`` turns to confirm
     steady-state pure-append TTFT (~4 s) as a sanity baseline.
  1. attempt A1: pre-warm window B (~30 turns), CANCEL after 20 s.
  2. intervening real turn on a different window A.
  3. attempt A2: pre-warm window B again, CANCEL after 20 s.
  4. intervening real turn on A.
  5. attempt A3: pre-warm window B, let it COMPLETE (num_predict=1),
     record prompt_eval_count / prompt_eval_duration / TTFT.
  6. real turn on window B (num_predict=8), record TTFT.

Interpretation:
  - if A3's prompt_eval_duration << full-cold (~250 s) and roughly
    (full - 2*20 s worth) -> partial progress ACCUMULATES -> background
    pre-warm is viable.
  - if A3 ~= full-cold -> it restarts -> background pre-warm cannot
    complete during a live conversation; need a different fallback.

Writes research_prewarm_resume_<ts>.json + verdict.
Run:  .venv/bin/python -u docs/research/m2_5_bargein/research_prewarm_resume.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from nexa.bootstrap import build_default_session, warm_up_session  # noqa: E402
from nexa.config import load_persona  # noqa: E402
from nexa.conversation.language import language_directive  # noqa: E402
from nexa.conversation.response_mode import voice_response_directive  # noqa: E402
from nexa.providers.base import CancelToken, ProviderMessage  # noqa: E402

PERSONA = load_persona()

_PL_Q = "Opowiedz mi coś ciekawego o {} i wyjaśnij dlaczego to ma znaczenie dla codziennego życia człowieka."
_PL_A = ("{} to temat, który łączy naukę i praktykę. W skrócie: liczy się obserwacja, "
         "cierpliwość i sprawdzanie źródeł. Warto pamiętać o trzech rzeczach — kontekście, "
         "skali i konsekwencjach — bo one zwykle decydują o tym, jak rozumiemy zjawisko.")
_EN_Q = "Tell me something interesting about {} and explain why it matters in ordinary daily life."
_EN_A = ("{} is a topic where science meets everyday practice. The short version: observation, "
         "patience and checking your sources matter most. Keep three things in mind — context, "
         "scale and consequences — since those usually decide how we understand the phenomenon.")
_TOPICS = ["czas", "światło", "dźwięk", "woda", "sen", "pamięć", "ruch", "ciepło", "kolory",
           "rośliny", "pogoda", "liczby", "mowa", "muzyka", "gwiazdy", "mrówki", "chleb",
           "mosty", "rzeki", "góry", "lód", "sól", "miód", "szkło", "papier", "ogień",
           "wiatr", "cień", "echo", "kurz", "deszcz", "śnieg", "burza", "tęcza", "mgła",
           "piasek", "glina", "metal", "drewno", "wełna"]


def _turn(i: int) -> list[ProviderMessage]:
    t = _TOPICS[i % len(_TOPICS)]
    if i % 2 == 0:
        return [ProviderMessage("user", _PL_Q.format(t)),
                ProviderMessage("system", language_directive("pl")),
                ProviderMessage("assistant", _PL_A.format(t.capitalize()))]
    return [ProviderMessage("user", _EN_Q.format(t)),
            ProviderMessage("system", language_directive("en")),
            ProviderMessage("assistant", _EN_A.format(t.capitalize()))]


HIST = [_turn(i) for i in range(90)]
NEW_U = ProviderMessage("user", _PL_Q.format("kometach"))
NEW_L = ProviderMessage("system", language_directive("pl"))


def _prefix() -> list[ProviderMessage]:
    return [ProviderMessage("system", PERSONA.system),
            ProviderMessage("system", voice_response_directive())]


def _ctx(base: int, upto: int) -> list[ProviderMessage]:
    m = _prefix()
    for t in HIST[base:upto]:
        m.extend(t)
    return m


async def _drive(provider, msgs, *, label, num_predict, cancel_after=None):
    """Run a generation to natural completion (num_predict small) OR cancel
    it after ``cancel_after`` s. Captures provider.last_metrics reliably by
    letting the worker reach ``done`` (small num_predict) or by polling
    after cancel."""
    opts = replace(PERSONA.options, num_predict=num_predict)
    provider.last_metrics = None
    tok = CancelToken()
    t0 = time.monotonic()
    ttft = None
    n = 0
    cancelled = False
    gen = provider.generate(msgs, opts, cancel_token=tok)
    try:
        async for _ in gen:
            n += 1
            if ttft is None:
                ttft = time.monotonic() - t0
            if cancel_after is not None and (time.monotonic() - t0) >= cancel_after:
                tok.cancel()
                cancelled = True
                break
    finally:
        try:
            await gen.aclose()
        except Exception:  # noqa: BLE001
            pass
    # let the worker settle so last_metrics is this call's (unless cancelled)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, tok.wait_worker_stopped, 3.0)
    await asyncio.sleep(0.2)
    m = provider.last_metrics or {}
    pe_ms = round(m.get("prompt_eval_duration", 0) / 1e6, 1)
    pec = m.get("prompt_eval_count")
    ld_ms = round(m.get("load_duration", 0) / 1e6, 1)
    rate = round(pec / (pe_ms / 1000), 1) if pec and pe_ms else None
    rec = {"label": label, "msgs": len(msgs), "ttft_s": round(ttft, 2) if ttft else None,
           "cancelled": cancelled, "elapsed_s": round(time.monotonic() - t0, 1),
           "prompt_eval_count": pec, "prompt_eval_ms": pe_ms,
           "prefill_tok_per_s": rate, "load_ms": ld_ms}
    print(f"  {label:38s} ttft={rec['ttft_s']}s elapsed={rec['elapsed_s']}s "
          f"pe={pec} ({pe_ms}ms {rate}tok/s) load={ld_ms} cancelled={cancelled}")
    return rec


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5B.2 research #2 — pre-warm resume/accumulate — {ts}")
    session = build_default_session()
    prov = session.provider
    await warm_up_session(session)
    print("warm-up done")
    out: dict = {"ts": ts}

    # 0. real pure-append sanity baseline
    print("\n=== 0. real session.send pure-append (steady-state sanity) ===")
    base = []
    for q in ("Co to jest światło?", "A dźwięk?", "Dlaczego niebo jest niebieskie?"):
        t0 = time.monotonic()
        first = None
        async for _ in session.send(q):
            if first is None:
                first = time.monotonic() - t0
        m = prov.last_metrics or {}
        r = {"q": q, "ttft_s": round(first, 2), "prompt_eval_ms": round(m.get("prompt_eval_duration", 0)/1e6, 1),
             "prompt_eval_count": m.get("prompt_eval_count")}
        base.append(r)
        print(f"  send {q!r:40s} ttft={r['ttft_s']}s pe={r['prompt_eval_count']} ({r['prompt_eval_ms']}ms)")
    out["baseline_pure_append"] = base

    WB0, WB1 = 60, 90     # window B = last 30 turns
    WA0, WA1 = 0, 34      # window A = a different (older) window

    # full-cold reference for window B would be ~250 s; skip an explicit
    # one to save time — Q3/Q4/Q5 already pinned it at ~11.4 tok/s.

    print("\n=== 1. attempt A1: pre-warm B, cancel after 20 s ===")
    out["a1"] = await _drive(prov, _ctx(WB0, WB1), label="prewarm B (cancel 20s)", num_predict=1, cancel_after=20)

    print("\n=== 2. intervening real turn on window A ===")
    out["interv_1"] = await _drive(prov, _ctx(WA0, WA1) + [NEW_U, NEW_L], label="intervening A #1", num_predict=8)

    print("\n=== 3. attempt A2: pre-warm B, cancel after 20 s ===")
    out["a2"] = await _drive(prov, _ctx(WB0, WB1), label="prewarm B (cancel 20s) #2", num_predict=1, cancel_after=20)

    print("\n=== 4. intervening real turn on window A ===")
    out["interv_2"] = await _drive(prov, _ctx(WA0, WA1) + [NEW_U, NEW_L], label="intervening A #2", num_predict=8)

    print("\n=== 5. attempt A3: pre-warm B to COMPLETION ===")
    out["a3_complete"] = await _drive(prov, _ctx(WB0, WB1), label="prewarm B to completion", num_predict=1)

    print("\n=== 6. real turn on window B (should be cheap if A3 warmed it) ===")
    out["b_real"] = await _drive(prov, _ctx(WB0, WB1) + [NEW_U, NEW_L], label="REAL turn on window B", num_predict=8)

    a3 = out["a3_complete"]
    verdict = {
        "a3_prompt_eval_ms": a3["prompt_eval_ms"],
        "a3_prompt_eval_count": a3["prompt_eval_count"],
        "a3_ttft_s": a3["ttft_s"],
        "b_real_ttft_s": out["b_real"]["ttft_s"],
        "full_cold_ms_estimate": round((a3["prompt_eval_count"] or 3000) / 11.4 * 1000, 0),
    }
    if a3["prompt_eval_ms"] and verdict["full_cold_ms_estimate"]:
        verdict["a3_fraction_of_full_cold"] = round(a3["prompt_eval_ms"] / verdict["full_cold_ms_estimate"], 2)
        verdict["progress_accumulates"] = a3["prompt_eval_ms"] < 0.75 * verdict["full_cold_ms_estimate"]
    out["verdict"] = verdict

    (HERE / f"research_prewarm_resume_{ts}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("\n" + "=" * 64)
    print("VERDICT:")
    for k, v in verdict.items():
        print(f"  {k}: {v}")
    print(f"-> research_prewarm_resume_{ts}.json")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
