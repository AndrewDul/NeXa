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
from nexa.realtime.context_projection import to_cloud_snapshot  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
