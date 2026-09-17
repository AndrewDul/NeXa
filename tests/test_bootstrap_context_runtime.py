"""``nexa.bootstrap.build_default_context_runtime`` (R0077 §15) — the
composition root for the long-lived Context Engine objects."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.bootstrap import (  # noqa: E402
    ContextRuntime,
    build_default_context_runtime,
    build_default_session,
)
from nexa.core.context.engine import ContextEngine  # noqa: E402
from nexa.core.memory.service import MemoryService  # noqa: E402


class TestBuildDefaultContextRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("NEXA_DATA_DIR")
        os.environ["NEXA_DATA_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old_data_dir is None:
            os.environ.pop("NEXA_DATA_DIR", None)
        else:
            os.environ["NEXA_DATA_DIR"] = self._old_data_dir
        self._tmp.cleanup()

    def test_returns_context_runtime_with_correct_types(self) -> None:
        runtime = build_default_context_runtime()
        try:
            self.assertIsInstance(runtime, ContextRuntime)
            self.assertIsInstance(runtime.memory_service, MemoryService)
            self.assertIsInstance(runtime.context_engine, ContextEngine)
        finally:
            runtime.connection.close()

    def test_separate_from_build_default_session(self) -> None:
        """build_default_session() alone must never open a database
        connection -- the two composition functions stay independent
        (R0077 §15: not merged into one side-effect-heavy call)."""
        session = build_default_session()
        self.assertFalse((Path(self._tmp.name) / "core.sqlite3").exists())
        self.assertIsNotNone(session)

    def test_reuses_same_underlying_data_directory_as_memory_default(self) -> None:
        runtime = build_default_context_runtime()
        try:
            self.assertTrue((Path(self._tmp.name) / "core.sqlite3").exists())
        finally:
            runtime.connection.close()

    def test_context_engine_has_exactly_one_memory_retriever(self) -> None:
        runtime = build_default_context_runtime()
        try:
            self.assertEqual(len(runtime.context_engine._retrievers), 1)
            self.assertEqual(runtime.context_engine._retrievers[0].source_kind, "memory")
        finally:
            runtime.connection.close()

    def test_connection_is_closeable(self) -> None:
        runtime = build_default_context_runtime()
        runtime.connection.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
