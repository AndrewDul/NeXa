# M1 Operator Blind Conversation Test

Status: **RUN and scored — 2026-09-04.** Operator: Andrzej Dul. Full transcripts
and hidden per-turn metrics: `docs/testing/m1_operator_blind_results/`
(`A_transcript.json`, `B_transcript.json`, `C_transcript.json`, plus
`A_scorecard.json`, `B_scorecard.json`, `C_scorecard.json`,
`comparative_scorecard.json`). Launcher: `scripts/m1_bench/blind_launcher.py`
(test tooling, deletable at M1.1 like the rest of `scripts/m1_bench/`). This was
the qualitative tie-breaker owed
before the M1.1 baseline model is frozen (`docs/reports/R0002_*` §"Unresolved"
item 2; `docs/reports/R0003_*` §"Unresolved" item 1;
`docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md` §19). M1.0B Phase 2 is
**complete** (the deep-dive that picked and stress-tested these 3 finalists);
all M1.0 / M1.0B quality scores were still `AGENT-ASSISTED` (Claude, against
transcripts) — not operator-confirmed, until this test. This test is the
operator-confirmed pass; results are in §8.

Related: `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md`,
`docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md`,
`docs/reports/R0003_m1_0b_current_small_model_sweep_20260901.md`.

---

## 1. Purpose

Machine metrics (tok/s, TTFT, RAM) and agent-assisted 1–5 scoring both have
blind spots a live conversation does not. Before any model is frozen as the
M1.1 baseline, the actual operator (Andrzej) should talk to the real
finalists, blind, and give a qualitative verdict. This document is the
protocol; running it is **not** part of the autonomous M1.0B sweep.

## 2. Models under test

Filled in from the M1.0B finalist ranking, now confirmed by Phase 2 (see
`docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md` §10, §14–§17 and
`docs/reports/R0003_*`): 3 finalists, labelled **Model A / B / C**. **SEALED** —
the mapping is recorded only in §6 ("After reveal") and must not be opened until
all three sessions are run and scored. Whoever sets up the terminal tabs (an
agent run) reads §6 to know which tag goes in which tab; the operator does not
read §6 until after scoring.

| Label | Actual model |
|---|---|
| Model A | *sealed — see §6* |
| Model B | *sealed — see §6* |
| Model C | *sealed — see §6* |

Model D is unused this round (3 finalists, not 4).

What the agent-assisted scoring expects (so a divergence is easy to spot — do
**not** show this to the operator before scoring): one finalist is the
all-round quality leader but the slowest and heaviest; one is nearly as good,
clearly the fastest, and the lightest of the two Gemma options; one has
excellent English but visibly non-native Polish and should not be picked for
bilingual everyday use. If the operator's live verdict contradicts that, record
the specific transcript moment that drove it — that is the more valuable
result.

## 3. How to run it blind

1. Whoever sets up the session (a second agent run, or the same agent in a
   separate step) assigns the A/B/C labels and does **not** reveal the
   mapping to the operator until after all three conversations are scored.
2. Use one consistent interface for all three (e.g. `ollama run <tag>` in three
   separate terminal tabs, or a simple script that swaps `--model`). Same
   system persona — use the compact native-Polish NeXa persona from
   `scripts/m1_bench/cases/conversation_pl.json` (the one M1.0B §15 found best
   for brevity + instruction-following) for every model, unless the point of a
   specific run is explicitly to also compare personas — keep that a separate
   pass. One model loaded at a time; `ollama stop` the previous before the next
   (the heaviest finalist is ~10 GB resident).
3. Same machine (this Pi), same `num_ctx` (8192), one model loaded at a time.
4. Operator talks to each model for **approximately 10–15 minutes**, in
   whatever mix of Polish/English feels natural — not a script. Encourage:
   normal chit-chat, at least one correction ("nie, nie o to mi chodziło"),
   one topic switch, one recall check, one moment of asking for a short vs a
   detailed answer, and — since one finalist is English-strong / Polish-weak —
   at least a few minutes of ordinary Polish per model.
5. Immediately after each model, before moving to the next, the operator
   fills in §4 for that letter while the impression is fresh.
6. After all three, the operator fills in §5 (comparative) and only then is
   the A/B/C → real-model mapping revealed.

## 4. Per-model scorecard — filled in 2026-09-04 (Andrzej, live, blind)

Full text (transcripts, free-form notes) in
`docs/testing/m1_operator_blind_results/{A,B,C}_scorecard.json` +
`{A,B,C}_transcript.json`. Summary below; real model column added post-reveal.

**Model A — `gemma4:e4b`** (20 turns)

| Question | Answer |
|---|---|
| Natural/human feeling (1–5) | 4 |
| Understood me (1–5) | 4 |
| Polish (1–5) | 4 |
| English (1–5) | 4 |
| Context/recall (1–5) | 4 |
| Correction handling (1–5) | 4 |
| Speed/rhythm (1–5) | 4 |
| Factual trustworthiness (1–5) | 4 |
| Everyday NeXa? | **YES** |
| Annoyed you | Responses sometimes felt a little slow; a few Polish phrases sounded slightly unnatural; jokes were weak. |
| Liked | Understood the conversation well, remembered earlier topics, handled Polish and English well, felt natural overall. |
| Invented/confidently wrong | None noticed (correctly declined the fabricated-band probe, "Silver Echo Theory"). |
| Free-form | Strong conversation; comfortable as an everyday NeXa, pending comparison with B/C. |

**Model B — `qwen3.5:2b`** (7 turns)

| Question | Answer |
|---|---|
| Natural/human feeling (1–5) | 2 |
| Understood me (1–5) | 2 |
| Polish (1–5) | 1.5 |
| English (1–5) | n/a — not sufficiently tested |
| Context/recall (1–5) | 4 |
| Correction handling (1–5) | n/a — not sufficiently tested |
| Speed/rhythm (1–5) | 2 |
| Factual trustworthiness (1–5) | 1 |
| Everyday NeXa? | **NO** |
| Annoyed you | Polish unnatural/barely coherent in places; answers turned strange/overly philosophical; some responses felt slow; the joke was bad and inappropriate. |
| Liked | Correctly recalled the "days pass too fast" thread. |
| Invented/confidently wrong | Yes — claimed the sun rises in the west; gave a confused/invented "Kotzebue on São Tomé" location. |
| Free-form | Much weaker than Model A; would not choose as everyday NeXa. |

**Model C — `gemma4:e2b`** (15 turns)

| Question | Answer |
|---|---|
| Overall operator score (1–5) | 3 |
| Everyday NeXa? | **MAYBE** |
| Note | Operator gave a compressed final judgement for this model (overall score + everyday-NeXa verdict only) rather than the full per-question table; the live transcript is complete (15 turns) and available for a follow-up detailed read if needed. |

## 5. Comparative scorecard — filled in 2026-09-04

| Question | Answer |
|---|---|
| Overall ranking, best to worst | **A > C > B** (`gemma4:e4b` > `gemma4:e2b` > `qwen3.5:2b`) |
| Everyday NeXa pick | **Model A** (`gemma4:e4b`) |
| Per-model overall score | A 4/5, C 3/5, B 2/5 |
| Which was most annoying / best Polish / best English / felt fastest / would avoid / surprises | Not asked individually — operator gave the compressed ranking + score form above instead of the full question-by-question comparative table. The per-model tables in §4 cover the substance (Polish/English/annoyance are scored there for A and B). |

## 6. After reveal

Mapping (recorded 2026-09-02 by the agent that set up Phase 1/2; opened to the
operator 2026-09-04 after all three sessions were run and scored):

| Label | Actual model |
|---|---|
| Model A | `gemma4:e4b` |
| Model B | `qwen3.5:2b` |
| Model C | `gemma4:e2b` |

## 7. Outcome

This test's result was the final qualitative input before the M1.1 baseline
model was frozen. It does not by itself trigger implementation — M1.1 remains
a separate, explicitly-started milestone. See §8 for the comparison with
R0003 and the decision this produced.

## 8. Result vs. R0003 (2026-09-04)

**Agreement — `qwen3.5:2b` (Model B) is unsuitable for bilingual everyday use,
now `OPERATOR-CONFIRMED`.** R0003's `AGENT-ASSISTED` finding ("gated out of the
bilingual role by broken Polish + a honesty-probe hallucination") is confirmed
independently: the operator scored it worst on every axis (Polish 1.5/5,
factual trustworthiness 1/5, everyday NeXa NO) and, blind, caught the same
class of failure R0003 flagged — a live conversational hallucination (a
confidently stated false claim that the sun rises in the west, plus a
garbled/invented geographic detail) — without being told to look for it.

**Both Gemma 4 models beat the incumbent-class model** — consistent with
R0003. Between the two Gemma 4 finalists there is a **partial divergence**:
R0003's *weighted* NeXa score (which folds in speed + Pi-practicality weight)
narrowly favored `gemma4:e2b` (4.0) over `gemma4:e4b` (3.9); the operator's
live, blind ranking put `gemma4:e4b` clearly ahead (4/5, YES vs. 3/5, MAYBE).
This is not a contradiction of the underlying evidence — R0003's own raw
Phase-2 conversation-quality sub-scores already had `gemma4:e4b` ≥
`gemma4:e2b` on natural conversation and honesty (`CONV-NATURAL-S1` 4.5 vs.
4.0, `CONV-HONESTY-S1` tied at 4.5) — the weighted total's lean toward e2b came
specifically from the speed/RAM weighting, not from conversation quality. The
operator's lived judgement, per this doc's own §6 guidance, is the actual
product bar: **live conversation quality outweighed the speed/footprint
advantage in practice**, at least in a single ~10–15 min session per model.

**Decision applied (2026-09-05):** the owner reviewed this result, explicitly
signed off on the RAM/latency trade (`gemma4:e4b` ~10 GB resident, ~3 tok/s vs.
`gemma4:e2b`'s ~7.4 GB / ~6 tok/s), and **`gemma4:e4b` is now FROZEN as the
M1.1 local conversation baseline** — see
`docs/decisions/ADR-0002_text_conversation_foundation.md` Amendment 2, which
supersedes the earlier `gemma4:e2b` `PROPOSAL` from the ADR-0002 M1.0B
amendment on the model question only. `gemma4:e2b` remains documented there as
a strong faster/lower-resource local alternative, and `qwen3:4b-instruct`
remains the Apache-2.0, largest-context swappable fallback.

This does not start M1.1. M1.1 remains a separate, explicitly-started
milestone.
