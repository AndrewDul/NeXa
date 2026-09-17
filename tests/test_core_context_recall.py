"""``ContextEngine.recall()`` — the second entry mode (R0079 / R0078
Revision 2 §1-§3): targeted knowledge retrieval with no
``ConversationSession`` and no current USER turn required. Exists because
a live cloud realtime turn has no canonical current turn to build one from
while Gemini is generating (R0078 §4's audit).
"""

from __future__ import annotations

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
from nexa.core.context.derivation import derive_context_request  # noqa: E402
from nexa.core.context.engine import ContextBuildError, ContextEngine  # noqa: E402
from nexa.core.context.memory_retriever import MemoryRetriever  # noqa: E402
from nexa.core.context.models import (  # noqa: E402
    MAX_RECALL_QUERY_CHARS,
    ContextRequest,
    RecallBudget,
    RecallOutcome,
    RecallRequest,
    RecallResult,
    TemporalIntent,
)
from nexa.core.identity import load_identity  # noqa: E402
from nexa.core.memory.models import (  # noqa: E402
    MemoryCategory,
    MemoryProvenance,
    MemoryWriteTrigger,
)
from nexa.core.memory.repository import MemoryRepository  # noqa: E402
from nexa.core.memory.service import MemoryService  # noqa: E402
from nexa.core.storage.sqlite import connect  # noqa: E402
from nexa.providers.base import GenerationOptions  # noqa: E402


class _FakeProvider:
    def describe(self):
        return None


def _session(*turns: ConversationTurn) -> ConversationSession:
    s = ConversationSession(
        provider=_FakeProvider(), system_prompt="x", options=GenerationOptions()
    )
    for t in turns:
        s._history.append(t)  # noqa: SLF001 -- test-only direct history seeding
    return s


class _RecallTestCase(unittest.TestCase):
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

    def _remember(self, namespace: str, content: str, **kwargs) -> None:
        category = kwargs.pop("category", MemoryCategory.FACT)
        record_type = kwargs.pop("record_type", "thing")
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace=namespace,
            category=category,
            record_type=record_type,
            content=content,
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            **kwargs,
        )


class TestRecallRequiresNoSession(_RecallTestCase):
    """R0079 acceptance item 1: recall() requires no current
    ConversationSession user turn -- the direct regression for R0078 §1's
    fix. build_context() retains its unchanged precondition (item 2)."""

    def test_recall_succeeds_with_no_session_at_all(self) -> None:
        self._remember("projects.nexa", "We chose one MemoryService authority.")
        request = RecallRequest(query_text="What did we decide about NeXa?")
        result = self.engine.recall(request)  # no ConversationSession anywhere in scope
        self.assertEqual(result.outcome, RecallOutcome.FOUND)
        self.assertEqual(len(result.items), 1)
        self.assertIn("MemoryService", result.items[0].content)

    def test_build_context_still_requires_current_user_turn(self) -> None:
        empty_session = _session()
        request = ContextRequest(session=empty_session)
        with self.assertRaises(ContextBuildError):
            self.engine.build_context(request)


class TestSharedRetrievalImplementation(_RecallTestCase):
    """R0079 acceptance item 3: both entrypoints reuse the same retriever
    instance -- no duplicated retrieval implementation, no second
    authority."""

    def test_recall_and_build_context_use_the_same_retriever_instance(self) -> None:
        self.assertIs(self.engine._retrievers[0], self.retriever)  # noqa: SLF001
        self.assertIs(self.engine._retrievers_by_kind["memory"], self.retriever)  # noqa: SLF001

        self._remember("projects.nexa", "one canonical authority")

        turn = ConversationTurn(role=Role.USER, content="What did we decide about NeXa?")
        via_build_context = self.engine.build_context(
            derive_context_request(_session(turn))
        )
        via_recall = self.engine.recall(RecallRequest(query_text="What did we decide about NeXa?"))

        self.assertEqual(
            via_build_context.selected_context_items[0].content,
            via_recall.items[0].content,
        )


class TestRecallRequestValidation(unittest.TestCase):
    def test_blank_query_text_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RecallRequest(query_text="   ")

    def test_oversized_query_text_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RecallRequest(query_text="x" * (MAX_RECALL_QUERY_CHARS + 1))

    def test_query_text_at_exact_limit_accepted(self) -> None:
        RecallRequest(query_text="x" * MAX_RECALL_QUERY_CHARS)  # must not raise

    def test_historical_without_at_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RecallRequest(query_text="anything", temporal_intent=TemporalIntent.HISTORICAL)

    def test_no_provider_specific_fields_exist(self) -> None:
        """R0079 acceptance item 5: RecallRequest contains no provider
        types/fields -- only query/domain_hint/temporal_intent/
        historical_at/budget."""
        fields = {f for f in RecallRequest.__dataclass_fields__}
        self.assertEqual(
            fields, {"query_text", "domain_hint", "temporal_intent", "historical_at", "budget"}
        )


class TestRecallResultInvariant(unittest.TestCase):
    def test_found_requires_nonempty_items(self) -> None:
        with self.assertRaises(ValueError):
            RecallResult(outcome=RecallOutcome.FOUND, items=())

    def test_no_match_requires_empty_items(self) -> None:
        from nexa.core.context.models import ContextItem, ContextItemPriority
        from nexa.core.privacy import CloudEligibility

        item = ContextItem(
            source_kind="memory", source_id="x", domain="d", record_type="t",
            scope_type=None, scope_id=None, content="c", category="fact",
            cloud_eligibility=CloudEligibility.CLOUD_SAFE, priority=ContextItemPriority.OPTIONAL,
            reason_selected="x", freshness=None,
        )
        with self.assertRaises(ValueError):
            RecallResult(outcome=RecallOutcome.NO_MATCH, items=(item,))

    def test_unavailable_and_permission_required_allow_empty_items(self) -> None:
        RecallResult(outcome=RecallOutcome.UNAVAILABLE)
        RecallResult(outcome=RecallOutcome.PERMISSION_REQUIRED)


class TestRecallOutcomes(_RecallTestCase):
    def test_no_match_when_nothing_in_memory(self) -> None:
        result = self.engine.recall(RecallRequest(query_text="anything at all"))
        self.assertEqual(result.outcome, RecallOutcome.NO_MATCH)
        self.assertEqual(result.items, ())

    def test_no_match_when_hint_matches_nothing(self) -> None:
        self._remember("projects.nexa", "one canonical authority")
        result = self.engine.recall(RecallRequest(query_text="completely unrelated zephyr"))
        self.assertEqual(result.outcome, RecallOutcome.NO_MATCH)

    def test_found_via_domain_hint(self) -> None:
        self._remember("projects.nexa", "one canonical authority")
        result = self.engine.recall(
            RecallRequest(query_text="ignored", domain_hint="projects.nexa")
        )
        self.assertEqual(result.outcome, RecallOutcome.FOUND)
        self.assertEqual(len(result.items), 1)

    def test_retracted_record_never_surfaced(self) -> None:
        """R0079 acceptance item 26: RETRACTED is unreachable through
        recall() the same way it already is through build_context() --
        preserved automatically by reuse, no new filtering code."""
        self._remember("projects.nexa", "an old, now-retracted fact")
        record = next(iter(self.service.by_namespace("projects.nexa", limit=10).items))
        self.service.retract(record.id)

        result = self.engine.recall(
            RecallRequest(query_text="ignored", domain_hint="projects.nexa")
        )
        self.assertEqual(result.outcome, RecallOutcome.NO_MATCH)

    def test_recall_budget_has_no_conversation_fields(self) -> None:
        """R0079 acceptance item: RecallBudget carries no conversation/
        current-turn bounds -- recall() has no conversation concept."""
        fields = set(RecallBudget.__dataclass_fields__)
        self.assertEqual(
            fields,
            {
                "max_items",
                "max_content_chars",
                "max_items_per_source",
                "max_retrieval_rounds",
                "max_knowledge_references",
            },
        )


class TestRecallTraceSafety(_RecallTestCase):
    """R0079 acceptance item 9: recall trace never contains raw query
    text, raw Memory content, payload, source_ref, exception strings, or
    provider arguments -- IDs, counts, and closed reason codes only."""

    def test_raw_query_text_never_appears_verbatim_in_trace(self) -> None:
        self._remember("projects.nexa", "one canonical authority for the whole system")
        raw_query = "What's the CANONICAL-Authority?! (please, NeXa)"
        result = self.engine.recall(RecallRequest(query_text=raw_query))
        trace_repr = repr(result.trace)
        self.assertNotIn(raw_query, trace_repr)

    def test_raw_memory_content_never_appears_in_trace(self) -> None:
        """Discovery matches against the descriptor's domain/summary
        (namespace name + record count + freshest date), never full-text
        content (R0075 §7's documented, deliberately weak topic
        discovery) -- so a hint that matches the NAMESPACE surfaces the
        record, and this test proves its CONTENT still never leaks into
        the trace even though it was selected."""
        secret_content = "a very specific secret sentence nobody else writes"
        self._remember("projects.nexa", secret_content)
        result = self.engine.recall(RecallRequest(query_text="nexa"))
        self.assertEqual(result.outcome, RecallOutcome.FOUND)
        trace_repr = repr(result.trace)
        self.assertNotIn(secret_content, trace_repr)

    def test_trace_never_included_on_result_serialization_boundary(self) -> None:
        """RecallResult.trace is Core-internal only; nothing about this
        test asserts trace is REMOVED from RecallResult (it legitimately
        stays there for Core-internal diagnostics) -- this documents the
        boundary explicitly so a future adapter change is caught by
        review, not silently regressed."""
        self._remember("projects.nexa", "x")
        result = self.engine.recall(RecallRequest(query_text="x"))
        self.assertIsNotNone(result.trace)  # present for Core-internal use
        # the adapter boundary (nexa.realtime.gemini) is responsible for
        # never serializing result.trace to a provider -- covered by the
        # Gemini tool adapter's own tests.


if __name__ == "__main__":
    unittest.main()
