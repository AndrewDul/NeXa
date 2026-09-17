# R0081 — Cloud Voice False Barge-In / Self-Echo Regression

**Date:** 2026-09-17
**Type:** Diagnostic + narrow instrumentation + direct hardware audit (NOT a confirmed resolution)
**Status:** Immediate failure mechanism CONFIRMED (local VAD false-fires on NeXa's own playback). Speaker volume and the R0053 gain-coherence fix are BOTH now ruled out as sufficient fixes, by direct live A/B/A2 evidence. A NEW, more specific, hardware-firmware-level lead was found by direct XVF3800 register query: a confirmed **-20dB internal far-end reference gain** baked into the DSP firmware, never compensated for by NeXa's software, which sends the reference unscaled. Root cause still NOT proven — the next required step is a direct, deterministic (non-conversational) AEC measurement this report built but did not execute (audible playback requires the operator's live presence/consent).
**Depends on:** R0080 (accepted `2b894e7`), R0071 (frozen baseline), R0052–R0056 (prior self-echo investigation), R0053 (gain-coherence defect, now further quantified)

No Memory/ContextEngine/recall_context/Personality/Relationship/Learning/FTS/vector changes touched. No forced audio-architecture redesign. No Silero threshold tuning. No DSP register writes.

---

## 1. Recorded live results (unchanged from the prior update)

Core recall's cloud live round-trip and dynamic same-session memory remain **PASS** — the operator recalled `ORBIT-47`, then `NOVA-82` in the same Gemini session, no restart. Not re-litigated here.

---

## 2. A1 / B / A2 — the complete operator A/B evidence

### A1 — baseline, normal speaker volume

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --diagnostic-audio-levels
```

~3 false `LOCAL_VAD_START` episodes (excluding the genuine initial user utterance). `REF_QUEUE_DEPTH:0`, `REF_DROPPED:0` throughout — the reference feeder itself was healthy.

### B — `--coherent-reference-gain` enabled

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --diagnostic-audio-levels --coherent-reference-gain
```

**Result: WORSE, not better.** ~6 false episodes (vs. ~3 at baseline). `REF_RMS` dropped dramatically (expected — the fix scales the reference down toward the real, quieter, audible-device mixer gain). `REF_QUEUE_DEPTH` reached 16, `REF_DROPPED` reached 4 — **new evidence this run surfaced**: reducing the reference's digital amplitude via this mechanism correlates with the reference feeder's own queue backing up and dropping chunks, which R0053/R0054 never observed (their trials didn't report queue/drop telemetry at all — this report's own new `REF_QUEUE_DEPTH`/`REF_DROPPED` instrumentation, §5 of the prior draft, is what makes this visible for the first time).

**Conclusion, corrected from the prior draft's more hopeful framing:** `coherent_reference_gain` is **not validated as a fix for this path** and must **remain opt-in/off by default** — this report does not claim it fixes anything, and the live evidence argues against enabling it without further investigation into why it correlates with dropped reference chunks.

### A2 — baseline again, substantially lower physical USB-speaker volume

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --diagnostic-audio-levels
```

~7 false episodes at 12.645, 16.804, 23.265, 27.744, 30.706, 34.405, 37.126 (excluding the initial genuine utterance) — **not materially reduced** from A1's baseline rate, despite substantially lower physical speaker volume. `REF_DROPPED = 0`, `REF_QUEUE_DEPTH ≈ 0` throughout.

**Conclusion:** exact episode-rate comparisons across A1/B/A2 are not overinterpreted here (LLM responses were not identical, deterministic audio stimuli across runs) — but the evidence is sufficient to **reject "speaker volume alone is the primary fix"** as a supported conclusion, exactly as instructed.

### Signal-level finding common to all three runs

Quiet-period `MIC_RMS ≈ 5–10`. Around false-VAD episodes, `MIC_RMS` shows clear speech-shaped excursions (`74, 99, 82, 299, 334, 48, 96` — representative values), while `REF_RMS` is simultaneously alive and frequently in the thousands. In A2 specifically, this occurs with `REF_DROPPED = 0` and `REF_QUEUE_DEPTH ≈ 0` — **the reference feeder is healthy and the physical speaker is quieter, and speech-like residual energy still reaches the exact microphone stream local VAD analyzes.** This is the single strongest piece of evidence in this report: it moves the investigation decisively away from "is the reference feed working" (yes) and "is volume the issue" (no, or not alone) and toward the actual XVF3800 capture signal / AEC processing state / reference routing and alignment at the DSP level — which §6 below now has real, direct hardware evidence for.

---

## 3. Tool-call hypothesis — still refuted; Core recall still cleared

Unchanged from the prior draft: all false episodes observed across A1/B/A2 occurred independent of `recall_context` tool-call timing, and Core recall's own code was already proven, by direct grep, to never reference any interruption/VAD/frame symbol. **Not re-litigated.**

---

## 4. Interruption source — CORRECTED: not every InterruptionFrame is a fresh local VAD onset

The prior draft's claim — "every `InterruptionFrame` traces to Pipecat's own `LLMContextAggregatorPair`, driven by local Silero" — was **too strong**, exactly as the operator's own A2 evidence proved:

```
12.645 LOCAL_VAD_START
12.646 INTERRUPTION_FRAME
...
13.313 LOCAL_VAD_STOP

14.301 InterruptionFrame        <- no new LOCAL_VAD_START immediately before this
14.301 PLAYBACK_STOPPED
```

**Root cause of this, confirmed by direct installed-source read (Pipecat 1.8.1), not guessed:**

```
grep -rn "broadcast_interruption()" .venv/lib/python3.13/site-packages/pipecat/
```

finds **TWO** relevant call sites in this pipeline, not one:

1. `pipecat/processors/aggregators/llm_response_universal.py:1292` — the user context aggregator (`LLMContextAggregatorPair`), firing **synchronously** with a fresh local `UserStartedSpeakingFrame` while a bot response is in flight. This is the mechanism the prior draft already documented correctly.
2. **`pipecat/services/google/gemini_live/llm.py:1333`** — `GeminiLiveLLMService` **itself**. Reading the surrounding code (`_connection_task_handler`, lines 1320-1333) directly:

   ```python
   sc = message.server_content
   if sc and sc.interrupted:
       # NOTE: while the service triggers interruptions in the specific
       # case of barge-ins, it does *not* emit UserStarted/
       # StoppedSpeakingFrames, as the Gemini Live API does not give us
       # broadly reliable signals to base those off of. ...
       logger.debug("Gemini VAD: interrupted signal received")
       await self.broadcast_interruption()
   ```

   This fires when Gemini's own server sends `serverContent.interrupted=True` — a message that arrives over the network on **Gemini's own schedule**, not synchronized with any local frame. **This directly answers the instruction's own explicit question**: `GeminiVADParams(disabled=True)` only disables Gemini's *autonomous* server-side turn detection from raw audio — it does **not** disable this acknowledgement path. The most plausible real mechanism, consistent with the observed 14.301 timing (~1.6s after the original 12.645-13.313 local VAD episode ended): NeXa's own `_handle_user_started_speaking()` sent Gemini an explicit `activity_start` signal when local VAD fired at 12.645 (confirmed in the installed SDK: `await self._session.send_realtime_input(activity_start=ActivityStart())`, `llm.py:806-815`, already audited in R0078/R0079); Gemini's server processed that as a barge-in and, on its own delayed schedule, sent back `serverContent.interrupted=True` — producing a **second, independent** `InterruptionFrame` with no corresponding fresh local VAD event.

Each of the two call sites' own `broadcast_interruption()` ALSO fans out an upstream + downstream instance (`frame_processor.py:1017-1054`, confirmed: `broadcast_frame()` constructs two frame instances, sets `broadcast_sibling_id` on each pointing at the other's `id`, pushes one downstream and one upstream) — so **up to 4** total `InterruptionFrame` sightings can legitimately correspond to ONE physical interruption event. This is confirmed present in the live A2 log too (`31.213`/`31.252` and `57.254`/`57.283` pairs).

### Phase 1 fix: direction-aware, source-distinguishing telemetry — implemented

`_ConversationEventTap`'s `InterruptionFrame` handling now emits:

```
INTERRUPTION_FRAME_{UPSTREAM|DOWNSTREAM} id=<n> sibling=<n|None> vad_active=<bool> awaiting_assistant=<bool>
```

- `direction` — read directly from the `direction` parameter Pipecat already passes into `process_frame()`, no new coupling.
- `id`/`sibling` — Pipecat's own frame-debugging fields (`frame.id`, `frame.broadcast_sibling_id`), plain integers, confirmed non-content-bearing by direct construction test. Two sightings sharing a `sibling`/`id` pair are **provably** the same `broadcast_interruption()` call's fan-out; two sightings with unrelated IDs are **provably** from two independent calls (e.g. the local aggregator AND Gemini's own acknowledgement).
- `vad_active` — new local bookkeeping in the tap (`self._vad_active`, set `True` on `LOCAL_VAD_START`, `False` on `LOCAL_VAD_STOP`), read-only, never influences routing. Distinguishes "this interruption arrived while local VAD is actively mid-detection" (source 1) from "arrived with no local VAD activity in progress" (consistent with source 2, Gemini's delayed acknowledgement).
- `awaiting_assistant` — the router's own existing `has_turn_awaiting_assistant()`, read at the same instant.

Never carries conversation content. Verified against a real Pipecat `Pipeline`/`PipelineWorker`: 6 new dedicated tests (`TestInterruptionDirectionTelemetry`) proving direction capture, sibling-pair identifiability, `vad_active` in both states, `awaiting_assistant` capture, and no content leakage. One genuinely interesting side-finding surfaced while writing these tests: `InterruptionFrame` is a Pipecat `SystemFrame` (`frames.py`: `class InterruptionFrame(SystemFrame)`), which the framework gives priority/out-of-band handling over normally-queued data frames — a same-batch-queued `InterruptionFrame` can be processed *before* an already-in-flight `TranscriptionFrame`, in synthetic tests without a real wall-clock gap between frames (real production timing is never this compressed, but this is worth recording as a documented Pipecat behavior, not a NeXa bug).

**What the operator's next diagnostic-timeline run will show, that this run could not**: whether the delayed, unpaired interruptions are consistently `vad_active=False` (supporting the Gemini-acknowledgement-path theory) and whether their `sibling`/`id` values are ever shared with a temporally-distant local-VAD-triggered one (they should never be, if they are genuinely independent calls) — direct, readable confirmation instead of inference.

---

## 5. `AEC_REF_ACTIVE` insufficiency — reconfirmed, unchanged

`AEC_REF_ACTIVE` stayed true throughout A1/B/A2. Liveness of the `aplay` reference process is not proof of cancellation quality — unchanged finding, now further underlined by §6 below (a live, healthy reference feed can still coexist with a real, quantified internal gain mismatch).

---

## 6. Direct XVF3800 hardware audit — Phase 3, executed live, read-only

This environment turned out to have **direct access to the real, physical hardware** (`aplay -l`/`arecord -l` confirm `card 3: Array [reSpeaker XVF3800 4-Mic Array]` and `card 2: UACDemoV1.0` are genuinely attached) — a discovery made partway through this report, not assumed going in. Everything below was queried **read-only**: no DSP register was ever set, no `/etc/asound.conf` or persistent device configuration was touched.

### USB Audio Class descriptor topology (`lsusb -d 2886:001a -v`)

Authoritative, standards-based evidence (not vendor-specific, not inferred) directly answering the instruction's §A question:

- **Playback interface** (interface 1, EP1 OUT, 2ch/16-bit PCM): `INPUT_TERMINAL 17` (USB Streaming) → `FEATURE_UNIT 18` → **`OUTPUT_TERMINAL 19`, `wTerminalType 0x0405 "Echo-canceling speakerphone"`** — this is what `plug:respeaker` plays into; the SAME endpoint `AecReferenceFeeder` targets in production.
- **Capture interface** (interface 2, EP1 IN, 2ch/16-bit PCM): **`INPUT_TERMINAL 33`, `wTerminalType 0x0405 "Echo-canceling speakerphone"`** (`bAssocTerminal 19`, explicitly paired with the playback terminal above) → `FEATURE_UNIT 34` → `OUTPUT_TERMINAL 35` (USB Streaming) — this is what `plug:respeaker` captures from; the SAME endpoint `LocalAudioTransport`/local Silero VAD reads in production.
- `0x0405` ("Echo-Canceling Speakerphone") is a **USB-IF-standard terminal type code**, not a vendor string — its presence on BOTH the input and output sides of this one, single, paired audio function is direct, first-party evidence that this device's ONE exposed capture stream is *classified, at the protocol level, as already being the output of an echo-canceling function* — not a raw/unprocessed alternative. There is only one playback AudioStreaming interface and one capture AudioStreaming interface exposed via standard USB Audio Class on this device — no separate "raw 4-mic array" streaming interface exists to select instead. **This substantially closes M2.1's own flagged "never independently verified" gap** (`docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` §9) — with the caveat that descriptor *classification* proves *design intent*, not *runtime effectiveness* (§6's DSP register reads, below, speak to effectiveness).
- **Interface 3** is `bInterfaceClass 255 "Vendor Specific"` — this is where the actual DSP/AEC configuration protocol lives (confirmed below, via the vendor's own tool). **Interface 4** is a standard DFU (firmware update) interface — never touched. **Interface 5** is HID (buttons/LEDs) — not audio-relevant.

### ALSA mixer controls — a new, previously-undocumented fact

```
amixer -c Array scontents
```

reveals **two** independent playback controls and **two** independent capture controls on this card — R0052/R0053's own system audit only ever checked one ('PCM', assumed singular):

| Control | Value |
|---|---|
| `'PCM',0` (stereo) | 100% / **0.00dB** / on |
| `'PCM',1` (mono) | 67% / **-20.00dB** / on |
| `'Headset',0` (stereo, capture) | 77% / **-14.00dB** / on |
| `'Headset',1` (mono, capture) | 100% / **0.00dB** / on |

Which literal ALSA subdevice `plug:respeaker`'s `hw:CARD=Array,DEV=0` alias actually engages for each direction was not further resolved by mixer enumeration alone (ALSA "Simple mixer control" indices do not map 1:1 to `DEV=` numbers) — recorded as a genuinely open, minor question, not chased further given the much stronger evidence below.

### Direct DSP register reads (Seeed's own vendor tool, found and used — read-only)

`/home/devdul/Tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/python_control/xvf_host.py` — Seeed's official Python control tool for this exact device, present on this machine **outside** the NeXa repository (exactly what the instruction asked to check for). Required two small, standard PyPI dependencies (`libusb_package`, `importlib_resources`) not previously installed, plus `sudo` for the raw USB control-transfer permission — installed/used in an **unrelated, pre-existing local dev venv** (`smart-desk-ai-assistant/.venv`, already on this machine), not touching NeXa's own project environment or any system-wide package state. Every invocation below omits the tool's own `--values` flag, which is what triggers a write — confirmed from the tool's own `--help` output that omitting it performs a read.

```
$ sudo <venv>/bin/python3 xvf_host.py SHF_BYPASS
SHF_BYPASS: [0]

$ sudo <venv>/bin/python3 xvf_host.py AEC_FAR_EXTGAIN
AEC_FAR_EXTGAIN: [-20.000]

$ sudo <venv>/bin/python3 xvf_host.py AEC_AECCONVERGED
AEC_AECCONVERGED: [1]

$ sudo <venv>/bin/python3 xvf_host.py AEC_NUM_FARENDS
AEC_NUM_FARENDS: [1]

$ sudo <venv>/bin/python3 xvf_host.py AEC_NUM_MICS
AEC_NUM_MICS: [4]

$ sudo <venv>/bin/python3 xvf_host.py AEC_AECPATHCHANGE
AEC_AECPATHCHANGE: [0]

$ sudo <venv>/bin/python3 xvf_host.py AEC_RT60
AEC_RT60: [0.000]

$ sudo <venv>/bin/python3 xvf_host.py AEC_HPFONOFF
AEC_HPFONOFF: [2]

$ sudo <venv>/bin/python3 xvf_host.py AEC_AECEMPHASISONOFF
AEC_AECEMPHASISONOFF: [1]

$ sudo <venv>/bin/python3 xvf_host.py AEC_ASROUTONOFF
AEC_ASROUTONOFF: [1]

$ sudo <venv>/bin/python3 xvf_host.py AEC_PCD_COUPLINGI
AEC_PCD_COUPLINGI: [-1.000]

$ sudo <venv>/bin/python3 xvf_host.py AEC_MIC_ARRAY_TYPE
AEC_MIC_ARRAY_TYPE: [2]
```

**Interpretation, per field, against the vendor tool's own documented semantics (never overclaimed beyond what those docs state):**

| Field | Value | Meaning |
|---|---|---|
| `SHF_BYPASS` | 0 | AEC is **not** bypassed. Not the cause. |
| `AEC_AECCONVERGED` | 1 (true) | The adaptive filter reports itself **converged** — the algorithm believes it has successfully adapted to the current acoustic path. Not obviously broken. |
| `AEC_NUM_FARENDS` | 1 | Exactly one far-end reference input is configured and being modeled — the AEC is not simply ignoring the reference. |
| `AEC_NUM_MICS` | 4 | Confirms the real 4-mic array is feeding the AEC internally. |
| `AEC_AECPATHCHANGE` | 0 | No abrupt echo-path change currently detected. |
| **`AEC_FAR_EXTGAIN`** | **-20.000 (dB)** | **The single most significant finding.** "External gain in dB applied to the far-end reference signals" — a **firmware-internal** parameter, independent of and in addition to anything ALSA/software does, that attenuates whatever reference PCM is fed to `plug:respeaker` by -20dB (≈0.1× linear) *before* the AEC's own adaptive filter uses it to model the echo. Directly corroborates R0053's own prior finding (R0071: "the reSpeaker's own firmware `AEC_FAR_EXTGAIN` confirmed -20dB, never touched by any NeXa software fix") via an **independent method** (direct DSP register read, not an ALSA-mixer-level inference) — the SAME number, from a different measurement path, is strong triangulation. |
| `AEC_HPFONOFF` | 2 (on125) | 125Hz high-pass on mic input — a normal, sensible setting, not obviously implicated. |
| `AEC_AECEMPHASISONOFF` | 1 (on) | Pre/de-emphasis filtering active — normal. |
| **`AEC_ASROUTONOFF`** | **1** | "if set to 0, the AEC residuals are output, one channel per microphone, if set to 1, the ASR processed output is used, where each channel is associated with a beam from the beamformer." **Value 1 confirms the exposed capture stream is the beamformed/processed output, not raw per-mic AEC residuals** — direct, definitive confirmation for §A's question, corroborating the USB descriptor finding above from a second, independent angle. |
| `AEC_PCD_COUPLINGI` | -1.000 | Outside its documented valid range `[0.0, 1.0]`, which the tool's own docs state is how "PCD" is disabled. Recorded factually; this report does not have further vendor documentation of what "PCD" stands for or its exact functional role, and does not speculate beyond the tool's own text. |
| `AEC_MIC_ARRAY_TYPE` | 2 (squarecular) | Array geometry classification; context only, not directly diagnostic. |
| `AEC_RT60` | 0.000 | Outside the tool's own documented valid range `[0.250, 0.900]`, but not negative either (which the docs say specifically indicates an invalid estimate) — an ambiguous reading (likely "not yet estimated" / idle), not confidently interpretable from this one read alone. |

### New leading hypothesis (evidence-backed, NOT proven, NOT acted on without further measurement)

NeXa's software (`AecReferenceFeeder`, `gain_source=None` by default) feeds the far-end reference at **full, unscaled digital amplitude**. The XVF3800's own firmware **additionally** attenuates whatever it receives on that reference input by a further **-20dB** before using it internally. If the *acoustic* reality (what the physical speaker is actually emitting into the room) does not happen to sit at exactly the amplitude this firmware-side -20dB pre-scaling implicitly expects, the AEC's internal reference-to-echo model is systematically off — even while `AEC_AECCONVERGED` reports true (a filter can "converge" to a wrong, gain-mismatched model and still leave meaningful residual echo). This is a **more specific, more directly hardware-confirmed** version of R0053's own gain-coherence hypothesis — not a new theory invented here, but the SAME theory now anchored to a firmware register value read directly from the chip, rather than only inferred from ALSA mixer topology. It is consistent with, though not proven by, every piece of live evidence gathered so far: reference-feed liveness alone is insufficient (§5), the existing ALSA-level gain-coherence fix (`CoherentReferenceGain`, tuned to the AUDIBLE device's mixer, not this internal firmware parameter) did not help and may have introduced a new problem (dropped reference chunks, §2's condition B) — because it targets the wrong layer of the gain chain, and lower physical volume did not help either (§2's A2) — consistent with a *fixed, additive* firmware attenuation that does not scale proportionally with whatever software-side amplitude changes were tried.

**Not acted on**: this report does not attempt to compensate for the -20dB firmware gain in software (e.g., by pre-boosting the reference PCM by +20dB before feeding it) without first measuring, deterministically, whether doing so actually improves cancellation — that is exactly what §7's diagnostic script is for.

---

## 7. Deterministic direct AEC measurement — built, NOT executed

`docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py` (new). No Gemini, no LLM, no Pipecat pipeline, no recorded human speech — reuses `cross_correlate_pcm`/`_rms`/`_peak`/`_write_wav` from the existing R0052/R0053 probe (imported, not reimplemented) and `nexa.voice.aec_gain.apply_gain` (the same production code, not a reimplementation).

**Method:** a short, deterministic, non-speech tone sequence (500/1000/2000Hz) is played to the real USB speaker while simultaneously capturing from `plug:respeaker` (the exact endpoint production VAD analyzes). In the "reference ON" condition, the identical PCM is *also* fed to `plug:respeaker`'s playback direction at the same time — the same duplex pattern (`AecReferenceFeeder` writes to `plug:respeaker` for playback while `LocalAudioTransport` reads from `plug:respeaker` for capture) production already relies on. Capture starts before playback (a fixed pre-roll) so the two PCM windows can be aligned to a shared t=0 by simple slicing — deliberately avoiding R0053/R0055's own documented `cross_correlate_pcm` calling-convention pitfall (their probe's reference and mic windows started at different offsets, saturating the lag search).

```bash
# baseline: reference OFF (upper bound on leakage, no cancellation possible at all)
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition off

# baseline: reference ON, unscaled -- gain=1.0 is EXACTLY what simple_conversation.py
# feeds today (gain_source=None); test this FIRST, before any gain experiment
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition on

# reference ON, +20dB pre-boost -- tests whether compensating for the firmware's
# own -20dB AEC_FAR_EXTGAIN (§6) in software measurably improves cancellation,
# in isolation, without a live nondeterministic Gemini conversation
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition on --gain 10.0
```

(`--gain` is linear; +20dB ≈ ×10.0 — the script's own `apply_gain` is the same production function, so this is a real test of the exact compensating factor §6's finding suggests, not an arbitrary number.)

Reports: quiet-room RMS floor, fed-signal RMS, captured RMS during playback, an ERLE-like attenuation figure in dB (explicitly labeled as such, not a certified AEC measurement), and the reference↔capture cross-correlation peak/lag. Saves WAV pairs for offline inspection.

**Why this was not executed by this report**: it produces real, audible sound through the operator's physical speaker. Unlike the silent USB register reads in §6, this is a perceptible, real-world side effect in the operator's own space — this report treats that as requiring the operator's live presence/awareness, consistent with the explicit instruction to hand off exact commands rather than run a live audio test unilaterally. **Recommended sequence for the operator**: `--condition off`, then `--condition on`, back to back, same physical volume/room/mic position as A1; repeat 3× per condition if practical (single-sample acoustic measurements vary). Optionally follow with `--condition on --gain 10.0` to test §6's compensation hypothesis directly, deterministically, before ever considering a software change.

---

## 8. Duplicate interruption frames — unchanged conclusion, now precisely explained

§4 above supersedes the prior draft's account of *why* duplicates occur (two independent call sites, not just one call's fan-out) but the **conclusion is unchanged**: `CloudTurnAccumulator.on_interruption()` is a plain, already-idempotent flag set (`nexa/realtime/turn.py:119-123`) — safe regardless of how many times or from which source it fires. The terminal print-debounce (presentation-only, zero interruption/router/turn-state logic touched) remains implemented and tested.

---

## 9. Root-cause audit priority order — updated status

| Priority | Item | Status |
|---|---|---|
| A | reSpeaker/XVF3800 capture endpoint (raw vs. AEC-processed) | **RESOLVED** by direct evidence (§6): USB descriptor terminal typing (`0x0405` Echo-Canceling Speakerphone, both directions) + `AEC_ASROUTONOFF=1` (beamformed/processed output, confirmed by direct DSP register read) both independently confirm the capture stream IS the processed output, not raw per-mic residuals. |
| B | Far-end reference correctness (gain, timing, format) | **New, more specific evidence**: firmware `AEC_FAR_EXTGAIN=-20dB`, confirmed by direct register read, corroborating R0053's ALSA-level finding via an independent method. This is now the leading hypothesis (§6). Not yet measured in isolation (§7, built, not executed). |
| C | Two independent ALSA playback paths (timing drift) | Still not measured; deprioritized relative to B given B's much more direct, quantified evidence. |
| D | XVF3800 hardware/DSP configuration | **Largely resolved** by §6's direct register reads: AEC not bypassed, converged, 1 far-end/4 mics configured, HPF/emphasis normal, PCD disabled (reason unclear), RT60 inconclusive from a single idle read. |
| E | Silero sensitivity/thresholds | Still deliberately last, still not attempted — the ~0.7-0.9s sustained false-VAD duration remains far too long to be a small-threshold artifact, and the new firmware-level evidence gives a much more specific, better-targeted lead than blind VAD tuning ever would. |

---

## 10. Forbidden fixes, blind tuning, and firmware writes — confirmed not applied

No interruption-while-speaking suppression, no VAD disabling, no blanket mic muting, no `confidence`/`min_volume`/`start_secs` change. **No DSP register was written** — every `xvf_host.py` invocation in §6 omitted `--values`, confirmed read-only by the tool's own `--help` text and by the fact that a write requires that flag. `/etc/asound.conf` and all persistent device configuration are untouched.

---

## 11. Files changed

- `src/nexa/realtime/gemini/simple_conversation.py` — direction/sibling/vad_active-aware `InterruptionFrame` diagnostics (§4); `_vad_active` bookkeeping.
- `tests/test_simple_cloud_conversation.py` — `TestInterruptionDirectionTelemetry` (6 new); corrected the one pre-existing assertion that expected a bare `"INTERRUPTION_FRAME"` label; fixed one test's reliance on same-batch queue ordering (the `SystemFrame`-priority finding, §4) with explicit sequential awaits.
- `docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py` (new) — the deterministic, non-conversational AEC measurement script (§7), built, not executed.

Everything from the prior update (`--diagnostic-timeline`, `--diagnostic-audio-levels`, `--coherent-reference-gain`, the interruption-print debounce, the `AecReferenceFeeder`/`_MicLevelTap` signal-level diagnostics) is unchanged in this update except for the correction above.

---

## 12. Full regression

```
pytest tests/ -q
```

`tests/test_simple_cloud_conversation.py`: 36 passed (30 from the prior update + 6 new `TestInterruptionDirectionTelemetry`).

**Full suite result:** 1500 passed, 9 skipped (7 pre-existing environment-gated live tests + 2 from R0080's own live-gated local-voice test), **1 pre-existing failure** — `tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::test_local_audio_config_fields_are_typed_and_explicit`, the same paused R0068-R0070 `scheduled_aec_reference` issue documented since R0076, untouched by this work. **Zero new failures.** `ruff check`/`py_compile`/`git diff --check` clean on every file this report touches.

---

## 13. Acceptance criteria — still NOT MET

| Criterion | Status |
|---|---|
| Silent playback test (0 false interruptions) | **FAILED** at normal volume (A1), FAILED with the gain fix (B, worse), FAILED at lower volume (A2) — three independent live attempts, none passing |
| Real barge-in (5/5) | Not separately re-verified this round; mechanism itself unchanged |
| Core recall | **PASS** (unchanged, §1) |
| PL/EN | Not run |
| Direct, deterministic AEC measurement (§7) | **NOT RUN** — built, requires the operator's live presence for audible playback |

**R0081 still does not pass.** A specific, hardware-confirmed, quantified candidate mechanism (§6's firmware `AEC_FAR_EXTGAIN=-20dB`) is now available for direct, deterministic testing (§7) before any software change is considered.

---

## 14. Next operator step — exactly what to run and return

**Do not start with another free-form conversation test.** First, the deterministic measurement:

```bash
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition off
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition on
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --condition on --gain 10.0
```

Same physical volume/room/mic position as the A1 run, back to back. **Return**: the full printed `RESULT` block from each of the three runs (quiet_before_rms, signal_rms, mic_window_rms, mic_window_peak, attenuation, cross-correlation lag/correlation) — no audio files need to be sent, the printed numbers are sufficient for the next analysis pass.

Separately, if convenient: a repeat of the `--diagnostic-timeline` conversational run (A1-style, no `--coherent-reference-gain`) so the new direction/sibling/`vad_active`-aware `INTERRUPTION_FRAME_*` labels (§4) can be read directly, to confirm or refute the "delayed Gemini-acknowledgement" theory for the unpaired interruptions.

---

## 15. Commit gate

Root cause is not confirmed. `coherent_reference_gain` is confirmed NOT validated (and stays off by default). No fix is claimed. Per this project's established practice for this exact situation: the diagnostic instrumentation (direction-aware interruption telemetry, tested against a real Pipecat pipeline) and the new deterministic AEC measurement script (built, not yet run) are committed locally, clearly labeled as diagnostics only. **R0081 is NOT marked PASS. No DSP/firmware/ALSA configuration was changed on the live system** — every hardware interaction in this report was a read.
