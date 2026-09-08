# M2.4B.3.4 — Local LLM serving & voice performance benchmark

Research + benchmark only. See
`docs/reports/R0021_m2_4b_3_4_local_llm_serving_voice_benchmark_20260907.md`.

## What this measures

The realtime-voice bottleneck after B.3.1–B.3.3 is local LLM generation
speed (R0018: long-form continuity needs ~15.9 generated **chars/s**;
`gemma4:e4b` delivers ~8–10 PL / ~14–15 EN). This benchmark:

1. characterises the current `gemma4:e4b` baseline (cold + warm, PL + EN),
2. sweeps Ollama serving options for a same-model speedup,
3. compares a small shortlist of alternative local models,

all under the **real production path**: `bench_llm.py` builds the wire
messages with `ConversationContext.build(...).to_provider_messages(
response_mode=ResponseMode.VOICE)` (exact persona + B.3.3 voice directive
+ per-turn language directive) and POSTs straight to Ollama `/api/chat`
so the `done`-chunk `load_duration` / `prompt_eval_*` / `eval_*` metrics
are captured (the `LocalModelProvider` drops them).

## Metric definitions

- **tok/s** = `eval_count / eval_duration` (Ollama done-chunk).
- **generated chars/s** = `len(text) / eval_duration` — the
  cross-language metric R0018's target is stated in. Polish packs fewer
  chars/token than English, so tok/s alone is not comparable across
  languages.
- **warm-prefix TTFT** = time to first content byte when the prompt
  prefix (persona + directive [+ prior turns]) is already in the
  llama.cpp KV cache (turn 2+ of a conversation, or an exact repeat).
- **cold-prefix TTFT** = first turn of a fresh conversation: the whole
  ~535-token preamble is prompt-eval'd.
- **cold load** = `load_duration` (model weights → RAM).

## Files

- `bench_llm.py` — the harness (throwaway; stdlib + `nexa` imports only).
- `run_all.sh` — chained batches B–F. `nt_confirm.py` — focused
  `num_thread` confirmation.
- `bench_baseline_e4b_20260907.json` (+ `_raw.txt`) — `gemma4:e4b` cold +
  core (SHORT / FOLLOW-UP / SCIENCE / REASONING + a 5-turn thread, PL &
  EN) + science-truth set.
- `bench_e2b_20260907.json` — `gemma4:e2b`, same.
- `bench_serving_e4b_20260907.json` — `num_thread` / `num_batch` /
  `num_ctx` variations on `gemma4:e4b`;
  `bench_serving_confirm_20260907.json` — the 5-rep `num_thread`
  confirmation.
- `bench_alt_20260907.json` — `qwen3:4b-instruct`, `qwen3.5:4b` (core +
  science + cold).
- `bench_fixed_20260907.json` — fixed `num_predict` throughput, 8 models.
- `bench_bielik_science_20260907.json` — Bielik science-truth (PL ref).
- `bench_smallpl_20260907.json` — `qwen2.5:3b` / `llama3.2:3b` PL+EN core
  spot-check.
- `*_raw.txt` — human-readable progress echoes of the batch runs.

## M2.4B.3.5 — operator-blind model A/B (R0022)

A real, operator-blind A/B of `gemma4:e4b` vs `gemma4:e2b` on the full
realtime voice path. **The A/B mapping is sealed and must not be printed
or revealed before the operator's verdict.**

- `make_mapping.py` — one-shot `secrets.SystemRandom` assignment of the
  two models to Candidate A / Candidate B; writes
  `operator_blind_ab_mapping_20260908.txt` and prints only a neutral line
  + the file's SHA-256. `--force` to reshuffle (do not, mid-test).
- `blind_ab_common.py` — the sealed-mapping loader, `_NumThreadProvider`
  (`LocalModelProvider` + one `num_thread` request option), and
  `build_blind_session(candidate)` — a fresh `ConversationSession` with
  the real persona + `GenerationOptions`, `num_thread=2` + `keep_alive=30m`
  applied to **both** candidates. Production default (`gemma4:e4b`)
  untouched.
- `operator_blind_ab.py` — the blind launcher.
  `--candidate {A,B} --language {pl,en}`; `--selftest` for an offline
  wiring check. Real mic + STT + `ConversationSession` +
  `ResponseMode.VOICE` + SpeechPlanner + continuity controller + Piper
  `nice +10`. The model tag is **never printed**; all per-turn metrics /
  resource samples go to `blind_results/candidate_<X>_<lang>_<ts>.txt` /
  `.jsonl`.
- `nt2_piper_validation.py` — checks `num_thread=2` (R0021) is still safe
  under a concurrent realistic Piper load before the A/B uses it.
- `operator_blind_ab_mapping_20260908.txt` — the sealed mapping
  (auditable; **do not open before the A/B verdict**).
- `blind_results/` — per-candidate metrics land here when the operator runs.

Operator commands (carry no model identity):

    ./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate A --language pl
    ./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate A --language en
    ./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate B --language pl
    ./.venv/bin/python docs/research/m2_4b_llm_bench/operator_blind_ab.py --candidate B --language en
