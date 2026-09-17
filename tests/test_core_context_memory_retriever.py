"""``nexa.core.context.memory_retriever.MemoryRetriever`` (R0075)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.session import ConversationSession  # noqa: E402
from nexa.core.context.memory_retriever import MemoryRetriever  # noqa: E402
from nexa.core.context.models import (  # noqa: E402
    ContextBudget,
    ContextRequest,
    KnowledgeDescriptorKind,
    RetrievalOutcome,
    TemporalIntent,
)
from nexa.core.context.retrieval import RetrievalQuery  # noqa: E402
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


class _FakeProvider:
    def describe(self):
        return None


def _session() -> ConversationSession:
    return ConversationSession(
        provider=_FakeProvider(), system_prompt="x", options=GenerationOptions()
    )


class _RetrieverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))
        self.retriever = MemoryRetriever(self.service)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _remember(self, namespace: str, content: str, **kwargs) -> None:
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST, namespace=namespace,
            category=kwargs.pop("category", MemoryCategory.FACT),
            record_type=kwargs.pop("record_type", "thing"), content=content,
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT, **kwargs,
        )


class TestDomainDiscovery(_RetrieverTestCase):
    def test_no_hints_enumerates_domains(self) -> None:
        self._remember("teacher.python", "x")
        request = ContextRequest(session=_session())
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertTrue(any(d.domain == "teacher.python" for d in descriptors))

    def test_exact_domain_hint_matches_leaf(self) -> None:
        self._remember("teacher.python", "x")
        request = ContextRequest(session=_session(), domain_hint="teacher.python")
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertEqual([d.domain for d in descriptors], ["teacher.python"])
        self.assertEqual(descriptors[0].kind, KnowledgeDescriptorKind.DOMAIN)

    def test_group_synthesized_for_single_child(self) -> None:
        """R0075 §5: a DOMAIN_GROUP is synthesized for even one child."""
        self._remember("lifeos.sleep", "x")
        request = ContextRequest(session=_session(), domain_hint="lifeos")
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        groups = [d for d in descriptors if d.kind is KnowledgeDescriptorKind.DOMAIN_GROUP]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].child_domains, ("lifeos.sleep",))

    def test_group_synthesized_for_multiple_children_sorted(self) -> None:
        self._remember("teacher.python", "x")
        self._remember("teacher.math", "y")
        request = ContextRequest(session=_session(), domain_hint="teacher")
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        groups = [d for d in descriptors if d.kind is KnowledgeDescriptorKind.DOMAIN_GROUP]
        self.assertEqual(groups[0].child_domains, ("teacher.math", "teacher.python"))

    def test_flat_namespace_gets_no_group(self) -> None:
        self._remember("core", "x")
        request = ContextRequest(session=_session())
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertFalse(any(d.kind is KnowledgeDescriptorKind.DOMAIN_GROUP and d.domain == "core"
                              for d in descriptors))

    def test_real_domain_and_synthesized_group_no_id_collision(self) -> None:
        """R0075 §4/§8/§15: a real namespace literally named 'lifeos'
        coexists with a synthesized group for the same prefix."""
        self._remember("lifeos", "flat one")
        self._remember("lifeos.sleep", "child one")
        request = ContextRequest(session=_session(), domain_hint="lifeos")
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        ids = {d.id for d in descriptors}
        self.assertIn("memory:domain:lifeos", ids)
        self.assertIn("memory:domain_group:lifeos", ids)
        self.assertEqual(len(ids), 2, "no collision -- both present distinctly")
        by_id = {d.id: d for d in descriptors}
        self.assertEqual(by_id["memory:domain:lifeos"].kind, KnowledgeDescriptorKind.DOMAIN)
        self.assertEqual(
            by_id["memory:domain_group:lifeos"].kind, KnowledgeDescriptorKind.DOMAIN_GROUP
        )

    def test_descriptor_ids_stable_across_independent_instances(self) -> None:
        """R0075 §4: deterministic across a fresh process/interpreter --
        simulated here via two independent MemoryRetriever/service pairs
        against the same underlying database file."""
        self._remember("projects.nexa", "x")
        self.conn.commit()
        req = ContextRequest(session=_session(), domain_hint="projects.nexa")
        first = self.retriever.describe_available_knowledge(
            domain_hint=req.domain_hint, subject_hints=req.subject_hints
        )

        other_conn = connect(Path(self._tmp.name) / "core.sqlite3")
        try:
            other_service = MemoryService(MemoryRepository(other_conn))
            other_retriever = MemoryRetriever(other_service)
            second = other_retriever.describe_available_knowledge(
                domain_hint=req.domain_hint, subject_hints=req.subject_hints
            )
        finally:
            other_conn.close()

        self.assertEqual([d.id for d in first], [d.id for d in second])

    def test_subject_hint_substring_match(self) -> None:
        self._remember("projects.nexa", "x")
        request = ContextRequest(session=_session(), subject_hints=("nexa",))
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertTrue(any(d.domain == "projects.nexa" for d in descriptors))

    def test_paraphrase_that_does_not_match_text_finds_nothing(self) -> None:
        """R0075 §7/§19 (Revision 2 acceptance): V1 topic discovery is a
        plain substring match, not semantic search -- a synonym/paraphrase
        that never appears in any domain/summary string matches nothing,
        even though semantically-relevant content exists."""
        self._remember("projects.nexa", "We chose one MemoryService authority.")
        request = ContextRequest(session=_session(), subject_hints=("architectural cohesion",))
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertEqual(descriptors, ())

    def test_descriptor_privacy_is_most_restrictive_of_members(self) -> None:
        self._remember("mixed.ns", "a", cloud_eligibility=CloudEligibility.CLOUD_SAFE)
        self._remember("mixed.ns", "b", cloud_eligibility=CloudEligibility.LOCAL_ONLY)
        request = ContextRequest(session=_session(), domain_hint="mixed.ns")
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertEqual(descriptors[0].cloud_eligibility, CloudEligibility.LOCAL_ONLY)

    def test_discovery_is_bounded_by_scan_limit_not_unbounded(self) -> None:
        """The retriever itself is not the layer that enforces
        ContextBudget.max_knowledge_references (that is ContextEngine's
        job, so it can compute an accurate omitted_descriptor_count) --
        but discovery must still be bounded, never an unbounded scan."""
        for i in range(300):
            self._remember(f"ns{i}", f"x{i}")
        request = ContextRequest(session=_session())
        descriptors = self.retriever.describe_available_knowledge(
            domain_hint=request.domain_hint, subject_hints=request.subject_hints
        )
        self.assertLess(len(descriptors), 300, "discovery must be bounded, not unbounded")


class TestRetrieve(_RetrieverTestCase):
    def test_ok_outcome_with_items(self) -> None:
        self._remember("projects.nexa", "decision A")
        result = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="memory:domain:projects.nexa", domain="projects.nexa"),
            ContextBudget(),
        )
        self.assertEqual(result.outcome, RetrievalOutcome.OK)
        self.assertEqual(result.items[0].content, "decision A")

    def test_no_match_outcome_for_empty_domain(self) -> None:
        result = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="memory:domain:nothing.here", domain="nothing.here"),
            ContextBudget(),
        )
        self.assertEqual(result.outcome, RetrievalOutcome.NO_MATCH)
        self.assertEqual(result.items, ())

    def test_context_item_carries_domain_record_type_scope(self) -> None:
        self._remember(
            "teacher.python", "mastery", category=MemoryCategory.STATE,
            record_type="skill_mastery", scope_type="skill", scope_id="recursion",
        )
        result = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="d", domain="teacher.python"), ContextBudget()
        )
        item = result.items[0]
        self.assertEqual(item.domain, "teacher.python")
        self.assertEqual(item.record_type, "skill_mastery")
        self.assertEqual(item.scope_type, "skill")
        self.assertEqual(item.scope_id, "recursion")
        self.assertEqual(item.category, "state")

    def test_context_item_never_carries_payload_or_evidence_fields(self) -> None:
        result = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="d", domain="does.not.exist"), ContextBudget()
        )
        self.assertEqual(result.items, ())  # NO_MATCH -- nothing to inspect, but proves no crash
        self._remember("has.payload", "x", payload={"secret": "value"})
        result2 = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="d", domain="has.payload"), ContextBudget()
        )
        item = result2.items[0]
        field_names = {f.name for f in __import__("dataclasses").fields(item)}
        for forbidden in ("payload", "provenance", "confidence", "supersedes_id", "source_ref"):
            self.assertNotIn(forbidden, field_names)

    def test_historical_retrieval_uses_valid_at(self) -> None:
        self._remember(
            "employer.ns", "Company A", record_type="employer",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        result = self.retriever.retrieve(
            RetrievalQuery(
                descriptor_id="d", domain="employer.ns", temporal_intent=TemporalIntent.HISTORICAL,
                at=datetime(2026, 6, 1, tzinfo=UTC),
            ),
            ContextBudget(),
        )
        self.assertEqual(result.outcome, RetrievalOutcome.OK)
        self.assertEqual(result.items[0].content, "Company A")

    def test_retracted_never_returned_current_or_historical(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST, namespace="car.ns",
            category=MemoryCategory.FACT, record_type="car_ownership", content="Owns a Ferrari",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.service.retract(result.memory_id)
        current = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="d", domain="car.ns"), ContextBudget()
        )
        self.assertEqual(current.outcome, RetrievalOutcome.NO_MATCH)
        historical = self.retriever.retrieve(
            RetrievalQuery(
                descriptor_id="d", domain="car.ns", temporal_intent=TemporalIntent.HISTORICAL,
                at=datetime(2026, 3, 1, tzinfo=UTC),
            ),
            ContextBudget(),
        )
        self.assertEqual(historical.outcome, RetrievalOutcome.NO_MATCH)

    def test_retrieval_query_has_no_statuses_field(self) -> None:
        """R0075 §6/§25: structural enforcement -- RETRACTED is not just
        excluded by default, there is nowhere on RetrievalQuery to
        request it at all."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(RetrievalQuery)}
        self.assertNotIn("statuses", field_names)
        self.assertNotIn("include_retracted", field_names)

    def test_limit_respects_budget_max_items_per_source(self) -> None:
        for i in range(20):
            self._remember("many.ns", f"item {i}")
        result = self.retriever.retrieve(
            RetrievalQuery(descriptor_id="d", domain="many.ns", limit=100),
            ContextBudget(max_items_per_source=5),
        )
        self.assertLessEqual(len(result.items), 5)


if __name__ == "__main__":
    unittest.main()
