#!/usr/bin/env python3
"""M2.4B research profiler (throwaway, scratchpad — no product code touched).

Measures, separately and with real components:
  1. Piper HTTP per-request synthesis latency vs. text length + audio seconds produced
     -> real-time factor (RTF) of local Piper medium on this Pi 5
  2. gemma4:e4b first-token latency + steady token rate (raw Ollama)
  3. A scripted end-to-end pipeline (AssistantSpeechBridge -> PiperHttpTTSService ->
     observer -> LocalAudioOutputTransport) fed tokens at a chosen rate, logging every
     TTS lifecycle event + measuring the actual silent gap between audio chunks.
"""
from __future__ import annotations

import asyncio
import sys
import time
import wave
import io
from pathlib import Path

REPO = Path("/home/devdul/Projects/NeXa_IkiGai")
sys.path.insert(0, str(REPO / "src"))

import aiohttp  # noqa: E402
import pyaudio  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    Frame, StartFrame, EndFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame,
    BotStartedSpeakingFrame, BotStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.piper.tts import PiperHttpTTSService  # noqa: E402
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from nexa.tts import EN_VOICE, PL_VOICE, PiperHttpServer  # noqa: E402
from nexa.voice import LocalAudioConfig  # noqa: E402
from nexa.voice.device import find_device_index  # noqa: E402
from nexa.voice_tts import AssistantSpeechBridge, TtsStatusObserver  # noqa: E402

PL_SENTENCES = [
    "Czarna dziura to obszar w przestrzeni, w którym grawitacja jest tak silna, że nic nie może się z niego wydostać.",
    "Najważniejszą cechą jest tak zwany horyzont zdarzeń, czyli granica, zza której nie ma powrotu.",
    "Powstaje, gdy bardzo masywna gwiazda kończy swoje życie i zapada się pod własnym ciężarem.",
    "W środku znajduje się osobliwość, punkt o nieskończonej gęstości.",
    "Choć nie widać ich bezpośrednio, astronomowie wykrywają je po wpływie na okoliczną materię.",
]


def wav_seconds(b: bytes) -> float:
    try:
        w = wave.open(io.BytesIO(b))
        return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


async def measure_piper(server: PiperHttpServer) -> None:
    print("\n===== 1. PIPER HTTP SYNTHESIS LATENCY (pl_PL-gosia-medium, this Pi 5) =====")
    print(f"{'chars':>6} {'synth_s':>8} {'audio_s':>8} {'RTF':>6}  text")
    for s in PL_SENTENCES:
        t0 = time.monotonic()
        audio = await server.synthesize(s, voice=PL_VOICE)
        dt = time.monotonic() - t0
        asec = wav_seconds(audio)
        rtf = dt / asec if asec else 0.0
        print(f"{len(s):>6} {dt:>8.3f} {asec:>8.2f} {rtf:>6.3f}  {s[:60]}...")
    # batch: whole reply in one request
    whole = " ".join(PL_SENTENCES)
    t0 = time.monotonic()
    audio = await server.synthesize(whole, voice=PL_VOICE)
    dt = time.monotonic() - t0
    asec = wav_seconds(audio)
    print(f"\nWHOLE REPLY in ONE request: {len(whole)} chars -> synth {dt:.3f}s, "
          f"audio {asec:.2f}s, RTF {dt/asec:.3f}")


async def measure_ollama() -> None:
    print("\n===== 2. gemma4:e4b TOKEN TIMING (raw Ollama /api/chat) =====")
    payload = {
        "model": "gemma4:e4b",
        "messages": [{"role": "user", "content": "Krótko wyjaśnij, czym jest czarna dziura i jak powstaje."}],
        "stream": True,
        "options": {"temperature": 0.7},
    }
    async with aiohttp.ClientSession() as sess:
        for run in (1, 2):  # cold then warm
            t0 = time.monotonic()
            first = None
            ntok = 0
            last = t0
            gaps = []
            async with sess.post("http://localhost:11434/api/chat", json=payload,
                                 timeout=aiohttp.ClientTimeout(total=180)) as r:
                async for line in r.content:
                    if not line.strip():
                        continue
                    import json as _j
                    d = _j.loads(line)
                    piece = d.get("message", {}).get("content", "")
                    if piece:
                        now = time.monotonic()
                        if first is None:
                            first = now - t0
                        else:
                            gaps.append(now - last)
                        last = now
                        ntok += 1
                    if d.get("done"):
                        break
            total = time.monotonic() - t0
            gaps.sort()
            p50 = gaps[len(gaps)//2] if gaps else 0
            p95 = gaps[int(len(gaps)*0.95)] if gaps else 0
            mx = gaps[-1] if gaps else 0
            rate = ntok / (total - (first or 0)) if total > (first or 0) else 0
            print(f"  run {run} ({'cold' if run==1 else 'warm'}): first_token={first:.2f}s "
                  f"tokens={ntok} total={total:.1f}s rate={rate:.2f} tok/s "
                  f"inter-token gap p50={p50*1000:.0f}ms p95={p95*1000:.0f}ms max={mx*1000:.0f}ms")


class _ScriptedTokens(FrameProcessor):
    def __init__(self, bridge, sentences, tok_delay, **kw):
        super().__init__(**kw)
        self._bridge = bridge
        self._sentences = sentences
        self._tok_delay = tok_delay
        self.events = []

    def log(self, label):
        self.events.append((time.monotonic(), label))
        print(f"  +{self.events[-1][0]-self._t0:6.2f}s  {label}", flush=True)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._t0 = time.monotonic()
            self.create_task(self._run())

    async def _run(self):
        await asyncio.sleep(0.2)
        self._bridge.on_user_transcript("Co to jest czarna dziura i jak powstaje?")
        self.log("LLM start")
        for si, s in enumerate(self._sentences):
            for w in s.split(" "):
                self._bridge.on_assistant_token(w + " ")
                await asyncio.sleep(self._tok_delay)
            self.log(f"sentence {si+1} text complete")
        self._bridge.on_assistant_complete(" ".join(self._sentences))
        self.log("LLM complete")


async def measure_pipeline(server: PiperHttpServer, tok_delay: float) -> None:
    print(f"\n===== 3. SCRIPTED END-TO-END PIPELINE, token delay={tok_delay*1000:.0f}ms "
          f"(~{1/tok_delay:.1f} tok/s) =====")
    cfg = LocalAudioConfig()
    pa = pyaudio.PyAudio()
    out_idx = find_device_index(pa, cfg.output_device_name, require_output=True)
    sess = aiohttp.ClientSession()
    tts = PiperHttpTTSService(base_url=server.config.synthesize_url, aiohttp_session=sess)
    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE)

    audio_bytes = [0]
    marks = []
    last_audio_t = [0.0]
    gap_list = []

    def on_started():
        marks.append((time.monotonic(), "TTSStarted"))
        print(f"  +{time.monotonic()-t0[0]:6.2f}s  TTSStarted", flush=True)

    def on_first():
        marks.append((time.monotonic(), "first audio"))
        print(f"  +{time.monotonic()-t0[0]:6.2f}s  first TTS audio", flush=True)

    def on_stopped():
        marks.append((time.monotonic(), "TTSStopped"))
        print(f"  +{time.monotonic()-t0[0]:6.2f}s  TTSStopped", flush=True)

    obs = TtsStatusObserver(on_tts_started=on_started, on_tts_first_audio=on_first,
                            on_tts_stopped=on_stopped, on_tts_error=lambda e: print("ERR", e))

    class _AudioSpy(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            now = time.monotonic()
            if isinstance(frame, TTSAudioRawFrame):
                if last_audio_t[0] and (now - last_audio_t[0]) > 0.35:
                    gap_list.append((now - t0[0], now - last_audio_t[0]))
                    print(f"  +{now-t0[0]:6.2f}s  >>> audio-frame gap {now-last_audio_t[0]:.2f}s", flush=True)
                last_audio_t[0] = now
                audio_bytes[0] += len(frame.audio)
            elif isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
                print(f"  +{now-t0[0]:6.2f}s  {type(frame).__name__}", flush=True)
            await self.push_frame(frame, direction)

    src = _ScriptedTokens(bridge, PL_SENTENCES, tok_delay)
    t0 = [0.0]
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=False, audio_out_enabled=True,
        audio_out_sample_rate=cfg.sample_rate, audio_out_channels=cfg.channels,
        output_device_index=out_idx))
    pipeline = Pipeline([src, bridge, tts, obs, _AudioSpy(), transport.output()])
    worker = PipelineWorker(pipeline, params=PipelineParams(audio_out_sample_rate=cfg.sample_rate),
                            enable_rtvi=False, idle_timeout_secs=None)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    t0[0] = time.monotonic()
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(2.0)
    while src.events and src.events[-1][1] != "LLM complete":
        await asyncio.sleep(0.5)
    # let audio drain
    await asyncio.sleep(max(6.0, audio_bytes[0]/32000 - (time.monotonic()-t0[0]) + 3))
    try:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run_task, timeout=10)
    except Exception:
        await runner.cancel(reason="done")
    await sess.close()
    total_audio_s = audio_bytes[0] / 32000.0
    print(f"  --- total audio produced: {total_audio_s:.1f}s; audio-frame gaps >0.35s: {len(gap_list)} "
          f"{['%.2fs'%g for _,g in gap_list]}")


async def main():
    server = PiperHttpServer()
    await server.start()
    pw = await server.prewarm()
    print("prewarm:", {k: round(v, 2) for k, v in pw.items()})
    await measure_piper(server)
    try:
        await measure_ollama()
    except Exception as e:
        print("ollama measure failed:", e)
    # ~3 tok/s (gemma4:e4b warm-ish) and a slower ~2 tok/s stress case
    await measure_pipeline(server, tok_delay=0.33)
    await measure_pipeline(server, tok_delay=0.5)
    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
