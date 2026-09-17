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
    KnowledgeDescriptor,
    RetrievalResult,
    TemporalIntent,
)


class RetrievalBudget(Protocol):
    """Structural contract for the budget fields retrieval/selection code
    actually reads (R0079 / R0078 Revision 2 §5) -- both
    :class:`~nexa.core.context.models.ContextBudget` (turn-based, carries
    additional conversation-window-only fields retrieval never touches)
    and :class:`~nexa.core.context.models.RecallBudget` (query-based, no
    conversation concept at all) satisfy this via plain structural typing.
    No inheritance relationship between the two -- this Protocol is the
    explicit shared contract, so retrieval code never relies on accidental
    duck typing."""

    max_items: int
    max_content_chars: int
    max_items_per_source: int
    max_retrieval_rounds: int
    max_knowledge_references: int


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
    jobs behind one signature (R0075 §7): with no hints given it is
    cheap, reliable DOMAIN discovery (enumerate known domains); with
    hints present it attempts TOPIC discovery (best-effort matching --
    V1's is a plain substring match, deliberately weak, not semantic
    search). A future richer retriever can replace the topic-matching
    internals without this method's signature, return type, or its
    caller (``ContextEngine``) ever changing.

    R0079 (R0078 Revision 2 §2): narrowed from taking a whole
    ``ContextRequest`` to the two fields any retriever has ever actually
    read from one (``domain_hint``, ``subject_hints``) -- audited against
    the real ``MemoryRetriever`` implementation, which never touched
    anything else on the request, including ``session``. This is what
    lets :meth:`~nexa.core.context.engine.ContextEngine.recall` share this
    exact discovery step with :meth:`~nexa.core.context.engine.ContextEngine.build_context`
    even though ``recall()`` has no ``ConversationSession`` at all.
    """

    source_kind: str

    def describe_available_knowledge(
        self, *, domain_hint: str | None, subject_hints: tuple[str, ...]
    ) -> tuple[KnowledgeDescriptor, ...]: ...

    def retrieve(self, query: RetrievalQuery, budget: RetrievalBudget) -> RetrievalResult:
        """Targeted, bounded fetch for ONE descriptor. A retriever catches
        its own expected failure modes and returns
        ``UNAVAILABLE``/``PERMISSION_REQUIRED`` with a closed-vocabulary
        ``reason_code`` -- an unexpected exception propagates normally
        (fail loud), it is never silently swallowed into a trace string."""
        ...
