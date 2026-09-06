# R0008 — M2.2 Local whisper.cpp STT Foundation

- **Date:** 2026-09-05/06
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.2 — whisper.cpp STT
  Adapter + Explicit PL/EN Language Strategy**
- **Related:** `docs/decisions/ADR-0003_realtime_voice_foundation.md` (D4,
  D5, D7, D11 — the decision this implements),
  `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md` (verified architecture
  detail), `docs/reports/R0006_m2_voice_feasibility_spikes_20260905.md`
  (the whisper.cpp `base/q8_0` baseline this wires up),
  `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` (the pipeline
  this substage extends, not replaces)

---

## TASK RESULT

**PASS.** All 20 success criteria met, including a real hardware-testing
finding (a genuine concurrency defect in the first implementation) that
required one deliberate fix-and-retest cycle before PASS — disclosed in
full below, not smoothed over.

## M2.1 DOCUMENTATION CHECK

Completed and committed separately before M2.2 implementation began
(commit `cc09a5b`): corrected R0007's test-count arithmetic ("34 new" →
"31 new (30 deterministic + 1 opt-in hardware)", verified via a temporary
`git worktree` at the pre-M2.1 commit) and precisely restated the audio
output claim (stream open/write/close verified only — no audible sound was
played or confirmed). Both corrections stand; not reopened here.

## WHISPER.CPP VERSION

`ggml-org/whisper.cpp`, tag `v1.9.3` (= build tag `b4938`), commit
`371b5a7561823ab2bb32142d2751e35e7534727b`, published 2026-08-20 —
**verified via the GitHub API directly** (`releases/latest`, `/tags`), not
WebFetch, which returned a stale/incorrect date for the same tag.
Independently confirmed by actually cloning `--branch v1.9.3 --depth 1` and
checking `git log -1` matched exactly. Pinned as named constants in
`src/nexa/stt/config.py` (`WHISPER_CPP_TAG`, `WHISPER_CPP_COMMIT`,
`WHISPER_CPP_REPO`) — `scripts/setup_whisper_cpp.py` verifies the resolved
tag's actual commit matches the pin at clone time and raises loudly on a
mismatch, rather than silently building against a moved tag.

## BUILD

`cmake -B build -DCMAKE_BUILD_TYPE=Release` + `cmake --build build --config
Release -j<nproc> --target whisper-cli whisper-quantize` — correctly
detects this Pi's Cortex-A76 ARM64 (`-mcpu=cortex-a76+crc+crypto+dotprod`).
Built into `$NEXA_STT_DATA_DIR` (env override) → `$XDG_DATA_HOME/nexa/stt`
→ `~/.local/share/nexa/stt` (fallback) — **entirely outside this git repo**,
via `scripts/setup_whisper_cpp.py`, idempotent (`--force` to rebuild),
requires `git`/`cmake` on `PATH`, no `sudo`, never runs automatically at
NeXa import/runtime. Ran it for real end-to-end (fresh clone/build/download)
and confirmed a second run correctly skips every already-complete step.

## MODEL

`base` GGML downloaded via whisper.cpp's own `models/download-ggml-model.sh
base`, quantized to `q8_0` via `whisper-quantize` — R0006's measured
baseline (0.354 avg WER, ~1.69s avg latency, ~221MB RSS on the exact 12-file
PL/EN corpus). **Not silently changed.** Stored at
`~/.local/share/nexa/stt/models/ggml-base-q8_0.bin`, outside git.

## STT ARCHITECTURE

`src/nexa/stt/`: `config.py` (pinned version, `Language` enum, path
resolution, `WhisperCppConfig`), `errors.py` (explicit error hierarchy, no
silent fallback), `transcriber.py` (`TranscriptionResult`,
`SpeechTranscriber` boundary protocol, `WhisperCppTranscriber` — the only
implementation), `utterance_buffer.py` (`UtteranceBuffer`, pure, no Pipecat
dependency), `queue.py` (`SerialTranscriptionQueue`, pure, no Pipecat
dependency). `src/nexa/voice/runtime.py` gained
`_UtteranceCaptureFrameProcessor` and optional
`transcriber`/`language`/`on_transcription`/`on_transcription_error`
parameters on `VoiceRuntime` — omitting them reproduces the exact M2.1
pipeline (verified by `tests/test_stt_architecture.py`). Subprocess
invocation uses an explicit argument list, never `shell=True`; JSON output
(`-oj`) is parsed from a structured file, not scraped from text. No
`ConversationSession`/LLM/TTS import anywhere in `src/nexa/stt/` or
`src/nexa/voice/` — `ast`-verified (`tests/test_stt_architecture.py`), not
just asserted. Full detail: `M2_2_LOCAL_STT_ARCHITECTURE.md` §3-6.

**Real hardware finding — concurrency fix (2026-09-05/06).** The first cut
dispatched one independent `self.create_task(...)` per completed utterance.
Live testing (operator Andrzej) showed a short next utterance could reach
`END_OF_TURN` before the previous whisper.cpp subprocess finished — a real
risk of two concurrent 4-thread whisper.cpp processes on a Pi already shown
to have CPU contention (R0006). Fixed with `SerialTranscriptionQueue`: audio
capture (`submit()`) never blocks, but exactly one `transcribe()` call
executes at a time, strictly FIFO, bounded (`max_queue_size=8`, explicit
`SttQueueOverflowError` on overflow — never a silent drop), with an explicit
`shutdown()` that drains in-flight work. 11 new deterministic tests
(`tests/test_stt_queue.py`) prove FIFO ordering, serialized execution
(`max_observed_concurrency` never exceeds 1), non-blocking `submit()`,
correct error propagation, and clean shutdown with no orphan task — using a
fake slow transcriber, no real whisper.cpp needed. Confirmed again on real
hardware after the fix (see "REAL HARDWARE CONCURRENCY RETEST" below).

**Real hardware finding — probe state-display bug (same session).**
`apps/nexa_stt_probe.py` originally suppressed the real
`END_OF_TURN → LISTENING` transition and printed a `LISTENING` line itself
once transcription finished — a real `USER_SPEAKING` for the *next*
utterance can legitimately arrive first, making that printed line false.
Fixed: `on_event` now prints every real `VoiceStateMachine` transition
unconditionally; the transcription/error callbacks print only STT-specific
output and never claim a voice state. Enforced by a new regression test,
`tests/test_stt_probe_state_display.py` (`ast`-based: the callback functions
must not reference `VoiceState`/`state_machine` or print a `"state:"` line).

## AUDIO PRE-ROLL

`PRE_ROLL_MS = 500`, justified two ways (not guessed):

- Theoretical minimum: `start_secs / (512/16000)` = 6.25 → 6 frames × 32ms =
  **192ms** must already have arrived before Silero confirms speech-start.
- Empirical measurement: the real `SileroVADAnalyzer` run against 3 real
  R0006 fixtures with manually-verified true onset (RMS inspection) measured
  actual confirmation delay at **352ms, 352ms, 288ms** — nearly double the
  theoretical minimum.

`UtteranceBuffer` (pure, no Pipecat dependency) implements a ring buffer
holding the most recent `PRE_ROLL_MS` while not capturing, seeded into the
utterance the instant speech is confirmed. 9 deterministic tests
(`tests/test_stt_utterance_buffer.py`): ring never exceeds budget, pre-roll
survives into the utterance, no cross-turn leakage, trailing audio never
truncated. **Real hardware confirmation (operator Andrzej, both languages):
the first word/syllable was never truncated** — the explicit acceptance
condition.

## PL/EN LANGUAGE STRATEGY

`Language` (`config.py`) is a `StrEnum` with exactly two members, `PL`/`EN`
— **no `AUTO` member exists**, structurally, not just by convention
(`tests/test_stt_config.py` asserts `Language("auto")` raises `ValueError`
and `"AUTO"` is not in `Language.__members__`). `WhisperCppTranscriber.
transcribe()` raises `TypeError` if `language` is not a `Language` instance.
`apps/nexa_stt_probe.py --language {pl,en}` (default `pl`) is the practical
dev interface. No automatic language switching or dual-model arbitration —
explicit selection only, per task scope.

## REAL POLISH TEST

Operator Andrzej, real reSpeaker XVF3800, `--language pl`:

| Phrase | Result |
|---|---|
| "Cześć NeXa." | "Cześć Cię, neksa!" — correct except the invented wake word's spelling (STT accuracy, not pre-roll) |
| "Co to jest teleportacja?" | exact |
| longer natural sentence | first words and overall meaning preserved |

First word/syllable **not truncated** in any case.

## REAL ENGLISH TEST

Operator Andrzej, real reSpeaker XVF3800, `--language en`:

| Phrase | Result |
|---|---|
| "Hello NeXa." | "Hello, Nexa." — essentially exact |
| "What is the speed of light?" | exact |
| longer natural sentence | first words and meaning preserved, minor recognition errors only |

First word/syllable **not truncated** in any case.

## REAL HARDWARE CONCURRENCY RETEST

After the `SerialTranscriptionQueue` fix, operator said "Hello NeXa." then
"How are you?" with the second beginning while the first was still
transcribing:

- Both utterances captured.
- Both transcribed.
- Result order preserved (FIFO).
- **`max concurrent STT executions observed this session: 1`** — read
  directly from the probe's own live instrumentation
  (`VoiceRuntime.max_observed_stt_concurrency`), not inferred from
  timestamps.
- Audio/VAD pipeline remained responsive throughout.
- Clean shutdown reached `IDLE`.
- One combined transcription ("Hello Nexa. How are you?" as a single
  utterance) reflects M2.1's `stop_secs=1.0` correctly holding a short
  inter-phrase gap as one acoustic utterance — not a queue defect.

## SAME-CORPUS REGRESSION

`docs/research/m2_2_stt_regression/run_stt_regression.py` ran the exact
pinned binary/model/thread-count `WhisperCppTranscriber` uses against the
same 12 R0006 PL/EN fixtures:

| Metric | R0006 (scratch build) | M2.2 (product code) |
|---|---|---|
| Avg WER | ~0.354 | 0.3542 |
| Avg latency | ~1.69 s | 1.648 s |
| Avg peak RSS | ~221 MB | 220.5 MB |

No meaningful regression on any axis. Full per-file results in
`docs/research/m2_2_stt_regression/stt_regression_results.json`.

## LATENCY

Same-corpus regression: avg 1.648s wall-clock per utterance (base/q8_0, 4
threads) — matches R0006. Real hardware transcriptions during operator
testing were consistently in the 1.5-2s range, subjectively confirmed
acceptable by the operator for a non-conversational STT-only probe.

## CPU

4 threads (`DEFAULT_THREADS` in `config.py`) — R0006's measured baseline,
an explicit, verified constant, not an undocumented CLI default.

## RAM

Peak RSS ~220.5 MB per whisper.cpp invocation (measured via `/proc/<pid>/status`
sampling during the regression run) — matches R0006's ~221 MB.

## THERMAL

Temperature rose 53.2°C → 60.4°C over 12 back-to-back whisper.cpp
invocations; **zero throttling** (`vcgencmd get_throttled` = `0x0` before
and after). Consistent with R0006's resource-budget spike finding that STT
alone carries no RAM/swap/thermal ceiling on this Pi.

## TESTS

118 total (64 pre-existing M1.1/M2.1 tests unaffected + 54 new M2.2 tests):

| File | Tier | Count |
|---|---|---|
| `tests/test_stt_config.py` | unit | 6 |
| `tests/test_stt_transcriber.py` | unit (fake `subprocess.run` boundary) | 13 |
| `tests/test_stt_utterance_buffer.py` | unit | 9 |
| `tests/test_stt_queue.py` | unit (fake slow transcriber) | 11 |
| `tests/test_voice_utterance_capture.py` | unit (real Pipecat `Frame` instances, no pipeline lifecycle) | 5 |
| `tests/test_stt_architecture.py` | unit (`ast`-based import inspection) | 6 |
| `tests/test_stt_probe_state_display.py` | unit (`ast`-based, regression for the state-display bug) | 2 |
| `tests/test_stt_transcriber_live.py` | integration, real whisper.cpp binary+model, opt-in (`NEXA_RUN_LIVE_STT_TEST=1`) | 2 |
| `apps/nexa_stt_probe.py` | manual, human-in-the-loop, not automatable | — |

`python -m unittest discover -s tests` and `pytest`: **118 tests, all
pass**, 4 intentionally skipped (2 pre-existing opt-in tests + 2 of this
substage's own opt-in live-STT tests). `ruff check src apps tests`: clean.
The opt-in live whisper.cpp integration test was run for real
(`NEXA_RUN_LIVE_STT_TEST=1`): passed against both fixtures. All 18 required
test scenarios are covered: typed PL/EN config, auto structurally rejected,
explicit language/thread-count in the command, missing binary/model →
explicit errors, subprocess failure/timeout propagate, valid/malformed
output parsing, utterance-buffer reset/pre-roll/no-cross-turn-leak/no-
truncation, temp file removal, no `ConversationSession` call, no LLM/
provider import into STT, no TTS path, M2.1 state-machine behavior intact —
plus the concurrency-specific scenarios the real hardware finding required
(FIFO ordering, serialized execution, non-blocking submit, clean shutdown,
no orphan task) and the state-display regression test.

## ARCHITECTURE

`docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md` — new, verified. Full
design rationale, the concurrency fix, the pre-roll derivation, and the real
hardware evidence chain.

## DOCUMENTATION

- `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md` — new.
- This report (`R0008`).
- `docs/research/m2_2_stt_regression/` — new: the regression script and its
  raw JSON results.
- `docs/CURRENT_STATE.md`, `docs/testing/TEST_STRATEGY.md` — updated.
- `docs/architecture/FOUNDATION_ARCHITECTURE.md` — one pointer added to the
  Voice section, same pattern as M2.1's pointer.
- `docs/decisions/ADR-0003_realtime_voice_foundation.md` — **not modified**.
  Nothing in this substage exposed a contradiction; the `SerialTranscriptionQueue`
  fix is exactly the kind of thread/CPU-budget discipline ADR-0003 D7
  already anticipated, applied here at the STT-execution level.
- `pyproject.toml` — unchanged (`whisper.cpp` is an external binary
  dependency, not a Python package).

## COMMIT

One commit, implementation + tests + docs (hash recorded below in the final
response). Not pushed.

## UNRESOLVED

- `SerialTranscriptionQueue.shutdown()` waits for in-flight work to finish
  (up to `timeout_s`, default 30s) rather than killing it — acceptable for
  M2.2 (no barge-in yet); M2.5's barge-in design will need to revisit this
  once interrupting STT itself becomes a real requirement.
- No STT provider abstraction beyond the single-implementation
  `SpeechTranscriber` protocol — correct per task scope (no second engine
  built), but a genuine seam for one if ever needed.
- `PRE_ROLL_MS=500` and `stop_secs=1.0` remain evidence-backed baselines,
  not permanently frozen — a future substage (e.g. once LLM/TTS latency is
  also in the loop) may justify different values with their own evidence.
- Auto language switching / dual-model arbitration remains explicitly
  deferred (ADR-0003 D5) — `pl`/`en` are selected once per session, not
  per-utterance.

## CURRENT VERIFIED STATE

M2.2 implemented, tested, and operator-confirmed on real hardware across a
genuine fix-and-retest cycle (the concurrency defect and the state-display
bug were both real hardware findings, not hypothetical). Local whisper.cpp
STT (`base/q8_0`, explicit PL/EN hint, pre-roll-preserving, serially
executed) works end-to-end from real microphone input to real text output.
No LLM, no `ConversationSession` adapter, no TTS, no barge-in exists yet —
`ConversationSession` is completely unaffected, no second conversation
authority was created. M2.1's VAD behavior is unchanged and its test suite
still passes in full.

## NEXT RECOMMENDED ACTION

Start **M2.3 — the `ConversationSession` voice adapter** (ADR-0003's
substage table): a thin `FrameProcessor` (or equivalent) that takes a
`TranscriptionResult`'s text from M2.2's `on_transcription` callback and
feeds it into the **existing, unchanged** `ConversationSession` (M1.1) as a
user turn — the same canonical path `apps/nexa_chat.py` already uses, now
driven by voice input instead of typed input. Explicitly no second
history/persona/model choice for voice (ADR-0003 D2). The frozen
`gemma4:e4b` baseline stays the model. Still no TTS output and no barge-in
in M2.3 — those remain M2.4/M2.5. `SerialTranscriptionQueue`'s FIFO-ordered
results are exactly what a session-per-turn feed needs; no new
ordering/concurrency work should be required for M2.3 to consume them
correctly.

## AGENTS.md: REVIEWED

No changes required — M2.2 followed the existing evidence/labeling/ADR/
report discipline without exposing any gap in `AGENTS.md` itself.

## LEGACY NEXA USED

NO — this substage's evidence came from M2.0A's already-copied fixtures
(`docs/research/m2_voice_spikes/`), the pinned whisper.cpp upstream, and
real hardware testing, not new legacy repo reads.

## EXTERNAL RESEARCH USED

YES — `ggml-org/whisper.cpp` GitHub API (releases/tags) and the actual
cloned `v1.9.3` source/CLI `--help` output (verified directly, not from
memory/tutorials) for the exact version, build flags, and CLI flag names
(`-oj`, `-of`, `-np`, `-nt`, `-t`, `-l`, `-m`, `-f`).
