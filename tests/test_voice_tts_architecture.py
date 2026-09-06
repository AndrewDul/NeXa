"""Architectural-invariant tests for M2.4 (ADR-0003 D6).

Mirrors M2.1/M2.2/M2.3's own architecture tests. Static (`ast`) checks —
real imports/calls, not text search or documentation claims.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

VOICE_TTS_PKG_DIR = SRC / "nexa" / "voice_tts"
TTS_PKG_DIR = SRC / "nexa" / "tts"
VOICE_PKG_DIR = SRC / "nexa" / "voice"
STT_PKG_DIR = SRC / "nexa" / "stt"
VOICE_CONVERSATION_PKG_DIR = SRC / "nexa" / "voice_conversation"

FORBIDDEN_CONSTRUCTOR_CALLS = (
    "ConversationSession",
    "LocalModelProvider",
    "LlamaServerProvider",
    "PersonaConfig",
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


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name:
                names.add(name)
    return names


class TestTtsPackageHasNoForbiddenDependencies(unittest.TestCase):
    """`nexa.tts` is the Piper-HTTP-process boundary — it must never import
    Pipecat, `nexa.conversation`, or `piper` itself in-process."""

    def test_tts_package_does_not_import_piper_in_process(self) -> None:
        py_files = sorted(TTS_PKG_DIR.glob("*.py"))
        self.assertGreater(len(py_files), 0, "expected source files under nexa/tts/")
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported == "piper" or imported.startswith("piper."):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"nexa.tts must never import piper in-process: {offenders}")

    def test_tts_package_does_not_import_pipecat_or_conversation(self) -> None:
        py_files = sorted(TTS_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported == "pipecat" or imported.startswith("pipecat."):
                    offenders.append((path.name, imported))
                if imported == "nexa.conversation" or imported.startswith("nexa.conversation."):
                    offenders.append((path.name, imported))
        msg = f"nexa.tts must stay a pure Piper-HTTP boundary: {offenders}"
        self.assertEqual(offenders, [], msg)


class TestVoiceTtsPackageOwnsNoConversationAuthority(unittest.TestCase):
    """`nexa.voice_tts` bridges Pipecat/TTS to the existing assistant-text
    stream — it must never construct a second `ConversationSession`/
    `ModelProvider`/persona, never import `nexa.bootstrap`/`nexa.config`,
    never import an LLM client directly, and never own its own
    response-language policy (it must call the canonical
    `nexa.conversation.language` function, never reimplement it)."""

    def test_package_never_constructs_a_second_session_or_provider(self) -> None:
        py_files = sorted(VOICE_TTS_PKG_DIR.glob("*.py"))
        self.assertGreater(len(py_files), 0, "expected source files under nexa/voice_tts/")
        offenders = []
        for path in py_files:
            for name in _called_names(path):
                if name in FORBIDDEN_CONSTRUCTOR_CALLS:
                    offenders.append((path.name, name))
        self.assertEqual(offenders, [], f"must never construct these itself: {offenders}")

    def test_package_does_not_import_bootstrap_or_config(self) -> None:
        py_files = sorted(VOICE_TTS_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported in ("nexa.bootstrap", "nexa.config"):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"must not build its own session/config: {offenders}")

    def test_package_does_not_import_an_llm_client_directly(self) -> None:
        py_files = sorted(VOICE_TTS_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if "ollama" in imported.lower():
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"must not import an LLM client directly: {offenders}")

    def test_package_never_reimplements_language_detection(self) -> None:
        """It may (and must) *call* `nexa.conversation.language`'s function
        — this only forbids a second, independent classifier."""
        py_files = sorted(VOICE_TTS_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in (
                    "detect_response_language",
                    "detect_language",
                ):
                    offenders.append((path.name, node.name))
        self.assertEqual(offenders, [], f"must not define its own language classifier: {offenders}")

    def test_package_imports_the_canonical_language_function(self) -> None:
        """Positive check: the bridge really does call the one canonical
        function, not silently skip language routing."""
        bridge_path = VOICE_TTS_PKG_DIR / "bridge.py"
        imported = _imported_module_names(bridge_path)
        self.assertIn("nexa.conversation.language", imported)


class TestNoSecondLlmOrTtsAuthorityAcrossVoicePackages(unittest.TestCase):
    """`nexa.voice`/`nexa.stt`/`nexa.voice_conversation` must remain exactly
    as isolated as their own M2.1/M2.2/M2.3 architecture tests already
    established — M2.4 must not have weakened any of them to fit a TTS
    dependency in. This is a regression guard, not new policy."""

    def test_voice_package_still_does_not_import_conversation_or_tts(self) -> None:
        py_files = sorted(VOICE_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported.startswith("nexa.conversation") or imported == "nexa.tts" or (
                    imported.startswith("nexa.tts.")
                ):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"nexa.voice must stay TTS/conversation-free: {offenders}")

    def test_stt_package_still_does_not_import_conversation_or_tts(self) -> None:
        py_files = sorted(STT_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported.startswith("nexa.conversation") or imported == "nexa.tts" or (
                    imported.startswith("nexa.tts.")
                ):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"nexa.stt must stay TTS/conversation-free: {offenders}")

    def test_voice_conversation_package_still_does_not_import_tts(self) -> None:
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                lowered = imported.lower()
                if any(keyword in lowered for keyword in ("piper", "tts", "kokoro")):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"nexa.voice_conversation must stay TTS-free: {offenders}")


if __name__ == "__main__":
    unittest.main()
