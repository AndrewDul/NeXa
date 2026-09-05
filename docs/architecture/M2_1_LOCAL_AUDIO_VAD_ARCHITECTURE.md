# M2.1 — Local Audio + Silero VAD Foundation (verified architecture)

Status: **`VERIFIED FACT`** — implemented and tested 2026-09-05. Per ADR-0003
D1–D3, D10. Unlike `docs/architecture/FOUNDATION_ARCHITECTURE.md`'s Voice
boundary (still conceptual for everything beyond this), this document
describes real code.

Related: `docs/decisions/ADR-0003_realtime_voice_foundation.md` (the decision
this implements), `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
(the `ConversationSession` this substage deliberately does **not** touch),
the M2.1 implementation report.

---

## 1. Scope (ADR-0003 M2.1)

Local microphone input → Pipecat local audio transport → Silero VAD →
speech-start/speech-stop events → NeXa `VoiceState` transitions → local
audio-output plumbing. **No STT, no LLM call, no TTS, no
`ConversationSession`, no barge-in.** Those are M2.2 (STT + language
strategy), M2.3 (the `ConversationSession` voice adapter), M2.4 (TTS), M2.5
(barge-in) — each its own ADR-0003-scoped substage, not built here.

## 2. The one path

```
microphone (reSpeaker XVF3800, resolved by name — see §5)
    │
    ▼
Pipecat LocalAudioTransport.input()  (PyAudio, mono, 16 kHz)
    │
    ▼
Pipecat VADProcessor(SileroVADAnalyzer)
    │  produces VADUserStartedSpeakingFrame / VADUserStoppedSpeakingFrame
    ▼
NeXa _VoiceStateFrameProcessor
    │  apply_frame_to_state_machine() — pure, no Pipecat lifecycle needed
    ▼
NeXa VoiceStateMachine  (IDLE → LISTENING → USER_SPEAKING → END_OF_TURN → LISTENING)
    │
    ▼
Pipecat LocalAudioTransport.output()  (plumbing verified open/close cleanly;
                                        nothing is played into it in M2.1)
```

Every frame is forwarded downstream unchanged by NeXa's processor — it
observes, it never terminates or rewrites the Pipecat pipeline.

## 3. Package layout (`src/nexa/voice/`)

| Module | Responsibility |
|---|---|
| `state.py` | `VoiceState` enum, `VoiceEvent`, `VoiceStateMachine` — pure state logic, zero Pipecat/audio dependency, fully unit-testable without hardware |
| `config.py` | `LocalAudioConfig` — explicit, typed, frozen dataclass |
| `device.py` | `find_device_index()` — resolves a PyAudio device **by name**, never a fixed index (see §5) |
| `runtime.py` | `VoiceRuntime` (builds and runs the Pipecat pipeline), `_VoiceStateFrameProcessor` (the one new Pipecat `FrameProcessor`), `apply_frame_to_state_machine()` (the pure frame→state mapping, extracted specifically so it's testable without a running pipeline) |

`apps/nexa_voice_probe.py` is a thin CLI wrapper (`apps/README.md`'s
convention) — wiring/presentation only, drives the real `VoiceRuntime`.

## 4. Architectural ownership — verified, not just asserted

`src/nexa/voice/` imports nothing from `nexa.conversation`, `nexa.providers`,
`nexa.bootstrap`, or `nexa.config` (the persona/provider loader) — enforced
by `tests/test_voice_architecture.py` via `ast`-based import inspection, not
just a code-review convention. There is no `ConversationSession` call, no
persona, no model/provider choice, no second conversation authority anywhere
in this substage. Pipecat owns exactly what ADR-0003 assigns it: audio
transport and VAD frame production — nothing else.

## 5. Current Pipecat API used (1.8.1) — verified against source, not memory

`PipelineTask`/`PipelineRunner` (common in older tutorials) are **deprecated
since 1.3.0**. `VoiceRuntime` uses the current, non-deprecated path:
`pipecat.pipeline.pipeline.Pipeline`, `pipecat.pipeline.worker.PipelineWorker`
+ `PipelineParams`, `pipecat.workers.runner.WorkerRunner`. VAD is **not** a
`TransportParams.vad_analyzer` field (an older pattern) — the current API
wires a `pipecat.processors.audio.vad_processor.VADProcessor` as its own
explicit pipeline stage, producing `VADUserStartedSpeakingFrame`/
`VADUserStoppedSpeakingFrame` (raw VAD-level; distinct from the
higher-level, STT-dependent `UserStartedSpeakingFrame`/
`UserStoppedSpeakingFrame`, which M2.1 does not use).

Silero VAD (`pipecat.audio.vad.silero.SileroVADAnalyzer`) ships **inside
pipecat-ai's core package** — no extra required beyond `pipecat-ai[local]`
(the `local` extra adds only `pyaudio`). `onnxruntime` is already a core,
unconditional dependency of `pipecat-ai`. `pipecat-ai[local-smart-turn]` is
**not installed** — ADR-0003 D10, independently reproduced: it pulls full
PyTorch + `coremltools`, and `coremltools`'s native modules
(`libcoremlpython`, `libmilstoragepython`) are macOS-only and fail to load
on this Linux/ARM64 Pi.

## 6. reSpeaker XVF3800 — real hardware findings

`VERIFIED FACT`, this Pi, 2026-09-05:

- Hardware ALSA format is **fixed**: `S16_LE`, 2 channels (stereo), 16000 Hz
  — not a range. `arecord/aplay --dump-hw-params` on `hw:CARD=Array,DEV=0`
  confirms `CHANNELS: 2` exactly, no mono option at the raw hardware level.
- The project's own `/etc/asound.conf` (pre-existing, not written by this
  task) already defines a `respeaker` ALSA `plug:` alias
  (`slave.pcm "hw:CARD=Array,DEV=0"`), addressed by stable **card name**
  rather than a positional index — the same lesson the file's own comment
  records from a prior incident (USB re-enumeration changing device order
  across reboots). PyAudio/PortAudio enumerates this alias as a real device
  (`"respeaker"`, `maxInputChannels=128`, `maxOutputChannels=128` — the
  large numbers signal ALSA's `plug` layer will convert format/rate/channels
  in software).
- **Verified directly**: opening `"respeaker"` with `channels=1,
  rate=16000` for both capture and playback succeeds cleanly — ALSA's `plug`
  layer downmixes the fixed 2-channel hardware to mono transparently. No
  custom channel-mixing code was needed in NeXa.
- **New finding, not previously documented**: the system-wide ALSA default
  output device (`pcm.!default` → `plug:usb_speaker` → a Jieli
  `UACDemoV1.0` USB DAC, addressed by stable card name in the same file) is
  **not physically connected** on this machine right now — attempting
  `aplay -D default` fails outright (`Cannot get card index for
  UACDemoV10`). `LocalAudioConfig`'s default (`"respeaker"` for **both**
  input and output) is a deliberate, documented choice given this reality,
  not a silent workaround — see `config.py`'s docstring.
- Device resolution is always **by name** (`find_device_index()`, exact
  match preferred, substring fallback), never a fixed PyAudio index — the
  same anti-flakiness discipline the project's own ALSA config already
  established for this exact hardware class.

## 7. Silero VAD — configuration and defaults

`pipecat.audio.vad.silero.SileroVADAnalyzer` wraps the bundled
`silero_vad.onnx` model via `onnxruntime`, **forced to the CPU execution
provider** with `inter_op_num_threads=1` and `intra_op_num_threads=1` set
internally by Pipecat itself — VAD is single-threaded by the library's own
design, not a NeXa tuning choice, and dovetails directly with ADR-0003 D7's
thread-budget requirement. Sample rate must be 8000 or 16000 Hz (16000 used
here, matching the reSpeaker's native rate); the model requires exactly 512
mono int16 samples per inference call at 16 kHz (32 ms frames).

`VADParams` used — `stop_secs` was **retuned from the library default with
real evidence**, not left unchanged, and not tuned aggressively without it:

| Parameter | Value | Meaning |
|---|---|---|
| `confidence` | 0.7 | minimum voice-detection confidence (library default, unchanged) |
| `start_secs` | 0.2 | speech duration required to confirm start (library default, unchanged — no evidence yet requires changing it) |
| `stop_secs` | **1.0** | silence duration required to confirm stop (**changed from the library default of 0.2** — see below) |
| `min_volume` | 0.6 | minimum audio volume threshold (library default, unchanged) |

**Why `stop_secs` changed, in two evidence steps (do not conflate them):**

1. A first live-hardware test (`stop_secs=0.2`, the library default) split
   one natural Polish sentence into three separate `USER_SPEAKING`/
   `END_OF_TURN` cycles. The two internal pauses measured (via this
   module's own event timestamps) at ~1.66 s and ~1.82 s. This is real
   evidence that 0.2 s is too short — but the operator correctly flagged
   that those specific pause lengths (aimed at ~0.5 s/~0.8 s, overshot by
   manual timing) are **not** evidence of where the correct threshold is,
   and a naive retest at a new value with "the same pauses as before" would
   be circular.
2. A **deterministic offline calibration** resolved the actual threshold
   without relying on further human-timed pauses: the real
   `SileroVADAnalyzer` (same class, same confirmation logic) was fed real
   recorded Polish speech with precisely inserted real-silence gaps (the
   actual reSpeaker room-noise floor, not digital zeros) of exactly
   0.4/0.6/0.8/1.0/1.2 s, swept against `stop_secs` candidates
   {0.2, 0.6, 0.8, 1.0}. Full results:
   `docs/research/m2_1_vad_calibration/vad_calibration_results.json`.

   | `stop_secs` | 0.4s gap | 0.6s gap | 0.8s gap | 1.0s gap | 1.2s gap | measured stop latency |
   |---|---|---|---|---|---|---|
   | 0.2 | split | split | split | split | split | ~0.16 s |
   | 0.6 | held | split | split | split | split | ~0.58 s |
   | 0.8 | held | held | **split** | split | split | ~0.77 s |
   | **1.0** | **held** | **held** | **held** | split | split | ~0.96 s |

   The operator's calibration target was explicit: pauses at ~0.4 s, ~0.6 s,
   **and ~0.8 s** should not split a sentence. `stop_secs=0.8` sits exactly
   on the 0.8 s boundary and fails that third target — a real "~0.8 s" pause
   can easily land at or above it. **`stop_secs=1.0` is the smallest tested
   value that holds all three targets**, at the measured cost of ~1.0 s
   end-of-turn latency instead of ~0.77 s (a real, felt trade-off, not a
   free change).

**`VERIFIED FACT` (2026-09-05, live hardware retest, confirmed)**: with
`stop_secs=1.0`, the operator repeated the same multi-pause Polish sentence
live — one continuous `USER_SPEAKING`, no split at either internal pause,
one `END_OF_TURN` after genuine completion, clean return to `LISTENING`.
Full transcript in the M2.1 report's "REAL HARDWARE TEST" section. **Note on
latency, stated precisely rather than overclaimed**: that live session's
event log times VAD-confirmed transitions only — the operator's physical
speech-end moment was not separately instrumented, so no exact live latency
figure is derived from it. The offline calibration's ~0.96 s (§7 table
above), measured against a known-exact silence-gap ground truth, is the
evidence-backed latency figure, not a value read off the live log.

`stop_secs=1.0` is recorded as an **evidence-backed baseline, not a
permanently frozen value** — same non-frozen-baseline discipline as every
other M2.1/M1.1 choice (ADR-0002 D4, ADR-0003 D4/D6): a future substage
(e.g. once STT/TTS latency is also in the loop) may justify a different
value with its own evidence, via the same process, not a silent change.

## 8. Resource footprint — measured on this Pi

`VERIFIED FACT`, 2026-09-05: idle/listening resident memory ~120 MB, average
CPU ~6.5% of one core over a 20 s continuous-listening sample (silence),
temperature stable ~52–53°C, zero throttling. This is intentionally light —
ADR-0003 D7 requires thread-budget discipline starting at M2.1, and Pipecat's
own Silero integration already restricts itself to a single inference
thread. Full stage-by-stage numbers (idle vs. listening vs. VAD-active) are
in the M2.1 report.

## 9. Known limitations, stated plainly

- **VAD-only turn detection**: `VADUserStartedSpeakingFrame`/
  `VADUserStoppedSpeakingFrame` reflect acoustic speech activity, not
  linguistic completeness — a pause longer than `stop_secs` (1.0 s, tuned —
  see §7) can still trigger `END_OF_TURN` before the user has actually
  finished a thought, if they genuinely pause that long mid-sentence. This
  is expected VAD-only behavior, not a bug; smarter, linguistically-aware
  turn-detection (if ever pursued) is explicitly deferred (ADR-0003 D10
  rejected Pipecat's `local-smart-turn` extra on this hardware; no
  replacement is chosen here).
- **No echo cancellation logic built here**: the reSpeaker XVF3800 has
  documented AEC/beamforming hardware capability (`OBSERVATION`, not
  independently verified in this substage — recorded for M2.5's barge-in
  design to account for, not used yet).
- **No STT/TTS interfaces created**: per the task's explicit instruction,
  M2.1 does not introduce speculative STT/TTS provider abstractions — those
  become real (and get their own interface, mirroring ADR-0002 D2's
  `ModelProvider` pattern) only in M2.2/M2.4 when they have real
  implementations behind them.
