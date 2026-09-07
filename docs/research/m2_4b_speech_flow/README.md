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

## One-line conclusion

Synthesis is ~7× faster than real-time and is **not** the bottleneck. The
gaps are (a) waiting for the LLM to finish each next sentence's text —
`gemma4:e4b` at ~2.8 tok/s sits right at Polish speech rate with no headroom
to build an audio cushion — plus (b) `SimpleTextAggregator`'s per-boundary
lookahead and English-only NLTK splitting, and (c) Pipecat's 3 s
context-idle timeout turning LLM inter-sentence stalls into
`TTSStopped`/`TTSStarted` splits. First-token latency is a separate
LLM-serving/contention problem.
