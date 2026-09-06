"""Architectural-invariant tests for M2.2 (ADR-0003 D11).

`nexa.stt` is a boundary: it must never import ConversationSession, a
provider/LLM client, or a TTS engine — mirroring `test_voice_architecture.py`'s
checks for `nexa.voice`. Static/structural (``ast``) checks, not text search,
so the module docstrings' own "this does NOT import X" notes don't trip it.
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

STT_PKG_DIR = SRC / "nexa" / "stt"
VOICE_PKG_DIR = SRC / "nexa" / "voice"

FORBIDDEN_IMPORT_PREFIXES = (
    "nexa.conversation",
    "nexa.providers",
    "nexa.bootstrap",
    "nexa.config",
)

TTS_KEYWORDS = ("piper", "tts", "kokoro")


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestSttPackageHasNoForbiddenDependencies(unittest.TestCase):
    def test_stt_package_does_not_import_conversation_or_provider_modules(self) -> None:
        py_files = sorted(STT_PKG_DIR.glob("*.py"))
        self.assertGreater(len(py_files), 0, "expected to find source files under src/nexa/stt/")

        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if any(imported.startswith(prefix) for prefix in FORBIDDEN_IMPORT_PREFIXES):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"M2.2 STT code must not import: {offenders}")

    def test_stt_package_does_not_import_an_llm_client(self) -> None:
        py_files = sorted(STT_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if "ollama" in imported.lower():
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"M2.2 STT code must not import an LLM client: {offenders}")

    def test_no_tts_engine_is_imported_anywhere_in_stt_or_voice(self) -> None:
        offenders = []
        for pkg_dir in (STT_PKG_DIR, VOICE_PKG_DIR):
            for path in sorted(pkg_dir.glob("*.py")):
                for imported in _imported_module_names(path):
                    lowered = imported.lower()
                    if any(keyword in lowered for keyword in TTS_KEYWORDS):
                        offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"No TTS path exists yet (M2.4+): {offenders}")

    def test_no_source_file_calls_conversation_session_directly(self) -> None:
        """Belt-and-suspenders: even an aliased/indirect import couldn't call
        `ConversationSession(...)` without the name `ConversationSession`
        appearing in an ast.Call somewhere in this package."""
        offenders = []
        for pkg_dir in (STT_PKG_DIR, VOICE_PKG_DIR):
            for path in sorted(pkg_dir.glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        name = getattr(func, "id", None) or getattr(func, "attr", None)
                        if name == "ConversationSession":
                            offenders.append(path.name)
        msg = f"no M2.2 file may construct ConversationSession: {offenders}"
        self.assertEqual(offenders, [], msg)


class TestVoiceRuntimeDefaultsPreserveM21Behavior(unittest.TestCase):
    def test_transcriber_and_language_default_to_none(self) -> None:
        from nexa.voice.runtime import VoiceRuntime

        runtime = VoiceRuntime()
        self.assertIsNone(runtime._transcriber)
        self.assertIsNone(runtime._language)

    def test_transcriber_without_language_is_rejected(self) -> None:
        from nexa.stt import Language, TranscriptionResult
        from nexa.voice.runtime import VoiceRuntime

        class _FakeTranscriber:
            async def transcribe(
                self, audio: bytes, *, language: Language
            ) -> TranscriptionResult:
                return TranscriptionResult(
                    text="", language=language, audio_duration_s=0.0, wall_latency_s=0.0
                )

        with self.assertRaises(ValueError):
            VoiceRuntime(transcriber=_FakeTranscriber())


if __name__ == "__main__":
    unittest.main()
