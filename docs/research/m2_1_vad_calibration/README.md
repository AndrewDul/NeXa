# M2.1 VAD endpointing calibration — raw artifacts

Evidence for `docs/architecture/M2_1_LOCAL_AUDIO_VAD_ARCHITECTURE.md` §7 and
the M2.1 implementation report's "REAL HARDWARE TEST" section.

- `vad_offline_calibration.py` — deterministic offline calibration script.
  Runs the real `pipecat.audio.vad.silero.SileroVADAnalyzer` (same class the
  live `nexa.voice.runtime.VoiceRuntime` uses) against real recorded Polish
  speech (`docs/research/m2_voice_spikes/asr_test_samples/`, already
  committed) with precisely inserted real-silence gaps (extracted from the
  same recordings' own room-noise floor, not digital zeros) of exactly
  0.4/0.6/0.8/1.0/1.2 s, swept against `stop_secs` in {0.2, 0.6, 0.8, 1.0}.
  No wall-clock timing, no human pause-length imprecision, no microphone
  needed — `VADAnalyzer`'s confirmation logic is purely audio-frame-count
  based, so this reproduces the exact logic the live pipeline uses.
  Reproducible: `python3 vad_offline_calibration.py <output.json>` (needs the
  project's `.venv` with `pipecat-ai[local]` installed).
- `vad_calibration_results.json` — the raw sweep output (20 rows: 4
  `stop_secs` candidates × 5 gap durations), each recording whether that gap
  was incorrectly confirmed as a stop (`stopped_during_gap`) and, if so,
  after how many seconds (`stop_latency_s`).

Context: this calibration exists because a live-hardware test with
`stop_secs=0.2` (Pipecat/Silero's own library default) split one natural
Polish sentence into three separate turns, and a first live retest at
`stop_secs=0.8` was correctly challenged (the aimed-for ~0.5 s/~0.8 s pauses
actually measured ~1.66 s/~1.82 s, which is evidence `0.2` is too short but
not evidence of where the right threshold is). This deterministic sweep
answers that question precisely instead of relying on further imprecise
human-timed pauses.
