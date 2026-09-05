"""M0 foundation checks.

These are deliberately minimal: they only assert that the repository is a
well-formed, importable Python project and that the core documentation and
convention files exist. They are runnable with plain ``unittest`` (no third-party
dependency) as well as under ``pytest``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestPackage(unittest.TestCase):
    def test_package_imports(self) -> None:
        import nexa

        self.assertTrue(hasattr(nexa, "__version__"))

    def test_version_is_string(self) -> None:
        import nexa

        self.assertIsInstance(nexa.__version__, str)


class TestFoundationFiles(unittest.TestCase):
    REQUIRED = [
        "README.md",
        "AGENTS.md",
        ".gitignore",
        ".env.example",
        "pyproject.toml",
        "docs/CURRENT_STATE.md",
        "docs/ROADMAP.md",
        "docs/architecture/FOUNDATION_ARCHITECTURE.md",
        "docs/decisions/ADR-TEMPLATE.md",
        "docs/decisions/ADR-0001_project_foundation.md",
        "docs/legacy/LEGACY_NEXA_INDEX.md",
        "docs/reports/R0001_project_foundation_20260831.md",
        "docs/reports/R0002_m1_natural_conversation_research_20260831.md",
        "docs/reports/R0003_m1_0b_current_small_model_sweep_20260901.md",
        "docs/reports/R0004_m1_1_canonical_text_conversation_path_20260905.md",
        "docs/reports/R0005_m2_realtime_voice_oss_research_20260905.md",
        "docs/research/RESEARCH_POLICY.md",
        "docs/research/M1_NATURAL_CONVERSATION_RESEARCH.md",
        "docs/research/M1_0B_CURRENT_SMALL_MODEL_SWEEP.md",
        "docs/testing/TEST_STRATEGY.md",
        "docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md",
        "docs/testing/M1_OPERATOR_BLIND_CONVERSATION_TEST.md",
        "docs/troubleshooting/README.md",
        "docs/decisions/ADR-0002_text_conversation_foundation.md",
    ]

    def test_required_files_exist(self) -> None:
        missing = [p for p in self.REQUIRED if not (REPO_ROOT / p).is_file()]
        self.assertEqual(missing, [], f"missing foundation files: {missing}")


if __name__ == "__main__":
    unittest.main()
