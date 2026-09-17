"""NeXa Memory Platform data model (R0073 Revision 3 + final review
corrections). One canonical local memory authority for every future NeXa
subsystem (LiFeOS, NeXa Teacher, Projects, Goals, Routines, Health,
Finance, Device/home/robot state, ...) — domain systems own semantics
within their own ``namespace``; NeXa Memory owns canonical persistence,
provenance, privacy, lifecycle, and retrieval infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from ..privacy import CloudEligibility


class MemoryCategory(StrEnum):
    """Small, stable, cross-domain SHAPE of information — never a
    domain-specific label (that's ``record_type``) and never determined by
    provenance/source (R0073 final review §3: the same real-world
    occurrence must classify the same way regardless of who/what reported
    it).

    ``FACT`` — a categorical/assertional claim about an entity/world state.
      A new value may supersede the current one; the old record can remain
      historically true during its own validity interval (R0073 §2).
      Example: "current employer = Company A".
    ``STATE`` — a changing measurable/status value where successive values
      naturally form a time series; an old value is never "false," just no
      longer current. Examples: skill mastery %, battery %, project status,
      account balance.
    ``PREFERENCE`` — the user's own stated or inferred inclination/choice.
      Kept distinct from FACT/STATE for product reasons (personalization,
      consent granularity), even though structurally similar to a FACT.
    ``EPISODE`` — a meaningful, narrative, typically singular remembered
      occurrence/experience. Category never depends on provenance —
      "moved to a new apartment" is an EPISODE whether the user said it or
      NeXa inferred it from context.
    ``EVENT`` — a discrete occurrence/log/transaction/action record,
      typically one of many similar entries over time. Also
      provenance-independent — a lesson completion, a purchase, a sensor
      reading, an appointment occurrence.
    """

    FACT = "fact"
    STATE = "state"
    PREFERENCE = "preference"
    EPISODE = "episode"
    EVENT = "event"


class MemoryProvenance(StrEnum):
    """HOW a piece of evidence was obtained — the "source shape," entirely
    separate from ``MemoryCategory`` (the "information shape"). Lives on
    :class:`MemoryEvidence`, never on :class:`MemoryRecord`."""

    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    CONVERSATION_DERIVED = "conversation_derived"
    USER_CORRECTION = "user_correction"
    SYSTEM_OBSERVATION = "system_observation"
    IMPORTED = "imported"
    LEARNED_INFERENCE = "learned_inference"


class MemoryStatus(StrEnum):
    """Lifecycle status of a :class:`MemoryRecord` — distinct from temporal
    truth (R0073 final review §1).

    ``ACTIVE`` — accepted current knowledge.
    ``SUPERSEDED`` — no longer current, but may have been historically true
      during its own ``[valid_from, valid_until)`` interval; the default,
      normal outcome of a changed/stale fact or preference.
    ``RETRACTED`` — explicitly withdrawn / invalidated knowledge (e.g. the
      user said it was simply wrong), retained only for audit/history-of-
      memory, never surfaced as historical truth by default.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class RelationStatus(StrEnum):
    """Lifecycle status of a :class:`MemoryRelation` — independent of, and
    simpler than, ``MemoryStatus`` (a relation is either in effect or
    soft-removed, no supersession/retraction distinction)."""

    ACTIVE = "active"
    DELETED = "deleted"


class MemoryWriteTrigger(StrEnum):
    """Why a :meth:`~nexa.core.memory.service.MemoryService.remember` call
    is happening — see ``nexa.core.memory.service`` for the write-policy
    rules attached to each value. There is no ``TEMPORARY_CONVERSATIONAL``
    or ``SENSITIVE_LOCAL_ONLY`` trigger: temporary content is simply never
    passed to ``remember()``, and "sensitive" is expressed via
    ``cloud_eligibility=LOCAL_ONLY`` on every write regardless of trigger.
    """

    EXPLICIT_REMEMBER_REQUEST = "explicit_remember_request"
    CORRECTION = "correction"
    DURABLE_CANDIDATE = "durable_candidate"


class HardDeleteReasonCode(StrEnum):
    """A short, machine-oriented, non-sensitive reason category for a
    :meth:`~nexa.core.memory.service.MemoryService.hard_delete` call — the
    ONLY thing ever logged alongside a hard-delete (R0073 final review
    §4). The vocabulary is intentionally small and extensible: any
    lowercase ``snake_case`` token is accepted (see
    ``nexa.core.memory.validation``), these four are simply the documented
    starting set."""

    USER_REQUESTED = "user_requested"
    PRIVACY_ERASURE = "privacy_erasure"
    INCORRECT_SENSITIVE_DATA = "incorrect_sensitive_data"
    ADMINISTRATIVE_CLEANUP = "administrative_cleanup"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """What NeXa currently stores/knows. Carries NO provenance/source/
    confidence of its own — those live on :class:`MemoryEvidence`
    (one-to-many; a record-level provenance column would be ambiguous once
    more than one source supports it — R0073 final review §2/§3)."""

    id: str
    namespace: str
    category: MemoryCategory
    record_type: str
    payload_version: int
    scope_type: str | None
    scope_id: str | None
    content: str
    payload: dict | None
    valid_from: datetime | None
    valid_until: datetime | None
    created_at: datetime
    updated_at: datetime
    cloud_eligibility: CloudEligibility
    status: MemoryStatus
    supersedes_id: str | None


@dataclass(frozen=True, slots=True)
class MemoryEvidence:
    """Why / from where NeXa knows or believes a :class:`MemoryRecord`.
    Multiple independent evidence rows may support the same record — a
    dedup hit on ``remember()`` ADDS evidence, it never discards it."""

    id: str
    memory_id: str
    provenance: MemoryProvenance
    source_kind: str | None
    source_ref: str | None
    confidence: float | None
    observed_at: datetime | None
    created_at: datetime
    payload: dict | None


@dataclass(frozen=True, slots=True)
class MemoryRelation:
    """A typed edge between two existing :class:`MemoryRecord` rows — a
    closed graph over memory records, not a general entity/graph database
    (R0073 §13: a scoped, revisable foundation choice)."""

    id: str
    source_id: str
    relation_type: str
    target_id: str
    created_at: datetime
    status: RelationStatus


@dataclass(frozen=True, slots=True)
class RememberResult:
    memory_id: str
    evidence_id: str
    was_new_memory: bool


@dataclass(frozen=True, slots=True)
class NamespaceSummary:
    """Cheap, bounded, ACTIVE-only knowledge-awareness metadata for one
    namespace — never the underlying record content (R0075). Produced by
    :meth:`~nexa.core.memory.repository.MemoryRepository.namespace_summary`,
    one bounded SQL aggregate, no N+1, no ``content``/``payload_json``
    crossing into Python. ``cloud_eligibility`` is already collapsed via
    :func:`~nexa.core.privacy.most_restrictive_cloud_eligibility`."""

    namespace: str
    record_count: int
    freshest: datetime
    cloud_eligibility: CloudEligibility


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
