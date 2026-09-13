# R0057 Gain A/B Experiment — Procedure Ready for Explicit Operator Approval

**Status: PREPARED, NOT EXECUTED. No hardware parameter has been written.
No probe run has been started under this procedure. This document, and
the companion script it describes
(`run_r0057_gain_ab_condition.sh`, same directory), require Andrzej's
explicit, separate approval before either condition is run.**

This procedure was originally designed in R0056/R0057 (`docs/reports/
R0056_existing_acoustic_solutions_and_portable_frontend_design_20260913.md`,
`docs/reports/R0057_warmup_lifecycle_diagnostic_initiating_exception_20260913.md`)
and finalized here (R0060) once the probe's own warm-up-completion and
runner-cleanup bugs were fixed and verified at full scale (R0058, R0059)
and its reference-observability gap was closed (R0060).

## 1. What this tests

Whether the reference-injection gain mismatch R0056 found (`Array
'PCM',1` fixed at −20dB while `AEC_FAR_EXTGAIN` auto-tracks it, but the
audible speaker's own independent gain does not track the same value)
measurably affects the XVF3800's own AEC cancellation quality, using
the SAME real, production-identical self-echo probe already used in
every prior R0052–R0059 checkpoint — never a new discriminator, never
custom DSP.

**This procedure does NOT assume an outcome.** −20dB is the currently
accepted, already-live setting; 0dB is the top of the control's own
supported range. Neither is assumed correct or faulty going in — see
§5.

## 2. Verified device/control identities (read-only, this checkpoint — not guessed)

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

- **`Array 'PCM',1'`** (card `Array`, index 2; simple-mixer name `'PCM',1`;
  numid=6) — raw range `0–60`, **linear 1dB/step**, `dBminmax
  min=-60.00dB max=0.00dB`. Raw `40` = `40−60 = -20.00dB` exactly
  (matches every prior reading in R0052–R0059). Raw `60` = **exactly
  `0.00dB`** — the actual top of the control's own supported range, not
  an approximation.
- **`UACDemoV10 PCM`** (card `UACDemoV10`, index 3; simple-mixer name
  `'PCM',0`, addressed as plain `PCM`; numid=3) — raw range `0–147`,
  current value `147,147` (both channels) = `-0.94dB`, its own verified
  **MAX** (the device's own maximum happens to be `-0.94dB`, not
  `0.00dB` — a real hardware property, unchanged since R0052). This is
  the "verified MAX baseline" referenced throughout this procedure —
  **never written by this procedure, only read for confirmation.**

Card names (`Array`, `UACDemoV10`), not numeric indices, are used in
every command below — `amixer -c <name>` resolves the same device
regardless of USB (re-)enumeration order across a reboot, which a bare
numeric index would not survive reliably.

## 3. Exact mixer commands

**Read (before/after every step, both conditions, and rollback verification):**
```bash
amixer -c Array sget 'PCM',1
amixer -c UACDemoV10 sget PCM
```

**Write condition A (baseline, −20dB):**
```bash
amixer -c Array sset 'PCM',1 40
```

**Write condition B (top of range, 0dB, "the actual supported value"):**
```bash
amixer -c Array sset 'PCM',1 60
```

**Rollback (after either condition, on success, failure, or interruption):**
```bash
amixer -c Array sset 'PCM',1 40
```

`UACDemoV10` is never written by any step of this procedure.

## 4. Execution wrapper (prepared, not run)

`run_r0057_gain_ab_condition.sh` (same directory) implements the above
plus supervision and rollback, gated behind an explicit
`--i-have-explicit-operator-approval` flag. Guard paths (missing flag,
wrong condition letter) were verified this checkpoint to exit before
touching any hardware — see R0060's own report. **The script's full
source is the actual, final, reviewable content — nothing here
paraphrases it.**

Run ONE condition at a time:
```bash
bash docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval
# inspect condition A's own results/log against §6 BEFORE proceeding
bash docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh B --i-have-explicit-operator-approval
```

Each invocation runs the probe **exactly once**:
```bash
.venv/bin/python -u docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --warmup-seconds 60 --level max --repeats 3 \
  --capture-pcm --max-lag-ms 500
```

**R0059's own already-completed 60-second warm-up validation is NOT
reused as condition A's warm-up.** Each condition's warm-up is freshly
run as part of that condition's own single invocation (combined
warm-up + 3 measured trials in one process, one log, one JSON) — R0059
proved the mechanism works at this scale; it did not, and could not,
stand in for either condition's own measured-trial data.

**Preconditions (operator checklist, unchanged between conditions):**
identical fixture (`docs/research/m2_voice_spikes/asr_test_samples/
en_explain_gravity.wav`, the probe's own default — do not pass `--wav`
or `--synthesize-piper`), identical committed probe/test revision (this
checkpoint's own commit, unchanged between A and B), identical physical
microphone/speaker placement and room conditions, identical
`UACDemoV10` MAX setting confirmed via the precheck read.

### Timeout/supervision budget (shown, not merely asserted)

Estimated real duration for one condition: ~3–4s startup settle +
~63s warm-up (18 repeats × 3.504s fixture, matching R0059's own
measured ~63.07s) + 3s pre-trial operator-volume pause + up to
~3×~10s for 3 measured trials (each ≤ ~2s quiet + ≤ ~3.5s playback,
possibly truncated by a real confirmed barge-in + ≤ 3s tail + 1s gap)
+ ~2s shutdown ≈ **~100–105s** total. The script's own external bound
(`timeout ... 240s`, `--kill-after=15s`) leaves roughly **135s of
margin** over that estimate — generous, not exact; if a condition's
own run genuinely needs the full 240s, that is itself an anomaly worth
investigating (see §5's invalid-run criteria), not a signal to simply
raise the bound and retry.

## 5. Invalid-run criteria

A run is **INVALID** (discard, investigate, do not fold into the
comparison) if ANY of the following hold:

- The probe raises `WarmupIncompleteError` (incomplete warm-up — the
  probe's own R0058 fix already enforces exact
  `playback_start_count == playback_stop_count == repeats_run` and
  full byte acceptance; any raise here means this specific run never
  produced trustworthy warm-up evidence).
- `RunnerShutdownError` is raised, or the log's own final
  `SHUTDOWN_OUTCOME` shows `'failed': True` (cleanup did not complete
  cleanly — R0058/R0059's own bounded-cleanup guarantee).
- The process exit code is not `0`, OR the external `timeout`
  supervisor had to terminate it (this script's own `EXIT_CODE`
  line will show a nonzero value; a `timeout`-forced exit is `124` or
  `137`/`143` depending on signal).
- A nonzero `chunks_dropped_delta` or `failure_count_delta` appears in
  the warm-up's own `aec_reference_telemetry` (R0060) — a queue
  overflow or a writer start/write failure during warm-up means the
  "≥60s ACCEPTED reference PCM" claim for that run is compromised,
  regardless of what the byte-count evidence alone shows.
- Either mixer reads differently than expected at any checkpoint read:
  `UACDemoV10` must read exactly `147,147` / `-0.94dB` at every
  precheck and postcheck; `Array 'PCM',1'` must read exactly the
  INTENDED condition's own raw value (`40` for A, `60` for B) after
  its own set-and-readback step, and exactly `40` after rollback.
- The expected results JSON
  (`self_echo_captures/self_echo_probe_<timestamp>.json`) and, since
  `--capture-pcm` is used, the per-trial mic/reference WAV pairs under
  `self_echo_captures/pcm/`, are missing after a claimed-successful run.

**Explicitly NOT invalid, per instruction:** a confirmed self-barge-in
during a MEASURED silent trial (`bargein_confirmed_count > 0` for a
`--level max` trial, i.e. `_run_silent_trial`, warm-up already excluded
by construction since `arm_bargein=False` there) is an **experimental
OUTCOME being measured**, not a run-invalidating defect. Do not discard
a trial, or a whole condition, merely because it recorded one or more
confirmed false barge-ins — that is the exact signal this experiment
exists to compare between conditions.

## 6. Comparison methodology

Report every trial **separately** — never pre-aggregated — for both
conditions, using fields the probe already emits per trial
(`summarize_trial`'s own output, `_run_silent_trial`'s own added
`aec_reference_telemetry`):

- `bargein_confirmed_count` (the false-barge-in signal itself),
  `bargein_candidate_count`, `bargein_rejected_count` — full
  VAD/candidate/rejection telemetry, not just the confirmed count.
- `playback_duration_ms` — the ACTUAL exposure that trial got. A trial
  truncated by a confirmed barge-in has LESS exposure than a
  full-length one; **do not compare raw confirmed-counts between a
  truncated and a full-length trial as if they had equal opportunity
  to (re-)trigger** — report duration alongside every count so a
  reader can judge exposure-adjusted rate, not just raw incidence.
- `aec_reference_telemetry` (`chunks_dropped_delta`,
  `respawns_delta`, `failure_count_delta`, `aec_ref_active_at_end`,
  `aec_ref_ever_started_at_end`) — per trial, per warm-up.
- `cross_correlation` (from `--capture-pcm --max-lag-ms 500`) when
  present — `best_lag_ms`/`normalized_correlation`, with R0055's own
  documented reference-offset search-boundary caveat carried forward
  unchanged (a `best_lag_ms` saturated at `-500` is a known search
  artifact, not necessarily "no real correlation").
- `reference_gain_applied` (proves the SAME production gain-coherence
  fix, R0053, stayed active throughout every trial in both conditions).

**Explicit interpretation limits (per instruction, not optional):**
3 trials per condition (6 total) is the SAME small-N design R0056/R0057
already accepted for this checkpoint's own scope — it is **not**
statistically sufficient to prove universal reliability of either
setting, and this procedure's own results must not be reported or
acted on as if it were. A clean run at one condition and a worse one at
the other is evidence toward a hypothesis, not proof; do **not**
conclude "−20dB is faulty" or "0dB is a fix" from this data alone —
that framing was explicitly rejected going in and must not be
introduced coming out. If the result is suggestive, the NEXT
recommended step is a larger, pre-registered trial count (matching the
5-per-level design R0052 already used successfully for the
volume/false-barge-in relationship), not an immediate hardware-setting
change.

## 7. Rollback / safety summary (already implemented in the script, restated here)

- Every mixer mutation is followed by an independent `amixer sget`
  readback in the SAME log, before the probe is ever started.
- A `trap ... EXIT INT TERM` rollback restores `Array 'PCM',1'` to `40`
  (−20dB) unconditionally — success, failure, or interruption — and
  reads it back independently afterward, plus a final `UACDemoV10`
  readback to confirm it was never touched.
- The external `timeout` supervisor bounds only the probe's own child
  process; the script issues no broad process-kill.
- No control other than `Array 'PCM',1'` is ever written by this
  procedure.
