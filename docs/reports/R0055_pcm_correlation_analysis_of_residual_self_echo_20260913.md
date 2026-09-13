# R0055 — PCM Correlation Analysis of Residual Self-Echo

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (post-R0054 PCM correlation checkpoint)
**Status:** Evidence-analysis checkpoint. **Two real, confirmed
diagnostic-fidelity bugs found and fixed in the probe itself** (a
playback-window-timestamp corruption bug and a post-interruption
reinjection bug); **no production fix implemented**. Root-cause
confidence is elevated to **MEDIUM-HIGH for hardware-AEC residual
correlation (Class C)** as the leading mechanism, based on real,
reproducible (n=3), cross-validated PCM evidence — but a discriminator is
**recommended, not implemented**, per this checkpoint's own gate.
**`M2.6B` remains IN PROGRESS.** No Gemini run. No push.

## INPUT EVIDENCE

The operator ran the exact R0054-requested capture:

```
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --level max --repeats 3 --capture-pcm --max-lag-ms 500
```

Operator silent throughout. No Gemini. Startup confirmed the R0053 gain
fix active (`audible_mixer_card=UACDemoV10`,
`audible_gain_db=-0.94`, `audible_linear_gain=reference_gain_applied
=0.8974`, unchanged across all 3 trials). Result: **3/3 false confirmed
barge-ins** (`self_echo_probe_20260913T090714Z.json`), each with a real
`BargeInController` confirm at `confirm_hold≈0.3015-0.3016s` after VAD
start — the timer itself, again, is not the defect (re-confirmed a third
time). Six WAV pairs captured under `self_echo_captures/pcm/` (mic +
reference, 16kHz mono, per trial); `en_explain_gravity.wav` fixture
confirmed 16kHz/3.504s exactly.

## STEP 0 — PROBE SOURCE AUDIT (BEFORE touching the PCM)

Per this checkpoint's own instruction, the probe was source-audited
FIRST, before any correlation analysis, for the reinjection artifact the
operator's own terminal transcript flagged (`INTERRUPT CONFIRMED` →
`Bot stopped speaking` → ~60ms later `Bot started speaking` again).

**CONFIRMED BUG #1 (new this checkpoint): unconditional post-interruption
reinjection.** `_play_assistant_phrase`'s per-chunk loop
(`docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`)
had zero awareness of a confirmed local barge-in. The REAL, unmodified
`BargeInController._do_confirm` (this probe's own production class)
calls `on_confirmed` then unconditionally `await
self.broadcast_interruption()`, clearing the output transport's queue —
but the loop kept calling `worker.queue_frames([frame])` for every
REMAINING chunk of the same ~3.5s fixture on its own 100ms schedule,
oblivious to the interruption. This re-populates the just-cleared queue
and produces a SECOND `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame`
pair for the SAME trial.

**Confirmed two independent ways, not merely inferred:**

1. **Direct operator observation** (this checkpoint's own input): every
   trial showed a second "Bot started speaking" ~60ms after
   `INTERRUPT CONFIRMED`.
2. **JSON self-consistency** (byte-exact, computed from the JSON alone,
   before any WAV was opened): `Recorder.mark_playback_start()`/
   `mark_playback_end()` unconditionally OVERWRITE `playback_start_t`/
   `playback_end_t` on every `BotStartedSpeakingFrame`/
   `BotStoppedSpeakingFrame` — so `summarize_trial`'s
   `mic_phase_playback`/`ref_raw_rms_phase_playback` windows (and every
   prior report's own analysis of them, including R0054's) were built
   from the LAST (post-restart) start/stop pair, not the true original
   one. Proof, per trial (`trial_start_t` subtracted for readability):

   | Trial | JSON `playback_start_t` (rel.) | `bargein_confirmed_t` (rel.) | gap |
   |---|---|---|---|
   | 1 | 3.5115s | 3.4479s | 0.0636s |
   | 2 | 3.5148s | 3.4341s | 0.0807s |
   | 3 | 3.5212s | 3.5007s | 0.0205s |

   In all 3 trials the JSON's own `playback_start_t` lands tens of
   milliseconds AFTER `bargein_confirmed_t`, not at the true dispatch
   time (`trial_start_t + 2.0s`, `QUIET_BEFORE_S`) — mechanically
   impossible for a genuine pre-confirmation playback start, and exactly
   consistent with the restart-overwrite mechanism above.

**Why this does NOT invalidate the already-captured PCM.** The reference
WAV for every trial is confirmed, byte-exact, **56064 samples = the
FULL, uncut 3.504s fixture** (`ref_pcm_chunks` accumulates every chunk
`_play_assistant_phrase` ever pushes, unconditionally, regardless of
where `playback_start_t` gets overwritten to) — the reinjection bug only
affects what happens **after** confirmation; the false VAD trigger itself
fires **before** confirmation. The continuous mic WAV (8.68-8.70s,
spanning quiet-before → playback → tail) is likewise unaffected — it is
a raw, un-windowed PCM capture, not built from the corrupted
`playback_start_t`/`playback_end_t` fields at all. **Conclusion: the
already-captured PCM is sufficient for this checkpoint's analysis; no
new capture was requested or needed** — only the (buggy) JSON
phase-window SUMMARIES built on top of it needed to be bypassed, which
this checkpoint's own re-analysis does (see STEP 1 below).

**Fix applied (probe-only, no production change):**
`Recorder.confirmed_event: asyncio.Event`, set the instant a confirmed
barge-in fires (mirrors production's own trigger point,
`nexa.realtime.gemini.runtime`'s `_on_confirmed`). `_play_assistant_phrase`
now checks it before each chunk and, once set, calls
`lifecycle.mark_interrupted()` — the SAME `_ResponseLifecycle` primitive
production's own `_on_confirmed` calls — instead of continuing to inject
chunks or running the normal `TTSStoppedFrame`/`mark_generation_done()`
completion tail. This probe has no `_ResponseGenerationGuard` (a
diagnostic harness, not production); this is the smallest faithful
equivalent. **Does not touch `BargeInController`, VAD, or any production
playback semantics** — only this diagnostic probe's own synthetic-phrase
injection loop. **+3 new deterministic tests**
(`TestPlayAssistantPhraseStopsOnConfirmedInterrupt`, pure/offline, fake
`P`/`worker`/`lifecycle`/`bargein` doubles, no Pipecat import, matching
this test file's own established convention) prove: (a) with no
confirmation, every chunk plays and the normal completion tail fires;
(b) a confirmation mid-phrase stops injection at the NEXT chunk boundary
and calls `mark_interrupted()`, never `mark_generation_done()`; (c) a
confirmation already set before the phrase starts injects nothing.

Also documented (not fixed) a related, lower-priority limitation in
`cross_correlate_pcm`'s own docstring: its **whole-trial** correlation
(the `cross_correlation` block already in each trial's JSON) has no
knowledge that `mic_pcm` starts recording `QUIET_BEFORE_S=2.0s` before
`ref_pcm`'s own first sample — with `max_lag_ms=500` far smaller than
that 2000ms offset, the search can never find the true alignment. This
fully explains why every trial's own whole-trial
`cross_correlation.best_lag_ms` saturates at or near the ±500ms search
boundary (`-486.25`, `-500.0`, `-496.5`) with a near-zero
`normalized_correlation` (`0.0010`, `0.0004`, `0.0009`) — **a
saturated-at-the-search-boundary result is the diagnostic signature of
this timeline mismatch, not evidence of low real echo correlation.**
Not fixed this checkpoint (kept separate from the reinjection fix); this
report's own STEP 1-3 analysis works around it externally, from the same
PCM.

## STEP 1 — TIMELINE RECONSTRUCTION (re-deriving the TRUE playback window)

Since the JSON's own `playback_start_t`/`playback_end_t` are corrupted
(STEP 0), the true playback dispatch time was re-derived independently:
`_play_assistant_phrase` is called immediately after
`await asyncio.sleep(QUIET_BEFORE_S)` (2.0s) from `trial_start_t`, with
no intervening `await` that could add material delay. Cross-validated
two ways:

1. **Cross-check against the wall-clock times already reported in this
   checkpoint's own input** (playback start / VAD start / confirm,
   independently observed from the terminal): predicted
   playback→VAD-start offsets (`vad_start_rel − 2.0`) of **1.1464s /
   1.1325s / 1.1991s** vs the originally reported **1.138s / 1.126s /
   1.192s** — agreement within 7-8ms in every trial, consistent with
   ordinary asyncio scheduling jitter, not a modeling error.
2. **Offline re-derivation of the REAL Silero VAD decision** (STEP 2,
   below) against the true continuous mic WAV independently reproduces a
   `SPEAKING` transition within **28-63ms** of the real pipeline's own
   recorded `vad_start_t`, in all 3 trials — strong evidence the captured
   mic WAV is indeed the exact causal signal for the real hardware's own
   VAD decision, and that the `t=2.0s` anchor is correct.

## STEP 2 — OFFLINE SILERO RE-DERIVATION (correct window this time)

The exact installed `pipecat.audio.vad.silero.SileroVADAnalyzer` +
`VADParams(stop_secs=0.5)` (byte-for-byte the same construction
`build_probe_pipeline`/production use) was fed the raw, continuous mic
WAV in real 512-sample (32ms) frames, replicating `VADAnalyzer._run_analyzer`'s
exact state machine (`VAD_CONFIDENCE=0.7`, `VAD_MIN_VOLUME=0.6`,
`start_secs=0.2` → 6-frame confirm window, `stop_secs=0.5`) — this
recovers TRUE `(confidence, smoothed_volume, speaking)` telemetry over
the CORRECT window, without needing new hardware.

**Self-check finding (own bug, caught and fixed before trusting any
number):** the first pass of this re-derivation script omitted
`self._prev_volume = volume` after calling `_get_smoothed_volume` — a
step `VADAnalyzer._run_analyzer` performs explicitly but
`_get_smoothed_volume` itself does not (confirmed by reading the
installed source directly). Without it, exponential smoothing never
accumulates and every re-derived volume stayed near-zero regardless of
real signal level — verified directly: a single 400ms window's raw
`calculate_audio_volume()` read **0.8485** (BS.1770 loudness,
appropriately high for that content) while the OMITTED-update run
reported a smoothed value near 0.13 for the same audio. Fixed before any
conclusion was drawn from this data.

**Corrected per-trial phase telemetry** (window boundaries now
`[true_playback_start, vad_start]` / `[vad_start, confirm]`, not the
corrupted JSON windows):

| Trial | window | n | conf_mean | conf_max | vol_mean | vol_max | speaking_frame_frac |
|---|---|---|---|---|---|---|---|
| 1 | quiet_before [0,2.0) | 63 | 0.004 | 0.045 | 0.0 | 0.0 | 0.0 |
| 1 | pb0→vad_start [2.0,3.146) | 36 | 0.336 | 0.907 | 0.406 | 0.755 | 0.222 |
| 1 | vad_start→confirm [3.146,3.448) | 9 | 0.905 | 0.915 | 0.797 | 0.828 | **1.0** |
| 2 | pb0→vad_start [2.0,3.133) | 35 | 0.276 | 0.871 | 0.471 | 0.771 | 0.171 |
| 2 | vad_start→confirm [3.133,3.434) | 10 | 0.863 | 0.892 | 0.810 | 0.833 | **1.0** |
| 3 | pb0→vad_start [2.0,3.199) | 37 | 0.295 | 0.852 | 0.470 | 0.787 | 0.216 |
| 3 | vad_start→confirm [3.199,3.501) | 10 | 0.823 | 0.850 | 0.819 | 0.834 | **1.0** |

**`speaking_frame_frac=1.0` for the vad_start→confirm window in all 3
trials** — by construction (this is literally the confirmed-speaking
interval), but it also confirms the underlying raw signal stayed
reliably above BOTH the confidence and volume gates for the WHOLE
0.3s window, not flickering. **R0054's own `speaking_frame_frac`
"clean vs false" comparison (0.11 vs 0.33-0.34) was computed on the
SAME corrupted (post-restart) window as `playback_start_t`/`playback_end_t`,
and does not describe the true pre-trigger acoustic signature** — this
is an erratum on R0054's own CLEAN VS FALSE TRIAL COMPARISON section,
not a retraction of R0054's other findings (the gain fix's 5/5→2/3
improvement, the two independently-confirmed bugs it fixed, and the
timer/gain conclusions all stand unaffected — none of those depended on
this specific window).

**Offline-re-derived vs real-pipeline VAD start** (validation, not a
production claim):

| Trial | offline re-derived SPEAKING | real `vad_start_rel` | diff |
|---|---|---|---|
| 1 | 3.104s | 3.1464s | −0.042s |
| 2 | 3.104s | 3.1325s | −0.029s |
| 3 | 3.136s | 3.1991s | −0.063s |

Small (28-63ms), one-directional (offline always slightly earlier) —
consistent with the real analyzer instance running continuously across
the whole probe session (periodic 5s Silero internal state resets,
warmup history) vs this analysis's fresh-per-trial instance; not a
material discrepancy for this checkpoint's purpose (confirming the
captured PCM is the true causal signal).

## STEP 3 — RAW PCM (no VAD/Silero involved)

| Trial | window | mic RMS | mic peak | mic clip_frac | mic nearsat_frac | ref RMS | ref peak |
|---|---|---|---|---|---|---|---|
| 1 | quiet_before | 5.58 | 25 | 0 | 0 | — | — |
| 1 | pb0→vad_start | 733.42 | 6275 | 0 | 0 | 1814.16 | 15454 |
| 1 | vad_start→confirm | 1993.34 | 6387 | 0 | 0 | 3734.46 | 13765 |
| 2 | quiet_before | 6.04 | 28 | 0 | 0 | — | — |
| 2 | pb0→vad_start | 851.69 | 5992 | 0 | 0 | 1811.63 | 15454 |
| 2 | vad_start→confirm | 1842.94 | 5846 | 0 | 0 | 3740.86 | 13765 |
| 3 | quiet_before | 6.55 | 32 | 0 | 0 | — | — |
| 3 | pb0→vad_start | 840.86 | 6176 | 0 | 0 | 1951.21 | 15454 |
| 3 | vad_start→confirm | 1843.66 | 6226 | 0 | 0 | 3374.26 | 13765 |

`clip_frac` = fraction of int16 samples at exactly ±32767; `nearsat_frac`
= fraction at or above ±30000 (≈−0.4 dBFS). **Zero in every window, every
trial, both mic and reference.** Mic peaks (5846-6387 / 32767 ≈ 18-19%
FS, ≈−14.7 to −14.2 dBFS) and reference peaks (13765-15454 / 32767 ≈
42-47% FS, ≈−7.5 to −6.5 dBFS) are both well below saturation.
**NONLINEARITY STATUS updated from R0054's "untested" to TESTED, NOT
SUPPORTED**: no digital clipping/near-saturation signature exists
anywhere in this capture. Soft/analog nonlinearity in the speaker or mic
transducer themselves cannot be ruled out from PCM alone (no digital
signature would exist even if present), but there is no positive
evidence for it either — Class D (nonlinear acoustic residual) is not
supported by this data and is not elevated as a leading hypothesis.

## STEP 4/5 — BOUNDED SLIDING-WINDOW CROSS-CORRELATION (the core new evidence)

Implemented independently (not a probe-file change): the reference
timeline offset (`+2.0s`, STEP 1) is applied explicitly, then a bounded
lag search (±500ms, the already-approved diagnostic bound) is run
per-window using the same normalized (zero-mean Pearson) correlation
metric `cross_correlate_pcm` already uses. Two granularities:

**(a) Five named diagnostic windows**, per the checkpoint's own request:

| Trial | window | lag (ms) | norm. corr | mic RMS | ref RMS |
|---|---|---|---|---|---|
| 1 | first 500ms after playback | **+209.7** | 0.083 | 15.7 | 284.3 |
| 1 | 0.8-1.0s | −123.9 | 0.460 | 166.4 | 3988.2 |
| 1 | 1.0-1.3s (failure region) | −116.6 | 0.307 | 1462.1 | 3834.9 |
| 1 | immediately before VAD start | −124.1 | 0.332 | 1412.5 | 3416.0 |
| 1 | VAD→confirm | −111.4 | **0.573** | 1993.3 | 3734.5 |
| 2 | first 500ms after playback | **+229.4** | 0.070 | 18.1 | 284.3 |
| 2 | 0.8-1.0s | −95.8 | 0.476 | 627.8 | 3988.2 |
| 2 | 1.0-1.3s (failure region) | −84.9 | 0.369 | 1651.5 | 3834.9 |
| 2 | immediately before VAD start | −96.1 | 0.417 | 1630.8 | 3439.9 |
| 2 | VAD→confirm | −99.2 | **0.594** | 1842.9 | 3740.9 |
| 3 | first 500ms after playback | **+215.6** | 0.074 | 17.4 | 284.3 |
| 3 | 0.8-1.0s | −116.9 | 0.384 | 424.8 | 3988.2 |
| 3 | 1.0-1.3s (failure region) | −102.2 | 0.389 | 1660.4 | 3834.9 |
| 3 | immediately before VAD start | −109.8 | 0.400 | 1656.9 | 3492.8 |
| 3 | VAD→confirm | −104.7 | **0.594** | 1843.7 | 3374.3 |

Clipping fraction: 0 in every one of these windows, both signals (STEP 3
already established this globally; re-verified per-window).

**(b) Fine sliding sweep** (200ms window, 50ms hop, `pb0` through
`confirm+0.2s`, 29-31 windows per trial): once past the near-silent first
~150-200ms of the fixture (where lag is erratic and positive —
mechanically expected: with almost no real signal, `argmax` over a flat
near-zero correlation surface returns noise, not a real alignment), lag
settles and stays in **−85ms to −130ms for the remainder of every
trial**, in all 3 trials, without a distinct jump specifically inside the
1.0-1.3s failure region. Correlation **rises across the trial and peaks
at 0.64-0.70 right around the VAD-start/confirm boundary** (trial 1:
0.66 at t=3.2s / 0.64 at t=3.3s; trial 2: 0.65 at t=3.2s / **0.70** at
t=3.3s; trial 3: 0.65 at t=3.2s / **0.67** at t=3.3s) — the single
highest correlation value observed anywhere in the sweep, in all 3
trials, landing at almost the same relative offset from playback start
each time.

## INTERPRETATION

**The reference-to-mic lag is real, stable, and reproducible — but it is
CONSTANT across the whole trial, not something that spikes specifically
in the 1.0-1.3s failure region.** Its magnitude (~85-130ms) is larger
than the sum of R0054's own documented KNOWN buffer/chunk granularities
(20ms mic input + 40ms audible re-chunking = 60ms), consistent with a
meaningful contribution from the two stages R0054 explicitly flagged as
**unmeasured from source** (PortAudio's own output buffer, `aplay`'s own
ALSA buffer) — this checkpoint is the first evidence quantifying their
combined real-world contribution (roughly 25-70ms beyond the known 60ms,
on this hardware). This is a genuine, newly-measured fact worth
recording, but per the checkpoint's own decision rule it does **not**
support Class A/B as the LEADING mechanism: a stable, whole-trial-uniform
system latency does not explain why THIS specific window
(1.0-1.3s / VAD→confirm) false-triggers and earlier quieter windows do
not — that difference tracks the fixture's own rising acoustic energy
and the resulting rise in correlation, not a timing anomaly localized to
the failure region.

**What DOES track the failure region: correlation.** Across all three
independent real-hardware trials, normalized correlation between the
reference and the post-hardware-AEC mic residual is not near the noise
floor (0.07-0.08, measured directly from the near-silent first window,
which serves as this checkpoint's own negative control) during the
failure window — it is **4-9× the noise floor** (0.31-0.59 in the five
named windows; up to 0.64-0.70 in the fine sweep), and it **peaks
precisely at the VAD-start/confirm boundary in every trial**. This is
the exact pattern the checkpoint's own decision rule describes:
*"stable high assistant correlation in mic residual at a consistent lag
during the false-trigger region, while timing is otherwise stable."*

**Classification: C — high correlated residual echo despite a
correctly-characterized (if imperfectly known) reference alignment —
is the evidence-backed LEADING mechanism**, with **A (a real,
newly-quantified ~85-130ms combined PortAudio/ALSA latency) as a
confirmed but non-differentiating secondary finding** — i.e., this
checkpoint's honest classification is **E (a documented combination)**,
but with C identified as the dominant, decision-relevant mechanism and A
demoted to "worth knowing, not worth fixing first," since fixing a
constant, region-independent latency would not by itself change which
window has the highest correlation. **B (pure trial-to-trial jitter)**
and **D (nonlinear/clipping residual)** are **not supported**: the lag
values cluster tightly (±20-25ms band across all 3 trials once past the
initial near-silent window) rather than varying erratically, and STEP 3
found zero clipping/near-saturation anywhere.

**Confidence: MEDIUM-HIGH.** This is n=3 real-hardware trials (small,
consistent with every other checkpoint in this thread), but the pattern
is fully reproducible in direction and rough magnitude across all three,
cross-validated by an independent offline Silero re-derivation that
matches the real pipeline's own VAD decision within tens of milliseconds,
and grounded in raw PCM the real hardware pipeline itself produced (not
simulated). It is not yet MAX confidence: no larger-N replication, no
LOW/NORMAL-volume comparison PCM (R0053's own gain hypothesis work used
LOW/NORMAL only for confirm-count evidence, not PCM), and the ~100ms
combined latency's own sub-components (PortAudio vs ALSA) remain
unseparated.

## DECISION

Per this checkpoint's own explicit gate — *"Do NOT implement such a
discriminator yet unless the evidence clearly supports it"* — this
report's role ends at recommending, not implementing. **Recommendation
for the next checkpoint**: a NeXa-owned echo/double-talk discriminator
ahead of `BargeInController` confirmation is now evidence-backed as the
correct next architectural layer, rather than further gain/timing tuning
alone. This is a genuinely new production component (not a bug fix), so
per this thread's own established pattern (R0052→R0053→R0054, each one
incremental, each gated on real hardware evidence before the next
design step), it should be its own checkpoint with its own design
proposal, not folded into this evidence-analysis report.

**No VAD/Silero/`BargeInController`/preroll/gain code was touched this
checkpoint.** The only `src/nexa/**`-adjacent change is the probe fix
(STEP 0), which lives entirely under `docs/research/`.

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
  — `Recorder.confirmed_event`; `_play_assistant_phrase` stops injecting
  further chunks and calls `lifecycle.mark_interrupted()` once a
  confirmed barge-in fires (confirmed bug fix); documented (not fixed)
  the whole-trial `cross_correlate_pcm` ref/mic timeline-offset
  limitation in its own docstring.
- `tests/test_m2_6b4m_self_echo_probe.py` — +3 tests
  (`TestPlayAssistantPhraseStopsOnConfirmedInterrupt`).
- `docs/reports/R0055_..._20260913.md` (this report).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated (below).

No `src/nexa/**` file touched this checkpoint (confirmed:
`git diff --stat -- src/nexa` is empty).

## TEST RESULTS

- `tests/test_m2_6b4m_self_echo_probe.py` — 41/41 pass (38 + 3 new).
- Full project suite:
  `.venv/bin/python -m unittest discover -s tests -p "test_*.py"` →
  **1061 tests, OK (skipped=7)**.
- `ruff check` on every touched file: clean.
- `pip check`: "No broken requirements found."
- `git diff --check`: clean.

## COMMIT HASHES

- `6239a9b` — research: PCM correlation analysis of residual self-echo
  (M2.6B.4N follow-up / R0055).

## GIT STATUS

Clean working tree after commit. Not pushed. No Gemini call.

## EXACT NEXT STEP

**No further operator hardware capture is needed for this specific
question** — the existing PCM (already captured, git-ignored, still on
disk under `self_echo_captures/pcm/`) was sufficient for this
checkpoint's full analysis, including the STEP 0 probe-bug audit. The
next checkpoint is a DESIGN proposal for a NeXa-owned echo/double-talk
discriminator ahead of `BargeInController` confirmation, informed by
this report's correlation evidence — not a new hardware run.
