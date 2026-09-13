# R0057 — Warm-up Lifecycle Diagnostic: Initiating Exception Confirmed

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (post-R0056 read-only audit)
**Status:** **BOUNDED DIAGNOSTIC CHECKPOINT — COMPLETE.** The initiating
exception behind the probe's warm-up "cancelled from outside" hang is
now **CONFIRMED from a live, full traceback**, not merely hypothesized.
**This is not the R0057 gain A/B experiment** (`Array PCM,1` −20dB vs
0dB) — that experiment remains **NOT EXECUTED**; this checkpoint claims
the `R0057` report number for the diagnostic work that had to happen
first. The gain A/B experiment itself is deferred to a future report
(proposed `R0058`) once the confirmed bug below is fixed and
re-validated. **No `src/nexa/**` change. No hardware parameter written.
No Gemini. Not pushed. `M2.6B` remains IN PROGRESS.**

## TASK RESULT

**PASS** (as a diagnostic checkpoint: the initiating exception is now
proven, not merely inferred). The underlying probe bug is **not yet
fixed** — fixing it is explicitly out of scope for this checkpoint.

## PURPOSE

Establish, with direct evidence (not inference from cancellation
timing), the exact exception or condition that causes the probe's own
warm-up path to leave `run_task` orphaned, triggering
`asyncio.run()`'s internal task-cancellation cleanup and the
subsequently observed teardown stall — first identified as a leading,
unconfirmed hypothesis in the prior read-only audit (same-day, this
session, no separate report number assigned to that audit per the
operator's own instruction to keep it conversational).

## BASELINE COMMIT AND PRE-EXISTING CHANGES

- **Baseline commit (start of this checkpoint):** `a5c416e9708adb0fd28abe49a303e5bcd048239d` (`main`, HEAD).
- **Pre-existing uncommitted change at checkpoint start** (from the prior,
  same-day checkpoint's own Step 4 instrumentation — preserved, not
  discarded, and extended below):
  `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
  — `WARMUP_RETURNED`, `WARMUP_ASSERT_{1,2,3}_{BEFORE,AFTER}`,
  `ENTERING_TRIAL_LOOP`, `ENTERING_FINALLY`, `run_task.done()`/
  `.cancelled()`/`.exception()` prints, a 5-second bounded task-stack
  watchdog, and `flush=True` on the two pre-existing warm-up prints.
  This pre-existing diff is **identified separately** from this
  checkpoint's own additions in FILES CHANGED below.

## WHAT I DID

1. Preserved and durably archived the two previously-ephemeral failed
   hardware logs (60s and 10s warm-up runs, both from `/tmp`, both
   already reported in the prior audit) into
   `docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs/`
   (git-ignored, same convention as `self_echo_captures/` itself — see
   `.gitignore:47`), so they no longer depend solely on session-lifetime
   `/tmp` paths.
2. Added probe-only exception-reporting instrumentation wrapping the
   entire `_run(args)` coroutine **inside** the `asyncio.run()` task
   boundary — logs exception type, `repr()`, full traceback, and a
   monotonic timestamp, flushes immediately, then unconditionally
   re-raises; `asyncio.CancelledError` handled in a separate branch,
   also re-raised.
3. Replaced the two `contextlib.suppress(...)`-wrapped teardown calls
   (`runner.end()`, `wait_for(run_task, timeout=10)`) with explicit
   try/except chains that print exactly one of `clean` / `timeout_10s`
   / `cancelled` / `exception_suppressed` before continuing — same
   absorption behavior as before (nothing propagates further than it
   already did), now fully observable.
4. Added one new deterministic, offline **characterization** test
   (`TestRunWarmup.test_characterization_warmup_can_return_before_final_playback_stop_observed`)
   using the real `_run_warmup()` and a new fake downstream worker that
   withholds the final playback-stop observation behind an explicit,
   never-set `asyncio.Event` (no sleeps, no arbitrary delays).
5. Ran the exact focused test suites, then the full project suite.
6. Ran **exactly one** real-hardware reproduction —
   `--warmup-seconds 1 --level max --repeats 0` — under an external,
   bounded `timeout` supervisor, with mixer values verified read-only
   immediately before and after, full stdout+stderr captured from
   process start.
7. Verified process and hardware cleanliness after the run.

## EXACT DIAGNOSTIC DIFF

`docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py` —
this checkpoint's own additions on top of the preserved pre-existing
diff (full current file diff against `a5c416e` also available via
`git diff a5c416e -- docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`):

- New import: `traceback` (module was not previously imported).
- New module-level coroutine `_run_with_initiating_exception_report(args)`:
  wraps `await _run(args)` in `try:`/`except asyncio.CancelledError:`
  (prints `INITIATING_CANCELLED_ERROR t_monotonic=...`, re-raises) /
  `except Exception as e:` (prints `INITIATING_EXCEPTION
  t_monotonic=... type=... message=...`, prints the full traceback via
  `traceback.print_exc(file=sys.stdout)`, flushes, re-raises). Zero
  change to `_run()`'s own body, control flow, assertion positions, or
  timing.
- `main()` now calls `asyncio.run(_run_with_initiating_exception_report(args))`
  instead of `asyncio.run(_run(args))` — the only change to `main()`.
- In `_run()`'s own `finally:` block, the two previously
  `contextlib.suppress(...)`-wrapped calls
  (`await runner.end(reason="probe done")` and
  `await asyncio.wait_for(run_task, timeout=10)`) are now explicit
  try/except chains, each printing a `RUNNER_END_OUTCOME=...` /
  `RUN_TASK_AWAIT_OUTCOME=...` marker (`clean` / `timeout_10s` /
  `cancelled` / `exception_suppressed`) before continuing. The set of
  exceptions absorbed, and the fact that they are absorbed rather than
  propagated, is **unchanged** — only visibility was added.

`tests/test_m2_6b4m_self_echo_probe.py` — one new nested fake class
(`TestRunWarmup._FakeWorkerWithheldFinalStop`) and one new test method
(`test_characterization_warmup_can_return_before_final_playback_stop_observed`),
both additive; zero existing test modified.

**Confirmed by direct diff inspection:** `git diff --stat -- src/nexa`
is empty; only the probe research script and its own test file changed.

## WHAT I VERIFIED

### Step 3 — deterministic offline characterization

```
.venv/bin/python -m unittest \
  tests.test_m2_6b4m_self_echo_probe.TestRunWarmup.test_characterization_warmup_can_return_before_final_playback_stop_observed -v
```
Result: **1 test, OK.**

This test derives the exact repeat count `_run_warmup` itself would need
(the identical loop as the real function, not guessed), configures a
fake downstream worker that withholds **only** the final repeat's
playback-stop behind an `asyncio.Event` that is **deliberately never
set**, and shows directly:

- `_run_warmup` **returns** (`repeats_run == expected_repeats`,
  `ref_accepted_bytes` fully caught up) despite the final stop never
  having arrived;
- the returned snapshot has `playback_stop_count == expected_repeats − 1`,
  strictly less than `playback_start_count`;
- the exact condition `_run()`'s own third assert checks
  (`playback_start_count == playback_stop_count == repeats_run`) is
  shown, by direct computation on the returned dict, **not to hold** —
  without invoking `_run()` or its `assert` statement at all.

This is explicitly a **characterization of current behavior**, not a
regression test proving a fix — no fix was made, and none of the
function's own timing, waits, or return conditions were altered to
produce this result.

### Full focused and project suites

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe -v
```
→ **48 tests, OK** (47 pre-existing + 1 new).

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe tests.test_bargein_m2_5b -q
```
→ **92 tests, OK.**

```
.venv/bin/ruff check tests/test_m2_6b4m_self_echo_probe.py \
  docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py
```
→ **All checks passed.**

```
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```
→ **1069 tests, 1 failure, skipped=7.** The one failure —
`test_tts_server.TestPiperNiceRealChildPriority.test_nice_wrapper_actually_sets_child_priority`
(expects a real child-process `nice` value of `10`, observed `15`) — is
in a file **never touched by this checkpoint** (`git diff --stat --
tests/test_tts_server.py` empty; last touched by an unrelated commit,
`47729a2`, predating this entire M2.6B.4N thread). Re-run in isolation,
it fails identically — a real-OS-state-dependent test, unrelated to
warm-up/bargein/probe work, not investigated further here as it is
outside this checkpoint's scope.

### Step 4 — the one real-hardware reproduction

**Hardware readings, immediately before** (read-only):
```
Array 'PCM',1  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```
Matches the expected, previously-recorded baseline exactly — no
mismatch to report.

**Command executed exactly once**, under an external bounded supervisor
(`timeout`, 60s normal bound + 10s SIGKILL grace, entirely outside the
Python process), full stdout+stderr captured from the first line:

```bash
timeout --signal=TERM --kill-after=10s 60s \
  .venv/bin/python -u docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --warmup-seconds 1 --level max --repeats 0 \
  > docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs/run3_warmup1_repeats0_20260913T135447Z.log 2>&1
```

**Result: the process was externally terminated by the `timeout`
supervisor (exit code `124`) after teardown stalled — this is explicitly
NOT a successful natural completion**, consistent with this
checkpoint's own instruction that an externally-terminated run must not
be reported as a clean pass. It is, however, a **complete and decisive
diagnostic capture**: the initiating exception fired and was fully
logged, with a real traceback, well before the external supervisor ever
had to intervene.

**Hardware readings, immediately after** (read-only): identical to
before — `Array 'PCM',1'` = `-20.00dB`, `UACDemoV10` = `-0.94dB`, both
unchanged. No stray `m2_6b4m_self_echo_probe`/`aplay` processes remained
(verified by `ps aux` after the supervisor's termination).

## COMPLETE PRESERVED REPRODUCTION LOG (key excerpt, in order, with timestamps)

Full log preserved at
`docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs/run3_warmup1_repeats0_20260913T135447Z.log`
(118 lines; ALSA/JACK startup noise omitted below, present in full in
the file):

```
14:54:48.223  DEBUG  PipelineWorker#0: StartFrame#0 reached the end of the pipeline, pipeline is now ready.
              AEC_REF_ACTIVE           True
              audible_mixer_card       UACDemoV10
              audible_gain_db          -0.94
              audible_linear_gain      0.8974
              reference_gain_applied   0.8974  (fed to AecReferenceFeeder.gain_source)

              warmup: requesting >= 1.0s of real, ACCEPTED reference PCM ...
14:54:51.104  DEBUG  Bot started speaking
14:54:52.214  DEBUG  VADProcessor#0: User started speaking
14:54:53.434  DEBUG  VADProcessor#0: User stopped speaking
              WARMUP_RETURNED {'requested_seconds': 1.0, 'delivered_bytes': 112128,
                                'ref_accepted_bytes': 112128, 'playback_start_count': 1,
                                'playback_stop_count': 0, 'repeats_run': 1,
                                'interrupt_confirmed_delta': 0}
              warmup: repeats_run=1 queued_s=3.50 accepted_s=3.50 (requested >= 1.0s)
                      playback_start_count=1 playback_stop_count=0 interrupt_confirmed_delta=0
              WARMUP_ASSERT_1_BEFORE
              WARMUP_ASSERT_1_AFTER
              WARMUP_ASSERT_2_BEFORE
              WARMUP_ASSERT_2_AFTER
              WARMUP_ASSERT_3_BEFORE
              INITIATING_EXCEPTION t_monotonic=423992.597927 type=AssertionError
                message=AssertionError('playback start/stop count does not match repeats run
                -- a hidden mid-fixture restart occurred during warmup; treat as a bug')
              Traceback (most recent call last):
                File ".../m2_6b4m_self_echo_probe.py", line 1573, in _run_with_initiating_exception_report
                  return await _run(args)
                File ".../m2_6b4m_self_echo_probe.py", line 1393, in _run
                  warmup_result["playback_start_count"]
                  == warmup_result["playback_stop_count"]
                  == warmup_result["repeats_run"]
              AssertionError: playback start/stop count does not match repeats run --
                a hidden mid-fixture restart occurred during warmup; treat as a bug
14:54:54.735  DEBUG  Pipeline worker PipelineWorker#0 got cancelled from outside...
14:54:54.735  DEBUG  Cancelling pipeline worker PipelineWorker#0
              [ -- no further output for the remainder of the 60s supervisor bound -- ]
EXIT_CODE=124
```

**No `WARMUP_ASSERT_3_AFTER`, `ENTERING_TRIAL_LOOP`, `ENTERING_FINALLY`,
`RUNNER_END_BEGIN`, or `RUN_TASK_AWAIT_BEGIN` marker appears anywhere in
this log** — direct, positive confirmation (not inference) that
execution never reached the `try:`/`finally:` block at all.

## INITIATING EXCEPTION EVIDENCE

- **Type:** `AssertionError`.
- **Message:** `"playback start/stop count does not match repeats run -- a hidden mid-fixture restart occurred during warmup; treat as a bug"`.
- **Failing source location:** `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py:1393`, inside `_run()`, the third of the three post-warm-up assertions:
  ```python
  assert (
      warmup_result["playback_start_count"]      # 1393
      == warmup_result["playback_stop_count"]     # 0
      == warmup_result["repeats_run"]              # 1
  ), (...)
  ```
- **Immediate cause:** `_run_warmup()`'s own returned snapshot
  (`WARMUP_RETURNED`, above) shows `playback_start_count=1`,
  `playback_stop_count=0`, `repeats_run=1` — the single repeat's own
  `BotStartedSpeakingFrame` had arrived, but its `BotStoppedSpeakingFrame`
  had not, at the exact moment `_run_warmup`'s own bounded wait (which
  polls only `ref_accepted_bytes`, never `playback_stop_count` — the
  prior audit's own source-confirmed finding) decided to return.
- **This exact scenario was independently, deterministically
  reproduced offline** by the new characterization test in Step 3
  **before** this hardware run — the hardware result matches the
  offline characterization's own prediction exactly.

## EVENT ORDER SUPPORTED BY TIMESTAMPS

| Time (wall-clock) | Event |
|---|---|
| 14:54:48.223 | Pipeline ready |
| 14:54:51.104 | Bot started speaking (warm-up's one repeat begins; ≈2.88s after ready, matching `WARMUP_S=3.0`) |
| 14:54:52.214 | Real VAD registers the echo as "user speaking" (false candidate; `BargeInController` disarmed via `arm_bargein=False`, so no confirm/broadcast occurs — consistent with R0056/R0057-prior findings) |
| 14:54:53.434 | VAD "user stopped speaking" |
| *(shortly after, same monotonic window)* | `_run_warmup` returns; three post-warm-up prints/asserts execute in order; **assert 3 raises** |
| *(t_monotonic=423992.597927)* | `INITIATING_EXCEPTION` logged with full traceback — **inside** the `asyncio.run()` task boundary, **before** any cleanup |
| 14:54:54.735 | `asyncio.run()`'s own cleanup (triggered by the now-unhandled `AssertionError` propagating out of the orphaned coroutine) forcibly cancels the still-running `run_task`; Pipecat logs `"got cancelled from outside"` |
| 14:54:54.735 → (external timeout, ~60s later) | **No further log output** — teardown stalls again, exactly as in both prior (60s- and 10s-target) runs, now proven independent of warm-up duration and repeat count (1 repeat here vs. 3 and 18 previously) |
| (external) | `timeout` supervisor sends SIGTERM (and, per exit code 124, the process did not exit cleanly within its own accounting) |

## RUNNER AND TEARDOWN OUTCOMES

- **`runner.end(reason="probe done")`: never called.** No
  `RUNNER_END_BEGIN` marker anywhere in the log — direct proof, not
  inference.
- **`asyncio.wait_for(run_task, timeout=10)`: never reached.** No
  `RUN_TASK_AWAIT_BEGIN` marker.
- **Forced termination: YES**, by the external `timeout` supervisor
  (exit code `124`), after teardown stalled following the
  `"Cancelling pipeline worker"` line with no further output for the
  remainder of the bounded window. **This run is explicitly not a
  successful natural completion** — reported as such, not glossed over.
- Zero `TEARDOWN_STALL_DETECTED_AFTER_5s` line appears either — because
  that watchdog is created *inside* `finally:` (prior checkpoint's own
  instrumentation), which, as predicted in the prior audit's own
  Question I, never runs when the exception escapes *before* `finally`
  is reached. **This is now empirically confirmed, not just a
  structural prediction.**

## VERIFIED FACTS

1. The initiating exception is `AssertionError`, at
   `m2_6b4m_self_echo_probe.py:1393`, with the exact message quoted
   above — captured directly, with a full traceback, not inferred.
2. `_run_warmup()` returned a snapshot with `playback_stop_count=0` <
   `playback_start_count=1` on real hardware, for a single-repeat
   (1-second-target) warm-up — the exact race the read-only audit
   identified as source-possible and this checkpoint's own offline
   characterization test reproduced deterministically beforehand.
3. This failure is **independent of warm-up duration and repeat
   count**: it occurred identically at 18 repeats (60s target, prior
   session), 3 repeats (10s target, prior session), and now 1 repeat
   (1s target, this checkpoint) — always at the *last* required
   repeat.
4. `runner.end()` and the `run_task` await are never reached in any of
   the three real-hardware failures observed to date — confirmed
   directly from the log's own marker absence in this run, and from
   the absence of `WorkerRunner`'s own `"ending gracefully"` log line
   in the two prior runs.
5. Teardown stalls after `asyncio.run()`'s own cleanup cancels the
   orphaned `run_task` — reproduced a third time, this time terminated
   by an external supervisor rather than left to hang indefinitely or
   manually killed.
6. Zero `src/nexa/**` changes; hardware mixer values unchanged before
   and after (`Array 'PCM',1'` = -20.00dB throughout, `UACDemoV10` =
   -0.94dB throughout).

## HYPOTHESES (status after this checkpoint)

- **"A premature warm-up snapshot causes an assertion to escape `_run`;
  `asyncio.run` then cancels the outstanding runner during shutdown;
  shutdown stalls before the initiating traceback becomes visible."**
  — **First two clauses: CONFIRMED** (directly observed, not inferred).
  **Third clause ("stalls before the initiating traceback becomes
  visible") is now PARTIALLY SUPERSEDED**: with this checkpoint's own
  instrumentation, the traceback **does** become visible (that was the
  point of Step 2) — what remains true and reproduced is that teardown
  *itself* still stalls, just no longer hiding the initiating cause.
- **Whether `_ResponseLifecycle` reuse across repeats is itself
  invalid:** still not supported by any direct evidence — the
  confirmed exception is a probe-level `assert` about telemetry
  counters, unrelated to `_ResponseLifecycle`'s own internal state.

## REMAINING UNKNOWNS

1. **Exact blocked call inside Pipecat's own second `_cancel()`/
   `_wait_for_pipeline_finished()`** after it catches the injected
   `CancelledError` — not observable with the current instrumentation,
   since the watchdog that would reveal this never arms (it lives
   inside `finally`, per the Runner/Teardown Outcomes section above).
   A future checkpoint would need to move an equivalent stack-dump
   watchdog to a point that runs regardless of whether `finally` is
   reached (e.g., a signal handler or an `atexit`-style hook installed
   before `_run_warmup` is ever called) to observe it.
2. **Whether `AecReferenceFeeder.chunks_dropped` incremented** during
   this run — not read by any current instrumentation (a gap already
   identified in the prior audit, still open).
3. **Whether the SAME `AssertionError` would recur with the underlying
   race fixed** — not tested, since fixing it is explicitly out of
   scope for this checkpoint.

## SMALLEST PROPOSED FUNCTIONAL FIX (not implemented)

Make `_run_warmup()`'s own return condition wait for the final
repeat's `playback_stop_count` to catch up to `playback_start_count`,
using the same bounded, pollable pattern already used for
`ref_accepted_bytes` (same `poll_timeout_s`, same 0.02s poll interval)
— i.e., extend the existing bounded-wait loop
(`m2_6b4m_self_echo_probe.py`'s `_run_warmup`, current lines ~1067–1071)
to also require
`recorder.playback_stop_count - playback_stop_before >= repeats_run`
before returning. This is the minimal change consistent with the
function's own existing design (it already has exactly this kind of
bounded wait for one counter; extending it to a second, already-tracked
counter is not a new mechanism). Separately and independently, moving
`_run_warmup()` and its three assertions inside the existing
`try:`/`finally:` block (or into their own `try:`/`finally:` that still
guarantees `runner.end()`/`run_task` cleanup) would prevent a *future*
assertion failure (from any cause) from ever orphaning `run_task` again,
regardless of whether the specific race above is also fixed. Neither
change is implemented in this checkpoint.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (installed CPython 3.13.5 `asyncio/runners.py` and installed Pipecat
1.8.1 source, both already vendored in this environment, re-confirmed
this checkpoint where cited; no new external research).

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
  — this checkpoint's own additions (exception-reporting wrapper,
  explicit teardown-outcome markers) layered on top of the
  already-preserved, pre-existing Step-4 instrumentation from the same
  day's earlier checkpoint. Why: to observe the initiating exception
  directly rather than infer it from cancellation timing, per this
  checkpoint's own explicit task.
- `tests/test_m2_6b4m_self_echo_probe.py` — one new fake class and one
  new characterization test (see WHAT I VERIFIED). Why: deterministic,
  offline proof of the exact race, independent of and prior to any
  real-hardware run.
- `docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs/`
  (new, git-ignored directory) — three preserved logs: the two prior
  (60s, 10s) failed runs copied out of `/tmp`, plus this checkpoint's
  own 1s reproduction. Why: durable evidence, per this checkpoint's own
  instruction not to rely exclusively on ephemeral `/tmp` paths.
- `docs/reports/R0057_warmup_lifecycle_diagnostic_initiating_exception_20260913.md`
  (this report).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated (below).

**No `src/nexa/**` file touched** (confirmed: `git diff --stat --
src/nexa` empty). **No XVF3800 parameter, no ALSA mixer, changed.**

## ARCHITECTURE IMPACT

None. This checkpoint touches only a diagnostic research script and its
own test file. `BargeInController` remains the sole interruption
authority (measured trials, unaffected by any of this checkpoint's
changes, still construct it armed exactly as before —
`arm_bargein=True` default, untouched). `ConversationSession` remains
canonical authority (not touched by anything in this thread).
Gemini/Pipecat boundary unchanged. Local baseline, 500ms preroll,
`Sulafat`, zero local LID — all untouched.

## DOCUMENTATION / REPORTS UPDATED

- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint).
- This report (`R0057`).

## UNRESOLVED

- The underlying race (warm-up returning before the final
  `BotStoppedSpeakingFrame`) is confirmed but **not fixed**.
- The `try`/`finally` boundary gap (warm-up + its asserts sitting
  outside cleanup) is confirmed but **not fixed**.
- Teardown itself still stalls once the exception fires — the deeper
  cause of *that* stall (which Pipecat-internal await, exactly) remains
  unknown (see REMAINING UNKNOWNS).
- The R0057 gain A/B experiment (`Array PCM,1` −20dB vs 0dB) remains
  **NOT EXECUTED**.

## CURRENT VERIFIED STATE

- Branch `main`, working tree has exactly two modified/new tracked-file
  changes at the time of writing (the probe script, the test file),
  plus this report and the `CURRENT_STATE.md`/`ROADMAP.md` updates —
  all to be committed together per this repo's own convention (see GIT
  STATUS below for the exact post-commit state).
- `Array 'PCM',1'` = -20.00dB, `UACDemoV10` = -0.94dB — unchanged
  throughout this entire checkpoint, verified read-only before and
  after the one hardware run.
- `M2.6B` remains IN PROGRESS. R0057's own gain A/B experiment remains
  NOT EXECUTED.

## NEXT RECOMMENDED ACTION

Implement the smallest proposed functional fix above (extend
`_run_warmup`'s bounded wait to also require `playback_stop_count`
catch-up; separately, bring warm-up + its asserts inside a
`try:`/`finally:` that guarantees `runner.end()`/`run_task` cleanup
regardless of outcome), validate it with a NEW deterministic test
(the current characterization test should then be either updated to
show the fix, or paired with a new one proving the fix's own
`>=`-then-return behavior), and only then re-attempt a real-hardware
warm-up run before returning to the still-pending R0057 gain A/B
experiment.

## TESTS

- `tests/test_m2_6b4m_self_echo_probe.py` → **48/48 pass** (47 + 1 new).
- `tests/test_bargein_m2_5b.py` → **44/44 pass** (unchanged).
- Full suite: **1069 tests, 1 failure (pre-existing, unrelated,
  `test_tts_server.py`, untouched by this checkpoint), skipped=7.**
- `ruff check` on both touched files: clean.
- `git diff --check`: clean.
- `git diff --stat -- src/nexa`: empty.

## GIT STATUS

Working tree will be clean after the commit described below. Not
pushed. No Gemini call. No hardware parameter changed.

## COMMIT HASHES

- `5c30fe8` — fix: R0057 diagnostic checkpoint -- confirm initiating exception behind warm-up teardown hang (M2.6B.4N follow-up)
