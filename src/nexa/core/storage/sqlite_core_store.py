"""``SQLiteCoreStore`` — the concrete :class:`~nexa.core.storage.contracts.CoreStore`
implementation, backed by a generic ``core_kv`` table (R0073).

For future SIMPLE mutable subsystems only (e.g. a small settings blob).
``nexa.core.memory`` does NOT use this — Memory has its own typed
``memory_records``/``memory_evidence``/``memory_relations`` tables and talks
to them directly (``CoreStore.get/put/delete`` is a low-level seam, not a
subsystem-facing API — see ``contracts.py``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from .sqlite import bootstrap_component

COMPONENT = "core_storage"
SCHEMA_VERSION = 1


def _tables_exist(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='core_kv'"
    ).fetchone()
    return row is not None


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE core_kv ("
        "collection TEXT NOT NULL, "
        "key TEXT NOT NULL, "
        "value_json TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "PRIMARY KEY (collection, key))"
    )


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteCoreStore:
    """Concrete ``CoreStore`` over the shared NeXa Core SQLite database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        bootstrap_component(
            conn,
            component=COMPONENT,
            expected_version=SCHEMA_VERSION,
            tables_exist=_tables_exist,
            create_schema=_create_schema,
        )

    def get(self, collection: str, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT value_json FROM core_kv WHERE collection = ? AND key = ?",
            (collection, key),
        ).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def put(self, collection: str, key: str, value: dict) -> None:
        value_json = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self._conn:
            self._conn.execute(
                "INSERT INTO core_kv (collection, key, value_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (collection, key) DO UPDATE SET "
                "value_json = excluded.value_json, updated_at = excluded.updated_at",
                (collection, key, value_json, _utcnow_iso()),
            )

    def delete(self, collection: str, key: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM core_kv WHERE collection = ? AND key = ?", (collection, key)
            )
