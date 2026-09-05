# R0007 — M2.1 Local Audio + Silero VAD Foundation

- **Date:** 2026-09-05
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.1 — Pipecat Foundation +
  Local Audio + Silero VAD**
- **Related:** `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D1-D3,
  D10 — the decision this implements),
  `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` (verified
  architecture detail), `docs/research/m2_1_vad_calibration/` (raw
  calibration evidence), `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
  (the `ConversationSession` this substage deliberately does not touch)

---

## TASK RESULT

**PASS.** All 15 acceptance criteria met. Endpointing (`stop_secs`) required
one round of operator-directed, evidence-based retuning before PASS — this
is disclosed in full below as the main technical narrative of this
substage, not smoothed over.

## WHAT I DID

1. Read `AGENTS.md`, `CURRENT_STATE.md`, `ROADMAP.md`, `ADR-0003`, the M2
   research/feasibility docs, `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`,
   `FOUNDATION_ARCHITECTURE.md`, `TEST_STRATEGY.md`; verified `git
   status`/`log`, `free -h`, `ollama ps`, and real audio hardware
   (`arecord -l`/`aplay -l`) before writing anything.
2. **Verified the current Pipecat (1.8.1) API against source, not memory**:
   confirmed `PipelineTask`/`PipelineRunner` are deprecated since 1.3.0 in
   favor of `PipelineWorker`/`WorkerRunner`; confirmed Silero VAD ships
   inside `pipecat-ai`'s core package (no extra needed beyond `[local]` for
   `pyaudio`); confirmed VAD is wired as an explicit `VADProcessor` pipeline
   stage (not a `TransportParams.vad_analyzer` field, an older pattern).
3. **Investigated the reSpeaker XVF3800 for real** (not assumed): confirmed
   fixed 2-channel/16 kHz hardware ALSA format; found and read the
   project's own pre-existing `/etc/asound.conf` (a `respeaker` `plug:`
   alias by stable card name); discovered and documented that the
   system-wide default *output* device (a separate USB speaker DAC) is not
   physically connected right now. Verified, via direct PyAudio testing
   before writing any production code, that the `respeaker` alias opens
   cleanly for **mono capture** (bytes were actually read back from the
   real microphone) and that the **mono playback stream opens, accepts a
   write, and closes without error**. This is stream-level open/close/write
   verification, not confirmation of audible sound reproduction — no tone
   was played and no operator listened for output in this test; that
   remains unverified (see "LOCAL AUDIO OUTPUT" below and "UNRESOLVED").
4. Installed `pipecat-ai[local]==1.8.1` into the repo's own `./.venv`
   (never the legacy repo's venv), explicitly **not** installing
   `[local-smart-turn]` (ADR-0003 D10).
5. Implemented `src/nexa/voice/` (`state.py`, `config.py`, `device.py`,
   `runtime.py`) and `apps/nexa_voice_probe.py` — see "ARCHITECTURE /
   DOCUMENTATION" below for the full design.
6. Wrote 31 new tests (`tests/test_voice_*.py`): 30 deterministic
   (state machine, frame mapping, device resolution, architecture checks)
   plus 1 opt-in hardware integration test.
7. **Ran the manual hardware acceptance test with the operator across three
   rounds**, each driven by real operator feedback that caught a real
   methodological gap — see "REAL HARDWARE TEST" below in full.
8. Measured idle/listening CPU/RAM/temperature/throttling on real hardware.
9. Wrote `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`, this
   report, updated `docs/CURRENT_STATE.md` and `docs/testing/TEST_STRATEGY.md`,
   and added one pointer from `FOUNDATION_ARCHITECTURE.md`'s Voice section.

## PIPECAT VERSION / API

`pipecat-ai[local]==1.8.1`, pinned exactly in `pyproject.toml`. Current,
non-deprecated API used throughout: `pipecat.pipeline.pipeline.Pipeline`,
`pipecat.pipeline.worker.PipelineWorker` + `PipelineParams`,
`pipecat.workers.runner.WorkerRunner`, `pipecat.processors.audio.vad_processor.VADProcessor`,
`pipecat.audio.vad.silero.SileroVADAnalyzer`, `pipecat.transports.local.audio.LocalAudioTransport`.
Full detail and the specific deprecated-API traps avoided:
`M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` §5.

## LOCAL AUDIO INPUT

reSpeaker XVF3800 4-Mic Array, resolved by name (`"respeaker"`, the
project's pre-existing ALSA `plug:` alias — never a fixed PyAudio index),
mono, 16 kHz. `find_device_index()` prefers an exact name match over a
substring match specifically so it can't accidentally resolve to the raw
`hw:2,0` device (fixed 2-channel, no software conversion) instead of the
flexible `plug:` alias. Verified stable, clean capture across all manual
test sessions — no device-open errors, no clipping observed.

## LOCAL AUDIO OUTPUT

Also `"respeaker"` (its own playback subdevice), mono, 16 kHz — **verified
only that the output stream opens, accepts written samples, and closes
without error**, both in isolated PyAudio testing and as part of every
Pipecat pipeline run in this substage (the pipeline always opens both
transports). **No audible tone or speech was ever played, and no operator
confirmed hearing anything from this output path** — M2.2 does not need
audio output at all (STT only), and TTS (M2.4) is what will first make this
path carry real audio. Stated precisely rather than overclaimed. **New
real-hardware finding**: the system-wide ALSA default *output* device (a
separate USB speaker DAC, addressed by stable card name `UACDemoV10` in the
pre-existing `/etc/asound.conf`) is not physically connected on this
machine right now (`aplay -D default` fails outright). Using the
reSpeaker's own output for M2.1 is a documented,
verified choice given that reality, not a silent workaround
(`LocalAudioConfig` docstring, `M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` §6).
No TTS or other audio is played into it in M2.1 — only open/close plumbing
was verified, per task scope.

## SILERO VAD

`pipecat.audio.vad.silero.SileroVADAnalyzer` — the library-bundled ONNX
model, CPU execution provider, single-threaded internally
(`inter_op_num_threads=1`, `intra_op_num_threads=1`, set by Pipecat itself).
`confidence=0.7`, `min_volume=0.6`, `start_secs=0.2` all left at library
defaults — no evidence required changing them. `stop_secs` required
retuning; see "REAL HARDWARE TEST" for the full evidence chain (this is the
substantive technical result of this substage).

## VOICE STATE MACHINE

`src/nexa/voice/state.py` — `VoiceState` (`IDLE`/`LISTENING`/
`USER_SPEAKING`/`END_OF_TURN`/`ERROR`, exactly the states real in M2.1, per
the task's explicit instruction not to pretend `UNDERSTANDING`/`THINKING`/
`SPEAKING`/`INTERRUPTED` exist yet). Pure state logic — no audio, no
Pipecat — guards against duplicate/noisy events explicitly (a second
speech-start while already speaking is ignored; a speech-stop with no
matching start is ignored) rather than corrupting state. `_VoiceStateFrameProcessor`
(`runtime.py`) is the one new Pipecat `FrameProcessor`, translating
`VADUserStartedSpeakingFrame`/`VADUserStoppedSpeakingFrame`/lifecycle frames
into state-machine calls via a pure, separately-testable function
(`apply_frame_to_state_machine`) — extracted specifically so the mapping is
unit-testable without a running Pipecat pipeline/task-manager lifecycle.

## REAL HARDWARE TEST

This substage's real technical narrative. Reported in full, in order, per
the task's "do not hide the result" instruction.

### Round 1 — `stop_secs=0.2` (library default): FAILED, evidence gathered

Operator ran the deliberate endpointing test (one sentence, two deliberate
internal pauses aimed at ~0.5 s/~0.8 s) with the as-shipped default. Real
observed transitions:

| # | USER_SPEAKING | END_OF_TURN | gap to next USER_SPEAKING |
|---|---|---|---|
| 1 | 11:33:54.284 | 11:33:55.604 | — |
| 2 | 11:33:57.264 | 11:33:58.705 | 1.660 s (11:33:55.604 → 11:33:57.264) |
| 3 | 11:34:00.525 | 11:34:01.205 | 1.820 s (11:33:58.705 → 11:34:00.525) |

The single intended utterance was split into **three** separate
`USER_SPEAKING`/`END_OF_TURN` cycles. **Operator correction, preserved
precisely**: the measured gaps (~1.66 s, ~1.82 s) are real, but they are
**not** a measurement of the intended ~0.5 s/~0.8 s pause lengths — manual
pause timing overshot considerably. This is evidence that `stop_secs=0.2` is
too short for ordinary speech, but explicitly **not** evidence of where the
correct threshold is.

### Round 2 attempt — methodological correction before any retest ran

Before running a live retest at a candidate value, the operator identified
that reproducing "the same pauses as before" at any `stop_secs < 1.66 s`
would trivially still split — proving nothing about ordinary pause
tolerance — and proposed splitting validation into (A) a controlled/
deterministic calibration and (B) a natural-feel live retest, plus
suggested an offline synthetic-silence test as an even better calibration
method. **No live audio was captured in this round** — the correction
happened before the next live test was run.

### Test A — deterministic offline calibration (no microphone, no human timing)

Built `docs/research/m2_1_vad_calibration/vad_offline_calibration.py`: feeds
the real `SileroVADAnalyzer` (identical class/config path `VoiceRuntime`
uses) real recorded Polish speech (`docs/research/m2_voice_spikes/asr_test_samples/`)
with precisely inserted **real silence** (extracted from the same
recordings' own room-noise floor — not digital zeros) of exactly
0.4/0.6/0.8/1.0/1.2 s, swept against `stop_secs` ∈ {0.2, 0.6, 0.8, 1.0}.
Verified this is a faithful reproduction of the live logic by reading
`pipecat/audio/vad/vad_analyzer.py` directly: the start/stop confirmation
is purely audio-frame-count-based (`_vad_stop_frames = round(stop_secs /
(512/sample_rate))`), not wall-clock-based, so no real-time waiting or
human timing is needed for a faithful test. Full raw output:
`docs/research/m2_1_vad_calibration/vad_calibration_results.json`.

| `stop_secs` | 0.4s gap | 0.6s gap | 0.8s gap | 1.0s gap | 1.2s gap | measured stop latency |
|---|---|---|---|---|---|---|
| 0.2 | split | split | split | split | split | ~0.16 s |
| 0.6 | held | split | split | split | split | ~0.58 s |
| 0.8 | held | held | **split** | split | split | ~0.77 s |
| **1.0** | **held** | **held** | **held** | split | split | ~0.96 s |

The operator's calibration target (0.4/0.6/**0.8** s pauses should not
split) rules out `0.8` (it sits exactly on that boundary and fails the
third target) and selects **`stop_secs=1.0`** as the smallest tested value
that clears all three, at a measured cost of ~0.96 s stop latency (vs.
~0.77 s at 0.8).

### Test B — live hardware retest, `stop_secs=1.0`: PASS

Operator repeated the same sentence live. Real observed transitions:

```
12:00:03.096  (start)     state: LISTENING       (runtime started)
12:00:14.115  +11019ms    state: USER_SPEAKING   (VAD speech start)
12:00:20.456  + 6340ms    state: END_OF_TURN     (VAD speech stop)
12:00:20.456  +    0ms    state: LISTENING       (turn complete, resume listening)
[Ctrl+C]
12:00:28.780  + 8324ms    state: IDLE            (runtime shutdown)
```

One continuous `USER_SPEAKING` for the whole sentence (both internal pauses
held, no split), exactly one `END_OF_TURN` after genuine completion, clean
return to `LISTENING`, clean `IDLE` on Ctrl+C — **PASS** against the
operator's acceptance rule.

**Latency, stated precisely per the operator's explicit instruction**: this
live log times VAD-confirmed transitions only. The operator's physical
speech-end moment was not separately instrumented (no external ground-truth
marker), so **no exact live end-of-turn latency figure is derived from this
log** — the `+11019ms`/`+6340ms`/`+8324ms` deltas are real-time gaps between
this session's own events (time spent silent before speaking, speaking
duration, time idle before Ctrl+C), not a latency measurement. **Test A's
~0.96 s, measured against a known-exact synthetic silence-gap ground
truth, is the evidence-backed latency figure for `stop_secs=1.0`.**

### Final value: `stop_secs=1.0` — evidence-backed baseline, not frozen

Recorded in `src/nexa/voice/runtime.py`'s `DEFAULT_VAD_PARAMS` with the full
evidence chain in a code comment (both rounds, explicitly not conflated),
and in `M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` §7. Configurable via
`VoiceRuntime(vad_params=...)` and exposed as `--stop-secs`/`--start-secs`
on `apps/nexa_voice_probe.py` for any future retest. **Not a permanently
frozen value** — same non-frozen-baseline discipline as every other M2.1/
M1.1 choice (ADR-0002 D4, ADR-0003 D4/D6): a future substage (e.g. once
STT/TTS latency is also in the loop) may justify a different value with its
own evidence.

## CPU / RAM / THERMAL

Measured during a 20 s continuous-LISTENING sample (silence, this dev
session, unaffected by the `stop_secs` value — VAD per-frame inference cost
is independent of the confirmation threshold):

| Metric | Value |
|---|---|
| Resident memory | ~120 MB |
| Average CPU | ~6.5% of one core |
| Temperature | stable ~52–53°C |
| Throttling | none (`vcgencmd get_throttled` = `0x0`) throughout every test in this substage |

Intentionally light — ADR-0003 D7 requires thread-budget discipline starting
at M2.1, and Pipecat's own Silero integration already restricts itself to a
single inference thread by the library's own design.

## TESTS

64 total (33 pre-existing M1.1 tests unaffected, verified via a temporary
`git worktree` at the pre-M2.1 commit `283ef75` + fresh `unittest discover` —
not assumed + 31 new, table below sums to 31):

| File | Tier | Count |
|---|---|---|
| `tests/test_voice_state.py` | unit | 13 |
| `tests/test_voice_frame_mapping.py` | unit (real Pipecat `Frame` instances, no pipeline lifecycle) | 7 |
| `tests/test_voice_device.py` | unit (fake PyAudio device list) | 5 |
| `tests/test_voice_architecture.py` | unit (`ast`-based import inspection) | 5 |
| `tests/test_voice_hardware_probe.py` | integration, real hardware, opt-in (`NEXA_RUN_VOICE_HARDWARE_TEST=1`) | 1 |
| `apps/nexa_voice_probe.py` | manual, human-in-the-loop, not automatable | — |

`python -m unittest discover -s tests` and `pytest`: **64 tests, all pass**,
2 intentionally skipped (the pre-existing live-Ollama test and this
substage's hardware probe — both opt-in). `ruff check src tests apps`:
clean. The opt-in hardware probe test was run for real
(`NEXA_RUN_VOICE_HARDWARE_TEST=1`): passed. The task's 10 required test
scenarios are covered: state-transition ordering, speech-start/stop
emission, return-to-LISTENING, duplicate/noisy-event safety, shutdown-to-IDLE,
explicit ERROR transitions, no `ConversationSession`/LLM call (`ast`-verified,
not just asserted), no second conversation authority (same), explicit typed
config (frozen-dataclass tests).

## ARCHITECTURE / DOCUMENTATION

- `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` — new, verified.
- This report (`R0007`).
- `docs/research/m2_1_vad_calibration/` — new: the offline calibration
  script, its raw JSON results, and a README explaining why the calibration
  exists and how to reproduce it.
- `docs/CURRENT_STATE.md`, `docs/testing/TEST_STRATEGY.md` — updated.
- `docs/architecture/FOUNDATION_ARCHITECTURE.md` — one pointer added to the
  Voice section (conceptual framing otherwise unchanged, same pattern as
  M1.1's pointer).
- `docs/decisions/ADR-0003_realtime_voice_foundation.md` — **not modified**.
  Nothing in this substage exposed a contradiction or a new architectural
  decision; `stop_secs` retuning is exactly the kind of evidence-based
  component-level change ADR-0003 D4/D6 already anticipated for STT/TTS
  baselines, applied here to VAD by the same discipline.
- `pyproject.toml` — `pipecat-ai[local]==1.8.1` added as a real, pinned
  runtime dependency (ADR-0003 D1).

## COMMIT

One commit, implementation + tests + docs (see below). Not pushed.

## UNRESOLVED

- `stop_secs=1.0`'s ~0.96 s added latency is a real, felt cost — not yet
  weighed against STT/LLM/TTS latency in a full turn (that requires M2.2+
  to exist). May be revisited once the full pipeline's latency budget is
  known.
- The reSpeaker XVF3800's documented AEC/beamforming capability was not
  independently verified in this substage (`OBSERVATION` only) — relevant
  input for M2.5's barge-in design.
- The dedicated USB speaker DAC (`UACDemoV10`) being disconnected is a real
  hardware-environment fact, not a NeXa bug — worth the owner's awareness
  for any future physical deployment step.
- No STT, `ConversationSession` adapter, TTS, or barge-in exists yet — all
  explicitly out of scope, per ADR-0003's M2.1 substage boundary.

## CURRENT VERIFIED STATE

M2.1 implemented, tested, and operator-confirmed on real hardware across a
genuine tuning cycle. `stop_secs=1.0` is the evidence-backed VAD baseline.
No STT/LLM/TTS/barge-in exists. M1.1 and `ConversationSession` are
completely unaffected — no second conversation authority was created.

## NEXT RECOMMENDED ACTION

Start **M2.2 — whisper.cpp STT adapter + PL/EN language-hint strategy**
(ADR-0003's substage table), as a new, explicitly-started task. `R0006`
already measured `whisper.cpp base/q8_0` as the accuracy/latency baseline on
this hardware and confirmed auto-language-detection is unreliable — M2.2's
job is to wire that measured choice behind a real component (not yet a
formal provider interface, per ADR-0003 D11 — that's still open until M2.2
shows whether one is genuinely needed) that consumes M2.1's `END_OF_TURN`
event to trigger transcription of the captured utterance, with an explicit
language hint, not `auto`.

## LEGACY NEXA USED

NO — this substage's audio evidence came from M2.0A's already-copied
fixtures (`docs/research/m2_voice_spikes/`) and this session's real
hardware, not new legacy repo reads.

## EXTERNAL RESEARCH USED

YES — `pipecat-ai` 1.8.1 source (GitHub-distributed via PyPI, read directly
from the installed package, not from memory/tutorials) for the current API;
no other external research.
