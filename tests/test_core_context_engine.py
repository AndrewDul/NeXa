"""``nexa.core.context.engine.ContextEngine`` — full pipeline (R0075 Revision 3)."""

from __future__ import annotations

import ast
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
from nexa.conversation.turn import ConversationTurn, Role  # noqa: E402
from nexa.core.context.engine import ContextBuildError, ContextEngine  # noqa: E402
from nexa.core.context.memory_retriever import MemoryRetriever  # noqa: E402
from nexa.core.context.models import (  # noqa: E402
    ContextBudget,
    ContextRequest,
    KnowledgeGapState,
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

CONTEXT_PKG = SRC / "nexa" / "core" / "context"


class _FakeProvider:
    def describe(self):
        return None


def _session() -> ConversationSession:
    return ConversationSession(
        provider=_FakeProvider(), system_prompt="x", options=GenerationOptions()
    )


class _EngineTestCase(unittest.TestCase):
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

    def _session_with_turn(self, text: str) -> ConversationSession:
        session = _session()
        session._history.append(ConversationTurn(role=Role.USER, content=text))
        return session

    def _request(self, session, **kwargs) -> ContextRequest:
        return ContextRequest(session=session, **kwargs)


class TestMandatoryContext(_EngineTestCase):
    def test_current_turn_derived_from_history_last(self) -> None:
        session = self._session_with_turn("hello")
        ctx = self.engine.build_context(self._request(session))
        self.assertEqual(ctx.current_turn, session.history[-1])

    def test_current_turn_not_in_conversation_window(self) -> None:
        """R0075 final review §3: corrected invariant -- current_turn and
        conversation_window are non-overlapping, not
        conversation_window[-1] == current_turn."""
        session = _session()
        session._history.append(ConversationTurn(role=Role.ASSISTANT, content="prior reply"))
        session._history.append(ConversationTurn(role=Role.USER, content="current message"))
        ctx = self.engine.build_context(self._request(session))
        self.assertNotIn(ctx.current_turn, ctx.conversation_window)
        self.assertEqual(ctx.conversation_window, tuple(session.history[:-1]))

    def test_empty_history_fails_loudly(self) -> None:
        with self.assertRaises(ContextBuildError):
            self.engine.build_context(self._request(_session()))

    def test_last_turn_not_user_fails_loudly(self) -> None:
        session = _session()
        session._history.append(ConversationTurn(role=Role.USER, content="hi"))
        session._history.append(ConversationTurn(role=Role.ASSISTANT, content="hello"))
        with self.assertRaises(ContextBuildError):
            self.engine.build_context(self._request(session))

    def test_conversation_window_turn_bound(self) -> None:
        session = _session()
        for i in range(30):
            session._history.append(ConversationTurn(role=Role.USER, content=f"turn {i}"))
        budget = ContextBudget(max_conversation_turns=5)
        ctx = self.engine.build_context(self._request(session, budget=budget))
        self.assertLessEqual(len(ctx.conversation_window), 5)

    def test_conversation_window_char_bound_keeps_most_recent(self) -> None:
        session = _session()
        session._history.append(ConversationTurn(role=Role.USER, content="a" * 100))
        session._history.append(ConversationTurn(role=Role.USER, content="b" * 100))
        session._history.append(ConversationTurn(role=Role.USER, content="c" * 10))
        budget = ContextBudget(max_conversation_chars=150, max_conversation_turns=10)
        ctx = self.engine.build_context(self._request(session, budget=budget))
        contents = [t.content for t in ctx.conversation_window]
        self.assertIn("b" * 100, contents)
        self.assertNotIn("a" * 100, contents, "oldest prior turn trimmed first")

    def test_current_turn_within_bound_succeeds_untouched(self) -> None:
        session = self._session_with_turn("a normal message")
        budget = ContextBudget(max_current_turn_chars=1000)
        ctx = self.engine.build_context(self._request(session, budget=budget))
        self.assertEqual(ctx.current_turn.content, "a normal message")

    def test_oversized_current_turn_fails_visibly_never_truncated(self) -> None:
        session = self._session_with_turn("x" * 200)
        budget = ContextBudget(max_current_turn_chars=50)
        with self.assertRaises(ContextBuildError):
            self.engine.build_context(self._request(session, budget=budget))
        # confirm the session's own history is untouched -- no silent rewrite
        self.assertEqual(len(session.history[-1].content), 200)


class TestNoHintCatalog(_EngineTestCase):
    def test_no_hints_returns_empty_selection_and_small_catalog(self) -> None:
        self._remember("projects.nexa", "a decision")
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session))
        self.assertEqual(ctx.selected_context_items, ())
        self.assertTrue(len(ctx.knowledge_references) >= 1)

    def test_knowledge_references_never_exceeds_budget(self) -> None:
        for i in range(20):
            self._remember(f"ns{i}", f"x{i}")
        budget = ContextBudget(max_knowledge_references=4)
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, budget=budget))
        self.assertLessEqual(len(ctx.knowledge_references), 4)

    def test_omitted_descriptor_count_when_over_budget(self) -> None:
        for i in range(20):
            self._remember(f"ns{i}", f"x{i}")
        budget = ContextBudget(max_knowledge_references=4)
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, budget=budget))
        self.assertGreater(ctx.trace.omitted_descriptor_count, 0)
        surfaced_ids = {d.id for d in ctx.knowledge_references}
        omitted_ids = set(ctx.trace.candidate_descriptor_ids) - surfaced_ids
        self.assertTrue(omitted_ids)
        for d in ctx.knowledge_references:
            self.assertNotIn(d.id, omitted_ids)


class TestTargetedRetrieval(_EngineTestCase):
    def test_domain_hint_selects_relevant_items(self) -> None:
        self._remember("projects.nexa", "We separated Evidence from Record.")
        self._remember("teacher.python", "unrelated")
        session = self._session_with_turn("Why did we separate Evidence from Record?")
        ctx = self.engine.build_context(self._request(session, domain_hint="projects.nexa"))
        contents = [i.content for i in ctx.selected_context_items]
        self.assertIn("We separated Evidence from Record.", contents)
        self.assertNotIn("unrelated", contents)

    def test_no_match_gap_survives_end_to_end(self) -> None:
        """R0075 §8/§16: 'I checked and it wasn't there' -- NO_MATCH must
        reach CurrentTurnContext.knowledge_gaps, not collapse to silence
        or a false UNAVAILABLE claim. Exercised via a historical query
        outside any record's validity window (deterministic NO_MATCH)."""
        self._remember(
            "projects.nexa", "an unrelated fact", record_type="unrelated",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session = self._session_with_turn("What did we decide about X specifically?")
        request = self._request(
            session,
            domain_hint="projects.nexa",
            temporal_intent=TemporalIntent.HISTORICAL,
            historical_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        ctx = self.engine.build_context(request)
        states = [g.state for g in ctx.knowledge_gaps]
        self.assertIn(KnowledgeGapState.NO_MATCH, states)

    def test_unknown_gap_for_unmatched_hint(self) -> None:
        session = self._session_with_turn("hi")
        request = self._request(session, domain_hint="nonexistent.domain")
        ctx = self.engine.build_context(request)
        self.assertEqual(len(ctx.knowledge_gaps), 1)
        self.assertEqual(ctx.knowledge_gaps[0].state, KnowledgeGapState.UNKNOWN)

    def test_retracted_never_appears_in_selected_items(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="car.ns",
            category=MemoryCategory.FACT,
            record_type="car_ownership",
            content="Owns a Ferrari",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.retract(result.memory_id)
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="car.ns"))
        self.assertEqual(ctx.selected_context_items, ())

    def test_historical_query_finds_superseded_record(self) -> None:
        old = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="employer.ns",
            category=MemoryCategory.FACT,
            record_type="employer",
            content="Works at Company A",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.service.supersede(
            old.memory_id,
            "Works at Company B",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            valid_from=datetime(2027, 1, 1, tzinfo=UTC),
        )
        session = self._session_with_turn("Where did I work in June 2026?")
        request = self._request(
            session,
            domain_hint="employer.ns",
            temporal_intent=TemporalIntent.HISTORICAL,
            historical_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        ctx = self.engine.build_context(request)
        self.assertEqual([i.content for i in ctx.selected_context_items], ["Works at Company A"])

    def test_bounded_max_items(self) -> None:
        for i in range(30):
            self._remember("many.ns", f"item {i}")
        budget = ContextBudget(max_items=5, max_items_per_source=30)
        session = self._session_with_turn("hi")
        request = self._request(session, domain_hint="many.ns", budget=budget)
        ctx = self.engine.build_context(request)
        self.assertLessEqual(len(ctx.selected_context_items), 5)

    def test_no_unbounded_memory_retrieval(self) -> None:
        """every retrieve() call must pass an explicit limit -- source-scan."""
        source = (CONTEXT_PKG / "memory_retriever.py").read_text(encoding="utf-8")
        self.assertIn("limit=limit", source)


class TestGroupNonRetrievability(_EngineTestCase):
    def test_domain_group_never_reaches_selected_items(self) -> None:
        self._remember("lifeos.sleep", "slept 7h")
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="lifeos"))
        self.assertEqual(ctx.selected_context_items, (), "no auto-expansion into retrieval")
        self.assertTrue(any(d.domain == "lifeos" for d in ctx.knowledge_references))


class TestConflictDetection(_EngineTestCase):
    def test_state_records_never_flagged(self) -> None:
        self._remember(
            "device.ns", "Battery: 82%", category=MemoryCategory.STATE, record_type="battery"
        )
        self._remember(
            "device.ns", "Battery: 79%", category=MemoryCategory.STATE, record_type="battery"
        )
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="device.ns"))
        self.assertEqual(ctx.conflicts, (), "STATE differing values are normal, never a conflict")

    def test_event_records_never_flagged(self) -> None:
        self._remember(
            "log.ns", "purchase A", category=MemoryCategory.EVENT, record_type="purchase"
        )
        self._remember(
            "log.ns", "purchase B", category=MemoryCategory.EVENT, record_type="purchase"
        )
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="log.ns"))
        self.assertEqual(ctx.conflicts, ())

    def test_fact_records_same_scope_flagged_as_potential_conflict(self) -> None:
        self._remember(
            "user", "Prefers morning workouts",
            category=MemoryCategory.PREFERENCE, record_type="preference",
        )
        self._remember(
            "user", "Prefers evening workouts",
            category=MemoryCategory.PREFERENCE, record_type="preference",
        )
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="user"))
        self.assertEqual(len(ctx.conflicts), 1)
        self.assertEqual(ctx.conflicts[0].type.value, "potential_conflict")

    def test_conflicting_items_both_remain_selected(self) -> None:
        self._remember(
            "user", "Prefers morning workouts",
            category=MemoryCategory.PREFERENCE, record_type="preference",
        )
        self._remember(
            "user", "Prefers evening workouts",
            category=MemoryCategory.PREFERENCE, record_type="preference",
        )
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="user"))
        self.assertEqual(len(ctx.selected_context_items), 2)


class TestTraceSafety(_EngineTestCase):
    def test_trace_contains_no_raw_content(self) -> None:
        self._remember("secret.ns", "SUPER-SECRET-CONTENT-XYZ")
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session, domain_hint="secret.ns"))
        trace_repr = repr(ctx.trace)
        self.assertNotIn("SUPER-SECRET-CONTENT-XYZ", trace_repr)


class TestDependencyDirection(_EngineTestCase):
    def _imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def _is_realtime_import(self, module: str) -> bool:
        return module == "nexa.realtime" or module.startswith("nexa.realtime.")

    def test_context_package_has_zero_imports_from_realtime(self) -> None:
        offenders = []
        for path in CONTEXT_PKG.rglob("*.py"):
            imports = self._imports(path)
            if any(self._is_realtime_import(m) for m in imports):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])

    def test_no_gemini_or_provider_sdk_imports(self) -> None:
        for path in CONTEXT_PKG.rglob("*.py"):
            imports = self._imports(path)
            self.assertFalse(any("gemini" in m.lower() for m in imports), path)

    def test_entire_core_package_still_has_zero_imports_from_realtime(self) -> None:
        """Regression: the pre-existing M3.2 full-tree test must keep
        passing after M3.3 lands."""
        offenders = []
        for path in (SRC / "nexa" / "core").rglob("*.py"):
            imports = self._imports(path)
            if any(self._is_realtime_import(m) for m in imports):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])

    def test_engine_never_calls_memory_write_methods(self) -> None:
        source = (CONTEXT_PKG / "engine.py").read_text(encoding="utf-8")
        forbidden = (
            ".remember(", ".supersede(", ".retract(",
            ".hard_delete(", ".relate(", ".unrelate(",
        )
        for token in forbidden:
            self.assertNotIn(token, source)

    def test_engine_never_touches_session_history(self) -> None:
        source = (CONTEXT_PKG / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("_history.append", source)
        self.assertNotIn("record_external_exchange", source)

    def test_engine_never_calls_evidence_for(self) -> None:
        engine_source = (CONTEXT_PKG / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("evidence_for", engine_source)
        retriever_source = (CONTEXT_PKG / "memory_retriever.py").read_text(encoding="utf-8")
        self.assertNotIn("evidence_for", retriever_source)


class TestDeterminism(_EngineTestCase):
    def test_same_state_same_request_same_output(self) -> None:
        self._remember("projects.nexa", "a decision")
        session = self._session_with_turn("hi")
        request = self._request(session, domain_hint="projects.nexa")
        ctx1 = self.engine.build_context(request)
        ctx2 = self.engine.build_context(request)
        self.assertEqual(ctx1.selected_context_items, ctx2.selected_context_items)
        self.assertEqual(ctx1.knowledge_references, ctx2.knowledge_references)


class TestDynamicKnowledge(_EngineTestCase):
    def test_new_knowledge_discoverable_without_restart(self) -> None:
        """R0075 §23/§26: same ContextEngine instance, no reconstruction,
        no restart -- knowledge written AFTER construction is discoverable
        on the very next build_context() call."""
        session = self._session_with_turn("hi")
        request = self._request(session, domain_hint="lifeos.goals")
        before = self.engine.build_context(request)
        self.assertEqual(before.selected_context_items, ())

        self._remember(
            "lifeos.goals", "Complete MSc with distinction", category=MemoryCategory.STATE
        )

        after = self.engine.build_context(request)
        self.assertEqual(len(after.selected_context_items), 1)


class TestStressScenarios(_EngineTestCase):
    def test_teacher_without_domain_specific_engine_code(self) -> None:
        self._remember(
            "teacher.python", "Recursion mastery: 55%",
            category=MemoryCategory.STATE, record_type="skill_mastery",
            scope_type="skill", scope_id="recursion",
        )
        self._remember(
            "teacher.python", "Off-by-one in base case",
            category=MemoryCategory.EPISODE, record_type="mistake",
        )
        session = self._session_with_turn("What should I learn next in Python?")
        request = self._request(session, domain_hint="teacher.python")
        ctx = self.engine.build_context(request)
        self.assertEqual(len(ctx.selected_context_items), 2)
        engine_source = (CONTEXT_PKG / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("teacher", engine_source.lower())

    def test_lifeos_bounded_to_hinted_domains_only(self) -> None:
        self._remember("lifeos.sleep", "slept 7h", category=MemoryCategory.EVENT)
        self._remember("lifeos.finance", "grocery purchase", category=MemoryCategory.EVENT)
        session = self._session_with_turn("Why have I been less productive recently?")
        request = self._request(session, domain_hint="lifeos.sleep")
        ctx = self.engine.build_context(request)
        contents = [i.content for i in ctx.selected_context_items]
        self.assertIn("slept 7h", contents)
        self.assertNotIn("grocery purchase", contents)

    def test_projects_scenario(self) -> None:
        self._remember(
            "projects.nexa", "Decided: one MemoryService authority",
            category=MemoryCategory.EPISODE, record_type="decision",
        )
        session = self._session_with_turn("What did we decide about Memory?")
        request = self._request(session, domain_hint="projects.nexa")
        ctx = self.engine.build_context(request)
        self.assertEqual(len(ctx.selected_context_items), 1)

    def test_large_namespace_count_never_loads_all_records(self) -> None:
        for i in range(50):
            self._remember(f"ns{i}", f"content {i}")
        session = self._session_with_turn("hi")
        ctx = self.engine.build_context(self._request(session))
        default_max = ContextBudget().max_knowledge_references
        self.assertLessEqual(len(ctx.knowledge_references), default_max)


if __name__ == "__main__":
    unittest.main()
