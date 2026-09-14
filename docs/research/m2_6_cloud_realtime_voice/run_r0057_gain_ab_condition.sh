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
# R0063 CORRECTIONS (this revision) to the R0062 draft, found in an external
# source review -- see docs/reports/R0063_..._20260914.md for the full
# rationale and the tests behind each:
#   1. Deterministic startup ownership handshake. R0062's own
#      `ps -o pgid= -p "$CHILD_PID"`, read immediately after backgrounding
#      `setsid timeout ... &`, could sample BEFORE `setsid()` actually took
#      effect inside the child -- in that window the child still shares the
#      WRAPPER's OWN process group, so the observed "PGID" could actually be
#      the wrapper's own group, and every later group-directed signal would
#      then target the wrapper itself (and anything else sharing that
#      group), never the probe. The R0062 fallback (`PGID=PID` when `ps`
#      returned nothing) did not establish ownership either -- it was a
#      guess, not a proof. Fixed: the launched child itself (a
#      `setsid bash -c '...'` wrapper around the real command) writes its
#      OWN `$$` -- which, for a process that has just called setsid(), IS
#      both its new pid and its new pgid -- to a marker file STRICTLY AFTER
#      setsid() has already completed and STRICTLY BEFORE it execs the real
#      command. The parent never samples speculatively; it only reads what
#      the child itself already confirmed, bounded and polled. The result is
#      independently cross-checked against the wrapper's OWN current pgid
#      (rejected if they match) before `CHILD_PGID_VERIFIED` is ever set --
#      no SECOND `ps`-based re-check of the child's own pgid is performed
#      (an earlier draft of this fix had one; it was found, during this
#      same checkpoint, to introduce a NEW race against a fast-completing
#      child, and was removed -- see ROUND 2 item 1 below for the further
#      hardening this whole mechanism received after external review). A
#      signal arriving during this handshake (before ownership is verified)
#      terminates the known CHILD_PID directly -- never a group signal,
#      since group ownership is not yet established -- so it can never leak
#      the child or reach an unrelated process.
#   2. Cleanup failure now prevents a successful exit. Previously, a failed
#      `terminate_process_group` only printed a warning and execution
#      continued straight to the probe's own exit status -- a run could
#      still finish with `FINAL_EXIT_CODE=0` while a member of the probe's
#      own process group was still alive. Fixed: `CLEANUP_STATUS` is
#      persisted (optimistic default -- nothing was ever launched, so
#      nothing to clean up -- flipped to failed ONLY by an actually-failed,
#      actually-attempted termination) and checked by `finalize()`; an
#      unverified termination now forces exit `96`, distinct from every
#      other failure class. `pgid_liveness()` now distinguishes `pgrep`
#      reporting "no matches" (a confirmed-empty group) from `pgrep` itself
#      failing to inspect (a syntax/fatal error) -- an inspection failure is
#      NEVER reported as verified emptiness, at any point in
#      `terminate_process_group`'s own escalation logic. Rollback is still
#      always attempted regardless (via `finalize()`'s own unconditional
#      call), and archiving still preserves whatever evidence exists -- but
#      the run's own FINAL reported outcome can no longer silently claim a
#      stable, complete capture while a writer may still be alive. No
#      unbounded wait was added anywhere in the signal path.
#   3. Persisted-log readiness is established BEFORE any mixer interaction
#      (including the precheck reads, a stronger guarantee than "before any
#      write" alone). `exec > >(tee -a "$LOG") 2>&1` only sets up an
#      asynchronous pipe; it proves nothing about whether `tee` actually
#      opened the file. Fixed: a synchronous `: > "$LOG"` probe write
#      happens first (catches a plainly unwritable log directory
#      immediately), then, once the `tee` pipe is set up, a canary line is
#      written and its actual on-disk appearance in `$LOG` is confirmed via
#      a bounded poll before the precheck (or anything else) proceeds. This
#      establishes readiness AT THIS POINT ONLY -- it is not, and does not
#      claim to be, protection against every possible LATER storage failure
#      (e.g. the disk filling up mid-run). NOTE (corrected claim): a
#      log-open failure here means zero mixer WRITES -- it does NOT mean
#      zero mixer interaction of any kind. `finalize()`'s own EXIT trap
#      still runs unconditionally (it was installed earlier) and still
#      calls `rollback()`, whose own UAC_FINAL_CHECK performs one harmless,
#      read-only `amixer -c UACDemoV10 sget` even here (its own output
#      simply never reaches the never-opened `$LOG`, only the process's
#      original stdout/stderr) -- the SAME already-documented behavior as
#      the EARLIER evidence-storage-setup-failure path (`exit 95` from the
#      `mkdir -p`/lock/marker checks above), not a new exception.
#   4. Mixer validation is now exact, not substring-based. `*"'PCM',1"*`
#      also matched `'PCM',10`/`'PCM',11`/...; `*"Limits: Playback 0 - 60"*`
#      also matched "...0 - 600"/"...0 - 160" -- a coincidentally-matching
#      SUPERSET string could pass. Fixed: `read_array_raw`/`read_uac_raws`
#      now require an EXACT, whole-line match (`grep -qxF`) for both the
#      control-identity line and the Limits line. The switch check
#      previously required only ONE `[on]` occurrence anywhere in the
#      output, so a genuinely MIXED UACDemoV10 state (Front Left `[off]`,
#      Front Right `[on]`) still passed -- fixed to require the EXACT
#      expected channel count of `[on]` markers (1 for Array's own Mono
#      channel, 2 for UACDemoV10's own stereo pair) AND zero `[off]`
#      markers anywhere in the output. Separately, `rollback()` now ALWAYS
#      attempts an independent Array readback even when the restore `sset`
#      itself reports failure -- the write's own outcome and the
#      independently OBSERVED final state are reported as two distinct
#      facts, and `ROLLBACK_STATUS`/`ROLLBACK_OUTCOME_LABEL` are derived
#      strictly from the OBSERVED reading, never assumed from the write
#      command's own self-reported exit status alone.
#   5. Archive acceptance now enforces its full stated contract, not just
#      filename presence. `ARCHIVE_COMPLETE` now additionally requires
#      `ARCHIVE_WAV_COUNT` to be EXACTLY `2 * EXPECTED_TRIALS` (never
#      silently accepting extra, untracked WAV files alongside the expected
#      six) and a new structural mapping check: every trial's own recorded
#      `mic_wav`/`ref_wav` path must be one of THIS RUN's own
#      newly-discovered, successfully-archived WAV originals (never a mere
#      basename coincidence with some unrelated file), with zero duplicate
#      references -- `MAPPING_VALID` is folded into the completeness
#      decision. Hash/manifest computation is now performed as a sequence
#      of individually-checked statements (`if ! h="$(sha256sum ...)"`)
#      inside a dedicated function whose own return status is derived from
#      an explicit `manifest_ok` flag set on every individual failure --
#      replacing the previous `if ! { multi-line block; } > file` pattern,
#      whose own exit status only ever reflected its LAST command, and the
#      previous `echo "...$(sha256sum ...)"` pattern, whose embedded
#      command-substitution failure was invisible to the enclosing `echo`'s
#      own (always-zero) exit status. Partial evidence is still archived on
#      an incomplete/inconsistent run, exactly as before -- only the
#      COMPLETENESS verdict is now honest about what was actually verified.
#   6. (Test-double fix, not this script itself -- see
#      `test_fakes/fake_amixer.py`'s own R0063 notes.)
#
# R0063 CORRECTIONS, ROUND 2 (same uncommitted checkpoint, a FURTHER
# external review of the round-1 diff above) -- see the R0063 report's own
# "ROUND 2" section for the full finding-to-fix-to-test table:
#   1. Startup handshake made genuinely TWO-WAY. Round 1 was one-way: the
#      child published its identity and immediately exec'd -- the probe
#      (and any descendants) could already exist before the PARENT finished
#      validating and set CHILD_PGID_VERIFIED=1; an interruption in that
#      window terminated only the (by-then possibly already-exec'd-past)
#      CHILD_PID. Fixed: the child now WAITS (bounded) for an explicit ACK
#      file the parent creates only after full validation, and never execs
#      the real command without it -- by construction, no probe descendant
#      can exist before ownership is verified. The narrower window between
#      "candidate known and confirmed safe" and "ack published" is covered
#      too, via `CHILD_PGID_CANDIDATE` -- `on_signal()` now group-kills
#      that candidate directly (structurally still just the parked child)
#      instead of falling back to a PID-only kill. Marker-publication
#      failure inside the child is now checked (`echo "$$" > ... || exit
#      97`) instead of silently continuing toward `exec`.
#   2. No unbounded waits remain in any failure path. `terminate_by_pid`'s
#      own unconditional trailing `wait "$pid"` could block forever against
#      a process stuck in uninterruptible I/O even after SIGKILL; removed
#      -- now only reaped when already confirmed gone. `on_signal()`'s own
#      `wait "$CHILD_PID"` after a FAILED group termination is removed too
#      -- rollback must be reached within the documented cleanup budget
#      regardless. If cleanup is unverified, evidence is preserved
#      (copied+hashed) but originals are NOT unlinked (a surviving writer
#      may still need them), and the concurrency lock is deliberately
#      RETAINED (`LOCK_QUARANTINED=1`), never auto-cleared, until an
#      operator manually verifies and removes it.
#   3. Mapping validation now rejects a trial with both paths empty. The
#      previous loop SILENTLY SKIPPED a trial row when mic_wav AND ref_wav
#      were both empty -- with 3 trial objects, 6 real WAV files, and only
#      2 populated rows, completeness could still be reported. Fixed:
#      every one of EXPECTED_TRIALS rows is now required, with BOTH fields
#      populated, matching its own EXACT expected per-trial/per-role
#      basename (catching a swapped mic/ref role too) and identifying one
#      of this run's own newly-archived originals. The JSON parser now
#      propagates structural errors (wrong types, missing lists) as an
#      explicit ERROR line instead of silently coercing to empty output.
#   4. Manifest writes and source removal are now checked, not assumed.
#      Every line appended to the manifest goes through `_manifest_append`
#      (checked); the temp file is created INSIDE `$ARCHIVE_DIR` itself
#      (same filesystem as the final `mv` target, a genuine precondition
#      for atomic rename -- the system tmp dir used previously often is
#      not). `archive_one_file`'s own `rm -f "$src"` is now checked
#      (previously unconditionally returned 0 even on a failed removal);
#      it also now accepts an `unlink_src` flag (see item 2) to preserve
#      sources when cleanup is unverified.
#   5. A failed rollback WRITE can no longer be converted into an
#      "operational success" merely because the readback happens to
#      observe the baseline anyway. `ROLLBACK_STATUS=0`/`"clean"` now
#      requires BOTH the write to have reported success AND the readback
#      to confirm baseline; a failed write forces `ROLLBACK_STATUS=1`
#      (label `write_failed_baseline_observed`/`_readback_mismatch`/
#      `_readback_unreadable`) regardless of what is observed --
#      "the write succeeded" and "baseline is observed" are reported as
#      two separate facts, never conflated into one.
#
# R0063 CORRECTIONS, ROUND 3 (same uncommitted checkpoint, two narrowly
# scoped fixes found by a further external review of the round-2 diff):
#   1. The initial `rm -f "$ack"` (clearing any pre-existing ack before
#      launch) was unchecked and its absence never independently
#      confirmed -- a failed removal could leave a STALE ack file
#      present, which the child's own wait loop would observe
#      immediately, exec'ing the real command before the parent had ever
#      validated anything. Fixed: both the removal's own exit status and
#      the file's actual absence are checked; failure aborts BEFORE any
#      child is ever launched.
#   2. The wrapper's own `own_pgid` lookup was used directly as
#      `-n "$own_pgid" && candidate==own_pgid` -- an EMPTY or malformed
#      lookup (the `ps` command itself failing, or its output being
#      unparseable) made the `-n` guard simply skip the whole safety
#      check, silently treating an UNVERIFIED own-group as "not a
#      match" rather than aborting. Fixed: a successful lookup with a
#      valid, POSITIVE numeric result is now REQUIRED before
#      `CHILD_PGID_CANDIDATE` is ever accepted or an ack is ever
#      published; an empty, malformed, or failed lookup aborts startup
#      via a PID-scoped (never group-scoped) termination.
#
# Runs exactly ONE condition (A or B) of the previously-designed R0057 gain
# A/B experiment (docs/reports/R0056_..., R0057_..., R0059_...; procedure
# finalized in R0060, corrected in R0061, R0062, and R0063,
# docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md).
# One probe invocation per condition, combining a fresh 60s warm-up with 3
# measured silent trials (never a reuse of R0059's own separate, warmup-only
# run).
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
#           archived (including a validated JSON<->WAV mapping), the probe's
#           own process group confirmed terminated/empty, final hardware
#           state verified, rollback verified. This is NOT the same as
#           SCIENTIFIC validity -- see the procedure document's own S5.
#   2    -- usage/argument error (no hardware touched).
#   90   -- precheck failed (mismatched, unreadable, or misidentified mixer
#           state, or a switch not [on]) -- aborted BEFORE any mutation; NO
#           hardware write is attempted (see Correction 1's R0062-era text,
#           unchanged in substance).
#   92   -- the condition's own mixer write or its post-write readback
#           verification failed (a possibly-partial write) -- restoration
#           is still attempted.
#   93   -- the probe exited 0, but this run's own archived evidence is not
#           EXACTLY the expected 1 JSON (3 trials) + 6 WAV files with a
#           validated JSON<->WAV mapping (R0063 Correction 5).
#   91   -- rollback did not verify, OR the final UACDemoV10 check did not
#           verify -- overrides an otherwise-successful 0.
#   94   -- another wrapper invocation already holds the concurrency lock
#           for this capture root -- nothing touched.
#   95   -- required evidence-storage directories, the run marker, or the
#           persisted-log pipeline could not be established -- nothing
#           touched (R0063 Correction 3 folds log-readiness into this same
#           code).
#   96   -- the probe's own process group could not be CONFIRMED terminated
#           after an otherwise-successful run (or after an interruption) --
#           a writer may still be alive; overrides an otherwise-successful
#           0 (R0063 Correction 2). The concurrency lock is QUARANTINED
#           (retained, not released) whenever this happens (round 2).
#   97   -- the probe's own launch could not be verified as running under an
#           ownership-confirmed, ACKNOWLEDGED, isolated process group
#           within the bounded startup window (marker publication failure,
#           handshake timeout, or a candidate matching the wrapper's own
#           group) -- the child/candidate group is terminated by the most
#           specific means known at that point (round 2); rollback is
#           still attempted.
#   130/143 -- interrupted by SIGINT/SIGTERM respectively; termination and
#           rollback are still attempted.
#   <probe's own code> -- otherwise, e.g. 1 (Python exception), 124
#           (external `timeout` fired) -- preserved as-is.
#
# TEST-ONLY environment overrides (never needed, and never set, for a real
# operator run -- see tests/test_run_r0057_gain_ab_condition_sh.py):
#   R0057_AB_CAPTURE_ROOT       -- replaces the real self_echo_captures/ dir.
#   R0057_AB_PROBE_LAUNCHER     -- replaces the whole python/probe command
#                                  line with a single executable (a fake).
#   R0057_AB_TIMEOUT_BOUND_S, R0057_AB_TIMEOUT_KILL_AFTER_S -- replace the
#                                  240s/15s external supervisor bounds.
#   R0057_AB_TEST_HANDSHAKE_DELAY_S -- (R0063) delays the launched child's
#                                  own PGID-marker write by this many
#                                  seconds, giving a test a reliable window
#                                  to signal the wrapper BEFORE the marker
#                                  is ever published.
#   R0057_AB_TEST_DELAY_BEFORE_ACK_S -- (R0063 round 2) delays the PARENT's
#                                  own publication of the handshake ack by
#                                  this many seconds, giving a test a
#                                  reliable window to signal the wrapper
#                                  AFTER the child's identity is known
#                                  (CHILD_PGID_CANDIDATE) but BEFORE it is
#                                  acknowledged.
#   R0057_AB_TEST_ACK_WAIT_S    -- (R0063 round 2) overrides the child's own
#                                  bounded wait-for-ack timeout (default
#                                  10s) -- shortened by a test so a
#                                  never-acknowledged child exits promptly.
#   R0057_AB_TEST_FORCE_CLEANUP_FAILURE -- (R0063) forces
#                                  `terminate_process_group` to report
#                                  failure deterministically, without
#                                  needing a genuinely unkillable process.
#   R0057_AB_TEST_FORCE_ACK_REMOVAL_FAILURE -- (R0063 round 3) forces the
#                                  startup handshake's own ack-absence
#                                  check to fail deterministically, before
#                                  any child is ever launched.
#   R0057_AB_TEST_FORCE_OWN_PGID_LOOKUP_FAILURE -- (R0063 round 3) forces
#                                  the wrapper's own pgid lookup to be
#                                  treated as empty/failed, so the
#                                  candidate-safety check aborts
#                                  deterministically without needing a
#                                  real `ps` failure.
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
EXPECTED_WAV_COUNT=$((EXPECTED_TRIALS * 2))

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
MUTATION_ATTEMPTED=0       # Correction 1 (R0062): only 1 once the condition sset is issued.
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
CHILD_PGID_CANDIDATE=""    # R0063 round 2: known-safe (excludes wrapper's own group) but not yet acknowledged.
CHILD_PGID_VERIFIED=0      # R0063 Correction 1: only 1 after the startup handshake proves ownership.
CLEANUP_STATUS=0           # R0063 Correction 2: optimistic (nothing launched = nothing to clean up); 1 only on an actually-failed, actually-attempted termination.
LOCK_QUARANTINED=0         # R0063 round 2: set when cleanup is unverified -- the lock is deliberately NOT released.
INTERRUPTED=0
ARCHIVE_JSON_COUNT=0
ARCHIVE_JSON_TRIAL_COUNT=0
ARCHIVE_WAV_COUNT=0
ARCHIVE_COMPLETE=0
ARCHIVE_OK=1
MAPPING_VALID=0            # R0063 Correction 5.
OWNED_WAV_ORIGINALS=""     # R0063 Correction 5: newline-separated, this-run-owned WAV original paths.
MARKER=""

# ---- Deterministic startup ownership handshake, TWO-WAY (R0063, round 2
# hardening) ---------------------------------------------------------------
# The parent NEVER samples the child's pgid speculatively. The child writes
# its own `$$` (which, immediately after it has called setsid(), IS both
# its new pid and its new pgid) to a MARKER file strictly AFTER setsid()
# has completed and strictly BEFORE it execs the real command -- there is
# no window in which the parent could observe a STALE value, because the
# parent never samples at all; it only reads what the child itself already
# confirmed, bounded and polled.
#
# Round 1 (first R0063 draft) stopped there: the child published its
# identity and immediately exec'd the real command, with NO gate. External
# review correctly found this one-way -- the probe (and anything IT spawns)
# could already exist before the PARENT ever finished validating and set
# CHILD_PGID_VERIFIED=1; an interruption in that exact window terminated
# only CHILD_PID, which by then might already have been replaced (via
# exec) by `timeout`, itself possibly already having forked the probe as a
# SEPARATE pid sharing the same pgid -- `kill` on the single old PID is not
# guaranteed to reach that descendant.
#
# Fixed here with an explicit ACKNOWLEDGEMENT the child must wait for
# BEFORE it is allowed to exec anything beyond itself: publish marker ->
# wait (bounded) for an ACK file the PARENT creates only after it has
# fully validated the candidate -> exec the real command ONLY once the ACK
# is observed. If the child's own bounded ack-wait times out (parent never
# acknowledged, e.g. because validation failed or the parent itself died),
# the child exits WITHOUT ever exec'ing the real command -- by
# CONSTRUCTION, no descendant of the probe can ever exist unless ownership
# was already fully verified. This eliminates the race rather than merely
# narrowing its window.
#
# The window BETWEEN the child publishing its marker and the parent
# publishing the ack is not unowned either: as soon as the parent reads a
# candidate pgid that is confirmed NUMERIC and DISTINCT from the wrapper's
# own pgid, it records it in CHILD_PGID_CANDIDATE (global) -- from that
# point on it is safe to terminate the WHOLE candidate group (the group
# structurally contains nothing but the still-parked child at this point,
# since the child cannot have exec'd anything else yet), and on_signal()
# uses exactly this to handle an interruption arriving in that specific
# window, not merely a PID-scoped kill.
launch_probe_with_verified_ownership() {
  local marker ack own_pgid candidate waited ack_wait_s

  if ! marker="$(mktemp)"; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: could not create the handshake marker file" >&2
    return 1
  fi
  if ! ack="$(mktemp)"; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: could not create the handshake ack file" >&2
    rm -f "$marker"
    return 1
  fi
  # The ACK's own existence (not its content) is the signal -- remove it
  # immediately so the child's own wait loop only sees it appear once the
  # parent deliberately re-creates it below. R0063 round 3: checked, and
  # its ABSENCE independently confirmed, BEFORE any child is ever
  # launched -- an unchecked or failed removal could leave a STALE ack
  # file already present, which the child's own wait loop would then
  # observe immediately, exec'ing the real command before the parent has
  # ever actually validated anything.
  if [[ -n "${R0057_AB_TEST_FORCE_ACK_REMOVAL_FAILURE:-}" ]] || ! rm -f "$ack" || [[ -e "$ack" ]]; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: could not confirm the handshake ack file is absent before launch -- aborting before any child is started" >&2
    rm -f "$marker"
    return 1
  fi

  ack_wait_s="${R0057_AB_TEST_ACK_WAIT_S:-10}"

  # shellcheck disable=SC2016
  setsid bash -c '
    marker_path="$1"; ack_path="$2"; ack_wait_s="$3"; shift 3
    delay="${R0057_AB_TEST_HANDSHAKE_DELAY_S:-0}"
    if [[ "$delay" != "0" ]]; then sleep "$delay"; fi
    if ! echo "$$" > "$marker_path"; then
      exit 97
    fi
    waited=0
    max_polls=$(( ${ack_wait_s%.*} * 10 ))
    while [[ ! -e "$ack_path" ]] && [[ "$waited" -lt "$max_polls" ]]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    if [[ ! -e "$ack_path" ]]; then
      # No acknowledgement within the bounded window -- never exec the
      # real command. No descendant of the probe can exist from this
      # path.
      exit 98
    fi
    exec "$@"
  ' _ "$marker" "$ack" "$ack_wait_s" "${LAUNCH_ARGS[@]}" &
  CHILD_PID=$!
  echo "LAUNCHED_CHILD_PID=${CHILD_PID}"

  waited=0
  while [[ ! -s "$marker" ]] && [[ "$waited" -lt 100 ]]; do
    if ! kill -0 "$CHILD_PID" 2>/dev/null; then
      break
    fi
    sleep 0.1
    waited=$((waited + 1))
  done

  if [[ ! -s "$marker" ]]; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: no PGID marker observed within the bounded startup window (marker publication may have failed -- see the child's own exit status)" >&2
    if terminate_by_pid "$CHILD_PID" 5; then CLEANUP_STATUS=0; else CLEANUP_STATUS=1; fi
    rm -f "$marker" "$ack"
    return 1
  fi

  candidate="$(tr -d ' \n' < "$marker" 2>/dev/null)"
  rm -f "$marker"

  if [[ ! "$candidate" =~ ^[0-9]+$ ]]; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: unreadable/non-numeric PGID (${candidate:-empty})" >&2
    if terminate_by_pid "$CHILD_PID" 5; then CLEANUP_STATUS=0; else CLEANUP_STATUS=1; fi
    rm -f "$ack"
    return 1
  fi

  # R0063 round 3: a successful lookup with a valid, POSITIVE numeric
  # result is now REQUIRED before CHILD_PGID_CANDIDATE is ever accepted
  # or an ack is ever published -- an empty, malformed, or failed lookup
  # means we cannot prove the candidate is distinct from the wrapper's
  # own group, so it must never be treated as safe to signal (never
  # group-kill, and never PID-kill something we'd otherwise call
  # "verified"; PID-scoped termination remains safe regardless, since it
  # targets a specific PID, never a group).
  if [[ -n "${R0057_AB_TEST_FORCE_OWN_PGID_LOOKUP_FAILURE:-}" ]]; then
    own_pgid=""
  else
    own_pgid="$(ps -o pgid= -p "$$" 2>/dev/null | tr -d ' ')" || own_pgid=""
  fi
  if [[ ! "$own_pgid" =~ ^[1-9][0-9]*$ ]]; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: could not determine the wrapper's own process group (lookup failed, empty, or malformed) -- refusing to accept any candidate without this safety check" >&2
    if terminate_by_pid "$CHILD_PID" 5; then CLEANUP_STATUS=0; else CLEANUP_STATUS=1; fi
    rm -f "$ack"
    return 1
  fi
  if [[ "$candidate" == "$own_pgid" ]]; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: candidate PGID (${candidate}) matches the wrapper's OWN process group -- refusing to treat it as an owned, isolated group, and refusing to ever acknowledge it (the child will time out its own ack-wait and exit without exec'ing anything)" >&2
    # Deliberately PID-scoped, never group-scoped: the candidate here IS
    # the wrapper's own group, so a group-kill would self-harm.
    if terminate_by_pid "$CHILD_PID" 5; then CLEANUP_STATUS=0; else CLEANUP_STATUS=1; fi
    rm -f "$ack"
    return 1
  fi

  # Candidate is numeric and confirmed distinct from the wrapper's own
  # group -- from here on it is safe to group-kill it (the group can only
  # contain the still-parked child; nothing else can exist yet).
  CHILD_PGID_CANDIDATE="$candidate"
  echo "CHILD_PGID_CANDIDATE=${candidate} (published, not yet acknowledged, excludes wrapper's own group ${own_pgid:-unknown})"

  if [[ -n "${R0057_AB_TEST_DELAY_BEFORE_ACK_S:-}" ]]; then
    # TEST-ONLY (see script header): widens the "candidate known, not yet
    # acknowledged" window to a size a test can reliably signal into.
    sleep "${R0057_AB_TEST_DELAY_BEFORE_ACK_S}"
  fi

  if [[ "$INTERRUPTED" -eq 1 ]]; then
    # on_signal() already ran (and already terminated CHILD_PGID_CANDIDATE)
    # while this function was asleep above -- never publish an ack for an
    # already-abandoned candidate.
    return 1
  fi

  if ! touch "$ack" 2>/dev/null; then
    echo "STARTUP_OWNERSHIP_HANDSHAKE_FAILED: could not publish the parent's own acknowledgement -- terminating the not-yet-launched candidate group" >&2
    if terminate_process_group "$candidate" 5; then CLEANUP_STATUS=0; else CLEANUP_STATUS=1; fi
    return 1
  fi

  CHILD_PGID="$candidate"
  CHILD_PGID_VERIFIED=1
  echo "CHILD_PID=${CHILD_PID} CHILD_PGID=${CHILD_PGID} (verified via startup handshake, acknowledged, excludes wrapper's own group ${own_pgid:-unknown})"
  return 0
}

# PID-scoped termination used ONLY before group ownership is verified (or
# when it never becomes verified) -- signals exactly one specific PID,
# never a process group, so it can never reach an unrelated process even if
# that PID still happens to share the wrapper's own group at this instant.
terminate_by_pid() {
  local pid="$1" grace_s="${2:-5}" waited max_polls
  if [[ -z "$pid" ]]; then
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill -TERM "$pid" 2>/dev/null || true
  waited=0; max_polls=$((grace_s * 10))
  while kill -0 "$pid" 2>/dev/null && [[ "$waited" -lt "$max_polls" ]]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
    waited=0
    while kill -0 "$pid" 2>/dev/null && [[ "$waited" -lt 30 ]]; do
      sleep 0.1
      waited=$((waited + 1))
    done
  fi
  # R0063 round 2: no unconditional `wait "$pid"` here. A process that
  # survives SIGKILL (e.g. stuck in uninterruptible D-state on blocked
  # I/O) would make an unconditional `wait` block INDEFINITELY, defeating
  # this function's own documented bound and preventing rollback from
  # ever being reached. Only reap (a near-instant, non-blocking-in-
  # practice call) if the bounded polling above already confirmed the
  # process is gone; otherwise report failure and return without waiting.
  if kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  wait "$pid" 2>/dev/null
  return 0
}

# ---- R0063 Correction 2: liveness inspection that distinguishes "no
# matches" (confirmed empty) from an inspection ERROR (pgrep itself failed)
# -- an inspection failure is NEVER treated as verified emptiness anywhere
# downstream. Returns via exit status: 0 = confirmed alive, 1 = confirmed
# empty, 2 = inspection error. --------------------------------------------#
pgid_liveness() {
  local pgid="$1" rc
  pgrep -g "$pgid" >/dev/null 2>&1
  rc=$?
  case "$rc" in
    0) return 0 ;;
    1) return 1 ;;
    *) return 2 ;;
  esac
}

# ---- Bounded, verified whole-process-group termination (R0062's own
# mechanism; R0063 adds inspection-error handling and the TEST-ONLY forced-
# failure hook). Sends `sig` to the ENTIRE process group `pgid` (never a
# bare PID). Escalates from TERM to KILL after `grace_s` if needed, and
# never returns success unless `pgid_liveness` has POSITIVELY confirmed
# empty -- an inspection error at the final check is reported as failure,
# exactly like a genuinely still-alive group, never as success. --------- #
terminate_process_group() {
  local pgid="$1" grace_s="${2:-5}" rc waited max_polls
  if [[ -z "$pgid" ]]; then
    return 0
  fi
  if [[ -n "${R0057_AB_TEST_FORCE_CLEANUP_FAILURE:-}" ]]; then
    # TEST-ONLY (see script header) -- deterministically exercises the
    # "cleanup did not verify" outcome without needing to fabricate a
    # genuinely unkillable real process.
    echo "PROCESS_GROUP_TERMINATION_FAILED pgid=${pgid}: forced by R0057_AB_TEST_FORCE_CLEANUP_FAILURE (test-only)" >&2
    return 1
  fi

  pgid_liveness "$pgid"; rc=$?
  if [[ "$rc" -eq 1 ]]; then
    return 0
  fi
  if [[ "$rc" -eq 2 ]]; then
    echo "PROCESS_GROUP_INSPECTION_FAILED pgid=${pgid}: pgrep itself failed -- cannot verify liveness before signaling; signaling defensively anyway" >&2
  fi

  kill -TERM -- "-${pgid}" 2>/dev/null || true
  waited=0; max_polls=$((grace_s * 10))
  while [[ "$waited" -lt "$max_polls" ]]; do
    pgid_liveness "$pgid"; rc=$?
    [[ "$rc" -eq 1 ]] && break
    sleep 0.1
    waited=$((waited + 1))
  done

  pgid_liveness "$pgid"; rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo "PROCESS_GROUP_STILL_ALIVE_AFTER_TERM pgid=${pgid} -- escalating to SIGKILL"
    kill -KILL -- "-${pgid}" 2>/dev/null || true
    waited=0
    while [[ "$waited" -lt 30 ]]; do
      pgid_liveness "$pgid"; rc=$?
      [[ "$rc" -eq 1 ]] && break
      sleep 0.1
      waited=$((waited + 1))
    done
    pgid_liveness "$pgid"; rc=$?
  fi

  if [[ "$rc" -eq 1 ]]; then
    return 0
  fi
  if [[ "$rc" -eq 2 ]]; then
    echo "PROCESS_GROUP_TERMINATION_UNVERIFIED pgid=${pgid}: inspection error -- emptiness could not be confirmed (never reported as clean)" >&2
  else
    echo "PROCESS_GROUP_TERMINATION_FAILED pgid=${pgid}: process(es) still present" >&2
  fi
  return 1
}

# ---- Parsing helpers -------------------------------------------------- #

# Extracts each "Playback N [" raw integer, one per line, in the order it
# appears in `amixer sget` output. Never lets a zero-match grep trip
# errexit-style callers -- always exits 0; callers check emptiness
# themselves.
extract_playback_raws() {
  grep -oE 'Playback [0-9]+ \[' 2>/dev/null | grep -oE '[0-9]+' || true
}

# R0063 Correction 4: EXACT, whole-line matching (never a substring) for
# control identity and Limits -- a substring check like *"'PCM',1"* also
# matched 'PCM',10/'PCM',11/...; *"Limits: Playback 0 - 60"* also matched
# "...0 - 600"/"...0 - 160". The switch check now requires the EXACT
# expected count of "[on]" markers (never just "at least one somewhere") AND
# zero "[off]" markers anywhere -- a genuinely MIXED state (one channel
# [off], the other [on]) is now rejected instead of passing on the strength
# of the ONE [on] channel alone.
read_array_raw() {
  local output raws n off_count on_count
  if ! output="$(amixer -c Array sget 'PCM',1 2>&1)"; then
    echo "ARRAY_READ_FAILED: $output" >&2
    return 1
  fi
  if ! printf '%s\n' "$output" | grep -qxF "Simple mixer control 'PCM',1"; then
    echo "ARRAY_READ_UNEXPECTED_CONTROL_IDENTITY: $output" >&2
    return 1
  fi
  if ! printf '%s\n' "$output" | grep -qxF "  Limits: Playback 0 - 60"; then
    echo "ARRAY_READ_UNEXPECTED_LIMITS: $output" >&2
    return 1
  fi
  off_count="$(printf '%s\n' "$output" | grep -oE '\[off\]' | grep -c . || true)"
  on_count="$(printf '%s\n' "$output" | grep -oE '\[on\]' | grep -c . || true)"
  if [[ "$off_count" -ne 0 || "$on_count" -ne 1 ]]; then
    echo "ARRAY_READ_SWITCH_NOT_ON: $output (off_count=${off_count} on_count=${on_count}, expected exactly 1 channel, all on)" >&2
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

read_uac_raws() {
  local output raws n off_count on_count
  if ! output="$(amixer -c UACDemoV10 sget PCM 2>&1)"; then
    echo "UAC_READ_FAILED: $output" >&2
    return 1
  fi
  if ! printf '%s\n' "$output" | grep -qxF "Simple mixer control 'PCM',0"; then
    echo "UAC_READ_UNEXPECTED_CONTROL_IDENTITY: $output" >&2
    return 1
  fi
  if ! printf '%s\n' "$output" | grep -qxF "  Limits: Playback 0 - 147"; then
    echo "UAC_READ_UNEXPECTED_LIMITS: $output" >&2
    return 1
  fi
  off_count="$(printf '%s\n' "$output" | grep -oE '\[off\]' | grep -c . || true)"
  on_count="$(printf '%s\n' "$output" | grep -oE '\[on\]' | grep -c . || true)"
  if [[ "$off_count" -ne 0 || "$on_count" -ne 2 ]]; then
    echo "UAC_READ_SWITCH_NOT_ON: $output (off_count=${off_count} on_count=${on_count}, expected exactly 2 channels, BOTH on)" >&2
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

# ---- Rollback: skips the hardware write entirely when no mutation was
# ever attempted; independently validates BOTH the restored Array value AND
# the still-unwritten UACDemoV10 state. R0063 Correction 4: the Array
# readback is now ALWAYS attempted, even when the restore write itself
# reports failure -- the outcome is derived strictly from what is OBSERVED,
# never assumed from the write command's own self-reported exit status. -- #
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
    local set_ok=1 read_raw="" read_ok=0
    if amixer -c Array sset 'PCM',1 "${BASELINE_RAW}" >/dev/null 2>&1; then
      set_ok=0
    else
      echo "ROLLBACK_WRITE_FAILED: amixer sset itself reported failure -- the observed final state is still checked independently below, never assumed from the write's own exit status" >&2
    fi
    if read_raw="$(read_array_raw)"; then
      read_ok=1
    fi
    # R0063 round 2: a failed restoration COMMAND must never be converted
    # into an "operational success" merely because the readback happens
    # to observe the baseline value anyway (e.g. because the value was
    # already at baseline before the failed write was even attempted).
    # "The write succeeded" and "the baseline is observed" are reported as
    # two SEPARATE facts (ROLLBACK_WRITE_STATUS / ROLLBACK_OBSERVED_RAW,
    # both logged unconditionally below); ROLLBACK_STATUS=0 ("clean") is
    # only ever set when BOTH are true at once.
    if [[ "$set_ok" -eq 0 && "$read_ok" -eq 1 && "$read_raw" == "${BASELINE_RAW}" ]]; then
      ROLLBACK_STATUS=0
      ROLLBACK_OUTCOME_LABEL="clean"
    elif [[ "$set_ok" -ne 0 ]]; then
      ROLLBACK_STATUS=1
      if [[ "$read_ok" -eq 1 && "$read_raw" == "${BASELINE_RAW}" ]]; then
        ROLLBACK_OUTCOME_LABEL="write_failed_baseline_observed"
      elif [[ "$read_ok" -eq 1 ]]; then
        ROLLBACK_OUTCOME_LABEL="write_failed_readback_mismatch"
      else
        ROLLBACK_OUTCOME_LABEL="write_failed_readback_unreadable"
      fi
    elif [[ "$read_ok" -eq 1 ]]; then
      ROLLBACK_STATUS=1
      ROLLBACK_OUTCOME_LABEL="readback_mismatch"
    else
      ROLLBACK_STATUS=1
      ROLLBACK_OUTCOME_LABEL="readback_unreadable"
    fi
    echo "ROLLBACK_WRITE_STATUS=$( [[ "$set_ok" -eq 0 ]] && echo ok || echo failed ) ROLLBACK_OBSERVED_RAW=${read_raw:-unknown}"
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

# ---- Explicit INT/TERM handling. Terminates whatever is actually owned,
# covering all THREE possible startup windows (R0063 round 2): the fully
# verified+acknowledged process group; a candidate pgid that is already
# known and confirmed distinct from the wrapper's own group but not yet
# acknowledged (structurally still just the parked child -- safe to
# group-kill); or, before any marker has even been published, the bare
# CHILD_PID (never a group signal, since no pgid is known/trusted yet). No
# chaining into another condition. No unconditional `wait` when
# termination itself was not verified -- rollback must still be reached
# within the documented cleanup budget. ----------------------------------- #
on_signal() {
  local sig="$1"
  if [[ "$INTERRUPTED" -eq 1 ]]; then
    return
  fi
  INTERRUPTED=1
  RUN_STATUS_LABEL="interrupted_${sig}"
  echo "INTERRUPTED: received SIG${sig}"
  if [[ "$CHILD_PGID_VERIFIED" -eq 1 && -n "$CHILD_PGID" ]]; then
    echo "STOPPING wrapper-owned process group (pgid ${CHILD_PGID})"
    if terminate_process_group "$CHILD_PGID" 5; then
      CLEANUP_STATUS=0
      echo "PROCESS_GROUP_TERMINATED pgid=${CHILD_PGID}"
      wait "$CHILD_PID" 2>/dev/null
    else
      CLEANUP_STATUS=1
      echo "PROCESS_GROUP_TERMINATION_UNVERIFIED pgid=${CHILD_PGID} after interruption" >&2
    fi
  elif [[ -n "$CHILD_PGID_CANDIDATE" ]]; then
    echo "STOPPING not-yet-acknowledged owned process group (candidate pgid ${CHILD_PGID_CANDIDATE}) -- interrupted after the child published its identity but before the parent's own acknowledgement"
    if terminate_process_group "$CHILD_PGID_CANDIDATE" 5; then
      CLEANUP_STATUS=0
    else
      CLEANUP_STATUS=1
      echo "PROCESS_GROUP_TERMINATION_UNVERIFIED pgid=${CHILD_PGID_CANDIDATE} after interruption (pre-acknowledgement)" >&2
    fi
  elif [[ -n "$CHILD_PID" ]]; then
    echo "INTERRUPTED_DURING_STARTUP_HANDSHAKE: no candidate pgid known yet -- terminating CHILD_PID ${CHILD_PID} directly, never a group signal"
    if terminate_by_pid "$CHILD_PID" 5; then
      CLEANUP_STATUS=0
    else
      CLEANUP_STATUS=1
      echo "CHILD_PID ${CHILD_PID} termination unverified after interruption during the startup handshake" >&2
    fi
  fi
  case "$sig" in
    INT) exit 130 ;;
    TERM) exit 143 ;;
  esac
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

# ---- One archived copy of one source file: hash, copy, re-hash, compare,
# remove source only on a verified match. Every step individually checked
# (no reliance on a surrounding block's own aggregate exit status).
# R0063 round 2: `unlink_src` (default 1) lets the caller SKIP removing
# the source entirely -- used when process-group cleanup could not be
# verified, so a possibly-still-alive writer's own files are never
# unlinked out from under it; the removal's own exit status is now also
# checked (previously `rm -f` could fail silently and the function still
# unconditionally returned 0). ------------------------------------------- #
archive_one_file() {
  local src="$1" dest_dir="$2" unlink_src="${3:-1}" src_hash dst_hash dest
  dest="$dest_dir/$(basename "$src")"
  if ! src_hash="$(sha256sum "$src" 2>/dev/null | awk '{print $1}')" || [[ -z "$src_hash" ]]; then
    echo "ARCHIVE_HASH_FAILED: $src" >&2
    return 1
  fi
  if ! cp "$src" "$dest" 2>/dev/null; then
    echo "ARCHIVE_COPY_FAILED: $src" >&2
    return 1
  fi
  if ! dst_hash="$(sha256sum "$dest" 2>/dev/null | awk '{print $1}')" || [[ -z "$dst_hash" ]]; then
    echo "ARCHIVE_HASH_FAILED: $dest" >&2
    return 1
  fi
  if [[ "$src_hash" != "$dst_hash" ]]; then
    echo "ARCHIVE_HASH_MISMATCH: $src" >&2
    return 1
  fi
  if [[ "$unlink_src" -ne 1 ]]; then
    echo "ARCHIVE_SOURCE_PRESERVED (cleanup unverified -- not unlinked): $src"
    return 0
  fi
  if ! rm -f "$src"; then
    echo "ARCHIVE_SOURCE_REMOVAL_FAILED: $src" >&2
    return 1
  fi
  return 0
}

# Appends one line to $tmp_out and checks the write itself. Relies on
# bash's own dynamic scoping to see and update the CALLER's `tmp_out`/
# `manifest_ok` locals -- must only be called from inside
# write_manifest_and_validate_mapping's own call stack. R0063 round 2:
# replaces every previously-unchecked `echo ... >> "$tmp_out"` -- a
# successful final `mv` does not by itself prove every individual line
# was actually written.
_manifest_append() {
  if ! printf '%s\n' "$1" >> "$tmp_out"; then
    manifest_ok=0
    echo "MANIFEST_APPEND_FAILED: could not write a manifest line" >&2
    return 1
  fi
  return 0
}

# ---- Manifest writing AND JSON<->WAV mapping validation, as a dedicated
# function whose return status is derived from an explicit `manifest_ok`
# flag set on every individual failure -- never from a surrounding block's
# own aggregate exit status, and never from a hash failure hidden inside
# an echoed command substitution. Sets the GLOBAL ARCHIVE_JSON_TRIAL_COUNT
# and MAPPING_VALID.
#
# R0063 round 2 (mapping strictness, finding 3): the previous loop SKIPPED
# a trial row entirely when BOTH mic_wav and ref_wav were empty --with
# three trial objects, six real WAV files, and only two populated mapping
# rows, completeness could still be reported. Fixed: every one of
# EXPECTED_TRIALS rows is now required to be present and BOTH its own
# mic_wav and ref_wav populated; each populated path must match its own
# EXPECTED per-trial, per-role basename EXACTLY (`{level}_trial{i}_mic.wav`
# / `_ref.wav`) -- catching a swapped mic/ref role, not just a wrong file
# -- and must exactly identify one of THIS RUN's own newly-discovered,
# successfully-archived WAV originals (OWNED_WAV_ORIGINALS), never merely
# a file sharing a basename. Every reference must be unique (no
# duplicates). A malformed JSON structure (not an object, `trials` not a
# list, a trial entry not an object, `cross_correlation` not an object, a
# non-string path) is now an explicit PARSE ERROR propagated to the bash
# side, never silently coerced into empty/default values. -------------- #
write_manifest_and_validate_mapping() {
  local manifest_ok=1 tmp_out
  # R0063 round 2 (finding 4): created INSIDE ARCHIVE_DIR itself, not the
  # system tmp directory -- `mv` is only genuinely atomic when source and
  # destination share a filesystem; a cross-filesystem mktemp would make
  # the later `mv` fall back to a non-atomic copy+unlink while this
  # function's own comments still claimed atomic rename.
  if ! tmp_out="$(mktemp "$ARCHIVE_DIR/.manifest.XXXXXX" 2>/dev/null)"; then
    echo "MANIFEST_TMP_CREATE_FAILED" >&2
    return 1
  fi

  _manifest_append "R0057 gain A/B experiment -- archived artifacts"
  _manifest_append "condition: ${CONDITION}"
  _manifest_append "run_timestamp_utc: ${TS}"
  _manifest_append "All JSON and WAV files listed below came from this SAME single probe invocation."
  _manifest_append ""
  _manifest_append "JSON:"

  local f h
  for f in "$ARCHIVE_DIR"/self_echo_probe_*.json; do
    [[ -e "$f" ]] || continue
    if h="$(sha256sum "$f" 2>/dev/null | awk '{print $1}')" && [[ -n "$h" ]]; then
      _manifest_append "  $(basename "$f")  sha256=${h}"
    else
      echo "MANIFEST_HASH_FAILED: $f" >&2
      manifest_ok=0
    fi
  done

  _manifest_append ""
  _manifest_append "PCM (WAV):"
  for f in "$ARCHIVE_DIR"/pcm/*.wav; do
    [[ -e "$f" ]] || continue
    if h="$(sha256sum "$f" 2>/dev/null | awk '{print $1}')" && [[ -n "$h" ]]; then
      _manifest_append "  pcm/$(basename "$f")  sha256=${h}"
    else
      echo "MANIFEST_HASH_FAILED: $f" >&2
      manifest_ok=0
    fi
  done

  _manifest_append ""
  _manifest_append "ORIGINAL JSON WAV PATH -> ARCHIVED FILE (the JSON itself is never rewritten):"

  local -a jsons=("$ARCHIVE_DIR"/self_echo_probe_*.json)
  local status_line="" status="" trial_count=0 body="" mapping_ok=1
  if [[ -e "${jsons[0]:-}" && "${#jsons[@]}" -eq 1 ]]; then
    local parsed
    parsed="$(python3 -c "
import json, sys

def fail(msg):
    print('ERROR', msg, sep='\t')
    sys.exit(0)

try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    fail(f'json parse error: {e}')
if not isinstance(d, dict):
    fail('top-level JSON is not an object')
trials = d.get('trials')
if not isinstance(trials, list):
    fail(\"'trials' is not a list\")
rows = []
for t in trials:
    if not isinstance(t, dict):
        fail('a trial entry is not an object')
    c = t.get('cross_correlation')
    if not isinstance(c, dict):
        fail(\"a trial's cross_correlation is not an object\")
    mic = c.get('mic_wav')
    ref = c.get('ref_wav')
    if mic is not None and not isinstance(mic, str):
        fail('mic_wav is not a string')
    if ref is not None and not isinstance(ref, str):
        fail('ref_wav is not a string')
    rows.append((mic or '', ref or ''))
print('OK', len(rows), sep='\t')
for mic, ref in rows:
    print(mic, ref, sep='\t')
" "${jsons[0]}" 2>/dev/null || true)"
    status_line="$(printf '%s\n' "$parsed" | head -1)"
    status="$(printf '%s' "$status_line" | cut -f1)"
    if [[ "$status" == "OK" ]]; then
      trial_count="$(printf '%s' "$status_line" | cut -f2)"
      [[ "$trial_count" =~ ^[0-9]+$ ]] || trial_count=0
      body="$(printf '%s\n' "$parsed" | tail -n +2)"
    else
      trial_count=0
      echo "MANIFEST_JSON_PARSE_ERROR: ${status_line#ERROR$'\t'}" >&2
      mapping_ok=0
    fi
  else
    echo "MANIFEST_JSON_PARSE_ERROR: expected exactly 1 results JSON, found ${#jsons[@]}" >&2
    mapping_ok=0
  fi
  ARCHIVE_JSON_TRIAL_COUNT="$trial_count"

  local -a referenced=()
  local trial_index=0
  if [[ "$trial_count" -gt 0 && -n "$body" ]]; then
    local mic ref orig exp name si base archived_path h2
    while IFS=$'\t' read -r mic ref; do
      trial_index=$((trial_index + 1))
      # R0063 round 2: NO skip-if-both-empty here -- an empty trial is a
      # structural defect to reject, not something to silently ignore.
      local -a slot_paths=("$mic" "$ref")
      local -a slot_expected=(
        "${EXPECTED_LEVEL}_trial${trial_index}_mic.wav"
        "${EXPECTED_LEVEL}_trial${trial_index}_ref.wav"
      )
      local -a slot_names=("mic_wav" "ref_wav")
      for si in 0 1; do
        orig="${slot_paths[$si]}"
        exp="${slot_expected[$si]}"
        name="${slot_names[$si]}"
        if [[ -z "$orig" ]]; then
          mapping_ok=0
          _manifest_append "  (trial ${trial_index} missing ${name}) -> INVALID"
          echo "MANIFEST_MAPPING_FIELD_MISSING: trial ${trial_index} ${name}" >&2
          continue
        fi
        referenced+=("$orig")
        if [[ "$(basename "$orig")" != "$exp" ]]; then
          mapping_ok=0
          _manifest_append "  ${orig} -> ROLE_MISMATCH (trial ${trial_index} ${name}, expected basename ${exp})"
          echo "MANIFEST_MAPPING_ROLE_MISMATCH: trial ${trial_index} ${name} basename $(basename "$orig") != ${exp}" >&2
          continue
        fi
        if ! grep -qxF "$orig" <<< "$OWNED_WAV_ORIGINALS"; then
          mapping_ok=0
          _manifest_append "  ${orig} -> REJECTED (not one of this run's own newly-archived files)"
          echo "MANIFEST_MAPPING_REJECTED: ${orig} -> REJECTED (not one of this run's own newly-archived files)" >&2
          continue
        fi
        base="$(basename "$orig")"
        archived_path="$ARCHIVE_DIR/pcm/${base}"
        if [[ -e "$archived_path" ]] && h2="$(sha256sum "$archived_path" 2>/dev/null | awk '{print $1}')" && [[ -n "$h2" ]]; then
          _manifest_append "  ${orig} -> pcm/${base}  sha256=${h2}"
        else
          mapping_ok=0
          _manifest_append "  ${orig} -> MISSING (never archived)"
          echo "MANIFEST_MAPPING_MISSING: ${orig} -> MISSING (never archived)" >&2
        fi
      done
    done <<< "$body"
  else
    _manifest_append "  (no JSON-embedded WAV paths found)"
    mapping_ok=0
  fi

  if [[ "$trial_index" -ne "$EXPECTED_TRIALS" ]]; then
    mapping_ok=0
    echo "MANIFEST_TRIAL_COUNT_MISMATCH: parsed ${trial_index} trial row(s), expected ${EXPECTED_TRIALS}" >&2
  fi

  if [[ "${#referenced[@]}" -gt 0 ]]; then
    local dup_count
    dup_count="$(printf '%s\n' "${referenced[@]}" | sort | uniq -d | grep -c . || true)"
    if [[ "$dup_count" -ne 0 ]]; then
      mapping_ok=0
      echo "MANIFEST_DUPLICATE_WAV_REFERENCE: ${dup_count} path(s) referenced more than once" >&2
    fi
  fi

  MAPPING_VALID=0
  [[ "$mapping_ok" -eq 1 ]] && MAPPING_VALID=1

  if ! mv "$tmp_out" "$ARCHIVE_DIR/MANIFEST.txt" 2>/dev/null; then
    echo "MANIFEST_WRITE_FAILED: $ARCHIVE_DIR/MANIFEST.txt" >&2
    rm -f "$tmp_out"
    return 1
  fi
  [[ "$manifest_ok" -eq 1 ]]
}

# ---- Always-attempted, best-effort archive of THIS run's own evidence,
# called from finalize() AFTER rollback so it never delays hardware
# restoration. R0063 Correction 5: completeness now also requires
# ARCHIVE_WAV_COUNT to be EXACTLY the expected count and a validated
# JSON<->WAV mapping (MAPPING_VALID), not just filename presence. ------- #
archive_current_run_artifacts() {
  if [[ -z "$MARKER" || ! -e "$MARKER" ]]; then
    # Nothing was ever set up to measure "new since" against (e.g. a very
    # early abort) -- there is nothing of this run's own to archive.
    return
  fi
  if ! mkdir -p "$ARCHIVE_DIR/pcm" 2>/dev/null; then
    echo "ARCHIVE_DIR_CREATE_FAILED: $ARCHIVE_DIR" >&2
    ARCHIVE_OK=0
    return
  fi

  local new_json new_wav f unlink
  new_json="$(find "$JSON_DIR" -maxdepth 1 -type f -name 'self_echo_probe_*.json' -newer "$MARKER" 2>/dev/null || true)"
  new_wav="$(find "$PCM_DIR" -maxdepth 1 -type f -name '*.wav' -newer "$MARKER" 2>/dev/null || true)"

  # R0063 round 2 (finding 2): if process-group cleanup could not be
  # verified, a writer may still be alive and may still be USING these
  # shared-location files -- preserve the evidence (still copy+hash) but
  # do NOT unlink the originals out from under a possibly-still-running
  # process.
  unlink=1
  [[ "$CLEANUP_STATUS" -ne 0 ]] && unlink=0

  OWNED_WAV_ORIGINALS=""

  if [[ -n "$new_json" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      ARCHIVE_JSON_COUNT=$((ARCHIVE_JSON_COUNT + 1))
      archive_one_file "$f" "$ARCHIVE_DIR" "$unlink" || ARCHIVE_OK=0
    done <<< "$new_json"
  fi

  if [[ -n "$new_wav" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      ARCHIVE_WAV_COUNT=$((ARCHIVE_WAV_COUNT + 1))
      if archive_one_file "$f" "$ARCHIVE_DIR/pcm" "$unlink"; then
        OWNED_WAV_ORIGINALS="${OWNED_WAV_ORIGINALS}${f}"$'\n'
      else
        ARCHIVE_OK=0
      fi
    done <<< "$new_wav"
  fi

  write_manifest_and_validate_mapping || ARCHIVE_OK=0

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
        && "$ARCHIVE_WAV_COUNT" -eq "$EXPECTED_WAV_COUNT" \
        && "$all_present" -eq 1 && "$MAPPING_VALID" -eq 1 && "$ARCHIVE_OK" -eq 1 ]]; then
    ARCHIVE_COMPLETE=1
  fi
  echo "ARCHIVE_DIR=${ARCHIVE_DIR}"
  echo "ARCHIVE_JSON_COUNT=${ARCHIVE_JSON_COUNT} ARCHIVE_JSON_TRIAL_COUNT=${ARCHIVE_JSON_TRIAL_COUNT} ARCHIVE_WAV_COUNT=${ARCHIVE_WAV_COUNT} ARCHIVE_MAPPING_VALID=${MAPPING_VALID} ARCHIVE_COMPLETE=${ARCHIVE_COMPLETE}"
}

# ---- finalize(): the ONE point that always runs on exit --------------- #
# NOTE (limitation, stated per instruction, not hidden): a SIGKILL sent to
# THIS wrapper process, or a host power loss, cannot be intercepted by any
# shell trap -- neither this EXIT trap nor the INT/TERM handlers above can
# run in that case, and rollback would NOT happen. An operator who kills -9
# this script, or who loses power mid-run, must manually verify and, if
# needed, restore Array 'PCM',1 to raw 40 afterward.
finalize() {
  local rc=$?
  if [[ "$FINALIZED" -eq 1 ]]; then
    return
  fi
  FINALIZED=1

  rollback
  archive_current_run_artifacts

  # R0063 round 2 (finding 2): if cleanup could not be verified, the lock
  # is deliberately RETAINED (quarantined), not released -- a possibly-
  # still-alive writer means the shared capture paths are not safe for a
  # second condition to start against. This is NOT auto-cleared by any
  # later run; an operator must manually verify and remove the lock
  # directory once satisfied nothing is still writing.
  if [[ "$LOCK_ACQUIRED" -eq 1 ]]; then
    if [[ "$CLEANUP_STATUS" -eq 0 ]]; then
      rmdir "$LOCK_DIR" 2>/dev/null || echo "LOCK_RELEASE_FAILED: ${LOCK_DIR}" >&2
    else
      LOCK_QUARANTINED=1
      echo "LOCK_HELD_QUARANTINE: process-group cleanup could not be verified -- the concurrency lock is intentionally RETAINED at ${LOCK_DIR} to prevent another condition from starting against these shared capture paths; manual operator verification and lock removal is required" >&2
    fi
  fi

  echo "RUN_STATUS_LABEL=${RUN_STATUS_LABEL}"
  echo "PROBE_EXIT_CODE=${PROBE_EXIT_CODE:-n/a}"
  echo "CLEANUP_STATUS=${CLEANUP_STATUS}"
  echo "LOCK_QUARANTINED=${LOCK_QUARANTINED}"
  echo "ROLLBACK_OUTCOME=${ROLLBACK_OUTCOME_LABEL} ROLLBACK_STATUS=${ROLLBACK_STATUS}"
  echo "UAC_FINAL_STATUS=${UAC_FINAL_STATUS}"
  echo "ARCHIVE_COMPLETE=${ARCHIVE_COMPLETE}"

  if [[ "$rc" -ne 0 ]]; then
    echo "FINAL_EXIT_CODE=${rc} (preserving the run's own non-zero outcome)"
    exit "$rc"
  fi
  if [[ "$CLEANUP_STATUS" -ne 0 ]]; then
    echo "WRAPPER_FAILURE: the probe's own process group could not be confirmed terminated -- a writer may still be alive; exiting nonzero despite an otherwise successful probe run" >&2
    echo "FINAL_EXIT_CODE=96"
    exit 96
  fi
  if [[ "$ROLLBACK_STATUS" -ne 0 || "$UAC_FINAL_STATUS" -ne 0 ]]; then
    echo "WRAPPER_FAILURE: final hardware state did not verify -- exiting nonzero despite an otherwise successful run" >&2
    echo "FINAL_EXIT_CODE=91"
    exit 91
  fi
  if [[ "$ARCHIVE_COMPLETE" -ne 1 ]]; then
    echo "WRAPPER_FAILURE: this run's own archived evidence is not exactly the expected 3-trial set with a validated JSON<->WAV mapping -- not a successful condition run" >&2
    echo "FINAL_EXIT_CODE=93"
    exit 93
  fi
  echo "FINAL_EXIT_CODE=0"
  exit 0
}
trap finalize EXIT

# ---- Evidence storage must exist and be lockable BEFORE any precheck or
# mutation is ever attempted. -------------------------------------------- #
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

# ---- R0063 Correction 3: persisted-log readiness established BEFORE any
# mixer interaction, including the precheck reads. `exec > >(tee -a "$LOG")`
# alone proves nothing about whether tee actually opened the file -- a
# synchronous probe write is checked first, then a canary line's actual
# on-disk appearance is confirmed via a bounded poll. This establishes
# readiness AT THIS POINT ONLY -- not a guarantee against every possible
# LATER storage failure (e.g. the disk filling up mid-run). -------------- #
if ! : > "$LOG" 2>/dev/null; then
  echo "FATAL: could not create/open the persisted log file ${LOG}" >&2
  RUN_STATUS_LABEL="log_open_failed"
  exit 95
fi

exec > >(tee -a "$LOG") 2>&1

LOG_READY_CANARY="LOG_PIPELINE_READY ${TS} pid=$$"
echo "$LOG_READY_CANARY"
log_ready=0
waited=0
while [[ "$waited" -lt 50 ]]; do
  if grep -qF "$LOG_READY_CANARY" "$LOG" 2>/dev/null; then
    log_ready=1
    break
  fi
  sleep 0.1
  waited=$((waited + 1))
done
if [[ "$log_ready" -ne 1 ]]; then
  echo "FATAL: persisted log pipeline did not confirm readiness within the bounded startup window -- tee may not have started writing to ${LOG}" >&2
  RUN_STATUS_LABEL="log_pipeline_not_confirmed"
  exit 95
fi

echo "=== R0057 gain A/B experiment -- condition ${CONDITION} -- ${TS} ==="
echo "LOG=${LOG}"

# ---- Precheck: abort BEFORE any mutation on mismatch, unreadable value,
# or a control-identity/limits/switch mismatch. -------------------------- #
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

# ---- Set condition + verify (mutation flag; stricter validation catches a
# possibly-partial write). ------------------------------------------------ #
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

# ---- Probe launch (R0063 Correction 1: deterministic ownership handshake,
# never a speculative pgid sample). --------------------------------------- #
# TEST-ONLY: the real probe never reads these; a fake launcher (used only
# by tests/test_run_r0057_gain_ab_condition_sh.py) uses them to know where
# to write its own fake artifacts, since JSON_DIR/PCM_DIR are computed from
# CAPTURE_ROOT above and may be a temp directory during a test.
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
LAUNCH_ARGS=(timeout --signal=TERM --kill-after="${TIMEOUT_KILL_AFTER_S}s" "${TIMEOUT_BOUND_S}s" "${PROBE_CMD[@]}")
if ! launch_probe_with_verified_ownership; then
  echo "ABORTING: probe launch ownership could not be established within the bounded startup window (see STARTUP_OWNERSHIP_HANDSHAKE_FAILED above)." >&2
  RUN_STATUS_LABEL="launch_ownership_handshake_failed"
  exit 97
fi

wait "$CHILD_PID"
PROBE_EXIT_CODE=$?
# `wait` returning only proves the SESSION LEADER (timeout) has been
# reaped -- it does not by itself prove every other member of the group has
# exited too (an orphaned descendant could still be running). Verify, and
# escalate/terminate if anything remains; persist the outcome so an
# unverified termination cannot silently produce a successful exit.
if terminate_process_group "$CHILD_PGID" 5; then
  CLEANUP_STATUS=0
else
  CLEANUP_STATUS=1
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
