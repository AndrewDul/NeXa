"""Deterministic tests for the Pipecat-frame -> VoiceState mapping (M2.1).

Uses real Pipecat ``Frame`` instances (they are plain, side-effect-free
objects) but never runs a Pipecat pipeline/processor lifecycle — this is the
"mock at the external boundary" the frame-processor's Pipecat plumbing sits
behind (see `test_voice_hardware_probe.py` for the real-pipeline path).
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
    CancelFrame,
    EndFrame,
    ErrorFrame,
    StartFrame,
    UserSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from nexa.voice.runtime import apply_frame_to_state_machine  # noqa: E402
from nexa.voice.state import VoiceState, VoiceStateMachine  # noqa: E402


class TestApplyFrameToStateMachine(unittest.TestCase):
    def test_start_frame_starts_listening(self) -> None:
        machine = VoiceStateMachine()
        apply_frame_to_state_machine(StartFrame(), machine)
        self.assertEqual(machine.state, VoiceState.LISTENING)

    def test_vad_started_frame_emits_user_speaking(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        apply_frame_to_state_machine(VADUserStartedSpeakingFrame(start_secs=0.2), machine)
        self.assertEqual(machine.state, VoiceState.USER_SPEAKING)

    def test_vad_stopped_frame_emits_end_of_turn_then_listening(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        apply_frame_to_state_machine(VADUserStartedSpeakingFrame(start_secs=0.2), machine)
        apply_frame_to_state_machine(VADUserStoppedSpeakingFrame(stop_secs=0.2), machine)
        self.assertEqual(machine.state, VoiceState.LISTENING)

    def test_end_frame_shuts_down(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        apply_frame_to_state_machine(EndFrame(), machine)
        self.assertEqual(machine.state, VoiceState.IDLE)

    def test_cancel_frame_shuts_down(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        apply_frame_to_state_machine(CancelFrame(), machine)
        self.assertEqual(machine.state, VoiceState.IDLE)

    def test_error_frame_becomes_error_state(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        apply_frame_to_state_machine(ErrorFrame(error="boom"), machine)
        self.assertEqual(machine.state, VoiceState.ERROR)

    def test_unrelated_frame_is_a_no_op(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        apply_frame_to_state_machine(UserSpeakingFrame(), machine)
        self.assertEqual(machine.state, VoiceState.LISTENING)
        self.assertEqual(len(machine.history), 1)  # only start_listening


if __name__ == "__main__":
    unittest.main()
