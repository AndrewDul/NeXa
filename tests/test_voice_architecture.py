"""Architectural-invariant tests for M2.1 (ADR-0003).

M2.1 must not call ConversationSession, must not create a second
conversation authority, and its configuration must be explicit and typed.
These are exactly the invariants a later, careless change could silently
break without a runtime error — hence a static/structural check here (real
imports only, via ``ast`` — not a raw text search, which would also flag the
module docstrings' own "this does NOT call ConversationSession" notes).
"""

from __future__ import annotations

import ast
import dataclasses
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

VOICE_PKG_DIR = SRC / "nexa" / "voice"

FORBIDDEN_IMPORT_PREFIXES = (
    "nexa.conversation",
    "nexa.providers",
    "nexa.bootstrap",
    "nexa.config",  # the persona/provider config loader, not this package's own config.py
)


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestNoSecondConversationAuthority(unittest.TestCase):
    """No M2.1 source file may *import* ConversationSession/ModelProvider/
    persona/provider modules — voice is plumbing only in this substage.
    (Prose in docstrings explaining this constraint is fine and expected.)"""

    def test_voice_package_does_not_import_conversation_or_provider_modules(self) -> None:
        py_files = sorted(VOICE_PKG_DIR.glob("*.py"))
        self.assertGreater(len(py_files), 0, "expected to find source files under src/nexa/voice/")

        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if any(imported.startswith(prefix) for prefix in FORBIDDEN_IMPORT_PREFIXES):
                    offenders.append((path.name, imported))

        self.assertEqual(offenders, [], f"M2.1 voice code must not import: {offenders}")

    def test_voice_package_does_not_import_ollama_http_or_llm_libraries(self) -> None:
        py_files = sorted(VOICE_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if "ollama" in imported.lower():
                    offenders.append((path.name, imported))
        msg = f"M2.1 voice code must not import an LLM client: {offenders}"
        self.assertEqual(offenders, [], msg)



class TestConfigIsExplicitAndTyped(unittest.TestCase):
    def test_local_audio_config_fields_are_typed_and_explicit(self) -> None:
        from nexa.voice.config import LocalAudioConfig

        fields = {f.name: f.type for f in dataclasses.fields(LocalAudioConfig)}
        self.assertEqual(
            fields,
            {
                "input_device_name": "str",
                "output_device_name": "str",
                # M2.6B.4N / R0053 — ALSA card backing output_device_name,
                # for reading the audible path's own real mixer gain.
                "output_alsa_mixer_card": "str",
                "sample_rate": "int",
                "channels": "int",
                # M2.5B — production barge-in switch; default False = R0026.
                "bargein_enabled": "bool",
            },
        )

    def test_local_audio_config_is_frozen(self) -> None:
        from nexa.voice.config import LocalAudioConfig

        config = LocalAudioConfig()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.sample_rate = 8000  # type: ignore[misc]

    def test_explicit_values_are_used_verbatim_not_silently_defaulted(self) -> None:
        from nexa.voice.config import LocalAudioConfig

        config = LocalAudioConfig(
            input_device_name="test-mic",
            output_device_name="test-speaker",
            sample_rate=8000,
            channels=2,
        )
        self.assertEqual(config.input_device_name, "test-mic")
        self.assertEqual(config.output_device_name, "test-speaker")
        self.assertEqual(config.sample_rate, 8000)
        self.assertEqual(config.channels, 2)


if __name__ == "__main__":
    unittest.main()
