"""The NeXa Memory Platform (R0073) — one canonical local memory authority
for every current and future NeXa subsystem (LiFeOS, NeXa Teacher,
Projects, Goals, Routines, Health, Finance, Device/home/robot state, ...).
Domain systems own semantics within their own ``namespace``; NeXa Memory
owns canonical persistence, provenance, privacy, lifecycle, and retrieval
infrastructure. Consume :class:`MemoryService` — ``MemoryRepository`` is an
internal implementation detail.

No cloud provider, no local/cloud LLM, ever owns memory (a model may only
propose a ``DURABLE_CANDIDATE`` write). No automatic conversation-sentence-
to-memory pipeline exists. No vector/embedding search. No dependency on
``nexa.realtime`` anywhere in this package (ADR-0005; ``CloudEligibility``
comes from ``nexa.core.privacy``).
"""

from __future__ import annotations

from .errors import MemoryError, MemoryNotFoundError, MemoryValidationError
from .models import (
    HardDeleteReasonCode,
    MemoryCategory,
    MemoryEvidence,
    MemoryProvenance,
    MemoryRecord,
    MemoryRelation,
    MemoryStatus,
    MemoryWriteTrigger,
    NamespaceSummary,
    Page,
    RelationStatus,
    RememberResult,
)
from .repository import MemoryRepository
from .service import MemoryService

__all__ = [
    "MemoryService",
    "MemoryRepository",
    "MemoryRecord",
    "MemoryEvidence",
    "MemoryRelation",
    "RememberResult",
    "Page",
    "MemoryCategory",
    "MemoryProvenance",
    "MemoryStatus",
    "RelationStatus",
    "MemoryWriteTrigger",
    "HardDeleteReasonCode",
    "NamespaceSummary",
    "MemoryError",
    "MemoryValidationError",
    "MemoryNotFoundError",
]
