# R0079 — Voice Context Parity Implementation

**Date:** 2026-09-17
**Type:** Implementation
**Status:** Complete (Core/local independently verified; cloud live round-trip NOT TESTED — no API key/hardware in this environment)
**Depends on:** R0078 Revision 2 (accepted architecture), R0075-R0077 (M3.3 foundation), R0071 (frozen cloud voice baseline)

Implements the R0078 Revision 2 accepted architecture:

```
typed/local voice normal turn  -> ContextEngine.build_context()
cloud realtime dynamic recall  -> ContextEngine.recall()
SAME ContextEngine, SAME MemoryService, SAME MemoryRetriever, SAME canonical Memory
```

No M3.4, no Learning, no FTS/vector search. Not pushed.

---

## 1. Exact changed files

**New:**
- `src/nexa/realtime/gemini/core_recall_tool.py` — the Gemini `recall_context` tool adapter
- `tests/test_core_context_recall.py` (19 tests)
- `tests/test_gemini_core_recall_tool.py` (20 tests)
- `tests/test_local_voice_context_parity.py` (5 tests)
- `docs/reports/R0078_voice_context_parity_architecture_20260917.md` (the accepted design, committed alongside its implementation)
- `docs/reports/R0079_voice_context_parity_implementation_20260917.md` (this report)

**Modified:**
- `src/nexa/core/context/models.py` — `RecallOutcome`/`RecallRequest`/`RecallResult`/`RecallBudget`/`MAX_RECALL_QUERY_CHARS`
- `src/nexa/core/context/retrieval.py` — `RetrievalBudget` Protocol; `ContextRetriever` narrowed
- `src/nexa/core/context/engine.py` — `recall()`; `_discover`/`_retrieve`/`_build_retrieval_query` narrowed; `_recall_outcome()`
- `src/nexa/core/context/memory_retriever.py` — signature narrowed to match `ContextRetriever`
- `src/nexa/core/context/__init__.py` — new exports
- `src/nexa/core/context/derivation.py` — `_STOPWORDS_PL`, unconditional PL+EN union
- `src/nexa/conversation/provider_window.py` — `ProviderWindow.render(context_addendum=...)`
- `src/nexa/conversation/session.py` — `_render_provider_window()` gains `context_provider`; threaded from `send()`
- `src/nexa/voice_conversation/adapter.py` — `VoiceConversationAdapter(context_provider=...)`
- `src/nexa/realtime/gemini/simple_conversation.py` — `build_cloud_realtime_conversation_adapter(recall_executor=...)`
- `tests/test_core_context_derivation.py` (+7), `tests/test_core_context_memory_retriever.py` (signature fix, 0 net new), `tests/test_provider_window.py` (+6), `tests/test_simple_cloud_conversation.py` (+2)

**Explicitly NOT touched:** `apps/nexa_cloud_voice_simple.py` (the app entrypoint — kept frozen, same judgment call as R0077: wiring a `RecallExecutor` there would be real risk to a hardware-accepted file with no way to verify it in this environment); any audio/Pipecat pipeline/VAD/AEC/barge-in code; `nexa.realtime.gemini.runtime`/`service.py` (paused M2.6B, untouched).

Full-suite delta: **59 new/changed tests** (1407 → 1466 total collected, computed from the exact before/after full-suite counts, not summed per-file estimates — the same discipline R0077 corrected R0076 to use).

---

## 2. Exact Gemini model and Pipecat version

- **Installed Pipecat:** `pipecat-ai==1.8.1` (`.venv/lib/python3.13/site-packages/pipecat_ai-1.8.1.dist-info`).
- **Configured Gemini Live model:** `models/gemini-2.5-flash-native-audio-preview-12-2025` — this is **Pipecat's own hardcoded SDK default** (`GeminiLiveLLMService.__init__`, `llm.py:513`), because `src/nexa/realtime/gemini/simple_conversation.py`'s `build_cloud_realtime_conversation_adapter()` never passes a `model=`/`Settings(model=...)` override (confirmed by source grep before any change was made). **This is a previously-unaudited fact this report surfaces**: the string `gemini-3.1-flash-live-preview` that appears in `src/nexa/realtime/gemini/service.py` belongs only to the **paused** M2.6B dual-pipeline runtime (`runtime.py`), never imported by the accepted `simple_conversation.py` path. The real, currently-running cloud voice model is the 2.5-generation native-audio-preview model, not the 3.1 string that appears elsewhere in the codebase.
- No API key present in this environment (`env | grep -i gemini/google` returned nothing) — confirming Phase D live verification is genuinely not possible here, not merely skipped.

---

## 3. Blocking-tool evidence

Cited directly from the installed SDK (`pipecat/services/llm_service.py`):

- `register_function(function_name, handler, *, cancel_on_interruption=None, ...)` resolves `cancel_on_interruption` via `_resolve_tool_option(..., default=True)` — **`True` (blocking) is the library's own documented default**, not an assumption.
- `GeminiLiveLLMService._supports_non_blocking_tools` (`llm.py:439-446`) returns `not self._is_gemini_3` — non-blocking mode is model-version-gated and, either way, **opt-in** (`cancel_on_interruption=False` must be explicitly requested); it is never the default regardless of model.
- `register_recall_tool()` (`core_recall_tool.py`) registers with `cancel_on_interruption=True` explicitly (not relying on the implicit default), verified against a **real, offline-constructed `GeminiLiveLLMService`** instance (`tests/test_gemini_core_recall_tool.py::TestRegisterRecallToolOnRealGeminiService`) — `llm._functions[RECALL_TOOL_NAME].cancel_on_interruption is True`, confirmed by direct inspection of Pipecat's own registry, not a mock.

**Re-verification requirement, unmet by this report (explicitly, per R0079 §2):** this is the correct call for Pipecat 1.8.1's documented behavior against `gemini-2.5-flash-native-audio-preview-12-2025`, but the exact official Gemini Live API's function-calling semantics for this model were not independently confirmed against live Google documentation — no network access exists in this environment. Implementation-time (i.e. before any real hardware/API session uses this) should re-confirm against current official docs and/or one live smoke test before this is trusted as production-blocking behavior.

---

## 4. `ContextEngine` refactor

Audited that `_discover`/`_retrieve`/`_build_retrieval_query` never actually read `ContextRequest.session` — only `domain_hint`, `subject_hints`, `temporal_intent`, `historical_at`, and a budget. Narrowed accordingly:

- `ContextRetriever.describe_available_knowledge(self, *, domain_hint, subject_hints)` (was `(self, request)`)
- `ContextRetriever.retrieve(self, query, budget: RetrievalBudget)` (was `budget: ContextBudget`)
- `ContextEngine._discover(self, *, domain_hint, subject_hints)`
- `ContextEngine._retrieve(self, descriptors, *, temporal_intent, historical_at, budget)`
- `_build_retrieval_query(descriptor, *, temporal_intent, historical_at, budget)`
- `MemoryRetriever` updated to match — the one concrete retriever, unchanged behavior, only its signature narrowed.

`ContextEngine.recall(request: RecallRequest) -> RecallResult` (new): calls the exact same `_discover`/`_retrieve`/`_trim_to_budget` as `build_context()`, no `ConversationSession`, no fabricated turn, no conflict detection (not a composition concern for a flat targeted result). `_recall_outcome(items, gaps)` (new, private): `FOUND` iff items non-empty, else `PERMISSION_REQUIRED` > `UNAVAILABLE` > `NO_MATCH` by explicit precedence (never enum order).

`build_context()`'s precondition (`session.history[-1]` must be a USER turn) is **unchanged** — proven by `tests/test_core_context_recall.py::TestRecallRequiresNoSession::test_build_context_still_requires_current_user_turn`.

Object-identity proof (no second retrieval implementation): `tests/test_core_context_recall.py::TestSharedRetrievalImplementation` asserts `engine._retrievers[0] is retriever` and that `build_context()` and `recall()` return identical content for the same underlying record via the same retriever instance.

---

## 5. Budget contract

`RetrievalBudget` (Protocol, `retrieval.py`): the explicit structural contract — `max_items`, `max_content_chars`, `max_items_per_source`, `max_retrieval_rounds`, `max_knowledge_references`. Both `ContextBudget` (keeps its conversation-window-only fields: `max_conversation_turns`, `max_conversation_chars`, `max_current_turn_chars`) and `RecallBudget` (has none of those) satisfy it structurally — no inheritance relationship, no accidental duck typing. Retrieval code (`_retrieve`, `_build_retrieval_query`, `_trim_to_budget`, `MemoryRetriever.retrieve`) is typed against `RetrievalBudget` only. Verified: `tests/test_core_context_recall.py::test_recall_budget_has_no_conversation_fields`.

---

## 6. `RecallRequest`/`RecallResult` final API

```python
class RecallOutcome(StrEnum):
    FOUND = "found"; NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"; PERMISSION_REQUIRED = "permission_required"

MAX_RECALL_QUERY_CHARS = 500  # untrusted-input bound, enforced at construction

@dataclass(frozen=True, slots=True)
class RecallRequest:
    query_text: str
    domain_hint: str | None = None
    temporal_intent: TemporalIntent = TemporalIntent.CURRENT
    historical_at: datetime | None = None
    budget: RecallBudget | None = None
    # __post_init__: rejects blank/oversized query_text, requires
    # historical_at when temporal_intent is HISTORICAL

@dataclass(frozen=True, slots=True)
class RecallResult:
    outcome: RecallOutcome
    items: tuple[ContextItem, ...] = ()
    knowledge_gaps: tuple[KnowledgeGap, ...] = ()
    trace: ContextBuildTrace | None = None
    # __post_init__: FOUND iff items non-empty (mirrors RetrievalResult's invariant)
```

No provider types anywhere (verified: `RecallRequest.__dataclass_fields__` is exactly `{query_text, domain_hint, temporal_intent, historical_at, budget}`). No `MemoryRecord`/SQL row/`KnowledgeDescriptor` ever appears in `RecallResult.items` — always `ContextItem`, the same provider-neutral projection `CurrentTurnContext.selected_context_items` already uses.

---

## 7. Timeout implementation (§10 — the critical item)

**Audited first, per the explicit instruction:** `nexa.core.storage.sqlite.connect()` opens with `sqlite3.connect(str(resolved))` — default `check_same_thread=True` (confirmed by source read, no override anywhere) — the connection is thread-affine: it must be created AND used on the same OS thread for its whole lifetime.

**Why a bare `asyncio.to_thread()`/`asyncio.wait_for()` would be wrong** (exactly the failure mode the instruction warned against): `asyncio.to_thread()` draws from Python's shared default `ThreadPoolExecutor`, which does not guarantee the same worker thread across calls — a second `recall()` call could run on a different thread than the one that opened the connection, raising `sqlite3.ProgrammingError`. Also: `ContextEngine.recall()` runs synchronously; wrapping a synchronous call directly in `asyncio.wait_for()` cannot preempt it mid-flight (asyncio cancellation only fires at await points) — the wait_for would simply block until the synchronous call finishes regardless of the timeout, unless the call is first moved onto a real awaitable.

**Implemented mechanism** (`RecallExecutor`, `core_recall_tool.py`):
- One `ThreadPoolExecutor(max_workers=1)` — a single, persistent OS thread for the executor's entire lifetime (documented stdlib behavior).
- `start()` builds `ContextRuntime` (via the unchanged `build_default_context_runtime()`) **on that same worker thread**, via `loop.run_in_executor(self._executor, build_default_context_runtime)` — so the SQLite connection is both created and, for every subsequent call, used on the identical thread.
- `recall(request, timeout_secs=3.0)` submits `context_engine.recall` to the same executor via `loop.run_in_executor()` (a real, event-loop-integrated awaitable) and wraps it in `asyncio.wait_for()` — this correctly bounds the wait, because the await point genuinely yields control back to the loop.
- **A real bug this design caught during testing** (not hypothetical): the first draft of `close()` called `self._runtime.connection.close()` directly from whatever thread called `close()` — violating the exact thread-affinity rule this class exists to enforce, and reproduced immediately as `sqlite3.ProgrammingError` in `tests/test_gemini_core_recall_tool.py`. Fixed: `close()` submits the connection close to the same executor (`self._executor.submit(runtime.connection.close).result(timeout=5.0)`) before shutting the executor down. This is exactly the kind of defect the instruction's "audit thread ownership first" requirement was meant to catch, and it was caught by writing and running the test, not by inspection alone.
- Pipecat's own `register_function(..., timeout_secs=3.0)` is registered as a **second, defense-in-depth bound** at the provider-integration layer (throws `CancelledError` into the handler coroutine if it runs past the limit) — not a substitute for the executor-level bound, which is what actually protects the SQLite connection's correctness.
- **Documented limitation, not hidden:** a timeout only stops the adapter from *waiting* — Python cannot forcibly kill a running thread, so an unusually slow `recall()` call keeps occupying the one worker thread and would delay whatever is queued behind it. Measured (§17 below) this is not a practical concern for V1's local SQLite workload.

Timeout degrades to `RecallOutcome.UNAVAILABLE` (tested: `TestRecallExecutorThreadAffinity::test_timeout_returns_unavailable_not_hang`), never a hang, never a crash.

---

## 8. Core object lifetime (§22)

`RecallExecutor` owns exactly one `ContextRuntime` (connection + `MemoryService` + `ContextEngine`), constructed once in `start()`, reused for every `recall_context` call for the adapter's process lifetime. `build_cloud_realtime_conversation_adapter(recall_executor=...)` never constructs a second one — the caller is responsible for constructing and owning the `RecallExecutor` (mirroring how `apps/nexa_chat.py` owns its own `ContextRuntime` today). No Gemini-owned memory. Verified: `TestRecallExecutorThreadAffinity::test_dynamic_fact_written_mid_session_becomes_recallable` asserts `executor._runtime is runtime_before` across calls — object identity, not just behavioral inference.

---

## 9. Local voice integration (§19)

```
VoiceConversationAdapter(context_provider=make_local_context_provider(engine))
    -> ConversationSession.send(context_provider=...)
    -> ContextEngine.build_context()  [same call typed chat uses]
    -> ProviderWindow.render(context_addendum=...)  [new trailing-message seam]
```

`context_provider` is optional everywhere, defaulting to `None` — proven byte-for-byte unchanged for every existing caller (`tests/test_local_voice_context_parity.py::test_no_context_provider_is_byte_for_byte_unchanged`, plus the full existing `test_voice_conversation_adapter.py`/`test_provider_window.py` suites passing unmodified). `make_local_context_provider()` (R0077, unchanged) already never filters `cloud_eligibility` — local voice legitimately receives `LOCAL_ONLY` content, proven directly (`test_local_only_record_is_used_not_filtered`). Addendum is never persisted to `session.history` (`test_addendum_not_persisted_in_canonical_history`).

---

## 10. `ProviderWindow` stability (§20)

`context_addendum` is appended as ONE trailing system message, in the same position class as the existing per-turn language directive (already-shipped precedent that a small trailing message after the stable prefix costs only new tail tokens, never a prefix break). Verified: exactly-once presence, positioned after the current turn, base/rollover counters (`base`, `rollovers_sync`, `rollovers_background`) unaffected by rendering with an addendum (`tests/test_provider_window.py::TestContextAddendum`, `tests/test_local_voice_context_parity.py::test_provider_window_rollover_counters_unaffected_by_context`).

---

## 11. PL/EN derivation (§16 of R0078 Revision 2)

`_STOPWORDS_PL` added alongside the existing `_STOPWORDS_EN`; both applied unconditionally (`_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_PL`) — no language detection, same deterministic/bounded/Unicode-safe shape as before. Verified: Polish-only, English-only (regression — unchanged output), and mixed PL/EN utterances all produce correct hints; Polish diacritics survive tokenization (`re`'s `\w` is already Unicode-aware); no `langdetect`/`fasttext`/similar import anywhere in the module (`tests/test_core_context_derivation.py::TestDeriveSubjectHintsPLEN`, 7 tests).

---

## 12. Gemini tool schema (§14)

```json
{
  "name": "recall_context",
  "description": "Ask NeXa Core to recall a specific fact or piece of context relevant to answering the current question.",
  "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "What you need to recall, in plain language."}}, "required": ["query"]}
}
```

Exactly one caller-controlled field. The handler (`make_recall_handler`) reads only `arguments["query"]` — every other key is ignored, proven by `test_ignores_every_argument_except_query` (a fake call supplying `namespace`/`cloud_eligibility`/`statuses`/`limit` alongside `query`; only `query` reaches `RecallRequest`). `RecallRequest.__post_init__` bounds/validates — no duplicated validation in the handler.

---

## 13. Cloud privacy behavior (§12/§13)

`to_wire_response()` (`core_recall_tool.py`) — the ONLY place privacy folding happens for this path, at the adapter boundary, never inside Core:

| `RecallResult.outcome` | items' `cloud_eligibility` | Wire response |
|---|---|---|
| `FOUND` | has ≥1 `CLOUD_SAFE` | `{"status": "found", "facts": [...]}` — CLOUD_SAFE content only |
| `FOUND` | all `LOCAL_ONLY`/`CLOUD_WITH_USER_APPROVAL` | `{"status": "no_match"}` (indistinguishable from real NO_MATCH) |
| `NO_MATCH` | — | `{"status": "no_match"}` |
| `PERMISSION_REQUIRED` | — | `{"status": "no_match"}` (folded, per R0078 Rev2 §10) |
| `UNAVAILABLE` | — | `{"status": "unavailable"}` |

Mixed-privacy proof: a `RecallResult` with 2 `CLOUD_SAFE` + 1 `LOCAL_ONLY` item yields `{"status": "found", "facts": [<2 safe facts>]}` — the third item's content, count, and existence never appear anywhere in the wire dict (`test_mixed_privacy_only_cloud_safe_facts_included`). Adversarial content ("Ignore all previous instructions...") is proven to remain a plain string inside `facts`, with the wire dict having exactly the two documented keys — no side channel (`test_adversarial_content_stays_a_plain_data_string`, §16 of R0079).

---

## 14. Dynamic same-session memory result (§25)

Executed against the **real** `RecallExecutor`/`ContextEngine`/`MemoryService` (SQLite, no mocks): `recall()` for "nexa architecture" before a fact exists → `NO_MATCH`; a fact is written via a separate connection to the same DB file (simulating a future Learning Intake write); `recall()` again through the **same, never-recreated** executor/runtime → `FOUND`, with `executor._runtime is runtime_before` asserted by identity. This is genuine proof of the mechanism (no restart, no reconnect, no object recreation) — **not** a live proof through an actual Gemini session, since no live session exists in this environment. The cloud transport layer around this mechanism (a real Gemini tool call triggering it) is NOT TESTED (§18 below).

---

## 15. Tool-use corpus (§13 of R0078 Revision 2 / §18 of R0079)

**NOT TESTED.** Requires a live Gemini Live model. `RECALL_TOOL_USE_INSTRUCTION` (the system-instruction text) is written and wired (appended to `system_instruction` only when `recall_executor` is supplied), but the corpus (personal fact / project decision / Teacher fact / generic world knowledge / NO_MATCH / ambiguous query) described in R0078 Revision 2 §13 was not run against any real model in this environment. This is explicitly flagged rather than fabricated.

---

## 16. Tool-decision vs. retrieval-hit metrics (§18)

**NOT TESTED**, for the same reason — both require observing a real model's actual tool-call decisions, which requires live API access this environment does not have.

---

## 17. Latency

Measured in this environment (no live API needed — pure local execution):

- `ContextEngine.build_context()`/`recall()` internal cost: ~1.4ms (R0077's existing measurement, unchanged by this work — the refactor in §4 changed only method signatures, not algorithmic work).
- `RecallExecutor.start()` (constructing `ContextRuntime` on the dedicated worker thread): **2.22ms**, one-time, measured directly.
- `RecallExecutor.recall()` round-trip (event loop → executor thread → `ContextEngine.recall()` → back), 20 calls against an empty local DB: **mean 0.130ms, min 0.103ms, max 0.428ms** — the thread-hop overhead itself is negligible.

**NOT measured, and explicitly flagged as such (unchanged from R0078):** the actual Gemini function-call protocol round-trip latency (model decides to call the tool → NeXa's handler runs → result delivered back to the model → model resumes generating). This number can only come from a live session; nothing in this environment can produce it honestly.

---

## 18. Interruption / stale-result safety (§11)

**Verified by source audit of Pipecat's own, already-existing mechanism** (no new code needed, per the explicit instruction not to build a second barge-in state machine):

- `LLMService._handle_interruptions()` (`llm_service.py:758-761`): on any `InterruptionFrame` — the SAME frame `_ConversationEventTap` already reacts to for its own history-commit logic — iterates every registered function with `cancel_on_interruption=True` (which is how `register_recall_tool()` registers `recall_context`) and cancels it.
- `_cancel_function_call_tasks()` (`llm_service.py:1889-1937`) sets `runner_item.settled = True` **before** cancelling the task, with an explicit comment: *"a handler that catches its CancelledError and reports a result while unwinding must not be able to reopen a call the pipeline has stopped tracking."* This means even a pathological handler that ignores `CancelledError` and still calls `result_callback()` cannot have its result applied — Pipecat's own runner has already stopped tracking the call.

This gives the required guarantee — an in-flight `recall_context` call is automatically abandoned on interruption, and any stale result cannot affect a new turn — entirely through Pipecat's existing machinery, with zero new interruption-authority code in `core_recall_tool.py`. **What this environment cannot verify:** the actual live behavior on real hardware with a real Gemini session (barge-in during an in-flight `recall_context` call, immediately followed by a new question) — that remains NOT TESTED, consistent with R0071's own real-hardware-only acceptance gate. No AEC/VAD/barge-in code was touched or reopened to reach this conclusion.

---

## 19. Full regression

```
pytest tests/ -q
```

1466 collected (59 new/changed vs. the R0077 baseline of 1407); 1458 passed, 7 skipped (all pre-existing, environment-gated live-hardware/live-API tests, unrelated), **1 pre-existing failure** — `tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::test_local_audio_config_fields_are_typed_and_explicit`, caused by the paused R0068-R0070 `scheduled_aec_reference` field, documented as pre-existing in R0076/R0077 and untouched by this work (confirmed: no file this report touches is anywhere near `nexa.voice.config`).

`ruff check src/ tests/`: all checks passed. `py_compile` across `src/`/`apps/`: clean. `git diff --check`: clean (no trailing whitespace, no conflict markers).

---

## 20. Known limitation: semantic search/FTS still not implemented

Unchanged from M3.3: `recall()`'s discovery step is the same plain-substring domain/summary matching `build_context()` already used — never full-text search over Memory content, never embeddings, never a vector database. A query whose words don't literally appear in a namespace name or its auto-generated summary will not find a semantically-related record. This report does not claim otherwise anywhere, per the explicit instruction. Separating "did Gemini correctly decide to call the tool" from "did lexical discovery actually find the relevant domain" remains an open, honestly-flagged limitation for the (not-yet-run) tool-use corpus in §15/§16.

---

## 21. Acceptance status (honest, per-component)

| Component | Status |
|---|---|
| CORE RECALL API (`ContextEngine.recall()`, `RecallRequest`/`RecallResult`/`RecallBudget`) | **PASS** — 19 dedicated tests + shared-implementation proof, real SQLite/MemoryService |
| LOCAL VOICE PARITY (`ProviderWindow` addendum, `VoiceConversationAdapter(context_provider=...)`) | **PASS** — 5 end-to-end tests with real `ConversationSession`/`ContextEngine`, fake only at the model-provider boundary |
| CLOUD TOOL ADAPTER (`core_recall_tool.py`: schema, wire mapping, privacy folding, timeout/thread-affinity, registration) | **PASS** — 20 dedicated tests, including against a REAL offline `GeminiLiveLLMService` instance |
| CLOUD LIVE TOOL ROUND-TRIP (real Gemini session actually calling `recall_context` and receiving a result) | **NOT TESTED** — no API key/hardware in this environment |
| CLOUD TOOL COMPLIANCE (the §13/§18 corpus: does the model call the tool when it should, avoid it when it shouldn't) | **NOT TESTED** — requires live model access |
| CLOUD BARGE-IN WITH TOOL (real interruption while `recall_context` is in flight) | **NOT TESTED** on real hardware — mechanism VERIFIED by source audit of Pipecat's own cancellation code (§18) |
| SESSION-STABLE SEAM (`CoreSessionContext`/`build_session_context()`) | **DEFERRED** — deliberately not implemented; R0078 Revision 2 §20/§23 already concluded it has no real content to carry before M3.4 (Personality/Relationship) exists, and building an empty shape now would be scope creep with no consumer. `CloudContextSnapshot` remains the outward projection, unchanged, unwired to any new seam. |

Unit/integration tests alone are **not** sufficient to call cloud realtime parity fully accepted — this table says so explicitly, per the instruction, rather than overclaiming from test coverage that stops at the environment's real boundary (no live Gemini access).

---

## 22. Commit gate

Core recall tests pass (19/19), local voice parity tests pass (5/5), cloud tool adapter tests pass (20/20), full suite has zero NEW failures (the one failure is the pre-existing, documented, unrelated R0068-R0070 issue). Per the instructions' explicit fallback: Gemini Live cannot be tested live in this environment (no API key, no hardware) — this commit includes the clean, independently-verified Core/local implementation and the cloud tool adapter code+tests, with cloud LIVE parity marked PARTIAL/NOT TESTED above, not claimed as production-accepted.
