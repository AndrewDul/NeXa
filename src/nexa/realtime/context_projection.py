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

import logging
import time
from collections.abc import Callable

from ..conversation.session import ConversationSession
from ..conversation.turn import Role
from ..core.context.derivation import derive_context_request
from ..core.context.engine import ContextEngine
from ..core.context.models import ContextBudget, CurrentTurnContext
from .snapshot import (
    DEFAULT_SNAPSHOT_CHAR_BUDGET,
    DEFAULT_SNAPSHOT_TURNS,
    CloudContextSnapshot,
    build_cloud_context_snapshot,
)

logger = logging.getLogger(__name__)


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


def make_cloud_snapshot_builder(
    engine: ContextEngine, *, budget: ContextBudget | None = None
) -> Callable[..., CloudContextSnapshot]:
    """Builds a ``snapshot_builder`` callable matching
    ``ConversationRouter``'s exact constructor seam (R0077 §9):
    ``snapshot_builder(session, *, language_preference=None, policy_name="",
    active_provider_name="") -> CloudContextSnapshot``.

    Audited finding (R0077): the accepted, frozen simplified cloud voice
    path (``nexa.realtime.gemini.simple_conversation``) builds its ONE
    ``CloudContextSnapshot`` at cold start, directly, BEFORE any
    conversation turn exists — ``ConversationRouter.start_cloud()`` (the
    seam this builder matches) is never actually called there. This
    function is therefore proven correct and ready (see
    ``tests/test_realtime_context_projection.py``), but NOT wired into
    ``apps/nexa_cloud_voice_simple.py`` this checkpoint, since doing so
    would have zero observable effect on that entrypoint's real flow and
    risks nothing while providing nothing — a genuine architectural
    finding, not a shortcut. It IS a correct drop-in for
    ``ConversationRouter(session, snapshot_builder=...)`` for any FUTURE
    flow (this one or a new one) that calls the seam after a real turn
    exists.

    Gracefully falls back to the plain, unmodified ``build_cloud_context_snapshot()``
    (byte-identical to no Context Engine wiring at all) whenever there is
    no current user turn to build a request from, or if context-building
    itself fails for any reason — never blocks cloud session start on a
    Memory/Context Engine problem."""

    def _build(
        session: ConversationSession,
        *,
        language_preference: str | None = None,
        policy_name: str = "",
        active_provider_name: str = "",
    ) -> CloudContextSnapshot:
        has_current_turn = bool(session.history) and session.history[-1].role is Role.USER
        if has_current_turn:
            start = time.monotonic()
            try:
                request = derive_context_request(session, budget=budget)
                current_turn_context = engine.build_context(request)
            except Exception:
                logger.warning(
                    "nexa.realtime.context_projection: cloud context build failed -- "
                    "falling back to the plain snapshot (fail-safe by design, R0077)",
                    exc_info=True,
                )
            else:
                logger.debug(
                    "nexa.realtime.context_projection: cloud context build took %.1fms "
                    "(discovery=%.1fms retrieval=%.1fms) items=%d",
                    (time.monotonic() - start) * 1000,
                    current_turn_context.trace.discovery_ms,
                    current_turn_context.trace.retrieval_ms,
                    len(current_turn_context.selected_context_items),
                )
                return to_cloud_snapshot(
                    current_turn_context,
                    session=session,
                    language_preference=language_preference,
                    policy_name=policy_name,
                    active_provider_name=active_provider_name,
                )
        return build_cloud_context_snapshot(
            session,
            language_preference=language_preference,
            policy_name=policy_name,
            active_provider_name=active_provider_name,
        )

    return _build
