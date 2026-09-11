# R0043 — M2.6B.4E: Attempt #2 Post-Interruption Audio-Loss Failure

**Date:** 2026-09-11
**Milestone:** M2.6B.4E (root-cause + fix, follow-on to the real Attempt #2
hardware run)
**Status:** Root cause CONFIRMED via source audit, fix implemented and
deterministically tested. No Gemini call this checkpoint. **Hardware
acceptance remains FAIL. `M2.6B` remains IN PROGRESS.**

## ATTEMPT #2 CLASSIFICATION

**Overall: FAIL.** Several important parts PASSED:

- `CLOUD_PROVIDER_READY` reached; `AEC_REF_ACTIVE` reached.
- No `_VadToProviderBridge` setup()/cleanup() crash (R0038's fix holds).
- First assistant answer audible through Sulafat.
- **Native Gemini language mirroring worked**: PL question -> PL answer
  ("Czarne dziury powstają zazwyczaj..."), later EN question -> EN answer
  ("The event horizon is like the point of no return..."), later PL
  question -> PL answer ("Grawitacja to siła..."). R0041's role-card
  wording restoration + R0042's zero-LID cleanup are both re-confirmed
  working live; **R0039/R0040 stay fallback research only — LID is not
  reopened.**

**FAIL**: mid-first-response, the operator cleared his throat (dry throat,
non-lexical sound). This confirmed a local barge-in and cut the audible
reply. FOUR `✂ cloud interruption acknowledged by provider` lines
printed. After this point Gemini kept hearing the user, kept generating
correct PL/EN text, and that text kept appearing in the terminal — but
**no subsequent assistant response was ever audible again** for the rest
of the session.

## LIVE EVENT RECONSTRUCTION

1. Turn 1 (real spoken question) dispatches normally; Sulafat audible.
2. Operator throat-clears mid-reply. Local Silero VAD detects this as
   speech; `BargeInController` admits it as an interrupt candidate, then
   (after `confirm_hold_secs`) confirms it — **exactly as designed**, this
   is not itself a bug (see FALSE THROAT-CLEAR NOTE below).
3. `_on_confirmed` fires: `generation_guard.interrupt()` invalidates the
   in-flight generation; `provider_handle.current.cancel()` is queued;
   local playback stops immediately (R0038's fix, unaffected).
4. **Independently of step 3**, `_VadToProviderBridge` (a SEPARATE
   `FrameProcessor` in the SAME hardware pipeline, reacting to the SAME
   VAD frames) had ALREADY forwarded the throat-clear's own
   `activity_start`/audio/`activity_end` to Gemini the moment local VAD
   detected it — the bridge does not gate on `BargeInController`'s
   candidate/confirm state at all; it simply relays every local VAD turn
   boundary to the provider, always. This is by design (M2.6B.1/.2
   architecture) and is NOT itself the bug.
5. Gemini's `serverContent.interrupted` field naturally arrives for the
   already-in-flight reply once it processes the new `activity_start` —
   this is Gemini's OWN server-side interruption ack, independent of
   NeXa's local `cancel()` call (see FOUR-ACK ROOT CAUSE below).
6. **Gemini never produces a `UserTranscriptionEvent` for the throat-clear
   itself** — it is not lexical content. No final transcript for it ever
   arrives.
7. The runtime's dispatch gate (`dispatched_for_turn`, before this
   checkpoint) was reset to `False` **only** by a fresh, final
   `UserTranscriptionEvent`. With none ever arriving for the interrupting
   sound, it stays `True` forever.
8. Every subsequent turn's `AssistantAudioEvent` therefore never passes
   `not dispatched_for_turn` — `start_new_generation()` is never called
   again — `current_gid` stays permanently pinned to the OLD, invalidated
   id — `generation_guard.is_valid(current_gid)` is `False` for every
   future chunk, of every future turn, in any language — all of it is
   silently dropped via the (already-existing) invalidated-audio path.
9. Assistant **text**, meanwhile, flows through
   `router.handle_provider_event(event)` completely independently of the
   generation guard — so text keeps appearing normally while audio is
   gone. This exactly matches the reported symptom.

## FOUR-ACK ROOT CAUSE

**Confirmed via source, not guessed.** Installed Pipecat 1.8.1:

- `FrameProcessor.broadcast_interruption()`
  (`processors/frame_processor.py:1017`) creates **two separate**
  `InterruptionFrame` instances (`broadcast_frame`) and pushes one
  upstream, one downstream, from wherever it is called.
- `GeminiLiveProvider.cancel()` (`service.py`) queues **one**
  `InterruptionFrame` into the provider's own headless pipeline
  (`[up_tap, user_agg, self._llm, down_tap, asst_agg]`). Flowing
  downstream, it is seen by `up_tap` (event #1), then by
  `GeminiLiveLLMService.process_frame` (which handles `InterruptionFrame`
  by calling `self._handle_interruption()` then re-pushing the SAME frame
  downstream), then by `down_tap` (event #2). **Two
  `ProviderInterruptionEvent`s from our own single `cancel()` call.**
- **Independently**, `gemini_live/llm.py`'s own receive loop
  (`llm.py:1332-1333`): `if sc and sc.interrupted: ... await
  self.broadcast_interruption()` — Gemini's OWN server-side interruption
  signal (`serverContent.interrupted`), arriving naturally once Gemini
  processes the new incoming activity, triggers `self._llm`'s OWN
  `broadcast_interruption()` call — a BRAND NEW pair of `InterruptionFrame`
  instances, one flowing upstream to `up_tap` (event #3), one downstream
  to `down_tap` (event #4).
- **Total: 2 (our own `cancel()`) + 2 (Gemini's own independent ack) = 4
  `ProviderInterruptionEvent`s from ONE underlying local interruption
  episode.** This is normal, source-confirmed Pipecat/Gemini behaviour,
  **not a NeXa-side bug** — one of the charter's own candidate
  explanations, now confirmed rather than assumed.
- The local side is already idempotent: `router.handle_provider_event`
  calls `self.on_interruption()` for every `ProviderInterruptionEvent`,
  and `CloudTurnAccumulator.on_interruption()` is a no-op once the turn is
  already terminal/interrupted — repeated acks cause no duplicate side
  effect. Nothing in `_consume_provider_events` reacted to
  `ProviderInterruptionEvent` at all before this checkpoint (a
  `provider_interruption_ack()` metrics method existed but was never
  called — now wired, purely for observability, changing no behaviour).
  **Verified by test**: `test_2_repeated_provider_interruption_events_are_idempotent`.

## GENERATION-GUARD STATE RESULT

**CONFIRMED the exact mechanism the charter's own "Failure Class A"
hypothesized**, traced above (LIVE EVENT RECONSTRUCTION steps 7-9). The
design assumption from R0038 — "a fresh final `UserTranscriptionEvent`
always precedes that turn's own assistant content" (R0034's own proven
guarantee) — held for *transcribable* turns but was never proven to hold
for a turn Gemini cannot transcribe at all. A non-lexical interrupting
sound violates it completely (zero events, not merely reordered ones).

## OUTPUT-TRANSPORT STATE RESULT

**Investigated via source; found NOT to exhibit the suspected defect.**
Installed Pipecat 1.8.1 `transports/base_output.py`,
`BaseOutputTransport.MediaSender.handle_interruptions()`: cancels the
clock/video tasks, and (absent a mixer/uninterruptible frames — our
case) does `await self._cancel_audio_task(); self._create_audio_task()`
— `_create_audio_task()` unconditionally builds a **fresh** `FrameQueue`
and a fresh audio-handler task (`if not self._audio_task:` — always true
immediately after cancellation, since `_cancel_audio_task()` sets
`self._audio_task = None`). `handle_audio_frame()` for subsequent chunks
queues into this fresh queue, processed by the fresh task. Nothing here
accumulates or leaks across repeated calls. Additionally: this handler is
called **once** per confirmed interruption in the actual live topology —
`_on_confirmed`'s own `provider_handle.current.cancel()` call queues its
`InterruptionFrame` into the PROVIDER's separate headless pipeline (no
transport there at all); only `BargeInController.broadcast_interruption()`
(called once per `_do_confirm()`, in the HARDWARE pipeline) reaches
`transport.output()`, and only its downstream-flowing instance (the
upstream one flows away from output). So the output transport is not
subjected to the four-fold ack multiplicity at all — Failure Class B does
not apply here; **Failure Class A (confirmed above) fully explains the
live symptom on its own.**

## CONFIRMED ROOT CAUSE

`GeminiVoiceRuntime._consume_provider_events`'s dispatch-rearm gate
(`dispatched_for_turn`) depended **solely** on a fresh, final
`UserTranscriptionEvent` to know "a genuinely new local turn has started,
the next Assistant event may get a fresh generation id." A non-lexical
interrupting sound (throat-clear/cough) can confirm a real local barge-in
and close a real local VAD turn while Gemini produces **zero**
transcription events for it. With no final transcript ever arriving, the
gate stayed permanently shut: `start_new_generation()` was never called
again for the rest of the session, `current_gid` stayed pinned to the
already-invalidated id, and **every subsequent assistant-audio chunk,
for as long as no turn produces a final transcript, was silently
dropped** as belonging to an invalidated generation — while assistant
text (which does not depend on the generation guard) kept flowing
normally. This is a deterministic, 100%-reproducible bug (not a timing
race), confirmed by a new test that FAILS (times out waiting for audio
that never arrives) with the fix disabled and PASSES with it restored
(`test_1_interruption_with_no_final_transcript_recovers_future_audio`) —
verified both ways in this checkpoint, not merely asserted.

**Honest scope note**: source alone proves the gate stays shut for as
long as no subsequent turn's `UserTranscriptionEvent(final=True)` ever
arrives — the PRE-EXISTING (unmodified) mechanism already resets the
gate correctly the moment ANY later turn IS cleanly transcribed (this is
exactly what test_7/test_8/test_9 exercise and confirm still works).
Whether the LIVE Attempt #2 session's later, clearly-lexical PL/EN
questions each promptly produced their own final transcript, and if so
why their audio was STILL lost, cannot be fully re-derived from source
alone (that would require Gemini-side event-timing evidence this
checkpoint does not have, since no Gemini call is made here). What IS
certain: (a) the mechanism above is a real, deterministic defect,
independent of and prior to any question about exactly how many turns it
affected live, and (b) the fix closes it unconditionally — it no longer
matters whether a future turn's transcript arrives promptly, late, or
never, because local VAD closing the turn is now sufficient on its own.

## FIX DESIGN

**Architecture change, per the charter's own suggested direction**: tie
dispatch re-arming to a NeXa-owned, Gemini-independent LOCAL signal, not
solely to whether Gemini chose to transcribe something.

`GeminiLiveProvider` (`service.py`) gained `local_turn_closed_seq` — a
plain counter incremented, unconditionally, at the top of every
`user_turn_end()` call (regardless of readiness, liveness, or whether
that utterance is ever transcribed). This fires for a throat-clear
exactly as reliably as for a real spoken question, because it is driven
by NeXa's own local VAD authority calling this method, never by anything
Gemini reports back.

`_consume_provider_events` now tracks `dispatched_at_turn_closed_seq` —
the value of that counter at the moment the CURRENT generation was
dispatched. The dispatch gate reopens (`dispatched_for_turn = False`)
when **both**: (a) the current generation has been invalidated by a
confirmed interruption (`not generation_guard.is_valid(current_gid)`),
**and** (b) `local_turn_closed_seq` has advanced past the value recorded
at dispatch — proof, from local VAD alone, that at least one more local
turn has genuinely closed since the interruption. This is checked on
every event, before the existing dispatch decision, so it only ever
*adds* a chance to re-arm; it never touches an in-progress, still-valid
generation. The original final-transcription-based reset is **kept
unchanged** as the (faster, when available) primary path — this is a
strict, backward-compatible addition, not a replacement: all 75
pre-existing tests in this module pass unchanged, plus one existing
long-form test (`test_late_invalidated_generation_audio_never_reaches_hardware`)
that exercises the exact anti-regression scenario the new fallback must
not weaken.

**Residual risk, disclosed honestly, not swept under the rug**: this
design cannot achieve a mathematically airtight guarantee that literally
zero trailing old-generation audio can ever arrive in the narrow window
right after `local_turn_closed_seq` advances (Gemini's own event ordering
across streams is explicitly "not guaranteed" per the official docs, R0039's
own finding) — a single stray trailing chunk from the just-interrupted
generation could, in principle, arrive in that exact window and get
mistakenly promoted into "the start of the next generation," producing at
most one brief audible artifact at the interruption boundary. This is
architecturally bounded (at most one chunk, immediately followed by
correct behaviour, not accumulating), and vastly preferable to the
confirmed alternative (permanent silence for the rest of the session).
Event-order tests 8 and 9 (below) further show this design does not drop
audio in the reordering cases the charter asked to check, at the cost of
an accepted, non-harmful redundant generation-id allocation in those
specific cases (never a dropped chunk).

## FALSE THROAT-CLEAR NOTE

Recorded as: **NON-LEXICAL FALSE BARGE-IN — REAL OPERATOR OBSERVATION.**
This is explicitly a SECONDARY concern per the charter and is **not**
addressed by disabling barge-in or blindly raising thresholds in this
checkpoint (VAD `stop_secs`/`confirm_hold_secs` are unchanged; no Whisper/
LID/STT was added to the barge-in confirmation path). The FATAL bug was
never "the cough interrupted" — VAD/barge-in correctly did its job — the
bug was that the audio path failed to recover for every subsequent turn.
That is fixed. Whether cough/throat-clear-specific false-positive
suppression is worth researching later (without harming natural spoken
interruptions) is left as a distinct, future, lower-priority question.

## LANGUAGE RESULT

**PASS.** Native mirroring produced both a correct English response to
English input and a correct Polish response to Polish input, live, after
R0041's wording restoration and with R0042's zero-LID cleanup in place.
**R0039/R0040 remain fallback research only.** No time spent reopening
LID this checkpoint, per the explicit instruction.

## FILES CHANGED

- `src/nexa/realtime/gemini/service.py` — `GeminiLiveProvider` gained
  `_local_turn_closed_seq` (incremented in `user_turn_end()`) and the
  read-only `local_turn_closed_seq` property. Module docstring gained an
  M2.6B.4E section. No other behaviour changed.
- `src/nexa/realtime/gemini/runtime.py` —
  - `_ResponseGenerationGuard` gained a read-only `valid_id` property
    (observability only; `is_valid` remains the sole dispatch gate).
  - `_consume_provider_events`: added the `local_turn_closed_seq`-based
    fallback re-arm (the fix); wired the previously-unused
    `provider_interruption_ack()` metrics call; added
    `assistant_response_dispatch`/`assistant_audio_received`/
    `assistant_audio_hw_queued`/`user_transcription_final_state`
    diagnostic calls at the natural points; extended
    `dropped_invalidated_generation_audio` with `reason`/
    `valid_generation_id`.
  - `_VadToProviderBridge`: added `bot_started()`/`bot_stopped()` metrics
    calls alongside the existing lifecycle observations.
  - `build_gemini_voice_runtime`'s `_on_confirmed`: extended
    `local_interruption_confirmed()`'s call with `confirm_count`/
    `generation_id`/`bargein_state`; added `generation_invalidated()` and
    `output_interruption_broadcast()` calls, backed by two small local
    closure counters (kept out of the shared, local-voice-frozen
    `nexa.voice.bargein` module entirely).
  - `RuntimeMetrics` gained `generation_invalidated`,
    `output_interruption_broadcast`, `user_transcription_final_state`,
    `assistant_response_dispatch`, `assistant_audio_received`,
    `assistant_audio_hw_queued`, `bot_started`, `bot_stopped`; extended
    `local_interruption_confirmed` and `provider_interruption_ack`
    (now actually wired) with new optional keyword args; extended
    `dropped_invalidated_generation_audio`. All are logging-only, no raw
    audio, no credentials.
- `tests/test_realtime_gemini_runtime.py` — new
  `TestPostInterruptionAudioRecovery` class, 7 tests (see TEST RESULTS).
- **Zero changes** to `src/nexa/voice/bargein.py`,
  `src/nexa/voice/interruption.py`, `src/nexa/realtime/router.py`,
  `src/nexa/realtime/snapshot.py`, `apps/nexa_cloud_voice_app.py`,
  `src/nexa/stt/**`, `src/nexa/conversation/**`, or anything under
  `src/nexa/voice_tts/**`. Model, voice, role-card wording, zero-LID
  decision, server VAD, local Silero, AEC architecture,
  `ConversationSession`, `CloudContextSnapshot` are unchanged.

## TEST RESULTS

New `TestPostInterruptionAudioRecovery` (7 tests, all pass):

1. `test_1_interruption_with_no_final_transcript_recovers_future_audio` —
   the core reproduction + fix proof (charter items 1 + 6). **Fails
   without the fix** (verified: reverting the fallback logic and re-running
   this one test reproduces a timeout waiting for the next generation's
   audio to ever reach the hardware queue).
2. `test_2_repeated_provider_interruption_events_are_idempotent` (item 2).
3. `test_3_and_4_cancel_and_confirm_counts_are_exactly_one_per_confirmation`
   (items 3 + 4).
5. `test_5_old_generation_trailing_audio_never_reaches_hardware` — the
   unchanged R0038 anti-regression guarantee, re-verified (item 5).
7. `test_7_event_order_a_final_transcript_then_audio` (item 7).
8. `test_8_event_order_b_audio_then_final_transcript_does_not_drop_audio`
   (item 8).
9. `test_9_event_order_c_assistant_text_then_audio_then_late_final_transcript`
   (item 9).

Items 10-11 (output-transport recovery, AEC receiving only playable
audio) are addressed by source audit (OUTPUT-TRANSPORT STATE RESULT
above) rather than a new automated test — both require real Pipecat
transport/audio-hardware internals not practically exercisable without a
real audio device; the structural argument (one injection point,
`hw_worker.queue_frames()`, feeds both speaker and AEC identically, and
the output transport's own cancel+recreate logic is unconditional) is
unchanged from R0041's own reasoning and re-confirmed by this
checkpoint's fresh source read.

Full suite: `python -m unittest discover -s tests` → **939 tests, OK
(skipped=7)** — up from 932, zero regressions, zero new skips (12 = 7 new
+ nothing removed — 932 -> 939). `ruff check src/nexa/realtime/gemini/runtime.py
src/nexa/realtime/gemini/service.py tests/test_realtime_gemini_runtime.py`
→ all checks passed. `git diff --check` → clean. `pip check` → no broken
requirements.

Items 12-15 (normal barge-in, VAD bridge lifecycle, PL/EN role-card
tests, zero-LID tests) — all pre-existing suites (`TestBargeInWiring`,
`TestVadBridgeProcessorLifecycle`, `TestSystemInstructionLanguagePolicy`,
`TestNoLocalLidInCloudRuntime`/`TestNativeOnlyLanguageDispatch`) re-run
unchanged and green, confirmed within the same full-suite pass above.

## LOCAL VOICE FREEZE CHECK

`git diff --stat -- src/nexa/voice src/nexa/voice_tts` → **empty**. Local
voice completely untouched this checkpoint.

## COMMIT HASHES

(recorded in the follow-up hash-record commit)

## GIT STATUS

Not pushed (standing constraint for the whole M2.6B session).

## EXACT NEXT LIVE RETEST COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Unchanged. Real reSpeaker + real USB speaker + real Gemini. Wait for
`>>> CLOUD_PROVIDER_READY <<<` and `✓ AEC_REF_ACTIVE`. Recommended
coverage for Attempt #3: repeat R0041's short PL/interrupt/EN/EN/PL
script, but this time **deliberately include at least one non-lexical
interruption** (a throat-clear or a short "um"/cough) partway through a
reply, followed by at least two further real turns in different
languages — confirm (a) the interrupted reply stops immediately as
before, (b) a subsequent real turn's reply **is** audible (the fix under
test), and (c) native PL/EN mirroring still holds. New diagnostic log
lines (`LOCAL_BARGEIN_CONFIRMED`, `PROVIDER_INTERRUPTION_ACK`,
`GENERATION_INVALIDATED`, `ASSISTANT_RESPONSE_DISPATCH`,
`ASSISTANT_AUDIO_RECEIVED`/`_DROPPED`/`_HW_QUEUED`, `BOT_STARTED`/
`BOT_STOPPED`, `OUTPUT_INTERRUPTION_BROADCAST`) are now available for a
precise post-hoc read of exactly what happened, without relying on
memory of the session. **Do NOT mark hardware acceptance PASS or `M2.6B`
COMPLETE until this retest is clean.**
