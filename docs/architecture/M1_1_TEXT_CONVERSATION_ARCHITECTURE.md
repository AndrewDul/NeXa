# M1.1 — Minimal Canonical Text Conversation Path (verified architecture)

Status: **`VERIFIED FACT`** — implemented and tested 2026-09-05. This document
describes real code, unlike `docs/architecture/FOUNDATION_ARCHITECTURE.md`
(conceptual-only map of intent for boundaries not yet built). Only the
**Conversation** and **Model Providers** boundaries are made concrete here;
everything else in `FOUNDATION_ARCHITECTURE.md` remains conceptual.

Related: `docs/decisions/ADR-0002_text_conversation_foundation.md` (the
decision this implements), `docs/reports/R0004_m1_1_canonical_text_conversation_path_20260905.md`
(the implementation report), `docs/testing/TEST_STRATEGY.md` §M1.1.

---

## 1. The one path

```
user text
    │
    ▼
ConversationSession.send(text)
    │  appends ConversationTurn(role=USER)
    ▼
ConversationContext.build(system_prompt, history, max_turns, max_chars)
    │  bounded, deterministic window — not memory, not summarization
    ▼
ModelProvider.generate(messages, options, cancel_token)
    │  interface only — session never sees Ollama-specific detail
    ▼
LocalModelProvider (Ollama) / LlamaServerProvider
    │  wire-level HTTP, one concrete backend at a time
    ▼
StreamingResponse
    │  wraps the raw async token stream, accumulates .text
    ▼
streamed chunks → caller, then ConversationTurn(role=ASSISTANT) appended
```

One path. No parallel "final answer" pipeline. On provider failure the
exception propagates unchanged to the caller and no assistant turn is
recorded (fail-closed, AGENTS.md §3.2).

## 2. Package layout (`src/nexa/`)

| Module | Responsibility |
|---|---|
| `conversation/turn.py` | `ConversationTurn` (value object), `Role` enum |
| `conversation/context.py` | `ConversationContext` — bounded window builder |
| `conversation/streaming.py` | `StreamingResponse` — chunk accumulation |
| `conversation/session.py` | `ConversationSession` — the canonical authority |
| `providers/base.py` | `ModelProvider` interface, `ProviderMessage`, `GenerationOptions`, `ProviderDescription`, `ModelUnavailableError`, `CancelToken` |
| `providers/ollama.py` | `LocalModelProvider` — first implementation (ADR-0002 D3) |
| `providers/llama_server.py` | `LlamaServerProvider` — second implementation, portability path |
| `config.py` | `PersonaConfig` + loader; env-based provider settings, fail-closed on an unimplemented `NEXA_MODEL_PROVIDER` |
| `bootstrap.py` | `build_default_session()` — the one place that wires config → provider → session |

`apps/nexa_chat.py` is a thin CLI wrapper around `bootstrap.build_default_session()` — wiring/presentation only, no product logic (`apps/README.md`).

## 3. Provider abstraction — what crosses the boundary

`ConversationSession` depends only on: `ModelProvider.generate(messages,
options, cancel_token) -> AsyncIterator[str]`, `ProviderMessage`,
`GenerationOptions`, `ModelUnavailableError`, `CancelToken`. It has zero
knowledge of HTTP, Ollama's NDJSON shape, `llama-server`'s OpenAI SSE shape,
`keep_alive`, or `base_url`. Those live only inside the two provider
implementations. This is what "the model is a replaceable provider, not NeXa
itself" means concretely (AGENTS.md §0, ADR-0002 Amendment 2).

Both implementations use only the standard library (`urllib` + a background
thread feeding an `asyncio.Queue`) — no HTTP client dependency was added.
`src/nexa` has **zero runtime dependencies** (`pyproject.toml` `dependencies = []`
unchanged).

## 4. Configuration, not identity

| Setting | Env var | Default (M1.1) |
|---|---|---|
| Provider family | `NEXA_MODEL_PROVIDER` | `local` (only implemented value; anything else raises `NotImplementedError` — fail closed, no silent fallback) |
| Local model tag | `NEXA_LOCAL_MODEL` | `gemma4:e4b` (the frozen baseline, ADR-0002 Amendment 2) |
| Local endpoint | `NEXA_LOCAL_MODEL_ENDPOINT` | `http://127.0.0.1:11434` |
| Persona | `configs/personas/nexa_persona_v1.json` | compact native-Polish/English persona (M1.0B best-performing) |

Swapping the model or endpoint is a config change, not a code change.
`AUTO`/`LOCAL ONLY`/`CLOUD PREFERRED` routing policy and manual provider
selection are explicitly **not** built here (ROADMAP "Later").

## 5. Context bounding strategy (`ConversationContext.build`)

Deterministic, not summarization: keep the most recent turns, walking from
newest to oldest, bounded by both `max_turns` (default 20) and a character
budget `max_chars` (default 12,000). Turns are never split. The single most
recent turn is always kept even if it alone exceeds the character budget —
the model always sees at least the latest thing the user said. `history` on
`ConversationSession` itself is **unbounded** within a session (the full
transcript); only the *context sent to the model* is bounded. Chat history vs.
context vs. long-term memory stays the three-way split ADR-0002/AGENTS.md §3.8
require — no memory system exists yet.

## 6. Cancellation

`CancelToken` is cooperative and per-request (never shared mutable state
across requests — legacy blocker B1). `LocalModelProvider`/`LlamaServerProvider`
check it once per received NDJSON/SSE line and stop reading further output.
**Known limitation, stated plainly:** this stops the provider from consuming
*further* generated tokens; it cannot interrupt a token Ollama/llama.cpp is
already computing mid-inference on the backend (neither backend exposes a
finer-grained abort in their streaming HTTP APIs). This is sufficient for
M1.1's scope (no UI, no barge-in yet) and is not a silent gap — it is recorded
here for M2 (voice/barge-in) to account for.

## 7. Verified test coverage

See `docs/testing/TEST_STRATEGY.md` §M1.1 for the full tier breakdown. Summary:
32 deterministic tests (unit + fake-HTTP-server integration, no Ollama
required) + 1 live-Ollama integration test (explicit opt-in via
`NEXA_RUN_LIVE_TESTS=1`, run once against the real `gemma4:e4b` in
`R0004`) + one manual, real, multi-turn CLI conversation via `apps/nexa_chat.py`.

## 8. `llama-server` adapter — implemented, not live-verified

`LlamaServerProvider` implements the same `ModelProvider` interface against
`llama-server`'s OpenAI-compatible `/v1/chat/completions` SSE endpoint, proving
provider independence at the code/interface level (ADR-0002 D3's "portability
path", the "owed in M1.1/M2" cost item in ADR-0002's Consequences). It is
covered by fake-HTTP-server tests only (`tests/test_llama_server_provider.py`).

**Not live-tested**, and this is a pre-existing, unrelated blocker, not a new
one: there is no GGUF weight file reachable on this machine outside Ollama's
`0700`/`ollama`-owned blob store (the same blocker recorded in
`docs/CURRENT_STATE.md` for the still-`NOT RUN` Ollama-vs-llama.cpp
benchmark). Running a real `llama-server` process would need either `sudo`
into that blob store or downloading a separate GGUF — both out of this
task's scope. **Known limitation, also stated plainly:** unlike Ollama,
`llama-server` has no per-request context-size parameter — its context length
is fixed by the server's own `-c` startup flag, so `GenerationOptions.num_ctx`
is not sent to it and cannot be enforced by this provider; the caller must
ensure the running server's `-c` matches.

## 9. Explicit non-goals (unchanged from ADR-0002 D1)

No MAS, no model router, no `AUTO`/`LOCAL ONLY`/`CLOUD PREFERRED` policy, no
capability layer, no fallback model, no long-term memory, no tools, no device
awareness, no voice, no UI. Each is a later milestone with its own ADR.
