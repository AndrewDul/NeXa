# R0026 — M2.4B.5A: Live Voice Stability / Backlog Investigation

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.5A — live voice
  backlog / false-turn / contention investigation (root cause + fix)**
- **Status:** **OPERATOR-CONFIRMED (2026-09-08) for normal post-fix live
  stability.** The operator ran the post-fix bilingual session: automatic
  PL↔EN switching worked, normal speaking voice transcribed correctly, STT
  stayed ~2.89–3.21 s, **no progressive 7–10 s slowdown, no hang/backlog**,
  max concurrent STT = 1, max concurrent turns = 1, final STT queue depth
  = 0, final conversation queue depth = 0. **TV-stress test: NOT PERFORMED
  / OPERATOR WAIVED** — the deterministic (`tests/test_bilingual_voice_stability.py`)
  + headless (`b5a_contention_probe.py`) busy-drop / contention evidence
  in this report stands as the proof for busy-period behaviour; the
  TV-stress pass is **not** claimed. The few poor transcripts that session
  were the operator speaking too quietly; at normal volume the same
  utterances were understood — **not** an STT regression, no
  SpeechQualityGuard track opened.
- **Related:** `R0025` (M2.4B.5 — the bilingual voice input this session
  exercised), `R0024`, `R0023` (`gemma4:e4b` + `num_thread=2` +
  `keep_alive=30m` — unchanged), `ADR-0003` D8 (barge-in is M2.5, not
  this), the M2.4 `HalfDuplexGate` (`R0011`).
- **Research dir:** `docs/research/m2_4b_bilingual_stt/`
  (`b5a_contention_probe.py` + `b5a_contention_probe_20260908_193640.json`).

---

## TASK RESULT

**PASS.** Root cause found (measured), fixed, deterministic regression
tests + headless contention/leak proof, and **operator-confirmed for
normal post-fix live operation** (2026-09-08 — STT ~2.89–3.21 s, no
slowdown/hang, queue depths 0). **TV-stress test NOT PERFORMED / operator
waived** — the deterministic + headless busy-drop evidence below is the
proof for busy-period behaviour; a TV-stress *pass* is not claimed.

There **was a bug** — a real defect, present since M2.4, exposed by the
first B.5 live session because that session had TV audio in the room while
NeXa was answering. It is **not** a B.5-specific regression, but B.5 made
its blast radius worse (the added ctypes LID pass ≈ +1.2 s of CPU-bound
work per backlogged utterance).

**Root cause (measured, not inferred):** the `HalfDuplexGate` only closes
the microphone once NeXa's TTS *audio is physically playing*. It leaves
the mic **open** through the whole *think → LLM generate → planner → TTS
synthesize* window — **~10–25 s on `gemma4:e4b`**. During that window
external speech (TV) passes the gate, is captured on `END_OF_TURN`, and is
submitted **unconditionally** to `SerialTranscriptionQueue`. Each such
utterance becomes a real STT job (ctypes LID encoder pass + `whisper-cli`
decode), then a real `SerialConversationQueue` turn, then a real
`ConversationSession.send()` + TTS. Under the CPU load of the LLM already
generating + Piper synthesising, each STT job is **starved ~4×** (measured
below: total 3.2 s → 13.2 s), so NeXa answers the TV, which keeps the CPU
pegged, which slows the next STT — the "hang" the operator saw. The
queues are bounded (8 STT / 4 conversation, overflow raises explicitly,
never silent) so it does not grow forever, but 8 + 4 stale items ground
through at ~4× latency is minutes of unresponsiveness.

**Fix:** the strict pre-M2.5 half-duplex rule the task specifies — once a
real user turn is **dispatched**, no new utterance may become STT or
conversation work until the reply is **fully finished** (generation +
playback). Three coordinated, defense-in-depth changes; a busy-period
utterance is **dropped explicitly** with `DROP_BUSY_RESPONSE_IN_FLIGHT`
telemetry, never queued, never replayed. History is untouched. This is
**not** barge-in.

## LIVE FAILURE EVIDENCE

From the B.5 operator session (R0025 acceptance run):

- Start was healthy — STT total **2.71–3.15 s** (PL `Co to jest czarna
  dziura.` detect ~1.21 / decode ~1.68 / total ~2.88; EN `Okay, tell me
  more about it.` ~2.83). PL→EN→PL switching worked.
- After the mic picked up TV / unintended audio **while an earlier
  response was still being processed**, and multiple
  `USER_SPEAKING` / `END_OF_TURN` / `LISTENING` events occurred, latency
  deteriorated:
  - detect 2.59 + decode 6.79 = **9.38 s**
  - detect 2.50 + decode 8.15 = **10.64 s**
  - detect 2.48 + decode 4.59 = **7.07 s**
  - detect 3.76 + decode 6.66 = **10.42 s**
- `VADController: "no audio received while speaking, forcing speech stop"`
  — VAD had started a speech segment (mic open), then mic frames stopped
  arriving mid-utterance (a `BotStartedSpeakingFrame` finally closed the
  gate), so VAD force-emitted a *truncated* stop → a partial/garbled
  transcript ("Co to jest, ta noba wola.", stray TV → "for your own good
  guys.").
- Operator: NeXa began to "hang", became very slow.

## ROOT CAUSE

**A. Turns admitted while a previous response is in flight — YES (primary).**
`nexa.voice.runtime._UtteranceCaptureFrameProcessor.process_frame` calls
`self._queue.submit(audio, language)` on **every** `VADUserStoppedSpeakingFrame`
with no check for an in-flight response. `nexa.voice_conversation.
VoiceConversationAdapter.handle_transcription` calls `self._queue.submit(result)`
with no such check either.

**B. `SerialTranscriptionQueue` backlog grows — YES**, up to its bound (8),
because of A. Not unbounded (9th raises `SttQueueOverflowError`), but 8
stale `whisper-cli` decodes at ~4× latency = ~40–80 s of wasted work.

**C. `SerialConversationQueue` backlog grows — YES**, up to its bound (4):
each backlogged STT result becomes a queued `ConversationSession.send()`,
so NeXa generates + speaks replies to TV noise, extending the CPU-pegged
window.

**D. Multiple `whisper-cli` subprocesses overlap — NO.**
`SerialTranscriptionQueue` guarantees **max 1** `transcribe()` in flight
(verified: `max_observed_concurrency == 1`; deterministic test
`state["max"] == 1` under a 40-submit flood; the contention probe measured
`max_whisper_procs == 1` in **both** phases). `subprocess.run(timeout=…)`
kills + reaps on timeout; the error path is tested not to wedge the queue.

**E. `WhisperCppLanguageDetector` executor jobs overlap — NO.** One
resident `libwhisper` context, guarded by a lock, called via
`run_in_executor` which is awaited before the next `transcribe()`; the STT
queue serialises anyway.

**F. LLM + Piper + Whisper CPU contention causes the slowdown — YES
(measured).** See CPU/RAM/THERMAL FINDINGS: STT total mean **3.19 s →
13.16 s** (+9.97 s, ~4.1×) with `gemma4:e4b` generating + Piper
synthesising, CPU 89 % → 99.4 %.

**G. Stale audio processed seconds later — YES**, a direct consequence of
A–C: a TV utterance captured at t=0 could be decoded + answered at
t=40 s+.

**H. `HalfDuplexGate` mis-wired in the bilingual probe — NO.** It is wired
**identically** to the canonical M2.4 `apps/nexa_voice_tts_probe.py`:
one shared `_gate` given to both `AssistantSpeechBridge(gate=_gate)` and
`VoiceRuntime(half_duplex_gate=_gate)`, and `on_user_transcript` /
`on_assistant_complete` chained to `bridge.on_user_transcript` /
`bridge.on_assistant_complete` (which call `notify_response_dispatched` /
`notify_response_finished`). Verified by diffing the two probes.

**I. The gate leaves an unsafe gap between "transcript accepted" and
"first assistant audio" — YES. This is the bug.** `gate.py`'s own
docstring documents the intent: *"`notify_response_dispatched`… Does not
by itself close the mic — capture stays open through the think/generation
window until real audio plays."* That window is ~10–25 s on `gemma4:e4b`
and is exactly where the TV audio got in.

**J. Process / FD / memory leak over a long session — NO.**
`WhisperCppLanguageDetector` RSS over **200** back-to-back `detect()`
calls: **152.9 MB → ~211 MB** (one-time ~58 MB working set on first use)
then **flat** (210.4 / 212.1 / 211.1 MB at 100 / 150 / 200 calls);
`close()` → 59.3 MB. No per-turn context leak, no accumulating native
allocations. `whisper-cli` is `subprocess.run` (always reaped).

**Verdict: K — a combination, driven by I.** The gate gap (I) admits
busy-period turns (A) → bounded backlog (B, C) → each job starved by
contention (F) → stale replies (G) → snowball. D, E, H, J are **not**
contributing.

## HALF-DUPLEX WIRING

`apps/nexa_bilingual_voice_probe.py` vs `apps/nexa_voice_tts_probe.py`:
the half-duplex wiring is equivalent — same `HalfDuplexGate` instance
shared between `AssistantSpeechBridge` and `VoiceRuntime`, same callback
chain into `notify_response_dispatched` / `notify_response_finished`, same
`_MicGateFrameProcessor` position (right after `transport.input()`, before
VAD). **No bypass, no mis-order.** The defect is in the gate's *contract*
(and the two submit sites that don't consult it), not in the probe.

**Fix to the contract (`nexa.voice.gate.HalfDuplexGate`):**

- New `response_in_flight` property: `True` from `notify_response_dispatched()`
  until the reply is fully done — generation complete **and**, if it
  produced audio, `BotStoppedSpeakingFrame` seen. Covers the
  think/generate/synthesize/play window as one unit.
- `mic_suppressed` now returns `self._bot_speaking or self.response_in_flight`
  (a superset of the old behaviour — the self-echo protection is
  unchanged; the generation window is now also closed).
- Inter-sentence-gap and never-latch-shut behaviour preserved (tests).

## QUEUE FINDINGS

| queue | bound | behaviour before fix | after fix |
|---|---|---|---|
| `SerialTranscriptionQueue` | 8, overflow → `SttQueueOverflowError` (explicit) | backlogs to 8 from busy-period TV audio | **0** — busy-period utterances dropped at capture, never submitted |
| `SerialConversationQueue` | 4, overflow → `ConversationQueueOverflowError` (explicit) | backlogs to 4 (one LLM turn per stale STT result) | **0** — busy-period STT results dropped at the adapter, never submitted |

Neither queue was ever *unbounded*; the harm was the bounded backlog
(8 + 4) processed serially under ~4× contention. Deterministic tests:
a 30-utterance flood at the capture processor and a 15/20-result flood at
the adapter, both while a response is in flight, leave **queue depth 0**
and the drop counters at 30 / 15 / 20, and only the one real turn reaches
`ConversationSession`.

## WHISPER PROCESS FINDINGS

- **Max concurrent `whisper-cli` = 1** — guaranteed by
  `SerialTranscriptionQueue` (proof, not inference:
  `max_observed_concurrency`; deterministic 40-submit flood test;
  `b5a_contention_probe` `max_whisper_procs == 1` in both phases).
- **Always reaped** — `subprocess.run(..., timeout=self.config.timeout_s)`
  kills the child on `TimeoutExpired` and always waits. No `Popen` without
  a wait anywhere in the STT path.
- **Timeout / error cannot orphan** — `_run_sync` raises
  `SttTimeoutError` / `SttSubprocessError`; the queue's `_run` catches,
  reports via `on_error`, and continues (test:
  `test_transcriber_error_does_not_wedge_the_queue`).
- **Executor tasks cannot accumulate** — LID `run_in_executor` is awaited;
  the STT queue serialises `transcribe()`.
- **Cancellation** — `SerialTranscriptionQueue.shutdown()` drains the
  current job then exits; `CancelToken` is per-request.

## CPU/RAM/THERMAL FINDINGS

`docs/research/m2_4b_bilingual_stt/b5a_contention_probe.py` — the real
bilingual STT path (ctypes LID + `whisper-cli` decode) over 6 corpus
clips, twice:

| | **A · ISOLATED** (LLM idle, Piper idle) | **B · CONTENDED** (`gemma4:e4b` generating, num_thread=2 + Piper `nice +10` loop) |
|---|---|---|
| STT **detect** mean | 1.30 s | **4.17 s** |
| STT **decode** mean | 1.89 s | **8.99 s** |
| STT **total** mean | **3.19 s** | **13.16 s**  (**+9.97 s, ~4.1×**) |
| STT total max | 3.63 s | 14.33 s |
| CPU total mean | 89 % | **99.4 %** (saturated) |
| dominant CPU consumer | (STT itself) | **`llama-server`** (pegged; `piper` also active — its process name is `python3`, so the probe's `pgrep -x piper` under-reports it) |
| max `whisper-cli` procs | **1** | **1** |
| MemAvailable min | 3663 MB | 3246 MB |
| temp max | 65.5 °C | 68.3 °C |
| throttled | `0x0` | `0x0` (temp fine) |

A clean quiet-Pi PL↔EN sequence (post-fix build) measured **2.81 s mean
(2.72–2.91)** — the fix does not touch the clean path.

**This is the "why 7–10 s": pure CPU contention.** `gemma4:e4b`
generation (llama-server) + Piper synthesis saturate the 4-core Pi; the
CPU-bound STT (LID encoder pass + whisper decode) is starved ~4×.
`num_thread=2` limits *the LLM's decode threads*, not llama-server's total
footprint, and Piper's `nice +10` only helps versus the LLM, not versus a
third contender. Nothing is throttled or memory-bound — it is scheduler
starvation.

## TV/FALSE-TURN FINDINGS

TV audio is external speech; language ID / speaker ID cannot tell it from
the operator, and this stage does **not** attempt to. The required
protection is scoped exactly as the task specifies:

- **While NeXa is BUSY / responding:** TV / any audio **cannot** create an
  STT or conversation turn — dropped at capture and/or at the adapter with
  `DROP_BUSY_RESPONSE_IN_FLIGHT`. (Implemented + tested.)
- **While NeXa is idle:** TV can still trigger VAD in this acceptance
  harness and be transcribed / answered. This is recorded as a **separate
  future attention / speaker-attribution problem** — no wake word, no
  speaker-ID system is added here.

## FIX IMPLEMENTED

1. **`nexa.voice.gate.HalfDuplexGate`** — `response_in_flight` +
   `mic_suppressed` now covers the whole response (above).
2. **`nexa.voice.runtime._UtteranceCaptureFrameProcessor`** — takes the
   `HalfDuplexGate` and an `on_utterance_dropped` callback. On
   `VADUserStoppedSpeakingFrame`, if `gate.response_in_flight` → build a
   `DroppedUtterance(reason=DROP_BUSY_RESPONSE_IN_FLIGHT, at, at_wall,
   audio_ms, stt_queue_depth, dropped_count_this_session)`, `logger.info`
   it, fire the callback, reset the buffer — **do not submit**. New
   `stt_queue_depth` / `dropped_busy_utterances` properties.
   `VoiceRuntime` gets an `on_utterance_dropped` param + `stt_queue_depth`
   / `dropped_busy_utterances` properties, and now passes the gate to the
   capture processor.
3. **`nexa.voice_conversation.VoiceConversationAdapter`** — `_turn_in_flight`
   (set the instant `_run_turn` starts, cleared in a `finally` after
   generation completes) + `on_turn_dropped` callback. `handle_transcription`
   drops (with a `DroppedTurn` record) any STT result that arrives while
   `_turn_in_flight` — never enqueued. New `conversation_queue_depth` /
   `turn_in_flight` / `dropped_busy_turns` properties.
4. **`apps/nexa_bilingual_voice_probe.py`** — wires both drop callbacks to
   print the drop live, and prints the busy-drop / queue-depth summary at
   shutdown.

Layering: the gate (`response_in_flight`) withholds mic frames for the
whole response, so VAD normally never even produces an event during a
reply. The capture-processor drop is the belt for the transition edge
(mic gate opening/closing mid-utterance, incl. the VAD "forcing speech
stop" case). The adapter drop is the belt for an STT job that was already
running when the previous turn dispatched. TTS-playback self-echo is still
covered by the pre-existing `_bot_speaking` path.

## STALE-TURN POLICY

**Explicit, telemetered, no silent drop, history untouched.**

- An utterance captured while `response_in_flight` is **dropped at
  capture** — reason `DROP_BUSY_RESPONSE_IN_FLIGHT`, with
  `time.monotonic()` + ISO-8601 UTC timestamp, `audio_ms`,
  `stt_queue_depth`, and a session running count. It is **never** enqueued
  for later.
- An STT result that arrives while `_turn_in_flight` is **dropped at the
  adapter** — same reason, with the transcript (truncated in the log),
  `input_speech_language`, `conversation_queue_depth`, session count.
- `ConversationSession` history is **not** modified for a dropped turn —
  no `ConversationTurn` is created, `_response_languages` is untouched.
- The pre-M2.5 rule is deliberate: a second user utterance during
  assistant processing is **not** treated as intentional interruption —
  barge-in is M2.5 (ADR-0003 D8).

## POST-FIX PI RESULTS

- **A. Clean PL → EN → PL → EN** (quiet Pi, post-fix bilingual STT path):
  total **2.81 s mean (2.72–2.91 s)** — within the established ~2.8–3.1 s
  range. Language alternates correctly. **No regression from the fix**
  (the drop only fires while a response is in flight, which never happens
  between clean sequential turns).
- **B. Busy-period flood (deterministic, `tests/test_bilingual_voice_stability.py`):**
  30 "TV" utterances at the capture processor + 15–20 STT results at the
  adapter, all while a response is in flight → **STT queue depth 0**,
  **conversation queue depth 0**, drop counters at 30 / 15–20, **only the
  one real turn** reaches `ConversationSession`, history has exactly the
  one user + one assistant turn, no stale replay after the response.
  Input re-opens for the next real utterance.
- **Contention still exists** (it is physics — 4 cores, 3 CPU-bound
  consumers): if a real user turn *and* a genuine second real turn
  arrived back-to-back the second's STT would still slow under the first's
  LLM+TTS load. But that second turn is now **dropped** (pre-M2.5
  half-duplex), so the operator never experiences it; and TV can no longer
  manufacture that load.

**The live spoken TV-stress test was operator-waived.** The operator ran
the normal post-fix session (clean PL↔EN, no TV) and accepted it as
sufficient evidence for normal operation (STT ~2.89–3.21 s, no
slowdown/hang, max concurrent STT/turns = 1, queue depths 0). The
busy-period drop behaviour is proven deterministically + headlessly (this
report); a TV-stress *pass* is not claimed and is not required.

## REGRESSION TESTS

`tests/test_bilingual_voice_stability.py` — **14 new**, offline:

- gate: dispatch closes the mic before any audio; reopens only after
  generation **and** playback both done; no-audio reply still reopens;
  hard stop never latches shut.
- capture processor: an utterance captured during a response is dropped
  with `DroppedUtterance` telemetry and **never transcribed**; a 30-flood
  builds **no STT backlog** (`stt_queue_depth == 0`); an utterance
  *before* a response transcribes normally; input re-opens after the
  response finishes.
- whisper safety: **max one decode at a time** under a 40-submit flood
  (`state["max"] == 1`, `queue_size == 0`, overflow raised not silent); a
  transcriber error does not wedge the queue.
- adapter: a busy-period 15-result burst → `conversation_queue_depth ==
  0`, `dropped_busy_turns == 15`, only the real turn runs, dropped text
  never in history; **normal sequential PL/EN unchanged** (no drops, full
  history); one session / one provider, no second authority.

Updated (behaviour this stage deliberately changes):

- `tests/test_voice_half_duplex_gate.py` — `dispatch_alone_keeps_mic_open`
  → `dispatch_closes_the_mic_for_the_whole_response`; the multi-sentence
  re-arm test asserts the mic is closed from dispatch.
- `tests/test_voice_conversation_adapter.py` —
  `second_transcription_can_arrive_while_first_turn_is_active` →
  `…_is_dropped` (asserts the drop + telemetry + history intact);
  `bounded_overflow_raises_…` reframed to the synchronous-burst case that
  still overflows; added `next_real_turn_admitted_after_response_finishes`
  and `busy_period_burst_produces_no_conversation_queue_backlog`.

Suite (at this stage's commit): **`pytest` 569 passed / 7 skipped / 14
subtests**; **`unittest` 576 OK**; `ruff` clean; `git diff --check`
clean. (After R0027: 587 / 594.)

## UNRESOLVED

- **Live spoken TV-stress test** — NOT PERFORMED / operator waived. The
  deterministic + headless busy-drop evidence is accepted as sufficient;
  the harness remains available if a future session wants to exercise it.
- **Idle-period TV** — while NeXa is *not* answering, TV can still trigger
  VAD → a turn. Out of scope here (no wake word / speaker-ID this stage);
  recorded as a future *attention / speaker-attribution* track.
- **Low-volume speech** — recognition quality can degrade when the
  operator speaks too quietly; normal speaking volume was accepted by the
  operator. **Not** a new STT-quality task, **not** a SpeechQualityGuard
  track (per operator instruction).
- **Low-confidence / garbage transcripts** ("Co to jest, ta noba wola.",
  "for your own good guys.") — the operator clarified these came from
  speaking too quietly; at normal volume the same utterances were
  understood. **No SpeechQualityGuard track is opened.** For the record
  only: whisper.cpp does expose `no_speech_prob` / per-segment
  `avg_logprob` via `whisper-cli --output-json-full` or the library, if a
  future stage ever needs a `SpeechQualityGuard` / `TurnAdmission`
  responsibility (kept distinct from `LanguageIdGuard`) — not now.
- **A hung LLM stream** (no tokens, no error, no completion) would keep
  `_turn_in_flight` / `response_in_flight` latched until the provider's
  600 s timeout. Acceptable for now; a shorter in-flight watchdog is a
  possible later hardening.

## FILES CHANGED

- `src/nexa/voice/gate.py` — `response_in_flight`; `mic_suppressed` covers
  it; docstrings.
- `src/nexa/voice/runtime.py` — `DroppedUtterance` +
  `DROP_BUSY_RESPONSE_IN_FLIGHT`; `_UtteranceCaptureFrameProcessor` gate +
  drop; `VoiceRuntime` `on_utterance_dropped` + `stt_queue_depth` +
  `dropped_busy_utterances`.
- `src/nexa/voice/__init__.py` — exports.
- `src/nexa/voice_conversation/adapter.py` — `DroppedTurn` +
  `DROP_BUSY_RESPONSE_IN_FLIGHT`; `_turn_in_flight` guard;
  `_run_turn`/`_run_turn_inner` split; `on_turn_dropped`;
  `conversation_queue_depth` / `turn_in_flight` / `dropped_busy_turns`.
- `src/nexa/voice_conversation/__init__.py` — exports.
- `apps/nexa_bilingual_voice_probe.py` — drop callbacks printed live +
  shutdown summary.
- `tests/test_bilingual_voice_stability.py` (new, 14);
  `tests/test_voice_half_duplex_gate.py`,
  `tests/test_voice_conversation_adapter.py` (updated for the intended
  behaviour change).
- `docs/research/m2_4b_bilingual_stt/b5a_contention_probe.py` (+ JSON);
  `docs/reports/R0026_…md` (this); `docs/CURRENT_STATE.md`.

**Unchanged:** `gemma4:e4b`, `num_thread=2`, `keep_alive=30m`, warm-up,
`ResponseMode.VOICE`, `ResponseLanguageResolver` semantics, Piper
voices/speed, `SpeechPlanner`, continuity controller, whisper model,
`LanguageIdGuard` thresholds, one `ConversationSession` / provider /
model. No `nexa.config` / `nexa.bootstrap` change. M2.5 not started.

## COMMIT HASH

`20660ce` — `fix: strict pre-M2.5 half-duplex — no turn while a response is in flight (M2.4B.5A / R0026)` (tip of `main`). Not pushed. (This hash-record edit lands in the next commit.)

## GIT STATUS

Branch `main`, not pushed. `ruff` clean for this stage's scope;
`git diff --check` clean.

## NEXT STEP  *(closed)*

**Operator-confirmed for normal post-fix operation** (2026-09-08). The
TV-stress test was operator-waived; the deterministic + headless
busy-drop / contention evidence in this report is accepted.
**M2.4B.5A → OPERATOR-CONFIRMED.** The response-language one-turn-vs-sticky
correction is **M2.4B.5B / R0027**. Then **M2.5 — barge-in / interruption**
(replaces the temporary half-duplex gate). Not started here.
