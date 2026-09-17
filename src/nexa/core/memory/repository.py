"""``MemoryRepository`` — typed SQLite CRUD and bounded/paginated retrieval
for the NeXa Memory Platform (R0073). Internal implementation detail: no
future subsystem writes arbitrary SQL against ``memory_records``/
``memory_evidence``/``memory_relations`` directly, and no external caller
should import this module — consume :class:`~nexa.core.memory.service.MemoryService`
instead (R0073 §11 ownership diagram).
"""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from ..privacy import CloudEligibility, most_restrictive_cloud_eligibility
from ..storage.sqlite import bootstrap_component
from .models import (
    MemoryCategory,
    MemoryEvidence,
    MemoryProvenance,
    MemoryRecord,
    MemoryRelation,
    MemoryStatus,
    NamespaceSummary,
    Page,
    RelationStatus,
)
from .validation import validate_limit

COMPONENT = "memory"
SCHEMA_VERSION = 1

#: "current knowledge" queries (by_namespace/by_scope/recent) default here
CURRENT_STATUSES: frozenset[MemoryStatus] = frozenset({MemoryStatus.ACTIVE})

#: valid_at() historical-truth default: ACTIVE + SUPERSEDED, NEVER RETRACTED
#: (R0073 final review §1 -- retraction means "explicitly withdrawn/invalid",
#: not "merely old", so it must not surface as historical truth by default)
HISTORICAL_STATUSES: frozenset[MemoryStatus] = frozenset(
    {MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED}
)

_SCHEMA_SQL = """
CREATE TABLE memory_records (
    id                 TEXT PRIMARY KEY,
    namespace          TEXT NOT NULL,
    category           TEXT NOT NULL,
    record_type        TEXT NOT NULL,
    payload_version    INTEGER NOT NULL DEFAULT 1,
    scope_type         TEXT,
    scope_id           TEXT,
    content            TEXT NOT NULL,
    payload_json       TEXT,
    valid_from         TEXT,
    valid_until        TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    cloud_eligibility  TEXT NOT NULL,
    status             TEXT NOT NULL,
    supersedes_id      TEXT REFERENCES memory_records(id) ON DELETE SET NULL,
    CHECK (payload_json IS NULL OR
           (json_valid(payload_json) AND json_type(payload_json) = 'object'))
);
CREATE INDEX idx_memory_namespace   ON memory_records(namespace, status);
CREATE INDEX idx_memory_record_type ON memory_records(record_type, status);
CREATE INDEX idx_memory_category    ON memory_records(category, status);
CREATE INDEX idx_memory_scope       ON memory_records(scope_type, scope_id, status);
CREATE INDEX idx_memory_updated_at  ON memory_records(updated_at);
CREATE INDEX idx_memory_valid_range ON memory_records(valid_from, valid_until);

CREATE TABLE memory_evidence (
    id            TEXT PRIMARY KEY,
    memory_id     TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    provenance    TEXT NOT NULL,
    source_kind   TEXT,
    source_ref    TEXT,
    confidence    REAL,
    observed_at   TEXT,
    created_at    TEXT NOT NULL,
    payload_json  TEXT,
    CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    CHECK (payload_json IS NULL OR json_valid(payload_json))
);
CREATE INDEX idx_evidence_memory_id ON memory_evidence(memory_id, observed_at);

CREATE TABLE memory_relations (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    relation_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX idx_relations_source ON memory_relations(source_id, relation_type, status);
CREATE INDEX idx_relations_target ON memory_relations(target_id, relation_type, status);
"""

_EXPECTED_TABLES = ("memory_records", "memory_evidence", "memory_relations")


def _tables_exist(conn: sqlite3.Connection) -> bool:
    """True if ANY owned table already exists -- not just "all of them" --
    so a PARTIAL set (interrupted bootstrap / corruption) is correctly
    treated as "tables exist" for the ambiguous-state check in
    ``bootstrap_component``, not misclassified as "no tables yet, safe to
    create" (which would collide with the surviving table)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
        _EXPECTED_TABLES,
    ).fetchall()
    return len(rows) > 0


def _verify_schema(conn: sqlite3.Connection) -> bool:
    # Lightweight sanity check, not a full migration-diff engine (R0073 §8):
    # confirm each expected table has at least its primary/required columns.
    expected_columns = {
        "memory_records": {"id", "namespace", "category", "record_type", "status"},
        "memory_evidence": {"id", "memory_id", "provenance"},
        "memory_relations": {"id", "source_id", "relation_type", "target_id"},
    }
    for table, required in expected_columns.items():
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not required.issubset(cols):
            return False
    return True


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _encode_cursor(updated_at: str, id_: str) -> str:
    raw = f"{updated_at}|{id_}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    updated_at, id_ = raw.split("|", 1)
    return updated_at, id_


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        namespace=row["namespace"],
        category=MemoryCategory(row["category"]),
        record_type=row["record_type"],
        payload_version=row["payload_version"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        content=row["content"],
        payload=json.loads(row["payload_json"]) if row["payload_json"] is not None else None,
        valid_from=_parse_iso(row["valid_from"]),
        valid_until=_parse_iso(row["valid_until"]),
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        cloud_eligibility=CloudEligibility(row["cloud_eligibility"]),
        status=MemoryStatus(row["status"]),
        supersedes_id=row["supersedes_id"],
    )


def _row_to_evidence(row: sqlite3.Row) -> MemoryEvidence:
    return MemoryEvidence(
        id=row["id"],
        memory_id=row["memory_id"],
        provenance=MemoryProvenance(row["provenance"]),
        source_kind=row["source_kind"],
        source_ref=row["source_ref"],
        confidence=row["confidence"],
        observed_at=_parse_iso(row["observed_at"]),
        created_at=_parse_iso(row["created_at"]),
        payload=json.loads(row["payload_json"]) if row["payload_json"] is not None else None,
    )


def _row_to_relation(row: sqlite3.Row) -> MemoryRelation:
    return MemoryRelation(
        id=row["id"],
        source_id=row["source_id"],
        relation_type=row["relation_type"],
        target_id=row["target_id"],
        created_at=_parse_iso(row["created_at"]),
        status=RelationStatus(row["status"]),
    )


class MemoryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        bootstrap_component(
            conn,
            component=COMPONENT,
            expected_version=SCHEMA_VERSION,
            tables_exist=_tables_exist,
            create_schema=_create_schema,
            verify_schema=_verify_schema,
        )

    @property
    def conn(self) -> sqlite3.Connection:
        """Exposed so :class:`~nexa.core.memory.service.MemoryService` can
        wrap a multi-statement write in one atomic ``with repository.conn:``
        transaction."""
        return self._conn

    # ---- writes (low-level; no dedup/validation policy -- see MemoryService) ----

    def insert_record(self, record: MemoryRecord) -> None:
        payload_json = json.dumps(record.payload, sort_keys=True, separators=(",", ":")) \
            if record.payload is not None else None
        self._conn.execute(
            "INSERT INTO memory_records (id, namespace, category, record_type, "
            "payload_version, scope_type, scope_id, content, payload_json, "
            "valid_from, valid_until, created_at, updated_at, cloud_eligibility, "
            "status, supersedes_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.id, record.namespace, record.category.value, record.record_type,
                record.payload_version, record.scope_type, record.scope_id, record.content,
                payload_json, _iso(record.valid_from), _iso(record.valid_until),
                _iso(record.created_at), _iso(record.updated_at),
                record.cloud_eligibility.value, record.status.value, record.supersedes_id,
            ),
        )

    def insert_evidence(self, evidence: MemoryEvidence) -> None:
        payload_json = json.dumps(evidence.payload, sort_keys=True, separators=(",", ":")) \
            if evidence.payload is not None else None
        self._conn.execute(
            "INSERT INTO memory_evidence (id, memory_id, provenance, source_kind, "
            "source_ref, confidence, observed_at, created_at, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                evidence.id, evidence.memory_id, evidence.provenance.value,
                evidence.source_kind, evidence.source_ref, evidence.confidence,
                _iso(evidence.observed_at), _iso(evidence.created_at), payload_json,
            ),
        )

    def find_active_duplicate(
        self, *, namespace: str, record_type: str,
        scope_type: str | None, scope_id: str | None, content: str,
    ) -> str | None:
        row = self._conn.execute(
            "SELECT id FROM memory_records WHERE namespace = ? AND record_type = ? "
            "AND scope_type IS ? AND scope_id IS ? AND content = ? AND status = ?",
            (namespace, record_type, scope_type, scope_id, content, MemoryStatus.ACTIVE.value),
        ).fetchone()
        return row["id"] if row is not None else None

    def mark_superseded(self, id_: str, *, valid_until: datetime | None) -> None:
        now = _iso(_utcnow())
        if valid_until is not None:
            self._conn.execute(
                "UPDATE memory_records SET status = ?, updated_at = ?, valid_until = ? "
                "WHERE id = ?",
                (MemoryStatus.SUPERSEDED.value, now, _iso(valid_until), id_),
            )
        else:
            self._conn.execute(
                "UPDATE memory_records SET status = ?, updated_at = ? WHERE id = ?",
                (MemoryStatus.SUPERSEDED.value, now, id_),
            )

    def mark_retracted(self, id_: str) -> None:
        self._conn.execute(
            "UPDATE memory_records SET status = ?, updated_at = ? WHERE id = ?",
            (MemoryStatus.RETRACTED.value, _iso(_utcnow()), id_),
        )

    def update_cloud_eligibility(self, id_: str, cloud_eligibility: CloudEligibility) -> None:
        self._conn.execute(
            "UPDATE memory_records SET cloud_eligibility = ?, updated_at = ? WHERE id = ?",
            (cloud_eligibility.value, _iso(_utcnow()), id_),
        )

    def physically_delete_record(self, id_: str) -> None:
        """Relies entirely on the schema's ``ON DELETE CASCADE``/``SET NULL``
        (R0073 final review §9 / Revision 3 §9) for atomic, correct FK
        cleanup -- evidence and relations referencing ``id_`` are removed,
        successors' ``supersedes_id`` pointing at ``id_`` are nulled, and
        nothing else is touched."""
        self._conn.execute("DELETE FROM memory_records WHERE id = ?", (id_,))

    def insert_relation(self, relation: MemoryRelation) -> None:
        self._conn.execute(
            "INSERT INTO memory_relations (id, source_id, relation_type, target_id, "
            "created_at, status) VALUES (?,?,?,?,?,?)",
            (
                relation.id, relation.source_id, relation.relation_type,
                relation.target_id, _iso(relation.created_at), relation.status.value,
            ),
        )

    def soft_delete_relation(self, relation_id: str) -> None:
        self._conn.execute(
            "UPDATE memory_relations SET status = ? WHERE id = ?",
            (RelationStatus.DELETED.value, relation_id),
        )

    # ---- reads ----

    def by_id(self, id_: str) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM memory_records WHERE id = ?", (id_,)
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def by_ids(self, ids: Sequence[str]) -> tuple[MemoryRecord, ...]:
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT * FROM memory_records WHERE id IN ({placeholders})", tuple(ids)
        ).fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def _query(
        self, *, clauses: list[str], params: list, statuses: Iterable[MemoryStatus] | None,
        cloud_eligibility: CloudEligibility | None, limit: int, cursor: str | None,
        order_col: str = "updated_at",
    ) -> Page[MemoryRecord]:
        validate_limit(limit)
        clauses = list(clauses)
        values = list(params)
        if statuses is not None:
            statuses = tuple(statuses)
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(s.value for s in statuses)
        if cloud_eligibility is not None:
            clauses.append("cloud_eligibility = ?")
            values.append(cloud_eligibility.value)
        if cursor is not None:
            cur_val, cur_id = _decode_cursor(cursor)
            clauses.append(f"({order_col} < ? OR ({order_col} = ? AND id < ?))")
            values.extend([cur_val, cur_val, cur_id])
        where_sql = " AND ".join(clauses) if clauses else "1=1"
        sql = (
            f"SELECT * FROM memory_records WHERE {where_sql} "
            f"ORDER BY {order_col} DESC, id DESC LIMIT ?"
        )
        values.append(limit + 1)
        rows = self._conn.execute(sql, values).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(_row_to_record(r) for r in rows)
        next_cursor = (
            _encode_cursor(rows[-1][order_col], rows[-1]["id"]) if has_more and rows else None
        )
        return Page(items=items, next_cursor=next_cursor)

    def by_namespace(
        self, namespace: str, *, record_type: str | None = None,
        category: MemoryCategory | None = None,
        cloud_eligibility: CloudEligibility | None = None,
        statuses: Iterable[MemoryStatus] | None = CURRENT_STATUSES,
        limit: int, cursor: str | None = None,
    ) -> Page[MemoryRecord]:
        clauses = ["namespace = ?"]
        params: list = [namespace]
        if record_type is not None:
            clauses.append("record_type = ?")
            params.append(record_type)
        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)
        return self._query(
            clauses=clauses, params=params, statuses=statuses,
            cloud_eligibility=cloud_eligibility, limit=limit, cursor=cursor,
        )

    def by_scope(
        self, scope_type: str, scope_id: str, *,
        cloud_eligibility: CloudEligibility | None = None,
        statuses: Iterable[MemoryStatus] | None = CURRENT_STATUSES,
        limit: int, cursor: str | None = None,
    ) -> Page[MemoryRecord]:
        return self._query(
            clauses=["scope_type = ?", "scope_id = ?"], params=[scope_type, scope_id],
            statuses=statuses, cloud_eligibility=cloud_eligibility, limit=limit, cursor=cursor,
        )

    def recent(
        self, limit: int, *, namespace: str | None = None, since: datetime | None = None,
        cloud_eligibility: CloudEligibility | None = None,
        statuses: Iterable[MemoryStatus] | None = CURRENT_STATUSES,
        cursor: str | None = None,
    ) -> Page[MemoryRecord]:
        clauses: list[str] = []
        params: list = []
        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if since is not None:
            clauses.append("updated_at >= ?")
            params.append(_iso(since))
        return self._query(
            clauses=clauses, params=params, statuses=statuses,
            cloud_eligibility=cloud_eligibility, limit=limit, cursor=cursor,
        )

    def valid_at(
        self, at: datetime, *, namespace: str | None = None, record_type: str | None = None,
        statuses: Iterable[MemoryStatus] | None = HISTORICAL_STATUSES,
        limit: int, cursor: str | None = None,
    ) -> Page[MemoryRecord]:
        """Historical-truth query (R0073 §2, final review §1): by default
        returns ACTIVE + SUPERSEDED records whose ``[valid_from,
        valid_until)`` interval contains ``at`` — NEVER RETRACTED by
        default (retraction means "withdrawn/invalid", not "historically
        true but superseded"). Pass ``statuses={MemoryStatus.RETRACTED}``
        explicitly for an audit query."""
        at_iso = _iso(at)
        clauses = [
            "(valid_from IS NULL OR valid_from <= ?)",
            "(valid_until IS NULL OR valid_until > ?)",
        ]
        params: list = [at_iso, at_iso]
        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if record_type is not None:
            clauses.append("record_type = ?")
            params.append(record_type)
        return self._query(
            clauses=clauses, params=params, statuses=statuses,
            cloud_eligibility=None, limit=limit, cursor=cursor,
        )

    def evidence_for(
        self, memory_id: str, *, limit: int, cursor: str | None = None
    ) -> Page[MemoryEvidence]:
        validate_limit(limit)
        clauses = ["memory_id = ?"]
        values: list = [memory_id]
        if cursor is not None:
            cur_val, cur_id = _decode_cursor(cursor)
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            values.extend([cur_val, cur_val, cur_id])
        where_sql = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM memory_evidence WHERE {where_sql} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?"
        )
        values.append(limit + 1)
        rows = self._conn.execute(sql, values).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(_row_to_evidence(r) for r in rows)
        next_cursor = (
            _encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
        )
        return Page(items=items, next_cursor=next_cursor)

    def related_to(
        self, memory_id: str, *, relation_type: str | None = None, limit: int
    ) -> tuple[MemoryRelation, ...]:
        validate_limit(limit)
        clauses = ["(source_id = ? OR target_id = ?)", "status = ?"]
        params: list = [memory_id, memory_id, RelationStatus.ACTIVE.value]
        if relation_type is not None:
            clauses.append("relation_type = ?")
            params.append(relation_type)
        where_sql = " AND ".join(clauses)
        rows = self._conn.execute(
            f"SELECT * FROM memory_relations WHERE {where_sql} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(_row_to_relation(r) for r in rows)

    def namespace_summary(self, *, limit: int) -> tuple[NamespaceSummary, ...]:
        """Knowledge Awareness metadata (R0075 §7/§0A): the top ``limit``
        ACTIVE namespaces by recency, each with a record count, freshest
        ``updated_at``, and an already-collapsed most-restrictive
        ``cloud_eligibility`` — ONE bounded SQL statement (a CTE that
        selects the top namespaces first, then aggregates only within
        them), never a full unbounded ``GROUP BY`` over every namespace in
        the table, never ``content``/``payload_json``, never one query per
        namespace."""
        validate_limit(limit)
        rows = self._conn.execute(
            """
            WITH top_namespaces AS (
                SELECT namespace, MAX(updated_at) AS freshest
                FROM memory_records
                WHERE status = ?
                GROUP BY namespace
                ORDER BY freshest DESC, namespace ASC
                LIMIT ?
            )
            SELECT m.namespace AS namespace, m.cloud_eligibility AS cloud_eligibility,
                   COUNT(*) AS record_count, MAX(m.updated_at) AS freshest,
                   t.freshest AS namespace_freshest
            FROM memory_records AS m
            JOIN top_namespaces AS t ON t.namespace = m.namespace
            WHERE m.status = ?
            GROUP BY m.namespace, m.cloud_eligibility
            ORDER BY t.freshest DESC, m.namespace ASC
            """,
            (MemoryStatus.ACTIVE.value, limit, MemoryStatus.ACTIVE.value),
        ).fetchall()

        by_namespace: dict[str, list[sqlite3.Row]] = {}
        order: list[str] = []
        for row in rows:
            ns = row["namespace"]
            if ns not in by_namespace:
                by_namespace[ns] = []
                order.append(ns)
            by_namespace[ns].append(row)

        summaries = []
        for ns in order:
            group = by_namespace[ns]
            summaries.append(
                NamespaceSummary(
                    namespace=ns,
                    record_count=sum(r["record_count"] for r in group),
                    freshest=_parse_iso(group[0]["namespace_freshest"]),
                    cloud_eligibility=most_restrictive_cloud_eligibility(
                        CloudEligibility(r["cloud_eligibility"]) for r in group
                    ),
                )
            )
        return tuple(summaries)
