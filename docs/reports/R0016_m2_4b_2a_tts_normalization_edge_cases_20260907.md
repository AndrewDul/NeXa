# R0016 — M2.4B.2A: TTS text-normalization edge-case fix

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.2A — corrective
  fix on top of M2.4B.2 (`R0015`)**
- **Related:** `docs/reports/R0015_m2_4b_2_speech_planner_20260907.md`
  (the speech planner this corrects — commit `d30f871`),
  `docs/reports/R0014_m2_4b_1a_cpu_scheduling_spike_20260907.md`
  (long intra-response silence = B.3, confirmed again here),
  `/tmp/nexa_m24b2_operator.jsonl` + `/tmp/nexa_m24b2_operator_console.txt`
  (the real operator microphone run that exposed the two issues).

**Scope: two narrow TTS-text-normalization corrections found in the real
operator run. No pacing, no `renice`, no `buffered_audio_seconds`, no
`stop_frame_timeout_s` change, no look-ahead batching, no speech-speed
change, no fillers, no M2.5. `ConversationSession` / canonical assistant
text / history are unchanged — proven again.**

---

## TASK RESULT

**PASS.**

1. **LaTeX/math markup no longer reaches Piper.** `normalize_for_speech`
   gains `_strip_math`: `$…$`, `$$…$$`, `\(…\)`, `\[…\]`, and bare
   `\text{…}` / `\mathrm{…}` / … are unwrapped to readable text; sub/
   superscript markers and bare `\commands` are dropped; equations are
   never *interpreted*. `Wodór ($\text{H}$)` → `Wodór (H)`; `$E = mc^2$` →
   `E = mc2`.
2. **The apparent "partial word" / "open paren" chunks are the model, not
   the planner.** Root cause traced to `GenerationOptions.num_predict =
   200` (`src/nexa/providers/base.py:54`, an M1/M2.4-baseline setting):
   `gemma4:e4b` hit the 200-token cap mid-word (`…życia gwiaz`) and right
   after an open paren (`…osobliwości (`). The planner's boundary logic
   **cannot** cut inside a word (all cuts are at `. ! ? … ; : ,` or a
   `str.rfind(" ")` space); the fragments came from the **final flush**
   emitting the truncated buffer verbatim. Fix: the flush (and every
   emitted phrase) now runs `_tidy_spoken` — it trims a *dangling trailing
   opener* the source left (`(`, `[`, `{`, `„`, `«`, a bare `–`/`-`) from
   the **spoken** copy only, and skips a phrase that has no alphanumeric
   character. A genuine mid-word partial (`gwiaz`) is **kept** — transcript
   truth, no invented completion, no silent deletion of content.
3. **B.2 successes preserved** (re-tested): `np. → na przykład`, no
   isolated `tzw.`, `tiny_text_chunk_count = 0`, Markdown/list cleanup,
   transcript invariant, PL/EN.
4. As a side effect of investigating #2, `_join_items` was corrected:
   multi-sentence list items are now joined as **plain consecutive
   sentences** (markers dropped, each keeps its full stop) instead of with
   `" oraz "` — the operator run had glued two paragraphs with "oraz"
   (`…w hel oraz Hel ($\text{He}$):`). Short name-lists still use
   `oraz` / `and` (`Call of Duty oraz Battlefield.`).
5. **Tests:** `tests/test_voice_tts_speech_planner.py` 45 → **59** (14 new
   B.2A). Full suite **`pytest` 374 passed / 7 skipped / 14 subtests**;
   **`unittest discover` 381 OK**; `ruff` clean; `git diff --check` clean.

**B.2 is NOT yet marked `OPERATOR-CONFIRMED`** — it waits on a fresh
microphone run confirming these corrections.

## ROOT CAUSE OF LATEX LEAK

`R0015`'s `normalize_for_speech` handled Markdown (`**`, `*`, `#`, `` ` ``,
`|`, lists, links) but had **no math handling**. `gemma4:e4b` writes
element symbols as inline LaTeX inside bolded list titles. From the real
run (turn 5, `/tmp/nexa_m24b2_operator_console.txt` ~line 715), the
canonical assistant text was:

```
1.  **Wodór ($\text{H}$):** Jest to najobficzej występujący pierwiastek …
2.  **Hel ($\text{He}$):** Jest to drugi pod względem obfitości pierwiastek …
```

`_strip_emphasis` removed `**`, `_normalize_lists` removed `1.` / `2.`, but
`$\text{H}$` / `$\text{He}$` passed straight through to Piper:

```
Generating TTS [Wodór ($\text{H}$): Jest to najobficzej występujący pierwiastek …]
Generating TTS [… gdzie wodór jest przekształcany w hel oraz Hel ($\text{He}$):]
```

`INFERENCE`: Piper/espeak-ng would vocalise `$`, `\text`, `{`, `}`
literally or skip them awkwardly. Not acceptable spoken text.

## LATEX NORMALIZATION FIX

`_strip_math(text)` — runs in `_normalize` immediately after inline-code
removal (so it never touches code), before links/headings/emphasis/lists.

| input | handling | result |
|---|---|---|
| `$…$`, `$$…$$`, `\(…\)`, `\[…\]` | unwrap the span; reduce the contents with `_despan_math` | contents as readable text |
| `\text{X}` `\mathrm{X}` `\mathbf{X}` `\operatorname{X}` … (in or out of a span) | keep the argument | `X` |
| other `\cmd{arg}` | keep the argument | `arg` |
| bare `\cmd` (`\alpha`, `\cdot`, `\left`, `\,`) | **dropped** — never turned into a word (no invented reading) | *(removed)* |
| `{` `}` | dropped | |
| `^` `_` (sub/superscript markers) | dropped | `mc^2` → `mc2` |
| leftover unmatched `$` / `\(` / `\[` … | dropped | |

Verified spoken forms (deterministic tests):

- `**Wodór ($\text{H}$):**` → `Wodór (H):` — the symbol is kept (useful,
  and `(H)` reads cleanly); the brief's "just `Wodór`" was allowed but
  keeping `(H)` loses nothing.
- `$\text{He}$` → `He`
- `$E = mc^2$` → `E = mc2`
- `$H$` / `$He$` → `H` / `He`
- `\( a + b \)` → `a + b`; `\[ x = y \]` → `x = y`
- `\text{Fe}` → `Fe`
- `$a \cdot b + \alpha$` → `a b +` (markup stripped, nothing invented)
- pure junk (`$`, `$$`, `\(`, `${}$`, `$^_{}$`) → never raises, returns a
  string.

**Not interpreted:** no `\cdot` → "times", no `\alpha` → "alpha", no
equation evaluation. "If arbitrary math cannot be normalized safely, strip
formatting while preserving readable textual content" (the brief) — the
readable words survive; the symbols/operators are dropped.

Transcript unaffected — `_strip_math` only runs inside `normalize_for_speech`,
which only ever produces the `AggregatedTextFrame` copy.

## ROOT CAUSE OF APPARENT PARTIAL WORDS

The two flagged chunks, from the real run:

```
turn 3:  [Wszystko, co za horyzontem, jest skazane na nieuchronne zapadnięcie się do osobliwości (]
turn 5:  [które zostały wytworzone w wcześniejszych etapach życia gwiaz]
```

Traced against `/tmp/nexa_m24b2_operator.jsonl` + console:

| candidate | verdict |
|---|---|
| **A — `gemma4:e4b` finished mid-token / mid-paren** | **CONFIRMED — this is the cause.** Turn 3 `chars: 651`, turn 5 `chars: 668` — each ≈ **200 generated tokens** = `GenerationOptions.num_predict = 200` (`src/nexa/providers/base.py:54`, unchanged since M1). Ollama returned its `done` chunk mid-word (`…życia gwiaz`) / right after `(`. The assistant-text stream from `ConversationSession` genuinely ended there. |
| B — provider/session stream termination bug | No. `grep` of the console shows **no `[error]`, no `Traceback`, no `KeyboardInterrupt`, no "conversation failed", no "flushed … unfinished"** for these turns. `on_assistant_complete` fired normally; turn-5 chunk #7 is tagged `(after gen complete)` — a clean final flush. |
| C — `NexaSpeechPlanner` hard-cap (`MAX_PHRASE_CHARS`) cut inside a word | No. The cap uses `s.rfind(" ", 0, max_phrase)` — it can only cut at a space. Both fragments are far under 240 chars anyway. Proven by `test_a8`. |
| D — final-flush logic | **Partly** — the flush (`_pending_phrases(final=True)`) *emitted* the truncated buffer verbatim, which is correct ("the source ends here"), except that a **dangling trailing `(`** should not be spoken. Fixed (below). |
| E — stdout/log interleaving only | No — the strings appear inside real `_push_tts_frames` "Generating TTS […]" log lines, i.e. they are the actual `run_tts` input. |
| F — other | None found. |

**The planner did not create a partial word.** `find_phrase_cut` returns a
cut index only at `. ! ? … ; :` , at a `,` (long clause), or at
`str.rfind(" ")` for the hard cap — every one of those is a punctuation or
whitespace boundary. It is structurally impossible for it to split inside
a word.

The `num_predict = 200` cap itself is **out of scope for B.2A** — it is an
M1/M2.4-baseline model-serving setting, not something the speech planner
introduced or should change. Raising it (or handling graceful truncation)
is a separate consideration for the LLM-serving / B.3 track.

## PLANNER BOUNDARY / FLUSH FIX

`_tidy_spoken(text)` — applied to every emitted phrase (streaming loop
*and* the final flush); operates on the **spoken copy only**, never on
`_emitted` (which stays on the raw normalized text for streaming
prefix-stability), never on the transcript:

- strip a trailing run of whitespace + dangling openers / bare dashes:
  `( [ { „ « ‹ < – — -`. So `"…do osobliwości ("` → `"…do osobliwości"`.
- strip one unbalanced trailing `"`.
- **keep** `. ! ? … , ; :` and closing `) ] }`.
- after tidying, a phrase with no alphanumeric char is not emitted — so a
  bare `"("` chunk can never be produced.

**Correct TTS behaviour for a genuinely mid-word source truncation:** the
planner speaks exactly what it was given (`"…życia gwiaz"`), letting Piper
pronounce the partial word. It does **not** try to complete it (that would
invent content) and does **not** drop it (that would erase content the
transcript keeps). `ConversationSession.history` keeps the truncated text
verbatim — transcript truth. This is documented and covered by
`test_a9` / `test_a10`.

End-to-end re-check on the exact operator inputs (streaming, 3-char
tokens, through the real `NexaSpeechPlanner`):

```
turn 5 → 8 phrases, none containing $ or \text, "1."/"2." gone, "**" gone,
         the two multi-sentence items joined as plain sentences
         ("…w hel. Hel (He): Jest to drugi…"), "…życia gwiaz" kept verbatim.
turn 3 → 1 phrase: "Wszystko, co za horyzontem, … do osobliwości"  (no trailing "(")
```

## TRANSCRIPT INVARIANT

Unchanged and re-proven:

- `_strip_math` / `_tidy_spoken` run only inside `normalize_for_speech` /
  the planner's emit path — the `AggregatedTextFrame` copy, never the
  source.
- `planner._raw == "".join(source_tokens)` byte-for-byte after a streamed
  reply that contains `**bold**` + `$\text{H}$` (`test_a14`).
- `speech_planner.py` still imports only `re` / `loguru` / `pipecat`;
  constructs no session/provider/persona; defines no language classifier.
- `ConversationSession.history` from the operator run stored the **raw**
  model text — `**Wodór ($\text{H}$):**`, `1.`, `\n\n` — unmodified.

## REAL B.2 HARDWARE FINDINGS

From `/tmp/nexa_m24b2_operator.jsonl` (5-turn real microphone run,
2026-09-07 15:03–15:10):

| turn | diagnosis | text chunks | `tiny_chunk_count` | mean `http_rtf` | max silence gap | CPU mean / peak |
|---|---|---|---|---|---|---|
| 1 | FIRST TOKEN | 4 | **0** | 0.244 | 7.1 s | 64.7 % / 100 % |
| 2 | LLM TEXT PRODUCTION | 5 | **0** | 0.254 | **19.6 s** | 98.7 % / 100 % |
| 3 | LLM TEXT PRODUCTION | 9 | **0** | 0.294 | 7.7 s | 93.4 % / 100 % |
| 4 | LLM TEXT PRODUCTION | 6 | **0** | 0.300 | **13.5 s** | 99.3 % / 100 % |
| 5 | LLM TEXT PRODUCTION | 8 | **0** | 0.280 | **39.1 s** | 90.7 % / 100 % |

Confirmed:

- **B.2 removed the fragmentation it targeted:** `tiny_text_chunk_count = 0`
  on every turn; no isolated `1.` / `np.` / `**`; `np. → na przykład`
  spoken correctly (console line ~134).
- **True Piper HTTP RTF stayed ~0.24–0.30** — synthesis is still ~3–4×
  real time; the planner adds no synthesis cost.
- **Long intra-response silence remains** — max gaps **≈ 19.6 s, ≈ 13.5 s,
  ≈ 39.1 s**; `audio_seconds_per_wall_second` 0.52–0.61 (pipeline runs
  dry); turns 2–5 all `diagnose = LLM TEXT PRODUCTION`; **CPU peaks 100 %,
  mean up to 99.3 %**; temp 64–68 °C, `throttled=0x0` (no thermal
  throttle). This is exactly the R0014 picture: LLM text starvation under
  CPU contention. **B.3 (look-ahead / buffered-audio refill + Piper
  `renice`) remains the correct next stage** after this cleanup.
- Two new normalization gaps (LaTeX, dangling `(`) — fixed here.

## STT ISSUE DEFERRED

The same run shows a **separate STT-quality problem** (not touched in
B.2A): whisper.cpp repeatedly mistranscribed the Polish phrase
*"horyzont zdarzeń"* as, across turns:

- `"chory zęzdarzyń"` (turn 1)
- `"choryząt zdarzeń"` (turn 2)
- `"choryząc dażem"` (turn 3)

The assistant then reasonably answered *"there is no recognised condition
called that"* — a downstream effect of the bad transcript, **not** a
conversation or TTS bug. Flagged as a **separate future quality track**
(STT model / prompt / Polish acoustic handling). **Not fixed in B.2A.**

## TEST RESULTS

- `tests/test_voice_tts_speech_planner.py` — **59 passed** (45 B.2 + 14
  B.2A):
  - `test_a1`–`test_a5` — `$\text{H}$` / `$\text{He}$` never reach Piper;
    inline `$…$` / `\(…\)` / `\[…\]` / bare `\text{}` removed safely;
    `_strip_math` never interprets an equation; never raises on math junk.
  - `test_a6`–`test_a12` — no standalone `"("` chunk; `_tidy_spoken` trims
    only dangling openers/dashes (keeps `. , ; :` and `)`);
    `MAX_PHRASE_CHARS` never cuts inside a word; mid-word truncation is
    preserved not completed; final flush neither duplicates nor invents;
    long multi-sentence list items are not glued with "oraz"; short
    name-lists still use "oraz".
  - `test_a13`–`test_a14` — B.2 successes intact (`np.` expansion, no
    isolated `tzw.`); source copy byte-identical with math markup present.
- `tests/test_voice_tts_metrics.py` — 69 passed (unchanged).
- Full suite: **`pytest` 374 passed, 7 skipped, 14 subtests**;
  **`python -m unittest discover -s tests` 381 OK, 7 skipped**.
- `ruff check src tests apps scripts/setup_piper_http.py` — clean.
  `git diff --check` — clean.

## FILES CHANGED

| file | change |
|---|---|
| `src/nexa/voice_tts/speech_planner.py` | `_strip_math` + `_despan_math` (new); `_MATH_SPAN_RES` / `_MATH_TEXT_CMD_RE` / `_DANGLING_TAIL` / `_LIST_PROSE_MAX_ITEM_CHARS` consts; `_strip_math` call added to `_normalize`; `_tidy_spoken` (new) applied in `_pending_phrases` (stream + flush); `_join_items` reworked (short name-list → "oraz"; long/multi-sentence items → plain sentences) |
| `tests/test_voice_tts_speech_planner.py` | +14 B.2A tests (`TestLatexMathNormalization`, `TestTruncationTailCleanup`, `TestB2SuccessesNotRegressed`) |
| `docs/reports/R0016_m2_4b_2a_tts_normalization_edge_cases_20260907.md` | **new** — this report |
| `docs/reports/R0015_m2_4b_2_speech_planner_20260907.md` | note the B.2A follow-up; B.2 not yet `OPERATOR-CONFIRMED` |
| `docs/CURRENT_STATE.md` | B.2A recorded; next stage still M2.4B.3 |
| `docs/research/m2_4b_speech_flow/README.md` | B.2A findings note |

Not changed: `nexa.tts`, `nexa.voice`, `nexa.stt`, `nexa.voice_conversation`,
`nexa.conversation`, `nexa.providers`; `pyproject.toml`; the frozen M2.4
path. No `num_predict` change, no CPU scheduling, no B.3 pacing, no
`stop_frame_timeout_s`, no M2.5. The planner is still additive — remove it
from `extra_output_stages` → byte-for-byte M2.4.

## COMMIT HASH

One local commit on top of `d30f871` — `fix: normalize LaTeX math + trim
truncation tails in speech planner` (tip of `main`; see `git log -1`).

## GIT STATUS

Branch `main`, working tree clean, **8 commits ahead of `origin/main`, not
pushed**. No voice model / model binary / audio file added. `git diff
--check` clean.

## NEXT STAGE

**M2.4B.3 — look-ahead / buffered-audio refill controller** (R0012
"LOOK-AHEAD", R0014 recommendation). Where the long intra-response silence
(the ≈ 19.6 / 13.5 / 39.1 s gaps confirmed above) is actually addressed:
key synthesis off `buffered_audio_seconds`; phrase 1 immediately,
batch-and-hold the rest; `renice -n 10` the Piper process; raise
`stop_frame_timeout_s` to ~8–10 s; re-validate the buffered-audio estimate
as a control signal first; sweep `target` with the `--report` profiler.
Parallel: Ollama `keep_alive` for first-token eviction; and — its own
track — the STT-quality issue above; and, if the operator wants replies to
finish rather than truncate at 200 tokens, a `num_predict` review.

Then **M2.5 — barge-in** (replaces the temporary half-duplex gate).

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

The two issues were traced to primary sources (the operator JSONL +
console, the `num_predict` constant) before any code changed; the
model-truncation cause was not blamed on the planner; the fix is the
smallest that closes the gap; the STT issue was flagged, not silently
folded in. No gap exposed.
