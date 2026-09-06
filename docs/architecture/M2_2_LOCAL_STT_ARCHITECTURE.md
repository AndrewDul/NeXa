# M2.2 — Local whisper.cpp STT Foundation (verified architecture)

Status: **`VERIFIED FACT`** — implemented, tested, and operator-confirmed on
real hardware 2026-09-05/06. Per ADR-0003 D4, D5, D7, D11. Unlike
`docs/architecture/FOUNDATION_ARCHITECTURE.md`'s Voice boundary (still
conceptual for STT/TTS/barge-in beyond this), this document describes real
code.

Related: `docs/decisions/ADR-0003_realtime_voice_foundation.md` (the decision
this implements), `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`
(the M2.1 pipeline this substage extends, not replaces), the M2.2
implementation report (`R0008`).

---

## 1. Scope (ADR-0003 M2.2)

One completed M2.1 utterance (LISTENING → `USER_SPEAKING` → `END_OF_TURN`) →
local whisper.cpp transcription → plain text, with an explicit PL/EN
language hint. **No LLM, no `ConversationSession` voice adapter, no TTS, no
Piper, no barge-in, no LiveKit/WebRTC, no cloud STT, no memory, no tools.**
Those are M2.3+ substages, not built here.

## 2. The one path

```
NeXa VoiceStateMachine (M2.1, unchanged)
    │
    ▼
_UtteranceCaptureFrameProcessor          (new, M2.2 — placed right after VADProcessor)
    │  InputAudioRawFrame  → UtteranceBuffer.append_audio()   (pre-roll ring, or linear capture)
    │  VADUserStartedSpeakingFrame → UtteranceBuffer.mark_speech_started()
    │  VADUserStoppedSpeakingFrame → UtteranceBuffer.mark_speech_stopped() → one utterance's raw PCM
    ▼
SerialTranscriptionQueue.submit(audio, language)   (non-blocking; FIFO; ≤1 whisper.cpp process at a time)
    │
    ▼
WhisperCppTranscriber.transcribe(audio, language=...)
    │  writes a temp WAV, invokes the pinned whisper-cli subprocess, parses its -oj JSON, deletes temp files
    ▼
TranscriptionResult(text, language, audio_duration_s, wall_latency_s)
    │
    ▼
on_transcription callback  (apps/nexa_stt_probe.py in M2.2; the ConversationSession
                             voice adapter, not built yet, in M2.3)
```

Every Pipecat frame is still forwarded downstream unchanged by
`_UtteranceCaptureFrameProcessor` — it observes and enqueues, it never
terminates or rewrites the pipeline. Audio/VAD capture is completely
decoupled from STT execution: the next utterance can be captured while the
previous one is still being transcribed (see §6).

## 3. Package layout (`src/nexa/stt/`)

| Module | Responsibility |
|---|---|
| `config.py` | Pinned whisper.cpp version/commit, `Language` (typed `pl`/`en`, no `auto`), `WhisperCppConfig`, XDG data-dir path resolution |
| `errors.py` | `SttError` hierarchy — every failure explicit, no silent fallback |
| `transcriber.py` | `TranscriptionResult`, `SpeechTranscriber` (the STT boundary protocol), `WhisperCppTranscriber` (the only implementation) |
| `utterance_buffer.py` | `UtteranceBuffer` — pure, no Pipecat dependency; owns the pre-roll ring and the per-turn linear capture |
| `queue.py` | `SerialTranscriptionQueue` — pure, no Pipecat dependency; guarantees ≤1 `transcribe()` call in flight, FIFO order |

`src/nexa/voice/runtime.py` gained `_UtteranceCaptureFrameProcessor` (wires
the above into the M2.1 pipeline) and `VoiceRuntime.__init__` gained
optional `transcriber`/`language`/`on_transcription`/`on_transcription_error`
parameters, defaulting to `None` — **omitting them reproduces the exact M2.1
pipeline**, verified by `tests/test_stt_architecture.py`'s
`TestVoiceRuntimeDefaultsPreserveM21Behavior`.

`apps/nexa_stt_probe.py` is a new, separate thin CLI wrapper
(`apps/README.md`'s convention) — `apps/nexa_voice_probe.py` (M2.1) was not
extended, per the task's explicit instruction.

`scripts/setup_whisper_cpp.py` is a developer/operational bootstrap script
(`scripts/README.md`) — no product logic; imports its pinned
version/path constants from `nexa.stt.config` rather than duplicating them.

## 4. Architectural ownership — verified, not just asserted

`src/nexa/stt/` and `src/nexa/voice/` import nothing from `nexa.conversation`
or `nexa.providers`, and reference no TTS engine (`piper`/`tts`/`kokoro`) —
enforced by `tests/test_stt_architecture.py` via `ast`-based import
inspection and an `ast.Call` scan for `ConversationSession(...)`, mirroring
M2.1's `test_voice_architecture.py`. There is no LLM call, no persona, no
TTS, no barge-in anywhere in this substage. `WhisperCppTranscriber` is the
only place that knows whisper.cpp CLI details (binary path, flags, JSON
schema) — the voice runtime depends only on the `SpeechTranscriber` protocol.

## 5. whisper.cpp version, build, and install — reproducible, outside git

`VERIFIED FACT`, via the GitHub API directly (not WebFetch, which returned a
stale/incorrect date) and confirmed by actually cloning the tag:

- Repo: `ggml-org/whisper.cpp`, tag `v1.9.3` (= build tag `b4938`), commit
  `371b5a7561823ab2bb32142d2751e35e7534727b`, published 2026-08-20.
- Built with `cmake -B build -DCMAKE_BUILD_TYPE=Release` +
  `cmake --build build --config Release -j<nproc> --target whisper-cli
  whisper-quantize` — correctly detects this Pi's Cortex-A76 ARM64
  (`-mcpu=cortex-a76+crc+crypto+dotprod`).
- Model: `base` GGML (`models/download-ggml-model.sh base`), quantized to
  `q8_0` via `whisper-quantize` — ADR-0003 D4 / R0006's measured baseline
  (0.354 avg WER, ~1.69s avg latency, ~221MB RSS on this exact 12-file PL/EN
  corpus). **Not silently changed** — `MODEL_NAME`/`MODEL_QUANT` are named
  constants in `config.py`.

**Install discipline** (explicit requirement — R0006's scratch build was
deleted, so this had to be genuinely reproducible, not re-typed from memory):
`scripts/setup_whisper_cpp.py` clones/builds/downloads into
`$NEXA_STT_DATA_DIR` (env override) → `$XDG_DATA_HOME/nexa/stt` →
`~/.local/share/nexa/stt` (fallback) — **entirely outside this git repo**.
Idempotent (`--force` to rebuild), verifies the resolved tag's actual commit
hash matches the pin (raises loudly on a mismatch, e.g. if upstream moved the
tag), requires `git`/`cmake` on `PATH`, no `sudo`. Never runs automatically —
`WhisperCppTranscriber.__init__` raises `SttBinaryNotFoundError`/
`SttModelNotFoundError` with the exact fix command if the binary/model are
missing, rather than triggering a hidden network download.

## 6. STT boundary and concurrency (ADR-0003 D11)

**`SpeechTranscriber`** is the smallest boundary that was genuinely needed:
one async method, `transcribe(audio: bytes, *, language: Language) ->
TranscriptionResult`. `WhisperCppTranscriber` is the only implementation —
no second STT engine was built, per task scope. It owns binary/model path
validation (at construction), the explicit language flag, thread count,
subprocess invocation (`subprocess.run([...])`, an explicit argument list,
never `shell=True`), timeout, JSON parsing (`-oj`/`--output-json` — a
structured file, not text-scraped from stdout), and temp-file cleanup
(`tempfile.TemporaryDirectory`, deleted in all cases including exceptions).

**Real hardware finding (2026-09-05/06, `VERIFIED FACT`) — concurrency fix.**
The first implementation dispatched one independent `self.create_task(...)`
per completed utterance. Real hardware testing showed a short next utterance
can reach `END_OF_TURN` before the previous whisper.cpp subprocess finishes
— on a 4-core Pi already shown to have real CPU contention (R0006), two
concurrent 4-thread whisper.cpp processes is a real risk, not a theoretical
one. Fixed with **`SerialTranscriptionQueue`** (`src/nexa/stt/queue.py`):

- `submit(audio, language)` is synchronous and non-blocking — audio/VAD
  capture is never slowed down by an in-flight transcription. A new
  utterance can be captured while the previous one is still transcribing.
- A single background worker processes the queue strictly FIFO, one
  `transcribe()` call at a time — `max_observed_concurrency` is tracked and
  proven to stay at 1 (`tests/test_stt_queue.py`, and live on real hardware
  — see §8).
- Bounded (`max_queue_size=8`, generous relative to realistic single-user
  speech pacing) — overflow raises `SttQueueOverflowError` explicitly; an
  utterance is never dropped silently.
- `shutdown()` drains whatever is queued (does not drop it), then exits —
  no orphan task, called from `_UtteranceCaptureFrameProcessor.cleanup()`
  (Pipecat's per-processor teardown hook).

**Probe display fix (same finding).** `apps/nexa_stt_probe.py` originally
suppressed the real `END_OF_TURN → LISTENING` transition and printed a
`LISTENING` line itself once transcription finished — but a real
`USER_SPEAKING` for the *next* utterance can legitimately occur first,
making that printed line false. Fixed: `on_event` prints every real
`VoiceStateMachine` transition unconditionally, in order; the
transcription/error callbacks print only STT-specific output (text,
latency, running max-concurrency) and never claim a voice state — enforced
by `tests/test_stt_probe_state_display.py` (`ast`-based: the callback
functions must not reference `VoiceState`/`state_machine` or print a
`"state:"` line).

## 7. Audio pre-roll — computed and empirically verified, not guessed

With `start_secs=0.2` (M2.1's `DEFAULT_VAD_PARAMS`), Silero only confirms
`VADUserStartedSpeakingFrame` after speech frames accumulate — audio arriving
before that confirmation is real speech (the utterance's own beginning), not
noise, and would otherwise be lost, truncating the first word. `UtteranceBuffer`
(`src/nexa/stt/utterance_buffer.py`) fixes this with a ring buffer that always
holds the most recent `PRE_ROLL_MS` of audio while not yet capturing, seeded
into the utterance the moment speech is confirmed.

**`PRE_ROLL_MS = 500`, justified two ways, per the task's explicit "test it
deterministically, do not just guess a large buffer" instruction:**

- Theoretical minimum: Silero confirms speech after `start_secs /
  (512/16000)` = 6.25 → 6 frames × 32 ms = **192 ms** of audio must already
  have arrived before the confirmation frame fires.
- Empirical measurement: the real `SileroVADAnalyzer` run against 3 real
  R0006 speech fixtures with manually-verified true onset (RMS profile
  inspection) measured actual confirmation delay at **352 ms, 352 ms, and
  288 ms** — nearly double the theoretical minimum. Using only the
  theoretical number would have truncated real speech.

500 ms covers the empirical worst case with a safety margin. `UtteranceBuffer`
is pure Python (no Pipecat dependency) — directly unit tested in
`tests/test_stt_utterance_buffer.py` (ring never exceeds the configured
budget; pre-roll audio survives into the utterance; no audio leaks between
turns; trailing audio up to the stop event is never truncated).

**Real hardware confirmation (2026-09-05/06, `VERIFIED FACT`, operator
Andrzej)**: across both Polish and English test sentences, **the first
word/syllable was never truncated** — the explicit acceptance condition for
this section. Spelling variants of the invented wake word ("NeXa"/"Nexa") in
the transcription are STT accuracy, not a pre-roll failure.

## 8. Real hardware test — PL/EN transcription + concurrency

`VERIFIED FACT`, this Pi, real reSpeaker XVF3800, operator Andrzej,
2026-09-05/06:

**PL/EN transcription + pre-roll acceptance** — Polish ("Cześć NeXa.", "Co to
jest teleportacja?", one longer sentence) and English ("Hello NeXa.", "What
is the speed of light?", one longer sentence) all transcribed with correct
first words and preserved meaning; minor STT accuracy variance on the
invented wake word only. `--language pl`/`--language en` explicit hints used
throughout — no auto-detect exists in the normal path (`Language` has no
`AUTO` member; `WhisperCppTranscriber.transcribe()` raises `TypeError` for
anything that is not the `Language` enum).

**Concurrency retest, after the `SerialTranscriptionQueue` fix** — operator
said "Hello NeXa." then "How are you?" with the second beginning while the
first was still transcribing:

- Both utterances were captured.
- Both were transcribed.
- Results stayed in FIFO order.
- `max concurrent STT executions observed this session: 1` (the probe's own
  live instrumentation — see §6) — proof, not something inferred from
  timestamps.
- The audio/VAD pipeline stayed responsive throughout.
- Clean shutdown reached `IDLE`.
- One earlier combined transcription ("Hello Nexa. How are you?" as one
  utterance) reflects M2.1's `stop_secs=1.0` VAD baseline correctly holding
  a short inter-phrase gap as one acoustic utterance — not a queue defect.

## 9. Same-corpus regression vs. R0006

`docs/research/m2_2_stt_regression/run_stt_regression.py` runs the exact
pinned binary/model/thread-count `WhisperCppTranscriber` uses against the
same 12 R0006 PL/EN fixtures. Result (`stt_regression_results.json`):

| Metric | R0006 (scratch build) | M2.2 (product code) |
|---|---|---|
| Avg WER | ~0.354 | 0.3542 |
| Avg latency | ~1.69 s | 1.648 s |
| Avg peak RSS | ~221 MB | 220.5 MB |

No meaningful regression — the product implementation matches the earlier
scratch-build measurement closely on all three axes.

## 10. Thread / CPU / RAM / thermal — measured on this Pi

`VERIFIED FACT`, 2026-09-05, sampled during the regression run above (12
whisper.cpp invocations back to back): **4 threads** (R0006's measured
baseline, `DEFAULT_THREADS` in `config.py` — an explicit, verified constant,
not an undocumented CLI default), peak RSS ~220.5 MB per invocation,
temperature rose 53.2°C → 60.4°C over the run, **zero throttling**
(`vcgencmd get_throttled` = `0x0` before and after). Consistent with R0006's
resource-budget spike, which already found no RAM/swap/thermal ceiling for
STT alone — CPU *contention* (§6, now fixed) was the real risk, not raw
per-call cost.

## 11. Known limitations, stated plainly

- **No STT provider abstraction beyond `SpeechTranscriber`**: per task
  scope, no second STT engine was built, so `SpeechTranscriber` has exactly
  one implementation. If a second engine is ever added, this protocol is
  the seam — it does not need to change shape for that.
- **Auto language switching is out of scope**: `pl`/`en` are selected
  explicitly per session (`--language`), not per utterance. Automatic
  language detection/arbitration remains explicitly deferred, not designed
  here (ADR-0003 D5).
- **`SerialTranscriptionQueue.shutdown()` waits for in-flight work**: if a
  transcription is mid-subprocess when shutdown is requested, shutdown waits
  for it to finish (up to `timeout_s`, default 30s) rather than killing it —
  acceptable for M2.2 (no barge-in yet); M2.5's barge-in design will need to
  revisit this once interrupting STT itself becomes a real requirement.
- **`PRE_ROLL_MS=500` and `stop_secs=1.0` remain evidence-backed baselines,
  not permanently frozen values** — same non-frozen-baseline discipline as
  every other M2.1/M2.2 choice (ADR-0002 D4, ADR-0003 D4/D6): a future
  substage may justify a different value with its own evidence.
- **No `ConversationSession` adapter, no TTS, no barge-in** — M2.3+,
  per ADR-0003's substage table.
