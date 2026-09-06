"""M2.4 `_MicGateFrameProcessor` + `VoiceRuntime` wiring tests.

Exercises the real processor class against real Pipecat frame types (no
running pipeline / TaskManager — `push_frame` is overridden to capture),
matching the pattern `test_voice_utterance_capture.py` established. Proves:

- TTS inactive -> `InputAudioRawFrame` passes through to VAD/STT normally
- TTS active  -> `InputAudioRawFrame` is withheld (never reaches downstream,
  so it can never enter `SerialTranscriptionQueue` or create a
  `ConversationSession` user turn)
- TTS stops (and the reply is done) -> mic audio flows again
- multiple sentence chunks in one reply do not reopen the input between them
- non-audio frames always pass, in both directions

Plus a structural check that the gate stage is inserted right after
`transport.input()` only when a gate is supplied — the M2.1/M2.2 pipeline is
untouched otherwise.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from nexa.voice import HalfDuplexGate, LocalAudioConfig, VoiceRuntime  # noqa: E402
from nexa.voice.runtime import _MicGateFrameProcessor  # noqa: E402

AUDIO = InputAudioRawFrame(audio=b"\x00\x01" * 512, sample_rate=16_000, num_channels=1)


def _audio() -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=b"\x00\x01" * 512, sample_rate=16_000, num_channels=1)


class _GatedProc:
    """A `_MicGateFrameProcessor` with `push_frame` captured."""

    def __init__(self, gate: HalfDuplexGate) -> None:
        self.proc = _MicGateFrameProcessor(gate)
        self.forwarded: list[tuple[Frame, FrameDirection]] = []

        async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            self.forwarded.append((frame, direction))

        self.proc.push_frame = fake_push_frame

    async def send(self, frame: Frame, direction=FrameDirection.DOWNSTREAM) -> None:
        await self.proc.process_frame(frame, direction)

    @property
    def forwarded_frames(self) -> list[Frame]:
        return [f for f, _ in self.forwarded]


class TestMicGateProcessor(unittest.IsolatedAsyncioTestCase):
    async def test_tts_inactive_audio_passes_through(self) -> None:
        g = _GatedProc(HalfDuplexGate())
        await g.send(_audio())
        await g.send(_audio())
        self.assertEqual(len(g.forwarded_frames), 2)
        self.assertTrue(all(isinstance(f, InputAudioRawFrame) for f in g.forwarded_frames))
        self.assertEqual(g.proc.suppressed_frame_count, 0)

    async def test_tts_active_audio_is_withheld(self) -> None:
        gate = HalfDuplexGate()
        g = _GatedProc(gate)
        gate.notify_response_dispatched()
        await g.send(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        # Every mic chunk during playback must be dropped here.
        for _ in range(5):
            await g.send(_audio())
        forwarded_audio = [f for f in g.forwarded_frames if isinstance(f, InputAudioRawFrame)]
        self.assertEqual(forwarded_audio, [])
        self.assertEqual(g.proc.suppressed_frame_count, 5)
        # The BotStartedSpeakingFrame itself is still forwarded (upstream).
        self.assertIn(
            FrameDirection.UPSTREAM,
            [d for f, d in g.forwarded if isinstance(f, BotStartedSpeakingFrame)],
        )

    async def test_no_stt_job_possible_from_echo_then_resumes(self) -> None:
        gate = HalfDuplexGate()
        g = _GatedProc(gate)
        gate.notify_response_dispatched()
        await g.send(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        for _ in range(10):
            await g.send(_audio())  # NeXa's own echo — must go nowhere
        self.assertEqual([f for f in g.forwarded_frames if isinstance(f, InputAudioRawFrame)], [])

        # Reply finishes: generation done + audio stopped -> mic resumes.
        gate.notify_response_finished()
        await g.send(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await g.send(_audio())
        await g.send(_audio())
        self.assertEqual(
            len([f for f in g.forwarded_frames if isinstance(f, InputAudioRawFrame)]), 2
        )

    async def test_multi_sentence_reply_never_reopens_between_chunks(self) -> None:
        gate = HalfDuplexGate()
        g = _GatedProc(gate)
        gate.notify_response_dispatched()

        # sentence 1 plays, then an early inter-chunk stop
        await g.send(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await g.send(_audio())
        await g.send(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        # In the gap between sentence 1 and 2 the mic must still be shut.
        await g.send(_audio())
        await g.send(_audio())

        # sentence 2 plays
        await g.send(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await g.send(_audio())
        await g.send(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

        # nothing forwarded yet — generation never reported complete
        self.assertEqual([f for f in g.forwarded_frames if isinstance(f, InputAudioRawFrame)], [])

        # whole reply completes
        gate.notify_response_finished()
        await g.send(_audio())
        self.assertEqual(
            len([f for f in g.forwarded_frames if isinstance(f, InputAudioRawFrame)]), 1
        )

    async def test_non_audio_frames_pass_while_gate_is_closed_but_audio_does_not(self) -> None:
        gate = HalfDuplexGate()
        g = _GatedProc(gate)
        gate.notify_response_dispatched()
        await g.send(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)  # gate now closed
        self.assertTrue(gate.mic_suppressed)

        await g.send(VADUserStartedSpeakingFrame(start_secs=0.2))
        await g.send(_audio())  # withheld
        await g.send(VADUserStoppedSpeakingFrame(stop_secs=1.0))
        await g.send(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

        kinds = {type(f).__name__ for f in g.forwarded_frames}
        self.assertIn("VADUserStartedSpeakingFrame", kinds)
        self.assertIn("VADUserStoppedSpeakingFrame", kinds)
        self.assertIn("BotStoppedSpeakingFrame", kinds)
        self.assertNotIn("InputAudioRawFrame", kinds)
        self.assertEqual(g.proc.suppressed_frame_count, 1)


class _Recorder:
    def __init__(self, stages) -> None:
        self.stages = list(stages)


class _FakeTransport:
    def __init__(self, *a, **k) -> None:
        self._in = object()
        self._out = object()

    def input(self):
        return self._in

    def output(self):
        return self._out


class TestVoiceRuntimeWiring(unittest.TestCase):
    def _build(self, *, gate):
        rt = VoiceRuntime(LocalAudioConfig(), half_duplex_gate=gate)
        fake_transport = _FakeTransport()
        with mock.patch("nexa.voice.runtime.pyaudio.PyAudio", return_value=object()), \
             mock.patch("nexa.voice.runtime.find_device_index", side_effect=[3, 2]), \
             mock.patch("nexa.voice.runtime.LocalAudioTransport", return_value=fake_transport), \
             mock.patch("nexa.voice.runtime.SileroVADAnalyzer", return_value=object()), \
             mock.patch("nexa.voice.runtime.VADProcessor", return_value="VAD"), \
             mock.patch("nexa.voice.runtime.Pipeline", side_effect=_Recorder) as pipe:
            rt._build_pipeline()
        return rt, pipe.call_args.args[0], fake_transport

    def test_gate_stage_inserted_right_after_input(self) -> None:
        gate = HalfDuplexGate()
        rt, stages, transport = self._build(gate=gate)
        self.assertIs(stages[0], transport.input())
        self.assertIsInstance(stages[1], _MicGateFrameProcessor)
        self.assertEqual(stages[2], "VAD")
        self.assertIs(rt._mic_gate_processor, stages[1])
        self.assertEqual(rt.suppressed_mic_frames, 0)

    def test_no_gate_leaves_pipeline_unchanged(self) -> None:
        rt, stages, transport = self._build(gate=None)
        self.assertIs(stages[0], transport.input())
        self.assertEqual(stages[1], "VAD")  # VAD immediately after input, as in M2.1
        self.assertFalse(
            any(isinstance(s, _MicGateFrameProcessor) for s in stages)
        )
        self.assertIsNone(rt._mic_gate_processor)
        self.assertIsNone(rt.suppressed_mic_frames)


if __name__ == "__main__":
    unittest.main()
