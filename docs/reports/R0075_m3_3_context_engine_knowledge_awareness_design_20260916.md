# R0075 — M3.3 Context Engine + Knowledge Awareness: Design (Revision 3)

**Date:** 2026-09-16 (revised 2026-09-17, second focused correction pass)
**Milestone:** M3.3 — NeXa Core, Context Engine + Knowledge Awareness (design phase)
**Status:** DESIGN ONLY, REVISED. No implementation. Not committed, not pushed.

## TASK RESULT

**DESIGN, final focused revision for review.**

## REVISION SUMMARY

Revision 2's architecture is approved and unchanged in shape: `nexa.core
-> never nexa.realtime`, `nexa.realtime.context_projection` as the cloud
adapter, typed retrieval results, deterministic descriptor IDs, bounded
retrieval, conversation char/turn budgets, `POTENTIAL_CONFLICT` limited to
`FACT`/`PREFERENCE`, explicit privacy precedence, reason-code-only
traces/gaps, hierarchical awareness, honest topic-vs-domain discovery,
`explain()` deferred, no FTS/vector DB, no Learning, no Teacher/LiFeOS
implementation. This pass fixes 8 remaining semantic gaps, all about
epistemic precision — NeXa's ability to represent *why* something isn't in
context (loaded vs. known-elsewhere vs. searched-and-absent vs.
unreachable vs. permission-gated), which is the actual point of Knowledge
Awareness:

1. **`RetrievalOutcome` gains `NO_MATCH`** — "queried successfully, found
   nothing" is now distinct from both `OK` (found something) and
   `UNAVAILABLE` (couldn't even query). Revision 2's "empty items = no
   gap at all" is corrected: it's a real, informative gap, just not the
   `UNAVAILABLE` one Revision 1 wrongly assigned it.
2. **`KnowledgeAvailability` and `KnowledgeGapState` are now two separate,
   smaller enums** — a descriptor's *source*-level accessibility
   (`AVAILABLE`/`UNAVAILABLE`/`PERMISSION_REQUIRED`) is a different
   concept from an *output*-level epistemic gap
   (`UNKNOWN`/`NO_MATCH`/`UNAVAILABLE`/`PERMISSION_REQUIRED`). `LOADED` is
   removed entirely — it was always redundant with
   `selected_context_items`'s mere presence.
3. **`current_turn` and `conversation_window` no longer overlap** —
   `conversation_window = session.history[:-1]` (bounded PRIOR turns
   only); `current_turn = session.history[-1]`. Revision 2 derived
   `current_turn` correctly but then left it duplicated inside the window.
4. **`KnowledgeDescriptorKind` added** (`DOMAIN` / `DOMAIN_GROUP`) —
   prevents a real future ID collision between a synthesized hierarchical
   group descriptor (e.g. `"lifeos"`) and an actual leaf namespace that
   might someday be named exactly that.
5. **A `DOMAIN_GROUP` is synthesized for one child too**, not only 2+ —
   "I have exactly one known LiFeOS sub-domain" is real, useful awareness.
6. **`DOMAIN_GROUP` non-retrievability is now structural**, not prose —
   `ContextEngine`'s retrieval step only ever considers `DOMAIN`-kind
   descriptors and raises a dedicated error if a `DOMAIN_GROUP` ever
   reaches it.
7. **`namespace_summary()`'s privacy computation is now a single bounded
   aggregate** (`GROUP BY namespace, cloud_eligibility`) — no per-namespace
   N+1 query, no record-content load, output still one row per distinct
   namespace after a small Python-side collapse via
   `most_restrictive_cloud_eligibility()`.
8. **`NO_MATCH` is a first-class, tested outcome** — the "I checked
   `projects.nexa` and that decision isn't recorded" case, distinct from
   "I don't know where to look" (`UNKNOWN`).

---

## Final consolidated type reference

Everything below supersedes Revision 2's type definitions where they
differ; unlisted Revision 2 types (`ContextRequest`, `ContextItem`,
`ContextConflict`, `ConflictType`, `ContextBuildTrace`'s general shape,
`RetrievalQuery`'s non-descriptor fields) are unchanged and not repeated
in full.

### Availability vs. gap — now two purpose-built enums

```python
class KnowledgeAvailability(StrEnum):
    """Accessibility of a KNOWN SOURCE (a KnowledgeDescriptor). If no
    descriptor exists for a domain, the source isn't 'unknown-availability'
    -- there is simply no descriptor; that absence is what produces a
    KnowledgeGap(state=UNKNOWN) downstream, not a value here."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"

class KnowledgeGapState(StrEnum):
    """The epistemic state surfaced in CurrentTurnContext.knowledge_gaps
    -- deliberately NOT the same type as KnowledgeAvailability (§2 of
    review): a gap can be UNKNOWN (no descriptor ever matched), which has
    no equivalent as a property of an existing descriptor."""
    UNKNOWN = "unknown"              # no descriptor matched the hint at all
    NO_MATCH = "no_match"             # descriptor matched; retriever queried
                                        #   successfully; nothing relevant found
    UNAVAILABLE = "unavailable"         # descriptor matched; retriever could
                                          #   not complete the query
    PERMISSION_REQUIRED = "permission_required"  # descriptor matched; access is gated
```

`LOADED` no longer exists anywhere — "I know this now" is represented
structurally by an item's mere presence in `selected_context_items`, never
duplicated as an enum value that could (incorrectly) also be constructed
on a `KnowledgeGap` or a stale `KnowledgeDescriptor`.

### `RetrievalOutcome` — gains `NO_MATCH`

```python
class RetrievalOutcome(StrEnum):
    OK = "ok"                              # query succeeded, items is non-empty
    NO_MATCH = "no_match"                    # query succeeded, items is EMPTY --
                                                #   a real, informative result, not
                                                #   an error and not "no gap"
    UNAVAILABLE = "unavailable"                # query could not be completed --
                                                  #   source unreachable
    PERMISSION_REQUIRED = "permission_required"    # query cannot be completed --
                                                      #   access is gated

RetrievalReasonCode = Literal["source_unreachable", "permission_missing", "retrieval_error"]

@dataclass(frozen=True, slots=True)
class RetrievalResult:
    descriptor_id: str
    outcome: RetrievalOutcome
    items: tuple["ContextItem", ...] = ()
    reason_code: RetrievalReasonCode | None = None   # only set when outcome != OK
    has_more: bool = False

    def __post_init__(self) -> None:
        """Invariant, enforced at construction, not just documented:
        outcome=OK <=> items is non-empty; every other outcome <=> items is empty.
        A retriever cannot accidentally report OK with nothing, or NO_MATCH/
        UNAVAILABLE/PERMISSION_REQUIRED while also carrying items."""
        has_items = bool(self.items)
        if (self.outcome is RetrievalOutcome.OK) != has_items:
            raise ValueError("RetrievalResult.outcome=OK iff items is non-empty")
```

A retriever implementation sets `outcome=NO_MATCH` (not `OK` with an
empty tuple) the moment its underlying query legitimately returns zero
rows — this is the exact fix for the review's hallucination-prevention
concern: NeXa can now represent "I checked the source where this should
be and it wasn't there," not just silence.

### `KnowledgeDescriptor` — gains `kind`, corrected ID scheme

```python
class KnowledgeDescriptorKind(StrEnum):
    DOMAIN = "domain"              # a real, retrievable namespace (leaf)
    DOMAIN_GROUP = "domain_group"    # a synthesized parent/prefix grouping --
                                        #   awareness/navigation only, never retrievable (§6)

@dataclass(frozen=True, slots=True)
class KnowledgeDescriptor:
    id: str                         # see ID scheme below -- deterministic, collision-free
    kind: KnowledgeDescriptorKind
    source_kind: str                 # "memory" (V1); future "teacher", "lifeos", "files"
    domain: str                       # namespace (DOMAIN) or prefix (DOMAIN_GROUP)
    summary: str
    availability: KnowledgeAvailability
    freshness: datetime | None
    cloud_eligibility: CloudEligibility
    child_domains: tuple[str, ...] = ()   # populated ONLY for DOMAIN_GROUP
```

**ID scheme (fixes the collision the review found):**
`id = f"{source_kind}:{kind.value}:{domain}"` — e.g.
`"memory:domain:lifeos.sleep"` vs. `"memory:domain_group:lifeos"`. A real
future leaf namespace literally named `"lifeos"` would get
`"memory:domain:lifeos"`, structurally distinct from the group's
`"memory:domain_group:lifeos"` — no collision is possible by
construction, not by convention. Still no Python `hash()`, still
deterministic across process restarts.

### `RetrievalQuery` — unchanged fields, one new structural rule

Unchanged from Revision 2 (`descriptor_id`, `domain`, `record_type_hint`,
`temporal_intent`, `at`, `limit`) — still no `statuses` field. **New
rule, enforced in the engine, not by the type alone (§6):** a
`RetrievalQuery` is only ever constructed by `ContextEngine`'s own
retrieval step, from a `KnowledgeDescriptor` whose `kind is
KnowledgeDescriptorKind.DOMAIN`. See "Structural non-retrievability of
`DOMAIN_GROUP`" below.

### `KnowledgeGap`

```python
@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    id: str
    query_hint: str
    state: KnowledgeGapState                          # not KnowledgeAvailability (§2)
    reason_code: RetrievalReasonCode | None = None      # only for UNAVAILABLE/PERMISSION_REQUIRED
```

### `CurrentTurnContext` — corrected current-turn/window split

```python
@dataclass(frozen=True, slots=True)
class CurrentTurnContext:
    identity: NeXaIdentity
    current_turn: ConversationTurn                 # session.history[-1], exactly, mandatory
    conversation_window: tuple[ConversationTurn, ...]  # session.history[:-1], BOUNDED, mandatory,
                                                          #   NEVER includes current_turn (§3)
    selected_context_items: tuple[ContextItem, ...]
    knowledge_references: tuple[KnowledgeDescriptor, ...]
    conflicts: tuple[ContextConflict, ...]
    knowledge_gaps: tuple[KnowledgeGap, ...]
    trace: ContextBuildTrace
```

(`current_turn` is now the `ConversationTurn` object itself, not just its
`.content` string, matching what `conversation_window` already holds —
consistent typing, and callers needing just the text use
`current_turn.content`.)

---

## Pipeline — corrected steps 1 and 4 (rest unchanged from Revision 2 §11)

1. **Mandatory context (corrected):**
   ```python
   if not request.session.history or request.session.history[-1].role is not Role.USER:
       raise ContextBuildError(...)
   current_turn = request.session.history[-1]
   conversation_window = _bounded_prior_turns(
       request.session.history[:-1],   # PRIOR turns only -- current_turn excluded by slicing, not by filtering
       max_turns=budget.max_conversation_turns,
       max_chars=budget.max_conversation_chars,
   )
   ```
   `conversation_window` and `current_turn` are non-overlapping by
   construction (`[:-1]` vs. `[-1]` — there is no code path that could
   accidentally include the same turn in both).
2. Deterministic sufficiency check — unchanged.
3. Discovery — unchanged, plus: descriptors returned may now include
   synthesized `DOMAIN_GROUP` ones (§ below); both kinds may appear in
   `knowledge_references` (group descriptors ARE valid awareness output),
   but only `DOMAIN`-kind descriptors are eligible for step 4.
4. **Targeted retrieval (corrected mapping):** for each matched `DOMAIN`
   descriptor (a `DOMAIN_GROUP` descriptor is filtered out here — see
   "Structural non-retrievability" below — never reaches this step),
   call `retrieve()`:
   - `outcome=OK` -> items merge into the candidate pool. No gap.
   - `outcome=NO_MATCH` -> `KnowledgeGap(state=NO_MATCH,
     query_hint=<the hint that led here>)`. **This is new and correct** —
     Revision 2 produced no gap here at all, silently discarding real
     epistemic information ("I checked and it wasn't there" collapsed
     into indistinguishable silence).
   - `outcome=UNAVAILABLE` -> `KnowledgeGap(state=UNAVAILABLE,
     reason_code=result.reason_code)`.
   - `outcome=PERMISSION_REQUIRED` -> `KnowledgeGap(state=PERMISSION_REQUIRED,
     reason_code=result.reason_code)`.
5-9. Unchanged from Revision 2 (merge/trim, conservative
   `FACT`/`PREFERENCE`-only conflict detection, `UNKNOWN` gap for
   zero-descriptor-match hints, trace, return).

---

## Structural non-retrievability of `DOMAIN_GROUP` (§6 of review)

Not left to prose alone:

```python
class RetrievalNotSupportedError(RuntimeError):
    """Raised if a DOMAIN_GROUP descriptor ever reaches the retrieval
    step -- should be structurally unreachable (the pipeline filters to
    DOMAIN-kind before calling retrieve()); this is defense-in-depth, not
    the primary mechanism."""

# inside ContextEngine's retrieval step (step 4 above), the ONLY place a
# RetrievalQuery is ever constructed in normal operation:
def _build_retrieval_query(descriptor: KnowledgeDescriptor, ...) -> RetrievalQuery:
    if descriptor.kind is not KnowledgeDescriptorKind.DOMAIN:
        raise RetrievalNotSupportedError(
            f"descriptor {descriptor.id!r} is {descriptor.kind}, not DOMAIN -- "
            f"group descriptors are awareness-only and are never retrieved"
        )
    return RetrievalQuery(descriptor_id=descriptor.id, domain=descriptor.domain, ...)
```

Two layers: (1) the pipeline's own descriptor-selection step only ever
passes `DOMAIN`-kind descriptors forward to retrieval — a `DOMAIN_GROUP`
match is used solely to populate `knowledge_references`; (2) the
construction helper itself refuses to build a `RetrievalQuery` from
anything else, so even a future pipeline bug can't silently fan out
retrieval across an entire group's children.

---

## Hierarchical namespace awareness — corrected threshold

**Corrected rule (§5 of review):** a `DOMAIN_GROUP` descriptor is
synthesized for **every distinct top-level prefix that has at least one
multi-segment (dotted) leaf namespace beneath it — including exactly
one.** A flat, dot-less namespace (e.g. `"core"`, `"user"`) has no group
synthesized for it, since it isn't a child of anything and grouping it
with itself would add no information.

```
namespaces present: {"lifeos.sleep", "teacher.python", "teacher.math", "core"}

synthesized groups:
  memory:domain_group:lifeos    child_domains=("lifeos.sleep",)               # ONE child -- still synthesized
  memory:domain_group:teacher   child_domains=("teacher.python", "teacher.math")

leaf descriptors (unchanged mechanism):
  memory:domain:lifeos.sleep
  memory:domain:teacher.python
  memory:domain:teacher.math
  memory:domain:core                                                            # no group -- "core" has no dot
```

Still one level of grouping only (first segment); still computed entirely
from the same already-fetched `namespace_summary()` result set — no new
query. Still awareness-only (§ above).

---

## `namespace_summary()` — corrected to a single bounded aggregate (§7 of review)

**Problem, confirmed:** Revision 2 didn't specify how a descriptor's
`cloud_eligibility` gets computed without either loading full records or
issuing one query per namespace.

```sql
SELECT namespace, cloud_eligibility, COUNT(*) AS record_count, MAX(updated_at) AS freshest
FROM memory_records
WHERE status = 'active'
GROUP BY namespace, cloud_eligibility
```

This is **one query**, using the existing `idx_memory_namespace` index for
the `status`/`namespace` portion, and returns **at most (distinct
namespaces x 3)** rows — still small and bounded, never touching
`content`/`payload_json`. `MemoryRepository.namespace_summary()` then does
one Python-side collapse pass, grouping those rows by `namespace` and
reducing each group with the existing `most_restrictive_cloud_eligibility()`
helper:

```python
@dataclass(frozen=True, slots=True)
class NamespaceSummary:
    namespace: str
    record_count: int
    freshest: datetime
    cloud_eligibility: CloudEligibility   # already collapsed -- most restrictive wins

def namespace_summary(self, *, limit: int) -> tuple[NamespaceSummary, ...]:
    rows = self._conn.execute(
        "SELECT namespace, cloud_eligibility, COUNT(*) AS n, MAX(updated_at) AS freshest "
        "FROM memory_records WHERE status = 'active' "
        "GROUP BY namespace, cloud_eligibility"
    ).fetchall()
    by_namespace: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_namespace[row["namespace"]].append(row)
    summaries = [
        NamespaceSummary(
            namespace=ns,
            record_count=sum(r["n"] for r in group),
            freshest=max(_parse_iso(r["freshest"]) for r in group),
            cloud_eligibility=most_restrictive_cloud_eligibility(
                CloudEligibility(r["cloud_eligibility"]) for r in group
            ),
        )
        for ns, group in by_namespace.items()
    ]
    return tuple(sorted(summaries, key=lambda s: s.freshest, reverse=True)[:limit])
```

No N+1 (one SQL call total), no full-record load (only `namespace`,
`cloud_eligibility`, an aggregate count, and an aggregate max-timestamp
cross the SQLite boundary), and the same honest discovery-cost framing
from Revision 1/2 still applies unchanged: the engine still scans matching
index rows internally to compute the `GROUP BY`, but the **result set
loaded into Python is bounded by distinct-namespace-count**, not by total
record count — that remains the property that matters for
`ContextEngine`'s own cost.

---

## `NO_MATCH` end-to-end example (§8 of review)

```
User: "What did we decide about naming for the NeXa memory evidence split?"
caller supplies: domain_hint="projects.nexa"

discovery: memory:domain:projects.nexa -- AVAILABLE (records exist under this namespace)
retrieval: MemoryRetriever.retrieve(RetrievalQuery(domain="projects.nexa", ...))
           -> query executes successfully against by_namespace("projects.nexa", ...)
           -> zero rows match (no record happens to be about this specific decision)
           -> RetrievalResult(outcome=NO_MATCH, items=())

CurrentTurnContext.knowledge_gaps = (
    KnowledgeGap(query_hint="projects.nexa", state=KnowledgeGapState.NO_MATCH),
)
```

The reasoning layer downstream can now honestly say "I checked my NeXa
project knowledge and don't have that decision recorded" — rather than
either fabricating an answer or collapsing this into indistinguishable
silence (Revision 2's bug) or a misleading "source unavailable" claim
(Revision 1's bug).

---

## Everything else — confirmed unchanged from Revision 2

Repository audit and ownership (§1-3 of Revision 2), the domain-vs-topic
discovery honesty and single-method contract (§7), `explain()` deferral
(§8), the `nexa.realtime.context_projection` module placement and
`most_restrictive_cloud_eligibility()` addition to `nexa.core.privacy`
(§9), `ContextItem`'s field set (§6), the conservative
`FACT`/`PREFERENCE`-only `POTENTIAL_CONFLICT` model (§17 of Revision 2),
`ContextBuildTrace`'s reason-code-only content rule, the keep-out list,
and all four stress tests (Teacher/LiFeOS/Projects/large-scale) are not
repeated here in full — none of them are affected by this pass's 8
corrections.

## Proposed modules — one addition

```
src/nexa/core/context/
  __init__.py
  models.py            # (adds KnowledgeDescriptorKind, KnowledgeGapState,
                        #   NamespaceSummary import; RetrievalOutcome gains NO_MATCH;
                        #   KnowledgeAvailability loses LOADED)
  retrieval.py           # ContextRetriever (Protocol), RetrievalQuery, RetrievalNotSupportedError
  memory_retriever.py      # MemoryRetriever
  engine.py                  # ContextEngine (incl. _build_retrieval_query guard)

src/nexa/realtime/context_projection.py    # unchanged from Revision 2
src/nexa/core/privacy.py                    # unchanged addition from Revision 2
```

No structural module changes beyond the type additions above.

---

## Acceptance tests (final, supersedes Revision 2's list)

1. **`nexa/core/context/**` has zero imports from `nexa.realtime`.**
2. **The pre-existing M3.2 full-tree test
   (`test_entire_core_package_has_zero_imports_from_realtime`) still
   passes unmodified.**
3. **Cloud projection lives outside Core** —
   `nexa.realtime.context_projection` depends inward on
   `nexa.core.context`, never the reverse.
4. **Descriptor IDs are stable across a fresh process/interpreter.**
5. **`KnowledgeAvailability` describes only known-source accessibility** —
   a test enumerates its members and confirms `LOADED` and `UNKNOWN` are
   not among them.
6. **`KnowledgeGapState.UNKNOWN` means no known matching source** — a
   hint matching zero descriptors produces exactly this state.
7. **`NO_MATCH` means source successfully queried, nothing relevant
   found** — the §8 example, end to end, through a real `MemoryRetriever`
   against real (empty-for-the-hint) Memory state.
8. **`UNAVAILABLE` remains distinct from `NO_MATCH`** — a retriever test
   double that raises/fails produces `UNAVAILABLE`, never `NO_MATCH`.
9. **`PERMISSION_REQUIRED` remains distinct from both** — a retriever
   test double explicitly returning it produces exactly that state.
10. **Loaded knowledge is represented only by `selected_context_items`
    membership** — no code path constructs a `KnowledgeGap` or
    `KnowledgeDescriptor` with anything resembling a "loaded" value (there
    is no such value to construct — enforced by the type itself, tested
    by enumerating both enums).
11. **`current_turn` is derived from `session.history[-1]`.**
12. **`conversation_window` contains only prior turns and never contains
    the current turn** — corrected from Revision 2's wrong
    `conversation_window[-1] == current_turn` invariant: assert
    `current_turn not in conversation_window` and
    `conversation_window == tuple(session.history[:-1])[-N:]` for the
    applicable bound `N`.
13. **Conversation window remains turn/char bounded**, applied to prior
    turns only — unchanged bound semantics, corrected scope.
14. **`build_context()` fails loudly on a malformed precondition** —
    unchanged from Revision 2.
15. **A real `DOMAIN` leaf and a synthesized `DOMAIN_GROUP` coexist
    without ID collision** — construct Memory state containing a
    namespace literally named `"lifeos"` alongside `"lifeos.sleep"`;
    assert both descriptors exist with distinct IDs
    (`memory:domain:lifeos` vs. `memory:domain_group:lifeos`) and correct
    `kind` values.
16. **`DOMAIN_GROUP` is synthesized for exactly one child** — Memory
    state containing only `"lifeos.sleep"` still produces a
    `memory:domain_group:lifeos` descriptor with
    `child_domains=("lifeos.sleep",)`.
17. **`DOMAIN_GROUP` is never passed to `retriever.retrieve()`** — a
    `domain_hint` that only resolves to a group descriptor produces zero
    `retrieve()` calls (spy/mock assertion) and zero
    `selected_context_items`; a direct attempt to build a
    `RetrievalQuery` from a `DOMAIN_GROUP` descriptor raises
    `RetrievalNotSupportedError`.
18. **`namespace_summary()` requires no record-content load and no N+1
    query** — a spy/mock on the SQLite connection asserts exactly one
    `execute()` call for a `namespace_summary()` invocation regardless of
    how many distinct namespaces exist, and that the SQL text selects only
    `namespace`/`cloud_eligibility`/aggregate columns, never `content` or
    `payload_json`.
19. **Mixed-eligibility domain gets the correct most-restrictive
    descriptor eligibility** — a namespace with both `CLOUD_SAFE` and
    `LOCAL_ONLY` records produces `NamespaceSummary.cloud_eligibility ==
    LOCAL_ONLY`, and the resulting `KnowledgeDescriptor.cloud_eligibility`
    matches.
20. **Successful targeted retrieval with zero matches produces
    `KnowledgeGapState.NO_MATCH`** — the primary new behavior (§8),
    tested directly against `ContextEngine.build_context()`'s output, not
    just the retriever's return value in isolation.
21. **`ContextBuildTrace` represents the `NO_MATCH` attempt without raw
    query/private content** — `retrieval_attempts` records
    `outcome=NO_MATCH` and the relevant `descriptor_id`/`item_count=0`,
    with no query text or record content anywhere in the trace.
22. **`RetrievalResult`'s own invariant is enforced at construction** —
    constructing `RetrievalResult(outcome=OK, items=())` or
    `RetrievalResult(outcome=NO_MATCH, items=(some_item,))` raises
    `ValueError` immediately (the `__post_init__` guard).
23. **Descriptor privacy uses the explicit precedence helper, never enum
    ordering** — unchanged from Revision 2, now additionally exercised
    through the corrected `namespace_summary()` aggregate.
24. **Trace and gap fields contain no raw exception/private content** —
    unchanged from Revision 2.
25. **`LOCAL_ONLY`/`CLOUD_WITH_USER_APPROVAL` privacy crossing rules** —
    unchanged from Revision 2, exercised through
    `nexa.realtime.context_projection.to_cloud_snapshot()`.
26. **`CloudContextSnapshot` remains an unmodified projection.**
27. **No memory writes; `ConversationSession`/`MemoryService` remain
    their own authorities.**
28. **Evidence isn't dumped automatically** (no `explain()` exists to
    test in this design).
29. **Deterministic context; bounded; no unbounded Memory retrieval;
    relevant-domain selection with unrelated-domain exclusion; historical
    vs. current correctness; `RETRACTED` never appears normally;
    dynamic-knowledge-without-restart; huge-memory scenario never loads
    all records; Teacher/LiFeOS/Projects scenarios work without
    domain-specific code in `ContextEngine`** — all unchanged from
    Revision 2, still required, not re-derived here.
30. **Natural-topic-discovery limitation remains explicitly tested** —
    unchanged from Revision 2 (a paraphrase/synonym query that doesn't
    literally appear in any descriptor's `domain`/`summary` produces
    `KnowledgeGapState.UNKNOWN`, not a false match).

---

## Risks / limitations — unchanged from Revision 2, restated

Topic discovery remains genuinely weak in V1 (substring match only); one
level of namespace-hierarchy grouping only; conflict detection stays
structural and `FACT`/`PREFERENCE`-only; `explain()` doesn't exist yet;
local-provider projection wiring is still an open implementation-time
decision; `PERMISSION_REQUIRED` remains a forward-compatible hook with no
real access-control system behind it yet. None of Revision 3's
corrections change any of these — they fix internal consistency, not
scope.

---

## Implementation readiness

These corrections fit cleanly against the real repository as audited
(§1 of Revision 1, re-confirmed in Revision 2 §10's flow check): the
`namespace_summary()` aggregate uses only columns and an index
(`idx_memory_namespace`) that already exist in the shipped M3.2 schema;
`most_restrictive_cloud_eligibility()` extends the already-shipped
`nexa.core.privacy` module; the `session.history[-1]`/`[:-1]` split
matches `ConversationSession.send()`'s confirmed real append-then-build
order exactly; and the corrected type set (three small, non-overlapping
enums instead of one overloaded one, plus `RetrievalResult`'s
construction-time invariant) removes every remaining internal
inconsistency raised across all three review passes without touching any
other part of the codebase. **The design is implementation-ready.**

## NEXT STEP

Implementation of M3.3 against this design, gated on review. Not started
in this checkpoint, per explicit instruction.
