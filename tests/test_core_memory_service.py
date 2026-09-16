"""``nexa.core.memory.service.MemoryService`` — write policy, lifecycle,
provider-neutrality, logging safety (R0073)."""

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

from nexa.core.memory import (  # noqa: E402
    HardDeleteReasonCode,
    MemoryCategory,
    MemoryNotFoundError,
    MemoryProvenance,
    MemoryStatus,
    MemoryValidationError,
    MemoryWriteTrigger,
)
from nexa.core.memory.repository import MemoryRepository  # noqa: E402
from nexa.core.memory.service import MemoryService  # noqa: E402
from nexa.core.privacy import CloudEligibility  # noqa: E402
from nexa.core.storage.sqlite import connect  # noqa: E402


class _ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()


class TestRememberBasics(_ServiceTestCase):
    def test_creates_new_memory_with_first_evidence(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.assertTrue(result.was_new_memory)
        record = self.service.by_id(result.memory_id)
        self.assertEqual(record.content, "Prefers morning workouts")
        self.assertEqual(
            record.cloud_eligibility, CloudEligibility.LOCAL_ONLY, "default is LOCAL_ONLY"
        )
        evidence = self.service.evidence_for(result.memory_id, limit=10)
        self.assertEqual(len(evidence.items), 1)
        self.assertEqual(evidence.items[0].id, result.evidence_id)

    def test_durable_candidate_requires_confidence(self) -> None:
        with self.assertRaises(MemoryValidationError):
            self.service.remember(
                MemoryWriteTrigger.DURABLE_CANDIDATE,
                namespace="user", category=MemoryCategory.FACT, record_type="thing",
                content="x", provenance=MemoryProvenance.SYSTEM_OBSERVATION,
            )

    def test_invalid_namespace_rejected(self) -> None:
        with self.assertRaises(MemoryValidationError):
            self.service.remember(
                MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
                namespace="NotLowercase", category=MemoryCategory.FACT, record_type="x",
                content="c", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            )

    def test_record_type_repeating_namespace_rejected(self) -> None:
        with self.assertRaises(MemoryValidationError):
            self.service.remember(
                MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
                namespace="teacher.python", category=MemoryCategory.STATE,
                record_type="teacher.skill_mastery", content="c",
                provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            )

    def test_conversation_history_never_automatically_becomes_memory(self) -> None:
        """A full ConversationSession with several turns must not create
        any memory_records unless remember() was called explicitly."""
        from nexa.bootstrap import build_default_session
        from nexa.conversation.turn import ConversationTurn, Role

        session = build_default_session()
        session._history.append(ConversationTurn(role=Role.USER, content="I really love mornings"))
        session._history.append(
            ConversationTurn(role=Role.ASSISTANT, content="Good to know!")
        )
        self.assertEqual(len(session.history), 2)
        page = self.service.recent(100)
        self.assertEqual(page.items, ())


class TestDedupPreservesEvidence(_ServiceTestCase):
    def test_repeated_explicit_statement_adds_evidence_not_duplicate_record(self) -> None:
        first = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            source_kind="conversation_turn",
        )
        second = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            source_kind="conversation_turn",
        )
        self.assertEqual(first.memory_id, second.memory_id)
        self.assertFalse(second.was_new_memory)
        self.assertNotEqual(first.evidence_id, second.evidence_id)

        all_records = self.service.by_namespace("user", limit=10)
        self.assertEqual(len(all_records.items), 1, "exactly one canonical record")

        evidence = self.service.evidence_for(first.memory_id, limit=10)
        self.assertEqual(len(evidence.items), 2, "both evidence rows preserved")

    def test_multiple_independent_sources_all_preserved(self) -> None:
        """Stress test from review: explicit statement, repeated explicit
        statement, then an independent LiFeOS system observation -- all
        three survive as evidence on the SAME canonical record."""
        r1 = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.SYSTEM_OBSERVATION,
            source_kind="lifeos_routine_pattern", confidence=0.81,
        )
        self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workouts", provenance=MemoryProvenance.IMPORTED,
            source_kind="calendar_event", source_ref="cal:evt:88f2", confidence=0.7,
        )
        evidence = self.service.evidence_for(r1.memory_id, limit=10)
        self.assertEqual(len(evidence.items), 4)
        self.assertEqual(
            {e.provenance for e in evidence.items},
            {
                MemoryProvenance.EXPLICIT_USER_STATEMENT,
                MemoryProvenance.SYSTEM_OBSERVATION,
                MemoryProvenance.IMPORTED,
            },
        )
        self.assertEqual(len(self.service.by_namespace("user", limit=10).items), 1)

    def test_no_record_level_confidence_field_exists(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="user", category=MemoryCategory.FACT, record_type="thing",
            content="x", provenance=MemoryProvenance.SYSTEM_OBSERVATION, confidence=0.9,
        )
        record = self.service.by_id(result.memory_id)
        self.assertFalse(
            hasattr(record, "confidence"), "MemoryRecord must not have a confidence field"
        )
        evidence = self.service.evidence_for(result.memory_id, limit=10).items[0]
        self.assertEqual(evidence.confidence, 0.9, "confidence lives only on evidence")


class TestSupersessionAndTemporalTruth(_ServiceTestCase):
    def test_supersede_closes_old_valid_until_for_fact(self) -> None:
        old = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.FACT, record_type="employer",
            content="Works at Company A", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        new = self.service.supersede(
            old.memory_id, "Works at Company B",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            valid_from=datetime(2027, 1, 1, tzinfo=UTC),
        )
        old_record = self.service.by_id(old.memory_id)
        new_record = self.service.by_id(new.memory_id)
        self.assertEqual(old_record.status, MemoryStatus.SUPERSEDED)
        self.assertEqual(old_record.valid_until, datetime(2027, 1, 1, tzinfo=UTC))
        self.assertEqual(new_record.status, MemoryStatus.ACTIVE)
        self.assertEqual(new_record.supersedes_id, old.memory_id)

        june_2026 = self.service.valid_at(
            datetime(2026, 6, 1, tzinfo=UTC), namespace="user", record_type="employer", limit=10
        )
        self.assertEqual([r.content for r in june_2026.items], ["Works at Company A"])

    def test_supersede_unknown_id_raises(self) -> None:
        with self.assertRaises(MemoryNotFoundError):
            self.service.supersede(
                "does-not-exist", "new content", provenance=MemoryProvenance.USER_CORRECTION,
            )

    def test_supersede_preserves_auditability(self) -> None:
        old = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.FACT, record_type="thing",
            content="original", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.supersede(
            old.memory_id, "corrected", provenance=MemoryProvenance.USER_CORRECTION
        )
        self.assertIsNotNone(self.service.by_id(old.memory_id), "old record never deleted")
        current = self.service.by_namespace("user", record_type="thing", limit=10)
        self.assertEqual([r.content for r in current.items], ["corrected"])


class TestRetractVsHardDelete(_ServiceTestCase):
    def test_retract_does_not_physically_delete(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.FACT, record_type="car_ownership",
            content="Owns a Ferrari", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.service.retract(result.memory_id)

        still_there = self.service.by_id(result.memory_id)
        self.assertIsNotNone(still_there, "retract must not physically delete")
        self.assertEqual(still_there.status, MemoryStatus.RETRACTED)

        current = self.service.by_namespace("user", record_type="car_ownership", limit=10)
        self.assertEqual(current.items, (), "excluded from default current-knowledge queries")

        historical = self.service.valid_at(
            datetime(2026, 3, 1, tzinfo=UTC), namespace="user",
            record_type="car_ownership", limit=10,
        )
        self.assertEqual(historical.items, (), "excluded from valid_at's historical default too")

        audit = self.service.valid_at(
            datetime(2026, 3, 1, tzinfo=UTC), namespace="user", record_type="car_ownership",
            limit=10, statuses={MemoryStatus.RETRACTED},
        )
        self.assertEqual(
            [r.content for r in audit.items], ["Owns a Ferrari"], "available via explicit audit"
        )

    def test_hard_delete_physically_removes(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.FACT, record_type="thing",
            content="sensitive info", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.hard_delete(result.memory_id, reason_code=HardDeleteReasonCode.PRIVACY_ERASURE)
        self.assertIsNone(self.service.by_id(result.memory_id))

    def test_hard_delete_invalid_reason_code_rejected(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.FACT, record_type="thing",
            content="x", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        with self.assertRaises(MemoryValidationError):
            self.service.hard_delete(result.memory_id, reason_code="free text reason, not a code!")

    def test_hard_delete_unknown_id_raises(self) -> None:
        with self.assertRaises(MemoryNotFoundError):
            self.service.hard_delete(
                "does-not-exist", reason_code=HardDeleteReasonCode.USER_REQUESTED
            )


class TestHardDeleteLoggingSafety(_ServiceTestCase):
    def test_log_contains_no_content_payload_or_source_ref(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="lifeos.health", category=MemoryCategory.EVENT,
            record_type="health_observation",
            content="SUPER-SECRET-MEDICAL-CONDITION-XYZ",
            payload={"metric": "TOP-SECRET-PAYLOAD-VALUE"},
            provenance=MemoryProvenance.SYSTEM_OBSERVATION,
            source_kind="device_sensor", source_ref="SECRET-DEVICE-SERIAL-9999",
            cloud_eligibility=CloudEligibility.LOCAL_ONLY,
        )
        with self.assertLogs("nexa.core.memory.service", level="INFO") as captured:
            self.service.hard_delete(
                result.memory_id, reason_code=HardDeleteReasonCode.PRIVACY_ERASURE
            )

        full_log_text = "\n".join(captured.output)
        for forbidden in (
            "SUPER-SECRET-MEDICAL-CONDITION-XYZ",
            "TOP-SECRET-PAYLOAD-VALUE",
            "SECRET-DEVICE-SERIAL-9999",
        ):
            self.assertNotIn(forbidden, full_log_text)
        self.assertIn(result.memory_id, full_log_text)
        self.assertIn("privacy_erasure", full_log_text)


class TestRelateUnrelate(_ServiceTestCase):
    def test_relate_unknown_ids_raise(self) -> None:
        real = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="ns", category=MemoryCategory.FACT, record_type="thing",
            content="x", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        with self.assertRaises(MemoryNotFoundError):
            self.service.relate(real.memory_id, "relates_to", "does-not-exist")

    def test_relate_and_unrelate(self) -> None:
        a = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="ns", category=MemoryCategory.FACT, record_type="thing",
            content="a", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        b = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="ns", category=MemoryCategory.FACT, record_type="thing",
            content="b", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        relation_id = self.service.relate(a.memory_id, "relates_to", b.memory_id)
        self.assertEqual(len(self.service.related_to(a.memory_id, limit=10)), 1)
        self.service.unrelate(relation_id)
        self.assertEqual(len(self.service.related_to(a.memory_id, limit=10)), 0)


class TestUpdateMetadata(_ServiceTestCase):
    def test_update_cloud_eligibility_only(self) -> None:
        result = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="ns", category=MemoryCategory.FACT, record_type="thing",
            content="x", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.update_metadata(
            result.memory_id, cloud_eligibility=CloudEligibility.CLOUD_SAFE
        )
        record = self.service.by_id(result.memory_id)
        self.assertEqual(record.cloud_eligibility, CloudEligibility.CLOUD_SAFE)
        self.assertEqual(record.content, "x", "content never touched by update_metadata")

    def test_update_metadata_unknown_id_raises(self) -> None:
        with self.assertRaises(MemoryNotFoundError):
            self.service.update_metadata(
                "does-not-exist", cloud_eligibility=CloudEligibility.CLOUD_SAFE
            )


class TestGenericCloudEligibilityQuery(_ServiceTestCase):
    def test_no_query_cloud_safe_method_exists(self) -> None:
        """R0073 final review §11: MemoryService stays provider-neutral --
        query_cloud_safe() was removed in favor of a generic filter."""
        self.assertFalse(hasattr(self.service, "query_cloud_safe"))

    def test_cloud_eligibility_is_just_another_filter(self) -> None:
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="ctx", category=MemoryCategory.FACT, record_type="thing",
            content="safe fact", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            cloud_eligibility=CloudEligibility.CLOUD_SAFE,
        )
        self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="ctx", category=MemoryCategory.FACT, record_type="thing",
            content="secret fact", provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
            cloud_eligibility=CloudEligibility.LOCAL_ONLY,
        )
        safe_only = self.service.by_namespace(
            "ctx", limit=10, cloud_eligibility=CloudEligibility.CLOUD_SAFE
        )
        self.assertEqual([r.content for r in safe_only.items], ["safe fact"])


class TestNoCloudProviderDependency(_ServiceTestCase):
    def test_service_module_has_no_realtime_or_provider_imports(self) -> None:
        import ast

        source = (SRC / "nexa" / "core" / "memory" / "service.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        self.assertFalse(any("realtime" in m or "gemini" in m for m in modules), modules)


if __name__ == "__main__":
    unittest.main()
