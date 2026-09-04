# R0004 — M1.1 Minimal Canonical Text Conversation Path

- **Date:** 2026-09-05
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M1 — Natural Text Conversation · **Substage M1.1 — Minimal
  Canonical Text Conversation Path**
- **Related:** `docs/decisions/ADR-0002_text_conversation_foundation.md`
  (D1–D4 + Amendment 2 — the decision this implements),
  `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` (verified
  architecture detail), `docs/testing/TEST_STRATEGY.md` §M1.1,
  `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md` (the evidence behind
  the frozen baseline model)

---

## TASK RESULT

**PASS.** All 15 acceptance criteria from the task were met; no criterion was
dropped or silently narrowed. One item (`llama-server` live verification) is
explicitly `PARTIAL` and disclosed below rather than hidden — the interface
exists and is tested, but not against a real running `llama-server` process,
for a documented pre-existing reason.

---

## WHAT I DID

1. **Verified current state** before writing anything: read `AGENTS.md`,
   `CURRENT_STATE.md`, `ROADMAP.md`, `FOUNDATION_ARCHITECTURE.md`, `ADR-0002`
   (incl. Amendment 2), `R0003`, the operator blind-test doc; confirmed `pwd`,
   `git status`/`log`, Ollama version/list/ps, `python3`/`pip` resolution
   (confirmed they resolve into the **legacy** repo's venv — known issue,
   avoided throughout), and free RAM.
2. **Created `./.venv`** from the system Python 3.13.5 (`/usr/bin/python3`,
   not the legacy repo's interpreter), installed the project editable with the
   `dev` extra (`pytest`, `ruff`) — **no new runtime dependency** was added;
   `pyproject.toml` `dependencies = []` is unchanged, verified correct as-is.
3. **Implemented the canonical turn path** in `src/nexa/`:
   - `conversation/turn.py` — `ConversationTurn` (frozen dataclass value
     object), `Role` (`StrEnum`).
   - `conversation/context.py` — `ConversationContext.build(...)`: bounded,
     deterministic window (most-recent-first, bounded by `max_turns` and a
     character budget, turns never split, latest turn always kept even if
     oversized). Not summarization, not long-term memory.
   - `conversation/streaming.py` — `StreamingResponse`: wraps a provider's raw
     async token stream, accumulates `.text`.
   - `conversation/session.py` — `ConversationSession`: the canonical
     authority. `send(user_text)` appends the user turn, builds context, calls
     the provider, streams chunks to the caller, appends the completed
     assistant turn only on success. On provider failure the exception
     propagates unchanged — no assistant turn recorded, no fallback.
   - `providers/base.py` — `ModelProvider` (ABC), `ProviderMessage`,
     `GenerationOptions`, `ProviderDescription`, `ModelUnavailableError`,
     `CancelToken` (cooperative, per-request).
   - `providers/ollama.py` — `LocalModelProvider`: Ollama `/api/chat`
     streaming, stdlib-only (`urllib` on a background thread feeding an
     `asyncio.Queue` — no HTTP client dependency), `num_ctx` always explicit,
     `think` forced via `GenerationOptions`, configurable `base_url`/`model`/
     `keep_alive`, raises `ModelUnavailableError` on any failure.
   - `providers/llama_server.py` — `LlamaServerProvider`: same interface
     against `llama-server`'s OpenAI-compatible `/v1/chat/completions` SSE
     endpoint. See "UNRESOLVED" — not live-tested.
   - `config.py` — `PersonaConfig` + `load_persona()`; `local_provider_settings_from_env()`
     reads `NEXA_MODEL_PROVIDER`/`NEXA_LOCAL_MODEL`/`NEXA_LOCAL_MODEL_ENDPOINT`,
     fails closed (`NotImplementedError`) for any provider family other than
     `local` — no silent fallback.
   - `bootstrap.py` — `build_default_session()`: the one place that wires
     persona + provider + session together, used by both the CLI harness and
     the live integration test (no parallel test-only wiring).
4. **Versioned persona**: `configs/personas/nexa_persona_v1.json` — the
   compact native-Polish/English persona that M1.0B found best for brevity and
   instruction-following, plus the frozen options (`num_ctx=8192`,
   `think=false`, etc.).
5. **Manual dev CLI harness**: `apps/nexa_chat.py` — thin wiring only, drives
   the real `bootstrap.build_default_session()` path. Not the final NeXa Chat
   UI.
6. **Tests** — see "TESTS" below.
7. **Documentation**: this report; `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
   (new, verified); two one-line pointers added to
   `FOUNDATION_ARCHITECTURE.md`'s Conversation/Model Providers sections;
   `docs/testing/TEST_STRATEGY.md` §M1.1 added; `docs/CURRENT_STATE.md`
   updated; `apps/README.md` updated; `.env.example` gained
   `NEXA_LOCAL_MODEL`.

## WHAT I VERIFIED

- `python -m unittest discover -s tests` and `pytest`, both via `./.venv`:
  **33 tests, 32 pass, 1 intentionally skipped** (the live Ollama test).
  `ruff check src tests apps`: clean.
- **Live Ollama integration**, opt-in (`NEXA_RUN_LIVE_TESTS=1`), run once
  against the real `gemma4:e4b`: one Polish turn ("Cześć, jak się masz?" →
  "Dzięki, wszystko gra. A Ty?") and one English turn ("Briefly, what is your
  name?" → "Jestem NeXa." — the model answered in Polish despite an English
  question; `OBSERVATION`, not a code defect, recorded for awareness). History
  was correct (4 turns, ordered, roles correct); model explicitly unloaded
  (`ollama stop`) in `tearDownClass`; `ollama ps` confirmed empty before and
  after.
- **Manual real multi-turn conversation** through `apps/nexa_chat.py` (piped
  input, real process, real Ollama call): asked a Polish question, then asked
  in English "What did I just say, in English?" — the model correctly recalled
  and translated its own earlier turn, proving real cross-turn history through
  the actual CLI wiring (not a parallel test-only path).
- RAM/thermal discipline maintained throughout: `ollama ps` checked before
  loading; model explicitly stopped after each live run; peak resident ~11 GB
  used / ~4 GB available at worst during the CLI demo, back to ~13 GB
  available after `ollama stop`; no swap used.
- `git diff --check`, `git status`, and a manual scan of all new/changed files
  for secrets, model weights, caches, and `.venv` — clean; `.gitignore`
  already covers `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`.

## TESTS

33 total, organized by tier (`docs/testing/TEST_STRATEGY.md` §M1.1):

| File | Tier | Count | Needs Ollama? |
|---|---|---|---|
| `tests/test_foundation.py` | unit | 3 | no |
| `tests/test_context.py` | unit | 6 | no |
| `tests/test_streaming_response.py` | unit | 4 | no |
| `tests/test_conversation_session.py` | unit (`FakeModelProvider`) | 10 | no |
| `tests/test_ollama_provider.py` | integration (fake HTTP server) | 5 | no |
| `tests/test_llama_server_provider.py` | integration (fake HTTP server) | 3 | no |
| `tests/test_live_ollama_integration.py` | integration, real backend | 1 | **yes** — opt-in only |

The 10 required test scenarios from the task all map onto the suite above:
ordered context (`test_sends_correct_ordered_context`), user turn stored
(`test_user_turn_is_stored`), streamed chunks combined
(`test_streamed_chunks_are_combined_correctly`, `test_streaming_response.py`),
assistant turn stored (`test_completed_assistant_turn_is_stored`), bounded
context (`test_context_remains_bounded`, `test_context.py`), provider failure
surfaces (`test_provider_failure_surfaces_explicitly`), no silent fallback
(`test_no_fallback_on_provider_failure`), PL input
(`test_polish_input_flows_through_canonical_path`), EN input
(`test_english_input_flows_through_canonical_path`), multi-turn history
(`test_multi_turn_history_persists_within_session`) — plus the live Ollama
test covering the same PL/EN pair against the real backend.

## UNRESOLVED

- **`LlamaServerProvider` is not live-tested.** Pre-existing, unrelated
  blocker (already recorded in `CURRENT_STATE.md` for the Ollama-vs-llama.cpp
  benchmark): no GGUF weight file is reachable on this machine outside
  Ollama's `0700`/`ollama`-owned blob store. Running a real `llama-server`
  needs either `sudo` into that store or downloading a separate GGUF — both
  out of this task's scope, per the task's own instruction to document rather
  than solve this blocker. Verified at the interface/request-shaping level via
  a stdlib fake HTTP server (`tests/test_llama_server_provider.py`).
- `llama-server` has no per-request context-size parameter (`-c` is a server
  startup flag); `GenerationOptions.num_ctx` is not sent to it. Documented in
  `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §8, not silently ignored.
- `CancelToken` cancellation is cooperative between stream chunks; it cannot
  interrupt a token already being computed mid-inference by either backend.
  Documented in §6 of the same doc as a known limitation for M2 to account
  for.
- No new numbered report was needed for the operator blind test / baseline
  freeze from the previous task — `R0004` is the next number and covers M1.1
  only, per sequential numbering (`AGENTS.md` §5).

## DOCUMENTATION / REPORTS UPDATED

- `docs/CURRENT_STATE.md` — fully updated (status, what works/partial/not
  implemented, architecture state, test status, focus, next task).
- `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` — new.
- `docs/architecture/FOUNDATION_ARCHITECTURE.md` — two pointer lines added
  (Conversation, Model Providers), conceptual framing otherwise unchanged.
- `docs/testing/TEST_STRATEGY.md` — §M1.1 added.
- `apps/README.md`, `.env.example` — updated for the new app/env surface.
- This report (`R0004`).
- `ADR-0002` — **not modified** in this task; no new architectural decision
  was exposed by implementation (D1–D4 + Amendment 2 already covered
  everything built here; the `llama-server` adapter fulfills D3's existing
  prediction rather than changing it).
- `tests/test_foundation.py` — `REQUIRED` list extended with this report's
  filename, per existing convention (each report is added when written).

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO — implementation follows ADR-0002's already-researched decisions directly;
no new external research was needed.

## CURRENT VERIFIED STATE

M1.1 is implemented, tested, and documented. `gemma4:e4b` (ADR-0002 Amendment
2) is the live, working local conversation baseline behind a real provider
abstraction. `./.venv` exists and is used for all Python work in this repo.
Repo is clean after one coherent commit (see below); nothing pushed.

## NEXT RECOMMENDED ACTION

Start **M2 — Realtime Voice** as a new, explicitly-started milestone with its
own ADR — nothing in M1.1 blocks it. Optional, non-blocking M1.1 hardening the
owner may want first: resolve the Ollama blob-store permission blocker so
`LlamaServerProvider` and the Ollama-vs-llama.cpp benchmark can run live; think
through whether M2's barge-in needs a stronger cancellation guarantee than
M1.1's cooperative `CancelToken` provides.

---

## Operator acceptance (2026-09-05, addendum — `OPERATOR-CONFIRMED`)

**Status of this addendum:** informational — it does not change the PASS
verdict or any evidence above; it adds a higher tier of evidence than the
agent's own manual-CLI check in "WHAT I VERIFIED".

Everything above this addendum (including the manual multi-turn conversation
in "WHAT I VERIFIED") was `AGENT-ASSISTED`: the agent drove `apps/nexa_chat.py`
itself via piped input and judged the result. Separately, and after this
report was first written, **the owner/operator Andrzej personally used the
real path** —

```
apps/nexa_chat.py → ConversationSession → ConversationContext →
ModelProvider → LocalModelProvider (Ollama) → gemma4:e4b
```

— in a real, interactive multi-turn conversation (not piped input, not
agent-driven), and recorded:

> **M1.1 HUMAN ACCEPTANCE: PASS.** Everything worked correctly and the
> conversation quality was satisfactory.

This is the human-acceptance tier `AGENTS.md` §4's evidence labels exist to
distinguish. `docs/CURRENT_STATE.md` has been updated to reflect it. No code,
architecture, or test was changed to produce this addendum — it is a record
of an evaluation, not new work.

**Implication:** M1.1's exit criteria (ROADMAP "M1 — Natural Text
Conversation … Rough exit criteria") are now met with both agent-assisted and
operator-confirmed evidence. **M2 — Realtime Voice is clear to start** with no
outstanding M1.1 acceptance gate.
