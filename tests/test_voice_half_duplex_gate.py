"""M2.4 `HalfDuplexGate` unit tests — the temporary half-duplex safety
behaviour: while NeXa's own TTS audio is physically playing, the microphone
must not be able to create a new STT/conversation turn (real-hardware
failure: the reSpeaker heard NeXa and her own answer was re-transcribed as
new user turns).

Pure state machine, driven only by real Pipecat playback frames + the
assistant-speech bridge's response-lifecycle notifications. No timers, no
`VoiceState`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    InputAudioRawFrame,
    StartFrame,
)

from nexa.voice import HalfDuplexGate  # noqa: E402
from nexa.voice.state import VoiceState  # noqa: E402


class TestClosedOnlyWhileNexaSpeaks(unittest.TestCase):
    def test_fresh_gate_is_open(self) -> None:
        self.assertFalse(HalfDuplexGate().mic_suppressed)

    def test_dispatch_alone_keeps_mic_open_during_think_window(self) -> None:
        """Committing to answer does not by itself close the mic — capture
        stays open through generation until real audio actually plays."""
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        self.assertFalse(gate.mic_suppressed)

    def test_bot_started_speaking_closes_the_mic(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)

    def test_mic_reopens_after_audio_stops_and_generation_is_done(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.notify_response_finished()
        self.assertTrue(gate.mic_suppressed)  # audio still playing
        gate.observe_frame(BotStoppedSpeakingFrame())
        self.assertFalse(gate.mic_suppressed)

    def test_generation_finishing_after_audio_stops_reopens_the_mic(self) -> None:
        """Reverse order: the last sentence's audio ends before
        `on_assistant_complete` fires."""
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.observe_frame(BotStoppedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)  # generation not confirmed done yet
        gate.notify_response_finished()
        self.assertFalse(gate.mic_suppressed)

    def test_stray_bot_stopped_without_start_does_not_latch_closed(self) -> None:
        gate = HalfDuplexGate()
        gate.observe_frame(BotStoppedSpeakingFrame())
        self.assertFalse(gate.mic_suppressed)


class TestMultiSentenceSafety(unittest.TestCase):
    """One assistant reply can produce more than one BotStarted/BotStopped
    pair (early inter-chunk `TTSStoppedFrame`). The mic must NOT reopen
    between two sentence chunks of the same answer."""

    def test_mic_stays_closed_across_inter_sentence_gap(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()

        # sentence 1
        gate.observe_frame(BotStartedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)
        gate.observe_frame(BotStoppedSpeakingFrame())
        # generation NOT finished -> this is only a gap between chunks
        self.assertTrue(gate.mic_suppressed)

        # sentence 2
        gate.observe_frame(BotStartedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)
        gate.observe_frame(BotStoppedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)

        # sentence 3, and only now does generation complete
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.notify_response_finished()
        self.assertTrue(gate.mic_suppressed)  # sentence 3 still playing
        gate.observe_frame(BotStoppedSpeakingFrame())
        self.assertFalse(gate.mic_suppressed)  # whole reply done -> mic resumes

    def test_never_opens_at_any_point_mid_reply(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        states: list[bool] = []
        # interleave many chunk boundaries; generation completes only at the end
        for _ in range(4):
            gate.observe_frame(BotStartedSpeakingFrame())
            states.append(gate.mic_suppressed)
            gate.observe_frame(BotStoppedSpeakingFrame())
            states.append(gate.mic_suppressed)
        self.assertTrue(all(states), f"mic reopened mid-reply: {states}")
        gate.notify_response_finished()
        self.assertFalse(gate.mic_suppressed)

    def test_next_turn_rearms_after_a_completed_reply(self) -> None:
        gate = HalfDuplexGate()
        # turn 1
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.notify_response_finished()
        gate.observe_frame(BotStoppedSpeakingFrame())
        self.assertFalse(gate.mic_suppressed)
        # turn 2 must behave exactly like a fresh reply
        gate.notify_response_dispatched()
        self.assertFalse(gate.mic_suppressed)
        gate.observe_frame(BotStartedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)
        gate.observe_frame(BotStoppedSpeakingFrame())
        gate.notify_response_finished()
        self.assertFalse(gate.mic_suppressed)


class TestNeverLatchedShutOnStop(unittest.TestCase):
    def test_end_frame_releases_the_gate(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        self.assertTrue(gate.mic_suppressed)
        gate.observe_frame(EndFrame())
        self.assertFalse(gate.mic_suppressed)

    def test_cancel_frame_releases_the_gate(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.observe_frame(CancelFrame())
        self.assertFalse(gate.mic_suppressed)

    def test_error_frame_releases_the_gate(self) -> None:
        gate = HalfDuplexGate()
        gate.notify_response_dispatched()
        gate.observe_frame(BotStartedSpeakingFrame())
        gate.observe_frame(ErrorFrame(error="boom"))
        self.assertFalse(gate.mic_suppressed)


class TestNoFakeVoiceState(unittest.TestCase):
    """The gate is a separate event-backed boolean; the acoustic VoiceState
    enum must not gain a SPEAKING member and the gate must not touch it."""

    def test_voice_state_has_no_speaking_member(self) -> None:
        self.assertNotIn("SPEAKING", VoiceState.__members__)

    def test_gate_module_does_not_reference_voice_state(self) -> None:
        import ast

        src = (SRC / "nexa" / "voice" / "gate.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {
            getattr(n, "id", None) or getattr(n, "attr", None)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Name, ast.Attribute))
        }
        self.assertNotIn("VoiceState", names)
        self.assertNotIn("VoiceStateMachine", names)

    def test_ignores_unrelated_frames(self) -> None:
        gate = HalfDuplexGate()
        gate.observe_frame(StartFrame())
        gate.observe_frame(
            InputAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)
        )
        self.assertFalse(gate.mic_suppressed)


class TestNoBargeInLogicIntroduced(unittest.TestCase):
    """M2.4 half-duplex is a *pause*, not barge-in. The gate and its
    processor must never cancel/interrupt TTS, LLM generation, or the
    ConversationSession, and must never synthesise interruption frames —
    that is all M2.5."""

    FORBIDDEN = {
        "StartInterruptionFrame",
        "StopInterruptionFrame",
        "BotInterruptionFrame",
        "EmulateUserStartedSpeakingFrame",
        "EmulateUserStoppedSpeakingFrame",
        "InterruptionFrame",
        "InterruptionTaskFrame",
        "cancel",
        "cancel_task",
        "handle_interruptions",
    }

    def _names(self, rel_path: str) -> set[str]:
        import ast

        src = (SRC / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(src)
        return {
            getattr(n, "id", None) or getattr(n, "attr", None)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Name, ast.Attribute))
        }

    def test_gate_module_has_no_interruption_machinery(self) -> None:
        offenders = self.FORBIDDEN & self._names("nexa/voice/gate.py")
        self.assertEqual(offenders, set(), f"gate.py must not do barge-in: {offenders}")

    def test_mic_gate_processor_has_no_interruption_machinery(self) -> None:
        import ast

        src = (SRC / "nexa/voice/runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "_MicGateFrameProcessor"
        )
        names = {
            getattr(n, "id", None) or getattr(n, "attr", None)
            for n in ast.walk(cls)
            if isinstance(n, (ast.Name, ast.Attribute))
        }
        offenders = self.FORBIDDEN & names
        self.assertEqual(
            offenders, set(), f"_MicGateFrameProcessor must not do barge-in: {offenders}"
        )


if __name__ == "__main__":
    unittest.main()
