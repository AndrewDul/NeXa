"""M2.4B.5A — live-voice stability regression tests (R0026).

Offline. Proves the strict pre-M2.5 half-duplex rule: once a real user
turn has been accepted, TV / ambient noise during the whole response
(think → generate → synthesize → play) cannot become STT or conversation
work, cannot build a queue backlog, and cannot be replayed later. No
barge-in (that is M2.5).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from fakes import FakeModelProvider  # noqa: E402
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import Role  # noqa: E402
from nexa.stt import Language, SerialTranscriptionQueue, TranscriptionResult  # noqa: E402
from nexa.stt.transcriber import SpeechTranscriber  # noqa: E402
from nexa.voice.gate import HalfDuplexGate  # noqa: E402
from nexa.voice.runtime import (  # noqa: E402
    DROP_BUSY_RESPONSE_IN_FLIGHT,
    DroppedUtterance,
    _UtteranceCaptureFrameProcessor,
)
from nexa.voice_conversation import DroppedTurn, VoiceConversationAdapter  # noqa: E402

SYSTEM_PROMPT = "Jesteś NeXa — testowa persona."
CHUNK = b"\x01\x00" * 512


class _FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, Language]] = []

    async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
        self.calls.append((audio, language))
        return TranscriptionResult(text="x", language=language,
                                   audio_duration_s=0.1, wall_latency_s=0.01)


def _fake_tr(text: str, lang: Language = Language.EN) -> TranscriptionResult:
    return TranscriptionResult(text=text, language=lang, audio_duration_s=1.0, wall_latency_s=0.1)


def _capture(transcriber, gate, *, on_transcription=None, on_utterance_dropped=None):
    proc = _UtteranceCaptureFrameProcessor(
        sample_rate=16_000, transcriber=transcriber, language=Language.EN,
        on_transcription=on_transcription, on_transcription_error=None,
        half_duplex_gate=gate, on_utterance_dropped=on_utterance_dropped,
    )
    proc._queue.start(asyncio.ensure_future)
    return proc


async def _push(proc, frame) -> None:
    await proc.process_frame(frame, FrameDirection.DOWNSTREAM)


# --------------------------------------------------------------------------- #
# HalfDuplexGate — the whole-response window
# --------------------------------------------------------------------------- #

class TestGateCoversWholeResponse(unittest.TestCase):
    def test_dispatch_closes_mic_before_any_audio(self) -> None:
        g = HalfDuplexGate()
        self.assertFalse(g.response_in_flight)
        g.notify_response_dispatched()
        self.assertTrue(g.response_in_flight)
        self.assertTrue(g.mic_suppressed)  # closed during the think/generate window

    def test_reopens_only_after_generation_and_playback_both_done(self) -> None:
        g = HalfDuplexGate()
        g.notify_response_dispatched()
        g.observe_frame(BotStartedSpeakingFrame())
        g.notify_response_finished()
        self.assertTrue(g.mic_suppressed)  # audio still playing
        g.observe_frame(BotStoppedSpeakingFrame())
        self.assertFalse(g.mic_suppressed)
        self.assertFalse(g.response_in_flight)

    def test_no_audio_reply_still_reopens(self) -> None:
        g = HalfDuplexGate()
        g.notify_response_dispatched()
        g.notify_response_finished()  # error / empty reply, TTS never spoke
        self.assertFalse(g.response_in_flight)
        self.assertFalse(g.mic_suppressed)

    def test_hard_stop_never_latches_shut(self) -> None:
        from pipecat.frames.frames import EndFrame
        g = HalfDuplexGate()
        g.notify_response_dispatched()
        g.observe_frame(BotStartedSpeakingFrame())
        g.observe_frame(EndFrame())
        self.assertFalse(g.mic_suppressed)


# --------------------------------------------------------------------------- #
# capture processor — busy utterances dropped, never enqueued
# --------------------------------------------------------------------------- #

class TestCaptureDropsBusyUtterances(unittest.IsolatedAsyncioTestCase):
    async def test_utterance_captured_during_response_is_dropped_with_telemetry(self) -> None:
        tr = _FakeTranscriber()
        gate = HalfDuplexGate()
        drops: list[DroppedUtterance] = []
        proc = _capture(tr, gate, on_utterance_dropped=drops.append)

        gate.notify_response_dispatched()  # NeXa is answering
        await _push(proc, VADUserStartedSpeakingFrame(start_secs=0.2))
        await _push(proc, InputAudioRawFrame(audio=CHUNK * 40, sample_rate=16_000, num_channels=1))
        await _push(proc, VADUserStoppedSpeakingFrame(stop_secs=1.0))  # "TV" ends
        await asyncio.sleep(0.02)

        self.assertEqual(tr.calls, [])  # never transcribed
        self.assertEqual(proc.stt_queue_depth, 0)  # nothing queued for later
        self.assertEqual(proc.dropped_busy_utterances, 1)
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0].reason, DROP_BUSY_RESPONSE_IN_FLIGHT)

    async def test_flood_of_busy_utterances_builds_no_stt_backlog(self) -> None:
        tr = _FakeTranscriber()
        gate = HalfDuplexGate()
        proc = _capture(tr, gate)
        gate.notify_response_dispatched()
        for _ in range(30):
            await _push(proc, VADUserStartedSpeakingFrame(start_secs=0.2))
            await _push(proc, InputAudioRawFrame(audio=CHUNK * 20, sample_rate=16_000,
                                                 num_channels=1))
            await _push(proc, VADUserStoppedSpeakingFrame(stop_secs=1.0))
        await asyncio.sleep(0.05)
        self.assertEqual(tr.calls, [])
        self.assertEqual(proc.stt_queue_depth, 0)
        self.assertEqual(proc.dropped_busy_utterances, 30)

    async def test_utterance_before_response_still_transcribes_normally(self) -> None:
        tr = _FakeTranscriber()
        gate = HalfDuplexGate()  # idle
        results: list[TranscriptionResult] = []
        proc = _capture(tr, gate, on_transcription=results.append)
        await _push(proc, VADUserStartedSpeakingFrame(start_secs=0.2))
        await _push(proc, InputAudioRawFrame(audio=CHUNK * 40, sample_rate=16_000, num_channels=1))
        await _push(proc, VADUserStoppedSpeakingFrame(stop_secs=1.0))
        await asyncio.sleep(0.05)
        self.assertEqual(len(tr.calls), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(proc.dropped_busy_utterances, 0)

    async def test_input_reopens_after_response_finishes(self) -> None:
        tr = _FakeTranscriber()
        gate = HalfDuplexGate()
        proc = _capture(tr, gate)
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.notify_response_finished()
        gate.observe_frame(BotStoppedSpeakingFrame())  # response fully done
        await _push(proc, VADUserStartedSpeakingFrame(start_secs=0.2))
        await _push(proc, InputAudioRawFrame(audio=CHUNK * 40, sample_rate=16_000, num_channels=1))
        await _push(proc, VADUserStoppedSpeakingFrame(stop_secs=1.0))
        await asyncio.sleep(0.05)
        self.assertEqual(len(tr.calls), 1)  # admitted again


# --------------------------------------------------------------------------- #
# whisper subprocess / executor safety
# --------------------------------------------------------------------------- #

class TestWhisperConcurrencySafety(unittest.IsolatedAsyncioTestCase):
    async def test_max_one_decode_at_a_time_under_flood(self) -> None:
        state = {"active": 0, "max": 0, "n": 0}

        class Slow(SpeechTranscriber):
            async def transcribe(self, audio, *, language):
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
                state["n"] += 1
                await asyncio.sleep(0.03)
                state["active"] -= 1
                return TranscriptionResult(text="x", language=language,
                                           audio_duration_s=1.0, wall_latency_s=0.03)

        q = SerialTranscriptionQueue(Slow())
        q.start()
        overflow = 0
        for _ in range(40):
            try:
                q.submit(b"\x01\x00" * 16000, Language.PL)
            except Exception:
                overflow += 1
        await asyncio.sleep(1.0)
        await q.shutdown()
        self.assertEqual(state["max"], 1)  # never two decodes at once
        self.assertEqual(q.max_observed_concurrency, 1)
        self.assertEqual(q.queue_size, 0)  # drained / bounded, not leaking
        self.assertGreater(overflow, 0)  # bounded: excess raises, never silent

    async def test_transcriber_error_does_not_wedge_the_queue(self) -> None:
        calls = {"n": 0}

        class Flaky(SpeechTranscriber):
            async def transcribe(self, audio, *, language):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                return TranscriptionResult(text="ok", language=language,
                                           audio_duration_s=1.0, wall_latency_s=0.01)

        errors: list[Exception] = []
        results: list[TranscriptionResult] = []
        q = SerialTranscriptionQueue(Flaky(), on_result=results.append, on_error=errors.append)
        q.start()
        q.submit(b"\x01\x00" * 100, Language.PL)
        await asyncio.sleep(0.05)
        q.submit(b"\x01\x00" * 100, Language.PL)  # queue must still work
        await asyncio.sleep(0.05)
        await q.shutdown()
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(results), 1)


# --------------------------------------------------------------------------- #
# adapter — no backlog, drops observable, normal PL/EN unchanged
# --------------------------------------------------------------------------- #

class _SlowProvider:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.calls: list[str] = []

    def describe(self):
        from nexa.providers.base import ProviderDescription
        return ProviderDescription(provider_name="slow", model="slow")

    async def generate(self, messages, options, *, cancel_token=None):
        self.calls.append(messages[-2].content if len(messages) >= 2 else messages[-1].content)
        await asyncio.sleep(self.delay_s)
        yield "ok"


class TestAdapterStrictHalfDuplex(unittest.IsolatedAsyncioTestCase):
    async def test_busy_period_stt_results_dropped_no_conv_backlog(self) -> None:
        prov = _SlowProvider(0.3)
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        drops: list[DroppedTurn] = []
        adapter = VoiceConversationAdapter(s, on_turn_dropped=drops.append)
        adapter.start()
        adapter.handle_transcription(_fake_tr("real question", Language.EN))
        await asyncio.sleep(0.02)
        for i in range(15):
            adapter.handle_transcription(_fake_tr(f"tv noise {i}", Language.EN))
        await asyncio.sleep(0.02)
        self.assertEqual(adapter.conversation_queue_depth, 0)
        self.assertEqual(adapter.dropped_busy_turns, 15)
        await asyncio.sleep(0.5)
        await adapter.shutdown()
        self.assertEqual(prov.calls, ["real question"])
        self.assertEqual([t.role for t in s.history], [Role.USER, Role.ASSISTANT])
        self.assertTrue(all(d.reason == DROP_BUSY_RESPONSE_IN_FLIGHT for d in drops))

    async def test_normal_sequential_pl_en_unchanged(self) -> None:
        prov = _SlowProvider(0.02)
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(s)
        adapter.start()
        adapter.handle_transcription(_fake_tr("Co to jest atom?", Language.PL))
        await asyncio.sleep(0.15)
        adapter.handle_transcription(_fake_tr("What is an atom?", Language.EN))
        await asyncio.sleep(0.15)
        adapter.handle_transcription(_fake_tr("A po co sen?", Language.PL))
        await asyncio.sleep(0.15)
        await adapter.shutdown()
        self.assertEqual(prov.calls, ["Co to jest atom?", "What is an atom?", "A po co sen?"])
        self.assertEqual(adapter.dropped_busy_turns, 0)
        self.assertEqual(len(s.history), 6)

    async def test_dropped_turn_never_touches_history(self) -> None:
        prov = _SlowProvider(0.3)
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(s)
        adapter.start()
        adapter.handle_transcription(_fake_tr("real", Language.EN))
        await asyncio.sleep(0.02)
        adapter.handle_transcription(_fake_tr("dropped", Language.EN))
        await asyncio.sleep(0.5)
        await adapter.shutdown()
        self.assertNotIn("dropped", [t.content for t in s.history])

    async def test_one_session_one_provider_no_second_authority(self) -> None:
        prov = FakeModelProvider([["a"], ["b"]])
        s = ConversationSession(provider=prov, system_prompt=SYSTEM_PROMPT)
        adapter = VoiceConversationAdapter(s)
        adapter.start()
        adapter.handle_transcription(_fake_tr("one", Language.PL))
        await asyncio.sleep(0.05)
        adapter.handle_transcription(_fake_tr("two", Language.EN))
        await asyncio.sleep(0.05)
        await adapter.shutdown()
        self.assertIs(adapter._session, s)
        self.assertIs(s.provider, prov)


if __name__ == "__main__":
    unittest.main()
