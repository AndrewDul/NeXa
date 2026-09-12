# R0044 — M2.6B.4F: Strict Post-Interruption Response Ownership

**Date:** 2026-09-12
**Milestone:** M2.6B.4F (follow-on to M2.6B.4E / R0043)
**Status:** Root cause of a CONCRETE R0043 regression CONFIRMED + fixed.
One residual gap (CASE 2) proven, by source audit, to be unresolvable
without provider/session replacement — reported, not implemented. No
Gemini call, no hardware. **Hardware acceptance remains FAIL. `M2.6B`
remains IN PROGRESS.**

## R0043 VALID FINDING

Unchanged and preserved: a non-lexical interruption (throat-clear) can
close a real local VAD turn while Gemini produces **zero**
`UserTranscriptionEvent`s for it. R0043's `local_turn_closed_seq` counter
(incremented unconditionally in `GeminiLiveProvider.user_turn_end()`) is
the correct, necessary mechanism for recovering dispatch in that case —
without it, `dispatched_for_turn` sticks `True` forever and all future
assistant audio is silently dropped while text keeps flowing. This
finding, and that mechanism, are **kept**.

## R0043 RESIDUAL RISK

R0043 disclosed, honestly, that its fallback re-arm could in principle
promote a "single stray trailing chunk" at an interruption boundary. This
checkpoint **quantified that risk precisely** with a new adversarial test
(CASE 1) and found it was **not a rare, narrow-window edge case** — it
was a **deterministic, 100%-reproducible defect** in R0043's fallback AS
WRITTEN: the fallback only required `local_turn_closed_seq` to advance by
**1** past its value at dispatch time, and that threshold is satisfied
merely by **the interrupting utterance's own local turn closing** — the
SAME closure that caused the interruption in the first place, before any
genuinely new turn has even started. Any trailing old-generation audio
arriving after that single closure (but before a real new turn) was
therefore wrongly promoted into a fresh, valid generation and played
audibly. **Verified empirically**: reverting the fix (threshold "1")
reproduces this exact failure (`[b'gen-1-chunk', b'OLD-1', b'OLD-2']`
instead of `[b'gen-1-chunk']`); restoring the fix (threshold "2")
corrects it.

## SOURCE-SUPPORTED RESPONSE BOUNDARY

Exhaustively audited, per the charter's own list, to determine whether
ANY event/frame/field exposed by the installed Pipecat 1.8.1 /
`google-genai` stack can serve as a hard barrier proving "this assistant
event belongs to the response for the NEW user turn":

| candidate | source finding | usable as a barrier? |
|---|---|---|
| `google.genai.types.LiveServerMessage` / `LiveServerContent` (the actual Live API message types) | Read the full field list directly from installed `google/genai/types.py`. **No response id, turn id, invocation id, or generation id field exists anywhere on these types.** (A `response_id` field DOES exist, but only on `GenerateContentResponse` — the separate, non-Live, request/response API type — never on `LiveServerMessage`.) | **NO — does not exist** |
| `serverContent.interrupted` | `LiveServerContent.interrupted`'s own docstring: "a good signal to stop and empty the current queue" — no claim about what may still arrive afterward. Official docs (WebFetch, `ai.google.dev/gemini-api/docs/live-guide`): confirms "the ongoing generation is canceled and discarded" but explicitly does **not** document whether more trailing content for that generation can still arrive on the wire after this signal, nor the ordering relationship between `interrupted`/`generation_complete`/`turn_complete`. | **NO — not documented as airtight** |
| `generation_complete` | `LiveServerContent.generation_complete`'s own docstring: "When model is interrupted while generating there will be **no** generation_complete message in interrupted turn" — i.e. this signal is explicitly **absent** for an interrupted generation, so it cannot mark that generation's end at all. | **NO — doesn't fire for the interrupted case** |
| `turn_complete` | Installed `_handle_msg_turn_complete` (`gemini_live/llm.py`): only pushes `LLMFullResponseEndFrame` `if self._bot_is_responding:` — and `_bot_is_responding` was ALREADY set `False` by our own `cancel()`'s `_handle_interruption()` before this message typically arrives, so the code's own comment confirms: "Do not send LLMFullResponseEndFrame here on interruption — the assistant context aggregator already knows." **No end-of-old-generation frame is emitted for an interrupted turn at all.** | **NO — suppressed for the interrupted case** |
| `LLMFullResponseStartFrame` | Installed `_handle_msg_model_turn`: pushed only when `self._bot_is_responding` transitions False→True — a **single local boolean with no correlation to Gemini's generation identity whatsoever**. Since our own `cancel()` already set this False, the very NEXT audio chunk to arrive — even if it is trailing OLD-generation content — will ALSO see `_bot_is_responding == False` and get a FRESH `LLMFullResponseStartFrame` emitted for it. **This frame can be, and structurally will be, falsely triggered by trailing old audio.** | **NO — provably falsifiable, not a barrier** |
| `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` | Purely playback-lifecycle frames (`BaseOutputTransport`), carry no generation identity either; already used for `_ResponseLifecycle`, unrelated to this question. | **NO — no identity information** |
| WebSocket message ordering | `session.receive()` is a single TCP/WebSocket stream — messages arrive in the order Gemini's server sent them (a real, confirmed transport-level guarantee). But this is **delivery order only** — it proves nothing about which logical generation a given message's content belongs to. | **Real guarantee, but insufficient alone** |
| `CancellationCompleteEvent` (our own local signal) | `GeminiLiveProvider.cancel()`: `await self._worker.queue_frames([frame])` then immediately `self._events.put_nowait(CancellationCompleteEvent())`. Installed `PipelineWorker.queue_frame()`: `await self._push_queue.put(frame)` — a plain enqueue, returns as soon as the `put` succeeds, **does not wait for the frame to be processed anywhere in the pipeline**. This event fires almost immediately and proves nothing about downstream delivery/drainage. | **NO — fires before any real processing** |

**Conclusion: no airtight in-session (same-connection) boundary exists.**
No response/turn/generation identifier is exposed anywhere in the
installed Live API types; every local proxy signal (`_bot_is_responding`,
`turn_complete`, `CancellationCompleteEvent`) is either suppressed for the
interrupted case or is untagged local bookkeeping that can be, and will
be, falsely triggered by trailing old content. This is not inferred from
observed ordering — it is a direct reading of the type definitions and
the exact code paths that produce each candidate frame.

## FOUR-ACK BARRIER ANALYSIS

Traced precisely why `InterruptionFrame` handling makes NONE of the four
acks (from R0043) a drain barrier either:

- `InterruptionFrame` is a Pipecat `SystemFrame` (confirmed:
  `class InterruptionFrame(SystemFrame)`, `frames.py:1142`).
  `FrameProcessor.__input_frame_task_handler` processes `SystemFrame`s
  **immediately**, bypassing `__process_queue` entirely — while ordinary
  `DataFrame`/`ControlFrame` instances (like `TTSAudioRawFrame`,
  `LLMFullResponseStartFrame`) are queued into `__process_queue` and
  drained by a **separate** task, in order, but not with priority.
- This means an `InterruptionFrame` reaching `down_tap` (our event
  translator) is processed and translated into a `ProviderInterruptionEvent`
  **ahead of** any `TTSAudioRawFrame` chunks that were already sitting,
  unprocessed, in `down_tap`'s own `__process_queue` at that moment — those
  chunks are **not discarded**, they are still delivered afterward, in
  order, by `down_tap`'s own process-queue task. Observing an ack
  therefore proves nothing about whether more trailing audio for the
  interrupted generation is still queued up behind it.
- `broadcast_interruption()`'s own `self.__reset_process_task()` (which
  DOES clear a process queue) is called only on the **calling**
  processor's own queue (`self`) — for our own `cancel()`, that is
  `self._llm`'s queue (upstream-side bookkeeping), never `down_tap`'s;
  for Gemini's own ack, that is also `self._llm`'s queue. Neither
  interruption path ever resets `down_tap`'s queue, which is the one that
  actually matters for "is there more trailing audio still in flight
  downstream."
- **This confirms R0043's own finding** (2 local + 2 remote = 4 acks) is
  correct, and additionally confirms **why** none of the four — local or
  remote — can serve as a "no more audio is coming" signal: the mechanism
  that fires them is architecturally decoupled from the queue that would
  need to be proven empty.

## FINAL OWNERSHIP MODEL

Kept: `_ResponseGenerationGuard` (is_valid/start_new_generation/interrupt),
the original R0038 final-transcript fast-path reset, R0043's
`local_turn_closed_seq` fallback mechanism, all diagnostics.

**Changed**: `_ResponseGenerationGuard.interrupt()` now takes an optional
`local_turn_seq_at_interrupt: int | None` and records it as
`invalidated_at_turn_seq` (cleared again on the next
`start_new_generation()`). The fallback re-arm check in
`_consume_provider_events` now requires:

```
provider.local_turn_closed_seq
    >= generation_guard.invalidated_at_turn_seq
       + MIN_LOCAL_TURN_CLOSURES_BEFORE_FALLBACK_REARM   # = 2
```

instead of R0043's original "+1 relative to dispatch time." The baseline
is captured **at interrupt time** (in `build_gemini_voice_runtime`'s
`_on_confirmed`, via `provider_handle.current.local_turn_closed_seq`),
not at dispatch time — interrupt time is necessarily earlier (mid-
playback of the generation being interrupted), so "+2" from that point
requires: (1) the interrupting utterance's own local turn to close, AND
(2) a genuinely SEPARATE, subsequent local turn to ALSO close, before the
fallback trusts the next assistant event. The original final-transcript
fast path is **completely unchanged** — this is a strict, additive
narrowing of the fallback path only.

`ProviderInterruptionEvent` also gained a `source` field
(`"local_cancel"` / `"remote_server_ack"`, tagged in
`GeminiLiveProvider._translate_frame` by tracking which `InterruptionFrame`
instances `cancel()` itself constructed) — **diagnostic only**, per the
FOUR-ACK BARRIER ANALYSIS above; nothing branches on it.

## WHY OLD AUDIO CAN NEVER RETURN

> **Erratum (added by R0045, 2026-09-12):** this heading is not an
> unconditional guarantee as written below — CASE 2 (documented open
> below, under ADVERSARIAL CASE 2 RESULT) is a live counter-example
> under the mechanism this checkpoint (R0044) shipped. **R0044 proved no
> airtight same-session response-ownership boundary exists** while
> continuing to consume events from the same Gemini provider/session —
> Gemini's Live API exposes no response/turn/generation identifier on
> any server message, so no local signal (transcript, turn-closure
> count, or any "+N" heuristic) can distinguish CASE-2's old, delayed
> audio from genuinely new content. **R0045 changes the architecture to
> provider-instance isolation** (atomic provider/session replacement on
> every confirmed local barge-in, never merely re-trusting the same
> provider after enough local turn closures) — under R0045, CASE 2 is
> closed: see `docs/reports/R0045_m2_6b_4g_atomic_provider_replacement_20260912.md`,
> "R0044 CASE-2 RESULT UNDER R0045". The mechanism described below
> (`_ResponseGenerationGuard` + the "+2" fallback) was superseded and
> removed by R0045, not merely narrowed further.

Two independent, unchanged mechanisms combine:

1. **While a generation is invalid** (`generation_guard.is_valid(current_gid)`
   is `False`), the ONE call site that ever reaches
   `hw_worker.queue_frames()` for assistant audio is gated on this check —
   unchanged from R0038, re-verified passing (`test_5`/CASE 1/CASE 5).
2. **The fallback re-arm cannot fire prematurely for the specific,
   concretely-demonstrated CASE-1 shape** (old audio arriving after only
   the interrupting utterance's own closure) — closed by the "+2"
   requirement, proven both by adversarial test (CASE 1, CASE 4, CASE 5)
   and by explicit before/after empirical verification (reverting to "+1"
   reproduces the exact failure this checkpoint set out to fix).

**Honest limit, not overclaimed**: CASE 2 (old audio arriving strictly
AFTER the "+2" threshold is already satisfied by a genuinely new turn's
own closure) remains open — proven, not merely suspected, by
`test_case_2_old_delayed_audio_after_new_turn_closes_is_an_open_gap`,
which documents the ACTUAL (not idealized) outcome. This is not a bug
introduced by this checkpoint; it is the same fundamental limitation the
SOURCE-SUPPORTED RESPONSE BOUNDARY section proves is unavoidable without
either a Gemini-exposed identifier (does not exist) or provider/session
replacement (see FIX DESIGN ALTERNATIVES below, not implemented).

## WHY NEW AUDIO CAN ALWAYS RECOVER

Unchanged from R0043, re-verified: the fallback path fires purely from
`local_turn_closed_seq` (driven only by NeXa's own local VAD authority
calling `user_turn_end()`), never from anything Gemini must transcribe or
acknowledge — so a genuinely new turn's audio is guaranteed to eventually
become dispatchable even if Gemini transcribes nothing for either the
interrupting sound or the new turn itself (CASE 3, and the original R0043
test, both still pass).

## ADVERSARIAL CASE 1 RESULT

**PASS (fixed by this checkpoint).** `interrupt -> local turn closes ->
OLD audio -> OLD audio -> NEW user turn -> fresh response boundary -> NEW
audio` → all OLD audio dropped, NEW audio plays
(`[b'gen-1-chunk', b'NEW-1']`). Confirmed to FAIL under the pre-R0044
"+1" design (`[b'gen-1-chunk', b'OLD-1', b'OLD-2']`) via a temporary,
reverted-and-restored empirical check.

## ADVERSARIAL CASE 2 RESULT

**DOCUMENTED OPEN GAP, not silently accepted.** `interrupt -> local turn
closes -> NEW user turn -> OLD delayed audio -> fresh response boundary
-> NEW audio` → the "OLD delayed audio" chunk **is** wrongly promoted
(`[b'gen-1-chunk', b'OLD-delayed']` after that point), because by the
time it arrives the "+2" threshold is already satisfied by the genuinely
new turn's own closure, and no signal distinguishes it from real new
content. **Recovery is not permanently broken by this**: a further,
truly-new chunk (`NEW-1`) still plays correctly afterward. This case is
proven, by the SOURCE-SUPPORTED RESPONSE BOUNDARY audit, to be
unresolvable by any local, non-timer, non-session-replacement signal.

## ADVERSARIAL CASE 3 RESULT

**PASS.** `interrupt -> no transcript for noise -> new lexical user turn
-> assistant audio arrives before final user transcript` → no permanent
silence (audio plays via the fallback path, which fires from local turn
closure, before the transcript arrives), and ownership is correct (the
first assistant event after the interruption IS the genuinely new turn's
own content in this scenario, with no intervening old audio to
mis-promote). The late-arriving transcript changes nothing afterward.

## ADVERSARIAL CASE 4 RESULT

**PASS.** Four `ProviderInterruptionEvent`s (2 local + 2 remote, per
R0043/this report's own FOUR-ACK analysis) with old audio delayed between
them, followed by a new turn and new response → identical safe result to
CASE 1. Confirms ack multiplicity never influences the ownership decision
— nothing in `_consume_provider_events` branches on `ProviderInterruptionEvent`
count or `.source` for dispatch purposes, only for logging.

## ADVERSARIAL CASE 5 RESULT

**PASS.** Two interruptions across consecutive assistant responses:
generation N interrupted (its own trailing audio never returns), N+1
dispatched and later ALSO interrupted (its own trailing audio, arriving
in the CASE-1 shape, is correctly dropped by the same "+2" mechanism now
applied to the SECOND interruption independently), N+2 dispatches and
plays correctly. Final hardware queue:
`[gen-N-chunk, gen-N+1-chunk, gen-N+2-chunk]` — no audio from N or N+1
ever returns.

## FIX DESIGN ALTERNATIVES CONSIDERED (not implemented)

Per the charter's own enumerated options:

- **A. A Gemini/Pipecat response-start frame tied to the new turn** —
  REJECTED; `LLMFullResponseStartFrame` proven falsifiable (see table
  above).
- **B. Server interruption ACK as a hard drain barrier** — REJECTED; not
  documented as such by Google, and the FOUR-ACK analysis shows it isn't
  even correlated with `down_tap`'s own queue state.
- **C. Provider-side response epoch from a source-backed boundary** — NOT
  POSSIBLE; no response/turn/generation identifier exists anywhere in the
  Live API's exposed types.
- **D. Queue quarantine until a provable fresh-response boundary** —
  reduces to "quarantine forever within this session," since no such
  boundary exists; this is effectively what the CURRENT design already
  does for CASE 2's shape (the audio remains ambiguous, not proven
  fresh) — the practical difference is that this checkpoint's "+2"
  narrows WHEN the ambiguous window opens, closing the CONCRETE CASE-1
  regression, without pretending the ambiguity is eliminated.
- **E. Provider/session replacement per confirmed interruption** — **the
  only architecturally airtight option**, because a NEW provider instance
  has its own separate `_events` queue, own separate pipeline/worker, own
  separate `self._llm` — structurally incapable of ever delivering stale
  content from the OLD instance, since NeXa would simply stop reading the
  old instance's queue (exactly the mechanism `recover_from_mid_turn_loss`
  already implements for connection loss). **Real, unsized costs**: a
  fresh session requires a new WebSocket handshake (~450ms per R0031's
  own measured connect time, likely more with the one-time history/context
  re-seed), discards Gemini's own live conversational context for the
  interrupting turn (NeXa's OWN canonical history is unaffected — only
  the cloud model's immediate context resets, requiring an explicit fresh
  `CloudContextSnapshot`), and would mean **every** confirmed barge-in —
  not just the rare non-lexical/edge case — pays this cost, since CASE 2
  can occur after ANY interruption, not only a non-lexical one. This has
  not been measured against real hardware and is a genuine product/UX
  trade-off (snappy barge-in responsiveness vs. absolute audio-ownership
  guarantee), not something to adopt casually. **Not implemented this
  checkpoint** — reported per the charter's own explicit instruction.

## FILES CHANGED

- `src/nexa/realtime/provider.py` — `ProviderInterruptionEvent` gained a
  `source: str = "unknown"` field (diagnostic only).
- `src/nexa/realtime/gemini/service.py` — `GeminiLiveProvider` gained
  `_local_cancel_frame_ids: set[int]` (tracks ids of `InterruptionFrame`
  instances `cancel()` itself constructs); `cancel()` records its frame's
  id before queuing; `_translate_frame` tags `ProviderInterruptionEvent.source`
  by membership check. Module docstring updated (M2.6B.4F section).
- `src/nexa/realtime/gemini/runtime.py` — `_ResponseGenerationGuard`
  gained `_invalidated_at_turn_seq`/`invalidated_at_turn_seq` and an
  `interrupt(*, local_turn_seq_at_interrupt=None)` parameter (cleared on
  `start_new_generation()`); new module constant
  `MIN_LOCAL_TURN_CLOSURES_BEFORE_FALLBACK_REARM = 2`;
  `_consume_provider_events`'s fallback re-arm check rewritten to use the
  new "+2 from interrupt-time baseline" condition (replacing R0043's "+1
  from dispatch-time baseline"); `build_gemini_voice_runtime`'s
  `_on_confirmed` now passes `local_turn_seq_at_interrupt=` into
  `generation_guard.interrupt()`. Module docstring updated (M2.6B.4F
  section in both the class docstring and the method docstring).
- `tests/test_realtime_gemini_service.py` — +1 test
  (`test_10_interruption_source_distinguishes_local_cancel_from_remote_ack`).
- `tests/test_realtime_gemini_runtime.py` — updated the three existing
  `_wire()` test helpers (`TestBargeInWiring`,
  `TestGenerationGuardWiredIntoConsumer`, `TestPostInterruptionAudioRecovery`)
  to pass the new `local_turn_seq_at_interrupt` kwarg; +4 new
  `_ResponseGenerationGuard` unit tests; +1 new test class
  `TestStrictPostInterruptionOwnership` (subclasses
  `TestPostInterruptionAudioRecovery` — inherits its 7 tests, adds the 5
  adversarial CASE tests).
- **Zero changes** to `src/nexa/voice/bargein.py`,
  `src/nexa/voice/interruption.py`, `src/nexa/realtime/router.py`,
  `src/nexa/realtime/snapshot.py`, `apps/nexa_cloud_voice_app.py`,
  `src/nexa/stt/**`, `src/nexa/conversation/**`, or anything under
  `src/nexa/voice_tts/**`. Model, voice, role-card wording, zero-LID
  decision, server VAD, local Silero, AEC architecture,
  `ConversationSession`, `CloudContextSnapshot`, barge-in thresholds are
  all unchanged.

## TEST RESULTS

- `ruff check src/nexa/realtime/provider.py src/nexa/realtime/gemini/service.py
  src/nexa/realtime/gemini/runtime.py tests/test_realtime_gemini_service.py
  tests/test_realtime_gemini_runtime.py` → all checks passed.
- `python -m unittest tests.test_realtime_gemini_runtime` → **64 tests,
  OK** (was 48 before this checkpoint: +4 `_ResponseGenerationGuard` unit
  tests, +5 new adversarial CASE tests, +7 inherited-and-re-run tests from
  `TestPostInterruptionAudioRecovery` via subclassing — 48 + 4 + 5 + 7 =
  64).
- `python -m unittest tests.test_realtime_gemini_service` → 35 tests, OK
  (+1 from this checkpoint).
- `python -m unittest discover -s tests` → **956 tests, OK (skipped=7)**
  — up from 939, zero regressions, zero new skips.
- `git diff --check` → clean.
- `pip check` → no broken requirements.
- **Empirical before/after verification** (not merely inspection): CASE 1
  reproduced as a genuine failure with the threshold temporarily reverted
  to "1" (`[b'gen-1-chunk', b'OLD-1', b'OLD-2']`), then confirmed fixed
  with the threshold restored to "2" (`[b'gen-1-chunk']` before the new
  turn, `[b'gen-1-chunk', b'NEW-1']` after).

## LOCAL VOICE FREEZE CHECK

`git diff --stat -- src/nexa/voice src/nexa/voice_tts` → **empty**. Local
voice completely untouched this checkpoint.

## COMMIT HASHES

fix/report commit: `8ce7139`

(recorded in the follow-up hash-record commit)

## GIT STATUS

Not pushed (standing constraint for the whole M2.6B session).

## EXACT NEXT LIVE RETEST COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Unchanged. Real reSpeaker + real USB speaker + real Gemini. Recommended
coverage for Attempt #3 (same as R0043's own recommendation, still
current): repeat the short PL/interrupt/EN/EN/PL script, deliberately
including a non-lexical interruption (throat-clear/cough) partway through
a reply, followed by at least two further real turns — confirm the
interrupted reply stops immediately, a subsequent real turn's reply IS
audible, and native PL/EN mirroring still holds. This checkpoint narrows
(does not eliminate) the theoretical risk of one stray old-generation
chunk becoming briefly audible at an interruption boundary — if that
specific symptom (a fragment of an OLD reply audible immediately before a
new one, rather than the PREVIOUS symptom of PERMANENT silence) is ever
observed live, that is expected, documented, residual behaviour (CASE 2),
not a new regression — and would be the concrete evidence needed to size
and justify Option E (provider/session replacement) as a future
checkpoint, not evidence that this checkpoint's fix failed.
