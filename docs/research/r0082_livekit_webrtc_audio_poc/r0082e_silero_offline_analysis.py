#!/usr/bin/env python3
"""R0082-E -- offline, production-equivalent Silero VAD analysis of the
four R0082-D self-echo mic captures (A1=OFF, B1=ON, B2=ON, A2=OFF).

**Research/analysis only. No hardware, no LiveKit, no Gemini, no
Pipecat pipeline, no live ConversationSession, no barge-in playback.**
Answers one question: would NeXa's CURRENT, frozen production Silero/
VAD/interruption configuration classify any part of these already-
recorded self-echo residuals as user speech?

## Two stages, two DIFFERENT pre-existing environments -- neither modified

No new package was installed anywhere for this script. The two stages
below use whichever ALREADY-INSTALLED environment actually has what
that stage needs, exactly the same discipline
`r0082d_generate_speech_stimulus.py` established:

    stage 1 (resample 48kHz -> 16kHz; needs scipy, which plain system
    python3 already has, confirmed R0082-C/D -- NOT installed here):
        python3 r0082e_silero_offline_analysis.py --stage resample

    stage 2 (the actual Silero+VAD+InterruptionStateMachine analysis;
    needs `onnxruntime` + `loudness`, BOTH already installed in NeXa's
    own `.venv` -- the EXACT versions production itself uses, which is
    WHY this stage deliberately runs there instead of plain python3,
    which lacks `loudness`):
        .venv/bin/python3 r0082e_silero_offline_analysis.py --stage analyze

This is a genuinely different concern from the LiveKit PoC scripts'
own isolation discipline (avoiding `nexa.*`/`pipecat.*`/`loguru` to
keep a script that touches real hardware/room state clean of the
production runtime surface). This script touches NO hardware and NO
LiveKit/room state at all -- the concern here is purely numerical
fidelity to production's own audio processing, so using the SAME
`onnxruntime`/`loudness` versions production uses is the right call,
not a violation of that other discipline.

## Production Silero/VAD/interruption configuration (audited from source)

- `SileroVADAnalyzer` (`pipecat.audio.vad.silero`, pipecat-ai 1.8.1):
  wraps the bundled ONNX model `pipecat/audio/vad/data/silero_vad.onnx`
  (sha256 `597d30b3ec076608d059477bb14cfeffdf951bf5cae370d38f65d33bbfe82004`)
  via `onnxruntime.InferenceSession`. `src/nexa/voice/runtime.py:509-511`
  constructs it with `sample_rate=self.config.sample_rate` (=16000,
  `LocalAudioConfig.sample_rate`, `src/nexa/voice/config.py:63-64`).
- Frame size: 512 samples = 32ms at 16kHz (`silero.py:191-197`,
  `vad_analyzer.py:159-160`).
- `VADParams` actually live in production -- `DEFAULT_VAD_PARAMS =
  VADParams(stop_secs=1.0)` (`src/nexa/voice/runtime.py:87`), used
  UNMODIFIED by the real entrypoint (`apps/nexa_bilingual_voice_probe.py`
  constructs `VoiceRuntime` without passing `vad_params`). Everything
  else is Pipecat's own library default (`vad_analyzer.py:25-28`):
  `confidence=0.7`, `start_secs=0.2` (NOT overridden), `min_volume=0.6`
  (NOT overridden). So: `confidence=0.7, start_secs=0.2, stop_secs=1.0,
  min_volume=0.6`.
- State machine (`VADAnalyzer._run_analyzer`, `vad_analyzer.py:213-246`):
  4 states QUIET/STARTING/SPEAKING/STOPPING. A frame counts as
  "speaking" iff `silero_confidence >= 0.7 AND smoothed_volume >= 0.6`
  (`vad_analyzer.py:211`) -- BOTH gates, not confidence alone.
  `_vad_start_frames = round(0.2 / (512/16000)) = 6` frames (192ms)
  of consecutive speaking frames confirms `SPEAKING` (Pipecat's real
  `VADUserStartedSpeakingFrame` fires here). `_vad_stop_frames =
  round(1.0 / (512/16000)) = 31` frames (~992ms) of consecutive
  non-speaking frames confirms `QUIET` again
  (`VADUserStoppedSpeakingFrame`).
- Volume: `AudioVolumeTracker` (`pipecat.audio.volume`) keeps a
  rolling 400ms window and measures ITU-R BS.1770 integrated loudness
  via the REAL `loudness.integrated_loudness()` binding (the same
  package production uses, NOT a hand-rolled reimplementation),
  normalized `(LUFS - (-110)) / ((-10) - (-110))` clamped to [0,1]
  (`pipecat/audio/utils.py:147-187`), then exponentially smoothed
  (`exp_smoothing`, factor=0.2, `vad_analyzer.py:87,176,208-209`).
- `SileroOnnxModel`'s own recurrent state resets every
  `_MODEL_RESET_STATES_TIME = 5.0` seconds of **wall-clock** time in
  the real code (`silero.py:23,214-220`) -- purely a memory-growth
  mitigation, unrelated to speech boundaries. Reproduced here via
  AUDIO-TIME bookkeeping instead of `time.time()`, since batch offline
  processing would otherwise never trigger it if run faster than real
  time; in a real continuous live capture, wall-clock time and audio
  time advance together, so this is a faithful reproduction of the
  INTENDED behavior, not a threshold/logic change. Documented, not
  hidden.
- `nexa.voice.interruption.InterruptionStateMachine` -- the REAL
  production class, loaded via `importlib.util.spec_from_file_location`
  directly from `src/nexa/voice/interruption.py` (confirmed this round
  to have ZERO imports beyond `dataclasses`/`enum` -- loading it this
  way bypasses `nexa/voice/__init__.py`'s own import chain, which does
  pull in `pipecat`/`loguru` via `bargein.py`, entirely). This is
  GENUINE production code reuse, not a reimplementation --
  `confirm_hold_secs=0.3` (`DEFAULT_CONFIRM_HOLD_SECS`,
  `interruption.py:44`, not overridden anywhere in the real wiring,
  `src/nexa/voice_tts/bargein_wiring.py:145-150`).
- **Explicitly OUT OF SCOPE, documented, not modeled**:
  `BargeInController`'s separate `AecReferenceHealth.barge_in_safe`
  gate (`bargein.py:326-340`) -- a legacy precondition specific to the
  CURRENT production XVF3800/`AecReferenceFeeder` hardware AEC path
  (R0028), unrelated to the audio content itself and unrelated to the
  PlatformAudio/WebRTC path R0082 is evaluating as a potential
  replacement. Level 3 below answers "would
  `InterruptionStateMachine` confirm an interrupt candidate from this
  audio", not "would the full current `BargeInController`, including
  its XVF3800-specific safety gate, admit it" -- a real, stated scope
  boundary, not an oversight.

## Three levels of evidence, kept separate (per instruction)

- **Level 1**: raw Silero probability per 32ms frame.
- **Level 2**: Pipecat's own `VADAnalyzer` state machine output
  (QUIET/STARTING/SPEAKING/STOPPING), i.e. whether a REAL
  `VADUserStartedSpeakingFrame`/`VADUserStoppedSpeakingFrame` would
  have fired.
- **Level 3**: `InterruptionStateMachine.speech_started()`/
  `speech_stopped()`/`poll()` fed by Level 2's transitions, modeling
  "NeXa is continuously replying" (`notify_response_dispatched()`
  called once at file start) for the WHOLE recording -- i.e. whether a
  production-equivalent `INTERRUPT_CONFIRMED` event would have fired.

No threshold was tuned to produce a particular result. The canonical
production values above are used exactly as audited.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import sys
import wave
from pathlib import Path

CAPTURES = (
    Path(__file__).resolve().parent / "r0082d_aec_captures"
)
RESAMPLED_DIR = Path(__file__).resolve().parent / "r0082e_16k_captures"

RUNS = {
    "A1_OFF": ("OFF", "r0082d_aecoff_20260918T144322Z_speech_mic.wav"),
    "B1_ON":  ("ON",  "r0082d_aecon_20260918T144740Z_speech_mic.wav"),
    "B2_ON":  ("ON",  "r0082d_aecon_20260918T144932Z_speech_mic.wav"),
    "A2_OFF": ("OFF", "r0082d_aecoff_20260918T145221Z_speech_mic.wav"),
}
#: Excluded, not part of the canonical four-run sequence -- flagged, not deleted.
EXCLUDED_RUN = "r0082d_aecoff_20260918T145123Z_speech_mic.wav"

SRC_SAMPLE_RATE = 48000
DST_SAMPLE_RATE = 16000
PRE_ROLL_S = 1.0
SPEECH_DURATION_S = 23.181416666666667
TAIL_STARTS_S = PRE_ROLL_S + SPEECH_DURATION_S

# Production values, audited from actual source this round -- see module docstring.
VAD_CONFIDENCE = 0.7
VAD_START_SECS = 0.2
VAD_STOP_SECS = 1.0
VAD_MIN_VOLUME = 0.6
FRAME_SAMPLES = 512  # 32ms @ 16kHz
FRAMES_PER_SEC = FRAME_SAMPLES / DST_SAMPLE_RATE
VAD_START_FRAMES = round(VAD_START_SECS / FRAMES_PER_SEC)
VAD_STOP_FRAMES = round(VAD_STOP_SECS / FRAMES_PER_SEC)
VOLUME_WINDOW_SECS = 0.4
VOLUME_SMOOTHING_FACTOR = 0.2
MODEL_RESET_INTERVAL_S = 5.0

ONNX_MODEL_PATH = (
    Path(__file__).resolve().parents[3]
    / ".venv"
    / "lib"
    / "python3.13"
    / "site-packages"
    / "pipecat"
    / "audio"
    / "vad"
    / "data"
    / "silero_vad.onnx"
)

NEXA_INTERRUPTION_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "nexa" / "voice" / "interruption.py"
)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_wav_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2, path
        sr = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    return pcm, sr


def write_wav_pcm(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def stage_resample() -> None:
    import numpy as np
    from scipy.signal import resample_poly

    print(f"Excluded (not canonical): {EXCLUDED_RUN} -- left untouched, not resampled.")
    for label, (aec, fname) in RUNS.items():
        src_path = CAPTURES / fname
        pcm, sr = load_wav_pcm(src_path)
        assert sr == SRC_SAMPLE_RATE, f"{src_path}: expected {SRC_SAMPLE_RATE}Hz, got {sr}"
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        # exact 1/3 ratio (48000/16000 reduced by gcd=48000) -- a clean
        # decimation via polyphase resampling (anti-aliasing lowpass
        # included), not an ad hoc method.
        y = resample_poly(x, up=1, down=3)
        y16 = y.round().clip(-32768, 32767).astype("<i2")
        out_path = RESAMPLED_DIR / f"{label}_16k.wav"
        write_wav_pcm(out_path, y16.tobytes(), DST_SAMPLE_RATE)
        digest = sha256_of(out_path)
        print(
            f"{label} ({aec}): {src_path.name} ({sr}Hz, {len(x)} samples) -> "
            f"{out_path.name} ({DST_SAMPLE_RATE}Hz, {len(y16)} samples, "
            f"{len(y16) / DST_SAMPLE_RATE:.3f}s) sha256={digest}"
        )


class SileroOnnxModel:
    """Verbatim reproduction of pipecat.audio.vad.silero.SileroOnnxModel
    (pipecat-ai 1.8.1) -- same ONNX model file, same call contract.
    Not imported from pipecat directly (would pull in loguru/pipecat's
    own package-init side effects); this is a faithful, byte-for-byte
    algorithmic copy, using the exact same bundled model file by path."""

    def __init__(self, path: str) -> None:
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        self.session = onnxruntime.InferenceSession(path, providers=providers, sess_options=opts)
        self.reset_states()
        self.sample_rates = [8000, 16000]

    def reset_states(self, batch_size: int = 1) -> None:
        import numpy as np

        self._state = np.zeros((2, batch_size, 128), dtype="float32")
        self._context = np.zeros((batch_size, 0), dtype="float32")
        self._last_sr = 0
        self._last_batch_size = 0

    def __call__(self, x, sr: int):
        import numpy as np

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
    """Verbatim from pipecat.audio.utils.exp_smoothing."""
    return prev_value + factor * (value - prev_value)


def normalize_value(value: float, min_value: float, max_value: float) -> float:
    """Verbatim from pipecat.audio.utils.normalize_value."""
    normalized = (value - min_value) / (max_value - min_value)
    return max(0.0, min(1.0, normalized))


class VolumeTracker:
    """Verbatim reproduction of pipecat.audio.volume.AudioVolumeTracker,
    using the REAL `loudness` package (the same binding production
    uses) for ITU-R BS.1770 integrated loudness -- not a hand-rolled
    reimplementation of the loudness algorithm itself."""

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
        import numpy as np

        if self.volume_cached is None:
            audio_np = np.frombuffer(self.buffer, dtype=np.int16)
            audio_float = audio_np.astype(np.float32) / 32768.0
            level = loudness.integrated_loudness(audio_float, self.sample_rate)
            self.volume_cached = normalize_value(level, -110, -10)
        return self.volume_cached


def load_interruption_state_machine_class():
    spec = importlib.util.spec_from_file_location(
        "nexa_interruption_standalone", str(NEXA_INTERRUPTION_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nexa_interruption_standalone"] = mod
    spec.loader.exec_module(mod)
    strict_forbidden = ("nexa.voice", "pipecat", "loguru")
    loaded = sorted(
        n
        for n in sys.modules
        if any(n == p or n.startswith(p + ".") for p in strict_forbidden)
        and n != "nexa_interruption_standalone"
    )
    if loaded:
        raise SystemExit(
            f"Loading {NEXA_INTERRUPTION_PATH} unexpectedly pulled in: {loaded} "
            "-- isolation broken, refusing to proceed."
        )
    return mod


def analyze_run(label: str, aec: str, pcm: bytes) -> dict:
    import numpy as np

    model = SileroOnnxModel(str(ONNX_MODEL_PATH))
    volume_tracker = VolumeTracker(DST_SAMPLE_RATE)
    prev_volume = 0.0

    interruption_mod = load_interruption_state_machine_class()
    sm = interruption_mod.InterruptionStateMachine()
    sm.notify_response_dispatched()  # models "NeXa is replying" for the whole file

    vad_state = "QUIET"
    starting_count = 0
    stopping_count = 0

    n_total_samples = len(pcm) // 2
    n_frames = n_total_samples // FRAME_SAMPLES
    last_reset_audio_time = 0.0

    timeline = []  # (t_s, prob, vad_state, volume, speech_start, speech_end, confirmed)
    confirmed_events = []
    started_events = []
    stopped_events = []

    for i in range(n_frames):
        frame_bytes = pcm[i * FRAME_SAMPLES * 2 : (i + 1) * FRAME_SAMPLES * 2]
        t_s = i * FRAME_SAMPLES / DST_SAMPLE_RATE

        audio_int16 = np.frombuffer(frame_bytes, np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        # ONNX output shape is (batch=1, 1) -- pipecat's own voice_confidence()
        # returns out[0] (shape (1,)) un-converted; float() on that 1-element
        # array raises in this numpy version, so index one level further.
        prob = float(model(audio_float32, DST_SAMPLE_RATE)[0][0])

        if t_s - last_reset_audio_time >= MODEL_RESET_INTERVAL_S:
            model.reset_states()
            last_reset_audio_time = t_s

        volume_tracker.update(frame_bytes)
        raw_volume = volume_tracker.volume
        volume = exp_smoothing(raw_volume, prev_volume, VOLUME_SMOOTHING_FACTOR)
        prev_volume = volume

        speaking = prob >= VAD_CONFIDENCE and volume >= VAD_MIN_VOLUME

        speech_start_event = False
        speech_end_event = False

        if speaking:
            if vad_state == "QUIET":
                vad_state = "STARTING"
                starting_count = 1
            elif vad_state == "STARTING":
                starting_count += 1
            elif vad_state == "STOPPING":
                vad_state = "SPEAKING"
                stopping_count = 0
        else:
            if vad_state == "STARTING":
                vad_state = "QUIET"
                starting_count = 0
            elif vad_state == "SPEAKING":
                vad_state = "STOPPING"
                stopping_count = 1
            elif vad_state == "STOPPING":
                stopping_count += 1

        if vad_state == "STARTING" and starting_count >= VAD_START_FRAMES:
            vad_state = "SPEAKING"
            starting_count = 0
            speech_start_event = True
            started_events.append(t_s)
            sm.speech_started(t_s)

        if vad_state == "STOPPING" and stopping_count >= VAD_STOP_FRAMES:
            vad_state = "QUIET"
            stopping_count = 0
            speech_end_event = True
            stopped_events.append(t_s)
            sm.speech_stopped(t_s)

        ev = sm.poll(t_s)
        confirmed = ev.value == "interrupt_confirmed"
        if confirmed:
            confirmed_events.append(t_s)

        timeline.append(
            (t_s, prob, vad_state, volume, speech_start_event, speech_end_event, confirmed)
        )

    probs = np.array([r[1] for r in timeline])
    return {
        "label": label,
        "aec": aec,
        "n_frames": n_frames,
        "timeline": timeline,
        "max_prob": float(np.max(probs)) if len(probs) else 0.0,
        "mean_prob": float(np.mean(probs)) if len(probs) else 0.0,
        "p95_prob": float(np.percentile(probs, 95)) if len(probs) else 0.0,
        "p99_prob": float(np.percentile(probs, 99)) if len(probs) else 0.0,
        "frames_above_threshold": int(np.sum(probs >= VAD_CONFIDENCE)),
        "started_events": started_events,
        "stopped_events": stopped_events,
        "confirmed_events": confirmed_events,
        "rejected_candidates": sm.rejected_candidates,
        "confirmed_interruptions": sm.confirmed_interruptions,
    }


def region_of(t_s: float) -> str:
    if t_s < PRE_ROLL_S:
        return "pre-roll"
    if t_s < TAIL_STARTS_S:
        return "speech"
    return "tail"


def stage_analyze() -> None:
    model_hash = sha256_of(ONNX_MODEL_PATH) if ONNX_MODEL_PATH.exists() else "N/A"
    print(f"ONNX model: {ONNX_MODEL_PATH}")
    print(f"  exists: {ONNX_MODEL_PATH.exists()}  sha256: {model_hash}")
    print(
        f"Production values: confidence={VAD_CONFIDENCE} start_secs={VAD_START_SECS} "
        f"stop_secs={VAD_STOP_SECS} min_volume={VAD_MIN_VOLUME}"
    )
    start_ms = VAD_START_FRAMES * FRAME_SAMPLES / DST_SAMPLE_RATE * 1000
    stop_ms = VAD_STOP_FRAMES * FRAME_SAMPLES / DST_SAMPLE_RATE * 1000
    print(
        f"  VAD_START_FRAMES={VAD_START_FRAMES} ({start_ms:.0f}ms)  "
        f"VAD_STOP_FRAMES={VAD_STOP_FRAMES} ({stop_ms:.0f}ms)"
    )
    print(f"Excluded (not canonical): {EXCLUDED_RUN}")
    print()

    all_results = {}
    for label, (aec, _fname) in RUNS.items():
        in_path = RESAMPLED_DIR / f"{label}_16k.wav"
        pcm, sr = load_wav_pcm(in_path)
        assert sr == DST_SAMPLE_RATE, f"{in_path}: expected {DST_SAMPLE_RATE}Hz, got {sr}"
        print(f"=== {label} ({aec}) -- {in_path.name}, {len(pcm) // 2} samples ===")
        result = analyze_run(label, aec, pcm)
        all_results[label] = result

        print(f"  max_prob={result['max_prob']:.4f}  mean_prob={result['mean_prob']:.4f}  "
              f"p95={result['p95_prob']:.4f}  p99={result['p99_prob']:.4f}")
        print(
            f"  frames_above_threshold({VAD_CONFIDENCE})="
            f"{result['frames_above_threshold']} / {result['n_frames']}"
        )
        print(f"  VADUserStartedSpeaking-equivalent events: {len(result['started_events'])}")
        for t in result["started_events"]:
            print(f"    t={t:7.3f}s ({region_of(t)})")
        print(f"  VADUserStoppedSpeaking-equivalent events: {len(result['stopped_events'])}")
        for t in result["stopped_events"]:
            print(f"    t={t:7.3f}s ({region_of(t)})")
        print(f"  InterruptionStateMachine CONFIRMED events: {len(result['confirmed_events'])} "
              f"(rejected_candidates={result['rejected_candidates']})")
        for t in result["confirmed_events"]:
            print(
                f"    t={t:7.3f}s ({region_of(t)})  "
                "*** PRODUCTION-EQUIVALENT FALSE SPEECH START ***"
            )

        # CSV timeline artifact
        csv_path = RESAMPLED_DIR / f"{label}_timeline.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_s", "region", "silero_prob", "threshold", "vad_state",
                        "speech_start_event", "speech_end_event", "interrupt_confirmed"])
            for t_s, prob, vs, _vol, se, ee, conf in result["timeline"]:
                w.writerow([f"{t_s:.3f}", region_of(t_s), f"{prob:.5f}", VAD_CONFIDENCE, vs,
                            int(se), int(ee), int(conf)])
        print(f"  wrote timeline CSV: {csv_path.name} ({len(result['timeline'])} rows)")
        print()

    print("=" * 70)
    print("SPECIAL REGION INSPECTION: natural-file-time t=6.5-8.5s (covers R0082-D's")
    print("flagged stimulus-relative t~6-7s region across all four runs' own lags)")
    print("=" * 70)
    for label, result in all_results.items():
        window = [r for r in result["timeline"] if 6.5 <= r[0] <= 8.5]
        max_p = max((r[1] for r in window), default=0.0)
        n_above = sum(1 for r in window if r[1] >= VAD_CONFIDENCE)
        print(f"  {label}: max_prob_in_window={max_p:.4f}  n_frames_above_threshold={n_above}")

    print()
    print("=" * 70)
    print("SPECIAL REGION INSPECTION: A1's pre-roll transient (t=0.0-1.0s)")
    print("=" * 70)
    a1 = all_results["A1_OFF"]
    window = [r for r in a1["timeline"] if r[0] < PRE_ROLL_S]
    max_p = max((r[1] for r in window), default=0.0)
    n_above = sum(1 for r in window if r[1] >= VAD_CONFIDENCE)
    print(
        f"  A1_OFF pre-roll: max_prob={max_p:.4f}  "
        f"n_frames_above_threshold={n_above} / {len(window)}"
    )

    print()
    print("=" * 70)
    print("MAIN COMPARISON TABLE")
    print("=" * 70)
    header = (
        f"{'run':>8} {'aec':>4} {'max_prob':>9} {'frames>thr':>11} "
        f"{'starts':>7} {'confirmed':>10}"
    )
    print(header)
    for label, result in all_results.items():
        print(
            f"{label:>8} {result['aec']:>4} {result['max_prob']:>9.4f} "
            f"{result['frames_above_threshold']:>11} {len(result['started_events']):>7} "
            f"{len(result['confirmed_events']):>10}"
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--stage", choices=["resample", "analyze"], required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.stage == "resample":
        stage_resample()
    else:
        stage_analyze()


if __name__ == "__main__":
    main()
