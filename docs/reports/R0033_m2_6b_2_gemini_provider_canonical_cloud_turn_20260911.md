# R0033 — M2.6B.2: GeminiLiveProvider + Canonical Cloud-Turn Integration

## TASK RESULT

**PASS (checkpoint).** Production `GeminiLiveProvider` implemented and
proven end-to-end against the REAL Pipecat 1.8.1 pipeline / worker /
aggregator machinery (a fake terminal service stands in for the network
boundary only — no real Gemini connection, no cloud call). The canonical
cloud-turn write path (`CloudTurnAccumulator` ->
`ConversationSession.record_external_exchange`) and `ConversationRouter`
are implemented and tested. Local voice (M2.5B, `R0029`) remains
byte-for-byte frozen — the only existing `src/nexa/**` file touched is
`conversation/session.py` (+1 additive method +1 additive enum) and its
`__init__.py` (+2 export lines). No hardware test, no real cloud call, no
reconnect-duration test, per this task's scope.

## WHAT I DID

1. Re-read `AGENTS.md`, `CURRENT_STATE.md`, `ROADMAP.md`, ADR-0004 +
   Amendment 1, `R0032`, the M2.6B.1 startup-sequencing source-audit note,
   the existing `src/nexa/realtime/**`, `ConversationSession`
   (`send`/`commit_interrupted_turn`), `response_language.py`, and the
   M2.5B barge-in/interruption implementation before writing anything.
2. Implemented, in `src/nexa/realtime/`:
   - **`turn.py` — `CloudTurnAccumulator`** (item 6): correlates ONE
     logical cloud turn (NeXa-local monotonic `generation` id, never a
     Gemini-provided id) across `start_turn` / `on_user_transcription` /
     `on_assistant_transcription` / `on_interruption` /
     `set_spoken_prefix` / `on_turn_complete` / `on_session_lost`, and
     guarantees **at most one canonical commit** per turn — every method
     is a no-op once the turn is `COMMITTED` or `ABANDONED`; a new
     `start_turn()` marks any unfinished previous turn `ABANDONED`.
   - **`turn_framing.py` — `UtteranceFramer`** (item 5): preserves the
     logical turn envelope (`activity_start` / `audio*` / `activity_end`)
     through a provider NOT_READY window — bounded by time *and* bytes,
     idempotent start/end, orphan audio rejected (never emitted without a
     preceding start), overflow prefers dropping a whole *completed*
     oldest utterance, and only drops mid-utterance audio (never the
     `activity_start`) when the front of the queue is still open.
   - **`router.py` — `ConversationRouter`** (items 9–10): owns
     `ConversationPolicy` / `active_provider` / provider lifecycle /
     `CloudContextSnapshot` creation / the cloud-turn commits / failure
     fallback. `LOCAL_ONLY` never calls the injected
     `cloud_provider_factory` (so it never constructs a provider, never
     builds a snapshot, never touches a credential). `AUTO` is provisional
     (reachability + eligibility only, no classifier). Every cloud failure
     produces one observable `ProviderSwitchNotice`, switches to `LOCAL`,
     and never auto-retries cloud on its own.
   - **`gemini/service.py` — `GeminiLiveProvider`** (items 1–4): the
     production `RealtimeVoiceProvider` implementation, built exactly per
     the R0032 source-audit sequencing (construction-time
     `system_instruction`, initial `LLMContext` seeded from
     `snapshot.recent_turns`, `inference_on_context_initialization=False`,
     one `LLMRunFrame` kickoff, Pipecat's own `_create_initial_response()`
     performs the one-time seed). Wires the M2.6B.1 `UtteranceFramer` into
     `user_turn_start` / `send_user_audio` / `user_turn_end`, gated on
     `readiness == READY`, flushed once on the READY transition.
3. Added `ConversationSession.record_external_exchange` (additive,
   `src/nexa/conversation/session.py` — the canonical cloud-turn write
   path, ADR-0004 Decision A).
4. Added the `cloud-gemini` optional-dependency extra to `pyproject.toml`
   (ADR-0004 Decision L) — `google-genai>=2.22,<3` +
   `websockets>=15,<17`, matching the versions already resolved in the
   research venv (R0030/R0031); `pipecat-ai[local]==1.8.1` unchanged; no
   `pipecat-ai[google]`.
5. Added 57 new deterministic tests across 5 new files, plus a net +1 in
   one extended file (58 net new — see TEST RESULTS; an earlier draft of
   this bullet said "88 new tests across 6 new files", which was wrong —
   corrected here in the M2.6B.2A pass, `R0034`, per the operator's review;
   the actual count was always 57+1=58, as TEST RESULTS below correctly
   stated).
6. Wrote this report; updated `docs/CURRENT_STATE.md` and
   `docs/ROADMAP.md`.

## GEMINI PROVIDER IMPLEMENTATION

`src/nexa/realtime/gemini/service.py` — zero top-level Pipecat/Gemini
import (`_pipecat_imports()` is deferred to inside `GeminiLiveProvider.
start()`); `service_class` is injectable (defaults to the real
`GeminiLiveLLMService`) so tests substitute a fake `FrameProcessor`
standing in for the network boundary while exercising the REAL
`Pipeline` / `PipelineWorker` / `WorkerRunner` / `LLMContextAggregatorPair`
around it.

**A real, executable bug was found and fixed while building this**
(discovered by actually running the real Pipecat pipeline in this sandbox
with a fake terminal service — no network involved): `WorkerRunner.
add_workers()` only starts an added worker's background task if the
runner is *already running* (`if self._running: await self._start_worker
(entry)`); calling it before `runner.run()` — as a first, naive draft did
— silently leaves the pipeline never actually executing. Fixed by
launching `self._run_task = asyncio.create_task(self._runner.run())`
immediately after `add_workers()`, matching the M2.6A research probe's own
proven pattern (`run_task = asyncio.create_task(runner.run())`,
`m2_6a_gemini_live_probe.py`). Documented in the module's own comment so
the reason is not lost.

## STARTUP SEQUENCE IMPLEMENTED

Exactly as the R0032 source audit prescribed:
`GeminiLiveLLMService(system_instruction=snapshot.system_instruction, …,
inference_on_context_initialization=False, settings=Settings(model=
"gemini-3.1-flash-live-preview", voice=<mapped preference>,
vad=GeminiVADParams(disabled=True), context_window_compression=…, …))` is
constructed; an `LLMContext` seeded from `snapshot.recent_turns` (role
card excluded — it goes only to `system_instruction=`, never duplicated
into context messages, per the R0031 "spurious user line" finding) is
wired through `LLMContextAggregatorPair(realtime_service_mode=True)`; the
pipeline (`[user_agg, llm, event_tap, asst_agg]`, no transport — audio is
`send_user_audio`/`AssistantAudioEvent`, not a device) starts under
`PipelineWorker`/`WorkerRunner`; exactly **one** `LLMRunFrame` is queued;
Pipecat's own `_create_initial_response()` performs the one-time
`clientContent` seed (never duplicated by NeXa); `_ready_for_realtime_input`
is polled and translated to `ProviderReadiness.READY`.
`system_instruction` is supplied once, at construction, and never mutated
— `GeminiLiveProvider` has no method to change it later (structural test).

**Proven live** (fake terminal service, real Pipecat pipeline, no
network): `start()` reaches `READY`; `user_turn_start` /
`send_user_audio` / `user_turn_end` flow through the real aggregators;
assistant audio/transcription frames translate to
`AssistantAudioEvent`/`AssistantTranscriptionEvent`; `stop()` cleanly
tears the pipeline down and is idempotent.

## FRESH-SNAPSHOT RESUMPTION DECISION

Resolved explicitly, matching ADR-0004 Decision I:
- `GeminiLiveProvider._observe_resumption_update` records a
  `SessionResumptionHandle` **only** `if resumable and new_handle` — never
  a stale/empty handle (mirrors the installed
  `GeminiLiveLLMService._handle_msg_resumption_update`'s own guard,
  confirmed in R0032).
- `ConversationRouter.request_fresh_snapshot_after_resumption_failure()`
  rebuilds a `CloudContextSnapshot` from the **current canonical**
  `ConversationSession` — never from Pipecat's own stale `self._context` —
  whenever NeXa decides a fresh session is required.
- **What is NOT yet wired** (explicitly out of scope for this checkpoint —
  no reconnect-duration test was permitted): `GeminiLiveProvider` does not
  yet *drive* an actual reconnect (no GoAway handling exists in Pipecat
  1.8.1, confirmed unchanged in R0032; no age-timer-triggered reconnect
  socket sequence is implemented here). The M2.6B.1 `ReconnectController`
  (deterministic outcome state machine) and the M2.6B.2 fresh-snapshot
  path exist and are unit-tested in isolation, but nothing yet calls
  `ReconnectController.attempt()` from inside `GeminiLiveProvider` on a
  real connection error — that live wiring (and its "one open M2.6B.2
  design question" noted in R0032, about whether to let Pipecat's own
  `_handle_session_ready` re-seed run or intercept it) is carried forward,
  most naturally alongside M2.6B.3's HYBRID audio + real-hardware
  checkpoint, where a live connection actually exists to reconnect.

## TURN-FRAMING / #5465 RESULT

`UtteranceFramer` is wired into `GeminiLiveProvider.user_turn_start` /
`send_user_audio` / `user_turn_end`, gated on `self._readiness ==
READY`: while not ready, calls go to the framer instead of the live
pipeline; `_flush_framer()` (called once `_wait_until_ready()` detects
`READY`, and available for a future reconnect to call again) delivers
every buffered event in order — `activity_start`, its audio, then
`activity_end` — via the same real pipeline. Proven both as a pure unit
(`UtteranceFramer`, 20 tests: full turn captured while NOT_READY; an
in-progress turn flushing what happened so far; two utterances across a
simulated reconnect never duplicating; overflow dropping audio but never
an `activity_start`; overflow dropping a whole completed oldest utterance
first) and as production wiring inside `GeminiLiveProvider` (2 tests:
audio buffered not sent live while `DEGRADED`/`RECONNECTING`; buffered
turn flushed in the correct order exactly once on the `READY` transition,
verified against the real pipeline with no duplication on a second
flush). Bounded — never unbounded growth; overflow is always logged
(`WARNING`), never silent.

## CANONICAL CLOUD-TURN WRITE PATH

`ConversationSession.record_external_exchange(user_text, assistant_text=
None, *, interrupted=False, response_language=None) ->
ExternalExchangeOutcome` (additive; `send()` / `commit_interrupted_turn()`
byte-for-byte unchanged — confirmed by the full existing
`test_conversation_session.py` / `test_session_provider_window.py` suites
passing unmodified). `_history` / `_response_languages` stay index-aligned
in every branch (same discipline as `commit_interrupted_turn`):
- **NORMAL** — both turns appended (`COMMITTED_EXCHANGE`).
- **USER ONLY** — session/provider lost before the assistant spoke:
  user turn kept, no assistant turn (`COMMITTED_USER_ONLY`).
- **INTERRUPTED** — user kept, assistant = exactly the caller-supplied
  spoken prefix, `interrupted=True`.
- **NO SPOKEN ASSISTANT** — interrupted before any audio: user kept, no
  empty assistant turn (`COMMITTED_USER_ONLY`).
- **INVALID** (blank `user_text`) — raises `ValueError`; never silently
  no-ops, never corrupts alignment.

`CloudTurnAccumulator` guarantees the "at most one commit" invariant
*before* this method is ever called: late provider frames after a
turn is `COMMITTED`/`ABANDONED` are dropped by the accumulator, so
`ConversationRouter.commit_cloud_turn()` simply returns `None` a second
time for the same turn (tested: two consecutive cloud turns stay
index-aligned; a second `commit_cloud_turn()` call commits nothing more).

## ROUTER RESULT

`ConversationRouter` proven with fake local/cloud provider adapters (no
`RealtimeVoiceProvider` ABC subclassing required in tests — duck-typed to
exactly what the router calls, keeping router tests independent of the
Gemini-specific plumbing):
- `LOCAL_ONLY` (the default): `start_cloud()` returns `False` without
  ever calling the factory — proven via a factory that records every
  call and asserting zero calls.
- `CLOUD_PREFERRED` / `AUTO`: starts when eligible (`ProviderEligibilityPolicy`
  + connectivity), refuses when a `DISTRIBUTED` target user is in
  EEA/CH/UK without `billing_verified`.
- Failure -> one `ProviderSwitchNotice`, `active_provider` back to
  `LOCAL`, no automatic re-attempt.
- `set_policy()` is the `SetConversationPolicy` command surface —
  additive, NeXa-owned, never called by a provider.

## DEPENDENCY / LOCAL-ONLY ISOLATION

- `pyproject.toml`: added `cloud-gemini = ["google-genai>=2.22,<3",
  "websockets>=15,<17"]` under `[project.optional-dependencies]`;
  `pipecat-ai[local]==1.8.1` untouched; core `dependencies` list
  untouched. `pip check` clean; the already-installed research-venv
  versions (`google-genai 2.22.0`, `websockets 16.1.1`) satisfy the new
  ranges — nothing was reinstalled or upgraded.
- Import-isolation gates (genuinely isolated `python -I` subprocesses,
  extended from M2.6B.1's `test_realtime_no_cloud_dependency.py`):
  - `import nexa.realtime.gemini.service` alone imports **neither**
    `google.genai` **nor** `pipecat` (every Pipecat/Gemini symbol is
    behind `_pipecat_imports()`, called only from `start()`).
  - Constructing a `ConversationRouter` with **no**
    `cloud_provider_factory` (the `LOCAL_ONLY` shape) never imports
    `nexa.realtime.gemini` at all, transitively or otherwise — proven by
    asserting `'nexa.realtime.gemini' not in sys.modules` after building
    a real `ConversationSession` + `ConversationRouter`.
  - (Carried from M2.6B.1, still true) bare `import nexa` never imports
    `nexa.realtime`; importing the generic boundary and the credential/
    voice modules never imports `google.genai`.

## FILES CHANGED

- **New:** `src/nexa/realtime/{turn,turn_framing,router}.py`,
  `src/nexa/realtime/gemini/service.py`.
- **New tests:** `tests/test_realtime_turn.py` (10),
  `tests/test_realtime_turn_framing.py` (10),
  `tests/test_realtime_router.py` (15),
  `tests/test_realtime_gemini_service.py` (14),
  `tests/test_conversation_record_external_exchange.py` (8).
- **Extended:** `tests/test_realtime_no_cloud_dependency.py` (+1 net —
  the M2.6B.1 test asserting `gemini.service` *didn't exist* was replaced
  with two tests reflecting the M2.6B.2 reality: the router never imports
  it under `LOCAL_ONLY`, and importing it alone never imports
  `google.genai`).
- **Additive-only edits:** `src/nexa/conversation/session.py` (+1 enum,
  +1 method, `send()`/`commit_interrupted_turn()` untouched — verified via
  `git diff`, only insertions), `src/nexa/conversation/__init__.py` (+2
  export lines), `src/nexa/realtime/__init__.py` (new re-exports for
  `turn.py` / `turn_framing.py` / `router.py`).
- **Modified:** `pyproject.toml` (`cloud-gemini` optional extra added;
  core `dependencies` unchanged).
- **Docs:** this report; `docs/CURRENT_STATE.md`; `docs/ROADMAP.md`.
- **Unmodified:** every other existing `src/nexa/**` file (confirmed —
  `git diff --name-only -- src/nexa` shows only the two files named
  above plus new files under `src/nexa/realtime/`); every other existing
  `tests/**` file.

## TEST RESULTS

New this checkpoint: **57 new tests** across 5 new files (10 + 10 + 15 +
14 + 8: `test_realtime_turn.py`, `test_realtime_turn_framing.py`,
`test_realtime_router.py`, `test_realtime_gemini_service.py`,
`test_conversation_record_external_exchange.py`), plus **+1 net** in
`test_realtime_no_cloud_dependency.py` (one M2.6B.1 test asserting
`gemini.service` didn't exist yet was replaced by two tests reflecting the
M2.6B.2 reality — see FILES CHANGED). Full repository suite: **870 tests,
OK (skipped=7)** — 812 (M2.6B.1 baseline) + 58 net new, **zero
regressions**. `ruff check` on every
new/edited file: clean. Whole-repo `ruff check .`: 82 pre-existing
errors, confirmed identical count and confirmed none in this task's files
(same method as R0032: `git stash` back to the pre-task commit and
re-running `ruff check .` reproduces exactly 82). `pip check`: clean.
`git diff --check`: clean. Secret scan: clean (no key material; the
research/spike Gemini key was never touched).

The `GeminiLiveProvider` tests are the most significant new evidence: they
exercise the **real** Pipecat 1.8.1 `Pipeline` / `PipelineWorker` /
`WorkerRunner` / `LLMContextAggregatorPair` machinery end-to-end (only the
terminal `GeminiLiveLLMService` is faked, via dependency injection), so
"one `LLMRunFrame`", "seeded once", "system_instruction construction-time
only", and the turn-framing wiring are proven against real Pipecat
behaviour, not merely asserted against a fully-mocked stand-in.

## LOCAL VOICE FREEZE CHECK

`git diff --name-only -- src/nexa` shows only
`conversation/session.py` (additive) and `conversation/__init__.py`
(additive export) among existing files — no local-voice file
(`voice/**`, `voice_tts/**`, `voice_conversation/**`, `stt/**`, `tts/**`,
`providers/**`, `config.py`) was touched. The full existing
`test_conversation_session.py` / `test_session_provider_window.py` /
`test_bargein_*` / `test_bilingual_*` / `test_voice_*` suites pass
unmodified as part of the 870-test full run.

## ACCEPTANCE GATES NOW SATISFIED

Extending R0032's list (ADR-0004's 19 gates): the canonical-write-path
gates now have a real implementation and tests, not just the foundation —
**gate 2** (CLOUD uses the canonical `ConversationSession` — proven via
`record_external_exchange` + `CloudTurnAccumulator` + router tests),
**gate 4** (no duplicate canonical assistant turns — proven via the
"late frames after commit" and "two consecutive turns" tests), **gate 5**
(interrupted cloud response stores only the spoken prefix — proven),
partial **gate 6** (turn-envelope-aware NOT_READY buffering is now wired
into the provider, not just the standalone buffer — proven for the
DEGRADED/RECONNECTING-while-buffering case; the live-reconnect path
itself is not yet exercised), **gate 16** (cloud-specific unit/integration
tests green). Gates needing a live connection, hardware, or an actual
reconnect (3, 7, 8 fully, 10–15, 17, 18) remain for later checkpoints.

## KNOWN RISKS

- **Reconnect is not yet driven end-to-end.** `ReconnectController`
  (M2.6B.1) and the fresh-snapshot decision (this checkpoint) exist and
  are unit-tested in isolation, but `GeminiLiveProvider` does not yet call
  into `ReconnectController` on a real connection error, nor does it
  implement the proactive age-timer / `GoAway` transition itself. This is
  explicit, known, and deferred — not a silent gap.
- **The one open M2.6B.2 design question from R0032 is still open:** on a
  resumption-failure reconnect without a resumable handle, Pipecat's own
  `_handle_session_ready` will try to re-seed from its internally-tracked
  `self._context` unless `GeminiLiveProvider` intercepts it. Not resolved
  in this checkpoint (needs the reconnect wiring above to even matter).
- **`_flush_framer` uses a poll-then-flush pattern**, not an event-driven
  callback from Pipecat's own readiness signal (there isn't one to hook —
  `_ready_for_realtime_input` is a private attribute observed by polling,
  same technique R0031's research probe used). Acceptable for now; a
  future revision could reduce the poll interval's latency contribution
  if it proves material on real hardware.
- **Never tested against a real Gemini connection.** All of the above is
  proven against a fake terminal service standing in for the network
  boundary. Real-hardware / real-cloud validation is M2.6B.3+.

## WHAT REMAINS FOR M2.6B

Per ADR-0004's ordered plan and this checkpoint's explicit exclusions:
GoAway/age-timer-driven reconnect actually wired into `GeminiLiveProvider`;
HYBRID audio wiring (tee cloud output to `AecReferenceFeeder`, keep Silero
+ `BargeInController` as authority for the cloud path); cloud
speaker/AEC/barge-in on real hardware; the real LOCAL↔CLOUD spoken switch
end-to-end; the operator cloud probe app; function calling/tools; memory;
identity; UI; the real `AUTO` privacy/task/cost classifier; a minimum
real-hardware operator acceptance session (required before M2.6B is marked
COMPLETE, per ADR-0004 gate 17).

## DOCUMENTATION / REPORTS UPDATED

This report; `docs/CURRENT_STATE.md` (M2.6B still IN PROGRESS; M2.6B.2
recorded as implemented; next = M2.6B.3); `docs/ROADMAP.md` (same).

## LEGACY NEXA USED

NO.

## EXTERNAL RESEARCH USED

NO new external research — this checkpoint's Pipecat behaviour was
established either by the R0032 static source audit or by directly
running the installed library in this sandbox (no network).

## CURRENT VERIFIED STATE

- Local realtime voice (M2.5B, `R0029`) — unchanged, `OPERATOR-CONFIRMED`.
- M2.6A feasibility — PASS / OPERATOR-CONFIRMED; `Sulafat` OPERATOR-
  CONFIRMED (unchanged).
- `ADR-0004` + Amendment 1 — Accepted (unchanged; nothing in this
  checkpoint contradicted the architecture).
- **M2.6B — IN PROGRESS.** `M2.6B.1` (`R0032`) provider-agnostic
  foundation — DONE. **`M2.6B.2` (this report, `R0033`)** —
  `GeminiLiveProvider` + canonical cloud-turn integration — IMPLEMENTED,
  deterministically tested against the real Pipecat pipeline, **no live
  Gemini connection, no reconnect wiring yet, no hardware test**. 870
  tests total, 0 regressions.
- `pip install .` (no extra) remains Google-cloud-free; `pip install
  ".[cloud-gemini]"` is now the tracked way to get `GeminiLiveProvider`'s
  runtime dependency.

## COMMIT HASH

`4d85820` — `feat(m2.6b.2): GeminiLiveProvider + canonical cloud-turn
integration (R0033)`.

Prior tip: `eeb3724` (R0032 M2.6B.1 hash-record commit).

## GIT STATUS

Branch `main`, ahead of `origin/main` (`505627f`) by 5 commits
(`986e65a`, `e4b84ec`, `eeb3724`, `4d85820`, and this hash-record
follow-up). Working tree clean after commit. Not pushed.

## NEXT RECOMMENDED ACTION

**M2.6B.3 — HYBRID cloud audio wiring + minimum real-hardware/operator
conversation acceptance**, per this task's own stated expectation — using
the `GeminiLiveProvider` + `ConversationRouter` + canonical write path
built here. Before that (or as its first sub-step), wire
`ReconnectController` into `GeminiLiveProvider` for a real connection-error
path (deterministic/mocked first, per ADR-0004's own testing order), and
resolve the still-open "stale Pipecat context vs. fresh NeXa snapshot on
resumption failure" design question. Local voice stays frozen. No push.
