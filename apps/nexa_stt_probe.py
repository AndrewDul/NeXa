#!/usr/bin/env python3
"""NeXa M2.2 STT probe — manual hardware acceptance harness.

Thin wiring/presentation only (`apps/README.md`) — no product logic here.
Drives the real `nexa.voice.VoiceRuntime` (local mic -> Pipecat -> Silero VAD
-> pre-roll utterance capture -> `WhisperCppTranscriber`) against the real
reSpeaker XVF3800. No LLM response, no ConversationSession, no TTS — this
proves local speech-to-text only (ADR-0003 M2.2 scope).

Language is always explicit — there is no `--language auto` (R0006 measured
auto-detection misclassifying a short Polish utterance as Japanese).

Usage:
    python3 apps/nexa_stt_probe.py --language pl
    python3 apps/nexa_stt_probe.py --language en
Speak naturally; watch the state transitions and transcription. Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.stt import (  # noqa: E402
    Language,
    SttBinaryNotFoundError,
    SttModelNotFoundError,
    TranscriptionResult,
    WhisperCppTranscriber,
)
from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402


def on_event(event: VoiceEvent) -> None:
    # Every real VoiceStateMachine transition is printed as it happens, in
    # order, with no suppression or reordering. STT runs independently in
    # the background (SerialTranscriptionQueue) and can still be
    # transcribing a previous utterance when a new USER_SPEAKING/END_OF_TURN
    # arrives — printing state here unconditionally is the only way this
    # display cannot lie about what state the machine is actually in.
    print(f"state: {event.to_state.value.upper()}")
    if event.to_state == VoiceState.END_OF_TURN:
        print("(stt: transcribing — not a VoiceState, just this probe's own status)")


def make_transcription_handlers(runtime_ref: list[VoiceRuntime]):
    """Closures over a 1-element list holding the not-yet-constructed
    `VoiceRuntime` (its callbacks must be passed into its own constructor)
    so every transcription/error print also reports the live
    max-observed-STT-concurrency proof — the concurrency fix's correctness
    must be visible in this probe's output, not something the operator has
    to infer from timestamps."""

    def on_transcription(result: TranscriptionResult) -> None:
        # STT-specific output only — never a voice state, which may already
        # have moved on (e.g. to USER_SPEAKING for the *next* utterance) by
        # the time this callback fires.
        max_concurrency = runtime_ref[0].max_observed_stt_concurrency
        print(f'  text: "{result.text}"')
        print(f"  stt latency: {result.wall_latency_s:.1f} s")
        print(f"  max concurrent STT executions so far: {max_concurrency}")

    def on_transcription_error(exc: Exception) -> None:
        max_concurrency = runtime_ref[0].max_observed_stt_concurrency
        print(f"  transcription error: {exc}")
        print(f"  max concurrent STT executions so far: {max_concurrency}")

    return on_transcription, on_transcription_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--language",
        choices=[lang.value for lang in Language],
        default=Language.PL.value,
        help="explicit STT language hint (no auto-detect) — default: pl",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    language = Language(args.language)

    try:
        transcriber = WhisperCppTranscriber()
    except (SttBinaryNotFoundError, SttModelNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    runtime_ref: list[VoiceRuntime] = []
    on_transcription, on_transcription_error = make_transcription_handlers(runtime_ref)
    runtime = VoiceRuntime(
        LocalAudioConfig(),
        on_event=on_event,
        transcriber=transcriber,
        language=language,
        on_transcription=on_transcription,
        on_transcription_error=on_transcription_error,
    )
    runtime_ref.append(runtime)

    print("NeXa M2.2 STT Probe")
    print(f"language: {language.value}")
    print()
    print("Speak naturally. Ctrl+C to stop.\n")

    await runtime.run()

    print("\nfinal state:", runtime.state_machine.state.value.upper())
    print(f"max concurrent STT executions observed this session: "
          f"{runtime.max_observed_stt_concurrency}")


if __name__ == "__main__":
    asyncio.run(main())
