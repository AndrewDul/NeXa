# R0073 — M3.2 Memory Foundation: Design (Revision 3)

**Date:** 2026-09-16 (revised same day, second review pass)
**Milestone:** M3.2 — NeXa Core, Memory Foundation (design phase)
**Status:** DESIGN ONLY, REVISED. No implementation. Not committed, not pushed.
Stops after this document — implementation begins only after review.

## TASK RESULT

**DESIGN, revised a second time for review — not a build.**

## REVISION SUMMARY (what changed since Revision 2)

Revision 2 built a real domain-extensible platform shape (namespace/
category/record_type, generic scope, relations, temporal columns) but
review found nine architectural corrections needed before implementation,
each aimed at preventing an expensive migration or a wrong dependency
boundary later:

1. **Dependency direction fixed** — `CloudEligibility` moves from
   `nexa.realtime.privacy` to `nexa.core.privacy`; NeXa Core no longer
   depends on the realtime/provider layer (§1).
2. **Temporal truth decoupled from lifecycle currentness** — `valid_at(t)`
   now returns historically-true records without a special flag; only
   ordinary "current knowledge" queries default to ACTIVE-only (§2).
3. **Evidence separated from the canonical record** — a new
   `memory_evidence` table preserves every independent source that
   supports a memory, instead of `remember()` silently discarding
   evidence on a dedup hit (§3).
4. **Confidence now lives at two levels on purpose** — record-level is a
   cache, evidence-level is the source of truth; no aggregation algorithm
   built (§4).
5. **`payload_json` gets a domain-owned `payload_version`** — a future
   Teacher schema change never touches the central DB schema (§5).
6. **`namespace`/`record_type` naming convention fixed** — `record_type`
   is short and unqualified; `namespace` alone supplies ownership (§6).
7. **Backup semantics corrected for WAL mode** — naive file copy is no
   longer described as sufficient while NeXa is running (§7).
8. **Component bootstrap distinguishes first-run from corruption** — a
   missing version row with existing tables now fails loudly instead of
   silently "adopting" unknown state (§8).
9. **Hard delete's FK behavior is now explicit and atomic** — declared via
   `ON DELETE CASCADE`/`SET NULL`, no dangling references, no accidental
   cascade to unrelated records (§9).

Plus three terminology/scope corrections: soft deactivation is renamed
`RETRACTED` and never called "forget" (§10); `MemoryService` drops
`query_cloud_safe()` to stay provider-neutral (§11); `CloudEligibility`
(privacy/export) is explicitly distinguished from a future, unbuilt data-
*sensitivity* axis (§12). §13 restates the relation/entity-model limit
explicitly. §14 gives each `MemoryCategory` a precise, cross-domain-stable
operational test instead of a soft description.

Everything approved in Revision 2 that isn't listed above is unchanged:
one `MemoryService` authority, local-only canonical storage, no vector DB,
deterministic-first bounded/paginated retrieval, no every-sentence
auto-memory, the Memory/Context-Engine separation, the closed-graph
relation model, the namespace/scope extensibility mechanism itself, XDG
storage location, and component-aware schema versioning as a *mechanism*
(refined in §8).

---

## §1 — Dependency direction: Core must not depend on Realtime

**Problem, confirmed:** Revision 2 imported
`nexa.realtime.privacy.CloudEligibility` into `nexa.core.memory`, creating
`nexa.core -> nexa.realtime` — backwards. `CloudEligibility` is a NeXa
Core privacy/export-policy concept (which local, Core-owned facts may
leave the device), not a realtime/audio concept; it happened to be
implemented under `nexa.realtime` in M3.1 only because that's where the
cloud-voice boundary work (ADR-0004) was happening at the time.

**Fix:** the canonical type moves to `nexa.core.privacy`:

```python
# src/nexa/core/privacy.py (new canonical location)
class CloudEligibility(StrEnum):
    LOCAL_ONLY = "local_only"
    CLOUD_SAFE = "cloud_safe"
    CLOUD_WITH_USER_APPROVAL = "cloud_with_user_approval"

def filter_cloud_safe(facts: Iterable[tuple[str, CloudEligibility]]) -> tuple[str, ...]: ...
```

Identical values, identical behavior — **not a second, duplicate enum**;
a *relocation*.

**Compatibility migration (planned, not executed in this design pass):**
`nexa.realtime.privacy` becomes a thin re-export:

```python
# src/nexa/realtime/privacy.py (after migration)
"""Deprecated location -- CloudEligibility moved to nexa.core.privacy
(NeXa Core boundary correction). Re-exported here so existing imports
don't break abruptly; new code should import nexa.core.privacy directly."""
from nexa.core.privacy import CloudEligibility, filter_cloud_safe

__all__ = ["CloudEligibility", "filter_cloud_safe"]
```

`nexa.realtime.snapshot` (`CloudContextSnapshot`, `build_cloud_context_snapshot`)
is updated to import from `nexa.core.privacy` directly. No call site
outside `nexa.realtime` needs to change at all — every existing
`from nexa.realtime.privacy import CloudEligibility` import keeps working
unchanged, it just now resolves through a re-export instead of a
definition.

**This is a boundary correction to already-shipped M3.1 code, not new
M3.2 functionality.** It should be executed as its own small, reviewed
step (with an ADR amendment recording the corrected dependency direction
— either ADR-0004 gets a further amendment, or a short new ADR, decided at
implementation time) *before* any M3.2 memory code is written, not bundled
into the same commit as the memory implementation.

**Corrected dependency diagram:**

```
nexa.core.memory  ───────┐
nexa.core.context (M3.3) ├──depends on──> nexa.core.privacy  (CloudEligibility, filter_cloud_safe)
nexa.core.identity  ─────┘

nexa.realtime.snapshot  ──depends on──> nexa.core.privacy   (imports the canonical type directly)
nexa.realtime.privacy   ──depends on──> nexa.core.privacy   (re-export only, compatibility)
nexa.realtime.gemini.*  ──depends on──> nexa.realtime.snapshot   (never nexa.core.memory directly)

nexa.core.*  ──depends on── NOTHING under nexa.realtime, ever.
```

The rule, stated once and enforced by acceptance tests (§ Acceptance 22,
23): the canonical authority for a Core concept always moves **inward**,
toward `nexa.core`, never outward toward a provider-facing package —
exactly the ADR-0005 ownership rule, now applied to privacy classification
the same way it was already applied to identity.

## §2 — Temporal truth must not be hidden by lifecycle status

**Problem, confirmed:** Revision 2's acceptance wording implied
`valid_at(t)` needed `include_superseded=True` to see a historically-true
`SUPERSEDED` record — semantically wrong. `SUPERSEDED` means "not the
current version," not "historically invisible."

**Fix — two genuinely different query shapes, not one flag:**

- **"What does NeXa currently know/use?"** (`by_namespace`, `by_scope`,
  `by_record_type`, `recent`) — defaults to `status=ACTIVE` only. This is
  a *currentness* query. An explicit `statuses: set[MemoryStatus] | None`
  param can widen it for audit purposes.
- **"What was true at time `t`?"** (`valid_at`) — a *historical-truth*
  query. It has **no status filter by default at all.** A row physically
  present in `memory_records` — whatever its lifecycle status
  (`ACTIVE`/`SUPERSEDED`/`RETRACTED`, §10) — is included whenever its
  `[valid_from, valid_until)` interval contains `t`. Only a row that has
  been `hard_delete`d is excluded, and that's automatic: it no longer
  exists in the table to be queried at all, no filter needed. An optional
  `statuses` param can *narrow* this later (e.g. "exclude anything the
  user retracted"), but the **default must return the complete historical
  truth with no special flag required** — matching the exact requirement:
  "Where did the user work in June 2026?" against
  `[FACT "Company A", valid_until=2027-01-01, status=SUPERSEDED]` and
  `[FACT "Company B", valid_from=2027-01-01, status=ACTIVE]` must return
  Company A, by default, with no extra parameter.

```python
def valid_at(self, at: datetime, *, namespace: str | None = None,
             record_type: str | None = None,
             statuses: set[MemoryStatus] | None = None,  # None = all statuses (default)
             limit: int, cursor: str | None = None) -> Page[MemoryRecord]: ...
```

## §3 — `MemoryRecord` (what NeXa knows) vs. `MemoryEvidence` (why/how it knows)

**Problem, confirmed:** Revision 2's `remember()` dedup returned the
existing record's `id` on an exact-content match and did nothing else —
silently discarding a second, independent source for the same durable
fact (the exact scenario from review: repeated explicit statement, then a
LiFeOS routine observation, for the same preference).

**Fix — split canonical knowledge from supporting evidence:**

```sql
CREATE TABLE memory_evidence (
    id            TEXT PRIMARY KEY,
    memory_id     TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    provenance    TEXT NOT NULL,     -- MemoryProvenance (§11, Revision 2, unchanged values)
    source_kind   TEXT,              -- open vocabulary (unchanged from Revision 2 §11)
    source_ref    TEXT,              -- opaque locator, not guaranteed durable (unchanged, §11.1)
    confidence    REAL,              -- per-EVIDENCE confidence (§4)
    observed_at   TEXT,              -- ISO-8601 UTC; when the evidence event itself happened
                                        --   (nullable -- defaults to created_at when unknown)
    created_at    TEXT NOT NULL,      -- ISO-8601 UTC; when NeXa stored this evidence row
    payload_json  TEXT,                -- evidence-specific metadata only (optional)
    CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0))
);
CREATE INDEX idx_evidence_memory_id ON memory_evidence(memory_id, observed_at);
```

`provenance`, `source_kind`, and `source_ref` move **entirely** off
`memory_records` — with more than one evidence source possible, a
record-level `provenance` column would be ambiguous ("which source's
provenance?"). They now live only on `memory_evidence`, which is
correctly one-to-many.

**Revised `remember()` behavior:**

1. Validate `namespace`/`record_type` (§6) and `payload`/`payload_version`
   (§5).
2. Look up an existing `ACTIVE` record with an exact-match `(namespace,
   record_type, scope_type, scope_id, content)`.
3. **If found:** insert a **new** `memory_evidence` row referencing it
   (this call's `provenance`/`source_kind`/`source_ref`/`confidence`/
   `observed_at`) — the existing evidence is never touched or replaced.
   Return `RememberResult(memory_id=existing.id, evidence_id=new.id,
   was_new_memory=False)`.
4. **If not found:** insert a new `memory_records` row **and** its first
   `memory_evidence` row in the same transaction. Return
   `RememberResult(memory_id=new.id, evidence_id=first_evidence.id,
   was_new_memory=True)`.

Evidence rows are never deduplicated against each other — a second,
independent confirmation of the same fact (the user repeating themselves,
or a system later observing the same pattern) is itself meaningful
information and is always appended, never merged away. Only the canonical
`MemoryRecord` is deduplicated (§P.1, unchanged principle from Revision 2,
now scoped precisely to "canonical record," not "evidence").

**Stress-tested (examples, not implemented) against exactly the sources
named in review:**

```
remember(EXPLICIT_REMEMBER_REQUEST, ..., content="Prefers morning workouts",
         provenance=EXPLICIT_USER_STATEMENT, source_kind="conversation_turn")
  -> was_new_memory=True,  memory_id=M1, evidence=[E1]

remember(EXPLICIT_REMEMBER_REQUEST, ..., content="Prefers morning workouts",
         provenance=EXPLICIT_USER_STATEMENT, source_kind="conversation_turn")
  -> was_new_memory=False, memory_id=M1, evidence=[E1, E2]   -- repeated statement kept

remember(DURABLE_CANDIDATE, ..., content="Prefers morning workouts",
         provenance=SYSTEM_OBSERVATION, source_kind="lifeos_routine_pattern",
         confidence=0.81)
  -> was_new_memory=False, memory_id=M1, evidence=[E1, E2, E3]  -- LiFeOS
     observation adds a THIRD, independent, differently-provenanced source
     without disturbing E1/E2

remember(..., content="Recursion attempt #8, mastery 0.55",
         provenance=SYSTEM_OBSERVATION, source_kind="teacher_exercise_attempt")
  -> Teacher exercise attempts naturally land as their own EVENT/STATE
     records (§14) or as evidence on a STATE record, per the domain's
     own choice -- both shapes are supported, not prescribed here.

remember(..., content="Calendar shows recurring gym booking",
         provenance=IMPORTED, source_kind="calendar_event", source_ref="cal:evt:88f2")
  -> was_new_memory=False, memory_id=M1, evidence=[..., E4]  -- imported
     calendar evidence for the SAME preference, fourth independent source

remember(..., content="Inferred preference for morning activity from usage pattern",
         provenance=LEARNED_INFERENCE, confidence=0.62)
  -> a FUTURE M3.6 learned inference is just another evidence source,
     same mechanism, no special case
```

## §4 — Where confidence lives

- **`memory_evidence.confidence`** — the source of truth. Each piece of
  evidence carries its own confidence, meaningful only in the context of
  that source (an explicit user statement is authoritative — typically
  left `NULL`, meaning "not applicable, treated as certain" — while a
  model inference or a system observation carries a real 0.0-1.0 value).
- **`memory_records.confidence`** — a **cached, current "working"
  confidence**, documented explicitly as a cache, not a computed
  aggregate. Set once at record creation from the first evidence's
  confidence (or left `NULL`). **M3.2 builds no aggregation algorithm** —
  nothing automatically recomputes this cache as more evidence arrives.
  A future component *with* an aggregation policy (e.g. "average the last
  N evidence confidences," "authoritative evidence always wins") may call
  `MemoryService.update_metadata(id, confidence=...)` to push a
  deliberately computed value — that policy is explicitly out of scope
  here, only the seam for it to exist without breaking the data model.

## §5 — `payload_version` and structured-data validation

`payload_json` needed a version boundary so a domain (Teacher, LiFeOS)
can change its own payload shape for a `record_type` years later without
touching the central `memory_records` SQL schema:

```sql
payload_version  INTEGER NOT NULL DEFAULT 1
```

**Semantics:** `payload_version` is the version of `payload_json`'s shape
*for this specific `record_type`*, defined and incremented by the owning
domain module alone — NeXa Memory never interprets `payload_json`'s
contents, so this column exists purely so a future domain reader can
branch on `payload_version` and apply the correct parsing logic for that
version. Example: `namespace="teacher.python"`, `record_type=
"skill_mastery"`, `payload_version=1`, `payload_json={"skill":...,
"mastery":...,"attempt_count":...}` — a future `payload_version=2` could
add a `"streak_days"` field, and old `version=1` rows remain valid,
readable by version-aware domain code, with **no `ALTER TABLE`, no
central migration.** No domain migration *framework* is built — this is
one integer column and a documented convention, nothing more.

**Validation and serialization rules (enforced by `MemoryRepository`,
Python-level, before every write):**

- `payload` must be `None` or a plain, JSON-serializable Python `dict`
  (never a Python object, never pickled — `json.dumps(payload)` is the
  only accepted serialization path; a `TypeError` from `json.dumps`
  becomes a clear `MemoryValidationError`, not a silent failure).
- Top-level shape must be a JSON **object** (`{}`), not an array or
  scalar, unless a future domain documents and justifies otherwise (not
  needed for any example in this design) — enforced at the DB level too:
  `CHECK (payload_json IS NULL OR (json_valid(payload_json) AND
  json_type(payload_json) = 'object'))` using SQLite's built-in JSON1
  functions (no new dependency).
- **Canonical serialization for deterministic tests:**
  `json.dumps(payload, sort_keys=True, separators=(",", ":"))` — stable
  byte-for-byte output for equal dict content, so payload-roundtrip tests
  (§ Acceptance 13) are exact-string-comparable, not "close enough."

## §6 — `namespace` / `record_type` convention

**Fixed direction (per review): `namespace` alone supplies ownership;
`record_type` is short and never re-prefixed with its own namespace.**

```
namespace="teacher.python",  record_type="skill_mastery"     -- not "teacher.skill_mastery"
namespace="lifeos.health",   record_type="health_observation" -- not "lifeos.health_observation"
namespace="lifeos.routines", record_type="routine"             -- not "lifeos.routine"
```

**Validation (enforced at write time, Python-level, not a DB `CHECK`,
consistent with how `NeXaIdentity`'s loader validates in Python rather
than via SQL constraints):**

```
namespace:    ^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$    (dot-hierarchical, lowercase)
record_type:  ^[a-z][a-z0-9_]*$                          (single lowercase snake_case token)
```

A `record_type` that starts with, or repeats, its own `namespace` is
rejected by the validator with a clear message pointing at this rule — so
domain wrapper modules cannot gradually drift into inconsistent naming.
The `(namespace, record_type)` **pair** is what scopes ownership; string
prefixing is redundant and explicitly disallowed.

## §7 — Backup semantics under WAL

**Problem, confirmed:** Revision 2 said "one file is the whole database,
copy it" while also enabling WAL mode — with WAL active, committed data
can still live only in the `-wal` sidecar file, so a naive copy of just
`core.sqlite3` is not guaranteed consistent.

**Corrected rule, two cases:**

- **NeXa running (WAL active):** use `sqlite3.Connection.backup(target)`
  (Python stdlib's online backup API) or `VACUUM INTO 'backup_path.sqlite3'`
  (a single SQL statement, safe against a live WAL connection, and
  additionally compacts). `VACUUM INTO` is the documented primary method —
  `nexa.core.storage.sqlite.backup_to(conn, path)` is the one function
  that wraps it (interface only, not implemented in this design pass).
  **A plain `cp core.sqlite3 backup.sqlite3` while NeXa is running is
  never documented or recommended** — it may silently miss committed
  transactions still sitting in `core.sqlite3-wal`.
- **NeXa fully stopped / cleanly checkpointed:** a plain file copy is
  valid **only after** `PRAGMA wal_checkpoint(TRUNCATE)` has run (or the
  connection was closed cleanly, which SQLite does automatically on a
  normal shutdown, removing the `-wal`/`-shm` sidecar files). Documented
  explicitly: if `-wal`/`-shm` files are present next to `core.sqlite3`,
  a raw copy of `core.sqlite3` alone is **not** valid — either checkpoint
  first, or copy all three files together and let SQLite recover from
  them on open (the safer of the two, but still inferior to the online
  backup API for a "take a backup right now" operation).

## §8 — Component bootstrap: first run vs. corruption

**Problem, confirmed:** "missing component row → treat as first run,
create tables" is unsafe if the component's tables already exist (partial
migration, corruption, an interrupted prior bootstrap, or a foreign
unrelated table that happens to share a name).

**Corrected decision table**, applied per component (`core_storage`,
`memory`, and any future component) independently, at connect time:

| `schema_components` row present? | Component's own tables exist? | Outcome |
|---|---|---|
| No | No | **Genuine first bootstrap** — create tables, insert version row. |
| No | **Yes** | **`SchemaStateError`** — ambiguous state (partial migration / corruption / interrupted bootstrap). Never silently adopt unknown tables. |
| Yes, version matches | Yes, structurally intact | Proceed normally. |
| Yes, version matches | **No, or malformed** (missing table/column) | **`SchemaStateError`** — corruption. Never auto-recreate. |
| Yes, version does **not** match expected | — | **`SchemaVersionError`** — unknown/newer/older version. Never auto-migrate. |

"Structurally intact" is checked with a lightweight `PRAGMA table_info`
comparison against the expected column set for that component's tables —
a sanity assertion, not a full migration-diff engine. Both error types are
distinct, typed exceptions so a caller (and a test) can tell "wrong
version" apart from "tables missing/malformed despite a version row."

## §9 — Hard delete: explicit, atomic FK behavior

**Problem, confirmed:** `hard_delete()` needed exact semantics for
`memory_evidence`, `memory_relations`, and `memory_records.supersedes_id`,
which all reference `memory_records.id`.

**Fix — declare the behavior in the schema itself, so the operation is a
single atomic statement, not hand-written multi-step deletion code:**

```sql
memory_evidence.memory_id            REFERENCES memory_records(id) ON DELETE CASCADE
memory_relations.source_id           REFERENCES memory_records(id) ON DELETE CASCADE
memory_relations.target_id           REFERENCES memory_records(id) ON DELETE CASCADE
memory_records.supersedes_id         REFERENCES memory_records(id) ON DELETE SET NULL
```

With `PRAGMA foreign_keys = ON` (already planned, §K), `hard_delete(id,
reason)` becomes:

```python
def hard_delete(self, id: str, reason: str) -> None:
    logger.info("memory hard_delete id=%s reason=%s", id, reason)  # logged BEFORE commit,
                                                                       # never persisted as a MemoryRecord
    with self._connection() as conn:
        conn.execute("DELETE FROM memory_records WHERE id = ?", (id,))  # one statement, one transaction
```

**Exact, by-construction behavior:**
- All `memory_evidence` rows for `id` are removed (`CASCADE`) — evidence
  belongs entirely to its record; nothing meaningful survives it.
- All `memory_relations` rows where `id` is `source_id` or `target_id` are
  removed (`CASCADE`) — a relation referencing a now-gone record is
  meaningless. **The relation's OTHER endpoint record is never touched or
  deleted** — only the edge row disappears.
- Any record whose `supersedes_id = id` (i.e. a later record that formally
  superseded this one) has that column set to `NULL` (`SET NULL`) — it no
  longer claims to supersede anything; this is a deliberate, visible break
  in the audit chain at exactly the point of an explicit erasure request,
  not a silent gap.
- If `id` itself had a `supersedes_id` pointing at an earlier record, that
  earlier record is **completely unaffected** — deleting a successor never
  touches its predecessor.
- **No cascade to any unrelated `MemoryRecord`** — by construction, since
  every `ON DELETE` clause targets only rows that directly reference `id`.
- Atomic: SQLite enforces all three FK actions within the single `DELETE`
  statement's transaction — no partial state is observable.

## §10 — Lifecycle terminology: retract vs. erase

**Problem, confirmed:** calling soft deactivation "forget" is dangerous
for user-owned memory — a user who says "forget this" reasonably expects
durable removal, not a hidden row.

**Fix — two clearly separate operations, renamed:**

```
retract(id, reason: str | None = None)
    -> status: ACTIVE/SUPERSEDED -> RETRACTED (was: DELETED)
    -> row PHYSICALLY REMAINS; excluded from default "current knowledge"
       queries (§2); STILL included in valid_at() historical queries by
       default (§2) -- it was still real, historical information, even if
       no longer treated as current/active
    -> meaning: "do not use this as active knowledge going forward";
       audit/history preserved

hard_delete(id, reason: str)  -- reason REQUIRED, not optional
    -> row PHYSICALLY REMOVED, FKs handled per §9
    -> meaning: an explicit, durable removal request (privacy/erasure)
```

`MemoryStatus` is renamed accordingly: `ACTIVE | SUPERSEDED | RETRACTED`
(no `DELETED` value — a status only describes a row that still exists;
"gone" isn't a status, it's the absence of a row).

**Product-intent mapping (documented, not built — M3.2 has no NL intent
handling):** a future "NeXa, forget that I said X" must eventually resolve
to `hard_delete()`, not `retract()`. `retract()` is the right primitive
for "stop using this, but I don't need it destroyed" (e.g. a stale
preference). M3.2 provides both correctly-named, correctly-behaved service
methods; deciding *which one a natural-language request means* is a future
Context Engine / intent-handling concern, explicitly out of scope here.

## §11 — `MemoryService` stays provider-neutral

**Fix, per review preference:** `MemoryService.query_cloud_safe()` is
**removed**. Memory should know privacy/export-eligibility *metadata* and
how to filter/query by it — it should not know Gemini, provider prompt
construction, or `CloudContextSnapshot`.

Instead, the existing bounded retrieval methods (§7, Revision 2, carried
forward) gain a generic, provider-agnostic filter parameter available on
every method:

```python
def by_namespace(self, namespace: str, *, record_type: str | None = None,
                  category: MemoryCategory | None = None,
                  cloud_eligibility: CloudEligibility | None = None,   # NEW, generic filter
                  limit: int, cursor: str | None = None,
                  statuses: set[MemoryStatus] | None = None) -> Page[MemoryRecord]: ...
# same cloud_eligibility filter added to by_scope, recent, valid_at
```

This is just another `WHERE` clause, structurally identical to filtering
by `namespace` or `category` — Memory never imports `CloudContextSnapshot`
or anything from `nexa.realtime`.

**Corrected layering (supersedes Revision 2's `query_cloud_safe()`):**

```
Memory (generic bounded query, cloud_eligibility as one ordinary filter)
        |
        v
M3.3 Context Engine   -- relevance selection (which memories matter for
                          this turn) + calls the generic eligibility filter
        |
        v
Provider-specific projection adapter (e.g. builds CloudContextSnapshot.
                                        context_facts -- nexa.realtime, unchanged)
        |
        v
Realtime / cloud provider
```

## §12 — Privacy classification vs. data sensitivity (documented distinction)

**Not building a policy engine now — documenting the axis so it is never
mistaken for the whole privacy model.** `CloudEligibility` (§1) answers
exactly one question: *may this record leave the device to a cloud
provider?* It does **not** describe:

- how sensitive the information is for **local** logging or diagnostics;
- whether it should be included in an **export**;
- how it should be handled under a **future encrypted multi-device sync**;
- whether a **UI** should redact/mask it when displayed.

A record can be `LOCAL_ONLY` and still be entirely mundane (a routine
gym-schedule preference), or `LOCAL_ONLY` and highly sensitive (a health
observation, per §13's LiFeOS example). **No `sensitivity` column is
added in M3.2** — not justified by any concrete requirement yet. This
section exists so a future milestone that *does* need a sensitivity axis
(LiFeOS health/finance encryption-at-rest policy is the most likely
trigger) adds it as a new, additive column or a small policy table,
without anyone having assumed `CloudEligibility` already covered it.

## §13 — Relation/entity model: the limit, stated explicitly

The closed graph over `memory_records` (§6, Revision 2, unchanged
mechanism) is the **M3.2 foundation choice**, not a claim that NeXa will
never need a dedicated canonical Entity/World Model. If a future milestone
demonstrates real need — entities requiring non-memory-shaped attributes,
or cross-mention entity resolution/deduplication — a dedicated Entity
table can be added later without invalidating this design:
`relation_type` stays an open string, `memory_relations`/`related_to()`
stay bounded (§7, Revision 2), and no graph-traversal framework is built
now or implied by anything here. This is a scoped, revisable choice.

## §14 — Category semantics, made precise

Each `MemoryCategory` now has one operational test, so two future domains
classify the same *shape* of information the same way:

```
FACT        A binary/categorical assertion. Test: when it changes, does
             the OLD value become literally FALSE (not just outdated data)?
             -> FACT. Example: "works at Company A" -- once superseded,
             the old employer claim is now false, not just an old reading.

STATE        A continuous or frequently-updating measurement/status of an
              ongoing thing. Test: does the OLD value remain a valid
              historical DATA POINT in a trend/series, never becoming
              "false," just no-longer-current? -> STATE. Examples: skill
              mastery %, routine active/inactive, device battery %,
              project/milestone status, financial balance.

PREFERENCE     The user's own stated or inferred inclination/choice, kept
                distinct from FACT even though structurally similar,
                because it needs distinct product handling (personalization
                surfaces, consent granularity). Test: is this specifically
                about what the user LIKES/CHOOSES, as opposed to an
                objective fact about the world? -> PREFERENCE (wins over
                FACT/STATE when ambiguous).

EPISODE         A specific, dated, narrative occurrence -- typically
                 low-frequency and human/conversation-sourced. Test: is
                 this a one-off remembered HAPPENING, narrative rather than
                 measurement or log-shaped, usually provenance
                 EXPLICIT_USER_STATEMENT or CONVERSATION_DERIVED?
                 -> EPISODE. Example: "mentioned their cat was sick,"
                 "moved to a new apartment."

EVENT            A timestamped, typically machine-logged or transactional
                  occurrence -- usually higher-frequency and system/
                  import-sourced. Test: is this a SYSTEM-LOGGED or
                  transactional occurrence, one of potentially many similar
                  rows, usually provenance SYSTEM_OBSERVATION or IMPORTED?
                  -> EVENT. Example: a financial transaction, a
                  lesson_event, a device-sensor reading.
```

**FACT vs. STATE, precisely:** does the old value become *false*, or does
it remain a *valid historical reading*? **EPISODE vs. EVENT, precisely:**
narrative/human-sourced/low-frequency vs. logged/machine-sourced/
higher-frequency. Both tests held up against every example in §Teacher/
§LiFeOS/§Projects/§Device stress tests below without exception — the set
is kept exactly as-is (five values), not expanded or collapsed.

---

## Full schema (revised)

```sql
CREATE TABLE schema_components (
    component  TEXT PRIMARY KEY,
    version    INTEGER NOT NULL
);
-- rows inserted by M3.2: ('core_storage', 1), ('memory', 1)

CREATE TABLE core_kv (                    -- backs SQLiteCoreStore (M3.1 CoreStore contract)
    collection  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (collection, key)
);

CREATE TABLE memory_records (
    id                 TEXT PRIMARY KEY,           -- uuid4 hex, stable across backup/restore/sync
    namespace          TEXT NOT NULL,                -- dot-hierarchical domain owner (open, §6)
    category           TEXT NOT NULL,                 -- small stable MemoryCategory (closed, §14)
    record_type        TEXT NOT NULL,                  -- domain-owned short token (open, §6)
    payload_version    INTEGER NOT NULL DEFAULT 1,       -- domain-owned payload_json shape version (§5)
    scope_type         TEXT,                              -- generic anchor (§3, Revision 2)
    scope_id           TEXT,                                -- opaque id within scope_type
    content            TEXT NOT NULL,                          -- short human-readable gist, always present
    payload_json       TEXT,                                     -- optional structured domain data (§5)
    valid_from         TEXT,                                      -- ISO-8601 UTC, real-world validity start
    valid_until        TEXT,                                       -- ISO-8601 UTC, real-world validity end
    created_at         TEXT NOT NULL,                                -- ISO-8601 UTC, storage bookkeeping
    updated_at         TEXT NOT NULL,                                 -- ISO-8601 UTC, storage bookkeeping
    confidence         REAL,                                           -- cached, NOT the source of truth (§4)
    cloud_eligibility  TEXT NOT NULL,                                    -- nexa.core.privacy.CloudEligibility (§1)
    status             TEXT NOT NULL,                                     -- MemoryStatus: ACTIVE|SUPERSEDED|RETRACTED (§10)
    supersedes_id      TEXT REFERENCES memory_records(id) ON DELETE SET NULL,  -- §9
    CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    CHECK (payload_json IS NULL OR (json_valid(payload_json) AND json_type(payload_json) = 'object'))
);
CREATE INDEX idx_memory_namespace   ON memory_records(namespace, status);
CREATE INDEX idx_memory_record_type ON memory_records(record_type, status);
CREATE INDEX idx_memory_category    ON memory_records(category, status);
CREATE INDEX idx_memory_scope       ON memory_records(scope_type, scope_id, status);
CREATE INDEX idx_memory_updated_at  ON memory_records(updated_at);
CREATE INDEX idx_memory_valid_range ON memory_records(valid_from, valid_until);

CREATE TABLE memory_evidence (                -- NEW (§3)
    id            TEXT PRIMARY KEY,
    memory_id     TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,  -- §9
    provenance    TEXT NOT NULL,
    source_kind   TEXT,
    source_ref    TEXT,
    confidence    REAL,
    observed_at   TEXT,
    created_at    TEXT NOT NULL,
    payload_json  TEXT,
    CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    CHECK (payload_json IS NULL OR json_valid(payload_json))
);
CREATE INDEX idx_evidence_memory_id ON memory_evidence(memory_id, observed_at);

CREATE TABLE memory_relations (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,  -- §9
    relation_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,  -- §9
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE INDEX idx_relations_source ON memory_relations(source_id, relation_type, status);
CREATE INDEX idx_relations_target ON memory_relations(target_id, relation_type, status);
```

## Revised repository / service APIs

```python
# nexa.core.memory.models
class MemoryCategory(StrEnum):
    FACT = "fact"; EPISODE = "episode"; PREFERENCE = "preference"
    STATE = "state"; EVENT = "event"

class MemoryProvenance(StrEnum):           # unchanged from Revision 2
    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    CONVERSATION_DERIVED = "conversation_derived"
    USER_CORRECTION = "user_correction"
    SYSTEM_OBSERVATION = "system_observation"
    IMPORTED = "imported"
    LEARNED_INFERENCE = "learned_inference"

class MemoryStatus(StrEnum):               # DELETED renamed to RETRACTED (§10)
    ACTIVE = "active"; SUPERSEDED = "superseded"; RETRACTED = "retracted"

class MemoryWriteTrigger(StrEnum):         # unchanged from Revision 2
    EXPLICIT_REMEMBER_REQUEST = "explicit_remember_request"
    CORRECTION = "correction"
    DURABLE_CANDIDATE = "durable_candidate"

@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str; namespace: str; category: MemoryCategory; record_type: str
    payload_version: int; scope_type: str | None; scope_id: str | None
    content: str; payload: dict | None
    valid_from: datetime | None; valid_until: datetime | None
    created_at: datetime; updated_at: datetime
    confidence: float | None                          # cached (§4)
    cloud_eligibility: CloudEligibility                # from nexa.core.privacy (§1)
    status: MemoryStatus; supersedes_id: str | None

@dataclass(frozen=True, slots=True)
class MemoryEvidence:                                  # NEW (§3)
    id: str; memory_id: str; provenance: MemoryProvenance
    source_kind: str | None; source_ref: str | None
    confidence: float | None; observed_at: datetime | None
    created_at: datetime; payload: dict | None

@dataclass(frozen=True, slots=True)
class MemoryRelation:
    id: str; source_id: str; relation_type: str; target_id: str
    created_at: datetime; status: str

@dataclass(frozen=True, slots=True)
class RememberResult:                                  # NEW (§3)
    memory_id: str; evidence_id: str; was_new_memory: bool

@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]; next_cursor: str | None


# nexa.core.memory.repository
class MemoryRepository:
    def by_id(self, id: str) -> MemoryRecord | None: ...
    def by_ids(self, ids: Sequence[str]) -> tuple[MemoryRecord, ...]: ...
    def by_namespace(self, namespace: str, *, record_type: str | None = None,
                      category: MemoryCategory | None = None,
                      cloud_eligibility: CloudEligibility | None = None,
                      statuses: set[MemoryStatus] | None = None,   # None = ACTIVE only (§2)
                      limit: int, cursor: str | None = None) -> Page[MemoryRecord]: ...
    def by_scope(self, scope_type: str, scope_id: str, *,
                 statuses: set[MemoryStatus] | None = None,
                 limit: int, cursor: str | None = None) -> Page[MemoryRecord]: ...
    def recent(self, limit: int, *, namespace: str | None = None,
               since: datetime | None = None,
               statuses: set[MemoryStatus] | None = None,
               cursor: str | None = None) -> Page[MemoryRecord]: ...
    def valid_at(self, at: datetime, *, namespace: str | None = None,
                 record_type: str | None = None,
                 statuses: set[MemoryStatus] | None = None,   # None = ALL statuses (§2, historical default)
                 limit: int, cursor: str | None = None) -> Page[MemoryRecord]: ...
    def evidence_for(self, memory_id: str, *, limit: int,
                      cursor: str | None = None) -> Page[MemoryEvidence]: ...
    def related_to(self, memory_id: str, *, relation_type: str | None = None,
                    limit: int) -> tuple[MemoryRelation, ...]: ...


# nexa.core.memory.service
class MemoryService:
    def remember(self, trigger: MemoryWriteTrigger, *, namespace: str,
                 category: MemoryCategory, record_type: str, content: str,
                 payload: dict | None = None, payload_version: int = 1,
                 scope_type: str | None = None, scope_id: str | None = None,
                 valid_from: datetime | None = None, valid_until: datetime | None = None,
                 provenance: MemoryProvenance, source_kind: str | None = None,
                 source_ref: str | None = None, confidence: float | None = None,
                 observed_at: datetime | None = None,
                 cloud_eligibility: CloudEligibility = CloudEligibility.LOCAL_ONLY,
                 ) -> RememberResult: ...              # §3 dedup-preserves-evidence behavior
    def update_metadata(self, id: str, *, cloud_eligibility: CloudEligibility | None = None,
                        confidence: float | None = None) -> None: ...   # never touches content
    def supersede(self, old_id: str, new_content: str, *, provenance: MemoryProvenance,
                  **kwargs) -> RememberResult: ...      # closes old valid_until for FACT/STATE (§ Rev.2 §5)
    def retract(self, id: str, *, reason: str | None = None) -> None: ...   # renamed (§10)
    def hard_delete(self, id: str, *, reason: str) -> None: ...             # §9, reason required
    def relate(self, source_id: str, relation_type: str, target_id: str) -> str: ...
    def unrelate(self, relation_id: str) -> None: ...
    # query_cloud_safe() REMOVED (§11) -- use by_namespace/by_scope/recent/valid_at with
    # the generic cloud_eligibility filter instead.
```

## Proposed modules (revised)

```
src/nexa/core/
  privacy.py                # NEW -- CloudEligibility, filter_cloud_safe (canonical, §1)
  memory/
    __init__.py
    models.py                # MemoryCategory, MemoryProvenance, MemoryStatus,
                              #   MemoryWriteTrigger + MemoryRecord, MemoryEvidence,
                              #   MemoryRelation, RememberResult, Page
    repository.py             # MemoryRepository -- typed SQLite CRUD + evidence + relations + retrieval
    service.py                 # MemoryService -- write policy, lifecycle, no cloud/provider knowledge
  storage/
    sqlite.py                  # connection/bootstrap: PRAGMA setup, schema_components gate (§8),
                                #   default_data_dir() (Revision 2 §9, unchanged), backup_to() (§7)
    sqlite_core_store.py         # SQLiteCoreStore -- concrete CoreStore (core_kv table)

src/nexa/realtime/
  privacy.py                   # BECOMES a re-export of nexa.core.privacy (§1, migration, planned)
  snapshot.py                    # updated to import CloudEligibility from nexa.core.privacy (§1)
```

---

## Stress tests (examples/relations only, not implemented)

Record-type values below use the corrected §6 convention (short,
unqualified `record_type`; `namespace` supplies ownership).

### NeXa Teacher

```
namespace="teacher.python", category=STATE, record_type="concept",
scope_type="concept", scope_id="recursion", content="Concept: recursion (Python)"

namespace="teacher.python", category=STATE, record_type="skill_mastery",
scope_type="skill", scope_id="recursion",
content="Recursion mastery: 55% (8 attempts)", payload_version=1,
payload={"skill":"recursion","mastery":0.55,"attempt_count":8}
-- superseded over time (§Q); each attempt can ALSO be modeled as its own
   evidence (provenance=SYSTEM_OBSERVATION, source_kind="teacher_exercise_attempt")
   on this STATE record, OR as its own EVENT record -- domain's choice, both fit.

namespace="teacher.python", category=EVENT, record_type="lesson_event",
scope_type="lesson", scope_id="python_recursion_101",
content="Completed lesson: Python Recursion 101",
provenance(evidence)=SYSTEM_OBSERVATION, source_kind="lesson_engine"

namespace="teacher.python", category=EPISODE, record_type="mistake",
content="Off-by-one error in recursive base case", payload={"exercise":"fib_sequence"}

namespace="teacher.python", category=STATE, record_type="learning_goal",
scope_type="goal", scope_id="master_recursion",
content="Learning goal: master recursion", payload={"status":"in_progress"}

namespace="teacher.python", category=STATE, record_type="spaced_review",
scope_type="skill", scope_id="recursion",
content="Next review due 2026-09-23", payload={"interval_days":7,"ease":2.3}

relations:
  lesson_event  --teaches-->        concept(recursion)
  mistake       --concerns-->        concept(recursion)
  concept(loops) --prerequisite_of--> concept(recursion)
```

### LiFeOS

```
namespace="lifeos.routines", category=STATE, record_type="routine",
scope_type="routine", scope_id="exercise",
content="Exercise routine: 3x/week", payload={"activity":"exercise","frequency":"3_per_week"},
valid_from=2026-01-01, valid_until=null

namespace="lifeos.goals", category=STATE, record_type="goal",
scope_type="goal", scope_id="run_5k",
content="Goal: run a 5k by end of year", payload={"target_date":"2026-12-31","status":"in_progress"}
relation: goal --belongs_to--> project(lifeos.projects/garden_rebuild)

namespace="lifeos.events", category=EPISODE, record_type="life_event",
content="Moved to a new apartment", valid_from=2026-06-01

namespace="lifeos.projects", category=STATE, record_type="project",
scope_type="project", scope_id="garden_rebuild",
content="Project: rebuild the garden", payload={"status":"active"}

namespace="user", category=PREFERENCE, record_type="preference",
content="Prefers morning workout reminders"

namespace="lifeos.health", category=EVENT, record_type="health_observation",
content="Resting heart rate 58 bpm", payload={"metric":"resting_hr","value":58,"unit":"bpm"},
provenance(evidence)=SYSTEM_OBSERVATION, source_kind="device_sensor", cloud_eligibility=LOCAL_ONLY

namespace="lifeos.finance", category=EVENT, record_type="financial_event",
content="Grocery purchase: £42.10", payload={"amount":42.10,"currency":"GBP"},
provenance(evidence)=IMPORTED, source_kind="import_file", cloud_eligibility=LOCAL_ONLY
```

### Projects

```
namespace="projects.nexa", category=EPISODE, record_type="decision",
content="Decided: single MemoryService authority, no per-domain databases",
valid_from=2026-09-16   -- EPISODE: a one-off, narrative, dated decision moment

namespace="projects.nexa", category=STATE, record_type="milestone",
scope_type="project", scope_id="nexa_ikigai",
content="Milestone: M3.2 design approved", payload={"status":"done"}
-- STATE: milestone status progresses (not_started -> in_progress -> done);
   old statuses remain valid historical readings, never "false"
```

### Future device / system observation

```
namespace="devices.respeaker", category=FACT, record_type="firmware_version",
content="reSpeaker XVF3800 firmware: 1.3", valid_from=2026-09-16
-- FACT: a categorical assertion; once updated, the OLD version claim
   becomes literally false as "current firmware" (still true it WAS
   installed then -- exactly why superseded rows stay queryable via
   valid_at, §2)

namespace="devices.respeaker", category=STATE, record_type="battery_level",
content="Battery: 82%", payload={"percent":82},
provenance(evidence)=SYSTEM_OBSERVATION, source_kind="device_sensor"
-- STATE: continuously varying measurement; old readings remain valid
   historical data points, not "false"
```

No new column was needed anywhere across Teacher, LiFeOS, Projects, or
device/system observation — every distinction is carried by `namespace`,
`category`, `record_type`, `scope_type`/`scope_id`, `payload`/
`payload_version`, and `memory_evidence`.

---

## Acceptance tests (revised, supersedes Revision 2's list)

1. **Persistence survives process restart** (unchanged).
2. **`ConversationSession` history is not automatically duplicated as
   memory** (unchanged).
3. **Evidence provenance/source/confidence roundtrip** — write
   `memory_evidence` with each `MemoryProvenance` value and varied
   `source_kind`/`source_ref`/`observed_at`, read back, exact match.
4. **Privacy classification (`nexa.core.privacy.CloudEligibility`)
   roundtrips; default is `LOCAL_ONLY`.**
5. **Namespace/record_type extensibility** — write under a namespace/
   record_type not present anywhere in `nexa.core.memory`'s own code
   (e.g. `"teacher.made_up_for_the_test"` / `"made_up_type"`), confirm
   `by_namespace`/retrieval finds it — proves extensibility rather than
   asserting it.
6. **Namespace/record_type validation is deterministic** — malformed
   input (uppercase, dot-qualified `record_type`, `record_type` repeating
   its own `namespace`) is rejected identically every time; well-formed
   input is accepted identically every time (§6).
7. **Generic scope anchor works for two unrelated domains simultaneously**
   (unchanged).
8. **Historical `valid_at(t)` returns a `SUPERSEDED` record that was true
   at `t`, with NO special flag required** — the exact Company A / Company
   B scenario from review (§2, corrected).
9. **Duplicate canonical memory acquires a second, independent evidence
   source without losing the first** — `remember()` called twice with
   identical `(namespace, record_type, scope_type, scope_id, content)`
   but different provenance returns the same `memory_id`, `was_new_memory
   =False` the second time, and **both** evidence rows are retrievable via
   `evidence_for()` (§3, replaces Revision 2's "no silent duplication"
   test, corrected to preserve evidence).
10. **Relations work and are bounded; `unrelate()` soft-deletes without
    deleting either endpoint record** (unchanged).
11. **Correction/supersession preserves auditability** (unchanged).
12. **Deterministic, bounded, paginated retrieval** — including the
    generic `cloud_eligibility` filter (§11) and `statuses` filter (§2)
    on every applicable method.
13. **Domain `payload_version` and `payload` survive roundtrip** — write
    `payload_version=2` with a shape only that version uses, read back
    exact match, including canonical JSON serialization (§5).
14. **Invalid/malformed payload fails loudly** — a non-JSON-serializable
    `payload` (e.g. containing a Python object), a non-`dict` top-level
    payload, or (bypassing the Python layer to test the DB constraint
    directly) a non-object `payload_json` string all raise a clear,
    typed error — no silent truncation or coercion.
15. **Evidence survives process restart** — write evidence, close/reopen
    the connection, confirm the same evidence rows for the same
    `memory_id` are retrievable, in the same order.
16. **Live-WAL-safe backup** — with WAL active and a pending write, call
    the documented `backup_to()` (`VACUUM INTO`); open the resulting file
    as an independent connection; confirm it is fully consistent (contains
    the write) — proving the API is used, not a naive file copy (§7).
17. **Missing component version + existing component tables fails
    visibly** — manually create `memory_records` without a `schema_components`
    row for `'memory'`; connecting raises `SchemaStateError`, not a silent
    "first run" (§8).
18. **Component version present + malformed/missing owned schema fails
    visibly** — a `schema_components` row for `'memory'` at the expected
    version but a `memory_records` table missing an expected column raises
    `SchemaStateError` (§8).
19. **Hard erase removes/repairs all relevant FK relations atomically** —
    create A, B (supersedes A), evidence for both, a relation
    A→C; `hard_delete(A, reason=...)`; assert: A's evidence gone, the
    A↔C relation gone, `B.supersedes_id IS NULL`, and **C is completely
    untouched** (still exists, its own unrelated evidence/relations
    intact) (§9).
20. **`retract()` does not claim physical deletion** — after `retract()`,
    the row is still returned by `by_id`, excluded from default
    "current knowledge" queries (`statuses=None` defaults to ACTIVE-only
    there), and **still included** by `valid_at()`'s historical default
    (§2, §10).
21. **Cloud code cannot bypass the snapshot boundary** — source-scan:
    nothing under any current/future cloud-provider package imports
    `nexa.core.memory` (unchanged).
22. **`nexa.core` has zero dependency on `nexa.realtime`** — source-scan
    the entire `nexa/core/` tree; assert no file imports anything under
    `nexa.realtime` (§1, new).
23. **Dependency direction is inward, not just absent** — `nexa/realtime/
    privacy.py` imports from `nexa.core.privacy` (re-export); it is never
    the other way around; `nexa.core.privacy` has zero imports from
    `nexa.realtime` (§1, new — stronger than §22 alone, since §22 only
    proves absence in one direction).
24. **No credentials/env leakage** (unchanged).

## Migration / versioning

Unchanged mechanism from Revision 2, refined bootstrap rule per §8:
component-aware `schema_components` (one row per component: `core_storage`,
`memory`, and any future component), no cross-component coupling, no
automatic migration, and now an explicit, tested distinction between
"genuine first run" and "inconsistent/corrupt state" instead of treating
every missing version row as first-run.

---

## NEXT STEP

Implementation of M3.2 against this revised design, gated on review —
**including, as a prerequisite first step, the small standalone `nexa.core.privacy`
relocation from §1 (with its own ADR amendment), executed before any new
memory code is written.** Not started in this checkpoint, per explicit
instruction.
