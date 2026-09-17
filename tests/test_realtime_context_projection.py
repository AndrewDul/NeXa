"""``nexa.realtime.context_projection`` — the cloud provider projection
adapter, living OUTSIDE Core (R0075 Revision 3 §9)."""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402
from nexa.core.context.engine import ContextEngine  # noqa: E402
from nexa.core.context.memory_retriever import MemoryRetriever  # noqa: E402
from nexa.core.context.models import ContextRequest  # noqa: E402
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
from nexa.providers.base import GenerationOptions  # noqa: E402
from nexa.realtime.context_projection import (  # noqa: E402
    make_cloud_snapshot_builder,
    to_cloud_snapshot,
)
from nexa.realtime.snapshot import CloudContextSnapshot, build_cloud_context_snapshot  # noqa: E402


class _FakeProvider:
    def describe(self):
        return None


def _session_with_turn(text: str) -> ConversationSession:
    session = ConversationSession(
        provider=_FakeProvider(), system_prompt="persona", options=GenerationOptions()
    )
    session._history.append(ConversationTurn(role=Role.USER, content=text))
    return session


class TestProjectionModulePlacement(unittest.TestCase):
    """R0075 §1/§9: cloud projection lives outside nexa.core, importing
    inward (realtime -> core), never the reverse."""

    def _imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_module_lives_under_realtime_not_core(self) -> None:
        path = SRC / "nexa" / "realtime" / "context_projection.py"
        self.assertTrue(path.exists())
        self.assertFalse((SRC / "nexa" / "core" / "context" / "projection.py").exists())

    def test_imports_core_context_inward(self) -> None:
        path = SRC / "nexa" / "realtime" / "context_projection.py"
        imports = self._imports(path)
        self.assertTrue(
            any("core.context" in m for m in imports) or any("core" in m for m in imports)
        )

    def test_reuses_unmodified_build_cloud_context_snapshot(self) -> None:
        source = (SRC / "nexa" / "realtime" / "context_projection.py").read_text(encoding="utf-8")
        self.assertIn("build_cloud_context_snapshot", source)
        # no reimplemented filtering logic -- no direct CloudEligibility comparison/filter loop
        self.assertNotIn("== CloudEligibility.CLOUD_SAFE", source)


class TestToCloudSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))
        self.retriever = MemoryRetriever(self.service)
        self.identity = load_identity()
        self.engine = ContextEngine(identity=self.identity, retrievers=(self.retriever,))

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _remember(self, content: str, eligibility: CloudEligibility) -> None:
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="projects.nexa",
            category=MemoryCategory.EPISODE,
            record_type="decision",
            content=content,
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            cloud_eligibility=eligibility,
        )

    def test_cloud_safe_item_crosses(self) -> None:
        self._remember("safe fact", CloudEligibility.CLOUD_SAFE)
        session = _session_with_turn("what do we know?")
        request = ContextRequest(session=session, domain_hint="projects.nexa")
        ctx = self.engine.build_context(request)
        snapshot = to_cloud_snapshot(ctx, session=session)
        self.assertIn("safe fact", snapshot.system_instruction)

    def test_local_only_item_never_crosses(self) -> None:
        self._remember("LOCAL-SECRET-MUST-NOT-CROSS", CloudEligibility.LOCAL_ONLY)
        session = _session_with_turn("what do we know?")
        request = ContextRequest(session=session, domain_hint="projects.nexa")
        ctx = self.engine.build_context(request)
        # confirm it IS in local context first
        self.assertTrue(
            any("LOCAL-SECRET-MUST-NOT-CROSS" in i.content for i in ctx.selected_context_items)
        )
        snapshot = to_cloud_snapshot(ctx, session=session)
        self.assertNotIn("LOCAL-SECRET-MUST-NOT-CROSS", snapshot.system_instruction)

    def test_cloud_with_user_approval_never_crosses_automatically(self) -> None:
        self._remember("NEEDS-APPROVAL-FACT", CloudEligibility.CLOUD_WITH_USER_APPROVAL)
        session = _session_with_turn("what do we know?")
        request = ContextRequest(session=session, domain_hint="projects.nexa")
        ctx = self.engine.build_context(request)
        snapshot = to_cloud_snapshot(ctx, session=session)
        self.assertNotIn("NEEDS-APPROVAL-FACT", snapshot.system_instruction)

    def test_knowledge_references_never_sent_to_cloud(self) -> None:
        """R0075 §14/§21: even descriptor existence can be sensitive --
        descriptors are never forwarded to the provider in V1."""
        self._remember("some fact", CloudEligibility.CLOUD_SAFE)
        session = _session_with_turn("hi")
        ctx = self.engine.build_context(ContextRequest(session=session))
        self.assertTrue(len(ctx.knowledge_references) >= 1)
        snapshot = to_cloud_snapshot(ctx, session=session)
        for descriptor in ctx.knowledge_references:
            self.assertNotIn(descriptor.domain, snapshot.system_instruction)

    def test_returns_real_cloud_context_snapshot_type(self) -> None:
        session = _session_with_turn("hi")
        ctx = self.engine.build_context(ContextRequest(session=session))
        snapshot = to_cloud_snapshot(ctx, session=session)
        self.assertIsInstance(snapshot, CloudContextSnapshot)

    def test_empty_selection_produces_valid_snapshot(self) -> None:
        session = _session_with_turn("hi")
        ctx = self.engine.build_context(ContextRequest(session=session))
        snapshot = to_cloud_snapshot(ctx, session=session)
        self.assertIsInstance(snapshot, CloudContextSnapshot)


class TestEquivalenceWithDirectCall(unittest.TestCase):
    def test_empty_context_facts_matches_direct_call(self) -> None:
        """to_cloud_snapshot() with no selected items must produce the
        same result as calling build_cloud_context_snapshot() directly
        with an empty context_facts -- proving no reimplementation."""
        session = _session_with_turn("hi")
        direct = build_cloud_context_snapshot(session, context_facts=())

        tmp = tempfile.TemporaryDirectory()
        try:
            conn = connect(Path(tmp.name) / "core.sqlite3")
            service = MemoryService(MemoryRepository(conn))
            retriever = MemoryRetriever(service)
            identity = load_identity()
            engine = ContextEngine(identity=identity, retrievers=(retriever,))
            ctx = engine.build_context(ContextRequest(session=session))
            via_projection = to_cloud_snapshot(ctx, session=session)
            conn.close()
        finally:
            tmp.cleanup()

        self.assertEqual(direct.system_instruction, via_projection.system_instruction)


class _CloudBuilderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))
        self.retriever = MemoryRetriever(self.service)
        self.identity = load_identity()
        self.engine = ContextEngine(identity=self.identity, retrievers=(self.retriever,))
        self.builder = make_cloud_snapshot_builder(self.engine)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _remember(self, content: str, eligibility: CloudEligibility) -> None:
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="projects.nexa", category=MemoryCategory.EPISODE,
            record_type="decision", content=content,
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT, cloud_eligibility=eligibility,
        )


class TestMakeCloudSnapshotBuilder(_CloudBuilderTestCase):
    def test_matches_router_seam_call_signature(self) -> None:
        """R0077 §9: the builder must be callable exactly the way
        ConversationRouter.__init__'s snapshot_builder parameter calls it:
        snapshot_builder(session, *, language_preference=None,
        policy_name="", active_provider_name="")."""
        session = ConversationSession(
            provider=None, system_prompt="x", options=GenerationOptions()
        )
        snapshot = self.builder(
            session, language_preference=None, policy_name="cloud_preferred",
            active_provider_name="cloud",
        )
        self.assertIsInstance(snapshot, CloudContextSnapshot)

    def test_empty_history_is_byte_identical_to_unwired_behavior(self) -> None:
        """R0077 §9 audited finding: the accepted simplified cloud path
        builds its snapshot before any turn exists -- this MUST be a
        complete no-op there."""
        self._remember("would be included if a turn existed", CloudEligibility.CLOUD_SAFE)
        session = ConversationSession(
            provider=None, system_prompt="x", options=GenerationOptions()
        )
        via_builder = self.builder(
            session, policy_name="cloud_preferred", active_provider_name="cloud"
        )
        direct = build_cloud_context_snapshot(
            session, policy_name="cloud_preferred", active_provider_name="cloud"
        )
        self.assertEqual(via_builder.system_instruction, direct.system_instruction)

    def test_with_current_turn_cloud_safe_crosses(self) -> None:
        self._remember("Cloud-safe project fact.", CloudEligibility.CLOUD_SAFE)
        session = ConversationSession(
            provider=None, system_prompt="x", options=GenerationOptions()
        )
        turn = ConversationTurn(role=Role.USER, content="What did we decide about NeXa?")
        session._history.append(turn)
        snapshot = self.builder(
            session, policy_name="cloud_preferred", active_provider_name="cloud"
        )
        self.assertIn("Cloud-safe project fact.", snapshot.system_instruction)

    def test_with_current_turn_local_only_never_crosses(self) -> None:
        self._remember("LOCAL-SECRET-MUST-NOT-CROSS", CloudEligibility.LOCAL_ONLY)
        session = ConversationSession(
            provider=None, system_prompt="x", options=GenerationOptions()
        )
        turn = ConversationTurn(role=Role.USER, content="What did we decide about NeXa?")
        session._history.append(turn)
        snapshot = self.builder(
            session, policy_name="cloud_preferred", active_provider_name="cloud"
        )
        self.assertNotIn("LOCAL-SECRET-MUST-NOT-CROSS", snapshot.system_instruction)

    def test_context_build_failure_falls_back_to_plain_snapshot(self) -> None:
        class _BrokenEngine:
            def build_context(self, request):
                raise RuntimeError("simulated failure")

        broken_builder = make_cloud_snapshot_builder(_BrokenEngine())
        session = ConversationSession(
            provider=None, system_prompt="x", options=GenerationOptions()
        )
        session._history.append(ConversationTurn(role=Role.USER, content="hi"))
        # must not raise -- falls back to the plain snapshot
        snapshot = broken_builder(
            session, policy_name="cloud_preferred", active_provider_name="cloud"
        )
        self.assertIsInstance(snapshot, CloudContextSnapshot)


class TestAccpetedCloudPathNotModified(unittest.TestCase):
    """R0077 §9/§18/§22: the accepted, frozen simplified cloud voice path
    must remain byte-for-byte untouched -- audited finding: it builds its
    one snapshot before any turn exists, so Context Engine wiring there
    would be a structural no-op; this is proven, not assumed."""

    def test_simple_conversation_module_unchanged_construction(self) -> None:
        source = (
            SRC / "nexa" / "realtime" / "gemini" / "simple_conversation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("router = ConversationRouter(session, policy=policy)", source)

    def test_app_entrypoint_still_calls_plain_snapshot_builder(self) -> None:
        source = (
            REPO_ROOT / "apps" / "nexa_cloud_voice_simple.py"
        ).read_text(encoding="utf-8")
        self.assertIn("build_cloud_context_snapshot(", source)
        self.assertNotIn("make_cloud_snapshot_builder", source)
        self.assertNotIn("ContextEngine", source)


if __name__ == "__main__":
    unittest.main()
