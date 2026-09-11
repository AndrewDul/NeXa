# R0042 — M2.6B.4D: Remove Cloud LID Runtime Cost Before Attempt #2

**Date:** 2026-09-11
**Milestone:** M2.6B.4D (narrow follow-on to M2.6B.4C / R0041)
**Status:** Deterministic checkpoint only. No Gemini call. No hardware.
`M2.6B` remains IN PROGRESS. Hardware acceptance NOT marked PASS.

## TASK RESULT

R0041 correctly decided NO local LID gate in the normal cloud critical
path, but the runtime still **constructed** a `WhisperCppLanguageDetector`
and **invoked** it fire-and-forget once per closed utterance
(`_analyze_turn_language`). R0041's own test proved only that the event
loop does not *await* a slow fake coroutine inline — it did NOT prove
that a REAL whisper.cpp CPU-bound inference call (genuinely
~0.5-1.7s of native-code CPU work per the R0039/R0040 benchmarks) has
zero impact on Pipecat's own scheduling, audio playback, AEC, VAD, or
realtime response latency once actually exercised on a resource-
constrained Raspberry Pi. Per the operator's explicit instruction —
**"normal cloud voice must run with ZERO local LID inference"** — this
checkpoint removes the construction and invocation entirely, not merely
fails to await it.

## CLOUD LID REMOVAL RESULT

Removed from `src/nexa/realtime/gemini/runtime.py`:

- `build_gemini_voice_runtime` no longer imports or constructs
  `nexa.stt.WhisperCppLanguageDetector` or
  `nexa.conversation.ResponseLanguageResolver` — that whole try/except
  construction block is deleted. No whisper.cpp model load, no CPU/RAM
  footprint, at construction time.
- `GeminiVoiceRuntime` dataclass no longer has `language_detector` /
  `language_resolver` fields.
- `_analyze_turn_language` method — deleted entirely.
- The `UserTranscriptionEvent(final=True)` branch in
  `_consume_provider_events` no longer pops pending audio or schedules
  `asyncio.create_task(self._analyze_turn_language(...))` — it now only
  resets `dispatched_for_turn` and logs `input_transcription_final`,
  exactly as before those two lines existed.
- `RuntimeMetrics.language_diagnostics` — deleted (its only caller was
  the deleted `_analyze_turn_language`).

**Verified by test, not just by inspection**:
`TestNoLocalLidInCloudRuntime.test_runtime_never_constructs_whisper_cpp_language_detector`
patches `WhisperCppLanguageDetector.__init__` to record every call, then
builds a full runtime (`dry=True`) and asserts zero calls.
`test_runtime_has_no_lid_related_fields_at_all` asserts the returned
`GeminiVoiceRuntime` instance has no `language_detector`/
`language_resolver`/`pending_utterance_audio` attribute at all — nothing
left for a future code path to accidentally wire up.
`TestNativeOnlyLanguageDispatch.test_pl_then_en_turns_dispatch_natively_with_zero_lid_and_no_provider_swap`
patches `WhisperCppLanguageDetector.detect` to raise
`AssertionError` if ever called, then drives a REAL Polish-content turn
followed by a REAL English-content turn through the actual consumer loop
— the test would fail loudly if LID were invoked at any point.

## RUNTIME CPU-PATH RESULT

Normal cloud voice now spends **zero** CPU on PL/EN classification for
every turn (previously ~0.5-1.7s of whisper.cpp inference per turn, per
R0039/R0040's own measurements, even though it never blocked the event
loop). `activityEnd` (`provider.user_turn_end()`) is never held waiting on
anything language-related — verified directly:
`test_pl_then_en_turns_dispatch_natively_with_zero_lid_and_no_provider_swap`
times each `user_turn_end()` call and asserts it returns in under 50ms,
for both the Polish-content and the English-content turn. No provider is
ever replaced because of language — the same test asserts
`runtime.provider is provider_before` after both turns complete. Gemini
native mirroring (ADR-0004 Option A) is the sole language mechanism; no
code path in the normal cloud runtime references language at all.

## RECOVERY BUFFERING CHECK

Carefully inspected before removing anything, per the charter's own
explicit caution ("do NOT remove it if reconnect/fresh-session recovery
still requires it"):

- `_PendingUtteranceAudio` (the class this checkpoint deletes) had
  **exactly one** caller anywhere in `src/nexa/realtime/`: the now-deleted
  `_analyze_turn_language`. Confirmed by grep across the whole
  `src/nexa/realtime/` tree before deletion — no reconnect/mid-turn-
  recovery code ever referenced it.
- `_VadToProviderBridge`'s parallel `self._utterance_buffer` (accumulated
  purely to feed `_PendingUtteranceAudio.push()`) is likewise deleted —
  it was never read by anything except that one push call.
- **Production audio-recovery buffering is a completely SEPARATE,
  untouched mechanism**: `GeminiLiveProvider.take_pending_audio()`
  (`src/nexa/realtime/gemini/service.py`) is backed by the provider's own
  `UtteranceFramer` (ADR-0004 Decision H — captures the local turn
  envelope while `readiness != READY`, flushed once ready again) and is
  what `ConversationRouter.recover_from_mid_turn_loss` (`router.py`)
  actually replays into a fresh provider after a mid-turn session loss.
  **Zero lines changed in `service.py` or `router.py` this checkpoint**
  (`git diff --stat` on both is empty) — recovery functionality is
  provably unaffected.

## R0038/R0041 FIX REGRESSION CHECK

Re-run, unchanged:

- `TestGenerationGuardWiredIntoConsumer` (R0038 generation guard) — PASS.
- `TestVadBridgeProcessorLifecycle` (VAD bridge setup/cleanup lifecycle)
  — PASS. (`test_runtime_metrics_never_receives_a_setup_or_cleanup_call`'s
  own regression-guard assertion was retargeted from the now-deleted
  `language_diagnostics` method name to `local_vad_start` — a method that
  still exists on `RuntimeMetrics` — so the test keeps proving the same
  thing: Pipecat's own `FrameProcessorMetrics` object never acquires a
  NeXa-specific method.)
- `TestBargeInWiring.test_consecutive_normal_turns_rearm_bargein_each_time`
  (R0041's new re-arm test) — PASS.
- `TestSystemInstructionLanguagePolicy` (R0041's `CLOUD_ROLE_CARD`
  wording tests) — PASS, unaffected (that file was not touched this
  checkpoint).
- `TestCanonicalTurnDiagnostics` (R0041's
  `USER_TRANSCRIPT`/`ASSISTANT_TRANSCRIPT`/`PROVIDER_SESSION_ID`
  diagnostics) — PASS, unaffected (`canonical_turn_committed` was not
  touched this checkpoint).

## R0039/R0040 FALLBACK RESEARCH PRESERVED

Nothing deleted: `~/.local/share/nexa/research/lid/ggml-tiny*.bin`, the
benchmark script
(`docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark.py`), and
all JSON result files remain exactly where R0040 left them. They remain
**FALLBACK RESEARCH ONLY** — not wired into any production path, not
implemented, unchanged status from R0041.

## FILES CHANGED

- `src/nexa/realtime/gemini/runtime.py` —
  - Deleted `_PendingUtteranceAudio` class.
  - `_VadToProviderBridge`: removed `pending_utterance_audio` param and
    `self._utterance_buffer` accumulation (never read by anything else).
  - `GeminiVoiceRuntime`: removed `pending_utterance_audio` /
    `language_detector` / `language_resolver` fields.
  - `_consume_provider_events`: removed the pending-audio pop + LID
    task-scheduling lines from the `UserTranscriptionEvent(final=True)`
    branch; docstring updated.
  - Deleted `_analyze_turn_language` method.
  - Deleted `RuntimeMetrics.language_diagnostics` method.
  - `build_gemini_voice_runtime`: removed `_PendingUtteranceAudio()`
    construction, removed the `WhisperCppLanguageDetector`/
    `ResponseLanguageResolver` construction block, removed the now-gone
    fields from both `GeminiVoiceRuntime(...)` construction call sites
    (dry and live) and from the bridge construction call.
  - Module docstring: R0038 §3 and R0041's own M2.6B.4C paragraph updated
    to point forward at this removal; new M2.6B.4D section added
    recording the rationale and exact mechanism preserved
    (`take_pending_audio`/recovery, untouched).
- `tests/test_realtime_gemini_runtime.py` —
  - Removed the `_PendingUtteranceAudio` import and all
    `pending_utterance_audio=...` construction kwargs (4 call sites).
  - `TestVadBridgeProcessorLifecycle`'s regression-guard assertion
    retargeted from `language_diagnostics` to `local_vad_start`.
  - Replaced R0041's `TestLidNeverBlocksCloudCriticalPath` (1 test, a
    slow-fake-coroutine proof) with two new classes:
    `TestNoLocalLidInCloudRuntime` (2 tests: never-constructs,
    no-lid-fields) and `TestNativeOnlyLanguageDispatch` (1 test:
    PL-then-EN native dispatch with zero LID calls, no provider swap,
    `activityEnd` never held).
- **Zero changes** to `src/nexa/realtime/gemini/service.py`,
  `src/nexa/realtime/router.py`, `src/nexa/realtime/snapshot.py`,
  `apps/nexa_cloud_voice_app.py`, or anything under
  `src/nexa/voice/**`/`src/nexa/voice_tts/**`/`src/nexa/stt/**`/
  `docs/research/**`.

## TEST RESULTS

- `ruff check src/nexa/realtime/gemini/runtime.py tests/test_realtime_gemini_runtime.py`
  → all checks passed.
- `python -m unittest tests.test_realtime_gemini_runtime` → **41 tests,
  OK** (39 prior − 1 replaced + 3 new).
- `python -m unittest discover -s tests` → **932 tests, OK (skipped=7)**
  — up from 930, zero regressions, zero new skips.
- `git diff --check` → clean.
- `pip check` → no broken requirements.

## LOCAL VOICE FREEZE CHECK

`git diff --stat -- src/nexa/voice src/nexa/voice_tts` → **empty**. Local
voice completely untouched this checkpoint (as in R0041).

## COMMIT HASHES

(recorded in the follow-up hash-record commit)

## GIT STATUS

Not pushed (standing constraint for the whole M2.6B session).

## EXACT ATTEMPT #2 COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Unchanged from R0041 — real reSpeaker + real USB speaker + real Gemini.
Wait for `>>> CLOUD_PROVIDER_READY <<<` and `✓ AEC_REF_ACTIVE`, then run
the short scripted coverage from R0041 (Polish question -> interrupt
naturally with a new Polish question, confirm old audio stops and never
resumes -> English -> English -> Polish again). This is now a genuinely
clean experiment: no background whisper.cpp CPU load competing with
Pipecat/audio/AEC/VAD during the run.

## READY LINES

- Cloud LID: **REMOVED** (construction + invocation), confirmed by test,
  not just by inspection.
- Runtime CPU path: **ZERO local LID inference** in the normal cloud
  turn — confirmed by test (patched `.detect()` raises if ever called).
- Recovery buffering: **UNAFFECTED** — `take_pending_audio`/
  `recover_from_mid_turn_loss` are a separate, untouched mechanism;
  `service.py`/`router.py` have zero diff this checkpoint.
- R0041's other changes (`CLOUD_ROLE_CARD` wording, per-turn
  diagnostics, R0038 fixes): **UNCHANGED**, re-verified still passing.
- R0039/R0040 fallback research: **PRESERVED**, still fallback-only.
- Local voice: **UNTOUCHED**.
- Full suite: **932 tests, OK (skipped=7)**.
- **Do NOT mark hardware acceptance PASS. Do NOT mark M2.6B COMPLETE.**
  Both remain contingent on the operator's clean Attempt #2.
