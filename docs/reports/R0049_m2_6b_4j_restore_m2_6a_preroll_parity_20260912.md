# R0049 — M2.6B.4J: Restore Accepted M2.6A User-Audio Preroll Parity

**Date:** 2026-09-12
**Milestone:** M2.6B.4J (fix, following the R0048 diagnostic audit)
**Status:** Architecture implemented and proven deterministically (18/18
charter test requirements). **LOCAL HARDWARE POST-FIX ACCEPTANCE
PENDING** — a real-hardware A/B re-capture with the fixed bridge has not
yet been performed by the operator; no PASS is fabricated here. No
Gemini call, not pushed. **Hardware acceptance remains FAIL. `M2.6B`
remains IN PROGRESS.**

## REAL HARDWARE CONFIRMATION FROM R0048

R0048's own diagnostic tool was run for real by the operator, on the
real reSpeaker, no Gemini involved: 10 utterances captured; every RAW
WAV contained the complete spoken phrase; every PRODUCTION_FORWARDED WAV
was audibly clipped at the start. For "Czarna dziura" the operator
specifically heard forwarded captures beginning at "dziura" or "arna
dziura"; for "Czarna dziura powstaje," a forwarded capture could begin at
"dziura powstaje." (Full detail: R0048's own new "OPERATOR REAL HARDWARE
FOLLOW-UP — 2026-09-12" section, amended alongside this checkpoint.) This
is the evidentiary basis for implementing the fix this checkpoint,
without re-deriving it.

## ROOT CAUSE

Unchanged from R0048, restated for this report's own completeness:
`_VadToProviderBridge` only ever called `provider.send_user_audio()`
once its local `_turn_open`/capturing state was `True` — set only by
`VADUserStartedSpeakingFrame`, which Silero (Pipecat's own unmodified
`VAD_START_SECS = 0.2 s` default) fires only after 0.2 s of
already-elapsed confirmed voice activity. `VADProcessor` itself forwards
every raw frame downstream unconditionally before running VAD detection
(so that 0.2 s of real audio physically reaches this module), but the
bridge discarded it — never buffering, never forwarding. The accepted
M2.6A spike never had this problem because its Silero VAD analyzer lived
in the SAME Pipecat pipeline as `GeminiLiveLLMService`, whose own
built-in preroll buffer (auto-sized to `start_secs + 0.1 s` via a
`SpeechControlParamsFrame` only a co-located `VADController` broadcasts)
was therefore continuously fed. Current production's separate-pipeline
topology left that SAME, still-present, still-correct mechanism inside
`GeminiLiveLLMService` permanently starved.

## CHOSEN FIX

**Option A** (bounded rolling PCM pre-buffer added to
`_VadToProviderBridge`), exactly as directed. No provider-boundary
redesign, no continuous idle-audio streaming to Gemini, no second VAD
instance, no change to `start_secs`/`stop_secs`/confidence/barge-in
thresholds. Reuses `nexa.stt.utterance_buffer.UtteranceBuffer`
**verbatim, unmodified** — the existing, already-tested, LOCAL-VOICE-proven
mechanism `nexa.voice.runtime` already depends on for the identical M2.2
pre-roll requirement (ring while idle → linear buffer while capturing →
idempotent exactly-once start/stop) — rather than reimplementing an
equivalent ring buffer. `git diff --stat -- src/nexa/stt` is empty: the
class itself was not touched.

## WHY OPTION A

- **Smallest change**: one new bounded buffer inside one existing
  processor; zero changes to the provider boundary
  (`RealtimeVoiceProvider.send_user_audio`'s own contract), zero changes
  to `GeminiLiveProvider`/`service.py`.
- **Preserves R0045 provider/session isolation completely**: R0045's own
  `_replace_provider_after_bargein`/`sealed_utterances`/quarantine logic
  is entirely untouched (`git diff` confirms zero lines changed in that
  method) — it already receives the FULL, now-correct PCM because the
  bridge's own buffer now seeds it correctly, not because the replacement
  mechanism itself changed.
- **Preserves R0046 canonical history ownership completely**: zero
  changes to `has_turn_awaiting_assistant()`, `begin_cloud_turn()`,
  `CloudTurnAccumulator`, or the confirmed/non-lexical interruption
  lifecycle — this checkpoint is PCM ingress only.
- **Restores the exact M2.6A property** (audio immediately before local
  VAD confirmation is included in the user turn) without reintroducing
  M2.6A's own topology (VAD co-located with the LLM service) or its
  trade-offs.
- **No Gemini/Pipecat topology rewrite required** — Option B (continuous
  ingress) and Option C (drive the provider's own pipeline with a real
  `VADController`) were both evaluated in R0048 and require materially
  larger, riskier changes for no additional benefit once the pre-buffer
  restores parity.

## PREROLL CAPACITY DERIVATION

Never hardcoded. A new module constant,
`AUTOSIZED_PREROLL_MARGIN_SECS = 0.1`, mirrors Pipecat's own
`AUTOSIZED_USER_AUDIO_PREROLL_MARGIN_SECS` (`pipecat/services/google/
gemini_live/llm.py`) — same value, same documented rationale ("absorb
small timing slop... and give a bit of extra audio context"). In
`build_gemini_voice_runtime`, immediately after constructing the ACTUAL
`vad_analyzer` (`SileroVADAnalyzer(sample_rate=INPUT_SAMPLE_RATE_HZ,
params=VADParams(stop_secs=0.5))` — unchanged construction):

```python
preroll_ms = int((vad_analyzer.params.start_secs + AUTOSIZED_PREROLL_MARGIN_SECS) * 1000)
```

— reading `vad_analyzer.params.start_secs` from the analyzer instance
itself (never a hardcoded `0.2`), so this always tracks whatever VAD
config is actually active. With production's current, unmodified
`start_secs=0.2` this evaluates to **300 ms** — the same effective
preroll the accepted M2.6A spike had. `preroll_ms` is passed into
`_VadToProviderBridge`'s new required constructor parameter, which
constructs `UtteranceBuffer(sample_rate=INPUT_SAMPLE_RATE_HZ,
pre_roll_ms=preroll_ms)` — `INPUT_SAMPLE_RATE_HZ` (16 000, mono, 16-bit
PCM) is the same existing constant already used throughout this module;
`UtteranceBuffer`'s own constructor derives the byte capacity from
`sample_rate * (pre_roll_ms/1000) * bytes_per_sample * channels`
(unchanged, existing logic, not reimplemented).

## NORMAL TURN PCM OWNERSHIP

`_VadToProviderBridge` no longer maintains a separate `_utterance_buffer`
bytearray at all — `self._pcm` (one `UtteranceBuffer` instance) is the
ONE coherent source of truth, serving both roles through its own
existing state machine:

- **Idle** (no local turn open): every `InputAudioRawFrame` is fed to
  `self._pcm.append_audio(...)`, which routes it into the bounded ring
  (never sent to the provider).
- **`VADUserStartedSpeakingFrame`**: the ring's current bytes are read
  (`b"".join(self._pcm._ring)`) — this necessarily already contains the
  very frame that triggered VAD confirmation, since `VADProcessor`
  forwards every frame downstream before running detection on it
  (confirmed from installed source in R0048; re-verified unchanged this
  checkpoint) — then `self._pcm.mark_speech_started()` transfers that
  same content into the linear (capturing) buffer, clearing the ring.
  The canonical turn opens exactly as R0046 requires
  (`has_turn_awaiting_assistant()` gate unchanged); if not quarantined,
  `provider.user_turn_start()` fires, then exactly ONE
  `provider.send_user_audio(preroll)` call for the retained prefix —
  before any further live frame.
- **Subsequent `InputAudioRawFrame`s** (turn open): fed to
  `append_audio()` (now routes to the linear buffer, since
  `is_capturing` is `True`) and, if not quarantined, forwarded live via
  `send_user_audio()` — unchanged live-forwarding behavior, now simply
  preceded by the one preroll call.
- **`VADUserStoppedSpeakingFrame`**: `self._pcm.mark_speech_stopped()`
  returns the COMPLETE utterance (preroll + every live frame since),
  idempotently (`b""` on a duplicate/spurious stop).

Result: the RAW MIC contract — `[pre-VAD speech onset][post-VAD
speech...]` — reaches the provider exactly once, in correct order, no
duplicate boundary frame, no missing prefix, no reordered PCM. Proven
directly (`TestVadBridgePrerollParity`, real bridge + real Pipecat
pipeline, tests 1, 3-5, 6, 11).

## BARGE-IN PCM OWNERSHIP

The charter's own worked example (`[PREE][POST]` → fresh provider
receives `[PREEPOST]` exactly once) is now a literal, passing test
(`test_7_8_confirmed_bargein_replay_includes_preroll_exactly_once`):
`PREE` fed while idle (goes to the ring), quarantine set (matching
`_on_confirmed`'s own synchronous action), then `VADUserStartedSpeakingFrame`
+ `POST` + `VADUserStoppedSpeakingFrame` — the bridge, being quarantined,
never sends anything live; `mark_speech_started()` still transfers `PREE`
into the linear buffer (preroll capture is independent of quarantine
state — it must be, since the interrupting utterance's own onset still
needs to survive), `POST` is appended to the same linear buffer via
`append_audio()`, and `mark_speech_stopped()` at VAD end returns
`b"PREEPOST"` — sealed into `provider_handle.sealed_utterances` exactly
once. **The active utterance buffer and the rolling pre-buffer have one,
non-overlapping owner** (`self._pcm`, single instance): the ring never
holds anything once `mark_speech_started()` has fired, and the linear
buffer never exists before it — there is no window where both could
claim the same bytes.

## EXACT-ONCE REPLAY RESULT

**Unaffected, because unnecessary to change.**
`GeminiVoiceRuntime._replace_provider_after_bargein` (R0045) drains
`provider_handle.sealed_utterances` and replays each entry via
`user_turn_start()` / `send_user_audio(pcm)` / `user_turn_end()` — since
each entry is now the CORRECT, complete PCM (preroll included) at the
moment it is sealed, zero changes were needed to the replacement method
itself (`git diff` confirms it). Never POST-only, never PREE-twice, never
`PREEPOSTPOST`, never `PREE + PREEPOST`, never zero audio — proven by the
same worked-example test above, and by `TestAtomicProviderReplacement`'s
existing 5 tests remaining green unmodified.

## BUFFER RESET RESULT

Explicit semantics, each proven or inherited from `UtteranceBuffer`'s
own already-tested, unmodified contract:

- **Startup**: `self._pcm = UtteranceBuffer(...)` constructed fresh per
  bridge instance — empty ring, not capturing.
- **Normal VAD START**: `mark_speech_started()` — idempotent (a
  duplicate marker is a no-op, per `UtteranceBuffer`'s own guard);
  transfers ring → linear buffer, clears the ring.
- **Normal VAD END**: `mark_speech_stopped()` — idempotent (`b""` if not
  currently capturing); resets to idle, ring stays empty (nothing was
  ever added to it during capture).
- **Confirmed barge-in**: identical to normal VAD END — the seal happens
  at the SAME `mark_speech_stopped()` call site, just routed to
  `sealed_utterances` instead of `user_turn_end()` when quarantined.
- **Rejected candidate**: no different from normal end-of-turn from the
  buffer's own perspective — VAD START/END still bracket it normally
  (R0046's `has_turn_awaiting_assistant()` gate, unchanged, is what
  decides whether a CANONICAL turn opens for it; the PCM buffer itself
  doesn't distinguish "candidate" from "normal turn" at all, by design —
  it only tracks capturing state, matching the charter's own "preserve
  existing behavior exactly except for adding the missing prefix PCM").
- **Provider quarantine / replacement**: unaffected — `self._pcm` is
  bridge-local, entirely independent of which provider instance
  `provider_handle.current` points at.
- **Connection loss**: unrelated code path (`recover_from_mid_turn_loss`
  operates on the PROVIDER's own `take_pending_audio()`
  `UtteranceFramer`, a completely separate, provider-internal mechanism,
  never the bridge's `self._pcm`) — unaffected, re-verified green
  (`TestMidTurnRuntimeRecovery`).
- **Next utterance**: proven directly — no stale PCM from utterance N
  prefixes utterance N+1 when no idle audio is fed between them
  (`test_11_two_consecutive_turns_do_not_leak_preroll_between_them`).
- **Runtime stop**: no explicit teardown needed — the bridge instance
  (and its `self._pcm`) is discarded with the rest of the hardware
  pipeline on `GeminiVoiceRuntime.stop()`, unchanged.

## R0044/R0045 REGRESSION RESULT

**PASS, unmodified.** All 5 `TestAtomicProviderReplacement` tests remain
green, including `test_case2_old_delayed_audio_after_new_turn_closes_is_now_dropped`
(R0044 CASE 2) — this checkpoint touches zero lines in
`_replace_provider_after_bargein`, `_ProviderHandle`, or the
`replacement_requested`/quarantine flag logic. `TestVadBridgeQuarantine`'s
2 existing tests (R0045's own bridge-quarantine proof) remain green
unmodified — their frame sequences never fed idle audio before any VAD
start, so their expected byte sequences are unaffected by the new
pre-buffer's presence, confirmed by direct re-run, not merely assumed.

## R0046 HISTORY REGRESSION RESULT

**PASS, unmodified.** All of `TestProductionCanonicalTurnLifecycle`'s 5
tests remain green — including
`test_real_bridge_opens_normal_turns_and_never_abandons_one_awaiting_assistant`,
which exercises the REAL bridge directly and would have caught any
interference between the new preroll logic and
`has_turn_awaiting_assistant()`/`begin_cloud_turn()` gating. Zero changes
to `src/nexa/realtime/turn.py` or `src/nexa/realtime/router.py` this
checkpoint (`git diff --stat` confirms).

## LOCAL POST-FIX PARITY RESULT

**Deterministic (synthetic) A/B: PASS.** The R0048 diagnostic test that
originally proved the clipping hypothesis
(`TestRealProcessorChainClipsPreVadStartAudio::test_bridge_forwards_nothing_before_vad_start_but_raw_tap_keeps_it`)
has been replaced by its direct post-fix counterpart
(`TestRealProcessorChainPreVadStartAudioParity::test_bridge_now_forwards_the_preroll_before_vad_start_exactly_once`,
same file, same real-bridge/real-pipeline method): **BEFORE R0049**,
`production_forwarded` == `spoken` only (pre-onset PCM absent).
**AFTER R0049** (this checkpoint, currently in effect), the SAME test
setup now asserts and confirms `production_forwarded` ==
`pre_onset + spoken`, exactly once, in order — the local, no-Gemini,
synthetic-PCM proof the architecture is correct.

**Real hardware A/B: PENDING — not fabricated.** No real-hardware
re-capture with the FIXED bridge has been performed this checkpoint (no
Gemini, no hardware touched by this session, per the charter). The exact
command for the operator to re-run (identical CLI to the R0048 capture,
now exercising the fixed bridge automatically since it imports current
`nexa.realtime.gemini.runtime`) is given below. **LOCAL HARDWARE
POST-FIX ACCEPTANCE remains PENDING** until that real capture is done and
the operator listens to the new WAV pairs.

## FILES CHANGED

```
docs/reports/R0048_m2_6b_4i_real_audio_ingress_parity_audit_20260912.md          | +58 (operator follow-up section)
docs/reports/R0049_m2_6b_4j_restore_m2_6a_preroll_parity_20260912.md             | new (this report)
docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py    | +18/-10 (preroll_ms wired through)
src/nexa/realtime/gemini/runtime.py                                              | +~150/-~24
tests/test_m2_6b4i_audio_ingress_parity_probe.py                                 | rewrote 1 test class (post-fix A/B)
tests/test_realtime_gemini_runtime.py                                            | +~270 (new TestVadBridgePrerollParity, 1 zero-LID test, preroll_ms wired into 5 existing call sites)
```

* `src/nexa/realtime/gemini/runtime.py` — new import
  (`from ...stt.utterance_buffer import UtteranceBuffer`), new
  `AUTOSIZED_PREROLL_MARGIN_SECS` constant, `_VadToProviderBridge` gains
  a required `preroll_ms` constructor param and its `self._pcm`
  (`UtteranceBuffer`) replaces the old `self._utterance_buffer`
  bytearray + `self._turn_open` flag; `build_gemini_voice_runtime`
  computes `preroll_ms` from the actual `vad_analyzer` and passes it;
  module docstring gains an M2.6B.4J section.
* `src/nexa/stt/**` — **zero changes** (`UtteranceBuffer` reused
  verbatim).
* `src/nexa/realtime/router.py`, `src/nexa/realtime/turn.py` — **zero
  changes**.

## TEST RESULTS

```
$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py -q
69 passed, 1 warning

$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py::TestVadBridgePrerollParity tests/test_realtime_gemini_runtime.py::TestNoLocalLidInCloudRuntime -q
9 passed, 1 warning

$ .venv/bin/python -m pytest tests/test_m2_6b4i_audio_ingress_parity_probe.py -q
10 passed, 1 warning   (re-run 3x consecutively together with the runtime file, no flake)

$ .venv/bin/python -m pytest tests/test_realtime_router.py tests/test_realtime_turn.py tests/test_realtime_gemini_service.py tests/test_cloud_voice_app_entrypoint.py -q
74 passed, 2 warnings

$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 981 tests in 64.939s
OK (skipped=7)
```

`ruff check src/ tests/ apps/ docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py`
— All checks passed.
`.venv/bin/pip check` — No broken requirements found.
`git diff --check` — clean.

All 18 charter test requirements are covered: (1) PCM before VAD START
retained — `test_1`; (2)/(12) bounded idle buffer — `test_2`; (3)(4)(5)
start→preroll→live, in order, no duplication — `test_3_4_5`; (6) complete
preroll+live delivered — `test_6`; (7)(8) active buffer seeded, exact-once
barge-in replay — `test_7_8`; (9) quarantine still prevents stale
output — `TestVadBridgeQuarantine` (unmodified, re-verified green); (10)
R0044 CASE 2 closed — `TestAtomicProviderReplacement` (unmodified,
re-verified green); (11) no cross-turn leakage —
`test_11`; (13) non-lexical interruption green —
`TestProductionCanonicalTurnLifecycle` (re-verified); (14) R0046 history
green — same; (15) PL/EN green — `TestNativeOnlyLanguageDispatch`/
`TestSystemInstructionLanguagePolicy`/`TestCloudSameTurnLanguage`
(re-verified); (16) zero-LID green — `TestNoLocalLidInCloudRuntime`, +1
NEW test specifically covering the bridge's own new import; (17)
connection-loss green — `TestMidTurnRuntimeRecovery` (re-verified); (18)
local voice untouched — see below.

## LOCAL VOICE FREEZE CHECK

```
$ git diff --stat -- src/nexa/voice src/nexa/voice_tts
(empty)
$ git diff --stat -- src/nexa/stt
(empty)
```

Zero diff in both — local voice AND the shared STT/audio-buffer module it
depends on (`UtteranceBuffer`) are completely untouched; only reused,
verbatim, by an import from the cloud module.

## COMMIT HASHES

Recorded in the follow-up hash-record commit.

## GIT STATUS

At time of writing (before this checkpoint's commit):

```
 M docs/reports/R0048_m2_6b_4i_real_audio_ingress_parity_audit_20260912.md
 M docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py
 M src/nexa/realtime/gemini/runtime.py
 M tests/test_m2_6b4i_audio_ingress_parity_probe.py
 M tests/test_realtime_gemini_runtime.py
?? docs/reports/R0049_m2_6b_4j_restore_m2_6a_preroll_parity_20260912.md
```

Not pushed.

## EXACT LOCAL POST-FIX CAPTURE COMMAND

**STOP — operator action required before hardware acceptance can be
marked PASS.** Re-run the SAME probe, unchanged CLI (it imports the
current, now-fixed `nexa.realtime.gemini.runtime` automatically — no new
flag, no code change needed to exercise the fix):

```
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py
```

Say the same phrases as the R0048 capture (repeat "Czarna dziura" first).
Listen to the NEW `production_forwarded.wav` files: they should now
audibly contain the full onset ("Czarna dziura," not "dziura"/"arna
dziura"), matching `raw_with_context.wav`. Report back whether this
holds for all takes — that confirmation is what moves LOCAL HARDWARE
POST-FIX ACCEPTANCE from PENDING to PASS.

## EXACT NEXT GEMINI ATTEMPT COMMAND

Only after the above local post-fix capture confirms parity (per the
charter, no Gemini call is made until then by this session; the operator
may of course proceed independently):

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Unchanged from R0047's corrected command — no new flag was added this
checkpoint either.
