# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-09-12
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — see `git log -1` for the current hash (not pushed)
- **Latest decision:** `docs/decisions/ADR-0004_cloud_realtime_voice_provider_boundary.md`
  (**Cloud Realtime Voice provider boundary (M2.6) — Accepted 2026-09-10;
  Amendment 1 2026-09-10.**
  **Amendment 1** = four pre-M2.6B factual/API/terms corrections, no accepted
  decision reversed: **(1)** the operator is in the **United Kingdom** (not
  the EEA; Google groups EEA/CH/UK); split "data treatment" (Paid-Services
  data terms apply to a UK developer's unpaid quota too) from "making an API
  Client available to users" (only Paid Services for EEA/CH/UK *users*);
  the Gemini API **Free Tier is available in the UK**, so `DEVELOPMENT`-mode
  M2.6B work is not blocked by billing; a Gemini key is **not** a "paid
  key" — eligibility is a deployment policy
  `ProviderEligibilityPolicy(distribution_mode∈{DEVELOPMENT,DISTRIBUTED},
  billing_verified)`, nothing inferred from the key string; `DISTRIBUTED` +
  EEA/CH/UK users requires `billing_verified`. **(2)** `system_instruction`
  is **immutable on an open Live connection** (VERIFIED) — a sticky language
  command updates NeXa canonical state immediately and reaches the provider
  setup only on the **next** new/resumed session; the running connection
  relies on Gemini's own live context; no forced per-command reconnect in
  M2.6B. **(3)** Gemini-3.1 initial history is seeded **once** at session
  start via the initial-history mechanism
  (`history_config.initial_history_in_client_content=true` →
  `clientContent` until `turnComplete`, no model call), never turn-by-turn.
  **(4)** session-resumption reconnect safety — keep only the latest
  `resumable=true` handle, never use a non-resumable/empty handle, prefer a
  safe turn boundary, use `GoAway.timeLeft`, buffer inbound audio while
  reconnecting, fall back to a fresh seeded session if safe resumption is
  impossible; **make-before-break connection overlap is no longer assumed**
  (socket sequencing → M2.6B). Preserved: one `ConversationSession`,
  `RealtimeVoiceProvider` peer boundary, `GeminiLiveProvider` wrapper, the
  policy/provider split, `CloudContextSnapshot`, Option A + Option B,
  `Sulafat`, NeXa-owned #5465 protection, XVF3800 AEC, local Silero + local
  barge-in authority, optional `cloud-gemini` extra, Pipecat
  infrastructure-only. M2.6A remains PASS/OPERATOR-CONFIRMED; M2.6B was
  NEXT / NOT STARTED as of this Amendment-1 commit **[since progressed —
  see "Latest report" above: M2.6B is now IN PROGRESS, M2.6B.1/M2.6B.2/
  M2.6B.2A/M2.6B.3/M2.6B.3A/M2.6B.3B/M2.6B.4 IMPLEMENTED (first real
  hardware/Gemini operator run: ATTEMPT #1 FAILED, three regressions
  found; two fixed and retest-ready, the language-mirroring one only
  diagnosed — M2.6B.4A research found the obvious fix too slow and paused
  pending a lighter LID; M2.6B.4B benchmarked three real multilingual
  whisper.cpp tiny variants and identified tiny-q5_1 as the best
  researched candidate; **M2.6B.4C (below) is an explicit PRODUCT
  DECISION reversing the LID trajectory: no local LID gate in the normal
  cloud critical path unless a CLEAN Attempt #2 (R0038's fixes in place)
  produces repeated evidence native mirroring is unreliable — tiny-q5_1
  downgraded to FALLBACK RESEARCH CANDIDATE ONLY, not adopted. A
  source-level differential audit against the OPERATOR-CONFIRMED M2.6A
  spike found no concrete architectural regression, restored one
  wording difference in the cloud role card, and the correct next
  evidence is a clean Attempt #2, not a LID gate; M2.6B.4D tightened
  this further — the fire-and-forget LID construction/invocation itself
  was REMOVED from the production cloud runtime, so Attempt #2 ran with
  zero local LID CPU cost of any kind. Attempt #2 then actually ran
  (M2.6B.4E): native PL/EN mirroring worked live, but a real
  throat-clear mid-reply exposed a genuine bug — assistant audio was
  permanently lost for the rest of the session once a non-lexical
  interrupting sound produced no transcript at all; fixed with a local-
  turn-closure fallback re-arm, but that fix's own residual risk (one
  stray old-generation chunk possibly misclassified at an interruption
  boundary) was correctly flagged as not yet closed. M2.6B.4F
  quantified that risk precisely — found and fixed a CONCRETE,
  deterministically-reproducible regression in R0043's fallback
  (satisfied by the interrupting sound's OWN closure alone), narrowed it
  to require a genuinely separate subsequent turn, and proved by source
  audit that ONE remaining gap (CASE 2) could not be closed without
  provider/session replacement — reported, not implemented then.
  **M2.6B.4G implements that replacement**: every confirmed
  local barge-in now atomically swaps the Gemini provider/session
  (provider-instance isolation, never turn-closure counting) — CASE 2 is
  now provably closed; the R0043/R0044 fallback mechanism is REMOVED as
  superseded, not merely narrowed further. **M2.6B.4H (below) closes a
  SEPARATE, pre-existing production gap R0045 discovered while auditing
  that replacement: `router.begin_cloud_turn()` had NO production caller
  at all, so no cloud conversation turn was ever written to canonical
  history in any hardware run to date — fixed with a minimal
  `has_turn_awaiting_assistant()` gate + a `CloudTurnAccumulator` guard,
  proven via the REAL bridge/`_on_confirmed` production code, not a test
  mirror)]**.
  Original Accepted content:
  **Cloud Realtime Voice provider boundary (M2.6) — Accepted 2026-09-10.**
  Encodes the canonical rule: NeXa is the persistent system; Gemini Live is
  a replaceable realtime voice provider; identity / canonical conversation /
  memory / preferences / permissions / routing authority belong to NeXa; a
  cloud provider must never become a second NeXa brain. Decides A–P:
  **A** one `ConversationSession` authority for local + cloud; **B** new
  NeXa-owned `RealtimeVoiceProvider` boundary — a *peer of* `ModelProvider`,
  not a subtype (speech-to-speech session ≠ text stream) — in a new
  provider-agnostic `src/nexa/realtime/` package; **C** `GeminiLiveProvider`
  wraps Pipecat 1.8.1 `GeminiLiveLLMService` (Option C), v1 baseline
  `gemini-3.1-flash-live-preview` / `Sulafat` / 16k-in-24k-out / server VAD
  OFF / local Silero + XVF3800 AEC + local barge-in authority unchanged;
  **D** persisted NeXa-owned `ConversationPolicy` (`LOCAL_ONLY` **default**,
  `CLOUD_PREFERRED`, `AUTO` provisional — no classifier in M2.6B) vs runtime
  `active_provider` (`LOCAL`/`CLOUD`); NeXa's `ConversationRouter` executes
  every switch; **E** `CloudContextSnapshot` = minimal role card + language
  preference + last ~12 turns + policy state, fresh per non-resumed session,
  **never** full history / memory / persona verbatim / creds / raw audio;
  **F** language routing = **Option A** (Gemini native same-turn mirroring;
  NeXa owns the *preference* permanently via `ResponseLanguageResolver` +
  per-turn metadata; timing headroom ≠ steerability), Option B
  (delayed `activity_end` + local language-ID, ≈ +1.1 s) is the measured
  fallback, Option C (parallel whisper.cpp) rejected unless A & B fail;
  **G** voice is a provider-agnostic NeXa user preference mapped to
  `Sulafat`, not `GeminiIdentity.voice`; **H** Pipecat #5465 handled by a
  NeXa-owned bounded inbound audio buffer at the boundary (drop-oldest,
  metric, no dup after reconnect), **not** a patched upstream fork;
  **I** `ReconnectController` — proactive age-timer (~8 min) + `GoAway`
  handling + latest resumption handle + fresh snapshot on resumption
  failure + backoff → Decision J; **J** explicit failure→fallback table, no
  silent policy-violating fallback, `LOCAL_ONLY` stays local, others fall
  back to `LOCAL` with the user informed, continuity survives; **K** XDG
  secret file `~/.config/nexa/secrets/gemini.env` (700/600),
  `NEXA_GEMINI_API_KEY`, env-var-first loader + `CredentialSource` seam,
  redacted always; **[Amendment 1]** no paid/unpaid flag on the key —
  eligibility is a deployment policy
  `ProviderEligibilityPolicy(distribution_mode, billing_verified)`;
  `DEVELOPMENT` mode not blocked by billing (UK Free Tier available),
  `DISTRIBUTED` + EEA/CH/UK users requires `billing_verified`;
  **L** `google-genai>=2.22,<3` + `websockets>=15,<17` as an **optional
  `cloud-gemini` extra** — `pip install .` stays Google-free, `LOCAL_ONLY`
  needs no cloud code, `src/nexa/realtime/gemini/` imports `google.genai`
  lazily, `pipecat-ai[local]==1.8.1` unchanged; **M** `ProviderUsageEvent`
  from authoritative `usageMetadata`, no raw audio retained for billing;
  **N** Gemini function calling never the capability authority — request →
  NeXa ActionRouter validates + executes → result → provider continues
  (OFF in v1, event types defined); **O** Gemini session ≠ NeXa memory,
  future memory stays local/canonical, boundary compatible with
  memory/identity/GUI-typed-chat/multi-device; **P** Pipecat owns media/WS
  mechanics only, NeXa owns canonical conversation / routing / policy /
  context selection / preferences / memory / capability authority /
  fallback / reconnect semantics; no second orchestration framework. The
  ADR carries the ordered **M2.6B implementation plan** (15 components with
  owning layer / deps / tests / failure cases / frozen-path impact) and 19
  measurable **M2.6B acceptance gates**. No `src/nexa/**` / `tests/**` /
  `pyproject.toml` change in the ADR task.)
- **Latest report:** `docs/reports/R0050_m2_6b_4k_residual_onset_clipping_after_r0049_20260912.md`
  (**M2.6B.4K — residual onset clipping after R0049, forensic audit,
  2026-09-12.** The operator re-ran the R0048/R0049 real-hardware
  capture after the 300ms preroll fix: RAW remains complete, but
  PRODUCTION_FORWARDED — improved vs pre-fix — is STILL audibly clipped
  at onset ("Czarna dziura" → "arna dziura"/"carna dziura"; English
  phrases too, systemic not Polish-specific). **R0049 deterministic
  architecture proof stands (PASS, not retracted); R0049 REAL HARDWARE
  POST-FIX ACCEPTANCE is corrected to FAIL.** New diagnostic tool
  (`wav_alignment.py`, 10 tests, pure Python, no Gemini/hardware) proved,
  byte-exact, that `production_forwarded.wav` is an exact contiguous
  subsequence of `raw_with_context.wav` starting precisely 200.0ms in
  (500ms diagnostic pre-context − 300ms retained preroll = 200ms
  omitted, byte-identical across all 6 takes — the fix mechanism works
  exactly as designed). A plain PCM RMS energy analysis (20ms windows,
  no invented speech-onset detector) of that omitted 200ms shows real,
  substantial, RISING acoustic energy in 8 of 10 real captured takes
  (both post-fix 1–6 and the still-present pre-fix 7–10), beginning
  roughly 300–460ms before VAD confirms speech start and continuing to
  rise past the 300ms boundary — real speech energy is being cut, not
  silence. Source re-audit of installed Pipecat confirms M2.6A's own
  effective preroll was ALSO ~300ms in practice (not 500ms — the
  `SpeechControlParamsFrame` auto-sizing mechanism applies identically
  in M2.6A's co-located-VAD topology, confirmed from
  `VADController.start()`/`_handle_speech_control_params` source) — so
  M2.6A likely had the SAME underlying onset-timing risk; its
  "accepted" status reflected overall conversational quality/Gemini's
  own recovery behavior, not verified zero onset loss. Deeper VAD
  latency audit: the nominal `start_secs=0.2` corresponds to an EXACT
  192ms (6×32ms Silero analysis chunks) confirmation window ONLY — it
  does NOT include the separate, quantifiable delay from Pipecat's own
  exponential volume smoothing (`factor=0.2`, ~100–330ms to cross a
  gate depending on onset sharpness) or Silero's own model-internal
  confidence timing, both of which are additive and data-dependent.
  **This exact investigation was already performed once before, for
  LOCAL voice** — `nexa/stt/utterance_buffer.py`'s own docstring
  (unchanged, untouched, re-read this checkpoint) documents an
  independent R0006-era empirical measurement of REAL confirmation
  delay at 288–352ms for the identical VAD mechanism, which is why
  local voice's own `PRE_ROLL_MS` default is 500, not 300 — a
  pre-existing, already-validated answer R0049 did not reuse. **Root
  cause: R0049's 300ms capacity, derived from an unvalidated generic
  Pipecat auto-sizing formula, understates real onset latency on this
  hardware (288–460ms range, confirmed by two independent lines of
  evidence).** Minimum next fix identified but NOT implemented this
  checkpoint (R0050 = evidence, R0051 = fix): reuse
  `UtteranceBuffer`'s own already-validated `PRE_ROLL_MS=500` default
  directly instead of the derived 300ms figure. Zero `src/nexa/**`
  changes this checkpoint. **Hardware acceptance remains FAIL; `M2.6B`
  remains IN PROGRESS.** No Gemini call. Not pushed.)
- **Prior report:** `docs/reports/R0049_m2_6b_4j_restore_m2_6a_preroll_parity_20260912.md`
  (**M2.6B.4J — restore accepted M2.6A user-audio preroll parity,
  2026-09-12.** After R0048's diagnostic tool, the operator ran the REAL
  reSpeaker capture: 10 utterances, every RAW WAV contained the complete
  spoken phrase, every PRODUCTION_FORWARDED WAV was audibly clipped at
  the start — "Czarna dziura" forwarded as "dziura"/"arna dziura,"
  "Czarna dziura powstaje" forwarded as "dziura powstaje" — a fourth,
  independent, real-hardware line of evidence on top of R0048's
  source-read + synthetic-bridge-test + mechanical-derivation trio (R0048
  amended with this follow-up). **Fix: Option A** — a bounded rolling
  PCM pre-buffer added to `_VadToProviderBridge`, reusing
  `nexa.stt.utterance_buffer.UtteranceBuffer` VERBATIM (unmodified — the
  existing, already-tested, LOCAL-VOICE-proven mechanism `nexa.voice.
  runtime` already depends on for the identical M2.2 pre-roll
  requirement) rather than reimplementing an equivalent buffer; `git diff
  --stat -- src/nexa/stt` is empty. Capacity derived, never hardcoded:
  `(vad_analyzer.params.start_secs + AUTOSIZED_PREROLL_MARGIN_SECS) *
  1000` — the SAME `0.1s` margin Pipecat's own `GeminiLiveLLMService`
  uses, evaluating to 300ms with production's unmodified `start_secs=0.2`
  — the SAME effective preroll the accepted M2.6A spike had. One
  coherent buffer (`self._pcm`) now serves BOTH the pre-roll-while-idle
  role and R0045's own active-utterance-accumulation role via
  `UtteranceBuffer`'s existing ring→linear/idempotent-start-stop state
  machine — no more separate, overlapping-ownership fields. R0045's
  `_replace_provider_after_bargein`/quarantine/`sealed_utterances` logic
  needed ZERO changes (it already receives the correct, complete PCM
  once the bridge seeds it correctly) — the charter's own worked example
  (`[PREE][POST]` → fresh provider receives `[PREEPOST]` exactly once)
  is now a literal passing test. R0046 canonical-turn-ownership logic
  also needed ZERO changes (`git diff` confirms zero lines touched in
  `router.py`/`turn.py`). All 18 charter test requirements proven,
  including a NEW test confirming this checkpoint's new
  `nexa.stt.utterance_buffer` import still constructs zero
  `WhisperCppLanguageDetector`/`WhisperCppTranscriber`/
  `BilingualSpeechTranscriber` instances (R0042's zero-LID guarantee
  re-verified, not merely assumed, after adding a cross-package import).
  A deterministic, no-Gemini, synthetic-PCM A/B proof (the exact R0048
  test that once proved clipping, now proving its absence) — PASS.
  **Real-hardware A/B post-fix capture: performed by the operator —
  RESULT: FAIL, corrected by R0050 (below), same day** — 300ms
  materially improved onset retention but did NOT fully restore it; the
  operator still audibly hears the first phoneme/syllable clipped. +9
  new tests (`TestVadBridgePrerollParity`) + 1 new zero-LID
  test + 1 rewritten post-fix parity test — **981 tests total, OK
  (skipped=7)**, 0 regressions (all R0044/R0045/R0046 tests re-verified
  green, unmodified); `ruff`/`pip check`/`git diff --check` all clean;
  local voice AND `src/nexa/stt` both completely untouched (empty diff).
  **Hardware acceptance remains FAIL; `M2.6B` remains IN PROGRESS.** Not
  pushed.)
- **Prior report:** `docs/reports/R0048_m2_6b_4i_real_audio_ingress_parity_audit_20260912.md`
  (**M2.6B.4I — accepted M2.6A vs production real audio ingress parity
  audit, 2026-09-12, DIAGNOSTIC ONLY.** Real Attempt #3 hardware run: the
  operator's first utterance, "czarna dziura", was misheard as "Czorna
  Jura" (then Gemini stayed confused for a follow-up), but later in the
  SAME session correctly understood "Ah, a black hole!" and answered
  normal PL/EN questions sensibly — the R0043/R0045 permanent
  post-interruption audio-loss failure did NOT recur. Investigated
  whether the FIRST utterance's onset was clipped before reaching
  Gemini. **Hypothesis CONFIRMED**, on two independent lines of evidence.
  (1) Exhaustive read of the installed `pipecat==1.8.1` source both the
  accepted M2.6A spike and current production depend on:
  `VADProcessor.process_frame` forwards every frame downstream
  UNCONDITIONALLY before running VAD detection (own comment: "Audio
  flows through immediately while VAD detection happens after") — so
  `VADProcessor` itself drops nothing; `VAD_START_SECS = 0.2` (Pipecat's
  own default, unmodified by either architecture) means
  `VADUserStartedSpeakingFrame` fires only after 0.2s of ALREADY-ELAPSED
  confirmed voice activity; `GeminiLiveLLMService` has a REAL, built-in
  pre-roll buffer (`_user_audio_preroll_buffer`, auto-sized to
  `start_secs + 0.1s` via a `SpeechControlParamsFrame`, falling back to
  a 0.5s default "when no VAD is present") that M2.6A's topology (the
  SAME Silero VAD instance lives in the SAME pipeline, directly upstream
  of the LLM service) keeps continuously fed and therefore functional —
  but current production's `_VadToProviderBridge` (a SEPARATE processor
  in a SEPARATE hardware pipeline) never calls `send_user_audio()` for
  ANY audio before its own `_turn_open` flag flips true, so the
  provider's own preroll buffer is present in the running code but
  PERMANENTLY STARVED of anything to buffer. (2) A new diagnostic tool
  (`docs/research/m2_6_cloud_realtime_voice/
  m2_6b4i_audio_ingress_parity_probe.py` — no Gemini, no credential, no
  assistant playback, reuses the REAL `_VadToProviderBridge`/
  `BargeInController`/`SileroVADAnalyzer` construction verbatim, never a
  reimplementation) proves this EMPIRICALLY: driving synthetic pre-onset
  + spoken PCM through the REAL, unmirrored bridge in a real Pipecat
  pipeline shows the production-forwarded capture is missing EXACTLY the
  pre-VAD-start window the raw capture retains, byte for byte. Beginning
  of utterance: CLIPPED (by construction). End of utterance: NOT
  clipped (the mechanism is asymmetric — stop-detection delay keeps
  forwarding through the trailing window; start-detection delay forwards
  nothing during the leading one). Root cause confidence: HIGH for the
  clipping mechanism itself; MEDIUM for it being the full explanation of
  the one observed mishearing (a single real-world anecdote; the
  operator's own real hardware run with this tool, listening to the
  resulting WAV pairs, is the next evidence). Evaluated 3 minimum parity
  fix options (bounded rolling pre-buffer in the bridge; continuous
  ingress into the provider's own pipeline with VAD as semantic markers
  only; drive the provider's pipeline with a real VADController so
  `GeminiLiveLLMService`'s existing preroll self-flushes) — **none
  chosen, none implemented this checkpoint**, per the charter's own
  "R0048 = evidence, R0049 = fix" structure. +10 new deterministic
  tests (pure `IngressCapture` windowing/finalize/WAV-writing logic, the
  real-bridge clipping proof, structural no-Gemini-import checks) —
  **974 tests total, OK (skipped=7)**, 0 regressions; `ruff`/`pip check`/
  `git diff --check` all clean; **zero `src/nexa/**` changes** (diagnostic
  only); local voice completely untouched (empty diff). **Hardware
  acceptance remains FAIL; `M2.6B` remains IN PROGRESS** — the operator
  can now run the real 10-take capture command (no Gemini, no
  credential) and listen to the resulting WAV pairs before R0049 picks a
  fix. Not pushed.)
- **Prior report:** `docs/reports/R0047_pre_attempt3_launch_contract_audit_20260912.md`
  (**Pre-Attempt #3 launch contract audit, 2026-09-12.** The operator ran
  R0045/R0046's own documented "EXACT ... COMMAND"
  (`apps/nexa_cloud_voice_app.py --bargein`) twice; both times argparse
  rejected it (`unrecognized arguments: --bargein`) — Attempt #3 never
  started, no Gemini evidence produced. **Root cause: documentation
  drift, not a code defect.** `apps/nexa_cloud_voice_app.py` has never
  defined a `--bargein` flag (confirmed via direct source read and
  `git log --all -p` — the string never appears in this file's own
  history); every prior report R0035-R0044 correctly documented the bare
  command. `--bargein` belongs exclusively to the unrelated LOCAL voice
  probe (`apps/nexa_bilingual_voice_probe.py`) and its own opt-in
  `HalfDuplexGate`/`bargein_enabled` mechanism — R0045 (written in the
  same session) pattern-matched that convention onto the cloud app's
  launch command without checking its actual `argparse`, and R0046 copied
  the error forward. **Cloud barge-in
  (`BargeInController`/R0045's atomic provider replacement/R0046's
  canonical turn lifecycle) is constructed unconditionally by
  `build_gemini_voice_runtime` — no flag exists or is needed to gate it,
  so no runtime/architecture change was made.** Corrected both reports'
  launch commands (with an erratum explaining the mistake) to
  `.venv/bin/python apps/nexa_cloud_voice_app.py` (no flags). Added
  `tests/test_cloud_voice_app_entrypoint.py` (5 tests, new) — imports the
  app module directly and runs its REAL `parse_args()`/`main()`, proving
  (1) the bare and `--dry` commands parse, (2) `--bargein` is rejected
  (a canary against this exact drift recurring), (3) the REAL `--dry`
  entrypoint constructs `BargeInController` unconditionally (spying on
  `build_gemini_voice_runtime`'s return value, not a reimplementation of
  its wiring), (4) the bare live command parses and proceeds to the
  credential-load boundary before any Gemini/hardware call. **964 tests
  total, OK (skipped=7)**, 0 regressions; `ruff`/`pip check`/
  `git diff --check` all clean; local voice completely untouched (empty
  diff). **Hardware acceptance remains FAIL; `M2.6B` remains IN
  PROGRESS** — the operator can now safely re-attempt Attempt #3 with the
  corrected, test-verified command. Not pushed.)
- **Prior report:** `docs/reports/R0046_m2_6b_4h_production_canonical_cloud_turn_lifecycle_20260912.md`
  (**M2.6B.4H — production canonical cloud turn lifecycle, 2026-09-12.**
  R0045's own audit found `router.begin_cloud_turn()` had NO production
  caller at all — confirmed by exhaustive grep, only test files called it.
  Consequence: `CloudTurnAccumulator._current` stayed `None` for the life
  of every M2.6A/M2.6B hardware run; audio worked correctly, but
  **canonical `ConversationSession.history` never received a single cloud
  conversation turn.** Fixed with the smallest state model that avoids the
  overlap hazard R0045 identified (an interruption candidate's own VAD
  start, before confirmation, must never abandon or corrupt the still-open
  turn whose assistant reply it may be interrupting): a new, minimal
  `ConversationRouter.has_turn_awaiting_assistant()` method (True iff the
  current turn already has a final user transcript but is not yet
  committed) gates `_VadToProviderBridge`'s new
  `router.begin_cloud_turn()` call on every local VAD start; `_on_confirmed`
  calls it unconditionally right after committing the just-interrupted
  turn (a confirmed interruption's utterance is a continuation of an
  ALREADY-open local turn — no future VAD start ever arrives for it).
  `CloudTurnAccumulator.on_user_transcription` gained one companion guard
  (`cur.user_final`, alongside `cur.is_terminal`) closing the
  pre-confirmation window where a candidate's own transcript could
  otherwise reach the still-open, already-finalized turn N and overwrite
  it. Post-confirmation, R0045's existing provider-instance isolation
  (unmodified) is what makes the NEW provider the sole authority — no new
  mechanism was needed for that half. Proven via the REAL, unmirrored
  `_VadToProviderBridge` (a real `Pipeline`/`PipelineWorker`/`WorkerRunner`,
  not a hand-rolled simulation) for the normal-turn-opening half, and the
  REAL `build_gemini_voice_runtime` `_on_confirmed` closure (not a mirror)
  for the confirmed-interruption half — the exact "production wiring
  proof" the charter demanded, since R0045's own tests had only ever
  proven a test's own reproduction of the logic. A non-lexical
  interruption's own promoted turn (never transcribed) correctly never
  commits and never blocks the next real turn from recovering (its own
  `CloudTurnAccumulator.start_turn()` "abandon" logic supersedes it
  cleanly). A `CloudContextSnapshot` built right after a confirmed
  interruption (the exact primitive R0045's atomic replacement uses)
  contains every prior committed turn plus the just-interrupted one, and
  structurally can never contain the new not-yet-committed turn (the
  snapshot only ever reads `session.history`). +13 net new tests (5
  production-wiring-proof tests, 5 `has_turn_awaiting_assistant()` unit
  tests, 3 `on_user_transcription` guard unit tests) — **959 tests total,
  OK (skipped=7)**, 0 regressions (all R0045 atomic-replacement tests
  still pass, their hand-rolled `_on_confirmed` mirror updated to stay
  faithful); `ruff`/`pip check`/`git diff --check` all clean; local voice
  completely untouched (empty diff). **Hardware acceptance remains FAIL;
  `M2.6B` remains IN PROGRESS** — Attempt #3 (unchanged launch command)
  remains the next evidence, now able to directly confirm
  `ConversationSession.history` actually grows on real hardware for the
  first time. Not pushed.)
- **Prior report:** `docs/reports/R0045_m2_6b_4g_atomic_provider_replacement_20260912.md`
  (**M2.6B.4G — atomic provider replacement on confirmed barge-in,
  2026-09-12.** R0044 exhaustively proved no airtight same-session
  response-ownership boundary exists (Gemini's Live API exposes no
  response/turn/generation identifier on any server message) and left
  ONE documented open gap (CASE 2: old delayed audio arriving after a
  genuinely new turn's own closure). This checkpoint changes the
  architecture instead of refining the heuristic: **every confirmed
  local barge-in now atomically replaces the Gemini provider/session** —
  the OLD provider's `events()` queue is quarantined synchronously (in
  `_on_confirmed`, before any `await`, so local playback stop is never
  delayed) and never read again (Option A from the charter — the same
  "stop consuming the old provider entirely" isolation
  `recover_from_mid_turn_loss` already used for connection-loss
  recovery, now shared via a new factored-out primitive
  `ConversationRouter.start_fresh_cloud_provider`). A new NeXa-owned
  active-utterance buffer (`_VadToProviderBridge`'s `bytearray`, reset
  per VAD-START, always appended while a turn is open, sealed on
  VAD-END into a FIFO) guarantees the interrupting utterance — including
  audio already sent live to the OLD provider before confirmation — is
  replayed **exactly once** to the replacement provider, concurrently
  started via `asyncio.gather` so both provider-ready-first and
  seal-first timing orderings are handled identically; proven directly:
  a pre-confirm live-sent prefix + a post-confirm buffered remainder
  are replayed as one coherent utterance, never zero/twice/partial.
  R0044's CASE 2 was reproduced verbatim and is now **CLOSED**: the
  trailing old-generation audio is dropped because it originates from a
  provider instance whose queue is structurally never read again, not
  because of any turn-closure count — the critical proof R0044 could not
  provide. `_on_confirmed` now also commits the interrupted turn
  (`router.commit_cloud_turn()`) — a new, minimal, safe addition; a
  SEPARATE, pre-existing, deeper gap was discovered and explicitly
  deferred (not fixed this checkpoint): `router.begin_cloud_turn()` is
  never called anywhere in production code, so no cloud conversation
  turn has ever actually been written to canonical history in any
  M2.6A/M2.6B hardware run to date — fixing it safely requires resolving
  a separate risk (an interrupting candidate's interim transcript could
  overwrite a still-open turn's `user_text` before it commits), proposed
  as a future **R0046**, not rushed into this already-large checkpoint.
  R0044/R0043's `local_turn_closed_seq`-based fallback re-arm mechanism
  (`_ResponseGenerationGuard`'s `+2` threshold,
  `MIN_LOCAL_TURN_CLOSURES_BEFORE_FALLBACK_REARM`) is REMOVED as
  superseded (its only use case is now handled unconditionally by
  atomic replacement), reverting that class to its simple R0038 form;
  verified, by source reading, that connection-loss recovery never
  depended on it either. Added the charter's exact 8 performance
  instrumentation points (`BARGEIN_CONFIRMED_T` … `FIRST_NEW_ASSISTANT_AUDIO_T`)
  — no Gemini call made this checkpoint, no live numbers fabricated.
  R0044's own "WHY OLD AUDIO CAN NEVER RETURN" heading corrected with an
  erratum recording that R0044 proved no airtight boundary exists and
  R0045 supersedes the mechanism. Net **-3 tests** (removed R0044's 5
  adversarial CASE tests + 4 `_ResponseGenerationGuard` unit tests + 1
  superseded recovery test tied to the removed fallback; added 5
  `TestAtomicProviderReplacement` + 2 `TestVadBridgeQuarantine` tests, all
  using a real second `GeminiLiveProvider`/real Pipecat pipeline, not
  simulated) — **946 tests total, OK (skipped=7)**; `ruff`/`pip check`/
  `git diff --check` all clean; local voice completely untouched (empty
  diff). **Hardware acceptance remains FAIL; `M2.6B` remains IN
  PROGRESS** — Attempt #3 (unchanged launch command,
  `apps/nexa_cloud_voice_app.py` — corrected post-checkpoint; see the
  PRE-ATTEMPT #3 LAUNCH CONTRACT AUDIT entry above, this report never
  actually had a `--bargein` flag) remains the next evidence,
  now expected to also surface the first real measurement of atomic
  replacement's reconnect/fresh-context latency cost via the new
  instrumentation. Not pushed.)
- **Prior report:** `docs/reports/R0044_m2_6b_4f_strict_post_interruption_response_ownership_20260912.md`
  (**M2.6B.4F — strict post-interruption response ownership, 2026-09-12.**
  R0043's `local_turn_closed_seq` fallback fix was VALID (recovering
  audio after a non-transcribed interruption is real and necessary) but
  its own disclosed "one stray chunk" residual risk turned out to be a
  CONCRETE, 100%-reproducible defect, not a narrow edge case: the
  fallback only required the counter to advance by 1 past its
  DISPATCH-time value — satisfied merely by the INTERRUPTING sound's
  OWN local turn closing — so trailing OLD-generation audio arriving
  right after that single closure (but before any real new turn) was
  wrongly promoted into a fresh, valid generation and played audibly.
  **Verified empirically**: reverting the fix to "+1" reproduces the
  exact failure (`[gen-1-chunk, OLD-1, OLD-2]`); restoring "+2" corrects
  it. Exhaustively source-audited the installed Pipecat 1.8.1/
  `google-genai` stack (Live API message types, `serverContent.interrupted`,
  `generation_complete`, `turn_complete`, `LLMFullResponseStartFrame`,
  `BotStarted/StoppedSpeakingFrame`, WebSocket ordering,
  `CancellationCompleteEvent`, the SystemFrame-priority mechanics behind
  the four provider-interruption acks) and found **no airtight in-session
  boundary exists**: `LiveServerMessage`/`LiveServerContent` expose NO
  response/turn/generation identifier anywhere (confirmed by reading the
  full field list directly); `generation_complete`/`turn_complete` are
  explicitly suppressed for an interrupted generation;
  `LLMFullResponseStartFrame` is untagged local bookkeeping provably
  falsifiable by trailing old audio; none of the four interruption acks
  correlate with the actual downstream queue that would need to be
  proven empty. **Fix**: `_ResponseGenerationGuard.interrupt()` now
  records `provider.local_turn_closed_seq` AT INTERRUPT TIME (not
  dispatch time), and the fallback re-arm requires it to advance by 2 —
  one for the interrupting utterance's own closure, one for a genuinely
  SEPARATE subsequent turn — closing the concrete CASE-1 regression
  while HONESTLY leaving one gap open (CASE 2: old audio arriving after
  a genuinely new turn's own closure, indistinguishable from real new
  content by any local signal) — proven, not merely suspected, by a
  dedicated adversarial test documenting the actual (not idealized)
  outcome. Also added purely-diagnostic `ProviderInterruptionEvent.source`
  tagging (`local_cancel`/`remote_server_ack`) — confirmed neither origin
  can serve as a barrier either. Evaluated all 5 charter-listed
  alternatives; concluded provider/session replacement per confirmed
  interruption is the ONLY architecturally airtight option, with real,
  unsized costs (reconnect latency, cloud-context loss, applies to EVERY
  barge-in not just edge cases) — reported per the charter's own
  instruction, deliberately **not implemented** this checkpoint. **All 5
  adversarial cases run and reported honestly** (1/3/4/5 PASS, 2 is a
  documented open gap, not a false PASS). **+17 net new tests** (4
  `_ResponseGenerationGuard` unit tests, 5 adversarial CASE tests, 1
  service-level source-tagging test, plus 7 tests re-run via subclassing)
  — **956 tests total, OK (skipped=7)**, 0 regressions;
  `ruff`/`pip check`/`git diff --check` all clean; local voice completely
  untouched (empty diff). **Hardware acceptance remains FAIL; `M2.6B`
  remains IN PROGRESS** — an Attempt #3 that deliberately exercises a
  non-lexical interruption plus further real turns remains the next
  evidence; a brief stale-audio artifact at an interruption boundary, if
  ever observed live, is expected/documented residual behaviour (CASE 2),
  not a fix failure — and would be the concrete evidence needed to size
  Option E (provider/session replacement) as a future checkpoint. Not
  pushed.)
- **Prior report:** `docs/reports/R0043_m2_6b_4e_attempt2_post_interruption_audio_loss_20260911.md`
  (**M2.6B.4E — Attempt #2 post-interruption audio-loss failure — root
  cause CONFIRMED + fixed, 2026-09-11.** The REAL Attempt #2 hardware run
  happened: `CLOUD_PROVIDER_READY`/`AEC_REF_ACTIVE` reached, no VAD-
  bridge crash, first response audible, **native PL/EN mirroring
  confirmed working live** (PL->PL, EN->EN, PL->PL) — R0041's role-card
  restoration + R0042's zero-LID cleanup both hold; R0039/R0040 stay
  fallback research only, not reopened. **But**: mid-first-reply the
  operator cleared his throat (dry throat); this non-lexical sound
  confirmed a real local barge-in (VAD/barge-in correctly did its job —
  not itself a bug) and printed FOUR `✂ cloud interruption acknowledged`
  lines; after that point Gemini kept generating correct PL/EN text
  (visible in the terminal) but **no further assistant audio was ever
  audible again**. Source-audited (not guessed): installed Pipecat
  1.8.1's `FrameProcessor.broadcast_interruption()` fans out TWO
  `InterruptionFrame` instances per call, and BOTH our own
  `provider.cancel()` AND Gemini's OWN independent
  `serverContent.interrupted` server-side ack (installed
  `gemini_live/llm.py:1332-1333`) each trigger one such broadcast inside
  the provider's headless pipeline — 2+2=4 `ProviderInterruptionEvent`s
  from ONE local confirmation is confirmed NORMAL Pipecat/Gemini
  behaviour, not a NeXa bug (the local side was already idempotent,
  proven by a new test). **Confirmed root cause**:
  `GeminiVoiceRuntime._consume_provider_events`'s dispatch-rearm gate
  (`dispatched_for_turn`) depended solely on a fresh, final
  `UserTranscriptionEvent` — a non-lexical sound can confirm a real
  local barge-in and close a real local VAD turn while Gemini produces
  ZERO transcription events for it; with none ever arriving, the gate
  stayed shut, `start_new_generation()` was never called again, and
  every subsequent assistant-audio chunk (for as long as no turn
  produces a final transcript) was silently dropped as
  belonging-to-an-invalidated-generation, while text (which bypasses the
  generation guard entirely) kept flowing — exactly the live symptom.
  Investigated Pipecat's own output-transport interruption handling
  (`BaseOutputTransport.MediaSender.handle_interruptions`) and found it
  correctly cancels+recreates its audio task/queue unconditionally, and
  is only invoked once per confirmed interruption in this topology —
  ruled OUT as a contributing cause. **Fix**: `GeminiLiveProvider`
  gained `local_turn_closed_seq`, a NeXa/Gemini-independent counter
  incremented once per `user_turn_end()` call regardless of
  transcription outcome; the consumer loop now ALSO re-arms dispatch
  once this counter has advanced past the value recorded when the
  now-invalidated generation was dispatched — proof, from local VAD
  alone, that at least one more turn has genuinely closed, even if
  Gemini never transcribed it. The original final-transcription reset
  is kept unchanged as the (faster) primary path; this is a strict,
  backward-compatible addition (all 75 pre-existing tests in the module
  pass unchanged). Honestly disclosed a bounded residual risk (Gemini's
  own event ordering is not guaranteed, so a single stray trailing chunk
  could in principle be misclassified as a fresh generation at an
  interruption boundary — at most one brief artifact, never
  accumulating, vastly preferable to the confirmed alternative of
  permanent silence). Added lightweight, non-blocking diagnostics
  (`LOCAL_BARGEIN_CONFIRMED`/`PROVIDER_INTERRUPTION_ACK`/
  `GENERATION_INVALIDATED`/`ASSISTANT_RESPONSE_DISPATCH`/
  `ASSISTANT_AUDIO_RECEIVED`/`_DROPPED`/`_HW_QUEUED`/`BOT_STARTED`/
  `BOT_STOPPED`/`OUTPUT_INTERRUPTION_BROADCAST`) for a precise post-hoc
  read of any future retest. **+7 new deterministic tests**
  (`TestPostInterruptionAudioRecovery`: the core no-transcript
  reproduction+fix — verified to FAIL without the fix and PASS with it
  restored, both checked in this session; repeated-provider-ack
  idempotency; cancel/confirm counts exactly one per confirmation;
  old-generation trailing audio still never reaches hardware; three
  event-ordering variants) — **939 tests total, OK (skipped=7)**, 0
  regressions; `ruff`/`pip check`/`git diff --check` all clean; local
  voice completely untouched (empty diff). Recorded the throat-clear
  itself as a separate, lower-priority NON-LEXICAL FALSE BARGE-IN
  observation — not addressed by disabling barge-in or adding LID/STT
  to the interrupt-confirmation path in this checkpoint. **Hardware
  acceptance remains FAIL; `M2.6B` remains IN PROGRESS** — contingent on
  an Attempt #3 that deliberately includes a non-lexical interruption
  followed by further real turns. Not pushed.)
- **Prior report:** `docs/reports/R0042_m2_6b_4d_remove_cloud_lid_runtime_cost_20260911.md`
  (**M2.6B.4D — remove cloud LID runtime cost before Attempt #2 —
  narrow cleanup, 2026-09-11.** R0041 correctly decided NO local LID
  gate in the normal cloud critical path, but the runtime still
  CONSTRUCTED `WhisperCppLanguageDetector` and INVOKED it fire-and-
  forget once per closed utterance. R0041's own test proved only that
  the event loop does not await a slow fake coroutine inline — not that
  a REAL whisper.cpp CPU-bound inference call (~0.5-1.7s of native-code
  work per R0039/R0040's own measurements) has zero impact on Pipecat's
  scheduling/audio/AEC/VAD once actually invoked on a resource-
  constrained Pi. Per the operator's explicit instruction ("normal
  cloud voice must run with ZERO local LID inference"), removed rather
  than merely proved-non-blocking: `build_gemini_voice_runtime` no
  longer constructs `WhisperCppLanguageDetector`/
  `ResponseLanguageResolver` at all; `_analyze_turn_language` and
  `RuntimeMetrics.language_diagnostics` deleted; `_VadToProviderBridge`
  no longer accumulates a parallel per-utterance PCM buffer;
  `_PendingUtteranceAudio` deleted (confirmed, by grep across
  `src/nexa/realtime/`, to have had exactly one caller — the deleted
  diagnostic — never used by reconnect/mid-turn recovery, which is a
  structurally separate mechanism, `GeminiLiveProvider.take_pending_audio()`/
  `ConversationRouter.recover_from_mid_turn_loss`, confirmed by an empty
  `git diff` on `service.py`/`router.py`). Verified by test, not just
  inspection: a patched `WhisperCppLanguageDetector.__init__` proves
  zero construction calls; a patched `.detect()` proves a REAL
  Polish-content turn followed by a REAL English-content turn both
  dispatch through the identical native provider path with zero LID
  calls, no provider replacement, and `activityEnd`
  (`user_turn_end()`) returning in under 50ms every time. R0041's other
  changes (`CLOUD_ROLE_CARD` wording, per-turn diagnostics, R0038
  fixes) and R0039/R0040's fallback research are unchanged/preserved.
  **+2 net new tests (41 in this file; 932 total, OK, skipped=7)**, 0
  regressions; `ruff`/`pip check`/`git diff --check` all clean; local
  voice completely untouched (empty diff). **Hardware acceptance NOT
  marked PASS; `M2.6B` NOT marked COMPLETE** — both still contingent on
  a clean operator Attempt #2, which now runs with zero background
  whisper.cpp CPU load competing with Pipecat/audio/AEC/VAD. Not
  pushed.)
- **Prior report:** `docs/reports/R0041_m2_6b_4c_m2_6a_parity_audit_attempt2_prep_20260911.md`
  (**M2.6B.4C — M2.6A vs M2.6B language parity audit + Attempt #2
  preparation — PRODUCT DECISION, 2026-09-11.** Operator-directed reversal
  of the LID trajectory: M2.6A (`R0031`, OPERATOR-CONFIRMED) already
  proved native PL/EN mirroring/switching/barge-in worked well with no
  local LID; Attempt #1 was not a clean same-architecture experiment (two
  real integration bugs were entangled with the run, both already fixed
  in R0038). Did a source-level differential audit of the OPERATOR-
  CONFIRMED M2.6A spike (`docs/research/m2_6_cloud_realtime_voice/
  m2_6a_gemini_live_probe.py`) against current production
  (`service.py`/`runtime.py`/`snapshot.py`) across 27 dimensions (model,
  voice, system_instruction, initial history, VAD framing, sample rates,
  aggregator config, pipeline order, hidden language settings, etc.) —
  produced as an explicit table in R0041. **Found no concrete
  architectural regression capable of explaining EN->PL by itself.**
  Confirmed from R0038's own live log that the VAD-bridge setup/cleanup
  crash was confined to Pipecat's per-processor metrics lifecycle hooks
  and never touched the turn-forwarding logic (new turns DID reach
  Gemini throughout that failed run) — the crash is provably independent
  of the language failure. Found exactly ONE genuine, source-level
  wording difference: the cloud role card's language-mirroring sentence
  had drifted from the spike's own proven, explicit per-turn framing
  ("the language the user is currently speaking...if explicitly
  asked...follow that request") to a terser "Mirror the user's language"
  — restored (`CLOUD_ROLE_CARD` in `src/nexa/realtime/snapshot.py`) as a
  low-risk alignment with the OPERATOR-CONFIRMED wording, explicitly NOT
  claimed as a proven fix (unverifiable without a live call, not made
  this checkpoint). A second, real but NOT language-related difference
  was found and left open: production's `_VadToProviderBridge` only
  forwards audio to the provider while a locally-detected turn is open,
  so Gemini's own speech-onset pre-roll buffer (which the M2.6A spike's
  continuous-streaming design DID populate) is never populated in
  production — a latency/onset-clipping question for a future
  checkpoint, not this one. **R0040's `tiny-q5_1` finding is explicitly
  DOWNGRADED**: was "SELECTED", now **BEST RESEARCHED FALLBACK CANDIDATE
  ONLY** — not adopted, not wired into production, nothing deleted
  (models/benchmark/JSON results preserved for a future checkpoint if a
  clean retest ever proves native mirroring genuinely unreliable). Added
  lightweight, non-blocking per-turn retest diagnostics
  (`RuntimeMetrics.canonical_turn_committed` gained
  `user_transcript`/`assistant_transcript`/`provider_instance_id`
  keyword args, logged as `USER_TRANSCRIPT`/`ASSISTANT_TRANSCRIPT`/
  `PROVIDER_SESSION_ID`) built only from state already held in memory —
  no new I/O, no added latency, no raw audio. **+7 new deterministic
  tests** (a consecutive-normal-turns barge-in re-arm test; 3
  system-instruction/no-implicit-language-preference tests; 1 test
  proving a deliberately 5-second-sleeping fake LID detector never
  delays turn dispatch/commit — confirms LID stays fully off the normal
  cloud critical path; 2 diagnostics-logging tests) — **930 tests total,
  OK (skipped=7)**, 0 regressions. `ruff`/`pip check`/`git diff --check`
  all clean; local voice completely untouched (empty diff on
  `src/nexa/voice`/`src/nexa/voice_tts`). R0038's fixes (VAD-bridge
  lifecycle, response-generation guard) re-verified unchanged. **Hardware
  acceptance NOT marked PASS; `M2.6B` NOT marked COMPLETE** — both
  contingent on a clean operator Attempt #2 using the exact scripted
  PL/interrupt/EN/EN/PL coverage in the report. Not pushed.)
- **Prior report:** `docs/reports/R0040_m2_6b_4b_lightweight_lid_benchmark_20260911.md`
  (**M2.6B.4B — real lightweight PL/EN LID candidate benchmark — RESEARCH
  ONLY, 2026-09-11.** R0039 proved `base/q8_0` too slow for per-turn gating
  but only *surveyed* lighter candidates without benchmarking a real one.
  This checkpoint downloaded and benchmarked the three REAL multilingual
  whisper.cpp `tiny` variants (`ggml-tiny.bin`/`ggml-tiny-q8_0.bin`/
  `ggml-tiny-q5_1.bin`, MIT-licensed, from `huggingface.co/ggerganov/whisper.cpp`,
  SHA256-verified downloads) against the SAME whisper.cpp v1.9.3/ctypes
  binding NeXa already uses, into an isolated research cache
  (`~/.local/share/nexa/research/lid/`, production `base/q8_0` untouched),
  extending (not replacing) R0039's own benchmark script with
  `--model-path`/`--model-label`, the full 50-item M2.4B corpus accuracy
  sweep, a 2-pair 0.5/1.0/1.5/2.0s/full truncation sweep, and an
  EOT-visible-latency simulation (background LID launched on the first
  1.5s of buffered speech while the utterance continues) — the metric the
  charter called potentially more important than raw inference time. All
  four models tie at 100% (30/30) on the main corpus; the ONE discriminating
  real fixture (`pl_co_to_są_kolory.wav`, "Co to są kolory?") is
  misclassified by `tiny` (full, wrong even at the FULL utterance) and by
  `tiny-q8_0` (wrong at exactly the 1.5s decision point a background-LID
  design would use, despite being the fastest candidate at ~511ms warm) —
  only `tiny-q5_1` matches `base/q8_0`'s accuracy exactly (4/4 cross-check,
  correct at 1.5s). A real EOT-visible-latency measurement bug (captured
  the LID task's "done" timestamp only after the full simulated sleep,
  falsely inflating every model to ~2005ms) was found and fixed before any
  number was trusted; corrected result: **all four models show 0.0ms
  EOT-visible latency on every 3.5s cross-check case** — LID cost is fully
  hidden by background execution on utterances this long, making accuracy
  (not speed) the actual discriminator. **DECISION GATE: B — TINY-Q5_1
  ACCEPTED** — not for raw speed (only ~13-20% faster than the rejected
  `base/q8_0` baseline, still nominally "too slow" alone) but for a genuine
  footprint win at equal accuracy (30.7 MiB vs 78.0 MiB disk, -61%; 133.3
  MiB vs 203.6 MiB peak RSS, -35%) — directly validating the charter's own
  "do not assume smaller quantization is automatically better" caution,
  since `tiny-q5_1` (smaller, slower) beats `tiny-q8_0` (larger, faster) on
  accuracy. **Explicit unresolved caveat**: the repo's own 10 `short`
  corpus fixtures (real recordings, all 1.344-1.728s — "tak"/"nie"/"okay"
  etc.) end at or before a 1.5s-start background LID would even begin, so
  the 0ms-hidden-latency result does NOT extend to short utterances — left
  as an open design question for R0041, not resolved here. Recommended
  (NOT implemented) an R0041 design sketch reusing R0038's
  `_VadToProviderBridge`/`_PendingUtteranceAudio` buffering and
  `recover_from_mid_turn_loss` provider-replacement machinery verbatim.
  Zero `src/nexa/**` change this checkpoint; full suite unchanged at 923
  tests, OK (skipped=7). **`M2.6B` remains IN PROGRESS; the
  language-mirroring gap remains open, now with an accepted lighter LID
  model but no implementation yet.** Not pushed.)
- **Prior report:** `docs/reports/R0039_m2_6b_4a_strict_language_authority_research_20260911.md`
  (**M2.6B.4A — strict same-turn PL/EN language authority — RESEARCH
  ONLY, 2026-09-11.** R0038 diagnosed but did not fix the language-
  mirroring failure. This checkpoint benchmarked
  `WhisperCppLanguageDetector` (base/q8_0, the same model local voice
  uses) on the real PL/EN fixtures per the charter's own "measure LID
  first" instruction — result: **~1.15–1.7s per call, essentially
  CONSTANT regardless of input duration** (0.5s of speech costs about the
  same as the full utterance), with no reliable early-truncation point
  (PL misclassified as EN below ~1.5s). Read the installed `whisper.cpp`
  v1.9.3 source directly: `whisper_lang_auto_detect` runs a full encoder
  forward pass over a FIXED ~30-second-equivalent context
  (`WHISPER_CHUNK_SIZE`, an architectural Whisper constant) regardless of
  actual audio length — confirming, from source, that this cost is
  inherent and not fixable by truncation/config. Holding `activityEnd`
  for this detector on EVERY turn (as the charter's strict-mode design
  specifies) would cost every ordinary SAME-language turn ~1.1–1.7s more
  than the M2.6A baseline — directly contradicting the charter's own
  stated goal. **Put this to the user with the measured numbers before
  writing any code; the user chose: pause implementation, research a
  lighter LID model first.** Verified official Gemini Live API docs live
  (WebFetch, no Gemini call): confirmed "the different modalities... are
  handled as concurrent streams... ordering... is not guaranteed" (rules
  out a realtime text hint as steering — independently confirms the
  charter's own caution), confirmed "you cannot update the configuration
  while the connection is open" (re-confirms ADR-0004 Amendment 1), and
  confirmed native-audio models have no `language_code` parameter — "you
  can restrict the languages it speaks in by specifying it in the system
  instructions" is Google's own sanctioned mechanism, validating the
  charter's provider-replacement design as correct once a viable
  low-latency LID exists. Surveyed (not implemented) lighter
  alternatives: a smaller `ggml` Whisper model (same binding, not yet
  downloaded/verified in this environment — this sandbox's `tiny.bin`
  files are whisper.cpp's own tiny CI test stubs, not real weights) or a
  dedicated non-Whisper spoken-LID model (would be a genuinely new,
  heavier dependency, not adopted casually). Recorded the full STRICT
  MODE design (detected_turn_language/sticky_language_preference/
  active_session_response_language, reusing R0038's
  `recover_from_mid_turn_loss`/`_ProviderHandle` machinery verbatim, one
  system-instruction wording proposal) as a specification for a future
  checkpoint — **not implemented**. Zero `src/nexa/**` change this
  checkpoint; full suite unchanged at 923 tests, OK. **`M2.6B` remains
  IN PROGRESS; the language-mirroring gap remains open and undecided.**
  Not pushed.)
- **Prior report:** `docs/reports/R0038_m2_6b_4_hardware_acceptance_attempt1_fail_20260911.md`
  (**M2.6B.4 — production hardware acceptance ATTEMPT #1 — FAIL,
  2026-09-11.** The FIRST real operator hardware/Gemini run happened:
  reached `CLOUD_PROVIDER_READY`, `AEC_REF_ACTIVE`, audible Sulafat,
  normal conversation possible — but **three real regressions**, found
  and fixed (deterministically; no further Gemini call made).
  **(1)** `_VadToProviderBridge` crashed Pipecat's real setup/cleanup:
  confirmed from installed source that `FrameProcessor.__init__` already
  owns `self._metrics`/calls `.setup()`/`.cleanup()` on it — NeXa's own
  code stored `RuntimeMetrics` under that SAME name, clobbering Pipecat's
  real metrics object. Fixed: renamed to `self._nexa_metrics`.
  **(2)** local playback did NOT stop on barge-in — the interrupted reply
  played to completion while the NEW reply was already generating.
  Source-audited the exact chain: `broadcast_interruption()` (unchanged)
  only clears the output queue's contents AT THAT INSTANT; it does
  nothing about audio still in `provider.events()`'s own queue or
  produced by Gemini in the brief window before honouring the cancel
  signal — confirmed as the real cause, not the cloud model. Fixed with
  new `_ResponseGenerationGuard`: every assistant audio chunk is checked
  against the currently VALID response-generation id before ever reaching
  `hw_worker.queue_frames()`; a confirmed interruption invalidates the
  current generation synchronously (no race). A genuine design bug was
  found and fixed while building this (conflating "may this chunk play"
  with "has a new local turn started" let trailing old-generation audio
  masquerade as a fresh dispatch) — closed by keying dispatch timing to a
  fresh, final `UserTranscriptionEvent` instead (R0034's own proven
  message-ordering guarantee), which also fixed a second, previously-
  latent bug (dispatch tracking never reset on a normal
  `GenerationCompleteEvent`, only on a fatal error). **(3)** two English
  questions were both answered in Polish (ADR-0004 Option A). Reconstructed
  the exact production snapshot: `apps/nexa_cloud_voice_app.py` never
  passed `language_preference` (defaults to `None`, so NO "Current
  language preference" line was ever appended) and
  `build_default_session()` starts with empty history — **the snapshot
  was genuinely neutral, NeXa did not force Polish anywhere** — a real
  Gemini native-mirroring reliability gap, activating ADR-0004's own
  documented Option-B fallback. Built the offline detection half:
  `_VadToProviderBridge` now also buffers each utterance's PCM locally
  (parallel copy, never delays what streams to Gemini) and
  `_consume_provider_events` correlates it with that turn's final
  transcription, reusing verbatim the ALREADY-ACCEPTED
  `nexa.stt.WhisperCppLanguageDetector` + `nexa.conversation.ResponseLanguageResolver`
  (sticky preference only on an EXPLICIT directive, never implicit) —
  updates any FUTURE fresh-session snapshot, logs the charter's exact
  diagnostic keys. **Honestly did NOT build** same-turn steering of the
  response already in flight — no verified, low-risk mechanism was found
  reachable through the installed stack without a live call; native
  mirroring remains active, the gap is explicitly documented, not
  papered over. **+13 net new tests (923 total, 0 regressions)** —
  including a real Pipecat `Pipeline`/`PipelineWorker`/`WorkerRunner`
  setup/process/cleanup test (not construction-only `--dry`) and a
  language test using REAL recorded PL/EN audio fixtures.
  `ruff`/`pip check`/`git diff --check`/secret-scan/import-isolation all
  clean; local voice completely untouched (only
  `src/nexa/realtime/gemini/runtime.py` + its own test file changed).
  **Hardware acceptance NOT marked PASS; `M2.6B` NOT marked COMPLETE.**
  Not pushed.)
- **Prior report:** `docs/reports/R0037_m2_6b_3b_interrupted_cloud_history_safety_20260911.md`
  (**M2.6B.3B — interrupted cloud history safety only — PASS (narrow
  deterministic checkpoint), hardware/Gemini operator acceptance STILL
  NOT YET RUN, 2026-09-11.** ADR-0004 + Amendment 1 unchanged; no cloud
  call, no hardware test. **Found R0036's own `_SpokenPrefixHighWater`
  one-chunk-lag mechanism still overclaimed**: a later audio chunk's mere
  existence does not prove how much of an EARLIER text snapshot that
  chunk's own audio covers (example: snapshot `"abcdef ghijkl mnop..."`,
  chunk #1 covers only "abc", chunk #2 only "def" — chunk #2 arriving
  proves nothing about whether chunk #1 covered the WHOLE snapshot text
  that existed when it arrived). Re-searched installed source for a real
  alignment mechanism: `google.genai.types.Transcription.words` /
  `WordInfo.start_offset`/`end_offset` **do exist** in the underlying
  SDK's own type schema (real per-word timing data), but Pipecat's
  installed `_handle_msg_output_transcription` **never reads or forwards
  `words`** (grepped the entire file: zero occurrences) — only the
  concatenated `.text` reaches any frame NeXa's provider can see, so this
  data is not reachable without bypassing Pipecat's own service (out of
  scope; ADR-0004 already forbids a second raw Gemini client), and it's
  unverified whether the API even populates it in practice (would need a
  live call). **Conclusion: no deterministic alignment exists in the
  integrated stack.** Deleted `_SpokenPrefixHighWater`; new
  `CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""` constant is now passed
  unconditionally on every confirmed interruption, regardless of chunk
  count or accumulated text — an interrupted cloud turn always commits
  `COMMITTED_USER_ONLY` (user turn preserved, no assistant canonical text
  ever credited). **Cloud interrupted-prefix precision: CONSERVATIVE / NO
  FALSE FUTURE TEXT** — under-crediting is acceptable, crediting unspoken
  words is not. Normal, non-interrupted completions unaffected (still
  store the full final assistant text). Playback lifecycle, AEC,
  reconnect/mid-turn-recovery, router architecture, Gemini model/voice,
  VAD, and barge-in thresholds were **not** touched. 5 tests replace
  R0036's 6 (matching the charter's own numbered scenarios exactly); full
  suite **910 tests, OK (skipped=7)**, 0 regressions;
  `ruff`/`pip check`/`git diff --check`/secret-scan/import-isolation all
  clean; `--dry` app re-verified. **Explicit standing note: `M2.6B` must
  NOT be marked fully COMPLETE after the hardware run until the
  proactive-reconnect/age-trigger gate (`should_proactively_reconnect()`,
  still no production caller) is either implemented+validated or
  explicitly changed by an ADR amendment — this is unchanged from R0036,
  restated here as a standing condition.** Not pushed.)
- **Prior report:** `docs/reports/R0036_m2_6b_3a_pre_live_hardening_20260911.md`
  (**M2.6B.3A — final pre-live playback/barge-in/recovery hardening —
  PASS (deterministic checkpoint), hardware/Gemini operator acceptance
  STILL NOT YET RUN, 2026-09-11.** ADR-0004 + Amendment 1 unchanged; no
  cloud call, no hardware test, no reconnect-duration/quota test. Fixed
  three production-significant seams R0035 got wrong or left undriven,
  each confirmed against installed Pipecat 1.8.1 source (not assumed):
  **(1)** `GenerationCompleteEvent` alone was flipping `BargeInController`
  back to IDLE before any assistant audio necessarily reached the
  speaker — `BaseOutputTransport` proves `BotStoppedSpeakingFrame` (a real
  `TTSStoppedFrame`, or a 3s silence fallback) is the only true
  playback-drain signal, unrelated to generation-complete. Fixed with new
  `_ResponseLifecycle` (the cloud analogue of the already-accepted
  `nexa.voice.gate.HalfDuplexGate` combinator) plus a deterministic
  `TTSStoppedFrame` injected right after each generation's audio (FIFO
  after every chunk), so the real stop is never a multi-second guess.
  **(2)** R0035 used the raw, running `CloudTurnAccumulator.assistant_text`
  as an interrupted turn's spoken prefix — Pipecat's own installed source
  (`gemini_live/llm.py`) documents, verbatim, that output-transcription
  "arrive[s] *before* the model_turn messages with audio" and "contain[s]
  much *more* text" (look-ahead), so "on an interruption our recorded
  context will contain some text that was actually never spoken" — a
  source-proven failure mode, not theoretical. Fixed with new
  `_SpokenPrefixHighWater`: a one-chunk-lag combinator that promotes text
  to the high-water mark only once a LATER audio chunk confirms an
  EARLIER snapshot has crossed the playback-output boundary (a response
  interrupted after only one chunk ever played deliberately commits an
  empty prefix — the conservative, safe choice given the proven overshoot
  risk). **(3)** `ConversationRouter.recover_from_mid_turn_loss()` had NO
  CALLER anywhere in `GeminiVoiceRuntime` — confirmed by direct
  inspection; R0035's "wired" claim was true only of the router method's
  existence. Fixed: `_consume_provider_events` now polls
  `provider.needs_fresh_session` after every event and drives recovery the
  instant it's observed, atomically swapping both `runtime.provider` and a
  new `_ProviderHandle` box the VAD bridge reads through (so future local
  audio always reaches the current provider) — never a second concurrent
  `provider.events()` reader; the old, already-stopped provider's queue is
  never read again (proven with a fabricated event on the abandoned
  queue). A genuine test-design RACE was found and fixed while proving
  this (the test's own stranded-audio injection could lose a race against
  the runtime's own reaction to the same `ReconnectingEvent` — fixed by
  sequencing the injection before the runtime's consumer task even
  exists). **(4)** confirmed `should_proactively_reconnect()` has no
  caller either — explicitly documented as DEFERRED (not silently
  overclaimed); the mid-turn-unsafe path from (3) is the one actually
  wired and functional. **(5)** the operator app was silently replacing
  (not composing with) the metrics logger's AEC-status callback by
  reaching into `AecReferenceHealth`'s private attribute — fixed with a
  proper `on_aec_change` parameter on `build_gemini_voice_runtime`,
  composed internally with the metrics logger. **+15 net new tests (911
  total, 0 regressions)**; `ruff`/`pip check`/`git diff --check`/
  secret-scan/import-isolation all clean; `apps/nexa_cloud_voice_app.py
  --dry` re-verified live in-sandbox after every change. **Real
  Gemini/hardware operator acceptance remains the one step before M2.6B
  is COMPLETE — not run, no PASS claimed; see R0036 for the exact launch
  command and READY lines (unchanged from R0035).** Not pushed.)
- **Prior report:** `docs/reports/R0035_m2_6b_3_hybrid_cloud_audio_hardware_acceptance_20260911.md`
  (**M2.6B.3 — production HYBRID cloud audio + real-hardware operator
  acceptance — TEST-READY (deterministic checkpoint), hardware/Gemini
  operator acceptance NOT YET RUN, 2026-09-11.** ADR-0004 + Amendment 1
  unchanged; no cloud call, no hardware test, no reconnect-duration/quota
  test. Wired `ReconnectController` into the production `GeminiLiveProvider`
  via a new NeXa-owned `_readiness_monitor()` observation seam (Pipecat's
  `GeminiLiveLLMService` reconnects automatically/internally with no
  external hook — confirmed from the installed source — so NeXa can only
  observe the readiness transition and decide independently whether to
  trust it). Explicit policy answer for the charter's central question:
  a mid-user-turn connection loss is **not** assumed safely resumable (no
  source evidence found) — `ConversationRouter.recover_from_mid_turn_loss()`
  destroys the old provider and starts a fresh one from a freshly rebuilt
  canonical `CloudContextSnapshot`, replaying **only** not-yet-delivered
  PCM (`take_pending_audio()`, destructive) as one new framed utterance;
  a safe-boundary loss may resume the same provider-scoped context with no
  re-seed, no duplicate turns. New `src/nexa/realtime/gemini/runtime.py`
  (`GeminiVoiceRuntime`/`build_gemini_voice_runtime`) wires the REAL
  hardware path: reSpeaker mic → the existing `LocalAudioTransport`/
  `SileroVADAnalyzer`/`VADProcessor` construction pattern (mirrored from
  `voice/runtime.py`) → a new, minimal `_VadToProviderBridge` (forwards
  VAD-bracketed audio into `provider.user_turn_start`/`send_user_audio`/
  `user_turn_end` — Gemini's own server VAD stays OFF, local Silero is the
  sole turn authority) → `GeminiLiveProvider` → Gemini → assistant PCM
  injected back into the SAME hardware pipeline via
  `PipelineWorker.queue_frames` (the same mechanism already used for the
  one-time `LLMRunFrame` kickoff) → USB speaker, teed to the XVF3800 AEC
  far-end reference by the existing, **unmodified** `AecReferenceFeeder`.
  Cloud barge-in reuses the existing, **unmodified** `BargeInController`:
  `notify_response_dispatched`/`notify_response_finished` kept in sync
  from the same single `provider.events()` consumption loop that drives
  the canonical write path (a second independent event-stream reader was
  drafted for operator printing, then found via re-reading `service.py`
  to be unsafe — `events()` is backed by ONE `asyncio.Queue` — and fixed
  with a single in-loop `on_event` hook instead); `_on_confirmed` calls
  `router.on_interruption()` (freezes `CloudTurnAccumulator`'s
  already-accumulated `assistant_text` as the spoken prefix — the same
  precision as the local `SpokenTextTracker`, no new tracker class built),
  fire-and-forgets `provider.cancel()`, and closes the capture phase
  immediately (cloud turns need no segment-coalescing capture, unlike
  local). New operator app `apps/nexa_cloud_voice_app.py`
  (`--dry`/live modes) — `--dry` run live in this sandbox: full object
  graph constructs, no audio device or network touched. **+10 new tests
  (896 total, 0 regressions)**; `ruff`/`pip check`/`git diff --check`/
  secret-scan/import-isolation all clean. Corrected a stale test-count in
  `R0034`'s own FILES CHANGED (said the service.py test file "+16", the
  actual split verified via `git show` is service.py +15 / turn.py +1 =
  16 total, matching R0034's own TEST RESULTS). **Real Gemini/hardware
  operator acceptance is the explicit next step — not run yet, no PASS
  claimed for it; see R0035 for the exact launch command and READY
  lines.** Not pushed.)
- **Prior report:** `docs/reports/R0034_m2_6b_2a_cloud_turn_reconnect_hardening_20260911.md`
  (**M2.6B.2A — pre-hardware cloud-turn / reconnect hardening —
  IMPLEMENTED, PASS (checkpoint), 2026-09-11.** ADR-0004 + Amendment 1
  unchanged; no cloud call, no hardware test. Found and fixed a **real
  silent-user-speech-loss bug**: `GeminiLiveProvider.user_turn_start()`
  decided "send live vs. buffer" once, at call time, and never recorded
  that decision — a turn that started live and then lost readiness
  mid-utterance had every further `send_user_audio` rejected by
  `UtteranceFramer` as an orphan (no `activity_start` recorded there), and
  the eventual `user_turn_end()` silently ignored too (no live
  `activity_end` ever reached Gemini either). **Fixed** with a
  `_live_turn_open` flag + deterministic abort-and-restart: the stranded
  live segment is marked aborted (logged, never silent — it never gets a
  live `activity_end`) and every subsequent frame becomes a NEW,
  self-contained buffered utterance, flushed once `READY` returns.
  **Production policy, stated explicitly: a connectivity loss exactly
  mid-utterance may split one utterance into two from Gemini's
  perspective, but never silently drops user audio and never delivers the
  same audio twice.** New `take_pending_audio()` lets a fresh-session
  hand-off retrieve (destructively — never redelivered) whatever was
  buffered but undelivered on a discarded provider instance. **Completed
  the provider->NeXa event map**: `InterimTranscriptionFrame` (partial
  user transcription, previously unhandled) + `TranscriptionFrame.
  finalized` (previously hard-coded `True`); a new, unambiguous
  `GenerationCompleteEvent` (replaces an earlier ambiguous empty-text
  `AssistantTranscriptionEvent` for turn-complete); `ErrorFrame`/
  `FatalErrorFrame` -> `RealtimeProviderError`/`RealtimeProviderFailedError`.
  **Found and fixed a second real gap**: Pipecat's own `push_error()`
  pushes error frames **upstream**, but the only event tap sat downstream
  of the Gemini service — it could never have seen an error. Fixed with a
  second `up_tap` before the user aggregator (mirrors the M2.6A probe's
  own upstream/downstream dual-tap pattern), proven with a test that
  pushes a `FatalErrorFrame` upstream and confirms it reaches
  `provider.events()`. **Closed the "manual injection" test gap**: new
  `ConversationRouter.handle_provider_event()` is now the *only* path by
  which a provider's event stream reaches the router/session; a new
  integration test drives a full cloud turn exclusively through real
  provider events (never by calling `on_user_transcription`/
  `commit_cloud_turn` with hand-picked text) and proves exactly one
  canonical exchange results. **5 turn-ordering/interruption permutations
  tested** (user-then-assistant-then-complete; local interruption beating
  a late server ACK — which also exposed and fixed a THIRD gap,
  `CloudTurnAccumulator.on_assistant_transcription` appending a late delta
  even after `on_interruption()`, now refused; provider death before vs.
  after a spoken prefix). **Turn-complete-before-delayed-transcription
  proven IMPOSSIBLE from the installed Pipecat source** (not assumed):
  `GeminiLiveLLMService._connection_task_handler` processes messages
  strictly in arrival order from one loop, and its own code comment
  confirms input_transcription is handled before turn_complete even
  within one bundled message. **Fresh-snapshot-on-resumption-failure
  mechanism made precise**: destroy-and-recreate — a brand-new
  `GeminiLiveProvider` builds its own `LLMContext` from a freshly built
  `CloudContextSnapshot` and holds no reference to any previous instance,
  so stale Pipecat-owned context cannot structurally leak into it; proven
  with a test using a deliberately stale old provider context vs. a fresh
  canonical snapshot. **+16 tests (886 total, 0 regressions).** Also
  corrected a stale test-count sentence in `R0033`'s own "WHAT I DID"
  section (said 88, was always 57+1=58, matching R0033's own TEST
  RESULTS). **Still explicitly deferred to M2.6B.3**: `ReconnectController`
  is not yet driven by `GeminiLiveProvider` on a real connection error —
  no live GoAway/age-timer reconnect wiring exists yet; this checkpoint
  hardens what happens once a fresh session is decided and while
  readiness is degraded mid-turn, not when that decision is made from a
  real socket. Not pushed.)
- **Prior report:** `docs/reports/R0033_m2_6b_2_gemini_provider_canonical_cloud_turn_20260911.md`
  (**M2.6B.2 — GeminiLiveProvider + canonical cloud-turn integration —
  IMPLEMENTED, PASS (checkpoint), 2026-09-11.** ADR-0004 + Amendment 1
  unchanged. Built on M2.6B.1 (`R0032`): `src/nexa/realtime/turn.py`
  (`CloudTurnAccumulator` — one logical cloud turn -> at most one
  canonical commit, NeXa-local `generation` id, never a Gemini id);
  `src/nexa/realtime/turn_framing.py` (`UtteranceFramer` — preserves
  activity-start/audio/activity-end ordering through a provider NOT_READY
  window, bounded, no orphan audio, overflow prefers a whole completed
  oldest utterance); `src/nexa/realtime/router.py` (`ConversationRouter`
  — owns `ConversationPolicy`/`active_provider`/provider lifecycle/
  `CloudContextSnapshot` creation/cloud-turn commits/failure fallback;
  `LOCAL_ONLY` never calls the injected cloud-provider factory);
  `src/nexa/realtime/gemini/service.py` (`GeminiLiveProvider` — the
  production `RealtimeVoiceProvider`, built exactly per the R0032 source
  audit: construction-time `system_instruction`, initial `LLMContext`
  seeded from `snapshot.recent_turns`, one `LLMRunFrame` kickoff, Pipecat's
  own one-time `clientContent` seed, `UtteranceFramer` wired into the
  turn-I/O methods gated on `readiness==READY`). **Proven against the REAL
  Pipecat 1.8.1 `Pipeline`/`PipelineWorker`/`WorkerRunner`/aggregators**
  (a fake terminal service stands in for the network boundary only — no
  cloud call); a real bug was found and fixed this way
  (`WorkerRunner.add_workers()` only starts a worker's background task if
  the runner is already running — fixed by launching
  `asyncio.create_task(runner.run())`, matching the M2.6A probe's proven
  pattern). Additive `ConversationSession.record_external_exchange`
  (`src/nexa/conversation/session.py`) — the canonical cloud-turn write
  path (NORMAL / USER-ONLY / INTERRUPTED / NO-SPOKEN-ASSISTANT / invalid
  cases all tested); `send()`/`commit_interrupted_turn()` byte-for-byte
  unchanged. `pyproject.toml`: `cloud-gemini` optional extra added
  (`google-genai>=2.22,<3` + `websockets>=15,<17`); core deps unchanged;
  `pip install .` stays Google-cloud-free (import-isolation gates
  extended and still pass). **+57 new tests** (+1 net in an extended
  file) — full suite **870 tests, OK (skipped=7)**, zero regressions;
  zero non-additive `src/nexa/**` change (only `conversation/session.py`
  +1 enum +1 method, `conversation/__init__.py` +2 exports,
  `realtime/__init__.py` new re-exports). **Known gap, explicit:**
  `ReconnectController` (M2.6B.1) is not yet driven by
  `GeminiLiveProvider` on a real connection error — no GoAway/age-timer
  reconnect wiring yet; the "stale Pipecat context vs. fresh NeXa
  snapshot on resumption failure" design question from R0032 remains
  open. No live Gemini connection, no hardware test, no reconnect-duration
  test in this checkpoint. Not pushed.)
- **Prior report:** `docs/reports/R0032_m2_6b_1_cloud_realtime_voice_foundation_20260911.md`
  (**M2.6B.1 — Production Cloud Realtime Voice, provider-agnostic
  foundation — IMPLEMENTED, PASS (checkpoint), 2026-09-11.** ADR-0004 +
  Amendment 1 remain Accepted, unmodified. New `src/nexa/realtime/`
  package (zero cloud SDK import): `RealtimeVoiceProvider` ABC (a peer of
  `ModelProvider`, never a subtype) + `ProviderReadiness` +
  `RealtimeProviderCapabilities` + the typed provider->NeXa event stream;
  `ConversationPolicy` (`LOCAL_ONLY` default, `CLOUD_PREFERRED`, `AUTO`
  provisional) + `ActiveProvider` + `ProviderEligibilityPolicy`
  (Amendment 1 — `distribution_mode` ∈ {DEVELOPMENT, DISTRIBUTED} +
  `billing_verified`, **not** a key property); `CloudContextSnapshot` +
  `build_cloud_context_snapshot` (pure, bounded by turn count *and* char
  budget, never persona verbatim / memory / credentials / device
  internals); `InboundAudioBuffer` (the NeXa-owned Pipecat #5465
  protection — bounded by time *and* bytes, drop-oldest overflow with
  metrics + logging, per-frame sequence id so a reconnect never
  re-delivers already-flushed audio); `ProviderUsageEvent` /
  `SessionUsageAggregate` / `UsagePriceTable` (authoritative counts only,
  no raw audio, cost estimate always a labelled estimate); a deterministic
  `ReconnectController` (age-timer, `GoAway` deadline, only-keep-a-
  `resumable=true`-handle, bounded backoff+jitter — **no socket, no
  Gemini-specific choreography**, that is M2.6B.2); `src/nexa/realtime/
  gemini/credentials.py` (env-var-first + XDG-secrets-file
  `CredentialSource`, redaction, no paid/tier inference) and `.../voice.py`
  (`warm_female` -> `Sulafat` mapping) — neither imports `google.genai`.
  **Mandatory Gemini/Pipecat startup-sequencing source audit** performed
  against the *installed* `pipecat-ai==1.8.1` (2180-line
  `gemini_live/llm.py`) + `google-genai==2.22.0`, **no cloud call**:
  **CONFIRMS, does not contradict, ADR-0004/Amendment 1** — Pipecat 1.8.1
  already sets `HistoryConfig(initial_history_in_client_content=True)`
  unconditionally (the initial-history seed mechanism Amendment 1 §3
  described is already implemented by the library); `_handle_context`
  already forces a full `_reconnect()` if a later context's effective
  `system_instruction` differs from the init-provided one (a structural
  proof that config is immutable on an open connection — confirms
  Amendment 1 §2); `_handle_msg_resumption_update` already only stores a
  handle `if update.resumable and update.new_handle` (confirms Amendment 1
  §4's rule already exists in the library); no `GoAway` handling exists
  anywhere in the file (confirms the unchanged existing finding); the
  `LLMRunFrame` -> `LLMContextFrame` -> `_handle_context` chain is
  confirmed at the source level (matches the R0031 M2.6A root-cause
  finding exactly). Full audit:
  `docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`.
  **+73 new tests** (`test_realtime_provider/policy/snapshot/
  inbound_audio_buffer/reconnect/usage/gemini_credentials/gemini_voice/
  no_cloud_dependency.py`); full suite **812 tests, OK (skipped=7)** — 739
  baseline + 73 new, **zero regressions**; import-isolation gates
  (genuinely isolated subprocess) prove importing `nexa.realtime` and
  `nexa.realtime.gemini.{credentials,voice}` never pulls in
  `google.genai`, and `import nexa` alone never imports `nexa.realtime`.
  **Zero existing `src/nexa/**` files modified** — every change is a new
  file under `src/nexa/realtime/`; no `pyproject.toml` / dependency-file
  change; `ruff` clean on every new file (whole-repo's 82 pre-existing
  errors, all in `scripts/m1_bench/`, confirmed unchanged by `git stash`
  against the base commit); `pip check` clean; no Gemini call; no hardware
  test. **`GeminiLiveProvider`, `ConversationRouter`, the canonical cloud
  write-path (`ConversationSession.record_external_exchange`), HYBRID
  audio wiring, and everything user-facing remain M2.6B.2+, NOT started.**
  Not pushed.)
- **Prior report:** `docs/reports/R0030_cloud_realtime_voice_research_architecture_20260910.md`
  (**M2.6 — Cloud Realtime Voice — RESEARCH / ARCHITECTURE + Phase-0 fact
  corrections + M2.6A probe implemented (2026-09-10).** No `src/` change;
  no tracked-dependency change; not pushed. See R0030 *PHASE 0 CORRECTIONS*
  — supersedes the body where they conflict. **VERIFIED (official docs,
  2026-09-10):** model `gemini-3.1-flash-live-preview` is real + current
  (Preview, 131k/65k context, native audio-to-audio, **synchronous-only**
  function calling, **no prompt caching**); Gemini Live = WebSocket,
  16-kHz PCM in / 24-kHz PCM out, **audio chunks 20–40 ms**, ~10-min
  connection lifetime, **session-resumption tokens valid 2 h** after the
  last session ends, `GoAway.timeLeft`, `contextWindowCompression` →
  unlimited session, audio tokens accrue **≈25 tok/s**. **Official pricing
  (paid):** input $0.75/1M text · $3.00/1M audio (~$0.005/min); output
  $4.50/1M text · $12.00/1M audio (~$0.018/min); free tier free of charge.
  **Data terms:** outside EEA/CH/UK unpaid-quota data is used to improve
  Google products; in EEA/CH/UK the paid data terms apply to unpaid quota
  too, and a cloud voice *made available to* EEA/CH/UK users must use Paid
  Services (ADR-0004 decision). **Pipecat 1.8.1 `GeminiLiveLLMService`**
  (installed) has two real unfixed gaps — GitHub **#5465** (user
  audio/text/tool-results silently dropped during the reconnect window;
  fix PR #5497 OPEN, unmerged) and **no `GoAway` handling** — bounded for
  a short spike, must be wrapped for production. **EXTERNAL REPORTED RISK
  (community, not a Google-confirmed fact):** one forum thread reports
  `gemini-3.1-flash-live-preview` native audio speaking Polish with a
  strong EN/US accent — Google only acknowledged the report. The
  authoritative check is the M2.6A operator test.
  **Connectivity smoke PASSED (2026-09-10):** the operator-provided key is
  accepted, model reachable, Live WebSocket setup in 449 ms, clean
  disconnect (no audio). `google-genai 2.22.0` installed in the research
  venv (+~32 MiB; `websockets` 17.1→16.1.1 within pipecat's range; pip
  check clean; no tracked dep file changed). M2.6A probe
  (`docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py`)
  implemented + `--dry` validated. Key stored **outside the repo** at
  `~/.config/nexa/secrets/gemini.env` (700/600), var `NEXA_GEMINI_API_KEY`.
  **[HISTORICAL — as of R0030, superseded]** ~~CRITICAL OPEN QUESTION for
  M2.6A to MEASURE (R0030 C6): whether the complete input transcription
  reaches NeXa *before* first cloud audio — if not, `ResponseLanguageResolver`
  cannot steer the same response without added latency; M2.6A temporarily
  lets Gemini mirror the spoken language natively; production
  language-routing authority is decided in ADR-0004 after measurement.~~
  **ANSWERED (R0031 C6, turn-local): RAW transcript precedes first audio
  3/3 valid turns; ADR-0004 Decision F adopted Gemini native mirroring
  (Option A) as the production default, with a measured Option B fallback
  — this is no longer open.**
  **[HISTORICAL — as of R0030, superseded]** ~~Architecture (design,
  ADR-0004 owed):~~ **CURRENT: ADR-0004 is WRITTEN, Accepted, and amended
  by Amendment 1 (2026-09-10) — see "Latest decision" above.** As designed
  in R0030: `ConversationSession` stays the
  one authority; cloud is a NEW `RealtimeVoiceProvider` boundary (not a
  `ModelProvider` — that's a text stream) handed a NeXa-derived, privacy-
  filtered `CloudContextSnapshot`; canonical transcript from Gemini's
  input/output transcription streams + a spoken-audio high-water mark; raw
  cloud audio NOT retained; minimal cloud `system_instruction` (role card,
  not NeXa's identity); `ConversationPolicy` (AUTO / LOCAL_ONLY /
  CLOUD_PREFERRED, NeXa-owned) distinct from runtime `active_provider`
  (LOCAL / CLOUD); the model may classify a switch intent, **NeXa executes
  it**; keep XVF3800 AEC + local Silero as the turn authority (HYBRID,
  server VAD off), NeXa keeps final authority over the speaker.
  **M2.6A CLOUD REALTIME VOICE FEASIBILITY — PASS / OPERATOR-CONFIRMED
  (2026-09-10), `R0031`. VOICE `Sulafat` — OPERATOR-CONFIRMED, frozen as
  the current NeXa cloud voice baseline.** Attempt #1 = infra bug (missing
  `LLMRunFrame` kickoff → `GeminiLiveLLMService` never became
  `_ready_for_realtime_input`, which with server VAD off gates
  `activity_start`/audio/`activity_end`), fixed. Attempt #2 (Pipecat
  default voice) = real PL/EN conversation judged EXCELLENT / "essentially
  immediate" / "mega super"; only ask was a female/cozy/warm voice.
  Attempt #3 (`Sulafat`, "Warm") = real natural conversation → operator:
  *"I like this voice, we keep it."* Machine evidence (3 sessions,
  **turn-local** reconstruction): **EOT→first audible ≈ 0.75–0.81 s
  median** (R0030 ≤1.5 s — PASS, faster than local); **barge-in ≈ 2 ms**
  local playback-stop (R0030 ≤100 ms — PASS); local speaker silent ~27 ms
  **before** the server-round-trip interruption frame; AEC 0 failures
  every session; #5465 NOT_READY window startup-only with no user speech
  in it. The "1.93 s / 19.84 s outliers" were **metric artifacts**
  (fragmented user speech + cross-turn pairing) — fixed by turn-local
  reconstruction; real per-turn latency ~0.75 s. Three sessions cost
  ≈ 7 cents. **C6 (turn-local, Sulafat session):** RAW input transcription
  precedes first response audio in **3/3 valid turns** (~0.5 s margin),
  PUSHED in 2/3 — **but timing headroom ≠ steerability** (NeXa cannot be
  assumed to influence the same cloud response; `activity_end` already
  closed the turn), so ADR-0004 defaults to Gemini native language
  mirroring (Option A), with delayed-`activity_end` + local language-ID as
  the measured Option B. Reconnect/lifetime testing deferred to M2.6B.
  Spike dir: `docs/research/m2_6_cloud_realtime_voice/` (static audit,
  connectivity smoke, probe, 3 evidence JSONs + 3 recomputed).
- **Prior report:** `docs/reports/R0029_m2_5b_production_barge_in_interruption_20260909.md`
  (**M2.5B — Production Barge-In / Interruption — COMPLETE / OPERATOR-CONFIRMED
  (2026-09-10).** Production barge-in ships behind
  `LocalAudioConfig.bargein_enabled` (default `False` = byte-for-byte R0026;
  probe `--bargein` turns it on).
  **Final live acceptance (2026-09-10, real Raspberry Pi,
  `apps/nexa_bilingual_voice_probe.py --bargein`):** `✓ AEC REF ACTIVE`;
  one continuous ~7-minute session with repeated natural + nested
  interruptions across multiple consecutive responses. Deliberate
  interruptions confirmed; old answer stopped; replacement started
  normally; a long Polish interruption captured as **one** canonical turn;
  PL/EN routing correct; no stale response resumed; no queue accumulation.
  **Zero** `capture[N] hit the 12.0s hard cap`, **zero** `interruption
  capture timed out after 15.0s`, **zero** `DROP_BUSY_RESPONSE_IN_FLIGHT`.
  Final telemetry: busy-drop (utterances at capture) 0; busy-drop (STT
  results at adapter) 0; STT queue depth 0; conversation queue depth 0;
  max concurrent STT 1; max concurrent turns 1. The one `✗ AEC REF DOWN`
  was after operator `Ctrl+C` on shutdown (expected).
  **Operator UX (unchanged from the 2026-09-10 acceptance):** pleasant,
  natural, fluent, good conversational quality, acceptable local response
  speed — accepted local voice UX, **not re-tuned**. Ordinary whisper
  `base-q8_0` recognition slips remain, known / accepted, **not a
  blocker**; STT model selection is not reopened.
  **Substages:** **M2.5B.1** interrupt-utterance capture/coalesce (one
  interruption → one canonical turn, `2460463`) + responsive Ollama
  cancellation (`cancel → worker-stop` ~2 s → **251 ms**, `4a55a7e`) —
  DONE. **M2.5B.2** `nexa.conversation.ProviderWindow` (`7f4e182` →
  `79a6899`) — a prefix-stable bounded provider-facing window over the
  still-complete canonical `ConversationSession.history`, boundary =
  `keep_entries=0` **context reset** (reset turn re-prefills persona
  (cached) + new user turn → ~3.4 s ordinary turn) — removes the permanent
  long-session KV-cache cliff; steady-state ~4 s for the whole session;
  `OLLAMA_NUM_PARALLEL=2` measured on the Pi + **rejected** (gemma4 SWA →
  concurrent foreground turn cold-reprocesses ~49 s); real-Pi 112-turn /
  10-reset benchmark retained — DONE. **M2.5B.3** interruption-capture
  lifecycle: **v1** (`989f68f`) fixed multi-segment capture but its live
  re-test FAILED (spurious 12 s / 15 s warnings after the replacement
  response — a stale/zombie `_capture_deadline` task orphaned by the forced
  `INTERRUPTING → RESPONDING` transition); **v2** (`be9f0cd`)
  capture-generation-scoped timers — every timer holds its `capture_id` by
  value and goes inert once `_active_capture_id` moves on; one
  `_end_capture_phase(abandon=…)` closes the phase on every exit incl. the
  forced transition; `adapter.abandon_interrupt_capture()` — **v2 passed
  the live re-test above** — DONE.
  `pytest` 732 passed / 7 skipped; `unittest` 739 OK / 7 skipped; `ruff`
  clean; `git diff --check` clean. Not pushed.)
- **Prior report:** `docs/reports/R0028_m2_5a_bargein_interruption_architecture_feasibility_20260909.md`
  (**M2.5A — Barge-In / Interruption Architecture & Real-Hardware Feasibility
  — COMPLETE / OPERATOR-CONFIRMED (2026-09-09).** No `src/` change in M2.5A.
  Key facts for M2.5B: **(1)** on the bare `plug:usb_speaker` route NeXa's
  own Piper voice trips the reSpeaker + Silero VAD on **14/14** silent-
  playback trials (latched 3.4–17.0 s) — a hot mic during a response is
  unsafe there; **(2)** feeding the XVF3800 its **AEC far-end reference**
  (identical PCM also to `plug:respeaker`) removes it — automated 0/4, and
  the operator M2.5A.2 test: audible+AEC active 3/3, QUIET_AEC false-VAD 0,
  operator voice detected 3/3, AEC live at every detection, Silero
  separation AEC-quiet residual conf/vol p95 0.756/0.536 vs operator speech
  0.985/0.761, `M2.5A CLOSE CRITERIA MET = True`; **(3)** SPIKE B-live v2
  media-stop: VAD start → PLAYBACK TASK STOPPED **28.5 ms mean / 37.4 ms
  max** (playback-process stopped, not last speaker sample); **(4)** Pipecat
  1.8.1 has first-class interruption (`InterruptionFrame`,
  `broadcast_interruption()`, `base_output.handle_interruptions()`) but NeXa
  wires none of it; `session.send(cancel_token=)` / Ollama cancel work
  (~1 s, model resident) but the voice path passes no token. **Superseded
  operator-action / next-step items in R0028 are historical — M2.5A.2 has
  since passed.** `pytest` 621 / `unittest` 628; `ruff` clean; `git diff
  --check` clean. Not pushed.
- **Prior report:** `docs/reports/R0027_response_language_override_vs_sticky_20260908.md`
  (M2.4B.5B — **Response Language Override vs Sticky Preference — DONE**.
  Corrected `ResponseLanguageResolver`: a **one-turn override** ("Answer in
  English.", "Odpowiedz po polsku.") now affects **this reply only** and
  does **not** set a sticky preference; a **sticky command / switch**
  ("From now on speak English.", "Od teraz mów po angielsku.", "Wracamy do
  polskiego.", "Switch to English.") sets `ResponsePreference.sticky`.
  Content mentions ("Tell me about Polish history.", "What is the English
  word for kot?", "Translate this English sentence.") never change
  anything. New `detect_language_request(text) -> LanguageRequest(language,
  kind)`; `detect_explicit_language_request` kept as a back-compat wrapper;
  `ResponseLanguageDecision.request_kind` added. 3 concepts kept distinct
  (InputSpeechLanguage / ResponseLanguage / ResponseLanguagePreference);
  the resolver mutates exactly one field on exactly one branch. +18
  `tests/test_response_language_override_vs_sticky.py`; 2 tests in
  `test_bilingual_voice_input.py` updated. `pytest` 587 / `unittest` 594;
  `ruff` clean; `git diff --check` clean. Nothing else moved — STT /
  `LanguageIdGuard` / whisper model / LLM / `num_thread=2` /
  `keep_alive=30m` / warm-up / Piper / `SpeechPlanner` / continuity / the
  M2.4B.5A `HalfDuplexGate` fix / `ResponseMode.TEXT` all unchanged. M2.5
  not started. Not pushed.
  **Operator status:** M2.4B.5 **OPERATOR-CONFIRMED (2026-09-08)** for
  normal bilingual PL/EN live voice (auto PL↔EN switching, normal voice
  transcribed, STT ~2.89–3.21 s, no slowdown/hang, queue depths 0);
  M2.4B.5A **OPERATOR-CONFIRMED** for normal post-fix stability; **TV-stress
  test NOT PERFORMED / operator waived** (deterministic + headless
  busy-drop evidence stands, R0026); low-volume speech can degrade
  recognition — normal volume accepted, **not** a new STT task.)
- **Prior report — R0026** (`docs/reports/R0026_m2_4b_5a_live_voice_stability_backlog_investigation_20260908.md`,
  M2.4B.5A — **Live Voice Stability / Backlog Investigation — ROOT CAUSE
  FOUND + FIXED + regression tests + headless contention proof;
  OPERATOR-CONFIRMED for normal post-fix operation; TV-stress test NOT
  PERFORMED / operator waived**. The B.5 operator session
  degraded from ~2.8 s STT to 7–10 s and "hung" after the mic picked up TV
  while NeXa was answering. **Root cause (measured, not inferred):** the
  `HalfDuplexGate` only closed the mic once TTS *audio was playing* — it
  left the mic OPEN through the ~10–25 s think/LLM-generate/TTS-synth
  window. TV audio in that window was captured on END_OF_TURN and
  submitted **unconditionally** to `SerialTranscriptionQueue` (`_UtteranceCaptureFrameProcessor`)
  → `SerialConversationQueue` (`VoiceConversationAdapter`) → real
  `ConversationSession.send()` + TTS. Bounded (8 STT / 4 conv, overflow
  raises — not silent) but the backlog under CPU contention snowballed.
  Contention measured (`b5a_contention_probe.py`): STT total mean **3.19 s
  isolated → 13.16 s** with `gemma4:e4b` generating + Piper synthesising
  (+9.97 s, ~4.1×; CPU 89 %→99.4 %; `llama-server` the dominant consumer;
  **max concurrent `whisper-cli` = 1 in both phases** — no overlap).
  Detector: no leak (200 `detect()` calls → +58 MB one-time then flat;
  `close()` frees it). Half-duplex was **wired correctly** (identical to
  the canonical `nexa_voice_tts_probe.py`) — the bug was the gate's
  *contract*. **Fix (strict pre-M2.5 half-duplex, not barge-in):**
  `HalfDuplexGate.response_in_flight` (dispatch → generation + playback
  done); `mic_suppressed` covers it; `_UtteranceCaptureFrameProcessor`
  drops a busy-period utterance at capture with
  `DROP_BUSY_RESPONSE_IN_FLIGHT` telemetry (never queued);
  `VoiceConversationAdapter` `_turn_in_flight` drops a busy-period STT
  result at the adapter (never enqueued); `ConversationSession` history
  untouched. New `stt_queue_depth` / `conversation_queue_depth` /
  `dropped_busy_*` telemetry, printed live in
  `apps/nexa_bilingual_voice_probe.py`. Post-fix clean PL↔EN sequence:
  **2.81 s mean** (no regression). +14 `tests/test_bilingual_voice_stability.py`;
  `test_voice_half_duplex_gate.py` + `test_voice_conversation_adapter.py`
  updated for the intended change. `pytest` 569 / `unittest` 576; `ruff`
  clean; `git diff --check` clean. `gemma4:e4b` / `num_thread=2` /
  `keep_alive=30m` / warm-up / `ResponseMode` / Piper / `SpeechPlanner` /
  continuity / whisper model / guard thresholds all **unchanged**
  (`ResponseLanguageResolver` semantics corrected later in R0027).
  **OPERATOR-CONFIRMED for normal post-fix operation (2026-09-08)**;
  TV-stress test NOT PERFORMED / operator waived. M2.5 not started. Not
  pushed.)
- **Prior report — R0025** (`docs/reports/R0025_m2_4b_5_automatic_bilingual_pl_en_voice_input_20260908.md`,
  M2.4B.5 — **Automatic Bilingual PL/EN Voice Input — IMPLEMENTED + tests;
  live operator voice acceptance PENDING**. R0024's accepted architecture
  is built behind the existing STT boundary (ADR-0003 D11), on the ONE
  canonical `ConversationSession`; `gemma4:e4b` / `num_thread=2` /
  `keep_alive=30m` / warm-up / persona / SpeechPlanner / continuity / Piper
  voices+speed / `ggml-base-q8_0` / `-t 4` all **unchanged**.
  (1) `nexa.stt.WhisperCppLanguageDetector` — `ctypes` binding to the
  **pinned, already-installed** `libwhisper.so` (`v1.9.3`; no fork, no
  version change, `ctypes` is stdlib) → `p_pl`, `p_en`, raw top-1 lang.
  (2) `nexa.stt.LanguageIdGuard` — one authority: AUTO_ACCEPT vs
  FALLBACK_REDECODE from constrained `argmax(p_pl,p_en)` + raw lang +
  configurable confidence threshold (**0.60 = R0024 initial calibration,
  documented, telemetered**) + 2.0 s duration floor.
  (3) `nexa.stt.BilingualSpeechTranscriber` (implements `SpeechTranscriber`)
  — LID → guard → **exactly one** explicit `whisper-cli` decode in the
  guard-selected PL/EN language; the wrong-language transcript is never
  generated, so nothing unsafe reaches `ConversationSession`. Holds the
  transient `last_input_language` (`pl`|`en`|`None`; not a turn, not
  memory).
  (4) `nexa.conversation.ResponseLanguageResolver` — SEPARATE authority:
  mirror `InputSpeechLanguage` by default; explicit request ("Answer in
  English." / "Odpowiedz po polsku." / "Od teraz mów po angielsku." /
  "Wracamy do polskiego.") switches + sets a transient sticky session
  preference; a narrow deterministic detector with a content-question
  deny-list so ordinary mentions of a language don't switch. Threaded via
  `ConversationSession.send(response_language=…)` → per-turn
  `language_directive`, replay-stable (R0009); `None`/TEXT unchanged.
  (5) TTS voice maps from **ResponseLanguage** (`bridge.select_voice`),
  not the transcript/STT-decode language. `InputSpeechLanguage ≠
  ResponseLanguage ≠ TTSVoice` — 3 owners.
  **Headless latency** (`b5_latency_probe.py`, real ctypes LID + real CLI
  decode over the R0024 corpus): total **~2.83 s mean / 2.80 s median /
  3.00 s p90** per utterance = **≈ +1.13 s** vs the ~1.7 s explicit
  baseline (matches R0024 `-l auto`), **flat** — LID + one decode always;
  FALLBACK adds no extra pass. Guard replay over all 50 R0024 fixtures:
  30/30 monolingual AUTO_ACCEPT correct (incl. `ru`/`ko`/`he` third-lang
  recovery); 10/10 shorts FALLBACK → inherit (Tak.→"Tak." not "Talk.").
  Mixed/code-switch: **BEST EFFORT / DEFERRED (not guaranteed)** — no
  dual-decode; guard picks the same language `-l auto` would, so not worse
  than R0024. **ADR-0003 Amendment 1 added** (guarded `-l auto` accepted;
  code-switch not guaranteed; shorts inherit; input≠response language;
  R0024 = evidence authority; no unrelated decision rewritten).
  +39 `tests/test_bilingual_voice_input.py`; `pytest` 552 / `unittest`
  559; `ruff` clean; `git diff --check` clean. Live harness ready:
  `apps/nexa_bilingual_voice_probe.py` (one command, 10 utterances). Not
  pushed.)
- **Prior report — R0024** (`docs/reports/R0024_m2_4b_4_bilingual_voice_input_research_benchmark_20260908.md`,
  M2.4B.4 — **Bilingual Voice Input Research & Benchmark — COMPLETE,
  research + decision only, NO production change**. Operator recorded a
  50-utterance real-voice corpus (15 PL / 15 EN / 10 short-ambiguous /
  10 mixed-code-switch, `docs/research/m2_4b_bilingual_stt/`); benchmark
  ran the pinned production whisper.cpp `v1.9.3` `ggml-base-q8_0` `-t 4`
  over all 50 under 4 strategies (200 runs). **Key evidence:** whisper
  v1.9.3 **never confuses PL with EN** (0/15 PL→EN, 0/15 EN→PL);
  `-l auto` monolingual LID **90 %** (27/30), all 3 misses are
  low-confidence slips to a *third* language (`ru`/`ko`/`he`, p 0.35–0.53
  vs 0.6–0.99 for confident hits); `-l auto` transcript == explicit
  baseline on 28/30 monolingual (the 2 diffs are the third-lang misdetects
  → wrong-script S3). `-l auto` costs **~+1.1 s/turn**; `-dl`→explicit
  costs ~+1.3 s and adds nothing (same detector) — rejected. Short PL
  one-word utterances break under auto (Tak→"Talk.", Nie→"Не.",
  Dobra→"Доброе утро!") but at low confidence — gate-able. Mixed:
  `-l auto` 6 USABLE / 4 PARTIALLY_USABLE / 0 BROKEN (better than either
  forced language). Resources identical across strategies: RSS 221 MB,
  ≤70 °C, `throttled 0x0`. **No stronger-model download** (architecture
  decides on base/q8_0 evidence; PL transcript-quality S2 rate 13 % PL /
  20 % EN is a *pre-existing* `base/q8_0` issue — separate track, `R0016`,
  candidate `large-v3-turbo-q5_0` named for a future approved benchmark).
  **Decision:** future bilingual input = `-l auto` single pass +
  PL/EN-and-confidence guard on the label + inherit-previous-input-language
  fallback; response-language stays a *separate* resolver from STT.
  **ADR-0003 D5 verdict: PARTIALLY supersede** — automatic per-utterance
  PL↔EN switching for normal turns is evidence-backed; isolated shorts +
  true within-utterance code-switch stay deferred. Proposed D5 amendment
  recorded, **ADR not edited**. `pytest` 513 / `unittest` 520 (+19
  `tests/test_bilingual_stt_bench.py`, +14 `_corpus`). Production
  `VoiceRuntime` explicit-language behaviour unchanged. Not pushed.)
- **Prior report — R0023** (`docs/reports/R0023_m2_4b_3_6_production_local_model_serving_freeze_20260908.md`,
  M2.4B.3.6 — **Production Local Model Serving Freeze — PASS**. The
  R0021/R0022 serving findings are productionised on the one canonical
  conversation path with `gemma4:e4b` kept as `DEFAULT_LOCAL_MODEL`:
  (1) **`num_thread=2`** — one-time provider policy in `nexa.config`
  (`DEFAULT_LOCAL_NUM_THREAD`), sent as an Ollama request option by
  `LocalModelProvider` (no `taskset` / LLM `renice` / RT sched; Piper
  stays `nice +10`); (2) **`keep_alive = 30m`** (`DEFAULT_LOCAL_KEEP_ALIVE`,
  was stock `5m`; not infinite — ~10 GB resident model, deferred
  resource-policy decision); (3) **startup warm-up** —
  `nexa.bootstrap.warm_up_session()` sends the canonical persona +
  `ResponseMode.VOICE` prefix with EMPTY history (`num_predict=1`,
  discarded) so Ollama loads the model and fills the ~507-token KV
  prefix. **Never writes `ConversationSession` history, never creates a
  turn, no second session/provider/persona; raises `ModelUnavailableError`
  visibly if Ollama is down.** Measured (`b36_serving_freeze_probe.py`,
  eviction-controlled, real Pi): cold first real voice turn TTFT ~76.7 s
  (load 33.4 s + 507-tok prefix reprocess 43.3 s) → with warm-up ~3.2 s
  (`prompt_eval` 43.3 s → 3.2 s; prefix served from KV cache) — **~73 s
  saved on turn 1, warm-up is not a no-op.** `num_thread=2` Piper-active
  re-confirm: decode ~1.74 → ~2.85 tok/s (+64 %), Piper unaffected, no
  throttle. `ResponseMode.TEXT` byte-for-byte unchanged, `VOICE` policy
  unchanged, PL/EN language mechanism unchanged, **no `e2b` routing / no
  per-language model / no fallback model**. `pytest` 480 passed / 7
  skipped / 14 subtests; `unittest` 487 OK; +20
  `tests/test_production_serving_freeze.py`; `test_operator_blind_ab.py`
  `TestProductionUntouched` updated for the approved change. Not pushed.)
- **Prior report — R0022** (`docs/reports/R0022_m2_4b_3_5_operator_blind_model_ab_20260908.md`,
  M2.4B.3.5 — **operator-blind local voice model A/B — CLOSED 2026-09-08**.
  Sealed `secrets.SystemRandom` mapping **revealed**: Candidate A =
  `gemma4:e2b`, Candidate B = `gemma4:e4b`. The operator ran A pl+en and
  B pl+en on the full realtime voice path and **chose `gemma4:e4b`** —
  reliability/quality over `e2b`'s ~1.6–2× speed; EN with `e4b` "idealny";
  PL slower but accepted (Polish latency + Polish STT are separate later
  tracks); NeXa stays bilingual; this is a model decision only, `e2b` not
  routed to. Operator gave verbatim qualitative observations, no 1–5
  scores (none invented). Objective `blind_results/` metrics (warm turns,
  `num_thread=2` + `keep_alive=30m` both): `e2b` PL ~18.4 / EN ~29
  chars/s, warm TTFT ~1.7 s; `e4b` PL ~11.2 / EN ~16.5 chars/s, warm
  TTFT ~3.0–3.5 s, END_OF_TURN→first-audio ~10–13 s; neither throttled,
  ≤ 67 °C; `e4b` holds ~2.4 GB more RAM. One Candidate B Polish attempt
  produced a 0-byte result and needed a Pi reset before the successful
  run — recorded, **not** attributed to the model. **B.3.3 now has real
  operator PL + EN voice evidence.** `R0021` = M2.4B.3.4 LLM serving &
  voice benchmark (`gemma4:e4b` PL ~9.4 chars/s / best PL quality;
  `gemma4:e2b` ~2× / repeatable factual regressions; `num_thread=2`
  +12–14 % decode); `R0020` = M2.4B.3.3 `ResponseMode` voice reply
  policy;
  `R0019` = M2.4B.3.2 continuity controller (no-op on today's rate);
  `R0018` = the rate-budget math authority; `R0017` … `R0011` as before.)
- **Prior report — R0021** (`docs/reports/R0021_m2_4b_3_4_local_llm_serving_voice_benchmark_20260907.md`,
  M2.4B.3.4 — local LLM serving & voice performance benchmark;
  **research only, no production model switch**. Measured `gemma4:e4b`
  under the real B.3.3 voice path: **PL ~9.4 generated chars/s** (~59 % of
  R0018's 15.9 target), **EN ~14.7** (~92 %); language ratio PL/EN ≈ 0.64;
  tok/s ~3.1; warm-prefix TTFT ~2.6 s, cold-prefix (fresh conversation
  turn 1) ~29 s (the ~535-tok persona+voice-directive preamble at
  ~20 tok/s), cold load ~33 s, ~10.2 GB resident; temp ≤ 72 °C, never
  throttled. Serving: **`num_thread=2` gives +14 % tok/s** (per-request
  Ollama option, no sudo) — still ~85 % of PL target; `num_batch`/`num_ctx`
  do nothing. Shortlist: **`gemma4:e2b` is the only real candidate** —
  ~2× faster (PL ~18.7 chars/s ✓ target, EN ~27), warm TTFT ~1.2 s,
  7.5 GB, same family so the persona + voice directive port unchanged —
  **BUT a repeatable quality regression** (factual self-contradictions,
  a hallucination, one EN→PL mirroring break, thinner reasoning). qwen3/
  qwen3.5:4b = same speed, garbled PL. qwen2.5:3b / llama3.2:3b = fast but
  word-salad PL. Bielik Q8_0/Q4KM = 2 tok/s / empty completions. **No
  model beats `gemma4:e4b` without quality loss.** Recommendation:
  operator A/B blind `e4b` vs `e2b`; take `num_thread=2` + warm-keep as
  free wins regardless. Also fixed (separate commit `c64b66b`): the
  continuity `_hold_then_release` self-cancel log warning + 2 regression
  tests. `R0020` = M2.4B.3.3 `ResponseMode` voice reply policy;
  `R0019` = M2.4B.3.2 continuity controller (no-op on today's rate);
  `R0018` = the rate-budget math authority; `R0017` = M2.4B.3.1 Piper
  `nice +10` + TTS context timeout 8 s; `R0016` = M2.4B.2A
  LaTeX/truncation fix; `R0015` = M2.4B.2 speech planner; `R0014` =
  M2.4B.1A CPU spike + metric fixes; `R0013` = M2.4B.1 instrumentation;
  `R0012` = M2.4B research; `R0011` = M2.4)
- **Current milestone:** **M1 — Natural Text Conversation — COMPLETE**;
  **M2 — Realtime Voice — LOCAL VOICE COMPLETE** (M2.1, M2.2, M2.3, M2.4,
  M2.4B COMPLETE, `OPERATOR-CONFIRMED`; **M2.5A — barge-in/interruption
  architecture & feasibility — COMPLETE / OPERATOR-CONFIRMED (2026-09-09),
  `R0028`**; **M2.5B — production barge-in / interruption — COMPLETE /
  OPERATOR-CONFIRMED (2026-09-10), `R0029`**). **Local realtime voice with
  production barge-in is done.** **Now: M2.6 — Cloud Realtime Voice.
  Research / architecture COMPLETE (`R0030`, 2026-09-10); **M2.6A
  feasibility PASS / OPERATOR-CONFIRMED** (`R0031`, 2026-09-10), voice
  `Sulafat` OPERATOR-CONFIRMED, frozen v1 cloud baseline
  (`gemini-3.1-flash-live-preview`; Pipecat OPTION C wrapped
  `GeminiLiveLLMService`); **C6 answered sufficiently for ADR-0004**
  (turn-local RAW transcript precedes first audio 3/3 valid turns — see
  R0031). **`ADR-0004` — Cloud Realtime Voice provider boundary — WRITTEN
  and Accepted (2026-09-10), Amendment 1 Accepted (2026-09-10)**: one
  `ConversationSession` authority for local + cloud; new NeXa-owned
  `RealtimeVoiceProvider` boundary (a *peer of* `ModelProvider`) in a
  provider-agnostic `src/nexa/realtime/` package; `CloudContextSnapshot`
  (privacy allow-list); `ConversationPolicy` (`LOCAL_ONLY` default) vs
  `active_provider`; language routing Option A default; NeXa-owned #5465
  buffer + `GoAway`/age-timer reconnect (Amendment 1: keep only the latest
  `resumable=true` handle, no assumed make-before-break overlap);
  `google-genai` as an optional `cloud-gemini` extra; deployment
  **eligibility policy** — `ProviderEligibilityPolicy(distribution_mode,
  billing_verified)` — **not** a key property (Amendment 1: the operator is
  in the **United Kingdom**, not the EEA; a Gemini API key has no
  paid/free property; `DEVELOPMENT` mode is **not** blocked by billing —
  the Gemini API Free Tier is available in the UK; `DISTRIBUTED` mode to
  EEA/CH/UK users requires `billing_verified`); full ordered M2.6B plan +
  19 acceptance gates inside the ADR.
  **`M2.6B` — production implementation — IN PROGRESS.**
  `M2.6B.1` (`R0032`, 2026-09-11): the provider-agnostic foundation
  (`RealtimeVoiceProvider`, `ConversationPolicy` +
  `ProviderEligibilityPolicy`, `CloudContextSnapshot`, the #5465
  `InboundAudioBuffer`, usage telemetry, a deterministic
  `ReconnectController`, Gemini credential/voice-mapping) — IMPLEMENTED.
  **`M2.6B.2` (`R0033`, 2026-09-11): `GeminiLiveProvider` (production
  `RealtimeVoiceProvider` wrapping Pipecat's `GeminiLiveLLMService`,
  proven against the REAL Pipecat pipeline/worker/aggregator machinery,
  no cloud call) + the canonical cloud-turn write path
  (`CloudTurnAccumulator`, `UtteranceFramer`, `ConversationRouter`,
  additive `ConversationSession.record_external_exchange`) — IMPLEMENTED.**
  +57 new tests (870 total, 0 regressions); `cloud-gemini` optional
  `pyproject.toml` extra added (core deps unchanged, `pip install .` stays
  Google-free). **`M2.6B.2A` (`R0034`, 2026-09-11): pre-hardware
  hardening — IMPLEMENTED.** Fixed a real silent-user-speech-loss bug
  (mid-turn readiness loss — see "Latest report" above for the full
  writeup) with a deterministic abort-and-restart policy; completed the
  provider event map (interim transcription, a proper
  `GenerationCompleteEvent`, error frames — which also exposed and fixed
  an upstream-vs-downstream tap gap); closed the "manual injection" test
  gap with `ConversationRouter.handle_provider_event()`; tested 5
  turn-ordering/interruption permutations (found and fixed a related
  accumulator gap: a late assistant-transcription delta after
  interruption was still being appended); made the fresh-snapshot
  reconnect mechanism precise (destroy-and-recreate) and tested against a
  deliberately stale context. +16 tests (886 total, 0 regressions).
  **`M2.6B.3` (`R0035`, 2026-09-11): production HYBRID cloud audio +
  reconnect wiring — IMPLEMENTED, TEST-READY (deterministic checkpoint);
  real-hardware/Gemini operator acceptance NOT YET RUN.** `GeminiLiveProvider`
  now drives `ReconnectController` via a NeXa-owned `_readiness_monitor()`
  observation seam (Pipecat's own reconnect is automatic/internal, no
  external hook — confirmed from source); mid-user-turn connection loss is
  explicitly NOT assumed safely resumable (no source evidence found) —
  `ConversationRouter.recover_from_mid_turn_loss()` destroys the old
  provider and starts a fresh one from a fresh canonical snapshot,
  replaying only not-yet-delivered PCM. New `src/nexa/realtime/gemini/
  runtime.py` (`GeminiVoiceRuntime`) wires the REAL hardware path —
  reSpeaker input → local Silero VAD (Gemini server VAD stays OFF) →
  provider → Gemini → assistant audio → USB speaker, teed to the XVF3800
  AEC far-end reference via the existing, unmodified `AecReferenceFeeder`
  — and cloud barge-in via the existing, unmodified `BargeInController`
  (`_on_confirmed`: `router.on_interruption()` freezes the spoken prefix,
  `provider.cancel()` fire-and-forget, capture phase closed immediately;
  local speaker-stop never waits on any of it). New operator app
  `apps/nexa_cloud_voice_app.py` (`--dry` proven live in-sandbox: full
  object graph constructs with no audio device/network touched). +10
  tests (896 total, 0 regressions). **Not yet run: the real Gemini/
  hardware operator conversation — see R0035 for the exact launch command
  and READY lines.**
  **`M2.6B.3A` (`R0036`, 2026-09-11): final pre-live hardening —
  IMPLEMENTED.** Fixed three production-significant gaps R0035 left:
  bargein-finish now waits for real `BotStoppedSpeakingFrame` playback
  truth via new `_ResponseLifecycle` (never `GenerationCompleteEvent`
  alone — Pipecat's own source proves generation-complete says nothing
  about the output-transport's own audio queue); the interrupted spoken
  prefix now follows a new `_SpokenPrefixHighWater` one-chunk-lag
  combinator instead of the raw running transcription (Pipecat's own
  installed source documents output-transcription arriving ahead of, and
  containing more text than, the audio actually produced); and
  `ConversationRouter.recover_from_mid_turn_loss()` — previously
  uncalled — is now actually driven by `_consume_provider_events`, with
  an atomic provider/`_ProviderHandle` swap and never a second concurrent
  event-stream reader. Also fixed a real AEC-callback bug (the operator
  app was silently replacing, not composing with, the metrics logger) and
  explicitly documented `should_proactively_reconnect()` as having no
  caller (deferred, not overclaimed). +15 net new tests (911 total, 0
  regressions). Still not yet run: the real Gemini/hardware operator
  conversation — see R0036.
  **`M2.6B.3B` (`R0037`, 2026-09-11): interrupted cloud history safety
  only — IMPLEMENTED.** Found R0036's own one-audio-chunk-lag spoken-
  prefix mechanism still overclaimed (a later chunk's existence doesn't
  prove how much of an earlier text snapshot that chunk's own audio
  covers). Re-searched source for real alignment: `google.genai.types.
  Transcription.words`/`WordInfo` timing fields exist in the SDK schema
  but Pipecat's installed service never reads/forwards them — no
  deterministic alignment reachable. Deleted `_SpokenPrefixHighWater`;
  interrupted-turn assistant text is now unconditionally empty
  (`CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""`) — under-crediting
  accepted, crediting unspoken words is not. Normal completions
  unaffected. Playback lifecycle/AEC/reconnect/router/model/VAD/barge-in
  untouched. 5 tests replace R0036's 6; full suite 910 tests, 0
  regressions. **Standing note restated: `M2.6B` must not be marked
  COMPLETE after the hardware run until proactive-reconnect is
  implemented+validated or explicitly deferred by an ADR amendment.**
  Still not yet run: the real Gemini/hardware operator conversation — see
  R0037 (launch command/READY lines unchanged from R0035/R0036). The
  operator's currently available Gemini API key/tier is sufficient to
  continue `DEVELOPMENT`-mode M2.6B work (stored outside the repo); a
  verified paid/billing-enabled project is required only before any
  `DISTRIBUTED` release to EEA/CH/UK users.**
- **Current substage:** M1.1 COMPLETE, `OPERATOR-CONFIRMED` (2026-09-05).
  M1.0B COMPLETE; operator blind test COMPLETE 2026-09-04; M1.1 local
  baseline FROZEN to `gemma4:e4b`, ADR-0002 Amendment 2, 2026-09-05. M2
  open-source-first research COMPLETE (2026-09-05) — `R0005`. M2.0A
  feasibility spikes COMPLETE (2026-09-05) — `R0006`. `ADR-0003` Accepted
  (2026-09-05). **M2.1 — Pipecat foundation + local audio + Silero VAD —
  COMPLETE, `OPERATOR-CONFIRMED` (2026-09-05)** — `R0007`. **M2.2 — local
  whisper.cpp STT adapter + explicit PL/EN language strategy — COMPLETE,
  `OPERATOR-CONFIRMED` (2026-09-06)** — `R0008`. **M2.3 — voice →
  `ConversationSession` adapter — COMPLETE, `OPERATOR-CONFIRMED`
  (2026-09-06)** — `R0009`: new `src/nexa/voice_conversation/` package
  (`VoiceConversationAdapter`, `SerialConversationQueue`) feeds M2.2's
  `TranscriptionResult` into the existing, unchanged `ConversationSession`
  — the identical path typed chat uses. Two real-hardware-driven
  fix-and-retest cycles: a conversation-turn concurrency defect (mirroring
  M2.2's STT concurrency fix one layer up — FIFO, max 1 turn in flight,
  confirmed live) and a response-language mirroring defect (a fresh English
  session answered in Polish) whose first fix caused its own latency
  regression (broke Ollama/llama.cpp prompt-prefix caching) — both found
  live, fixed, and reconfirmed before PASS. No TTS/barge-in yet.
  **M2.4 — streaming local Piper TTS (Piper HTTP + Pipecat) — COMPLETE,
  `OPERATOR-CONFIRMED` (2026-09-06)** — `R0011`: new `src/nexa/tts/`
  (external Piper HTTP process boundary) + `src/nexa/voice_tts/`
  (`AssistantSpeechBridge`, `TtsStatusObserver`, `voice_for_language`,
  preflight, turn-timing) feed M2.3's streamed assistant text into
  Pipecat 1.8.1's `PiperHttpTTSService` + built-in SENTENCE aggregation +
  `LocalAudioOutputTransport`; PL/EN voice chosen by the canonical M2.3
  language function. Two real-hardware findings, fixed before PASS:
  (1) a self-conversation loop (the reSpeaker heard NeXa's own TTS and
  whisper.cpp re-transcribed her answer as new user turns) — fixed with a
  **temporary half-duplex self-echo gate** (`HalfDuplexGate` +
  `_MicGateFrameProcessor`, mic audio withheld before VAD/STT while real
  TTS playback is active; not barge-in — that is M2.5); (2) output routed
  to the reSpeaker's own alias after a USB replug — fixed to independent
  by-name selection (`LocalAudioConfig.output_device_name = "usb_speaker"`
  = the dedicated USB DAC's stable ALSA alias; input stays `"respeaker"`).
  Plus a probe turn-timing instrumentation fix. No barge-in.
- **Substage M2.4B — Natural Speech Flow / Streaming Pacing — COMPLETE**
  (2026-09-09). Every sub-stage landed and the operator confirmed normal
  bilingual live voice operation (M2.4B.5 / .5A, 2026-09-08); M2.4B.5B
  corrected the response-language semantics (R0027). **No M2.4B blocker
  remains.** The one outstanding item — a B.3.6 operator latency
  re-confirmation (STT latency + END_OF_TURN→first-audio) — is explicitly
  **non-blocking** and does not gate M2.5. Research/design was frozen at
  `f8c3964` (`R0012`). Sub-stage history:
  **M2.4B.1 — realtime speech-flow instrumentation / gap profiler:
  IMPLEMENTED** (`R0013`, `509da2d`): `src/nexa/voice_tts/metrics.py` +
  `apps/nexa_voice_tts_probe.py --report` — measure-only. **M2.4B.1A —
  CPU contention spike + metric-accuracy fixes: DONE** (`R0014`,
  `42591f5` + `24d88ee`): intra-response silence is primarily LLM text
  starvation under CPU contention; Piper on 1 Pi 5 core stays ~2× realtime;
  `renice +10` on the Piper process is the recommended (not-yet-shipped)
  scheduling fix; true Piper HTTP timing + `buffer_drain_to_stop_lag_s`
  corrected. **M2.4B.2 — Polish-aware speech planner + TTS-only
  normalisation + M2.4B.2A LaTeX/truncation-tail fix: COMPLETE,
  `OPERATOR-CONFIRMED` (2026-09-07)** — `R0015` + `R0016`.
  `src/nexa/voice_tts/speech_planner.py` (`NexaSpeechPlanner` between the
  bridge and `PiperHttpTTSService`, one natural-phrase `AggregatedTextFrame`
  at a time; Polish/EN abbreviation-aware boundaries; Markdown/list → prose;
  conservative abbreviation expansion; `tzw.` not expanded; `_strip_math`
  for inline LaTeX; `_tidy_spoken` trims a dangling `"("` from a
  `num_predict`-truncated reply, mid-word partials kept = transcript
  truth). No pacing. Transcript invariant proven (`ast` + behaviour).
  Operator verdict on the fresh mic run: *"The spoken response itself is
  good if we ignore the pauses"* — B.2/B.2A confirmed; `$\text{H}$` never
  reached Piper (`"wodoru (H) i helu (He)"`), `tiny_text_chunk_count = 0`,
  true Piper RTF ~0.27. **M2.4B.3.1 — Piper CPU priority + TTS context
  continuity: IMPLEMENTED** (`R0017`): the external Piper process starts at
  `nice +10` (`PiperHttpConfig.nice`, no sudo, NeXa's own process untouched,
  `llama-server` untouched); Pipecat `stop_frame_timeout_s` raised 3 s → 8 s
  (`DEFAULT_TTS_CONTEXT_TIMEOUT_S`) so a short inter-phrase stall keeps one
  speaking context. No refill controller. **M2.4B.3.2A — speech rate
  budget: RESEARCH DONE** (`R0018`, offline sim
  `docs/research/m2_4b_speech_flow/rate_budget_sim.py`):
  **`realtime_text_ratio ≈ 0.53`** — `gemma4:e4b` produces spoken text at
  ~53 % of the rate `pl_PL-gosia-medium` consumes it (8.5 vs 15.5
  chars/s). This is a hard constraint: no finite steady-state buffer makes
  an arbitrarily long reply continuous; a prebuffer only relocates silence
  to the front (`wall_to_finish` invariant); batching cannot move the
  first underrun (can't synthesize text the LLM hasn't generated);
  `length_scale ≤ 1.10` closes ≤ 11 % of the deficit. **M2.4B.3.2 —
  small speech continuity controller: IMPLEMENTED** (`R0019`,
  `src/nexa/voice_tts/continuity.py`): `NexaSpeechContinuityController`
  between the planner and `PiperHttpTTSService` — phrase 0 always
  immediate (no prebuffer); phrases 1..N released the moment the
  ESTIMATED audio reserve (`Σ produced-audio-s − wall-since-first-audio`,
  fed by the downstream observer's real byte counts) is below a target
  (`DEFAULT_CONTINUITY_TARGET_S = 2.0`, candidate A/B 1.5/2.0/2.5), held
  ≤ 0.4 s only while healthy; never batches-to-grow, never adds silence,
  never touches text/speech-rate. B.3.1's `nice +10` + `stop_frame_timeout_s
  = 8` unchanged. **Offline replay of the R0018 timelines: the controller
  never makes first-audio later / the first underrun earlier / total
  silence higher, and on those (LLM-behind) turns it changes nothing —
  it CANNOT fix the 0.53 rate deficit.** Its value is proven
  no-regression + the metrics seam + correct bounded behaviour for the
  genuinely short-and-fast reply. **M2.4B.3.3 — conversational voice
  response policy: IMPLEMENTED** (`R0020`,
  `src/nexa/conversation/response_mode.py`): new `ResponseMode.{TEXT,
  VOICE}` `StrEnum` + `voice_response_directive()`. `ConversationSession.
  send(..., response_mode=TEXT)` threads it to
  `ConversationContext.to_provider_messages(..., response_mode=…)`, which
  for `VOICE` inserts ONE constant bilingual `system` message at a fixed
  index (right after the persona) — "answer directly, ~1–3 sentences for
  an ordinary question, short first sentence, no lecture/list by default,
  BUT honour an explicit request for detail/steps/list/comparison/more".
  `VoiceConversationAdapter` defaults to `VOICE`; typed chat stays `TEXT`
  = byte-for-byte pre-B.3.3. Same session/history/persona/model; the
  directive is NEVER stored in history; it does NOT decide the response
  language (the per-turn `language_directive` still does); it is a
  generation policy, not truncation (`num_predict` unchanged). Follows the
  R0009 KV-cache discipline (constant string, fixed position). Hardware
  A/B (`b33_ab.py`): the four ordinary questions asked *without* "krótko"
  → `2/2/3/2` sentences under `VOICE` (was `2/2/5/11` under `TEXT`; two
  markdown lectures/lists became flat prose), mean answer chars 395→186;
  "wyjaśnij dokładnie" still answered in 5 sentences/522 chars, uncut;
  first-audio equal-or-better on 4 of 5 (Q-B −8.0 s, Q-E −16.4 s);
  ordinary intra-response gaps 41.6 s→≤5 s. **`realtime_text_ratio ≈
  0.53` is unchanged** — a long answer still gaps; this stage is reply
  *shape*, not rate. **M2.4B.3.4 — local LLM serving & voice performance
  benchmark: DONE, research only** (`R0021`, `docs/research/m2_4b_llm_bench/`;
  **no production model change**). Measured `gemma4:e4b` under the real
  B.3.3 voice path: PL ~9.4 generated chars/s (~59 % of R0018's 15.9
  target), EN ~14.7 (~92 %); PL/EN throughput ratio ≈ 0.64; ~3.1 tok/s;
  warm-prefix TTFT ~2.6 s, cold-prefix ~29 s, cold load ~33 s, ~10.2 GB
  resident; never throttled. Serving: `num_thread=2` = +14 % tok/s
  (per-request Ollama option, no sudo; still ~85 % of PL target);
  `num_batch`/`num_ctx` inert. `gemma4:e2b` is the only viable faster
  model (~2× — PL ~18.7 chars/s clears target, warm TTFT ~1.2 s, 7.5 GB,
  same family/persona/voice-policy) **but has a repeatable quality
  regression** (factual self-contradictions, a hallucination, one EN→PL
  break, thinner reasoning). qwen3/3.5:4b same speed + garbled PL;
  qwen2.5:3b/llama3.2:3b fast but word-salad PL; Bielik 2 tok/s / empty
  completions. **No model beats `gemma4:e4b` without quality loss** —
  recommend operator A/B blind `e4b` vs `e2b`; `num_thread=2` + warm-keep
  are free wins regardless. Also fixed separately (`c64b66b`): continuity
  `_hold_then_release` self-cancel log warning + 2 regression tests.
  **M2.4B.3.5 — operator-blind model A/B: COMPLETE, CLOSED 2026-09-08**
  (`R0022`). Mapping revealed — A = `gemma4:e2b`, B = `gemma4:e4b`; the
  operator ran A pl+en / B pl+en and **chose `gemma4:e4b`** (quality over
  `e2b`'s ~1.6–2× speed; EN "idealny"; PL slower but accepted; bilingual
  kept; `e2b` not routed to). Verbatim operator observations only, no 1–5
  scores. **M2.4B.3.6 — Production Local Model Serving Freeze: COMPLETE,
  PASS** (`R0023`). On the ONE canonical path, `gemma4:e4b` kept as
  `DEFAULT_LOCAL_MODEL`: `num_thread=2` + `keep_alive=30m` are now
  one-time provider policy in `nexa.config`
  (`DEFAULT_LOCAL_NUM_THREAD` / `DEFAULT_LOCAL_KEEP_ALIVE`, env-overridable)
  wired through `LocalModelProvider` (extra Ollama option only; no
  `taskset` / LLM `renice` / RT sched — Piper stays `nice +10`) +
  `build_default_session()`; new `nexa.bootstrap.warm_up_session()` primes
  the persona/`ResponseMode.VOICE` KV prefix at startup (empty history,
  `num_predict=1`, output discarded — never enters history, no second
  authority, `ModelUnavailableError` visible). Measured
  (`b36_serving_freeze_probe.py`, eviction-controlled): cold first real
  voice turn TTFT ~76.7 s → with warm-up ~3.2 s (`prompt_eval` 43.3 s →
  3.2 s; 507-tok prefix from KV cache) — **~73 s saved, not a no-op**;
  `num_thread=2` Piper-active decode ~1.74 → ~2.85 tok/s (+64 %), Piper
  unaffected, no throttle. `ResponseMode.TEXT` byte-for-byte unchanged,
  `VOICE` policy unchanged, PL/EN language mechanism unchanged, no `e2b`
  routing / per-language model / fallback model. +20
  `tests/test_production_serving_freeze.py`. Separate flagged tracks
  (deferred, NOT in B.3.6): Polish throughput (~11 vs 15.9 chars/s target);
  Polish STT accuracy (whisper.cpp `base`/`q8_0`); bilingual auto-STT /
  code-switch; `keep_alive` = infinite residency (resource-policy
  decision). **M2.4B.4 — Bilingual Voice Input Research & Benchmark:
  COMPLETE (research + decision, NO production change)** — `R0024`. Real
  50-utterance operator corpus + `whisper.cpp v1.9.3 base/q8_0` benchmark
  (4 strategies, 200 runs). PL↔EN never confused (0/30); `-l auto` LID
  90 %, all 3 misses low-confidence third-language slips; `-l auto`
  transcript == explicit baseline when the label is right; `-l auto`
  +~1.1 s/turn, `-dl`→explicit +~1.3 s and no gain (rejected). Decision:
  future bilingual input = guarded `-l auto` (PL/EN + confidence gate +
  inherit-previous-input-language fallback), response-language a *separate*
  resolver. **ADR-0003 D5: PARTIALLY supersede** (per-utterance PL↔EN
  switching OK; isolated shorts + within-utterance code-switch deferred);
  proposed D5 amendment recorded, ADR not edited. No stronger-model
  download (PL S2 rate 13 %/20 % is a pre-existing `base/q8_0` issue —
  separate track, candidate `large-v3-turbo-q5_0` named for a future
  approved benchmark). **M2.4B.5 — Automatic Bilingual PL/EN Voice Input:
  IMPLEMENTED, live operator acceptance PENDING** — `R0025`, `ADR-0003
  Amendment 1`. `nexa.stt.{WhisperCppLanguageDetector (ctypes → pinned
  libwhisper.so, p_pl/p_en), LanguageIdGuard (threshold 0.60 = R0024
  calibration, configurable; 2.0 s duration floor), BilingualSpeechTranscriber
  (LID→guard→ONE explicit PL/EN decode; wrong-lang transcript never
  generated; holds transient last_input_language)}` +
  `nexa.conversation.ResponseLanguageResolver` (separate authority; mirror
  input by default; explicit request → sticky pref; narrow detector, no
  false-switch on content mentions) threaded via
  `ConversationSession.send(response_language=…)` (replay-stable, R0009;
  TEXT unchanged); TTS voice from ResponseLanguage. Headless latency
  ~2.83 s mean / ~+1.13 s vs explicit, flat. Mixed = BEST EFFORT /
  DEFERRED. +39 tests. **OPERATOR-CONFIRMED (2026-09-08)** for normal
  bilingual PL/EN live voice (auto switching, STT ~2.89–3.21 s, no
  slowdown, queue depths 0). The B.5 session also exposed a stability bug
  → fixed in **M2.4B.5A**; the one-turn-vs-sticky semantics were corrected
  in **M2.4B.5B / R0027**.
  **M2.4B.5A — Live Voice Stability / Backlog Investigation: ROOT CAUSE
  FOUND + FIXED, regression-tested; OPERATOR-CONFIRMED for normal post-fix
  operation; TV-stress test NOT PERFORMED / operator waived** —
  `R0026`. `HalfDuplexGate` left the mic OPEN through the
  think/generate/TTS-synth window (~10–25 s); TV audio there backlogged
  `SerialTranscriptionQueue` + `SerialConversationQueue` and, under
  `gemma4:e4b`+Piper CPU contention (STT 3.19 s→13.16 s measured, ~4.1×;
  `whisper-cli` max concurrency 1, no overlap; no detector leak), NeXa
  "hung". Fix (strict pre-M2.5 half-duplex, NOT barge-in):
  `HalfDuplexGate.response_in_flight` covers the whole response;
  `_UtteranceCaptureFrameProcessor` + `VoiceConversationAdapter` DROP
  busy-period audio/results with `DROP_BUSY_RESPONSE_IN_FLIGHT` telemetry
  (never queued, history untouched); new `stt_queue_depth` /
  `conversation_queue_depth` / `dropped_busy_*` telemetry. Post-fix clean
  PL↔EN: 2.81 s mean (no regression). +14
  `tests/test_bilingual_voice_stability.py`; 2 existing test files updated
  for the intended change. `gemma4:e4b` / `num_thread=2` / `keep_alive=30m`
  / warm-up / `ResponseMode` / Piper / planner / continuity / whisper
  model / guard thresholds **unchanged**.
  **M2.4B.5B — Response Language Override vs Sticky Preference: DONE** —
  `R0027`. `ResponseLanguageResolver` corrected: a **one-turn override**
  ("Answer in English.", "Odpowiedz po polsku.") affects **this reply
  only** and does **not** mutate `ResponsePreference`; a **sticky
  command / switch** ("From now on speak English.", "Od teraz mów po
  angielsku.", "Wracamy do polskiego.", "Switch to English.") sets the
  sticky preference; content mentions never change anything. New
  `detect_language_request → LanguageRequest(language, kind)`;
  `ResponseLanguageDecision.request_kind`. 3 concepts distinct
  (InputSpeechLanguage / ResponseLanguage / ResponseLanguagePreference).
  +18 `tests/test_response_language_override_vs_sticky.py`; `pytest` 587 /
  `unittest` 594; ruff + diff-check clean. Nothing else moved.
  **→ M2.4B is COMPLETE (2026-09-09); no blocker remains.**
- **Current objective:** **M2.6 — Cloud Realtime Voice.** LOCAL VOICE is
  closed: M2.5A (`R0028`) + **M2.5B (`R0029`) COMPLETE / OPERATOR-CONFIRMED
  (2026-09-10)**, frozen (`gemma4:e4b` / `num_thread=2` / `keep_alive=30m`
  / warm-up / `ggml-base-q8_0` `-t 4` / `LanguageIdGuard` /
  `ResponseLanguageResolver` / Piper / SpeechPlanner / continuity /
  `ProviderWindow keep_entries=0` / `bargein_enabled` default-off). **M2.6
  research / architecture COMPLETE (`R0030`, 2026-09-10)** — provider
  frozen to Gemini Live `gemini-3.1-flash-live-preview`, Pipecat OPTION C,
  one `ConversationSession` + `RealtimeVoiceProvider` + `CloudContextSnapshot`,
  HYBRID audio, `ConversationPolicy` vs `active_provider`. **Phase-0
  fact corrections applied** (R0030 *PHASE 0 CORRECTIONS*: UK/EEA/CH data
  terms; 20–40 ms chunks; 2 h resumption tokens; official pricing;
  Polish-accent retagged as an unverified community report; a critical
  input-transcription-vs-audio-ordering question for M2.6A to measure —
  **[since answered, see C6 below and ADR-0004 Decision F]**).
  **M2.6A CLOUD REALTIME VOICE FEASIBILITY — PASS / OPERATOR-CONFIRMED
  2026-09-10** (`R0031`). **VOICE `Sulafat` — OPERATOR-CONFIRMED** (real
  natural conversation → *"I like this voice, we keep it."*), frozen cloud
  voice baseline. Attempt #1 infra bug (missing `LLMRunFrame` kickoff)
  fixed; operator retests = real PL/EN conversations judged EXCELLENT,
  latency essentially immediate; machine (turn-local) EOT→first-audible
  ≈0.75–0.81 s median (R0030 ≤1.5 s PASS), barge-in ≈2 ms (R0030 ≤100 ms
  PASS), AEC 0 failures, #5465 startup-window only. "1.93 s / 19.84 s
  outliers" were metric artifacts (fragmented speech + cross-turn
  pairing), fixed by turn-local reconstruction. **C6 (turn-local, Sulafat
  session):** RAW input transcription precedes first audio 3/3 valid turns
  (~0.5 s), PUSHED 2/3 — but timing headroom ≠ steerability; ADR-0004
  defaults to Gemini native mirroring (Option A), delayed-`activity_end` +
  local language-ID as the measured Option B.
  **`ADR-0004` — Cloud Realtime Voice provider boundary — WRITTEN and
  Accepted (2026-09-10)**
  (`docs/decisions/ADR-0004_cloud_realtime_voice_provider_boundary.md`).
  Decided A–P: one `ConversationSession` authority (local + cloud); new
  NeXa-owned `RealtimeVoiceProvider` boundary — a *peer of* `ModelProvider`,
  not a subtype — in a provider-agnostic `src/nexa/realtime/` package, with
  `GeminiLiveProvider` wrapping Pipecat 1.8.1 `GeminiLiveLLMService`
  (Option C); `CloudContextSnapshot` = minimal role card + language
  preference + last ~12 turns + policy state, fresh per non-resumed
  session, never full history / memory / persona / creds / raw audio;
  `ConversationPolicy` (`LOCAL_ONLY` **default**, `CLOUD_PREFERRED`, `AUTO`
  provisional — no classifier in M2.6B) vs runtime `active_provider`
  (`LOCAL`/`CLOUD`), NeXa's `ConversationRouter` executes every switch;
  language routing Option A (native mirroring; NeXa owns the preference
  permanently), Option B is the measured fallback; voice = provider-agnostic
  NeXa user preference → `Sulafat`; Pipecat #5465 handled by a NeXa-owned
  bounded inbound audio buffer at the boundary (not an upstream fork);
  `ReconnectController` (proactive age-timer + `GoAway` + resumption + fresh
  snapshot on resumption failure); explicit failure→fallback table
  (`LOCAL_ONLY` never leaves local; others fall back to `LOCAL` with the
  user informed; continuity survives); credential surface = XDG secret file
  `~/.config/nexa/secrets/gemini.env` (700/600) + env-var-first loader +
  `CredentialSource` seam; **[Amendment 1]** eligibility is a deployment
  policy `ProviderEligibilityPolicy(distribution_mode, billing_verified)`,
  not a key property — `DEVELOPMENT` mode may use the project's available
  Gemini API tier, including Free Tier where available (the UK's is), and
  is not blocked solely because billing is disabled;
  `DISTRIBUTED` to EEA/CH/UK users requires verified active billing (Google
  terms — stated as terms, not a legal opinion);
  `google-genai>=2.22,<3` + `websockets>=15,<17` as an **optional
  `cloud-gemini` extra** (`pip install .` stays Google-free; lazy import in
  `src/nexa/realtime/gemini/`); `ProviderUsageEvent` from `usageMetadata`;
  Gemini function calling never the capability authority (event types
  defined, OFF in v1); Pipecat owns media/WS mechanics only. The ADR carries
  the ordered **M2.6B implementation plan (15 components)** and 19
  measurable **M2.6B acceptance gates**; local voice stays byte-for-byte
  frozen; real-hardware operator acceptance required before M2.6B COMPLETE.
  No `src/nexa/**` / `tests/**` / `pyproject.toml` change in the ADR task.
  **`M2.6B` — IN PROGRESS. `M2.6B.1` — provider-agnostic foundation:
  IMPLEMENTED (`R0032`, 2026-09-11)** — new `src/nexa/realtime/` package
  (`RealtimeVoiceProvider`, `ConversationPolicy` + `ProviderEligibilityPolicy`,
  `CloudContextSnapshot`, `InboundAudioBuffer` for #5465, usage telemetry,
  a deterministic `ReconnectController`, Gemini credential/voice mapping —
  **zero cloud SDK import**); a mandatory Gemini/Pipecat startup-sequencing
  source audit against the installed `pipecat-ai==1.8.1` +
  `google-genai==2.22.0` **confirmed, did not contradict, ADR-0004/
  Amendment 1** (Pipecat already implements the initial-history seed and
  the resumable-handle-only rule internally — see
  `docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md`).
  **`M2.6B.2` — `GeminiLiveProvider` + canonical cloud-turn integration:
  IMPLEMENTED (`R0033`, 2026-09-11)** — `src/nexa/realtime/turn.py`
  (`CloudTurnAccumulator`), `turn_framing.py` (`UtteranceFramer`),
  `router.py` (`ConversationRouter`), `gemini/service.py`
  (`GeminiLiveProvider`, proven against the **real** Pipecat pipeline /
  worker / aggregator machinery, no cloud call); additive
  `ConversationSession.record_external_exchange`. `cloud-gemini` optional
  `pyproject.toml` extra added. +57 new tests, **870 total, 0
  regressions**; zero non-additive `src/nexa/**` change; no live Gemini
  connection; **reconnect not yet wired into `GeminiLiveProvider`** (known
  gap, explicit).
  **`M2.6B.2A` — pre-hardware cloud-turn / reconnect hardening:
  IMPLEMENTED (`R0034`, 2026-09-11)** — fixed a real silent-user-speech-
  loss bug (mid-turn readiness loss: a turn that started live and then
  lost readiness had its further audio rejected as an orphan and its
  `activity_end` silently swallowed) with a deterministic
  abort-and-restart policy (never silent loss, never duplicated audio; a
  connectivity hiccup mid-utterance may split it into two utterances from
  Gemini's perspective — a documented trade-off, not data loss); completed
  the provider->NeXa event map (interim transcription, a proper
  `GenerationCompleteEvent`, error frames) which exposed and fixed a
  second gap (Pipecat's `push_error()` pushes upstream; the only tap was
  downstream — fixed with a second `up_tap`); closed the "manual
  injection" test gap with `ConversationRouter.handle_provider_event()`;
  tested 5 turn-ordering/interruption permutations (found and fixed a
  third gap: a late assistant-transcription delta after interruption was
  still being appended); proved one permutation (turnComplete before a
  delayed final transcription) impossible from the installed Pipecat
  source; made the fresh-snapshot-on-resumption-failure mechanism precise
  (destroy-and-recreate, never leaks stale context) and tested it. +16
  tests, **886 total, 0 regressions**. Still no live Gemini connection;
  reconnect still not wired to a real socket (explicit, deferred to
  M2.6B.3).
  **`M2.6B.3` (`R0035`), `M2.6B.3A` pre-live hardening (`R0036`) and
  `M2.6B.3B` interrupted-history safety (`R0037`) since IMPLEMENTED — see
  "Latest report" above. Next: the real Gemini/hardware operator
  acceptance run, NOT YET RUN.**
  **Credential:** operator-provided key stored at
  `~/.config/nexa/secrets/gemini.env` (outside the repo, 700/600), var
  `NEXA_GEMINI_API_KEY`. Non-blocking, owed independently: the B.3.6
  operator latency re-confirmation; a resource-safe non-blocking pre-warm to
  remove the M2.5B.2 reset continuity dip.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project; foundation tests pass
  (`python -m unittest discover -s tests`).
- Documentation + ADR + report systems in place (`R0001`–`R0011`; `ADR-0001`,
  `ADR-0002` + its M1.0B amendment + Amendment 2, `ADR-0003`).
- **M2.5B — production barge-in / interruption complete, `OPERATOR-CONFIRMED`
  (2026-09-10)** (`R0029`). Behind `LocalAudioConfig.bargein_enabled`
  (default `False` = byte-for-byte R0026; probe `--bargein`). **Accepted
  LOCAL VOICE baseline:** audio input → Pipecat local transport → Silero
  VAD → whisper.cpp `base/q8_0` → bilingual PL/EN guard → `ConversationSession`
  → `ProviderWindow` → `gemma4:e4b` via Ollama → `NexaSpeechPlanner` →
  Piper → audio output. **Production barge-in:** XVF3800 AEC far-end
  reference (TTS PCM teed to `plug:respeaker` so the mic stays hot safely);
  sustained-VAD (≥ 300 ms, no intervening stop) interruption confirmation;
  responsive Ollama cancellation (`cancel → worker-stop` ~251 ms);
  interruption capture/coalescing (one interruption = one canonical turn,
  multi-segment safe); **capture-generation-scoped timers** (`_active_capture_id`
  + `_capture_stale(cid)`; each settle / hard-cap timer holds its
  `capture_id` by value and goes inert once superseded; one
  `_end_capture_phase(abandon=…)` on every exit incl. the forced
  `INTERRUPTING → RESPONDING`); correct interrupted-history semantics
  (CASE A rollback / CASE B `interrupted=True` prefix); PL/EN
  response-language routing preserved across an interruption; no stale
  audio resumes; a segment that began during `INTERRUPTING` is never
  busy-dropped. If the AEC feed cannot start or dies, barge-in disables
  itself for that response and the mic falls back to R0026 suppression —
  loudly (telemetry), never a silent unsafe hot mic. **ProviderWindow
  (M2.5B.2):** canonical `ConversationSession.history` stays complete;
  provider-facing context is a prefix-stable bounded window;
  `keep_entries=0` context reset at the boundary; no permanent
  long-session KV-cache cliff; reset turns ~ordinary latency in the
  shipped `keep=0` config; real-Pi 112-turn / 10-reset benchmark retained.
  The LLM (`gemma4:e4b`) is a **replaceable conversation / reasoning
  engine**, not NeXa's identity. Live acceptance 2026-09-10: ~7-minute
  continuous `--bargein` session, repeated / nested interruptions, zero
  `12.0s cap` / `15.0s timeout` / `DROP_BUSY_RESPONSE_IN_FLIGHT`, all
  telemetry clean. `pytest` 732 / `unittest` 739 / `ruff` clean.
- **M2.5A — barge-in / interruption architecture & real-hardware
  feasibility complete, `OPERATOR-CONFIRMED` (2026-09-09)** (`R0028`).
  No `src/` change. Established the AEC far-end-reference prerequisite,
  the Pipecat 1.8.1 interruption capability audit, and the media-stop
  latency (VAD start → playback task stopped 28.5 ms mean / 37.4 ms max).
- **M2.4 — streaming local Piper TTS complete, `OPERATOR-CONFIRMED`**
  (`R0011`; `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md`):
  `src/nexa/tts/` (external `python -m piper.http_server` process on
  `127.0.0.1:5001`, its own venv `~/.local/share/nexa/tts/piper-http-venv/`,
  GPL `piper-tts` never imported into NeXa's process — `ast`-verified;
  `pyproject.toml` unchanged) + `src/nexa/voice_tts/` (`AssistantSpeechBridge`
  translates M2.3's `on_assistant_token`/`_complete` callbacks into
  Pipecat's `LLMFullResponseStart`/`LLMTextFrame`/`LLMFullResponseEnd`
  vocabulary in FIFO; `TtsStatusObserver` reports only real
  `TTSStarted`/`AudioRaw`/`Stopped`/`Error` frames; `voice_for_language`
  maps the **canonical** `nexa.conversation.language.detect_response_language`
  result to `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium` — the exact
  prior-assistant voices, byte-identical files, config-json inference
  defaults). Pipecat 1.8.1 `PiperHttpTTSService` (configured with the
  `/synthesize` URL) + built-in SENTENCE aggregation +
  `LocalAudioOutputTransport` (auto-resamples 22050→16 kHz) reused as-is,
  no patch. Output is now the **dedicated USB DAC** (`UACDemoV1.0`,
  `usb_speaker` alias → `hw:CARD=UACDemoV10`), selected **independently**
  of the reSpeaker input (`respeaker` alias), both by name, no shared
  index, no silent fallback. **Two real-hardware findings fixed before
  PASS**: (1) self-conversation loop — the reSpeaker heard NeXa's own TTS
  and whisper.cpp re-transcribed fragments of her answer ("Saturny nie
  jest czarną dziurą.", "Masz rację.", …) as new user turns; fixed with a
  **temporary half-duplex self-echo gate** — `HalfDuplexGate`
  (event-backed boolean, driven by real
  `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` + the bridge's
  response-lifecycle notifications, no timers, no fake `VoiceState`) +
  `_MicGateFrameProcessor` (right after `transport.input()`, drops
  `InputAudioRawFrame` before VAD/STT while suppressed; multi-sentence
  latch never reopens the mic between chunks of one reply). NOT barge-in —
  nothing is cancelled, the user cannot interrupt NeXa mid-reply; M2.5
  replaces it. (2) Output routed to the reSpeaker's own alias after a USB
  replug re-enumeration; fixed to independent by-name selection.
  Plus a genuine probe **instrumentation** fix (`TurnTimingTracker` —
  per-turn FIFO-correlated latency record; `first_tts_audio_at` /
  `first_sentence_ready_at` written once per response). 91 new passing
  tests + 3 opt-in live-Piper skips, 11 new test files. No barge-in —
  `ast`-verified. Operator-confirmed real conversation: full local voice
  loop works, NeXa no longer talks to herself, context preserved,
  responses coherent. Known follow-up: natural-speech-flow / pacing
  (`M2.4B`, not started).
- **M2.3 — voice → `ConversationSession` adapter complete, `OPERATOR-CONFIRMED`**
  (`R0009`; `docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md`):
  new `src/nexa/voice_conversation/` — `VoiceConversationAdapter` (owns no
  history/context/persona/model of its own, `ast`-verified) feeds M2.2's
  `TranscriptionResult` into the existing, unchanged `ConversationSession`;
  `SerialConversationQueue` mirrors M2.2's STT-queue design one layer up
  (FIFO, non-blocking `submit()`, ≤1 conversation turn in flight, explicit
  bounded-overflow error). **Two real-hardware findings, found live and
  fixed before PASS**: (1) the initial design could run two
  `ConversationSession.send()` calls concurrently if a second utterance
  finished transcribing while the first was still generating — fixed by
  the FIFO queue above; reconfirmed live (`max concurrent conversation
  turns observed this session: 1`, real fast-second-utterance test).
  (2) A fresh English voice session answered "What is the speed of light?"
  in Polish — the M1.1 persona's all-Polish system prompt biased the model
  too strongly for its own single mirroring sentence to override. Fixed
  with a deterministic PL/EN directive
  (`nexa.conversation.language.detect_response_language`) recomputed after
  every historical user turn inside `ConversationContext.to_provider_messages()`
  — canonical `ConversationSession` policy, applying identically to typed
  and voice input, never the voice adapter's own concern. **A second
  finding inside that same fix**: injecting the directive only for the
  *current* turn (not replayed from history) permanently broke
  Ollama/llama.cpp's prompt-prefix KV-cache reuse the instant it was used
  once — "warm" turns (~2-6s) stayed at ~15-20s for the rest of any session
  that ever used it. Fixed by recomputing the identical directive from
  each turn's own stored (unmodified) text on every rebuild, restoring
  full cache reuse while keeping every turn's language correct — confirmed
  by a direct controlled experiment and real hardware retest (warm turns
  back to ~2-5s, correct PL/EN mirroring preserved through language
  switches). `ConversationTurn`/`session.history` remain the pure,
  unmodified real transcript throughout. Real Polish and English
  multi-turn voice conversations (with follow-up questions preserving
  cross-language context) both operator-confirmed. No TTS, no barge-in.
- **M2.2 — local whisper.cpp STT foundation complete, `OPERATOR-CONFIRMED`**
  (`R0008`; `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md`):
  `src/nexa/stt/` — pinned whisper.cpp `v1.9.3`/`base`/`q8_0` (R0006's
  measured baseline), built/installed outside git via
  `scripts/setup_whisper_cpp.py` into `~/.local/share/nexa/stt/`.
  `WhisperCppTranscriber` (the `SpeechTranscriber` boundary's only
  implementation) owns subprocess invocation (explicit arg list, no
  `shell=True`), JSON parsing, and typed errors — no silent fallback.
  `UtteranceBuffer` adds a pre-roll ring (`PRE_ROLL_MS=500`, justified by a
  192ms theoretical minimum plus a 288-352ms *empirical* measurement against
  real Silero VAD + real fixtures — the empirical number, not the
  theoretical one, drove the choice). `Language` is a strict `pl`/`en`
  enum with no `AUTO` member — auto-detect is structurally impossible to
  select (R0006 measured it misdetecting Polish as Japanese).
  **Two real hardware findings, found live and fixed before PASS**: (1) the
  first implementation could run two whisper.cpp subprocesses concurrently
  if a short utterance followed quickly — fixed with
  `SerialTranscriptionQueue` (FIFO, ≤1 execution at a time, non-blocking
  `submit()`, explicit bounded-overflow error, no orphan task on shutdown);
  reconfirmed live afterward (`max concurrent STT executions observed this
  session: 1`). (2) `apps/nexa_stt_probe.py` printed a fake `LISTENING` line
  from the transcription callback — fixed so only the real
  `VoiceStateMachine` event stream ever prints a state line. Real reSpeaker
  hardware verified: Polish and English phrases transcribed correctly with
  **no first-word truncation** in any case (the pre-roll's explicit
  acceptance condition). Same-corpus regression against R0006's exact 12
  fixtures: 0.3542 avg WER / 1.648s avg latency / 220.5MB avg peak RSS — no
  meaningful regression vs. R0006's ~0.354/~1.69s/~221MB. 4 threads
  (R0006's measured baseline), zero throttling. 54 new tests (52
  deterministic + 2 opt-in live-whisper.cpp), all passing. No LLM, no
  `ConversationSession` adapter, no TTS, no barge-in — `ast`-verified, not
  just asserted (ADR-0003 M2.2 scope).
- **M2.1 — local audio + Silero VAD foundation complete, `OPERATOR-CONFIRMED`**
  (`R0007`; `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`):
  `src/nexa/voice/` — Pipecat (`pipecat-ai[local]==1.8.1`, current
  non-deprecated `PipelineWorker`/`WorkerRunner` API) local audio
  transport + Silero VAD → a NeXa-owned `VoiceStateMachine`
  (`IDLE`/`LISTENING`/`USER_SPEAKING`/`END_OF_TURN`/`ERROR`). Real reSpeaker
  XVF3800 hardware verified (mono/16kHz via its own ALSA `plug:` alias;
  discovered the system-wide default *output* device isn't currently
  connected — documented, not silently routed around). VAD endpointing
  required real retuning: the library default (`stop_secs=0.2`) measurably
  split one natural Polish sentence into 3 turns on real hardware; a
  deterministic offline calibration (real speech + real inserted silence
  gaps against the actual `SileroVADAnalyzer`, `docs/research/m2_1_vad_calibration/`)
  found `stop_secs=1.0` is the smallest value that holds 0.4/0.6/0.8s pauses
  as one utterance; a live hardware retest confirmed it. Idle/listening
  footprint: ~120 MB RAM, ~6.5% of one CPU core, no throttling. 31 new
  tests (30 deterministic + 1 opt-in hardware), all passing — verified
  precisely against the pre-M2.1 commit's 33-test baseline, not assumed
  (`R0007` "M2.1 DOCUMENTATION CHECK" corrects an earlier arithmetic
  error). No STT, no LLM call, no TTS, no `ConversationSession` —
  `ast`-verified, not just asserted (ADR-0003 M2.1 scope). Audio output was
  verified only at the stream open/close/write level — no audible sound was
  played or confirmed by the operator in M2.1 (accurate as of this
  correction; unaffected by M2.2, which does not need audio output).
- **M2.0A voice feasibility spikes complete** (`R0006`;
  `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md`): real whisper.cpp benchmark
  against the exact same 12 legacy PL/EN audio fixtures faster-whisper was
  scored on — whisper.cpp's `base` model beat legacy's faster-whisper `base`
  on **both** accuracy and speed even at a matched beam size (0.354 avg WER
  at 1972 ms vs. legacy's 0.537–0.558 avg WER at 3486–3575 ms); `q8_0`
  quantization cost no accuracy at ~23% less latency. Auto-language-detection
  reproduced legacy's exact known failure (misdetects one Polish sentence as
  Japanese) — confirmed independent of STT engine, an explicit language hint
  is required. A real resource-budget test (VAD + whisper.cpp + the frozen
  `gemma4:e4b` + Piper TTS, 8 stages) found no RAM/swap/thermal ceiling
  (min free RAM ~3.8 GB, swap <70 MB, zero throttling, peak 64.8°C) but
  reproduced and precisely quantified legacy's CPU-contention failure mode
  under naive full concurrency (VAD ~73×, STT ~4.75×, TTS ~2.9×,
  LLM time-to-first-token ~2.9× slower; LLM steady-state tok/s barely moved,
  ~2%). Classified **`LOCAL_FEASIBLE_WITH_TUNING`** — local full pipeline is
  viable provided the M2 architecture sequences the pipeline instead of
  running everything concurrently. `gemma4:e4b` unchanged. NVIDIA
  Parakeet/Canary's license (CC-BY-4.0) and Polish support were confirmed
  from the primary model card (both `UNKNOWN` before); hardware path
  plausible but not benchmarked (would need a multi-GB conversion,
  correctly deferred).
- **M2 open-source-first research complete** (`R0005`;
  `docs/research/M2_REALTIME_VOICE_RESEARCH.md`): deep-dived pipecat-ai/pipecat
  and livekit/agents source code (not just docs) against the requirement that
  `ConversationSession` stay the canonical brain — both frameworks have a
  clean, confirmed integration seam (`Agent.llm_node`/custom
  `FrameProcessor`) that can delegate straight to it. License review caught a
  real risk by unpacking an actual PyPI wheel: LiveKit Agents' *default*
  local VAD/turn-detector models carry a proprietary, framework-locked
  license (usable only inside LiveKit Agents) — the open `livekit-plugins-silero`
  (MIT) must be substituted explicitly. Piper's actively-maintained successor
  (`OHF-Voice/piper1-gpl`) is GPL-3.0 (the original MIT `rhasspy/piper` is
  archived) — recommended subprocess-only invocation, not an in-process
  import. Real Pi 5 evidence pulled from the legacy repo (read-only) anchors
  every feasibility claim: legacy's own measured numbers show
  faster-whisper's fast configs (`tiny`/`base`) are too inaccurate for Polish
  (WER 0.54–0.68) while the accurate config (`small`) is too slow
  (35–92 s); Piper's real Polish-voice latency was ~7 s/utterance (much
  slower than generic English-voice benchmarks suggest); and a full
  LLM-answered voice turn took 27.6–54 s end-to-end, with a real CPU-contention
  failure once recorded when other processes shared the same 4 cores. No
  prototype was built or run — see `R0005`'s "UNRESOLVED".
- **M1.1 — Minimal Canonical Text Conversation Path implemented and verified**
  (`R0004`; `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`):
  `ConversationSession` → `ConversationContext` (bounded, deterministic) →
  `ModelProvider` → streamed tokens → appended `ConversationTurn`, in
  `src/nexa/conversation/` + `src/nexa/providers/`. `LocalModelProvider`
  (Ollama) is the first, live-verified implementation, configured for the
  frozen baseline `gemma4:e4b`; `LlamaServerProvider` is a second
  implementation behind the same interface (portability path), verified at the
  interface level only — see limitations below. One versioned persona
  (`configs/personas/nexa_persona_v1.json`). Repo-local `./.venv` created
  (stdlib-only runtime — `pyproject.toml` `dependencies = []` unchanged; `dev`
  extras `pytest`/`ruff` installed into it). 33 deterministic tests (unit +
  fake-HTTP-server integration) pass via both `python -m unittest` and
  `pytest`; `ruff check` clean. One live Ollama integration test (opt-in,
  `NEXA_RUN_LIVE_TESTS=1`) passed against the real `gemma4:e4b`: one Polish and
  one English turn, streamed, history correct, model unloaded after. A manual
  multi-turn conversation through the real `apps/nexa_chat.py` CLI harness
  also verified end to end (real recall across turns, e.g. translating its own
  earlier Polish reply into English on request).
- **`OPERATOR-CONFIRMED` (2026-09-05):** Andrzej personally ran a real
  multi-turn conversation through `apps/nexa_chat.py` → `ConversationSession`
  → `ConversationContext` → `ModelProvider` → Ollama → `gemma4:e4b` (the exact
  canonical path, not a test-only harness) and recorded **"M1.1 HUMAN
  ACCEPTANCE: PASS"** — everything worked correctly and conversation quality
  was satisfactory. This is the human-acceptance evidence tier above the
  agent's own manual-CLI verification recorded in `R0004`; see `R0004`'s
  "Operator acceptance" addendum for the exact record.
- M1.0 research complete: Pi hardware/runtime/model inventory verified;
  legacy conversation stack audited (read-only); external research done;
  local models benchmarked on the Pi.
- **M1.0B complete:** 8 current small models (Qwen3.5 ×2, Gemma 4 ×2, Phi-4-mini,
  Bielik ×2 configs, + the `qwen3:4b-instruct` control) benchmarked on the Pi —
  perf + `CONV-PL-S1` + `CONV-EN-S1` for all; Phase 2 deep-dive
  (`CONV-MIX-1`, `CONV-NATURAL-S1`, `CONV-HONESTY-S1`, persona A/B,
  standardized-vs-recommended, long-context decay, weighted score) for the 3
  finalists. Ollama upgraded 0.30.10 → 0.33.2 for valid Qwen3.5/Gemma 4 runs.
- Throwaway benchmark harness `scripts/m1_bench/` works (stdlib only, no deps);
  raw results under `docs/research/m1_bench_results/` (M1.0B under `.../m1_0b/`).

## What is partial

- M1.0 + M1.0B conversation-quality scores were **`AGENT-ASSISTED`** (Claude vs
  transcripts + rubric). The **operator blind test is done and the baseline is
  frozen** (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md` §4/§8,
  `docs/testing/m1_operator_blind_results/`, `ADR-0002` Amendment 2): Andrzej
  talked blind to all 3 M1.0B finalists (~7–20 turns each), scored them, then
  the mapping was revealed. Result — `OPERATOR-CONFIRMED`: `gemma4:e4b` ranked
  best (4/5, everyday NeXa YES), `gemma4:e2b` second (3/5, maybe), `qwen3.5:2b`
  worst (2/5, no — operator independently caught a live hallucination, matching
  R0003's honesty-probe finding). This **superseded the M1.0B weighted-score
  `PROPOSAL`** (which had favored `gemma4:e2b` 4.0 vs. `gemma4:e4b` 3.9, driven
  by speed/RAM weighting, not a conversation-quality disagreement — see the
  test doc §8). The owner then explicitly signed off on `gemma4:e4b`'s
  RAM/latency cost (~10 GB resident, ~3 tok/s) and it is now **FROZEN** as the
  M1.1 local conversation baseline in `ADR-0002` Amendment 2 (2026-09-05).
- **M1.0B/blind-test model picture (historical vs. final — do not conflate):**
  the **M1.0B weighted-score recommendation** (`R0003`, sweep doc §19,
  `AGENT-ASSISTED`, 2026-09-01/02) was `gemma4:e2b` as the M1.1 baseline
  *candidate*. The **final, operator-confirmed decision** (2026-09-04/05) froze
  **`gemma4:e4b`** instead — see above. Both remain documented: `gemma4:e4b`
  (PL 4/5, EN 4/5) is the frozen baseline / quality leader; `gemma4:e2b`
  (PL 3.5/5, EN 4/5, ~2× faster, flattest long-context decay) is documented as
  the fast/low-RAM alternative; `qwen3:4b-instruct` (PL 3/5, EN 3.5/5) remains
  the reliable incumbent / safe fallback; `qwen3.5:2b` is English-only (broken
  Polish + a honesty-probe hallucination, operator-confirmed). Bielik re-test at
  temp 0.1 + Q4_K_M did **not** flip anything (Q8_0 weak EN / no TTFT warm-up;
  3rd-party Q4_K_M is a reliability failure). Full detail: `R0003`, sweep doc
  §8–§19 (historical M1.0B evidence and ranking — unedited); `ADR-0002`
  Amendment 2 (final decision).
- `llama.cpp`-direct vs Ollama head-to-head **still not measured** — blocked by
  Ollama blob-store permissions (`0700`/`ollama`-owned); needs an operator
  decision. The **same blocker** means `LlamaServerProvider` (built in M1.1)
  has never been run against a real `llama-server` process — no GGUF weight
  file is reachable outside that blob store on this machine. It is verified
  only at the interface/request-shaping level (fake-HTTP-server tests).
- Incumbent `qwen3:4b-instruct` was carried into the M1.0B head-to-head on
  Phase 1 data only (not run through the Phase 2 battery).
- Cancellation (`CancelToken`) is cooperative between received stream chunks;
  it cannot interrupt a token Ollama/llama.cpp is already computing
  mid-inference (neither backend's streaming HTTP API exposes finer-grained
  abort). Documented in `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §6 as a known
  limitation for M2 (voice/barge-in) to account for, not a silent gap.

## What is not implemented (by design)

- **Cloud realtime voice** — not built. Next planned stage after LOCAL
  VOICE closure (see ROADMAP): Google Gemini Live
  (`gemini-3.1-flash-live-preview`) as a **replaceable** conversation
  provider behind NeXa's own router. Modes AUTO / LOCAL ONLY / CLOUD
  PREFERRED, switchable by natural voice command ("Przełącz na chmurę." /
  "Rozmawiaj lokalnie." / "Używaj najlepszego trybu."). The model may
  identify the intent; NeXa's core executes the switch. **Not started.**
- **Natural speech flow / streaming pacing (`M2.4B`)** — not built. M2.4's
  spoken output is per-sentence Piper synthesis with audible gaps between
  chunks and occasional bad phrase-boundary splits (incl. Polish
  abbreviations — Pipecat's NLTK splitter runs English-only). Buffered/
  look-ahead generation and buffer-adaptive pacing are `M2.4B`.
- **Voice-model selection** — `pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium`
  (the prior assistant's voices) are in use. A softer/cozier voice is a
  separate research task after the pipeline is stable, not M2.4/M2.4B.
- Robust context beyond M1.1's bounded window (M3), device awareness /
  capability registry (M4), long-term memory (M5), and everything later —
  not yet researched or decided.
- Model router / `AUTO`/`LOCAL ONLY`/`CLOUD PREFERRED` policy, MAS, tools,
  online model provider — all explicitly out of M1.1 scope (ADR-0002 D1,
  ROADMAP "Later").

## Known problems / notes

- `OBSERVATION`: the login shell's `python3`/`pip` resolve into the **legacy**
  repo's `.venv`. NeXa must always use its own `./.venv`.
- `VERIFIED FACT`: on this Pi, neither the V3D GPU (Vulkan) nor the Hailo-10H
  helps M1 LLM inference — CPU is the only viable path (see research doc §2.2–2.3).
- `VERIFIED FACT`: legacy `smart-desk-ai-assistant/config/settings.json` `/llm`
  block is stale (points at a llama-server + Qwen2.5-1.5B path the MAS does not
  actually use). Legacy is reference-only; not our file to fix.
- `OBSERVATION` (M2.4, non-fatal): a run once logged
  `"TTS context … completed with no audio"` before audio then appeared
  normally — no audible effect; not root-caused; deferred to `M2.4B`.
- `OBSERVATION` (M2.4, shutdown-only): abrupt Ctrl+C (`WorkerRunner.cancel`)
  can trip an ALSA-lib `snd_pcm_plugin_status` assertion during interpreter
  teardown, **after** all audio has played, on the `plug`→`dmix` chain. A
  graceful `EndFrame` stop is clean. Cosmetic; a fix needs
  `VoiceRuntime.run()` shutdown changes — deferred.
- `VERIFIED FACT` (M2.4): the two Piper voice files in
  `~/.local/share/nexa/tts/voices/` are byte-identical (sha256) to the
  legacy `smart-desk-ai-assistant/voices/piper/` files.
  `scripts/setup_piper_http.py` *optionally* copies them from that
  read-only legacy path if present (else downloads); NeXa's runtime code
  never reads the legacy path.

## Current architecture state

- Conceptual boundaries: `docs/architecture/FOUNDATION_ARCHITECTURE.md`
  (conceptual only, except three pointers into the docs below).
- **Real (`VERIFIED FACT`):** `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
  — the Conversation and (local) Model Providers boundaries, implemented in
  `src/nexa/conversation/` + `src/nexa/providers/`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md`
  — the M2.1 slice of the Voice boundary (local audio transport + VAD only),
  implemented in `src/nexa/voice/`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_2_LOCAL_STT_ARCHITECTURE.md`
  — the M2.2 slice of the Voice boundary (local whisper.cpp STT, pre-roll
  capture, serialized transcription execution), implemented in
  `src/nexa/stt/` + `src/nexa/voice/runtime.py`'s
  `_UtteranceCaptureFrameProcessor`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_3_VOICE_CONVERSATION_ADAPTER_ARCHITECTURE.md`
  — the M2.3 slice of the Voice boundary (voice → `ConversationSession`
  wiring, serialized conversation-turn execution, response-language
  mirroring), implemented in `src/nexa/voice_conversation/` +
  `src/nexa/conversation/language.py`/`context.py`.
- **Real (`VERIFIED FACT`):** `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md`
  — the M2.4 slice of the Voice boundary (streamed assistant text →
  sentence-chunked Piper TTS via an external HTTP process → dedicated USB
  speaker DAC; response-language → voice mapping; a temporary half-duplex
  self-echo gate; independent input/output device selection), implemented
  in `src/nexa/tts/` + `src/nexa/voice_tts/` + `src/nexa/voice/gate.py` +
  `src/nexa/voice/runtime.py`'s `_MicGateFrameProcessor` +
  `src/nexa/voice/config.py`. **Barge-in is NOT here** — M2.5.
- **ADR-0002 (Accepted)** sets the M1 direction: one minimal canonical
  text-conversation path (`ConversationSession` → `ConversationContext` →
  `ModelProvider` → streamed tokens); model access via a minimal
  OpenAI-chat-shaped `ModelProvider` abstraction; **Ollama** as the first
  `LocalModelProvider` implementation (`llama.cpp` second / portability) —
  **both now implemented**, Ollama live-verified, `llama-server` interface-only
  (see "What is partial").
- **ADR-0002 M1.0B amendment (2026-09-02, informational — D1–D4 unchanged at
  the time):** the fair Bielik re-test is done and does **not** flip the
  baseline (D4 caveat (b) resolved); a better Polish persona lifted
  `qwen3:4b-instruct` PL 2/5 → 3/5 (caveat (a) partially addressed); new
  evidence — `gemma4:e4b` / `gemma4:e2b` out-converse the incumbent in both
  languages.
- **ADR-0002 Amendment 2 (2026-09-05, decisive — supersedes D4 on model choice
  only; D1–D3 unchanged):** operator blind test run and scored; owner signed
  off on the RAM/latency cost; **`gemma4:e4b` is FROZEN as the M1.1 local
  conversation baseline.** `gemma4:e2b` documented as the fast/low-RAM
  alternative, `qwen3:4b-instruct` as the swappable safe fallback. Provider/model
  abstraction (D1–D3) explicitly preserved — this model is the M1.1 *local*
  baseline, not NeXa itself; no router implemented.
- **ADR-0003 (Accepted, 2026-09-05)** sets the M2 direction — architecture
  and component choices decided; Pipecat (BSD-2-Clause) as the local voice
  orchestration framework, owning audio transport/VAD-wiring/turn-detection
  only; a NeXa-owned adapter (`VoiceConversationAdapter`, now built — M2.3
  — as a plain callback-driven class rather than the ADR's illustrative
  `FrameProcessor` sketch, which explicitly left the mechanism open) feeds
  the same, unchanged `ConversationSession` — no second history/persona/
  model choice for voice. Silero VAD (`USE AS-IS`).
  whisper.cpp `base/q8_0` as the initial local STT baseline (**not frozen** —
  same discipline as ADR-0002 D4; Parakeet/Canary and Hailo offload remain
  open candidates). Piper via subprocess as an explicitly **temporary**
  TTS baseline (**not frozen**; too slow for the final target, GPL-3.0
  successor). Explicit PL/EN language-hint strategy required — auto-detect
  rejected. Pipeline sequencing (VAD/STT before LLM generation; TTS may
  overlap generation) and explicit CPU-thread budgets are architectural
  requirements. Full barge-in is the M2 target, with new
  interruption-coordination work named (not built) beyond M1.1's
  `CancelToken`. LiveKit Agents is **deferred, not rejected** — no second
  orchestration framework installed for the first local implementation.
- **M2.1 implemented per ADR-0003 D1–D3, D10** (`src/nexa/voice/`,
  `R0007`): Pipecat local audio transport + Silero VAD →
  `VoiceStateMachine`. VAD `stop_secs` retuned from the library default
  (0.2s) to an evidence-based `1.0s` — see "What works" above for the full
  chain. No STT/LLM/TTS/barge-in — `ConversationSession` untouched.
- **M2.2 implemented per ADR-0003 D4, D5, D7, D11** (`src/nexa/stt/`,
  `R0008`): pinned whisper.cpp `base/q8_0` → `WhisperCppTranscriber` →
  `SerialTranscriptionQueue` → `on_transcription` callback, fed by a
  pre-roll-preserving `UtteranceBuffer` inside a new
  `_UtteranceCaptureFrameProcessor` in the M2.1 pipeline. Explicit `pl`/`en`
  language hint required — no `AUTO` member exists. See "What works" above
  for the two real-hardware findings (concurrency, state-display) and their
  fixes. No LLM/`ConversationSession` adapter/TTS/barge-in —
  `ConversationSession` untouched.
- **M2.3 implemented per ADR-0003 D2** (`src/nexa/voice_conversation/`,
  `R0009`): `VoiceConversationAdapter` → `SerialConversationQueue` →
  `ConversationSession.send()` — the exact same call typed chat makes.
  Response-language mirroring (`src/nexa/conversation/language.py`, wired
  into `ConversationContext.to_provider_messages()`) is canonical
  `ConversationSession` policy, not voice-specific — see "What works"
  above for the two real-hardware findings (conversation-turn concurrency;
  language mirroring plus its own cache-breaking latency regression) and
  their fixes. No TTS/barge-in yet.
- **M2.4 implemented per ADR-0003 D6, D7** (`src/nexa/tts/`,
  `src/nexa/voice_tts/`, `src/nexa/voice/gate.py`, `R0011`; follows R0010's
  Recommendation A): `AssistantSpeechBridge` → `PiperHttpTTSService`
  (Pipecat 1.8.1, `/synthesize` URL, built-in SENTENCE aggregation) →
  `TtsStatusObserver` → `LocalAudioOutputTransport` → the `UACDemoV1.0` USB
  DAC. External `python -m piper.http_server` process, its own venv, GPL
  `piper-tts` never imported in-process (`ast`-verified), `pyproject.toml`
  unchanged. Voice chosen from M2.3's canonical response-language function.
  A temporary **half-duplex self-echo gate** (`HalfDuplexGate` +
  `_MicGateFrameProcessor`) withholds mic audio before VAD/STT while real
  TTS playback frames say NeXa is speaking — NOT barge-in (M2.5). Output is
  now an **independent** by-name device selection from the reSpeaker input.
  See "What works" above for the two real-hardware findings
  (self-conversation loop; post-replug output routing) and their fixes.
- Product code now exists for M1.1 (`src/nexa/conversation/`,
  `src/nexa/providers/`, `src/nexa/config.py`, `src/nexa/bootstrap.py`,
  `apps/nexa_chat.py`), M2.1 (`src/nexa/voice/`, `apps/nexa_voice_probe.py`),
  M2.2 (`src/nexa/stt/`, `apps/nexa_stt_probe.py`), M2.3
  (`src/nexa/voice_conversation/`, `apps/nexa_voice_chat_probe.py`), and
  M2.4 (`src/nexa/tts/`, `src/nexa/voice_tts/`, `src/nexa/voice/gate.py`,
  `apps/nexa_voice_tts_probe.py`, `scripts/setup_piper_http.py`). **M2.4B
  has product code now:** `src/nexa/voice_tts/metrics.py` +
  `timed_tts.py` (B.1 / B.1A, measure-only), `src/nexa/voice_tts/speech_planner.py`
  (B.2/B.2A — `NexaSpeechPlanner`, `normalize_for_speech`,
  `find_phrase_cut`; wired into the probe's `extra_output_stages` between
  the bridge and the TTS service), and B.3.1 CPU/context tuning:
  `PiperHttpConfig.nice` (+ `DEFAULT_PIPER_NICE = 10`, `default_piper_nice()`)
  applied in `nexa/tts/server.py` via a `nice -n N` exec prefix, and
  `nexa/voice_tts.DEFAULT_TTS_CONTEXT_TIMEOUT_S = 8.0` passed to
  `PiperHttpTTSService(stop_frame_timeout_s=…)` by the probe. **B.3.2** —
  `src/nexa/voice_tts/continuity.py` (`NexaSpeechContinuityController`,
  `decide_release`; wired into `extra_output_stages` between the planner
  and the TTS service; `--continuity-target-s` / `--no-continuity`).
  **B.3.3** — `src/nexa/conversation/response_mode.py` (`ResponseMode`
  `StrEnum` + `voice_response_directive()`); `ConversationSession.send`
  and `ConversationContext.to_provider_messages` take a `response_mode`
  keyword (default `TEXT` = unchanged); `VoiceConversationAdapter`
  defaults to `VOICE`; probe flag `--response-mode {text,voice}`. M2.5
  (barge-in) still has no product code.

## Current test status

- Repo-local `./.venv` (system Python 3.13.5, **not** the legacy repo's venv)
  with `dev` extras (`pytest`, `ruff`) and `pipecat-ai[local]==1.8.1`
  installed.
- `python -m unittest discover -s tests`: **440 OK, 7 skipped**;
  `pytest`: **433 passed, 7 skipped, 14 subtests passed** (2026-09-07,
  after M2.4B.3.3 — `+17` `tests/test_conversation_response_mode.py`).
  The 7 skips are all opt-in / environment-gated: 1 live Ollama, 2 live
  whisper.cpp, 3 live Piper HTTP (`NEXA_RUN_LIVE_TTS_TEST=1`), 1 reSpeaker
  hardware probe. `ruff check src tests apps scripts/setup_piper_http.py`:
  clean. (`tests/test_voice_tts_metrics.py` 69 tests — B.1/B.1A/B.2;
  `tests/test_voice_tts_speech_planner.py` 59 tests — B.2 + B.2A;
  `tests/test_tts_server.py` — B.3.1 Piper `nice`;
  `tests/test_voice_tts_bridge.py` — B.3.1 TTS context timeout.)
  (Pre-existing unrelated `ruff` findings in `scripts/m1_bench/` — M1
  benchmark tooling, committed in `b79a752`, untouched.)
- Live Ollama integration test (`NEXA_RUN_LIVE_TESTS=1 python -m unittest
  tests.test_live_ollama_integration`): **PASS** against real `gemma4:e4b`
  (2026-09-05) — see `R0004` for the transcript evidence.
- **Human acceptance test (M1.1):** Andrzej ran a real multi-turn conversation
  through `apps/nexa_chat.py` (the actual canonical path, real `gemma4:e4b`)
  and recorded **"M1.1 HUMAN ACCEPTANCE: PASS"** (2026-09-05) — see `R0004`'s
  "Operator acceptance" addendum.
- **Hardware acceptance test (M2.1):** `NEXA_RUN_VOICE_HARDWARE_TEST=1
  python -m unittest tests.test_voice_hardware_probe`: **PASS** against the
  real reSpeaker XVF3800. Separately, Andrzej ran the real
  `apps/nexa_voice_probe.py` across a genuine tuning cycle (library-default
  `stop_secs=0.2` → deterministic offline calibration → confirmed live
  retest at `stop_secs=1.0`) — see `R0007` for the full evidence chain.
- **Hardware acceptance test (M2.2):** `NEXA_RUN_LIVE_STT_TEST=1 python -m
  unittest tests.test_stt_transcriber_live`: **PASS** against the real
  pinned whisper.cpp binary+model. Separately, Andrzej ran the real
  `apps/nexa_stt_probe.py` for real Polish/English transcription (no
  first-word truncation in any case) and, after a real concurrency defect
  was found live and fixed, a dedicated concurrency retest confirming
  `max concurrent STT executions observed this session: 1` — see `R0008`
  for the full evidence chain.
- **Hardware acceptance test (M2.3):** Andrzej ran the real
  `apps/nexa_voice_chat_probe.py` for real Polish and English multi-turn
  voice conversations (context preserved across turns, including a
  cross-language PL→EN follow-up), a dedicated fast-second-utterance
  concurrency retest (`max concurrent conversation turns observed this
  session: 1`), and — after a real response-language mirroring defect and
  its own cache-breaking latency regression were found live and fixed — a
  final retest confirming both correct PL/EN mirroring and restored warm
  first-token latency (~2-5s) — see `R0009` for the full evidence chain.
- **Hardware acceptance test (M2.4):** Andrzej ran the real
  `apps/nexa_voice_tts_probe.py` for an extended real voice conversation
  and explicitly confirmed the full local loop (mic → VAD → whisper.cpp →
  `ConversationSession`/`gemma4:e4b` → streamed reply → Piper TTS →
  `UACDemoV1.0` USB DAC): old NeXa Piper voice audible, NeXa no longer
  talks to herself (half-duplex gate), multi-turn context preserved,
  responses coherent. Preceded by separately-confirmed direct-hardware
  steps (device re-detection after reboot/replug, direct tone, direct old
  PL/EN Piper playback to the DAC, Pipecat-only playback without LLM) —
  see `R0011` for the full evidence chain. Optional live suite:
  `NEXA_RUN_LIVE_TTS_TEST=1 python -m unittest tests.test_tts_server_live`.
- `scripts/m1_bench/bench.py`: smoke-tested and used for real measurements
  (M1.0/M1.0B; unrelated to the M1.1/M2.1/M2.2/M2.3 product tests above).

## Active architectural decisions

- **ADR-0001** — NeXa project foundation (Accepted).
- **ADR-0002** — Text conversation foundation (Accepted for architecture /
  provider boundary / first runtime) + **M1.0B amendment** (2026-09-02,
  informational) + **Amendment 2** (2026-09-05, decisive: M1.1 local baseline
  model **FROZEN** to `gemma4:e4b`).
- **ADR-0003** — Realtime voice foundation (Accepted, 2026-09-05): Pipecat +
  unchanged `ConversationSession` + Silero VAD + whisper.cpp `base/q8_0`
  (baseline) + Piper/subprocess (temporary baseline) + sequencing/thread-budget
  rules + full barge-in target + LiveKit deferred. **whisper.cpp `base/q8_0`
  is now real** (M2.2, `R0008`); **D2's voice → `ConversationSession`
  adapter is now real** (M2.3, `R0009`, as a plain callback-driven class —
  the ADR's illustrative `FrameProcessor` mechanism was explicitly left
  open for this substage to decide) — see "Current architecture state"
  above for the full summary; full text in
  `docs/decisions/ADR-0003_realtime_voice_foundation.md`. Not modified by
  M2.2, M2.3, or M2.4 — no implementation contradiction was found in any.
  **D6's TTS baseline is now real** (M2.4, `R0011`): external Piper HTTP
  process, no in-process GPL import — Piper stays explicitly *temporary*,
  not frozen.

## Current focus

- **M2.6 — Cloud Realtime Voice. Research + Phase-0 corrections COMPLETE
  (`R0030`); M2.6A CLOUD REALTIME VOICE FEASIBILITY = PASS /
  OPERATOR-CONFIRMED (`R0031`, 2026-09-10); VOICE `Sulafat` =
  OPERATOR-CONFIRMED (frozen cloud voice baseline).** Attempt #1 infra bug
  (missing `LLMRunFrame` kickoff) fixed; attempt #2 (Pipecat default
  voice) = real PL/EN conversation judged EXCELLENT / "essentially
  immediate" / "mega super", only ask a female/cozy voice; attempt #3
  (`Sulafat`) → operator *"I like this voice, we keep it."* Machine
  evidence (3 sessions, turn-local): EOT→first-audible ≈0.75–0.81 s
  median (R0030 ≤1.5 s PASS), barge-in ≈2 ms (R0030 ≤100 ms PASS), AEC 0
  failures. "1.93 s / 19.84 s outliers" = metric artifacts (fragmented
  speech + cross-turn pairing), fixed by turn-local reconstruction.
  **C6 (turn-local):** RAW transcript before first audio 3/3 valid turns
  (~0.5 s), PUSHED 2/3 — timing headroom ≠ steerability; ADR-0004 defaults
  to Gemini native mirroring (A), delayed-`activity_end` + local
  language-ID as (B). **C6 = ANSWERED; ADR-0004 = Accepted, Amendment 1
  Accepted.** Reconnect testing deferred to M2.6B. LOCAL REALTIME VOICE
  is complete and operator-confirmed: M1 through M1.1, and M2 through
  **M2.5B (`R0029`, COMPLETE / OPERATOR-CONFIRMED 2026-09-10)** — research
  (`R0005`) → spikes (`R0006`) → `ADR-0003` → M2.1 (`R0007`) → M2.2
  (`R0008`) → M2.3 (`R0009`) → M2.4A (`R0010`) → M2.4 (`R0011`) → M2.4B
  natural-speech-flow (`R0012`–`R0027`) → M2.5A feasibility (`R0028`) →
  M2.5B (`R0029`) — all operator-confirmed on real hardware. The accepted
  LOCAL VOICE baseline and the production barge-in stack are recorded
  under *What works (VERIFIED FACT)* above and are frozen.
- **M2.6 research conclusions (`R0030`):** provider frozen for v1 to
  Google Gemini Live `gemini-3.1-flash-live-preview` (VERIFIED real +
  current; Preview; native audio; synchronous-only tools; no caching).
  Integration = Pipecat **OPTION C** — wrap the installed 1.8.1
  `GeminiLiveLLMService`, harden its two known gaps (GitHub #5465 silent
  reconnect-window drops — fix PR #5497 OPEN; no `GoAway` handling).
  Architecture principle held: **one `ConversationSession` authority**;
  cloud is a NEW `RealtimeVoiceProvider` boundary (not a `ModelProvider`)
  fed a NeXa-derived, privacy-filtered `CloudContextSnapshot`; canonical
  transcript from Gemini's input/output transcription + a spoken-audio
  high-water mark; raw cloud audio NOT retained; minimal cloud
  `system_instruction` (role card, not NeXa identity). `ConversationPolicy`
  (AUTO / LOCAL_ONLY / CLOUD_PREFERRED, NeXa-owned) is distinct from
  runtime `active_provider` (LOCAL / CLOUD); the model may classify a
  switch intent, **NeXa executes it**. HYBRID audio — keep XVF3800 AEC +
  local Silero as the turn authority, Gemini server VAD off, NeXa keeps
  final authority over the speaker. **EXTERNAL REPORTED RISK (community,
  not Google-confirmed):** one forum thread reports Polish native audio
  with a strong EN/US accent — the M2.6A operator test is the
  authoritative check. **Data terms:** in EEA/CH/UK the paid data terms
  apply to unpaid quota too, and a cloud voice *made available to* such
  users must use Paid Services; outside those regions unpaid-quota data is
  used to improve Google products. **[HISTORICAL — as of R0030, superseded]**
  ~~Paid-key decision → ADR-0004.~~ **CURRENT: eligibility is settled by
  ADR-0004 Amendment 1 — `ProviderEligibilityPolicy(distribution_mode,
  billing_verified)`; no tier is encoded in the Gemini API key; the
  operator is in the United Kingdom (not the EEA); `DEVELOPMENT` mode is
  not blocked solely by billing; a `DISTRIBUTED` release to EEA/CH/UK users
  requires `billing_verified`.** **M2.6A
  feasibility = PASS / OPERATOR-CONFIRMED; VOICE `Sulafat` =
  OPERATOR-CONFIRMED (`R0031`).** **C6 (R0030) — ANSWERED (turn-local,
  Sulafat session):** RAW input transcription reaches NeXa ~0.5 s before
  first response audio in 3/3 valid turns; PUSHED (aggregated) in 2/3.
  Gemini has almost certainly begun generating by then (`activity_end`
  already sent), and there is no observed mechanism for NeXa to steer the
  *same* response — so ADR-0004 defaults to Gemini native language
  mirroring (which worked), with delayed-`activity_end` + a local
  language-ID as the measured fallback. ADR-0004 = Accepted, Amendment 1
  Accepted. **[HISTORICAL — as of the ADR-0004 Amendment-1 task,
  superseded]** ~~M2.6B = NEXT / NOT STARTED.~~ **CURRENT: `M2.6B` is
  IN PROGRESS — `M2.6B.1` (`R0032`), `M2.6B.2` (`R0033`), `M2.6B.2A`
  hardening (`R0034`), `M2.6B.3` (`R0035`), `M2.6B.3A` pre-live hardening
  (`R0036`) and `M2.6B.3B` interrupted-history safety (`R0037`) are all
  IMPLEMENTED — the real Gemini/hardware operator acceptance is the one
  remaining step before `M2.6B` can be marked COMPLETE (and even then,
  only once the proactive-reconnect gate is resolved — see R0037's
  standing note). See "Latest report" above.**
- **After local + cloud voice** (unchanged plan): memory / identity /
  personality / capabilities → full graphical UI → typed chat in that UI
  on the **same** `ConversationSession` / NeXa brain as voice (never a
  separate voice-NeXa and chat-NeXa).

## Exact next recommended task

**Re-run the M2.6B real-hardware/Gemini operator acceptance test
(ATTEMPT #2).** ATTEMPT #1 (`R0038`, 2026-09-11) FAILED with three real
regressions — a `_VadToProviderBridge` Pipecat setup/cleanup crash, local
playback not stopping on barge-in, and English input answered in Polish —
all three found and root-caused against installed source; the first two
fixed deterministically (923 tests, 0 regressions), none re-tested on
real hardware yet. **The third (language) has no fix yet**:
`M2.6B.4A` (`R0039`) benchmarked the obvious fix (gate `activityEnd` on
local LID) and found it costs ~1.1–1.7s on EVERY turn with the currently
available detector — the user chose to pause and research a lighter LID
rather than ship that cost; native mirroring (still unreliable) remains
the only mechanism in place for language. `ADR-0004` + Amendment 1 remain Accepted, unchanged; M2.6A
feasibility and the `Sulafat` voice remain OPERATOR-CONFIRMED. Launch
`apps/nexa_cloud_voice_app.py` (no `--dry`), wait for
`CLOUD_PROVIDER_READY` + `AEC_REF_ACTIVE`, then a short natural
conversation deliberately re-exercising what failed: one clear mid-reply
interruption (confirm playback stops immediately and never resumes), and
at least one English question after some Polish conversation (observe
the new `TURN_INPUT_LANGUAGE`/`SNAPSHOT_LANGUAGE_PREFERENCE` diagnostic
lines — native mirroring is not expected to be forced correct by this
checkpoint, only observed) — see `R0038`'s EXACT NEXT LIVE RETEST
COMMAND. Only after a clean PASS on this retest, **and only once the
proactive-reconnect gate is resolved (standing note, carried since
R0036/R0037)**, can `M2.6B` be marked COMPLETE.

**`M2.6B.1` DONE (`R0032`, 2026-09-11)** — the provider-agnostic
`src/nexa/realtime/` package: `RealtimeVoiceProvider` ABC (a peer of
`ModelProvider`) + `ProviderReadiness`; `ConversationPolicy` +
`ProviderEligibilityPolicy` (`DISTRIBUTED` to EEA/CH/UK users needs
`billing_verified`, `DEVELOPMENT` does not — no tier flag on the key);
`CloudContextSnapshot`; `InboundAudioBuffer` for Pipecat #5465;
`ProviderUsageEvent` telemetry; a deterministic `ReconnectController` (no
socket); `gemini/credentials.py` + `gemini/voice.py` — neither imports
`google.genai`.

**`M2.6B.2` DONE (`R0033`, 2026-09-11)** — the canonical cloud-turn write
path and the production provider:
- `src/nexa/realtime/turn.py` (`CloudTurnAccumulator`) — one logical cloud
  turn -> at most one canonical commit, NeXa-local `generation` id.
- `src/nexa/realtime/turn_framing.py` (`UtteranceFramer`) — preserves the
  `activity_start`/audio/`activity_end` envelope through a NOT_READY
  window; bounded, no orphan audio, no duplicate delivery.
- `src/nexa/realtime/router.py` (`ConversationRouter`) — owns
  `ConversationPolicy`/`active_provider`/provider lifecycle/snapshot
  creation/cloud-turn commits/failure fallback; `LOCAL_ONLY` never calls
  the injected cloud-provider factory.
- `src/nexa/realtime/gemini/service.py` (`GeminiLiveProvider`) — the
  production `RealtimeVoiceProvider`, built exactly per the R0032 source
  audit (construction-time `system_instruction`, initial `LLMContext`
  from `snapshot.recent_turns`, one `LLMRunFrame` kickoff, Pipecat's own
  one-time `clientContent` seed, `UtteranceFramer` wired into the turn-I/O
  methods gated on `readiness==READY`). **Proven against the REAL Pipecat
  1.8.1 pipeline/worker/aggregator machinery** (fake terminal service only
  — no cloud call); a real bug was found and fixed this way
  (`WorkerRunner.add_workers()` needs `asyncio.create_task(runner.run())`
  launched alongside it, matching the M2.6A probe's proven pattern).
- Additive `ConversationSession.record_external_exchange` — NORMAL /
  USER-ONLY / INTERRUPTED / NO-SPOKEN-ASSISTANT / invalid cases all
  tested; `send()`/`commit_interrupted_turn()` unchanged.
- `pyproject.toml`: `cloud-gemini` optional extra added
  (`google-genai>=2.22,<3` + `websockets>=15,<17`); core deps unchanged;
  `pip install .` stays Google-cloud-free.
- +57 new tests (870 total, 0 regressions); zero non-additive
  `src/nexa/**` change.
- **Known gap (resolved in M2.6B.2A below):** `ReconnectController` is not
  yet driven by `GeminiLiveProvider` on a real connection error — no live
  GoAway/age-timer reconnect wiring yet.

**`M2.6B.2A` DONE (`R0034`, 2026-09-11)** — pre-hardware cloud-turn /
reconnect hardening:
- **Fixed a real silent-user-speech-loss bug:** `user_turn_start()`
  decided "send live vs. buffer" only once, at call time, with no record
  of that decision — a turn that started live and then lost readiness
  mid-utterance had all further audio rejected as an orphan by
  `UtteranceFramer` (no `activity_start` was ever recorded there), and the
  eventual `user_turn_end()` silently ignored too (no live `activity_end`
  ever reached Gemini). Fixed with a `_live_turn_open` flag +
  deterministic abort-and-restart: the stranded live segment is marked
  aborted (logged, never silent) and every subsequent frame becomes a
  NEW, self-contained buffered utterance, flushed once `READY` returns.
  **Production policy:** a connectivity loss exactly mid-utterance may
  split it into two utterances from Gemini's perspective, but never
  silently drops user audio and never delivers the same audio twice. New
  `take_pending_audio()` lets a fresh-session hand-off retrieve (once,
  destructively) whatever was buffered but undelivered.
- **Completed the provider->NeXa event map:** `InterimTranscriptionFrame`
  (partial user transcription, previously unhandled) +
  `TranscriptionFrame.finalized` (previously hard-coded `True`); a new,
  unambiguous `GenerationCompleteEvent` (replaces an earlier ambiguous
  empty-text `AssistantTranscriptionEvent` for turn-complete);
  `ErrorFrame`/`FatalErrorFrame` -> `RealtimeProviderError`/
  `RealtimeProviderFailedError`. **Found and fixed a second gap:**
  Pipecat's own `push_error()` pushes error frames **upstream**, but the
  only tap sat downstream of the Gemini service — could never see one.
  Fixed with a second `up_tap` before the user aggregator.
- **Closed the "manual injection" test gap:** new
  `ConversationRouter.handle_provider_event()` is the only path by which a
  provider's event stream reaches the router/session; a new integration
  test drives one full cloud turn exclusively through real provider
  events and proves exactly one canonical exchange results.
- **5 turn-ordering/interruption permutations tested** (user→assistant→
  complete; local interruption beating a late server ACK — exposing and
  fixing a third gap, `CloudTurnAccumulator.on_assistant_transcription`
  still appending a late delta after `on_interruption()`, now refused;
  provider death before vs. after a spoken prefix). **Turn-complete-
  before-delayed-transcription proven IMPOSSIBLE from the installed
  Pipecat source** (not assumed): `_connection_task_handler` processes
  messages strictly in arrival order, handling input_transcription before
  turn_complete even within one bundled message.
- **Fresh-snapshot-on-resumption-failure mechanism made precise:**
  destroy-and-recreate — a brand-new `GeminiLiveProvider` builds its own
  `LLMContext` from a freshly built `CloudContextSnapshot` and holds no
  reference to any previous instance, so stale Pipecat-owned context
  cannot structurally leak in; proven with a deliberately stale old
  context vs. a fresh canonical snapshot.
- +16 tests (886 total, 0 regressions); zero non-additive `src/nexa/**`
  change (no existing file outside `src/nexa/realtime/**` touched this
  checkpoint).
- **Known gap, still explicit:** `ReconnectController` is not yet driven
  by `GeminiLiveProvider` on a real connection error — no live GoAway/
  age-timer reconnect wiring yet; this checkpoint hardens what happens
  once a fresh session is decided and while readiness is degraded
  mid-turn, not when that decision is made from a real socket.

**`M2.6B.3` DONE (`R0035`, 2026-09-11), TEST-READY** — `ReconnectController`
wired into `GeminiLiveProvider` via a new `_readiness_monitor()`
observation seam (deterministic/mocked; Pipecat's own reconnect is
automatic/internal with no external hook); mid-user-turn loss is
explicitly NOT assumed resumable —
`ConversationRouter.recover_from_mid_turn_loss()` destroys the old
provider and replays only pending PCM into a fresh one from a fresh
canonical snapshot. New `src/nexa/realtime/gemini/runtime.py`
(`GeminiVoiceRuntime`) wires the HYBRID audio path — reSpeaker → local
Silero (Gemini server VAD OFF) → provider → Gemini → assistant audio →
USB speaker, teed to the XVF3800 AEC far-end reference via the existing,
unmodified `AecReferenceFeeder` — and cloud barge-in via the existing,
unmodified `BargeInController`. New operator app
`apps/nexa_cloud_voice_app.py` (`--dry` proven live in-sandbox). +10 tests
(896 total, 0 regressions). **Real LOCAL↔CLOUD spoken switch test not
required for this run (charter policy); minimum real-hardware operator
acceptance is the one remaining step before M2.6B is marked COMPLETE —
NOT YET RUN, see R0035.**

**`M2.6B.3A` DONE (`R0036`, 2026-09-11)** — final pre-live hardening on
three seams R0035 left wrong or undriven: new `_ResponseLifecycle`
(cloud analogue of `nexa.voice.gate.HalfDuplexGate`'s combinator) makes
bargein-finish wait for real `BotStoppedSpeakingFrame` playback truth
(plus a deterministic `TTSStoppedFrame` injected after each generation's
audio, in the same FIFO the audio chunks went through) instead of firing
on `GenerationCompleteEvent` alone (installed Pipecat source proves
generation-complete says nothing about the output transport's own
queue); new `_SpokenPrefixHighWater` one-chunk-lag combinator replaces
the raw running `CloudTurnAccumulator.assistant_text` as the interrupted
spoken prefix (Pipecat's own source documents output-transcription
arriving ahead of, and containing more text than, the audio actually
produced — a proven, not theoretical, overshoot risk); and
`recover_from_mid_turn_loss()` — previously uncalled anywhere — is now
actually driven by `_consume_provider_events`, with a new
`_ProviderHandle` box giving an atomic provider swap and never a second
concurrent `provider.events()` reader. Also fixed a real bug (the
operator app silently replaced, rather than composed with, the metrics
AEC callback) and explicitly documented the proactive-reconnect caller
as deferred (not overclaimed). +15 net new tests (911 total, 0
regressions). Real Gemini/hardware operator acceptance remains the one
step before M2.6B is COMPLETE — NOT YET RUN, see R0036.

**`M2.6B.3B` DONE (`R0037`, 2026-09-11)** — interrupted cloud history
safety only. Found R0036's own `_SpokenPrefixHighWater` one-audio-chunk-
lag mechanism still overclaimed: a later chunk's mere existence does not
prove how much of an EARLIER text snapshot that chunk's own audio covers
(snapshot `"abcdef ghijkl mnop..."`, chunk #1 = "abc", chunk #2 = "def" —
chunk #2 arriving proves nothing about chunk #1 covering the WHOLE
earlier snapshot). Re-searched installed source for real alignment:
`google.genai.types.Transcription.words`/`WordInfo.start_offset`/
`end_offset` exist in the underlying SDK's own type schema (real per-word
timing), but Pipecat's installed `_handle_msg_output_transcription`
never reads or forwards `words` (grepped the whole file: zero
occurrences) — unreachable without bypassing Pipecat's own service
(out of scope). **Conclusion: no deterministic alignment exists in the
integrated stack.** Deleted `_SpokenPrefixHighWater`; new
`CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""` is now passed
unconditionally on every confirmed interruption — an interrupted cloud
turn always commits `COMMITTED_USER_ONLY` regardless of chunk count or
accumulated text. **Cloud interrupted-prefix precision: CONSERVATIVE / NO
FALSE FUTURE TEXT** — under-crediting accepted, crediting unspoken words
is not. Normal completions unaffected. Playback lifecycle, AEC,
reconnect/mid-turn-recovery, router architecture, Gemini model/voice,
VAD, and barge-in thresholds untouched. 5 tests replace R0036's 6
(matching the charter's own numbered scenarios); full suite **910 tests,
OK (skipped=7)**, 0 regressions. **Standing note: `M2.6B` must NOT be
marked COMPLETE after the hardware run until the proactive-reconnect/
age-trigger gate is either implemented+validated or explicitly changed by
an ADR amendment** — restated from R0036, not newly resolved.

**`M2.6B.4` — production hardware acceptance ATTEMPT #1: FAIL** (`R0038`,
2026-09-11). The FIRST real operator hardware/Gemini run happened:
`CLOUD_PROVIDER_READY`, `AEC_REF_ACTIVE`, audible Sulafat, normal
conversation all reached — but **three real regressions**, each
root-caused against installed source (not guessed) and fixed
deterministically (no further Gemini call made): **(1)**
`_VadToProviderBridge` crashed Pipecat's real setup/cleanup —
`FrameProcessor.__init__` already owns `self._metrics`
(`frame_processor.py:256,653,669`); NeXa's code stored `RuntimeMetrics`
under that same name, clobbering it. Fixed: renamed to
`self._nexa_metrics`. **(2)** local playback did not stop on barge-in —
the interrupted reply played to completion while the new reply was
already generating. Source-audited: `broadcast_interruption()`
(unchanged) only clears the output queue's contents at that instant; it
does nothing about audio still on `provider.events()`'s own queue or
produced by Gemini in the brief window before honouring the cancel
signal — confirmed as the real cause, not a cloud-model problem. Fixed
with new `_ResponseGenerationGuard`: every assistant audio chunk checked
against the currently valid response-generation id before ever reaching
`hw_worker.queue_frames()`; interruption invalidates the current
generation synchronously (no race). A real design bug was found and
fixed while building this (conflating "may this chunk play" with "has a
new local turn started" let trailing old-generation audio masquerade as
a fresh dispatch) — fixed by keying dispatch timing to a fresh, final
`UserTranscriptionEvent` instead (R0034's own proven message-ordering
guarantee), which also fixed a second, previously-latent bug (dispatch
tracking never reset on a normal `GenerationCompleteEvent`). **(3)** two
English questions were both answered in Polish. Reconstructed the exact
production snapshot: `apps/nexa_cloud_voice_app.py` never passed
`language_preference` and `build_default_session()` starts with empty
history — **the snapshot was genuinely neutral**, a real Gemini
native-mirroring reliability gap, activating ADR-0004's own documented
Option-B fallback. Built the offline detection half (reusing verbatim
`nexa.stt.WhisperCppLanguageDetector` + `nexa.conversation.
ResponseLanguageResolver` — sticky only on an explicit directive) with
utterance-audio buffering + correlation + the charter's exact diagnostic
log keys; **honestly did NOT build** same-turn steering of the response
already in flight (no verified, low-risk mechanism reachable without a
live call) — native mirroring stays active, the gap is explicit, not
papered over. **+13 net new tests (923 total, 0 regressions)**, including
a real Pipecat `Pipeline`/`PipelineWorker`/`WorkerRunner` setup/process/
cleanup test (not `--dry`) and a language test using real recorded PL/EN
audio. Local voice completely untouched (only
`realtime/gemini/runtime.py` + its test file changed this checkpoint).
**Hardware acceptance NOT marked PASS; `M2.6B` NOT marked COMPLETE.** Real
Gemini/hardware operator RETEST is the one remaining step — NOT YET RUN,
see R0038 for the exact retest command.

**`M2.6B.4A` — strict same-turn PL/EN language authority: RESEARCH ONLY**
(`R0039`, 2026-09-11). R0038 diagnosed but did not fix the language-
mirroring failure. Benchmarked `WhisperCppLanguageDetector` (base/q8_0)
on the real PL/EN fixtures per the charter's own "measure LID first"
instruction: **~1.15–1.7s per call, essentially CONSTANT regardless of
input duration**, no reliable early-truncation point (PL misclassified
as EN below ~1.5s). Read the installed `whisper.cpp` v1.9.3 source
directly: `whisper_lang_auto_detect` always runs a full encoder forward
pass over a FIXED ~30-second-equivalent context (`WHISPER_CHUNK_SIZE`, a
Whisper architectural constant), regardless of actual audio length —
confirms the cost is inherent, not a config/truncation problem. A "hold
`activityEnd` for LID on every turn" gate (the charter's own strict-mode
design) would therefore cost every ordinary same-language turn ~1.1–1.7s
more than the M2.6A baseline — directly contradicting the charter's
stated goal. Put this to the user with the measured numbers **before**
writing any code; **the user chose: pause implementation, research a
lighter LID model first.** Verified official Gemini Live API docs live
(no Gemini call): confirmed realtime modalities are "concurrent streams"
with "ordering... not guaranteed" (rules out a text hint as steering,
independently confirming the charter's own caution); confirmed
`system_instruction` cannot change on an open connection (re-confirms
ADR-0004 Amendment 1); confirmed native-audio models have no
`language_code` param and that a system-instruction language restriction
is Google's own sanctioned mechanism — validating the charter's
provider-replacement design as correct, once a viable low-latency LID
exists. Surveyed (not implemented) lighter alternatives: a smaller `ggml`
Whisper model (same binding, not yet downloaded/verified — this
sandbox's `tiny.bin` files are whisper.cpp's own CI test stubs, not real
weights) or a dedicated non-Whisper spoken-LID model (a genuinely new,
heavier dependency, not adopted casually). Recorded the full STRICT MODE
design (three precisely-named states, reusing R0038's
`recover_from_mid_turn_loss`/`_ProviderHandle` machinery verbatim, a
system-instruction wording proposal) as a specification for a future
checkpoint — **not implemented**. Zero `src/nexa/**` change; full suite
unchanged at 923 tests, OK. **`M2.6B` remains IN PROGRESS; the language-
mirroring gap remains open and undecided** — pending either a lighter
LID, an explicit product decision to accept the measured cost, or a
narrower scope (e.g. first-turn + explicit-sticky-only).

Local realtime voice with production barge-in (`R0029`) is the frozen
baseline M2.6B builds beside — do not destabilise it; `bargein_enabled`
default stays `False`; every existing local-voice regression suite must
stay green; real-hardware operator acceptance is required before M2.6B is
marked COMPLETE. **Credential:** operator-provided key at
`~/.config/nexa/secrets/gemini.env` (outside the repo, 700/600), var
`NEXA_GEMINI_API_KEY`.

Non-blocking, owed independently (not gating cloud): the B.3.6 operator
latency re-confirmation (STT latency + END_OF_TURN → first-audio); a
resource-safe non-blocking pre-warm of the small post-reset
`ProviderWindow` to also remove the M2.5B.2 `keep_entries=0` reset
continuity dip (the `prewarm_provider_context` / `cutover_background`
seam is built and tested; `OLLAMA_NUM_PARALLEL=2` was measured on this Pi
and rejected — gemma4 SWA → concurrent foreground turn cold-reprocesses
~49 s).

Original M2.4B goals (from `R0012`), for historical reference:

- Buffered / look-ahead generation so TTS is not driven one isolated
  sentence at a time; a continuous coherent spoken response rather than
  independent sentence clips.
- Pacing that adapts mildly to how much future generated/audio content is
  buffered.
- Better sentence/phrase boundaries — Pipecat's NLTK splitter runs
  `language="english"` only, so Polish abbreviations (`ul.`, `tzw.`) can
  split a phrase badly (e.g. `"…jest tzw."` / `"horyzont zdarzeń…"`).
- Investigate the non-fatal `"TTS context … completed with no audio"`
  event observed once in a real run.
- Keep the temporary half-duplex gate as-is (M2.5 replaces it).
- `gemma4:e4b` stays frozen; Piper stays the temporary TTS baseline.
- Thread-budget discipline (ADR-0003 D7) still applies — STT + LLM + TTS
  are three concurrent CPU consumers.

**M2.5 — barge-in / interruption / own-TTS suppression / echo handling —
DONE (`R0028` feasibility, `R0029` production, COMPLETE / OPERATOR-CONFIRMED
2026-09-10).** The temporary half-duplex gate is replaced by production
barge-in behind `bargein_enabled` (default off = R0026 unchanged).

Separate, any time (NOT a voice blocker): voice-model selection research
(the operator would eventually prefer a softer/cozier voice than
`pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium`).

Optional, non-blocking, can run any time: a scoped Parakeet/Canary
conversion + benchmark spike (license and Polish support are confirmed
clean per `R0006`; only the hardware path is untested).

Optional, not required, M1.1 hardening the owner may still want at some
point (unrelated to M2, each its own small task):
- Resolve the pre-existing Ollama blob-store permission blocker (needs an
  explicit operator/`sudo` decision) so the `llama-server` adapter and the
  Ollama-vs-llama.cpp benchmark can actually run live.
- Decide whether cancellation needs a stronger guarantee before M2's
  barge-in depends on it (see `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md` §6).
