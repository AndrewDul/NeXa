# R0031 — M2.6A: Minimal Gemini Live Real-Hardware Feasibility Spike

- **Date:** 2026-09-10
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **M2.6 — Cloud Realtime Voice** ·
  substage **M2.6A** (feasibility spike, the cloud analogue of M2.5A).
- **Status:** **M2.6A CLOUD REALTIME VOICE FEASIBILITY — PASS /
  OPERATOR-CONFIRMED (2026-09-10). VOICE: `Sulafat` — OPERATOR-CONFIRMED,
  frozen as the current NeXa cloud voice baseline.** Attempt #1 was an
  infrastructure bug (missing `LLMRunFrame` kickoff), root-caused from
  source and fixed. Attempt #2 (Pipecat default voice) = real PL/EN
  conversation, operator EXCELLENT / "essentially immediate" / "mega
  super"; the operator's only ask was a female/cozy/warm voice. A **third
  session** was then run with `Sulafat` ("Warm"): after real natural use
  the operator said *"I like this voice, we keep it."* Machine evidence
  across all sessions: **EOT → first audible ≈ 0.75 s median** (turn-local;
  R0030 gate ≤ 1.5 s), **barge-in ≈ 2 ms** local playback-stop, 0 AEC-feed
  failures. **C6 (input-transcription vs first audio), turn-local, Sulafat
  session:** the RAW input transcription precedes first response audio in
  **3/3 valid turns** by ~0.5 s (median 0.54 s); the aggregated (PUSHED)
  transcript precedes it in only 2/3. **But timing headroom ≠
  steerability** — see the C6 architectural conclusion: NeXa cannot be
  assumed to steer the *same* cloud response, so ADR-0004 should default
  to Gemini's native language mirroring (which worked). Reconnect /
  lifetime testing deliberately deferred to M2.6B (quota preserved).
- **Related:** `R0030` (Cloud Realtime Voice research/architecture — **and
  its `PHASE 0 CORRECTIONS`, applied in this task**), `R0029` (frozen
  local baseline), `ADR-0003` (D2 one `ConversationSession`; compliance
  "no cloud credentials in the local M2 path" — this spike opens that
  surface deliberately, hence **ADR-0004 is owed** before any `src/`
  cloud code), `docs/research/m2_6_cloud_realtime_voice/`.
- **Not pushed. No `src/nexa/**` change. No tracked-dependency-file
  change.**

---

## R0030 CORRECTIONS MADE (Phase 0, 2026-09-10)

Re-checked against current official Google sources. Full detail in R0030
§`PHASE 0 CORRECTIONS`; summary:

| # | Was (R0030 v1) | Now (VERIFIED 2026-09-10) | Source |
|---|---|---|---|
| **C1** | "free-tier conversations are used for product improvement / human review, therefore M2.6A needs a paid tier for privacy" (blanket) | **Region-dependent.** *In* EEA/CH/UK the Paid-Services data terms apply to unpaid quota too → data **not** used to improve Google products; *outside*, unpaid-quota data **is** used to improve Google products + may be human-reviewed. Separately: a NeXa cloud voice *made available to users* in EEA/CH/UK must use Paid Services. M2.6A uses scripted non-sensitive phrases → safe either way. | `ai.google.dev/gemini-api/terms` |
| **C2** | "chunk size — OPEN QUESTION, docs give none" | **CLOSED.** *"Send audio in chunks of 20ms to 40ms."* / *"Send small chunks (20ms - 100ms) to minimize latency"* / do not buffer ~1 s. **M2.6A baseline 20 ms.** | `…/live-api/best-practices` |
| **C3** | "resumption window — 24 h vs 2 h, contradictory, verify" | **CLOSED.** *"Resumption tokens are valid for 2 hours after the last session terminates."* Also: connection ~10 min; audio-only session 15 min without compression; compression → unlimited; audio ≈ **25 tokens/second**; `GoAway.timeLeft` → wrap up / reconnect before close. | `…/live-api/best-practices` |
| **C4** | "pricing — sources disagree ($1/1M vs $3/$12)" | **Official table.** `gemini-3.1-flash-live-preview` paid: input **$0.75/1M text**, **$3.00/1M audio** (~$0.005/min); output **$4.50/1M text**, **$12.00/1M audio** (~$0.018/min). Free tier free of charge. Grounding: 5,000 free/month (shared Gemini 3.x), then $14/1,000. Data "used to improve products": free = yes, paid = no. | `…/docs/pricing` |
| **C5** | "Polish native audio speaks with a strong EN/US accent (G8, unresolved)" tagged **VERIFIED** | **Retagged EXTERNAL REPORTED RISK / COMMUNITY EVIDENCE.** It is one forum thread; Google staff only said *"we've passed your feedback to the relevant product teams"* — not a confirmation. **The M2.6A operator test is the authority.** Still High-priority to *test*. | `discuss.ai.google.dev/t/…/177436` |
| **C6** | *(missing)* | **New CRITICAL OPEN QUESTION.** A native speech-to-speech model may start speaking **before** the complete input transcription reaches NeXa — then `ResponseLanguageResolver` cannot steer the *same* response without added latency. M2.6A **must measure** the event ordering and answer questions A–G (R0030 C6). M2.6A temporarily lets Gemini mirror the spoken language natively; the production language-routing authority is an **ADR-0004** decision *after* measurement. | this task |
| **C7** | "no cloud call was made" | **One MEASURED FACT added:** authenticated Live-API WebSocket setup handshake **449 ms**, clean disconnect, no audio, no secret leak. | this task |

`docs/CURRENT_STATE.md` and `docs/ROADMAP.md` updated to match.

---

## OPERATOR ATTEMPT #1 — FAIL (silent after user speech)

Real reSpeaker + speaker, `m2_6a_gemini_live_probe.py`. Operator log:

| Stage | Result |
|---|---|
| Gemini connection (`"Connected to Gemini service"`) | **PASS** — key / model / auth healthy |
| AEC far-end reference (`"XVF3800 AEC reference feed active on plug:respeaker"`) | **PASS** |
| `LocalAudioTransport` start; `input_device_index=3` (respeaker), `output_device_index=2` (usb_speaker) | **PASS** |
| Local Silero VAD — `User started speaking` / `User stopped speaking` ×2; Smart Turn `COMPLETE` | **PASS** — mic + turn detection working |
| **Gemini response path** — input transcription, server content, cloud audio, `turnComplete`, audible reply, after **both** detected turns | **FAIL — nothing** |

So this was **not** an audio-quality test. The failure is entirely
upstream of cloud playback: **Gemini received nothing** for either turn.

## ROOT CAUSE (proven by source analysis)

**The probe never sent the one-time `LLMRunFrame` that initialises
`GeminiLiveLLMService`'s context, so `_ready_for_realtime_input` stayed
`False` for the whole session — and with server VAD disabled that flag
gates every path that talks to Gemini.**

Chain, all in the installed Pipecat 1.8.1 (`pipecat/services/google/gemini_live/llm.py`
+ `pipecat/processors/aggregators/llm_response_universal.py`):

1. `GeminiLiveLLMService.setup()` → `_connect()` opens the WebSocket
   (`"Connected to Gemini service"`). But `_handle_session_ready()` on the
   **initial** connection finds `_run_llm_when_session_ready=False`, no
   resumption handle, and **`self._context is None`** → it takes the
   `else` branch, whose own comment is: *"Initial connection: session is
   ready before context has arrived. Nothing to do — `_handle_context`
   will call `_create_initial_response` when the context arrives."*
2. `_create_initial_response()` is the **only** thing that sets
   `self._ready_for_realtime_input = True` (or queues it via
   `_run_llm_when_session_ready`). It `assert`s `self._context is not
   None`, and `_context` is only ever set inside `_handle_context()`.
3. `_handle_context()` only runs when an **`LLMContextFrame`** reaches the
   service. The context aggregator emits that frame from
   `push_context_frame()`, which is called by **`_handle_llm_run()`** (on
   an `LLMRunFrame`) or by `push_aggregation()` (after a *completed user
   turn is transcribed*).
4. No `LLMRunFrame` is ever queued (the probe didn't send one), and no
   user turn can be transcribed because Gemini isn't receiving audio —
   **circular deadlock**. `_context` stays `None`,
   `_ready_for_realtime_input` stays `False`.
5. With `vad=GeminiVADParams(disabled=True)` (`_vad_disabled=True`):
   - `_handle_user_started_speaking()` sends `activity_start` +
     `_flush_user_audio_preroll()` **only if** `self._vad_disabled and
     self._session and self._ready_for_realtime_input` → **False** →
     `activity_start` never sent, pre-roll never flushed.
   - `_send_user_audio()` bare-`return`s while `not
     self._ready_for_realtime_input` → mic audio is only appended to the
     rolling pre-roll buffer, never sent.
   - `_handle_user_stopped_speaking()` sends `activity_end` under the same
     guard → never sent.
6. Gemini therefore gets **no `activity_start`, no audio, no
   `activity_end`** → no input transcription, no `serverContent`, no
   model turn, no `turnComplete`, no audio. **Exactly the operator's
   symptom, for every turn.**

The frames themselves *did* reach the service (the operator saw
`User started/stopped speaking`, and the `LLMUserAggregator` forwards
`UserStarted/StoppedSpeakingFrame` and `InputAudioRawFrame` downstream via
its `else` branch) — they were just no-ops inside the guards.

## WHY THE RUN WAS SILENT — one sentence

The socket was "Connected" but the service was never told to run
(`LLMRunFrame`), so it never built a context, never became
`_ready_for_realtime_input`, and with server VAD off that flag is exactly
what lets it send `activity_start` / audio / `activity_end` — so Gemini
sat idle with nothing to respond to.

## OFFICIAL PIPECAT COMPARISON

`examples/realtime/realtime-gemini-live-locally-driven-turns.py`
(pipecat v1.8.1) — verbatim relevant lines:

```python
pipeline = Pipeline([transport.input(), user_aggregator, llm, transport.output(), assistant_aggregator])
...
@transport.event_handler("on_client_connected")
async def on_client_connected(transport, client):
    logger.info("Client connected")
    await worker.queue_frames([LLMRunFrame()])
```

- The example **explicitly queues one `LLMRunFrame()`** from the
  transport's `on_client_connected` handler — this is the kickoff that
  pushes the initial `LLMContextFrame` into `GeminiLiveLLMService`.
- The example's transports (Daily / FastAPI-websocket / eval) fire
  `on_client_connected`. **`LocalAudioTransport` has no client and fires
  no such event** (it registers no event handlers at all). So the
  example's kickoff mechanism has **no equivalent** in our local-device
  probe — and our probe omitted the kickoff entirely. **That omission is
  the root cause.**
- The example also uses `inference_on_context_initialization` default
  (`True`) with a system prompt that *asks for* an opening greeting
  ("Say hello…"). Our probe must **not** greet.

## THE FIX (probe only — no `src/nexa/**`, no deps)

`docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py`:

1. **Queue exactly one `LLMRunFrame()`** after the socket connects. A new
   `_kickoff()` coroutine polls `llm._session` (≤ 15 s), then
   `await worker.queue_frames([LLMRunFrame()])` once, marking
   `KICKOFF_SOCKET_CONNECTED` / `LLM_RUN_FRAME_QUEUED`. This is the
   `LocalAudioTransport` equivalent of the example's `on_client_connected`
   kickoff.
2. **`inference_on_context_initialization=False`** on
   `GeminiLiveLLMService` → the init seed goes out with
   `turn_complete=False`, so the service becomes ready **without
   generating an opening bot utterance**.
3. **Empty `LLMContext(messages=[])`** (system instruction supplied only
   via `system_instruction=` / `Settings`). With an empty context +
   `inference_on_context_initialization=False`, `_create_initial_response`
   early-returns and flips `_ready_for_realtime_input = True` **without
   sending any seed at all** — and it removes the Pipecat warning
   *"Both system_instruction and an initial system message in context are
   set … converting to a user message"* seen in the first smoke.

No initial spoken greeting. No duplicated conversation state. The frozen
local NeXa path is untouched (this is a `docs/research/` file).

## DIAGNOSTICS ADDED (probe only, clearly spike-labelled)

- **`_state_poller()`** (10 Hz, SPIKE-ONLY): first-transition marks
  `GEMINI_CONNECTED` (`llm._session`), `LLM_CONTEXT_INITIALIZED`
  (`llm._context`), `GEMINI_REALTIME_READY` (`llm._ready_for_realtime_input`).
  Stops after all three; no per-audio-frame logging.
- **`_install_llm_diagnostics()`** (SPIKE-ONLY): wraps four *private*
  `GeminiLiveLLMService` methods **on the single probe instance** — pure
  observation, each wrapper calls the original unchanged:
  - `_handle_user_started_speaking` → marks `USER_STARTED_FRAME_AT_GEMINI`
    (+ `ready` / `session` / `vad_disabled`) and `ACTIVITY_START_SENT` or
    `ACTIVITY_START_SKIPPED{reason}`.
  - `_handle_user_stopped_speaking` → `USER_STOPPED_FRAME_AT_GEMINI`,
    `ACTIVITY_END_SENT` / `ACTIVITY_END_SKIPPED{reason}`.
  - `_flush_user_audio_preroll` → `PREROLL_FLUSH_TO_GEMINI{bytes}`.
  - `_send_user_audio` → counts `audio_bytes_to_gemini` /
    `audio_chunks_to_gemini` only for frames that pass the send guard;
    first one marks `INPUT_AUDIO_TO_GEMINI_FIRST{bytes}`.
- **`_SpikeMetrics`** gains `GEMINI_SERVER_CONTENT_FIRST` (first of
  `TTSStartedFrame` / `LLMFullResponseStartFrame` / `TranscriptionFrame` /
  `TTSTextFrame` / `TTSAudioRawFrame` from the server), and its
  `INPUT_TRANSCRIPTION_FIRST` / `OUTPUT_TRANSCRIPTION_FIRST` /
  `FIRST_SERVER_CONTENT` are now first-only (a `_once()` helper; the
  previous code had an `... or True` bug that re-marked every frame).
- `PIPELINE_READY` is marked once the runner starts.
- New probe mode **`--lifecycle-smoke`**: one authenticated,
  **no-microphone** run on a transport-less pipeline
  (`[user_aggregator, llm]`) that queues the `LLMRunFrame` and verifies
  `_ready_for_realtime_input` flips True. No user turn → the model
  generates nothing → near-zero tokens.

## LIFECYCLE-SMOKE PROOF (agent-run, 2026-09-10)

```
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py --lifecycle-smoke

lifecycle events: ['KICKOFF_SOCKET_CONNECTED', 'GEMINI_CONNECTED',
                   'LLM_RUN_FRAME_QUEUED', 'LLM_CONTEXT_INITIALIZED',
                   'GEMINI_REALTIME_READY']
GEMINI_CONNECTED        : True
LLM_CONTEXT_INITIALIZED : True
GEMINI_REALTIME_READY   : True
RESULT: PASS — service reached realtime-ready
EXIT=0
```

**MEASURED FACT (2026-09-10):** with the `LLMRunFrame` kickoff +
`inference_on_context_initialization=False` + empty context, the service
now reaches `_ready_for_realtime_input=True` (`GEMINI_REALTIME_READY`)
after `LLM_RUN_FRAME_QUEUED` → `LLM_CONTEXT_INITIALIZED`. No mic, no user
turn, no model generation, no seed sent (empty-context early return), no
warning. Two such smokes were run (before/after the empty-context
cleanup); no audio session, no long generation, negligible token use.

**Still operator-only:** whether, once ready, a real spoken turn produces
a timely PL/EN audio reply of acceptable quality.

---

## OPERATOR ATTEMPT #2 — PASS (real conversation)

Two consecutive real-hardware sessions, reSpeaker + speaker,
`gemini-3.1-flash-live-preview`. Machine evidence:
`docs/research/m2_6_cloud_realtime_voice/m2_6a_probe_results_20260910T212136Z.json`
(session 1, ~133 s, 9 turns incl. deliberate barge-ins) and
`…T212254Z.json` (session 2, ~37 s, 2 plain turns). Metrics recomputed
with the corrected barge-in logic → `…_recomputed.json` next to each.

### Operator UX judgement (real natural conversation)

| Axis | Operator verdict |
|---|---|
| Conversation quality | **EXCELLENT** |
| Perceived response latency | **essentially immediate** |
| Reasoning / answers | intelligent and appropriate |
| Natural conversational flow | **EXCELLENT** |
| Barge-in (interrupt naturally + immediately) | **EXCELLENT** |
| Overall | *"mega super"*, *"rest perfect"* |
| **Only** requested change | replace the current voice with a **pleasant, cozy, female** voice |

### Real-hardware latency (verified from the JSON, not the terminal)

`eot_to_first_audio_played_s` (LOCAL_VAD_EOT → BotStartedSpeakingFrame):

| Session | Values (s) | Median |
|---|---|---|
| 1 (`212136Z`) | 0.8198, 0.8129, 0.8268, 0.8094, 0.8430, 0.7442, 0.7544 | **0.8129** |
| 2 (`212254Z`) | 0.6769, 0.9063 | 0.7916 |
| **Combined (9)** | min 0.677 · max 0.906 · mean 0.799 | **0.8129** |

`eot_to_first_audio_received_s` (EOT → first `TTSAudioRawFrame` off the
socket, before playback): session 1 median ≈ 0.775 s.

**R0030 gate: EOT → first audible ≤ 1.5 s median = PASS** (0.81 s, ~1.8×
inside the bar), and materially faster than the accepted local voice.
Consistent across both sessions and all 9 turns.

### Cost (this evidence)

Session 1 estimate **~$0.043**, session 2 **~$0.007** (audio-duration ×
official rates; `usageMetadata` not yet wired). Two real conversation
sessions cost **~5 cents** total.

---

## OPERATOR ATTEMPT #3 — SULAFAT: PASS / VOICE OPERATOR-CONFIRMED

Third real-hardware session, `--voice Sulafat` (now the probe default),
`m2_6a_probe_results_20260910T215423Z.json` (+ `…_recomputed.json`). ~64 s,
**4 reconstructed user turns** (one fragmented — see below). After real
natural use the operator said:

> **"I like this voice, we keep it."**

**Decision recorded:** **VOICE = `Sulafat` — OPERATOR-CONFIRMED**, the
frozen current NeXa cloud voice baseline. Model / VAD / AEC / Smart Turn /
barge-in / latency settings / audio format / chunk size / system
instruction / conversation behaviour **unchanged**.

### Turn-local latency (metric corrected again)

`eot_to_first_audio_played_s` via the raw `_pairs` was
`[0.7564, 0.8771, 1.9299, 0.7405, 0.7361]`. The **1.93 s value is not a
real latency** — same class of cross-boundary pairing bug as the earlier
barge-in metric. **Turn 3 was fragmented speech**: the operator spoke it
in **two VAD segments** with a ~0.45 s mid-utterance pause (VAD closed at
t = 26.51 s, the operator resumed at t = 26.96 s, true end t = 27.70 s).
Silero + Smart Turn correctly treated it as one continued utterance and
Gemini responded 0.70 s after the *true* (final) end — the naive
`_pairs` matched the **first** segment's `LOCAL_VAD_EOT` (26.51) against
the audio that answered the whole utterance (28.44) → 1.93 s.

**Fix (probe `derive()` only):** `Timeline.turns()` reconstructs user
turns; consecutive `LOCAL_VAD_START`s before the turn's first
`FIRST_AUDIO_RECEIVED` are merged as **fragments** (the turn is flagged
`fragmented` and measured from its **final** `LOCAL_VAD_EOT`); every turn
is bounded by the next turn's first VAD-start so no event is paired
across turns. `--recompute` applies it to existing timelines.

| Turn | Status | EOT → first audible (s) |
|---|---|---|
| 1 | clean | 0.756 |
| 2 | clean (this turn interrupted turn 1's reply) | 0.877 |
| 3 | **fragmented (2 VAD segments)** — measured from final EOT | 0.741 |
| 4 | clean | 0.736 |

**Turn-local EOT → first audible: [0.756, 0.877, 0.741, 0.736], median
≈ 0.749 s, mean ≈ 0.777 s.** The "outlier" was **fragmented speech
surfacing as a metric artifact** — not Smart Turn misbehaviour (it did
the right thing), not an interruption, not a delayed server response, not
unknown. **No tuning warranted** (operator judged the conversation
excellent; corrected per-turn latency ~0.75 s).

Combined with attempts #1/#2: EOT → first audible is ~**0.75–0.81 s
median** across three real sessions — R0030 ≤ 1.5 s gate **PASS**, and
faster than local voice.

### Barge-in (Sulafat session)

1 real barge-in candidate (turn 2 interrupting turn 1's reply):
`vad_start → LOCAL_PLAYBACK_STOPPED` = **2.0 ms**, `→ BOT_AUDIO_STOPPED` =
**3.5 ms**, `LOCAL_PLAYBACK_STOPPED − last interruption frame` =
**−28.3 ms** (local speaker silent ~28 ms *before* the server-round-trip
frame). Consistent with attempt #2 (medians 2.3 / 4.0 / −27.2 ms).
**PASS.**

### AEC (Sulafat session)

`ever_started=true`, `failure_count=0`, `chunks_dropped=0`,
`AEC_REF_ACTIVE` at t = 1.23 s (before any speech). `AEC_REF_DOWN` at
t = 63.92 s, ~33 ms before `SESSION_STOP` (Ctrl+C teardown) →
`active_at_end=false` is **expected teardown, not a failure**. Healthy.

### #5465 (Sulafat session)

One NOT_READY window, 540 ms, at startup (`LLM_READY` at t = 1.88 s).
First `LOCAL_VAD_START` at t = 4.44 s — **2.56 s after the window
closed**. **No user speech in the window.** Same verdict:
*KNOWN PRODUCTION RISK STILL OPEN, NOT OBSERVED AS USER-AUDIO LOSS.*

### Cost (Sulafat session)

~$0.0195 (~2 cents) for ~64 s.

---

## C6 — INPUT-TRANSCRIPTION vs FIRST AUDIO (turn-local; Sulafat session)

The Sulafat session is the first with the C6 wrappers installed
(`INPUT_TRANSCRIPTION_RAW_FIRST` = first raw input-transcription chunk of
a turn; `INPUT_TRANSCRIPTION_PUSHED` = Pipecat's aggregated user sentence
— end-of-sentence marker OR its 0.5 s timeout flush). Analysed
**turn-locally** (`c6` block in the recomputed JSON); the earlier raw
`_pairs` produced a spurious **19.84 s** value by pairing turn 3's
second-segment PUSHED against **turn 4's** first audio.

### Per turn

| Turn | Status | EOT→RAW | EOT→PUSHED | EOT→audio recv | RAW→audio recv | PUSHED→audio recv |
|---|---|---|---|---|---|---|
| 1 | clean | 0.178 | 0.210 | 0.714 | **+0.536** | **+0.503** |
| 2 | clean (interrupted T1) | 0.230 | 0.731 | 0.876 | **+0.646** | **+0.145** |
| 3 | fragmented — **EXCLUDED** | 0.216 | 0.716 | 0.703 | +0.488 | −0.013 |
| 4 | clean | 0.196 | 0.698 | 0.687 | **+0.491** | **−0.011** |

(Positive = the transcript reached NeXa before the first response audio
for the same turn. Turn 3 excluded: fragmented / merged utterance.)

### C6 statistic (valid turns 1, 2, 4)

- **n valid turns = 3** (turn 3 excluded — fragmented user speech).
- **RAW transcript preceded first audio: 3 / 3.** Margins
  `[0.536, 0.646, 0.491] s` → **median 0.536 s, min 0.491, max 0.646**.
- **PUSHED (aggregated) transcript preceded first audio: 2 / 3.** Margins
  `[0.503, 0.145, −0.011] s` → median 0.145 s, min −0.011, max 0.503.
  (Turn 4's aggregated sentence landed ~11 ms *after* first audio — it hit
  the aggregator's 0.5 s timeout flush rather than an end-of-sentence
  marker.)

### C6 ARCHITECTURAL CONCLUSION

The three questions R0030 asks are **separate**, and the evidence answers
them differently:

1. **Is the transcript locally available in time?**
   **RAW: YES** — the first raw chunk lands at EOT + ~0.2 s and precedes
   the first response audio by **~0.5 s, consistently (3/3)**.
   **PUSHED: not reliably** — EOT + 0.21 s to EOT + 0.73 s depending on
   punctuation vs the 0.5 s flush; 1/3 valid turns it arrived *after*
   first audio.

2. **Has Gemini already begun generating internally by then?**
   **INFERENCE: almost certainly YES.** `activity_end` is sent at EOT and
   Gemini 3.x "acts on it immediately with no additional wait" (R0030 C3).
   The RAW transcription (~EOT + 0.2 s) is a *parallel* server output —
   the server transcribes and generates concurrently. Nothing in the
   evidence suggests "transcript arrived" implies "generation has not
   started"; the timing suggests the opposite.

3. **Can NeXa actually influence the SAME response?**
   **NOT DEMONSTRATED, and the mechanism is doubtful.** To steer *this*
   response, NeXa would have to inject an instruction (e.g. "answer in
   Polish") that reaches Gemini after the transcript but before the
   response is committed, and that Gemini honours mid-turn. But
   `activity_end` already closed the turn ~0.2–0.7 s earlier; Pipecat's
   `_send_user_text` goes via `realtimeInput` where "turn completion is
   automatically inferred", so a post-`activity_end` text most likely
   opens a **new** turn. There is **no evidence** a mid-turn instruction
   modifies the in-flight response. **Timing headroom ≠ steerability.**

**Therefore, for ADR-0004:** NeXa's per-turn `ResponseLanguageResolver`
**cannot be assumed to steer the same cloud response.** Options:

- **A (recommended for v1):** let Gemini mirror the spoken language
  natively (it did this well — the operator had zero language
  complaints). NeXa's resolver still runs on the transcript for canonical
  history language metadata and sticky-preference tracking; the
  `CloudContextSnapshot` system instruction carries the current preference
  so it applies **from the next turn**.
- **B (if strict per-turn authority is required):** deliberately delay
  `activity_end` until a fast local language-ID runs on the buffered
  utterance audio — a *measured* added latency, decided in ADR-0004.
- **C:** parallel local whisper.cpp — heavier, contradicts "no second STT
  in cloud mode"; rejected unless B is insufficient.

**Is the C6 evidence sufficient to start ADR-0004? YES.** The timing
question is answered (RAW transcript available ~0.5 s before audio,
consistent) and the steerability question is answered enough to decide:
proceed with **Option A** as the default, **Option B** documented as the
measured-latency fallback. Caveat: n = 3 valid turns from one session —
the C6 wrappers stay in place, so every future ordinary probe run adds
data with zero extra effort.

---

## AUTHENTICATION SMOKE RESULT

`docs/research/m2_6_cloud_realtime_voice/m2_6a_connect_smoke.py` — smallest
possible authenticated Gemini Live connection, **no microphone, no
speaker, no conversation**.

```
google-genai   : 2.22.0
model          : gemini-3.1-flash-live-preview
key            : present (length 53, not shown)
PASS: Live WebSocket opened + setup complete in 449 ms
PASS: clean disconnect
EXIT=0
```

**MEASURED FACT (2026-09-10):** credential accepted · model
`gemini-3.1-flash-live-preview` exists and is reachable · `client.aio.live.connect`
opened the WebSocket and completed the `BidiGenerateContentSetup`
handshake in **449 ms** · clean context-manager exit · no auth / quota /
billing / model error · the key value was never printed (the script
redacts it and only reports length). Google project `591383313359`.

No hardware debugging was needed — authentication is healthy, so M2.6A can
proceed straight to the audio session.

---

## SECRET STORAGE MECHANISM

**NeXa has no canonical local-secret store today** — `src/nexa/config.py`
is pure `os.environ.get(...)`, and `.gitignore` already ignores `.env` /
`.env.*` (with `!.env.example`). So, per the task instruction, for this
spike:

- **Location (outside the git repo):** `~/.config/nexa/secrets/gemini.env`
- **Permissions:** directory `~/.config/nexa` and `~/.config/nexa/secrets`
  = `700`; file `gemini.env` = `600` (verified: `-rw-------`).
- **Variable name:** `NEXA_GEMINI_API_KEY`
- **File contents (shape only):** a header comment naming the Google
  project, plus `NEXA_GEMINI_API_KEY=<value>`. The value is **not**
  reproduced anywhere in the repo, this report, the probe, the JSON
  metrics, or any commit.
- **How the probe reads it:** `os.environ["NEXA_GEMINI_API_KEY"]` first;
  if unset, it parses the `~/.config/nexa/secrets/gemini.env` line itself
  (so the operator can launch without a manual `source`). Any accidental
  occurrence of the key in an error string is redacted before printing.
- **Repo documents only the variable name + path**, never the value
  (`docs/research/m2_6_cloud_realtime_voice/README.md`, this report).
- **Not** added to shell history (written via the editor tool, not
  `echo`); **not** committed; **not** logged.

A canonical NeXa secret mechanism (env + optional XDG file loader, region
awareness) is an **ADR-0004 / M2.6B** design item.

---

## DEPENDENCIES INSTALLED + VERSION / WEIGHT DELTA

Pipecat 1.8.1's `[google]` extra requires three packages; only
**`google-genai`** is imported by `GeminiLiveLLMService`
(`google-cloud-speech` / `google-cloud-texttospeech` are for Google STT /
TTS, not Gemini Live). So the **minimum** was installed:

```
.venv/bin/pip install 'google-genai>=1.68.0,<3'
```

(`>=1.68.0,<3` is exactly Pipecat 1.8.1's own constraint.)

**Installed (research venv only — `pyproject.toml` / lock files
untouched):**

| Package | Version | Note |
|---|---|---|
| **google-genai** | **2.22.0** | the one Pipecat imports |
| google-auth | 2.58.0 | new (transitive) |
| requests | 2.34.2 | new |
| urllib3 | 2.7.0 | new |
| charset-normalizer | 3.5.1 | new |
| cryptography | 50.0.1 | new |
| cffi | 2.1.1 | new |
| pycparser | 3.0 | new |
| pyasn1 | 0.6.4 | new |
| pyasn1-modules | 0.4.2 | new |
| tenacity | 9.1.4 | new |
| **websockets** | **17.1 → 16.1.1** | **DOWNGRADE** — `google-genai 2.22.0` pins `websockets<17.0,>=13.0.0`; Pipecat allows `websockets>=13.1` so 16.1.1 satisfies both. |

- **venv size:** 614,012,137 → 647,684,026 bytes = **+33,671,889 B ≈
  +32.1 MiB** (~+5 %). Package count 68 → 79.
- **`import google.genai`:** ~**608 ms** cumulative (one-time).
- **`pip check`:** *"No broken requirements found."*
- **Regression check (websockets downgrade):** `pipecat`,
  `pipecat.transports.local.audio`, `pipecat.audio.vad.silero`,
  `pipecat.services.google.gemini_live.llm`, `google.genai`,
  `nexa.voice.runtime`, `nexa.voice_tts.bargein_wiring`,
  `nexa.conversation` all import cleanly; `tests/test_voice_architecture.py`
  + `tests/test_bargein_wiring_m2_5b.py` → **17 passed**. (Full suite not
  re-run — no `src/` or `tests/` change; last green at `788a64d`: pytest
  732 / unittest 739.)
- **No tracked dependency file changed** — `git status` shows nothing for
  `pyproject.toml` / lock / requirements. Ratifying `google-genai` as a
  project dependency (and pinning `websockets`) is an **ADR-0004** item.

---

## GEMINI LIVE CONFIG USED

`m2_6a_gemini_live_probe.py`:

| Setting | Value | Why |
|---|---|---|
| model | `gemini-3.1-flash-live-preview` | the frozen v1 provider/model (R0030) |
| `voice` | attempt #1/#2: Pipecat default (`Charon`); **now `Sulafat`** (`--voice`, default) | operator asked for female/cozy/warm; `Sulafat` = "Warm". `Settings(voice=…)` → `prebuilt_voice_config.voice_name`. A NeXa user preference. |
| `modalities` | `AUDIO` | native audio-to-audio |
| `vad` | `GeminiVADParams(disabled=True)` | **server VAD OFF** — local Silero is the turn authority (HYBRID) |
| turn markers | `activity_start` / `activity_end` from Silero `UserStarted/StoppedSpeakingFrame` | Pipecat sends these automatically in local-VAD mode |
| input transcription | ON | Pipecat's `GeminiLiveLLMService` enables `input_audio_transcription` unconditionally |
| output transcription | ON | …and `output_audio_transcription` unconditionally |
| `context_window_compression` | `enabled=True` | unlimited session duration (needed for the reconnect phase) |
| `system_instruction` | the minimal spike role card (below) | not NeXa's identity |
| **`inference_on_context_initialization`** | **`False`** | **attempt-#1 fix** — init seed with `turn_complete=False`, service becomes ready with **no opening greeting** |
| **`LLMContext(messages=[])`** | **empty** | **attempt-#1 fix** — system instruction only via `system_instruction=`; empty context → `_create_initial_response` early-returns ready, no seed, no Pipecat "converting to a user message" warning |
| **startup kickoff** | **one `LLMRunFrame()`** queued by `_kickoff()` after the socket connects | **attempt-#1 fix** — `LocalAudioTransport` has no `on_client_connected`; without this the service never initialises its context or becomes `_ready_for_realtime_input` |
| `user_audio_preroll_secs` | `None` (auto) | Pipecat auto-sizes from Silero `start_secs` + margin |
| mic PCM to Gemini | 16-bit LE, mono, **16 kHz** | Gemini native input |
| cloud audio from Gemini | **24 kHz** PCM | Gemini native output |
| input chunk target | **20 ms** | best-practices (R0030 C2) |
| Silero `stop_secs` | `0.5` | > Pipecat's 0.2 s default; matches the upstream locally-driven-turns example |

**Spike system instruction (verbatim):**

> "You are the temporary realtime conversation engine for an assistant
> called NeXa. Speak naturally and concisely. Normally answer in the
> language the user is currently speaking. If explicitly asked to use
> Polish or English, follow that request. You do not hold long-term
> memory or the user's identity; the host system does."

No persona upload, no memory, no seeded private history. Scripted
non-sensitive conversation only.

---

## M2.6A PROBE ARCHITECTURE

`docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py` — a
disposable research harness. **Imports NeXa helpers READ-ONLY**
(`nexa.voice.config.LocalAudioConfig`, `nexa.voice.device.find_device_index`,
`nexa.voice.aec.AecReferenceHealth`,
`nexa.voice_tts.aec_reference.AecReferenceFeeder`). No `src/nexa/**`
change.

**Pipeline (Pipecat), in order:**

```
transport.input()            LocalAudioTransport, 16 kHz in / 24 kHz out,
                             respeaker in / usb_speaker out (by name)
  → _SpikeMetrics("upstream")   timeline taps + #5465 NOT_READY audio accounting
  → user_aggregator            LLMContextAggregatorPair(realtime_service_mode=True),
                               user_params.vad_analyzer = SileroVADAnalyzer(stop_secs=0.5)
  → GeminiLiveLLMService        gemini-3.1-flash-live-preview, server VAD off
  → _SpikeMetrics("downstream") first cloud audio, output transcription, interruptions
  → AecReferenceFeeder          tees TTSAudioRawFrame @24 kHz → aplay -D plug:respeaker
                                (XVF3800 far-end reference; health → AecReferenceHealth)
  → transport.output()          speaker
  → assistant_aggregator        collects what was actually spoken
```

- **One monotonic clock** (`Timeline`): every event is `(t, name, extra)`;
  derived latencies pair each `A` mark with the next `B` after it (handles
  multi-turn sessions).
- **#5465 instrumentation** (upstream tap): on every `InputAudioRawFrame`
  it reads `GeminiLiveLLMService._ready_for_realtime_input`; logs
  `LLM_READY` / `LLM_NOT_READY` transitions; accumulates
  `mic_audio_ms_presented` vs `mic_audio_ms_while_not_ready` and
  `not_ready_window_count`. Any non-zero "while not ready" value is audio
  Pipecat 1.8.1 **silently dropped** (bare `return`) — surfaced in the
  summary and JSON, **never** folded into a PASS.
- **AEC health**: `AecReferenceHealth.on_change` marks `AEC_REF_ACTIVE` /
  `AEC_REF_DOWN` on the timeline; the summary + JSON report
  `ever_started` / `active_at_end` / `failure_count` /
  `frames_mirrored` / `chunks_dropped`.
- **Cost estimate**: from `mic_audio_ms` / `cloud_audio_ms` × the official
  ≈25 tok/s × the official $3.00 / $12.00 per 1M (R0030 C4).
  `usageMetadata` wiring is a follow-up (Pipecat maps it to
  `LLMTokenUsage`; the probe currently estimates from audio duration).
- **Safety rails**: hard **15-minute** wall-clock session cap; Ctrl+C /
  SIGTERM → graceful stop → JSON written → summary printed. `--dry`
  constructs the whole cloud-side object graph (Settings, `LLMContext`,
  aggregator pair, `GeminiLiveLLMService`) with **no** device and **no**
  network, to validate the API surface.
- **No API key value** in any print, log, or the JSON.

**`--dry` result (this task):** all cloud-side objects construct against
the real installed `pipecat 1.8.1` + `google-genai 2.22.0`. Notable log:
`LLMUserAggregator … realtime mode — mutated turn strategies: dropped
['TranscriptionUserTurnStartStrategy']; set wait_for_transcript=False on
['TurnAnalyzerUserTurnStopStrategy']` — i.e. `realtime_service_mode=True`
reconfigures the aggregator's turn strategies for a realtime service (it
also loads a bundled "Local Smart Turn v3" ONNX model for the stop
strategy — noted, not a blocker).

---

## PIPECAT #5465 — observation from the evidence

The probe instruments the bug (does **not** fix it — that is M2.6B /
ADR-0004): `LLM_READY`/`LLM_NOT_READY` transitions timestamped;
`mic_audio_ms_while_not_ready` / `mic_chunks_while_not_ready` /
`not_ready_window_count` counters; JSON `pipecat_5465` block.

**What the evidence shows:**

| | Session 1 | Session 2 |
|---|---|---|
| `not_ready_window_count` | 1 | 1 |
| `mic_audio_ms_while_not_ready` | 620 ms | 520 ms |
| `LLM_NOT_READY` → `LLM_READY` | t = 1.38 → 2.00 s | t = 1.37 → 1.89 s |
| First `LOCAL_VAD_START` | t = **20.34 s** | t = **4.69 s** |

The single NOT_READY window is the **startup window** — between the
pipeline starting to receive mic frames and the `LLMRunFrame` kickoff
making the service ready (~0.5–0.6 s). In **both** sessions the operator's
first speech began **many seconds after** the window closed (18 s / 2.8 s
later). **No user speech occurred during the NOT_READY window.**

**Verdict: KNOWN PRODUCTION RISK STILL OPEN, NOT OBSERVED AS USER-AUDIO
LOSS IN THIS SESSION.** The ~0.5–0.6 s of dropped audio was startup
ambience, not speech. The production fix (a not-ready send buffer +
proactive `GoAway` reconnect) remains an M2.6B / ADR-0004 item. **No
tools** were registered, so the worst #5465 case (an unanswered tool
call) cannot occur here.

---

## BARGE-IN VERDICT (metric corrected 2026-09-10)

The attempt-#2 JSON's original `vad_start_to_local_playback_stopped_s`
(e.g. `[21.5382, 8.2028, 0.0022, …]`) and
`server_interrupted_to_playback_stopped_s` (`[…, 10.65, …, 9.31, …]`) were
**semantically invalid**: the generic "pair each `LOCAL_VAD_START` with
the next `LOCAL_PLAYBACK_STOPPED`" also matched **ordinary user turns**
where the bot was not speaking, so it paired a turn's VAD-start with a
normal `TURN_COMPLETE` 8–21 s later. Also, on realtime turn-start the
Pipecat user aggregator broadcasts an `InterruptionFrame`
**unconditionally** (its standard "user started → stop the bot" behaviour),
so every turn produced an `INTERRUPTION_DOWNSTREAM` mark even with nothing
playing.

**Fix (research probe metric derivation only — `Timeline` in the probe):**
track a `bot_is_speaking` state (`FIRST_AUDIO_PLAYED` → True;
`LOCAL_PLAYBACK_STOPPED` / `BOT_AUDIO_STOPPED` / `TURN_COMPLETE` → False).
A `LOCAL_VAD_START` is a **barge-in candidate only while
`bot_is_speaking`**. `--recompute <json>` re-derives from an existing
timeline (no cloud call). `SERVER_INTERRUPTED` renamed
`INTERRUPTION_DOWNSTREAM` (it can be the local broadcast OR Gemini's
`serverContent.interrupted` — indistinguishable in the frame stream).

**Recomputed from the existing evidence:**

| | Session 1 barge-ins | Session 2 |
|---|---|---|
| Real barge-in candidates (bot was playing) | **5** (turns C–G; the 2 bogus `[21.5, 8.2]` values gone) | **0** (plain 2-turn chat) |
| `vad_start → LOCAL_PLAYBACK_STOPPED` (control-plane) | 2.2, 2.5, 2.6, 1.7, 2.3 ms → **median 2.3 ms** | — |
| `vad_start → BOT_AUDIO_STOPPED` (transport) | 4.0, 4.6, 4.5, 3.0, 4.0 ms → **median 4.0 ms** | — |
| `LOCAL_PLAYBACK_STOPPED − last InterruptionFrame` | −27.9, −27.5, −27.2, −22.4, −24.1 ms → **median −27.2 ms** | — |

- **R0030 gate: local VAD-start → playback stop ≤ 100 ms = PASS** — by a
  factor of ~25–40. Consistent with R0028's ~28 ms local media-stop
  (faster here — the interruption path is the aggregator's
  `broadcast_interruption()` → output-transport cancel, no `aplay` kill).
- The **negative** `LOCAL_PLAYBACK_STOPPED − last InterruptionFrame`
  (~−27 ms) is the important architectural fact: **NeXa's local playback
  authority silenced the speaker ~27 ms *before* the second (later, most
  likely server-round-trip) `InterruptionFrame` arrived** — the local
  barge-in path does not wait on the cloud. Matches ADR-0003 / R0028
  "NeXa keeps final authority over the physical speaker."
- Operator judged barge-in **EXCELLENT** ("interrupt naturally and
  immediately"). Machine evidence agrees.

---

## EVENT ORDERING FINDINGS (R0030 C6 — language routing)

**Historical (attempts #1/#2):** those two JSONs contain **no
input-transcription timestamps** — verified from Pipecat 1.8.1 source,
`_push_user_transcription` pushes the `TranscriptionFrame`
`FrameDirection.UPSTREAM` and `LLMUserAggregator` **consumes** it before
any metrics tap. Spike-only wrappers on `_handle_msg_input_transcription`
(`INPUT_TRANSCRIPTION_RAW_FIRST`) and `_push_user_transcription`
(`INPUT_TRANSCRIPTION_PUSHED`) were added.

**Now answered — see the turn-local analysis in
*C6 — INPUT-TRANSCRIPTION vs FIRST AUDIO (turn-local; Sulafat session)*
above.** In short: the RAW input transcription reaches NeXa ~0.5 s before
the first response audio in **3/3 valid turns** (median 0.54 s); the
aggregated (PUSHED) transcript only in 2/3. But this is *timing headroom*,
not *steerability* — the C6 architectural conclusion (three separate
questions) says NeXa cannot be assumed to influence the **same** cloud
response, so ADR-0004 should default to Gemini's native language mirroring
(Option A) with a delayed-`activity_end` + local language-ID fallback
(Option B). **C6 is now a SUFFICIENT input to start ADR-0004** (n = 3
valid turns from one session; the wrappers stay in place for more).

---

## AUTOMATED / NON-OPERATOR RESULTS (agent-run, 2026-09-10)

| Check | Result |
|---|---|
| Pipecat `GeminiLiveLLMService` static audit (`inspect_pipecat_gemini_live.py`) | #5465 silent guards **PRESENT**; `GoAway` **ABSENT**; session resumption + reconnect **PRESENT**; Gemini-3.x async tools **NOT SUPPORTED** (unchanged from R0030) |
| `google-genai` install | 2.22.0; +32 MiB venv; `websockets` 17.1→16.1.1; `pip check` clean; no tracked dep file changed |
| Import smoke (pipecat + gemini_live + google.genai + nexa) | all OK |
| Spot-check tests (`test_voice_architecture`, `test_bargein_wiring_m2_5b`) | **17 passed** |
| **Authenticated Live-API connectivity smoke** | **PASS** — setup handshake **449 ms**, clean disconnect, no secret leak |
| Probe `--dry` (full cloud-side object construction) | **PASS** — Settings + LLMContext + aggregator pair + GeminiLiveLLMService build against real installed libs |
| `ruff check docs/research/m2_6_cloud_realtime_voice/` | All checks passed |
| `py_compile` all three scripts | OK |
| Secret scan of staged content (`AIza`, `AQ.`, `api[_-]?key=…`, `Authorization:`, `Bearer`, `NEXA_GEMINI_API_KEY=<value>`) | **none** |
| `git diff -- src/nexa` / `git diff -- tests` | **empty** |

---

## AEC STATE — verdict from the evidence

The probe wires the **existing** `AecReferenceFeeder` on the cloud bot
audio (`TTSAudioRawFrame` @24 kHz) → `aplay -D plug:respeaker` — the same
XVF3800 far-end reference path R0028/R0029 proved necessary, fed from the
cloud reply instead of Piper.

| | Session 1 | Session 2 |
|---|---|---|
| `AEC_REF_ACTIVE` | t = 1.25 s (before any speech) | t = 1.24 s |
| `ever_started` | **true** | true |
| `failure_count` | **0** | **0** |
| `frames_mirrored` | 298 | 59 |
| `chunks_dropped` | 94 (drop-oldest queue shedding under load — R0028 documents this as tolerable; the feed stays fresh) | 0 |
| `AEC_REF_DOWN` | t = 133.42 s, **~33 ms before `SESSION_STOP`** (Ctrl+C teardown) | t = 36.57 s, just before `SESSION_STOP` |
| `active_at_end` | false | false |

**Verdict: AEC feed healthy.** `active_at_end=false` is **expected
teardown** (it goes down during the Ctrl+C stop sequence, essentially
simultaneously with `SESSION_STOP`), **not an AEC failure** —
`failure_count=0` confirms. Barge-in worked cleanly across all 5 real
interruptions and the operator rated it EXCELLENT; no spurious
self-echo triggers were reported.

---

## TOKEN / COST TELEMETRY

Estimate from audio duration × official 2026-09-10 rates (R0030 C4: input
audio $3.00/1M ≈ 25 tok/s, output audio $12.00/1M); `usageMetadata` not
yet wired.

| | mic audio | cloud audio | est. cost |
|---|---|---|---|
| Session 1 (~133 s) | 131.9 s | 109.2 s | **~$0.043** |
| Session 2 (~37 s) | 35.1 s | 15.7 s | **~$0.007** |

**Two real conversation sessions ≈ 5 cents.** A 15-minute session's rough
ceiling is well under $1.

---

## VOICE CONFIGURATION & RECORD

**Chronology (accurate history — earlier steps preserved):**

1. Attempts #1/#2 ran with **Pipecat's default voice** (`Charon`,
   "Informative"). After the successful attempt-#2 conversation the
   operator said this voice was **not preferred** and asked for a
   **female / cozy / pleasant / warm** voice.
2. **`Sulafat`** ("Warm") was proposed as the best semantic match.
3. The operator listened to other candidates, **including `Achernar`**
   ("Soft"), and the recorded alternatives `Vindemiatrix` ("Gentle") /
   `Aoede` ("Breezy").
4. The operator then **used `Sulafat` in a real Gemini Live conversation**
   (session `…215423Z`, `--voice Sulafat`).
5. The operator **explicitly selected `Sulafat`**:
   *"I like this voice, we keep it."*
6. **`Sulafat` is now OPERATOR-CONFIRMED and the frozen current NeXa
   cloud voice baseline.**

**Mechanism (VERIFIED):**

- Official docs (`ai.google.dev/gemini-api/docs/speech-generation`,
  2026-09-10): `Sulafat` style descriptor = **"Warm"**. (The style table
  lists no gender; female identification is the operator-supplied basis
  from Google/Firebase voice metadata.)
- Installed Pipecat 1.8.1 source: `GeminiLiveLLMService.Settings(voice="Sulafat")`
  → `_connect()` maps it to
  `generation_config.speech_config.voice_config.prebuilt_voice_config.voice_name = "Sulafat"`
  — exactly Google's `speech_config → voice_config → prebuilt_voice_config
  → voice_name`. No invented API.
- Probe: `DEFAULT_VOICE = "Sulafat"` + `--voice <name>`. **Voice is a NeXa
  user preference**, kept configurable and separate from the model / VAD /
  AEC / Smart Turn / barge-in / audio format / chunk size / latency
  settings / system instruction (all unchanged) and from the Gemini
  identity. Future NeXa architecture must keep voice a user preference,
  not part of the Gemini provider.

---

## FAILURES / ANOMALIES

- **Attempt #1 = FAIL (silent after user speech)** — missing `LLMRunFrame`
  kickoff; `GeminiLiveLLMService` never became `_ready_for_realtime_input`
  (which, server VAD off, gates `activity_start` / audio / `activity_end`).
  Fixed in the probe; proven by the no-mic lifecycle smoke.
- **Barge-in metrics (attempt-#2 JSONs)** were semantically invalid (naive
  "next event" pairing). Fixed in `derive()` (`bot_is_speaking` gate) +
  recomputed from history.
- **Latency "1.93 s outlier" (Sulafat session)** was **fragmented user
  speech** (a mid-utterance pause = 2 VAD segments) surfacing as a
  cross-boundary pairing artifact. Fixed by turn-local reconstruction
  (`Timeline.turns()`); real per-turn latency was 0.74 s.
- **C6 raw `_pairs` "19.84 s"** — turn 3's second-segment PUSHED
  transcript paired against turn 4's first audio. Fixed by the turn-local
  `c6` analysis.
- **C6 (attempts #1/#2)** was not instrumented — the input `TranscriptionFrame`
  is pushed upstream and consumed by the aggregator before any tap.
  Wrappers added; the Sulafat session has the data.
- **#5465 silent-drop guards present** (known). Instrumented; startup
  window only; no user-audio loss observed in any of the three sessions.
- **`websockets` 17.1 → 16.1.1** transitive downgrade (from `google-genai`;
  within Pipecat's `>=13.1`; `pip check` clean; spot-check tests pass).
  Pin in ADR-0004.
- **No `GoAway` handling in Pipecat 1.8.1** (R0030). Reconnect / lifetime
  testing **deliberately deferred** to M2.6B production hardening — not
  needed to prove feasibility; quota preserved.
- **Bundled "Local Smart Turn v3" ONNX model** loads via the realtime
  aggregator's stop strategy — a different, base-bundled model, not the
  ADR-0003 D10-rejected `[local-smart-turn]` extra. Not a blocker; note
  for ADR-0004 whether to force a pure-VAD stop strategy.

---

## WHAT REMAINS (post-PASS)

1. **`usageMetadata` wiring** for exact cost (small follow-up; the
   audio-duration estimate is adequate for a spike).
2. **More C6 data** — every future ordinary probe run adds turn-local C6
   samples automatically (wrappers in place); n = 3 valid turns today.
3. **ADR-0004** — provider boundary; `google-genai` as a tracked
   dependency (+ pin `websockets`); **production language-routing
   authority** (default to Gemini native mirroring, Option A; delayed
   `activity_end` + local language-ID as Option B); the #5465 not-ready
   buffer + `GoAway`/age-timer reconnect; `RealtimeVoiceProvider` /
   `CloudContextSnapshot` / `ConversationRouter`. **Not started.**
4. **M2.6B** production. **Not started.**

---

## FILES CHANGED

### This commit (Sulafat OPERATOR-CONFIRMED + turn-local C6 / latency)

**Changed (probe / report / evidence only — no `src/nexa/**`, no deps):**

- `docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py` —
  **turn-local metric derivation**: `Timeline.turns()` reconstructs user
  turns (merging fragmented multi-VAD-segment utterances, bounding each by
  the next turn's VAD-start); `derive()` adds
  `eot_to_first_audio_*_turnlocal_s` and a `c6` block
  (`c6_turnlocal()`) that reports RAW/PUSHED transcript → first-audio
  margins per valid turn and excludes fragmented turns with a stated
  reason. `--recompute` applies it to existing timelines. (Earlier this
  file also gained the `bot_is_speaking` barge-in gate, `--recompute`,
  the C6 wrappers, and `DEFAULT_VOICE="Sulafat"` + `--voice`.)
- `docs/research/m2_6_cloud_realtime_voice/m2_6a_probe_results_20260910T215423Z.json`
  — **the Sulafat operator session** (added).
- `…_recomputed.json` (×3) — all three sessions recomputed with the
  turn-local logic.
- `docs/reports/R0031_…md` — this report: OPERATOR ATTEMPT #3 (Sulafat) =
  PASS + **VOICE OPERATOR-CONFIRMED**; turn-local latency + outlier
  analysis; **turn-local C6 analysis + architectural conclusion**; voice
  record chronology; ADR-0004-readiness note.
- `docs/research/m2_6_cloud_realtime_voice/README.md`,
  `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — VOICE = Sulafat /
  OPERATOR-CONFIRMED; C6 result; ADR-0004 next.

### Earlier in M2.6A

`f920315` / `b3c7c32` — R0031 v1 + connectivity smoke + probe v1 + R0030
Phase-0 corrections. `d15e785` / `377e99a` — attempt-#1 diagnosis + fix
(`LLMRunFrame` kickoff). `ddb7c70` / `3346256` — attempt-#2 PASS +
corrected barge-in metrics + Sulafat proposed.

**Outside the repo (not committed, cannot be):**
`~/.config/nexa/secrets/gemini.env` — the operator's key, `600`.

**Not changed (verified):** all `src/nexa/**`, all `tests/**`,
`pyproject.toml` / lock files. Local voice baseline (R0029) untouched;
`bargein_enabled` default-off untouched.

## CHECKS (this commit)

`ruff check docs/research/m2_6_cloud_realtime_voice/` → All checks passed ·
`py_compile` → OK · `--dry` (default Sulafat) → PASS (`voice = Sulafat`;
`Settings.voice` carries through; `_connect` maps `voice_name=`) ·
`--recompute` on **all 3** existing JSONs (turn-local) → PASS
(Sulafat session: 4 turns, 3 valid for C6; turn-local EOT→audible median
0.7485 s; barge-in 2.0 ms; RAW→first-audio [0.536, 0.646, 0.491],
3/3 before audio; PUSHED 2/3) ·
**no Gemini conversation run; no lifecycle smoke re-run** · `git diff
--check` → clean · secret scan (`AIza…` / `AQ.…` / key leading chars /
`api_key=` / `Authorization:` / `Bearer` / `NEXA_GEMINI_API_KEY=<value>`)
over staged content → **none** · `git diff -- src/nexa` → **empty** ·
`git diff -- tests` → **empty** · no tracked dependency file changed ·
full suite not re-run (no `src`/`tests` change).

## COMMIT HASH

- `f920315` / `b3c7c32` — R0031 v1 + connectivity smoke + probe v1 + R0030
  Phase-0 corrections.
- `d15e785` / `377e99a` — attempt-#1 diagnosis + `LLMRunFrame` fix.
- `ddb7c70` / `3346256` — attempt-#2 PASS + corrected barge-in metrics
  (`--recompute`) + C6 instrumentation + `Sulafat` proposed.
- `<PENDING — this commit>` — **Sulafat OPERATOR-CONFIRMED**; turn-local
  reconstruction (latency + C6); turn-local C6 analysis + architectural
  conclusion.

This hash-record note is finalised by the immediately-following commit
(R0026–R0030 pattern). Prior tip: `3346256`.

## GIT STATUS

Branch `main`, **not pushed**. `git diff --check` clean. No `src/` /
`tests/` / tracked-dependency change. This commit changes only the probe +
R0031 + README + `CURRENT_STATE` + `ROADMAP`, adds the Sulafat session
JSON, and refreshes the 3 recomputed JSONs. The Gemini key lives only at
`~/.config/nexa/secrets/gemini.env` (outside the repo).

## R0031 FINAL VERDICT

**M2.6A CLOUD REALTIME VOICE FEASIBILITY: PASS / OPERATOR-CONFIRMED
(2026-09-10). VOICE `Sulafat`: OPERATOR-CONFIRMED, frozen cloud voice
baseline.**

- Attempt #1 = infrastructure bug (missing `LLMRunFrame` kickoff), fixed.
- Attempt #2 (Pipecat default voice) = real PL/EN conversation, operator
  **EXCELLENT** / "essentially immediate" / *"mega super"*; only ask was a
  female/cozy/warm voice.
- Attempt #3 (`Sulafat`) = real natural conversation, operator: *"I like
  this voice, we keep it."* → **VOICE OPERATOR-CONFIRMED.**
- Machine evidence (3 sessions, turn-local): **EOT → first audible ≈
  0.75–0.81 s median** (R0030 ≤ 1.5 s — **PASS**, faster than local);
  **barge-in ≈ 2 ms** local playback-stop (R0030 ≤ 100 ms — **PASS**);
  local speaker silent ~27 ms **before** the server-round-trip
  interruption frame; **AEC 0 failures** in every session; #5465 NOT_READY
  window startup-only, **no user speech in it** in any session. The
  "1.93 s / 19.84 s outliers" were **metric artifacts** (fragmented speech
  + cross-turn pairing), fixed by turn-local reconstruction.
- **C6 (turn-local, Sulafat session):** RAW input transcription precedes
  first response audio in **3/3 valid turns** (~0.5 s margin); PUSHED in
  2/3. **But timing headroom ≠ steerability** — NeXa cannot be assumed to
  influence the *same* cloud response (`activity_end` already closed the
  turn). ADR-0004: default to Gemini native language mirroring (Option A);
  delayed-`activity_end` + local language-ID as the measured Option B.
- Three real sessions cost **≈ 7 cents** total.

**Sufficient to start ADR-0004?** **YES.** Feasibility + voice are
operator-confirmed; C6 timing is answered and the steerability question
is decided-enough (Option A default). Remaining ADR-0004 inputs
(`usageMetadata` cost, reconnect/lifetime) are M2.6B-scoped and don't
gate the ADR.

**Not started:** ADR-0004, M2.6B, `ConversationRouter`, any `src/nexa/**`
cloud code.

## M2.6A STATUS

**FEASIBILITY PASS / OPERATOR-CONFIRMED. VOICE `Sulafat`
OPERATOR-CONFIRMED.** Next recommended step: **write ADR-0004** (not in
this task). The operator may keep talking to NeXa with `Sulafat` any time
— every run adds turn-local C6 samples automatically.

### Launch command (just talk — Sulafat is the default)

```
set -a; . ~/.config/nexa/secrets/gemini.env; set +a
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py
```

Wait for `GEMINI_REALTIME_READY` and `AEC_REF_ACTIVE`, then have a normal
PL/EN conversation. `Ctrl+C` writes `m2_6a_probe_results_<timestamp>.json`.

---

**Do not begin ADR-0004, M2.6B, or the `ConversationRouter`. Not
pushed.**
