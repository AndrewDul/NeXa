# ADR-0006 — Context Engine and Knowledge Awareness boundary

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Andrzej Dul (owner), Claude Code (agent)
- **Related:** ADR-0004 (cloud realtime voice provider boundary — states
  the coarse NeXa-Core-vs-provider ownership split but not Context
  Engine's internal architecture), ADR-0005 (Identity root boundary —
  explicitly named "future Context Engine" without designing it),
  `docs/reports/R0075_m3_3_context_engine_knowledge_awareness_design_20260916.md`
  (three design revisions), `docs/reports/R0076_m3_3_context_engine_knowledge_awareness_implementation_20260917.md`

---

## Context

`VERIFIED FACT` — ADR-0005 D1 established `NeXaIdentity` as a small,
immutable root, distinct from every mutable NeXa Core subsystem, and its
own docstring (`src/nexa/core/identity/render.py`) explicitly deferred
"a general context/prompt engine" as "a future, separate Context Engine."
M3.2 (ADR unaffected, R0073/R0074) then built `nexa.core.memory` as the
canonical long-term memory authority, with bounded, typed retrieval
methods (`by_namespace`, `by_scope`, `recent`, `valid_at`) but no consumer
that selects across memories for a given conversational turn.

`OBSERVATION` — Nothing in `src/nexa/` before this decision selected,
bounded, or composed a "current turn's" context from more than one Core
authority (Identity, Memory, live conversation transcript) at once, and
nothing represented *awareness* of knowledge that exists but isn't loaded
— a real gap for preventing hallucination ("I don't know" vs. "I know
where to look but haven't looked" vs. "I looked and found nothing" are
different, useful statements NeXa could not previously make).

`ASSUMPTION` — The product intent (ADR-0001 D2: local-first, one
canonical authority per responsibility) extends to this new concern
exactly as it already extends to Identity and Memory: NeXa needs one
selection/composition layer, not a per-provider or per-domain duplicate,
and needs an explicit way to represent "knowledge that exists but is not
currently loaded" without duplicating that knowledge into a second store.

## Decision

**We will:**

### D1 — `ContextEngine` owns selection/composition/retrieval orchestration only

`nexa.core.context.ContextEngine` is the one canonical authority for
turning `(NeXaIdentity, ConversationSession, MemoryService, ...)` into a
provider-neutral `CurrentTurnContext` for one conversational turn. It
never persists a second memory, never writes to `MemoryService`, never
mutates `NeXaIdentity`, never appends to `ConversationSession.history`,
and never becomes provider-specific (no Gemini/cloud-SDK knowledge
anywhere in `nexa.core.context`).

### D2 — Knowledge Awareness is metadata, not a second knowledge store

`KnowledgeDescriptor` (what/where knowledge exists, cheaply, at
namespace/domain granularity) is generated **live** from the real
source (`MemoryRepository.namespace_summary()`, one bounded SQL
aggregate) on every discovery call — never cached into a separate
"knowledge map" database. This is what makes newly-written knowledge
discoverable on the very next turn with no engine restart, no retriever
reconstruction, and no hardcoded domain registration (verified,
R0076 — dynamic-knowledge acceptance test).

### D3 — Provider projection lives outside Core, always

Cloud-provider-specific projection (`CurrentTurnContext` ->
`CloudContextSnapshot`) lives in `nexa.realtime.context_projection`, not
`nexa.core.context` — `nexa.core` must never depend on `nexa.realtime`
(the same invariant M3.2 already established for `CloudEligibility`,
ADR-0005; enforced here by the same kind of source-scan test, still
passing unmodified after this change). There is exactly one
`ContextEngine`, not a `CloudContextEngine`/`LocalContextEngine` split —
the same selection logic runs regardless of which provider will answer;
only the downstream projection step differs.

### D4 — Epistemic states are represented by three purpose-built types, not one

`KnowledgeAvailability` (a known source's accessibility),
`RetrievalOutcome` (one retrieve() call's result, including the
`NO_MATCH` "queried successfully, found nothing" state distinct from both
success-with-results and unreachable), and `KnowledgeGapState` (the
aggregate epistemic gap surfaced in output, including `UNKNOWN` — "no
descriptor ever matched," which has no equivalent as a property of an
existing descriptor) are three separate small enums. Collapsing them into
one would allow illegal states (e.g. a gap with state "OK", or a
descriptor with state "loaded") that this design deliberately makes
unconstructible.

### D5 — RETRACTED memory is structurally excluded from normal context

`RetrievalQuery` (the contract between `ContextEngine` and any
`ContextRetriever`) has no `statuses`/`include_retracted` field at all —
not merely a default that excludes retracted knowledge, but no way to
request it through the normal contract. `MemoryRetriever` always calls
`MemoryService`'s own already-safe default status filters
(`CURRENT_STATUSES`/`HISTORICAL_STATUSES`, M3.2), never an override.

## Options considered

### Option A (chosen) — `ContextEngine` as a thin orchestration layer over existing authorities, Knowledge Awareness as live metadata
- Pros: no new canonical store to keep consistent with Memory; new domains
  (Teacher, LiFeOS, Projects) become usable with zero Context Engine code
  changes, proven by stress tests; matches the one-canonical-authority
  principle already established for Identity and Memory.
- Cons: discovery cost is a real (bounded) query on every turn rather than
  an O(1) cache lookup — acceptable at current and near-future scale
  (R0075 §35's honest framing), revisited only if measurement shows it
  matters.

### Option B — A separate, persisted Knowledge Map / index database
- Pros: potentially faster discovery at very large scale.
- Cons: a second store that can drift out of sync with canonical Memory;
  exactly the kind of duplicate-authority problem ADR-0001 already
  rejected for the legacy MAS (`memory_bus`/`memory_retriever` split
  across dozens of packages); not justified by any measured need today.

### Option C — Do nothing; let each provider adapter query Memory directly
- Pros: zero new code.
- Cons: recreates the exact problem this ADR exists to prevent — no
  bounded selection, no epistemic-gap representation, no reusable
  discovery mechanism, and each future provider/domain would reinvent its
  own selection logic (a new "prompt builder" per call site, the same
  anti-pattern named in Option B).

## Consequences

- Positive: Teacher/LiFeOS/Projects domains are usable through
  `MemoryRetriever` with zero domain-specific `ContextEngine` code,
  proven by stress tests, not asserted; NeXa can now represent "I don't
  know," "I know where to look," "I looked and found nothing," and "I
  found it" as four genuinely distinct, testable states.
- Negative / costs: three new small enum types and a typed
  `RetrievalResult`/`KnowledgeGap` model to learn; discovery is a live
  query, so very large namespace *cardinality* (not record count) is the
  one dimension that could eventually need revisiting.
- Follow-up work this creates: M3.4+ (Personality/Relationship,
  Capabilities/Permissions, Learning) and any future `TeacherRetriever`/
  `LiFeOSRetriever`/`FileRetriever` build against the
  `ContextRetriever` contract established here, not a new one.
- What this constrains for future milestones: no future subsystem may
  build its own parallel "prompt builder" or context-selection path — a
  new knowledge source becomes a new `ContextRetriever`, not a new
  authority.

## Compliance / review

Any PR that adds a `ContextRetriever` implementation, a new provider
projection adapter, or a field to `NeXaIdentity`/`MemoryRecord` to work
around `ContextEngine`'s contract should be reviewed against this ADR.
Revisit D2 (live discovery vs. a cached index) if measurement — not
speculation — shows discovery cost matters at real scale; revisit D3 if a
genuinely new provider-projection shape can't be expressed as an adapter
outside Core.
