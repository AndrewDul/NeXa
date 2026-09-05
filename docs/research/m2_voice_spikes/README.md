# M2.0A voice feasibility spikes — raw artifacts

Raw evidence for `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md` / `R0006`.

- `asr_test_samples/` — the exact 12 PL/EN test recordings (real human speech,
  recorded via the reSpeaker mic) already used by legacy NeXa's faster-whisper
  benchmark (`smart-desk-ai-assistant/var/data/asr_test_samples/`, copied
  read-only for a same-material comparison) + `index.json` (expected text,
  language, recording metadata).
- `whisper_cpp_bench_results.json` — Spike 1 main sweep: 4 configs
  (tiny/fp16, tiny/q8_0, base/fp16, base/q8_0) × 12 files, whisper.cpp
  defaults (beam_size=5, best_of=5), language-hinted.
- `whisper_cpp_bench_bs1_results.json` — Spike 1 supplemental: tiny/fp16 and
  base/fp16 at beam_size=1 (matched to legacy's faster-whisper beam1 configs),
  isolating decode-strategy effects from runtime effects.
- `spike2_resource_budget_results.json` — Spike 2: stages A-H (VAD/STT/LLM/TTS
  isolated and combined), with system RAM/swap/load/temp/throttle sampled
  throughout each stage.
- `scripts/` — the exact throwaway harness scripts used to produce the above
  (stdlib + `whisper-cli`/`whisper-vad-speech-segments`/`piper` subprocess
  calls). **Not standalone-runnable as committed** — they reference an
  external scratch build (`/home/devdul/nexa_m2_spike_build/whisper.cpp`,
  built from `ggml-org/whisper.cpp` with `cmake -B build
  -DCMAKE_BUILD_TYPE=Release`, models via `models/download-ggml-model.sh
  {tiny,base}` + `whisper-quantize ... q8_0` + `models/download-vad-model.sh
  silero-v5.1.2`, and a scratch venv with `piper-tts` + the legacy
  `pl_PL-gosia-medium.onnx` Piper voice copied read-only) that was
  deliberately kept out of the NeXa repo/venv per the task's testing
  discipline. Kept here for exact-command reproducibility, not for reuse as
  library code.

None of this is NeXa product code. `src/nexa/` and `tests/` are unaffected by
this research.
