#!/usr/bin/env python3
"""NeXa M2.4 streaming-TTS probe — manual hardware acceptance harness.

Thin wiring/presentation only (`apps/README.md`) — no product logic here.
Drives the full real path: local mic -> Pipecat -> Silero VAD -> pre-roll
utterance capture -> `WhisperCppTranscriber` -> `VoiceConversationAdapter`
-> the SAME `ConversationSession` `apps/nexa_chat.py`/`nexa_voice_chat_probe.py`
use -> streamed assistant text -> `AssistantSpeechBridge` -> Pipecat's
native sentence aggregation -> `PiperHttpTTSService` (external Piper HTTP
process) -> `LocalAudioOutputTransport` -> real speaker.

No barge-in yet (ADR-0003 M2.4 scope) — TTS playback is not interrupted by
new speech.

Usage:
    python3 apps/nexa_voice_tts_probe.py --language pl
    python3 apps/nexa_voice_tts_probe.py --language en
Speak naturally; watch voice state, the transcript, and listen for the
spoken reply. Ctrl+C to stop.
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

import aiohttp  # noqa: E402
from pipecat.services.piper.tts import PiperHttpTTSService  # noqa: E402

from nexa.bootstrap import build_default_session  # noqa: E402
from nexa.stt import (  # noqa: E402
    Language,
    SttBinaryNotFoundError,
    SttModelNotFoundError,
    TranscriptionResult,
    WhisperCppTranscriber,
)
from nexa.tts import (  # noqa: E402
    EN_VOICE,
    PL_VOICE,
    PiperHttpError,
    PiperHttpServer,
    PiperServerStartError,
    PiperVenvNotFoundError,
    PiperVoiceNotFoundError,
)
from nexa.tts.errors import SentenceTokenizerDataMissingError  # noqa: E402
from nexa.voice import HalfDuplexGate, LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.state import VoiceEvent, VoiceState  # noqa: E402
from nexa.voice_conversation import VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts import (  # noqa: E402
    AssistantSpeechBridge,
    TtsStatusObserver,
    TurnTimingTracker,
    ensure_sentence_tokenizer_data,
)

# M2.4 half-duplex safety gate — while NeXa's TTS audio plays, the mic is
# withheld before VAD/STT so she can't hear and re-transcribe herself
# (real-hardware self-conversation loop). Temporary until M2.5 barge-in.
_gate = HalfDuplexGate()

# FIFO of (end_of_turn_monotonic, stt_result_monotonic) pairs — same
# correlation pattern as apps/nexa_voice_chat_probe.py.
_pending_timings: deque[tuple[float, float]] = deque()
_last_end_of_turn: float | None = None

# Correlates timing across the fully async STT -> ConversationSession -> TTS
# pipeline. A single shared "current turn" dict is wrong here: a later
# turn's conversation processing can start before an earlier turn's TTS has
# finished *playing* (SerialConversationQueue only serializes generation) —
# real hardware testing found exactly this corrupting the reported
# streaming-overlap proof. See nexa.voice_tts.timing for the fix.
_timing = TurnTimingTracker()


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
            _pending_timings.append((_last_end_of_turn, now))
        if text:
            print("conversation: QUEUED")
        adapter_ref[0].handle_transcription(result)

    def on_transcription_error(exc: Exception) -> None:
        print(f"  stt error: {exc}")
        adapter_ref[0].handle_transcription_error(exc)

    return on_transcription, on_transcription_error


def make_conversation_handlers(bridge: AssistantSpeechBridge):
    def on_user_transcript(text: str) -> None:
        end_of_turn_ts, stt_result_ts = (
            _pending_timings.popleft() if _pending_timings else (None, None)
        )
        _timing.start_turn(end_of_turn=end_of_turn_ts, stt_result=stt_result_ts)
        print(f'\nuser: "{text}"')
        print("conversation: THINKING")
        print("assistant: ", end="", flush=True)
        bridge.on_user_transcript(text)

    def on_assistant_token(token: str) -> None:
        _timing.first_token()
        print(token, end="", flush=True)
        bridge.on_assistant_token(token)

    def on_assistant_complete(full_text: str) -> None:
        _timing.assistant_complete()
        print()
        bridge.on_assistant_complete(full_text)

    def on_conversation_error(exc: Exception) -> None:
        print(f"\n[error] conversation failed: {exc}\n")
        bridge.on_conversation_error(exc)

    return on_user_transcript, on_assistant_token, on_assistant_complete, on_conversation_error


def make_tts_handlers(adapter_ref: list[VoiceConversationAdapter]):
    def on_tts_started() -> None:
        _timing.tts_started()
        print("tts: sentence ready")

    def on_tts_first_audio() -> None:
        _timing.tts_first_audio()
        gate_state = "CLOSED" if _gate.mic_suppressed else "OPEN"
        print(f"tts: PLAYING   [mic gate: {gate_state}]")

    def on_tts_stopped() -> None:
        gate_state = "CLOSED" if _gate.mic_suppressed else "OPEN"
        print(f"tts: done   [mic gate: {gate_state}]")
        finished = _timing.tts_stopped()
        if finished is not None:
            _report_turn_timings(finished, adapter_ref)

    def on_tts_error(error: str) -> None:
        print(f"\n[error] tts failed: {error}\n")

    return on_tts_started, on_tts_first_audio, on_tts_stopped, on_tts_error


def _report_turn_timings(turn, adapter_ref: list[VoiceConversationAdapter]) -> None:
    print()
    if turn.end_of_turn is not None and turn.stt_result is not None:
        delta = turn.stt_result - turn.end_of_turn
        print(f"  latency END_OF_TURN -> STT result:        {delta:.2f}s")
    if turn.stt_result is not None and turn.first_token is not None:
        delta = turn.first_token - turn.stt_result
        print(f"  latency STT result -> first token:        {delta:.2f}s")
    if turn.end_of_turn is not None and turn.tts_first_audio is not None:
        delta = turn.tts_first_audio - turn.end_of_turn
        print(f"  latency END_OF_TURN -> first TTS audio:   {delta:.2f}s")
    overlap = turn.streaming_overlap_seconds
    if overlap is not None:
        confirmed = turn.streaming_overlap_confirmed
        relation = "BEFORE" if confirmed else "AFTER"
        verdict = (
            "PASS — first_tts_audio_at < assistant_complete_at"
            if confirmed
            else "FAIL — no overlap (first TTS audio did not precede assistant completion)"
        )
        print(
            f"  STREAMING OVERLAP PROOF: first TTS audio started {abs(overlap):.2f}s "
            f"{relation} assistant completion ({verdict})"
        )
    concurrency = adapter_ref[0].max_observed_conversation_concurrency
    print(f"  max concurrent conversation turns observed: {concurrency}")
    print()


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
        ensure_sentence_tokenizer_data()
    except SentenceTokenizerDataMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        transcriber = WhisperCppTranscriber()
    except (SttBinaryNotFoundError, SttModelNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("NeXa M2.4 Streaming TTS Probe")
    print(f"language: {language.value}")
    print("starting external Piper HTTP server...")
    try:
        piper_server = PiperHttpServer()
        await piper_server.start()
        prewarm_times = await piper_server.prewarm()
    except (PiperVenvNotFoundError, PiperVoiceNotFoundError, PiperServerStartError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Piper ready. Prewarm timings: {prewarm_times}")

    session = build_default_session()
    description = session.provider.describe()
    print(f"model: {description.model}")

    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=_gate)
    adapter_ref: list[VoiceConversationAdapter] = []
    on_tts_started, on_tts_first_audio, on_tts_stopped, on_tts_error = make_tts_handlers(
        adapter_ref
    )
    tts_observer = TtsStatusObserver(
        on_tts_started=on_tts_started,
        on_tts_first_audio=on_tts_first_audio,
        on_tts_stopped=on_tts_stopped,
        on_tts_error=on_tts_error,
    )

    aiohttp_session = aiohttp.ClientSession()
    tts_service = PiperHttpTTSService(
        base_url=piper_server.config.synthesize_url, aiohttp_session=aiohttp_session
    )

    on_user_transcript, on_assistant_token, on_assistant_complete, on_conversation_error = (
        make_conversation_handlers(bridge)
    )
    adapter = VoiceConversationAdapter(
        session,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
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
        extra_output_stages=[bridge, tts_service, tts_observer],
        half_duplex_gate=_gate,
    )

    print("Speak naturally. Ctrl+C to stop.\n")

    try:
        await runtime.run()
    finally:
        await adapter.shutdown()
        await aiohttp_session.close()
        try:
            await piper_server.stop()
        except PiperHttpError:
            pass

    print("\nfinal voice state:", runtime.state_machine.state.value.upper())
    print(f"max concurrent STT executions observed this session: "
          f"{runtime.max_observed_stt_concurrency}")
    print(f"max concurrent conversation turns observed this session: "
          f"{adapter.max_observed_conversation_concurrency}")
    print(f"mic frames withheld by half-duplex gate (NeXa's own audio): "
          f"{runtime.suppressed_mic_frames}")


if __name__ == "__main__":
    asyncio.run(main())
