# R0074 — M3.2 Memory Foundation Implementation

**Date:** 2026-09-16
**Milestone:** M3.2 — NeXa Core, Memory Foundation (implementation)
**Status:** Implemented and verified against R0073's final (third) design
revision, including its five last-review corrections. Not pushed.

## TASK RESULT

**PASS.**

## WHAT I DID

Implemented the NeXa Memory Platform exactly as approved in R0073
(Revision 3 + final corrections), in the prescribed order:

1. **Core privacy relocation (prerequisite).** Moved the canonical
   `CloudEligibility`/`filter_cloud_safe` from `nexa.realtime.privacy` to
   `nexa.core.privacy` — one definition, not a duplicate. `nexa.realtime.privacy`
   is now a two-line re-export so every existing
   `from nexa.realtime.privacy import CloudEligibility` call site keeps
   working unchanged (verified by test, not just by inspection).
   `nexa.realtime.snapshot` now imports the canonical type directly. Ran
   the full privacy/snapshot/cloud-conversation regression suite (25
   tests) before continuing, per instruction — all passed.
2. **SQLite application-data/storage foundation.** `nexa.core.storage.sqlite`:
   `default_data_dir()` (XDG-aware, `NEXA_DATA_DIR` override for tests,
   never defaults under the repo's `var/`), `connect()` (WAL + foreign
   keys), `bootstrap_component()` (the first-run-vs-corruption decision
   table from R0073 §8), `backup_to()` (`sqlite3.Connection.backup()`,
   per the final review's stdlib preference over `VACUUM INTO`).
   `SQLiteCoreStore` (generic `core_kv` table, the concrete `CoreStore`
   from M3.1 — Memory does not use it).
3. **Memory models.** `MemoryCategory` (FACT/STATE/PREFERENCE/EPISODE/EVENT,
   defined by information SHAPE, never by provenance — final review §3),
   `MemoryProvenance`, `MemoryStatus` (ACTIVE/SUPERSEDED/RETRACTED — no
   `DELETED`, final review §10), `MemoryWriteTrigger`,
   `HardDeleteReasonCode`, and the frozen dataclasses `MemoryRecord`
   (no confidence/provenance/source fields — final review §2/§3),
   `MemoryEvidence`, `MemoryRelation`, `RememberResult`, `Page`.
4. **Memory repository.** Typed SQLite CRUD over `memory_records`/
   `memory_evidence`/`memory_relations` (one `schema_components` row,
   `'memory'`, covering all three — they evolve as one unit). Bounded,
   cursor-paginated retrieval (`by_namespace`, `by_scope`, `recent`,
   `valid_at`, `evidence_for`, `related_to`) with a `MAX_QUERY_LIMIT=1000`
   gate — no method can return "everything."
5. **Evidence.** `find_active_duplicate()` + evidence-on-dedup wiring in
   the service (below) — a repeated/independent source ADDS evidence, it
   never overwrites or discards.
6. **Temporal queries.** `valid_at(t)` — corrected per the FINAL review
   (not Revision 3's draft): defaults to `ACTIVE + SUPERSEDED` only,
   **excluding `RETRACTED`** (retraction means "withdrawn/invalid," not
   "historically true but superseded" — final review §1). An explicit
   `statuses={MemoryStatus.RETRACTED}` still retrieves it for audit.
7. **Relations.** `memory_relations`, closed graph over `memory_records`,
   `ON DELETE CASCADE` on both endpoints.
8. **`MemoryService` write/lifecycle policy.** `remember()` (dedup adds
   evidence, `DURABLE_CANDIDATE` requires `confidence`), `update_metadata()`
   (cloud_eligibility only — no record-level confidence exists to update),
   `supersede()` (never overwrites content; closes `valid_until` for
   FACT/STATE), `retract()` (renamed from "deactivate," never called
   "forget" — final review §10), `hard_delete()` (`reason_code`, a
   validated short machine token, **never free text** — final review §4),
   `relate()`/`unrelate()`. Provider-neutral: no `query_cloud_safe()` —
   `cloud_eligibility` is a generic filter on the ordinary retrieval
   methods instead (final review §11).
9. **Backup.** `backup_to()` used via the stdlib online backup API;
   verified WAL-safe against a live connection with uncommitted-to-disk
   committed data.
10. **Acceptance/regression tests.** 78 new tests across 6 files (below);
    full suite re-run.
11. **This report**, plus `CURRENT_STATE.md`/`ROADMAP.md` updates
    (separate commit hunks, see below).

## FIVE FINAL-REVIEW CORRECTIONS APPLIED (beyond Revision 3)

1. **`valid_at(t)` default is `ACTIVE + SUPERSEDED`, NOT `+ RETRACTED`.**
   Implemented and tested: a `RETRACTED` "owns a Ferrari" record is
   invisible to `valid_at()` by default, visible only via an explicit
   `statuses={RETRACTED}` audit query.
2. **`memory_records.confidence` removed entirely.** Confidence exists
   only on `MemoryEvidence` — verified by a test asserting
   `MemoryRecord` has no `confidence` field at all, and that
   `update_metadata()` no longer accepts one.
3. **`MemoryCategory` redefined to describe semantics, not source.**
   FACT/STATE/PREFERENCE/EPISODE/EVENT definitions no longer mention
   provenance; a dedicated test (`test_category_independent_of_provenance`)
   proves the SAME occurrence classifies identically whether reported by
   the user or observed by a system.
4. **`hard_delete()` takes `reason_code`, not free text, and logs
   nothing sensitive.** `HardDeleteReasonCode` (a small, extensible,
   validated vocabulary); the log line contains only the memory id and
   the reason code — a dedicated test writes a record with deliberately
   "SUPER-SECRET"-flagged content/payload/source_ref, hard-deletes it
   while capturing logs, and asserts none of those markers appear
   anywhere in the captured log output.
5. **Backup uses `sqlite3.Connection.backup()`** (stdlib preference,
   not `VACUUM INTO`) — implemented and tested against a live WAL
   connection with data not yet checkpointed to the main file.

## WHAT I VERIFIED

```
tests/test_core_privacy.py                  8 passed   (relocation + re-export + dependency-direction scan)
tests/test_core_storage_sqlite.py          14 passed   (data dir, bootstrap decision table, WAL backup, CoreStore)
tests/test_core_memory_models.py            7 passed   (construction, immutability, no confidence field)
tests/test_core_memory_repository.py       21 passed   (CRUD, pagination, temporal, relations, hard-delete FK)
tests/test_core_memory_service.py          23 passed   (write policy, lifecycle, logging safety, no cloud coupling)
tests/test_core_memory_stress_examples.py   5 passed   (Teacher, LiFeOS, Projects, device -- schema unchanged)
```
= **78 new tests, all passing**, each written against the FINAL design
(the five last-review corrections — `valid_at` excluding `RETRACTED`,
no record-level confidence, provenance-independent category, safe
`hard_delete` logging, stdlib backup — are baked into these tests, not
bolted on afterward).

Full regression suite (`./.venv/bin/python3 -m pytest tests/ -q`, real
run, sandbox disabled): **1264 passed, 7 skipped, 1 pre-existing unrelated
failure** (`tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::
test_local_audio_config_fields_are_typed_and_explicit`, caused by the
already-known, still-uncommitted `scheduled_aec_reference` field from the
paused R0068-R0070 work — untouched by this checkpoint, same failure
present before this work started).

`ruff check` clean on every new/changed file (fixed 2 `E501`s in source
and ~20 `E501`/`I001`/`B017` issues in the new tests along the way).
`py_compile` clean on all 16 files under `src/nexa/core/`. `git diff --check`
clean (see staged diff below).

## SCHEMA (final, as implemented)

```sql
CREATE TABLE schema_components (component TEXT PRIMARY KEY, version INTEGER NOT NULL);
-- rows: ('core_storage', 1), ('memory', 1)

CREATE TABLE core_kv (collection TEXT NOT NULL, key TEXT NOT NULL,
    value_json TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, key));

CREATE TABLE memory_records (
    id TEXT PRIMARY KEY, namespace TEXT NOT NULL, category TEXT NOT NULL,
    record_type TEXT NOT NULL, payload_version INTEGER NOT NULL DEFAULT 1,
    scope_type TEXT, scope_id TEXT, content TEXT NOT NULL, payload_json TEXT,
    valid_from TEXT, valid_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    cloud_eligibility TEXT NOT NULL, status TEXT NOT NULL,
    supersedes_id TEXT REFERENCES memory_records(id) ON DELETE SET NULL,
    CHECK (payload_json IS NULL OR (json_valid(payload_json) AND json_type(payload_json)='object'))
);  -- 6 indexes: namespace, record_type, category, scope, updated_at, valid range

CREATE TABLE memory_evidence (
    id TEXT PRIMARY KEY, memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    provenance TEXT NOT NULL, source_kind TEXT, source_ref TEXT, confidence REAL,
    observed_at TEXT, created_at TEXT NOT NULL, payload_json TEXT,
    CHECK (confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    CHECK (payload_json IS NULL OR json_valid(payload_json))
);

CREATE TABLE memory_relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL, target_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
```

## FILES CHANGED

```
NEW:
  src/nexa/core/privacy.py
  src/nexa/core/storage/sqlite.py
  src/nexa/core/storage/sqlite_core_store.py
  src/nexa/core/memory/__init__.py
  src/nexa/core/memory/errors.py
  src/nexa/core/memory/models.py
  src/nexa/core/memory/validation.py
  src/nexa/core/memory/repository.py
  src/nexa/core/memory/service.py
  tests/test_core_privacy.py
  tests/test_core_storage_sqlite.py
  tests/test_core_memory_models.py
  tests/test_core_memory_repository.py
  tests/test_core_memory_service.py
  tests/test_core_memory_stress_examples.py
  docs/reports/R0073_m3_2_memory_foundation_design_20260916.md
  docs/reports/R0074_m3_2_memory_foundation_implementation_20260916.md (this file)

MODIFIED:
  src/nexa/realtime/privacy.py     (-> re-export of nexa.core.privacy)
  src/nexa/realtime/snapshot.py    (imports CloudEligibility from nexa.core.privacy)
  src/nexa/core/storage/__init__.py (exports the new storage/sqlite symbols)
  docs/CURRENT_STATE.md, docs/ROADMAP.md (checkpoint update, below)
```

## EXAMPLES (real output from this session's own test runs / smoke checks)

### Teacher example
```
concept       = remember(namespace="teacher.python", category=STATE, record_type="concept", ...)
mastery       = remember(..., record_type="skill_mastery", payload={"skill":"recursion","mastery":0.55,"attempt_count":8})
lesson_event  = remember(..., category=EVENT, record_type="lesson_event")
mistake       = remember(..., category=EPISODE, record_type="mistake")
relate(lesson_event -> "teaches" -> concept)
relate(mistake -> "concerns" -> concept)
related_to(concept) == {"teaches", "concerns"}   # both edges present, bounded query
```
No Teacher-specific column exists anywhere in the schema.

### LiFeOS example
```
routine  = remember(namespace="lifeos.routines", category=STATE, record_type="routine", ...)
goal     = remember(namespace="lifeos.goals", category=STATE, record_type="goal", ...)
project  = remember(namespace="lifeos.projects", category=STATE, record_type="project", ...)
relate(goal -> "belongs_to" -> project)
health   = remember(namespace="lifeos.health", category=EVENT, record_type="health_observation",
                     cloud_eligibility=LOCAL_ONLY)   # stays LOCAL_ONLY, verified
finance  = remember(namespace="lifeos.finance", category=EVENT, record_type="financial_event",
                     cloud_eligibility=LOCAL_ONLY)
```

### Duplicate-memory + multiple-evidence example (real captured run)
```
r1 = remember(EXPLICIT_REMEMBER_REQUEST, content="Prefers morning workouts",
              provenance=EXPLICIT_USER_STATEMENT)
  -> memory_id=67a0e2b6..., was_new_memory=True

r2 = remember(EXPLICIT_REMEMBER_REQUEST, content="Prefers morning workouts",
              provenance=EXPLICIT_USER_STATEMENT)      # repeated
  -> memory_id=67a0e2b6... (SAME), was_new_memory=False

r3 = remember(DURABLE_CANDIDATE, content="Prefers morning workouts",
              provenance=SYSTEM_OBSERVATION, source_kind="lifeos_routine_pattern", confidence=0.81)
  -> memory_id=67a0e2b6... (SAME), was_new_memory=False

evidence_for(67a0e2b6...) -> 3 rows: SYSTEM_OBSERVATION, EXPLICIT_USER_STATEMENT, EXPLICIT_USER_STATEMENT
by_namespace("user") -> exactly 1 canonical record
```

### Historical supersession example (real captured run)
```
old = remember(FACT, "Works at Company A", valid_from=2026-01-01)
new = supersede(old.memory_id, "Works at Company B", valid_from=2027-01-01)
  -> old.status = SUPERSEDED, old.valid_until = 2027-01-01 (auto-closed)
  -> new.status = ACTIVE, new.supersedes_id = old.id

valid_at(2026-06-01) -> ["Works at Company A"]   # no special flag needed
```

### Retraction example (real captured run)
```
retract_target = remember(FACT, "Owns a Ferrari", valid_from=2026-01-01)
retract(retract_target.memory_id)

valid_at(2026-03-01)                                    -> []                    (default: excludes RETRACTED)
valid_at(2026-03-01, statuses={RETRACTED})              -> ["Owns a Ferrari"]    (explicit audit)
```

### Physical erasure example (real captured run, FK behavior)
```
a = remember("A content")
b = supersede(a.id, "B content")           # b.supersedes_id == a.id
c = remember("C content")
relate(a, "relates_to", c)

hard_delete(a.id, reason_code=USER_REQUESTED)

by_id(a.id)                -> None                     (physically gone)
by_id(b.id).supersedes_id  -> None                      (nulled, not dangling)
by_id(c.id)                -> still "C content"          (completely untouched)
evidence_for(a.id)         -> ()                          (cascaded)
related_to(c.id)           -> ()                            (a<->c relation cascaded)
```

### Backup example (real captured run)
```
conn (WAL mode) <- 50 committed inserts
backup_to(conn, backup_path)                     # sqlite3.Connection.backup()
independent_connection(backup_path).execute("SELECT COUNT(*) FROM t") -> 50
```
Never a raw `cp` of the live file — the online backup API is exercised by
the test, not merely documented.

### Dependency-direction proof (real captured run)
```
ast-scan(nexa/core/privacy.py)              -> zero imports of nexa.realtime.*
ast-scan(nexa/realtime/privacy.py)          -> imports nexa.core.privacy (re-export)
ast-scan(entire src/nexa/core/** )          -> zero files import anything under nexa.realtime
CloudEligibility (core) is ReExportedCloudEligibility (realtime)  -> True (same object, not a duplicate)
```

## KNOWN LIMITATIONS

- **Ordering for all paginated queries (including `valid_at`) is
  uniformly `(updated_at DESC, id DESC)`**, not `valid_from`-ordered as
  R0073 Revision 3's prose footnote suggested — a deliberate
  implementation simplification (one pagination helper, one cursor
  format) that does not violate any acceptance requirement (determinism,
  boundedness, and correctness are all satisfied; only the specific sort
  key differs from that footnote).
- **`payload_version` mismatch handling is entirely the future domain's
  responsibility** — NeXa Memory stores and returns the integer but does
  not validate that a given `record_type`'s payload shape actually
  matches its declared version (by design — Memory never interprets
  `payload_json`'s contents).
- **No cross-component migration tooling** — `schema_components` gives
  each component its own version, but there is no migration *runner*;
  a real future schema change still requires hand-written, reviewed
  migration code (explicitly deferred in R0073 §10/§M, not a gap
  introduced here).
- **`MemoryRepository.related_to` has no multi-hop traversal** — one
  hop only, by design (R0073 §13); a future Context Engine or a
  dedicated Entity/World Model would build multi-hop reasoning on top,
  not here.
- **The pre-existing, unrelated `test_voice_architecture.py` failure**
  (paused R0068-R0070 `scheduled_aec_reference` field) remains present,
  unrelated to and untouched by this work.

## UNRESOLVED

None specific to M3.2. Everything explicitly out of scope for this
milestone (automatic conversation→memory extraction, Context Engine,
embeddings/vector DB, FTS, Teacher/LiFeOS implementations, a World Model,
multi-device sync, cloud memory, the learning algorithm) remains
unbuilt, as instructed.

## DOCUMENTATION UPDATED

- `docs/reports/R0073_m3_2_memory_foundation_design_20260916.md` (all
  three design revisions, kept as the historical design record).
- This report.
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` (concise checkpoint update).

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO.

## NEXT RECOMMENDED ACTION (do not start without review)

**M3.3 — Context Engine.** Decides which memories matter for a given
turn/task (reading `nexa.core.memory`'s bounded retrieval methods,
including the generic `cloud_eligibility` filter) and produces the
provider/local context projection — the layer that was explicitly kept
out of `MemoryService` in this checkpoint (R0073 final review §11).
