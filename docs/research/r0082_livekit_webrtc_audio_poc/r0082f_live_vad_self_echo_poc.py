#!/usr/bin/env python3
"""R0082-F — MINIMAL LIVE production-equivalent VAD self-echo test.

**No Gemini, no LLM, no full NeXa Core, no ConversationSession, no real
conversational loop, no `AecReferenceFeeder`, no XVF3800.** Answers ONE
question: while NeXa-like speech is actually playing live through the
real speaker and the human user stays completely silent, does the LIVE
`PlatformAudio` -> Silero VAD path generate any false
`UserStartedSpeaking`-equivalent events, in real time, with real
hardware?

## Hardware attempt #1 — FAILED (self-read on a `PlatformAudioSource`
## track yields ZERO frames), now RETIRED from the canonical path

The first real hardware run (`r0082f_silent_user_...`, AEC ON)
completed cleanly at the transport level — `PlatformAudio` connected,
published the real reSpeaker mic, subscribed to the speech track, the
LiveKit server showed the hardware mic genuinely flowing through the
room with no packet loss, and the speech participant completed its
full PRE_ROLL/PLAYBACK/TAIL sequence and disconnected cleanly. But the
evidence WAVs it produced were literally empty (`nframes=0`, 44-byte
header-only files, confirmed via `wave.open()` and SHA256
`531cd597...17caa0` / `ba584a37...24f0fb`) and the timeline CSV
contained zero data rows. **The prior round's documented
"self-read your own `PlatformAudioSource`-backed `LocalAudioTrack` via
`rtc.AudioStream`" design — previously confirmed only for a SYNTHETIC
`rtc.AudioSource`-backed track — does NOT generalize to a real
`PlatformAudioSource`-backed track**: `PlatformAudioSource`'s own
docstring already hinted at this ("frames are captured and sent
directly by the ADM"), and this is now the empirically CONFIRMED
failure mode, exactly the risk the prior round's own LIMITATIONS
section flagged and provided a fallback for.

**This self-read design is RETIRED from the canonical R0082-F path.**
It is preserved here only as documented history — the code that
performed it has been removed from `run_hardware_role`, which now does
nothing but own `PlatformAudio` (mic capture, speaker playout, AEC) and
publish its mic track, exactly like R0082-D's own `run_hardware_role`.

## Canonical path (this version) — REMOTE subscription, proven in R0082-D

The VAD observation point has moved to the SPEECH participant, reusing
the exact remote-subscription + `rtc.AudioStream` pattern R0082-D's own
`run_test_role` already proved works with REAL captured hardware mic
audio (non-empty WAV evidence, real RMS/peak values, all four R0082-D
runs). `--role speech` now does TWO things concurrently: (A) publish
the frozen speech stimulus, and (B) subscribe to and continuously
consume the hardware's REMOTE mic track, live, through the SAME
`StreamingResampler` -> Silero -> `VADAnalyzer` -> `InterruptionStateMachine`
chain built last round (unchanged, still validated — see R0082-F's own
report §87 for the whole-array/irregular-chunk/impulse/frequency
validation, none of which is invalidated by this topology change: the
resampler and VAD chain code themselves are untouched).

```
PROCESS A (--role hardware)              PROCESS B (--role speech)
  rtc.PlatformAudio()                      rtc.AudioSource (synthetic)
  real reSpeaker mic, real UAC playout      publishes the frozen speech WAV
  publishes its own mic track                      |
  subscribes to B's speech track  <----------------+ (triggers automatic
  (AEC far-end reference)                            playout)
        |                                           |
        | (does NOT self-read; does NOT run VAD)     | subscribes to A's
        |                                            | REMOTE mic track
        v                                            v
  holds for the fixed measurement                rtc.AudioStream(remote
  lifetime, then cleans up                        hardware mic track)
                                                        v
                                                 FIRST-FRAME GATE (mandatory,
                                                 bounded timeout -- see below)
                                                        v
                                                 StreamingResampler (48kHz->16kHz)
                                                        v
                                                 Level 1: SileroOnnxModel
                                                        v
                                                 Level 2: VADAnalyzer state
                                                        v
                                                 Level 3: REAL InterruptionStateMachine
                                                        v
                                                 CSV + milestone log, running
                                                 CONCURRENTLY with playback
                                                 (asyncio tasks, not sequential)
```

Still exactly TWO OS processes — no third relay process was needed;
R0082-D's own remote-subscription pattern already solves this without
one. No shared `Room`/`PlatformAudio`, no Python `multiprocessing`/
`fork()` — R0082-C's own `RaceDetected()` fix, unchanged.

## Mandatory first-frame gate — fail-closed, not silently accepted

The prior attempt's `0 VAD frames` was never explicitly checked against
— it was silently treated as if the run had simply found nothing
noteworthy. **That must never happen again.** `run_speech_role` now:

1. Subscribes to the hardware's remote mic track.
2. Creates `rtc.AudioStream(remote_mic_track, sample_rate=48000,
   num_channels=1)` and starts a background consumer task immediately.
3. **Waits (bounded, `FIRST_FRAME_TIMEOUT_S`) for that consumer to
   report its FIRST real decoded frame** — logging the frame's own
   `sample_rate`/`num_channels`/`samples_per_channel` (verified, never
   assumed) — **before** starting the 2s PRE_ROLL countdown.
4. If no real frame arrives within the timeout, the script prints
   `TEST INVALID` and raises `SystemExit` — it does **not** play the
   speech stimulus, and does **not** produce a misleading PASS/FAIL
   summary.
5. At the very end, an explicit validity check
   (`remote_audio_frames_received`, `remote_audio_samples_received`,
   `vad_frames_processed`, `capture_duration_s`,
   `expected_min_duration_s`, `capture_complete`) gates the final
   report: if `vad_frames_processed == 0`, the script prints
   `INVALID TEST — NO VAD INPUT FRAMES` instead of any PASS/FAIL
   language, even if it somehow got that far.

## `BargeInController` audit (read-only, NOT modified, NOT used) — unchanged from last round

`src/nexa/voice/bargein.py` was read in full (no execution, no
modification) last round. Exactly one piece of its behavior depends on
`AecReferenceHealth`:

```python
def _handle_speech_started(self) -> None:
    if not self._sm.response_in_flight:
        return
    if not self._aec.barge_in_safe:        # <- the ONLY gating dependency
        ...
        return
    ev = self._sm.speech_started(self._now())
```

`barge_in_safe` is specific to the CURRENT production XVF3800/
`AecReferenceFeeder` hardware AEC path (R0028) — `PlatformAudio`'s
WebRTC AEC has no equivalent discrete health signal exposed to Python.
This round still does NOT insert `BargeInController` — the canonical
chain stops at the real `InterruptionStateMachine` directly.
`BargeInController` remains NOT validated on `PlatformAudio`.

## AEC state: still **ON**, unchanged, not re-swept

Per instruction, this round keeps AEC ON (the only configuration that
would ever ship) and does not reopen the OFF/ON comparison.

## Important scientific limitation — stated explicitly, not hidden

The VAD process now observes the mic AFTER: `PlatformAudio` capture ->
WebRTC sender -> the local LiveKit server -> Opus/RED transport &
decode -> the remote `rtc.AudioStream`. It does **not** directly tap
local post-AEC PCM inside the hardware process (that was the retired,
failed self-read design). **This is a research OBSERVATION point, not
the final desired NeXa production routing.** It is used here because:
it is now proven (via R0082-D) to carry the real hardware mic; it
preserves live timing; it lets THIS round's question (does live Silero
false-trigger on this residual) be answered; and no simpler,
already-proven alternative exists. This round does not claim this
network round-trip routing is, or will become, NeXa's production audio
path.

## Environment / reuse — unchanged from last round

`StreamingResampler`, `SileroOnnxModel`, `VolumeTracker`, `LiveVadChain`,
and `load_interruption_state_machine_class()` are UNCHANGED from last
round (validated in R0082-F's own report §87 — whole-array vs.
regular/irregular chunking bit-for-bit identical, impulse test
bit-for-bit identical, frequency/alias test clean, both VAD controls
PASS). `onnxruntime==1.24.4`/`loudness==0.2.0`/`scipy` remain installed
only in the isolated probe venv (operator-approved), confirmed by
`pip check` to have caused no dependency drift.

## R0082-G ADDENDUM — deliberate human barge-in mode (`--test-mode
## deliberate-bargein`), additive only, silent-user mode UNCHANGED

R0082-F (above) only proves the live chain does not spuriously fire
while the human stays silent. It cannot show the chain correctly
detects and acts on a REAL human interruption. `--test-mode
deliberate-bargein` (default remains `silent-user`, byte-identical to
everything above) adds exactly that, on the SAME architecture: no
return to local self-read, no VAD config changes.

**Cue timing** — chosen from an offline short-time-RMS scan of the
frozen stimulus (50ms window/25ms hop, 200-count int16 RMS silence
threshold; see R0082's own report for the full trace). The scan found
a continuous active-speech run from **6.475s-7.600s** (English
portion, well clear of both the initial silence and the ~0.6s EN/PL
gap later in the file). The cue is fixed at:

```
BARGEIN_CUE_START_TIME_S = 4.0   (playback-relative) -- "BARGE-IN IN 3"
                             5.0                     -- "BARGE-IN IN 2"
                             6.0                     -- "BARGE-IN IN 1"
BARGEIN_SPEAK_NOW_TIME_S = 7.0   (playback-relative) -- ">>> SPEAK NOW <<<"
```

`SPEAK NOW` lands inside the 6.475-7.600s active-speech run with 0.6s
of continuing deterministic speech still to come after it, and the
operator's own post-reaction-time speech is expected to overlap
partly with the tail of that run and partly with the following
7.925-9.300s run (separated by only a 0.325s natural TTS pause) --
i.e. the operator is very likely to be speaking WHILE the deterministic
stimulus is still audible, which is the condition under test. The
countdown is delivered as terminal text only (no speaker beep, per
instruction -- a beep would itself be an acoustic event captured by
the mic and would contaminate the VAD/interruption evidence) via a
separate `asyncio.sleep`-paced task, not tied to frame-submission
counting.

**Playback cancellation** -- real, not faked. The frame-submission loop
checks a shared `asyncio.Event` (`interrupt_confirmed_event`) once
per 10ms frame and stops calling `signal_source.capture_frame()`
entirely the moment it is set; the event is set from exactly one place
(`_consume_mic`'s VAD chain, the instant `chain.confirmed_events`
grows), i.e. only a real `InterruptionStateMachine`
`INTERRUPT_CONFIRMED` can set it -- not raw Silero probability, not a
candidate start. There is no retry/resume code path anywhere in the
loop, so once broken the loop cannot restart.

**Validity criteria are SEPARATE from silent-user mode** -- deliberate-
bargein mode does not require the full ~27.18s capture; it requires
capture through `max(speak_now boundary, first confirmed interruption
+ 1.0s margin)` if a confirmed interruption occurred, else the full
duration (nothing legitimately shortened it if no interruption fired).

**Pre-cue false positives are tracked, reported in full detail
(`pre_cue_started`/`pre_cue_confirmed`), AND wired into the canonical
verdict as `FAIL-E`** -- a false accepted VAD start or confirmed
interruption before the cue's own countdown-start boundary is the
ORIGINAL self-echo failure mode and forces the overall run to FAIL,
regardless of whether the later genuine post-cue barge-in also
succeeds. **Correction (post-preparation-round fix):** the first
implementation computed `pre_cue_false_positive` but did not consult it
in the verdict chain, so a run could report
`pre_cue_false_positive=True` alongside `VERDICT=PASS`. Fixed by
checking it immediately after instrumentation validity and before any
of the post-cue A/B/C/D checks -- see the FAIL taxonomy below and
`evaluate_deliberate_bargein_result()`'s own exact `if/elif` chain.

**FAIL taxonomy** (distinct classes, per instruction), checked in this
exact order:
F = test instrumentation invalid (first-frame gate, zero VAD frames,
or capture too short even under the relaxed deliberate-mode rule);
E = a false accepted VAD start or confirmed interruption occurred
BEFORE the cue (`pre_cue_false_positive`) -- checked next, before any
post-cue outcome is considered;
A = operator spoke, no accepted post-cue VAD start;
B = accepted post-cue VAD start, no post-cue `INTERRUPT_CONFIRMED`;
C = `INTERRUPT_CONFIRMED` occurred but playback did not verifiably stop
early (frame submission reached natural completion);
D = playback stopped then resumed (structurally impossible by this
loop's design -- checked anyway).
PASS = exactly the expected post-cue chain AND `pre_cue_false_positive
is False`. `VERDICT=PASS` can no longer coexist with a pre-cue false
positive.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import csv
import hashlib
import importlib.util
import os
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r0082_audio_utils import _write_wav  # noqa: E402  (local, stdlib-only)

try:
    from livekit import api, rtc
except ModuleNotFoundError as e:  # pragma: no cover -- environment-dependent, by design
    print(
        "ERROR: `livekit`/`livekit.api` is not importable in this Python environment.\n"
        "This script deliberately does NOT run in NeXa's own .venv -- see this "
        "module's own docstring and R0082's report for the exact isolated-venv "
        f"install command. ({e})",
        file=sys.stderr,
    )
    raise SystemExit(1) from e

# ----------------------------------------------------------------------
# Constants -- audio / timing
# ----------------------------------------------------------------------
LIVE_SAMPLE_RATE = 48000  # what we request from rtc.AudioStream
VAD_SAMPLE_RATE = 16000  # production Silero input rate
DECIM = LIVE_SAMPLE_RATE // VAD_SAMPLE_RATE  # exact 3:1
FRAME_SAMPLES_LIVE = 480  # 10ms @ 48kHz, matches every prior R0082 script
VAD_FRAME_SAMPLES = 512  # 32ms @ 16kHz, production Silero frame size

SPEECH_DURATION_S = 23.181416666666667
PRE_ROLL_S = 2.0  # "at least 2s" per instruction
TAIL_S = 2.0  # "at least 2s" per instruction
SETTLE_S = 1.0  # matches R0082-C/D's own post-subscribe settle
SUBSCRIBE_TIMEOUT_S = 30.0  # matches R0082-C/D
FIRST_FRAME_TIMEOUT_S = 15.0  # bounded wait for the first REAL remote mic frame
# Bumped from R0082-F round 1's 3.0s: the hardware role's hold is a fixed
# budget (unchanged), but the speech/VAD role now has an ADDITIONAL
# first-frame-gate wait before its own pre-roll starts -- this margin
# absorbs that extra (expected-to-be-short) delay so the hardware role
# does not disconnect before the speech/VAD role finishes its capture.
CLEANUP_GRACE_S = 6.0

# Production VAD values -- unchanged, re-audited last round against
# current source, confirmed byte-for-byte identical to R0082-E's own
# audit. NOT re-audited again this round since nothing in the codebase
# changed (per instruction).
VAD_CONFIDENCE = 0.7
VAD_START_SECS = 0.2
VAD_STOP_SECS = 1.0
VAD_MIN_VOLUME = 0.6
FRAMES_PER_SEC = VAD_FRAME_SAMPLES / VAD_SAMPLE_RATE
VAD_START_FRAMES = round(VAD_START_SECS / FRAMES_PER_SEC)
VAD_STOP_FRAMES = round(VAD_STOP_SECS / FRAMES_PER_SEC)
VOLUME_WINDOW_SECS = 0.4
VOLUME_SMOOTHING_FACTOR = 0.2
MODEL_RESET_INTERVAL_S = 5.0

HARDWARE_IDENTITY = "r0082f_hardware"
SPEECH_IDENTITY = "r0082f_speech"

# ----------------------------------------------------------------------
# R0082-G -- deliberate human barge-in mode constants (additive; do not
# affect --test-mode silent-user, which is the default and unchanged).
# ----------------------------------------------------------------------
BARGEIN_SPEAK_NOW_TIME_S = 7.0  # playback-relative; see module docstring
BARGEIN_CUE_LEAD_S = 3.0  # "3, 2, 1" at 1s apart before SPEAK NOW
BARGEIN_CUE_START_TIME_S = BARGEIN_SPEAK_NOW_TIME_S - BARGEIN_CUE_LEAD_S  # 4.0
OPERATOR_PHRASE_PL = "Przerwij, teraz opowiedz mi o czymś innym."
POST_INTERRUPT_MARGIN_S = 1.0  # min capture required after a confirmed interruption
MIN_DELIBERATE_CAPTURE_S = PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S + 0.5

CSV_HEADER_BARGEIN = [
    "timestamp_monotonic",
    "audio_relative_timestamp_s",
    "silero_prob",
    "confidence_threshold",
    "smoothed_volume",
    "volume_threshold",
    "vad_state",
    "candidate_start",
    "vad_user_started_speaking_equivalent",
    "vad_user_stopped_speaking_equivalent",
    "interruption_state",
    "interrupt_confirmed",
    "playback_active",
    "playback_cancel_requested",
]

EXPECTED_INPUT_NAME_SUBSTRING = "reSpeaker"
EXPECTED_OUTPUT_NAME_SUBSTRING = "UACDemoV1.0"

DEFAULT_URL = "ws://127.0.0.1:7880"
DEFAULT_API_KEY = "devkey"
DEFAULT_API_SECRET = "secret"

REPO_ROOT = Path(__file__).resolve().parents[3]
ONNX_MODEL_PATH = (
    REPO_ROOT / ".venv" / "lib" / "python3.13" / "site-packages"
    / "pipecat" / "audio" / "vad" / "data" / "silero_vad.onnx"
)
NEXA_INTERRUPTION_PATH = REPO_ROOT / "src" / "nexa" / "voice" / "interruption.py"

SPEECH_WAV_PATH = (
    Path(__file__).resolve().parent / "r0082d_speech_stimulus" / "r0082d_speech_en_pl_v1.wav"
)
EXPECTED_SPEECH_SHA256 = "703db210bcef8222adf044e88594958289be8d0e6ff4798ad8245b0c1076c21d"

OUT_DIR = Path(__file__).resolve().parent / "r0082f_live_captures"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_token(*, api_key: str, api_secret: str, identity: str, room: str) -> str:
    grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(grants)
        .to_jwt()
    )


def _verify_default_device(devices: list, expected_substring: str, *, role: str) -> None:
    """Unchanged from R0082-C/D/E -- see those scripts' own docstrings."""
    default_entries = [d for d in devices if d.name.startswith("default:")]
    if not default_entries:
        raise SystemExit(
            f"No {role} device reported as PipeWire's own default. "
            f"Enumerated: {[(d.index, d.name) for d in devices]}"
        )
    default_name = default_entries[0].name
    if expected_substring.lower() not in default_name.lower():
        raise SystemExit(
            f"PipeWire's default {role} device is {default_name!r}, which does "
            f"NOT contain {expected_substring!r}. Cannot force-select a "
            "specific device on this platform -- change the system default via "
            "`pactl set-default-source`/`set-default-sink` first, then retry."
        )
    print(f"  {role} default OK: {default_name!r}")


def read_speech_wav() -> bytes:
    if not SPEECH_WAV_PATH.exists():
        raise SystemExit(f"Frozen speech WAV not found: {SPEECH_WAV_PATH}")
    digest = sha256_of(SPEECH_WAV_PATH)
    if digest != EXPECTED_SPEECH_SHA256:
        raise SystemExit(
            f"Frozen speech WAV sha256 MISMATCH: expected {EXPECTED_SPEECH_SHA256}, "
            f"got {digest}. Refusing to proceed -- do not regenerate this file."
        )
    with wave.open(str(SPEECH_WAV_PATH), "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == 48000
        pcm = wf.readframes(wf.getnframes())
    print(f"Speech WAV verified: {SPEECH_WAV_PATH.name} sha256={digest} OK")
    return pcm


# ----------------------------------------------------------------------
# Streaming 48kHz -> 16kHz resampler -- UNCHANGED from last round,
# validated bit-for-bit against a whole-array reference under both
# regular and irregular chunking, plus an impulse test and a frequency/
# alias sweep (see R0082's own report §87).
# ----------------------------------------------------------------------
class StreamingResampler:
    """Stateful 48kHz->16kHz FIR-lowpass + exact 3:1 decimation.

    `scipy.signal.lfilter`'s own `zi`/`zf` state is carried across every
    `push()` call, and a running total-samples-seen counter fixes the
    decimation phase across arbitrary chunk-length boundaries -- NOT
    independent per-chunk resampling."""

    def __init__(self, in_rate: int = LIVE_SAMPLE_RATE, out_rate: int = VAD_SAMPLE_RATE):
        from scipy.signal import firwin, lfilter_zi

        assert in_rate % out_rate == 0
        self.decim = in_rate // out_rate
        nyquist_out = out_rate / 2.0
        cutoff = nyquist_out * 0.9  # margin below the output Nyquist
        self.b = firwin(63, cutoff, fs=in_rate)
        self.zi = lfilter_zi(self.b, [1.0]) * 0.0  # starts from silence (pre-roll is silence)
        self._total_in_samples = 0

    def push(self, chunk_int16: np.ndarray) -> np.ndarray:
        from scipy.signal import lfilter

        x = chunk_int16.astype(np.float64)
        y, self.zi = lfilter(self.b, [1.0], x, zi=self.zi)
        start_offset = (-self._total_in_samples) % self.decim
        out = y[start_offset :: self.decim]
        self._total_in_samples += len(x)
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)


# ----------------------------------------------------------------------
# Level 1 -- verbatim reproduction of pipecat.audio.vad.silero.SileroOnnxModel
# (unchanged from last round)
# ----------------------------------------------------------------------
class SileroOnnxModel:
    def __init__(self, path: str) -> None:
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            path, providers=["CPUExecutionProvider"], sess_options=opts
        )
        self.reset_states()

    def reset_states(self, batch_size: int = 1) -> None:
        self._state = np.zeros((2, batch_size, 128), dtype="float32")
        self._context = np.zeros((batch_size, 0), dtype="float32")
        self._last_sr = 0
        self._last_batch_size = 0

    def __call__(self, x: np.ndarray, sr: int) -> np.ndarray:
        if np.ndim(x) == 1:
            x = np.expand_dims(x, 0)
        num_samples = 512 if sr == 16000 else 256
        if np.shape(x)[-1] != num_samples:
            raise ValueError(f"expected {num_samples} samples for sr={sr}, got {np.shape(x)[-1]}")
        batch_size = np.shape(x)[0]
        context_size = 64 if sr == 16000 else 32
        if not self._last_batch_size:
            self.reset_states(batch_size)
        if self._last_sr and self._last_sr != sr:
            self.reset_states(batch_size)
        if self._last_batch_size and self._last_batch_size != batch_size:
            self.reset_states(batch_size)
        if not np.shape(self._context)[1]:
            self._context = np.zeros((batch_size, context_size), dtype="float32")
        x = np.concatenate((self._context, x), axis=1)
        ort_inputs = {"input": x, "state": self._state, "sr": np.array(sr, dtype="int64")}
        ort_outs = self.session.run(None, ort_inputs)
        out = ort_outs[0]
        self._state = ort_outs[1]
        self._context = x[..., -context_size:]
        self._last_sr = sr
        self._last_batch_size = batch_size
        return out


def exp_smoothing(value: float, prev_value: float, factor: float) -> float:
    return prev_value + factor * (value - prev_value)


def normalize_value(value: float, min_value: float, max_value: float) -> float:
    normalized = (value - min_value) / (max_value - min_value)
    return max(0.0, min(1.0, normalized))


class VolumeTracker:
    """Verbatim reproduction of pipecat.audio.volume.AudioVolumeTracker,
    using the REAL `loudness` package for ITU-R BS.1770 loudness."""

    def __init__(self, sample_rate: int) -> None:
        import math

        self.sample_rate = sample_rate
        self.window_num_bytes = math.ceil(VOLUME_WINDOW_SECS * sample_rate) * 2
        self.buffer = b""
        self.volume_cached: float | None = 0.0

    def update(self, audio: bytes) -> None:
        self.buffer = (self.buffer + audio)[-self.window_num_bytes :]
        if len(self.buffer) == self.window_num_bytes:
            self.volume_cached = None

    @property
    def volume(self) -> float:
        import loudness

        if self.volume_cached is None:
            audio_np = np.frombuffer(self.buffer, dtype=np.int16)
            audio_float = audio_np.astype(np.float32) / 32768.0
            level = loudness.integrated_loudness(audio_float, self.sample_rate)
            self.volume_cached = normalize_value(level, -110, -10)
        return self.volume_cached


def load_interruption_state_machine_class():
    """Loads the REAL src/nexa/voice/interruption.py directly, bypassing
    nexa/voice/__init__.py's own pipecat/loguru import chain entirely.
    Verified isolation, unchanged from R0082-E/F round 1."""
    spec = importlib.util.spec_from_file_location(
        "nexa_interruption_standalone_f", str(NEXA_INTERRUPTION_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nexa_interruption_standalone_f"] = mod
    spec.loader.exec_module(mod)
    strict_forbidden = ("nexa.voice", "pipecat", "loguru")
    loaded = sorted(
        n
        for n in sys.modules
        if any(n == p or n.startswith(p + ".") for p in strict_forbidden)
        and n != "nexa_interruption_standalone_f"
    )
    if loaded:
        raise SystemExit(
            f"Loading {NEXA_INTERRUPTION_PATH} unexpectedly pulled in: {loaded} "
            "-- isolation broken, refusing to proceed."
        )
    return mod


class LiveVadChain:
    """The live Level1->Level2->Level3 pipeline, fed one VAD_FRAME_SAMPLES
    (512-sample, 16kHz) frame at a time. Unchanged from R0082-F round 1
    (validated in R0082-F's own report §87 -- positive/negative controls
    both PASS with the resampler this class is paired with)."""

    def __init__(self, csv_writer, response_dispatched: bool = True) -> None:
        self.model = SileroOnnxModel(str(ONNX_MODEL_PATH))
        self.volume_tracker = VolumeTracker(VAD_SAMPLE_RATE)
        self.prev_volume = 0.0
        interruption_mod = load_interruption_state_machine_class()
        self.sm = interruption_mod.InterruptionStateMachine()
        if response_dispatched:
            self.sm.notify_response_dispatched()
        self.vad_state = "QUIET"
        self.starting_count = 0
        self.stopping_count = 0
        self.last_reset_audio_time = 0.0
        self.n_frames = 0
        self.started_events: list[float] = []
        self.stopped_events: list[float] = []
        self.confirmed_events: list[float] = []
        # R0082-G additions -- wall-clock companions to the audio-relative
        # lists above, and candidate-start (STARTING-entry) timestamps, all
        # purely additive bookkeeping used only by deliberate-bargein mode's
        # latency reporting. Silent-user mode does not read these.
        self.started_events_mono: list[float] = []
        self.confirmed_events_mono: list[float] = []
        self.candidate_start_events: list[float] = []
        self.candidate_start_events_mono: list[float] = []
        self.csv_writer = csv_writer
        self.max_prob = 0.0

    def process_frame(
        self, frame_int16: np.ndarray, t_s: float, *, extra_fields: list | None = None
    ) -> None:
        assert len(frame_int16) == VAD_FRAME_SAMPLES
        audio_float32 = frame_int16.astype(np.float32) / 32768.0
        prob = float(self.model(audio_float32, VAD_SAMPLE_RATE)[0][0])
        self.max_prob = max(self.max_prob, prob)

        if t_s - self.last_reset_audio_time >= MODEL_RESET_INTERVAL_S:
            self.model.reset_states()
            self.last_reset_audio_time = t_s

        frame_bytes = frame_int16.astype("<i2").tobytes()
        self.volume_tracker.update(frame_bytes)
        raw_volume = self.volume_tracker.volume
        volume = exp_smoothing(raw_volume, self.prev_volume, VOLUME_SMOOTHING_FACTOR)
        self.prev_volume = volume

        speaking = prob >= VAD_CONFIDENCE and volume >= VAD_MIN_VOLUME
        speech_start_event = False
        speech_end_event = False

        if speaking:
            if self.vad_state == "QUIET":
                self.vad_state = "STARTING"
                self.starting_count = 1
                self.candidate_start_events.append(t_s)
                self.candidate_start_events_mono.append(time.monotonic())
            elif self.vad_state == "STARTING":
                self.starting_count += 1
            elif self.vad_state == "STOPPING":
                self.vad_state = "SPEAKING"
                self.stopping_count = 0
        else:
            if self.vad_state == "STARTING":
                self.vad_state = "QUIET"
                self.starting_count = 0
            elif self.vad_state == "SPEAKING":
                self.vad_state = "STOPPING"
                self.stopping_count = 1
            elif self.vad_state == "STOPPING":
                self.stopping_count += 1

        if self.vad_state == "STARTING" and self.starting_count >= VAD_START_FRAMES:
            self.vad_state = "SPEAKING"
            self.starting_count = 0
            speech_start_event = True
            self.started_events.append(t_s)
            self.started_events_mono.append(time.monotonic())
            self.sm.speech_started(t_s)

        if self.vad_state == "STOPPING" and self.stopping_count >= VAD_STOP_FRAMES:
            self.vad_state = "QUIET"
            self.stopping_count = 0
            speech_end_event = True
            self.stopped_events.append(t_s)
            self.sm.speech_stopped(t_s)

        ev = self.sm.poll(t_s)
        confirmed = ev.value == "interrupt_confirmed"
        if confirmed:
            self.confirmed_events.append(t_s)
            self.confirmed_events_mono.append(time.monotonic())

        self.n_frames += 1
        if self.csv_writer is not None:
            row = [
                f"{time.monotonic():.6f}",
                f"{t_s:.3f}",
                f"{prob:.5f}",
                VAD_CONFIDENCE,
                f"{volume:.5f}",
                VAD_MIN_VOLUME,
                self.vad_state,
                int(self.vad_state == "STARTING"),
                int(speech_start_event),
                int(speech_end_event),
                self.sm.state.value,
                int(confirmed),
            ]
            if extra_fields is not None:
                row.extend(extra_fields)
            self.csv_writer.writerow(row)


CSV_HEADER = [
    "timestamp_monotonic",
    "audio_relative_timestamp_s",
    "silero_prob",
    "confidence_threshold",
    "smoothed_volume",
    "volume_threshold",
    "vad_state",
    "candidate_start",
    "vad_user_started_speaking_equivalent",
    "vad_user_stopped_speaking_equivalent",
    "interruption_state",
    "interrupt_confirmed",
]


async def run_hardware_role(
    *,
    aec: bool,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> None:
    """RETIRED self-read design removed. This role now does exactly what
    R0082-D's own `run_hardware_role` did: own `PlatformAudio` (real mic
    capture, real speaker playout, WebRTC AEC), publish its mic track,
    subscribe to the speech track (for automatic playout), hold for the
    fixed measurement lifetime, clean up. It does NOT self-read its own
    track and does NOT run any VAD logic -- that has moved to
    `run_speech_role`, which subscribes to THIS role's track remotely
    (the proven R0082-D pattern)."""
    print(f"[hardware pid={os.getpid()}] starting")
    platform_audio = rtc.PlatformAudio()
    try:
        recs = platform_audio.recording_devices()
        plays = platform_audio.playout_devices()
        print("[hardware] recording_devices():")
        for d in recs:
            print(f"    index={d.index} name={d.name!r}")
        print("[hardware] playout_devices():")
        for d in plays:
            print(f"    index={d.index} name={d.name!r}")
        _verify_default_device(recs, EXPECTED_INPUT_NAME_SUBSTRING, role="input")
        _verify_default_device(plays, EXPECTED_OUTPUT_NAME_SUBSTRING, role="output")

        options = rtc.PlatformAudioOptions(
            echo_cancellation=aec, noise_suppression=False, auto_gain_control=False
        )
        source = platform_audio.create_audio_source(options)
        track = rtc.LocalAudioTrack.create_audio_track("r0082f_hardware_mic", source)

        token = _make_token(
            api_key=api_key, api_secret=api_secret, identity=HARDWARE_IDENTITY, room=room_name
        )
        room = rtc.Room()
        speech_track_ready = asyncio.Event()

        def _on_track_subscribed(track_, publication, participant) -> None:
            if (
                participant.identity == SPEECH_IDENTITY
                and track_.kind == rtc.TrackKind.KIND_AUDIO
            ):
                speech_track_ready.set()

        room.on("track_subscribed", _on_track_subscribed)

        try:
            print(f"[hardware pid={os.getpid()}] connecting to room {room_name!r}...")
            await room.connect(url, token)
            print("[hardware] MILESTONE: hardware connected")
            await room.local_participant.publish_track(track)
            print("[hardware] MILESTONE: mic published")

            print("[hardware] waiting for speech participant's track subscription...")
            await asyncio.wait_for(speech_track_ready.wait(), timeout=SUBSCRIBE_TIMEOUT_S)
            print("[hardware] MILESTONE: speech track subscribed")

            total_hold_s = (
                SETTLE_S
                + FIRST_FRAME_TIMEOUT_S  # margin for the speech role's own first-frame gate
                + PRE_ROLL_S
                + SPEECH_DURATION_S
                + TAIL_S
                + CLEANUP_GRACE_S
            )
            print(f"[hardware] MILESTONE: holding for {total_hold_s:.1f}s")
            await asyncio.sleep(total_hold_s)
            print("[hardware] MILESTONE: hold complete")
        finally:
            source.close()
            await room.disconnect()
            print(f"[hardware pid={os.getpid()}] disconnected cleanly")
    finally:
        platform_audio.close()
        print(f"[hardware pid={os.getpid()}] platform_audio closed, exiting")


async def wait_for_first_frame(
    stream, *, timeout_s: float = FIRST_FRAME_TIMEOUT_S
) -> rtc.AudioFrame:
    """Mandatory first-frame gate: returns the FIRST real frame from
    `stream` (an async iterator of `AudioFrameEvent`), or raises
    `asyncio.TimeoutError` after `timeout_s`. Separated into its own
    function specifically so it can be unit-tested against a fake
    stream that never yields anything (the mandatory fail-closed
    negative test, see R0082's own report)."""
    aiter = stream.__aiter__()
    event = await asyncio.wait_for(aiter.__anext__(), timeout=timeout_s)
    return event.frame


async def run_speech_role(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> None:
    """Publishes the frozen speech stimulus AND (new this round) is the
    canonical VAD observation point: subscribes to the hardware role's
    REMOTE mic track (the proven R0082-D pattern), gates on a real first
    frame before starting PRE_ROLL, then runs playback and live VAD
    consumption CONCURRENTLY via asyncio tasks -- not sequentially."""
    print(f"[speech pid={os.getpid()}] starting")
    signal_pcm = read_speech_wav()
    token = _make_token(
        api_key=api_key, api_secret=api_secret, identity=SPEECH_IDENTITY, room=room_name
    )
    room = rtc.Room()
    hw_track_ready = asyncio.Event()
    remote_mic_track: list[rtc.Track] = []

    def _on_track_subscribed(track_, publication, participant) -> None:
        if participant.identity == HARDWARE_IDENTITY and track_.kind == rtc.TrackKind.KIND_AUDIO:
            remote_mic_track.append(track_)
            hw_track_ready.set()

    room.on("track_subscribed", _on_track_subscribed)

    signal_source = rtc.AudioSource(LIVE_SAMPLE_RATE, 1)
    signal_track = rtc.LocalAudioTrack.create_audio_track("r0082f_speech_signal", signal_source)

    try:
        print(f"[speech pid={os.getpid()}] connecting to room {room_name!r}...")
        await room.connect(url, token)
        await room.local_participant.publish_track(signal_track)
        print("[speech] MILESTONE: speech track published")

        print("[speech] waiting for hardware mic track subscription...")
        await asyncio.wait_for(hw_track_ready.wait(), timeout=SUBSCRIBE_TIMEOUT_S)
        print("[speech] MILESTONE: hardware mic track subscribed (remote)")

        mic_stream = rtc.AudioStream(
            remote_mic_track[0], sample_rate=LIVE_SAMPLE_RATE, num_channels=1
        )

        print(
            f"[speech] waiting up to {FIRST_FRAME_TIMEOUT_S}s for the FIRST real "
            "remote mic frame (mandatory gate -- will NOT proceed without it)..."
        )
        try:
            first_frame = await wait_for_first_frame(mic_stream)
        except TimeoutError:
            await mic_stream.aclose()
            raise SystemExit(
                "TEST INVALID -- no real remote mic frame arrived within "
                f"{FIRST_FRAME_TIMEOUT_S}s. Refusing to play the speech stimulus. "
                "STOP -- diagnose the remote subscription path before retrying."
            ) from None

        print(
            f"[speech] MILESTONE: VAD INPUT READY -- first real remote mic frame: "
            f"sample_rate={first_frame.sample_rate} num_channels={first_frame.num_channels} "
            f"samples_per_channel={first_frame.samples_per_channel}"
        )
        if first_frame.num_channels != 1:
            raise SystemExit(
                f"Remote mic frame has num_channels={first_frame.num_channels}, expected 1 "
                "(mono). Refusing to silently drop/mix a channel -- this needs an explicit, "
                "documented conversion decision before proceeding."
            )
        if first_frame.sample_rate != LIVE_SAMPLE_RATE:
            raise SystemExit(
                f"Remote mic frame sample_rate={first_frame.sample_rate}, expected "
                f"{LIVE_SAMPLE_RATE}. Refusing to proceed with an unverified rate assumption."
            )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        csv_path = OUT_DIR / f"r0082f_aecon_{run_id}_timeline.csv"
        csv_file = open(csv_path, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(CSV_HEADER)

        chain = LiveVadChain(csv_writer)
        resampler = StreamingResampler()
        vad_buffer = np.zeros(0, dtype=np.int16)
        total_16k_samples = 0
        raw_48k_chunks: list[bytes] = []
        resampled_16k_chunks: list[bytes] = []
        remote_frames_received = 0
        remote_samples_received = 0

        # the first frame was already consumed by wait_for_first_frame() --
        # feed it through the SAME pipeline before continuing the loop, so
        # no real audio is silently dropped.
        raw_bytes0 = bytes(first_frame.data)
        raw_48k_chunks.append(raw_bytes0)
        remote_frames_received += 1
        remote_samples_received += first_frame.samples_per_channel
        chunk0 = np.frombuffer(raw_bytes0, dtype="<i2")
        resampled0 = resampler.push(chunk0)
        resampled_16k_chunks.append(resampled0.astype("<i2").tobytes())
        vad_buffer = np.concatenate([vad_buffer, resampled0])
        while len(vad_buffer) >= VAD_FRAME_SAMPLES:
            vf = vad_buffer[:VAD_FRAME_SAMPLES]
            vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
            chain.process_frame(vf, total_16k_samples / VAD_SAMPLE_RATE)
            total_16k_samples += VAD_FRAME_SAMPLES

        capture_stop = asyncio.Event()

        async def _consume_mic() -> None:
            nonlocal vad_buffer, total_16k_samples, remote_frames_received
            nonlocal remote_samples_received
            try:
                async for event in mic_stream:
                    if capture_stop.is_set():
                        break
                    frame = event.frame
                    raw_bytes = bytes(frame.data)
                    raw_48k_chunks.append(raw_bytes)
                    remote_frames_received += 1
                    remote_samples_received += frame.samples_per_channel
                    chunk = np.frombuffer(raw_bytes, dtype="<i2")
                    resampled = resampler.push(chunk)
                    resampled_16k_chunks.append(resampled.astype("<i2").tobytes())
                    vad_buffer = np.concatenate([vad_buffer, resampled])
                    while len(vad_buffer) >= VAD_FRAME_SAMPLES:
                        vf = vad_buffer[:VAD_FRAME_SAMPLES]
                        vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
                        t_s = total_16k_samples / VAD_SAMPLE_RATE
                        chain.process_frame(vf, t_s)
                        total_16k_samples += VAD_FRAME_SAMPLES
            except asyncio.CancelledError:
                return

        consume_task = asyncio.create_task(_consume_mic())

        print(f"[speech] MILESTONE: PRE_ROLL start ({PRE_ROLL_S}s)")
        await asyncio.sleep(PRE_ROLL_S)

        print("[speech] MILESTONE: PLAYBACK start")
        total_samples = len(signal_pcm) // 2
        offset = 0
        while offset < total_samples:
            chunk_samples = min(FRAME_SAMPLES_LIVE, total_samples - offset)
            frame = rtc.AudioFrame.create(LIVE_SAMPLE_RATE, 1, FRAME_SAMPLES_LIVE)
            buf = array.array("h", frame.data)
            chunk = array.array("h")
            chunk.frombytes(signal_pcm[offset * 2 : (offset + chunk_samples) * 2])
            for i in range(FRAME_SAMPLES_LIVE):
                buf[i] = chunk[i] if i < chunk_samples else 0
            frame.data[:] = buf
            await signal_source.capture_frame(frame)
            offset += chunk_samples
        print("[speech] MILESTONE: PLAYBACK end")

        print(f"[speech] MILESTONE: TAIL start ({TAIL_S}s)")
        await asyncio.sleep(TAIL_S)
        print("[speech] MILESTONE: TAIL end")

        capture_stop.set()
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await mic_stream.aclose()
        csv_file.close()

        raw_wav_path = OUT_DIR / f"r0082f_aecon_{run_id}_mic_48k.wav"
        vad_wav_path = OUT_DIR / f"r0082f_aecon_{run_id}_mic_16k.wav"
        _write_wav(raw_wav_path, b"".join(raw_48k_chunks), sample_rate=LIVE_SAMPLE_RATE)
        _write_wav(vad_wav_path, b"".join(resampled_16k_chunks), sample_rate=VAD_SAMPLE_RATE)
        print(f"[speech] wrote {raw_wav_path.name} sha256={sha256_of(raw_wav_path)}")
        print(f"[speech] wrote {vad_wav_path.name} sha256={sha256_of(vad_wav_path)}")
        print(f"[speech] wrote {csv_path.name} ({chain.n_frames} VAD frames)")

        # -- mandatory fail-closed validity check -------------------- #
        capture_duration_s = remote_samples_received / LIVE_SAMPLE_RATE
        expected_min_duration_s = PRE_ROLL_S + SPEECH_DURATION_S + TAIL_S
        capture_complete = capture_duration_s >= expected_min_duration_s

        print("\n" + "=" * 70)
        print("VALIDITY SUMMARY")
        print("=" * 70)
        print(f"  remote_audio_frames_received  = {remote_frames_received}")
        print(f"  remote_audio_samples_received = {remote_samples_received}")
        print(f"  vad_frames_processed          = {chain.n_frames}")
        print(f"  capture_duration_s            = {capture_duration_s:.3f}")
        print(f"  expected_min_duration_s       = {expected_min_duration_s:.3f}")
        print(f"  capture_complete              = {capture_complete}")

        if chain.n_frames == 0:
            print("\n*** INVALID TEST — NO VAD INPUT FRAMES ***")
            print("This run cannot be classified as PASS or FAIL for self-interruption.")
            return
        if not capture_complete:
            print(
                "\n*** INVALID TEST — CAPTURE DURATION SHORTER THAN EXPECTED "
                f"({capture_duration_s:.3f}s < {expected_min_duration_s:.3f}s) ***"
            )
            print("This run cannot be classified as PASS or FAIL for self-interruption.")
            return

        print(
            f"\n  RESULT: max_prob={chain.max_prob:.4f}  "
            f"started_events={len(chain.started_events)}  "
            f"confirmed_events={len(chain.confirmed_events)}"
        )
        for t in chain.started_events:
            print(f"    VADUserStartedSpeaking-equivalent at t={t:.3f}s")
        for t in chain.confirmed_events:
            print(
                f"    *** INTERRUPT_CONFIRMED at t={t:.3f}s -- "
                "PRODUCTION-RELEVANT FALSE POSITIVE ***"
            )
        if len(chain.started_events) == 0 and len(chain.confirmed_events) == 0:
            print("\n  VALID TEST -- PASS (0 false speech starts, 0 confirmed interruptions)")
        else:
            print("\n  VALID TEST -- FAIL (see events above)")
    finally:
        await signal_source.aclose()
        await room.disconnect()
        print(f"[speech pid={os.getpid()}] disconnected cleanly")


def evaluate_deliberate_bargein_result(
    *,
    started_events: list[float],
    confirmed_events: list[float],
    vad_frames_processed: int,
    capture_duration_s: float,
    cue_emitted: bool,
    speak_now_boundary_t_s: float,
    cue_start_boundary_t_s: float,
    playback_stopped_early: bool,
    playback_resumed_after_stop: bool,
) -> dict:
    """R0082-G classification -- pure function, unit-testable without any
    hardware/asyncio/LiveKit involvement. Returns a dict with:
    `pre_cue_false_positive` (bool) -- a false accepted speech start or a
    confirmed interruption BEFORE the operator's own cue is the original
    self-echo failure mode and is now WIRED INTO the verdict as `FAIL-E`
    (previously computed but not consulted -- fixed per correction: a
    run could report `pre_cue_false_positive=True` alongside
    `VERDICT=PASS` if the later genuine post-cue barge-in also
    succeeded; that is no longer possible -- `PASS` requires
    `pre_cue_false_positive is False`). `verdict` is one of "PASS",
    "FAIL-A".."FAIL-F". The supporting event lists split pre/post cue
    are still returned in full."""
    pre_cue_started = [t for t in started_events if t < cue_start_boundary_t_s]
    pre_cue_confirmed = [t for t in confirmed_events if t < cue_start_boundary_t_s]
    post_cue_started = [t for t in started_events if t >= cue_start_boundary_t_s]
    post_cue_confirmed = [t for t in confirmed_events if t >= cue_start_boundary_t_s]
    pre_cue_false_positive = bool(pre_cue_started or pre_cue_confirmed)

    if post_cue_confirmed:
        min_required_s = max(
            speak_now_boundary_t_s, post_cue_confirmed[0] + POST_INTERRUPT_MARGIN_S
        )
    else:
        min_required_s = MIN_DELIBERATE_CAPTURE_S
    capture_ok = capture_duration_s >= min_required_s

    if vad_frames_processed == 0 or not cue_emitted or not capture_ok:
        verdict = "FAIL-F"
    elif pre_cue_false_positive:
        verdict = "FAIL-E"
    elif not post_cue_started:
        verdict = "FAIL-A"
    elif not post_cue_confirmed:
        verdict = "FAIL-B"
    elif not playback_stopped_early:
        verdict = "FAIL-C"
    elif playback_resumed_after_stop:
        verdict = "FAIL-D"
    else:
        verdict = "PASS"

    return {
        "pre_cue_false_positive": pre_cue_false_positive,
        "pre_cue_started": pre_cue_started,
        "pre_cue_confirmed": pre_cue_confirmed,
        "post_cue_started": post_cue_started,
        "post_cue_confirmed": post_cue_confirmed,
        "capture_ok": capture_ok,
        "min_required_capture_s": min_required_s,
        "verdict": verdict,
    }


async def _play_signal_cancelable(
    signal_source,
    signal_pcm: bytes,
    *,
    cancel_event: asyncio.Event,
    milestones: dict,
    cue_start_time_s: float = BARGEIN_CUE_START_TIME_S,
    speak_now_time_s: float = BARGEIN_SPEAK_NOW_TIME_S,
    emit_cue: bool = True,
) -> tuple[int, bool]:
    """Submits `signal_pcm` frame-by-frame, exactly like the silent-user
    loop, but (a) concurrently delivers the terminal countdown cue on a
    real-time `asyncio.sleep` schedule tied to PLAYBACK start, and (b)
    checks `cancel_event` once per 10ms frame and stops submitting
    IMMEDIATELY (no further `capture_frame` calls) the moment it is set.
    There is no code path that resumes submission afterward -- once this
    function returns, it is not re-entered for the same stream. Returns
    `(samples_submitted, stopped_early)`. Pure enough to unit-test with a
    fake `signal_source` stub and a pre-set `cancel_event`."""
    total_samples = len(signal_pcm) // 2
    milestones["playback_start"] = time.monotonic()

    async def _deliver_cue() -> None:
        if not emit_cue:
            return
        await asyncio.sleep(cue_start_time_s)
        milestones["cue_start"] = time.monotonic()
        print("\n  BARGE-IN IN 3")
        await asyncio.sleep(1.0)
        print("  BARGE-IN IN 2")
        await asyncio.sleep(1.0)
        print("  BARGE-IN IN 1")
        await asyncio.sleep(1.0)
        milestones["speak_now"] = time.monotonic()
        print(f"  >>> SPEAK NOW <<<   ({OPERATOR_PHRASE_PL})\n")

    cue_task = asyncio.create_task(_deliver_cue())
    offset = 0
    stopped_early = False
    try:
        while offset < total_samples:
            if cancel_event.is_set():
                stopped_early = True
                break
            chunk_samples = min(FRAME_SAMPLES_LIVE, total_samples - offset)
            frame = rtc.AudioFrame.create(LIVE_SAMPLE_RATE, 1, FRAME_SAMPLES_LIVE)
            buf = array.array("h", frame.data)
            chunk = array.array("h")
            chunk.frombytes(signal_pcm[offset * 2 : (offset + chunk_samples) * 2])
            for i in range(FRAME_SAMPLES_LIVE):
                buf[i] = chunk[i] if i < chunk_samples else 0
            frame.data[:] = buf
            await signal_source.capture_frame(frame)
            milestones["last_speech_frame_submitted"] = time.monotonic()
            offset += chunk_samples
    finally:
        if not cue_task.done():
            cue_task.cancel()
            try:
                await cue_task
            except asyncio.CancelledError:
                pass
    if stopped_early:
        milestones["playback_stopped"] = time.monotonic()
    return offset, stopped_early


async def run_speech_role_deliberate_bargein(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> None:
    """R0082-G. Identical connection/subscription/first-frame-gate
    preamble to `run_speech_role` (silent-user), then diverges: playback
    goes through `_play_signal_cancelable` (real-time countdown cue +
    genuine, non-resuming cancellation on INTERRUPT_CONFIRMED), and the
    CSV/validity/classification logic is R0082-G's own."""
    print(f"[speech pid={os.getpid()}] starting (test-mode=deliberate-bargein)")
    signal_pcm = read_speech_wav()
    token = _make_token(
        api_key=api_key, api_secret=api_secret, identity=SPEECH_IDENTITY, room=room_name
    )
    room = rtc.Room()
    hw_track_ready = asyncio.Event()
    remote_mic_track: list[rtc.Track] = []

    def _on_track_subscribed(track_, publication, participant) -> None:
        if participant.identity == HARDWARE_IDENTITY and track_.kind == rtc.TrackKind.KIND_AUDIO:
            remote_mic_track.append(track_)
            hw_track_ready.set()

    room.on("track_subscribed", _on_track_subscribed)

    signal_source = rtc.AudioSource(LIVE_SAMPLE_RATE, 1)
    signal_track = rtc.LocalAudioTrack.create_audio_track("r0082f_speech_signal", signal_source)

    try:
        print(f"[speech pid={os.getpid()}] connecting to room {room_name!r}...")
        await room.connect(url, token)
        await room.local_participant.publish_track(signal_track)
        print("[speech] MILESTONE: speech track published")

        print("[speech] waiting for hardware mic track subscription...")
        await asyncio.wait_for(hw_track_ready.wait(), timeout=SUBSCRIBE_TIMEOUT_S)
        print("[speech] MILESTONE: hardware mic track subscribed (remote)")

        mic_stream = rtc.AudioStream(
            remote_mic_track[0], sample_rate=LIVE_SAMPLE_RATE, num_channels=1
        )

        print(
            f"[speech] waiting up to {FIRST_FRAME_TIMEOUT_S}s for the FIRST real "
            "remote mic frame (mandatory gate -- will NOT proceed without it)..."
        )
        try:
            first_frame = await wait_for_first_frame(mic_stream)
        except TimeoutError:
            await mic_stream.aclose()
            raise SystemExit(
                "TEST INVALID -- no real remote mic frame arrived within "
                f"{FIRST_FRAME_TIMEOUT_S}s. Refusing to play the speech stimulus. "
                "STOP -- diagnose the remote subscription path before retrying."
            ) from None

        print(
            f"[speech] MILESTONE: VAD INPUT READY -- first real remote mic frame: "
            f"sample_rate={first_frame.sample_rate} num_channels={first_frame.num_channels} "
            f"samples_per_channel={first_frame.samples_per_channel}"
        )
        if first_frame.num_channels != 1 or first_frame.sample_rate != LIVE_SAMPLE_RATE:
            raise SystemExit(
                f"Remote mic frame mismatch: num_channels={first_frame.num_channels}, "
                f"sample_rate={first_frame.sample_rate}. Refusing to proceed."
            )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        csv_path = OUT_DIR / f"r0082g_bargein_{run_id}_timeline.csv"
        csv_file = open(csv_path, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(CSV_HEADER_BARGEIN)

        chain = LiveVadChain(csv_writer)
        resampler = StreamingResampler()
        vad_buffer = np.zeros(0, dtype=np.int16)
        total_16k_samples = 0
        raw_48k_chunks: list[bytes] = []
        resampled_16k_chunks: list[bytes] = []
        remote_frames_received = 0
        remote_samples_received = 0

        playback_active = False
        playback_cancel_requested = False
        interrupt_confirmed_event = asyncio.Event()
        milestones: dict[str, float] = {}

        def _extra_fields() -> list:
            return [int(playback_active), int(playback_cancel_requested)]

        raw_bytes0 = bytes(first_frame.data)
        raw_48k_chunks.append(raw_bytes0)
        remote_frames_received += 1
        remote_samples_received += first_frame.samples_per_channel
        chunk0 = np.frombuffer(raw_bytes0, dtype="<i2")
        resampled0 = resampler.push(chunk0)
        resampled_16k_chunks.append(resampled0.astype("<i2").tobytes())
        vad_buffer = np.concatenate([vad_buffer, resampled0])
        while len(vad_buffer) >= VAD_FRAME_SAMPLES:
            vf = vad_buffer[:VAD_FRAME_SAMPLES]
            vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
            chain.process_frame(
                vf, total_16k_samples / VAD_SAMPLE_RATE, extra_fields=_extra_fields()
            )
            total_16k_samples += VAD_FRAME_SAMPLES

        capture_stop = asyncio.Event()
        prev_confirmed_count = len(chain.confirmed_events)

        async def _consume_mic() -> None:
            nonlocal vad_buffer, total_16k_samples, remote_frames_received
            nonlocal remote_samples_received, playback_cancel_requested, prev_confirmed_count
            try:
                async for event in mic_stream:
                    if capture_stop.is_set():
                        break
                    frame = event.frame
                    raw_bytes = bytes(frame.data)
                    raw_48k_chunks.append(raw_bytes)
                    remote_frames_received += 1
                    remote_samples_received += frame.samples_per_channel
                    chunk = np.frombuffer(raw_bytes, dtype="<i2")
                    resampled = resampler.push(chunk)
                    resampled_16k_chunks.append(resampled.astype("<i2").tobytes())
                    vad_buffer = np.concatenate([vad_buffer, resampled])
                    while len(vad_buffer) >= VAD_FRAME_SAMPLES:
                        vf = vad_buffer[:VAD_FRAME_SAMPLES]
                        vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
                        t_s = total_16k_samples / VAD_SAMPLE_RATE
                        chain.process_frame(vf, t_s, extra_fields=_extra_fields())
                        total_16k_samples += VAD_FRAME_SAMPLES
                        if (
                            len(chain.confirmed_events) > prev_confirmed_count
                            and not playback_cancel_requested
                        ):
                            playback_cancel_requested = True
                            prev_confirmed_count = len(chain.confirmed_events)
                            milestones["playback_cancel_requested"] = time.monotonic()
                            print(
                                "[speech] MILESTONE: PLAYBACK_CANCEL_REQUESTED "
                                f"(INTERRUPT_CONFIRMED at t={chain.confirmed_events[-1]:.3f}s)"
                            )
                            interrupt_confirmed_event.set()
            except asyncio.CancelledError:
                return

        consume_task = asyncio.create_task(_consume_mic())

        print(f"[speech] MILESTONE: PRE_ROLL start ({PRE_ROLL_S}s)")
        await asyncio.sleep(PRE_ROLL_S)

        print("[speech] MILESTONE: PLAYBACK start")
        playback_active = True
        samples_submitted, stopped_early = await _play_signal_cancelable(
            signal_source,
            signal_pcm,
            cancel_event=interrupt_confirmed_event,
            milestones=milestones,
        )
        playback_active = False
        if stopped_early:
            print(
                f"[speech] MILESTONE: PLAYBACK_STOPPED (cancelled -- submitted "
                f"{samples_submitted}/{len(signal_pcm) // 2} samples)"
            )
        else:
            print("[speech] MILESTONE: PLAYBACK end (completed naturally, not cancelled)")

        print(f"[speech] MILESTONE: TAIL start ({TAIL_S}s)")
        await asyncio.sleep(TAIL_S)
        print("[speech] MILESTONE: TAIL end")

        capture_stop.set()
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await mic_stream.aclose()
        csv_file.close()

        raw_wav_path = OUT_DIR / f"r0082g_bargein_{run_id}_mic_48k.wav"
        vad_wav_path = OUT_DIR / f"r0082g_bargein_{run_id}_mic_16k.wav"
        _write_wav(raw_wav_path, b"".join(raw_48k_chunks), sample_rate=LIVE_SAMPLE_RATE)
        _write_wav(vad_wav_path, b"".join(resampled_16k_chunks), sample_rate=VAD_SAMPLE_RATE)
        print(f"[speech] wrote {raw_wav_path.name} sha256={sha256_of(raw_wav_path)}")
        print(f"[speech] wrote {vad_wav_path.name} sha256={sha256_of(vad_wav_path)}")
        print(f"[speech] wrote {csv_path.name} ({chain.n_frames} VAD frames)")

        cue_emitted = "speak_now" in milestones
        capture_duration_s = remote_samples_received / LIVE_SAMPLE_RATE
        speak_now_boundary_t_s = PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S
        cue_start_boundary_t_s = PRE_ROLL_S + BARGEIN_CUE_START_TIME_S
        playback_resumed_after_stop = False  # structurally impossible; see docstring

        result = evaluate_deliberate_bargein_result(
            started_events=chain.started_events,
            confirmed_events=chain.confirmed_events,
            vad_frames_processed=chain.n_frames,
            capture_duration_s=capture_duration_s,
            cue_emitted=cue_emitted,
            speak_now_boundary_t_s=speak_now_boundary_t_s,
            cue_start_boundary_t_s=cue_start_boundary_t_s,
            playback_stopped_early=stopped_early,
            playback_resumed_after_stop=playback_resumed_after_stop,
        )

        latencies = {}
        if "speak_now" in milestones and result["post_cue_started"]:
            idx = chain.started_events.index(result["post_cue_started"][0])
            latencies["cue_to_vad_start_s"] = (
                chain.started_events_mono[idx] - milestones["speak_now"]
            )
        if "speak_now" in milestones and result["post_cue_confirmed"]:
            idx = chain.confirmed_events.index(result["post_cue_confirmed"][0])
            latencies["cue_to_interrupt_confirmed_s"] = (
                chain.confirmed_events_mono[idx] - milestones["speak_now"]
            )
        if "playback_cancel_requested" in milestones and result["post_cue_confirmed"]:
            idx = chain.confirmed_events.index(result["post_cue_confirmed"][0])
            latencies["interrupt_confirmed_to_cancel_requested_s"] = (
                milestones["playback_cancel_requested"] - chain.confirmed_events_mono[idx]
            )

        import json

        milestones_path = OUT_DIR / f"r0082g_bargein_{run_id}_milestones.json"
        milestones_path.write_text(
            json.dumps({"milestones": milestones, "latencies_s": latencies}, indent=2)
        )
        print(f"[speech] wrote {milestones_path.name}")

        print("\n" + "=" * 70)
        print("R0082-G VALIDITY + CLASSIFICATION SUMMARY")
        print("=" * 70)
        print(f"  remote_audio_frames_received  = {remote_frames_received}")
        print(f"  remote_audio_samples_received = {remote_samples_received}")
        print(f"  vad_frames_processed          = {chain.n_frames}")
        print(f"  capture_duration_s            = {capture_duration_s:.3f}")
        print(f"  cue_emitted                   = {cue_emitted}")
        print(f"  min_required_capture_s        = {result['min_required_capture_s']:.3f}")
        print(f"  capture_ok                    = {result['capture_ok']}")
        print(f"  playback_stopped_early        = {stopped_early}")
        print(f"  pre_cue_false_positive        = {result['pre_cue_false_positive']}")
        if result["pre_cue_false_positive"]:
            print(
                "  *** PRE-CUE FALSE POSITIVE(S) -- self-echo trigger BEFORE the "
                f"cue: started={result['pre_cue_started']} confirmed={result['pre_cue_confirmed']} "
                "-- this is the original self-echo failure mode and forces "
                "VERDICT=FAIL-E below, regardless of the post-cue outcome ***"
            )
        print(f"  post_cue_started_events       = {result['post_cue_started']}")
        print(f"  post_cue_confirmed_events     = {result['post_cue_confirmed']}")
        for k, v in latencies.items():
            print(f"  {k:<40s} = {v:.3f}s")
        print(f"\n  VERDICT: {result['verdict']}")
        if result["verdict"] != "PASS":
            print("  This is NOT a PASS -- see FAIL taxonomy in this module's docstring.")
    finally:
        await signal_source.aclose()
        await room.disconnect()
        print(f"[speech pid={os.getpid()}] disconnected cleanly")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--role", choices=["hardware", "speech"], required=True)
    p.add_argument(
        "--aec", choices=["on", "off"], default="on",
        help="WebRTC AEC on/off for --role hardware (default: on -- unchanged, "
             "not re-swept this round). Ignored by --role speech.",
    )
    p.add_argument("--room-name", required=True)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--api-secret", default=DEFAULT_API_SECRET)
    p.add_argument(
        "--test-mode", choices=["silent-user", "deliberate-bargein"], default="silent-user",
        help="R0082-F silent-user (default, unchanged) or R0082-G deliberate-bargein "
             "(operator speaks once, on cue). Only affects --role speech; ignored by "
             "--role hardware, which behaves identically either way.",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    if args.role == "hardware":
        await run_hardware_role(
            aec=(args.aec == "on"), url=args.url, api_key=args.api_key,
            api_secret=args.api_secret, room_name=args.room_name,
        )
    elif args.test_mode == "deliberate-bargein":
        await run_speech_role_deliberate_bargein(
            url=args.url, api_key=args.api_key, api_secret=args.api_secret,
            room_name=args.room_name,
        )
    else:
        await run_speech_role(
            url=args.url, api_key=args.api_key, api_secret=args.api_secret,
            room_name=args.room_name,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
