# M1 Natural Conversation Benchmark

Status: **M1.0 — design + first baseline run.** This defines stable cases and a
human-review rubric for natural NeXa conversation. It is a measurement
instrument, not a product spec. Cases are append-only: never renumber, mark
retired ones as such.

Related: `docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md`,
`docs/reports/R0002_m1_natural_conversation_research_20260831.md`,
`scripts/m1_bench/` (throwaway harness), `docs/testing/TEST_STRATEGY.md`.

---

## 1. What this benchmark measures

Natural, human conversation quality — **not** factual QA. A turn is "good" if a
thoughtful bilingual person would accept it from a companion: right language,
right length, uses what was just said, handles corrections and topic changes,
does not lecture, does not invent, does not repeat boilerplate.

Two axes, scored separately:

- **Quality** — human 1–5 rubric (§4), per turn and per session.
- **Performance** — measured automatically by the harness (§5).

## 2. Fixed test conditions (every model, every run)

| Condition | Value (M1.0) |
|---|---|
| Backend | Ollama HTTP `/api/chat`, streaming |
| Host | Raspberry Pi 5 16GB, `nexa`, Debian 13 aarch64 (see research doc §2) |
| Context window | `num_ctx = 8192` for conversation cases |
| Sampling | model's documented recommendation, recorded per run (§ research doc) |
| System prompt | the short NeXa persona in each case file (`scripts/m1_bench/cases/*.json`) |
| History | full in-session transcript resent each turn (no truncation below 8k) |
| Cold start | model unloaded before turn 1 so load time is measured once |
| Repeats | perf micro-benchmark ×3; conversation sessions ×1 (M1.0), ×3 later |

M1.0 runs on Ollama only. `llama.cpp` direct is a Phase-9 comparison item and may
be added with the same cases and matched sampling.

## 3. Cases

Each case ID is a stable reference. In M1.0 they are exercised as turns inside
three session files; later they may become individually scriptable.

### Polish — `scripts/m1_bench/cases/conversation_pl.json` (session `CONV-PL-S1`)

| ID | Intent | Turn(s) |
|---|---|---|
| `CONV-PL-001` | Basic natural Polish conversation, casual opener | 1–2 |
| `CONV-PL-002` | Multi-turn reference resolution ("nie zdążę z prezentacją" → later "tej prezentacji") | 3, 11 |
| `CONV-PL-003` | Correction: "Nie, nie chodzi mi o … Chodzi mi o …" | 5 |
| `CONV-PL-004` | Topic switch ("Zmieńmy temat" → present for brother) | 6–7 |
| `CONV-PL-005` | "Powiedz to prościej, jednym zdaniem" | 8 |
| `CONV-PL-006` | Disagreement / natural opinion ("Nie zgadzasz się?") | 9 |
| `CONV-PL-007` | Short casual turns without robotic over-explanation | 1, 6, 8 |
| `CONV-CTX-001` | Recall something from earlier in the session ("O czym rozmawialiśmy na początku?") | 10 |
| `CONV-STYLE-001` | No repeated boilerplate / assistant phrasing across the session | all |

### English — `scripts/m1_bench/cases/conversation_en.json` (session `CONV-EN-S1`)

| ID | Intent | Turn(s) |
|---|---|---|
| `CONV-EN-001` | Basic natural English conversation, casual opener | 1–2 |
| `CONV-EN-002` | Multi-turn reference resolution ("the being-on-hold part") | 3, 5 |
| `CONV-EN-003` | Correction: "Right, but I meant … specifically" | 5 |
| `CONV-EN-004` | Topic switch ("Different topic." → Lisbon trip) | 6–7 |
| `CONV-EN-005` | "Say that back to me in one sentence" | 8 |
| `CONV-EN-006` | Disagreement / natural opinion ("Disagree?") | 9 |
| `CONV-CTX-002` | Recall the first thing raised in the session | 10 |

### Mixed language — `scripts/m1_bench/cases/conversation_mix.json` (session `CONV-MIX-1`)

| ID | Intent | Turn(s) |
|---|---|---|
| `CONV-MIX-001` | PL → EN → PL switching inside one session, replies follow the user's current language | 3, 5 |
| `CONV-CTX-003` | Cross-language recall (fact stated in PL, asked again in PL after an EN detour) | 6 |

## 4. Human quality rubric (1–5)

Score each **turn** on the dimensions that apply, then give a **session** score.

| Score | Meaning |
|---|---|
| 5 | Indistinguishable from a thoughtful person; nothing to fault |
| 4 | Good; a minor nit (slightly long, one clumsy phrase) |
| 3 | Acceptable but visibly "assistant" — over-explains, mild boilerplate, or slightly misses intent |
| 2 | Noticeably wrong: wrong language, ignores the correction, invents a capability/fact, lectures |
| 1 | Broken: unusable answer, wrong language throughout, hallucination presented as fact, or non-response |

Dimensions to hold in mind while scoring (not each separately numbered):

- **Language correctness** — replies in the user's current language; no drift.
- **Length discipline** — matches the weight of the question; no lecture on a one-line question.
- **Reference resolution** — "it / that / the presentation" resolved from context.
- **Correction handling** — the "no, I meant X" turn actually changes the answer.
- **Topic switching** — drops the old thread cleanly on request.
- **Recall** — earlier session facts retrievable when asked.
- **Naturalness** — phrasing a person would use; not "Certainly! Here are three things:".
- **Honesty** — says "I don't know" / "I'm not sure" instead of inventing.
- **Repetition** — no reused stock sentences or repeated jokes across turns.

Scoring is done by a human (or a clearly-labelled model-assisted pass that a human
confirms). M1.0 records transcripts; scores are filled in against those
transcripts and stored in the research doc's quality tables.

## 5. Performance metrics (harness-measured, per turn)

- model load time (turn 1 only), time-to-first-token (TTFT)
- generation tokens/sec, prompt-eval tokens/sec
- total wall time per turn, output token count, context tokens used
- `MemAvailable` before load / per turn / after; inferred model RAM
- CPU temperature before / peak during; `vcgencmd get_throttled` before/after
- failures / timeouts

Targets are **not** pass/fail gates in M1.0 — they are reference points to judge
"is the Pi fast enough":

| Metric | Comfortable | Usable | Poor |
|---|---|---|---|
| Warm TTFT | < 1.5 s | < 4 s | ≥ 4 s |
| Generation tok/s | ≥ 8 | 4–8 | < 4 |
| Short reply (≈50 tok) wall | < 6 s | < 15 s | ≥ 15 s |
| Peak CPU temp under load | < 75 °C | < 82 °C | throttling |

## 6. Out of scope for M1.0

Voice / STT / TTS turns, wake word, barge-in, tool execution, long-term memory
recall across sessions, and any automated LLM-judge score without a defined,
human-audited methodology.
