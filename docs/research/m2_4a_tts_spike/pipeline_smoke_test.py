#!/usr/bin/env python3
"""M2.4A spike: minimal real Pipecat pipeline proving PiperHttpTTSService's
audio actually reaches LocalAudioOutputTransport (real reSpeaker output).

Throwaway research script (not NeXa product code). Injects a scripted
LLMTextFrame stream (simulating ConversationSession.send() token output)
into: LLMTextFrame(s) -> LLMFullResponseEndFrame -> PiperHttpTTSService
(sentence aggregation, real HTTP call to a locally running
`python -m piper.http_server`) -> LocalAudioOutputTransport.

Usage (English server on :5001 must already be running):
    python3 docs/research/m2_4a_tts_spike/pipeline_smoke_test.py --lang en
    python3 docs/research/m2_4a_tts_spike/pipeline_smoke_test.py --lang pl
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import aiohttp  # noqa: E402
import pyaudio  # noqa: E402
from loguru import logger  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.piper.tts import PiperHttpTTSService  # noqa: E402
from pipecat.transports.local.audio import (  # noqa: E402
    LocalAudioTransport,
    LocalAudioTransportParams,
)
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from nexa.voice.device import find_device_index  # noqa: E402

EN_TOKENS = [
    "Hello ", "NeXa. ", "This ", "is ", "a ", "streaming ", "text ", "to ", "speech ", "test.",
]
PL_TOKENS = [
    "Cześć ", "NeXa. ", "To ", "jest ", "test ", "syntezy ", "mowy ",
    "w ", "czasie ", "rzeczywistym.",
]


class _ScriptedTextSource(FrameProcessor):
    """Pushes a scripted LLMTextFrame stream on StartFrame, simulating
    ConversationSession's token stream, and records timing/frame events."""

    def __init__(self, tokens: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._tokens = tokens
        self.events: list[tuple[float, str]] = []

    def _log(self, label: str) -> None:
        self.events.append((time.monotonic(), label))
        logger.info(f"[spike] {label}")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.create_task(self._run())

    async def _run(self) -> None:
        self._log("llm_response_start")
        await self.push_frame(LLMFullResponseStartFrame())
        for tok in self._tokens:
            await self.push_frame(LLMTextFrame(tok))
            await asyncio.sleep(0.05)  # simulate real token pacing
        self._log("llm_response_end")
        await self.push_frame(LLMFullResponseEndFrame())


class _TTSEventObserver(FrameProcessor):
    """Observes TTS frames flowing downstream, forwards unchanged."""

    def __init__(self, events: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self._events = events

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame):
            self._events.append((time.monotonic(), "tts_started"))
            logger.info("[spike] tts_started")
        elif isinstance(frame, TTSAudioRawFrame):
            self._events.append((time.monotonic(), f"tts_audio_chunk({len(frame.audio)}B)"))
        elif isinstance(frame, TTSStoppedFrame):
            self._events.append((time.monotonic(), "tts_stopped"))
            logger.info("[spike] tts_stopped")
        await self.push_frame(frame, direction)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=["en", "pl"], default="en")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--duration", type=float, default=15.0)
    args = parser.parse_args()

    tokens = EN_TOKENS if args.lang == "en" else PL_TOKENS
    port = args.port or (5001 if args.lang == "en" else 5002)
    base_url = f"http://127.0.0.1:{port}/synthesize"

    pa = pyaudio.PyAudio()
    output_index = find_device_index(pa, "respeaker", require_output=True)

    session = aiohttp.ClientSession()
    tts = PiperHttpTTSService(base_url=base_url, aiohttp_session=session)

    events: list[tuple[float, str]] = []
    source = _ScriptedTextSource(tokens)
    observer = _TTSEventObserver(events)

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=False,
            audio_out_enabled=True,
            audio_out_sample_rate=16000,
            audio_out_channels=1,
            output_device_index=output_index,
        )
    )

    pipeline = Pipeline([source, tts, observer, transport.output()])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_out_sample_rate=16000),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    runner = WorkerRunner()
    await runner.add_workers(worker)

    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(args.duration)

    await runner.cancel(reason="spike complete")
    try:
        await asyncio.wait_for(run_task, timeout=5)
    except TimeoutError:
        pass
    await session.close()

    print("\n=== EVENT LOG ===")
    t0 = source.events[0][0] if source.events else events[0][0]
    for t, label in sorted(source.events + events):
        print(f"+{t - t0:7.3f}s  {label}")


if __name__ == "__main__":
    asyncio.run(main())
