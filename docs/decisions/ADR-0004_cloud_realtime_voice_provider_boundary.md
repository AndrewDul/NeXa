# ADR-0004 — Cloud Realtime Voice provider boundary (M2.6)

- **Status:** Accepted (provider boundary, canonical-authority rule,
  `CloudContextSnapshot` contract, policy/provider split, language-routing
  authority, reconnect/failure semantics, credential + dependency policy,
  M2.6B plan and acceptance gates). The Gemini Live model, voice, and audio
  parameters are **the M2.6A-frozen v1 baseline, not permanently frozen** —
  they are replaceable behind the boundary this ADR defines.
  **Amended by [Amendment 1](#amendment-1--factual--api--terms-corrections-before-m26b-2026-09-10)
  (2026-09-10) — four pre-implementation factual / API / terms corrections
  (UK vs EEA and paid-services scope; `system_instruction` is immutable on
  an open Live connection; Gemini-3.1 initial-history seeding shape;
  session-resumption reconnect safety). No accepted decision is reversed;
  where Amendment 1 supersedes wording the affected section carries an
  inline pointer.**
- **Date:** 2026-09-10 (Amendment 1: 2026-09-10)
- **Deciders:** Andrzej Dul (owner / operator), Claude Sonnet 5 (research + drafting)
- **Related:**
  `docs/decisions/ADR-0002_text_conversation_foundation.md`
  (D1 one canonical turn path; D2 `ModelProvider` abstraction — the pattern
  this ADR extends, deliberately, to a *different* boundary shape),
  `docs/decisions/ADR-0003_realtime_voice_foundation.md`
  (D1 Pipecat is M2 orchestration infrastructure; D2 `ConversationSession`
  is the sole conversation authority — "no later M2 substage may introduce a
  second conversation history, persona, or model/provider decision point for
  voice"; D8 barge-in; D9 LiveKit deferred; D11 replaceable component
  interfaces),
  `docs/reports/R0030_cloud_realtime_voice_research_architecture_20260910.md`
  (M2.6 research + Phase-0 fact corrections),
  `docs/reports/R0031_m2_6a_gemini_live_hardware_spike_20260910.md`
  (M2.6A feasibility spike — PASS / OPERATOR-CONFIRMED; voice `Sulafat`
  OPERATOR-CONFIRMED),
  `docs/reports/R0028_*` / `docs/reports/R0029_*` (local barge-in baseline
  this ADR must not disturb),
  `docs/ROADMAP.md` (M2.6).

---

## Context

Truth labels follow `AGENTS.md` §4 (`VERIFIED FACT` / `OBSERVATION` /
`INFERENCE` / `ASSUMPTION` / `PROPOSAL` / `UNKNOWN`).

### Where NeXa is

- **`VERIFIED FACT`** — M2 **local** realtime voice is COMPLETE and FROZEN:
  M2.5B production barge-in / interruption is COMPLETE / OPERATOR-CONFIRMED
  (2026-09-10, `R0029`). Accepted local baseline: audio in → Pipecat local
  transport → Silero VAD → whisper.cpp `base/q8_0` → bilingual PL/EN guard →
  `ConversationSession` → `ProviderWindow` (`keep_entries=0`) → `gemma4:e4b`
  via Ollama → `NexaSpeechPlanner` → Piper → audio out. Production barge-in:
  XVF3800 hardware AEC far-end reference, sustained-VAD confirmation,
  ~251 ms Ollama cancellation, capture/coalescing, correct interrupted-history
  semantics. This ADR must not reopen, re-tune, or regress any of it.
- **`VERIFIED FACT`** — M2.6A cloud realtime voice feasibility: PASS /
  OPERATOR-CONFIRMED (2026-09-10, `R0031`). Three real operator sessions on
  the real Raspberry Pi + reSpeaker/XVF3800 path against Gemini Live
  `gemini-3.1-flash-live-preview`. Turn-local machine evidence: EOT → first
  audible ≈ **0.75–0.81 s median** (R0030 target ≤ 1.5 s — PASS, faster than
  the local path); barge-in ≈ **2 ms** local playback-stop (R0030 target
  ≤ 100 ms — PASS); local speaker silenced ~27 ms *before* the
  server-round-trip interruption frame; XVF3800 AEC **0 failures** in every
  session; Pipecat issue #5465 NOT_READY window observed at **startup only**,
  with no user speech in it. The "1.93 s / 19.84 s outliers" were metric
  artifacts (fragmented user speech + cross-turn event pairing), removed by
  turn-local reconstruction. Three sessions cost ≈ 7 cents.
- **`VERIFIED FACT`** — Voice `Sulafat` is OPERATOR-CONFIRMED as the current
  NeXa cloud voice baseline (operator, after a real natural conversation:
  *"I like this voice, we keep it."*).
- **`VERIFIED FACT`** — No M2.6 production cloud code exists in `src/nexa/**`.
  All M2.6 work so far is under `docs/research/m2_6_cloud_realtime_voice/`.
  `pyproject.toml` is unchanged (`dependencies = ["pipecat-ai[local]==1.8.1"]`,
  the only optional-dependency group is `dev`).

### Evidence carried in from R0030 / R0031 (re-verified against current
official sources on 2026-09-10 where noted)

- **`VERIFIED FACT`** (Google Gemini Live docs, ai.google.dev, checked
  2026-09-10) — `gemini-3.1-flash-live-preview` is real and currently listed
  (alongside `gemini-2.5-flash-native-audio-preview-12-2025`). Live API
  audio is always raw little-endian 16-bit PCM; **input natively 16 kHz,
  output always 24 kHz**. When automatic VAD is disabled, the client is
  responsible for sending `activityStart` / `activityEnd`. Native-audio
  Live models carry a 128k-token session context window; other Live models
  32k. Audio-only sessions are limited to 15 minutes.
- **`VERIFIED FACT`** (R0030 Phase-0, official docs 2026-09-10) — Live API
  connections have a ~10-minute WebSocket lifetime; **session-resumption
  handles are valid for 2 hours** after the last session ends;
  `GoAway` carries `timeLeft`; `contextWindowCompression` (sliding window)
  makes a session effectively unlimited in duration; function calling on the
  3.x models is **synchronous only**; there is **no prompt caching**;
  `send_client_content` on 3.x seeds context at start only. Paid pricing
  (per 1M tokens): input $0.75 text / $3.00 audio; output $4.50 text /
  $12.00 audio; free tier free of charge.
  *(Amendment 1 refines the initial-history-seeding mechanism — see
  [Amendment 1 §3](#3--gemini-31-initial-history-seeding).)*
- **`VERIFIED FACT`** (R0030 Phase-0, Google terms 2026-09-10) — data
  terms: outside the EEA / Switzerland / UK, unpaid-quota data is used to
  improve Google products; in the EEA / CH / UK the paid data terms apply to
  unpaid quota too, and a cloud voice *made available to* users in those
  regions must use Paid Services. This is a **terms** statement, not a legal
  opinion. *(Amendment 1 corrects the operator's region — United Kingdom,
  not EEA — and splits "data treatment" from "making API Clients available
  to users", and records that the Gemini API Free Tier is available in the
  UK. See [Amendment 1 §1](#1--uk--eea--paid-services-correction).)*
- **`OBSERVATION`** (community forum, not Google-confirmed) — one thread
  reports `gemini-3.1-flash-live-preview` native audio speaking Polish with
  a strong EN/US accent. R0031's operator sessions did **not** reproduce a
  problem (PL/EN both judged good), but this remains an unverified external
  risk to watch in M2.6B.

### Installed integration stack

- **`VERIFIED FACT`** — `pipecat-ai[local]==1.8.1` is the pinned, shipped
  dependency (ADR-0003 D1). It provides `GeminiLiveLLMService`,
  `LLMContext`, `LLMContextAggregatorPair(realtime_service_mode=True)`,
  `LLMRunFrame`, the local audio transport, and Silero VAD.
- **`VERIFIED FACT`** (GitHub, checked 2026-09-10) — Pipecat issue
  **#5465** ("`GeminiLiveLLMService` silently drops user audio / text /
  tool-results during the reconnect / not-ready window") is **OPEN**. The
  linked fix **PR #5497 is not merged**; no 1.8.x fix release. The installed
  `GeminiLiveLLMService` also has **no `GoAway` handling** (static audit,
  `inspect_pipecat_gemini_live.py`). Session-resumption plumbing *is*
  present.
- **`VERIFIED FACT`** — `google-genai 2.22.0` and `websockets 16.1.1` are
  installed in the research `.venv` only; `pyproject.toml` / lock files were
  never touched. Pipecat 1.8.1's own `[google]` extra constrains
  `google-genai>=1.68.0,<3`; `google-genai 2.22.0` pulled `websockets`
  17.1 → 16.1.1, still inside Pipecat's `websockets>=13.1`; `pip check`
  clean.

### NeXa architecture this ADR builds on (`VERIFIED FACT`, direct inspection)

- `src/nexa/conversation/session.py` — `ConversationSession` is a dataclass
  with a single write path: `send(user_text, …)` appends a user turn,
  renders a bounded provider view (`ProviderWindow` or `ConversationContext`),
  calls `provider.generate(...)`, streams tokens, appends the completed
  assistant turn. `_history` and `_response_languages` are kept
  index-aligned. `commit_interrupted_turn(spoken_text)` yields
  `ROLLED_BACK_USER_TURN` / `COMMITTED_SPOKEN_PREFIX` / `NOTHING_TO_COMMIT`
  and preserves the index alignment. `history` is a read-only tuple.
- `src/nexa/providers/base.py` — `ModelProvider` is an ABC:
  `describe() -> ProviderDescription`, `generate(messages, options, *,
  cancel_token) -> AsyncIterator[str]`. It is a **text-stream** contract:
  NeXa hands it messages, it yields assistant text. `ModelUnavailableError`
  "must never silently switch to a different model or provider."
- `src/nexa/conversation/provider_window.py` — `ProviderWindow` is the
  precedent for "a derived, bounded, provider-facing view over the complete
  canonical history": the provider sees `history[base:]`, `base` is stable
  between boundaries, the canonical `ConversationSession.history` stays
  complete. `CloudContextSnapshot` is the same idea, taken further (privacy
  filtering, not just size bounding).
- `src/nexa/conversation/response_language.py` — `ResponseLanguageResolver`
  is the existing separate authority for reply language: mirror the spoken
  language by default; a one-turn override affects only the current reply; a
  sticky command ("Od teraz mów po angielsku.", "Switch to English.") sets
  `ResponsePreference.sticky`. "The LLM is never the sole language-routing
  authority" (ADR-0003 Amendment 1).
- `src/nexa/config.py` — configuration is **env-var only** (`os.environ.get`),
  fail-closed (`NEXA_MODEL_PROVIDER` accepts only `local`, else raises).
- `src/nexa/voice/` — `VoiceRuntime`, `BargeInController`,
  `AecReferenceHealth`, `InterruptionStateMachine`; `src/nexa/voice_tts/` —
  `AecReferenceFeeder` (tees TTS PCM to `plug:respeaker` for the XVF3800
  far-end reference), `SpokenTextTracker` (spoken high-water mark). These are
  the local-voice mechanisms the HYBRID audio design reuses unchanged.
- `src/nexa/voice_conversation/adapter.py` — `VoiceConversationAdapter`
  bridges one M2.2 transcription stream into the one `ConversationSession`,
  serialising turns FIFO. It "never changes history/persona/model/language
  authority."

### The canonical rule this ADR exists to encode

> **NeXa is the persistent personal AI system. Gemini Live is a replaceable
> realtime voice / reasoning provider. Identity, canonical conversation
> state, long-term memory, user preferences, permissions, capabilities,
> device state, actions, and routing authority belong to NeXa. A cloud
> provider must never become a second NeXa brain.**

---

## Problem

M2.6A proved a cloud realtime voice provider is *feasible* and *good*. It did
**not** define how that provider attaches to NeXa without violating the
single-authority principle. The spike ran Gemini Live essentially
standalone: an empty `LLMContext`, Gemini's own transcription, Gemini's own
turn memory, no NeXa conversation history, no policy, no fallback, no
reconnect handling, a hard 15-minute cap, and a NOT_READY window that
*happened* not to eat user speech.

For production we must decide, once, and in a way that survives future
milestones (memory, identity, capabilities, GUI typed chat, multi-device):

1. What is the boundary type for a cloud speech-to-speech provider — it is
   **not** a `ModelProvider` (that is a text-stream contract), so what is it?
2. How does a cloud turn (user speech, cloud transcription, cloud spoken
   output, interruption, provider switch) commit into the **one**
   `ConversationSession` without creating a second history?
3. Who owns language routing when the cloud provider produces audio before
   NeXa has finished processing the user's transcription?
4. What derived context may leave the device, and what must never?
5. What happens on cloud failure, reconnect, GoAway, resumption failure,
   quota exhaustion, region/policy blocks — without a silent fallback that
   violates the user's stated policy?
6. How are cloud credentials and cloud dependencies handled so that a
   local-first, `LOCAL_ONLY` install still needs **no** Google cloud code or
   packages?
7. Where does Pipecat's authority end and NeXa's begin?

---

## Constraints

- **`ConversationSession` stays the sole conversation authority** (ADR-0003
  D2). No second history, persona, model/provider decision point, or answer
  path — for voice *or* for the cloud.
- **Local voice is frozen** (M2.5B / `R0029`). The XVF3800 AEC path, Silero
  turn authority, sustained-VAD barge-in, Ollama cancellation, interrupted-
  history semantics, PL/EN routing, `ProviderWindow` — none of it changes.
- **Local-first / privacy-first / user-owned-data-first** (`AGENTS.md` §3).
  Cloud is opt-in. `LOCAL_ONLY` must be provably airtight.
- **Provider-independent, device-independent, testable, observable,
  documented. One canonical authority per responsibility. No silent
  fallback brains.**
- **Config is env-var only, fail-closed** (`src/nexa/config.py` pattern).
- This ADR is **architecture only**. No `src/nexa/**` change, no `tests/**`
  change, no `pyproject.toml` change in this task. M2.6B is the
  implementation milestone and is **not** started here.

---

## Decision

**We will** attach cloud realtime voice to NeXa through a new NeXa-owned
boundary, `RealtimeVoiceProvider`, that is a **peer of** `ModelProvider`,
not a subtype of it. **We will** keep exactly one `ConversationSession` as
the canonical conversation authority for local and cloud alike; the cloud
provider is fed a derived, privacy-filtered `CloudContextSnapshot` and its
completed turns are committed back into that one session through a new
additive method. **We will** separate a persisted, NeXa-owned
`ConversationPolicy` (`AUTO` / `LOCAL_ONLY` / `CLOUD_PREFERRED`) from a
runtime `active_provider` (`LOCAL` / `CLOUD`), with NeXa — never the cloud
model — executing every provider switch. **We will** default production
language routing to Gemini's native same-turn language mirroring (Option A),
with NeXa retaining permanent ownership of the language preference and its
canonical metadata. **We will** put the Pipecat #5465 protection, GoAway /
age-timer reconnect, credential loading, usage telemetry, and fallback
policy in **NeXa-owned** code at the boundary, not in patched Pipecat
internals. **We will** ship `google-genai` as an **optional** dependency
extra so `LOCAL_ONLY` / local-first installs pull no Google cloud code.

The numbered decisions below correspond one-to-one to items A–P of the M2.6B
charter.

---

### Decision A — One canonical conversation authority (charter A) — Accepted

There is exactly one `ConversationSession` per NeXa conversation. Local and
cloud providers **do not** maintain separate canonical histories. The
Gemini Live session's internal turn memory is **temporary provider state**,
owned by the provider, discarded on disconnect/replacement, and never read
back as authority.

Canonical write events and where they land in the one session:

| Cloud event | Canonical commit |
|---|---|
| User utterance (from Gemini `inputAudioTranscription`, finalised) | user turn text — same slot a local STT transcript would fill |
| Assistant cloud spoken output (from Gemini `outputAudioTranscription`, finalised) | assistant turn text |
| Interruption (local barge-in confirmed) | assistant turn = **only the actually-spoken prefix** (spoken high-water mark), marked interrupted; user turn is **kept** (Gemini did hear it) — see the write-path section |
| Partial spoken response, no interruption, session lost mid-turn | assistant turn = spoken prefix if any audio played, else **no assistant turn**, user turn kept |
| Provider switch (LOCAL↔CLOUD) mid-conversation | the in-flight turn is committed (spoken prefix rule) before the switch; the next turn is produced by the new provider into the same session |

Canonical state (`ConversationSession._history` / `_response_languages`,
and later long-term memory / identity / preferences) **survives**: Gemini
reconnect, Gemini session replacement, LOCAL↔CLOUD switch, and app restart
(restart persistence is M3+, but the boundary is designed for it — nothing
canonical lives inside the provider).

**Forbids:** a `GeminiConversation` / cloud-side history object used as a
source of truth; reading Gemini's context back into NeXa; letting a
reconnect or a switch "start a new conversation".

---

### Decision B — The `RealtimeVoiceProvider` boundary (charter B) — Accepted

A new NeXa-owned abstraction, `RealtimeVoiceProvider` (ABC), living in a new
provider-agnostic package `src/nexa/realtime/` with **no cloud dependency**.
It is a **peer of `ModelProvider`, not a subtype** — `ModelProvider` is
`messages → AsyncIterator[str]` (a text turn); a realtime voice provider is
a **bidirectional speech-to-speech session** with its own lifecycle and
event stream. Forcing it into `ModelProvider` would misrepresent it and
push audio/lifecycle concerns into `ConversationSession`.

Minimum interface / lifecycle (names are `PROPOSAL`, to be finalised in
M2.6B against repo conventions):

**Lifecycle**
- `async start(snapshot: CloudContextSnapshot) -> None` — connect, run
  setup, seed context, become ready.
- `async stop(*, reason: str) -> None` — disconnect cleanly, release audio.
- `readiness` → `ProviderReadiness` (`CONNECTING` / `CONTEXT_INIT` /
  `READY` / `DEGRADED` / `RECONNECTING` / `FAILED`).

**User → provider**
- `send_user_audio(pcm: bytes)` — 16 kHz PCM frames from the (AEC-processed)
  mic.
- `user_turn_start()` / `user_turn_end()` — driven by NeXa's Silero turn
  authority (maps to Gemini `activityStart` / `activityEnd`; server VAD off).

**Provider → NeXa (event stream)**
- `assistant_audio(pcm: bytes)` — 24 kHz PCM to route to the speaker.
- `user_transcription(text, *, final: bool)` — for canonical commit +
  language metadata.
- `assistant_transcription(text, *, final: bool)` — for canonical commit.
- `interruption()` — provider acknowledged an interruption
  (`serverContent.interrupted`); informational — NeXa's local barge-in is
  the authority.
- `cancellation_complete()` — provider stopped generating after a NeXa
  cancel.
- `reconnecting(reason)` / `resumed(from_handle: bool)` — session
  continuity events.
- `usage(event: ProviderUsageEvent)` — authoritative usage metadata.
- `error(err: RealtimeProviderError)` / `failed(reason)` — recoverable vs
  terminal failure.
- `capabilities()` → `RealtimeProviderCapabilities` (supports interruption,
  supports resumption, supports server transcription, function-calling mode,
  input/output rates, max session seconds, language behaviour).

Pipecat implements the media plumbing **beneath** an implementation of this
interface — it does **not** own identity, memory, routing, policy, or
context selection. A future second provider (a different cloud vendor, a
local speech-to-speech model) is added by writing one more
`RealtimeVoiceProvider`; `ConversationSession` is not rewritten.

**Forbids:** `RealtimeVoiceProvider(ModelProvider)`; audio types leaking
into `src/nexa/conversation/`; a provider that requires `ConversationSession`
changes to add.

---

### Decision C — Gemini Live is the v1 `RealtimeVoiceProvider` implementation (charter C) — Accepted

> **Amendment 1 (2026-09-10)** adds the required Gemini-3.1 session
> start-up elements (configure initial-history support; setup /
> `system_instruction`; seed the bounded `CloudContextSnapshot` recent
> turns **once** via `clientContent`; begin realtime audio) and records
> that `system_instruction` **cannot be updated on an open connection**.
> Their exact composition/order against the M2.6A `LLMRunFrame` kickoff and
> installed Pipecat 1.8.1 / `google-genai` 2.22.0 source is an
> **M2.6B implementation-detail verification** — not assumed by this ADR.
> See [Amendment 1 §2](#2--system-instruction-cannot-change-mid-connection)
> and [§3](#3--gemini-31-initial-history-seeding). The Decision C baseline
> (model / voice / rates / VAD / AEC / barge-in) is unchanged.

`GeminiLiveProvider(RealtimeVoiceProvider)` in `src/nexa/realtime/gemini/`,
wrapping and hardening Pipecat 1.8.1's `GeminiLiveLLMService` (ADR-0003 D1;
R0030 "Option C"). **v1 baseline, unchanged from M2.6A** and **not**
permanently frozen:

- model `gemini-3.1-flash-live-preview`
- voice `Sulafat` (OPERATOR-CONFIRMED; see Decision G for how it is stored)
- 16 kHz PCM mic in / 24 kHz PCM out
- **server VAD OFF**; local **Silero** VAD is the turn authority
  (`activityStart` / `activityEnd` driven by NeXa)
- the existing **XVF3800 hardware AEC** path (mic already echo-controlled;
  `AecReferenceFeeder` tees the *cloud* 24 kHz output to `plug:respeaker`
  exactly as it tees Piper today)
- the existing **local speaker + barge-in authority** — NeXa can always stop
  the speaker locally, independent of any server round-trip (R0031: local
  stop preceded the server interruption frame by ~27 ms)

The M2.6A `LLMRunFrame` kickoff fix (empty `LLMContext`,
`inference_on_context_initialization=False`, one `LLMRunFrame` after the
socket connects) is a required part of the wrapper — without it the service
never becomes `_ready_for_realtime_input` and, with server VAD off, silently
drops everything.

**Forbids:** changing the accepted M2.6A voice UX (model, VAD, AEC, turn
authority, barge-in, latency knobs, audio format, chunking) as a side
effect of productionising.

---

### Decision D — `ConversationPolicy` is separate from `active_provider` (charter D) — Accepted

Two distinct concepts, both NeXa-owned:

- **`ConversationPolicy`** (persisted, user-owned intent):
  - `LOCAL_ONLY` — **default.** Never send conversation text, audio, or a
    `CloudContextSnapshot` to any cloud provider. The cloud code path is not
    entered.
  - `CLOUD_PREFERRED` — use cloud when it is available; on cloud failure,
    follow the explicit fallback in Decision J (switch to local, inform the
    user).
  - `AUTO` — NeXa chooses per turn/session from availability, privacy of the
    content, task type, connectivity, latency, cost, and capability needs.
    **The decision classifier is NOT implemented in M2.6B.** In M2.6B `AUTO`
    is accepted as a stored value and resolves *provisionally* to
    "`CLOUD_PREFERRED` semantics gated only on reachability + a satisfied
    `ProviderEligibilityPolicy` (Decision K, as amended) + a healthy
    connectivity check" — no privacy/task/cost classification yet — and this
    provisional behaviour is documented as such. `AUTO` is **not** the
    default.
- **`active_provider`** (runtime state): `LOCAL` | `CLOUD` (extensible).
  Which provider is currently producing turns.

Policy is set via config (`NEXA_CONVERSATION_POLICY`) and a NeXa-internal
`SetConversationPolicy` command. A voice command ("Przełącz na chmurę.",
"Rozmawiaj lokalnie.", "Używaj najlepszego trybu.") may be **recognised** as
an intent by a model, but **NeXa's own router executes the switch** — the
cloud model never changes its own routing.

**Forbids:** `CLOUD_ONLY` (there is no such policy — local is always the
floor); a model self-selecting the active provider; `AUTO` shipping a hidden
privacy classifier that was never designed.

---

### Decision E — `CloudContextSnapshot` contract (charter E) — Accepted

> **Amendment 1 (2026-09-10):** the snapshot contract is unchanged, but two
> mechanism details are corrected — (a) the `recent_turns` seed is delivered
> **once at session start** through the Gemini-3.1 initial-history mechanism
> (`clientContent` messages processed until `turnComplete`, no model call;
> `history_config.initial_history_in_client_content = true` equivalent), not
> as a general mid-session mechanism; (b) a sticky language-preference change
> does **not** rewrite `system_instruction` on the open connection — it
> updates NeXa canonical state immediately and is applied to the provider
> setup only on the **next** new / resumed session. See
> [Amendment 1 §2](#2--system-instruction-cannot-change-mid-connection) and
> [§3](#3--gemini-31-initial-history-seeding).

`CloudContextSnapshot` is a frozen dataclass in `src/nexa/realtime/`, a
**pure derived projection** of canonical NeXa state, built by
`build_cloud_context_snapshot(session, *, policy, language_preference, …)`.
It is the **only** thing that crosses to the cloud besides live mic audio.

**Minimum content (v1 / M2.6B):**
- `system_instruction` — a **minimal role card, not NeXa's identity**:
  "You are the realtime voice provider for NeXa, a personal AI assistant.
  Speak naturally and concisely for a spoken conversation. Mirror the user's
  language (Polish or English)." plus one response-shape line mirroring the
  intent of `ResponseMode.VOICE` (short, direct, ~1–3 sentences for an
  ordinary question, honour explicit requests for detail) plus, if set, one
  language-preference line (Decision F).
- `language_preference` — `pl | en | None`, from the
  `ResponseLanguageResolver` sticky preference.
- `recent_turns` — the last **N** canonical turns (bounded:
  `DEFAULT_SNAPSHOT_TURNS ≈ 12` and a char cap in the low thousands),
  rendered as a one-time context seed at session start (`send_client_content`
  on 3.x seeds only — `VERIFIED FACT`).
- `policy_state` — `ConversationPolicy` + `active_provider`, informational
  (keeps the role card consistent; not necessarily sent as literal text).

**Deferred content (M3 / M4 / M5, explicitly not in v1):**
- compact running summary / current-task descriptor
- retrieved long-term memory items
- capability / tool context

**MUST NOT contain, by default, ever:**
- the full lifetime conversation history
- the whole long-term memory store / personal vault (now or in future)
- NeXa's full persona / identity / personality definition
- user PII beyond what the user themselves just spoke in `recent_turns`
- credentials, API keys, device internals, routing internals, telemetry
- raw audio of past turns

**Freshness:** every new session, and every reconnect where
session-resumption is **not** used, gets a **freshly built** snapshot from
the current canonical state. When resumption **is** used, no snapshot is
re-sent (Gemini still holds its own context). A sticky language-preference
change or a policy change triggers a snapshot rebuild for the next session
start.

**Forbids:** streaming the growing canonical history to the cloud
turn-by-turn; sending memory "just in case"; putting the persona verbatim in
`system_instruction`.

---

### Decision F — Language-routing authority (charter F, C6) — Accepted: **Option A default**, Option B is the measured fallback

> **Amendment 1 (2026-09-10)** corrects the mid-session mechanism only.
> `system_instruction` is immutable on an open Live connection (`VERIFIED
> FACT`). So a sticky language command does **not** rewrite the cloud
> `system_instruction` mid-connection. v1 rule: NeXa records the preference
> in canonical state immediately; the spoken command also stays in Gemini's
> own live context so Gemini may naturally keep following it for the rest of
> that connection; NeXa's authoritative preference is guaranteed to reach
> the provider setup on the **next** new / resumed session. A forced
> turn-boundary reconnect purely to enforce a language change is **not**
> required in M2.6B unless evidence shows it is needed. Option A / Option B /
> Option C and the three distinct authorities are unchanged. See
> [Amendment 1 §2](#2--system-instruction-cannot-change-mid-connection).

**Measured basis (`VERIFIED FACT`, R0031 turn-local C6, Sulafat session):**
the RAW input transcription was available before the first response audio in
**3/3** valid turns (median margin ≈ 0.54 s); the PUSHED (aggregated)
transcription in **2/3**. **Timing headroom does not prove steerability of
the same, already-started response** — by the time the transcript exists,
`activityEnd` has already closed the turn and Gemini has begun generating;
there is no verified mechanism to inject a post-transcription instruction
into that same turn.

**Decision:**
- **Option A (default, chosen).** Gemini mirrors the spoken language
  **natively for the current turn** (R0031: PL and EN both worked, operator
  judged good). NeXa runs `ResponseLanguageResolver` on the finalised input
  transcription **off the critical path**, records the per-turn language as
  canonical metadata (`_response_languages` alignment), owns the sticky
  **preference**, and carries that preference into the **next** new /
  resumed session's provider setup (`system_instruction`) via a rebuilt
  `CloudContextSnapshot`. *(Amendment 1: the earlier "updated
  `system_instruction` line … mid-session" wording is superseded —
  `system_instruction` is immutable on an open connection; the running
  connection instead relies on Gemini's own live context retaining the
  spoken command.)* NeXa owns the language **preference permanently.**
  Option A only delegates *same-turn native mirroring* to Gemini.
- **Option B (measured fallback).** Strict per-turn NeXa authority: hold
  `activityEnd` briefly to run a fast local language-ID on the utterance and
  set an explicit per-turn language before Gemini generates. **Cost:** adds
  the local LID latency ahead of turn close (R0025 measured the production
  bilingual LID + one decode at ≈ +1.1 s; a lighter detector would be
  less). Adopt only if Option A's native mirroring proves unreliable in
  M2.6B operator testing.
- **Option C (parallel `whisper.cpp` transcription alongside the cloud
  turn).** Rejected unless both A and B are insufficient — it duplicates
  STT work already done well locally and adds CPU load with no proven
  benefit over B.

Three authorities stay distinct: **same-turn native language behaviour**
(Gemini, Option A) / **language preference authority** (NeXa,
`ResponseLanguageResolver`, permanent) / **canonical per-turn language
metadata authority** (NeXa).

**Forbids:** stating that Option A gives Gemini ownership of the language
*preference*; claiming NeXa can steer the current cloud response without a
verified injection mechanism.

---

### Decision G — Voice is a NeXa user preference (charter G) — Accepted

Cloud voice is a **NeXa user preference**, provider-agnostic. Store an
abstract preference (v1: a single value, default `warm_female`, current
concrete mapping → `Sulafat`), and map preference → provider-specific
compatible voice at provider start
(`gemini_voice_for_preference(pref) -> "Sulafat"`). **Not**
`GeminiIdentity.voice = "Sulafat"` — the provider does not own the
preference. `Sulafat` is the OPERATOR-CONFIRMED current default. Recorded
future alternatives (from R0031, not tested): `Vindemiatrix` (Gentle),
`Achernar` (Soft), `Aoede` (Breezy). No voice testing in this ADR or as a
gate precondition; `--voice` override stays available for M2.6B bring-up.

**Forbids:** hard-coding the voice inside the Gemini provider; treating the
voice as provider identity rather than user preference.

---

### Decision H — #5465 not-ready audio loss (charter H) — Accepted: NeXa-owned inbound audio buffer at the boundary

**Decision:** the protection lives in **NeXa-owned code at the
`RealtimeVoiceProvider` boundary**
(`src/nexa/realtime/inbound_audio_buffer.py`), **not** in patched Pipecat
internals (PR #5497 is unmerged and its semantics are still under
discussion; patching upstream privately is a maintenance trap).

Design (implemented in M2.6B, not now):
- While `readiness != READY`, mic PCM frames are appended to a **bounded**
  buffer — bounded by wall-time (`≈ 3–5 s`) **and** by bytes.
- On the transition to `READY`, buffered audio is flushed **in order**,
  respecting `activityStart` / `activityEnd` framing, then live streaming
  resumes.
- **Deterministic overflow:** drop-oldest (preserve the freshest audio),
  increment `inbound_buffer_dropped_frames`, log at `WARNING`. **No
  unbounded replay.**
- **No duplicated utterance after reconnect:** each buffered utterance
  carries a sequence id and a `delivered` flag; a reconnect flushes only
  not-yet-delivered audio.
- **Explicit readiness state** (`ProviderReadiness`) is surfaced to
  `ConversationRouter` and to telemetry.
- Metrics + structured logging on every buffer/flush/drop.
- Tests (deterministic, mocked provider) in M2.6B.

This also covers the R0031-observed **startup** NOT_READY window (no user
speech landed in it there, but it is now designed for, not left to luck).

**Forbids:** silent drop of user speech while NOT_READY; unbounded buffering
/ replay; a fork of `GeminiLiveLLMService` carrying a private patch.

---

### Decision I — GoAway / reconnect / session resumption (charter I) — Accepted

> **Amendment 1 (2026-09-10)** tightens reconnect safety (`VERIFIED FACT`,
> Live API reference): resumption **is not possible at some points**
> (model generating / executing function calls) and using a handle then
> **can lose data**; `SessionResumptionUpdate` carries `resumable` +
> `newHandle`. Corrected rules: keep only the latest handle with
> `resumable=true`; never use an empty / non-resumable handle; prefer a
> safe turn boundary (`generationComplete`) for the transition; on `GoAway`
> use `timeLeft` to schedule it; buffer inbound audio while `RECONNECTING`;
> if a safe resumption is not possible, start a **fresh** provider session
> from a rebuilt `CloudContextSnapshot` rather than risk corrupt provider
> context. **Make-before-break overlap of two connections is not claimed as
> supported** — exact socket sequencing is finalised in M2.6B after
> inspecting Pipecat 1.8.1 + `google-genai` 2.22.0 and deterministic tests.
> Local barge-in stays authoritative; the canonical `ConversationSession` is
> never touched. See [Amendment 1 §4](#4--session-resumption--reconnect-safety).

`ReconnectController` in `src/nexa/realtime/reconnect.py` (NeXa-owned;
`GeminiLiveLLMService` has no GoAway handling — `VERIFIED FACT`).

- **Proactive reconnect.** An age timer starts on connect. At
  `connection_age ≥ PROACTIVE_RECONNECT_AGE_S` (`≈ 8 min` — inside the
  ~10-min WebSocket lifetime and the 15-min audio-session cap), or on
  `GoAway{timeLeft}` (schedule for `deadline − margin`), transition to a
  reconnected session using the **latest `resumable=true`** handle, at a
  safe turn boundary where possible. *(Amendment 1: whether this is done as
  an overlapping make-before-break swap or a break-before-make transition is
  deferred to M2.6B — the ADR no longer assumes overlap is supported.)* If no
  safe resumption is possible, start a fresh session from a rebuilt
  `CloudContextSnapshot`.
- **Connection readiness** during the swap: cloud **output audio is muted**
  and inbound mic audio goes to the Decision H buffer; the swap targets a
  gap the user does not hear as a dropped turn.
- **Resumption success:** Gemini restores its own context; NeXa touches
  nothing in the canonical session.
- **Resumption failure:** NeXa builds a **fresh `CloudContextSnapshot`**
  from the canonical `ConversationSession` and starts a clean session —
  conversation continuity is preserved **by NeXa**, not by Gemini.
- **Backoff:** exponential with jitter; `MAX_RECONNECT_ATTEMPTS ≈ 5`. On
  exhaustion → Decision J failure transition (to `LOCAL` for
  `CLOUD_PREFERRED` / `AUTO`).
- **`ConversationSession` survival:** it is the same object throughout; a
  reconnect is invisible to it.
- **No duplicate responses:** only one provider's audio reaches the speaker
  at any instant; the in-flight assistant turn is either completed from the
  old session before teardown or its spoken prefix is committed once
  (Decision A rules).
- **Interruption during reconnect:** local barge-in authority still holds
  (NeXa can always stop the speaker). A barge-in during a reconnect cancels
  the in-flight cloud turn, commits the spoken prefix, and the new user
  utterance is captured by the Decision H buffer for the new session.

No live ~10-minute reconnect test in this ADR; M2.6B validates
deterministically first (mocked GoAway / socket close), then with a minimum
of real-cloud validation.

**Forbids:** waiting for the socket to die before reconnecting; rebuilding
the *canonical* conversation on reconnect; double-committing or
double-speaking a turn across a reconnect.

---

### Decision J — Cloud failure / fallback semantics (charter J) — Accepted

Explicit, per failure class. **No silent fallback that violates the user's
policy.**

| Failure | `LOCAL_ONLY` | `CLOUD_PREFERRED` / `AUTO` |
|---|---|---|
| No network | n/a (never used cloud) | stay/return to `LOCAL`; inform user once |
| Auth failure (bad/expired key) | n/a | `LOCAL`; inform user; log; do **not** retry-loop on 401/403 |
| Quota / rate limit (429) | n/a | `LOCAL`; inform user; cooldown before any retry |
| Gemini model unavailable / retired | n/a | `LOCAL`; inform user; flag for a model-baseline update |
| WebSocket failure | n/a | `ReconnectController` (Decision I); on exhaustion → `LOCAL` |
| Repeated reconnecting / flapping | n/a | after `MAX_RECONNECT_ATTEMPTS` → `LOCAL`; inform user |
| Unsupported region / policy block | n/a | refuse to start cloud; `LOCAL`; inform user; log the reason |

- `LOCAL_ONLY` **always stays local** — none of the above can move it to
  cloud, and the cloud path is never entered.
- For `CLOUD_PREFERRED` / `AUTO`, `ConversationRouter` performs the switch to
  `LOCAL`: stop the cloud provider, commit any in-flight cloud turn (spoken
  prefix rule), resume the local `VoiceRuntime` path on the **same**
  `ConversationSession`. The user is informed (a short spoken/text notice —
  "Switching to local mode.").
- **Continuity survives the switch** because both paths write to the one
  session (Decision A).
- Return to cloud is **not** automatic mid-conversation for `CLOUD_PREFERRED`
  (avoid flapping); it happens on the next session or on an explicit
  command. `AUTO`'s provisional resolver may re-evaluate at a session
  boundary only.

**Forbids:** a cloud failure silently degrading a `LOCAL_ONLY` user onto
cloud or vice-versa without notice; a tight retry loop on a hard auth/region
failure; losing the in-flight turn on the switch.

---

### Decision K — Credential surface + region / paid-key policy (charter K) — Accepted

> **Amendment 1 (2026-09-10)** corrects the region facts and removes the
> "paid key" framing. The operator is in the **United Kingdom** (not the
> EEA; Google's terms name the EEA, Switzerland and the UK as three
> separate covered regions). Two Google-terms facts are now kept distinct:
> **(A) data treatment** — for a developer in the EEA/CH/UK the Paid
> Services "How Google uses Your Data" provisions apply to *all* Services
> including AI Studio and unpaid Gemini API quota, even free of charge;
> **(B) making API Clients available to users** — only Paid Services may be
> used when making an API Client available to *users* in the EEA/CH/UK
> (a distribution / user-facing requirement). The Gemini API **Free Tier is
> available in the UK**, so private/internal M2.6B development is **not**
> blocked by billing not being enabled. A Gemini API key is **not**
> inherently a "paid key" — billing/tier belongs to the project/account and
> is verified out-of-band, never inferred from the key string. The
> credential loader therefore carries **no** paid/unpaid flag; eligibility
> is a separate **deployment / provider-eligibility policy** input
> (`distribution_mode` ∈ {`DEVELOPMENT`, `DISTRIBUTED`}; `billing_verified`
> bool set out-of-band). The `CredentialSource` seam is unchanged. See
> [Amendment 1 §1](#1--uk--eea--paid-services-correction).

- **v1 production mechanism:** an XDG secret file
  `~/.config/nexa/secrets/gemini.env` (directory `700`, file `600`),
  containing `NEXA_GEMINI_API_KEY=<value>`. Loaded by
  `src/nexa/realtime/gemini/credentials.py`, which reads the environment
  variable first and falls back to parsing that file — consistent with
  `src/nexa/config.py`'s env-var-only, fail-closed philosophy (the file only
  populates the variable). A `CredentialSource` seam is defined so a future
  **OS keychain / secret-service** backend is an added implementation, not a
  rewrite.
- **Redaction:** a single helper renders a key as `first ≤4 chars +
  "…" + length`. The key is **never** printed, logged, written to result
  JSON, put in telemetry, or committed. `git`-side defence: the secret path
  is outside the repo tree; additionally add a defensive `.gitignore` rule
  for any `*.env` under a `secrets/` path if such a path is ever introduced
  in-repo.
- **Region / Paid Services (technical vs terms — corrected by Amendment 1):**
  - **Technically required:** only a valid API key and a reachable endpoint
    to establish a Live session.
  - **Terms — data treatment** (`VERIFIED FACT`, Google Gemini API
    Additional Terms 2026-09-10): "If you're in the European Economic Area,
    Switzerland, or the United Kingdom, the terms under 'How Google uses
    Your Data' in 'Paid Services' apply to all Services, including Google AI
    Studio and unpaid quota in the Gemini API, even though they are offered
    free of charge." (A **terms** statement, not a legal opinion.)
  - **Terms — distribution** (`VERIFIED FACT`, same source): "You may use
    only Paid Services when making API Clients available to users in the
    European Economic Area, Switzerland, or the United Kingdom." This binds
    a **user-facing / distributed** NeXa cloud mode, not private operator
    development.
  - **Free Tier availability** (`VERIFIED FACT`, Gemini API
    available-regions / billing docs 2026-09-10): the United Kingdom is a
    supported region and the Free Tier is available there; there is no
    UK-specific Free-Tier exclusion.
  - **NeXa's position — eligibility is a deployment policy input, not a key
    property:**
    - `distribution_mode = DEVELOPMENT` (private operator, not made
      available to other users): may use whichever Gemini API tier the
      project is enrolled in and the terms permit. **Not** blocked because
      billing is not enabled. No artificial quota-exhaustion testing.
    - `distribution_mode = DISTRIBUTED` **and** any target user is in the
      EEA / CH / UK: **must** use Paid Services. Before such a release,
      verify `billing_verified` (active billing / appropriate paid-service
      status on the project) out-of-band. `CLOUD_PREFERRED` / `AUTO` in this
      mode refuse to start cloud until `billing_verified` is true.
    - The `credentials.py` loader carries **no** paid/unpaid flag and infers
      nothing from the key string. `ProviderEligibilityPolicy`
      (`distribution_mode`, `billing_verified`) lives with
      `ConversationPolicy` config, not with the credential.
- No dependency-file edits here (that is Decision L / M2.6B).

**Forbids:** the key in the repo, in logs, in telemetry, in the ADR;
unsupported legal assertions; inventing a cryptographic "paid key"
property/prefix; blocking `DEVELOPMENT`-mode M2.6B work solely because
billing is not enabled; starting a `DISTRIBUTED` EEA/CH/UK release without
`billing_verified` under `CLOUD_PREFERRED` / `AUTO`.

---

### Decision L — Dependency policy (charter L) — Accepted: `google-genai` is an **optional extra**

- `google-genai` becomes a **tracked** dependency for cloud voice, but as an
  **optional-dependency group**, not a core dependency. Proposed
  `pyproject.toml` shape (applied in M2.6B, **not** now):
  ```toml
  [project.optional-dependencies]
  dev = ["pytest>=8", "ruff>=0.6"]
  cloud-gemini = ["google-genai>=2.22,<3", "websockets>=15,<17"]
  ```
- **Version reasoning:** Pipecat 1.8.1's own `[google]` extra constrains
  `google-genai>=1.68.0,<3`; the research venv resolved `google-genai
  2.22.0`. `websockets` — Pipecat needs `>=13.1`; `google-genai 2.22.0`
  pulled it from 17.1 down to 16.1.1; pin `websockets>=15,<17` so both are
  satisfied and a silent major bump can't happen. `pip check` was clean at
  these versions.
- **Pipecat stays `pipecat-ai[local]==1.8.1`** (unchanged). We do **not**
  add `pipecat-ai[google]` — only `google-genai` itself is needed, and the
  full extra risks pulling more than required.
- **Local-first / `LOCAL_ONLY` installs keep no Google cloud code:**
  `pip install .` (no extra) is byte-for-byte today's stack. Cloud voice
  requires `pip install ".[cloud-gemini]"`.
  `src/nexa/realtime/gemini/` imports `google.genai` **lazily** (guarded
  import inside the module's functions) so `import nexa` without the extra
  never fails. The provider-agnostic boundary
  (`RealtimeVoiceProvider`, `CloudContextSnapshot`, `ConversationPolicy`,
  `ConversationRouter`, readiness, inbound buffer, reconnect, usage) lives in
  `src/nexa/realtime/` with **no** cloud dependency and is always importable.

**Forbids:** `google-genai` in the core `dependencies` list; a top-level
`import google.genai` on any always-loaded NeXa path; forcing Google cloud
packages onto a `LOCAL_ONLY` device.

---

### Decision M — Usage / cost telemetry (charter M) — Accepted

`ProviderUsageEvent` in `src/nexa/realtime/usage.py`:
`{session_id, provider, input_text_tokens, input_audio_tokens,
output_text_tokens, output_audio_tokens, wall_start, wall_end}`, sourced
from Gemini's authoritative `usageMetadata` on server messages — **not**
audio-duration estimates. Per-session aggregate; an **optional** cost
estimate computed from a config-provided price table (the R0030 published
rates), clearly labelled an estimate. **No raw private audio is retained for
billing** — only token / duration counts. Implemented in M2.6B.

**Forbids:** estimating billed usage from local audio duration when
`usageMetadata` is available; persisting raw audio for cost accounting.

---

### Decision N — Tools / actions boundary (charter N) — Accepted

Gemini function calling **may** be enabled in a later milestone; Gemini is
**never** the capability authority. Flow:

```
Gemini emits function call
  → GeminiLiveProvider surfaces a ProviderToolRequest event
  → NeXa ActionRouter / capability system (M4) validates
       permission + availability + device state
  → NeXa executes the action
  → NeXa returns a ProviderToolResult
  → GeminiLiveProvider forwards it to Gemini (synchronous function
       calling on 3.x — the turn blocks until the result returns, so
       NeXa must answer promptly or cancel the turn)
  → Gemini continues the spoken response
```

In v1 (M2.6B) function calling is **OFF**. The `ProviderToolRequest` /
`ProviderToolResult` event types are defined now so the boundary exists and
a later milestone slots in without reshaping the provider interface. The
cloud provider **never** directly owns device permissions and **never**
executes an action itself.

**Forbids:** Gemini calling a device/tool path directly; the cloud model
holding or evaluating permissions.

---

### Decision O — Future memory / identity compatibility (charter O) — Accepted

- The Gemini Live session is **provider-scoped ephemeral state**, never NeXa
  memory.
- Future NeXa long-term memory (M5) stays **local, user-owned, canonical**.
  Only explicitly retrieved / selected / minimal items may ever enter a
  `CloudContextSnapshot` (Decision E), and none in v1.
- Gemini is **never** the identity authority — the `system_instruction`
  role card is deliberately **not** NeXa's persona.
- This boundary is compatible with future **memory / identity / personality
  / device awareness / capability registry / typed chat in the GUI /
  phone-desktop-robot clients** because every one of those attaches to
  `ConversationSession` + NeXa core, **not** to any provider. There must
  never be a separate voice-NeXa and chat-NeXa brain (ROADMAP), and there
  must never be a separate cloud-NeXa brain.

**Forbids:** persisting anything canonical inside the provider; giving the
cloud model the persona or the memory store; a design that would need
reworking to add memory/identity later.

---

### Decision P — Pipecat authority boundary (charter P) — Accepted

- **Pipecat 1.8.1 MAY own:** realtime frame / media plumbing (transport in
  and out, audio frame types), `GeminiLiveLLMService` WebSocket mechanics
  (setup, send/recv, `serverContent` parsing), VAD frame production,
  interruption-frame propagation.
- **NeXa MUST own:** the canonical `ConversationSession`; `ConversationRouter`
  + `ConversationPolicy`; the `RealtimeVoiceProvider` abstraction;
  `CloudContextSnapshot` selection; user preferences (voice, language);
  memory / identity; capability / permission authority; the fallback policy;
  reconnect / age-timer / GoAway semantics; the #5465 inbound buffer;
  lifecycle and error semantics exposed to the rest of NeXa.
- **No second orchestration framework** (no LiveKit, no hand-rolled raw
  WebSocket client) unless M2.6B research proves the wrapped
  `GeminiLiveLLMService` cannot meet the acceptance gates. (ADR-0003 D9:
  LiveKit remains deferred, not rejected, for multi-device transport — a
  different concern.)

**Forbids:** Pipecat deciding routing/policy/fallback; a private fork of
`GeminiLiveLLMService`; adding a competing framework without evidence.

---

## Architecture

```
                         ┌───────────────────────────────────────────────┐
                         │                   NeXa core                    │
                         │                                               │
  mic (reSpeaker) ──▶ XVF3800 HW AEC ──▶ Silero VAD (turn authority)      │
                         │                     │                         │
                         │                     ▼                         │
                         │        ┌────────────────────────┐             │
                         │        │   ConversationRouter    │  ConversationPolicy
                         │        │  active_provider =      │  (LOCAL_ONLY default,
                         │        │     LOCAL | CLOUD        │   CLOUD_PREFERRED, AUTO)
                         │        └───────┬────────┬────────┘   — persisted, NeXa-owned
                         │                │        │
              active_provider=LOCAL ◀─────┘        └────▶ active_provider=CLOUD
                         │                                   │
     ┌───────────────────▼──────────────┐     ┌──────────────▼─────────────────────┐
     │  local path (M2.5B, FROZEN)      │     │  RealtimeVoiceProvider (NeXa ABC)  │
     │  whisper.cpp → guard → session   │     │  src/nexa/realtime/                │
     │  → ProviderWindow → gemma4:e4b   │     │   • ProviderReadiness             │
     │  → SpeechPlanner → Piper         │     │   • inbound audio buffer (#5465)  │
     └───────────────────┬──────────────┘     │   • ReconnectController (GoAway)  │
                         │                    │   • usage telemetry              │
                         │                    │   • CloudContextSnapshot builder │
                         │                    └──────────────┬─────────────────────┘
                         │                                   │  (optional extra:
                         │                                   │   cloud-gemini)
                         │                    ┌──────────────▼─────────────────────┐
                         │                    │ GeminiLiveProvider                │
                         │                    │ src/nexa/realtime/gemini/         │
                         │                    │  wraps Pipecat 1.8.1             │
                         │                    │  GeminiLiveLLMService (Option C) │
                         │                    │  model gemini-3.1-flash-live-... │
                         │                    │  voice Sulafat · 16k in/24k out  │
                         │                    │  server VAD OFF                  │
                         │                    └──────────────┬─────────────────────┘
                         │                                   │  WSS + API key
                         │                                   ▼
                         │                            Google Gemini Live
                         │
     ┌───────────────────▼───────────────────────────────────────────────┐
     │        ConversationSession  (THE ONE canonical authority)         │
     │  _history / _response_languages  ·  record_external_exchange(…)    │
     │  commit spoken-prefix on interruption  ·  survives switch/reconnect│
     └───────────────────┬───────────────────────────────────────────────┘
                         │  24 kHz cloud audio  /  Piper audio
                         ▼
        AecReferenceFeeder ──▶ plug:respeaker (AEC far-end ref)
                         │
                         ▼
                    USB speaker   (NeXa keeps final speaker + barge-in authority)
```

---

## Responsibility boundaries

| Responsibility | Owner | Not the owner |
|---|---|---|
| Canonical conversation history / turns | `ConversationSession` | Gemini session, Pipecat aggregators, any provider |
| Persona / identity / personality | NeXa (persona files, future identity system) | `system_instruction` role card, Gemini |
| Long-term memory / vault | NeXa (local, M5) | Gemini session memory |
| User preferences (voice, language, policy) | NeXa (persisted) | Gemini settings, Pipecat config |
| Language **preference** authority | `ResponseLanguageResolver` (NeXa) | Gemini, the local LLM |
| Same-turn spoken language behaviour | Gemini native mirroring (Option A) | — (NeXa records it as metadata) |
| Turn boundary / VAD authority | NeXa Silero (server VAD OFF) | Gemini server VAD |
| Speaker / barge-in authority | NeXa (`BargeInController`, local stop) | Gemini `serverContent.interrupted` (informational only) |
| Provider selection (`active_provider`) | `ConversationRouter` (NeXa) | any model, any voice command handler alone |
| Conversation policy | `ConversationPolicy` (NeXa, persisted) | runtime, provider |
| Context sent to cloud | `CloudContextSnapshot` builder (NeXa) | Gemini, Pipecat |
| Reconnect / GoAway / resumption strategy | `ReconnectController` (NeXa) | Pipecat (has none) |
| NOT_READY user-audio protection | NeXa inbound buffer at the boundary | patched Pipecat internals |
| Failure → fallback decision | `ConversationRouter` + Decision J table (NeXa) | provider, silent path |
| Credentials | `credentials.py` + XDG secret file (NeXa) | repo, logs, telemetry |
| Capability / permission / action execution | NeXa ActionRouter (M4) | Gemini function calling |
| Cost / usage accounting | NeXa `ProviderUsageEvent` from `usageMetadata` | audio-duration guesses |
| Realtime media plumbing / WS mechanics | Pipecat 1.8.1 `GeminiLiveLLMService` | NeXa (wraps, does not reimplement) |

---

## `RealtimeVoiceProvider` interface & lifecycle contract

State machine (`ProviderReadiness`):

```
        start(snapshot)
  ─────────────────────────▶ CONNECTING ──▶ CONTEXT_INIT ──▶ READY
                                 │               │            │  │
                                 │ error         │ error      │  │ GoAway / age timer
                                 ▼               ▼            │  ▼
                               FAILED ◀──────────────────  RECONNECTING
                                 ▲                             │
                                 │ MAX_RECONNECT_ATTEMPTS       │ resumed(from_handle=True|False)
                                 └─────────────────────────────┘
   transient server/network degradation while nominally up ──▶ DEGRADED ──▶ READY | RECONNECTING
        stop(reason) from any state ──▶ (disconnected)
```

Contract:
- `start()` is called by `ConversationRouter` **only** when
  `ConversationPolicy != LOCAL_ONLY` and a switch to `CLOUD` is decided. It
  receives a freshly built `CloudContextSnapshot`.
- The provider MUST NOT accept `send_user_audio` as *delivered to the model*
  unless `readiness == READY`; while not `READY` it MUST route incoming mic
  audio to the NeXa inbound buffer (Decision H).
- `user_turn_start()` / `user_turn_end()` are driven by NeXa's Silero turn
  authority; the provider maps them to `activityStart` / `activityEnd`.
- Every provider→NeXa event is delivered on an async event stream NeXa
  consumes; the provider never calls into `ConversationSession` directly —
  `ConversationRouter` does, applying Decision A's commit rules.
- `stop()` MUST be idempotent and MUST release the audio route and any
  resumption handle.
- `capabilities()` MUST be answerable before `READY` (from static knowledge)
  so the router can plan.
- Errors are typed: `RealtimeProviderError` (recoverable, → `DEGRADED` /
  `RECONNECTING`) vs `failed(reason)` (terminal, → `FAILED`).

---

## Canonical conversation write-path (cloud turn)

Applied by `ConversationRouter` consuming the provider event stream; the one
`ConversationSession` is the target. A new **additive** method is introduced
in M2.6B (`PROPOSAL`: `ConversationSession.record_external_exchange`) that
appends a user turn and/or an assistant turn while preserving the
`_history` / `_response_languages` index alignment and the interrupted-turn
semantics already established by `commit_interrupted_turn`.

Normal completed cloud turn:
1. Silero detects turn start → `provider.user_turn_start()` → Gemini
   `activityStart`.
2. AEC-processed mic PCM streams via `provider.send_user_audio()` (buffered
   if not `READY`).
3. Silero detects end-of-turn → `provider.user_turn_end()` → Gemini
   `activityEnd`.
4. `user_transcription(text, final=True)` arrives → held as the pending user
   turn text; `ResponseLanguageResolver` runs on it off the critical path
   (Decision F) → per-turn language metadata; sticky-command detection
   updates the canonical NeXa preference **immediately** and marks the next
   new / resumed session's `CloudContextSnapshot` for rebuild.
   *(Amendment 1: no `system_instruction` edit on the open connection —
   Gemini's own live context retains the spoken command for the rest of
   this connection.)*
5. `assistant_audio(...)` frames stream to the speaker via
   `AecReferenceFeeder`; `SpokenTextTracker`-equivalent maintains the spoken
   high-water mark.
6. `assistant_transcription(text, final=True)` arrives → assistant turn
   text.
7. On turn completion (`serverContent.turnComplete` equivalent):
   `record_external_exchange(user_text, assistant_text,
   response_language=<metadata>)` — one user turn, one assistant turn,
   appended to the one session.

Interrupted cloud turn (local barge-in confirmed):
1–5 as above.
6. `BargeInController` confirms the interruption locally → speaker stopped
   locally (authority; ~2 ms in R0031) → `provider` cancel issued → Gemini
   `activityStart` for the new utterance is gated until the cancel settles.
7. `record_external_exchange`:
   - **user turn is kept** (Gemini did hear and process it — unlike the
     local pre-generation `ROLLED_BACK_USER_TURN` case).
   - **assistant turn = the actually-spoken prefix only** (spoken
     high-water mark), marked interrupted (same `INTERRUPTED_WIRE_SUFFIX`
     convention as local). If **no** audio played, **no** assistant turn is
     appended.
8. The new utterance proceeds as a fresh turn.

Session lost mid-turn (no interruption): user turn kept; assistant turn =
spoken prefix if any audio played, else omitted; `ReconnectController`
takes over (Decision I).

---

## Provider switching path

`ConversationRouter` owns this. The `ConversationSession` object is the same
before and after — continuity is automatic.

LOCAL → CLOUD:
1. Policy check: `ConversationPolicy != LOCAL_ONLY`; for
   `CLOUD_PREFERRED` / `AUTO`, the `ProviderEligibilityPolicy` is satisfied
   (Decision K as amended — `DEVELOPMENT` mode, or `DISTRIBUTED` +
   `billing_verified` for EEA/CH/UK users) and the connectivity check
   passes.
2. Let the current local turn finish (or commit its spoken prefix if the
   user explicitly forced the switch mid-turn).
3. `build_cloud_context_snapshot(session, …)` from current canonical state.
4. `provider.start(snapshot)`; wait for `READY` (mic audio buffered
   meanwhile).
5. Route mic → cloud provider; route cloud 24 kHz audio → speaker via
   `AecReferenceFeeder`.
6. `active_provider = CLOUD`.

CLOUD → LOCAL (command, policy change, or Decision J failure):
1. `provider.stop(reason=…)`.
2. Commit any in-flight cloud turn (spoken-prefix rule).
3. Resume the local `VoiceRuntime` path on the **same**
   `ConversationSession` (its `ProviderWindow` base is recomputed from the
   now-complete history exactly as after any turn).
4. `active_provider = LOCAL`; inform the user if the switch was caused by a
   failure.

A voice command that means "switch" is recognised (possibly by a model) and
handed to `ConversationRouter` as a `SetConversationPolicy` /
switch-intent — **the router executes it.**

---

## Reconnect path

See Decision I (as amended by Amendment 1 §4). Summary sequence for a
proactive reconnect:

```
age ≥ 8 min  OR  GoAway{timeLeft}
   │
   ├─ pick transition point: prefer a safe turn boundary (generationComplete);
   │     on GoAway schedule within timeLeft
   ├─ mute cloud output; route mic → inbound buffer (RECONNECTING)
   ├─ latest handle has resumable=true ?
   │     ├─ yes → attempt resumed session with that handle
   │     │        ├─ resumed OK      → resume routing; flush buffer; done
   │     │        └─ resume failed   → FRESH session: build FRESH
   │     │                             CloudContextSnapshot from canonical
   │     │                             session, seed once, resume routing,
   │     │                             flush buffer
   │     └─ no / empty / non-resumable → FRESH session (as above); never use
   │                                     a non-resumable handle
   ├─ tear down the old connection (overlap vs break-before-make = M2.6B decision;
   │     this ADR does NOT assume make-before-break overlap is supported)
   └─ backoff+jitter on failure; after MAX_RECONNECT_ATTEMPTS → Decision J (→ LOCAL)
```

`ConversationSession` is untouched throughout. No turn is spoken or
committed twice. Local barge-in stays authoritative during `RECONNECTING`:
a barge-in stops the speaker locally, cancels the in-flight cloud turn,
commits its spoken prefix, and the new utterance lands in the inbound
buffer for the new session. Exact socket sequencing is finalised in M2.6B
after inspecting Pipecat 1.8.1 + `google-genai` 2.22.0 behaviour with
deterministic tests; no 10-minute quota-burning test is required now.

---

## `CloudContextSnapshot` contract (summary)

See Decision E (as amended). One-line form: **a freshly built, bounded,
privacy-filtered projection of canonical NeXa state — minimal role card,
language preference, last ~12 turns, policy state — seeded once at session
start via the Gemini-3.1 initial-history mechanism (`clientContent` until
`turnComplete`, no model call); never the full history, never memory, never
the persona verbatim, never credentials or raw audio; never re-injected
turn-by-turn.**

---

## Language routing decision (summary)

See Decision F (as amended). **Option A is the production default:** Gemini
mirrors the spoken language natively for the current turn; NeXa owns the
language **preference** permanently (`ResponseLanguageResolver`), records
per-turn language as canonical metadata, and carries the preference forward
into the **next** new / resumed session's provider setup via a rebuilt
`CloudContextSnapshot` — **not** by editing `system_instruction` on the open
connection (Amendment 1 §2). **Option B**
(delayed `activityEnd` + fast local language-ID, cost ≈ +1.1 s at turn
close) is the measured fallback. **Option C** (parallel `whisper.cpp`) is
rejected unless A and B both fail in M2.6B testing. Timing headroom (R0031:
transcript before first audio 3/3 turns) is **not** proof of same-turn
steerability.

---

## Provider switching policy (summary)

See Decision D + the switching path. `ConversationPolicy` (`LOCAL_ONLY`
default, `CLOUD_PREFERRED`, `AUTO` provisional) is persisted and NeXa-owned;
`active_provider` (`LOCAL` / `CLOUD`) is runtime. NeXa's `ConversationRouter`
executes every switch. `LOCAL_ONLY` never enters the cloud path. The
`AUTO` decision classifier is **not** built in M2.6B.

---

## Failure / fallback semantics (summary)

See Decision J's table. No silent policy-violating fallback; `LOCAL_ONLY`
stays local unconditionally; `CLOUD_PREFERRED` / `AUTO` fall back to `LOCAL`
with the user informed; continuity survives because both paths write the one
session; no automatic mid-conversation return to cloud (anti-flap).

---

## Security / credential decision (summary)

See Decision K (as amended by Amendment 1 §1). XDG secret file
`~/.config/nexa/secrets/gemini.env` (`700`/`600`), `NEXA_GEMINI_API_KEY`,
env-var-first loader with a `CredentialSource` seam for a future keychain.
Key never printed / logged / committed / in telemetry; redaction helper;
**no paid/unpaid flag on the key** and nothing inferred from the key string.
Terms (not legal opinion): the operator is in the **UK**; Google groups
EEA / CH / UK. **Data treatment:** for a developer there the Paid-Services
data provisions apply to all Services incl. unpaid quota. **Distribution:**
only Paid Services may be used when making an API Client available to *users*
in EEA/CH/UK. The Free Tier **is** available in the UK, so `DEVELOPMENT`-mode
M2.6B work is not blocked by billing. Eligibility is a deployment policy
input — `ProviderEligibilityPolicy(distribution_mode, billing_verified)`;
`DISTRIBUTED` to EEA/CH/UK users requires `billing_verified` before
`CLOUD_PREFERRED` / `AUTO` will start cloud.

---

## Dependency decision (summary)

See Decision L. `cloud-gemini` **optional** extra:
`google-genai>=2.22,<3`, `websockets>=15,<17`. `pipecat-ai[local]==1.8.1`
unchanged; **no** `pipecat-ai[google]`. `LOCAL_ONLY` / local-first installs
(`pip install .`) pull **no** Google cloud code; `src/nexa/realtime/gemini/`
imports `google.genai` lazily; the provider-agnostic boundary in
`src/nexa/realtime/` has no cloud dependency. **No dependency-file edit in
this ADR task.**

---

## Privacy boundary

- **Leaves the device** (only when `ConversationPolicy != LOCAL_ONLY` and
  `active_provider == CLOUD`): live mic audio (AEC-processed) for the
  current session; one `CloudContextSnapshot` per non-resumed session
  (minimal role card, language preference, last ~12 turns, policy state);
  `activityStart` / `activityEnd` markers; (future, off by default) function
  call arguments NeXa chooses to expose.
- **Never leaves the device:** full conversation history; long-term memory /
  vault (now or future); persona / identity definition; credentials; device
  internals; routing/telemetry internals; raw audio of past turns; anything
  under `LOCAL_ONLY`.
- **Not retained from the cloud:** raw cloud audio is played and discarded;
  only the finalised transcriptions (→ canonical turns) and
  `usageMetadata` counts are kept.
- `LOCAL_ONLY` is the default and is airtight: the cloud module is not
  imported, no snapshot is built, no audio is sent.

---

## Tool / capability boundary

See Decision N. Gemini function calling is OFF in v1. When later enabled:
Gemini *requests*, NeXa's ActionRouter/capability system (M4) *authorises
and executes*, NeXa returns the result, Gemini continues. The cloud provider
never owns device permissions and never executes an action. Event types
(`ProviderToolRequest` / `ProviderToolResult`) are defined now so the
boundary is stable.

---

## Consequences

### Positive
- One canonical conversation authority is preserved across local, cloud,
  reconnect, and provider switches — the ADR-0003 D2 rule holds for the
  cloud.
- Cloud is genuinely replaceable: a second realtime voice provider is one
  new `RealtimeVoiceProvider` implementation, no `ConversationSession`
  change.
- `LOCAL_ONLY` / local-first users install and run with **zero** Google
  cloud code or packages.
- Privacy is a designed boundary (`CloudContextSnapshot` allow-list), not an
  afterthought.
- The two known Pipecat gaps (#5465, no GoAway) are handled in NeXa-owned,
  testable code rather than a private upstream fork.
- Language routing matches what M2.6A actually measured, and NeXa keeps the
  preference authority.
- Compatible with every planned later milestone (memory, identity,
  capabilities, GUI typed chat, multi-device) because they attach to NeXa
  core, not to a provider.

### Negative / costs
- New surface area: a `src/nexa/realtime/` package (boundary + router +
  policy + snapshot + readiness + inbound buffer + reconnect + usage) plus
  `src/nexa/realtime/gemini/` — a non-trivial M2.6B.
- An additive `ConversationSession` method (`record_external_exchange`) —
  the first change to that file since M2.5B; must be purely additive and
  fully covered.
- `AUTO` ships provisional (no real classifier) — a documented gap.
- Cloud dependency version pins (`google-genai`, `websockets`) add a
  maintenance point that interacts with Pipecat's constraints.
- Provider eligibility adds deployment-policy configuration
  (`distribution_mode` / `billing_verified`) that must be set correctly for
  `DISTRIBUTED` deployments (Amendment 1 §1) — not a key property, so it
  cannot be inferred; it is a config item someone must set right.
- Reconnect / resumption cannot be fully proven without real-cloud time;
  M2.6B carries deterministic tests + a minimum of live validation.

### Follow-up work
- M2.6B (the whole implementation — see the plan below).
- A real `AUTO` decision classifier (privacy/task/cost/connectivity) — a
  later milestone, its own ADR or amendment.
- Function-calling enablement (needs M4 ActionRouter).
- OS keychain `CredentialSource` backend.
- Multi-device transport (ADR-0003 D9 LiveKit) — unrelated, still deferred.

### What this constrains for future milestones
- No milestone may introduce a second conversation history, persona, or
  provider-selection point for the cloud (extends ADR-0003 D2).
- Long-term memory (M5) must stay local/canonical; only explicit retrieved
  items may enter a `CloudContextSnapshot`.
- Capabilities (M4) own permissions; no provider may bypass the ActionRouter.
- Any new realtime voice provider implements `RealtimeVoiceProvider` and is
  fed a `CloudContextSnapshot`; it does not get a bespoke path.
- The GUI typed-chat milestone uses the **same** `ConversationSession`; it
  does not get its own brain, cloud or local.

---

## Options considered

### Provider boundary shape
- **Option A — `RealtimeVoiceProvider` as a peer of `ModelProvider`
  (chosen).** Honest about the contract (bidirectional speech-to-speech
  session vs text stream); keeps audio/lifecycle out of
  `ConversationSession`.
- **Option B — make the cloud a `ModelProvider`.** Rejected: it is not a
  `messages → text` function; would force audio, VAD, interruption, and
  reconnect concerns into `ConversationSession` / `ModelProvider`.
- **Option C — no boundary; wire Pipecat's `GeminiLiveLLMService` straight
  into the voice pipeline (the M2.6A spike shape).** Rejected for
  production: no canonical history, no policy, no fallback, no privacy
  filter, Pipecat owns routing by default.

### Canonical history for cloud turns
- **Option A — commit finalised cloud transcriptions into the one
  `ConversationSession` via an additive method (chosen).**
- **Option B — keep a cloud-side history and reconcile.** Rejected: a second
  brain by construction (violates the canonical rule and ADR-0003 D2).
- **Option C — no history for cloud (spike behaviour).** Rejected: breaks
  continuity across a LOCAL↔CLOUD switch and across reconnect.

### Language routing
- **Option A — Gemini native same-turn mirroring; NeXa owns the preference
  (chosen default).**
- **Option B — strict per-turn NeXa authority via delayed `activityEnd` +
  local language-ID (measured fallback, ≈ +1.1 s).**
- **Option C — parallel `whisper.cpp` transcription (rejected unless A & B
  fail).**

### #5465 protection
- **Option A — NeXa-owned inbound audio buffer at the boundary (chosen).**
- **Option B — carry PR #5497 as a private patch to `GeminiLiveLLMService`.**
  Rejected: unmerged, semantics unsettled, maintenance trap.
- **Option C — do nothing (rely on the startup-only observation).**
  Rejected: user speech loss is a silent data-loss bug waiting for timing to
  change.

### Cloud dependency
- **Option A — `google-genai` as an optional `cloud-gemini` extra
  (chosen).** Local-first installs stay clean.
- **Option B — `google-genai` in core `dependencies`.** Rejected: forces
  Google cloud packages onto `LOCAL_ONLY` devices.
- **Option C — vendor a minimal Live client.** Rejected: reinvents a
  maintained SDK; more risk than value.

### Do nothing
Keep M2.6A as a research artifact and not productionise cloud voice.
Rejected: M2.6A is OPERATOR-CONFIRMED and cloud voice is a roadmap
commitment; leaving it un-architected guarantees an ad-hoc integration that
violates the canonical rule.

---

## Known risks

- **`RISK`** — Gemini Live `gemini-3.1-flash-live-preview` is a **Preview**
  model; it can change or be retired. Mitigation: model is a baseline behind
  the boundary (Decision C); Decision J handles "model retired".
- **`RISK` / `OBSERVATION`** — the unverified community report of a Polish
  EN/US accent. Mitigation: M2.6B operator acceptance is the authoritative
  check; Option B language path and a voice change are available responses.
- **`RISK`** — reconnect / resumption behaviour is not fully proven without
  real-cloud time. Mitigation: deterministic tests first, minimum live
  validation, proactive reconnect well inside the lifetime limits.
- **`RISK`** — synchronous-only function calling on 3.x means a slow NeXa
  action blocks the spoken turn. Mitigation: function calling OFF in v1;
  when enabled, NeXa must answer promptly or cancel the turn.
- **`RISK`** — `google-genai` / `websockets` pins interacting with a future
  Pipecat bump. Mitigation: narrow ranges, `pip check` in CI for the
  `cloud-gemini` extra.
- **`RISK`** — the additive `ConversationSession` method is the first change
  to that file since M2.5B. Mitigation: purely additive, index-alignment
  invariant tests, local-voice regression suite must stay green.
- **`RISK`** — `AUTO` provisional behaviour could surprise a user expecting
  a real privacy classifier. Mitigation: documented; `AUTO` is not the
  default; `LOCAL_ONLY` is.

---

## Deferred work (explicitly not in M2.6B)

- The real `AUTO` decision classifier (privacy / task / cost / connectivity
  / latency / capability inputs).
- Gemini function calling / tools enablement (needs M4).
- Long-term memory retrieval into `CloudContextSnapshot` (needs M5).
- Running-summary / current-task descriptor in the snapshot (needs M3).
- OS keychain / secret-service `CredentialSource` backend.
- Multi-device transport (ADR-0003 D9).
- Any change to the frozen local voice baseline.
- A second `RealtimeVoiceProvider` implementation.

---

## M2.6B implementation plan (ordered; NOT executed in this ADR)

Local voice behaviour stays **byte-for-byte frozen**; every component below
is new code or a purely additive hook. Naming is `PROPOSAL`, to be
reconciled with repo conventions during implementation.

| # | Component | File (proposed) | Owning layer | Responsibility | Key dependencies | Tests | Failure cases | Touches frozen local voice? |
|---|---|---|---|---|---|---|---|---|
| 1 | `RealtimeVoiceProvider` ABC + event/lifecycle types + `ProviderReadiness` + `RealtimeProviderCapabilities` + typed errors | `src/nexa/realtime/provider.py` | NeXa boundary (no cloud dep) | Define the peer-of-`ModelProvider` contract | stdlib only | contract/abc tests; readiness state-machine transitions | n/a (definitions) | No |
| 2 | `ConversationPolicy` + `ActiveProvider` + `ProviderEligibilityPolicy` + `NEXA_CONVERSATION_POLICY` loader | `src/nexa/realtime/policy.py`, `src/nexa/config.py` (additive) | NeXa | Persisted policy; runtime provider enum; `ProviderEligibilityPolicy(distribution_mode ∈ {DEVELOPMENT,DISTRIBUTED}, billing_verified)` (Decision K as amended — separate from the credential); fail-closed parse | `nexa.config` pattern | parse/default/invalid-value; `LOCAL_ONLY` default; DISTRIBUTED+EEA/CH/UK requires `billing_verified` | invalid env value → raise (fail closed); DISTRIBUTED without `billing_verified` → cloud refused | No (additive config) |
| 3 | `CloudContextSnapshot` + `build_cloud_context_snapshot(...)` | `src/nexa/realtime/snapshot.py` | NeXa | Derived, bounded, privacy-filtered projection; `recent_turns` shaped for one-time initial-history seeding (Amendment 1 §3), not turn-by-turn injection | `ConversationSession`, `ResponseLanguageResolver` | allow-list content; bound N turns + chars; never persona/memory/creds; freshness; seed-once shape | empty history; over-long turns; missing preference | No (reads history) |
| 4 | `ProviderReadiness` inbound audio buffer (#5465) | `src/nexa/realtime/inbound_audio_buffer.py` | NeXa boundary | Bounded capture of mic PCM while `!= READY`; ordered flush; drop-oldest; per-utterance `delivered` flag | stdlib; audio frame type | fill/flush/overflow/drop metric; no dup after reconnect; ordering | overflow; flush during a second outage; reconnect mid-flush | No |
| 5 | `ReconnectController` (GoAway + age timer + resumption) | `src/nexa/realtime/reconnect.py` | NeXa boundary | Proactive reconnect at a safe turn boundary; handle GoAway via `timeLeft`; keep only the latest `resumable=true` handle; never use a non-resumable/empty handle; buffer inbound audio while `RECONNECTING`; fall back to a fresh seeded session when safe resumption isn't possible; backoff; failure→Decision J. Overlap vs break-before-make is a code-time decision here — the ADR does not assume make-before-break is supported | provider events; `snapshot.py` | mocked GoAway/socket-close; `resumable=false` → fresh session; resumption success + failure→fresh snapshot; backoff cap; no double-commit; no non-resumable-handle use | resumption fail; non-resumable handle; repeated flap; barge-in mid-reconnect | No |
| 6 | `ProviderUsageEvent` + per-session aggregation + optional cost estimate | `src/nexa/realtime/usage.py` | NeXa | Authoritative usage from `usageMetadata`; labelled cost estimate; no raw audio retained | provider `usage()` events; price table (config) | aggregation; estimate math; no-audio-retention | missing `usageMetadata`; partial session | No |
| 7 | `credentials.py` + `CredentialSource` seam | `src/nexa/realtime/gemini/credentials.py` | NeXa (cloud pkg) | Env-var-first load; XDG secret file fallback; redaction. **No paid/unpaid flag; nothing inferred from the key string** (Amendment 1 §1 — eligibility is `ProviderEligibilityPolicy` in row 2) | `os.environ`, file perms check | env path; file path; redaction; missing key → typed error; perms warning; no key-string tier inference | missing/malformed file; unreadable key file | No |
| 8 | `GeminiLiveProvider(RealtimeVoiceProvider)` wrapping Pipecat `GeminiLiveLLMService` | `src/nexa/realtime/gemini/service.py` | NeXa cloud pkg | Option C wrapper: architecturally-required start-up ELEMENTS = configure initial-history support, setup / `system_instruction` (immutable once open — Amendment 1 §2), seed `CloudContextSnapshot` recent turns **once** via `clientContent` until `turnComplete` (Amendment 1 §3), the `LLMRunFrame` kickoff (R0031), and realtime audio start — their exact composition/ORDER against installed Pipecat 1.8.1 + `google-genai` 2.22.0 source is an M2.6B implementation-detail verification, not assumed here; empty `LLMContext`; server VAD off; `activityStart/End` from Silero; event translation; capture only `resumable=true` handles; `usageMetadata` surfacing; lazy `google.genai` import | `pipecat-ai[local]==1.8.1`, `google-genai` (extra) | dry object-graph build; mocked service lifecycle; one-time seed (no repeated injection); event translation; readiness gating of `send_user_audio` | connect failure; auth failure; NOT_READY; server interruption; GoAway; non-resumable handle | No (parallel path) |
| 9 | `gemini_voice_for_preference(...)` + voice preference storage | `src/nexa/realtime/gemini/voice.py`, preference store | NeXa | Provider-agnostic voice preference → Gemini voice name; default `warm_female`→`Sulafat`; `--voice` override | config/preference | mapping; default; override; unknown preference | unknown voice name from override | No |
| 10 | `ConversationRouter` | `src/nexa/realtime/router.py` | NeXa | Resolve `ConversationPolicy`→`active_provider`; execute LOCAL↔CLOUD switch; consume provider event stream; apply Decision A commit rules; apply Decision J on failure; drive Silero turn markers to the active provider | `ConversationSession`, provider, `snapshot.py`, `reconnect.py`, local `VoiceRuntime` | `LOCAL_ONLY` never touches cloud; switch preserves continuity; no dup assistant turn; interrupted → spoken-prefix only; failure → LOCAL + notice | switch mid-turn; failure during switch; policy change mid-session | Additive wiring only; local path unchanged |
| 11 | `ConversationSession.record_external_exchange(...)` (additive) + `SetConversationPolicy` command | `src/nexa/conversation/session.py` (additive), command surface | NeXa canonical | Append user/assistant turn(s) from a cloud turn keeping `_history`/`_response_languages` alignment and interrupted semantics; internal policy-set command | existing session invariants | additive-only; index alignment; interrupted cloud turn keeps user turn, commits spoken prefix / omits assistant if nothing spoken; `NOTHING_TO_COMMIT` parity | interrupted before any audio; missing assistant transcription; empty user transcription | **Additive method only**; existing `send()` / `commit_interrupted_turn()` byte-for-byte unchanged |
| 12 | HYBRID audio wiring: tee cloud 24 kHz output to `AecReferenceFeeder`; keep Silero + `BargeInController` as authority for the cloud path | `src/nexa/realtime/` wiring + reuse `src/nexa/voice_tts/aec_reference.py` | NeXa | Cloud output feeds the XVF3800 far-end reference exactly as Piper does; local barge-in stops the speaker regardless of server round-trip | `AecReferenceFeeder`, `BargeInController`, `AecReferenceHealth` | AEC ref active during cloud playback; local stop precedes server interruption; health telemetry | AEC ref down; server interruption never arrives | Reuses frozen components unchanged; no new tuning |
| 13 | Probe / operator app for cloud path | `apps/nexa_cloud_voice_probe.py` (or extend the existing bilingual probe with `--cloud`) | tooling | One-command real operator session on the cloud path with live telemetry | all of the above | n/a (manual) | n/a | No |
| 14 | `pyproject.toml` `cloud-gemini` optional extra + CI `pip check` for it | `pyproject.toml`, CI | build | Track `google-genai>=2.22,<3`, `websockets>=15,<17` as an opt-in extra | — | `pip install .` has no google; `pip install .[cloud-gemini]` resolves + `pip check` clean | resolver conflict with a future Pipecat bump | No |
| 15 | Docs: ADR-0004 amendments if M2.6B evidence diverges; update `CURRENT_STATE` / `ROADMAP` on completion | `docs/**` | docs | Keep the record accurate | — | markdown/doc checks | — | No |

Suggested order: **1 → 2 → 3 → 4 → 6 → 7 → 9 → 5 → 8 → 11 → 10 → 12 → 14 → 13**
(boundary + policy + snapshot + buffer + usage + creds + voice first; then
reconnect; then the Gemini wrapper; then the additive session method; then
the router that ties it together; then HYBRID audio wiring; then the extra
and the probe).

---

## M2.6B acceptance gates (measurable)

M2.6B is COMPLETE only when **all** of the following hold:

1. **`LOCAL_ONLY` never invokes cloud** — with `NEXA_CONVERSATION_POLICY`
   unset or `LOCAL_ONLY`, no cloud module is imported, no network call is
   made, no `CloudContextSnapshot` is built, no audio leaves the device
   (asserted by test: import graph + a network-blocking test + no-snapshot
   assertion).
2. **CLOUD uses the canonical `ConversationSession`** — a cloud turn appends
   exactly one user turn and (when spoken) one assistant turn to the one
   session; `history` reflects them; `_history` / `_response_languages` stay
   index-aligned.
3. **LOCAL↔CLOUD switch preserves continuity** — a conversation that starts
   local, switches to cloud, and switches back has one coherent
   `history` with no gap, no duplication, and correct ordering.
4. **No duplicate canonical assistant turns** — across turn completion,
   interruption, provider switch, and reconnect, each assistant turn is
   committed at most once.
5. **Interrupted cloud response stores only the actually-spoken prefix** —
   per the local interruption semantics; if no audio played, no assistant
   turn; the user turn is retained.
6. **No silent user-audio loss during NOT_READY** — mic audio during any
   `readiness != READY` window is buffered and flushed in order, or dropped
   only on bounded overflow with a metric and a `WARNING` log; deterministic
   test proves no silent loss and no post-reconnect duplication.
7. **Reconnect does not create a second brain** — after a proactive
   reconnect (mocked GoAway / socket close), the canonical session is
   byte-identical to no-reconnect for the same turns; Gemini's restored
   context is not read back as authority.
8. **Resumption failure / non-resumable handle rebuilds from
   `CloudContextSnapshot`** — when the resumption handle is rejected, empty,
   or `resumable=false`, a fresh session is started and a fresh snapshot
   seeded once; a non-resumable handle is never sent (deterministic test).
9. **No API key leakage** — key never appears in logs, telemetry, result
   JSON, `history`, or the repo; redaction helper covered by a test; secret
   scan clean.
10. **AEC healthy on the cloud path** — `AecReferenceHealth` reports the
    far-end reference active during cloud playback in an operator session;
    0 AEC failures (matching the R0031 bar).
11. **Local barge-in within the accepted threshold on the cloud path** —
    local speaker stop on a confirmed interruption is within the M2.5B /
    R0031 accepted range (~2 ms local stop; ≤ 100 ms R0030 target),
    independent of the server round-trip.
12. **Cloud latency does not materially regress from M2.6A** — EOT → first
    audible stays ≈ 0.75–0.9 s median in an operator session (R0030 ≤ 1.5 s
    remains the hard bar).
13. **PL/EN preserves operator UX** — a bilingual operator session on the
    cloud path is judged at least as good as M2.6A; sticky
    language-preference commands take effect on the next turn/session.
14. **`Sulafat` is the default and the voice is configurable** — default
    with no override; `--voice` / preference override works.
15. **Local voice regression suite green** — the full existing `pytest` /
    `unittest` suites pass unchanged (no local-voice behaviour moved).
16. **Cloud-specific unit/integration tests green** — deterministic tests
    for the boundary, snapshot allow-list, inbound buffer, reconnect
    controller (mocked), router policy/switch/fallback, and the additive
    session method.
17. **Real-hardware operator acceptance** — an operator cloud-path session
    on the real Pi + reSpeaker/XVF3800 is explicitly accepted **before**
    M2.6B is marked COMPLETE (as M2.6A required).
18. **Initial history seeded exactly once; `system_instruction` never
    mutated on an open connection** (Amendment 1 §2 / §3) — the bounded
    `CloudContextSnapshot` recent turns are delivered once at session start
    via the initial-history mechanism (no model call), never re-injected
    turn-by-turn; a sticky language command updates canonical state
    immediately and reaches the provider setup only on the next new /
    resumed session (deterministic test).
19. **Eligibility is a deployment policy, not a key property** (Amendment 1
    §1) — `DEVELOPMENT` mode is not blocked when billing is disabled;
    `DISTRIBUTED` mode with an EEA/CH/UK target user refuses to start cloud
    under `CLOUD_PREFERRED` / `AUTO` unless `billing_verified` is true; the
    credential loader never infers a tier from the key string
    (deterministic test).

**Reconnect testing:** deterministic / mocked first (GoAway, socket close,
resumption reject / `resumable=false`, age-timer); a **minimum** of
real-cloud validation after. **No artificial quota-exhaustion test is
required** — the 429 path is covered by a mocked response and the Decision J
table.

---

## Compliance / review

- **ADR-0003 D2 upheld and extended:** one `ConversationSession`; the cloud
  gets a derived snapshot and commits back into that session; no second
  history, persona, or provider-selection point — now explicitly for the
  cloud.
- **ADR-0003 D1 / Decision P:** Pipecat remains infrastructure (media / WS
  mechanics); NeXa owns routing, policy, context, memory, identity,
  capabilities, fallback, reconnect.
- **`AGENTS.md` §3 principles:** local-first (optional extra; `LOCAL_ONLY`
  default and airtight), privacy-first (`CloudContextSnapshot` allow-list;
  raw cloud audio discarded), user-owned data (memory stays local; policy
  persisted and user-owned), provider-independent (`RealtimeVoiceProvider`
  peer boundary), one canonical authority per responsibility, no silent
  fallback brains (Decision J).
- **Local voice baseline (M2.5B / `R0029`) untouched:** no `src/nexa/**`
  change in this ADR; the M2.6B plan marks every component's relationship to
  the frozen path (all "No" except purely additive wiring / an additive
  `ConversationSession` method).
- **Config discipline:** `NEXA_CONVERSATION_POLICY` follows the env-var-only,
  fail-closed pattern.
- **Secret discipline:** the key is outside the repo, redacted, never
  logged/committed; this ADR contains no key material.
- **This task's scope check:** ADR + doc updates only. `git diff -- src/nexa`
  empty; `git diff -- tests` empty; `git diff -- pyproject.toml` empty; no
  dependency file changed; no Gemini conversation run; no reconnect / quota /
  stress test run.
- **Amendment 1 (2026-09-10):** four pre-implementation factual / API /
  terms corrections (see the Amendment 1 section). **No accepted decision is
  reversed.** Explicitly preserved unchanged: one canonical
  `ConversationSession`; `RealtimeVoiceProvider` as a peer of
  `ModelProvider`; the `GeminiLiveProvider` wrapper; the `LOCAL_ONLY` /
  `CLOUD_PREFERRED` / `AUTO` policy split and the `active_provider`
  distinction; the privacy-filtered `CloudContextSnapshot`; Option A
  native-mirroring default + Option B fallback; `Sulafat`; the NeXa-owned
  #5465 protection; XVF3800 AEC; local Silero turn authority; local
  speaker / barge-in authority; the optional `cloud-gemini` dependency;
  tools gated by a future NeXa ActionRouter; Gemini session ≠ NeXa
  identity / memory; Pipecat infrastructure-only authority. M2.6A remains
  PASS / OPERATOR-CONFIRMED. M2.6B remains NEXT / NOT STARTED.

### Revisit triggers
- M2.6B operator evidence contradicting Option A language routing → adopt
  Option B (amendment).
- The Polish-accent community report reproducing under operator test →
  Option B and/or a voice change.
- Gemini `gemini-3.1-flash-live-preview` retired or materially changed →
  update the Decision C baseline (amendment).
- A real `AUTO` classifier being specified → its own ADR or an amendment
  here.
- A second realtime voice provider being added → confirm the
  `RealtimeVoiceProvider` contract still fits; amend if not.

---

## Amendment 1 — factual / API / terms corrections before M2.6B (2026-09-10)

Four pre-implementation corrections against **current** official Google
Gemini documentation and the Gemini API Additional Terms (verified
2026-09-10). **No accepted decision is reversed.** Original decision text is
kept; each affected section above carries an inline pointer here.

### Official facts verified (2026-09-10)

| # | `VERIFIED FACT` | Source |
|---|---|---|
| 1 | "You may use only Paid Services when making API Clients available to users in the European Economic Area, Switzerland, or the United Kingdom." | Gemini API Additional Terms |
| 1 | "If you're in the European Economic Area, Switzerland, or the United Kingdom, the terms under 'How Google uses Your Data' in 'Paid Services' apply to all Services, including Google AI Studio and unpaid quota in the Gemini API, even though they are offered free of charge." | Gemini API Additional Terms |
| 1 | The United Kingdom is a supported Gemini API region; the available-regions / billing docs place no UK-specific Free-Tier exclusion — the Free Tier is available in the UK. | Gemini API available-regions / billing docs |
| 2 | "You cannot update the configuration while the connection is open. However, you can change the configuration parameters, except the model, when pausing and resuming via the session resumption mechanism." (`BidiGenerateContentSetup` = `model`, `generationConfig`, `systemInstruction`, `tools[]`) | Live API WebSockets reference |
| 3 | `HistoryConfig.initialHistoryInClientContent` (bool): "If true, after sending setupComplete, the server will wait and at first process clientContent messages until turnComplete is true. This initial history will not trigger a model call." | Live API WebSockets reference |
| 4 | `SessionResumptionUpdate` = `newHandle` (string) + `resumable` (bool). "Resumption is not possible at some points in the session. For example, when the model is executing function calls or generating … such [resumption] will result in some data loss." | Live API WebSockets reference |
| 4 | `GoAway.timeLeft` = "The remaining time before the connection will be terminated as ABORTED." | Live API WebSockets reference |

### 1 — UK / EEA / Paid-Services correction

**What the ADR said:** "the operator is in the EEA" and, from that, "v1
cloud voice must use a **paid-tier key**"; the credential loader was to
"record whether the configured key is declared paid-tier"; `CLOUD_PREFERRED`
/ `AUTO` would "refuse to start cloud without that declaration".

**Corrected:**

- The operator is in the **United Kingdom**. The UK is **not** in the EEA.
  Google's terms name the **European Economic Area, Switzerland and the
  United Kingdom** as three separate covered regions. Correct every
  occurrence.
- Keep two distinct terms facts separate:
  - **(A) Data treatment.** For a developer in the EEA / CH / UK, the Paid
    Services "How Google uses Your Data" provisions apply to **all**
    Services, including Google AI Studio and unpaid Gemini API quota, even
    when free of charge. So the ADR must **not** state that a UK developer's
    free quota is used for model improvement under the ordinary
    unpaid-services rule.
  - **(B) Making an API Client available to users.** Only Paid Services may
    be used when making API Clients available to **users** in the EEA / CH /
    UK. This is a **distribution / user-facing** requirement.
- Do **not** convert (B) into "M2.6B private/internal development cannot run
  unless the API project is already paid." The Gemini API **Free Tier is
  available in the UK**. Development is not blocked by billing being
  disabled, and no artificial quota-exhaustion testing is done.
- A Gemini API key is **not** inherently a "paid key". Billing / tier
  belongs to the associated **project / account**, verified out-of-band. Do
  **not** invent a cryptographic property or prefix that marks a key as
  paid.

**Production rule (replaces the "paid-tier key" concept):**

- `ProviderEligibilityPolicy` is a deployment-policy input, held with
  `ConversationPolicy` config, **not** with the credential:
  - `distribution_mode` ∈ { `DEVELOPMENT`, `DISTRIBUTED` }
  - `billing_verified` : bool — set out-of-band from the project's billing /
    paid-service status; never inferred from the key.
- `distribution_mode = DEVELOPMENT` (private operator, not offered to other
  users): may use whichever Gemini API tier the project is enrolled in and
  the terms permit; **not** blocked because billing is not enabled.
- `distribution_mode = DISTRIBUTED` **and** any target user is in the
  EEA / CH / UK: **must** use Paid Services; `CLOUD_PREFERRED` / `AUTO`
  refuse to start cloud until `billing_verified` is true; a release is
  checked for active billing before it is made available to users.
- The `credentials.py` loader carries **no** paid/unpaid flag. The
  `CredentialSource` seam is unchanged.

Affected above: Context evidence bullet; Decision D (`AUTO` provisional
gate); Decision K (heading pointer + Region/Paid-Services bullets +
Forbids); Provider switching path step 1; Security/credential summary;
M2.6B plan rows 2 and 7; acceptance gate 19.

### 2 — System instruction cannot change mid-connection

**What the ADR said:** a sticky language-preference change could update the
Gemini `system_instruction` "into an updated `system_instruction` line when
a sticky command is detected mid-session" and the write-path could "schedule
a `system_instruction` refresh".

**Corrected (VERIFIED):** the `BidiGenerateContentSetup` message fixes
`model`, `generationConfig`, `systemInstruction`, `tools` for the
connection; the configuration **cannot be updated while the connection is
open**; parameters other than `model` can change only when pausing/resuming
via session resumption.

**Production rule (Option A language routing is otherwise unchanged):**

- Gemini performs native same-turn language mirroring.
- NeXa permanently owns the sticky language preference and records it in
  canonical state **immediately** when the spoken command is detected.
- NeXa does **not** mutate `system_instruction` on the currently open
  connection.
- For v1, the simplest robust behaviour: the spoken sticky command itself
  stays in Gemini's live conversational context, so Gemini may naturally
  keep following it for later turns **in that connection**. NeXa's
  authoritative preference is guaranteed to be applied to the Gemini setup /
  system instruction on the **next** new or resumed / reconfigured
  connection (via a rebuilt `CloudContextSnapshot`).
- If strict, immediate, NeXa-enforced preference is ever required
  independently of Gemini understanding the spoken command, schedule a
  **controlled turn-boundary session reconfiguration / resumption** with the
  updated setup. Do **not** force such a reconnect on every language command
  in M2.6B unless evidence shows it is necessary.
- Keep three things distinct: (a) Gemini following the user's spoken command
  in its own live context; (b) NeXa canonical preference state; (c)
  NeXa-enforced provider setup configuration.

Affected above: Decision E (heading pointer + freshness); Decision F
(heading pointer + the "next session" wording); Canonical write-path step 4;
Language-routing summary; acceptance gate 18.

### 3 — Gemini 3.1 initial history seeding

**What the ADR said:** `CloudContextSnapshot.recent_turns` (~12 canonical
turns) seeded "using `send_client_content`" as a one-time seed, with
`send_client_content` described only as "seeds context at start only".

**Corrected / made explicit (VERIFIED):** for
`gemini-3.1-flash-live-preview`, initial context history is seeded by
enabling the initial-history mode — `history_config.initial_history_in_client_content = true`
(field `HistoryConfig.initialHistoryInClientContent`) — after which the
server processes `clientContent` messages until `turnComplete` **without a
model call**. After the first model turn, incremental text uses realtime
input text, **not** `send_client_content` as a general mid-session history
mechanism.

**Production rule:**

- **New, non-resumed Gemini session:**
  1. configure initial-history support;
  2. establish setup / `system_instruction`;
  3. seed the bounded `CloudContextSnapshot` recent-turn history **once**
     (`clientContent` until `turnComplete`; no model call);
  4. begin realtime audio conversation.
- **Resumed Gemini session:** do **not** duplicate the snapshot / history if
  successful session resumption already restored the provider context.
- **Resumption failure:** start a fresh session and seed a **fresh**
  `CloudContextSnapshot` once.
- **No repeated history injection turn-by-turn.**

Affected above: Context evidence bullet; Decision C (heading pointer);
Decision E (heading pointer + `recent_turns`); `CloudContextSnapshot`
summary; M2.6B plan rows 3 and 8; acceptance gate 18.

### 4 — Session resumption / reconnect safety

**What the ADR said (Decision I / Reconnect path):** "open a **new** session
using the **latest** session-resumption handle, transfer audio routing,
then close the old session" — which reads as an assumed make-before-break
overlap of two connections.

**Corrected (VERIFIED):** `SessionResumptionUpdate` carries `resumable` and
`newHandle`; **resumption is temporarily not possible** at some points
(model generating or executing function calls) and using an earlier token in
such a state **may cause data loss**; `GoAway.timeLeft` gives the controlled
window before an `ABORTED` termination.

**Amended Decision I rules:**

- Maintain only the **latest handle for which `resumable=true`**.
- **Never** treat an empty / non-resumable handle as usable.
- Prefer a **safe turn boundary** (`generationComplete`) for the transition
  where possible.
- On `GoAway`, use `timeLeft` to schedule the controlled transition.
- Buffer inbound user audio while `RECONNECTING`.
- Local barge-in remains authoritative; the canonical `ConversationSession`
  is never touched.
- If safe resumption cannot occur, **fall back to a fresh provider session
  built from a rebuilt `CloudContextSnapshot`** rather than risk corrupt
  provider context.
- Do **not** claim make-before-break connection overlap is supported unless
  proven. The exact socket sequencing (overlap vs break-before-make) is
  finalised in M2.6B after inspecting Pipecat 1.8.1 + `google-genai` 2.22.0
  behaviour with deterministic tests.
- No 10-minute quota-burning test at this point.

Affected above: Decision I (heading pointer + proactive-reconnect bullet);
Reconnect path diagram + prose; M2.6B plan row 5; acceptance gate 8.

### Preserved unchanged by Amendment 1

One canonical `ConversationSession`; `RealtimeVoiceProvider` as a peer of
`ModelProvider`; the `GeminiLiveProvider` wrapper; `LOCAL_ONLY` /
`CLOUD_PREFERRED` / `AUTO` policy split and the `active_provider`
distinction; privacy-filtered `CloudContextSnapshot`; Gemini native
language mirroring (Option A) + Option B fallback; `Sulafat`; NeXa-owned
#5465 protection; XVF3800 AEC; local Silero turn authority; local speaker /
barge-in authority; the optional `cloud-gemini` dependency; tools controlled
by a future NeXa ActionRouter; Gemini session ≠ NeXa identity / memory;
Pipecat infrastructure-only authority boundary. **M2.6A remains PASS /
OPERATOR-CONFIRMED. M2.6B remains NEXT / NOT STARTED.**

---

## Amendment 2 — NeXa Core boundary, M2.6B dual-pipeline pause, simplified golden-derived baseline (2026-09-16)

### Context

M2.6B's production HYBRID runtime (`nexa.realtime.gemini.runtime`, M2.6B.3+)
is a DUAL-PIPELINE architecture: the provider's own headless Pipecat
pipeline is bridged to a separate hardware pipeline via an `asyncio.Queue`,
with a NeXa-owned `BargeInController`/candidate-ownership state machine
layered on top of Gemini's own native turn/interruption handling.

R0071 (real-hardware acceptance, 2026-09-16) found this dual-pipeline
runtime produces sustained self-echo/self-conversation on the current Pi
topology, even after (a) fixing a genuine architectural ownership gap
(a raw local VAD candidate could reach the provider before
`BargeInController` decided confirm/reject — fixed, kept, see below) and
(b) a controlled reference-gain A/B (`CoherentReferenceGain` on vs. off)
that did not reproduce clean behavior either way. The SAME day, on the
SAME physical microphone + separate USB speaker, a from-source re-run of
the ORIGINAL, OPERATOR-CONFIRMED R0031/M2.6A probe (commit `7dd6b87`) —
ONE Pipecat pipeline, Gemini's own native VAD-driven turn/interruption
handling, no NeXa-owned interruption-authority layer — was clean: natural
conversation, natural interruption, zero self-interruption during a 14s
silent-operator window, all operator-rated 10/10. See
`docs/reports/R0071_golden_voice_recovery_and_boundary_20260916.md`.

### Decision

1. **The M2.6B dual-pipeline production media path is PAUSED, not
   deleted.** `nexa.realtime.gemini.runtime`, `BargeInController`'s use in
   the cloud path, the R0068–R0070 scheduled-reference research, and every
   R00xx self-echo/AEC investigation report remain in the repository as
   evidence and as a resumable investigation, should a future real product
   requirement need it. None of it is removed or reverted.
2. **The accepted `CLOUD PREFERRED` production baseline is the simplified,
   golden-M2.6A-derived path**: `nexa.realtime.gemini.simple_conversation`
   (`CloudRealtimeConversationAdapter`) + `apps/nexa_cloud_voice_simple.py`.
   ONE Pipecat pipeline; Gemini's own native VAD-driven turn/interruption
   handling (no second, NeXa-owned interruption-authority state machine);
   `AecReferenceFeeder` fed WITHOUT `gain_source` (byte-for-byte golden's
   own reference path). This is BEHAVIOR extracted from the proven probe,
   not the probe's own diagnostics, wired to the SAME `ConversationRouter`/
   `ConversationSession`/`CloudContextSnapshot` boundary this ADR already
   established — zero new conversation-state logic.
3. **NeXa Core vs. cloud provider ownership is now stated explicitly**
   (previously implicit in this ADR's "Responsibility boundaries" /
   "Privacy boundary" sections, which are unchanged and still govern):

   NeXa Core (local, canonical, persistent) owns identity, personality,
   memory, user model, relationship state, goals, capabilities,
   permissions, device state, `ConversationSession` and canonical
   history, and learning/adaptation. A `RealtimeVoiceProvider` (Gemini
   Live today) owns only the realtime conversational session it is
   handed: audio ingress, conversational inference, speech-to-speech
   generation, its own native turn/interruption handling, and streaming
   transcripts/events back through the SAME `ConversationRouter` entry
   point this ADR already defines. Provider state is ephemeral and is
   never canonical NeXa memory; a provider is replaceable without
   changing NeXa's identity. This does not change any decision already
   made in this ADR — it is the explicit statement of what Decisions A/D/E
   already implied.
4. **Explicit cloud-eligibility classification** (`nexa.realtime.privacy.
   CloudEligibility`: `LOCAL_ONLY` / `CLOUD_SAFE` / `CLOUD_WITH_USER_APPROVAL`)
   gives the "Privacy boundary" section's existing prose rule
   ("anything under `LOCAL_ONLY`" never leaves the device) a real type.
   `CloudContextSnapshot` gains an optional `context_facts` field, built
   only through `build_cloud_context_snapshot(..., context_facts=[(text,
   eligibility), ...])`, which keeps only `CLOUD_SAFE`-tagged facts
   (`filter_cloud_safe`) — never a raw memory query, never a whole file,
   never anything the caller tagged `LOCAL_ONLY`/`CLOUD_WITH_USER_APPROVAL`.
   No memory system exists yet (M5) — this is the boundary a future one
   will use, not a memory implementation.
5. **Not rebuilt in NeXa**: turn semantics, native speech-to-speech
   conversation, and Gemini's own interruption handling remain
   provider-owned, exactly as Decision C/F already say. The tool/capability
   boundary (Decision N) is unchanged — a cloud provider still never
   executes an action; NeXa Core's Capability + Permission authority
   remains the sole gate, once M4 exists.

### Preserved unchanged by Amendment 2

Everything in "Preserved unchanged by Amendment 1"; every M2.6B dual-pipeline
component (paused, not deleted); the candidate/reject ownership invariant
fix (a real, tested architectural correctness fix, independent of which
runtime is active); local voice (`nexa.voice`/`nexa.voice_tts`), untouched;
`ConversationPolicy` (`LOCAL_ONLY`/`CLOUD_PREFERRED`/`AUTO`); `Sulafat`;
XVF3800 AEC as the LOCAL voice's own accepted mechanism (M2.5B, unaffected —
Amendment 2 only changes which CLOUD path is production-default).

### Real-hardware acceptance (2026-09-16, same day)

`CloudRealtimeConversationAdapter`/`apps/nexa_cloud_voice_simple.py` passed
real-hardware acceptance (current reSpeaker mic, current separate USB
speaker, real Gemini, Sulafat, one `CLOUD_SAFE` context fact). Operator-
confirmed comparable to golden M2.6A: normal conversation, PL/EN +
language switching, no self-conversation, natural interruption with
correct recovery, `CloudContextSnapshot` context-fact integration all
PASS. **`Cloud realtime conversation baseline = ACCEPTED / FROZEN`** —
known non-blocking issue: occasional playback/stream continuity stutter
during longer assistant speech (backlog, not investigated). See
`docs/reports/R0071_golden_voice_recovery_and_boundary_20260916.md`.
