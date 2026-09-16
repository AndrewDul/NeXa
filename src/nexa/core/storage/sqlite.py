"""Shared SQLite connection, application-data location, and component-aware
schema bootstrap for NeXa Core's local persistence (R0073 §7, §8, §9).

One local database file backs every mutable Core subsystem (``core_kv`` for
:class:`~nexa.core.storage.sqlite_core_store.SQLiteCoreStore`,
``memory_records``/``memory_evidence``/``memory_relations`` for
``nexa.core.memory``, and future components) — never the repository's own
``var/`` runtime-evidence directory (that is test/hardware-acceptance
evidence for THIS checkout, not a home for a real user's lifetime data).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_FILENAME = "core.sqlite3"


class SchemaVersionError(RuntimeError):
    """A component's stored ``schema_components`` version does not match
    what this code expects. Never auto-migrated — fix the mismatch (restore
    a compatible backup, or run an explicit, reviewed migration) before
    reconnecting."""


class SchemaStateError(RuntimeError):
    """A component's ``schema_components`` row and its owned tables are
    inconsistent: a row with no tables (corruption), or tables with no row
    (partial migration / interrupted bootstrap / unrelated tables). Never
    silently adopted, never auto-recreated — an ambiguous state fails
    loudly instead of guessing."""


def default_data_dir() -> Path:
    """XDG-aware application-data directory; overridable via
    ``NEXA_DATA_DIR`` (tests always set this to a temp directory — never
    the real user's data directory)."""
    override = os.environ.get("NEXA_DATA_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "nexa"


def default_db_path() -> Path:
    return default_data_dir() / DEFAULT_DB_FILENAME


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating parent directories as needed) the shared NeXa Core
    database, with foreign keys enforced and WAL journaling enabled."""
    resolved = Path(path) if path is not None else default_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_components ("
        "component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
    )
    conn.commit()
    return conn


def bootstrap_component(
    conn: sqlite3.Connection,
    *,
    component: str,
    expected_version: int,
    tables_exist: Callable[[sqlite3.Connection], bool],
    create_schema: Callable[[sqlite3.Connection], None],
    verify_schema: Callable[[sqlite3.Connection], bool] | None = None,
) -> None:
    """Component-aware bootstrap gate (R0073 §8). Each component owns its
    own row in ``schema_components`` and is checked independently — adding
    a new component never touches another component's version, and a
    missing-row-but-existing-tables state is a hard failure, never a
    silently-assumed "first run"."""
    row = conn.execute(
        "SELECT version FROM schema_components WHERE component = ?", (component,)
    ).fetchone()
    exists = tables_exist(conn)

    if row is None:
        if exists:
            raise SchemaStateError(
                f"component {component!r} has existing tables but no "
                f"schema_components row -- ambiguous state (partial "
                f"migration, corruption, or an interrupted bootstrap); "
                f"refusing to guess"
            )
        create_schema(conn)
        conn.execute(
            "INSERT INTO schema_components (component, version) VALUES (?, ?)",
            (component, expected_version),
        )
        conn.commit()
        return

    stored_version = row["version"]
    if stored_version != expected_version:
        raise SchemaVersionError(
            f"component {component!r} schema_components.version="
            f"{stored_version!r}, expected {expected_version!r}"
        )
    if not exists:
        raise SchemaStateError(
            f"component {component!r} has a schema_components row "
            f"(version={stored_version!r}) but its owned tables are "
            f"missing -- corruption; refusing to auto-recreate"
        )
    if verify_schema is not None and not verify_schema(conn):
        raise SchemaStateError(
            f"component {component!r} tables exist but do not match the "
            f"expected schema (missing/renamed column) -- corruption; "
            f"refusing to proceed"
        )


def backup_to(conn: sqlite3.Connection, destination: Path | str) -> None:
    """Consistent, WAL-safe backup via SQLite's own online backup API
    (``sqlite3.Connection.backup`` — R0073 §7). Never a raw file copy of a
    live database: with WAL active, committed data can still live only in
    the ``-wal`` sidecar file, so copying just the main file is not
    guaranteed complete."""
    dest_path = Path(destination)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        conn.backup(dest_conn)
    finally:
        dest_conn.close()
    logger.info("memory backup written to %s", dest_path)
