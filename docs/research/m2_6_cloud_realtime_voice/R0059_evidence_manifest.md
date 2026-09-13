# R0059 Evidence Manifest — Full 60-Second Baseline Warm-up Validation

This file is TRACKED. The raw log and JSON it describes are NOT — they
live under `docs/research/m2_6_cloud_realtime_voice/self_echo_captures/`,
excluded entirely by `.gitignore:47`
(`docs/research/m2_6_cloud_realtime_voice/self_echo_captures/`),
including its `warmup_hang_logs/` subdirectory (confirmed via
`git check-ignore -v` on both paths below, each resolving to that same
rule). This is the third such manifest in this thread — see also
`R0058_evidence_manifest.md` for the immediately preceding checkpoint's
own evidence.

## Pre-conditions (read-only, before this checkpoint's own hardware run)

```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```

Matches the expected baseline exactly. No leftover
`m2_6b4m_self_echo_probe`/`aplay` process before the run.

## Evidence (this checkpoint)

| File | SHA-256 | Size |
|---|---|---|
| `self_echo_captures/warmup_hang_logs/run5_baseline60_verification_20260913T150149Z.log` | `1e9c29970f40f0fd4ebffb78f0984f2bd048c49db0a6972f13303d3390003bb2` | 23126 bytes |
| `self_echo_captures/self_echo_probe_20260913T150301Z.json` | `05dae276149b45ea1b07d941aab84bf537f4b7e20bbe3a5137a4785fd11c9da6` | 591 bytes |

### Exact command

```bash
timeout --signal=TERM --kill-after=10s 150s \
  .venv/bin/python -u docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --warmup-seconds 60 --level max --repeats 0 \
  > docs/research/m2_6_cloud_realtime_voice/self_echo_captures/warmup_hang_logs/run5_baseline60_verification_20260913T150149Z.log 2>&1
```

Deliberately a fresh 150s/10s-grace external bound, NOT a reuse of the
prior checkpoint's 60s/10s bound (that bound matched a 1-second warm-up
target; a 60-second warm-up target needs materially more wall-clock
room for the 18 repeats × ~3.5s fixture, VAD settle time, and shutdown).

**Exit status: `0`** (natural completion; total wall time
16:01:49.368 → 16:03:01.449 UTC ≈ 72.1s, well inside the 150s bound —
the bound was never exercised).

### Decisive excerpt

```
WARMUP_RETURNED {'requested_seconds': 60.0, 'delivered_bytes': 2018304,
                  'ref_accepted_bytes': 2018304, 'playback_start_count': 18,
                  'playback_stop_count': 18, 'repeats_run': 18,
                  'interrupt_confirmed_delta': 0}
  warmup: repeats_run=18 queued_s=63.07 accepted_s=63.07 (requested >= 60.0s)
          playback_start_count=18 playback_stop_count=18 interrupt_confirmed_delta=0
ENTERING_TRIAL_LOOP

Operator: set the REAL speaker volume to 'MAX' now, then remain completely SILENT.

RUNNER_END_BEGIN
2026-09-13 16:03:01.315 DEBUG WorkerRunner 'runner-0cdc6ddf': ending gracefully (reason=probe done)
RUNNER_END_OUTCOME=clean
RUN_TASK_AWAIT_BEGIN
2026-09-13 16:03:01.315 DEBUG Worker 'PipelineWorker#0': received cancel
2026-09-13 16:03:01.315 DEBUG Cancelling pipeline worker PipelineWorker#0
2026-09-13 16:03:01.315 DEBUG PipelineWorker#0: Closing. Waiting for CancelFrame#0(reason: runner exiting) to reach the end of the pipeline...
2026-09-13 16:03:01.316 DEBUG PipelineWorker#0: CancelFrame#0(reason: runner exiting) reached the end of the pipeline.
2026-09-13 16:03:01.448 DEBUG Pipeline worker PipelineWorker#0 is finishing...
2026-09-13 16:03:01.449 DEBUG Pipeline worker PipelineWorker#0 has finished
2026-09-13 16:03:01.449 DEBUG WorkerRunner 'runner-0cdc6ddf': finished running
RUN_TASK_AWAIT_OUTCOME=clean
SHUTDOWN_OUTCOME={'runner_end': 'clean', 'run_task_await': 'clean', 'escalated': False, 'failed': False}

==========================================================================
SILENT 'MAX' RESULT: 0 false confirmed barge-in(s) across 0 trials (expect 0)
results JSON: .../self_echo_probe_20260913T150301Z.json
This probe does NOT choose a fix -- it only measures.
EXIT_CODE=0
```

### Success criteria checked against this run

| Criterion | Result |
|---|---|
| Requested warm-up PCM duration met | `accepted_s=63.07 >= 60.0` ✅ |
| `playback_start_count == playback_stop_count == repeats_run` | `18 == 18 == 18` ✅ |
| Zero confirmed warm-up interruptions | `interrupt_confirmed_delta=0` ✅ |
| Zero measured trials | `trials: []`, `--repeats 0` ✅ |
| Clean shutdown without escalation | `SHUTDOWN_OUTCOME={..., 'escalated': False, 'failed': False}` ✅ |
| Natural exit code 0 | `EXIT_CODE=0` ✅ |
| No leftover probe-owned processes | `ps aux \| grep -i "m2_6b4m_self_echo_probe\|aplay"` → empty ✅ |
| Unchanged mixer readings afterward | `-20.00dB` / `-0.94dB`, identical before/after ✅ |

### Repeat-count derivation (not assumed)

```
$ python3 -c "
import wave
with wave.open('docs/research/m2_voice_spikes/asr_test_samples/en_explain_gravity.wav','rb') as wf:
    rate = wf.getframerate(); n = wf.getnframes()
    print(rate, n, n/rate)
"
16000 56064 3.504
```
Fixture: 3.504s @ 16000Hz. Looping `_run_warmup`'s own
`while delivered_bytes < target_bytes` condition for a 60s target
requires exactly 18 whole repeats (18 × 3.504s = 63.072s ≥ 60s; 17 ×
3.504s = 59.568s < 60s) — matching the run's own observed
`repeats_run=18` exactly, derived from the actual fixture, not assumed.

## Post-conditions (read-only, after this checkpoint's own hardware run)

```
Array 'PCM',1'  : 40 [67%] [-20.00dB] [on]
UACDemoV10 PCM,0: 147 [100%] [-0.94dB] [on]  (both channels)
```

Identical to pre-conditions. No leftover process after the run.

## Evidence limitation carried forward for the eventual gain A/B readiness assessment

`ref_accepted_bytes` (and this run's own `accepted_s=63.07 >= 60.0`
proof) is evidence that reference PCM reached and was accepted by
`AecReferenceFeeder`'s own internal queue (the post-`aec_feeder` tap,
confirmed by source in `_PlaybackWatcher`'s own docstring) — it is
**NOT** proof that the physical `plug:respeaker` ALSA device, or the
XVF3800's own reference input, actually ingested or reproduced that
PCM, and it does **NOT** exclude an internal drop inside
`AecReferenceFeeder`'s own writer path (its own drop-oldest queue,
`chunks_dropped`, is still not read by any instrumentation in this
probe — an open gap, unchanged by this checkpoint). Likewise,
`playback_start_count`/`playback_stop_count` are logical
`BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` signals driven by
`TTSAudioRawFrame`/`TTSStoppedFrame` control markers passing through
Pipecat's own single-consumer output frame queue (confirmed this
checkpoint by reading `pipecat/transports/base_output.py`'s
`_handle_frame`/`_bot_started_speaking`/`_bot_stopped_speaking`) — they
prove the FRAME-LEVEL pipeline correctly produced one start and one
stop per repeat, not that the physical speaker cone finished
reproducing the sound at that exact instant (there is still OS/ALSA
buffering and DAC latency after a frame is handed to
`_internal_write_audio_frame`). Both signals are real, software-level,
production-identical evidence — not physical-acoustic proof. This
limitation was already true before this checkpoint; it is restated
here explicitly, as instructed, for whoever assesses A/B readiness.
