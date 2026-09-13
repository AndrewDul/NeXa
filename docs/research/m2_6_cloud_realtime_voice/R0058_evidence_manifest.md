# R0058 Evidence Manifest — Warm-up Lifecycle Fix Verification

This file is TRACKED. The raw logs and JSON it describes are NOT — they
live under `docs/research/m2_6_cloud_realtime_voice/self_echo_captures/`,
which `.gitignore:47` (`docs/research/m2_6_cloud_realtime_voice/self_echo_captures/`)
excludes entirely, including its `warmup_hang_logs/` subdirectory
(confirmed this checkpoint via `git check-ignore -v` on every path
listed below — each one resolves to that same `.gitignore:47` rule).
This manifest exists so the essential evidence (hashes, sizes, exact
commands, exit statuses, and the decisive excerpts) survives even
though the raw captures themselves are, by long-standing convention in
this repo, never committed (real audio-adjacent telemetry).

## Pre-existing evidence (from the prior same-day R0057 diagnostic checkpoint, unchanged by this one)

| File | SHA-256 | Size |
|---|---|---|
| `self_echo_captures/warmup_hang_logs/run1_warmup60_repeats3_20260913T123841Z.log` | `a35808079f690b2e46c63a9e3baf630b778877afcfa2527eadc471a816a81c44` | 19138 bytes |
| `self_echo_captures/warmup_hang_logs/run2_warmup10_repeats0_20260913T131050Z.log` | `509f027ab395629b1d7f6f12766c7905bfd16e4dd95bfa511c44457cefef38d5` | 10623 bytes |
| `self_echo_captures/warmup_hang_logs/run3_warmup1_repeats0_20260913T135447Z.log` | `1481f04d9680bc71d8f5b075c1293bd2e29156323b9bd6e0c2eaffb4e3689717` | 11315 bytes |

`run3` is the DEFINITIVE pre-fix reproduction: full traceback captured,
the `AssertionError` at the old `m2_6b4m_self_echo_probe.py:1393`, and
the subsequent teardown stall requiring external termination (exit
124). Its decisive excerpt is already fully quoted, with timestamps,
in `docs/reports/R0057_warmup_lifecycle_diagnostic_initiating_exception_20260913.md`
("COMPLETE PRESERVED REPRODUCTION LOG"). Not re-quoted here.

## New evidence (this checkpoint, R0058 — fix verification)

| File | SHA-256 | Size |
|---|---|---|
| `self_echo_captures/warmup_hang_logs/run4_fix_verification_warmup1_repeats0_20260913T143633Z.log` | `b3c4e87f86b821e753f88ef7778398758347ce53524bed823e67a914ef2f21bb` | 12610 bytes |
| `self_echo_captures/self_echo_probe_20260913T143643Z.json` | `072aa2177200e1f1e767292012035d44de702562f8c7639ca139c7f3c73bb982` | 585 bytes |

### Exact command (run4)

```bash
timeout --signal=TERM --kill-after=10s 60s \
  .venv/bin/python -u docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --warmup-seconds 1 --level max --repeats 0 \
  > docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs/run4_fix_verification_warmup1_repeats0_20260913T143633Z.log 2>&1
```

**Exit status: `0`** (natural completion — the process was NOT
terminated by the external `timeout` supervisor; the 60s/10s bound was
never exercised).

### Hardware mixer readings (read-only, before and after — identical)

```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```

### Process check after the run

`ps aux | grep -i "m2_6b4m_self_echo_probe\|aplay"` → no output (no
leftover probe-owned or `aplay` process).

### Decisive excerpt from run4 (full file preserved locally; key lines below)

```
2026-09-13 15:36:37.119 DEBUG Bot started speaking
2026-09-13 15:36:39.643 DEBUG VADProcessor#0: User stopped speaking
2026-09-13 15:36:40.503 DEBUG VADProcessor#0: User started speaking
2026-09-13 15:36:40.767 DEBUG Bot stopped speaking based on TTSStoppedFrame
2026-09-13 15:36:40.767 DEBUG Bot stopped speaking
WARMUP_RETURNED {'requested_seconds': 1.0, 'delivered_bytes': 112128,
                  'ref_accepted_bytes': 112128, 'playback_start_count': 1,
                  'playback_stop_count': 1, 'repeats_run': 1,
                  'interrupt_confirmed_delta': 0}
  warmup: repeats_run=1 queued_s=3.50 accepted_s=3.50 (requested >= 1.0s)
          playback_start_count=1 playback_stop_count=1 interrupt_confirmed_delta=0
ENTERING_TRIAL_LOOP

Operator: set the REAL speaker volume to 'MAX' now, then remain completely SILENT.

RUNNER_END_BEGIN
2026-09-13 15:36:43.784 DEBUG WorkerRunner 'runner-a82f0e0d': ending gracefully (reason=probe done)
RUNNER_END_OUTCOME=clean
RUN_TASK_AWAIT_BEGIN
2026-09-13 15:36:43.784 DEBUG Worker 'PipelineWorker#0': received cancel
2026-09-13 15:36:43.784 DEBUG Cancelling pipeline worker PipelineWorker#0
2026-09-13 15:36:43.785 DEBUG PipelineWorker#0: Closing. Waiting for CancelFrame#0(reason: runner exiting) to reach the end of the pipeline...
2026-09-13 15:36:43.786 DEBUG PipelineWorker#0: CancelFrame#0(reason: runner exiting) reached the end of the pipeline.
2026-09-13 15:36:43.912 DEBUG Pipeline worker PipelineWorker#0 is finishing...
2026-09-13 15:36:43.912 DEBUG Pipeline worker PipelineWorker#0 has finished
2026-09-13 15:36:43.912 DEBUG WorkerRunner 'runner-a82f0e0d': finished running
RUN_TASK_AWAIT_OUTCOME=clean
SHUTDOWN_OUTCOME={'runner_end': 'clean', 'run_task_await': 'clean', 'escalated': False, 'failed': False}

==========================================================================
SILENT 'MAX' RESULT: 0 false confirmed barge-in(s) across 0 trials (expect 0)
results JSON: .../self_echo_probe_20260913T143643Z.json
This probe does NOT choose a fix -- it only measures.
EXIT_CODE=0
```

**Contrast with run3 (pre-fix, same `--warmup-seconds 1 --level max
--repeats 0` invocation):** `playback_start_count=1,
playback_stop_count=0` → `AssertionError` → orphaned `run_task` →
`"got cancelled from outside"` → indefinite teardown stall → forced
termination, exit 124. **run4 (post-fix, identical invocation):**
`playback_start_count=1, playback_stop_count=1` (the bounded wait in
`_run_warmup` now actually waited for the real
`BotStoppedSpeakingFrame`) → `WARMUP_RETURNED` with fully matched
counters → `ENTERING_TRIAL_LOOP` → zero measured trials (`--repeats 0`)
→ `RUNNER_END_OUTCOME=clean` → `RUN_TASK_AWAIT_OUTCOME=clean` →
`SHUTDOWN_OUTCOME={..., 'failed': False}` → natural exit 0.

## Provenance check: `test_tts_server.TestPiperNiceRealChildPriority.test_nice_wrapper_actually_sets_child_priority`

Compared directly, in the SAME shell/process-priority environment (this
shell's own base `os.nice(0)` reads `5`, not `0` — an environment/sandbox
characteristic, not something either commit controls), using a detached
git worktree at the baseline commit `1e38c55` (removed after the
comparison; the main working tree was never touched):

```
$ python3 -c "import os; print(os.nice(0))"
5

# baseline commit 1e38c55, via `git worktree add --detach /tmp/nexa_baseline_check 1e38c55`
$ cd /tmp/nexa_baseline_check
$ /home/devdul/Projects/NeXa_IkiGai/.venv/bin/python -m unittest \
    tests.test_tts_server.TestPiperNiceRealChildPriority.test_nice_wrapper_actually_sets_child_priority -v
...
AssertionError: '15' != '10'
FAILED (failures=1)

# current revision (this checkpoint), same shell, same venv
$ /home/devdul/Projects/NeXa_IkiGai/.venv/bin/python -m unittest \
    tests.test_tts_server.TestPiperNiceRealChildPriority.test_nice_wrapper_actually_sets_child_priority -v
...
AssertionError: '15' != '10'
FAILED (failures=1)
```

**VERIFIED FACT** (not merely asserted from an unrelated `git diff`):
identical failure at the baseline commit and at this checkpoint's own
revision, under the same process-priority conditions. `git diff --stat
-- tests/test_tts_server.py` between `1e38c55` and this checkpoint's
own commit is also empty (the file is untouched). The failure's root
cause is this sandbox's own base nice level (`5`, not the usual `0`)
combined with the test's own use of a RELATIVE `nice -n 10` offset
against an assumed `0` baseline — an environment characteristic,
**pre-existing and unrelated to this checkpoint's changes**, not a
regression introduced or a latent bug newly exposed by this checkpoint.
Not fixed here (explicitly out of scope).
