#!/usr/bin/env python3
"""M2.4B.1 — validate the ``buffered_audio_seconds`` ESTIMATE against reality.

Runs the real M2.4 output path (`AssistantSpeechBridge` ->
`PiperHttpTTSService` -> `TtsStatusObserver` -> `LocalAudioOutputTransport`),
fed a scripted token stream at a chosen rate, with the M2.4B.1
`MetricsCollector` wired in and a 200 ms periodic buffer sampler. It then
compares, for each turn:

  estimate crossed <= 0 (underrun event)   vs   real BotStoppedSpeaking
  estimate value at each real BotStopped

No microphone, no Ollama. Throwaway research harness (like ``gap_profiler.py``).
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
from pipecat.services.piper.tts import PiperHttpTTSService  # noqa: E402
from pipecat.transports.local.audio import (  # noqa: E402
    LocalAudioTransport,
    LocalAudioTransportParams,
)
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from nexa.tts import EN_VOICE, PL_VOICE, PiperHttpServer  # noqa: E402
from nexa.voice import LocalAudioConfig  # noqa: E402
from nexa.voice.device import find_device_index  # noqa: E402
from nexa.voice_tts import AssistantSpeechBridge, MetricsCollector, TtsStatusObserver  # noqa: E402

PL_SENTENCES = [
    "Czarna dziura to obszar w przestrzeni, w którym grawitacja jest tak silna, że nic nie ucieka.",
    "Najważniejszą cechą jest tak zwany horyzont zdarzeń, granica bez powrotu.",
    "Powstaje, gdy masywna gwiazda zapada się pod własnym ciężarem.",
    "W środku jest osobliwość, punkt o nieskończonej gęstości.",
    "Astronomowie wykrywają je po wpływie na okoliczną materię.",
]


class _Src(FrameProcessor):
    def __init__(self, bridge, collector, tok_delay, **kw):
        super().__init__(**kw)
        self.b, self.c, self.d = bridge, collector, tok_delay

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.create_task(self._run())

    async def _run(self):
        await asyncio.sleep(0.2)
        self.c.start_turn(end_of_turn=time.monotonic(), stt_result=time.monotonic(), stt_text="q")
        self.b.on_user_transcript("Co to jest czarna dziura i jak powstaje?")
        for s in PL_SENTENCES:
            for w in s.split(" "):
                self.c.first_token()
                self.c.assistant_token(w + " ")
                self.b.on_assistant_token(w + " ")
                await asyncio.sleep(self.d)
        full = " ".join(PL_SENTENCES)
        self.c.assistant_complete(full)
        self.b.on_assistant_complete(full)


async def _periodic(collector, stop):
    while not stop.is_set():
        collector.periodic_buffer_sample()
        await asyncio.sleep(0.2)


async def run_one(server, tok_delay):
    cfg = LocalAudioConfig()
    pa = pyaudio.PyAudio()
    out_idx = find_device_index(pa, cfg.output_device_name, require_output=True)
    sess = aiohttp.ClientSession()
    tts = PiperHttpTTSService(base_url=server.config.synthesize_url, aiohttp_session=sess)
    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE)

    finals = []
    collector = MetricsCollector(on_turn_finalized=finals.append)
    obs = TtsStatusObserver(
        on_tts_started=collector.tts_started,
        on_tts_first_audio=collector.tts_first_audio,
        on_tts_stopped=collector.tts_stopped,
        on_tts_error=lambda e: print("tts err", e),
        on_tts_audio=collector.tts_audio,
        on_tts_text=collector.tts_text,
        on_tts_response_end=collector.tts_response_end,
        on_bot_started_speaking=collector.bot_started_speaking,
        on_bot_stopped_speaking=collector.bot_stopped_speaking,
    )
    src = _Src(bridge, collector, tok_delay)
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=False, audio_out_enabled=True,
        audio_out_sample_rate=cfg.sample_rate, audio_out_channels=cfg.channels,
        output_device_index=out_idx))
    pipeline = Pipeline([src, bridge, tts, obs, transport.output()])
    worker = PipelineWorker(pipeline, params=PipelineParams(audio_out_sample_rate=cfg.sample_rate),
                            enable_rtvi=False, idle_timeout_secs=None)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    stop = asyncio.Event()
    run_task = asyncio.create_task(runner.run())
    per_task = asyncio.create_task(_periodic(collector, stop))

    # wait until finalised or timeout
    for _ in range(120):
        if finals:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(3.0)
    stop.set()
    await per_task
    try:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run_task, timeout=10)
    except Exception:
        await runner.cancel(reason="done")
    for tm in collector.close():
        finals.append(tm)
    await sess.close()

    print(f"\n===== tok_delay={tok_delay*1000:.0f}ms  ({1/tok_delay:.1f} tok/s) =====")
    for tm in finals:
        stops = [round(s.stopped_at - (tm.timing.end_of_turn or 0), 2)
                 for s in tm.playback_spans if s.stopped_at]
        unders = [round(u - (tm.timing.end_of_turn or 0), 2) for u in tm.buffer.underrun_events]
        buf_at_stop = []
        for s in tm.playback_spans:
            if s.stopped_at:
                # nearest buffer sample to this BotStopped
                near = min(tm.buffer.samples, key=lambda x: abs(x[0] - s.stopped_at), default=None)
                buf_at_stop.append(round(near[1], 2) if near else None)
        print(f"  turn {tm.turn_index}: BotStopped@{stops}  underrun-events@{unders}")
        print(f"    buffer estimate value at each real BotStopped: {buf_at_stop}")
        print(f"    buffer min={_r(tm.buffer.min_value)} max={_r(tm.buffer.max_value)}  "
              f"underruns={tm.output_underrun_count}  gaps_ms={[round(g,0) for g in tm.silence_gaps_ms]}")
        print(f"    estimate_vs_real_stop_error_s = {_r(tm.estimate_vs_real_stop_error_s)}")
        print(f"    diagnosis = {tm.to_dict()['diagnosis']}")


def _r(x):
    return None if x is None else round(x, 2)


async def main():
    server = PiperHttpServer()
    await server.start()
    await server.prewarm()
    await run_one(server, 0.33)   # ~3 tok/s (warm gemma4:e4b)
    await run_one(server, 0.50)   # ~2 tok/s (stress)
    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
