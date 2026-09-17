"""``ContextEngine`` — selection / composition / retrieval orchestration
ONLY (R0075). Never a canonical memory authority, never mutates Identity,
never writes anything, never becomes provider-specific. No LLM/model call
inside this module, no autonomous retrieval loop — every round is bounded
by :class:`~nexa.core.context.models.ContextBudget`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...conversation.turn import ConversationTurn, Role
from ..identity.model import NeXaIdentity
from .models import (
    ConflictType,
    ContextBudget,
    ContextBuildTrace,
    ContextConflict,
    ContextItem,
    ContextRequest,
    CurrentTurnContext,
    KnowledgeDescriptor,
    KnowledgeDescriptorKind,
    KnowledgeGap,
    KnowledgeGapState,
    RetrievalAttempt,
    RetrievalOutcome,
)
from .retrieval import ContextRetriever, RetrievalNotSupportedError, RetrievalQuery

_MIN_DATETIME = datetime.min.replace(tzinfo=UTC)

#: categories where "more than one ACTIVE value for the same scope" is
#: worth a conservative signal -- STATE/EVENT/EPISODE are deliberately
#: excluded (R0075 final review §11): multiple differing values there are
#: their normal, expected shape (a time series, a log, distinct narrative
#: moments), not a contradiction.
_CONFLICT_ELIGIBLE_CATEGORIES = frozenset({"fact", "preference"})


class ContextBuildError(RuntimeError):
    """``build_context()`` requires ``session.history`` to be non-empty
    with its LAST entry being a USER turn (matching
    ``ConversationSession.send()``'s own confirmed append-then-build
    order), and the current turn's own text must not exceed
    ``ContextBudget.max_current_turn_chars``. Fails loudly rather than
    guessing or silently truncating user input."""


class ContextEngine:
    def __init__(self, identity: NeXaIdentity, retrievers: tuple[ContextRetriever, ...]) -> None:
        self._identity = identity
        self._retrievers = retrievers
        self._retrievers_by_kind = {r.source_kind: r for r in retrievers}

    def build_context(self, request: ContextRequest) -> CurrentTurnContext:
        budget = request.budget or ContextBudget()
        current_turn, conversation_window = self._mandatory_turns(request, budget)

        request_summary = (
            f"domain_hint={request.domain_hint!r} subject_hints={request.subject_hints!r} "
            f"temporal={request.temporal_intent.value}"
        )

        all_descriptors = self._discover(request)
        capped_descriptors = all_descriptors[: budget.max_knowledge_references]
        omitted_descriptor_count = max(0, len(all_descriptors) - len(capped_descriptors))

        has_hints = bool(request.domain_hint or request.subject_hints)
        if not has_hints:
            trace = ContextBuildTrace(
                request_summary=request_summary,
                candidate_descriptor_ids=tuple(d.id for d in all_descriptors),
                budget_used={"items": 0, "content_chars": 0, "rounds": 0},
                omitted_descriptor_count=omitted_descriptor_count,
            )
            return CurrentTurnContext(
                identity=self._identity,
                current_turn=current_turn,
                conversation_window=conversation_window,
                selected_context_items=(),
                knowledge_references=tuple(capped_descriptors),
                conflicts=(),
                knowledge_gaps=(),
                trace=trace,
            )

        knowledge_gaps: list[KnowledgeGap] = []
        if not all_descriptors:
            domain_hints = [request.domain_hint] if request.domain_hint else []
            hints = domain_hints + list(request.subject_hints)
            knowledge_gaps.extend(
                KnowledgeGap(id=f"gap:{i}", query_hint=hint, state=KnowledgeGapState.UNKNOWN)
                for i, hint in enumerate(hints)
            )

        selected_items, retrieval_attempts, retrieval_gaps, rounds = self._retrieve(
            capped_descriptors, request, budget
        )
        knowledge_gaps.extend(retrieval_gaps)

        selected_items.sort(key=lambda i: (i.freshness or _MIN_DATETIME, i.source_id), reverse=True)
        trimmed, rejected = _trim_to_budget(selected_items, budget)
        conflicts = _detect_conflicts(trimmed)

        trace = ContextBuildTrace(
            request_summary=request_summary,
            candidate_descriptor_ids=tuple(d.id for d in all_descriptors),
            selected_item_ids=tuple(i.source_id for i in trimmed),
            rejected_item_ids=tuple(i.source_id for i in rejected),
            rejection_reason_codes={i.source_id: "budget_exceeded" for i in rejected},
            retrieval_attempts=tuple(retrieval_attempts),
            budget_used={
                "items": len(trimmed),
                "content_chars": sum(len(i.content) for i in trimmed),
                "rounds": rounds,
            },
            conflict_ids=tuple(c.id for c in conflicts),
            gap_ids=tuple(g.id for g in knowledge_gaps),
            omitted_descriptor_count=omitted_descriptor_count,
        )

        return CurrentTurnContext(
            identity=self._identity,
            current_turn=current_turn,
            conversation_window=conversation_window,
            selected_context_items=tuple(trimmed),
            knowledge_references=tuple(capped_descriptors),
            conflicts=tuple(conflicts),
            knowledge_gaps=tuple(knowledge_gaps),
            trace=trace,
        )

    # ---- pipeline steps ----

    def _mandatory_turns(
        self, request: ContextRequest, budget: ContextBudget
    ) -> tuple[ConversationTurn, tuple[ConversationTurn, ...]]:
        history = request.session.history
        if not history or history[-1].role is not Role.USER:
            raise ContextBuildError(
                "build_context() requires session.history to be non-empty with its "
                "last entry being a USER turn (reason_code=missing_current_turn)"
            )
        current_turn = history[-1]
        if len(current_turn.content) > budget.max_current_turn_chars:
            raise ContextBuildError(
                f"current turn is {len(current_turn.content)} chars, exceeds "
                f"max_current_turn_chars={budget.max_current_turn_chars} "
                f"(reason_code=current_turn_too_large) -- never silently truncated"
            )
        conversation_window = _bounded_prior_turns(
            history[:-1], budget.max_conversation_turns, budget.max_conversation_chars
        )
        return current_turn, conversation_window

    def _discover(self, request: ContextRequest) -> list[KnowledgeDescriptor]:
        descriptors: list[KnowledgeDescriptor] = []
        for retriever in self._retrievers:
            descriptors.extend(retriever.describe_available_knowledge(request))
        descriptors.sort(key=lambda d: (d.freshness or _MIN_DATETIME, d.id), reverse=True)
        return descriptors

    def _retrieve(
        self,
        descriptors: list[KnowledgeDescriptor],
        request: ContextRequest,
        budget: ContextBudget,
    ) -> tuple[list[ContextItem], list[RetrievalAttempt], list[KnowledgeGap], int]:
        selected_items: list[ContextItem] = []
        attempts: list[RetrievalAttempt] = []
        gaps: list[KnowledgeGap] = []
        rounds = 0

        retrievable = [d for d in descriptors if d.kind is KnowledgeDescriptorKind.DOMAIN]
        for descriptor in retrievable:
            if rounds >= budget.max_retrieval_rounds:
                break
            retriever = self._retrievers_by_kind[descriptor.source_kind]
            query = _build_retrieval_query(descriptor, request, budget)
            result = retriever.retrieve(query, budget)
            rounds += 1

            attempts.append(
                RetrievalAttempt(
                    source_kind=descriptor.source_kind,
                    descriptor_id=descriptor.id,
                    outcome=result.outcome,
                    item_count=len(result.items),
                    reason_code=result.reason_code,
                )
            )

            if result.outcome is RetrievalOutcome.OK:
                selected_items.extend(result.items)
            elif result.outcome is RetrievalOutcome.NO_MATCH:
                gaps.append(
                    KnowledgeGap(
                        id=f"gap:{len(gaps)}", query_hint=descriptor.domain,
                        state=KnowledgeGapState.NO_MATCH,
                    )
                )
            elif result.outcome is RetrievalOutcome.UNAVAILABLE:
                gaps.append(
                    KnowledgeGap(
                        id=f"gap:{len(gaps)}", query_hint=descriptor.domain,
                        state=KnowledgeGapState.UNAVAILABLE, reason_code=result.reason_code,
                    )
                )
            elif result.outcome is RetrievalOutcome.PERMISSION_REQUIRED:
                gaps.append(
                    KnowledgeGap(
                        id=f"gap:{len(gaps)}", query_hint=descriptor.domain,
                        state=KnowledgeGapState.PERMISSION_REQUIRED, reason_code=result.reason_code,
                    )
                )

        return selected_items, attempts, gaps, rounds


def _build_retrieval_query(
    descriptor: KnowledgeDescriptor, request: ContextRequest, budget: ContextBudget
) -> RetrievalQuery:
    if descriptor.kind is not KnowledgeDescriptorKind.DOMAIN:
        raise RetrievalNotSupportedError(
            f"descriptor {descriptor.id!r} is {descriptor.kind}, not DOMAIN -- "
            f"group descriptors are awareness-only and are never retrieved"
        )
    return RetrievalQuery(
        descriptor_id=descriptor.id,
        domain=descriptor.domain,
        temporal_intent=request.temporal_intent,
        at=request.historical_at,
        limit=budget.max_items_per_source,
    )


def _bounded_prior_turns(
    prior: tuple[ConversationTurn, ...], max_turns: int, max_chars: int
) -> tuple[ConversationTurn, ...]:
    """Most recent PRIOR turns first, trimmed from the OLDEST end when
    over budget -- never splits a turn (matches the existing
    ``ConversationContext`` convention), and always keeps at least the
    single most recent prior turn even if it alone exceeds the char
    budget."""
    candidates = prior[-max_turns:] if max_turns > 0 else ()
    kept: list[ConversationTurn] = []
    total_chars = 0
    for turn in reversed(candidates):
        total_chars += len(turn.content)
        if kept and total_chars > max_chars:
            break
        kept.append(turn)
    kept.reverse()
    return tuple(kept)


def _trim_to_budget(
    items: list[ContextItem], budget: ContextBudget
) -> tuple[list[ContextItem], list[ContextItem]]:
    """``items`` must already be sorted freshest-first. Drops
    lowest-priority (all OPTIONAL in V1 -- MANDATORY fields are
    structurally outside this pool), oldest-freshness items first once
    over ``max_items``/``max_content_chars``."""
    kept: list[ContextItem] = []
    rejected: list[ContextItem] = []
    total_chars = 0
    for item in items:
        if len(kept) >= budget.max_items:
            rejected.append(item)
            continue
        if kept and total_chars + len(item.content) > budget.max_content_chars:
            rejected.append(item)
            continue
        kept.append(item)
        total_chars += len(item.content)
    return kept, rejected


def _detect_conflicts(items: list[ContextItem]) -> list[ContextConflict]:
    groups: dict[tuple, list[ContextItem]] = {}
    for item in items:
        if item.category not in _CONFLICT_ELIGIBLE_CATEGORIES:
            continue
        key = (item.source_kind, item.domain, item.record_type, item.scope_type, item.scope_id)
        groups.setdefault(key, []).append(item)

    conflicts: list[ContextConflict] = []
    for key, group in groups.items():
        distinct_contents = {i.content for i in group}
        if len(distinct_contents) > 1:
            _, domain, record_type, scope_type, scope_id = key
            conflicts.append(
                ContextConflict(
                    id=f"conflict:{len(conflicts)}",
                    type=ConflictType.POTENTIAL_CONFLICT,
                    item_source_ids=tuple(i.source_id for i in group),
                    description=(
                        f"{len(group)} differing {record_type!r} records for "
                        f"(domain={domain!r}, scope=({scope_type!r}, {scope_id!r}))"
                    ),
                )
            )
    return conflicts
