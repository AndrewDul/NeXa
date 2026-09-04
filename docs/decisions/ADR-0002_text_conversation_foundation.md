# ADR-0002 — Text conversation foundation (M1)

- **Status:** Accepted (architecture, provider boundary, first runtime);
  **Baseline — FROZEN 2026-09-05** (`gemma4:e4b` is the M1.1 local conversation
  baseline — see Amendment 2). D1–D3 (provider abstraction, Ollama-first,
  turn-path architecture) unchanged throughout.
- **Date:** 2026-08-31
- **Deciders:** Andrzej Dul (owner), Claude Code (agent)
- **Related:** `docs/reports/R0002_m1_natural_conversation_research_20260831.md`,
  `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md`,
  `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md`,
  `docs/decisions/ADR-0001_project_foundation.md`, `docs/ROADMAP.md`

---

## Context

M1.0 (research + empirical baseline) measured the Raspberry Pi 5, inventoried
existing local runtimes/models, audited the legacy NeXa conversation stack
(read-only), did external research, and benchmarked local models. Full evidence:
the research doc and R0002. The load-bearing facts:

- `VERIFIED FACT` — Ollama 0.30.10 is already installed and running as a service
  on the Pi (`127.0.0.1:11434`), with `qwen3:4b-instruct` (Qwen3-4B-Instruct-2507,
  Q4_K_M, **Apache-2.0**, 262k ctx) and five smaller models already local. It
  exposes both a native and a full OpenAI-compatible streaming chat API.
- `VERIFIED FACT` — `llama.cpp` builds on this Pi (legacy has a CPU-only build);
  it is the portable engine (Linux/macOS/Windows **+ iOS/Android**) and ships an
  OpenAI-compatible `llama-server`.
- `VERIFIED FACT` — on this Pi, CPU is the only viable LLM path: the V3D GPU
  (Vulkan) is not production-ready for llama.cpp, and the Hailo-10H GenAI path is
  capped at ≤1.7B models / 2048 context / a mismatched toolchain.
- `VERIFIED FACT` — measured generation speed (Ollama, CPU): `qwen3:4b` Q4_K_M
  **3.9 tok/s**, `llama3.2:3b` Q4_K_M 4.35, `qwen2.5:3b` Q4_K_M 4.55,
  `qwen2.5:1.5b` Q4_K_M 9.35, **Bielik-4.5B Q8_0 2.0**. Warm TTFT ~0.5–0.7 s
  (Bielik cold TTFT 33–37 s). No thermal throttling (≤ 68.8 °C). **3B is not
  meaningfully faster than 4B; Q8_0 halves throughput vs Q4_K_M.**
- `INFERENCE` (strong; legacy Report 176, 124 live typed turns, 2026-08-24) —
  legacy conversation naturalness suffered primarily from **pipeline complexity,
  late/missing grounding with deterministic pre-model interceptors, and latency**,
  not from model quality. The legacy report's own words: the model, *when
  actually reached*, "correctly, honestly answered." Legacy carried a 2573-line
  single "brain", ~50 agent packages, a cascading total-outage bug on any
  generation timeout, and canned/false pre-model replies to short inputs and
  capability paraphrases.
- `VERIFIED FACT` — legacy already discovered the right *principles* (one
  canonical entry, no second answer path, fail-closed on model unavailability,
  chat-history separate from long-term memory, an injected provider that never
  guesses a model) but implemented them inside an over-large, entangled stack.

## Decision

### D1 — One minimal canonical text-conversation path (Accepted)

M1.1 implements exactly one turn path and nothing more:

```
text UI / (later) STT → ConversationSession → ConversationContext (in-session
messages, bounded by turns + chars) → ModelProvider.generate(...) → streamed
tokens → append assistant message
```

Concretely, M1.1 builds only: `ModelProvider` (interface), `LocalModelProvider`
(Ollama impl), `ConversationContext`, `ConversationSession`, `ConversationTurn`
(value object), `StreamingResponse`, and one versioned system persona in
`configs/`. Nothing else.

**Not in M1.1:** model router / multi-model fallback, MAS or any
Meaning/Reasoning/Verifier agent, capability detection, tools, long-term memory,
disk persistence, voice, UI, device awareness. Each is a later milestone with its
own ADR.

**Carried constraints (from legacy failure evidence):**
1. No pre-model layer may emit a user-visible answer (no deterministic
   interceptors returning canned text).
2. Cancellation must actually stop generation and must never leave shared mutable
   state broken; per-request state only (legacy blocker B1).
3. `num_ctx` / context window is always set explicitly.
4. On provider failure, fail closed with an honest message — never a silent
   downgrade to a different model (legacy Report 173).

### D2 — Model access is a provider abstraction shaped like the OpenAI chat API (Accepted)

NeXa core talks only to a `ModelProvider` interface: `generate(messages,
options, *, cancel_token) -> async token stream`, plus `describe()` capabilities
and a typed "model unavailable" error. The interface is deliberately minimal
(messages, sampling options, context window, stop, streaming, cancellation) —
**no** tools/images/embeddings/batching in M1. The wire shape targeted is the
OpenAI-compatible `/v1/chat/completions` streaming contract so one adapter can
drive Ollama, `llama-server`, a trusted-PC node, or an online provider by
configuration. No provider name appears in core control flow (`AGENTS.md` §3.4,
ADR-0001 D4).

### D3 — Ollama is the first `LocalModelProvider` implementation (Accepted)

Because it is already installed, running, model-managed, and API-served on the
target hardware. `llama.cpp` (`llama-server`, and later an in-process binding for
mobile) is the designated **second** implementation and the portability path, to
be added in M1.1/M2. NeXa depends on **neither** — both are implementations of
D2's interface.

### D4 — `qwen3:4b-instruct` is the first conversation baseline model — baseline, NOT frozen

It is the strongest realistic already-local model for natural bilingual
conversation (newest architecture, Apache-2.0, genuine multilingual, 262k
context, non-thinking instruct) and it is what legacy production used, giving
continuity of evidence.

**Confirmed by measurement (M1.0 §12A head-to-head vs Bielik-4.5B-v3 Q8_0):**

| | `qwen3:4b` Q4_K_M | Bielik-4.5B Q8_0 |
|---|---|---|
| Polish quality (agent-assisted) | 2/5 — weak grammar, hallucination | **3/5** — native grammar, but truncates answers |
| English quality (agent-assisted) | **4/5** | 2.5/5 — hedgy, T1 non-sequitur |
| Speed (Pi, conversation) | **~3.2–3.8 tok/s** | ~2.1–2.2 tok/s |
| Context | **262k** | 8k |
| RAM resident | **~4.0 GB** | ~5.4 GB |
| Official Q4_K_M | **yes** | no (Q8_0/FP16 only) |

Bielik wins Polish *language fidelity* only; `qwen3:4b` wins speed, English,
context, footprint, and quant availability — the dimensions that decide on-Pi
viability. **D4 stands**, with a recorded caveat: `qwen3:4b`'s Polish is **not
good enough as-is**, so M1.1 must (a) improve the Polish system persona /
few-shot and (b) re-test Bielik at its recommended sampling and with a Q4_K_M
GGUF before the baseline is treated as settled. The model is a **configuration
choice**, swappable at any time on evidence; this ADR does **not** bind NeXa to
Qwen or any vendor, and the M1.1 re-test could still flip it.

## Options considered

- **Reuse the legacy MAS brain / port it** — rejected: 2573-line entry +
  ~50 agent packages, measured latency and the B1 outage bug, and it contradicts
  `AGENTS.md` §3 and ADR-0001. Its *principles* are reused; its code is not.
- **Skip the provider interface, call Ollama directly for M1** — rejected:
  recreates the vendor coupling ADR-0001 D4 exists to prevent; the interface is
  cheap.
- **llama.cpp direct as the first runtime** — deferred: no daemon here, legacy's
  build is stale/CPU-only, and Ollama already provides managed serving. llama.cpp
  is the second impl / portability path, not the first.
- **Hailo-10H as the LLM runtime** — rejected: ≤1.7B models, 2048 context,
  ~5–10 tok/s, HailoRT 5.2.0-vs-5.3.0 mismatch; worse conversation quality than a
  CPU 3–4B. Revisit later (notably Hailo Whisper offload for M2).
- **Drop to a 3B for speed** — rejected as a lever: measured 3B is only ~10–17 %
  faster than 4B on this Pi. The real trade is 4B-class quality vs 1.5B-class
  speed.
- **Freeze a specific model** — rejected: violates provider/model independence;
  D4 is explicitly a non-frozen baseline.

## Consequences

**Positive**
- A tiny, auditable conversation core that cannot regress into a legacy-style
  mega-pipeline without a superseding ADR.
- Provider independence is structural from the first line of M1 code.
- The first runtime and model are the lowest-friction possible (already on the
  box), so M1.1 can focus on the turn path and its tests.

**Negative / costs**
- ~3.9 tok/s on a 4B is marginal for fast chat rhythm; M1.1 must lean on
  streaming + a brevity-enforcing persona, and keep the provider swappable.
- Ollama-first means the OpenAI-shape adapter and the llama.cpp second impl are
  owed in M1.1/M2 to prove independence in practice.
- Polish quality of the baseline model is not yet human-scored (benchmark
  transcripts captured in M1.0; scoring feeds the M1.1 model choice).

**Follow-up work**
- M1.1: implement D1's types + D3 provider + tests; add the `llama-server`
  adapter; create the repo-local `./.venv` and first real dependency.
- Score the M1.0 benchmark transcripts (PL/EN/MIX) and confirm or change D4.
- Later ADRs: realtime voice transport boundary (M2), context vs history vs
  memory split (M3), model router if/when >1 model is justified.

## Compliance / review

- Code review for M1.1 checks: exactly one turn path; no pre-model canned
  answers; no provider name in core; cancellation stops generation; explicit
  `num_ctx`; fail-closed on provider error.
- Revisit D3/D4 whenever benchmark evidence favours another runtime/model, or if
  Pi latency proves unacceptable in real M1.1 use — via a new ADR with evidence,
  not silently.

---

## Amendment — M1.0B Current Small-Model Sweep (2026-09-02)

- **Status of this amendment:** informational. **D1–D4 are unchanged**; D4's
  "baseline — not frozen" status line stands. This records fresh evidence and
  resolves two open caveats. It does **not** rewrite M1.0 history.
- **Evidence:** `docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md`,
  `docs/reports/R0003_m1_0b_current_small_model_sweep_20260901.md`. Raw data:
  `docs/research/m1_bench_results/m1_0b/`. All quality scores are
  `AGENT-ASSISTED` (not operator-confirmed).

### What M1.0B changes about D4

D3 (Ollama first) and D1/D2 (the minimal turn path + provider boundary) are
**unaffected** — M1.0B is a model question only. For D4 (the first baseline
*model*):

1. **Caveat (b) — "re-test Bielik at recommended sampling + a Q4_K_M GGUF" —
   RESOLVED.** M1.0B re-tested `SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0` at
   its baked `temperature 0.1` and a third-party (Second State) Q4_K_M GGUF with
   the identical template. Result: **the baseline does not flip.** Q8_0 still
   opens English with a non-sequitur, overclaims capabilities, never warms its
   TTFT, and gives shallow/repetitive Polish; the 3rd-party Q4_K_M is a
   reliability failure (empty completions + a stuck template loop). This
   *confirms* D4's original head-to-head rather than overturning it.
2. **Caveat (a) — "improve the Polish system persona" — PARTIALLY ADDRESSED.** A
   better Polish persona lifted `qwen3:4b-instruct`'s Polish from `2/5` (M1.0)
   to `3/5` (M1.0B) — better, not yet "good". M1.0B also measured that a compact
   native-Polish persona materially improves brevity and instruction-following
   for the Gemma 4 models (persona A/B, sweep doc §15).
3. **New evidence D4 did not have:** two model families that did not exist at
   M1.0 — **Gemma 4** (`gemma4:e4b`, `gemma4:e2b`) and **Qwen3.5** — were
   benchmarked on the same cases. `gemma4:e4b` (PL 4/5, EN 4/5) and `gemma4:e2b`
   (PL 3.5/5, EN 4/5, ~2× the incumbent's tok/s, flattest long-context decay in
   either milestone) **out-converse `qwen3:4b-instruct` in both languages**.
   `qwen3:4b-instruct` remains reliable, Apache-2.0, and has by far the largest
   context window (262k), but is no longer the quality leader and has the worst
   long-context speed decay of the four head-to-head models.

### Recommendation carried forward (not yet a decision)

M1.0B's `PROPOSAL` is that the M1.1 baseline model should be **`gemma4:e2b`**
(best weighted NeXa score, best Pi balance, Apache-2.0), with **`gemma4:e4b`** as
the quality-mode alternative and `qwen3:4b-instruct` retained as the swappable
safe fallback. `qwen3.5:2b` is an English-primary / low-RAM option only (broken
Polish, one honesty-probe hallucination).

**This is not applied here.** D4 stays as written until:
(1) the operator blind test (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`)
is run and scored, and (2) the RAM/latency trade for a Gemma 4 model
(~7.5–10 GB resident, ~3–7 tok/s) is accepted by the owner. A change to the
baseline model is then made in a **new ADR** (or a superseding revision of this
one) with that combined evidence — per the "Compliance / review" rule above.

Both conditions are now met — see Amendment 2 below.

---

## Amendment 2 — Operator blind test result: M1.1 local baseline FROZEN (2026-09-05)

- **Status of this amendment:** decisive. This **supersedes D4** on the model
  choice only. D1–D3 (turn-path architecture, `ModelProvider` abstraction,
  Ollama-first) are **unchanged**.
- **Evidence:** `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md` §4/§8
  (live, blind, operator-run — Andrzej Dul, 2026-09-04); full transcripts and
  hidden per-turn metrics in `docs/testing/m1_operator_blind_results/`; prior
  `AGENT-ASSISTED` evidence in `R0003` and
  `docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md`.

### Decision

**`gemma4:e4b` is FROZEN as the M1.1 local conversation baseline.**

### Why

- **Operator blind test winner.** Of the three sealed M1.0B finalists
  (`gemma4:e4b`, `gemma4:e2b`, `qwen3.5:2b`), the operator — blind to identity,
  talking normally in mixed Polish/English — scored it highest: **4/5
  overall, "everyday NeXa: YES."** `gemma4:e2b` scored 3/5 ("maybe");
  `qwen3.5:2b` scored 2/5 ("no").
- **Best live conversation quality among the finalists**, matching R0003's raw
  (unweighted) Phase-2 conversation-quality sub-scores, where `gemma4:e4b`
  already led or tied `gemma4:e2b` (`CONV-NATURAL-S1` 4.5 vs. 4.0;
  `CONV-HONESTY-S1` tied at 4.5) — the operator's live preference is not a
  surprise reversal, it is agreement with the quality evidence R0003 already
  had.
- **Strongest Polish among the tested practical candidates** — R0003's "Best
  model for Polish" call (`gemma4:e4b` 4/5, `gemma4:e2b` 3.5/5 second;
  Bielik is native-grammar but undeployable) is now operator-confirmed rather
  than agent-assisted only.
- **Differs from the earlier `gemma4:e2b` weighted-score recommendation**
  (`R0003`, sweep doc §19: `gemma4:e2b` 4.0 vs. `gemma4:e4b` 3.9) **because
  that weighted total gave substantial weight to Pi speed/RAM efficiency**
  (latency 15 + Pi practicality 5 of 100 points), not because the two
  disagree on conversation quality. The operator, given the real trade-off
  live, weighted quality over speed differently than the pre-set formula did.
  This is the kind of divergence `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`
  §6 anticipated: "the operator's lived judgement is the actual product bar."

### Accepted cost (owner sign-off, Andrzej Dul, 2026-09-05)

The owner explicitly accepts the measured Raspberry Pi resource/latency trade
of `gemma4:e4b` over `gemma4:e2b`:

- ~10 GB resident RAM (vs. `gemma4:e2b`'s ~7.4 GB)
- ~3 tok/s live generation (vs. `gemma4:e2b`'s ~6 tok/s)
- slower TTFT than `gemma4:e2b` (live blind-test average ~3.6 s vs. ~1.5 s)

Owner's words: *"I am choosing conversation quality over the speed/RAM
advantage of gemma4:e2b for the canonical M1.1 local baseline."*

### Provider/model abstraction — explicitly preserved

- `gemma4:e4b` is the **M1.1 LOCAL baseline model** — a swappable
  configuration choice behind the D2 `ModelProvider` interface. **It is not
  NeXa.** NeXa's identity, memory, context, user relationship, goals,
  capabilities, permissions, and learning belong to NeXa's canonical
  system/runtime (per `AGENTS.md` §0, §3), never to one model.
- This does **not** permanently bind NeXa to Gemma or to any vendor. A future
  benchmark, a new model release, or a hardware change can supersede this
  amendment the same way it superseded D4 — via evidence and a new/superseding
  ADR, never a silent swap.
- Future `AUTO` / `LOCAL ONLY` / `CLOUD PREFERRED` routing policies remain
  **planned, not implemented**. Manual provider/model selection remains an
  **advanced future capability**, not built here.
- **No model router is implemented by this amendment.** M1.1 still builds
  exactly the D1 turn path against this one frozen local model; routing
  between models/providers is later work with its own ADR.

### What remains documented alongside the frozen baseline

- **`gemma4:e2b`** — strong faster / lower-resource local alternative
  (~2× the tok/s, ~2.5 GB less RAM, flattest measured long-context decay of
  the roster). Worth keeping as a fast-mode option once M1.1's provider layer
  makes swapping trivial.
- **`qwen3:4b-instruct`** — reliable, Apache-2.0, by far the largest context
  window (262k) of the roster; remains documented as the swappable safe
  fallback / reference baseline it already was.
- **`qwen3.5:2b`** — English-primary / low-RAM option only; not suitable for
  bilingual use (broken Polish, a honesty-probe hallucination the operator
  independently reproduced live).

### Follow-up work

- M1.1 implements D1's turn path against `gemma4:e4b` via the D3 Ollama
  provider, with `gemma4:e2b` and `qwen3:4b-instruct` kept as documented,
  easily-swappable alternates behind the same `ModelProvider` interface.
- A model router / `AUTO`/`LOCAL ONLY`/`CLOUD PREFERRED` policy layer remains
  a later milestone, out of scope for M1.1.
