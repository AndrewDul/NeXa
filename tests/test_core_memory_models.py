"""``nexa.core.memory.models`` — construction and immutability (R0073)."""

from __future__ import annotations

import dataclasses
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest  # noqa: E402

from nexa.core.memory.models import (  # noqa: E402
    MemoryCategory,
    MemoryEvidence,
    MemoryProvenance,
    MemoryRecord,
    MemoryRelation,
    MemoryStatus,
    Page,
    RelationStatus,
    RememberResult,
)
from nexa.core.privacy import CloudEligibility  # noqa: E402


def _record() -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(
        id=uuid.uuid4().hex, namespace="teacher.python", category=MemoryCategory.STATE,
        record_type="skill_mastery", payload_version=1, scope_type="skill", scope_id="recursion",
        content="Recursion mastery: 55%", payload={"mastery": 0.55}, valid_from=None,
        valid_until=None, created_at=now, updated_at=now,
        cloud_eligibility=CloudEligibility.LOCAL_ONLY, status=MemoryStatus.ACTIVE,
        supersedes_id=None,
    )


class TestMemoryRecord(unittest.TestCase):
    def test_construction(self) -> None:
        record = _record()
        self.assertEqual(record.namespace, "teacher.python")
        self.assertEqual(record.category, MemoryCategory.STATE)

    def test_frozen(self) -> None:
        record = _record()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            record.content = "changed"  # type: ignore[misc]

    def test_no_provenance_source_or_confidence_fields(self) -> None:
        field_names = {f.name for f in dataclasses.fields(MemoryRecord)}
        for forbidden in ("provenance", "source_kind", "source_ref", "confidence"):
            self.assertNotIn(forbidden, field_names)


class TestMemoryEvidence(unittest.TestCase):
    def test_construction_and_frozen(self) -> None:
        now = datetime.now(UTC)
        evidence = MemoryEvidence(
            id=uuid.uuid4().hex, memory_id="abc", provenance=MemoryProvenance.IMPORTED,
            source_kind="calendar_event", source_ref="cal:1", confidence=0.7,
            observed_at=now, created_at=now, payload=None,
        )
        self.assertEqual(evidence.provenance, MemoryProvenance.IMPORTED)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            evidence.confidence = 0.9  # type: ignore[misc]


class TestMemoryRelation(unittest.TestCase):
    def test_construction(self) -> None:
        relation = MemoryRelation(
            id=uuid.uuid4().hex, source_id="a", relation_type="teaches", target_id="b",
            created_at=datetime.now(UTC), status=RelationStatus.ACTIVE,
        )
        self.assertEqual(relation.relation_type, "teaches")


class TestPageAndRememberResult(unittest.TestCase):
    def test_page_construction(self) -> None:
        page = Page(items=(_record(),), next_cursor=None)
        self.assertEqual(len(page.items), 1)
        self.assertIsNone(page.next_cursor)

    def test_remember_result_construction(self) -> None:
        result = RememberResult(memory_id="m1", evidence_id="e1", was_new_memory=True)
        self.assertTrue(result.was_new_memory)


if __name__ == "__main__":
    unittest.main()
