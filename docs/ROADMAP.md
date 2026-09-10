# ROADMAP

Staged milestones for NeXa. This roadmap is **allowed to evolve** through ADRs
and evidence. Distant milestones are deliberately under-specified — do not treat
them as final design.

Only one milestone is active at a time. See `docs/CURRENT_STATE.md` for which.

---

## M0 — Foundation  ✅ (current)

Repository structure, documentation system, engineering conventions, legacy
relationship. No product features.

**Exit criteria:** foundation files exist and are internally consistent;
`AGENTS.md` is usable by a fresh agent; foundation tests pass; `CURRENT_STATE.md`
matches reality; `R0001` written; one coherent commit.

---

## M1 — Natural Text Conversation

A single canonical typed-conversation turn path (NeXa Chat style). One authority
for producing a reply. A **provider abstraction** for models (local-first
default; online providers optional and given minimum context). No MAS in the
simple turn. Chat history handling is explicitly *not* long-term memory.

**Rough exit criteria:** one documented turn path; provider abstraction with at
least one local provider; deterministic tests around the turn contract;
conversation-quality benchmark IDs defined (not necessarily all passing).

---

## M2 — Realtime Voice

Voice as an interaction mode over the M1 conversation core. Transport / framework
must remain replaceable behind an internal boundary. STT and TTS are provider
abstractions.

**M2 — LOCAL REALTIME VOICE: COMPLETE / OPERATOR-CONFIRMED (2026-09-10).**
M2.1 Pipecat + local audio + Silero VAD → M2.2 whisper.cpp STT → M2.3 voice →
`ConversationSession` adapter → M2.4 streaming Piper TTS → M2.4B natural speech
flow → M2.5A barge-in feasibility (`R0028`) → **M2.5B production barge-in /
interruption (`R0029`)**. Accepted local voice baseline: audio in → Pipecat local
transport → Silero VAD → whisper.cpp `base/q8_0` → bilingual PL/EN guard →
`ConversationSession` → `ProviderWindow` (`keep_entries=0`) → `gemma4:e4b` via
Ollama → `NexaSpeechPlanner` → Piper → audio out. Production barge-in: XVF3800 AEC
far-end reference, sustained-VAD confirmation, ~251 ms Ollama cancellation,
interruption capture/coalescing, capture-generation-scoped timers, correct
interrupted-history semantics, PL/EN routing preserved.

**M2.6 — CLOUD REALTIME VOICE.** Research / architecture **COMPLETE** (`R0030`,
2026-09-10; research-only, no `src/` change). Initial provider **frozen for v1**:
Google Gemini Live `gemini-3.1-flash-live-preview` (VERIFIED real + current;
Preview; native audio-to-audio; synchronous-only function calling; no prompt
caching). Integration = Pipecat **OPTION C** (wrap + harden the installed 1.8.1
`GeminiLiveLLMService`; known gaps: GitHub #5465 silent reconnect-window drops,
no `GoAway` handling). Architecture principle: **NeXa remains the single
authority; cloud and local are replaceable conversation providers, never NeXa's
identity.** Cloud is a NEW `RealtimeVoiceProvider` boundary (not a
`ModelProvider`) fed a NeXa-derived, privacy-filtered `CloudContextSnapshot`;
canonical transcript stays in `ConversationSession`; raw cloud audio not
retained; minimal cloud system instruction (role card, not identity). Modes:
`ConversationPolicy` = AUTO / LOCAL_ONLY / CLOUD_PREFERRED (NeXa-owned,
persisted), distinct from runtime `active_provider` = LOCAL / CLOUD. Switchable
by natural voice command ("Przełącz na chmurę.", "Rozmawiaj lokalnie.", "Używaj
najlepszego trybu.") — a model/provider may recognise the intent, **NeXa's own
router/core executes the switch**. HYBRID audio: keep the XVF3800 AEC + local
Silero as the turn authority, Gemini server VAD off. **Known provider RISK:**
`gemini-3.1-flash-live-preview` native audio currently speaks Polish with a
strong EN/US accent (Google-acknowledged 2026-08-18, unresolved). Real use
requires a paid-tier key (free-tier data is used for product improvement).

- **M2.6A** (next implementation task) — minimal Gemini Live real-hardware
  spike: reSpeaker → existing AEC/local audio path → Gemini Live → native
  streamed cloud audio → existing speaker, plus latency / Polish-quality /
  reconnect / cost metrics (PASS/WARN/FAIL table in `R0030`). No memory,
  identity, router, tools or GUI. → report `R0031`.
- **ADR-0004** — ratify the provider boundary, the cloud credential surface,
  and the `pipecat-ai[google]` / `google-genai` dependency. Owed **before**
  M2.6B.
- **M2.6B** — production `RealtimeVoiceProvider` + `ConversationRouter` +
  `SetConversationPolicy` + `CloudContextSnapshot` + reconnect hardening.

**Then, after local + cloud voice are both complete, in order:** memory / identity
/ personality / capabilities → full graphical UI → typed chat in that UI using the
**same** `ConversationSession` / NeXa brain as voice. There must never be separate
voice-NeXa and chat-NeXa brains.

---

## M3 — Robust Context

Turn-to-turn and session context that is coherent, bounded, and observable.
Clear separation between transient context, chat history, and long-term memory.

---

## M4 — Device Awareness + Capability Registry

- **Device Awareness:** "What hardware / body do I currently have?"
- **Capability Registry:** "What can I actually do with this hardware, these
  permissions, and this runtime state?"

Foundational for multi-device and for the robot body.

---

## M5 — Long-Term Memory

User-owned long-term memory as a system distinct from chat history. Local-first
storage. Explicit write / retrieve paths.

---

## Later (unordered, not designed yet)

- Personal Vault (privacy-preserving user data store)
- Tools / actions framework
- Reasoning / router (including opt-in MAS for complex reasoning)
- Vision
- Robot adapter (Raspberry Pi first physical body)
- Multi-device runtime + identity/memory/context sync
- Distributed trusted compute
- Mobile apps (phone / tablet)
- Richer UI (touch / mouse / keyboard) and web

---

## Principles that constrain every milestone

Local-first, privacy-first, user-owned-data-first, modular, provider-independent,
device-independent, testable, observable, documented. One canonical authority per
responsibility. No silent fallback brains. No duplicate supervisors or hidden
answer paths. See `AGENTS.md` §3.
