"""M2.5B.1 — real-Pi latency / KV-cache / cancellation-overlap measurement.

RESEARCH SPIKE (disposable). No operator, no audio pipeline. Drives the
REAL ``gemma4:e4b`` (via ``build_default_session`` + ``warm_up_session``,
num_thread=2, keep_alive=30m — unchanged) and the REAL whisper.cpp binary
to answer three questions from the failed M2.5B live acceptance:

  A. Does TTFT / ``prompt_eval_duration`` grow over a long conversation,
     and specifically does an *interrupted-history mutation*
     (``commit_interrupted_turn`` — CASE A rollback / CASE B spoken prefix)
     collapse Ollama's prompt-prefix KV-cache reuse?
  B. When an old Ollama generation is cancelled and a whisper.cpp decode
     starts immediately after, does the decode inflate because the old
     worker is still burning CPU on the 4-core Pi? (the live 7.28 s STT)
  C. How long, in ms, from ``CancelToken.cancel()`` to the provider worker
     actually stopping?

Writes measure_interrupt_latency_pi_<ts>.json + prints a verdict.

Run:  .venv/bin/python docs/research/m2_5_bargein/measure_interrupt_latency_pi.py
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
from nexa.providers.base import CancelToken  # noqa: E402
from nexa.stt.config import Language  # noqa: E402
from nexa.stt.transcriber import WhisperCppTranscriber  # noqa: E402

QUESTIONS = [
    "Co to jest czarna dziura?",
    "Jak powstaje gwiazda neutronowa?",
    "Dlaczego niebo jest niebieskie?",
    "Czym różni się kometa od asteroidy?",
    "Jak działa teleskop kosmiczny?",
    "Co to jest przesunięcie ku czerwieni?",
    "Dlaczego Księżyc zawsze pokazuje tę samą stronę?",
    "Jak daleko jest najbliższa galaktyka?",
    "Co to jest ciemna materia?",
    "Jak mierzy się odległość do gwiazd?",
    "Dlaczego Mars jest czerwony?",
    "Co to jest rok świetlny?",
    "Jak powstają pierścienie Saturna?",
    "Czym jest wiatr słoneczny?",
    "Dlaczego gwiazdy migoczą?",
    "Co to jest horyzont zdarzeń?",
    "Jak długo świeci Słońce?",
    "Czym jest soczewkowanie grawitacyjne?",
]
STT_WAV = (
    HERE.parents[0] / "m2_voice_spikes" / "asr_test_samples"
    / "pl_jaka_jest_prędkość_światła.wav"
)
N_TURNS = 18
INTERRUPT_AT = {5: "spoken_prefix", 10: "rollback", 14: "spoken_prefix"}


def _wav_pcm16k_mono(path: Path) -> bytes:
    import wave

    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if (sr, ch, sw) == (16000, 1, 2):
        return raw
    import audioop

    if ch == 2:
        raw = audioop.tomono(raw, sw, 0.5, 0.5)
    if sw != 2:
        raw = audioop.lin2lin(raw, sw, 2)
    if sr != 16000:
        raw, _ = audioop.ratecv(raw, 2, 1, sr, 16000, None)
    return raw


async def _one_turn(session, text: str, *, interrupt: str | None) -> dict:
    prov = session.provider
    prov.last_metrics = None
    t0 = time.monotonic()
    first_tok_at = None
    n_chunks = 0
    chunks: list[str] = []
    gen = session.send(text, response_mode=__import__(
        "nexa.conversation.response_mode", fromlist=["ResponseMode"]
    ).ResponseMode.VOICE)
    async for ch in gen:
        n_chunks += 1
        chunks.append(ch)
        if first_tok_at is None:
            first_tok_at = time.monotonic()
        if interrupt is not None and n_chunks >= 4:
            break
    rec: dict = {
        "text": text,
        "interrupt": interrupt,
        "ttft_ms": round((first_tok_at - t0) * 1000, 1) if first_tok_at else None,
        "history_turns_before": len(session.history),
    }
    if interrupt is not None:
        await gen.aclose()
        if interrupt == "rollback":
            out = session.commit_interrupted_turn("")
        else:
            out = session.commit_interrupted_turn("".join(chunks).strip()[:120])
        rec["interrupt_outcome"] = out.value
        # give Ollama a beat to notice the dropped stream
        await asyncio.sleep(0.2)
    else:
        m = prov.last_metrics or {}
        rec["prompt_eval_count"] = m.get("prompt_eval_count")
        _ped = m.get("prompt_eval_duration")
        rec["prompt_eval_ms"] = round(_ped / 1e6, 1) if _ped else None
        rec["eval_count"] = m.get("eval_count")
        _ed = m.get("eval_duration")
        rec["eval_ms"] = round(_ed / 1e6, 1) if _ed else None
        _ld = m.get("load_duration")
        rec["load_ms"] = round(_ld / 1e6, 1) if _ld else None
        if m.get("eval_count") and m.get("eval_duration"):
            rec["decode_tok_s"] = round(m["eval_count"] / (m["eval_duration"] / 1e9), 1)
    rec["history_turns_after"] = len(session.history)
    return rec


async def part_a_kv_cache(session) -> list[dict]:
    print("\n=== PART A — history growth / KV-cache after interrupted mutations ===")
    out = []
    for i in range(1, N_TURNS + 1):
        q = QUESTIONS[(i - 1) % len(QUESTIONS)]
        interrupt = INTERRUPT_AT.get(i)
        r = await _one_turn(session, q, interrupt=interrupt)
        r["turn"] = i
        tag = f" [INTERRUPT:{interrupt}]" if interrupt else ""
        print(f"  turn {i:2d}{tag}  ttft={r.get('ttft_ms')}ms  "
              f"prompt_eval={r.get('prompt_eval_count')} "
              f"({r.get('prompt_eval_ms')}ms)  hist={r['history_turns_after']}")
        out.append(r)
    return out


async def part_bc_cancel_overlap(session) -> dict:
    print("\n=== PART B/C — cancel -> worker-stop + whisper decode overlap ===")
    try:
        stt = WhisperCppTranscriber()
    except Exception as exc:  # noqa: BLE001
        return {"skipped": f"whisper.cpp unavailable: {exc}"}
    pcm = _wav_pcm16k_mono(STT_WAV)

    async def _decode() -> float:
        t = time.monotonic()
        await stt.transcribe(pcm, language=Language.PL)
        return round((time.monotonic() - t) * 1000, 1)

    # baseline: 3 decodes, no concurrent Ollama
    base = [await _decode() for _ in range(3)]
    print(f"  baseline whisper decode (no Ollama): {base} ms  median={statistics.median(base)}")

    # overlap: start an Ollama generation, let it run, cancel, decode immediately
    overlaps, cancel_to_stop = [], []
    for k in range(3):
        tok = CancelToken()
        gen = session.provider.generate(
            session.build_context().to_provider_messages(),
            session.options, cancel_token=tok,
        )
        got = 0
        async for _ in gen:
            got += 1
            if got >= 3:
                break
        await asyncio.sleep(0.8)  # Ollama is mid-eval
        t_cancel = time.monotonic()
        tok.cancel()
        # decode NOW (worker may still be running)
        d = await _decode()
        loop = asyncio.get_running_loop()
        stopped = await loop.run_in_executor(None, tok.wait_worker_stopped, 5.0)
        c2s = round((time.monotonic() - t_cancel) * 1000, 1) if stopped else None
        try:
            await gen.aclose()
        except Exception:  # noqa: BLE001
            pass
        overlaps.append(d)
        cancel_to_stop.append(c2s)
        print(f"  overlap run {k+1}: decode={d}ms  cancel->worker_stop={c2s}ms  "
              f"observed={tok.cancel_observed}")

    return {
        "baseline_decode_ms": base,
        "baseline_median_ms": statistics.median(base),
        "overlap_decode_ms": overlaps,
        "overlap_median_ms": statistics.median(overlaps),
        "cancel_to_worker_stop_ms": cancel_to_stop,
        "decode_inflation_ratio": round(
            statistics.median(overlaps) / statistics.median(base), 2
        ) if statistics.median(base) else None,
    }


async def main() -> None:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    print(f"M2.5B.1 Pi latency / KV-cache / cancel-overlap — {ts}")
    session = build_default_session()
    p = session.provider
    print(f"model={p.describe().model}  num_thread={p._num_thread}  keep_alive={p._keep_alive}")
    await warm_up_session(session)
    print("warm-up done")

    part_a = await part_a_kv_cache(session)
    part_bc = await part_bc_cancel_overlap(session)

    # analysis
    normal = [r for r in part_a if not r["interrupt"] and r.get("prompt_eval_count")]
    after_int = [
        part_a[i] for i in range(len(part_a))
        if i > 0 and part_a[i - 1]["interrupt"] and not part_a[i]["interrupt"]
        and part_a[i].get("prompt_eval_ms")
    ]
    early = [r for r in normal if r["turn"] <= 6]
    late = [r for r in normal if r["turn"] >= 13]

    def _avg(rows, key):
        xs = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(statistics.mean(xs), 1) if xs else None

    analysis = {
        "ttft_ms_early_avg": _avg(early, "ttft_ms"),
        "ttft_ms_late_avg": _avg(late, "ttft_ms"),
        "prompt_eval_count_early_avg": _avg(early, "prompt_eval_count"),
        "prompt_eval_count_late_avg": _avg(late, "prompt_eval_count"),
        "prompt_eval_ms_normal_avg": _avg(normal, "prompt_eval_ms"),
        "prompt_eval_ms_after_interrupt_avg": _avg(after_int, "prompt_eval_ms"),
        "prompt_eval_ms_after_interrupt_turns": [r["turn"] for r in after_int],
    }
    out = {"ts": ts, "part_a_turns": part_a, "part_bc": part_bc, "analysis": analysis}
    (HERE / f"measure_interrupt_latency_pi_{ts}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print("\n" + "=" * 64)
    print("ANALYSIS:")
    for k, v in analysis.items():
        print(f"  {k}: {v}")
    if not part_bc.get("skipped"):
        print(f"  whisper baseline median: {part_bc['baseline_median_ms']} ms")
        print(f"  whisper overlap  median: {part_bc['overlap_median_ms']} ms  "
              f"(inflation x{part_bc['decode_inflation_ratio']})")
        print(f"  cancel->worker_stop: {part_bc['cancel_to_worker_stop_ms']} ms")
    print(f"-> measure_interrupt_latency_pi_{ts}.json")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
