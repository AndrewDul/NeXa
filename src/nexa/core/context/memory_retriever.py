"""``MemoryRetriever`` — the one V1 concrete :class:`~nexa.core.context.retrieval.ContextRetriever`
(R0075 §9). Wraps :class:`~nexa.core.memory.service.MemoryService` — never
writes, never applies a ``cloud_eligibility`` filter at retrieval time
(the full local context may legitimately contain ``LOCAL_ONLY`` items;
filtering happens only at provider-projection time, outside Core).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..memory.service import MemoryService
from ..privacy import most_restrictive_cloud_eligibility
from .models import (
    ContextItem,
    ContextItemPriority,
    KnowledgeAvailability,
    KnowledgeDescriptor,
    KnowledgeDescriptorKind,
    RetrievalOutcome,
    RetrievalResult,
    TemporalIntent,
)
from .retrieval import RetrievalBudget, RetrievalQuery

#: Bounded scan width for hint-based discovery -- deliberately wider than
#: ContextBudget.max_knowledge_references, because matching must search
#: over more candidate namespaces than it ultimately returns (R0075 §7).
#: The no-hint "domain enumeration" case doesn't need this -- it scans
#: exactly max_knowledge_references, the top-N most recent namespaces.
_DISCOVERY_SCAN_LIMIT = 200

_MIN_DATETIME = datetime.min.replace(tzinfo=UTC)


def _domain_descriptor_id(source_kind: str, domain: str) -> str:
    return f"{source_kind}:{KnowledgeDescriptorKind.DOMAIN.value}:{domain}"


def _domain_group_descriptor_id(source_kind: str, prefix: str) -> str:
    return f"{source_kind}:{KnowledgeDescriptorKind.DOMAIN_GROUP.value}:{prefix}"


def _top_level_prefix(namespace: str) -> str | None:
    if "." not in namespace:
        return None
    return namespace.split(".", 1)[0]


def _matches(
    descriptor: KnowledgeDescriptor,
    domain_hint: str | None,
    subject_hints: tuple[str, ...],
) -> bool:
    if domain_hint is not None and descriptor.domain == domain_hint:
        return True
    haystack = f"{descriptor.domain} {descriptor.summary}".lower()
    return any(hint.lower() in haystack for hint in subject_hints)


class MemoryRetriever:
    """``MemoryRetriever`` queries a local, always-available SQLite
    database — it never returns ``UNAVAILABLE``/``PERMISSION_REQUIRED`` in
    V1 (those states exist for future non-local retrievers). An actual
    database-level failure is an unexpected error and propagates normally
    (fail loud), never silently converted into a knowledge-gap state."""

    source_kind = "memory"

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory_service = memory_service

    def _leaf_descriptor(self, summary) -> KnowledgeDescriptor:  # summary: NamespaceSummary
        return KnowledgeDescriptor(
            id=_domain_descriptor_id(self.source_kind, summary.namespace),
            kind=KnowledgeDescriptorKind.DOMAIN,
            source_kind=self.source_kind,
            domain=summary.namespace,
            summary=(
                f"{summary.record_count} record(s) under '{summary.namespace}', "
                f"most recent {summary.freshest.date().isoformat()}"
            ),
            availability=KnowledgeAvailability.AVAILABLE,
            freshness=summary.freshest,
            cloud_eligibility=summary.cloud_eligibility,
        )

    def _group_descriptors(
        self, leaves: tuple[KnowledgeDescriptor, ...]
    ) -> tuple[KnowledgeDescriptor, ...]:
        children_by_prefix: dict[str, list[KnowledgeDescriptor]] = {}
        for leaf in leaves:
            prefix = _top_level_prefix(leaf.domain)
            if prefix is None:
                continue
            children_by_prefix.setdefault(prefix, []).append(leaf)

        groups = []
        for prefix, children in children_by_prefix.items():
            child_domains = tuple(sorted(c.domain for c in children))
            groups.append(
                KnowledgeDescriptor(
                    id=_domain_group_descriptor_id(self.source_kind, prefix),
                    kind=KnowledgeDescriptorKind.DOMAIN_GROUP,
                    source_kind=self.source_kind,
                    domain=prefix,
                    summary=(
                        f"'{prefix}' has {len(child_domains)} known sub-domain(s): "
                        f"{', '.join(child_domains)}"
                    ),
                    availability=KnowledgeAvailability.AVAILABLE,
                    freshness=max(c.freshness for c in children if c.freshness is not None),
                    cloud_eligibility=most_restrictive_cloud_eligibility(
                        c.cloud_eligibility for c in children
                    ),
                    child_domains=child_domains,
                )
            )
        return tuple(groups)

    def describe_available_knowledge(
        self, *, domain_hint: str | None, subject_hints: tuple[str, ...]
    ) -> tuple[KnowledgeDescriptor, ...]:
        """Scans up to ``_DISCOVERY_SCAN_LIMIT`` namespaces (bounded, not
        unbounded) and returns every candidate it finds -- NOT capped to
        ``budget.max_knowledge_references`` here. Final surfacing/omission
        accounting is the ENGINE's job (R0075 implementation correction):
        if this retriever self-capped, the engine could never observe "more
        candidates existed than fit," making ``omitted_descriptor_count``
        unobservable whenever there is exactly one retriever.

        R0079 (R0078 Revision 2 §2): narrowed to the two fields this method
        ever read from ``ContextRequest`` -- it never touched ``session``,
        so it works identically whether the caller is
        ``ContextEngine.build_context()`` (a real current turn) or
        ``ContextEngine.recall()`` (no session at all)."""
        has_hints = bool(domain_hint or subject_hints)

        summaries = self._memory_service.namespace_summary(limit=_DISCOVERY_SCAN_LIMIT)
        leaves = tuple(self._leaf_descriptor(s) for s in summaries)
        groups = self._group_descriptors(leaves)
        candidates = leaves + groups

        if has_hints:
            candidates = tuple(
                d for d in candidates if _matches(d, domain_hint, subject_hints)
            )

        return tuple(sorted(candidates, key=lambda d: d.freshness or _MIN_DATETIME, reverse=True))

    def retrieve(self, query: RetrievalQuery, budget: RetrievalBudget) -> RetrievalResult:
        limit = min(query.limit, budget.max_items_per_source)
        if query.temporal_intent is TemporalIntent.HISTORICAL:
            if query.at is None:
                raise ValueError("HISTORICAL retrieval requires RetrievalQuery.at")
            page = self._memory_service.valid_at(
                query.at, namespace=query.domain, record_type=query.record_type_hint, limit=limit
            )
        else:
            page = self._memory_service.by_namespace(
                query.domain, record_type=query.record_type_hint, limit=limit
            )

        items = tuple(_to_context_item(record) for record in page.items)
        if items:
            return RetrievalResult(
                descriptor_id=query.descriptor_id, outcome=RetrievalOutcome.OK, items=items
            )
        return RetrievalResult(descriptor_id=query.descriptor_id, outcome=RetrievalOutcome.NO_MATCH)


def _to_context_item(record) -> ContextItem:  # record: MemoryRecord
    return ContextItem(
        source_kind="memory",
        source_id=record.id,
        domain=record.namespace,
        record_type=record.record_type,
        scope_type=record.scope_type,
        scope_id=record.scope_id,
        content=record.content,
        category=record.category.value,
        cloud_eligibility=record.cloud_eligibility,
        priority=ContextItemPriority.OPTIONAL,
        reason_selected=f"namespace_match:{record.namespace}",
        freshness=record.updated_at,
    )
