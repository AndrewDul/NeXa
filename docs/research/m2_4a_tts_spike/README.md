# M2.4A TTS spike — raw scripts and how to reproduce

Throwaway research material (not NeXa product code) supporting
`docs/reports/R0010_m2_4a_pipecat_streaming_tts_spike_20260906.md`.

## Setup used for this spike (not committed as project dependencies)

```
./.venv/bin/pip install "piper-tts[http]"   # GPL-3.0-or-later, spike-only,
                                             # NOT added to pyproject.toml
```

Two small "low" quality voices (16kHz, ~63MB each) downloaded to
`~/.local/share/nexa/tts-spike-voices/` (outside git, outside this repo —
same external-data-dir discipline as M2.2's whisper.cpp models):

```python
from pathlib import Path
from piper.download_voices import download_voice
d = Path.home() / ".local/share/nexa/tts-spike-voices"
download_voice("en_US-amy-low", d)
download_voice("pl_PL-mls_6892-low", d)
```

Running a Piper HTTP server (the real, correct endpoint is `/synthesize`,
not `/` — see R0010 §"HTTP ENDPOINT COMPATIBILITY"):

```
./.venv/bin/python3 -m piper.http_server -m en_US-amy-low \
    --data-dir ~/.local/share/nexa/tts-spike-voices --port 5001
./.venv/bin/python3 -m piper.http_server -m pl_PL-mls_6892-low \
    --data-dir ~/.local/share/nexa/tts-spike-voices --port 5002
```

## `pipeline_smoke_test.py`

A minimal real Pipecat pipeline: a scripted `LLMTextFrame` source (standing
in for `ConversationSession.send()`'s token stream) → `PiperHttpTTSService`
(configured with `base_url="http://127.0.0.1:<port>/synthesize"`) →
`LocalAudioOutputTransport` (real reSpeaker output). Proves the frame
sequence and sentence aggregation end to end; prints a timestamped event
log. Real reSpeaker hardware required (resolves the `"respeaker"` output
device by name, same as `nexa.voice`).

```
python3 docs/research/m2_4a_tts_spike/pipeline_smoke_test.py --lang en
python3 docs/research/m2_4a_tts_spike/pipeline_smoke_test.py --lang pl
```

Note: calling `PiperHttpTTSService.run_tts()` directly, outside a running
Pipecat pipeline (skipping `setup()`/`start()`), silently yields zero
frames and zero errors — `self.sample_rate`/`self.chunk_size` default to 0
until the pipeline lifecycle sets them, and `iter_chunked(0)` then yields no
chunks. This is a real gotcha discovered while building this spike, not
guessed — do not test `run_tts()` in isolation; always drive it through a
real `Pipeline`/`PipelineWorker`/`WorkerRunner`, as `nexa.voice.runtime`
already does for the other services.
