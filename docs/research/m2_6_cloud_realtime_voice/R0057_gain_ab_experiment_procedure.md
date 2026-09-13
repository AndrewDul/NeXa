# R0057 Gain A/B Experiment — Procedure Ready for Explicit Operator Approval

**Status: PREPARED, NOT EXECUTED. No hardware parameter has been written.
No probe run has been started under this procedure. This document, and
the companion script it describes
(`run_r0057_gain_ab_condition.sh`, same directory), require Andrzej's
explicit approval before either condition is run.**

**On approval scope:** a single approval MAY cover the full A-then-B
experiment, conditional on condition A's own run being VALID (§5) before
condition B is run. This document does not require two separate
approvals, one per condition — but the script itself never chains A into
B automatically; each invocation runs exactly one condition, and whoever
is running it decides whether to proceed. No approval of any kind has
been given as of this writing.

This procedure was originally designed in R0056/R0057, finalized in
R0060 once the probe's own warm-up-completion/runner-cleanup bugs were
fixed (R0058, R0059) and its reference-observability gap was closed
(R0060), and **corrected here in R0061** after review found concrete
defects in the R0060 wrapper script itself — see
`docs/reports/R0061_gain_ab_wrapper_corrections_20260913.md` for the
full rationale. The experiment's own design (§1, §2, §3, §6) is
unchanged from R0060; §4, §5, and the wrapper script are corrected.

## 1. What this tests

Whether the reference-injection gain mismatch R0056 found (`Array
'PCM',1` fixed at −20dB while `AEC_FAR_EXTGAIN` auto-tracks it, but the
audible speaker's own independent gain does not track the same value)
measurably affects the XVF3800's own AEC cancellation quality, using
the SAME real, production-identical self-echo probe already used in
every prior R0052–R0060 checkpoint — never a new discriminator, never
custom DSP.

**This procedure does NOT assume an outcome.** −20dB is the currently
accepted, already-live setting; 0dB is the top of the control's own
supported range. Neither is assumed correct or faulty going in — see
§6.

## 2. Verified device/control identities (read-only — not guessed)

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
  (matches every prior reading in R0052–R0060). Raw `60` = **exactly
  `0.00dB`** — the actual top of the control's own supported range, not
  an approximation.
- **`UACDemoV10 PCM`** (card `UACDemoV10`, index 3; simple-mixer name
  `'PCM',0`, addressed as plain `PCM`; numid=3) — raw range `0–147`,
  current value `147,147` (both channels) = `-0.94dB`, its own verified
  **MAX**. This is the "verified MAX baseline" referenced throughout
  this procedure — **never written by this procedure, only read and
  VALIDATED (not merely eyeballed) for confirmation.**

Card names, not numeric indices, are used in every command — `amixer -c
<name>` resolves the same device regardless of USB (re-)enumeration
order across a reboot.

## 3. Exact mixer commands

**Read (parsed and validated by the script — never a bare print a human
must eyeball):**
```bash
amixer -c Array sget 'PCM',1
amixer -c UACDemoV10 sget PCM
```

**Write condition A (baseline, −20dB):** `amixer -c Array sset 'PCM',1 40`
**Write condition B (top of range, 0dB):** `amixer -c Array sset 'PCM',1 60`
**Rollback (unconditional, on every outcome):** `amixer -c Array sset 'PCM',1 40`

`UACDemoV10` is never written by any step of this procedure.

## 4. Execution wrapper (prepared, not run)

`run_r0057_gain_ab_condition.sh` implements the above plus supervision,
validated prechecks, and rollback, gated behind an explicit
`--i-have-explicit-operator-approval` flag. **The script's full source
is the actual, final, reviewable content — nothing here paraphrases
it.**

Run ONE condition at a time:
```bash
bash docs/research/m2_6_cloud_realtime_voice/run_r0057_gain_ab_condition.sh A --i-have-explicit-operator-approval
# inspect condition A's own results/log/archive against §5 BEFORE proceeding
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
run as part of that condition's own single invocation.

**Preconditions (operator checklist, unchanged between conditions):**
identical fixture (the probe's own default — do not pass `--wav` or
`--synthesize-piper`), identical committed probe/test revision, identical
physical microphone/speaker placement and room conditions, identical
`UACDemoV10` MAX setting (the script's own precheck now VALIDATES this,
not merely displays it — see §5).

### What the corrected wrapper actually does (R0061)

1. **Validated precheck, before any mutation:** parses (never just
   prints) `Array 'PCM',1` and both `UACDemoV10` channels; aborts with
   exit `90` on any mismatch or unreadable value, WITHOUT writing
   anything.
2. **Set + immediately verify:** writes the intended condition value,
   then re-reads and parses it to confirm the write actually took
   effect (catches a "succeeded but silently ineffective" write); aborts
   with exit `92` on mismatch, but still attempts rollback afterward.
3. **One persisted transcript:** every wrapper action (prechecks,
   set/readback, interruption, rollback/readback) AND the probe's own
   complete stdout/stderr live in the SAME log file under
   `self_echo_captures/warmup_hang_logs/gain_ab_condition_<A|B>_<ts>.log`.
4. **Exit-code convention** (documented in full in the script's own
   header): `0` fully successful; `2` usage error; `90` precheck
   failed; `92` condition write/readback failed; `91` rollback itself
   did not verify (overrides an otherwise-successful `0`); `130`/`143`
   interrupted (SIGINT/SIGTERM); otherwise the probe's own exit code
   (e.g. `1`, `124`) is preserved as this script's own exit code.
5. **Idempotent, verified rollback:** always attempted exactly once
   (guarded against double-execution across trap paths), restores
   `Array 'PCM',1'` to raw `40`, and INDEPENDENTLY reads it back to
   confirm — a rollback that does not verify is reported as its own,
   separate outcome (`ROLLBACK_OUTCOME=...` in the log) and forces a
   nonzero final exit even when the run itself otherwise succeeded.
6. **Explicit interruption handling:** SIGINT/SIGTERM are each handled
   distinctly, stop and reap ONLY this script's own child (the
   `timeout`-supervised probe — never a broad process-kill), and then
   run the SAME rollback path. **Explicit limitation, not hidden:** a
   SIGKILL sent to this wrapper itself, or a host power loss, cannot be
   intercepted by any shell trap — no unconditional rollback guarantee
   is claimed for those cases; an operator who kills -9 this script, or
   who loses power mid-run, must manually verify and, if needed,
   restore `Array 'PCM',1'` to raw `40` afterward.
7. **Per-run artifact archive:** immediately after a successful probe
   exit, the results JSON and `--capture-pcm` WAV pairs created DURING
   this specific run (found via a marker-file/`find -newer` scan — never
   "newest file by mtime", which could silently pick up a stale file
   left by an earlier, unrelated run) are copied, hash-verified, and
   moved into
   `self_echo_captures/gain_ab_experiment/<timestamp>_condition_<A|B>/`,
   with a `MANIFEST.txt` listing every archived file's SHA-256 and
   stating explicitly that the JSON and WAV files in that one directory
   all came from the SAME single invocation. This is what prevents
   condition B's own trial WAVs from overwriting condition A's — they
   no longer share a fixed-name path once each run's own artifacts are
   archived before the next run can start.

### Timeout/supervision budget (shown, not merely asserted)

Estimated real duration for one condition: ~3–4s startup settle + ~63s
warm-up (18 repeats × 3.504s fixture, matching R0059's own measured
~63.07s) + 3s pre-trial operator-volume pause + up to ~3×~10s for 3
measured trials + ~2s shutdown ≈ **~100–105s** total. The script's own
external bound (`timeout ... 240s`, `--kill-after=15s`) leaves roughly
**135s of margin** — generous, not exact; a run that genuinely needs
the full 240s is itself an anomaly worth investigating (§5), not a
signal to simply raise the bound and retry.

## 5. Invalid-run and validity-flag criteria (R0061: aligned with the corrected wrapper and the R0060 telemetry fix)

A run is **INVALID** (discard, investigate, do not fold into the
comparison) if ANY of the following hold:

- The wrapper's own final exit code is not `0` (covers: `90`
  precheck failed, `92` condition write/readback failed, `91` rollback
  did not verify, `93` this run's own artifact archive did not complete
  cleanly, `130`/`143` interrupted, or the probe's own preserved nonzero
  code including `124`/external-timeout).
- The probe raises `WarmupIncompleteError` (incomplete warm-up) or
  `RunnerShutdownError`, or the log's own `SHUTDOWN_OUTCOME` shows
  `'failed': True`.
- **Reference telemetry, checked for the WARM-UP window AND for EACH
  measured trial separately** (R0060 made per-trial
  `aec_reference_telemetry` available; this criterion now actually uses
  it, not just the warm-up's own): a nonzero `chunks_dropped_delta`,
  a nonzero `respawns_delta` (a full feed restart — the reference was
  NOT being fed at all for some interval, a materially worse condition
  than a single dropped chunk), or a nonzero `failure_count_delta`
  invalidates that window's own evidence. `aec_ref_active_at_end` must
  be `true` and `aec_ref_ever_started_at_end` must be `true` at the end
  of the warm-up and of every trial — a feed that is not confirmed
  active invalidates that window regardless of any other reading.
  **Missing telemetry (any field reading `None`/`null`) is UNKNOWN, and
  must be treated as UNKNOWN — never assumed to mean zero drops/failures
  or an inactive-but-otherwise-fine feed.** In the current, unmodified
  `_run()` wiring this should never actually happen (the real
  `aec_feeder`/`aec_health` objects are always passed), but the
  procedure states the rule explicitly rather than relying on that
  always holding.
- Any `WARNING: _ResponseLifecycle never fired 'finished' within
  timeout` line for a MEASURED trial (the probe's own existing
  `_run_silent_trial` lifecycle-timeout warning) is a validity FLAG:
  investigate that trial's own telemetry closely before including it —
  it does not automatically invalidate the trial, but it means the
  trial's own `_wait_for_finish` bound (20s) was exhausted rather than
  the response settling normally, which is itself worth explaining
  before trusting that trial's other numbers.
- Either mixer reads differently than expected at any checkpoint the
  script performs (it now aborts on this itself — see §4 — so this
  criterion is enforced by construction, not left to manual reading).
- The expected results JSON and `--capture-pcm` WAV pairs are missing
  from that run's own archive directory (also enforced by the wrapper
  itself via exit `93`).

**Explicitly NOT invalid, per instruction — restated and unchanged from
R0060:** a confirmed self-barge-in during a MEASURED silent trial
(`bargein_confirmed_count > 0` for a `--level max` trial; warm-up is
excluded by construction, `arm_bargein=False`) is an **experimental
OUTCOME being measured**, not a run-invalidating defect. Do not discard
a trial, or a whole condition, merely because it recorded one or more
confirmed false barge-ins — that is the exact signal this experiment
exists to compare between conditions. This is unaffected by, and
independent of, every telemetry/lifecycle criterion above — a trial can
be simultaneously "a confirmed false barge-in occurred" (an outcome,
keep it) and "a nonzero `chunks_dropped_delta` occurred in the SAME
trial" (a validity defect, discard it) — the two are checked and
reported separately, never conflated.

## 6. Comparison methodology (unchanged in substance from R0060; strengthened on correlation use)

Report every trial **separately** — never pre-aggregated — for both
conditions:

- `bargein_confirmed_count`, `bargein_candidate_count`,
  `bargein_rejected_count` — full VAD/candidate/rejection telemetry.
- `playback_duration_ms` — the ACTUAL exposure that trial got; do not
  compare raw confirmed-counts between a truncated and a full-length
  trial as if they had equal opportunity to (re-)trigger.
- `aec_reference_telemetry` — per trial, per warm-up (see §5's own
  invalidity use of the same fields; report the raw values regardless
  of whether they invalidated the window, so the comparison itself is
  transparent about what was excluded and why).
- `reference_gain_applied` (proves the R0053 gain-coherence fix stayed
  active throughout every trial in both conditions).
- **`cross_correlation`, with an explicit constraint (R0061): do NOT use
  the probe's own BUILT-IN `cross_correlation` field as evidence of
  low or absent echo.** R0055 already found and documented that this
  field's search is built on a "both signals start at t=0" assumption
  that is wrong by a fixed, known offset (`QUIET_BEFORE_S`), so a
  `best_lag_ms` saturated at the search boundary (e.g. `-500`) is a
  KNOWN ARTIFACT of that misalignment, not a measurement of low real
  correlation — treating a low/saturated reading here as "the echo was
  weak" would be actively misleading, not merely imprecise. The
  `--capture-pcm` WAV pairs this wrapper archives (hashed, mapped to
  their own JSON in each run's own `MANIFEST.txt`) exist SPECIFICALLY
  so the SAME PCM can be re-analyzed OFFLINE with R0055's own correct,
  offset-aware sliding-window correlation method — that re-analysis,
  not the probe's own built-in field, is what any A/B conclusion about
  echo/cancellation quality must be based on.

**Explicit interpretation limits (unchanged, per instruction, not
optional):** 3 trials per condition (6 total) is not statistically
sufficient to prove universal reliability of either setting. Do **not**
conclude "−20dB is faulty" or "0dB is a fix" from this data alone. If
the result is suggestive, the NEXT recommended step is a larger,
pre-registered trial count (matching R0052's own 5-per-level design),
not an immediate hardware-setting change.

## 7. Rollback / safety summary (implemented in the script; restated here)

- Every mixer mutation is followed by an independent, PARSED readback
  in the SAME persisted log, checked by the script itself, not left for
  a human to notice a mismatch.
- Rollback restores `Array 'PCM',1'` to `40` on every exit path this
  script can intercept (normal completion, any detected failure,
  SIGINT, SIGTERM) — idempotent, independently verified, and its own
  outcome is reported separately from the run's own outcome. **Not
  claimed for a SIGKILL of this wrapper or a power loss** — see §4.
- The external `timeout` supervisor bounds only the probe's own child
  process; on interruption the wrapper itself stops and reaps that same
  child before proceeding to rollback — no broad process-kill.
- No control other than `Array 'PCM',1'` is ever written by this
  procedure.
