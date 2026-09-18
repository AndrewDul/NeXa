#!/usr/bin/env python3
"""R0082-F — MINIMAL LIVE production-equivalent VAD self-echo test.

**No Gemini, no LLM, no full NeXa Core, no ConversationSession, no real
conversational loop, no `AecReferenceFeeder`, no XVF3800.** Answers ONE
question: while NeXa-like speech is actually playing live through the
real speaker and the human user stays completely silent, does the LIVE
`PlatformAudio` -> Silero VAD path generate any false
`UserStartedSpeaking`-equivalent events, in real time, with real
hardware?

R0082-E already answered this OFFLINE, on 4 already-recorded WAV
captures (0 false triggers in all four). This round tests the SAME
production-equivalent VAD chain LIVE — real-time frame delivery, real
concurrency, no batch shortcuts.

## Architecture — two genuinely separate OS processes (R0082-C's own fix)

```
PROCESS A (--role hardware)          PROCESS B (--role speech)
  rtc.PlatformAudio()                  rtc.AudioSource (synthetic)
  real reSpeaker mic capture           publishes the frozen speech WAV
  real UACDemoV1.0 playout                    |
  publishes its own mic track                 |
  subscribes to B's speech track  <----+ (triggers PlatformAudio's
  (triggers automatic playout,           automatic playout -- the AEC
   the AEC far-end reference)            far-end reference)
        |
        | self-reads its OWN mic track via rtc.AudioStream
        | (confirmed this round: works with NO room round-trip for a
        |  synthetic AudioSource-backed track; NOT yet empirically
        |  confirmed for a PlatformAudioSource-backed track -- see
        |  the module-level LIMITATIONS note below)
        v
  streaming 48kHz->16kHz resampler (stateful FIR + persistent decimation
  phase -- NOT per-chunk independent resampling)
        v
  Level 1: SileroOnnxModel (verbatim reproduction, same bundled ONNX
           model file as R0082-E, same sha256)
        v
  Level 2: VADAnalyzer state machine (verbatim reproduction, same
           production constants: confidence=0.7, start_secs=0.2,
           stop_secs=1.0, min_volume=0.6)
        v
  Level 3: REAL nexa.voice.interruption.InterruptionStateMachine
           (genuine production code, loaded via
           importlib.util.spec_from_file_location, bypassing
           nexa/voice/__init__.py's own pipecat/loguru import chain)
        v
  diagnostic CSV + milestone log + raw/16k WAV evidence -- NOTHING else.
  `BargeInController` is deliberately NOT used this round (see
  "BargeInController audit" below) -- only its architecture-neutral
  inner layer (`InterruptionStateMachine`) is exercised.
```

No Python `multiprocessing`, no `fork()`-based worker model, no shared
`Room`/`PlatformAudio` object — the same discipline R0082-C's own
`RaceDetected()` fix established.

## BargeInController audit (read-only this round, NOT modified, NOT used)

`src/nexa/voice/bargein.py` was read in full (no execution, no
modification). Exactly one piece of its behavior depends on
`AecReferenceHealth`:

```python
def _handle_speech_started(self) -> None:
    if not self._sm.response_in_flight:
        return
    if not self._aec.barge_in_safe:        # <- the ONLY gating dependency
        ...
        return
    ev = self._sm.speech_started(self._now())
    ...
```

Plus a mandatory `aec_health: AecReferenceHealth` constructor parameter
(no default — the class cannot be built without one) and two
telemetry-only fields (`aec_reference_active`/`aec_reference_failure_count`,
reporting, not gating). **Everything else is architecture-neutral**:
`InterruptionStateMachine` ownership, the confirm-hold scheduling
(`_schedule_confirm`/`_confirm_after_hold`), the M2.5B.1/.3
settle-phase/capture-id lifecycle (`_arm_settle`/`_settle_after`/
`_capture_deadline`), `notify_response_dispatched`/
`notify_response_finished`/`notify_interruption_complete`, and Pipecat
frame routing (`process_frame`) reference no XVF3800/hardware-specific
state at all — they operate purely on `VADUserStartedSpeakingFrame`/
`VADUserStoppedSpeakingFrame`/`UserSpeakingFrame` and would behave
identically regardless of which audio backend produced those frames.

**Consequence for this round**: `barge_in_safe` is a precondition
SPECIFIC to the CURRENT production XVF3800/`AecReferenceFeeder` hardware
AEC path (R0028) — there is no equivalent "far-end reference confirmed
active" signal for `PlatformAudio`'s WebRTC AEC (a fundamentally
different mechanism with no discrete health/heartbeat concept exposed to
Python). Using `BargeInController` here would require supplying a stub
`AecReferenceHealth`-like object with `barge_in_safe` hardcoded `True` —
which would be presenting a FAKE precondition as if it meant something
on this architecture, a genuine confound. Per instruction, **this round
does NOT insert `BargeInController`** — the canonical live detection
chain is PlatformAudio mic -> Silero -> VADAnalyzer state -> the REAL
`InterruptionStateMachine` directly -> diagnostic logging only.
`BargeInController` has **NOT** been validated on PlatformAudio and this
round makes no such claim.

## Real-time 48kHz -> 16kHz resampling — stateful, not per-frame independent

`rtc.AudioStream` on this SDK delivers audio at whatever `sample_rate`
is requested when constructing it (this script requests 48000, matching
every prior R0082 script's own confirmed convention) — R0082-A/B/C/D/E
all independently confirmed PlatformAudio/`MediaDevices` operate
internally at 48kHz, not the old NeXa-internal 16kHz. Production Silero
expects 16kHz. `StreamingResampler` (below) applies a single FIR
anti-aliasing lowpass via `scipy.signal.lfilter` with a PERSISTENT `zi`
(filter state) carried across every call, plus a persistent sample
counter that tracks the exact 3:1 decimation phase across arbitrary
chunk-length boundaries — NOT independent per-chunk resampling, which
would reset the filter's memory (and therefore distort the signal) at
every chunk boundary. Chunk size arriving from `rtc.AudioStream` is
whatever LiveKit delivers per `AudioFrameEvent` (observed and logged at
runtime, not assumed).

## Genuine production code reuse vs. research glue

- **Genuine production code, unmodified, loaded directly from source**:
  `nexa.voice.interruption.InterruptionStateMachine` (via
  `importlib.util.spec_from_file_location`, isolation verified — see
  `load_interruption_state_machine_class()`).
- **Verbatim algorithmic reproduction** (not imported, to avoid pulling
  `loguru`/`pipecat`'s own package-init side effects into a script that
  ALSO needs real-time LiveKit hardware access in the SAME process):
  `SileroOnnxModel` (same bundled ONNX file, same sha256, same
  algorithm as `pipecat.audio.vad.silero`), the `VADAnalyzer` 4-state
  hysteresis machine and its exact constants (`pipecat.audio.vad.
  vad_analyzer`), and the volume-gate math (`exp_smoothing`,
  `normalize_value`, `AudioVolumeTracker`'s rolling-window logic —
  reusing the REAL `loudness.integrated_loudness()` binding for the
  actual BS.1770 computation, not a hand-rolled reimplementation of
  that algorithm).
- **Research glue, new this round**: the split-process room
  orchestration (extends R0082-C/D's own proven pattern), the
  streaming resampler, and the CSV/milestone telemetry writer.

## LIMITATIONS -- stated explicitly, not hidden

- **Self-read on a `PlatformAudioSource`-backed `LocalAudioTrack` has
  NOT been empirically confirmed** — only confirmed this round for a
  synthetic `AudioSource`-backed track (no room, no hardware). If the
  real hardware run finds the self-read `AudioStream` never yields
  frames (or yields silence) on the real `PlatformAudio` mic track,
  the documented fallback is R0082-D's own pattern: have the SPEECH
  role subscribe to and read back the HARDWARE role's REMOTE track
  instead — not built this round, to keep this round's harness
  minimal, but the exact contingency if needed.
- **AEC state**: fixed at **ON** for this round (see `--aec` default
  and the report's own justification) — R0082-D/E found no reliable
  AEC-driven difference in either direction, and AEC ON is the only
  configuration that would ever actually be deployed, making it the
  most production-relevant (least confounded, in the sense of "least
  likely to test something that will never ship") choice. This round
  does NOT sweep AEC OFF/ON again.
- Not tested: `BargeInController`'s own `AecReferenceHealth` gate (by
  design, see above), Gemini/LLM/TTS concurrency, real
  `ConversationSession` behavior, deliberate human barge-in (a LATER,
  separate round).
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

PRE_ROLL_S = 2.0  # "at least 2s" per instruction
TAIL_S = 2.0  # "at least 2s" per instruction
SETTLE_S = 1.0  # matches R0082-C/D's own post-subscribe settle
SUBSCRIBE_TIMEOUT_S = 30.0  # matches R0082-C/D
CLEANUP_GRACE_S = 3.0  # matches R0082-C/D

# Production VAD values -- RE-AUDITED this round against current source,
# confirmed byte-for-byte identical to R0082-E's own audit (§ report).
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
    """Unchanged from R0082-C/D -- see those scripts' own docstrings."""
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
# Streaming 48kHz -> 16kHz resampler -- stateful, real-time-safe
# ----------------------------------------------------------------------
class StreamingResampler:
    """Stateful 48kHz->16kHz FIR-lowpass + exact 3:1 decimation.

    `scipy.signal.lfilter`'s own `zi`/`zf` state is carried across every
    `push()` call, and a running total-samples-seen counter fixes the
    decimation phase across arbitrary chunk-length boundaries -- NOT
    independent per-chunk resampling (which would reset the FIR filter's
    memory, and therefore distort the signal, at every chunk boundary)."""

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
    Verified isolation, same as R0082-E."""
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
    (512-sample, 16kHz) frame at a time as they become available from the
    streaming resampler. Reused, not duplicated, across the live hardware
    role and the offline synthetic-control tests below."""

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
        self.csv_writer = csv_writer
        self.max_prob = 0.0

    def process_frame(self, frame_int16: np.ndarray, t_s: float) -> None:
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

        self.n_frames += 1
        if self.csv_writer is not None:
            self.csv_writer.writerow(
                [
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
            )


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

            # Self-read our OWN just-published mic track. Confirmed this
            # round (synthetic control, no hardware) that AudioStream on a
            # LocalAudioTrack works with no room round-trip; NOT yet
            # empirically confirmed specifically for a PlatformAudioSource
            # -backed track -- see module docstring LIMITATIONS.
            mic_stream = rtc.AudioStream(track, sample_rate=LIVE_SAMPLE_RATE, num_channels=1)

            OUT_DIR.mkdir(parents=True, exist_ok=True)
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            csv_path = OUT_DIR / f"r0082f_aec{'on' if aec else 'off'}_{run_id}_timeline.csv"
            csv_file = open(csv_path, "w", newline="")
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(CSV_HEADER)

            chain = LiveVadChain(csv_writer)
            resampler = StreamingResampler()
            vad_buffer = np.zeros(0, dtype=np.int16)
            total_16k_samples = 0
            raw_48k_chunks: list[bytes] = []
            resampled_16k_chunks: list[bytes] = []
            first_frame_logged = False

            total_hold_s = SETTLE_S + PRE_ROLL_S + 23.181416666666667 + TAIL_S + CLEANUP_GRACE_S
            deadline = time.monotonic() + total_hold_s
            print(
                f"[hardware] MILESTONE: VAD ready -- holding for {total_hold_s:.1f}s "
                f"(SETTLE={SETTLE_S}+PRE_ROLL={PRE_ROLL_S}+speech=23.181+TAIL={TAIL_S}"
                f"+CLEANUP_GRACE={CLEANUP_GRACE_S})"
            )

            async def _consume() -> None:
                nonlocal vad_buffer, total_16k_samples, first_frame_logged
                async for event in mic_stream:
                    frame = event.frame
                    if not first_frame_logged:
                        print(
                            f"[hardware] first live frame: sample_rate={frame.sample_rate} "
                            f"num_channels={frame.num_channels} "
                            f"samples_per_channel={frame.samples_per_channel}"
                        )
                        first_frame_logged = True
                    raw_bytes = bytes(frame.data)
                    raw_48k_chunks.append(raw_bytes)
                    chunk = np.frombuffer(raw_bytes, dtype="<i2")
                    resampled = resampler.push(chunk)
                    resampled_16k_chunks.append(resampled.astype("<i2").tobytes())
                    vad_buffer = np.concatenate([vad_buffer, resampled])
                    while len(vad_buffer) >= VAD_FRAME_SAMPLES:
                        vad_frame = vad_buffer[:VAD_FRAME_SAMPLES]
                        vad_buffer = vad_buffer[VAD_FRAME_SAMPLES:]
                        t_s = total_16k_samples / VAD_SAMPLE_RATE
                        chain.process_frame(vad_frame, t_s)
                        total_16k_samples += VAD_FRAME_SAMPLES
                    if time.monotonic() >= deadline:
                        break

            consume_task = asyncio.create_task(_consume())
            try:
                await asyncio.wait_for(consume_task, timeout=total_hold_s + 5.0)
            except TimeoutError:
                consume_task.cancel()

            print("[hardware] MILESTONE: TAIL end / hold complete")
            csv_file.close()

            raw_wav_path = OUT_DIR / f"r0082f_aec{'on' if aec else 'off'}_{run_id}_mic_48k.wav"
            vad_wav_path = OUT_DIR / f"r0082f_aec{'on' if aec else 'off'}_{run_id}_mic_16k.wav"
            _write_wav(raw_wav_path, b"".join(raw_48k_chunks), sample_rate=LIVE_SAMPLE_RATE)
            _write_wav(vad_wav_path, b"".join(resampled_16k_chunks), sample_rate=VAD_SAMPLE_RATE)
            print(f"[hardware] wrote {raw_wav_path.name} sha256={sha256_of(raw_wav_path)}")
            print(f"[hardware] wrote {vad_wav_path.name} sha256={sha256_of(vad_wav_path)}")
            print(f"[hardware] wrote {csv_path.name} ({chain.n_frames} VAD frames)")

            print(
                f"[hardware] RESULT: max_prob={chain.max_prob:.4f}  "
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

            await mic_stream.aclose()
        finally:
            source.close()
            await room.disconnect()
            print(f"[hardware pid={os.getpid()}] disconnected cleanly")
    finally:
        platform_audio.close()
        print(f"[hardware pid={os.getpid()}] platform_audio closed, exiting")


async def run_speech_role(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> None:
    print(f"[speech pid={os.getpid()}] starting")
    signal_pcm = read_speech_wav()
    token = _make_token(
        api_key=api_key, api_secret=api_secret, identity=SPEECH_IDENTITY, room=room_name
    )
    room = rtc.Room()
    hw_track_ready = asyncio.Event()

    def _on_track_subscribed(track_, publication, participant) -> None:
        if participant.identity == HARDWARE_IDENTITY and track_.kind == rtc.TrackKind.KIND_AUDIO:
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
        print("[speech] MILESTONE: hardware mic track subscribed")
        await asyncio.sleep(SETTLE_S)

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
        await asyncio.sleep(TAIL_S + CLEANUP_GRACE_S)
        print("[speech] MILESTONE: TAIL end")
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
        help="WebRTC AEC on/off for --role hardware (default: on -- see "
             "module docstring / report for why ON is the chosen state "
             "this round). Ignored by --role speech.",
    )
    p.add_argument("--room-name", required=True)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--api-secret", default=DEFAULT_API_SECRET)
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    if args.role == "hardware":
        await run_hardware_role(
            aec=(args.aec == "on"), url=args.url, api_key=args.api_key,
            api_secret=args.api_secret, room_name=args.room_name,
        )
    else:
        await run_speech_role(
            url=args.url, api_key=args.api_key, api_secret=args.api_secret,
            room_name=args.room_name,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
