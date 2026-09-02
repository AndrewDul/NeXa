#!/usr/bin/env bash
# THROWAWAY M1.0B Phase 1 fast screen: perf + PL conversation + EN conversation,
# at each model's own recommended settings (--bare-options: only num_ctx +
# num_predict forced; temperature/top_p/top_k/repeat_penalty left to the
# model's Ollama Modelfile defaults; non-thinking mode forced for any model
# whose `ollama show` capabilities list "thinking").
#
# Safety: no pgrep-on-command-string watchers (see
# docs/troubleshooting/20260831_m1_bench_watchers_and_bielik_detection.md).
# Progress is tracked purely via the log file + a sentinel .done file.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
OUT=docs/research/m1_bench_results/m1_0b
LOG="$OUT/phase1_log.txt"
mkdir -p "$OUT"
B="python3 scripts/m1_bench/bench.py --backend ollama --out $OUT"

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

run_model() {
  local tag="$1"
  local flags
  flags="$(think_flag_for "$tag")"
  echo "=== $(date -Is) :: MODEL $tag (auto-flags: $flags) ===" | tee -a "$LOG"
  probe "before-$tag"
  eval "$B --model '$tag' --perf --perf-tokens 200 --perf-runs 3 --bare-options --num-ctx 4096 $flags" 2>&1 | tee -a "$LOG"
  eval "$B --model '$tag' --cases scripts/m1_bench/cases/conversation_pl.json --bare-options --num-ctx 8192 --num-predict 200 $flags" 2>&1 | tee -a "$LOG"
  eval "$B --model '$tag' --cases scripts/m1_bench/cases/conversation_en.json --bare-options --num-ctx 8192 --num-predict 200 $flags" 2>&1 | tee -a "$LOG"
  probe "after-$tag"
  sleep 5
}

echo "=== PHASE1 START $(date -Is) ===" | tee -a "$LOG"

for TAG in \
  "qwen3:4b-instruct" \
  "qwen3.5:4b" \
  "qwen3.5:2b" \
  "phi4-mini:3.8b" \
  "gemma4:e2b" \
  "gemma4:e4b" \
  "SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0" \
  "bielik-4.5b-v3-q4km-thirdparty" \
  ; do
  if ollama show "$TAG" >/dev/null 2>&1; then
    run_model "$TAG"
  else
    echo "!!! SKIP $TAG — not present locally at phase1 run time ($(date -Is))" | tee -a "$LOG"
  fi
done

echo "=== PHASE1 DONE $(date -Is) ===" | tee -a "$LOG"
touch "$OUT/phase1.done"
