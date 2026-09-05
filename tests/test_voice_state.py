"""Deterministic tests for the M2.1 voice state machine (ADR-0003).

No audio, no Pipecat, no hardware — ``VoiceStateMachine`` is pure state
logic. Hardware-dependent behavior is covered separately by the opt-in
`tests/test_voice_hardware_probe.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.voice.state import VoiceEvent, VoiceState, VoiceStateMachine  # noqa: E402


class TestVoiceStateMachine(unittest.TestCase):
    def test_initial_state_is_idle(self) -> None:
        machine = VoiceStateMachine()
        self.assertEqual(machine.state, VoiceState.IDLE)
        self.assertEqual(machine.history, ())

    def test_start_listening_transitions_idle_to_listening(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        self.assertEqual(machine.state, VoiceState.LISTENING)

    def test_speech_started_emits_user_speaking(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.speech_started()
        self.assertEqual(machine.state, VoiceState.USER_SPEAKING)

    def test_speech_stopped_emits_end_of_turn_then_listening(self) -> None:
        events: list[VoiceEvent] = []
        machine = VoiceStateMachine(on_event=events.append)
        machine.start_listening()
        machine.speech_started()
        machine.speech_stopped()

        self.assertEqual(machine.state, VoiceState.LISTENING)
        # END_OF_TURN must actually have been emitted as its own transition,
        # not skipped straight from USER_SPEAKING to LISTENING.
        to_states = [e.to_state for e in events]
        self.assertEqual(
            to_states,
            [
                VoiceState.LISTENING,
                VoiceState.USER_SPEAKING,
                VoiceState.END_OF_TURN,
                VoiceState.LISTENING,
            ],
        )

    def test_transitions_are_ordered_correctly_in_history(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.speech_started()
        machine.speech_stopped()
        machine.speech_started()
        machine.speech_stopped()

        pairs = [(e.from_state, e.to_state) for e in machine.history]
        self.assertEqual(
            pairs,
            [
                (VoiceState.IDLE, VoiceState.LISTENING),
                (VoiceState.LISTENING, VoiceState.USER_SPEAKING),
                (VoiceState.USER_SPEAKING, VoiceState.END_OF_TURN),
                (VoiceState.END_OF_TURN, VoiceState.LISTENING),
                (VoiceState.LISTENING, VoiceState.USER_SPEAKING),
                (VoiceState.USER_SPEAKING, VoiceState.END_OF_TURN),
                (VoiceState.END_OF_TURN, VoiceState.LISTENING),
            ],
        )

    def test_runtime_returns_to_listening_after_end_of_turn(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.speech_started()
        machine.speech_stopped()
        self.assertEqual(machine.state, VoiceState.LISTENING)

    def test_duplicate_speech_started_does_not_corrupt_state(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.speech_started()
        machine.speech_started()  # duplicate VAD start while already speaking
        machine.speech_started()
        self.assertEqual(machine.state, VoiceState.USER_SPEAKING)
        # Only one real transition into USER_SPEAKING should be recorded.
        speaking_transitions = [
            e for e in machine.history if e.to_state == VoiceState.USER_SPEAKING
        ]
        self.assertEqual(len(speaking_transitions), 1)

    def test_noisy_speech_stopped_without_start_is_ignored(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.speech_stopped()  # no matching speech_started
        self.assertEqual(machine.state, VoiceState.LISTENING)
        self.assertEqual(len(machine.history), 1)  # only the initial start_listening

    def test_speech_started_while_idle_is_ignored(self) -> None:
        machine = VoiceStateMachine()
        machine.speech_started()  # never started listening
        self.assertEqual(machine.state, VoiceState.IDLE)
        self.assertEqual(machine.history, ())

    def test_shutdown_returns_to_idle_from_any_state(self) -> None:
        for setup in [
            lambda m: None,
            lambda m: m.start_listening(),
            lambda m: (m.start_listening(), m.speech_started()),
        ]:
            with self.subTest(setup=setup):
                machine = VoiceStateMachine()
                setup(machine)
                machine.shutdown()
                self.assertEqual(machine.state, VoiceState.IDLE)

    def test_errors_become_error_state_explicitly(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.speech_started()
        machine.error("audio device disconnected")
        self.assertEqual(machine.state, VoiceState.ERROR)
        self.assertEqual(machine.history[-1].reason, "audio device disconnected")

    def test_start_listening_recovers_from_error(self) -> None:
        machine = VoiceStateMachine()
        machine.start_listening()
        machine.error("transient failure")
        machine.start_listening()
        self.assertEqual(machine.state, VoiceState.LISTENING)

    def test_on_event_callback_invoked_for_every_transition(self) -> None:
        events: list[VoiceEvent] = []
        machine = VoiceStateMachine(on_event=events.append)
        machine.start_listening()
        machine.speech_started()
        machine.speech_stopped()
        self.assertEqual(len(events), 4)  # listen, speaking, end_of_turn, listen
        self.assertTrue(all(isinstance(e, VoiceEvent) for e in events))


if __name__ == "__main__":
    unittest.main()
