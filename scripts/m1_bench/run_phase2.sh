#!/usr/bin/env bash
# THROWAWAY M1.0B Phase 2 deep-dive: run against finalist model tags only.
# Usage: scripts/m1_bench/run_phase2.sh "<tag1>" "<tag2>" ["<tag3>"]
#
# Per finalist tag runs, at recommended settings (--bare-options, same
# methodology as Phase 1):
#   - CONV-MIX-1 (language switching)
#   - CONV-NATURAL-S1 (18-turn naturalness stress; also used for the
#     long-context decay analysis by reading per-turn ttft/gen_tps/
#     prompt_eval_count out of the resulting JSON — no separate decay
#     harness needed)
#   - CONV-HONESTY-S1 (hallucination / factual-restraint probe)
#   - persona A/B on CONV-PL-S1: persona_minimal_v0 vs persona_nexa_v1
#     (standardized sampling for this pair specifically, so the only
#     variable is the system prompt)
#   - a STANDARDIZED-settings run of CONV-PL-S1 (shared temp0.7/top_p0.8/
#     top_k20/repeat_penalty1.05 case-file defaults) for the standardized-
#     vs-recommended comparison required by the task (Phase 1 only ran
#     recommended-settings).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
OUT=docs/research/m1_bench_results/m1_0b
LOG="$OUT/phase2_log.txt"
mkdir -p "$OUT"
B="python3 scripts/m1_bench/bench.py --backend ollama --out $OUT"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <model_tag> [<model_tag> ...]" >&2
  exit 2
fi

probe() {
  echo "[probe $1] $(date -Is) temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null) throttled=$(vcgencmd get_throttled 2>/dev/null) $(grep MemAvailable /proc/meminfo)" | tee -a "$LOG"
}

think_flag_for() {
  local tag="$1"
  local caps
  caps="$(ollama show "$tag" 2>/dev/null | awk '/Capabilities/{f=1} f && NF==0{exit} f')"
  if printf '%s' "$caps" | grep -qi thinking; then
    echo "--no-think"
  else
    echo ""
  fi
}

echo "=== PHASE2 START $(date -Is) — models: $* ===" | tee -a "$LOG"

for TAG in "$@"; do
  flags="$(think_flag_for "$TAG")"
  echo "=== $(date -Is) :: FINALIST $TAG (auto-flags: $flags) ===" | tee -a "$LOG"
  probe "before-$TAG"

  echo "--- CONV-MIX-1 (recommended settings) ---" | tee -a "$LOG"
  eval "$B --model '$TAG' --cases scripts/m1_bench/cases/conversation_mix.json --bare-options --num-ctx 8192 --num-predict 200 $flags" 2>&1 | tee -a "$LOG"

  echo "--- CONV-NATURAL-S1 (recommended settings; also feeds long-context decay analysis) ---" | tee -a "$LOG"
  eval "$B --model '$TAG' --cases scripts/m1_bench/cases/conversation_natural.json --bare-options --num-ctx 8192 --num-predict 200 $flags" 2>&1 | tee -a "$LOG"

  echo "--- CONV-HONESTY-S1 (recommended settings) ---" | tee -a "$LOG"
  eval "$B --model '$TAG' --cases scripts/m1_bench/cases/conversation_honesty.json --bare-options --num-ctx 8192 --num-predict 200 $flags" 2>&1 | tee -a "$LOG"

  echo "--- persona A/B: MINIMAL persona, CONV-PL-S1, standardized sampling ---" | tee -a "$LOG"
  eval "$B --model '$TAG' --cases scripts/m1_bench/cases/conversation_pl.json --system-file scripts/m1_bench/personas/persona_minimal_v0.txt --num-ctx 8192 $flags" 2>&1 | tee -a "$LOG"

  echo "--- persona A/B: NEXA persona v1, CONV-PL-S1, standardized sampling ---" | tee -a "$LOG"
  eval "$B --model '$TAG' --cases scripts/m1_bench/cases/conversation_pl.json --system-file scripts/m1_bench/personas/persona_nexa_v1.txt --num-ctx 8192 $flags" 2>&1 | tee -a "$LOG"

  echo "--- standardized-settings CONV-PL-S1 (case-file defaults, for standardized-vs-recommended comparison) ---" | tee -a "$LOG"
  eval "$B --model '$TAG' --cases scripts/m1_bench/cases/conversation_pl.json --num-ctx 8192 $flags" 2>&1 | tee -a "$LOG"

  probe "after-$TAG"
  sleep 5
done

echo "=== PHASE2 DONE $(date -Is) ===" | tee -a "$LOG"
touch "$OUT/phase2.done"
