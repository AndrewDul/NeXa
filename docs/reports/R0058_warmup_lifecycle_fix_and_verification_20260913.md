# R0058 — Warm-up Lifecycle Fix and Verification

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (implements R0057's own proposed fix)
**Status:** **BOUNDED FUNCTIONAL FIX — IMPLEMENTED AND VERIFIED.** Both
fixes R0057 proposed (warm-up completion evidence; runner
ownership/cleanup) are implemented, covered by new deterministic tests,
and verified by exactly one real-hardware reproduction that now
completes naturally (exit code 0) instead of stalling. **This is not
the R0057 gain A/B experiment** (`Array PCM,1` −20dB vs 0dB) — that
experiment remains **NOT EXECUTED**, explicitly deferred again, now
that its own blocking prerequisite is resolved. **No `src/nexa/**`
change. No hardware parameter written (`Array PCM,1` still exactly
−20.00dB, `UACDemoV10` still exactly −0.94dB, read-only before and
after). No Gemini. No TV tests. No dependency change. Not pushed.
`M2.6B` remains IN PROGRESS.**

## TASK RESULT

**PASS.**

## PURPOSE

Implement and validate the bounded probe-lifecycle fix R0057's own
"SMALLEST PROPOSED FUNCTIONAL FIX" section described but explicitly did
not implement: (1) extend `_run_warmup`'s own bounded-wait completion
condition to also require the final repeat's playback-stop to be
observed, not just reference-byte acceptance; (2) bring the warm-up
call, its validation, and the measured trial loop under the SAME
cleanup guarantee the trial loop alone previously had, so a future
failure anywhere in that sequence can never again orphan `run_task` the
way R0057's own real-hardware reproduction (`run3`) demonstrated.

## BASELINE

- **Baseline commit:** `1e38c55dac8bac1a8f3fcf857ccbedaf3ad05cc` (`main`, HEAD at the start of this checkpoint).
- **Working tree at start:** confirmed clean (`git status --short` empty) — verified before any edit, not assumed.
- **Pre-existing uncommitted work to preserve:** none found.

## WHAT I DID

1. Re-read `AGENTS.md`, the current `R0057` report, the probe script,
   and its test file in full before changing anything.
2. Inspected `_run_warmup`'s own lifecycle-reuse and event-ordering
   assumptions (whether completion must be awaited per-repeat or once
   after the final repeat) directly from source — documented the
   reasoning in the function's own docstring (Correction 6) rather than
   assuming it.
3. Fixed **Bug 1 (warm-up completion):** extended `_run_warmup`'s
   bounded wait to require `playback_stop_count`'s cumulative delta to
   reach `repeats_run`, in addition to the existing `ref_accepted_bytes`
   condition — using baseline-relative deltas throughout (never
   absolute totals), and EXACT equality (`==`, not `>=`) on
   `playback_start_count`/`playback_stop_count`/`repeats_run` so an
   extra, unexplained event is caught exactly like a missing one.
   `_run_warmup` now RAISES a new `WarmupIncompleteError` (carrying the
   full expected-vs-observed evidence as `.context`) instead of ever
   returning a dict describing an incomplete or contradictory warm-up —
   the three checks that used to live as bare `assert` statements in
   `_run()` (silently stripped entirely under `python -O`, unlike a
   raised exception) are gone; a successful return from `_run_warmup`
   is now, by construction, already fully validated evidence.
4. Fixed **Bug 2 (runner ownership / cleanup):** introduced
   `_shutdown_runner()` (the ONE place the probe asks
   `WorkerRunner.end()` — its own public, documented API — to shut
   down, plus a bounded, non-cancelling peek at `run_task`'s status,
   explicit escalation, and a second bounded peek) and
   `_run_body_with_guaranteed_cleanup()` (pure control flow: runs a
   `body()` coroutine, guarantees `cleanup()` is attempted exactly once
   regardless of outcome, never lets a cleanup failure mask an original
   `body()` failure, and promotes a failed cleanup into a raised
   `RunnerShutdownError` when `body()` itself succeeded). `_run()` now
   wraps EVERYTHING from just after `run_task` creation — the warm-up
   sleep/startup prints, the warm-up call, and the measured trial loop —
   inside this guaranteed-cleanup body, not just the trial loop as
   before.
5. **Confirmed and fixed a real bug in my own first draft of
   `_shutdown_runner`**, via a standalone reproduction script before
   trusting the design (see CORRECTED UNDERSTANING OF `asyncio.wait_for`
   below) — this is documented as part of the fix's own reasoning, not
   hidden.
6. Replaced/added tests in `tests/test_m2_6b4m_self_echo_probe.py`:
   removed the now-obsolete characterization test (which required the
   OLD bug to persist) and added deterministic coverage using explicit
   event coordination for: warm-up remaining pending while the final
   stop is withheld; completing correctly once released; a bounded
   failure with attached evidence when the stop never arrives; multiple
   repeats with baseline-relative (not stale/absolute) counters; a
   failing body still getting a cleanup attempt with the original
   failure surviving even a cleanup that also fails; and a cleanup
   failure alone preventing a false success.
7. Ran the focused test suites, then the full project suite, then
   ruff.
8. Compared `test_tts_server.TestPiperNiceRealChildPriority
   .test_nice_wrapper_actually_sets_child_priority` directly against
   the baseline commit, in the same shell, via a detached git worktree
   (removed afterward) — not merely inferred from an empty `git diff`.
9. Ran **exactly one** real-hardware reproduction of the EXACT command
   that previously failed (R0057's own `run3`), under the same external
   bounded `timeout` supervisor, with mixer values read-only
   before/after.
10. Preserved all raw logs/JSON locally (git-ignored, per existing
    convention) and wrote a tracked evidence manifest
    (`docs/research/m2_6_cloud_realtime_voice/R0058_evidence_manifest.md`)
    with hashes, sizes, commands, exit statuses, and decisive excerpts.
11. Corrected four specific overclaims/predictions in the R0057 report
    text itself (not renamed, not renumbered) — see DOCUMENTATION /
    REPORTS UPDATED below.

## LIFECYCLE-REUSE / EVENT-ORDERING INSPECTION (source, not assumed)

Read directly from `_run_warmup`, `_play_assistant_phrase`, and
`Recorder` (`m2_6b4m_self_echo_probe.py`) before choosing whether
completion must be awaited per-repeat or once after the final repeat:

- Every repeat within ONE `_run_warmup` call shares the SAME
  `_ResponseLifecycle` instance and the SAME `Recorder`.
  `playback_start_count`/`playback_stop_count` are cumulative counters,
  incremented once per real `BotStartedSpeakingFrame`/
  `BotStoppedSpeakingFrame` (`Recorder.mark_playback_start`/
  `mark_playback_end`) — never reset mid-warmup (`_run_warmup` never
  calls `recorder.reset_trial()`; only `_run_silent_trial`/
  `_run_control_trial` do, once per MEASURED trial).
- `_play_assistant_phrase` already blocks, chunk by chunk, until its
  own PCM is fully queued, and only returns after ALSO queuing
  `TTSStoppedFrame` and calling `lifecycle.mark_generation_done()` —
  but that return proves only that the frame reached the worker's own
  push queue, never that the transport has actually finished producing
  sound for that repeat (the exact gap Correction 5 already
  established for `ref_accepted_bytes`).
- Because the counters are monotonic, cumulative, and never reset
  mid-warmup, a stop cannot be attributed to a repeat that has not yet
  started, and the Nth stop cannot be observed before the Nth start —
  so waiting ONCE, after the LAST repeat has been submitted, for
  `playback_stop_count`'s cumulative delta to reach `repeats_run` is
  equivalent in strictness to waiting after every individual repeat.
  Polling once at the end (the same pattern already established for
  `ref_accepted_bytes`) is therefore correct and does not add an
  unnecessary poll per repeat, or any `asyncio.sleep` beyond the
  existing bounded 0.02s poll interval.

## A CORRECTED UNDERSTANDING OF `asyncio.wait_for`, VERIFIED BEFORE TRUSTING THE FIX

My own first draft of `_shutdown_runner` used
`asyncio.wait_for(run_task, timeout=...)` directly, reasoning from its
docstring ("cancels the task and raises TimeoutError") that this alone
would bound the wait. **Verified wrong with a standalone reproduction
script before trusting it**: reading `asyncio/timeouts.py` (installed
CPython 3.13.5) shows `Timeout.__aexit__` converts a pending
cancellation into `TimeoutError` ONLY if a `CancelledError` is what is
actually propagating out of the `async with` block when it exits.
`_on_timeout()` cancels the CALLING task, which (since it is suspended
on `_fut_waiter = run_task`) forwards `.cancel()` onto `run_task` — but
if `run_task` catches that `CancelledError` internally and goes on to
suspend on something else (exactly `PipelineWorker.run()`'s own
documented shape: catch, call `_cancel()`, await
`_wait_for_pipeline_finished()` again), the calling task's `await
run_task` is registered as a done-callback and simply never wakes up
until `run_task` actually finishes — no exception ever propagates, so
no `TimeoutError` is ever raised, and `wait_for` hangs for exactly as
long as `run_task` itself takes, timeout argument notwithstanding.
Reproduced directly: a throwaway task that catches `CancelledError` and
loops forever hung an `asyncio.wait_for(task, timeout=0.05)` call
indefinitely, not for 0.05s (see the evidence manifest for the
transcript). **Fixed** by using `asyncio.wait({task}, timeout=...)`
instead — it returns `(done, pending)` after AT MOST `timeout` seconds
regardless of whether the task ever finishes, and does NOT cancel
anything itself — then explicitly calling `run_task.cancel()` (a scoped
cancellation of a task the caller already owns, never a global
monkeypatch) and taking one further, independently-bounded
`asyncio.wait()` peek to see whether that explicit cancellation
actually took effect. If still pending, `_shutdown_runner` gives up and
reports failure — genuinely bounded at
`run_task_timeout_s + run_task_cancel_grace_s`, verified by the same
reproduction script, not merely re-asserted.

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`:
  - New `WarmupIncompleteError` (carries `.context`).
  - `_run_warmup`: Correction 6 — bounded wait now requires
    `playback_stop_count` catch-up too; raises `WarmupIncompleteError`
    (with fast-fail on an already-impossible counter state) instead of
    ever returning incomplete evidence.
  - New `RunnerShutdownError` (carries `.outcome`), `_shutdown_runner`,
    `_run_body_with_guaranteed_cleanup`.
  - `_run()`: the three former bare `assert` statements are gone (moved
    into `_run_warmup`); the warm-up call + trial loop now run inside
    `_run_body_with_guaranteed_cleanup`'s own body; the old ad-hoc
    diagnostic-only scaffolding from the R0057 checkpoint (manual
    `ENTERING_FINALLY`/`run_task.done()`/`.exception()` prints, the
    5-second stack-dump watchdog, the `WARMUP_ASSERT_*_BEFORE/AFTER`
    markers) is removed, superseded by the new structural guarantee and
    `_shutdown_runner`'s own prints — no longer needed once cleanup is
    unconditionally attempted at the right point instead of guessed at
    after the fact.
  - `main()`/`_run_with_initiating_exception_report`: unchanged (still
    the outermost exception-reporting boundary; now mostly observes
    exceptions that `_run_body_with_guaranteed_cleanup` already handled
    cleanup for, rather than ones that orphaned `run_task` entirely).
  - Removed the now-unused `contextlib` import.
- `tests/test_m2_6b4m_self_echo_probe.py`:
  - Removed `test_characterization_warmup_can_return_before_final_playback_stop_observed`
    (required the old bug to persist).
  - Adapted `test_artificially_dropped_reference_pcm_makes_the_proof_fail`
    to assert `WarmupIncompleteError` is raised (with the expected
    evidence in `.context`) instead of asserting on a returned dict.
  - Added `_yielding_sleep` (a genuinely-yielding sleep-patch
    alternative to the file's existing `_instant_sleep`, needed
    wherever a concurrently-scheduled fake task must get a real
    scheduling turn — documented in its own docstring why
    `_instant_sleep` alone would starve such a task).
  - Added `TestRunWarmup.test_warmup_completion_waits_for_final_playback_stop_before_returning`,
    `test_warmup_missing_final_stop_is_a_bounded_failure_with_evidence`,
    `test_warmup_uses_baseline_relative_counters_not_stale_totals`.
  - Added `TestShutdownRunner` (4 tests) and
    `TestRunBodyWithGuaranteedCleanup` (5 tests), both using fake
    doubles / plain callables, no Pipecat.
- `docs/reports/R0057_warmup_lifecycle_diagnostic_initiating_exception_20260913.md`:
  corrected in place (see DOCUMENTATION / REPORTS UPDATED).
- `docs/research/m2_6_cloud_realtime_voice/R0058_evidence_manifest.md`
  (new, tracked): hashes/sizes/commands/exit-statuses/excerpts for the
  raw, git-ignored evidence this report cites.
- This report (`R0058`).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (updated after this
  report, per repo convention).

**No `src/nexa/**` file touched** (confirmed: `git diff --stat --
src/nexa` empty against `1e38c55`). **No XVF3800 parameter, no ALSA
mixer, changed.**

## WHAT I VERIFIED

### Focused tests

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe -v
```
→ **59 tests, OK** (48 prior + removed 1 + added 12 = 59).

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe tests.test_bargein_m2_5b -q
```
→ **103 tests, OK.**

```
.venv/bin/ruff check tests/test_m2_6b4m_self_echo_probe.py \
  docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py
```
→ **All checks passed.**

### Full project suite

```
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```
→ **1080 tests, 1 failure, skipped=7.** (1069 + 12 new − 1 removed =
1080.) The one failure is the SAME `test_tts_server
.TestPiperNiceRealChildPriority.test_nice_wrapper_actually_sets_child_priority`
already reported in R0057 — see the PROVENANCE VERIFICATION section
below for the direct baseline comparison performed this checkpoint
(not merely inferred).

### Provenance verification (direct comparison, not inference)

The environment's own shell runs at `os.nice(0) == 5` (not the usual
`0`) — an environment/sandbox characteristic. Using a detached git
worktree at this checkpoint's own baseline commit (`1e38c55`, removed
afterward via `git worktree remove`; the main working tree was never
touched), ran the SAME single test with the SAME venv, in the SAME
shell:

```
baseline (1e38c55): AssertionError: '15' != '10'  -- FAILED
current  (HEAD)    : AssertionError: '15' != '10'  -- FAILED
```

**VERIFIED FACT:** identical failure at both revisions, under
identical process-priority conditions. `git diff --stat -- tests/test_tts_server.py`
between the two revisions is also empty. **Provenance: pre-existing,
environment-caused (this sandbox's own base nice level), unrelated to
this checkpoint's changes** — confirmed directly, not assumed from an
untouched-file inference alone. Not fixed here (out of scope, per this
checkpoint's own explicit constraint).

### Hardware validation — the one real-hardware reproduction

**Mixer readings, immediately before** (read-only):
```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```

**Command executed exactly once**, under the same external bounded
supervisor R0057 used:

```bash
timeout --signal=TERM --kill-after=10s 60s \
  .venv/bin/python -u docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --warmup-seconds 1 --level max --repeats 0 \
  > .../warmup_hang_logs/run4_fix_verification_warmup1_repeats0_20260913T143633Z.log 2>&1
```

**Result: EXIT CODE 0 — natural completion.** The `timeout` supervisor
never needed to intervene (contrast with R0057's `run3`, same exact
command, EXIT_CODE=124).

**Decisive excerpt** (full log preserved locally; see the evidence
manifest for hashes and the complete quoted excerpt):
```
WARMUP_RETURNED {'requested_seconds': 1.0, 'delivered_bytes': 112128,
                  'ref_accepted_bytes': 112128, 'playback_start_count': 1,
                  'playback_stop_count': 1, 'repeats_run': 1,
                  'interrupt_confirmed_delta': 0}
ENTERING_TRIAL_LOOP
RUNNER_END_BEGIN
RUNNER_END_OUTCOME=clean
RUN_TASK_AWAIT_BEGIN
RUN_TASK_AWAIT_OUTCOME=clean
SHUTDOWN_OUTCOME={'runner_end': 'clean', 'run_task_await': 'clean', 'escalated': False, 'failed': False}
SILENT 'MAX' RESULT: 0 false confirmed barge-in(s) across 0 trials (expect 0)
EXIT_CODE=0
```

`playback_start_count == playback_stop_count == repeats_run == 1` —
the exact condition that raised `AssertionError` in R0057's `run3` now
holds, proven by real hardware telemetry, not merely by the offline
tests.

**Mixer readings, immediately after** (read-only): identical to
before — unchanged.

**Process check after the run:** `ps aux | grep -i
"m2_6b4m_self_echo_probe\|aplay"` → no output. No leftover process.

## SUCCESS CRITERIA — ALL MET

- ✅ Complete warm-up evidence (`playback_start_count == playback_stop_count == repeats_run == 1`, `interrupt_confirmed_delta=0`, `ref_accepted_bytes` fully caught up).
- ✅ Zero confirmed warm-up interruptions.
- ✅ Zero measured trials (`--repeats 0`).
- ✅ Successful runner shutdown (`SHUTDOWN_OUTCOME={..., 'failed': False}`).
- ✅ Natural process completion, exit code 0.
- ✅ No leftover probe-owned audio processes.
- ✅ Unchanged mixer values, verified read-only before and after.

## REMAINING CLEANUP UNCERTAINTY

`_shutdown_runner`'s escalation path (explicit `run_task.cancel()` +
one further bounded `asyncio.wait()`) is now covered by deterministic
tests using a task that never finishes and one that finishes only after
an explicit cancel — both confirmed to return within their configured
bound rather than hang. What remains UNVERIFIED ON REAL HARDWARE is the
scenario the escalation path exists for in the first place: a
`run_task` that genuinely refuses to finish even after
`WorkerRunner.end()` + an explicit cancel (e.g. a wedged PortAudio
callback or a Pipecat-internal await that never resolves). This
checkpoint's own real-hardware run did not exercise that path (the
runner shut down cleanly on the first attempt) — by design, since this
checkpoint's own instruction was to run the ONE straightforward
reproduction, not to manufacture a wedged shutdown. If such a case ever
occurs on real hardware, `_shutdown_runner` will report `failed: True`
with a `still_pending_after_escalation` marker rather than hang the
process, but the external `timeout` supervisor (or an equivalent
process-level bound in whatever calls this probe) remains the only
TRUE hard backstop, exactly as this function's own docstring says — not
newly introduced by this checkpoint, just no longer masked by an
earlier assertion that used to orphan `run_task` before cleanup ever
had a chance to run at all.

## VERIFIED FACTS

1. `_run_warmup` now waits for `playback_stop_count` catch-up, not just
   `ref_accepted_bytes` — confirmed by source and by both offline tests
   and the real-hardware run.
2. A failure during `_run_warmup`/the trial loop now reliably triggers
   `_shutdown_runner` — confirmed by `TestRunBodyWithGuaranteedCleanup`.
3. `asyncio.wait_for(existing_task, timeout=...)` does NOT reliably
   bound a task that catches cancellation and continues — confirmed
   directly via a standalone reproduction script, not merely reasoned
   from documentation.
4. `asyncio.wait({task}, timeout=...)` does provide a genuine,
   non-cancelling bound — confirmed the same way.
5. The exact hardware command that previously produced `AssertionError`
   → orphaned `run_task` → indefinite stall → forced termination
   (exit 124) now completes naturally with exit code 0, unchanged
   otherwise (same `--warmup-seconds 1 --level max --repeats 0`,
   same hardware, same mixer settings).
6. `test_tts_server.TestPiperNiceRealChildPriority
   .test_nice_wrapper_actually_sets_child_priority`'s failure is
   confirmed, by direct baseline comparison, to be pre-existing and
   environment-caused, not introduced or exposed by this checkpoint.

## HYPOTHESES

None outstanding for the fix itself — REMAINING CLEANUP UNCERTAINTY
above documents the one still-untested (on real hardware) path, framed
as an explicit unknown, not a hypothesis.

## UNRESOLVED

- The genuinely-wedged-`run_task` escalation path is untested on real
  hardware (see REMAINING CLEANUP UNCERTAINTY).
- `AecReferenceFeeder.chunks_dropped` is still not read by any probe
  instrumentation (a gap identified in the original, pre-R0057 audit,
  still open, still out of this checkpoint's scope).
- The R0057 gain A/B experiment (`Array PCM,1` −20dB vs 0dB) remains
  **NOT EXECUTED**.

## ARCHITECTURE IMPACT

None. This checkpoint touches only a diagnostic research script and its
own test file. `BargeInController` remains the sole interruption
authority; measured trials still construct it armed exactly as before
(`arm_bargein=True` default, untouched — confirmed unchanged by this
checkpoint's own diff). `ConversationSession` remains canonical
authority (untouched). Gemini/Pipecat boundary unchanged.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (installed CPython 3.13.5 `asyncio/timeouts.py`/`asyncio/tasks.py`
and installed Pipecat 1.8.1 source, both already vendored in this
environment, read this checkpoint; no new external research).

## DOCUMENTATION / REPORTS UPDATED

- `docs/reports/R0057_warmup_lifecycle_diagnostic_initiating_exception_20260913.md`
  — corrected in place (report identity preserved, never renamed or
  renumbered): (1) TESTS section — the nice-value test's "pre-existing"
  claim is marked as the INFERENCE it was at the time, with a pointer
  to this report's own direct confirmation; (2) EVENT ORDER table —
  withdrew the "independent of warm-up duration and repeat count"
  overclaim for the two historical (60s/18-repeat, 10s/3-repeat) runs;
  (3) VERIFIED FACTS #3 — rewritten to distinguish the ONE
  directly-confirmed data point (this checkpoint's own `run3`, full
  traceback) from the two symptom-matched-but-not-independently-confirmed
  historical runs, and withdrew the universal-independence claim; (4)
  GIT STATUS — replaced the predictive "will be clean" wording with the
  observed post-commit fact.
- `docs/research/m2_6_cloud_realtime_voice/R0058_evidence_manifest.md`
  (new, tracked).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint).
- This report (`R0058`).

## CURRENT VERIFIED STATE

- Branch `main`. `Array 'PCM',1'` = -20.00dB, `UACDemoV10` = -0.94dB —
  unchanged throughout this entire checkpoint, verified read-only
  before and after the one hardware run.
- `M2.6B` remains IN PROGRESS. R0057's own gain A/B experiment remains
  NOT EXECUTED.
- Post-commit git status: see GIT STATUS below (observed, not
  predicted).

## NEXT RECOMMENDED ACTION

Execute the R0057 gain A/B experiment (`Array PCM,1` −20dB vs 0dB) now
that its blocking probe-lifecycle bug is fixed and verified — using the
exact procedure R0056/R0057 already designed (ONE probe invocation per
condition, `--warmup-seconds 60 --level max --repeats 3 --capture-pcm
--max-lag-ms 500`), which will now benefit from this checkpoint's own
complete warm-up evidence and guaranteed cleanup.

## TESTS

- `tests/test_m2_6b4m_self_echo_probe.py` → **59/59 pass**.
- `tests/test_bargein_m2_5b.py` → **44/44 pass** (unchanged).
- Full suite: **1080 tests, 1 failure (confirmed pre-existing via
  direct baseline comparison, unrelated to this checkpoint), skipped=7.**
- `ruff check` on all touched files: clean.
- `git diff --check`: clean.
- `git diff --stat -- src/nexa`: empty.

## GIT STATUS

Working tree **CONFIRMED clean** — `git status --short` returned empty
output immediately after the commit below. Not pushed. No Gemini call.
No hardware parameter changed.

## COMMIT HASHES

- `16daf10` — fix: R0058 warm-up lifecycle fix -- playback-stop completion evidence and guaranteed runner cleanup (M2.6B.4N follow-up)
