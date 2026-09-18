#!/usr/bin/env python3
"""R0082-D — PlatformAudio + PipeWire + LiveKit, REAL SPEECH AEC OFF/ON.

**No Gemini, no LLM, no STT, no Memory, no NeXa Core, no Silero, no
barge-in logic, no `AecReferenceFeeder`.** Tests whether LiveKit's
`rtc.PlatformAudio` WebRTC AEC suppresses a REAL SPEECH-SHAPED
self-playback signal reaching the mic path -- not the synthetic
`stationary_multitone` R0082-C used. This is still an audio-layer-only
diagnostic: Silero VAD is deliberately NOT exercised here (that is a
LATER, separate stage — see R0082's own report).

## Why speech, now

R0082-C's `stationary_multitone` OFF/ON pair (analyzed in R0082's own
report, §44-47) found: OFF genuinely captured real, frequency-exact
speaker leakage (though small relative to unrelated background noise);
ON had a large, abrupt, non-monotonic startup transient; and — even
after convergence — ON remained materially ABOVE OFF, both overall and
at the exact stimulus frequencies. That result was diagnostic only
(synthetic, non-speech, single un-repeated run pair) and does not by
itself explain or predict NeXa's real self-interruption problem, which
depends on how a REAL SPEECH-shaped residual behaves relative to what
Silero VAD would classify as speech. R0082-D moves one step closer:
same split-process topology, same AEC toggle, but now with a frozen,
real speech recording as the stimulus, played identically across a
COUNTERBALANCED four-run sequence (OFF1/ON1/ON2/OFF2) instead of one
single pair — because R0082-C's own OFF/ON pair had materially
different pre-stimulus quiet baselines (2.84 vs. 15.06 RMS), and one
pair alone cannot separate a true AEC effect from run-to-run variation.

## The frozen speech asset (generated once, reused byte-for-byte)

`docs/research/r0082_livekit_webrtc_audio_poc/r0082d_speech_stimulus/
r0082d_speech_en_pl_v1.wav` — generated ONCE, offline, by
`r0082d_generate_speech_stimulus.py` (a separate, one-time preparation
script, NOT part of this runtime harness) from NeXa's own
ALREADY-INSTALLED local Piper voices (`en_GB-jenny_dioco-medium` +
`pl_PL-gosia-medium`, exactly as configured in `src/nexa/tts/config.py`
— no new TTS system installed, no cloud/Gemini TTS used). Resampled
once (polyphase, exact 320/147 ratio) from Piper's native 22050Hz to
this project's canonical 48000Hz mono S16_LE research format. This file
is IMMUTABLE — never regenerated between runs, never resampled or
normalized per-run. See R0082's own report for its exact SHA256,
duration, RMS, peak, and the exact text/voices used.

## Format contract — reject, never silently coerce

This script REQUIRES `--stimulus-wav` to already be mono, 16-bit
(S16_LE), 48000Hz. It does not resample, upmix/downmix, or otherwise
"fix" an incompatible file at run time — `_read_wav_validated()` raises
`SystemExit` with a clear diagnostic instead. Any format conversion
must happen once, offline, exactly as `r0082d_generate_speech_stimulus.py`
did for the canonical asset above.

## Split-process topology — preserved unchanged from R0082-C

Same two-genuinely-separate-OS-processes design R0082-C's own
`RaceDetected()` fix established (see `r0082c_platform_audio_room_aec_poc.py`'s
own docstring for the full history) — no shared `Room`, no shared
`PlatformAudio`, no Python `multiprocessing`, no `fork()`-based worker
model, no version change to the already-confirmed-compatible
`livekit==1.1.19`. `--role hardware` / `--role test`, coordinated only
via a shared `--room-name`.

## Duration synchronization — derived from the WAV, not duplicated

R0082-C's own hardware role derived its measurement-lifetime hold from
a `--duration` CLI flag the OPERATOR had to pass identically to both
processes — a fragile manual duplication. R0082-D instead has BOTH
roles read `--stimulus-wav`'s own header to derive the exact stimulus
duration directly (the hardware role reads ONLY the header, for timing;
it never plays anything itself — playout happens automatically via
PlatformAudio once it subscribes to the test role's published track).
The operator passes the SAME `--stimulus-wav` path to both invocations
and the duration is derived identically on both sides — no duplicated
number, no new unexplained magic sleep. The existing bounded
subscription handshake (`SUBSCRIBE_TIMEOUT_S`) from R0082-C is
unchanged.

## Device selection, local server, environment — unchanged from R0082-C

See `r0082c_platform_audio_room_aec_poc.py`'s own docstring for the
full detail (device-default verification instead of explicit
selection, since `AudioDeviceInfo.id` is always empty on this platform;
the local dev `livekit-server` in the session scratchpad; the isolated
probe venv this script must run in, NOT NeXa's own `.venv`). This
script imports only `r0082_audio_utils` (`_rms`/`_peak`/`_write_wav`,
stdlib-only) and `livekit`/`livekit.api` — never `nexa.*`, `pipecat.*`,
`google.*`, or `loguru`.

## Usage -- TWO separate terminals/processes, a SHARED --room-name

    # terminal 1: local dev room server (start once)
    <scratchpad>/livekit_server/livekit-server --dev --bind 127.0.0.1

    # terminal 2: hardware participant (real PlatformAudio)
    SPEECH=docs/research/r0082_livekit_webrtc_audio_poc/r0082d_speech_stimulus/r0082d_speech_en_pl_v1.wav
    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082d_platform_audio_speech_poc.py \\
        --role hardware --aec off --stimulus-wav "$SPEECH" --room-name r0082d_off1

    # terminal 3: test participant (publishes the frozen speech WAV, records mic)
    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082d_platform_audio_speech_poc.py \\
        --role test --aec off --stimulus-wav "$SPEECH" --room-name r0082d_off1

`--room-name` MUST match between the two invocations of ONE run, and
MUST be unique PER run across the four-run OFF1/ON1/ON2/OFF2 sequence
(see R0082's own report for the exact four-run procedure). `--aec` is
required for both roles: functional on `--role hardware`
(`PlatformAudioOptions.echo_cancellation`); label-only on `--role test`
(used only in its own output WAV filenames).
"""

from __future__ import annotations

import argparse
import array
import asyncio
import hashlib
import os
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r0082_audio_utils import _peak, _rms, _write_wav  # noqa: E402  (local, stdlib-only)

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

SAMPLE_RATE = 48000
FRAME_SAMPLES = 480
WINDOW_S = 3.0
PRE_ROLL_S = 1.0
TAIL_S = 1.0

#: R0082-C's own named timing constants, unchanged -- see that script's
#: own docstring for the full "why" (replaces an earlier unsafe magic-
#: number sleep with event-based cross-process synchronization).
SETTLE_S = 1.0
SUBSCRIBE_TIMEOUT_S = 30.0
CLEANUP_GRACE_S = 3.0

OUT_DIR = Path(__file__).resolve().parent / "r0082d_aec_captures"

HARDWARE_IDENTITY = "r0082d_hardware"
TEST_IDENTITY = "r0082d_test"

#: Unchanged from R0082-C -- see that script's own docstring: explicit
#: `set_recording_device`/`set_playout_device` cannot target a specific
#: device on this platform/SDK version (`AudioDeviceInfo.id` always
#: empty), so this verifies PipeWire's own current default instead.
EXPECTED_INPUT_NAME_SUBSTRING = "reSpeaker"
EXPECTED_OUTPUT_NAME_SUBSTRING = "UACDemoV1.0"

DEFAULT_URL = "ws://127.0.0.1:7880"
DEFAULT_API_KEY = "devkey"
DEFAULT_API_SECRET = "secret"


def _make_token(*, api_key: str, api_secret: str, identity: str, room: str) -> str:
    grants = api.VideoGrants(
        room_join=True, room=room, can_publish=True, can_subscribe=True
    )
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(grants)
        .to_jwt()
    )


def _verify_default_device(
    devices: list, expected_substring: str, *, role: str
) -> None:
    """Unchanged from R0082-C -- see that script's own docstring."""
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
            "specific device on this platform (AudioDeviceInfo.id is always "
            "empty; set_recording_device/set_playout_device require a real id) "
            "-- change the system default via `pactl set-default-source`/"
            "`set-default-sink` first, then retry."
        )
    print(f"  {role} default OK: {default_name!r}")


def _read_wav_validated(path: Path) -> tuple[bytes, int, float, str]:
    """Read `path` and REJECT it (SystemExit) unless it is already mono,
    16-bit (S16_LE), 48000Hz -- never silently resample/upmix/downmix at
    run time (per instruction: reject incompatible input rather than
    silently changing it during the run). Returns (pcm_bytes,
    n_samples, duration_s, sha256_hex)."""
    if not path.exists():
        raise SystemExit(f"--stimulus-wav not found: {path}")
    with wave.open(str(path), "rb") as wf:
        nch, sw, fr, nframes = (
            wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        )
        pcm = wf.readframes(nframes)
    if (nch, sw, fr) != (1, 2, SAMPLE_RATE):
        raise SystemExit(
            f"--stimulus-wav {path} is channels={nch} sampwidth={sw} "
            f"framerate={fr} -- REQUIRED: mono, 16-bit (S16_LE), {SAMPLE_RATE}Hz. "
            "Refusing to silently resample/convert at run time -- convert it "
            "once, offline, and point --stimulus-wav at the canonical result."
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    duration_s = nframes / fr
    return pcm, nframes, duration_s, digest


async def _play_signal(source: rtc.AudioSource, pcm: bytes) -> None:
    """Push `pcm` into `source` in 480-sample (10ms @ 48kHz) frames.
    Unchanged from R0082-C -- see that script's own docstring for the
    `frame.data` memoryview-format note (R0082-B finding)."""
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
        frame.data[:] = buf
        await source.capture_frame(frame)
        offset += chunk_samples


async def run_hardware_role(
    *,
    aec: bool,
    duration_s: float,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> None:
    """Unchanged in structure from R0082-C's own `run_hardware_role` --
    only `duration_s` now comes from the stimulus WAV's own header
    (read by the caller) instead of a `--duration` CLI flag. See
    `r0082c_platform_audio_room_aec_poc.py`'s own docstring for the
    full synchronization history."""
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
        track = rtc.LocalAudioTrack.create_audio_track("r0082d_hardware_mic", source)

        token = _make_token(
            api_key=api_key, api_secret=api_secret, identity=HARDWARE_IDENTITY, room=room_name
        )
        room = rtc.Room()
        test_track_ready = asyncio.Event()

        def _on_track_subscribed(track: rtc.Track, publication, participant) -> None:
            if participant.identity == TEST_IDENTITY and track.kind == rtc.TrackKind.KIND_AUDIO:
                test_track_ready.set()

        room.on("track_subscribed", _on_track_subscribed)

        try:
            print(f"[hardware pid={os.getpid()}] connecting to room {room_name!r}...")
            await room.connect(url, token)
            print(f"[hardware pid={os.getpid()}] connected, publishing mic track")
            await room.local_participant.publish_track(track)

            print(
                f"[hardware pid={os.getpid()}] waiting for test participant "
                "audio track subscription..."
            )
            await asyncio.wait_for(test_track_ready.wait(), timeout=SUBSCRIBE_TIMEOUT_S)
            print(f"[hardware pid={os.getpid()}] hardware subscribed to test audio track")

            hold_s = SETTLE_S + PRE_ROLL_S + duration_s + TAIL_S + CLEANUP_GRACE_S
            print(
                f"[hardware pid={os.getpid()}] holding room for {hold_s:.1f}s "
                f"(SETTLE={SETTLE_S}+PRE_ROLL={PRE_ROLL_S}+wav_duration={duration_s:.3f}"
                f"+TAIL={TAIL_S}+CLEANUP_GRACE={CLEANUP_GRACE_S})"
            )
            await asyncio.sleep(hold_s)
        finally:
            source.close()
            await room.disconnect()
            print(f"[hardware pid={os.getpid()}] disconnected cleanly")
    finally:
        platform_audio.close()
        print(f"[hardware pid={os.getpid()}] platform_audio closed, exiting")


async def run_test_role(
    *,
    signal_pcm: bytes,
    duration_s: float,
    total_s: float,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> dict:
    """Structurally unchanged from R0082-C's own `run_test_role` --
    plays the PROVIDED `signal_pcm` (the frozen speech WAV's exact
    bytes, read and validated by the caller) instead of calling
    `build_signal(...)`. Capture-completeness validation (R0082-C's own
    fix) is preserved unchanged: a short capture FAILS the run outright,
    never zero-padded or partially classified."""
    print(f"[test pid={os.getpid()}] starting")
    token = _make_token(
        api_key=api_key, api_secret=api_secret, identity=TEST_IDENTITY, room=room_name
    )
    room = rtc.Room()
    mic_track_ready = asyncio.Event()
    remote_mic_track: list[rtc.Track] = []

    def _on_track_subscribed(track: rtc.Track, publication, participant) -> None:
        if participant.identity == HARDWARE_IDENTITY and track.kind == rtc.TrackKind.KIND_AUDIO:
            remote_mic_track.append(track)
            mic_track_ready.set()

    room.on("track_subscribed", _on_track_subscribed)

    signal_source = rtc.AudioSource(SAMPLE_RATE, 1)
    signal_track = rtc.LocalAudioTrack.create_audio_track("r0082d_test_signal", signal_source)

    try:
        print(f"[test pid={os.getpid()}] connecting to room {room_name!r}...")
        await room.connect(url, token)
        print(f"[test pid={os.getpid()}] connected, publishing stimulus track")
        await room.local_participant.publish_track(signal_track)

        print(f"[test pid={os.getpid()}] waiting for hardware mic track subscription...")
        await asyncio.wait_for(mic_track_ready.wait(), timeout=SUBSCRIBE_TIMEOUT_S)
        print(f"[test pid={os.getpid()}] test subscribed to hardware mic")
        await asyncio.sleep(SETTLE_S)

        captured_chunks: list[bytes] = []
        mic_stream = rtc.AudioStream(remote_mic_track[0], sample_rate=SAMPLE_RATE, num_channels=1)

        async def _capture() -> None:
            deadline = time.monotonic() + total_s
            try:
                async for event in mic_stream:
                    captured_chunks.append(bytes(event.frame.data))
                    if time.monotonic() >= deadline:
                        break
            finally:
                await mic_stream.aclose()

        capture_task = asyncio.create_task(_capture())

        await asyncio.sleep(PRE_ROLL_S)
        print(f"[test pid={os.getpid()}] playing {duration_s:.3f}s speech stimulus")
        await _play_signal(signal_source, signal_pcm)
        await asyncio.sleep(TAIL_S)
        await capture_task
        print(f"[test pid={os.getpid()}] capture complete")

        captured = b"".join(captured_chunks)

        # R0082-C's own capture-completeness validation, unchanged.
        expected_total_s = PRE_ROLL_S + duration_s + TAIL_S
        expected_samples = int(expected_total_s * SAMPLE_RATE)
        expected_bytes = expected_samples * 2
        actual_samples = len(captured) // 2
        actual_bytes = len(captured)
        actual_duration_s = actual_samples / SAMPLE_RATE
        print(f"[test pid={os.getpid()}] capture completeness check:")
        print(
            f"  expected: {expected_samples} samples / {expected_bytes} bytes "
            f"/ {expected_total_s:.3f}s @ {SAMPLE_RATE}Hz mono S16_LE"
        )
        print(
            f"  actual:   {actual_samples} samples / {actual_bytes} bytes "
            f"/ {actual_duration_s:.3f}s"
        )
        if actual_samples < expected_samples:
            raise SystemExit(
                f"Capture INCOMPLETE: expected >= {expected_samples} samples "
                f"({expected_total_s:.3f}s @ {SAMPLE_RATE}Hz mono S16_LE), got "
                f"only {actual_samples} samples ({actual_duration_s:.3f}s). "
                "Refusing to compute AEC/residual metrics from a truncated "
                "recording, and refusing to pad missing data with zeros -- "
                "this run FAILS, not a valid (even partial) measurement."
            )

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
            if len(window) < window_samples * 2:
                raise SystemExit(
                    f"Window {i + 1} INCOMPLETE: expected {window_samples} "
                    f"samples, got only {len(window) // 2}. Refusing to "
                    "compute a partial-window RMS/peak value -- this run "
                    "FAILS."
                )
            windows_data.append({
                "window": i + 1,
                "window_start_s": round(i * WINDOW_S, 2),
                "window_end_s": round((i + 1) * WINDOW_S, 2),
                "mic_window_rms": round(_rms(window), 1),
                "mic_window_peak": _peak(window),
            })

        return {
            "quiet_before_rms": round(_rms(quiet_before), 1),
            "windows_data": windows_data,
            "captured_full": captured,
        }
    finally:
        await signal_source.aclose()
        await room.disconnect()
        print(f"[test pid={os.getpid()}] disconnected cleanly")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--role", choices=["hardware", "test"], required=True,
        help="Which participant this OS process runs. Launch each role as a "
             "SEPARATE python3 invocation -- see this module's own docstring.",
    )
    p.add_argument(
        "--aec", choices=["on", "off"], required=True,
        help="WebRTC AEC on/off. Functional for --role hardware "
             "(PlatformAudioOptions.echo_cancellation); label-only for "
             "--role test (used only in its own output WAV filenames).",
    )
    p.add_argument(
        "--stimulus-wav", required=True, type=Path,
        help="Path to the frozen speech stimulus WAV (mono, 16-bit, 48000Hz "
             "-- REJECTED, not converted, if it doesn't already match). "
             "Both roles read this SAME file: --role hardware reads only its "
             "header (for duration); --role test reads and publishes the "
             "full PCM.",
    )
    p.add_argument(
        "--room-name", required=True,
        help="Room name -- MUST be identical between the --role hardware and "
             "--role test invocations of ONE run, and unique per run across "
             "a multi-run sequence.",
    )
    p.add_argument("--url", default=DEFAULT_URL, help=f"LiveKit room URL (default {DEFAULT_URL}).")
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--api-secret", default=DEFAULT_API_SECRET)
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aec = args.aec == "on"

    signal_pcm, n_samples, duration_s, sha256_hex = _read_wav_validated(args.stimulus_wav)
    total_s = PRE_ROLL_S + duration_s + TAIL_S

    print(
        f"R0082-D PlatformAudio+room SPEECH AEC PoC (split-process) -- "
        f"role={args.role} run_id={run_id} aec={args.aec} room={args.room_name!r} "
        f"pid={os.getpid()}"
    )
    print(
        f"  stimulus-wav: {args.stimulus_wav}  n_samples={n_samples}  "
        f"duration={duration_s:.3f}s  sha256={sha256_hex}"
    )

    if args.role == "hardware":
        await run_hardware_role(
            aec=aec, duration_s=duration_s, url=args.url, api_key=args.api_key,
            api_secret=args.api_secret, room_name=args.room_name,
        )
        return 0

    result = await run_test_role(
        signal_pcm=signal_pcm, duration_s=duration_s, total_s=total_s,
        url=args.url, api_key=args.api_key, api_secret=args.api_secret,
        room_name=args.room_name,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"r0082d_aec{args.aec}_{run_id}_speech"
    signal_out = OUT_DIR / f"{prefix}_signal.wav"
    mic_out = OUT_DIR / f"{prefix}_mic.wav"
    _write_wav(signal_out, signal_pcm, sample_rate=SAMPLE_RATE)
    _write_wav(mic_out, result["captured_full"], sample_rate=SAMPLE_RATE)
    signal_out_sha256 = hashlib.sha256(signal_out.read_bytes()).hexdigest()
    print(
        f"  wrote signal evidence sha256={signal_out_sha256} "
        f"(matches frozen source: {signal_out_sha256 == sha256_hex})"
    )

    print(f"  quiet_before_rms = {result['quiet_before_rms']}")
    print(f"  {'window':>6}  {'start_s':>7}  {'end_s':>7}  {'rms':>8}  {'peak':>6}")
    rms_values = []
    for w in result["windows_data"]:
        print(
            f"  {w['window']:>6}  {w['window_start_s']:>7}  {w['window_end_s']:>7}  "
            f"{w['mic_window_rms']:>8}  {w['mic_window_peak']:>6}"
        )
        rms_values.append(w["mic_window_rms"])

    n = len(rms_values)
    overall_mean = sum(rms_values) / n
    first_half = rms_values[: n // 2]
    second_half = rms_values[n // 2 :]
    first_mean = sum(first_half) / len(first_half) if first_half else 0.0
    second_mean = sum(second_half) / len(second_half) if second_half else 0.0
    print(f"  overall mean     = {overall_mean:.2f}")
    print(f"  first-half mean  = {first_mean:.2f}")
    print(f"  second-half mean = {second_mean:.2f}")
    print(f"  min/max          = {min(rms_values):.2f} / {max(rms_values):.2f}")
    print(f"  max/min          = {max(rms_values) / min(rms_values):.2f}x")
    print("  NOTE: monitor CPU/RAM separately (e.g. `top`/`ps`) -- not measured here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
