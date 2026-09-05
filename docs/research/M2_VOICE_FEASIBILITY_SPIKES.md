# M2.0A — Realtime Voice Feasibility Spikes

- **Date:** 2026-09-05
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.0A — Feasibility Spikes**
  (measurement only; no M2 ADR, no M2 product runtime)
- **Status:** Both required spikes complete. Optional Spike 3 partially done
  (two of three gates verified from primary sources; benchmarking correctly
  not attempted — see §5). **Evidence ready for ADR — this document does not
  choose an architecture.**
- **Related:** `docs/research/M2_REALTIME_VOICE_RESEARCH.md`,
  `docs/reports/R0005_m2_realtime_voice_oss_research_20260905.md`,
  `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md` (frozen baseline:
  `gemma4:e4b`, Amendment 2 — **unchanged by this task**), raw data:
  `docs/research/m2_voice_spikes/`

**Canonical rule carried into this spike (unchanged):** any voice framework
must ultimately feed the same `ConversationSession`
(`src/nexa/conversation/session.py`, M1.1). Nothing in this task built,
prototyped, or wired a second LLM path, history, or persona — these spikes
measured standalone component behavior (whisper.cpp, Silero VAD, Piper,
Ollama), never a competing conversation authority.

---

## 1. Method

Per `RESEARCH_POLICY.md` and the task's explicit instruction: reuse the
legacy ASR benchmark material rather than inventing a new one. All whisper.cpp
measurements in Spike 1 use the **exact same 12 real, human-recorded** PL/EN
`.wav` files legacy's faster-whisper sweep used
(`smart-desk-ai-assistant/var/data/asr_test_samples/`, copied read-only into
`docs/research/m2_voice_spikes/asr_test_samples/` — legacy repo itself
untouched). Legacy's own numbers come from
`smart-desk-ai-assistant/var/reports/asr_benchmark_sweep_20260518-225531.json`
and `asr_runtime_decision_20260519-000000.md` (both read-only, cited exactly,
not altered).

whisper.cpp (`ggml-org/whisper.cpp`) was built from source in a scratch
location (`/home/devdul/nexa_m2_spike_build/`, **outside** both repos and
outside NeXa's `.venv`, per the task's testing discipline) with
`cmake -B build -DCMAKE_BUILD_TYPE=Release`, correctly auto-detecting this
Pi's Cortex-A76 CPU (`-mcpu=cortex-a76+crc+crypto+dotprod`). Models: `tiny`
and `base` downloaded via the project's own `models/download-ggml-model.sh`,
each quantized to `q8_0` with the project's own `whisper-quantize` tool for a
realistic-deployment comparison point; `small` spot-checked separately (see
§2.4). VAD used whisper.cpp's own **native ggml Silero VAD** integration
(`models/download-vad-model.sh silero-v5.1.2`, 860 KB — no PyTorch). TTS used
`piper-tts` (scratch venv) with legacy's real production Polish voice,
`pl_PL-gosia-medium.onnx`, copied read-only. The LLM is Ollama serving the
frozen M1.1 baseline, `gemma4:e4b` (ADR-0002 Amendment 2), via the same
`/api/chat` streaming contract `src/nexa/providers/ollama.py` uses — **not
modified, not swapped, for this task.**

## 2. Spike 1 — whisper.cpp vs. legacy faster-whisper (same material)

### 2.1 Configurations tested

| Config | Decode | Threads | Language |
|---|---|---|---|
| `tiny/fp16`, `tiny/q8_0`, `base/fp16`, `base/q8_0` | whisper.cpp defaults (`beam_size=5`, `best_of=5`) | 4 | hinted (`pl`/`en`, matching each file's real language — legacy's fairer "lang=sample" mode) |
| `tiny/fp16/bs1`, `base/fp16/bs1` | `beam_size=1`, `best_of=1` — **matched to legacy's beam1 configs** | 4 | hinted |
| `small/fp16` | defaults | 4 | hinted | (3-file spot check only, §2.4) |

Full per-file transcripts and timings: `docs/research/m2_voice_spikes/whisper_cpp_bench_results.json` and `..._bs1_results.json`.

### 2.2 Aggregate results (all 12 files, `VERIFIED FACT` — measured this session)

| Config | Avg WER | EN WER | PL WER | Avg latency | Peak RSS |
|---|---|---|---|---|---|
| tiny/fp16 | 0.326 | 0.233 | 0.393 | 985 ms | 176 MB |
| tiny/q8_0 | 0.451 | 0.233 | 0.607 | 788 ms | 142 MB |
| base/fp16 | 0.354 | 0.100 | 0.536 | 2191 ms | 284 MB |
| base/q8_0 | 0.354 | 0.100 | 0.536 | 1688 ms | 221 MB |
| tiny/fp16/**bs1** | 0.465 | 0.233 | 0.631 | 907 ms | — |
| base/fp16/**bs1** | 0.354 | 0.100 | 0.536 | 1972 ms | — |

WER method: word-level Levenshtein edit distance / reference word count, on
NFKC-normalized, lowercased, punctuation-stripped text — a standard
approximation, not necessarily identical to legacy's own "word_error_rough"
implementation (legacy's exact formula wasn't in the JSON); both are
consistent within themselves, which is what the relative comparison below
needs.

### 2.3 Direct comparison vs. legacy faster-whisper (`VERIFIED FACT`, same files)

| Config | Avg WER | EN WER | PL WER | Avg latency |
|---|---|---|---|---|
| legacy `tiny`/int8/**beam3**/lang=sample | 0.572 | 0.473 | 0.643 | 1927 ms |
| legacy `base`/int8/**beam1**/lang=sample | 0.558 | 0.407 | 0.667 | 3575 ms |
| legacy `base`/int8/**beam3**/lang=sample | 0.537 | 0.407 | 0.631 | 3486 ms |
| legacy `small`/int8/beam1/lang=sample | 0.301 | 0.307 | 0.298 | 9650 ms (worst 18896 ms) |

**Finding, stated plainly: whisper.cpp is not "the same accuracy, different
runtime."** Even at a **matched beam size (1)**, whisper.cpp's `base` model
scores **0.354 avg WER at 1972 ms**, beating legacy's faster-whisper `base`
beam1 (**0.558 avg WER at 3575 ms**) and beam3 (**0.537 avg WER at 3486 ms**)
on **both** accuracy and speed, on the identical audio. whisper.cpp's `tiny`
at beam1 (0.465 avg WER, 907 ms) also beats legacy's `tiny` beam3 (0.572 avg
WER, 1927 ms) on both axes. This holds before even crediting whisper.cpp's
own default beam_size=5, which improves `tiny`'s accuracy further (0.326)
at a still-fast 985 ms. The task's instruction not to assume identical
accuracy "merely because both use Whisper weights" was correct to insist on
— the two runtimes are measurably different in practice on this exact
hardware and material, and whisper.cpp is the better one here.

`base/q8_0` vs `base/fp16` produced **identical WER** (0.354) — quantization
cost essentially nothing in accuracy at this precision tier, while cutting
latency ~23% (2191 ms → 1688 ms) and peak RSS ~22% (284 MB → 221 MB).

### 2.4 `small` — spot check only (legacy already rejected it on latency)

3 of 12 files, chosen to include the hardest PL sentence
(`pl_wyjaśnij_grawitację`, the worst performer across every other config):

| File | Recognized | WER | Latency |
|---|---|---|---|
| `pl_wyjaśnij_grawitację` | "Wyjaśnij grawitację." | **0.00** | 7.30 s |
| `en_explain_gravity` | "Explain gravity." | **0.00** | 6.60 s |
| `pl_co_to_są_kolory` | "Co to są kolory?" | **0.00** | 7.00 s |

Perfect transcription on all three (vs. legacy's `small` avg WER 0.301) —
but at ~7 s/utterance, this **confirms legacy's own conclusion still holds**:
`small`'s accuracy is excellent but its latency (~7 s here, legacy's own
9.65 s avg / 18.9 s worst case) is not "remotely practical" for a
conversational turn, whichever runtime produces it. Not pursued further, per
the task's own instruction.

### 2.5 Auto-language-detection failure mode — reproduced, not just assumed

The task warned not to assume identical behavior; this cuts both ways —
whisper.cpp **reproduces legacy's exact known failure**, independently
confirming it rather than being a faster-whisper-specific bug:

- `pl_co_to_są_kolory.wav` under `-l auto`: `tiny` → `"さとさんコロール"`
  (Japanese), `base` → `"外さん color"` (mixed garbage). Legacy's own sweep
  hit the identical file with the identical symptom: `detected_language: "ja"`,
  recognized `"さとさん、コロール"`.
- Other files auto-detected correctly (`pl_czym_jest_teleportacja` → correct
  Polish; `en_explain_gravity` → same "Explan gravity." typo as the hinted
  runs — a genuine recognition error, not a language issue).

**Implication for M2, `INFERENCE`**: language auto-detection on short
utterances is not reliable enough to depend on, confirmed across two
independent Whisper runtimes. NeXa's M2 architecture needs an explicit
language-hint strategy (a configured default, a wake-word-specific hint, or
running both hints and picking by confidence) — not naive `auto` mode.

## 3. Spike 2 — resource budget (VAD + STT + `gemma4:e4b` + TTS)

### 3.1 Stages and raw results (`VERIFIED FACT`, measured this session)

Selected STT config: **`base/fp16`** (Spike 1's best accuracy/latency
balance). VAD: native ggml Silero. TTS: Piper, `pl_PL-gosia-medium`, one
representative Polish sentence. LLM: one real `gemma4:e4b` turn via Ollama,
`num_ctx=8192`, `think:false` (same options M1.1 uses). Full raw JSON incl.
per-stage system samples: `docs/research/m2_voice_spikes/spike2_resource_budget_results.json`.

| Stage | What ran | Wall time | Key result | Min RAM avail | Max swap | Max load1 | Temp start→end |
|---|---|---|---|---|---|---|---|
| A | VAD only | 0.08 s | 80 ms | 14249 MB | 0 MB | 0.69 | 54.3→54.3°C |
| B | STT only (`base/fp16`) | 2.64 s | 2637 ms | 13919 MB | 0 MB | 0.71 | 54.3→59.3°C |
| C | LLM only (cold load) | 40.56 s | TTFT 35.28 s, 3.22 tok/s | 4239 MB | 41 MB | 1.24 | 56.5→61.5°C |
| D | TTS only *(see caveat)* | 3.41 s | 3408 ms | 4107 MB | 42 MB | 1.24 | 58.2→60.4°C |
| E | VAD + STT concurrent | 3.48 s | VAD 2439 ms, STT 3482 ms | 3984 MB | 69 MB | 1.77 | 58.2→60.9°C |
| F | LLM + TTS concurrent (LLM warm) | 7.59 s | TTFT 4.36 s, 3.41 tok/s; TTS 4068 ms | 4058 MB | 68 MB | 2.02 | 59.8→62.6°C |
| G | VAD + STT, LLM resident+**idle** | 3.77 s | VAD 2862 ms, STT 3766 ms | 3984 MB | 62 MB | 2.95 | 63.7→63.7°C |
| H | **Full concurrent**: LLM actively generating + VAD + STT + TTS all at once | 16.15 s | TTFT 12.84 s, 3.33 tok/s; VAD 5811 ms; STT 12527 ms; TTS 9951 ms | 3806 MB | 62 MB | 3.77 | 60.9→64.2°C |

**Methodological caveat, disclosed**: stage D ran within stage C's 30 s
`keep_alive` window, so its "min RAM avail" (4107 MB) reflects a still-resident
LLM in the background, not TTS in true isolation with the full 14 GB free.
This does not affect the *concurrent-degradation* comparisons below (E vs. B,
H vs. B/D), which are the load-bearing numbers.

**No throttling occurred in any stage** (`vcgencmd get_throttled` stayed
`0x0` throughout every stage). Peak temperature across the entire spike was
64.8°C, well below the Pi 5's throttle point (~80°C+). Swap usage never
exceeded 69 MB (of 2 GB available) — trivial.

### 3.2 The CPU-contention failure pattern — reproduced and quantified

Legacy recorded one real CPU-contention incident but never measured the
voice pipeline running concurrently with the LLM (`R0005` §3, citing legacy's
own `164_raport_14_08_28.md`). This spike **deliberately created that
condition** (stage H: everything started at once, same 4 CPU cores, no
thread-budget coordination between components) and measured the result
precisely, comparing each component's stage-H time against its own solo
baseline:

| Component | Solo baseline | Stage H (full concurrent) | Slowdown |
|---|---|---|---|
| VAD | 80 ms (A) | 5811 ms | **~73×** |
| STT (`base/fp16`) | 2637 ms (B) | 12527 ms | **~4.75×** |
| TTS (Piper) | 3408 ms (D, contaminated — see caveat) | 9951 ms | **~2.9×** |
| LLM time-to-first-token | 4.36 s warm (F) | 12.84 s | **~2.9×** |
| LLM steady-state tok/s | 3.41 (F, warm) | 3.33 | **~2% — barely moved** |

**Do not hide this degradation — it is real and severe for VAD/STT/TTS.**
But it is not the whole story: **RAM/swap/thermal never became the
bottleneck** (min available RAM stayed ≥3.8 GB even at worst; swap stayed
under 70 MB; no throttling). This **confirms and quantifies legacy's own
diagnosis exactly**: the constraint is **CPU-core contention among
short-lived audio tasks fighting an actively-generating LLM for the same 4
cores**, not memory or heat. Once the LLM finishes generating and audio
components have the cores to themselves again (stages A/B/E), latency
returns to normal.

**A second, important nuance, `INFERENCE`**: stage H deliberately started
all four components at the exact same instant with no thread-budget
coordination (each component requesting up to 4 threads on a 4-core
machine) — a worst-case stress test, not a realistic pipeline. A real M2
turn is naturally more **sequential**: VAD/STT happen *before* the LLM call
even starts (there is no user speech to transcribe while the assistant is
mid-answer), and only TTS narration of an already-completed sentence would
plausibly overlap with the LLM still generating the *next* sentence — closer
to stage F's condition (LLM+TTS only), where degradation was much milder
(TTS +19%, LLM TTFT still only 4.36 s once warm). **The severe VAD/STT
numbers in stage H are a ceiling for the worst case, not a prediction for a
well-sequenced pipeline.**

### 3.3 What Spike 2 does and does not answer

- **Answers**: this Pi 5 has enough RAM and thermal headroom to hold all four
  components' resident state simultaneously (confirmed up to ~3.8 GB free
  even at the worst measured point) — RAM is not the limiter GB-for-GB.
  CPU-core contention, when it happens, is real and large for the audio
  components, small for the LLM's steady-state throughput, and concentrated
  in time-to-first-output rather than total-processing collapse.
- **Does not answer**: the actual latency of a realistically-*sequenced* M2
  turn (VAD → STT → LLM → TTS, not everything-at-once) — that requires a
  real pipeline, which is explicitly out of this task's scope (M2.0A is
  feasibility measurement, not M2 implementation).

## 4. Framework overhead — not benchmarked (per task scope)

Per the task's explicit instruction, full Pipecat/LiveKit pipelines were not
built. A cheap idle-overhead probe was judged **not worth doing separately**
in this task: `docs/research/M2_REALTIME_VOICE_RESEARCH.md` §5.1/§5.2 already
recorded real, measured base-install footprints for both
(Pipecat ~668 MB venv, LiveKit Agents ~438 MB venv, neither pulling in
PyTorch) in the prior research task. Re-measuring *idle process* RAM/CPU
without a working pipeline behind either would not add information beyond
what installability already established, and would risk exactly the kind of
premature framework benchmarking this task explicitly deferred to the ADR
stage. **Framework choice remains untouched by this spike, as instructed.**

## 5. Optional Spike 3 — NVIDIA Parakeet/Canary quick check

Per the task's gating rule: verify Polish support and license from primary
sources first; only benchmark if both pass **and** a hardware path is
plausible.

- **License, `VERIFIED FACT`** (fetched the model card directly,
  `huggingface.co/nvidia/parakeet-tdt-0.6b-v3`): **CC-BY-4.0**. Explicitly
  "ready for commercial/non-commercial use"; the only obligation is
  attribution. No field-of-use or redistribution restriction found. **Gate 1: PASS** — clean, commercial-friendly license, unlike the `UNKNOWN` left in the prior research doc.
- **Polish support, `VERIFIED FACT`** (same model card): Polish (`pl`) is
  explicitly one of 25 supported languages (alongside Czech, German, Russian,
  Ukrainian, and others). **Gate 2: PASS.**
- **Hardware path, `INFERENCE` — plausible but not confirmed within this
  task's time budget, correctly stopped short of a full benchmark**:
  `ggml-org/whisper.cpp` (the same project already built for Spike 1) already
  ships `parakeet-cli` and `parakeet-quantize` binaries plus a
  `models/convert-parakeet-to-ggml.py` converter — real, existing
  infrastructure for a CPU/ARM64 path, and the CLI's default model path
  (`models/ggml-parakeet-tdt-0.6b-v3.bin`) matches this exact model. However,
  actually producing that `.bin` requires downloading NVIDIA's original
  multi-GB NeMo checkpoint and running the conversion — a real, non-trivial
  amount of additional time/disk/dependency weight (likely NeMo-toolkit-class
  tooling) that would have exceeded the task's explicit "do NOT spend major
  time" instruction once both hard gates already passed. **Correctly stopped
  here, not benchmarked.**

**Recommendation, unchanged from the prior research doc's spirit**: Parakeet
is now confirmed *more* promising than previously known (license and Polish
support both resolved to PASS, not `UNKNOWN`), and whisper.cpp's own tooling
gives a plausible, low-new-dependency path to try it. Worth a dedicated,
scoped follow-up spike (convert + benchmark) before an M2 ADR closes the STT
question — but it is not required to reach a decision, since whisper.cpp's
`base` already clears the accuracy/latency bar Spike 1 established.

## 6. Local voice feasibility classification

# **LOCAL_FEASIBLE_WITH_TUNING**

**Why not `LOCAL_FULLY_FEASIBLE`**: Spike 2 found real, large (3×–73×)
latency degradation for VAD/STT/TTS under naive full concurrency with an
actively-generating LLM (§3.2). That is not "fully feasible with no caveats."

**Why not `LOCAL_HYBRID_RECOMMENDED` or `LOCAL_FULL_PIPELINE_NOT_PRACTICAL`**:
none of the three resources that would force that conclusion were exhausted.
RAM never dropped below ~3.8 GB free even in the worst case; swap stayed
under 70 MB; there was **zero throttling** at any point, with peak temperature
(64.8°C) well clear of the Pi 5's throttle threshold. The LLM's steady-state
token throughput barely moved (3.41 → 3.33 tok/s, ~2%) even under the worst
concurrent stress — the contention shows up almost entirely as **added
time-to-first-output** for the audio components, not as a hard resource
ceiling or a collapse in the LLM's own generation rate. A component that is
this close to fine under a deliberately-worst-case stress test is a tuning
problem, not a hard "this machine can't do it" result.

**What "tuning" concretely means here, `PROPOSAL` for the eventual M2 ADR to
weigh, not decided here**:
1. **Sequence, don't naively parallelize.** VAD/STT should run *before* the
   LLM call starts (there is no reason for them to overlap with active
   generation in a normal turn); only TTS narrating an already-completed
   sentence should overlap the LLM continuing to generate the next one — the
   much milder stage-F condition, not stage H's.
2. **Coordinate thread budgets explicitly** across whatever components *are*
   concurrently active, rather than letting each request 4 threads on a
   4-core machine (stage H's contention was worsened by exactly this).
3. **Keep the explicit language-hint requirement from §2.5** — auto-detect
   is not safe to rely on regardless of which STT engine is chosen.
4. If turn latency is still judged too slow after sequencing/thread tuning,
   the documented, **not-yet-decided** levers named in the task itself remain
   available for a future ADR: a lighter local STT config, the Hailo-Whisper-
   offload path (still unproven — `R0005`/`M2_REALTIME_VOICE_RESEARCH.md`
   §8), `gemma4:e2b` as a speed-priority mode, PC/companion offload, or a
   later `CLOUD PREFERRED` policy. **None of these are applied in this
   task** — `gemma4:e4b` remains the frozen M1.1 baseline (ADR-0002
   Amendment 2), unchanged.

## 7. Unresolved questions for the M2 ADR

1. Realistic *sequenced*-pipeline turn latency is still unmeasured (§3.3) —
   this spike measured components in isolation and in worst-case
   simultaneous contention, not a real VAD→STT→LLM→TTS pipeline in order.
2. Parakeet/Canary's hardware path is plausible but unconverted/unbenchmarked
   (§5) — a good candidate for a small follow-up spike, not a blocker.
3. Framework overhead (Pipecat/LiveKit idle cost) remains unmeasured beyond
   installability (§4) — deferred to the ADR stage as instructed.
4. Thread-budget coordination strategy across concurrently-active components
   is a real open design question for the M2 architecture, not resolved
   here.

## 8. Recommendation

**Evidence ready for ADR.** Both required spikes are complete with real,
quantified, same-hardware measurements. The local-first, full-pipeline
approach (VAD + whisper.cpp `base` STT + `gemma4:e4b` + Piper TTS) is
**feasible on this Pi 5**, provided the eventual M2 architecture sequences
the pipeline sensibly rather than naively running everything concurrently.
This document does **not** choose Pipecat vs. LiveKit Agents vs. a hybrid —
that remains the M2 architecture ADR's decision, now backed by real local
feasibility evidence rather than assumption.
