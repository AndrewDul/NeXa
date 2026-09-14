# R0063 — Gain A/B Wrapper External-Review Corrections (Offline, Not Executed)

**Date:** 2026-09-14
**Milestone:** M2.6B.4N follow-up (external source-review checkpoint,
post-R0062)
**Status:** **CORRECTION CHECKPOINT — COMPLETE. OFFLINE ONLY.** An external
source review of the complete R0062 wrapper found six concrete defects —
a process-group ownership race at launch, a cleanup failure that could
still produce a successful exit, mixer validation weaker than documented
(substring matching, a switch check satisfied by one `[on]` marker in a
genuinely mixed state), archive acceptance that did not enforce its own
stated contract, persisted-log readiness never actually confirmed before
mutation, and a fake `amixer` that accepted unsupported control
arguments. All six are fixed in this checkpoint, each backed by a new,
deterministic offline test that fails against the R0062 behavior and
passes against the fix. **ROUND 2 (same uncommitted checkpoint, continued
in place — see the dedicated section below): a FURTHER external review of
this checkpoint's own round-1 diff found five more concrete defects — a
one-way (not synchronized) startup handshake, unbounded waits remaining in
two failure paths, a mapping-validation gap that skipped a trial with
both paths empty, unchecked manifest-write/source-removal failures, and a
failed rollback write that could still be reported as an operational
success. All five are fixed, each backed by a new test.** **ROUND 3 (same
uncommitted checkpoint): two further, narrowly scoped defects found in the
round-2 startup handshake — an unchecked initial ack-removal, and an
own-pgid lookup failure silently treated as "no match" — both fixed, each
with a new test proving the fake probe never starts on either path.**
**This checkpoint authorized local implementation and offline validation
only throughout all three rounds. No hardware writes, audio runs, Gemini,
production changes, dependency changes, or push, at any point. The R0057
gain A/B experiment itself remains NOT EXECUTED — no approval of any kind
has been given for it. `M2.6B` remains IN PROGRESS. This checkpoint is
now CLOSED WITH A LOCAL COMMIT — see COMMIT HASHES below. Not pushed.**

## TASK RESULT

**PASS.**

## PURPOSE

Fix the six concrete findings named by an external review of the R0062
wrapper (`docs/reports/R0062_gain_ab_wrapper_deep_review_corrections_20260914.md`,
commit `148f486`), each with a root-cause fix and new, focused offline
regression coverage that would fail against the PREVIOUS (R0062) behavior.
Correct the procedure document's own §4/§5/§7 to match. Do not repeat
warm-up validation, do not launch a broad new audit, do not redesign the
production probe, do not add unrelated DSP work.

## BASELINE

- **Baseline commit:** `9900d381172f61a0f2107b597ec899baae3591d7` (`main`,
  HEAD at the start of this checkpoint) — verified via `git rev-parse
  HEAD`, matching the R0062 checkpoint's own final commit.
- **Working tree at start:** confirmed clean (`git status --short` empty).
- Read `AGENTS.md`, the actual current `run_r0057_gain_ab_condition.sh`,
  both fakes, `tests/test_run_r0057_gain_ab_condition_sh.py`, the R0057
  procedure document, and R0057–R0062 in full before changing anything —
  this session began as a read-only takeover review (recorded separately
  in the conversation, not a numbered report of its own) that
  independently re-verified R0062's claims against the actual source and
  ran the then-existing 24-test suite once, observing 24/24 pass — that
  observation is the one immediately prior VERIFIED FACT this checkpoint
  built on before making any further change.

## THE SIX FINDINGS, THEIR ROOT CAUSES, AND THEIR FIXES

### 1. Process-group ownership race at launch

**Root cause:** R0062's own `CHILD_PGID="$(ps -o pgid= -p "$CHILD_PID"
...)"` sampled the child's process group IMMEDIATELY after backgrounding
`setsid timeout ... &`. `setsid()` takes effect asynchronously inside the
forked child; the parent's `ps` call could win the race and observe the
child BEFORE `setsid()` ran, in which case it would read back the
WRAPPER's OWN process group — every later group-directed signal
(including the SIGKILL escalation path) would then target the wrapper
itself, and anything else sharing that group, never the probe. The
R0062-era fallback (`CHILD_PGID="$CHILD_PID"` when `ps` returned nothing)
did not establish ownership either — a bare guess, not a proof.

**Fix (`launch_probe_with_verified_ownership`, new):** the parent never
samples speculatively. The launched child (`setsid bash -c '... echo "$$"
> "$marker_path"; exec "$@"'`) writes its OWN `$$` — which, for a process
that has just called `setsid()`, IS both its new pid and its new pgid, by
definition of what `setsid()` does — to a marker file strictly AFTER
`setsid()` has completed and strictly BEFORE it execs the real command.
There is no window in which the parent can observe a stale value, because
the parent never samples at all; it only reads what the child itself
already confirmed, via a bounded poll on the marker file's existence and
size. The candidate is then cross-checked against the wrapper's OWN
current pgid (`ps -o pgid= -p "$$"`) and rejected if they match, before
`CHILD_PGID_VERIFIED` is ever set. A signal arriving DURING this
handshake (before ownership is verified) terminates the known `CHILD_PID`
directly via a new `terminate_by_pid()` helper — never a group signal —
so it can never leak the child or reach an unrelated process, satisfying
the "handle interruption during startup without leaking the child or
signalling unrelated processes" requirement without a guessed sleep or
broad process matching.

**A self-found bug during this checkpoint's own first implementation
attempt (recorded, not hidden):** the first draft ALSO cross-checked the
candidate against a SECOND, later `ps -o pgid= -p "$CHILD_PID"` reading of
the child's own current pgid. Running the new test suite against this
first draft failed 16 of 42 tests with `STARTUP_OWNERSHIP_HANDSHAKE_FAILED:
CHILD_PID's own current pgid (unreadable) does not match the self-reported
PGID` — because the fake probe used throughout the offline suite completes
almost instantly (no real 60s warm-up), the child process had, in many
runs, ALREADY EXITED by the time this second `ps` call ran, a NEW race
this checkpoint's own first draft introduced. Removed entirely: the
marker-file value is not a sample subject to a race (it is the child's own
synchronous, in-process report of its own state immediately after
`setsid()` succeeded), and a second, later `ps` lookup added no safety the
marker did not already provide, while being racy against a fast-completing
child. This is recorded here because it is exactly the kind of claim this
checkpoint's own instruction (do not accept an unverified claim) applies
to its OWN work, not just R0062's.

### 2. Cleanup failure could still produce exit 0

**Root cause:** after `terminate_process_group` failed, R0062's code only
printed a warning, cleared `CHILD_PID`/`CHILD_PGID`, and continued
straight to the probe's own exit status — a run could finish with
`FINAL_EXIT_CODE=0` while a member of the probe's own process group was
still alive. Separately, `pgrep`'s exit codes were not distinguished:
`if ! pgrep -g "$pgid" >/dev/null 2>&1; then return 0 (empty); fi` treated
ANY nonzero exit — including `pgrep`'s own exit 2 (syntax error) or 3
(fatal error, e.g. `/proc` unreadable) — identically to exit 1 ("no
processes matched"), so an inspection FAILURE could be reported as
verified emptiness.

**Fix:** `CLEANUP_STATUS` (optimistic default — nothing launched means
nothing to clean up; flipped to failed ONLY by an actually-attempted,
actually-failed termination) is now persisted and checked by `finalize()`;
an unverified termination forces exit `96`, distinct from every other
failure class, and checked ONLY on the path that would otherwise report
success (an already-nonzero preserved exit code, e.g. the probe's own
failure or a precheck failure, is unaffected — those already short-circuit
first, unchanged from R0062). `pgid_liveness()` (new) returns three
distinguishable outcomes — confirmed alive (0), confirmed empty (1), or an
inspection error (2, from any `pgrep` exit code other than 0 or 1) — and
`terminate_process_group`'s own escalation logic never treats an
inspection error as empty at any point, including its FINAL check (the one
that decides the overall verdict). No unbounded wait was added anywhere in
the signal path — the existing bounded TERM-then-KILL polling loops are
unchanged in structure, only their liveness-check calls now go through
`pgid_liveness`. Rollback is still always attempted (via `finalize()`'s
own unconditional call, unchanged), and the archive step still preserves
whatever evidence exists — only the run's own FINAL reported outcome can
no longer silently claim a stable, complete capture while a writer may
still be alive.

### 3. Logging readiness not established before mixer mutation

**Root cause:** `exec > >(tee -a "$LOG") 2>&1` sets up an asynchronous
pipe; by itself it proves nothing about whether `tee` actually opened the
file for writing. Directory creation alone (the only prior check) does
not prove the SPECIFIC log file can be created (e.g. the directory exists
but is read-only).

**Fix:** a synchronous `: > "$LOG"` probe write is checked FIRST (catches
a plainly unwritable log directory immediately, exit `95`). Once the
`tee` pipe is set up, a canary line (`LOG_PIPELINE_READY <ts> pid=$$`) is
written and its actual on-disk appearance in `$LOG` is confirmed via a
bounded poll (up to 5s) BEFORE the precheck — or anything else — proceeds;
a failure to confirm aborts (exit `95`) with zero mixer interaction of any
kind, satisfied even before the precheck's own READ calls, a stronger
guarantee than "before any write" alone. This establishes readiness AT
THAT POINT ONLY, stated explicitly in both the script's own header and the
procedure document — it is not, and does not claim to be, protection
against a LATER storage failure (e.g. the disk filling up mid-run).

### 4. Mixer validation weaker than documented

**Root cause (identity/limits):** `[[ "$output" != *"Simple mixer control
'PCM',1"* ]]` and `[[ "$output" != *"Limits: Playback 0 - 60"* ]]` are
SUBSTRING checks — `'PCM',1` is itself a substring of `'PCM',10`/`'PCM',11`
/...; `0 - 60` is itself a substring of `0 - 600`/`0 - 160`. A
coincidentally-matching SUPERSET string would pass.

**Root cause (switch):** `[[ "$output" != *"[on]"* ]]` required only ONE
`[on]` occurrence ANYWHERE in the output. For `UACDemoV10` (two channels),
a genuinely MIXED state — Front Left `[off]`, Front Right `[on]` — still
satisfied this check on the strength of the one remaining `[on]` channel;
the substring check had no way to notice the OTHER channel was off.

**Root cause (rollback readback):** `if ! amixer -c Array sset 'PCM',1
"${BASELINE_RAW}" ...; then ROLLBACK_STATUS=1; ROLLBACK_OUTCOME_LABEL=
"set_failed"; elif validate_array_raw ...; then ...` — when the restore
write itself reported failure, the `elif` branch (the independent
readback) was never reached at all, so the ACTUAL observed final state in
that case was never learned or reported.

**Fix:** `read_array_raw`/`read_uac_raws` now require an EXACT, whole-line
match (`grep -qxF`) for both the control-identity line and the Limits
line — a superset string fails a whole-line comparison by construction,
without needing hand-crafted anchoring. The switch check now counts
`[on]`/`[off]` markers separately and requires the EXACT expected count
(1 for Array's own Mono channel, 2 for `UACDemoV10`'s stereo pair) with
ZERO `[off]` markers — a mixed state now fails because the `[off]` count
is nonzero, independent of how many `[on]` markers are also present.
`rollback()` now ALWAYS attempts the independent Array readback, even when
the restore write itself reported failure — `ROLLBACK_STATUS`/
`ROLLBACK_OUTCOME_LABEL` are derived strictly from what is OBSERVED
(`read_array_raw`'s own return), never assumed from the write command's
own self-reported exit status; both facts (`ROLLBACK_WRITE_STATUS=ok|
failed`, `ROLLBACK_OBSERVED_RAW=<value>`) are now logged explicitly and
separately.

### 5. Archive acceptance did not enforce its stated contract

**Root cause (WAV count):** `ARCHIVE_COMPLETE` required every expected
FILENAME to be present but never checked `ARCHIVE_WAV_COUNT` against the
expected total — an extra, untracked WAV file alongside the expected six
would still pass.

**Root cause (mapping):** the MANIFEST's own "original path -> archived
file" mapping was built by taking the JSON's own recorded `mic_wav`/
`ref_wav` path, extracting its BASENAME, and checking whether a file with
that basename existed in the archive directory — never checking that the
ORIGINAL path itself was one of THIS RUN's own newly-discovered files. A
JSON referencing a path that merely shares a basename with some unrelated
file — or a stale path that was never actually written this run — would
still produce a mapping line that looked correct.

**Root cause (error propagation):** the manifest was written via `if ! {
multi-line block; } > "$ARCHIVE_DIR/MANIFEST.txt"; then ARCHIVE_OK=0; fi`
— a `{ }` group's own exit status reflects only its LAST executed
command, not an aggregate of everything inside it; an earlier `sha256sum`
failure, buried inside the block, was invisible to this check. Separately,
`echo "  $(basename "$f")  sha256=$(sha256sum "$f" | awk '{print
$1}')"` embeds a command substitution INSIDE another command's (`echo`'s)
argument — `echo` itself always exits 0 regardless of what the embedded
substitution produced, so a failing `sha256sum` there was invisible to
any exit-status check at all, even with `pipefail` active (which governs
pipeline exit status, not a substitution embedded in a larger argument).

**Fix:** `ARCHIVE_COMPLETE` now additionally requires `ARCHIVE_WAV_COUNT
-eq EXPECTED_WAV_COUNT` (`2 * EXPECTED_TRIALS`, exactly). A new
`write_manifest_and_validate_mapping()` function parses the archived
JSON's own trials, and for each trial's `mic_wav`/`ref_wav` path,
requires an EXACT match (`grep -qxF`) against `OWNED_WAV_ORIGINALS` — the
newline-separated set of ORIGINAL paths that were actually discovered via
`find -newer "$MARKER"` AND successfully archived this run (populated
only on a per-file successful archive) — never merely a basename lookup;
a mismatch is logged both into the manifest and to stderr
(`MANIFEST_MAPPING_REJECTED`/`MANIFEST_MAPPING_MISSING`/
`MANIFEST_MAPPING_FIELD_MISSING`) and sets `MAPPING_VALID=0`. Duplicate
references (the same path referenced by more than one trial slot) are
detected via `sort | uniq -d` over the full referenced-path list and
logged as `MANIFEST_DUPLICATE_WAV_REFERENCE`. `ARCHIVE_COMPLETE` now
requires `MAPPING_VALID=1` in addition to the existing filename/
trial-count checks. Every hash computation inside the manifest function is
now a standalone, explicitly-checked statement (`if h="$(sha256sum
...)" && [[ -n "$h" ]]; then ... else manifest_ok=0; fi`) — never embedded
inside an `echo`'s own argument — and the function's own return status is
derived from an explicit `manifest_ok` flag set on every individual
failure, not from a surrounding block's aggregate exit status; the
manifest itself is written to a temp file and atomically `mv`'d into
place, with the `mv` itself checked. Partial evidence is still archived on
an incomplete/inconsistent run, exactly as before (`archive_one_file` is
unconditionally attempted for every discovered file regardless of what
the LATER mapping validation finds) — only the COMPLETENESS verdict is
now honest about what was actually verified. `archive_one_file` (new,
factored out of the previous inline per-file loop body) is itself a
single, individually-checked hash/copy/re-hash/compare/remove sequence,
reused for both JSON and WAV archiving.

### 6. Fake `amixer` accepted unsupported control arguments

**Root cause:** the fake checked only `card`/`verb` (via `len(argv) < 4`,
a MINIMUM, not an exact bound) and used `argv[-1]` for the `sset` value —
`argv[3]` (the actual control identifier, e.g. `PCM,1`) was never
checked, an extra trailing argument was silently ignored, and a
non-integer or out-of-range value would either be silently accepted (if
somehow parseable) or raise an uncaught Python exception rather than a
clean, structured rejection.

**Fix:** every supported invocation shape now checks EXACT arity and the
EXACT `argv[3]` control identifier (`PCM,1` for `Array`, `PCM` for
`UACDemoV10`) before proceeding; an `sset` value is parsed with an
explicit `try/except ValueError` (clean rejection, not an uncaught
exception) and range-checked (`0 <= value <= 60`) before being accepted.
Every rejection goes through a single `reject(argv, reason)` helper (exit
1, a distinct diagnostic message) — there is still no catch-all success
path. New injectable state for the mixed-switch and prefix-collision
scenarios this checkpoint's own tests needed:
`array_identity_prefix_collision`, `array_limits_prefix_collision`,
`uac_limits_prefix_collision` (a control identity/limits line that is a
SUPERSET string of the expected one, e.g. `'PCM',10` or `0 - 600`), and
`uac_switch_off_after_first_read` (call-counted drift, symmetric to the
existing `uac_fail_after_first_read`/`uac_mismatch_after_first_read`,
isolating "wrong only at the wrapper's own later final-state check").

## NEW/UPDATED TEST DOUBLES

- `test_fakes/fake_amixer.py` — rewritten per finding 6 above (strict
  arity/argv[3] checking, value parsing/range checks) plus the new
  injectable state listed above.
- `test_fakes/fake_probe.py` — new `FAKE_PROBE_CORRUPT_MAPPING` env var
  (`"wrong_path"`/`"duplicate"`/`"extra_wav"`), deliberately corrupting
  the JSON<->WAV relationship for testing the wrapper's own new
  structural mapping validation — never a real probe behavior.
- `test_fakes/fake_pgrep.py` (**new file**) — a shim that delegates to
  the REAL, absolute-path system `pgrep` for every invocation except one
  it is specifically asked to intercept (`PGREP_FAKE_ERROR_PGID` for one
  specific pgid, or `PGREP_FAKE_ALWAYS_ERROR=1` for a blanket mode used
  by this checkpoint's own test, which does not know the real pgid in
  advance) — simulating a fatal inspection error (exit 3) rather than
  either "alive" or "confirmed empty". Located by absolute path
  (`/usr/bin/pgrep`, confirmed present on this system) specifically to
  avoid recursing into itself when `PATH` is shadowed by its own
  directory during a test.

## TESTS ADDED (18 new, 42 total — up from R0062's 24)

| Finding | Test(s) |
|---|---|
| 1. Startup ownership handshake / interruption | `TestStartupOwnershipHandshake` (2: `test_verified_pgid_excludes_wrappers_own_process_group`, `test_interruption_during_startup_handshake_terminates_child_without_leaking`) |
| 2. Cleanup failure prevents success | `TestCleanupFailure.test_cleanup_failure_after_successful_probe_prevents_exit_zero` (1, via the new `R0057_AB_TEST_FORCE_CLEANUP_FAILURE` test-only hook) |
| 2. Inspection error never reported as empty | `TestProcessInspectionError.test_pgrep_inspection_error_is_never_reported_as_verified_emptiness` (1) |
| 3. Log-open failure before mutation | `TestLogOpenFailure.test_log_directory_unwritable_aborts_before_precheck` (1) |
| 4. Mixed UAC switch | `TestPrecheckFailureCausesZeroWrites.test_uac_mixed_switch_causes_zero_writes`, `TestFinalUacCheck.test_final_uac_mixed_switch_after_otherwise_successful_run` (2) |
| 4. Identity/limits prefix collision | `TestPrecheckFailureCausesZeroWrites.test_array_identity_prefix_collision_causes_zero_writes`, `test_array_limits_prefix_collision_causes_zero_writes` (2) |
| 4. Rollback write failure + independent readback | `TestFailedRollback.test_rollback_write_reports_failure_but_observed_state_is_independently_reported` (1) |
| 5. Incomplete/inconsistent archive mapping | `TestArchiveMappingConsistency` (3: wrong-path reference, duplicate reference, extra untracked WAV) |
| 6. Fake amixer arity/argument strictness | `TestFakeAmixerRejectsUnsupportedInvocations` (+5: extra trailing arg, wrong control on sget, wrong control on sset, malformed value, out-of-range value) |

Every new test isolates fake hardware (the same `PATH`-prepended
fake-`amixer`/fake-`pgrep` mechanism as the existing suite) and, where a
real OS process is involved (the startup-handshake and cleanup-failure
tests), signals only a PID/PGID this checkpoint's own test itself
launched and tracked — never a broad process-kill or an unrelated group.

## ROUND 2 — FURTHER EXTERNAL-REVIEW FINDINGS (same uncommitted checkpoint)

A further external review of the ROUND 1 diff above found five more
concrete defects. Kept concise per instruction — root causes are one
sentence each; the wrapper script's own header comment (`ROUND 2`
section) and the procedure document carry the fuller rationale.

| # | Finding (root cause, one line) | Function(s) changed | Regression test(s) |
|---|---|---|---|
| 1 | Handshake was one-way: child exec'd immediately after publishing its marker, before the parent finished validating — an interruption in that window terminated only the (possibly already-exec'd-past) `CHILD_PID`, and the actual `setsid`/launcher-child relationship (not merely "the probe") was never accounted for. | `launch_probe_with_verified_ownership` (rewritten: child now blocks on an explicit ACK file before `exec`; marker-write failure checked; new `CHILD_PGID_CANDIDATE` global), `on_signal` (new middle branch group-kills the candidate) | `TestStartupOwnershipHandshake.test_interruption_after_candidate_known_before_acknowledgement_kills_whole_group` (new); existing `test_interruption_during_startup_handshake_terminates_child_without_leaking` still covers the earlier (pre-marker) window unchanged |
| 2 | `terminate_by_pid`'s own trailing `wait "$pid"` and `on_signal`'s own `wait "$CHILD_PID"` after a FAILED group termination were both unconditional — a process surviving SIGKILL (D-state) would block them indefinitely, past the documented cleanup budget. Unverified cleanup also auto-released the lock and let `archive_one_file` unlink originals a live writer might still need. | `terminate_by_pid`, `on_signal`, `archive_one_file` (new `unlink_src` param), `archive_current_run_artifacts` (computes it from `CLEANUP_STATUS`), `finalize` (lock quarantine, `LOCK_QUARANTINED`) | `TestCleanupFailureQuarantine.test_unverified_cleanup_quarantines_lock_and_preserves_original_sources` (new) |
| 3 | The mapping loop `continue`d past a trial row with BOTH `mic_wav`/`ref_wav` empty instead of rejecting it — 3 trial objects + 6 real WAVs + only 2 populated rows could still report completeness. JSON structural errors were silently coerced to empty output. | `write_manifest_and_validate_mapping` (per-trial loop rewritten: no skip, exact per-trial/per-role basename check, stricter Python JSON parser propagating structural errors as `ERROR`) | `TestArchiveMappingConsistency.test_trial_with_both_paths_missing_prevents_successful_exit`, `.test_swapped_mic_ref_roles_prevents_successful_exit` (both new) |
| 4 | Manifest line writes (`echo ... >> "$tmp_out"`) were never individually checked; the temp file was created in the system tmp dir (possibly a different filesystem than `$ARCHIVE_DIR`, breaking the claimed atomic `mv`); `archive_one_file`'s own `rm -f "$src"` unconditionally returned 0 even on failure. | `write_manifest_and_validate_mapping` (new `_manifest_append` helper; `mktemp "$ARCHIVE_DIR/.manifest.XXXXXX"`), `archive_one_file` (checked `rm -f`, returns 1 on failure) | `TestArchiveSourceRemovalFailure.test_unremovable_source_is_reported_and_evidence_still_preserved` (new, via a new `FAKE_PROBE_CHMOD_PCM_DIR_READONLY_AFTER_WRITE` fake-probe hook) |
| 5 | `ROLLBACK_STATUS=0`/`"clean"` could be reported when the restore `sset` itself reported FAILURE, as long as the independent readback happened to observe the baseline anyway (e.g. because the value was already there). | `rollback` (outcome now requires BOTH `set_ok==0` AND a matching readback for `"clean"`; a failed write always forces `ROLLBACK_STATUS=1`, labeled `write_failed_baseline_observed`/`_readback_mismatch`/`_readback_unreadable`) | `TestFailedRollback.test_rollback_write_fails_but_readback_observes_baseline_does_not_report_success` (new); existing `test_rollback_write_reports_failure_but_observed_state_is_independently_reported` updated in place (label renamed to `write_failed_readback_mismatch`, not deleted) |

**Also fixed, per explicit instruction ("correct stale claims"):**
- The wrapper's own header comment still described the SECOND `ps`-based
  pgid re-check this same checkpoint had already found racy and removed
  (round 1's own self-found bug) — corrected in place, with a pointer to
  where the fuller account lives.
- "Zero mixer interaction" on a log-open failure was inaccurate — corrected
  to "zero mixer WRITES"; `finalize()`'s own EXIT trap still runs
  `rollback()`'s harmless, read-only `UACDemoV10` check even then (the
  same already-documented behavior as the storage-setup-failure path, not
  a new exception).
- "Three passing runs... stable, not flaky" overclaimed what three runs
  prove — corrected below (WHAT I VERIFIED) to state this as evidence of
  no *obvious* flakiness, not a formal absence-of-races proof.
- This report's own verification commands were re-run capturing the
  ACTUAL `unittest` exit status directly (`$?` immediately after the
  command, no `| tail` in between — a pipeline ending in `tail` without
  `pipefail` reports `tail`'s own status, not the test runner's), not
  merely inferred from the printed "OK"/"FAILED" line.

## ROUND 3 — TWO NARROWLY SCOPED FIXES (same uncommitted checkpoint)

A further external review of the round-2 diff found two concrete
defects in `launch_probe_with_verified_ownership`, both fixed here with
no broader change:

1. **Unchecked ack removal.** The initial `rm -f "$ack"` (clearing any
   pre-existing ack file before the child is ever launched) was
   unchecked, and the file's actual absence was never independently
   confirmed. A failed removal could leave a STALE ack already present,
   which the child's own wait loop would observe immediately, exec'ing
   the real command before the parent had validated anything. **Fixed:**
   both the removal's own exit status and `[[ ! -e "$ack" ]]` are now
   checked together; failure aborts (exit `97`) BEFORE any child is
   ever launched (`CHILD_PID` is never set on this path).
2. **Unverified own-pgid lookup silently treated as "no match".** `if
   [[ -n "$own_pgid" && "$candidate" == "$own_pgid" ]]` meant an EMPTY
   `own_pgid` (the `ps` lookup itself failing, or its output being
   unparseable) made the `-n` guard skip the whole safety check —
   proceeding to accept `CHILD_PGID_CANDIDATE` and later publish the ack
   without ever having proven the candidate was distinct from the
   wrapper's own group. **Fixed:** a successful lookup with a valid,
   POSITIVE numeric result (`^[1-9][0-9]*$`) is now REQUIRED before
   `CHILD_PGID_CANDIDATE` is accepted or an ack is ever published; an
   empty, malformed, or failed lookup aborts (exit `97`) via a
   PID-scoped (never group-scoped) termination of the still-parked
   child, preserving `CLEANUP_STATUS`/quarantine exactly as any other
   handshake failure does.

Two new TEST-ONLY hooks (`R0057_AB_TEST_FORCE_ACK_REMOVAL_FAILURE`,
`R0057_AB_TEST_FORCE_OWN_PGID_LOOKUP_FAILURE`) make each path
deterministically reproducible, mirroring the existing
`R0057_AB_TEST_FORCE_CLEANUP_FAILURE` pattern already used elsewhere in
this script.

**New regression tests** (`TestStartupOwnershipHandshake`, +2):
`test_ack_removal_failure_aborts_before_launching_child` (asserts exit
`97`, no `LAUNCHED_CHILD_PID=` line at all, no `FAKE_PROBE_READY`, clean
rollback) and `test_own_pgid_lookup_failure_aborts_before_acknowledging`
(asserts exit `97`, the child WAS launched but no `CHILD_PGID_CANDIDATE=`
and no `FAKE_PROBE_READY` — i.e. never acknowledged, never exec'd — clean
rollback). Both confirm the fake probe never starts on either path.

**Source-delivery reconciliation (read-only, requested separately):**
inspected the four previously-flagged excerpts (`on_signal`'s verified-
group failure branch; the parser's `status_line` assignment; the
complete WAV archive loop; the main launch conditional through
`PROBE_EXIT_CODE` capture) directly against the current file. All four
are intact, complete, and consistent with the documented design — no
code defect was found in any of them; the earlier concern was about
completeness of a pasted chat excerpt, not the underlying source, which
was independently re-verified as unmodified in that interval (no
`Edit`/`Write` call touched it between the last passing test run and the
reconciliation check).

**Verification (round 3):**
```
bash -n docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
```
→ clean.
```
.venv/bin/python -m unittest tests.test_run_r0057_gain_ab_condition_sh -v
```
→ **50 tests, OK**, `ACTUAL_UNITTEST_EXIT_CODE=0` (captured directly, no
`| tail`) — up from round 2's 48; the 2 new tests above.
```
.venv/bin/ruff check tests/test_run_r0057_gain_ab_condition_sh.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_probe.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_pgrep.py
```
→ all checks passed (after fixing 2 line-length violations in the two
new tests — an ordinary lint fix, not a functional change).
`git diff --stat -- src/nexa`: empty. No leaked owned process after the
suite (`ps aux` confirmed empty). Real hardware mixer unchanged
(read-only): `Array 'PCM',1` = -20.00dB/raw 40/[on], `UACDemoV10` =
-0.94dB/raw 147/[on]. Per instruction, only ONE full-suite run was
performed this round (no repeated stability runs).

## WHAT I VERIFIED

```
bash -n docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
```
→ clean.

```
.venv/bin/python -m unittest tests.test_run_r0057_gain_ab_condition_sh -v
```
→ **42 tests, OK**, three consecutive full runs (~43.2s, ~43.4s, ~43.2s).
**CORRECTED (round 2):** "stable, not flaky" as originally worded here
overclaimed what three runs on one machine actually prove. Three passing
runs is evidence of no *obvious* flakiness under this specific test
machine's own timing, not a formal proof of the absence of every
possible race — several of these tests use real, bounded sleeps/polls
(0.1–10s scale: the SIGKILL-escalation and startup-handshake-
interruption tests each genuinely exercise several real seconds of
bounded polling), which are inherently more likely to surface a timing
bug than an instant in-process assertion, but "ran N times without
failing" is empirical, not exhaustive, for any test that depends on
process scheduling.

```
.venv/bin/ruff check tests/test_run_r0057_gain_ab_condition_sh.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_probe.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_pgrep.py
```
→ all checks passed (after fixing 7 line-length violations found on the
first pass — recorded as an ordinary lint fix, not a functional change).

`git diff --stat -- src/nexa`: **empty**, confirmed against the baseline
commit.

Real hardware mixer verified unchanged (read-only) this checkpoint:
```
Array 'PCM',1  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```

The probe/bargein regression suites (`tests.test_m2_6b4m_self_echo_probe`,
`tests.test_bargein_m2_5b`) were **not re-run this checkpoint** — no
production code under `src/nexa` was touched (confirmed by the empty
`git diff --stat -- src/nexa` above) and no file under
`docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
itself was touched either — this checkpoint is scoped entirely to the
wrapper, its fakes, its own tests, and the procedure document, per
instruction not to redesign the production probe or repeat warm-up
validation.

### ROUND 2 verification (same checkpoint, continued)

```
bash -n docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
```
→ clean.

```
.venv/bin/python -m py_compile <every touched/new *.py>
```
→ clean.

```
.venv/bin/python -m unittest tests.test_run_r0057_gain_ab_condition_sh -v > /tmp/... 2>&1
ACTUAL_EXIT=$?
```
Run with the ACTUAL unittest exit status captured directly (`$?` read
immediately after the command with no `| tail` in between), per
instruction. First run: **1 failure** — an EXISTING test
(`test_wrong_path_reference_prevents_successful_exit`) asserted a
`MANIFEST_MAPPING_REJECTED` marker that finding 3's new, EARLIER
per-trial basename check now intercepts first for that specific fake
input (a correct behavior change, not a regression) — the fake's own
"wrong_path" corruption mode was adjusted to use a path with the correct
expected basename but the wrong directory, isolating "rejected: not
owned" from "rejected: wrong basename" again. Re-run after the fix:
**`ACTUAL_UNITTEST_EXIT_CODE=0`, 48 tests, OK** (two further consecutive
runs, same result, ~58.4–58.6s each — see the corrected framing above for
what this does and does not prove).

```
.venv/bin/ruff check <every touched/new *.py>
```
→ all checks passed.

`git diff --stat -- src/nexa`: **empty**.

Real hardware mixer re-verified unchanged (read-only) after round 2:
identical to the round-1 reading above.

`ps aux | grep -iE "fake_probe|fake_amixer|fake_pgrep|gain_ab_condition|m2_6b4m_self_echo_probe"`
→ empty, confirmed after the full round-2 suite (no leaked owned process
from any of the new interruption/cleanup-failure tests).

### Process-inspection check performed BEFORE this session's own work began (read-only takeover)

Before any edit, this session confirmed via read-only process/session
inspection that no other session held an active write lock on this
checkout (another Claude Code session had the working directory open but
held no write file handles, had made no file modification in the
preceding 5 minutes, and held no concurrency lock) — recorded here for
completeness since it was part of the same continuous session, not a
separate numbered checkpoint.

## VERIFIED FACTS

1. All six externally-reviewed findings are fixed in the wrapper script
   and/or its test doubles, each with a corresponding, passing offline
   test that would fail against the PREVIOUS (R0062) behavior — confirmed
   by running the test suite, not merely by writing it (each new test was
   observed to genuinely target R0062-era behavior via direct inspection
   of the R0062 source before it was changed).
2. `tests/test_run_r0057_gain_ab_condition_sh.py` → **48/48 pass**,
   `ACTUAL_UNITTEST_EXIT_CODE=0` captured directly (not through a `tail`
   pipe), in three total full-suite runs across both rounds combined (not
   merely individually, and not merely once) — see the framing correction
   above for what repeated passing runs do and do not prove.
3. `git diff --stat -- src/nexa` is empty; the probe's own production
   code (`m2_6b4m_self_echo_probe.py`) is unmodified.
4. Real hardware mixer state (`Array PCM,1` = -20.00dB/raw 40/[on],
   `UACDemoV10` = -0.94dB/raw 147/[on], both channels) is unchanged,
   verified read-only, this checkpoint (both rounds).
5. `ruff check` on all touched/new Python files: clean. `bash -n` on the
   corrected wrapper: clean. No leaked owned process after the full
   round-2 suite (confirmed via `ps aux`).
6. This checkpoint's own first implementation draft of the round-1
   startup-ownership handshake contained a genuine, self-introduced race
   (a redundant second `ps` lookup of the child's own pgid, racy against a
   fast-completing child) — found by running the new test suite against
   that draft (16/42 failures), root-caused, and removed; the marker-file
   mechanism alone is both necessary and sufficient. The round-1 header
   comment describing that removed check was itself found stale by the
   round-2 external review and is now corrected (see ROUND 2 section).
7. Round 2's own first full-suite run found ONE real (non-regression)
   test-assumption break in an EXISTING test, caused by the new,
   EARLIER-firing per-trial basename check now intercepting a fake input
   that used to reach a later check first — fixed by adjusting the fake's
   own corruption fixture, not by weakening the new check.

## HYPOTHESES

None outstanding blocking this checkpoint's own scope.

## UNRESOLVED

- The corrected wrapper has never been run against real hardware or the
  real probe — this checkpoint is offline-only, by instruction. Its first
  real-hardware exercise will be condition A itself, if and when approved.
- The R0057 gain A/B experiment remains **NOT EXECUTED** — no approval of
  any kind has been given.
- No dedicated test exercises a failure of the run-marker's own
  `mktemp`/`touch` creation specifically (as distinct from the earlier,
  already-tested failure of the parent evidence-storage `mkdir -p`) —
  the code path (exit 95) was inspected directly and is structurally
  identical to the tested path, but was not independently exercised this
  checkpoint. Not believed to be a functional gap; recorded as an honest
  scope boundary, not asserted as covered.
- `AecReferenceFeeder.chunks_dropped`/`respawns`/
  `AecReferenceHealth.failure_count` remain unexercised against a real
  nonzero value on real hardware (unchanged from R0060/R0061/R0062's own
  notes on this — out of this checkpoint's scope).
- **CLOSED:** this checkpoint's changes went through three rounds of
  external review while uncommitted, by explicit instruction each round,
  before being closed with one local commit covering all three rounds
  together (see COMMIT HASHES) — not pushed.
- **Round 2 additions:** "no unbounded wait remains" is verified by code
  removal/inspection (the unconditional `wait` calls no longer exist) and
  by the existing bounded-time interruption/cleanup-failure tests all
  completing promptly — it is NOT verified by a test that manufactures a
  genuinely SIGKILL-immune (D-state) process, which is not practically
  constructible in this offline suite; that remains an inherent limit of
  what can be proven without real, possibly-wedged hardware I/O.
- No dedicated test exercises an individual `_manifest_append` line-write
  failure directly (as distinct from the tested `mktemp` tmp-creation
  failure and the tested `rm -f` source-removal failure) — the function
  is structurally identical for every call site, but this specific
  failure mode was not independently reproduced this checkpoint.
- The two-way handshake's OWN internal bound
  (`R0057_AB_TEST_ACK_WAIT_S`, default 10s for a real run) has not been
  exercised at its OWN default value in any test (tests override it only
  implicitly by never needing it, since the parent normally acknowledges
  almost instantly) — the timeout-exit path (child's own `exit 98` when
  never acknowledged) is exercised only via the interruption tests, which
  take a different, faster exit route (SIGTERM to the group) before that
  internal timeout would ever fire.

## ARCHITECTURE IMPACT

None. This checkpoint touches only research-directory tooling (a shell
script, a procedure document, three test-fake scripts — one new) and one
test file. `BargeInController`, `ConversationSession`, and the
Gemini/Pipecat boundary are all untouched. No local voice baseline
change. No cloud provider change. No LID in any critical path (unrelated
to this checkpoint's scope entirely).

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (bash's own documented `setsid`/process-group/`pgrep` exit-code
semantics — `0` at least one match, `1` no processes matched, `2`
invalid options, `>2` other errors, per the installed `pgrep`'s own
`--help`/manual — and POSIX `setsid()` semantics, applied and verified
empirically this checkpoint via the test suite itself; no new external
research).

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
  — §4 rewritten (8-point description of the corrected wrapper's actual
  behavior, R0063-superseding-R0062 framing preserved as the doc's own
  convention), §5 extended (exit codes `96`/`97` added to the invalidity
  list), §7 rewritten (rollback/safety summary reflecting the handshake,
  persisted cleanup verification, and always-attempted readback). Round 2:
  §4 point 6 extended (two-way handshake), §5's exit-`96` entry extended
  (lock quarantine), §7 extended — targeted edits, not another full
  rewrite. Header status block updated to record this correction pass and
  point at this report plus the `**CORRECTED**` note added to the R0062
  report (see below).
- `docs/reports/R0062_gain_ab_wrapper_deep_review_corrections_20260914.md`
  — corrected in place (identity preserved, not renamed or renumbered):
  a `**CORRECTED**` note added immediately under its own TASK RESULT,
  naming all six findings this report addresses, matching this
  repository's own established convention (every prior checkpoint in this
  thread added such a note to its predecessor).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint, after this
  report, per repo convention).
- This report (`R0063`).

## CURRENT VERIFIED STATE

- Branch `main`. `Array 'PCM',1'` = -20.00dB (raw 40, [on]), `UACDemoV10`
  = -0.94dB (raw 147, both channels, [on]) — unchanged throughout this
  entire checkpoint.
- **Offline correction and validation only — the R0057 gain A/B
  experiment remains NOT EXECUTED.** No approval has been given for it.
  `M2.6B` remains IN PROGRESS.
- **This checkpoint is CLOSED WITH A LOCAL COMMIT** (all three rounds,
  as one coherent, scoped commit) — see GIT STATUS / COMMIT HASHES below
  for the observed post-commit state. Not pushed.

## NEXT RECOMMENDED ACTION

The only remaining action is Andrzej's explicit approval (which may
cover the full A-then-B experiment, conditional on A's own validity per
§5) before running
`run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval`
against real hardware for the first time. Nothing further is pending on
this wrapper checkpoint itself.

## TESTS

**Historical, by round** (each count is what that round's own run
observed at the time — not restated as still current below):
round 1 → 42/42; round 2 → 48/48; **round 3 (final) → 50/50**.

**Current, final state:**
- `tests/test_run_r0057_gain_ab_condition_sh.py` → **50/50 pass**,
  `ACTUAL_UNITTEST_EXIT_CODE=0` (captured directly, not through a `tail`
  pipe) — up from R0062's 24 across all three rounds: round 1 added 18,
  round 2 added 6 (`TestStartupOwnershipHandshake` +1,
  `TestCleanupFailureQuarantine` +1 new class,
  `TestArchiveMappingConsistency` +2, `TestArchiveSourceRemovalFailure`
  +1 new class, `TestFailedRollback` +1), round 3 added 2 more
  (`TestStartupOwnershipHandshake` +2: ack-removal-failure,
  own-pgid-lookup-failure). None of the prior tests deleted — 4 were
  extended/relabeled in place across rounds 2–3 where a finding's own
  fix changed an existing, still-valid scenario's observable log output.
- `ruff check` on all touched/new Python files: clean.
- `bash -n` on the corrected script: clean.
- `git diff --stat -- src/nexa`: empty.
- No leaked owned process after any round's full suite (`ps aux`
  confirmed empty of every fake/probe/wrapper process name).
- Full project suite / probe-bargein regression suites: not re-run this
  checkpoint (no repo gate required it; no production code or probe
  script touched — see WHAT I VERIFIED above for the explicit rationale).
- Documentation-only edits made while closing this checkpoint (this
  report's own closure edits, `CURRENT_STATE.md`, `ROADMAP.md`) were
  **not** re-verified by re-running the suite — no code changed after
  the round-3 50/50 run quoted above.

## GIT STATUS

**Pre-commit state, observed immediately before closing this checkpoint**
(confirmed via `git status --porcelain=v1`, not predicted) — all three
rounds' changes, staged for one coherent, scoped commit:
```
 M docs/CURRENT_STATE.md
 M docs/ROADMAP.md
 M docs/reports/R0062_gain_ab_wrapper_deep_review_corrections_20260914.md
 M docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md
 M docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
 M docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py
 M docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_probe.py
 M tests/test_run_r0057_gain_ab_condition_sh.py
?? docs/reports/R0063_gain_ab_wrapper_external_review_corrections_20260914.md
?? docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_pgrep.py
```
Baseline `HEAD` at the start of this entire checkpoint (all three
rounds): `9900d38` (`main`). Wrapper SHA-256 immediately before commit:
`581f7dc04204a92d43a648cb9a334e0f96226cdd6d88959b9585cba57d0a8643`
(confirmed unchanged since the round-3 50/50 test run — no edit touched
it after that run). Not pushed. No Gemini call. No hardware parameter
changed (confirmed read-only, all three rounds).

## COMMIT HASHES

Recorded in a follow-up "docs: record commit hash" commit, per this
repository's own established convention (see e.g. R0062 →
`148f486`/`9900d38`) — the implementation commit is created first, then
a second, docs-only commit inserts its own hash here.
