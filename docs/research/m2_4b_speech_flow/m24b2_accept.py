"""M2.4B.2 scripted hardware acceptance — real Ollama + real Piper + real
LocalAudioOutputTransport, real ConversationSession. Only the microphone/STT
is replaced by three scripted operator transcripts.

Drives: ConversationSession -> VoiceConversationAdapter -> AssistantSpeechBridge
-> NexaSpeechPlanner -> PiperHttpTTSService -> TtsStatusObserver ->
LocalAudioOutputTransport -> USB speaker, with the M2.4B.1 --report metrics.

Prints, per turn: the exact phrases that reached Piper (post-planner), the
metrics report, and the assistant text stored in ConversationSession
history (transcript-invariant check).
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO = Path("/home/devdul/Projects/NeXa_IkiGai")
sys.path.insert(0, str(REPO / "src"))

import aiohttp  # noqa: E402
import pyaudio  # noqa: E402
from pipecat.frames.frames import EndFrame, Frame, StartFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.transports.local.audio import (  # noqa: E402
    LocalAudioTransport,
    LocalAudioTransportParams,
)
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from nexa.bootstrap import build_default_session  # noqa: E402
from nexa.stt import Language, TranscriptionResult  # noqa: E402
from nexa.tts import EN_VOICE, PL_VOICE, PiperHttpServer  # noqa: E402
from nexa.voice import HalfDuplexGate, LocalAudioConfig  # noqa: E402
from nexa.voice.device import find_device_index  # noqa: E402
from nexa.voice_conversation import VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts import (  # noqa: E402
    AssistantSpeechBridge,
    MetricsCollector,
    NexaSpeechPlanner,
    TimedPiperHttpTTSService,
    TtsStatusObserver,
    render_turn_report,
)

PHRASES = [
    "Wyjaśnij, czym jest tak zwany horyzont zdarzeń.",
    "Podaj mi dwa przykłady strzelanek i jedną grę RPG.",
    "Podaj przykład, na przykład czarnej dziury, i wyjaśnij to prosto.",
]


class _Src(FrameProcessor):
    def __init__(self, feed, **kw):
        super().__init__(**kw)
        self._feed = feed

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.create_task(self._feed())


async def main() -> None:
    server = PiperHttpServer()
    await server.start()
    await server.prewarm()

    session = build_default_session()
    cfg = LocalAudioConfig()
    pa = pyaudio.PyAudio()
    out_idx = None
    for cand in (cfg.output_device_name, "UACDemo", "UAC", "respeaker", "pulse", "default"):
        try:
            out_idx = find_device_index(pa, cand, require_output=True)
            print(f"  [audio out] matched {cand!r} -> index {out_idx}")
            break
        except Exception:
            continue
    aio = aiohttp.ClientSession()

    gate = HalfDuplexGate()
    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=gate)
    planner = NexaSpeechPlanner(en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language="pl")

    spoken: list[list[str]] = [[]]
    finals: list = []
    collector = MetricsCollector(on_turn_finalized=finals.append)

    tts = TimedPiperHttpTTSService(
        base_url=server.config.synthesize_url,
        aiohttp_session=aio,
        on_http_call=lambda call: collector.http_synthesis(call),
    )
    obs = TtsStatusObserver(
        on_tts_started=collector.tts_started,
        on_tts_first_audio=collector.tts_first_audio,
        on_tts_stopped=collector.tts_stopped,
        on_tts_error=lambda e: print("  tts err", e),
        on_tts_audio=collector.tts_audio,
        on_tts_text=lambda t: (spoken[-1].append(t), collector.tts_text(t)),
        on_tts_response_end=collector.tts_response_end,
        on_bot_started_speaking=collector.bot_started_speaking,
        on_bot_stopped_speaking=collector.bot_stopped_speaking,
    )

    def on_user_transcript(text: str) -> None:
        collector.start_turn(end_of_turn=time.monotonic(), stt_result=time.monotonic(),
                             stt_text=text)
        bridge.on_user_transcript(text)

    def on_assistant_token(tok: str) -> None:
        collector.first_token()
        collector.assistant_token(tok)
        bridge.on_assistant_token(tok)

    def on_assistant_complete(full: str) -> None:
        collector.assistant_complete(full)
        bridge.on_assistant_complete(full)
        print(f"\n  [assistant canonical text] {full!r}")
        print(f"  [phrases to Piper so far this turn] {spoken[-1]}", flush=True)

    adapter = VoiceConversationAdapter(
        session,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
        on_conversation_error=lambda e: print("  conv err", e),
    )
    adapter.start()

    async def feed():
        await asyncio.sleep(0.3)
        for i, phrase in enumerate(PHRASES):
            print("\n" + "=" * 70)
            print(f"USER: {phrase}", flush=True)
            spoken.append([])
            adapter.handle_transcription(
                TranscriptionResult(
                    text=phrase, language=Language.PL, audio_duration_s=2.0, wall_latency_s=0.0
                )
            )
            # wait for this turn to finalise (downstream LLMFullResponseEnd +
            # playback drain) before the next
            deadline = time.monotonic() + 260
            while len(finals) < i + 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            print(f"  [session.history after turn {i + 1}]", flush=True)
            for turn in session.history:
                print(f"    {turn.role.value}: {turn.content!r}", flush=True)
            await asyncio.sleep(1.0)

    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=False, audio_out_enabled=True,
        audio_out_sample_rate=cfg.sample_rate, audio_out_channels=cfg.channels,
        output_device_index=out_idx))
    src = _Src(feed)
    pipeline = Pipeline([src, bridge, planner, tts, obs, transport.output()])
    worker = PipelineWorker(pipeline, params=PipelineParams(audio_out_sample_rate=cfg.sample_rate),
                            enable_rtvi=False, idle_timeout_secs=None)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())

    deadline = time.monotonic() + 600
    while len(finals) < len(PHRASES) and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
    await asyncio.sleep(2.0)

    try:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run_task, timeout=15)
    except Exception:
        await runner.cancel(reason="done")
    for tm in collector.close():
        finals.append(tm)
    await adapter.shutdown()
    await aio.close()
    await server.stop()

    print("\n\n" + "#" * 70)
    print("# M2.4B.2 ACCEPTANCE SUMMARY")
    print("#" * 70)
    for idx, (phrase, chunks) in enumerate(zip(PHRASES, spoken[1:], strict=False)):
        print(f"\n--- TURN {idx + 1}: {phrase}")
        print(f"  phrases that reached Piper (post-planner): {len(chunks)}")
        for k, c in enumerate(chunks):
            print(f"    [{k}] {c!r}")
    for tm in finals:
        print("\n" + render_turn_report(tm))

    print("\n\n# ConversationSession history (canonical transcript — must be clean text):")
    for turn in session.history:
        print(f"  {turn.role.value}: {turn.content!r}")


if __name__ == "__main__":
    asyncio.run(main())
