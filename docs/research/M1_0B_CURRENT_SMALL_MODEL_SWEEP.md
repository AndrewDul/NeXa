# M1.0B — Current Small-Model Sweep

Milestone: **M1.0B — Current Small-Model Sweep.** Research + empirical
benchmark only. No product code. Supersedes/qualifies parts of M1.0
(`docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md`) with fresher evidence —
**M1.0's findings are not rewritten**, they are the historical baseline this
milestone measures against. Truth labels per `AGENTS.md` §4.

Date: 2026-09-01. Host: Raspberry Pi 5 16GB (`nexa`), same machine as M1.0.

---

## 1. Why this milestone exists

M1.0 (2026-08-31) established `qwen3:4b-instruct` as the baseline against a
field of models available *at that time*. Model ecosystems move fast — by
2026-09-01, newer model families exist (Qwen3.5, Gemma 4) that did not exist
when M1.0 ran. M1.0B re-opens the model question with current evidence,
without assuming the newest model automatically wins (`AGENTS.md` "most
important principle": real conversation + real Pi performance + real Polish +
real English + real context behaviour + real reliability decide, not
leaderboards or vendor claims).

## 2. Starting state (verified 2026-09-01)

- Repo `AndrewDul/NeXa`, branch `main`, HEAD `33f0d89` (research: establish
  natural conversation baseline) — 1 commit ahead of `origin/main`, clean
  working tree.
- Pi: idle 49.9 °C, `throttled=0x0`, 798 GB free / 917 GB SSD, 14 GiB RAM
  available.
- Ollama: **0.30.10** at session start.

## 3. Ollama upgraded — `VERIFIED FACT`, load-bearing

`ollama --version` was **0.30.10**, unchanged since M1.0. External research
(§4) found that Qwen3.5's hybrid Gated DeltaNet attention needs Ollama
**≥ 0.17.4** (0.30.10 already qualifies) but that Gemma 4 support and its
later tool-calling/multi-turn fixes landed across the 0.3x series, and the
current upstream release was **0.33.1** (2026-08-26). To avoid silently
benchmarking new architectures on a stale runtime — the single highest-risk
unknown in this sweep — the official installer
(`curl -fsSL https://ollama.com/install.sh | sh`) was run once. Result:
**Ollama 0.33.2**, `ollama.service` active, all 7 pre-existing local models
intact (verified via `ollama list` before/after). This is the only
system-level change made outside the repo in M1.0B; it is reversible
(re-run the installer with an older release, or `apt`/binary swap) and
required for valid Qwen3.5 / Gemma 4 measurements.

## 4. External research (verified 2026-09-01, before any download)

Per the sweep's research rule: nothing below was assumed from the task
prompt without a live check. Community sources are labelled `OBSERVATION`.

### 4.1 Qwen3.5-4B / Qwen3.5-2B

- `VERIFIED FACT` (Qwen HF model cards, Qwen blog, Alibaba_Qwen announcement):
  Qwen3.5 is a real, current family. Small models (`0.8B/2B/4B/9B`) shipped
  **2026-03-02**. Qwen3.5-4B/2B are **natively multimodal** (text+image),
  hybrid architecture — Gated DeltaNet (linear attention) in ~75% of layers,
  full attention in the rest, plus sparse MoE routing — 262k native context
  (extensible to 1M), 201 languages claimed.
- `VERIFIED FACT`: **thinking mode is ON by default** for Qwen3.5 (`<think>…
  </think>` before the final answer); recommended sampling for both thinking
  and non-thinking modes: `temperature 1.0, top_p 0.95, top_k 20, min_p 0.0,
  presence_penalty 1.5, repetition_penalty 1.0` (`OBSERVATION`, community
  Unsloth/HF discussion — Ollama's own Modelfile defaults were used instead
  where they existed, see §7).
- `VERIFIED FACT`: `llama.cpp` needs a build with Gated-DeltaNet support; a
  recent Ollama (`≥ 0.17.4`) is required — our upgraded 0.33.2 qualifies.
- `VERIFIED FACT` (Ollama library page): official tags exist —
  `qwen3.5:4b` (3.4 GB), `qwen3.5:2b` (2.7 GB). Sizes are larger than a
  pure-text Q4_K_M 4B/2B (`qwen3:4b-instruct` Q4_K_M is 2.5 GB) —
  **caveat**: these tags likely bundle a vision encoder even though M1.0B
  only exercises the text path; exact quant confirmed post-pull via
  `ollama show` (§7).
- `VERIFIED FACT`: **no Qwen3.6 or Qwen3.8 small (≤9B) release exists** as of
  2026-09-01 — those generations stayed at 27B/35B/9B/Max tiers. Qwen3.5-4B/2B
  remain the current smallest/newest Qwen3.5+ models. No substitution needed.

### 4.2 Gemma — substituted Gemma 4 for the prompt's named Gemma 3 / Gemma 3n

- `VERIFIED FACT`: **Gemma 4** released **2026-04-02**, Apache-2.0 (a real
  license improvement over Gemma 3's custom Gemma license), day-one
  `llama.cpp` support, sizes `E2B / E4B / 12B / 26B-A4B / 31B` (12B added
  2026-06-03). This directly supersedes the task prompt's named "Gemma 3 4B
  IT" and "Gemma 3n E2B" — both are now one generation behind, on a more
  restrictive license, with less current tooling support.
- **Deliberate substitution, documented per the research rule**: M1.0B
  benchmarks **Gemma 4 E2B and E4B** instead of Gemma 3 / Gemma 3n. This is
  the same conceptual slot the prompt asked for (a "4B-class Pi candidate"
  and an "edge/mobile candidate"), filled with the actually-current model.
- `VERIFIED FACT` (Ollama library page): official tags `gemma4:e2b` (7.2 GB),
  `gemma4:e4b` (9.6 GB) — **much larger than expected for "2B/4B"**. Gemma 4
  E2B/E4B, like Gemma 3n before it, use a MatFormer-style effective-vs-raw
  parameter split (the "E" stands for *effective* parameters; raw parameter
  count and on-disk size are larger) and ship multimodal (image/audio) by
  default under the bare tag — this is flagged as a real Pi-practicality risk
  and tested empirically (§9) rather than assumed.

### 4.3 Bielik-4.5B-v3.0-Instruct — Q4_K_M does **not** exist officially

- `VERIFIED FACT` (direct file listing,
  `huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct-GGUF`): SpeakLeash
  publishes **exactly two** GGUF files — `Bielik-4.5B-v3.0-Instruct-fp16.gguf`
  (9.52 GB) and `Bielik-4.5B-v3.0-Instruct.Q8_0.gguf` (5.06 GB). **No official
  Q4_K_M.** This confirms M1.0's original finding and directly contradicts an
  initial (incorrect) web-search summary that claimed a 2.88 GB Q4_K_M lived
  in the official repo — the raw file listing is authoritative, the search
  summary was wrong, and is not used.
- `VERIFIED FACT` (direct file listing,
  `huggingface.co/second-state/Bielik-4.5B-v3.0-Instruct-GGUF`): Second
  State publishes a **third-party requantization** (quantized with
  `llama.cpp b5201`, not a SpeakLeash release) covering `Q2_K` through
  `f16`, including **`Q4_K_M` at 2.88 GB**. Downloaded and imported locally
  (§7) — labelled **THIRD-PARTY / COMMUNITY** everywhere it appears in this
  report, per the task's explicit instruction not to claim SpeakLeash
  officially ships Q4_K_M.
- `VERIFIED FACT` (`ollama show SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0
  --modelfile`, ground truth from the locally-installed official tag):
  ChatML-adjacent Llama-3-style template (`<|start_header_id|>` /
  `<|end_header_id|>` / `<|eot_id|>`), single baked sampling parameter
  `temperature 0.1` (no top_p/top_k override — Ollama global defaults apply
  to those). This exact template + parameter set was reused verbatim for the
  local Q4_K_M import (§7), so the only variable between the two Bielik
  variants under test is the quantization.

### 4.4 Phi-4-mini-instruct

- `VERIFIED FACT`: MIT license; 3.8B; 128k context; Polish is one of ~20
  supported languages (Arabic, Chinese, Czech, Danish, Dutch, English,
  Finnish, French, German, Hebrew, Hungarian, Italian, Japanese, Korean,
  Norwegian, Portuguese, Russian, Spanish, Swedish, Thai, Turkish, Ukrainian,
  **Polish**). Official Ollama library tag `phi4-mini:3.8b` exists.

### 4.5 PLLuM — investigated, not benchmarked

- `VERIFIED FACT` (HF `CYFRAGOVPL/*`): PLLuM is a real, credible Polish model
  family (consortium of Polish research institutions, 140B-token Polish
  corpus), a genuine second Polish-specialist family distinct from Bielik.
- `OBSERVATION` (Ollama library, community `PRIHLOP/PLLuM` tags): the
  smallest clearly chat/instruct-tuned tag found is `PLLuM:12b` (~13 GB, no
  disclosed quantization level in the listing); the `8b` tag is base
  (non-instruct), ~16 GB. **No confirmed small Q4-class instruct tag exists.**
  At ~13 GB with unknown quant, this model would be at least as heavy as
  Bielik-Q8_0 (5.1 GB, ~2 tok/s, already below the benchmark's "usable"
  threshold) — likely far below it.
- **Decision**: investigated per the task's requirement, **not** added as an
  active benchmark candidate. Spending Pi-hours on an ambiguously-quantized,
  ~2.5× larger model with a materially worse expected floor than Bielik
  (already the slowest tested-and-borderline-viable model in M1.0) is not a
  good use of the sweep's time budget. Recorded as a candidate worth
  revisiting if/when a reputable small (≤ 4B) PLLuM Q4_K_M GGUF appears.
- No other additional candidate met the "credible reason to win a specific
  NeXa role" bar within the sweep's time budget — the roster below (Qwen3.5
  ×2, Bielik ×2 configs, Phi-4-mini, Gemma 4 ×2, plus the qwen3:4b control)
  is already 7 distinct models across 4 independent families, which is a
  substantial current-generation sweep on its own.

## 5. Final candidate roster (Phase 1 — fast screen)

| # | Model | Params | License | Quant | Source | Role hypothesis |
|---|---|---|---|---|---|---|
| 0 | `qwen3:4b-instruct` | 4.0B | Apache-2.0 | Q4_K_M (official) | Ollama (already local) | **control** — M1.0 baseline |
| 1 | `qwen3.5:4b` | 4B (multimodal, hybrid) | Apache-2.0 (Qwen3.5 license — verify exact text at pull time) | official Ollama default | Ollama pull | primary new challenger |
| 2 | `qwen3.5:2b` | 2B (multimodal, hybrid) | Apache-2.0 | official Ollama default | Ollama pull | fast challenger |
| 3 | `SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0` | 4.8B | Apache-2.0 | Q8_0 (official) | Ollama (already local) | Polish specialist — **recommended-sampling retest** (temp 0.1, was temp 0.7 in M1.0) |
| 4 | Bielik-4.5B-v3.0-Instruct Q4_K_M | 4.6B | Apache-2.0 | Q4_K_M (**third-party**, Second State) | HF download + local `ollama create` | Polish specialist — speed retest |
| 5 | `phi4-mini:3.8b` | 3.8B | MIT | official Ollama default | Ollama pull | independent (non-Qwen, non-Bielik) control |
| 6 | `gemma4:e2b` | "E2B" effective (raw larger) | Apache-2.0 | official Ollama default | Ollama pull | edge/mobile-relevant candidate |
| 7 | `gemma4:e4b` | "E4B" effective (raw larger) | Apache-2.0 | official Ollama default | Ollama pull | Pi-class Gemma candidate |

Deliberately excluded from active benchmarking (investigated only):
PLLuM (§4.5); Qwen3.6/3.8 (no small variant exists, §4.1); Gemma 3 / Gemma 3n
(superseded by Gemma 4, §4.2); Bielik-1.5B (M1.0 already covers the
latency-floor role via `qwen2.5:1.5b`; not re-justified here).

## 6. Methodology

### 6.1 Phase 1 — fast screen (this document, in progress)

Per model: perf micro-benchmark (cold + 3× warm, ~200 tokens, fixed English
prompt) + `CONV-PL-S1` + `CONV-EN-S1`, **at each model's own recommended
settings** — `--bare-options`: only `num_ctx` (8192 conversation / 4096 perf)
and `num_predict` (200) are forced; `temperature`/`top_p`/`top_k`/
`repeat_penalty` are left to the model's Ollama Modelfile defaults (which for
`qwen3:4b-instruct` are, empirically, temp 0.7/top_p 0.8/top_k 20 — i.e. the
same as M1.0's "standardized" case-file settings, so the control run stays
directly comparable to M1.0). Non-thinking mode (`think: false`) is forced
for every model whose `ollama show` capabilities list `thinking` (Qwen3,
Qwen3.5; checked per-model, not assumed for Gemma 4 / Phi-4-mini). This is a
deliberate simplification of the prompt's full standardized-vs-recommended
matrix for the *fast screen* stage only — the point of Phase 1 is to find
genuine finalists, so no candidate is handicapped by force-fitting shared
settings it wasn't tuned for. The full standardized-vs-recommended
comparison, the naturalness stress session, honesty checks, persona A/B,
long-context decay, and the Ollama-vs-llama.cpp head-to-head are all reserved
for Phase 2 finalists only, per the operator's explicit two-phase scope.

Harness: `scripts/m1_bench/bench.py`, extended this session with
`--bare-options`, `--temperature/--top-p/--top-k/--repeat-penalty/
--num-predict` CLI overrides, `--system-file` (persona swap), and
`--think/--no-think` (maps to Ollama's top-level `think` field). Driver:
`scripts/m1_bench/run_phase1.sh` (auto-detects thinking capability per model,
probes temp/RAM/throttle before and after each model, no self-matching
`pgrep` watchers — see
`docs/troubleshooting/20260831_m1_bench_watchers_and_bielik_detection.md`).

New stable case: `CONV-NATURAL-S1`
(`scripts/m1_bench/cases/conversation_natural.json`, 18 turns) — the
naturalness stress session (§9 of the task), run for Phase 2 finalists only.

### 6.2 Ranking → finalist selection

After Phase 1, all 7 non-control candidates (plus the control) are ranked on
the actual NeXa criteria the operator specified: natural conversation
quality (agent-assisted, against transcripts), Polish, English, latency,
reliability (no crashes/timeouts/truncation-to-nothing), factual restraint,
Pi practicality (RAM/thermal/tok/s). The top 2–3 become Phase 2 finalists.
Disqualifying caveats (e.g. a model that truncates to empty answers, or is
below ~1 tok/s) are applied explicitly, not hidden inside the arithmetic —
per the task's weighted-score guidance.

---

## 7. Interruption & resume — `VERIFIED FACT`

Phase 1's original single continuous run (2026-09-01, `phase1_log.txt`) covered
5 of 8 roster candidates (control + `qwen3.5:4b`, `qwen3.5:2b`, `phi4-mini:3.8b`,
`gemma4:e2b`) cleanly, then stopped mid-way through `gemma4:e4b`'s first perf
run with no error captured, no completion marker, and no downstream Phase 2
work started. `MemAvailable` had drained from ~11.2 GB (session start) to
~3.0 GB by the time `gemma4:e4b` (a 9.6 GB tag) began loading — the most likely
cause, though the Pi rebooted before this document's audit (2026-09-02) and no
system log survives from the original window to confirm the exact mechanism
(OOM kill vs. crash vs. session end). This is disclosed, not hidden — see the
resume audit that recovered the run. The remaining 3 candidates
(`gemma4:e4b`, both Bielik variants) were completed 2026-09-02 in a fresh,
low-memory-pressure session (`phase1_resume_log.txt`), each run in isolation
(previous model explicitly unloaded first, RAM/temp/throttle/swap probed
before and after every model) per the operator's explicit resume-safety
instructions. No candidate was retried after a failure; `gemma4:e4b` completed
cleanly on its one attempt.

## 8. Phase 1 — performance results (machine-measured)

Perf micro-benchmark: cold + 3× warm, ~200 tokens, fixed English prompt,
`--bare-options` (model's own Modelfile sampling defaults).

| Model | gen tok/s (mean) | Warm TTFT (s) | Cold load (s) | Peak temp °C | Model RAM (resident) |
|---|---|---|---|---|---|
| `qwen3:4b-instruct` (control) | 2.96 | 0.37 | 13.4 | 67.2 | ~3.9 GB |
| `qwen3.5:4b` | 3.03 | 0.45 | 15.8 | 67.8 | ~4.3–4.5 GB |
| `qwen3.5:2b` | 4.54 | 0.27 | 12.7 | 68.3 | ~3.6–3.7 GB |
| `phi4-mini:3.8b` | 4.12 | 0.27 | 12.7 | 69.4 | ~4.0 GB |
| `gemma4:e2b` | 6.58 | 0.30 | 25.3 | 68.8 | ~7.4 GB |
| `gemma4:e4b` | 3.11 | 0.72 | 32.4 | 66.7–68.8 | ~10.1 GB |
| Bielik Q8_0 (temp 0.1 retest) | 2.04 | 0.54 (perf run) | 21.1 | 67.8–69.4 | ~5.1 GB nominal |
| Bielik Q4_K_M (3rd-party) | 3.77 | 0.28 (perf run) | 12.1 | 68.3–73.2 | ~2.9 GB nominal |

**Anomaly, `OBSERVATION`:** Bielik Q8_0's per-turn TTFT in the *conversation*
runs never dropped below ~8 s, unlike the perf run's warm TTFT (0.5 s) — every
one of 21 conversation turns paid an 8–13 s TTFT, which is "poor" by this
benchmark's own target (§5 of the benchmark doc, ≥4 s = poor). Plausible cause:
Bielik's native context is 8192 (exactly `num_ctx` used here), which may force
full prompt reprocessing each turn instead of KV-cache reuse — not confirmed,
flagged for anyone re-testing Bielik. Both third-party-quant RAM figures read
implausibly low (68–234 MB) versus the official quants (~3.9–10.1 GB) —
this is very likely a bench.py accounting quirk (delta-vs-already-resident
memory) rather than a real number; **not used** for any Pi-practicality
judgement below.

## 9. Phase 1 — quality scores (`AGENT-ASSISTED`, Claude vs. transcripts — NOT operator-confirmed)

Scored against `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md` §4's 1–5
rubric (naturalness, correction handling, recall, honesty, repetition, length
discipline). Every score below is `AGENT-ASSISTED`.

| Model | PL quality | EN quality | Key defects observed |
|---|---|---|---|
| `qwen3:4b-instruct` (control) | 3/5 | 3.5/5 | PL over-lists/lectures on casual venting turns despite persona asking for brevity; 3 turns truncated at `num_predict=200` mid-sentence; EN coherent but emoji-heavy/performative-casual |
| `qwen3.5:4b` | 2/5 | 4/5 | PL: garbled/nonsense words ("towsamistą rzecz", "przetrzebować na szlifu"), hallucinated brand names, self-contradicts its own gift advice turn-to-turn, wrong-ish recall with bad grammar; EN: natural, creative, no defects found |
| `qwen3.5:2b` | 2/5 | **4.5/5** | PL: a literal Chinese-character leak mid-sentence ("已完成"), an alarmist/dramatic tone shift, a nonsense word; EN: best in the roster — concise, warm, proactively helpful, zero defects found |
| `phi4-mini:3.8b` | 1.5/5 | 2.5/5 | PL: a bizarre off-topic non-sequitur turn (an unrelated "adult toys" list in response to "change the subject"), a persona-breaking "Jako AI nie mam uczuć" disclaimer; EN: generic boilerplate assistant tone throughout, and a clear recall **error** (named the wrong first topic) |
| `gemma4:e2b` | 3.5/5 | 4/5 | PL: best brevity/naturalness in the roster, but **fails** the turn-8 "one sentence" instruction (asks a meta-question back instead of complying); EN: very concise, occasionally too curt to feel warm, otherwise clean |
| `gemma4:e4b` | **4/5** | 4/5 | Cleanest transcript in the whole roster in both languages — correct recall, correctly complies with the "one sentence" instruction e2b missed, coherent disagreement handling, no garbling, no persona breaks |
| Bielik Q8_0 (M1.0B temp-0.1 retest) | 2.5/5 | 1.5/5 | PL: grammatically native but shallow and **repetitive** (near-identical short answers 3 turns running), ignores "bass" specificity in the gift question; EN: opens with a flat **non-sequitur** ("I need to find a hotel in New York City"), overclaims capability twice ("I'll call for you"), TTFT never warms up (§8) |
| Bielik Q4_K_M (3rd-party) | 2/5 | **1/5** | PL: even more repetitive (same 3-item list verbatim 3 turns running), wrong recall; EN: **2 of 10 turns are empty completions**, the rest is a stuck, incoherent "I would ..." template loop disconnected from what the user actually said — a reliability failure, not just a quality one |

## 10. Phase 1 — ranking and finalist selection

Ranked on natural conversation quality, Polish, English, latency, generation
speed, reliability, factual restraint, and Pi practicality (RAM/thermal/tok/s)
— per the operator's explicit instruction, **not** on generic benchmark
cleverness or newest-generation halo.

- **BEST OVERALL: `gemma4:e4b`** — the only model with zero quality defects
  found across both full transcripts (correct recall both languages, correct
  instruction-following, no garbling, no persona breaks, coherent
  disagreement handling).
- **BEST POLISH: `gemma4:e4b`** — grammatically clean *and* content-rich *and*
  instruction-compliant; both Bielik variants are nominally native-grammar but
  shallow/repetitive/wrong-recall, which the rubric weighs as a real quality
  defect, not just a style nit.
- **BEST ENGLISH: `qwen3.5:2b`** — most natural, warmest, most proactively
  useful English of the roster, with zero defects found, at the lowest RAM
  footprint of any credible finalist.
- **FASTEST: `gemma4:e2b`** — highest sustained generation throughput
  (6.6–7.2 tok/s), well above every other candidate.
- **MOST NATURAL: `gemma4:e2b`** — the tersest, least "assistant-sounding"
  phrasing in the roster; marred only by one instruction-following miss.
- **BEST PI BALANCE: `qwen3.5:2b`** — smallest RAM footprint of any
  finalist-quality candidate (~3.6–3.7 GB), strong speed, and by far the best
  English, at the cost of Polish needing persona work.

**Disqualified from finalist status, with reasons:**
- **Bielik Q4_K_M (3rd-party)** — reliability failure: 2 of 10 English turns
  are empty completions, and the rest is an incoherent repetitive template
  loop. Faster and lighter than Q8_0 on paper, but not usable.
- **Bielik Q8_0 (official)** — the M1.0B recommended-sampling (temp 0.1)
  retest does **not** fix the English brokenness or the TTFT-never-warms
  anomaly found in M1.0; Polish is native-grammar but shallow/repetitive.
  Confirms ADR-0002's original D4 finding rather than overturning it.
- **`phi4-mini:3.8b`** — a genuine off-topic non-sequitur and a persona-break
  in Polish, plus a clear recall error and generic boilerplate tone in
  English; MIT license and good raw perf numbers do not offset this.
- **`qwen3.5:4b`** — Polish is more broken than the `qwen3:4b` control it was
  meant to challenge (garbled words, hallucinated brands, self-contradiction);
  no criterion favours it over `qwen3.5:2b` or the Gemma pair.
- **`qwen3:4b-instruct` (incumbent baseline)** — solid and reliable, but no
  single criterion above points to it over at least one finalist; kept as the
  continuity baseline for the Phase 2 head-to-head, not carried forward as an
  active finalist.

**Finalists selected for Phase 2 (automatic, no tie requiring operator input):**

1. **`gemma4:e4b`** — best overall / best Polish
2. **`gemma4:e2b`** — fastest / most natural / lighter alternative within the
   same model family
3. **`qwen3.5:2b`** — best English / best Pi-practicality balance; carried
   forward despite weaker Polish specifically to test whether Phase 2's
   persona swap and mix/honesty/long-context sessions change that picture.

This spread deliberately covers the heaviest-best-quality option, the
fastest-lightest-of-the-strong-family option, and the lightest-best-English
option, so the Phase 2 head-to-head has a real trade-off to resolve rather
than three near-duplicates.

## 11. Ollama vs. llama.cpp — `NOT RUN`, blocked

Attempted for the top 2 finalists (`gemma4:e4b`, `gemma4:e2b`) using the
existing compiled `llama-cli` binary at
`/home/devdul/Projects/smart-desk-ai-assistant/llama.cpp/build/bin/llama-cli`
(legacy repo, read-only use, no new build) against the exact GGUF blobs Ollama
already has on disk for these two tags (no new download). Blocked: Ollama's
blob store (`/usr/share/ollama/.ollama/models/blobs/`) is mode `0700`, owned
by the `ollama` service account, unreadable by the operator's user even though
it is in the `ollama` group. Reading it would require `sudo` to reach into
another service account's private store — a privilege-escalation step this
recovery run does not treat as pre-authorized, so it was not taken
unilaterally. Recorded as a **blocked** item for an explicit operator decision
(grant read access to the blob dir, or export a GGUF through `ollama`'s own
tooling), not silently skipped.

---

## 11A. Phase 1 recovery audit — `VERIFIED FACT` (2026-09-02, second recovery pass)

A second recovery pass on 2026-09-02 (this session) re-checked every Phase 1
artifact before any new run. Result: **Phase 1 is complete and valid for all 8
roster models.** The task hand-off audit listed `gemma4:e4b`, Bielik Q8_0, and
Bielik Q4_K_M as "missing / incomplete" — that reflected the *interrupted*
`phase1_log.txt` only. `phase1_resume_log.txt` shows all three were finished
cleanly on 2026-09-02, each run in isolation with the previous model unloaded
and temp/RAM/throttle/swap probed before and after. Verified per file:

- Every `CONV-*` JSON has the expected turn count and **zero empty completions**
  except where noted as a genuine model failure (Bielik Q4_K_M EN turns 2–3,
  §9). Every `perf_*` JSON has 3 runs with populated timing.
- `gemma4:e4b` ran **stably alone** on its one resumed attempt — peak 68.8 °C,
  `throttled=0x0`, swap 0 MB, `MemAvailable` steady ~4.5 GB throughout. The
  original interruption was memory pressure (a 9.6 GB tag loading with ~3.0 GB
  free after `gemma4:e2b`), not a model defect.

**No Phase 1 model was re-run.** All raw files and transcripts are reused
unchanged. This recovery pass added only the missing **Phase 2** work (§12–§17).

## 12. Phase 2 — performance results (machine-measured)

Finalists only, `--bare-options` (each model's own Modelfile sampling), 8192
conversation context. `gemma4:e4b` Phase 2 ran 2026-09-02 (`phase2_log.txt`);
`gemma4:e2b` and `qwen3.5:2b` ran 2026-09-02 in this recovery pass
(`phase2_resume_log.txt`, hardened driver `run_phase2_resume.sh` — explicit
`ollama stop` between models, plus a MemAvailable<1.5 GB / swap>512 MB abort
guard; no guard trip occurred, swap stayed 0 MB, peak 69.4 °C, no throttling).

| Finalist | conv gen tok/s (range across P2 sessions) | warm TTFT (conv) | cold load | peak °C | model RAM (resident) |
|---|---|---|---|---|---|
| `gemma4:e4b` | 3.1–3.4 | 1.3–5.0 s | 30–33 s | 70.0 | ~10.0–10.2 GB |
| `gemma4:e2b` | 6.4–7.0 | 0.6–2.6 s | 11–24 s | 69.4 | ~7.4–7.6 GB |
| `qwen3.5:2b` | 4.8–5.1 | 2.0–7.3 s | 7–13 s | 69.4 | ~3.5–3.6 GB |

## 13. Phase 2 — long-context decay (`CONV-NATURAL-S1`, 18 turns, ~1.3–1.9k prompt tokens by the end)

Read from per-turn `ttft_s` / `gen_tps` / `prompt_eval_count` in the
`CONV-NATURAL-S1_*` JSON (no separate decay harness).

| Finalist | gen tok/s turn 2 → turn 18 | Δ | warm TTFT across the session | throttle / swap |
|---|---|---|---|---|
| `gemma4:e4b` | 3.56 → 3.14 | −12 % | 1.3–4.9 s | none / 0 MB |
| `gemma4:e2b` | 6.49 → 6.41 | −1 % | 0.6–2.3 s | none / 0 MB |
| `qwen3.5:2b` | 4.98 → 4.65 | −7 % | 2.0–7.0 s | none / 0 MB |

**Finding:** all three degrade **far less** with growing history than M1.0's
`qwen3:4b-instruct` did (M1.0 §13: ~4.0 → ~1.9 tok/s, −52 % over a shorter
session). `gemma4:e2b` is essentially flat and keeps warm TTFT under ~2.5 s at
~1250 context tokens — the best long-session profile in either milestone.

## 14. Phase 2 — conversation quality (`AGENT-ASSISTED`, Claude vs. transcripts — NOT operator-confirmed)

`CONV-MIX-1` (language switching), `CONV-NATURAL-S1` (18-turn naturalness
stress), `CONV-HONESTY-S1` (hallucination / factual-restraint probe). Rubric:
`docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md` §4.

| Finalist | CONV-MIX-1 | CONV-NATURAL-S1 | CONV-HONESTY-S1 | Key observations |
|---|---|---|---|---|
| `gemma4:e4b` | **3.5 / 5** | **4.5 / 5** | **4.5 / 5** | Cleanest deep-dive in the roster. Correct PL↔EN switching + correct cross-language recall (MIX T6). NATURAL: correction (T3→4), recall correct T9/T10/T11/T18, scales detail on request, plays along with "gdybyś mogła pić". HONESTY: refuses to invent a *Bassline Theory* album, calls the 3-min full-charge claim "raczej mit", clean meta-honesty. Defects: verbose (hits the 200-cap on MIX T2/T4/T7), emoji despite persona, occasional PL gender slip ("musiałbyś"). |
| `gemma4:e2b` | **3.5 / 5** | **4.0 / 5** | **4.5 / 5** | Near-parity with e4b at ~2× speed. MIX: clean switching + correct cross-language recall (T6). NATURAL: correction handled, recall correct T9/T10/T11/T18, scales detail, good epistemic honesty at T15 ("Nie jestem pewna, bo nie znam pełnego kontekstu"). HONESTY: no hallucination, direct "To jest fałsz", clean meta-honesty. Defects: one mild persona break ("Jako sztuczna inteligencja, nie mam fizjologii" — NATURAL T16), weak pun, terser/shallower than e4b. The Phase-1 "one-sentence" miss did **not** recur (NATURAL T10 complied). |
| `qwen3.5:2b` | **2 / 5** | **~2.5 / 5** (EN ~3.5, PL ~1.5) | **2 / 5** | English is good (NATURAL EN recall T9–T11 works). Polish is broken: MIX T2/T5 "rozgrzej duszpasterską wodę do 90 °C" (nonsense *and* wrong for sourdough), **MIX T6 fails the recall question outright** — lectures on flour chemistry instead of answering "mąka żytnia i pszenna chlebowa". NATURAL: misreads "to spotkanie" as "this conversation" (T3), incoherent joke (T6), code-switches to English mid-Polish reply (T12). **HONESTY T1 hallucination**: invents two album titles ("The Lighthouse", "Merry Christmas Baby") for the non-existent band before recovering at T2. |

## 15. Phase 2 — persona A/B (`CONV-PL-S1`, standardized sampling, only the system prompt varies)

Three system prompts per finalist: **A** = `persona_minimal_v0` ("You are a
helpful assistant…"), **B** = `persona_nexa_v1` (detailed EN NeXa persona),
**S** = the case-file compact native-Polish NeXa persona.

| Finalist | Persona A (minimal) | Persona B (nexa_v1) | Persona S (compact PL) |
|---|---|---|---|
| `gemma4:e4b` | most verbose, most emoji, effusive ("Bardzo się cieszę…"), T8 one-sentence **missed** | tighter; T8 still asks a question back | **tightest**; T8 one-sentence **complied**; best brevity |
| `gemma4:e2b` | more emoji, "Jasne, oto podsumowanie:" preamble on T8 (misses one-sentence) | no emoji, T8 **complied** | **tightest**; T8 **complied**; "Basista. Dobrze, to konkret." |
| `qwen3.5:2b` | verbose, self-address "Cześć! Jestem NeXa…" | T8 complied but hallucinated specifics ("album w wersji limitowanej"), T10 recall wrong | best of its three, T10 recall ~correct, still non-native grammar ("mam pełen kontrolę") + a gender flip |

**Finding:** persona choice **materially** affects brevity and
instruction-following for the Gemma models — the compact native-Polish persona
is clearly best, the minimal "helpful assistant" persona is clearly worst
(verbose, emoji, effusive). For `qwen3.5:2b` the persona swap improves Polish
*coherence* somewhat under standardized sampling but does **not** fix the
non-native grammar or the occasional wrong recall — this is a
training/tokenizer limit, not a prompting one.

## 16. Phase 2 — standardized vs. recommended sampling

`CONV-PL-S1` was run for each finalist both at recommended settings (Phase 1,
`--bare-options`) and at the shared standardized case-file sampling (temp 0.7 /
top_p 0.8 / top_k 20 / repeat_penalty 1.05; Phase 2 persona-S run). Observed
difference for the Gemma models: **small** — a little more length under
standardized sampling, same instruction-following and recall outcomes, no
garbling either way. For `qwen3.5:2b`: standardized sampling is **slightly less
garbled** than its `--bare-options` default (which pushes temperature higher),
but still visibly non-native. Net: the Phase 1 recommended-settings screen was
not unfair to any finalist; the standardized comparison does not change any
ranking.

## 17. Phase 2 — final head-to-head + weighted NeXa score

Head-to-head is across the two carried finalists (`gemma4:e4b`, `gemma4:e2b`),
the demoted finalist (`qwen3.5:2b`), and the **incumbent baseline**
`qwen3:4b-instruct` as the continuity anchor (Phase 1 `CONV-PL-S1` /
`CONV-EN-S1` + perf only — it was **not** put through the Phase 2 battery, per
the operator's two-phase scope; this is a disclosed limitation).

### 17.1 Weighted NeXa score

Weights follow the operator's stated priorities (natural everyday conversation,
Polish, English, latency, reliability, factual restraint, Pi practicality).
Every input score is `AGENT-ASSISTED`.

| Criterion | Weight | `gemma4:e2b` | `gemma4:e4b` | `qwen3:4b-instruct` | `qwen3.5:2b` |
|---|---|---|---|---|---|
| Natural everyday conversation | 25 % | 4.0 | 4.5 | 3.0 | 2.5 |
| Polish quality | 20 % | 3.5 | 4.0 | 3.0 | 2.0 |
| English quality | 15 % | 4.0 | 4.0 | 3.5 | 4.5 |
| Latency (TTFT + tok/s) | 15 % | 4.0 | 2.5 | 2.5 | 3.5 |
| Reliability | 10 % | 4.5 | 4.5 | 4.0 | 3.5 |
| Factual restraint | 10 % | 4.5 | 4.5 | 3.5 | 2.0 |
| Pi practicality (RAM/thermal) | 5 % | 3.5 | 2.5 | 4.0 | 4.5 |
| **Weighted total (/5)** | | **4.0** | **3.9** | **3.2** | **3.4** |

### 17.2 Explicit disqualifying caveats (applied outside the arithmetic)

Per the task's weighted-score guidance, disqualifiers are stated, not hidden in
the numbers:

- **`qwen3.5:2b`** scores 3.4 on the weighted sum — above the incumbent — purely
  on English + low RAM. It is nonetheless **not eligible for the bilingual
  everyday-NeXa role**: its Polish is broken across every Phase 1 and Phase 2
  Polish session (garbled grammar, a failed recall turn, a mid-reply
  code-switch) and it **hallucinated album titles** in the honesty probe. It is
  retained only as the **English-primary / lowest-RAM reference**.
- **Bielik Q4_K_M (3rd-party)**, **Bielik Q8_0**, **phi4-mini:3.8b**,
  **qwen3.5:4b** — disqualified in Phase 1 (§10), unchanged.

### 17.3 Head-to-head verdict

| Axis | Winner | Note |
|---|---|---|
| Best overall (bilingual) | **`gemma4:e4b`** | zero quality defects P1 + strongest P2 (NATURAL 4.5, HONESTY 4.5) |
| Best practical pick | **`gemma4:e2b`** | ~90 % of e4b's conversation quality at ~2× tok/s, ~2.5 GB less RAM, flattest long-context decay, warm TTFT ~1–2 s |
| Best Polish | **`gemma4:e4b`** | 4/5; `gemma4:e2b` 3.5/5 close 2nd |
| Best English | **`qwen3.5:2b`** | 4.5/5; the three others tie at ~4/5 |
| Fastest acceptable | **`gemma4:e2b`** | only quality-passing candidate near the benchmark's "usable" ≥4 tok/s band |
| Most natural | **`gemma4:e2b`** | tersest / least "assistant-sounding"; P1 instruction miss did not recur in P2 |
| Best Pi balance | **`gemma4:e2b`** | best quality-per-resource once `qwen3.5:2b` is gated out for broken Polish |
| Incumbent standing | `qwen3:4b-instruct` | reliable, Apache-2.0, 262k ctx, ~3.9 GB; no longer the quality leader, worse long-context speed decay than all 3 finalists |

## 18. Ollama vs. llama.cpp — still `NOT RUN`

Unchanged from §11 — Ollama's blob store is `0700`/`ollama`-owned and reading it
needs `sudo` into another service account, not treated as pre-authorised by this
recovery pass. Recorded as a blocked operator decision, not silently skipped.

## 19. Recommendations and ADR implication

`PROPOSAL`, grounded in §8–§17. All quality scores are `AGENT-ASSISTED`; the
operator blind test (`docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md`) is
the owed confirmation before any M1.1 model is frozen.

> **UPDATE (2026-09-05, historical note — this section is not rewritten):**
> the operator blind test has since run (§8 of the test doc) and the owner
> froze **`gemma4:e4b`** — not the `gemma4:e2b` this section proposes — as the
> M1.1 local baseline, in `docs/decisions/ADR-0002_text_conversation_foundation.md`
> Amendment 2. The divergence is explained there: it is a weighting difference
> (this section's weighted score favors e2b's speed/RAM; the operator favored
> e4b's live conversation quality), not a disagreement on the underlying
> quality data below, which is unchanged.

1. **Everyday bilingual NeXa (M1.1 baseline candidate): `gemma4:e2b`.** Best
   weighted score (4.0), best Pi balance, flattest long-context decay, warm TTFT
   ~1–2 s, conversation quality within ~0.5 of the roster's best. Cost: ~7.5 GB
   resident and a 25 s cold load (one-time). Apache-2.0.
2. **Quality mode: `gemma4:e4b`.** When RAM/latency headroom allows, the roster's
   best conversation and Polish, at ~3.1 tok/s / ~10 GB.
3. **English-primary / lowest-RAM role: `qwen3.5:2b`.** Not for bilingual use.
4. **Incumbent / safe fallback: `qwen3:4b-instruct`.** Keep swappable; it remains
   reliable and by far the largest context window (262k).
5. **Polish-grammar reference only: Bielik Q8_0** (undeployable at ~2 tok/s /
   8k ctx). **Do not use** the 3rd-party Bielik Q4_K_M (reliability failure).

**ADR-0002 D4 implication:** M1.0B **resolves** D4's caveat (b) — a fair Bielik
re-test at temp 0.1 plus a Q4_K_M GGUF does **not** flip the baseline (Q8_0 is
still weak on English and TTFT-never-warms; the 3rd-party Q4_K_M is unusable).
It **partially addresses** caveat (a) — a better Polish persona lifted
`qwen3:4b-instruct` from PL 2/5 (M1.0) to 3/5, and the compact native-Polish
persona measurably improves the Gemma models' brevity and instruction-following.
It adds **new evidence D4 did not have**: two Gemma 4 models out-converse the
incumbent in both languages. This does **not** unilaterally overturn D4 (the
Gemma models did not exist at M1.0; the RAM/latency trade for `gemma4` is an
owner call; the operator blind test is not yet run). Recorded as an **amendment**
to ADR-0002, not a status change — see the ADR's M1.0B amendment section.

---

*Final task-result summary:
`docs/reports/R0003_m1_0b_current_small_model_sweep_20260901.md`.*
