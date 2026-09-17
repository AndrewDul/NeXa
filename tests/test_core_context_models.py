"""``nexa.core.context.models`` — type invariants (R0075 Revision 3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.core.context.models import (  # noqa: E402
    KnowledgeAvailability,
    KnowledgeGapState,
    RetrievalOutcome,
    RetrievalResult,
)


class TestEnumSeparation(unittest.TestCase):
    """R0075 final review §2: source-accessibility, per-call outcome, and
    aggregate epistemic gap are three separate, non-overlapping-in-purpose
    types -- not one overloaded enum."""

    def test_knowledge_availability_has_no_loaded_or_unknown(self) -> None:
        values = {e.value for e in KnowledgeAvailability}
        self.assertNotIn("loaded", values)
        self.assertNotIn("unknown", values)
        self.assertEqual(values, {"available", "unavailable", "permission_required"})

    def test_knowledge_gap_state_has_unknown(self) -> None:
        values = {e.value for e in KnowledgeGapState}
        self.assertEqual(values, {"unknown", "no_match", "unavailable", "permission_required"})

    def test_retrieval_outcome_has_ok_and_no_match(self) -> None:
        values = {e.value for e in RetrievalOutcome}
        self.assertEqual(values, {"ok", "no_match", "unavailable", "permission_required"})


class TestRetrievalResultInvariant(unittest.TestCase):
    def test_ok_requires_nonempty_items(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalResult(descriptor_id="d1", outcome=RetrievalOutcome.OK, items=())

    def test_no_match_requires_empty_items(self) -> None:
        item = _dummy_item()
        with self.assertRaises(ValueError):
            RetrievalResult(descriptor_id="d1", outcome=RetrievalOutcome.NO_MATCH, items=(item,))

    def test_no_match_carries_no_reason_code(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalResult(
                descriptor_id="d1", outcome=RetrievalOutcome.NO_MATCH, reason_code="retrieval_error"
            )

    def test_ok_carries_no_reason_code(self) -> None:
        item = _dummy_item()
        with self.assertRaises(ValueError):
            RetrievalResult(
                descriptor_id="d1", outcome=RetrievalOutcome.OK, items=(item,),
                reason_code="retrieval_error",
            )

    def test_unavailable_requires_reason_code(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalResult(descriptor_id="d1", outcome=RetrievalOutcome.UNAVAILABLE)

    def test_permission_required_requires_reason_code(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalResult(descriptor_id="d1", outcome=RetrievalOutcome.PERMISSION_REQUIRED)

    def test_valid_ok_construction(self) -> None:
        item = _dummy_item()
        result = RetrievalResult(descriptor_id="d1", outcome=RetrievalOutcome.OK, items=(item,))
        self.assertEqual(result.items, (item,))

    def test_valid_no_match_construction(self) -> None:
        result = RetrievalResult(descriptor_id="d1", outcome=RetrievalOutcome.NO_MATCH)
        self.assertEqual(result.items, ())

    def test_valid_unavailable_construction(self) -> None:
        result = RetrievalResult(
            descriptor_id="d1", outcome=RetrievalOutcome.UNAVAILABLE,
            reason_code="source_unreachable",
        )
        self.assertEqual(result.reason_code, "source_unreachable")


def _dummy_item():
    from datetime import UTC, datetime

    from nexa.core.context.models import ContextItem, ContextItemPriority
    from nexa.core.privacy import CloudEligibility

    return ContextItem(
        source_kind="memory", source_id="m1", domain="ns", record_type="thing",
        scope_type=None, scope_id=None, content="x", category="fact",
        cloud_eligibility=CloudEligibility.LOCAL_ONLY, priority=ContextItemPriority.OPTIONAL,
        reason_selected="test", freshness=datetime.now(UTC),
    )


if __name__ == "__main__":
    unittest.main()
