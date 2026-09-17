"""NeXa Context Engine + Knowledge Awareness (R0075) — selection /
composition / retrieval orchestration ONLY. Never a canonical memory
authority (that's :mod:`nexa.core.memory`), never a conversation authority
(that's ``nexa.conversation.ConversationSession``), never mutates Identity.

Zero dependency on ``nexa.realtime``, any provider SDK, or Gemini —
provider-specific projection (e.g. building a ``CloudContextSnapshot``)
lives outside Core, in ``nexa.realtime.context_projection``.
"""

from __future__ import annotations

from .derivation import derive_context_request, derive_subject_hints
from .engine import ContextBuildError, ContextEngine
from .memory_retriever import MemoryRetriever
from .models import (
    ConflictType,
    ContextBudget,
    ContextBuildTrace,
    ContextConflict,
    ContextItem,
    ContextItemPriority,
    ContextRequest,
    CurrentTurnContext,
    KnowledgeAvailability,
    KnowledgeDescriptor,
    KnowledgeDescriptorKind,
    KnowledgeGap,
    KnowledgeGapState,
    RetrievalAttempt,
    RetrievalOutcome,
    RetrievalReasonCode,
    RetrievalResult,
    TemporalIntent,
)
from .retrieval import ContextRetriever, RetrievalNotSupportedError, RetrievalQuery

__all__ = [
    "ContextEngine",
    "ContextBuildError",
    "derive_context_request",
    "derive_subject_hints",
    "ContextRetriever",
    "RetrievalNotSupportedError",
    "RetrievalQuery",
    "MemoryRetriever",
    "TemporalIntent",
    "KnowledgeAvailability",
    "KnowledgeGapState",
    "KnowledgeDescriptorKind",
    "RetrievalOutcome",
    "RetrievalReasonCode",
    "ContextItemPriority",
    "ConflictType",
    "KnowledgeDescriptor",
    "ContextItem",
    "ContextConflict",
    "KnowledgeGap",
    "RetrievalResult",
    "RetrievalAttempt",
    "ContextBuildTrace",
    "ContextBudget",
    "ContextRequest",
    "CurrentTurnContext",
]
