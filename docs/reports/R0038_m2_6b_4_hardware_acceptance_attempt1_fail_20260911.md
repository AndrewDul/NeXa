# R0038 — M2.6B.4: Production Hardware Acceptance Attempt #1 — FAIL

## TASK RESULT

**FAIL (real operator hardware/Gemini run).** The first real production
run reached `CLOUD_PROVIDER_READY`, `AEC_REF_ACTIVE`, and audible Sulafat
speech, and a normal conversation was possible — but three real production
regressions were observed. **Hardware acceptance is NOT marked PASS.
`M2.6B` is NOT marked COMPLETE.** All three root causes were reproduced
deterministically and fixed in this checkpoint. **No further Gemini call
was made after the failed run; nothing in this checkpoint touches
hardware.** Local voice remains untouched.

## LIVE FAILURE RECONSTRUCTION

Operator command: `.venv/bin/python apps/nexa_cloud_voice_app.py` (real
reSpeaker + real USB speaker + real Gemini).

**PASS:**
- Gemini connected; `>>> CLOUD_PROVIDER_READY <<<` reached.
- `✓ AEC_REF_ACTIVE` reached.
- Sulafat audible; normal conversation was possible.
- New user turns did reach Gemini.
- Provider interruption ACKs occurred (twice).

**FAIL:**
1. `_VadToProviderBridge#0` failed real Pipecat setup: `Error setting up
   processor: 'RuntimeMetrics' object has no attribute 'setup'`; the
   worker then declared the bridge unusable; teardown failed the same
   way (`'RuntimeMetrics' object has no attribute 'cleanup'`).
2. Operator interrupted a black-hole explanation mid-reply to ask about
   the Sun. The old reply's audio kept playing to completion; a
   `✂ cloud interruption acknowledged by provider` line appeared (twice
   across the session) but did not stop local playback; the Sun answer
   was already being generated while the old audio was still audible.
3. Two English questions ("Can you explain what happens near the event
   horizon?", then a speed-of-light question) were both answered in
   Polish, despite ADR-0004 Option A (native language mirroring) being
   the active mechanism and prior M2.6A spike evidence showing it work.

Per the charter: the long ALSA "Unknown PCM" enumeration noise is not
treated as a failure (the run still reached READY/ACTIVE/audible); the
one `AEC REF DOWN` observed fell at Ctrl+C teardown, treated as expected
shutdown, not a mid-conversation failure (no evidence otherwise).

## VAD BRIDGE ROOT CAUSE

**Confirmed from installed Pipecat 1.8.1 source, not assumed.**
`FrameProcessor.__init__` (`processors/frame_processor.py:222-257`) itself
already does:

```python
def __init__(self, *, name=None, enable_direct_mode=False,
             metrics: FrameProcessorMetrics | None = None, **kwargs):
    ...
    self._metrics = metrics or FrameProcessorMetrics()
    self._metrics.set_processor_name(self.name)
```

and its own `setup()`/`cleanup()` (`frame_processor.py:653,669`) call
`await self._metrics.setup(self.task_manager)` /
`await self._metrics.cleanup()` on whatever object holds that name.
`_VadToProviderBridge.__init__` called `super().__init__()` with no
arguments (so Pipecat's own `FrameProcessorMetrics()` was constructed and
assigned to `self._metrics` as usual) and then immediately did
`self._metrics = metrics` — silently **overwriting** Pipecat's own metrics
object with NeXa's `RuntimeMetrics` instance. Pipecat's real `setup()`/
`cleanup()` then called `.setup()`/`.cleanup()` on NeXa's telemetry object
instead — exactly the live error. Confirmed to be new to this module: none
of the accepted local-voice `FrameProcessor` subclasses
(`BargeInController`, `AecReferenceFeeder`) ever store anything under
`self._metrics`.

**Fix:** renamed the attribute to `self._nexa_metrics` throughout
`_VadToProviderBridge`. A new invariant is documented in the class: never
give a NeXa-owned field the same name as any Pipecat-reserved attribute on
a `FrameProcessor` subclass.

## LOCAL PLAYBACK BARGE-IN ROOT CAUSE

**Source-audited the exact chain**, not guessed:
`AssistantAudioEvent` → `_consume_provider_events` → `TTSAudioRawFrame` →
`hw_worker.queue_frames()` → the hardware `Pipeline`
(`bargein` → `bridge` → `aec_feeder` → `transport.output()`) →
`BaseOutputTransport.MediaSender.handle_audio_frame` → its own
`_audio_queue` → the output audio task → the speaker.

`BargeInController._do_confirm()` calls `broadcast_interruption()`
(`frame_processor.py:1017`), which pushes an `InterruptionFrame` both
directions and, on reaching `BaseOutputTransport.handle_interruptions()`
(`transports/base_output.py:566`): cancels the clock/video tasks, and
(without a mixer/uninterruptible frames, our case) **cancels and recreates
the output audio task** — which discards whatever was in `_audio_queue`
at that instant — then fires `_bot_stopped_speaking()`.

**This is a real, working, one-time clear.** The root cause is what
happens *after* it: `_consume_provider_events`'s `async for event in
provider.events()` loop keeps running and keeps calling
`hw_worker.queue_frames([TTSAudioRawFrame(...)])` for every subsequent
`AssistantAudioEvent` — including ones for the SAME (just-interrupted)
generation that were **already sitting in `provider.events()`'s own
queue** before the interruption fired, or that Gemini produces in the
brief window before it honours the cancel signal sent via
`provider.cancel()` (fire-and-forget, asynchronous, never guaranteed to
land before more audio arrives). Nothing in the pre-R0038 code
distinguished "audio for the generation that was just invalidated" from
"audio for a genuinely new generation" — both are plain
`TTSAudioRawFrame`s with no marker. So `broadcast_interruption()`'s
one-time queue clear got immediately refilled by the OLD generation's own
trailing audio, which then played to completion exactly as observed live.
**`provider.cancel()`/the server's interruption ACK is not the same thing
as "old PCM removed from the local hardware playback path"** — confirmed,
not merely suspected.

## LOCAL PLAYBACK FIX

New `_ResponseGenerationGuard` (`src/nexa/realtime/gemini/runtime.py`) —
"hard local output clear (`broadcast_interruption`, unchanged) +
generation invalidation (this guard)", never either alone:

- `start_new_generation() -> int`: allocates a fresh, monotonically
  increasing generation id.
- `is_valid(generation_id) -> bool`: is this id still allowed to produce
  audible output.
- `interrupt()`: invalidates the CURRENT generation immediately (sets the
  valid-id sentinel to `0`, never a real id). Called **synchronously**
  from `_on_confirmed` (no `await` before it in that function), so it
  always completes before any other coroutine gets a turn on this
  single-threaded event loop — no race against `_consume_provider_events`.

`_consume_provider_events` now checks `self.generation_guard.is_valid(current_gid)`
before ever calling `hw_worker.queue_frames()` for an `AssistantAudioEvent`,
and before injecting the deterministic `TTSStoppedFrame` on
`GenerationCompleteEvent`. An invalid chunk is dropped — never reaching
the speaker **or** the AEC reference (both are fed from the exact same
`hw_worker.queue_frames()` call, so preventing the call structurally
prevents both) — and logged via a new
`metrics.dropped_invalidated_generation_audio()` line for operator
visibility.

Satisfies the required invariant exactly: local interruption confirmed →
immediately (1) `broadcast_interruption()` already stopped/invalidated
old local playback (Pipecat's own mechanism, unchanged) → (2) no further
OLD-generation PCM reaches the speaker (the guard) → (3) none reaches the
AEC reference either (same call site) → (4) `provider.cancel()` happens
independently/asynchronously (fire-and-forget, unchanged) → (5) new user
speech is still captured (VAD/bridge input path untouched) → (6) a new
Gemini turn may start → (7) OLD buffered audio can never resume (its
generation id is permanently invalid; `start_new_generation()` ids are
monotonic and never reused).

## RESPONSE-GENERATION INVALIDATION DESIGN

**A real design bug was found and fixed while building this**, worth
recording precisely: the first draft tried to fold "the next assistant
output needs a fresh dispatch (to `BargeInController`/`_ResponseLifecycle`)"
into the SAME guard, keyed off the same interrupt-vs-valid state (an
`interrupt()` call both invalidated the current generation *and* armed
"the next audio event gets a new id"). This conflated two different
signals: **"may this chunk play"** (a property of the generation itself)
and **"has a genuinely new local user turn started"** (a property of the
conversation, provable only by a fresh, final `UserTranscriptionEvent` —
R0034's own proven message-ordering guarantee: input transcription always
precedes that turn's own assistant content). Trailing audio for an
invalidated generation and the interrupting utterance's own brand-new
reply both arrive as plain `AssistantAudioEvent`s with no marker
distinguishing them — so the first draft's very next audio event after an
interrupt (which is almost always the trailing OLD-generation audio, not
the new one) was treated as a fresh, valid dispatch, defeating the guard
entirely (caught by
`TestGenerationGuardWiredIntoConsumer.test_late_invalidated_generation_audio_never_reaches_hardware`,
which failed against the first draft with exactly the live symptom
reproduced deterministically).

**Fixed design**: `_ResponseGenerationGuard` is deliberately dumb — only
`is_valid`/`start_new_generation`/`interrupt`, no dispatch-timing
knowledge at all. `_consume_provider_events` tracks dispatch timing
itself via a plain `dispatched_for_turn` bool, reset specifically on a
**fresh, final** `UserTranscriptionEvent` (a genuinely new local user turn
was just heard by Gemini) — never merely because a generation was
interrupted. This closes the conflation and, as a side effect, **fixes a
second, previously-latent bug**: the pre-R0038 code never reset its
dispatch-tracking flag on normal `GenerationCompleteEvent` at all (only on
`RealtimeProviderFailedError`), so a second consecutive normal turn would
never have re-dispatched to `BargeInController` — never previously
exercised by any single-turn test.

## LANGUAGE SNAPSHOT ROOT CAUSE

**Reconstructed the exact production construction path, not guessed.**
`apps/nexa_cloud_voice_app.py`'s snapshot construction:

```python
snapshot = runtime.router._snapshot_builder(
    session, policy_name=ConversationPolicy.CLOUD_PREFERRED.value,
    active_provider_name="cloud",
)
```

**`language_preference` is never passed** — `build_cloud_context_snapshot`'s
own default is `None`. Per its source:

```python
instruction = CLOUD_ROLE_CARD
if language_preference:
    instruction += f" Current language preference: {language_preference}."
```

Since `language_preference` is `None` (falsy), **no** "Current language
preference" line was ever appended — `system_instruction` was exactly
`CLOUD_ROLE_CARD` ("...Mirror the user's language (Polish or English).").
`build_default_session()` starts with **empty** history
(`ConversationSession._history: list = field(default_factory=list)`) and
the app never calls `warm_up_session()`, so `recent_turns` was `()` —
**zero bias from prior turns either.** Grepped `service.py`/`voice.py` for
any hardcoded language config: none. **The production snapshot was
genuinely neutral — NeXa did not force a Polish preference anywhere.**
This is a real Gemini native-mirroring reliability gap under live
production conditions, not a NeXa-side bug.

(Existing test `tests/test_realtime_snapshot.py::test_language_preference_carried_but_optional`
already proves this exact contract — `without_pref.system_instruction ==
CLOUD_ROLE_CARD` and `with_pref.language_preference == "pl"` — cited as
sufficient existing coverage for the charter's item 15, not duplicated.)

Also checked whether Gemini's own `input_transcription.language_code` (a
real field on `google.genai.types.Transcription`) could have been used as
a free per-turn language signal instead of local LID: installed
`gemini_live/llm.py:1937` passes `self._settings.language` (a **static,
configured** setting, defaulting to `"en-US"` and never set by
`GeminiLiveProvider`) into every `TranscriptionFrame.language` — **not**
a dynamic per-utterance detection result. Confirmed via source: this
field is useless for real language detection in our configuration; local
LID is the only reliable option, exactly as the charter anticipated.

## OPTION A vs OPTION B DECISION

Per the charter's own decision order: **the production snapshot is
neutral, and Gemini still answered English input in Polish twice — this
activates ADR-0004's own documented Option-B fallback.**

**What was built (bounded to what the charter allows without a live
call):** reused, verbatim, the two ALREADY-ACCEPTED local-voice
mechanisms — `nexa.stt.WhisperCppLanguageDetector` (R0024's own
`argmax(p_pl, p_en)` method) and `nexa.conversation.ResponseLanguageResolver`
(sets a sticky preference **only** on an explicit directive like "always
answer in English"/"odpowiadaj mi po polsku", never from the language
merely spoken — exactly the "no implicit sticky preference" requirement).
`_VadToProviderBridge` now also buffers each utterance's PCM locally (a
parallel copy, never delaying what streams live to Gemini);
`_consume_provider_events` correlates it with that turn's own final
`UserTranscriptionEvent` (FIFO, safe given strictly sequential turn-taking)
and runs detection+resolution fire-and-forget (`asyncio.create_task`,
never on the critical audio path). A sticky decision updates
`self._recovery_language_preference` for any FUTURE fresh-session
snapshot (ADR-0004 Amendment 1 §2's own documented mechanism — a sticky
command reaches the provider only on the next new/resumed session). The
charter's exact diagnostic keys are logged:
`SNAPSHOT_LANGUAGE_PREFERENCE`/`TURN_INPUT_LANGUAGE`/
`TURN_INPUT_TRANSCRIPT`/`LANGUAGE_ROUTING_MODE` (routing mode logged as
`"native"` — see below).

**What was honestly NOT built, and why:** this does **not** retroactively
steer the response Gemini is already generating for the CURRENT turn.
Investigated whether a low-risk, per-turn steering primitive exists: (a)
a mid-session text hint alongside the audio turn — no verified mechanism
found in the installed Pipecat/google-genai surface, and verifying one
would require a live call, out of scope here; (b) a forced
reconnect-with-updated-`system_instruction` on every detected language
switch — technically buildable with the SAME machinery as
`recover_from_mid_turn_loss`, but carries a real, unmeasured reconnect
latency cost per switch that was not asked to be traded off here. Per the
charter's own permission ("if strict same-turn Gemini steering genuinely
requires a controlled turn-boundary/session mechanism... state that
honestly"): **native mirroring (Option A) remains the active per-turn
mechanism; same-turn steering is an open, explicitly documented gap, not
attempted in this checkpoint.** `LANGUAGE_ROUTING_MODE=native` reflects
this honestly in the logs — it is not yet `"strict"`.

## FILES CHANGED

- **Modified:** `src/nexa/realtime/gemini/runtime.py` —
  `_VadToProviderBridge` renamed its metrics attribute to
  `self._nexa_metrics` (FAILURE 1 fix); added `_ResponseGenerationGuard`
  and wired it into `_consume_provider_events`/`_on_confirmed` (FAILURE 2
  fix, plus the `dispatched_for_turn` redesign); added
  `_PendingUtteranceAudio`, utterance-audio buffering in
  `_VadToProviderBridge`, `GeminiVoiceRuntime._analyze_turn_language`, and
  `RuntimeMetrics.language_diagnostics`/
  `dropped_invalidated_generation_audio` (FAILURE 3); `GeminiVoiceRuntime`
  dataclass gained `generation_guard`/`pending_utterance_audio`/
  `language_detector`/`language_resolver` fields; `build_gemini_voice_runtime`
  constructs the language detector best-effort (never a hard dependency)
  and threads all new objects through both `dry`/real construction paths
  and the bridge constructor. Module docstring records all three fixes.
- **Modified (tests):** `tests/test_realtime_gemini_runtime.py` — +13 net
  new tests: `TestResponseGenerationGuard` (5, pure unit),
  `TestVadBridgeProcessorLifecycle` (2, real Pipecat setup/process/cleanup
  against a genuine `Pipeline`/`PipelineWorker`/`WorkerRunner` — not
  construction-only `--dry`), `TestGenerationGuardWiredIntoConsumer` (1,
  reproduces the exact live scenario end-to-end and measures
  confirm→invalidation latency), `TestCloudSameTurnLanguage` (5, using
  REAL recorded PL/EN WAV fixtures already in the repo, never fabricated
  silence/noise). `TestBargeInWiring`/`TestMidTurnRuntimeRecovery`
  construction helpers updated for the two new required
  `GeminiVoiceRuntime` fields.
- **New:** this report.
- **Unmodified:** `src/nexa/realtime/router.py`, `.../service.py`,
  `.../turn.py`, `.../snapshot.py` (its existing test already proves the
  neutral-snapshot contract — no change needed), every local-voice file
  under `nexa.voice.*`/`nexa.voice_tts.*`/`nexa.stt.*`/`nexa.tts.*`/
  `nexa.conversation.*` (both `WhisperCppLanguageDetector` and
  `ResponseLanguageResolver` are reused exactly as they already exist —
  zero edits to either).

## TEST RESULTS

Charter's 17 required proofs — items 1-9 (VAD bridge + playback
invalidation), covered by the new tests above; items 10-15 (language),
covered by `TestCloudSameTurnLanguage` + the existing snapshot test; item
16 (local voice unchanged) — no local-voice file touched, confirmed by
`git diff --stat`; item 17 (full baseline green) — confirmed below.

- `python -m unittest tests.test_realtime_gemini_runtime -v`: **32 tests,
  OK.**
- Full suite: `python -m unittest discover -s tests`: **923 tests, OK
  (skipped=7)** — 910 (M2.6B.3B baseline) + 13 net new, **zero
  regressions**.
- `ruff check src tests apps`: all checks passed.
- `git diff --check`: clean. `pip check`: clean.
- Import-isolation (in-process): `nexa.realtime.router`/`.policy` alone
  never pull `nexa.realtime.gemini` into `sys.modules`.
- Secret scan: no literal key/token patterns in any changed file.
- `apps/nexa_cloud_voice_app.py --dry`: run live in this sandbox after
  every change — object graph (including the real local language
  detector, which does construct successfully in this sandbox) builds
  cleanly, no audio device or network touched.
- No Gemini call, no hardware test performed in this checkpoint.

## LOCAL VOICE FREEZE CHECK

`git diff --stat` for this checkpoint touches exactly
`src/nexa/realtime/gemini/runtime.py` and
`tests/test_realtime_gemini_runtime.py` — no file under `nexa.voice.*`,
`nexa.voice_tts.*`, `nexa.stt.*`, `nexa.tts.*`, or `nexa.conversation.*`
was modified. `WhisperCppLanguageDetector` and `ResponseLanguageResolver`
are imported and used exactly as they already exist. `gemini-3.1-flash-live-preview`,
`Sulafat`, server VAD OFF, local Silero turn authority, the XVF3800 AEC
architecture, `ConversationSession` canonical authority, `LOCAL_ONLY`
default, and `CloudContextSnapshot`'s privacy boundary are all unchanged —
this run showed no model-quality/reasoning regression; the production
*integration* is what failed, and only the integration was touched. Full
regression suite green (923/923, 0 new failures beyond the pre-existing 7
skips) is the acceptance evidence.

## KNOWN RISKS (carried + new)

- **Not yet re-tested on real hardware.** All three fixes are proven
  deterministically (including, for FAILURE 1, against a genuine Pipecat
  `Pipeline`/`PipelineWorker`/`WorkerRunner` — not construction-only) but
  none have been exercised against the real reSpeaker/USB-speaker/Gemini
  path yet. That is the explicit next step.
- **Same-turn Gemini language steering remains unresolved** (documented
  above, not a regression from this checkpoint — the gap already existed,
  now made visible and diagnosed rather than silently assumed away).
- **`should_proactively_reconnect()` still has no production caller**
  (unchanged, carried from R0036/R0037). Per the charter: this does not
  block the next short operator run, but `M2.6B` must not be marked fully
  COMPLETE until this gate is either implemented and deterministically
  validated, or explicitly changed by an ADR amendment.
- **Local LID adds a background `asyncio.create_task` per closed
  utterance** (whisper.cpp LID inference, off the critical audio path).
  Best-effort: any exception is caught and logged, never propagated; if
  whisper.cpp is not installed/configured, the whole feature is a no-op
  (`language_detector=None`), cloud voice continues exactly as before.

## WHAT REMAINS FOR M2.6B

The real Gemini/hardware operator retest — see EXACT NEXT LIVE RETEST
COMMAND below — is the immediate next step. After a PASS on that retest,
`M2.6B` still cannot be marked COMPLETE until the standing
proactive-reconnect condition (above, carried from R0036/R0037) is
resolved.

## COMMIT HASHES

(recorded in a follow-up commit once made — see the session's final
message.)

## GIT STATUS

Not pushed (per the standing constraint for this entire M2.6 body of
work).

## EXACT NEXT LIVE RETEST COMMAND

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Wait for both READY lines exactly as before:

```
· PROVIDER READINESS: READY
>>> CLOUD_PROVIDER_READY <<<
```

```
  ✓ AEC_REF_ACTIVE
```

Suggested retest focus (not a new/different conversation shape — just
re-attempt what previously failed): a short reply, deliberately
interrupted mid-sentence, confirming playback stops immediately and the
old reply never resumes or continues after the new one starts; and at
least one English question after some Polish conversation, to observe
`TURN_INPUT_LANGUAGE`/`SNAPSHOT_LANGUAGE_PREFERENCE` in the logs and
whether native mirroring behaves differently this time (still not
expected to be forced correct — Option A remains active; this is
observational evidence-gathering, not a claim the language issue is
fixed). No deliberately long session, no quota test, no reconnect-age
test.
