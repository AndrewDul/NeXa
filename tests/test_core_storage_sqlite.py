"""``nexa.core.storage.sqlite`` / ``sqlite_core_store`` (R0073 §7, §8, §9)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.core.storage.sqlite import (  # noqa: E402
    SchemaStateError,
    SchemaVersionError,
    backup_to,
    bootstrap_component,
    connect,
    default_data_dir,
)
from nexa.core.storage.sqlite_core_store import SQLiteCoreStore  # noqa: E402


class TestDefaultDataDir(unittest.TestCase):
    def test_respects_nexa_data_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("NEXA_DATA_DIR")
            os.environ["NEXA_DATA_DIR"] = tmp
            try:
                self.assertEqual(default_data_dir(), Path(tmp))
            finally:
                if old is None:
                    os.environ.pop("NEXA_DATA_DIR", None)
                else:
                    os.environ["NEXA_DATA_DIR"] = old

    def test_respects_xdg_data_home_when_no_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_override = os.environ.pop("NEXA_DATA_DIR", None)
            old_xdg = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                self.assertEqual(default_data_dir(), Path(tmp) / "nexa")
            finally:
                if old_override is not None:
                    os.environ["NEXA_DATA_DIR"] = old_override
                if old_xdg is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_xdg

    def test_never_defaults_under_repo_var_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("NEXA_DATA_DIR")
            os.environ["NEXA_DATA_DIR"] = tmp
            try:
                resolved = default_data_dir()
            finally:
                if old is None:
                    os.environ.pop("NEXA_DATA_DIR", None)
                else:
                    os.environ["NEXA_DATA_DIR"] = old
            self.assertNotIn("var", resolved.parts)
            self.assertFalse(str(resolved).startswith(str(REPO_ROOT)))


class _TempDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "core.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestConnectAndBootstrap(_TempDbTestCase):
    def test_connect_creates_parent_dirs_and_schema_components_table(self) -> None:
        nested = self.db_path.parent / "nested" / "core.sqlite3"
        conn = connect(nested)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_components'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_first_run_creates_tables_and_version_row(self) -> None:
        conn = connect(self.db_path)
        try:
            bootstrap_component(
                conn, component="widget", expected_version=1,
                tables_exist=lambda c: c.execute(
                    "SELECT name FROM sqlite_master WHERE name='widgets'"
                ).fetchone() is not None,
                create_schema=lambda c: c.execute("CREATE TABLE widgets (id TEXT PRIMARY KEY)"),
            )
            row = conn.execute(
                "SELECT version FROM schema_components WHERE component='widget'"
            ).fetchone()
            self.assertEqual(row["version"], 1)
        finally:
            conn.close()

    def test_version_mismatch_raises_schema_version_error(self) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute("INSERT INTO schema_components (component, version) VALUES (?, ?)",
                         ("widget", 99))
            conn.commit()
            with self.assertRaises(SchemaVersionError):
                bootstrap_component(
                    conn, component="widget", expected_version=1,
                    tables_exist=lambda c: True, create_schema=lambda c: None,
                )
        finally:
            conn.close()

    def test_missing_row_with_existing_tables_fails_loudly(self) -> None:
        """R0073 §8: a missing version row is NOT automatically "first
        run" if the component's tables already exist -- ambiguous state,
        refuse to guess."""
        conn = connect(self.db_path)
        try:
            with self.assertRaises(SchemaStateError):
                bootstrap_component(
                    conn, component="widget", expected_version=1,
                    tables_exist=lambda c: True,  # tables exist despite no row
                    create_schema=lambda c: (_ for _ in ()).throw(
                        AssertionError("create_schema must not run")
                    ),
                )
        finally:
            conn.close()

    def test_present_row_with_missing_tables_fails_loudly(self) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute("INSERT INTO schema_components (component, version) VALUES (?, ?)",
                         ("widget", 1))
            conn.commit()
            with self.assertRaises(SchemaStateError):
                bootstrap_component(
                    conn, component="widget", expected_version=1,
                    tables_exist=lambda c: False, create_schema=lambda c: None,
                )
        finally:
            conn.close()

    def test_verify_schema_failure_fails_loudly(self) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute("INSERT INTO schema_components (component, version) VALUES (?, ?)",
                         ("widget", 1))
            conn.commit()
            with self.assertRaises(SchemaStateError):
                bootstrap_component(
                    conn, component="widget", expected_version=1,
                    tables_exist=lambda c: True, create_schema=lambda c: None,
                    verify_schema=lambda c: False,
                )
        finally:
            conn.close()


class TestBackupWalSafe(_TempDbTestCase):
    def test_backup_via_connection_backup_api_captures_committed_wal_data(self) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t (v) VALUES ('hello')")
            conn.commit()
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal"
            )

            backup_path = Path(self._tmp.name) / "backup.sqlite3"
            backup_to(conn, backup_path)

            independent = sqlite3.connect(str(backup_path))
            try:
                row = independent.execute("SELECT v FROM t").fetchone()
                self.assertEqual(row[0], "hello")
            finally:
                independent.close()
        finally:
            conn.close()

    def test_backup_not_a_naive_file_copy(self) -> None:
        """The backup file must be openable/self-consistent independently
        -- proving the online backup API was used, not `cp`, which under
        WAL would risk missing data still only in the -wal sidecar file."""
        conn = connect(self.db_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.commit()
            for i in range(50):
                conn.execute("INSERT INTO t (v) VALUES (?)", (f"row-{i}",))
            conn.commit()  # committed, but may still live only in -wal

            backup_path = Path(self._tmp.name) / "backup2.sqlite3"
            backup_to(conn, backup_path)

            independent = sqlite3.connect(str(backup_path))
            try:
                count = independent.execute("SELECT COUNT(*) FROM t").fetchone()[0]
                self.assertEqual(count, 50)
            finally:
                independent.close()
        finally:
            conn.close()


class TestSQLiteCoreStore(_TempDbTestCase):
    def test_put_get_delete_roundtrip(self) -> None:
        conn = connect(self.db_path)
        try:
            store = SQLiteCoreStore(conn)
            self.assertIsNone(store.get("settings", "k1"))
            store.put("settings", "k1", {"a": 1, "b": [1, 2, 3]})
            self.assertEqual(store.get("settings", "k1"), {"a": 1, "b": [1, 2, 3]})
            store.put("settings", "k1", {"a": 2})
            self.assertEqual(store.get("settings", "k1"), {"a": 2})
            store.delete("settings", "k1")
            self.assertIsNone(store.get("settings", "k1"))
        finally:
            conn.close()

    def test_collections_are_namespaced(self) -> None:
        conn = connect(self.db_path)
        try:
            store = SQLiteCoreStore(conn)
            store.put("a", "k", {"v": 1})
            store.put("b", "k", {"v": 2})
            self.assertEqual(store.get("a", "k"), {"v": 1})
            self.assertEqual(store.get("b", "k"), {"v": 2})
        finally:
            conn.close()

    def test_persists_across_reconnect(self) -> None:
        conn = connect(self.db_path)
        SQLiteCoreStore(conn).put("settings", "k1", {"a": 1})
        conn.close()

        conn2 = connect(self.db_path)
        try:
            self.assertEqual(SQLiteCoreStore(conn2).get("settings", "k1"), {"a": 1})
        finally:
            conn2.close()


if __name__ == "__main__":
    unittest.main()
