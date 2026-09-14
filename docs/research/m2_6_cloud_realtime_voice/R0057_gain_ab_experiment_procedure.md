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
(R0060), corrected in R0061 after review found seven concrete defects
in the R0060 wrapper script, and **corrected again here in R0062**
after a second, deeper review found that R0061's own tests did not
actually prove several of its claims (zero-writes-on-failed-precheck,
final hardware-state validation, owned-process termination including a
SIGTERM-resistant descendant, exact archive completeness, storage-setup
failure handling) — see
`docs/reports/R0062_gain_ab_wrapper_deep_review_corrections_20260914.md`
for the full rationale, and the `**CORRECTED**` note in
`docs/reports/R0061_gain_ab_wrapper_corrections_20260913.md` for exactly
which of its claims were not substantiated. The experiment's own design
(§1, §2, §3, §6) is unchanged from R0060; §4 and §5 are corrected again
here, and the wrapper script is corrected again.

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

### What the corrected wrapper actually does (R0062, superseding R0061's own description below)

1. **Mutation-tracked, validated precheck:** parses (never just prints)
   `Array 'PCM',1` and both `UACDemoV10` channels, checking each
   control's exact identity line, its documented limits, and its
   `[on]` switch state — never accepting a coincidentally-matching raw
   integer from the wrong control as proof of anything. An explicit
   `MUTATION_ATTEMPTED` flag is set immediately BEFORE, and only
   before, the one condition write is issued — never earlier. On any
   precheck mismatch or unreadable value the script aborts (exit `90`)
   having issued **zero** `amixer sset` calls, verified by a dedicated
   offline test that records every fake invocation and asserts none of
   them is a write.
2. **Set + immediately verify:** writes the intended condition value,
   then re-reads and parses it (same identity/limits/switch validation
   as the precheck) to confirm the write actually took effect (catches
   a "succeeded but silently ineffective" write); aborts with exit `92`
   on mismatch, but still attempts rollback afterward since a write was
   attempted (`MUTATION_ATTEMPTED=1`).
3. **One persisted transcript:** every wrapper action (prechecks,
   set/readback, interruption, rollback/readback, archive) AND the
   probe's own complete stdout/stderr live in the SAME log file under
   `self_echo_captures/warmup_hang_logs/gain_ab_condition_<A|B>_<ts>.log`.
4. **Exit-code convention** (documented in full in the script's own
   header): `0` fully successful; `2` usage error; `90` precheck
   failed (zero writes issued); `92` condition write/readback failed;
   `91` rollback did not verify OR the independent final-hardware-state
   check (below) failed (overrides an otherwise-successful `0`); `93`
   this run's own artifact archive is not exactly complete; `94` a
   concurrent invocation already holds the capture-location lock; `95`
   required evidence-storage setup (directories, marker, manifest,
   persisted log) could not be created; `130`/`143` interrupted
   (SIGINT/SIGTERM); otherwise the probe's own exit code (e.g. `1`,
   `124`) is preserved as this script's own exit code.
5. **Rollback that only runs if a mutation was attempted, and validates
   BOTH sides of hardware state independently:** if `MUTATION_ATTEMPTED`
   is unset, rollback is explicitly SKIPPED (`ROLLBACK_SKIPPED: no
   mutation was attempted`) and issues no write at all — there is
   nothing to restore. If a mutation was attempted (even a possibly
   partial, failed one), rollback restores `Array 'PCM',1'` to raw `40`
   and INDEPENDENTLY reads it back to confirm (exit `91` on mismatch).
   Separately, and regardless of whether a mutation was attempted,
   rollback INDEPENDENTLY re-reads both `UACDemoV10` channels
   (identity/limits/switch included) to confirm they are still at their
   own verified MAX baseline — **the script never writes to
   `UACDemoV10` to correct a mismatch it finds here**; an unreadable or
   mismatched final UAC reading is reported (`UAC_FINAL_CHECK_FAILED`)
   and forces exit `91`, since it means the run's own environment
   cannot be trusted even if the Array side rolled back cleanly.
6. **Explicit interruption handling with verified process-group
   termination:** the probe is launched under its own dedicated
   `setsid`-created process group; on SIGINT/SIGTERM the wrapper sends
   SIGTERM to that OWNED group specifically (never a broad
   process-kill, never a PID guessed from an environment marker),
   polls for the group to actually empty, and escalates to SIGKILL only
   if a bounded grace period elapses with a member still alive —
   cleanup is reported only once the group is CONFIRMED empty, not
   merely once a signal was sent. The same group-termination safety net
   also runs after ordinary completion, in case the probe left an
   orphaned descendant. **Explicit limitation, not hidden:** a SIGKILL
   sent to this wrapper itself, or a host power loss, cannot be
   intercepted by any shell trap — no unconditional rollback guarantee
   is claimed for those cases; an operator who kills -9 this script, or
   who loses power mid-run, must manually verify and, if needed,
   restore `Array 'PCM',1'` to raw `40` afterward.
7. **Exact per-run artifact archive, with a concurrency lock:** a
   `mkdir`-based lock (exit `94` if already held) prevents two wrapper
   invocations from ever sharing the same capture location at all —
   `find -newer <marker>` inventories a run's own candidate files, but
   is never treated as proof of ownership by itself. Archive
   completeness for a claimed successful run requires EXACTLY one
   current-run results JSON reporting the expected trial count, plus
   EVERY expected mic/reference WAV pair by the probe's own real fixed
   naming scheme — missing, incomplete, or ambiguous evidence produces
   exit `93`, never a silent success. Whatever partial evidence DOES
   exist is still archived (never discarded) even on failure or
   interruption, without delaying the hardware-restoration steps above.
   Archiving copies, hash-verifies, and removes each original file into
   `self_echo_captures/gain_ab_experiment/<timestamp>_condition_<A|B>/`,
   with a `MANIFEST.txt` listing every archived file's SHA-256 AND an
   explicit mapping from each original JSON WAV path to its archived
   file and hash — condition A's own archived bytes are verified
   unchanged after condition B runs, since each run's fixed-name
   working files are fully cleared before the lock is released.
8. **Operational failures are checked, not swallowed:** creation of the
   evidence-storage directories, the run marker, the persisted log
   redirection, every archive copy/hash, and manifest writing are each
   explicitly checked; a failure at any of these aborts (exit `95` for
   storage/logging setup) rather than silently proceeding to a mutation
   with evidence storage not actually ready.

### Timeout/supervision budget (shown, not merely asserted)

Estimated real duration for one condition: ~3–4s startup settle + ~63s
warm-up (18 repeats × 3.504s fixture, matching R0059's own measured
~63.07s) + 3s pre-trial operator-volume pause + up to ~3×~10s for 3
measured trials + ~2s shutdown ≈ **~100–105s** total. The script's own
external bound (`timeout ... 240s`, `--kill-after=15s`) leaves roughly
**135s of margin** — generous, not exact; a run that genuinely needs
the full 240s is itself an anomaly worth investigating (§5), not a
signal to simply raise the bound and retry.

## 5. Invalid-run and validity-flag criteria (R0062: aligned with the corrected wrapper and the R0060 telemetry fix)

**Wrapper execution success is not the same claim as scientific run
validity.** Exit `0` means the script's OWN mechanics behaved correctly
(precheck, mutation, rollback, final hardware-state check, and archive
all did what they claim) — it is a precondition for trusting the run's
data at all, never proof the trial's own telemetry is clean. Every
criterion below is checked in addition to, not instead of, a `0` exit.

A run is **INVALID** (discard, investigate, do not fold into the
comparison) if ANY of the following hold:

- The wrapper's own final exit code is not `0` (covers: `90`
  precheck failed — zero writes issued, `92` condition write/readback
  failed, `91` rollback or the independent final-hardware-state check
  did not verify, `93` this run's own artifact archive is not exactly
  complete, `94` a concurrent invocation held the capture-location lock
  and this run never proceeded, `95` required evidence-storage setup
  could not be created, `130`/`143` interrupted, or the probe's own
  preserved nonzero code including `124`/external-timeout).
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
  `_run_silent_trial` lifecycle-timeout warning) is a validity FLAG,
  not an automatic invalidation, and **not automatic clearance either
  — it remains UNRESOLVED evidence until actually investigated.** It
  means the trial's own `_wait_for_finish` bound (20s) was exhausted
  rather than the response settling normally. Concretely: this warning
  on a condition-A trial must NOT be treated as good enough to proceed
  to condition B under a single combined approval (§ top) — investigate
  and explain it first, exactly as for any other flagged trial, before
  deciding condition A's own trials are usable as the basis for running
  B.
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
  (control identity, limits, and switch state — not just a raw integer)
  in the SAME persisted log, checked by the script itself, not left for
  a human to notice a mismatch.
- Rollback runs ONLY if a mutation was actually attempted this run — a
  precheck failure that wrote nothing skips rollback explicitly rather
  than issuing a redundant, no-op write. When rollback does run, it
  restores `Array 'PCM',1'` to `40` on every exit path this script can
  intercept (normal completion, any detected failure, SIGINT, SIGTERM)
  — idempotent, independently verified — and SEPARATELY, always,
  independently re-validates that `UACDemoV10` is still at its own
  verified MAX baseline, never writing to it to correct what it finds.
  Both outcomes are reported separately from the run's own outcome.
  **Not claimed for a SIGKILL of this wrapper or a power loss** — see
  §4.
- The probe runs under its own dedicated process group; on interruption
  (or as a safety net after ordinary completion) the wrapper terminates
  and CONFIRMS-empty that owned group specifically — escalating from
  SIGTERM to SIGKILL only after a bounded grace period — before
  proceeding to rollback. No broad process-kill.
- No control other than `Array 'PCM',1'` is ever written by this
  procedure.
