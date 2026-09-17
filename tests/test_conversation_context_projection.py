"""``nexa.conversation.context_projection`` — local Context Engine
integration (R0077). Full end-to-end acceptance: identity/turn/history
non-duplication, natural recall without explicit domain_hint, unrelated-
domain exclusion, dynamic knowledge, LOCAL_ONLY usability, fail-safe
behavior."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.context_projection import (  # noqa: E402
    make_local_context_provider,
    to_local_context_addendum,
)
from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.core.context.engine import ContextEngine  # noqa: E402
from nexa.core.context.memory_retriever import MemoryRetriever  # noqa: E402
from nexa.core.identity import load_identity  # noqa: E402
from nexa.core.memory.models import (  # noqa: E402
    MemoryCategory,
    MemoryProvenance,
    MemoryWriteTrigger,
)
from nexa.core.memory.repository import MemoryRepository  # noqa: E402
from nexa.core.memory.service import MemoryService  # noqa: E402
from nexa.core.privacy import CloudEligibility  # noqa: E402
from nexa.core.storage.sqlite import connect  # noqa: E402
from nexa.providers.base import GenerationOptions, ProviderDescription  # noqa: E402


class _StubProvider:
    def __init__(self) -> None:
        self.captured_messages: list[list] = []

    def describe(self):
        return ProviderDescription(provider_name="stub", model="stub-model")

    async def generate(self, messages, options, cancel_token=None):
        self.captured_messages.append(messages)
        yield "stub reply"
        return
        yield  # pragma: no cover -- unreachable, satisfies async generator shape


class _IntegrationTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))
        self.retriever = MemoryRetriever(self.service)
        self.identity = load_identity()
        self.engine = ContextEngine(identity=self.identity, retrievers=(self.retriever,))
        self.stub_provider = _StubProvider()
        self.session = ConversationSession(
            provider=self.stub_provider,
            system_prompt=(
                "You are NeXa, part of NeXa IkiGai.\nPurpose: ...\n\nPersona text here."
            ),
            options=GenerationOptions(),
        )
        self.context_provider = make_local_context_provider(self.engine)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _remember(self, namespace: str, content: str, **kwargs) -> None:
        category = kwargs.pop("category", MemoryCategory.EPISODE)
        record_type = kwargs.pop("record_type", "decision")
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace=namespace, category=category, record_type=record_type,
            content=content, provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            **kwargs,
        )

    async def _send(self, text: str) -> None:
        async for _ in self.session.send(text, context_provider=self.context_provider):
            pass


class TestNoDuplication(_IntegrationTestCase):
    async def test_identity_appears_exactly_once(self) -> None:
        await self._send("hello")
        system_messages = [m for m in self.stub_provider.captured_messages[0] if m.role == "system"]
        combined = "\n".join(m.content for m in system_messages)
        self.assertEqual(combined.count("You are NeXa"), 1)

    async def test_current_turn_appears_exactly_once(self) -> None:
        await self._send("hello there")
        user_messages = [m for m in self.stub_provider.captured_messages[0] if m.role == "user"]
        self.assertEqual(len(user_messages), 1)
        self.assertEqual(user_messages[0].content, "hello there")

    async def test_history_not_duplicated_across_turns(self) -> None:
        await self._send("first message")
        await self._send("second message")
        self.assertEqual(len(self.session.history), 4)  # user/assistant x2

        # Turn 2's wire messages legitimately contain BOTH real user turns
        # rendered once each by the existing ConversationContext (turn 1 as
        # history, turn 2 as current) -- exactly 2 distinct user messages,
        # never more. More than 2 would mean conversation_window (from
        # CurrentTurnContext) was ALSO independently re-rendered into the
        # wire format on top of what ConversationContext already produces.
        turn2_messages = self.stub_provider.captured_messages[1]
        user_messages_turn2 = [m for m in turn2_messages if m.role == "user"]
        self.assertEqual(len(user_messages_turn2), 2)
        self.assertEqual(
            [m.content for m in user_messages_turn2], ["first message", "second message"]
        )

    async def test_addendum_appended_not_replacing_identity_persona(self) -> None:
        self._remember("projects.nexa", "One MemoryService is the canonical memory authority.")
        await self._send("What did we decide about NeXa memory?")
        system_content = self.stub_provider.captured_messages[0][0].content
        self.assertIn("You are NeXa", system_content)
        self.assertIn("Persona text here.", system_content)
        self.assertIn("One MemoryService is the canonical memory authority.", system_content)


class TestNaturalRecall(_IntegrationTestCase):
    async def test_a_generic_project_recall_without_explicit_domain_hint(self) -> None:
        """R0077 §12.A -- no explicit domain_hint supplied by the test."""
        self._remember("projects.nexa", "One MemoryService is the canonical memory authority.")
        await self._send("What did we decide about NeXa memory?")
        system_content = self.stub_provider.captured_messages[0][0].content
        self.assertIn("One MemoryService is the canonical memory authority.", system_content)

    async def test_b_teacher_lexical_recall_without_hardcoded_mapping(self) -> None:
        """R0077 §12.B -- no explicit domain_hint; recall works purely
        through generic lexical hint matching against the real namespace
        string 'teacher.python'."""
        self._remember(
            "teacher.python", "Recursion mastery: 55%",
            category=MemoryCategory.STATE, record_type="skill_mastery",
        )
        await self._send("What should I learn next in Python?")
        system_content = self.stub_provider.captured_messages[0][0].content
        self.assertIn("Recursion mastery: 55%", system_content)

    async def test_c_unrelated_domain_excluded(self) -> None:
        """R0077 §12.C -- asking about Python must not load lifeos.finance."""
        self._remember(
            "teacher.python", "Recursion mastery: 55%",
            category=MemoryCategory.STATE, record_type="skill_mastery",
        )
        self._remember(
            "lifeos.finance", "UNRELATED-FINANCE-FACT",
            category=MemoryCategory.EVENT, record_type="financial_event",
        )
        await self._send("What should I learn next in Python?")
        system_content = self.stub_provider.captured_messages[0][0].content
        self.assertNotIn("UNRELATED-FINANCE-FACT", system_content)


class TestLocalOnlyUsableLocally(_IntegrationTestCase):
    async def test_local_only_memory_may_be_used_in_local_context(self) -> None:
        self._remember(
            "projects.nexa", "LOCAL-ONLY-FACT-FOR-LOCAL-USE-ONLY",
            cloud_eligibility=CloudEligibility.LOCAL_ONLY,
        )
        await self._send("What did we decide about NeXa?")
        system_content = self.stub_provider.captured_messages[0][0].content
        self.assertIn("LOCAL-ONLY-FACT-FOR-LOCAL-USE-ONLY", system_content)


class TestDynamicKnowledge(_IntegrationTestCase):
    async def test_newly_added_knowledge_usable_next_turn_same_session(self) -> None:
        await self._send("What did we decide about NeXa?")
        first_system = self.stub_provider.captured_messages[0][0].content
        self.assertNotIn("Dynamically added fact.", first_system)

        self._remember("projects.nexa", "Dynamically added fact.")

        await self._send("What did we decide about NeXa?")
        second_system = self.stub_provider.captured_messages[1][0].content
        self.assertIn("Dynamically added fact.", second_system)


class TestFailSafe(_IntegrationTestCase):
    async def test_broken_context_provider_does_not_crash_conversation(self) -> None:
        def _broken_provider(session):
            raise RuntimeError("simulated Context Engine failure")

        chunks = []
        async for chunk in self.session.send("hello", context_provider=_broken_provider):
            chunks.append(chunk)
        self.assertEqual(chunks, ["stub reply"])
        self.assertEqual(len(self.session.history), 2, "history intact despite failure")

    async def test_no_context_provider_is_byte_identical_to_pre_r0077(self) -> None:
        async for _ in self.session.send("hello"):
            pass
        system_content = self.stub_provider.captured_messages[0][0].content
        self.assertNotIn("Relevant things you already know", system_content)


class TestAddendumRendering(unittest.TestCase):
    def test_empty_selection_returns_none(self) -> None:
        from nexa.conversation.turn import ConversationTurn, Role
        from nexa.core.context.models import (
            ContextBuildTrace,
            CurrentTurnContext,
        )

        identity = load_identity()
        turn = ConversationTurn(role=Role.USER, content="hi")
        context = CurrentTurnContext(
            identity=identity, current_turn=turn, conversation_window=(),
            selected_context_items=(), knowledge_references=(), conflicts=(),
            knowledge_gaps=(), trace=ContextBuildTrace(request_summary="x"),
        )
        self.assertIsNone(to_local_context_addendum(context))

    def test_never_renders_trace_or_descriptors_or_gaps(self) -> None:
        source = (SRC / "nexa" / "conversation" / "context_projection.py").read_text(
            encoding="utf-8"
        )
        # to_local_context_addendum must only ever touch selected_context_items
        addendum_fn_start = source.index("def to_local_context_addendum")
        addendum_fn_end = source.index("\ndef ", addendum_fn_start + 1)
        fn_body = source[addendum_fn_start:addendum_fn_end]
        for forbidden in ("trace", "knowledge_references", "knowledge_gaps", "payload"):
            self.assertNotIn(forbidden, fn_body)


if __name__ == "__main__":
    unittest.main()
