# ADR-0001 — NeXa project foundation

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Andrzej Dul (owner), Claude Code (agent)
- **Related:** `docs/reports/R0001_project_foundation_20260831.md`,
  `docs/architecture/FOUNDATION_ARCHITECTURE.md`, `docs/ROADMAP.md`,
  `docs/legacy/LEGACY_NEXA_INDEX.md`

This is a single foundational ADR covering four tightly coupled decisions. They
share one context and one set of consequences, so per `AGENTS.md` §6 they are
recorded together rather than as four artificial files.

---

## Context

`VERIFIED FACT` — A prior system exists at
`/home/devdul/Projects/smart-desk-ai-assistant`
(`AndrewDul/smart-desk-ai-assistant`): 630 commits, 2026-03-31 → 2026-08-24,
branch `main`. It is a Raspberry Pi 5 robot / smart-desk assistant runtime in
Python with a voice pipeline (openWakeWord, Silero VAD, Vosk PL/EN, Faster-Whisper
/ whisper.cpp, Piper TTS), a Godot "Visual Shell" UI, camera/vision, pan-tilt and
mobile-base drivers, a `llama.cpp` build, and a large multi-agent-system (MAS)
experiment under `modules/nexa_agents/` (~50 agent directories) with reports
`R0`–`R26+`. Its most recent commits describe retiring competing supervisors and
consolidating a "canonical MAS runtime".

`VERIFIED FACT` — The new repository `AndrewDul/NeXa` (local `NeXa_IkiGai`) began
with one commit and a 6-byte `README.md`.

`OBSERVATION` — The legacy repo's own architecture-decision notes
(`docs/NeXa_Architecture_Decisions/`, `ADR_001_core_brain_replaces_legacy_runtime`
and a series of "core brain", "model router", "conversation authority", "multi
agent roles" documents) show repeated cycles of replacing one central runtime with
another and of parallel supervisors accreting over time.

`INFERENCE` — The legacy codebase is a valuable knowledge base but carries
architectural debt (multiple brains / supervisors / answer paths, Pi-shaped
assumptions baked deep, provider coupling) that would be expensive to untangle
in place.

`ASSUMPTION` — The product intent stated in the M0 task prompt is authoritative:
NeXa is one personal AI system that spans many devices as bodies of the same
system, with central identity/memory/context, and must be local-first,
privacy-first, user-owned-data-first, modular, and provider/device-independent.

---

## Decision

**We will:**

### D1 — Start a new canonical repository rather than evolve the legacy one
`AndrewDul/NeXa` is the single architecture authority going forward. The legacy
repository is reference / knowledge base only: read-only, never modified, never
bulk-migrated. Reuse of legacy code is deliberate, file-scoped, and re-verified
against this repo's principles (see `docs/legacy/LEGACY_NEXA_INDEX.md`).

### D2 — Adopt local-first / privacy-first / user-owned-data as non-negotiable
The system must run and be useful without the cloud. The user owns their
identity, memory, and context. Data leaving a device is a policy decision, not a
default. Online services receive only the minimum required context.

### D3 — Treat NeXa as one system across many devices; devices are bodies
Identity, memory, and context are central and persistent, not per-device. Devices
(robot, PC, phone, tablet, web, later smart home / car / wearables) attach as
interchangeable bodies/interfaces at the Interaction / Device Awareness /
Capabilities / Embodiment boundaries. The Raspberry Pi robot is the first
physical body and must not define the architecture.

### D4 — Make model access a provider abstraction from the start
Models are replaceable providers selected by configuration. No provider name
appears in core control flow. There is one canonical authority for producing a
response; no silent fallback brains, no duplicate supervisors, no hidden
alternate answer paths. MAS may later serve complex reasoning but must not sit in
every simple conversation turn.

---

## Options considered

### For D1: new repo vs. refactor legacy in place vs. fork legacy
- **New repo (chosen).** Pros: clean architecture authority; no inherited debt;
  legacy stays intact as reference; principles enforced from commit one. Cons:
  re-implementation cost; risk of losing hard-won practical knowledge (mitigated
  by the legacy index and deliberate reuse).
- **Refactor legacy in place.** Pros: keeps working code. Cons: must modify the
  legacy repo (explicitly forbidden); multiple-brain debt is structural; history
  and tooling assume the Pi robot.
- **Fork legacy.** Pros: keeps history. Cons: inherits the debt and the
  Pi-shaped assumptions wholesale; the temptation to "just keep it working"
  defeats the point.

### For D2: local-first vs. cloud-first vs. hybrid-default
- **Local-first (chosen).** Matches the product intent and privacy stance; higher
  engineering cost on constrained hardware, accepted.
- **Cloud-first / hybrid-default.** Cheaper capability per unit effort; violates
  privacy-first and user-owned-data; makes the user the product. Rejected.

### For D3: one central system vs. per-device assistants that sync
- **One central system (chosen).** Enables "same NeXa follows the user"; needs a
  coordination layer later (M4+).
- **Independent per-device assistants with sync.** Simpler per device; produces
  divergent identities/behaviour and a hard merge problem. Rejected as the
  primary model.

### For D4: provider abstraction vs. pick one model/provider now
- **Provider abstraction (chosen).** Keeps the system provider-independent and
  testable; small upfront interface cost in M1.
- **Hard-code a model/provider.** Fastest to a demo; recreates the exact coupling
  the legacy repo is being left behind for. Rejected.

---

## Consequences

**Positive**
- One clear architecture authority; principles enforceable from the start.
- Legacy knowledge preserved without legacy debt.
- Provider/device independence and the "one canonical authority" rule are
  structural, not aspirational.

**Negative / costs**
- Proven functionality (voice pipeline, drivers, UI) must be re-earned milestone
  by milestone.
- Deliberate-reuse discipline is slower than copy-paste and must be upheld.
- A multi-device coordination layer is now owed (M4+).

**Follow-up work this creates**
- M1 ADR for the model-provider abstraction boundary and the single
  text-conversation turn path.
- Later ADRs for: realtime voice transport boundary (M2); context vs. history vs.
  memory split (M3); Device Awareness + Capability Registry (M4); long-term
  memory store (M5); multi-device coordination.

**Constraints on future milestones**
- No milestone may introduce a second "brain" or answer path without a superseding
  ADR.
- No milestone may hard-code a model provider into core control flow.
- No milestone may assume the Raspberry Pi robot as the only body.

---

## Compliance / review

- Every ADR and report states whether it upholds D1–D4.
- Code review checks: single response authority, no provider names in core flow,
  no per-device identity/memory forks.
- Revisit if evidence shows local-first cannot meet an essential capability bar on
  target hardware, or if the one-canonical-authority rule proves to block a
  genuine need — in which case a new ADR supersedes the relevant part, with
  evidence.
