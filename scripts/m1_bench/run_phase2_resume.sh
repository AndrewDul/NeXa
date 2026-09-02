#!/usr/bin/env bash
# THROWAWAY M1.0B Phase 2 RESUME — finalists still missing Phase 2 data only.
# gemma4:e4b already has a full Phase 2 battery (phase2_log.txt, 2026-09-02);
# this run covers the two remaining finalists: gemma4:e2b and qwen3.5:2b.
#
# Safety hardening over run_phase2.sh (the original interrupted mid-gemma4:e2b
# with the previous model still resident):
#   - every locally-loaded model is explicitly `ollama stop`ped before each
#     finalist, and again at the end
#   - swap-used is probed alongside temp/RAM/throttle before & after each model
#     and between sub-runs
#   - a soft guard aborts the current finalist if MemAvailable < 1500 MB or
#     swap-used > 512 MB right before a sub-run (prevents a repeat OOM stall)
# Progress tracked purely via the log file (no self-matching pgrep watchers).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
OUT=docs/research/m1_bench_results/m1_0b
LOG="$OUT/phase2_resume_log.txt"
mkdir -p "$OUT"
B="python3 scripts/m1_bench/bench.py --backend ollama --out $OUT"

FINALISTS=("gemma4:e2b" "qwen3.5:2b")

probe() {
  local swap_used
  swap_used=$(free -m | awk '/^Swap:/{print $3}')
  echo "[probe $1] $(date -Is) temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null) throttled=$(vcgencmd get_throttled 2>/dev/null) $(grep MemAvailable /proc/meminfo) swap_used=${swap_used}MB loadavg=$(cut -d' ' -f1 /proc/loadavg)" | tee -a "$LOG"
}

mem_avail_mb() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }
swap_used_mb() { free -m | awk '/^Swap:/{print $3}'; }

guard() {
  local ma sw
  ma=$(mem_avail_mb); sw=$(swap_used_mb)
  if [ "$ma" -lt 1500 ] || [ "$sw" -gt 512 ]; then
    echo "!!! GUARD TRIP before $1: MemAvailable=${ma}MB swap_used=${sw}MB — aborting this finalist" | tee -a "$LOG"
    return 1
  fi
  return 0
}

stop_all_models() {
  local m
  for m in $(ollama ps 2>/dev/null | awk 'NR>1{print $1}'); do
    echo "  ollama stop $m" | tee -a "$LOG"
    ollama stop "$m" 2>&1 | tee -a "$LOG" || true
  done
  sleep 3
}

think_flag_for() {
  local tag="$1" caps
  caps="$(ollama show "$tag" 2>/dev/null | awk '/Capabilities/{f=1} f && NF==0{exit} f')"
  if printf '%s' "$caps" | grep -qi thinking; then echo "--no-think"; else echo ""; fi
}

run_sub() {
  # $1 = label for log/guard ; rest = bench args
  local label="$1"; shift
  guard "$label" || return 1
  echo "--- $label $(date -Is) ---" | tee -a "$LOG"
  eval "$B $*" 2>&1 | tee -a "$LOG"
  probe "after-$label"
}

echo "=== PHASE2 RESUME START $(date -Is) — finalists: ${FINALISTS[*]} ===" | tee -a "$LOG"
stop_all_models
probe "session-start"

for TAG in "${FINALISTS[@]}"; do
  if ! ollama show "$TAG" >/dev/null 2>&1; then
    echo "!!! SKIP $TAG — not present locally ($(date -Is))" | tee -a "$LOG"
    continue
  fi
  flags="$(think_flag_for "$TAG")"
  echo "=== $(date -Is) :: FINALIST $TAG (auto-flags: $flags) ===" | tee -a "$LOG"
  stop_all_models
  probe "before-$TAG"

  SAFE="${TAG//[:\/]/-}"

  run_sub "CONV-MIX-1 [$TAG]" \
    "--model '$TAG' --cases scripts/m1_bench/cases/conversation_mix.json --bare-options --num-ctx 8192 --num-predict 200 $flags" || { echo "abort $TAG" | tee -a "$LOG"; continue; }

  run_sub "CONV-NATURAL-S1 [$TAG]" \
    "--model '$TAG' --cases scripts/m1_bench/cases/conversation_natural.json --bare-options --num-ctx 8192 --num-predict 200 $flags" || { echo "abort $TAG" | tee -a "$LOG"; continue; }

  run_sub "CONV-HONESTY-S1 [$TAG]" \
    "--model '$TAG' --cases scripts/m1_bench/cases/conversation_honesty.json --bare-options --num-ctx 8192 --num-predict 200 $flags" || { echo "abort $TAG" | tee -a "$LOG"; continue; }

  run_sub "persona-A minimal_v0 CONV-PL-S1 [$TAG]" \
    "--model '$TAG' --cases scripts/m1_bench/cases/conversation_pl.json --system-file scripts/m1_bench/personas/persona_minimal_v0.txt --num-ctx 8192 $flags" || { echo "abort $TAG" | tee -a "$LOG"; continue; }

  run_sub "persona-B nexa_v1 CONV-PL-S1 [$TAG]" \
    "--model '$TAG' --cases scripts/m1_bench/cases/conversation_pl.json --system-file scripts/m1_bench/personas/persona_nexa_v1.txt --num-ctx 8192 $flags" || { echo "abort $TAG" | tee -a "$LOG"; continue; }

  run_sub "standardized CONV-PL-S1 [$TAG]" \
    "--model '$TAG' --cases scripts/m1_bench/cases/conversation_pl.json --num-ctx 8192 $flags" || { echo "abort $TAG" | tee -a "$LOG"; continue; }

  stop_all_models
  probe "done-$TAG"
  sleep 5
done

stop_all_models
echo "=== PHASE2 RESUME DONE $(date -Is) ===" | tee -a "$LOG"
touch "$OUT/phase2_resume.done"
