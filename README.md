# NeXa

NeXa is one personal AI system — a companion and platform that belongs to a single
user and follows that user across the devices they own.

> **Status: M0 — Foundation.** This repository currently contains project
> structure, documentation, and engineering conventions only. No product features
> (conversation, voice, memory, robot control, UI, model routing) are implemented
> yet. Everything below marked as *vision* describes intended direction, not
> shipped behaviour.

---

## What NeXa is (vision)

NeXa is **not** only a chatbot, only a robot, only Raspberry Pi software, only a
voice assistant, only a UI, only a single LLM, and not only a multi-agent system.

One user's NeXa should eventually exist across:

- robot
- PC / laptop
- phone
- tablet
- web
- later: smart home, car, wearables, other devices

Those devices are **bodies / interfaces** of the same NeXa. The same identity,
memory, context, and behaviour should follow the user across them.

### Three first-class interaction modes (future)

1. Natural voice conversation
2. NeXa Chat — typed, ChatGPT / Claude-style conversations
3. Touch / mouse / keyboard UI

None of these are implemented during M0.

---

## Architectural direction

NeXa is being built to remain:

- **local-first** — the system runs and is useful without the cloud
- **privacy-first** — the user is not the product
- **user-owned-data-first** — the user owns their identity, memory, and context
- **provider-independent** — models and services are replaceable abstractions
- **device-independent** — no single device defines the architecture
- **modular, testable, observable, documented**

The initial Raspberry Pi robot is an important first physical body, but it does
**not** define the whole architecture.

---

## Current development status

| Area | Status |
|---|---|
| Repository foundation | In place (M0) |
| Documentation system (state, roadmap, ADRs, reports) | In place (M0) |
| Natural text conversation | Not implemented |
| Realtime voice | Not implemented |
| Memory / context | Not implemented |
| Device awareness / capabilities | Not implemented |
| Robot / hardware adapters | Not implemented |
| UI / apps | Not implemented |

**Current milestone:** M0 — Foundation
**Next milestone:** M1 — Natural Text Conversation (not started)

See [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) for the authoritative,
operational snapshot.

---

## This repository vs. the legacy repository

- **Canonical repository:** `AndrewDul/NeXa` — this repository. Local workspace
  folder: `NeXa_IkiGai`.
- **Legacy / reference only:** `/home/devdul/Projects/smart-desk-ai-assistant`
  (`AndrewDul/smart-desk-ai-assistant`). It is a knowledge base of prior work on
  Raspberry Pi hardware, the voice pipeline, and multi-agent experiments. It is
  **not** the architecture authority and must not be modified or bulk-migrated.

See [`docs/legacy/LEGACY_NEXA_INDEX.md`](docs/legacy/LEGACY_NEXA_INDEX.md).

---

## Where the important docs live

| Document | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Operating manual for every coding agent working here |
| [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) | Short operational truth: what works, what doesn't, next task |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Staged milestones |
| [`docs/architecture/FOUNDATION_ARCHITECTURE.md`](docs/architecture/FOUNDATION_ARCHITECTURE.md) | Conceptual boundaries |
| [`docs/decisions/`](docs/decisions/) | Architecture Decision Records (ADRs) |
| [`docs/reports/`](docs/reports/) | Sequential task reports (`R####`) |
| [`docs/testing/TEST_STRATEGY.md`](docs/testing/TEST_STRATEGY.md) | Testing philosophy |
| [`docs/research/RESEARCH_POLICY.md`](docs/research/RESEARCH_POLICY.md) | How technology choices are researched |
| [`docs/troubleshooting/README.md`](docs/troubleshooting/README.md) | Troubleshooting record format |

---

## Naming

- Product / system: **NeXa** (also referred to as *NeXa IkiGai* / *NeXa system* —
  the same central project).
- GitHub repository: **AndrewDul/NeXa**
- Local workspace: **NeXa_IkiGai**

There is no separate architectural product called "NeXa Core".
