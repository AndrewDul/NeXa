# R0031 — M2.6A: Minimal Gemini Live Real-Hardware Feasibility Spike

- **Date:** 2026-09-10
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **M2.6 — Cloud Realtime Voice** ·
  substage **M2.6A** (feasibility spike, the cloud analogue of M2.5A).
- **Status:** **OPERATOR ATTEMPT #1 FAILED (silent after user speech) →
  ROOT CAUSE FOUND (static) + FIXED + proven by a no-microphone lifecycle
  smoke → READY_FOR_OPERATOR_RETEST. NOT OPERATOR-CONFIRMED.** The
  connection, AEC feed, mic and VAD all worked on attempt #1, but Gemini
  produced no transcription / server content / audio because the probe
  never sent the one-time `LLMRunFrame` that initialises the LLM service's
  context and flips `_ready_for_realtime_input` — and with server VAD
  disabled that flag gates `activity_start` / user audio / `activity_end`.
  Fix applied to the probe (research file only); a single authenticated
  no-mic lifecycle smoke confirms the service now reaches realtime-ready.
  The listening/speaking judgement is still the operator's.
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

## PIPECAT #5465 INSTRUMENTATION

The probe does **not** attempt the production fix (that is M2.6B /
ADR-0004). It makes the bug **visible**:

- `LLM_READY` / `LLM_NOT_READY` transitions timestamped.
- `mic_audio_ms_presented`, `mic_audio_ms_while_not_ready`,
  `mic_chunks_while_not_ready`, `not_ready_window_count` counters.
- JSON block `pipecat_5465` with an explicit note: *"pipecat 1.8.1
  bare-returns `InputAudioRawFrame` while `_ready_for_realtime_input` is
  False; any non-zero value here was NOT sent to Gemini."*
- The session summary prints the not-ready audio seconds + window count.

**No tools** are registered (so the worst #5465 case — an unanswered tool
call — cannot occur in M2.6A). A tiny spike-only replay buffer was
**considered and deliberately not added** — keeping the probe honest
about the raw 1.8.1 behaviour is more useful for the ADR-0004 decision.

---

## EVENT ORDERING FINDINGS (R0030 C6 — the critical question)

**Not yet measured — this is the operator session's primary job.** The
probe records, on one clock:

```
LOCAL_VAD_START · LOCAL_VAD_EOT · INPUT_TRANSCRIPTION_FIRST ·
INPUT_TRANSCRIPTION_FLUSHED · FIRST_SERVER_CONTENT · FIRST_AUDIO_RECEIVED ·
FIRST_AUDIO_PLAYED · OUTPUT_TRANSCRIPTION · TURN_COMPLETE ·
SERVER_INTERRUPTED · LOCAL_PLAYBACK_STOPPED · AEC_REF_ACTIVE/DOWN ·
LLM_READY/NOT_READY
```

and derives, per turn:

- `eot_to_first_server_content_s`, `eot_to_first_audio_received_s`,
  `eot_to_first_audio_played_s`
- `eot_to_input_transcription_first_s`,
  `eot_to_input_transcription_flushed_s`
- **`input_transcription_flushed_to_first_audio_s`** — the sign of this
  answers R0030 C6 question **A**: if positive, the complete input
  transcription is available *before* the first cloud audio and
  `ResponseLanguageResolver` *could* steer the same response; if negative,
  it cannot without added latency.
- `vad_start_to_local_playback_stopped_s`,
  `server_interrupted_to_playback_stopped_s` (barge-in)
- `reconnect_start_to_ready_s`

Questions **B–G** (steer-after-transcription, global "answer in the
spoken language" behaviour, PL↔EN switching, sticky commands, options to
retain strict resolver authority + their latency cost) are answered from
the operator's PL/EN script + the timeline. **ADR-0004 decides the
production language-routing authority afterward.**

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

## AEC STATE

The probe wires the **existing** `AecReferenceFeeder` on the cloud bot
audio (`TTSAudioRawFrame` @24 kHz) → `aplay -D plug:respeaker` — the same
XVF3800 far-end reference path R0028/R0029 proved necessary, just fed from
the cloud reply instead of Piper. `AecReferenceHealth` gates it. **The
operator session must confirm `AEC_REF_ACTIVE` before any barge-in test**;
while the operator is silent and Gemini is speaking, the false-interruption
count must stay **0**. If the reference feed is not healthy, the barge-in
step is skipped and the failure is surfaced loudly (not a silent PASS).

*Not yet run on hardware.*

---

## TOKEN / COST TELEMETRY

The probe prints a running `mic_audio_seconds` / `cloud_audio_seconds` and
a `usd_estimate` using the **official 2026-09-10** rates (R0030 C4):
input audio $3.00/1M (~25 tok/s), output audio $12.00/1M. A 15-minute
mixed session's rough ceiling is well under **$1**. `usageMetadata` (the
authoritative count Pipecat exposes as `LLMTokenUsage`) is a small
follow-up wiring item; the audio-duration estimate is adequate for a
feasibility spike.

---

## FAILURES / ANOMALIES

- **OPERATOR ATTEMPT #1 = FAIL (silent after user speech)** — root cause:
  missing `LLMRunFrame` kickoff → `GeminiLiveLLMService` never became
  `_ready_for_realtime_input`; with server VAD off that gates
  `activity_start` / audio / `activity_end`. **Fixed in the probe**
  (kickoff + `inference_on_context_initialization=False` + empty context);
  proven by the no-mic lifecycle smoke. Full analysis above.
- **`websockets` 17.1 → 16.1.1 downgrade** (transitive, forced by
  `google-genai`). Within Pipecat's allowed range; `pip check` clean;
  spot-check tests pass. Flagged for ADR-0004 (pin it).
- **No `GoAway` handling in Pipecat 1.8.1** (known from R0030). The probe
  will just observe the ~10-minute reconnect reactively; the reconnect
  phase records `CONNECTION_LOST` / `RECONNECT_START` / `RECONNECT_READY`
  and whether pre-reconnect context survived.
- **#5465 silent-drop guards present** (known). Instrumented, not fixed.
- **Bundled "Local Smart Turn v3" ONNX model** loads via the realtime
  aggregator's stop strategy — unexpected (R0005/ADR-0003 D10 rejected the
  `[local-smart-turn]` *extra*; this is a different, base-bundled model).
  Not a blocker for M2.6A; note for ADR-0004 whether to force a pure-VAD
  stop strategy.
- Nothing else. Authentication is healthy; the probe builds and dry-runs
  clean.

---

## WHAT REMAINS OPERATOR-ONLY

1. **Run the probe with the real reSpeaker + speaker.** (agent cannot
   judge audio.)
2. **Judge Polish** pronunciation / accent / naturalness (R0030 C5 / R1).
3. **Judge English** naturalness.
4. **Judge end-to-end latency feel** vs the accepted local voice.
5. **Exercise PL → EN → PL** switching in one session (R0030 C6 D).
6. **Paste back** the terminal + the JSON path so this report can be
   updated with measured numbers and a PASS/WARN/FAIL verdict.
7. Only if the short session passes: the **~10-minute reconnect phase**.

---

## FILES CHANGED

### This commit (operator-attempt-#1 diagnosis + fix)

**Changed (probe / report only — no `src/nexa/**`, no deps):**

- `docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py` —
  the fix (one `LLMRunFrame` kickoff via `_kickoff()`;
  `inference_on_context_initialization=False`; empty `LLMContext`) +
  diagnostics (`_state_poller`, `_install_llm_diagnostics`,
  `GEMINI_SERVER_CONTENT_FIRST`, first-only markers, `PIPELINE_READY`) +
  new `--lifecycle-smoke` mode. Also fixed a pre-existing
  `... or True` bug that re-marked `INPUT_TRANSCRIPTION_FIRST` every frame.
- `docs/reports/R0031_…md` — this report: OPERATOR ATTEMPT #1 = FAIL,
  root cause, official-Pipecat comparison, the fix, diagnostics,
  lifecycle-smoke proof, status → READY_FOR_OPERATOR_RETEST.
- `docs/research/m2_6_cloud_realtime_voice/README.md`,
  `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — status updated to
  "attempt #1 failed → fixed → retest".

### Earlier in M2.6A (commit `f920315`)

`R0031` + `m2_6a_connect_smoke.py` + `m2_6a_gemini_live_probe.py` (first
version) + R0030 `PHASE 0 CORRECTIONS` + `CURRENT_STATE` / `ROADMAP`.

**Outside the repo (not committed, cannot be):**
`~/.config/nexa/secrets/gemini.env` — the operator's key, `600`.

**Not changed (verified):** all `src/nexa/**`, all `tests/**`,
`pyproject.toml` / lock files. Local voice baseline (R0029) untouched;
`bargein_enabled` default-off untouched.

## CHECKS (this commit)

`ruff check docs/research/m2_6_cloud_realtime_voice/` → All checks passed ·
`py_compile m2_6a_gemini_live_probe.py` → OK · `--dry` → PASS ·
**`--lifecycle-smoke` → PASS** (`GEMINI_REALTIME_READY` reached; no mic,
no generation) · diagnostic-wrapper targets verified present on
`GeminiLiveLLMService` · `git diff --check` → clean · secret scan
(`AIza…` / `AQ.…` / the key's own leading characters / `api_key=` /
`Authorization:` / `Bearer` / `NEXA_GEMINI_API_KEY=<value>`) over staged
content → **none** ·
`git diff -- src/nexa` → **empty** · `git diff -- tests` → **empty** ·
no tracked dependency file changed · full suite not re-run (no
`src`/`tests` change).

## COMMIT HASH

- `f920315` / `b3c7c32` — earlier M2.6A commit + its hash record
  (R0031 v1 + connectivity smoke + probe v1 + R0030 Phase-0 corrections).
- `d15e785` — operator-attempt-#1 diagnosis + probe fix (LLMRunFrame kickoff +
  `inference_on_context_initialization=False` + empty context) + R0031 update.

This hash-record note is finalised by the immediately-following commit
(R0026–R0030 pattern). Prior tip: `b3c7c32`.

## GIT STATUS

Branch `main`, **not pushed**. `git diff --check` clean. No `src/` /
`tests/` / tracked-dependency change. This commit changes only the probe
+ R0031 + README + `CURRENT_STATE` + `ROADMAP`. The Gemini key lives only
at `~/.config/nexa/secrets/gemini.env` (outside the repo).

## M2.6A STATUS

**READY_FOR_OPERATOR_RETEST.** Attempt #1 failed (silent after user
speech); root cause found by source analysis, fixed in the probe, and
confirmed by a no-microphone lifecycle smoke (`GEMINI_REALTIME_READY`
reached). The audio session — Polish / English quality, latency, PL↔EN
switching — is still owed from the operator.

### The one launch command

```
set -a; . ~/.config/nexa/secrets/gemini.env; set +a
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py
```

The probe now queues the `LLMRunFrame` kickoff itself once the socket
connects — watch for `KICKOFF_SOCKET_CONNECTED`, `GEMINI_REALTIME_READY`,
then `AEC_REF_ACTIVE` before speaking. Ctrl+C ends the session and writes
`m2_6a_probe_results_<timestamp>.json` next to the probe; hard 15-minute
cap.

### Minimum spoken test script (one continuous session)

1. **EN, clean:** *"Tell me in two sentences why the sky is blue."* — let
   it finish. (first-audio latency; input-transcription vs audio ordering)
2. **EN, interrupt:** *"Give me a long, detailed explanation of how
   rainbows form."* … after ~2 s of the reply, interrupt with *"Stop —
   just give me the short version."* (barge-in latency; spoken high-water
   mark; `SERVER_INTERRUPTED`)
3. **PL, clean:** *"Powiedz mi krótko, dlaczego niebo jest niebieskie."*
   — **judge Polish pronunciation, accent, naturalness, latency.**
4. **PL follow-up:** *"A dlaczego zachód słońca jest czerwony?"* (PL
   multi-turn context)
5. **Switch:** ask one more question in **English**, then one more in
   **Polish** — i.e. PL → EN → PL within this session. (native language
   switching; does it need a NeXa STT?)

Then **Ctrl+C** and paste back the terminal output + the JSON path.

**Reconnect phase (SECOND session, only if step 1–5 pass latency + Polish):**
launch again, ask one short question, then stay **silent ~11 minutes**,
then ask one more EN question — to force one ~10-minute reconnect and
observe whether the pre-reconnect context survived and how long
`RECONNECT_START → RECONNECT_READY` took. **Skip this if the basic
session already fails the latency or Polish gate.**

---

**Do not mark M2.6A COMPLETE before the operator live test. Do not begin
ADR-0004, M2.6B, or the `ConversationRouter`. Not pushed.**
