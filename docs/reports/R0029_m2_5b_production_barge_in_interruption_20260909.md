# R0029 — M2.5B: Production Barge-In / Interruption

- **Date:** 2026-09-09
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.5B — production
  barge-in / interruption.**
- **Status:** **LIVE ACCEPTANCE FAILED once (2026-09-09) → M2.5B.1
  (interrupt integrity + cancellation) + M2.5B.2 (long-session context /
  KV-cache stability) applied; automated Pi evidence clean; ONE live
  re-test owed. NOT operator-confirmed.** The first `--bargein` operator
  session showed: AEC healthy; barge-in cancellation worked; PL↔EN
  switching worked — **but** (1) an interrupting utterance spoken as two
  VAD segments was **fragmented** — **FIXED** (M2.5B.1, capture/coalesce
  phase); and (2) model time-to-first-token degraded to tens of seconds
  later in the session. **Root cause of (2):** the context window evicting
  its oldest turn every turn once history exceeds the cap permanently
  collapses Ollama's prompt-prefix KV-cache reuse (78 s at cap 20, 154 s
  at cap 40). M2.5B.1's `max_turns` 20→40 only *moved* that cliff.
  **M2.5B.2 removes it:** `ProviderWindow` — a prefix-stable, bounded
  *provider-facing* window over the (still complete) canonical
  `ConversationSession.history`; steady-state turns stay ~4 s for the
  whole session; the window rolls over with one bounded, rare, logged
  ~20-32 s turn instead of the permanent every-turn regime. Real-Pi
  110-turn benchmark: multiple rollovers, no permanent regime. Responsive
  cancellation (`cancel → worker-stop` ~2 s → 251 ms) stands.
  `--no-bargein`'s mic policy is byte-for-byte R0026; it shares the
  M2.5B.2 provider-context fix. Not pushed.
- **Related:** `R0028` (M2.5A architecture + real-hardware feasibility;
  **M2.5A COMPLETE / OPERATOR-CONFIRMED 2026-09-09**), `R0026` (the
  half-duplex behaviour this milestone replaces when enabled), `R0027`
  (one-turn vs sticky response language — must survive an interruption),
  `R0025`/`R0024` (bilingual STT), `ADR-0003` D8 ("barge-in is M2.5"),
  `R0009` (KV-cache prefix discipline).

---

## TASK RESULT

**IMPLEMENTED; LIVE ACCEPTANCE FAILED once; M2.5B.1 fix in progress.**
Production barge-in is implemented end-to-end behind
`LocalAudioConfig.bargein_enabled` (default `False`). With the flag off the
M1/M2.1/M2.4/R0026 pipeline is **byte-for-byte unchanged** (whole existing
suite green, three prior lock-down assertions updated for the M2.5B
premise). The first live `--bargein` session (2026-09-09) surfaced two
defects — **Problem 1** interrupt-utterance fragmentation (**FIXED**,
commit `2460463`, regression-tested) and **Problem 2** late-session model
TTFT collapse (**root-caused + FIXED** — a pre-existing 20-turn
context-window eviction that permanently breaks Ollama prompt-prefix
KV-cache reuse; window resized so a normal session never evicts; see
*M2.5B.1*). **Not `OPERATOR-CONFIRMED`.** With the flag on:

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

### Architecture chosen — stable window + bounded small rollover (`nexa.conversation.ProviderWindow`)

Smallest design the measurements support:

* **`ConversationSession.provider_window`** (opt-in; `None` = the
  pre-M2.5B.2 `ConversationContext` path, byte-for-byte — typed chat and
  every existing test unaffected). The voice probe sets it for **both**
  `--bargein` and `--no-bargein` (a latency fix, no conversation-semantics
  change — the R0026 mic/half-duplex policy is untouched).
* The model is shown **`history[base:]`** — a verbatim recent window.
  `base` is **stable between rollovers**, so every ordinary turn is a pure
  prefix-extension → **~4 s**, exactly as inside the old cap, *for as long
  as the session lasts*.
* When the window reaches `hard_entries` with no pre-warm ready, the next
  `send` **cuts over synchronously**: `base` jumps so only the last
  `keep_entries` (a small, even count — one whole exchange minimum)
  survive. That one turn pays a **bounded** cold prefill of just
  `keep_entries` worth of tokens (the fixed persona prefix stays cached
  across the cutover), and it is **logged at WARNING** — never silent.
  Every subsequent turn is warm again until the next rollover, roughly
  once per `hard_entries − keep_entries` entries.
* The seam for a *free* rollover already exists —
  `ProviderWindow.mark_prewarmed` / `prewarm_ready` / `cutover_background`
  and `ConversationSession.prewarm_provider_context()` (pre-warm the small
  post-roll window in an idle gap; Q4 shows the next turn is then ~7 s).
  It is **not wired to fire automatically in M2.5B.2**: because a pre-warm
  blocks the model uninterruptibly, firing it speculatively risks the same
  ~`keep_entries`-prefill wait it is trying to save. A *non-blocking*
  background pre-warm needs a second Ollama slot
  (`OLLAMA_NUM_PARALLEL≥2`) — a serving change deferred past M2.5B.2 (see
  *RISKS*). Until then the bounded sync rollover is the "safest bounded
  temporary policy": one ~`keep_entries`-sized slow turn per rollover
  instead of the permanent every-turn 78–164 s regime.

`base` always lands on a turn boundary, so a rollover never orphans an
assistant reply; `_response_languages` stays index-aligned; `interrupted`
turns keep their `INTERRUPTED_WIRE_SUFFIX`; canonical `history` is never
touched.

### Canonical history vs provider context

| | canonical `ConversationSession.history` | provider-facing context |
|---|---|---|
| authority | **the one transcript** (ADR-0003 D2) | a derived, bounded *view* |
| bound | none — grows for the whole session | `history[base:]`, `base` advances at rollover |
| interrupted turns | stored verbatim (`interrupted=True`) | rendered with `INTERRUPTED_WIRE_SUFFIX` while in-window |
| response-language slots | `_response_languages`, index-aligned, never rolled | replayed per in-window USER turn |
| future memory (M5) | reads all of it | irrelevant |

Nothing is deleted. A turn that scrolls out of the provider window is
still in `history` for M5 long-term memory to summarise/recall.

### KV-cache reuse behaviour

* **Steady state:** `[persona][voice-dir][history[base:]] + [new user]` — a
  pure byte-identical prefix-extension every turn → Ollama re-evaluates
  only the ~30–60 genuinely new tokens (~3–4 s).
* **At a rollover:** the prefix changes at `history[base]`; `[persona]
  [voice-dir]` (≈ 330 tok) is still a common prefix and stays cached, so
  the cold cost is ≈ `keep_entries` window tokens + the new user turn
  (bounded — see the benchmark).
* **`num_ctx`:** `soft`/`hard` are sized so the steady-state window never
  approaches Ollama's 8 k `num_ctx` (where its own `--context-shift` would
  collapse the cache anyway); `hard − keep` sets the rollover frequency.

### Real-Pi long-session benchmark — `bench_long_session_provider_window.py`

**110 turns**, real `gemma4:e4b`, a deliberately small window
(`keep=6 / soft=16 / hard=22` entries) so the rollover boundary is
crossed often; mix of normal PL, normal EN, CASE-A interrupt rollback (t13
/ 37 / 68 / 97) and CASE-B interrupted spoken-prefix commit (t21 / 53 /
84). Result JSON `bench_long_session_provider_window_20260910_152509.json`.

| metric | value |
|---|---|
| turns | 110 |
| rollovers (all synchronous) | **12** — turns 12, 21, 29, 37, 46, 54, 62, 71, 79, 87, 95, 104 (~every 8 turns, as designed) |
| **warm-turn TTFT** (93 turns) | **mean 3.72 s · p50 3.60 s · max 7.04 s** |
| warm TTFT early / mid / late third | 3.72 / 3.65 / 3.84 s mean — **no drift** |
| rollover-turn TTFT (12 turns) | mean **22.7 s** · p50 22.7 s · min 18.9 s · **max 25.2 s** — bounded, never approaches the old 78–164 s |
| turns > 15 s | **exactly the 12 rollover turns**, each logged `provider-context SYNC rollover …` — **zero** warm turns > 15 s |
| `load_duration` | 1.4–5.7 ms every turn — **no model reload** |
| canonical history at end | **212 entries / 22 262 chars — complete** (`provider_window.base` = 192, i.e. the model saw only the last ~20 entries, but all 212 are in `history`) |
| CASE A / CASE B interrupts | all correct (`rolled_back_user_turn` / `committed_spoken_prefix`), including t21 where a rollover and a barge-in coincided |
| PL / EN | mixed throughout, no mis-routing |

**Reading:** the permanent cliff is **gone** — turn 110 (canonical
history 212 entries) is 3.83 s, identical to turn 1. What remains is one
**bounded, logged, ~19–25 s** maintenance turn per rollover. With the
benchmark's short turns that is ~12 s (`keep=2`, the shipped default) to
~22 s (`keep=6`); projected to full voice turns, `keep=2` ⇒ **~16 s**,
`keep=4` ⇒ ~28–32 s. At the shipped `soft=72 / hard=88` a rollover happens
only ~once per ~43 exchanges. `ANY TURN > 15 s?` — **YES**, the rollover
turns (explained, logged, rare); no *unexplained* or *steady-state* turn
exceeds ~7 s. Removing the rollover turn needs the `OLLAMA_NUM_PARALLEL`
follow-up (*RISKS 7*).

### Regression tests

`tests/test_provider_window.py` (15) — policy (bounds, arming, background
vs sync cutover, base stays on a turn boundary, repeated rollovers stay
bounded) + rendering (window slice only, pure prefix-extension at a fixed
base, **byte-identical to `ConversationContext.to_provider_messages` for
the same slice**, `INTERRUPTED_WIRE_SUFFIX`, language-alignment guard).
`tests/test_session_provider_window.py` (10) — `provider_window=None` is
the legacy path; windowed `send` renders only `history[base:]`; a pre-warm
then the next `send` cuts over cheaply and re-bounds the window; the hard
limit forces a **logged** sync rollover; prefix is a pure extension
between rollovers; response-language slots stay aligned across a rollover;
CASE-A interrupted rollback still works with a window; canonical history
stays complete throughout.

## OPERATOR ACCEPTANCE STATUS

**FAILED once (2026-09-09); fixes applied + automated Pi evidence clean;
ONE live re-test now owed.** M2.5B is **not** `OPERATOR-CONFIRMED`.

- Problem 1 (fragmentation): fixed (capture/coalesce phase) + 13 regression
  tests including the exact live reproduction.
- Problem 2 (late-session TTFT): root-caused on the real Pi
  (`gemma4:e4b`) — the context window evicting one turn per turn once
  history exceeds the cap permanently collapses Ollama prompt-prefix
  KV-cache reuse (3 s → 78 s at cap 20; 154–164 s at cap 40). M2.5B.1's
  `max_turns` 20→40 was a **partial mitigation only** — it moved the cliff
  to turn 40. **M2.5B.2 removes it:** `ProviderWindow` — a prefix-stable,
  bounded *provider-facing* window over the (still complete) canonical
  history, with a bounded, rare, logged synchronous rollover instead of
  the permanent every-turn collapse. Not a queue, not a reload, not
  interrupted-history mutation, not an un-cancelled worker.
- Responsive cancellation: `cancel → worker-stop` ~2 s → 251 ms
  (`select()` poll in the provider worker).
- Automated evidence: `pytest` 712 / `unittest` 719 / `ruff` clean;
  repeated-interruption stress (15 cycles) stable; the 7.28 s STT is
  explained (CPU contention from the collapsed prompt-eval, gone once
  Problem 2 is fixed); M2.5B.2 real-Pi long-session benchmark — see
  *M2.5B.2* (110 turns, multiple rollovers, no permanent regime).

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
- `src/nexa/voice_conversation/latency_ledger.py` — `LatencyLedger` /
  `TurnLedgerRecord` (M2.5B.1 per-turn latency instrumentation).
- `src/nexa/conversation/provider_window.py` — **M2.5B.2: `ProviderWindow`**
  — the prefix-stable bounded provider-facing window + rollover policy +
  wire rendering. Module header documents the whole strategy + evidence.
- `tests/test_provider_window.py` (15) / `tests/test_session_provider_window.py`
  (10) — **M2.5B.2** policy + rendering + session integration.
- `docs/research/m2_5_bargein/measure_interrupt_latency_pi.py` — real-Pi
  latency / KV-cache / cancel-overlap spike (M2.5B.1) + its result JSONs.
- `docs/research/m2_5_bargein/research_provider_context_kv.py`,
  `research_prewarm_resume.py`,
  `bench_long_session_provider_window.py` — **M2.5B.2** research spikes +
  the real-Pi long-session benchmark + their result JSONs.
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

- `<PENDING>` — `nexa.conversation.ProviderWindow`: prefix-stable bounded
  provider-facing window over the (complete) canonical history; bounded
  logged synchronous rollover; `ConversationSession.provider_window` +
  `prewarm_provider_context()`; probe sets it; 25 regression tests; the
  research spikes + real-Pi 110-turn benchmark; this report's M2.5B.2
  section.

This hash-record edit lands in the immediately-following commit
(R0026/R0027/R0028 pattern). Prior milestone tip: `2f23613` (M2.5A
closure). **Not pushed.**

## GIT STATUS

Branch `main`, **not pushed**. `git diff --check` clean. Sequence:
`2f23613` (M2.5A closed) → `c3155ca` → `dcf0df4` → `59141f6` → `c1306b6`
→ `97f4a84` → `20df578` → `69e8828` → `2460463` → `5e6f877` → `4a55a7e` →
`893c8a3` → **`<PENDING>` (M2.5B.2)** → hash-record commit (this edit).
Frozen components (model / `num_thread` / `keep_alive` / `num_ctx` /
whisper / Piper / resolver / `ResponseLanguageResolver`) untouched.
`DEFAULT_MAX_TURNS` / `DEFAULT_MAX_CHARS` (M2.5B.1) and the new
`ProviderWindow` (M2.5B.2) are deliberate context-policy changes, never on
the frozen list; canonical `ConversationSession.history` semantics
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
7. **M2.5B.2 rollover turn (the residual).** The provider window removes
   the *permanent* KV-cache collapse: steady-state turns stay ~4 s for the
   whole session. What remains is **one bounded, logged, ~20-32 s turn per
   rollover** (~once per `hard - keep` ≈ 84 entries with the production
   defaults). It is an *explained maintenance* turn, not an unexplained
   steady-state one, and vastly better than the every-turn 78-164 s
   regime — but it is still a user-visible pause. Removing it needs a
   **non-blocking background pre-warm**, which needs a **second Ollama KV
   slot** (`OLLAMA_NUM_PARALLEL>=2`, or a dedicated pre-warm runner): an
   Ollama-serving change (roughly doubles KV memory; splits `num_ctx`
   across slots unless raised) that is out of M2.5B.2's minimal remit. The
   seam is built and tested (`prewarm_provider_context` /
   `cutover_background`); wiring it non-blockingly is the follow-up. Knobs
   until then: `keep_entries=2` (~halve the rollover cost, less
   post-rollover context) or a larger `hard_entries` (rarer rollovers,
   closer to `num_ctx`).
8. **Post-rollover continuity.** For the few turns after a rollover the
   model sees only the last `keep_entries` exchanges verbatim (canonical
   history is intact — M5 memory reads all of it). The window regrows by
   append within ~2-3 turns. `keep_entries=4` keeps two full exchanges
   across the seam.
9. **Rollover cost scales with real-turn size.** The benchmark's short
   turns roll in ~21 s; full voice turns (~2× the tokens per entry)
   project to ~28-32 s at `keep_entries=4`. If a live session shows this
   as too long, drop `keep_entries` or bring the `NUM_PARALLEL` follow-up
   forward.

## NEXT STEP

1. **Operator runs the live acceptance session** (below) — now also a
   long-session check: keep talking past ~40 exchanges and confirm turns
   stay in the ~3-10 s band, with at most an occasional **logged**
   `provider-context ... rollover` turn (~20-32 s) and no unexplained
   creep toward tens of seconds. On PASS: mark M2.5B
   **OPERATOR-CONFIRMED**, update `CURRENT_STATE.md`, record the commit
   hash.
2. If cases 1/A–F expose a tuning need, adjust `confirm_hold_secs` /
   response-time VAD profile only — no architecture change. If the
   rollover turn is too long, drop `ProviderWindow(keep_entries=…)` or
   bring the `OLLAMA_NUM_PARALLEL` non-blocking-pre-warm follow-up forward.
3. Non-blocking, owed independently: the B.3.6 operator latency
   re-confirmation (STT latency + END_OF_TURN → first-audio); the
   non-blocking background pre-warm (second Ollama KV slot) to remove the
   M2.5B.2 rollover turn entirely.

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
