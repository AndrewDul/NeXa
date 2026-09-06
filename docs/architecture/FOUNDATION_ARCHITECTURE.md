# FOUNDATION ARCHITECTURE

Status: **M0 — conceptual only.**

This document names the conceptual boundaries of NeXa so that future work has a
shared vocabulary. **These are conceptual boundaries, not Python packages, not
interfaces, and not code.** Nothing here is implemented. Do not create modules or
abstractions for these areas until a milestone requires them (see
`docs/ROADMAP.md`) and, for anything load-bearing, an ADR records the decision.

Truth labels (`VERIFIED FACT`, `INFERENCE`, `ASSUMPTION`, `PROPOSAL`, …) are
defined in `AGENTS.md` §4. Everything in this document is `PROPOSAL` unless
marked otherwise.

---

## 1. The one-sentence shape

NeXa is one user-owned system whose **identity, memory, and context** are central
and persistent, while **devices are interchangeable bodies/interfaces** that
present interaction modes and expose capabilities.

```
                       ┌───────────────────────────────────────┐
                       │        NeXa (the one system)          │
                       │  identity · memory · context · policy │
                       └───────────────────────────────────────┘
                                        │
             ┌──────────────────────────┼──────────────────────────┐
          device A                   device B                   device C
        (robot body)              (PC / laptop)              (phone / web)
     interaction modes +        interaction modes +        interaction modes +
        capabilities               capabilities               capabilities
```

`INFERENCE`: keeping identity/memory/context central (not per-device) is what
makes "the same NeXa follows the user" achievable. This is a direction, not a
built mechanism.

---

## 2. Conceptual boundaries

Each boundary is a *responsibility*, with one eventual canonical authority.

### Interaction
How a human engages NeXa in a given moment. Three first-class modes (future):
natural voice conversation, NeXa Chat (typed), and touch/mouse/keyboard UI. A
mode is a front-end onto the Conversation boundary, not its own brain.

### Conversation
The single canonical authority that turns an incoming user turn into a response.
No parallel "final answer" paths. Simple turns do not require MAS.

> M1.1 made this boundary real (the minimal text-conversation path only): see
> `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`. Everything else
> in this document remains conceptual.

### Context
Transient, bounded, per-session working state that a turn needs. Distinct from
chat history and from long-term memory.

### Memory
User-owned, long-lived knowledge NeXa retains about the user and the world.
**Separate system from chat history.** Explicit write and retrieve paths.

> Chat history (the transcript of past conversations) is a *third* thing, distinct
> from both Context and Memory.

### Model Providers
Models are replaceable provider abstractions. Local-first default. No provider
name in core control flow. Online providers, if used, receive only the minimum
required context.

> M1.1 made this boundary real for local providers only (Ollama + a
> `llama-server` adapter): see
> `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §3, §8. Online
> providers remain conceptual.

### Device Awareness
Answers: *"What hardware / body do I currently have right now?"* (sensors,
actuators, displays, audio, compute, connectivity).

### Capabilities
Answers: *"What can I actually do, given this hardware, these permissions, and
this runtime state?"* Capabilities are derived, not hard-coded per device.

### Tools
Structured actions NeXa can take in the world (beyond producing text). Gated by
Capabilities and by policy/consent.

### Voice
STT, TTS, wake, endpointing, barge-in. The realtime transport/framework is an
internal, replaceable boundary.

> `ADR-0003` (Accepted, 2026-09-05) decided this boundary's M2 architecture
> (Pipecat orchestration, Silero VAD, whisper.cpp STT, Piper TTS as a
> temporary baseline, full barge-in as the target) — see
> `docs/decisions/ADR-0003_realtime_voice_foundation.md`. **M2.1 (local audio
> transport + VAD only) and M2.2 (local whisper.cpp STT) are now real** —
> see `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` and
> `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md`, the same way
> `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` made Conversation
> real. Everything else in this Voice boundary (the `ConversationSession`
> adapter, TTS, barge-in) remains conceptual until M2.3+ implements it.

### Vision
Perception from cameras/sensors. Feeds Context and Capabilities.

### Embodiment
Mapping NeXa intent onto a physical body (robot: motion, pan-tilt, base). The
Raspberry Pi robot is the first body, not the definition of the system.

### UI
Visual presentation and direct-manipulation input across screens (robot display,
desktop, web, mobile).

### Privacy / Vault
User-owned data store and the policy layer deciding what may leave the device,
to whom, and with how much context. Enforces privacy-first / local-first.

### Multi-device coordination
Keeps identity, memory, and context coherent as the user moves between bodies.
Handles presence, hand-off, and conflict resolution.

---

## 3. Cross-cutting principles (apply to every boundary)

- **One canonical authority per responsibility.**
- **No silent fallback brains; no duplicate supervisors; no hidden answer paths.**
- **Provider independence** for models, and by extension STT/TTS/transport.
- **Local-first, privacy-first, user-owned-data-first.**
- **Device-independent core**; devices attach at Interaction / Device Awareness /
  Capabilities / Embodiment.
- **Observable and testable** at each boundary.
- **Minimum structure for the current milestone**; no speculative modules.

---

## 4. Explicit non-goals for M0

No code for any boundary above. No package layout beyond the placeholder
`src/nexa`. No interface definitions. No wiring diagrams that imply
implementation. This document is a map of intent that later milestones and ADRs
will make concrete one boundary at a time.
