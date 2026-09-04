# R0003 — M1.0B Current Small-Model Sweep

- **Date:** 2026-09-01 (sweep opened + Phase 1); recovery, Phase 2, and
  finalisation completed 2026-09-02. Filename keeps the `20260901` stamp — that
  is when the milestone was opened.
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M1 — Natural Text Conversation · **Substage M1.0B — Current
  Small-Model Sweep** (research + empirical benchmark only; no product code)
- **Related:** `docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md` (primary
  evidence doc), `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md` (M1.0
  baseline — historical, not rewritten),
  `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md`,
  `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md`,
  `docs/reports/R0002_m1_natural_conversation_research_20260831.md`,
  raw data under `docs/research/m1_bench_results/m1_0b/`

---

## TASK RESULT

**PASS (with one disclosed limitation).** M1.0B is a research + benchmark
milestone; no product architecture was implemented. All seven task steps
completed:

1. **Recovered and scored existing results** — all 8 Phase 1 models verified
   valid; agent-assisted quality scores produced against the existing
   transcripts; no transcript altered.
2. **Finished the missing Phase 1 tests** — none needed re-running. The
   hand-off audit's "missing" list (`gemma4:e4b`, Bielik Q8_0, Bielik Q4_K_M)
   was already completed cleanly on 2026-09-02 (`phase1_resume_log.txt`); this
   was verified file-by-file, not assumed.
3. **Completed the Phase 1 ranking** — full table + explicit BEST/FASTEST/
   DISQUALIFIED calls + automatic finalist selection.
4. **Ran Phase 2 on the finalists** — `gemma4:e4b` (pre-existing, validated),
   `gemma4:e2b` and `qwen3.5:2b` (run this session): `CONV-MIX-1`,
   `CONV-NATURAL-S1`, `CONV-HONESTY-S1`, persona A/B, standardized-vs-recommended,
   long-context decay, weighted NeXa score, final head-to-head.
5. **Documented everything** — `M1_0B_CURRENT_SMALL_MODEL_SWEEP.md` §11A–§19
   written; this report created; `CURRENT_STATE.md` updated; ADR-0002 amended
   (not status-changed); operator blind test updated with the real finalists.
6. **Verified** — foundation tests pass; `git diff --check` clean; no secrets;
   no model weights/caches staged; raw evidence preserved; no benchmark process
   left running.
7. **Committed** — one local commit; **not pushed**.

**Disclosed limitation:** `Ollama vs. llama.cpp` head-to-head is still
**NOT RUN** (Ollama blob store is `0700`/`ollama`-owned; reading it needs `sudo`
into another service account — not treated as pre-authorised). The incumbent
`qwen3:4b-instruct` was carried into the head-to-head on its Phase 1
`CONV-PL-S1` / `CONV-EN-S1` + perf data only; it was not put through the full
Phase 2 battery, per the operator's explicit two-phase scope.

All quality scores are **`AGENT-ASSISTED`** (Claude vs. transcripts + the
`M1_NATURAL_CONVERSATION_BENCHMARK.md` §4 rubric). The operator blind test is
the owed confirmation and is **prepared, not run**.

---

## What was recovered

The previous M1.0B run was interrupted twice: once mid-`gemma4:e4b` during the
original Phase 1 (`phase1_log.txt`, memory pressure — a 9.6 GB tag loading with
~3.0 GB free), and once mid-`gemma4:e2b` during Phase 2 (`phase2_log.txt`).
Between those, a `phase1_resume_log.txt` run on 2026-09-02 had already completed
all three "missing" Phase 1 candidates in isolation.

This session's recovery audit (`M1_0B` doc §11A):

- **Phase 1 — complete for all 8 models.** Every `CONV-*` JSON has the expected
  turn count and zero empty completions except the genuine Bielik Q4_K_M EN
  failure. Every `perf_*` JSON has 3 populated runs. `gemma4:e4b` ran stably
  alone on its resumed attempt (peak 68.8 °C, `throttled=0x0`, swap 0). **No
  Phase 1 model was re-run** — all raw files and transcripts reused unchanged.
- **Phase 2 — `gemma4:e4b` battery pre-existing and valid** (6 sessions:
  MIX/NATURAL/HONESTY + persona A/B + standardized PL). `gemma4:e2b` and
  `qwen3.5:2b` batteries **run this session** with a hardened driver
  (`scripts/m1_bench/run_phase2_resume.sh` — explicit `ollama stop` between
  models, MemAvailable<1.5 GB / swap>512 MB abort guard). No guard trip; swap
  stayed 0 MB; peak 69.4 °C; no throttling.

---

## Phase 1 full results

Perf: cold + 3× warm, ~200 tok, fixed EN prompt, `--bare-options`. Conversation
scores: `AGENT-ASSISTED` vs. transcripts.

| Model | PL | EN | gen tok/s | warm TTFT | cold load | peak °C | model RAM | reliability | notes |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3:4b-instruct` (control) | 3/5 | 3.5/5 | 2.96 | 0.37 s | 13.4 s | 68.8 | ~3.9 GB | solid | emoji-heavy, verbose, some 200-cap truncation, ctx speed decays to ~1.9 tok/s |
| `qwen3.5:4b` | 2/5 | 4/5 | 3.03 | 0.45 s | 15.8 s | 67.8 | ~4.3 GB | ok | PL garbled words + hallucinated brands + self-contradiction; EN clean |
| `qwen3.5:2b` | 2/5 | **4.5/5** | 4.54 | 0.27 s | 12.7 s | 68.3 | ~3.6 GB | ok | best EN in roster; PL garbled + a Chinese-character leak |
| `phi4-mini:3.8b` | 1.5/5 | 2.5/5 | 4.12 | 0.27 s | 12.7 s | 69.4 | ~4.0 GB | ok | PL non-sequitur (an "adult toys" list on "zmieńmy temat") + persona break; EN boilerplate + a recall error |
| `gemma4:e2b` | 3.5/5 | 4/5 | **6.58** | 0.30 s | 25.3 s | 68.8 | ~7.4 GB | solid | fastest; terse; one "one-sentence" instruction miss (P1) |
| `gemma4:e4b` | **4/5** | **4/5** | 3.11 | 0.72 s | 32.4 s | 68.8 | ~10.1 GB | solid | cleanest transcript both languages; heavy RAM |
| Bielik Q8_0 (temp-0.1 retest) | 2.5/5 | 1.5/5 | 2.04 | 8–13 s (conv) | 21.1 s | 69.4 | ~5.1 GB | poor TTFT | EN opens with a non-sequitur + overclaims ("I'll call for you") + wrong recall; shallow/repetitive PL; TTFT never warms |
| Bielik Q4_K_M (3rd-party) | 2/5 | **1/5** | 3.77 | 5–7 s (conv) | 12.1 s | **73.2** | ~2.9 GB nominal | **FAIL** | 2 of 10 EN turns empty; rest a stuck "I would …" loop; wrong PL recall; hottest in roster |

## AGENT-ASSISTED quality scores

Phase 1 above (PL / EN columns). Phase 2 deep-dive (finalists):

| Finalist | CONV-MIX-1 | CONV-NATURAL-S1 | CONV-HONESTY-S1 |
|---|---|---|---|
| `gemma4:e4b` | 3.5/5 | **4.5/5** | **4.5/5** |
| `gemma4:e2b` | 3.5/5 | 4.0/5 | **4.5/5** |
| `qwen3.5:2b` | 2/5 | ~2.5/5 (EN ~3.5, PL ~1.5) | 2/5 |

Weighted NeXa score (weights: conversation 25 / PL 20 / EN 15 / latency 15 /
reliability 10 / factual restraint 10 / Pi practicality 5):

| | `gemma4:e2b` | `gemma4:e4b` | `qwen3:4b-instruct` | `qwen3.5:2b` |
|---|---|---|---|---|
| **Weighted total (/5)** | **4.0** | **3.9** | **3.2** | 3.4 † |

† `qwen3.5:2b`'s 3.4 is above the incumbent on English + low RAM alone; it is
**gated out** of the bilingual role by broken Polish + a honesty-probe
hallucination (see below).

## BEST OVERALL

**`gemma4:e4b`.** The only roster model with zero quality defects across both
full Phase 1 transcripts, and the strongest Phase 2 deep-dive (NATURAL 4.5,
HONESTY 4.5). Correct recall and instruction-following in both languages,
coherent disagreement handling, no garbling, no persona breaks. Cost: ~3.1 tok/s,
~10 GB resident, ~32 s cold load.

## BEST POLISH

**`gemma4:e4b`** (4/5) — grammatically clean *and* content-rich *and*
instruction-compliant. `gemma4:e2b` (3.5/5) close second. Both Bielik variants
are nominally native-grammar but shallow / repetitive / wrong-recall, which the
rubric scores as real quality defects; the temp-0.1 retest did not fix this.

## BEST ENGLISH

**`qwen3.5:2b`** (4.5/5) — most natural, warmest, most proactively useful
English of the roster, zero defects found, at the lowest RAM of any candidate.
`gemma4:e4b` / `gemma4:e2b` / `qwen3.5:4b` tie second at ~4/5.

## FASTEST

**`gemma4:e2b`** — 6.4–7.2 tok/s sustained (perf and conversation), roughly 2×
the rest of the credible field, and the only quality-passing candidate that
reaches the benchmark's "usable" ≥ 4 tok/s band.

## MOST NATURAL

**`gemma4:e2b`** — tersest, least "assistant-sounding" phrasing; the Phase 1
"one-sentence" instruction miss did **not** recur in Phase 2 (NATURAL T10
complied). `gemma4:e4b` is the *deepest* conversationalist but runs longer and
leans on emoji.

## BEST PI BALANCE

**`gemma4:e2b`** — best quality-per-resource once `qwen3.5:2b` is gated out for
broken Polish: ~90 % of `gemma4:e4b`'s conversation quality at ~2× tok/s,
~2.5 GB less RAM, the flattest long-context decay in either milestone
(−1 % over 18 turns vs. M1.0's −52 % for `qwen3:4b-instruct`), warm TTFT
~1–2 s. Caveat: ~7.5 GB resident and a 25 s one-time cold load.

## DISQUALIFIED MODELS

- **Bielik Q4_K_M (3rd-party, Second State)** — reliability failure: 2 of 10
  English turns are empty completions, the rest a stuck repetitive "I would …"
  template loop; wrong PL recall; ran hottest (73.2 °C).
- **Bielik Q8_0 (official)** — the M1.0B recommended-sampling (temp 0.1) retest
  does **not** fix the English non-sequitur/overclaim behaviour or the
  TTFT-never-warms anomaly; Polish is native-grammar but shallow and repetitive.
  Confirms ADR-0002 D4 rather than overturning it.
- **`phi4-mini:3.8b`** — a genuine off-topic non-sequitur and a persona break in
  Polish; generic boilerplate tone and a clear recall error in English. MIT
  license and good raw perf do not offset this.
- **`qwen3.5:4b`** — Polish is *more* broken than the `qwen3:4b-instruct`
  control it was meant to challenge (garbled words, hallucinated brand names,
  turn-to-turn self-contradiction). No criterion favours it.
- **`qwen3.5:2b`** — **demoted from finalist to English-only reference.** Phase 2
  confirmed broken Polish across every Polish session (garbled grammar, a failed
  recall turn in MIX, a mid-reply code-switch in NATURAL); the persona swap did
  not fix it. Its honesty probe **hallucinated two album titles** for a
  non-existent band before recovering. Retained only for the English-primary /
  lowest-RAM role.

Not disqualified — **`qwen3:4b-instruct`** (incumbent): reliable, Apache-2.0,
262k context, second-lightest RAM; simply no longer the quality leader, and it
has the worst long-context speed decay of the four head-to-head models.

## PHASE 2 FINALISTS

Selected automatically after Phase 1 on the operator's NeXa criteria (natural
conversation, PL, EN, latency, reliability, factual restraint, Pi practicality —
**not** tok/s or generic benchmark cleverness):

1. **`gemma4:e4b`** — best overall / best Polish.
2. **`gemma4:e2b`** — fastest / most natural / best Pi balance / lighter same-family alternative.
3. **`qwen3.5:2b`** — carried in to test whether the persona swap and the
   mix/honesty/long-context sessions would rescue its Polish. **They did not** —
   Phase 2 demoted it to the English-only role.

The incumbent `qwen3:4b-instruct` is the continuity anchor for the head-to-head
(Phase 1 data only).

## PHASE 2 RESULTS

- **Language switching (CONV-MIX-1):** both Gemma models switch PL↔EN cleanly
  and answer the cross-language recall correctly ("mąka żytnia i pszenna
  chlebowa"). `qwen3.5:2b` handles the English turns but its Polish turns are
  garbled and it **fails the cross-language recall turn** (lectures instead of
  answering).
- **Naturalness stress (CONV-NATURAL-S1, 18 turns):** `gemma4:e4b` 4.5/5 —
  correction handling, correct recall ×4, detail-scaling on request, epistemic
  honesty, plays along with hypotheticals. `gemma4:e2b` 4.0/5 — same strengths,
  terser, one mild "as an AI" persona break. `qwen3.5:2b` EN portions ~3.5/5
  (recall works in English), PL portions ~1.5/5 (misreads references, incoherent
  joke, code-switches mid-reply).
- **Honesty / hallucination (CONV-HONESTY-S1):** `gemma4:e4b` and `gemma4:e2b`
  both 4.5/5 — neither invents an album for the fictitious *Bassline Theory*,
  both correctly reject the "3-minute full charge" claim, both give a clean
  meta-honesty answer. `qwen3.5:2b` 2/5 — invents two album titles on turn 1.
- **Persona A/B:** persona choice materially affects the Gemma models' brevity
  and instruction-following — the compact native-Polish persona is best, the
  minimal "helpful assistant" persona worst (verbose, emoji, effusive). For
  `qwen3.5:2b` the persona swap improves Polish *coherence* a little but not the
  non-native grammar.
- **Standardized vs. recommended sampling:** difference is small for the Gemma
  models (a little more length under standardized; same recall/instruction
  outcomes). Slightly *less* garbled for `qwen3.5:2b` under standardized. No
  ranking changes.
- **Long-context decay:** all three finalists degrade far less than M1.0's
  `qwen3:4b-instruct` (−1 % / −7 % / −12 % over 18 turns vs. −52 %).
  `gemma4:e2b` is essentially flat with warm TTFT < 2.5 s at ~1250 context
  tokens.
- **Ollama vs. llama.cpp:** NOT RUN (blocked, see disclosed limitation).

## FINAL MODEL RANKING (everyday bilingual NeXa)

1. **`gemma4:e2b`** — 4.0/5 weighted. Best practical balance; recommended M1.1
   baseline candidate.
2. **`gemma4:e4b`** — 3.9/5. Quality ceiling; "quality mode" when RAM/latency
   allow.
3. **`qwen3:4b-instruct`** — 3.2/5. Reliable incumbent; weaker PL/EN polish,
   emoji/verbose, worst long-context speed decay of the four.
4. **`qwen3.5:2b`** — 3.4/5 raw, **gated to English-only**.
5. `qwen3.5:4b` — no niche it wins.
6. Bielik Q8_0 — Polish-grammar reference only, undeployable at ~2 tok/s.
7. `phi4-mini:3.8b` — not recommended.
8. Bielik Q4_K_M (3rd-party) — unusable.

## BEST MODEL FOR EVERYDAY NEXA

**`gemma4:e2b`** — as the M1.1 baseline candidate (pending the operator blind
test), with **`gemma4:e4b`** as the quality-mode alternative. Both Apache-2.0,
both run comfortably on the Pi 5 16GB with no throttling.

> **UPDATE (2026-09-05, historical note — this report is not rewritten):**
> this was the `AGENT-ASSISTED`, weighted-score recommendation at the time.
> The operator blind test (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`
> §8) subsequently ranked `gemma4:e4b` above `gemma4:e2b` live, and the owner
> froze `gemma4:e4b` as the actual M1.1 local baseline —
> `docs/decisions/ADR-0002_text_conversation_foundation.md` Amendment 2. The
> analysis above remains accurate as of 2026-09-01/02; it is superseded as a
> *recommendation*, not as evidence.

## BEST MODEL FOR POLISH

**`gemma4:e4b`** (4/5), `gemma4:e2b` (3.5/5) second. Bielik Q8_0 is the
native-grammar reference but is not deployable (2 tok/s, 8k context,
shallow/repetitive answers).

## BEST MODEL FOR ENGLISH

**`qwen3.5:2b`** (4.5/5) — for English-primary deployments only. For bilingual
use, `gemma4:e4b`/`gemma4:e2b` at ~4/5 are the practical English pick because
their Polish is usable.

## FASTEST ACCEPTABLE MODEL

**`gemma4:e2b`** — 6.4–7.2 tok/s, warm TTFT ~1–2 s, the only quality-passing
candidate in the "usable" speed band. Every faster option in the roster fails on
quality or reliability.

## MODEL ROLE RECOMMENDATIONS

| Role | Model | Rationale |
|---|---|---|
| Everyday bilingual NeXa (M1.1 baseline candidate) | **`gemma4:e2b`** | best weighted score, best Pi balance, flattest long-context decay, Apache-2.0 |
| Quality mode | **`gemma4:e4b`** | roster-best conversation + Polish; use when ~10 GB RAM / ~3 tok/s is acceptable |
| English-primary / lowest RAM | **`qwen3.5:2b`** | best English, ~3.5 GB; **not** for Polish |
| Incumbent / safe fallback | **`qwen3:4b-instruct`** | reliable, Apache-2.0, 262k context; keep the provider swappable |
| Polish-grammar reference (not deployable) | Bielik Q8_0 | native grammar, but 2 tok/s / 8k ctx / shallow |
| Do not use | Bielik Q4_K_M (3rd-party), `phi4-mini:3.8b` | reliability failure / non-sequitur + recall error |

## R0003 STATUS

**Created** (this file).

## CURRENT_STATE STATUS

**Updated** — M1.0B recorded as complete; substage note, "what is partial", and
"exact next recommended task" revised to point at the operator blind test as the
last qualitative input before an M1.1 baseline model is frozen. M1.0 history
unchanged.

## ADR-0002 STATUS

**Amended, not status-changed.** A dated "M1.0B amendment" section records: D4
caveat (b) **resolved** (fair Bielik re-test does not flip the baseline);
caveat (a) **partially addressed** (better Polish persona lifted
`qwen3:4b-instruct` PL 2/5 → 3/5; compact PL persona measurably helps the Gemma
models); **new evidence** — `gemma4:e4b` / `gemma4:e2b` out-converse the
incumbent in both languages. D4's status line ("baseline — not frozen") is
unchanged: the model choice is flagged for the operator blind test, not flipped
unilaterally (the Gemma models did not exist at M1.0; the RAM/latency trade is
an owner call). No M1.0 history rewritten.

## TESTS

- `python3 -m unittest discover -s tests` → **OK** (foundation tests; updated
  required-files list to include the new M1.0B docs).
- `scripts/m1_bench/bench.py` — `ast.parse` clean; used for all measurements;
  stdlib-only, no dependency installed.
- `git diff --check` — clean (no whitespace errors, no conflict markers).
- No secrets; no model weights or `~/.ollama` caches staged; raw evidence under
  `docs/research/m1_bench_results/m1_0b/` preserved (transcripts unmodified).
- No benchmark process running at hand-off (`ollama ps` empty; both finalists
  `ollama stop`ped by the driver).

## GIT STATUS

One new local commit on `main`: `research: benchmark current NeXa conversation
models`. **Not pushed.** Working tree clean after commit.

## UNRESOLVED ITEMS

1. **Operator blind test** — prepared
   (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`, sealed A/B/C mapping),
   **not run**. It is the owed operator-confirmed pass before an M1.1 baseline
   model is frozen. All M1.0 + M1.0B quality scores remain `AGENT-ASSISTED`.
2. **Ollama vs. llama.cpp** head-to-head — still blocked (blob store `0700`,
   `sudo` into another service account not pre-authorised). Needs an explicit
   operator decision (grant read access, or `ollama` GGUF export).
3. **Incumbent not in the full Phase 2 battery** — `qwen3:4b-instruct` was
   anchored on Phase 1 data only, per the two-phase scope. If the head-to-head
   result is contested, run it through MIX/NATURAL/HONESTY too.
4. **`gemma4:e2b` cold load** — 25 s one-time; acceptable for a persistent Pi
   service, worth confirming in real M1.1 use with `keep_alive` tuned.
5. **Persona work** — the compact native-Polish persona is best of the three
   tested; M1.1 should iterate it further for whichever model is frozen.

## NEXT RECOMMENDED ACTION

Run `docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md` — the operator
(Andrzej) talks blind to the three finalists (`gemma4:e4b`, `qwen3.5:2b`,
`gemma4:e2b`, sealed as A/B/C) for ~10–15 min each, scores them, then the
mapping is revealed and compared against this report's agent-assisted ranking
and weighted score. That result is the final qualitative input before the M1.1
baseline model is settled.

**Do not begin M1.1** until the blind test is done and the baseline model is
chosen. M1.1 remains a separate, explicitly-started milestone.
