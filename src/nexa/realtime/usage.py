"""``ProviderUsageEvent`` — provider-agnostic realtime-voice usage telemetry
(ADR-0004 Decision M).

Sourced from a provider's own authoritative usage report (e.g. Gemini Live
``usageMetadata``), never estimated from local audio duration. No raw audio
is ever held here — token/duration counts only. Cost estimation is entirely
optional and always a labelled *estimate*, computed from a price table
supplied by config — never hard-wired into this module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderUsageEvent:
    """One authoritative usage report from a realtime voice provider."""

    provider: str
    session_id: str
    input_text_tokens: int = 0
    input_audio_tokens: int = 0
    output_text_tokens: int = 0
    output_audio_tokens: int = 0
    wall_start: float | None = None
    wall_end: float | None = None


@dataclass(frozen=True, slots=True)
class UsagePriceTable:
    """Per-1M-token rates for an OPTIONAL, clearly-labelled cost estimate
    (ADR-0004 Decision M). Supplied by config/caller — this module has no
    opinion on what any provider actually charges."""

    input_text_per_million: float
    input_audio_per_million: float
    output_text_per_million: float
    output_audio_per_million: float


@dataclass
class SessionUsageAggregate:
    """Running per-session totals — a mutable accumulator, not a wire type.

    Retains only counts; never raw audio (ADR-0004 Decision M).
    """

    provider: str
    session_id: str
    input_text_tokens: int = 0
    input_audio_tokens: int = 0
    output_text_tokens: int = 0
    output_audio_tokens: int = 0
    event_count: int = 0

    def add(self, event: ProviderUsageEvent) -> None:
        if event.provider != self.provider or event.session_id != self.session_id:
            raise ValueError(
                f"usage event for {event.provider}/{event.session_id} does not "
                f"match aggregate {self.provider}/{self.session_id}"
            )
        self.input_text_tokens += event.input_text_tokens
        self.input_audio_tokens += event.input_audio_tokens
        self.output_text_tokens += event.output_text_tokens
        self.output_audio_tokens += event.output_audio_tokens
        self.event_count += 1

    def estimated_cost(self, prices: UsagePriceTable) -> float:
        """A labelled ESTIMATE only (ADR-0004 Decision M) — never
        authoritative billing, and never a substitute for the provider's
        own usage report."""
        return (
            self.input_text_tokens / 1_000_000 * prices.input_text_per_million
            + self.input_audio_tokens / 1_000_000 * prices.input_audio_per_million
            + self.output_text_tokens / 1_000_000 * prices.output_text_per_million
            + self.output_audio_tokens / 1_000_000 * prices.output_audio_per_million
        )
