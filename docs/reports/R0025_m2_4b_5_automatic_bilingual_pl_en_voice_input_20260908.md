# R0025 — M2.4B.5: Automatic Bilingual PL/EN Voice Input

- **Date:** 2026-09-08
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.5 — automatic
  bilingual PL/EN voice input (implementation)**
- **Status:** **IMPLEMENTED + deterministic tests + headless latency
  measured. Live operator voice acceptance PENDING** — the one-command
  harness is ready (`apps/nexa_bilingual_voice_probe.py`); the 10-utterance
  same-session PL↔EN + sticky-preference run needs the operator's mic.
- **Related:** `R0024` (the evidence authority — 50-utterance real corpus
  benchmark, architecture decision), `ADR-0003` D5 + **Amendment 1**
  (added this stage), `R0009` (per-turn response-language directive +
  KV-cache discipline), `R0023` (`gemma4:e4b` + `num_thread=2` +
  `keep_alive=30m` + warm-up — all unchanged), `R0016` (Polish
  transcript-quality track — still separate, not touched).
- **Research dir:** `docs/research/m2_4b_bilingual_stt/`.

---

## TASK RESULT

**PASS (implementation) — live operator acceptance pending by design.**

The R0024 architecture is implemented behind the existing STT boundary
(ADR-0003 D11), on the one canonical `ConversationSession`, with
`gemma4:e4b` / `num_thread=2` / `keep_alive=30m` / warm-up untouched:

- **Library-level PL/EN detection** — `nexa.stt.WhisperCppLanguageDetector`:
  a `ctypes` binding to the **already-installed, pinned** `libwhisper.so`
  (`v1.9.3`, same build tree as `whisper-cli`). No fork, no vendored code,
  no version change, no new dependency (`ctypes` is stdlib). It returns
  `p_pl`, `p_en`, whisper's raw top-1 language, and its confidence — never
  a single collapsed string. It only *detects*; it never transcribes.
- **`LanguageIdGuard`** — one authority, pure/deterministic. AUTO_ACCEPT
  (trust the detector's PL/EN call) vs FALLBACK_REDECODE (do not), from
  the constrained `argmax(p_pl, p_en)`, the raw language, a **configurable
  confidence threshold (0.60 — R0024 initial calibration, documented as
  such, telemetered)**, and a duration floor (2.0 s — R0024 corpus
  separation).
- **`BilingualSpeechTranscriber`** — implements `SpeechTranscriber`:
  LID → guard → **exactly one** explicit `WhisperCppTranscriber` decode in
  the guard-selected PL/EN language. **The wrong-language transcript is
  never generated**, so there is nothing unsafe to send to
  `ConversationSession`. Holds the transient `last_input_language`
  (`pl`|`en`|`None`).
- **`ResponseLanguageResolver`** (`nexa.conversation`) — a *separate*
  authority. Default: mirror `InputSpeechLanguage`. Explicit request
  ("Answer in English.", "Odpowiedz po polsku.", "Od teraz mów po
  angielsku.", "Wracamy do polskiego.") → switch **and** set a transient
  sticky session preference. Deterministic narrow request detector — does
  not fire on ordinary content mentioning a language.
- **TTS voice** maps from `ResponseLanguage` (via `bridge.select_voice`),
  not from the transcript text and not from the STT decode language.
- **ADR-0003 Amendment 1** added.

**Headless latency (real ctypes LID + real CLI decode over the R0024
corpus, `b5_latency_probe.py`):** total **~2.83 s mean / 2.80 s median /
3.00 s p90** per utterance — **≈ +1.13 s** over the ~1.7 s explicit
baseline, matching R0024's `-l auto` estimate, **flat** (no conditional
second decode; the FALLBACK path is still one decode, just in the
session's language). 552 pytest / 559 unittest pass; `ruff` clean; `git
diff --check` clean. Not pushed.

## R0024 DECISION IMPLEMENTED

| R0024 finding / recommendation | how B.5 implements it |
|---|---|
| whisper `v1.9.3` never confuses PL↔EN; `argmax(p_pl,p_en)` classified all 30 monolingual | `LanguageIdGuard`: raw ∈ {pl,en} → trust it; raw third-language → constrained `argmax` (confidence- or ratio-gated) |
| the 3 LID misses were low-confidence third-language slips | confidence threshold 0.60 + a "decisive PL/EN split" ratio rule that recovers the `ru`/`ko`/`he` monolingual cases |
| short PL utterances are the dangerous case ("Tak." → "Talk.") | sub-2.0 s → **always** FALLBACK → decode in `last_input_language` (or bootstrap) |
| the ideal is a true `{p_pl,p_en}` detector, which needs the whisper.cpp library not the CLI | `ctypes` binding to the pinned `libwhisper.so` (`whisper_pcm_to_mel` + `whisper_lang_auto_detect`) |
| `-dl` → explicit adds latency, no detection benefit — don't two-pass every turn | LID (ctypes, model resident) + one decode; **no** conditional third pass; ~+1.1 s total (== `-l auto`) |
| mixed = 6 USABLE / 4 PARTIAL / 0 BROKEN — tolerable, not solved | no dual-decode merging; guard falls back to session language on tied splits; telemetry recorded; status = "best-effort / deferred" |
| keep `ggml-base-q8_0` / `-t 4`; no stronger-model download | unchanged |
| InputSpeechLanguage ≠ ResponseLanguage ≠ TTSVoice | 3 separate authorities: `BilingualSpeechTranscriber`, `ResponseLanguageResolver`, `bridge.select_voice` |

## ARCHITECTURE

```
mic → VAD → utterance audio (16 kHz mono PCM16, unchanged pipeline)
  │
  ├─ WhisperCppLanguageDetector.detect(audio)     ── ctypes, pinned libwhisper.so
  │     → LanguageDetectionResult(p_pl, p_en, raw_language, raw_confidence)
  │
  ├─ LanguageIdGuard.evaluate(detection, audio_duration_s, last_input_language)
  │     → GuardOutcome(AUTO_ACCEPT | FALLBACK_REDECODE, selected_language ∈ {pl,en}, reason)
  │        AUTO_ACCEPT  → selected = detector's PL/EN call ; update last_input_language
  │        FALLBACK     → selected = last_input_language or bootstrap ; do NOT update
  │
  ├─ WhisperCppTranscriber.transcribe(audio, language=selected)   ── ONE decode, PL or EN
  │     → CanonicalTranscript
  │
  └─ TranscriptionResult(text, language=selected, language_decision=<full telemetry>)
        │  (through the unchanged SerialTranscriptionQueue / VoiceRuntime)
        ▼
   VoiceConversationAdapter._run_turn
        ├─ ResponseLanguageResolver.resolve(transcript, input_language=selected)
        │     no request, no sticky → ResponseLanguage = InputSpeechLanguage
        │     explicit request      → ResponseLanguage = requested ; sticky := requested
        │     sticky set            → ResponseLanguage = sticky
        ├─ on_turn_language(TurnLanguage{...})   → terminal debug + bridge.select_voice(ResponseLanguage)
        └─ ConversationSession.send(transcript, response_mode=VOICE, response_language=ResponseLanguage)
              → ConversationContext injects language_directive(ResponseLanguage) for THIS turn
                (historical turns keep their stored resolved language — replay-stable, R0009)
        ▼
   AssistantSpeechBridge → NexaSpeechPlanner → continuity → PiperHttpTTSService
        voice = pl_PL-gosia-medium | en_GB-jenny_dioco-medium   (from ResponseLanguage)
```

**Five concepts, five owners, never one variable:** `InputSpeechLanguage`
(& `STTDecodeLanguage`, identical here) = `BilingualSpeechTranscriber` +
`LanguageIdGuard`; `CanonicalTranscript` = the one CLI decode;
`ResponseLanguage` = `ResponseLanguageResolver`; `TTSVoice` =
`bridge.select_voice` / `voice_for_language`.

## LANGUAGE DETECTION BOUNDARY

`nexa.stt.language_detection.WhisperCppLanguageDetector` (ctypes):

- Loads `libwhisper.so` (resolves `libwhisper.so` / `.so.1` / `.so.1.9.3`
  next to `whisper-cli`; `SttLibraryNotFoundError` if absent). The lib's
  `RUNPATH` already points at its own dir, so its `libggml*` siblings load
  automatically. Model = the same `ggml-base-q8_0.bin` (`SttModelNotFoundError`
  if absent).
- Binds only: `whisper_init_from_file` (the simple path-only init — **no
  by-value struct**, so zero ABI-layout risk), `whisper_pcm_to_mel`,
  `whisper_lang_auto_detect`, `whisper_lang_id`, `whisper_lang_max_id`,
  `whisper_lang_str`, `whisper_free`, `whisper_log_set`.
- Model is loaded once at construction (context resident, ~130 MB — the
  same the CLI would load per call) and freed on `.close()`. Model-load
  stderr spam is silenced (fd-level redirect for the load + a no-op
  `whisper_log_set` callback kept alive for the process lifetime).
- `detect(audio)` runs `pcm_to_mel` + `lang_auto_detect` on the executor,
  under a lock (the STT queue already serialises; the lock is defence in
  depth). Returns the typed `LanguageDetectionResult` with
  `constrained_language` / `constrained_confidence` / `pl_en_ratio`
  helpers. A `pcm_to_mel` / `lang_auto_detect` non-zero return raises
  `LanguageDetectionError` — never a fabricated language.
- Measured cost on the Pi: **~1.15–1.23 s per utterance** (the encoder
  pass), model resident.

*If the library ever cannot be bound* the task's sanctioned fallback is a
CLI-only guard on `-l auto`'s top-1 (no `p_pl`/`p_en`, never fabricated) —
not needed here: the ctypes binding is simple and works.

## LANGUAGE ID GUARD

`nexa.stt.language_guard.LanguageIdGuard` — one authority, no I/O, no
whisper.cpp. `GuardConfig` (all R0024 calibration, all configurable):

| knob | default | rationale (R0024) |
|---|---|---|
| `confidence_threshold` | **0.60** | separation region in R0024; the misses were ≤ 0.53, confident hits 0.6–0.99. **Not universal** — tune from telemetry. |
| `min_reliable_duration_s` | **2.0** | on the corpus every monolingual sentence was ≥ 2.37 s and every one-word/ambiguous utterance ≤ 1.73 s |
| `decisive_ratio` / `decisive_floor` | **4.0 / 0.15** | recovers the `pl→ru` / `en→ko` / `en→he` monolingual misses (PL/EN split was ~20–40×) |
| `trust_supported_raw_language` | **True** | R0024: **0 PL↔EN confusion** — raw `pl`/`en` trusted even at p ≈ 0.27 |
| `bootstrap_language` | passed per-call | the session's configured default (`--bootstrap`, default `pl`) |

Decision order: sub-duration → FALLBACK · raw ∈ {pl,en} → AUTO_ACCEPT ·
`max(p_pl,p_en) ≥ threshold` → AUTO_ACCEPT(constrained) · decisive split →
AUTO_ACCEPT(constrained) · else → FALLBACK.

**Calibration replay over all 50 R0024 fixtures + their real p_pl/p_en:**
30/30 monolingual → AUTO_ACCEPT, **correct language** (even with
`last_input_language = None`); 10/10 shorts → FALLBACK (inherit); mixed →
matches the R0024 `-l auto` language pick (never worse).

## LAST INPUT LANGUAGE

`BilingualSpeechTranscriber.last_input_language` — `pl` | `en` | `None`.
Rules (task-specified, implemented):

- confident PL turn → `last_input_language = "pl"`
- confident EN turn → `"en"`
- ambiguous / low-confidence (FALLBACK) → **inherit** it for the decode;
  do **not** overwrite it (nothing was learned)
- session start, none yet → the documented `bootstrap` language
  (`--bootstrap`, default `pl` — the operator's primary), deterministic

It is a plain attribute on the transcriber. **Not** a `ConversationTurn`,
**not** in `session.history`, **not** long-term memory. `reset_language_state()`
clears it.

## FALLBACK RE-DECODE

The task's "re-decode the original audio in the fallback PL/EN language"
is realised as **the single decode simply uses the fallback language** —
because detection runs *first*, no wrong-language transcript is ever
produced to reject. The FALLBACK path therefore adds **no** extra decode
pass. `LanguageDecision.redecoded == True` means "the guard chose the
decode language from session state, not from detection", and the decode
always receives the **original, unmodified** utterance bytes (asserted in
tests). A third-language label (`ru`/`ko`/`he`) can never be the decode
language.

## RESPONSE LANGUAGE RESOLVER

`nexa.conversation.response_language.ResponseLanguageResolver` — one
deterministic authority, separate from STT.

- **No request, no sticky** → `ResponseLanguage = InputSpeechLanguage`
  (spoken PL → answer PL, spoken EN → answer EN).
- **Explicit request in the transcript** → answer in the requested
  language **and** set the sticky `ResponsePreference` (`pl`|`en`|`None`).
  A one-turn-only override is a possible later refinement; the accepted
  behaviour (and what the acceptance script needs) is sticky.
- **Sticky set, no new request** → answer in the sticky language,
  regardless of what was spoken.

`detect_explicit_language_request(text)` is a narrow regex set requiring an
imperative *about how NeXa answers* ("answer/reply/speak/… in
English/Polish", "od teraz … po angielsku", "wracamy do polskiego", …),
with a content-question deny-list ("how do you say … in English", "what is
… in Polish") so ordinary content that merely names a language does **not**
switch anything. The LLM is never asked to be the routing authority.

Threaded to the model via `ConversationSession.send(..., response_language=…)`
→ `ConversationContext` emits `language_directive(response_language)` for
the current turn; historical turns keep their **stored** resolved language
(session-internal `_response_languages`, index-aligned with `_history`) so
the rebuilt prompt is byte-stable for KV-cache reuse (R0009). `None`
(always for typed chat) = unchanged R0009 text detection.

## EXPLICIT / STICKY LANGUAGE REQUESTS

| utterance | effect | `ResponsePreference.sticky` after |
|---|---|---|
| "Answer in English." / "Odpowiedz po angielsku." | reply EN this turn onward | `en` |
| "Odpowiedz po polsku." / "Answer in Polish." | reply PL this turn onward | `pl` |
| "Od teraz mów po angielsku." | reply EN from now on | `en` |
| "Wracamy do polskiego." / "Let's go back to Polish." | reply PL from now on | `pl` |
| "Tell me about Polish history." | **no change** — content mention | (unchanged) |
| "What is the English word for kot?" | **no change** | (unchanged) |
| (any ordinary PL/EN question) | mirror `InputSpeechLanguage`, unless a sticky is set | (unchanged) |

Deterministic tests cover every row.

## TTS LANGUAGE MAPPING

Unchanged voice identifiers (`pl_PL-gosia-medium` / `en_GB-jenny_dioco-medium`,
`voice_for_language`). The **source** of the language changed:
`AssistantSpeechBridge.select_voice(response_language)` is called from the
adapter's `on_turn_language` before the reply starts; `on_user_transcript`
no longer derives the voice from the transcript text unless no
`ResponseLanguageResolver` is wired (pre-B.5 back-compat, still tested).
No voice change, no speech-speed change.

## LATENCY

`docs/research/m2_4b_bilingual_stt/b5_latency_probe.py` — the **real**
production path (ctypes LID + CLI decode + guard) over R0024 corpus WAVs
in realistic conversational order (`last_input_language` meaningful):

| scenario | detect mean | decode mean | **total mean** | total median | total max |
|---|---|---|---|---|---|
| confident normal PL | 1.20 s | 1.79 s | **2.99 s** | 2.99 s | 3.08 s |
| confident normal EN | 1.16 s | 1.62 s | **2.77 s** | 2.78 s | 2.79 s |
| low-confidence PL short (FALLBACK) | 1.18 s | 1.54 s | **2.72 s** | 2.64 s | 3.00 s |
| language switch PL → EN | 1.17 s | 1.66 s | **2.82 s** | 2.84 s | 2.87 s |
| language switch EN → PL | 1.16 s | 1.65 s | **2.81 s** | 2.80 s | 2.91 s |
| third-language recovery (`ru`/`ko`/`he` → pl/en) | 1.15 s | 1.71 s | **2.86 s** | 2.84 s | 3.04 s |
| **overall (n = 24)** | ~1.17 s | ~1.66 s | **2.83 s** | **2.80 s** | 3.08 s (p90 3.00 s) |

**End-to-end added STT cost ≈ +1.13 s** vs the ~1.7 s explicit baseline
(R0024). **Flat** — the confident path is LID + one decode; the FALLBACK
path is **also** LID + one decode (no third pass). This matches R0024's
`-l auto` cost (~+1.1 s) while additionally yielding `p_pl`/`p_en`, which
`-l auto` on the CLI cannot. The END_OF_TURN → first-audio figure needs
the live pipeline (operator session).

## REAL PI RESULTS

Headless, real path, over the R0024 fixtures (see LATENCY + the guard
calibration replay):

- **PL monolingual:** 15/15 AUTO_ACCEPT → decode `pl`; transcripts
  identical to R0024's explicit `-l pl` baseline (incl. its pre-existing
  S2s `sen→sam`, `niebieskie→"nie pieskie"` — a `base/q8_0` issue, `R0016`,
  untouched here).
- **EN monolingual:** 15/15 AUTO_ACCEPT → decode `en`; identical to
  explicit `-l en` (incl. its pre-existing S2s).
- **third-language recovery:** `002` (raw `ru`) → `pl` "Jak ona
  powstaje."; `021` (raw `ko`) → `en`; `029` (raw `he`) → `en` "Why is the
  sky blue?" — all AUTO_ACCEPT via the decisive-split rule. The R0024
  wrong-script S3 outputs (Cyrillic / Hebrew) **no longer occur**.
- **CPU/RAM/thermal:** unchanged from R0024 — the decode is the same
  `whisper-cli base/q8_0 -t 4` (peak RSS ~221 MB); the detector adds one
  resident `libwhisper` context (~130 MB) shared for the session. No
  throttling in any run.

## SHORT UTTERANCE RESULTS

All 10 R0024 shorts (≤ 1.73 s) → **FALLBACK_REDECODE**, decode in
`last_input_language`:

| after PL context | after EN context |
|---|---|
| "Tak." → **"Tak."** (was `en` "Talk.") | "Yeah." → "Yeah." |
| "Nie." → **"Nie."** (was `ru` "Не.") | "No." → "No." |
| "Dobra." → **"Dobra."** (was `ru` "Доброе утро!") | "Right." → "Right." |
| "Okej." → "Ok." | "Okay." → "Okay." |

**No wrong-script / third-language text ever enters `ConversationSession`**
— it is never generated. When FALLBACK fires the decision reason is
logged (`LanguageDecision.guard_reason` / `fallback_reason`) and the
canonical transcript is the one decode in the fallback language. Turn 1
with no prior language uses the deterministic `--bootstrap` (default `pl`).

## PL → EN → PL SAME-SESSION RESULT

Headless proof (the `switch_pl_to_en` / `switch_en_to_pl` scenarios + the
adapter tests): PL and EN turns alternate in **one**
`BilingualSpeechTranscriber` / **one** `ConversationSession`, with
`last_input_language` tracking correctly and `ResponseLanguage` mirroring —
no restart, one history. Sticky-preference behaviour (items 7–10:
"Odpowiedz po polsku." → later EN input still answered PL; "Answer in
English." → later PL input still answered EN; "Wracamy do polskiego." →
back to PL) is proven by `ResponseLanguageResolver` + adapter tests. The
**live, spoken** end-to-end run is the operator acceptance below.

## MIXED / CODE-SWITCH STATUS

**BEST EFFORT / DEFERRED — not guaranteed** (`MIXED_CODE_SWITCH_STATUS`
constant; asserted not "guaranteed"). B.5 adds no dual-decode merging. On
the R0024 mixed fixtures the guard selects the same PL/EN language `-l
auto` picked (or FALLBACK to session language on a tied split), so the
transcript is **not worse** than the R0024 auto baseline (6 USABLE / 4
PARTIALLY_USABLE / 0 BROKEN). Every mixed turn's `p_pl`/`p_en` + guard
decision is captured in `TranscriptionResult.language_decision` for later
research.

## ADR-0003 AMENDMENT

`docs/decisions/ADR-0003_realtime_voice_foundation.md` → **Amendment 1**
(Accepted, 2026-09-08). Supersedes *part of* D5: explicit fixed language
stays supported; **guarded automatic per-utterance PL↔EN is now accepted
production behaviour**; true within-utterance code-switch is **not**
guaranteed; ambiguous shorts use session-local `last_input_language` +
the single fallback-language decode; `InputSpeechLanguage ≠
ResponseLanguage`; `R0024` is the evidence authority. No unrelated ADR
decision was rewritten.

## TEST RESULTS

- `pytest` — **552 passed / 7 skipped / 14 subtests** (was 552 at B.4
  head; **+39** `tests/test_bilingual_voice_input.py`; note B.4's own
  bench tests are included).
- `python -m unittest discover -s tests` — **559 OK / 7 skipped**.
- `ruff check src tests apps docs/research/m2_4b_bilingual_stt` — clean.
- `git diff --check` — clean.

`tests/test_bilingual_voice_input.py` (39, offline — fake detector + fake
base transcriber at the external boundary) proves:

- confident PL → `pl`; confident EN → `en`; PL/EN alternate in one
  session; `last_input_language` updates only on AUTO_ACCEPT.
- supported raw language trusted at low absolute p (R0024); third-language
  decisive split recovers; sub-2 s always FALLBACK; no-last-language uses
  the deterministic bootstrap; threshold configurable.
- low-confidence third-language → FALLBACK; **rejected/third-language text
  never produced** (only `pl`/`en` decodes ever run); the fallback decode
  gets the **original audio bytes**; telemetry fields all present.
- `ResponseLanguageResolver`: mirror by default; explicit request switches
  + sets sticky; sticky EN holds over PL input; "Wracamy do polskiego." →
  sticky PL; "Od teraz mów po angielsku." sticky; **ordinary content
  mentioning "Polish"/"English" does not switch**; input ≠ response
  language.
- `ConversationContext`/`Session`: resolved response language overrides
  R0009 text detection for the current turn; historical directives are
  replay-stable (byte-identical prompt prefix); response language is
  **never** stored in `history`; `TEXT` mode byte-for-byte unchanged.
- adapter: PL then EN turns hit the **same** session; sticky request makes
  input ≠ response language on the wire; a bare transcript still reaches
  `send()` unchanged; no-resolver path keeps pre-B.5 behaviour.
- bridge: `select_voice` maps from `ResponseLanguage`; does not get
  overridden by transcript text; resets per response; falls back to text
  detection only with no resolver.
- scope: `DEFAULT_LOCAL_MODEL == gemma4:e4b`, `num_thread == 2`,
  `keep_alive == "30m"`, warm-up intact; no `gemma4:e2b` anywhere in the
  new code; the new STT modules construct no `ConversationSession` /
  provider; mixed status not marked guaranteed.

Plus the existing **552** tests still green (session / context /
response-mode / adapter / bridge / voice architecture unchanged in
behaviour).

## FILES CHANGED

**New (`src/`):**
- `src/nexa/stt/language_detection.py` — ctypes `WhisperCppLanguageDetector`
  + `LanguageDetectionResult` + `LanguageDetector` protocol.
- `src/nexa/stt/language_guard.py` — `LanguageIdGuard` + `GuardConfig` +
  `GuardOutcome` + `GuardDecision`.
- `src/nexa/stt/bilingual.py` — `BilingualSpeechTranscriber` +
  `LanguageDecision` telemetry + `MIXED_CODE_SWITCH_STATUS`.
- `src/nexa/conversation/response_language.py` — `ResponseLanguageResolver`
  + `ResponsePreference` + `ResponseLanguageDecision` +
  `detect_explicit_language_request`.

**Changed (`src/`):**
- `src/nexa/stt/config.py` — `default_whisper_lib_path()`.
- `src/nexa/stt/errors.py` — `SttLibraryNotFoundError`,
  `LanguageDetectionError`.
- `src/nexa/stt/transcriber.py` — `TranscriptionResult.language_decision`
  (optional, `None` for the plain transcriber — additive).
- `src/nexa/stt/__init__.py` — exports.
- `src/nexa/conversation/context.py` — `ConversationContext.build(...,
  response_languages=…)` + `turn_response_languages`; `to_provider_messages`
  uses a stored resolved language when present (else R0009). `TEXT`
  unchanged.
- `src/nexa/conversation/session.py` — `send(..., response_language=…)` +
  session-internal `_response_languages` (not history).
- `src/nexa/conversation/__init__.py` — exports.
- `src/nexa/voice_conversation/adapter.py` — optional
  `response_language_resolver` + `on_turn_language` + `TurnLanguage`;
  carries the whole `TranscriptionResult` through the queue; passes
  `response_language` to `send()`. No-resolver path unchanged.
- `src/nexa/voice_conversation/queue.py` — item type generalised
  (`str` → opaque; no behaviour change).
- `src/nexa/voice_conversation/__init__.py` — exports.
- `src/nexa/voice_tts/bridge.py` — `select_voice(response_language)`;
  `on_user_transcript` only derives voice from text when no explicit voice
  was set.

**New (apps / research / docs):**
- `apps/nexa_bilingual_voice_probe.py` — the one-command live acceptance
  harness.
- `docs/research/m2_4b_bilingual_stt/b5_latency_probe.py` (+ its
  `b5_latency_probe_*.json`).
- `tests/test_bilingual_voice_input.py` (39).
- `docs/decisions/ADR-0003_…md` — Amendment 1.
- `docs/reports/R0025_…md` (this); `docs/CURRENT_STATE.md`.

**Unchanged:** `gemma4:e4b`, `num_thread=2`, `keep_alive=30m`, warm-up,
persona, `ResponseMode`, `SpeechPlanner`, continuity controller, Piper
voices/speed, `ggml-base-q8_0` / `-t 4`, one `ConversationSession`. No
`nexa.bootstrap` / `nexa.config` model change. M2.5 not started.

## COMMIT HASH

_Recorded on commit (this stage's implementation commit)._

## GIT STATUS

Branch `main`, not pushed. `ruff` clean for this stage's scope;
`git diff --check` clean. No model weights / `.so` blobs staged (the
ctypes binding loads the already-installed pinned lib by path).

## NEXT STEP

**Operator runs the live acceptance harness** — one command, one
continuous `ConversationSession`, the 10 utterances below. Confirm from
the concise per-turn debug that: PL↔EN alternate with no restart; short
"Dobra." inherits the surrounding language; "Odpowiedz po polsku." makes
later EN input answered in PL; "Answer in English." makes later PL input
answered in EN; the voice matches the response language each turn.

After operator confirmation: mark M2.4B.5 `OPERATOR-CONFIRMED`, then
**M2.5 — barge-in**. Separate/optional later: Polish transcript-quality
model track (`large-v3-turbo-q5_0`, approval before download); a real
within-utterance code-switch stage if it proves to matter; tune the guard
threshold from accumulated telemetry.

### Live harness

```
./.venv/bin/python apps/nexa_bilingual_voice_probe.py
```

Speak, in one session (no restart):

1. Co to jest czarna dziura?
2. Okay, tell me more about it.
3. Z czego składa się gwiazda?
4. What is the actual colour of the Sun?
5. Dobra.
6. Tell me something interesting.
7. Odpowiedz po polsku.
8. Why is the sky blue?
9. Answer in English.
10. Po co człowiekowi sen?
