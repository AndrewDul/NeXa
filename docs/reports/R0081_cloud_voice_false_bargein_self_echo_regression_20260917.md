# R0081 — Cloud Voice False Barge-In / Self-Echo Regression

**Date:** 2026-09-17
**Type:** Diagnostic + narrow instrumentation + direct hardware audit (NOT a confirmed resolution)
**Status (updated 2026-09-18):** **MAJOR RESULT (§15.9, real hardware): the regression-isolation gate ran — the dependency-isolated historical golden path (`7dd6b87`) ALSO shows false self-interruption today, alongside current. CASE 2** — a later NeXa Core/Memory/Recall software regression is NOT the primary explanation. The golden log's `[Tu es beau.]` false transcription during silence is strong direct evidence of speech-like acoustic leakage (not, alone, proof of provenance). Mixer/device snapshots were identical before both runs. Different Gemini models on each path cannot be the SOLE explanation (both fail).

**Reference-queue overflow (§16), confirmed and now fully correlated against the already-captured current-run log (§16.5):** one episode, 144 dropped chunks (`AecReferenceFeeder`, `asyncio.Queue(maxsize=24)`); the speaker path continues independently of reference-enqueue success (NOT proven to play perfectly — R0071's own known playback stutter is a plausible connection). Queue growth precedes the first false VAD in that answer by 10.6s, the first drop by 9.5s (refutes "the first interruption caused the initial overflow" for that episode — does NOT refute overflow as a later contributing factor, does not prove overflow caused the VAD). Only 2 of 9 proxy-classified false events (co-occurring `BOT_AUDIO_STOPPED`, not transcript-verified ground truth) are temporally associated with the single, non-recurring overflow episode — the other 7 occurred with a healthy, zero-drop queue, confirming baseline residual echo alone is sufficient for the symptom; overflow is not necessary for it. Golden has the identical queue architecture (byte-for-byte since `7dd6b87`) — a strong shared-mechanism candidate, not measured to have actually overflowed during golden's own session.

**ALSA capture query result (§16.7, real hardware, 2026-09-18): the active-period explanation for §7d's exact 512-sample/32.000ms MLS lag split is REFUTED** — actual negotiated `period_size=2000`/`buffer_size=8000` frames, neither divides evenly by 512; a source scope-check of the diagnostic script found no application-level 512-sample block either. The MLS bimodality itself remains a real, reproduced observation — its source is now UNRESOLVED, not invalidated. A new, unrelated ALSA fact: `plug:respeaker`'s mono capture is a 50/50 route mix of the device's 2 (ASR-beamformed) hardware channels — a plausible contributor to the already-documented spectral-reshaping finding (§7d), not claimed to cause the lag split.

**Counterbalanced gain confirmation RESULT (§16.9, real hardware, 2026-09-18): `gain=0.5` does NOT reproduce — REJECTED/de-prioritized.** `gain1.0` beat `gain0.5` 3/3 in the A-first sequence and lost only 1/3 in the B-first sequence; combined, `gain0.5` is ~6.36%/~0.54dB WORSE than `gain1.0`. **Why the original sweep showed an apparent ~30% advantage is UNRESOLVED** — order/settle confounding in that sweep's own ascending design and the newly-observed time/state dependence (below) are both plausible contributors; this report does not assign the cause to either without further evidence, and does not retroactively claim the sweep result is "explained." Production `gain=1.0` stays unchanged; no Gemini/conversational gain=0.5 test is warranted from this evidence. **A far larger, unanticipated finding**: residual dropped ~35-41% between the run's first half (sequence 1) and second half (sequence 2) — 30.10→19.40 (gain0.5), 29.23→17.30 (gain1.0) — while quiet floor and acoustic stimulus stayed essentially fixed. An offline lifecycle audit (§16.10) found the Python-level control flow is structurally uniform block to block (fresh subprocess recreation every time, no explicit AEC reset anywhere) — this does **not** identify which layer is responsible; host/ALSA/USB scheduling, driver/stream-open-close state, XVF3800 DSP state, and physical/acoustic state are all still-open candidates, not ranked against each other. A new, cleaner script mode, `--track-continuous` (§16.12), was built — a true continuous-stream test with no stream recreation or gap anywhere inside the measured window — ahead of the restart-based `--track` mode built previously (§16.11 explains why it is now secondary). **Its first real-hardware run (§16.14) confirms a large (~4.64×/~13.3dB) in-stream, non-monotonic residual oscillation with zero stream recreation** — a second-stage silence-interval test (§16.15) is now the recommended next hardware step (§16.16).

**First `--track-continuous` real-hardware run (§16.14, run id `20260918T074722Z`): a large, non-monotonic, IN-STREAM residual state change is CONFIRMED with zero stream recreation.** 10 byte-identical 3s cycles, one uninterrupted capture/speaker/reference stream, `gain=1.0` fixed: RMS went 39.0→38.5→25.8→11.1→8.4 (a strong early improvement) then 24.7→15.4→24.5→10.9→13.3 (degrades and recovers repeatedly, no stream event of any kind). First-half mean 24.56 → second-half mean 17.76 (−27.7%/≈−2.8dB); max/min span ≈4.64×/≈13.3dB within the SAME stream. This directly refutes "stream reopen/recreation is required for large residual variation" and rules out a simple monotonic-warm-up model — intermittent timing/state/DSP/AEC instability is now STRONGLY SUPPORTED as a class, though the exact responsible layer (XVF3800 DSP, ALSA/USB scheduling, driver state, host scheduling, acoustic state) remains unassigned. `quiet_before_rms=15.3` this run is materially higher than the ~7 RMS floor during §16.9's confirmation — a caveat against cross-run absolute-RMS comparison, not an explanation for the in-run oscillation (all 10 cycles share one run's conditions). This gives the operator's own "some answers perfect, others broken" observation a deterministic, non-speech hardware analogue — not yet claimed to prove the exact live conversational mechanism (no speech, no Silero/VAD in this test). A second-stage run after 30s of defined silence (§16.15-16.16) is now justified and is the next step.

Working model: baseline residual echo (always present, §7/§7b) + an intermittent reference-queue/scheduling failure (confirmed once today, §16) + a confirmed, large, non-monotonic in-stream residual-stability effect (§16.14) + possibly AEC adaptation/recovery effects, combining to varying degrees per utterance. AEC/state stability is now a HIGHER-priority open question than gain calibration. Root cause still NOT proven.
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

## 7. Deterministic direct AEC measurement — EXECUTED live, real results in

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

### Real results (operator-executed, same physical volume/room/mic position, back to back)

| Metric | OFF | ON, gain=1.0 | ON, gain=10.0 |
|---|---|---|---|
| `quiet_before_rms` | 7.3 | 7.6 | 7.9 |
| `signal_rms` (fed to speaker) | 11428.8 | 11428.8 | 11428.8 |
| `mic_window_rms` (captured) | **771.6** | **308.3** | 300.9 |
| `mic_window_peak` | 2123 | 1179 | 972 |
| attenuation (ERLE-like) | -23.41 dB | -31.38 dB | -31.59 dB |
| `best_lag_ms` | 108.5 | 173.38 | 141.44 |
| `normalized_correlation` | 0.4353 | 0.5302 | 0.4811 |

**Conclusion 1 — AEC is definitely functioning (CONFIRMED):** OFF→ON is a **~2.50× reduction in captured RMS** (771.6 → 308.3) and **~7.97dB of additional attenuation** (-23.41 → -31.38dB). This directly confirms the far-end reference reaches the XVF3800 and the AEC measurably suppresses leakage — AEC is not doing nothing. This materially narrows the problem: it is no longer "maybe AEC isn't working," it demonstrably is. The open question is why a substantial residual (~RMS 300) remains after cancellation.

**Conclusion 2 — gain×10 did NOT show a material further improvement, but see §7a: this comparison is likely invalid, not just small.** ON gain=1.0 → ON gain=10.0 moved `mic_window_rms` only 308.3 → 300.9 (~2.4% reduction, ~0.21dB) — nowhere near what a valid +20dB linear reference boost compensating a firmware -20dB attenuation would be expected to show if that hypothesis were both correct and being tested cleanly. **This result must NOT be used to clear or confirm the `AEC_FAR_EXTGAIN` hypothesis either way** until §7a's clipping concern is resolved.

**Finding 3 — timing (`best_lag_ms`) remains a serious but not yet resolved candidate**, and must NOT be over-interpreted: OFF=108.50ms, ON=173.38ms, ON×10=141.44ms are three different values, but `cross_correlate_pcm` always correlates the **original, un-gained source tone** (`signal_pcm`, what's fed to the physical speaker) against the **captured, post-processing residual** (`mic_window`) — never against the actual post-gain reference bytes sent to `plug:respeaker` (see the diagnostic script's own updated module docstring, added this turn, for the full explanation). For the OFF condition this is a reasonably direct measure of the acoustic-only path delay (speaker → air → mic, no cancellation). For the ON conditions, the *residual* being correlated has already been reshaped by whatever cancellation occurred — a shifted lag reflects the correlation search locking onto a different, attenuated, reshaped waveform, **not a literal change in AEC processing delay**. This script also cannot separately measure (2) reference-feed-write → XVF3800 internal processing/capture alignment, or (3) the far-end-reference vs. real-acoustic-echo relative timing error the AEC's own adaptive filter sees internally — the USB descriptor audit (§6) confirms this device exposes exactly ONE capture stream, already ASR/beamformed (`AEC_ASROUTONOFF=1`); there is no standard-ALSA way to capture a "before AEC" and "after AEC" pair simultaneously from this device as currently configured. Only the net residual is observable this way. **Reference/playback relative timing remains UNRESOLVED**, not ruled in or out.

**Connection to the live false-VAD data (§2):** live quiet-period `MIC_RMS ≈ 5–10` is consistent with this deterministic test's own quiet floor (`quiet_before_rms ≈ 7.3–7.9`). The deterministic AEC-ON residual (`mic_window_rms ≈ 300–308`) is of a magnitude **consistent with** (not numerically equivalent to — a synthetic 3-tone sequence and real speech are different signals) the residual speech-shaped `MIC_RMS` excursions observed around false-VAD episodes in §2 (`74, 99, 82, 299, 334, 48, 96`). This is supportive circumstantial evidence, not proof that this exact residual is what trips Silero — but it is no longer an unexplained coincidence of magnitude either.

---

## 7a. Clipping audit of the `--gain 10.0` result — the gain=10 comparison is INVALID as run

**Instruction that triggered this audit, and why it matters:** on an S16_LE PCM path, `apply_gain()` (`nexa.voice.aec_gain.apply_gain`, reused unmodified by this script) uses `audioop.mul(pcm, 2, gain)`. A direct Python test in this session confirms this **saturates at the int16 boundary, it does not wrap**:

```python
>>> import audioop, struct
>>> audioop.mul(struct.pack('<4h', 30000, -30000, 5000, -5000), 2, 10.0)
(32767, -32768, 32767, -32768)
```

The diagnostic script's `build_test_signal()` generated its tone sequence at a fixed peak amplitude of **0.5** (i.e. peak ≈ 0.5 × 32767 ≈ **16383**) in every condition run so far, including the `--gain 10.0` trial. A ×10 gain on a signal peaking at ~16383 would need to represent values up to **~163,835** — roughly **5×** past the int16 ceiling of 32767. Any sample whose original magnitude exceeds `32767/10 ≈ 3277` (i.e. `|sin| > 0.2`, true for roughly **~87% of a sine cycle's duration** away from its zero-crossings) saturates to exactly ±32767/±32768 after gain. **The gain=10.0 reference signal actually sent to `plug:respeaker` was, in all likelihood, heavily and continuously clipped — not a clean +20dB boost, but something close to a square wave.**

**Consequence:** the "gain×10 did not materially improve cancellation" result (Conclusion 2 above) is **not valid evidence about whether compensating `AEC_FAR_EXTGAIN=-20dB` in software would help**. A heavily clipped reference signal is harmonically distorted (introduces energy at frequencies the AEC's adaptive filter was never modeling) and is not a faithful +20dB-scaled copy of the original tone sequence — the AEC has no reason to cancel it well, clipped or not, so this result cannot confirm OR refute the firmware-gain hypothesis. **This gain=10.0 run is marked INVALID for that purpose.**

**Fix, implemented this turn, not yet re-run:** `r0081_direct_aec_diagnostic.py` gained:
- `build_test_signal(..., amplitude: float = 0.5)` — the peak amplitude is now a parameter instead of a hardcoded constant, so it can be lowered to leave headroom for a given `--gain`.
- `clip_stats(pcm) -> dict` — counts int16 samples landing exactly on the ±32767/±32768 saturation boundary (the exact signature `audioop.mul` produces on this signal; a genuine unclipped sine essentially never lands on that precise integer by chance) and reports `n_clipped`/`pct_clipped`.
- `run_trial()` now separately tracks and returns `speaker_pcm` (never gain-adjusted — the real acoustic stimulus stays IDENTICAL across `--gain` values for a fixed `--amplitude`, which is what makes an off/on/on+gain comparison meaningful at all), `reference_pre_gain_rms/peak` (= the speaker signal, what `apply_gain()` receives), `reference_post_gain_rms/peak` (what is ACTUALLY sent to `plug:respeaker`), and `reference_clipped_samples`/`reference_clipped_percent`.
- `main()` now prints all of the above for every run (not just gain≠1 ones), and prints an explicit `*** WARNING: post-gain reference is CLIPPED ***` line whenever `reference_clipped_percent > 0`, plus a `--repeats N` option that runs the trial N times and reports mean/min/max across the set (`_mean_min_max`) — implementing the operator's requested OFF/ON repeatability protocol in the script itself rather than by hand.

**No firmware write was made or is authorized.** `AEC_FAR_EXTGAIN=-20dB` remains a hardware-confirmed FACT (§6). The corrected, non-clipped retest requested here has now been run by the operator — see §7b.

---

## 7b. Corrected, non-clipped, apples-to-apples gain comparison — ×10 compensation REFUTED

The operator re-ran the diagnostic at a single fixed, low `--amplitude 0.05` for all three relevant conditions, so the acoustic stimulus (what the physical speaker actually emits) is **identical** across them — `speaker_pcm_rms ≈ 1142.5`, `speaker_pcm_peak ≈ 1638` in every trial. 3-trial repeats for each.

| Metric | OFF | ON, gain=1.0 | ON, gain=10.0 |
|---|---|---|---|
| `mic_window_rms` mean | **40.80** (min 40.50, max 41.30) | **26.17** (min 24.10, max 27.90) | **116.23** (min 95.20, max 144.80) |
| attenuation mean | -28.94 dB | -32.82 dB | -39.99 dB (relative to the much larger gain=10 reference — see note below) |
| `reference_post_gain_rms` / peak | n/a | 1142.5 / 1638 | 11424.8 / 16380 |
| `reference_clipped_percent` | n/a | 0.00% | **0.00%** — confirmed genuinely non-clipped this time |
| quiet floor | ~6.8-6.9 RMS | (same) | (same) |

**Conclusion 1 — AEC still confirmed functioning at this (much lower) signal level:** OFF→ON gain=1.0 is `40.80 → 26.17` RMS, **~1.56× reduction, ~3.86dB additional attenuation**. Smaller in absolute terms than the earlier high-amplitude measurement (§7: ~2.50×/~7.97dB), but real and reproducible (tight 3-trial spread: 24.10-27.90).

**Conclusion 2 — the naive ×10 (+20dB) software compensation hypothesis is REFUTED, not merely "not proven":** with clipping now genuinely ruled out (`reference_clipped_percent = 0.00%` for every trial), gain×10 makes the captured residual **~4.44× LARGER** than gain=1.0 (26.17 → 116.23 RMS, ~12.95dB worse) — and even **~2.85× larger than reference OFF** (40.80 → 116.23 RMS, ~9.09dB worse than not feeding a reference at all). Feeding a ×10-boosted reference is actively counterproductive on this path. **NeXa must NOT implement a blanket +20dB software compensation for `AEC_FAR_EXTGAIN`.** `AEC_FAR_EXTGAIN=-20dB` remains a hardware-confirmed fact (§6), but this report does **not** claim that value is proven optimal either — only that the specific "invert it with a ×10 software multiplier" fix is refuted. The -20dB firmware value may be an intentional part of the XVF3800's own internal reference calibration rather than a missing gain NeXa's software needs to invert.

**Important second finding — AEC's measured effect is level-dependent, and this is not itself evidence of a defect:**

| | OFF mean RMS | ON gain=1.0 mean RMS | Reduction |
|---|---|---|---|
| amplitude=0.5 (earlier repeatability run) | 1461.87 | 213.90 | ~6.84× / ~16.7dB |
| amplitude=0.05 (this run) | 40.80 | 26.17 | ~1.56× / ~3.86dB |

At low amplitude the captured residual (mean 26.17) approaches the ~7 RMS room/system noise floor, so SNR/floor effects plausibly compress the apparent dB reduction — this is **not** interpreted here as the AEC becoming less effective, but the level dependency itself is worth documenting: **normal live Gemini speech produces false-VAD-adjacent `MIC_RMS` excursions ranging from the tens into the hundreds (§2), and the high-amplitude deterministic AEC residual is ALSO in the hundreds** — i.e. real playback segments plausibly sit at signal levels where the residual is large enough, in absolute terms, to cross local Silero's decision boundary, even though the *relative* (dB) cancellation at those levels is actually the AEC's best-measured performance so far. This continues to support the same picture as §7's original finding: **AEC works, but residual speech-shaped energy remains large enough in some real playback segments to trigger local Silero.**

**Gain-near-baseline sweep — built last update, now RUN live.** Before abandoning gain mismatch as a contributor entirely, the operator's instruction was to test whether the true optimum sits modestly around gain=1.0 rather than only checking the extremes (1.0 vs 10.0). `r0081_direct_aec_diagnostic.py` gained a `--sweep` mode: fixed points `off, 0.25×, 0.5×, 1.0×, 2.0×, 4.0×` (ascending, documented order — `gain=10.0` deliberately excluded, already shown far too strong), same fixed `--amplitude` (defaults to 0.05, safe across this whole range with zero clipping), 3 repeats per point by default, each point preceded by a `--sweep-settle` (default 1.0s) unmeasured warmup at its own gain so the AEC has time to reconverge before being measured — mitigating order/carryover effects between points run back to back in one process. A fixed ascending order was used rather than randomizing it, because the settle step (not trial order) is what actually re-establishes steady AEC state before each measured point; this rationale is documented directly in the script (`SWEEP_GAIN_POINTS`'s own comment). One invocation (`--sweep`) runs and reports the whole thing, including a final summary table naming the point with the lowest mean residual RMS. **Real results and the settle/order confound they surfaced: §7c.**

**Cross-correlation / timing stimulus fixed this update, now RUN live.** The operator flagged that `best_lag_ms` has jumped to values near the ±300ms search boundary (e.g. ≈-299ms) across trials, and that the periodic 500/1000/2000Hz tone stimulus has many equally-valid correlation peaks spaced by its own period — a bounded lag search can lock onto the wrong one, so `best_lag_ms` should **not** be used as strong timing evidence with that stimulus. This is now independently confirmed, not just theorized: an in-repo check computed the tone stimulus's own autocorrelation over a 0-50ms lag range and found **112 separate lags with correlation > 0.5** (i.e. many strong, ambiguous peaks) versus **0** for a newly added MLS (maximum-length-sequence) pseudonoise stimulus over the same range — a stark, direct demonstration of exactly the ambiguity the operator raised. `r0081_direct_aec_diagnostic.py` gained `--stimulus {tones,mls}` (default `tones`, unchanged behavior for existing commands): `mls` generates a deterministic 16-bit maximal-length Fibonacci LFSR chip sequence (period 65535 chips ≈4.09s at 16kHz; a documented, checkable, independently-verifiable tap set, not picked ad hoc), with a sharp, unambiguous single correlation peak. Per the operator's own instruction, this was offered as the way to **first establish a stable acoustic speaker→capture lag in reference-OFF mode across repeated trials**. **Real results and the offline follow-up audit: §7d.**

---

## 7c. Gain sweep — real results: gain=0.5 candidate found, but a settle/order confound is open

The operator ran `--sweep` live (fixed conditions: amplitude=0.05, 3 repeats/point, 1.0s settle/point, same physical geometry throughout, **zero clipping at every point**):

| Point | mic_window_rms mean |
|---|---|
| off | 38.43 |
| gain=0.25 | 25.87 |
| **gain=0.5** | **22.23 (lowest)** |
| gain=1.0 | 31.70 |
| gain=2.0 | 37.73 |
| gain=4.0 | 68.97 |

**Positive finding:** `gain=0.5` vs `gain=1.0` is `22.23` vs `31.70` RMS — **~30% lower residual, ~3.1dB lower**. This is now material, first-pass evidence that reference amplitude calibration near baseline (not just the already-refuted extreme ×10) may be a real contributor. **Not yet a production change** — see the confound below and §14.

**Important possible confound — first-trial quiet-floor anomaly.** The sweep's own per-trial `quiet_before_rms` (room noise floor, measured during the pre-roll BEFORE that trial's own playback) shows a suspicious pattern after several gain transitions:

| Point | trial1 quiet | trial2 quiet | trial3 quiet |
|---|---|---|---|
| gain=1.0 | 36.6 | 15.9 | 15.9 |
| gain=2.0 | 26.8 | 16.2 | 16.4 |
| gain=4.0 | 28.2 | 15.8 | 15.7 |

Trial1 (immediately after that point's own settle step, at a NEWLY changed gain) is consistently elevated versus trials 2/3 at the SAME gain. This pattern was reported by the operator for the three points that follow an actual gain CHANGE from the immediately preceding point (1.0, 2.0, 4.0); it is not yet established whether off/0.25/0.5 show the same pattern (not reported).

**This report does not guess which mechanism is responsible**, per explicit instruction. Candidate, non-exclusive possibilities, none confirmed:
- the 1.0s `--sweep-settle` is insufficient for full AEC reconvergence after a gain change;
- the first measured trial still contains transient AEC/DSP adaptation state;
- acoustic reverberation/decay tail from the settle playback (or the transition into it) has not fully died out within the fixed 1.0s pre-roll;
- another deterministic first-trial transition effect not yet identified.

**A second, separate piece of evidence that the exact numerical optimum is not yet stable**: an earlier, independent amplitude=0.05/gain=1.0 repeat set (§7b) gave `mic_window_rms` mean **26.17**, while `gain=1.0`'s own point *inside this ascending sweep* gave **31.70** — a ~21% difference for nominally the identical condition, run at a different point in a different process invocation. **The exact numerical optimum is not yet stable enough to change production**, even setting the quiet-floor anomaly aside.

**Fix, built this update, not yet run: `--confirm`.** A new mode was added specifically to remove ascending-order ambiguity. **Important correction, made within this same update before any live run**: the first implementation ran only ONE alternating sequence (`A,B,A,B,...`) — this does NOT fully counterbalance order, since every `B` is always preceded by `A` and every `A` (after the first) is always preceded by `B`; there is no run in which `B` occupies the "goes first" role. A claim that a result "holds regardless of order" from that design alone would have been too strong. **Corrected before any operator run**: `--confirm` now runs BOTH an A-first (`ABABAB`) AND a B-first (`BABABA`) sequence in one invocation (default `A=gain0.5`, `B=gain1.0`, 3 cycles per sequence = 6 measured trials per gain total, one trial per block — no silent trial-discarding), each block preceded by its own settle (default raised to **3.0s**, up from `--sweep`'s 1.0s, an evidence-based increase given the quiet-floor anomaly above) and an optional additional `--post-settle-gap` (silent pause, no playback, default 0.0/off) before that block's own measured trial. `quiet_before_rms` is now also included in every summary block (previously only shown per-trial), and the final `CONFIRM SUMMARY` reports each sequence's own results separately, the combined pool, paired per-cycle differences, and how many cycles each gain "won" **in each sequence individually** — the script's own verdict text only claims an order-independent effect if `gain=0.5` wins in (nearly) every cycle in BOTH sequences, and explicitly reports a "mixed result" (not order-independence) otherwise. **Not yet run live** — see §14 for the exact command.

**Decision criteria (unchanged from the operator's own instruction, restated for this update's record):** if `--confirm` shows `gain=0.5` reproducibly beating `gain=1.0` regardless of cycle order by a material margin, mark reference gain calibration a CONFIRMED CONTRIBUTOR and `gain=0.5` a LIVE CANDIDATE (still not a production default — would need a conversational opt-in test first). If the difference collapses under balanced order/settle control, the sweep was confounded and timing/residual characteristics remain the primary open question.

---

## 7d. MLS timing — real results: tone ambiguity confirmed fixed, but the OFF-condition lag is bimodal

The operator ran `--condition off --stimulus mls --repeats 3` live:

| Trial | lag | correlation | mic RMS |
|---|---|---|---|
| 1 | 134.50ms | 0.0726 | 432.1 |
| 2 | 134.31ms | 0.0714 | 430.8 |
| 3 | 102.50ms | 0.0638 | 431.7 |

**Positive result:** trials 1 and 2 agree to within ~0.19ms — dramatically more coherent than any periodic-tone lag measurement produced so far (which jumped as far as the ±300ms search boundary). This is real, direct confirmation that the MLS stimulus fixes the ambiguity problem it was built to fix.

**But trial 3 differs by ~32ms, and this report does not average 102.5 and 134.4ms into a single claimed physical delay.** Per the operator's own instruction, an offline audit of the three ALREADY-CAPTURED WAV files was performed this update — **no new hardware access, no new live capture** — reusing the exact same `signal.wav`/`mic.wav` pair (and the exact same `cross_correlate_pcm` call) each trial's own reported `best_lag_ms`/`normalized_correlation` came from, to first confirm the offline recomputation exactly reproduces the live numbers (it does, to the reported precision) before extracting a full correlation curve instead of just the single best lag.

**Finding 1 — this is a genuine bimodal switch, not noise/jitter around one true peak.** The correlation value AT the "other" trial's peak lag is essentially at the noise floor in every case, not merely a weaker secondary peak:

| Trial | corr @ ~102.5ms | corr @ ~134.4ms | trial's own best |
|---|---|---|---|
| 1 | 0.0030 (noise) | 0.0351 | 134.50ms / 0.0726 |
| 2 | -0.0011 (noise) | 0.0220 | 134.31ms / 0.0714 |
| 3 | 0.0638 (= its own peak) | -0.0062 (noise) | 102.50ms / 0.0638 |

**Finding 2 — the separation between the two lags is EXACTLY 512 samples (32.000ms at 16kHz), not an approximate/noisy ~32ms.** This is a suspiciously round, exact integer sample count. **UPDATE (§16.7, real hardware, 2026-09-18): the specific "active ALSA capture period/buffer-boundary" explanation for this has been directly tested and REFUTED** — the actual negotiated `plug:respeaker` capture setup is `period_size=2000` frames / `buffer_size=8000` frames (125ms/500ms), neither of which divides evenly by 512 (`2000/512=3.90625`, `8000/512=15.625`). A source-level scope check of the diagnostic script and its helpers (§16.7) found no application-level 512-sample (or 1024/2048-byte) fixed block anywhere in the capture/analysis path either. **The 512-sample MLS bimodality itself remains a real, reproduced observation — its source is UNRESOLVED, not invalidated**; the specific buffer-boundary hypothesis is the part that is refuted. See §16.7 for the full command output and scope-check audit.

**Finding 3 — the low correlation magnitude (~0.06-0.07) has a plausible, evidence-backed explanation: device-side spectral reshaping, not a broken measurement.** An offline FFT-based spectral comparison (this update) between each trial's `signal.wav` (source) and `mic.wav` (capture) found:

- Energy below 125Hz is reduced to ~0.0% in every capture (source has ~1.6%) — directly consistent with the already-confirmed `AEC_HPFONOFF=2` (125Hz high-pass, §6).
- A substantial, and nearly IDENTICAL across all 3 trials, redistribution of energy: the source's 4-8kHz share (~50%) is roughly halved in the capture (~25%), while the capture's 1-4kHz share is roughly double the source's own share there. This spectral reshaping profile is consistent with the device's confirmed ASR-oriented beamforming/AEC processing (`AEC_ASROUTONOFF=1`, §6) — not raw passthrough.
- Because this reshaping is nearly IDENTICAL across all 3 trials (regardless of which lag each trial locked onto), it does **not** explain the bimodal lag split — it is a separate, consistent characteristic of the capture path, and a genuine reason a raw linear cross-correlation against an unprocessed source would read low even when a real, consistent timing relationship exists underneath.

**Answering the operator's specific audit questions directly:**
- *Is the correlation operating on the correct aligned signal window?* — **Yes, confirmed.** The offline recomputation from the saved WAVs exactly reproduces the live-reported `best_lag_ms`/`normalized_correlation` for all 3 trials.
- *Does beamforming/ASR processing strongly decorrelate broadband MLS?* — **Plausible and evidence-backed** (Finding 3) as the explanation for the low correlation MAGNITUDE specifically; not (on its own) an explanation for the bimodal LAG split, which is a separate phenomenon.
- *Is the MLS bandwidth interacting with XVF3800 processing?* — Related to the above; the device's HPF and beamforming are broadband-aware, and the MLS's full-band (up to 8kHz Nyquist) content is measurably reshaped by them.
- *Does the capture require whitening/normalized filtering before correlation?* — **Plausible, standard technique (GCC-PHAT / pre-whitened cross-correlation)** for making a lag estimate robust to a known source of spectral coloring like this. Not implemented this update (scope) — flagged as a concrete, well-defined candidate follow-up if a future pass needs a sharper lag estimate than raw linear cross-correlation gives here.
- *Did one run experience a USB/ALSA scheduling offset? Is 102.5ms or 134.4ms a deterministic buffer-boundary difference?* — See Finding 2: the exact 512-sample separation is strong circumstantial support for this, but **not yet directly confirmed** against the device's real configured ALSA parameters (§14 has the read-only command to check this without guessing further).

**Conclusion, restated exactly as instructed:** MLS removing the periodic-tone ambiguity is **SUPPORTED** (trials 1/2's sub-millisecond agreement, plus the offline 112-vs-0 autocorrelation comparison). A stable physical acoustic lag across all trials is **NOT YET CONFIRMED** (trial 3 is a genuine, non-noise outlier, not measurement error). A single canonical lag value (e.g. averaging 102.5 and 134.4) is **NOT YET JUSTIFIED**.

---

## 7e. Filename bug found and fixed: captures did not encode `--stimulus`

The operator's live MLS run saved its 3 trials as `off_gain1_amp0.5_trial{1,2,3}_{signal,mic}.wav` — the SAME naming pattern earlier tone-stimulus runs also used (e.g. an earlier `on_gain1_amp0.5_trial*` tone run). Confirmed by inspecting file timestamps in `r0081_aec_captures/`: the diagnostic's default label was built from `condition`/`gain`/`amplitude` only — **`--stimulus` was never included**, so an MLS run at the same condition/gain/amplitude as a prior tone run would silently overwrite that prior run's WAV files. In this specific instance no actual prior file existed at that exact name (verified: no overwrite occurred this time), but the bug is real and would bite the next time the same condition/gain/amplitude combination is used with a different `--stimulus`.

**Fixed, this update, diagnostics-only, no production code touched:**
- Every mode's default label now includes `--stimulus` (`_run_single`: `{condition}_gain{gain}_amp{amplitude}_{stimulus}`; `_run_sweep`: `sweep_{point}_amp{amplitude}_{stimulus}`; the new `_run_confirm`: `confirm_{A|B}{cycle}_gain{gain}_amp{amplitude}_{stimulus}`).
- `run_condition()`'s saved-file trial suffix (`_trial{N}`) is now ALWAYS appended, even for `repeats=1` (previously omitted for single-trial runs, which combined with the missing `stimulus` field was the other half of the collision risk).

Every filename now encodes condition/gain/amplitude/stimulus/trial, e.g. `off_gain1.0_amp0.5_mls_trial1_mic.wav`.

---

## 8. Duplicate interruption frames — unchanged conclusion, now precisely explained

§4 above supersedes the prior draft's account of *why* duplicates occur (two independent call sites, not just one call's fan-out) but the **conclusion is unchanged**: `CloudTurnAccumulator.on_interruption()` is a plain, already-idempotent flag set (`nexa/realtime/turn.py:119-123`) — safe regardless of how many times or from which source it fires. The terminal print-debounce (presentation-only, zero interruption/router/turn-state logic touched) remains implemented and tested.

---

## 9. Root-cause audit priority order — updated status

| Priority | Item | Status |
|---|---|---|
| A | reSpeaker/XVF3800 capture endpoint (raw vs. AEC-processed) | **RESOLVED** by direct evidence (§6): USB descriptor terminal typing (`0x0405` Echo-Canceling Speakerphone, both directions) + `AEC_ASROUTONOFF=1` (beamformed/processed output, confirmed by direct DSP register read) both independently confirm the capture stream IS the processed output, not raw per-mic residuals. |
| B | Far-end reference correctness (gain, timing, format) | **Gain**: firmware `AEC_FAR_EXTGAIN=-20dB` confirmed (§6); AEC confirmed functioning at two signal levels, level-dependent (§7, §7b); a clean, non-clipped ×10 (+20dB) compensation test **REFUTES** the blunt "invert the firmware gain in software" hypothesis (§7b). A narrow sweep (0.25x-4.0x) found `gain=0.5` as the lowest-residual point (~30% better than gain=1.0, §7c) — **MATERIAL BUT UNCONFIRMED**, an open settle/order confound must be ruled out first (`--confirm` built, not yet run). **Timing**: relative reference/playback alignment remains **UNRESOLVED** — MLS confirmed fixing the tone stimulus's lag ambiguity, but the 3 OFF-condition MLS trials show a genuine bimodal lag split (~102.5ms vs ~134.4ms, exact 512-sample separation, §7d) with a leading but unconfirmed buffer-boundary hypothesis. |
| C | Two independent ALSA playback paths (timing drift) | Still not measured; deprioritized relative to B given B's much more direct, quantified evidence. |
| D | XVF3800 hardware/DSP configuration | **Largely resolved** by §6's direct register reads: AEC not bypassed, converged, 1 far-end/4 mics configured, HPF/emphasis normal, PCD disabled (reason unclear), RT60 inconclusive from a single idle read. |
| E | Silero sensitivity/thresholds | Still deliberately last, still not attempted — the ~0.7-0.9s sustained false-VAD duration remains far too long to be a small-threshold artifact, and the new firmware-level evidence gives a much more specific, better-targeted lead than blind VAD tuning ever would. |

### 9a. R0081 status summary table

| Item | Status |
|---|---|
| AEC effectiveness | **CONFIRMED** at two signal levels (§7, §7b) |
| AEC high-level OFF→ON reduction | **~16.7dB** (amplitude=0.5 repeatability set, §7b) |
| AEC low-level OFF→ON reduction | **~3.86dB** (amplitude=0.05, apples-to-apples set, §7b) |
| Residual echo | **CONFIRMED** present at every level tested (§7, §7b) |
| `AEC_FAR_EXTGAIN=-20dB` | **HARDWARE-CONFIRMED FACT** (§6, direct register read) |
| Software ×10 compensation | **REFUTED** — makes residual ~4.4× WORSE than gain=1.0, worse than OFF too (§7b, clean non-clipped retest) |
| `CoherentReferenceGain` | **NOT VALIDATED** — made things worse live (§2 condition B); stays off by default |
| Speaker volume alone | **NOT SUFFICIENT** — A2's lower volume did not materially reduce false episodes (§2) |
| `gain=0.5` improvement | **FAILED REPLICATION under counterbalanced confirmation** — 3/3 loss in A-first sequence, 1/3 win in B-first; combined ~6.36%/~0.54dB WORSE than gain=1.0 (§16.9) |
| `gain=0.5` live conversational candidate | **REJECTED / DE-PRIORITIZED** (§16.9) |
| Production `gain=1.0` | **KEEP UNCHANGED** — no evidence supports a change; not claimed globally optimal, only that this confirmation found no improvement from 0.5 (§16.9) |
| Reference gain mismatch as primary root cause | **NOT SUPPORTED** by current evidence (§16.9) |
| Gain-order/settle confound (original sweep) | **UNRESOLVED** — the sweep's apparent advantage did not reproduce under counterbalancing (§7c, §16.9), but the CAUSE (order/settle confounding vs. the newly-found time/state effect vs. both) is not assigned |
| AEC residual stability across one fixed physical setup | **NOT STABLE** — confirmed ~35-41% residual drop between the confirmation run's first and second half, with quiet floor and stimulus held fixed (§16.9) |
| Sequence/time effect vs. gain effect | **Sequence/time effect is FAR LARGER** (~35-41%) than the gain effect under test (~6%) (§16.9) |
| Cause of the sequence/time effect | **OPEN** — AEC warm-up/adaptation, retained cross-exposure state, and another time-dependent mechanism are candidates; not chosen among (§16.10) |
| Continuous in-stream residual change (first `--track-continuous` run) | **CONFIRMED** — non-monotonic, zero stream recreation; max/min span ~4.64×/~13.3dB (§16.14) |
| Simple monotonic warm-up model | **NOT SUFFICIENT** — real hardware shows repeated degrade/recover cycles, not a settling curve (§16.14) |
| Stream reopen/recreation required for large residual variation | **REFUTED** — the full ~13.3dB span occurred inside one uninterrupted stream (§16.14) |
| Intermittent timing/state/DSP/AEC instability | **STRONGLY SUPPORTED as a class**; exact layer (XVF3800 DSP vs. ALSA/USB/driver/host/acoustic) still OPEN (§16.14) |
| Cross-run quiet-floor comparability (§16.9 vs. §16.14) | **NOT COMPARABLE** — 15.3 RMS vs. ~7 RMS; recorded as a caveat, not an explanation for the in-run oscillation (§16.14) |
| MLS timing stimulus | **WORKING / better than tones** — trials 1/2 agree to ~0.19ms; tone stimulus independently confirmed ambiguous (112 vs 0 spurious peaks) (§7d) |
| Physical acoustic lag | **NOT YET STABLE 3/3** — bimodal (2× ~134.4ms, 1× ~102.5ms), exact 512-sample/32.000ms split — REAL OBSERVATION, source UNRESOLVED (§7d, §16.7) |
| 512-sample split caused by active ALSA capture period/buffer | **REFUTED** — actual negotiated `period_size=2000`/`buffer_size=8000`, neither divides evenly by 512; no app-level 512 block found in source either (§16.7) |
| Reference/acoustic timing mismatch | **UNRESOLVED** (§7d, §16.7) |
| False local VAD | **CONFIRMED** (§2) |
| Core recall | **CLEARED** as a cause (§3); PASS on its own merits (§1) |
| Regression-isolation gate (golden `7dd6b87` vs. current, today) | **CASE 2: BOTH FAIL** (§15.9) — software/path regression relative to golden NOT supported as primary explanation |
| Core/Memory/Recall as regression cause | **DE-PRIORITIZED** (§15.9 — Core Recall disabled in current, never present in golden, both still fail) |
| Mixer/device snapshot drift, golden vs. current | **NOT OBSERVED** (measured snapshot only, §15.9) |
| Gemini model difference as sole explanation | **REFUTED** — both models show the same failure class (§15.9); a partial/severity contribution is not ruled out |
| False ASR transcription during silence (golden log, `[Tu es beau.]`) | **CONFIRMED** — strong direct evidence of speech-like acoustic leakage during operator silence, highly consistent with self-echo in context; not, alone, absolute proof of provenance (§15.9) |
| Reference queue overflow (current run) | **CONFIRMED** — one episode, 144 dropped chunks, `asyncio.Queue(maxsize=24)` overflow; speaker path continues independently of reference-enqueue success (not proven to play perfectly — R0071's own known stutter issue is a plausible connection) while reference develops content gaps (§16) |
| Overflow precedes first false VAD in that answer | **CONFIRMED** — queue growth by 10.6s, first drop by 9.5s (§16.5.1) |
| First false interruption caused the initial overflow | **REFUTED for that episode** — overflow demonstrably began before any false VAD in that answer (§16.5.1) |
| Golden path has the same queue architecture | **CONFIRMED from full source diff** — byte-for-byte unchanged since `7dd6b87`; golden actually overflowing during its own session is NOT measured (§16.3) |
| Queue overflow as sole root cause | **REFUTED by A2** — false VAD with `REF_DROPPED=0` (§16.4), and by this session itself: 7 of 9 proxy-classified false events occurred with a healthy, zero-drop queue (§16.5.5) |
| Queue overflow as aggravating contributor | **STRONGLY SUPPORTED, causality not fully proven** — temporally associated with the session's most severe cluster (2 of 9 proxy-classified false events), not its overall rate (§16.5.5) |
| Baseline residual echo | **CONFIRMED sufficient for false VAD even when the reference queue is healthy — reference overflow is NOT necessary for the symptom** (§7/§7b, §16.5.5) |
| Intermittent scheduling/state-margin problem | **LEADING CLASS OF HYPOTHESES** (§16.6) — working model, not concluded root cause |

**R0081 is still NOT PASS.**

---

## 10. Forbidden fixes, blind tuning, and firmware writes — confirmed not applied

No interruption-while-speaking suppression, no VAD disabling, no blanket mic muting, no `confidence`/`min_volume`/`start_secs` change. **No DSP register was written** — every `xvf_host.py` invocation in §6 omitted `--values`, confirmed read-only by the tool's own `--help` text and by the fact that a write requires that flag. `/etc/asound.conf` and all persistent device configuration are untouched.

---

## 11. Files changed

- `src/nexa/realtime/gemini/simple_conversation.py` — direction/sibling/vad_active-aware `InterruptionFrame` diagnostics (§4); `_vad_active` bookkeeping.
- `tests/test_simple_cloud_conversation.py` — `TestInterruptionDirectionTelemetry` (6 new); corrected the one pre-existing assertion that expected a bare `"INTERRUPTION_FRAME"` label; fixed one test's reliance on same-batch queue ordering (the `SystemFrame`-priority finding, §4) with explicit sequential awaits.
- `docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py` — the deterministic, non-conversational AEC measurement script. Previously gained `amplitude`/`clip_stats`/gain-chain diagnostics, `--repeats`, `--sweep`, `--stimulus {tones,mls}` (§7a, §7b), and a first (later corrected) `--confirm` implementation. This update: (1) fixes the filename bug (§7e) — every mode's default label now includes `--stimulus`, and the saved-file trial suffix is now always appended, even for single-trial runs; (2) **corrects `--confirm` to be genuinely counterbalanced** — it now runs BOTH an A-first (ABABAB) and a B-first (BABABA) sequence in one invocation, instead of a single alternating sequence that could not rule out order effects (§7c, §9); (3) adds `--post-settle-gap` (shared by `--sweep`/`--confirm`, default 0.0/off) and `quiet_before_rms` reporting in every summary block. Verified via `py_compile`/`ruff check` (both clean) plus targeted offline sanity checks: argparse mutual-exclusivity/default resolution, sequence-construction correctness (`ABABAB`/`BABABA`), and two stubbed end-to-end `_run_confirm` runs with a fake `run_condition` — one simulating a genuine order-independent gain effect (correctly verdicted as order-independent) and one simulating a pure position/order effect with NO real gain difference (correctly verdicted as "mixed," not falsely claimed order-independent). This standalone research script remains intentionally outside the pytest suite (consistent with `m2_6b4m_self_echo_probe.py`'s own precedent).
- `/tmp/.../scratchpad/r0081_mls_peak_audit.py` (session scratchpad, NOT part of the repo, not committed) — offline-only analysis of the 3 already-captured MLS OFF-condition WAVs: full correlation curve/peak-picking (§7d Finding 1) and FFT-based spectral-band comparison (§7d Finding 3). No hardware access; read-only against existing capture files.

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
| Direct, deterministic AEC measurement (§7, §7b) | **RUN, repeatedly, at two levels** — AEC effectiveness confirmed and level-dependent; ×10 compensation cleanly REFUTED (non-clipped, apples-to-apples) |
| OFF/ON repeatability (≥3 runs each) | **RUN** (§7b, both the amplitude=0.5 and amplitude=0.05 sets) |
| Non-clipped +20dB gain-compensation retest | **RUN** (§7b) — result: REFUTED, makes residual worse |
| Narrow gain sweep (0.25x-4.0x around baseline) | **RUN** (§7c) — `gain=0.5` lowest of 6 points, but settle/order confound open |
| MLS timing stimulus, OFF-condition repeatability | **RUN** (§7d) — tone ambiguity confirmed fixed; physical lag bimodal, not yet stable 3/3 |
| Balanced A/B gain confirmation (`--confirm`) | **NOT YET RUN** — built this update (§7c, §14) |
| ALSA period-size / buffer-boundary check | **NOT YET RUN** — read-only command identified, not yet executed (§7d, §14) |

**R0081 still does not pass.** AEC is confirmed working, is level-dependent, and leaves a substantial, speech-magnitude-consistent residual at every level tested; a blunt ×10 software compensation for `AEC_FAR_EXTGAIN=-20dB` is cleanly refuted. A narrower gain candidate (`gain=0.5`) and a real MLS-based timing improvement both emerged this round, but each has its own open confound (settle/order for the gain sweep; a bimodal, not-yet-stable lag for timing) that must be resolved before either can inform a production decision.

---

## 14. Gain sweep / timing follow-up — DEFERRED behind §15, not abandoned

**This section's own two commands (`--confirm`, the verbose ALSA query) are NOT the immediate next step.** A higher-level causal question was raised after this section was written: **is any of this gain/timing work even addressing a software regression, or is the false self-interruption itself a NEW symptom relative to a historically clean, operator-confirmed baseline on today's same hardware?** §15 (new) answers that FIRST, via a direct regression-isolation experiment, before any more parameter tuning. Nothing below is deleted or retracted — it remains valid, ready-to-run evidence, just temporarily de-prioritized. Proceed to §15 for the operator's actual next action.

**Do not start with another free-form conversation test yet.** Two things, in this order (once §15 clears):

**1. Counterbalanced A/B gain confirmation (rule out the sweep's settle/order confound):**

```bash
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py --confirm
```

Runs BOTH an A-first (`ABABAB`, `gain=0.5` then `gain=1.0`, 3 cycles) AND a B-first (`BABABA`) sequence in one invocation — fixed `--amplitude 0.05`, a longer 3.0s settle before every block (up from the sweep's 1.0s). A single alternating sequence alone cannot rule out order effects (one gain would never occupy the "goes first" role); running both orders can. Ends with a `CONFIRM SUMMARY` showing the A-first sequence's own results, the B-first sequence's own results, and the combined pool, plus how many cycles each gain "won" **in each sequence separately**. **Return the full output**, especially both sequences' own win counts (not just the combined numbers) and every `quiet_before_rms` line per trial (watch specifically for the same first-trial-elevated pattern seen in the sweep).

- Only treat `gain=0.5` as beating `gain=1.0` "regardless of order" if it wins in (nearly) every cycle in **BOTH** the A-first **and** the B-first sequence — the script's own final verdict line enforces this and will report a "mixed result" otherwise rather than claiming order-independence from a one-sided win.
- If the result is genuinely mixed (the two sequences disagree, or either one is itself mixed): the original sweep was likely confounded — de-prioritize gain calibration and treat timing/residual characteristics as primary. Do not average or pool your way past a mixed result.
- If `quiet_before_rms` is STILL elevated on first trials despite the longer 3.0s settle: re-run with `--confirm --post-settle-gap 0.5` (or similar) to test whether an explicit silent gap (as opposed to more settle-with-playback) is what's actually needed — report which one clears the anomaly, if either does.

**2. Directly inspect the ACTUAL negotiated ALSA PCM setup (not just the capability range) to test the buffer-boundary timing hypothesis (§7d Finding 2).** `--dump-hw-params` alone only reports the device's pre-configured hardware-parameter capability/range space (what the driver CAN offer) — it does NOT show what was actually negotiated for a real capture. Adding `-v`/`--verbose` is what shows the actual configured PCM setup. This is a READ-ONLY query — it opens the capture device briefly (1 second) to negotiate and print its parameters, the same category of action already performed throughout this investigation, but is NOT something this update ran unilaterally:

```bash
arecord -D plug:respeaker \
  -f S16_LE \
  -r 16000 \
  -c 1 \
  --dump-hw-params \
  -v \
  -d 1 \
  /dev/null
```

**Return the FULL output**, not just a single number. Explicitly identify, in your reply, which lines are the **capability/range information** (typically printed first, e.g. `period_size: [X Y]`-style ranges) versus the **actual configured/negotiated PCM setup** (the `-v` section, showing the single, actual `period_size`/`buffer_size`/`periods` values ALSA settled on for this capture). Only the actual configured `period_size` value corroborates or refutes the exact-512-sample bimodal split found in §7d — a range that merely CONTAINS 512 is not sufficient evidence on its own.

**3. Optional, if convenient:** a repeat of the `--diagnostic-timeline` conversational run (A1-style, no `--coherent-reference-gain`) so the direction/sibling/`vad_active`-aware `INTERRUPTION_FRAME_*` labels (§4) can be read directly, to confirm or refute the "delayed Gemini-acknowledgement" theory for the unpaired interruptions:

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py --diagnostic-timeline --diagnostic-audio-levels
```

**Return** the printed `INTERRUPTION_FRAME_*` lines (direction/sibling/vad_active/awaiting_assistant) in order, with timestamps.

No firmware write, no Silero tuning, no `CoherentReferenceGain` enable, no production gain change, and no free-form live-conversation acceptance run should happen until items 1-2 above give a clear read on the settle/order confound and the buffer-boundary hypothesis.

---

## 15. Regression-isolation gate — THE IMMEDIATE NEXT STEP (inserted ahead of §14)

**The operator's question:** *"Why did this work correctly before, and now self-interrupt again?"* R0071 recorded that the exact final accepted R0031/M2.6A revision (`7dd6b87`), re-run unmodified in an isolated worktree against **today's same hardware topology**, was clean — 10/10, zero self-interruption. Today's accepted simple cloud path (the same R0071-frozen baseline this whole R0081 investigation concerns) shows repeated false `LOCAL_VAD_START`/self-interruption on the same general hardware. Before any more gain/timing tuning, directly test: (A) a software regression relative to the known-good path, or (B) a current hardware/environment/provider-state change that would affect the OLD known-good implementation too.

### 15.1 Phase 1 — the exact golden procedure, recovered from source (not reconstructed from memory)

R0071's own report (`docs/reports/R0071_golden_voice_recovery_and_boundary_20260916.md`) names the commit and the qualitative result but does **not** itself contain the exact command or config table — that was recovered directly from two things preserved on this machine from R0071's own original run: the git history at `7dd6b87` and an **already-existing, still-present worktree** (`/home/devdul/Projects/NeXa_IkiGai_golden_m26a`, still checked out at `7dd6b87`, `git diff --stat` empty against that commit — confirmed clean, not reconstructed) containing the actual preserved session log (`r0031_golden_session.log`) from R0071's own original golden re-run.

**Recovered, directly from the probe's own printed config header and source, not inferred:**

| Field | Value |
|---|---|
| Script | `docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py` (at `7dd6b87`) |
| Invocation | No flags = full live session (default `--voice` = `Sulafat`, confirmed `DEFAULT_VOICE = "Sulafat"` in source) |
| Gemini model | `gemini-3.1-flash-live-preview` (hardcoded `MODEL` constant, **not** CLI-overridable) |
| Voice | `Sulafat` ("Warm") |
| `in_sample_rate` / `out_sample_rate` | 16000 / 24000 |
| `input_device_name` / `output_device_name` | `respeaker` / `usb_speaker` |
| `aec_reference_device` | `plug:respeaker` |
| `server_vad` | disabled (local Silero is turn authority) — same architecture as today's accepted path |
| VAD params | `confidence=0.7 start_secs=0.2 stop_secs=0.5 min_volume=0.6` (from the preserved log) |
| Credential | `NEXA_GEMINI_API_KEY` env var, or read directly from `~/.config/nexa/secrets/gemini.env` if unset (script's own fallback, confirmed in source) |
| Mixer state recording | **NOT built into the probe** — confirmed by source grep (`amixer`/`mixer`/`ALSA` all absent from the script). Must be captured separately, read-only, exactly as §6 already established. |
| Exit | Ctrl+C (`KeyboardInterrupt` → exit 130) |

**What "10/10 / 14s silent window" actually is:** the probe is a **free-form live conversation** (Ctrl+C to stop), not a scripted N-trial harness with a built-in pass/fail counter — confirmed directly from source (only `--dry`/`--lifecycle-smoke`/`--recompute`/`--voice`/`--note` flags exist; no trial-count or window-duration parameter). "10/10" and "14s" are the **operator's own qualitative tally and recollection** of that live session, not a script-measured metric. The preserved log's own `SESSION SUMMARY` (auto-printed at Ctrl+C) shows 9 reconstructed turns, 7 barge-in candidates, and the single longest uninterrupted bot-speaking stretch was **~16.6s** (17:23:57.852 → 17:24:14.423, no interruption during it) — consistent with, though not numerically identical to, "14s"; there is no evidence of self-interruption anywhere in the preserved log. This is recorded precisely so a future reader does not mistake "10/10" for an automated test result.

### 15.2 Phase 2 — the isolated worktree already exists; a real dependency-isolation risk found and fixed

**Do not create a new worktree — reuse the existing one.** `git worktree list` shows:

```
/home/devdul/Projects/NeXa_IkiGai              <main HEAD>  [main]
/home/devdul/Projects/NeXa_IkiGai_golden_m26a  7dd6b87 (detached HEAD)
```

This is the exact worktree R0071 itself created and used. `git diff --stat` against `7dd6b87` inside it is empty (only an untracked results JSON and gitignored `__pycache__` dirs sit alongside it) — it is unmodified. No new worktree needed; nothing about the current repo's branch/index/working tree was touched to confirm this (read-only `git worktree list`/`git diff`/`git status` only).

**A real, verified dependency-isolation risk, found this session, not previously documented anywhere:** the golden probe has NO `sys.path`/`PYTHONPATH` setup of its own — it imports `nexa.voice.aec`, `nexa.voice.config`, `nexa.voice.device`, `nexa.voice_tts.aec_reference` as plain package imports. `nexa` is installed **editable** in the shared main `.venv`, resolving to `/home/devdul/Projects/NeXa_IkiGai/src/nexa/__init__.py` — **today's current main tree, not the golden worktree's own historical copy** — confirmed directly: `.venv/bin/python3 -c "import nexa; print(nexa.__file__)"` prints the MAIN repo's path, not the worktree's. This means naively running the golden worktree's script with the shared `.venv` python would actually execute **today's** `AecReferenceFeeder`/`LocalAudioConfig` code, not `7dd6b87`'s — silently contaminating the exact subsystem under suspicion. `git diff 7dd6b87 HEAD -- src/nexa/voice/config.py src/nexa/voice_tts/aec_reference.py` confirms these files HAVE changed since (97 lines total: `config.py` gained the R0053 `output_alsa_mixer_card` field; `aec_reference.py` gained the R0053 gain-scaling mechanism and R0081's own `on_diagnostic` hooks) — additive/default-preserving on inspection, but there is no reason to rely on "probably fine" when a correct fix is simple. **Fix, verified working this session (via `--dry`, no hardware/network):**

```bash
PYTHONPATH=/home/devdul/Projects/NeXa_IkiGai_golden_m26a/src \
  .venv/bin/python3 \
  /home/devdul/Projects/NeXa_IkiGai_golden_m26a/docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py \
  --dry
```

verified (this session) to print `settings_ok: True` and correctly resolve `nexa` from the golden worktree's own `src/` (`PYTHONPATH` entries are searched before the editable install's site-packages entry — confirmed empirically, not assumed: `import nexa; print(nexa.__file__)` under this exact `PYTHONPATH` prints the golden worktree's path). **`--dry` never opens a device or calls the network** — this verification did not touch hardware or Gemini, consistent with the instruction not to run hardware this pass. **Note this is a genuine improvement over R0071's own original golden re-run**: nothing in R0071's report confirms `PYTHONPATH` isolation was used originally, so its own "byte-identical modulo the gain mechanism" claim (§ "WHAT I VERIFIED") was reasoned from a static `git diff`, not confirmed live. Today's regression-isolation run, using the command above, IS rigorously isolated for NeXa's own code.

### 15.3 Dependency-isolation status — stated explicitly, per instruction

- **Third-party dependencies (`pipecat-ai`, `google-genai`, etc.): SHARED and IDENTICAL between golden and current.** Both runs use the same single `.venv` (confirmed: `pipecat-ai==1.8.1`, `google-genai==2.22.0` installed; the golden worktree's own `pyproject.toml` pins the identical `pipecat-ai[local]==1.8.1`). This does **NOT** isolate dependency drift *since `7dd6b87` was originally written* — if a `pip install -U`/environment change happened between then and now, both runs would share it identically, and this experiment cannot detect that as a separate variable. Named here, not silently assumed away.
- **NeXa's own `src/nexa` code: NOW correctly isolated**, via the `PYTHONPATH` fix above (§15.2) — the golden run will execute the TRUE `7dd6b87` version of `AecReferenceFeeder`/`LocalAudioConfig`/etc., not today's.
- **The Gemini MODEL itself differs, and this is NOT eliminable without editing the "unmodified" golden probe (which would defeat its purpose as a control).** Verified directly this session: the golden probe's `MODEL` constant is hardcoded `gemini-3.1-flash-live-preview` (no CLI override exists); the CURRENT accepted path's `GEMINI_MODEL` constant (`simple_conversation.py`) is `models/gemini-2.5-flash-native-audio-preview-12-2025` — confirmed via the current app's own `--dry` output. The current code's own comment states this was a **deliberate, evaluated choice** (R0080 §15, not an accidental drift) — but for THIS regression-isolation experiment specifically, it means a "Case 1" result (golden clean, current false-interrupt) cannot, by itself, fully rule out a Gemini-side model difference in server-VAD/interruption behavior as a contributing factor alongside (or instead of) a NeXa code regression. This is flagged explicitly in the decision table below rather than left implicit.

### 15.4 Phase 3 — making the comparison fair

Run both back to back, no intentional hardware/environment change between them: same physical speaker volume, speaker position, reSpeaker position, room, USB topology, mixer state, output device, input device. **Do not ask the operator to change any of these** — only observe/record.

**Read-only mixer/device snapshot** (reuses exactly the read-only pattern already established in §6 — `amixer`/`aplay -l`/`arecord -l`, no `--values`/no write flag), recommended immediately before EACH run:

```bash
aplay -l; arecord -l
amixer -c Array scontents
amixer -c UACDemoV10 scontents
```

Return this output for both "immediately before golden" and "immediately before current" — if they differ, that is itself a finding (e.g. a mixer value drifted between the two runs, which would itself explain a result even absent any code difference).

### 15.5 Required golden run — exact operator procedure

```bash
set -a; . ~/.config/nexa/secrets/gemini.env; set +a
PYTHONPATH=/home/devdul/Projects/NeXa_IkiGai_golden_m26a/src \
  .venv/bin/python3 \
  /home/devdul/Projects/NeXa_IkiGai_golden_m26a/docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py \
  2>&1 | tee /tmp/r0081_golden_rerun_$(date +%Y%m%dT%H%M%S).log
```

1. Ask a question that invites a sufficiently long spoken answer (R0071's own preserved session had multiple ~9-17s uninterrupted bot-speaking stretches organically — any question inviting a multi-sentence explanation is fine; there is no fixed scripted prompt to reuse, since none exists in the tooling — see §15.1).
2. Remain completely silent while NeXa speaks.
3. Observe whether ANY self-interruption/self-conversation occurs (NeXa stopping and restarting, or responding to herself, with no real speech from the operator).
4. Perform at least one deliberate real interruption (speak over NeXa mid-response) to confirm genuine barge-in still works in this golden path.
5. `Ctrl+C` to end the session — the probe prints its own `SESSION SUMMARY` and writes a results JSON under `docs/research/m2_6_cloud_realtime_voice/` inside the golden worktree.

No cleanup is required — the worktree is a persistent, reusable asset (already reused once). If disk space needs reclaiming later, `git worktree remove /home/devdul/Projects/NeXa_IkiGai_golden_m26a` from the main repo would remove it, but this is optional and NOT requested here.

### 15.6 Required current-runtime control — exact operator procedure

Immediately after, same physical conditions, closest behaviorally-equivalent mode: default/unscaled reference gain (no `--coherent-reference-gain`, no candidate `gain=0.5` — that is §14's own still-open question, deliberately not mixed into this test), Core Recall disabled via `--no-core-recall` (source-verified this session: `recall_executor` defaults to `None`; both its call sites in `simple_conversation.py` are `if recall_executor is not None:` guards, so `None` skips them entirely without touching any audio/VAD/AEC/playback construction — corroborated by existing tests `test_no_recall_executor_is_byte_for_byte_unchanged`/`test_default_none_is_byte_for_byte_unchanged`). `--diagnostic-timeline --diagnostic-audio-levels` are included for comparable visibility to the golden log's own DEBUG-level output (the current app forces `loguru` to `WARNING` unconditionally, so without these flags almost nothing would print) — both are purely-observational per this report's own §4/§7 verification; `--diagnostic-audio-levels` does insert one small, read-only `_MicLevelTap` node into the pipeline (RMS-only, never mutates/drops/delays a frame) — named here rather than silently claimed as zero topology change:

```bash
.venv/bin/python apps/nexa_cloud_voice_simple.py \
  --no-core-recall \
  --diagnostic-timeline \
  --diagnostic-audio-levels \
  2>&1 | tee /tmp/r0081_current_control_$(date +%Y%m%dT%H%M%S).log
```

Same 5 operator actions as §15.5 (long response, silence, observe, deliberate interrupt, Ctrl+C).

### 15.7 What to return

From BOTH runs: (1) the mixer/device snapshot from §15.4 taken immediately before that run; (2) the full terminal output (the `tee`d log); (3) an explicit yes/no + timestamps for any self-interruption observed; (4) confirmation the deliberate interruption worked. From the golden run specifically: the auto-printed `SESSION SUMMARY`. From the current run specifically: the `INTERRUPTION_FRAME_*`/`LOCAL_VAD_START`/`BOT_AUDIO_STARTED` diagnostic-timeline lines.

### 15.8 Decision table — interpret ONLY after both runs happen today

| Case | Golden `7dd6b87` | Current | Conclusion |
|---|---|---|---|
| **1** | CLEAN | FALSE SELF-INTERRUPT | Software/path regression **strongly** supported (caveat: §15.3's Gemini-model-string difference is a real, uneliminated confound — do not treat this as 100% code-isolated). Next: smallest commit/path delta between `7dd6b87` and today's simple path capable of affecting capture/VAD, AEC feeder, playback scheduling, Pipecat processor ordering, ALSA write behavior, or interruption propagation — a bisect/differential plan, not more gain tuning. |
| **2** ← **SELECTED, see §15.9** | FALSE SELF-INTERRUPT | FALSE SELF-INTERRUPT | A later NeXa Core/Memory/Recall software regression is **NOT** supported as the primary explanation (Core Recall was already disabled in the current run, and the golden path never had it at all). Something shared by both runs TODAY has changed or is variable — prioritize ALSA/device timing state, XVF3800 state, physical/acoustic conditions, USB scheduling/buffering, and (per §15.3) genuinely-shared dependency drift since `7dd6b87` was written, over a NeXa-code explanation. |
| **3** | CLEAN | CLEAN | The current fault is intermittent/state-dependent — supports the stability-margin/timing/adaptation hypothesis already on record (§7b). **Do not declare it fixed from one clean run** — repeat enough silent-response trials to bound the intermittent rate before concluding anything. |
| **4** | Either run invalid (setup/device/provider error, e.g. wrong device index, credential failure, crash) | — | Report INVALID; do not interpret. Re-run under corrected conditions. |

### 15.9 RESULT (2026-09-17, real hardware): CASE 2 — both golden and current fail today

The operator ran both procedures from §15.5/§15.6 back to back, same physical setup, mixer/device snapshot taken before each.

**Golden `7dd6b87` (dependency-isolated via `PYTHONPATH`): FALSE SELF-INTERRUPT.** Not clean. The first long answer showed severe, repeated self-interruption (content delivered piece-by-piece, "extremely difficult to listen to" — operator's own words). One later spoken segment was temporarily perfect/uninterrupted. A later question tested in complete silence still self-interrupted. Behavior varied strongly between utterances within the SAME session — some periods nearly perfect, others severely broken.

**Current (default gain, `--no-core-recall`, `--diagnostic-timeline --diagnostic-audio-levels`): FALSE SELF-INTERRUPT.** Also not clean — consistent with all prior R0081 evidence.

**Mixer/device snapshot, immediately before each run — IDENTICAL in the supplied logs:**

| Device | Control | Value |
|---|---|---|
| USB speaker (`UACDemoV10`, card 2) | `PCM` playback | 88 / 60% / -11.95dB |
| reSpeaker (`Array`, card 3) | `PCM`,0 playback | 60 / 100% / 0.00dB |
| reSpeaker | `PCM`,1 playback | 40 / 67% / -20.00dB |
| reSpeaker | `Headset`,0 capture | 46 / 77% / -14.00dB |
| reSpeaker | `Headset`,1 capture | 60 / 100% / 0.00dB |

No mixer/device-index drift observed between the two back-to-back runs. **This is a narrower claim than "all hardware state is identical"** — only the measured mixer/device snapshot is; acoustic/thermal/USB-scheduling/XVF3800-internal state was not and could not be captured this way.

**Recorded per instruction:**

```
Regression gate:                                CASE 2
Golden historical source (7dd6b87) today:       FAIL
Current today:                                  FAIL
Core/Memory/Recall regression:                  DE-PRIORITIZED (not supported as primary cause)
Mixer/device drift between back-to-back tests:  NOT OBSERVED (measured snapshot only)
```

**Gemini model difference (§15.3) is confirmed NOT the sole explanation** (not "zero influence" — just not sufficient alone): `gemini-3.1-flash-live-preview` (golden) and `models/gemini-2.5-flash-native-audio-preview-12-2025` (current) both exhibit the same CLASS of false self-interruption today. A model-level contribution to severity/timing cannot be ruled out from this evidence, but model choice alone cannot explain presence-vs-absence of the symptom, since both models show it.

**Not merely a VAD-sensitivity artifact — a real false transcription.** The golden log, during the operator-silent long-answer test, contains a genuine false ASR transcription: `[Transcription:user] [Tu es beau.]` — French, produced with no real user speech present. This is materially stronger evidence than a VAD start/stop pair: actual speech-like acoustic content reached Gemini's own speech recognition with enough fidelity to produce a plausible (if wrong-language) transcription — not just cross a local energy/confidence threshold. **Precisely stated**: this is strong direct evidence of speech-like acoustic leakage reaching the recognition path during operator silence, highly consistent with self-echo in the observed context (repeated self-interruption during NeXa's own playback, immediately before and after) — it does **not**, by itself, mathematically prove the leaked content was specifically NeXa's own voice as opposed to some other acoustic source; self-echo remains the leading, evidence-consistent explanation, not an established fact from this transcription alone.

**Complete false-start/interruption sequence, from the supplied golden excerpt** (silent-operator segment starting after the 19:36:36.399 `Bot started speaking`): 6 distinct `User started speaking → Bot stopped speaking → Gemini interrupted` cycles are given explicitly, at 19:36:37.797, 40.556, 43.658, 46.477, 49.377, 51.977, with more beyond these ("and many more" — the complete count requires the full raw log, not yet supplied; see §16.5). **The 5 inter-cycle gaps in the given excerpt are strikingly regular**: 2.759s, 3.102s, 2.819s, 2.900s, 2.600s — mean ≈2.84s, range 2.6-3.1s. This regularity is noted as a real, computed observation (not asserted as necessarily causal) — a periodicity this tight is more consistent with a recurring scheduling/retry/recovery cadence than with random acoustic-threshold noise, though this report does not yet know which mechanism produces it.

### 15.10 What remains valid and untouched from §§1-14

Not deleted, not reverted, temporarily secondary: AEC works but residual remains (§7, §7b); software ×10 compensation refuted (§7b); `gain=0.5` an unconfirmed candidate awaiting counterbalanced confirmation (§7c, §16.8); MLS found ~102.5ms/~134.4ms bimodality with an exact 512-sample split — the active-ALSA-period explanation for the split is now REFUTED, source still unresolved (§7d, §16.7). §16 (new) is inserted as the immediate next priority given the major new evidence in §15.9-15.10 — a reference-queue overflow found in the current run's own diagnostics.

---

## 16. Reference queue overflow — source audit, golden/current structural comparison, and reconciliation with A2

### 16.1 The signal

The current control run's `AecReferenceFeeder` telemetry (`REF_QUEUE_DEPTH`/`REF_DROPPED`, `--diagnostic-audio-levels`) shows a clear overflow episode during assistant playback: depth `0 → 1 → 12 → 11 → 20 → 23 → 24` (hitting the queue's own bound), with `REF_DROPPED` climbing `3, 7, 17, 32, 55, 70, 89, 105, 113, 127, ... 144` before the queue drains back to depth 0, `REF_DROPPED` remaining fixed at 144 in later telemetry (as supplied). This is treated as a first-priority signal, not a minor diagnostic detail: it means the far-end reference path demonstrably failed to keep pace with outgoing playback for part of the run, and the real speaker output and the XVF3800's far-end reference cannot have remained sample-content-equivalent throughout that interval.

### 16.2 `AecReferenceFeeder` queue semantics — answered from source (`src/nexa/voice_tts/aec_reference.py`), not assumption

**1. What is one "chunk"?** One chunk = the exact `bytes` payload of ONE `TTSAudioRawFrame.audio` — confirmed by `process_frame()`: `if isinstance(frame, TTSAudioRawFrame) and frame.audio: ... self._enqueue(pcm)`, no internal re-slicing. Tracing that frame's origin in the INSTALLED Pipecat source (`.venv/lib/python3.13/site-packages/pipecat/services/google/gemini_live/llm.py`, `_handle_msg_server_content`): `audio = inline_data.data; frame = TTSAudioRawFrame(audio=audio, sample_rate=self._sample_rate, num_channels=1)` — **one chunk is exactly one raw Gemini Live server message's audio payload**, whatever size Google's own server and the network delivery produce. There is no fixed-size slicing anywhere in this path. **Frame sizes are confirmed VARIABLE, size controlled server-side, not by NeXa's own code.**

   The commonly-cited "20-40ms audio chunks" figure (R0030 §C2, `https://ai.google.dev/gemini-api/docs/live-api/best-practices`) is **Google's own recommendation for chunking INPUT audio sent TO Gemini** (mic → Gemini), explicitly labeled "Input chunk size" in that same report — it is **not** a documented or guaranteed figure for OUTPUT audio chunk size (Gemini → speaker), which is the side `AecReferenceFeeder` mirrors. Applying the input-side figure to the output side would be exactly the kind of unjustified assumption this audit was told to avoid — **not done here.**

   The reference feed itself plays at `OUTPUT_SAMPLE_RATE_HZ = 24000` Hz (`AecReferenceFeeder(..., sample_rate=OUTPUT_SAMPLE_RATE_HZ, ...)` in `simple_conversation.py`), mono, S16_LE — confirmed from the same construction call site.

**2. What happens at `DEFAULT_MAX_QUEUED_CHUNKS = 24`?** From `_enqueue()`:
   ```python
   def _enqueue(self, pcm: bytes) -> None:
       try:
           self._queue.put_nowait(pcm)
       except asyncio.QueueFull:
           # drop the oldest, keep the newest (freshest reference matters)
           try:
               self._queue.get_nowait()
               self._queue.put_nowait(pcm)
           except (asyncio.QueueEmpty, asyncio.QueueFull):
               pass
           self.chunks_dropped += 1
   ```
   **Drop OLDEST, keep newest** — confirmed directly, matches the code's own comment. Relative order of the remaining (surviving) chunks is preserved; the queue always holds the most recent ≤24 chunks. Net effect over a sustained overflow: the reference feed continuously skips forward, discarding older not-yet-played content, converging toward "catch up to the newest audio" rather than accumulating a fixed constant delay.

**3. What consumer drains the queue, and what can make it slower than the producer?** `_run_writer()`: a single asyncio task loops `pcm = await self._queue.get()`, then `await loop.run_in_executor(None, self._sink.write, pcm)`. `_PcmSink.write()` does `self._proc.stdin.write(pcm); self._proc.stdin.flush()` into a **persistent, single** `aplay -D plug:respeaker` subprocess's stdin pipe. The blocking write happens in a threadpool executor (correct — does not block the asyncio loop itself), but the writer task can only pull its NEXT queue item once that executor call returns. `aplay` consumes its stdin at real-time playback pace — so if `TTSAudioRawFrame`s ever arrive faster than real-time (a burst of buffered/delayed Gemini server messages delivered in a cluster, e.g. after network jitter or a scheduler/GC pause), the write-side cannot keep up, the bounded `asyncio.Queue` fills, and overflow/drop follows exactly as observed. This mechanism is sufficient to explain the overflow on its own: `write()`/`flush()` blocks until it returns; the producer can keep enqueueing independently in the meantime; the bounded queue reaches `maxsize=24`; drop-oldest occurs; 144 drops were observed. (No specific OS pipe-buffer capacity figure is used in this conclusion — actual pipe capacity on this runtime was not measured, and is not needed to establish the mechanism.)

**4. Is the REAL speaker exposed to the same buffering/backpressure? Confirmed: NOT via this mechanism — they are independent consumers.** `AecReferenceFeeder` is a TEE: `process_frame()` always ends with `await self.push_frame(frame, direction)`, unconditionally forwarding the SAME frame downstream toward the real output transport, **regardless of whether `_enqueue()` succeeded or silently dropped**. The real speaker's own playback path (Pipecat's standard output transport) is a **separate consumer of the same frames**, with its own independent buffering, **not gated by or coupled to `AecReferenceFeeder`'s queue state in any way**. **Precisely stated, this source proves only**: *the speaker path continues independently of reference-enqueue success* — it does **not** prove the real speaker plays perfectly/continuously; the output transport has its own, separately-owned buffering/scheduling that this audit did not inspect, and R0071 already recorded a known, unresolved "occasional short playback/stream continuity stutter" on this exact accepted baseline (see §16.6 item 6 for why that existing observation is a relevant, plausible connection). What IS established: during an overflow episode, whatever the real acoustic output is doing, the XVF3800's far-end reference input is independently missing content — the AEC's adaptive filter is fed a reference signal with real GAPS relative to whatever is acoustically emitted, precisely the kind of reference/acoustic misalignment that degrades cancellation and lets genuine self-echo through to trip local VAD.

**5. `REF_DROPPED = 144` in audio time — cannot be precisely or even order-of-magnitude-bounded from currently available evidence.** Because (a) chunk size is confirmed variable/server-controlled (§16.2.1), and (b) the only documented Gemini Live chunk-duration figure applies to the INPUT side, not output, **this report does not multiply 144 by a guessed constant.** A real number would require a new, narrow diagnostic addition (e.g. logging `len(pcm)` per enqueue, or accumulating dropped-byte totals alongside the existing `chunks_dropped` counter) — not built this pass, named as a candidate follow-up, not implemented unilaterally.

**6. What does overflow produce: a gap, a permanent offset, catch-up, or something else?** Per §16.2.2's drop-oldest mechanism, repeated across 144 individual drop events, the net effect is a reference stream with **real, discontinuous GAPS** (missing segments) during the overflow interval, converging toward the newest audio — **not** a single fixed constant-offset delay. This distinction matters: an adaptive filter can often track and compensate for a fixed delay, but it cannot correlate against content that was never delivered at all — the skipped intervals are genuinely, unrecoverably missing information from the AEC's point of view.

**7. Connection to the exact 512-sample / 32ms MLS bimodality (§7d) — NO link found; kept separate, per instruction.** `AecReferenceFeeder` has no fixed 512-sample (or any other fixed-size) write anywhere in its source — its writes are exactly whatever size each incoming `TTSAudioRawFrame` carries (§16.2.1, confirmed variable). The MLS bimodality was measured on the CAPTURE side (`arecord` via `r0081_direct_aec_diagnostic.py`, 16kHz), a completely separate code path from this WRITE-side reference feeder (24kHz). No evidence connects the two; they are **not** claimed to share a cause here. The capture-side ALSA period/buffer question (§7d Finding 2) remains open and is answered by §16.5's command, not by this analysis.

### 16.3 Golden (`7dd6b87`) vs. current — same queue architecture, confirmed from the full diff

`git diff 7dd6b87 HEAD -- src/nexa/voice_tts/aec_reference.py` (read in full this session): the **entire** diff is additive — a docstring block, two new imports (`audioop`, `time`), three new optional constructor params (`gain_source`, `on_diagnostic`, `diagnostic_interval_s`, all defaulting to `None`/inert), the `gain_source` scaling block inserted into `process_frame()` before the (unchanged) `self._enqueue(pcm)` call, and the new `_emit_diagnostic()` method (a pure read of `self._queue.qsize()`/`self.chunks_dropped` plus one `audioop.rms()` call — no sleeping, no I/O, does not alter enqueue/dequeue timing).

**`DEFAULT_MAX_QUEUED_CHUNKS = 24`, the `asyncio.Queue(maxsize=...)` construction, `_enqueue()`'s drop-oldest logic, `_run_writer()`'s consumer loop, `_PcmSink`'s single-persistent-`aplay`-via-stdin-pipe mechanism, and `_start_sink`/lifecycle are ALL byte-for-byte unchanged between `7dd6b87` and current HEAD.** In today's current control run, `gain_source=None` (no `--coherent-reference-gain`), so that addition was fully inert too — the ONLY functional difference in play was `on_diagnostic` being wired (so the drops were visible at all).

**Conclusion, precisely scoped: the golden path has the identical queue-overflow VULNERABILITY — CONFIRMED, from source.** Its own session log cannot show `REF_QUEUE_DEPTH`/`REF_DROPPED` (that telemetry didn't exist in `7dd6b87`, and the golden PROBE script — a separate file, `m2_6a_gemini_live_probe.py` — never wires an equivalent hook), but the underlying `AecReferenceFeeder.chunks_dropped` counter and the same bounded queue exist and would behave identically under the same producer-burst conditions. This is a structural finding, not a guess: the class doing the work is, for every mechanism relevant to overflow, textually identical. **What is NOT established: whether the golden path's queue actually DID overflow during its own specific failing session today** — that would require the same telemetry, which the golden probe does not have, and this report does not claim it was measured.

### 16.4 Reconciling with A2's zero-drop false VAD — queue overflow is NOT the sole root cause

A2 (§2) showed `REF_DROPPED = 0`, queue depth mostly 0, and false local VAD STILL occurred. This is decisive: **queue overflow cannot be the sole root cause**, even though today's 144-drop episode is real and material. The evidence is consistent with:

```
baseline residual echo (confirmed, §7/§7b — AEC works but leaves a real residual at every level tested)
+ intermittent reference-queue/scheduling failure (confirmed today, NOT present in A2)
= much worse self-interruption when both are present; baseline-only residual when the queue stays healthy
```

This directly fits the operator's own observation (§15.10): some utterances nearly perfect, others severely broken, in the SAME session/setup — exactly what an intermittent, state/scheduling-dependent SECOND failure mode layered on top of an always-present baseline residual would produce, rather than a simple deterministic "current code always wrong" explanation.

### 16.5 Machine-derived CURRENT timeline correlation — the log already existed, analyzed offline

**Correction to the prior version of this section**: it claimed the interleaved current-run log had not been supplied and asked the operator to provide it. That was wrong — the log was already captured by §15.6's own `tee` command and located on disk at `/tmp/r0081_current_control_20260917T193834.log` (844 lines, captured 2026-09-17 19:38-19:40). Analyzed offline this update; no new hardware/Gemini run.

**16.5.1 First answer's overflow episode and the two temporally-associated false VADs — verified exactly against the real log:**

```
15.012  REF_QUEUE_DEPTH=1   REF_DROPPED=0     <- queue growth begins
15.319  REF_QUEUE_DEPTH=12  REF_DROPPED=0
15.857  REF_QUEUE_DEPTH=20  REF_DROPPED=0
16.118  REF_QUEUE_DEPTH=23  REF_DROPPED=3     <- first observed drop
16.405  REF_QUEUE_DEPTH=24  REF_DROPPED=7
16.929  REF_QUEUE_DEPTH=24  REF_DROPPED=32
17.728  REF_QUEUE_DEPTH=24  REF_DROPPED=89
18.528  REF_QUEUE_DEPTH=24  REF_DROPPED=127   <- last REF telemetry before a ~10.16s gap (see 16.5.2)
25.555  MIC_RMS=76                            <- nearest preceding MIC_RMS before the first false VAD
25.618  LOCAL_VAD_START / INTERRUPTION_FRAME_DOWNSTREAM / PLAYBACK_STOPPED / BOT_AUDIO_STOPPED  <- FIRST false VAD
28.687  REF_QUEUE_DEPTH=0   REF_DROPPED=144   <- queue drained; drop count now fixed for the rest of the session (16.5.4)
29.195  MIC_RMS=48                            <- nearest preceding MIC_RMS before the second false VAD
29.257  LOCAL_VAD_START / INTERRUPTION_FRAME_DOWNSTREAM / PLAYBACK_STOPPED / BOT_AUDIO_STOPPED  <- SECOND false VAD
```

Computed, confirmed exact:

```
queue growth precedes first false VAD by:   25.618 - 15.012 = 10.606 s
first drop precedes first false VAD by:     25.618 - 16.118 =  9.500 s
second false VAD follows queue-drain by:    29.257 - 28.687 =  0.570 s
```

**This rules out, for this specific episode**: "the first false interruption caused the initial queue overflow" (overflow demonstrably began 10.6s before any false VAD in this answer). **It does NOT prove** "queue overflow caused the false VAD" — temporal precedence alone is insufficient, and A2 already establishes false VAD occurs with zero drops (§16.4).

**16.5.2 A new finding: a ~10.16s gap in `REF_RMS`/`REF_QUEUE_DEPTH` telemetry (18.528s → 28.687s).** `_emit_diagnostic()` is called on every `TTSAudioRawFrame` reaching `process_frame()` (throttled to ≥0.25s between PRINTS, not between calls) — for telemetry to go completely silent for over 10 seconds while the assistant is nominally still "responding" (per the `BOT_AUDIO_STOPPED` events bracketing this window) is consistent with no new `TTSAudioRawFrame`s reaching the feeder during that interval — i.e., the assistant's own audio delivery may have stalled/gapped, independent of the queue's own drop mechanism. Not chased further this pass; noted as a second, related data point for §16.6's shared-scheduling hypotheses (a stalled/bursty upstream audio delivery is consistent with both the queue-overflow burst AND this quiet gap).

**16.5.3 Interpreting the 0.570s gap between queue-drain and the second false VAD — three possibilities, not chosen among, per instruction.** The queue's drop-oldest/keep-newest design means draining to `REF_QUEUE_DEPTH=0` means the writer has caught up to the newest queued reference content — **this is not evidence of a permanent fixed time offset**. Candidate, non-exclusive explanations:
1. The earlier gaps disturbed the XVF3800's AEC adaptation, and recovery is not instantaneous — a residual "hangover" effect after the reference stream itself is caught up.
2. Baseline residual echo (§7/§7b, already confirmed present at every level tested) independently triggers VAD shortly after, unrelated to the just-ended overflow.
3. A third, shared scheduling/acoustic mechanism affects both queue health and residual echo around the same window (see §16.5.2 and §16.6).

No evidence in this log distinguishes between these; this report does not choose among them.

**16.5.4 Full-session classification: separating true operator turns from false starts — a PROXY, not ground truth.** No `USER_TRANSCRIPT:` line appears anywhere in this 844-line log (final transcripts never printed this session) — transcript content cannot be used as the discriminator. Instead, this audit uses a source-grounded proxy: whether a `LOCAL_VAD_START` has a **co-occurring `BOT_AUDIO_STOPPED`** (within ~0.001-0.002s) — if the bot's own audio-stopped event fires at the same instant, the bot demonstrably had audio in flight being interrupted (consistent with false self-interruption during active playback); if not, the bot was not actively producing audio at that moment (consistent with a genuine new user turn, most plausibly a real question during an actual silence). **This proxy's own input signal is demonstrably incomplete**: only 2 `BOT_AUDIO_STARTED` events appear in the entire session (§16.5.6) against 14 `BOT_AUDIO_STOPPED` events — the session's very first bot response has no logged `BOT_AUDIO_STARTED` at all. The classification below is therefore reported as **events classified as likely false / likely real using this proxy**, not as an exact, independently-verified ground-truth count — it should not be used to claim precise causal weighting on its own.

All 14 `LOCAL_VAD_START` events in the session, classified this way:

| Timestamp | Co-occurring `BOT_AUDIO_STOPPED`? | Classification |
|---|---|---|
| 2.920s | No (bot had not spoken yet — this is the session's first, genuine user question) | REAL |
| 25.618s | Yes (25.619s) | FALSE — overflow-associated (16.5.1) |
| 29.257s | Yes (29.258s) | FALSE — overflow-associated (16.5.1) |
| 34.698s | Yes (34.698s) | FALSE |
| 39.318s | Yes (39.319s) | FALSE |
| 42.758s | Yes (42.759s) | FALSE |
| 46.859s | Yes (46.860s) | FALSE |
| 72.921s | No | REAL |
| 82.642s | Yes (82.643s) | FALSE |
| 89.402s | Yes (89.403s) | FALSE |
| 107.183s | No | REAL |
| 115.003s | No | REAL |
| 122.444s | No | REAL |
| 127.923s | Yes (127.924s) | FALSE |

**9 events classified as likely false, 5 classified as likely real, using co-occurring `BOT_AUDIO_STOPPED` as the proxy**, in a ~130s session.

**16.5.5 Quantitative reconciliation — only 2 of the 9 proxy-classified false events are temporally associated with the overflow episode.** `REF_DROPPED` was checked across the ENTIRE session (every distinct value that appears): `0, 3, 7, 17, 32, 55, 70, 89, 105, 113, 127, 144` — **it never exceeds 144 anywhere else in the ~130s log**, and `REF_QUEUE_DEPTH` reads `0` at every one of the 7 LATER proxy-classified-false timestamps (34.698s-127.923s), directly checked against the nearest surrounding `REF_RMS` telemetry line for each. Only the FIRST 2 (25.618s, 29.257s — the pair the operator described as "extremely difficult to listen to") are temporally associated with the single, non-recurring overflow episode.

**Precisely stated conclusion** (narrower than a "dominant contributor by count" claim, since the 9/5 split is a proxy, not ground truth): **baseline residual echo is confirmed sufficient for false VAD to occur even when the reference queue is healthy — reference-queue overflow is NOT necessary for the symptom.** The queue-overflow episode is associated with (not proven to cause) the specific cluster the operator described as the most severe listening experience — it is not required to explain the session's false interruptions in general, most of which (by this proxy) occurred with a healthy queue.

**16.5.6 Secondary note (the incompleteness referenced by §16.5.4's proxy caveat)**: no `BOT_AUDIO_STARTED` event appears anywhere before 8.459s (when `REF_RMS` telemetry first shows non-zero activity), despite the assistant clearly having started responding by then — the session's very first `BOT_AUDIO_STARTED` is effectively missing from this log (only two `BOT_AUDIO_STARTED` events appear in the whole 130s session, at 42.764s and 129.723s, against 14 `BOT_AUDIO_STOPPED` events). Not investigated further this pass; flagged as a data-quality anomaly worth a future look, not incorporated into any conclusion above.

### 16.6 Shared-state hypotheses, prioritized (not concluded)

Because true historical (`7dd6b87`, dependency-isolated) and current source both fail today while the measured mixer/device snapshot is equal, shared variables are prioritized over a NeXa-code-only explanation:

1. ALSA scheduling/buffering / USB timing (§16.2.3's burst-delivery mechanism is a concrete, source-grounded candidate here).
2. XVF3800 runtime/adaptation state.
3. Physical/acoustic state (not measured by the mixer snapshot).
4. CPU/executor scheduling/load (the same Pi runs both the Python process and, potentially, contends with other load).
5. Shared third-party dependency/environment state (§15.3 — pipecat/google-genai versions are shared and identical; drift SINCE `7dd6b87` was written is not ruled out).
6. Provider timing/audio chunk cadence — **a specific, named candidate this section adds**: R0071's own report already recorded a **known, unresolved, non-blocking issue**: *"occasional short playback/stream continuity stutter during longer assistant speech"* on the ACCEPTED baseline. Bursty/uneven `TTSAudioRawFrame` delivery from Gemini (§16.2.3) is a plausible SHARED mechanism for both that acoustic stutter AND today's reference-queue overflow — the same underlying phenomenon potentially explaining two previously-separate-looking symptoms. Not proven; flagged as the most concrete, source-grounded lead among the six.

No root cause is concluded from this list — it is a priority order for further investigation, not a verdict.

### 16.7 ALSA capture query RESULT (real hardware, 2026-09-18): active-period hypothesis REFUTED for this negotiated setup

The operator ran the read-only verbose query:

```bash
arecord -D plug:respeaker -f S16_LE -r 16000 -c 1 --dump-hw-params -v -d 1 /dev/null
```

**Actual negotiated `plug:respeaker` capture setup:**

```
stream          : CAPTURE
access          : RW_INTERLEAVED
format          : S16_LE
channels        : 1
rate            : 16000 (exact)
buffer_size     : 8000
period_size     : 2000
period_time     : 125000 us
avail_min       : 2000
start_threshold : 1
stop_threshold  : 8000
```

**Actual physical slave** (`hw:CARD=Array`): `channels: 2`, `rate: 16000`, `buffer_size: 8000`, `period_size: 2000`, `period_time: 125000 us`, `avail_min: 2000` — the `plug:` layer's own negotiated period/buffer match the hardware slave's exactly (no `plug`-level rate/period mismatch to account for).

**Conclusion — REFUTED, not merely "not confirmed," per instruction**: `2000` (period_size) and `8000` (buffer_size) do **not** divide evenly by 512 — `2000 / 512 = 3.90625`, `8000 / 512 = 15.625`. **The specific hypothesis "the exact 512-sample/32ms MLS split is directly caused by the active ALSA capture period/buffer boundary" is REFUTED for this negotiated capture setup.** This does **not** mean the MLS bimodality itself is invalidated — §7d's measurement (2 trials at ~134.4ms, 1 at ~102.5ms, an exact 512-sample separation) stands as a real, reproduced observation; only this one specific explanation for its SOURCE has been tested and ruled out.

**Scope check performed before finalizing this conclusion, per instruction — searched source rather than assumed:** audited `docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py` (the script that produced the MLS measurement) and its reused helpers in `m2_6b4m_self_echo_probe.py` for any application-level fixed block size:

- `capture_pcm()` invokes `arecord -q -t raw -f S16_LE -r 16000 -c 1 -D plug:respeaker -d <duration>` — **same device, same 16kHz/S16_LE/mono configuration** as the query above, and passes **no** explicit `--period-size`/`--buffer-size` (so it was subject to the exact same ALSA-negotiated defaults just measured).
- `_run_subprocess()` reads the captured PCM via `await proc.communicate()` — reads the **entire** subprocess stdout to EOF in one call; no fixed-size chunked read, no 1024/2048-byte read loop, at the Python level.
- `run_trial()`'s only slicing constants are `PRE_ROLL_S=1.0`/`TAIL_MARGIN_S=1.0` (whole seconds, not a fixed sample-block size).
- `cross_correlate_pcm()` (in `m2_6b4m_self_echo_probe.py`, reused not reimplemented) operates on the whole PCM arrays via numpy slicing by lag, not by any fixed 512/1024/2048-sample block.
- Full-file grep of both files for `512`, `1024`, `2048` found **zero** matches referring to any capture/read/chunk size (the only `512`/`period` matches in the diagnostic script are in its own docstring discussing this exact still-open question, and the MLS generator's own `_MLS16_PERIOD = 65535`, unrelated).

**No 512-sample (or 1024/2048-byte, or 32ms-derived) application-level block exists anywhere in this capture/analysis path.** Recorded exactly as instructed:

```
exact 512-sample MLS split:  REAL OBSERVATION
source:                      UNRESOLVED
ALSA active-period explanation: REFUTED
```

**New ALSA fact, read-only, not causally linked to the 32ms split**: the query's `plug` conversion reports `Route conversion PCM: 0 <- 0*0.5 + 1*0.5` — the mono `plug:respeaker` capture is a 50/50 mix of the physical device's 2 raw capture channels, not a single untouched hardware channel. Per §6's own already-established `AEC_ASROUTONOFF=1` finding ("each channel is associated with a beam from the beamformer"), these 2 channels are two DIFFERENT ASR-processed beamformer beams, not e.g. left/right stereo mic pickup — a fact not previously stated this precisely in `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` (which records only "2 channels (stereo)"). This is noted as a plausible contributing factor to the spectral reshaping/correlation-magnitude finding already documented in §7d Finding 3 (the application is not observing one untouched physical channel) — **it is explicitly NOT claimed to cause the 32ms lag split itself**, per instruction.

### 16.8 Next action taken (2026-09-18) — this recommendation was executed; result in §16.9

**Do not spend another live test chasing the 512-sample value** — the specific active-ALSA-period hypothesis is refuted, and this offline audit produced no new concrete testable candidate for its source. The next highest-value hardware experiment was the already-built, genuinely counterbalanced gain confirmation (§7c's `gain=0.5` sweep finding still needed order-independent confirmation before it could be called anything more than a candidate). The operator ran it, with the additional `--post-settle-gap 0.5` opt-in:

```bash
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
  --confirm \
  --post-settle-gap 0.5
```

Real result and interpretation: §16.9. A much larger, unanticipated finding — a strong sequence/time effect exceeding the gain effect under test — required an offline lifecycle audit (§16.10) and a new, cleaner script mode (§16.12), whose own first real-hardware result (§16.14) now justifies a second-stage silence-interval test (§16.15) as the next hardware step (§16.16).

---

## 16.9 Counterbalanced gain confirmation RESULT: `gain=0.5` NOT confirmed; a far larger sequence/time effect found

**Protocol, exactly as run**: A=gain 0.5, B=gain 1.0, amplitude=0.05, settle before EVERY block=3.0s, post-settle silence=0.5s, 0% clipping everywhere, quiet-floor ~7 RMS throughout. Sequence 1 = ABABAB, sequence 2 = BABABA (3 cycles each, per §7c's own counterbalancing design).

**A-first sequence:**

| | cycle1 | cycle2 | cycle3 | mean |
|---|---|---|---|---|
| gain0.5 (A) | 30.8 | 30.5 | 29.0 | 30.10 |
| gain1.0 (B) | 30.2 | 29.0 | 28.5 | 29.23 |
| A−B | +0.60 | +1.50 | +0.50 | |

`gain1.0` beat `gain0.5` in **3/3 cycles** in this sequence.

**B-first sequence:**

| | cycle1 | cycle2 | cycle3 | mean |
|---|---|---|---|---|
| gain1.0 (B) | 23.2 | 14.3 | 14.4 | 17.30 |
| gain0.5 (A) | 21.6 | 20.6 | 16.0 | 19.40 |
| A−B | −1.60 | +6.30 | +1.60 | |

`gain0.5` beat `gain1.0` in only **1/3 cycles** in this sequence.

**Combined**: `gain0.5` mean = 24.75, `gain1.0` mean = 23.27 — `gain0.5` residual is **~6.36% / ~0.54dB higher (worse)** than `gain1.0` in the pooled data. All figures verified exactly by independent recomputation this update (percentage/dB conversions match to the reported precision).

**Primary conclusion, per §15.2's own decision criteria**: the original ascending sweep's apparent ~30% `gain=0.5` advantage (§7c) **does NOT reproduce** under the counterbalanced/long-settle/post-gap protocol — `gain1.0` won 3/3 in one sequence and lost 2/3 in the other, a mixed/reversed result, not a consistent order-independent win for either gain.

```
gain=0.5 as preferred calibration        NOT CONFIRMED
gain=0.5 live conversational candidate   REJECTED / DE-PRIORITIZED
production gain=1.0                      KEEP UNCHANGED
reference gain mismatch as primary cause NOT SUPPORTED by current evidence
```

**Precisely scoped, per instruction**: this does **not** prove `gain=1.0` is mathematically optimal globally — only that this controlled confirmation found no evidence that changing production gain from 1.0 to 0.5 improves cancellation, and the earlier sweep's apparent advantage failed replication. No Gemini/conversational gain=0.5 test was run or is recommended from this evidence.

**The far more important finding — a large sequence/time effect, exceeding the gain effect under test:**

| | gain0.5 mean | gain1.0 mean |
|---|---|---|
| Sequence 1 (first half, earlier in the run) | 30.10 | 29.23 |
| Sequence 2 (second half, later in the run) | 19.40 | 17.30 |
| Change | −35.5% / −3.82dB | −40.8% / −4.56dB |

While: quiet floor stayed essentially flat (A group 7.20→7.20; B group 7.27→7.03), speaker PCM was identical throughout (1142.5 RMS / 1638 peak, fixed amplitude), and clipping was 0% everywhere. **This between-sequence residual drop (~35-41%) is far larger than the ~6% gain0.5-vs-gain1.0 difference this run was designed to measure**, and it cannot be explained by room noise (quiet floor did not materially change) or by acoustic stimulus changes (identical throughout). This is recorded as a **major new lead**, not attributed to any single mechanism without further evidence (§16.10).

**Note on stimulus, per instruction**: this run used `--stimulus tones` (the default) — `best_lag_ms`/correlation values from it are known-unreliable (periodic-tone ambiguity, §7d) and are **not** used anywhere in this section's or §16.10's conclusions. Only RMS/peak/quiet-floor measurements are used for the state analysis here.

---

## 16.10 Offline lifecycle/chronology audit — answered from source, before proposing a next hardware step

Performed before recommending any further hardware run, per instruction. All of the following is read directly from `r0081_direct_aec_diagnostic.py`'s own source (`run_trial`, `run_condition`, `play_pcm`/`_run_subprocess`), not assumed.

**1-2. Wall-clock time and cumulative reference exposure per block (computed, not measured — the script's own durations are fixed/deterministic).** Each `--confirm` block = settle (3.0s, speaker+reference concurrent) + post-settle gap (0.5s, silent) + one measured trial (capture runs a fixed `PRE_ROLL_S(1.0) + duration_s(3.0) + TAIL_MARGIN_S(1.0) = 5.0s`, dominating the trial's own wall time) ≈ **8.5s per block** (plus small, unmeasured subprocess-spawn overhead). Reference PCM is fed to `plug:respeaker` during BOTH the block's settle (3.0s) and its own trial playback (3.0s) — **6.0s of reference exposure per block**, regardless of A or B.

**Corrected elapsed-time column**: the previous version of this table reported timestamps that could not be reconciled with the block durations above (e.g. block 1 at "~4s" when the block itself takes ~8.5s to complete). This version reports **elapsed time when each block's own measured result becomes available** — i.e. the cumulative sum of each prior block's own ~8.5s, including the block's own — since that is the timestamp actually meaningful for "when was this RMS value measured," not a reconstructed mid-block estimate. Reconstructed, block-by-block, in chronological run order:

| Block | Sequence | Gain | `mic_window_rms` | Cumulative ref. exposure (s) | Elapsed at block-end (s, computed from ~8.5s/block) |
|---|---|---|---|---|---|
| 1 | 1 (A-first) | A (0.5) | 30.8 | 6 | ~8.5 |
| 2 | 1 | B (1.0) | 30.2 | 12 | ~17.0 |
| 3 | 1 | A | 30.5 | 18 | ~25.5 |
| 4 | 1 | B | 29.0 | 24 | ~34.0 |
| 5 | 1 | A | 29.0 | 30 | ~42.5 |
| 6 | 1 | B | 28.5 | 36 | ~51.0 |
| 7 | 2 (B-first) | B | 23.2 | 42 | ~59.5 |
| 8 | 2 | A | 21.6 | 48 | ~68.0 |
| 9 | 2 | B | 14.3 | 54 | ~76.5 |
| 10 | 2 | A | 20.6 | 60 | ~85.0 |
| 11 | 2 | B | 14.4 | 66 | ~93.5 |
| 12 | 2 | A | 16.0 | 72 | ~102.0 |

**These are still reconstructed approximations** (± subprocess-spawn overhead, not directly measured) — the elapsed-time/cumulative-exposure columns in the new `--track`/`--track-continuous` modes (§16.11) report REAL timestamps (`time.monotonic()`) instead, which should be preferred for the next experiment rather than this kind of after-the-fact reconstruction.

The overall envelope trends from ~30 (block 1) down to ~14-16 (blocks 9-12) as elapsed time/cumulative exposure increases, but **not perfectly monotonically** — block 9→10 rises (14.3→20.6) and block 11→12 rises again (14.4→16.0). A real downward trend with real non-monotonic noise, not a clean curve either way.

**3. `aplay`/`arecord` subprocess recreation — CONFIRMED, uniform across every block, unlike production.** `play_pcm()` spawns a **brand-new** `aplay` process via `asyncio.create_subprocess_exec` on **every single call** — once for the speaker and once for the reference, for BOTH the settle step and the measured trial, in every block. `capture_pcm()` likewise spawns a fresh `arecord` per trial. There is **no persistent `aplay`/`arecord` process anywhere in this diagnostic script** — a fundamentally different lifecycle from production's `AecReferenceFeeder`, which uses exactly ONE persistent `aplay` for the whole session (§16.2). This pattern is **uniform** across all 12 blocks (settle and trial alike) — it does not itself differ between early and late blocks, so it cannot by itself explain WHY the residual changed over the run, but it does mean the reference ALSA stream was opened and closed roughly 24 times (2 per block: settle + trial) over the course of this one `--confirm` run. **Correction**: each of these 24 "exposure" windows is itself interrupted by silence — every block contains 3.0s settle/reference, then 0.5s explicit silence, then a 1.0s capture pre-roll with NO reference playing, then 3.0s measured reference/playback, then a 1.0s capture tail — so "24 opens" does not mean 24 seconds of uninterrupted signal; each open/close cycle is itself bounded by silence on both sides.

**4. Whether hardware AEC state persists across those process recreations — NOT determinable from software source.** This is a hardware/firmware question this audit cannot answer by reading Python. It is plausible (many embedded AEC implementations run their adaptive filter in onboard DSP firmware, independent of the host-side ALSA stream lifecycle, so coefficients could in principle survive a stream close/reopen) but this is **not confirmed** — recorded as a genuine open question, not assumed either way.

**5. Nothing is explicitly reset between sequence 1 and sequence 2.** `_run_confirm()` calls `_run_confirm_sequence()` for seq1 then seq2 back to back, with no settle/pause/reset step at the sequence boundary beyond block 7's own normal 3.0s settle (same as every other block). Confirmed by direct source read: no `xvf_host.py` invocation, no ALSA mixer write, no device close/reopen beyond the per-block `aplay`/`arecord` pattern already described in item 3, anywhere in this script.

**6. Sequence-boundary consecutive-B exposure — CONFIRMED, a real design asymmetry, precisely stated.** Sequence 1 ends with block 6 (B, gain=1.0); sequence 2 begins with block 7 (B, gain=1.0) — two consecutive gain=1.0 blocks occur exactly at the sequence boundary. **Precisely, per instruction**: this is **12 seconds of cumulative gain=1.0 reference exposure across two consecutive gain=1.0 blocks** — NOT 12 seconds of continuous, uninterrupted reference (item 3's silence/pre-roll/tail structure applies here too: block 6's own settle+trial reference windows are each separated from each other and from block 7's by the same silence/gap/pre-roll boundaries every block has). The silence/reopen boundaries themselves may matter and are not excluded as a factor. This does not undermine the overall counterbalancing (6 A blocks and 6 B blocks total, evenly split which sequence goes first), but it is a real, identifiable structural asymmetry at the transition point, distinct from the smoothly alternating pattern within each sequence.

**7-8. No difference in subprocess-creation pattern between blocks; no explicit AEC reset found anywhere.** Confirmed by the same source read as items 3 and 5 — the mechanism is uniform throughout, and this script (like every other tool built in this investigation) never writes to any XVF3800 register or ALSA mixer control.

**Conclusion of this audit, precisely scoped**: this audit proves only that the diagnostic script's own Python-level control flow is structurally UNIFORM block to block (identical subprocess-recreation pattern, no explicit reset, every block). **It does NOT exclude, and does not rank, any of the following as the layer actually responsible**: ALSA scheduling, USB scheduling, subprocess-startup timing, host CPU scheduling/load, ALSA driver state, stream-open/close effects, XVF3800 DSP state, or physical/acoustic state. A real time/state-dependent effect is observed; **its layer is unresolved across host/ALSA/USB/DSP/acoustic state** — this report does not claim the XVF3800 hardware itself is the most likely layer over the others. See §16.11 for the next experiment, designed specifically to reduce (not eliminate) several of these candidate layers by removing stream recreation and gaps from inside the measured window.

## 16.11 `--track` (built previously) — kept as a SECONDARY, restart-based test; NOT the highest-value next step

`--track` (§16.9's own follow-up, built last update) removes gain switching but **still recreates every `aplay`/`arecord` subprocess, plays a fresh 3.0s settle, and inserts a silent gap for EVERY measured trial** — confirmed directly by §16.10's own audit (it reuses `run_condition`/`run_trial`, exactly like `--confirm` did). This means `--track` does **not** cleanly isolate simple continuous AEC exposure/adaptation from repeated stream open/close and silence injection — it removes ONE confound (gain switching) while leaving another (stream/silence recreation) fully in place. **`--track` is not deleted and remains available as a secondary, restart-based test** — useful in its own right (e.g. to check whether repeated restarts themselves matter), but it is superseded as the IMMEDIATE next step by §16.12's continuous-stream design, which removes the stream/silence confound too.

## 16.12 New script mode built: `--track-continuous` — continuous-stream fixed-gain adaptation diagnostic (the actual next step)

Built this update, after §16.10's audit identified `--track`'s own remaining confound. `r0081_direct_aec_diagnostic.py` gains `--track-continuous` (mutually exclusive with `--sweep`/`--confirm`/`--track`, same `--amplitude`-defaults-to-0.05 convention): fixed `--track-gain` (default 1.0), **NO gain switching, ONE capture stream (`arecord`), ONE speaker playback stream (`aplay`), ONE reference playback stream (`aplay`) for the ENTIRE run — no stream recreation, no silent gap, anywhere inside the measured window.**

**Design**: builds ONE `--track-continuous-cycle-s`-long deterministic segment (default 3.0s, via the existing `build_signal`) exactly once, then repeats it byte-identically `--track-continuous-cycles` times (default 10 → 30s continuous playback) via plain concatenation (`build_continuous_signal`) — every cycle's acoustic source content is therefore identical, so any measured RMS difference between cycles reflects only capture-side/system-state change, not stimulus difference. A single fixed pre-roll (1.0s, reusing `PRE_ROLL_S`) precedes playback; a single fixed tail (1.0s, `TAIL_MARGIN_S`) follows it. `run_continuous()` starts the ONE `arecord` capture task, waits the pre-roll, starts the ONE speaker `aplay` and ONE reference `aplay` concurrently (both playing the full continuous signal, reference gain-scaled), waits for the whole continuous capture to complete, then **slices the single resulting PCM into the 10 corresponding 3s cycle windows OFFLINE** — only the initial stream startup (before cycle 1) is outside the continuously-measured exposure. Reports, per cycle: cycle number, `cycle_start_s`, `cycle_end_s`, cumulative continuous reference exposure, `mic_window_rms`, `mic_window_peak` — plus `quiet_before_rms`, first-half/second-half means, and min/max. No correlation/lag conclusion is drawn (the default `tones` stimulus remains periodic-ambiguous per §7d) — RMS/state trend is the whole purpose.

**Two diagnostic-quality corrections made before the first hardware run, per review** (neither changes the PCM slicing itself, only field names and filenames):

1. **Elapsed-time semantics fixed.** The offline stub output showed cycle 1 as `elapsed=0.0`, revealing the field was actually reporting the cycle's START offset, not elapsed time once that cycle's own measurement had completed. Renamed `elapsed_playback_s`/`source_offset_s` to explicit, unambiguous `cycle_start_s` and `cycle_end_s` (e.g. cycle 1: start=0.0, end=3.0; cycle 2: start=3.0, end=6.0), alongside the existing `cumulative_reference_exposure_s`. Table header and printed columns updated to match. **Offline test added/adjusted**: a synthetic 4-cycle capture with known, distinct constant per-cycle amplitudes confirmed `cycle_start_s`/`cycle_end_s`/`cumulative_reference_exposure_s` compute exactly `[0,1,2,3]`/`[1,2,3,4]`/`[1,2,3,4]` (1s cycles) with zero error, alongside the RMS-recovery check already in place.
2. **WAV-overwrite risk fixed.** The previous filename (`track_continuous_gain{g}_amp{a}_{stimulus}_{signal|mic}.wav`) had no run-to-run differentiator — a second identical invocation would silently overwrite the first run's raw evidence. Added a run id (`datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")`, a real timestamp, not random, matching this investigation's existing evidence-naming convention) generated once per invocation and shared by both the signal and mic WAV from that invocation; printed prominently in the output (both near the top and again alongside the "saved:" lines). Historical WAV names from prior runs are untouched — this only changes the filename template for future `--track-continuous` invocations. **Offline test added**: two simulated invocations one second apart produced fully disjoint filename sets, confirming the fix works as intended.

**Verified offline this update, no hardware touched**: `py_compile`/`ruff check` clean; argparse mutual-exclusivity (`--track-continuous` vs. `--sweep`/`--confirm`/`--track`) and default resolution (`--amplitude` → 0.05) confirmed; `build_continuous_signal` confirmed to produce byte-identical repeated cycles (direct equality check against 3 independently-sliced copies); a full stubbed end-to-end run against `run_continuous` with a synthetic captured PCM carrying 4 cycles of known, distinct constant amplitudes confirmed **exactly 1 `capture_pcm` call and exactly 2 `play_pcm` calls (speaker + reference) for the whole run** — satisfying the "exactly one capture subprocess and one speaker/reference playback subprocess per continuous run" validation requirement directly — confirmed the offline window-slicing recovers the exact known per-cycle RMS values with zero error, and confirmed `cycle_start_s`/`cycle_end_s`/`cumulative_reference_exposure_s` arithmetic is exact; a full stubbed run of the printing/summary path (`_run_track_continuous`) completed without error and confirmed distinct filenames across two simulated invocations. No `src/`, `apps/`, or production `tests/` file touched; no DSP/ALSA configuration touched.

## 16.13 Decision logic (restated exactly as instructed)

- **Progressive cycle-to-cycle decrease**: continuous exposure / AEC adaptation effect is STRONGLY SUPPORTED. Do not yet claim which internal XVF3800 algorithm causes it.
- **Flat residual**: pure continuous warm-up is NOT supported; the `--confirm` sequence drop more likely depends on stream lifecycle, gain switching, intermittent state, or another boundary effect (all still live candidates per §16.10's own unranked list).
- **Sharp non-monotonic jumps**: intermittent timing/state instability becomes the leading class.
- **One step-change followed by a new stable level**: report the exact cycle/time it occurs at — this is a real, distinct finding; **do not describe it as monotonic warm-up**.

## 16.14 First `--track-continuous` RESULT (real hardware, 2026-09-18, run id `20260918T074722Z`)

Protocol exactly as designed (§16.12): `gain=1.0` fixed, `amplitude=0.05`, `stimulus=tones`, ONE capture stream, ONE speaker playback stream, ONE reference playback stream, 10 byte-identical 3s cycles = 30s continuous playback/reference, no stream recreation, no silent gaps between cycles, no Gemini, no VAD, 0% clipping. `quiet_before_rms = 15.3`.

| Cycle | Window | `mic_window_rms` | `mic_window_peak` |
|---|---|---|---|
| 1 | 0-3s | 39.0 | 248 |
| 2 | 3-6s | 38.5 | 132 |
| 3 | 6-9s | 25.8 | 107 |
| 4 | 9-12s | 11.1 | 66 |
| 5 | 12-15s | 8.4 | 52 |
| 6 | 15-18s | 24.7 | 145 |
| 7 | 18-21s | 15.4 | 83 |
| 8 | 21-24s | 24.5 | 157 |
| 9 | 24-27s | 10.9 | 62 |
| 10 | 27-30s | 13.3 | 65 |

First-half mean = 24.56, second-half mean = 17.76 (**−27.7% / ≈−2.8dB**, verified exactly this update); min=8.4 (cycle 5), max=39.0 (cycle 1) — **≈4.64× / ≈13.3dB span** within this one uninterrupted stream (also verified exactly).

**Interpretation, per §16.13's own decision logic**: this is **not** a flat run, and **not** a simple monotonic warm-up curve. Cycles 1-5 show a strong, consistent improvement (39.0→38.5→25.8→11.1→8.4), but cycle 6 then degrades sharply — **without any stream restart** — back to 24.7, followed by another improvement (15.4), another degradation (24.5), then two more low cycles (10.9, 13.3). A single step-change-to-a-new-stable-level (§16.13's third bullet) is explicitly ruled out by this shape — the residual does not settle at a new level, it oscillates.

```
Continuous exposure produces a large state-dependent residual change:   CONFIRMED
Simple monotonic warm-up model:                                         NOT SUFFICIENT
Stream reopen/recreation REQUIRED for large residual changes:           REFUTED for this run
Large residual deterioration can occur inside ONE uninterrupted stream: CONFIRMED
Intermittent timing/state/DSP/AEC instability:                          STRONGLY SUPPORTED as a class
```

**The exact responsible layer is NOT assigned to XVF3800 DSP specifically.** §16.10's own unranked candidate list still applies in full: XVF3800 adaptive/AEC internal state, reference/acoustic relative alignment, USB scheduling, ALSA timing, driver state, host scheduling, or another physical/acoustic time-varying state. This continuous design removes stream-reopen boundaries from INSIDE the run (confirmed: the oscillation happens with zero stream recreation), but it does not, on its own, distinguish between these remaining candidate layers.

**Quiet-floor caveat, recorded as a caveat, not an explanation**: this run's `quiet_before_rms = 15.3` is materially higher than the ~7 RMS floors seen during the earlier counterbalanced confirmation (§16.9) — **absolute RMS values must not be directly compared across these two experiments** as though baseline acoustic conditions were identical. This does **not** explain the within-run 39.0→8.4→24.7→10.9 oscillation, since all 10 cycles belong to the same uninterrupted run, use byte-identical source content, and share whatever quiet-floor condition was present for that one run.

**Consequence for R0081, precisely scoped**: the operator's own subjective production observation (some answers nearly perfect, others severely broken, in the same session/setup) now has a deterministic, non-speech, non-VAD hardware analogue — same stream, same gain, same signal, same devices, same physical setup, yet residual cancellation effectiveness varies by up to ~13.3dB over 30 seconds with zero external change. This materially strengthens the case that live false-self-interruption severity can depend on time/state rather than a deterministic Core/Memory or gain setting. **Not yet claimed**: that this proves the exact live conversational failure mechanism — the stimulus here is deterministic tones, not speech, and Silero/VAD is not exercised by this test at all.

## 16.15 Second-stage experiment — NOW JUSTIFIED by §16.14's result; design and interpretation documented before handoff

The previously-deferred second-stage test is now justified by §16.14's real result: does the good/bad state persist across stream closure and a controlled silence interval, or reset, or remain irregular? **No production/script redesign** — this reuses the exact same `--track-continuous` command a second time, separated by 30 seconds of defined silence.

**Protocol for the operator**: run the identical command once, wait 30 seconds of complete silence (no physical volume change, no mic/speaker movement, no mixer change, no USB reconnect, no process touching the reference device during the silence), then run the identical command again. Preserve both timestamped WAV pairs (the run-id fix from the prior update already guarantees this — the two runs will have different run ids and will not overwrite each other).

**Interpretation, documented BEFORE the operator runs it, per instruction**:

- **Case A — second run starts low** (e.g. cycles near 10-12 from the start): supports persistence of the improved state across stream closure / 30s silence.
- **Case B — second run resets high** (e.g. cycles near 35-38 from the start, similar to run 1's own opening): supports reset/decay of the improved state during closure/silence.
- **Case C — second run begins or remains highly irregular** (e.g. an unpredictable mix like 14, 30, 11, 27 with no clear opening level): strengthens intermittent state/timing instability over a simple warm-up/reset model — consistent with what run 1 itself already showed after cycle 5.

**Do not infer persistence/reset from cycle 1 alone** — cycle 1 may contain a startup transient. Compare at least cycles 1-3 together, and the overall shape of the full 10-cycle run, against run 1's own shape (§16.14) before concluding which case applies.

## 16.16 Next action

**Exactly one operator procedure**: 30 seconds of controlled silence, then one identical `--track-continuous` command.

```bash
# 1. Wait 30 seconds of complete silence (no volume/position/mixer/USB change,
#    nothing touching the reference device) after the first run already completed.
# 2. Then run the identical command again:
python3 docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py \
  --track-continuous --track-gain 1.0 \
  --track-continuous-cycle-s 3.0 --track-continuous-cycles 10
```

**Return the full output** (per-cycle table, run id, first-half/second-half means, min/max) from this second run. Interpret per §16.15's Case A/B/C framework, comparing against §16.14's own first-run shape. This does not use Gemini, VAD, or touch production code/DSP registers/Silero/gain.

---

## 17. Commit gate

Root cause is not confirmed. `coherent_reference_gain` is confirmed NOT validated (and stays off by default). No fix is claimed or implemented — `gain=0.5` is **no longer the active candidate** (rejected on replication failure, §16.9); the reference-queue overflow (§16) and the newly-found sequence/time effect (§16.9-16.10) are both confirmed, material, source-grounded leads, neither yet a proven root cause. AEC is confirmed functioning at two signal levels but a substantial residual remains at every level tested; a clean, non-clipped test cleanly REFUTES blunt ×10 software compensation for the firmware's confirmed `AEC_FAR_EXTGAIN=-20dB`.

**This update's own correction and completion**: the previous update's §16.5 incorrectly claimed the current run's interleaved timestamped log had not been supplied. It had — the log from §15.6's own `tee` command was already on disk (`/tmp/r0081_current_control_20260917T193834.log`). Analyzed it offline this update (no new hardware/Gemini run): confirmed the operator's own summarized queue-growth/drop-count numbers exactly against the raw log, computed the precedence timing (queue growth precedes the first false VAD in that answer by 10.6s; the first drop by 9.5s — ruling out "the first interruption caused the overflow" for that episode, not proving the reverse), and classified all 14 `LOCAL_VAD_START` events session-wide (via a co-occurring-`BOT_AUDIO_STOPPED` proxy, since no `USER_TRANSCRIPT` line exists anywhere in this log) into 9 false / 5 real. Found that only 2 of the 9 false interruptions are temporally associated with the single, non-recurring overflow episode — `REF_DROPPED` never exceeds 144 for the rest of the ~130s session, and the other 7 false interruptions all occur with `REF_QUEUE_DEPTH=0`, matching A2's own zero-drop pattern exactly. Two wording corrections applied throughout, per explicit instruction: the speaker-path claim is now scoped to what source actually proves (continues independently of reference-enqueue success — not a claim the speaker plays perfectly, given R0071's own known stutter issue), and the `[Tu es beau.]` false-transcription claim no longer asserts absolute provenance (strong evidence of acoustic leakage highly consistent with self-echo, not mathematical proof the leaked content was NeXa's own voice). The pipe-buffer-size speculation was removed from the causal argument (not needed to establish the overflow mechanism). No source file changed this update.

**This update (2026-09-18)**: incorporated the operator's real-hardware result from the already-prepared, read-only verbose ALSA query (§16.7) — the active-ALSA-capture-period explanation for §7d's exact 512-sample MLS lag split is REFUTED (actual `period_size=2000`/`buffer_size=8000`, neither divides by 512), stated as a refutation, not weakened to "not confirmed," per instruction; the MLS bimodality itself remains a real, unresolved-source observation. A source scope-check of `r0081_direct_aec_diagnostic.py` and its reused helpers found no application-level 512/1024/2048-sized block anywhere in the capture/analysis path. Documented the new, read-only `plug:respeaker` mono route-mix fact (50/50 of 2 ASR-beamformed hardware channels) without claiming it causes the lag split. Corrected two prior overclaims per explicit instruction: (1) the queue-overflow-precedes-false-VAD finding now explicitly states it does NOT refute overflow as a later contributing factor, only the specific "first interruption caused the initial overflow" claim; (2) the 9-false/5-real session classification is now labeled throughout as a `BOT_AUDIO_STOPPED`-co-occurrence PROXY (with its own input signal confirmed incomplete), not ground truth, and "dominant contributor by count" was replaced with the narrower "baseline residual echo is confirmed sufficient for false VAD even when the queue is healthy; overflow is not necessary for the symptom." §16.8 now names the counterbalanced `--confirm` run as the single next recommended hardware step, since the 512-sample lead produced no new testable candidate.

**This update (2026-09-18, continued)**: incorporated the operator's real-hardware counterbalanced gain confirmation result (`--confirm --post-settle-gap 0.5`, §16.9) — `gain=0.5` FAILED replication (3/3 loss in A-first, 1/3 win in B-first; combined ~6.36%/~0.54dB worse than gain=1.0), rejecting it as a live conversational candidate and keeping production `gain=1.0` unchanged, without claiming `gain=1.0` is proven globally optimal. Found and prioritized a much larger, unanticipated finding: a ~35-41% residual drop between the run's first and second half with quiet floor and acoustic stimulus held fixed — performed the requested offline lifecycle audit BEFORE proposing any new hardware step (§16.10: confirmed every `aplay`/`arecord` subprocess is freshly spawned per settle/trial with no persistence anywhere in this script, unlike production; confirmed no explicit AEC reset exists anywhere in source; confirmed whether XVF3800 hardware state survives ALSA stream re-opens is not determinable from software alone; found a real sequence-boundary asymmetry — two consecutive gain=1.0 blocks at the seq1→seq2 transition; built a chronological cumulative-reference-exposure table). Built `--track` (§16.11, new script mode, `py_compile`/`ruff check` clean, argparse and a full stubbed end-to-end run verified offline, no hardware touched) — same-gain repeated trials with gain switching removed entirely, to isolate elapsed time/exposure from the gain-order interaction. No production code touched.

**This update (2026-09-18, review corrections — no new hardware run)**: corrected several methodological/reporting problems review found before permitting the next live run. (1) Removed the overclaim that the original sweep's advantage was "an artifact of its own confounded design" — the cause is now recorded as UNRESOLVED (order/settle confounding and the newly-found time/state effect are both plausible, not assigned) — and fixed the stale §17 wording that still called `gain=0.5` "a candidate." (2) Corrected the §16.10 elapsed-time table: the previous per-block timestamps could not be reconciled with the block durations; replaced with elapsed-time-at-block-completion (cumulative ~8.5s/block: 8.5, 17.0, 25.5, ... 102.0s), explicitly labeled as still-reconstructed approximations, with a note that `--track`/`--track-continuous` report REAL `time.monotonic()` timestamps instead. (3) Corrected "12 seconds continuous gain1 exposure" at the sequence boundary to "12 seconds of CUMULATIVE gain1 reference exposure across two consecutive gain1 blocks" — each block's own settle/silence/pre-roll/tail structure means this was never 12 uninterrupted seconds, and the silence/reopen boundaries themselves are not excluded as a factor. (4) Removed the over-attribution to "the XVF3800 hardware itself" as most consistent with the sequence/time effect — the source audit proves only that Python control flow is uniform block-to-block; it does not exclude or rank host/ALSA/USB scheduling, driver/stream state, DSP state, or acoustic state against each other. (5) Explicitly documented `--track`'s own remaining confound (still recreates every subprocess/settle/gap per trial) and retitled it a SECONDARY, restart-based test, kept but not deleted. (6) Built `--track-continuous` (§16.12) — a true continuous-stream diagnostic (ONE capture stream, ONE speaker stream, ONE reference stream for the whole run, no stream recreation or gap anywhere inside the measured window) — as the actual next recommended hardware step, verified offline (`py_compile`/`ruff check` clean, argparse defaults/exclusivity confirmed, `build_continuous_signal` confirmed byte-identical per cycle, and a full stubbed run against a synthetic 4-cycle known-RMS capture confirmed exactly 1 capture + 2 playback subprocesses for the whole run and exact offline window-slicing recovery — no hardware touched). Added decision logic (§16.13) and explicitly deferred the second-stage silence-interval test (§16.14, not built, only if justified by the first continuous result).

**This update (2026-09-18, two small diagnostic-quality corrections before the first `--track-continuous` hardware run, per review — still no hardware run)**: (1) renamed the ambiguous `elapsed_playback_s`/`source_offset_s` fields to explicit `cycle_start_s`/`cycle_end_s` (§16.12) — PCM slicing itself unchanged, reporting only; added an offline test confirming the exact arithmetic. (2) Added a per-invocation run id (`datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")`) shared by both the signal and mic WAV from one run, preventing a second identical `--track-continuous` invocation from silently overwriting the first run's raw evidence; printed prominently in the output; added an offline test confirming two invocations produce disjoint filenames. Neither correction touches `src/`/`apps/`/`tests/`, DSP, ALSA, or production gain.

**This update (2026-09-18, first real `--track-continuous` result)**: recorded the operator's real-hardware first continuous run (run id `20260918T074722Z`) — a large (max/min ≈4.64×/≈13.3dB), non-monotonic residual oscillation CONFIRMED inside ONE uninterrupted capture/speaker/reference stream, with zero stream recreation. This refutes "stream reopen is required for large residual variation" and rules out a simple monotonic warm-up model; intermittent timing/state/DSP/AEC instability is now STRONGLY SUPPORTED as a class, with the exact responsible layer still OPEN (not assigned to XVF3800 DSP specifically). Recorded the cross-run quiet-floor difference (15.3 vs. ~7 RMS) as a caveat against absolute-RMS comparison between experiments, explicitly not as an explanation for the in-run oscillation. Documented the consequence for R0081 precisely: this gives the operator's own subjective "some answers perfect, others broken" observation a deterministic, non-speech hardware analogue, without yet claiming it proves the exact live conversational mechanism. Designed and documented the second-stage 30-second-silence-interval experiment's Case A/B/C interpretation BEFORE handoff, per instruction, reusing the exact same command — no script/production changes needed for this next step. No new code, no hardware run, this update.

Per this project's established practice for this exact situation: this update's change is the R0081 report only — no `src/`/`apps/`/`tests/`/diagnostic-script file touched. **R0081 is NOT marked PASS. No DSP/firmware/ALSA configuration was changed on the live system, `CoherentReferenceGain` was not enabled, Silero was not touched, production reference gain was not changed, and no Gemini conversation was run this update** — no hardware was run by me this update; the real-hardware result incorporated here was already explained (§16.13's decision logic) before being run and was performed by the operator.
