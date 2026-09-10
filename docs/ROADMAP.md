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

**M2.6 — CLOUD REALTIME VOICE.** Research / architecture + Phase-0 fact
corrections **COMPLETE** (`R0030`, 2026-09-10; no `src/` change, no tracked-
dependency change). Initial provider **frozen for v1**:
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
Silero as the turn authority, Gemini server VAD off. Verified facts
(official docs, 2026-09-10): 16-kHz PCM in / 24-kHz out, **20–40 ms audio
chunks**, ~10-min connection lifetime, **session-resumption tokens valid
2 h**, `GoAway.timeLeft`, `contextWindowCompression` → unlimited session;
paid pricing input $0.75/1M text · $3.00/1M audio, output $4.50/1M text ·
$12.00/1M audio, free tier free. **EXTERNAL REPORTED RISK (community, not
Google-confirmed):** one forum thread reports `gemini-3.1-flash-live-preview`
native audio speaking Polish with a strong EN/US accent — the M2.6A
operator test is the authoritative check. **Data terms:** in EEA/CH/UK the
paid data terms apply to unpaid quota too, and a cloud voice made
available to such users must use Paid Services; outside those regions
unpaid-quota data is used to improve Google products.

- **M2.6A** — minimal Gemini Live real-hardware spike: reSpeaker →
  existing AEC/local audio path → Gemini Live → native streamed cloud
  audio → existing speaker + metrics. No memory, identity, router, tools
  or GUI. Attempt #1 = infra bug (missing `LLMRunFrame` kickoff), fixed.
  **PASS / OPERATOR-CONFIRMED (2026-09-10, `R0031`); VOICE `Sulafat` =
  OPERATOR-CONFIRMED** (3 real sessions; attempt #2 default voice judged
  EXCELLENT / "mega super", attempt #3 with `Sulafat` → *"I like this
  voice, we keep it."*). Machine (turn-local): EOT→first-audible
  ≈ 0.75–0.81 s median (R0030 ≤ 1.5 s — PASS), barge-in ≈ 2 ms local
  playback-stop (R0030 ≤ 100 ms — PASS), AEC 0 failures, #5465
  startup-window only. "1.93 s / 19.84 s outliers" = metric artifacts
  (fragmented speech + cross-turn pairing), fixed by turn-local
  reconstruction. **C6 (turn-local):** RAW input transcription precedes
  first response audio 3/3 valid turns (~0.5 s), PUSHED 2/3 — but timing
  headroom ≠ steerability (`activity_end` already closed the turn) →
  ADR-0004 defaults to Gemini native language mirroring (Option A),
  delayed-`activity_end` + local language-ID as measured Option B.
  Reconnect/lifetime testing deferred to M2.6B. **Three sessions cost
  ≈ 7 cents.**
- **ADR-0004** — Cloud Realtime Voice provider boundary — **WRITTEN /
  Accepted (2026-09-10)**
  (`docs/decisions/ADR-0004_cloud_realtime_voice_provider_boundary.md`).
  Encodes the canonical rule (NeXa is the persistent system; Gemini Live is
  a replaceable realtime voice provider; a cloud provider must never become
  a second NeXa brain) and decides A–P: one `ConversationSession` authority
  for local + cloud; a new NeXa-owned `RealtimeVoiceProvider` boundary — a
  **peer of** `ModelProvider`, not a subtype (speech-to-speech session ≠
  text stream) — in a provider-agnostic `src/nexa/realtime/` package;
  `GeminiLiveProvider` wraps Pipecat 1.8.1 `GeminiLiveLLMService`
  (Option C); `CloudContextSnapshot` (privacy allow-list: minimal role
  card + language preference + last ~12 turns + policy state, fresh per
  non-resumed session; never full history / memory / persona / creds / raw
  audio); persisted NeXa-owned `ConversationPolicy` (`LOCAL_ONLY`
  **default**, `CLOUD_PREFERRED`, `AUTO` provisional — no classifier in
  M2.6B) vs runtime `active_provider` (`LOCAL`/`CLOUD`), NeXa's
  `ConversationRouter` executes every switch; language routing **Option A**
  default (Gemini native same-turn mirroring; NeXa owns the *preference*
  permanently), Option B (delayed `activity_end` + local language-ID,
  ≈ +1.1 s) is the measured fallback, Option C (parallel whisper.cpp)
  rejected unless A & B fail; voice = provider-agnostic NeXa user
  preference → `Sulafat`; Pipecat #5465 handled by a NeXa-owned bounded
  inbound audio buffer at the boundary (not an upstream fork);
  `ReconnectController` (proactive ~8-min age-timer + `GoAway` + latest
  resumption handle + fresh snapshot on resumption failure + backoff);
  explicit failure→fallback table (no silent policy-violating fallback;
  `LOCAL_ONLY` never leaves local; others fall back to `LOCAL` with the
  user informed; continuity survives); credentials = XDG secret file
  `~/.config/nexa/secrets/gemini.env` (700/600) + env-var-first loader +
  `CredentialSource` seam, paid-tier key required for `CLOUD_PREFERRED`/
  `AUTO` (EEA/CH/UK Paid-Services terms — stated as terms, not a legal
  opinion); `google-genai>=2.22,<3` + `websockets>=15,<17` as an
  **optional `cloud-gemini` extra** (`pip install .` stays Google-free;
  `src/nexa/realtime/gemini/` imports `google.genai` lazily;
  `pipecat-ai[local]==1.8.1` unchanged); `ProviderUsageEvent` from
  authoritative `usageMetadata`; Gemini function calling never the
  capability authority (event types defined, OFF in v1); Pipecat owns
  media/WS mechanics only. The ADR carries the ordered M2.6B
  implementation plan (15 components) and 17 measurable M2.6B acceptance
  gates. No `src/` / `tests/` / dependency-file change in the ADR task.
- **M2.6B** (next task) — production `RealtimeVoiceProvider` +
  `GeminiLiveProvider` + `ConversationRouter` + `ConversationPolicy` +
  `SetConversationPolicy` + `CloudContextSnapshot` + inbound #5465 buffer +
  `ReconnectController` + usage telemetry + cloud credential loader +
  provider voice/language preference, per the ADR-0004 plan and gates.
  Local voice stays byte-for-byte frozen; real-hardware operator
  acceptance required before COMPLETE. NOT STARTED.

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
