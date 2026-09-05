# R0006 — M2.0A Realtime Voice Feasibility Spikes

- **Date:** 2026-09-05
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice · **Substage M2.0A — Feasibility Spikes**
  (measurement only; no M2 ADR, no M2 product runtime, no M1.1 product code
  touched)
- **Related:** `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md` (primary
  evidence doc), `docs/research/M2_REALTIME_VOICE_RESEARCH.md`, `R0005`,
  `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md`, raw data:
  `docs/research/m2_voice_spikes/`

---

## TASK RESULT

**PASS.** Both required spikes complete with real, same-hardware,
same-material measurements. Optional Spike 3 correctly stopped after its two
hard gates (license, Polish support) passed but before a full benchmark,
per the task's own scope discipline. No M2 architecture ADR written — this
is evidence for one, not the decision. No M1.1 product code touched.
Feasibility classified as **LOCAL_FEASIBLE_WITH_TUNING**.

## WHAT I DID

1. Read `AGENTS.md`, `CURRENT_STATE.md`, `ROADMAP.md`,
   `M2_REALTIME_VOICE_RESEARCH.md`, `R0005`,
   `M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`, `ADR-0002`; verified `git
   status`/`log` and hardware state (no model loaded, no throttling) before
   starting.
2. **Located and reused the exact legacy ASR benchmark material** (read-only):
   the 12 real, human-recorded PL/EN `.wav` fixtures legacy's faster-whisper
   sweep used, plus its `index.json` metadata, copied into
   `docs/research/m2_voice_spikes/asr_test_samples/`. Read
   `asr_benchmark_sweep_20260518-225531.json` and
   `asr_runtime_decision_20260519-000000.md` for legacy's exact numbers.
3. **Spike 1**: built `ggml-org/whisper.cpp` from source in a scratch
   location outside both repos (`/home/devdul/nexa_m2_spike_build/`),
   downloaded `tiny`/`base` GGUF models plus `q8_0` quantized variants,
   downloaded whisper.cpp's native ggml Silero VAD model, and ran a
   deterministic benchmark harness against all 12 legacy fixtures at 4 main
   configs plus a beam_size=1-matched supplemental run (to isolate
   decode-strategy effects from runtime effects) plus a 3-file `small`
   spot-check plus an auto-language-detection reproduction check.
4. **Spike 2**: built a resource-budget harness measuring 8 stages (A-H:
   VAD/STT/LLM/TTS isolated, then combined up to full 4-way concurrency),
   using whisper.cpp (`base/fp16`, Spike 1's selected config), whisper.cpp's
   native VAD, `piper-tts` (scratch venv) with legacy's real
   `pl_PL-gosia-medium.onnx` Polish voice (copied read-only), and Ollama
   serving the frozen `gemma4:e4b` baseline — sampling system RAM/swap/
   load/temperature/throttle state throughout each stage.
5. **Optional Spike 3**: fetched NVIDIA's Parakeet-TDT-0.6B-v3 model card
   directly (primary source) for license and language support; stopped
   before benchmarking once the hardware-path check showed it would require
   downloading a multi-GB NeMo checkpoint and conversion tooling, exceeding
   the task's "do not spend major time" instruction.
6. Wrote `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md`, this report, and a
   `docs/research/m2_voice_spikes/README.md` indexing the raw artifacts.
7. **Documentation consistency check**: found and corrected the stale `R0005`
   "TESTS" paragraph the task flagged (it said tests were not re-run, while
   a later section of the same report, added in a prior correction pass,
   said they were) — corrected in place with the historical context
   preserved, no unrelated section rewritten.
8. Updated `docs/CURRENT_STATE.md` and `tests/test_foundation.py`'s
   `REQUIRED` list (this report's filename), per existing convention.

## WHISPER.CPP RESULTS

See `M2_VOICE_FEASIBILITY_SPIKES.md` §2 for full tables. Headline:
`base/fp16` at whisper.cpp's default decode settings: **0.354 avg WER
(EN 0.100, PL 0.536) at 2191 ms**; `q8_0` quantization gave **identical
accuracy** at ~23% lower latency and ~22% lower peak RSS. `tiny/fp16`:
**0.326 avg WER at 985 ms**. Auto-language-detection reproduced legacy's
exact known failure mode on the same file (`pl_co_to_są_kolory.wav` →
Japanese-script garbage on both `tiny` and `base`) — an explicit language
hint is required, confirmed independently of the STT engine choice.

## FASTER-WHISPER COMPARISON

Same 12 files, same-hardware. At a **matched beam_size=1**, whisper.cpp's
`base` (0.354 avg WER, 1972 ms) beat legacy's faster-whisper `base` beam1
(0.558 avg WER, 3575 ms) and beam3 (0.537 avg WER, 3486 ms) on **both**
accuracy and speed. whisper.cpp's `tiny` beam1 (0.465, 907 ms) beat legacy's
`tiny` beam3 (0.572, 1927 ms) the same way. **The task's caution not to
assume identical accuracy across runtimes was correct** — the two Whisper
runtimes are measurably different here, and whisper.cpp is the better one on
this hardware/material. `small` was spot-checked (3/12 files, all 0.00 WER)
but confirmed legacy's own latency rejection still holds (~7 s/utterance).

## RESOURCE-BUDGET RESULTS

8 stages measured (A-H, `M2_VOICE_FEASIBILITY_SPIKES.md` §3.1). No
throttling in any stage; peak temperature 64.8°C (well below the Pi 5's
throttle point); swap never exceeded 69 MB; minimum available RAM never
dropped below ~3.8 GB even in the worst (full 4-way concurrent) stage.
RAM/thermal are **not** the constraint on this hardware for this stack.

## CPU CONTENTION

Reproduced and quantified precisely (stage H: LLM actively generating +
VAD + STT + TTS all started at once, uncoordinated thread budgets, same 4
cores): VAD **~73× slower** (80 ms → 5811 ms), STT **~4.75× slower**
(2637 ms → 12527 ms), TTS **~2.9× slower** (3408 ms → 9951 ms), LLM
time-to-first-token **~2.9× slower** (4.36 s warm → 12.84 s) — but LLM
**steady-state token throughput barely moved** (3.41 → 3.33 tok/s, ~2%).
This confirms and precisely quantifies legacy's own "CPU contention, not
thermal" diagnosis (`R0005` §3, citing legacy's `164_raport_14_08_28.md`).
Stage H is a deliberate worst case (naive full concurrency); stage F
(LLM + TTS only, no simultaneous VAD/STT) showed much milder degradation
(TTS +19%, LLM TTFT still 4.36 s warm) — closer to what a well-sequenced
real pipeline would actually produce.

## LOCAL VOICE FEASIBILITY

**LOCAL_FEASIBLE_WITH_TUNING.** Full reasoning in
`M2_VOICE_FEASIBILITY_SPIKES.md` §6. Not "fully feasible" (real, large
degradation exists under naive full concurrency); not "hybrid recommended"
or "not practical" (no resource — RAM, swap, thermal — was actually
exhausted, and the LLM's own generation rate held up under the worst
measured stress). Tuning levers named (not applied): sequence the pipeline
instead of naive parallelism, coordinate thread budgets across concurrent
components, keep the explicit language-hint requirement. `gemma4:e4b` was
**not** changed — it remains the frozen M1.1 baseline (ADR-0002 Amendment 2).

## OPTIONAL PARAKEET/CANARY RESULT

Both hard gates **PASS** from primary-source verification: license is
**CC-BY-4.0** (commercial-friendly, attribution-only — resolves the prior
research doc's `UNKNOWN`), Polish is one of 25 explicitly supported
languages (resolves the prior `UNKNOWN`). Hardware path is plausible
(`ggml-org/whisper.cpp` already ships `parakeet-cli` + a conversion script
whose default model path matches this exact model) but **not benchmarked** —
producing the required `.bin` needs a multi-GB NeMo checkpoint download plus
conversion tooling, correctly judged to exceed this task's "do not spend
major time" scope once both hard gates already passed. Recommended as a
small, scoped follow-up spike, not a blocker for the M2 ADR.

## FRAMEWORK IMPLICATIONS

None resolved here by design — full Pipecat/LiveKit pipelines were
explicitly out of scope for this task. A separate idle-overhead micro-probe
was judged not worth doing on top of the already-measured base-install
footprints from the prior research task (`M2_REALTIME_VOICE_RESEARCH.md`
§5.1/§5.2: Pipecat ~668 MB venv, LiveKit Agents ~438 MB venv, neither
pulling in PyTorch) — re-measuring idle process cost without a working
pipeline behind it would not add real information. Framework choice remains
entirely open for the M2 ADR.

## DOCUMENTATION

- `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md` — new, primary evidence doc.
- `docs/research/m2_voice_spikes/` — new: raw benchmark JSON (Spike 1 main +
  bs1 + Spike 2), the 12 legacy audio fixtures + `index.json` (read-only
  copies), the 3 throwaway harness scripts (for exact-command
  reproducibility — not standalone-runnable without rebuilding the scratch
  whisper.cpp), and a `README.md` indexing all of it.
- This report (`R0006`).
- `docs/reports/R0005_m2_realtime_voice_oss_research_20260905.md` — **TESTS**
  section corrected (was stale; historical context preserved, nothing else
  rewritten). See "DOCUMENTATION CONSISTENCY CHECK" note below.
- `docs/CURRENT_STATE.md` — updated.
- `tests/test_foundation.py` — `REQUIRED` list extended with this report's
  filename, per existing convention.
- No ADR written — per the task's explicit instruction, and because framework
  choice (the other major M2 ADR input) is still open.

## DOCUMENTATION CONSISTENCY CHECK (as requested)

Confirmed the contradiction: `R0005`'s "TESTS" section said "not re-run...
since nothing under `src/`/`tests/` was touched," while its own later "WHAT I
VERIFIED" section (added in a prior correction commit) said the full suite
and `ruff` **were** re-run, and `tests/test_foundation.py` **was** in fact
touched (a `REQUIRED`-list addition) in that same correction. Corrected the
"TESTS" section in place with a dated note explaining why it was originally
written that way, rather than silently rewriting it or leaving the
contradiction standing. No other section of `R0005` was changed.

## TESTS

`python -m unittest discover -s tests` and `ruff check src tests apps`: run
after all documentation edits (including the `test_foundation.py`
`REQUIRED`-list addition). **33 tests pass, 1 intentionally skipped
(live Ollama test); lint clean.** No `src/` product code was touched by this
task at any point — confirmed via `git status` before commit.

## COMMIT

One commit, research/docs only (see below). Not pushed.

## UNRESOLVED

- Realistic *sequenced*-pipeline turn latency (not worst-case simultaneous
  concurrency) is still unmeasured — the natural next spike once a pipeline
  shape exists.
- Parakeet/Canary hardware path unconverted/unbenchmarked (license + Polish
  support confirmed; conversion + benchmark deferred as a small follow-up).
- Framework (Pipecat vs. LiveKit vs. hybrid) idle-overhead numbers remain
  unmeasured beyond installability — deferred to the M2 ADR stage as
  instructed.
- Thread-budget coordination strategy across concurrently-active pipeline
  components is a real open M2 design question, not resolved by this spike.

## CURRENT VERIFIED STATE

M2.0A feasibility spikes complete. Local-first full voice pipeline
(VAD + whisper.cpp + `gemma4:e4b` + Piper) is classified
**LOCAL_FEASIBLE_WITH_TUNING** on this exact Pi 5, with real, quantified
evidence for both the happy path and the worst-case CPU-contention failure
mode. `gemma4:e4b` remains the frozen M1.1 baseline, unchanged. No M2
architecture ADR exists yet. M1.1 is unaffected.

## NEXT RECOMMENDED ACTION

Write the **M2 architecture ADR**, now backed by: (1) the open-source-first
framework research (`M2_REALTIME_VOICE_RESEARCH.md`/`R0005`) and (2) this
task's real local feasibility evidence. The ADR should choose among Pipecat /
LiveKit Agents / a hybrid (framework research §7's A/B/C), state the pipeline
sequencing and thread-budget approach this spike's findings call for (§6),
and record the explicit language-hint requirement (§2.5) as a constraint.
Optionally, a small follow-up spike (Parakeet conversion + benchmark) can run
either before or in parallel with ADR-writing — it is not a blocker.

## LEGACY NEXA USED

YES — read-only. The 12 real ASR test fixtures + `index.json`, the
`asr_benchmark_sweep_20260518-225531.json` sweep data, the Piper Polish voice
file (`pl_PL-gosia-medium.onnx`), and `asr_runtime_decision_20260519-000000.md`
for cross-reference. Not modified.

## EXTERNAL RESEARCH USED

YES — `ggml-org/whisper.cpp` (built from source), Hugging Face model card for
`nvidia/parakeet-tdt-0.6b-v3` (primary-source license/language verification).
