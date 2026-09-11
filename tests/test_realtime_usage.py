"""``ProviderUsageEvent`` / ``SessionUsageAggregate`` (ADR-0004 Decision M)."""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.realtime.usage import (  # noqa: E402
    ProviderUsageEvent,
    SessionUsageAggregate,
    UsagePriceTable,
)


class TestSessionUsageAggregate(unittest.TestCase):
    def test_accumulates_across_events(self) -> None:
        agg = SessionUsageAggregate(provider="gemini", session_id="s1")
        agg.add(
            ProviderUsageEvent(
                provider="gemini",
                session_id="s1",
                input_text_tokens=10,
                input_audio_tokens=100,
                output_text_tokens=5,
                output_audio_tokens=50,
            )
        )
        agg.add(
            ProviderUsageEvent(
                provider="gemini",
                session_id="s1",
                input_text_tokens=1,
                input_audio_tokens=1,
                output_text_tokens=1,
                output_audio_tokens=1,
            )
        )
        self.assertEqual(agg.input_text_tokens, 11)
        self.assertEqual(agg.input_audio_tokens, 101)
        self.assertEqual(agg.output_text_tokens, 6)
        self.assertEqual(agg.output_audio_tokens, 51)
        self.assertEqual(agg.event_count, 2)

    def test_rejects_mismatched_provider_or_session(self) -> None:
        agg = SessionUsageAggregate(provider="gemini", session_id="s1")
        with self.assertRaises(ValueError):
            agg.add(ProviderUsageEvent(provider="gemini", session_id="s2"))
        with self.assertRaises(ValueError):
            agg.add(ProviderUsageEvent(provider="other", session_id="s1"))

    def test_estimated_cost_is_labelled_and_computed_from_supplied_prices(self) -> None:
        agg = SessionUsageAggregate(
            provider="gemini",
            session_id="s1",
            input_text_tokens=1_000_000,
            input_audio_tokens=1_000_000,
            output_text_tokens=1_000_000,
            output_audio_tokens=1_000_000,
        )
        prices = UsagePriceTable(
            input_text_per_million=0.75,
            input_audio_per_million=3.00,
            output_text_per_million=4.50,
            output_audio_per_million=12.00,
        )
        self.assertAlmostEqual(agg.estimated_cost(prices), 0.75 + 3.00 + 4.50 + 12.00)

    def test_no_pricing_is_hard_wired_into_the_abstraction(self) -> None:
        # The provider abstraction itself never bakes in a specific
        # provider's price table (ADR-0004 Decision M).
        field_names = {f.name for f in dataclasses.fields(ProviderUsageEvent)}
        self.assertNotIn("cost", field_names)
        self.assertNotIn("price", field_names)


class TestNoRawAudioRetained(unittest.TestCase):
    def test_usage_event_never_carries_audio_bytes(self) -> None:
        field_names = {f.name for f in dataclasses.fields(ProviderUsageEvent)}
        for name in field_names:
            self.assertNotIn("audio_bytes", name)
            self.assertNotIn("pcm", name)
        # every field is a count, an id, or a timestamp
        expected = {
            "provider",
            "session_id",
            "input_text_tokens",
            "input_audio_tokens",
            "output_text_tokens",
            "output_audio_tokens",
            "wall_start",
            "wall_end",
        }
        self.assertEqual(field_names, expected)

    def test_aggregate_never_carries_audio_bytes(self) -> None:
        field_names = {f.name for f in dataclasses.fields(SessionUsageAggregate)}
        for name in field_names:
            self.assertNotIn("audio_bytes", name)
            self.assertNotIn("pcm", name)


if __name__ == "__main__":
    unittest.main()
