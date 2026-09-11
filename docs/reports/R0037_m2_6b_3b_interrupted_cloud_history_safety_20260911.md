# R0037 — M2.6B.3B: Interrupted Cloud History Safety Only

## TASK RESULT

**PASS (narrow deterministic checkpoint).** R0036's
`_SpokenPrefixHighWater` one-audio-chunk-lag mechanism was found to still
overclaim what it could prove — a later chunk's mere existence does not
establish how much of an *earlier* text snapshot that chunk's own audio
actually covers. A further source check for a real provider-supported
alignment mechanism (word-level timing) found one exists in the
underlying SDK's type schema but is **not reachable** through the
installed Pipecat service NeXa wraps. **Conclusion: no deterministic
assistant-text/audio alignment exists in the integrated stack.** The v1
rule for an interrupted cloud turn is now fully conservative: the
committed assistant text is unconditionally empty. Normal, non-interrupted
completions are unaffected. **No cloud call, no hardware test.** Playback
lifecycle (R0036 §1), AEC, reconnect, router architecture, the Gemini
model/voice, VAD, and barge-in thresholds were **not** reopened — only the
spoken-prefix mechanism from R0036 §2 was touched.

## AUDIO/TEXT ALIGNMENT EVIDENCE

**Why R0035/R0036's mechanisms were both insufficient**, concretely:

- R0035: used the raw, running `CloudTurnAccumulator.assistant_text`
  directly. Disproven by R0036 (Pipecat's own source comment: output
  transcription can arrive ahead of, and contain more text than, the
  audio produced).
- R0036: promoted a one-audio-chunk-lag snapshot (text-as-of-the-previous-
  chunk, once a new chunk arrives). **This checkpoint's finding: still
  insufficient.** Consider transcription snapshot `"abcdef ghijkl
  mnop..."` with audio chunk #1 covering only "abc" and chunk #2 covering
  only "def" — chunk #2's mere arrival says nothing about whether chunk
  #1's audio actually covered the *entire* snapshot text that existed at
  the time chunk #1 arrived (it might cover only "abc" of "abcdef ghijkl
  mnop..."). The one-chunk-lag rule would have credited the *whole*
  snapshot-as-of-chunk-1 the moment chunk #2 arrived — an unproven,
  potentially-inflated credit. This is a logical gap in R0036's own
  reasoning, not a new source finding contradicting it — the source
  evidence R0036 cited (transcription arrives ahead of / overshoots audio)
  was correct; the mechanism built on top of it was not conservative
  enough.

**New search for a real, provider-supported alignment mechanism** —
re-inspected both the installed Pipecat 1.8.1 service and the underlying
`google-genai` SDK's own type schema:

- `google.genai.types.Transcription` (installed package,
  `google/genai/types.py:2201`) **does** carry a `words:
  Optional[list[WordInfo]]` field, described as "Detailed word-level
  transcriptions and timing details." `WordInfo`
  (`google/genai/types.py:2162`) carries `word`, `start_offset`, and
  `end_offset` — "Start/End offset in time of the word relative to the
  start of the audio." This is real, potentially-deterministic per-word
  timing data defined in the SDK's own schema.
- **However**, `pipecat/services/google/gemini_live/llm.py`'s
  `_handle_msg_output_transcription` (the only place Pipecat reads a
  `server_content.output_transcription` message) extracts **only**
  `message.server_content.output_transcription.text` — grepped the entire
  file for `.words`/`WordInfo`: **zero occurrences**. Pipecat never reads
  or forwards the `words` field to any frame; `TTSTextFrame` (what
  actually reaches `GeminiLiveProvider._translate_frame`) carries only
  `text` and an aggregation-type flag, nothing else.
- **Conclusion**: this per-word timing data is *defined* by the SDK but
  **not wired through** the Pipecat service NeXa's `GeminiLiveProvider`
  is built on. Using it would require bypassing `GeminiLiveLLMService`'s
  own frame translation and parsing raw `LiveServerMessage`s directly —
  a second, parallel path around the very abstraction ADR-0004 relies on,
  and explicitly out of scope for this task ("do not reopen ... Gemini
  model"; the standing ADR-0004 rule already forbids a second raw Gemini
  client). It is also unverified whether the Gemini Live API actually
  *populates* this field for `gemini-3.1-flash-live-preview` in practice
  — confirming that would require a live call, which this checkpoint does
  not make.
- No other timing/duration/offset/alignment field was found anywhere in
  `gemini_live/llm.py` relating output text to output audio.

**Decision, per the charter's own framework**: no deterministic,
already-integrated alignment exists. Adopted the conservative v1 rule.

## INTERRUPTED HISTORY RULE

**Cloud interrupted-prefix precision: CONSERVATIVE / NO FALSE FUTURE
TEXT.** An interrupted cloud turn's committed assistant text is now
unconditionally `CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""` (new
module-level constant, `src/nexa/realtime/gemini/runtime.py`) —
`_on_confirmed` calls `router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)`
then `router.on_interruption()`, unconditionally, regardless of how many
audio chunks arrived or how much transcription text had accumulated.
`_SpokenPrefixHighWater` (the R0036 class) is deleted — dead weight for a
mechanism now known to be unsound, not a design worth preserving alongside
a correction.

**Under-crediting an interrupted reply is acceptable in v1
(the assistant side of that turn's canonical history may simply be
absent). Crediting words that were never actually spoken is not.**
Concretely: an interrupted turn now always commits
`ExternalExchangeOutcome.COMMITTED_USER_ONLY` (the user's turn is
preserved; no assistant canonical text is ever recorded for that turn),
regardless of how far the reply's transcription or audio had progressed
before the interruption. This is a deliberate, documented v1 trade-off,
not an oversight — a future, source-evidenced alignment mechanism (e.g. if
Pipecat is later found or patched to forward `WordInfo`, or a live test
proves the field is actually populated) could recover partial credit; none
is claimed now.

Normal, non-interrupted completions are entirely unaffected — they still
commit the full final assistant transcription via `GenerationCompleteEvent`
exactly as before (unchanged code path, unchanged test:
`test_5_normal_non_interrupted_turn_stores_full_final_text`).

## PROACTIVE RECONNECT NOTE

Unchanged from R0036: `should_proactively_reconnect()` still has **no
production caller** — remains explicitly documented as deferred, not
implemented in this checkpoint. Per the task's own instruction, this does
**not** block the short operator hardware acceptance run, but `M2.6B`
must **not** be marked fully COMPLETE after that run until the production
reconnect/age-trigger acceptance gate is either implemented and
deterministically validated, or explicitly changed by an ADR amendment.
This is a standing condition on M2.6B completion, not something this
checkpoint resolves.

## FILES CHANGED

- **Modified:** `src/nexa/realtime/gemini/runtime.py` — deleted
  `_SpokenPrefixHighWater`; added module-level
  `CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX = ""`; `_on_confirmed` now
  passes that constant unconditionally instead of a computed high-water
  value; `GeminiVoiceRuntime` dataclass lost the `prefix_tracker` field;
  `_consume_provider_events` lost the `prefix_tracker.reset()`/
  `on_audio_chunk(...)` calls (dead once the mechanism was removed);
  `build_gemini_voice_runtime` lost the `prefix_tracker` local and its two
  constructor-call sites; module docstring §2 rewritten to record this
  correction and its evidence in place of the R0036 claim.
- **Modified (tests):** `tests/test_realtime_gemini_runtime.py` —
  `TestSpokenPrefixHighWater` (6 tests, R0036) replaced by
  `TestInterruptedHistorySafety` (5 tests, matching the charter's own
  numbered scenarios 1-5 exactly); `TestBargeInWiring._wire` and its
  `on_confirmed` hook updated to use the constant instead of a tracker;
  one `TestBargeInWiring` test gained an explicit assertion that the
  committed `assistant_text` is `""` after a confirmed interruption;
  `TestMidTurnRuntimeRecovery`'s construction helper dropped the unused
  `prefix_tracker`.
- **New:** `docs/reports/R0037_m2_6b_3b_interrupted_cloud_history_safety_20260911.md`
  (this report).
- **Unmodified:** `src/nexa/realtime/router.py`, `src/nexa/realtime/gemini/service.py`,
  `src/nexa/realtime/turn.py` (`CloudTurnAccumulator` itself untouched —
  `set_spoken_prefix`/`on_interruption` are unchanged, only what the
  *caller* passes changed), every local-voice file, the playback-lifecycle
  (`_ResponseLifecycle`) and mid-turn-recovery (`_ProviderHandle`,
  `_consume_provider_events`'s recovery branch) mechanisms from R0036.

## TEST RESULTS

Charter's 9 required proofs:

1. **Transcript far ahead of first audio → no unproven future text**:
   `test_1_transcript_far_ahead_of_first_audio_commits_no_future_text` —
   a full paragraph of look-ahead transcription, zero audio chunks,
   interrupted → `COMMITTED_USER_ONLY`.
2. **Multiple audio chunks alone do not prove the whole snapshot spoken**:
   `test_2_multiple_audio_chunks_do_not_prove_the_whole_snapshot_spoken` —
   the exact scenario R0036's high-water mechanism treated as safe
   (two transcription deltas, audio "arriving" between them) still
   commits no assistant text.
3. **No trustworthy aligned prefix → USER ONLY commit**:
   `test_3_interruption_with_no_trustworthy_prefix_is_user_only_commit`.
4. **Late transcription after interruption remains ignored**:
   `test_4_late_transcription_after_interruption_remains_ignored`.
5. **Normal completed response still stores complete assistant text**:
   `test_5_normal_non_interrupted_turn_stores_full_final_text` (unchanged
   from R0036).
6. **Barge-in still stops playback normally**: unchanged
   `TestBargeInWiring` (4 tests, `BargeInController`/`broadcast_interruption`
   untouched), now with an added explicit assertion that the committed
   prefix is `""`.
7. **Playback lifecycle from R0036 remains unchanged**: unchanged
   `TestResponseLifecycle` (6 tests, `_ResponseLifecycle` untouched by
   this checkpoint) — all still pass.
8. **Mid-turn provider recovery remains unchanged**: unchanged
   `TestMidTurnRuntimeRecovery` (1 test, `_ProviderHandle`/recovery-driving
   logic untouched) — still passes.
9. **Baseline remains green**: full suite **910 tests, OK (skipped=7)** —
   911 (R0036 baseline) with `TestSpokenPrefixHighWater`'s 6 tests
   replaced by `TestInterruptedHistorySafety`'s 5 (a deliberate,
   documented net -1 from consolidating onto the charter's own 5-item
   list, not a dropped/skipped test), **zero regressions** in any
   previously-passing test.

Also: `python -m unittest tests.test_realtime_gemini_runtime -v`: **19
tests, OK**. `ruff check src tests apps`: all checks passed. `git diff
--check`: clean. `pip check`: clean. Import-isolation (in-process):
`nexa.realtime.router`/`.policy` alone never pull
`nexa.realtime.gemini`/`.gemini.runtime` into `sys.modules`. Secret scan:
no literal key/token patterns in any changed file.
`apps/nexa_cloud_voice_app.py --dry`: run live in this sandbox — object
graph constructs cleanly, no audio device or network touched.

## LOCAL VOICE FREEZE CHECK

No file under `nexa.voice.*`, `nexa.voice_tts.*`, `nexa.stt.*`,
`nexa.tts.*`, or `nexa.voice_conversation.*` was modified. Untouched by
this checkpoint: playback lifecycle (`_ResponseLifecycle`), AEC
(`AecReferenceFeeder`/`AecReferenceHealth`), reconnect/mid-turn-recovery
(`_ProviderHandle`, `ConversationRouter.recover_from_mid_turn_loss`),
router architecture (`ConversationRouter` itself), the Gemini model/voice
selection (`gemini-3.1-flash-live-preview`/`Sulafat`), VAD, and
`BargeInController`'s own thresholds/behaviour. Full regression suite
green (910/910, 0 new failures beyond the pre-existing 7 skips) is the
acceptance evidence.

## KNOWN RISKS (carried + new)

- **Not hardware/Gemini-tested.** Unchanged — Phase 7 still requires the
  operator.
- **Proactive reconnect has no caller** (unchanged from R0036) — a
  standing condition on `M2.6B` COMPLETE, not resolved by this or any
  prior M2.6B.3x checkpoint.
- **Interrupted cloud replies now always lose their assistant-side
  canonical text**, even in cases where a human listener would agree a
  meaningful, safely-creditable prefix was genuinely spoken (e.g. a
  multi-second reply interrupted near its very end). This is the accepted
  v1 trade-off — the alternative (crediting unproven future text) is
  considered strictly worse for canonical-history integrity.
- A future, source-evidenced alignment mechanism (verified `WordInfo`
  population via a live call, or a Pipecat version/patch that forwards
  it) could recover partial credit in a later milestone — not attempted
  here.

## WHAT REMAINS FOR M2.6B

Unchanged: Phase 7 (real Gemini + real hardware operator acceptance) is
the one remaining step before `M2.6B` can be marked COMPLETE — **and,
per this checkpoint's explicit note, `M2.6B` must not be marked COMPLETE
after that run until the proactive-reconnect/age-trigger gate is either
implemented+validated or explicitly changed by an ADR amendment.**

## EXACT LIVE LAUNCH COMMAND

Unchanged from R0035/R0036:

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

```
.venv/bin/python apps/nexa_cloud_voice_app.py --dry   # sanity check only
```

## READY LINES

Unchanged from R0035/R0036 — wait for both before speaking:

```
· PROVIDER READINESS: READY
>>> CLOUD_PROVIDER_READY <<<
```

```
  ✓ AEC_REF_ACTIVE
```

## COMMIT HASHES

Narrow hardening commit: `52d152a` — "fix(m2.6b.3b): interrupted cloud
history now unconditionally conservative (R0037)". Not pushed.

## GIT STATUS

Not pushed (per the standing constraint for this entire M2.6 body of
work). All files listed under FILES CHANGED are committed at `52d152a`.
