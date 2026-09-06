# TEST STRATEGY

Status: **M0.** Philosophy and conventions only; the concrete suites arrive with
their milestones.

---

## Philosophy

- **Real behaviour over superficial mocks.** A test that only asserts a mock was
  called proves nothing. Mock at true external boundaries (network, hardware,
  paid APIs); exercise real logic otherwise.
- **Deterministic unit tests** where the logic is deterministic. No sleeps, no
  network, no wall-clock dependence, seeded randomness.
- **Integration tests at subsystem boundaries.** Every conceptual boundary in
  `docs/architecture/FOUNDATION_ARCHITECTURE.md` gets integration coverage where
  it meets another boundary.
- **Hardware tests on actual hardware.** Anything depending on a physical device
  (mic, camera, motors, displays, accelerators) is tested on real hardware and
  clearly marked; it must not run or fail in a hardware-less environment.
- **Conversation quality is benchmarked empirically**, not asserted by hand.
  Benchmarks produce scores over a fixed set of cases and track them over time.
- **Failed experiments are recorded**, not deleted — see
  `docs/troubleshooting/README.md`.

## Test tiers

| Tier | Scope | Runs where | Speed |
|---|---|---|---|
| `unit` | one module, no I/O | everywhere, every commit | fast |
| `integration` | 2+ modules across a boundary | everywhere (external deps faked at the seam) | medium |
| `hardware` | real devices | tagged; only on the target machine | slow |
| `benchmark` | quality / latency over a fixed case set | on demand / scheduled | slow |

## Stable test IDs

Where a test represents a *capability contract* (not just an implementation
detail), give it a stable ID so it can be referenced across reports and ADRs even
if the code moves.

- Format: `<AREA>-<SUBAREA>-<NNN>`, e.g. `CONV-PL-001`, `CONV-EN-001` for natural
  conversation quality (Polish / English), `VOICE-STT-001`, `DEV-AWARE-001`.
- Record the ID in the test's docstring and in the milestone's report.
- IDs are append-only: never renumber, mark retired ones as such.

### Placeholder benchmark IDs (NOT implemented at M0)

| ID | Intent | Milestone |
|---|---|---|
| `CONV-EN-001` | Natural English conversation quality — baseline case set | M1 |
| `CONV-PL-001` | Natural Polish conversation quality — baseline case set | M1 |
| `CONV-*-*` | Further conversation cases (context use, refusal, persona) | M1+ |
| `VOICE-TURN-001` | End-to-end voice turn latency budget | M2 |
| `CTX-001` | Context coherence across a multi-turn session | M3 |
| `DEV-AWARE-001` | Correct hardware/body report on a known device | M4 |
| `MEM-RW-001` | Long-term memory write→retrieve round trip | M5 |

These are documented placeholders only. Do not implement them until their
milestone, and do not let them exist as skipped tests that imply coverage.

## M0 status

- `tests/test_foundation.py` — tier `unit`. Asserts the package imports, exposes a
  string `__version__`, and that the foundation doc/convention files exist.
- Runnable with `python -m unittest discover -s tests` (no third-party dep) and
  under `pytest` once the `dev` extra is installed.
- No `hardware` or `benchmark` tests exist yet.

## M1.1 status

- `tests/fakes.py` — `FakeModelProvider`, a deterministic in-memory
  `ModelProvider`. Never imported from `src/nexa/`.
- `tests/test_context.py`, `tests/test_streaming_response.py`,
  `tests/test_conversation_session.py` — tier `unit`. The canonical turn path
  (`ConversationSession` → `ConversationContext` → `ModelProvider`) tested
  entirely against `FakeModelProvider`; no network, no Ollama.
- `tests/test_ollama_provider.py`, `tests/test_llama_server_provider.py` — tier
  `integration`. Each provider tested against a small stdlib
  `http.server`-based fake of its real wire protocol (NDJSON / OpenAI SSE) —
  mocking at the true external (network) boundary per this doc's philosophy,
  not by mocking the provider itself.
- `tests/test_live_ollama_integration.py` — tier `integration`, real backend.
  Talks to a real local Ollama + the frozen `gemma4:e4b` baseline
  (ADR-0002 Amendment 2). **Not run by default** (loads a ~10 GB model) — set
  `NEXA_RUN_LIVE_TESTS=1` to run it explicitly.
- All `unit`/fake-server `integration` tests run with plain
  `python -m unittest discover -s tests` or `pytest` — no Ollama required, no
  new runtime dependency (stdlib only).
- Beyond the automated tiers above, M1.1 also has a **human-acceptance**
  record — the owner personally using the real path (`apps/nexa_chat.py`) and
  giving an explicit verdict. Not a test suite tier; see
  `docs/reports/R0004_m1_1_canonical_text_conversation_path_20260905.md`
  ("Operator acceptance" addendum) and `docs/CURRENT_STATE.md`.

## M2.1 status

- `tests/test_voice_state.py` — tier `unit`. `VoiceStateMachine` — pure state
  logic, no audio, no Pipecat.
- `tests/test_voice_frame_mapping.py` — tier `unit`. Real Pipecat `Frame`
  instances (plain, side-effect-free objects) fed to the pure
  `apply_frame_to_state_machine()` function — deliberately extracted so the
  frame→state mapping is testable without a running Pipecat
  processor/task-manager lifecycle.
- `tests/test_voice_device.py` — tier `unit`. PyAudio device-name resolution
  against a fake device list — mocked at the PyAudio boundary, no real audio
  hardware.
- `tests/test_voice_architecture.py` — tier `unit`. `ast`-based import
  inspection: verifies `src/nexa/voice/` does not import
  `nexa.conversation`/`nexa.providers`/`nexa.bootstrap`/`nexa.config`, i.e.
  no second conversation authority — checked structurally, not just by
  convention.
- `tests/test_voice_hardware_probe.py` — tier `integration`, real hardware.
  Opens the real reSpeaker XVF3800 via the real Pipecat pipeline, confirms
  LISTENING is reached and shutdown is clean. **Not run by default** — set
  `NEXA_RUN_VOICE_HARDWARE_TEST=1`.
- Beyond the automated tiers, M2.1 has the same **human-acceptance** pattern
  as M1.1 — the owner personally using `apps/nexa_voice_probe.py` against
  real speech, including a genuine evidence-based retuning cycle for VAD
  endpointing. See `docs/reports/R0007_m2_1_local_audio_vad_foundation_20260905.md`
  ("REAL HARDWARE TEST") and `docs/research/m2_1_vad_calibration/` (a
  deterministic offline calibration script + raw results, used specifically
  to avoid relying on further human-timed pause measurements once those
  proved imprecise).

## M2.2 status

- `tests/test_stt_config.py` — tier `unit`. `Language` (strict `pl`/`en`,
  no `AUTO` member) and `WhisperCppConfig` — typed, frozen, explicit.
- `tests/test_stt_transcriber.py` — tier `unit`. `WhisperCppTranscriber`
  against a fake `subprocess.run` boundary (patched at
  `nexa.stt.transcriber.subprocess.run`) — mocking at the true external
  (subprocess) boundary per this doc's philosophy, not by mocking the
  transcriber itself. Covers command construction (explicit language/thread
  flags, no `shell=True`), JSON parsing (valid/malformed/missing output),
  subprocess failure/timeout, and temp-file cleanup.
- `tests/test_stt_utterance_buffer.py` — tier `unit`. `UtteranceBuffer` —
  pure logic, no Pipecat/hardware. Covers pre-roll retention, the ring's
  budget cap, no cross-turn audio leakage, and no trailing-audio truncation.
- `tests/test_stt_queue.py` — tier `unit`. `SerialTranscriptionQueue`
  against a fake slow transcriber — proves FIFO ordering, ≤1 execution in
  flight (`max_observed_concurrency`), non-blocking `submit()`, explicit
  bounded-overflow error, and clean shutdown with no orphan task. Added
  after a **real hardware finding** (2026-09-05/06): the original per-turn
  `create_task` dispatch could run two whisper.cpp subprocesses
  concurrently — these tests are the regression guard for that fix.
- `tests/test_voice_utterance_capture.py` — tier `unit`. The real
  `_UtteranceCaptureFrameProcessor` driven with real Pipecat `Frame`
  instances (no running pipeline/`TaskManager` — `push_frame` on an
  unlinked processor is a documented no-op; the processor's internal queue
  is started explicitly and drained via `shutdown()` per test).
- `tests/test_stt_architecture.py` — tier `unit`. `ast`-based import
  inspection (mirrors M2.1's `test_voice_architecture.py`): `src/nexa/stt/`
  imports no `nexa.conversation`/`nexa.providers`, references no TTS
  engine, and constructs no `ConversationSession`. Also verifies
  `VoiceRuntime`'s STT parameters default to `None` (M2.1 behavior
  preserved when STT isn't configured).
- `tests/test_stt_probe_state_display.py` — tier `unit`, `ast`-based.
  Regression test for a second **real hardware finding**: the probe
  originally printed a fake `LISTENING` line from the transcription
  callback, which could be false if a new `USER_SPEAKING` arrived first.
  Asserts the transcription/error callback functions never reference
  `VoiceState`/`state_machine` or print a `"state:"` line.
- `tests/test_stt_transcriber_live.py` — tier `integration`, real
  whisper.cpp binary+model (no microphone). Transcribes two real R0006
  fixtures and checks the recognized text contains the expected words.
  **Not run by default** — set `NEXA_RUN_LIVE_STT_TEST=1`.
- Beyond the automated tiers, M2.2 has the same **human-acceptance** pattern
  as M1.1/M2.1 — the owner personally using `apps/nexa_stt_probe.py` against
  real Polish and English speech, including a genuine fix-and-retest cycle
  for the concurrency defect above. See
  `docs/reports/R0008_m2_2_local_whisper_cpp_stt_20260906.md` ("REAL POLISH
  TEST", "REAL ENGLISH TEST", "REAL HARDWARE CONCURRENCY RETEST").
- `docs/research/m2_2_stt_regression/run_stt_regression.py` — a same-corpus
  regression script (not a `tests/` suite; throwaway research harness like
  M2.0A's spike scripts) comparing the product `WhisperCppTranscriber`
  against R0006's original scratch-build measurement on the identical 12
  fixtures.

## Tooling direction

- `pytest` as the runner (declared in `pyproject.toml` `dev` extras); config lives
  in `[tool.pytest.ini_options]` with `pythonpath = ["src"]`.
- `ruff` for lint/format (`dev` extras).
- CI is not set up during M0; a milestone that adds real code should add it.
