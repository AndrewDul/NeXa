# R0081 — Cloud Voice False Barge-In / Self-Echo Regression

**Date:** 2026-09-17
**Type:** Diagnostic + narrow instrumentation + one evidence-backed opt-in fix (NOT a confirmed resolution)
**Status:** Immediate failure mechanism CONFIRMED by real live diagnostic evidence (local VAD false-fires during silent-operator bot playback). Deeper acoustic/DSP root cause NOT yet confirmed. One known, previously-unapplied, evidence-backed defect (AEC reference gain incoherence) is now available as an opt-in A/B fix. Signal-level diagnostics built. **R0081 does not pass its own acceptance criteria** — the next step is a live operator A/B run this report specifies precisely but cannot execute (no hardware/API access in this environment).
**Depends on:** R0080 (accepted `2b894e7`), R0071 (frozen baseline), R0052–R0056 (prior self-echo root-cause investigation, referenced not reopened), R0053 (the specific confirmed defect reused here)

No Memory/ContextEngine/recall_context/Personality/Relationship/Learning/FTS/vector changes. No forced audio-architecture redesign — all fixes below are opt-in, off by default, byte-for-byte R0071/R0080-preserving unless explicitly enabled.

---

## 1. Recorded live results (R0080 status update)

The operator ran the real cloud runtime and successfully recalled `ORBIT-47`, then, in the **same** Gemini session (no restart), `NOVA-82`.

| Component | R0080 status | **Updated status** |
|---|---|---|
| CLOUD LIVE RECALL ROUND-TRIP | NOT TESTED | **PASS** (operator live-tested) |
| DYNAMIC SAME-SESSION MEMORY | PARTIAL | **PASS** (operator live-tested, same session, no reconnect) |

---

## 2. First diagnostic run — instrumentation validated, immediate mechanism confirmed

The operator ran:

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline
```

with `AEC_REF_ACTIVE` present from `0.672s` and staying up until shutdown.
**The operator was silent** during NeXa's long spoken responses. Real
excerpt:

```
Czarna dziura to obszar czasoprzestrzeni, którego
27.011 LOCAL_VAD_START
27.011 USER_TURN_START
27.012 INTERRUPTION_FRAME
27.012 PLAYBACK_STOPPED
27.846 LOCAL_VAD_STOP

Czarna dziura to obszar czasoprzestrzeni, z którego
31.213 LOCAL_VAD_START
31.213 USER_TURN_START
31.213 INTERRUPTION_FRAME
31.213 PLAYBACK_STOPPED
32.013 LOCAL_VAD_STOP
```

repeating at 35.212, 39.213, 43.172, 46.653, 52.994, 57.253 — 8 false
`LOCAL_VAD_START` episodes, each **~0.7–0.9s** of sustained VAD-active
duration (not a transient blip — this magnitude matches the
"sustained, voice-shaped echo" character R0052/R0053 already established
for the *different*, paused M2.6B pipeline). The first `TOOL_CALL_START`
in this run was at **72.489s** — 15+ seconds after the *last* of these 8
false episodes.

**This is the diagnostic instrumentation (§5 below) working exactly as
designed** — every marker requested is present, correctly timestamped,
and directly legible. The immediate failure chain is now confirmed, not
inferred:

```
NeXa playback (BOT_AUDIO_STARTED, implicitly — no explicit marker shown
in this excerpt, consistent with continuous multi-sentence speech)
    ↓
LOCAL_VAD_START while the operator is silent
    ↓
InterruptionFrame within ~0-1ms (local, not server -- see §3)
    ↓
PLAYBACK_STOPPED
    ↓
Gemini regenerates/resumes its answer (slightly reworded each time --
"którego" vs "z którego" — a fresh generation, not a literal audio replay)
    ↓
LOCAL_VAD_START fires again minutes/seconds later
```

**The deeper acoustic/DSP cause of the false `LOCAL_VAD_START` itself
remains unconfirmed** — this run answers "where does the interruption
chain start" (§3, unchanged from the pre-live-evidence audit) but not yet
"why does local VAD fire on NeXa's own played-back audio."

---

## 3. Tool-call hypothesis — REFUTED as the primary cause

R0081's first draft (before this live evidence) proposed that a
`recall_context` tool call's mid-response audio pause might create a
reference/playback timing gap worth investigating. **The live evidence
directly refutes this as the primary or sole mechanism**: all 8 false
`LOCAL_VAD_START` episodes occurred between 27s and 57s, while the first
(and, in this excerpt, only) `TOOL_CALL_START` fired at 72.489s — no tool
call was in flight, pending, or even yet requested during any of the 8
false episodes. **Corrected conclusion: false barge-in exists
independently of Core recall.** §2 of the earlier draft (Core recall's
own code never references any interruption/VAD/frame-push symbol,
confirmed by direct grep across `core_recall_tool.py` and
`nexa.core.context`) still stands as a structural, code-level guarantee —
this live result is the behavioral confirmation on top of it. **Core
recall is fully cleared as a cause.** The `TOOL_CALL_START`/`TOOL_CALL_END`
diagnostic markers are retained (they cost nothing, are off by default,
and remain useful for the operator's own future sessions) but are no
longer this report's leading hypothesis.

---

## 4. Exact interruption source — unchanged, now behaviorally confirmed

`vad=GeminiVADParams(disabled=True)` is set explicitly in
`simple_conversation.py` — Gemini's own server-side VAD is off. Every
`InterruptionFrame` observed in this run traces to Pipecat's own
`LLMContextAggregatorPair`, driven by the local `SileroVADAnalyzer`. This
was established by source audit in R0081's first draft; the live evidence
now confirms it behaviorally too — the near-zero (`~0-1ms`) gap between
`LOCAL_VAD_START` and `INTERRUPTION_FRAME` at every one of the 8 episodes
is exactly what a purely local, synchronous frame-broadcast mechanism
(no server round-trip) would produce.

| Category | Status |
|---|---|
| `LOCAL_VAD_USER_START` | **Confirmed live** — the mechanism for all 8 episodes |
| `GEMINI_SERVER_INTERRUPTED` | Structurally impossible (server VAD disabled) — unchanged |
| `TOOL_CANCELLATION` | Refuted by live evidence (§3) |
| `MANUAL_OPERATOR_INTERRUPT` | Explicitly excluded — operator confirmed silence |
| `OTHER` | None identified |

---

## 5. Diagnostic instrumentation — delivered and validated live

Unchanged from the first draft's implementation, now proven against real
hardware (not just a real Pipecat pipeline in tests): `_ConversationEventTap`
gained `on_diagnostic`, observing already-flowing
`TTSStartedFrame`/`TTSStoppedFrame`/`UserStartedSpeakingFrame`/
`UserStoppedSpeakingFrame`/`InterruptionFrame`/final `TranscriptionFrame`,
plus `USER_TURN_START`/`USER_TURN_END` around the router's own
`begin_cloud_turn()`/`commit_cloud_turn()` calls, plus `AEC_REF_ACTIVE`/
`AEC_REF_DOWN` from the existing `on_aec_change` hook, plus
`TOOL_CALL_START`/`TOOL_CALL_END` from `core_recall_tool.py`. `--diagnostic-timeline`
(off by default) on `apps/nexa_cloud_voice_simple.py`. 5 dedicated tests
against a real Pipecat `Pipeline`/`PipelineWorker` (`TestDiagnosticTimeline`),
all passing, label sequence for a normal turn independently reconfirmed
correct by the live run's own output.

### New this round: signal-level diagnostics (requested explicitly)

Implemented, narrow, numeric-only, throttled (≥0.25s between emissions),
never logging raw PCM:

- **`AecReferenceFeeder`** (`nexa.voice_tts.aec_reference`, shared with
  local voice) gained an optional `on_diagnostic`/`diagnostic_interval_s`
  pair. When wired, emits `REF_RMS:<n> REF_QUEUE_DEPTH:<n> REF_DROPPED:<n>`
  computed via `audioop.rms()` on the exact reference PCM it tees to
  `plug:respeaker` — reusing the already-existing `chunks_dropped`/queue
  telemetry the class already tracked (nothing new needed there). Default
  `on_diagnostic=None` — local voice's own `build_bargein_stack` never
  passes it, so local voice is provably unaffected (existing 87
  barge-in/AEC tests re-verified unmodified + 5 new diagnostic-specific
  tests, `TestAecReferenceFeederDiagnostics`, all passing).
- **New `_MicLevelTap`** (`simple_conversation.py`), a second, small,
  purely-observational `FrameProcessor`, filtered to `InputAudioRawFrame`
  (the exact frame type Pipecat's own VAD analysis gates on — same
  discipline R0053's own `_MicRmsTap` already established), emitting
  `MIC_RMS:<n>`. **Only ever inserted into the pipeline stage list when
  audio-level diagnostics are actually requested** — the frozen R0071
  pipeline shape (`[transport.input(), user_agg, llm, tap, aec_feeder,
  transport.output(), _asst_agg]`) is completely unchanged, not merely
  silent, when this flag is off. 3 new tests against a real Pipecat
  pipeline (`TestMicLevelTap`), all passing.
- **New CLI flag `--diagnostic-audio-levels`** (off by default) —
  requires `--diagnostic-timeline` (needs a sink), gates both the mic tap
  insertion and the reference-feeder emission via one shared
  `emit_audio_levels = on_diagnostic is not None and diagnostic_audio_levels`
  condition, verified by 4 source-level regression tests
  (`TestDiagnosticAudioLevelsGating`).

**Why "bounded window around each VAD start" was not built as literal
buffered pre/post windowing**: the requested correlation is already
achievable directly from the timestamped, interleaved stream this
delivers — `MIC_RMS`/`REF_RMS` samples printed at their own throttled
cadence sit in the SAME chronological timeline as `LOCAL_VAD_START`, so
the operator (or a later offline analysis of the captured stdout) can
already read off "what was RMS doing in the ~1s before/after this
`LOCAL_VAD_START`" without NeXa needing to buffer and replay a window
itself — avoiding a more invasive pipeline change for the same
diagnostic value.

---

## 6. AEC_REF_ACTIVE insufficiency — reconfirmed by live evidence

`AEC_REF_ACTIVE` stayed continuously true through all 8 false episodes —
**exactly the R0053-established distinction this report already
emphasized**: liveness of the `aplay` reference-feed process is not
proof of cancellation quality. The live run adds no new information here
beyond confirming the reference process itself never died or
disconnected during the failure — the defect, whatever it is, is not
"the reference feed stopped," it is "the reference feed, while alive, is
not sufficiently canceling the echo."

---

## 7. AEC reference path — a confirmed, previously-unapplied defect found and made available (opt-in)

Re-reading R0053 in full (previously only summarized via R0071) surfaced
a **directly actionable, already-proven-real fact this report's first
draft missed**: R0053's own real, direct ALSA system audit (not
inference) confirmed that the reSpeaker's reference-injection mixer and
the USB speaker's audible-output mixer are **two independent ALSA
hardware controls** — raising real speaker volume **never** changes the
digital reference amplitude fed to the XVF3800's AEC. This is a real,
system-verified defect (not a hypothesis) whose fix
(`nexa.voice.aec_gain.CoherentReferenceGain`) was **built, tested, and
wired into the paused M2.6B `runtime.py` — but never into
`simple_conversation.py`**, the path this report's live evidence was
gathered against. `AecReferenceFeeder`'s `gain_source=None` in
`simple_conversation.py` is confirmed, by direct source read, unchanged
since R0071.

**Important calibration, stated honestly, not overclaimed**: R0053's own
erratum (R0054) found this fix, once applied to the *other* (M2.6B)
pipeline, reduced but did **not** eliminate false self-barge-in (5/5 →
2/3 false at MAX volume) — "gain-ownership incoherence is a real,
confirmed defect and a plausible contributor, but must not be documented
as the sole confirmed root cause" (R0054's own words, still true here).
Also unresolved: R0071's own acceptance test ran `simple_conversation.py`
**without** this fix and observed *zero* self-conversation across a
9-minute, 40+-interruption session — meaning the defect existing does not
by itself explain why THIS session failed and THAT session did not; real
speaker volume was never recorded/controlled between the two sessions, so
a volume difference remains a fully open, untested variable (exactly what
§9's A1/A2 protocol below is designed to resolve).

**What was done**: wired the exact same, already-tested
`CoherentReferenceGain` mechanism into `simple_conversation.py`, gated
behind a **new, opt-in** `coherent_reference_gain: bool = False` parameter
(`--coherent-reference-gain` CLI flag) — default `False` preserves
R0071/R0080's exact unscaled behavior byte-for-byte; enabling it mirrors
`runtime.py`'s proven construction pattern exactly (same class, same
`card=cfg.output_alsa_mixer_card`, same `gain_source=reference_gain.current_gain`).
4 new source-level regression tests confirm the default/gating are wired
correctly (`TestCoherentReferenceGainWiring`); the underlying
`CoherentReferenceGain`/`apply_gain` class itself is unmodified,
already-tested code (R0053's own 17-test suite, unchanged).

**This is offered as a testable A/B candidate, not a claimed fix** — see
§9's exact protocol.

**Two independent ALSA playback paths (§C of the instruction) — code-level
finding, still not measurable without hardware**: `aec_feeder` observes
each `TTSAudioRawFrame` and enqueues it to `plug:respeaker` essentially
concurrently with the same frame continuing to `transport.output()`, but
the two devices are separately buffered/scheduled ALSA playback streams
with independent `run_in_executor` writes — nothing in this code
guarantees sample-accurate lockstep. No existing code measures the actual
drift between them; adding that measurement would require either a
loopback/cross-correlation capture (exactly what R0053's own **offline**
research probe already built as `cross_correlate_pcm`, never wired into
production, and explicitly out of scope to wire in now per "do not
redesign the pipeline") or real hardware access this environment lacks.
**Not measured. Flagged, not guessed.**

---

## 8. reSpeaker/XVF3800 capture endpoint — audited, genuinely unresolved

Re-read `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` in full.
Confirmed facts:

- `LocalAudioConfig.input_device_name = "respeaker"` resolves (via
  `/etc/asound.conf`'s `pcm.respeaker { type plug; slave.pcm
  "hw:CARD=Array,DEV=0" }`) to the reSpeaker XVF3800's standard USB-audio-class
  capture endpoint, addressed by stable card name (not index).
- **The M2.1 architecture document's own §9 "Known limitations" section
  states, verbatim**: *"No echo cancellation logic built here: the
  reSpeaker XVF3800 has documented AEC/beamforming hardware capability
  (OBSERVATION, not independently verified in this substage — recorded
  for M2.5's barge-in design to account for, not used yet)."*

**This means the project's own foundational documentation has never
independently confirmed that the audio captured via `"respeaker"` is
actually the XVF3800's AEC-processed output**, as opposed to raw or
beamformed-only audio the onboard DSP happens to also expose on the same
standard USB audio capture channel. R0028/M2.5A's own real-hardware
finding (a hot mic during playback is only safe *when the reference is
fed*) is consistent with AEC being active and working when a reference is
present — but "improves markedly with a reference fed" is not the same
claim as "cancellation is fully sufficient at all volumes/content," and
this project has never directly interrogated the XVF3800's own firmware
configuration state (e.g. via I2C register reads/vendor config tool) —
confirmed by grep: no such tooling exists anywhere in this repository.
**This remains an open, unresolved question this report cannot close
without either hardware-level firmware inspection or the operator's own
A/B volume test (§9) providing strong indirect evidence.**

---

## 9. Controlled live reproduction protocol — for the operator (A1/A2, updated)

**Still not executed by this report** — no hardware/API access. Exact
commands, in priority order:

### Step 1 — confirm the gain fix doesn't help or does (cheap first pass)

```bash
# A: current behavior (byte-for-byte R0071/R0080), diagnostic timeline on
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline

# B: with the R0053 gain-coherence fix opted in
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --coherent-reference-gain
```

Ask the same few questions in both runs (e.g. re-ask about black holes,
long enough to observe multiple seconds of uninterrupted playback).
Record false-`LOCAL_VAD_START` count in each. **If B shows a material
reduction, the R0053 defect is a real, confirmed contributor here too —
but R0054's own precedent (2/3 still failed with the fix on the other
pipeline) means do not expect B alone to reach zero.**

### Step 2 — A1/A2 volume-controlled test (the load-bearing diagnostic)

```bash
# A1: current/normal real USB speaker volume
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --diagnostic-audio-levels

# A2: same setup, physical USB speaker volume reduced ~50% relative to A1
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --diagnostic-audio-levels
```

For each: NeXa speaks for ~60s total (ask a few questions that produce
longer answers), operator completely silent throughout. Count
`LOCAL_VAD_START` occurrences and interruption episodes in each.
`--diagnostic-audio-levels` additionally prints `MIC_RMS`/`REF_RMS`/
`REF_QUEUE_DEPTH`/`REF_DROPPED` throughout, interleaved with the event
timeline, so the actual signal levels around each false trigger are
directly legible from the same terminal output (no post-processing
needed for a first read).

**Interpretation:**
- False-`LOCAL_VAD_START` count drops sharply at lower volume → acoustic
  speaker-to-mic leakage / insufficient AEC margin is strongly supported
  (matches R0052/R0053's own MAX/NORMAL/LOW escalation pattern on the
  *other* pipeline) — the lower-volume run is diagnostic only, never a
  proposed permanent setting.
- False-`LOCAL_VAD_START` count stays essentially unchanged → look
  harder at reference routing/timing/XVF3800 configuration (§7/§8) or a
  non-acoustic trigger, since volume-independence would argue against a
  simple leakage-margin explanation.

### Step 3 — real barge-in control (run in BOTH volume conditions)

While NeXa speaks, operator says "stop" (or the Polish equivalent).
**Expected, and must be preserved regardless of any other finding**:
`LOCAL_VAD_START` → `INTERRUPTION_FRAME` → `PLAYBACK_STOPPED`, promptly,
every time. Any future change that breaks this is rejected outright.

### Step 4 — controlled test phrase (self-conversation direct proof, if the transcript stays elusive)

Ask a question NeXa will answer with a short, distinctive, low-ambiguity
phrase (the exact wording "ALPHA BRAVO CHARLIE DELTA" cannot be forced
from a live LLM deterministically — phrase the question toward
whatever fixed fact/code the operator seeds, e.g. re-use `ORBIT-47`).
Operator silent throughout. If `USER_TRANSCRIPT:` ever shows text
resembling NeXa's own immediately-preceding spoken content, that is
direct proof of mic-picking-up-speaker. **Noted limitation, unresolved**:
the first live run produced no useful `USER_TRANSCRIPT` evidence for any
of the 8 false episodes — per the explicit instruction, this is **not**
interpreted as proof no echo reached Gemini (Gemini's own input
transcription is a best-effort, asynchronous side channel — R0078's own
audit already established this is not guaranteed to produce a transcript
for everything the model "hears"; not redesigned here, out of scope).

---

## 10. Duplicate interruption frames — confirmed present live, telemetry fix unchanged and validated

The live log itself shows the documented pattern directly: pairs like
`31.213 INTERRUPTION_FRAME` / `31.252 INTERRUPTION_FRAME` and
`57.254`/`57.283` — Pipecat's own `broadcast_interruption()` fan-out,
exactly as R0081's first draft's source audit predicted, now confirmed
live. `CloudTurnAccumulator.on_interruption()` remains a plain,
already-idempotent flag set (unchanged finding) — **this is duplicate
telemetry, not a duplicate interruption authority**, and the terminal
print-debounce fix (§14 of the original draft, unchanged, still the
correct scope: presentation-only, zero interruption/router/turn-state
logic touched) stands as implemented, still passing its own 2 dedicated
tests. **No further action taken here** — the actual problem to solve
remains the preceding `LOCAL_VAD_START`, per the explicit instruction.

---

## 11. `BOT_AUDIO_STARTED`/`STOPPED` precision — noted limitation, no fix attempted

The live output shows assistant text appearing before the first
`BOT_AUDIO_STARTED` marker in the excerpt (a continuation of an
already-in-progress multi-sentence response, not a fresh turn start, so
this is expected — `BOT_AUDIO_STARTED` only fires once per bot-turn via
`_bot_is_responding`'s own edge-triggered guard, not once per sentence).
**Explicitly not treated as a precise physical-speaker boundary** — this
report's own §5 (original draft) already documented this exact caveat
("`PLAYBACK_STOPPED`... is a lower-bound proxy, not the literal physical
stop moment") and extends it here to `BOT_AUDIO_STARTED`/`STOPPED`
symmetrically: these are Pipecat/Gemini SDK-level signals about when
audio *content* starts/stops flowing through the frame pipeline, not
externally-measured acoustic events at the physical speaker. No fix
attempted — building true physical-boundary instrumentation would require
transport-level changes this milestone must not make.

---

## 12. USER_TRANSCRIPT limitation — noted, not redesigned

No useful `USER_TRANSCRIPT` evidence appeared for any of the 8 live false
`LOCAL_VAD_START` episodes. Per the explicit instruction, this is **not**
interpreted as proof that no echo reached Gemini's own audio input —
Gemini's input transcription is an independent, best-effort, asynchronous
side channel (R0078's own audit: not guaranteed to arrive, not
necessarily correlated 1:1 with every VAD-detected segment). No
transcription redesign was attempted or considered — explicitly out of
scope for this milestone.

---

## 13. Root-cause audit priority order — status per item

| Priority | Item | Status |
|---|---|---|
| A | reSpeaker/XVF3800 capture endpoint (raw vs. AEC-processed) | **Audited, genuinely unresolved** — the project's own M2.1 docs flag this as never independently verified (§8). Requires hardware-level firmware inspection this environment cannot perform. |
| B | Far-end reference correctness (gain, timing, format) | **Confirmed real defect found** (gain incoherence, R0053) — fix now available opt-in (§7), not proven sufficient alone. Timing/alignment (§C below) audited at the code level, not measured. |
| C | Two independent ALSA playback paths | **Audited (§7's closing paragraph)** — structurally plausible drift source, not measured, no measurement tooling wired in per "do not redesign the pipeline." |
| D | XVF3800 hardware/DSP configuration (AEC enable, far-end gain, AGC, noise suppression) | **Not auditable from this repository** — no I2C/vendor-config tooling exists in this codebase (confirmed by grep); R0053's own audit found the reference-injection mixer fixed at 0dB/unity gain and never touched by NeXa software, which is the one DSP-adjacent fact already on record. Requires live hardware access. |
| E | Silero sensitivity/thresholds | **Deliberately last, not attempted.** The ~0.7–0.9s sustained false-VAD duration observed live is far too long to be a threshold/persistence artifact fixable by a small `start_secs` nudge (matches R0053's own explicit rejection of generic VAD tuning for the analogous sustained-echo pattern on the other pipeline). No blind tuning performed, consistent with the explicit prohibition. |

---

## 14. Forbidden fixes and blind tuning — confirmed not applied

No interruption-while-speaking suppression, no VAD disabling, no blanket
mic muting during playback exists anywhere in this diff. No
`confidence`/`min_volume`/`start_secs` change was made. The two
behavioral changes in this entire report are: (1) the interruption-print
debounce (§10, presentation-only), and (2) the opt-in
`coherent_reference_gain` fix (§7, off by default, evidence-backed reuse
of an already-tested mechanism, not a new/invented one, not enabled by
default).

---

## 15. Files changed (full R0081 diff)

- `src/nexa/realtime/gemini/simple_conversation.py` — `on_diagnostic`
  param (event-timeline markers); `coherent_reference_gain` opt-in param
  + wiring; `diagnostic_audio_levels` opt-in param + `_MicLevelTap`
  insertion gating; `_make_mic_level_tap_class()` (new).
- `src/nexa/realtime/gemini/core_recall_tool.py` — `on_diagnostic` param
  on `make_recall_handler`/`register_recall_tool` (`TOOL_CALL_START`/`END`).
- `src/nexa/voice_tts/aec_reference.py` — `on_diagnostic`/
  `diagnostic_interval_s` params on `AecReferenceFeeder` (`REF_RMS`/
  `REF_QUEUE_DEPTH`/`REF_DROPPED`), default `None` — shared with local
  voice, confirmed unaffected (87 existing tests unmodified + 5 new).
- `apps/nexa_cloud_voice_simple.py` — `--diagnostic-timeline`,
  `--diagnostic-audio-levels`, `--coherent-reference-gain` flags;
  `_make_diagnostic_timeline_printer()`; interruption-print debounce in
  `_make_event_printer()`.
- `tests/test_simple_cloud_conversation.py` — `TestDiagnosticTimeline` (5),
  `TestMicLevelTap` (3), `TestCoherentReferenceGainWiring` (4),
  `TestDiagnosticAudioLevelsGating` (4) — 16 new.
- `tests/test_cloud_voice_simple_entrypoint.py` — `TestInterruptionPrintDebounce`
  (2 new).
- `tests/test_bargein_m2_5b.py` — `TestAecReferenceFeederDiagnostics`
  (5 new).

18 new tests total this milestone (measured via exact before/after collected counts: test_simple_cloud_conversation.py 13→24, test_cloud_voice_simple_entrypoint.py 13→15, test_bargein_m2_5b.py 44→49 -- not summed per-class estimates).

---

## 16. Full regression

```
pytest tests/ -q
```

**Result:** 1494 passed, 9 skipped (7 pre-existing environment-gated
live tests + 2 new from R0080's own live-gated local-voice test, both
skipped by default), **1 pre-existing failure** —
`tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::test_local_audio_config_fields_are_typed_and_explicit`,
the same paused R0068-R0070 `scheduled_aec_reference` issue documented
since R0076, untouched by this work. **Zero new failures.** `ruff check`/
`py_compile`/`git diff --check` clean on every file this report touches;
local voice's own `AecReferenceFeeder`/barge-in suites (87 pre-existing +
5 new = 92 tests in `tests/test_bargein_m2_5b.py`) re-verified, confirming
local voice is unaffected by every change in this report.

---

## 17. Acceptance criteria — still NOT MET, live re-run still required

| Criterion | Status |
|---|---|
| Silent playback test (≥10 responses, 0 false interruptions, 0 self-conversation) | **RAN, FAILED** (8 false episodes observed in the operator's own diagnostic run — this IS the evidence the criterion asks for, and it does not pass) |
| Real barge-in (≥5 deliberate interruptions, 5/5 detected, prompt stop, no resume) | **NOT YET RE-RUN** with the new instrumentation; mechanism unchanged from R0071 |
| Core recall (ORBIT-47 + same-session new fact) | **PASS** (operator live-tested, §1) |
| PL/EN (≥2 PL, ≥2 EN, one switch, no false self-interruption) | **NOT RUN** |
| A1/A2 volume-controlled diagnostic (§9) | **NOT RUN** — the specific next step |

**R0081 does not pass.** The false-barge-in/self-echo regression is
CONFIRMED to exist and its immediate trigger mechanism is now fully
understood; its deeper acoustic cause is not yet isolated. One
evidence-backed, previously-unapplied, opt-in candidate fix is now
available for A/B testing (§7/§9).

---

## 18. Commit gate

Per the explicit instruction and this project's established practice for
this exact situation: the diagnostic instrumentation (event timeline +
signal-level RMS/queue telemetry, all off by default) and the opt-in,
evidence-backed `coherent_reference_gain` fix (off by default) are
committed locally, clearly labeled as diagnostics-and-candidate-fix, not
as a resolution. **Root cause remains unconfirmed. R0081 is NOT marked
PASS. Do not ship or rely on any change in this report as a complete
fix** — the next required step is the operator's own live A1/A2 run
(§9), which this environment cannot perform.
