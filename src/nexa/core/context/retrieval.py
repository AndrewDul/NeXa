"""``ContextRetriever`` — the one extension point for a Knowledge Source
(R0075 §6/§13). V1 has exactly one concrete implementation
(:class:`~nexa.core.context.memory_retriever.MemoryRetriever`) — no
plugin framework, no ``TeacherRetriever``/``LiFeOSRetriever``/
``FileRetriever`` classes yet, but the contract stays source-neutral for
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import (
    ContextBudget,
    ContextRequest,
    KnowledgeDescriptor,
    RetrievalResult,
    TemporalIntent,
)


class RetrievalNotSupportedError(RuntimeError):
    """Raised if a ``DOMAIN_GROUP`` descriptor ever reaches the retrieval
    step (R0075 §6). Should be structurally unreachable -- the pipeline
    only ever forwards ``DOMAIN``-kind descriptors to retrieval; this is
    defense-in-depth, not the primary mechanism."""


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    descriptor_id: str
    domain: str
    record_type_hint: str | None = None
    temporal_intent: TemporalIntent = TemporalIntent.CURRENT
    at: datetime | None = None
    limit: int = 10


class ContextRetriever(Protocol):
    """Two methods only -- not a plugin framework.

    ``describe_available_knowledge`` serves TWO conceptually distinct
    jobs behind one signature (R0075 §7): with no hints in ``request`` it
    is cheap, reliable DOMAIN discovery (enumerate known domains); with
    hints present it attempts TOPIC discovery (best-effort matching --
    V1's is a plain substring match, deliberately weak, not semantic
    search). A future richer retriever can replace the topic-matching
    internals without this method's signature, return type, or its
    caller (``ContextEngine``) ever changing.
    """

    source_kind: str

    def describe_available_knowledge(
        self, request: ContextRequest
    ) -> tuple[KnowledgeDescriptor, ...]: ...

    def retrieve(self, query: RetrievalQuery, budget: ContextBudget) -> RetrievalResult:
        """Targeted, bounded fetch for ONE descriptor. A retriever catches
        its own expected failure modes and returns
        ``UNAVAILABLE``/``PERMISSION_REQUIRED`` with a closed-vocabulary
        ``reason_code`` -- an unexpected exception propagates normally
        (fail loud), it is never silently swallowed into a trace string."""
        ...
