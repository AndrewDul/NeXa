"""Provider-facing adapter: :class:`~nexa.core.context.models.CurrentTurnContext`
(``nexa.core.context``) -> :class:`~nexa.realtime.snapshot.CloudContextSnapshot`
(R0075 Revision 3 §9). Lives under ``nexa.realtime``, never ``nexa.core`` —
Core must not depend on the realtime/provider layer (ADR-0005; the M3.2
``nexa.core -> never nexa.realtime`` invariant, still enforced by
``tests/test_core_privacy.py::TestDependencyDirection`` and the
``nexa.core.context`` dependency-direction tests).

Reuses ``build_cloud_context_snapshot()``/``filter_cloud_safe()`` UNCHANGED
— this module does not reimplement privacy filtering, it only becomes a
real producer of ``context_facts`` for the already-existing, already-tested
machinery.
"""

from __future__ import annotations

from ..conversation.session import ConversationSession
from ..core.context.models import CurrentTurnContext
from .snapshot import (
    DEFAULT_SNAPSHOT_CHAR_BUDGET,
    DEFAULT_SNAPSHOT_TURNS,
    CloudContextSnapshot,
    build_cloud_context_snapshot,
)


def to_cloud_snapshot(
    context: CurrentTurnContext,
    *,
    session: ConversationSession,
    language_preference: str | None = None,
    policy_name: str = "",
    active_provider_name: str = "",
    max_turns: int = DEFAULT_SNAPSHOT_TURNS,
    max_chars: int = DEFAULT_SNAPSHOT_CHAR_BUDGET,
) -> CloudContextSnapshot:
    """Projects ``context.selected_context_items`` into
    ``build_cloud_context_snapshot()``'s ``context_facts`` — each item's
    ``content`` tagged with its own ``cloud_eligibility``, exactly as that
    function already expects. Only ``CLOUD_SAFE``-tagged items ever cross
    (enforced by the existing, unmodified ``filter_cloud_safe()``, not by
    anything new here). ``context.knowledge_references`` (descriptors) are
    NOT sent to the provider in V1 — even the existence of a domain can be
    sensitive (R0075 §14/§21); only concretely selected, already-privacy-
    tagged content ever leaves Core.
    """
    context_facts = tuple(
        (item.content, item.cloud_eligibility) for item in context.selected_context_items
    )
    return build_cloud_context_snapshot(
        session,
        language_preference=language_preference,
        policy_name=policy_name,
        active_provider_name=active_provider_name,
        max_turns=max_turns,
        max_chars=max_chars,
        context_facts=context_facts,
    )
