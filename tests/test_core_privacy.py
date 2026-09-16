"""``nexa.core.privacy`` — the relocated canonical ``CloudEligibility``
(R0073 §1): NeXa Core must not depend on ``nexa.realtime``, and the
compatibility re-export at the old location must keep working unchanged.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.core.privacy import CloudEligibility, filter_cloud_safe  # noqa: E402
from nexa.realtime.privacy import CloudEligibility as ReexportedCloudEligibility  # noqa: E402
from nexa.realtime.privacy import filter_cloud_safe as reexported_filter_cloud_safe  # noqa: E402


class TestCanonicalLocation(unittest.TestCase):
    def test_core_privacy_has_the_three_values(self) -> None:
        self.assertEqual(
            {e.value for e in CloudEligibility},
            {"local_only", "cloud_safe", "cloud_with_user_approval"},
        )

    def test_filter_cloud_safe_works_from_canonical_location(self) -> None:
        facts = [("safe", CloudEligibility.CLOUD_SAFE), ("not safe", CloudEligibility.LOCAL_ONLY)]
        self.assertEqual(filter_cloud_safe(facts), ("safe",))


class TestReexportCompatibility(unittest.TestCase):
    """Existing ``from nexa.realtime.privacy import CloudEligibility``
    call sites must keep working, unchanged -- this is the SAME object,
    not a second, duplicate enum (R0073 §1)."""

    def test_reexported_type_is_identical_object(self) -> None:
        self.assertIs(ReexportedCloudEligibility, CloudEligibility)

    def test_reexported_function_is_identical_object(self) -> None:
        self.assertIs(reexported_filter_cloud_safe, filter_cloud_safe)

    def test_reexported_enum_still_behaves_correctly(self) -> None:
        facts = [("x", ReexportedCloudEligibility.CLOUD_SAFE)]
        self.assertEqual(reexported_filter_cloud_safe(facts), ("x",))


class TestDependencyDirection(unittest.TestCase):
    """R0073 §1 / final review §15 acceptance 22-23: the canonical
    authority moves inward toward nexa.core, never outward -- proven by
    source scan, not just by the re-export behaving correctly above."""

    def _imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_core_privacy_has_zero_imports_from_realtime(self) -> None:
        imports = self._imports(SRC / "nexa" / "core" / "privacy.py")
        self.assertFalse(
            any(m == "nexa.realtime" or m.startswith("nexa.realtime.") for m in imports),
            f"nexa/core/privacy.py imports from nexa.realtime: {imports}",
        )

    def test_realtime_privacy_imports_from_core_not_the_reverse(self) -> None:
        imports = self._imports(SRC / "nexa" / "realtime" / "privacy.py")
        self.assertTrue(
            any(m == "nexa.core.privacy" for m in imports),
            f"nexa/realtime/privacy.py does not import nexa.core.privacy: {imports}",
        )

    def test_entire_core_package_has_zero_imports_from_realtime(self) -> None:
        offenders = []
        for path in (SRC / "nexa" / "core").rglob("*.py"):
            imports = self._imports(path)
            if any(m == "nexa.realtime" or m.startswith("nexa.realtime.") for m in imports):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [], f"nexa/core files importing nexa.realtime: {offenders}")


if __name__ == "__main__":
    unittest.main()
