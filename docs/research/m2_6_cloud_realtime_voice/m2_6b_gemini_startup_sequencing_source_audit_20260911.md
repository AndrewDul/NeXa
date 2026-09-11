# M2.6B.1 — Gemini/Pipecat startup-sequencing source audit (2026-09-11)

Mandatory pre-`GeminiLiveProvider` source audit required by the M2.6B.1
charter, before any production Gemini service code is written (M2.6B.2+).
**No cloud call was made** — this is a static audit of the installed
`pipecat-ai==1.8.1` package (`.venv/lib/python3.13/site-packages/pipecat/
services/google/gemini_live/llm.py`, 2180 lines) and `google-genai==2.22.0`
in this repo's own venv, cross-checked against the current official Live
API WebSockets reference (already verified in ADR-0004 Amendment 1). Line
numbers below refer to the installed `llm.py`, current on 2026-09-11.

**Verdict: NO ADR-0004 CONTRADICTION.** This audit *confirms and refines*
ADR-0004 / Amendment 1 — the installed Pipecat 1.8.1 already implements
most of what Amendment 1 §2/§3/§4 described as required NeXa-side
behaviour, at the library level. It changes *implementation sequencing*
for M2.6B.2, not architecture.

## Questions answered

### 1. What causes `GeminiLiveLLMService` to create/setup the Live session?

`setup()` (line 763), called once by the Pipecat pipeline framework when
the processor starts, calls `self._connect()` (line 768) immediately —
`VERIFIED FACT` (direct read). Session/socket creation is driven by
pipeline startup, **not** by `LLMRunFrame`. `_connect()` builds the full
`LiveConnectConfig` (model, generation config, voice, VAD, history config,
context-window compression, **and** `system_instruction`/`tools` from
`self._context` if already set, else from the constructor's
`system_instruction=` value) and starts `_connection_task_handler`.

### 2. What exactly does `LLMRunFrame` do in Pipecat 1.8.1?

`LLMRunFrame` is handled by `LLMUserAggregator._handle_llm_run` (in
`pipecat/processors/aggregators/llm_response_universal.py`, line 1195),
which calls `push_context_frame()` — this emits an `LLMContextFrame`
carrying the aggregator's current `LLMContext` downstream.
`GeminiLiveLLMService.process_frame` (line 841) handles `LLMContextFrame`
by calling `_handle_context(frame.context)` (line 908). **Without an
`LLMRunFrame`, no `LLMContextFrame` is ever produced**, `self._context`
stays `None`, and `_handle_context`/`_create_initial_response` never run —
`VERIFIED FACT`, matches R0031's M2.6A finding exactly (the missing
`LLMRunFrame` kickoff was the attempt-#1 root cause).

### 3. When does `_ready_for_realtime_input` become true?

Inside `_create_initial_response()` (line 1573), unconditionally at the
end of the method (line 1683/1712) — whether or not a seed was actually
sent. If there are no messages to seed with, it is set immediately
(line 1643) without sending anything. `_handle_session_ready` (line 1754)
defers this correctly regardless of arrival order: if `LLMRunFrame`
(hence `_handle_context`) fires **before** the session is ready, a
`_run_llm_when_session_ready` flag (line 1631) defers
`_create_initial_response()` until the session actually becomes ready
(line 1757-1759) — `VERIFIED FACT`.

### 4. How can Gemini 3.1 initial history be enabled through the installed
Pipecat/`google-genai` surfaces?

**It already is, by default.** `_get_history_config()` (line 1119)
unconditionally returns `HistoryConfig(initial_history_in_client_content=True)`
unless a subclass overrides it (only the Vertex AI subclass does, because
Vertex doesn't support it) — `VERIFIED FACT`. `_connect()` (line 1171-1173)
always attaches this to `LiveConnectConfig.history_config`. NeXa's
`GeminiLiveProvider` (M2.6B.2) does **not** need to configure this itself.

### 5. Can initial client history be seeded before `LLMRunFrame`, as part of
it, or only after session setup?

Only **after** the WebSocket session is ready (`self._session` set) AND
only as part of the `LLMRunFrame` -> `LLMContextFrame` -> `_handle_context`
-> `_create_initial_response()` chain (question 2/3). Ordering between
`LLMRunFrame` arrival and session readiness is handled robustly by Pipecat
itself either way (`_run_llm_when_session_ready`) — `VERIFIED FACT`.

### 6. Does Pipecat expose the necessary `HistoryConfig` directly?

Yes (question 4) — via the overridable `_get_history_config()`, already
defaulting to the correct value for the non-Vertex Gemini Live service NeXa
uses. No override needed.

### 7. If not, what is the smallest wrapper/subclass/composition needed?

N/A for `HistoryConfig` itself. `GeminiLiveProvider`'s (M2.6B.2) job is
narrower than Amendment 1 §3 first assumed:

* construct `GeminiLiveLLMService(system_instruction=snapshot.system_instruction,
  …, inference_on_context_initialization=False, …)` — passing
  `CloudContextSnapshot.system_instruction` at **construction time**
  (`_system_instruction_from_init`) so it is correct on the very first
  `_connect()`, avoiding an unwanted automatic `_reconnect()` (question 8);
* build an initial `LLMContext(messages=<snapshot.recent_turns rendered as
  provider messages>)` — non-empty when the snapshot has recent turns —
  fed through `LLMContextAggregatorPair(realtime_service_mode=True)`;
* after the pipeline/transport is running, queue exactly **one**
  `LLMRunFrame()` (the same one-time kickoff R0031 already established);
  Pipecat's own `_create_initial_response()` then calls
  `self._session.send_client_content(turns=seed_messages,
  turn_complete=False)` (Gemini-3.x path, `_inference_on_context_initialization=False`)
  — this call **is** the "seed `CloudContextSnapshot` once via
  `clientContent`" step Amendment 1 §3 described; NeXa does not need to
  hand-build it.

### 8. Does any required solution contradict ADR-0004?

**No.** It is confirming, not contradicting, evidence for Amendment 1:

* `_handle_context` (line 908-925) compares the context's effective
  `system_instruction` to the init-provided value and calls
  `await self._reconnect()` **if they differ** — i.e. Pipecat's own
  behaviour structurally proves "system_instruction is immutable on an
  open connection": a mismatch doesn't mutate the live config, it forces a
  whole new connection. This is exactly Amendment 1 §2's finding, now
  confirmed at the source level, and is why `GeminiLiveProvider` must
  supply `CloudContextSnapshot.system_instruction` correctly at
  construction — never expect a later in-place update.
* `_handle_msg_resumption_update` (line 2162-2166) only ever stores
  `self._session_resumption_handle = update.new_handle` when
  `update.resumable and update.new_handle` — this is *exactly*
  Amendment 1 §4's "only keep a `resumable=true` handle" rule, already
  implemented in the installed library and now mirrored by
  `nexa.realtime.reconnect.ReconnectController.offer_resumption_handle`.
* `_reconnect()` (line 1410) = `_disconnect()` +
  `_connect(session_resumption_handle=self._session_resumption_handle)` —
  i.e. Pipecat already reconnects using the last known-good resumable
  handle, consistent with Amendment 1 §4.
* No `GoAway` handling exists anywhere in the file (confirmed by an
  exhaustive case-insensitive search) — matches ADR-0004 / R0030 / R0031's
  existing finding exactly; unchanged.
* The #5465 readiness gates (`_ready_for_realtime_input` gating
  `_handle_user_started_speaking` / `_send_user_audio` /
  `_handle_user_stopped_speaking`, lines 808/825/1436/1482/1538/1552) are
  present and unchanged — the NeXa-owned `InboundAudioBuffer` (ADR-0004
  Decision H, implemented in M2.6B.1) is still the right, and still the
  only, place to protect against it.

### 9. What exact sequencing should M2.6B.2 use for `GeminiLiveProvider`?

```
construct GeminiLiveLLMService(
    system_instruction=snapshot.system_instruction,   # correct at init — never changed later
    inference_on_context_initialization=False,        # no unwanted greeting
    voice=gemini_voice_for_preference(...),
    vad=GeminiVADParams(disabled=True),                # server VAD OFF, unchanged M2.6A baseline
    ...
)
build LLMContext(messages=render(snapshot.recent_turns))   # non-empty if there is history to seed
wire through LLMContextAggregatorPair(realtime_service_mode=True)
start the pipeline/transport  ->  Pipecat calls setup() -> _connect() automatically
queue ONE LLMRunFrame()  ->  _handle_context() -> _create_initial_response()
                              -> Pipecat sends the one-time clientContent seed itself
wait for _ready_for_realtime_input (surfaced to NeXa as ProviderReadiness.READY)
   -> flush InboundAudioBuffer, begin live send_user_audio()
```

One important nuance for M2.6B.2 design (not a blocker for M2.6B.1, not an
ADR contradiction): on a resumption-failure reconnect **without** a
resumable handle, `_handle_session_ready`'s `elif self._context:` branch
(line 1765-1776) already re-seeds from Pipecat's own internally-tracked
`self._context` via `_create_initial_response(for_reconnect=True)`. Since
that `self._context` is only ever the object last pushed through
`LLMContextFrame`, `GeminiLiveProvider` (M2.6B.2) must decide whether to
let this internal recovery run as-is, or to intercept it and force a fresh
NeXa-rebuilt `CloudContextSnapshot` (ADR-0004 Decision I) — recorded here
as an M2.6B.2 design question, not resolved in this checkpoint.

## Conclusion

No ADR-0004 architectural decision is invalidated. Amendment 1 §2 ("system
instruction immutable on an open connection"), §3 ("seed once via the
initial-history mechanism"), and §4 ("only keep a resumable handle") are
all independently confirmed by the installed source, and in several cases
Pipecat 1.8.1 already implements the exact mechanism NeXa needs — reducing,
not increasing, the work M2.6B.2 must do. `GeminiLiveProvider`'s job is
primarily correct *construction-time* wiring (system instruction, initial
context, voice, VAD) plus the one-time `LLMRunFrame` kickoff, not a
custom seeding/resumption implementation.
