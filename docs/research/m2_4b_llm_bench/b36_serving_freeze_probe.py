"""M2.4B.3.6 — Production Local Model Serving Freeze: evidence probe.

RESEARCH TOOLING ONLY (deletable with the rest of `docs/research/`). Does
not change any production default. Measures, on the real Pi + real Ollama
(`gemma4:e4b`), the three things B.3.6 productionises:

  1. WARM-UP KV BENEFIT (eviction-controlled). The production warm-up
     (`nexa.bootstrap.warm_up_session`) sends the canonical persona +
     `ResponseMode.VOICE` prefix with EMPTY history (two `system`
     messages, no user text, `num_predict=1`) once. This probe reproduces
     exactly that call, then measures a real first turn's TTFT /
     `prompt_eval_duration` with and without it, each from an evicted
     model. Proves (or disproves) that the warm-up leaves a reusable
     prefix in the llama.cpp KV cache.

  2. num_thread=2 vs default, Piper ACTIVE — a light re-confirm of the
     R0021/R0022 serving finding with fresh B.3.6 numbers.

  3. ACCEPTANCE TURNS headless (num_thread=2, keep_alive=30m), Piper
     ACTIVE — the B.3.6 PL + EN acceptance questions run as one
     accumulating context per language, capturing warm TTFT, generated
     chars/s, tok/s, CPU, temp, throttled, MemAvailable. (STT latency and
     END_OF_TURN->first-audio need a live mic and are collected in the
     operator voice session, not here.)

Writes b36_serving_freeze_probe_<ts>.json + _raw.txt.
"""
from __future__ import annotations

import asyncio
import json
import statistics as st
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path.insert(0, str(B.parents[2] / "src"))
sys.path.insert(0, str(B))

from bench_llm import (  # noqa: E402
    chat,
    mem_avail_mb,
    ollama_stop,
    temp_c,
    throttled,
    wire_messages,
)

from nexa.config import load_persona  # noqa: E402
from nexa.conversation.context import ConversationContext  # noqa: E402
from nexa.conversation.response_mode import ResponseMode  # noqa: E402
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402
from nexa.tts import PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402

MODEL = "gemma4:e4b"
KEEP_ALIVE = "30m"
NUM_THREAD = 2

PL_ACCEPT = ["Co to jest czarna dziura?", "Jak ona powstaje?", "Po co człowiekowi sen?"]
EN_ACCEPT = ["What is a black hole?", "How does it form?", "Why do humans need sleep?"]
NUM_PREDICT_ACCEPT = 90

PIPER_TEXT = (
    "Czarna dziura to obszar czasoprzestrzeni o tak silnej grawitacji, "
    "że nic, nawet światło, nie może się z niej wydostać."
)
_WAV_BYTES_PER_S = 22050 * 2
PIPER_PAUSE_S = 3.0

_RAW: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    _RAW.append(msg)


def production_warmup_messages() -> list[dict]:
    """EXACTLY what `nexa.bootstrap.warm_up_session` puts on the wire:
    the canonical persona + VOICE directive, empty history, no user turn."""
    persona = load_persona()
    ctx = ConversationContext.build(persona.system, [])
    pmsgs = ctx.to_provider_messages(response_mode=ResponseMode.VOICE)
    return [{"role": m.role, "content": m.content} for m in pmsgs]


def evict() -> None:
    ollama_stop(MODEL)
    time.sleep(3.0)


# --------------------------------------------------------------------------- #
# 1. warm-up KV benefit (eviction-controlled)
# --------------------------------------------------------------------------- #

def section_warmup_benefit() -> dict:
    log("\n===== 1. WARM-UP KV BENEFIT (eviction-controlled, Piper idle) =====")
    persona = load_persona()
    warm_msgs = production_warmup_messages()
    warm_opts = {"num_thread": NUM_THREAD}
    out: dict = {"prefix_message_count": len(warm_msgs)}

    # --- COLD: no warm-up. Evicted model -> straight to a real first turn.
    evict()
    q = PL_ACCEPT[0]
    cold = chat(MODEL, wire_messages([], q, ResponseMode.VOICE),
                keep_alive=KEEP_ALIVE, num_predict=NUM_PREDICT_ACCEPT,
                extra_opts=warm_opts)
    log(f"  COLD  (no warm-up)  turn1 PL: ttft={cold['ttft_s']}s  "
        f"load={cold['load_duration_s']}s  prompt_eval={cold['prompt_eval_duration_s']}s "
        f"({cold['prompt_eval_count']} tok)  tok/s={cold['tok_s']}")
    out["cold_no_warmup"] = cold

    # --- WARM: evicted model -> production warm-up call -> real first turn.
    evict()
    w = chat(MODEL, warm_msgs, keep_alive=KEEP_ALIVE, num_predict=1, extra_opts=warm_opts)
    log(f"  warm-up call        : ttft={w['ttft_s']}s  load={w['load_duration_s']}s  "
        f"prompt_eval={w['prompt_eval_duration_s']}s ({w['prompt_eval_count']} tok)  "
        f"discarded_chars={w['gen_chars']}")
    time.sleep(0.5)
    warm = chat(MODEL, wire_messages([], q, ResponseMode.VOICE),
                keep_alive=KEEP_ALIVE, num_predict=NUM_PREDICT_ACCEPT,
                extra_opts=warm_opts)
    log(f"  WARM  (after warm-up) turn1 PL: ttft={warm['ttft_s']}s  "
        f"load={warm['load_duration_s']}s  prompt_eval={warm['prompt_eval_duration_s']}s "
        f"({warm['prompt_eval_count']} tok)  tok/s={warm['tok_s']}")
    out["warmup_call"] = w
    out["warm_after_warmup"] = warm

    if cold["ttft_s"] and warm["ttft_s"]:
        out["ttft_cold_s"] = cold["ttft_s"]
        out["ttft_warm_s"] = warm["ttft_s"]
        out["ttft_saved_s"] = round(cold["ttft_s"] - warm["ttft_s"], 2)
        out["prompt_eval_cold_s"] = cold["prompt_eval_duration_s"]
        out["prompt_eval_warm_s"] = warm["prompt_eval_duration_s"]
        log(f"  => first real turn TTFT: cold {cold['ttft_s']}s -> warm "
            f"{warm['ttft_s']}s  (saved {out['ttft_saved_s']}s);  "
            f"prompt_eval {cold['prompt_eval_duration_s']}s -> "
            f"{warm['prompt_eval_duration_s']}s")
    # sanity: does the persona system prompt actually match what we primed?
    out["persona_id"] = persona.id
    return out


# --------------------------------------------------------------------------- #
# 2 + 3. Piper-active
# --------------------------------------------------------------------------- #

async def piper_loop(server: PiperHttpServer, stop: asyncio.Event, rtfs: list[float]) -> None:
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            wav = await server.synthesize(PIPER_TEXT, voice=PL_VOICE)
        except Exception as exc:  # noqa: BLE001
            rtfs.append(-1.0)
            log(f"  piper synth error: {exc}")
            continue
        wall = time.monotonic() - t0
        audio_s = max(len(wav) - 44, 0) / _WAV_BYTES_PER_S
        if audio_s > 0:
            rtfs.append(round(wall / audio_s, 3))
        await asyncio.sleep(PIPER_PAUSE_S)


def _turn(history: list[ConversationTurn], q: str, opts: dict) -> dict:
    r = chat(MODEL, wire_messages(history, q, ResponseMode.VOICE),
             keep_alive=KEEP_ALIVE, num_predict=NUM_PREDICT_ACCEPT, extra_opts=opts)
    return r


async def section_piper_active() -> dict:
    log("\n===== 2+3. PIPER-ACTIVE (num_thread=2, keep_alive=30m) =====")
    server = PiperHttpServer(PiperHttpConfig(nice=10))
    await server.start()
    await server.prewarm()
    stop = asyncio.Event()
    rtfs: list[float] = []
    loop_task = asyncio.create_task(piper_loop(server, stop, rtfs))
    await asyncio.sleep(2.0)
    loop = asyncio.get_running_loop()

    # -- 2. num_thread=2 vs default, 2 warm turns each (fresh context) --
    reconfirm = {}
    for name, opts in (("default", {}), ("num_thread=2", {"num_thread": NUM_THREAD})):
        # warm the prefix for this variant
        await loop.run_in_executor(None, lambda o=opts: chat(
            MODEL, production_warmup_messages(), keep_alive=KEEP_ALIVE,
            num_predict=1, extra_opts=o))
        tks, chs = [], []
        for q in PL_ACCEPT[:2]:
            r = await loop.run_in_executor(None, lambda o=opts, qq=q: _turn([], qq, o))
            tks.append(r["tok_s"])
            chs.append(r["gen_chars_s"])
            log(f"  [{name}] PL '{q[:28]}': tok/s={r['tok_s']} ch/s={r['gen_chars_s']} "
                f"ttft={r['ttft_s']}s temp={temp_c()}C thr={throttled()}")
        reconfirm[name] = {
            "tok_s_mean": round(st.mean([x for x in tks if x]), 3) if any(tks) else None,
            "chars_s_mean": round(st.mean([x for x in chs if x]), 3) if any(chs) else None,
        }

    # -- 3. acceptance turns, accumulating context, num_thread=2 --
    opts = {"num_thread": NUM_THREAD}
    accept = {}
    for lang, qs in (("pl", PL_ACCEPT), ("en", EN_ACCEPT)):
        # warm prefix once per language block
        await loop.run_in_executor(None, lambda: chat(
            MODEL, production_warmup_messages(), keep_alive=KEEP_ALIVE,
            num_predict=1, extra_opts=opts))
        history: list[ConversationTurn] = []
        turns = []
        for q in qs:
            r = await loop.run_in_executor(
                None, lambda qq=q, h=history: _turn(h, qq, opts)
            )
            history.append(ConversationTurn(role=Role.USER, content=q))
            history.append(ConversationTurn(role=Role.ASSISTANT, content=r["text"]))
            snap = {"temp_c": temp_c(), "throttled": throttled(),
                    "mem_avail_mb": round(mem_avail_mb(), 1)}
            turns.append({"q": q, "ttft_s": r["ttft_s"], "gen_chars": r["gen_chars"],
                          "gen_chars_s": r["gen_chars_s"], "tok_s": r["tok_s"],
                          "prompt_eval_s": r["prompt_eval_duration_s"],
                          "prompt_eval_count": r["prompt_eval_count"],
                          "truncated": r["truncated_num_predict"], **snap})
            log(f"  [{lang}] '{q[:30]}': ttft={r['ttft_s']}s chars={r['gen_chars']} "
                f"ch/s={r['gen_chars_s']} tok/s={r['tok_s']} "
                f"temp={snap['temp_c']}C thr={snap['throttled']} "
                f"mem={snap['mem_avail_mb']}MB trunc={r['truncated_num_predict']}")
        ch = [t["gen_chars_s"] for t in turns if t["gen_chars_s"]]
        tk = [t["tok_s"] for t in turns if t["tok_s"]]
        tf = [t["ttft_s"] for t in turns if t["ttft_s"]]
        warm_tf = tf[1:] if len(tf) > 1 else tf
        accept[lang] = {
            "turns": turns,
            "warm_ttft_s_mean": round(st.mean(warm_tf), 3) if warm_tf else None,
            "gen_chars_s_mean": round(st.mean(ch), 3) if ch else None,
            "tok_s_mean": round(st.mean(tk), 3) if tk else None,
        }

    stop.set()
    await loop_task
    good_rtf = [x for x in rtfs if x > 0]
    piper = {
        "rtf_n": len(good_rtf),
        "rtf_mean": round(st.mean(good_rtf), 3) if good_rtf else None,
        "rtf_max": round(max(good_rtf), 3) if good_rtf else None,
        "synth_errors": sum(1 for x in rtfs if x < 0),
    }
    log(f"  Piper (whole Piper-active phase): RTF mean {piper['rtf_mean']} "
        f"max {piper['rtf_max']} (n={piper['rtf_n']}, err={piper['synth_errors']})")
    await server.stop()
    return {"num_thread_reconfirm": reconfirm, "acceptance": accept, "piper": piper}


async def main() -> None:
    log(f"M2.4B.3.6 serving-freeze probe — {MODEL}, keep_alive={KEEP_ALIVE}, "
        f"num_thread={NUM_THREAD}")
    log(f"start: {datetime.now(UTC).isoformat()}  temp={temp_c()}C thr={throttled()} "
        f"mem={round(mem_avail_mb(),1)}MB")
    s1 = section_warmup_benefit()
    s23 = await section_piper_active()
    ollama_stop(MODEL)
    out = {
        "model": MODEL, "keep_alive": KEEP_ALIVE, "num_thread": NUM_THREAD,
        "ts": datetime.now(UTC).isoformat(),
        "warmup_benefit": s1, "piper_active": s23,
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    (B / f"b36_serving_freeze_probe_{ts}.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    (B / f"b36_serving_freeze_probe_{ts}_raw.txt").write_text("\n".join(_RAW))
    log(f"\nB36_SERVING_FREEZE_PROBE DONE -> b36_serving_freeze_probe_{ts}.json")


if __name__ == "__main__":
    asyncio.run(main())
