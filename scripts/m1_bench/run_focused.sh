#!/usr/bin/env bash
# THROWAWAY M1.0 focused conversation benchmark. Perf micro-benchmarks for the
# 4 already-local models were run separately (see perf_*.json). This run covers
# the two decisions that need conversation transcripts:
#   - qwen3:4b-instruct  x {PL, EN}    (primary baseline)
#   - Bielik-4.5B-v3 Q8_0 x {PL, EN}   (Polish-quality challenger; pulled here)
# MIX + smaller-model conversations are skipped: perf data + established evidence
# already cover them, and a 4B on this Pi is ~3 tok/s so each session is ~12 min.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
OUT=docs/research/m1_bench_results
LOG=$OUT/run_focused.log
B="python3 scripts/m1_bench/bench.py --backend ollama --out $OUT"
BIELIK="SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0"
mkdir -p "$OUT"
echo "=== run_focused start $(date -Is) ===" | tee -a "$LOG"
probe () { echo "[probe $1] temp=$(cat /sys/class/thermal/thermal_zone0/temp) throttled=$(vcgencmd get_throttled) $(grep MemAvailable /proc/meminfo)" | tee -a "$LOG"; }

for C in conversation_pl conversation_en; do
  echo "--- $(date -Is) :: qwen3:4b-instruct $C ---" | tee -a "$LOG"; probe before
  eval "$B --model 'qwen3:4b-instruct' --cases scripts/m1_bench/cases/$C.json" 2>&1 | tee -a "$LOG"
  probe after; sleep 5
done

echo "--- $(date -Is) :: pulling $BIELIK ---" | tee -a "$LOG"
df -h / | tee -a "$LOG"; free -h | tee -a "$LOG"
ollama pull "$BIELIK" 2>&1 | tail -3 | tee -a "$LOG"
ollama show "$BIELIK" 2>&1 | tee -a "$LOG"
# NOTE: `ollama list | grep -qi bielik` under `set -o pipefail` gives a FALSE
# NEGATIVE: grep -q exits on first match, ollama list then dies with SIGPIPE
# (141), and pipefail propagates that as the pipeline's status. Capture first,
# then match, so the producer's SIGPIPE never poisons the test.
bielik_list="$(ollama list 2>/dev/null || true)"
if printf '%s\n' "$bielik_list" | grep -qi 'bielik'; then
  eval "$B --model '$BIELIK' --perf --perf-tokens 200 --perf-runs 3" 2>&1 | tee -a "$LOG"
  for C in conversation_pl conversation_en; do
    echo "--- $(date -Is) :: bielik $C ---" | tee -a "$LOG"; probe before
    eval "$B --model '$BIELIK' --cases scripts/m1_bench/cases/$C.json" 2>&1 | tee -a "$LOG"
    probe after; sleep 5
  done
else
  echo "BIELIK PULL FAILED — qwen3:4b results stand." | tee -a "$LOG"
fi
echo "=== run_focused done $(date -Is) ===" | tee -a "$LOG"
