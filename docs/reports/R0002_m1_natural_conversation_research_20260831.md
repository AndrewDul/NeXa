# R0002 — M1.0 Natural Conversation Research & Empirical Baseline

- **Date:** 2026-08-31 (research + benchmarks); Bielik follow-up + finalisation
  completed 2026-09-01. Filename keeps the `20260831` stamp — that is when the
  work was done and when the report was opened.
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M1 — Natural Text Conversation · **Substage M1.0 — Research & Empirical Baseline**
- **Related:** `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md` (primary
  evidence doc), `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md`,
  `docs/decisions/ADR-0001_project_foundation.md`,
  `docs/legacy/LEGACY_NEXA_INDEX.md`, `docs/reports/R0001_project_foundation_20260831.md`

---

## TASK RESULT

**PASS** — M1.0 is a research + benchmark milestone; no product architecture was
implemented. All 18 required questions are answered (see §"Final questions").
Deliverables produced: research doc, benchmark design + stable cases, throwaway
benchmark harness, machine-measured performance data for **5 local models**
(`qwen3:4b`, `llama3.2:3b`, `qwen2.5:3b`, `qwen2.5:1.5b`, **Bielik-4.5B Q8_0**),
**4 multi-turn conversation transcripts** (`qwen3:4b` × PL/EN, Bielik-4.5B ×
PL/EN) with agent-assisted 1–5 scoring and a `qwen3` vs Bielik head-to-head, a
read-only legacy audit, external research, ADR-0002, a troubleshooting record,
`CURRENT_STATE` update, this report.

Partial elements are disclosed, not hidden: see §"Unresolved".

---

## Task objective

Determine, with evidence, the strongest practical foundation for natural NeXa
conversation on the current Raspberry Pi 5 and for later portability to Linux /
Windows / macOS / iPhone / Android — without implementing the M1 conversation
architecture. Deliver a recommended M1.1 architecture, a provider boundary
sketch, a model + runtime recommendation, and a reusable benchmark.

---

## What I did

1. **Startup** — read `AGENTS.md`, `CURRENT_STATE`, `ROADMAP`,
   `FOUNDATION_ARCHITECTURE`, `ADR-0001`, `R0001`, `LEGACY_NEXA_INDEX`,
   `RESEARCH_POLICY`, `TEST_STRATEGY` (all authored in this session, re-confirmed
   in context). Verified repo identity: `AndrewDul/NeXa`, branch `main`, HEAD
   `ffdc0f7`, clean.
2. **Phase 1 — Pi facts** — collected verified hardware/software state via
   `uname`, `lscpu`, `/proc/*`, `free`, `df`, `vcgencmd`, `lsusb`, `lspci`,
   `vulkaninfo`, `hailortcli`, toolchain `--version`, and network reachability
   checks.
3. **Phase 2 — runtimes** — inventoried Ollama (running service), legacy
   `llama.cpp` / `whisper.cpp` builds, legacy `.venv` AI libs, HailoRT + the
   `hailo_platform.genai` stack and `~/hailo_model_zoo_genai`.
4. **Phase 3 — models** — `ollama list` / `ollama show` for all 6 local models;
   located GGUF files and HF cache; read the Hailo GenAI `MODELS.rst`.
5. **Phase 4 — legacy (read-only)** — audited the legacy conversation
   architecture and its measured failures via legacy Reports 164, 165, 173, 175,
   176 and direct code/config inspection (`typed_chat/*`, `settings.json`).
   Produced a reuse matrix. **No legacy file was modified.**
6. **Phase 5 — external research** — Qwen3-4B-Instruct-2507, Bielik (SpeakLeash),
   Gemma 3 4B, llama.cpp/Ollama on Pi 5, V3D Vulkan status, Pipecat vs LiveKit,
   whisper.cpp/faster-whisper/Silero, llama.cpp iOS/Android. Sources in the
   research doc §22.
7. **Phase 6–10 — benchmark** — built `scripts/m1_bench/` (throwaway, stdlib
   only): a harness (`bench.py`), stable case files (`CONV-PL-S1`, `CONV-EN-S1`,
   `CONV-MIX-1`), and drivers. Wrote `docs/testing/M1_NATURAL_CONVERSATION_
   BENCHMARK.md` (cases + 1–5 human rubric + metrics + non-gate targets).
8. **Phases 7–13 — ran benchmarks** on the Pi against Ollama: perf
   micro-benchmarks (cold + 3× warm, ~200 tokens) for `qwen3:4b-instruct`,
   `llama3.2:3b`, `qwen2.5:3b`, `qwen2.5:1.5b`; pulled and perf-benchmarked
   **Bielik-4.5B-v3.0-Instruct** (Q8_0). Multi-turn conversation sessions
   actually run: `qwen3:4b-instruct` × {**PL, EN**} and Bielik-4.5B × {**PL,
   EN**}. `CONV-MIX-1` and conversation runs for the smaller models were **not**
   run (scoped out for time — see research doc §9). Monitored
   temperature/throttling throughout; no throttling occurred.
9. **Phases 12–20 — synthesis** — wrote the recommended minimal M1.1
   architecture, the minimal provider contract, cross-device and voice-future
   analysis, risks, rejected alternatives, `ADR-0002`, and this report.

---

## What I verified (highlights — full detail in the research doc)

### Pi hardware / software (`VERIFIED FACT`)

- Raspberry Pi 5 Model B Rev 1.1, Debian 13 trixie, kernel `6.18.39`, `aarch64`,
  Cortex-A76 ×4 @ 2.4 GHz (no SVE/i8mm/bf16), **15 GiB RAM** (~13 GiB free at
  rest), 2 GiB zram swap, **803 GB free** SSD, idle ~50 °C, `throttled=0x0`.
- Network **works** — github.com and huggingface.co both HTTP 200; external
  research and model pulls were performed for real.
- **Accelerators do not help M1 LLM inference:** V3D GPU Vulkan is not
  production-ready for llama.cpp on Pi 5 (probe ran 100 % CPU); Hailo-10H GenAI
  is real but caps at ≤ 1.7B models / 2048 context and its installed HailoRT
  (5.2.0) mismatches the GenAI model set (5.3.0). Legacy config confirms it hit
  the same class of Hailo version mismatch.
- Toolchain (gcc/g++ 14.2, cmake 3.31.6, make 4.4.1) can build `llama.cpp` — the
  legacy repo already has a (stale, CPU-only) build.
- `OBSERVATION`: the login shell's `python3`/`pip` resolve into the **legacy**
  `.venv`; NeXa must always use its own `./.venv`.

### Runtimes (`VERIFIED FACT`)

- **Ollama 0.30.10** — installed `/usr/local/bin/ollama`, **running** as
  `ollama.service`, `127.0.0.1:11434`, models in `/usr/share/ollama/.ollama/
  models`, native + OpenAI-compatible streaming APIs. The only turnkey local LLM
  server on the box.
- `llama.cpp` — built binaries exist only under the **legacy** repo
  (`llama-cli`, `llama-server`), CPU-only, upstream `87589042c` (2026-05-17).
- `whisper.cpp` built (legacy); `faster-whisper` 1.2.1, `piper` 1.4.2,
  `openwakeword` 0.4.0, `silero-vad` 6.2.1, `onnxruntime` 1.24.4, `vosk` 0.3.45
  in the legacy `.venv`.

### Models (`VERIFIED FACT`, `ollama show`)

| Model | Params | Quant | Native ctx | License |
|---|---|---|---|---|
| `qwen3:4b-instruct` (Qwen3-4B-Instruct-2507) | 4.0B | Q4_K_M | 262144 | Apache-2.0 |
| `qwen2.5:3b` | 3.1B | Q4_K_M | 32768 | Qwen RESEARCH (non-commercial) |
| `llama3.2:latest` (3B) | 3.2B | Q4_K_M | 131072 | Llama 3.2 Community |
| `qwen2.5:1.5b` | 1.5B | Q4_K_M | 32768 | Apache-2.0 |
| `llama3.2:1b` | 1.2B | Q8_0 | 131072 | Llama 3.2 Community |
| `qwen2.5-coder:3b` | 3.1B | Q4_K_M | 32768 | Qwen RESEARCH |
| **Bielik-4.5B-v3.0-Instruct** (pulled this task) | 4.5B | **Q8_0** (no official Q4_K_M) | 8K (Ollama tag) | Apache-2.0 |

Only local GGUF conversational file otherwise: `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`
(legacy `models/` + `~/Models/nexa/`) — the model legacy `settings.json`'s stale
`/llm` block points at.

### Legacy conversation stack (`VERIFIED FACT` from legacy Reports 164/165/173/175/176 + code)

- One canonical brain `process_typed_chat_via_mas` (**2573 lines**), shared
  typed+voice; pipeline NexaInput→TurnSupervisor→Meaning→Context→inline gates→
  (voice-only)ActionDispatcher→Reasoning→**Conversation (only LLM call)**→
  Verifier→optional Phase-B.
- Production model: `qwen3:4b-instruct` via Ollama, **fail-closed** (Report 173
  removed silent downgrade to a smaller model).
- Chat history `ChatHistoryStore` (`MAX_CONTEXT_TURNS=8`, `MAX_CONTEXT_CHARS=
  3200`); long-term memory `MemoryBusStore` — **separate systems** (matches
  NeXa principle #8).
- A provider seam already exists: `typed_chat/ollama_conversation_provider.py`
  ("never selects a model itself… honest `failed` stage") — good contract,
  entangled implementation.
- Legacy `settings.json` `/llm` block (llama-server + Qwen2.5-1.5B + ctx 2048 +
  10 s timeout) is **stale / does not match runtime** — recorded discrepancy.

### Legacy measured failures — legacy Report 176 (124 live typed turns, 2026-08-24) (`FACT_RUNTIME` in their labelling)

- **B1**: one generation timeout → the whole typed `chat_server` process fails
  every subsequent request until manual restart (6/124 turns triggered; 5/5
  reproduced). Root cause: timeout path never cancels the abandoned pipeline
  future → in-process state corruption. Their note: *"not a model problem."*
- **B2**: capability paraphrases ("Odlicz mi pięć minut") fall through to the LLM
  which **fabricates a false "I can't do that"** (3/3). Cause: 5 fragmented
  non-communicating capability mechanisms; no grounding at the model.
- **B3**: "tak"/"nie"/"ten" → deterministic `model=none` ~62 ms canned reply,
  **sometimes English in a Polish conversation**. But "wiesz o co chodzi" reached
  qwen3:4b and it *"correctly, honestly answered"* — the bug is the interceptor,
  not the model.
- Latency: free-form voice turn median **39–58 s**; one typed LLM TTFT **13.4 s**;
  75/140 s hard timeouts discard in-progress replies.

### Benchmark measurements (`VERIFIED FACT` — `scripts/m1_bench/bench.py` on the Pi, raw under `docs/research/m1_bench_results/`)

Perf micro-benchmark (Ollama, CPU, Q4_K_M, cold then 3× warm ~200 tok):

| Model | Cold load | Warm TTFT | **Gen tok/s (mean)** | Peak °C | Throttled |
|---|---|---|---|---|---|
| `qwen3:4b-instruct` | 7.6 s | ~0.62 s | **3.89** | 66.7 | no |
| `llama3.2:3b` | 11.2 s | ~0.68 s | **4.35** | 68.3 | no |
| `qwen2.5:3b` | 9.8 s | ~0.58 s | **4.55** | 67.8 | no |
| `qwen2.5:1.5b` | 5.8 s | ~0.47 s | **9.35** | 68.3 | no |
| **Bielik-4.5B-v3 (Q8_0, 4.8B)** | 12.9 s | ~0.55 s | **2.01** | 68.3 | no |

**Non-obvious measured results:** (1) 3B is only ~10–17 % faster than 4B on this
Pi; the only real speed step is 1.5B (~2.4×). (2) Bielik-4.5B at **Q8_0** runs at
**~2.0 tok/s — half of `qwen3:4b` at Q4_K_M** — quantisation matters more than
param count here. Generation is memory-bandwidth-bound.

Conversation sessions actually run: `qwen3:4b-instruct` × {PL, EN} and
Bielik-4.5B × {PL, EN}. Per-session numbers + full transcripts: research doc
§10.2 / §11 / §12 / §12A and `docs/research/m1_bench_results/CONV-*_*.md`.
`CONV-MIX-1` and conversation runs for the smaller models were **not** run.

### Conversation quality — agent-assisted 1–5 scoring (NOT operator-confirmed)

Scored by Claude against the raw transcripts and the §4 rubric. An operator
pass is still owed before the M1.1 baseline is treated as settled.

| Model | Polish | English | Notes |
|---|---|---|---|
| `qwen3:4b-instruct` (Q4_K_M) | **2 / 5** | **4 / 5** | PL: rough grammar, unnatural, hallucinates song titles, verbose; recall + topic-switch OK. EN: natural, good recall/correction, verbose + emoji-heavy. |
| Bielik-4.5B-v3 (Q8_0) | **3 / 5** | **2.5 / 5** | PL: native grammar, no hallucination, correct recall — but **truncates answers to a stub** (T4/T5/T7) and uses banned assistant formulas. EN: blunt/hedgy, T1 non-sequitur ("What's the current time?"), poisoned recall. |

### Head-to-head — `qwen3:4b` vs Bielik-4.5B (M1.0 §12A)

Bielik wins **Polish language fidelity** and **factual restraint**. `qwen3:4b`
wins **speed (~2×: 3.3 vs 2.1 conv tok/s), English, context (262k vs 8k), RAM
(~4.0 vs ~5.4 GB), cold load (~7 vs ~15 s), and quant availability (official
Q4_K_M vs Q8_0-only)** — i.e. the dimensions that decide on-Pi viability.

### Baseline model decision (ADR-0002 D4)

**D4 stands: `qwen3:4b-instruct` remains the M1.1 baseline** — evidence-based,
not leaderboard-based. Bielik's single advantage (Polish grammar) does not
outweigh its costs on a Pi (slower, context-limited, truncates, weak English,
no Q4). **Recorded caveat:** `qwen3:4b`'s Polish (2/5) is not good enough as-is;
M1.1 must improve the Polish persona/few-shot and **re-test Bielik at its
recommended sampling (temp ~0.1) + a third-party Q4_K_M GGUF** — that re-test
could still flip the baseline.

---

## Tests / checks performed

- `scripts/m1_bench/bench.py` — syntax-checked (`ast.parse`), smoke-tested, then
  used for all measurements. Stdlib-only; no dependency installed.
- Foundation tests re-run after doc changes: `python3 -m unittest discover -s
  tests` → **OK** (updated to include the new M1.0 docs in the required-files
  check).
- `git diff --check` — clean (no whitespace errors, no conflict markers).
- No secrets; model weights and `docs/research/m1_bench_results/` raw logs
  handling: see §"Git".

---

## Unresolved

1. `llama.cpp` direct vs Ollama on identical model/quant/sampling — not measured
   (Phase-9 follow-up); expected ~same ceiling (same ggml kernels).
2. **Operator-confirmed** quality scoring — M1.0 scoring is **agent-assisted**
   (Claude) against the transcripts (research doc §11/§12/§12A). An operator
   pass is owed before the M1.1 baseline is treated as settled.
3. **Bielik fair re-test** — M1.0 ran Bielik at temp 0.7 (its `ollama show`
   default is 0.1) with a Q8_0 GGUF (no official Q4_K_M exists). The observed
   answer-truncation and EN T1 non-sequitur may be a params/template mismatch.
   M1.1 should re-test at recommended settings + a third-party Q4_K_M GGUF — it
   could flip ADR-0002 D4.
4. `qwen3:4b` Polish is weak (2/5). Open: can a better PL system persona /
   few-shot lift it to acceptable, or is a PL-specialised model unavoidable?
5. Exact Hailo Whisper-offload numbers — needs HailoRT 5.3.0; deferred to M2
   planning.
6. Pipecat vs LiveKit — deliberately not decided (M1.0 is text-only).

Resolved in M1.0: Bielik context = **8k** (`ollama show`), not 32k as earlier
assumed; Bielik has **no official Q4_K_M** (Q8_0 / FP16 only).

---

## Documentation / reports updated

- **Created:** `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md`,
  `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md`,
  `docs/reports/R0002_m1_natural_conversation_research_20260831.md` (this file),
  `docs/troubleshooting/20260831_m1_bench_watchers_and_bielik_detection.md`,
  `scripts/m1_bench/` (`README.md`, `bench.py`, `run_focused.sh`, `cases/*.json`),
  `docs/research/m1_bench_results/` (raw measurement JSON + Markdown transcripts +
  `run_*.log`).
- **Updated:** `docs/CURRENT_STATE.md` (milestone → M1, substage M1.0 complete,
  next M1.1), and `tests/test_foundation.py` (required-files list).
- **Reviewed — no change required:** `AGENTS.md` (the engineering loop and
  principles held for this task without amendment).
- **Legacy:** read-only; nothing written.

## Legacy NeXa used

**YES** — read-only. Legacy Reports 164/165/173/175/176, `config/settings.json`,
and `modules/nexa_agents/typed_chat/*` were the primary source for the legacy
architecture audit and the reuse matrix. No legacy file was modified.

## External research used

**YES** — Qwen3-4B-Instruct-2507 & Unsloth docs, SpeakLeash/Bielik (HF + Ollama),
Gemma 3, Ollama OpenAI-compatibility docs, llama.cpp & whisper.cpp GitHub, Pi 5
LLM benchmark write-ups, V3D Vulkan issue threads, Pipecat-vs-LiveKit
comparisons, on-device llama.cpp (iOS/Android), Hailo Model Zoo GenAI. Full list:
research doc §22. Community/anecdotal items are labelled and used only to steer
local benchmarking.

## Current verified state

- Repo `AndrewDul/NeXa`, branch `main`, one new commit for M1.0 (hash in
  `CURRENT_STATE` / session summary); working tree clean; foundation tests pass.
- No product code. `src/nexa/` still the placeholder package.
- M0 remains complete; M1.0 (research + baseline) complete; M1.1 not started.
- Active decisions: ADR-0001 (foundation) + **ADR-0002** (text conversation
  foundation: minimal canonical path, provider abstraction, Ollama first,
  `qwen3:4b-instruct` baseline — not frozen).

## Final questions — explicit answers

1. **Which local runtime first, and why?** **Ollama** — already installed and
   running as a service on the Pi, model-managed, native + OpenAI-compatible
   streaming APIs. It is the first implementation of a NeXa `LocalModelProvider`;
   `llama.cpp`/`llama-server` is the designated second implementation and the
   mobile/portability path. NeXa binds to neither.
2. **Which exact model as the first M1.1 baseline, and why?**
   `qwen3:4b-instruct` (Qwen3-4B-Instruct-2507, Q4_K_M) — **confirmed by
   head-to-head measurement vs Bielik-4.5B** (§12A): it wins speed, English,
   context (262k), RAM, and quant availability; Apache-2.0; the legacy
   production model (evidence continuity). **Baseline, not frozen** (ADR-0002 D4).
   Recorded caveat: its Polish (2/5) needs M1.1 work.
3. **Which second model as comparison/control?** **Bielik-4.5B-v3.0-Instruct**
   (SpeakLeash, Apache-2.0, Polish-specialised) — benchmarked as the
   Polish-quality challenger (better PL grammar, worse everything else on a Pi).
   `llama3.2:3b` = English/speed control (perf only); `qwen2.5:1.5b` = latency
   floor / Hailo-tier reference (perf only).
4. **Is the Pi fast enough for acceptable text conversation?** `INFERENCE`:
   **borderline yes for a 4B Q4 with streaming + a brevity persona** — warm TTFT
   ~0.6 s is fine; `qwen3:4b` generates at ~3.9 tok/s (perf) / ~3.2–3.8 tok/s in
   conversation, **degrading to ~2.5 tok/s** as history grows → a ~50-token
   reply ≈ 13–20 s. Usable for a considered companion, marginal for fast rhythm.
   A **Q8_0 4.5B (Bielik) at ~2 tok/s is *not* fast enough.** No throttling.
5. **What TTFT and tok/s were actually measured?** Perf micro-benchmark (Ollama,
   CPU): `qwen3:4b` Q4_K_M warm TTFT ~0.62 s / **3.89 tok/s**; `qwen2.5:3b`
   ~0.58 s / 4.55; `llama3.2:3b` ~0.68 s / 4.35; `qwen2.5:1.5b` ~0.47 s / 9.35;
   **Bielik-4.5B Q8_0 ~0.55 s warm / 33–37 s cold / 2.01 tok/s**. Cold load
   5.8–15.5 s. Conversation-mode (8k ctx, growing history): `qwen3:4b` PL mean
   3.2 (4.03→2.25), EN mean 3.8; Bielik PL mean 2.13, EN mean 2.22.
6. **Is Polish quality good enough?** **No, not as-is.** Agent-assisted:
   `qwen3:4b` Polish **2/5** (rough grammar, unnatural, hallucinates, verbose);
   Bielik-4.5B Polish **3/5** (native grammar, no hallucination, but truncates
   answers). M1.1 must improve the PL persona and re-test Bielik at recommended
   settings. Recall and topic-switching *do* work in Polish for both.
7. **Is English quality good enough?** **`qwen3:4b`: yes (4/5)** — natural, good
   recall/correction, main flaws verbosity + over-eagerness (persona-fixable).
   Bielik-4.5B English **2.5/5** (blunt, hedgy, T1 non-sequitur) — not competitive.
8. **Does direct-model conversation outperform the legacy pipeline?**
   `INFERENCE` (strong): **yes.** Direct = warm TTFT ~0.6 s, honest answers, no
   canned interceptors, no process-wide failure mode. Legacy = 13.4 s measured
   TTFT, 75/140 s hard timeouts, a single timeout permanently breaks the server
   (B1), short inputs/paraphrases intercepted with canned/false replies (B2/B3).
   Legacy Report 176: the model *when reached* "correctly, honestly answered."
9. **What legacy pieces to reuse?** *Concepts, not code:* one canonical entry /
   no second answer path / fail-closed on model unavailability; the injected
   provider that never guesses a model (contract shape from
   `ollama_conversation_provider.py`); chat-history bounding by turns+chars;
   history-vs-long-term-memory separation; small-first-chunk sentence streaming
   (benchmark then adopt). *Knowledge:* llama.cpp / whisper.cpp Pi 5 build flags;
   the whole voice pipeline (openWakeWord/Silero/Vosk/whisper/Piper) as M2
   reference; Piper `pl`/`en` voices as later assets.
10. **What legacy pieces to reject?** The 2573-line MAS brain and ~50 agent
    packages; the timeout handler (no cancel → process-wide corruption);
    deterministic pre-model interceptors (5 capability mechanisms, short-input
    canned replies); MAS-in-every-turn; `route_model_with_fallbacks` for M1
    (defer); Hailo-Ollama for M1.
11. **Can this architecture later work on iPhone?** `INFERENCE`: **yes** —
    in-process `llama.cpp` (Metal) or MLX-Swift provider adapter, or a
    trusted-node provider to the user's PC/Pi. Only the adapter changes.
12. **Android?** `INFERENCE`: **yes** — in-process `llama.cpp` (JNI/NDK) adapter
    or trusted-node. KMP wrappers exist.
13. **Laptop/PC (Linux/Windows/macOS)?** **Yes** — Ollama / `llama-server` run
    natively; a PC can also *be* a provider ("trusted node") for other devices.
14. **Can it later support voice without replacing the Conversation System?**
    **Yes by design** — the Conversation System takes "user text + session id"
    (from a text UI *or* an STT result) and emits a token stream (rendered by a
    chat UI *or* chunked by TTS). VAD/STT/TTS live in a future
    `RealtimeVoiceAdapter` (M2) that calls the same `ConversationSession`.
15. **Pipecat or LiveKit first for M2?** `PROPOSAL`: prototype with **Pipecat**
    first (local-first, transport-agnostic, BSD-2, best local-dev story);
    keep LiveKit as the multi-device transport/presence candidate. Not decided
    in M1.0 — evidence is not overwhelming.
16. **What exact minimal architecture should M1.1 implement?** `ConversationSession`
    → `ConversationContext` (bounded in-session messages) → `ConversationTurn`
    → `ModelProvider.generate(messages, options, *, cancel_token)` → async token
    stream → `StreamingResponse`; one `LocalModelProvider` (Ollama); one
    versioned system persona in `configs/`. Nothing else (no router, MAS,
    verifier, capabilities, tools, memory, persistence, voice, UI). Full spec:
    research doc §18, ADR-0002 D1.
17. **Single biggest technical risk now?** Generation speed: ~3.9 tok/s for a 4B
    on this Pi is marginal for natural chat rhythm (research doc R1). Mitigated
    by streaming + short-reply persona + a swappable provider; re-checked with
    real M1.1 use.
18. **Next concrete task?** M1.1 — see below.

---

## Next recommended action

Begin **M1.1 — Minimal Canonical Text Conversation Path**. Concretely:

1. Create the repo-local `./.venv`; add the first real dependency (an HTTP client
   is enough — `httpx` — or use stdlib); `pip install -e ".[dev]"`; confirm
   `pytest`/`ruff` run.
2. Implement ADR-0002 D1's small type set in `src/nexa/` — `ModelProvider`
   interface, `LocalModelProvider` (Ollama, OpenAI-compatible streaming),
   `ConversationContext` (bounded), `ConversationSession`, `ConversationTurn`,
   `StreamingResponse` — and one versioned system persona in `configs/`.
3. Deterministic tests for the turn contract (a fake provider) + one live
   integration test against Ollama behind a marker.
4. Wire the M1.0 benchmark harness's cases as the conversation-quality check;
   score PL/EN transcripts against the rubric and confirm or change the baseline
   model (Bielik-4.5B vs `qwen3:4b-instruct`).
5. Add the `llama-server` provider adapter to prove provider independence.

Do **not** add MAS, a model router, capability detection, tools, memory, voice,
or UI in M1.1.
