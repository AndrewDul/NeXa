#!/usr/bin/env python3
"""NeXa M2.1 voice probe — manual hardware acceptance harness.

Thin wiring/presentation only (`apps/README.md`) — no product logic here.
Drives the real `nexa.voice.VoiceRuntime` (local mic -> Pipecat -> Silero VAD)
against the real reSpeaker XVF3800. This is NOT speech recognition — it only
proves speech-activity / turn-boundary detection (ADR-0003 M2.1 scope). STT,
the ConversationSession adapter, and TTS are later substages (M2.2+).

Usage:
    python3 apps/nexa_voice_probe.py
Speak naturally; watch the state transitions. Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: E402

from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.runtime import DEFAULT_VAD_PARAMS  # noqa: E402
from nexa.voice.state import VoiceEvent  # noqa: E402

_last_event_monotonic: float | None = None


def on_event(event: VoiceEvent) -> None:
    global _last_event_monotonic
    now = time.monotonic()
    wall = event.at.astimezone().strftime("%H:%M:%S.%f")[:-3]
    if _last_event_monotonic is None:
        delta = "  (start)"
    else:
        delta = f"+{(now - _last_event_monotonic) * 1000:7.0f}ms"
    _last_event_monotonic = now
    print(f"{wall}  {delta}  state: {event.to_state.value.upper():14s} ({event.reason})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stop-secs",
        type=float,
        default=DEFAULT_VAD_PARAMS.stop_secs,
        help=f"VAD silence duration required to confirm end-of-turn "
        f"(default: {DEFAULT_VAD_PARAMS.stop_secs}s, the NeXa-tuned value — "
        f"see nexa.voice.runtime.DEFAULT_VAD_PARAMS)",
    )
    parser.add_argument(
        "--start-secs",
        type=float,
        default=DEFAULT_VAD_PARAMS.start_secs,
        help=f"VAD speech duration required to confirm start "
        f"(default: {DEFAULT_VAD_PARAMS.start_secs}s, unchanged library default)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    vad_params = VADParams(start_secs=args.start_secs, stop_secs=args.stop_secs)
    runtime = VoiceRuntime(LocalAudioConfig(), vad_params=vad_params, on_event=on_event)

    print("NeXa M2.1 Voice Probe")
    print(f"(input/output device: {runtime.config.input_device_name!r}, "
          f"{runtime.config.sample_rate} Hz, {runtime.config.channels}ch mono)")
    print(f"(VAD: start_secs={vad_params.start_secs}, stop_secs={vad_params.stop_secs})")
    print("Speak naturally. Ctrl+C to stop.\n")

    await runtime.run()

    print("\nfinal state:", runtime.state_machine.state.value.upper())
    print(f"{len(runtime.state_machine.history)} transitions recorded this session.")


if __name__ == "__main__":
    asyncio.run(main())
