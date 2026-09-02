# M1 Operator Blind Conversation Test

Status: **prepared, not yet run.** This is the qualitative tie-breaker owed
before the M1.1 baseline model is frozen (`docs/reports/R0002_*` §"Unresolved"
item 2; `docs/reports/R0003_*` §"Unresolved" item 1;
`docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md` §19). M1.0B Phase 2 is
**complete** (the deep-dive that picked and stress-tested these 3 finalists);
all M1.0 / M1.0B quality scores are still `AGENT-ASSISTED` (Claude, against
transcripts) — **not** operator-confirmed. This test is the operator-confirmed
pass.

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

## 4. Per-model scorecard (fill in once per letter, right after that session)

**Model: ___**

| Question | Answer / note |
|---|---|
| Which felt most human? (1–5) | |
| Which understood me best? (1–5) | |
| How did the Polish sound? (1–5, or n/a if the session was English-only) | |
| How did the English sound? (1–5, or n/a if the session was Polish-only) | |
| Anything that annoyed you? | |
| Was it too verbose / too terse? | |
| Did it feel fast enough for normal chat rhythm? | |
| Would you want this as your everyday NeXa? (yes/no/maybe) | |
| Did you notice anything invented / wrong that was stated confidently? | |
| Did it remember something from earlier in the same conversation, correctly? | |
| Did a correction ("nie o to chodziło") actually land, or did it ignore it? | |
| Free-form notes | |

## 5. Comparative scorecard (fill in after all three, before reveal)

| Question | Answer |
|---|---|
| Overall ranking, best to worst | |
| Which would you pick as your everyday NeXa, if you had to pick one today? | |
| Which was most annoying? | |
| Which Polish sounded best? | |
| Which English sounded best? | |
| Which felt fastest? | |
| Any model you'd actively avoid? | |
| Surprises (a model did better/worse than the machine metrics suggested)? | |

## 6. After reveal

**SEALED — do not read this section until all three sessions (§4) are run and
scored.** Mapping, recorded 2026-09-02 by the agent that set up Phase 1/2
(not to be quoted or hinted at in any chat/report summary before reveal):

| Label | Actual model |
|---|---|
| Model A | `gemma4:e4b` |
| Model B | `qwen3.5:2b` |
| Model C | `gemma4:e2b` |

Record the A/B/C/D → model mapping here, plus whether the operator's ranking
agrees or disagrees with the M1.0B agent-assisted scoring and the weighted
NeXa score. A disagreement is not automatically wrong — the operator's lived
judgement is the actual product bar. If operator and agent scoring diverge
significantly, record why (a specific transcript moment is more useful than
"felt different").

## 7. Outcome

This test's result is the final qualitative input before the M1.1 baseline
model is frozen. It does not by itself trigger implementation — M1.1 remains
a separate, explicitly-started milestone.
