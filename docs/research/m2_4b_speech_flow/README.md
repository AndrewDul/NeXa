# M2.4B — Natural Speech Flow / Streaming Pacing — research artifacts

Research + design only. No product code changed. See
`docs/reports/R0012_m2_4b_speech_flow_research_20260906.md` for the full
report and recommendation.

## Files

- `gap_profiler.py` — throwaway profiler (scratchpad-style, like R0010's
  `m2_4a_tts_spike/pipeline_smoke_test.py`). Measures, with real components:
  1. Piper HTTP per-request synthesis latency vs. text length + audio
     seconds produced (real-time factor).
  2. `gemma4:e4b` first-token + token-rate (raw Ollama `/api/chat`).
  3. A scripted end-to-end pipeline (`AssistantSpeechBridge` →
     `PiperHttpTTSService` → `TtsStatusObserver` → `LocalAudioOutputTransport`)
     fed tokens at a chosen rate, logging every TTS lifecycle event and the
     actual production gap between audio chunks.

  Run: `./.venv/bin/python -u docs/research/m2_4b_speech_flow/gap_profiler.py`
  (needs the external Piper HTTP venv + voices from
  `scripts/setup_piper_http.py`, and a running Ollama with `gemma4:e4b`).

- `ollama_timing.py` — minimal `gemma4:e4b` first-token / token-rate probe
  (`python3 docs/research/m2_4b_speech_flow/ollama_timing.py`).

- `gap_profile_raw_20260906.txt` — raw output of `gap_profiler.py` on this
  Pi 5, 2026-09-06 (ALSA/pipecat-linking noise filtered).

## Headline measurements (this Pi 5, 2026-09-06)

| Thing | Value |
|---|---|
| Piper `pl_PL-gosia-medium` synth, per sentence (65–112 chars) | 0.6–0.9 s → 4.6–6.8 s audio, **RTF ≈ 0.13** |
| Piper, whole 456-char reply in one request | 4.0 s → 29.3 s audio, RTF 0.14 |
| Measured Polish speech rate (gosia) | ≈ 15.6 chars/s ≈ **2.5 words/s** |
| `gemma4:e4b` warm, tiny prompt, uncontended | first-token **1.7 s**, **2.8 tok/s** |
| `gemma4:e4b` under CPU contention (Piper + pipeline active) | first-token **stalled minutes** (reproduced the operator's "tens of seconds") |
| Scripted pipeline, 3 tok/s feed | first audio **+7.9 s**; production gaps **4.5 / 5.9 / 2.1 / 4.8 s**; 3× `BotStopped`→`BotStarted` |
| Scripted pipeline, 2 tok/s feed | first audio **+12.3 s**; production gaps **6.9 / 6.8 / 4.4 / 6.5 s** |

## M2.4B.1 additions (2026-09-06 — see R0013)

- `buffer_validation.py` + `buffer_validation_raw_20260906.txt` — runs the
  real M2.4 output path with the in-tree `MetricsCollector` and a 200 ms
  periodic buffer sampler; compares `buffered_audio_seconds` reaching ≤ 0
  against real `BotStoppedSpeaking`. Result: at 3 tok/s the estimate hit 0
  at the exact BotStopped (`estimate_vs_real_stop_error_s = 0.0`); at
  2 tok/s it stayed > +3.6 s with no underruns and no audible gaps — the
  3 s-`stop_frame_timeout_s` `BotStopped`/`BotStarted` churn is cosmetic
  while the buffer holds.
- `first_token_contention.py` + `first_token_contention_raw_20260906.txt` —
  controlled first-token latency (product model unchanged). Idle run 1 =
  33 s with `load_duration` 31 s (model **eviction + reload**); warm =
  0.65 s; with Piper synthesising concurrently `prompt_eval` 0.66 s → 16 s
  and generation 2.9 → 0.15 tok/s. Two distinct first-token causes, both
  LLM-layer.

## M2.4B.1A additions (2026-09-07 — see R0014, CPU contention spike)

- `piper_cores.py` + `piper_cores_raw_20260907.txt` — Piper `pl_PL-gosia-medium`
  RTF vs CPU-core budget (`taskset -a -pc`), uncontended, 6 reps each.
  **1 core → RTF 0.485 (worst 0.496) — comfortably ~2× faster than real
  time**; 2 → 0.234; 3 → 0.195; 4 (control) → 0.137. Piper uses ~2.5 cores
  unrestricted, ~0.77 when pinned to one. **Answer to "can Piper live on
  one core?": yes.**
- `cpu_strat_fast.py` + `cpu_strat_fast_raw_20260907.txt` — five CPU
  strategies, ~35 s concurrent LLM+Piper window each, ordinary CFS only (no
  SCHED_FIFO/RR). CONTROL (both free): `gemma4:e4b` `prompt_eval` **24 s**,
  **1.3 tok/s** — the intra-response-silence mechanism. **C (Piper
  `renice +10`, no affinity): `prompt_eval` 0.91 s, 3.1 tok/s** (back to
  uncontended) while Piper still ran at RTF 0.44 — the recommended
  topology. D (1-core pin **+** nice) starved Piper to RTF 0.88 — never
  stack the two. A/B/D LLM figures are model-eviction noise.
- `buffer_validation.py` (updated) + `buffer_validation_raw_20260907.txt` —
  re-run with the corrected metrics + a bursty mid-reply-stall scenario.
  On the bursty turn (the shape that gave the operator's spurious ~18 s):
  `buffer_drain_to_stop_lag_s = -0.03 s`, `underruns_without_following_stop
  = 0`, `diagnosis = LLM TEXT PRODUCTION`, real gaps `[6.5, 3.5] s`,
  `context-span RTF 0.60` vs true synthesis ~0.24 (metric bug visible).

## M2.4B.2 additions (2026-09-07 — see R0015, speech planner)

- `m24b2_accept.py` + `m24b2_accept_raw_20260907.txt` — scripted
  hardware-acceptance harness for the `NexaSpeechPlanner`. Real
  `gemma4:e4b` (Ollama) + real Piper HTTP + real
  `LocalAudioOutputTransport` + `ConversationSession`; only the
  microphone/whisper.cpp is replaced by the three scripted operator
  transcripts from the B.2 brief. Captures the exact phrase text handed to
  Piper *after* the planner (`_push_tts_frames` "Generating TTS […]") and
  the assistant text `ConversationSession` stored (transcript invariant).
  Result: `1.`/`2.`/`*` list markers and every `**` gone; list → prose
  with `oraz`; abbreviations never isolated; canonical history
  byte-identical to the raw model output. Long intra-response silence
  still present — the B.3 target.

## M2.4B.2A additions (2026-09-07 — see R0016, edge-case fix)

The real operator mic run (`/tmp/nexa_m24b2_operator.jsonl` +
`_console.txt`) confirmed B.2 removed the fragmentation it targeted
(`tiny_text_chunk_count = 0` all 5 turns; `np. → na przykład`; no isolated
`tzw.`/`1.`/`**`; true Piper RTF ~0.24–0.30) — and exposed two gaps, both
fixed in B.2A:

- **LaTeX math leaked to Piper** — `gemma4:e4b` emits `$\text{H}$` /
  `$\text{He}$` for element symbols inside bolded list titles.
  `normalize_for_speech` now has `_strip_math` (`$…$`, `\(…\)`, `\[…\]`,
  `\text{}` → readable text; equations never interpreted).
- **Dangling trailing `"("` / mid-word chunk** — traced to
  `GenerationOptions.num_predict = 200` (`providers/base.py`): the model
  hit its token cap mid-word / right after `(`, and the planner's
  final-flush emitted the buffer verbatim. `_tidy_spoken` now trims a
  dangling trailing opener from the *spoken* copy (never the transcript);
  a genuine mid-word partial is kept (transcript truth, no invented
  completion). The planner's boundary logic already cannot cut inside a
  word.

Also recorded: long intra-response silence persists (≈ 19.6 / 13.5 /
39.1 s gaps; CPU ~99–100 %; turns 2–5 diagnose LLM TEXT PRODUCTION) — the
B.3 target. And a **separate STT-quality issue** ("horyzont zdarzeń"
mistranscribed as "chory zęzdarzyń" etc.) — flagged, not fixed here.

## M2.4B.3.1 additions (2026-09-07 — see R0017, Piper priority + context continuity)

B.2/B.2A `OPERATOR-CONFIRMED` (fresh mic run: *"the spoken response itself
is good if we ignore the pauses"*; `$\text{H}$` never reached Piper;
`tiny_text_chunk_count = 0`; true Piper RTF ~0.27).

- `b31_compare.py` + `b31_compare_raw_20260907.txt` — scripted before/after
  for B.3.1: warm-up (`"Ile jest osiem razy siedem?"`, not measured) then
  measured (`"Powiedz mi, z czego składa się gwiazda."`), CONTROL (Piper
  nice 0 / `stop_frame_timeout_s` 3 s) vs B.3.1 (nice 10 / 8 s). Real
  gemma4:e4b + Piper + audio transport; mic/STT scripted.
- B.3.1 result: Piper RTF unchanged at nice +10 (~0.26, still ~4× real
  time); `gemma4:e4b` chars/s unchanged (~8.5) in this low-contention
  scripted workload; **a multi-second inter-phrase gap remains** (~8–11 s;
  `audio-s per wall-s ~0.5`; `diagnose = LLM TEXT PRODUCTION`). The first
  phrase buys ~4 s of audio while the next phrase's text takes ~14 s to
  generate. **B.3.2 (buffer-aware look-ahead / refill) is still needed.**

## M2.4B.3.2A additions (2026-09-07 — see R0018, rate budget)

- `rate_budget_sim.py` + `rate_budget_sim_raw_20260907.txt` — **offline
  deterministic** production/consumption budget over already-measured
  per-phrase data (operator JSONL + B.3.1 raw). No runtime component;
  self-checks against the measured gaps (CONTROL `[10.87, 7.03]` s;
  operator star `39.4` s).
- **`realtime_text_ratio ≈ 0.53`** — `gemma4:e4b` produces spoken text at
  ~53 % of the rate `pl_PL-gosia-medium` consumes it (8.5 vs 15.5
  chars/s). Below 1.0 → **no finite steady-state buffer makes an
  arbitrarily long reply continuous.** A prebuffer only relocates silence
  to the front (`wall_to_finish` invariant); batching cannot move the
  first underrun (can't synthesize non-existent text). `length_scale ≤
  1.10` closes ≤ 11 % of the deficit. Asymptotic fix = ~2× faster
  generation (~15.9 chars/s). B.3.2 = small controller **scoped to short
  conversational replies** + reply-length shaping (separate track).

## M2.4B.3.2 additions (2026-09-07 — see R0019, continuity controller)

- `NexaSpeechContinuityController` (`src/nexa/voice_tts/continuity.py`):
  phrase 0 immediate; phrases 1..N released as soon as the ESTIMATED audio
  reserve is low, held ≤ 0.4 s only while it is healthy. Never batches to
  grow, never adds silence, never changes text or speech rate. B.3.1's
  Piper `nice +10` + `stop_frame_timeout_s = 8` unchanged.
- `b32_replay.py` + `b32_replay_raw_20260907.txt` — offline replay of the
  R0018 timelines through the REAL `decide_release` policy. Result: the
  controller **never** makes first audio later / the first underrun
  earlier / total silence higher; on the R0018 timelines (LLM behind) it
  changes **nothing** (reserve already low → immediate release); its
  holds only engage when the LLM is *ahead*, then ≤ 0.4 s. It **cannot**
  eliminate the operator-star ~39 s stall — R0018's rate constraint.
- `b32_ab_raw_20260907.txt` — scripted hardware A/B (CONTROL vs targets
  1.5 / 2.0 / 2.5) on the short-answer prompts.

## One-line conclusion

Synthesis is ~7× faster than real-time and is **not** the bottleneck. The
gaps are (a) waiting for the LLM to finish each next sentence's text —
`gemma4:e4b` at ~2.8 tok/s sits right at Polish speech rate with no headroom
to build an audio cushion — plus (b) `SimpleTextAggregator`'s per-boundary
lookahead and English-only NLTK splitting, and (c) Pipecat's 3 s
context-idle timeout turning LLM inter-sentence stalls into
`TTSStopped`/`TTSStarted` splits. First-token latency is a separate
LLM-serving/contention problem.
