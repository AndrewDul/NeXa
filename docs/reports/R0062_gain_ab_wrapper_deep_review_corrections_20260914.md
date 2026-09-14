# R0062 — Gain A/B Wrapper Deep-Review Corrections (Offline, Not Executed)

**Date:** 2026-09-14
**Milestone:** M2.6B.4N follow-up (post-R0061 deep-review checkpoint)
**Status:** **CORRECTION CHECKPOINT — COMPLETE. OFFLINE ONLY.** A second,
deeper review found that R0061's own offline test suite did not
actually prove several of its claims — this checkpoint fixes the
underlying wrapper gaps those tests missed, rewrites the test doubles
and the suite itself so each claim is backed by a test that could
actually fail if the claim were false, and corrects R0061's own report
in place. **This checkpoint authorizes local implementation and offline
validation only. No hardware writes, audio runs, Gemini, production
changes, dependency changes, or push. The R0057 gain A/B experiment
itself remains NOT EXECUTED — no approval of any kind has been given
for it. `M2.6B` remains IN PROGRESS.**

## TASK RESULT

**PASS.**

## PURPOSE

Review instructed: do not accept R0061's PASS claim as proof its
described paths actually work — treat R0061's own tests as suspect
until independently re-examined against the real wrapper script and
fakes, then fix whatever the review's six named defect categories
actually find, correcting the tests and test doubles alongside the
wrapper itself so each requirement is enforced by a test that would
fail on the previous, insufficiently-verified behavior.

## BASELINE

- **Baseline commit:** `d688a94c0f0c6acd09679cb730583ca54c1427de` (`main`, HEAD at the start of this checkpoint) — verified via `git rev-parse HEAD`.
- **Working tree at start:** the wrapper, both fakes, and the test file
  already carried an in-progress, uncommitted rewrite from the same
  bounded task (a prior session of this same review had been
  interrupted mid-implementation by a spend limit). Per instruction,
  this work was inspected and preserved, not restarted — the six
  categories below were verified against the actual current file
  contents, not assumed complete from an unverified prior claim.
- Read the actual current `run_r0057_gain_ab_condition.sh`,
  `test_fakes/fake_amixer.py`, `test_fakes/fake_probe.py`,
  `tests/test_run_r0057_gain_ab_condition_sh.py`, the R0057 procedure
  document, and the R0061 report in full before changing any of them
  further.

## THE SIX REVIEW CATEGORIES AND THEIR FIXES

1. **No mutation after failed precheck.** R0061's own
   `TestPrecheckMismatch` asserted the Array raw value was forced back
   to `40` after a failed precheck — the test EXPECTED a write on the
   failure path and never inspected which `amixer` invocations actually
   occurred, so it could not have caught a script that mutated hardware
   despite a failed precheck. **Fixed:** an explicit
   `MUTATION_ATTEMPTED=0` flag, set to `1` immediately before, and only
   before, the one condition-write `amixer sset` call in the main body.
   `rollback()` checks this flag first: if unset, it prints
   `ROLLBACK_SKIPPED: no mutation was attempted this run -- Array
   'PCM',1 left untouched`, issues no write, and exits successfully;
   if a mutation WAS attempted (even a possibly partial, failed one —
   e.g. the post-write readback itself failed), rollback still runs.
   `TestPrecheckMismatch` renamed to `TestPrecheckFailureCausesZeroWrites`
   and rewritten: every sub-test now asserts the Array raw value STAYS
   at its wrong precheck value and that
   `[inv for inv in invocations if "sset" in inv] == []`, using a new
   `AMIXER_FAKE_INVOCATION_LOG` the fake `amixer` appends every
   invocation to before processing it.
2. **Validate final hardware state.** R0061's rollback tests checked
   only that `Array 'PCM',1'` was restored; nothing independently
   re-read `UACDemoV10` after rollback, and no test forced a wrong or
   unreadable final UAC state to prove the wrapper would notice.
   **Fixed:** `rollback()`'s own final step calls `validate_uac_raw`
   (read-only) against the verified MAX baseline; a failure sets
   `UAC_FINAL_STATUS=1` and prints `UAC_FINAL_CHECK_FAILED: ... never
   writes UACDemoV10 to correct it` — the script never issues a
   corrective UAC write anywhere. `read_array_raw`/`read_uac_raws` now
   also check each control's exact `Simple mixer control '...'`
   identity line, its documented `Limits: Playback 0 - N` range, and
   its `[on]` switch state — not merely a raw integer that happens to
   match — before parsing a value at all, closing the false-positive
   risk of a coincidentally-matching number from the wrong control.
   New `TestFinalUacCheck` (2 tests: read failure and value mismatch,
   each isolated to occur only AFTER an otherwise-successful precheck,
   via new call-counted drift in the fake) plus 2 new
   `TestPrecheckFailureCausesZeroWrites` sub-tests for wrong control
   identity and switch-off.
3. **Terminate owned processes before rollback.** R0061's own
   interruption tests verified process death via an environment-marker
   `pgrep` pattern-match, never the actual PID/PGID the wrapper itself
   started, and never exercised a process that survives SIGTERM — so
   the SIGKILL escalation path R0061's own report described was never
   actually run by any test. **Fixed:** the probe is launched via
   `setsid timeout ... "${PROBE_CMD[@]}" &`, its PGID captured (`ps -o
   pgid= -p "$CHILD_PID"`) and used for everything downstream. A new
   `terminate_process_group(pgid, grace_s)` sends `kill -TERM --
   "-${pgid}"`, polls `pgrep -g "$pgid"` every 0.1s, and escalates to
   `kill -KILL -- "-${pgid}"` only if a member is still alive after the
   grace period, finally confirming empty before returning success —
   called from `on_signal()` before rollback AND, as a safety net,
   after ordinary completion. The test suite's `_parse_pgid`/
   `_pgid_alive` helpers now verify the EXACT recorded PGID is gone,
   and a new fake-probe mode (`FAKE_PROBE_IGNORE_SIGTERM=1` +
   `FAKE_PROBE_SPAWN_CHILD_SLEEP_SECONDS`, with an explicit
   `FAKE_PROBE_READY` synchronization marker) forces and verifies the
   real SIGKILL escalation path in
   `test_sigterm_resistant_descendant_requires_sigkill_escalation`.
4. **Make artifact acceptance match the real experiment.** R0061's own
   `TestDistinctArtifactsAcrossConditions` used DIFFERENT fake filenames
   for condition A vs B, which never exercises the real collision risk
   (the real probe always writes the SAME fixed filenames regardless of
   condition), and no test covered a missing/incomplete artifact set.
   **Fixed:** the fake probe now writes the real probe's own fixed
   names (`{level}_trial{i}_mic.wav`/`_ref.wav`) for every condition,
   folding a distinguishing label into file CONTENT instead of the
   filename. The wrapper's archive step now requires EXACTLY one
   current-run JSON reporting the expected trial count plus every
   expected WAV pair (via an explicit `expected_wav_names` check),
   setting `ARCHIVE_COMPLETE=1` only when all of that holds — otherwise
   exit `93`. `MANIFEST.txt` now also records an explicit mapping from
   each original JSON `cross_correlation.mic_wav`/`ref_wav` path string
   to its archived file and SHA-256, not just a hash list. Whatever
   partial evidence exists IS still archived on an incomplete/failed
   run (never discarded), before hardware-restoration steps, without
   delaying them. `find -newer <marker>` is still used for candidate
   discovery but is no longer treated as ownership proof by itself — a
   `mkdir`-based lock (`.gain_ab_experiment.lock`, exit `94` if held)
   now prevents two invocations from ever sharing capture locations.
   `TestDistinctArtifactsAcrossConditions` rewritten to hash condition
   A's archived bytes both before and after condition B runs and assert
   equality; new `TestIncompleteArchive` (missing WAV pairs; zero
   artifacts) and `TestConcurrencyLock`.
5. **Check operational failures explicitly.** Nothing in R0061 checked
   whether the evidence-storage directories, the run marker, or the
   persisted-log redirection were actually created successfully before
   proceeding. **Fixed:** `mkdir -p` for the required directories and
   `mktemp`/`touch` for the run marker are each checked explicitly,
   aborting with exit `95` (`could not create required
   evidence-storage directories` / marker-creation failure) before the
   concurrency lock, precheck, or any mutation — reachable even this
   early because `trap finalize EXIT` is installed first, so rollback
   (correctly a no-op, `MUTATION_ATTEMPTED` still unset) and archiving
   (correctly a no-op, no marker exists yet) both still run cleanly.
   New `TestStorageFailure` (an unwritable parent directory) asserts
   exit `95`, that the capture directory was never created, and that
   zero `sset` calls occurred (one harmless, read-only `UACDemoV10
   sget` from rollback's own final-state check IS expected even on
   this early path, and is explicitly distinguished from a write in the
   assertion).
6. **Repair the test doubles and claims.** The fake `amixer`'s own UAC
   dB formula reported raw `147` as `0.00dB`; the real device reports
   `-0.94dB` at that raw value (verified this checkpoint via a direct,
   read-only `amixer -c UACDemoV10 sget PCM` against the actual
   hardware). **Fixed** with a linear interpolation between the real
   device's own verified `dBminmax` endpoints
   (`-28.37dB` at raw `0`, `-0.94dB` at raw `147`). The fake already
   rejected unsupported invocations (wrong verb, `UACDemoV10 sset`,
   unknown card) with no catch-all success path — R0061's report
   described this correctly but had no dedicated test; added
   `TestFakeAmixerRejectsUnsupportedInvocations` (3 tests) invoking the
   fake directly. R0061's report is corrected in place (see below) for
   its unsupported "13/13 pass proves X" claims where the cited test
   did not actually exercise X. The procedure document (§5) now states
   explicitly that a measured-trial lifecycle-timeout warning remains
   UNRESOLVED evidence until investigated and must not silently qualify
   condition A's trials as good enough to proceed to condition B under
   a single combined approval; a confirmed self-barge-in during a
   measured trial remains, unchanged, the OUTCOME being measured, never
   an invalidity reason, and execution success (exit `0`) is now stated
   explicitly as a precondition for trusting a run's data, not proof of
   its scientific validity.

## EXIT-CODE CONVENTION (extended this checkpoint; fully documented in the script's own header)

| Code | Meaning |
|---|---|
| `0` | Fully successful: precheck OK, condition set+verified, probe exited 0, rollback (if a mutation was attempted) verified, final UAC state verified, archive exactly complete. |
| `2` | Usage/argument error — no hardware touched. |
| `90` | Precheck failed (mismatched/unreadable/misidentified mixer state, or switch off) — aborted before any mutation; zero `amixer sset` calls issued. |
| `92` | The condition's own mixer write or its post-write readback verification failed (a possibly-partial write) — rollback still attempted. |
| `93` | Probe exited 0 but this run's own artifact archive is not EXACTLY complete (missing JSON, missing WAV pair, wrong trial count, or zero artifacts). |
| `91` | Rollback itself did not verify, OR the independent final-UAC-state check failed — overrides an otherwise-successful `0`. |
| `94` | A concurrent invocation already holds the capture-location lock — this invocation never proceeded. |
| `95` | Required evidence-storage setup (directories, run marker) could not be created — aborted before the lock, precheck, or any mutation. |
| `130`/`143` | Interrupted by SIGINT/SIGTERM — owned process group confirmed terminated before rollback. |
| *(probe's own code)* | Otherwise preserved as-is (e.g. `1`, `124` for an external-timeout kill). |

Every one of these is exercised by the offline test suite (below).

## OFFLINE TEST INFRASTRUCTURE (rewritten this checkpoint)

- `test_fakes/fake_amixer.py` — corrected UAC dB formula (linear
  interpolation matching real hardware); `AMIXER_FAKE_INVOCATION_LOG`
  (every invocation recorded before processing, for zero-write
  assertions); `array_wrong_identity`/`uac_wrong_identity`/
  `array_switch_off`/`uac_switch_off` injectable state; call-counted
  UAC drift (`uac_fail_after_first_read`, `uac_mismatch_after_first_read`
  + `uac_mismatch_raw_r`) isolating "wrong at precheck" from "wrong only
  at the wrapper's own later final check," with the mismatched value
  never actually persisted (proving "detected wrong" is distinct from
  "silently accepted/fixed").
- `test_fakes/fake_probe.py` — fixed, real-probe-matching filenames for
  every condition (label moved into file content); JSON `trials`
  entries carry the actual WAV path strings; `FAKE_PROBE_IGNORE_SIGTERM`
  + `FAKE_PROBE_SPAWN_CHILD_SLEEP_SECONDS` for the SIGKILL-escalation
  scenario; explicit `FAKE_PROBE_READY` synchronization marker.
- `tests/test_run_r0057_gain_ab_condition_sh.py` — **24 tests** (up
  from R0061's 13), each still constructing an isolated temp `PATH`
  (fake `amixer` first) and an isolated temp capture-root, invoking the
  REAL script via `subprocess.run`/`Popen`. The real `amixer` binary is
  still never invoked by any test (`TestNeverInvokesRealAmixer`
  unchanged).

### Finding-to-test table

| Review finding (category) | Test(s) | Result |
|---|---|---|
| 1. Zero writes on failed precheck | `TestPrecheckFailureCausesZeroWrites` (5: wrong Array baseline, wrong UAC channel, unreadable Array, wrong control identity, switch off) | exit 90, Array raw value unchanged, zero `sset` invocations recorded |
| 2. Final hardware-state validation (Array + UAC), never a repair-write | `TestFinalUacCheck` (2: read failure, value mismatch after first read) | exit 91, `UAC_FINAL_CHECK_FAILED` logged, zero `UACDemoV10 sset` calls ever, underlying fake state unchanged (mismatch was reporting-only) |
| 3. Owned-process-group termination, verified, with SIGKILL escalation | `TestInterruption` (3: SIGINT→130, SIGTERM→143, SIGTERM-resistant descendant→SIGKILL escalation) | exact recorded PGID confirmed empty afterward via `pgrep -g`; escalation test shows `PROCESS_GROUP_STILL_ALIVE_AFTER_TERM` then `PROCESS_GROUP_TERMINATED`, 5.887s runtime consistent with the real 5s grace period being exercised |
| 4. Exact archive completeness; A survives B; explicit JSON→WAV mapping; concurrency lock | `TestDistinctArtifactsAcrossConditions`, `TestIncompleteArchive` (2), `TestConcurrencyLock` | A's archived bytes/hashes byte-identical before/after B runs; missing/zero-artifact runs give exit 93 with partial evidence still archived; second concurrent invocation refused with exit 94, lock released after the first completes |
| 5. Operational (storage/marker) failures checked explicitly | `TestStorageFailure` | exit 95, capture directory never created, zero `sset` calls (one harmless read-only UAC check permitted) |
| 6. Fake amixer UAC dB formula, unsupported-invocation rejection, existing-behavior coverage | `TestFakeAmixerRejectsUnsupportedInvocations` (3); dB formula change verified via `uac_block`'s own docstring math and the corrected hardware reading below | all 3 rejected (exit 1); real hardware confirms raw 147 = -0.94dB, matching the corrected fake |
| (carried over, unchanged) Normal completion, nonzero probe status, supervisor timeout, failed set/readback, failed rollback | `TestNormalCompletion`, `TestNonzeroProbeStatus`, `TestSupervisorTimeout`, `TestFailedSetReadback`, `TestFailedRollback` | all still pass against the corrected wrapper |

## WHAT I VERIFIED

```
bash -n docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
```
→ clean.

```
.venv/bin/python -m unittest tests.test_run_r0057_gain_ab_condition_sh -v
```
→ **24 tests, OK** (real wall time ~19.3s — the SIGKILL-escalation test
alone takes ~5.9s, genuinely exercising the wrapper's own 5-second grace
period rather than a shortened stand-in).

```
.venv/bin/ruff check tests/test_run_r0057_gain_ab_condition_sh.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_probe.py
```
→ all checks passed.

`git diff --stat -- src/nexa`: **empty**.

Real hardware mixer verified unchanged (read-only) this checkpoint:
```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```
(This same read also supplied the corrected fake UAC dB formula's
verified endpoint, `-0.94dB` at raw `147`.)

The probe/bargein regression suites
(`tests.test_m2_6b4m_self_echo_probe`, `tests.test_bargein_m2_5b`) were
**not re-run this checkpoint** — no production code under `src/nexa`
was touched (confirmed by the empty `git diff --stat -- src/nexa`
above), so re-running 112 unrelated tests would not have added
evidence; this is a deliberate, stated skip per instruction not to
repeat already-sufficient checks without a concrete reason, not an
oversight.

## PROCEDURE DOCUMENT ALIGNED (`R0057_gain_ab_experiment_procedure.md`)

- §4 rewritten: describes the mutation-tracked precheck, the
  dual-sided (Array + UAC) independent final-state validation with a
  never-repair guarantee, owned-process-group termination with verified
  SIGKILL escalation, the concurrency lock, exact archive-completeness
  requirements with the explicit JSON→WAV hash mapping, and the new
  `94`/`95` exit codes.
- §5 extended: the `90`–`95` exit-code set is now named explicitly as
  invalidating; a new opening statement distinguishes wrapper execution
  success (a precondition) from scientific run validity (a separate,
  still-required judgment); the lifecycle-timeout criterion is
  strengthened to state it remains UNRESOLVED evidence that must not
  silently qualify condition A's trials as sufficient to proceed to
  condition B.
- §7 updated to reflect mutation-gated rollback and process-group
  (not single-PID) termination.
- §1–§3, §6 (comparison methodology, self-barge-in-as-outcome,
  interpretation limits) unchanged in substance from R0060/R0061.

## CORRECTIONS TO THE R0061 REPORT (unsupported claims found and fixed)

Added a `**CORRECTED**` section immediately under R0061's own TASK
RESULT, identity preserved (not renamed/renumbered), listing six
specific claims R0061's own 13-test suite did not actually substantiate
(precheck zero-writes, UAC-side rollback validation, exact-PGID/SIGKILL
verification, real filename-collision testing, missing-evidence/
storage-failure/concurrency coverage, and the fake's own UAC dB
formula) — each cross-referenced to the fix and new test in this
report. R0061's account of its own seven original defects and fixes is
left unchanged; only the sufficiency of the tests cited as proof for
several of them is corrected.

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh`
  — corrected per the six review categories above (still not
  executable against real hardware without explicit approval; still
  never executed).
- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
  — §4/§5/§7 aligned with the corrected script.
- `docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py`,
  `fake_probe.py` — rewritten test doubles (see above).
- `tests/test_run_r0057_gain_ab_condition_sh.py` — rewritten, 24 tests
  (11 new: `TestFinalUacCheck` ×2, `TestIncompleteArchive` ×2,
  `TestStorageFailure` ×1, `TestConcurrencyLock` ×1,
  `TestFakeAmixerRejectsUnsupportedInvocations` ×3, plus 2 new
  `TestPrecheckFailureCausesZeroWrites` sub-tests for control identity
  and switch-off).
- `docs/reports/R0061_gain_ab_wrapper_corrections_20260913.md` —
  corrected in place (unsupported-claims note); identity preserved, not
  renamed or renumbered.
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated after this
  report per convention.
- This report (`R0062`).

**No `src/nexa/**` file touched.** **No hardware parameter written** —
every `amixer` command actually reaching real hardware this checkpoint
was a bare, read-only `sget`, run directly to confirm state and to
source the corrected fake's own dB formula, never through the wrapper
script (which only ever ran against the fakes).

## VERIFIED FACTS

1. All six review-confirmed defect categories from this checkpoint's
   own deep review are fixed in the wrapper script, each with a
   corresponding, passing offline test that would fail against the
   PREVIOUS (R0061) behavior.
2. `tests/test_run_r0057_gain_ab_condition_sh.py` → 24/24 pass in a
   single full-suite run (not merely individually).
3. `git diff --stat -- src/nexa` is empty; the probe's own production
   code is unmodified.
4. Real hardware mixer state (`Array PCM,1` = -20.00dB/raw 40/[on],
   `UACDemoV10` = -0.94dB/raw 147/[on], both channels) is unchanged,
   verified read-only, this checkpoint.
5. `ruff check` on all touched Python files: clean. `bash -n` on the
   corrected wrapper: clean.
6. R0061's report now carries an explicit, in-place correction naming
   exactly which of its claims were not substantiated by its own test
   suite.

## HYPOTHESES

None outstanding blocking review.

## UNRESOLVED

- The corrected wrapper has never been run against real hardware or the
  real probe — this checkpoint is offline-only, by instruction. Its
  first real-hardware exercise will be condition A itself, if and when
  approved.
- The R0057 gain A/B experiment remains **NOT EXECUTED** — no approval
  of any kind has been given.
- A measured-trial `_ResponseLifecycle` lifecycle-timeout warning, if
  one ever occurs on real hardware, remains unresolved evidence
  requiring investigation before use — this checkpoint only strengthens
  the procedure document's own language on this point; it does not and
  cannot resolve what a real occurrence would mean, since none has
  happened.
- `AecReferenceFeeder.chunks_dropped`/`respawns`/
  `AecReferenceHealth.failure_count` remain unexercised against a real
  nonzero value on hardware (unchanged from R0060/R0061).

## ARCHITECTURE IMPACT

None. This checkpoint touches only research-directory tooling (a shell
script, a procedure document, two test-fake scripts) and one test file.
`BargeInController`, `ConversationSession`, and the Gemini/Pipecat
boundary are all untouched.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (bash's own documented process-group/`setsid`/`kill -g`/`pgrep -g`
semantics and POSIX signal-disposition inheritance across fork/exec,
verified empirically this checkpoint via direct experimentation, not
external research).

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
  (§4/§5/§7 corrected).
- `docs/reports/R0061_gain_ab_wrapper_corrections_20260913.md`
  (corrected in place — identity unchanged).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint).
- This report (`R0062`). `R0057`–`R0061` identities are all unchanged
  — none renamed or renumbered.

## CURRENT VERIFIED STATE

- Branch `main`. `Array 'PCM',1'` = -20.00dB (raw 40, [on]),
  `UACDemoV10` = -0.94dB (raw 147, both channels, [on]) — unchanged
  throughout this entire checkpoint.
- **Offline correction and validation only — the R0057 gain A/B
  experiment remains NOT EXECUTED.** No approval has been given for it.
  `M2.6B` remains IN PROGRESS.
- Post-commit git status: see GIT STATUS below (observed, not
  predicted).

## NEXT RECOMMENDED ACTION

Obtain Andrzej's explicit approval (which may cover the full A-then-B
experiment, conditional on A's own validity per §5) before running
`run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval`
against real hardware for the first time.

## TESTS

- `tests/test_run_r0057_gain_ab_condition_sh.py` → **24/24 pass** (up
  from R0061's 13; 11 new tests, all others rewritten or extended, not
  a superficial extension of the count).
- `ruff check` on all touched Python files: clean.
- `bash -n` on the corrected script: clean.
- `git diff --stat -- src/nexa`: empty.
- Full project suite / probe-bargein regression suites: not re-run this
  checkpoint (no repo gate required it; no production code touched —
  see WHAT I VERIFIED above for the explicit rationale).

## GIT STATUS

See COMMIT HASHES below and the actual `git status --short` output
captured at completion of this checkpoint (observed, not predicted, per
instruction).

## COMMIT HASHES

(recorded in a follow-up "docs: record commit hash" commit per this
thread's established convention)
