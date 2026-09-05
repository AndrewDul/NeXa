# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-09-05
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — see `git log -1` for the current hash (not pushed)
- **Latest report:** `docs/reports/R0005_m2_realtime_voice_oss_research_20260905.md`
- **Current milestone:** **M1 — Natural Text Conversation — COMPLETE**;
  **M2 — Realtime Voice — RESEARCH DONE, implementation NOT STARTED**
- **Current substage:** M1.1 COMPLETE, `OPERATOR-CONFIRMED` (2026-09-05).
  M1.0B COMPLETE; operator blind test COMPLETE 2026-09-04; M1.1 local
  baseline FROZEN to `gemma4:e4b`, ADR-0002 Amendment 2, 2026-09-05. **M2
  open-source-first research COMPLETE (2026-09-05)** —
  `docs/research/M2_REALTIME_VOICE_RESEARCH.md`, `R0005` — no M2 product code,
  no M2 ADR yet.
- **Next substage:** **M2 implementation** (**NOT STARTED** — two small
  spikes recommended first, see "Exact next recommended task")
- **Current objective:** none active — M1.1 is implemented, tested, and
  human-accepted. M2's open-source-first architecture research
  (Pipecat/LiveKit Agents/STT/TTS/VAD candidates, license review,
  make-vs-build table) is done and awaiting the owner's read before an M2
  architecture ADR is written.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project; foundation tests pass
  (`python -m unittest discover -s tests`).
- Documentation + ADR + report systems in place (`R0001`–`R0005`; `ADR-0001`,
  `ADR-0002` + its M1.0B amendment + Amendment 2).
- **M2 open-source-first research complete** (`R0005`;
  `docs/research/M2_REALTIME_VOICE_RESEARCH.md`): deep-dived pipecat-ai/pipecat
  and livekit/agents source code (not just docs) against the requirement that
  `ConversationSession` stay the canonical brain — both frameworks have a
  clean, confirmed integration seam (`Agent.llm_node`/custom
  `FrameProcessor`) that can delegate straight to it. License review caught a
  real risk by unpacking an actual PyPI wheel: LiveKit Agents' *default*
  local VAD/turn-detector models carry a proprietary, framework-locked
  license (usable only inside LiveKit Agents) — the open `livekit-plugins-silero`
  (MIT) must be substituted explicitly. Piper's actively-maintained successor
  (`OHF-Voice/piper1-gpl`) is GPL-3.0 (the original MIT `rhasspy/piper` is
  archived) — recommended subprocess-only invocation, not an in-process
  import. Real Pi 5 evidence pulled from the legacy repo (read-only) anchors
  every feasibility claim: legacy's own measured numbers show
  faster-whisper's fast configs (`tiny`/`base`) are too inaccurate for Polish
  (WER 0.54–0.68) while the accurate config (`small`) is too slow
  (35–92 s); Piper's real Polish-voice latency was ~7 s/utterance (much
  slower than generic English-voice benchmarks suggest); and a full
  LLM-answered voice turn took 27.6–54 s end-to-end, with a real CPU-contention
  failure once recorded when other processes shared the same 4 cores. No
  prototype was built or run — see `R0005`'s "UNRESOLVED".
- **M1.1 — Minimal Canonical Text Conversation Path implemented and verified**
  (`R0004`; `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`):
  `ConversationSession` → `ConversationContext` (bounded, deterministic) →
  `ModelProvider` → streamed tokens → appended `ConversationTurn`, in
  `src/nexa/conversation/` + `src/nexa/providers/`. `LocalModelProvider`
  (Ollama) is the first, live-verified implementation, configured for the
  frozen baseline `gemma4:e4b`; `LlamaServerProvider` is a second
  implementation behind the same interface (portability path), verified at the
  interface level only — see limitations below. One versioned persona
  (`configs/personas/nexa_persona_v1.json`). Repo-local `./.venv` created
  (stdlib-only runtime — `pyproject.toml` `dependencies = []` unchanged; `dev`
  extras `pytest`/`ruff` installed into it). 33 deterministic tests (unit +
  fake-HTTP-server integration) pass via both `python -m unittest` and
  `pytest`; `ruff check` clean. One live Ollama integration test (opt-in,
  `NEXA_RUN_LIVE_TESTS=1`) passed against the real `gemma4:e4b`: one Polish and
  one English turn, streamed, history correct, model unloaded after. A manual
  multi-turn conversation through the real `apps/nexa_chat.py` CLI harness
  also verified end to end (real recall across turns, e.g. translating its own
  earlier Polish reply into English on request).
- **`OPERATOR-CONFIRMED` (2026-09-05):** Andrzej personally ran a real
  multi-turn conversation through `apps/nexa_chat.py` → `ConversationSession`
  → `ConversationContext` → `ModelProvider` → Ollama → `gemma4:e4b` (the exact
  canonical path, not a test-only harness) and recorded **"M1.1 HUMAN
  ACCEPTANCE: PASS"** — everything worked correctly and conversation quality
  was satisfactory. This is the human-acceptance evidence tier above the
  agent's own manual-CLI verification recorded in `R0004`; see `R0004`'s
  "Operator acceptance" addendum for the exact record.
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

- M1.0 + M1.0B conversation-quality scores were **`AGENT-ASSISTED`** (Claude vs
  transcripts + rubric). The **operator blind test is done and the baseline is
  frozen** (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md` §4/§8,
  `docs/testing/m1_operator_blind_results/`, `ADR-0002` Amendment 2): Andrzej
  talked blind to all 3 M1.0B finalists (~7–20 turns each), scored them, then
  the mapping was revealed. Result — `OPERATOR-CONFIRMED`: `gemma4:e4b` ranked
  best (4/5, everyday NeXa YES), `gemma4:e2b` second (3/5, maybe), `qwen3.5:2b`
  worst (2/5, no — operator independently caught a live hallucination, matching
  R0003's honesty-probe finding). This **superseded the M1.0B weighted-score
  `PROPOSAL`** (which had favored `gemma4:e2b` 4.0 vs. `gemma4:e4b` 3.9, driven
  by speed/RAM weighting, not a conversation-quality disagreement — see the
  test doc §8). The owner then explicitly signed off on `gemma4:e4b`'s
  RAM/latency cost (~10 GB resident, ~3 tok/s) and it is now **FROZEN** as the
  M1.1 local conversation baseline in `ADR-0002` Amendment 2 (2026-09-05).
- **M1.0B/blind-test model picture (historical vs. final — do not conflate):**
  the **M1.0B weighted-score recommendation** (`R0003`, sweep doc §19,
  `AGENT-ASSISTED`, 2026-09-01/02) was `gemma4:e2b` as the M1.1 baseline
  *candidate*. The **final, operator-confirmed decision** (2026-09-04/05) froze
  **`gemma4:e4b`** instead — see above. Both remain documented: `gemma4:e4b`
  (PL 4/5, EN 4/5) is the frozen baseline / quality leader; `gemma4:e2b`
  (PL 3.5/5, EN 4/5, ~2× faster, flattest long-context decay) is documented as
  the fast/low-RAM alternative; `qwen3:4b-instruct` (PL 3/5, EN 3.5/5) remains
  the reliable incumbent / safe fallback; `qwen3.5:2b` is English-only (broken
  Polish + a honesty-probe hallucination, operator-confirmed). Bielik re-test at
  temp 0.1 + Q4_K_M did **not** flip anything (Q8_0 weak EN / no TTFT warm-up;
  3rd-party Q4_K_M is a reliability failure). Full detail: `R0003`, sweep doc
  §8–§19 (historical M1.0B evidence and ranking — unedited); `ADR-0002`
  Amendment 2 (final decision).
- `llama.cpp`-direct vs Ollama head-to-head **still not measured** — blocked by
  Ollama blob-store permissions (`0700`/`ollama`-owned); needs an operator
  decision. The **same blocker** means `LlamaServerProvider` (built in M1.1)
  has never been run against a real `llama-server` process — no GGUF weight
  file is reachable outside that blob store on this machine. It is verified
  only at the interface/request-shaping level (fake-HTTP-server tests).
- Incumbent `qwen3:4b-instruct` was carried into the M1.0B head-to-head on
  Phase 1 data only (not run through the Phase 2 battery).
- Cancellation (`CancelToken`) is cooperative between received stream chunks;
  it cannot interrupt a token Ollama/llama.cpp is already computing
  mid-inference (neither backend's streaming HTTP API exposes finer-grained
  abort). Documented in `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §6 as a known
  limitation for M2 (voice/barge-in) to account for, not a silent gap.

## What is not implemented (by design)

- Realtime voice (M2), robust context beyond M1.1's bounded window (M3),
  device awareness / capability registry (M4), long-term memory (M5), and
  everything later.
- Model router / `AUTO`/`LOCAL ONLY`/`CLOUD PREFERRED` policy, MAS, tools,
  online model provider — all explicitly out of M1.1 scope (ADR-0002 D1,
  ROADMAP "Later").

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
  (conceptual only, except two pointers into the doc below).
- **Real (`VERIFIED FACT`):** `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
  — the Conversation and (local) Model Providers boundaries, implemented in
  `src/nexa/conversation/` + `src/nexa/providers/`.
- **ADR-0002 (Accepted)** sets the M1 direction: one minimal canonical
  text-conversation path (`ConversationSession` → `ConversationContext` →
  `ModelProvider` → streamed tokens); model access via a minimal
  OpenAI-chat-shaped `ModelProvider` abstraction; **Ollama** as the first
  `LocalModelProvider` implementation (`llama.cpp` second / portability) —
  **both now implemented**, Ollama live-verified, `llama-server` interface-only
  (see "What is partial").
- **ADR-0002 M1.0B amendment (2026-09-02, informational — D1–D4 unchanged at
  the time):** the fair Bielik re-test is done and does **not** flip the
  baseline (D4 caveat (b) resolved); a better Polish persona lifted
  `qwen3:4b-instruct` PL 2/5 → 3/5 (caveat (a) partially addressed); new
  evidence — `gemma4:e4b` / `gemma4:e2b` out-converse the incumbent in both
  languages.
- **ADR-0002 Amendment 2 (2026-09-05, decisive — supersedes D4 on model choice
  only; D1–D3 unchanged):** operator blind test run and scored; owner signed
  off on the RAM/latency cost; **`gemma4:e4b` is FROZEN as the M1.1 local
  conversation baseline.** `gemma4:e2b` documented as the fast/low-RAM
  alternative, `qwen3:4b-instruct` as the swappable safe fallback. Provider/model
  abstraction (D1–D3) explicitly preserved — this model is the M1.1 *local*
  baseline, not NeXa itself; no router implemented.
- Product code now exists for M1.1 only (`src/nexa/conversation/`,
  `src/nexa/providers/`, `src/nexa/config.py`, `src/nexa/bootstrap.py`,
  `apps/nexa_chat.py`). Nothing else on the roadmap has product code yet.

## Current test status

- Repo-local `./.venv` (system Python 3.13.5, **not** the legacy repo's venv)
  with `dev` extras (`pytest`, `ruff`) installed.
- `python -m unittest discover -s tests` and `pytest`: **33 tests, all PASS**,
  1 intentionally skipped (live Ollama test, opt-in only). `ruff check src
  tests apps`: clean.
- Live Ollama integration test (`NEXA_RUN_LIVE_TESTS=1 python -m unittest
  tests.test_live_ollama_integration`): **PASS** against real `gemma4:e4b`
  (2026-09-05) — see `R0004` for the transcript evidence.
- **Human acceptance test:** Andrzej ran a real multi-turn conversation through
  `apps/nexa_chat.py` (the actual canonical path, real `gemma4:e4b`) and
  recorded **"M1.1 HUMAN ACCEPTANCE: PASS"** (2026-09-05) — see `R0004`'s
  "Operator acceptance" addendum.
- `scripts/m1_bench/bench.py`: smoke-tested and used for real measurements
  (M1.0/M1.0B; unrelated to the M1.1 product tests above).

## Active architectural decisions

- **ADR-0001** — NeXa project foundation (Accepted).
- **ADR-0002** — Text conversation foundation (Accepted for architecture /
  provider boundary / first runtime) + **M1.0B amendment** (2026-09-02,
  informational) + **Amendment 2** (2026-09-05, decisive: M1.1 local baseline
  model **FROZEN** to `gemma4:e4b`).

## Current focus

- None active. M1 (Natural Text Conversation) M1.0 → M1.0B → operator blind
  test → ADR-0002 Amendment 2 → M1.1 implementation → **M1.1 human acceptance
  (2026-09-05, PASS)** is now a complete, operator-confirmed chain. **M2 is
  clear to start.**

## Exact next recommended task

M2's open-source-first research is done
(`docs/research/M2_REALTIME_VOICE_RESEARCH.md`, `R0005`) but its own §7–§8
recommend **two small spikes before an M2 architecture ADR is written**, not
jumping straight to implementation:

1. **Resource-budget spike**: can this Pi 5 run VAD + STT + the frozen
   `gemma4:e4b` (~10 GB, ~3 tok/s) + TTS concurrently without the
   CPU-contention failure legacy already hit once? Cheapest way to find out
   before committing to a framework integration.
2. **whisper.cpp Polish-accuracy check** against the same test sentences
   legacy already used for faster-whisper, to close the one clean
   apples-to-apples STT gap the research left open.

Then: write the **M2 architecture ADR**, choosing among the research doc's
A/B/C options (Pipecat / LiveKit Agents / both) — LiveKit Agents' proprietary
default-VAD framework-lock and Piper's GPL-3.0 successor both need an
explicit call in that ADR, not a silent default. Only after that ADR should
M2 implementation start.

Optional, not required, M1.1 hardening the owner may still want at some
point (unrelated to M2, each its own small task):
- Resolve the pre-existing Ollama blob-store permission blocker (needs an
  explicit operator/`sudo` decision) so the `llama-server` adapter and the
  Ollama-vs-llama.cpp benchmark can actually run live.
- Decide whether cancellation needs a stronger guarantee before M2's
  barge-in depends on it (see `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §6).
