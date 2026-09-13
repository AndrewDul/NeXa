# R0060 — Reference-Observability Fix and Gain A/B Experiment Procedure Preparation

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (post-R0059 preparation checkpoint)
**Status:** **PREPARATION CHECKPOINT — COMPLETE. NO HARDWARE EXECUTION.**
Closed the reference-observability gap identified in review (probe-only,
reused counters, no feeder-behavior change) and prepared the exact,
reviewable R0057 gain A/B experiment procedure — script, rollback,
invalid-run criteria, comparison methodology — for Andrzej's explicit
approval. **The gain A/B experiment itself remains NOT EXECUTED. No
hardware parameter was written (`Array PCM,1` still exactly −20.00dB,
`UACDemoV10` still exactly −0.94dB, read-only throughout). No `src/nexa/**`
change. No Gemini. No TV tests. No dependency change. No audio hardware
run. Not pushed. `M2.6B` remains IN PROGRESS.**

## TASK RESULT

**PASS.**

## PURPOSE

1. Close the reference-observability gap: `ref_accepted_bytes` alone
   never surfaced `AecReferenceFeeder`'s own NEGATIVE evidence
   (queue-overflow drops, start/write failures) — add the smallest
   probe-only observation to report it, baseline-relative, for warm-up
   and every measured trial, distinguishing "unavailable" from a
   genuine zero.
2. Prepare — but explicitly NOT execute — the previously-accepted R0057
   gain A/B experiment procedure: exact mixer commands (verified
   against real device/control identities, not guessed), a supervised
   execution/rollback wrapper, invalid-run criteria, and a comparison
   methodology that accounts for unequal trial exposure and does not
   overclaim from a small-N result.
3. Do not repeat R0059's own already-completed full 60-second baseline
   warm-up validation.

## BASELINE

- **Baseline commit:** `4e72a602d8451ca37f4602f0797f23bb69d3d617` (`main`, HEAD at the start of this checkpoint) — matches the supplied transcript (`514c0c4` then `4e72a60`), verified directly via `git rev-parse HEAD`, not assumed.
- **Working tree at start:** confirmed clean (`git status --short` empty).
- Read `AGENTS.md`, the actual current source of `m2_6b4m_self_echo_probe.py`, `nexa/voice_tts/aec_reference.py`, and `nexa/voice/aec.py` in full before making any change.

## PART 1 — REFERENCE-OBSERVABILITY GAP CLOSED

### Source inspected (not assumed)

- `src/nexa/voice_tts/aec_reference.py` — the real, unmodified
  `AecReferenceFeeder`. Confirmed existing counters:
  `chunks_dropped` (incremented in `_enqueue()` only when the bounded
  reference queue, `DEFAULT_MAX_QUEUED_CHUNKS=24`, overflows and the
  OLDEST already-queued chunk is evicted to make room for the newest);
  `respawns` (incremented in `_start_sink(initial=False)`, i.e. every
  time the `aplay` sink is restarted after dying); `frames_mirrored`/
  `bytes_mirrored` (incremented in `_run_writer()` only AFTER
  `sink.write()` returns without raising — a stronger, but still
  software-only, signal, not surfaced by this checkpoint's new fields
  since neither is a drop/error signal).
- `src/nexa/voice/aec.py` — the real, unmodified `AecReferenceHealth`.
  `failure_count` is the only production writer-error evidence that
  exists (incremented in `mark_failed()`, called both on sink-start
  failure and on `_run_writer()`'s own `BrokenPipeError`/`OSError`/
  `ValueError` from a real write) — there is no separate per-write
  error counter; `active`/`ever_started` are liveness state, not
  counters.
- Confirmed `AecReferenceFeeder` was constructed inside
  `build_probe_pipeline` but never returned to the caller — `_run()`
  had NO reference to it at all before this checkpoint, so none of
  this telemetry was reachable by the probe's own reporting code.

### What I did

1. Added two new pure, offline-testable functions,
   `_aec_reference_snapshot(aec_feeder, aec_health)` and
   `_aec_reference_telemetry_delta(before, after)`, in the probe's own
   "pure, offline-testable analysis helpers" section (same convention
   as `speaking_intervals`/`rms_windows_from_events`). Both read ONLY
   existing attributes via `getattr(..., None)` — a missing attribute
   or a `None` object returns `None` for that field, explicitly
   distinguished from a present, genuine `0`. No new DSP, no feeder
   behavior change (read-only observation).
2. `build_probe_pipeline` now also returns `aec_feeder` (appended to
   its existing return tuple) so `_run()` can reach it.
3. `_run_warmup` gained optional `aec_feeder=None, aec_health=None`
   kwargs (every existing call site keeps working unchanged, reporting
   all-`None` telemetry when omitted) — snapshots before the delivery
   loop, diffs against a fresh snapshot when building its own `result`
   dict, so `aec_reference_telemetry` is present on EVERY return AND
   on every raised `WarmupIncompleteError.context`. **No new
   invalidating condition was added to `_run_warmup`'s own pass/fail
   logic** — a nonzero drop/failure delta does not, by itself, make
   `_run_warmup` raise; that judgment is left to the caller/report (the
   A/B procedure's own invalid-run criteria, Part 2 §5), per this
   checkpoint's own scope ("smallest probe-only observation").
4. `_run_silent_trial`/`_run_control_trial` gained the same optional
   kwargs, snapshotting at the start of each trial (before that
   trial's own `QUIET_BEFORE_S` sleep) and merging
   `result["aec_reference_telemetry"]` into their own returned dict —
   report per-trial, not only per-warmup.
5. `_run()` unpacks `aec_feeder` from `build_probe_pipeline` and
   threads both `aec_feeder`/`aec_health` into every warm-up/trial call
   site, printing the new telemetry alongside existing per-trial/
   per-warmup summary lines.
6. **Corrected the exact-tap-location accuracy of `ref_accepted_bytes`**
   (per instruction — field/behavior preserved, only its docstring's
   precision improved): the prior docstring said passing this tap was
   "proof of ACCEPTANCE." Re-reading `AecReferenceFeeder.process_frame()`
   /`_enqueue()` directly this checkpoint shows the frame counted here
   has run through the feeder's own gain-scaling and its own
   synchronous `_enqueue()` ATTEMPT — proof of passage through the
   feeder's own acceptance logic, not proof the chunk was successfully
   WRITTEN to `plug:respeaker` (that is `frames_mirrored`/
   `bytes_mirrored`, a separate, stronger, still-software-only signal)
   and certainly not proof of physical hardware ingestion. Corrected
   both `Recorder.ref_accepted_bytes`'s own docstring and
   `_PlaybackWatcher`'s matching claim; field name, value, and call
   sites are byte-for-byte unchanged.

### Tests added

`TestAecReferenceTelemetry` (7 tests, pure/offline, no asyncio): reads
existing counters correctly; both objects `None` → all `None`; a
present-but-missing single attribute (`chunks_dropped`) is `None` while
a present, genuine `0` (`respawns`) is reported as `0` — the exact
distinction required; exact delta arithmetic; a genuine zero delta
(same snapshot twice) is reported as `0`, not confused with
unavailable; either-snapshot-unavailable propagates `None` for deltas
while still reporting the AFTER snapshot's own liveness state.

Two `TestRunWarmup` integration tests: `_run_warmup` correctly threads
a fake feeder/health through end-to-end (reports the exact delta, not
the fake's own absolute totals); and reports all-`None` telemetry,
present as a key (never omitted), when neither is supplied — proving
every pre-existing call site keeps working.

## WHAT I VERIFIED (Part 1)

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe -v
```
→ **68 tests, OK** (60 prior + 8 new).

```
.venv/bin/python -m unittest tests.test_m2_6b4m_self_echo_probe tests.test_bargein_m2_5b -q
```
→ **112 tests, OK.**

```
.venv/bin/ruff check tests/test_m2_6b4m_self_echo_probe.py \
  docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py
```
→ **All checks passed.**

`--dry` re-checked after every source edit: unchanged, still passes.

Per instruction ("Run relevant focused tests and lint only, unless
repo instructions require another gate"), the full project suite was
**not** re-run this checkpoint — no other repo gate required it for a
probe-only, `src/nexa`-untouched change, and its one known failure was
already confirmed pre-existing in R0058 via a direct baseline
comparison.

`git diff --stat -- src/nexa`: **empty**, confirmed against `4e72a60`.

## PART 2 — GAIN A/B EXPERIMENT PROCEDURE PREPARED (NOT EXECUTED)

Full procedure, exact commands, invalid-run criteria, and comparison
methodology are in the dedicated, tracked document
`docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
(summarized here; that document is the authoritative, reviewable copy).
The execution wrapper is a real, committed (but not executed, not
`+x`) script,
`docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh`.

### Device/control identities — verified, not guessed

Read-only this checkpoint, from the live hardware:

```
$ cat /proc/asound/cards
 2 [Array          ]: USB-Audio - reSpeaker XVF3800 4-Mic Array
 3 [UACDemoV10     ]: USB-Audio - UACDemoV1.0

$ amixer -c Array cget numid=6
numid=6,iface=MIXER,name='PCM Playback Volume',index=1
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=60,step=0
  : values=40
  | dBminmax-min=-60.00dB,max=0.00dB

$ amixer -c UACDemoV10 cget numid=3
numid=3,iface=MIXER,name='PCM Playback Volume'
  ; type=INTEGER,access=rw---R--,values=2,min=0,max=147,step=0
  : values=147,147
  | dBminmax-min=-28.37dB,max=-0.94dB
```

`Array 'PCM',1'` (card `Array`, numid=6) is confirmed **linear, 1dB per
raw step**, range exactly `[-60.00dB, 0.00dB]` — raw `40` = `-20.00dB`
(the current, already-live, accepted baseline — matches every reading
since R0052) and raw `60` = **exactly `0.00dB`**, the actual top of the
control's own range, not an approximation. `UACDemoV10` (numid=3) is
confirmed at its own verified MAX (`147,147` = `-0.94dB`, its device's
own maximum, not `0.00dB`) — **never written by this procedure.**

### Procedure summary

- **Condition A:** `amixer -c Array sset 'PCM',1 40` (−20dB, the
  current baseline).
- **Condition B:** `amixer -c Array sset 'PCM',1 60` (0dB, top of
  range).
- **Each condition, ONE invocation:**
  `--warmup-seconds 60 --level max --repeats 3 --capture-pcm
  --max-lag-ms 500` — a FRESH warm-up per condition, never a reuse of
  R0059's own separate warm-up-only run.
- **Rollback:** `amixer -c Array sset 'PCM',1 40`, unconditionally, via
  a bash `trap ... EXIT INT TERM` in the wrapper script — success,
  failure, or interruption — with an independent `amixer sget`
  readback immediately after, plus a `UACDemoV10` readback confirming
  it was never touched.
- **Supervision:** `timeout --signal=TERM --kill-after=15s 240s
  <probe>` — estimated real duration ~100–105s (arithmetic shown in the
  procedure doc), leaving ~135s margin; termination limited to the
  probe's own child process, no broad process-kill.
- **Approval gate:** the script requires a literal
  `--i-have-explicit-operator-approval` argument — verified this
  checkpoint (read-only; no hardware touched) that every guard path
  (missing flag, wrong condition letter, no arguments) exits with
  usage text before any `amixer` call:

```
$ bash run_r0057_gain_ab_condition.sh
Usage: run_r0057_gain_ab_condition.sh <A|B> --i-have-explicit-operator-approval
...
EXIT=2
$ bash run_r0057_gain_ab_condition.sh A
(same usage/EXIT=2 -- missing the approval flag)
$ bash run_r0057_gain_ab_condition.sh X --i-have-explicit-operator-approval
(same usage/EXIT=2 -- invalid condition letter)
```

Mixer values were re-read (`amixer -c Array sget 'PCM',1` /
`amixer -c UACDemoV10 sget PCM`) immediately after these guard-path
checks and confirmed byte-for-byte unchanged (`-20.00dB` / `-0.94dB`).

- **Invalid-run criteria** (discard/investigate, do not compare):
  `WarmupIncompleteError`/`RunnerShutdownError` raised;
  `SHUTDOWN_OUTCOME` shows `failed: True`; nonzero exit code or an
  external-supervisor-forced termination; a nonzero
  `chunks_dropped_delta`/`failure_count_delta` in the warm-up's own
  telemetry (this checkpoint's own Part 1 fix is what makes this
  criterion checkable at all); either mixer reading differently than
  expected at any checkpoint; missing results JSON or `--capture-pcm`
  WAV artifacts. **A confirmed self-barge-in during a measured silent
  trial is explicitly NOT invalid** — it is the outcome being measured.
- **Comparison methodology:** report every trial separately (never
  pre-aggregated) — confirmed/candidate/rejected counts,
  `playback_duration_ms` (to account for unequal exposure when a
  barge-in truncates a trial), the new `aec_reference_telemetry`,
  `cross_correlation` when present, `reference_gain_applied`. Explicit
  limits stated in the procedure doc itself: 3 trials/condition is not
  statistically sufficient to prove universal reliability of either
  setting; the result must not be read as "−20dB is faulty" or "0dB is
  a fix."

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`:
  `_aec_reference_snapshot`, `_aec_reference_telemetry_delta` (new,
  pure); `build_probe_pipeline` returns `aec_feeder` too;
  `_run_warmup`/`_run_silent_trial`/`_run_control_trial` gained
  optional `aec_feeder`/`aec_health` kwargs and now report
  `aec_reference_telemetry`; `_run()` threads them through and prints
  the new telemetry; corrected `Recorder.ref_accepted_bytes`'s and
  `_PlaybackWatcher`'s own docstrings for exact-tap-location accuracy
  (no behavior change).
- `tests/test_m2_6b4m_self_echo_probe.py`: new `TestAecReferenceTelemetry`
  (7 tests) and two new `TestRunWarmup` integration tests.
- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md`
  (new, tracked): the full, reviewable procedure.
- `docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh`
  (new, tracked, not executable, not executed): the prepared execution/
  rollback wrapper, approval-gated.
- This report (`R0060`).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (updated after this
  report, per repo convention).

**No `src/nexa/**` file touched** (confirmed empty diff against
`4e72a60`). **No XVF3800 parameter, no ALSA mixer, changed** — every
`amixer`/`aplay -l`/`/proc/asound/cards` command run this checkpoint
was a bare read; the two guard-path script invocations exited before
any `amixer sset`.

## VERIFIED FACTS

1. `AecReferenceFeeder`/`AecReferenceHealth` expose exactly
   `chunks_dropped`, `respawns`, `failure_count`, `active`,
   `ever_started` as existing, reusable evidence — no per-write error
   counter exists beyond `failure_count`.
2. `aec_feeder` was previously unreachable from `_run()` — now returned
   and threaded through; every existing call site is backward
   compatible (verified: all 60 pre-existing tests pass unchanged).
3. `ref_accepted_bytes`'s own prior "proof of ACCEPTANCE" docstring
   overstated what the tap proves — corrected to "proof of passage
   through the feeder's own acceptance logic," distinct from a
   successful write (`frames_mirrored`/`bytes_mirrored`) and from
   physical hardware ingestion (unprovable by any software signal).
4. `Array 'PCM',1'` (numid=6) is linear, 1dB/step, exactly
   `[-60.00dB, 0.00dB]` — raw 40 = -20.00dB (current), raw 60 = exactly
   0.00dB (condition B's actual target, not approximate).
5. `UACDemoV10` (numid=3) sits at its own verified MAX (147,147 =
   -0.94dB) — confirmed unchanged before and after every check this
   checkpoint performed.
6. The prepared script's approval-gate and argument-validation paths
   exit before any hardware write, for every tested invalid invocation.

## HYPOTHESES

None outstanding blocking approval. Whether the gain difference
measurably affects false-barge-in rate remains the experiment's own
open question — explicitly not pre-judged by this checkpoint's
procedure text (§5/§6 of the procedure doc).

## UNRESOLVED

- The R0057 gain A/B experiment itself remains **NOT EXECUTED** —
  awaiting Andrzej's explicit approval.
- `AecReferenceFeeder.chunks_dropped`/`AecReferenceHealth.failure_count`
  are now OBSERVABLE (this checkpoint's own fix) but not yet exercised
  against real nonzero values on hardware — the fix's own correctness
  is proven by pure/offline tests and by threading verification, not
  yet by a real-hardware drop/failure event (none occurred in R0059's
  own clean run, nor would be expected to for a healthy feed).
- The genuinely-wedged-`run_task` escalation path in `_shutdown_runner`
  remains untested on real hardware (unchanged from R0058/R0059's own
  note).

## ARCHITECTURE IMPACT

None. This checkpoint touches only a diagnostic research script, its
own test file, and two new research-directory documents (a procedure
doc and a shell script). `BargeInController` remains the sole
interruption authority; measured trials still construct it armed
exactly as before (unchanged by this checkpoint's diff — no measured
trial was run this checkpoint). `ConversationSession` remains canonical
authority (untouched). Gemini/Pipecat boundary unchanged.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO (installed `alsa-utils`/`amixer`, `/proc/asound/cards`, and the
already-installed `src/nexa/voice_tts/aec_reference.py`/`src/nexa/voice
/aec.py` sources — all already present in this environment, read this
checkpoint; no new external research).

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/m2_6_cloud_realtime_voice/R0057_gain_ab_experiment_procedure.md` (new, tracked).
- `docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh` (new, tracked).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (this checkpoint).
- This report (`R0060`). `R0057`, `R0058`, `R0059` identities are
  unchanged — none renamed or renumbered.

## CURRENT VERIFIED STATE

- Branch `main`. `Array 'PCM',1'` = -20.00dB, `UACDemoV10` = -0.94dB —
  unchanged throughout this entire checkpoint, verified read-only
  repeatedly.
- **Preparation only — the R0057 gain A/B experiment remains NOT
  EXECUTED.** `M2.6B` remains IN PROGRESS.
- Post-commit git status: see GIT STATUS below (observed, not
  predicted).

## NEXT RECOMMENDED ACTION

Obtain Andrzej's explicit approval to run
`run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval`,
inspect condition A's own results/log against the procedure's own
invalid-run criteria, and only then request approval for condition B.
Do not run both conditions unattended in one session.

## TESTS

- `tests/test_m2_6b4m_self_echo_probe.py` → **68/68 pass** (60 + 8 new).
- `tests/test_bargein_m2_5b.py` → **44/44 pass** (unchanged).
- `ruff check` on all touched Python files: clean.
- `git diff --check`: clean.
- `git diff --stat -- src/nexa`: empty.
- `bash -n` on the new script: clean; guard-path invocations verified
  to exit before any hardware write.
- Full project suite: not re-run this checkpoint (no repo gate
  required it for this probe-only change; see WHAT I VERIFIED).

## GIT STATUS

Working tree **CONFIRMED clean** — `git status --short` returned empty
output immediately after the commit below. Not pushed. No Gemini call.
No hardware parameter changed.

## COMMIT HASHES

- `e8858d6` — feat: R0060 reference-observability fix and gain A/B experiment procedure preparation (M2.6B.4N follow-up)
