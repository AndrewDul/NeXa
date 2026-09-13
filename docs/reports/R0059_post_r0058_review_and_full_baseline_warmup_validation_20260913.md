# R0059 — Post-R0058 Review Fixes and Full 60-Second Baseline Warm-up Validation

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (post-R0058 review checkpoint)
**Status:** **BOUNDED VALIDATION CHECKPOINT — COMPLETE.** Three specific
review points against R0058's own implementation were investigated
against the actual installed source; two revealed real, now-fixed gaps
(probe-only, minimal, both covered by new tests); the third (lifecycle
association across repeats) was investigated in depth and found to have
**no concrete functional defect** requiring a broader change. The full
60-second baseline warm-up (the actual condition R0057's gain A/B
experiment needs) was then validated on real hardware, exactly once,
and succeeded on every criterion. **This is full baseline warm-up
validation ONLY — the R0057 gain A/B experiment (`Array PCM,1` −20dB vs
0dB) remains NOT EXECUTED. No `src/nexa/**` change. No hardware
parameter written (`Array PCM,1` still exactly −20.00dB, `UACDemoV10`
still exactly −0.94dB, read-only before and after). No Gemini. No TV
tests. No dependency change. No measured trials. Not pushed. `M2.6B`
remains IN PROGRESS.**

## TASK RESULT

**PASS.**

## PURPOSE

1. Resolve three specific review points against R0058's own
   implementation (`16daf10`/`129832d`) before any further hardware
   execution: (a) an inaccurate shutdown-timeout-semantics comment, (b)
   a body-failure visibility gap in `_run_body_with_guaranteed_cleanup`
   if cleanup stalls, (c) whether `_ResponseLifecycle`'s own state
   handling across back-to-back warm-up repeats can corrupt
   `_run_warmup`'s own completion evidence.
2. Once resolved, validate exactly one full 60-second baseline warm-up
   on real hardware — the actual condition the still-pending R0057 gain
   A/B experiment requires (60s per condition, not the 1-second
   reproduction target R0058 used).

## BASELINE

- **Baseline commit:** `129832d1e5f76b1c2f0ee474a2c053a3c5982c90` (`main`, HEAD at the start of this checkpoint) — matches the supplied transcript (`16daf10` then `129832d`), verified directly via `git rev-parse HEAD` and `git log --oneline`, not assumed.
- **Working tree at start:** confirmed clean (`git status --short` empty).
- Read `AGENTS.md`, the actual current source of
  `m2_6b4m_self_echo_probe.py` and its test file in full (not a
  terminal-rendered diff) before making any change.

## REVIEW POINT 1 — Shutdown timeout semantics

**Finding: the prior comment was inaccurate, as flagged.** It claimed,
as a general rule, that `asyncio.wait_for` is "reliably bounded" for a
bare coroutine (like `runner.end(...)`) in a way an existing `Task`
(like `run_task`) is not. Verified against the installed CPython 3.13.5
source (`asyncio/tasks.py`, `asyncio/timeouts.py`) that this framing is
wrong in general: `wait_for`'s `TimeoutError` conversion depends
entirely on whether a `CancelledError` actually **propagates out** of
whatever is awaited — a bare coroutine can suppress that exactly like a
`Task` can (e.g. its own internal `except CancelledError: <keep
awaiting something else>`). The coroutine-vs-Task distinction itself
proves nothing.

**What DOES justify the present `wait_for(runner.end(...), ...)`
call**, confirmed by reading the ACTUAL installed
`WorkerRunner.end()` (`pipecat/workers/runner.py`) this checkpoint:

- `end()` sets `_shutdown_event` (synchronous), then
  `await self._finish_running_workers(BusEndWorkerMessage, reason)`.
- `_finish_running_workers` calls `await self._bus.send(message)` per
  still-running worker.
- `WorkerBus.send()` (`pipecat/bus/bus.py`): a `BusEndWorkerMessage` is
  a `BusLocalMessage`, delivered via a direct, **synchronous**
  `self.on_message_received(message)` call — confirmed by reading
  `AsyncQueueBus.publish()` (`pipecat/bus/local/async_queue.py`, the
  bus type this probe's own `WorkerRunner()` constructs by default)
  too: `on_message_received(message)`, again fully synchronous.

**There is no actual suspension point anywhere in this call chain**,
for this probe's single-in-process-worker, default-bus configuration —
`end()` runs to completion in one step. There is nothing for a
cancellation to interrupt mid-flight, and no cancellation-swallowing
pattern exists anywhere in its own source to worry about. This is why
`wait_for` around `runner.end(...)` is safe here — not because it is a
bare coroutine in general, but because THIS SPECIFIC coroutine has no
internal await at all in this deployment. A different `WorkerBus`
implementation (e.g. a networked one) would need the same scrutiny
before trusting `wait_for` around it the same way — noted explicitly in
the corrected comment as a caveat for any future change.

**Explicitly restated, per instruction:** neither `asyncio.wait_for`
nor `asyncio.wait` supplies a hard wall-clock or process-level
deadline. Both are best-effort bounds on the coroutine that calls them.
A genuinely wedged event loop or callback can still only be bounded by
an external process supervisor (this checkpoint's own hardware
validation, like R0058's, uses `timeout(1)`). This was already
mostly correct for the `run_task` escalation path in R0058's own text;
the correction here brings the `runner.end()` comment up to the same
standard and removes the inaccurate "bare coroutine" generalization.

**Fix applied:** the inline comment above
`await asyncio.wait_for(runner.end(reason="probe done"), timeout=end_timeout_s)`
in `_shutdown_runner` was rewritten to state the VERIFIED reason (no
suspension point in this deployment's `end()`/`bus.send()` chain) in
place of the incorrect general claim, and to restate the "no hard
deadline" caveat explicitly at that call site too. No behavioral change
— `_shutdown_runner`'s actual mechanism (`wait_for` for `end()`,
`wait`+explicit cancel for `run_task`) is unchanged, since the
mechanism itself was already correct; only the justification was wrong.

## REVIEW POINT 2 — Initiating-exception visibility

**Finding: CONFIRMED, a real gap.** `_run_body_with_guaranteed_cleanup`
caught a `body()` failure with a bare `except BaseException:` (no
exception bound, nothing printed) and only reported it later, via the
OUTER `_run_with_initiating_exception_report` wrapper — **after**
`await cleanup()` below had already fully resolved. If `cleanup()`
stalls (e.g. `_shutdown_runner`'s own bounded escalation window, up to
`run_task_timeout_s + run_task_cancel_grace_s` seconds by default, or
longer if something unexpected happens), the ORIGINAL failure's own
evidence would not reach disk until AFTER that entire stall — this
reproduces, one layer higher, the exact visibility gap R0057 exists to
document (an exception whose traceback never gets a chance to print
before something else hangs). The outer wrapper alone is insufficient
if cleanup stalls first, exactly as flagged.

**Fix applied (smallest probe-only correction, no shutdown redesign):**
`_run_body_with_guaranteed_cleanup`'s `except BaseException as e:`
branch now prints and flushes the exception's type, message, and full
traceback **immediately**, **before** `cleanup()` is even attempted.
The original exception object is then still passed through `cleanup()`
unmodified and re-raised via a bare `raise` afterward — propagation is
unchanged; only visibility timing changed. The outer wrapper's own
print is untouched (still runs, now a harmless, redundant-for-this-case
confirmation at the `asyncio.run()` boundary) and remains the only
reporter for a failure occurring OUTSIDE this function's own scope
(e.g. before `run_task` is created in `_run()`, which is not wrapped by
this function) — "keep exception reporting inside the asyncio.run
boundary" is preserved, this adds an earlier, additional layer rather
than replacing it.

**Test added:**
`TestRunBodyWithGuaranteedCleanup.test_body_failure_traceback_is_printed_before_cleanup_completes`
— a `body()` that raises, and a `cleanup()` deliberately parked on an
`asyncio.Event` the test itself controls (never a sleep/timing guess).
Proves, while the driving task is still genuinely pending (`task.done()
is False`, cleanup not yet called) and stdout is captured via
`contextlib.redirect_stdout`: the `BODY_FAILURE_BEFORE_CLEANUP` marker,
the exception's own message, and a full `Traceback (most recent call
last)` block are ALL already present in the captured output. Releases
the event afterward and confirms the SAME original exception is what
ultimately propagates.

## REVIEW POINT 3 — Multiple-repeat lifecycle association

**Finding: no concrete functional defect requiring a broader change.**
Investigated the real `_ResponseLifecycle`
(`src/nexa/realtime/gemini/runtime.py`) and the actual
`BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` generation and
ordering (`pipecat/transports/base_output.py`, read this checkpoint) —
not assumed.

**A real, source-confirmed race in `_ResponseLifecycle`'s OWN internal
state DOES exist** across back-to-back warm-up repeats: `mark_dispatched()`
(called at the START of each repeat, from `_play_assistant_phrase`,
running in the `_run_warmup` loop's own calling task) unconditionally
resets `_generating`, `_produced_audio`, `_bot_speaking`,
`_playback_confirmed_drained`, and `_finished_fired`. Because
`_play_assistant_phrase` returning after `worker.queue_frames([TTSStoppedFrame()])`
proves only that the stop frame reached the worker's own push queue —
exactly the same "queued is not accepted/processed" gap Correction 5
already documented for `ref_accepted_bytes` — the PREVIOUS repeat's own
`observe_bot_stopped()` call (fired later, from the pipeline's own
processing task, when the real `BotStoppedSpeakingFrame` for that
repeat is actually processed) can in principle arrive AFTER the NEXT
repeat's `mark_dispatched()` has already reset the object.

**Traced the consequence and found it inert for this probe's own
completion evidence:**

1. If repeat N's stop arrives after repeat N+1's `mark_dispatched()`:
   `observe_bot_stopped()` sets `_bot_speaking=False` (a no-op, already
   False) and checks `if not self._generating:` — but `_generating` is
   already `True` again (reset for N+1), so `_playback_confirmed_drained`
   is NOT wrongly set for N+1. `_maybe_fire()` then returns immediately
   because `_generating` is `True`. **No incorrect `on_finished()` ever
   fires from this race.**
2. `_run_warmup` **never reads** `lifecycle._finished_fired` or
   `on_finished`'s own output at all — confirmed directly from its
   source: it only reads `bargein.telemetry.interrupt_confirmed` and
   `Recorder`'s own counters. Only `_run_silent_trial`/`_run_control_trial`
   (via `_wait_for_finish`) ever consult the lifecycle's `_finished_fired`
   flag — neither runs during warm-up, and neither ran in this
   checkpoint (`--repeats 0`).
3. **`Recorder.playback_start_count`/`playback_stop_count` — the ONLY
   counters `_run_warmup`'s own Correction 6 completion proof depends
   on — are driven directly and unconditionally in
   `_PlaybackWatcher.process_frame()`, completely independent of
   `_ResponseLifecycle`'s own state.** Confirmed from
   `pipecat/transports/base_output.py`: `_bot_started_speaking()`/
   `_bot_stopped_speaking()` fire off a **single-consumer, strictly
   ordered FIFO** (`_audio_queue`, processed one frame at a time by ONE
   `_audio_task_handler` task) — `_bot_started_speaking()` is a no-op if
   already speaking; `_bot_stopped_speaking()` fires only on a
   `TTSStoppedFrame` when `_tts_audio_received` is `True`, then resets
   it. Because `_run_warmup`'s own loop only starts repeat N+1's
   `_play_assistant_phrase` call AFTER repeat N's own call has already
   `await`ed its full chunk sequence AND its `TTSStoppedFrame` (Python's
   own sequential `await`, not concurrent), the frames for every repeat
   reach this SAME FIFO queue in strict repeat order:
   `[chunks₁, STOP₁, chunks₂, STOP₂, …, chunksₖ, STOPₖ]` — and a single
   consumer processing one frame at a time cannot reorder or interleave
   them. This structurally guarantees exactly one start and one stop
   per repeat, in order, regardless of real hardware write latency
   (`_internal_write_audio_frame` runs AFTER `_handle_frame` for each
   frame, so it cannot affect the ORDER frames are logically handled
   in). **Cumulative equality of these counters is therefore not a
   generic, potentially-fooled heuristic here — it follows directly
   from a structural guarantee in the underlying transport, confirmed
   from source, not merely observed to hold so far.**

**One separate, pre-existing overclaim noticed while tracing this**
(not itself a defect in the fix, but worth recording accurately):
`_PlaybackWatcher`'s own docstring and R0057/R0058's prior comments
describe `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` as firing
"once the real PortAudio device has actually begun/finished producing
sound." Reading `base_output.py` directly shows these are **logical**
signals driven by `TTSAudioRawFrame`/`TTSStoppedFrame` CONTROL markers
passing through the frame-processing queue — they prove the frame-level
pipeline correctly produced one start/stop per repeat, not that the
physical speaker has finished reproducing the sound at that instant
(actual device writes happen via `_internal_write_audio_frame`,
strictly AFTER `_handle_frame`, so there is still OS/ALSA buffering and
DAC latency beyond this signal). This is folded into the EVIDENCE
LIMITATION section below and the tracked manifest, per instruction —
**not treated as a functional defect**, since `_run_warmup`'s own claim
("every repeat reached a clean start-then-stop completion at the frame
level") remains fully proven; only the STRONGER, unproven claim
("...and the physical speaker had finished") is corrected.

**No code change made for this review point** — no functional defect
was found in `_run_warmup`'s own actual completion proof; no arbitrary
sleep or repeat-pacing change was made, per instruction.

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`:
  - `_shutdown_runner`: corrected the inline comment above the
    `runner.end(...)` `wait_for` call (Review Point 1) — verified
    reasoning from actual `WorkerRunner.end()`/`WorkerBus.send()`/
    `AsyncQueueBus.publish()` source, explicit "no hard deadline"
    restatement. No behavioral change.
  - `_run_body_with_guaranteed_cleanup`: prints and flushes the
    original exception's type/message/traceback immediately in the
    `except BaseException as e:` branch, before `cleanup()` is
    attempted (Review Point 2). Docstring extended with the confirmed
    finding and fix. No change to propagation semantics.
- `tests/test_m2_6b4m_self_echo_probe.py`: added
  `TestRunBodyWithGuaranteedCleanup.test_body_failure_traceback_is_printed_before_cleanup_completes`;
  added `io` to imports (needed for the new test's `io.StringIO`
  stdout capture).
- `docs/research/m2_6_cloud_realtime_voice/R0059_evidence_manifest.md`
  (new, tracked): hashes/sizes/commands/exit-status/decisive excerpt
  for this checkpoint's own git-ignored raw log/JSON, plus the explicit
  evidence-limitation note for A/B readiness.
- This report (`R0059`).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (updated after this
  report, per repo convention).

**No `src/nexa/**` file touched** (confirmed: `git diff --stat --
src/nexa` empty against `129832d`). **No XVF3800 parameter, no ALSA
mixer, changed.**

## WHAT I VERIFIED

### Focused tests

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe.TestRunBodyWithGuaranteedCleanup -v
```
→ **6 tests, OK** (5 prior + 1 new).

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe -v
```
→ **60 tests, OK** (59 prior + 1 new).

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe tests.test_bargein_m2_5b -q
```
→ **104 tests, OK.**

```
.venv/bin/ruff check tests/test_m2_6b4m_self_echo_probe.py \
  docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py
```
→ **All checks passed.**

Per instruction, the full project suite was **not** re-run this
checkpoint (its one known failure, `test_tts_server`'s nice-value test,
is already documented as confirmed pre-existing in R0058 via a direct
baseline comparison — re-running it here would only reproduce the same
already-recorded result at real cost, not new evidence).

`--dry` re-checked after both source edits: unchanged, still passes.

### Hardware validation — the one full 60-second baseline warm-up

**Mixer readings, immediately before** (read-only):
```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```
Matches the expected baseline exactly — no mismatch to report (had
there been one, this checkpoint would have stopped before running
anything, per instruction).

**Repeat count derived from the actual fixture, not assumed:** the
default fixture (`en_explain_gravity.wav`) is confirmed 3.504s @
16000Hz (`wave` module, read directly). `_run_warmup`'s own
`while delivered_bytes < target_bytes` loop for a 60s target requires
exactly 18 whole repeats (17×3.504=59.568s < 60s; 18×3.504=63.072s ≥
60s) — matching the task's own stated expectation of "approximately
18" and the run's own observed `repeats_run=18` exactly.

**Command executed exactly once**, under a FRESH external bound (150s
+ 10s kill-after grace) — deliberately not a reuse of the prior
checkpoint's 60s bound, which matched a 1-second warm-up target, not a
60-second one:

```bash
timeout --signal=TERM --kill-after=10s 150s \
  .venv/bin/python -u docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --warmup-seconds 60 --level max --repeats 0 \
  > .../warmup_hang_logs/run5_baseline60_verification_20260913T150149Z.log 2>&1
```

**Result: EXIT CODE 0 — natural completion.** Total wall time
16:01:49.368 → 16:03:01.449 UTC ≈ 72.1s, well inside the 150s bound
(the external supervisor was never exercised).

**Decisive excerpt** (full log preserved locally; hashes and the
complete excerpt in the evidence manifest):
```
WARMUP_RETURNED {'requested_seconds': 60.0, 'delivered_bytes': 2018304,
                  'ref_accepted_bytes': 2018304, 'playback_start_count': 18,
                  'playback_stop_count': 18, 'repeats_run': 18,
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

**Mixer readings, immediately after** (read-only): identical to
before.

**Process check after the run:** `ps aux | grep -i
"m2_6b4m_self_echo_probe\|aplay"` → no output. No leftover process.

## SUCCESS CRITERIA — ALL MET

- ✅ Requested warm-up PCM duration met (`accepted_s=63.07 >= 60.0`).
- ✅ `playback_start_count == playback_stop_count == repeats_run` (`18 == 18 == 18`).
- ✅ Zero confirmed warm-up interruptions (`interrupt_confirmed_delta=0`).
- ✅ Zero measured trials (`--repeats 0`, `trials: []`).
- ✅ Clean shutdown without escalation (`escalated: False, failed: False`).
- ✅ Natural exit code 0.
- ✅ No leftover probe-owned processes.
- ✅ Unchanged mixer readings afterward.

## EVIDENCE LIMITATION FOR THE EVENTUAL GAIN A/B READINESS ASSESSMENT (explicit, per instruction)

The post-feeder byte count (`ref_accepted_bytes`) proves reference PCM
reached `AecReferenceFeeder`'s own internal queue — it does **not**
prove the physical `plug:respeaker` device or the XVF3800's own
reference input actually ingested that PCM, and it does **not**
exclude an internal drop inside `AecReferenceFeeder`'s own writer path
(`chunks_dropped` is still not read by any probe instrumentation — an
open, unchanged gap). Separately (a finding from this checkpoint's own
Review Point 3 investigation), `playback_start_count`/
`playback_stop_count` are logical, frame-level signals proven correct
at the Pipecat frame-processing level, not a physical-acoustic playback
confirmation. Both points are recorded in
`R0059_evidence_manifest.md` for whoever performs the eventual A/B
readiness assessment.

## VERIFIED FACTS

1. `asyncio.wait_for`'s `TimeoutError` conversion depends on whether a
   `CancelledError` genuinely propagates out of the awaited coroutine —
   coroutine-vs-Task is not the determining factor; verified against
   installed CPython 3.13.5 source.
2. `WorkerRunner.end()`, in this probe's own single-worker/default-bus
   configuration, has no actual suspension point anywhere in its call
   chain — confirmed by reading `end()` →
   `_finish_running_workers` → `WorkerBus.send()` →
   `AsyncQueueBus.publish()`, all synchronous for a `BusLocalMessage`.
3. `_run_body_with_guaranteed_cleanup` previously reported a body
   failure's traceback only AFTER `cleanup()` fully resolved — fixed to
   print/flush immediately, before `cleanup()` is attempted; the
   original exception object still propagates unmodified.
4. `_ResponseLifecycle`'s own internal state CAN receive a stale
   `observe_bot_stopped()` call after the next repeat's
   `mark_dispatched()` has already reset it, but this never produces an
   incorrect `on_finished()` firing and has zero effect on
   `Recorder.playback_start_count`/`playback_stop_count`, the only
   counters `_run_warmup`'s own completion proof depends on — these are
   driven by a structurally-ordered, single-consumer FIFO in Pipecat's
   own output transport, confirmed from source.
5. One full 60-second baseline warm-up (18 repeats, matching the
   fixture-derived expectation exactly) completed naturally on real
   hardware with every success criterion met; mixer values unchanged.

## HYPOTHESES

None outstanding requiring further investigation before the gain A/B
experiment. The `_ResponseLifecycle` staleness noted in Review Point 3
is a confirmed, currently-inert race — not a hypothesis needing
testing, since its consequence (or lack thereof) was traced completely
from source.

## UNRESOLVED

- `AecReferenceFeeder.chunks_dropped` is still not read by any probe
  instrumentation (unchanged, open gap, out of this checkpoint's
  scope).
- The genuinely-wedged-`run_task` escalation path in `_shutdown_runner`
  remains untested on real hardware (this run's own shutdown was clean,
  non-escalated — unchanged from R0058's own note on this).
- The R0057 gain A/B experiment (`Array PCM,1` −20dB vs 0dB) remains
  **NOT EXECUTED**.

## ARCHITECTURE IMPACT

None. This checkpoint touches only a diagnostic research script and its
own test file. `BargeInController` remains the sole interruption
authority; measured trials still construct it armed exactly as before
(unchanged by this checkpoint's diff — this run had zero measured
trials by design, `--repeats 0`). `ConversationSession` remains
canonical authority (untouched). Gemini/Pipecat boundary unchanged.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (installed CPython 3.13.5 `asyncio/tasks.py`/`asyncio/timeouts.py`,
installed Pipecat 1.8.1 `pipecat/workers/runner.py`,
`pipecat/bus/bus.py`, `pipecat/bus/local/async_queue.py`,
`pipecat/transports/base_output.py`, and `src/nexa/realtime/gemini
/runtime.py`'s own `_ResponseLifecycle` — all already installed/present
in this repository, read this checkpoint; no new external research).

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/m2_6_cloud_realtime_voice/R0059_evidence_manifest.md` (new, tracked).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint).
- This report (`R0059`). `R0057` and `R0058` identities are unchanged —
  neither renamed nor renumbered.

## CURRENT VERIFIED STATE

- Branch `main`. `Array 'PCM',1'` = -20.00dB, `UACDemoV10` = -0.94dB —
  unchanged throughout this entire checkpoint, verified read-only
  before and after the one hardware run.
- **Full baseline warm-up validation only** — R0057's own gain A/B
  experiment remains NOT EXECUTED. `M2.6B` remains IN PROGRESS.
- Post-commit git status: see GIT STATUS below (observed, not
  predicted).

## NEXT RECOMMENDED ACTION

Execute the R0057 gain A/B experiment (`Array PCM,1` −20dB vs 0dB)
using the exact procedure R0056/R0057 already designed (ONE probe
invocation per condition, `--warmup-seconds 60 --level max --repeats 3
--capture-pcm --max-lag-ms 500`) — both the warm-up completion proof
and the runner cleanup guarantee are now fixed and validated at the
FULL 60-second scale the experiment itself requires, and the three
review points raised against the R0058 implementation are resolved.

## TESTS

- `tests/test_m2_6b4m_self_echo_probe.py` → **60/60 pass** (59 + 1 new).
- `tests/test_bargein_m2_5b.py` → **44/44 pass** (unchanged).
- `ruff check` on both touched files: clean.
- `git diff --check`: clean.
- `git diff --stat -- src/nexa`: empty.
- Full project suite: not re-run this checkpoint (see WHAT I VERIFIED
  for why — its one known, already-confirmed-pre-existing failure is
  unrelated and unchanged).

## GIT STATUS

Working tree **CONFIRMED clean** — `git status --short` returned empty
output immediately after the commit below. Not pushed. No Gemini call.
No hardware parameter changed.

## COMMIT HASHES

- `514c0c4` — fix: R0059 post-R0058 review fixes and full 60s baseline warm-up validation (M2.6B.4N follow-up)
