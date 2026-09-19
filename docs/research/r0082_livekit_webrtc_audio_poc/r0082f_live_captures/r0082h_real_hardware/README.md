# R0082-H REAL HARDWARE evidence archive

This directory holds a byte-identical local copy of the four artifacts
produced by the **one REAL R0082-H hardware run** (continuous
silent-user, 10-episode session). It is a preservation copy, not the
original capture location.

- **Classification: REAL HARDWARE** (not a dry run, not synthetic).
  Distinct from `../r0082h_dryrun_synthetic_NOT_real_hardware/`,
  which holds only synthetic dry-run evidence from a local
  `livekit-server --dev` + fake-hardware stand-in and must never be
  confused with this directory.
- **Run id:** `20260919T105048Z`
- **Room:** `r0082h_silentseries_real_001`
- **Result:** `VALID TEST -- PASS` -- 10/10 episodes, 0 accepted VAD
  starts, 0 confirmed interruptions, 0 frames with `silero_prob>=0.7`,
  global max probability `0.64971`. Full independent verification in
  `docs/reports/R0082_livekit_webrtc_audio_poc_20260918.md`, section
  "R0082-H REAL HARDWARE continuous silent-user run".
- **Hardware:** input = reSpeaker XVF3800 4-Mic Array (Analog Stereo,
  default); output = UACDemoV1.0 (Analog Stereo, default); AEC ON.

## Original path

The original, untouched artifacts (this directory holds a copy of
them, not a move) live one level up, at:

```
docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_captures/
  r0082h_silentseries_20260919T105048Z_mic_48k.wav
  r0082h_silentseries_20260919T105048Z_mic_16k.wav
  r0082h_silentseries_20260919T105048Z_timeline.csv
  r0082h_silentseries_20260919T105048Z_milestones.json
```

## Archive path (this directory)

```
docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_captures/r0082h_real_hardware/
  r0082h_silentseries_20260919T105048Z_mic_48k.wav
  r0082h_silentseries_20260919T105048Z_mic_16k.wav
  r0082h_silentseries_20260919T105048Z_timeline.csv
  r0082h_silentseries_20260919T105048Z_milestones.json
  MANIFEST.sha256   (tracked in git)
  README.md         (this file, tracked in git)
```

## SHA256 (see `MANIFEST.sha256` for the machine-checkable form)

```
5412325e23a908008ccd1286d574c28862dc36bd242e8bd859d1477fd84f9161  r0082h_silentseries_20260919T105048Z_mic_48k.wav
735af9de4c3a76f46d9082efbe46c53f3eaf7984b6d3fc899518bed8fff5e1f6  r0082h_silentseries_20260919T105048Z_mic_16k.wav
e7d911f200160bf6561d8341a9b67cfe7b6ed57212c4dd670ad933c7af84c108  r0082h_silentseries_20260919T105048Z_timeline.csv
2393a11d3d03b9d85b58f2ed9b42260897b64936403e67826b328c7d764a667e  r0082h_silentseries_20260919T105048Z_milestones.json
```

Verify with:

```
sha256sum -c MANIFEST.sha256
```

## Why the WAV/CSV/JSON themselves are intentionally local/untracked

This repository has an established, existing convention -- documented
in `.gitignore` for R0048 (`ingress_captures/`, "real operator voice
recordings -- WAV + JSON, never committed"), R0052
(`self_echo_captures/`, "real hardware telemetry JSON -- never
committed"), and R0067 (`r0067_aec3_offline_output/`,
`r0067_validation_20260916/`, "same convention as
self_echo_captures/") -- of never committing raw real-audio-capture or
real-hardware-telemetry binaries to the repository. Every capture
directory under R0082 (`r0082_aec_captures/`, `r0082c_aec_captures/`,
`r0082d_aec_captures/`, `r0082e_16k_captures/`,
`r0082f_live_captures/` including this `r0082h_real_hardware/`
subdirectory) has consistently followed the same convention throughout
this entire investigation: `git status` has shown all of them as
untracked (`??`) at every prior round, and none has ever been staged.

This directory does NOT override that policy. The four evidence files
(`*.wav`, `*.csv`, `*.json`) remain local-only and untracked, now made
explicit via a scoped `.gitignore` entry (matching the exact style of
the existing R0048/R0052/R0067 entries) so they are also protected
from an ordinary `git clean -fd`. Only this `README.md` and
`MANIFEST.sha256` are tracked in git, so the run's identity, hashes,
and classification survive even if the local machine's untracked
capture files are ever lost -- a future session (or operator) can use
this manifest to verify a re-obtained copy of the same evidence, or at
minimum know exactly what existed and its exact hash.
