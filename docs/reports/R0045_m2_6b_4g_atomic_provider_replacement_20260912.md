# R0045 — M2.6B.4G: Atomic Provider Replacement on Confirmed Barge-In

**Date:** 2026-09-12
**Milestone:** M2.6B.4G (follow-on to M2.6B.4F / R0044)
**Status:** Implemented, tested, all hard invariants proven deterministically.
No Gemini call, no hardware. **Hardware acceptance remains FAIL. `M2.6B`
remains IN PROGRESS.**

## ARCHITECTURAL DECISION

R0044 exhaustively source-audited the installed Pipecat 1.8.1/`google-genai`
stack and proved **no airtight same-session response-ownership boundary
exists**: `LiveServerMessage`/`LiveServerContent` carry no
response/turn/generation identifier on any server message, so no local
signal (transcript, turn-closure counting, any "+N" heuristic) can ever
distinguish a genuinely new response's audio from a stale, interrupted
response's audio arriving late on the **same** provider/session. R0044's
own adversarial CASE 2 test proved this concretely, not merely in theory.

R0045 changes the architecture rather than refining the heuristic:
**every confirmed local barge-in now atomically replaces the Gemini
provider/session**, never continuing to read events from the interrupted
one. This is Option A from the charter ("stop consuming the old provider
entirely") — the exact same isolation pattern
`ConversationRouter.recover_from_mid_turn_loss` already used for
connection-loss recovery, now applied to a second trigger (a confirmed
local barge-in) via a shared primitive
(`ConversationRouter.start_fresh_cloud_provider`, factored out of
`recover_from_mid_turn_loss` — no parallel recovery architecture was
built). Ownership after a swap is enforced by **provider-instance
isolation**, not by an epoch/generation tag on events (Option B from the
charter): `_consume_provider_events` reads from exactly one
`provider.events()` async generator at a time and never resumes reading
the old one after a swap, so an old-provider event physically arriving
after the swap is structurally incapable of reaching the router, the
canonical history, `hw_worker`, AEC, dispatch re-arm, or barge-in
re-arm — there is no code path left that still calls into it.

**No session resumption for this path** (per the charter's default): the
replacement provider is a fresh `GeminiLiveProvider` + fresh
`CloudContextSnapshot` built from canonical NeXa state (same role card,
current language preference, bounded recent canonical turns, the
just-interrupted assistant turn per the existing conservative
interruption-history rule, no duplicate user turn) — identical to what
`recover_from_mid_turn_loss` already builds; no attempt was made to prove
server-side live conversational state can survive a resumed session
without exposing old-response content, so resumption is not used here.

## OLD PROVIDER QUARANTINE RESULT

**PASS.** The OLD provider is quarantined **synchronously**, inside
`_on_confirmed` (the `BargeInController.on_confirmed` hook — the same
synchronous callback that already calls `broadcast_interruption()` and
`router.on_interruption()`), before any `await` — so the "local playback
stops immediately" invariant is never made to wait on the replacement:

```python
provider_handle.old_provider = provider_handle.current
provider_handle.quarantined = True
provider_handle.replacement_requested = True
```

`quarantined` is read by `_VadToProviderBridge.process_frame` on every
frame (never sent live to a quarantined provider — buffered locally
instead, see ACTIVE UTTERANCE BUFFER DESIGN below) and by
`_replace_provider_after_bargein` to know when to clear it (only once the
new provider is live and the drain/replay loop has finished).
`replacement_requested` is read by `_consume_provider_events`, checked
**first**, ahead of every other per-event handler (`ProviderInterruptionEvent`
counting, `router.handle_provider_event`, the `_ResponseGenerationGuard`
audio gate, `needs_fresh_session`) — once true, the OLD provider's queue
is never read again for the rest of that provider's lifetime; the loop
`break`s out of `async for event in provider.events()` and restarts on
`self.provider`, which by then already points at the new instance.
`TestVadBridgeQuarantine` proves the bridge side (2/2 tests); the "old
provider events never reach the router/canonical history/hardware/AEC
again" side is proven by `test_case2_old_delayed_audio_after_new_turn_closes_is_now_dropped`
and by fabricating a further event directly on the old provider's
abandoned queue in `test_recovery_after_no_transcript_interruption_via_replacement`
(`old_provider._events.put_nowait(GenerationCompleteEvent())` — proven to
produce no second commit).

## ACTIVE UTTERANCE BUFFER DESIGN

`_VadToProviderBridge` now always maintains a small `bytearray()`
(`self._utterance_buffer`) alongside the existing live-forwarding logic —
**not LID, not a transcript, never inspected for content**; it exists
solely so the interrupting utterance can be replayed exactly once to the
replacement provider even though some of its audio was already sent live
to the OLD provider before the barge-in was confirmed (real VAD timing:
`confirm_hold_secs` of sustained candidate speech elapses **after** the
utterance already started).

* `VADUserStartedSpeakingFrame` → buffer reset to empty, `user_turn_start()`
  sent live (only if not quarantined).
* `InputAudioRawFrame` while a turn is open → **always** appended to the
  buffer, **and** forwarded live via `send_user_audio()` — but only while
  not quarantined; the moment `_on_confirmed` sets `quarantined = True`
  (synchronously, before any further frame is processed), subsequent
  audio for the same still-open utterance is buffered only, never sent to
  the (now-abandoned) old provider.
* `VADUserStoppedSpeakingFrame` (VAD END — "sealed") → if quarantined,
  the buffer is pushed onto `provider_handle.sealed_utterances` (a FIFO,
  not a scalar — a second local utterance sealing during the replacement
  window must never overwrite the first) and
  `provider_handle.sealed_utterance_ready` (an `asyncio.Event`, so no
  missed-signal race regardless of which side — provider-ready or
  VAD-end — happens first) is set; otherwise `user_turn_end()` is sent
  live as before.

Normal, non-interrupted turns are completely unaffected: the buffer is
maintained (cheap — a `bytearray.extend()` per frame, reset per
utterance) but never read, so there is no added latency on the
unmodified live-forwarding path. `TestVadBridgeQuarantine` proves both
the steady-state (unchanged live send) and quarantine (buffer-only, no
provider I/O) behaviour with a real Pipecat pipeline (not a simulated
one), and proves the pre-confirm live-sent prefix plus the post-confirm
buffered remainder are both accounted for.

## EXACT-ONCE REPLAY RESULT

**PASS.** `_replace_provider_after_bargein` starts the new provider and
waits for the utterance to seal **concurrently** (`asyncio.gather`),
satisfying "start creating a fresh provider/session immediately, in
parallel with the user continuing to speak" and handling both timing
orderings identically (new-provider-ready-before-seal and
seal-before-new-provider-ready — both covered by dedicated tests, see
TEST RESULTS):

```python
stop_old_task = asyncio.create_task(old_provider.stop(reason=...))
new_provider, _ = await asyncio.gather(_start_new(), _wait_first_seal())
await stop_old_task
```

Once both sides are ready, the drain loop replays **every** currently-queued
sealed utterance (not just the first) as one coherent `user_turn_start()`
/ `send_user_audio()` / `user_turn_end()` sequence per utterance, then
clears `quarantined`. `test_recovery_after_no_transcript_interruption_via_replacement`
proves the exact-once property directly: the pre-confirm live-sent prefix
(`b"PREE"`) and the post-confirm buffered remainder (`b"POST"`) are
replayed to the new provider as the single concatenated utterance
`b"PREEPOST"` — `new_fake.received_audio == [b"PREEPOST"]`, never zero,
never twice, never partial. A freshly-swapped provider's own not-ready
audio buffering (`UtteranceFramer` inside `GeminiLiveProvider`) was
deliberately **not** relied on to absorb the replay silently: source
reading confirmed `_flush_framer()` is only re-triggered by a detected
not-ready→ready transition inside `_readiness_monitor()`, not by every
subsequent audio-append once an utterance's own start was captured
pre-ready — an explicit, controlled replay (as implemented) is the only
safe design.

## PROVIDER EPOCH RESULT

**Option A chosen and proven** (per the charter, explicitly allowed):
"stop consuming the old provider entirely," not "reject events by epoch
tag." `_consume_provider_events` runs a `while True:` loop around
`async for event in provider.events()`, always iterating the CURRENT
`provider` local variable; `replacement_requested` is checked first in
the per-event body and, once true, the inner `async for` is `break`'d
after the swap — the old provider's own async generator is simply never
iterated again (Python's `async for` does not resume a broken-out-of
generator). No second concurrent reader of any provider's `events()` is
ever created (same invariant `recover_from_mid_turn_loss` already
preserves for connection-loss recovery). This is mechanically provable
by construction (a single consumer loop, one `provider` reference at a
time) rather than requiring proof that every event carries and is
checked against a correct tag.

## CANONICAL HISTORY RESULT

**No duplicate canonical turn; exactly one commit per confirmed
interruption that had a transcribed turn open.** `_on_confirmed` now
calls `router.commit_cloud_turn()` (a new, minimal, safe addition) after
`router.set_spoken_prefix(CONSERVATIVE_INTERRUPTED_ASSISTANT_PREFIX)` and
`router.on_interruption()` — committing the interrupted turn's user text
(if any) with the conservative empty assistant prefix, exactly the same
one-shot-commit guarantee `CloudTurnAccumulator` already provides
(`on_turn_complete()` is a no-op once a turn is terminal). The **new**
provider epoch is the sole authority for whatever comes after: since
`begin_cloud_turn()` is never called again until a caller starts a new
turn, `router._turn.current` stays terminal (COMMITTED) and any further
late event — including one fabricated directly on the OLD provider's
abandoned queue — cannot re-open or double-commit it.
`test_recovery_after_no_transcript_interruption_via_replacement` proves
this precisely: after the swap, `session.history` has exactly one entry
(the committed, interrupted turn 1); a `GenerationCompleteEvent`
fabricated on the old provider's queue afterward produces no second
entry.

**Deferred gap, explicitly not fixed this checkpoint (do not guess
this part — reported honestly instead):** exhaustive `grep -rn
"begin_cloud_turn\|\.start_turn(" src/ apps/` confirms
`router.begin_cloud_turn()` is **never called anywhere in production
code** (`runtime.py`, `apps/nexa_cloud_voice_app.py`) — only in tests.
This means `CloudTurnAccumulator._current` is always `None` in
production, so `on_user_transcription()`'s guard always no-ops and no
cloud conversation turn has ever actually been written to canonical
`ConversationSession.history` in any M2.6A/M2.6B hardware run to date,
independent of and pre-dating this checkpoint. R0045 does not attempt to
fix `begin_cloud_turn()`'s production wiring: doing so safely requires
resolving a separate, deeper risk — VAD start for an interrupting
candidate utterance fires **before** confirmation, while turn N is still
open and uncommitted, so an INTERIM transcript for a not-yet-confirmed
candidate could silently overwrite turn N's own `user_text` before it
commits (`on_user_transcription` has no way to tell "continuation of the
same turn" from "a different, overlapping utterance"). That needs its
own dedicated checkpoint (proposed **R0046**), not a rushed fix folded
into an already-large replacement-architecture change. The
`commit_cloud_turn()` call added here is correct and necessary for the
day `begin_cloud_turn()` is wired — until then it remains a safe no-op in
production (as directly observed: hardware runs to date show no
`begin_cloud_turn()` call site), and is fully exercised, deterministically,
by the new tests, which call `router.begin_cloud_turn()` explicitly.

## R0044 CASE-2 RESULT UNDER R0045

**CLOSED — the critical proof R0044 could not provide.** R0044's CASE 2
("old delayed audio arriving after a genuinely new local turn's own
closure") was reproduced verbatim as
`test_case2_old_delayed_audio_after_new_turn_closes_is_now_dropped`: a
confirmed barge-in triggers the atomic swap; a NEW local turn opens,
sends audio, and closes on the NEW provider; only *afterward* does a
trailing audio event for the OLD generation arrive — fabricated directly
on the OLD provider's now-abandoned queue, exactly reproducing R0044's
adversarial shape. **PASS**: the trailing old-generation audio is
dropped, and the queued hardware audio remains exactly what it should be
(no stray old-generation chunk was ever promoted). The mechanism is
different in kind from R0044's: R0044 tried to prove the event
*insufficiently recent* (turn-closure counting on the SAME provider) and
proved that insufficient; R0045 proves the event *structurally
unreachable* (it originates from a provider instance whose queue is never
read again) — the OLD audio has no "old" vs "new" distinction left to
guess at, because there is no shared queue for it to arrive on at all.

## PERFORMANCE INSTRUMENTATION

All eight charter-required timing points implemented as plain
`RuntimeMetrics` methods (`logger.info`/`logger.warning`, monotonic
timestamps only — no PII, no raw audio, no Gemini call this checkpoint,
no fabricated numbers):

| Method | Fired |
|---|---|
| `bargein_confirmed_t` | start of `_on_confirmed`, before any other action |
| `old_audio_stop_t` | immediately after quarantine flags are set (same instant as confirm — local stop is synchronous) |
| `replacement_start_t` | entry to `_replace_provider_after_bargein` |
| `new_provider_ready_t` | `router.start_fresh_cloud_provider` returns a live provider |
| `interrupting_utterance_end_t` | `sealed_utterance_ready` first observed set (VAD END for the interrupting utterance) |
| `replay_start_t(byte_count=...)` | each sealed utterance's replay begins |
| `replay_end_t` | each sealed utterance's replay (`user_turn_end`) completes |
| `first_new_assistant_audio_t` | the NEW provider's first `AssistantAudioEvent` reaches the consumer loop |
| `bargein_replacement_failed(reason=...)` | new provider construction/start fails (fallback to LOCAL, Decision J) |

No live measurements were taken — no Gemini call was made this
checkpoint, per the charter's explicit instruction.

## R0044's "+2" FALLBACK REASSESSED

**Removed, not narrowed further.** `MIN_LOCAL_TURN_CLOSURES_BEFORE_FALLBACK_REARM`
and the `_invalidated_at_turn_seq`/`local_turn_seq_at_interrupt` plumbing
in `_ResponseGenerationGuard` are deleted; the class reverts to its
simple R0038 form (`start_new_generation` / `is_valid` / `interrupt` /
`valid_id` only). Its only use case — "recover dispatch after a confirmed
barge-in whose own turn produced no transcript" — is now handled
unconditionally by atomic replacement: the new provider's first
`AssistantAudioEvent` re-arms dispatch directly (`dispatched_for_turn =
False` set by the replacement itself), the same mechanism
`needs_fresh_session` recovery already used, never turn-closure counting
on a still-being-read provider. Verified, by source reading, that
connection-loss recovery paths (`needs_fresh_session`/
`RealtimeProviderFailedError`) never depended on the removed fallback
either (both already forced `dispatched_for_turn = False` directly), so
removing it does not regress connection-loss recovery. No redundant
ownership machinery was left in place merely because it existed.

## FILES CHANGED

```
docs/reports/R0044_m2_6b_4f_strict_post_interruption_response_ownership_20260912.md |  18 +
src/nexa/realtime/gemini/runtime.py                                                 | 506 ++++++++++----
src/nexa/realtime/router.py                                                         |  57 +-
tests/test_realtime_gemini_runtime.py                                               | 837 +++++++++++++---------
4 files changed, 925 insertions(+), 493 deletions(-)
```

* `src/nexa/realtime/router.py` — new shared primitive
  `start_fresh_cloud_provider` (factored out of, and now reused by,
  `recover_from_mid_turn_loss` — no duplicated snapshot-build/construct/
  start logic).
* `src/nexa/realtime/gemini/runtime.py` — `_ProviderHandle` extended with
  `quarantined`/`replacement_requested`/`old_provider`/
  `sealed_utterances`/`sealed_utterance_ready`; `_ResponseGenerationGuard`
  reverted to its simple R0038 form; `_VadToProviderBridge` extended with
  the always-on active utterance buffer + quarantine gating;
  `RuntimeMetrics` gained the nine R0045 instrumentation methods;
  `_consume_provider_events` gained the `replacement_requested` check
  (first in the per-event loop body) and `awaiting_first_new_audio`
  tracking; new method `_replace_provider_after_bargein`; `_on_confirmed`
  (inside `build_gemini_voice_runtime`) rewritten to quarantine
  synchronously and commit the interrupted turn.
* `docs/reports/R0044_...md` — erratum added under "WHY OLD AUDIO CAN
  NEVER RETURN" recording that R0044 proved no airtight same-session
  boundary exists and that R0045 supersedes the mechanism with
  provider-instance isolation.
* `tests/test_realtime_gemini_runtime.py` — removed
  `TestStrictPostInterruptionOwnership` (R0044's 5 adversarial CASE
  tests, tied to the now-removed `_invalidated_at_turn_seq` plumbing) and
  4 R0044-specific `_ResponseGenerationGuard` unit tests; reverted 3
  `_wire()` helpers' `generation_guard.interrupt()` calls to their bare
  R0038 form; removed
  `TestPostInterruptionAudioRecovery.test_1_interruption_with_no_final_transcript_recovers_future_audio`
  (superseded) with the class docstring updated to explain why the
  remaining tests (properties independent of the specific recovery
  mechanism) remain valid; added `TestVadBridgeQuarantine` (2 tests, real
  Pipecat pipeline) and `TestAtomicProviderReplacement` (5 tests, a real
  second `GeminiLiveProvider` as the replacement target, via the same
  `_fresh_provider_factory` pattern `TestMidTurnRuntimeRecovery`
  established).

## TEST RESULTS

New/changed tests, isolated:

```
tests/test_realtime_gemini_runtime.py::TestAtomicProviderReplacement (5 tests) — PASS
tests/test_realtime_gemini_runtime.py::TestVadBridgeQuarantine (2 tests) — PASS
```

Full file:

```
$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py -q
54 passed, 1 warning in 13.45s
```

Router + service (the two other touched/adjacent modules):

```
$ .venv/bin/python -m pytest tests/test_realtime_router.py tests/test_realtime_gemini_service.py -q
50 passed, 1 warning in 7.79s
```

Full project suite:

```
$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 946 tests in 61.522s
OK (skipped=7)
```

(The one traceback printed during this run is expected test output —
`TestArchitectureAndReset.test_20b_on_release_callback_raising_does_not_break_speech`
deliberately raises inside an `on_release` callback to verify the
continuity controller's own error resilience; it is caught, logged, and
the test passes.)

`ruff check src/ tests/` — All checks passed.
`.venv/bin/pip check` — No broken requirements found.
`git diff --check` — clean (no whitespace errors).

PL/EN role-card tests, zero-LID tests: covered by the full-suite run
above (`TestNativeOnlyLanguageDispatch`, `TestNoLocalLidInCloudRuntime`,
`TestSystemInstructionLanguagePolicy`, `TestCloudSameTurnLanguage` in
`test_realtime_gemini_runtime.py`, all green as part of the 54-test file
run).

## LOCAL VOICE FREEZE CHECK

```
$ git diff --stat -- src/nexa/voice src/nexa/voice_tts
(empty)
```

Zero diff — local voice is completely untouched by this checkpoint.

## COMMIT HASHES

fix/report commit: `4f6cb1d`

(recorded in the follow-up hash-record commit)

## GIT STATUS

At the time of writing (before this checkpoint's commit):

```
 M src/nexa/realtime/gemini/runtime.py
 M src/nexa/realtime/router.py
 M tests/test_realtime_gemini_runtime.py
?? docs/reports/R0045_m2_6b_4g_atomic_provider_replacement_20260912.md
 M docs/reports/R0044_m2_6b_4f_strict_post_interruption_response_ownership_20260912.md
```

Not pushed.

## EXACT ATTEMPT #3 COMMAND

Unchanged launch command from prior checkpoints — the atomic-replacement
change is entirely internal to `nexa.realtime.gemini.runtime`; no new
CLI flags were added:

```
.venv/bin/python apps/nexa_cloud_voice_app.py --bargein
```

## EXACT LIVE ACCEPTANCE SCRIPT

1. Start a normal exchange; let the assistant begin speaking.
2. Interrupt with a clear, lexical utterance partway through — confirm
   the assistant stops immediately, the interrupting utterance is
   answered correctly, and no old audio is ever heard again.
3. Interrupt with a **non-lexical** sound (cough/throat-clear) that
   Gemini will not transcribe — confirm the SAME immediate stop, and
   confirm a subsequent real turn still dispatches normally (this is
   R0043's original scenario, now recovered via replacement instead of
   turn-closure counting).
4. Deliberately speak a short, ambiguous interrupting utterance that
   straddles the confirm boundary (some audio before, some after) —
   confirm the full utterance is answered coherently (this exercises the
   exact-once replay path with a real barge-in timing, not simulated).
5. Watch the logs for the eight `BARGEIN_*`/`REPLACEMENT_*`/`REPLAY_*`/
   `FIRST_NEW_ASSISTANT_AUDIO_T` instrumentation lines and record actual
   latencies for `OLD_AUDIO_STOP_T` (should be ~0 relative to
   `BARGEIN_CONFIRMED_T`) and `FIRST_NEW_ASSISTANT_AUDIO_T` minus
   `BARGEIN_CONFIRMED_T` (the real, previously-unmeasured cost of this
   architecture — reconnect + fresh-context latency) — this is the first
   real evidence for sizing that cost, deferred by R0044.
6. Confirm no operator-audible regression versus the pre-R0045 baseline
   for ordinary (non-interrupted) turns — the active utterance buffer is
   designed to add no latency there, but only a live run confirms it.
