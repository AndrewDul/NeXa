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
    DEFAULT_PIPER_NICE,
    EN_VOICE,
    PL_VOICE,
    PiperHttpConfig,
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
    DEFAULT_CONTINUITY_TARGET_S,
    DEFAULT_TTS_CONTEXT_TIMEOUT_S,
    ActivityFlags,
    AssistantSpeechBridge,
    MetricsCollector,
    NexaSpeechContinuityController,
    NexaSpeechPlanner,
    ResourceSampler,
    TimedPiperHttpTTSService,
    TtsStatusObserver,
    TurnMetrics,
    TurnReportJsonlWriter,
    TurnTimingTracker,
    ensure_sentence_tokenizer_data,
    render_turn_report,
)

# M2.4 half-duplex safety gate — while NeXa's TTS audio plays, the mic is
# withheld before VAD/STT so she can't hear and re-transcribe herself
# (real-hardware self-conversation loop). Temporary until M2.5 barge-in.
_gate = HalfDuplexGate()

# M2.4B.1 — realtime speech-flow instrumentation. `None` unless --report is
# passed; when None every hook below is a no-op and the probe runs the exact
# frozen M2.4 path. Never changes speech behaviour, never touches the model
# or the pipeline — it only observes.
_metrics: MetricsCollector | None = None
_activity: ActivityFlags | None = None

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
        if _activity is not None:
            # STT (whisper.cpp) runs between END_OF_TURN and the transcription
            # result — an approximate "STT active" window for the report's
            # concurrency check. Labelled as a proxy in R0013.
            _activity.stt_active = True


_pending_stt: deque[tuple[str, float]] = deque()  # (stt_text, stt_wall_latency_s) for --report


def make_transcription_handlers(adapter_ref: list[VoiceConversationAdapter]):
    def on_transcription(result: TranscriptionResult) -> None:
        now = time.monotonic()
        text = result.text.strip()
        if _activity is not None:
            _activity.stt_active = False
        print(f'  stt result: "{result.text}" (stt latency {result.wall_latency_s:.1f}s)')
        if text and _last_end_of_turn is not None:
            _pending_timings.append((_last_end_of_turn, now))
            _pending_stt.append((text, result.wall_latency_s))
        if text:
            print("conversation: QUEUED")
        adapter_ref[0].handle_transcription(result)

    def on_transcription_error(exc: Exception) -> None:
        if _activity is not None:
            _activity.stt_active = False
        print(f"  stt error: {exc}")
        adapter_ref[0].handle_transcription_error(exc)

    return on_transcription, on_transcription_error


def make_conversation_handlers(bridge: AssistantSpeechBridge):
    def on_user_transcript(text: str) -> None:
        end_of_turn_ts, stt_result_ts = (
            _pending_timings.popleft() if _pending_timings else (None, None)
        )
        _timing.start_turn(end_of_turn=end_of_turn_ts, stt_result=stt_result_ts)
        if _metrics is not None:
            stt_text, stt_lat = _pending_stt.popleft() if _pending_stt else (text, None)
            _metrics.start_turn(
                end_of_turn=end_of_turn_ts,
                stt_result=stt_result_ts,
                stt_text=stt_text,
                stt_wall_latency_s=stt_lat,
            )
        print(f'\nuser: "{text}"')
        print("conversation: THINKING")
        print("assistant: ", end="", flush=True)
        bridge.on_user_transcript(text)

    def on_assistant_token(token: str) -> None:
        _timing.first_token()
        if _metrics is not None:
            _metrics.first_token()
            _metrics.assistant_token(token)
        print(token, end="", flush=True)
        bridge.on_assistant_token(token)

    def on_assistant_complete(full_text: str) -> None:
        _timing.assistant_complete(full_text)
        if _metrics is not None:
            _metrics.assistant_complete(full_text)
        print()
        bridge.on_assistant_complete(full_text)

    def on_conversation_error(exc: Exception) -> None:
        if _metrics is not None:
            _metrics.conversation_error(exc)
        print(f"\n[error] conversation failed: {exc}\n")
        bridge.on_conversation_error(exc)

    return on_user_transcript, on_assistant_token, on_assistant_complete, on_conversation_error


def make_tts_handlers(adapter_ref: list[VoiceConversationAdapter]):
    def on_tts_started() -> None:
        _timing.tts_started()
        if _metrics is not None:
            _metrics.tts_started()
        print("tts: sentence ready")

    def on_tts_first_audio() -> None:
        _timing.tts_first_audio()
        if _metrics is not None:
            _metrics.tts_first_audio()
        gate_state = "CLOSED" if _gate.mic_suppressed else "OPEN"
        print(f"tts: PLAYING   [mic gate: {gate_state}]")

    def on_tts_stopped() -> None:
        gate_state = "CLOSED" if _gate.mic_suppressed else "OPEN"
        print(f"tts: done   [mic gate: {gate_state}]")
        if _metrics is not None:
            _metrics.tts_stopped()
        finished = _timing.tts_stopped()
        if finished is not None:
            _report_turn_timings(finished, adapter_ref)

    def on_tts_error(error: str) -> None:
        print(f"\n[error] tts failed: {error}\n")

    # M2.4B.1 measurement-only hooks (active only with --report).
    def on_tts_audio(nbytes: int, sample_rate: int, num_channels: int) -> None:
        if _metrics is not None:
            _metrics.tts_audio(nbytes, sample_rate, num_channels)

    def on_tts_text(text: str) -> None:
        if _metrics is not None:
            _metrics.tts_text(text)

    def on_tts_response_end() -> None:
        if _metrics is not None:
            _metrics.tts_response_end()

    return (
        on_tts_started,
        on_tts_first_audio,
        on_tts_stopped,
        on_tts_error,
        on_tts_audio,
        on_tts_text,
        on_tts_response_end,
    )


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
    parser.add_argument(
        "--report",
        action="store_true",
        help="M2.4B.1: measure-only speech-flow instrumentation. Prints a "
        "compact per-turn gap report and samples OS resources. Does NOT "
        "change speech behaviour, the model, or the pipeline.",
    )
    parser.add_argument(
        "--report-json",
        metavar="PATH",
        default=None,
        help="M2.4B.1: append one JSON record per turn to PATH (implies "
        "--report). Short text previews only; never raw audio.",
    )
    parser.add_argument(
        "--report-sample-ms",
        type=int,
        default=400,
        help="resource-sampler period in ms (--report only; default 400).",
    )
    parser.add_argument(
        "--piper-nice",
        type=int,
        default=DEFAULT_PIPER_NICE,
        help=f"M2.4B.3.1: POSIX nice value for the external Piper process so "
        f"gemma4:e4b wins CPU while generating the next phrase (R0014). "
        f"Default {DEFAULT_PIPER_NICE}; 0 disables. No sudo, NeXa's own "
        f"process is never reniced, llama-server is never touched.",
    )
    parser.add_argument(
        "--tts-context-timeout-s",
        type=float,
        default=DEFAULT_TTS_CONTEXT_TIMEOUT_S,
        help=f"M2.4B.3.1: Pipecat stop_frame_timeout_s — idle time before the "
        f"speaking context is torn down (Pipecat default 3.0). Raised to "
        f"{DEFAULT_TTS_CONTEXT_TIMEOUT_S} so a normal inter-phrase LLM stall "
        f"no longer causes BotStopped/BotStarted churn. Keeps the context "
        f"alive only — adds no silence.",
    )
    parser.add_argument(
        "--continuity-target-s",
        type=float,
        default=DEFAULT_CONTINUITY_TARGET_S,
        help=f"M2.4B.3.2: NexaSpeechContinuityController target audio reserve "
        f"(estimate, seconds). Phrase 0 always immediate; phrases 1..N are "
        f"held only while the estimated reserve is >= this, and released "
        f"immediately below it. Candidate A/B: 1.5 / 2.0 / 2.5 "
        f"(default {DEFAULT_CONTINUITY_TARGET_S}). Never grows a batch, "
        f"never adds silence.",
    )
    parser.add_argument(
        "--no-continuity",
        action="store_true",
        help="M2.4B.3.2: disable the continuity controller (pure pass-through) "
        "— for the A/B 'off' baseline.",
    )
    return parser.parse_args()


async def main() -> None:
    global _metrics, _activity
    args = parse_args()
    language = Language(args.language)
    report_mode = args.report or args.report_json is not None

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
        piper_server = PiperHttpServer(PiperHttpConfig(nice=args.piper_nice))
        await piper_server.start()
        prewarm_times = await piper_server.prewarm()
    except (PiperVenvNotFoundError, PiperVoiceNotFoundError, PiperServerStartError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Piper ready (nice {args.piper_nice}). Prewarm timings: {prewarm_times}")

    session = build_default_session()
    description = session.provider.describe()
    print(f"model: {description.model}")

    # --- M2.4B.1: measure-only instrumentation (only when --report) --------
    sampler: ResourceSampler | None = None
    jsonl: TurnReportJsonlWriter | None = None
    if report_mode:
        _activity = ActivityFlags()
        sampler = ResourceSampler(
            period_s=max(0.05, args.report_sample_ms / 1000.0), activity=_activity
        )
        if args.report_json is not None:
            jsonl = TurnReportJsonlWriter(args.report_json)

        def _on_turn_finalized(tm: TurnMetrics) -> None:
            print("\n" + render_turn_report(tm) + "\n", flush=True)
            if jsonl is not None:
                jsonl.write(tm)

        _metrics = MetricsCollector(
            on_turn_finalized=_on_turn_finalized,
            resource_summary_fn=(sampler.window_summary if sampler is not None else None),
            activity=_activity,
        )
        sampler.start()
        print("M2.4B.1 report mode ON — measure-only; speech behaviour unchanged.")
        if jsonl is not None:
            print(f"M2.4B.1 JSONL: {args.report_json}")

    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=_gate)
    # M2.4B.2 — NeXa speech planner: TTS-only Markdown/list normalization +
    # Polish-aware phrase boundaries. One natural phrase per AggregatedTextFrame;
    # NO pacing / buffer control / CPU scheduling (that is B.3). Never mutates
    # canonical assistant text or history.
    speech_planner = NexaSpeechPlanner(
        en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language=language.value
    )
    # M2.4B.3.2 — short-reply speech continuity controller. Phrase 0 always
    # immediate (no prebuffer); phrases 1..N released as soon as the ESTIMATED
    # audio reserve is low, held only briefly while it is healthy. Never grows
    # a batch, never inserts silence, never changes speech rate or text.
    continuity_controller = NexaSpeechContinuityController(
        target_reserve_s=args.continuity_target_s,
        enabled=not args.no_continuity,
        on_release=(
            (lambda rel: _metrics.controller_release(rel)) if report_mode else None
        ),
    )
    adapter_ref: list[VoiceConversationAdapter] = []
    (
        on_tts_started,
        on_tts_first_audio,
        on_tts_stopped,
        on_tts_error,
        on_tts_audio,
        on_tts_text,
        on_tts_response_end,
    ) = make_tts_handlers(adapter_ref)
    def _fan_out_tts_audio(nbytes: int, sample_rate: int, num_channels: int) -> None:
        # Always feed the continuity controller its buffer-estimate signal;
        # additionally feed the B.1 metrics in report mode.
        continuity_controller.note_tts_audio(nbytes, sample_rate, num_channels)
        if report_mode:
            on_tts_audio(nbytes, sample_rate, num_channels)

    tts_observer = TtsStatusObserver(
        on_tts_started=on_tts_started,
        on_tts_first_audio=on_tts_first_audio,
        on_tts_stopped=on_tts_stopped,
        on_tts_error=on_tts_error,
        on_tts_audio=_fan_out_tts_audio,
        on_tts_text=(on_tts_text if report_mode else None),
        on_tts_response_end=(on_tts_response_end if report_mode else None),
        on_bot_started_speaking=(
            (lambda: _metrics.bot_started_speaking()) if report_mode else None
        ),
        on_bot_stopped_speaking=(
            (lambda: _metrics.bot_stopped_speaking()) if report_mode else None
        ),
    )

    aiohttp_session = aiohttp.ClientSession()
    # M2.4B.3.1: keep one speaking context alive across a normal inter-phrase
    # LLM stall (Pipecat default 3.0 s -> BotStopped/BotStarted churn).
    tts_kwargs = dict(
        base_url=piper_server.config.synthesize_url,
        aiohttp_session=aiohttp_session,
        stop_frame_timeout_s=args.tts_context_timeout_s,
    )
    if report_mode:
        # Measure-only subclass: times each run_tts HTTP request (true Piper
        # synthesis speed) — yields an identical frame stream, no behaviour
        # change. M2.4B.1A metric correction (R0013/R0014).
        tts_service = TimedPiperHttpTTSService(
            on_http_call=(lambda call: _metrics.http_synthesis(call)), **tts_kwargs
        )
    else:
        tts_service = PiperHttpTTSService(**tts_kwargs)
    print(f"TTS context timeout: {args.tts_context_timeout_s}s "
          f"(Pipecat default 3.0; M2.4B.3.1)")
    _cc = ("OFF (pass-through)" if args.no_continuity
           else f"target reserve {args.continuity_target_s}s (ESTIMATE)")
    print(f"continuity controller: {_cc}  (M2.4B.3.2)")

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
        extra_output_stages=[
            bridge, speech_planner, continuity_controller, tts_service, tts_observer
        ],
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
        if _metrics is not None:
            # Flush any turn that never received its downstream
            # LLMFullResponseEndFrame (e.g. Ctrl+C mid-reply). _finalize is
            # the single emit point — it prints + writes JSONL exactly once.
            flushed = _metrics.close()
            if flushed:
                print(f"(M2.4B.1: flushed {len(flushed)} unfinished turn record(s) at shutdown)")
        if sampler is not None:
            sampler.stop()
        if jsonl is not None:
            jsonl.close()

    print("\nfinal voice state:", runtime.state_machine.state.value.upper())
    print(f"max concurrent STT executions observed this session: "
          f"{runtime.max_observed_stt_concurrency}")
    print(f"max concurrent conversation turns observed this session: "
          f"{adapter.max_observed_conversation_concurrency}")
    print(f"mic frames withheld by half-duplex gate (NeXa's own audio): "
          f"{runtime.suppressed_mic_frames}")


if __name__ == "__main__":
    asyncio.run(main())
