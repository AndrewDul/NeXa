"""``nexa.core.context.derivation`` — ContextRequest derivation (R0077)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402
from nexa.core.context.derivation import derive_context_request, derive_subject_hints  # noqa: E402
from nexa.providers.base import GenerationOptions  # noqa: E402


class _FakeProvider:
    def describe(self):
        return None


class TestDeriveSubjectHints(unittest.TestCase):
    def test_documented_example(self) -> None:
        self.assertEqual(
            derive_subject_hints("What should I learn next in Python?"),
            ("learn", "next", "python"),
        )

    def test_deterministic(self) -> None:
        text = "Why have I been less productive recently?"
        self.assertEqual(derive_subject_hints(text), derive_subject_hints(text))

    def test_bounded_hint_count(self) -> None:
        text = " ".join(f"wordnumber{i}" for i in range(50))
        hints = derive_subject_hints(text)
        self.assertLessEqual(len(hints), 10)

    def test_no_hardcoded_domain_mapping(self) -> None:
        """The derivation module's CODE (excluding its module docstring,
        which discusses the anti-pattern in prose as an explicit example
        of what NOT to do) must never actually contain a domain-mapping
        assignment."""
        import ast

        source_path = SRC / "nexa" / "core" / "context" / "derivation.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        module_docstring = ast.get_docstring(tree) or ""
        code_without_module_docstring = source.replace(module_docstring, "", 1)
        forbidden_patterns = (
            'domain_hint = "teacher', 'domain_hint = "lifeos', 'domain_hint = "projects',
        )
        for forbidden in forbidden_patterns:
            self.assertNotIn(forbidden, code_without_module_docstring)

    def test_case_normalized(self) -> None:
        self.assertEqual(derive_subject_hints("PYTHON"), derive_subject_hints("python"))

    def test_punctuation_stripped(self) -> None:
        self.assertEqual(
            derive_subject_hints("Python, recursion; mastery!"),
            derive_subject_hints("Python recursion mastery"),
        )

    def test_short_tokens_dropped(self) -> None:
        hints = derive_subject_hints("Is it ok to go to a spa?")
        for hint in hints:
            self.assertGreaterEqual(len(hint), 3)

    def test_duplicates_removed_preserving_order(self) -> None:
        hints = derive_subject_hints("python python recursion python")
        self.assertEqual(hints, ("python", "recursion"))

    def test_no_model_call_pure_function(self) -> None:
        """Pure, deterministic, no network -- calling it many times rapidly
        must never raise or hang (proxy for 'no model/network call')."""
        for _ in range(100):
            derive_subject_hints("What did we decide about NeXa memory?")


class TestDeriveContextRequest(unittest.TestCase):
    def _session_with_turn(self, text: str) -> ConversationSession:
        session = ConversationSession(
            provider=_FakeProvider(), system_prompt="x", options=GenerationOptions()
        )
        session._history.append(ConversationTurn(role=Role.USER, content=text))
        return session

    def test_subject_hints_derived_from_current_turn(self) -> None:
        session = self._session_with_turn("What should I learn next in Python?")
        request = derive_context_request(session)
        self.assertEqual(request.subject_hints, ("learn", "next", "python"))

    def test_domain_hint_stays_none_by_default(self) -> None:
        session = self._session_with_turn("hi")
        request = derive_context_request(session)
        self.assertIsNone(request.domain_hint)

    def test_domain_hint_passthrough_when_supplied(self) -> None:
        session = self._session_with_turn("hi")
        request = derive_context_request(session, domain_hint="projects.nexa")
        self.assertEqual(request.domain_hint, "projects.nexa")

    def test_session_reference_preserved(self) -> None:
        session = self._session_with_turn("hi")
        request = derive_context_request(session)
        self.assertIs(request.session, session)


if __name__ == "__main__":
    unittest.main()
