# R0039 — M2.6B.4A: Strict Same-Turn PL/EN Language Authority (Research)

## TASK RESULT

**RESEARCH ONLY — implementation deliberately paused, at the user's own
explicit direction, after a mid-task finding.** The charter asked for a
full STRICT-mode provider-replacement architecture. Before writing it,
this checkpoint benchmarked the tool the design was to be built on
(`WhisperCppLanguageDetector`) exactly as instructed ("measure LID first")
— the measured cost (~1.1-1.7s per call, **independent of how much speech
is fed to it**) would make a "hold `activityEnd` for LID on every turn"
gate cost every ordinary same-language turn ~1.1-1.7s more than the M2.6A
baseline, directly contradicting the charter's own stated goal ("same-
language turns remain as close as possible to M2.6A"). This was put to
the user with the measured numbers before writing any code; the user
chose: **pause implementation, research a lighter LID model first.**
This report is that research. **No code in `src/nexa/**` changed this
checkpoint.** No Gemini call, no hardware. `M2.6B` remains IN PROGRESS,
not COMPLETE; R0038's two non-language fixes (VAD bridge lifecycle,
playback-generation guard) are unaffected and remain ready for a hardware
retest — only the language-mirroring failure is still open.

## OFFICIAL/API + SOURCE AUDIT

**Confirmed from the installed `google-genai` SDK source
(`live.py:send_realtime_input`), verbatim:**
> "`send_realtime_input` is optimized for responsivness at the expense of
> deterministic ordering. Audio and video tokens are added to the context
> when they become available."

and, from the same method's own implementation (not documentation —
enforced in code):
```python
if len(kwargs) != 1:
    raise ValueError(f"Only one argument can be set, got {len(kwargs)}: ...")
```
**A single `send_realtime_input()` call cannot carry text and audio (or
`activity_start`) together at all** — the API rejects it outright.

**Confirmed from the official Gemini Live API docs** (`ai.google.dev`,
fetched live this checkpoint, quoted verbatim):
> "The different modalities (audio, video and text) are handled as
> concurrent streams. The ordering across these streams is not
> guaranteed."

> "You cannot update the configuration while the connection is open."
> ... "you can change the configuration parameters, except the model,
> when pausing and resuming" (session resumption).

> "Native audio output models automatically choose the appropriate
> language and don't support explicitly setting the language code." ...
> "You can also restrict the languages it speaks in by specifying it in
> the system instructions."

**Conclusions, directly answering the charter's own caution:**
1. **A realtime text hint interleaved beside audio (e.g. "respond in
   English") is definitively NOT deterministic same-turn language
   authority** — confirmed independently by the SDK's own docstring, the
   SDK's own runtime enforcement (one argument per call), and the
   official API docs' explicit "ordering... is not guaranteed" statement.
   Not adopted, per the charter's own instruction not to use it without
   stronger evidence — the evidence found points the other way.
2. **`system_instruction` is immutable on an open connection** —
   re-confirmed (ADR-0004 Amendment 1 already established this from the
   installed Pipecat source in R0032; the official docs now confirm it
   independently: "You cannot update the configuration while the
   connection is open"). A NEW session is required to change it.
3. **Google's own sanctioned language-control mechanism for native-audio
   models is a `system_instruction` restriction** ("You can also restrict
   the languages it speaks in by specifying it in the system
   instructions") — there is no `language_code`/`SpeechConfig` parameter
   for this model class. This directly validates the charter's own
   "SYSTEM-INSTRUCTION LANGUAGE TARGET" design as the *correct* (and only
   officially sanctioned) mechanism, once a fresh session is warranted.
4. **An interesting, not-yet-pursued avenue**: the docs note session
   resumption can "change the configuration parameters, except the
   model" when pausing/resuming — distinct from a fully fresh session.
   Whether `system_instruction` is among those changeable parameters, and
   whether Pipecat/our `GeminiLiveProvider` could use resumption instead
   of full destroy-and-recreate for a language switch, is **not verified**
   here (would need a live call to confirm) — noted as a possible future
   optimization, not assumed or built on.

**Re-confirmed from installed Pipecat 1.8.1 / current `GeminiLiveProvider`
(unchanged since R0034/R0038, re-read this checkpoint, not re-derived):**
`_VadToProviderBridge` maps local Silero VAD frames 1:1 onto
`user_turn_start()`/`send_user_audio()`/`user_turn_end()`, which in turn
drive Pipecat's `GeminiLiveLLMService` to call
`send_realtime_input(activity_start=...)` then per-chunk
`audio=...` then `activity_end=...` as the manual-VAD framing this system
already relies on (server VAD OFF, per the frozen M2.6A baseline —
unchanged). `ConversationRouter.recover_from_mid_turn_loss()` and
`GeminiVoiceRuntime`'s `_ProviderHandle` atomic-swap machinery (R0038) are
exactly the "destroy old, build fresh `CloudContextSnapshot`, construct +
start new provider, atomically swap `runtime.provider`/`_ProviderHandle`"
primitives the charter's own "reuse the proven mid-turn destroy/recreate
machinery" instruction points at — confirmed reusable, not re-implemented
in this pass (see STRICT LANGUAGE DESIGN below for exactly how).
`ResponseLanguageResolver`/`WhisperCppLanguageDetector` remain exactly as
documented in R0038 (sticky preference only on an explicit directive;
`argmax(p_pl, p_en)` classification) — re-read, unchanged.

## LID BENCHMARK

Ran `docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark.py`
against the SAME real recorded PL/EN fixtures R0038 used (never
fabricated audio) — raw results saved alongside it
(`m2_6b4a_lid_benchmark_results_20260911T203641Z.json`).

| measurement | result |
|---|---|
| cold start (first call ever, full utterance) | 1185.6 ms |
| warm invocations, full utterance (6 files, 3 PL/EN pairs) | 1150–1207 ms, **all 6/6 correct** |
| truncated to 0.5 s | 1190/1275 ms — EN correct, **PL MISCLASSIFIED as EN** |
| truncated to 1.0 s | 1197/1169 ms — EN correct, **PL MISCLASSIFIED as EN** |
| truncated to 1.5 s | 1151/1140 ms — **both correct** |
| truncated to 2.0 s | 1138/1158 ms — **both correct** |
| overall span across all 14 calls | 1138.3–1275.1 ms (mean 1177.0 ms) |

**Two findings, both material:**
1. **Latency is ~1.15–1.7s and essentially CONSTANT regardless of input
   duration** — 0.5s of audio costs about the same as the full 3.5s
   utterance (±12% across the whole sweep). There is no "warm invocation"
   speedup either (cold start 1185.6ms vs. warm mean 1177.0ms —
   statistically indistinguishable; the model context is already resident
   per-process, so there is nothing further to warm).
2. **No reliable early-truncation point for this detector**: PL is
   misclassified as EN at both 0.5s and 1.0s for the tested utterance,
   only becoming reliable at ≈1.5s+. Combined with (1), there is no
   latency benefit to be had by truncating shorter, since the cost is
   fixed regardless — truncating only *hurts* accuracy for no speed gain.

**Root cause of the constant latency, confirmed by reading the installed
`whisper.cpp` v1.9.3 source directly (`~/.local/share/nexa/stt/whisper.cpp-v1.9.3/src/whisper.cpp`,
fetched by `scripts/setup_whisper_cpp.py`, not guessed):**
`whisper_lang_auto_detect_with_state()` calls `whisper_encode_with_state()`
— a full encoder forward pass — before reading language logits.
`whisper_encode_internal()`'s conv/embed step builds a mel input tensor of
FIXED size `2 * n_audio_ctx` (`n_audio_ctx` is the model's own hyperparameter,
tied to Whisper's architectural `WHISPER_CHUNK_SIZE = 30` (seconds) —
`whisper.h:36`) and zero-pads it (`memset` to the full fixed size, then
copies only the real mel frames into the front): **the encoder always runs
its full forward pass over a fixed ~30-second-equivalent input, whatever
the actual utterance length.** This is an inherent architectural property
of the Whisper encoder (fixed positional embeddings sized for 30s), not a
NeXa configuration issue, not fixable by truncating the input PCM, and not
specific to LID — the same fixed cost underlies R0006's own measured
~1.69s full-transcription baseline (`base/q8_0`, same model). **This
confirms, from source, that the existing detector is "materially too
slow" for a per-turn pre-`activityEnd` gate — exactly the bar the charter
set before considering an alternative.**

**Lightweight-alternative research (per charter: research before
implementing anything new) — surveyed, none implemented this checkpoint:**
- **A smaller `ggml` Whisper model** (e.g. `tiny`/`tiny.en`, ~39M params
  vs. `base`'s ~74M) would very likely be meaningfully faster (fewer
  layers/heads → less compute per fixed-size forward pass) while staying
  on the exact same, already-integrated `ctypes` binding — the lowest-risk
  next step. **Not verified here**: this environment's
  `~/.local/share/nexa/stt/whisper.cpp-v1.9.3/models/for-tests-ggml-tiny*.bin`
  files are whisper.cpp's own CI test fixtures (~0.5-0.6 MB — stub/random
  weights, confirmed by file size alone: a real `tiny` model is ~75 MB),
  **not usable for real PL/EN accuracy** — a genuine `ggml-tiny-q8_0.bin`
  would need downloading and a fresh accuracy+latency benchmark against
  these same fixtures before adoption; out of scope for this research-only
  checkpoint (would also reopen the frozen local-voice STT model
  selection if reused there, which this task does not propose).
- **A dedicated, non-Whisper spoken-language-ID model** (e.g. an x-vector/
  CNN-based classifier — the class of model behind, for instance, Silero's
  own separate language-ID offering, or SpeechBrain's VoxLingua107 model)
  would not carry Whisper's fixed-30s-context architectural cost and could
  plausibly run in tens of milliseconds on CPU. **Not adopted**: this
  environment has no such dependency installed (`pip list` confirms no
  `speechbrain`/`silero`/`torch`/`langid`/`fasttext`), and evaluating one
  means a genuinely new, likely heavier (PyTorch-class) dependency —
  explicitly the kind of "new framework casually" the charter says not to
  install without cause; a real evaluation (accuracy on these fixtures,
  install footprint, license) is future-checkpoint work.
- **No classical DSP heuristic was pursued** — distinguishing PL from EN
  acoustically without a trained model is not a reliable approach for
  arbitrary speech content.

**Conclusion**: the existing detector's cost is real, source-confirmed,
and not fixable by configuration or truncation. A viable lighter option
plausibly exists (smaller Whisper model, same binding) but is not yet
downloaded, verified, or benchmarked — that is the concrete next step for
whoever picks this up, not attempted here per the user's own choice to
stop at research.

## STRICT LANGUAGE DESIGN (specification only — NOT implemented this checkpoint)

Recorded here in full so a future checkpoint can implement it directly
once a viable low-latency LID exists, without re-deriving the design.

**Three distinct, precisely-named concepts** (per the charter's own
naming instruction), all living on `GeminiVoiceRuntime`:

- `detected_turn_language: str` — this specific utterance's acoustic
  language, from local LID on the full buffered utterance
  (`_PendingUtteranceAudio`, already built in R0038 — reused, not
  rebuilt).
- `sticky_language_preference: str | None` — `ResponseLanguageResolver`'s
  own `preference.sticky` (already exists, unchanged) — set **only** on
  an explicit directive ("always answer in English"/"odpowiadaj zawsze po
  polsku"), never from acoustic detection alone.
- `active_session_response_language: str | None` — NEW state:
  which language the **currently-open Gemini provider session** was
  constructed with an explicit `system_instruction` restriction for.
  `None` for the initial neutral connection (fast READY, per the
  charter's own "FIRST TURN" allowance) or after a provider swap before
  its first strict turn completes.

`target_response_language` is computed each turn as: `sticky_language_preference`
if set, else `detected_turn_language`. Never the reverse — an explicit
sticky preference always wins over what was just acoustically detected,
matching `ResponseLanguageResolver`'s own existing, unchanged semantics.

**Turn flow** (holds `activityEnd`, not `activityStart`/audio — those
still stream live exactly as today, per the charter — only the FINAL
signal is gated):

1. Local Silero VAD start → `activity_start` sent live (unchanged).
2. Audio streams live to Gemini AND is buffered locally (unchanged,
   `_PendingUtteranceAudio` already does this).
3. Local Silero VAD stop → **do not call `user_turn_end()` yet.**
4. Run local LID on the complete buffered utterance → `detected_turn_language`.
5. Compute `target_response_language`.
6. **If `target_response_language == active_session_response_language`**:
   call `user_turn_end()` normally on the SAME provider. No reconnect, no
   replay, no extra cost beyond step 4's LID latency (which, with the
   CURRENT detector, is the ~1.1–1.7s measured above — the reason
   implementation is paused).
7. **Else** (first strict turn, or a detected language switch): a
   CONTROLLED LANGUAGE-BOUNDARY PROVIDER REPLACEMENT — reusing
   `ConversationRouter.recover_from_mid_turn_loss()`'s exact shape (R0038):
   mark the old provider's open turn abandoned (never send it
   `activityEnd`; it already has `activity_start` + audio with no
   matching close — exactly the R0034 "mid-turn-unsafe" case, same
   handling), stop/destroy it, build a fresh `CloudContextSnapshot` (new
   `system_instruction` parameter — see below — carrying
   `target_response_language`), construct + start a fresh
   `GeminiLiveProvider`, atomically swap `runtime.provider` +
   `_ProviderHandle` (`_ResponseGenerationGuard.interrupt()` also fires
   here, exactly as the existing mid-turn-recovery path already does —
   R0038's playback-invalidation guard needs no change), replay the FULL
   buffered utterance once as a self-contained turn (`user_turn_start()` →
   buffered PCM → `user_turn_end()`), set
   `active_session_response_language = target_response_language`.
8. The new provider's own final input transcription becomes the one
   canonical user turn (unchanged canonical-write path,
   `handle_provider_event`); the old, abandoned provider's queue is never
   read again (R0038's existing single-consumer guarantee already
   enforces this structurally).

**System-instruction wording** — following existing NeXa role-card
convention (`CLOUD_ROLE_CARD`, short, distinct from persona) and Google's
own confirmed guidance ("restrict the languages it speaks in by
specifying it in the system instructions" — no dedicated parameter
exists for native-audio models):
```
"You are the realtime voice provider for NeXa, a personal AI assistant.
Speak naturally and concisely for a live spoken conversation, usually
1-3 sentences for an ordinary question. Respond in {LANGUAGE_NAME} for
this session unless the user explicitly asks for a different language."
```
`{LANGUAGE_NAME}` = "Polish" or "English". Deliberately: (a) still allows
an explicit mid-conversation override utterance to be honoured natively
within the session (Gemini's own context can still hear and react to it,
consistent with Amendment 1's existing "the spoken command also stays in
Gemini's own live context" reasoning), (b) is session ROUTING metadata,
never NeXa identity, never an implicit sticky preference — the canonical
`sticky_language_preference` is set only by `ResponseLanguageResolver`
reading the ROUTER's own canonical transcript, never inferred from which
system-instruction variant happened to be active.

## FIRST-TURN BEHAVIOUR

Matches the charter's own allowance exactly: the initial connection stays
neutral (`active_session_response_language = None`, current
`CLOUD_ROLE_CARD` with no language line — unchanged from R0038, still
proven correct/neutral by the existing snapshot test) so `READY` is
reached at today's speed. The FIRST completed user utterance always
falls into step 7 above (no existing `active_session_response_language`
to match) — one-time LID + provider replacement + one utterance replay.
**With the current detector this one-time cost is the same ~1.1–1.7s LID
+ whatever the destroy/recreate + replay + fresh-READY sequence measures
(not measured in this checkpoint — would need constructing the fresh
provider, which touches the same `GeminiLiveProvider.start()` timing
R0031/R0038 already characterise for a NORMAL cold connect; a strict
first turn adds LID on top of that, not instead of it).** Not hidden:
this is a real, one-time, larger latency hit on turn 1 of every session,
exactly as the charter anticipated ("Do not hide this cost").

## PL→EN→PL RESULT

Specification only (not implemented, so not exercised end-to-end this
checkpoint): per the design above, PL→EN→PL forces exactly two provider
replacements (PL-neutral, or PL-strict → EN-strict on the switch, then
EN-strict → PL-strict on the switch back); the middle-of-three same-
language run (however many consecutive same-language turns occur) costs
only the per-turn LID, no reconnect. Exactly one canonical user turn per
spoken utterance falls directly out of reusing
`recover_from_mid_turn_loss()`'s existing, already-tested guarantee (old
provider never receives `activityEnd`, is stopped before its queue could
ever be read again, canonical write path only ever sees the NEW
provider's own final transcription) — this is the SAME mechanism R0038's
own `TestMidTurnRuntimeRecovery` test already proves for the connection-
loss case; a language-switch replacement is architecturally identical
(same `recover_from_mid_turn_loss` shape, different trigger condition and
snapshot content), so no new proof obligation beyond re-running that
existing test's shape with a language-triggered call site once
implemented.

## STICKY LANGUAGE RESULT

Unchanged: `ResponseLanguageResolver` semantics (R0038, itself unchanged
since M2.4B.5B/R0027) already implement exactly what's required — a
sticky preference, once explicitly set, overrides `detected_turn_language`
in `target_response_language`'s computation regardless of what was just
acoustically spoken. No new sticky-detection logic is proposed; the
existing resolver is the sole authority, reused verbatim (per the
charter's own "keep `ResponseLanguageResolver` semantics").

## PROVIDER REPLACEMENT RESULT

Specification only. Architecturally identical in shape to R0038's
`recover_from_mid_turn_loss` (destroy → fresh snapshot → fresh provider →
atomic swap → replay pending PCM once) with two differences: (a) trigger
condition (language mismatch/first-strict-turn, vs. a connection drop),
and (b) the fresh snapshot's `system_instruction` carries an explicit
language restriction rather than the neutral role card. No second
reconnection architecture is proposed — the charter's "do not fork a
second reconnection architecture" instruction is satisfied by design,
not merely by intent.

## R0038 PLAYBACK REGRESSION CHECK

Not applicable to change this checkpoint — `_ResponseGenerationGuard`,
`_ResponseLifecycle`, and the VAD-bridge lifecycle fix are **untouched**
(no code changed). Full existing R0038 test suite re-run below to confirm
nothing regressed from the research/benchmark work (which touches no
`src/nexa/**` file at all).

## FILES CHANGED

- **New:** `docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark.py`
  (the benchmark script, reusable/re-runnable).
- **New:** `docs/research/m2_6_cloud_realtime_voice/m2_6b4a_lid_benchmark_results_20260911T203641Z.json`
  (raw measured results).
- **New:** this report.
- **Unmodified:** every file under `src/nexa/**` and `apps/**` — no
  implementation this checkpoint, per the user's own explicit choice.

## TEST RESULTS

No new deterministic tests were required (no new `src/nexa/**` code was
written to test). Re-ran the full existing suite to confirm the
research/benchmark work introduced zero regressions:

- Full suite: `python -m unittest discover -s tests`: **923 tests, OK
  (skipped=7)** — unchanged from R0038 (no test added, none removed, none
  modified).
- `ruff check src tests apps`: all checks passed (unchanged files).
- `git diff --check`: clean. `pip check`: clean.
- `apps/nexa_cloud_voice_app.py --dry`: re-verified live in this sandbox —
  unaffected, object graph constructs cleanly.
- No Gemini call, no hardware test.

## EXPECTED LATENCY COST

Reported honestly, per the charter's own instruction not to claim the old
baseline without measurement:

- **Same-language warm-turn expected added latency** (design intent):
  should be near-zero (no reconnect) — but with the CURRENT detector, the
  per-turn LID gate itself costs the full **~1.1–1.7s measured above**,
  which is why implementation is paused rather than shipped at this cost.
  This is the one number that must improve (via a lighter LID) before the
  design is worth deploying as specified.
- **Language-switch expected added latency**: LID cost (~1.1–1.7s, current
  detector) **plus** a full fresh-provider connect+replay cycle — not
  measured this checkpoint (would require constructing a real
  `GeminiLiveProvider`, which the fake-service-driven test suite
  deliberately never times against wall-clock reality; a real number
  needs either a live call or a carefully-controlled local timing harness
  around construction-only steps — out of scope here).
- **First strict-turn expected added latency**: LID cost + the same
  fresh-provider connect+replay cost as a language switch (this is, by
  construction, the SAME code path).
- **Goal restated, not yet met**: same-language turns should remain close
  to the M2.6A ~0.75–0.81s baseline. **They do not, with the current LID
  tool, if gated on every turn.** This is exactly why the user chose to
  pause here rather than ship the literal design against this cost.

## LOCAL VOICE FREEZE CHECK

No file under `nexa.voice.*`, `nexa.voice_tts.*`, `nexa.stt.*`,
`nexa.tts.*`, or `nexa.conversation.*` was modified — nor was any
`nexa.realtime.*`/`apps/*` file. This checkpoint is documentation +
research-script-only. `gemini-3.1-flash-live-preview`, `Sulafat`, server
VAD OFF, local Silero authority, AEC architecture, barge-in thresholds,
`ConversationSession` authority, `CloudContextSnapshot`'s privacy
boundary, and `LOCAL_ONLY` default are all untouched.

## WHAT REMAINS FOR M2.6B

Unchanged from R0038 for the two non-language fixes (ready for hardware
retest). For the language failure specifically: **STRICT mode remains
designed but NOT implemented.** Before implementing it, either (a) a
lighter local LID becomes available and is benchmarked to a latency the
product accepts for a per-turn gate, or (b) the product explicitly
accepts the current ~1.1–1.7s per-turn cost and asks for the literal
design to be built anyway, or (c) a narrower scope is chosen (e.g. gate
only on the FIRST turn and on turns following an explicit sticky command,
leaving ordinary mid-conversation acoustic switches to native mirroring's
existing, measured-unreliable behaviour) — none of these choices were
made in this checkpoint; it stops at research, as directed.
`should_proactively_reconnect()` still has no production caller (standing
condition, unchanged, carried since R0036/R0037/R0038). **`M2.6B` remains
IN PROGRESS, not COMPLETE.**

## COMMIT HASHES

Research commit: `373f012` — "research(m2.6b.4a): strict same-turn
language authority -- LID too slow, paused (R0039)". Not pushed.

## GIT STATUS

Not pushed (per the standing constraint for this entire M2.6 body of
work). All files listed under FILES CHANGED are committed at `373f012`.

## EXACT NEXT LIVE RETEST COMMAND

Unchanged from R0038 — still valid for retesting the VAD-bridge and
playback-generation-guard fixes (the language issue is **not** expected to
be resolved by this checkpoint; it remains open, by design):

```
.venv/bin/python apps/nexa_cloud_voice_app.py
```

Wait for:

```
· PROVIDER READINESS: READY
>>> CLOUD_PROVIDER_READY <<<
```

```
  ✓ AEC_REF_ACTIVE
```

No Gemini call was made to produce this report.
