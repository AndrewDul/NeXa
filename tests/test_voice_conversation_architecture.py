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
        """M2.3/R0009 + M2.4B.5 (ADR-0003 Amendment 1): the low-level
        PL/EN detection primitives (`nexa.conversation.language` —
        `detect_response_language` / `language_directive`) stay canonical
        `ConversationSession` policy; the voice adapter must never import
        that module or call those primitives. It MAY invoke the dedicated
        `nexa.conversation.response_language.ResponseLanguageResolver`
        authority (B.5) and pass its result to
        `session.send(response_language=…)` — that resolver is defined in
        `nexa.conversation`, not reimplemented here."""
        py_files = sorted(VOICE_CONVERSATION_PKG_DIR.glob("*.py"))
        offenders = []
        for path in py_files:
            for imported in _imported_module_names(path):
                if imported in ("nexa.conversation.language", "nexa.conversation"):
                    offenders.append((path.name, imported))
            for name in _called_names(path):
                if name in ("detect_response_language", "language_directive"):
                    offenders.append((path.name, name))
        # `nexa.conversation.session` (ConversationSession type hint) and
        # `nexa.conversation.response_language` (the B.5 resolver authority)
        # are allowed — only the low-level *language* module/calls above are
        # forbidden.
        self.assertEqual(offenders, [], f"voice adapter must not own language policy: {offenders}")

    def test_response_language_resolver_is_a_conversation_authority(self) -> None:
        """B.5: the adapter invokes the resolver but the resolver itself
        lives in `nexa.conversation` (one authority), not in this package."""
        self.assertFalse(
            (VOICE_CONVERSATION_PKG_DIR / "response_language.py").exists(),
            "ResponseLanguageResolver must be defined in nexa.conversation, not duplicated here",
        )
        from nexa.conversation.response_language import ResponseLanguageResolver
        self.assertEqual(
            ResponseLanguageResolver.__module__, "nexa.conversation.response_language"
        )


if __name__ == "__main__":
    unittest.main()
