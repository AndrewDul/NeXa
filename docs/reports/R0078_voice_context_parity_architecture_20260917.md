# R0078 — Voice Context Parity Architecture (Revision 2)

**Date:** 2026-09-17
**Type:** Design / Audit — **NO IMPLEMENTATION**
**Status:** Draft for review
**Depends on:** R0075/R0076/R0077 (M3.3 Context Engine + Knowledge Awareness, accepted `10795d9`/`04b60fb`), R0071 (frozen cloud voice baseline, `83194c4`), M2.5B (local voice barge-in, accepted), ADR-0004 (realtime provider contract), ADR-0006 (Context Engine boundary)

> This report changes **no code**. Every file reference is a citation of
> the *current, real* source (paths + line numbers as of `04b60fb`), not a
> proposal. No commit, no push, no touch to the frozen audio pipeline.

---

## REVISION SUMMARY

Revision 1's primary architecture decision is **approved and unchanged**:
cloud session-stable context via session-start projection; cloud
turn-dynamic knowledge via Pattern B (Core recall as a provider tool);
local voice via `ContextEngine.build_context()` threaded through
`ProviderWindow`; R0071's frozen audio/interruption architecture untouched.

This revision fixes one internal inconsistency Revision 1 left unresolved,
plus nineteen related boundary/precision issues raised in review:

1. **The critical fix (§1–§5):** Revision 1 proposed routing a cloud tool
   call through `derive_context_request(session)` → `ContextEngine.
   build_context()` — but R0078 §4 itself already proved
   `session.history` never contains the current USER turn during a live
   cloud turn. That path is internally inconsistent and is corrected here:
   `ContextEngine` gains a second, session-independent entry mode,
   `recall(RecallRequest) -> RecallResult`, sharing every internal
   retrieval/selection/budget/privacy-metadata primitive with
   `build_context()` — **one engine, two entry modes, no second
   authority.**
2. Session-stable context gets an explicit provider-neutral seam (§6) so
   `CloudContextSnapshot` is confirmed as an **outward projection**, never
   the canonical owner of future Personality/Relationship/Goals/UserModel
   state.
3. Identity/persona ownership is stated explicitly to prevent future
   duplication (§7).
4. Gemini's blocking-vs-non-blocking tool-call behavior is now cited from
   the installed SDK (§8) rather than assumed, with an explicit
   implementation-time re-verification requirement.
5. A Core-owned bounded recall timeout replaces any reliance on
   provider-side timeout behavior (§9).
6. `CLOUD_WITH_USER_APPROVAL` is now folded into the same
   wire-indistinguishable-from-`NO_MATCH` rule Revision 1 already applied
   to `LOCAL_ONLY` (§10) — Revision 1 under-specified this.
7. Tool arguments are explicitly minimal/untrusted, with a stated maximum
   query length and a closed set of caller-controllable fields (§11).
8. `RETRACTED` unreachability is confirmed structurally preserved by pure
   reuse, not by new code (§12).
9. Model tool-use compliance gets an explicit acceptance-test design (§13).
10. Local voice (§14) and PL/EN derivation (§15) are **unchanged and
    reconfirmed** — Revision 1 got these right.
11. The parity matrix is corrected (§16) to reflect that once §2/§14 are
    implemented, local voice's turn-dynamic recall path is the *same*
    `build_context()` call typed chat already uses — not a second,
    lesser mechanism.
12. A final canonical diagram (§17) and an updated 21-item acceptance plan
    (§18) close the report.

Sections 2 (problem restated), 3 (do-not-reopen-audio), 4 (cloud timeline
audit), 5 (SDK capability audit table), 17–22 of Revision 1's own text
(latency/interruption/knowledge-gap-vocabulary/risks) are **unchanged in
substance** and are not reproduced verbatim below except where a fix
requires quoting them — Revision 1 remains the source of record for the
raw audit evidence (cloud turn timeline, Gemini Live capability table).
This revision is additive/corrective on top of that evidence, per the
instruction to fix boundary issues, not broadly redesign.

---

## 1. The critical fix, restated precisely

Revision 1's own audit (unchanged, still correct):

```
ConversationRouter.handle_provider_event() only calls commit_cloud_turn()
on GenerationCompleteEvent (router.py:345-346); commit_cloud_turn() is the
ONLY call to session.record_external_exchange() (router.py:289-305) — the
ONLY path that appends a cloud turn to session.history. Mid-turn events
(UserTranscriptionEvent etc.) only feed CloudTurnAccumulator's internal
buffer, never session.history.

=> session.history[-1] cannot be "the current user turn" at any point
   during cloud generation.
```

But `ContextEngine.build_context()` has a hard, correctly-enforced
precondition (`engine.py:148-167`, `_mandatory_turns`):

```python
history = request.session.history
if not history or history[-1].role is not Role.USER:
    raise ContextBuildError(...)
```

Revision 1's proposed cloud tool path (`CoreRecallRequest(query_text,
session)` → `derive_context_request(session)` → `build_context()`) would
either crash on every cloud tool call (no current turn exists) or require
fabricating a fake session/turn to satisfy the precondition — exactly the
"pretend the current user turn exists" anti-pattern this review correctly
forbids. **This was a real defect in Revision 1's design, not a
restatement of the already-known audit finding.** It is fixed below.

---

## 2. One Context Engine, two entry modes

Audited the real internals (`engine.py`) to find the smallest correct
refactor rather than inventing a parallel structure.

**Finding:** `ContextEngine._discover()`, `_retrieve()`, and
`_build_retrieval_query()` — the entire retrieval/selection/budget
pipeline — **already never read `request.session`.** The only method that
touches `request.session` anywhere in `ContextEngine` is
`_mandatory_turns()` (`engine.py:148-167`), which exists solely to produce
`current_turn`/`conversation_window` for `CurrentTurnContext`. Concretely,
verified line by line:

- `_discover(self, request)` (`engine.py:169-174`) calls
  `retriever.describe_available_knowledge(request)` for each retriever —
  and `MemoryRetriever.describe_available_knowledge()` (`memory_retriever.py:121-143`)
  reads **only** `request.domain_hint` and `request.subject_hints`, never
  `request.session`.
- `_retrieve()` (`engine.py:176-230`) and `_build_retrieval_query()`
  (`engine.py:233-247`) read only `request.temporal_intent`,
  `request.historical_at`, and the separately-passed `budget` — never
  `request.session`.
- `_trim_to_budget()` and `_detect_conflicts()` operate purely on already-
  retrieved `ContextItem`s — no session dependency at all.

So the retrieval engine was **already session-agnostic internally**; only
`ContextRequest`'s *type* (and the `ContextRetriever` Protocol's signature,
which passes the whole `ContextRequest` through even though it only ever
reads two fields of it) coupled it to a session that turn-dynamic cloud
recall cannot supply. The fix is a narrow signature refactor, not a new
subsystem:

```
ContextEngine
├── build_context(ContextRequest) -> CurrentTurnContext
│      unchanged precondition: session.history[-1] == current USER turn
│      unchanged: current_turn, conversation_window, identity in the result
│      used by: typed local (LIVE), local voice (§14, designed)
│
└── recall(RecallRequest) -> RecallResult                    [NEW]
       no ConversationSession required, no current-turn precondition
       reuses the SAME _discover/_retrieve/_build_retrieval_query/
       _trim_to_budget internals, the SAME self._retrievers tuple,
       the SAME MemoryRetriever instance, the SAME privacy metadata
       (ContextItem.cloud_eligibility) — nothing duplicated
       used by: cloud realtime tool calls (Pattern B, designed here)
```

**Concrete refactor shape** (design only — not implemented by this
report): extract the two fields `_discover`/`retriever.describe_available_
knowledge()` actually use into the call signature directly, so the
`ContextRetriever` Protocol stops depending on the full `ContextRequest`
shape it was only ever partially using:

```python
# retrieval.py — Protocol signature narrowed to what retrievers actually read
class ContextRetriever(Protocol):
    source_kind: str

    def describe_available_knowledge(
        self, *, domain_hint: str | None, subject_hints: tuple[str, ...]
    ) -> tuple[KnowledgeDescriptor, ...]: ...

    def retrieve(self, query: RetrievalQuery, budget: ContextBudget) -> RetrievalResult: ...
```

```python
# engine.py — private helpers take the fields they use, not a whole request
def _discover(self, *, domain_hint: str | None, subject_hints: tuple[str, ...]
              ) -> list[KnowledgeDescriptor]: ...

def _retrieve(self, descriptors, *, temporal_intent, historical_at, budget
              ) -> tuple[list[ContextItem], list[RetrievalAttempt], list[KnowledgeGap], int]: ...
```

```python
def build_context(self, request: ContextRequest) -> CurrentTurnContext:
    current_turn, conversation_window = self._mandatory_turns(request, budget)  # unchanged
    all_descriptors = self._discover(
        domain_hint=request.domain_hint, subject_hints=request.subject_hints
    )
    selected_items, attempts, gaps, rounds = self._retrieve(
        capped_descriptors,
        temporal_intent=request.temporal_intent,
        historical_at=request.historical_at,
        budget=budget,
    )
    ...  # everything else unchanged

def recall(self, request: RecallRequest) -> RecallResult:
    budget = request.budget or RecallBudget()
    all_descriptors = self._discover(
        domain_hint=request.domain_hint,
        subject_hints=derive_subject_hints(request.query_text),  # §4
    )
    capped = all_descriptors[: budget.max_knowledge_references]
    selected_items, attempts, gaps, rounds = self._retrieve(
        capped,
        temporal_intent=request.temporal_intent,
        historical_at=request.historical_at,
        budget=budget,
    )
    trimmed, rejected = _trim_to_budget(selected_items, budget)   # SAME helper
    # no conflict detection needed for a single targeted recall result
    # (conflict detection is a CurrentTurnContext-composition concern —
    #  recall() returns a flat, bounded item set, not a composed turn)
    outcome = _recall_outcome(trimmed, gaps)                       # §3
    return RecallResult(items=tuple(trimmed), outcome=outcome, knowledge_gaps=tuple(gaps), trace=...)
```

No `CloudMemoryEngine`, `GeminiMemoryService`, or `RecallDatabase` is
introduced. `MemoryRetriever` is constructed exactly once, in `nexa.
bootstrap.build_default_context_runtime()` (unchanged, R0077), and is
shared by both entry modes through the same `ContextEngine._retrievers`
tuple — verified today by `test_bootstrap_context_runtime.py::
test_context_engine_has_exactly_one_memory_retriever` (existing test,
unchanged), which becomes the natural place to add "and `recall()` uses
that same instance" as a new assertion at implementation time (§18 item 3).

---

## 3. `RecallRequest` / `RecallResult` / `RecallBudget` — provider-neutral shapes

Design only, living in `nexa.core.context.models` alongside the existing
M3.3 types (not a new module, not a new package — same file, same
review-tested discipline: frozen dataclasses, closed-vocabulary outcomes,
invariants enforced at construction).

```python
class RecallOutcome(StrEnum):
    """The result of ONE ContextEngine.recall() call — deliberately
    smaller than RetrievalOutcome/KnowledgeGapState: recall() is a single
    flat operation, not a multi-descriptor build pipeline, so it reports
    one outcome for the whole call, not per-descriptor attempts."""
    FOUND = "found"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"


#: Untrusted-input bound (§11) — enforced at construction, not a tunable
#: budget knob (it's an input-validation concern, not a resource budget).
_MAX_RECALL_QUERY_CHARS = 500


@dataclass(frozen=True, slots=True)
class RecallRequest:
    """Self-contained: the caller's query text IS the information need.
    No ConversationSession, because turn-dynamic cloud recall (§1) has no
    canonical current turn to point to during a live cloud turn. Historical
    prior-conversation state may be added as an explicit, separate field
    later ONLY if a real, audited need for it emerges — never smuggled in
    by attaching a session."""

    query_text: str
    domain_hint: str | None = None
    temporal_intent: TemporalIntent = TemporalIntent.CURRENT
    historical_at: datetime | None = None
    budget: "RecallBudget | None" = None

    def __post_init__(self) -> None:
        if not self.query_text or not self.query_text.strip():
            raise ValueError("RecallRequest.query_text must not be blank")
        if len(self.query_text) > _MAX_RECALL_QUERY_CHARS:
            raise ValueError(
                f"RecallRequest.query_text is {len(self.query_text)} chars, "
                f"exceeds max {_MAX_RECALL_QUERY_CHARS} (reason=untrusted "
                f"provider input must be bounded, §11)"
            )
        if self.temporal_intent is TemporalIntent.HISTORICAL and self.historical_at is None:
            raise ValueError("RecallRequest(temporal_intent=HISTORICAL) requires historical_at")


@dataclass(frozen=True, slots=True)
class RecallBudget:
    """Deliberately narrower than ContextBudget — no
    max_conversation_turns/max_conversation_chars/max_current_turn_chars,
    because recall() has no conversation window or current-turn concept at
    all (§1). Same defaults as the equivalent ContextBudget fields."""
    max_items: int = 20
    max_content_chars: int = 4000
    max_items_per_source: int = 10
    max_retrieval_rounds: int = 2
    max_knowledge_references: int = 10


@dataclass(frozen=True, slots=True)
class RecallResult:
    """Provider-neutral. Never a MemoryRecord, never a SQL row, never a
    KnowledgeDescriptor (no descriptor list is ever included — recall()
    callers get bounded, already-selected ContextItems only, same
    Knowledge-Awareness-stays-internal rule build_context() already
    follows). ContextItem.cloud_eligibility travels with each item
    UNFILTERED here — exactly like CurrentTurnContext.selected_context_items
    today (MemoryRetriever never applies a cloud filter at retrieval time,
    memory_retriever.py:3-5) — because recall() is provider-neutral and a
    future local-provider caller may legitimately want LOCAL_ONLY content.
    Privacy filtering to a SPECIFIC provider happens at the adapter
    boundary (§10), exactly where CloudContextSnapshot projection already
    does it today (to_cloud_snapshot(), outside nexa.core)."""

    outcome: RecallOutcome
    items: tuple[ContextItem, ...] = field(default_factory=tuple)
    knowledge_gaps: tuple[KnowledgeGap, ...] = field(default_factory=tuple)
    trace: ContextBuildTrace | None = None  # Core-internal only; never serialized to a provider
```

`_recall_outcome()` (private helper, `engine.py`): `FOUND` iff
`trimmed` is non-empty; else the single most-specific gap state present
(`PERMISSION_REQUIRED` > `UNAVAILABLE` > `NO_MATCH`, mirroring the existing
precedence discipline `most_restrictive_cloud_eligibility()` already
established for privacy — closed, explicit, never enum-declaration-order).

**Why this satisfies §5's "do not return Gemini types" requirement:**
`RecallResult` lives in `nexa.core.context.models`, imports nothing from
`nexa.realtime`, and is exactly as provider-neutral as
`CurrentTurnContext` already is. The adapter that maps it onto a
`FunctionResponse` dict lives in `nexa.realtime.gemini`, outside Core —
symmetric with how `to_cloud_snapshot()` already maps `CurrentTurnContext.
selected_context_items` onto `CloudContextSnapshot` today (R0077,
unchanged).

---

## 4. Subject-hint derivation — already correct, zero changes needed

Re-audited `derive_subject_hints()` (`derivation.py:47-71`): its signature
is already `derive_subject_hints(text: str) -> tuple[str, ...]` — it never
took a `ConversationSession` in the first place. Only the *wrapper*,
`derive_context_request(session, ...)` (`derivation.py:74-102`), extracts
`session.history[-1].content` before calling it. This means:

```
typed/local:  derive_subject_hints(session.history[-1].content)  [via derive_context_request, unchanged]
cloud recall: derive_subject_hints(recall_request.query_text)     [new call site, same function]
```

**Zero changes to `derive_subject_hints()` itself.** The only new code is
one more call site inside `ContextEngine.recall()` (§2), calling the exact
same, already-shipped, already-tested function. No Gemini-specific
classification, no hardcoded domain mapping — confirmed unchanged from
Revision 1's design intent, now precisely located in the real code.

---

## 5. Provider adapter mapping (Gemini-specific, outside Core)

New module (design only, not created): `nexa.realtime.gemini.
core_recall_tool` (or folded into `simple_conversation.py` at
implementation time if smaller — decide then, not here).

```
Gemini function-call arguments (untrusted, minimal — §11)
    {"query": "<text the model wants to know>"}
        ↓ adapter validates/normalizes, constructs
RecallRequest(query_text=args["query"])
        ↓
ContextEngine.recall(request)   # the SAME engine instance typed chat uses
        ↓
RecallResult
        ↓ adapter applies §10's privacy-wire-folding, THEN maps to
{"status": "found" | "no_match", "content": "<bounded text>" | None}
        ↓ passed to Pipecat's _tool_result(tool_call_id, tool_name, response_dict)
```

A future OpenAI/Anthropic-realtime adapter would implement the same two
arrows (args → `RecallRequest`, `RecallResult` → that provider's own
tool-response shape) in its own module — `RecallRequest`/`RecallResult`
never change.

---

## 6. Session-stable context — provider-neutral Core seam

Confirmed correct in Revision 1 that `CloudContextSnapshot` already
*functions* as the cloud session-start projection. The gap Revision 2
closes: nothing currently states that it must never become the *canonical
owner* of session-stable state once Personality/Relationship/Goals/
UserModel exist (M3.4+).

**Smallest clean seam (design only):**

```python
# nexa.core.context.session_context (or a similarly small, new module —
# exact placement decided at implementation time; the point is it lives
# in nexa.core, is provider-neutral, and owns nothing itself)

@dataclass(frozen=True, slots=True)
class CoreSessionContext:
    """Bounded, session-start-only facts Core is willing to hand to ANY
    provider adapter at session start. Read-only projection over Core's
    real state (Identity today; future Personality/Relationship baseline,
    stable preferences, active-project, permission/capability policy) —
    never a second copy with independent lifecycle. Core computes this
    fresh on demand; nothing caches or owns a CoreSessionContext instance
    beyond the call that built it."""
    identity: NeXaIdentity
    stable_preferences: tuple[str, ...] = ()      # future
    active_project_hint: str | None = None         # future
    # Personality/Relationship/permission-policy fields added only when
    # those milestones exist — NOT stubbed in now (§7 of the user's review:
    # "do not add future Personality/Relationship content yet").


def build_session_context(identity: NeXaIdentity, ...) -> CoreSessionContext:
    """Provider-neutral. Lives in nexa.core.context, imports nothing from
    nexa.realtime. Analogous in spirit to derive_context_request(), but
    for session-start rather than per-turn data."""
```

```
nexa.core (CoreSessionContext, build_session_context)
        ↓
nexa.realtime.context_projection.to_cloud_snapshot()  [EXISTING, R0077 — extended,
                                                         not replaced, to read from
                                                         CoreSessionContext instead of
                                                         constructing ad hoc fields]
        ↓
CloudContextSnapshot   [outward cloud projection ONLY — not the authority]
```

This is intentionally the **smallest** seam: one small, read-only,
provider-neutral dataclass and one builder function, not a subsystem. It
establishes the layering the user's review requires — `CloudContextSnapshot`
is confirmed as a **projection**, not a **model** — without inventing
Personality/Relationship/Goals content that does not exist yet (§22 of
Revision 1, unchanged: M3.4 is still out of scope).

**Not decided in this report, deferred to implementation:** whether
`build_cloud_context_snapshot()`'s existing call sites are worth touching
at all before M3.4 actually needs `CoreSessionContext` fields beyond what
`CloudContextSnapshot` already carries (identity, language preference).
Revision 2's position: establish the seam's *shape* now (so M3.4 has
somewhere provider-neutral to add fields), but do not force a refactor of
already-working, hardware-accepted code paths (R0071) merely to route
through it before there is new content to carry.

---

## 7. Identity ownership — explicit, no duplication

Stated explicitly, as required:

- **`NeXaIdentity` itself** (the canonical model/state) is owned by
  `nexa.core.identity` (M3.1, unchanged) — the only authority.
- **Local typed/voice prompt rendering** — `render_identity_instruction()`
  composed once in `nexa.bootstrap` into `ConversationSession.system_prompt`
  (M3.1/R0077, unchanged) — this is the ONLY place identity text is
  rendered into a local system prompt.
- **Cloud session-start rendering** — `CloudContextSnapshot.system_instruction`,
  built once at adapter construction from the same `NeXaIdentity`
  (R0071/R0077, unchanged) — the ONLY place identity text is rendered for
  Gemini.
- **`CoreSessionContext` (§6, new, design only)** carries `identity:
  NeXaIdentity` as a *reference/projection field for a future consumer that
  doesn't yet exist* (there is no third rendering path today) — it does
  **not** independently render identity text. Until `to_cloud_snapshot()`
  is actually changed to read from it (deferred per §6), `CoreSessionContext`
  produces no output a user could ever see duplicated. This explicitly
  prevents the failure mode the review named: "identity rendered by
  bootstrap + identity rendered by session context."

---

## 8. Gemini Live blocking/non-blocking tool-call behavior — cited, not assumed

Re-audited the installed SDK specifically for this question (new evidence
beyond Revision 1's §5 table):

- `GeminiLiveLLMService._supports_non_blocking_tools` (`llm.py:439-446`):
  ```python
  @property
  def _supports_non_blocking_tools(self) -> bool:
      """... Gemini 3.x has not yet shipped support for NON_BLOCKING
      function declarations or for the `scheduling` field on
      FunctionResponse."""
      return not self._is_gemini_3
  ```
- `_process_completed_function_calls()`'s own docstring/logic
  (`llm.py:978-1005`, already cited in Revision 1): a tool registered with
  `cancel_on_interruption=False` (the async/non-blocking style) produces a
  logged **error** and a `push_error()` on any model that doesn't support
  it: *"cancel_on_interruption=False is not properly supported by the
  current Gemini Live model. Use cancel_on_interruption=True (the
  default)..."* — confirming **`cancel_on_interruption=True` (blocking,
  synchronous-equivalent) is the SDK's own stated default behavior**, and
  is the mode that works across model versions, including ones that don't
  support non-blocking tools at all.

**Conclusion for this design:** registering the Core-recall tool with
`cancel_on_interruption=True` (leave at its default — do not opt into
async/non-blocking mode) is the correct, evidence-grounded choice for the
"Gemini must wait for the result" semantic the review requires (§8 of the
review). This is a **Pipecat-wrapper-level** finding (as flagged
throughout this report) about the specific installed version
(`_is_gemini_3` shows this wrapper already special-cases at least two
model generations) — **implementation must still confirm, against the
exact Gemini Live model NeXa is configured to use and the exact installed
Pipecat version at that time**, that this default still holds and that the
configured model is not one of the (currently Gemini-3.x-only) exceptions.
This report does not change NeXa's configured model to obtain a preferred
tool mode — that decision is explicitly deferred to implementation-time
review, per the instruction.

---

## 9. Tool timeout semantics — Core-owned bound

Design only. `ContextEngine.recall()` must not be allowed to hang the
realtime session indefinitely regardless of what the provider/SDK does on
its own timeout. The adapter (§5) wraps the `recall()` call with a bounded
wait (e.g. `asyncio.wait_for(..., timeout=RECALL_TIMEOUT_SECS)` — exact
figure to be set at implementation time against measured `retrieval_ms`
figures, §17 of Revision 1, still open) and treats a timeout exactly like
`RecallOutcome.UNAVAILABLE` — mapped to the same `"no_match"`-shaped
`{"status": "no_match"}` wire response (per §10's folding rule) or an
explicit `{"status": "unavailable"}`, decided at implementation time
depending on whether "unavailable" needs to be distinguishable from
"no_match" for the model's own retry/rephrase behavior (an open,
implementation-time UX question, not an architectural one). **Never**:
hang, crash the realtime session, or rely on undocumented provider-side
timeout behavior. This is a NeXa-owned execution boundary, symmetric with
how `ContextEngine`'s existing `context_provider` hook in
`ConversationSession.send()` already fails safe on any exception
(`session.py:262-274`) — the same discipline, applied to a timeout instead
of an exception.

---

## 10. Cloud privacy — `CLOUD_WITH_USER_APPROVAL` strengthened

Revision 1 correctly forbade `LOCAL_ONLY` content/existence from crossing.
This revision extends the **same** wire-indistinguishability rule to
`CLOUD_WITH_USER_APPROVAL`, per the review:

```
At the cloud wire boundary (the adapter mapping RecallResult -> Gemini
FunctionResponse, §5):

  RecallResult items whose cloud_eligibility is LOCAL_ONLY
      -> excluded from the wire response entirely
  RecallResult items whose cloud_eligibility is CLOUD_WITH_USER_APPROVAL
      -> excluded from the wire response entirely, UNLESS Core already
         holds an explicit, valid, prior user approval for that specific
         disclosure (no such approval workflow exists yet — until it
         does, this branch is always "excluded")
  Both exclusions produce the SAME wire outcome as a genuine NO_MATCH
  ("status": "no_match") -- the model cannot distinguish "nothing found"
  from "something private was found and withheld."

  RecallResult items whose cloud_eligibility is CLOUD_SAFE
      -> may cross, content included, exactly as today's snapshot path
```

Internally (Core-side, never wire-visible), `RecallResult`/
`KnowledgeGap` MAY still distinguish `PERMISSION_REQUIRED` from `NO_MATCH`
for future local/UI-facing approval handling (a person asking NeXa
directly, locally, "why couldn't you tell the cloud that?" is a legitimate
future local-only surface) — the folding described above applies **only**
at the adapter's cloud-wire-serialization step, never inside `RecallResult`
itself. This mirrors exactly how `ContextItem.cloud_eligibility` already
travels unfiltered inside Core (§3) and is filtered only at the provider
boundary (§9's `to_cloud_snapshot()` precedent).

---

## 11. Tool arguments — minimal, untrusted, validated

**Tool schema (Gemini function declaration), design only:**

```json
{
  "name": "recall_context",
  "description": "Ask NeXa Core to recall a specific fact or piece of context relevant to answering the current question.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "What you need to recall, in plain language."}
    },
    "required": ["query"]
  }
}
```

**Exactly one caller-controllable field: `query`.** Every other concern is
Core-decided, never provider-supplied:

| Concern | Who decides |
|---|---|
| Memory namespace / domain | Core (`_discover`/`_matches`, lexical matching over `derive_subject_hints(query)`) — the model never names a namespace directly |
| Retriever selection | Core (`self._retrievers`, fixed at construction) |
| Record statuses / `include_retracted` | Core — `RecallRequest` has **no such field**, structurally (§12) |
| Cloud eligibility / privacy mode | Core (§10) |
| Retrieval limits (`max_items`, `max_items_per_source`, rounds) | Core — `RecallBudget` is never provider-supplied; the adapter always constructs `RecallRequest(query_text=..., budget=None)`, letting `recall()` apply its own default `RecallBudget()` (§3) — **no arbitrary provider-controlled `ContextBudget`/`RecallBudget`, ever** |
| Raw scope ids | Core — never exposed to the tool schema at all |
| `domain_hint` | Not exposed via the tool schema in V1 either (kept `None` from the adapter, mirroring `derive_context_request()`'s own existing discipline of leaving `domain_hint=None` "unless the caller already has an explicit, trustworthy scope," `derivation.py:89-91`) — reserved as a `RecallRequest` field for a possible future trusted internal caller, not for provider input |

`RecallRequest.__post_init__` (§3) enforces the explicit maximum query
length (`_MAX_RECALL_QUERY_CHARS = 500` — generous for a spoken question,
far below anything that could be used to smuggle a large payload) and
rejects blank input. The adapter treats every field of the incoming
Gemini function-call `arguments` dict as untrusted: only `query` is read;
any other key present is ignored, not merged into anything Core-trusting.

---

## 12. `RETRACTED` unreachability — confirmed structurally preserved

Re-audited `RetrievalQuery` (`retrieval.py:31-38`): fields are
`descriptor_id, domain, record_type_hint, temporal_intent, at, limit` —
**no `statuses`/`include_retracted` field exists today**, and
`MemoryRetriever.retrieve()` (`memory_retriever.py:145-163`) never passes
one to `MemoryService.by_namespace()`/`valid_at()`, relying entirely on
those methods' own default status filtering (M3.2, unchanged). Since
`ContextEngine.recall()` (§2) reuses this exact, unmodified code path —
same `RetrievalQuery` construction inside `_build_retrieval_query()`, same
`MemoryRetriever.retrieve()` — and since `RecallRequest` (§3) likewise has
no `statuses`/`include_retracted` field, **`RETRACTED` unreachability is
preserved automatically by reuse, requiring zero new code or new
validation.** This section exists to make that reasoning explicit and
auditable, not because anything needs to change.

---

## 13. Model tool-use compliance — acceptance test design

Per the review, this is a testable behavior, not just prompt wording.
**Design of the acceptance corpus** (execution deferred to the
implementation report, since it requires live model access this
design-only audit does not have):

| Query class | Example | Expected tool behavior |
|---|---|---|
| Clearly personal remembered fact | "What's my current MSc deadline?" | SHOULD call `recall_context` |
| NeXa project decision | "What did we decide about the context engine's budget defaults?" | SHOULD call `recall_context` |
| Generic world knowledge | "What's the capital of France?" | should NOT call `recall_context` |
| `NO_MATCH` query | A fact never stored | calls the tool, receives `no_match`, must NOT fabricate an answer |
| Ambiguous query | "What did I say about that?" (no clear referent) | either behavior may be acceptable; log and review, not a hard pass/fail |

**Metrics to record** (implementation-time): recall-requested-when-needed
rate, unnecessary-recall rate, fabricated-answer-without-recall rate (most
critical — a hard failure if observed on the `NO_MATCH` or personal-fact
rows). **Explicitly rejected as a "fix":** forcing the tool call on every
turn to inflate the pass rate — the review is correct that this would
defeat the point of model-initiated recall (§6 Pattern B's whole latency
advantage is that it's *not* paid on every turn). System-instruction
wording (the literal string configured on `GeminiLiveLLMService(system_
instruction=...)`) is deferred to implementation, where it can be iterated
against this corpus.

---

## 14. Local voice design — approved, unchanged

Confirmed unchanged from Revision 1 §14/§23(C). Reconfirmed against the
real code in this revision's own re-audit (§2 above, which additionally
confirms `_discover`/`_retrieve` never depended on session in the first
place — reinforcing, not weakening, the §14 design): `context_addendum`
threaded through `ProviderWindow.render()`, `_render_provider_window()`
gaining the same fail-safe `context_provider` handling `send()`'s
non-window branch already has, `VoiceConversationAdapter` gaining an
optional `context_provider` constructor parameter defaulting to `None`.
No changes required by this revision. Local voice continues to use
`ContextEngine.build_context()` (the turn-based entry mode, §2) — never
`recall()`, which exists specifically for the case where no current turn
is available (cloud only, today).

---

## 15. PL/EN derivation design — approved, unchanged

Confirmed unchanged from Revision 1 §16. `derive_subject_hints()` gains a
generic Polish stopword set alongside the existing English one, applied
unconditionally (no language detection). §4 above additionally confirms
this function is the exact, single call site both `build_context()`'s
turn-based path and `recall()`'s query-text-based path will share —
strengthening, not changing, the design: the PL/EN improvement benefits
both entry modes for free, with no duplicated tokenization logic.

---

## 16. Corrected parity matrix

Supersedes Revision 1 §15's rows for Memory/Knowledge Awareness/dynamic
facts, reflecting that once §2 (`recall()`) and §14 (local-voice wiring)
are actually implemented, local voice uses the **same per-turn
`build_context()`** typed chat already uses — not a lesser mechanism.
Cloud stays `FOUNDATION`/design-only until Pattern B is actually built and
tested (§13's corpus not yet run).

| Capability | Typed local | Local voice (post-§14 implementation) | Cloud realtime (post-Pattern B implementation) |
|---|---|---|---|
| Identity | LIVE | LIVE (unchanged) | LIVE (session-start, unchanged) |
| Conversation history | LIVE | LIVE (`ProviderWindow`) | LIVE (post-hoc canonical write, unchanged) |
| Memory — turn-dynamic recall | LIVE (R0077) | **LIVE** (same `build_context()` call, §14) | FOUNDATION/DESIGN (`recall()`, Pattern B — designed §2, not implemented) |
| Knowledge Awareness used internally by Core | LIVE | **LIVE** (Core-internal only; never projected to any provider, unchanged invariant) | FOUNDATION/DESIGN — same invariant applies to `recall()`'s internal `_discover()` step once implemented |
| Dynamic newly-written Memory fact | LIVE on next turn, any means of writing (M3.4 write path not yet built) | **LIVE on next local-voice turn** (same retrieval path as typed, §14) | FOUNDATION/DESIGN — reasoned viable (§12 of Revision 1, unchanged reasoning), not implemented |
| `LOCAL_ONLY` exclusion | LIVE (M3.3 invariant) | NOT APPLICABLE (no cloud provider) | FOUNDATION/DESIGN (§10, strengthened this revision) |
| `CLOUD_WITH_USER_APPROVAL` exclusion | NOT APPLICABLE (no cloud provider in this path) | NOT APPLICABLE | FOUNDATION/DESIGN (§10, **new this revision** — Revision 1 under-specified this) |
| `CLOUD_SAFE` use | NOT APPLICABLE | NOT APPLICABLE | LIVE (snapshot path); FOUNDATION/DESIGN (`recall()` path) |
| Personality/Relationship/Goals/Teacher/LiFeOS (future) | NOT APPLICABLE (M3.4 not started) | NOT APPLICABLE | NOT APPLICABLE |
| Session-stable Core seam provider-neutrality | NOT APPLICABLE (no separate seam needed — `bootstrap.py` composes directly) | NOT APPLICABLE | FOUNDATION/DESIGN (`CoreSessionContext`/`build_session_context`, §6 — shape only, not wired) |

"LIVE" for Knowledge Awareness means **Core actively uses it internally**
(descriptor discovery drives what gets retrieved) — it is never sent to
any provider, in any row, at any point in this design. This distinction is
restated per the review's explicit instruction.

---

## 17. Canonical architecture diagram

```
                              NEXA CORE
                                 |
              +-------------------+--------------------+
              |                                         |
       SESSION-STABLE                              TURN-DYNAMIC
          CONTEXT                                     RECALL
              |                                         |
   CoreSessionContext (§6, design)          ContextEngine.recall(RecallRequest)
   build_session_context()                             |
              |                              same _discover/_retrieve/_trim_to_budget
              |                              as build_context() -- ONE retrieval engine
              |                                         |
              |                              Knowledge Awareness (Core-internal only)
              |                              MemoryRetriever (the one shared instance)
              |                              future Teacher/LiFeOS retrievers, same Protocol
              |                                         |
              +-------------------+--------------------+
                                  |
                          provider adapters (nexa.realtime.*, outside Core)
                         /                              \
                 local provider                    realtime cloud adapter
           (typed: build_context() directly    (§5: Gemini function-call args
            local voice: build_context()        -> RecallRequest -> recall()
            via context_provider, §14)           -> RecallResult -> FunctionResponse,
                                                   privacy-folded per §10)

Typed / local voice normal turn context:
    ConversationSession -> ContextEngine.build_context() -> CurrentTurnContext
    (current_turn precondition UNCHANGED, current-turn/window/identity intact)

Cloud dynamic recall:
    Gemini tool query -> RecallRequest -> ContextEngine.recall() -> RecallResult
    (no ConversationSession precondition -- self-contained, per §1's fix)

SAME ContextEngine instance. SAME MemoryService. SAME canonical Memory
state. Different entry mode ONLY because realtime cloud turn timing is
fundamentally different (§1, §4 of Revision 1's audit) -- never because
Core knows two different things.
```

---

## 18. Updated acceptance plan (21 items, for the eventual implementation report)

Supersedes Revision 1 §24's list. Every still-valid Revision 1 test is
retained (interruption/barge-in/PL-EN/latency-measurement items); new
items reflect this revision's fixes.

1. `ContextEngine.recall()` succeeds with no `ConversationSession` in
   scope at all (constructed from a bare `RecallRequest`) — the direct
   regression test for §1's fix.
2. `ContextEngine.build_context()` retains its existing current-turn
   precondition unchanged (`ContextBuildError` still raised on empty/
   non-USER-terminated history) — proves §2's refactor didn't weaken it.
3. Both `build_context()` and `recall()` are proven, by object-identity
   assertion, to call into the exact same `self._retrievers` tuple / same
   `MemoryRetriever` instance (extends the existing
   `test_context_engine_has_exactly_one_memory_retriever` test, §2).
4. No second Context/Memory authority exists anywhere in the diff (a
   source-scan regression test, mirroring R0077's existing
   `"ContextEngine" not in source` style checks) — no
   `CloudMemoryEngine`/`GeminiMemoryService`/`RecallDatabase` symbol
   anywhere in the codebase.
5. `RecallRequest` contains no provider-specific (Gemini) types anywhere
   in its field types (a static/structural check, mirroring how M3.3
   already keeps `ContextItem` free of `payload`/`provenance`).
6. The tool boundary rejects an oversized `query` (>`_MAX_RECALL_QUERY_CHARS`)
   with a clean `ValueError` at `RecallRequest` construction, never a raw
   exception reaching the provider.
7. The tool schema (§11) is proven to expose no field capable of
   requesting `statuses`/`RETRACTED`/privacy overrides — a schema-shape
   assertion, not just a runtime behavior test.
8. A `RecallResult` containing only `LOCAL_ONLY` items is proven
   wire-indistinguishable from a genuine `NO_MATCH` after adapter mapping
   (§10).
9. A `RecallResult` containing only `CLOUD_WITH_USER_APPROVAL` items is
   proven wire-indistinguishable from `NO_MATCH` the same way (§10, new
   this revision).
10. A `recall()` call that exceeds the Core-owned timeout bound (§9)
    returns a well-formed `UNAVAILABLE`-shaped wire response, never hangs
    the test, never propagates a raw timeout exception to the provider
    layer.
11. The exact configured Gemini model + installed Pipecat version's tool
    mode is explicitly re-verified as blocking/synchronous-equivalent at
    implementation time (§8) — a documented manual/scripted check, not
    assumed from this report's SDK reading alone.
12. An integration/live test proves the model waits for the `recall_context`
    result before producing a knowledge-dependent answer (not just that the
    tool round-trip completes eventually).
13. The §13 tool-use compliance corpus passes at a documented, explicitly
    stated acceptable rate (not 100% forced compliance, and not silently
    dropped as "best-effort").
14. Generic world-knowledge queries from the §13 corpus do NOT trigger
    unnecessary `recall_context` calls, measured, not assumed.
15. `CoreSessionContext`/`build_session_context()` (§6) are proven
    provider-neutral — no import of anything under `nexa.realtime`
    anywhere in `nexa.core.context.session_context` (naming TBD at
    implementation time).
16. `CloudContextSnapshot` construction is proven to remain a pure
    projection (no independent state/lifecycle of its own beyond one
    construction call) — a structural/object-identity test, not just a
    docstring claim.
17. Identity is proven not duplicated: local typed, local voice, and cloud
    session-start each render identity text from exactly one call path
    (regression extension of existing R0077-era tests), and
    `CoreSessionContext.identity` (§6/§7) produces no separate rendered
    output anywhere until `to_cloud_snapshot()` is actually changed to
    consume it (which this report does not do).
18. Local voice uses `build_context()` per turn (not `recall()`) — an
    explicit assertion distinguishing the two entry modes at the call site
    (§14).
19. `ProviderWindow` prefix-stability behavior (base/rollover counters) is
    unchanged with `context_addendum` wired in — regression against
    R0029/M2.5B.2's existing KV-cache-stability tests.
20. PL-only, EN-only, and mixed PL/EN hint derivation all produce correct,
    non-empty hints (§15, unchanged from Revision 1's design, now via the
    single shared `derive_subject_hints()` call site both entry modes use).
21. All existing R0071/R0077/M3.3 regression suites remain passing
    unmodified (frozen audio pipeline, typed-path context wiring, Memory
    foundation) — the final, unconditional non-regression gate.

---

## 19. External verification note (unchanged emphasis, restated)

This report's SDK evidence (§5 of Revision 1, §8 of this revision) is
grounded entirely in the **locally installed Pipecat wrapper source**
(`.venv/lib/python3.13/site-packages/pipecat/services/google/gemini_live/llm.py`)
— no hosted Google documentation was reachable from this environment.
This is first-party evidence of what *this installed version* exposes,
not a claim about the full Gemini Live API surface or about every model
version. The wrapper's own code already distinguishes model-generation
behavior internally (`_is_gemini_3`, `_supports_non_blocking_tools`),
which is itself proof that this is a moving target across model versions —
**implementation must re-verify the exact configured model's exact
function-calling/blocking behavior against official, current documentation
and/or a live smoke test before changing any production wiring**, per the
explicit instruction. Findings here must not be generalized to "all Gemini
Live versions behave this way."

---

## 20. Implementation-readiness statement

After this audit-grounded correction, the design is **conceptually
implementation-ready for the cloud Pattern B recall path and the local
voice `context_addendum` path**, with three explicit, named prerequisites
that implementation itself must resolve (not further design work — these
are verification/measurement tasks, not open architectural questions):

1. **§8/§19** — confirm the exact configured Gemini Live model's
   blocking-tool-call behavior against current official documentation
   and/or a live smoke test, before wiring `recall_context` as a
   construction-time tool.
2. **§17 of Revision 1** — measure the actual function-call round-trip
   latency on real hardware/live API; this report could not measure it.
3. **§13** — the tool-use compliance corpus must actually be run against
   the live model before Pattern B is trusted in production; system-
   instruction wording needs iteration against real results, not
   finalized here.

The session-stable seam (§6) is **not** implementation-ready to be wired
end-to-end yet, and is not recommended to be — it is a shape to build
*into*, to be actually connected only when M3.4 (Personality/Relationship)
gives it real content to carry, per §6's own explicit deferral. Building
it fully now would touch `to_cloud_snapshot()`'s already-hardware-accepted
call sites for no current behavioral benefit — unjustified risk to a
frozen, accepted path, consistent with §3's do-not-reopen-audio principle
applied to R0071's cloud-snapshot construction as well as its audio
pipeline.

**No implementation performed. No commit. No push. Stopping for review.**
