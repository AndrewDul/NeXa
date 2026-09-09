# R0028 — M2.5A: Barge-In / Interruption Architecture & Real-Hardware Feasibility

- **Date:** 2026-09-09
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.5A — barge-in /
  interruption architecture, capability audit, AEC / self-echo
  feasibility, interruption-semantics design, isolated hardware spikes.**
- **Status:** **RESEARCH COMPLETE; one confirmatory latency spike still
  OPEN.** No production behaviour changed. The half-duplex `HalfDuplexGate`
  (R0026) is untouched and still in force. Deterministic tests added for
  the spike tooling. **SPIKE B-live was run once by the operator on
  2026-09-09 (`spike_bargein_live_20260909_134647.json`). Its detection
  result — near-field operator speech IS picked up by the reSpeaker +
  Silero VAD — is kept; its latency figure (`mean=5.168 s`) is INVALID and
  is withdrawn** (instrumentation faults: no arming window, playback never
  actually ran, VAD→stop never measured — see *SPIKE B-live — FIRST RUN
  (2026-09-09): WHY THE LATENCY IS INVALID*). `spike_bargein_live.py` has
  been rewritten (v2); the operator must re-run it once. The exact
  one-line command is in *OPERATOR ACTION REQUIRED* below. Not pushed.
- **Related:** `R0026` (the half-duplex fix this milestone replaces),
  `R0027` (one-turn vs sticky response language — must survive barge-in),
  `R0024`/`R0025` (bilingual STT), `ADR-0003` D8 ("barge-in is M2.5"),
  `R0009` (KV-cache prefix discipline).

---

## SPIKE B-live — FIRST RUN (2026-09-09): WHY THE LATENCY IS INVALID

The operator ran `spike_bargein_live.py` once on 2026-09-09
(`spike_bargein_live_20260909_134647.json`, `VERDICT: detected 5/5;
onset-after-prompt mean=5.168 s median=5.095 s max=11.606 s`). Raw-run
inspection shows the latency number is not usable.

**What the first run DID prove — kept as M2.5A evidence:**

- Near-field operator speech **is detectable by the current reSpeaker
  XVF3800 + Silero VAD** — a valid `USER_SPEAKING` transition fired in
  every one of the 5 trials. This is consistent with the SPIKE A / headless
  SPIKE B finding that only the *speaker→mic* self-echo path is below the
  VAD floor; a live near-field voice is a much stronger signal and is seen.

**Why `5.168 s` is INVALID / INCONCLUSIVE and is withdrawn:**

1. **No arming window.** The harness accepted the *first-ever*
   `USER_SPEAKING` transition. Trial 5 latched a VAD event that fired
   ~2.6 s into the 3 s pre-playback warm-up (`rel_play_s: null`,
   `onset_after_prompt_s: -2.359`) — before the prompt was printed, so it
   cannot be an operator response. Trials 1 and 2 each contained multiple
   start/stop cycles with no rule for which one counts.
2. **Playback almost certainly never ran.** The spike played NeXa's speech
   with a *second* `aplay` on `plug:usb_speaker`, a non-mixing raw-hw ALSA
   device (`hw:CARD=UACDemoV10,DEV=0`, no `dmix`; `/etc/asound.conf`).
   The old harness runs a full `VoiceRuntime`, whose audio-output transport
   (`audio_out_enabled=True`, hard-wired in `_build_pipeline`) already holds
   that device. A concurrent open returns **`Device or resource busy`** and
   `aplay` exits in milliseconds — verified directly on this Pi. `aplay`'s
   `stderr` was routed to `DEVNULL`, so the failure was silent. **So NeXa
   produced no sound during the trials**, and "onset-after-prompt" is the
   operator waiting against silence.
3. **`playback killed None` every trial — root cause.** Because `aplay`
   had already exited, `p.poll()` returned its exit code at every VAD
   onset, so the `p.poll() is None` guard was false, `p.kill()` never ran,
   `killed["t"]` stayed `None`, and `playback_stop_after_onset_s` printed
   as `None` (`"Nones"`). Trial 5 had a second cause: its accepted event
   fired before `subprocess.Popen` was even called (`proc_box["p"]` was
   `None`). **The critical VAD-onset → playback-stopped latency was never
   measured.**
4. **`prompt → VAD` includes human reaction time.** Even with everything
   else fixed, that metric bundles the operator's reaction to the prompt.
   It is logged for reference; it is **not** system barge-in latency.

**Trial-5 pre-prompt VAD — finding:** the event fired during the 3 s
pipeline warm-up sleep, *before* playback start and *before* `Popen`. The
data cannot further separate operator-speech-before-prompt from
room-noise / a Silero startup transient. The v2 harness makes this moot —
it counts pre-arm VAD starts (`prearm_vad_count`), discards them, and only
ever measures a `VADUserStartedSpeakingFrame` at/after `trial_armed`. A
pre-arm event now flags the trial (`prearm_contaminated`) instead of
silently becoming the measurement.

**Instrumentation repair (v2 — `spike_bargein_live.py`, no `src/` change):**

- Builds a **VAD-only** Pipeline from the real production components
  (`SileroVADAnalyzer` + `VADProcessor` + `DEFAULT_VAD_PARAMS`) with
  `audio_out_enabled=False`, so this harness does **not** hold
  `usb_speaker` and the trial `aplay` can actually open and drive it.
  `VoiceRuntime` is not used because it cannot be told to release the
  speaker. No production module is imported for mutation; nothing in `src/`
  changes.
- `aplay` runs with `stderr=PIPE`; each trial confirms playback is alive
  (`poll()` after a 1.5 s lead-in) **before** arming and reports a
  device-busy failure loudly (`playback_started: false` + captured stderr)
  instead of proceeding silently.
- Explicit per-trial window (A–J): start playback → confirm active →
  discard & count pre-arm VAD → print `>>> SPEAK NOW <<<` → arm → accept
  only the first post-arm `VADUserStartedSpeakingFrame` → record
  `playback_stop_requested` and `proc.kill()` → `proc.wait()` → record
  `playback_actually_stopped` → count later starts (`post_accept_vad_count`).
- Timestamps recorded: `playback_started`, `prompt_printed`, `trial_armed`,
  `vad_user_started`, `playback_stop_requested`, `playback_actually_stopped`.
- Metrics derived:
  - `prompt_to_vad_s` — operator reaction + VAD — **INFORMATIONAL ONLY**.
  - `vad_to_stop_request_ms` — **system control-plane latency**
    (VAD start → stop requested).
  - `vad_to_playback_stopped_ms` — **media-stop latency**, labelled
    **PLAYBACK TASK STOPPED** (the `aplay` process is reaped). This is
    **not** the last physical speaker sample — this isolated spike has no
    hardware-drain instrumentation and does not claim that precision.
- No pass/fail threshold is baked in; the corrected numbers are to be read
  first. Production target is unchanged: **< 1 s** from real user speech
  onset to old audio stopped; the directly measurable control-plane metric
  here (`VADUserStartedSpeakingFrame → playback stopped`) should be well
  under that.
- Pure metric derivation (`derive_trial_metrics`, `_agg`) is unit-tested
  offline (`tests/test_bargein_spike.py`, +6): none-not-zero on
  no-detection, control latency still reported when playback failed,
  pre-arm events flag contamination without changing the measured event,
  negative `prompt_to_vad_s` reported not clamped.

**M2.5A status:** open only for the corrected latency confirmation.
**M2.5B has not started.**

---

## RETURN SUMMARY

- **TASK RESULT:** PASS (research). Architecture, capability audit and
  feasibility evidence are in hand; a production design for M2.5B is
  recommended below; one operator spike is prepared and pending.
- **IS M2.4B NOW COMPLETE? — YES.** Every sub-stage B.1…B.5B landed;
  M2.4B.5 and M2.4B.5A are OPERATOR-CONFIRMED for normal bilingual PL/EN
  live voice operation (2026-09-08); M2.4B.5B (R0027) corrected the
  response-language semantics with a green suite. The only open item is a
  **B.3.6 operator latency re-confirmation** (STT latency +
  END_OF_TURN→first-audio) which R0023 already measured on the bench and
  which the milestone plan marks **non-blocking**; it does not gate M2.5.
  `CURRENT_STATE.md` has been reconciled (see *CURRENT_STATE CONSISTENCY
  FIX*). **The active voice milestone is now M2.5 — barge-in /
  interruption**, opening with this M2.5A spike.
- **PIPECAT BARGE-IN SUPPORT FOUND:** Strong, first-class — but NeXa does
  not currently wire it. `InterruptionFrame` (a `SystemFrame`, travels
  out-of-band), `FrameProcessor.broadcast_interruption()` →
  `broadcast_frame(InterruptionFrame)` both directions, every
  `FrameProcessor._start_interruption()` on receipt, and
  `base_output` `handle_interruptions()` which **cancels and recreates the
  audio task = drops all queued and currently-playing PCM**. The
  canonical VAD→interruption bridge in Pipecat 1.8.1 is the
  `LLMResponseAggregator` / `UserTurn` path (`llm_response_universal.py`
  calls `broadcast_interruption()` on `UserStartedSpeakingFrame`). **NeXa
  uses none of it** — it runs a raw `VADProcessor` plus custom
  processors, so today nothing in NeXa ever emits an `InterruptionFrame`.
  The `InterruptionFrame` handlers already present in
  `NexaSpeechPlanner` and `NexaSpeechContinuityController` are dead code
  waiting for a producer.
- **AEC / SELF-ECHO RESULT:** The reSpeaker XVF3800 substantially rejects
  speaker-reproduced audio at the mic **even with no AEC reference fed**.
  Three self-echo spike runs (`spike_self_echo.py`): NeXa's own Piper
  voice played through the live `plug:usb_speaker` route produced **0
  false `USER_SPEAKING` events** during playback, with or without the
  XVF3800 USB playback endpoint driven as an AEC far-end reference, and
  with the Jieli DAC PCM at max. The headless interruption spike
  (`spike_interruption_latency.py`) then played a **real recorded human
  utterance** through the same speaker, mixed over the TTS, at +0 dB and
  again at +9.5 dB: **0 / 8 detections.** Conclusion: at conversational
  levels the current hardware path from `plug:usb_speaker` → reSpeaker
  mic → Silero VAD (`min_volume=0.6`, `confidence=0.7`) is **below the VAD
  floor** — good for self-echo safety, but it also means a headless,
  speaker-played "interruption" cannot be measured, because it is the
  exact signal path the array suppresses. A live near-field operator
  voice is a different, much stronger signal and must be measured
  separately (SPIKE B-live).
- **CAN WE KEEP THE MIC OPEN SAFELY WHILE NEXA SPEAKS? — YES, with a
  guarded trigger.** The self-echo evidence says NeXa's own TTS will not
  by itself trip the VAD on this hardware, so the R0026 whole-response mic
  close can be replaced by *keeping the raw mic hot during a response* and
  admitting **at most one** interruption candidate through an explicit
  state machine (below). The backlog bug is prevented not by closing the
  mic but by the state machine's single-candidate invariant plus the
  retained `SerialTranscriptionQueue` (max 1 concurrent) and
  `SerialConversationQueue` (max 1 in flight).
- **INTERRUPTION TRIGGER RECOMMENDATION:** Trigger on **sustained VAD
  speech while `response_in_flight` is true** — the first
  `VADUserStartedSpeakingFrame` starts an *interruption candidate*; the
  candidate is *confirmed* only if speech persists ≥ **300 ms**
  (`start_secs=0.2` of VAD onset + a ~100 ms guard) without a
  `VADUserStoppedSpeakingFrame`. Confirmation immediately issues
  `broadcast_interruption()` — it does **not** wait for STT. Full
  transcription of the interrupting utterance runs afterwards, off the
  same captured audio, and becomes the next user turn. Detection and
  transcription are separate stages.
- **LLM CANCELLATION RESULT:** Effective backend cancellation exists and
  was measured. `session.send(..., cancel_token=…)` →
  `provider.generate(..., cancel_token=…)`; the Ollama worker thread
  checks `cancel_token.is_cancelled` per streamed line and `return`s,
  which exits the `urlopen` context and drops the TCP connection. On a
  hard client disconnect, llama-server CPU spiked ~102 jiffies in the
  first second then went flat — **generation stops within ~1 s, the model
  stays resident**. **Gap:** the voice adapter never creates or passes a
  `CancelToken` today, and simply closing the async generator (without
  `cancel_token.cancel()`) does **not** stop the worker thread — it keeps
  draining Ollama into a queue nobody reads. M2.5B must create a per-turn
  `CancelToken`, thread it through `_run_turn_inner` → `session.send`, and
  call `.cancel()` on interruption.
- **TTS CANCELLATION RESULT:** Pipecat's `PiperHttpTTSService`
  `_handle_interruption()` is comprehensive (clears the text aggregator,
  filters, the aggregated-frame sequencer, pending
  `LLMFullResponseEndFrame`s, word timestamps, stops and recreates the
  audio-context task, resets the serialization queue). `NexaSpeechPlanner`
  and `NexaSpeechContinuityController` already `_reset()` on
  `InterruptionFrame`. **One real gap:** `AssistantSpeechBridge` does
  **not** handle `InterruptionFrame`; its internal `asyncio.Queue` of
  `LLMTextFrame`s would leak stale tokens of the interrupted response into
  the next one. M2.5B must add an `InterruptionFrame` branch to
  `AssistantSpeechBridge.process_frame` that drains `self._queue` and
  drops the in-flight `on_assistant_*` sequence.
- **INTERRUPTED HISTORY RECOMMENDATION: Option C** — on a confirmed
  interruption, commit the assistant turn with **exactly the text that was
  actually flushed to TTS at the interruption instant**, tagged
  `interrupted=True`; never commit the unspoken remainder, never leave the
  user turn orphaned, never spawn a voice-only history. Mechanics below.
- **QUEUE-SAFETY RECOMMENDATION:** Keep both serial queues and their
  bounds. Add an `InterruptionState` machine that admits **at most one**
  interruption candidate at a time and only promotes it to a real user
  turn after the old turn's cancellation has completed
  (`cancel_token.cancelled` acknowledged, planner/bridge flushed, audio
  task recreated). The old `DROP_BUSY_RESPONSE_IN_FLIGHT` drop path stays
  as the fallback for utterances that arrive while an interruption is
  already being processed.
- **THINK-WINDOW INTERRUPTION RECOMMENDATION: Option B** — an interruption
  is accepted at any time `response_in_flight` is true, including the
  think / generation / TTS-synthesis window before the first audio frame.
  Rationale: R0026 proved the think window is exactly where the operator
  currently loses control (10–25 s of dead air). The audio-stop actions
  simply become no-ops when nothing is playing yet; the LLM-cancel and
  history-commit actions still apply (with an empty flushed-text →
  Option A/"discard the user turn cleanly", see below).
- **MEASURED INTERRUPTION LATENCY:** **Not measurable headless on this
  hardware.** `spike_self_echo.py` (3 runs) and
  `spike_interruption_latency.py` (2 runs, 8 plays) both show the
  speaker→mic path is below the VAD floor, so a speaker-played
  "interruption" yields 0 detections and no latency. The dominant term
  that *can* be reasoned about: Silero VAD `start_secs=0.2` + our ~100 ms
  confirmation guard ≈ **~300 ms speech-onset → interruption confirmed**,
  plus a bounded audio-stop tail of one output chunk (~20–40 ms) +
  `cancel_task` latency for `handle_interruptions()`. **Target:
  < 1 s** speech-onset → last old audio frame. Whether the live hardware
  meets it is **still unproven**: SPIKE B-live's first run (2026-09-09)
  proved near-field operator speech IS detected during a response (5/5) but
  its latency figure (`5.168 s`) is **withdrawn as invalid** — no arming
  window, `aplay` never actually opened the busy `plug:usb_speaker`, and
  VAD-onset → playback-stopped was never measured (see *SPIKE B-live —
  FIRST RUN*). `spike_bargein_live.py` v2 fixes the instrumentation;
  operator re-run pending.
- **RECOMMENDED M2.5B ARCHITECTURE:** see *RECOMMENDED PRODUCTION
  ARCHITECTURE* — a NeXa `BargeInController` frame processor that owns the
  `InterruptionState` machine and `response_id`, emits
  `broadcast_interruption()` on confirmation, drives the per-turn
  `CancelToken`, and coordinates the history commit; Pipecat owns media
  transport + audio-queue cancellation; the one `ConversationSession`
  stays the sole brain / history / model / language authority.
- **OPERATOR ACTION REQUIRED? — YES, one re-run.** SPIKE B-live's
  instrumentation was repaired (v2); the operator re-runs the **single
  command** in *OPERATOR ACTION REQUIRED* (5 trials, one short phrase each
  after `>>> SPEAK NOW <<<`). No TV test.
- **TEST RESULTS:** `tests/test_bargein_spike.py` +16 total (+6 this
  round: `derive_trial_metrics` / `_agg` — none-not-zero on no-detection,
  control latency still reported when playback failed, pre-arm events flag
  contamination without becoming the measurement, negative
  `prompt_to_vad_s` reported not clamped). Full suite:
  **`pytest` 603 passed / 7 skipped / 14 subtests**; **`unittest` 610
  OK / 7 skipped**; **`ruff check src tests apps
  docs/research/m2_5_bargein`** clean; **`git diff --check`** clean.
- **COMMIT HASH:** _pending_ — the SPIKE B-live v2 repair commit is made
  after this edit; its hash is recorded in the immediately-following commit
  (the R0026/R0027 pattern). Prior R0028 tip: `22dc46b`.
- **GIT STATUS:** branch `main`, not pushed.
- **NEXT STEP:** operator re-runs SPIKE B-live v2 (one command below); fold
  the corrected `vad_to_stop_request_ms` / `vad_to_playback_stopped_ms`
  numbers into this report; **then** implement **M2.5B** to the
  architecture below. **M2.5B has not started and does not start before the
  corrected latency evidence exists.**

---

## CURRENT PRE-M2.5 BASELINE

The path a spoken turn takes today (all measured / read this stage):

```
reSpeaker mic (plug:respeaker, XVF3800, 16 kHz)
  → transport.input()
  → _MicGateFrameProcessor        (withholds InputAudioRawFrame while HalfDuplexGate.mic_suppressed)
  → VADProcessor (Silero)         (confidence=0.7 start_secs=0.2 stop_secs=1.0 min_volume=0.6)
  → _UtteranceCaptureFrameProcessor
        on VADUserStoppedSpeakingFrame:
          gate.response_in_flight ? _drop_busy(DROP_BUSY_RESPONSE_IN_FLIGHT) : submit → SerialTranscriptionQueue (max 8, ≤1 concurrent)
  → BilingualSpeechTranscriber    (ctypes LID → LanguageIdGuard → ONE explicit whisper-cli decode, base/q8_0 -t4)
  → VoiceConversationAdapter.handle_transcription
        _turn_in_flight ? _drop_busy(DroppedTurn) : submit → SerialConversationQueue (max 4, ≤1 in flight)
  → _run_turn → _run_turn_inner
        ResponseLanguageResolver.resolve()  (R0027: one-turn vs sticky)
        on_turn_language / on_user_transcript
        async for chunk in ConversationSession.send(text, response_mode=VOICE, response_language=…)   ← NO cancel_token
            on_assistant_token(chunk)  → AssistantSpeechBridge._queue.put_nowait(LLMTextFrame)
        on_assistant_complete("".join(chunks))
  → AssistantSpeechBridge (_queue worker) → NexaSpeechPlanner → NexaSpeechContinuityController → PiperHttpTTSService → transport.output()
```

**HalfDuplexGate (R0026/B.5A):** `notify_response_dispatched()` sets
`_response_generating=True`; `response_in_flight` is true from dispatch
until *generation done AND playback done*; `mic_suppressed = _bot_speaking
or response_in_flight`. So today the mic is **fully closed for the whole
response window**, including the think window. That is the behaviour M2.5A
is chartered to replace *without* reintroducing the R0026 backlog
(8 stale STT + 4 stale turns).

**ConversationSession.send (read this stage):**

```python
self._history.append(ConversationTurn(role=USER, content=user_text))     # (1) user turn committed immediately
self._response_languages.append(response_language)                        # (2) 1:1 list
context = self.build_context(); messages = context.to_provider_messages(...)
raw_stream = self.provider.generate(messages, self.options, cancel_token=cancel_token)
response = StreamingResponse(raw_stream)
async for chunk in response:
    yield chunk
self._history.append(ConversationTurn(role=ASSISTANT, content=response.text))  # (3) assistant turn — ONLY on normal exhaustion
self._response_languages.append(None)                                          # (4) — ditto
```

## CURRENT_STATE CONSISTENCY FIX

`docs/CURRENT_STATE.md` said, in the same file, all of: "M2.4B is NOT
complete", "M2.4B.5 OPERATOR-CONFIRMED", "M2.4B.5A OPERATOR-CONFIRMED for
normal post-fix operation", "M2.4B.5B DONE", and "Current objective =
M2.5". That is internally contradictory. Reconciled truthfully (edit made
this stage, not yet committed):

- **Current milestone** line now reads
  `M2.1, M2.2, M2.3, M2.4, M2.4B COMPLETE, OPERATOR-CONFIRMED; active:
  M2.5 — barge-in / interruption`.
- The M2.4B substage block is retitled
  **"Substage M2.4B — Natural Speech Flow / Streaming Pacing — COMPLETE
  (2026-09-09)"** with an explicit line: **"No M2.4B blocker remains. The
  one outstanding item — a B.3.6 operator latency re-confirmation — is
  explicitly non-blocking and does not gate M2.5."** The full sub-stage
  history (B.1…B.5B) is kept verbatim underneath.
- The old sentence **"M2.4B is NOT complete."** is replaced with
  **"→ M2.4B is COMPLETE (2026-09-09); no blocker remains. The active
  voice milestone is now M2.5 — barge-in / interruption, opening with the
  M2.5A architecture / feasibility spike (R0028)."**
- **Current objective** is now **"M2.5A — barge-in / interruption
  architecture & real-hardware feasibility (R0028) — research +
  architecture audit + Pipecat capability audit + AEC/self-echo
  feasibility + interruption-semantics design + small isolated spikes.
  NOT the production implementation (that is M2.5B). Replaces the
  temporary R0026 whole-response HalfDuplexGate without reintroducing the
  backlog bug."** The B.3.6 latency re-confirmation is listed there as
  *non-blocking, deferred*.

No unfinished work was invented. `gemma4:e4b` / `num_thread=2` /
`keep_alive=30m` / warm-up / `ggml-base-q8_0 -t4` / `LanguageIdGuard`
thresholds / `ResponseLanguageResolver` / Piper / `SpeechPlanner` /
continuity are all recorded as unchanged.

## PIPECAT INTERRUPTION CAPABILITIES

Read from the installed Pipecat 1.8.1 tree:

| Mechanism | Location | What it does |
|---|---|---|
| `InterruptionFrame` | `frames/frames.py` | `SystemFrame` — travels out of band, ahead of queued data frames. |
| `FrameProcessor.broadcast_interruption()` | `processors/frame_processor.py:1017` | `broadcast_frame(InterruptionFrame)` — pushes it **both** upstream and downstream from the calling processor. |
| `FrameProcessor` handling | `frame_processor.py:839` | On `InterruptionFrame` → `await self._start_interruption()` (line 1130): cancels + recreates the processor's own input/process task, stops metrics, so queued frames in that processor are dropped. |
| Worker bypass | `pipeline/worker.py:1463-1469` | An `InterruptionWorkerFrame` makes the worker queue an `InterruptionFrame` **directly into the pipeline**, bypassing the push queue (works even if the push side is blocked). |
| Output-transport drop | `transports/base_output.py:566 handle_interruptions()` | `_cancel_audio_task()` then `_create_audio_task()` — **kills the task that is playing/enqueuing PCM and starts a fresh one → every queued and currently-playing audio frame is discarded.** Also cancels clock/video tasks. Reached from line 384 when a sender sees the interruption. |
| TTS-service cleanup | `services/tts_service.py:1031 _handle_interruption()` | Clears text aggregator, filters, `_aggregated_frame_sequencer`, `_pending_llm_response_end_frames`, word timestamps; `_stop_audio_context_task()`; `_serialization_queue.reset()`; per-context `on_audio_context_interrupted`; `reset_active_audio_context()`; `_turn_context_id=None`; recreates the audio-context task; `_maybe_resume_frame_processing()`. |
| Canonical VAD→interruption bridge | `processors/aggregators/llm_response_universal.py:1292` | The universal LLM aggregator calls `broadcast_interruption()` when it sees the user start speaking. This is the piece NeXa would normally get "for free" — **and does not use.** |

**Where NeXa stands against this:** NeXa's pipeline is
`transport.input() → _MicGateFrameProcessor → VADProcessor →
_UtteranceCaptureFrameProcessor → _VoiceStateFrameProcessor →
[AssistantSpeechBridge → NexaSpeechPlanner → NexaSpeechContinuityController
→ PiperHttpTTSService] → transport.output()`. There is **no**
`LLMResponseAggregator`, **no** `UserTurnProcessor`, **no**
`enable_interruptions=True` anywhere. `NexaSpeechPlanner.process_frame`
and `NexaSpeechContinuityController.process_frame` both already have an
`isinstance(frame, InterruptionFrame)` branch that `_reset()`s and
forwards — but nothing upstream ever produces the frame, so those
branches never run in production. `AssistantSpeechBridge.process_frame` is
pure pass-through and has **no** interruption branch at all.

**Boundary rule for M2.5B (unchanged from the milestone brief):** Pipecat
may own media transport, audio-queue cancellation and interruption
*signalling*. Pipecat must **not** become the conversation brain, the
history authority, the model-routing authority, the language authority,
or a second `ConversationSession`. The `broadcast_interruption()` call is
issued by a **NeXa** processor; the decision of what the interrupted
history looks like is made by the **one** `ConversationSession`.

## CURRENT HALF-DUPLEX LIMITATION

R0026's fix makes `response_in_flight` cover generation + playback, and
`_MicGateFrameProcessor` withholds every `InputAudioRawFrame` for that
whole window. Consequences that M2.5B must remove:

1. **No barge-in is physically possible** — mic frames never reach the
   VAD while NeXa is thinking or speaking, so there is nothing to detect.
2. **The think window is dead to the operator** — R0026 measured 10–25 s
   where the operator cannot influence the turn; anything they say is
   dropped with `DROP_BUSY_RESPONSE_IN_FLIGHT`.
3. **Replacing it naively reintroduces R0026** — if the mic is simply
   left open and every `VADUserStoppedSpeakingFrame` is submitted, TV /
   room audio during a long generation snowballs into the 8-STT /
   4-turn backlog again. The single-candidate `InterruptionState`
   machine is the specific mechanism that prevents this.

## MIC / SPEAKER / AEC TOPOLOGY

- **Capture:** reSpeaker XVF3800 4-Mic Array, ALSA `hw:CARD=Array`, via
  `pcm.respeaker` (`type plug`). XMOS on-chip DSP: beamforming, noise
  suppression, de-reverb, and an **AEC that needs the far-end (playback)
  reference on the array's own USB *playback* endpoint** (Interface 1,
  Endpoint 0x01 OUT, S16_LE 2ch 16 kHz).
- **Playback:** NeXa plays TTS to `plug:usb_speaker` →
  `hw:CARD=UACDemoV10` — a **separate** Jieli UACDemoV1.0 USB DAC. The
  XVF3800's playback endpoint is **not** driven by NeXa, so the hardware
  AEC gets **no reference** in the configuration actually in use.
- **`/etc/asound.conf`:** `pcm.!default` is `asym` — playback
  `plug:usb_speaker`, capture `plug:respeaker`; `ctl.!default` = the
  Jieli card.
- **Pipecat AEC:** Pipecat 1.8.1 ships only *noise-suppression* audio
  filters (`krisp`, `ai_coustics`, `noisereduce` wrappers) — **no
  reference-based echo canceller**. There is no usable local AEC in this
  environment from Pipecat.

**Net:** there is no software AEC and the hardware AEC is unfed. Whether
that matters is answered empirically by the self-echo spike — and it
turns out **not to**, because the array's other DSP stages plus the
physical speaker→mic attenuation already put NeXa's own voice below the
VAD floor.

## SELF-ECHO TEST

`docs/research/m2_5_bargein/spike_self_echo.py` — VAD-only `VoiceRuntime`
on the reSpeaker, no STT/LLM/TTS-pipeline; synthesises ~12–13 s of Polish
Piper speech; plays it and counts `USER_SPEAKING` transitions during
playback. Three variants:

| Variant | Playback route | Purpose |
|---|---|---|
| `BASE_no_playback` | — | room-noise control |
| `A1_usb_speaker_only` | `plug:usb_speaker` | the **current** NeXa route; XVF3800 AEC gets no reference |
| `A2_with_xvf3800_aec_reference` | `plug:usb_speaker` **and** `plug:respeaker` | also drives the array's USB playback endpoint = the hardware AEC far-end reference |

**Results — 3 independent runs** (`spike_self_echo_20260909_033338.json`,
`_033544.json`, `_033651.json`; run 3 with the Jieli PCM forced to
100 %):

| Run | BASE | A1 during-playback `USER_SPEAKING` | A2 during-playback `USER_SPEAKING` |
|---|---|---|---|
| 1 | 0 | 0 → false barge-in **False** | 0 → **False** |
| 2 | 0 | 0 → **False** | 0 → **False** |
| 3 (louder) | 0 | 0 → **False** | 0 → **False** |

**Finding:** NeXa's own Piper voice, at the levels tested, does **not**
trip the reSpeaker's Silero VAD on the current route, and feeding the
XVF3800 its AEC reference makes no observable difference (there is
nothing to cancel at the VAD's input). Self-echo false-barge-in risk on
this hardware is **low**. (An earlier ad-hoc RMS check in the prior
session showed the mic *does* acoustically pick up the echo — RMS ~8→~348
during a tone — so the rejection is happening in the array DSP + the VAD
threshold, not because the mic is deaf.)

## INTERRUPTION TRIGGER

**Recommendation:** confirmed sustained VAD speech during
`response_in_flight`, split into two stages:

1. **Candidate** — the first `VADUserStartedSpeakingFrame` seen while
   `response_in_flight` is true and no interruption is already in
   progress. Records `interrupt_candidate_started` (timestamp,
   `response_id`).
2. **Confirmed** — the candidate is still speaking after a **300 ms**
   hold (`VADParams.start_secs=0.2` already elapsed inside the frame +
   ~100 ms guard) with no intervening `VADUserStoppedSpeakingFrame`.
   Confirmation fires `broadcast_interruption()` and the cancellation
   actions **immediately** — it does **not** wait for STT.

**Detection ≠ transcription.** The interrupting utterance's audio keeps
being captured; when its `VADUserStoppedSpeakingFrame` arrives it goes
through the normal `SerialTranscriptionQueue` →
`BilingualSpeechTranscriber` → adapter path and becomes the next user
turn. Stopping the old TTS never waits on whisper.

**Why not the Pipecat `UserTurnProcessor` / `enable_interruptions`
path:** adopting it would drag in the `LLMResponseAggregator` as a
second turn/847-context authority. NeXa keeps its `ConversationSession`
as the only brain; the trigger is a small NeXa processor calling
`broadcast_interruption()`.

**Self-echo / false-trigger defence (evidence-backed):** the self-echo
spike shows NeXa's own voice does not reach the VAD, so the 300 ms
sustain is mainly a guard against room-noise transients and against the
operator's own tail-echo. Room-noise `BASE` runs were 0/0. If live
telemetry later shows spurious confirmations, the sustain window is the
first knob; a `min_volume` bump on a response-time VAD-params profile is
the second.

## INTERRUPTION STATE MACHINE

Owned by a new NeXa `BargeInController` frame processor (see architecture
section). One instance, one active `response_id` at a time.

```
        ┌─────────┐  notify_response_dispatched()          ┌──────────────────┐
        │  IDLE   │ ────────────────────────────────────▶  │  RESPONDING      │
        │         │                                        │  response_id=N   │
        └─────────┘  ◀───────────────────────────────────  │  in_flight=True  │
             ▲        response completes normally           └──────────────────┘
             │        (assistant turn committed)                  │
             │                                                    │ VADUserStartedSpeakingFrame
             │                                                    ▼
             │                                          ┌──────────────────────┐
             │        VADUserStoppedSpeakingFrame       │  INTERRUPT_CANDIDATE  │
             │        before 300 ms  ───────────────▶   │  (still response_id=N)│
             │        (no-op, back to RESPONDING)       └──────────────────────┘
             │                                                    │ speech sustained ≥ 300 ms
             │                                                    ▼
             │                                          ┌──────────────────────┐
             │   cancellation done  &  new utterance    │  INTERRUPTING        │
             └────  transcribed & promoted to turn ───  │  - response_id N     │
                    (SerialConversationQueue ≤1)        │    invalidated       │
                                                        │  - broadcast_interrupt│
                                                        │  - cancel_token.cancel│
                                                        │  - planner/bridge flush│
                                                        │  - audio task recreate │
                                                        │  - commit interrupted  │
                                                        │    assistant turn (C)  │
                                                        └──────────────────────┘
```

**Invariants:**

- **At most one** `INTERRUPT_CANDIDATE` / `INTERRUPTING` at a time. A
  `VADUserStartedSpeakingFrame` received while already in
  `INTERRUPT_CANDIDATE` or `INTERRUPTING` is ignored (not queued).
- No new user turn is promoted to `SerialConversationQueue` until the
  machine has left `INTERRUPTING` (cancellation acknowledged + history
  committed). Utterances that finish transcription while still
  `INTERRUPTING` are dropped with the existing
  `DROP_BUSY_RESPONSE_IN_FLIGHT` telemetry.
- `SerialTranscriptionQueue` (max 8, ≤1 concurrent) and
  `SerialConversationQueue` (max 4, ≤1 in flight) bounds are **unchanged**.
- Every LLM token, planner segment, TTS request and audio frame carries
  (or is checked against) the active `response_id`; anything tagged with
  an invalidated id is dropped before it can reach the planner / TTS /
  history.

## LLM CANCELLATION FINDINGS

- **Path exists:** `ConversationSession.send(cancel_token=…)` →
  `provider.generate(cancel_token=…)`. The Ollama provider's worker
  thread checks `cancel_token.is_cancelled` once per streamed line
  (`ollama.py:103`) and `return`s; the `finally` closes the `urlopen`
  context → TCP disconnect. `llama_server.py:93` has the identical check.
  `CancelToken` is a stdlib `threading.Event` wrapper, per-request only.
- **Backend actually stops (measured):** on a hard client disconnect,
  llama-server CPU spent ~102 jiffies in the first second then went flat
  — Ollama halts generation within **~1 s**, no zombie decode, model
  stays resident (no reload cost on the next turn). This satisfies
  "cancel local generation" without any Ollama-specific API.
- **Cancellation latency bound:** the per-line check means cancellation
  is observed at the *next token*; at PL ~9–11 chars/s that is tens to a
  few hundred ms. Acceptable. A fully stalled stream is bounded by the
  socket timeout, not by us.
- **Gaps M2.5B must close:**
  1. The voice adapter calls `session.send(...)` **without** a
     `cancel_token` — there is currently no way to cancel a voice turn's
     LLM stream.
  2. Merely breaking the adapter's `async for` (or cancelling its task)
     triggers `aclose()` on the generators but **does not** set the
     token, so the Ollama worker thread keeps draining the HTTP response
     into a dead queue until `done`. The token **must** be cancelled
     explicitly.
  3. `SerialConversationQueue._run` awaits `self._handler(item)` directly;
     to cancel a turn without killing the serial worker, `_run_turn_inner`
     must consume `session.send` inside its own `asyncio.Task` (or at
     minimum hold the `CancelToken`), which the `BargeInController` can
     signal.

## CONVERSATION HISTORY CANCELLATION FINDINGS

From the `send()` body above:

- The **user turn is committed immediately** (line 1), before any token.
- The **assistant turn is committed only on normal loop exhaustion**
  (line 3). If the consumer stops iterating early, Python throws
  `GeneratorExit` at `yield chunk`; lines 3–4 never run.
- **Orphan user turn:** after an early stop the history ends with a `USER`
  turn and no `ASSISTANT` reply. `_history` and `_response_languages`
  stay equal length (1 appended to each), so `ConversationContext.build`
  does not raise — but the **next** `send()` appends a second `USER` turn,
  producing two consecutive user messages on the wire. This is the defect
  to fix.
- **Full unspoken answer can never silently enter history today** —
  `response.text` is only read at line 3, which the early stop skips. Good.
- **A partially-heard answer currently leaves no assistant trace at all**
  — the operator heard 8 words, history records nothing. Also a defect.

**Options considered for the interrupted assistant turn:**

| | Representation | Verdict |
|---|---|---|
| **A** | Discard the user turn too; history unchanged, as if the turn never happened. | Use **only** for a think-window interruption where **zero** assistant text was flushed (nothing was heard, nothing to anchor). |
| **B** | Commit the assistant turn with the **full generated** text (buffer to completion even though playback stopped). | **Reject** — burns CPU finishing a dead turn, and records words the operator never heard as if spoken. |
| **C** | Commit the assistant turn with **exactly the text flushed to TTS at the interruption instant**, marked `interrupted=True`; drop the LLM remainder. | **Recommended.** History matches what the operator actually heard; the model sees it was cut off. |
| **D** | Keep the user turn, commit **no** assistant turn (orphan). | **Reject** — this is today's accidental behaviour; produces consecutive user turns. |

**Recommended mechanics (Option C, one `ConversationSession`, no
voice-only history):**

- The adapter already accumulates `chunks: list[str]` as tokens arrive.
  On interruption it stops iterating and calls a **new**
  `ConversationSession` method — `commit_interrupted_turn(spoken_text,
  *, response_language)` — instead of letting `send()` fall through to
  line 3.
- `spoken_text` = the text the planner/continuity actually **released to
  Piper** at the interruption instant (not every token the adapter
  buffered — some may still be sitting in `AssistantSpeechBridge._queue`
  or the planner's `_raw`). The `BargeInController` reads the planner's
  released-text high-water mark; if that seam is impractical for M2.5B,
  the safe approximation is `"".join(chunks)` truncated at the last
  sentence boundary the planner emitted.
- `commit_interrupted_turn` appends `ConversationTurn(role=ASSISTANT,
  content=spoken_text, interrupted=True)` and
  `_response_languages.append(None)` — the same two-list discipline as
  the normal path, so lengths stay 1:1 and `R0009` KV-prefix replay stays
  byte-stable (the stored resolved language for the *user* turn is
  unchanged).
- `ConversationTurn` gains an optional `interrupted: bool = False`
  (additive, defaulted — typed chat and all existing turns unaffected).
  `to_provider_messages` may optionally append " …[interrupted]" to that
  turn's content on the wire so the model does not treat the fragment as
  a complete thought; this is a wire-only decoration, never stored.
- If `spoken_text` is empty (think-window interruption, Option A):
  `commit_interrupted_turn` instead **removes** the just-added user turn
  and its `_response_languages` entry, returning history to its
  pre-`send` state. The interrupting utterance then becomes a clean fresh
  user turn.

**Bilingual (R0027) preservation:** the resolver runs once per real user
turn in the adapter, *before* `session.send`. An interruption does not
re-run it for the old turn. The new (interrupting) utterance goes through
`ResponseLanguageResolver.resolve()` normally as its own turn, so
`InputSpeechLanguage` / `ResponseLanguage` / `ResponsePreference.sticky`
stay separate and a one-turn override that was in force for the
interrupted turn does **not** leak forward (it was never sticky). A
sticky preference set by the interrupted turn's text — if the operator
managed to say "od teraz po angielsku" and then immediately interrupted
themselves — is an edge case: recommend the resolver mutation stays
because it is keyed to the *utterance*, not to whether the reply
completed. Covered by the acceptance matrix (cases 4, 5).

## TTS / PLANNER CANCELLATION FINDINGS

On `InterruptionFrame` today:

| Processor | Handles `InterruptionFrame`? | Action |
|---|---|---|
| `AssistantSpeechBridge` | **NO** | pure pass-through; internal `_queue` of `LLMTextFrame` + the `LLMFullResponseStart/End` bracketing would leak into the next response |
| `NexaSpeechPlanner` | yes (`process_frame`, ~line 607) | `_reset()` (clears `_raw` + partial phrase state), forwards the frame |
| `NexaSpeechContinuityController` | yes (`process_frame`, ~line 324) | `await _release_held(RESET)` + `_reset_turn()`, forwards the frame |
| `PiperHttpTTSService` (Pipecat) | yes (`_handle_interruption`) | full cleanup (see capability table); in-flight aiohttp `run_tts` generator is abandoned when its audio context is reset |
| `base_output` transport | yes (`handle_interruptions`) | cancels + recreates the audio task → drops queued + playing PCM |

**M2.5B TTS work:**
1. Add an `InterruptionFrame` branch to
   `AssistantSpeechBridge.process_frame`: drain `self._queue`
   (`get_nowait` until empty), set `_explicit_voice_this_turn=False`,
   drop any pending `on_assistant_complete` for the old `response_id`,
   forward the frame.
2. Confirm the abandoned Piper aiohttp request is actually closed
   (context reset abandons the async generator; verify no socket is left
   half-read — a `spike` or a unit test with a fake slow Piper).
3. Nothing in planner/continuity needs changing beyond making sure the
   `InterruptionFrame` reaches them **before** any late `LLMTextFrame` of
   the old response — guaranteed because `InterruptionFrame` is a
   `SystemFrame` and travels out of band.

## RESPONSE-ID / RACE-SAFETY FINDINGS

There is no response identity today — tokens, phrases and audio frames
are anonymous, so a late token from a cancelled generation is
indistinguishable from a live one.

**Recommendation:** the `BargeInController` allocates a monotonic
`response_id` at `notify_response_dispatched()` and holds it as
`active_response_id`. Then:

- The adapter tags its `on_assistant_token` / `on_assistant_complete`
  callbacks with the `response_id` it started with.
- `AssistantSpeechBridge` stamps each `LLMTextFrame` /
  `LLMFullResponseStartFrame` / `LLMFullResponseEndFrame` it enqueues
  with `response_id` (a frame attribute or a wrapper).
- On confirmation the controller sets `active_response_id = None` (or the
  next id). Any frame / callback whose `response_id != active_response_id`
  is dropped at the earliest processor that checks — the bridge for text
  frames, the controller for adapter callbacks.
- This is belt-and-braces with `broadcast_interruption()` +
  `cancel_token.cancel()`: even if a straggler token escapes the
  cancelled Ollama stream in the ~1 s window, it carries the dead id and
  never reaches the planner, TTS, or `commit_interrupted_turn`.

**Race that the id closes:** operator interrupts → controller cancels →
Ollama emits one more line before it sees the token → adapter's
`async for` (if not yet fully torn down) yields it → `on_assistant_token`
fires. With the id check that callback is a no-op. Without it, that token
would be appended to `chunks` and could land in `commit_interrupted_turn`.

## QUEUE SAFETY

R0026 guarantees to preserve: **max concurrent STT = 1** (via
`SerialTranscriptionQueue`, overflow raises `SttQueueOverflowError`),
**max concurrent conversation turn = 1** (via `SerialConversationQueue`,
overflow raises `ConversationQueueOverflowError`). The R0026 incident was
8 stale STT + 4 stale turns because *every* `VADUserStoppedSpeakingFrame`
was submitted during a long response.

**M2.5A position:** keep both queues and both bounds exactly. The
interruption path does **not** add a parallel queue. It adds the
single-candidate `InterruptionState` gate *in front of* promotion to
`SerialConversationQueue`:

- While `RESPONDING`: an utterance that completes STT is still
  `DROP_BUSY`-ed (unchanged) — unless it is *the* confirmed interruption,
  which is promoted exactly once, only after `INTERRUPTING` completes.
- While `INTERRUPT_CANDIDATE` / `INTERRUPTING`: further
  `VADUserStartedSpeakingFrame`s are ignored at the controller (never
  reach STT submission), so no second candidate, no backlog.
- The mic is **open** during a response (that is the change), but the VAD
  self-echo floor (self-echo spike) plus the single-candidate gate means
  room noise produces at most one candidate that fails the 300 ms
  sustain and resets — it cannot snowball.

Result: the worst case is **1 interruption STT + 1 promoted turn**, never
8 + 4.

## BILINGUAL INTERRUPTION REQUIREMENTS

- The interrupted turn's `ResponseLanguage` / one-turn override is **not**
  reused for the interrupting turn. The new utterance runs the full
  `BilingualSpeechTranscriber` (LID → guard → decode) and
  `ResponseLanguageResolver.resolve()` as an independent turn.
- `ResponsePreference.sticky` is session state and **survives** an
  interruption untouched unless the interrupting utterance itself is a
  sticky command.
- A one-turn override in force for the interrupted turn does **not** leak
  forward — it was never written to `sticky` (R0027).
- TTS voice for the new turn comes from the new turn's
  `ResponseLanguage` via `AssistantSpeechBridge.select_voice`, exactly as
  normal — the interruption does not pin the old voice.
- Acceptance matrix cases 1–6, 13 exercise this.

## THINK-WINDOW INTERRUPTION

**Recommendation: Option B — interruption accepted whenever
`response_in_flight` is true**, i.e. from `notify_response_dispatched()`
onward, not only once audio is playing.

- Rationale: R0026 proved the think/generation window (10–25 s) is
  exactly where the operator currently loses control. Restricting
  barge-in to "audio physically playing" (Option A) would leave that
  window half-duplex and defeat the milestone's purpose.
- When the interruption confirms with **no** audio yet flushed: the
  audio-stop actions are no-ops, `cancel_token.cancel()` still stops the
  LLM, and the history commit takes the **Option A** branch of
  `commit_interrupted_turn` (remove the just-added user turn; the
  interrupting utterance becomes a clean fresh turn).
- When the interruption confirms with audio partly flushed: full
  Option C.
- The state machine is identical for both; only
  `commit_interrupted_turn`'s empty-vs-nonempty `spoken_text` branch
  differs.

## REAL PI LATENCY FINDINGS

- **Self-echo spike (3 runs)** and **headless interruption spike (2 runs,
  8 speaker plays, +0 dB and +9.5 dB)**: the `plug:usb_speaker` → reSpeaker
  → Silero VAD path is **below the VAD floor** on this hardware. A
  speaker-played "interruption" produces **0 `USER_SPEAKING`** events, so
  **no headless latency number is obtainable** — the automated approach
  conflates the interruption signal with the self-echo signal we want
  suppressed.
- **What can be stated:** the detection term is Silero
  `start_secs=0.2` + a ~100 ms confirmation guard ≈ **~300 ms**
  speech-onset → interruption confirmed; the audio-stop tail via
  `base_output.handle_interruptions()` is one output chunk (~20–40 ms) +
  `cancel_task`. **Target: < 1 s** speech-onset → last old audio frame.
- **Measured capability vs target:** the target is **not yet proven** on
  the live hardware. `spike_bargein_live.py` (operator, near-field voice)
  is the test that produces the real numbers. **First run (2026-09-09):
  detection proven 5/5; latency INVALID and withdrawn** — the harness had
  no arming window and its `aplay` could never open the busy
  `plug:usb_speaker` that `VoiceRuntime` was already holding, so
  VAD-onset → playback-stopped was never measured (full analysis in *SPIKE
  B-live — FIRST RUN*). Rewritten to **v2** (VAD-only pipeline that holds no
  speaker; explicit A–J window; `playback_actually_stopped` measured via
  `kill()` + `wait()`). Operator re-run pending — see below.
- Room-noise `BASE` runs were 0 `USER_SPEAKING` across all three
  self-echo runs → idle-room false-interruption rate is low without any
  extra gating.

## RECOMMENDED PRODUCTION ARCHITECTURE

**New:** one NeXa frame processor, `BargeInController`, placed **right
after `VADProcessor`** (so it sees `VADUserStartedSpeakingFrame` first)
and given a handle to the `HalfDuplexGate`, the adapter, and the
`ConversationSession`. It owns:

- the `InterruptionState` machine and the single-candidate invariant;
- `active_response_id` (allocated on `notify_response_dispatched`);
- the per-turn `CancelToken` (created here, handed to the adapter for
  `session.send`, cancelled here on confirmation);
- issuing `broadcast_interruption()` on confirmation;
- calling `session.commit_interrupted_turn(spoken_text, …)` (Option C /
  A) instead of the adapter's normal `on_assistant_complete`;
- telemetry emission (below).

**Changed, minimally:**

- `HalfDuplexGate`: add a `bargein_enabled` mode where, during
  `response_in_flight`, `_MicGateFrameProcessor` **stops withholding**
  `InputAudioRawFrame` (raw mic stays hot) but `_bot_speaking` still
  suppresses the acoustic self-echo tail *after* `BotStoppedSpeaking`.
  The R0026 whole-response close remains the default and the fallback
  (`--no-bargein`).
- `AssistantSpeechBridge`: `InterruptionFrame` branch that drains
  `_queue`; `response_id` stamping on enqueued frames.
- `VoiceConversationAdapter._run_turn_inner`: accept a `CancelToken`,
  pass it to `session.send`, consume the stream in a cancellable task,
  and on cancellation call `commit_interrupted_turn` with the planner's
  released-text high-water mark instead of `on_assistant_complete`.
- `ConversationSession`: new `commit_interrupted_turn(spoken_text, *,
  response_language)`; `ConversationTurn.interrupted: bool = False`
  (additive); `send()` unchanged for the normal path.
- `SerialConversationQueue`: no interface change; the handler simply
  becomes cancellable.

**Unchanged authorities:** one `ConversationSession` = brain + history +
model routing + language; `ResponseLanguageResolver` (R0027);
`BilingualSpeechTranscriber` + `LanguageIdGuard` (R0024/R0025);
`gemma4:e4b` / `num_thread=2` / `keep_alive=30m` / warm-up; Piper voices +
`length_scale`; `NexaSpeechPlanner` / continuity tunables;
`ggml-base-q8_0 -t4`. Pipecat owns transport + audio-queue cancellation +
`InterruptionFrame` propagation only.

**Flow on a confirmed barge-in:**

```
VAD start (response_in_flight) → CANDIDATE → (≥300 ms sustained) → CONFIRMED:
  1. active_response_id := None            (invalidate; late tokens/frames dropped by id)
  2. cancel_token.cancel()                 (Ollama stops in ~1 s, measured)
  3. broadcast_interruption()              (NeXa processor → both directions)
       → AssistantSpeechBridge drains _queue
       → NexaSpeechPlanner._reset()
       → NexaSpeechContinuityController releases/reset
       → PiperHttpTTSService._handle_interruption()   (abandons in-flight run_tts)
       → base_output.handle_interruptions()           (drops queued + playing PCM)
  4. session.commit_interrupted_turn(spoken_text_highwater, response_language)
       spoken_text == ""  → remove the orphan user turn (Option A, think-window)
       spoken_text != ""  → append ASSISTANT turn interrupted=True (Option C)
  5. state := INTERRUPTING; when the interrupting utterance finishes STT it is
     promoted ONCE to SerialConversationQueue as the next user turn; state := RESPONDING (new id) or IDLE
```

## WHAT M2.5B MUST IMPLEMENT

1. `BargeInController` frame processor + `InterruptionState` machine +
   single-candidate invariant + `response_id`.
2. `HalfDuplexGate.bargein_enabled` (raw mic hot during
   `response_in_flight`; self-echo tail still gated by `_bot_speaking`);
   `--no-bargein` fallback to R0026 behaviour.
3. Per-turn `CancelToken`: created in the controller, threaded
   adapter → `session.send` → `provider.generate`, cancelled on
   confirmation; adapter consumes the stream in a cancellable task
   without killing the `SerialConversationQueue` worker.
4. `AssistantSpeechBridge` `InterruptionFrame` handling + `_queue` drain +
   `response_id` stamping / checking.
5. `ConversationSession.commit_interrupted_turn()` (Option C, with the
   Option A empty-`spoken_text` branch) + `ConversationTurn.interrupted`
   (additive) + optional wire-only "[interrupted]" decoration in
   `to_provider_messages`.
6. Planner "released-text high-water mark" seam so `spoken_text` is what
   was actually spoken, not what was buffered.
7. Verify the abandoned Piper aiohttp request is closed on interruption
   (test with a fake slow Piper).
8. Telemetry (below) wired to the probe's live print + shutdown summary.
9. The 17-case acceptance matrix (below) as `tests/…` + a live operator
   run.
10. `apps/nexa_bilingual_voice_probe.py`: surface interruption events
    live; keep the R0026 drop summary.

## ACCEPTANCE TEST MATRIX (for M2.5B)

| # | Scenario | Expected |
|---|---|---|
| 1 | Assistant speaking **PL**, operator interrupts in **PL** | old TTS stops < ~1 s; PL answer to the new request; same session |
| 2 | Assistant speaking **PL**, operator interrupts in **EN** | old TTS stops; **EN** answer (mirrors new `InputSpeechLanguage`); voice switches to EN |
| 3 | Assistant speaking **EN**, operator interrupts in **PL** | old TTS stops; **PL** answer; voice switches to PL |
| 4 | Sticky preference = EN, assistant speaking EN, operator interrupts in PL (no command) | new answer still **EN** (sticky survives interruption) |
| 5 | One-turn override ("odpowiedz po angielsku") was in force for the interrupted turn; next (interrupting) turn is plain PL | interrupting turn answered in **PL** — the override did not leak (never sticky) |
| 6 | Interrupting utterance **is** a sticky command ("od teraz po angielsku") | `ResponsePreference.sticky := en`; that turn + subsequent turns EN |
| 7 | NeXa's own TTS playing, **no** operator speech | **no** interruption; response completes normally (self-echo spike backs this) |
| 8 | Silence / room tone during a response | **no** interruption turn admitted |
| 9 | Continuous room noise (TV) during a long generation | at most **one** candidate, fails the 300 ms sustain, resets; **0** promoted turns; no STT/turn backlog (R0026 regression guard) |
| 10 | Operator interrupts, then two more people talk over each other immediately | **exactly one** interruption turn admitted; extras ignored, not queued |
| 11 | Confirmed interruption | old TTS never resumes after the new answer; no leftover phrases from the planner/bridge/Piper |
| 12 | Straggler LLM token escapes the cancelled Ollama stream | dropped by `response_id`; never in the new answer, never in history |
| 13 | Interrupted-history representation (Option C) | history has the interrupted assistant turn = exactly what was heard, `interrupted=True`; no consecutive user turns; lengths 1:1 |
| 14 | Interruption **before** first audio (think window) | LLM cancelled; **Option A** — orphan user turn removed; interrupting utterance is a clean fresh turn |
| 15 | 10 interruptions over a 5-minute session | STT queue depth returns to 0, conversation queue depth to 0; no progressive slowdown; model stays resident |
| 16 | Normal PL↔EN turns with **no** interruption | byte-for-byte the R0027 behaviour; `--no-bargein` == R0026 behaviour |
| 17 | Operator interrupts the interruption's answer (nested) | same guarantees recursively; one active `response_id`; one candidate |

## OBSERVABILITY / TELEMETRY

Emit (probe live-print + shutdown summary; **no** user-audio persistence
beyond the existing policy):

`response_id`, `response_in_flight`, `assistant_speaking`,
`interrupt_candidate_started` (t, response_id),
`interrupt_confirmed` (t, response_id, reason),
`interrupt_reason` (`sustained_vad` | `manual` | …),
`interrupt_timestamp`,
`llm_cancel_requested` (t), `llm_cancel_completed` (t, Δ),
`planner_flushed`, `bridge_queue_drained` (n frames),
`tts_cancelled`, `tts_flushed`, `last_old_audio_frame_ts`,
`interrupt_utterance_start` / `_end`, `stt_start` / `_end`,
`new_response_start`,
`queue_depth_stt`, `queue_depth_conversation`,
`queue_depth_tts_planner`,
`interrupted_turn_committed` (chars spoken, `interrupted=True`),
`orphan_user_turn_removed` (think-window Option A count).

## RISKS

1. **Live near-field barge-in latency unproven.** The headless path
   cannot measure it (self-echo floor). SPIKE B-live's first run proved
   *detection* (5/5) but its latency was invalid (see *SPIKE B-live —
   FIRST RUN*); the v2 harness fixes the instrumentation. Still mitigated
   by a corrected SPIKE B-live run before M2.5B.
2. **Raw mic hot during a response** could still catch loud room audio
   the array does not fully suppress — a candidate that *passes* 300 ms.
   Mitigated by the single-candidate gate (worst case = 1 spurious turn,
   not a backlog) and a tunable sustain window; a response-time
   `min_volume` profile is the fallback.
3. **`spoken_text` high-water mark** — if the planner seam is hard to
   read precisely, the fragment committed to history may be a sentence
   or two off from what was heard. Acceptable; documented; refine later.
4. **Piper aiohttp request abandonment** — if the context reset does not
   actually close the socket, a synth could complete and its PCM arrive
   after the new turn started. Must be verified with a fake-slow-Piper
   test in M2.5B.
5. **`ConversationTurn.interrupted` field** touches a core dataclass —
   additive + defaulted, but every `to_provider_messages` / context
   builder / persistence path must be checked so typed chat is
   byte-for-byte unchanged.
6. **Nested interruptions** (case 17) — the `response_id` allocator and
   the state machine must be re-entrant-safe; a naive boolean flag is
   not enough.

## FILES CHANGED (this stage — research only)

- `docs/CURRENT_STATE.md` — consistency fix (M2.4B COMPLETE; M2.5A active;
  B.3.6 latency re-confirmation marked non-blocking).
- `docs/research/m2_5_bargein/spike_self_echo.py` — SPIKE A (self-echo),
  ran 3×; JSON results committed.
- `docs/research/m2_5_bargein/audio_mix.py` — pure `mix_overlay` /
  `read_wav_i16` / `write_wav_i16` (numpy + `wave` only).
- `docs/research/m2_5_bargein/spike_interruption_latency.py` — SPIKE B
  headless; ran 2× (0/8 detections — documented negative result).
- `docs/research/m2_5_bargein/spike_bargein_live.py` — SPIKE B-live.
  **v1 ran once (2026-09-09), latency invalid; rewritten to v2** — VAD-only
  pipeline (no audio output held), explicit arming window, captured `aplay`
  stderr, `playback_actually_stopped` measured via `proc.kill()` +
  `proc.wait()` ("PLAYBACK TASK STOPPED"), pre-/post-arm VAD counts. Still
  research-only; no `src/` import for mutation. Operator re-run pending.
- `docs/research/m2_5_bargein/spike_self_echo_2026090*.json`,
  `spike_interruption_latency_2026090*.json`,
  `spike_bargein_live_20260909_134647.json` — raw spike output (the last is
  v1's invalidated run, kept for the record).
- `tests/test_bargein_spike.py` — +16 deterministic (pure maths + AST
  research-only guards; +6 `derive_trial_metrics` / `_agg` this round).
- `docs/reports/R0028_…md` — this report.

**No `src/` change. No production behaviour change.** `HalfDuplexGate`,
`_MicGateFrameProcessor`, `_UtteranceCaptureFrameProcessor`, the serial
queues, `ConversationSession`, `AssistantSpeechBridge`,
`NexaSpeechPlanner`, `NexaSpeechContinuityController`,
`ResponseLanguageResolver`, `BilingualSpeechTranscriber`,
`LanguageIdGuard`, `gemma4:e4b` / `num_thread=2` / `keep_alive=30m` /
warm-up, Piper, `ggml-base-q8_0 -t4` — all untouched.

## TEST RESULTS

- `tests/test_bargein_spike.py` — **16 deterministic, offline** (10 prior
  + 6 this round):
  - prior: `mix_overlay` offset / extension / int16-clip (no wrap) /
    gain-scales-overlay-only / negative-start-rejected; WAV round-trip;
    stereo downmix; AST guards that every spike imports **no**
    `nexa.conversation` / `nexa.providers` / `nexa.voice_conversation` /
    `nexa.memory` / `nexa.bootstrap` module, never contains `_history` or
    `.send(`; `audio_mix` is `{__future__, wave, pathlib, numpy}` only.
  - new (SPIKE B-live v2 metric derivation, loaded AST-isolated so no
    pyaudio/pipecat import): happy-path `prompt_to_vad_s` /
    `vad_to_stop_request_ms` / `vad_to_playback_stopped_ms`; no-detection
    yields **`None`, not `0`**, latencies; playback-failed trial still
    reports control latency but `None` media-stop; a pre-arm VAD event
    flags `prearm_contaminated` **without** changing the measured
    (post-arm) event; a negative `prompt_to_vad_s` is reported, never
    clamped; `_agg` ignores `None` and reports `n`.
- Full suite: **`pytest` 603 passed / 7 skipped / 14 subtests** ·
  **`python -m unittest discover -s tests` 610 OK / 7 skipped** ·
  **`ruff check src tests apps docs/research/m2_5_bargein`** clean ·
  **`git diff --check`** clean.
- SPIKE B-live v2 itself is **not** run in CI (needs the reSpeaker, the USB
  DAC, real Piper, and the operator).

## OPERATOR ACTION REQUIRED

SPIKE B-live's instrumentation was repaired (v2). **Re-run it once** — the
same single command, from the repo root:

```
.venv/bin/python docs/research/m2_5_bargein/spike_bargein_live.py
```

**5 trials.** Each trial NeXa speaks a ~19 s Polish sentence through the
USB speaker; ~2 s after playback is confirmed active the terminal prints
`>>> SPEAK NOW <<<`. **Only after that prompt**, at **normal speaking
volume**, say **one short phrase** and stop. The harness accepts only the
first VAD speech-start *after* it arms, immediately kills the playback, and
waits for that process to exit. Suggested phrases (one per trial, vary
them):

1. `Stop.`
2. `Czekaj.`
3. `Actually, tell me about black holes instead.`
4. `Nie, zapytam o coś innego.`
5. `Hold on — what about gravity?`

If a trial prints `!! PLAYBACK DID NOT START`, note it and keep going — the
`VERDICT` block reports how many trials had playback confirmed active.
It writes `docs/research/m2_5_bargein/spike_bargein_live_<ts>.json` and
prints a `VERDICT (<ts>):` block with: playback-confirmed-active count,
post-arm-VAD-detected count, fully-measurable count, pre-arm / post-accept
VAD counts, **`VAD start → stop requested` (control, ms)**, **`VAD start →
PLAYBACK TASK STOPPED` (media-stop, ms)**, and `prompt → VAD` (seconds,
**informational only — includes human reaction time**). Paste that block
(or the JSON) back. **No TV test. Do not start M2.5B before that corrected
evidence exists.**

## COMMIT HASH

Prior R0028 tip: `22dc46b` — `docs: record R0028 commit hash`. The SPIKE
B-live v2 instrumentation-repair commit is made immediately after this
edit; its hash is recorded in the following commit (R0026/R0027 pattern).
Not pushed.

## GIT STATUS

Branch `main`. Not pushed. Working tree: `spike_bargein_live.py` rewritten
(v2), `tests/test_bargein_spike.py` +6, this report updated, plus the
untracked v1 raw output `spike_bargein_live_20260909_134647.json`. `ruff`
clean for this stage's scope; `git diff --check` clean.

## NEXT STEP

1. Operator **re-runs SPIKE B-live v2** (one command above); the corrected
   `vad_to_stop_request_ms` / `vad_to_playback_stopped_ms` (and the
   playback-confirmed-active count) are folded into this report. M2.5A
   closes only then. **M2.5B has not started.**
2. **M2.5B — production barge-in** to *RECOMMENDED PRODUCTION
   ARCHITECTURE* + *WHAT M2.5B MUST IMPLEMENT*, verified against the
   17-case matrix and a live operator session, `--no-bargein` keeping
   the R0026 behaviour as fallback. **Does not begin before step 1's
   corrected evidence exists.**
3. Still non-blocking and owed independently: the B.3.6 operator latency
   re-confirmation (STT latency + END_OF_TURN→first-audio).
