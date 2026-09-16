# ADR-0005 — NeXa Core: Identity root vs. mutable subsystem boundary

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** Andrzej Dul (owner), Claude Code (agent)
- **Related:** ADR-0004 (`docs/decisions/ADR-0004_cloud_realtime_voice_provider_boundary.md`,
  Amendment 2 — states the coarse NeXa-Core-vs-cloud-provider ownership split
  but not `nexa.core`'s own internal structure), `docs/reports/R0072_m3_1_identity_foundation_20260916.md`,
  `docs/ROADMAP.md` (M3 — NeXa Core)

---

## Context

`OBSERVATION` — ADR-0004 Amendment 2 (2026-09-16) established that NeXa Core
(identity, personality, memory, user-model, goals, capabilities, permissions,
device-state, canonical history) is owned locally and never delegated to a
cloud provider, which is ephemeral and replaceable. It did not define
`nexa.core`'s own internal package structure, or what "Identity" means as a
distinct concept from the rest of that state.

`ASSUMPTION` — The product intent (ADR-0001 D2/D3: local-first,
privacy-first, user-owned-data-first, modular) implies NeXa's Core should be
composed of narrow, independently-owned subsystems rather than one large
object — the ADR-0001 "no MAS / one canonical authority per responsibility"
principle applies inside NeXa Core exactly as it applies to conversation and
voice today.

`HYPOTHESIS` (rejected during M3 planning) — An initial M3 draft treated
"Identity" as a container broad enough to eventually also hold user
preferences, relationship state, and learned facts. Andrzej rejected this: it
would recreate the "one growing god-object" failure mode ADR-0001 already
diagnosed in the legacy codebase, just renamed. This ADR records the
corrected, narrower definition.

## Decision

**We will:**

### D1 — `NeXaIdentity` is a small, stable, immutable root — nothing else

`NeXaIdentity` holds only facts that answer "what/who is NeXa, invariantly,
across every user, device, and conversation": `identity_id`, `name`,
`product_name`, `purpose`, `principles`, `identity_schema_version`. It is a
frozen dataclass loaded once from versioned config.

It explicitly does **not** hold: user preferences, relationship state,
memories, projects/goals, learned facts, communication-style adaptation,
device state, or session/temporary mood/context. Those are the concern of
separate, future NeXa Core subsystems (User Model, Relationship, Memory,
Context, Goals/Projects, Personality-as-adaptation, Capabilities,
Permissions, Devices, Learning) — each gets its own owner module when it is
actually built, never a field bolted onto `NeXaIdentity`.

### D2 — Persona stays the Conversation Style Layer, not Identity

`nexa.config.PersonaConfig` / `configs/personas/nexa_persona_v1.json` remain
exactly what their own file already documents: "the minimal
conversation-behavior layer only." Persona controls *how* NeXa speaks
(tone, brevity, language-mirroring); Identity states *what NeXa is*. Neither
replaces the other. `bootstrap.build_default_session()` composes both into
one `system_prompt` (identity instruction first, persona second) — additive,
not a replacement of the existing persona wiring.

### D3 — Persistence: immutable config in JSON now; one shared store for future mutable state

Versioned, product-level, effectively-immutable config (like Identity)
continues to live in tracked, versioned JSON files under `configs/`, per the
existing `PersonaConfig` precedent — this needs no database.

Future *mutable* Core state (memory, user model, relationship, preferences,
goals, learning, capability/permission/device state) will **not** each get
their own independent ad hoc JSON store. They will share one local
persistence boundary behind a `CoreStore` interface (`nexa.core.storage`),
with a concrete `SQLiteCoreStore` implementation added only when the first
mutable subsystem that actually needs it is built (M3.2 Memory). M3.1 adds
only the interface contract (`nexa.core.storage.contracts`) — no schema, no
implementation — so the shape exists before the first consumer, without
speculative building ahead of real need.

### D4 — Target package structure

```
src/nexa/core/
  __init__.py
  identity/
    __init__.py
    model.py     # NeXaIdentity (frozen dataclass)
    loader.py    # load_identity() — fail-loud on missing/invalid config
    render.py    # render_identity_instruction() — pure function
  storage/
    __init__.py
    contracts.py # CoreStore interface only (Protocol/ABC), no implementation
  # user/, memory/, context/, personality/, relationship/, capabilities/,
  # permissions/, devices/, learning/ are added one at a time, each when
  # its own milestone (M3.2+) actually needs it — never created empty
  # ahead of time just to look complete.
```

### D5 — No `nexa.core` god-object

There is no `NeXaCore` class that aggregates every subsystem into one
object. Each subsystem is imported and used directly by whatever composes
them (today: `bootstrap.py`; later: a dedicated Core composition point if
one becomes genuinely necessary). This mirrors D5 of ADR-0002 / the
"one canonical authority per responsibility" rule in `AGENTS.md`.

## Options considered

### Option A (chosen) — Narrow immutable Identity + separate future mutable subsystems behind a shared store interface
- Pros: matches ADR-0001's own anti-god-object principle; each subsystem
  independently testable/reviewable; persistence direction decided once,
  consistently, before multiple ad hoc stores accrete.
- Cons: more files/modules up front than one big `NeXaState` class; requires
  discipline to keep resisting scope creep onto `NeXaIdentity` as new
  requirements appear.

### Option B — Broad `NeXaIdentity`/`NeXaCore` holding all Core state
- Pros: fewer types to wire together initially.
- Cons: recreates the exact "one growing brain object" failure mode
  ADR-0001 diagnosed in the legacy repo; couples unrelated concerns (a
  memory bug fix would risk touching identity-loading code); explicitly
  rejected by the product owner during M3 planning.

### Option C — Do nothing (keep Identity implicit in the persona prompt only)
- Pros: zero new code.
- Cons: does not answer the actual M3 requirement — NeXa needs a
  stable identity independent of persona A/B changes and independent of
  which cloud/local provider is active; also blocks M3.2+ (Memory, User
  Model) from having anywhere principled to live.

## Consequences

- Positive: Identity is trivially testable (pure load + pure render, no
  side effects beyond one file read); persona and identity can now change
  independently without risk of conflating "how NeXa talks" with "what NeXa
  is"; the persistence direction for M3.2+ is decided once, not
  re-litigated per subsystem.
- Negative / costs: one more package (`nexa.core`) and a few more files than
  a single-file approach; the `CoreStore` interface is speculative until
  M3.2 gives it a real implementation and first caller.
- Follow-up work this creates: M3.2 Memory Foundation is the first consumer
  of `nexa.core.storage` and should implement `SQLiteCoreStore` against the
  `CoreStore` contract defined here, not invent a second contract.
- What this constrains for future milestones: no future subsystem may add
  fields to `NeXaIdentity` to avoid building its own module — a new kind of
  state requiring a new owner module is the correct response, not identity
  scope creep.

## Compliance / review

Any PR that adds a field to `NeXaIdentity` other than the six named in D1
should be rejected in review unless this ADR is amended first. Revisit this
decision if M3.2+ implementation reveals the shared-`CoreStore` design
doesn't fit a subsystem's real access pattern (e.g. a subsystem genuinely
needs a different persistence technology) — evidence from that
implementation, not speculation now, is what should drive a change.
