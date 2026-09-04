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

## Tooling direction

- `pytest` as the runner (declared in `pyproject.toml` `dev` extras); config lives
  in `[tool.pytest.ini_options]` with `pythonpath = ["src"]`.
- `ruff` for lint/format (`dev` extras).
- CI is not set up during M0; a milestone that adds real code should add it.
