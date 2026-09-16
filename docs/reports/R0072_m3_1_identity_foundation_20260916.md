# R0072 — M3.1 Identity Foundation

**Date:** 2026-09-16
**Milestone:** M3.1 — NeXa Core, Identity Foundation (ADR-0005)
**Status:** M3.1 focused tests PASS; no new regressions detected; full suite
retains one known pre-existing unrelated failure from paused R0068-R0070
work. Committed locally (see `feat: establish NeXa Core identity
foundation`), not pushed.

## TASK RESULT

**PASS.**

## WHAT I DID

1. Wrote `docs/decisions/ADR-0005_nexa_core_identity_boundary.md` — a new
   ADR, since ADR-0004 Amendment 2 states the coarse NeXa-Core-vs-cloud-
   provider boundary but not `nexa.core`'s own internal structure. Records
   the corrected scope decision: `NeXaIdentity` is a small, stable,
   immutable root only — not a container for user preferences, memory,
   relationship, goals, or any other mutable state (those get their own
   future subsystem modules); persona stays the separate Conversation Style
   Layer; persistence direction (JSON for immutable config now, a shared
   `CoreStore` → `SQLiteCoreStore` interface for future mutable state);
   target `nexa.core` package structure; no `NeXaCore` god-object.
2. Created `src/nexa/core/` (new package):
   - `identity/model.py` — `NeXaIdentity` (frozen dataclass): `identity_id`,
     `identity_schema_version`, `name`, `product_name`, `purpose`,
     `principles: tuple[str, ...]`. Nothing else.
   - `identity/loader.py` — `load_identity(path)`: reads/validates versioned
     JSON, fails loudly (`IdentityConfigError`) on a missing file, invalid
     JSON, non-object JSON, any missing required field, a schema version
     other than `CURRENT_IDENTITY_SCHEMA_VERSION` (1), an empty/blank
     required string, or an empty `principles` list. No silent fallback to
     a default identity anywhere.
   - `identity/render.py` — `render_identity_instruction(identity)`: a pure
     function producing a short system-prompt instruction block from the
     identity fields only. Explicitly not a general context/prompt engine
     (that is the future Context Engine, out of scope here).
   - `storage/contracts.py` — `CoreStore` (a `Protocol`): `get`/`put`/
     `delete` over `(collection, key)`. Interface only, no implementation,
     no schema — for M3.2+ to build against.
   - `__init__.py` files re-exporting the small public surface of each
     subpackage (matching the existing `nexa.realtime` / `nexa.conversation`
     `__init__.py` convention).
3. Added `configs/identity/nexa_identity_v1.json` — the versioned seed
   identity config (`identity_schema_version: 1`), content grounded in
   ADR-0001's own already-decided product framing (personal AI system
   across devices, local-first/privacy-first/user-owned-data/one-canonical-
   authority/provider-and-device-independent).
4. Integrated into `src/nexa/bootstrap.py::build_default_session()` in the
   smallest safe way: loads identity, composes
   `render_identity_instruction(identity) + "\n\n" + persona.system` as the
   session's `system_prompt`. `PersonaConfig` / `load_persona()` are
   unchanged and still fully used — this is additive composition, not a
   replacement.
5. Wrote `tests/test_core_identity.py` — 13 tests covering the 10 required
   acceptance criteria (several criteria got more than one test): deterministic
   load, missing-field fail-loud, bad-schema-version fail-loud, missing-file
   fail-loud, empty-principles fail-loud, immutability (frozen dataclass +
   tuple principles), deterministic rendering, rendered text contains
   name/purpose/principles, rendered text excludes env/credential/memory-
   shaped content (including a live `os.environ` leak check), Identity-
   module-does-not-import-Persona independence proof (source-scan, not just
   behavior), Identity's field set has no persona/style concerns, and
   `build_default_session()` composes identity + persona correctly (order
   and content).

## WHAT I VERIFIED

- `./.venv/bin/python3 -m pytest tests/test_core_identity.py -q` — **13
  passed**.
- `./.venv/bin/python3 -m pytest tests/ -q` (full suite, sandbox disabled,
  real run — not the failing-to-launch system Python) — **1186 passed, 1
  failed, 7 skipped, 153s**. The one failure
  (`tests/test_voice_architecture.py::TestConfigIsExplicitAndTyped::
  test_local_audio_config_fields_are_typed_and_explicit`) is **pre-existing
  and unrelated to M3.1**: it fails because `src/nexa/voice/config.py`
  already carries an uncommitted `scheduled_aec_reference` field from the
  paused R0068–R0070 acoustic-scheduling work (confirmed: this session made
  no edits to `src/nexa/voice/config.py` or `tests/test_voice_architecture.py`).
  The 7 skips are all pre-existing live-hardware/live-service opt-in tests
  (`NEXA_RUN_LIVE_*` env vars), unrelated to this change.
- `ruff check src/nexa/core/ src/nexa/bootstrap.py tests/test_core_identity.py`
  — clean (found and fixed 3 line-length violations in `loader.py` during
  this checkpoint).
- `py_compile` on every new/changed file — clean.
- `git diff --check` on every new/changed file — clean, no whitespace errors.
- Manually rendered the seed identity to confirm real output (see below).

## TESTS

```
tests/test_core_identity.py                    13 passed
tests/ (full suite, sandboxed shell disabled)   1186 passed, 1 pre-existing
                                                 unrelated failure, 7 skipped
```

## EXAMPLE RENDERED IDENTITY INSTRUCTION

```
You are NeXa, part of NeXa IkiGai.
Purpose: NeXa is one personal AI system that spans many devices as bodies of the same system, with one central identity, memory, and context shared across all of them — not a separate assistant per device or per provider.
Principles:
- Local-first: NeXa's own state and reasoning run locally whenever possible; a cloud provider is an optional, replaceable capability, never the seat of NeXa's identity or memory.
- Privacy-first: what may reach a cloud provider is explicit and filtered by construction, never assumed.
- User-owned data: the user's history, memory, and context belong to the user, not to any provider.
- One canonical authority per responsibility: no competing brains, no duplicate identity, memory, or conversation authorities.
- Provider- and device-independent: local and cloud providers, and every device NeXa runs on, are replaceable bodies of the same NeXa — never separate identities.
```

This is prepended to the existing persona system prompt in
`build_default_session()`.

## ARCHITECTURE

```
src/nexa/core/
  __init__.py            # no god-object; docstring points to subsystem imports
  identity/
    __init__.py
    model.py              # NeXaIdentity (frozen dataclass, 6 fields, nothing else)
    loader.py              # load_identity() — fail-loud, no silent fallback
    render.py              # render_identity_instruction() — pure function
  storage/
    __init__.py
    contracts.py            # CoreStore Protocol — interface only, no implementation
configs/identity/nexa_identity_v1.json   # versioned, immutable seed config
```

`identity/` and `storage/` are the only subpackages created — no
`user/`, `memory/`, `context/`, `personality/`, `relationship/`,
`capabilities/`, `permissions/`, `devices/`, or `learning/` packages were
created ahead of the milestone that actually needs them (ADR-0005 D4).

## IDENTITY OWNERSHIP SCOPE (why this shape)

`NeXaIdentity` answers "what/who is NeXa, invariantly" — the same six
fields regardless of user, device, conversation, or which provider
(local/cloud) is currently active. It owns none of: user preferences,
relationship state, memories, projects/goals, learned facts, communication-
style adaptation, device state, or session/temporary mood — each of those
is a distinct kind of state with its own change rate and its own future
owner module (User Model, Relationship, Memory, Context, Goals/Projects,
Capabilities, Permissions, Devices, Learning). Putting any of them on
`NeXaIdentity` would recreate the "one growing brain object" failure ADR-
0001 already diagnosed in the legacy codebase (`docs/legacy/
LEGACY_NEXA_INDEX.md`), just under a new name.

**Why Persona is style, not identity:** `configs/personas/nexa_persona_v1.json`
already self-describes as "the minimal conversation-behavior layer only;
NeXa's identity, memory, and capabilities belong to NeXa's runtime, not
this prompt" — M3.1 makes that boundary real rather than aspirational.
Persona governs *how* NeXa talks (tone, brevity, bilingual mirroring
rules); Identity states *what NeXa is*. They can now change independently:
an A/B test on persona wording never touches identity, and a future
identity revision (e.g. a new principle) never touches persona wording.

**How this preps M3.2/M3.3:** `nexa.core.storage.contracts.CoreStore` gives
M3.2 (Memory Foundation) a persistence contract to implement
(`SQLiteCoreStore`) instead of inventing its own store. The subsystem-per-
concern pattern established here (own model/loader/render or equivalent,
own `__init__.py`, own tests) is the template M3.2 (Memory Foundation),
M3.3 (Context Engine), and later subsystems (including User Model, which
remains a canonical NeXa Core concern placed under M3.4 Personality +
Relationship, not renamed to M3.3 — see `docs/ROADMAP.md`) should follow,
rather than each subsystem improvising its own shape.

**`ConversationSession.history` is not long-term memory.** This distinction
stays load-bearing for M3.2 planning: `ConversationSession.history`
(`src/nexa/conversation/session.py`) is the canonical chat transcript for
one session — exactly what the user said and what the model replied,
nothing else. It is not touched by this checkpoint and must not become
NeXa's memory database; M3.2 defines a separate, typed memory store that
*may* later derive candidate memories from completed conversation events,
without duplicating or replacing canonical history.

## MIGRATION RISK

Low. `build_default_session()` is the only integration point changed, and
the change is additive (identity text prepended to the existing persona
`system_prompt` string) — no existing field, type, or call signature
changed. `ConversationSession.system_prompt` was already an opaque string
consumer-side, so no downstream code needed updating. The one path this
touches beyond bootstrap is any caller that asserts an exact
`system_prompt` value equal to `persona.system` alone — none exists in the
current test suite (verified: full suite green apart from the pre-existing,
unrelated `LocalAudioConfig` field-set failure above).

## UNRESOLVED / NOT BUILT (explicitly out of scope, per instruction)

Long-term memory, vector database, embeddings, full `UserModel`,
`RelationshipState`, goals, capabilities, permissions, device awareness,
learning, a general Context Engine, and any `NeXaCore` aggregating
god-object. None of these were touched or stubbed.

## DOCUMENTATION UPDATED

- `docs/decisions/ADR-0005_nexa_core_identity_boundary.md` (new).
- This report.
- `docs/CURRENT_STATE.md` / `docs/ROADMAP.md` — **not yet updated**, per
  instruction (update only after M3.1 review).

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO.

## NEXT RECOMMENDED ACTION (do not start without review)

**M3.2 — Memory Foundation.** Implement `SQLiteCoreStore` against
`nexa.core.storage.contracts.CoreStore`, and the first real mutable
subsystem (conversation-derived memory facts) on top of it — the first
real consumer of the persistence direction decided in ADR-0005 D3.
