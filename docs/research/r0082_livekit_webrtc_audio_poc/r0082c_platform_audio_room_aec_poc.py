#!/usr/bin/env python3
"""R0082-C — PlatformAudio + PipeWire + a REAL LiveKit room AEC PoC.

**No Gemini, no LLM, no STT, no TTS, no Memory, no NeXa Core, no Silero,
no barge-in logic, no `AecReferenceFeeder`.** Tests ONLY whether
LiveKit's `rtc.PlatformAudio` -- the PipeWire-backed WebRTC Audio Device
Module (ADM), NOT `MediaDevices`/PortAudio/direct-ALSA -- gives a more
STABLE AEC residual over 30-60s than R0082-B's `MediaDevices` path
(which confirmed real attenuation, ~-8dB, but did NOT eliminate a large
late-run rise, §12g/§20-24 of R0082's report).

## SPLIT-PROCESS TOPOLOGY (this version) -- why

The FIRST real `PlatformAudio` hardware attempt (run id
`20260918T132003Z`) reached real hardware -- both devices were
discovered and verified correctly -- then the process aborted inside
native WebRTC:

    Fatal error in: ../audio/audio_send_stream.cc, line 404
    Check failed: !race_checker404.RaceDetected()

That build ran BOTH participants ("hardware" role with real
`PlatformAudio`, "test" role with a synthetic `AudioSource`) as two
`rtc.Room()` objects inside ONE Python process / ONE asyncio loop. A
single-process/two-`Room`/two-`PeerConnection` concurrency structure is
the HIGH-PRIORITY HYPOTHESIS for this crash -- NOT a confirmed root
cause. It was not an AEC result, not a reSpeaker sample-rate failure,
not a PipeWire default-device failure, and not a Python exception --
device discovery and room join both fully succeeded before the native
abort (see R0082's own report for the full classification).

Per instruction, this version does NOT use Python `multiprocessing`
(there is a currently open upstream LiveKit Python SDK issue reporting
SIGABRT/FFI callback crashes with multiprocessing's PROCESS mode on
Linux) and does NOT casually version-hop `livekit` away from the
already-confirmed-compatible `1.1.19`. Instead, the two participants now
run as **two genuinely separate, independently exec'd OS processes**,
each with its own Python interpreter, its own `rtc.Room()`, and its own
LiveKit FFI/runtime state -- no shared `Room`, no shared `PlatformAudio`,
no `fork()`-based worker model. Launch with `--role hardware` in one
terminal/process and `--role test` in another, sharing only a
`--room-name` (and the room server URL/credentials) as coordination.

A short real-hardware preflight (no deterministic stimulus played)
confirmed the split-process topology does NOT reproduce
`RaceDetected()` -- evidence FOR, not proof of, the concurrency
hypothesis above. That preflight also surfaced a SEPARATE, real
synchronization/lifetime defect (now fixed, see `run_hardware_role`'s
own docstring): the hardware role originally held the room open via a
fixed `total_s + 2.0` sleep started immediately after publish, with no
tie to when the test role actually began or finished capturing --
timing math showed a test role capture window of ~33s starting only
AFTER it detects the hardware mic subscription, so even a modest
operator launch-timing gap could let the hardware role disconnect
mid-capture and silently truncate the recording. Fixed by having the
hardware role wait (bounded) for the TEST role's own track to be
subscribed before starting its measurement-lifetime hold, and by adding
explicit capture-completeness validation on the test role's side
(`run_test_role` FAILS the run outright, rather than analyzing or
zero-padding a short recording).

## Why a real LiveKit room is required here (unlike R0082-B)

R0082-A's own source audit of `rtc.platform_audio.py` (installed
livekit 1.1.19) found `PlatformAudio` has NO local-loopback playout
API equivalent to `MediaDevices.open_output()` -- its own docstring's
"Automatic playout" feature only fires when a REMOTE track is
subscribed inside an actual `Room`. There is no way to feed
`PlatformAudio`'s AEC reverse/render reference without a real
publish+subscribe round trip through a room.

  - `--role hardware`: real `rtc.PlatformAudio()` -- real reSpeaker
    capture (mic), real UACDemoV1.0 playout (speaker), real WebRTC
    AEC/NS/AGC. Publishes its processed mic audio as a track;
    auto-subscribes to the test process's published stimulus track,
    which triggers PlatformAudio's own automatic playout -- this IS the
    AEC far-end reference path.
  - `--role test`: a synthetic `rtc.AudioSource` (NOT PlatformAudio, no
    physical device) that publishes the SAME deterministic stimulus
    R0082-B used, and captures the hardware process's subscribed mic
    track for analysis -- i.e. it records the mic AFTER the real
    PlatformAudio/WebRTC processing path, exactly like R0082-B's own
    mic capture did for `MediaDevices`.

Both processes join the SAME local LiveKit room (a local, dev-mode
`livekit-server` -- see this module's own docstring below; no external
server or account needed).

## Device selection -- NOT via `set_recording_device`/`set_playout_device`

R0082-A already found `AudioDeviceInfo.id` is always an empty string on
this Linux/PipeWire system. R0082-C's own audit found
`PlatformAudio.set_recording_device()`/`set_playout_device()` require a
real, non-empty `id` (a raw empty-string call silently no-ops; a NAME
passed instead of an id raises `PlatformAudioError: Device not found`)
-- so neither device can be explicitly selected via this SDK version on
this platform. This script does NOT attempt to. Instead, the hardware
role relies on PipeWire's own default sink/source (confirmed via
`pactl info` to already be exactly UACDemoV1.0 / reSpeaker XVF3800 on
this machine) and defensively VERIFIES this at startup against
`recording_devices()`/`playout_devices()`'s own `"default: "`-prefixed
entries, refusing to proceed with a clear error if the system default
ever changes to something else.

## Local LiveKit server -- confirmed working, not bundled with this script

No `livekit-server` binary or Docker was present on this machine
(R0082-C audit). The official single static Go binary (Apache-2.0,
`github.com/livekit/livekit`, v1.13.7 `linux_arm64`) was downloaded into
this session's own scratchpad (NOT this repo, NOT system-wide) and
confirmed working in `--dev` mode (auto-generated `devkey`/`secret`
credentials, binds `127.0.0.1` only, no Redis/config file needed).
Start it yourself before running either role:

    <path-to-livekit-server>/livekit-server --dev --bind 127.0.0.1

This script's defaults (`--url ws://127.0.0.1:7880 --api-key devkey
--api-secret secret`) match that dev-mode server exactly. The dev
token's `InsecureKeyLengthWarning` (6-byte HMAC key) is expected for
this local placeholder credential and is unrelated to the
`RaceDetected()` abort above -- recorded, not chased as a cause.

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

## Usage -- TWO separate terminals/processes, a SHARED --room-name

    # terminal 1: local dev room server (start once)
    <scratchpad>/livekit_server/livekit-server --dev --bind 127.0.0.1

    # terminal 2: hardware participant (real PlatformAudio)
    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \\
        --role hardware --aec off --room-name r0082c_run1

    # terminal 3: test participant (synthetic, publishes stimulus + records mic)
    <isolated-venv>/bin/python3 \\
        docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \\
        --role test --aec off --duration 30 --stimulus stationary_multitone --room-name r0082c_run1

`--room-name` MUST match between the two invocations. `--aec` is
required for BOTH roles: for `--role hardware` it functionally sets
`PlatformAudioOptions.echo_cancellation`; for `--role test` it does
nothing functionally (the test role never touches AEC) but labels its
own output WAV filenames so evidence from a coordinated pair of runs
stays identifiable. Start the hardware role slightly before (or at the
same time as) the test role -- the test role waits (bounded timeout) for
the hardware role's track to appear via `track_subscribed`; the hardware
role waits (bounded timeout) similarly for the test role's stimulus
track before its own room join is considered complete, so either start
order works within the timeout window.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import os
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
FRAME_SAMPLES = 480
WINDOW_S = 3.0
PRE_ROLL_S = 1.0
TAIL_S = 1.0

#: R0082-C -- named timing constants for cross-process coordination
#: (replaces an earlier unexplained `total_s + 2.0` magic-number sleep on
#: the hardware role, which assumed a fixed operator launch-timing gap
#: instead of synchronizing to an actual event -- found to be unsafe:
#: even a small launch delay could let the hardware process disconnect
#: before the test process finished its own capture window).
#:
#: SETTLE_S: matches the test role's own post-subscribe settle sleep
#: (lets the automatic-playout pipeline actually start rendering before
#: pre-roll is counted).
#: SUBSCRIBE_TIMEOUT_S: bounded wait for the OTHER participant's track to
#: be subscribed -- used by BOTH roles, so neither depends on the other
#: having started first or on any assumed wall-clock launch gap.
#: CLEANUP_GRACE_S: extra time the hardware role stays up after its
#: computed measurement lifetime, so the test role's own `finally`
#: cleanup (aclose/disconnect) has room to complete before the hardware
#: role tears down PlatformAudio.
SETTLE_S = 1.0
SUBSCRIBE_TIMEOUT_S = 30.0
CLEANUP_GRACE_S = 3.0

OUT_DIR = Path(__file__).resolve().parent / "r0082c_aec_captures"

HARDWARE_IDENTITY = "r0082c_hardware"
TEST_IDENTITY = "r0082c_test"

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
    to be the only reliable selector, since explicit
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


async def _play_signal(source: rtc.AudioSource, pcm: bytes) -> None:
    """Push `pcm` into `source` in 480-sample (10ms @ 48kHz) frames.
    `frame.data` is an 'h'-format (int16) memoryview -- assigning an
    `array.array('h', ...)` directly is format-compatible (confirmed
    empirically, R0082-B); assigning `.tobytes()` is NOT."""
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
    """Separate-OS-process hardware participant: real `PlatformAudio`
    capture (reSpeaker) + automatic playout (UACDemoV1.0) via room-track
    subscription. Publishes its processed mic audio; does not itself
    analyze anything (the test process captures and analyzes the
    subscribed copy of this track, exactly mirroring what a REAL remote
    listener -- e.g. a future Gemini Live leg -- would receive). Runs
    entirely in its own process/interpreter -- no other `Room` or
    `PlatformAudio` object exists anywhere in this process.

    R0082-C (this round) -- does NOT hold the room for a fixed
    `total_s + <magic number>` sleep started right after publish (found
    unsafe: nothing tied that sleep to when the test process actually
    started capturing, so a launch-timing gap of only a few seconds
    could truncate the test role's capture). Instead, waits (bounded,
    `SUBSCRIBE_TIMEOUT_S`) for the TEST participant's own audio track to
    be subscribed -- the same class of event-based sync the test role
    already used for the hardware mic track -- before starting its own
    measurement-lifetime hold. This makes operator launch ORDER/timing
    between the two processes irrelevant within the timeout window."""
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
        track = rtc.LocalAudioTrack.create_audio_track("r0082c_hardware_mic", source)

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
                f"(SETTLE={SETTLE_S}+PRE_ROLL={PRE_ROLL_S}+duration={duration_s}"
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
    stimulus: str,
    duration_s: float,
    amplitude: float,
    total_s: float,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
    play_stimulus: bool = True,
) -> dict:
    """Separate-OS-process deterministic test participant: synthetic
    `AudioSource` (NOT PlatformAudio -- no physical device), publishes
    the requested stimulus, auto-subscribes to and captures the hardware
    process's mic track, then runs the same windowed RMS/peak analysis
    R0082-B used. `play_stimulus=False` is used only by the short
    pre-hardware "hold the room alive" preflight (per the correction
    request's required sequence) -- publishes a track but sends no
    signal PCM, so the room/subscribe path can be validated without any
    deterministic playback yet."""
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
    signal_track = rtc.LocalAudioTrack.create_audio_track("r0082c_test_signal", signal_source)

    try:
        print(f"[test pid={os.getpid()}] connecting to room {room_name!r}...")
        await room.connect(url, token)
        print(f"[test pid={os.getpid()}] connected, publishing stimulus track")
        await room.local_participant.publish_track(signal_track)

        print(f"[test pid={os.getpid()}] waiting for hardware mic track subscription...")
        await asyncio.wait_for(mic_track_ready.wait(), timeout=SUBSCRIBE_TIMEOUT_S)
        print(f"[test pid={os.getpid()}] test subscribed to hardware mic")
        # Settle: let the hardware process's auto-subscribe + PlatformAudio
        # automatic playout pipeline actually start rendering before we
        # start counting pre-roll -- avoids capturing a cold-start
        # transient as "quiet_before".
        await asyncio.sleep(SETTLE_S)

        if not play_stimulus:
            print(f"[test pid={os.getpid()}] play_stimulus=False -- holding room, no signal sent")
            await asyncio.sleep(total_s)
            return {}

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

        await asyncio.sleep(PRE_ROLL_S)
        print(f"[test pid={os.getpid()}] playing {duration_s:.1f}s stimulus ({stimulus})")
        await _play_signal(signal_source, signal_pcm)
        await asyncio.sleep(TAIL_S)
        await capture_task
        print(f"[test pid={os.getpid()}] capture complete")

        captured = b"".join(captured_chunks)

        # R0082-C -- capture completeness validation. Must NOT silently
        # analyze a truncated recording (e.g. if the hardware process
        # disconnected early, or the capture stream stalled) -- a short
        # capture must FAIL the run, not produce a misleadingly "valid"
        # AEC/stability classification from partial data.
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
                "Refusing to compute AEC/stability metrics from a truncated "
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
            "signal_pcm": signal_pcm,
        }
    finally:
        # R0082-C -- confirmed from installed source: `rtc.AudioSource`
        # (synthetic, used here) exposes an ASYNC `aclose()`, unlike
        # `PlatformAudioSource` (used by the hardware role), which
        # exposes a SYNC `close()`. A dry-run validation of this exact
        # cleanup path once found `FfiHandle` AssertionErrors on a
        # successful run because an earlier draft looked for `close`
        # (absent on `AudioSource`) instead of `aclose` -- fixed by
        # calling the correct method (kept here).
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
        "--duration", type=float, default=30.0,
        help="Test duration in seconds (default 30). Both roles must be given "
             "the SAME value.",
    )
    p.add_argument(
        "--stimulus", choices=["tones", "mls", "stationary_multitone"], default="tones",
        help=(
            "'tones'/'mls' reused from R0081/R0082-B (default 'tones'). "
            "'stationary_multitone' (R0082-C): 500+1000+2000Hz mixed "
            "simultaneously for the full duration, no segment transitions "
            "-- removes the frequency-content confound R0082-B's WAV "
            "sanity check found in 'tones'. Only used by --role test."
        ),
    )
    p.add_argument(
        "--amplitude", type=float, default=0.05,
        help="Peak amplitude (default 0.05, matches R0081/R0082-B). Only "
             "used by --role test.",
    )
    p.add_argument(
        "--room-name", required=True,
        help="Room name -- MUST be identical between the --role hardware and "
             "--role test invocations of a single coordinated run.",
    )
    p.add_argument(
        "--no-stimulus", action="store_true",
        help="--role test only: join and hold the room without playing any "
             "signal PCM -- used for the pre-hardware room-mechanics preflight.",
    )
    p.add_argument("--url", default=DEFAULT_URL, help=f"LiveKit room URL (default {DEFAULT_URL}).")
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--api-secret", default=DEFAULT_API_SECRET)
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aec = args.aec == "on"
    total_s = PRE_ROLL_S + args.duration + TAIL_S

    print(
        f"R0082-C PlatformAudio+room AEC PoC (split-process) -- role={args.role} "
        f"run_id={run_id} aec={args.aec} room={args.room_name!r} pid={os.getpid()}"
    )

    if args.role == "hardware":
        await run_hardware_role(
            aec=aec, duration_s=args.duration, url=args.url, api_key=args.api_key,
            api_secret=args.api_secret, room_name=args.room_name,
        )
        return 0

    result = await run_test_role(
        stimulus=args.stimulus, duration_s=args.duration, amplitude=args.amplitude,
        total_s=total_s, url=args.url, api_key=args.api_key, api_secret=args.api_secret,
        room_name=args.room_name, play_stimulus=not args.no_stimulus,
    )

    if not result:
        print(f"[test pid={os.getpid()}] preflight complete (no stimulus played, no WAV written)")
        return 0

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
