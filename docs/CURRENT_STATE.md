# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-08-31
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — see `git log -1` for the current hash
- **Latest report:** `docs/reports/R0002_m1_natural_conversation_research_20260831.md`
- **Current milestone:** **M1 — Natural Text Conversation**
- **Current substage:** **M1.0 — Research & Empirical Baseline — COMPLETE**
- **Next substage:** **M1.1 — Minimal Canonical Text Conversation Path** (not started)
- **Current objective:** none active — M1.0 delivered research + baseline; M1.1
  has not begun.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project; foundation tests pass
  (`python -m unittest discover -s tests`).
- Documentation + ADR + report systems in place (`R0001`, `R0002`; `ADR-0001`,
  `ADR-0002`).
- M1.0 research complete: Pi hardware/runtime/model inventory verified;
  legacy conversation stack audited (read-only); external research done;
  local models benchmarked on the Pi.
- Throwaway benchmark harness `scripts/m1_bench/` works (stdlib only, no deps);
  raw results under `docs/research/m1_bench_results/`.

## What is partial

- Conversation-quality scoring in
  `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md` §11–§12A is
  **agent-assisted** (Claude vs transcripts + rubric), **not operator-confirmed**.
  An operator pass is owed before the M1.1 baseline model is settled.
- `qwen3:4b` Polish quality measured **weak (2/5)**; Bielik-4.5B Polish better
  (3/5) but 2× slower / 8k-ctx / truncates / weak English. M1.1 owes: a better
  PL persona and a fair Bielik re-test (recommended params + a Q4_K_M GGUF).
- `llama.cpp`-direct vs Ollama head-to-head not measured (Phase-9 follow-up).
- `CONV-MIX-1` and smaller-model conversation runs not executed (perf only).

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
  D4 **confirmed by measurement** (M1.0 §12A head-to-head vs Bielik-4.5B: qwen3
  wins speed/English/context/RAM/quant; Bielik wins only Polish grammar), with
  a recorded caveat that qwen3's Polish needs M1.1 work.
- No product code exists for any of the above yet.

## Current test status

- `tests/test_foundation.py`: **PASS** via `python -m unittest`. `pytest`/`ruff`
  still not installed (system interpreter); `dev` extras for M1.1.
- `scripts/m1_bench/bench.py`: smoke-tested and used for real measurements.

## Active architectural decisions

- **ADR-0001** — NeXa project foundation (Accepted).
- **ADR-0002** — Text conversation foundation (Accepted for architecture /
  provider boundary / first runtime; baseline-not-frozen for the first model).

## Current focus

- None active. M1.0 closed. Awaiting start of M1.1.

## Exact next recommended task

**M1.1 — Minimal Canonical Text Conversation Path.** Create `./.venv` + first
real dependency; implement ADR-0002 D1's small type set in `src/nexa/`
(`ModelProvider` + `LocalModelProvider` (Ollama, streaming) + `ConversationContext`
(bounded) + `ConversationSession` + `ConversationTurn` + `StreamingResponse`) +
one versioned system persona in `configs/`; deterministic turn-contract tests
with a fake provider + one marked live Ollama integration test; score the M1.0
PL/EN transcripts and confirm/deny the baseline model; add a `llama-server`
provider adapter. No MAS, no model router, no capability layer, no fallback
model, no memory, no voice, no UI.
