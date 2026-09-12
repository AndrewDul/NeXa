# R0054 — Residual Self-Echo After Coherent-Gain Fix

**Date:** 2026-09-12
**Milestone:** M2.6B.4N follow-up (post-R0053 real hardware forensics)
**Status:** R0053's coherent-gain fix is a **real, confirmed defect fix**
that measurably reduced (not eliminated) MAX-volume false self-barge-in
in this small real-hardware sample. Two NEW real bugs were found and
fixed this checkpoint while analyzing the new capture (a probe
diagnostic-fidelity bug and a production event-loop-blocking bug) —
neither is claimed to explain the remaining residual failure by itself.
**No production self-echo fix implemented this checkpoint** — per its
own explicit gate, this is forensics + confirmed-bug fixes only.
**`M2.6B` remains IN PROGRESS.** No Gemini run. No push.

## POST-R0053 REAL HARDWARE RESULT

Real reSpeaker + real USB speaker + real AEC reference, corrected
gain-wired probe, operator completely silent. Startup diagnostics
confirmed the fix was active:

```
audible_mixer_card       UACDemoV10
audible_gain_db          -0.94
audible_linear_gain      0.8974
reference_gain_applied   0.8974
```

MAX volume, 3 silent trials
(`self_echo_probe_20260912T224316Z.json`):

| Trial | Result | playback→VAD offset | confirm hold |
|---|---|---|---|
| 1 | clean (0 VAD starts) | — | — |
| 2 | **false confirm** | 1.0773s | 0.3017s |
| 3 | **false confirm** | 1.1092s | 0.3014s |

**2/3 false confirmed barge-ins.** The pre-fix (R0052/R0053) MAX result
was 5/5 false.

## WHAT IMPROVED

MAX-volume false-barge-in rate in this small sample dropped from
**5/5 → 2/3** with the coherent-gain fix active
(`reference_gain_applied=0.8974` throughout, confirmed by the probe's
own live-read diagnostic, not asserted). This is consistent with
reference/audible gain ownership incoherence being a real, contributing
defect — the fix is not a no-op.

## WHAT STILL FAILS

- 2 of 3 MAX trials still produced a real, confirmed false barge-in.
- The false-onset timing (1.0773s, 1.1092s) lands in the **same
  ~1.08–1.11s content-relative window** R0053's pre-fix 5/5 MAX data
  showed (mean 1.0962s, stdev ≈0.0154s across all 7 false MAX
  observations to date, pre- and post-fix combined) — the SAME region
  of the fixture is implicated regardless of the gain fix.
- Confirm-hold timing (0.3014–0.3017s) is exactly the configured
  `confirm_hold_secs`, in both trials, both before and after the gain
  fix — `BargeInController`'s own timer is not itself part of the
  problem (re-confirmed, not newly discovered).
- The SAME reference/audible gain (`0.8974` linear, unchanged across
  all 3 trials — a single MAX volume setting for the whole run) was
  active in the ONE clean trial and BOTH false trials. Gain alone
  cannot explain why trial 1 differed from trials 2/3 — see GAIN
  HYPOTHESIS STATUS.

## CORRECTED JSON ANALYSIS

R0053 fixed R0052's confidence-conversion bug and mic/reference RMS
tap contamination, so this new capture's `conf_mean`/`conf_max`/
`vol_mean`/`vol_max`/mic RMS fields are genuinely usable this time
(unlike R0052's 5 files, which needed disregarding per R0052's own
erratum). Per-trial table, built directly from the JSON:

| Field | Trial 1 (clean) | Trial 2 (false) | Trial 3 (false) |
|---|---|---|---|
| playback duration | 3504.0ms (full) | 1381.4ms (cut short) | 1412.6ms (cut short) |
| playback→VAD offset | n/a (no VAD start) | 1.0773s | 1.1092s |
| confirm hold | n/a | 0.3017s | 0.3014s |
| mic quiet-before conf_max / vol_max | 0.0477 / 0.5943 | 0.0041 / 0.0 | 0.0065 / 0.0 |
| mic playback conf_mean / conf_max | 0.2231 / 0.7873 | 0.4765 / 0.9399 | 0.4641 / 0.9450 |
| mic playback vol_mean / vol_max | 0.4667 / 0.8317 | 0.4792 / 0.7959 | 0.4838 / 0.7931 |
| mic playback **speaking_frame_frac** | **0.1091** | **0.3409** | **0.3333** |
| mic raw RMS mean / max (playback) | 255.11 / 3412.36 | 391.42 / 2575.51 | 384.48 / 2437.12 |
| mic raw peak_max (playback) | 5974 | 6386 | 5887 |
| mic raw RMS mean (quiet-before) | 35.76 | 7.44 | 7.20 |
| reference RMS (playback) | **0 frames recorded** — see below | 0 frames | 0 frames |
| reference_gain_applied | 0.8974 | 0.8974 | 0.8974 |

**`ref_raw_rms_phase_playback: {"n_frames": 0}` in all 3 trials** was
investigated and explained (not merely noted) — see BUFFER/RESAMPLER
AUDIT and the confirmed probe bug fix below; it is a diagnostic
telemetry gap in THIS probe, not evidence about the real hardware path.

## CLEAN VS FALSE TRIAL COMPARISON

The single clearest, real, uncontaminated distinguishing signal between
the clean trial and the two false trials is **`speaking_frame_frac`
during playback**: **0.1091** (trial 1) vs **0.3409** / **0.3333**
(trials 2/3) — roughly **3×** higher in the false trials. Both trial
1's `conf_max` (0.7873) and `vol_max` (0.8317) individually cross the
production speech gate (`confidence≥0.7`, `volume≥0.6`) just as the
false trials' do — the difference is **duration/sustain**, not peak
amplitude: Pipecat's own `_vad_start_frames ≈ 6` consecutive analyzer
frames (≈192ms) must ALL satisfy both thresholds before
`VADUserStartedSpeakingFrame` fires. Trial 1 crossed the gate only
momentarily (11% of frames, evidently never 6 in a row); trials 2/3
sustained it roughly a third of the whole playback window — long
enough, repeatedly, to cross the hold.

Mic quiet-before RMS varied trial to trial (35.76 → 7.44 → 7.20) —
real room-noise-floor variation between trials, not attributable to
gain (unchanged) or fixture (identical WAV every trial).

## REFERENCE/AUDIBLE TIMING PATH

Re-audited both paths against the currently installed source (Pipecat
1.8.1, this repo's own code), end to end:

**REFERENCE:** `TTSAudioRawFrame` → `AecReferenceFeeder.process_frame`
(now off-loop gain read, see PRODUCTION CHANGE) → `_enqueue`
(`asyncio.Queue(maxsize=24)`, drop-oldest on overflow) → `_run_writer`
task → `loop.run_in_executor(None, self._sink.write, pcm)` → persistent
`aplay -t raw -f S16_LE -D plug:respeaker` subprocess stdin → ALSA
`plug:respeaker` (`hw:CARD=Array,DEV=0`) → XVF3800's own far-end
reference input.

**AUDIBLE:** the SAME `TTSAudioRawFrame` → Pipecat's
`BaseOutputTransport` internal 40ms re-chunking
(`audio_out_10ms_chunks=4`, confirmed in its installed source) →
`_audio_queue` (an **unbounded** `FrameQueue()` — confirmed, no
`maxsize` passed anywhere in its construction) → a background write
task → blocking `PyAudio` `stream.write()` via a dedicated
single-worker `ThreadPoolExecutor` (confirmed in
`LocalAudioOutputTransport.__init__`) → ALSA `plug:usb_speaker`
(`hw:CARD=UACDemoV10,DEV=0`) → physical DAC/amp/speaker → acoustic path
→ reSpeaker mic array → XVF3800 hardware AEC (subtracts its own
internal model of the far-end reference above) → `LocalAudioInputTransport`
(20ms PortAudio input buffer, `int(sample_rate/100)*2`, confirmed in
its installed source) → Silero VAD.

## BUFFER / RESAMPLER AUDIT

**Known, source-confirmed:**

- Mic (input) PortAudio buffer: **20ms** (`frames_per_buffer =
  int(sample_rate/100)*2`).
- Audible (output) internal re-chunking: **40ms**
  (`audio_out_10ms_chunks=4 × 10ms`), before `_audio_queue`.
- `AecReferenceFeeder`'s own queue: **24 chunks** max, drop-oldest —
  in production, chunk size is whatever `AssistantAudioEvent.pcm` size
  Gemini's own stream delivers (an external, not fully controlled
  quantity, not a fixed ms value we can state precisely from this
  source alone); in this probe, chunks are `ASSISTANT_CHUNK_MS=100ms`
  each (after this checkpoint's pacing fix, see below), so a
  theoretical worst-case backlog of 24×100ms=2.4s **only under
  sustained overflow**, not normal operation.
- Audible output's own `_audio_queue`: confirmed **unbounded** — no
  backpressure whatsoever propagates from real device write speed back
  up the pipeline to whatever queues `TTSAudioRawFrame`s.

**NOT knowable from source alone (no arbitrary assumption made):**

- PortAudio's own internal output buffer size/latency: Pipecat's
  `LocalAudioOutputTransport.setup()` opens the output stream with NO
  `frames_per_buffer` argument — PortAudio's own host-API default
  applies, and this repo's source does not fix or expose that number.
- The `aplay`/ALSA hardware period/buffer size actually negotiated for
  `plug:respeaker` (`hw:CARD=Array,DEV=0`): `_PcmSink` passes no
  `--buffer-size`/`--period-size` to `aplay` — ALSA's own
  hardware-negotiated default applies, likewise unmeasured from source.

**CONFIRMED BUG FOUND while explaining `ref_raw_rms_phase_playback: 0`
(all 3 trials, including the full-length clean trial — ruling out
"cut short by interruption" as the explanation):** this probe's own
`_play_assistant_phrase` built its ENTIRE ~35-chunk frame list for a
3.5s phrase and pushed it through ONE `worker.queue_frames(frames)`
call. Because the output transport's own `_audio_queue` is unbounded
(confirmed above), nothing throttles that injection to real time — all
~35 `TTSAudioRawFrame`s reach `_RefRmsTap` (positioned upstream of
`aec_feeder`) within milliseconds of each other, clustered near
`trial_start`, entirely **before** the real, wall-clock-paced
`playback_start_t`/`playback_end_t` window (which only reflects when
`BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` actually fire, i.e.
when the real device has actually begun/finished producing sound).
That fully explains the anomaly: the reference frames were real and
correctly tapped, just timestamped entirely outside the one window this
probe reported them against. **This is a diagnostic-fidelity defect in
the PROBE's own simulation of assistant speech, not evidence about the
real hardware/AEC timing path** — confirmed by checking real
production's own per-chunk code path
(`nexa.realtime.gemini.runtime`'s event-consumption loop): production
calls `self.lifecycle.mark_audio_produced()` then
`await self.hw_worker.queue_frames([frame])` **once per
`AssistantAudioEvent`**, as they arrive from Gemini's own event stream
(network/model-paced), never as one pre-built burst.

**Fixed this checkpoint:** `_play_assistant_phrase` now queues one
frame at a time, calling `lifecycle.mark_audio_produced()` before each
`queue_frames([frame])` (production's own exact per-chunk order) and
pacing each with `asyncio.sleep(ASSISTANT_CHUNK_MS/1000)` — the SAME
`ASSISTANT_CHUNK_MS=100` constant this probe already used to SIZE its
chunks, not a new number. Also added `ref_raw_rms_phase_quiet_before`/
`ref_raw_rms_phase_after` fields (mirroring mic's existing 3-phase
reporting) so a future run reveals WHERE reference frames land even
without a full `--capture-pcm` capture. Neither change touches VAD,
Silero, `BargeInController`, or the 500ms preroll.

**SEPARATE, ALSO-CONFIRMED BUG (production code, found during the same
source audit):** `AecReferenceFeeder.process_frame` called
`self._gain_source()` (bound to
`CoherentReferenceGain.current_gain`, which shells out to `amixer`
when its cache is stale) **synchronously, inline, with no
`run_in_executor`** — blocking the ENTIRE shared asyncio event loop
(mic capture, VAD, frame propagation, everything) for the call's
duration. Measured directly on this Pi: a real `amixer` call takes
**~3ms** — small, and (given the ~1.08s-scale offsets observed) too
small alone to explain the residual failure's magnitude, but a blocking
call on a hot async path is a genuine defect regardless of measured
size, and `refresh_secs=2.0s` combined with this probe's own
inter-trial timing means EVERY trial's first reference-scaled frame was
likely to trigger a fresh (blocking) read. **Fixed:** wrapped in
`loop.run_in_executor(None, self._gain_source)` — the exact pattern
this same file already uses for `self._sink.write`. New regression test
(`test_slow_gain_source_does_not_block_the_event_loop`) proves a
concurrent task keeps ticking while a deliberately slow `gain_source`
runs.

## GAIN HYPOTHESIS STATUS

**Not sufficient alone, explicitly tested per the charter's own
instruction:** trial 1 (clean) and trials 2/3 (false) used the
identical `reference_gain_applied=0.8974` (the same MAX volume setting,
unchanged for the whole run), the identical fixture, and the same room
setup. Since the input was identical across all three trials and the
outcome was NOT, gain amplitude by itself cannot explain the remaining
trial-to-trial nondeterminism. Gain-ownership incoherence remains a
real, confirmed, and evidently contributing defect (5/5 → 2/3), but it
is **not documented as the sole confirmed root cause** of the false
self-barge-in.

## TIMING HYPOTHESIS STATUS

Elevated to the leading remaining candidate, but **not yet proven** —
no bounded PCM/correlation evidence exists yet (this run did not use
`--capture-pcm`). What IS established: (a) a confirmed, real
event-loop-blocking bug in the gain-read path (fixed, magnitude ~3ms,
likely too small alone); (b) a confirmed, real diagnostic-fidelity bug
in the probe's own injection pacing (fixed, probe-side only, does not
by itself imply a production-side timing defect, since production
already paces per-`AssistantAudioEvent`); (c) two PortAudio/ALSA buffer
stages (audible output, reference `aplay`) whose actual size cannot be
determined from source alone. None of this proves or disproves a real
reference-vs-echo lag at the XVF3800 sufficient to explain 2/3 MAX
failures with identical gain — that requires the bounded PCM capture
and cross-correlation this report requests next (STEP 4/5, not yet
run).

## NONLINEARITY STATUS

**Not proven, not ruled out.** The only peak data available this run
is mic RMS/peak per trial: `peak_max` 5974 / 6386 / 5887 — all well
below int16 saturation (32767), no evidence of hard clipping in the mic
signal in the (coarse, window-aggregated) telemetry available. The
reference signal's own peak/clipping behavior is **unknown this run**
(zero reference frames were correctly windowed, per the confirmed bug
above) — genuinely untested, not assumed clean. A real
sample-level clipping-fraction measurement requires the requested
`--capture-pcm` run.

## WHY VAD TUNING REMAINS REJECTED

Unchanged from R0053: `confirm_hold_secs` fired at almost exactly
0.3014–0.3017s in both new false trials (matching R0053's earlier
0.301–0.302s observations exactly) — the timer is not the defect.
`speaking_frame_frac` cleanly distinguishes trial 1 (11%, correctly
rejected) from trials 2/3 (33–34%, correctly confirmed per the
EXISTING, unmodified sustained-duration rule) — the VAD/BargeInController
logic is behaving exactly as designed on the evidence it is given; the
defect (whatever mixture of gain/timing/nonlinearity it ultimately is)
lies upstream of VAD, in the acoustic/reference signal itself. No
Silero/VAD/`BargeInController`/preroll parameter was changed this
checkpoint.

## ADDITIONAL PCM EVIDENCE REQUIRED

To distinguish Classes B/C (timing/buffering) from E (nonlinearity)
from "hardware AEC residual insufficient regardless of either," this
checkpoint requests the SMALLEST additional real-hardware run: MAX
volume, 3 silent trials, `--capture-pcm` (now meaningful — the
reference-tap timing bug above is fixed, so reference PCM will be
captured within the real playback window this time), with a widened
correlation lag search (`--max-lag-ms 500`, wider than the 200ms
default): every documented buffer/chunk granularity found in BUFFER/
RESAMPLER AUDIT above (20ms mic, 40ms audible re-chunking, 100ms
reference chunking) is well under 500ms, and the two genuinely
unmeasured stages (PortAudio's own output buffer, `aplay`'s own ALSA
buffer) are conservatively expected to be smaller still on typical
Raspberry Pi ALSA configurations — 500ms is the charter's own suggested
evidence-informed margin above every KNOWN granularity, not an
arbitrary number. Analysis of the returned PCM (bounded sliding-window
correlation, clipping fraction, best-lag comparison between the clean
and false trials) is explicitly **STEP 5, deferred to the next
checkpoint** once this data exists — no speculative correlation
analysis or production fix is implemented against data that does not
yet exist.

## FILES CHANGED

- `src/nexa/voice_tts/aec_reference.py` — wrapped the `gain_source()`
  call in `loop.run_in_executor` (confirmed event-loop-blocking bug
  fix).
- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
  — `_play_assistant_phrase` now paces per-chunk injection to match
  production's real per-event cadence (confirmed diagnostic-fidelity
  bug fix); added `ref_raw_rms_phase_quiet_before`/
  `ref_raw_rms_phase_after` fields.
- `tests/test_bargein_m2_5b.py` — +1 test
  (`test_slow_gain_source_does_not_block_the_event_loop`).
- `tests/test_m2_6b4m_self_echo_probe.py` — +2 tests (the new
  ref-RMS phase fields).
- `docs/reports/R0053_..._20260912.md` — erratum recording this
  checkpoint's real hardware result (PARTIAL IMPROVEMENT / FAIL).
- `docs/reports/R0054_..._20260912.md` (this report).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated (below).

No VAD/Silero/`BargeInController`/preroll code touched. No speculative
self-echo production fix implemented.

## TEST RESULTS

- `tests/test_bargein_m2_5b.py` — 43/43 pass (42 + 1 new).
- `tests/test_m2_6b4m_self_echo_probe.py` — 38/38 pass (36 + 2 new).
- `tests/test_self_echo_probe_production_gain_parity.py` — 7/7 pass,
  unmodified (this checkpoint did not touch the gain-wiring lines it
  checks).
- `tests/test_voice_aec_gain.py` — 17/17 pass, unmodified.
- `tests/test_realtime_gemini_runtime.py::TestVadBridgePrerollParity`
  (R0051) — 6/6 pass, unmodified.
- `tests/test_realtime_gemini_runtime.py::TestAtomicProviderReplacement`
  (R0045) — 5/5 pass, unmodified.
- `tests/test_realtime_gemini_runtime.py::TestProductionCanonicalTurnLifecycle`
  (R0046) — 5/5 pass, unmodified.
- `tests/test_realtime_gemini_runtime.py::TestNoLocalLidInCloudRuntime`
  (zero local LID) — 3/3 pass, unmodified.
- `tests/test_realtime_gemini_service.py::TestMidTurnReadinessLoss` +
  `TestReconnectWiring` (connection-loss) — 9/9 pass, unmodified.
- Full project suite:
  `.venv/bin/python -m unittest discover -s tests -p "test_*.py"` →
  **1058 tests, OK (skipped=7)**.
- `ruff check` on every touched file: clean.
- `pip check`: "No broken requirements found."
- `git diff --check`: clean (exit 0).

## COMMIT HASHES

- `7be8ea5` — fix: two confirmed bugs found during post-R0053
  forensics (M2.6B.4N follow-up / R0054).

## GIT STATUS

Clean working tree after commit. Not pushed.

## EXACT NEXT LOCAL-ONLY COMMAND

Operator: remain completely silent. No Gemini.

```bash
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py \
  --level max --repeats 3 --capture-pcm --max-lag-ms 500
```

This is the SMALLEST additional real-hardware step — 3 trials, not the
full matrix. It will save bounded mic + reference PCM (now correctly
windowed, per the confirmed fix above) as WAV pairs under
`docs/research/m2_6_cloud_realtime_voice/self_echo_captures/pcm/` and
print a whole-trial cross-correlation result for each. Report back the
printed output, the JSON path, and the WAV paths — STEP 5's bounded
sliding-window correlation/clipping analysis (not yet implemented) is
the next checkpoint's first task once this data exists. Do not run the
full 5/10/10/5 + 3-control acceptance matrix yet.
