# R0046 — M2.6B.4H: Production Canonical Cloud Turn Lifecycle

**Date:** 2026-09-12
**Milestone:** M2.6B.4H (follow-on to M2.6B.4G / R0045)
**Status:** Production defect confirmed and fixed. All charter invariants
proven deterministically. No Gemini call, no hardware. **Hardware
acceptance remains FAIL. `M2.6B` remains IN PROGRESS.**

## CONFIRMED PRE-R0046 DEFECT

Exhaustive `grep -rn "begin_cloud_turn\|\.start_turn(" src/ apps/` (repeated
from R0045, re-verified here) confirms `ConversationRouter.begin_cloud_turn()`
was called **only from test files** before this checkpoint —
`src/nexa/realtime/gemini/runtime.py` and `apps/nexa_cloud_voice_app.py`
never called it. Consequence, traced precisely:
`CloudTurnAccumulator._current` stayed `None` for the life of every
M2.6A/M2.6B hardware run; `on_user_transcription()`'s guard
(`if cur is None or cur.is_terminal: return`) always no-op'd;
`commit_cloud_turn()`'s `on_turn_complete()` always returned `None`. Audio
worked correctly at every layer (this is why the defect went unnoticed
through several hardware-run attempts) — **canonical
`ConversationSession.history` never received a single cloud conversation
turn.** R0045 discovered this while auditing the barge-in replacement path
and explicitly deferred fixing it, flagging the one real risk that made it
unsafe to fix casually: an interruption candidate's own VAD start happens
**before** confirmation, while the still-open turn N (whose reply the
candidate may be interrupting) has already recorded its own user text —
naively calling `begin_cloud_turn()` on every VAD start would let
`CloudTurnAccumulator.start_turn()`'s own "abandon the still-open current
turn" rule discard turn N, or let a stray candidate transcript overwrite
its already-finalized `user_text`, before anyone knows whether the
candidate will even be confirmed.

## CANONICAL TURN AUTHORITY

Local VAD (via `_VadToProviderBridge`, the ONE place local turn boundaries
are observed) and `_on_confirmed` (via `BargeInController`, the ONE local
interruption-confirmation authority) are the only two call sites that ever
open a canonical turn — never provider-event arrival order or content.
The one new piece of state needed is a single, minimal, already-derivable
boolean:

```python
def has_turn_awaiting_assistant(self) -> bool:
    cur = self._turn.current
    return cur is not None and cur.state is CloudTurnState.AWAITING_ASSISTANT
```

`AWAITING_ASSISTANT` means "this turn already has a final user transcript
but has not yet been committed" — i.e. an assistant reply is presumably
being generated for it right now. This is deliberately **narrower** than
"any non-terminal current turn": a turn that opened but never received its
own final transcript (state `OPEN` — e.g. a confirmed non-lexical
interruption's own promoted turn, which the provider never transcribes)
must **not** block a fresh `begin_cloud_turn()` — otherwise the next real
turn could never recover. No new turn-id/candidate-turn object was
introduced (the charter's own "preferred direction" section explicitly
said not to blindly implement one) — `CloudTurnAccumulator`'s existing
state machine already carries the exact distinction needed.

## NORMAL TURN LIFECYCLE

`_VadToProviderBridge.process_frame`, on every `VADUserStartedSpeakingFrame`:

```python
if not self._router.has_turn_awaiting_assistant():
    self._router.begin_cloud_turn()
```

For an ordinary, non-overlapping turn, `has_turn_awaiting_assistant()` is
always `False` at VAD start (no assistant reply is in flight), so this
opens exactly one fresh canonical turn every time — proven by
`TestProductionCanonicalTurnLifecycle.test_real_bridge_opens_normal_turns_and_never_abandons_one_awaiting_assistant`
(invariants 1, 4) via the REAL `_VadToProviderBridge` class driven through
a real `Pipeline`/`PipelineWorker`/`WorkerRunner`, not a hand-rolled
simulation of it — this is the exact call site R0045's audit found
missing.

## INTERRUPTION-CANDIDATE LIFECYCLE

While turn N is `AWAITING_ASSISTANT` (its own final transcript already in,
assistant reply presumably in flight), a candidate's own VAD start —
confirmed interruption or a false one (throat-clear) that never confirms,
indistinguishable to the bridge until/unless `_on_confirmed` later fires —
hits the `if not has_turn_awaiting_assistant()` gate and does **nothing**:
no `begin_cloud_turn()` call, so turn N is never abandoned (invariant 6,
7). The candidate's audio is still sent live to the not-yet-quarantined
provider (R0045, unchanged), so its own transcript **can** physically
arrive at `router.on_user_transcription` while turn N is still
`self._current` — this is closed by the second, independent half of the
fix: `CloudTurnAccumulator.on_user_transcription` now also checks
`cur.user_final`:

```python
if cur is None or cur.is_terminal or cur.user_final:
    return  # late/foreign frame -- never re-opens or overwrites it
```

Turn N's `user_final` is already `True` by the time ANY candidate can
start speaking (R0034's proven ordering: final transcript always precedes
assistant dispatch), so this guard categorically blocks the corruption —
proven both as a pure unit test
(`tests/test_realtime_turn.py::TestForeignTranscriptCannotOverwriteFinalizedUserText`,
3 tests: a second final, an interim after final, and a turn's own
legitimate interim→final progression still working) and end-to-end
through the real bridge + real router together (invariant 10, same test
method as above).

If the candidate is rejected (VAD stop before confirm, no
`_on_confirmed` call at all), nothing further happens — no canonical turn
was ever opened for it (invariant 7), proven by the same real-bridge test.

## CONFIRMED INTERRUPTION LIFECYCLE

`_on_confirmed` (inside `build_gemini_voice_runtime`) — unchanged R0045
logic, plus one new line right after committing turn N:

```python
outcome = router.commit_cloud_turn()
if outcome is not None:
    ...  # metrics, unchanged
router.begin_cloud_turn()  # NEW -- promotes the candidate to its own turn
lifecycle.mark_interrupted()
...
```

Ordered strictly after the commit: `CloudTurnAccumulator.start_turn()`'s
own "abandon the still-open current turn" branch safely no-ops for an
already-terminal (just-committed) turn N, so no incorrect double-mutation
occurs. This is the ONE other call site — no future
`VADUserStartedSpeakingFrame` will ever arrive for the interrupting
utterance (it is a continuation of the SAME already-open local utterance
the bridge saw earlier, correctly gated off by
`has_turn_awaiting_assistant()` at the time). Proven via the REAL,
unmirrored `_on_confirmed` closure (`build_gemini_voice_runtime(dry=True)`
+ `runtime.bargein._on_confirmed(...)`, not a test's own reproduction of
the logic) by
`test_confirmed_interruption_commits_n_and_opens_n_plus_1_via_real_on_confirmed`
(invariant 8's canonical-history half): turn N committed+interrupted,
exactly one fresh turn N+1 opened, `has_turn_awaiting_assistant()` false
again immediately after (N+1 has no transcript yet), and N+1 later
receives its own transcript and commits exactly once (invariant 12, 13).

## NON-LEXICAL INTERRUPTION RESULT

**PASS — no fake user text is ever invented.** A confirmed non-lexical
interruption (cough/throat-clear) opens its own canonical turn N+1 via the
same `_on_confirmed` call, but if the provider never produces a transcript
for it, N+1 stays in state `OPEN` forever — `commit_cloud_turn()`'s
existing guard (`if turn is None or not turn.user_text: return None`)
refuses to commit it, and `has_turn_awaiting_assistant()` correctly
reports `False` for an `OPEN` (non-`AWAITING_ASSISTANT`) turn — so the
**next real, lexical turn opens freely** via `begin_cloud_turn()`, whose
own "abandon the still-open current turn" logic cleanly supersedes the
never-transcribed noise turn (marking it `ABANDONED`, never committed).
Proven by
`test_non_lexical_interruption_invents_no_text_and_next_turn_recovers`
(invariants 9, 11): after the throat-clear, `session.history` is
unchanged (no invented entry); the next real turn commits normally with
`session.history` correctly extended by exactly two entries (user +
assistant).

## OLD/NEW PROVIDER TRANSCRIPT OWNERSHIP

Two independent, complementary mechanisms, deliberately not merged into
one:

* **Pre-confirmation window** (candidate speaking, not yet quarantined):
  closed by this checkpoint's two additions above
  (`has_turn_awaiting_assistant()` gating `begin_cloud_turn()`, and
  `user_final` gating `on_user_transcription`) — the OLD provider's own
  stray candidate transcript, if it arrives here, is discarded before it
  can attribute to anything.
* **Post-confirmation window** (OLD provider quarantined): closed entirely
  by R0045's existing, unmodified provider-instance isolation —
  `_consume_provider_events` checks `provider_handle.replacement_requested`
  as the very first thing examined for every event pulled from the OLD
  provider's queue, and `break`s before that event (or any subsequent one)
  ever reaches `router.handle_provider_event`. R0046 adds **no** new
  mechanism for this half — it was already provably closed. The NEW
  provider's own transcript, once the swap completes, reaches
  `router.on_user_transcription` exactly like any other provider's events
  always have (`_consume_provider_events` is completely unmodified by this
  checkpoint) and correctly targets the freshly-opened turn N+1 (its
  `user_final` starts `False`, so nothing blocks the first legitimate
  write).

No `_consume_provider_events` changes were needed at all — confirmed by
`git diff` — because the corruption vector was always in the two call
sites above, never in the provider-event consumption loop itself.

## CANONICAL HISTORY RESULT

`ConversationSession.history` now reliably becomes `USER1, ASSISTANT1,
USER2, ASSISTANT2, ...` (invariant 5) for ordinary turns, and
`USER(interrupted), USER(next), ASSISTANT(next), ...` for confirmed
interruptions — proven for two consecutive interruptions in a row
(`test_two_consecutive_interruptions_preserve_history_ordering`, invariant
17): `["question one", "question two", "question three", "final reply"]`,
oldest first, never duplicated or reordered. `commit_cloud_turn()`/
`CloudTurnAccumulator` themselves are **unchanged** except for the one
`user_final` guard line — the "at most one commit per turn" invariant
(invariant 13) was already fully proven by R0045/earlier checkpoints and
is unaffected.

## CLOUD SNAPSHOT RESULT

**PASS — no missing prior turns, no duplicate interrupting user turn, no
unspoken assistant text, no OLD provider state.**
`test_snapshot_after_interruption_contains_prior_turns_not_the_new_one`
builds two ordinary completed turns, confirms a snapshot
(`build_cloud_context_snapshot`) contains both (4 entries), then confirms
turn N ("tell me about black holes") and calls
`router.request_fresh_snapshot_after_resumption_failure()` — the exact
method `start_fresh_cloud_provider` (R0045's atomic-replacement primitive)
calls to build the replacement provider's context. The result: the 4 prior
entries, unchanged and in order, plus turn N's own user text (the
conservative interruption rule — empty assistant text, so no assistant
entry) — **5 entries total**, and the NEW canonical turn N+1 (opened for
the interrupting utterance, not yet committed) does **not** appear
anywhere in it, because `build_cloud_context_snapshot` reads only
`session.history` (committed turns), never the in-flight accumulator. This
is why "no duplicate interrupting user turn" is structurally guaranteed,
not merely tested: the snapshot is always built (synchronously, before any
PCM replay) from state that cannot yet contain the turn being replayed.

## R0045 REGRESSION CHECK

All 5 `TestAtomicProviderReplacement` tests still pass — their hand-rolled
`_on_confirmed` mirror was updated with the one new
`router.begin_cloud_turn()` line to stay faithful to production (verified
this is inert for their own assertions: none of them check
`session.history`/`router._turn` state affected by the addition except
`test_recovery_after_no_transcript_interruption_via_replacement`, which
already accounted for turn-commit behavior from R0045 and needed no
further change). `TestVadBridgeQuarantine` (2 tests) unaffected — those
test audio buffering only, exercised before the bridge's new
`router`-dependent code path is ever reached, and the constructor call
sites were updated to pass a minimal real `ConversationRouter`. All other
R0038/R0041/R0042/R0043/R0044/R0045 test classes in
`test_realtime_gemini_runtime.py` (54 tests, unchanged) still pass — R0045
CASE 2 remains closed, exact-once PCM replay remains green, `dispatched_
for_turn` re-arm logic untouched.

## PERFORMANCE INSTRUMENTATION

None added or needed — this checkpoint is purely about canonical-history
correctness, never audio timing. R0045's 8 timing points are unaffected.

## FILES CHANGED

```
src/nexa/realtime/gemini/runtime.py   |  78 ++++++++
src/nexa/realtime/router.py           |  25 ++-
src/nexa/realtime/turn.py             |  17 +-
tests/test_realtime_gemini_runtime.py | 363 +++++++++++++++++++++++++++++++++-
tests/test_realtime_router.py         |  45 +++++
tests/test_realtime_turn.py           |  49 +++++
6 files changed, 568 insertions(+), 9 deletions(-)
```

* `src/nexa/realtime/turn.py` — `CloudTurnAccumulator.on_user_transcription`
  gains the `cur.user_final` guard.
* `src/nexa/realtime/router.py` — new `ConversationRouter.has_turn_awaiting_assistant()`
  method (imports `CloudTurnState`).
* `src/nexa/realtime/gemini/runtime.py` — `_VadToProviderBridge` gains a
  required `router` constructor param and the VAD-start gate;
  `build_gemini_voice_runtime` passes `router=router` at the one bridge
  construction site; `_on_confirmed` gains the `router.begin_cloud_turn()`
  call; module docstring gains an M2.6B.4H section.
* `tests/test_realtime_gemini_runtime.py` — added `_fresh_router()` helper;
  updated the 4 existing bridge-construction call sites
  (`TestVadBridgeProcessorLifecycle` x2, `TestVadBridgeQuarantine` x2) to
  pass a router; updated `TestAtomicProviderReplacement._wire()`'s
  `_on_confirmed` mirror with the new `begin_cloud_turn()` line; added
  `TestProductionCanonicalTurnLifecycle` (5 tests, the production-wiring
  proof).
* `tests/test_realtime_router.py` — added `TestHasTurnAwaitingAssistant`
  (5 tests).
* `tests/test_realtime_turn.py` — added
  `TestForeignTranscriptCannotOverwriteFinalizedUserText` (3 tests).

## TEST RESULTS

```
$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py -q
59 passed, 1 warning in ~14s

$ .venv/bin/python -m pytest tests/test_realtime_router.py -q
20 passed in 0.12s

$ .venv/bin/python -m pytest tests/test_realtime_turn.py -q
14 passed in 0.06s

$ .venv/bin/python -m pytest tests/test_realtime_gemini_service.py -q
35 passed, 1 warning

$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 959 tests in 62.250s
OK (skipped=7)
```

(The one traceback in the full-suite run is expected test output —
`TestArchitectureAndReset.test_20b_on_release_callback_raising_does_not_break_speech`
deliberately raises inside an `on_release` callback to verify the
continuity controller's own error resilience; caught, logged, test
passes — unrelated to this checkpoint.)

`ruff check src/ tests/` — All checks passed.
`.venv/bin/pip check` — No broken requirements found.
`git diff --check` — clean.

PL/EN role-card tests (`TestNativeOnlyLanguageDispatch`,
`TestSystemInstructionLanguagePolicy`, `TestCloudSameTurnLanguage`) and
zero-LID tests (`TestNoLocalLidInCloudRuntime`) remain green as part of
the full `test_realtime_gemini_runtime.py` run above.

One transient, non-reproducible flake was observed during development (a
single real-Pipecat-pipeline test failed once when run as part of the
full file, with no `StartFrame reached the end of the pipeline` line ever
appearing in its log — consistent with an async pipeline-startup race
under load, not a logic defect) and did not reproduce across 3 subsequent
full-file runs, nor in isolation. Documented here rather than hidden;
noted as a pre-existing category of risk this test-infrastructure pattern
already carries (shared with `TestVadBridgeQuarantine`/
`TestVadBridgeProcessorLifecycle`, unrelated to R0046's own logic).

## LOCAL VOICE FREEZE CHECK

```
$ git diff --stat -- src/nexa/voice src/nexa/voice_tts
(empty)
```

Zero diff — local voice is completely untouched by this checkpoint.

## COMMIT HASHES

fix/report commit: `24ace00`

(recorded in the follow-up hash-record commit)

## GIT STATUS

At the time of writing (before this checkpoint's commit):

```
 M src/nexa/realtime/gemini/runtime.py
 M src/nexa/realtime/router.py
 M src/nexa/realtime/turn.py
 M tests/test_realtime_gemini_runtime.py
 M tests/test_realtime_router.py
 M tests/test_realtime_turn.py
?? docs/reports/R0046_m2_6b_4h_production_canonical_cloud_turn_lifecycle_20260912.md
```

Not pushed.

## EXACT NEXT LIVE COMMAND

Unchanged launch command — this checkpoint is entirely internal to
`nexa.realtime` wiring; no new CLI flags were added:

```
.venv/bin/python apps/nexa_cloud_voice_app.py --bargein
```

## EXACT ATTEMPT #3 SCRIPT

1. Have two or three ordinary exchanges; after each, confirm (via logs or
   a debugger, since there is no UI yet) that `ConversationSession.history`
   actually grew by two entries (user + assistant) — this is the FIRST
   time this has ever been directly observable on real hardware.
2. Interrupt a reply with a clear, lexical utterance — confirm the OLD
   reply's turn commits as interrupted (empty assistant text) and the
   interrupting utterance itself lands as its own, correctly-ordered
   canonical turn once its (new-provider) reply completes.
3. Interrupt with a non-lexical sound (cough/throat-clear) — confirm no
   `"<cough>"`-shaped or empty-but-present canonical turn appears in
   history, and the next real turn commits normally.
4. Speak two overlapping utterances in quick succession (a genuine
   interruption candidate that gets rejected — a brief sound during a
   reply that stops before confirming) — confirm the still-open turn's
   own canonical history is completely unaffected.
5. Confirm no operator-audible regression versus the R0045 baseline for
   ordinary and interrupted turns — this checkpoint changes zero audio
   behavior, only canonical bookkeeping, but only a live run confirms it.
