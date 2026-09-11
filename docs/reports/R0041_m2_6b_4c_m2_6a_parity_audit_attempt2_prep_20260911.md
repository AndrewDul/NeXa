# R0041 — M2.6B.4C: M2.6A vs M2.6B Language Parity Audit + Attempt #2 Preparation

**Date:** 2026-09-11
**Milestone:** M2.6B.4C (follow-on to M2.6B.4 / R0038; supersedes the
R0039/R0040 STRICT-LID trajectory as the next implementation step)
**Status:** Deterministic checkpoint only. No Gemini call. No hardware.
`M2.6B` remains IN PROGRESS. Hardware acceptance NOT marked PASS.

## TASK RESULT

**PRODUCT DECISION (operator-directed): no local LID gate in the normal
cloud critical path.** M2.6A (`R0031`, OPERATOR-CONFIRMED 2026-09-10)
already proved native PL/EN mirroring, switching, and barge-in all worked
well with no local LID. Attempt #1 (`R0038`) was not a clean same-
architecture experiment — it happened alongside two real integration
bugs (VAD-bridge lifecycle crash, playback-interruption failure), both
already fixed. A source-level differential audit (below) compared the
operator-confirmed spike against the current production wiring,
line-by-line, across all 27 requested dimensions. **Found no concrete
regression capable of explaining EN->PL by itself.** The one genuine,
source-verified wording difference — the cloud role card's language-
mirroring sentence had drifted from the spike's own proven, more explicit
per-turn framing — was restored, as a low-risk alignment, not a proven
fix (unverifiable without a live call, which this checkpoint does not
make). **R0039/R0040's local-Whisper-LID research is downgraded to
FALLBACK RESEARCH CANDIDATE ONLY** — not adopted, and not implemented
unless a clean Attempt #2 (this checkpoint's fixes in place) produces
REPEATED evidence that native mirroring is genuinely unreliable in
production.

## M2.6A VS M2.6B DIFFERENTIAL TABLE

Sources: `docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py`
(M2.6A, OPERATOR-CONFIRMED `R0031`) vs `src/nexa/realtime/gemini/service.py`
+ `src/nexa/realtime/gemini/runtime.py` + `src/nexa/realtime/snapshot.py`
(M2.6B current production), read directly, not assumed.

| # | dimension | M2.6A WORKING SPIKE | M2.6B CURRENT PRODUCTION | LANGUAGE-RELEVANT? | ACTION |
|---|---|---|---|---|---|
| 1 | Gemini model | `gemini-3.1-flash-live-preview` | `gemini-3.1-flash-live-preview` (`service.py` `MODEL`) | no (identical) | none |
| 2 | Sulafat voice config | `voice="Sulafat"` (`DEFAULT_VOICE`) | `gemini_voice_for_preference("warm_female") == "Sulafat"` | no (identical) | none |
| 3 | `system_instruction` | `SPIKE_SYSTEM_INSTRUCTION`: "...Normally answer in the language the user is **currently speaking**. If **explicitly asked** to use Polish or English, follow that request..." | prior `CLOUD_ROLE_CARD`: "...**Mirror** the user's language (Polish or English)." — shorter, no "currently speaking" per-turn framing, no explicit-override clause | **YES — the one concrete wording difference found** | **FIXED** — `CLOUD_ROLE_CARD` restored to the spike's explicit per-turn + explicit-override framing (see below) |
| 4 | initial history / context | `LLMContext(messages=[])` (deliberately empty) | `LLMContext(messages=_render_snapshot_messages(snapshot))` — renders `session.history`; for `build_default_session()` on Attempt #1 this was ALSO empty | no, for THIS run (both effectively `[]`); the design differs but manifests identically on a fresh session | none for Attempt #2 (fresh session again) |
| 5 | `history_config` | not set (Pipecat default) | not set (Pipecat default) | no (identical, both rely on the unconditional Gemini-3.1 `HistoryConfig(initial_history_in_client_content=True)` default) | none |
| 6 | `inference_on_context_initialization` | `False` | `False` | no (identical) | none |
| 7 | initial `LLMRunFrame`/kickoff | one `LLMRunFrame` queued once the socket connects (`_kickoff`) | one `LLMRunFrame` queued once the socket connects (`service.py: await self._worker.queue_frames([P["LLMRunFrame"]()])`) | no (identical, same R0031 fix) | none |
| 8 | input transcription config | not set explicitly (Pipecat enables both input+output unconditionally) | not set explicitly (same) | no (identical) | none |
| 9 | output transcription config | not set explicitly (same default) | not set explicitly (same default) | no (identical) | none |
| 10 | server VAD config | `GeminiVADParams(disabled=True)` | `GeminiVADParams(disabled=True)` | no (identical) | none |
| 11 | local VAD framing | `SileroVADAnalyzer(sample_rate=16000, VADParams(stop_secs=0.5))`, wired INSIDE `LLMUserAggregatorParams(vad_analyzer=...)` — the context aggregator runs its OWN internal `VADController` | `SileroVADAnalyzer(sample_rate=16000, VADParams(stop_secs=0.5))`, wired as a STANDALONE `VADProcessor` in a SEPARATE hardware pipeline; `LLMUserAggregatorParams()` has no `vad_analyzer` (`_vad_controller` is `None`) | no — verified from installed Pipecat source (`llm_response_universal.py`): every frame (including an externally-injected plain `UserStartedSpeakingFrame`) reaches `self._user_turn_controller.process_frame(frame)` identically regardless of whether `vad_controller` exists; the aggregator cannot tell a self-generated VAD-frame from an externally-injected one of the same type | none (architecture change is M2.6B.1/.2's own deliberate, already-documented redesign — two independent pipelines bridged by `_VadToProviderBridge`, not a hidden regression) |
| 12 | `activityStart` timing | sent the instant the aggregator's own VAD fires `on_speech_started` | sent the instant `_VadToProviderBridge` sees `VADUserStartedSpeakingFrame` from the hardware pipeline's own VAD, then calls `provider.user_turn_start()` -> pushes a plain `UserStartedSpeakingFrame()` into the provider's headless pipeline | no (same VAD analyzer/params; only the wiring path differs, not the timing source) | none |
| 13 | audio streaming timing/chunking | continuous frames from `LocalAudioTransport.input()` reach `_send_user_audio` at ALL times (even outside a turn) — Gemini's own `_user_audio_preroll_buffer` fills during idle periods and is flushed on turn start, recovering ~0.1-0.3s of speech onset | `_VadToProviderBridge` forwards `InputAudioRawFrame` to `provider.send_user_audio()` **only while `self._turn_open`** — no idle-period audio ever reaches the LLM service, so its own `_user_audio_preroll_buffer` is NEVER populated; the flush on turn start is a no-op | **possibly, for onset clipping** (not proven to cause EN->PL, but a genuine, source-confirmed behavioural difference) | **NOTED, not fixed this checkpoint** — out of scope for the language question; flagged for a future latency/quality checkpoint (see EXACT NEXT TASK) |
| 14 | `activityEnd` timing | sent on the aggregator's own VAD `on_speech_stopped` | sent when the bridge sees `VADUserStoppedSpeakingFrame`, then calls `provider.user_turn_end()` | no (same underlying VAD state machine/params) | none |
| 15 | input sample rate | 16000 Hz | 16000 Hz (`INPUT_SAMPLE_RATE_HZ`) | no (identical) | none |
| 16 | output sample rate | 24000 Hz | 24000 Hz (`OUTPUT_SAMPLE_RATE_HZ`) | no (identical) | none |
| 17 | `GeminiLiveLLMService` construction args | `api_key`, `system_instruction`, `settings=Settings(model, modalities=AUDIO, voice, vad=disabled, context_window_compression=enabled, system_instruction)`, `inference_on_context_initialization=False`, `user_audio_preroll_secs=None` | `api_key`, `system_instruction=snapshot.system_instruction`, `settings=Settings(model, modalities=AUDIO, voice, vad=disabled, context_window_compression=enabled, system_instruction)`, `inference_on_context_initialization=False` (`user_audio_preroll_secs` not passed — installed source default is already `None`, identical) | no (functionally identical after confirming the installed default) | none |
| 18 | `LLMContext` construction | `LLMContext(messages=[])`, one pipeline, aggregator pair built with it | `LLMContext(messages=_render_snapshot_messages(snapshot))`, provider's own headless pipeline | no, for a fresh session (see #4) | none |
| 19 | user/assistant aggregator configuration | `LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(vad_analyzer=...), realtime_service_mode=True)` | `LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(), realtime_service_mode=True)` | no (see #11 — `realtime_service_mode=True` identical; only the `vad_analyzer` presence differs, proven inconsequential) | none |
| 20 | pipeline processor order | ONE pipeline: `[transport.input(), up, user_agg, llm, down, aec_feeder, transport.output(), asst_agg]` | TWO pipelines: hardware `[transport.input(), vad_processor, bargein, bridge, aec_feeder, transport.output()]` + provider's own headless `[up_tap, user_agg, llm, down_tap, asst_agg]`, bridged by plain async calls | no — deliberate, already-documented M2.6B.1/.2 architecture (module docstring), not a hidden change; frame types reaching `llm` are the same either way | none |
| 21 | M2.6A hidden/static language setting | none found (`SPIKE_SYSTEM_INSTRUCTION` is language-neutral; no `language_code`) | none found (`CLOUD_ROLE_CARD` is language-neutral; no `language_code` — native-audio Gemini has no such parameter, per R0039's own official-docs finding) | n/a — confirmed absent on both sides | none |
| 22 | M2.6B introduced language setting | n/a | `apps/nexa_cloud_voice_app.py` never passes `language_preference` to the snapshot builder (defaults to `None`); `build_default_session()` starts with empty history — **re-confirmed from source this checkpoint**, matches R0038's own finding exactly | no — confirmed neutral, no NeXa-side bias | none (re-asserted as a standing regression test, see TEST RESULTS) |
| 23 | session resumption/history behaviour | n/a (session too short to exercise; ~15 min hard cap not reached) | n/a for Attempt #1 (short session, no reconnect observed) | no evidence either way — not exercised in Attempt #1 | none this checkpoint |
| 24 | recent-turn seed behaviour | empty (fresh session, no prior turns) | empty (fresh session via `build_default_session()`, no prior turns) | no (identical for this run) | none |
| 25 | exact role-card wording | see #3 | see #3 | **YES — same finding as #3** | see #3 |
| 26 | Pipecat defaults differing between probe and production | `user_audio_preroll_secs` explicit `None` vs implicit `None` (same); `LLMUserAggregatorParams()` vad_analyzer presence (see #11/#19, proven inconsequential) | (mirror of left column) | no beyond what's already covered above | none |
| 27 | Attempt #1 setup failure changing turn framing | n/a | `_VadToProviderBridge#0` failed Pipecat `setup()` (R0038 FAILURE 1) — but R0038's own log shows "new user turns did reach Gemini" and a normal conversation was possible **during** the failure, i.e. the crash was confined to Pipecat's per-processor metrics lifecycle hook (`self._metrics.setup()`/`.cleanup()`), which `process_frame()`'s own turn-forwarding logic (`user_turn_start`/`send_user_audio`/`user_turn_end`) does not depend on at all | **NO — confirmed independent of the language failure** (see ROOT CAUSE section below) | already fixed in R0038 (renamed `_nexa_metrics`); re-verified here, unchanged |

## LANGUAGE-RELEVANT DIFFERENCES

Exactly **one** concrete, source-level difference was found with a
plausible (not proven) language-relevant mechanism: item #3/#25, the
`system_instruction` wording. Item #13 (pre-roll/onset audio) is a real,
source-confirmed behavioural difference but is an onset-clipping/latency
concern, not a language-routing one, and is left open for a future
checkpoint rather than conflated with this one.

**Old production wording** (`CLOUD_ROLE_CARD`, before this checkpoint):
> "...Mirror the user's language (Polish or English)."

**M2.6A spike wording** (OPERATOR-CONFIRMED, `R0031`):
> "...Normally answer in the language the user is currently speaking. If
> explicitly asked to use Polish or English, follow that request..."

The production wording dropped two things the spike had: (a) explicit
**per-turn** framing ("currently speaking", anchoring the instruction to
each new utterance rather than reading as a static session trait), and
(b) an explicit clause for what to do on an explicit language-switch
request. Per the charter's own instruction ("if the existing M2.6A
wording is measurably different and was better, prefer restoring the
exact accepted M2.6A wording unless it conflicts with a later ADR
requirement"): ADR-0004 Decision E's own text is illustrative of the role
card's *structure* (short role card + response-shape line + optional
language-preference line), not a verbatim mandate — restoring closer
wording does not reverse or conflict with any ADR-0004 decision (Decision
F, Option A/native mirroring, is unchanged either way).

**Fix applied**: `CLOUD_ROLE_CARD` (`src/nexa/realtime/snapshot.py`) now
reads: "...Answer in the language the user is currently speaking, Polish
or English; if the user explicitly asks you to switch, follow that
request." — same length class as before (no larger prompt invented, per
the charter), keeping the ADR-mandated response-shape sentence unchanged.

## ROOT CAUSE / NO-ROOT-CAUSE RESULT

**No proven root cause exists for the EN->PL failures** — proving one
would require a live Gemini call, explicitly forbidden this checkpoint.
What was established, with certainty, from source:

1. The `_VadToProviderBridge` setup/cleanup crash (R0038 FAILURE 1) is
   **confirmed independent** of the language failure: R0038's own live
   log records "new user turns did reach Gemini" and a normal
   conversation was possible throughout the failed run — the crash was
   confined to Pipecat's per-processor `FrameProcessorMetrics.setup()`/
   `.cleanup()` hooks, which `process_frame()`'s turn-forwarding logic
   (the code path that actually drives `user_turn_start`/
   `send_user_audio`/`user_turn_end`, i.e. everything relevant to a
   turn's language) never touches. A broken metrics lifecycle hook cannot
   change what language Gemini answers in.
2. The playback-interruption bug (R0038 FAILURE 2) is architecturally
   unrelated to language routing (it is about audio continuing to play
   after a confirmed barge-in, not about which language is chosen).
3. No hidden/static language bias was found on EITHER side (#21/#22) —
   re-confirmed from source this checkpoint, matching R0038's own finding.
4. The one concrete difference found (#3) is a plausible, low-risk,
   easily-reversible contributing factor — restored to the
   OPERATOR-CONFIRMED wording, not claimed as a proven fix.

**Conclusion: no meaningful architectural regression exists.** The
honest position, per the charter's own decision tree: state this
clearly, restore the one concrete wording difference found, and treat a
CLEAN Attempt #2 (this checkpoint's fixes + R0038's two integration
fixes, all in place, with no crash and no playback bug in the way this
time) as the correct next evidence — not a LID gate.

## R0040 PRODUCT STATUS CORRECTION

**R0040's `ggml-tiny-q5_1` finding is downgraded**, per explicit operator
direction:

- **WAS recorded as**: "SELECTED PRODUCTION CLOUD LANGUAGE ROUTER" /
  DECISION GATE B (R0040's own language, restated for context).
- **IS NOW**: **BEST RESEARCHED FALLBACK CANDIDATE ONLY** — not adopted,
  not wired into any production path, not implemented.
- **Reason** (operator's own, preserved verbatim as the rationale):
  warm raw LID is still ≈961 ms; the "hidden-latency" result was only
  demonstrated on ~3.5 s utterances; the repo's own short-utterance
  fixtures are ~1.344-1.728 s and cannot hide that cost; the
  cross-check evidence set is small (2 pairs, 4 files); and M2.6A already
  proved excellent native language behaviour without any LID. Speed and
  accuracy findings from R0040 remain valid research data — nothing in
  R0040's benchmark methodology or numbers is disputed — only the
  PRODUCT DECISION built on top of them is corrected.
- **Nothing was deleted.** The downloaded models
  (`~/.local/share/nexa/research/lid/ggml-tiny{,-q8_0,-q5_1}.bin`), the
  benchmark script, and all JSON result files remain exactly where R0040
  left them, preserved as a fallback option should a future clean
  production run require it.
- `docs/CURRENT_STATE.md`/`docs/ROADMAP.md` updated (below) so future
  work does not automatically proceed into strict-LID implementation —
  the language-mirroring gap is now framed as "pending a clean Attempt
  #2", not "pending a lighter LID".

## R0038 FIX REGRESSION CHECK

All three R0038 fixes were re-read from source this checkpoint and
**confirmed unchanged**:

- `_VadToProviderBridge`'s `self._nexa_metrics` rename — unchanged
  (`runtime.py`, `_VadToProviderBridge.__init__`).
- `_ResponseGenerationGuard` + generation-gated audio dispatch —
  unchanged (`runtime.py`, `_consume_provider_events`); re-exercised by
  `TestGenerationGuardWiredIntoConsumer` (unchanged) plus a NEW
  `test_consecutive_normal_turns_rearm_bargein_each_time` (this
  checkpoint) proving a SECOND, independent normal turn dispatches and
  finishes identically after the first fully drains — no leftover state
  from turn 1 blocks turn 2.
- Single `provider.events()` consumer, canonical-history safety
  (M2.6B.3B's conservative interrupted-prefix rule) — unchanged, verified
  by the unchanged `TestInterruptedHistorySafety`/
  `TestMidTurnRuntimeRecovery` suites (all still passing).
- "Old generation cannot reach speaker" / "cannot reach AEC": proven by
  construction, not merely by test — `_ResponseGenerationGuard.is_valid()`
  gates the ONE call site (`hw_worker.queue_frames([frame])`) that feeds
  BOTH the speaker and the AEC feeder identically (same hardware
  pipeline, `aec_feeder` stage sits between the bridge and
  `transport.output()`); a chunk that never reaches that queue
  structurally cannot reach either downstream stage. Re-confirmed
  unchanged this checkpoint (existing `TestGenerationGuardWiredIntoConsumer`
  test, still passing).
- **No LID invoked anywhere in the normal cloud critical path**: proven
  by a NEW deterministic test this checkpoint,
  `TestLidNeverBlocksCloudCriticalPath.test_slow_detector_never_delays_turn_dispatch_or_commit`
  — wires a deliberately 5-second-sleeping fake detector as
  `runtime.language_detector` and proves a turn still dispatches/commits
  in well under 1 second; `_analyze_turn_language` is confirmed
  fire-and-forget (`asyncio.create_task`, never awaited inline).

## FILES CHANGED

- `src/nexa/realtime/snapshot.py` — `CLOUD_ROLE_CARD` wording restored
  closer to the OPERATOR-CONFIRMED M2.6A phrasing (explicit per-turn
  "currently speaking" + explicit-override clause); comment records the
  audit rationale. No structural change (still a frozen dataclass, same
  fields, same `build_cloud_context_snapshot` signature).
- `src/nexa/realtime/gemini/runtime.py` — `RuntimeMetrics.canonical_turn_committed`
  gained optional `user_transcript`/`assistant_transcript`/
  `provider_instance_id` keyword args, logged with the charter's exact
  keys (`USER_TRANSCRIPT`/`ASSISTANT_TRANSCRIPT`/`PROVIDER_SESSION_ID`),
  built only from state the turn accumulator already held in memory — no
  new I/O, no added latency, no raw audio, no credential. Module
  docstring gained an M2.6B.4C section recording this checkpoint's
  product decision and audit conclusion. **No other runtime logic
  changed** — `_ResponseGenerationGuard`, `_VadToProviderBridge`,
  `_ResponseLifecycle`, `_consume_provider_events`'s dispatch/guard/
  recovery logic, and the R0038 fixes are byte-for-byte unchanged.
- `tests/test_realtime_gemini_runtime.py` — +7 new tests: 1 consecutive-
  normal-turns re-arm test (`TestBargeInWiring`), 3 system-instruction/
  language-policy tests (`TestSystemInstructionLanguagePolicy`, new
  class), 1 LID-off-critical-path test (`TestLidNeverBlocksCloudCriticalPath`,
  new class), 2 diagnostics-logging tests (`TestCanonicalTurnDiagnostics`,
  new class).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated (below).
- **Zero changes** to `apps/nexa_cloud_voice_app.py`, `service.py`,
  `router.py`, `turn.py`, `provider.py`, `reconnect.py`, or anything under
  `src/nexa/voice/**`/`src/nexa/voice_tts/**`/`src/nexa/stt/**`.

## TEST RESULTS

- `ruff check src/nexa/realtime/snapshot.py src/nexa/realtime/gemini/runtime.py tests/test_realtime_gemini_runtime.py`
  → all checks passed.
- `python -m unittest tests.test_realtime_gemini_runtime` → **39 tests,
  OK** (32 prior + 7 new).
- `python -m unittest discover -s tests` → **930 tests, OK (skipped=7)**
  — up from 923 (the +7 new tests above), zero regressions, zero new
  skips.
- `git diff --check` → clean.
- `pip check` → no broken requirements.

## LOCAL VOICE FREEZE CHECK

`git diff --stat -- src/nexa/voice src/nexa/voice_tts` → **empty**. Local
voice completely untouched this checkpoint.

## COMMIT HASHES

audit/fix/report commit: `b6e8ebc`

(recorded in the follow-up hash-record commit)

## GIT STATUS

Not pushed (standing constraint for the whole M2.6B session).

## EXACT ATTEMPT #2 COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Real reSpeaker + real USB speaker + real Gemini, exactly as Attempt #1.
Wait for `>>> CLOUD_PROVIDER_READY <<<` and `✓ AEC_REF_ACTIVE`, then run
the SHORT scripted coverage below (not a long conversation):

1. **Polish**: "Powiedz mi krótko jak powstaje czarna dziura."
2. **Interrupt it naturally, mid-reply**: "Dobra stop, powiedz mi coś o
   Słońcu." — confirm the OLD audio stops immediately and never resumes.
3. **English**: "Can you explain what happens near the event horizon?"
4. **English again**: "What is the speed of light?"
5. **Polish again**: "A teraz odpowiedz mi po polsku — czym jest
   grawitacja?"

Observe: PL->PL, EN->EN, EN->EN, PL->PL. New per-turn log lines now show
`USER_TRANSCRIPT=`/`ASSISTANT_TRANSCRIPT=`/`PROVIDER_SESSION_ID=` for each
canonical commit — useful for a precise post-hoc read of exactly what was
heard/answered, without needing to trust memory of the session.

## READY LINES

- R0038's two integration fixes (VAD-bridge lifecycle, playback-generation
  guard): **READY**, re-verified unchanged, +1 new re-arm test.
- System-instruction wording: **FIXED** (restored to OPERATOR-CONFIRMED
  M2.6A framing), covered by 2 new deterministic tests.
- No LID on the critical path: **CONFIRMED BY TEST**, deliberately slow
  fake detector proves zero added dispatch latency.
- Retest diagnostics (`USER_TRANSCRIPT`/`ASSISTANT_TRANSCRIPT`/
  `PROVIDER_SESSION_ID`): **READY**, covered by 2 new deterministic tests.
- Local voice: **UNTOUCHED** (empty diff).
- Full suite: **930 tests, OK (skipped=7)**.
- **Do NOT mark hardware acceptance PASS. Do NOT mark M2.6B COMPLETE.**
  Both remain contingent on a clean Attempt #2, run by the operator.

## EXACT NEXT TASK

**M2.6B.5 (pending Attempt #2 result)** — the operator runs the exact
command and coverage above. Two outcomes, per the charter's own decision
tree:

- **Clean pass** (runtime healthy, barge-in fixed, native PL/EN switching
  works): cloud LID is NOT needed; mark hardware acceptance PASS
  (contingent on operator confirmation of audio quality/naturalness, not
  this checkpoint's call), record R0039/R0040 as fallback research only
  (already done here), do not implement strict LID.
- **Repeated EN->PL failure despite clean runtime**: this is now clean,
  repeated evidence that native mirroring is unreliable in production —
  only then reopen the fallback decision and design (not casually
  implement) an R0041-successor strict-mode checkpoint using
  `ggml-tiny-q5_1` per R0040's own sketch.

Separately, noted but explicitly deferred (not urgent, not
language-related): item #13's onset/pre-roll audio difference — a future
checkpoint may want to verify whether the first ~0.1-0.3 s of speech onset
is ever clipped in production (unlike the spike, which recovered it via
Gemini's own pre-roll buffer) — a latency/transcription-completeness
question, not a language-routing one.
