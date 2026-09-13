# R0061 — Gain A/B Wrapper Corrections (Offline, Not Executed)

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (post-R0060 review checkpoint)
**Status:** **CORRECTION CHECKPOINT — COMPLETE. OFFLINE ONLY.** Fixed
seven confirmed defects in the R0060 gain A/B wrapper script, verified
every fix with a new, hardware-free offline test suite that shadows
`amixer` and the probe with controllable fakes, and corrected two
factual errors in the R0060 report. **This checkpoint authorizes local
implementation and offline validation only. No hardware writes, audio
runs, Gemini, production changes, dependency changes, or push. The
R0057 gain A/B experiment itself remains NOT EXECUTED — no approval of
any kind has been given for it. `M2.6B` remains IN PROGRESS.**

## TASK RESULT

**PASS.**

## PURPOSE

Fix the seven confirmed defects review found in
`run_r0057_gain_ab_condition.sh` (R0060) before it is ever offered for
real execution, verify every fix with genuine, hardware-free tests
(never invoking real `amixer`), align the procedure document's own
validity criteria with the corrected script and with R0060's own
telemetry fix, and correct two factual errors in the R0060 report
itself (a test-count miscount; an unsupported claim about R0059's own
feeder-drop count).

## BASELINE

- **Baseline commit:** `211b4ae26997226243692f4028134959eed2fd5e` (`main`, HEAD at the start of this checkpoint) — verified via `git rev-parse HEAD`, matching the supplied transcript (`e8858d6` then `211b4ae`).
- **Working tree at start:** confirmed clean.
- Read the actual current `run_r0057_gain_ab_condition.sh` and
  `R0057_gain_ab_experiment_procedure.md` in full before changing
  either.

## THE SEVEN CONFIRMED DEFECTS AND THEIR FIXES

1. **`set -e` swallowed the probe's own exit status.** `set -euo
   pipefail` was active; `timeout ... > "$LOG" 2>&1` was a plain
   command whose nonzero exit triggers `set -e` IMMEDIATELY, aborting
   the script (running the EXIT trap) BEFORE the next line
   (`EXIT_CODE=$?`) ever executed — the R0060 script's own EXIT_CODE
   bookkeeping and its "INVALID RUN" check were unreachable on any real
   probe failure. **Fixed** by removing `-e` entirely (kept `-u` and
   `pipefail`) and checking every command that can legitimately fail
   via an explicit `if`/`&&`, since several OTHER new command
   substitutions added this checkpoint (`raws="$(...)"`) have the exact
   same hazard if `-e` were re-enabled selectively — a single
   consistent rule (no errexit anywhere, every intentional failure
   checked explicitly) is simpler to verify correct than bracketing
   `-e` on and off around individual commands.
2. **Mixer readbacks were printed but never validated.** The R0060
   precheck/set/rollback steps all called `amixer sget`/`sset` and
   printed the output for a human to eyeball — nothing in the script
   itself compared the reading to an expected value. **Fixed** with
   `extract_playback_raws`/`read_array_raw`/`read_uac_raws`/
   `validate_array_raw`/`validate_uac_raw`: every mixer read the script
   uses for a DECISION is now parsed and compared against an expected
   value, aborting before mutation on mismatch or an unreadable value
   (exit `90`), and the post-set readback is validated the same way
   (exit `92` on mismatch).
3. **`EXPECTED_UAC_RAW` was declared but never used.** Renamed to
   `UAC_EXPECTED_RAW` and it is now the actual expected value
   `validate_uac_raw` checks against — no longer dead code.
4. **The persisted log excluded prechecks, condition writes, and
   rollback.** Only the probe's own redirected stdout/stderr reached
   `$LOG`; every wrapper-side `echo` before/after went to the terminal
   only and was lost once the shell exited. **Fixed** with
   `exec > >(tee -a "$LOG") 2>&1` near the top of the script, so every
   subsequent line this script OR the probe produces reaches the same
   persisted file, duplicated to the terminal too.
5. **Condition B overwrote condition A's own fixed-name PCM files.**
   `_save_pcm_and_correlate`'s own file names
   (`{level}_trial{index}_mic.wav`/`_ref.wav`) do not vary by run or
   condition — never changed here, per instruction not to redesign the
   probe. **Fixed at the wrapper level**: a marker file is `touch`ed
   immediately before the probe runs; afterward, `find ... -newer
   "$MARKER"` inventories exactly the JSON/WAV files THIS run created
   (never "newest by mtime", which could pick up a stale file left by
   an earlier, unrelated run) and moves them (copy, hash-verify, then
   remove the original) into a fresh, timestamped, per-condition
   archive directory with a `MANIFEST.txt` (SHA-256 per file, and an
   explicit statement that the JSON and WAVs in that one directory came
   from the same single invocation) — so the SHARED, fixed-name
   location is empty again before the next condition can ever run.
6. **Signal handling did not explicitly preserve interruption status or
   coordinate child termination before rollback.** The R0060 script
   handled INT/TERM via the SAME `rollback` function as normal EXIT,
   with no distinct interruption status and no explicit step to stop
   the actual child process first. **Fixed** with `on_signal()`,
   installed separately for INT and TERM: it records a distinct
   `RUN_STATUS_LABEL`, sends SIGTERM (then, after a bounded grace
   period, SIGKILL) to `$CHILD_PID` specifically (the `timeout`
   process this script itself started — never a broad process-kill)
   and `wait`s for it, THEN exits with the conventional 128+signum
   code (`130`/`143`), which in turn triggers the SAME `finalize`/
   `rollback` path exactly once (idempotency guard: `INTERRUPTED`/
   `FINALIZED`/`ROLLBACK_DONE` flags each checked before doing
   anything). The script never chains into a second condition under
   any circumstance — each invocation only ever knows about the ONE
   condition it was given.
7. **Comments incorrectly promised rollback on wrapper SIGKILL.** The
   R0060 script's own comments implied the trap-based rollback was an
   unconditional guarantee. **Corrected**: the script's header and
   `finalize()`'s own comment now state explicitly that a SIGKILL sent
   to this wrapper, or a host power loss, cannot be intercepted by any
   shell trap, and that no unconditional guarantee is or can be made
   for those cases — an operator in that situation must manually verify
   and, if needed, restore the baseline.

## EXIT-CODE CONVENTION (new, fully documented in the script's own header)

| Code | Meaning |
|---|---|
| `0` | Fully successful: precheck OK, condition set+verified, probe exited 0, rollback verified, artifacts archived. |
| `2` | Usage/argument error — no hardware touched. |
| `90` | Precheck failed (mismatched or unreadable mixer state) — aborted before any mutation. |
| `92` | The condition's own mixer write or its post-write readback verification failed (a possibly-partial write). |
| `93` | Probe exited 0 but this run's own artifact archive did not complete cleanly. |
| `91` | Rollback itself did not verify — overrides an otherwise-successful `0`. |
| `130`/`143` | Interrupted by SIGINT/SIGTERM. |
| *(probe's own code)* | Otherwise preserved as-is (e.g. `1`, `124` for an external-timeout kill). |

Every one of these is exercised by the new offline test suite (below).

## OFFLINE TEST INFRASTRUCTURE (new)

- `docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py` —
  a stateful fake `amixer`, state persisted in a JSON file named by
  `AMIXER_FAKE_STATE`, supporting exactly the three invocation shapes
  the wrapper uses (`-c Array sget/sset 'PCM',1[, value]`, `-c
  UACDemoV10 sget PCM`) plus injectable failure switches
  (`fail_array_sget`, `fail_array_sset`, `fail_uac_sget`,
  `array_sset_no_op_raw` — a write that "succeeds" but silently does
  not change the stored value, for testing readback verification
  specifically).
- `docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_probe.py` —
  a controllable stand-in probe (`FAKE_PROBE_EXIT_CODE`,
  `FAKE_PROBE_SLEEP_SECONDS`, `FAKE_PROBE_WRITE_ARTIFACTS`,
  `FAKE_PROBE_LABEL`), invoked in place of the real probe via
  `R0057_AB_PROBE_LAUNCHER`.
- New wrapper environment overrides, `R0057_AB_CAPTURE_ROOT`,
  `R0057_AB_PROBE_LAUNCHER`, `R0057_AB_TIMEOUT_BOUND_S`,
  `R0057_AB_TIMEOUT_KILL_AFTER_S` — all documented in the script's own
  header as test-only, never needed or set for a real operator run
  (every default is the real path/value).
- `tests/test_run_r0057_gain_ab_condition_sh.py` — 13 tests, each
  constructing an isolated temp `PATH` (fake `amixer` first) and an
  isolated temp capture-root, invoking the REAL script via
  `subprocess.run`/`Popen`. **The real `amixer` binary is never
  invoked by any test** — confirmed both structurally (the fake
  directory is prepended to `PATH` in every test) and by a dedicated
  test (`TestNeverInvokesRealAmixer`) that asserts `command -v amixer`
  resolves to the fake, not the system binary.

### Coverage (every scenario the checkpoint asked for)

| Scenario | Test | Result |
|---|---|---|
| Normal completion | `TestNormalCompletion` | exit 0, mixer restored, exactly 1 archived JSON + 2 WAVs, MANIFEST hashes present, shared paths empty afterward |
| Nonzero probe status | `TestNonzeroProbeStatus` | wrapper preserves the probe's own code (17), rollback still verified |
| Supervisor timeout | `TestSupervisorTimeout` | external `timeout` kills a 30s-sleeping fake probe under a 1s/1s bound, wrapper preserves 124, rollback verified |
| Precheck mismatch | `TestPrecheckMismatch` (3 tests: wrong Array baseline, wrong UAC channel, unreadable Array) | exit 90, no `SET_CONDITION_*` line ever appears (no mutation attempted), safety rollback still runs |
| Failed set/readback | `TestFailedSetReadback` | a write that "succeeds" but doesn't take effect is caught by the post-write readback, exit 92, rollback still verified |
| Failed rollback | `TestFailedRollback` | isolates a rollback-only failure (condition write succeeds normally; only the LATER restore-to-baseline write is made ineffective) — exit 91, mixer left at the condition's own value, log states this plainly |
| INT/TERM | `TestInterruption` (3 tests) | SIGINT → 130, SIGTERM → 143, both roll back and verify; a THIRD test confirms the fake probe's own OS process is actually gone afterward (`pgrep` on a unique per-test marker), not merely that the wrapper exited |
| Distinct A/B artifacts | `TestDistinctArtifactsAcrossConditions` | runs A then B against the SAME shared capture root; both archived, contents distinguishable by their own JSON `label`, WAV filename sets disjoint, shared fixed-name paths empty after both |

## WHAT I VERIFIED

```
bash -n docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
```
→ clean.

```
bash docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh
bash docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh A
bash docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh X --i-have-explicit-operator-approval
```
→ all three exit `2` with usage text; mixer confirmed unchanged
(`-20.00dB`/`-0.94dB`) after each, via a real, read-only `amixer sget`
run directly in this session (not through the fakes) both before and
after.

```
.venv/bin/python -m unittest tests.test_run_r0057_gain_ab_condition_sh -v
```
→ **13 tests, OK** (real wall time ~4.5s — the timeout/interruption
tests use small, overridden bounds specifically so this suite stays
fast without needing real hardware timing).

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe tests.test_bargein_m2_5b -q
```
→ **112 tests, OK** (unchanged — this checkpoint touches no probe
production code).

```
.venv/bin/ruff check tests/test_run_r0057_gain_ab_condition_sh.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py \
  docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_probe.py
```
→ all checks passed.

`git diff --stat -- src/nexa`: **empty**, confirmed against `211b4ae`.

Real hardware mixer verified unchanged (read-only) before and after
every check this checkpoint performed:
```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```
No leftover `fake_probe`/`fake_amixer` process after the test suite ran
(checked directly via `ps aux`).

## PROCEDURE DOCUMENT ALIGNED (§7 of the task, R0057_gain_ab_experiment_procedure.md)

- §5's invalid-run criteria now cover reference telemetry for the
  WARM-UP window AND every measured trial separately (previously
  warm-up-only), explicitly add `respawns_delta > 0` and an inactive/
  never-started reference feed as invalidating, and state plainly that
  a missing/`None` telemetry field is UNKNOWN, never assumed zero.
- Added a lifecycle-timeout validity FLAG (the probe's own existing
  `_ResponseLifecycle never fired 'finished'` warning for a measured
  trial) — investigate, not automatic invalidation.
- Re-stated, unchanged in substance, that a confirmed self-barge-in
  during a measured trial remains the OUTCOME being measured, never an
  invalidity reason — and clarified it is independent of, and can
  coexist with, a telemetry-based invalidity finding in the SAME trial.
- §6 now explicitly instructs: do NOT use the probe's own built-in
  `cross_correlation` field as evidence of low/absent echo (R0055's own
  documented search-misalignment makes a saturated reading an artifact,
  not a measurement) — the archived, hashed PCM exists specifically for
  correct, offset-aware OFFLINE re-analysis instead.
- §4 documents the corrected wrapper's actual exit-code convention,
  archive behavior, and the explicit SIGKILL/power-loss limitation.
- Approval framing (top of document): a single approval MAY cover
  A-then-B, conditional on A's own validity — this document does not
  itself impose two separate per-condition approvals, and no approval
  of any kind has been given as of this checkpoint.

## CORRECTIONS TO THE R0060 REPORT (factual errors found and fixed)

1. **Test count.** R0060's own report claimed `TestAecReferenceTelemetry`
   had "7 tests, pure/offline, no asyncio." Counting the actual `def
   test_` methods in the class (not re-asserting the original number)
   shows **6**. Corrected in three places in the R0060 report
   (`docs/reports/R0060_..._20260913.md`) with an explicit
   `**CORRECTED**` marker, and in `docs/CURRENT_STATE.md`'s own mirrored
   text. The overall total, "68 tests, OK (60 prior + 8 new)," was
   already arithmetically correct (6 + 2 integration tests = 8) and is
   unchanged.
2. **R0059 feeder-drop claim.** R0060's own "UNRESOLVED" section
   claimed real-hardware drop/failure telemetry showed "none occurred
   in R0059's own clean run." This is not a fact the report could
   state: R0059's own run predates R0060's telemetry entirely, so
   `chunks_dropped`/`failure_count` were never read or recorded during
   it — their value during that run is **UNKNOWN**, not zero. R0059's
   own report and evidence manifest already correctly said "not yet
   read by any instrumentation"; R0060's text should have matched that
   instead of implying a clean reading that was never taken. Corrected
   in place with an explicit `**CORRECTED**` marker.

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh`
  — rewritten per the seven corrections above (still not executable,
  still never executed).
- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
  — §4/§5/§6/§7 aligned with the corrected script and R0060's own
  telemetry fix; approval-scope framing clarified at the top.
- `docs/research/m2_6_cloud_realtime_voice/test_fakes/fake_amixer.py`,
  `fake_probe.py` (new) — offline test doubles.
- `tests/test_run_r0057_gain_ab_condition_sh.py` (new) — 13 tests.
- `docs/reports/R0060_reference_observability_and_gain_ab_procedure_preparation_20260913.md`
  — corrected in place (test count, R0059 drop-count claim); identity
  preserved, not renamed or renumbered.
- `docs/CURRENT_STATE.md` — corrected test-count mirror; updated after
  this report per convention.
- This report (`R0061`).

**No `src/nexa/**` file touched.** **No hardware parameter written** —
every `amixer` command actually reaching real hardware this checkpoint
was a bare, read-only `sget`, run directly by me to confirm state
before/after, never through the wrapper script (which only ever ran
against the fakes, or exited at its own usage guard before touching
anything).

## VERIFIED FACTS

1. All seven review-confirmed defects are fixed in the wrapper script,
   each with a corresponding, passing offline test.
2. The wrapper's own guard path (missing/wrong arguments) still exits
   before any hardware interaction, reconfirmed this checkpoint.
3. `git diff --stat -- src/nexa` is empty; the probe's own production
   code is unmodified.
4. Real hardware mixer state (`Array PCM,1` = -20.00dB, `UACDemoV10` =
   -0.94dB) is unchanged, verified read-only, throughout this entire
   checkpoint.
5. `TestAecReferenceTelemetry` has exactly 6 test methods (not 7);
   `chunks_dropped`/`failure_count` were never read during R0059's own
   run (their value then is unknown, not zero) — both now correctly
   stated in the R0060 report.

## HYPOTHESES

None outstanding blocking review. Whether the SIGKILL/power-loss
limitation ever matters in practice depends on operator behavior during
a future real execution, not on anything testable offline.

## UNRESOLVED

- The corrected wrapper has never been run against real hardware or the
  real probe — this checkpoint is offline-only, by instruction. Its
  first real-hardware exercise will be condition A itself, if and when
  approved.
- The R0057 gain A/B experiment remains **NOT EXECUTED** — no approval
  of any kind has been given.
- `AecReferenceFeeder.chunks_dropped`/`respawns`/
  `AecReferenceHealth.failure_count` remain unexercised against a real
  nonzero value on hardware (unchanged from R0060's own note, now
  correctly stated as "unknown for R0059," not "confirmed clean").

## ARCHITECTURE IMPACT

None. This checkpoint touches only research-directory tooling (a shell
script, a procedure document, two test-fake scripts) and one new test
file. `BargeInController`, `ConversationSession`, and the Gemini/Pipecat
boundary are all untouched.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (bash's own documented `set -e`/trap/signal semantics, already
understood and applied; no new external research).

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
  (rewritten, §4/§5/§6/§7).
- `docs/reports/R0060_reference_observability_and_gain_ab_procedure_preparation_20260913.md`
  (corrected in place — identity unchanged).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint).
- This report (`R0061`). `R0057`, `R0058`, `R0059`, `R0060` identities
  are all unchanged — none renamed or renumbered.

## CURRENT VERIFIED STATE

- Branch `main`. `Array 'PCM',1'` = -20.00dB, `UACDemoV10` = -0.94dB —
  unchanged throughout this entire checkpoint.
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

- `tests/test_run_r0057_gain_ab_condition_sh.py` → **13/13 pass** (new).
- `tests/test_m2_6b4m_self_echo_probe.py` + `tests/test_bargein_m2_5b.py`
  → **112/112 pass** (unchanged).
- `ruff check` on all touched Python files: clean.
- `bash -n` on the corrected script: clean.
- `git diff --check`: clean.
- `git diff --stat -- src/nexa`: empty.
- Full project suite: not re-run this checkpoint (no repo gate required
  it for this offline, probe-production-code-untouched change).

## GIT STATUS

Working tree **CONFIRMED clean** — `git status --short` returned empty
output immediately after the commit below. Not pushed. No Gemini call.
No hardware parameter changed.

## COMMIT HASHES

- `652a333` — fix: R0061 gain A/B wrapper corrections, offline only (M2.6B.4N follow-up)
