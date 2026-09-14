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
# R0057_gain_ab_experiment_procedure.md, S5 -- EXECUTION success, as this
# script reports it, is NOT the same thing as SCIENTIFIC validity; see that
# document's own explicit distinction) before condition B is run -- this
# script does not itself require two separate approvals, one per condition,
# but it also never chains A and B automatically: each invocation runs
# exactly one condition, and the operator decides whether to proceed.
# ============================================================================
#
# R0062 CORRECTIONS (this revision) to the R0061 draft, found in review --
# see docs/reports/R0062_..._20260913.md for the full rationale and the
# empirical checks behind each:
#   1. No mutation is attempted after a failed precheck. `MUTATION_ATTEMPTED`
#      is set to 1 IMMEDIATELY before the first condition-setting `amixer
#      sset` call, and only then; `rollback()` checks it and SKIPS the
#      hardware write entirely (reporting `ROLLBACK_OUTCOME=skipped_no_mutation`)
#      when it is still 0 -- R0061's own rollback unconditionally wrote
#      Array 'PCM',1 back to the baseline even after a precheck failure that
#      had never touched it. A possibly-partial failed write still sets the
#      flag first, so restoration is still attempted for that case.
#   2. The final hardware state is independently validated, not merely
#      printed: `validate_uac_raw` is now called (read-only) as part of
#      rollback's own final check, and an unreadable or mismatched UAC
#      reading now prevents a successful wrapper exit -- UACDemoV10 is
#      NEVER written to correct it, only reported. `read_array_raw`/
#      `read_uac_raws` also now check the control's own identity line,
#      `Limits:` range, and `[on]` switch state -- not just that SOME
#      "Playback N [" pattern happens to appear in the output.
#   3. `timeout`'s supervised command now runs via `setsid`, in its own new
#      session/process group; the actual PGID is read back with `ps -o
#      pgid=` (verified empirically this checkpoint: `timeout`, run this
#      way, does not put its own child in a separate nested group -- the
#      probe and anything IT spawns share the SAME group). Both the
#      INT/TERM signal path and the normal post-probe path now terminate
#      that WHOLE process group (`kill -TERM/-KILL -- "-$PGID"`, never an
#      unrelated PID) with a bounded TERM-then-KILL escalation, and VERIFY
#      via `pgrep -g` that nothing remains in that group before reporting
#      clean cleanup.
#   4. Artifact acceptance now requires EXACTLY the expected evidence for a
#      claimed-successful 3-trial condition: 1 results JSON reporting
#      exactly 3 trials, and exactly 6 WAV files (`max_trial{1,2,3}_
#      {mic,ref}.wav` -- the real probe's own fixed names, unchanged here).
#      Archiving is now attempted ALWAYS, from `finalize()`, AFTER rollback
#      (so it never delays hardware restoration) -- so partial evidence
#      from an interrupted or failed run is preserved too, not only a
#      successful run's own. A `MANIFEST.txt` now also maps each ORIGINAL
#      path the JSON itself records for its own WAV files (parsed from the
#      untouched JSON, never rewritten) to where that file was actually
#      archived, with its hash. A lock directory
#      (`.gain_ab_experiment.lock`) under the capture root prevents two
#      wrapper invocations from ever running concurrently against the same
#      capture locations -- `find -newer` alone was never sufficient proof
#      of run ownership, only a weaker signal on top of this lock.
#   5. Directory creation, the run marker, every copy/hash/manifest-write,
#      and the persisted-log setup are now checked explicitly. A failure
#      creating the capture directories or acquiring the concurrency lock
#      aborts (exit 95 / 94) BEFORE the precheck ever runs, so hardware
#      mutation can never happen before evidence storage is confirmed
#      ready.
#   6. (Test-double fixes, not this script itself -- see test_fakes/ and
#      tests/test_run_r0057_gain_ab_condition_sh.py.)
#
# Runs exactly ONE condition (A or B) of the previously-designed R0057 gain
# A/B experiment (docs/reports/R0056_..., R0057_..., R0059_...; procedure
# finalized in R0060, corrected in R0061 and R0062,
# docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md).
# One probe invocation per condition, combining a fresh 60s warm-up with 3
# measured silent trials (never a reuse of R0059's own separate,
# warmup-only run).
#
# Condition A: Array 'PCM',1 = 40 (raw)  = -20.00dB (the currently-accepted,
#              already-live baseline).
# Condition B: Array 'PCM',1 = 60 (raw)  =   0.00dB (the exact top of the
#              control's own supported range).
#
# UACDemoV10 (the audible speaker mixer) is NEVER written by this script --
# only ever read and VALIDATED (identity, limits, switch state, and both
# channels' raw value), before any mutation is attempted AND again at the
# very end.
#
# Exit code convention (all documented, all distinguishable in the log):
#   0    -- fully successful EXECUTION: precheck OK, condition set+verified,
#           probe exited 0 with exactly the expected 3-trial evidence
#           archived, final hardware state verified, rollback verified.
#           This is NOT the same as SCIENTIFIC validity -- see the
#           procedure document's own S5.
#   2    -- usage/argument error (no hardware touched).
#   90   -- precheck failed (mismatched or unreadable mixer state) --
#           aborted BEFORE any mutation; NO hardware write is attempted
#           (see Correction 1).
#   92   -- the condition's own mixer write or its post-write readback
#           verification failed (a possibly-partial write) -- restoration
#           is still attempted.
#   93   -- the probe exited 0, but this run's own archived evidence is
#           not EXACTLY the expected 1 JSON (3 trials) + 6 WAV files.
#   91   -- rollback did not verify, OR the final UACDemoV10 check did not
#           verify -- overrides an otherwise-successful 0.
#   94   -- another wrapper invocation already holds the concurrency lock
#           for this capture root -- nothing touched.
#   95   -- required evidence-storage directories could not be created --
#           nothing touched.
#   130/143 -- interrupted by SIGINT/SIGTERM respectively; termination and
#           rollback are still attempted.
#   <probe's own code> -- otherwise, e.g. 1 (Python exception), 124
#           (external `timeout` fired) -- preserved as-is.
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
# Run condition A FIRST, inspect its own results/log/archive/validity
# criteria (see the procedure doc) BEFORE running condition B. This script
# will not chain into a second condition under any circumstance.

set -uo pipefail
# `set -e` is intentionally NOT enabled anywhere in this script -- it
# aborts at a plain failing command BEFORE the very next line (an `rc=$?`
# capture, or any of this script's own parsing assignments) ever runs.
# Every command whose failure is part of normal control flow is checked
# explicitly (`if ! cmd; then ...`) instead.

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
EXPECTED_LEVEL="max"      # matches --level below; used to name expected WAVs.
EXPECTED_TRIALS=3         # matches --repeats below.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PROBE="$REPO_ROOT/docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py"

CAPTURE_ROOT="${R0057_AB_CAPTURE_ROOT:-$REPO_ROOT/docs/research/m2_6_cloud_realtime_voice/self_echo_captures}"
JSON_DIR="$CAPTURE_ROOT"
PCM_DIR="$CAPTURE_ROOT/pcm"
LOG_DIR="$CAPTURE_ROOT/warmup_hang_logs"
ARCHIVE_ROOT="$CAPTURE_ROOT/gain_ab_experiment"
LOCK_DIR="$CAPTURE_ROOT/.gain_ab_experiment.lock"

TIMEOUT_BOUND_S="${R0057_AB_TIMEOUT_BOUND_S:-240}"
TIMEOUT_KILL_AFTER_S="${R0057_AB_TIMEOUT_KILL_AFTER_S:-15}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/gain_ab_condition_${CONDITION}_${TS}.log"
ARCHIVE_DIR="$ARCHIVE_ROOT/${TS}_condition_${CONDITION}"

# ---- State (set throughout; read by finalize()/rollback()) ------------ #
MUTATION_ATTEMPTED=0       # Correction 1: only 1 once the condition sset is issued.
PRECHECK_STATUS=""
SET_STATUS=""
RUN_STATUS_LABEL="not_started"
PROBE_EXIT_CODE=""
ROLLBACK_DONE=0
ROLLBACK_STATUS=1          # pessimistic default; 0 only on verified restore/skip
ROLLBACK_OUTCOME_LABEL="not_run"
UAC_FINAL_STATUS=1         # pessimistic default; 0 only on a verified reading
FINALIZED=0
LOCK_ACQUIRED=0
CHILD_PID=""
CHILD_PGID=""
INTERRUPTED=0
ARCHIVE_JSON_COUNT=0
ARCHIVE_WAV_COUNT=0
ARCHIVE_COMPLETE=0
ARCHIVE_OK=1
MARKER=""

# ---- Correction 3: bounded, verified whole-process-group termination -- #
# Sends `sig` to the ENTIRE process group `pgid` (never a bare PID --
# confirmed this checkpoint, via a standalone experiment, that `timeout`
# run under `setsid` shares ONE process group with everything it
# supervises, so targeting that one group reaches the probe and anything
# it spawns). Escalates from TERM to KILL after `grace_s` if needed, and
# does not return success until `pgrep -g` confirms the group is empty or
# a bounded number of polls is exhausted.
terminate_process_group() {
  local pgid="$1" grace_s="${2:-5}"
  if [[ -z "$pgid" ]]; then
    return 0
  fi
  if ! pgrep -g "$pgid" >/dev/null 2>&1; then
    return 0
  fi
  kill -TERM -- "-${pgid}" 2>/dev/null || true
  local waited=0 max_polls=$((grace_s * 10))
  while pgrep -g "$pgid" >/dev/null 2>&1 && [[ "$waited" -lt "$max_polls" ]]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if pgrep -g "$pgid" >/dev/null 2>&1; then
    echo "PROCESS_GROUP_STILL_ALIVE_AFTER_TERM pgid=${pgid} -- escalating to SIGKILL"
    kill -KILL -- "-${pgid}" 2>/dev/null || true
    waited=0
    while pgrep -g "$pgid" >/dev/null 2>&1 && [[ "$waited" -lt 30 ]]; do
      sleep 0.1
      waited=$((waited + 1))
    done
  fi
  if pgrep -g "$pgid" >/dev/null 2>&1; then
    echo "PROCESS_GROUP_TERMINATION_FAILED pgid=${pgid}: process(es) still present" >&2
    return 1
  fi
  return 0
}

# ---- Parsing helpers -------------------------------------------------- #

# Extracts each "Playback N [" raw integer, one per line, in the order it
# appears in `amixer sget` output. Never lets a zero-match grep trip
# errexit-style callers -- always exits 0; callers check emptiness
# themselves.
extract_playback_raws() {
  grep -oE 'Playback [0-9]+ \[' 2>/dev/null | grep -oE '[0-9]+' || true
}

# Correction 2: reads Array 'PCM',1 -- validates the CONTROL IDENTITY
# (its own "Simple mixer control" line), its LIMITS (0-60), and that the
# switch is [on], not just that some "Playback N [" text happens to
# appear -- before ever parsing a raw value out of it. Prints the single
# raw value on stdout, or prints nothing and returns 1 on any failure.
read_array_raw() {
  local output raws n
  if ! output="$(amixer -c Array sget 'PCM',1 2>&1)"; then
    echo "ARRAY_READ_FAILED: $output" >&2
    return 1
  fi
  if [[ "$output" != *"Simple mixer control 'PCM',1"* ]]; then
    echo "ARRAY_READ_UNEXPECTED_CONTROL_IDENTITY: $output" >&2
    return 1
  fi
  if [[ "$output" != *"Limits: Playback 0 - 60"* ]]; then
    echo "ARRAY_READ_UNEXPECTED_LIMITS: $output" >&2
    return 1
  fi
  if [[ "$output" != *"[on]"* ]]; then
    echo "ARRAY_READ_SWITCH_NOT_ON: $output" >&2
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

# Correction 2: same identity/limits/switch validation for UACDemoV10's
# own two-channel control before parsing its raw values.
read_uac_raws() {
  local output raws n
  if ! output="$(amixer -c UACDemoV10 sget PCM 2>&1)"; then
    echo "UAC_READ_FAILED: $output" >&2
    return 1
  fi
  if [[ "$output" != *"Simple mixer control 'PCM',0"* ]]; then
    echo "UAC_READ_UNEXPECTED_CONTROL_IDENTITY: $output" >&2
    return 1
  fi
  if [[ "$output" != *"Limits: Playback 0 - 147"* ]]; then
    echo "UAC_READ_UNEXPECTED_LIMITS: $output" >&2
    return 1
  fi
  if [[ "$output" != *"[on]"* ]]; then
    echo "UAC_READ_SWITCH_NOT_ON: $output" >&2
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

# ---- Correction 1 + 2: rollback skips the hardware write entirely when
# no mutation was ever attempted; independently validates BOTH the
# restored Array value AND the still-unwritten UACDemoV10 state. ------- #
rollback() {
  if [[ "$ROLLBACK_DONE" -eq 1 ]]; then
    return
  fi
  ROLLBACK_DONE=1

  if [[ "$MUTATION_ATTEMPTED" -ne 1 ]]; then
    echo "ROLLBACK_SKIPPED: no mutation was attempted this run -- Array 'PCM',1 left untouched"
    ROLLBACK_OUTCOME_LABEL="skipped_no_mutation"
    ROLLBACK_STATUS=0
  else
    echo "ROLLBACK_BEGIN: restoring Array 'PCM',1 to raw ${BASELINE_RAW} (-20.00dB)"
    if ! amixer -c Array sset 'PCM',1 "${BASELINE_RAW}" >/dev/null 2>&1; then
      ROLLBACK_STATUS=1
      ROLLBACK_OUTCOME_LABEL="set_failed"
    elif validate_array_raw "${BASELINE_RAW}" "post-rollback baseline"; then
      ROLLBACK_STATUS=0
      ROLLBACK_OUTCOME_LABEL="clean"
    else
      ROLLBACK_STATUS=1
      ROLLBACK_OUTCOME_LABEL="readback_mismatch"
    fi
    echo "ROLLBACK_OUTCOME=${ROLLBACK_OUTCOME_LABEL}"
  fi

  echo "UAC_FINAL_CHECK (validated, read-only -- NEVER written by this script):"
  if validate_uac_raw "${UAC_EXPECTED_RAW}"; then
    UAC_FINAL_STATUS=0
  else
    UAC_FINAL_STATUS=1
    echo "UAC_FINAL_CHECK_FAILED: UACDemoV10 did not read back as expected -- this script never writes UACDemoV10 to correct it" >&2
  fi
}

# ---- Correction 3: explicit INT/TERM handling, whole-group termination,
# own status, no chaining into another condition. ----------------------- #
on_signal() {
  local sig="$1"
  if [[ "$INTERRUPTED" -eq 1 ]]; then
    return
  fi
  INTERRUPTED=1
  RUN_STATUS_LABEL="interrupted_${sig}"
  echo "INTERRUPTED: received SIG${sig}"
  if [[ -n "$CHILD_PGID" ]]; then
    echo "STOPPING wrapper-owned process group (pgid ${CHILD_PGID})"
    if terminate_process_group "$CHILD_PGID" 5; then
      echo "PROCESS_GROUP_TERMINATED pgid=${CHILD_PGID}"
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

# ---- Correction 4: always-attempted, best-effort archive of THIS run's
# own evidence, called from finalize() AFTER rollback so it never delays
# hardware restoration. Requires EXACTLY the expected 1 JSON (reporting
# EXACTLY EXPECTED_TRIALS trials) + 6 WAV files for ARCHIVE_COMPLETE=1. -- #
archive_current_run_artifacts() {
  if [[ -z "$MARKER" || ! -e "$MARKER" ]]; then
    # Nothing was ever set up to measure "new since" against (e.g. a
    # very early abort) -- there is nothing of this run's own to archive.
    return
  fi
  if ! mkdir -p "$ARCHIVE_DIR/pcm" 2>/dev/null; then
    echo "ARCHIVE_DIR_CREATE_FAILED: $ARCHIVE_DIR" >&2
    ARCHIVE_OK=0
    return
  fi

  local new_json new_wav f src_hash dst_hash mapping_lines="" trial_count
  new_json="$(find "$JSON_DIR" -maxdepth 1 -type f -name 'self_echo_probe_*.json' -newer "$MARKER" 2>/dev/null || true)"
  new_wav="$(find "$PCM_DIR" -maxdepth 1 -type f -name '*.wav' -newer "$MARKER" 2>/dev/null || true)"

  if [[ -n "$new_json" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      local paths
      paths="$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print(len(d.get('trials', [])))
for t in d.get('trials', []):
    c = t.get('cross_correlation') or {}
    for key in ('mic_wav', 'ref_wav'):
        if c.get(key):
            print(c[key])
" "$f" 2>/dev/null || true)"
      trial_count="$(printf '%s\n' "$paths" | head -1)"
      mapping_lines="${mapping_lines}$(printf '%s\n' "$paths" | tail -n +2)"$'\n'
    done <<< "$new_json"
  fi
  ARCHIVE_JSON_TRIAL_COUNT="${trial_count:-0}"

  if [[ -n "$new_json" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      ARCHIVE_JSON_COUNT=$((ARCHIVE_JSON_COUNT + 1))
      if ! src_hash="$(sha256sum "$f" 2>/dev/null | awk '{print $1}')"; then
        echo "ARCHIVE_HASH_FAILED: $f" >&2; ARCHIVE_OK=0; continue
      fi
      if ! cp "$f" "$ARCHIVE_DIR/" 2>/dev/null; then
        echo "ARCHIVE_COPY_FAILED: $f" >&2; ARCHIVE_OK=0; continue
      fi
      if ! dst_hash="$(sha256sum "$ARCHIVE_DIR/$(basename "$f")" 2>/dev/null | awk '{print $1}')"; then
        echo "ARCHIVE_HASH_FAILED: $ARCHIVE_DIR/$(basename "$f")" >&2; ARCHIVE_OK=0; continue
      fi
      if [[ "$src_hash" != "$dst_hash" ]]; then
        echo "ARCHIVE_HASH_MISMATCH: $f" >&2; ARCHIVE_OK=0; continue
      fi
      rm -f "$f"
    done <<< "$new_json"
  fi

  if [[ -n "$new_wav" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      ARCHIVE_WAV_COUNT=$((ARCHIVE_WAV_COUNT + 1))
      if ! src_hash="$(sha256sum "$f" 2>/dev/null | awk '{print $1}')"; then
        echo "ARCHIVE_HASH_FAILED: $f" >&2; ARCHIVE_OK=0; continue
      fi
      if ! cp "$f" "$ARCHIVE_DIR/pcm/" 2>/dev/null; then
        echo "ARCHIVE_COPY_FAILED: $f" >&2; ARCHIVE_OK=0; continue
      fi
      if ! dst_hash="$(sha256sum "$ARCHIVE_DIR/pcm/$(basename "$f")" 2>/dev/null | awk '{print $1}')"; then
        echo "ARCHIVE_HASH_FAILED: $ARCHIVE_DIR/pcm/$(basename "$f")" >&2; ARCHIVE_OK=0; continue
      fi
      if [[ "$src_hash" != "$dst_hash" ]]; then
        echo "ARCHIVE_HASH_MISMATCH: $f" >&2; ARCHIVE_OK=0; continue
      fi
      rm -f "$f"
    done <<< "$new_wav"
  fi

  if ! {
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
    echo
    echo "ORIGINAL JSON WAV PATH -> ARCHIVED FILE (the JSON itself is never rewritten):"
    if [[ -n "${mapping_lines// /}" ]]; then
      while IFS= read -r orig; do
        [[ -n "$orig" ]] || continue
        base="$(basename "$orig")"
        archived_path="$ARCHIVE_DIR/pcm/${base}"
        if [[ -e "$archived_path" ]]; then
          h="$(sha256sum "$archived_path" | awk '{print $1}')"
          echo "  ${orig} -> pcm/${base}  sha256=${h}"
        else
          echo "  ${orig} -> MISSING (never archived)"
        fi
      done <<< "$mapping_lines"
    else
      echo "  (no JSON-embedded WAV paths found)"
    fi
  } > "$ARCHIVE_DIR/MANIFEST.txt" 2>/dev/null; then
    echo "MANIFEST_WRITE_FAILED: $ARCHIVE_DIR/MANIFEST.txt" >&2
    ARCHIVE_OK=0
  fi

  rm -f "$MARKER" 2>/dev/null

  local expected_wav_names=(
    "${EXPECTED_LEVEL}_trial1_mic.wav" "${EXPECTED_LEVEL}_trial1_ref.wav"
    "${EXPECTED_LEVEL}_trial2_mic.wav" "${EXPECTED_LEVEL}_trial2_ref.wav"
    "${EXPECTED_LEVEL}_trial3_mic.wav" "${EXPECTED_LEVEL}_trial3_ref.wav"
  )
  local all_present=1 name
  for name in "${expected_wav_names[@]}"; do
    if [[ ! -e "$ARCHIVE_DIR/pcm/$name" ]]; then
      all_present=0
      echo "ARCHIVE_MISSING_EXPECTED_WAV: $name" >&2
    fi
  done
  if [[ "$ARCHIVE_JSON_COUNT" -eq 1 && "$ARCHIVE_JSON_TRIAL_COUNT" -eq "$EXPECTED_TRIALS" \
        && "$all_present" -eq 1 && "$ARCHIVE_OK" -eq 1 ]]; then
    ARCHIVE_COMPLETE=1
  fi
  echo "ARCHIVE_DIR=${ARCHIVE_DIR}"
  echo "ARCHIVE_JSON_COUNT=${ARCHIVE_JSON_COUNT} ARCHIVE_JSON_TRIAL_COUNT=${ARCHIVE_JSON_TRIAL_COUNT:-0} ARCHIVE_WAV_COUNT=${ARCHIVE_WAV_COUNT} ARCHIVE_COMPLETE=${ARCHIVE_COMPLETE}"
}

# ---- finalize(): the ONE point that always runs on exit --------------- #
# NOTE (limitation, stated per instruction, not hidden): a SIGKILL sent to
# THIS wrapper process, or a host power loss, cannot be intercepted by any
# shell trap -- neither this EXIT trap nor the INT/TERM handlers above can
# run in that case, and rollback would NOT happen. An operator who kills
# -9 this script, or who loses power mid-run, must manually verify and, if
# needed, restore Array 'PCM',1 to raw 40 afterward.
finalize() {
  local rc=$?
  if [[ "$FINALIZED" -eq 1 ]]; then
    return
  fi
  FINALIZED=1

  rollback
  archive_current_run_artifacts

  if [[ "$LOCK_ACQUIRED" -eq 1 ]]; then
    rmdir "$LOCK_DIR" 2>/dev/null || echo "LOCK_RELEASE_FAILED: ${LOCK_DIR}" >&2
  fi

  echo "RUN_STATUS_LABEL=${RUN_STATUS_LABEL}"
  echo "PROBE_EXIT_CODE=${PROBE_EXIT_CODE:-n/a}"
  echo "ROLLBACK_OUTCOME=${ROLLBACK_OUTCOME_LABEL} ROLLBACK_STATUS=${ROLLBACK_STATUS}"
  echo "UAC_FINAL_STATUS=${UAC_FINAL_STATUS}"
  echo "ARCHIVE_COMPLETE=${ARCHIVE_COMPLETE}"

  if [[ "$rc" -ne 0 ]]; then
    echo "FINAL_EXIT_CODE=${rc} (preserving the run's own non-zero outcome)"
    exit "$rc"
  fi
  if [[ "$ROLLBACK_STATUS" -ne 0 || "$UAC_FINAL_STATUS" -ne 0 ]]; then
    echo "WRAPPER_FAILURE: final hardware state did not verify -- exiting nonzero despite an otherwise successful run" >&2
    echo "FINAL_EXIT_CODE=91"
    exit 91
  fi
  if [[ "$ARCHIVE_COMPLETE" -ne 1 ]]; then
    echo "WRAPPER_FAILURE: this run's own archived evidence is not exactly the expected 3-trial set -- not a successful condition run" >&2
    echo "FINAL_EXIT_CODE=93"
    exit 93
  fi
  echo "FINAL_EXIT_CODE=0"
  exit 0
}
trap finalize EXIT

# ---- Correction 5: evidence storage must exist and be lockable BEFORE
# any precheck or mutation is ever attempted. ---------------------------- #
if ! mkdir -p "$CAPTURE_ROOT" "$LOG_DIR" "$JSON_DIR" "$PCM_DIR" "$ARCHIVE_ROOT"; then
  echo "FATAL: could not create required evidence-storage directories under ${CAPTURE_ROOT}" >&2
  RUN_STATUS_LABEL="storage_setup_failed"
  exit 95
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "FATAL: another wrapper invocation already holds the concurrency lock (${LOCK_DIR}) -- refusing to run concurrently against the same capture root." >&2
  RUN_STATUS_LABEL="lock_held"
  exit 94
fi
LOCK_ACQUIRED=1

if ! MARKER="$(mktemp)"; then
  echo "FATAL: could not create the run's own artifact-inventory marker file" >&2
  RUN_STATUS_LABEL="storage_setup_failed"
  exit 95
fi
if ! touch "$MARKER"; then
  echo "FATAL: could not initialize the run's own artifact-inventory marker file" >&2
  RUN_STATUS_LABEL="storage_setup_failed"
  exit 95
fi

# Correction 5: capture EVERYTHING (this script's own output AND the
# probe's) into one persisted log, from this point on, while still
# showing it on the terminal. (A failure setting this redirection up
# cannot be reliably detected from inside the same shell -- see the
# procedure document's own explicit note on this residual limitation.)
exec > >(tee -a "$LOG") 2>&1

echo "=== R0057 gain A/B experiment -- condition ${CONDITION} -- ${TS} ==="
echo "LOG=${LOG}"

# ---- Precheck (Correction 1 + 2): abort BEFORE any mutation on mismatch,
# unreadable value, or a control-identity/limits/switch mismatch. ------- #
echo "PRECHECK (validated, read-only):"
if validate_array_raw "${BASELINE_RAW}" "current baseline before condition ${CONDITION}" \
   && validate_uac_raw "${UAC_EXPECTED_RAW}"; then
  PRECHECK_STATUS="ok"
else
  PRECHECK_STATUS="failed"
  echo "ABORTING before any mutation: precheck did not pass." >&2
  RUN_STATUS_LABEL="precheck_failed"
  exit 90
fi

# ---- Set condition + verify (Correction 1's mutation flag; Correction 2's
# stricter validation catches a possibly-partial write). ---------------- #
echo "SET_CONDITION_${CONDITION}: Array 'PCM',1 -> raw ${TARGET_RAW}"
MUTATION_ATTEMPTED=1
if amixer -c Array sset 'PCM',1 "${TARGET_RAW}" >/dev/null 2>&1 \
   && validate_array_raw "${TARGET_RAW}" "condition ${CONDITION} target"; then
  SET_STATUS="ok"
else
  SET_STATUS="failed"
  echo "ABORTING: condition ${CONDITION} mixer write/readback did not verify (possibly partial)." >&2
  RUN_STATUS_LABEL="set_failed"
  exit 92
fi

# ---- Probe launch (Correction 3: own process group, tracked and later
# verified empty). ------------------------------------------------------- #
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
    --warmup-seconds 60 --level "$EXPECTED_LEVEL" --repeats "$EXPECTED_TRIALS" \
    --capture-pcm --max-lag-ms 500)
fi

echo "RUNNING PROBE (condition ${CONDITION})"
setsid timeout --signal=TERM --kill-after="${TIMEOUT_KILL_AFTER_S}s" "${TIMEOUT_BOUND_S}s" \
  "${PROBE_CMD[@]}" &
CHILD_PID=$!
CHILD_PGID="$(ps -o pgid= -p "$CHILD_PID" 2>/dev/null | tr -d ' ')"
if [[ -z "$CHILD_PGID" ]]; then
  CHILD_PGID="$CHILD_PID"
fi
echo "CHILD_PID=${CHILD_PID} CHILD_PGID=${CHILD_PGID}"
wait "$CHILD_PID"
PROBE_EXIT_CODE=$?
# Correction 3: `wait` returning only proves the SESSION LEADER (timeout)
# has been reaped -- it does not by itself prove every other member of
# the group has exited too (an orphaned descendant could still be
# running). Verify, and escalate/terminate if anything remains.
if ! terminate_process_group "$CHILD_PGID" 5; then
  echo "WARNING: process group ${CHILD_PGID} was not confirmed empty after the probe's own exit" >&2
fi
CHILD_PID=""
CHILD_PGID=""
RUN_STATUS_LABEL="probe_exit_${PROBE_EXIT_CODE}"
echo "EXIT_CODE=${PROBE_EXIT_CODE}"

if [[ "$PROBE_EXIT_CODE" -ne 0 ]]; then
  echo "INVALID RUN: probe did not exit 0 (EXIT_CODE=${PROBE_EXIT_CODE})." >&2
  exit "$PROBE_EXIT_CODE"
fi

RUN_STATUS_LABEL="probe_exit_0"
exit 0
# finalize() (EXIT trap) runs automatically here regardless of the exit
# code above: it always attempts rollback, then archiving, in that order,
# and always reports both plus the final, authoritative exit code.
