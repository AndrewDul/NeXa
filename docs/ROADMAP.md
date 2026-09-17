# ROADMAP

Staged milestones for NeXa. This roadmap is **allowed to evolve** through ADRs
and evidence. Distant milestones are deliberately under-specified — do not treat
them as final design.

Only one milestone is active at a time. See `docs/CURRENT_STATE.md` for which.

---

## M0 — Foundation  ✅ (complete — see `docs/CURRENT_STATE.md` for the active milestone)

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
  `CredentialSource` seam; `google-genai>=2.22,<3` + `websockets>=15,<17`
  as an **optional `cloud-gemini` extra** (`pip install .` stays
  Google-free; `src/nexa/realtime/gemini/` imports `google.genai` lazily;
  `pipecat-ai[local]==1.8.1` unchanged); `ProviderUsageEvent` from
  authoritative `usageMetadata`; Gemini function calling never the
  capability authority (event types defined, OFF in v1); Pipecat owns
  media/WS mechanics only. The ADR carries the ordered M2.6B
  implementation plan (15 components) and 19 measurable M2.6B acceptance
  gates. No `src/` / `tests/` / dependency-file change in the ADR task.
  **Amendment 1 (2026-09-10)** — four pre-M2.6B factual/API/terms
  corrections, **no decision reversed**: (1) operator is in the **UK**
  (not the EEA — Google groups EEA/CH/UK); split "data treatment" from
  "making an API Client available to users"; the Gemini API **Free Tier is
  available in the UK** so `DEVELOPMENT`-mode M2.6B work is not blocked by
  billing; a Gemini key is **not** a "paid key" — eligibility is a
  deployment policy `ProviderEligibilityPolicy(distribution_mode,
  billing_verified)`, nothing inferred from the key string, `DISTRIBUTED` +
  EEA/CH/UK users requires `billing_verified`. (2) `system_instruction` is
  **immutable on an open Live connection** — a sticky language command
  updates NeXa canonical state immediately and reaches the provider setup
  only on the **next** new/resumed session; no forced per-command reconnect
  in M2.6B. (3) Gemini-3.1 recent-turn history is seeded **once** at
  session start via the initial-history mechanism
  (`history_config.initial_history_in_client_content=true`), never
  turn-by-turn. (4) session-resumption reconnect keeps only the latest
  `resumable=true` handle, never uses a non-resumable/empty handle, prefers
  a safe turn boundary, uses `GoAway.timeLeft`, buffers inbound audio while
  reconnecting, falls back to a fresh seeded session if safe resumption is
  impossible — **make-before-break connection overlap is no longer
  assumed** (socket sequencing → M2.6B).
- **M2.6B — IN PROGRESS.** Production `RealtimeVoiceProvider` +
  `GeminiLiveProvider` + `ConversationRouter` + `ConversationPolicy` +
  `SetConversationPolicy` + `CloudContextSnapshot` + inbound #5465 buffer +
  `ReconnectController` + usage telemetry + cloud credential loader +
  provider voice/language preference, per the ADR-0004 plan and gates.
  Local voice stays byte-for-byte frozen; real-hardware operator
  acceptance required before COMPLETE.
  - **M2.6B.1 — provider-agnostic foundation: IMPLEMENTED** (`R0032`,
    2026-09-11). New `src/nexa/realtime/` package (no cloud SDK import):
    `RealtimeVoiceProvider` (peer of `ModelProvider`) + `ProviderReadiness`;
    `ConversationPolicy` (`LOCAL_ONLY` default) + `ProviderEligibilityPolicy`
    (Amendment 1 — `distribution_mode`/`billing_verified`, not a key
    property); `CloudContextSnapshot` (bounded, allow-listed, pure);
    `InboundAudioBuffer` (#5465 protection — bounded, drop-oldest, no
    duplicate delivery); `ProviderUsageEvent` telemetry; a deterministic
    `ReconnectController` (no socket); `src/nexa/realtime/gemini/`
    credential loader + voice-preference mapping (`warm_female` ->
    `Sulafat`, no cloud SDK either). Mandatory Gemini/Pipecat
    startup-sequencing source audit done against the *installed*
    `pipecat-ai==1.8.1` + `google-genai==2.22.0` — **confirms, does not
    contradict**, ADR-0004/Amendment 1 (Pipecat already implements the
    initial-history seed and the resumable-handle-only rule internally);
    see `docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`.
    +73 tests (812 total, 0 regressions); zero existing `src/nexa/**` file
    modified; no `pyproject.toml` change; no cloud call.
  - **M2.6B.2 — `GeminiLiveProvider` + canonical cloud-turn integration:
    IMPLEMENTED** (`R0033`, 2026-09-11). `src/nexa/realtime/turn.py`
    (`CloudTurnAccumulator` — one logical cloud turn -> at most one
    canonical commit, NeXa-local `generation` id); `turn_framing.py`
    (`UtteranceFramer` — preserves the activity-start/audio/activity-end
    envelope through a NOT_READY window); `router.py`
    (`ConversationRouter` — owns policy/active_provider/provider
    lifecycle/snapshot creation/cloud-turn commits/failure fallback;
    `LOCAL_ONLY` never calls the injected cloud-provider factory);
    `gemini/service.py` (`GeminiLiveProvider` — production
    `RealtimeVoiceProvider`, built exactly per the R0032 source-audit
    sequencing: construction-time `system_instruction`, initial
    `LLMContext` from `snapshot.recent_turns`, one `LLMRunFrame` kickoff,
    Pipecat's own one-time `clientContent` seed, `UtteranceFramer` wired
    into the turn-I/O methods). **Proven against the REAL Pipecat 1.8.1
    pipeline/worker/aggregator machinery** (fake terminal service only —
    no cloud call); found and fixed a real bug this way
    (`WorkerRunner.add_workers()` needs `asyncio.create_task(runner.run())`
    launched alongside it). Additive
    `ConversationSession.record_external_exchange` (NORMAL / USER-ONLY /
    INTERRUPTED / NO-SPOKEN-ASSISTANT / invalid cases tested;
    `send()`/`commit_interrupted_turn()` unchanged). `pyproject.toml`:
    `cloud-gemini` optional extra added
    (`google-genai>=2.22,<3` + `websockets>=15,<17`; core deps unchanged,
    `pip install .` stays Google-free). +57 tests (870 total, 0
    regressions); zero non-additive `src/nexa/**` change. **Known gap
    (resolved in M2.6B.2A below):** `ReconnectController` not yet driven
    by `GeminiLiveProvider` on a real connection error — no live reconnect
    wiring yet.
  - **M2.6B.2A — pre-hardware cloud-turn / reconnect hardening:
    IMPLEMENTED** (`R0034`, 2026-09-11). Fixed a real silent-user-speech-
    loss bug (mid-turn readiness loss — a turn started live then lost
    readiness had further audio rejected as an orphan and its
    `activity_end` silently swallowed) with a deterministic
    abort-and-restart policy: the stranded live segment is aborted
    (logged, never silent) and everything after becomes a new,
    self-contained buffered utterance — never silent loss, never
    duplicated audio; a connectivity hiccup mid-utterance may present as
    two utterances to Gemini instead of one, a documented trade-off, not
    data loss. New `take_pending_audio()` for a fresh-session hand-off.
    Completed the provider->NeXa event map (interim transcription, a
    proper `GenerationCompleteEvent`, error frames) — which exposed and
    fixed a second gap: Pipecat's `push_error()` pushes upstream, but the
    only tap was downstream; fixed with a second `up_tap`. Closed the
    "manual injection" test gap with
    `ConversationRouter.handle_provider_event()`. Tested 5 turn-ordering/
    interruption permutations (found and fixed a third gap: a late
    assistant-transcription delta after interruption was still being
    appended); proved turn-complete-before-delayed-transcription
    impossible from the installed Pipecat source, not assumed. Made the
    fresh-snapshot-on-resumption-failure mechanism precise
    (destroy-and-recreate, proven never to leak stale context). +16 tests
    (886 total, 0 regressions). Still no live Gemini connection; reconnect
    still not wired to a real socket (explicit, deferred to M2.6B.3).
  - **M2.6B.3 — production HYBRID cloud audio + real-hardware operator
    acceptance: IMPLEMENTED, TEST-READY** (`R0035`, 2026-09-11).
    `ReconnectController` wired into `GeminiLiveProvider` via a new
    `_readiness_monitor()` observation seam (Pipecat's own reconnect is
    automatic/internal, no external hook — confirmed from source; NeXa
    can only observe the readiness transition it produces). Explicit
    policy: a mid-user-turn connection loss is **not** assumed safely
    resumable (no source evidence found) —
    `ConversationRouter.recover_from_mid_turn_loss()` destroys the old
    provider and starts a fresh one from a freshly rebuilt canonical
    snapshot, replaying only not-yet-delivered PCM as one new utterance;
    a safe-boundary loss may resume the same provider-scoped context, no
    re-seed, no duplicate turns. New `src/nexa/realtime/gemini/
    runtime.py` (`GeminiVoiceRuntime`) wires the REAL hardware path:
    reSpeaker mic → the existing `LocalAudioTransport`/`SileroVADAnalyzer`/
    `VADProcessor` construction pattern → a new, minimal
    `_VadToProviderBridge` (Gemini server VAD stays OFF; local Silero
    remains the sole turn authority) → `GeminiLiveProvider` → Gemini →
    assistant audio injected back into the SAME hardware pipeline via
    `PipelineWorker.queue_frames` → USB speaker, teed to the XVF3800 AEC
    far-end reference by the existing, unmodified `AecReferenceFeeder` —
    no second AEC implementation. Cloud barge-in reuses the existing,
    unmodified `BargeInController`: kept in sync with cloud turns from the
    same single `provider.events()` consumption loop that drives the
    canonical write path (a second independent event-stream reader was
    drafted for operator printing, found unsafe on re-reading
    `service.py` — `events()` is one `asyncio.Queue`, single-consumer —
    and fixed with an in-loop `on_event` hook); `_on_confirmed` freezes
    the spoken prefix via the existing `CloudTurnAccumulator.assistant_text`
    (same precision as the local `SpokenTextTracker`, no new tracker
    class), fire-and-forgets `provider.cancel()`, never blocks local
    speaker-stop. New operator app `apps/nexa_cloud_voice_app.py`
    (`--dry` proven live in-sandbox: full object graph constructs, no
    audio device/network touched). +10 tests (896 total, 0 regressions).
    **Real Gemini/hardware operator acceptance is the one remaining step
    before M2.6B is marked COMPLETE — NOT YET RUN, see R0035 for the
    exact launch command and READY lines.**
  - **M2.6B.3A — final pre-live playback/barge-in/recovery hardening:
    IMPLEMENTED, PASS** (`R0036`, 2026-09-11). Fixed three
    production-significant seams R0035 got wrong or left undriven, each
    confirmed (not assumed) against installed Pipecat 1.8.1 source: **(1)**
    `GenerationCompleteEvent` alone was flipping `BargeInController` back
    to IDLE before any assistant audio necessarily reached the speaker —
    `BaseOutputTransport` proves `BotStoppedSpeakingFrame` (a real
    `TTSStoppedFrame`, or a 3s silence fallback) is the only true
    playback-drain signal. Fixed with new `_ResponseLifecycle` (the cloud
    analogue of the already-accepted `nexa.voice.gate.HalfDuplexGate`
    combinator) plus a deterministic `TTSStoppedFrame` injected right
    after each generation's audio (same FIFO the audio chunks went
    through), so the real stop is never a multi-second guess. **(2)** the
    raw, running `CloudTurnAccumulator.assistant_text` was being used
    directly as an interrupted turn's spoken prefix — Pipecat's own
    installed source (`gemini_live/llm.py`) documents, verbatim, that
    output-transcription "arrive[s] *before* the model_turn messages with
    audio" and "contain[s] much *more* text" (look-ahead), so "on an
    interruption our recorded context will contain some text that was
    actually never spoken" — a source-proven failure mode. Fixed with new
    `_SpokenPrefixHighWater`: a one-chunk-lag combinator promoting text to
    the high-water mark only once a LATER audio chunk confirms an EARLIER
    snapshot has crossed the playback-output boundary (a response
    interrupted after only one chunk ever played deliberately commits an
    empty prefix — the conservative, safe choice). **(3)**
    `ConversationRouter.recover_from_mid_turn_loss()` had NO CALLER
    anywhere in `GeminiVoiceRuntime` (confirmed by direct inspection).
    Fixed: `_consume_provider_events` now polls
    `provider.needs_fresh_session` after every event and drives recovery
    the instant it's observed, atomically swapping both `runtime.provider`
    and a new `_ProviderHandle` box the VAD bridge reads through — never a
    second concurrent `provider.events()` reader; the old, already-stopped
    provider's queue is never read again. A genuine test-design race was
    found and fixed while proving this. **(4)** confirmed
    `should_proactively_reconnect()` also has no caller — explicitly
    documented as DEFERRED (not silently overclaimed); the mid-turn-unsafe
    path from (3) is the one actually wired. **(5)** fixed a real bug: the
    operator app was silently replacing (not composing with) the metrics
    logger's AEC-status callback by reaching into `AecReferenceHealth`'s
    private attribute — fixed with a proper `on_aec_change` parameter,
    composed internally with the metrics logger. +15 net new tests (911
    total, 0 regressions); `ruff`/`pip check`/`git diff --check`/
    secret-scan/import-isolation all clean. **Real Gemini/hardware
    operator acceptance remains the one step before M2.6B is COMPLETE —
    NOT YET RUN, see R0036 for the exact launch command and READY lines
    (unchanged from R0035).**
  - **M2.6B.3B — interrupted cloud history safety only: IMPLEMENTED,
    PASS** (`R0037`, 2026-09-11). R0036's own §2 fix (the one-audio-
    chunk-lag `_SpokenPrefixHighWater`) was found to still overclaim: a
    later chunk's mere existence does not prove how much of an EARLIER
    text snapshot that chunk's own audio actually covers (snapshot
    `"abcdef ghijkl mnop..."` with chunk #1 = "abc", chunk #2 = "def" —
    chunk #2 arriving proves nothing about chunk #1 covering the WHOLE
    earlier snapshot). Re-searched the installed source for a real,
    provider-supported alignment mechanism: `google.genai.types.
    Transcription.words`/`WordInfo.start_offset`/`end_offset` **do
    exist** in the underlying SDK's own type schema (real per-word timing
    data), but Pipecat's installed `_handle_msg_output_transcription`
    **never reads or forwards `words`** (zero occurrences anywhere in the
    file) — only the concatenated `.text` reaches any frame NeXa's
    provider can see, so this data is unreachable without bypassing
    Pipecat's own service (out of scope; ADR-0004 already forbids a
    second raw Gemini client), and it's unverified whether the API even
    populates it in practice. **Conclusion: no deterministic alignment
    exists in the integrated stack.** Deleted `_SpokenPrefixHighWater`;
    new `CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""` is passed
    unconditionally on every confirmed interruption regardless of chunk
    count or accumulated text — an interrupted cloud turn always commits
    `COMMITTED_USER_ONLY`. **Cloud interrupted-prefix precision:
    CONSERVATIVE / NO FALSE FUTURE TEXT** — under-crediting an
    interrupted reply is acceptable in v1; crediting words that were
    never spoken is not. Normal, non-interrupted completions unaffected.
    Playback lifecycle, AEC, reconnect/mid-turn-recovery, router
    architecture, the Gemini model/voice, VAD, and barge-in thresholds
    were **not** touched. 5 tests replace R0036's 6 (matching the
    charter's own numbered scenarios exactly); full suite **910 tests, OK
    (skipped=7)**, 0 regressions; `ruff`/`pip check`/`git diff --check`/
    secret-scan/import-isolation all clean. **Standing note: `M2.6B` must
    NOT be marked fully COMPLETE after the hardware run until the
    proactive-reconnect/age-trigger gate is either implemented and
    deterministically validated, or explicitly changed by an ADR
    amendment** — restated from R0036, not newly resolved by this
    checkpoint.
  - **M2.6B.4 — production hardware acceptance ATTEMPT #1: FAIL**
    (`R0038`, 2026-09-11). The FIRST real operator hardware/Gemini run
    happened: `CLOUD_PROVIDER_READY`, `AEC_REF_ACTIVE`, audible Sulafat,
    normal conversation all reached — but three real regressions, each
    root-caused against installed source (not guessed) and fixed
    deterministically, no further Gemini call made. **(1)**
    `_VadToProviderBridge` crashed Pipecat's real setup/cleanup:
    `FrameProcessor.__init__` already owns `self._metrics`
    (installed source, `frame_processor.py:256,653,669`) and calls
    `.setup()`/`.cleanup()` on it; NeXa's code stored `RuntimeMetrics`
    under that SAME name, clobbering Pipecat's own metrics object, so
    Pipecat's real lifecycle went on to call those methods on NeXa's
    telemetry instead. Fixed: renamed to `self._nexa_metrics`. **(2)**
    local playback did not stop on barge-in — the interrupted reply
    played to completion while the new reply was already generating.
    Source-audited the exact chain: `broadcast_interruption()`
    (unchanged) only clears the output-transport queue's contents at
    that instant; it does nothing about audio still sitting on
    `provider.events()`'s own queue, or produced by Gemini in the brief
    window before it honours the cancel signal — confirmed as the real
    cause, not a cloud-model problem. Fixed with new
    `_ResponseGenerationGuard`: every assistant audio chunk is checked
    against the currently VALID response-generation id before it is ever
    handed to `hw_worker.queue_frames()`; a confirmed local interruption
    invalidates the current generation synchronously (no race on the
    single-threaded event loop) — "hard local output clear
    (`broadcast_interruption`) + generation invalidation (this guard)",
    never either alone. A real design bug was found and fixed while
    building this: an earlier draft conflated "may this chunk play" with
    "has a new local turn started" (both keyed off the same
    interrupt-vs-valid state), which let trailing old-generation audio
    masquerade as a fresh dispatch and defeat the guard — fixed by
    keying dispatch timing to a fresh, final `UserTranscriptionEvent`
    instead (R0034's own proven message-ordering guarantee: input
    transcription always precedes that turn's own assistant content),
    which also fixed a second, previously-latent bug (dispatch tracking
    never reset on a normal `GenerationCompleteEvent`, only on a fatal
    provider error — never exercised by any prior single-turn test).
    **(3)** two English questions were both answered in Polish.
    Reconstructed the exact production snapshot:
    `apps/nexa_cloud_voice_app.py` never passed `language_preference` to
    the snapshot builder (defaults to `None`, so no "Current language
    preference" line was ever appended) and `build_default_session()`
    starts with empty history — **the production snapshot was genuinely
    neutral, NeXa did not force a Polish preference anywhere** — a real
    Gemini native-mirroring reliability gap under live conditions, not a
    NeXa-side bug, activating ADR-0004's own documented Option-B
    fallback. Built the offline detection half, reusing verbatim the
    ALREADY-ACCEPTED local-voice mechanisms:
    `nexa.stt.WhisperCppLanguageDetector` (R0024's own
    `argmax(p_pl, p_en)` LID) and `nexa.conversation.ResponseLanguageResolver`
    (sticky preference set ONLY on an explicit directive like "always
    answer in English", never from the language merely spoken) —
    `_VadToProviderBridge` now buffers each utterance's PCM locally (a
    parallel copy, never delaying what streams live to Gemini) and
    `_consume_provider_events` correlates it with that turn's own final
    transcription, updates any FUTURE fresh-session snapshot on a sticky
    decision, and logs the charter's exact diagnostic keys
    (`SNAPSHOT_LANGUAGE_PREFERENCE`/`TURN_INPUT_LANGUAGE`/
    `TURN_INPUT_TRANSCRIPT`/`LANGUAGE_ROUTING_MODE`). **Honestly did NOT
    build** same-turn steering of the response Gemini is already
    generating for the current turn — no verified, low-risk steering
    primitive was found reachable through the installed Pipecat/
    google-genai stack without a live call to test it, so native
    mirroring (Option A) remains the active per-turn mechanism and the
    gap is explicitly documented (`LANGUAGE_ROUTING_MODE=native`), not
    papered over. **+13 net new tests (923 total, 0 regressions)** —
    including a real Pipecat `Pipeline`/`PipelineWorker`/`WorkerRunner`
    setup/process/cleanup test (construction-only `--dry` is explicitly
    NOT accepted as proof for this) and a language test using REAL
    recorded PL/EN audio fixtures already in the repo (never fabricated
    silence/noise). `ruff`/`pip check`/`git diff --check`/secret-scan/
    import-isolation all clean; local voice completely untouched (only
    `src/nexa/realtime/gemini/runtime.py` and its own test file changed
    this checkpoint). **Hardware acceptance is NOT marked PASS; `M2.6B`
    is NOT marked COMPLETE.** The real Gemini/hardware operator RETEST is
    the one remaining step — NOT YET RUN, see R0038 for the exact retest
    command and READY lines (unchanged from R0035/R0036/R0037).
  - **M2.6B.4A — strict same-turn PL/EN language authority: RESEARCH
    ONLY** (`R0039`, 2026-09-11). R0038 diagnosed but did not fix the
    language-mirroring failure. Benchmarked `WhisperCppLanguageDetector`
    (base/q8_0, the same model local voice uses) on the real PL/EN
    fixtures per the charter's own "measure LID first" instruction:
    **~1.15–1.7s per call, essentially CONSTANT regardless of input
    duration** (0.5s of speech costs about the same as the full 3.5s
    utterance), with no reliable early-truncation point (PL misclassified
    as EN below ~1.5s of speech). Read the installed `whisper.cpp` v1.9.3
    source directly (not guessed): `whisper_lang_auto_detect` always runs
    a full encoder forward pass over a FIXED ~30-second-equivalent
    context (`WHISPER_CHUNK_SIZE`, an architectural Whisper constant,
    `whisper.h:36`), regardless of actual audio length — the same fixed
    cost underlies R0006's own measured ~1.69s full-transcription
    baseline. This confirms the cost is inherent to the model
    architecture, not a config/truncation problem, and not NeXa-side.
    Holding `activityEnd` for this detector on EVERY turn (the charter's
    own strict-mode design, needed to decide same-language vs. switch)
    would therefore cost every ordinary SAME-language turn ~1.1–1.7s more
    than the M2.6A baseline — directly contradicting the charter's own
    stated goal ("same-language turns remain as close as possible to
    M2.6A"). Put this to the user with the measured numbers **before**
    writing any implementation code; **the user chose: pause
    implementation, research a lighter LID model first** (rather than
    ship the design at this cost, or narrow its scope unilaterally).
    Verified the official Gemini Live API docs live via WebFetch (no
    Gemini call): confirmed, verbatim, "the different modalities (audio,
    video and text) are handled as concurrent streams. The ordering
    across these streams is not guaranteed" — independently confirms the
    charter's own caution against treating a realtime text hint as
    deterministic same-turn steering (also confirmed by the installed
    `google-genai` SDK's own docstring AND its runtime enforcement that
    `send_realtime_input` accepts only one argument per call — text and
    audio/`activity_start` can never even be sent together). Confirmed
    "you cannot update the configuration while the connection is open"
    (re-confirms ADR-0004 Amendment 1's own "VERIFIED FACT" independently
    from the official docs, not just from Pipecat's own source). Confirmed
    native-audio-output models (our `gemini-3.1-flash-live-preview`) have
    no `language_code`/`SpeechConfig` parameter at all — "you can restrict
    the languages it speaks in by specifying it in the system
    instructions" is Google's own sanctioned mechanism — this directly
    validates the charter's "controlled language-boundary provider
    replacement" design (destroy old, fresh `CloudContextSnapshot` with
    a language-restricted `system_instruction`, fresh provider, atomic
    swap, replay the buffered utterance once) as the *correct* mechanism,
    once a viable low-latency LID exists to drive it. Surveyed (did NOT
    implement) two lighter-alternative candidates: a smaller `ggml`
    Whisper model (e.g. `tiny`/`tiny.en` — same already-integrated
    `ctypes` binding, very likely meaningfully faster, but not yet
    downloaded/verified in this environment — the `tiny.bin` files
    already present locally are whisper.cpp's own CI test stubs at
    ~0.5MB, confirmed NOT real weights by file size alone, unusable for
    real accuracy); and a dedicated non-Whisper spoken-language-ID model
    (e.g. an x-vector/CNN-class classifier, plausibly tens of
    milliseconds on CPU) — not adopted, since it would require a
    genuinely new, likely much heavier dependency (no
    `speechbrain`/`silero`/`torch`/`langid`/`fasttext` currently
    installed), exactly the kind of "new framework casually" the charter
    says not to install without evidence-backed cause. Recorded the full
    STRICT MODE design as a specification for a future checkpoint —
    **three precisely-named states**
    (`detected_turn_language`/`sticky_language_preference`/
    `active_session_response_language`), reusing R0038's
    `ConversationRouter.recover_from_mid_turn_loss()`/`_ProviderHandle`
    atomic-swap machinery verbatim (never a second reconnection
    architecture), a proposed system-instruction wording following
    existing `CLOUD_ROLE_CARD` convention, and the exact turn flow
    (`activityStart`/audio stream live as today; only `activityEnd` is
    held pending local LID on the full buffered utterance already
    captured by R0038's `_PendingUtteranceAudio`) — **not implemented**.
    Zero `src/nexa/**` or `apps/**` change this checkpoint; full suite
    unchanged at **923 tests, OK (skipped=7)**; `ruff`/`pip check`/
    `git diff --check` all clean. **`M2.6B` remains IN PROGRESS; the
    language-mirroring gap remains open and undecided** — pending either
    a lighter LID being found/verified, an explicit product decision to
    accept the measured ~1.1–1.7s per-turn cost, or a narrower scope
    (e.g. gate only the first turn and turns following an explicit sticky
    command, leaving ordinary mid-conversation acoustic switches to
    native mirroring's existing, measured-unreliable behaviour). R0038's
    other two fixes (VAD bridge lifecycle, playback-generation guard)
    are unaffected and remain ready for a hardware retest independent of
    this open language question.
  - **M2.6B.4B — real lightweight PL/EN LID candidate benchmark: RESEARCH
    ONLY** (`R0040`, 2026-09-11). R0039 proved `base/q8_0` too slow but only
    *surveyed* lighter candidates without benchmarking a real one. This
    checkpoint downloaded the three REAL multilingual whisper.cpp `tiny`
    variants (`ggml-tiny.bin`/`ggml-tiny-q8_0.bin`/`ggml-tiny-q5_1.bin`,
    verified from the official `download-ggml-model.sh` source, MIT-
    licensed, SHA256-verified against HuggingFace's own `x-linked-etag`)
    into an isolated research cache (production `base/q8_0` untouched),
    and extended R0039's own benchmark script (same whisper.cpp v1.9.3/
    ctypes binding, no new framework) with `--model-path`/`--model-label`,
    the full 50-item M2.4B corpus accuracy sweep, a 2-pair
    0.5/1.0/1.5/2.0s/full truncation sweep, and an EOT-visible-latency
    simulation (background LID launched on the first 1.5s of buffered
    speech while the utterance continues — the metric the charter called
    potentially more important than raw inference time). A real
    measurement bug in that simulation (captured the LID task's "done"
    timestamp only after the full simulated sleep, falsely inflating every
    model to ~2005ms) was found and fixed before trusting any number.
    Result: all four models tie at 100% (30/30) on the main corpus; the
    ONE discriminating real fixture (`pl_co_to_są_kolory.wav`) is
    misclassified by `tiny` (full — wrong even at the FULL utterance) and
    by `tiny-q8_0` (wrong at exactly the 1.5s decision point a
    background-LID design would use, despite being fastest at ~511ms
    warm); only `tiny-q5_1` matches `base/q8_0`'s accuracy exactly (4/4
    cross-check, correct at 1.5s). Corrected EOT-visible-latency: **all
    four models show 0.0ms visible latency on every 3.5s cross-check
    case** — LID cost is fully hidden by background execution on
    utterances this long, making accuracy (not speed) the actual
    discriminator. **DECISION GATE: B — TINY-Q5_1 ACCEPTED** — not for
    raw speed (only ~13-20% faster than the rejected `base/q8_0`
    baseline) but for a genuine footprint win at equal accuracy (30.7 MiB
    vs 78.0 MiB disk, -61%; 133.3 MiB vs 203.6 MiB peak RSS, -35%),
    directly validating the charter's "do not assume smaller quantization
    is automatically better" caution (the smaller, slower `tiny-q5_1`
    beats the larger, faster `tiny-q8_0` on accuracy). **Explicit
    unresolved caveat**: the repo's own 10 `short` corpus fixtures (real
    recordings, all 1.344–1.728s long) end at or before a 1.5s-start
    background LID would even begin, so the 0ms-hidden-latency result
    does NOT extend to short utterances — an open design question left
    for R0041. Recommended (NOT implemented) an R0041 design sketch
    reusing R0038's `_VadToProviderBridge`/`_PendingUtteranceAudio`
    buffering and `recover_from_mid_turn_loss` provider-replacement
    machinery verbatim. Zero `src/nexa/**` change this checkpoint; full
    suite unchanged at **923 tests, OK (skipped=7)**;
    `ruff`/`pip check`/`git diff --check` all clean. **`M2.6B` remains IN
    PROGRESS; the language-mirroring gap remains open — now with a
    researched (not yet adopted) lighter LID model.**
  - **M2.6B.4C — M2.6A vs M2.6B language parity audit + Attempt #2
    preparation: PRODUCT DECISION** (`R0041`, 2026-09-11). Operator-
    directed: reverse the LID trajectory — M2.6A (`R0031`, OPERATOR-
    CONFIRMED) already proved native PL/EN mirroring/switching/barge-in
    worked well with NO local LID; Attempt #1 was not a clean same-
    architecture experiment (two real integration bugs, both already
    fixed in R0038, were entangled with the run). Did a source-level
    differential audit of the OPERATOR-CONFIRMED M2.6A spike
    (`docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py`)
    against current production (`service.py`/`runtime.py`/`snapshot.py`)
    across 27 requested dimensions (model, voice, system_instruction,
    initial history/history_config, kickoff, transcription config,
    server VAD, local VAD framing, activityStart/End timing, sample
    rates, service construction args, aggregator config, pipeline
    processor order, hidden language settings, session
    resumption/history, role-card wording, Pipecat defaults, and whether
    Attempt #1's setup failure changed turn framing) — recorded as an
    explicit table in R0041. **Found no concrete architectural
    regression capable of explaining EN->PL by itself.** Confirmed from
    R0038's own live log that the VAD-bridge setup/cleanup crash was
    confined to Pipecat's per-processor metrics lifecycle hooks and
    never touched the actual turn-forwarding logic (new turns DID reach
    Gemini throughout that failed run) — provably independent of the
    language failure. Found exactly ONE genuine wording difference: the
    cloud role card's language-mirroring sentence had drifted from the
    spike's own proven, explicit per-turn framing ("the language the
    user is currently speaking...if explicitly asked...follow that
    request") to a terser "Mirror the user's language" — restored
    (`CLOUD_ROLE_CARD`) as a low-risk alignment, explicitly not claimed
    as a proven fix (unverifiable without a live call, not made this
    checkpoint). Also found and left open (not language-related): the
    hardware pipeline's `_VadToProviderBridge` only forwards audio to
    the provider while a locally-detected turn is open, so Gemini's own
    speech-onset pre-roll buffer (which the spike's continuous-streaming
    design DID populate) is never populated in production — a future
    latency/onset-clipping question, not this checkpoint's. **R0040's
    `tiny-q5_1` finding is explicitly DOWNGRADED from "SELECTED" to
    BEST RESEARCHED FALLBACK CANDIDATE ONLY** — not adopted, not wired
    into production; nothing deleted (models/benchmark/results preserved
    for a future checkpoint if a clean retest ever proves native
    mirroring genuinely unreliable). Added lightweight, non-blocking
    per-turn retest diagnostics (`RuntimeMetrics.canonical_turn_committed`
    gained `user_transcript`/`assistant_transcript`/
    `provider_instance_id` keyword args, logged as
    `USER_TRANSCRIPT`/`ASSISTANT_TRANSCRIPT`/`PROVIDER_SESSION_ID`) built
    only from state already held in memory. **+7 new deterministic
    tests** (consecutive-normal-turns barge-in re-arm; 3
    system-instruction/no-implicit-language-preference tests; 1 proving
    a deliberately 5-second-sleeping fake LID detector never delays turn
    dispatch/commit; 2 diagnostics-logging tests) — **930 tests total, OK
    (skipped=7)**, 0 regressions; `ruff`/`pip check`/`git diff --check`
    all clean; local voice completely untouched (empty diff). R0038's
    fixes re-verified unchanged. **Hardware acceptance NOT marked PASS;
    `M2.6B` NOT marked COMPLETE** — both contingent on a clean operator
    Attempt #2 (exact command + scripted PL/interrupt/EN/EN/PL coverage
    in R0041). If Attempt #2 is clean, native mirroring stays the
    permanent mechanism and strict LID is never implemented; only
    REPEATED clean-runtime EN->PL failure reopens the fallback decision.
  - **M2.6B.4D — remove cloud LID runtime cost before Attempt #2: narrow
    cleanup** (`R0042`, 2026-09-11). R0041 correctly decided NO local LID
    gate in the normal cloud critical path, but the runtime still
    CONSTRUCTED `WhisperCppLanguageDetector` and INVOKED it fire-and-
    forget once per closed utterance. R0041's own test proved only that
    the event loop does not await a slow fake coroutine inline — not
    that a REAL whisper.cpp CPU-bound inference call (~0.5-1.7s of
    native-code work per R0039/R0040's own measurements) has zero
    impact on Pipecat's scheduling/audio playback/AEC/VAD once actually
    invoked on a resource-constrained Raspberry Pi. Per the operator's
    explicit instruction — "normal cloud voice must run with ZERO local
    LID inference" — removed rather than merely proved-non-blocking:
    `build_gemini_voice_runtime` no longer constructs
    `WhisperCppLanguageDetector`/`ResponseLanguageResolver` at all (no
    import, no whisper.cpp model load, no CPU/RAM footprint);
    `_analyze_turn_language` and `RuntimeMetrics.language_diagnostics`
    deleted; `_VadToProviderBridge` no longer accumulates a parallel
    per-utterance PCM buffer; `_PendingUtteranceAudio` deleted after
    confirming (by grep across `src/nexa/realtime/`) it had exactly one
    caller — the deleted diagnostic — and was never used by reconnect/
    mid-turn recovery, which is a structurally separate, untouched
    mechanism (`GeminiLiveProvider.take_pending_audio()`/
    `ConversationRouter.recover_from_mid_turn_loss`, confirmed by an
    empty `git diff` on `service.py`/`router.py`). Verified by test, not
    just inspection: a patched `WhisperCppLanguageDetector.__init__`
    proves zero construction calls; a patched `.detect()` proves a REAL
    Polish-content turn followed by a REAL English-content turn both
    dispatch through the identical native provider path with zero LID
    calls, no provider replacement, and `activityEnd`
    (`user_turn_end()`) returning in under 50ms every time. R0041's
    other changes (`CLOUD_ROLE_CARD` wording, per-turn diagnostics,
    R0038 fixes) and R0039/R0040's fallback research are unchanged/
    preserved (nothing deleted from `docs/research/`). **+2 net new
    tests (932 total, OK, skipped=7)**, 0 regressions;
    `ruff`/`pip check`/`git diff --check` all clean; local voice
    completely untouched (empty diff). **Hardware acceptance NOT marked
    PASS; `M2.6B` NOT marked COMPLETE** — both still contingent on a
    clean operator Attempt #2, which now runs with zero background
    whisper.cpp CPU load competing with Pipecat/audio/AEC/VAD.
  - **M2.6B.4E — Attempt #2 post-interruption audio-loss failure: root
    cause CONFIRMED + fixed** (`R0043`, 2026-09-11). The REAL Attempt #2
    hardware run happened. PASS: `CLOUD_PROVIDER_READY`/`AEC_REF_ACTIVE`
    reached, no VAD-bridge crash, first response audible, **native
    PL/EN mirroring confirmed working live** (PL->PL, EN->EN, PL->PL) —
    R0041/R0042 both hold; R0039/R0040 stay fallback research only, not
    reopened. FAIL: mid-first-reply the operator cleared his throat; this
    non-lexical sound confirmed a real local barge-in (VAD/barge-in
    correctly did its job) and printed FOUR `✂ cloud interruption
    acknowledged` lines; assistant TEXT kept flowing correctly afterward
    but no further assistant AUDIO was ever audible again. Source-audited
    (not guessed): installed Pipecat 1.8.1's
    `FrameProcessor.broadcast_interruption()` fans out TWO
    `InterruptionFrame` instances per call, and BOTH our own
    `provider.cancel()` AND Gemini's OWN independent
    `serverContent.interrupted` server-side ack (`gemini_live/llm.py:
    1332-1333`) each trigger one such broadcast — 2+2=4
    `ProviderInterruptionEvent`s from ONE local confirmation is confirmed
    NORMAL Pipecat/Gemini behaviour, not a NeXa bug (local side already
    idempotent, proven by test). **Confirmed root cause**:
    `GeminiVoiceRuntime._consume_provider_events`'s dispatch-rearm gate
    (`dispatched_for_turn`) depended solely on a fresh, final
    `UserTranscriptionEvent` — a non-lexical sound can confirm a real
    local barge-in and close a real local VAD turn while Gemini produces
    ZERO transcription events for it; with none ever arriving, the gate
    stayed shut, `start_new_generation()` was never called again, and
    every subsequent assistant-audio chunk (for as long as no turn
    produces a final transcript) was silently dropped as
    belonging-to-an-invalidated-generation, while text (bypassing the
    generation guard entirely) kept flowing — exactly the live symptom.
    Investigated Pipecat's own output-transport interruption handling and
    found it correctly cancels+recreates its audio task/queue
    unconditionally and is invoked only once per confirmed interruption
    in this topology — ruled OUT as a contributing cause. **Fix**:
    `GeminiLiveProvider` gained `local_turn_closed_seq` (a
    NeXa/Gemini-independent counter incremented once per
    `user_turn_end()` regardless of transcription outcome); the consumer
    loop now ALSO re-arms dispatch once this counter advances past the
    value recorded when the now-invalidated generation was dispatched --
    proof, from local VAD alone, that at least one more turn has
    genuinely closed even if Gemini never transcribed it. The original
    final-transcription reset is kept unchanged as the (faster) primary
    path -- a strict, backward-compatible addition (all 75 pre-existing
    tests in the module pass unchanged). Honestly disclosed a bounded
    residual risk (Gemini's event ordering is not guaranteed, so a single
    stray trailing chunk could in principle be misclassified as a fresh
    generation at an interruption boundary -- at most one brief artifact,
    never accumulating, vastly preferable to the confirmed alternative of
    permanent silence). Added lightweight diagnostics
    (`LOCAL_BARGEIN_CONFIRMED`/`PROVIDER_INTERRUPTION_ACK`/
    `GENERATION_INVALIDATED`/`ASSISTANT_RESPONSE_DISPATCH`/
    `ASSISTANT_AUDIO_RECEIVED`/`_DROPPED`/`_HW_QUEUED`/`BOT_STARTED`/
    `BOT_STOPPED`/`OUTPUT_INTERRUPTION_BROADCAST`). **+7 new
    deterministic tests** (the core no-transcript reproduction+fix --
    verified to FAIL without the fix and PASS with it restored, both
    checked in this session; repeated-provider-ack idempotency;
    cancel/confirm counts exactly one per confirmation; old-generation
    trailing audio still never reaches hardware; three event-ordering
    variants) -- **939 tests total, OK (skipped=7)**, 0 regressions;
    `ruff`/`pip check`/`git diff --check` all clean; local voice
    completely untouched (empty diff). Recorded the throat-clear as a
    separate, lower-priority NON-LEXICAL FALSE BARGE-IN observation --
    not addressed by disabling barge-in or adding LID/STT to the
    interrupt-confirmation path. **Hardware acceptance remains FAIL;
    `M2.6B` remains IN PROGRESS** — contingent on an Attempt #3 that
    deliberately includes a non-lexical interruption followed by further
    real turns.
  - **M2.6B.4F — strict post-interruption response ownership** (`R0044`,
    2026-09-12). R0043's `local_turn_closed_seq` fallback fix was valid
    but its own disclosed "one stray chunk" residual risk turned out to
    be a CONCRETE, 100%-reproducible defect, not a narrow edge case: the
    fallback only required the counter to advance by 1 past its
    DISPATCH-time value — satisfied merely by the INTERRUPTING sound's
    OWN local turn closing — so trailing OLD-generation audio arriving
    right after that single closure (but before any real new turn) was
    wrongly promoted into a fresh, valid generation and played audibly.
    Verified empirically: reverting to "+1" reproduces the exact failure;
    restoring "+2" corrects it. Exhaustively source-audited installed
    Pipecat 1.8.1/`google-genai` (Live API message types,
    `serverContent.interrupted`, `generation_complete`, `turn_complete`,
    `LLMFullResponseStartFrame`, Bot Started/Stopped frames, WebSocket
    ordering, `CancellationCompleteEvent`, the SystemFrame-priority
    mechanics behind the four provider-interruption acks) and found NO
    airtight in-session boundary exists: `LiveServerMessage`/
    `LiveServerContent` expose no response/turn/generation identifier
    anywhere (confirmed from the full field list);
    `generation_complete`/`turn_complete` are explicitly suppressed for
    an interrupted generation; `LLMFullResponseStartFrame` is untagged
    local bookkeeping provably falsifiable by trailing old audio; none of
    the four acks correlate with the downstream queue that would need to
    be proven empty. Fix: `_ResponseGenerationGuard.interrupt()` now
    records `provider.local_turn_closed_seq` AT INTERRUPT TIME, and the
    fallback re-arm requires it to advance by 2 — one for the
    interrupting utterance's own closure, one for a genuinely SEPARATE
    subsequent turn — closing the concrete CASE-1 regression while
    honestly leaving ONE gap open (CASE 2: old audio arriving after a
    genuinely new turn's own closure, indistinguishable from real new
    content by any local signal) — proven, not merely suspected, by a
    dedicated test documenting the actual outcome. Also added purely-
    diagnostic `ProviderInterruptionEvent.source` tagging
    (`local_cancel`/`remote_server_ack`) — confirmed neither origin can
    serve as a barrier either. Evaluated all 5 charter-listed
    alternatives; concluded provider/session replacement per confirmed
    interruption is the ONLY architecturally airtight option, with real,
    unsized costs (reconnect latency, cloud-context loss, applies to
    EVERY barge-in) — reported, deliberately NOT implemented this
    checkpoint. All 5 adversarial cases run and reported honestly
    (1/3/4/5 PASS, 2 is a documented open gap). **+17 net new tests** —
    **956 tests total, OK (skipped=7)**, 0 regressions;
    `ruff`/`pip check`/`git diff --check` all clean; local voice
    completely untouched (empty diff). **Hardware acceptance remains
    FAIL; `M2.6B` remains IN PROGRESS** — an Attempt #3 exercising a
    non-lexical interruption plus further real turns remains the next
    evidence; a brief stale-audio artifact at an interruption boundary,
    if ever observed live, is expected/documented residual behaviour
    (CASE 2), not a fix failure.
  - **M2.6B.4G — atomic provider replacement on confirmed barge-in**
    (`R0045`, 2026-09-12). R0044 proved no airtight same-session
    response-ownership boundary exists and left CASE 2 (old delayed
    audio after a genuinely new turn's own closure) open. This
    checkpoint changes the architecture instead of refining the
    heuristic: every confirmed local barge-in now atomically replaces
    the Gemini provider/session. The OLD provider's `events()` queue is
    quarantined synchronously in `_on_confirmed` (before any `await` —
    local playback stop is never delayed) and never read again (Option
    A — the same "stop consuming the old provider entirely" isolation
    `recover_from_mid_turn_loss` already used for connection-loss
    recovery, now shared via a new factored-out primitive
    `ConversationRouter.start_fresh_cloud_provider`). A new NeXa-owned
    active-utterance buffer (`_VadToProviderBridge`'s always-on
    per-utterance `bytearray`, sealed on VAD-END into a FIFO) guarantees
    the interrupting utterance — including audio already sent live to
    the OLD provider before confirmation — is replayed exactly once to
    the replacement provider; the new provider is started concurrently
    (`asyncio.gather`) with waiting for the utterance to seal, so both
    provider-ready-first and seal-first timing orderings are handled
    identically. R0044's CASE 2 was reproduced verbatim and is now
    CLOSED: the trailing old-generation audio is dropped because it
    originates from a provider instance whose queue is structurally
    never read again, not because of any turn-closure count — the
    critical proof R0044 could not provide. `_on_confirmed` now also
    commits the interrupted turn (`router.commit_cloud_turn()`); a
    SEPARATE, pre-existing gap was discovered and explicitly deferred:
    `router.begin_cloud_turn()` is never called anywhere in production
    code, so no cloud conversation turn has ever actually been written
    to canonical history in any M2.6A/M2.6B hardware run to date —
    fixing it safely requires resolving a separate risk (an
    interrupting candidate's interim transcript could overwrite a
    still-open turn's `user_text` before it commits), proposed as a
    future **R0046**. R0043/R0044's `local_turn_closed_seq`-based
    fallback re-arm mechanism (`_ResponseGenerationGuard`'s `+2`
    threshold) is REMOVED as superseded (its only use case is now
    handled unconditionally by atomic replacement), reverting that class
    to its simple R0038 form; verified connection-loss recovery never
    depended on it. Added the charter's exact 8 performance
    instrumentation points (`BARGEIN_CONFIRMED_T` …
    `FIRST_NEW_ASSISTANT_AUDIO_T`) — no Gemini call made this
    checkpoint. R0044's own "WHY OLD AUDIO CAN NEVER RETURN" heading
    corrected with an erratum. Net -3 tests (removed 5 R0044 adversarial
    CASE tests + 4 `_ResponseGenerationGuard` unit tests + 1 superseded
    recovery test; added 5 `TestAtomicProviderReplacement` + 2
    `TestVadBridgeQuarantine` tests, using a real second
    `GeminiLiveProvider`/real Pipecat pipeline) — **946 tests total, OK
    (skipped=7)**; `ruff`/`pip check`/`git diff --check` all clean;
    local voice completely untouched (empty diff). **Hardware acceptance
    remains FAIL; `M2.6B` remains IN PROGRESS** — Attempt #3 (unchanged
    launch command) remains the next evidence, now expected to also
    surface the first real measurement of atomic replacement's
    reconnect/fresh-context latency cost.
  - **M2.6B.4H — production canonical cloud turn lifecycle** (`R0046`,
    2026-09-12). R0045's own audit discovered a SEPARATE, pre-existing
    production defect: `router.begin_cloud_turn()` had NO production
    caller at all (confirmed by exhaustive grep — only test files called
    it), so `CloudTurnAccumulator._current` stayed `None` for the life of
    every M2.6A/M2.6B hardware run — audio worked correctly, but canonical
    `ConversationSession.history` never received a single cloud
    conversation turn. Fixed with the smallest state model that avoids the
    overlap hazard R0045 identified (an interruption candidate's own VAD
    start, before confirmation, must never abandon or corrupt the
    still-open turn whose assistant reply it may be interrupting): a new,
    minimal `ConversationRouter.has_turn_awaiting_assistant()` method
    (True iff the current turn already has a final user transcript but is
    not yet committed) gates `_VadToProviderBridge`'s new
    `router.begin_cloud_turn()` call on every local VAD start;
    `_on_confirmed` calls it unconditionally right after committing the
    just-interrupted turn. `CloudTurnAccumulator.on_user_transcription`
    gained one companion guard (`cur.user_final`) closing the
    pre-confirmation window where a candidate's own transcript could
    otherwise overwrite the still-open, already-finalized turn N.
    Post-confirmation, R0045's existing provider-instance isolation
    (unmodified) remains the sole mechanism — no new code was needed for
    that half. Proven via the REAL, unmirrored `_VadToProviderBridge` (a
    real Pipeline/PipelineWorker/WorkerRunner) for the normal-turn-opening
    half, and the REAL `build_gemini_voice_runtime` `_on_confirmed`
    closure (not a mirror) for the confirmed-interruption half — the exact
    "production wiring proof" the charter demanded. A non-lexical
    interruption's own promoted turn (never transcribed) correctly never
    commits and never blocks the next real turn from recovering. A
    `CloudContextSnapshot` built right after a confirmed interruption
    contains every prior committed turn plus the just-interrupted one, and
    structurally can never contain the new not-yet-committed turn. +13 net
    new tests (5 production-wiring-proof, 5 `has_turn_awaiting_assistant()`
    unit tests, 3 `on_user_transcription` guard unit tests) — **959 tests
    total, OK (skipped=7)**, 0 regressions; `ruff`/`pip check`/
    `git diff --check` all clean; local voice completely untouched (empty
    diff). **Hardware acceptance remains FAIL; `M2.6B` remains IN
    PROGRESS** — Attempt #3 (unchanged launch command) remains the next
    evidence, now able to directly confirm `ConversationSession.history`
    actually grows on real hardware for the first time.
  - **Pre-Attempt #3 launch contract audit** (`R0047`, 2026-09-12,
    docs/test-only). The operator ran R0045/R0046's own documented launch
    command (`apps/nexa_cloud_voice_app.py --bargein`) twice; argparse
    rejected it both times — Attempt #3 never started. Root cause:
    documentation drift, not a code defect — `apps/nexa_cloud_voice_app.py`
    has never defined a `--bargein` flag (confirmed via source + full git
    history); that flag belongs only to the unrelated LOCAL voice probe
    (`apps/nexa_bilingual_voice_probe.py`) and its own opt-in
    `HalfDuplexGate` mechanism. Cloud barge-in
    (`BargeInController`/R0045's replacement/R0046's turn lifecycle) is
    constructed unconditionally by `build_gemini_voice_runtime` — no flag
    exists or is needed, so **no runtime/architecture change was made**.
    Corrected both reports' launch commands to
    `.venv/bin/python apps/nexa_cloud_voice_app.py` (no flags), with an
    erratum explaining the mistake. Added
    `tests/test_cloud_voice_app_entrypoint.py` (5 tests, new) — imports
    the app module directly and runs its REAL `parse_args()`/`main()`,
    proving the documented commands parse, `--bargein` is rejected (a
    canary against recurrence), and the REAL `--dry` entrypoint
    constructs `BargeInController` unconditionally. **964 tests total, OK
    (skipped=7)**, 0 regressions; `ruff`/`pip check`/`git diff --check`
    all clean; local voice untouched. **Hardware acceptance remains FAIL;
    `M2.6B` remains IN PROGRESS** — the operator can now safely re-attempt
    Attempt #3 with the corrected, test-verified command.
  - **M2.6B.4I — accepted M2.6A vs production real audio ingress parity
    audit** (`R0048`, 2026-09-12, DIAGNOSTIC ONLY). Real Attempt #3: the
    operator's first utterance ("czarna dziura") was misheard as "Czorna
    Jura"; later in the same session Gemini understood correctly and
    answered normally — the R0043/R0045 post-interruption audio-loss
    failure did not recur. Investigated whether utterance ONSET is
    clipped before reaching Gemini. **Hypothesis CONFIRMED**: source-read
    of installed `pipecat==1.8.1` shows `VADProcessor` forwards every
    frame downstream unconditionally (drops nothing itself);
    `VAD_START_SECS = 0.2` (unmodified default in both M2.6A and
    production) means `VADUserStartedSpeakingFrame` fires only after
    0.2s of already-elapsed voice activity; `GeminiLiveLLMService` has a
    real built-in pre-roll buffer that M2.6A's topology (same Silero VAD
    instance, same pipeline, directly upstream of the LLM service) keeps
    fed and working — but production's `_VadToProviderBridge` (a
    separate processor in a separate hardware pipeline) never calls
    `send_user_audio()` for audio before its own `_turn_open` flag flips
    true, so the provider's own preroll buffer exists in the running
    code but is permanently starved. Proven EMPIRICALLY too: a new
    diagnostic tool (no Gemini, no credential, no playback) drives
    synthetic pre-onset + spoken PCM through the REAL, unmirrored
    `_VadToProviderBridge` in a real Pipecat pipeline — the
    production-forwarded capture is missing exactly the pre-VAD-start
    window the raw capture retains, byte for byte. Onset: CLIPPED.
    Offset: NOT clipped (asymmetric mechanism). Root cause confidence
    HIGH for the mechanism, MEDIUM for fully explaining the one observed
    mishearing. Evaluated 3 fix options — none chosen, none implemented
    (R0048 = evidence, R0049 = fix, per the charter). +10 new
    deterministic tests — **974 tests total, OK (skipped=7)**, 0
    regressions; `ruff`/`pip check`/`git diff --check` all clean; **zero
    `src/nexa/**` changes**; local voice untouched. **Hardware acceptance
    remains FAIL; `M2.6B` remains IN PROGRESS** — the operator can now
    run the real 10-take capture command and listen to the WAV pairs
    before R0049 picks a fix.
  - **M2.6B.4J — restore accepted M2.6A user-audio preroll parity**
    (`R0049`, 2026-09-12). The operator's real R0048 hardware capture
    confirmed the clipping hypothesis on real reSpeaker audio: every
    PRODUCTION_FORWARDED WAV was audibly clipped at the start ("Czarna
    dziura" forwarded as "dziura"/"arna dziura"). Fix: Option A — a
    bounded rolling PCM pre-buffer added to `_VadToProviderBridge`,
    reusing `nexa.stt.utterance_buffer.UtteranceBuffer` VERBATIM
    (unmodified — the same, already-tested, LOCAL-VOICE-proven mechanism
    `nexa.voice.runtime` already depends on) rather than reimplementing
    an equivalent buffer; `git diff --stat -- src/nexa/stt` is empty.
    Capacity derived, never hardcoded, from the ACTUAL constructed VAD
    analyzer's own `start_secs` plus Pipecat's own documented 0.1s
    margin (300ms with production's unmodified `start_secs=0.2` — the
    same effective preroll M2.6A had). One coherent buffer now serves
    both the pre-roll-while-idle role and R0045's own
    active-utterance-accumulation role. R0045's provider-replacement
    logic and R0046's canonical-turn-ownership logic needed ZERO code
    changes — both already correctly receive/gate on whatever PCM the
    bridge now correctly seeds. The charter's own worked barge-in
    example (`[PREE][POST]` → fresh provider receives `[PREEPOST]`
    exactly once) is now a literal passing test. All 18 charter test
    requirements proven, including a new test re-verifying R0042's
    zero-LID guarantee after this checkpoint's new cross-package import.
    Deterministic, no-Gemini synthetic-PCM A/B proof: PASS (the exact
    R0048 test that once proved clipping now proves its absence).
    +9 new tests, +1 zero-LID test, 1 test rewritten for the post-fix
    assertion — **981 tests total, OK (skipped=7)**, 0 regressions;
    `ruff`/`pip check`/`git diff --check` all clean; local voice AND
    `src/nexa/stt` both untouched. **Real-hardware post-fix A/B capture
    performed by the operator — RESULT: FAIL, corrected by R0050 (below):
    300ms materially improved onset retention but did not fully restore
    it.** Hardware acceptance remains FAIL; `M2.6B` remains IN PROGRESS.
  - **M2.6B.4K — residual onset clipping after R0049, forensic audit**
    (`R0050`, 2026-09-12). The operator's post-fix real capture confirms
    R0049's 300ms preroll improved but did not eliminate audible onset
    clipping ("Czarna dziura" → "arna dziura"/"carna dziura"; English
    too — systemic). A new diagnostic tool (`wav_alignment.py`, 10
    tests, no Gemini/hardware) proves byte-exact that
    `production_forwarded.wav` starts precisely 200ms into
    `raw_with_context.wav` in every one of 6 real takes (500ms diagnostic
    pre-context − 300ms retained = 200ms omitted — the fix mechanism
    works exactly as designed). A plain PCM RMS analysis of that omitted
    200ms (no invented detector) shows real, rising acoustic energy in
    8 of 10 real captured takes (both post- and pre-fix), beginning
    roughly 300–460ms before VAD confirms speech start — real speech is
    being cut, not silence. Source re-audit confirms M2.6A's own
    effective preroll was ALSO ~300ms in practice (the
    `SpeechControlParamsFrame` auto-sizing applies identically in its
    co-located-VAD topology) — M2.6A likely had the same underlying
    risk; its "accepted" status reflected overall quality, not verified
    zero onset loss. Deeper VAD latency audit: the nominal
    `start_secs=0.2` is an exact 192ms (6×32ms) confirmation window
    only — it excludes Pipecat's own exponential volume-smoothing delay
    (~100–330ms, data-dependent) and Silero's model-internal confidence
    timing, both additive. This exact investigation was already done
    once before for LOCAL voice: `nexa/stt/utterance_buffer.py`'s own
    docstring (untouched) documents an independent R0006-era empirical
    measurement of real confirmation delay at 288–352ms for the
    identical mechanism — why local voice's own default is 500ms, not
    300ms; R0049 did not reuse this pre-existing answer. **Root cause:
    R0049's 300ms capacity, from an unvalidated generic Pipecat formula,
    understates real onset latency on this hardware (288–460ms range,
    two independent lines of evidence).** Minimum next fix identified,
    NOT implemented (R0050 = evidence, R0051 = fix): reuse
    `UtteranceBuffer`'s own already-validated `PRE_ROLL_MS=500` default
    directly. Zero `src/nexa/**` changes this checkpoint. Hardware
    acceptance remains FAIL; `M2.6B` remains IN PROGRESS. No Gemini call.
  - **M2.6B.4L — use empirically validated 500ms user-audio preroll**
    (`R0051`, 2026-09-12). Implements R0050's identified fix: replaced
    R0049's `AUTOSIZED_PREROLL_MARGIN_SECS`-derived 300ms preroll
    (proven insufficient by real hardware evidence) with NeXa's own,
    already-empirically-validated `nexa.stt.utterance_buffer.
    PRE_ROLL_MS = 500` — an independent, pre-existing R0006-era
    measurement against real speech fixtures for the IDENTICAL VAD
    mechanism, already trusted by local voice, reused directly rather
    than re-deriving a second number. Zero new layering (same
    dependency edge R0049 already introduced for `UtteranceBuffer`
    itself); the now-proven-wrong `AUTOSIZED_PREROLL_MARGIN_SECS`
    constant removed entirely, not left dead. Zero changes to Silero
    params, barge-in thresholds, R0045's replacement/quarantine logic,
    or R0046's canonical-history logic — capacity only. `git diff
    --stat -- src/nexa/stt` remains empty (local voice's own
    `PRE_ROLL_MS`/`UtteranceBuffer` untouched, its own 9-test suite
    re-verified green). Also corrected a same-day R0050 documentation
    slip (claimed the full suite was deferred when it had actually
    already run and passed). All offline validation green — **991
    tests total, OK (skipped=7)**, 0 regressions; `ruff`/`pip check`/
    `git diff --check` all clean; the R0048 probe's `--dry` now
    live-confirms `preroll_ms 500`. **Real hardware post-fix result:
    PASS** (same-day operator follow-up) — real reSpeaker, 10
    utterances, `preroll_ms=500` confirmed from the probe's own printed
    config, byte-exact re-alignment confirms ZERO bytes of the
    diagnostic's own 500ms pre-VAD-start window omitted from any of the
    10 real forwarded captures (`prefix_ms=0.0` every take). Operator
    listened and confirmed: "Czarna dziura" now arrives complete (no
    more "arna dziura"/"carna dziura"/"dziura"), English onset also
    complete, no audible duplication/corruption. **Local hardware
    post-fix acceptance: PASS.** `M2.6B` remains IN PROGRESS regardless
    — a minimal live Gemini conversational validation of this exact fix
    is still required (all evidence so far is local-only), and the
    previously-documented proactive-reconnect-caller gap
    (`ReconnectController` still has no production driver) remains a
    separate, unresolved completion item. No Gemini call this
    checkpoint either.
  - **M2.6B.4M — false self-barge-in / speaker-echo root cause
    diagnostic** (`R0052`, 2026-09-12). A NEW, distinct failure class
    from a live Gemini run: NeXa sometimes triggered her own local
    barge-in while her own speaker audio was playing (repeated `✂ cloud
    interruption acknowledged by provider` with no real interruption),
    especially at high/max volume, `AEC_REF_ACTIVE` present throughout.
    SOURCE AUDIT of real installed source: `AecReferenceHealth.
    barge_in_safe` is a PURE liveness check (reference feed alive),
    never a cancellation-quality measure; `AecReferenceFeeder` forwards
    byte-identical, ungained PCM to both the reference
    (`plug:respeaker` via `aplay`) and audible (`plug:usb_speaker` via
    PortAudio) paths — software-level parity proven exact; no
    `amixer`/software-volume code exists anywhere in
    `src/nexa/voice*`. Real candidate root causes identified but NOT
    ranked/confirmed: (A) a hardware/OS-level volume mechanism on the
    physically separate speaker only (uniquely explains
    volume-dependence); (B/C) two independent OS audio stacks (`aplay`
    vs PortAudio) through two independently-resampling ALSA `plug:`
    devices — a plausible timing-divergence source; (F)
    `BargeInController` has zero echo-discrimination beyond generic
    Silero VAD + AEC liveness. Built a REAL local-only self-echo probe
    (`docs/research/m2_6_cloud_realtime_voice/
    m2_6b4m_self_echo_probe.py`) reusing the actual production
    `AecReferenceFeeder`/`BargeInController`/`AecReferenceHealth`/
    `_ResponseLifecycle`/`SileroVADAnalyzer`
    (`VADParams(stop_secs=0.5)`, matching cloud production) — NO
    Gemini, NO cloud, NO STT/LLM — instrumented with every required
    telemetry point (playback/VAD/barge-in timestamps, real Silero
    confidence/volume, raw mic + far-end-reference RMS/peak);
    supports `--level {low,normal,max}` silent-operator trials and a
    `--control` real "przerwij" human trial. 23 new deterministic
    offline tests for the probe's own pure logic, all passing;
    `--dry` and a full pipeline-construction smoke test both pass on
    this dev machine (structural validation only, not a real-hardware
    measurement). **Root cause NOT determined — explicitly STOPS at
    the diagnostic result**, per its own gate: no fix implemented
    before real reSpeaker + real USB speaker + silent-operator data
    returns. Zero `src/nexa/realtime`, `src/nexa/voice`,
    `src/nexa/voice_tts`, `src/nexa/stt` changes (`git diff --stat`
    empty for all four) — R0051's 500ms preroll, R0045 provider
    isolation, R0046 canonical history, zero local LID, local-voice
    freeze all re-verified, not reopened. **1014 tests total, OK
    (skipped=7)**; `ruff`/`pip check`/`git diff --check` all clean. No
    Gemini call. `M2.6B` remains IN PROGRESS — self-echo root
    cause/fix, the still-pending minimal live Gemini conversational
    validation, and the proactive-reconnect-caller gap all remain open.
  - **M2.6B.4N — self-echo discrimination root cause and production
    fix** (`R0053`, 2026-09-12). R0052's real hardware evidence came
    back: 0/5 false confirmed barge-ins at LOW, 1/5 at NORMAL, 5/5 at
    MAX, with a highly repeatable ~1.08–1.12s onset at MAX (stdev
    ≈0.016s); real "przerwij" control correctly confirmed 2/2. While
    analyzing this data, found and fixed two real bugs in R0052's OWN
    diagnostic instrumentation (documented as an R0052 erratum): Silero's
    real `voice_confidence()` returns a numpy shape-(1,) array, and
    `float()` on it raises under this repo's installed NumPy 2.5.2 --
    the probe's own tap silently defaulted confidence to 0.0 on every
    frame (production's real decision was unaffected); and the probe's
    mic/reference RMS taps double-counted the injected assistant PCM as
    "mic" telemetry (one linear, bidirectional pipeline), contaminating
    the mic-vs-reference comparison in every file including both real
    control runs. Real, direct ALSA system audit (read-only) found the
    actual mechanism: the reSpeaker's reference-injection mixer (card
    `Array`) and the USB speaker's audible mixer (card `UACDemoV10`) are
    two INDEPENDENT ALSA hardware controls; `/etc/asound.conf`'s
    `ctl.!default { card UACDemoV10 }` means the system's one "volume"
    control only ever reaches the USB speaker, never the reSpeaker's own
    reference mixer (found fixed at 0dB). **Leading evidence-backed
    root-cause hypothesis: reference/audible gain ownership
    incoherence** -- the digital reference PCM the XVF3800's AEC models
    against is always unscaled, completely decoupled from the
    physically separate speaker's real volume, so cancellation
    degrades as real volume rises. That this mechanism exists is
    confirmed by direct system inspection; that it is *the* cause of
    the false barge-ins is not yet proven -- pending the post-fix real
    hardware run. **Fix (smallest evidence-backed layer; no VAD/Silero/
    BargeInController/preroll touched):** new `nexa.voice.aec_gain.
    CoherentReferenceGain` reads the audible device's real, current
    ALSA mixer gain (bounded-cost, cached 2s) and `AecReferenceFeeder`
    (new optional `gain_source` parameter, default `None` = exact prior
    unscaled behavior) scales the reference PCM to match. Wired only
    into the cloud runtime; local voice's own stack never passes
    `gain_source`, so its behavior is untouched (existing tests
    re-verified green unmodified, +4 new gain-specific tests).
    **Same-day validation-contract self-check found the probe ITSELF
    had no `gain_source` wired at all** -- fixed before any hardware
    command was given to the operator, plus a new AST-based test
    (`test_self_echo_probe_production_gain_parity.py`, 7 tests) that
    structurally guarantees production and the probe stay wired the
    same way going forward. +47 new tests total across `aec_gain` (17),
    `AecReferenceFeederGain` (4), the probe's extensions (13), and the
    gain-parity check (7), plus a `LocalAudioConfig` field-inventory
    update. **1055 tests total, OK (skipped=7)**; `ruff`/`pip check`/
    `git diff --check` all clean. R0051 preroll, R0045 provider
    isolation, R0046 canonical history, zero local LID, connection-loss
    tests all re-verified green, unmodified. No Gemini call. **UPDATE
    (R0054, same day): real hardware re-validation returned PARTIAL
    IMPROVEMENT / FAIL, not PASS** -- see below.
  - **M2.6B.4N follow-up -- residual self-echo after coherent-gain fix**
    (`R0054`, 2026-09-12). MAX volume, 3 silent trials, gain fix
    confirmed active (`reference_gain_applied=0.8974` throughout) -> 1
    clean, 2 false confirmed barge-ins (was 5/5 pre-fix) -- an
    improvement, but NOT a pass; the same ~1.08-1.11s content-relative
    onset recurred. Explicitly tested and confirmed gain alone cannot
    explain the residual nondeterminism: all 3 trials used the
    identical gain; the real distinguishing signal was
    `speaking_frame_frac` during playback (0.11 clean vs 0.33-0.34
    false) -- duration/sustain, not amplitude. While analyzing the new
    capture (now usable -- R0053 fixed R0052's telemetry bugs), found
    and fixed two MORE real bugs: (1) the probe's own
    `_play_assistant_phrase` burst-injected an entire phrase in one
    `queue_frames()` call; Pipecat's own output-transport queue is
    confirmed UNBOUNDED (no backpressure), so nothing paced that
    injection to real time -- this explained why
    `ref_raw_rms_phase_playback` read 0 frames in every trial (a
    diagnostic-fidelity gap in the probe, not evidence about real
    hardware); fixed by pacing per-chunk injection to match
    production's own real per-`AssistantAudioEvent` cadence
    (production already queues one frame per event, confirmed in its
    own source); (2) `AecReferenceFeeder` called its `gain_source`
    (occasionally a real blocking `amixer` call, measured ~3ms) inline
    on the shared asyncio event loop -- fixed with `run_in_executor`,
    the same pattern already used elsewhere in that file. Source-audited
    both reference and audible timing paths end to end: documented
    every known buffer/chunk size (20ms mic input, 40ms audible
    re-chunking, 100ms reference chunking) and was explicit about two
    stages NOT knowable from source alone (PortAudio's own output
    buffer, `aplay`'s own ALSA buffer) -- no arbitrary timing
    assumptions. No VAD/Silero/BargeInController/preroll code touched;
    no speculative production self-echo fix implemented. +3 new tests.
    **1058 tests total, OK (skipped=7)**; `ruff`/`pip check`/
    `git diff --check` all clean. R0051 preroll, R0045 provider
    isolation, R0046 canonical history, zero local LID, connection-loss
    tests all re-verified green, unmodified. No Gemini call. `M2.6B`
    remains IN PROGRESS: requests one more small real-hardware capture
    (`--capture-pcm --max-lag-ms 500`, 3 MAX trials) before any
    correlation analysis or production fix is attempted; this fix's own
    real-hardware acceptance, the still-pending minimal live Gemini
    conversational validation, and the proactive-reconnect-caller gap
    all remain open.
  - **M2.6B.4N follow-up -- PCM correlation analysis of residual
    self-echo** (`R0055`, 2026-09-13). The R0054-requested capture came
    back: MAX volume, 3 silent trials, `--capture-pcm --max-lag-ms 500`
    -> **3/3 false confirmed barge-ins**, gain fix confirmed still
    active and unchanged. STEP 0 (source-audit the probe BEFORE touching
    any PCM, per this checkpoint's own instruction) found and fixed TWO
    real diagnostic-fidelity bugs: (1) `Recorder.mark_playback_start()`/
    `mark_playback_end()` unconditionally overwrite on every
    `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` -- so every
    PRIOR report's `mic_phase_playback`/`ref_raw_rms_phase_playback`
    windows were built from a POST-interruption RESTART segment, not
    the true pre-confirmation window (proven by both the operator's own
    "Bot started speaking again" observation and independent JSON
    self-consistency: every trial's own `playback_start_t` lands AFTER
    `bargein_confirmed_t`, mechanically impossible for a genuine
    pre-confirm start); root cause was `_play_assistant_phrase`'s
    per-chunk loop having zero awareness of `BargeInController`'s own
    real `broadcast_interruption()`, so it kept injecting the REST of
    the fixture after a confirmed interruption -- fixed with
    `Recorder.confirmed_event` + calling the SAME
    `lifecycle.mark_interrupted()` primitive production's own
    `_on_confirmed` calls (probe-only; no production/VAD/BargeInController
    change); +3 tests. Confirmed the already-captured PCM was NOT
    contaminated by this bug (reference WAV is always the full, uncut
    fixture; the false trigger fires BEFORE confirmation) -- **no new
    hardware capture was needed**. Also documented (not fixed) a
    related whole-trial `cross_correlate_pcm` limitation: it has no
    knowledge that mic recording starts 2.0s before reference
    recording, so its own whole-trial `best_lag_ms` saturates at the
    +-500ms search boundary with near-zero correlation every trial -- a
    methodology artifact, not evidence of low real echo correlation.
    STEP 1-5: re-derived the TRUE playback timeline externally
    (cross-validated within 7-8ms against this checkpoint's own
    reported wall-clock times, and within 28-63ms via an offline
    re-derivation of the REAL installed Silero VAD model -- exact same
    class/params production uses -- that independently reproduces the
    real pipeline's own VAD-start decision from the raw mic WAV alone;
    a bug in that re-derivation itself, `_prev_volume` not being
    persisted per `VADAnalyzer._run_analyzer`'s own source, was found
    and fixed before trusting it). Raw PCM: zero clipping / zero
    near-saturation anywhere, any trial, any window (Class D
    nonlinearity: tested, not supported). Bounded sliding-window
    cross-correlation (+-500ms search, 200ms window / 50ms hop,
    ref-offset now correctly applied) across all 3 trials: lag is
    stable at ~85-130ms for the WHOLE trial (not spiking specifically
    in the failure region -- a real, newly-quantified system latency
    beyond R0054's own documented 60ms of known buffer stages, but NOT
    the driver of which window false-triggers); normalized correlation
    is 4-9x the measured noise floor (0.07-0.08) in the failure region
    (0.31-0.59 named windows, up to 0.64-0.70 in the fine sweep),
    peaking at almost the same relative offset in all 3 trials, right
    at the VAD-start/confirm boundary. **Classification: leading
    mechanism is C (hardware AEC leaves a real, correlated residual of
    the assistant's own audio) with A (the newly-quantified stable
    latency) as a confirmed but non-differentiating secondary finding
    -- honestly E, a documented combination, with C dominant.**
    Confidence MEDIUM-HIGH (n=3, reproducible, cross-validated against
    the real pipeline's own VAD decision). **Per this checkpoint's own
    gate, a NeXa-owned echo/double-talk discriminator ahead of
    `BargeInController` confirmation is RECOMMENDED for the next
    checkpoint as its own design proposal -- NOT implemented this
    checkpoint.** Zero `src/nexa/**` file touched. **1061 tests, OK
    (skipped=7)**; `ruff`/`pip check`/`git diff --check` all clean. No
    Gemini call. `M2.6B` remains IN PROGRESS -- next step is a
    discriminator DESIGN proposal, not another hardware capture.
    **SUPERSEDED same day** by the entry immediately below once the
    product direction changed to reject an XVF3800-specific
    discriminator -- R0055's own PCM evidence stands, only the
    "what to build next" conclusion was revised.
  - **M2.6B.4N follow-up -- existing AEC/double-talk solutions audit +
    portable Acoustic Frontend design** (`R0056`, 2026-09-13,
    RESEARCH/DESIGN ONLY). New product requirement: NeXa must work
    equally well on Pi/XVF3800, Windows, Linux, macOS, iPhone/iPad,
    Android, headsets, and future hardware -- XVF3800 must never become
    NeXa's canonical voice architecture. Before designing anything
    custom, researched existing solutions per an explicit decision
    hierarchy (native hardware/OS AEC first, mature software fallback
    -- WebRTC AEC3 -- second, custom NeXa DSP only as a last resort).
    **Headline finding**: a live, read-only audit of the REAL Pi
    hardware via the official, already-installed Seeed/XMOS `xvf_host`
    tool found the reSpeaker's own `Array` USB card has a SEPARATE ALSA
    mixer (`PCM,1`, currently -20dB) that exactly matches the live
    `AEC_FAR_EXTGAIN=-20` firmware readback -- a reference-path
    attenuation R0053's own `CoherentReferenceGain` fix has never
    touched (that fix only matches the SEPARATE `UACDemoV10` audible-
    speaker mixer). The official XMOS tuning guide (fetched, quoted)
    confirms this exact USB-variant auto-tracking mechanism, meaning the
    XVF3800's own adaptive filter has likely modeled the echo path
    against a reference ~10x quieter than the true acoustic echo the
    whole time -- a native, zero-code, fully-reversible root-cause
    candidate more fundamental than anything R0053-R0055 investigated.
    Also found: R0055's measured ~85-130ms reference-to-mic lag is
    35-50x the vendor's own stated ideal (<=40-sample) target (needs
    `xvf_tools.py`/`mic_ref_correlate`, not yet on this machine, to
    properly recalibrate `AUDIO_MGR_SYS_DELAY`); `PP_DTSENSITIVE=0`
    (current) is actually the vendor's documented STARTING point for
    tuning, not a misconfiguration. Researched WebRTC AEC3 (portable
    fallback of choice), PipeWire's echo-cancel module (WebRTC-only
    backend, confirmed via live fetch: cleaned audio only, no
    double-talk/ERL/ERLE signal exposed to clients), GStreamer's
    webrtcdsp/webrtcechoprobe, Apple Voice Processing I/O, Android
    AcousticEchoCanceler (official availability caveat), Windows
    (weaker native guarantees) -- platform-knowledge sections clearly
    flagged where a live fetch could not be verified. Designed a
    device-agnostic `AcousticFrontend`/`AcousticCapabilities`/
    `AcousticEvidence` contract (optional-evidence, capability-declared)
    `BargeInController` would consult via ONE new optional constructor
    parameter (`acoustic_frontend=None` default = today's exact
    behavior, mirroring the already-proven `aec_health`/`gain_source`
    zero-risk-when-absent pattern) -- `BargeInController` remains the
    SOLE, unmodified interruption authority on every platform; only what
    feeds it varies per device (`Xvf3800AcousticBackend` wraps existing
    R0053/R0054 components verbatim on the Pi; `AppleVoiceProcessingBackend`;
    `AndroidNativeAcousticBackend` + WebRTC fallback; `WebRtcAecBackend`
    default on Windows/Linux; `PassthroughAcousticBackend` for headsets).
    **Explicit recommendation for the CURRENT Pi problem: do NOT
    implement a discriminator or the Xvf3800 backend yet -- first try
    native tuning** (raise the `Array` `PCM,1` mixer toward unity and
    re-run R0055's own unmodified capture command; only if insufficient,
    pursue `AUDIO_MGR_SYS_DELAY` recalibration once `xvf_tools.py` is
    obtained, then `PP_GAMMA_*`; a custom NeXa double-talk algorithm
    remains a documented, ready-to-resurrect fallback only if native
    tuning + WebRTC both prove insufficient). Zero `src/nexa/**` change,
    zero test change, zero XVF3800 parameter written (every `xvf_host`
    call was a bare read). **1061 tests, OK (skipped=7)**, unchanged;
    `ruff`/`pip check`/`git diff --check` all clean. No Gemini call.
    `M2.6B` remains IN PROGRESS -- next checkpoint (proposed R0057) is
    the native XVF3800 ALSA-mixer experiment, not a discriminator or
    backend implementation. **SAME-DAY PRE-R0057 CORRECTION** (still
    R0056, same file/commit sequence): softened the -20dB "10x quieter"
    causal claim to CONFIRMED (exact -20dB match + official auto-
    tracking mechanism) vs. HYPOTHESIS (that NeXa's split-speaker
    topology violates the mechanism's single-speaker assumption enough
    to matter) vs. UNKNOWN (internal AEC compensation) -- resolvable
    only by experiment; withdrew the "35-50x the XMOS ideal" timing
    comparison (not the same observation point as R0055's own external
    measurement; no `AUDIO_MGR_SYS_DELAY` value derived from it). Wrote
    a reversible R0057 procedure into the report (originally:
    two reboots to avoid `AEC_AECCONVERGED`'s documented latch). **Same-day
    Correction 3, before any hardware command was issued**: a
    pre-execution review rejected the `REBOOT`-based design (`REBOOT`
    resets EVERY writable parameter to firmware default on a device
    confirmed `BLD_MODIFIED=TRUE`, with no guarantee "default" matches
    this thread's own already-recorded baseline for anything but the
    one parameter under test). **Revision 2**: no reboot -- `Array
    PCM,1` changed live, both measured trials gated by an identical
    fixed 60-second local-fixture warm-up instead of the latched
    `AEC_AECCONVERGED` flag. **Same-day Correction 4, before any
    hardware command was issued**: source-audited and CONFIRMED the
    60-second warm-up itself was unreliable -- it used `--repeats 17`
    against the diagnostic probe assuming each repeat delivers the full
    ~3.5s fixture, but R0055's own confirmed-bug fix in
    `_play_assistant_phrase` truncates injection the instant a real
    confirmed barge-in fires (measured 3/3 at MAX by R0055 itself), and
    since the false-confirm RATE is exactly the variable this
    experiment changes between conditions, `--repeats 17` could deliver
    a different real warm-up duration to each condition, biasing the
    comparison. Fixed in the diagnostic probe only (`git diff --stat --
    src/nexa` confirmed empty): `_play_assistant_phrase` now returns
    the bytes it actually queued; a new `_run_warmup()` +
    `--warmup-seconds` flag loops it with `confirmed_event=None`
    (structurally never truncates) and counts real delivered bytes
    until they PROVE at least the requested duration; measured-trial
    behavior is completely unchanged. +4 tests (`TestRunWarmup`);
    **1065 tests, OK (skipped=7)**; `ruff`/`pip check`/`git diff
    --check` all clean. **Same-day Correction 5, before any hardware
    command was issued**: source-audited and CONFIRMED Correction 4's
    own claim ("`AecReferenceFeeder` never reacts to `InterruptionFrame`")
    was WRONG -- it inherits that reaction from the base `FrameProcessor`
    class (confirmed by reading installed Pipecat source), so a real
    confirmed self-barge-in could still occur during Correction 4's own
    warm-up (`BargeInController` stayed fully armed) and discard
    already-queued reference PCM before it reached `AecReferenceFeeder`
    -- "queued" was never proof of "accepted." Fixed in the diagnostic
    probe only (`git diff --stat -- src/nexa` confirmed empty):
    `arm_bargein=False` (proven from `BargeInController
    ._handle_speech_started`'s own `response_in_flight` guard) makes
    confirmation structurally unreachable during warmup; `_run_warmup`
    verifies three ways from real production telemetry --
    `bargein.telemetry.interrupt_confirmed` stays zero, a new
    `Recorder.ref_accepted_bytes` counter (tapped at the existing
    `_PlaybackWatcher` position, unchanged, after `aec_feeder`) proves
    real acceptance, and new playback start/stop counters prove every
    repeat completed cleanly. +2 net probe tests + 1 new test on the
    REAL `BargeInController` (`tests/test_bargein_m2_5b.py`, pure
    coverage, zero `nexa/voice/bargein.py` lines changed). **1068 tests,
    OK (skipped=7)**; `ruff`/`pip check`/`git diff --check` all clean.
    R0057 now uses one probe invocation per condition (`--warmup-seconds
    60 --level max --repeats 3 --capture-pcm --max-lag-ms 500`) whose
    own output proves ACCEPTED (not merely queued) warm-up duration,
    zero interruptions during warmup, and clean playback completion.
    Still not executed; no parameter/mixer changed by any correction
    pass.
  - **M2.6B.4N follow-up -- warm-up lifecycle diagnostic checkpoint**
    (`R0057`, 2026-09-13, **not** the R0057 gain A/B experiment itself,
    which remains NOT EXECUTED). Real hardware attempts to run the
    experiment's own warm-up kept reproducing "got cancelled from
    outside" + an indefinite teardown stall, at three different
    warm-up durations/repeat counts (60s/18, 10s/3, and now 1s/1).
    **Confirmed, with a real captured traceback, the initiating cause**:
    an `AssertionError` at `m2_6b4m_self_echo_probe.py:1393`, inside
    `_run()`'s own third post-warm-up assert
    (`playback_start_count == playback_stop_count == repeats_run`) --
    `_run_warmup()`'s bounded wait polls only `ref_accepted_bytes`
    catching up to `delivered_bytes`, never `playback_stop_count`, so
    it returned on real hardware with the final repeat's own
    `BotStoppedSpeakingFrame` not yet observed
    (`playback_start_count=1`, `playback_stop_count=0`). Because this
    assert sits outside `_run()`'s own `try:`/`finally:`, the exception
    orphans `run_task`; `asyncio.run()`'s own cleanup then force-
    cancels it, producing exactly the observed log line -- reproduced
    a third time, now proven independent of warm-up duration/repeat
    count. A new deterministic offline characterization test
    (`TestRunWarmup.test_characterization_warmup_can_return_before_final_playback_stop_observed`,
    explicit `asyncio.Event` control, no sleeps) independently
    reproduces the identical condition without hardware, before the
    hardware run confirmed it. Added probe-only instrumentation
    (`_run_with_initiating_exception_report` wrapping `_run()` inside
    the `asyncio.run()` boundary; explicit `RUNNER_END_OUTCOME=`/
    `RUN_TASK_AWAIT_OUTCOME=` teardown markers replacing silent
    `contextlib.suppress`) that made this exception directly observable
    for the first time -- `git diff --stat -- src/nexa` confirmed
    empty throughout. The one real-hardware reproduction run
    (`--warmup-seconds 1 --level max --repeats 0`, under an external
    bounded `timeout` supervisor) was externally terminated (exit 124)
    after teardown stalled again post-cancellation -- explicitly
    reported as an externally-terminated run, not a clean pass; the
    deeper cause of that second-half teardown stall remains an open
    unknown (the existing stack-dump watchdog lives inside `finally`,
    never reached when the exception escapes before it). Hardware
    (`Array PCM,1` -20.00dB, `UACDemoV10` -0.94dB) verified unchanged
    before/after. 48/48 probe tests + 92/92 combined with bargein
    tests pass; full suite 1069 tests/1 pre-existing unrelated failure
    (`test_tts_server.py`, untouched, confirmed via empty `git diff
    --stat`)/skipped=7; `ruff`/`git diff --check` clean. No Gemini
    call. Not pushed. Smallest proposed fix (NOT implemented this
    checkpoint): extend `_run_warmup`'s existing bounded-wait pattern
    to also require `playback_stop_count` catch-up, and/or move
    warm-up + its asserts inside the existing `try:`/`finally:`.
    **`M2.6B` remains IN PROGRESS; R0057's own gain A/B experiment
    remains NOT EXECUTED** -- next step is implementing this fix, then
    re-attempting the hardware warm-up, before returning to the gain
    A/B experiment itself.
  - **M2.6B.4N follow-up -- warm-up lifecycle fix and verification**
    (`R0058`, 2026-09-13, implements R0057's own proposed fix; **not**
    the R0057 gain A/B experiment itself, still NOT EXECUTED). Both
    fixes implemented: (1) `_run_warmup`'s bounded wait now ALSO
    requires `playback_stop_count` to catch up to `repeats_run` (not
    just `ref_accepted_bytes`), with exact equality checks -- raises a
    new `WarmupIncompleteError` (full expected-vs-observed evidence
    attached) instead of ever returning an incomplete result; the three
    former bare `assert` statements (silently stripped under `python
    -O`) are gone. (2) `_run()` now wraps the warm-up call, its
    validation, and the measured trial loop -- not just the trial loop
    as before -- inside a new `_run_body_with_guaranteed_cleanup()`
    primitive guaranteeing `_shutdown_runner()` (using ONLY
    `WorkerRunner.end()`, the runner's own public API) is attempted
    exactly once regardless of outcome, and promoting a failed cleanup
    into a raised `RunnerShutdownError` when the body itself succeeded.
    Caught and fixed a real bug in the cleanup helper's own first draft
    before trusting it: verified via a standalone reproduction script
    that `asyncio.wait_for(existing_task, timeout=...)` does NOT
    reliably bound a task that catches cancellation and keeps running
    (its `TimeoutError` conversion only fires if a `CancelledError`
    actually propagates out, which never happens if the awaited task
    swallows it) -- fixed with the non-cancelling `asyncio.wait({task},
    timeout=...)` plus an explicit, scoped `run_task.cancel()`
    escalation instead. Removed the now-obsolete characterization test
    and added 12 new deterministic tests (warm-up completion + cleanup
    coverage, fake doubles only, no Pipecat). **One real-hardware
    reproduction of the EXACT command that previously failed**
    (`--warmup-seconds 1 --level max --repeats 0`, same external
    `timeout` supervisor) **now completes naturally with exit code 0**
    (was: AssertionError -> orphaned run_task -> indefinite stall ->
    exit 124) -- matched start/stop/repeats counters, zero confirmed
    interruptions, zero measured trials, clean shutdown outcome, mixer
    values unchanged before/after, no leftover processes. Directly
    compared (detached worktree at the pre-fix baseline, same
    shell/venv, removed after) the one pre-existing full-suite failure
    (`test_tts_server`'s nice-value test) and confirmed it fails
    identically at baseline -- pre-existing, environment-caused (this
    sandbox's own base nice level), unrelated to this checkpoint.
    Corrected four overclaims/predictions in the R0057 report text
    itself (identity preserved, not renamed): directly-confirmed vs
    inferred historical evidence, withdrew a universal
    duration-independence claim, marked the nice-value provenance as
    inference-at-the-time (now confirmed), replaced predicted git
    cleanliness with the observed fact. New tracked evidence manifest
    (`docs/research/m2_6_cloud_realtime_voice/R0058_evidence_manifest.md`).
    **1080 tests, 1 failure (confirmed pre-existing), skipped=7**;
    `ruff`/`git diff --check` clean; `git diff --stat -- src/nexa`
    empty. No Gemini call. Not pushed. **`M2.6B` remains IN PROGRESS**
    -- next step is executing the still-pending R0057 gain A/B
    experiment itself.
  - **M2.6B.4N follow-up -- post-R0058 review fixes and full 60-second
    baseline warm-up validation** (`R0059`, 2026-09-13; **not** the
    R0057 gain A/B experiment, still NOT EXECUTED). Resolved three
    review points against R0058's own implementation: (1) a comment
    claiming `asyncio.wait_for` is generically "reliably bounded" for a
    bare coroutine vs. an existing Task was wrong (verified against
    installed CPython 3.13.5 source: the determining factor is whether
    `CancelledError` actually propagates out, not coroutine-vs-Task) --
    corrected to state the VERIFIED reason `wait_for(runner.end(...))`
    is safe here: `WorkerRunner.end()` -> `_finish_running_workers` ->
    `WorkerBus.send()` -> `AsyncQueueBus.publish()` has NO suspension
    point anywhere in this probe's single-worker/default-bus config,
    confirmed by reading all four; no hard wall-clock/process deadline
    claimed, external supervision remains necessary. (2) confirmed a
    real gap: `_run_body_with_guaranteed_cleanup` reported a body
    failure's traceback only AFTER cleanup fully resolved -- if cleanup
    stalls, the original failure's evidence would not reach disk until
    the stall ends, reproducing R0057's own visibility gap one layer
    higher. Fixed (smallest probe-only change): print+flush the
    exception immediately, before cleanup is attempted; propagation
    unchanged. New test proves the traceback is observable via captured
    stdout while a controlled, event-gated cleanup is still genuinely
    pending. (3) investigated whether `_ResponseLifecycle`'s own state
    (real source read, plus Pipecat's actual
    BotStartedSpeakingFrame/BotStoppedSpeakingFrame generation in
    `base_output.py`) could let a delayed prior-repeat stop corrupt
    lifecycle state after the next repeat's `mark_dispatched()` --
    confirmed the race EXISTS but is INERT: never produces an incorrect
    `on_finished()`, and has zero effect on
    `Recorder.playback_start_count`/`playback_stop_count` (the counters
    `_run_warmup`'s own completion proof actually uses), which are
    driven by a structurally-ordered, single-consumer FIFO in Pipecat's
    own output transport -- cumulative equality follows from a
    source-confirmed structural guarantee here, not a potentially-fooled
    heuristic. No functional defect found; no code change, no arbitrary
    sleep, no repeat-pacing change. Then validated exactly one full
    60-second baseline warm-up on real hardware
    (`--warmup-seconds 60 --level max --repeats 0`, external
    150s/10s-grace supervisor, a fresh bound not a reuse of the prior
    60s one): 18 repeats (matching the 3.504s fixture's own derived
    expectation exactly), `playback_start_count == playback_stop_count
    == repeats_run == 18`, `accepted_s=63.07 >= 60.0`, zero confirmed
    interruptions, zero measured trials, clean non-escalated shutdown,
    exit code 0, mixer values unchanged before/after, no leftover
    processes. Also recorded (evidence-limit, not a defect) that
    `BotStoppedSpeakingFrame` is a logical frame-level signal, not
    physical-speaker-finished proof -- alongside the existing
    `ref_accepted_bytes` evidence-limit note, for the eventual gain A/B
    readiness assessment. **60/60 probe tests (59+1), 104/104 combined
    with bargein tests**; `ruff`/`git diff --check` clean; `git diff
    --stat -- src/nexa` empty (full project suite not re-run -- its one
    known, already-confirmed pre-existing failure is unrelated and
    unchanged). No Gemini call. No TV tests. No dependency change. Not
    pushed. New tracked evidence manifest
    (`docs/research/m2_6_cloud_realtime_voice/R0059_evidence_manifest.md`).
    **`M2.6B` remains IN PROGRESS** -- next step is executing the
    still-pending R0057 gain A/B experiment itself, now with both the
    warm-up completion proof and the runner cleanup guarantee validated
    at the full 60-second scale the experiment requires.
  - **M2.6B.4N follow-up -- reference-observability fix and gain A/B
    experiment procedure preparation** (`R0060`, 2026-09-13; the
    experiment itself remains NOT EXECUTED). Closed a
    reference-observability gap: `ref_accepted_bytes` never surfaced
    `AecReferenceFeeder`'s own NEGATIVE evidence (`chunks_dropped`
    queue-overflow drops, `AecReferenceHealth.failure_count` start/write
    failures) -- added two new pure, offline functions reusing ONLY
    those existing counters (no new DSP, no feeder-behavior change),
    `None` for unavailable vs. explicit `0` for a genuine zero.
    `build_probe_pipeline` now also returns `aec_feeder` (previously
    unreachable from `_run()`); `_run_warmup`/`_run_silent_trial`/
    `_run_control_trial` gained optional `aec_feeder`/`aec_health`
    kwargs (every existing call site backward compatible) and report
    `aec_reference_telemetry` per warm-up and per measured trial.
    Corrected `ref_accepted_bytes`'s own docstring for exact-tap-location
    accuracy (field/behavior unchanged): proof of passage through the
    feeder's own acceptance logic, not a successful write and not
    physical hardware ingestion. +8 tests. Then prepared -- explicitly
    did NOT execute -- the previously-accepted gain A/B experiment:
    verified (read-only, not guessed) `Array 'PCM',1'` is linear
    1dB/step over exactly [-60.00dB, 0.00dB] (raw 40 = -20dB current,
    raw 60 = exactly 0.00dB) and `UACDemoV10` sits at its own verified
    MAX (never written). New tracked procedure document
    (`R0057_gain_ab_experiment_procedure.md`) and a real, non-executable,
    approval-gated wrapper script (`run_r0057_gain_ab_condition.sh`,
    requires `--i-have-explicit-operator-approval`) implementing: one
    probe invocation per condition (fresh 60s warm-up + 3 trials +
    capture-pcm, never reusing R0059's own warm-up-only run); a
    trap-based rollback to -20dB on success/failure/interruption with
    independent readback; a 240s/15s-grace external supervisor;
    invalid-run criteria (explicitly excluding a confirmed barge-in
    during a measured trial, which is the outcome being measured); and
    a comparison methodology requiring every trial reported separately,
    exposure-adjusted, with an explicit caution against overclaiming
    from 3 trials/condition. Verified the script's own approval-gate
    exits before any hardware write for every invalid invocation
    tested. **68/68 probe tests (60+8), 112/112 combined with bargein
    tests**; `ruff`/`git diff --check` clean; `git diff --stat --
    src/nexa` empty. No Gemini call. No TV tests. No dependency change.
    No audio hardware run. Not pushed. **`M2.6B` remains IN PROGRESS.
    The R0057 gain A/B experiment remains NOT EXECUTED** -- awaiting
    Andrzej's explicit approval to run either condition.
  - **M2.6B.4N follow-up -- gain A/B wrapper corrections, offline only**
    (`R0061`, 2026-09-13; the experiment itself remains NOT EXECUTED, no
    approval given). Review found seven concrete defects in the R0060
    wrapper script; all fixed: (1) `set -e` made EXIT_CODE bookkeeping
    unreachable on any real probe failure -- removed `-e` entirely,
    every intentionally-fallible command checked explicitly instead.
    (2) mixer readbacks were printed but never validated -- fixed with
    parsed read/validate helpers that abort before mutation on mismatch
    or an unreadable value. (3) `EXPECTED_UAC_RAW` was dead code -- now
    actually used. (4) the persisted log excluded all wrapper-side
    output -- fixed with `exec > >(tee -a "$LOG") 2>&1` capturing
    everything in one file. (5) condition B could overwrite condition
    A's own fixed-name PCM files -- fixed at the wrapper level with a
    marker-file/`find -newer` current-run inventory that archives each
    run's own JSON+WAVs, hash-verified, into a per-condition directory
    with a MANIFEST.txt. (6) signal handling didn't distinguish
    interruption status or stop the child before rollback -- fixed with
    dedicated INT/TERM handlers that terminate and reap ONLY this
    script's own child, then run the same idempotent rollback, never
    chaining into another condition. (7) comments incorrectly implied
    an unconditional rollback guarantee -- corrected to state a wrapper
    SIGKILL or power loss cannot be intercepted by any shell trap. New
    documented exit-code convention (0/2/90/91/92/93/130/143/probe's
    own code). Verified all seven with a new offline test suite (13
    tests) using a stateful fake `amixer` and a controllable fake probe
    launcher, never touching real hardware (confirmed via a dedicated
    test) -- covering normal completion, nonzero probe status,
    supervisor timeout, precheck mismatch (3 variants), failed
    set/readback, failed rollback (isolated from failed set),
    SIGINT/SIGTERM (including confirming the child process is actually
    gone), and distinct A/B artifact preservation. Aligned the
    procedure document's invalid-run criteria: telemetry checked for
    warm-up AND every trial, `respawns_delta`/inactive-feed added as
    invalidating, missing telemetry stated as UNKNOWN never zero, a
    lifecycle-timeout warning added as a validity flag, and an explicit
    instruction not to use the probe's own known-misaligned
    `cross_correlation` as evidence of low echo. Clarified a single
    future approval may cover the full A-then-B experiment (conditional
    on A's own validity). Also corrected two factual errors in the
    R0060 report itself (identity preserved): `TestAecReferenceTelemetry`
    has 6 tests not 7; R0059 never recorded feeder-drop telemetry at
    all, so its value there is UNKNOWN, not "confirmed clean." **13/13
    new wrapper tests, 112/112 probe+bargein tests unchanged**;
    `ruff`/`bash -n`/`git diff --check` clean; `git diff --stat --
    src/nexa` empty. No Gemini call. No hardware writes -- every real
    `amixer` call this checkpoint was a bare read-only confirmation,
    never through the wrapper. Not pushed. **`M2.6B` remains IN
    PROGRESS. The R0057 gain A/B experiment remains NOT EXECUTED** --
    no approval has been given.
  - **M2.6B.4N follow-up -- gain A/B wrapper deep-review corrections,
    offline only** (`R0062`, 2026-09-14; the experiment itself remains
    NOT EXECUTED, no approval given). A second, deeper review found
    R0061's own 13-test suite did not actually prove several of its own
    claims -- fixed the underlying wrapper gaps and rewrote the test
    doubles/suite so each claim is backed by a test that could fail
    against the previous behavior. (1) Zero-writes-on-failed-precheck:
    an explicit `MUTATION_ATTEMPTED` flag set only immediately before
    the one condition write; `rollback()` skips entirely when unset;
    a recorded-invocation-log now asserts zero `sset` calls on every
    precheck-failure variant (5 tests, incl. new wrong-control-identity
    and switch-off cases) -- R0061's own test had instead expected a
    write on this path. (2) Final hardware-state validation: rollback
    now independently re-reads both `Array 'PCM',1'` and both
    `UACDemoV10` channels (exact control identity/limits/switch, not
    just a matching integer), never issuing a corrective UAC write; an
    unreadable/mismatched final UAC state forces exit 91 (new
    `TestFinalUacCheck`, 2 tests). (3) Owned-process termination: the
    probe now runs under its own `setsid`-created process group,
    terminated via `kill -TERM/-KILL -- "-$PGID"` with polling and
    bounded SIGKILL escalation, verified CONFIRMED-EMPTY before
    rollback; replaced the old environment-marker/pgrep test with exact
    recorded-PGID verification and a new fake-probe SIGTERM-ignore +
    child-spawn mode that forces and proves the real escalation path.
    (4) Archive acceptance: fake probe now uses the real probe's fixed
    filenames for every condition (label moved into file content) so
    identical-filename collision risk is genuinely exercised; archive
    requires exactly the expected JSON + all expected WAV pairs (exit
    93 otherwise, partial evidence still archived); MANIFEST.txt now
    records an explicit original-JSON-path -> archived-file+hash
    mapping; condition A's archived bytes verified byte-identical
    before/after B runs; a `mkdir`-based concurrency lock (exit 94) now
    prevents two invocations from sharing capture locations. (5)
    Operational failures: storage/marker creation now explicitly
    checked, aborting (exit 95) before the lock/precheck/any mutation.
    (6) Fixed the fake `amixer`'s own UAC dB formula (raw 147 was
    reporting 0.00dB; real hardware verified this checkpoint as
    -0.94dB); added dedicated rejection tests for the fake's
    already-correct-but-previously-untested invocation rejection.
    Strengthened the procedure document: execution success (exit 0) now
    stated explicitly as a precondition for trusting a run's data, never
    proof of scientific validity; a measured-trial lifecycle-timeout
    warning now stated as remaining UNRESOLVED evidence that must not
    silently qualify condition A's trials as sufficient to proceed to
    condition B. Corrected the R0061 report in place (identity
    preserved) naming exactly which of its claims its own test suite did
    not substantiate. **24/24 wrapper tests pass in a single full-suite
    run** (up from 13; probe/bargein regression suites deliberately not
    re-run -- no `src/nexa` production code touched); `ruff`/`bash -n`
    clean; `git diff --stat -- src/nexa` empty. No Gemini call. No
    hardware writes -- every real `amixer` call this checkpoint was a
    bare read-only confirmation, never through the wrapper. Not pushed.
    **`M2.6B` remains IN PROGRESS. The R0057 gain A/B experiment remains
    NOT EXECUTED** -- no approval has been given.
  - **M2.6B.4N follow-up -- gain A/B wrapper EXTERNAL-review
    corrections, offline only** (`R0063`, 2026-09-14; the experiment
    itself remains NOT EXECUTED, no approval given). An external source
    review of R0062's own wrapper found six further concrete defects;
    all fixed, each backed by a new offline test that fails against
    R0062's own behavior. (1) Process-group ownership race at launch --
    R0062's `ps -o pgid= -p "$CHILD_PID"`, read immediately after
    backgrounding, could sample before `setsid()` took effect and
    observe the wrapper's OWN group -- fixed with a deterministic
    startup handshake: the launched child writes its own `$$` to a
    marker file strictly after `setsid()` succeeds and strictly before
    it execs the real command; the parent only reads what the child
    confirmed, cross-checked against the wrapper's own pgid (exit 97 on
    a match or an unconfirmed handshake); an interruption during the
    handshake terminates the known child by PID directly, never a group
    signal. A self-found race in this checkpoint's own first draft (a
    redundant, racy second `ps` re-check) was found via the new test
    suite and removed. (2) A failed process-group cleanup only printed
    a warning and could still produce exit 0 -- fixed with a persisted
    `CLEANUP_STATUS` checked before reporting success (exit 96);
    `pgrep` reporting "no matches" is now distinguished from `pgrep`
    itself failing to inspect, never treated as verified emptiness.
    (3) Persisted-log readiness is now confirmed (synchronous probe
    write + a canary line's actual on-disk appearance, bounded poll)
    BEFORE the precheck. (4) Mixer validation is now EXACT whole-line
    matching, not substring (`'PCM',10`/`0 - 600` no longer pass for
    `'PCM',1`/`0 - 60`); the switch check now rejects a genuinely MIXED
    on/off UACDemoV10 state; rollback's Array readback is now ALWAYS
    attempted even when the restore write itself reports failure,
    deriving the outcome from what is OBSERVED. (5) Archive
    completeness now also requires the exact expected WAV count and a
    validated JSON<->WAV mapping (each trial's own mic/ref path must
    exactly match one of this run's own newly-archived originals, never
    a basename coincidence); hash/manifest failures now propagate via
    individually-checked statements, never a surrounding block's own
    aggregate exit status. (6) The fake `amixer` now checks exact arity
    and the actual control identifier, not just card+verb. Procedure
    document §4/§5/§7 updated (exit codes 96/97 added). Corrected the
    R0062 report in place naming all six findings. **ROUND 2 (same
    uncommitted checkpoint): a further external review found five more
    defects, all fixed** -- (1) the startup handshake was one-way (child
    exec'd right after publishing its marker, before the parent finished
    validating) -- fixed with a two-way ack the child must wait for
    before it may ever exec, plus group-kill of the known-safe
    `CHILD_PGID_CANDIDATE` if interrupted in the narrower pre-ack window.
    (2) two unconditional `wait` calls could block rollback indefinitely
    on a failed termination -- removed; unverified cleanup now
    QUARANTINES the concurrency lock and preserves (never unlinks)
    original evidence. (3) the mapping loop silently skipped a trial with
    both paths empty -- fixed to reject it, require exact per-trial/role
    basenames, and propagate JSON parse errors. (4) manifest writes and
    source removal were unchecked, and the manifest tmp file could live
    on a different filesystem than the claimed-atomic `mv` target -- both
    fixed. (5) a failed rollback write could still report "clean" success
    if the readback happened to observe baseline anyway -- fixed to
    require both facts together. Also corrected two stale claims (a
    header comment describing round 1's own already-removed check; "zero
    mixer interaction" corrected to "zero mixer writes"). Round 2 alone:
    48/48. **ROUND 3 (same checkpoint, now closed): two further, narrowly
    scoped defects** in the round-2 handshake, both fixed -- (1) the
    initial `rm -f "$ack"` was unchecked and its absence never
    independently confirmed -- fixed, aborting (exit 97) BEFORE any child
    is launched if unconfirmed. (2) an empty/failed `own_pgid` lookup
    silently skipped the whole safety check instead of aborting -- fixed
    to REQUIRE a successful, positive-numeric lookup before
    `CHILD_PGID_CANDIDATE` is accepted or an ack published; failure aborts
    via a PID-scoped (never group-scoped) termination. Two new tests
    prove the fake probe never starts on either path. **FINAL RESULT
    (all three rounds): 50/50 wrapper tests pass, actual unittest exit
    status (0) captured directly** (not through a `tail` pipe);
    `ruff`/`bash -n` clean; `git diff --stat -- src/nexa` empty; no
    leaked owned process after any round's suite. No Gemini call. No
    hardware writes. Wrapper SHA-256 at close:
    `581f7dc04204a92d43a648cb9a334e0f96226cdd6d88959b9585cba57d0a8643`.
    **This checkpoint is CLOSED WITH A LOCAL COMMIT** (see commit hash
    below the report) -- not pushed.
    **`M2.6B` remains IN PROGRESS. The R0057 gain A/B experiment remains
    NOT EXECUTED** -- no approval has been given; that is the next task.

**R0071 (2026-09-16) — STRATEGIC PIVOT, M2.6B dual-pipeline PAUSED (ADR-0004
Amendment 2):** re-ran the original R0031/M2.6A probe unmodified on today's
hardware -- clean, 10/10, reconfirms golden. Fixed a real ownership gap in
M2.6B's dual-pipeline bridge (candidate/reject vs. provider, kept), but
real-hardware acceptance of the fixed M2.6B runtime still FAILED (sustained
self-echo survives the 300ms confirm-hold legitimately); a controlled
reference-gain A/B also failed both ways. Root cause remains unproven
(hardware AEC residual / uncorrected reSpeaker firmware `AEC_FAR_EXTGAIN`
/ dual-pipeline timing overhead, per R0053-R0056 + this checkpoint's own
audit) -- **investigation explicitly stopped by product decision, not
abandoned mid-idea.** Decision: stop building a second, NeXa-owned
realtime-interruption architecture to compete with Gemini Live's own.
Extracted golden M2.6A's *behavior* (one Pipecat pipeline, Gemini's native
VAD/turn/interruption handling) into a new simplified adapter
(`nexa.realtime.gemini.simple_conversation`, `apps/nexa_cloud_voice_simple.py`),
wired to the SAME `ConversationSession`/`ConversationRouter`/
`CloudContextSnapshot` boundary. NeXa Core (identity/memory/personality/
capabilities/permissions/device-state/canonical history, all local) vs.
cloud provider (ephemeral realtime session only, replaceable) ownership
now stated explicitly; new `CloudEligibility` (`LOCAL_ONLY`/`CLOUD_SAFE`/
`CLOUD_WITH_USER_APPROVAL`) formalizes the privacy boundary.
**M2.6B dual-pipeline code preserved, unchanged, paused -- not the
production default.** 25 new tests + 148 unaffected regression tests
green. **Real-hardware acceptance of the new simplified adapter PASSED,
same day (2026-09-16): normal conversation, PL/EN + language switching, no
self-conversation, natural interruption + correct recovery,
`CLOUD_SAFE` context-fact integration -- operator-confirmed comparable to
golden M2.6A.** `Cloud realtime conversation baseline = ACCEPTED / FROZEN`
(known non-blocking issue: occasional playback/stream continuity stutter
during longer assistant speech -- backlog, not investigated). See
`docs/reports/R0071_golden_voice_recovery_and_boundary_20260916.md`.

**M2 — REALTIME VOICE (LOCAL + CLOUD): COMPLETE.** Project priority moves
to **M3 -- NeXa Core**. Voice/AEC/VAD/confirm-hold/scheduled-reference/
PipeWire work stays paused unless a future product requirement reopens it.

**Then, after local + cloud voice are both complete, in order (M3.1-M3.6
below):** identity → memory → context → personality/relationship →
capabilities/permissions → learning → full graphical UI → typed chat in
that UI using the **same** `ConversationSession` / NeXa brain as voice.
There must never be separate voice-NeXa and chat-NeXa brains.

---

## M3 — NeXa Core

**The canonical active milestone (R0071 pivot, ADR-0004 Amendment 2,
ADR-0005).** One local, provider-independent Core owns identity, memory,
context, personality, relationship, capabilities, permissions, device
awareness, and learning — a cloud provider or local LLM never owns this
state; a model may *propose* a Core write, NeXa Core alone decides and
persists it. Each subsystem below is its own narrow module with its own
owner, never one aggregating `NeXaCore` god-object (ADR-0005 D5).

**User Model, Goals/Projects, and Device Awareness** remain canonical NeXa
Core concerns even though none of them gets its own numbered submilestone
yet — they are placed under the M3.x subsystem below that will own them
first (User Model/Goals under M3.4 Personality+Relationship's user-specific
state and M3.2 Memory's project/goal memory; Device Awareness under M3.5
Capabilities + Permissions), not forgotten.

### M3.1 — Identity Foundation

**STATUS: COMPLETE** (R0072, 2026-09-16). Small, stable, immutable identity
root (`NeXaIdentity`: `identity_id`/`name`/`product_name`/`purpose`/
`principles`/`identity_schema_version`, nothing else) — explicitly distinct
from Persona (conversation style layer, unchanged, `configs/personas/`) and
from every mutable Core subsystem below. See ADR-0005
(`docs/decisions/ADR-0005_nexa_core_identity_boundary.md`),
`docs/reports/R0072_m3_1_identity_foundation_20260916.md`.

### M3.2 — Memory Foundation

**STATUS: COMPLETE** (R0074, 2026-09-16). The NeXa Memory Platform —
supersedes the earlier "M5 — Long-Term Memory" framing below. One
canonical local `MemoryService` authority (`nexa.core.memory`), built for
every current/future NeXa subsystem (LiFeOS, NeXa Teacher, Projects,
Goals, Routines, Health, Finance, device/home/robot state), not just
assistant chat memory: extensible `namespace`/`record_type` (no central
enum edit per new domain), a small stable `category`
(FACT/STATE/PREFERENCE/EPISODE/EVENT, defined by information shape, never
by provenance), generic `scope_type`/`scope_id` anchor, structured
`payload`/`payload_version`, temporal validity (`valid_from`/`valid_until`)
kept separate from storage bookkeeping, a `memory_evidence` table so
multiple independent sources for the same fact are preserved (never
silently discarded on dedup), a closed-graph `memory_relations`, SQLite
persistence at an XDG application-data location (never the repo's `var/`),
component-aware schema versioning, and a WAL-safe backup path via the
stdlib `sqlite3.Connection.backup()`. Explicitly distinct from
`ConversationSession.history` (the chat transcript, unchanged — never
becomes the memory database) and from any cloud provider's own ephemeral
session state — `MemoryService` is provider-neutral (no
`CloudContextSnapshot`/Gemini knowledge; `cloud_eligibility` is a generic
query filter). Prerequisite correction: `CloudEligibility` relocated from
`nexa.realtime.privacy` to `nexa.core.privacy` — NeXa Core no longer
depends on the realtime/provider layer. Design:
`docs/reports/R0073_m3_2_memory_foundation_design_20260916.md` (3
revisions). Implementation: `docs/reports/R0074_m3_2_memory_foundation_implementation_20260916.md`.

### M3.3 — Context Engine + Knowledge Awareness

**STATUS: foundation COMPLETE (R0076); runtime integration LOCAL WIRED,
CLOUD AUDITED-NOT-WIRED (R0077, 2026-09-17).** Supersedes the earlier
"M3 — Robust Context" framing below. `nexa.core.context.ContextEngine`:
one canonical selection/composition/retrieval orchestrator over Identity
(M3.1), Memory (M3.2), and `ConversationSession` — never a second memory
store, never provider-specific. Knowledge Awareness (`KnowledgeDescriptor`,
live-queried, never cached into a duplicate store) distinguishes "loaded
now" / "known to exist elsewhere" / "don't know" (`UNKNOWN`) / "checked,
found nothing" (`NO_MATCH`) / "known but unreachable or permission-gated"
as type-enforced states. Teacher/LiFeOS/Projects domains work through one
generic `MemoryRetriever` with zero domain-specific engine code (verified
by test). `RETRACTED` memory is structurally unreachable through the
retrieval contract. `nexa.core -> never nexa.realtime` still holds.

**Local typed path (`apps/nexa_chat.py`) is wired and exercised**:
`nexa.core.context.derivation.derive_context_request()` turns a real user
turn into lexical hints (no hardcoded domain mapping — generic substring
matching against real namespace strings); natural NeXa-project and
Teacher-skill recall both proven to work with no explicit `domain_hint`;
identity/current-turn/history never duplicated; dynamic mid-session
knowledge usable next turn with no restart; Context Engine failures
degrade safely (logged, never crash). One real turn exercised against
live Ollama; isolated engine overhead measured at ~1.4ms (250 records/50
namespaces) — negligible next to any model call.

**Cloud realtime path: audited, adapter built and fully tested, but NOT
wired.** The accepted, frozen simplified cloud voice path (R0071) builds
its one `CloudContextSnapshot` before any conversation turn exists
(confirmed by source read) — `ContextEngine` structurally requires a
current turn, so wiring in there would be a proven no-op (verified
byte-identical). `apps/nexa_cloud_voice_simple.py` and
`nexa.realtime.gemini.simple_conversation` remain completely untouched.
No real-hardware voice acceptance was attempted (no hardware available in
this environment) — not claimed as performed.

See ADR-0006 (`docs/decisions/ADR-0006_context_engine_knowledge_awareness_boundary.md`),
design: `docs/reports/R0075_m3_3_context_engine_knowledge_awareness_design_20260916.md`
(3 revisions), foundation implementation:
`docs/reports/R0076_m3_3_context_engine_knowledge_awareness_implementation_20260917.md`,
runtime integration: `docs/reports/R0077_m3_3_runtime_integration_20260917.md`.

### M3.4 — Personality + Relationship

**BLOCKED as of R0081 (2026-09-17): do not start.** A cloud voice
false-barge-in/self-echo regression (`docs/reports/
R0081_cloud_voice_false_bargein_self_echo_regression_20260917.md`) is
the active task and must reach live acceptance PASS before M3.4 work
begins — see `docs/CURRENT_STATE.md`'s "Active checkpoint" section for
the current status.

Stable personality state, user-specific relationship state (User Model),
communication adaptation. Distinct from Persona (the M1.1 conversation
style prompt, which stays the default/fallback voice) and from Identity
(M3.1, invariant across users).

### M3.5 — Capabilities + Permissions

Capability registry, permission authority, and device-aware availability —
supersedes the earlier "M4 — Device Awareness + Capability Registry"
framing below. Device Awareness ("what hardware / body do I currently
have?") is owned here. Foundational for multi-device and the robot body.

### M3.6 — Learning + Adaptation

Explicit, inspectable learning of preferences, routines, useful facts, and
later skills — proposed by models, decided and persisted by NeXa Core, per
the ownership rule above.

---

### Historical framing, superseded by the M3.1-M3.6 sequence above

Kept for historical evidence only — do not treat as current planning; the
M3.1-M3.6 sequence above is the canonical, active structure.

**M3 — Robust Context (historical, pre-R0071):** Turn-to-turn and session
context that is coherent, bounded, and observable. Clear separation between
transient context, chat history, and long-term memory. → now **M3.3
Context Engine**.

**M4 — Device Awareness + Capability Registry (historical, pre-R0071):**
- **Device Awareness:** "What hardware / body do I currently have?"
- **Capability Registry:** "What can I actually do with this hardware, these
  permissions, and this runtime state?"

Foundational for multi-device and for the robot body. → now **M3.5
Capabilities + Permissions**.

**M5 — Long-Term Memory (historical, pre-R0071):** User-owned long-term
memory as a system distinct from chat history. Local-first storage.
Explicit write / retrieve paths. → now **M3.2 Memory Foundation**.

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
