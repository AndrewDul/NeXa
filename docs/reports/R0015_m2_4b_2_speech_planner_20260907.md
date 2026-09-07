# R0015 — M2.4B.2: Polish-aware speech planner / TTS-only normalization

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.2 — implementation**
- **Related:** `docs/reports/R0012_m2_4b_speech_flow_research_20260906.md`
  (recommended this exact component and boundary policy),
  `docs/reports/R0013_m2_4b_1_gap_profiler_20260906.md` (the `--report`
  metrics this integrates with),
  `docs/reports/R0014_m2_4b_1a_cpu_scheduling_spike_20260907.md`
  (established that intra-response silence is primarily LLM text
  starvation — **that is B.3, not this stage**),
  `docs/architecture/M2_4_STREAMING_TTS_ARCHITECTURE.md` (frozen M2.4 —
  commit `36ec1e4`).

**Scope: fix WHAT TEXT reaches Piper. One small NeXa-owned
`FrameProcessor` between `AssistantSpeechBridge` and `PiperHttpTTSService`.
No pacing, no audio-buffer control, no look-ahead controller, no CPU
scheduling, no dynamic speech speed, no fillers, no voice change, no M2.5.
`ConversationSession` / canonical assistant text / history / persona /
model output / response-language authority are all unchanged.**

> **Follow-up (2026-09-07):** the real operator microphone run exposed two
> normalization edge cases — inline LaTeX math (`$\text{H}$`) reaching
> Piper, and a dangling trailing `"("` on a reply the model truncated at
> its 200-token cap. Both are fixed in **M2.4B.2A** —
> `docs/reports/R0016_m2_4b_2a_tts_normalization_edge_cases_20260907.md`.
> **M2.4B.2 + M2.4B.2A are `OPERATOR-CONFIRMED` (2026-09-07)** — fresh mic
> run, operator verdict *"the spoken response itself is good if we ignore
> the pauses"*; `$\text{H}$` never reached Piper, `tiny_text_chunk_count =
> 0`. The remaining intra-response pauses are M2.4B.3 (see `R0017`).

---

## TASK RESULT

**PASS.**

- New `src/nexa/voice_tts/speech_planner.py` — `NexaSpeechPlanner`
  (`FrameProcessor`) + the pure, deterministic `normalize_for_speech()` and
  `find_phrase_cut()` it is built from.
- Pipeline is now `AssistantSpeechBridge → NexaSpeechPlanner →
  PiperHttpTTSService`. The planner consumes `LLMTextFrame` and emits one
  natural phrase per `AggregatedTextFrame`, which `PiperHttpTTSService`
  synthesizes directly (bypassing its English-only `SimpleTextAggregator`,
  the R0012-verified extension point).
- Polish-aware boundaries: 30+ abbreviations never end a phrase
  (`tzw. np. itd. itp. m.in. …`); decimals (`3.5`), ellipsis, quotes,
  parentheses, `; :`, and cautious comma handling; a minimum-phrase floor
  so `"1."` / `"np."` / `"Czy chodzi Ci o:"` can never be spoken alone.
- TTS-only Markdown normalization: `**bold**`, `*italic*`, `_ _`, `#`
  headings, `` `code` `` / fenced blocks, `>` quotes, `|` tables, links —
  none reach Piper. Numbered / bulleted list runs become natural prose
  (`"… Call of Duty oraz Battlefield."`).
- Conservative, non-gendered abbreviation *pronunciation* expansions
  (`np. → na przykład`, `itd. → i tak dalej`, `m.in. → między innymi`,
  `tj. → to jest`; EN `e.g./i.e./etc.`). **`tzw.` is deliberately NOT
  expanded** (gender-sensitive) — it is kept attached to the following
  noun phrase instead.
- English still works: EN sentence boundaries, `e.g./i.e./Dr./Mr.`, and
  language-neutral Markdown cleanup all covered by tests.
- **Transcript invariant proven:** the planner only ever sees the
  already-emitted token copy; it constructs no session/provider/persona,
  imports nothing from `nexa.conversation`/`nexa.bootstrap`/`nexa.providers`,
  defines no language classifier, and keeps its raw input byte-identical
  (only its *output* is normalized). `ConversationSession` history is the
  pure assistant text, exactly as before.
- **Tests:** `tests/test_voice_tts_speech_planner.py` — 45 deterministic
  tests (the brief's numbered list 1–32 plus harness/architecture guards).
  Full suite **360 passed / 7 skipped / 14 subtests**; `unittest discover`
  **367 OK**; `ruff` clean; `git diff --check` clean.
- **Long intra-response silence is NOT expected to be gone** — that is the
  known B.3 target (R0014). B.2's success criterion is *natural text
  boundaries and zero formatting garbage*, which is met.

## M2.4B.2 STATUS

**Implemented and self-tested. Awaiting the operator's in-person
microphone acceptance** (the same human gate as M2.1–M2.4). A scripted
end-to-end run on real hardware — real `gemma4:e4b`, real Piper HTTP, real
`LocalAudioOutputTransport`, only the mic/STT replaced by the three
scripted operator phrases — is recorded under **REAL HARDWARE TEST** below.

## SPEECH PLANNER ARCHITECTURE

```
VoiceConversationAdapter  (M2.3, unchanged)
   │  on_user_transcript / on_assistant_token / on_assistant_complete
   ▼
AssistantSpeechBridge     (M2.4, unchanged — FIFO LLM-frame vocabulary,
   │                        picks the PL/EN voice via the canonical
   │                        nexa.conversation.language function, forwards
   │                        TTSUpdateSettingsFrame + LLMFullResponseStart/
   │                        LLMTextFrame*/LLMFullResponseEnd)
   ▼
NexaSpeechPlanner          (NEW — the only new runtime component)
   ├─ consumes LLMTextFrame; forwards every other frame unchanged, in order
   ├─ normalize_for_speech()  — TTS-only Markdown/list/whitespace cleanup +
   │                            conservative abbreviation expansion
   ├─ find_phrase_cut()       — Polish/English-aware phrase boundary scan
   └─ emits AggregatedTextFrame(text, SENTENCE) — one natural phrase each
        │                       (raw_text=text, append_to_context=True,
        │                        skip_tts mirrored from the source frame)
        ▼
PiperHttpTTSService        (Pipecat 1.8.1, unchanged — process_frame routes
   │                        AggregatedTextFrame straight to _push_tts_frames,
   │                        bypassing SimpleTextAggregator)
   ▼
TtsStatusObserver → LocalAudioOutputTransport → USB speaker   (unchanged)
```

**Streaming model (no pacing):** the planner keeps the raw assistant copy
in `_raw` and, on every `LLMTextFrame`, re-runs `normalize_for_speech(_raw,
streaming=True)` and releases every phrase whose boundary is now safe
(`find_phrase_cut`). `_emitted` tracks the already-released prefix of the
normalized text; a phrase is only ever cut at a boundary that has a
following non-space character, so a completed `**bold**` / list-join before
that boundary is already in final form and the emitted prefix stays
stable. `streaming=True` additionally *holds* a Markdown list run that is
not yet terminated by a real following line, so list content is never
spoken half-formed or re-spoken. On `LLMFullResponseEndFrame` the planner
flushes the remaining normalized tail as exactly one `AggregatedTextFrame`
(if it contains any alphanumeric character) and resets. It also resets on
`LLMFullResponseStartFrame` and on `InterruptionFrame` (M2.4 half-duplex
only — no barge-in logic).

**Tunables** (module constants, constructor-overridable, marked CANDIDATE —
to be A/B-tuned with the B.1 `--report` profiler in B.3/B.4, not adopted
as product truth here):

| constant | value | role |
|---|---|---|
| `MIN_SENTENCE_CHARS` | 12 | min alnum for a `. ! ? …` phrase to stand alone (kills `"1."`, `"np."`; keeps a real short sentence) |
| `MIN_CLAUSE_CHARS` | 18 | min alnum left side for a `;` / `:` boundary (`"Czy chodzi Ci o:"` = 12 → held) |
| `MIN_COMMA_CHARS` | 40 | min alnum left side before a comma may be a boundary at all |
| `COMMA_MIN_BUFFER_CHARS` | 120 | a comma boundary is only taken once the un-emitted buffer is at least this long with no better boundary |
| `MAX_PHRASE_CHARS` | 240 | hard cap — cut at the last space so text is never held indefinitely |

## POLISH BOUNDARY POLICY

`find_phrase_cut(s)` scans left-to-right and returns the index of the first
*good* boundary, or `None` (hold for more tokens / the final flush).

1. **Sentence** — a run of `. ! ? …`. Rejected as a boundary when:
   - it sits at the very end of the current text (no lookahead char yet) —
     which also defers a decimal like `3.5` until the digit after the
     point has arrived;
   - it is a lone `.` immediately between two digits (decimal);
   - the text ending at the `.` matches a known abbreviation
     (`_ends_with_abbreviation`) — the Polish set
     `tzw np itd itp m.in ul al dr prof inż mgr św nr str godz min sek tys
     mln mld ok tj cd dot par przyp ww pt ds`, the English set
     `e.g i.e mr mrs ms dr prof vs etc st no fig inc ltd jr sr approx dept
     est`, a `word.word` suffix like the `in` of `m.in.`, or a single
     letter + `.` (an initial such as `J.`);
   - the left phrase has fewer than `MIN_SENTENCE_CHARS` alnum chars — the
     boundary is skipped and the text *merges forward* into the next
     phrase (so a tiny sentence is never emitted alone; a genuinely tiny
     *final* fragment can still be flushed at end-of-turn).
   An ellipsis (`…` or `...`) is always a real sentence end, never an
   abbreviation.
2. **Clause** — `;` or `:`, taken when the left side has ≥
   `MIN_CLAUSE_CHARS` alnum chars and a lookahead char exists.
3. **Comma** — recorded only when the left side has ≥ `MIN_COMMA_CHARS`
   alnum chars; *used* only if no sentence/clause boundary was found and
   the un-emitted buffer already exceeds `COMMA_MIN_BUFFER_CHARS`. Commas
   are otherwise never split on.
4. **Hard cap** — past `MAX_PHRASE_CHARS` with no boundary, cut at the last
   space.

Never split inside `( … )` (depth-counted) or inside a straight/`„…"`/`«…»`
quoted span. These two guards are what stop the R0012 example
`"z czarnymi dziurami (np."` and the mid-quote period.

## ENGLISH COMPATIBILITY

- English `. ! ?` sentence boundaries work unchanged (they are just Latin
  sentence-enders — no NLTK, no language arg needed).
- English abbreviations `e.g. i.e. Mr. Mrs. Ms. Dr. Prof. vs. etc. St. No.
  Fig. Inc. Ltd. Jr. Sr.` never create a false phrase break (shared
  `_ends_with_abbreviation`). `e.g./i.e./etc.` are expanded to
  `for example / that is / and so on`.
- Markdown normalization is entirely language-neutral (character-class
  regex, no dictionary).
- The planner is **not** a response-language classifier. It reads the
  language `AssistantSpeechBridge` already chose, from the
  `TTSUpdateSettingsFrame` voice it forwards (`pl_PL-gosia-medium` →
  `pl`, `en_GB-jenny_dioco-medium` → `en`), and uses it *only* to pick the
  list connector word (`oraz` / `and`) and the abbreviation-expansion set.
  It defaults to the constructor `default_language` until the first
  settings frame. The canonical PL/EN decision stays in
  `nexa.conversation.language` via the bridge, exactly as in M2.4.

## MARKDOWN NORMALIZATION

`normalize_for_speech(text, *, language, streaming=False)` — pure,
deterministic, **never raises** (any internal error falls back to a
minimal marker strip so a turn can never be silenced). Pipeline:

1. fenced code ` ``` … ``` ` removed entirely (never read code aloud);
   stray ``` ``` ``` stripped; inline `` `code` `` → `code`.
2. `[text](url)` → `text`; `<http…>` and bare `http(s)://…` → the bare
   host/path (scheme dropped).
3. `#` headings (leading and closing `#`), `>` blockquote markers.
4. emphasis: `**` / `__` removed; paired single `*` / `_` around a span
   removed.
5. table rule/pipe scaffolding removed; remaining `|` → space.
6. **list runs → prose:** a maximal run of `^\s*(\d+[.)]|[-*+•·‣▪])\s+…`
   lines is joined — `a, b oraz c.` (PL) / `a, b and c.` (EN), one final
   period, item-internal sentence punctuation trimmed. A preceding
   `"…:"` lead-in line is kept and the prose follows it. During streaming
   an unterminated trailing run is held until a real following line (or
   the final flush) so it is never spoken piecemeal.
7. conservative abbreviation expansion (below).
8. newlines → spaces, whitespace runs collapse, leftover lone markers
   (`*` `_` `#` `•` `>` `-`) swept, space-before-punctuation tidied.

The R0012 example
`**Dwa przykłady strzelanek:**\n1. *Call of Duty*\n2. *Battlefield*`
normalizes to `Dwa przykłady strzelanek: Call of Duty oraz Battlefield.`
— content preserved, no marker spoken, no semantic rewrite.

**`MarkdownTextFilter` was evaluated** (installed, R0012-verified) and is
*not* used: on this Pi it leaves mid-stream numbered markers (`\n1.`)
in place (its regex is not `MULTILINE`), leaves list newlines, and does no
list-to-prose join — so a NeXa normalizer was needed anyway; a single
focused deterministic one (no `markdown` package, ~120 lines, one test per
rule) is the smaller component. `text_filters` / `text_transforms` on the
TTS service are likewise unused — the planner is the one clean seam, and
using both would double-process the already-segmented frame.

## ABBREVIATION HANDLING

Two distinct mechanisms:

- **Boundary suppression** (always on) — every abbreviation in the PL/EN
  sets above stops `find_phrase_cut` from ending a phrase on that period.
  This alone guarantees `"tzw."` / `"np."` are never spoken as an isolated
  chunk: they always ride with the following words into the same phrase.
- **Pronunciation expansion** (`expand_abbreviations=True` by default,
  TTS-only, applied inside `normalize_for_speech`):

  | abbreviation | spoken as | note |
  |---|---|---|
  | `np.` | `na przykład` | period dropped |
  | `m.in.` | `między innymi` | period dropped |
  | `tj.` | `to jest` | period dropped |
  | `itd.` | `i tak dalej` | period dropped |
  | `itp.` | `i tym podobne` | period dropped |
  | `e.g.` / `i.e.` / `etc.` | `for example` / `that is` / `and so on` | period dropped |
  | **`tzw.`** | **— (not expanded)** | gender-sensitive (`tak zwany/zwana/zwane`); the safe option per the brief is to leave it attached to the following phrase, which the boundary suppression already does |

  The abbreviation's own trailing period is **always dropped** on
  expansion so an expanded abbreviation can never fabricate a mid-sentence
  break (`"planety itd. w galaktyce"` must stay one clause). A genuinely
  sentence-final abbreviation (`"A, B, C itd."`) then loses its full stop
  and merges with the next sentence — acceptable prosody, far better than
  a false split. No expansion changes grammatical agreement or invents
  information.

## TRANSCRIPT INVARIANT

The planner **cannot** touch canonical text, and it is proven three ways:

1. **Structural** (`ast` tests): `speech_planner.py` imports only
   `__future__`, `re`, `loguru`, `pipecat`. It never imports
   `nexa.conversation*`, `nexa.bootstrap`, `nexa.config`, `nexa.providers`,
   or an LLM client; never calls `ConversationSession` / `LocalModelProvider`
   / `LlamaServerProvider` / `PersonaConfig` / `ModelProvider`; defines no
   `detect_response_language` / `detect_language`. (Also covered by the
   package-wide `tests/test_voice_tts_architecture.py` dir-glob.)
2. **Positional:** the planner sits *downstream* of `AssistantSpeechBridge`
   in the Pipecat output pipeline. `on_assistant_complete(full_text)` is
   delivered to `ConversationSession` by `VoiceConversationAdapter`
   directly — a path the planner is not on. It receives only the
   `LLMTextFrame` *copy* of the token stream, exactly as the M2.4 bridge
   already did.
3. **Behavioural:** after feeding a formatted reply, `planner._raw ==
   "".join(source_tokens)` byte-for-byte — the planner keeps its input
   verbatim; only the `AggregatedTextFrame` *output* is normalized.
   The scripted hardware run below prints `ConversationSession.history`
   after all three turns — it is clean assistant prose with the original
   Markdown intact, unaffected by the planner.

## METRICS COMPARISON

Integrated with the corrected M2.4B.1 `--report` metrics
(`nexa.voice_tts.metrics`). `on_tts_text` fires on the `TTSTextFrame` the
TTS service emits *after* the planner, so `TurnMetrics.text_chunks` now
records the post-planner phrases. Added this stage (measure-only):

- `TurnMetrics.tiny_text_chunk_count` — `TTSTextFrame`s shorter than
  `TINY_TEXT_CHUNK_CHARS = 12` (the `"1."` / `"np."` / `"tzw."`
  fragmentation symptom). Goal: **0**.
- `TurnMetrics.mean_text_chunk_chars`.
- `to_dict()` gains a `text_to_tts` block (`chunk_count`,
  `mean_chunk_chars`, `tiny_chunk_count`, `tiny_chunk_threshold_chars`);
  `render_turn_report` shows `mean chars` + `tiny (<12)` on the
  `TEXT -> TTS` line.

Compared against the operator's M2.4B.1 baseline shape (R0013 §E — the
`tzw.` / `np.` bad-split turn):

| dimension | M2.4 baseline (SimpleTextAggregator) | M2.4B.2 (NexaSpeechPlanner) |
|---|---|---|
| phrase boundaries | English NLTK; Polish abbrevs over-split | Polish/EN-aware; abbrevs never split |
| pathological tiny chunks | `"…jest tzw."`, `"1."`, `"z … (np."` seen | **0** in the scripted run below |
| spoken Markdown | `**`, `1.`, `*Call of Duty*` audible | none reach Piper |
| list rendering | one clip per `\nN. item` | one prose phrase, `oraz` / `and` |
| true Piper `mean_http_rtf` | ~0.24 (R0014) | unchanged (~0.2–0.3) — planner adds no synthesis cost |
| number of text chunks | ~1 per sentence + fragments | ~1 per *natural phrase*, no fragments |
| intra-response silence / gaps | present (LLM starvation) | **still present — B.3 target, not B.2** |
| `audio_seconds_per_wall_second` | < 1 under contention | still < 1 — B.3 |

Scripted-run tallies (below): **11 phrases across 3 turns; 0 tiny chunks
(<12 chars); 0 spoken Markdown markers; 0 spoken list numbers; 2 comma
boundary releases** (long run-on sentences — expected under B.2's comma
policy, B.3 batching re-merges them).

## REAL HARDWARE TEST

Scripted end-to-end on this Pi 5 — real `gemma4:e4b` via Ollama, real
external Piper HTTP server, real `LocalAudioOutputTransport`, the M2.4
half-duplex gate, the M2.4B.1 `--report` metrics; only the microphone +
whisper.cpp were replaced by the three scripted operator transcripts.
Harness: `docs/research/m2_4b_speech_flow/m24b2_accept.py`, raw:
`docs/research/m2_4b_speech_flow/m24b2_accept_raw_20260907.txt`.

Prompts (verbatim from the brief):

1. `Wyjaśnij, czym jest tak zwany horyzont zdarzeń.`
2. `Podaj mi dwa przykłady strzelanek i jedną grę RPG.`
3. `Podaj przykład, na przykład czarnej dziury, i wyjaśnij to prosto.`

**Turn 1** — `gemma4:e4b` answered without the abbreviation "tzw." itself.
Canonical stored (unchanged): *"Horyzont zdarzeń to granica … Jest to punkt
bez powrotu.\n\nJeśli cokolwiek – czy to światło, czy materię – …"*
Phrases handed to Piper (post-planner):

1. `Horyzont zdarzeń to granica wokół bardzo masywnej obiektywu, jak czarna dziura.`
2. `Jest to punkt bez powrotu.`
3. `Jeśli cokolwiek – czy to światło, czy materię – przekroczy ten horyzont,`
4. `nie ma już żadnej siły, nawet ta najsilniejsza, która byłaby w stanie temu zapobiec i sprawić, że wróci do obserwatora.`
5. `Z perspektywy zewnętrznego obserwatora, wszystko, co za nim zniknie, nigdy nie dotrze do niego.`

Complete sentences; em-dash asides kept inline; `#3` is a comma release of
a long run-on sentence (no sentence end had arrived yet). No fragments, no
markup.

**Turn 2** — `gemma4:e4b` emitted a full Markdown numbered list + bolded
titles + a `*` bullet. Canonical stored **verbatim, untouched**:
`"Dwa przykłady strzelanek:\n\n1.  **Call of Duty:** … \n2.  **Battlefield:**
… \n\nGra RPG:\n\n*   **The Witcher 3: Wild Hunt:** …"`.
Phrases handed to Piper (post-planner):

1. `Dwa przykłady strzelanek:`
2. `Call of Duty: Seria znana z dynamicznej rozgrywki FPS (First-Person Shooter) oraz Battlefield:`
3. `Często kojarzony z większymi bitwami i pojazdami, również jest strzelanką.`
4. `Gra RPG: The Witcher 3: Wild Hunt: Świetnie napisana gra fabularna z otwartym światem, gdzie wcielasz się w Geralta z Rivii.`

**`1.` / `2.` / `*` markers and every `**` are gone**; the two shooter
items are joined with **`oraz`**; parentheses kept inline; the RPG bullet
became prose. No "one dot / two dot", no stars.

**Turn 3** — canonical stored (unchanged): `"Przykładem jest **czarna
dziura**.\n\nWyjaślenie proste: To obszar w kosmosie … że nic – nawet
światło – … Wyobraź sobie, że to kosmiczny \"próżnia\" …"`.
Phrases handed to Piper (post-planner):

1. `Przykładem jest czarna dziura.`
2. `Wyjaślenie proste: To obszar w kosmosie, gdzie grawitacja jest tak niesamowicie silna,`
3. `że nic – nawet światło – nie jest w stanie się z niej wydostać.`
4. `Wyobraź sobie, że to kosmiczny "próżnia" o ekstremalnie potężnej sile przyciągania.`

`**czarna dziura**` → `czarna dziura` (period kept). `#2` is a comma
release (long lead-in with a colon). The period after the closing `"`
correctly ended `#4` (quote depth balanced — not split mid-quote). The
model didn't emit `np.`; had it, the boundary rules keep it attached.

(`gemma4:e4b`'s own spelling wobbles — "obiektywu", "Wyjaślenie",
`"próżnia"` gender — are the model's, not the planner's; NeXa never
rewrites content.)

Listening checklist (from the brief), against the scripted run:

| check | result |
|---|---|
| no isolated `"tzw."` | PASS — never emitted alone (boundary rule; model didn't use it here) |
| no isolated `"np."` | PASS — expanded to `na przykład`; never a standalone chunk |
| no spoken Markdown stars | PASS — every `**` / `*` stripped before Piper (turns 2 & 3) |
| no "one dot / two dot" artifacts | PASS — `1.` / `2.` / `*` bullets never reached Piper (turn 2) |
| list content sounds coherent | PASS — list → prose with `oraz`, parentheses inline |
| no semantic content lost | PASS — every item name / clause preserved; only markers removed |
| same canonical assistant answer still stored | PASS — `ConversationSession.history` holds the raw model text incl. `**bold**`, `1.`, `\n\n` — byte-identical to model output |
| PL voice correct | PASS — `pl_PL-gosia-medium` selected via the canonical language fn (bridge, unchanged) |
| self-talk still fixed (half-duplex gate untouched) | PASS — `HalfDuplexGate` + bridge lifecycle notifications unchanged; planner never touches the gate |
| long intra-response silence | still present (turn 1 had ~8–12 s inter-phrase gaps) — **known B.3 target, not a B.2 failure** |

**The in-person microphone acceptance remains the operator's gate**, as
for every prior M2 stage. This scripted run is engineering evidence that
the planner behaves correctly against real model output and real
synthesis.

## TEST RESULTS

- `tests/test_voice_tts_speech_planner.py` — **45 passed**. Covers the
  brief's numbered list:
  - Polish 1–10: `tzw.` not split; `np.` expand + no break; comma
    `na przykład,` not split; `itd./itp./m.in.`; decimal `3.5`; quoted
    span; parentheses; colon; semicolon; comma only on a long clause; no
    tiny `"1."`/`"2."`.
  - Markdown 11–16: bold / italic markers not spoken; numbered-list
    markers gone (list → prose); bullets not spoken; headings cleaned;
    inline + fenced code safe. Plus the R0012 list-to-prose example, the
    EN connector, "never raises", and the streaming list-hold.
  - Streaming 17–21: incremental tokens combine; final flush exactly once;
    no duplicate text; no empty / formatting-only frames; reset clears
    prior state. Plus: Start/End forwarded; `LLMTextFrame` consumed not
    forwarded; unknown frames pass through; language tracked from the
    settings frame; emitted frames are `AggregatedTextFrame` /
    `aggregated_by=sentence`; hard cap on a run-on.
  - Architecture 22–30: no session/history reference; source copy
    byte-identical; no model-provider / session import or call; no
    response-language authority; no `renice`/`taskset`/`length_scale`/
    `sleep`/`buffered_audio`/`BufferEstimate`/filler/barge-in tokens;
    imports only stdlib + pipecat.
  - English 31–32 (+2): EN sentence boundaries; `e.g./i.e.` no false
    boundary; `Dr./Mr.` titles don't break; language-neutral Markdown.
- `tests/test_voice_tts_metrics.py` — +2 tests for
  `tiny_text_chunk_count` / `mean_text_chunk_chars` / the `text_to_tts`
  dict block. **69 passed.**
- Full suite: **`pytest` 360 passed, 7 skipped, 14 subtests** (was 358).
  **`python -m unittest discover -s tests` — 367 OK.**
- `ruff check src/ tests/ apps/ scripts/setup_piper_http.py` — clean.
- `git diff --check` — clean.

## FILES CHANGED

| file | change |
|---|---|
| `src/nexa/voice_tts/speech_planner.py` | **new** — `NexaSpeechPlanner` + `normalize_for_speech` + `find_phrase_cut` + the PL/EN abbreviation sets/expansions |
| `src/nexa/voice_tts/__init__.py` | export `NexaSpeechPlanner`, `normalize_for_speech`, `find_phrase_cut` |
| `apps/nexa_voice_tts_probe.py` | insert `NexaSpeechPlanner` between the bridge and the TTS service in `extra_output_stages` (always on) |
| `src/nexa/voice_tts/metrics.py` | measure-only: `TINY_TEXT_CHUNK_CHARS`, `TurnMetrics.tiny_text_chunk_count` / `mean_text_chunk_chars`, `to_dict` `text_to_tts` block, one `render_turn_report` line |
| `tests/test_voice_tts_speech_planner.py` | **new** — 45 deterministic tests |
| `tests/test_voice_tts_metrics.py` | +2 tests for the new tiny-chunk metrics |
| `docs/reports/R0015_m2_4b_2_speech_planner_20260907.md` | **new** — this report |
| `docs/research/m2_4b_speech_flow/m24b2_accept.py` + `m24b2_accept_raw_20260907.txt` | **new** — scripted hardware-acceptance harness + raw output |
| `docs/research/m2_4b_speech_flow/README.md` | M2.4B.2 additions section |
| `docs/CURRENT_STATE.md` | M2.4B.2 status + `M2.4B.3` next-task |

Not changed: `nexa.tts`, `nexa.voice`, `nexa.stt`, `nexa.voice_conversation`,
`nexa.conversation`, `nexa.providers`; `pyproject.toml`; the frozen M2.4
speech path (the planner is additive; with it removed from
`extra_output_stages` the pipeline is byte-for-byte M2.4). No voice model,
no model binary, no CPU scheduling, no B.3 pacing, no M2.5.

## COMMIT HASH

One coherent local commit — `feat: add speech-aware TTS text planning`
(the tip of `main`; see `git log -1`). Contents:
`src/nexa/voice_tts/speech_planner.py` (new) + `__init__.py` +
`metrics.py`, `apps/nexa_voice_tts_probe.py`,
`tests/test_voice_tts_speech_planner.py` (new) +
`tests/test_voice_tts_metrics.py`, this report, the scripted research
harness + raw, `docs/CURRENT_STATE.md`,
`docs/research/m2_4b_speech_flow/README.md`.

## GIT STATUS

Branch `main`, working tree clean, **7 commits ahead of `origin/main`, not
pushed**. No voice model / model binary / audio file added; no CPU
scheduling policy; no B.3 pacing; no M2.5. `git diff --check` clean.

```
* feat: add speech-aware TTS text planning          ← this stage (tip of main)
  24d88ee docs: record M2.4B.1A CPU scheduling spike
  42591f5 fix: correct realtime speech flow metrics
  509da2d feat: add realtime speech flow instrumentation
  f8c3964 docs: record M2.4B speech flow research
  36ec1e4 feat: add local streaming speech output   ← frozen M2.4 baseline
```

## NEXT STAGE

**M2.4B.3 — look-ahead / buffered-audio refill controller** (R0012 §
"LOOK-AHEAD", R0014 recommendation): key synthesis off
`buffered_audio_seconds`; phrase 1 out immediately, batch-and-hold the
rest, release on `buffered_audio_s ≤ target` or generation done; raise
`stop_frame_timeout_s` to ~8–10 s; `renice -n 10` the Piper process
(R0014); re-validate the buffer estimate as a control signal on a real
bursty turn. This is where the **long intra-response silence** is
addressed. Parallel: Ollama `keep_alive` for the first-token eviction
stalls. Then **M2.5 — barge-in** (replaces the temporary half-duplex
gate).

## REMAINING GAPS — EXPLICITLY DEFERRED TO B.3

- **Long silence inside one assistant response.** B.2 makes each phrase
  natural; it does not make them *arrive* fast enough. `gemma4:e4b` at
  ~1.3–3 tok/s under load still starves the audio buffer between phrases
  (R0014). Deferred to B.3's buffered-audio controller.
- **`BotStopped`/`BotStarted` churn** from the 3 s `stop_frame_timeout_s`
  when the LLM stalls mid-reply — B.3 raises the timeout.
- **First-audio latency** (whole first phrase must generate before any
  sound) — B.3 protects it by emitting phrase 1 immediately; the LLM
  first-token track is separate.
- **Tunable values** (`MIN_SENTENCE_CHARS` etc., connector words, the
  abbreviation lists) are CANDIDATE — operator A/B in B.3/B.4 may adjust
  them; nothing here is frozen as product truth.
- **A tiny sentence-final fragment** (e.g. a 6-char `"Koniec."`) can still
  be flushed on its own at end-of-turn — it cannot merge backward into an
  already-emitted phrase. B.3 batching absorbs it.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

Framework-reuse-first honoured (Pipecat `AggregatedTextFrame` intake, the
`LLMTextProcessor`-shaped role; `MarkdownTextFilter` evaluated and the
reason for a NeXa normalizer recorded). Evidence discipline held
(deterministic tests per rule; the scripted hardware run's limitations
disclosed; the operator's mic acceptance still the gate). No gap exposed.
