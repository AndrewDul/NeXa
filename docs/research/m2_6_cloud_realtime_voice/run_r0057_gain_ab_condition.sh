#!/usr/bin/env bash
#
# ============================================================================
# PREPARED, NOT APPROVED FOR EXECUTION.
#
# This script performs a REAL hardware mixer write (Array 'PCM',1) and runs
# the R0057 gain A/B experiment probe against real audio hardware. It is
# committed as a reviewable artifact -- it must NOT be run by an agent or
# operator without Andrzej's explicit, separate approval for the specific
# invocation. The `--i-have-explicit-operator-approval` flag below is a
# deliberate manual gate, not a formality.
#
# A single future approval MAY cover the full A-then-B experiment,
# conditional on condition A's own run being valid (see
# R0057_gain_ab_experiment_procedure.md, S5) before condition B is run --
# this script does not itself require two separate approvals, one per
# condition, but it also never chains A and B automatically: each
# invocation still runs exactly one condition, and the operator decides
# whether to proceed to the next.
# ============================================================================
#
# R0061 CORRECTIONS (this revision) to the R0060 draft, found in review,
# fixed here -- see docs/reports/R0061_..._20260913.md for the full
# rationale and evidence for each:
#   1. `set -e` is no longer enabled anywhere in this script -- it aborts
#      a script at a plain failing command BEFORE the next line (an
#      `rc=$?` capture) ever runs, so the R0060 draft's own EXIT_CODE
#      bookkeeping was unreachable on any real probe failure (and the
#      same hazard applies to several of this revision's own new
#      command-substitution assignments, e.g. `raws="$(... )"` when
#      parsing fails). Fixed by removing `-e` entirely and checking every
#      command that can legitimately fail via an explicit `if`/`&&`
#      rather than relying on errexit anywhere (`-u`/`pipefail` stay on).
#   2. Every mixer read this script relies on for a DECISION is now
#      PARSED and VALIDATED against an expected value (never just printed
#      for a human to eyeball) -- prechecks abort before any mutation on
#      a mismatch or an unreadable value; the post-set readback is
#      verified the same way.
#   3. Rollback is now idempotent (a guard prevents double-execution from
#      overlapping trap paths), reports its OWN outcome separately from
#      the run's own outcome, and a failed/unverified rollback now forces
#      a nonzero wrapper exit even when the run itself looked fine.
#   4. INT/TERM are handled explicitly and separately from normal EXIT:
#      each records a distinct interruption status, stops and reaps ONLY
#      this script's own child process (the `timeout`-supervised probe),
#      and then performs the SAME rollback path -- never chains into
#      another condition. SIGKILL of this wrapper itself, or a host power
#      loss, CANNOT be intercepted by any shell trap -- no code here
#      claims otherwise; that residual risk is inherent to any
#      trap-based rollback and is called out explicitly, not hidden.
#   5. The ENTIRE transcript -- prechecks, set/readback, the probe's own
#      stdout/stderr, any interruption, and rollback/readback -- is now
#      captured in ONE persisted log file (previously only the probe's
#      own output reached the log; every wrapper-side echo was
#      terminal-only and lost once the shell exited).
#   6. Each condition's own results JSON and `--capture-pcm` WAV pairs are
#      now archived into a per-run, per-condition directory immediately
#      after that run, using a marker-file/`find -newer` inventory (never
#      "newest file by mtime", which could silently pick up a stale file
#      from an earlier, unrelated run) -- so condition B can no longer
#      overwrite condition A's own fixed-name PCM files at their shared
#      default path. A MANIFEST.txt with SHA-256 hashes accompanies each
#      archive, making the JSON<->WAV association for that run explicit.
#
# Runs exactly ONE condition (A or B) of the previously-designed R0057 gain
# A/B experiment (docs/reports/R0056_..., R0057_..., R0059_...; procedure
# finalized in R0060, corrected in R0061,
# docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md).
# One probe invocation per condition, combining a fresh 60s warm-up with 3
# measured silent trials (never a reuse of R0059's own separate,
# warmup-only run).
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
# only ever read, before and after, and its reading is VALIDATED (both
# channels must read exactly its own verified MAX baseline, 147/147,
# -0.94dB) before any mutation is attempted.
#
# Exit code convention (all documented, all distinguishable in the log):
#   0    -- fully successful: precheck OK, condition set+verified OK,
#           probe exited 0, rollback verified.
#   2    -- usage/argument error (no hardware touched).
#   90   -- precheck failed (mismatched or unreadable mixer state) --
#           aborted BEFORE any mutation; rollback-to-baseline is still
#           attempted afterward as a safety default (see rollback()).
#   92   -- the condition's own mixer write or its post-write readback
#           verification failed (a possibly-partial write) -- rollback is
#           still attempted.
#   93   -- the probe exited 0, but this run's own artifact inventory
#           (the results JSON and/or its `--capture-pcm` WAV pairs,
#           found via a marker-file/`find -newer` scan, never "newest
#           file by mtime") did not archive cleanly -- see the
#           ARCHIVE_* lines in the log; rollback is still attempted.
#   91   -- rollback itself did not verify, even though the run otherwise
#           looked fine (this OVERRIDES a 0 that would otherwise be
#           reported -- a failed restore must never look like success).
#   130/143 -- interrupted by SIGINT/SIGTERM respectively (standard
#           128+signum convention); rollback is still attempted.
#   <probe's own code> -- otherwise, e.g. 1 (Python exception), 124
#           (external `timeout` fired) -- preserved as-is so the log's
#           own EXIT_CODE line carries real information; rollback is
#           still attempted, and a rollback failure on TOP of a probe
#           failure is reported via its own log line even though the
#           process exit code reports the probe's own (already nonzero)
#           status.
#
# TEST-ONLY environment overrides (never needed, and never set, for a
# real operator run -- see tests/test_run_r0057_gain_ab_condition_sh.py):
#   R0057_AB_CAPTURE_ROOT      -- replaces the real self_echo_captures/ dir.
#   R0057_AB_PROBE_LAUNCHER    -- replaces the whole python/probe command
#                                 line with a single executable (a fake).
#   R0057_AB_TIMEOUT_BOUND_S, R0057_AB_TIMEOUT_KILL_AFTER_S -- replace the
#                                 240s/15s external supervisor bounds.
#
# Usage:
#   bash run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval
#   bash run_r0057_gain_ab_condition.sh B --i-have-explicit-operator-approval
#
# Run condition A FIRST, inspect its own results/log/invalid-run criteria
# (see the procedure doc) BEFORE running condition B. This script will not
# chain into a second condition under any circumstance.

set -uo pipefail
# NOTE: `set -e` is intentionally NOT enabled globally (Correction 1 above).
# Every command whose failure is part of this script's own normal control
# flow is checked explicitly (`if ! cmd; then ...`) rather than relying on
# errexit, which cannot survive a `cmd; rc=$?` capture pattern.

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

BASELINE_RAW=40           # -20.00dB -- the accepted baseline to restore to.
UAC_EXPECTED_RAW=147      # UACDemoV10's own verified MAX baseline (each channel).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PROBE="$REPO_ROOT/docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py"

CAPTURE_ROOT="${R0057_AB_CAPTURE_ROOT:-$REPO_ROOT/docs/research/m2_6_cloud_realtime_voice/self_echo_captures}"
JSON_DIR="$CAPTURE_ROOT"
PCM_DIR="$CAPTURE_ROOT/pcm"
LOG_DIR="$CAPTURE_ROOT/warmup_hang_logs"
ARCHIVE_ROOT="$CAPTURE_ROOT/gain_ab_experiment"

TIMEOUT_BOUND_S="${R0057_AB_TIMEOUT_BOUND_S:-240}"
TIMEOUT_KILL_AFTER_S="${R0057_AB_TIMEOUT_KILL_AFTER_S:-15}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/gain_ab_condition_${CONDITION}_${TS}.log"
ARCHIVE_DIR="$ARCHIVE_ROOT/${TS}_condition_${CONDITION}"

mkdir -p "$LOG_DIR" "$JSON_DIR" "$PCM_DIR"

# ---- Correction 5: capture EVERYTHING (this script's own output AND the
# probe's) into one persisted log, from this point on, while still showing
# it on the terminal.
exec > >(tee -a "$LOG") 2>&1

echo "=== R0057 gain A/B experiment -- condition ${CONDITION} -- ${TS} ==="
echo "LOG=${LOG}"

# ---- Parsing helpers -------------------------------------------------- #

# Extracts each "Playback N [" raw integer, one per line, in the order it
# appears in `amixer sget` output. Never lets a zero-match grep trip
# errexit-style callers -- always exits 0; callers check emptiness
# themselves.
extract_playback_raws() {
  grep -oE 'Playback [0-9]+ \[' 2>/dev/null | grep -oE '[0-9]+' || true
}

# Reads Array 'PCM',1 and prints its single raw value on stdout, or prints
# nothing and returns 1 if the read failed or did not parse to exactly one
# value.
read_array_raw() {
  local output raws n
  if ! output="$(amixer -c Array sget 'PCM',1 2>&1)"; then
    echo "ARRAY_READ_FAILED: $output" >&2
    return 1
  fi
  raws="$(printf '%s\n' "$output" | extract_playback_raws)"
  n="$(printf '%s\n' "$raws" | grep -c . || true)"
  if [[ "$n" -ne 1 ]]; then
    echo "ARRAY_READ_UNPARSEABLE (expected exactly 1 value, got ${n}): $output" >&2
    return 1
  fi
  printf '%s\n' "$raws"
}

# Reads UACDemoV10 PCM and prints its two raw channel values (one per
# line), or prints nothing and returns 1 if the read failed or did not
# parse to exactly two values.
read_uac_raws() {
  local output raws n
  if ! output="$(amixer -c UACDemoV10 sget PCM 2>&1)"; then
    echo "UAC_READ_FAILED: $output" >&2
    return 1
  fi
  raws="$(printf '%s\n' "$output" | extract_playback_raws)"
  n="$(printf '%s\n' "$raws" | grep -c . || true)"
  if [[ "$n" -ne 2 ]]; then
    echo "UAC_READ_UNPARSEABLE (expected exactly 2 channel values, got ${n}): $output" >&2
    return 1
  fi
  printf '%s\n' "$raws"
}

# ---- Correction 2: validated prechecks/postchecks, never a bare print -- #

validate_array_raw() {
  local expected="$1" label="$2" raw
  if ! raw="$(read_array_raw)"; then
    echo "PRECHECK_FAIL: Array 'PCM',1 unreadable (${label})"
    return 1
  fi
  if [[ "$raw" != "$expected" ]]; then
    echo "PRECHECK_FAIL: Array 'PCM',1 = ${raw} (raw), expected ${expected} (${label})"
    return 1
  fi
  echo "PRECHECK_OK: Array 'PCM',1 = ${raw} (raw), matches expected ${expected} (${label})"
  return 0
}

validate_uac_raw() {
  local expected="$1" raws r
  if ! raws="$(read_uac_raws)"; then
    echo "PRECHECK_FAIL: UACDemoV10 unreadable"
    return 1
  fi
  while IFS= read -r r; do
    if [[ "$r" != "$expected" ]]; then
      echo "PRECHECK_FAIL: UACDemoV10 channel = ${r} (raw), expected ${expected} (its own verified MAX)"
      return 1
    fi
  done <<< "$raws"
  echo "PRECHECK_OK: UACDemoV10 both channels = ${expected} (raw), matches its own verified MAX baseline"
  return 0
}

# ---- State (set throughout; read by finalize()/rollback()) ------------ #
PRECHECK_STATUS=""        # "" until checked; "ok" or "failed"
SET_STATUS=""              # "" until attempted; "ok" or "failed"
RUN_STATUS_LABEL="not_started"
PROBE_EXIT_CODE=""
ROLLBACK_DONE=0
ROLLBACK_STATUS=1          # pessimistic default; 0 only on a VERIFIED restore
FINALIZED=0
CHILD_PID=""
INTERRUPTED=0

# ---- Correction 3: idempotent rollback, own outcome, verified -------- #
rollback() {
  if [[ "$ROLLBACK_DONE" -eq 1 ]]; then
    return
  fi
  ROLLBACK_DONE=1
  echo "ROLLBACK_BEGIN: restoring Array 'PCM',1 to raw ${BASELINE_RAW} (-20.00dB)"
  if ! amixer -c Array sset 'PCM',1 "${BASELINE_RAW}" >/dev/null 2>&1; then
    ROLLBACK_STATUS=1
    echo "ROLLBACK_OUTCOME=set_failed"
  elif validate_array_raw "${BASELINE_RAW}" "post-rollback baseline"; then
    ROLLBACK_STATUS=0
    echo "ROLLBACK_OUTCOME=clean"
  else
    ROLLBACK_STATUS=1
    echo "ROLLBACK_OUTCOME=readback_mismatch"
  fi
  echo "UAC_READBACK (must be unchanged -- never written by this script):"
  amixer -c UACDemoV10 sget PCM 2>&1 || echo "UAC_READBACK_FAILED"
}

# ---- Correction 4: explicit INT/TERM handling, own status, no chaining #
on_signal() {
  local sig="$1"
  if [[ "$INTERRUPTED" -eq 1 ]]; then
    return
  fi
  INTERRUPTED=1
  RUN_STATUS_LABEL="interrupted_${sig}"
  echo "INTERRUPTED: received SIG${sig}"
  if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    echo "STOPPING wrapper-owned child (pid ${CHILD_PID}) via SIGTERM"
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    local waited=0
    while kill -0 "$CHILD_PID" 2>/dev/null && [[ "$waited" -lt 50 ]]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    if kill -0 "$CHILD_PID" 2>/dev/null; then
      echo "Child still alive after grace period -- sending SIGKILL"
      kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    wait "$CHILD_PID" 2>/dev/null
  fi
  case "$sig" in
    INT) exit 130 ;;
    TERM) exit 143 ;;
  esac
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

# ---- Correction 5 (finalize): single point that always runs on exit --- #
# NOTE (limitation, stated per instruction, not hidden): a SIGKILL sent to
# THIS wrapper process, or a host power loss, cannot be intercepted by any
# shell trap -- neither this EXIT trap nor the INT/TERM handlers above can
# run in that case, and rollback would NOT happen. There is no way to give
# an unconditional guarantee here; an operator who kills -9 this script,
# or who loses power mid-run, must manually verify and, if needed, restore
# Array 'PCM',1 to raw 40 afterward.
finalize() {
  local rc=$?
  if [[ "$FINALIZED" -eq 1 ]]; then
    return
  fi
  FINALIZED=1
  rollback
  echo "RUN_STATUS_LABEL=${RUN_STATUS_LABEL}"
  echo "PROBE_EXIT_CODE=${PROBE_EXIT_CODE:-n/a}"
  echo "ROLLBACK_STATUS=${ROLLBACK_STATUS}"
  if [[ "$rc" -ne 0 ]]; then
    echo "FINAL_EXIT_CODE=${rc} (preserving the run's own non-zero outcome)"
    exit "$rc"
  fi
  if [[ "$ROLLBACK_STATUS" -ne 0 ]]; then
    echo "WRAPPER_FAILURE: rollback did not verify -- exiting nonzero despite an otherwise successful run" >&2
    echo "FINAL_EXIT_CODE=91"
    exit 91
  fi
  echo "FINAL_EXIT_CODE=0"
  exit 0
}
trap finalize EXIT

# ---- Precheck (Correction 2): abort BEFORE any mutation on mismatch --- #
echo "PRECHECK (validated, read-only):"
if validate_array_raw "${BASELINE_RAW}" "current baseline before condition ${CONDITION}" \
   && validate_uac_raw "${UAC_EXPECTED_RAW}"; then
  PRECHECK_STATUS="ok"
else
  PRECHECK_STATUS="failed"
  echo "ABORTING before any mutation: precheck did not pass." >&2
  exit 90
fi

# ---- Set condition + verify (Correction 2 + 3's "partial write") ------ #
echo "SET_CONDITION_${CONDITION}: Array 'PCM',1 -> raw ${TARGET_RAW}"
if amixer -c Array sset 'PCM',1 "${TARGET_RAW}" >/dev/null 2>&1 \
   && validate_array_raw "${TARGET_RAW}" "condition ${CONDITION} target"; then
  SET_STATUS="ok"
else
  SET_STATUS="failed"
  echo "ABORTING: condition ${CONDITION} mixer write/readback did not verify (possibly partial)." >&2
  RUN_STATUS_LABEL="set_failed"
  exit 92
fi

# ---- Correction 6: current-run artifact inventory marker -------------- #
MARKER="$(mktemp)"
touch "$MARKER"

# ---- Probe launch (Correction 1: no errexit across this command) ------ #
# TEST-ONLY: the real probe never reads these; a fake launcher (used only
# by tests/test_run_r0057_gain_ab_condition_sh.py) uses them to know where
# to write its own fake artifacts, since JSON_DIR/PCM_DIR are computed
# from CAPTURE_ROOT above and may be a temp directory during a test.
export R0057_AB_RUN_JSON_DIR="$JSON_DIR"
export R0057_AB_RUN_PCM_DIR="$PCM_DIR"

if [[ -n "${R0057_AB_PROBE_LAUNCHER:-}" ]]; then
  # TEST-ONLY: a single fake executable replaces the whole command line.
  PROBE_CMD=("$R0057_AB_PROBE_LAUNCHER")
else
  PROBE_CMD=("$REPO_ROOT/.venv/bin/python" -u "$PROBE" \
    --warmup-seconds 60 --level max --repeats 3 \
    --capture-pcm --max-lag-ms 500)
fi

echo "RUNNING PROBE (condition ${CONDITION})"
# No errexit is enabled anywhere in this script (Correction 1) -- a
# nonzero exit from `timeout`/the probe is captured below via a plain
# `$?` read on the very next line, exactly like every other explicit
# status check in this script; there is no `set -e`/`set +e` bracket
# to get wrong here.
timeout --signal=TERM --kill-after="${TIMEOUT_KILL_AFTER_S}s" "${TIMEOUT_BOUND_S}s" \
  "${PROBE_CMD[@]}" &
CHILD_PID=$!
wait "$CHILD_PID"
PROBE_EXIT_CODE=$?
CHILD_PID=""
RUN_STATUS_LABEL="probe_exit_${PROBE_EXIT_CODE}"
echo "EXIT_CODE=${PROBE_EXIT_CODE}"

if [[ "$PROBE_EXIT_CODE" -ne 0 ]]; then
  echo "INVALID RUN: probe did not exit 0 (EXIT_CODE=${PROBE_EXIT_CODE})." >&2
  exit "$PROBE_EXIT_CODE"
fi

# ---- Correction 6: archive this run's own artifacts, hashed, mapped --- #
mkdir -p "$ARCHIVE_DIR/pcm"
NEW_JSON="$(find "$JSON_DIR" -maxdepth 1 -type f -name 'self_echo_probe_*.json' -newer "$MARKER" 2>/dev/null || true)"
NEW_WAV="$(find "$PCM_DIR" -maxdepth 1 -type f -name '*.wav' -newer "$MARKER" 2>/dev/null || true)"
rm -f "$MARKER"

ARCHIVE_OK=1
if [[ -n "$NEW_JSON" ]]; then
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    src_hash="$(sha256sum "$f" | awk '{print $1}')"
    if cp "$f" "$ARCHIVE_DIR/"; then
      dst_hash="$(sha256sum "$ARCHIVE_DIR/$(basename "$f")" | awk '{print $1}')"
      if [[ "$src_hash" == "$dst_hash" ]]; then
        rm -f "$f"
      else
        echo "ARCHIVE_HASH_MISMATCH: $f" >&2
        ARCHIVE_OK=0
      fi
    else
      echo "ARCHIVE_COPY_FAILED: $f" >&2
      ARCHIVE_OK=0
    fi
  done <<< "$NEW_JSON"
else
  echo "ARCHIVE_WARNING: no new results JSON found for this run (marker-based inventory)." >&2
  ARCHIVE_OK=0
fi

if [[ -n "$NEW_WAV" ]]; then
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    src_hash="$(sha256sum "$f" | awk '{print $1}')"
    if cp "$f" "$ARCHIVE_DIR/pcm/"; then
      dst_hash="$(sha256sum "$ARCHIVE_DIR/pcm/$(basename "$f")" | awk '{print $1}')"
      if [[ "$src_hash" == "$dst_hash" ]]; then
        rm -f "$f"
      else
        echo "ARCHIVE_HASH_MISMATCH: $f" >&2
        ARCHIVE_OK=0
      fi
    else
      echo "ARCHIVE_COPY_FAILED: $f" >&2
      ARCHIVE_OK=0
    fi
  done <<< "$NEW_WAV"
fi

{
  echo "R0057 gain A/B experiment -- archived artifacts"
  echo "condition: ${CONDITION}"
  echo "run_timestamp_utc: ${TS}"
  echo "All JSON and WAV files listed below came from this SAME single probe invocation."
  echo
  echo "JSON:"
  for f in "$ARCHIVE_DIR"/self_echo_probe_*.json; do
    [[ -e "$f" ]] || continue
    echo "  $(basename "$f")  sha256=$(sha256sum "$f" | awk '{print $1}')"
  done
  echo
  echo "PCM (WAV):"
  for f in "$ARCHIVE_DIR"/pcm/*.wav; do
    [[ -e "$f" ]] || continue
    echo "  pcm/$(basename "$f")  sha256=$(sha256sum "$f" | awk '{print $1}')"
  done
} > "$ARCHIVE_DIR/MANIFEST.txt"

echo "ARCHIVE_DIR=${ARCHIVE_DIR}"
if [[ "$ARCHIVE_OK" -ne 1 ]]; then
  echo "ARCHIVE_INCOMPLETE: see ARCHIVE_* warnings above -- treat this run's artifacts as unverified." >&2
  RUN_STATUS_LABEL="archive_incomplete"
  exit 93
fi

RUN_STATUS_LABEL="probe_exit_0_archived"
exit 0
# finalize() (EXIT trap) runs automatically here regardless of the exit
# code above, and always attempts + reports rollback.
