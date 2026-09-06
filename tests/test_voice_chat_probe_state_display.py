"""Regression guard for `apps/nexa_voice_chat_probe.py` (M2.3), following the
exact lesson M2.2 learned the hard way (`test_stt_probe_state_display.py`):
only `on_event` (fed directly by the real `VoiceStateMachine`) may ever print
a "voice state: ..." line. The conversation-side callbacks
(`on_user_transcript`/`on_assistant_token`/`on_assistant_complete`/
`on_conversation_error`) must never claim a voice state — the acoustic
VoiceState can have moved on (e.g. to a new `USER_SPEAKING`) while a
conversation turn is still being generated.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "apps" / "nexa_voice_chat_probe.py"

CONVERSATION_CALLBACK_NAMES = {
    "on_user_transcript",
    "on_assistant_token",
    "on_assistant_complete",
    "on_conversation_error",
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


class TestVoiceChatProbeDoesNotFakeVoiceState(unittest.TestCase):
    def test_conversation_callbacks_never_print_a_voice_state_line(self) -> None:
        tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"), filename=str(PROBE_PATH))
        checked = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in CONVERSATION_CALLBACK_NAMES:
                checked += 1
                strings = _string_constants_in(node)
                offending = [s for s in strings if "voice state:" in s.lower()]
                self.assertEqual(
                    offending, [],
                    f"{node.name} must not print a 'voice state: ...' line: {offending}",
                )
        self.assertGreater(checked, 0, "expected to find the conversation-side callbacks")

    def test_conversation_callbacks_never_reference_voice_state_machine(self) -> None:
        tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"), filename=str(PROBE_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in CONVERSATION_CALLBACK_NAMES:
                referenced = _names_referenced_in(node)
                offenders = referenced & {"VoiceState", "VoiceStateMachine", "state_machine"}
                self.assertEqual(
                    offenders, set(),
                    f"{node.name} must not reference voice state machine internals: {offenders}",
                )


if __name__ == "__main__":
    unittest.main()
