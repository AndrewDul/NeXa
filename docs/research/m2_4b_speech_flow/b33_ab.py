"""M2.4B.3.3 scripted hardware A/B — real gemma4:e4b + real Piper (nice +10,
stop_frame_timeout_s 8) + real LocalAudioOutputTransport + real
ConversationSession + the NexaSpeechContinuityController (kept at its B.3.2
default target 2.0s). Only mic/STT is scripted.

Compares the plain typed-chat policy (ResponseMode.TEXT — the A/B baseline)
against the new conversational voice policy (ResponseMode.VOICE) for four
ORDINARY questions asked WITHOUT "krótko" / "w dwóch zdaniach", plus one
explicit DETAIL-OVERRIDE question ("Wyjaśnij dokładnie ...").

Warm-up ("Ile jest osiem razy siedem?") is run first and excluded from the
comparison. Prints the M2.4B.1 --report for every measured turn and a
compact per-config summary (answer sentences, answer chars, first-sentence
chars) at the end.
"""
from __future__ import annotations

import asyncio
import re
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
from nexa.conversation import ResponseMode  # noqa: E402
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
# Ordinary questions, deliberately WITHOUT "krótko" / "w dwóch zdaniach".
ORDINARY = [
    ("A", "Co to jest czarna dziura?"),
    ("B", "Z czego składa się gwiazda?"),
    ("C", "Jak powstaje hel w gwieździe?"),
    ("D", "Po co człowiekowi sen?"),
]
# Explicit detail request — the VOICE policy must NOT clip this to 1-3 sentences.
DETAIL = ("E", "Wyjaśnij dokładnie, jak powstaje czarna dziura.")
QUESTIONS = [*ORDINARY, DETAIL]
CONFIGS = [ResponseMode.TEXT, ResponseMode.VOICE]

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


class _Src(FrameProcessor):
    def __init__(self, feed, **kw):
        super().__init__(**kw)
        self._feed = feed

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.create_task(self._feed())


async def run_config(mode: ResponseMode, out_idx, server, cfg_audio) -> tuple[list, list]:
    session = build_default_session()
    aio = aiohttp.ClientSession()
    activity = ActivityFlags()
    sampler = ResourceSampler(period_s=0.4, activity=activity)
    gate = HalfDuplexGate()
    bridge = AssistantSpeechBridge(en_voice=EN_VOICE, pl_voice=PL_VOICE, gate=gate)
    planner = NexaSpeechPlanner(en_voice=EN_VOICE, pl_voice=PL_VOICE, default_language="pl")
    # B.3.2 controller kept exactly at its shipped default.
    controller = NexaSpeechContinuityController(target_reserve_s=2.0, enabled=True)

    spoken: list[list[str]] = [[]]
    canonical: list[str] = []
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
        canonical.append(full)
        print(f"    [canonical] {full!r}", flush=True)

    adapter = VoiceConversationAdapter(
        session,
        on_user_transcript=on_user_transcript,
        on_assistant_token=on_assistant_token,
        on_assistant_complete=on_assistant_complete,
        on_conversation_error=lambda e: print("err", e),
        response_mode=mode,
    )
    adapter.start()

    async def feed():
        await asyncio.sleep(0.3)
        for i, (tag_id, phrase) in enumerate([("W", WARMUP), *QUESTIONS]):
            tag = "WARM-UP (excl.)" if i == 0 else f"Q{tag_id}"
            print(f"\n  --- {tag}: {phrase}", flush=True)
            spoken.append([])
            adapter.handle_transcription(TranscriptionResult(
                text=phrase, language=Language.PL, audio_duration_s=2.0, wall_latency_s=0.0))
            deadline = time.monotonic() + 300
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
    deadline = time.monotonic() + 1500
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
    return finals, canonical


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

    summary: dict[str, list] = {}
    for mode in CONFIGS:
        label = f"ResponseMode.{mode.name}"
        print("\n" + "#" * 78 + f"\n# {label}\n" + "#" * 78, flush=True)
        finals, canonical = await run_config(mode, out_idx, server, cfg_audio)
        for idx, tm in enumerate(finals):
            if idx == 0:
                continue  # skip warm-up
            qid = QUESTIONS[idx - 1][0]
            print(f"\n===== {label}  Q{qid} =====")
            print(render_turn_report(tm))
        summary[label] = canonical
        await asyncio.sleep(2.0)

    print("\n" + "=" * 78 + "\n REPLY SHAPE SUMMARY (canonical model output)\n" + "=" * 78)
    for label, canonical in summary.items():
        print(f"\n{label}")
        for j, ans in enumerate(canonical):
            if j == 0:
                continue  # warm-up
            qid, q = QUESTIONS[j - 1]
            sents = _sentences(ans)
            first_len = len(sents[0]) if sents else 0
            print(f"  Q{qid} {q!r}")
            print(f"     sentences={len(sents)}  answer_chars={len(ans)}  "
                  f"first_sentence_chars={first_len}")
            print(f"     first_sentence={sents[0]!r}" if sents else "     (empty)")

    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
