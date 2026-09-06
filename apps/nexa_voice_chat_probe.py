#!/usr/bin/env python3
"""NeXa M2.3 voice-conversation probe — manual hardware acceptance harness.

Thin wiring/presentation only (`apps/README.md`) — no product logic here.
Drives the real path: local mic -> Pipecat -> Silero VAD -> pre-roll
utterance capture -> `WhisperCppTranscriber` -> `VoiceConversationAdapter`
-> the SAME `ConversationSession` `apps/nexa_chat.py` uses (built by
`nexa.bootstrap.build_default_session()`) -> streamed assistant text.

No TTS, no Piper, no speaker playback of assistant responses, no barge-in
(ADR-0003 M2.3 scope).

Usage:
    python3 apps/nexa_voice_chat_probe.py --language pl
    python3 apps/nexa_voice_chat_probe.py --language en
Speak naturally; watch voice state, the transcript, and the streamed
assistant reply. Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.bootstrap import build_default_session  # noqa: E402
from nexa.stt import (  # noqa: E402
    Language,
    SttBinaryNotFoundError,
    SttModelNotFoundError,
    TranscriptionResult,
    WhisperCppTranscriber,
)
from nexa.voice import LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402
from nexa.voice_conversation import VoiceConversationAdapter  # noqa: E402

# FIFO of (end_of_turn_monotonic, stt_result_monotonic) pairs, one per
# accepted (non-empty) transcript, pushed in `on_transcription` and popped in
# `on_user_transcript`. Correlation by FIFO position is correct here because
# `VoiceConversationAdapter` guarantees conversation turns start in the exact
# order transcripts were accepted (SerialConversationQueue) — even when a
# second utterance is spoken while the first is still being answered.
_pending_timings: deque[tuple[float, float]] = deque()
_current_turn_timing: dict[str, float | None] = {}
_last_end_of_turn: float | None = None


def on_event(event: VoiceEvent) -> None:
    global _last_end_of_turn
    print(f"voice state: {event.to_state.value.upper()}")
    if event.to_state == VoiceState.END_OF_TURN:
        _last_end_of_turn = time.monotonic()


def make_transcription_handlers(adapter_ref: list[VoiceConversationAdapter]):
    def on_transcription(result: TranscriptionResult) -> None:
        now = time.monotonic()
        text = result.text.strip()
        print(f'  stt result: "{result.text}" (stt latency {result.wall_latency_s:.1f}s)')
        if text and _last_end_of_turn is not None:
            print(f"  latency END_OF_TURN -> STT result: {now - _last_end_of_turn:.2f}s")
            _pending_timings.append((_last_end_of_turn, now))
        if text:
            print("conversation: QUEUED")
        adapter_ref[0].handle_transcription(result)

    def on_transcription_error(exc: Exception) -> None:
        print(f"  stt error: {exc}")
        adapter_ref[0].handle_transcription_error(exc)

    return on_transcription, on_transcription_error


def on_user_transcript(text: str) -> None:
    end_of_turn_ts, stt_result_ts = (
        _pending_timings.popleft() if _pending_timings else (None, None)
    )
    _current_turn_timing.clear()
    _current_turn_timing["end_of_turn"] = end_of_turn_ts
    _current_turn_timing["stt_result"] = stt_result_ts
    _current_turn_timing["first_token"] = None
    print(f'\nuser: "{text}"')
    print("conversation: THINKING")
    print("assistant: ", end="", flush=True)


def on_assistant_token(token: str) -> None:
    if _current_turn_timing.get("first_token") is None:
        _current_turn_timing["first_token"] = time.monotonic()
    print(token, end="", flush=True)


def on_assistant_complete(_full_text: str, *, adapter_ref: list[VoiceConversationAdapter]) -> None:
    now = time.monotonic()
    stt_result_ts = _current_turn_timing.get("stt_result")
    end_of_turn_ts = _current_turn_timing.get("end_of_turn")
    first_token_ts = _current_turn_timing.get("first_token")
    print()
    if stt_result_ts is not None and first_token_ts is not None:
        print(f"  latency STT result -> first token: {first_token_ts - stt_result_ts:.2f}s")
    if stt_result_ts is not None:
        print(f"  latency STT result -> completion:  {now - stt_result_ts:.2f}s")
    if end_of_turn_ts is not None and first_token_ts is not None:
        print(f"  latency END_OF_TURN -> first token: {first_token_ts - end_of_turn_ts:.2f}s")
    if end_of_turn_ts is not None:
        print(f"  latency END_OF_TURN -> completion:  {now - end_of_turn_ts:.2f}s")
    concurrency = adapter_ref[0].max_observed_conversation_concurrency
    print(f"  max concurrent conversation turns observed: {concurrency}")
    print()


def on_conversation_error(exc: Exception) -> None:
    print(f"\n[error] conversation failed: {exc}\n")


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

    session = build_default_session()
    description = session.provider.describe()

    adapter_ref: list[VoiceConversationAdapter] = []
    adapter = VoiceConversationAdapter(
        session,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=lambda text: on_assistant_complete(text, adapter_ref=adapter_ref),
        on_conversation_error=on_conversation_error,
    )
    adapter_ref.append(adapter)
    adapter.start()

    on_transcription, on_transcription_error = make_transcription_handlers(adapter_ref)
    runtime = VoiceRuntime(
        LocalAudioConfig(),
        on_event=on_event,
        transcriber=transcriber,
        language=language,
        on_transcription=on_transcription,
        on_transcription_error=on_transcription_error,
    )

    print("NeXa M2.3 Voice Conversation Probe")
    print(f"language: {language.value}")
    print(f"model: {description.model}")
    print()
    print("Speak naturally. Ctrl+C to stop.\n")

    try:
        await runtime.run()
    finally:
        await adapter.shutdown()

    print("\nfinal voice state:", runtime.state_machine.state.value.upper())
    print(f"max concurrent STT executions observed this session: "
          f"{runtime.max_observed_stt_concurrency}")
    print(f"max concurrent conversation turns observed this session: "
          f"{adapter.max_observed_conversation_concurrency}")


if __name__ == "__main__":
    asyncio.run(main())
