"""M2.4B.3.2 scripted hardware A/B — real gemma4:e4b + real Piper (nice +10,
stop_frame_timeout_s 8) + real LocalAudioOutputTransport + real
ConversationSession + the NexaSpeechContinuityController. Only mic/STT is
scripted.

Warm-up ("Ile jest osiem razy siedem?" — excluded), then the 3 short-answer
B.3.2 questions, run for CONTROL (--no-continuity) then targets 1.5 / 2.0 /
2.5. Prints the M2.4B.1 --report for every measured turn.
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
    NexaSpeechContinuityController,
    NexaSpeechPlanner,
    ResourceSampler,
    TimedPiperHttpTTSService,
    TtsStatusObserver,
    render_turn_report,
)

WARMUP = "Ile jest osiem razy siedem?"
QUESTIONS = [
    "Co to jest czarna dziura? Odpowiedz krótko.",
    "Z czego głównie składa się gwiazda? Odpowiedz w dwóch zdaniach.",
    "A jak powstaje hel w gwieździe? Krótko.",
]
# None = CONTROL (--no-continuity); floats = continuity target reserve seconds
CONFIGS = [None, 1.5, 2.0, 2.5]


class _Src(FrameProcessor):
    def __init__(self, feed, **kw):
        super().__init__(**kw)
        self._feed = feed

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.create_task(self._feed())


async def run_config(cfg, out_idx, server, cfg_audio) -> list:
    session = build_default_session()
    aio = aiohttp.ClientSession()
    activity = ActivityFlags()
    sampler = ResourceSampler(period_s=0.4, activity=activity)
    gate = HalfDuplexGate()
    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=gate)
    planner = NexaSpeechPlanner(en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language="pl")
    controller = NexaSpeechContinuityController(
        target_reserve_s=(cfg if cfg is not None else 2.0),
        enabled=(cfg is not None),
    )

    spoken: list[list[str]] = [[]]
    finals: list = []
    collector = MetricsCollector(
        on_turn_finalized=finals.append,
        resource_summary_fn=sampler.window_summary, activity=activity,
    )
    controller._on_release = lambda rel: collector.controller_release(rel)

    tts = TimedPiperHttpTTSService(
        base_url=server.config.synthesize_url, aiohttp_session=aio,
        stop_frame_timeout_s=DEFAULT_TTS_CONTEXT_TIMEOUT_S,
        on_http_call=lambda call: collector.http_synthesis(call),
    )

    def _audio(n, r, c):
        controller.note_tts_audio(n, r, c)
        collector.tts_audio(n, r, c)

    obs = TtsStatusObserver(
        on_tts_started=collector.tts_started, on_tts_first_audio=collector.tts_first_audio,
        on_tts_stopped=collector.tts_stopped, on_tts_error=lambda e: print("  tts err", e),
        on_tts_audio=_audio,
        on_tts_text=lambda t: (spoken[-1].append(t), collector.tts_text(t)),
        on_tts_response_end=collector.tts_response_end,
        on_bot_started_speaking=collector.bot_started_speaking,
        on_bot_stopped_speaking=collector.bot_stopped_speaking,
    )

    def on_user_transcript(text):
        collector.start_turn(end_of_turn=time.monotonic(), stt_result=time.monotonic(),
                             stt_text=text)
        bridge.on_user_transcript(text)

    def on_assistant_token(tok):
        collector.first_token()
        collector.assistant_token(tok)
        bridge.on_assistant_token(tok)

    def on_assistant_complete(full):
        collector.assistant_complete(full)
        bridge.on_assistant_complete(full)
        print(f"    [canonical] {full!r}", flush=True)

    adapter = VoiceConversationAdapter(
        session,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
        on_conversation_error=lambda e: print("err", e),
    )
    adapter.start()

    async def feed():
        await asyncio.sleep(0.3)
        for i, phrase in enumerate([WARMUP, *QUESTIONS]):
            tag = "WARM-UP (excl.)" if i == 0 else f"Q{i}"
            print(f"\n  --- {tag}: {phrase}", flush=True)
            spoken.append([])
            adapter.handle_transcription(TranscriptionResult(
                text=phrase, language=Language.PL, audio_duration_s=2.0, wall_latency_s=0.0))
            deadline = time.monotonic() + 240
            while len(finals) < i + 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.4)
            await asyncio.sleep(1.5)

    sampler.start()
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=False, audio_out_enabled=True,
        audio_out_sample_rate=cfg_audio.sample_rate, audio_out_channels=cfg_audio.channels,
        output_device_index=out_idx))
    src = _Src(feed)
    pipeline = Pipeline([src, bridge, planner, controller, tts, obs, transport.output()])
    worker = PipelineWorker(pipeline, params=PipelineParams(
        audio_out_sample_rate=cfg_audio.sample_rate), enable_rtvi=False, idle_timeout_secs=None)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    deadline = time.monotonic() + 900
    while len(finals) < 1 + len(QUESTIONS) and time.monotonic() < deadline:
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
    return finals, spoken


async def main() -> None:
    server = PiperHttpServer(PiperHttpConfig(nice=10))
    await server.start()
    await server.prewarm()
    cfg_audio = LocalAudioConfig()
    pa = pyaudio.PyAudio()
    out_idx = None
    for cand in (cfg_audio.output_device_name, "UACDemo", "UAC", "respeaker", "default"):
        try:
            out_idx = find_device_index(pa, cand, require_output=True)
            print(f"[audio out] {cand!r} -> {out_idx}")
            break
        except Exception:
            continue

    for cfg in CONFIGS:
        label = "CONTROL (no continuity)" if cfg is None else f"continuity target {cfg}s"
        print("\n" + "#" * 78 + f"\n# {label}\n" + "#" * 78, flush=True)
        finals, spoken = await run_config(cfg, out_idx, server, cfg_audio)
        for idx, tm in enumerate(finals):
            if idx == 0:
                continue  # skip warm-up
            print(f"\n===== {label}  Q{idx} =====")
            print(render_turn_report(tm))
        await asyncio.sleep(2.0)

    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
