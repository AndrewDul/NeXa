"""Regression test for a real M2.2 hardware-testing finding (2026-09-06):
`apps/nexa_stt_probe.py` printed a fake ``state: LISTENING`` line from the
transcription-result callback, timed to *look* right in the common case —
but a real VAD event (a new ``USER_SPEAKING``) can legitimately arrive
before a slow transcription finishes, at which point that print was a lie
about the actual ``VoiceStateMachine`` state.

Static (``ast``) check: the transcription/error callback functions must
never reference ``VoiceState``/``state_machine``, and must never print a
string containing "state:" — only ``on_event`` (fed directly by the real
``VoiceStateMachine``) is allowed to print state lines.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "apps" / "nexa_stt_probe.py"

CALLBACK_FUNCTION_NAMES = {"on_transcription", "on_transcription_error"}


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


class TestSttProbeDoesNotFakeVoiceState(unittest.TestCase):
    def test_transcription_callbacks_never_print_a_state_line(self) -> None:
        tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"), filename=str(PROBE_PATH))
        checked = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in CALLBACK_FUNCTION_NAMES:
                checked += 1
                strings = _string_constants_in(node)
                offending = [s for s in strings if "state:" in s.lower()]
                self.assertEqual(
                    offending, [],
                    f"{node.name} must not print a 'state: ...' line: {offending}",
                )
        self.assertGreater(checked, 0, "expected to find on_transcription/on_transcription_error")

    def test_transcription_callbacks_never_reference_voice_state_machine(self) -> None:
        tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"), filename=str(PROBE_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in CALLBACK_FUNCTION_NAMES:
                referenced = _names_referenced_in(node)
                offenders = referenced & {"VoiceState", "VoiceStateMachine", "state_machine"}
                self.assertEqual(
                    offenders, set(),
                    f"{node.name} must not reference voice state machine internals: {offenders}",
                )



if __name__ == "__main__":
    unittest.main()
