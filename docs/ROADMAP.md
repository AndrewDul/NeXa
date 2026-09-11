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
