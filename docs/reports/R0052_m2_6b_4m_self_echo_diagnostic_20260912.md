# R0052 — False Self-Barge-In / Speaker-Echo Root Cause and Fix

**Date:** 2026-09-12
**Milestone:** M2.6B.4M (self-echo / false-barge-in diagnostic, following
R0051's accepted 500ms preroll)
**Status:** SOURCE AUDIT complete. Local self-echo probe built,
offline-tested, and structurally validated (`--dry` + a construction
smoke test on this dev machine). **Root cause NOT YET determined** —
per this checkpoint's own explicit gate, the real evidence run has not
been performed by me (no Gemini, and this is a real-hardware
diagnostic that requires the operator's real reSpeaker + real USB
speaker + real silence). This report therefore STOPS at the
diagnostic-tool result and hands the operator one exact command.
**No production code changed. No Gemini run. Not pushed.**

## REAL LIVE FAILURE

From a live Gemini conversational run (R0051-era production code,
`preroll_ms=500`, unrelated to and not reopening that fix): while NeXa
was speaking through the USB speaker, she sometimes triggered her own
local barge-in. The terminal repeatedly showed `✂ cloud interruption
acknowledged by provider` even though the operator was not
intentionally interrupting. This was observed **especially at
high/maximum speaker volume**. `AEC_REF_ACTIVE` was present throughout
(the far-end reference feed was running) — so the failure is not "no
reference at all," it is "a live reference exists, but residual/echoed
assistant speech is still sometimes strong enough at the mic to
satisfy local VAD's own generic speaking gate."

## SOURCE TRACE

Production pipeline order, confirmed unchanged in
`src/nexa/realtime/gemini/runtime.py` (`build_gemini_voice_runtime`):

```
transport.input()   # mic capture, AFTER the XVF3800's own onboard AEC
  -> vad_processor   # SileroVADAnalyzer, VADParams(stop_secs=0.5)
  -> bargein         # BargeInController
  -> bridge          # _VadToProviderBridge (R0049/R0051 preroll, untouched)
  -> aec_feeder      # AecReferenceFeeder (tees assistant PCM to plug:respeaker)
  -> transport.output()  # audible plug:usb_speaker
```

Traced end to end against the actually-installed source (not
documentation, not memory):

- `src/nexa/voice_tts/aec_reference.py` — `AecReferenceFeeder`.
- `src/nexa/voice/aec.py` — `AecReferenceHealth`.
- `src/nexa/voice/bargein.py` — `BargeInController`, `InterruptContext`.
- `src/nexa/voice/interruption.py` — `InterruptionStateMachine`,
  `DEFAULT_CONFIRM_HOLD_SECS = 0.3`.
- `src/nexa/realtime/gemini/runtime.py` — `_ResponseLifecycle`,
  `build_gemini_voice_runtime`'s exact hardware pipeline construction.
- `.venv/lib/python3.13/site-packages/pipecat/audio/vad/vad_analyzer.py`
  — `VADAnalyzer._run_analyzer` (confirms exact per-frame order:
  `voice_confidence()` computed, THEN `_get_smoothed_volume()`, THEN
  `speaking = confidence >= params.confidence and volume >=
  params.min_volume`).
- `src/nexa/voice/config.py` — `LocalAudioConfig` (device names,
  `plug:` resampling docstring).
- `src/nexa/voice/device.py` — `find_device_index`.
- No `amixer` / software-volume code exists anywhere in
  `src/nexa/voice` or `src/nexa/voice_tts` (grepped; zero matches).

## WHAT AEC_REF_ACTIVE ACTUALLY MEANS

`AecReferenceHealth` (`src/nexa/voice/aec.py`):

```python
@property
def active(self) -> bool:
    """The AEC far-end reference feed is currently running."""
    return self._active

@property
def barge_in_safe(self) -> bool:
    """Whether it is safe to admit an interruption on a hot mic right
    now. Barge-in code MUST gate on this."""
    return self._active
```

`self._active` is flipped to `True` only by `mark_started()`, which
fires once the reference-feed's `_PcmSink` (a persistent
`aplay -D plug:respeaker` subprocess) is confirmed alive. **This is a
pure liveness check.** It answers exactly one question — "is a PCM
stream currently being fed to the reference device at all" — and
answers zero questions about: residual echo energy, dB of cancellation
achieved, correlation between reference and mic signal, or whether the
XVF3800's internal AEC adaptive filter has actually converged. No code
anywhere in this class, `AecReferenceFeeder`, or elsewhere in the
codebase measures any of those things. The charter's own caution —
"Do NOT infer from AEC_REF_ACTIVE that cancellation quality is
sufficient" — is confirmed correct by source, not merely repeated as a
warning.

## REFERENCE SIGNAL PARITY

`AecReferenceFeeder.process_frame` (`src/nexa/voice_tts/aec_reference.py`)
enqueues `frame.audio` **unchanged** into its writer queue, and
forwards the **same, unmodified** `TTSAudioRawFrame` object downstream
to `transport.output()` via `push_frame`. There is no resample, no
gain, no reordering, no format conversion performed by NeXa's own code
between the reference path and the audible path — they receive
byte-identical PCM, in the same order, at the software level. This is
proven by direct reading of `process_frame`/`_enqueue`, not inferred.

Combined with the grep-confirmed absence of any `amixer`/software gain
anywhere in `src/nexa/voice*`, this means: **any amplitude difference
between what reaches the reference device and what reaches the audible
speaker must originate outside NeXa's own software** — most plausibly
a hardware volume knob or an OS/ALSA per-device mixer that affects only
the physically separate USB DAC (`plug:usb_speaker`), with no
correlated effect on the digitally separate `plug:respeaker` reference
injection path. **Software-level PCM parity: proven exact.
Physical/acoustic-level amplitude parity: NOT observable or
controllable from NeXa's own code, and NOT yet measured.**

## REFERENCE TIMING

Two independent OS-level audio mechanisms carry the two paths:

- Reference: a raw `aplay` CLI subprocess (ALSA) writing to
  `plug:respeaker`.
- Audible: PyAudio/PortAudio via Pipecat's `LocalAudioTransport.output()`
  writing to `plug:usb_speaker`.

Both are ALSA `plug:` devices, and per `LocalAudioConfig`'s own
docstring each resamples independently to its underlying hardware's
native rate (the reSpeaker's array is natively 16kHz, the USB DAC is
natively 48kHz stereo). Two independently-driven playback stacks with
two independent resampling filters is a real, source-grounded
mechanism by which the reference signal could reach the XVF3800's AEC
DSP measurably earlier or later than the same PCM reaches the room
acoustically through the speaker — which would degrade or defeat
linear echo cancellation even with byte-identical PCM. **This is a
plausible root-cause class (B/C), not yet confirmed** — confirming or
ruling it out requires a real-hardware timing measurement, which
software source reading alone cannot provide.

## REFERENCE LEVEL/GAIN

No software gain stage exists on either path (see REFERENCE SIGNAL
PARITY above) — this section restates that finding from the
gain-specific angle the charter asks for: `AecReferenceFeeder` never
scales `frame.audio` samples, and no `amixer`/PortAudio volume call
exists in the traced path. If reference-vs-speaker level mismatch is
real, it is a **hardware/system-level** phenomenon a physical volume
knob or the OS's own default-sink mixer for `plug:usb_speaker`
introduces, invisible to NeXa's own PCM. This uniquely and cleanly
would explain why the failure is reported as **volume-dependent**: a
pure pipeline-timing-jitter explanation would be expected to misbehave
fairly independent of how loud the physical speaker is turned up,
whereas a hardware volume stage sitting only on the audible path would
not.

## FALSE VAD TRACE

Silero's own per-frame computation (`VADAnalyzer._run_analyzer`,
Pipecat 1.8.1, confirmed by direct source read) is:

```python
confidence = self.voice_confidence(audio_frames)
volume = self._get_smoothed_volume(audio_frames)
speaking = confidence >= self._params.confidence and volume >= self._params.min_volume
```

This runs on the **post-hardware-AEC** mic signal only — whatever
residual energy the XVF3800 leaves after attempting cancellation.
There is no second, software-side echo-discrimination stage anywhere
between `transport.input()` and `vad_processor`. If the XVF3800's
cancellation is insufficient at a given volume/timing/gain condition,
the residual assistant speech is indistinguishable, at this point in
the pipeline, from real user speech — Silero has no way to know the
difference, because no information about "this residual might be
assistant-correlated" is passed to it.

## BARGE-IN CONFIRMATION TRACE

`BargeInController` (`src/nexa/voice/bargein.py`) admits a candidate
only when:

1. `InterruptionStateMachine.response_in_flight` is true (the state
   machine is in `RESPONDING`/`INTERRUPT_CANDIDATE`/`INTERRUPTING`),
   **and**
2. `AecReferenceHealth.barge_in_safe` is true (pure liveness, per
   above),

then requires **`confirm_hold_secs = 0.3s`** of continuously-sustained
local VAD "speaking" state (the exact same generic Silero gate used
for ordinary user turns — `confidence >= 0.7 and volume >= 0.6` by
default), via `InterruptionStateMachine.poll()`, with no intervening
VAD-stop, before calling `on_confirmed`. **There is no additional
echo-discrimination signal anywhere in this confirmation path.** This
makes Class F of the charter's decision tree ("BargeInController
should require evidence beyond raw local VAD while assistant playback
is active") a real, source-confirmed candidate fix — but so are
Classes A/B/C/E, which would instead point at fixing the reference
path itself rather than the confirmation logic. Source reading alone
cannot decide between these; only real measured data can.

## LOCAL SELF-ECHO PROBE

Built: `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`.

Uses the REAL production classes, imported directly (never
reimplemented): `AecReferenceFeeder`, `AecReferenceHealth`,
`BargeInController`, `_ResponseLifecycle`, `LocalAudioConfig` +
`find_device_index`, and the SAME `SileroVADAnalyzer` +
`VADParams(stop_secs=0.5)` construction `build_gemini_voice_runtime`
uses. NO Gemini, NO cloud credentials, NO LLM, NO STT. Pipeline order
exactly mirrors production (`transport.input() -> vad_processor ->
bargein -> aec_feeder -> transport.output()`), with only
non-mutating, non-dropping diagnostic taps inserted (`_MicRmsTap`,
`_RefRmsTap`, `_VadWatcher`, `_PlaybackWatcher` — each forwards every
frame unchanged after recording telemetry).

Assistant-speech fixture: defaults to the already-committed
`docs/research/m2_voice_spikes/asr_test_samples/en_explain_gravity.wav`
(16-bit mono, 16kHz, 3.5s real recorded speech — acoustically
equivalent to synthesized assistant speech for the purpose of
exercising the room/AEC mechanism this probe measures, and avoids any
new dependency on a running Piper HTTP server). `--synthesize-piper
TEXT [--voice pl|en]` is available as the charter's documented
fallback, calling the existing, frozen `PiperHttpServer`/`PiperHttpConfig`
API unmodified, for a true assistant-voice fixture if preferred.

Simulates the real playback lifecycle using the real
`_ResponseLifecycle` combinator (never a timer): `notify_response_dispatched()`
+ `mark_dispatched()` before queuing audio, chunked `TTSAudioRawFrame`s
at a realistic ~100ms cadence, `mark_audio_produced()`, a
`TTSStoppedFrame` + `mark_generation_done()`, with `observe_bot_started()`/
`observe_bot_stopped()` driven by real `BotStartedSpeakingFrame`/
`BotStoppedSpeakingFrame` frames tapped upstream of `transport.output()`
— exactly production's own event chain, never a reimplementation.

Instrumentation (exact names, all present): `PLAYBACK_START_T`/
`PLAYBACK_END_T` (via `Recorder.mark_playback_start/end`, driven off
real `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame`),
`AEC_REF_ACTIVE` state (printed at warm-up, and on every transition via
`AecReferenceHealth`'s own `on_change` hook), `VAD_START_T`/`VAD_END_T`/
`VAD_DURATION_MS` (via `_VadWatcher` + `speaking_intervals`),
`BARGEIN_CANDIDATE_T`/`BARGEIN_CONFIRMED_T`/`BARGEIN_REJECTED_T` (via
`BargeInController`'s own `on_candidate`/`on_confirmed`/
`on_candidate_rejected` hooks), `assistant_playing` (derivable from the
`playback_start_t`/`playback_end_t` window in each summarized trial),
real Silero confidence/volume/speaking per analysis frame (via a
`_ProbedSilero` subclass overriding `voice_confidence()`/
`_get_smoothed_volume()` non-invasively, mirroring the already-proven
pattern in `docs/research/m2_5_bargein/spike_playback_false_vad.py`'s
own `_ProbedSilero` — never a reimplementation, never an invented
threshold beyond what Silero itself reports), and raw mic/far-end
reference RMS + peak in windowed phases (`_MicRmsTap`/`_RefRmsTap`,
tapped immediately before the frames reach `vad_processor` and
`aec_feeder` respectively, so the recorded values can never diverge
from what those real components actually receive).

Supports the three required silent-operator volume levels
(`--level {low,normal,max}` — an operator-set REAL hardware volume
label; the probe cannot control or discover the actual amplitude
mechanism from software, per REFERENCE LEVEL/GAIN above, so the
operator sets their real speaker volume before each run and the probe
only labels the trial), `--repeats N` for repeated silent trials at a
level, and `--control` for the one required real human "przerwij" test.

**Validation performed by me (structural only, no real hardware
claim):** `--dry` constructs a real `SileroVADAnalyzer` with the
production `VADParams(stop_secs=0.5)` and prints its params — passes.
A direct construction smoke test (`build_probe_pipeline` called with
the default WAV fixture, on this dev machine) links the full 10-stage
pipeline (`transport.input() -> _MicRmsTap -> VADProcessor ->
_VadWatcher -> BargeInController -> _RefRmsTap -> AecReferenceFeeder ->
_PlaybackWatcher -> transport.output()`) with zero exceptions and
returns real, correctly-typed `PipelineWorker`/`BargeInController`/
`_ResponseLifecycle`/`AecReferenceHealth` instances. **This proves the
probe is wired correctly against the real production API surface — it
does NOT constitute a real-hardware self-echo measurement**, since this
dev machine's actual reSpeaker/USB-speaker acoustic behavior was not
exercised end-to-end with real audio flowing.

23 new deterministic offline tests
(`tests/test_m2_6b4m_self_echo_probe.py`) cover the probe's pure logic
only: `_pcm_ms`/`_rms`/`_peak` helpers, `speaking_intervals` (single/
multiple/open/ignored-extra-stop/duplicate-start/empty cases),
`rms_windows_from_events` and `raw_rms_windows_from_events` (window
membership, half-open boundary, empty-window), and `summarize_trial`
(clean pass, confirmed-false-barge-in flagging, rejected-candidate
non-flagging, missing playback bounds, missing/present raw RMS frame
lists). All 23 pass. None of these tests exercise real audio hardware
or claim any real-world self-echo result.

## ROOT CAUSE

**PENDING real hardware data.** Per this checkpoint's own explicit
gate ("R0052 may implement the fix ONLY AFTER the local probe
mechanically identifies the cause... If evidence is still ambiguous:
STOP at diagnostic result. Do not guess."), I have not run the probe
against real audio hardware — this is a real-microphone, real-speaker,
silent-operator diagnostic that only the operator can execute
correctly (I have no way to guarantee silence, set a real physical
volume level, or say "przerwij"). The SOURCE TRACE above establishes
that Classes A (hardware/system-level amplitude mismatch, most likely
given the volume-dependence of the failure), B/C (independent
resampling/timing divergence between the two playback stacks), and F
(no echo-discrimination beyond raw VAD in `BargeInController`) are all
real, source-grounded candidates; source reading cannot rank or
eliminate them. Class D (XVF3800 misconfiguration) and Class E
(nonlinear clipping at max volume) remain possible but have no
source-level evidence either way. **STOP — awaiting the operator's
real-hardware probe run below.**

## FIX DECISION

**PENDING.** Not made. No production code will be changed until the
real evidence run returns a mechanically-identified cause, per the
charter's explicit instruction.

## PRODUCTION CHANGE

**None in this checkpoint.** `git diff --stat -- src/` is empty for
this session (confirmed below) — only a new diagnostic tool, a new
test file, and `.gitignore` were touched.

## ASSISTANT-ONLY RESULT

**PENDING** real hardware data at LOW/NORMAL/MAX.

## REAL USER BARGE-IN CONTROL RESULT

**PENDING** the operator's real "przerwij" control trial.

## R0051 REGRESSION RESULT

R0051's own test suite (`TestVadBridgePrerollParity`,
`TestAtomicProviderReplacement`, `TestProductionCanonicalTurnLifecycle`,
`TestVadBridgeQuarantine`, zero-LID tests) all still pass — see FULL
TEST RESULT below; nothing in `src/nexa/realtime/gemini/runtime.py`,
`src/nexa/realtime/router.py`, `src/nexa/realtime/turn.py`, or
`src/nexa/stt/utterance_buffer.py` was touched this checkpoint. The
canonical 500ms preroll (`PRE_ROLL_MS`) is untouched.

## LOCAL VOICE FREEZE RESULT

`git diff --stat -- src/nexa/voice src/nexa/voice_tts src/nexa/stt`
is empty — confirmed clean, no local-voice-tuning code was touched.

## FULL TEST RESULT

- New: `tests/test_m2_6b4m_self_echo_probe.py` — 23/23 pass.
- Full project suite: `.venv/bin/python -m unittest discover -s tests
  -p "test_*.py"` → **1014 tests, OK (skipped=7)**.
- `ruff check` on both new files: clean (one import-order finding
  auto-fixed with `--fix` before commit).
- `pip check`: "No broken requirements found."
- `git diff --check`: clean (exit 0).

## FILES CHANGED

- `docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py`
  (new) — the local-only self-echo/false-barge-in diagnostic probe.
- `tests/test_m2_6b4m_self_echo_probe.py` (new) — 23 deterministic
  offline tests for the probe's pure logic.
- `.gitignore` — added
  `docs/research/m2_6_cloud_realtime_voice/self_echo_captures/` (real
  hardware telemetry JSON, never committed, mirroring the existing
  `ingress_captures/` convention from R0048).
- `docs/reports/R0052_m2_6b_4m_self_echo_diagnostic_20260912.md` (this
  report).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated truthfully
  (below).

No file under `src/nexa/realtime`, `src/nexa/voice`, `src/nexa/voice_tts`,
or `src/nexa/stt` was modified.

## COMMIT HASHES

- `6804ace` — research: false self-barge-in / speaker-echo root cause
  diagnostic (M2.6B.4M / R0052).

## GIT STATUS

Clean working tree after commit (no push).

## EXACT LOCAL HARDWARE VALIDATION COMMAND

Operator: remain **completely silent** for each silent-operator run;
set your **real** speaker volume to the labeled level before each one.
No Gemini is involved in any of these.

```bash
# 1) LOW volume -- set your real speaker to a low level, stay silent:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level low --repeats 5

# 2) NORMAL volume -- set your real speaker to your normal level, stay silent:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level normal --repeats 5

# 3) MAXIMUM volume -- set your real speaker to maximum, stay silent:
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --level max --repeats 5

# 4) Human control -- assistant speaks, you deliberately say "przerwij":
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4m_self_echo_probe.py --control
```

Required real acceptance for a clean self-echo result: **0 confirmed
barge-ins** at LOW, **0** at NORMAL, **0** at MAX, and **exactly 1**
correct confirmed barge-in on the "przerwij" control. Any confirmed
barge-in during a silent run is the false self-echo this checkpoint
exists to diagnose — report the printed summary and the JSON path
(`docs/research/m2_6_cloud_realtime_voice/self_echo_captures/
self_echo_probe_<timestamp>.json`, git-ignored) back for the root-cause
determination and fix, which will be R0053 (or a continuation of this
checkpoint) — **not decided or implemented here.**

---

## ERRATUM (R0053) — REAL HARDWARE RESULT + TWO CONFIRMED PROBE BUGS

The operator ran the exact command above on the real reSpeaker + real
USB speaker. **Real result:**

| Level | Trials | False confirmed barge-ins | Verdict |
|---|---|---|---|
| LOW | 5 | 0/5 | PASS |
| NORMAL | 5 | 1/5 (trial 5) | **FAIL** |
| MAX | 5 | 5/5 | **FAIL** |
| Control ("przerwij") #1 | 1 | 1/1 correct | PASS |
| Control ("przerwij") #2 | 1 | 1/1 correct | PASS |

At MAX, the false VAD onset landed at an almost identical relative
offset after playback start in all 5 trials (~1.08–1.12s), and every
confirmed interruption followed ~0.301–0.302s later (the configured
`confirm_hold_secs`) — a highly repeatable pattern, not random noise.
Full ingestion, analysis, and root-cause determination is R0053 (see
`docs/reports/R0053_...md`).

**Two real bugs in THIS probe's own instrumentation were found while
analyzing that real data** — both now fixed in R0053, neither affects
the validity of the VAD/barge-in timing/count evidence above:

1. **Silero confidence always recorded as 0.0.** Silero's real
   `voice_confidence()` (pipecat-ai 1.8.1) returns a shape-(1,) numpy
   array; `float()` on it raises `TypeError` under this repo's
   installed NumPy (2.5.2) — R0052's probe silently caught that and
   defaulted to 0.0 on every single frame, in every one of the 5 real
   captures above. **Every `conf_mean`/`conf_max` field in those 5
   JSON files is therefore invalid and must be disregarded.**
   Production's own decision was never affected (it only ever
   compares/`bool()`s the array, which numpy permits for one element).
   The `vol_mean`/`vol_max`/`speaking_frame_frac` fields in the same
   files ARE valid (a different, unaffected code path).
2. **`mic_raw_rms_phase_*`/`ref_raw_rms_phase_*` were cross-contaminated.**
   The probe is one linear, bidirectional Pipecat pipeline; frames
   queued via `worker.queue_frames()` (the injected assistant fixture)
   enter at the pipeline's own Source and so pass through the earlier
   `_MicRmsTap` too, on their way to `aec_feeder`/`transport.output()`
   — alongside the real, separately-arriving mic `InputAudioRawFrame`s.
   R0052's taps recorded RMS/peak for ANY frame with an `.audio`
   attribute, so `mic_raw_rms_phase_playback` was contaminated with
   raw, un-attenuated assistant PCM in transit, not real
   post-hardware-AEC residual echo — this is why it came out nearly
   identical to `ref_raw_rms_phase_playback` in every one of the 5
   files, including both real "przerwij" controls. **All 5 files'
   `mic_raw_rms_phase_playback`/`ref_raw_rms_phase_playback` fields
   must be disregarded as a mic-vs-reference amplitude comparison.**
   `mic_raw_rms_phase_quiet_before` (before any assistant PCM is
   queued) was NOT affected and remains valid.

Both bugs are now fixed (frame-type filtering added to both taps;
`np.asarray(c).reshape(-1)[0]` replaces the bare `float(c)`), with new
regression tests. **None of this reopens or invalidates the VAD
start/stop timestamps, playback timestamps, or barge-in
candidate/confirmed/rejected counts and timing above** — those come
from real `VADUserStartedSpeakingFrame`/`BotStartedSpeakingFrame`/
`BargeInController` hooks, an entirely different, unaffected code path
(confirmed by direct source read of Pipecat's own `VADController.
process_frame`, which gates real VAD analysis on `isinstance(frame,
InputAudioRawFrame)` alone). See R0053 for the full analysis, the real
ALSA system audit, root cause, and production fix.
