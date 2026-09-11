# R0036 — M2.6B.3A: Final Pre-Live Playback/Barge-In/Recovery Hardening

## TASK RESULT

**PASS (deterministic checkpoint).** All three production-significant
seams the charter flagged in R0035 as needing verification/fixing before
operator acceptance were investigated against the installed Pipecat 1.8.1
source and NeXa's own accepted local-voice patterns, and are now fixed and
tested. **No cloud call, no hardware test, no reconnect-duration/quota
test.** Local voice remains frozen — no file under `nexa.voice.*`,
`nexa.voice_tts.*`, `nexa.stt.*`, `nexa.tts.*` was touched.

Summary of what R0035 got wrong or left undriven, and what changed:

1. **Confirmed wrong, fixed**: `GenerationCompleteEvent` alone was flipping
   `BargeInController` back to IDLE via `notify_response_finished()`,
   before any of the assistant's audio had necessarily reached the
   speaker. Installed Pipecat source (`BaseOutputTransport`) proves
   `BotStoppedSpeakingFrame` is the only real playback-drain signal
   (either a genuine `TTSStoppedFrame`, or a multi-second silence
   fallback) — generation completing says nothing about the output
   transport's own audio queue. Fixed with `_ResponseLifecycle`, the
   cloud analogue of the already-accepted `nexa.voice.gate.HalfDuplexGate`
   combinator, plus a deterministic `TTSStoppedFrame` injected right after
   every generation's audio, so the real stop signal never depends on the
   library's 3-second silence-fallback timer.
2. **Confirmed wrong, fixed**: R0035 used
   `CloudTurnAccumulator.assistant_text` directly as the interrupted
   turn's spoken prefix. The installed Pipecat source
   (`gemini_live/llm.py:_handle_msg_output_transcription`) contains the
   library's own verbatim comment: output-transcription messages "arrive
   *before* the model_turn messages with audio" and "contain much *more*
   text... on an interruption our recorded context will contain some text
   that was actually never spoken." This is a source-proven failure mode,
   not a theoretical concern. Fixed with `_SpokenPrefixHighWater`, a
   one-chunk-lag combinator: text is promoted to the high-water mark only
   once a *later* audio chunk confirms the *earlier* snapshot has crossed
   the playback-output boundary.
3. **Confirmed true, fixed**: `ConversationRouter.recover_from_mid_turn_loss()`
   had no caller anywhere in `GeminiVoiceRuntime` — R0035's own claim that
   the mid-turn-unsafe policy was "wired" was accurate only up to the
   router method existing, not up to anything actually invoking it in the
   production runtime. Fixed: `_consume_provider_events` now polls
   `provider.needs_fresh_session` after every event, drives the recovery
   the instant it is observed, and atomically swaps both
   `GeminiVoiceRuntime.provider` and the new `_ProviderHandle` box the VAD
   bridge reads through — never a second concurrent `provider.events()`
   reader, and the old, already-`stop()`'d provider's queue is never read
   again.
4. **Confirmed true, deferred deliberately**: nothing calls
   `should_proactively_reconnect()` in the production runtime. Per the
   charter's own option (B), this is now explicitly documented as
   deferred, not silently overclaimed — the unexpected-error (mid-turn-
   unsafe) reconnect path from item 3 remains fully functional and is the
   one this checkpoint actually drives.
5. **Confirmed real bug, fixed**: the operator app reached into
   `AecReferenceHealth`'s private `_on_change` attribute *after*
   construction, silently replacing (not composing with) the metrics
   logger's own callback. Fixed with a proper `on_aec_change` parameter on
   `build_gemini_voice_runtime`, composed internally with the metrics
   logger so both always fire together.

## PLAYBACK-LIFECYCLE RESULT

Read `pipecat/transports/base_output.py` (installed 1.8.1) end to end.
Confirmed: `BotStartedSpeakingFrame` fires on the first `TTSAudioRawFrame`
reaching the output transport's `MediaSender` (`_handle_bot_speech` ->
`_bot_currently_speaking` -> `_bot_started_speaking`, guarded so repeat
calls while already speaking are no-ops). `BotStoppedSpeakingFrame` fires
**only** from: (a) a `TTSStoppedFrame` frame, if any TTS audio was
actually received (`_tts_audio_received`) — `handle_tts_stopped` first
flushes any partial trailing buffered audio so it plays before the stop is
processed; or (b) `BOT_VAD_STOP_FALLBACK_SECS = 3` seconds of no new frame
reaching the audio queue. Neither is tied to the model's own
generation-complete signal in any way — proving the charter's suspicion
correct: **`GenerationCompleteEvent` can absolutely occur while queued
cloud PCM is still playing or not yet even started.**

`_ResponseLifecycle` (new, `src/nexa/realtime/gemini/runtime.py`) is the
cloud analogue of `nexa.voice.gate.HalfDuplexGate`'s own
generation+playback combinator (same three-flag shape:
generating/bot_speaking/produced_audio, tracked instead of
gate's response_generating/bot_speaking/spoken_this_response), but
**actively fires** an `on_finished()` callback exactly once per response
the moment the combined state settles, rather than being a passively
queried property (`BargeInController.notify_response_finished()` is
event-driven, unlike the gate's `mic_suppressed` property). Rules,
verified by 6 unit tests (`TestResponseLifecycle`):

- generation-complete while `bot_speaking` (or before any
  `BotStartedSpeakingFrame` at all, if audio was produced) never fires;
- a `BotStoppedSpeakingFrame` that arrives before generation-complete
  (multi-chunk inter-gap) never fires either — waiting for the real
  `TTSStoppedFrame`-driven stop that always follows once generation
  completes;
- multiple BotStarted/BotStopped spans within one response never trip an
  early finish (each span checked against current `_generating`, not
  latched);
- a confirmed local interruption (`mark_interrupted()`) suppresses any
  stale finish signal for the response that was just talked over — a
  fresh `mark_dispatched()` for the next turn re-arms it cleanly;
- a response that produced no audio at all (a text-only edge case, or an
  error before any audio arrived) finishes immediately on
  generation-complete — nothing will ever make Bot* frames fire for it.

`_VadToProviderBridge` (extended) observes `BotStartedSpeakingFrame`/
`BotStoppedSpeakingFrame` travelling upstream from `transport.output()`
(through the unmodified `AecReferenceFeeder`/`BargeInController`/
`VADProcessor`, each of which already forwards unrecognised frame types
unchanged) and feeds them to `_ResponseLifecycle`.
`_consume_provider_events` injects a `TTSStoppedFrame` via
`hw_worker.queue_frames([...])` immediately on `GenerationCompleteEvent` —
in the SAME FIFO the audio chunks already went through, so it is
processed strictly after every chunk of that generation, making the real
`BotStoppedSpeakingFrame` deterministic rather than dependent on the
3-second silence fallback.

## BARGE-IN ACTIVE-WINDOW RESULT

`BargeInController` itself is **unmodified** (per the freeze). What
changed is *when* `notify_response_dispatched()`/`notify_response_finished()`
are called: dispatch still fires on the first `AssistantAudioEvent`/
`AssistantTranscriptionEvent` of a turn (unchanged); finish now fires only
from `_ResponseLifecycle.on_finished` (real playback-drain truth), never
directly from `GenerationCompleteEvent`. 4 integration tests
(`TestBargeInWiring`) prove: dispatch still happens correctly;
`GenerationCompleteEvent` alone does NOT flip the controller back to IDLE
(and the deterministic `TTSStoppedFrame` was in fact injected); a REAL
`observe_bot_started()`/`observe_bot_stopped()` sequence after
generation-complete DOES flip it to IDLE. The `_on_confirmed` hook's own
three actions (spoken-prefix set, router interruption, provider cancel,
capture-phase close) are unchanged in shape from R0035, only reordered per
§2 below and fixed to read through `_ProviderHandle` (see §3).

## SPOKEN-PREFIX HIGH-WATER RESULT

Read `pipecat/services/google/gemini_live/llm.py` (installed 1.8.1),
`_handle_msg_output_transcription` and `_push_output_transcription_text_frames`.
The library's own comment (quoted verbatim above) proves output
transcription can arrive **before**, and **look further ahead than**, the
audio it describes — this is not a hypothetical, it is a documented,
named failure mode in the exact class NeXa wraps. Consequence: R0035's use
of the raw, running `CloudTurnAccumulator.assistant_text` as an
interrupted turn's canonical prefix was unsafe, exactly as the charter
suspected.

`_SpokenPrefixHighWater` (new) tracks a one-chunk-lag snapshot: each
`AssistantAudioEvent` promotes whatever `assistant_text` had accumulated
**as of the previous** audio chunk into the high-water mark, then
snapshots the *current* text as the new pending value for the *next*
chunk to promote. Text that arrived after the last audio chunk (has not
yet crossed the playback-output boundary) is therefore never included —
and, as a deliberate consequence, **a response interrupted after only ONE
audio chunk ever arrived commits an empty prefix** (proven by
`test_a_unplayed_ahead_of_audio_text_is_not_yet_in_high_water`): with only
one data point, there is no way to know whether that chunk's already-
accumulated text overshoots its own audio duration (the exact documented
Gemini quirk), so the conservative, safe choice is to credit nothing yet
rather than risk crediting look-ahead text. This is the "smallest safe v1
mechanism" the charter asked for, not a fuller alignment scheme — sentence/
chunk-level precision, never sample-accurate, exactly the documented
approximation the charter explicitly permits.

Wired into `_on_confirmed`: `router.set_spoken_prefix(prefix_tracker.high_water)`
then `router.on_interruption()` (the charter's own literal ordering;
functionally either order is safe — neither `CloudTurnAccumulator` method
depends on the other having already run, confirmed from source and from
the existing R0034 tests, which use both orders in different scenarios).

6 unit + integration tests (`TestSpokenPrefixHighWater`) prove, matching
the charter's own lettered scenarios exactly:
- **A** unplayed, ahead-of-audio text is not yet in the high-water mark;
- **B** text whose chunk has been superseded by a later one (both
  "released toward playback") may be committed;
- **C** an interruption halfway through a response stores only the
  high-water prefix, via the real `ConversationRouter` write path;
- **D** a late transcription delta arriving after the interruption cannot
  enlarge the committed prefix (the existing R0034
  `CloudTurnAccumulator.on_assistant_transcription` guard, unchanged,
  still holds — this test proves the NEW high-water mechanism doesn't
  reopen that hole);
- **E** an interruption before any audio ever arrived commits no assistant
  text at all (`ExternalExchangeOutcome.COMMITTED_USER_ONLY`);
- **F** a normal, non-interrupted turn is completely unaffected — it still
  stores the full final assistant text (the high-water mechanism is
  interruption-only; normal completion still commits via
  `GenerationCompleteEvent` exactly as before).

## MID-TURN RUNTIME RECOVERY RESULT

`GeminiVoiceRuntime._consume_provider_events` now checks
`provider.needs_fresh_session` after handling every event from the
currently-active provider. The moment it is observed true (which happens
the instant the `ReconnectingEvent(reason="mid_turn_unsafe_loss")` — the
very event that sets the flag, in the same synchronous step inside
`GeminiLiveProvider._readiness_monitor`, before queuing it — is read), the
consumer:

1. calls `await self.router.recover_from_mid_turn_loss(old_provider=provider, ...)`
   (unchanged router method from R0035 — the gap was purely the missing
   caller);
2. on success, atomically reassigns **both** `self.provider` and
   `self.provider_handle.current` to the new instance;
3. `break`s out of the inner `async for event in provider.events()` loop
   over the OLD provider's queue — it is never read again (the old
   provider was already `stop()`'d inside `recover_from_mid_turn_loss`,
   so nothing further would arrive on it in production anyway; a
   deterministic test fabricates an event directly on the abandoned queue
   and proves the router/session are unaffected by it regardless);
4. the outer `while True:` loop re-enters `async for event in
   self.provider.events()` on the NEW provider — **exactly one consumer
   at all times**, never two concurrent readers of any provider's event
   stream;
5. on failure (`recover_from_mid_turn_loss` returns `None` — the router
   already applied its Decision-J LOCAL fallback), the consumer returns,
   ending cloud-side consumption entirely, consistent with "no cloud
   provider to read from any more."

`_ProviderHandle` (new) is the box `_VadToProviderBridge` holds instead of
a raw `GeminiLiveProvider` reference — its `.current` is reassigned in
lock-step with `self.provider`, so **future local VAD-bracketed audio
reaches the NEW provider immediately**, with no risk of the bridge
continuing to call into a dead, already-`stop()`'d instance.
`_on_confirmed`'s `provider.cancel()` call was also fixed to read through
`provider_handle.current` for the same reason (R0035 called the
construction-time closure variable directly, which would have cancelled a
stale instance after a swap).

One full runtime-level deterministic test
(`TestMidTurnRuntimeRecovery.test_mid_turn_loss_swaps_provider_replays_pending_and_next_turn_is_clean`)
drives this exactly per the charter's own required sequence: a real
mid-turn drop -> `needs_fresh_session` observed -> the runtime recovers on
its own (never a direct test-to-router call) -> the new provider becomes
`runtime.provider` -> a fabricated event on the old, abandoned queue
provably reaches nothing -> the new provider received exactly the one
pending, previously-undelivered chunk (never the one already confirmed
sent live before the drop) -> a subsequent NORMAL turn commits exactly
once through the new provider only, with no duplicate or lost canonical
history entry.

**A real race was found and fixed while writing this test** (not a bug in
the runtime code — a bug in the test's own initial design): sending the
simulated "stranded" audio concurrently with the runtime's consumer task
already running raced the consumer's reaction to the very same
`ReconnectingEvent` that proves the drop — the consumer could complete an
entire (successful, but audio-less) recovery before the test coroutine's
own `send_user_audio` call ever executed, since Python's `asyncio.Queue`
wakeup for an already-waiting consumer is not guaranteed to lose a race
against a differently-scheduled coroutine. Fixed by driving the drop and
the stranded audio to completion **before** the runtime's consumer task is
even created — the provider's own readiness-monitor background task
(which sets `needs_fresh_session`) is entirely independent of the
runtime's consumer, so this ordering is deterministic without it.

## PROACTIVE RECONNECT CALLER RESULT

Verified: nothing in `GeminiVoiceRuntime`, `build_gemini_voice_runtime`, or
`apps/nexa_cloud_voice_app.py` calls
`GeminiLiveProvider.should_proactively_reconnect()`. Per the charter's
option (B): **explicitly deferred**, documented here and in the module
docstring, rather than silently left implied-functional. The
unexpected-error (mid-turn-unsafe) reconnect path — the one this
checkpoint actually wires end-to-end — remains fully functional and is
unaffected by this deferral. No 8-minute connection-age test was run or
is required for this deferral decision.

## AEC READY SIGNAL RESULT

`build_gemini_voice_runtime` gained an `on_aec_change` parameter, composed
internally with the metrics logger (`_combined_aec_change` calls both) —
the ONE supported way to observe AEC status, rather than reaching into
`AecReferenceHealth`'s private `_on_change` attribute after construction
(which R0035's operator app did, silently replacing rather than composing
with the metrics callback — a real, if latent, bug: the metrics log line
would simply never have fired in the R0035 operator app). `nexa_cloud_voice_app.py`
now passes its printer via `on_aec_change=` at construction time. One
deterministic test
(`test_aec_ready_signal_is_observable_and_composes_with_metrics`) proves
both the operator callback and the metrics logger fire, in order, on a
real `AecReferenceHealth.mark_started()`/`mark_failed()` transition.

`CLOUD_PROVIDER_READY` was re-verified as already correct in R0035: it is
printed from the `on_event` hook only upon a real `ReadinessChangedEvent`
carrying `ProviderReadiness.READY`, which `GeminiLiveProvider` emits only
after `_wait_until_ready()` observes the real (fake, in tests)
`_ready_for_realtime_input` flag — not a fabricated string.

## FILES CHANGED

- **Modified:** `src/nexa/realtime/gemini/runtime.py` — `_ProviderHandle`,
  `_ResponseLifecycle`, `_SpokenPrefixHighWater` (new classes);
  `_VadToProviderBridge` extended to observe `BotStartedSpeakingFrame`/
  `BotStoppedSpeakingFrame` and take a `_ProviderHandle` instead of a raw
  provider reference; `_consume_provider_events` rewritten (playback-
  lifecycle-aware bargein finishing, `TTSStoppedFrame` injection,
  mid-turn recovery driving with atomic provider/handle swap);
  `_on_confirmed` reordered (`set_spoken_prefix` -> `on_interruption` ->
  `lifecycle.mark_interrupted()` -> `provider_handle.current.cancel()` ->
  `notify_interruption_complete()`); `build_gemini_voice_runtime` gained
  `on_aec_change`; `GeminiVoiceRuntime` dataclass gained
  `provider_handle`/`lifecycle`/`prefix_tracker` fields.
- **Modified:** `apps/nexa_cloud_voice_app.py` — `on_aec_change=` passed at
  construction instead of reaching into `aec_health._on_change` after the
  fact.
- **Modified (tests):** `tests/test_realtime_gemini_runtime.py` — rewritten
  around the new classes/fields; +15 net new tests across
  `TestDryConstruction` (+1: AEC signal), `TestResponseLifecycle` (+6, new
  class), `TestSpokenPrefixHighWater` (+6, new class),
  `TestBargeInWiring` (existing 3 tests updated for the new wiring +1 new:
  generation-complete-alone-does-not-finish), `TestMidTurnRuntimeRecovery`
  (+1, new class).
- **Unmodified:** `src/nexa/realtime/router.py` (item 3 needed only a
  caller, not a router change — `recover_from_mid_turn_loss` is
  byte-for-byte the R0035 version); `src/nexa/realtime/gemini/service.py`;
  every local-voice file.

## TEST RESULTS

- `tests.test_realtime_gemini_runtime`: **20 tests, OK** —
  `TestDryConstruction` 3, `TestResponseLifecycle` 6,
  `TestSpokenPrefixHighWater` 6, `TestBargeInWiring` 4,
  `TestMidTurnRuntimeRecovery` 1 = 20 total.
- `tests.test_realtime_gemini_service`: **34 tests, OK** (unchanged from
  R0035 — router.py/service.py untouched this checkpoint).
- Full suite: `python -m unittest discover -s tests`: **911 tests, OK
  (skipped=7)** — 896 (R0035 baseline) + 15 net new, **zero regressions**.
- `ruff check src tests apps`: all checks passed.
- `git diff --check`: clean. `pip check`: clean.
- Import-isolation (in-process): `nexa.realtime.router` + `.policy` alone
  never pull `nexa.realtime.gemini`/`.gemini.runtime` into `sys.modules`.
- Secret scan: no literal key/token patterns in any changed file.
- `apps/nexa_cloud_voice_app.py --dry`: run live in this sandbox after
  every change in this checkpoint — object graph constructs cleanly, no
  audio device or network touched.

## LOCAL VOICE FREEZE CHECK

No file under `nexa.voice.*`, `nexa.voice_tts.*`, `nexa.stt.*`,
`nexa.tts.*`, or `nexa.voice_conversation.*` was modified this checkpoint.
`BargeInController`, `AecReferenceFeeder`, `AecReferenceHealth`,
`InterruptionStateMachine` are imported and used exactly as they already
exist — zero edits. `_ResponseLifecycle` is a NEW class, not a
modification of `HalfDuplexGate` (which remains untouched and is not
imported by the cloud path at all) — it independently reimplements the
same combinator *shape* for the cloud pipeline's own distinct timing
characteristics (see §1 above for why a literal shared instance would not
have been correct: cloud's `AssistantAudioEvent`-driven injection has
different premature-finish race characteristics than local's concurrent
synthesis-while-generating streaming). Full regression suite green
(911/911, 0 new failures beyond the pre-existing 7 skips) is the
acceptance evidence.

## KNOWN RISKS (carried + new)

- **Not hardware/Gemini-tested.** Unchanged from R0035 — this remains
  Phase 7, requiring the operator.
- **Proactive reconnect has no caller** (§4) — deliberately deferred, not
  a regression; the mid-turn-unsafe path this checkpoint actually wires
  is the one production needs first.
- **Spoken-prefix precision remains chunk-level, and specifically requires
  at least two audio chunks before crediting any text** — a response
  interrupted after only one chunk played commits an empty assistant
  prefix even though some audio genuinely did play. This is the
  documented, deliberate conservative trade-off (§2), not an oversight.
- **A genuine >3-second silence gap mid-generation, with no further audio
  ever following before the model's own generation-complete signal**,
  would still rely on the library's fallback timer rather than the
  injected `TTSStoppedFrame` for that specific span's BotStopped (Pipecat
  itself guards `_bot_stopped_speaking()` against firing twice in a row
  without an intervening BotStarted, so an injected `TTSStoppedFrame`
  arriving after a fallback-timer-driven stop that already fired is a
  silent no-op at the Pipecat level, not a second event). This is an
  extreme edge case for Gemini's typical streaming cadence and is
  documented here rather than engineered around, consistent with the
  charter's own "sentence/chunk-level precision is acceptable" allowance.

## WHAT REMAINS FOR M2.6B

Unchanged from R0035: Phase 7 (real Gemini + real hardware operator
acceptance) is the one remaining step before `M2.6B` can be marked
COMPLETE. Everything deterministic is now implemented, tested, and green.

## EXACT LIVE LAUNCH COMMAND

Unchanged from R0035:

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

```
.venv/bin/python apps/nexa_cloud_voice_app.py --dry   # sanity check only
```

## READY LINES

Unchanged from R0035 — wait for both before speaking:

```
· PROVIDER READINESS: READY
>>> CLOUD_PROVIDER_READY <<<
```

```
  ✓ AEC_REF_ACTIVE
```

(Now genuinely composed with the metrics logger per §5 above, not a
silently-replaced callback.)

## COMMIT HASHES

(recorded in a follow-up commit once made — see the session's final
message.)

## GIT STATUS

Not pushed (per the standing constraint for this entire M2.6 body of
work).
