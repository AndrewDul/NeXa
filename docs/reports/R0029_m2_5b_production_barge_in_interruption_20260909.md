# R0029 — M2.5B: Production Barge-In / Interruption

- **Date:** 2026-09-09
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.5B — production
  barge-in / interruption.**
- **Status:** **IMPLEMENTED, deterministic + hardware-safe tests GREEN;
  awaiting the live operator acceptance session.** `--no-bargein` (the
  default) is byte-for-byte R0026. Not pushed.
- **Related:** `R0028` (M2.5A architecture + real-hardware feasibility;
  **M2.5A COMPLETE / OPERATOR-CONFIRMED 2026-09-09**), `R0026` (the
  half-duplex behaviour this milestone replaces when enabled), `R0027`
  (one-turn vs sticky response language — must survive an interruption),
  `R0025`/`R0024` (bilingual STT), `ADR-0003` D8 ("barge-in is M2.5"),
  `R0009` (KV-cache prefix discipline).

---

## TASK RESULT

**PASS (implementation).** Production barge-in is implemented end-to-end
behind `LocalAudioConfig.bargein_enabled` (default `False`). With the flag
off the M2.1/M2.4/R0026 pipeline is **byte-for-byte unchanged** (existing
suite green, two R0026 lock-down guard tests updated for the M2.5B
premise). With the flag on:

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

**38 deterministic tests** (`tests/test_bargein_m2_5b.py`) + updated
guards; **full suite `pytest` 659 / `unittest` 666, `ruff` clean, `git
diff --check` clean**; a **hardware-safe integration smoke** (real
reSpeaker + real `aplay -D plug:respeaker`, operator absent) passed: AEC
reference active, mic hot during a simulated reply, **0 false candidates /
0 confirmations while silent**, clean teardown.

**Remaining:** one short live operator session (script below). Nothing is
marked `OPERATOR-CONFIRMED` until it is run.

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

Full suite: **`pytest` 659 passed / 7 skipped / 14 subtests** ·
**`python -m unittest discover -s tests` 666 OK / 7 skipped** ·
**`ruff check src tests apps` clean** · **`git diff --check` clean**.

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

## OPERATOR ACCEPTANCE STATUS

**NOT YET RUN.** M2.5B is not `OPERATOR-CONFIRMED` until the live session
(script below) passes. `--no-bargein` remains available and is
byte-for-byte R0026.

## FILES CHANGED

**New:**

- `src/nexa/voice/interruption.py` — `InterruptionStateMachine` +
  `InterruptionState` / `InterruptionEvent` + `DEFAULT_CONFIRM_HOLD_SECS`.
- `src/nexa/voice/aec.py` — `AecReferenceHealth`.
- `src/nexa/voice/bargein.py` — `BargeInController` + `BargeInTelemetry` +
  `InterruptContext`.
- `src/nexa/voice_tts/spoken_text.py` — `SpokenTextTracker`.
- `src/nexa/voice_tts/aec_reference.py` — `AecReferenceFeeder` + `_PcmSink`.
- `tests/test_bargein_m2_5b.py` — 38 deterministic cases.
- `docs/reports/R0029_…md` — this report.

**Changed:**

- `src/nexa/conversation/turn.py` — `ConversationTurn.interrupted` field.
- `src/nexa/conversation/session.py` — `commit_interrupted_turn` +
  `InterruptedTurnOutcome`.
- `src/nexa/conversation/context.py` — `INTERRUPTED_WIRE_SUFFIX`
  deterministic wire annotation.
- `src/nexa/conversation/__init__.py` — export `InterruptedTurnOutcome`.
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
  `response_id`, `interrupt_active_turn()`, `_commit_interrupted`,
  `on_turn_interrupted` / `InterruptedTurn`, `response_id_source` /
  `spoken_prefix_source`.
- `apps/nexa_bilingual_voice_probe.py` — `--bargein` / `--no-bargein` +
  full wiring + live surface.
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

`c1306b6` — this report. Implementation commits: `c3155ca` (barge-in
core), `dcf0df4` (38 deterministic tests + continuity discard), `59141f6`
(probe wiring + AEC status callback). This hash-record edit lands in the
immediately-following commit (R0026/R0027/R0028 pattern). Prior milestone
tip: `2f23613` (M2.5A closure). Not pushed.

## GIT STATUS

Branch `main`, **not pushed**. `git diff --check` clean. Sequence:
`2f23613` (M2.5A closed) → `c3155ca` → `dcf0df4` → `59141f6` → `c1306b6`
(this report). No `src/` change to any frozen component.

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

## NEXT STEP

1. **Operator runs the live acceptance session** (below). On PASS: mark
   M2.5B **OPERATOR-CONFIRMED**, update `CURRENT_STATE.md`, record the
   commit hash.
2. If cases 1/A–F expose a tuning need, adjust `confirm_hold_secs` /
   response-time VAD profile only — no architecture change.
3. Non-blocking, owed independently: the B.3.6 operator latency
   re-confirmation (STT latency + END_OF_TURN → first-audio).

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
