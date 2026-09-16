"""``CloudEligibility`` / ``filter_cloud_safe`` (ADR-0004 Amendment 2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.privacy import CloudEligibility, filter_cloud_safe  # noqa: E402


class TestFilterCloudSafe(unittest.TestCase):
    def test_cloud_safe_facts_pass_through(self) -> None:
        facts = [("current project: NeXa voice pipeline", CloudEligibility.CLOUD_SAFE)]
        self.assertEqual(filter_cloud_safe(facts), ("current project: NeXa voice pipeline",))

    def test_local_only_facts_are_excluded(self) -> None:
        facts = [("SECRET-SHOULD-NOT-LEAK", CloudEligibility.LOCAL_ONLY)]
        self.assertEqual(filter_cloud_safe(facts), ())

    def test_cloud_with_user_approval_is_excluded_without_explicit_relabel(self) -> None:
        """A caller that has obtained approval must pass the fact through as
        CLOUD_SAFE itself -- this filter never treats
        CLOUD_WITH_USER_APPROVAL as sufficient on its own, so an approval
        decision is never made implicitly inside the filter."""
        facts = [("wants to share this once approved", CloudEligibility.CLOUD_WITH_USER_APPROVAL)]
        self.assertEqual(filter_cloud_safe(facts), ())

    def test_mixed_list_keeps_only_cloud_safe_in_order(self) -> None:
        facts = [
            ("fact A (safe)", CloudEligibility.CLOUD_SAFE),
            ("fact B (local only)", CloudEligibility.LOCAL_ONLY),
            ("fact C (safe)", CloudEligibility.CLOUD_SAFE),
            ("fact D (needs approval)", CloudEligibility.CLOUD_WITH_USER_APPROVAL),
        ]
        self.assertEqual(filter_cloud_safe(facts), ("fact A (safe)", "fact C (safe)"))

    def test_empty_input_returns_empty_tuple(self) -> None:
        self.assertEqual(filter_cloud_safe([]), ())


if __name__ == "__main__":
    unittest.main()
