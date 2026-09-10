# ruff: noqa: E501  (disposable research spike — long print/format lines)
"""M2.5B.2 research — Ollama / llama.cpp KV-cache behaviour for a long
voice conversation, to pick a prefix-stable provider-context strategy.

RESEARCH SPIKE (disposable). Real ``gemma4:e4b`` via the canonical
``LocalModelProvider`` (num_thread=2, keep_alive=30m). No operator, no
audio. Drives the provider directly with hand-built ``ProviderMessage``
lists so the exact prompt tokens are controlled.

Questions:

  Q1  BASELINE — pure append (window base fixed at 0): does every turn
      stay ~warm (prompt_eval_duration small) as history grows?
  Q2  CLIFF — advancing the window base by one turn (the M2.5B.1 slide):
      how cold is the next turn, and does it stay cold?
  Q3  SYNC ROLLOVER — advancing base by a stride in one step: cold cost.
  Q4  BACKGROUND PRE-WARM + CUTOVER — send [system]+history[base':] with
      num_predict=1 (warm the new prefix), THEN a real turn on that new
      window: is the real turn warm? (does pre-warm -> cutover work?)
  Q5  INTERVENING EVICTION — pre-warm new window, then run ONE real turn
      on the OLD window, then a real turn on the new window: is the new
      window still warm, or did the intervening turn evict the pre-warm?
  Q6  THREADS FOR PREFILL — a cold eval of the same prompt with
      num_thread=4 vs 2: faster prefill? does changing num_thread reload
      the model (load_duration)?
  Q7  SMALL COMPACTED CONTEXT — [system] + ~250-token recap + last 6
      turns, cold: is a small summarised context's cold eval tolerable
      (< ~15 s)?

Writes research_provider_context_kv_<ts>.json + prints a verdict.
Run:  .venv/bin/python -u docs/research/m2_5_bargein/research_provider_context_kv.py
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

from nexa.config import load_persona, local_provider_settings_from_env  # noqa: E402
from nexa.conversation.language import language_directive  # noqa: E402
from nexa.conversation.response_mode import voice_response_directive  # noqa: E402
from nexa.providers.base import CancelToken, ProviderMessage  # noqa: E402
from nexa.providers.ollama import LocalModelProvider  # noqa: E402

PERSONA = load_persona()
SETTINGS = local_provider_settings_from_env()

# --- synthetic but realistic-length bilingual conversation -----------------
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
           "wiatr", "cień", "echo", "kurz"]


def _turn_msgs(i: int) -> list[ProviderMessage]:
    """One canonical turn (user + lang directive + assistant), ~120-160 tok."""
    topic = _TOPICS[i % len(_TOPICS)]
    if i % 2 == 0:
        return [
            ProviderMessage("user", _PL_Q.format(topic)),
            ProviderMessage("system", language_directive("pl")),
            ProviderMessage("assistant", _PL_A.format(topic.capitalize())),
        ]
    return [
        ProviderMessage("user", _EN_Q.format(topic)),
        ProviderMessage("system", language_directive("en")),
        ProviderMessage("assistant", _EN_A.format(topic.capitalize())),
    ]


def _prefix() -> list[ProviderMessage]:
    return [
        ProviderMessage("system", PERSONA.system),
        ProviderMessage("system", voice_response_directive()),
    ]


HISTORY = [_turn_msgs(i) for i in range(80)]          # 80 canonical turns
NEW_USER = ProviderMessage("user", _PL_Q.format("kometach"))
NEW_LANG = ProviderMessage("system", language_directive("pl"))


def _ctx(base: int, upto: int) -> list[ProviderMessage]:
    msgs = list(_prefix())
    for t in HISTORY[base:upto]:
        msgs.extend(t)
    return msgs


async def _one(provider, msgs, *, label, num_predict=64, cancel_after=None):
    opts = replace(PERSONA.options, num_predict=num_predict)
    provider.last_metrics = None
    tok = CancelToken()
    t0 = time.monotonic()
    ttft = None
    n = 0
    gen = provider.generate(msgs, opts, cancel_token=tok)
    async for _ in gen:
        n += 1
        if ttft is None:
            ttft = time.monotonic() - t0
        if cancel_after is not None and (time.monotonic() - t0) >= cancel_after:
            tok.cancel()
            break
        if n >= num_predict:
            break
    try:
        await gen.aclose()
    except Exception:  # noqa: BLE001
        pass
    m = provider.last_metrics or {}
    pe_ms = round(m.get("prompt_eval_duration", 0) / 1e6, 1)
    pec = m.get("prompt_eval_count")
    ld_ms = round(m.get("load_duration", 0) / 1e6, 1)
    rate = round(pec / (pe_ms / 1000), 1) if pec and pe_ms else None
    rec = {
        "label": label, "msgs": len(msgs),
        "ttft_s": round(ttft, 2) if ttft else None,
        "prompt_eval_count": pec, "prompt_eval_ms": pe_ms,
        "prefill_tok_per_s": rate, "load_ms": ld_ms,
    }
    print(f"  {label:42s} ttft={rec['ttft_s']}s  pe={pec} ({pe_ms}ms, {rate} tok/s)  load={ld_ms}ms")
    return rec


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5B.2 provider-context / KV-cache research — {ts}")
    p2 = LocalModelProvider(model=SETTINGS.model, base_url=SETTINGS.base_url,
                            keep_alive=SETTINGS.keep_alive, num_thread=2)
    p4 = LocalModelProvider(model=SETTINGS.model, base_url=SETTINGS.base_url,
                            keep_alive=SETTINGS.keep_alive, num_thread=4)
    out: dict = {"ts": ts, "persona_chars": len(PERSONA.system)}

    # Q1 BASELINE — pure append, base fixed at 0, grow the window 4 -> 24
    print("\n=== Q1 BASELINE — pure append (base=0) ===")
    q1 = []
    for upto in (4, 8, 12, 16, 20, 24, 28):
        q1.append(await _one(p2, _ctx(0, upto) + [NEW_USER, NEW_LANG],
                             label=f"append upto={upto}"))
    out["q1_baseline_append"] = q1

    # Q2 CLIFF — from a warm base=0/upto=28, advance base by ONE, twice
    print("\n=== Q2 CLIFF — advance base by 1 ===")
    q2 = []
    for base in (1, 2, 3):
        q2.append(await _one(p2, _ctx(base, 28) + [NEW_USER, NEW_LANG],
                             label=f"slide base={base} upto=28"))
    out["q2_slide_by_one"] = q2

    # Q3 SYNC ROLLOVER — big jump in one step (base 3 -> 24), window ~ last 24
    print("\n=== Q3 SYNC ROLLOVER — base jump to 24 (window = last ~24 turns) ===")
    out["q3_sync_rollover"] = [
        await _one(p2, _ctx(24, 48) + [NEW_USER, NEW_LANG], label="cold window[24:48]")
    ]

    # Q4 BACKGROUND PRE-WARM + CUTOVER
    print("\n=== Q4 PRE-WARM (num_predict=1) then real turn on the SAME window ===")
    prewarm = await _one(p2, _ctx(50, 74), label="PREWARM window[50:74] np=1", num_predict=1)
    real = await _one(p2, _ctx(50, 74) + [NEW_USER, NEW_LANG],
                      label="REAL turn on prewarmed window[50:74]")
    out["q4_prewarm_then_real"] = {"prewarm": prewarm, "real_turn": real}

    # Q5 INTERVENING EVICTION
    print("\n=== Q5 pre-warm window B, run a turn on window A, then a turn on B ===")
    pwB = await _one(p2, _ctx(52, 76), label="PREWARM window[52:76] np=1", num_predict=1)
    interv = await _one(p2, _ctx(0, 30) + [NEW_USER, NEW_LANG],
                        label="intervening turn on OLD window[0:30]")
    backB = await _one(p2, _ctx(52, 76) + [NEW_USER, NEW_LANG],
                       label="back to window[52:76] (evicted?)")
    out["q5_intervening_eviction"] = {"prewarmB": pwB, "intervening_A": interv, "back_to_B": backB}

    # Q6 THREADS FOR PREFILL — cold eval nt=2 vs nt=4 (fresh window each)
    print("\n=== Q6 cold prefill: num_thread 2 vs 4 ===")
    c2 = await _one(p2, _ctx(26, 50) + [NEW_USER, NEW_LANG], label="cold window[26:50] nt=2")
    c4 = await _one(p4, _ctx(28, 52) + [NEW_USER, NEW_LANG], label="cold window[28:52] nt=4")
    c4b = await _one(p4, _ctx(28, 52) + [NEW_USER, NEW_LANG], label="warm window[28:52] nt=4 (reload check)")
    out["q6_threads_prefill"] = {"nt2_cold": c2, "nt4_cold": c4, "nt4_warm": c4b}

    # Q7 SMALL COMPACTED CONTEXT — system + ~250 tok recap + last 6 turns
    print("\n=== Q7 small compacted context cold ===")
    recap = ProviderMessage("system", (
        "Dotychczasowa rozmowa (skrót): użytkownik pytał po polsku i angielsku o "
        "codzienne zjawiska — czas, światło, dźwięk, wodę, sen, pamięć, ruch, ciepło, "
        "kolory, rośliny, pogodę, liczby, mowę i muzykę. NeXa odpowiadała krótko, "
        "podkreślając obserwację, kontekst, skalę i konsekwencje. Ton: rzeczowy, "
        "przyjazny, dwujęzyczny zależnie od języka pytania."
    ))
    small = [*_prefix(), recap, *sum(HISTORY[74:80], []), NEW_USER, NEW_LANG]
    out["q7_small_compacted"] = [await _one(p2, small, label="cold small compacted ctx")]

    (HERE / f"research_provider_context_kv_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print("\n" + "=" * 64)
    print("VERDICT INPUTS:")
    print(f"  Q1 append last:      {q1[-1]['prompt_eval_ms']}ms  ({q1[-1]['prefill_tok_per_s']} tok/s)")
    print(f"  Q2 slide-by-1:       {[r['prompt_eval_ms'] for r in q2]} ms")
    print(f"  Q3 sync rollover:    {out['q3_sync_rollover'][0]['prompt_eval_ms']} ms")
    print(f"  Q4 prewarm->real:    prewarm {prewarm['prompt_eval_ms']}ms  real {real['prompt_eval_ms']}ms  ttft {real['ttft_s']}s")
    print(f"  Q5 back-to-B:        {backB['prompt_eval_ms']}ms  (prewarmB {pwB['prompt_eval_ms']}ms, intervenedA {interv['prompt_eval_ms']}ms)")
    print(f"  Q6 nt2 {c2['prompt_eval_ms']}ms / nt4 {c4['prompt_eval_ms']}ms  load nt4={c4['load_ms']}ms")
    print(f"  Q7 small ctx cold:   {out['q7_small_compacted'][0]['prompt_eval_ms']} ms")
    print(f"-> research_provider_context_kv_{ts}.json")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
