# R0030 — Cloud Realtime Voice: Research / Architecture

- **Date:** 2026-09-10
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **new branch M2.6 — Cloud Realtime
  Voice.** Research / architecture only. **No production `src/` change.**
- **Status:** **RESEARCH COMPLETE. GO for the M2.6A real-hardware spike.**
  Phase-0 fact corrections applied (see *PHASE 0 CORRECTIONS* below);
  M2.6A probe implemented + `--dry` validated; `google-genai 2.22.0`
  installed (research venv only, no tracked dep file changed); an
  authenticated Live-API connectivity smoke **passed** (449 ms handshake,
  no audio); operator-provided key stored outside the repo. **Awaiting the
  M2.6A operator live session** — see `R0031`. Not pushed.
- **Related:** `R0029` (M2.5B — production local barge-in, COMPLETE /
  OPERATOR-CONFIRMED 2026-09-10 — the frozen local baseline this builds
  *beside*), `ADR-0003` (realtime-voice foundation; D2 "one
  `ConversationSession`", D9 "LiveKit deferred", D11 "replaceable
  voice interfaces", compliance "no cloud credentials in the local M2
  path" — this branch deliberately opens that surface, so it needs its
  own ADR), `R0028` (M2.5A feasibility — the pattern this report follows),
  `R0009`/`R0027` (bilingual PL/EN + response-language authority),
  `docs/research/RESEARCH_POLICY.md`.
- **Supporting evidence:** `docs/research/m2_6_cloud_realtime_voice/`
  (`inspect_pipecat_gemini_live.py` + captured output).

---

## PHASE 0 CORRECTIONS (2026-09-10, before M2.6A)

Re-checked against **current official Google sources** on 2026-09-10, per
the "research wins over previous documentation" rule. **The statements in
this section supersede any conflicting text later in the report.**

### C1 — UK / EEA / Switzerland data treatment (was over-stated)

**VERIFIED (official, `https://ai.google.dev/gemini-api/terms`, fetched
2026-09-10):**

- **Unpaid Services:** *"Google uses the content you submit to the
  Services and any generated responses to provide, improve, and develop
  Google products and services"* including *"machine learning
  technologies."*
- **Paid Services:** *"Google doesn't use your prompts … or responses to
  improve our products, and will process your prompts and responses in
  accordance with the Data Processing Addendum."*
- **EEA / Switzerland / UK exception (verbatim):** *"If you're in the
  European Economic Area, Switzerland, or the United Kingdom, the terms
  under 'How Google uses Your Data' in 'Paid Services' apply to all
  Services, including Google AI Studio and unpaid quota in the Gemini
  API, even though they are offered free of charge."*
- **Separate API-Client restriction (verbatim):** *"You may use only Paid
  Services when making API Clients available to users in the European
  Economic Area, Switzerland, or the United Kingdom."*

**Correction.** The blanket claim *"free-tier conversations are used for
product improvement / human review, therefore M2.6A requires a paid tier
for privacy"* is **withdrawn**. The correct picture, keeping to what the
Terms say (no legal interpretation beyond that):

| Concern | For an operator **in** EEA/CH/UK | For an operator **outside** EEA/CH/UK |
|---|---|---|
| **Data-use treatment** of an internal/dev spike | Paid-Services data terms already apply on unpaid quota → inputs/outputs **not** used to improve Google products | Unpaid-quota inputs/outputs **are** used to improve Google products (and may be human-reviewed) |
| **Billing requirement** for the M2.6A spike | none for an internal spike (one operator, not "making an API Client available to users") | none for an internal spike |
| **Billing requirement** for a future NeXa cloud voice **made available to EEA/CH/UK users** | **Paid Services required** (Terms clause above) — an ADR-0004 / M2.6B item | Paid Services not mandated by this clause; still recommended |

**M2.6A decision (unchanged in effect, corrected in reasoning):** the
spike uses **only scripted, non-sensitive test conversation**, so it is
safe on either tier. The provided key is used as-is. Whether M2.6B needs
a paid key is an **ADR-0004** decision driven by (a) the operator's
region and (b) whether NeXa cloud voice will be "made available to users"
in EEA/CH/UK.

### C2 — Audio chunk size (was OPEN — now resolved)

**VERIFIED (`https://ai.google.dev/gemini-api/docs/live-api/best-practices`,
fetched 2026-09-10):** *"Send audio in chunks of 20ms to 40ms."* and
*"Don't buffer input audio significantly (such as 1 second) before
sending. Send small chunks (20ms - 100ms) to minimize latency."*

**M2.6A baseline = 20 ms.** 40 ms may be measured as a cheap comparison.
This is **no longer an OPEN QUESTION**; OPEN QUESTION #3 in R0030 is
closed.

### C3 — Session-resumption token validity (was "24 h vs 2 h" contradiction)

**VERIFIED (same best-practices page, 2026-09-10):** *"Resumption tokens
are valid for 2 hours after the last session terminates."*

**Correction.** The "resume within 24 hours" reading is **dropped** — no
current official Google page consulted on 2026-09-10 still states 24 h.
**CURRENT VERIFIED FACT: 2 hours.** OPEN QUESTION #4 and RISK R10 are
closed to "verify what a resumed native-audio session actually restores"
only.

Same page also VERIFIED: **connection lifetime ≈ 10 minutes**; without
compression, **audio-only sessions are limited to 15 minutes**
(audio-video to 2 minutes); **`contextWindowCompression` extends sessions
to unlimited duration**; **audio tokens accumulate at ≈ 25 tokens per
second**; **`GoAway` includes `timeLeft`** and the client *"should listen
for this message and use the `timeLeft` field to gracefully wrap up or
reconnect before the connection closes."*

### C4 — Current official pricing (was a "sources disagree" spread)

**VERIFIED (`https://ai.google.dev/gemini-api/docs/pricing`, fetched
2026-09-10) — `gemini-3.1-flash-live-preview`:**

| | Free tier | Paid tier |
|---|---|---|
| **Input** | Free of charge | **$0.75 / 1M text tokens**; **$3.00 / 1M audio tokens** (≈ **$0.005 / min** audio) |
| **Output** | Free of charge | **$4.50 / 1M text tokens**; **$12.00 / 1M audio tokens** (≈ **$0.018 / min** audio) |
| **Data used to improve products** | Yes | No |
| **Grounding w/ Google Search** | 5,000 free requests/month (shared across all Gemini 3.x models), then **$14 / 1,000** | same |

Combined with C3's **≈ 25 audio tokens/second**: ≈ 1,500 audio tokens per
minute each way. The earlier "≈ 600 in / ≈ 1,200 out tokens per minute"
figure (borrowed from OpenAI's ratio) is **replaced** by this official
rate. **No prompt caching** on this model (VERIFIED earlier) — every
reconnect re-seed is billed in full. The M2.6A probe's cost estimate uses
these official numbers.

### C5 — Polish pronunciation risk (retagged: external report, not platform fact)

The Polish-accent regression comes from a **Google AI Developers Forum**
thread (`.../177436`), not from the model specification. Google staff on
2026-08-18 replied only *"we've passed your feedback to the relevant
product teams"* — an acknowledgement of the report, **not** an official
confirmation that the regression is real, reproduced, or a known issue.

**Retag:** this is **EXTERNAL REPORTED RISK / COMMUNITY EVIDENCE**, not a
VERIFIED PLATFORM FACT. Everywhere R0030 (or CURRENT_STATE / ROADMAP)
implies Google has confirmed a Polish regression, read it as "one
external report, unverified by Google." **The authoritative answer for
NeXa is the real M2.6A operator test** — an operator listening to
`gemini-3.1-flash-live-preview` speak Polish and judging pronunciation /
accent / naturalness. It remains a **High**-priority risk to *test*; it
is no longer stated as a settled fact.

### C6 — CRITICAL architecture question missed in R0030: language routing vs. input-transcription timing

R0030's canonical-history + language-routing design assumed NeXa's
`ResponseLanguageResolver` can read Gemini's **input transcription** and
still be the language-routing authority *for the same response*. But a
native speech-to-speech model **may start generating and speaking before
the complete input transcription has reached NeXa** — in which case the
resolver cannot steer *that* response without deliberately adding
latency.

**This is now an explicit OPEN QUESTION that M2.6A must MEASURE**, not
hand-wave. The probe instruments the ordering on one monotonic clock:

```
LOCAL_VAD_START
LOCAL_VAD_EOT               (Silero end-of-turn → activity_end sent)
INPUT_TRANSCRIPTION_FIRST   (first serverContent.inputTranscription chunk)
INPUT_TRANSCRIPTION_FLUSHED (Pipecat flushes the aggregated user sentence)
FIRST_SERVER_CONTENT        (first serverContent of the reply)
FIRST_AUDIO_RECEIVED        (first TTSAudioRawFrame from Gemini)
FIRST_AUDIO_PLAYED          (BotStartedSpeakingFrame from the output transport)
```

Questions the spike answers **experimentally** (not decided here):

- **A.** Does the *complete* input transcription arrive **before** the
  first model audio? (i.e. is `INPUT_TRANSCRIPTION_FLUSHED` before
  `FIRST_AUDIO_RECEIVED`?)
- **B.** If not, can a text / instruction update sent *after* the
  transcription still steer the **same** in-flight response?
- **C.** How does Gemini behave with the global instruction *"respond in
  the language spoken by the user"* (the spike's system instruction)?
- **D.** Does PL → EN → PL switching work reliably on native Gemini
  language understanding **alone** (no NeXa STT)?
- **E.** How would NeXa **sticky** language commands ("From now on speak
  English") fit without a second STT?
- **F.** To keep strict `ResponseLanguageResolver` authority, what would
  it take — a local lightweight language-ID, a parallel whisper.cpp, a
  deliberate `activity_end` delay, or something else?
- **G.** What latency does each of those options add?

**M2.6A is allowed to temporarily let Gemini mirror the spoken language
natively** purely to assess cloud speech quality. The production
language-routing authority decision is an **ADR-0004** item, made *after*
these measurements — not now.

### C7 — Not a correction: connectivity confirmed

**MEASURED FACT (2026-09-10, this task):** the provided credential is
accepted, `gemini-3.1-flash-live-preview` exists and is reachable, the
Live API WebSocket opened and completed setup in **449 ms**, clean
disconnect, no secret leaked. (`m2_6a_connect_smoke.py`.) R0030's
"no cloud call was made" no longer holds — this one no-audio handshake
was made.

---

## FACT TAGGING

- **VERIFIED FACT** — confirmed in an official doc or by direct
  inspection of installed code, with the source recorded.
- **MEASURED FACT** — a number produced by running something on this
  hardware. The original report had none; *PHASE 0 · C7* adds one (a
  449 ms no-audio Live-API handshake). The audio-latency numbers are
  still M2.6A's job.
- **INFERENCE** — a conclusion the agent drew from verified facts.
- **DESIGN DECISION** — a choice this report proposes (not yet ratified;
  an ADR-0004 is owed before production).
- **OPEN QUESTION** — must be answered by the spike or a follow-up.

---

## EXECUTIVE SUMMARY

1. **Gemini Live is still the right first cloud provider.** It is a true
   native audio-to-audio model, an order of magnitude cheaper than OpenAI
   Realtime, has the widest language coverage, and Pipecat (the framework
   NeXa already runs, ADR-0003 D1) ships a mature `GeminiLiveLLMService`.
   The recorded model `gemini-3.1-flash-live-preview` is real and current.
   **INFERENCE:** freeze this one provider + model for the first
   implementation; do not survey further.

2. **One hard, provider-specific risk:** `gemini-3.1-flash-live-preview`
   native audio currently **speaks Polish with a strong English/American
   accent** — a regression vs `gemini-2.5` native audio, reported
   2026-08-07, acknowledged by Google 2026-08-18, **unresolved**
   (VERIFIED). NeXa is a bilingual PL/EN companion whose accepted local
   baseline has correct Polish (`pl_PL-gosia-medium`). **The spike's PASS
   gate must include an explicit operator judgement of Polish audio
   quality**, and cloud mode may have to ship EN-only first, or wait for a
   Google fix, or fall back to local for Polish turns.

3. **`ConversationSession` stays the single authority (ADR-0003 D2).** The
   cloud path is **not** a `ModelProvider` — that interface is a text
   stream (`generate() -> AsyncIterator[str]`) and Gemini Live is
   speech-to-speech with audio + transcription events inside the Pipecat
   pipeline. **DESIGN DECISION:** a *new* NeXa-owned boundary
   (`RealtimeVoiceProvider`), parallel to `ModelProvider`, that is handed a
   NeXa-derived `CloudContextSnapshot`, runs Gemini Live, and emits
   canonical turn events that NeXa writes back to the one
   `ConversationSession`. Canonical transcript is built from Gemini's
   **input/output transcription** streams, reconciled against what was
   actually played; raw cloud audio is **not** retained by default.

4. **Pipecat, wrapped and hardened (OPTION C), not raw and not
   hand-rolled.** `GeminiLiveLLMService` in our installed Pipecat **1.8.1**
   has two real, currently-unfixed gaps (VERIFIED by source audit):
   GitHub issue **#5465** — user audio / text / video are silently dropped
   with a bare `return` during the reconnect window (fix PR #5497 is
   **open, unmerged**); and **no `GoAway` handling at all** — reconnect is
   purely reactive after the ~10-minute socket actually errors. Both are
   tolerable for a short spike, unacceptable for an hours-long companion.
   A thin NeXa wrapper closes them; a direct-SDK rebuild throws away
   Pipecat's mature reconnect / session-resumption / activity-window /
   Gemini-2.5-vs-3.x handling for no benefit.

5. **Keep the XVF3800 AEC + local Silero VAD as the turn authority
   (HYBRID).** Stream the *echo-controlled* mic signal to Gemini, disable
   Gemini server VAD, drive Gemini's activity window from local Silero
   (Pipecat already does exactly this). NeXa remains the final authority
   over physical playback and over what enters canonical history.

6. **Recommended next task: M2.6A — minimal Gemini Live real-hardware
   spike.** Prove *only*: reSpeaker mic → existing AEC/local audio path →
   Gemini Live → native streamed cloud audio → existing speaker, plus
   latency + Polish-quality + event-ordering metrics. No memory, identity,
   router, tools or GUI. **Key/tier:** see *PHASE 0 CORRECTIONS · C1* —
   the spike runs on scripted non-sensitive phrases and is safe on either
   tier; whether M2.6B needs a paid key is an ADR-0004 decision driven by
   the operator's region and whether NeXa cloud voice is "made available
   to users" in EEA/CH/UK. An **ADR-0004** is owed before any production
   cloud code.

**GO / NO-GO: GO for the M2.6A spike. NO-GO for production cloud code,
the router, or ADR-0004 ratification until the spike has measured
end-to-end latency, the input-transcription-vs-first-audio ordering
(*PHASE 0 · C6*), and an operator has judged Polish audio quality.**

---

## SOURCES / VERSIONS / DATES

All URLs fetched **2026-09-10**.

### Official Google — Gemini Live API

| # | Source | Used for |
|---|---|---|
| G1 | `https://ai.google.dev/gemini-api/docs/live-api/capabilities` | audio formats, VAD config, interruption, transcription, context-window sizes, function calling |
| G2 | `https://ai.google.dev/gemini-api/docs/live-session` | ~10-min connection lifetime, 15-min / 2-min session caps, compression → unlimited, GoAway.timeLeft, session resumption, generationComplete vs turnComplete |
| G3 | `https://ai.google.dev/api/live` | WebSocket message types + field names (BidiGenerateContentSetup / ClientContent / RealtimeInput / ToolResponse; serverContent / toolCall / goAway / sessionResumptionUpdate / usageMetadata) |
| G4 | `https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview` | model ID, Preview status, 131,072 / 65,536 context, modalities, "synchronous only" tools, "caching not supported", `send_client_content` seed-only, migration from 2.5 |
| G5 | `https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk` | google-genai SDK surface (`client.aio.live.connect`, `send_realtime_input`, `send_client_content`, `session.receive()`) |
| G6 | `https://ai.google.dev/gemini-api/docs/pricing` (+ secondary pricing trackers) | pricing spread (see Cost) |
| G7 | `https://ai.google.dev/gemini-api/docs/rate-limits` | tier qualification (Tier 1 = billing on; Tier 2 = $100+3d; Tier 3 = $1000+30d); Live concurrency not published — check AI Studio |
| G8 | `https://discuss.ai.google.dev/t/polish-pronunciation-regression-in-gemini-3-1-flash-live-preview-native-audio-vs-2-5-native-audio/177436` | **Polish accent regression** (reported 2026-08-07; Google ack 2026-08-18; unresolved) |
| G9 | `https://discuss.ai.google.dev/t/.../gemini-3-1-flash-live-preview-first-audio-16-26-s-since-2026-09-05...` ; `.../persistent-high-pitched-dial-up-like-audio-distortion/...` ; `.../gemini-3-1-flash-live-preview-returns-quota-exceeded/...` | preview-model instability incidents (latency spikes, audio distortion, quota) |

**Privacy** — free vs paid data use: Google AI Studio / paid Gemini API
terms as summarised by multiple 2026 trackers (Meetily LLM-privacy page,
YingTu free-tier guide) — free tier inputs/outputs used to improve
products + human review; paid tier not used for training; EEA/CH/UK get
paid terms on all tiers. **OPEN QUESTION:** confirm current exact wording
against `https://ai.google.dev/gemini-api/terms` at spike time.

### Official Pipecat + upstream

| # | Source | Used for |
|---|---|---|
| P1 | installed `.venv/.../pipecat/services/google/gemini_live/llm.py` (**pipecat 1.8.1**, 2181 lines) | direct source audit — the authoritative statement of what *our* install does |
| P2 | `https://github.com/pipecat-ai/pipecat/issues/5465` | **OPEN** (created 2026-08-27); "GeminiLiveLLMService silently drops text, audio and tool results while reconnecting"; env = Pipecat main, Gemini 3.1 Flash Live Preview |
| P3 | `https://github.com/pipecat-ai/pipecat/pull/5497` | **OPEN, not merged** (created 2026-08-30); "fix(gemini-live): answer a tool call that outlived its session"; +16 tests `tests/test_gemini_live_reconnect.py`; also makes every send-guard log dropped messages |
| P4 | `https://github.com/pipecat-ai/pipecat/releases` | latest release = **v1.8.1** (~2026-08-27); no newer release; no Gemini-Live reconnect fix in any released version |
| P5 | `https://github.com/pipecat-ai/pipecat/issues/3350` (user transcription) ; `#3381` (interrupted handling / delayed interruptions) | older Gemini-Live issues; #3381's symptom is not present in 1.8.1 source (it *does* call `broadcast_interruption()` on `sc.interrupted`) |
| P6 | `https://docs.pipecat.ai/api-reference/server/services/s2s/gemini-live` | construction, `Settings`, `LLMContextAggregatorPair(realtime_service_mode=True)`, local-VAD via `GeminiVADParams(disabled=True)` + `vad_analyzer` |

### This repo (VERIFIED by inspection, tip `788a64d`)

`src/nexa/providers/base.py`, `src/nexa/conversation/{session,context,provider_window,turn,response_language}.py`,
`src/nexa/voice_conversation/adapter.py`, `src/nexa/voice/{runtime,bargein,interruption}.py`,
`src/nexa/voice_tts/{bargein_wiring,aec_reference}.py`, `docs/decisions/ADR-0003_realtime_voice_foundation.md`,
`docs/CURRENT_STATE.md`, `docs/ROADMAP.md`, `docs/reports/R0029_*.md`.

### Comparative (alternatives)

OpenAI Realtime / `gpt-realtime` pricing + transport: HackerNoon 2026
"OpenAI Realtime API Pricing in 2026", Forasoft "WebRTC/SIP/WebSocket in
2026", webscraft "GPT-Realtime-2 vs Gemini Live API 2026". Used only for
the *Rejected alternatives* section.

---

## CURRENT REPO BOUNDARIES

**VERIFIED** — the seams the cloud path must respect.

### `ConversationSession` — the one authority (`src/nexa/conversation/session.py`)

- `send(user_text, *, cancel_token, response_mode, response_language) ->
  AsyncIterator[str]` — the **one path**: append user turn → render
  provider messages (via `ConversationContext` **or** `ProviderWindow`) →
  `provider.generate(...)` → stream text → append assistant turn.
- `commit_interrupted_turn(spoken_text) -> InterruptedTurnOutcome` —
  `ROLLED_BACK_USER_TURN` (CASE A: nothing spoken → pop the orphan user
  turn) / `COMMITTED_SPOKEN_PREFIX` (CASE B: append assistant turn,
  `interrupted=True`, content = spoken prefix only) / `NOTHING_TO_COMMIT`.
- `history: tuple[ConversationTurn, ...]` — **canonical, complete, never
  bounded** (ADR-0003 D2). `_response_languages` — index-aligned
  wire-level metadata, *not* history, *not* memory.
- `provider_window: ProviderWindow | None` — when set, the *provider-facing*
  prompt is a bounded, prefix-stable window over `history`; canonical
  `history` is unchanged. **This is the existing precedent for a
  "derived, bounded, provider-facing view".**

### `ModelProvider` (`src/nexa/providers/base.py`)

- `generate(messages: list[ProviderMessage], options, *, cancel_token) ->
  AsyncIterator[str]` — **text in, text stream out.**
- `ModelUnavailableError` — "Callers must surface this explicitly and must
  never silently switch to a different model or provider."
- `CancelToken` — cooperative per-request cancel + completion signals
  (`cancel_observed`, `worker_stopped`, `wait_worker_stopped`).

**INFERENCE:** a speech-to-speech cloud session cannot implement
`ModelProvider` honestly — it produces audio and out-of-band transcription
events, owns its own I/O inside the Pipecat pipeline, and has no
`messages -> text` call shape. Forcing it into `ModelProvider` would be
the "second brain / hidden path" ADR-0003 D2 and AGENTS.md §3 forbid.

### `VoiceConversationAdapter` (`src/nexa/voice_conversation/adapter.py`)

Consumes `TranscriptionResult` / `CoalescedInterruptTurn`, runs
`ResponseLanguageResolver`, calls `session.send(...)`, streams tokens to
`_on_assistant_token`, and on barge-in calls `_commit_interrupted(...)` →
`session.commit_interrupted_turn(...)`. **Tightly bound to the
streaming-text contract** — the cloud path needs a sibling, not a
retrofit of this class.

### `VoiceRuntime._build_pipeline` (`src/nexa/voice/runtime.py`)

`transport.input()` → `_MicGateFrameProcessor` → *(optional
`BargeInController`)* → `_UtteranceCaptureFrameProcessor` →
`_VoiceStateFrameProcessor` → `extra_output_stages` → `transport.output()`,
on a `LocalAudioTransport`. `extra_output_stages` (M2.4) is the documented
insertion seam; `AecReferenceFeeder` (tees TTS PCM to `plug:respeaker` for
the XVF3800 far-end reference) rides there today.

### Barge-in stack (`src/nexa/voice/bargein.py`, `interruption.py`, `voice_tts/bargein_wiring.py`)

`InterruptionStateMachine` (IDLE/RESPONDING/INTERRUPT_CANDIDATE/
INTERRUPTING), `BargeInController` (Pipecat `FrameProcessor` after VAD,
uses `broadcast_interruption()`), `HalfDuplexGate`, capture-generation-
scoped timers (R0029 M2.5B.3 v2). This whole stack is designed around the
**local** streaming-TTS pipeline; cloud mode reuses the *concepts*
(interruption state, `commit_interrupted_turn`, AEC feed) but Gemini Live
does its own audio cancellation, so the wiring differs (see
*Audio/VAD/Barge-in*).

### ADR-0003 constraints this branch touches

- **D2:** no second conversation history / persona / model-or-provider
  decision point for voice. **Honoured** by the design below.
- **D9:** LiveKit deferred, not rejected — future *multi-device transport*.
  Cloud voice v1 does **not** bring LiveKit in (single Pi, Pipecat local
  transport still owns the mic/speaker).
- **D11:** STT/TTS behind NeXa-owned replaceable interfaces — extended
  here to a `RealtimeVoiceProvider` interface.
- **Compliance:** "no LiveKit Cloud credentials … in the local M2 path."
  Cloud voice **deliberately opens a cloud credential surface** → this is
  exactly why an **ADR-0004** is required before production, not just a
  report.

---

## HOW GEMINI LIVE WORKS

All **VERIFIED** from G1–G5 unless tagged. Field names quoted from G3
(WebSocket reference) / G5 (google-genai SDK).

### Protocol / session model

- **Transport:** one WebSocket, endpoint
  `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent`.
  Messages are JSON (`BidiGenerateContentClientMessage` /
  `…ServerMessage`). google-genai SDK: `async with
  client.aio.live.connect(model=…, config=LiveConnectConfig(…)) as
  session:` then `session.send_realtime_input(...)` /
  `session.send_client_content(...)` / `async for msg in
  session.receive():`.
- **First client message:** `BidiGenerateContentSetup` — `"model"`
  (required), `"generationConfig"`, `"systemInstruction"`, `"tools"`,
  `"realtimeInputConfig"`, `"sessionResumption"`,
  `"contextWindowCompression"`, `"inputAudioTranscription"`,
  `"outputAudioTranscription"`. Server replies
  `BidiGenerateContentSetupComplete`.
- **During the session** the client streams `BidiGenerateContentRealtimeInput`
  (`"audio"` Blob, `"text"`, `"activityStart"`, `"activityEnd"`,
  `"audioStreamEnd"`) and `BidiGenerateContentClientContent`
  (`"turns"`, `"turnComplete"`) — **G4: on Gemini 3.x `send_client_content`
  is restricted to initial context seeding only**; turns during the
  session go via `realtimeInput`. Tool results:
  `BidiGenerateContentToolResponse.functionResponses`.

### Audio formats + chunking

- **Input (VERIFIED G1):** raw **16-bit PCM, little-endian, 16 kHz, mono**,
  mime `"audio/pcm;rate=16000"`. Other rates are resampled server-side;
  16 kHz is native.
- **Output (VERIFIED G1):** raw PCM **24 kHz** in
  `serverContent.modelTurn.parts[].inlineData` (Pipecat emits it as
  `TTSAudioRawFrame(sample_rate=24000)`).
- **Chunk size (VERIFIED — *PHASE 0 · C2*, best-practices page,
  2026-09-10):** *"Send audio in chunks of 20ms to 40ms."* / *"Don't
  buffer input audio significantly (such as 1 second) before sending. Send
  small chunks (20ms - 100ms)…"* **M2.6A baseline = 20 ms** (≈ 640 bytes @
  16 kHz mono); 40 ms as a cheap comparison. No longer an OPEN QUESTION.

### Turn detection — three modes (VERIFIED G1)

1. **Server VAD (default):** `realtimeInputConfig.automaticActivityDetection`
   — `disabled` (default false), `startOfSpeechSensitivity`,
   `endOfSpeechSensitivity`, `prefixPaddingMs` (doc: ≥ 500 ms recommended),
   `silenceDurationMs` (doc: **< 500 ms causes "fragmented audio"**).
2. **Manual / locally-driven:** set `automaticActivityDetection.disabled =
   true`, then bracket each user turn with `activityStart` … `activityEnd`
   `RealtimeInput` messages. "The server acts immediately on your
   `activityEnd` signal with no additional wait."
3. **Hybrid:** local VAD drives `activityStart`/`activityEnd`; server VAD
   off. **This is what Pipecat does when `GeminiVADParams(disabled=True)`
   (P1 `_handle_user_started_speaking` / `_handle_user_stopped_speaking`
   send `ActivityStart()` / `ActivityEnd()` from the Silero-driven
   `UserStarted/StoppedSpeakingFrame`).**

### Interruption / barge-in (VERIFIED G1, P1)

- Server emits `serverContent.interrupted = true` on a detected barge-in;
  **"the ongoing generation is canceled and discarded"** server-side.
- **G1/P1 caveat:** the API does **not** emit reliable user-turn-start /
  -end signals — `serverContent.interrupted` is the only barge-in event;
  Pipecat's `GeminiLiveLLMService` explicitly **"does NOT emit
  `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`"** and tells
  pipelines to run local Silero for turn tracking.
- On `interrupted`, Pipecat 1.8.1 calls `broadcast_interruption()` (P1
  line ~1333) and `_handle_interruption()` pushes `TTSStoppedFrame`.

### Output-buffer cancellation

- Server-side discard happens on `interrupted`. **Local playback** is the
  client's responsibility — Pipecat's output transport cancels+recreates
  its audio task on the broadcast interruption (same mechanism NeXa's
  local barge-in already relies on, R0028/R0029). **INFERENCE:** NeXa
  keeps final authority over the speaker exactly as today.

### Transcription (VERIFIED G1, P1)

- Enable with `"inputAudioTranscription": {}` and
  `"outputAudioTranscription": {}` in setup.
- Server sends `serverContent.inputTranscription.text` (user speech) and
  `serverContent.outputTranscription.text` (model speech) as **small
  word/phrase chunks**. Pipecat aggregates into sentences with a 0.5 s
  flush timeout (P1 `_handle_msg_input_transcription` /
  `_transcription_timeout_handler`) and emits `TranscriptionFrame`
  (user) / `TTSTextFrame` + `LLMTextFrame` (bot).
- **P1 in-source note (VERIFIED):** output transcription "looks further
  ahead" than the audio actually spoken — *"on an interruption our
  recorded context will contain some text that was actually never
  spoken."* Directly relevant to canonical-transcript design (below).

### `generationComplete` vs `turnComplete` vs `interrupted` (VERIFIED G2)

- `generationComplete` — the model finished **generating** this response.
- `turnComplete` — the **conversational turn** concluded (turn passes back
  to the user).
- `interrupted` — user barge-in; current generation cancelled + discarded.
- Gemini 3.x may bundle `modelTurn` / transcription / `turnComplete` on a
  single message (P1 handles fields non-exclusively).

### Session lifetime / resumption / GoAway (VERIFIED G2, P1)

- **WebSocket connection: ~10 minutes**, then the socket closes.
- **Session (no compression):** audio-only max **15 min**, audio+video
  max **2 min**. **With `contextWindowCompression` (slidingWindow +
  `triggerTokens`): unlimited** session duration.
- **Session resumption:** `sessionResumption: {handle}` in setup; server
  streams `sessionResumptionUpdate {newHandle, resumable}`; on reconnect,
  pass the last `newHandle` and the server restores context. **VALIDITY
  (VERIFIED — *PHASE 0 · C3*, best-practices page 2026-09-10):**
  *"Resumption tokens are valid for 2 hours after the last session
  terminates."* (The "24 hours" reading is withdrawn.) Still open: what a
  resumed native-audio session actually restores.
- **`GoAway {timeLeft}`** — server's advance warning that it will close
  the connection (as `ABORTED`) in `timeLeft`. Intended for a **proactive**
  reconnect. **Pipecat 1.8.1 does NOT handle `GoAway`** (VERIFIED, spike):
  it only reconnects *after* the socket errors.

### Context window / compression / tokens (VERIFIED G1, G4)

- `gemini-3.1-flash-live-preview`: **131,072 input / 65,536 output**
  tokens. (Other Live models: 32k. Native-audio 2.5: 128k.)
- `contextWindowCompression.slidingWindow` with `triggerTokens` (default
  ≈ 80 % of the window) transparently compresses older context so the
  session can run indefinitely — at the cost of the model losing verbatim
  old context (like NeXa's own `ProviderWindow` reset, conceptually).
- `usageMetadata`: `promptTokenCount`, `responseTokenCount`,
  `totalTokenCount`, `cachedContentTokenCount`, plus per-modality
  breakdowns (audio-token counts) — Pipecat maps these to
  `LLMTokenUsage` (P1 `_handle_msg_usage_metadata`).

### Billing implications of long sessions (INFERENCE from G1/G4/G6)

- Native audio tokens accrue continuously while streaming (input **and**
  output). A long session's `promptTokenCount` **compounds** every turn
  because the whole running context is re-charged as prompt each turn.
- **G4: caching is NOT supported on `gemini-3.1-flash-live-preview`** — so
  the implicit-prompt-cache discount NeXa relies on locally does **not**
  apply; every reconnect re-seed is fully re-charged.
- `contextWindowCompression` bounds the compounding but adds its own
  processing. **OPEN QUESTION:** measured cost of a 30-minute PL/EN
  session with compression on — spike metric.

### Function calling (VERIFIED G1, G4, P1)

- Tools declared in `BidiGenerateContentSetup.tools`; server emits
  `toolCall.functionCalls`; client replies
  `BidiGenerateContentToolResponse.functionResponses`;
  `toolCallCancellation.ids` cancels outstanding calls.
- **Gemini 2.5:** supports `behavior: NON_BLOCKING` + `scheduling`
  (`INTERRUPT` / `WHEN_IDLE` / `SILENT`) — the model keeps talking while a
  tool runs.
- **`gemini-3.1-flash-live-preview`: SYNCHRONOUS ONLY** (G4 "Async
  function calling unsupported; synchronous only"; P1 `_supports_
  non_blocking_tools = not _is_gemini_3`, with an in-source `logger.error`
  if a non-blocking tool is registered on a Gemini-3 model). The model
  **blocks** waiting for every tool result.

### Initial history seeding + incremental updates (VERIFIED P1, G4)

- Seed at connect via `send_client_content(turns=[…], turn_complete=…)`.
  Pipecat's `_create_initial_response` has elaborate Gemini-2.5-vs-3.x
  handling (2.5 needs a trailing user turn + a forced `turn_complete` on
  reconnect to recall seeded history; 3.x merges cleanly).
- **G4: on 3.x, `send_client_content` is seed-only** — you cannot keep
  appending history mid-session that way; further turns are `realtimeInput`
  audio/text and the server maintains its own running context.
- On **reconnect**, Pipecat re-seeds the whole `LLMContext` via
  `send_client_content` if it has no resumption handle (P1
  `_create_initial_response(for_reconnect=True)`).

---

## WHAT GEMINI REMEMBERS VS WHAT NeXa OWNS

**DESIGN DECISION** — three layers, no overlap.

| Layer | Owner | Lifetime | Contents |
|---|---|---|---|
| **Canonical conversation** | NeXa `ConversationSession.history` | the whole conversation, unbounded | verbatim user + assistant turns, `interrupted` flags, per-turn language metadata (side-list) |
| **Gemini Live session context** | Google (server-side), seeded + steered by NeXa | one Live session (≤ minutes to hours w/ compression); **discarded on final disconnect** | recent turns as the server holds them, native audio state, tool state; **compressed / lossy over time** |
| **NeXa durable memory** | NeXa (M5, not built) | user-owned, persistent | facts, preferences, identity — **never** delegated to a provider |

**Rules (DESIGN DECISION):**

1. Gemini Live context is **working memory only**. It is *derived from*
   and *subordinate to* `ConversationSession.history`. NeXa never reads
   canonical facts back *out* of Gemini.
2. On every (re)connect, NeXa re-derives a fresh `CloudContextSnapshot`
   from *current* canonical history. A stale Gemini session or resumption
   handle is **never** trusted to still match reality (see
   *Local ⇄ Cloud switch*, case H).
3. When the Live session ends, its context is gone and that is **fine** —
   the canonical transcript is already in `ConversationSession`.

---

## CANONICAL HISTORY DESIGN

**DESIGN DECISION.** Question 2, point by point.

### What gets written to `ConversationSession` during cloud voice

Exactly the same shape as local voice: alternating `ConversationTurn`
(USER / ASSISTANT), plus the index-aligned response-language side-list,
plus `interrupted=True` on a barge-in-truncated assistant turn. **No new
fields, no audio, no provider metadata in `history`.**

A small **additive** method is required (not a `send()` change):

```
ConversationSession.append_external_turn(
    role: Role,
    content: str,
    *,
    interrupted: bool = False,
    response_language: str | None = None,
) -> None
```

— appends one already-finalised turn produced *outside* the `send()`
streaming loop (i.e. by the cloud provider). Typed chat and local voice
never call it; `send()` and `commit_interrupted_turn()` are byte-for-byte
unchanged. (Name/'shape TBD in ADR-0004 — it may instead be a pair
`begin_external_turn` / `complete_external_turn` if we want the user turn
recorded before the assistant reply streams.)

### How we obtain canonical USER text

From `serverContent.inputTranscription` (enabled in setup), aggregated to
a sentence/utterance by Pipecat and delivered as a `TranscriptionFrame`.
**INFERENCE:** authoritative *enough* for canonical history — it is
Google's own STT of the exact echo-controlled audio we sent, and it is
what the model actually acted on. It will not always match a local
whisper.cpp decode; that is acceptable (they are different engines) and
we do **not** run local STT in parallel in cloud mode.

### How we obtain canonical ASSISTANT text

From `serverContent.outputTranscription`, aggregated by Pipecat
(`TTSTextFrame`). **Caveat (VERIFIED P1):** output transcription can run
*ahead* of the audio actually spoken. So:

- On a **clean** turn (`turnComplete`, no `interrupted`): the full
  aggregated output transcription is the canonical assistant turn.
- On an **interrupted** turn: the output transcription may include text
  never voiced. We must **truncate to what was actually played** — see
  next.

### "How do we know what audio was actually spoken?" + interrupted turns

**DESIGN DECISION** — reuse NeXa's existing local pattern
(`SpokenTextTracker` high-water mark, R0028/R0029), adapted:

- Track a **spoken-audio high-water mark** in cloud mode from the
  `TTSAudioRawFrame`s that actually reached the output transport
  (bytes → ms at 24 kHz), and correlate it to the output-transcription
  stream position (transcription sentences are time-orderable against the
  audio they narrate).
- On `serverContent.interrupted`: canonical ASSISTANT turn =
  output-transcription text **up to the high-water mark**, committed via
  `commit_interrupted_turn(spoken_prefix)` → `COMMITTED_SPOKEN_PREFIX`
  (`interrupted=True`), or `ROLLED_BACK_USER_TURN` if nothing was voiced.
- The unspoken remainder is **discarded**, never stored — identical
  semantics to local barge-in.

**OPEN QUESTION:** the transcription-to-audio time alignment is
approximate. The spike must measure how far the output transcription
over-runs the played audio on a real barge-in, and whether a
sentence-granular high-water mark is precise enough (it is for local;
Gemini's look-ahead may be larger).

### "Gemini transcript differs slightly from the audio"

**DESIGN DECISION:** the **transcription stream is canonical text**; the
audio is the *rendering*. If they differ (accent artefacts, homophones),
we store the transcription. Rationale: (a) it is what the model believes
it said and will condition future turns on; (b) it is deterministic text
we can re-seed on reconnect; (c) NeXa never replays the audio. This
mirrors local voice, where canonical history stores planner/LLM text, not
a re-STT of Piper's output.

### Retain raw cloud audio? — **NO (default).**

**DESIGN DECISION.** Do not persist inbound or outbound cloud audio.
Reasons: privacy-first (raw voice is the most sensitive artefact),
storage, and no downstream consumer (canonical history is text; M5 memory
is text). A **debug-only**, explicit, off-by-default, time-boxed capture
(local file, retained N minutes, operator-triggered) is permitted for the
spike and for incident diagnosis — never a default, never uploaded.

### Language metadata

- Cloud mode still runs NeXa's `ResponseLanguageResolver` on the
  **input transcription** to decide the reply language and the sticky
  preference (R0027 semantics unchanged), and passes that language into
  the `CloudContextSnapshot` system instruction ("respond in Polish" /
  "respond in English").
- The per-turn resolved language is written to the index-aligned
  `_response_languages` side-list via `append_external_turn(...,
  response_language=…)` — exactly as local voice does.
- **INFERENCE:** the LLM (cloud or local) is still never the
  language-routing authority (ADR-0003 Amendment 1) — NeXa decides,
  Gemini is *told*.
- **RISK:** Gemini may not honour the language instruction as tightly as
  the local `language_directive`, and the Polish-accent regression (G8) is
  a *rendering* problem the resolver cannot fix.

### `interrupted=True` semantics mapping

Local: `INTERRUPTED_WIRE_SUFFIX (" […]")` is appended to the interrupted
assistant turn **at wire-build time only** (deterministic, KV-cache
stable). Cloud: the same canonical `interrupted=True` turn is produced;
when NeXa builds the *next* `CloudContextSnapshot` seed it appends the
same `" […]"` marker to that turn's text so Gemini knows the prior answer
was cut off. Canonical `ConversationTurn.content` stays clean, as today.

---

## LOCAL ⇄ CLOUD SWITCH DESIGN

**DESIGN DECISION.** Question 3.

### Policy vs active provider — two separate things

```
ConversationPolicy   (user preference, persisted by NeXa)
    AUTO | LOCAL_ONLY | CLOUD_PREFERRED

active_provider       (runtime state, chosen by NeXa's router)
    LOCAL | CLOUD
```

- `policy = LOCAL_ONLY`  → `active_provider` is always `LOCAL`. Cloud code
  is never reached. **This is the guarantee that NeXa runs cloud-free.**
- `policy = AUTO`        → router picks: `CLOUD` when reachable + healthy +
  (optional) task suggests it; else `LOCAL`. Silent, automatic fall back
  to `LOCAL` on any cloud failure.
- `policy = CLOUD_PREFERRED` → router uses `CLOUD` whenever it can
  connect; falls back to `LOCAL` on failure and **retries cloud** on the
  next turn / on a backoff timer.

### Internal command — NeXa executes the switch, not Gemini

A recognised natural-language intent (from a small local matcher, *or*
later from the model as a classified intent) produces an **internal**
command that only NeXa's router consumes:

```
SetConversationPolicy(mode: ConversationPolicy, *, reason: str)
```

Phrase → mode (initial matcher, extends `detect_language_request`
pattern):

| Utterance (PL / EN) | Command |
|---|---|
| "Przełącz na chmurę." / "Use the cloud." | `SetConversationPolicy(CLOUD_PREFERRED)` |
| "Rozmawiaj lokalnie." / "Wróć lokalnie." / "Talk locally." | `SetConversationPolicy(LOCAL_ONLY)` |
| "Używaj najlepszego trybu." / "Use the best mode." | `SetConversationPolicy(AUTO)` |

**Rule:** the cloud model may *classify the intent* (return "user wants
cloud"); it **must never** perform the switch, hold the policy, or know
the other provider exists beyond that. The policy lives in NeXa core
state and (later) is persisted with NeXa's other user preferences.

A separate lower-level `SetActiveConversationProvider(LOCAL|CLOUD,
reason)` exists for the router's own use (fallback, health) and for a
debug/operator override — it does **not** change the user's `policy`.

### Switch mechanics (within ONE `ConversationSession`)

The `ConversationSession` instance **never changes** across a switch — it
is the handoff medium. What changes is which *voice conversation stage* is
active in the pipeline:

- **LOCAL:** `VoiceConversationAdapter` + `session.send()` + Piper, as
  today (R0029, frozen).
- **CLOUD:** `CloudVoiceConversationAdapter` + `RealtimeVoiceProvider`
  (Gemini Live) inside the pipeline; canonical turns land via
  `append_external_turn` / `commit_interrupted_turn`.

**DESIGN DECISION:** simplest correct v1 = **rebuild the conversation
stage on switch** (tear down one adapter+stage, stand up the other),
keeping transport / mic-gate / AEC feed / Silero VAD up. A hot in-place
swap is a later optimisation. A switch is only honoured at a **turn
boundary** (never mid-user-utterance, never mid-assistant-audio unless
forced — see F).

### The eight cases

| Case | Behaviour (DESIGN DECISION) |
|---|---|
| **A. LOCAL → CLOUD after 20 local turns** | At the next turn boundary: build a `CloudContextSnapshot` from canonical history (bounded to the last *N* entries — see snapshot section), connect Gemini, seed via `send_client_content`. First cloud reply is history-aware. The 20 local turns stay canonical; only a bounded tail is shown to Gemini. |
| **B. CLOUD → LOCAL after 20 cloud turns** | At the next turn boundary: finalise any open cloud turn to canonical history, `_disconnect` Gemini, activate the local adapter. `session.send()`'s next `ConversationContext` / `ProviderWindow` render includes those 20 cloud turns verbatim (they are canonical) — the local model gets full continuity. **RISK:** a cold local prefill of a 20-turn tail (`ProviderWindow` may fire a `keep_entries=0` reset) — acceptable, it is one ordinary reset turn (R0029 M2.5B.2). |
| **C. CLOUD disconnects unexpectedly** | Pipecat reconnects (≤ 3 tries) with the resumption handle; NeXa shows a brief "reconnecting" state. If it recovers within *T* (spike to set, ~2–5 s) the user may not notice. If it fails: router auto-switches `active_provider = LOCAL` (policy unchanged), the current answer is committed as `interrupted=True` up to what was spoken, the user's *next* turn is answered locally. **Never** lose canonical history — every finalised turn is already in `ConversationSession`. |
| **D. Internet disappears** | Same as C but the reconnect fails fast. `AUTO` / `CLOUD_PREFERRED` → seamless local fallback; `LOCAL_ONLY` never had cloud up. When connectivity returns, `CLOUD_PREFERRED` retries on the next turn; `AUTO` retries per the router's policy. |
| **E. Gemini rate-limit / quota / provider error** | Treated as a cloud failure = case C/D path (auto local fallback). A `429` / quota error is surfaced in telemetry (never silently absorbed — AGENTS.md §3.2) and backs off cloud retries. `ModelUnavailableError`-equivalent is raised inside the cloud provider and caught by the router, **not** by making up an answer. |
| **F. User says "LOCAL ONLY" while cloud audio is playing** | Honour immediately: NeXa stops local playback now (existing barge-in playback-stop path), sends interruption to Gemini, commits the spoken prefix as `interrupted=True`, `_disconnect`s Gemini, sets `policy = LOCAL_ONLY` + `active_provider = LOCAL`. The command utterance itself is transcribed locally (mic path is unchanged) and, being a pure policy command, is **not** sent as a conversational turn (same "command, not content" rule as `ResponseLanguageResolver`). |
| **G. Back to cloud after many local turns** | Case A again: fresh `CloudContextSnapshot` from *current* canonical history (now longer), fresh connect + seed. No attempt to reuse an old Gemini session or handle from before the local stretch. |
| **H. Old Gemini session/handle exists but canonical history changed while cloud was inactive** | **The resumption handle is discarded.** NeXa tags every minted handle with the canonical-history length/sequence at mint time; on reconnect it compares to *current* canonical length. If it advanced (local turns happened, or an edit) → **do not resume**, open a fresh session and seed from a fresh snapshot. A handle is only used for a *pure transport blip* where canonical history is byte-identical to when the handle was minted. **This is the core guarantee that the cloud provider never resumes stale context as if nothing changed.** |

---

## CLOUD CONTEXT SNAPSHOT

**DESIGN DECISION.** Question 4. Modelled on the existing `ProviderWindow`
(a bounded, prefix-stable, provider-facing *view derived from* canonical
history) — but **smaller and privacy-filtered**, because it is re-sent on
every (re)connect, is charged at full price (no caching on 3.x, G4), and
crosses a trust boundary.

```
CloudContextSnapshot  (derived, never authoritative)
├─ system_instruction   : the MINIMAL cloud instruction (see next section)
├─ response_language     : "pl" | "en"  (from ResponseLanguageResolver)
├─ recent_turns          : last N canonical entries (default N ≈ 8–12,
│                          i.e. 4–6 exchanges), verbatim, with " […]" on
│                          interrupted assistant turns
├─ current_task?         : one short line, only if a task is active
├─ device_context?       : coarse only ("voice, headless Pi, near-field
│                          mic"), only if it changes model behaviour
├─ retrieved_memory?     : [] until M5; then only explicitly-retrieved,
│                          user-scoped facts relevant to THIS turn
└─ tool_surface?         : [] until a tools milestone; then only the
                           declarations for tools allowed in this context
```

### Always sent

- `system_instruction` (minimal), `response_language`, `recent_turns`
  (bounded tail).

### Sent only when relevant

- `current_task`, `device_context`, `retrieved_memory`, `tool_surface`.
  Each is opt-in per turn and derived from NeXa state; absent by default.

### Maximum sensible size

**DESIGN DECISION:** target ≤ ~1,500 tokens for the seed (≈ 6 exchanges +
instruction). Rationale: keeps first-connect latency and per-reconnect
cost low; the model's server-side context grows naturally from there
during the session; on the next reconnect we re-seed a *fresh* bounded
tail, not the accumulated session. Hard ceiling ~4k tokens.

### Privacy rules — what MUST NEVER be sent automatically

- Full lifetime conversation history (only the bounded recent tail).
- Any Personal Vault / durable-memory content that was not *explicitly
  retrieved for this turn* (M5 concern; until then: nothing).
- Credentials, API keys, tokens, file paths, other users' data.
- Precise location / device identifiers / network details — coarse
  `device_context` only, and only when it changes behaviour.
- Anything the user marked private / off-the-record.
- Raw audio (see canonical-history section).

**INFERENCE:** the snapshot is a *projection* of NeXa state through a
privacy filter, exactly as `ProviderWindow.render()` is a projection of
history through the wire-format rules. The filter is a NeXa-owned,
testable function — deterministic, no model in the loop.

### User control

`policy = LOCAL_ONLY` disables the snapshot entirely. A future setting
("what may NeXa send to the cloud") tightens `recent_turns` N,
`device_context`, and memory retrieval. Default is conservative.

---

## SYSTEM INSTRUCTION / IDENTITY

**DESIGN DECISION.** Question 5. **Do not put NeXa's identity, persona,
backstory, or memory into Google.**

### The minimal cloud system instruction (shape, not final wording)

> You are the live voice for an assistant called NeXa during this session.
> Speak naturally and concisely, in {response_language}. You are not the
> owner of the user's identity, long-term memory, preferences, or
> permissions — the host system holds those and will give you what you
> need each turn. Do not claim persistent memory of past sessions. If
> asked to take an action on a device, describe what you would do; the
> host decides whether to run it.

That is roughly 60–90 tokens. It tells the model **how to behave as the
current engine**, and explicitly **denies it the authoritative roles**.

### Where each thing lives (no duplicated authority)

| # | Layer | Holds | Never holds |
|---|---|---|---|
| **A. NeXa Core state** | identity, personality, values, `ConversationPolicy`, permissions, action policy, canonical history, device capability registry | — |
| **B. Cloud system instruction** | how to *act as the voice* this session: tone, brevity, language, "you are not the memory/identity authority", tool-use etiquette | identity, memory, persona detail, policy, user data |
| **C. `CloudContextSnapshot`** | recent turns, current language, current task, (later) retrieved memory + allowed tools | lifetime history, vault, credentials |
| **D. NeXa durable memory (M5)** | user-owned facts/preferences, persistent | — (and it is never *delegated* to a provider; only *queried* into C) |

**INFERENCE:** NeXa's full local persona/system prompt (Polish-language,
detailed) stays **local-only**. The cloud instruction is a deliberately
thin "role card". If the model's cloud-mode personality feels different
from local-mode, that is a tuning item for B + C, **not** a reason to ship
the identity to Google.

---

## PIPECAT vs DIRECT GOOGLE SDK DECISION

**DESIGN DECISION: OPTION C — use Pipecat's `GeminiLiveLLMService`,
wrapped in a thin NeXa hardening layer.** Ratify in ADR-0004.

### The three options

| | Approach | Verdict |
|---|---|---|
| **A** | Use `GeminiLiveLLMService` as-is | **Rejected.** Ships issue #5465 (silent drop on reconnect) and *no* `GoAway` handling into an hours-long companion. |
| **B** | Build a NeXa `RealtimeVoiceProvider` directly on `google-genai` Live API | **Rejected for v1.** Re-implements, with no benefit: reconnect + session-resumption, the local-VAD activity-window contract, transcription sentence-aggregation, Gemini-2.5-vs-3.x seed quirks, tool-call plumbing, metrics. Diverges from the framework ADR-0003 D1 standardised on. Reconsider only if Pipecat proves unfixable. |
| **C** | `GeminiLiveLLMService` **+** a NeXa wrapper that (1) buffers user audio/text while `not _ready_for_realtime_input` and replays on ready, (2) handles `GoAway` with a proactive reconnect, (3) exposes the NeXa-canonical event surface, (4) enforces the `CloudContextSnapshot` seed | **Chosen.** Keeps Pipecat's mature core; closes the two known gaps at the NeXa boundary; upstreamable later. |

### Why C over B, concretely

Pipecat 1.8.1's `GeminiLiveLLMService` (P1, 2181 lines) already does, and
we would otherwise rewrite:

- reconnect with backoff + failure ceiling (`MAX_CONSECUTIVE_FAILURES=3`,
  `CONNECTION_ESTABLISHED_THRESHOLD=10 s`);
- `sessionResumption` (`SessionResumptionConfig(handle=…)`,
  `_handle_msg_resumption_update`);
- reconnect **re-seed** of the whole context when there is no handle
  (`_create_initial_response(for_reconnect=True)`), with distinct
  Gemini-2.5 vs 3.x code paths for the documented audio-input/history
  recall quirk;
- local-VAD activity window: `activity_start` / `activity_end` +
  `user_audio_preroll_buffer` to recover speech onset;
- input-transcription sentence aggregation with a 0.5 s flush;
- `serverContent` field-bundling for Gemini 3.x;
- `interrupted` → `broadcast_interruption()`;
- `usageMetadata` → `LLMTokenUsage`.

### What the NeXa wrapper must add (the two gaps + the boundary)

1. **Not-ready send buffer (issue #5465).** VERIFIED (spike): in 1.8.1
   `_send_user_audio`, `_send_user_text`, `_send_user_video` all
   `return` silently when `not self._ready_for_realtime_input` — i.e. for
   the entire reconnect window. Pipecat's `_user_audio_preroll_buffer`
   only recovers the *speech onset* in local-VAD mode; sustained audio
   during a reconnect is lost. **Wrapper:** intercept the frames upstream
   of `GeminiLiveLLMService`, and while a "cloud not ready" flag is set,
   hold user audio in a bounded ring buffer (drop-oldest, cap ~3–5 s) +
   queue text, then flush on ready. Track PR **#5497** for an upstream
   fix and drop the wrapper piece when it lands + releases.
2. **`GoAway` proactive reconnect.** VERIFIED (spike): 1.8.1 has **zero**
   `GoAway` handling. **Wrapper:** since the Pipecat service does not
   surface `GoAway`, either (a) contribute a small upstream patch that
   emits a `GoAway` event, or (b) run a **connection-age timer** (~9 min,
   under the ~10-min limit) that triggers `_reconnect()` proactively
   during a silence. (a) is cleaner; (b) works with zero upstream
   changes. Decide in ADR-0004.
3. **The NeXa-canonical event surface.** The wrapper translates Pipecat
   frames → NeXa events: `on_user_turn(text, language, t)` (from
   `TranscriptionFrame` + resolver), `on_assistant_turn(text, language, t,
   interrupted)` (from aggregated `TTSTextFrame` + the spoken-audio
   high-water mark on `turnComplete` / `interrupted`),
   `on_connection_state(DISCONNECTED|CONNECTING|READY|RECONNECTING|FAILED)`.
   NeXa writes these to the one `ConversationSession`.
4. **Seed enforcement.** The wrapper builds the `LLMContext` Pipecat
   needs *from* the `CloudContextSnapshot` (never lets Pipecat's
   context-aggregator accumulate an independent history — it runs with
   `realtime_service_mode=True` and NeXa keeps the snapshot bounded).

### Dependency

`pipecat-ai[google]` (adds `google-genai`) — **not installed today**
(VERIFIED). Adding it is an ADR-0004 decision (new dependency + cloud
credential surface). It must not change the local path's behaviour
(import-guarded, cloud stage only constructed when `policy != LOCAL_ONLY`
and a key is configured).

---

## PIPECAT #5465 / RECONNECT AUDIT

**VERIFIED** — GitHub + direct source inspection of the installed file,
2026-09-10. Spike: `docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live.py`.

### Issue #5465 — status TODAY

| Question | Answer |
|---|---|
| Open or closed? | **OPEN** (created 2026-08-27). |
| Fix PR? | **#5497** — "fix(gemini-live): answer a tool call that outlived its session" — **OPEN, NOT merged** (created 2026-08-30). +16 tests `tests/test_gemini_live_reconnect.py`; also makes all send-guards log dropped messages (once per outage for media). |
| Affected versions? | Pipecat **main** per the report; env "Gemini 3.1 Flash Live Preview". Latest release is **v1.8.1** (~2026-08-27) — **no released version contains a fix**. |
| Does our installed Pipecat 1.8.1 contain the bug? | **YES.** Spike CONFIRMED all three silent guards present. |
| Can audio be silently dropped while not ready? | **YES.** `_send_user_audio` bare-`return` on `not self._ready_for_realtime_input` (P1 ~L1478–1484). The `_user_audio_preroll_buffer` recovers only the onset in local-VAD mode. |
| Can text be silently dropped? | **YES.** `_send_user_text` bare-`return` (P1 ~L1538–1539). |
| Can tool responses be silently dropped? | **YES, and it is the worst case** — a tool call that outlived the session is never answered, so the model waits forever and the bot goes silent for the turn (this is exactly what #5497 targets). |
| What happens during reconnect? | `_reconnect()` → `_disconnect()` (sets `_ready_for_realtime_input = False`, closes the socket) → `_connect(session_resumption_handle=…)`. Between those, every `_send_user_*` drops. On the new session: with a handle, server restores context and `_ready…` flips true; without a handle, `_create_initial_response(for_reconnect=True)` re-seeds the whole context, *then* `_ready…` flips true. |
| What does Pipecat queue vs discard? | **Queues:** nothing on the send side except the rolling audio pre-roll (onset only). **Discards:** user audio/text/video/tool-results sent while not ready. `_handle_send_error` treats a send error as **fatal** (`push_error`). |
| Other current Gemini Live reliability issues? | **#3350** (user transcription not returned) and **#3381** (interrupted not handled → delayed interruptions) are older; #3381's symptom is **absent** in 1.8.1 (it *does* call `broadcast_interruption()` on `sc.interrupted`). No `GoAway` handling anywhere (spike: ABSENT). Forum reports (G9) of preview-model first-audio latency spikes, audio distortion, and quota errors are **provider-side**, not Pipecat. |

### Reconnect flow — verdict

**INFERENCE:** for the **M2.6A spike** (short sessions, no tools,
operator present) the #5465 window is unlikely to fire and its blast
radius is "a few hundred ms of dropped audio during a rare reconnect" —
acceptable, *if* we log it. For **production** it is not acceptable
(hours-long sessions → many ~10-min reconnects) and the wrapper's
not-ready send buffer + proactive `GoAway`/age-timer reconnect are
**required** before M2.6B.

---

## AUDIO / VAD / BARGE-IN ARCHITECTURE

**DESIGN DECISION.** Question 7. **HYBRID — option C.**

### The choice

| Option | Verdict |
|---|---|
| A. Gemini automatic activity detection only | **Rejected.** Throws away the XVF3800 hardware AEC + Silero tuning that R0028/R0029 proved necessary on this route (bare-route self-echo tripped VAD 14/14). Server VAD would run on our echo-controlled stream but we lose the local, sub-40 ms media-stop authority and the barge-in state machine. |
| B. Local Silero only + `activity_start`/`activity_end`, no server VAD | **Chosen baseline.** Reuses the working local turn detector; Gemini is *told* when turns start/end. Pipecat already implements exactly this when `GeminiVADParams(disabled=True)`. |
| C. Hybrid (local Silero drives turns; keep server VAD as a cross-check) | **Chosen, as B + observability.** Run B; additionally log `serverContent.interrupted` and any server turn signals to compare against local Silero during the spike. If local and server disagree materially, revisit. Do **not** let both act. |

### The audio path (unchanged local ownership)

```
reSpeaker mic ─▶ XVF3800 hardware AEC ─▶ (echo-controlled signal)
   ─▶ Pipecat LocalAudioTransport.input()
   ─▶ _MicGateFrameProcessor  (NeXa)
   ─▶ Silero VAD               (NeXa — turn authority)
   ─▶ [cloud stage] GeminiLiveLLMService wrapper
         · streams the echo-controlled PCM to Gemini @16kHz
         · sends activity_start/activity_end from Silero turn frames
   ─▶ ... (bot audio comes back) ...
   ─▶ TTSAudioRawFrame @24kHz
   ─▶ AecReferenceFeeder  (NeXa — tee to plug:respeaker far-end ref)
   ─▶ LocalAudioTransport.output()  ─▶ speaker
```

**VERIFIED / DESIGN DECISION:** the signal streamed to Gemini is the
**post-XVF3800 echo-controlled** mic signal — never raw mic. The
`AecReferenceFeeder` continues to feed the array its far-end reference
from the *cloud* bot audio (same tee, different source), so barge-in
during cloud playback is as safe as during local playback (R0028's whole
finding).

### Ownership table

| Responsibility | Owner |
|---|---|
| "user started speaking" | **NeXa** — local Silero VAD |
| "user stopped speaking" | **NeXa** — local Silero VAD |
| notify Gemini a turn started / ended | NeXa wrapper → `activity_start` / `activity_end` |
| cancel cloud output (server-side) | Pipecat `GeminiLiveLLMService` on `broadcast_interruption()` → server discards on `interrupted` |
| stop local playback immediately | **NeXa** — existing output-transport interruption path (sub-40 ms, R0028 SPIKE B-live) |
| clear local playback buffer | **NeXa** — existing path |
| decide the turn was an interruption | **NeXa** — `InterruptionStateMachine` (reused concept) driven by local Silero, cross-checked against `serverContent.interrupted` |
| commit the interrupted assistant prefix to canonical history | **NeXa** — `ConversationSession.commit_interrupted_turn(spoken_prefix)` |
| final authority over the physical speaker | **NeXa** — always |

### Trade-offs (INFERENCE; the spike must measure)

- **Interruption latency:** local Silero + local playback-stop is already
  ~28–37 ms media-stop (R0028). Cloud adds only the round-trip to tell
  Gemini to stop *generating* — the user stops hearing audio at local
  speed regardless.
- **EOT latency:** "server acts immediately on `activityEnd`" (G1) — so
  local Silero's end-of-turn decision is the critical path, same as local
  mode.
- **False starts / false ends:** governed by the *local* Silero params
  we already tuned (M2.1) — not Gemini's. Good: one tuned detector, not
  two racing.
- **Self-echo:** handled by the XVF3800 AEC exactly as in local mode
  (the mic signal is already clean before it forks to Gemini).
- **Network jitter:** new risk — jitter on the *inbound* audio stream can
  cause playback underruns. Mitigation: a small jitter buffer on
  `TTSAudioRawFrame` before the output transport; measure underruns.
- **Double-VAD race:** avoided by disabling server VAD (`disabled=True`)
  — only local Silero acts; server VAD signals are observed, not obeyed.

---

## SESSION / RECONNECT ARCHITECTURE

**DESIGN DECISION.** Question 9. Minimal state, driven by NeXa.

```
        ┌──────────────┐
        │ DISCONNECTED │◀───────── policy=LOCAL_ONLY, or cloud torn down
        └──────┬───────┘
               │ router selects CLOUD (turn boundary)
        ┌──────▼───────┐
        │  CONNECTING  │──── connect + BidiGenerateContentSetup(seed) ────┐
        └──────┬───────┘                                                  │
               │ SetupComplete + _ready_for_realtime_input                │
        ┌──────▼───────┐                                                  │
        │    READY     │  (sub-states: USER_STREAMING / MODEL_STREAMING)  │
        └──┬────────┬──┘                                                  │
           │        │ socket error / GoAway.timeLeft elapsed             │
           │        ▼                                                     │
           │  ┌──────────────┐  handle valid + canonical unchanged ──────┤
           │  │ RECONNECTING │  else: fresh connect + fresh snapshot seed │
           │  └──────┬───────┘                                           │
           │         │ 3 consecutive failures                            │
           │         ▼                                                   │
           │   ┌──────────┐   router: active_provider = LOCAL            │
           │   │  FAILED  │──▶ (policy unchanged; retry cloud per policy)│
           │   └──────────┘                                             │
           │ user "talk locally" / router switch (turn boundary)        │
           └───────────────────────────────────────────────────────────▶ DISCONNECTED
```

- `READY.USER_STREAMING` / `READY.MODEL_STREAMING` are sub-states, not
  peers — no need for separate top-level states (keeps it minimal per the
  question's caution).
- **Reconnect decision (case H logic):** on entering `RECONNECTING`,
  compare `canonical_history_len` now vs at handle-mint time. Equal →
  resume with handle. Changed → discard handle, `CONNECTING` with a fresh
  `CloudContextSnapshot`.
- **`GoAway`:** on `GoAway{timeLeft}` (or the ~9-min age timer), if idle
  → reconnect now; if mid-turn → finish the turn, then reconnect before
  `timeLeft` expires. (Wrapper responsibility — Pipecat doesn't do this.)
- **`contextWindowCompression`: ON** for production (unlimited session).
  For the **spike**: run *both* a compression-on and a compression-off
  session and compare latency/cost/quality (spike metric).
- **Canonical history is never at risk:** every finalised turn is written
  to `ConversationSession` the moment it completes. A dropped connection
  loses at most the *in-progress* assistant turn, which is committed as
  `interrupted=True` up to the spoken high-water mark.

---

## PRIVACY / SECURITY / COST

> **Superseded in part by *PHASE 0 CORRECTIONS* (C1 data terms, C4
> pricing).** Read this section through those. The Phase-0 text is the
> authority where they conflict.

### API-key handling (VERIFIED practice, G5/G7)

- **DESIGN DECISION:** key via environment variable
  (`NEXA_GEMINI_API_KEY` / `GOOGLE_API_KEY`), read through
  `src/nexa/config.py` like every other setting. **Never** a literal in
  code, tests, probes, or committed config. Add the var name to
  `.env.example` (if one exists) / documented in the spike README; add
  `.env` to `.gitignore` if not already.
- Cloud stage is only constructed when `policy != LOCAL_ONLY` **and** a
  key is present. No key + `AUTO` ⇒ behaves as `LOCAL_ONLY`.
- The key authorises billing — treat as a secret in logs (redact),
  telemetry, and error messages.
- **INFERENCE:** a `git` pre-commit secret scan (or at least a
  `git diff --check` + manual grep in the R0030 validation) is prudent;
  this report's own validation does exactly that.

### Free vs paid data use — **see *PHASE 0 · C1* (this is the corrected version)**

- **Outside EEA/CH/UK:** unpaid-quota inputs/outputs **are** used "to
  provide, improve, and develop Google products" and may be
  human-reviewed; paid-tier not used for training.
- **In EEA/CH/UK:** the Paid-Services data terms apply to **all** services
  including unpaid quota — inputs/outputs **not** used to improve Google
  products. Separately, a NeXa cloud voice *made available to users* in
  EEA/CH/UK must use Paid Services.
- **DESIGN DECISION:** the M2.6A spike uses **only scripted non-sensitive
  phrases** → safe on either tier / region. The M2.6B paid-key decision
  is an ADR-0004 item driven by region + distribution (C1 table).

### Billing model + cost dynamics — **see *PHASE 0 · C4* for the official table**

- **Official (2026-09-10), `gemini-3.1-flash-live-preview`, paid:** input
  **$0.75 / 1M text**, **$3.00 / 1M audio** (≈ $0.005/min); output
  **$4.50 / 1M text**, **$12.00 / 1M audio** (≈ $0.018/min); free tier
  free of charge. Grounding w/ Search: 5,000 free/month (shared across
  Gemini 3.x), then $14 / 1,000. Still ~an order of magnitude cheaper than
  OpenAI `gpt-realtime` (≈ $32 / $64 per 1M audio in/out).
- **Native audio token accumulation (VERIFIED — *PHASE 0 · C3*):** audio
  tokens accrue at **≈ 25 tokens/second** each way (≈ 1,500 tok/min). The
  earlier "≈ 600 in / 1,200 out per minute" (OpenAI-derived) is replaced.
- **Context compounding:** every turn re-charges the running context as
  prompt tokens; a long session's prompt cost grows with turn count.
- **No prompt caching on `gemini-3.1-flash-live-preview`** (G4) — the
  cache discount NeXa exploits locally does **not** apply; every
  reconnect re-seed is billed in full.
- **`contextWindowCompression`** bounds the compounding (keeps prompt
  tokens near the trigger threshold) at the cost of the model losing
  verbatim old context and some compression overhead.
- **Transcription surcharge:** enabling `inputAudioTranscription` /
  `outputAudioTranscription` — **OPEN QUESTION** whether it adds cost;
  check pricing page at spike time.
- **DESIGN DECISION (cost guardrails for the spike):** a hard per-session
  wall-clock cap (e.g. 15 min), a hard turn cap, and a printed running
  token/`usageMetadata` tally after every turn. Log every `429`/quota.

### Rate limits / quotas (VERIFIED G7)

- Tier gate: Free → **Tier 1** (billing enabled, instant) → Tier 2
  ($100 cumulative + 3 days) → Tier 3 ($1,000 + 30 days).
- Live API **concurrent-session** limits are **not published** on the
  rate-limits page — visible per-project in **Google AI Studio**.
- Forum (G9): the preview model has thrown `quota exceeded` for some
  users. **OPEN QUESTION:** the actual Tier-1 concurrent-session and RPM
  limits for `gemini-3.1-flash-live-preview` — read them in AI Studio
  during spike setup.

### One credential

**DESIGN DECISION:** exactly **one** Google AI Studio / Gemini API key,
billing enabled. No multi-account setup. **Not created in this task.**

---

## FUNCTION CALLING / FUTURE ACTIONS

**Research only. Nothing implemented.** Question 11.

- **VERIFIED (G4, P1):** `gemini-3.1-flash-live-preview` supports function
  calling but **synchronous only** — no `NON_BLOCKING` behaviour, no
  `scheduling` hints. The model **blocks** on every tool result;
  `cancel_on_interruption=False` (keep talking during a tool) is
  structurally impossible on this model. Pipecat logs a `logger.error` +
  `push_error` if an async tool is registered on a Gemini-3 model.
- **INFERENCE:** a slow NeXa action during a cloud turn = dead air until
  it returns. Actions invoked from cloud voice must be fast, or the
  wrapper must send an interim spoken "working on it" and a follow-up.
- **DESIGN DECISION (future, ADR-0004+):** the model **requests**, NeXa
  **decides and executes**:

  ```
  Gemini toolCall ─▶ NeXa wrapper
     ─▶ NeXa ActionRouter: validate name + args against the allowed
        tool_surface for this context
     ─▶ NeXa permissions + user-confirmation policy
     ─▶ NeXa executes (or declines)  ─▶ functionResponse back to Gemini
  ```

  The cloud provider never gets a raw device handle, a filesystem path, a
  shell, or an unmediated capability. The `tool_surface` in the
  `CloudContextSnapshot` is the *only* set of names Gemini may call, and
  even a valid call is re-validated + permission-checked before execution.
- **Pipecat behaviour during interruptions + tools (VERIFIED P1):** on
  `gemini-3` Pipecat treats `cancel_on_interruption` as always-true;
  `_process_completed_function_calls` sends results via the formal
  tool-response channel; a tool call issued by a *previous* session
  currently gets **no** answer (bug, PR #5497 pending — would deliver it
  as conversation text instead).
- **OPEN QUESTION:** does a barge-in *during* a pending synchronous tool
  call leave the model wedged? Spike or a targeted follow-up.

---

## MULTI-DEVICE COMPATIBILITY

**Design constraint, not implementation.** Question 12.

- **DESIGN DECISION:** the `RealtimeVoiceProvider` / cloud wrapper takes
  **logical audio + events**, never Pi specifics:
  - in: an async stream of PCM frames (sample rate + encoding as
    parameters) + turn markers (`user_started` / `user_stopped`) +
    a `CloudContextSnapshot`;
  - out: an async stream of PCM frames + `on_user_turn` /
    `on_assistant_turn` / `on_connection_state` events.
  - No `plug:respeaker`, no `aplay`, no reSpeaker, no ALSA in the cloud
    boundary. Those live in the **transport / audio** layer
    (`LocalAudioTransport`, `AecReferenceFeeder`) which is already
    separate.
- The XVF3800 AEC and Silero VAD are **today's** turn/echo implementation
  behind that logical boundary; a phone client would bring its own
  AEC/VAD (or WebRTC's) and feed the same logical interface.
- **ADR-0003 D9 stays intact:** when multi-device becomes real, a
  LiveKit/WebRTC transport can feed this same cloud (or local) pipeline —
  the cloud provider does not care what carried the audio.
- **Non-goal for v1:** running Gemini Live over WebRTC directly to a
  browser. NeXa stays in the middle (it must — it owns canonical history,
  policy, permissions).

---

## RISKS

| # | Risk | Severity | Mitigation / status |
|---|---|---|---|
| R1 | **Polish audio quality** — **one external forum report** (G8) says `gemini-3.1-flash-live-preview` native audio speaks Polish with a strong EN/US accent; Google only acknowledged the report, did not confirm the regression (*PHASE 0 · C5*). NeXa is bilingual PL/EN. | **High (to test)** | The M2.6A operator test is the authoritative check. Options if it fails there: cloud EN-only first; route PL turns to local (`AUTO` per-turn by language); wait for a Google fix; try `gemini-2.5` native audio. |
| R2 | **Preview-model instability** — G9: first-audio 16–26 s latency incidents, "dial-up" audio distortion, `quota exceeded`. Preview, not GA; Google changes it under us. | High | Pin the model string; monitor the model page + forum; the spike is explicitly a *feasibility* check, not a ship decision. `AUTO` fallback to local absorbs an outage. |
| R3 | **Pipecat #5465** — silent drop of user audio/text/tool-results during the reconnect window; **no `GoAway` handling** (both VERIFIED). | Med (spike) / High (prod) | Spike: log the window, keep sessions short. Prod: NeXa wrapper adds a not-ready send buffer + proactive `GoAway`/age-timer reconnect; track PR #5497. |
| R4 | **Cost compounding** — no caching on 3.x, per-turn context re-charge, native-audio token rates. | Med | `contextWindowCompression` on; bounded `CloudContextSnapshot`; per-session wall-clock + turn caps; running `usageMetadata` tally; measure a 30-min PL/EN session in the spike. |
| R5 | **Privacy / data use** — *outside* EEA/CH/UK, unpaid-quota inputs/outputs are used to improve Google products + may be human-reviewed; *in* EEA/CH/UK the Paid-Services data terms already apply to unpaid quota (*PHASE 0 · C1*). | Med | M2.6A: scripted non-sensitive phrases only (safe on either tier). M2.6B: `CloudContextSnapshot` privacy filter; raw audio not retained; `LOCAL_ONLY` fully bypasses cloud; paid-key / region decision in ADR-0004. |
| R6 | **Canonical-transcript drift** — output transcription runs ahead of spoken audio; interruption truncation is approximate (VERIFIED P1 note). | Med | Spoken-audio high-water mark (reuse local `SpokenTextTracker` pattern); measure over-run distance on real barge-ins in the spike; store transcription text, discard unspoken remainder. |
| R7 | **New dependency + credential surface** — `pipecat-ai[google]` / `google-genai`; a cloud key in the repo's runtime. | Med | Import-guarded, cloud-stage-only; env-var key; ADR-0004 before production; secret-scan in validation. |
| R8 | **Two turn detectors** if server VAD is left on. | Low | Disable server VAD (`GeminiVADParams(disabled=True)`); local Silero is the sole authority; server signals observed only. |
| R9 | **Language-routing authority** — Gemini may ignore the "respond in X" instruction; the resolver can't fix a rendering-level accent. | Med | Resolver still decides + tells Gemini; measure adherence in the spike; R1 covers the rendering side. |
| R10 | **Session-resumption** — token validity is **2 h after the last session terminates** (*PHASE 0 · C3*, resolved); what a resumed native-audio session actually restores is still unverified. | Low | Design re-seeds from a fresh snapshot whenever canonical history changed (case H) — resumption is a nice-to-have, not load-bearing. Confirm restore behaviour in the reconnect phase. |
| R11 | **Scope creep** — cloud voice invites building memory / identity / router / tools "while we're here". | Med | Explicit: M2.6A proves native cloud conversation quality + latency **only**. Router, memory, tools, GUI are later, separate, each with its own report/ADR. |

---

## OPEN QUESTIONS

1. **Polish audio quality** — is `gemini-3.1-flash-live-preview` native
   Polish acceptable to the operator *today*, or must PL route to local?
   (Spike PASS gate; *PHASE 0 · C5*.)
2. **End-to-end latency on this Pi + this network** — EOT → first audible
   cloud audio. Target median 0.8–1.5 s (**not** guaranteed). (Spike.)
3. ~~Input chunk size~~ — **CLOSED (*PHASE 0 · C2*):** 20–40 ms per
   official best-practices; M2.6A baseline 20 ms, optional 40 ms compare.
4. ~~Session-resumption validity window~~ — **CLOSED (*PHASE 0 · C3*):**
   2 h after the last session terminates. Still open: *what* a resumed
   native-audio session restores (reconnect phase).
5. **Transcription surcharge** — does enabling input/output transcription
   add token cost? (Pricing page at spike time — the C4 table did not
   itemise it.)
6. **Language routing vs input-transcription timing** — *PHASE 0 · C6*
   questions A–G. The single most important thing M2.6A must measure for
   ADR-0004.
7. **`contextWindowCompression` effect** — latency / cost / quality delta
   vs compression-off, over a 30-min PL/EN session. (Spike, both modes.)
8. **Tier-1 concurrent-session + RPM limits** for the preview model —
   read in AI Studio during setup.
9. **Barge-in during a pending synchronous tool call** — does the model
   wedge? (Later; no tools in M2.6A.)
10. **Output-transcription look-ahead distance on interruption** — how many
    words does it over-run the played audio? Determines high-water-mark
    granularity. (Spike.)
11. ~~Exact current Gemini API data-use terms~~ — **CLOSED (*PHASE 0 ·
    C1*):** read verbatim from `ai.google.dev/gemini-api/terms` on
    2026-09-10.
12. ~~`pipecat-ai[google]` transitive-dep weight~~ — **CLOSED (this
    task):** installed only `google-genai 2.22.0` (not the full extra) +
    10 transitive packages; venv **+32 MiB** (~+5 %); `google.genai`
    import ~608 ms; **`websockets` downgraded 17.1 → 16.1.1** (within
    pipecat's `>=13.1`; `pip check` clean; spot-check voice tests pass).
    No tracked dependency file changed.
13. **Interaction with NeXa's local `BargeInController`** — in cloud mode
    the local streaming-TTS barge-in machinery is not in the path; confirm
    the reused *concepts* (interruption state, `commit_interrupted_turn`,
    AEC feed) compose cleanly without the `broadcast_interruption()` /
    capture-coalesce plumbing.

---

## REJECTED ALTERNATIVES

| Alternative | Why rejected (now) |
|---|---|
| **OpenAI Realtime (`gpt-realtime` / `-mini`)** | ~10× the token cost of Gemini Live; narrower language coverage; no evidence it does Polish better; WebRTC-first (we don't need a browser transport for a Pi). **Kept as the documented fallback** if Gemini's Polish (R1) or stability (R2) proves unworkable — `gpt-realtime` is noted as stronger on long sessions + compliance. |
| **Grok Voice / xAI** | Less mature API + docs; no Polish evidence; no Pipecat first-party service. Not a serious v1 candidate. |
| **Direct `google-genai` Live API (OPTION B)** | Re-implements Pipecat's mature reconnect / session-resumption / activity-window / transcription-aggregation / Gemini-2.5-vs-3.x handling for zero benefit; diverges from ADR-0003 D1's framework choice. Reconsider only if C proves unfixable. |
| **Use Pipecat `GeminiLiveLLMService` as-is (OPTION A)** | Ships #5465 (silent drop) + zero `GoAway` handling into an hours-long companion. |
| **Cloud path as a `ModelProvider` subtype** | `ModelProvider` is `messages -> AsyncIterator[str]`; Gemini Live is speech-to-speech with audio + out-of-band transcription and owns its I/O. Forcing the fit = the hidden second path ADR-0003 D2 forbids. |
| **Gemini server VAD as the turn authority** | Discards the XVF3800 AEC + Silero tuning R0028/R0029 proved necessary; two detectors racing; loses local sub-40 ms media-stop authority. |
| **LiveKit for cloud voice v1** | ADR-0003 D9 — LiveKit is deferred to a *multi-device transport* stage. Single Pi + Pipecat local transport is the v1 requirement; nothing here needs WebRTC. |
| **Retain raw cloud audio by default** | Privacy-first violation; no consumer for it; canonical history is text. Debug-only, off-by-default, time-boxed capture is the only allowed form. |
| **Put NeXa's identity/persona/memory in `system_instruction`** | Hands the authoritative identity to a provider; duplicated authority; violates the "cloud never owns identity/memory" invariant. Minimal role-card instruction only. |
| **Let the model hold the LOCAL/CLOUD policy** | The provider must never own routing. Policy is NeXa core state; the model may only *classify the intent*. |

---

## PROPOSED NEXT SPIKE — M2.6A

**Minimal Gemini Live real-hardware feasibility spike.** Mirrors how M2.5
opened with M2.5A (R0028). Its own short report (**R0031**), then an
**ADR-0004** before any production cloud code (**M2.6B**).

### Prove ONLY

```
reSpeaker mic
  → XVF3800 hardware AEC  (existing)
  → Pipecat LocalAudioTransport.input + Silero VAD  (existing)
  → Gemini Live (gemini-3.1-flash-live-preview), server VAD disabled,
      activity_start/activity_end from Silero, input+output transcription on
  → native streamed cloud audio (24 kHz)
  → AecReferenceFeeder tee + LocalAudioTransport.output  (existing)
  → speaker
  + metrics + a running usageMetadata token tally
```

### Explicitly NOT in the spike

Memory, identity system, the full `ConversationRouter`, `SetConversationPolicy`
NL matching, tools/actions, GUI, LiveKit, a production `RealtimeVoiceProvider`
interface, `append_external_turn` in `src/`, canonical-history write-back
(the spike may just *log* the transcription streams). Local mode
(R0029) is untouched and `bargein_enabled` stays default-off.

### Shape

- A **bounded, disposable** harness — a new `apps/` probe
  (`apps/nexa_cloud_voice_probe.py`) or a `docs/research/m2_6_cloud_realtime_voice/`
  script — that builds a Pipecat pipeline with the existing transport +
  Silero + `AecReferenceFeeder` and `GeminiLiveLLMService`
  (`pipecat-ai[google]` added). No change to `src/nexa/**` behaviour;
  the probe may import NeXa transport/AEC helpers read-only.
- Paid-tier key via `NEXA_GEMINI_API_KEY` env var. Hard 15-min session
  cap, hard turn cap, per-turn token tally, `429`/error logging.
- **Scripted interaction (operator):**
  1. EN: "Tell me, in two sentences, why the sky is blue." — let it
     finish. *(clean EN turn, first-audio latency)*
  2. EN barge-in: ask a long question, interrupt after ~2 s with "stop —
     just give me the short version." *(interruption latency, spoken
     high-water mark, `serverContent.interrupted`)*
  3. PL: "Powiedz mi krótko, dlaczego niebo jest niebieskie." — let it
     finish. **Operator rates Polish pronunciation / naturalness /
     accent** (R1).
  4. PL follow-up: "A dlaczego zachód słońca jest czerwony?" *(PL
     multi-turn context)*
  5. Sit silent ~11 minutes, then ask one more EN question. *(force one
     ~10-min reconnect; observe #5465 window + resumption / re-seed +
     whether the answer is context-aware)*
- Capture: terminal log + a JSON metrics file under
  `docs/research/m2_6_cloud_realtime_voice/`.

---

## PASS / WARN / FAIL METRICS FOR THAT SPIKE

Measured on the real Pi + real network. Latency clocks start at the
**local Silero end-of-turn** decision unless noted.

| Metric | PASS | WARN | FAIL |
|---|---|---|---|
| **EOT → first cloud server event** | ≤ 0.6 s | 0.6–1.2 s | > 1.2 s |
| **EOT → first received audio chunk** | ≤ 1.0 s | 1.0–2.0 s | > 2.0 s |
| **EOT → first *audible* audio** (target UX: materially faster than local) | ≤ 1.5 s median | 1.5–2.5 s | > 2.5 s, or slower than local voice |
| **Interruption: local Silero start → local playback stopped** | ≤ 100 ms | 100–250 ms | > 250 ms |
| **Interruption: barge-in → cloud stops generating** (`serverContent.interrupted` observed) | ≤ 1.0 s | 1.0–2.0 s | > 2.0 s or never |
| **Output-transcription over-run past spoken audio on interruption** | ≤ 1 sentence | 2–3 sentences | > 3 sentences (high-water mark unreliable) |
| **~10-min reconnect duration** (socket close → READY again) | ≤ 3 s | 3–8 s | > 8 s, or session/context lost |
| **Post-reconnect answer is context-aware** (references pre-reconnect turns) | yes | partial / one "forgot" turn then recovers | no |
| **User audio dropped during the reconnect window** (#5465) | 0 ms (or fully logged + < 300 ms) | 300 ms–1 s, logged | > 1 s, or silent |
| **Input-transcription accuracy** (PL + EN, vs what was said) | usable, ≤ occasional word error | frequent word errors | unusable / wrong language |
| **Polish audio quality** (operator judgement) | natural, correct Polish phonetics | understandable but noticeable EN accent | strong EN/US accent — not shippable for PL |
| **English audio quality** (operator judgement) | natural | minor artefacts | distorted / robotic |
| **Audio underruns / glitches per 5-min** | 0 | 1–3 | > 3 or continuous |
| **Cost — 15-min mixed PL/EN session** | within the pre-set budget, tally printed | 1.5–2× budget | > 2× budget or untracked |
| **Self-echo during cloud playback** (false barge-in while silent) | 0 | 1 | ≥ 2 |
| **`pipecat-ai[google]` venv impact** | < ~200 MB added, imports clean on Pi | 200–500 MB | fails to import / > 500 MB |
| **Stability over the session** | no unhandled exception, clean teardown | one recovered error | crash / hang / stuck reconnect loop |

**Overall spike verdict:**
- **GO to ADR-0004 + M2.6B** if every row is PASS/WARN and Polish audio
  is at least WARN and end-to-end latency is PASS.
- **CONDITIONAL** (EN-only cloud, or per-language routing) if Polish audio
  is FAIL but everything else is PASS/WARN.
- **NO-GO / revisit provider** if latency is FAIL, or reconnect loses
  context, or stability is FAIL.

---

## FILES CHANGED

Research only — **no `src/` change, no test change, no secret.**

**New:**

- `docs/reports/R0030_cloud_realtime_voice_research_architecture_20260910.md`
  — this report.
- `docs/research/m2_6_cloud_realtime_voice/README.md` — pointer.
- `docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live.py`
  — OFFLINE static audit of the installed Pipecat 1.8.1 `GeminiLiveLLMService`
  (issue #5465 silent guards, GoAway handling, session resumption /
  reconnect, Gemini-3.x async tools). No network, no key, exit 0.
- `docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live_output_20260910.txt`
  — captured output, 2026-09-10, pipecat 1.8.1.

**Changed (end-of-task, only because the research decision is well
supported):**

- `docs/CURRENT_STATE.md` — "Latest report" pointer → R0030; next-stage
  line names **M2.6A** + notes ADR-0004 owed.
- `docs/ROADMAP.md` — M2 "Next sub-stage" gains the concrete **M2.6A**
  spike name + the R0030 reference + the "ADR-0004 before M2.6B" note.

**Not changed (verified):** all of `src/nexa/**`, all of `tests/**`,
`pyproject.toml` / dependencies (`pipecat-ai[google]` / `google-genai`
**not** added — that is an ADR-0004 / M2.6A decision), the frozen local
voice baseline (R0029), `bargein_enabled` default.

---

## TESTS / CHECKS RUN

- `.venv/bin/python docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live.py`
  → exit 0; confirmed the #5465 silent guards PRESENT, GoAway handling
  ABSENT, session resumption + reconnect PRESENT, Gemini-3.x async tools
  NOT supported. Output captured to the `.txt` artifact.
- `.venv/bin/ruff check docs/research/m2_6_cloud_realtime_voice/` → All
  checks passed.
- `.venv/bin/python -m py_compile …/inspect_pipecat_gemini_live.py` → OK.
- `git diff --check` → clean (see GIT STATUS).
- Secret scan — manual `grep` for `api[_-]?key`, `AIza`, `token`,
  `secret` across the staged diff → none (the script reads an env var
  name only; no key value anywhere).
- **Not run:** the full `pytest` / `unittest` suite — no `src/` or
  `tests/` change (last green at `788a64d`: pytest 732 / unittest 739 /
  ruff clean). No cloud call (no key; `google-genai` not installed).

---

## COMMIT HASH

- `b82c3bd` — `research: M2.6 Cloud Realtime Voice — architecture &
  Gemini Live audit (R0030)` (this report + the
  `docs/research/m2_6_cloud_realtime_voice/` spike + the
  `CURRENT_STATE` / `ROADMAP` M2.6 updates).

This hash-record edit lands in the immediately-following commit
(R0026–R0029 pattern). Prior tip: `788a64d` (M2.5B closure). **Not
pushed.**

---

## GIT STATUS

Branch `main`, **not pushed**. `git diff --check` clean. No `src/` /
`tests/` / dependency change. New: R0030 + `docs/research/m2_6_cloud_realtime_voice/`.
End-of-task doc edits: `docs/CURRENT_STATE.md`, `docs/ROADMAP.md`.
Frozen local baseline (R0029) untouched; `bargein_enabled` default-off
untouched; no API key, no `google-genai`, no `pipecat-ai[google]`.

---

## GO / NO-GO FOR FIRST GEMINI LIVE HARDWARE SPIKE

**GO** for **M2.6A — minimal Gemini Live real-hardware spike.**

- Provider + model are the right first choice and are frozen for v1
  (`gemini-3.1-flash-live-preview`).
- The integration path is understood end-to-end (Pipecat OPTION C, hybrid
  local-VAD audio, NeXa-owned canonical transcript + policy).
- The two Pipecat gaps (#5465, no `GoAway`) are identified, bounded, and
  don't block a short operator-present spike.
- All 12 questions are answered at the design level; the remaining
  unknowns are exactly what a spike is for (latency, Polish audio quality,
  cost, reconnect behaviour on real hardware).

**NO-GO** (until the spike reports) for: adding `google-genai` /
`pipecat-ai[google]` to the **tracked** project deps (`pyproject.toml`),
writing any `src/nexa/**` cloud code, the `ConversationRouter` /
`SetConversationPolicy`, `append_external_turn`, and ratifying
**ADR-0004**. Those wait on M2.6A's measured latency, the
input-transcription-vs-audio ordering (*PHASE 0 · C6*), and the operator's
Polish-audio judgement.

**Prerequisite status:** the operator has provided one Gemini API key
(Google project `591383313359`); it is stored **outside the repo** at
`~/.config/nexa/secrets/gemini.env` (dir `700`, file `600`), variable
`NEXA_GEMINI_API_KEY`. The connectivity smoke used it successfully. Its
tier / region and whether M2.6B needs a paid key is an ADR-0004 decision
(*PHASE 0 · C1*). **The key value appears nowhere in the repo.**
