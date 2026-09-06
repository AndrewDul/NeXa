"""Architectural-invariant tests for M2.3 (ADR-0003 D2).

Unlike `nexa.voice`/`nexa.stt` (which must never import
`nexa.conversation`/`nexa.providers` — see their own architecture tests),
`nexa.voice_conversation`'s whole job IS bridging into `ConversationSession`.
The invariant here is narrower but just as important: this package must
never construct a *second* `ConversationSession`/`ModelProvider`/persona of
its own, must never call a provider (e.g. Ollama) directly, and must never
reference a TTS engine. Static (`ast`) checks — real imports/calls, not text
search or documentation claims.
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

VOICE_CONVERSATION_PKG_DIR = SRC / "nexa" / "voice_conversation"

TTS_KEYWORDS = ("piper", "tts", "kokoro")

# Constructing any of these inside nexa.voice_conversation would mean a
# second conversation authority / second model choice — this package must
# only ever be *handed* an already-built ConversationSession/ModelProvider,
# exactly like apps/nexa_chat.py is.
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


class TestNoSecondConversationAuthority(unittest.TestCase):
    def test_package_never_constructs_a_second_session_or_provider(self) -> None:
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        self.assertGreater(len(py_files), 0, "expected source files under nexa/voice_conversation/")

        offenders = []
        for path in py_files:
            for name in _called_names(path):
                if name in FORBIDDEN_CONSTRUCTOR_CALLS:
                    offenders.append((path.name, name))
        self.assertEqual(
            offenders, [],
            f"nexa.voice_conversation must never construct these itself: {offenders}",
        )

    def test_package_does_not_import_an_llm_client_directly(self) -> None:
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if "ollama" in imported.lower():
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"must not import an LLM client directly: {offenders}")

    def test_no_tts_engine_is_referenced(self) -> None:
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                lowered = imported.lower()
                if any(keyword in lowered for keyword in TTS_KEYWORDS):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"No TTS path exists yet (M2.4+): {offenders}")

    def test_package_does_not_import_bootstrap_or_config(self) -> None:
        """The adapter is *handed* a built ConversationSession — it must not
        build its own from `nexa.bootstrap`/`nexa.config` internally, which
        would let it silently diverge from typed chat's wiring."""
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported in ("nexa.bootstrap", "nexa.config"):
                    offenders.append((path.name, imported))
        self.assertEqual(offenders, [], f"must not build its own session/config: {offenders}")

    def test_package_owns_no_language_policy_of_its_own(self) -> None:
        """M2.3/R0009: response-language mirroring is canonical
        `ConversationSession` policy (`nexa.conversation.language`) — the
        voice adapter must never import it directly or otherwise decide a
        response language itself; it only ever calls `session.send(text)`,
        exactly like typed chat, and the session decides the language the
        same way for both."""
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported in ("nexa.conversation.language", "nexa.conversation"):
                    offenders.append((path.name, imported))
            for name in _called_names(path):
                if name in ("detect_response_language", "language_directive"):
                    offenders.append((path.name, name))
        # `nexa.conversation.session` (for the `ConversationSession` type
        # hint) is allowed and expected — only the *language* module/calls
        # above are forbidden.
        self.assertEqual(offenders, [], f"voice adapter must not own language policy: {offenders}")


if __name__ == "__main__":
    unittest.main()
