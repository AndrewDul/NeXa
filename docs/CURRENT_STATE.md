# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-09-02
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — see `git log -1` for the current hash (1+ commit ahead of
  `origin/main`; **not pushed**)
- **Latest report:** `docs/reports/R0003_m1_0b_current_small_model_sweep_20260901.md`
- **Current milestone:** **M1 — Natural Text Conversation**
- **Current substage:** **M1.0B — Current Small-Model Sweep — COMPLETE**
  (research + benchmark only; M1.0 stays COMPLETE and unrewritten)
- **Next substage:** **M1.1 — Minimal Canonical Text Conversation Path** (not started)
- **Current objective:** none active — M1.0B delivered a fresh model sweep;
  the owed step is the operator blind test, then M1.1.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project; foundation tests pass
  (`python -m unittest discover -s tests`).
- Documentation + ADR + report systems in place (`R0001`, `R0002`, `R0003`;
  `ADR-0001`, `ADR-0002` + its M1.0B amendment).
- M1.0 research complete: Pi hardware/runtime/model inventory verified;
  legacy conversation stack audited (read-only); external research done;
  local models benchmarked on the Pi.
- **M1.0B complete:** 8 current small models (Qwen3.5 ×2, Gemma 4 ×2, Phi-4-mini,
  Bielik ×2 configs, + the `qwen3:4b-instruct` control) benchmarked on the Pi —
  perf + `CONV-PL-S1` + `CONV-EN-S1` for all; Phase 2 deep-dive
  (`CONV-MIX-1`, `CONV-NATURAL-S1`, `CONV-HONESTY-S1`, persona A/B,
  standardized-vs-recommended, long-context decay, weighted score) for the 3
  finalists. Ollama upgraded 0.30.10 → 0.33.2 for valid Qwen3.5/Gemma 4 runs.
- Throwaway benchmark harness `scripts/m1_bench/` works (stdlib only, no deps);
  raw results under `docs/research/m1_bench_results/` (M1.0B under `.../m1_0b/`).

## What is partial

- **All** M1.0 + M1.0B conversation-quality scores are **`AGENT-ASSISTED`**
  (Claude vs transcripts + rubric), **not operator-confirmed**. The operator
  blind test (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`) is prepared
  with the 3 finalists (sealed A/B/C) but **not run** — it is the owed step
  before an M1.1 baseline model is frozen.
- **M1.0B model picture:** `gemma4:e4b` (PL 4/5, EN 4/5) is the quality leader;
  `gemma4:e2b` (PL 3.5/5, EN 4/5, ~2× faster, flattest long-context decay) is
  the recommended M1.1 baseline candidate; `qwen3:4b-instruct` (PL 3/5, EN 3.5/5)
  is the reliable incumbent / safe fallback; `qwen3.5:2b` is English-only
  (broken Polish + a honesty-probe hallucination). Bielik re-test at temp 0.1 +
  Q4_K_M did **not** flip the baseline (Q8_0 weak EN / no TTFT warm-up; 3rd-party
  Q4_K_M is a reliability failure). Full detail: `R0003`, sweep doc §8–§19.
- `llama.cpp`-direct vs Ollama head-to-head **still not measured** — blocked by
  Ollama blob-store permissions (`0700`/`ollama`-owned); needs an operator
  decision.
- Incumbent `qwen3:4b-instruct` was carried into the M1.0B head-to-head on
  Phase 1 data only (not run through the Phase 2 battery).

## What is not implemented (by design)

- The M1 conversation architecture itself (M1.1). `src/nexa/` is still the
  placeholder package.
- Realtime voice (M2), robust context (M3), device awareness / capability
  registry (M4), long-term memory (M5), and everything later.
- No Python virtual environment yet — created in M1.1 with the first real
  dependency.

## Known problems / notes

- `OBSERVATION`: the login shell's `python3`/`pip` resolve into the **legacy**
  repo's `.venv`. NeXa must always use its own `./.venv`.
- `VERIFIED FACT`: on this Pi, neither the V3D GPU (Vulkan) nor the Hailo-10H
  helps M1 LLM inference — CPU is the only viable path (see research doc §2.2–2.3).
- `VERIFIED FACT`: legacy `smart-desk-ai-assistant/config/settings.json` `/llm`
  block is stale (points at a llama-server + Qwen2.5-1.5B path the MAS does not
  actually use). Legacy is reference-only; not our file to fix.

## Current architecture state

- Conceptual boundaries: `docs/architecture/FOUNDATION_ARCHITECTURE.md`
  (conceptual only).
- **ADR-0002 (Accepted)** sets the M1 direction: one minimal canonical
  text-conversation path (`ConversationSession` → `ConversationContext` →
  `ModelProvider` → streamed tokens); model access via a minimal
  OpenAI-chat-shaped `ModelProvider` abstraction; **Ollama** as the first
  `LocalModelProvider` implementation (`llama.cpp` second / portability);
  `qwen3:4b-instruct` as the first baseline model — **baseline, not frozen**.
- **ADR-0002 M1.0B amendment (2026-09-02, informational — D1–D4 unchanged):**
  the fair Bielik re-test is done and does **not** flip the baseline (D4 caveat
  (b) resolved); a better Polish persona lifted `qwen3:4b-instruct` PL 2/5 → 3/5
  (caveat (a) partially addressed); new evidence — `gemma4:e4b` / `gemma4:e2b`
  out-converse the incumbent in both languages. The M1.1 baseline-model choice
  is now flagged for the operator blind test; a change would be a new/superseding
  ADR, not a silent edit.
- No product code exists for any of the above yet.

## Current test status

- `tests/test_foundation.py`: **PASS** via `python -m unittest`. `pytest`/`ruff`
  still not installed (system interpreter); `dev` extras for M1.1.
- `scripts/m1_bench/bench.py`: smoke-tested and used for real measurements.

## Active architectural decisions

- **ADR-0001** — NeXa project foundation (Accepted).
- **ADR-0002** — Text conversation foundation (Accepted for architecture /
  provider boundary / first runtime; baseline-not-frozen for the first model)
  + **M1.0B amendment** (2026-09-02, informational).

## Current focus

- None active. M1.0B closed (research + benchmark). Awaiting the operator blind
  test, then M1.1.

## Exact next recommended task

**Run the operator blind test**
(`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`) — Andrzej talks blind to
the 3 M1.0B finalists (`gemma4:e4b`, `qwen3.5:2b`, `gemma4:e2b`, sealed as
A/B/C) for ~10–15 min each, scores them, then the mapping is revealed and
compared with `R0003`'s agent-assisted ranking + weighted score. That result is
the final qualitative input before the M1.1 baseline model is chosen.

**Then M1.1 — Minimal Canonical Text Conversation Path.** Create `./.venv` +
first real dependency; implement ADR-0002 D1's small type set in `src/nexa/`
(`ModelProvider` + `LocalModelProvider` (Ollama, streaming) + `ConversationContext`
(bounded) + `ConversationSession` + `ConversationTurn` + `StreamingResponse`) +
one versioned system persona in `configs/`; deterministic turn-contract tests
with a fake provider + one marked live Ollama integration test; freeze the
baseline model on the blind-test result; add a `llama-server` provider adapter.
No MAS, no model router, no capability layer, no fallback model, no memory, no
voice, no UI.
