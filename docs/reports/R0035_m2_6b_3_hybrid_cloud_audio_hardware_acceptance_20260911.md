# R0035 — M2.6B.3: Production HYBRID Cloud Audio + Real-Hardware Operator Acceptance

## TASK RESULT

**TEST-READY, PASS (deterministic checkpoint) — hardware/Gemini operator
acceptance NOT YET RUN.** Every deterministic (no-network, no-hardware)
requirement for this checkpoint is implemented and green: the
`ReconnectController`/mid-turn-unsafe reconnect policy is wired into the
production `GeminiLiveProvider` and `ConversationRouter`; the HYBRID
hardware audio pipeline (reSpeaker input → local Silero VAD → provider →
Gemini → assistant audio → USB speaker + XVF3800 AEC reference) is built
in a new module reusing the existing, unmodified `BargeInController` /
`AecReferenceFeeder` / `AecReferenceHealth`; cloud barge-in is wired to the
same local barge-in authority; a minimal operator app exists. No real
Gemini connection and no real audio device have been exercised — that is
Phase 7 of the M2.6B.3 charter and requires the operator. **This report
does not claim hardware acceptance.** See EXACT LAUNCH COMMAND / READY
LINES / WHAT THE OPERATOR SHOULD DO below.

## DETERMINISTIC RECONNECT RESULT

`GeminiLiveProvider` gained a NeXa-owned observation seam,
`_readiness_monitor()` — a background task polling
`self._llm._ready_for_realtime_input` for transitions. This exists because
source reading confirmed Pipecat's `GeminiLiveLLMService` reconnects
**automatically and internally**
(`_connection_task_handler`'s exception handler calls
`_handle_connection_error()` then `_reconnect()` with no external hook);
NeXa cannot prevent that attempt, only observe the readiness transition it
produces and decide, independently, whether to trust it.

On READY→not-ready the monitor captures whether a local turn was live
(`self._live_turn_open`, from R0034) at the moment readiness dropped,
flags `_needs_fresh_session` accordingly, and emits `ReconnectingEvent`. If
the loss was **safe** (no local turn open), the monitor keeps observing;
if Pipecat's own reconnect brings readiness back, the monitor flushes the
buffered framer, calls `reconnect.on_connected()`, and emits `ResumedEvent`
— the SAME provider-scoped context resumes, canonical
`ConversationSession` untouched, no re-seed, no duplicate turns. If the
loss was **unsafe** (mid-user-turn), the monitor stops observing (this
instance is doomed) and `needs_fresh_session` becomes `True`.

`ConversationRouter.recover_from_mid_turn_loss()` is the destroy-and-
recreate execution path a caller invokes once `needs_fresh_session` is
observed: `take_pending_audio()` off the old provider (never audio already
confirmed sent), `stop()` the old provider, build a fresh
`CloudContextSnapshot` from the canonical `ConversationSession`
(`request_fresh_snapshot_after_resumption_failure`), construct + start a
brand-new `GeminiLiveProvider` from the injected factory, and replay
**only** the pending PCM as one new self-contained utterance
(`user_turn_start` → chunks → `user_turn_end`). On any failure it calls
the router's existing Decision-J fallback (`_handle_cloud_failure`) and
returns `None`.

`should_proactively_reconnect()` is a thin passthrough to
`ReconnectController.should_proactively_reconnect(now=...)` — decision
logic only; **no live 8–10 minute connection-age test was run** (per the
explicit "do not burn quota" instruction). Pipecat 1.8.1 has no `GoAway`
frame/attribute anywhere in its source (confirmed absent, unchanged from
R0032's audit) — there is nothing further to wire for it; the age-timer
path already existed in `ReconnectController` from M2.6B.1 and is now
reachable from the provider.

## MID-TURN RECONNECT POLICY

**Answer to the charter's explicit question: no, this same Gemini logical
session must NOT be assumed resumable after a mid-user-turn connection
loss**, absent installed-source evidence to the contrary — none was found
(Pipecat 1.8.1's own automatic reconnect calls `_connect(session_
resumption_handle=self._session_resumption_handle)`, which is a NEW
connection attempt using a previously-stored resumption handle; nothing
in the source proves the provider-side turn state a mid-utterance
`activityStart` left open is safely resumable by that handle). The v1
safe policy from the charter is implemented exactly:

1. the already-sent live segment is marked aborted (never gets a live
   `activity_end`);
2. only not-yet-delivered post-loss PCM is preserved
   (`take_pending_audio()`, destructive — a second call returns `[]`);
3. `FRESH_SESSION_REQUIRED` is signalled via `needs_fresh_session`;
4. a fresh `CloudContextSnapshot` is obtained from the canonical
   `ConversationSession` (never a stale provider-owned context);
5. the old `GeminiLiveProvider` is destroyed (`stop()`);
6. a new `GeminiLiveProvider` is constructed from the factory;
7. the new provider is started fresh;
8. pending PCM is replayed as ONE new framed utterance.

Tested deterministically (5 new tests, `TestReconnectWiring` in
`test_realtime_gemini_service.py`): a safe-boundary loss may resume
without a fresh session; a mid-turn loss is flagged unsafe and never
auto-resumes; `recover_from_mid_turn_loss` recovers with only the pending
audio; the fresh session uses a fresh canonical snapshot, never a stale
provider context; reconnect exhaustion (no factory) falls back to LOCAL
with an explicit notice.

## HYBRID AUDIO WIRING

New module `src/nexa/realtime/gemini/runtime.py`
(`build_gemini_voice_runtime`, `GeminiVoiceRuntime`). Two independent
Pipecat pipelines run side by side, bridged only by plain async calls / an
event stream — never by sharing frame-processor state:

- the provider's OWN headless pipeline (unchanged, built inside
  `GeminiLiveProvider.start()`, M2.6B.2) — no transport of its own, by
  ADR-0004 design;
- a new HARDWARE pipeline: `[transport.input(), vad_processor,
  bargein_controller, _VadToProviderBridge, aec_feeder,
  transport.output()]` under its own `PipelineWorker`/`WorkerRunner`,
  constructed exactly like `voice/runtime.py`'s proven
  `LocalAudioTransport`/`LocalAudioTransportParams`/`SileroVADAnalyzer`/
  `VADProcessor` pattern (device names resolved via the existing
  `find_device_index`/`LocalAudioConfig` — `"respeaker"` in,
  `"usb_speaker"` out, unchanged defaults).

`_VadToProviderBridge` is the one genuinely new piece: it forwards
`VADUserStartedSpeakingFrame`/`InputAudioRawFrame`/
`VADUserStoppedSpeakingFrame` into `provider.user_turn_start()`/
`send_user_audio()`/`user_turn_end()`, and only forwards audio to the
provider **while a locally-detected turn is open** — Gemini's own server
VAD stays OFF (frozen M2.6A baseline); local Silero remains the sole turn
authority (ADR-0004 Decision C). Verified against the installed Pipecat
1.8.1 source that `VADProcessor.process_frame` forwards the raw audio
frame first, then the VAD-derived frames, so both reach this bridge in
order.

Cloud assistant audio is injected into the SAME hardware pipeline from
`GeminiVoiceRuntime._consume_provider_events()` via
`hw_worker.queue_frames([TTSAudioRawFrame(...)])` — the identical
injection mechanism (`PipelineWorker.queue_frames`) already used for the
one-time `LLMRunFrame` kickoff since M2.6B.2. This enters at the pipeline
front, passes harmlessly through the input-side processors (which only
react to VAD-specific frame/turn types), and is intercepted by
`aec_feeder` before `transport.output()` plays it.

`_consume_provider_events()` is the single canonical consumer of
`provider.events()` (an `asyncio.Queue`-backed async generator — single-
consumer by construction); it both drives the router's one true event
entry point (`handle_provider_event`) and keeps `BargeInController`'s
state machine in sync (below), and injects assistant audio. An optional
`on_event` hook lets the operator app observe every event for printing
**from inside this same loop** — a second independent `provider.events()`
reader would race this loop for events and was explicitly avoided (caught
and fixed during this checkpoint; see KNOWN RISKS below for how it was
found).

`dry=True` construction builds every object above EXCEPT the audio device
/ network (no `pyaudio.PyAudio()`, no device index lookup, no Gemini
connection) — proven live in this sandbox (`apps/nexa_cloud_voice_app.py
--dry` runs end to end, see TEST RESULTS).

## AEC WIRING

Reused, unmodified: `AecReferenceHealth` (`nexa.voice.aec`) and
`AecReferenceFeeder` (`nexa.voice_tts.aec_reference`, `AEC_REFERENCE_PCM =
"plug:respeaker"`) — the exact mechanism proven by M2.5A/M2.5B/M2.6A. No
second AEC implementation was written. `AecReferenceFeeder` is placed
immediately before `transport.output()` in the hardware pipeline, so it
tees every `TTSAudioRawFrame` reaching the speaker (Gemini's audio,
injected as `TTSAudioRawFrame` for exactly this reason) to the XVF3800 far-
end reference — the same frame type and the same processor class Piper's
local TTS output already used. `AecReferenceFeeder.setup()` (Pipecat's own
lifecycle hook) starts the feed automatically; failure reports through
`AecReferenceHealth.mark_failed()` → `barge_in_safe` goes False →
`BargeInController` itself already refuses to admit an interruption while
unsafe (existing, unmodified logic in `bargein.py`) — AEC failure stays
fail-safe and visible, never silent, with no new code required for that
guarantee.

## CLOUD BARGE-IN WIRING

Reused, unmodified: `BargeInController` (`nexa.voice.bargein`) and its
owned `InterruptionStateMachine`. The controller only admits a candidate
while `response_in_flight` — this checkpoint wires the two calls that keep
it in sync with cloud turns, driven from the SAME single event-consumption
loop:

- `bargein.notify_response_dispatched()` on the first `AssistantAudioEvent`
  or `AssistantTranscriptionEvent` of a turn (RESPONDING entered, a fresh
  monotonic `response_id` allocated);
- `bargein.notify_response_finished()` on `GenerationCompleteEvent`
  (normal completion — back to IDLE).

`_on_confirmed` (the hook `BargeInController` invokes once an
interruption is confirmed) performs, in order: (1) nothing — Pipecat's own
`broadcast_interruption()` has already torn down queued/playing output
audio **before** this hook runs, so local speaker-stop authority never
waits on anything below; (2) `router.on_interruption()` — freezes
`CloudTurnAccumulator.interrupted=True`, which also makes the accumulator
refuse any further assistant-transcription deltas for this turn (the
R0034 fix); (3) `asyncio.create_task(provider.cancel())` — fire-and-forget,
not on the critical path for local speaker-stop, consistent with "local
speaker-stop authority must not wait for Gemini's server ACK"; (4)
`bargein.notify_interruption_complete()` — cloud turns need no segment-
coalescing capture phase (unlike the local path, which waits to coalesce a
multi-segment interruption utterance before submitting one turn), so the
capture phase is closed immediately, ready to admit a future interruption.

**Spoken-prefix authority**: no new tracker class was built.
`CloudTurnAccumulator.assistant_text` — the transcription deltas already
handed to output before the interruption moment, frozen the instant
`on_interruption()` fires — is architecturally the same precision as the
accepted local mechanism (`SpokenTextTracker` also only tracks text
*handed to* the output stage, not a sample-accurate DAC position); reusing
it directly satisfies "reuse the accepted M2.5B semantic mechanism... do
not fork interruption semantics" without adding a parallel tracker.
`ConversationRouter.set_spoken_prefix()` remains available (unused by this
v1 wiring) for a future, more precise playback-position source.

## CANONICAL HISTORY WIRING

Unchanged from M2.6B.2/M2.6B.2A: `handle_provider_event()` remains the
*only* path a provider's events reach the router/session;
`CloudTurnAccumulator` still guarantees at most one canonical commit per
turn; `ConversationSession.record_external_exchange()` still performs the
actual write. This checkpoint adds no new write path — the HYBRID/barge-in
wiring above only adds calls into the existing entry points
(`router.on_interruption()`, `router.handle_provider_event()`), never a
new one.

## FILES CHANGED

- **New:** `src/nexa/realtime/gemini/runtime.py` — `GeminiVoiceRuntime`,
  `build_gemini_voice_runtime`, `RuntimeMetrics`, `_VadToProviderBridge`
  (built lazily inside `_make_vad_bridge_class`).
- **New:** `apps/nexa_cloud_voice_app.py` — the M2.6B.3 operator entry
  point (`--dry` and live modes).
- **New:** `tests/test_realtime_gemini_runtime.py` — 5 tests: dry
  construction (x2), first-assistant-output dispatches barge-in response,
  generation-complete finishes it, `_on_confirmed` notifies router +
  schedules `provider.cancel()` + closes the capture phase.
- **Modified:** `src/nexa/realtime/gemini/service.py` (Phase 1 reconnect
  wiring — `ReconnectController` integration, `_needs_fresh_session`,
  `needs_fresh_session` property, `_readiness_monitor()`,
  `should_proactively_reconnect()`, readiness-monitor teardown in
  `stop()`).
- **Modified:** `src/nexa/realtime/router.py`
  (+`recover_from_mid_turn_loss()`).
- **Modified (tests):** `tests/test_realtime_gemini_service.py`
  (+5 tests, `TestReconnectWiring`).
- **Docs corrected:** `docs/reports/
  R0034_m2_6b_2a_cloud_turn_reconnect_hardening_20260911.md` (the
  service.py test-count bullet said "+16"; verified via `git show
  c334ccd` — actual split is service.py +15 / turn.py +1 = 16 total,
  matching the section's own TEST RESULTS; corrected to "+15").
- **Unmodified:** every local-voice file (`nexa.voice.*`, `nexa.voice_tts.*`
  ,`nexa.stt.*`, `nexa.tts.*`) — reused directly, byte-for-byte, per the
  LOCAL VOICE FREEZE below.

## TEST RESULTS

- `python -m unittest tests.test_realtime_gemini_service -v`: 34 tests,
  all pass (29 prior + 5 new `TestReconnectWiring`).
- `python -m unittest tests.test_realtime_gemini_runtime -v`: 5 tests, all
  pass.
- Full suite: `python -m unittest discover -s tests`: **896 tests, OK
  (skipped=7)** — 886 (R0034 baseline) + 5 + 5 = 896, **zero regressions**.
- `ruff check src tests apps`: all checks passed (on every file touched or
  added this checkpoint).
- `git diff --check`: clean (no whitespace errors).
- `pip check`: clean.
- Import-isolation (in-process, this checkpoint): importing
  `nexa.realtime.router` + `nexa.realtime.policy` alone never pulls
  `nexa.realtime.gemini` (or `.gemini.runtime`/`.gemini.service`) into
  `sys.modules` — confirmed by direct inspection, not just absence of an
  import statement.
- Secret scan: no literal key/token patterns in any new or modified file.
- `apps/nexa_cloud_voice_app.py --dry`: run live in this sandbox — object
  graph constructs cleanly (provider, router, `AecReferenceHealth`,
  `BargeInController`), prints `router policy: cloud_preferred`,
  `provider readiness: connecting`, exits 0. No audio device opened, no
  network touched.

Of the charter's 17 deterministic items: the 5 reconnect items (safe-
boundary resume; mid-turn-unsafe never auto-resumes; fresh-provider
recovery with only pending PCM; no replay of already-sent PCM; fresh
snapshot never stale) are covered by `TestReconnectWiring`
(R0034+this checkpoint). Provider-event-only-full-turn-commits-exactly-
once, late-audio/text-after-interruption-discarded, and successful-
resumption-doesn't-duplicate-history are covered by the existing R0034
suite (`TestNoManualInjectionFullCloudTurn`,
`TestTurnOrderingPermutations`, `TestMidTurnReadinessLoss`) — unchanged by
this checkpoint. New this checkpoint: barge-in dispatch/finish tracking
and the `_on_confirmed` three-action contract
(`TestBargeInWiring`). **Not yet independently tested at the wired
(hardware-pipeline) level** (deferred to hardware evidence, since they
require a real pipeline run): AEC-tee-exactly-once under real audio,
barge-in-stops-playback-before-server-ACK timing, and reconnect-
exhaustion's explicit LOCAL fallback notice observed on a live connection
— all are exercised at the unit/construction level above, and the
underlying mechanisms (`AecReferenceFeeder`, `BargeInController`,
`ConversationRouter._handle_cloud_failure`) are the same, previously
operator-confirmed local-voice primitives, unmodified.

## LOCAL VOICE FREEZE CHECK

No file under `nexa.voice.*`, `nexa.voice_tts.*`, `nexa.stt.*`,
`nexa.tts.*`, or `nexa.voice_conversation.*` was modified this checkpoint.
`BargeInController`, `AecReferenceFeeder`, `AecReferenceHealth`,
`InterruptionStateMachine`, `LocalAudioConfig`, `find_device_index` are
all imported and used exactly as they already exist — zero edits, zero
new subclasses, zero monkeypatching. `gemma4:e4b`, whisper.cpp
`base`/`q8_0`, `num_thread=2`, `keep_alive=30m`, warm-up,
`LanguageIdGuard`, `ResponseLanguageResolver`, Piper, `NexaSpeechPlanner`,
`ProviderWindow(keep_entries=0)` are untouched — the new cloud runtime
does not construct any of them (it uses `build_default_session()` for the
canonical session/local `ModelProvider`, exactly like every existing
voice app, and never touches the STT/TTS stack at all — Gemini Live is the
speech-to-speech path in this app). Full regression suite green (896/896,
0 skipped beyond the pre-existing 7) is the acceptance evidence.

## KNOWN RISKS (carried + new)

- **Not hardware/Gemini-tested.** Everything above is proven by
  construction-only (`--dry`) validation and unit tests against a FAKE
  terminal service. The real reSpeaker/USB-speaker/XVF3800/Gemini path has
  not been exercised. This is Phase 7, explicitly deferred to the
  operator (below).
- **`_on_event` hook found and fixed during this checkpoint**: the first
  draft of the operator app read `provider.events()` a second time in a
  separate print-only task, believing an async generator was safely
  multi-consumer. Re-reading `service.py` showed `events()` is backed by
  ONE `asyncio.Queue` (`await self._events.get()`) — a second reader would
  race the runtime's own consumer for every event and could steal one the
  router needs to commit a turn. Fixed by adding a single `on_event` hook
  invoked from inside the runtime's one true consumption loop; the app
  now uses that hook instead of a second reader. No test currently proves
  a regression of this specific mistake (the fix predates any test that
  could have caught it) — flagged here rather than silently corrected.
- **Reconnect real-cloud policy untested against a live socket.** The
  `_readiness_monitor` design is based on reading Pipecat's own
  `_connection_task_handler`/`_reconnect` source, not on inducing a real
  connection drop against Gemini. `should_proactively_reconnect()` has
  never been exercised against an 8+ minute live connection (deliberately
  — no quota-consuming test was run).
- **Cloud spoken-prefix precision** is bounded by transcription-delta
  granularity (whatever text Gemini has sent as `output_transcription`
  deltas by the interruption instant), same as the local mechanism's own
  bound — not a sample-accurate playback position. Accepted as v1, per
  charter.

## WHAT REMAINS FOR M2.6B

Phase 7 (real Gemini + real hardware operator acceptance) — see below.
After that: Phase 5 metrics are logging-only right now (`RuntimeMetrics`,
console `logger.info` lines) — no persisted telemetry file, per the "no
raw audio retention merely for metrics" instruction; if the operator run
surfaces a need for structured telemetry, that is a follow-up, not part
of this checkpoint's charter. `ConversationPolicy.AUTO` remains
provisional (no classifier) — unchanged, not in scope. `set_spoken_prefix`
remains available but unused by v1 wiring.

## EXACT LAUNCH COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

(Credential: `NEXA_GEMINI_API_KEY` env var, or
`~/.config/nexa/secrets/gemini.env`, per `gemini/credentials.py` —
already the established location; nothing new to configure.)

A `--dry` run (no hardware, no network) can be repeated any time as a
sanity check:

```
.venv/bin/python apps/nexa_cloud_voice_app.py --dry
```

## READY LINES TO WAIT FOR

Before speaking, wait for **both** of these lines on stdout:

```
· PROVIDER READINESS: READY
>>> CLOUD_PROVIDER_READY <<<
```

and

```
  ✓ AEC_REF_ACTIVE
```

If `✗ AEC REF DOWN — barge-in unsafe` appears instead, barge-in is unsafe
(mic gate does not have the AEC reference) — stop and report it rather
than continuing.

## WHAT THE OPERATOR SHOULD DO

A short, natural conversation only — **not** a duration/quota/reconnect
test:

1. Ask one question in Polish.
2. Ask one natural follow-up (same language).
3. Ask one question in English.
4. Deliberately interrupt NeXa once, mid-reply, then continue naturally
   after the interruption.
5. Stop with Ctrl+C.

Do **not** keep talking merely to generate metrics, and do **not** run an
8–11 minute session to exercise reconnect/GoAway — that is explicitly out
of scope for this acceptance run (quota-conscious, per the charter).
Report back: was Gemini's voice audible and natural; did PL and EN both
work; was latency comparable to the M2.6A baseline (~0.75–0.81 s median
EOT→first audible — a small variance is fine, only a material regression
matters); did the AEC reference stay active throughout; did NeXa's own
voice NOT self-trigger a false barge-in; did the deliberate interruption
actually stop playback and let you continue; did anything look duplicated
or lost in the canonical transcript afterward.

## COMMIT HASH

(recorded in a follow-up commit once made — see the session's final
message for the TEST-READY commit hash.)

## GIT STATUS

Not pushed (per the standing constraint for this entire M2.6 body of
work). Working tree at the time of this report: the files listed under
FILES CHANGED, uncommitted until the TEST-READY commit below.
