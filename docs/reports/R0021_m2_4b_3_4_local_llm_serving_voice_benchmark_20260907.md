# R0021 — M2.4B.3.4: Local LLM serving & voice performance benchmark

- **Date:** 2026-09-07
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.4B.3.4 — research +
  benchmark only (no production model switch)**
- **Related:** `docs/reports/R0018_…` (**the rate authority** — long-form
  continuity needs ~15.9 generated **chars/s**; `realtime_text_ratio ≈
  0.53` at `gemma4:e4b`'s ~8.5), `docs/reports/R0014_…` (CPU contention —
  Piper `nice +10` recovers the LLM), `docs/reports/R0020_…` (B.3.3
  `ResponseMode.VOICE` — the policy this benchmark drives),
  `docs/reports/R0003_…` (M1.0B model sweep — reused as prior evidence),
  `docs/research/m2_4b_llm_bench/` (harness + raw JSON/logs).

**Research + benchmark + one isolated continuity-warning fix. No
production model change. `gemma4:e4b` stays the frozen M1.1 baseline
(ADR-0002 Amendment 2) until the operator approves a switch. No systemd /
global config change, no `sudo`, no `renice`/`taskset` shipped. Not
pushed.**

---

## TASK RESULT

**PASS (benchmark).** Measured `gemma4:e4b` cold + warm, PL + EN, under the
real B.3.3 voice path; swept Ollama serving options; benchmarked a
shortlist of alternatives; scored quality on a 10-axis rubric + a
deterministic science-truth set.

**Headline:**

- **`gemma4:e4b` PL generation ≈ 9.4 chars/s** (core-set mean, uncontended,
  pure decode) — **~59 % of R0018's 15.9 chars/s target**. **EN ≈ 14.7
  chars/s** (~92 % of target). tok/s is ~3.1 both languages; the gap is
  Polish tokenisation (~3.1 PL chars/token vs ~4.6 EN). **Language
  throughput ratio PL/EN ≈ 0.64.**
- **Warm-prefix TTFT ≈ 2.6 s** (turn 2+); **cold-prefix TTFT ≈ 29 s**
  (first turn of a fresh conversation — the ~535-token persona + voice
  directive preamble evaluated at ~20 tok/s). Cold model load ≈ 33 s,
  ~10.2 GB resident.
- **One real serving win: `num_thread = 2` gives `gemma4:e4b` +14 % tok/s**
  (+19 % PL chars/s) — fewer decode threads beat the default nproc = 4 on
  this memory-bandwidth-bound 4-core Pi. It is a per-request Ollama option
  (no `sudo`, no system change). It moves PL from ~59 % to ~68–74 % of
  target — worth taking, does not close the gap. `num_batch` / `num_ctx`
  do nothing for decode speed. The other free win is operational, not a
  knob: **keep the model + preamble prefix warm** so every turn is a
  warm-prefix turn (2–3 s TTFT, not 29 s).
- **`gemma4:e2b` (same family, MatFormer sub-model) is the one candidate
  that changes the speed picture: ~2× faster in both languages — PL ≈ 18.7
  chars/s (meets the 15.9 target), EN ≈ 27.4 chars/s — warm TTFT ≈ 1.1–1.4 s
  (meets ≤ 1.5 s), ~7.5 GB resident (2.7 GB lighter).** Everyday
  conversation and context retention are close to `e4b`, **but the
  science-truth set exposed a real, repeatable quality regression**: a
  self-contradiction on the classic "kg of iron vs kg of feathers" trick
  (PL), a self-contradiction on relativity (EN: "mass does not increase …
  the formula shows that mass increases"), a hallucination ("the Sun has
  a pink hue"), an unanswered myth ("the Sun … is yellow"), and **one
  English question answered in Polish** (language-mirroring break).
  Reasoning answers are correct but the working is thin. This is **not a
  clean 90–95 %-of-quality Pareto point** — it is a genuine quality drop
  that the operator must weigh against the 2× speed. Recommendation:
  operator A/B blind session, with the caveat stated plainly.
- **No other shortlist model is a viable bilingual replacement.**
  `qwen3:4b-instruct` / `qwen3.5:4b` — same speed as `e4b`, garbled Polish
  grammar. `qwen2.5:3b` / `llama3.2:3b` — fast and tiny, but Polish is
  word-salad / nonsense. Both Bielik variants — 2 tok/s (Q8_0) and empty
  completions on 4–5 of 6 Polish prompts (Q4_K_M). All eliminated.
- **Answer to the task's two decision questions:** *Does any candidate
  meet ~15.9 PL chars/s?* — only `gemma4:e2b` (~18.7). *Does any candidate
  beat `gemma4:e4b` without quality loss?* — **no.**

**Continuity warning:** root-caused and fixed (trivial self-cancel);
committed **separately** as `fix: stop continuity hold task cancelling
itself` with 2 regression tests. See CONTINUITY WARNING RESULT — it is
**not** part of the benchmark conclusions.

## CURRENT GEMMA4:E4B BASELINE

`docs/research/m2_4b_llm_bench/bench_baseline_e4b_20260907.json`
(uncontended; `ResponseMode.VOICE`; `num_ctx 8192`, `num_predict 200`,
temp 0.7; Ollama 0.33.2; Pi 5 16 GB; `nice 0`; no Piper running).

**COLD** (`ollama stop` then one PL turn):

| metric | value |
|---|---|
| model load (`load_duration`) | **33.2 s** |
| first-ever TTFT (load + cold prompt-eval + first token) | 60.3 s |
| resident RAM (ΔMemAvailable) | **~10.2 GB** |
| generation once loaded | 3.28 tok/s |

**WARM**:

| metric | PL | EN |
|---|---|---|
| generation | **3.06 tok/s** | **3.22 tok/s** |
| generated chars/s (core-set mean, `chars / eval_duration`) | **9.4** (6.0–11.6) | **14.7** (7.5–17.9) |
| typical-prose chars/s (excl. the short reasoning reply) | ~10.5 | ~15.5 |
| **warm-prefix TTFT** (turn 2+, prefix cached) | **2.1–3.3 s** (mean 2.6) | **2.1–2.5 s** |
| **cold-prefix TTFT** (fresh conversation, turn 1) | **28.4–30.6 s** | 28.4–30.6 s |
| prompt-eval rate (~535-tok preamble, cold) | **~20 tok/s** | — |
| CPU temp during run | 66–72 °C | |
| throttled | **0x0** (never) | |
| MemAvailable during run | steady ~4.2 GB, no swap | |

**vs R0018's 15.9 chars/s target:** PL **59 %** (pure decode) / ~53 %
(R0018's effective-under-TTS figure); EN **92 %**. The Polish deficit is
real and is the whole reason for this stage; English is already close.

**Why cold-prefix TTFT is ~29 s:** llama.cpp reuses the KV cache only for
a prompt that is a prefix-extension of the immediately preceding request.
NeXa's real voice pipeline gets 2–3 s first-token because every turn
extends the previous turn's token sequence (persona + directive + history
+ new user text — R0009's byte-stable prefix). The **first turn of a
fresh conversation** has nothing cached and pays the full ~535-token
preamble at ~20 tok/s ≈ 27 s. `followup t1` (an exact repeat of the
`short` prompt that had just run) returned in **0.46 s** — proof the
cache works when the prefix matches.

## SERVING OPTIMISATION RESULTS

`bench_serving_e4b_20260907.json` — `gemma4:e4b`, science prompt PL+EN,
2 reps each, warm. Ollama per-request `options` only (no `sudo`, no
service/systemd change, no `flash_attn` toggle — that is server-launch,
not per-request, and left as-is).

| variant | tok/s PL | tok/s EN | chars/s PL | chars/s EN | vs baseline tok/s |
|---|---|---|---|---|---|
| **baseline** (defaults) | 3.30 | 3.34 | 11.3 | 17.8 | — |
| `num_thread=4` (explicit) | 3.29 | 3.33 | 11.7 | 17.5 | ±0 % (already the default) |
| **`num_thread=3`** | **3.59** | **3.67** | 13.3 | 19.1 | **+9 %** |
| **`num_thread=2`** | **3.76** | **3.80** | **13.5** | **19.8** | **+14 %** |
| `num_batch=256` | 3.25 | 3.29 | 11.8 | 16.8 | ±0 % |
| `num_batch=1024` | 3.29 | 3.35 | 11.9 | 17.4 | ±0 % |
| `num_ctx=4096` | 3.29 | 3.35 | 11.9 | 16.9 | ±0 % |
| `num_ctx=2048` | 3.30 | 3.32 | 12.3 | 16.7 | ±0 % |

**Finding: fewer decode threads are faster on this Pi 5.** `num_thread=2`
gives **+12–14 % tok/s** (sweep 3.30 → 3.76; focused 5-rep run
3.38 → 3.77); `num_thread=3` a milder +8–9 %; **`num_thread=1` is −15 %**
(one thread is too few — 2 is the floor); `num_thread=4` = the default.
This is the classic 4-core memory-bandwidth-bound GGUF pattern —
llama.cpp's default (nproc = 4) oversubscribes the cores against the OS +
the Ollama Go runtime + (in the real pipeline) Piper, and the sync
overhead costs more than the 3rd/4th thread's compute adds. `num_batch`
and `num_ctx` do nothing for decode speed (only prompt-eval / memory
footprint). *(The large TTFT swings for the `num_thread` rows are the
`llama-server` reload that a thread-count change triggers — a one-time
cost if the value is set once at provider init, not per turn.)*

**`num_thread=2` still leaves `gemma4:e4b` PL at ~13.5 chars/s — ~85 % of
the 15.9 target, not there.** It is a real, free, no-privilege
improvement (a per-request Ollama option NeXa's provider can set), worth
taking on its own merit, but it does **not** by itself solve the Polish
continuity deficit. **Recorded as a candidate provider-config change; not
shipped in B.3.4.** A focused 5-rep warm confirmation is in
`bench_serving_confirm_20260907.json` (see BEST CONFIGURATION).

**Operational win (not a knob):** the ~29 s cold-prefix TTFT and the
~33 s cold load only ever happen on the **first turn after an eviction**.
`keep_alive` is already `5m` in `LocalModelProvider`; raising it (or a
startup + idle warm-up ping that also primes the persona+directive
prefix) keeps every real turn a warm-prefix turn (2–3 s). Config/ops
change, recorded here, not shipped in B.3.4.

## PL PERFORMANCE

Core-set warm generation (mean of SHORT / FOLLOW-UP / SCIENCE / REASONING
+ 5-turn thread), plus the fixed-length run (`num_predict 170`, dense
prose, 3 reps) as a second reading:

| model | tok/s | core chars/s | fixed chars/s | warm TTFT | vs 15.9 target | PL quality |
|---|---|---|---|---|---|---|
| **gemma4:e4b** (baseline) | 3.06 | **9.4** (6–11.6) | 11.7 | 2.1–3.3 s | 59 % (core) / 74 % (fixed) | **best** (4/5) |
| **gemma4:e2b** | 5.79 | **18.7** (13.3–23.6) | 21.3 | **1.1–1.4 s** | **118 %** ✓ | ~4/5 conv, **weak facts** |
| qwen3:4b-instruct | 3.82 | 9.2 (6.6–12.0) | 11.1 | 5.6 s | 58 % | **garbled grammar** (2/5) |
| qwen3.5:4b | 3.29 | 9.9 (7.8–11.4) | 9.8 | 5.5 s | 62 % | **garbled grammar** (2/5) |
| qwen2.5:3b | 4.85 | 12.8 | 14.4 | ~0.5 s | 81–91 % | **broken** (word-salad, 1/5) |
| llama3.2:3b | 4.80 | 12.2 | 12.9 | ~0.9 s | 77–81 % | **broken** (nonsense, 1.5/5) |
| Bielik-4.5B Q8_0 | 2.01 | — | 7.8 | 0.5 s | 49 % | too slow; empty completions |
| Bielik-4.5B Q4_K_M (3rd-party) | 3.51 | — | 12.8 | 2.6 s | 81 % | **empty completions** (unusable) |

## EN PERFORMANCE

| model | tok/s | core chars/s | fixed chars/s | warm TTFT | vs 15.9 target | EN quality |
|---|---|---|---|---|---|---|
| **gemma4:e4b** (baseline) | 3.22 | **14.7** (7.5–17.9) | 15.1 | 2.1–2.5 s | 92 % / 95 % | 4.5/5 |
| **gemma4:e2b** | 5.97 | **27.4** (15.0–34.4) | 23.0 | **0.9–1.1 s** | **172 %** ✓ | 4.5/5 (1 EN→PL slip) |
| qwen3:4b-instruct | 3.89 | 15.2 (7.7–20.9) | 17.1 | 3.9 s | 96 % | 4/5 (clean EN) |
| qwen3.5:4b | 3.31 | 14.8 (8.4–19.1) | 13.0 | 4.4 s | 88 % | 3.5/5 (weak reasoning, 1 EN→PL) |
| qwen2.5:3b | 5.34 | — | 25.4 | ~0.5 s | 160 % | not scored (M1) |
| llama3.2:3b | 4.84 | — | 22.4 | ~0.9 s | 141 % | ok EN (M1) |
| Bielik-4.5B Q8_0 | 2.02 | — | 6.1 | 0.5 s | 38 % | weak (M1) |
| Bielik-4.5B Q4_K_M (3rd-party) | 3.99 | — | **0.0** (empty) | — | — | **unusable — empty EN completions** |

**English was never the binding constraint** — `gemma4:e4b` is already at
~92–95 % of target in EN, and `e2b` / `qwen2.5:3b` / `llama3.2:3b` all
clear it comfortably.

## LANGUAGE THROUGHPUT DIFFERENCE

`chars/s` at equal tok/s differs by language because of tokenisation
density:

| model | PL chars/tok | EN chars/tok | **PL chars/s ÷ EN chars/s** |
|---|---|---|---|
| gemma4:e4b | ~3.1 | ~4.6 | **0.64** |
| gemma4:e2b | ~3.2 | ~4.6 | **0.68** |
| qwen3:4b-instruct | ~2.9 | ~4.4 | **0.64** |
| qwen2.5:3b | ~2.7 | ~4.8 | **0.57** |

**Implication:** tok/s alone must never be used as the NeXa speed metric.
Every model tested delivers ~55–70 % as many chars/s in Polish as in
English at the same tok/s. R0018's 15.9 chars/s target is a **Polish**
target; English has headroom on every candidate. A model that looks fast
on an English benchmark (e.g. `qwen2.5:3b` at 25 EN chars/s) can still sit
near target or below in Polish (14.4).

## MODEL SHORTLIST

Built from RAM fit (16 GB), ARM64/Ollama compatibility, quant
availability, PL + EN ability, licence, and **reusing R0003 (M1.0B)
evidence** rather than re-downloading a catalogue.

| model | params / quant | ~RAM | licence | prior evidence (R0003) | in this benchmark |
|---|---|---|---|---|---|
| **gemma4:e4b** | MatFormer e4b (~Q4) | ~10.2 GB | Gemma (permissive) | best overall, PL 4/5, EN 4/5, ~3.1 tok/s | **baseline — full** |
| **gemma4:e2b** | MatFormer e2b | ~7.5 GB | Gemma | fastest quality-passing, PL 3.5/5, EN 4/5, ~6.6 tok/s | **candidate — full** |
| qwen3:4b-instruct | 4B Q4 | ~2.5 GB | **Apache-2.0** | incumbent, reliable, PL 3/5, EN 3.5/5 | core + science |
| qwen3.5:4b | 4B Q4 | ~3.4 GB | Apache-2.0 | **broken PL** (garbled, hallucinated) | core + science (re-confirm) |
| SpeakLeash/Bielik-4.5B-v3 | 4.5B Q8_0 | ~5.1 GB | Apache-2.0 | native PL grammar but shallow, ~2 tok/s, EN weak | fixed + science |
| Bielik-4.5B-v3 Q4_K_M (3rd-party) | 4.5B Q4_K_M | ~2.9 GB | Apache-2.0 weights | **reliability failure** (empty EN turns) | fixed + science |
| llama3.2:3b | 3B Q4 | ~2 GB | Llama 3.2 | weak PL | fixed |
| qwen2.5:3b | 3B Q4 | ~1.9 GB | Qwen | not scored in M1 | fixed |

Excluded without re-test (R0003 disqualified, nothing changed): `phi4-mini:3.8b`
(PL non-sequitur + persona break), `qwen3.5:2b` (broken PL — English-only).

**Post-benchmark shortlist status:**

- **`gemma4:e4b`** — baseline, unbeaten on Polish quality.
- **`gemma4:e2b`** — the only real speed candidate; quality caveat (above).
- **`qwen3:4b-instruct` / `qwen3.5:4b`** — **eliminated**: same speed as
  `e4b` (no win), and Polish grammar is garbled (non-words, case errors,
  invented quantities) — not deployable as the bilingual model despite
  Apache-2.0 + low RAM.
- **`qwen2.5:3b` / `llama3.2:3b`** — **eliminated**: fast + tiny + very
  fast prefill (`qwen2.5:3b` prompt-eval ~730 tok/s vs `e4b`'s ~20), and
  EN is usable, but the Polish spot-check is **word-salad** ("masy
  cukierkowej", "ciał omaszywanych", "grzejniczkę"; `qwen2.5:3b` also got
  the time-arithmetic wrong — "20:45" — and *denied* a fact the user had
  just given) / **nonsense** (`llama3.2:3b`: "Czarna dziura to ogromna
  gęsta kawę, którą ludzie wietrzą do kawy" + a leaking `assistant\n\n`
  chat-template artefact). Not deployable for Polish.
- **Bielik-4.5B-v3 (Q8_0 and Q4_K_M 3rd-party)** — **eliminated**: Q8_0
  runs at 2 tok/s and both variants return **empty completions** on
  4–5 of 6 Polish science prompts (reliability failure, confirms R0003).

## MODEL COMPARISON TABLE

`AGENT-ASSISTED` quality (Claude vs the raw transcripts + R0003 rubric).
`chars/s` = core-set warm mean. `sci` = science-truth score /6.

| model | params/quant | RAM | cold load | warm TTFT PL / EN | tok/s PL / EN | chars/s PL / EN | qual PL / EN | reasoning | sci PL / EN | context | voice-policy | licence | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **gemma4:e4b** | MatFormer e4b ~Q4 | 10.2 GB | 33 s | 2.6 / 2.3 s | 3.06 / 3.22 | **9.4 / 14.7** | 4 / 4.5 | 5/5 | 5 / 4.5 | 5/5 | strong | Gemma | **baseline — keep** |
| **gemma4:e2b** | MatFormer e2b | 7.5 GB | 24 s | **1.2 / 1.0 s** | 5.79 / 5.97 | **18.7 / 27.4** | ~4 / 4.5 | 3.5/5 | **2.5 / 3** | 5/5 | strong | Gemma | **candidate — 2× speed, real quality drop** |
| qwen3:4b-instruct | 4B Q4 | 2.5 GB | 12 s | 5.6 / 3.9 s | 3.82 / 3.89 | 9.2 / 15.2 | **2 / 4** | 4.5/5 | **5 / 5.5** | 5/5 | medium (markdown) | **Apache-2.0** | not viable PL (garbled grammar) |
| qwen3.5:4b | 4B Q4 | 3.4 GB | 16 s | 5.5 / 4.4 s | 3.29 / 3.31 | 9.9 / 14.8 | 2 / 3.5 | 3/5 | 3 / 3 | 4/5 | medium | Apache-2.0 | not viable (garbled PL, EN→PL, hallucination) |
| qwen2.5:3b | 3B Q4 | 1.9 GB | ~9 s | ~0.5 s | 4.85 / 5.34 | 12.8 / 22.7 | **1/5** / ~4/5 | ✗ ("20:45") | — | ✗ (denies user's fact) | — | Qwen | **not viable — word-salad Polish** |
| llama3.2:3b | 3B Q4 | 2.0 GB | ~9 s | ~0.9 s | 4.80 / 4.79 | 12.2 / 20.9 | **1.5/5** / ~3.5/5 | ✓ ("16:25") | — | ✓ | — (template `assistant\n\n` leak) | Llama 3.2 | **not viable — nonsense Polish** |
| Bielik-4.5B-v3 Q8_0 | 4.5B Q8_0 | 5.1 GB | 21 s | 0.5 s | 2.01 / 2.02 | — / — | native but shallow | — | 2 / — | — | — | Apache-2.0 | **too slow (2 tok/s) + empty completions** |
| Bielik-4.5B-v3 Q4_K_M (3rd-party) | 4.5B Q4_K_M | 2.9 GB | 12 s | 2.6 s | 3.51 / 3.99 | 12.8 / **0.0** | — | — | ✗ | — | — | Apache-2.0 wts | **unusable — empty completions, wrong physics** (confirms R0003) |

## QUALITY RESULTS

10-axis rubric (R0003 §4 philosophy; `AGENT-ASSISTED` — Claude vs the raw
transcripts; the operator's live session is the owed confirmation).

**gemma4:e4b:**

| axis | score | note |
|---|---|---|
| 1 Polish naturalness | 4/5 | native, clean grammar, occasionally a touch stiff |
| 2 English naturalness | 4.5/5 | natural, well-scoped |
| 3 instruction following | 4.5/5 | honoured "krótko / show reasoning" |
| 4 context retention | 5/5 | recalled the two flours 4 turns later, both langs; "ona/it" resolved |
| 5 short voice-response compliance | 4.5/5 | ordinary answers 86–320 chars / 1–3 sentences |
| 6 explicit-detail override | 4.5/5 | "wyjaśnij dokładnie" → 622 PL / 838 EN chars |
| 7 reasoning | 5/5 | 14:50 + 95 min = 16:25 and 2:50 pm + 95 = 4:25 pm, both correct with working shown |
| 8 factual / scientific correctness | 3.5/5 | 5 of 6 truth cases correct; **relativity/mass wrong framing** (see below) |
| 9 hallucination tendency | 5/5 | none observed |
| 10 mixed PL/EN history | (deferred) | not tested this stage (task: provider-text only; no STT change) |

**gemma4:e2b:**

| axis | score | note |
|---|---|---|
| 1 Polish naturalness | ~4/5 | clean native grammar, terser than e4b; conversation reads well |
| 2 English naturalness | 4.5/5 | on par with e4b |
| 3 instruction following | 3.5/5 | concise & complied, but **1 EN science turn answered in Polish** (mirroring break) |
| 4 context retention | 5/5 | recalled both flours 4 turns later, both langs |
| 5 short voice-response compliance | 5/5 | tightest of the field — all ordinary answers 1–3 sentences, no lists |
| 6 explicit-detail override | 4/5 | "wyjaśnij dokładnie" expanded (relativity 294 PL / 273 EN chars) — shorter than e4b's but present |
| 7 reasoning | 3.5/5 | **correct answers** (16:25 / 4:25 pm) but working is thin / just restated |
| 8 factual / scientific correctness | **2.5/5** | core PL science turn: *"im większa masa, tym bardziej ograniczona prędkość"* — **wrong**; science set: self-contradiction on iron/feathers (PL) and relativity (EN) |
| 9 hallucination tendency | **3/5** | "the Sun has a pink hue" (PL); doesn't correct "the Sun is yellow" (EN) |
| 10 mixed PL/EN history | (deferred) | not tested |

**Verdict:** `e2b`'s everyday conversation, brevity and context retention
are genuinely close to `e4b` (axes 1–2, 4–5). The gap is concentrated in
**axes 3, 7, 8, 9** — instruction-mirroring, reasoning depth,
factual precision and hallucination — where it is clearly weaker, with
*repeatable* failures (not one-offs). "~2× faster at ~90 % of the
quality" undersells the risk: the 10 % that is missing is the reliability
10 %.

## SCIENTIFIC TRUTH RESULTS

Deterministic set, `ResponseMode.VOICE`, asked fresh each. `✓` accurate ·
`≈` partial · `✗` wrong/misleading · `∅` empty completion · `→PL` English
question answered in Polish.

| case | expected | e4b PL | e4b EN | e2b PL | e2b EN | qwen3:4b-instr PL | qwen3:4b-instr EN | qwen3.5:4b PL | qwen3.5:4b EN | Bielik Q8_0 PL | Bielik Q4KM PL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mass increase near *c* | rest mass invariant; E & p diverge; "relativistic mass" outdated | ✗ leads "masa relatywistyczna", hedges then truncates | ✗ "relativistic mass increases" | ✗ "masa faktycznie rośnie" (no hedge) | ✗✗ **self-contradicts**: "mass does not increase … the formula shows mass increases" | ≈ "nie rośnie w sensie klasycznym … m₀ nie zmienia się" (garble "maszyna") | **✓** "outdated interpretation of relativity" — best answer of the field | ✗ "masa relatywistyczna faktycznie rośnie" | ≈→PL "masa spoczynkowa nie rośnie … niezmienna" (right, wrong language) | ✗ "masa rośnie" | ✗ garbled `m = γ m₀ ⋅ c²` |
| water always boils 100 °C | no — pressure-dependent | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ≈ "wrzywa" (non-word) | ≈→PL garbled | ≈ "wrze w 100 … ale ciśnienie może wpływać" (weak) | ≈ "Tak, woda wrze w 100 … ale w niższym…" (contradicts itself) |
| kg iron vs kg feathers | equal | ✓ | ✓ | ✗ **"żelaza jest cięższy"** then "taką samą masę" (self-contradiction) | ✓ | ≈ "1 kg piętra" (garble) content OK | ✓ | ✓ (grammar slips) | ✓ | ∅ empty | ✗ "żelaza ma większą masę niż kilogram papieru" (misread + wrong) |
| 10 % brain myth | myth / false | ✓ | ✓ | ✓ (weak) | ✓→PL (content OK, wrong language) | ✓ | ✓ | ✗ **hallucination: "wykorzystuje ~20 % mózgu"** | ✓ | ∅ empty | ∅ empty |
| why sky is blue | Rayleigh; shorter λ scatter more | ✓ | ✓ | ✓ | ✓ | ≈ "rozpraszają czerwone i niebieskie" (muddled) | ✓ | ≈ "odbijają" (reflect ≠ scatter) | ✓ | ∅ empty | ∅ empty |
| real colour of the Sun | ~white; looks yellow via atmosphere | ✓ (slip "żółte niebo") | ≈ "mix of colours", no "white" | ✗ **"ma różowy odcień"** (hallucination) | ✗ "its visible color is yellow" (doesn't correct) | ✓ "wygląda jak biały, bo emituje wszystkie długości fal" | ✓ | ✓ "emituje białe światło" | ✓ "actually white" | ✗ "złocisto-żółty" | ≈ "żółto-pomarańczową" |
| **score /6** | | **5** | **4.5** | **2.5** | **3 (incl. a lang break)** | **~5** | **5.5** | **3** | **~4 (2 lang breaks)** | **~1** | **~1** |

**Reading:**

- **`gemma4:e4b`: 5 PL / 4.5 EN.** The *only* miss is the flagged
  relativity/mass case — it still leads with the outdated "relativistic
  mass increases" framing in both languages rather than "rest mass is
  invariant; the energy and momentum needed to keep accelerating grow
  without bound." Everything else (boiling point, the iron/feathers
  trick, the 10 %-brain myth, Rayleigh scattering, the Sun's colour) is
  correct and cleanly phrased.
- **`gemma4:e2b`: 2.5 PL / 3 EN.** A real regression: **self-contradicts
  on the iron/feathers trick (PL) and on relativity (EN)**, hallucinates
  ("the Sun has a pink hue"), and answers one English question in Polish.
- **`qwen3:4b-instruct`** is the *only* model that gets relativity right
  ("an outdated interpretation of relativity") and scores highest on the
  truth set — but its everyday **Polish grammar is garbled** ("maszyna",
  "piętra", "Bobyłoby", non-words), which disqualifies it as the
  bilingual model regardless.
- **`qwen3.5:4b`** hallucinates ("~20 % of the brain") and breaks
  language mirroring twice (EN→PL).
- **Both Bielik variants** produce **empty completions** on 4–5 of 6 PL
  prompts and garble the physics — a reliability failure, confirming
  R0003.

**Net:** no model both fixes the relativity phrasing *and* keeps
`gemma4:e4b`'s clean, reliable Polish. The physics-precision gain
(`qwen3:4b-instruct`) and the everyday-Polish quality (`gemma4:e4b`) are
in different models.

## CONTEXT / CONVERSATION RESULTS

5-turn threads, PL and EN (bread-baking; final turn asks the model to
recall a detail from turn 2).

- **gemma4:e4b** — PL and EN both: coherent across all 5 turns, advice
  builds on prior turns, and **turn 5 recalled "mąka pszenna chlebowa oraz
  trochę żytniej" / "bread wheat flour and rye flour" exactly.** The
  2-turn `followup` ("Jak ona powstaje?" / "How does it form?") resolved
  the pronoun to the black hole correctly in both languages. **5/5.**
- **gemma4:e2b** — same: coherent, and **turn 5 recalled both flours
  correctly in both languages.** Slightly terser advice; one difference
  of substance (estimated total bake time 2–3 h vs e4b's 4–6 h — e4b's is
  the better answer). **5/5 on retention**, ~4/5 on advice depth.

Mixed PL↔EN switching inside one thread was **not** tested this stage (the
task scoped it to provider-text only and explicitly deferred the
bilingual auto-STT architecture — see BILINGUAL FUTURE).

## VOICE POLICY RESULTS

Same `voice_response_directive()` for every model — not re-tuned per model
(task requirement).

- **gemma4:e4b** — ordinary questions 86–320 chars (1–3 sentences), no
  markdown lists in the core set; `wyjaśnij dokładnie` expanded to 622 PL
  / 838 EN chars (detail override intact). Compliant.
- **gemma4:e2b** — even tighter: ordinary answers 54–241 chars, uniformly
  1–3 sentences, no lists. The same directive works unchanged. Compliant.
- **qwen3:4b-instruct** — honours the length policy (short answers) but
  leaks Markdown structure (`  \n` hard line breaks, occasional bullet
  fragments) that the B.3.3 directive alone does not suppress on this
  model — the speech planner's Markdown→prose pass (R0015) would still
  need to catch it. Partially compliant.
- **qwen3.5:4b** — length OK; occasional "Think of them as cosmic
  vacuum…" filler openers. Mostly compliant.

The B.3.3 policy is **portable to the Gemma family unchanged**; on the
Qwen models it controls *length* but not *Markdown*, so a model switch off
Gemma would lean harder on the speech planner's normalisation.

## CONTINUITY WARNING RESULT

**Root cause (trivial, isolated):** `NexaSpeechContinuityController._hold_
then_release`, when the bounded hold expired *naturally*, called
`_release_held`, which asked Pipecat's task manager to cancel
`self._hold_task` — the task it was running inside. Pipecat detects the
self-cancel, logs `ignoring attempt to cancel the running task`, and does
nothing. The phrase was still released correctly (`HOLD_EXPIRED`); the
only symptom was one log line. **Not** a functional bug, **not** a
redesign.

**Fix** (`src/nexa/voice_tts/continuity.py`, committed separately as
`fix: stop continuity hold task cancelling itself`, `c64b66b`):
`_hold_then_release` clears `self._hold_task` before calling
`_release_held`; `_release_held` additionally skips `cancel_task` when the
target is `asyncio.current_task()`. The early-flush paths (a later phrase,
turn end, interruption), where the hold task really is still sleeping,
still cancel it. **+2 deterministic regression tests** (`TestHoldTask
Lifecycle`) that use a real `asyncio.sleep` so the task is genuinely
running when it releases: natural expiry does not cancel its own task; an
early flush still does. Full suite 435 passed / 7 skipped; ruff +
`git diff --check` clean.

This is a controller-lifecycle fix and is **independent of** every LLM
benchmark conclusion in this report.

## BEST CONFIGURATION

**For `gemma4:e4b` (no model change): `num_thread = 2` + keep the model &
prefix warm.**

- **`num_thread = 2`** — confirmed in **two** independent runs. Sweep
  (2 reps): baseline 3.30 → `num_thread=2` 3.76 (**+14 %**), `=3` 3.59
  (+9 %). Focused 5-rep back-to-back run
  (`bench_serving_confirm_20260907.json`): stabilised baseline 3.38 →
  `=2` **3.77 (+12 %)**, `=3` 3.65 (+8 %), **`=1` 2.88 (−15 %)** (a single
  thread is much slower — 2 is the floor). So the effect is **+12–14 %
  tok/s** and robust; `num_thread=2` is the optimum on this 4-core Pi.

  It is a per-request Ollama `options` field, but changing it mid-session
  forces a `llama-server` reload (the big TTFT spikes in the confirmation
  raw are that reload) — so `LocalModelProvider` should set it **once at
  init**, not per turn. **No `sudo`, no system change.** Recommended as a
  provider-config change (not shipped in B.3.4); re-check against a real
  Piper-active turn first (R0014's CPU topology — Piper `nice +10` +
  2-thread LLM should coexist comfortably on 4 cores).
- **`num_batch` / `num_ctx`** — no effect on decode speed. Leave
  `num_ctx = 8192` (persona + directive + a long thread needs it);
  dropping to 2048 saves memory but does not speed generation.
- **`flash_attn`** — already on (`llama-server --flash-attn auto`, R0014);
  it is a server-launch flag, not a per-request option, so not tunable
  here without an `ollama serve` restart (out of scope — no system
  change).
- **Warm residency** — `keep_alive` (currently `5m`) governs whether the
  next turn pays the ~33 s reload + ~29 s cold-prefix eval or runs in
  2–3 s. For an always-on Pi assistant, raise it (`30m`+ or `-1`) and/or
  add a startup + post-idle warm-up call that also primes the
  persona+directive prefix. Operational change, recorded, not shipped.

**None of this closes the Polish gap on its own** — `num_thread = 2` takes
`e4b` PL from ~59 % to ~68–74 % of the 15.9 target. It is worth doing;
it is not a substitute for the model question.

## BEST MODEL CANDIDATE

**`gemma4:e2b`** — the only shortlist model that is both meaningfully
faster *and* still conversationally Polish-capable. ~2× `e4b` throughput
in both languages, **clears the 15.9 chars/s Polish target (~18.7 core /
~21 dense-prose)**, warm TTFT ~1.2 s (meets ≤ 1.5 s), 2.7 GB lighter,
same MatFormer family so the B.3.3 voice policy and the persona port
**unchanged** (confirmed — the directive needed no re-tuning).

**Its cost is real and repeatable, not cosmetic:** self-contradictions on
a trick question and on relativity, a hallucination ("pink Sun"), one
English question answered in Polish, and thinner reasoning working. On the
everyday conversational axes (naturalness, brevity, context retention) it
is genuinely close to `e4b`; on the *reliability* axes it is not.

It is a **Pareto point** — "2× the speed for a step down in factual
reliability" — that only the operator can accept or reject, in a live A/B.
It is **not** a model that can be swapped in on benchmark evidence alone.

## DOES ANY CANDIDATE MEET THE ~15.9 CHARS/S TARGET?

**Polish (the binding constraint):**

| model | PL chars/s (core / fixed) | meets 15.9? |
|---|---|---|
| gemma4:e4b | 9.4 / 11.7 | **no** (~59–74 %) |
| gemma4:e4b + `num_thread=2` | ~11 / 13.5 | no (~68–85 %) |
| **gemma4:e2b** | **18.7 / 21.3** | **yes** |
| qwen3:4b-instruct | 9.2 / 11.1 | no |
| qwen3.5:4b | 9.9 / 9.8 | no |
| qwen2.5:3b | (spot) / 14.4 | borderline (~90 %) — but PL quality (spot-check) |
| llama3.2:3b | (spot) / 12.9 | no |

**Only `gemma4:e2b` clears the Polish target.** `qwen2.5:3b` gets close
on raw throughput but its Polish quality is the open question (spot-check
below). **English:** `e2b`, `qwen2.5:3b`, `llama3.2:3b` all clear it;
`e4b` sits at ~92–95 % — English was never the binding constraint.

## DOES ANY CANDIDATE BEAT GEMMA4:E4B WITHOUT QUALITY LOSS?

**No.** `gemma4:e2b` is ~2× faster with comparable *conversation* quality
and identical context retention, but with **repeatable** factual
self-contradictions, a hallucination, a language-mirroring break, and
thinner reasoning — a genuine step down on reliability, not noise. Every
other faster-or-equal model has worse Polish (garbled grammar: qwen 4B;
empty completions: Bielik). **The speed win and the `e4b`-level Polish
quality do not exist in the same model on this hardware today.**

## FILES CHANGED

| file | change |
|---|---|
| `src/nexa/voice_tts/continuity.py` | **fix** (separate commit `c64b66b`) — hold task no longer self-cancels |
| `tests/test_voice_tts_continuity.py` | **+2 tests** (`TestHoldTaskLifecycle`), separate commit |
| `docs/reports/R0021_…md` | **new** — this report |
| `docs/research/m2_4b_llm_bench/bench_llm.py` | **new** — benchmark harness (throwaway; builds the real B.3.3 wire messages, POSTs to Ollama) |
| `docs/research/m2_4b_llm_bench/run_all.sh` | **new** — chained batches B–F |
| `docs/research/m2_4b_llm_bench/nt_confirm.py` | **new** — focused `num_thread` confirmation |
| `docs/research/m2_4b_llm_bench/bench_baseline_e4b_…json/.log` | **new** — `gemma4:e4b` cold + core + science |
| `docs/research/m2_4b_llm_bench/bench_e2b_…json` | **new** — `gemma4:e2b` cold + core + science |
| `docs/research/m2_4b_llm_bench/bench_serving_e4b_…json` + `bench_serving_confirm_…json` | **new** — serving sweep + `num_thread` confirmation |
| `docs/research/m2_4b_llm_bench/bench_alt_…json` | **new** — `qwen3:4b-instruct`, `qwen3.5:4b` |
| `docs/research/m2_4b_llm_bench/bench_fixed_…json` | **new** — fixed-length throughput, 8 models |
| `docs/research/m2_4b_llm_bench/bench_bielik_science_…json` + `bench_smallpl_…json` | **new** — Bielik science + `qwen2.5:3b`/`llama3.2:3b` PL spot-check |
| `docs/research/m2_4b_llm_bench/README.md` | **new** |
| `docs/CURRENT_STATE.md` | B.3.4 benchmark result recorded; next-step |

No `src/` change other than the isolated continuity fix. `gemma4:e4b`
stays the frozen production default. No `num_predict` / persona / voice
directive / STT / TTS / scheduling change.

## COMMIT HASH

- `c64b66b` — `fix: stop continuity hold task cancelling itself`
  (continuity self-cancel + 2 regression tests).
- `research: benchmark local LLM serving & voice performance` — tip of
  `main` (this report + harness + raw data + `CURRENT_STATE`; see
  `git log -1`).

## GIT STATUS

Branch `main`, **14 commits ahead of `origin/main`, not pushed**. Both
commits above are on `main`. `git diff --check` clean; working tree clean.
No model weights or `~/.ollama` blobs staged. Ollama left with **no model
resident** (every benchmarked model `ollama stop`ped). Full test suite
**435 passed / 7 skipped** (`unittest discover` 442 OK); `ruff` clean.
No `src/` change other than the isolated continuity fix.

## RECOMMENDATION

1. **Do not switch the production model in this stage** — as instructed.
   `gemma4:e4b` remains the ADR-0002 frozen baseline.
2. **`gemma4:e2b` is the recommended candidate for an operator A/B blind
   session** (the same instrument as M1.0B's blind test). It is the only
   option that turns R0018's Polish continuity deficit from "~53 % of
   target" into "target met", at half the latency and 2.7 GB less RAM,
   with the B.3.3 policy unchanged. The operator must judge whether its
   thinner reasoning and the science-precision gap are acceptable for
   NeXa's role — that is a quality call, not a benchmark call.
3. **Take the two free wins regardless of the model choice** (both
   provider-config, no `sudo`, recorded as a follow-up — not shipped in
   B.3.4):
   - `LocalModelProvider` sets `options.num_thread = 2` (Ollama
     per-request) → +14 % tok/s / +19 % PL chars/s on `gemma4:e4b`
     (`e2b` benefits similarly). Re-check it against a real Piper-active
     turn (R0014 CPU topology) before committing.
   - Keep the model + preamble prefix warm — raise `keep_alive` and/or a
     startup + post-idle warm-up call that primes the persona+directive
     prefix — so first-turn TTFT is 2–3 s, not ~29–33 s.
4. If `e2b`'s quality is judged insufficient, the honest position is:
   **there is no free lunch on this Pi** — Polish long-form continuity at
   `e4b` quality needs either a slightly slower voice + shorter replies
   (already shipped via B.3.2/B.3.3, gets the common case), or hardware /
   a future better small model. Keep watching the Gemma / Qwen small-model
   space.

## NEXT STEP

Operator A/B blind session `gemma4:e4b` vs `gemma4:e2b` on real Polish +
English voice (M1.0B blind-test instrument). In parallel, non-blocking:
the `keep_alive` / prefix-warm operational change (#3 above). Then, once
a model is settled, **M2.5 — barge-in** (replaces the temporary
half-duplex gate). The bilingual auto-STT architecture (BILINGUAL FUTURE
below) is recorded, not started.

**Do not switch the production model. Do not implement bilingual auto-STT.
Do not start M2.5. Do not push.**

## BILINGUAL FUTURE (recorded, NOT implemented)

Planned next voice-language architecture — **one `ConversationSession`**,
per utterance:

```
audio → language identification → language-aware STT → canonical transcript
      → response-language resolver (answer follows the current utterance's
        language unless the user explicitly asks otherwise)
      → ConversationSession.send(...)  → corresponding TTS voice
```

Target UX: PL utterance → PL answer; EN utterance → EN answer; PL→EN→PL
switching inside one conversation with no restart. Explicit override wins
("Odpowiedz po angielsku", "Answer in Polish", "Od teraz mów po
angielsku", "Wracamy do polskiego"). Code-switch / mixed-language
utterances are a separate later stage. **Not implemented in B.3.4** — the
response-language resolver already exists for typed+voice
(`conversation.language`, R0009); the missing pieces are audio language
ID and language-aware STT selection, which touch M2.2's STT layer and are
out of this stage's scope.

## AGENTS.md: REVIEWED — NO CHANGE REQUIRED

Measurement over assumption: every number from the real voice path +
Ollama done-chunk metrics; prior R0003 evidence reused rather than
re-run; the cross-language chars/s pitfall surfaced explicitly rather
than hidden behind tok/s; the `e2b` result stated as a Pareto point with
its quality caveat, not oversold as a winner; no production model
switched; the continuity fix kept separate from the benchmark
conclusions. No gap exposed.
