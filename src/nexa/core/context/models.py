"""NeXa Context Engine + Knowledge Awareness data model (R0075 Revision 3).

Three knowledge levels, never conflated:

    Level 1 -- Active Context: CurrentTurnContext.selected_context_items
        (small, bounded, immediately usable this turn)
    Level 2 -- Knowledge Awareness: CurrentTurnContext.knowledge_references
        (this exists, roughly what/where, NOT loaded -- metadata only,
        never a second copy of canonical knowledge)
    Level 3 -- Full Source: MemoryService (today); future Teacher, LiFeOS,
        files, devices

Epistemic states are deliberately represented by THREE separate small
types, not one overloaded enum (R0075 final review §2):

    KnowledgeAvailability  -- accessibility of a KNOWN SOURCE (a descriptor)
    RetrievalOutcome        -- the result of ONE retrieve() call
    KnowledgeGapState        -- the aggregate epistemic gap surfaced in output

There is no "LOADED" value anywhere -- "I know this now" is represented
structurally by an item's presence in ``selected_context_items``, never
duplicated as a constructible enum value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from ...conversation.session import ConversationSession
from ...conversation.turn import ConversationTurn
from ..identity.model import NeXaIdentity
from ..privacy import CloudEligibility


class TemporalIntent(StrEnum):
    """Set by the CALLER (R0075 §18/§25) -- Context Engine never guesses
    this, and never asks Memory to guess it either."""

    CURRENT = "current"
    HISTORICAL = "historical"


class KnowledgeAvailability(StrEnum):
    """Accessibility of a KNOWN SOURCE (a :class:`KnowledgeDescriptor`).
    If no descriptor exists for a domain, the source isn't
    'unknown-availability' -- there is simply no descriptor; that absence
    is what produces a :class:`KnowledgeGap` with
    ``state=KnowledgeGapState.UNKNOWN`` downstream, not a value here."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"


class KnowledgeGapState(StrEnum):
    """The epistemic state surfaced in
    ``CurrentTurnContext.knowledge_gaps`` -- deliberately not the same
    type as :class:`KnowledgeAvailability`: a gap can be ``UNKNOWN`` (no
    descriptor ever matched), which has no equivalent as a property of an
    existing descriptor."""

    UNKNOWN = "unknown"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"


class KnowledgeDescriptorKind(StrEnum):
    """``DOMAIN`` -- a real, retrievable namespace (leaf). ``DOMAIN_GROUP``
    -- a synthesized parent/prefix grouping; awareness/navigation only,
    never retrievable (R0075 §6/§9)."""

    DOMAIN = "domain"
    DOMAIN_GROUP = "domain_group"


class RetrievalOutcome(StrEnum):
    """The result of ONE :meth:`~nexa.core.context.retrieval.ContextRetriever.retrieve`
    call. ``OK`` and ``NO_MATCH`` are both SUCCESS states -- the only
    difference is whether anything matched; neither is an error."""

    OK = "ok"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"


class ContextItemPriority(StrEnum):
    MANDATORY = "mandatory"
    OPTIONAL = "optional"


class ConflictType(StrEnum):
    POTENTIAL_CONFLICT = "potential_conflict"


#: Small, closed, machine-oriented vocabulary. NEVER raw exception text
#: anywhere a RetrievalReasonCode is stored (R0075 §13).
RetrievalReasonCode = Literal["source_unreachable", "permission_missing", "retrieval_error"]


@dataclass(frozen=True, slots=True)
class KnowledgeDescriptor:
    """A locator/summary -- NOT the knowledge itself (R0075 §5). ``id`` is
    deterministic across process restarts: ``f"{source_kind}:{kind.value}:{domain}"``
    -- never Python's ``hash()``, and collision-proof between a real
    ``DOMAIN`` and a synthesized ``DOMAIN_GROUP`` sharing the same string
    (R0075 §4/§8)."""

    id: str
    kind: KnowledgeDescriptorKind
    source_kind: str
    domain: str
    summary: str
    availability: KnowledgeAvailability
    freshness: datetime | None
    cloud_eligibility: CloudEligibility
    child_domains: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ContextItem:
    """A projection/reference for THIS turn's context -- never a second
    copy of a MemoryRecord's full shape. No ``payload``, no ``provenance``,
    no ``confidence``, no ``supersedes_id``, no evidence (R0075 §6/§20)."""

    source_kind: str
    source_id: str
    domain: str
    record_type: str
    scope_type: str | None
    scope_id: str | None
    content: str
    category: str | None
    cloud_eligibility: CloudEligibility
    priority: ContextItemPriority
    reason_selected: str
    freshness: datetime | None


@dataclass(frozen=True, slots=True)
class ContextConflict:
    """A CONSERVATIVE signal, never an asserted contradiction (R0075 §17/§19)."""

    id: str
    type: ConflictType
    item_source_ids: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    id: str
    query_hint: str
    state: KnowledgeGapState
    reason_code: RetrievalReasonCode | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Typed retrieval outcome -- ``outcome=OK`` is impossible with empty
    ``items``, and every other outcome is impossible WITH items, enforced
    at construction (R0075 §1/§7 of final review), not just documented."""

    descriptor_id: str
    outcome: RetrievalOutcome
    items: tuple[ContextItem, ...] = field(default_factory=tuple)
    reason_code: RetrievalReasonCode | None = None
    has_more: bool = False

    def __post_init__(self) -> None:
        has_items = bool(self.items)
        if (self.outcome is RetrievalOutcome.OK) != has_items:
            raise ValueError(
                f"RetrievalResult invariant violated: outcome={self.outcome!r} "
                f"but items {'is empty' if not has_items else 'is non-empty'} "
                f"-- outcome=OK iff items is non-empty"
            )
        #: only UNAVAILABLE/PERMISSION_REQUIRED are attributable failures;
        #: OK and NO_MATCH are both success outcomes and carry no reason_code
        failure_outcomes = (RetrievalOutcome.UNAVAILABLE, RetrievalOutcome.PERMISSION_REQUIRED)
        if self.outcome in failure_outcomes and self.reason_code is None:
            raise ValueError(
                f"RetrievalResult with outcome={self.outcome!r} must carry a reason_code"
            )
        if self.outcome not in failure_outcomes and self.reason_code is not None:
            raise ValueError(
                f"RetrievalResult with outcome={self.outcome!r} must not carry a reason_code"
            )


@dataclass(frozen=True, slots=True)
class RetrievalAttempt:
    source_kind: str
    descriptor_id: str
    outcome: RetrievalOutcome
    item_count: int
    reason_code: RetrievalReasonCode | None = None


@dataclass(frozen=True, slots=True)
class ContextBuildTrace:
    """IDs, counts, and closed-vocabulary reason codes ONLY -- never raw
    content, ``payload``, ``source_ref``, file paths, or raw exception text
    (R0075 §22/§24)."""

    request_summary: str
    candidate_descriptor_ids: tuple[str, ...] = field(default_factory=tuple)
    selected_item_ids: tuple[str, ...] = field(default_factory=tuple)
    rejected_item_ids: tuple[str, ...] = field(default_factory=tuple)
    rejection_reason_codes: dict[str, str] = field(default_factory=dict)
    retrieval_attempts: tuple[RetrievalAttempt, ...] = field(default_factory=tuple)
    budget_used: dict[str, int] = field(default_factory=dict)
    conflict_ids: tuple[str, ...] = field(default_factory=tuple)
    gap_ids: tuple[str, ...] = field(default_factory=tuple)
    omitted_descriptor_count: int = 0


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_items: int = 20
    max_content_chars: int = 4000
    max_items_per_source: int = 10
    max_retrieval_rounds: int = 2
    max_knowledge_references: int = 10
    max_conversation_turns: int = 20
    max_conversation_chars: int = 8000
    #: Defensive bound on the CURRENT turn only (R0075 §0B). Reuses the
    #: one existing canonical size bound in this codebase for a comparable
    #: concern -- nexa.conversation.context.DEFAULT_MAX_CHARS (the whole
    #: local-provider conversation-context budget, 20_000 chars) -- rather
    #: than inventing a new arbitrary number. A single turn allowed to be
    #: as large as the entire historical-context budget is intentionally
    #: generous, not tight.
    max_current_turn_chars: int = 20_000


@dataclass(frozen=True, slots=True)
class ContextRequest:
    session: ConversationSession
    temporal_intent: TemporalIntent = TemporalIntent.CURRENT
    historical_at: datetime | None = None
    domain_hint: str | None = None
    subject_hints: tuple[str, ...] = field(default_factory=tuple)
    budget: ContextBudget | None = None


@dataclass(frozen=True, slots=True)
class CurrentTurnContext:
    identity: NeXaIdentity
    current_turn: ConversationTurn
    conversation_window: tuple[ConversationTurn, ...]
    selected_context_items: tuple[ContextItem, ...]
    knowledge_references: tuple[KnowledgeDescriptor, ...]
    conflicts: tuple[ContextConflict, ...]
    knowledge_gaps: tuple[KnowledgeGap, ...]
    trace: ContextBuildTrace
