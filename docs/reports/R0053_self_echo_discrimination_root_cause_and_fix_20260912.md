# R0053 — Self-Echo Discrimination Root Cause and Production Fix

**Date:** 2026-09-12
**Milestone:** M2.6B.4N (self-echo/false-barge-in fix, following R0052's
diagnostic + real-hardware evidence)
**Status:** **Leading evidence-backed root-cause hypothesis / implemented
fix, pending real hardware validation.** A real ALSA mixer-ownership
defect was found and confirmed to EXIST by direct, real system
inspection (not inferred, not fabricated) — that a real defect exists
is proven; that it is *the* (or the only) cause of the false
self-barge-ins is not yet proven, and will not be until the post-fix
real hardware run below shows the false confirms disappear. Smallest
evidence-backed fix implemented for this defect (reference/audible gain
coherence). Two real bugs in R0052's own diagnostic instrumentation
found and fixed while analyzing its real data (see R0052's erratum).
A separate, real validation-contract gap was then found and fixed in
THIS checkpoint's own probe (see VALIDATION CONTRACT CORRECTION below)
— an earlier revision of this fix's own validation probe would not
have exercised the fix at all. All deterministic tests green. **Real
hardware re-validation of THIS fix is still required from the
operator** — this report STOPS before claiming that PASS, per the
checkpoint's own required real-acceptance gate. No Gemini run. Not
pushed.

## VALIDATION CONTRACT CORRECTION (same-day self-check)

Before handing the operator any hardware command, this checkpoint's
own probe was source-audited against the fix it was meant to validate.
**Finding: the probe's `build_probe_pipeline` constructed
`AecReferenceFeeder` with no `gain_source` at all** — byte-for-byte the
pre-fix, unscaled construction. A "0 false barge-ins" result from that
probe would have proven **nothing** about this checkpoint's actual fix,
because the fix lives entirely in `gain_source`. This was caught and
fixed before any hardware command was issued to the operator — see
FIX DECISION / PRODUCTION CHANGE below for the corrected probe wiring,
and ASSISTANT-ONLY DETERMINISTIC TESTS for the new tests
(`tests/test_self_echo_probe_production_gain_parity.py`) that now
structurally guarantee the probe and production stay wired the same
way, so this class of gap cannot silently recur.

## REAL R0052 HARDWARE EVIDENCE

Real reSpeaker XVF3800 + real USB speaker + real AEC reference, operator
completely silent during all silent trials:

| Level | Trials | False confirmed barge-ins |
|---|---|---|
| LOW | 5 | 0/5 |
| NORMAL | 5 | 1/5 (trial 5) |
| MAX | 5 | 5/5 |

Real human control ("przerwij" said deliberately while assistant audio
played): 1/1 correct confirmed barge-in, twice (two separate runs).
`AEC_REF_ACTIVE` (liveness only, per R0052) was `false` at the very
start of each of these 5 JSON files' warm-up print (the reference
`aplay` subprocess had not yet been marked started at the print
statement's moment — it activates moments later once the first
reference PCM flows; every trial's own reference-tap data shows PCM
did flow) — not a fault, simply a timing artifact of when that one
diagnostic print statement runs relative to `AecReferenceFeeder.setup()`.

## VOLUME DEPENDENCE

Mechanically confirmed monotonic escalation: **0/5 → 1/5 → 5/5** as
real speaker volume rises from LOW to NORMAL to MAX. This is the
central, load-bearing real-world fact this checkpoint explains.

## REPEATED FALSE-VAD TIMING

Playback→VAD-start offsets, computed directly from each JSON's own
`playback_start_t`/`vad_spans[0].vad_start_t`:

| Trial | Offset (s) |
|---|---|
| MAX #1 | 1.1229 |
| MAX #2 | 1.0834 |
| MAX #3 | 1.0848 |
| MAX #4 | 1.1056 |
| MAX #5 | 1.0905 |
| NORMAL #5 (the one false trial) | 1.2028 |

MAX mean = **1.0974s**, sample stdev ≈ **0.016s** — a highly repeatable
onset, consistent with the SAME acoustic/content region of the fixture
crossing the local VAD gate every time, not random background noise.
Every one of these 6 events then reached `bargein_confirmed_t` almost
exactly **`confirm_hold_secs` (0.3s)** later (0.301–0.302s in every
case) — exactly the configured sustained-speech hold, with zero
anomalous extra delay, confirming `BargeInController`'s own confirm
timer is operating correctly and is not itself part of the problem.

## REAL JSON COMPARISON

All 5 real JSON files
(`self_echo_probe_20260912T212857Z.json` [LOW],
`self_echo_probe_20260912T213024Z.json` [NORMAL],
`self_echo_probe_20260912T213127Z.json` [MAX],
`self_echo_probe_20260912T213201Z.json` [control #1],
`self_echo_probe_20260912T213236Z.json` [control #2]) were read in
full. Per-trial evidence table (only the fields proven valid — see
SILERO CONFIDENCE/VOLUME ANALYSIS and MIC RESIDUAL ANALYSIS below for
which fields were disregarded and why):

| Level | # | playback dur (ms) | VAD start offset (s) | confirmed? | mic vol_max | quiet-before mic RMS |
|---|---|---|---|---|---|---|
| LOW | 1–5 | ~3504 | — (no VAD) | no | 0.45–0.49 | 186–214 |
| NORMAL | 1–3 | ~3504 | — | no | 0.52–0.57 | 186–189 |
| NORMAL | 4 | 3504 | — (momentary only) | no | 0.72 | 189 |
| NORMAL | 5 | 1506 (cut short by confirm) | 1.203 | **yes** | 0.73 | 188 |
| MAX | 1–5 | ~1387–1427 (all cut short) | 1.08–1.12 | **yes ×5** | 0.79–0.84 | 182–187 |
| control #1 | 1 | 2835 (cut short) | 2.532 | yes (correct) | 0.65 | 282 |
| control #2 | 1 | 1761 (cut short) | 1.458 | yes (correct) | 0.72 | 277 |

The `quiet-before mic RMS` column is the ONE raw-RMS field from R0052's
5 files that is NOT contaminated (see MIC RESIDUAL ANALYSIS) — shown
here only to establish it stayed roughly stable (~180–280) regardless
of level, i.e. the room's own ambient noise floor did not itself
change with speaker volume (as expected — the speaker was silent
during that window).

NORMAL trial 4 is the single most informative non-obvious data point:
its `vol_max` (0.72) crossed `min_volume` (0.6) — same as the false
trial 5 — yet it did **not** produce a VAD start at all. The
distinguishing factor is *sustained* crossing: Pipecat's own
`_vad_start_frames = round(start_secs/0.032) ≈ 6` consecutive analysis
frames (≈192ms) must ALL satisfy `confidence≥0.7 and volume≥0.6`
before `VADUserStartedSpeakingFrame` fires — a momentary spike is not
enough. This is real, uncontaminated evidence (see next section) that
the false triggers are driven by a *sustained*, voice-shaped
energy/confidence region of the assistant's own audio — consistent
with genuine acoustic echo of real speech content, not an isolated
transient.

## MIC RESIDUAL ANALYSIS

**Confirmed instrumentation bug (fixed this checkpoint, see R0052's
erratum):** R0052's `_MicRmsTap` sat right after `transport.input()` in
one linear, bidirectional Pipecat pipeline. Frames the probe injects
via `worker.queue_frames()` (the assistant fixture, queued as
`TTSAudioRawFrame`) enter at the pipeline's own Source and therefore
ALSO pass through that same early tap on their way to
`aec_feeder`/`transport.output()` — alongside the real, independently
arriving `InputAudioRawFrame`s from the actual microphone. R0052's tap
recorded RMS/peak for *any* frame carrying an `.audio` attribute, so
`mic_raw_rms_phase_playback` was a mix of genuine post-hardware-AEC mic
residual AND the raw, un-attenuated assistant PCM in transit — which is
exactly why it came out numerically almost identical to
`ref_raw_rms_phase_playback` in every one of the 5 files, **including
both real "przerwij" control runs**, where a genuine human voice was
also present. That similarity was never real evidence of anything —
it was the tap counting the same bytes twice.

Fix (this checkpoint): `_MicRmsTap` now filters to
`isinstance(frame, InputAudioRawFrame)` — the exact same type
Pipecat's own `VADController.process_frame` gates real VAD analysis on
(confirmed directly from its installed source: `if isinstance(frame,
InputAudioRawFrame): ...`). This makes the tap measure exactly, and
only, what Silero itself analyzes. **This bug did not affect VAD
correctness** — `VADController` never analyzed the injected assistant
frames as speech (confirmed: `TTSAudioRawFrame` and `InputAudioRawFrame`
are sibling branches under `AudioRawFrame`, not related by
inheritance) — only this diagnostic's own recorded telemetry was
wrong. `mic_raw_rms_phase_quiet_before` (recorded before any assistant
PCM exists in the pipeline at all) was never affected and remains valid
uncontaminated evidence of real ambient room noise (~180–280 RMS,
stable across levels).

Because the existing 5 JSON files' `mic_raw_rms_phase_playback` values
must be disregarded, a fresh, now-correctly-instrumented real residual
RMS measurement is part of what the operator's post-fix validation run
below will produce (with `--capture-pcm` also available for a direct
offline cross-correlation, see `cross_correlate_pcm` below — new this
checkpoint, not yet exercised on real hardware).

## FAR-END REFERENCE ANALYSIS

`AecReferenceFeeder.process_frame` (source re-confirmed unchanged)
enqueues `frame.audio` for a genuine `isinstance(frame, TTSAudioRawFrame)`
check, byte-identical and ungained, to `plug:respeaker` — software-level
parity is exact (R0052's finding stands). R0052's `_RefRmsTap` had the
mirror-image bug to `_MicRmsTap` (any `.audio` frame counted, including
real `InputAudioRawFrame`s continuing to flow past that point since
nothing upstream removes them) — fixed the same way, filtered to
`isinstance(frame, TTSAudioRawFrame)` (the same type
`AecReferenceFeeder` itself gates on).

## SILERO CONFIDENCE/VOLUME ANALYSIS

**Confirmed instrumentation bug (fixed this checkpoint):** Silero's
real `voice_confidence()` (pipecat-ai 1.8.1,
`SileroOnnxModel.__call__`) returns a shape-`(1,)` numpy array, not a
bare scalar. `float()` on a shape-`(1,)` array raises `TypeError: only
0-dimensional arrays can be converted to Python scalars` under this
repo's installed NumPy (2.5.2) — reproduced directly, standalone,
against the real installed Silero model:

```
>>> arr = np.array([0.83], dtype="float32")
>>> float(arr)
TypeError: only 0-dimensional arrays can be converted to Python scalars
```

R0052's `_ProbedSilero.voice_confidence` silently caught exactly this
exception and defaulted `self._last_conf = 0.0` on **every single
frame**, in every one of the 5 real captures — this is why
`conf_mean`/`conf_max` read `0.0` everywhere, even during confirmed
real barge-ins. **Production's own decision was never affected**:
Pipecat's real `_run_analyzer` only ever does
`confidence >= self._params.confidence` (a numpy comparison, fine for a
1-element array) and lets Python's `and` call `bool()` on the result
(also fine for 1 element) — it never calls `float()` on the confidence
value at all. Verified directly:

```
>>> bool(np.array([0.83]) >= 0.7)
True
```

Fix: `float(np.asarray(c).reshape(-1)[0])` replaces the bare `float(c)`
— handles both the real shape-`(1,)` array and a bare scalar
identically. New tests
(`tests/test_m2_6b4m_self_echo_probe.py::TestSileroConfidenceConversionFix`)
lock in both the fix and the original failure mode (so a future numpy
relaxing the rule again would not silently make the regression test
meaningless).

**What remains valid despite this bug**: the recorded `vol`
(`_get_smoothed_volume`) values were never affected — that method
already returns a native Python `float` from its own base
implementation, confirmed directly by a standalone call. Hypothesis B
("Silero confidence/volume crosses the production speech gate in the
same assistant-audio region") is therefore **PROVEN**, without even
needing the confidence fix: every trial that produced a real
`VADUserStartedSpeakingFrame` shows `vol_max` clearly above
`min_volume=0.6` (0.72–0.84 across the 6 false trials + 2 real control
trials), and by the pure logical necessity of Pipecat's own gate
(`confidence>=0.7 AND volume>=0.6`, sustained ~192ms) firing a real
frame, confidence *must* also have crossed 0.7 for that same sustained
window — regardless of what R0052's broken tap separately recorded.

## SPEAKER VOLUME OWNERSHIP

Real, direct system audit performed on this Pi (read-only; nothing
written to any mixer):

```
$ aplay -l
card 2: Array [reSpeaker XVF3800 4-Mic Array], device 0: USB Audio
card 3: UACDemoV10 [UACDemoV1.0], device 0: USB Audio

$ cat /etc/asound.conf
pcm.usb_speaker { type plug; slave.pcm "hw:CARD=UACDemoV10,DEV=0" }
pcm.respeaker   { type plug; slave.pcm "hw:CARD=Array,DEV=0" }
ctl.!default    { type hw; card UACDemoV10 }

$ amixer get PCM                     # the OS "default" mixer control
$ amixer -c 3 get PCM                # == UACDemoV10, the USB speaker
Front Left: Playback 100 [68%] [-9.72dB] [on]
  (both commands print byte-identical output)

$ amixer -c 2 get PCM                # == Array, the reSpeaker
Front Left: Playback 60 [100%] [0.00dB] [on]
Front Right: Playback 60 [100%] [0.00dB] [on]
```

**Confirmed facts, not inference:**

1. The reSpeaker (`Array`, card 2) and the USB DAC (`UACDemoV10`,
   card 3) each expose their **own, independent** ALSA hardware
   playback mixer (`amixer -c 2 get PCM` vs `amixer -c 3 get PCM` are
   two entirely separate controls on two separate cards).
2. `/etc/asound.conf`'s `ctl.!default { card UACDemoV10 }` means the
   system's **one** "default" volume control — however the operator
   actually adjusted LOW/NORMAL/MAX (a GUI slider, `alsamixer`,
   `amixer` with no `-c`) — can **only ever reach the USB speaker's own
   mixer**. Verified directly: `amixer get PCM` (no `-c`) prints
   byte-identical output to `amixer -c 3 get PCM`.
3. The reSpeaker's own reference-injection mixer was found sitting at
   its own fixed maximum (60/60, 0.00dB, i.e. unity gain, no
   attenuation) — nothing in NeXa's software or this system's normal
   operation ever touches it.
4. Answering STEP 3's exact questions: **(1)** the operator's real
   speaker-volume change is applied entirely OUTSIDE any PCM copy
   NeXa's software makes — it happens at ALSA's own hardware mixer,
   downstream of everything NeXa's frames ever pass through. **(2)**
   the far-end reference amplitude is therefore **completely
   independent** of the real, physical playback gain — confirmed, not
   assumed. **(3)** yes — `amixer -c <card> get PCM` (or any ALSA
   mixer API) exposes an exact, numeric, dB-scaled gain for both
   cards; NeXa's software previously read neither (confirmed by grep,
   R0052). **(4)** yes — the USB speaker exposes exactly one real
   playback gain control (`'PCM',0`, range 0–147, mapped −28.37dB to
   −0.94dB). **(5)** at MAX, if the operator pushed this mixer near its
   own ceiling, the DAC/amplifier could additionally enter a nonlinear
   region (Class E) — a real, plausible SECONDARY contributor this
   report does not rule out, but not needed to explain the primary
   mechanism below.

**Live-change verification (this checkpoint):** to prove
`CoherentReferenceGain.current_gain()` actually tracks the real mixer
rather than a cached/stale value, the USB speaker's real mixer was
read (100/147, −9.72dB), changed to 50/147 (−19.05dB), re-read through
a fresh `CoherentReferenceGain(card="UACDemoV10")` (`current_gain()` →
`0.11156`, matching `10**(-19.05/20)` exactly), then restored to its
original 100/147 — a real, reversible, local check, no Gemini, no
production code touched:

```
$ amixer -c UACDemoV10 get PCM        # before: 100/147, -9.72dB
$ amixer -c UACDemoV10 sset PCM 50    # change
$ amixer -c UACDemoV10 get PCM        # after: 50/147, -19.05dB
>>> CoherentReferenceGain(card="UACDemoV10").current_gain()
0.11155781513508523                   # == 10**(-19.05/20), exactly
$ amixer -c UACDemoV10 sset PCM 100   # restored
$ amixer -c UACDemoV10 get PCM        # confirmed: 100/147, -9.72dB again
```

## REFERENCE GAIN/TIMING FINDING

Combining SPEAKER VOLUME OWNERSHIP with R0052's own proven
software-level PCM parity: the reference PCM `AecReferenceFeeder` feeds
the XVF3800 is **always** unscaled, digital full-scale-relative audio —
completely disconnected from whatever the USB speaker's own,
independently-adjustable hardware mixer is doing to the *actual*
acoustic output. A hardware AEC's adaptive filter models the
microphone signal as a scaled, time-aligned copy of its reference; when
the true acoustic gain drifts away from what an always-unscaled
reference implies (which it does, arbitrarily, every time "system
volume" changes — the two mixers share no relationship whatsoever),
the adaptive filter's own reference-to-echo model is systematically
wrong, and cancellation degrades. This is a **timing-independent**
mechanism (no clock/lag mismatch required to explain it) and does not
require nonlinear clipping to explain the LOW→NORMAL→MAX escalation,
though clipping at MAX (Class E) may compound it.

## ROOT CAUSE (leading evidence-backed hypothesis, pending real hardware validation)

**Class A/B/2 (charter's decision tree): far-end reference amplitude
mismatch, caused by reference/audible gain ownership incoherence.**
Two independent ALSA hardware mixers exist for the two paths; only the
audible path's mixer is reachable by "system volume," and NeXa's
software has never read or compensated for the resulting, arbitrary
divergence between the digital reference amplitude and the true
acoustic playback amplitude. **What is confirmed, by direct real
system inspection, not inferred from statistics alone:** this
mismatch mechanism genuinely exists on this hardware right now, exactly
as described. **What is NOT yet confirmed:** that this mechanism is
the (or the only) cause of the false self-barge-ins — that claim is a
hypothesis, consistent with every real-hardware observation collected
(the monotonic LOW→NORMAL→MAX escalation, the highly repeatable
~1.08–1.12s onset timing at MAX, and real human "przerwij" speech still
triggering correctly regardless), but a hypothesis only until the
post-fix real hardware run below shows the false confirms actually
disappear. Until then, this section states a **leading evidence-backed
root-cause hypothesis**, not a proven root cause.

Root cause Classes B/C (independent-stack timing divergence between
`aplay` and PortAudio) and D (XVF3800 misconfiguration) remain
unproven and are not needed to explain the evidence; Class E (nonlinear
clipping at MAX) is a plausible secondary contributor at the highest
volume, not excluded, but not required either. If the post-fix
real-hardware run does NOT show the false confirms disappearing, one
or more of these other classes — or a combination with Class A/2 — is
still in play, and this hypothesis would need to be revisited rather
than assumed correct.

## WHY GENERIC VAD TUNING IS REJECTED

No change was made to Silero `confidence`/`min_volume`/`start_secs`, to
`confirm_hold_secs`, to the 500ms preroll, or to any barge-in
sustained-duration constant. The evidence above shows those thresholds
are working exactly as designed — NORMAL trial 4's momentary,
non-sustained volume spike was correctly REJECTED by the existing
6-consecutive-frame requirement, and the confirm-hold timing landed at
almost exactly 0.3s in every false AND every real trial. The defect is
not "VAD is too sensitive" — it is "the reference signal VAD's upstream
hardware AEC relies on no longer reflects the true acoustic gain once
the operator changes real speaker volume." Tuning VAD would either fail
to fix MAX (the mismatch would still exist) or would degrade real
near-field speech detection at LOW/NORMAL (exactly the "degraded normal
microphone recognition" the charter explicitly forbids).

## FIX DECISION

**FIX PRIORITY #2: make reference/audible gain ownership coherent** —
the smallest evidence-backed layer, per the charter's own preferred
order. `nexa.voice.aec_gain.CoherentReferenceGain` reads the **real,
current** ALSA playback gain of the audible output device (normalized
to that device's own 0dB/maximum) and `AecReferenceFeeder` applies the
identical linear scale to the reference PCM before it reaches
`plug:respeaker` — restoring the coupling a hardware AEC reference is
supposed to have with the acoustic signal it models. This assumes the
reSpeaker's own reference-injection mixer stays at the fixed maximum
this checkpoint measured (0dB); nothing in NeXa's software or this fix
ever touches it, and nothing in this system's normal operation is known
to change it — documented as an explicit, checked-in assumption, not
hidden.

No echo-discriminator (Class F) was added: the mechanism this fix
targets is a reference-signal defect, not a BargeInController
confirmation-logic defect, so per the charter's own instruction ("If
root cause is reference timing/signal parity: fix the AEC reference
path, not VAD") the fix belongs at the reference path — pending the
real-hardware run below actually confirming this layer was sufficient.
If it is not, a Class F discriminator remains the documented next
option, not implemented here.

## PRODUCTION CHANGE

- **New:** `src/nexa/voice/aec_gain.py` — `parse_amixer_db_gain`,
  `CoherentReferenceGain`, `apply_gain`, `_run_amixer`. Pure, fully
  dependency-injectable (real `amixer` shelled out to by default;
  tests inject a fake runner + fake clock).
- **`src/nexa/voice_tts/aec_reference.py`** — `AecReferenceFeeder`
  gains an optional `gain_source: Callable[[], float] | None = None`
  constructor parameter. Default `None` preserves the exact prior,
  unscaled behavior byte-for-byte (existing `TestAecReferenceFeeder`
  tests re-verified green, unmodified). When provided, the reference
  PCM is scaled via `apply_gain` before being enqueued; a raising
  `gain_source` is caught and logged, falling back to unscaled PCM for
  that frame rather than ever dropping it.
- **`src/nexa/voice/config.py`** — new
  `LocalAudioConfig.output_alsa_mixer_card: str = "UACDemoV10"` field
  (the ALSA card name backing `output_device_name`), purely additive.
- **`src/nexa/realtime/gemini/runtime.py`** —
  `build_gemini_voice_runtime` now constructs a
  `CoherentReferenceGain(card=cfg.output_alsa_mixer_card)` and passes
  `reference_gain.current_gain` as `AecReferenceFeeder`'s
  `gain_source`. This is the **only** functional change to the cloud
  runtime this checkpoint — no VAD/Silero/BargeInController/preroll
  code touched.
- Bounded cost: `CoherentReferenceGain` re-reads the mixer at most once
  per `refresh_secs` (default 2.0s) — never one `amixer` subprocess per
  audio chunk. A failed or garbled read falls back to the last
  known-good gain, never crashes, never drops a frame.
- Local voice's own `build_bargein_stack` (`src/nexa/voice_tts/__init__.py`)
  never passes `gain_source` — its `AecReferenceFeeder` instances keep
  the exact prior behavior.
- **`docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`**
  (validation-contract correction, see above) — `build_probe_pipeline`
  now constructs the identical `CoherentReferenceGain(card=cfg.
  output_alsa_mixer_card)` and passes `reference_gain.current_gain` as
  `AecReferenceFeeder`'s `gain_source`, exactly like production. Prints
  `audible_mixer_card`/`audible_gain_db`/`audible_linear_gain`/
  `reference_gain_applied` at startup and records
  `reference_gain_applied` in every trial's own JSON summary, so a
  run's own output is itself the proof the fix was active — never an
  assertion the operator has to take on faith.

## ASSISTANT-ONLY DETERMINISTIC TESTS

- `tests/test_voice_aec_gain.py` (new, 18 tests): `parse_amixer_db_gain`
  against the REAL captured `amixer` output shapes from this
  checkpoint's own system audit (USB speaker at −9.72dB, reSpeaker at
  0.00dB), a muted-channel case, garbled/empty input, positive-dB
  clamping; `apply_gain` (identity at 1.0, silence at 0.0, halving at
  0.5, empty-input no-op); `CoherentReferenceGain` (correct real-shape
  read, refresh-window caching, re-read after the window elapses,
  default-1.0-before-any-read, fallback-to-last-known-good on a later
  failed read); `_run_amixer` never raising for a nonexistent binary.
- `tests/test_bargein_m2_5b.py::TestAecReferenceFeederGain` (new, 4
  tests): default `gain_source=None` stays byte-for-byte unscaled;
  `gain_source` scales enqueued PCM; `gain_source` returning 1.0 is a
  true no-op; a raising `gain_source` falls back to unscaled PCM
  without dropping the frame or crashing.
- `tests/test_m2_6b4m_self_echo_probe.py` (extended: 23 from R0052 + 13
  new this checkpoint = 36 total):
  `cross_correlate_pcm` (known positive lag, known negative lag,
  uncorrelated noise gives low correlation, empty reference/mic/pure
  silence all handled without raising), the confirmed Silero
  confidence conversion fix (shape-(1,) ndarray, the original bug
  reproduced as a documented regression guard, a bare scalar), and
  `_gain_to_db_text` (unity gain is 0dB, a known real captured gain
  matches its own dB value, zero/negative gain reports "muted" rather
  than raising on `log10`).
- `tests/test_voice_architecture.py` — updated the existing
  `LocalAudioConfig` field-inventory lock-down test to include the new
  `output_alsa_mixer_card` field (an intentional, additive change to
  an intentionally strict test, not a relaxation of it).
- `tests/test_self_echo_probe_production_gain_parity.py` (new, 7
  tests) — the validation-contract check itself: parses
  `build_gemini_voice_runtime`'s and `build_probe_pipeline`'s own
  source with `ast` (neither function is ever called — both are heavy,
  device-opening functions, and this repo's own convention, confirmed
  by every existing `build_gemini_voice_runtime` test, is `dry=True`
  construction only) and proves each constructs exactly one
  `CoherentReferenceGain` with `card=` sourced from
  `output_alsa_mixer_card`, and exactly one `AecReferenceFeeder` with
  `gain_source=` bound to that SAME instance's `.current_gain` — not
  identical object instances, but identical ownership and calculation
  semantics, exactly as the charter requires. Three meta-tests prove
  this check itself actually rejects the original bug shape (a bare
  feeder with no `gain_source=`, a `gain_source=` from an unrelated
  variable, a `card=` not sourced from the config field) — so the
  check is not an accidental tautology. Two more tests confirm the
  probe imports the REAL `CoherentReferenceGain`/`AecReferenceFeeder`
  classes, never a reimplementation.

All new/modified tests pass; see FULL TEST RESULT.

## REAL USER DOUBLE-TALK TESTS

`tests/test_bargein_m2_5b.py`'s full existing `TestBargeInController`
suite (candidate admission, AEC-down rejection, short-VAD rejection,
sustained-VAD confirmation, single-candidate invariant, silent-operator
zero-candidates) re-verified green, unmodified — this fix never touches
`BargeInController`'s own confirmation logic, so real user-over-assistant
barge-in behavior is provably unchanged at the unit level. The REAL,
live double-talk proof (assistant playing + genuine "przerwij") is the
required real-hardware re-validation below, not yet performed against
this fix.

## CPU/BUFFER BOUNDS

`CoherentReferenceGain.current_gain()` shells out to `amixer` at most
once per `refresh_secs` (2.0s default) — a single, short-lived
subprocess (`timeout=2.0s` hard cap in `_run_amixer`), never per audio
chunk. `apply_gain` is a true no-op (returns the original `bytes`
object unchanged, no `audioop` call at all) whenever gain is exactly
`1.0` — the common case whenever no attenuation is present. When gain
differs from 1.0, `audioop.mul` is a single C-implemented pass over the
PCM buffer (typically 100ms of 16-bit mono audio at a stream's normal
sample rate) — negligible CPU cost, well within Raspberry Pi budget,
identical order of magnitude to the sample-rate conversions `plug:`
devices already perform on every frame.

## R0051 REGRESSION

`tests.test_realtime_gemini_runtime.TestVadBridgePrerollParity` (6
tests) re-run green, unmodified. `PRE_ROLL_MS`/`UtteranceBuffer` and
`_VadToProviderBridge`'s preroll logic were not touched this checkpoint.

Also re-run green, unmodified: `TestAtomicProviderReplacement` (5,
R0045), `TestProductionCanonicalTurnLifecycle` (5, R0046),
`TestNoLocalLidInCloudRuntime` (3, zero local LID),
`TestMidTurnReadinessLoss` + `TestReconnectWiring` (9, connection-loss).

## LOCAL VOICE FREEZE

`git diff --stat -- src/nexa/voice src/nexa/stt` is **not** empty this
checkpoint (unlike every prior R0048–R0051 checkpoint) — this
checkpoint's own charter requires a production fix, and the smallest
correct layer for it is exactly `AecReferenceFeeder`/`LocalAudioConfig`,
files shared between local and cloud voice. What "freeze" means here,
made explicit rather than asserted: **local voice's own TUNING is
untouched** — no VAD threshold, timing constant, or barge-in parameter
changed — and **local voice's own behavior is untouched**, because
`build_bargein_stack` (`src/nexa/voice_tts/__init__.py`, not modified)
never passes the new, opt-in `gain_source` parameter, so its
`AecReferenceFeeder` instances run byte-for-byte the same code path as
before (proven by `TestAecReferenceFeeder`'s original 3 tests passing
unmodified, alongside the 4 new gain-specific tests that exercise only
the new, additive path). `src/nexa/stt` was not touched at all.

## FULL TEST RESULT

- New: `tests/test_voice_aec_gain.py` — 17/17 pass.
- New: `tests/test_bargein_m2_5b.py::TestAecReferenceFeederGain` — 4/4
  pass; full file 42/42 pass (unchanged pre-existing tests included).
- Extended: `tests/test_m2_6b4m_self_echo_probe.py` — 36/36 pass (23
  from R0052 + 13 new this checkpoint, including the validation-contract
  correction's `_gain_to_db_text` tests).
- New: `tests/test_self_echo_probe_production_gain_parity.py` — 7/7
  pass (the validation-contract check itself, added after finding and
  fixing the probe's own gain-wiring gap).
- Updated: `tests/test_voice_architecture.py` — full file 5/5 pass.
- Full project suite:
  `.venv/bin/python -m unittest discover -s tests -p "test_*.py"` →
  **1055 tests, OK (skipped=7)**.
- `ruff check` on every touched file: clean (two import-order/line-
  length findings auto-fixed with `--fix` before commit; the 82
  pre-existing, unrelated findings elsewhere in the repo — e.g.
  `scripts/m1_bench/blind_launcher.py` — are untouched and out of
  scope).
- `pip check`: "No broken requirements found."
- `git diff --check`: clean (exit 0).

## FILES CHANGED

- `src/nexa/voice/aec_gain.py` (new) — coherent reference gain.
- `src/nexa/voice_tts/aec_reference.py` — optional `gain_source` param.
- `src/nexa/voice/config.py` — new `output_alsa_mixer_card` field.
- `src/nexa/realtime/gemini/runtime.py` — wires
  `CoherentReferenceGain` into `AecReferenceFeeder` for the cloud
  pipeline only.
- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
  — fixed the confidence-conversion bug, fixed the mic/reference RMS
  tap contamination, added `cross_correlate_pcm` +
  `--capture-pcm`/`--max-lag-ms` (Step 4's bounded-PCM-window +
  offline correlation capability, available but not yet exercised on
  real hardware); **then, per the validation-contract check, wired
  `CoherentReferenceGain`/`gain_source` into `build_probe_pipeline`
  (it had none) and added startup + per-trial
  `audible_mixer_card`/`audible_gain_db`/`audible_linear_gain`/
  `reference_gain_applied` diagnostic printing + JSON fields.**
- `tests/test_voice_aec_gain.py` (new) — 17 tests.
- `tests/test_bargein_m2_5b.py` — +4 tests
  (`TestAecReferenceFeederGain`).
- `tests/test_m2_6b4m_self_echo_probe.py` — +13 tests
  (`TestCrossCorrelatePcm`, `TestSileroConfidenceConversionFix`,
  `TestGainToDbText`).
- `tests/test_voice_architecture.py` — updated the `LocalAudioConfig`
  field-inventory lock-down test.
- `tests/test_self_echo_probe_production_gain_parity.py` (new) — 7
  tests; the validation-contract check.
- `docs/reports/R0052_..._20260912.md` — erratum recording the real
  hardware evidence and the two confirmed probe bugs.
- `docs/reports/R0053_..._20260912.md` (this report).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated (below).

## COMMIT HASHES

- `009a0e7` — fix: coherent AEC reference/audible gain -- self-echo
  root cause fix (M2.6B.4N / R0053).

## GIT STATUS

Clean working tree after commit (no push).

## EXACT LOCAL-ONLY HARDWARE VALIDATION COMMAND

**Step 0 — cheap preliminary smoke test, run this first:** MAX volume,
3 silent trials, before committing to the full 5/10/10/5 + 3-control
matrix below. Watch the printed `audible_mixer_card`/`audible_gain_db`/
`audible_linear_gain`/`reference_gain_applied` lines — they prove the
fix is actually active in this run, not merely assumed.

```bash
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level max --repeats 3
```

**Expected preliminary result: MAX 0/3 false confirmed barge-ins.** If
MAX still self-interrupts here, STOP — do not run the rest of the
matrix — and report back the full printed output (including the
gain-diagnostic lines) plus the JSON path for evidence-based
inspection instead.

Only once the 0/3 preliminary result is confirmed, proceed to the full
acceptance matrix. Operator: remain **completely silent** for every
silent-operator run; set your **real** speaker volume to the labeled
level before each one. No Gemini is involved in any of these.
`--capture-pcm` is optional but recommended on at least the MAX run and
one control run — it saves bounded raw PCM (mic + reference) as WAV
pairs and prints an offline cross-correlation result, giving direct,
additional confirmation beyond the confirmed-barge-in count.

```bash
# 1) LOW volume -- 5 trials, expect 0/5 confirmed barge-ins:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level low --repeats 5

# 2) NORMAL volume -- 10 trials, expect 0/10:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level normal --repeats 10

# 3) MAX volume -- 10 trials, expect 0/10:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level max --repeats 10 --capture-pcm

# 4) MAX volume, LONGER fixture (~6.6s, a different real recorded
#    phrase) -- proves the fix is not overfit to the 3.5s default:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level max --repeats 5 \
  --wav docs/research/m2_4b_bilingual_stt/fixtures/audio/010_pl_ktora_bedzie_godzina_za_dziewiecdziesiat.wav

# 5) Real human control -- say "przerwij" once assistant playback
#    starts, run at least 3 times:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --control --capture-pcm
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --control
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --control
```

Required real acceptance: **0/5** confirmed barge-ins at LOW, **0/10**
at NORMAL, **0/10** at MAX (default fixture), **0/5** at MAX with the
longer fixture, and **at least 3/3** correct confirmed barge-ins on the
"przerwij" control. Report the printed summaries + JSON paths (and, if
`--capture-pcm` was used, the printed `cross_correlation` result and
WAV paths under `docs/research/m2_6_cloud_realtime_voice/
self_echo_captures/pcm/`, all git-ignored) back for a final PASS
determination — **not claimed here.**
