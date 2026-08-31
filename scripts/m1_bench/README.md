# scripts/m1_bench/ — THROWAWAY M1.0 benchmark harness

**This is not production architecture.** It is a disposable measurement tool for
the M1.0 research milestone (see `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md`
and `docs/reports/R0002_*`). It talks to a local Ollama server over HTTP and to
`llama-cli` via subprocess, records timing / resource / thermal data, and dumps
multi-turn conversation transcripts for human review.

It deliberately:
- uses only the Python standard library (no venv, no dependency),
- contains **no** `ModelProvider`, `ConversationSession`, or any type that M1.1
  should build — do not import from here into `src/nexa/`,
- writes results to `docs/research/m1_bench_results/` as JSON + Markdown.

When M1.1 begins, this directory can be deleted.

## Files

- `bench.py` — runner. `python3 scripts/m1_bench/bench.py --help`
- `cases/conversation_pl.json`, `cases/conversation_en.json`,
  `cases/conversation_mix.json` — stable benchmark conversations
  (`CONV-PL-00x`, `CONV-EN-00x`, `CONV-MIX-001`, ...).
- `run_focused.sh` — the driver actually used for the M1.0 baseline: perf
  micro-benchmarks were run per-model ad hoc; this script runs the two
  conversation comparisons that needed transcripts (`qwen3:4b-instruct` and
  `Bielik-4.5B-v3` × {PL, EN}) and pulls Bielik.

Raw results (JSON + Markdown transcripts + `run_focused.log`) land in
`docs/research/m1_bench_results/`.

## Usage

```bash
# Ollama must be running (systemd service 'ollama').
python3 scripts/m1_bench/bench.py \
  --backend ollama \
  --model qwen3:4b-instruct \
  --cases scripts/m1_bench/cases/conversation_pl.json \
  --out docs/research/m1_bench_results

# perf-only micro benchmark (fixed prompt, N tokens, repeated)
python3 scripts/m1_bench/bench.py --backend ollama --model qwen3:4b-instruct --perf --perf-tokens 200 --perf-runs 3
```
