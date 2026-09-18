#!/usr/bin/env python3
"""R0082-B — minimal, room-free LiveKit/WebRTC audio-path AEC PoC.

**No Gemini, no LLM, no STT, no TTS, no Memory, no NeXa Core, no Silero,
no barge-in logic, no LiveKit server/room required.** Tests ONLY whether
LiveKit's WebRTC Audio Processing Module (APM) -- via the
``livekit.rtc.media_devices`` helper -- can capture from the real
reSpeaker XVF3800 mic while playing a deterministic signal through the
real USB speaker, with acoustic echo cancellation applied to the SAME
capture, and whether the resulting residual is more STABLE over a
30-60s continuous run than R0081's manual-reference topology.

## Why this exists, and why it does NOT need a LiveKit server

R0081 (``docs/reports/R0081_...20260917.md``) exhaustively investigated
NeXa's current manual topology: a real physical USB speaker
(``plug:usb_speaker``) and a SEPARATE ``AecReferenceFeeder`` process
feeding the XVF3800's own far-end reference input (``plug:respeaker``)
via a second ``aplay`` -- two independent USB Audio Class devices with
no shared/configured clock, one ALSA-resampled 16kHz->48kHz and one not.
That investigation found the XVF3800's own hardware AEC works but leaves
a large, non-monotonic, in-stream residual that changes over time even
inside ONE uninterrupted stream (R0081 SS16.14/SS16.17).

R0082 asks whether LiveKit's own WebRTC Audio Device Module -- a single,
unified audio subsystem that owns BOTH capture and playout and feeds the
render signal directly into its own APM's reverse-stream path (no
separate physical reference device, no separate ALSA process, no manual
reference feed) -- produces a more stable result. Per R0082-A's own
source audit (``docs/reports/R0082_...md``), the installed
``livekit.rtc.media_devices.MediaDevices`` helper (backed by
``sounddevice``/PortAudio + the real WebRTC ``AudioProcessingModule``,
NOT a mock) supports exactly this: ``open_input(enable_aec=...)`` then
``open_output()`` wires the output's rendered PCM into the SAME APM
instance's reverse stream automatically -- fully local, no
room/server/token needed. This script uses exactly that path.

## Reused, not reimplemented -- but from a LOCAL, dependency-free copy

Test-signal generation (``build_signal``/``build_test_signal``/
``build_mls_signal``) and the analysis primitives (``_rms``/``_peak``/
``_write_wav``) are imported from this directory's own
``r0082_audio_utils.py`` -- the SAME algorithms/amplitude semantics as
``docs/research/m2_6_cloud_realtime_voice/r0081_direct_aec_diagnostic.py``
and ``m2_6b4m_self_echo_probe.py`` (byte-for-byte identical output; see
``r0082_audio_utils.py``'s own docstring), at a 48kHz sample rate
(``MediaDevices``'s own default; R0082-A's own audit found this API path
does NOT use NeXa's old 16kHz internal rate).

**Why a local copy instead of importing R0081's scripts directly:** the
first real R0082-B hardware attempt failed at import time --
``r0081_direct_aec_diagnostic.py`` imports ``nexa.voice.aec_gain`` at
module scope, which transitively loads ``nexa.voice``'s own
``__init__.py`` -> ``nexa.voice.bargein`` -> ``loguru``, none of which are
(or should be) installed in R0082's isolated probe venv. That was an
import-time isolation defect, not a hardware/AEC/LiveKit failure --
``r0082_audio_utils.py`` fixes it by holding a verbatim, standard-library-
only copy of just the primitives this PoC actually needs.

## Environment -- this needs an ISOLATED venv, NOT NeXa's own .venv

``livekit``/``livekit-api``/``sounddevice``/``tenacity``/``pyjwt`` are
NOT installed in NeXa's own project ``.venv`` (confirmed, R0082-A) --
installing them there would change the production dependency surface
for a pure architecture spike, which this investigation does not do.
Per this project's own established precedent (R0053/R0081's XVF3800
vendor-tool dependencies, installed into a pre-existing UNRELATED local
dev venv, never NeXa's own), this script must be run with a SEPARATE
Python environment that has the ``livekit``/``sounddevice`` extras
installed -- see R0082-A's report for the exact versions verified
compatible with this Raspberry Pi (aarch64) and the exact install
command. This script imports ONLY this directory's own
``r0082_audio_utils.py`` (stdlib-only) plus ``livekit``/``sounddevice`` --
never ``nexa.*``, ``pipecat.*``, ``google.*``, or ``loguru``.

## Usage (see R0082's own report for the full protocol; do not run this
## against real hardware without reading R0082-A's device-enumeration
## findings first -- device SELECTION uses a NAME substring match, not a
## hardcoded index, because R0082-A found `AudioDeviceInfo.id` is EMPTY
## on this Linux/PipeWire backend, contrary to the SDK's own docstring
## claim that `id` is generally preferred and stable)

    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py \\
        --aec on --duration 30 --stimulus tones

    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py \\
        --aec off --duration 30 --stimulus tones

Run ``--aec off`` then ``--aec on`` back to back, same physical volume/
room/device positions, for a directly comparable A/B pair -- exactly the
same discipline R0081 established for its own ALSA-reference A/B tests.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# R0082 -- import ONLY from this directory's own local, stdlib-only
# r0082_audio_utils.py, NOT from docs/research/m2_6_cloud_realtime_voice/.
# r0081_direct_aec_diagnostic.py imports `nexa.voice.aec_gain` at module
# scope, which transitively loads `nexa.voice.bargein` -> `loguru` --
# an accidental production-runtime dependency this PoC must not carry.
# r0082_audio_utils.py holds verbatim copies of the same algorithms
# (build_signal/_rms/_peak/_write_wav) with that dependency removed; see
# its own module docstring and R0082's report for the full story.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from r0082_audio_utils import (  # noqa: E402  (local, stdlib-only)
    _peak,
    _rms,
    _write_wav,
    build_signal,
)

try:
    from livekit import rtc
except ModuleNotFoundError as e:  # pragma: no cover -- environment-dependent, by design
    print(
        "ERROR: `livekit` is not importable in this Python environment.\n"
        "This script deliberately does NOT run in NeXa's own .venv -- see this "
        "module's own docstring and R0082-A's report for the exact isolated-venv "
        f"install command. ({e})",
        file=sys.stderr,
    )
    raise SystemExit(1) from e

SAMPLE_RATE = 48000  # R0082-A: MediaDevices' own DEFAULT_SAMPLE_RATE, NOT the old 16kHz path
FRAME_SAMPLES = 480  # 10ms at 48kHz, matches MediaDevices' own FRAME_SAMPLES
WINDOW_S = 3.0  # analysis window size, matching R0081's --track-continuous cycle_s convention
PRE_ROLL_S = 1.0
TAIL_S = 1.0

OUT_DIR = Path(__file__).resolve().parent / "r0082_aec_captures"

#: R0082-A: real device names observed on THIS hardware via
#: `MediaDevices.list_input_devices()`/`list_output_devices()` (PipeWire's
#: own PulseAudio-compatible naming). Used as a case-insensitive substring
#: match against each enumerated device's own `name` field at RUNTIME --
#: never a hardcoded index -- because a fresh enumeration is the only way
#: to get a name/index pairing that's actually valid for THIS run (index
#: alone is not guaranteed stable across reconnects/reboots, exactly the
#: same class of problem /etc/asound.conf's own comment already documents
#: for the OLD ALSA path).
DEFAULT_INPUT_NAME_SUBSTRING = "reSpeaker"
DEFAULT_OUTPUT_NAME_SUBSTRING = "UACDemoV1.0"


def _find_device_index(devices: list[dict], name_substring: str, *, role: str) -> int:
    """Resolve a device `index` by case-insensitive NAME substring match
    against a freshly-enumerated device list -- never a hardcoded index.
    Prefers a device whose name starts with "default: " (PipeWire's own
    marker for the currently-configured default sink/source) if more than
    one match exists, since that is the one actually wired to the real
    hardware by the system's own audio-server configuration."""
    matches = [d for d in devices if name_substring.lower() in str(d.get("name", "")).lower()]
    if not matches:
        names = [d.get("name") for d in devices]
        raise SystemExit(
            f"No {role} device name contains {name_substring!r}. "
            f"Enumerated {role} devices: {names}"
        )
    default_matches = [d for d in matches if str(d.get("name", "")).startswith("default:")]
    chosen = default_matches[0] if default_matches else matches[0]
    return int(chosen["index"])


def clip_stats(pcm: bytes) -> dict:
    """R0082 -- same clipping-detection convention as R0081's own script
    (int16 saturation boundary count), reused conceptually, not
    reimported (R0081's ``clip_stats`` is not a public reuse target for a
    different sample-rate/amplitude convention module, but the logic is
    identical and trivial -- kept local to avoid a cross-module coupling
    that would not carry meaning across the two different scripts' own
    signal pipelines)."""
    if not pcm:
        return {"n_samples": 0, "n_clipped": 0, "pct_clipped": 0.0}
    values = array.array("h")
    values.frombytes(pcm)
    n_clipped = sum(1 for v in values if v in (32767, -32768))
    n = len(values)
    return {
        "n_samples": n,
        "n_clipped": n_clipped,
        "pct_clipped": round(100.0 * n_clipped / n, 2) if n else 0.0,
    }


async def _play_signal(source: rtc.AudioSource, pcm: bytes) -> None:
    """Push ``pcm`` into a synthetic ``AudioSource`` in FRAME_SAMPLES (10ms)
    chunks. ``AudioSource.capture_frame`` self-paces via its own internal
    queue backpressure (confirmed from the installed SDK's own docstring,
    R0082-A) -- it awaits until there is queue room, so no manual
    ``asyncio.sleep`` pacing is added here; a trailing chunk shorter than
    FRAME_SAMPLES is zero-padded to a full frame (WebRTC frames are fixed-
    size) rather than dropped."""
    total_samples = len(pcm) // 2
    offset = 0
    while offset < total_samples:
        chunk_samples = min(FRAME_SAMPLES, total_samples - offset)
        frame = rtc.AudioFrame.create(SAMPLE_RATE, 1, FRAME_SAMPLES)
        buf = array.array("h", frame.data)
        chunk = array.array("h")
        chunk.frombytes(pcm[offset * 2 : (offset + chunk_samples) * 2])
        for i in range(FRAME_SAMPLES):
            buf[i] = chunk[i] if i < chunk_samples else 0
        # R0082 -- `frame.data` is an 'h'-format (int16) memoryview, NOT raw
        # bytes (confirmed empirically against the installed SDK): assigning
        # `buf.tobytes()` (a bytes object, 'B' format) raises
        # "memoryview assignment: lvalue and rvalue have different
        # structures". Assigning the array.array('h', ...) object directly
        # is format-compatible and confirmed to work.
        frame.data[:] = buf
        await source.capture_frame(frame)
        offset += chunk_samples


async def _capture_track(
    track: rtc.Track, duration_s: float, chunks: list[bytes]
) -> None:
    """Read frames from an ``AudioStream`` wrapping ``track`` (a LOCAL
    track here -- ``AudioStream`` works on any ``Track``, local or
    remote, confirmed from the installed SDK source, R0082-A) for
    ``duration_s`` seconds, appending each frame's raw PCM bytes to
    ``chunks``. Purely additive accumulation -- no processing here."""
    stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
    deadline = time.monotonic() + duration_s
    try:
        async for event in stream:
            chunks.append(bytes(event.frame.data))
            if time.monotonic() >= deadline:
                break
    finally:
        await stream.aclose()


async def run_poc(*, aec: bool, duration_s: float, stimulus: str, amplitude: float) -> dict:
    media = rtc.MediaDevices(input_sample_rate=SAMPLE_RATE, output_sample_rate=SAMPLE_RATE)
    input_devices = media.list_input_devices()
    output_devices = media.list_output_devices()
    input_idx = _find_device_index(input_devices, DEFAULT_INPUT_NAME_SUBSTRING, role="input")
    output_idx = _find_device_index(output_devices, DEFAULT_OUTPUT_NAME_SUBSTRING, role="output")

    print(f"  input device  (index={input_idx}): "
          f"{next(d['name'] for d in input_devices if d['index'] == input_idx)!r}")
    print(f"  output device (index={output_idx}): "
          f"{next(d['name'] for d in output_devices if d['index'] == output_idx)!r}")

    input_capture = media.open_input(
        enable_aec=aec, noise_suppression=False, high_pass_filter=False,
        auto_gain_control=False, input_device=input_idx,
    )
    output_player = media.open_output(output_device=output_idx)

    # Build the full deterministic signal ONCE (reused builder, R0081).
    total_s = PRE_ROLL_S + duration_s + TAIL_S
    signal_pcm = build_signal(
        stimulus, duration_s=duration_s, amplitude=amplitude, sample_rate=SAMPLE_RATE
    )

    # Local mic track (wraps the real captured audio from InputCapture.source)
    # and local test-signal track (a synthetic AudioSource we push PCM into).
    mic_track = rtc.LocalAudioTrack.create_audio_track("r0082_mic", input_capture.source)
    signal_source = rtc.AudioSource(SAMPLE_RATE, 1)
    signal_track = rtc.LocalAudioTrack.create_audio_track("r0082_signal", signal_source)

    captured_chunks: list[bytes] = []
    capture_task = asyncio.create_task(_capture_track(mic_track, total_s, captured_chunks))

    await output_player.add_track(signal_track)
    await output_player.start()

    await asyncio.sleep(PRE_ROLL_S)
    await _play_signal(signal_source, signal_pcm)
    await asyncio.sleep(TAIL_S)

    await capture_task
    await output_player.aclose()
    await input_capture.aclose()
    media.close() if hasattr(media, "close") else None

    captured = b"".join(captured_chunks)
    pre_roll_samples = int(PRE_ROLL_S * SAMPLE_RATE)
    quiet_before = captured[: pre_roll_samples * 2]
    start_byte = pre_roll_samples * 2
    window_samples = int(WINDOW_S * SAMPLE_RATE)
    n_windows = int(duration_s // WINDOW_S)

    windows_data = []
    for i in range(n_windows):
        w_start = start_byte + i * window_samples * 2
        w_end = w_start + window_samples * 2
        window = captured[w_start:w_end]
        windows_data.append({
            "window": i + 1,
            "window_start_s": round(i * WINDOW_S, 2),
            "window_end_s": round((i + 1) * WINDOW_S, 2),
            "mic_window_rms": round(_rms(window), 1),
            "mic_window_peak": _peak(window),
        })

    return {
        "aec": aec,
        "stimulus": stimulus,
        "amplitude": amplitude,
        "quiet_before_rms": round(_rms(quiet_before), 1),
        "windows_data": windows_data,
        "captured_full": captured,
        "signal_pcm": signal_pcm,
        "input_device_name": next(d["name"] for d in input_devices if d["index"] == input_idx),
        "output_device_name": next(d["name"] for d in output_devices if d["index"] == output_idx),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--aec", choices=["on", "off"], required=True,
        help="WebRTC AEC on/off -- the A/B toggle this PoC exists to test.",
    )
    p.add_argument(
        "--duration", type=float, default=30.0,
        help="Test duration in seconds (default 30).",
    )
    p.add_argument(
        "--stimulus", choices=["tones", "mls"], default="tones",
        help="Reused from R0081 (default 'tones').",
    )
    p.add_argument(
        "--amplitude", type=float, default=0.05,
        help="Peak amplitude (default 0.05, matches R0081).",
    )
    p.add_argument("--input-name", default=DEFAULT_INPUT_NAME_SUBSTRING)
    p.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME_SUBSTRING)
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    global DEFAULT_INPUT_NAME_SUBSTRING, DEFAULT_OUTPUT_NAME_SUBSTRING
    DEFAULT_INPUT_NAME_SUBSTRING = args.input_name
    DEFAULT_OUTPUT_NAME_SUBSTRING = args.output_name

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    print("NeXa R0082-B — LiveKit/WebRTC PlatformAudio AEC PoC")
    print(f"  run id: {run_id}")
    print(f"  AEC: {args.aec}  duration: {args.duration:g}s  stimulus: {args.stimulus}  "
          f"amplitude: {args.amplitude}  sample_rate: {SAMPLE_RATE}Hz")

    result = await run_poc(
        aec=(args.aec == "on"), duration_s=args.duration,
        stimulus=args.stimulus, amplitude=args.amplitude,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    label = f"r0082_aec{args.aec}_{run_id}_{args.stimulus}"
    signal_path = OUT_DIR / f"{label}_signal.wav"
    mic_path = OUT_DIR / f"{label}_mic.wav"
    _write_wav(signal_path, result["signal_pcm"], sample_rate=SAMPLE_RATE)
    _write_wav(mic_path, result["captured_full"], sample_rate=SAMPLE_RATE)

    print(f"  quiet_before_rms: {result['quiet_before_rms']}")
    print()
    print(f"  {'window':>6}  {'start_s':>8}  {'end_s':>8}  {'mic_rms':>8}  {'mic_peak':>9}")
    for w in result["windows_data"]:
        print(f"  {w['window']:>6}  {w['window_start_s']:>8.2f}  {w['window_end_s']:>8.2f}  "
              f"{w['mic_window_rms']:>8.1f}  {w['mic_window_peak']:>9}")

    rms_values = [w["mic_window_rms"] for w in result["windows_data"]]
    if rms_values:
        half = max(1, len(rms_values) // 2)
        first_half, second_half = rms_values[:half], rms_values[half:]
        fh_mean = sum(first_half) / len(first_half)
        print()
        print(f"  overall mean       : {sum(rms_values) / len(rms_values):.2f}")
        print(f"  first-half mean    : {fh_mean:.2f}")
        if second_half:
            sh_mean = sum(second_half) / len(second_half)
            print(f"  second-half mean   : {sh_mean:.2f}")
        rms_min, rms_max = min(rms_values), max(rms_values)
        print(f"  min / max          : {rms_min:.2f} / {rms_max:.2f}")
        if rms_min > 0:
            print(f"  max/min ratio      : {rms_max / rms_min:.2f}x")

    print()
    print(f"  run id {run_id} -- saved:")
    print(f"    {signal_path}")
    print(f"    {mic_path}")
    print()
    print("Report CPU/RAM usage separately (e.g. `ps -o %cpu,%mem,etime -p <pid>` or "
          "`top`/`htop` during this run) -- not measured by this script itself, per "
          "R0082's own acceptance criteria.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
