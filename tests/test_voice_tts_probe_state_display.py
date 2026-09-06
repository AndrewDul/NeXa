"""Regression guard for `apps/nexa_voice_tts_probe.py` (M2.4), extending
the same lesson M2.2/M2.3 learned the hard way
(`test_stt_probe_state_display.py`, `test_voice_chat_probe_state_display.py`):
only `on_event` (fed directly by the real `VoiceStateMachine`) may ever
print a "voice state: ..." line. The TTS-status callbacks
(`on_tts_started`/`on_tts_first_audio`/`on_tts_stopped`/`on_tts_error`) and
the conversation-side callbacks must never claim a voice state — the
acoustic `VoiceState` can have moved on while TTS is still speaking a
previous turn.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "apps" / "nexa_voice_tts_probe.py"

NON_VOICE_STATE_FUNCTION_NAMES = {
    "on_user_transcript",
    "on_assistant_token",
    "on_assistant_complete",
    "on_conversation_error",
    "on_tts_started",
    "on_tts_first_audio",
    "on_tts_stopped",
    "on_tts_error",
    "_report_turn_timings",
}


def _string_constants_in(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _names_referenced_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


class TestVoiceTtsProbeDoesNotFakeVoiceState(unittest.TestCase):
    def test_non_voice_state_callbacks_never_print_a_voice_state_line(self) -> None:
        tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"), filename=str(PROBE_PATH))
        checked = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in NON_VOICE_STATE_FUNCTION_NAMES:
                checked += 1
                strings = _string_constants_in(node)
                offending = [s for s in strings if "voice state:" in s.lower()]
                self.assertEqual(
                    offending, [],
                    f"{node.name} must not print a 'voice state: ...' line: {offending}",
                )
        self.assertGreater(checked, 0, "expected to find the non-voice-state callbacks")

    def test_non_voice_state_callbacks_never_reference_voice_state_machine(self) -> None:
        tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"), filename=str(PROBE_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in NON_VOICE_STATE_FUNCTION_NAMES:
                referenced = _names_referenced_in(node)
                offenders = referenced & {"VoiceState", "VoiceStateMachine", "state_machine"}
                self.assertEqual(
                    offenders, set(),
                    f"{node.name} must not reference voice state machine internals: {offenders}",
                )


if __name__ == "__main__":
    unittest.main()
