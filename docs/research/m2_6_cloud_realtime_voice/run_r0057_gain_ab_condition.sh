#!/usr/bin/env bash
#
# ============================================================================
# PREPARED, NOT APPROVED FOR EXECUTION.
#
# This script performs a REAL hardware mixer write (Array 'PCM',1) and runs
# the R0057 gain A/B experiment probe against real audio hardware. It is
# committed as a reviewable artifact for Andrzej's explicit approval
# (R0060 checkpoint) -- it must NOT be run by an agent or operator without
# that explicit, separate approval. The `--i-have-explicit-operator-approval`
# flag below is a deliberate manual gate, not a formality: passing it means
# a human has actually reviewed and approved THIS run.
# ============================================================================
#
# Runs exactly ONE condition (A or B) of the previously-designed R0057 gain
# A/B experiment (docs/reports/R0056_..., R0057_..., R0059_...; procedure
# finalized in R0060, docs/research/m2_6_cloud_realtime_voice/
# R0057_gain_ab_experiment_procedure.md). One probe invocation per condition,
# combining a fresh 60s warm-up with 3 measured silent trials (never a reuse
# of R0059's own separate, warmup-only run).
#
# Condition A: Array 'PCM',1 = 40 (raw)  = -20.00dB (the currently-accepted,
#              already-live baseline -- verified via `amixer -c Array cget
#              numid=6`, dBminmax min=-60.00dB max=0.00dB, linear 1dB/step,
#              so raw 40 = 40-60 = -20dB exactly).
# Condition B: Array 'PCM',1 = 60 (raw)  =   0.00dB (the exact top of the
#              control's own supported range -- confirmed from the SAME
#              dBminmax metadata, not an approximation).
#
# UACDemoV10 (the audible speaker mixer) is NEVER written by this script --
# only ever read, before and after, to confirm it stayed at its own verified
# MAX baseline (147/147, -0.94dB) throughout.
#
# Safety properties (all verified this checkpoint against live, read-only
# `amixer`/`aplay -l`/`/proc/asound/cards` output -- never guessed):
#   - `set -euo pipefail` + a rollback() function installed via
#     `trap ... EXIT INT TERM` -- Array 'PCM',1 is restored to the baseline
#     (40 / -20.00dB) on normal completion, on any error, AND on interruption
#     (Ctrl-C, or the external `timeout` supervisor's own SIGTERM/SIGKILL),
#     with an independent `amixer sget` readback printed immediately after.
#   - The ONLY control this script ever writes is Array 'PCM',1 (numid=6).
#     No other mixer, no UACDemoV10 control, is ever set.
#   - `timeout --signal=TERM --kill-after=<grace>s <bound>s <probe>` bounds
#     ONLY the probe's own child process tree -- no broad `pkill`/`killall`,
#     nothing that could affect an unrelated process.
#   - Every mixer mutation is immediately followed by an independent
#     `amixer sget` readback, printed to the log, before the probe is ever
#     started.
#
# Usage:
#   bash run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval
#   bash run_r0057_gain_ab_condition.sh B --i-have-explicit-operator-approval
#
# Run condition A FIRST, inspect its own results/log/invalid-run criteria
# (see the procedure doc) BEFORE running condition B. Never run both
# unattended in one shot.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_r0057_gain_ab_condition.sh <A|B> --i-have-explicit-operator-approval

This performs a REAL hardware mixer write and a real audio hardware probe
run. It must NOT be invoked without explicit operator approval for THIS
specific run.
EOF
  exit 2
}

if [[ $# -ne 2 || "${2:-}" != "--i-have-explicit-operator-approval" ]]; then
  usage
fi

CONDITION="$1"
case "$CONDITION" in
  A) TARGET_RAW=40 ;;   # -20.00dB
  B) TARGET_RAW=60 ;;   #   0.00dB
  *) usage ;;
esac

BASELINE_RAW=40  # -20.00dB -- the accepted, currently-live baseline to restore to.
EXPECTED_UAC_RAW=147  # UACDemoV10's own verified MAX baseline (both channels).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PROBE="$REPO_ROOT/docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py"
CAPTURE_DIR="$REPO_ROOT/docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$CAPTURE_DIR/gain_ab_condition_${CONDITION}_${TS}.log"

mkdir -p "$CAPTURE_DIR"

echo "=== R0057 gain A/B experiment -- condition ${CONDITION} -- ${TS} ==="
echo "PRECHECK (read-only):"
amixer -c Array sget 'PCM',1
amixer -c UACDemoV10 sget PCM

# ---- Rollback trap: fires on normal exit, error (set -e), or interruption.
# Touches ONLY Array 'PCM',1 -- never any other control.
rollback() {
  local rc=$?
  echo "ROLLBACK: restoring Array 'PCM',1 to raw ${BASELINE_RAW} (-20.00dB)"
  amixer -c Array sset 'PCM',1 "${BASELINE_RAW}" >/dev/null
  echo "ROLLBACK_READBACK (independent, post-restore):"
  amixer -c Array sget 'PCM',1
  echo "UAC_READBACK (must be unchanged throughout -- never written by this script):"
  amixer -c UACDemoV10 sget PCM
  exit "$rc"
}
trap rollback EXIT INT TERM

echo "SET_CONDITION_${CONDITION}: Array 'PCM',1 -> raw ${TARGET_RAW}"
amixer -c Array sset 'PCM',1 "${TARGET_RAW}" >/dev/null
echo "SET_CONDITION_${CONDITION}_READBACK (independent):"
amixer -c Array sget 'PCM',1

echo "RUNNING PROBE (condition ${CONDITION}); log: ${LOG}"
timeout --signal=TERM --kill-after=15s 240s \
  "$REPO_ROOT/.venv/bin/python" -u "$PROBE" \
  --warmup-seconds 60 --level max --repeats 3 \
  --capture-pcm --max-lag-ms 500 \
  > "$LOG" 2>&1
EXIT_CODE=$?
echo "EXIT_CODE=${EXIT_CODE}" | tee -a "$LOG"

if [[ "$EXIT_CODE" -ne 0 ]]; then
  echo "INVALID RUN: probe did not exit 0 (see EXIT_CODE above and the log at ${LOG})." >&2
fi

exit "$EXIT_CODE"
# rollback() runs automatically here (EXIT trap) regardless of EXIT_CODE.
