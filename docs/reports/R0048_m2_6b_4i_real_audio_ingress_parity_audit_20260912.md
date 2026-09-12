# R0048 — M2.6B.4I: Accepted M2.6A vs Production Real Audio Ingress Parity Audit

**Date:** 2026-09-12
**Milestone:** M2.6B.4I (diagnostic checkpoint, following the real
Attempt #3 "Czorna Jura" symptom)
**Status:** Hypothesis CONFIRMED — mechanically, from installed source,
and empirically, via the REAL production `_VadToProviderBridge` running
in a real Pipecat pipeline. **DIAGNOSTIC ONLY — no production fix applied
this checkpoint** (per the charter: R0048 = evidence, R0049 = fix). No
Gemini call, no hardware, not pushed. **Hardware acceptance remains FAIL.
`M2.6B` remains IN PROGRESS.**

## OPERATOR REAL HARDWARE FOLLOW-UP — 2026-09-12

After this checkpoint's own diagnostic tool was built and validated
offline, the operator ran it for real, on the real reSpeaker, with no
Gemini involved:

```
.venv/bin/python \
docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py
```

**10 utterances captured successfully** — the terminal showed all 10 VAD
turns correctly. Summary JSON:
`docs/research/m2_6_cloud_realtime_voice/ingress_captures/
ingress_capture_20260912T184419Z.json` (git-ignored, per this
checkpoint's own `.gitignore` entry — not committed; read and summarized
here). Every one of the 10 entries shows the SAME consistent pattern:
`ms_raw_audio_before_first_forwarded ≈ 500 ms` (range 500.2–500.5 ms
across the 10 takes), `onset_gap_vad_start_to_first_forwarded_s ≈ 0.019 s`
(forwarding begins almost immediately once local VAD confirms — the
~19 ms is real audio-chunk granularity, not diagnostic overhead), and
`postroll_never_forwarded_s ≈ 0.52 s` (the trailing raw-capture context
window, present by the diagnostic's own design, not evidence of any
output-side loss).

**IMPORTANT, and stated exactly as instructed:** the ~500 ms figure is
**not** to be read as "500 ms of speech was clipped." It is the
diagnostic's own deliberately-generous `PRE_CONTEXT_SECS = 0.5` raw
capture window (see REAL HARDWARE CAPTURE METHOD, below) — chosen so the
raw WAV would comfortably contain whatever was clipped, with margin. The
source-backed speech-start delay remains `VAD_START_SECS = 0.2 s`
(Pipecat's own default). The evidence that matters is not the ~500 ms
number — it is what the operator actually **heard** inside that window.

**The operator listened to RAW vs PRODUCTION_FORWARDED WAV pairs
directly.** Real result: RAW files contain the complete spoken phrase in
every case. PRODUCTION_FORWARDED files are audibly clipped at the start.
For "Czarna dziura," the operator specifically heard forwarded captures
beginning at **"dziura"** or **"arna dziura"** — never the complete
"Czarna dziura" that RAW always contained. For "Czarna dziura powstaje,"
a forwarded capture could begin at **"dziura powstaje"** while RAW
contained the complete phrase.

This is now confirmed by **real reSpeaker + real operator speech + real
production VAD/bridge path + direct human listening of RAW vs FORWARDED
WAVs** — a fourth, independent line of evidence on top of the three
already established in this report (installed-source read, synthetic
real-bridge test, and the mechanical VAD_START_SECS derivation). This
materially raises confidence that the Attempt #3 "Czorna Jura"
misrecognition was caused, at least in significant part, by onset
clipping — moving ROOT CAUSE CONFIDENCE for the clipping mechanism itself
from "high, mechanically proven" to "high, mechanically AND empirically
proven on real hardware with real speech," while the mishearing's exact
attribution (below) remains honestly at MEDIUM (a single real-world
anecdote; no controlled Gemini A/B was run). No Gemini was used for this
follow-up capture. **See `docs/reports/R0049_...md` for the production
fix this evidence justified and implemented.**

## ATTEMPT #3 OBSERVED SYMPTOM

The operator's first utterance of the session, intended as "czarna
dziura" (black hole), was misheard by Gemini as "Czorna Jura" (prompting
a confused clarifying reply about a possible place name), followed by
"Dalej nie jestem pewien..." (still not sure). Later in the SAME session,
Gemini correctly understood "Ah, a black hole!" and went on to answer
normal PL/EN questions sensibly. The R0043/R0045 permanent post-
interruption audio-loss failure mode did **not** recur — assistant audio
continued normally after interruption acknowledgements. This checkpoint
does not reopen that already-fixed issue; it investigates only whether
the FIRST utterance's onset was clipped before ever reaching Gemini.

## ACCEPTED M2.6A INGRESS PATH

Read directly from
`docs/research/m2_6_cloud_realtime_voice/m2_6a_gemini_live_probe.py`
(the exact, operator-accepted spike): mic audio flows through ONE
Pipecat pipeline — `[transport.input(), up, user_agg, llm, down,
aec_feeder, transport.output(), asst_agg]` — where `user_agg` is
Pipecat's own `LLMContextAggregatorPair`'s user aggregator, constructed
with `LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(...,
params=VADParams(stop_secs=0.5)))` — i.e. the **same VAD analyzer that
decides local turn boundaries lives inside the SAME pipeline, immediately
upstream of `llm` (`GeminiLiveLLMService`)**. `llm` itself is constructed
with `user_audio_preroll_secs=None` — the spike's own comment: "auto-size
from Silero start_secs". `SileroVADAnalyzer.params` is never given an
explicit `start_secs` override, so Pipecat's own default applies (see
below).

## CURRENT PRODUCTION INGRESS PATH

Read directly from `src/nexa/realtime/gemini/runtime.py` (`build_gemini_voice_runtime`)
and `src/nexa/realtime/gemini/service.py` (`GeminiLiveProvider`): TWO
independent Pipecat pipelines. The HARDWARE pipeline —
`[transport.input(), vad_processor, bargein, bridge, aec_feeder,
transport.output()]` — owns local Silero VAD (`SileroVADAnalyzer(...,
params=VADParams(stop_secs=0.5))`, same `stop_secs`, same missing
`start_secs` override). `_VadToProviderBridge` (`bridge`) forwards audio
to the provider via **explicit method calls**
(`provider.user_turn_start()`/`send_user_audio()`/`user_turn_end()`),
gated by its own `self._turn_open` flag — set `True` only by
`VADUserStartedSpeakingFrame`, `False` by `VADUserStoppedSpeakingFrame`.
The PROVIDER's own headless pipeline (`GeminiLiveProvider.start()`) is
entirely SEPARATE: `LLMContextAggregatorPair(context, user_params=
LLMUserAggregatorParams(), realtime_service_mode=True)` — **no
`vad_analyzer` argument at all** — because the real VAD lives in the
other, hardware pipeline. `GeminiLiveLLMService` is constructed with no
`user_audio_preroll_secs` override either (confirmed:
`llm_kwargs` in `service.py` never sets it).

## SOURCE DIFFERENCES

Read directly from the installed `pipecat==1.8.1` package (never inferred,
every fact below is a direct source quote/line reference):

1. **`pipecat/processors/audio/vad_processor.py` `VADProcessor.process_frame`**
   forwards every frame downstream (`await self.push_frame(frame,
   direction)`) **unconditionally, BEFORE** running VAD detection on it —
   own comment: *"Audio flows through immediately while VAD detection
   happens after."* Confirms `VADProcessor` itself drops nothing — any
   clipping must happen strictly downstream of it.
2. **`pipecat/audio/vad/vad_analyzer.py`**: `VAD_START_SECS = 0.2`,
   `VAD_STOP_SECS = 0.2` (module-level defaults). Neither the M2.6A spike
   nor current production overrides `start_secs` — both use Pipecat's own
   0.2 s default. `VADUserStartedSpeakingFrame` therefore fires only
   after **0.2 s of already-elapsed, confirmed voice activity** — that
   0.2 s of real speech audio necessarily precedes the marker frame in
   both architectures identically.
3. **`pipecat/services/google/gemini_live/llm.py`**: `GeminiLiveLLMService`
   has a REAL, built-in pre-roll mechanism —
   `_send_user_audio()` (lines ~1465-1503): while `self._vad_disabled and
   not self._user_is_speaking`, every incoming `InputAudioRawFrame` is
   retained in a rolling `_user_audio_preroll_buffer` (sized to
   `_user_audio_preroll_secs`) **instead of being sent**;
   `_handle_user_started_speaking()` (line ~806) sends `activity_start`
   THEN immediately calls `_flush_user_audio_preroll()` — own comment:
   *"The speech onset arrived (and was buffered) before this
   activity_start. Send it inside the window, so it's not clipped."*
   `_user_audio_preroll_secs` auto-sizes from a `SpeechControlParamsFrame`
   (broadcast by a `VADController` at pipeline start) to `start_secs +
   AUTOSIZED_USER_AUDIO_PREROLL_MARGIN_SECS` (= 0.2 + 0.1 = 0.3 s);
   **falling back to `DEFAULT_USER_AUDIO_PREROLL_SECS = 0.5 s` "when no
   VAD is present."**
4. **The decisive structural difference**: in M2.6A, the SAME pipeline
   that owns `llm` also owns the VAD analyzer, so (a) `InputAudioRawFrame`s
   flow continuously into `llm._send_user_audio()` from the very start of
   the session (proven by the spike's own diagnostic wrapper, which counts
   `audio_bytes_to_gemini` even during "not ready" windows) and (b) a
   `SpeechControlParamsFrame` IS broadcast, auto-sizing the preroll to
   0.3 s. **`GeminiLiveLLMService`'s own preroll buffer is therefore
   populated and correctly recovers the speech onset.** In current
   production, `_VadToProviderBridge.send_user_audio()` — and therefore
   `GeminiLiveProvider.send_user_audio()` and therefore
   `GeminiLiveLLMService._send_user_audio()` — is **never called at all**
   for audio arriving before `_turn_open` flips `True`. The provider's own
   preroll buffer exists in the running code (defaulting to 0.5 s, no VAD
   present so never auto-sized down) but is **permanently starved**: it
   never receives the pre-onset audio to buffer, because the external
   bridge already discarded it one layer up, before `send_user_audio()`
   was ever invoked.

## REAL HARDWARE CAPTURE METHOD

New diagnostic tool:
`docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py`.
No Gemini, no cloud API, no credential, no assistant playback. Reuses the
REAL production classes verbatim — `nexa.realtime.gemini.runtime.
_make_vad_bridge_class`/`_ProviderHandle`/`RuntimeMetrics`/
`_ResponseLifecycle`, `nexa.voice.bargein.BargeInController`, and the
EXACT `SileroVADAnalyzer`/`VADParams(stop_secs=0.5)`/`VADProcessor`
construction `build_gemini_voice_runtime` uses — with a real reSpeaker
`LocalAudioTransport` input (`audio_out_enabled=False`, no output stage
at all) and a `_StubProvider` in place of `GeminiLiveProvider` that
records every `user_turn_start()`/`send_user_audio()`/`user_turn_end()`
call instead of talking to Gemini. Two diagnostic-only taps (never
mutating or dropping a frame) bracket the VAD stage: `_RawCaptureTap`
(upstream of `VADProcessor`, feeds a rolling ~3 s ring buffer) and
`_VadMarkerTap` (downstream, marks the exact local-clock time each VAD
boundary frame arrives). Per utterance, `raw_with_context.wav` (VAD start
− 0.5 s ... VAD stop + 0.5 s, from the ring buffer) and
`production_forwarded.wav` (exactly the bytes the `_StubProvider`
received) are written, plus one running summary JSON with per-utterance
derived timing. `--dry` validates construction with no audio device.
`ingress_captures/` output is git-ignored (real operator voice, never
committed).

## TEST PHRASES

The charter's exact 10-phrase set is printed to the operator at session
start (repeated "Czarna dziura" first, as the primary diagnostic,
matching the real Attempt #3 symptom):

```
1. Czarna dziura
2. Czarna dziura
3. Czarna dziura powstaje...
4. Czarna dziura powstaje...
5. Black hole
6. Black hole
7. What is a black hole?
8. What is a black hole?
9. Gwiazdy składają się z wodoru i helu
10. Gwiazdy składają się z wodoru i helu
```

**No real hardware run was performed this checkpoint** (explicitly out of
scope — "No Gemini. No hardware." applies to this session). The tool is
built, `--dry`-validated, and deterministically tested; the operator runs
the real 10-take capture separately (exact command below).

## RAW VS FORWARDED RESULTS

**MEASURED, this session, via the REAL, unmirrored `_VadToProviderBridge`**
(`tests/test_m2_6b4i_audio_ingress_parity_probe.py::TestRealProcessorChainClipsPreVadStartAudio`,
3 consecutive clean runs, no flake observed): synthetic "pre-onset" PCM
queued before a hand-injected `VADUserStartedSpeakingFrame`, "spoken" PCM
after it, then `VADUserStoppedSpeakingFrame` — driven through the REAL
processor chain (`build_processor_chain`: real `VADProcessor` + real
`BargeInController` + the REAL, imported-not-reimplemented
`_VadToProviderBridge`) inside a real `Pipeline`/`PipelineWorker`/
`WorkerRunner`. Result: the raw capture (`raw_with_context.wav`) contains
BOTH the pre-onset and spoken audio; the production-forwarded capture
(`production_forwarded.wav`) contains **only** the spoken audio —
`forwarded_pcm == spoken` exactly, byte for byte; the pre-onset PCM is
**never present** in it. This is not a simulation of the bridge's logic —
it is the actual `_VadToProviderBridge` class imported from
`src/nexa/realtime/gemini/runtime.py`.

## BEGINNING-OF-UTTERANCE RESULT

**CLIPPED, by construction, deterministically.** `_VadToProviderBridge`
only calls `provider.send_user_audio()` while `self._turn_open` is
`True`, which becomes `True` only on `VADUserStartedSpeakingFrame` —
itself fired only after `VAD_START_SECS = 0.2 s` of already-elapsed
confirmed voice activity (Pipecat's own, unmodified default, identical in
both architectures). Every byte of audio during that 0.2 s window
physically arrives at the bridge (per `VADProcessor`'s own "forward
first, detect after" source-confirmed behavior) but is unconditionally
discarded — never appended to `_utterance_buffer`, never forwarded — since
`_turn_open` is still `False`. `GeminiLiveLLMService`'s own preroll buffer
(present, code-complete, defaulting to 0.5 s) never receives this audio to
buffer, because `send_user_audio()` is never called for it in the first
place.

## END-OF-UTTERANCE RESULT

**NOT clipped, by the same mechanical reasoning.** `VAD_STOP_SECS = 0.5 s`
(production's explicit `stop_secs=0.5` override) means
`VADUserStoppedSpeakingFrame` fires 0.5 s AFTER the user's last spoken
audio — but `_turn_open` stays `True` for that entire trailing window (it
only flips `False` on the stop marker itself), so every byte of audio
during it — the tail of real speech plus trailing silence — continues
being forwarded normally. The asymmetry is real and mechanical: onset
detection delay causes loss (nothing is forwarded during it); offset
detection delay does not (forwarding continues through it). No output
audio-loss recurrence was found or looked for here (out of scope,
already covered by R0043/R0045).

## M2.6A PRE-ROLL/PARITY RESULT

**SOURCE-DERIVED M2.6A BEHAVIOR (no live M2.6A run was made this
checkpoint or ever re-measured — this is read directly from the
installed Pipecat source both architectures share, not a claimed live
M2.6A measurement):** M2.6A's own pipeline topology places the SAME
Silero VAD analyzer directly upstream of `GeminiLiveLLMService`, so (1)
`InputAudioRawFrame`s reach `_send_user_audio()` continuously from
session start (M2.6A's own diagnostic instrumentation counts bytes
delivered even in "not ready" windows) and (2) a `SpeechControlParamsFrame`
IS broadcast (a `VADController` exists in that same pipeline), auto-sizing
`GeminiLiveLLMService`'s preroll to `start_secs + 0.1 s = 0.3 s` — which
the service's own `_handle_user_started_speaking()` flushes immediately
after `activity_start`. **M2.6A's architecture recovers the onset;
current production's architecture cannot, because the bridge that owns
turn-boundary decisions is a SEPARATE processor in a SEPARATE pipeline
from the LLM service, and never calls `send_user_audio()` for pre-onset
audio at all** — starving an otherwise-present, otherwise-correct preroll
mechanism inside `GeminiLiveLLMService` of any audio to buffer.

## HYPOTHESIS CONFIRMED OR REJECTED

**CONFIRMED**, on two independent lines of evidence: (1) exhaustive
source reading of the exact installed `pipecat==1.8.1` package both
architectures depend on, quoting the library's own comments explaining
the mechanism; (2) a deterministic test driving the REAL, unmodified
`_VadToProviderBridge` through a real Pipecat pipeline, proving the
production-forwarded capture is missing exactly the pre-VAD-start window
the raw capture retains. Current production begins provider forwarding
only after local VAD confirms speech start (0.2 s into the utterance, by
Pipecat's own unmodified default), so the first ~0.2 s of an utterance's
phonetic content is structurally absent from what Gemini ever receives —
consistent with, though this checkpoint does not claim to PROVE
word-for-word, the "Czorna Jura" mishearing of a word beginning with the
voiceless affricate "cz" (a sound whose identifying acoustic energy is
concentrated in its first ~50-150 ms).

## ROOT CAUSE CONFIDENCE

**HIGH** for the clipping mechanism itself (mechanically proven, not
inferred — both from installed source and from a real, unmirrored
production code path). **MEDIUM** for this being the specific,
sufficient explanation of the ONE observed "Czorna Jura" mishearing (a
single real-world anecdote; Gemini's own transcription/ASR has other
failure modes independent of NeXa's audio delivery, and no controlled
A/B with real speech was performed this checkpoint, by design — that is
exactly what the operator's real hardware run with this tool, plus
listening to the resulting WAV pairs, is for).

## MINIMUM PARITY FIX OPTIONS

Evaluated, **not chosen, not implemented** (per the charter):

- **A. Bounded rolling PCM pre-buffer before VAD START, added to
  `_VadToProviderBridge` itself** — mirrors `GeminiLiveLLMService`'s own
  preroll concept but implemented in NeXa's own bridge (which already
  maintains an unrelated `_utterance_buffer` for R0045's exact-once
  replay) so it works regardless of how audio reaches the provider.
  Smallest conceptual change; needs care not to interact with R0045's
  own buffer/replay semantics or R0046's canonical-turn-opening timing.
- **B. Preserve continuous ingress into the provider's own pipeline, use
  local VAD only for semantic turn markers** — closer to M2.6A's own
  topology (let `GeminiLiveLLMService`'s existing, currently-starved
  preroll mechanism do the job it already does); would mean streaming
  `InputAudioRawFrame`s to the provider continuously and separately
  signaling `user_turn_start()`/`user_turn_end()` — a larger change to
  the provider-boundary contract (`RealtimeVoiceProvider.send_user_audio`'s
  own meaning) and to how `_ProviderHandle`/R0045's quarantine gating
  interact with continuously-flowing audio.
- **C. Another source-backed, M2.6A-equivalent mechanism** — e.g. driving
  `GeminiLiveProvider`'s own pipeline with a real `VADController` so
  `GeminiLiveLLMService`'s EXISTING preroll auto-sizes and self-flushes,
  without NeXa maintaining a second buffer at all — closest in spirit to
  "stop fighting Pipecat's own mechanism," but requires the provider's
  headless pipeline to observe the SAME VAD signal the hardware pipeline
  already computes, without duplicating Silero inference.

No option is chosen here. R0049 (per the charter's own preferred
structure) is the next checkpoint to pick one, with real hardware
evidence from this tool informing the choice.

## FILES CHANGED

```
.gitignore                                                                              |  4 +
docs/reports/R0048_m2_6b_4i_real_audio_ingress_parity_audit_20260912.md                 | new (this report)
docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py           | new
tests/test_m2_6b4i_audio_ingress_parity_probe.py                                         | new, 10 tests
```

No `src/nexa/**` change of any kind — diagnostic only, as the charter
requires for this checkpoint.

## TEST RESULTS

```
$ .venv/bin/python -m pytest tests/test_m2_6b4i_audio_ingress_parity_probe.py -q
10 passed in ~1.7s   (re-run 3x consecutively, no flake)

$ .venv/bin/python -m pytest tests/test_realtime_gemini_runtime.py::TestAtomicProviderReplacement tests/test_realtime_gemini_runtime.py::TestProductionCanonicalTurnLifecycle tests/test_cloud_voice_app_entrypoint.py -q
15 passed in ~3.9s

$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 974 tests in 63.087s
OK (skipped=7)
```

`ruff check src/ tests/ apps/ docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py`
— All checks passed.
`.venv/bin/pip check` — No broken requirements found.
`git diff --check` — clean.

## LOCAL VOICE FREEZE CHECK

```
$ git diff --stat -- src/nexa/voice src/nexa/voice_tts
(empty)
```

Zero diff — local voice completely untouched.

## GIT STATUS

Commit `7c4772a` made this checkpoint's changes. Working tree clean as of
writing (hash-record follow-up commit pending). Not pushed.

## EXACT LOCAL CAPTURE COMMAND

For the operator's real 10-take hardware capture (no Gemini, no
credential, no network — safe to run at any time):

```
.venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6b4i_audio_ingress_parity_probe.py
```

(`--dry` first, to sanity-check construction with no audio device, is
optional but recommended.) Speak each printed phrase, pausing briefly
between takes; Ctrl+C ends the session and writes
`ingress_captures/<NNN>_raw_with_context.wav` /
`<NNN>_production_forwarded.wav` per utterance plus one
`ingress_capture_<timestamp>.json` summary. Listen to both WAV files per
utterance and judge whether the clipped ~0.2-0.5 s pre-roll window
audibly contains the missing initial consonant/syllable — that judgment,
plus the JSON's derived timing, is the evidence R0049 needs to pick a fix.
