#!/usr/bin/env python3
"""R0082-C — PlatformAudio + PipeWire + a REAL LiveKit room AEC PoC.

**No Gemini, no LLM, no STT, no TTS, no Memory, no NeXa Core, no Silero,
no barge-in logic, no `AecReferenceFeeder`.** Tests ONLY whether
LiveKit's `rtc.PlatformAudio` -- the PipeWire-backed WebRTC Audio Device
Module (ADM), NOT `MediaDevices`/PortAudio/direct-ALSA -- gives a more
STABLE AEC residual over 30-60s than R0082-B's `MediaDevices` path
(which confirmed real attenuation, ~-8dB, but did NOT eliminate a large
late-run rise, §12g/§20-24 of R0082's report).

## Why a real LiveKit room is required here (unlike R0082-B)

R0082-A's own source audit of `rtc.platform_audio.py` (installed
livekit 1.1.19) found `PlatformAudio` has NO local-loopback playout
API equivalent to `MediaDevices.open_output()` -- its own docstring's
"Automatic playout" feature only fires when a REMOTE track is
subscribed inside an actual `Room`. There is no way to feed
`PlatformAudio`'s AEC reverse/render reference without a real
publish+subscribe round trip through a room. This script therefore
runs TWO room participants -- in ONE process, ONE asyncio loop, so a
single operator command suffices:

  - "pi" role: `rtc.PlatformAudio()` -- real reSpeaker capture (mic),
    real UACDemoV1.0 playout (speaker), real WebRTC AEC/NS/AGC. Publishes
    its processed mic audio as a track; auto-subscribes to the "test"
    role's published stimulus track, which triggers PlatformAudio's own
    automatic playout -- this IS the AEC far-end reference path.
  - "test" role: a synthetic `rtc.AudioSource` (NOT PlatformAudio, no
    physical device) that publishes the SAME deterministic stimulus
    R0082-B used, and captures the "pi" role's subscribed mic track for
    analysis -- i.e. it records the Pi's mic AFTER the real
    PlatformAudio/WebRTC processing path, exactly like R0082-B's own
    mic capture did for `MediaDevices`.

Both participants join the SAME local LiveKit room (a local, dev-mode
`livekit-server` -- see this module's own docstring below for exactly
how R0082-C confirmed one works on this machine; no external server or
account needed).

## Device selection -- NOT via `set_recording_device`/`set_playout_device`

R0082-A already found `AudioDeviceInfo.id` is always an empty string on
this Linux/PipeWire system. R0082-C's own audit (this round) found
`PlatformAudio.set_recording_device()`/`set_playout_device()` require a
real, non-empty `id` (a raw empty-string call silently no-ops; a NAME
passed instead of an id raises `PlatformAudioError: Device not found`)
-- so neither device can be explicitly selected via this SDK version on
this platform. This script does NOT attempt to. Instead, it relies on
PipeWire's own default sink/source (confirmed via `pactl info` to
already be exactly UACDemoV1.0 / reSpeaker XVF3800 on this machine) and
defensively VERIFIES this at startup against `recording_devices()`/
`playout_devices()`'s own `"default: "`-prefixed entries, refusing to
proceed with a clear error if the system default ever changes to
something else.

## Local LiveKit server -- confirmed working, not bundled with this script

No `livekit-server` binary or Docker was present on this machine
(R0082-C audit, this round). The official single static Go binary
(Apache-2.0, `github.com/livekit/livekit`, v1.13.7 `linux_arm64`) was
downloaded into this session's own scratchpad (NOT this repo, NOT
system-wide) and confirmed working in `--dev` mode (auto-generated
`devkey`/`secret` credentials, binds `127.0.0.1` only, no Redis/config
file needed) -- a real synthetic room connect/publish/disconnect round
trip against it was confirmed clean before this script was written.
Start it yourself before running this script:

    <path-to-livekit-server>/livekit-server --dev --bind 127.0.0.1

This script's defaults (`--url ws://127.0.0.1:7880 --api-key devkey
--api-secret secret`) match that dev-mode server exactly.

## Reused, not reimplemented

`build_signal`/`_rms`/`_peak`/`_write_wav` are imported from this
directory's own standard-library-only `r0082_audio_utils.py` (R0082-B's
own isolation fix) -- NOT re-copied here, NOT imported from R0081's
scripts. See `r0082_audio_utils.py`'s own docstring for why.

## Environment -- same isolated venv as R0082-B, NOT NeXa's own .venv

This script imports only `r0082_audio_utils` (stdlib-only) and
`livekit`/`livekit.api` -- never `nexa.*`, `pipecat.*`, `google.*`, or
`loguru`. `sounddevice` is NOT required by this script (PlatformAudio
does not use PortAudio) even though it is present in the shared probe
venv from R0082-B.

## Usage (do not run against real hardware without reading R0082's own
## report for the full protocol and this round's device-selection
## caveat above)

    # in a separate terminal, start the local dev room server:
    <scratchpad>/livekit_server/livekit-server --dev --bind 127.0.0.1

    # then, in the isolated probe venv:
    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \\
        --aec off --duration 30 --stimulus tones

    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \\
        --aec on --duration 30 --stimulus tones

Run ``--aec off`` then ``--aec on`` back to back, same physical volume/
room/device positions -- the same discipline R0081/R0082-B established.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r0082_audio_utils import (  # noqa: E402  (local, stdlib-only)
    _peak,
    _rms,
    _write_wav,
    build_signal,
)

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
WINDOW_S = 3.0
PRE_ROLL_S = 1.0
TAIL_S = 1.0

OUT_DIR = Path(__file__).resolve().parent / "r0082c_aec_captures"

#: R0082-C -- `set_recording_device`/`set_playout_device` are NOT used
#: (see this module's own docstring: broken on this platform for this
#: SDK version, empty `id` field). These substrings are used only to
#: VERIFY PipeWire's own default input/output already match our target
#: physical devices, refusing to proceed otherwise.
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
    """R0082-C -- refuse to proceed unless PipeWire's own CURRENT default
    device (the `index=0`, `"default: "`-prefixed entry -- confirmed
    this round to be the only reliable selector, since explicit
    `set_recording_device`/`set_playout_device` cannot target a specific
    device on this platform/SDK version) already matches the expected
    physical device. Raises SystemExit with a clear diagnostic rather
    than silently capturing/playing through the wrong device."""
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
            f"NOT contain {expected_substring!r}. R0082-C cannot force-select a "
            "specific device on this platform (AudioDeviceInfo.id is always "
            "empty; set_recording_device/set_playout_device require a real id) "
            "-- change the system default via `pactl set-default-source`/"
            "`set-default-sink` first, then retry."
        )
    print(f"  {role} default OK: {default_name!r}")


async def _run_pi_role(
    *,
    aec: bool,
    total_s: float,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
    connected: asyncio.Event,
) -> None:
    """The Pi hardware participant: real `PlatformAudio` capture
    (reSpeaker) + automatic playout (UACDemoV1.0) via room-track
    subscription. Publishes its processed mic audio; does not itself
    analyze anything (the "test" role captures and analyzes the
    subscribed copy of this track, exactly mirroring what a REAL remote
    listener -- e.g. a future Gemini Live leg -- would receive)."""
    platform_audio = rtc.PlatformAudio()
    try:
        recs = platform_audio.recording_devices()
        plays = platform_audio.playout_devices()
        print("  pi role -- recording_devices():")
        for d in recs:
            print(f"    index={d.index} name={d.name!r}")
        print("  pi role -- playout_devices():")
        for d in plays:
            print(f"    index={d.index} name={d.name!r}")
        _verify_default_device(recs, EXPECTED_INPUT_NAME_SUBSTRING, role="input")
        _verify_default_device(plays, EXPECTED_OUTPUT_NAME_SUBSTRING, role="output")

        options = rtc.PlatformAudioOptions(
            echo_cancellation=aec, noise_suppression=False, auto_gain_control=False
        )
        source = platform_audio.create_audio_source(options)
        track = rtc.LocalAudioTrack.create_audio_track("r0082c_pi_mic", source)

        token = _make_token(
            api_key=api_key, api_secret=api_secret, identity="r0082c_pi", room=room_name
        )
        room = rtc.Room()
        try:
            await room.connect(url, token)
            await room.local_participant.publish_track(track)
            connected.set()
            await asyncio.sleep(total_s + 2.0)
        finally:
            source.close()
            await room.disconnect()
    finally:
        platform_audio.close()


async def _run_test_role(
    *,
    stimulus: str,
    duration_s: float,
    amplitude: float,
    total_s: float,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> dict:
    """The deterministic test participant: synthetic `AudioSource`
    (NOT PlatformAudio -- no physical device), publishes the same R0081/
    R0082-B stimulus, auto-subscribes to and captures the Pi role's mic
    track, then runs the same windowed RMS/peak analysis R0082-B used."""
    token = _make_token(
        api_key=api_key, api_secret=api_secret, identity="r0082c_test", room=room_name
    )
    room = rtc.Room()
    mic_track_ready = asyncio.Event()
    remote_mic_track: list[rtc.Track] = []

    def _on_track_subscribed(track: rtc.Track, publication, participant) -> None:
        if participant.identity == "r0082c_pi" and track.kind == rtc.TrackKind.KIND_AUDIO:
            remote_mic_track.append(track)
            mic_track_ready.set()

    room.on("track_subscribed", _on_track_subscribed)

    signal_source = rtc.AudioSource(SAMPLE_RATE, 1)
    signal_track = rtc.LocalAudioTrack.create_audio_track("r0082c_test_signal", signal_source)

    try:
        await room.connect(url, token)
        await room.local_participant.publish_track(signal_track)

        await asyncio.wait_for(mic_track_ready.wait(), timeout=15.0)
        # Settle: let the Pi role's auto-subscribe + PlatformAudio automatic
        # playout pipeline actually start rendering before we start counting
        # pre-roll -- avoids capturing a cold-start transient as "quiet_before".
        await asyncio.sleep(1.0)

        signal_pcm = build_signal(
            stimulus, duration_s=duration_s, amplitude=amplitude, sample_rate=SAMPLE_RATE
        )

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

        import array

        FRAME_SAMPLES = 480

        async def _play() -> None:
            total_samples = len(signal_pcm) // 2
            offset = 0
            while offset < total_samples:
                chunk_samples = min(FRAME_SAMPLES, total_samples - offset)
                frame = rtc.AudioFrame.create(SAMPLE_RATE, 1, FRAME_SAMPLES)
                buf = array.array("h", frame.data)
                chunk = array.array("h")
                chunk.frombytes(signal_pcm[offset * 2 : (offset + chunk_samples) * 2])
                for i in range(FRAME_SAMPLES):
                    buf[i] = chunk[i] if i < chunk_samples else 0
                frame.data[:] = buf
                await signal_source.capture_frame(frame)
                offset += chunk_samples

        await asyncio.sleep(PRE_ROLL_S)
        await _play()
        await asyncio.sleep(TAIL_S)
        await capture_task

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
            "quiet_before_rms": round(_rms(quiet_before), 1),
            "windows_data": windows_data,
            "captured_full": captured,
            "signal_pcm": signal_pcm,
        }
    finally:
        # R0082-C -- confirmed from installed source: `rtc.AudioSource`
        # (synthetic, used here) exposes an ASYNC `aclose()`, unlike
        # `PlatformAudioSource` (used by the "pi" role), which exposes a
        # SYNC `close()`. A first dry-run validation of this exact
        # cleanup path found `FfiHandle` AssertionErrors on a successful
        # run because an earlier version of this code looked for `close`
        # (absent on `AudioSource`) instead of `aclose` -- the source
        # was never actually released. Confirmed fixed by calling the
        # correct method.
        await signal_source.aclose()
        await room.disconnect()


async def run_poc(
    *,
    aec: bool,
    duration_s: float,
    stimulus: str,
    amplitude: float,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
) -> dict:
    total_s = PRE_ROLL_S + duration_s + TAIL_S
    pi_connected = asyncio.Event()

    pi_coro = _run_pi_role(
        aec=aec, total_s=total_s, url=url, api_key=api_key, api_secret=api_secret,
        room_name=room_name, connected=pi_connected,
    )
    test_coro = _run_test_role(
        stimulus=stimulus, duration_s=duration_s, amplitude=amplitude, total_s=total_s,
        url=url, api_key=api_key, api_secret=api_secret, room_name=room_name,
    )

    pi_task = asyncio.create_task(pi_coro)
    test_task = asyncio.create_task(test_coro)
    _, result = await asyncio.gather(pi_task, test_task)
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--aec", choices=["on", "off"], required=True,
        help="WebRTC AEC on/off (PlatformAudioOptions.echo_cancellation).",
    )
    p.add_argument(
        "--duration", type=float, default=30.0,
        help="Test duration in seconds (default 30).",
    )
    p.add_argument(
        "--stimulus", choices=["tones", "mls", "stationary_multitone"], default="tones",
        help=(
            "'tones'/'mls' reused from R0081/R0082-B (default 'tones'). "
            "'stationary_multitone' (R0082-C): 500+1000+2000Hz mixed "
            "simultaneously for the full duration, no segment transitions "
            "-- removes the frequency-content confound R0082-B's WAV "
            "sanity check found in 'tones'."
        ),
    )
    p.add_argument(
        "--amplitude", type=float, default=0.05,
        help="Peak amplitude (default 0.05, matches R0081/R0082-B).",
    )
    p.add_argument("--url", default=DEFAULT_URL, help=f"LiveKit room URL (default {DEFAULT_URL}).")
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--api-secret", default=DEFAULT_API_SECRET)
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    room_name = f"r0082c_{run_id}"
    aec = args.aec == "on"

    print(f"R0082-C PlatformAudio+room AEC PoC -- run_id={run_id} aec={args.aec}")
    print(f"  room: {room_name}  url: {args.url}")

    result = await run_poc(
        aec=aec, duration_s=args.duration, stimulus=args.stimulus, amplitude=args.amplitude,
        url=args.url, api_key=args.api_key, api_secret=args.api_secret, room_name=room_name,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"r0082c_aec{args.aec}_{run_id}_{args.stimulus}"
    _write_wav(OUT_DIR / f"{prefix}_signal.wav", result["signal_pcm"], sample_rate=SAMPLE_RATE)
    _write_wav(OUT_DIR / f"{prefix}_mic.wav", result["captured_full"], sample_rate=SAMPLE_RATE)

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
    first_mean = sum(first_half) / len(first_half)
    second_mean = sum(second_half) / len(second_half)
    print(f"  overall mean     = {overall_mean:.2f}")
    print(f"  first-half mean  = {first_mean:.2f}")
    print(f"  second-half mean = {second_mean:.2f}")
    print(f"  min/max          = {min(rms_values):.2f} / {max(rms_values):.2f}")
    print(f"  max/min          = {max(rms_values) / min(rms_values):.2f}x")
    print("  NOTE: monitor CPU/RAM separately (e.g. `top`/`ps`) -- not measured here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
