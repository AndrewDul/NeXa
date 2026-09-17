# R0076 — M3.3 Context Engine + Knowledge Awareness Implementation

**Date:** 2026-09-17
**Milestone:** M3.3 — NeXa Core, Context Engine + Knowledge Awareness (implementation)
**Status:** Implemented and verified against R0075 Revision 3, plus the two
implementation-time clarifications from the authorizing instruction (§0A/§0B).
Not pushed.

## TASK RESULT

**PASS.**

## WHAT I DID

Implemented incrementally, verifying each layer with real (not mocked)
SQLite/Memory/ConversationSession objects before moving to the next, per
instruction:

1. **`most_restrictive_cloud_eligibility()`** added to `nexa.core.privacy`
   — explicit precedence map (`LOCAL_ONLY > CLOUD_WITH_USER_APPROVAL >
   CLOUD_SAFE`), never enum/string ordering. 6 new tests; existing 8
   privacy tests re-run, all pass.
2. **`MemoryRepository.namespace_summary()`** — one bounded SQL statement
   (a CTE selecting the top-N namespaces by recency, joined back for a
   per-namespace/per-eligibility aggregate), per the corrected §0A
   instruction (not the illustrative Python-side `[:limit]` from R0075's
   prose). Verified via `sqlite3.Connection.set_trace_callback`: exactly
   one SQL statement per call, selecting only
   `namespace`/`cloud_eligibility`/aggregate columns — never `content` or
   `payload_json`. `MemoryService.namespace_summary()` passthrough added.
   8 new tests.
3. **`src/nexa/core/context/`** (new package): `models.py` (all R0075
   Revision 3 types), `retrieval.py` (`ContextRetriever` Protocol,
   `RetrievalQuery`, `RetrievalNotSupportedError`), `memory_retriever.py`
   (`MemoryRetriever` — the one V1 concrete retriever), `engine.py`
   (`ContextEngine`, `ContextBuildError`). No `projection.py` inside Core
   — moved out per the approved design.
4. **`src/nexa/realtime/context_projection.py`** (new) — `to_cloud_snapshot()`,
   depending inward on `nexa.core.context` and reusing
   `build_cloud_context_snapshot()`/`filter_cloud_safe()` unmodified.
5. **`ContextBudget.max_current_turn_chars`** (§0B) — reuses
   `nexa.conversation.context.DEFAULT_MAX_CHARS` (20 000, the one existing
   canonical size bound for a comparable concern) as its default rather
   than inventing a new number; an oversized current turn raises
   `ContextBuildError`, never silently truncated.
6. **91 new/changed tests** across 6 files (below), all passing after
   fixing 3 real bugs found by testing against actual objects (not
   mocks) — see "Bugs found and fixed."
7. Full regression suite, `ruff`, `py_compile`, `git diff --check`.
8. This report, ADR-0006, `CURRENT_STATE.md`/`ROADMAP.md` updates.

## BUGS FOUND AND FIXED DURING IMPLEMENTATION

Testing against real `MemoryService`/`ConversationSession` objects (not
mocks) at each layer caught three real defects the design review passes
did not surface:

1. **`RetrievalResult.__post_init__` required a `reason_code` for `NO_MATCH`**
   (should only be required for `UNAVAILABLE`/`PERMISSION_REQUIRED`) —
   caught immediately by a first smoke run; fixed before any formal test
   was written.
2. **`MemoryRetriever.describe_available_knowledge()` self-capped its
   output at `budget.max_knowledge_references`** before `ContextEngine`
   could see the true candidate count, making
   `ContextBuildTrace.omitted_descriptor_count` structurally unobservable
   whenever there is exactly one retriever (V1's actual situation) — the
   retriever now scans up to a bounded `_DISCOVERY_SCAN_LIMIT` (200) and
   returns everything it finds; `ContextEngine` alone applies the final
   `max_knowledge_references` cap and computes the omitted count. Caught
   by `test_omitted_descriptor_count_when_over_budget` failing on first run.
3. **A test's own wrong assumption**, not a code bug: a `NO_MATCH`
   acceptance test assumed a record with no explicit `valid_from` would
   fail a historical query for a much earlier date — but `valid_at()`
   correctly treats an unset validity range as "always valid" (matching
   M3.2's own documented semantics), so the record legitimately matched.
   Fixed the test to set an explicit `valid_from` after the queried date,
   not the implementation.

## WHAT I VERIFIED

```
tests/test_core_privacy.py                        14 passed (+6 new)
tests/test_core_memory_repository.py               29 passed (+8 new)
tests/test_core_context_models.py                  12 passed (new file)
tests/test_core_context_memory_retriever.py        19 passed (new file)
tests/test_core_context_engine.py                  36 passed (new file)
tests/test_realtime_context_projection.py           10 passed (new file)
```
= **91 new/changed tests, all passing.**

Full regression suite (`./.venv/bin/python3 -m pytest tests/ -q`, real run,
sandbox disabled): **1355 passed, 7 skipped, 1 pre-existing unrelated
failure** (`tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::
test_local_audio_config_fields_are_typed_and_explicit` — the same paused
R0068-R0070 `scheduled_aec_reference` issue as every prior checkpoint this
session; untouched by this work). `ruff check` clean on every new/changed
file. `py_compile` clean on all of `src/nexa/core/` and the new realtime
module. `git diff --check` clean (see staged diff below).

## NAMESPACE-SUMMARY SQL STRATEGY (§0A)

```sql
WITH top_namespaces AS (
    SELECT namespace, MAX(updated_at) AS freshest
    FROM memory_records
    WHERE status = 'active'
    GROUP BY namespace
    ORDER BY freshest DESC, namespace ASC
    LIMIT ?
)
SELECT m.namespace, m.cloud_eligibility, COUNT(*) AS record_count,
       MAX(m.updated_at) AS freshest, t.freshest AS namespace_freshest
FROM memory_records AS m
JOIN top_namespaces AS t ON t.namespace = m.namespace
WHERE m.status = 'active'
GROUP BY m.namespace, m.cloud_eligibility
ORDER BY t.freshest DESC, m.namespace ASC
```

One statement, using the existing `idx_memory_namespace` index. Python
then does one pass collapsing rows per namespace via
`most_restrictive_cloud_eligibility()`. Verified by
`set_trace_callback`-based tests: exactly 1 statement per call regardless
of namespace count, and the SQL text never references `content` or
`payload_json`.

## KNOWLEDGE AWARENESS EXAMPLE

```
Memory contains: "lifeos.sleep" (1 record), "teacher.python" (1),
                  "teacher.math" (1)

describe_available_knowledge(domain_hint="lifeos") ->
    KnowledgeDescriptor(kind=DOMAIN_GROUP, id="memory:domain_group:lifeos",
                         domain="lifeos", child_domains=("lifeos.sleep",))
    -- ONE known child, still surfaced (R0075 §5 correction)

describe_available_knowledge(domain_hint="teacher") ->
    KnowledgeDescriptor(kind=DOMAIN_GROUP, id="memory:domain_group:teacher",
                         domain="teacher",
                         child_domains=("teacher.math", "teacher.python"))
```

Neither group match causes any `retrieve()` call — confirmed by test
(`TestGroupNonRetrievability`): `selected_context_items` stays empty; the
group is awareness-only.

## LOADED / AVAILABLE / UNKNOWN / NO_MATCH / UNAVAILABLE / PERMISSION_REQUIRED — real examples

```
LOADED               -> item present in CurrentTurnContext.selected_context_items
                        (no enum value -- structural presence)

AVAILABLE            -> KnowledgeDescriptor(domain="projects.nexa",
                                             availability=AVAILABLE)

UNKNOWN              -> domain_hint="nonexistent.domain" with no matching
                        descriptor -> KnowledgeGap(state=UNKNOWN,
                        query_hint="nonexistent.domain")

NO_MATCH (real run)  -> domain_hint="projects.nexa" (descriptor exists),
                        HISTORICAL query at a date before the only
                        record's valid_from ->
                        RetrievalResult(outcome=NO_MATCH, items=()) ->
                        KnowledgeGap(state=NO_MATCH, query_hint="projects.nexa")

UNAVAILABLE /
PERMISSION_REQUIRED  -> forward-compatible states; no real V1 retriever
                        returns them (MemoryRetriever queries a local,
                        always-available SQLite database) -- exercised
                        only by test doubles, as designed
```

## CURRENT-TURN / CONVERSATION-WINDOW EXAMPLE (real test output)

```python
session.history = [ConversationTurn(ASSISTANT, "prior reply"),
                    ConversationTurn(USER, "current message")]

ctx = engine.build_context(ContextRequest(session=session))

ctx.current_turn            == session.history[-1]          # "current message"
ctx.conversation_window     == tuple(session.history[:-1])   # ("prior reply",)
ctx.current_turn in ctx.conversation_window                  # False, always
```

Oversized current turn (real test): a 200-char turn against
`max_current_turn_chars=50` raises `ContextBuildError` immediately;
`session.history[-1].content` is confirmed unchanged (still 200 chars) —
never silently truncated.

## DOMAIN / GROUP EXAMPLE (collision-proof, real test)

```
Memory contains BOTH a flat namespace "lifeos" AND "lifeos.sleep".

descriptors for domain_hint="lifeos":
    id="memory:domain:lifeos"        kind=DOMAIN        (the real, flat namespace)
    id="memory:domain_group:lifeos"  kind=DOMAIN_GROUP   (the synthesized parent)

Two distinct descriptors, two distinct IDs -- no collision (R0075 §4/§8).
```

## PRIVACY EXAMPLE (real end-to-end run, local -> cloud)

```
Memory: "Cloud-safe project fact." (CLOUD_SAFE)
        "LOCAL-ONLY-SECRET-SHOULD-NOT-CROSS" (LOCAL_ONLY)

ctx = engine.build_context(domain_hint="projects.nexa")
ctx.selected_context_items -> BOTH items present (full local context, R0075 §21/§14)

snapshot = to_cloud_snapshot(ctx, session=session)
snapshot.system_instruction contains "Cloud-safe project fact."   -> True
snapshot.system_instruction contains "LOCAL-ONLY-SECRET..."       -> False
```

Also verified: `CLOUD_WITH_USER_APPROVAL` items never cross automatically
(same mechanism); `knowledge_references` (descriptors) are never sent to
the cloud projection at all in V1, even when populated — a dedicated test
confirms no descriptor `domain` string appears anywhere in the resulting
`system_instruction`.

## CONFLICT EXAMPLE (real test output)

```
FACT/PREFERENCE, same (domain, record_type, scope):
  "Prefers morning workouts"  +  "Prefers evening workouts"
  -> ContextConflict(type=POTENTIAL_CONFLICT), BOTH items stay selected

STATE, same domain/scope:
  "Battery: 82%"  +  "Battery: 79%"   -> zero conflicts (normal time series)

EVENT, same domain:
  "purchase A"  +  "purchase B"        -> zero conflicts (normal log)
```

## DYNAMIC NEW-KNOWLEDGE EXAMPLE (real test, same engine instance)

```
engine = ContextEngine(identity, (memory_retriever,))   # constructed once

before = engine.build_context(domain_hint="lifeos.goals")
before.selected_context_items == ()                       # nothing yet

memory_service.remember(namespace="lifeos.goals",
                         content="Complete MSc with distinction")

after = engine.build_context(domain_hint="lifeos.goals")   # SAME engine instance
after.selected_context_items == (1 item,)                   # discoverable immediately
```

No restart, no retriever reconstruction, no hardcoded namespace list.

## HISTORICAL EXAMPLE (real test)

```
old = remember("Works at Company A", valid_from=2026-01-01)
supersede(old, "Works at Company B", valid_from=2027-01-01)

build_context(domain_hint="employer.ns", temporal_intent=HISTORICAL,
               historical_at=2026-06-01)
-> selected_context_items == ["Works at Company A"]   # correctly historical
```

Retracted-knowledge check (real test): a retracted `car_ownership` fact
never appears in `selected_context_items` under either `CURRENT` or
`HISTORICAL` intent.

## TEACHER EXAMPLE (real test, zero Teacher-specific engine code)

```
Memory (via plain MemoryService.remember(), no Teacher module exists):
  teacher.python / skill_mastery: "Recursion mastery: 55%"
  teacher.python / mistake: "Off-by-one in base case"

build_context(domain_hint="teacher.python")
-> selected_context_items has both records

grep "teacher" src/nexa/core/context/engine.py -> zero matches (confirmed by test)
```

## LIFEOS EXAMPLE (real test, bounded to hinted domain only)

```
Memory: lifeos.sleep: "slept 7h"; lifeos.finance: "grocery purchase"

build_context(domain_hint="lifeos.sleep")
-> selected_context_items contains "slept 7h"
-> does NOT contain "grocery purchase" (finance never hinted, never touched)
```

## CLOUD PROJECTION RESULT

Confirmed equivalent to calling `build_cloud_context_snapshot()` directly:
a test builds a `CloudContextSnapshot` both via `to_cloud_snapshot()` (with
an empty selection) and via the existing function directly with
`context_facts=()`, and asserts the resulting `system_instruction` strings
are byte-identical — proving no privacy logic was reimplemented, only a
new, real caller was added.

## WHAT IS ACTUALLY WIRED INTO RUNTIME

| Component | Implemented | Tested | Wired into a production path | Deferred |
|---|---|---|---|---|
| `namespace_summary()` (Memory discovery) | Yes | Yes (real SQLite) | No — a library capability, no app calls it yet | Wiring into any app entrypoint |
| `ContextEngine` / `MemoryRetriever` | Yes | Yes (real `MemoryService`/`ConversationSession`) | No | Wiring into `bootstrap.py` or any app |
| `nexa.realtime.context_projection.to_cloud_snapshot()` | Yes | Yes (real objects, equivalence-checked against the existing function) | **No** — not passed as `ConversationRouter`'s `snapshot_builder` in `apps/nexa_cloud_voice_simple.py` or anywhere else | Runtime wiring into the accepted cloud voice path |
| `ConversationSession` integration | Yes, as a **reader** (`ContextEngine` correctly consumes real `session.history`, exercised with the real class, not a mock) | Yes | No — `ConversationSession.send()`/`ConversationRouter` do not call `ContextEngine` | Whether/where to call it per-turn |
| Cloud realtime (frozen voice) integration | **Not touched** | N/A | **No** | Explicitly deferred — see below |
| Local provider (`bootstrap.py`) integration | **Not touched** | N/A | **No** | Explicitly deferred — see below |

**On the frozen voice pipeline (§22 of the instruction):** M2.6B, AEC, the
dual-pipeline architecture, and the accepted `apps/nexa_cloud_voice_simple.py`
/ `ConversationRouter` cloud path were **not modified, not reopened, and
not exercised** this checkpoint. `to_cloud_snapshot()` is a straightforward,
tested adapter that WOULD be compatible with `ConversationRouter`'s
existing `snapshot_builder` seam (same eventual output type,
`CloudContextSnapshot`), but actually passing it in was judged to be
runtime-integration work belonging to a dedicated, separately-authorized
task — not something to fold into this already-large milestone without
its own real-hardware verification. Reported as deferred, not claimed as
integrated.

**On the local provider (§23):** `bootstrap.build_default_session()`'s
identity+persona string composition was left untouched, per instruction —
wiring `ContextEngine` output into it would mean deciding exactly how
selected memory items get rendered into the local system prompt, which is
a real design question (not just plumbing) better done as its own
reviewed step, not introduced as a side effect of this report.

## FILES CHANGED

```
NEW:
  src/nexa/core/context/__init__.py
  src/nexa/core/context/models.py
  src/nexa/core/context/retrieval.py
  src/nexa/core/context/memory_retriever.py
  src/nexa/core/context/engine.py
  src/nexa/realtime/context_projection.py
  tests/test_core_context_models.py
  tests/test_core_context_memory_retriever.py
  tests/test_core_context_engine.py
  tests/test_realtime_context_projection.py
  docs/decisions/ADR-0006_context_engine_knowledge_awareness_boundary.md
  docs/reports/R0075_m3_3_context_engine_knowledge_awareness_design_20260916.md (3 revisions, this session)
  docs/reports/R0076_m3_3_context_engine_knowledge_awareness_implementation_20260917.md (this file)

MODIFIED:
  src/nexa/core/privacy.py                 (+ most_restrictive_cloud_eligibility)
  src/nexa/core/memory/models.py            (+ NamespaceSummary)
  src/nexa/core/memory/repository.py         (+ namespace_summary())
  src/nexa/core/memory/service.py             (+ namespace_summary() passthrough)
  src/nexa/core/memory/__init__.py             (+ NamespaceSummary export)
  tests/test_core_privacy.py                    (+ 6 tests)
  tests/test_core_memory_repository.py           (+ 8 tests)
  docs/CURRENT_STATE.md, docs/ROADMAP.md          (checkpoint update, below)
```

## KNOWN LIMITATIONS

Unchanged from R0075's own stated limitations (topic discovery is a weak
substring match, not semantic search; one level of namespace-hierarchy
grouping; conflict detection is structural and `FACT`/`PREFERENCE`-only;
no `explain()`; no real access/permission system behind
`PERMISSION_REQUIRED`). Additionally, from this implementation pass: no
production entrypoint calls `ContextEngine` yet (table above) — this
milestone delivers the foundation, not the wiring, per the instruction's
own explicit scope boundary (§22/§23).

## UNRESOLVED

None specific to M3.3's own scope. Everything explicitly excluded (Learning,
automatic memory extraction, Goals, LiFeOS, NeXa Teacher, UserModel,
Personality, Relationship, capability execution, permission decisions,
device discovery, vector DB, embeddings, FTS, durable transcript store,
model-router redesign, `NeXaCore` god-object) remains unbuilt, as instructed.

## DOCUMENTATION UPDATED

- `docs/decisions/ADR-0006_context_engine_knowledge_awareness_boundary.md` (new).
- `docs/reports/R0075_m3_3_context_engine_knowledge_awareness_design_20260916.md`
  (kept as the historical 3-revision design record, unmodified further).
- This report.
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (concise checkpoint update).
- Historical voice reports: not touched, per instruction.

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO.

## NEXT RECOMMENDED ACTION (do not start without review)

**M3.4 — Personality + Relationship**, per the already-approved ROADMAP.md
M3.1-M3.6 sequence (R0072). Alternatively, if runtime wiring is judged more
valuable next, a scoped follow-up task to wire `to_cloud_snapshot()` into
`ConversationRouter`'s `snapshot_builder` seam and/or `ContextEngine` into
`bootstrap.py` — deliberately not started here, flagged for a decision.
