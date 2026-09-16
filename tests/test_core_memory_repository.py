"""``nexa.core.memory.repository.MemoryRepository`` — typed SQLite CRUD,
bounded/paginated retrieval, temporal queries, relations (R0073)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uuid  # noqa: E402

from nexa.core.memory.models import (  # noqa: E402
    MemoryCategory,
    MemoryEvidence,
    MemoryProvenance,
    MemoryRecord,
    MemoryRelation,
    MemoryStatus,
    RelationStatus,
)
from nexa.core.memory.repository import MemoryRepository  # noqa: E402
from nexa.core.memory.validation import MemoryValidationError  # noqa: E402
from nexa.core.privacy import CloudEligibility  # noqa: E402
from nexa.core.storage.sqlite import connect  # noqa: E402


def _rec(
    *, namespace="test.ns", category=MemoryCategory.FACT, record_type="thing",
    content="content", scope_type=None, scope_id=None, valid_from=None, valid_until=None,
    status=MemoryStatus.ACTIVE, supersedes_id=None, cloud_eligibility=CloudEligibility.LOCAL_ONLY,
    payload=None, payload_version=1, when=None,
) -> MemoryRecord:
    now = when or datetime.now(UTC)
    return MemoryRecord(
        id=uuid.uuid4().hex, namespace=namespace, category=category, record_type=record_type,
        payload_version=payload_version, scope_type=scope_type, scope_id=scope_id,
        content=content, payload=payload, valid_from=valid_from, valid_until=valid_until,
        created_at=now, updated_at=now, cloud_eligibility=cloud_eligibility,
        status=status, supersedes_id=supersedes_id,
    )


def _evi(
    *, memory_id, provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
    source_kind=None, source_ref=None, confidence=None, observed_at=None, when=None,
) -> MemoryEvidence:
    return MemoryEvidence(
        id=uuid.uuid4().hex, memory_id=memory_id, provenance=provenance,
        source_kind=source_kind, source_ref=source_ref, confidence=confidence,
        observed_at=observed_at, created_at=when or datetime.now(UTC), payload=None,
    )


class _RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.repo = MemoryRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()


class TestRecordCrud(_RepoTestCase):
    def test_insert_and_by_id(self) -> None:
        record = _rec(content="hello")
        self.repo.insert_record(record)
        self.conn.commit()
        fetched = self.repo.by_id(record.id)
        self.assertEqual(fetched.content, "hello")
        self.assertEqual(fetched.status, MemoryStatus.ACTIVE)

    def test_by_id_missing_returns_none(self) -> None:
        self.assertIsNone(self.repo.by_id("does-not-exist"))

    def test_by_ids_bulk(self) -> None:
        r1, r2 = _rec(), _rec()
        self.repo.insert_record(r1)
        self.repo.insert_record(r2)
        self.conn.commit()
        fetched = self.repo.by_ids([r1.id, r2.id])
        self.assertEqual({f.id for f in fetched}, {r1.id, r2.id})

    def test_payload_roundtrip_object_shape(self) -> None:
        record = _rec(payload={"a": 1, "b": "two"}, payload_version=3)
        self.repo.insert_record(record)
        self.conn.commit()
        fetched = self.repo.by_id(record.id)
        self.assertEqual(fetched.payload, {"a": 1, "b": "two"})
        self.assertEqual(fetched.payload_version, 3)

    def test_malformed_payload_json_rejected_by_db_check(self) -> None:
        import sqlite3

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO memory_records (id, namespace, category, record_type, "
                "payload_version, content, payload_json, created_at, updated_at, "
                "cloud_eligibility, status) VALUES "
                "('x','ns','fact','t',1,'c','[1,2,3]','now','now','local_only','active')"
            )

    def test_persists_across_reconnect(self) -> None:
        record = _rec(content="durable")
        self.repo.insert_record(record)
        self.conn.commit()
        self.conn.close()

        conn2 = connect(Path(self._tmp.name) / "core.sqlite3")
        try:
            repo2 = MemoryRepository(conn2)
            fetched = repo2.by_id(record.id)
            self.assertEqual(fetched.content, "durable")
        finally:
            conn2.close()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")  # for tearDown


class TestEvidence(_RepoTestCase):
    def test_evidence_roundtrip_all_provenance_values(self) -> None:
        record = _rec()
        self.repo.insert_record(record)
        for prov in MemoryProvenance:
            ev = _evi(memory_id=record.id, provenance=prov, source_kind="k", source_ref="r",
                      confidence=0.5, observed_at=datetime(2026, 1, 1, tzinfo=UTC))
            self.repo.insert_evidence(ev)
        self.conn.commit()
        page = self.repo.evidence_for(record.id, limit=100)
        self.assertEqual(len(page.items), len(list(MemoryProvenance)))
        self.assertEqual({e.provenance for e in page.items}, set(MemoryProvenance))

    def test_evidence_persists_across_reconnect(self) -> None:
        record = _rec()
        self.repo.insert_record(record)
        ev = _evi(memory_id=record.id, source_ref="cal:evt:1", confidence=0.9)
        self.repo.insert_evidence(ev)
        self.conn.commit()
        self.conn.close()

        conn2 = connect(Path(self._tmp.name) / "core.sqlite3")
        try:
            page = MemoryRepository(conn2).evidence_for(record.id, limit=10)
            self.assertEqual(len(page.items), 1)
            self.assertEqual(page.items[0].source_ref, "cal:evt:1")
            self.assertEqual(page.items[0].confidence, 0.9)
        finally:
            conn2.close()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")


class TestNamespaceAndScopeQueries(_RepoTestCase):
    def test_by_namespace_extensibility(self) -> None:
        """A namespace/record_type NOT present anywhere in nexa.core.memory's
        own code must still be queryable -- proves extensibility rather
        than asserting it (R0073 acceptance 5)."""
        record = _rec(namespace="teacher.made_up_for_the_test", record_type="made_up_type")
        self.repo.insert_record(record)
        self.conn.commit()
        page = self.repo.by_namespace("teacher.made_up_for_the_test", limit=10)
        self.assertEqual([r.id for r in page.items], [record.id])

    def test_by_scope_two_unrelated_domains(self) -> None:
        project_rec = _rec(scope_type="project", scope_id="nexa_ikigai")
        lesson_rec = _rec(scope_type="lesson", scope_id="python_recursion_101")
        self.repo.insert_record(project_rec)
        self.repo.insert_record(lesson_rec)
        self.conn.commit()
        projects = self.repo.by_scope("project", "nexa_ikigai", limit=10)
        lessons = self.repo.by_scope("lesson", "python_recursion_101", limit=10)
        self.assertEqual([r.id for r in projects.items], [project_rec.id])
        self.assertEqual([r.id for r in lessons.items], [lesson_rec.id])

    def test_default_current_query_excludes_superseded_and_retracted(self) -> None:
        active = _rec(namespace="ns1", status=MemoryStatus.ACTIVE)
        superseded = _rec(namespace="ns1", status=MemoryStatus.SUPERSEDED)
        retracted = _rec(namespace="ns1", status=MemoryStatus.RETRACTED)
        for r in (active, superseded, retracted):
            self.repo.insert_record(r)
        self.conn.commit()
        page = self.repo.by_namespace("ns1", limit=10)
        self.assertEqual([r.id for r in page.items], [active.id])

    def test_cloud_eligibility_filter(self) -> None:
        safe = _rec(namespace="ns2", cloud_eligibility=CloudEligibility.CLOUD_SAFE)
        local = _rec(namespace="ns2", cloud_eligibility=CloudEligibility.LOCAL_ONLY)
        self.repo.insert_record(safe)
        self.repo.insert_record(local)
        self.conn.commit()
        page = self.repo.by_namespace(
            "ns2", limit=10, cloud_eligibility=CloudEligibility.CLOUD_SAFE
        )
        self.assertEqual([r.id for r in page.items], [safe.id])


class TestPagination(_RepoTestCase):
    def test_bounded_and_paginated_deterministic(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        records = [
            _rec(namespace="page.ns", content=f"item-{i}", when=base + timedelta(minutes=i))
            for i in range(25)
        ]
        for r in records:
            self.repo.insert_record(r)
        self.conn.commit()

        seen_ids: list[str] = []
        cursor = None
        for _ in range(10):  # generous upper bound on page count
            page = self.repo.by_namespace("page.ns", limit=10, cursor=cursor)
            seen_ids.extend(r.id for r in page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        self.assertEqual(len(seen_ids), 25)
        self.assertEqual(len(set(seen_ids)), 25, "no duplicates across pages")
        self.assertEqual(set(seen_ids), {r.id for r in records})

    def test_limit_above_max_rejected(self) -> None:
        with self.assertRaises(MemoryValidationError):
            self.repo.by_namespace("page.ns", limit=1001)

    def test_repeated_query_is_deterministic(self) -> None:
        for i in range(5):
            self.repo.insert_record(_rec(namespace="det.ns", content=f"x{i}"))
        self.conn.commit()
        first = [r.id for r in self.repo.by_namespace("det.ns", limit=100).items]
        second = [r.id for r in self.repo.by_namespace("det.ns", limit=100).items]
        self.assertEqual(first, second)


class TestValidAtHistoricalTruth(_RepoTestCase):
    def test_valid_at_returns_superseded_record_true_at_t(self) -> None:
        company_a = _rec(
            namespace="employer.ns", record_type="employer", content="Company A",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_until=datetime(2027, 1, 1, tzinfo=UTC),
            status=MemoryStatus.SUPERSEDED,
        )
        company_b = _rec(
            namespace="employer.ns", record_type="employer", content="Company B",
            valid_from=datetime(2027, 1, 1, tzinfo=UTC), valid_until=None,
            status=MemoryStatus.ACTIVE, supersedes_id=company_a.id,
        )
        self.repo.insert_record(company_a)
        self.repo.insert_record(company_b)
        self.conn.commit()

        june_2026 = self.repo.valid_at(
            datetime(2026, 6, 1, tzinfo=UTC), namespace="employer.ns", limit=10
        )
        self.assertEqual([r.content for r in june_2026.items], ["Company A"])
        # NO special flag required (R0073 final review §1)

    def test_valid_at_excludes_retracted_by_default(self) -> None:
        retracted = _rec(
            namespace="car.ns", record_type="car_ownership", content="Owns a Ferrari",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC), status=MemoryStatus.RETRACTED,
        )
        self.repo.insert_record(retracted)
        self.conn.commit()
        result = self.repo.valid_at(datetime(2026, 3, 1, tzinfo=UTC), namespace="car.ns", limit=10)
        self.assertEqual(result.items, ())

    def test_explicit_audit_query_can_retrieve_retracted(self) -> None:
        retracted = _rec(
            namespace="car.ns", record_type="car_ownership", content="Owns a Ferrari",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC), status=MemoryStatus.RETRACTED,
        )
        self.repo.insert_record(retracted)
        self.conn.commit()
        result = self.repo.valid_at(
            datetime(2026, 3, 1, tzinfo=UTC), namespace="car.ns", limit=10,
            statuses={MemoryStatus.RETRACTED},
        )
        self.assertEqual([r.content for r in result.items], ["Owns a Ferrari"])


class TestRelations(_RepoTestCase):
    def test_relate_and_related_to_bounded(self) -> None:
        lesson = _rec(namespace="teacher.python", record_type="lesson_event")
        concept = _rec(namespace="teacher.python", record_type="concept")
        mistake = _rec(namespace="teacher.python", record_type="mistake")
        for r in (lesson, concept, mistake):
            self.repo.insert_record(r)
        self.conn.commit()

        rel1 = MemoryRelation(
            id=uuid.uuid4().hex, source_id=lesson.id, relation_type="teaches",
            target_id=concept.id, created_at=datetime.now(UTC), status=RelationStatus.ACTIVE,
        )
        rel2 = MemoryRelation(
            id=uuid.uuid4().hex, source_id=mistake.id, relation_type="concerns",
            target_id=concept.id, created_at=datetime.now(UTC), status=RelationStatus.ACTIVE,
        )
        self.repo.insert_relation(rel1)
        self.repo.insert_relation(rel2)
        self.conn.commit()

        related = self.repo.related_to(concept.id, limit=10)
        self.assertEqual(len(related), 2)
        teaches_only = self.repo.related_to(concept.id, relation_type="teaches", limit=10)
        self.assertEqual([r.id for r in teaches_only], [rel1.id])

    def test_unrelate_soft_deletes_without_deleting_endpoints(self) -> None:
        a, b = _rec(), _rec()
        self.repo.insert_record(a)
        self.repo.insert_record(b)
        rel = MemoryRelation(
            id=uuid.uuid4().hex, source_id=a.id, relation_type="belongs_to",
            target_id=b.id, created_at=datetime.now(UTC), status=RelationStatus.ACTIVE,
        )
        self.repo.insert_relation(rel)
        self.conn.commit()

        self.repo.soft_delete_relation(rel.id)
        self.conn.commit()

        self.assertEqual(self.repo.related_to(a.id, limit=10), ())
        self.assertIsNotNone(self.repo.by_id(a.id))
        self.assertIsNotNone(self.repo.by_id(b.id))


class TestHardDeleteFkBehavior(_RepoTestCase):
    def test_hard_delete_cascades_evidence_and_relations_but_not_unrelated_records(self) -> None:
        a = _rec(content="A")
        self.repo.insert_record(a)
        b = _rec(content="B", supersedes_id=a.id)
        self.repo.insert_record(b)
        c = _rec(content="C")
        self.repo.insert_record(c)
        self.repo.insert_evidence(_evi(memory_id=a.id))
        rel = MemoryRelation(
            id=uuid.uuid4().hex, source_id=a.id, relation_type="relates_to",
            target_id=c.id, created_at=datetime.now(UTC), status=RelationStatus.ACTIVE,
        )
        self.repo.insert_relation(rel)
        self.conn.commit()

        self.repo.physically_delete_record(a.id)
        self.conn.commit()

        self.assertIsNone(self.repo.by_id(a.id))
        self.assertEqual(self.repo.evidence_for(a.id, limit=10).items, ())
        self.assertEqual(self.repo.related_to(c.id, limit=10), ())
        b_after = self.repo.by_id(b.id)
        self.assertIsNotNone(b_after)
        self.assertIsNone(
            b_after.supersedes_id, "successor's supersedes_id must be nulled, not left dangling"
        )
        c_after = self.repo.by_id(c.id)
        self.assertIsNotNone(c_after, "unrelated record C must be completely untouched")
        self.assertEqual(c_after.content, "C")


if __name__ == "__main__":
    unittest.main()
