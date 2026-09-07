"""M2.4B.3.1 scripted hardware comparison — real Ollama + real Piper (nice
+10) + real LocalAudioOutputTransport + real ConversationSession, with
Pipecat stop_frame_timeout_s = 8 s. Only mic/STT is replaced by two
scripted transcripts.

Turn 1 = warm-up ("Ile jest osiem razy siedem?") — NOT measured.
Turn 2 = measured ("Powiedz mi, z czego składa się gwiazda.").

Prints the full M2.4B.1 --report for the measured turn + a resource window
summary.
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
from nexa.tts import EN_VOICE, PL_VOICE, PiperHttpConfig, PiperHttpServer  # noqa: E402
from nexa.voice import HalfDuplexGate, LocalAudioConfig  # noqa: E402
from nexa.voice.device import find_device_index  # noqa: E402
from nexa.voice_conversation import VoiceConversationAdapter  # noqa: E402
from nexa.voice_tts import (  # noqa: E402
    DEFAULT_TTS_CONTEXT_TIMEOUT_S,
    ActivityFlags,
    AssistantSpeechBridge,
    MetricsCollector,
    NexaSpeechPlanner,
    ResourceSampler,
    TimedPiperHttpTTSService,
    TtsStatusObserver,
    render_turn_report,
)

WARMUP = "Ile jest osiem razy siedem?"
MEASURED = "Powiedz mi, z czego składa się gwiazda."


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
    server = PiperHttpServer(PiperHttpConfig(nice=10))
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

    activity = ActivityFlags()
    sampler = ResourceSampler(period_s=0.4, activity=activity)
    gate = HalfDuplexGate()
    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=gate)
    planner = NexaSpeechPlanner(en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language="pl")

    spoken: list[list[str]] = [[]]
    finals: list = []
    collector = MetricsCollector(
        on_turn_finalized=finals.append,
        resource_summary_fn=sampler.window_summary,
        activity=activity,
    )

    tts = TimedPiperHttpTTSService(
        base_url=server.config.synthesize_url,
        aiohttp_session=aio,
        stop_frame_timeout_s=DEFAULT_TTS_CONTEXT_TIMEOUT_S,
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
        print(f"\n  [assistant canonical text] {full!r}", flush=True)

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
        for i, phrase in enumerate([WARMUP, MEASURED]):
            tag = "WARM-UP (not measured)" if i == 0 else "MEASURED"
            print("\n" + "=" * 70 + f"\n{tag}: {phrase}", flush=True)
            spoken.append([])
            adapter.handle_transcription(
                TranscriptionResult(text=phrase, language=Language.PL,
                                    audio_duration_s=2.0, wall_latency_s=0.0)
            )
            deadline = time.monotonic() + 300
            while len(finals) < i + 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            await asyncio.sleep(2.0)

    sampler.start()
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

    deadline = time.monotonic() + 700
    while len(finals) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
    await asyncio.sleep(2.0)
    try:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run_task, timeout=15)
    except Exception:
        await runner.cancel(reason="done")
    for tm in collector.close():
        finals.append(tm)
    sampler.stop()
    await adapter.shutdown()
    await aio.close()
    await server.stop()

    print("\n\n" + "#" * 70)
    print("# M2.4B.3.1 COMPARISON — measured turn only (turn #2)")
    print("#" * 70)
    for idx, tm in enumerate(finals):
        label = "WARM-UP (ignore)" if idx == 0 else "MEASURED"
        print(f"\n########## {label} ##########")
        print(render_turn_report(tm))
    print("\n# phrases to Piper on the MEASURED turn:")
    for k, c in enumerate(spoken[2] if len(spoken) > 2 else []):
        print(f"  [{k}] {c!r}")
    print("\n# ConversationSession history:")
    for turn in session.history:
        print(f"  {turn.role.value}: {turn.content!r}")


if __name__ == "__main__":
    asyncio.run(main())
