# R0029 — M2.5B: Production Barge-In / Interruption

- **Date:** 2026-09-09
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.5B — production
  barge-in / interruption.**
- **Status:** **LIVE ACCEPTANCE FAILED once (2026-09-09) → M2.5B.1
  (interrupt integrity + cancellation) done; M2.5B.2 (long-session context
  / KV-cache stability) IN PROGRESS. NOT operator-confirmed.** The first
  `--bargein` operator session showed: AEC healthy; barge-in cancellation
  worked; PL↔EN switching worked — **but** (1) an interrupting utterance
  spoken as two VAD segments was **fragmented** — **FIXED** (M2.5B.1,
  capture/coalesce phase); and (2) model time-to-first-token degraded to
  tens of seconds later in the session. **Root cause of (2):** the context
  window evicting its oldest turn every turn once history exceeds the cap
  permanently collapses Ollama's prompt-prefix KV-cache reuse (78 s at cap
  20, 154 s at cap 40). M2.5B.1's `max_turns` 20→40 only *moved* that
  cliff. **M2.5B.2** `ProviderWindow` — a prefix-stable, bounded
  *provider-facing* window over the (still complete) canonical
  `ConversationSession.history` — **eliminates the permanent collapse**:
  steady-state turns stay ~4 s for the whole session, and a real-Pi
  110-turn run showed no drift and no permanent regime. The **residual**
  is the window's boundary crossing: the first M2.5B.2 implementation left
  a ~20–32 s synchronous "rollover" turn, which is **not acceptable** for
  NeXa (a persistent companion runs for hundreds of exchanges). The fix
  under way is a **context reset** (`keep_entries = 0`) so the boundary
  turn re-prefills only the persona prefix (cached) + the new user turn —
  an ordinary ~4–6 s turn. `OLLAMA_NUM_PARALLEL=2` was measured on the Pi
  and **rejected** (SWA cache interference → concurrent foreground turn
  cold-reprocesses ~49 s). Responsive cancellation (`cancel → worker-stop`
  ~2 s → 251 ms) stands. `--no-bargein`'s mic policy is byte-for-byte
  R0026.
  **M2.5B.3 (2026-09-10):** a real live `--bargein` conversation —
  **operator rates conversation quality, naturalness, voice, local
  response speed and PL/EN switching GOOD / ACCEPTABLE.** One remaining
  defect: the interruption-capture phase sometimes hung to the 12 s
  controller hard cap + 15 s adapter timeout, and once busy-dropped
  6360 ms of an interruption's own audio. **Root-caused + FIXED** — the
  settle window was armed only on a VAD `INTERRUPT_SEGMENT_ENDED`, and
  `broadcast_interruption()` flushes the `BargeInController`'s own frame
  queue, so on a lagged Pi loop that segment-END could be dropped and the
  phase could never settle. The settle is now armed at confirm and driven
  by a `_last_vad_activity` timestamp (every VAD frame, incl.
  `UserSpeakingFrame`); `_interrupt_open_segments` no longer gates
  finalisation; a trailing segment of the confirmed interruption is never
  DROP_BUSY'd. See *M2.5B.3*. **Still NOT operator-confirmed** — one short
  live re-test of the interruption lifecycle is owed. Not pushed.
- **Related:** `R0028` (M2.5A architecture + real-hardware feasibility;
  **M2.5A COMPLETE / OPERATOR-CONFIRMED 2026-09-09**), `R0026` (the
  half-duplex behaviour this milestone replaces when enabled), `R0027`
  (one-turn vs sticky response language — must survive an interruption),
  `R0025`/`R0024` (bilingual STT), `ADR-0003` D8 ("barge-in is M2.5"),
  `R0009` (KV-cache prefix discipline).

---

## TASK RESULT

**IMPLEMENTED; LIVE ACCEPTANCE FAILED once; M2.5B.1 done, M2.5B.2 done
(pending its final benchmark run).** Production barge-in is implemented
end-to-end behind `LocalAudioConfig.bargein_enabled` (default `False`).
With the flag off the M1/M2.1/M2.4/R0026 pipeline is **byte-for-byte
unchanged**. The first live `--bargein` session (2026-09-09) surfaced two
defects — **Problem 1** interrupt-utterance fragmentation (**FIXED**,
commit `2460463`, regression-tested) and **Problem 2** late-session model
TTFT collapse. Problem 2 was **root-caused** (a context-window eviction
that permanently breaks Ollama prompt-prefix KV-cache reuse — 78 s at cap
20, 154 s at cap 40); M2.5B.1's `max_turns` 20→40 only *moved* the cliff;
**M2.5B.2** (`ProviderWindow` — a prefix-stable bounded provider-facing
window over the complete canonical history, boundary crossing = a
`keep_entries=0` **context reset** costing one ~3.4 s ordinary turn)
**eliminates it**. **Not `OPERATOR-CONFIRMED`.** With the flag on:

- NeXa's TTS PCM is teed to the XVF3800 AEC far-end reference
  (`plug:respeaker`) so the microphone can safely stay hot during a reply;
- a **sustained** operator interruption (≥ 300 ms VAD, no intervening
  stop) cancels the reply — old audio dropped by Pipecat
  `handle_interruptions()`, Ollama generation cancelled via the per-turn
  `CancelToken`, planner / continuity / bridge queue flushed of stale
  text, exactly the one interrupting utterance admitted, history committed
  correctly, same `ConversationSession`, bilingual semantics preserved;
- if the AEC reference feed cannot start or dies, barge-in **disables
  itself** for that response and the mic returns to R0026 whole-response
  suppression — loudly (telemetry), never a silent unsafe hot mic.

**38 + 12 + 13 tests** (`test_bargein_m2_5b.py` / `_wiring_m2_5b.py` /
`_m2_5b1.py`) + updated guards; **full suite `pytest` 684 / `unittest`
691, `ruff` clean, `git diff --check` clean**; a **hardware-safe integration smoke** (real
reSpeaker + real `aplay -D plug:respeaker`, operator absent) passed: AEC
reference active, mic hot during a simulated reply, **0 false candidates /
0 confirmations while silent**, clean teardown.

**Remaining:** one short live operator session (below). The Problem 2
analysis is done (real Pi, `gemma4:e4b`): the cause was **context-window
eviction breaking KV-cache prefix reuse**, fixed by resizing the window;
no cancellation barrier / summarisation needed. Nothing is
`OPERATOR-CONFIRMED` until the live session passes.

## M2.5A CLOSURE

**M2.5A — COMPLETE / OPERATOR-CONFIRMED (2026-09-09), `R0028`.** Recorded
there and in `CURRENT_STATE.md`:

| Evidence | Result |
|---|---|
| Bare `plug:usb_speaker` route self-echo (automated) | **UNSAFE** — 14/14 silent-playback trials tripped the VAD (latched 3.4–17.0 s) |
| Automated `AEC_REF` block (XVF3800 far-end reference fed) | **0/4** false-VAD |
| **Live AEC + operator test (M2.5A.2)** | audible + AEC ref active **3/3**; QUIET_AEC false-VAD **0**; operator voice detected **3/3**; AEC ref active at **every** detection; `M2.5A CLOSE CRITERIA MET = True` |
| Silero separation | AEC-quiet residual conf/vol p95 **0.756 / 0.536** (below the 0.6 `min_volume` gate) vs operator speech **0.985 / 0.761** |
| SPIKE B-live v2 media-stop | VAD start → PLAYBACK TASK STOPPED **28.5 ms mean / 29.4 ms median / 37.4 ms max** (playback-process reaped, **not** last physical speaker sample); VAD → stop-request ≈ 0 ms |

M2.5A tests are **not** repeated in M2.5B.

## AEC PRODUCTION WIRING

**`nexa.voice_tts.aec_reference.AecReferenceFeeder`** — a `FrameProcessor`
placed in the output stages **immediately after the Piper TTS service**
(so it sees every `TTSAudioRawFrame` the audible path plays) and **before**
the `TtsStatusObserver`.

- **One persistent sink**, not a process per segment: `aplay -q -t raw -f
  S16_LE -r <sr> -c <ch> -D plug:respeaker -`, fed raw PCM on stdin.
- A **bounded drop-oldest queue** (`DEFAULT_MAX_QUEUED_CHUNKS = 24`, a few
  hundred ms) + a writer task: an AEC reference tolerates a dropped chunk
  far better than back-pressuring the pipeline, so on overflow the oldest
  chunk is dropped and counted (`chunks_dropped`), never blocked. Writes
  go through `run_in_executor` so a slow pipe never stalls the loop.
- **Health → `nexa.voice.aec.AecReferenceHealth`** — the single source of
  truth. `mark_started` when the sink is confirmed alive, `mark_failed`
  on spawn failure / `BrokenPipeError` / a dead process (one respawn is
  attempted, then it stays failed), `mark_stopped` on clean shutdown.
  `barge_in_safe == active`.
- **ALSA routing decision (from R0028 M2.5A.2):** a single-writer `type
  multi` tee over the two independent USB clocks fails ALSA slave
  negotiation, so the audible path (Jieli DAC, unchanged) and the
  reference (`plug:respeaker`) are **separate sinks**. The reference is
  driven from the *same* `TTSAudioRawFrame` stream the audible path plays,
  frame-for-frame, so alignment is inherent (no second `aplay` racing a
  first). An adaptive AEC tracks bulk echo-path delay by design.
- **No `/etc/asound.conf` change.** The `_PcmSink` is injectable
  (`sink_factory`) so all frame-handling / health logic is unit-tested
  with no audio device.
- **The tee is built first / gated first:** `HalfDuplexGate.bargein_active`
  is `bargein_enabled AND aec_health.barge_in_safe`. Until the feed is
  confirmed active the mic stays in R0026 suppression; the
  `BargeInController` refuses to admit any interruption
  (`unsafe_speech_ignored` telemetry + a `logger.warning`).
- **Production self-echo regression:** the hardware-safe smoke drove the
  real reSpeaker + real `aplay -D plug:respeaker` with the operator
  silent → **0 interruption candidates, 0 confirmations** (see *DETERMINISTIC
  TESTS*). Case 7 of the live matrix repeats this with real Piper audio.

## BARGE-IN STATE MACHINE

**`nexa.voice.interruption.InterruptionStateMachine`** — pure, no I/O, time
injected (deterministically testable, mirrors `nexa.voice.state`).

```
                notify_response_dispatched()  (allocates response_id N)
   IDLE ───────────────────────────────────────────────▶ RESPONDING
     ▲                                                     │   ▲
     │ notify_response_finished()  (normal completion)     │   │ speech_stopped()
     │                                                     │   │ before the hold
     │                       speech_started(now)           ▼   │  (reject)
     │                  ┌──────────────────────▶  INTERRUPT_CANDIDATE
     │                  │                                   │
     │ notify_          │       poll(now) with              │
     │ interruption_    │   now - candidate_started ≥       │
     │ complete()       │   confirm_hold_secs (0.3 s)       ▼
   INTERRUPTING ◀───────┴──────────────────────────  (INTERRUPT_CONFIRMED,
                                                       active_response_id → None)
```

Invariants:

- **At most one candidate.** `speech_started` while `INTERRUPT_CANDIDATE`
  or `INTERRUPTING` is counted (`ignored_speech_starts`) and ignored — no
  parallel candidate, no second busy-period turn (R0026 regression guard).
- **Monotonic `response_id`.** Each `RESPONDING` entry (including the
  interrupting turn's own reply — nested-safe) allocates the next id;
  `active_response_id` goes `None` on confirmation so late tokens/frames
  stamped with the old id are droppable by `is_current_response()`.
- **300 ms confirm hold** = `VADParams.start_secs` (0.2 s, already elapsed
  inside a `VADUserStartedSpeakingFrame`) + a ~100 ms guard, with a 1 ns
  slop so a poll at exactly `candidate_started + hold` is not lost to
  float subtraction.
- `reset()` on pipeline stop/error — never a latched state.

## INTERRUPTION TRIGGER

**`nexa.voice.bargein.BargeInController`** — a `FrameProcessor` inserted by
`VoiceRuntime(bargein_controller=…)` **immediately after `VADProcessor`**
and **before** utterance capture, so an interruption is decided before any
STT / conversation work is enqueued. It owns the state machine, the
confirm-hold task, and `BargeInTelemetry`; it reads `AecReferenceHealth`.

Per frame:

1. `VADUserStartedSpeakingFrame` while `response_in_flight`:
   - if **not** `aec_health.barge_in_safe` → **ignore** (`unsafe_speech_
     ignored`, `logger.warning`); the mic gate is already back in R0026
     mode.
   - else `sm.speech_started(loop.time())` → on `CANDIDATE_STARTED` fire
     `on_candidate(response_id)` and schedule `_confirm_after_hold()` (a
     `create_task` that sleeps `confirm_hold_secs` then polls, so
     confirmation fires even with no further VAD frames).
2. `VADUserStoppedSpeakingFrame` → `sm.speech_stopped()`; on
   `CANDIDATE_REJECTED` cancel the confirm task, fire `on_candidate_rejected`.
3. Every frame also runs `sm.poll(now)` (belt for the scheduled wake-up).
   On `INTERRUPT_CONFIRMED` → `_do_confirm("sustained_vad")`:
   - `await self.broadcast_interruption()` → Pipecat `InterruptionFrame`
     upstream + downstream (cancels + recreates the output audio task =
     drops all queued + currently-playing PCM);
   - `self._on_confirmed(InterruptContext(invalidated_response_id, reason))`
     — one injected, synchronous, fast hook (the adapter's
     `interrupt_active_turn`).
   - re-entrancy guarded (`_confirming`) — one broadcast, one hook call.
4. `StartFrame` / `EndFrame` / `CancelFrame` / `ErrorFrame` → `sm.reset()`.

**Detection ≠ transcription.** `broadcast_interruption()` and the LLM
cancel happen on VAD confirmation; the interrupting utterance's audio is
still captured and, only when its `VADUserStoppedSpeakingFrame` arrives,
goes through the normal `SerialTranscriptionQueue` →
`BilingualSpeechTranscriber` → `VoiceConversationAdapter` →
`ResponseLanguageResolver` → the same `ConversationSession`.

**No `LLMResponseAggregator`.** The trigger is this one small NeXa
processor; Pipecat owns only `InterruptionFrame` propagation + audio-queue
cancellation.

**Think-window barge-in:** `response_in_flight` is true from
`notify_response_dispatched` (before the first audio frame), so an
interruption during generation is confirmed the same way; the audio-stop
actions become no-ops and the adapter takes the CASE A history path
(below).

## LLM CANCELLATION

**Per-turn `CancelToken` in `VoiceConversationAdapter`.** When barge-in is
wired (`response_id_source` set), each `_run_turn_inner`:

- allocates `self._active_cancel_token = CancelToken()` and reads
  `self._active_response_id` from the controller;
- passes the token to `session.send(…, cancel_token=…)` →
  `provider.generate(…, cancel_token=…)`;
- consumes the stream in a **child task** (`_consume`).

`interrupt_active_turn()` (called by `BargeInController.on_confirmed`):

1. sets `_interrupt_requested`;
2. `self._active_cancel_token.cancel()` — the Ollama worker **thread**
   observes `is_cancelled` on its next streamed line, `return`s and drops
   the HTTP stream (R0028: closing the async generator alone leaves the
   worker draining into a queue nobody reads);
3. cancels the consume task so a parked `await` unblocks immediately.

`_run_turn_inner` then `await gen.aclose()` — `GeneratorExit` at
`send()`'s `yield`, so `send()` **does not** append its own assistant turn
— and calls `commit_interrupted_turn(...)`. `InterruptedTurn` telemetry
records `llm_cancel_completed` (was the token actually cancelled).

**Proven deterministically** with a thread-backed fake provider that
mirrors Ollama (worker thread, per-chunk token check): cancelling the
*consumer* task does **not** stop the worker — only the token does; the
worker thread then exits (`join_worker()` — no leak); `send()` records no
turn. The **real** Ollama ~1 s stop + resident model is R0028's live
measurement / `test_live_ollama_integration.py`.

## RESPONSE-ID / RACE SAFETY

- **Allocator:** `InterruptionStateMachine._response_id` (monotonic int),
  surfaced as `BargeInController.active_response_id` /
  `adapter.active_response_id`.
- On confirmation the state machine sets `active_response_id → None`, so
  `is_current_response(old_id)` is `False` for every consumer.
- **`SpokenTextTracker`** (`nexa.voice_tts.spoken_text`) is keyed by
  `response_id`: a synthesized sentence (`TTSTextFrame` via
  `TtsStatusObserver.on_tts_text`) from a reply that is no longer the
  active one is **dropped**, never mis-attributed to the next turn.
- **`AssistantSpeechBridge`** on `InterruptionFrame` drains every queued
  `LLMTextFrame` / trailing `LLMFullResponseEndFrame` from the killed
  reply (`interrupted_frames_dropped`) — a straggler token that escaped
  Ollama after `cancel()` cannot reach the planner, Piper, or history.
- The adapter's consume task is cancelled, so the adapter emits no more
  `on_assistant_token` for the killed reply.

## ASSISTANT SPEECH BRIDGE

`AssistantSpeechBridge(on_interruption=…)` (new, optional):

- on `InterruptionFrame`: `_drain_queue()` (discard every queued LLM frame
  except the `_SHUTDOWN` sentinel; count `interrupted_frames_dropped`),
  reset `_explicit_voice_this_turn`, call `on_interruption` (wired to
  `BargeInController.notify_response_finished` — closes the interruption
  state machine + lets `HalfDuplexGate` reopen), then **forward** the
  `InterruptionFrame` so the planner / continuity / TTS also clear.
- The bridge never itself produces an `InterruptionFrame` or cancels
  anything — that is the controller's job (guard test updated for this).

## PLANNER / CONTINUITY / PIPER / AUDIO CANCELLATION

- **`NexaSpeechPlanner`** already handled `InterruptionFrame` (clears
  `_raw`/`_emitted`, forwards). Verified by test 14: a partial buffer from
  the killed reply never leaks into the next response's spoken output.
- **`NexaSpeechContinuityController`** — **changed**: on `InterruptionFrame`
  it now `_discard_held()` (drop the held phrase **without** speaking it)
  instead of `_release_held()`. Under R0026 releasing was harmless
  (nothing else was cancelled); under barge-in it would leak a stale
  phrase into TTS after the interrupt. Normal pacing policy is untouched.
  Existing continuity test updated (`test_20_interruption_discards_held_phrase`).
- **Piper / output audio** — `broadcast_interruption()` →
  `FrameProcessor._start_interruption()` on every downstream processor →
  `base_output.handle_interruptions()` cancels + recreates the audio task
  (drops queued + playing PCM), and `PiperHttpTTSService` abandons its
  in-flight aiohttp request. This is Pipecat 1.8.1's tested path (R0028
  capability audit). **The fake-slow-Piper deep check (matrix case 17) and
  "old audio never resumes" (case 16) are in the live probe** — they need
  real Piper; the *propagation* (bridge drain + planner/continuity clear +
  `broadcast_interruption` called exactly once) is covered deterministically.

## INTERRUPTED HISTORY SEMANTICS

**`ConversationTurn.interrupted: bool = False`** (additive, defaulted — 11
construction sites + every typed-chat path unchanged).

**`ConversationSession.commit_interrupted_turn(spoken_text) →
InterruptedTurnOutcome`** — the adapter calls it once, *after* it has
stopped consuming the stream early (so `send()` never appended):

| Case | Condition | Action | Outcome |
|---|---|---|---|
| **A** | `spoken_text` blank (think-window / nothing synthesized yet) | pop the trailing USER turn **and** its `_response_languages` slot | `ROLLED_BACK_USER_TURN` |
| **B** | `spoken_text` non-blank | append `ConversationTurn(ASSISTANT, spoken_text, interrupted=True)` + `None` lang slot | `COMMITTED_SPOKEN_PREFIX` |
| — | history empty, or last turn already ASSISTANT (stream finished as the cut landed) | nothing | `NOTHING_TO_COMMIT` (adapter then fires `on_assistant_complete`) |

`_history` and `_response_languages` stay index-aligned in every branch
(test 21). The **unspoken remainder is never stored.** No voice-only
history — the same canonical `ConversationSession.history`.

**Spoken-text high-water mark:** `SpokenTextTracker` accumulates
`TTSTextFrame` sentence text for the active `response_id`
(`TtsStatusObserver.on_tts_text`). This is **synthesized-sentence
precision** — downstream of planner + continuity, i.e. "text released
toward the speaker". **Documented approximation** (R0028 RISK 3): a
sentence synthesized just before the cut may not have fully *played*, so
the committed prefix can be a sentence generous — never short, never
invented. The adapter falls back to the raw delivered tokens only if no
tracker is wired.

## WIRE REPRESENTATION

Stored `content` stays **clean transcript text**. `ConversationContext.
to_provider_messages()` appends a **constant** `INTERRUPTED_WIRE_SUFFIX`
(`" […]"`) to an interrupted ASSISTANT turn's wire content *only* — so the
model sees its previous answer was cut off. It is **deterministic**: the
same history rebuilds to a byte-identical message list every call, so
Ollama / llama.cpp prompt-prefix KV-cache reuse is preserved exactly as
for the response-language directive (this is the discipline `context.py`'s
header documents at length). Typed chat never sets `interrupted`, so its
wire output is byte-for-byte unchanged (test 27).

## BILINGUAL BEHAVIOUR

`ResponseLanguageResolver` runs **before** the stream, so an interruption
mid-stream does not touch it. The interrupting utterance is a fresh turn:
its own STT language decision + `resolver.resolve()` apply.

- Sticky preference survives an interrupted turn (nothing rolls it back).
- A one-turn override applied to the interrupted turn does **not** leak to
  the interrupting turn (the resolver is per-turn; the override was never
  sticky). Test 22/23 drives: sticky→EN, then an interrupted one-turn
  PL override, then a plain turn → still EN.
- PL assistant interrupted in EN → new `InputSpeechLanguage=en` →
  `ResponseLanguage=en` (unless sticky) → Jenny voice; EN→PL symmetric →
  Gosia. (Audible PL/EN voice-switch across an interrupt = live matrix
  cases 24/25.)

## QUEUE SAFETY

`SerialTranscriptionQueue` (≤ 1 concurrent) and `SerialConversationQueue`
(≤ 1 in flight) are retained. The barge-in path adds the single-candidate
`InterruptionState` gate *in front of* promotion:

- while `RESPONDING`, a second busy-period utterance is still
  `DROP_BUSY`-ed (unchanged);
- exactly **one** interrupting utterance is admitted — `HalfDuplexGate.
  admit_next_utterance()` (a one-shot, set by the confirmed
  `InterruptionFrame`) lets that utterance past the DROP_BUSY gate;
  `should_drop_busy_utterance()` consumes it;
- during `INTERRUPT_CANDIDATE` / `INTERRUPTING` further VAD starts are
  ignored at the controller — no hidden queue of future interruptions.

Test 8/9: mid-reply, two extra utterances are `DROP_BUSY`-ed
(`conversation_queue_depth` stays 0), then an interrupt → after teardown
both queues are 0 and `max_observed_conversation_concurrency ≤ 1`.

## AEC FAILURE SAFE MODE

- `AecReferenceHealth.barge_in_safe` gates everything: `HalfDuplexGate.
  mic_suppressed` returns to R0026 whole-response suppression the instant
  the feed is not active; `BargeInController` refuses to open a candidate
  (`unsafe_speech_ignored`).
- `AecReferenceFeeder` reports `mark_failed` on spawn failure / broken
  pipe / dead process; it attempts **one** respawn, then stays failed.
- Telemetry: `aec_reference_active`, `aec_reference_failure_count` (on the
  controller telemetry and printed by the probe on transitions —
  `✓ AEC REF ACTIVE` / `✗ AEC REF DOWN — barge-in in R0026 safe mode`).
- **No second conversation path** — the same adapter / session / queues;
  only the mic-open decision changes.

## HALF-DUPLEX FALLBACK

- `LocalAudioConfig.bargein_enabled: bool = False` — the default. The
  probe's `--no-bargein` (an explicit alias of the default) forces it off.
- With it off: `VoiceRuntime` inserts **no** `BargeInController`, the probe
  builds **no** `AecReferenceFeeder`, `HalfDuplexGate()` is constructed
  with defaults → `mic_suppressed` / `should_drop_busy_utterance` are the
  R0026 logic verbatim. Existing suite (which never sets the flag) is
  green with no test changes beyond the two guard tests whose *premise*
  (no barge-in machinery anywhere) M2.5B legitimately changes.
- The fallback is **not** entered silently on a runtime error — an AEC
  failure drops to the R0026 *mic policy* but stays within the barge-in
  build and is reported; only an explicit `--no-bargein` removes the
  machinery.

## TELEMETRY

- **`BargeInTelemetry`** (`BargeInController.telemetry.snapshot()`):
  `response_started`, `response_finished`, `candidate_started`,
  `candidate_rejected`, `interrupt_confirmed`, `ignored_speech_starts`,
  `unsafe_speech_ignored`, `active_response_id`, `state`,
  `aec_reference_active`, `aec_reference_failure_count`,
  `last_interrupt_reason`.
- **`InterruptedTurn`** (`adapter.on_turn_interrupted`): `reason`, `at` /
  `at_wall`, `invalidated_response_id`, `outcome`, `spoken_chars`,
  `llm_cancel_completed`, `interrupted_count_this_session`.
- **`AssistantSpeechBridge.interrupted_frames_dropped`**,
  **`AecReferenceFeeder`** `frames_mirrored` / `bytes_mirrored` /
  `chunks_dropped` / `respawns`, **`adapter.interrupted_turns`**.
- No user audio is persisted beyond the current policy.

## DETERMINISTIC TESTS

`tests/test_bargein_m2_5b.py` — **38**, offline, no hardware/model/network.
Mapped to the acceptance matrix:

| # | Covered by |
|---|---|
| 1 | `test_case1_bargein_active_keeps_mic_hot_during_a_reply` |
| 2 | `test_case2_aec_reference_down_falls_back_to_r0026_suppression`, `test_case2_aec_reference_down_ignores_the_interruption` |
| 3 | `test_case3_short_speech_then_stop_rejects_candidate`, `test_case3_short_vad_then_stop_rejects_no_broadcast` |
| 4 | `test_case4_sustained_speech_confirms_exactly_once`, `test_case4_sustained_vad_confirms_and_broadcasts_once` |
| 5, 6 | `test_case5_…`, `test_case6_…`, `test_case5_6_single_candidate_invariant` |
| 7 | `test_case7_admit_one_utterance_…`, `test_case7_nexa_speaks_operator_silent_zero_candidates` + the hardware-safe smoke |
| 8, 9 | `test_case8_9_exactly_one_interrupt_utterance_queues_are_clean` |
| 10 | `test_case10_double_interrupt_call_is_idempotent` |
| 11, 12, 30 | `test_case11_12_interrupt_cancels_stream_and_skips_send_append` (+`join_worker`), state-machine `is_current_response` |
| 13 | `test_case13_interruption_frame_drains_queued_llm_frames` |
| 14 | `test_case14_planner_clears_partial_buffer_on_interruption` |
| 15 | `test_20_interruption_discards_held_phrase` (continuity suite) |
| 16, 17 | propagation covered here; **deep checks in the live probe** (real Piper) |
| 18 | `test_case18_think_window_rollback_…`, `test_case18_adapter_think_window_interrupt_rolls_back_user_turn` |
| 19 | `test_case19_spoken_prefix_commits_interrupted_assistant_turn`, `SpokenTextTracker` tests |
| 20 | `test_case20_no_double_commit_when_reply_already_finished` |
| 21 | `test_case21_wire_suffix_only_on_interrupted_assistant_turn`, index-alignment asserts |
| 22, 23 | `test_case22_23_one_turn_override_does_not_leak_sticky_survives` |
| 24, 25 | resolver path here; **audible voice-switch in the live probe** |
| 26 | `test_case26_bargein_off_path_is_the_pre_m2_5b_path` |
| 27 | `test_case27_typed_chat_send_is_unchanged_by_the_interrupted_field` |
| 28 | `test_case28_default_gate_is_byte_for_byte_r0026` + whole existing suite unchanged |
| 29 | `test_case29_nested_interruption_gets_a_fresh_monotonic_id` |

Full suite: **`pytest` 684 passed / 7 skipped / 14 subtests** ·
**`python -m unittest discover -s tests` 691 OK / 7 skipped** ·
**`ruff check src tests apps` clean** · **`git diff --check` clean**.

**M2.5B.1 regression tests — `tests/test_bargein_m2_5b1.py` (13):**

| # | Test | Locks |
|---|---|---|
| 1 | `test_1_and_5_split_A_B_becomes_ONE_canonical_turn` | the live bug: 2 VAD segments → 1 turn, segment B not `DROP_BUSY`'d |
| 2 | `test_5_exactly_one_user_turn_even_with_three_segments` | 3 segments → 1 turn |
| 3 | `test_3_no_DROP_BUSY_for_a_segment_of_the_confirmed_interruption` | no segment of the interruption is dropped |
| 4 | `test_4_unrelated_later_busy_speech_is_STILL_dropped` | a genuinely new mid-reply utterance is still `DROP_BUSY` |
| 5 | `test_noise_only_interruption_produces_no_replacement_turn` | cough/noise interruption → no turn, clean state |
| 6 | `test_6_queues_and_in_flight_return_to_zero_after_the_interruption` | queue depth **and** in-flight → 0, no latched capture phase |
| 7 | `test_7_cancel_requested_and_worker_stop_are_tracked_separately` | `llm_cancel_requested` (commit-time) vs `CancelCompletion.cancel_to_worker_stop_ms` (measured, later) never conflated |
| 8 | `test_8_no_stale_provider_worker_after_repeated_cancels` | 8 cancels → workers started == stopped; every completion saw `cancel_observed` + `worker_stopped` |
| 9 | `test_9_response_id_is_strictly_monotonic_across_interruptions` | 6 interruptions → strictly increasing, unique `response_id`; `last_invalidated_response_id` == the active id |
| 10 | `test_10_pl_reply_interrupted_in_english_coalesces_as_english` | PL reply, EN interruption segments → coalesced turn dispatched EN (Jenny) |
| 11 | `test_11_no_bargein_has_no_capture_phase_and_plain_drop_busy` | `enabled=False`: no controller, no capture phase, plain R0026 `DROP_BUSY` |
| 12 | `test_capture_timeout_never_hangs` | missing settle signal → hard timeout finalises, never hangs |
| 13 | `test_12_fifteen_interruptions_leave_no_accumulation` | 15 cycles: queues 0, ≤ 1 concurrency, ≤ 3 net asyncio tasks, no latency growth, 15 coalesced / 15 interrupted |

**Hardware-safe integration smoke** (agent, operator absent): built
`VoiceRuntime` + `BargeInController` + `AecReferenceFeeder` on the real
reSpeaker; real `aplay -D plug:respeaker` started (`XVF3800 AEC reference
feed active`, `barge_in_safe=True`); `gate.mic_suppressed=False` during a
simulated reply; **operator silent → 0 candidates, 0 confirmations, 0
unsafe-ignored**; clean teardown (`AEC REF` → down, 0 failures). **PASS.**

## LIVE PROBE

`apps/nexa_bilingual_voice_probe.py --bargein` (default `--no-bargein`).
Surfaces live: `✓ AEC REF ACTIVE` / `✗ AEC REF DOWN`, `⟂ INTERRUPT
CANDIDATE (response_id=…)`, `candidate rejected`, `✂ INTERRUPT CONFIRMED —
cancelling response_id=…`, `✂ interrupted turn committed — outcome=…,
spoken_chars=…, llm_cancel_completed=…`, plus the existing per-turn
language block, STT/queue stats and DROP_BUSY lines.

## WIRING AUDIT (pre-live, 2026-09-09)

A focused audit of the two wires most likely to be silently missing:

**1. Is `AecReferenceFeeder` instantiated when `--bargein`? — YES.** It was
already correct in the file at `97f4a84` (the earlier pasted diff excerpt
was incomplete — it showed the `aec_feeder: … = None` declaration and the
`if aec_feeder is not None: out_stages.append(...)` guard but not the
`if bargein_on: aec_feeder = AecReferenceFeeder(...)` two lines above, nor
`on_tts_text=_on_tts_text`). Verified: one `AecReferenceHealth` was shared
by the gate, the controller and the feeder; the feeder sat after
`tts_service` in `extra_output_stages`; the `on_tts_text` callback was
passed to `TtsStatusObserver`; `TtsStatusObserver.process_frame` calls it
on `TTSTextFrame` (`bridge.py:255`); `tracker.start_response` ran before
any sentence (in `on_user_transcript`, top of `_run_turn_inner`).

**No bug — but three hardenings + single-sourcing were done anyway:**

- **`nexa.voice_tts.bargein_wiring.build_bargein_stack` (NEW).** The probe
  and the M2.5B integration tests and the hardware-safe smoke now build
  the barge-in stack through this **one** function — no chance of the probe
  and a test diverging. It cross-wires the ONE `AecReferenceHealth` into
  the gate + controller + feeder, and exposes the exact hooks the app
  plugs in (`note_response_dispatched`, `note_tts_sentence`,
  `note_interruption`, `response_id_source`, `spoken_prefix_source`,
  `output_stages`). `enabled=False` → plain `HalfDuplexGate()`, no feeder,
  no controller (R0026, byte-for-byte). The probe was refactored onto it;
  the hand-rolled construction is gone.
- **`InterruptionStateMachine.last_invalidated_response_id`.** `poll()`
  nulls `active_response_id` on confirm; the controller now reads the
  just-invalidated id from this dedicated field instead of a
  telemetry-sync-order-dependent read.
- **Adapter captures the spoken prefix on the loop at confirm time.**
  `interrupt_active_turn()` now reads `spoken_prefix_source(response_id)`
  **synchronously** (the instant the interruption confirms, before any
  late `TTSTextFrame` or the next turn's `start_response` can touch the
  tracker) into `_captured_interrupt_prefix`; `_commit_interrupted` uses
  that captured value. Removes a theoretical stale-attribution window from
  affecting committed history.

**Stale response-id protection — verified** (test H): after `poll()`
invalidates the id, `note_tts_sentence` credits the sentinel `-1` → the
tracker drops it; the killed reply's accumulated prefix is unchanged; the
next `note_response_dispatched` resets the tracker; a straggler cannot
reach the new reply.

**Hardware-safe wiring smoke (agent, operator absent, 2026-09-09) — via
`build_bargein_stack` + `VoiceRuntime`, the probe's construction path:**
`shared aec_health identity: True True True`; output stage order
`PIPER → AecReferenceFeeder → OBS`; real `aplay -D plug:respeaker`
started under `VoiceRuntime` → **`AEC REF ACTIVE`** (printed via the
stack's `aec_status` callback); `barge_in_safe=True`,
`gate.mic_suppressed=False` (hot) during a simulated reply; **6 s silent →
`candidate_started=0`, `interrupt_confirmed=0`**; clean teardown
(`AEC REF DOWN`, 0 failures). **PASS.**

## M2.5B.1 — LIVE STABILITY / LATENCY / INTERRUPT-UTTERANCE INTEGRITY

### What the live session showed (2026-09-09)

**Worked:** `✓ AEC REF ACTIVE` held; NeXa's own voice never triggered a
barge-in; interruptions triggered and cancelled replies; PL↔EN switching
+ Jenny/Gosia mapping correct.

**Failed — Problem 1, interrupt-utterance fragmentation.** The operator
meant *"Czekaj. Powiedz tylko, jak powstaje."* — the runtime produced
`canonical transcript: "Czekaj."`, NeXa replied *"Czekam. Co chciałbyś
wiedzieć dalej?"*, then `DROP_BUSY_RESPONSE_IN_FLIGHT — dropped STT result
'tylko jak powstaje.'`. Also seen: `"Sorry. I just meant..."` → dropped
`'colon.'`; `"No."` → then `dropped 3540ms of audio captured while NeXa is
answering`. VAD's `stop_secs=1.0` splits a mid-sentence pause into two
segments; the one-shot `admit_next_utterance` let only segment 1 through,
segment 1 dispatched a reply, segment 2 was `DROP_BUSY`'d. **Violates "full
interrupting utterance is captured".**

**Failed — Problem 2, latency degradation.** Most turns ~2.8–3.5 s STT
total; later, after repeated interruptions, one turn `detect 2.55s + decode
4.72s = 7.28s`, and perceived long end-to-end waits. Queue depths were
often 0 (so not a visible backlog).

### Problem 1 — root cause + fix (SHIPPED, commit `2460463`)

**Root cause:** a confirmed interruption was treated as **one utterance =
one VAD segment**. `HalfDuplexGate.admit_next_utterance()` was a *one-shot*;
`InterruptionState.INTERRUPTING` treated a later `VADUserStartedSpeakingFrame`
as `IGNORED_SPEECH`; the adapter dispatched a turn on the first STT result
while more of the same interruption was still being captured/transcribed.

**Fix — an explicit interruption-capture / settle phase** after
`INTERRUPT_CONFIRMED` (default OFF; `--no-bargein` unchanged):

- `InterruptionStateMachine`: `INTERRUPTING` now means *"capturing the
  interruption utterance"*. `speech_started` / `speech_stopped` in that
  state return `INTERRUPT_SEGMENT_STARTED` / `_ENDED` (not `IGNORED_SPEECH`);
  it tracks `capture_open_segments` / `capture_segments_ended`.
  `notify_response_finished` is a **no-op from `INTERRUPTING`** — only
  `notify_interruption_complete` exits it.
- `BargeInController`: on each segment end, arm a **settle window**
  (`DEFAULT_INTERRUPT_SETTLE_SECS = 1.2`, restarted on every new segment;
  hard cap `DEFAULT_MAX_CAPTURE_SECS = 12`). In the common single-segment
  case the settle overlaps the segment's STT decode entirely and adds ~0
  latency. On settle → `on_interrupt_capture_settled`.
- `HalfDuplexGate`: the one-shot is replaced by a `capturing_interrupt`
  **phase** (set on `InterruptionFrame`, cleared on the next
  `notify_response_dispatched`). `should_drop_busy_utterance()` **never**
  drops while capturing → every segment of the one interruption passes.
- `VoiceConversationAdapter`: `handle_transcription` **first** checks
  `_capturing_interrupt` (before the turn-in-flight / DROP_BUSY checks) and
  accumulates each segment result. When the capture has *settled* **and**
  every owed segment result has arrived, **ONE** `CoalescedInterruptTurn`
  (`" ".join` of the segments, language from the last segment) is submitted
  — a single canonical turn. Hard timeout guard. A noise-only interruption
  yields no replacement turn. `_run_turn_inner` handles
  `CoalescedInterruptTurn` exactly like a `TranscriptionResult`.

**Invariant now enforced & tested:** *one confirmed interruption → exactly
one canonical user turn*, no matter how many VAD segments; extras are
coalesced, never `DROP_BUSY`'d, never separate turns. An *unrelated* later
busy utterance is still dropped.

### Problem 2 — Fix 1: responsive LLM cancellation (SHIPPED + validated)

**Root cause of the slow `cancel() → worker stopped`.** The `ollama` /
`llama_server` provider worker thread streamed the response with a plain
blocking `resp.readline()`. Between two streamed lines (Ollama mid-token,
~0.3–2 s on the Pi) the thread was parked inside `readline()` and could
not see `cancel_token.is_cancelled`. R0029's first spike measured
`cancel() → worker stopped` at **~2.0 s** (`cancel_to_worker_stop_ms =
[2057, 2098, 2105]`), and a whisper.cpp decode started in that ~2 s window
inflated **+34 %** (`decode_inflation_ratio 1.34`) from the old worker
still burning a core.

**Fix.** The worker now waits for socket readability with
`select.select([resp.fileno()], [], [], 0.25)` before each `readline()`,
so the `is_cancelled` check runs ~4×/s while the socket is idle. The
socket stays in **blocking** mode — an earlier attempt with
`socket.settimeout(0.25)` corrupted `http.client`'s chunked-transfer
reader (`OSError: cannot read from timed out object` against real Ollama).
On cancel the worker calls `mark_cancel_observed()` and returns; the
`finally` calls `mark_worker_stopped()`.

**Validated against the real Ollama server (`gemma4:e4b`, Pi):** streaming
still works (24 chunks, `last_metrics` populated); `cancel() → worker
stopped` = **251 ms** consistently in the re-measurement (three interrupt
turns: 251.3 / 251.0 / 251.9 ms — one `_CANCEL_POLL_SECS` tick, the
designed worst case), **down from ~2000 ms**. This alone removes the
old-generation / new-work CPU overlap: by the time the coalesced
interruption turn calls `session.send()` (after the ≥ 1.2 s settle window
+ the last segment's STT decode), a 251 ms-old cancellation has been
complete for seconds. No explicit pre-`send()` "cancellation barrier" /
sleep is added — Fix 1 makes it redundant (the re-measured decode-overlap
inflation dropped x1.34 → x1.18, and even that residual never coincides
with the interrupting utterance's decode in production — see below).

### Problem 2 — instrumentation + measurement

Added (measurement only, no control-flow dependency):

- **`CancelToken`** now carries completion signals: `mark_cancel_observed()`
  / `mark_worker_stopped()` (set by the provider worker thread) +
  `cancel_observed` / `worker_stopped` / `wait_worker_stopped(timeout)`.
  The `ollama` / `llama_server` workers set them; `is_cancelled` alone no
  longer conflates *requested* with *stopped* — `InterruptedTurn` now has
  `llm_cancel_requested` **and** `provider_worker_stopped_at_commit`, and a
  separate `on_cancel_completed(CancelCompletion)` fires with the measured
  `cancel_to_worker_stop_ms`.
- **`LocalModelProvider.last_metrics`** — the Ollama server's own
  `prompt_eval_count/duration`, `eval_count/duration`, `load_duration`,
  `total_duration` from the `done` chunk.
- **`SerialTranscriptionQueue` / `SerialConversationQueue`** expose
  `in_flight` (queue_size hid in-flight work).
- **`nexa.voice_conversation.LatencyLedger`** — per-turn record (STT
  detect/decode/total, model TTFT + prompt_eval/eval/load, EOT→first-audio,
  queue depth+in-flight, AEC, cancel completion, history growth) with an
  early-vs-late summary + optional JSONL.
- **`docs/research/m2_5_bargein/measure_interrupt_latency_pi.py`** — a
  real-Pi spike (real `gemma4:e4b`, real whisper.cpp, no operator, no
  audio): 18 turns with interruption-history mutations at turns 5/10/14
  (KV-cache-collapse check) + `cancel() → worker stopped` timing + a
  whisper-decode-while-old-Ollama-worker-runs measurement vs a no-Ollama
  baseline (the 7.28 s STT hypothesis).

### Problem 2 — measurement results (real Pi, `gemma4:e4b`, whisper.cpp)

Spike `measure_interrupt_latency_pi.py`, 18-turn Polish session, per-turn
`CancelToken.cancel()` at turns 5 (spoken-prefix) / 10 (rollback) / 14
(spoken-prefix), then a `cancel → worker-stop` / decode-overlap block.
Raw: `docs/research/m2_5_bargein/measure_interrupt_latency_pi_20260909_203953.json`
(the first run, `…_191448.json`, is superseded — its interrupt path did
**not** call `.cancel()`, so an abandoned generation ran to completion
server-side and inflated the two post-interrupt turns to ~28 s; that was a
spike bug, not a production effect).

**A. Was the live failure a queue backlog? — NO.** `conv_queue_depth` /
`stt_queue_depth` were 0 throughout; `in_flight` ≤ 1. The degradation is
entirely **model time to first token**, not work waiting in a queue.

**B. `cancel() → provider worker stopped` — bounded at one poll tick.**
Part A: **251.3 / 251.0 / 251.9 ms** (all three `cancel_observed=True`,
`worker_stopped=True`). Was ~2.0 s pre-Fix-1. (Part B/C's
`cancel_to_worker_stop_ms` of ~1.84 s is a *spike artefact* — that block
measures the stop only *after* an intervening `await whisper_decode()`, so
it reports `max(actual_stop, decode_time)`; Part A's inline number is the
real one.)

**C. Whisper decode overlapping a just-cancelled Ollama worker —
x1.18** (baseline median 1557.9 ms → overlap median 1839.1 ms; was x1.34
pre-Fix-1). ~280 ms, from the ≤ 251 ms tail of the old worker plus
llama.cpp finishing its current batch. **In production this overlap never
occurs on the critical path**: the interrupting utterance's decode starts
only after its VAD segments end, ≥ 1.2 s (settle) after the cancel — the
worker (≤ 251 ms) is long gone. So the live 7.28 s STT was **not** a
still-running cancelled worker (production always cancelled).

**D. Why the late-session latency collapse — THE root cause.** Per-turn
model TTFT / `prompt_eval_duration`:

| turns | history | prompt_eval_count | prompt_eval | TTFT | regime |
|---|---|---|---|---|---|
| 1–4 | 2–8 | 535 → 798 | 3.1–3.5 s | 3.4–3.8 s | warm (KV-prefix reuse) |
| 6–9 (after interrupts 5) | 12–18 | 945 → 1241 | 3.4–4.0 s | 3.7–4.3 s | **still warm** |
| 11 (after interrupt 10) | 20 | 1331 | 3.1 s | 3.7 s | **still warm** |
| **12** | **22** | **1382** | **77.9 s** | **78.3 s** | **collapsed** |
| 13–18 | 24–34 | 1321–1387 | 71–79 s | 71–79 s | collapsed (permanent) |

- `load_duration` is 1–4 ms on **every** turn → the model never reloads;
  `keep_alive=30m` is fine. Not a reload.
- The collapse is **exactly** at the point `len(history)` first exceeds the
  old `DEFAULT_MAX_TURNS = 20` (turn 12, history 22). `prompt_eval_count`
  then **plateaus** at ~1350–1387 while history climbs 22 → 34 — i.e. the
  context window has started **evicting its oldest turn every turn**.
- Dropping the oldest turn changes the token sequence right after the
  system prompt, so Ollama/llama.cpp's cached prompt **prefix diverges at
  position ~0** and the whole ~1.4 k-token prompt is cold-reprocessed at
  ~17 tok/s (≈ 78 s). Because the eviction advances again next turn, it
  **never recovers**. This is precisely the KV-cache-prefix failure mode
  `context.py`'s module header already documents for the R0009 language
  directive — the same rule, applied to eviction.
- **Interrupted-history mutations are NOT the cause.** Turns 6 and 11
  (immediately after a spoken-prefix commit and a CASE-A rollback) are
  ~3.7–4.3 s — fully warm. CASE A / CASE B keep the prefix byte-stable, as
  designed. `ttft_ms_turn_after_interrupt = [4285, 3741, 73838]` — the
  third is 74 s only because the whole session is already collapsed by
  turn 15, not because of interrupt 14.

**Fix (Problem 2): `DEFAULT_MAX_TURNS` 20 → 40, `DEFAULT_MAX_CHARS`
12 000 → 20 000** (`nexa.conversation.context`). ~40 turns / ~5 k tokens
sits well inside Ollama's 8 k `num_ctx`, so a normal voice session —
including an interruption-heavy M2.5B one — **never reaches the first
eviction**, and every turn stays in the ~3–4 s TTFT regime the early
turns show. Model, `num_thread=2`, `keep_alive=30m`, `num_ctx`, whisper,
Piper: **unchanged**. This is not summarisation/compaction — a genuine
40 +-turn marathon still eventually slides the window; a prefix-stable
compaction for that is long-term-memory work (M5, AGENTS.md §3.8),
explicitly out of M2.5B.1 scope.

Regression tests: `test_context.py::test_default_window_holds_a_long_voice_
session_without_eviction`, `::test_prompt_prefix_is_byte_stable_as_the_
session_grows`, `::test_eviction_above_the_cap_still_keeps_the_newest_turns`.

**E. 44-turn re-measurement (`…_210436.json`) — the fix, and its
boundary.** Same spike, `N_TURNS=44`, interrupts at 5/10/14/22/30/38, run
*with* the resized window:

| turns | history | prompt_eval_count | TTFT | regime |
|---|---|---|---|---|
| 1–21 | 2 → 40 | 535 → 2155 (**grows, no plateau**) | **3.4–4.6 s** | warm — every turn, the whole span the old build collapsed at turn 12 |
| 6, 11, 12, 13 (after interrupts) | 12–24 | — | 3.8–4.5 s | warm — interrupted-history mutations still don't break it |
| 24–44 | 44 → 82 | plateaus ~2250–2340 | **154–164 s** | collapsed — history now exceeds the **new** 40-turn cap; identical mechanism, moved 20 turns out |
| 23, 39 (turn *after* a rollback) | 42 / 72 | 2223 / 2311 | 4.0 / 4.6 s | one warm turn: CASE-A rollback leaves the cache matching the evicted prefix, so the next turn only appends a user message |

So the `max_turns` 20→40 change is a **partial mitigation only**: it
**moves** the eviction cliff (turn 20 → turn 40), it does not remove it. A
40 +-turn session collapses the same way — and a persistent personal
companion routinely exceeds 40 turns. **M2.5B.2 (below) removes the cliff.**
`cancel → worker-stop` held at **250–256 ms** across all six interrupts in
this run.

**Model decode rate** (`eval` ~3.4 tok/s on the Pi at `num_thread=2`) is a
pre-existing, accepted R0022/R0023 trade-off and is *not* touched here —
it affects how fast a long reply finishes streaming, not the "tens of
seconds to first audio" the operator reported (that was D).

## M2.5B.2 — LONG-SESSION CONTEXT / KV-CACHE STABILITY

**Goal:** eliminate the sliding-window KV-cache cliff for good — not move
it again. The canonical `ConversationSession.history` stays **complete**;
only the *provider-facing* context is bounded.

### Research (real Pi, `gemma4:e4b`, num_thread=2) — `research_provider_context_kv.py`

The spike's per-call `prompt_eval_*` capture lagged one row (metrics read
before the worker's `done` chunk); **TTFT was measured directly** and the
cold-prefill rate is dead-consistent across every full-window call, so the
findings are firm:

| Q | question | result |
|---|---|---|
| **Q1** | pure-append baseline | contaminated by the spike's fixed-suffix construction; the *clean* number is from the M2.5B.1 runs + this stage's `research_prewarm_resume.py` step 0: **~3.7 s** once the prefix cache is established |
| **Q2** | slide window base by 1 | TTFT **303 / 293 / 283 s** — dropping the oldest turn cold-reprocesses the whole ~3.3 k-token window (the M2.5B.1 cliff, re-confirmed) |
| **Q3** | synchronous rollover (jump base to a 24-turn window) | TTFT **274 s** cold — a sync rollover of a *large* window is catastrophic |
| **Q4** | pre-warm (`num_predict=1`) then a real turn on the same window | pre-warm 268 s; **real turn 6.8 s** — a pre-warmed prefix → cheap cutover ✅ |
| **Q5** | pre-warm B, one real turn on old window A, back to B | back-to-B **7.2 s** — a completed pre-warm **survives** an intervening turn on a different window (Ollama 0.33.2 / llama.cpp keeps multiple prefixes while they fit in `num_ctx`) ✅ |
| **Q6** | cold prefill `num_thread` 2 vs 4 | ~1.4× at best (274→199 s) **and a ~31 s model reload appeared at the 2→4 switch** — per-request thread changes are unsafe, **do not** |
| **Q7** | small compacted context (system + ~250-tok recap + 6 turns ≈ 1.3 k tok) cold | **~110 s** — even a *small* compacted context has a ~110 s synchronous cold prefill; compaction alone does not make a sync rollover affordable |

Follow-up spike `research_prewarm_resume.py` step 1 established the
decisive constraint: **an Ollama request cannot be interrupted during
prefill** — `cancel()` only lands between *streamed* lines, and there are
none during prefill. A pre-warm of a ~30-turn window occupied the model
for **336 s** before the cancel took effect. So a large speculative
background pre-warm is *unsafe* during an active conversation — a real
turn arriving mid-prefill queues behind it for the full prefill.

**Cold prefill rate on this Pi at num_thread=2: ~11.5 tok/s**, everywhere.

### The first M2.5B.2 implementation, and why it was not enough

The first `ProviderWindow` kept a small verbatim window across the
boundary (`keep_entries` 4–6) and did a *synchronous* cutover. It removed
the permanent collapse — a real-Pi 110-turn run
(`bench_long_session_provider_window_20260910_152509.json`) held **93
warm turns at mean 3.72 s / max 7.04 s with no drift**, 12 boundary
crossings, canonical history 212 entries intact, no reload — **but every
crossing was a bounded ~19–25 s turn** (projected ~28–50 s for full voice
replies at `keep=4`). For a persistent companion that runs for hundreds
of exchanges, a sudden ~20–32 s wait every ~44 exchanges is **not
acceptable**, rare and logged or not. So the boundary transition itself
had to be brought to ≤ ~10 s.

### Candidate evaluation (real Pi)

**A — second Ollama KV slot (`OLLAMA_NUM_PARALLEL=2`): REJECTED.** Loaded
`llama-server -c 16384 -np 2` (each slot keeps a full 8 k context — not
halved). Memory is *fine*: gemma4 uses sliding-window attention so the KV
cache is tiny (188 MiB → 376 MiB for two slots; model weights shared),
runner RSS ~9.5 GB, +~400 MiB, thermal 54–65 °C, no throttle. **But the
serving behaviour is wrong:** a foreground request issued while the other
slot is busy hit `slot … forcing full prompt re-processing due to lack of
cache data (likely due to SWA or hybrid/recurrent memory)` — it **lost
its cached persona prefix and cold-reprocessed the whole prompt** (~49 s
for a 529-token prompt), the exact opposite of the goal. Plus the two
slots contend for the `-t 2` threads (big prefill 11.5 → 9.8 tok/s) and
swap rose ~770 MB with the editor also resident. Override removed,
`ollama` restarted to `-np 1`, swap back to baseline.

**B — smaller `keep_entries` (real-Pi,
`bench_provider_window_reset_*.json`):**

| `keep_entries` | reset-turn TTFT (benchmark, short replies) | projected, full voice replies | continuity kept |
|---|---|---|---|
| 4 | **18.8 s** | ~45–50 s | 2 exchanges |
| 2 | **10.0 / 11.2 s** | ~24–28 s | 1 exchange |
| **0** | **3.4 s** | **~3.5–5 s** (independent of reply length) | none |

`keep=2`/`4` still fail for real voice because a retained *assistant*
entry is ~100–130 tokens. **`keep_entries = 0`** retains no entries, so
the reset turn cold-prefills only the new user turn (~40 tok) on top of
the still-cached persona prefix — **an ordinary turn**, and its cost does
not grow with conversation content.

**C — Ollama persistent cache / session API:** `/api/chat` exposes no
per-conversation KV handle; `/api/generate`'s deprecated `context` array
is opaque tokens with no room for NeXa's per-turn language directive or
the interrupted-wire suffix, and no rollback for a CASE-A interrupt.
llama.cpp keeps up to 32 context *checkpoints* (~20 MiB each) — but they
only accelerate a prefix that was *already seen*, i.e. a pre-warm. No
usable option without bypassing Ollama.

**D — idle pre-warm with an abort guarantee:** rejected for *auto-firing*
on one slot. A `keep>0` pre-warm blocks ~24–50 s uninterruptibly; there
is no mechanism on a single slot to stop a new user turn being trapped
behind it. The seam (`prewarm_provider_context` / `mark_prewarmed` /
`cutover_background`) is retained for a future resource-safe non-blocking
pre-warm, but is **not wired** in M2.5B.2.

### Architecture chosen — stable window + context reset (`nexa.conversation.ProviderWindow`)

* **`ConversationSession.provider_window`** (opt-in; `None` = the
  pre-M2.5B.2 `ConversationContext` path, byte-for-byte — typed chat and
  every existing test unaffected). The voice probe sets it for both
  `--bargein` and `--no-bargein` (a latency fix, no conversation-semantics
  change — the R0026 mic/half-duplex policy is untouched).
* The model is shown **`history[base:]`** — a verbatim recent window.
  `base` is **stable between resets**, so every ordinary turn is a pure
  byte-identical prefix-extension → **~4 s, for as long as the session
  runs**.
* When the window reaches `hard_entries` the next `send` performs a
  **context reset**: with the default **`keep_entries = 0`**, `base` jumps
  to the current turn, so the prompt is just `[persona][voice-dir] + [the
  new user turn]`. The persona prefix is still cached → the reset turn
  cold-prefills only ~40–60 tokens → **~3.5–5 s, an ordinary turn**,
  logged at WARNING. The model briefly has no recent conversational
  context; the window regrows by append over the next 2–3 turns.
* `keep_entries > 0` (even) retains that many recent entries across the
  reset for more continuity, at the reset-turn cost in table **B**; only
  raise it if a ≥ 15 s reset turn is acceptable for the deployment.
* `soft = 72 / hard = 88` keep the steady-state window ~44 exchanges
  (~5–6 k tokens, clear of `num_ctx` head-room), so a reset happens only
  ~once per ~44 exchanges.

`base` always lands on a turn boundary, so a reset never orphans an
assistant reply; `_response_languages` stays index-aligned; `interrupted`
turns keep their `INTERRUPTED_WIRE_SUFFIX`; canonical `history` is never
touched.

### Canonical history vs provider context

| | canonical `ConversationSession.history` | provider-facing context |
|---|---|---|
| authority | **the one transcript** (ADR-0003 D2) | a derived, bounded *view* |
| bound | none — grows for the whole session | `history[base:]`; `base` jumps at a reset |
| interrupted turns | stored verbatim (`interrupted=True`) | rendered with `INTERRUPTED_WIRE_SUFFIX` while in-window |
| response-language slots | `_response_languages`, index-aligned, never reset | replayed per in-window USER turn |
| future memory (M5) | reads all of it | irrelevant |

Nothing is deleted. A turn that scrolls out of the provider window is
still in `history` for M5 long-term memory. The one behavioural cost of
`keep=0` is a **continuity dip**: for the reset turn and ~1–2 turns after
it, the model sees no recent context (a pure follow-up like "and the
third one?" spoken right at a reset may get a clarifying question back).
Bounded, rare (~1 in 44 exchanges), self-healing as the window regrows.

### KV-cache reuse behaviour

* **Steady state:** `[persona][voice-dir][history[base:]] + [new user]` — a
  byte-identical prefix-extension every turn → Ollama re-evaluates only
  the ~30–60 genuinely new tokens (`prompt_eval_count` grows, `…_duration`
  stays ~3 s; the Ollama log shows `cached n_tokens` climbing turn over
  turn). ~3–4 s.
* **At a `keep=0` reset:** the prefix after `[persona][voice-dir]` (≈ 500
  cached tokens in the log) is discarded; the only cold work is the new
  user turn. ~3.5–5 s.
* **`num_ctx`:** `soft`/`hard` keep the steady-state window well clear of
  Ollama's 8 k `num_ctx` (its own `--context-shift` collapses the cache),
  so the *only* prefix change in the whole session is the deliberate,
  cheap reset.

### Real-Pi long-session benchmark — `bench_provider_window_reset.py`

`gemma4:e4b`, small window (`soft=16 / hard=22`) so resets are frequent,
`num_predict=24`, mix of normal PL / normal EN / CASE-A rollback / CASE-B
spoken-prefix (t13/19/34/47/78/95). Three phases change `keep_entries`
mid-run to compare. **112 turns, 10 resets.** Result JSON
`bench_provider_window_reset_20260910_163112.json`.

| phase | `keep_entries` | turns | warm TTFT (mean / p50 / max) | reset-turn TTFT |
|---|---|---|---|---|
| A | 4 | 1–20 | 3.65 / 3.65 / 4.36 s | **18.8 s** (1 reset) |
| B | 2 | 21–38 | 3.55 / 3.56 / 4.14 s | **10.0 s, 11.2 s** (2 resets) |
| **C — shipped default** | **0** | **39–112** | **3.84 / 3.67 / 9.5 s** (63 turns) | **3.44 / 3.02 / 3.03 / 3.43 / 3.82 / 3.68 / 3.06 s** — mean **3.35 s**, max **3.82 s** (7 resets) |

**Whole run** (95 warm turns + 10 resets + 7 interrupts): warm mean
**3.76 s** / p50 3.65 s / **max 9.5 s**; **only two turns > 10 s** — t12
(18.8 s, keep=4) and t32 (11.2 s, keep=2), both from the *comparison*
phases; **turn > 15 s: only t12 (keep=4).** In the **`keep=0` phase — the
shipped config — no turn exceeded 10 s** (max 9.5 s, a warm turn; the 7
resets were 3.0–3.8 s, i.e. ordinary turns). `load_duration` ≤ 8.8 ms
throughout (no reload). Canonical history at end: **216 entries /
24 367 chars — complete** (`base` = 212, model saw the last 4 entries).
CASE-A / CASE-B interrupts and PL/EN routing all correct across every
reset. Three of the 74 `keep=0` warm turns landed at 6.4–9.5 s (an
occasional llama.cpp checkpoint / SWA re-prefill of ~60–105 tokens
instead of ~35); the rest are ~3.7 s, and none over 10 s.

### Regression tests

`tests/test_provider_window.py` (16) — policy (bounds incl. `keep=0`,
arming, background vs sync cutover, base stays on a turn boundary,
repeated resets stay bounded, `keep=0` shows only persona + current turn)
+ rendering (window slice only, pure prefix-extension at a fixed base,
**byte-identical to `ConversationContext.to_provider_messages` for the
same slice**, `INTERRUPTED_WIRE_SUFFIX`, language-alignment guard).
`tests/test_session_provider_window.py` (11) — `provider_window=None` is
the legacy path; windowed `send` renders only `history[base:]`; a pre-warm
then the next `send` cuts over cheaply; the hard limit forces a **logged**
reset; **`keep=0` reset turn's prompt is persona + the current user turn
only**; prefix is a pure extension between resets; response-language slots
stay aligned across a reset; CASE-A interrupted rollback still works with
a window; canonical history stays complete throughout.

## M2.5B.3 — INTERRUPTION-CAPTURE LIFECYCLE

### Live session (2026-09-10) — overall UX

A real live `--bargein` conversation. **Operator assessment: conversation
quality, naturalness, voice, local response speed and PL/EN switching —
GOOD / ACCEPTABLE.** Repeated interruptions succeeded, old answer stopped,
Jenny/Gosia mapping and follow-up context all correct. The accepted local
voice-UX baseline stands and is **not** revisited here.

**Remaining defect — interruption-capture lifecycle.** The terminal
repeatedly printed `nexa.voice.bargein: interruption capture hit the
12.0s cap — forcing settle`, once `nexa.voice_conversation: interruption
capture timed out after 15.0s — finalising with what we have`, and once
`DROP_BUSY_RESPONSE_IN_FLIGHT: dropped 6360ms of audio captured while NeXa
is answering (session total 1, stt_queue_depth 0)` around a long operator
utterance (*"Słuchaj, źle to robimy, bo ty nie uruchamiasz angielskiego
modelu, tylko…"*).

### Root cause (reconstructed from code + Pipecat 1.8.1 source)

The interruption-capture phase finalised only when **all three** of
`_interrupt_capture_settled`, `_interrupt_open_segments == 0` and
`_interrupt_pending_results == 0` held. `_interrupt_capture_settled` was
set **only** by the controller's settle window, and that window was armed
**only** on a VAD `INTERRUPT_SEGMENT_ENDED`
(`BargeInController._handle_speech_stopped`). `_do_confirm` explicitly
cancelled any settle task and armed only the 12 s hard cap.

`FrameProcessor.broadcast_interruption()` (Pipecat 1.8.1) calls
`self.__reset_process_task()` → **clears the calling processor's own frame
queue**, and pushes `InterruptionFrame` up/down where every other
processor's `_start_interruption()` cancels+recreates its process task and
flushes *its* queue. `_do_confirm` normally runs from the
`_confirm_after_hold` task, so while it `await`s `broadcast_interruption()`
the `BargeInController`'s process task is live and pulling frames — and on
a Pi whose event loop is saturated by the in-flight LLM decode, a
`VADUserStoppedSpeakingFrame` for the interrupting utterance's current
segment can already be **queued** and is then **discarded** by that
flush. (The `VADController._audio_idle_handler` — force-stop after 1.0 s
of no audio while `SPEAKING` — can likewise be perturbed by the flush and
mis-sequence a start/stop pair.)

**Consequence chain:**

1. the lost `VADUserStoppedSpeakingFrame` ⟹ no `INTERRUPT_SEGMENT_ENDED`
   ⟹ `_arm_settle()` **never called** ⟹ the settle window **never fires**
   ⟹ `_interrupt_capture_settled` **never set** by the normal path;
2. `_interrupt_open_segments` was seeded to 1 for the in-progress segment
   and its decrement (`note_interrupt_segment_ended`) is the same lost
   event ⟹ it **stays ≥ 1**; a VAD re-segmentation of the ongoing speech
   then adds an unmatched `note_interrupt_segment_started` ⟹ **stuck non-zero
   after STT has completed**;
3. → the **12 s controller hard cap** (`_capture_deadline`) fires (it
   calls `_notify_capture_settled`, which sets `settled` but cannot clear
   the stuck `open` count);
4. → the **15 s adapter timeout** (`_capture_timeout_guard`) force-zeros
   the counters and finalises;
5. if the operator is still mid-segment when step 4 dispatches the
   replacement turn, `HalfDuplexGate.notify_response_dispatched()` clears
   `capturing_interrupt`, so the segment's `VADUserStoppedSpeakingFrame`
   (buffered ≈ **6360 ms**) reaches `_UtteranceCaptureFrameProcessor` with
   `should_drop_busy_utterance()` now `True` ⟹ **DROP_BUSY**.

### Answers to the seven questions

| # | Answer |
|---|---|
| 1 | A normal interruption leaves the 12 s deadline alive when its **only** `VADUserStoppedSpeakingFrame` (or the one for the segment in progress at confirm) is flushed from the `BargeInController` queue by `broadcast_interruption()` on a lagged loop — the settle window was armed *only* by that event, so it never fires and `notify_interruption_complete()` (which cancels the deadline) is never reached. |
| 2 | **Yes** — the settle was armed *only* on `INTERRUPT_SEGMENT_ENDED` and cancelled on `INTERRUPT_SEGMENT_STARTED`; a lost END means it is never (re)armed at all, and `_do_confirm` itself cancels it. It was never armed at confirm. |
| 3 | **Yes** — `_interrupt_open_segments` is seeded to 1 and only a matched segment-END decrements it; a lost END plus a VAD re-segmentation of the ongoing speech leaves it ≥ 1 permanently, *after* the STT result for the actual utterance has already arrived. |
| 4 | **Yes** — `notify_interruption_complete()` is only reached from `_finalize_interrupt_turn()`, which is gated on the three converged counters; with a stuck `open` count it is reached only via the 15 s force-finalise (too late) — the 12 s cap in between does not reach it. |
| 5 | **Yes, but only via the timeout.** A new response is dispatched when the 15 s `_capture_timeout_guard` force-finalises while capture state was still (nominally) active — and the coalesced turn's `on_user_transcript` → `notify_response_dispatched` is what then clears the still-lingering gate/SM capture flags. In ordinary operation (fix in place) no response is dispatched while capture is active. |
| 6 | **Yes, transiently.** `HalfDuplexGate.capturing_interrupt` is set by the `InterruptionFrame` (via the mic-gate processor) and cleared by `notify_response_dispatched`; `VoiceConversationAdapter._capturing_interrupt` is set in `_begin_interrupt_capture` and cleared in `_finalize_interrupt_turn`. After a force-finalise the adapter clears first; the gate stays `True` until the queued replacement turn runs — a window in which a segment is submitted to STT (gate says capture) but its result is then handled as non-capture (adapter). |
| 7 | **A — the dropped 6360 ms was part of the SAME confirmed interruption** (the operator's continued speech / a trailing VAD segment of *"Słuchaj, źle to robimy…"*), dropped because the 15 s force-finalise closed the capture window while that segment was still being spoken. It is a downstream symptom of the same lifecycle bug, not a genuine new utterance. |

### Fix (minimal — no accepted-UX change)

`src/nexa/voice/bargein.py`, `…/adapter.py`, `…/runtime.py`:

1. **The settle window is armed at *confirm*** (in `_do_confirm`, before
   `broadcast_interruption()`) and is a **single long-lived task** that
   fires `settle_secs` after `_last_vad_activity` — a timestamp bumped on
   **every** VAD frame during `INTERRUPTING`: `VADUserStartedSpeakingFrame`,
   `VADUserStoppedSpeakingFrame`, **and `UserSpeakingFrame`** (the "still
   speaking" tick, emitted ~5×/s). A lost segment-END no longer prevents
   settling — the `UserSpeakingFrame` ticks keep the deadline pushed out
   until ~`settle_secs` after the operator actually stops.
2. **`_interrupt_open_segments` no longer gates finalisation.** It is
   diagnostic only. `_maybe_finalize` gates on `settled AND
   pending_results == 0`. On settle, any lingering `open` count is
   reconciled to 0 (VAD silent for the whole settle window ⟹ nothing is
   open) and recorded as *expected-late* STT results.
3. **`_do_confirm` reorder** — the adapter enters `_capturing_interrupt`
   and the settle + hard-cap are armed **synchronously, before** the
   `await broadcast_interruption()`. No VAD segment callback can land in a
   "state machine INTERRUPTING but adapter not capturing" gap.
4. **`_UtteranceCaptureFrameProcessor` consults the state machine
   directly** (`state == INTERRUPTING`), not just the gate boolean: a VAD
   segment of a still-confirmed interruption is **always** submitted to
   STT, **never** DROP_BUSY'd — killing the 6360 ms drop.
5. **Late STT result grace** — an STT result arriving within one decode of
   a finalise, *when an unmatched segment-start was reconciled*, is
   discarded quietly (INFO, no `DroppedTurn`) as a known artefact, not a
   busy-drop.
6. **Instrumentation** — a `capture_id` per confirmed interruption; one
   concise `nexa.voice.bargein: capture[N] <event> …` line per lifecycle
   step (`interrupt_confirmed`, `settle_armed`, `deadline_armed`,
   `segment_started/ended`, `settle_fired`, `interruption_complete
   deadline_cancelled`) plus the adapter's `interruption capture ready …`
   summary. The 12 s cap and 15 s timeout log at WARNING with "this should
   not happen in ordinary use".

The 12 s cap and the 15 s adapter timeout are **retained unchanged as
safety guards** — not raised, not removed. In a healthy session they now
fire **zero** times.

### Why the fix does not change accepted voice UX

Steady-state, non-interruption behaviour is untouched. For an
interruption: the replacement reply still dispatches ~`settle_secs`
(1.2 s) after the operator stops + the STT decode — exactly as intended;
the only observable change is that a rare interruption that used to stall
to 12–15 s now settles promptly. Multi-segment coalescing, one-canonical-
turn, PL/EN routing, Jenny/Gosia, `--no-bargein` R0026, the
`ProviderWindow` latency fix, `gemma4:e4b` / `num_thread=2` /
`keep_alive=30m` / Whisper / Piper / `ResponseLanguageResolver` — all
unchanged.

### New regression tests — `tests/test_bargein_m2_5b3.py` (9)

Built on the M2.5B.1 fixture (real `BargeInController` + adapter via
`build_bargein_stack`), driving VAD frames + `UserSpeakingFrame` ticks
with an injected clock:

| test | what it locks |
|---|---|
| A | single-segment interruption: settles promptly from ticks + a delivered END; the 12 s deadline task is cancelled, never fired |
| B | **the live bug:** the *only* segment-END is LOST — capture still settles from the ticks; **no WARNING logged** (no 12 s cap, no 15 s timeout); the coalesced turn carries the text |
| B2 | lost first segment-END + more speech → ONE coalesced turn; a late STT result for the lost segment is discarded quietly, **not** DROP_BUSY'd |
| C | when the replacement response begins, all capture state (adapter + gate + state machine counters) is already cleared |
| D | a genuinely new utterance after the replacement reply (past the late-result grace) is a normal `DROP_BUSY` |
| E | delayed STT: settle fires, capture waits for the owed result, then finalises **exactly once** |
| F | missing VAD **and** STT: only the hard timeout acts; state released, machine not latched, no crash |
| G | a ~6 s second segment of the confirmed interruption is **not dropped** — its text reaches the coalesced turn |
| H | 4 repeated interruptions: no stale deadline/timeout task from cycle N fires during cycle N+1 |

Test B **fails against the pre-fix code** (settle never fires) and passes
with the fix.

## OPERATOR ACCEPTANCE STATUS

**LIVE SESSION 2026-09-10 — overall UX GOOD / ACCEPTED BY OPERATOR
(conversation quality, naturalness, voice, local response speed, PL/EN
switching). REMAINING DEFECT: interruption-capture lifecycle / timeout —
root-caused + FIXED (M2.5B.3). NOT yet `OPERATOR-CONFIRMED` — one short
live re-test of the interruption lifecycle is owed.**

- M2.5B.3 interruption-capture lifecycle: the 12 s controller hard cap and
  15 s adapter timeout were firing for ordinary interruptions because the
  settle window was armed only on a VAD segment-END that
  `broadcast_interruption()` can flush from the controller's frame queue on
  a lagged Pi loop; the 6360 ms drop was a trailing segment of that same
  interruption. Fixed: settle armed at confirm + driven by every VAD frame
  (incl. `UserSpeakingFrame`); `_interrupt_open_segments` off the finalise
  gate; `_do_confirm` enters capture before the `await`; the capture
  processor never DROP_BUSY's a segment while the state machine is
  `INTERRUPTING`. 9 new deterministic regression tests. The 12 s / 15 s
  guards are retained (unchanged) and now fire **zero** times in a healthy
  session.
- Problem 1 (fragmentation): fixed (capture/coalesce phase) + 13 regression
  tests including the exact live reproduction.
- Problem 2 (late-session TTFT): root-caused on the real Pi — the context
  window evicting one turn per turn once history exceeds the cap
  permanently collapses Ollama prompt-prefix KV-cache reuse (78 s at cap
  20; 154–164 s at cap 40). M2.5B.1's `max_turns` 20→40 only **moved** the
  cliff. **M2.5B.2** `ProviderWindow` — a prefix-stable bounded
  provider-facing window over the (complete) canonical history —
  **eliminates the permanent collapse**: steady-state turns ~3.7 s for the
  whole session (110-turn real-Pi run, no drift). Its boundary crossing is
  a **context reset** (`keep_entries = 0`): the reset turn re-prefills only
  the persona prefix (cached) + the new user turn → **~3.4 s, an ordinary
  turn** (measured; the first M2.5B.2 cut kept a small window and cost
  ~20 s — rejected). `OLLAMA_NUM_PARALLEL=2` measured and rejected (SWA
  cache interference → ~49 s cold foreground). Not a queue, not a reload,
  not interrupted-history mutation, not an un-cancelled worker.
- Responsive cancellation: `cancel → worker-stop` ~2 s → 251 ms.
- Automated evidence: `pytest` 723 / `unittest` 730 / `ruff` clean /
  `git diff --check` clean; repeated-interruption stress stable; 7.28 s STT
  explained; M2.5B.2 reset-cost benchmark — see *M2.5B.2*; M2.5B.3
  interruption-lifecycle tests — see *M2.5B.3*.

`--no-bargein`'s mic/half-duplex policy remains byte-for-byte R0026; it
shares the M2.5B.2 provider-context fix (no semantics change).

## FILES CHANGED

**New:**

- `src/nexa/voice/interruption.py` — `InterruptionStateMachine` +
  `InterruptionState` / `InterruptionEvent` + `DEFAULT_CONFIRM_HOLD_SECS`.
- `src/nexa/voice/aec.py` — `AecReferenceHealth`.
- `src/nexa/voice/bargein.py` — `BargeInController` + `BargeInTelemetry` +
  `InterruptContext`.
- `src/nexa/voice_tts/spoken_text.py` — `SpokenTextTracker`.
- `src/nexa/voice_tts/aec_reference.py` — `AecReferenceFeeder` + `_PcmSink`.
- `src/nexa/voice_tts/bargein_wiring.py` — `BargeInStack` +
  `build_bargein_stack` (the single construction path; **wiring audit**).
- `tests/test_bargein_m2_5b.py` — 38 deterministic cases.
- `tests/test_bargein_wiring_m2_5b.py` — 12 integration/wiring cases (A–J
  + probe-uses-the-builder parity).
- `tests/test_bargein_m2_5b1.py` — **13** M2.5B.1 cases (no-fragmentation,
  cancellation-overlap audit, bilingual interruption, `--no-bargein`
  unchanged, 15-cycle stress).
- `tests/test_bargein_m2_5b3.py` — **9** M2.5B.3 interruption-capture
  lifecycle cases (lost segment-END still settles / no 12 s cap / no 15 s
  timeout; trailing segment never DROP_BUSY'd; delayed STT; missing
  VAD+STT; repeated interruptions).
- `src/nexa/voice_conversation/latency_ledger.py` — `LatencyLedger` /
  `TurnLedgerRecord` (M2.5B.1 per-turn latency instrumentation).
- `src/nexa/conversation/provider_window.py` — **M2.5B.2: `ProviderWindow`**
  — the prefix-stable bounded provider-facing window + `keep_entries=0`
  context-reset policy + wire rendering. Module header documents the whole
  strategy + evidence (incl. the `NUM_PARALLEL=2` rejection).
- `tests/test_provider_window.py` (16) / `tests/test_session_provider_window.py`
  (11) — **M2.5B.2** policy + `keep=0` reset + rendering + session
  integration.
- `docs/research/m2_5_bargein/measure_interrupt_latency_pi.py` — real-Pi
  latency / KV-cache / cancel-overlap spike (M2.5B.1) + its result JSONs.
- `docs/research/m2_5_bargein/research_provider_context_kv.py`,
  `research_prewarm_resume.py`,
  `bench_long_session_provider_window.py`,
  `bench_provider_window_reset.py` — **M2.5B.2** research spikes + the two
  real-Pi long-session benchmarks (first-cut + `keep_entries` sweep /
  reset) + their result JSONs.
- `docs/reports/R0029_…md` — this report.

**Changed:**

- `src/nexa/conversation/turn.py` — `ConversationTurn.interrupted` field.
- `src/nexa/conversation/session.py` — `commit_interrupted_turn` +
  `InterruptedTurnOutcome`; **M2.5B.2: `provider_window` field**,
  `send()` renders via it when set (rollover consumed first),
  `prewarm_provider_context()` (the idle-gap pre-warm seam, not
  auto-fired in M2.5B.2 — see *M2.5B.2*).
- `src/nexa/conversation/__init__.py` — export `InterruptedTurnOutcome`;
  **M2.5B.2: export `ProviderWindow` + its default constants.**
- `src/nexa/conversation/context.py` — `INTERRUPTED_WIRE_SUFFIX`
  deterministic wire annotation; **M2.5B.1: `DEFAULT_MAX_TURNS` 20 → 40,
  `DEFAULT_MAX_CHARS` 12 000 → 20 000** so a normal session never evicts a
  turn mid-session (the first eviction permanently collapses KV-cache
  prefix reuse — Problem 2 root cause). Header documents the mechanism.
  **M2.5B.2** makes the M2.5B.1 constants the *fallback* (`provider_window`
  = `None`) path bound; the voice path uses `ProviderWindow` instead.
- `src/nexa/providers/ollama.py`, `src/nexa/providers/llama_server.py` —
  **M2.5B.1: `select()` poll before each streamed `readline()`** so the
  worker sees `cancel_token.is_cancelled` ~4×/s (was parked in a blocking
  `readline` between tokens → `cancel → stop` ~2 s; now ≤ 251 ms). Socket
  stays blocking (a per-read `settimeout` corrupts `http.client`'s chunked
  reader). `mark_cancel_observed()` / `mark_worker_stopped()`.
- `src/nexa/providers/base.py` — **M2.5B.1: `CancelToken`** gains
  `mark_cancel_observed()` / `mark_worker_stopped()` / `cancel_observed` /
  `worker_stopped` / `wait_worker_stopped(timeout)`.
- `src/nexa/stt/queue.py`, `src/nexa/voice_conversation/queue.py` —
  **M2.5B.1: `in_flight` property** (queue depth alone hid in-flight work
  in the ledger).
- `src/nexa/voice/gate.py` — `HalfDuplexGate(bargein_enabled, aec_health)`,
  `bargein_active`, `admit_next_utterance` / `should_drop_busy_utterance`,
  observes `InterruptionFrame`.
- `src/nexa/voice/config.py` — `LocalAudioConfig.bargein_enabled`.
- `src/nexa/voice/runtime.py` — `VoiceRuntime(bargein_controller=…)`
  inserted after VAD; capture drop uses `should_drop_busy_utterance()`.
- `src/nexa/voice/__init__.py`, `src/nexa/voice_tts/__init__.py` — exports.
- `src/nexa/voice_tts/bridge.py` — `AssistantSpeechBridge(on_interruption=…)`
  + `InterruptionFrame` queue drain.
- `src/nexa/voice_tts/continuity.py` — discard (not release) the held
  phrase on `InterruptionFrame`.
- `src/nexa/voice_conversation/adapter.py` — per-turn `CancelToken` +
  `response_id`, `interrupt_active_turn()` (**+ capture the spoken prefix
  on the loop at confirm time**), `_commit_interrupted` (**uses the
  captured value**), `on_turn_interrupted` / `InterruptedTurn`,
  `response_id_source` / `spoken_prefix_source`. **M2.5B.1:**
  interruption-capture phase — `handle_transcription` accumulates every
  segment result *before* the DROP_BUSY check; `CoalescedInterruptTurn`
  (one canonical turn from N segments) via `note_interrupt_segment_*` /
  `note_interrupt_capture_settled` + a hard timeout guard;
  `CancelCompletion` / `on_cancel_completed` + `_watch_cancel_completion`
  (measures `cancel → worker-stop` off-loop); `llm_cancel_requested`
  vs `provider_worker_stopped_at_commit` split.
- `src/nexa/voice/interruption.py` — **`last_invalidated_response_id`**
  (wiring-audit hardening; the controller reads it in `_do_confirm`).
  **M2.5B.1:** `INTERRUPTING` is a *capture* state —
  `INTERRUPT_SEGMENT_STARTED` / `_ENDED` events, `capture_open_segments` /
  `capture_segments_ended`; `notify_response_finished` a no-op from
  `INTERRUPTING` (only `notify_interruption_complete` exits it).
- `src/nexa/voice/bargein.py` — **M2.5B.1:** per-segment settle window
  (`DEFAULT_INTERRUPT_SETTLE_SECS = 1.2`, restarted each segment; hard cap
  `DEFAULT_MAX_CAPTURE_SECS = 12`), `set_capture_hooks(...)`,
  `notify_interruption_complete()`, `_arm_settle` / `_capture_deadline`;
  telemetry `interrupt_segments` / `_ended`.
- `src/nexa/voice/gate.py` — **M2.5B.1:** the `admit_next_utterance`
  one-shot is replaced by a `capturing_interrupt` *phase* (set on
  `InterruptionFrame`, cleared on the next `notify_response_dispatched`);
  `should_drop_busy_utterance()` never drops while capturing.
- `src/nexa/voice_tts/bargein_wiring.py` — **M2.5B.1:** `bind_adapter()`
  (late-binds controller capture hooks ↔ adapter), `on_interruption_
  complete()`; `note_interruption()` now a controller no-op.
- `apps/nexa_bilingual_voice_probe.py` — **M2.5B.1:**
  `interruption_complete_hook` + `stack.bind_adapter(adapter)`.
- `apps/nexa_bilingual_voice_probe.py` — `--bargein` / `--no-bargein`;
  **refactored onto `build_bargein_stack`** (the hand-rolled construction
  removed); live surface. **M2.5B.2: sets `session.provider_window =
  ProviderWindow()`** for both modes (prints its bounds).
- `tests/test_voice_half_duplex_gate.py`,
  `tests/test_voice_architecture.py`,
  `tests/test_voice_tts_continuity.py` — three assertions updated for the
  M2.5B premise (documented above).
- `src/nexa/voice/bargein.py` — **M2.5B.3:** settle armed at confirm,
  driven by `_last_vad_activity` (bumped on every VAD frame incl.
  `UserSpeakingFrame`), single long-lived re-checking `_settle_after`;
  `_do_confirm` enters capture (`_on_confirmed` + arm settle + arm cap)
  **before** `await broadcast_interruption()`; `capture_id` + concise
  lifecycle trace (`_trace`); the hard cap logs at WARNING as a guard.
- `src/nexa/voice_conversation/adapter.py` — **M2.5B.3:**
  `_maybe_finalize_interrupt` gates on `settled AND pending == 0` only
  (`_interrupt_open_segments` is diagnostic); `note_interrupt_capture_
  settled` reconciles a lingering open count to 0 + records
  `_late_interrupt_results`; a late interruption-segment STT result within
  one decode of finalise is discarded quietly (not a `DroppedTurn`);
  `_finalize_interrupt_turn` logs a "capture ready" summary; the 15 s
  timeout logs at WARNING as a guard.
- `src/nexa/voice/runtime.py` — **M2.5B.3:** `_UtteranceCaptureFrame
  Processor(is_capturing_interrupt=…)` — a segment is never DROP_BUSY'd
  while the `InterruptionStateMachine` is `INTERRUPTING`, regardless of the
  gate boolean.

**Unchanged (verified):** `gemma4:e4b` / `num_thread=2` / `keep_alive=30m`
/ warm-up, whisper `ggml-base-q8_0 -t4`, `LanguageIdGuard` thresholds,
`ResponseLanguageResolver` semantics, Piper voices / speed,
`NexaSpeechPlanner` normal pacing, continuity normal policy, memory, model
router, cloud/local policy. No cloud STT, no second LLM, no second
conversation brain, no new framework.

## COMMIT HASH

**M2.5B (implementation):** `c3155ca` (barge-in core) → `dcf0df4` (38
deterministic tests + continuity discard) → `59141f6` (probe wiring + AEC
status) → `c1306b6` → `97f4a84` → `20df578` (wiring audit +
`build_bargein_stack`) → `69e8828` (hash record).

**M2.5B.1 (this fix cycle):**

- `2460463` — Problem 1: interruption-utterance fragmentation → capture /
  coalesce phase (one confirmed interruption = one canonical turn).
- `5e6f877` — latency ledger + `CancelToken` completion signals + Ollama
  `last_metrics`.
- `4a55a7e` — Problem 2 *partial mitigation*: `DEFAULT_MAX_TURNS` 20→40,
  `DEFAULT_MAX_CHARS` 12k→20k + responsive LLM cancellation (`select()`
  poll; `cancel → worker-stop` ~2 s → 251 ms) + 8 regression tests.
- `893c8a3` — M2.5B.1 hash record.

**M2.5B.2 (long-session context / KV-cache stability):**

- `7f4e182` — `nexa.conversation.ProviderWindow` (first cut): prefix-stable
  bounded provider-facing window; `keep_entries` 2/4 verbatim carry across
  a synchronous rollover (~10–20 s reset turn — superseded).
- `79a6899` — **`keep_entries = 0` context reset** (the reset turn
  re-prefills only persona + the new user turn → ~3.4 s ordinary turn);
  `OLLAMA_NUM_PARALLEL=2` measured + rejected; `bench_provider_window_
  reset.py` real-Pi `keep_entries` sweep + 112-turn / 10-reset run; this
  report's M2.5B.2 rewrite; 27 regression tests.
- `b9f3738` — M2.5B.2 hash record.

**M2.5B.3 (interruption-capture lifecycle):**

- `<PENDING>` — settle armed at confirm + `_last_vad_activity`-driven
  (bargein.py); `_interrupt_open_segments` off the finalise gate + late
  segment-result grace (adapter.py); capture processor consults the state
  machine directly so no interruption segment is DROP_BUSY'd (runtime.py);
  `capture_id` lifecycle trace; 9 new regression tests
  (`test_bargein_m2_5b3.py`); this report's M2.5B.3 section.

This hash-record edit lands in the immediately-following commit
(R0026/R0027/R0028 pattern). Prior milestone tip: `2f23613` (M2.5A
closure). **Not pushed.**

## GIT STATUS

Branch `main`, **not pushed**. `git diff --check` clean. Sequence:
`2f23613` (M2.5A closed) → … → `4a55a7e` → `893c8a3` (M2.5B.1) →
`7f4e182` → `79a6899` → `b9f3738` (M2.5B.2) → **`<PENDING>` (M2.5B.3)** →
hash-record commit (this edit). Frozen components (model / `num_thread` /
`keep_alive` / `num_ctx` / whisper / Piper / `ResponseLanguageResolver`)
untouched; the Ollama service was briefly reconfigured to
`OLLAMA_NUM_PARALLEL=2` for a measurement and **reverted** (back to
`-np 1`, swap back to baseline). `DEFAULT_MAX_TURNS` / `DEFAULT_MAX_CHARS`
(M2.5B.1) and the new `ProviderWindow` (M2.5B.2) are deliberate
context-policy changes, never on the frozen list; the M2.5B.3 change is
internal interruption-capture bookkeeping only — no conversation-semantics
or accepted-UX change. Canonical `ConversationSession.history` semantics
unchanged.

## RISKS

1. **Live confirm-hold vs real VAD cadence.** The 300 ms hold is from
   R0028's reasoning, not a live barge-in measurement. If the operator
   session shows spurious confirmations, the first knob is the hold
   (`confirm_hold_secs`); the second is a response-time `min_volume`
   profile. `min_volume=0.6` is retained (it held the AEC residual below
   the gate in M2.5A.2).
2. **Spoken-prefix precision.** Synthesized-sentence, not last-audio-frame.
   The committed prefix may run a sentence past what was heard. Documented;
   acceptable; refine with a `BotStoppedSpeaking`-per-sentence seam later
   if the operator finds it jarring.
3. **AEC feed under sustained load.** The drop-oldest queue protects the
   pipeline but a persistently slow `aplay` pipe would degrade the
   reference (more `chunks_dropped`) and could, in the worst case, let
   residual echo through. The `min_volume` gate is the backstop; telemetry
   makes it visible; the live probe should watch `chunks_dropped`.
4. **Pipecat `handle_interruptions` timing on this hardware.** The
   media-stop leg was measured at 28.5 ms mean / 37.4 ms max with the
   *research* kill path (`aplay` reaped); Pipecat's own
   cancel-and-recreate-audio-task path is a comparable order but unmeasured
   here. The live probe's "old audio stops promptly" is the check.
5. **Nested / rapid interruptions in the wild.** Covered deterministically
   (case 29) and by the single-candidate gate; live matrix case F is the
   real-world check.
6. **`ConversationTurn.interrupted` on a frozen dataclass.** Additive +
   defaulted; every construction site and `to_provider_messages` audited;
   typed-chat wire output byte-identical (test 27).
7. **M2.5B.2 context-reset continuity dip.** The window removes the
   permanent KV-cache collapse *and* the ~20 s rollover turn (that first
   cut, `keep_entries` 4–6, is superseded). The residual is behavioural,
   not latency: at a reset (`keep_entries = 0`, ~once per ~44 exchanges)
   the model sees no recent conversational context for that turn and ~1–2
   after, until the window regrows by append. A pure follow-up ("and the
   third one?") spoken right at a reset may get a clarifying question
   back. Canonical history is intact; M5 long-term memory reads all of it.
   If a live session shows this as jarring, `keep_entries = 2` restores
   one exchange across the seam at a ~10 s (bench) / ~24 s (full voice)
   reset turn — a worse latency trade, so only on request.
8. **Removing even the continuity dip** needs a *non-blocking* background
   pre-warm of the small post-reset window. On one Ollama slot a pre-warm
   blocks a concurrent turn for its whole prefill; `OLLAMA_NUM_PARALLEL=2`
   was measured on this Pi and **rejected** — gemma4's sliding-window
   attention makes a foreground turn on the other slot cold-reprocess
   (~49 s), plus CPU contention and swap. The seam
   (`prewarm_provider_context` / `mark_prewarmed` / `cutover_background`)
   is built and tested for a future resource-safe mechanism (a dedicated
   pre-warm runner, or a llama.cpp slot-save path Ollama does not yet
   expose).

## NEXT STEP

1. **Operator runs ONE short live re-test — the interruption-capture
   lifecycle** (command + short interaction list below). PASS if: no
   `interruption capture hit the 12.0s cap` line, no `interruption capture
   timed out after 15.0s` line, no `DROP_BUSY … dropped …ms of audio` for
   an interruption's own speech, and every interruption's replacement reply
   still starts promptly (~1–3 s after you stop). On PASS: mark M2.5B
   **OPERATOR-CONFIRMED**, update `CURRENT_STATE.md`, record the commit
   hash.
2. If the re-test still shows a 12 s / 15 s line, capture the terminal —
   the `nexa.voice.bargein: capture[N] …` trace lines make one
   interruption fully reconstructable.
3. Non-blocking, owed independently: the B.3.6 operator latency
   re-confirmation; a resource-safe non-blocking pre-warm to also remove
   the M2.5B.2 reset continuity dip.

---

## OPERATOR ACTION REQUIRED — ONE LIVE SESSION

From the repo root:

```
.venv/bin/python apps/nexa_bilingual_voice_probe.py --bargein
```

Wait for **`✓ AEC REF ACTIVE`** to print (barge-in stays in the R0026 safe
mode until it does). Then, in one continuous session, say — and watch the
terminal for `INTERRUPT CONFIRMED` + `interrupted turn committed`:

- **Self-echo (case 7):** ask *"Opowiedz mi dokładnie, jak powstaje czarna
  dziura."* and let NeXa answer **without interrupting** for ~15 s.
  Expect: **no** `INTERRUPT CANDIDATE`, the answer finishes normally.
- **A. PL interrupt PL:** ask *"Opowiedz mi wszystko o czarnych dziurach."*;
  while NeXa is speaking, say *"Czekaj. Powiedz tylko, jak powstaje."*
  Expect: old speech stops fast, `✂ INTERRUPT CONFIRMED`, a new PL answer
  to the shorter question, same session.
- **B. PL interrupt EN:** ask *"Z czego składa się gwiazda?"*; interrupt
  with *"Actually, just tell me its colour."* Expect: EN answer, Jenny
  voice.
- **C. EN interrupt PL:** ask *"Tell me about the speed of light."*;
  interrupt with *"Nie, powiedz po polsku, dlaczego niebo jest niebieskie."*
  Expect: PL answer, Gosia voice.
- **D. Think-window:** ask *"Tell me about black holes."*; interrupt
  **before NeXa starts speaking** with *"Actually, only explain how they
  form."* Expect: `outcome=rolled_back_user_turn`, the second question is
  answered; no consecutive user turns.
- **E. No interruption:** ask *"Po co człowiekowi sen?"* and let it finish.
  Expect: normal completion, `response_finished`.
- **F. Nested:** interrupt one answer (say *"Krócej."*), then interrupt the
  **next** answer too. Expect: two `INTERRUPT CONFIRMED`, monotonic
  `response_id`, queues clean, no slowdown.

**PASS if:** `✓ AEC REF ACTIVE` held; NeXa's own voice never triggered a
barge-in; every deliberate interruption stopped the old audio promptly and
cancelled the old reply; exactly one interrupting utterance was admitted
each time; the new answer was in the right language with the right voice;
no stale phrase resumed; `Ctrl+C` and re-run `--no-bargein` behaves exactly
as before. Paste the terminal (or just the `INTERRUPT …` / `interrupted
turn committed` / `AEC REF` lines) back.

**No TV. No milestone after M2.5B.**
