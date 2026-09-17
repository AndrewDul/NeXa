"""Local-provider projection adapter (R0077): renders a
:class:`~nexa.core.context.models.CurrentTurnContext` into a bounded
addendum for :meth:`~nexa.conversation.session.ConversationSession.send`'s
``context_provider`` hook.

Rendering/projection ONLY — never a second prompt-builder authority.
Renders exactly ``selected_context_items``, nothing else from
``CurrentTurnContext`` (no trace, no descriptors, no knowledge gaps, no
evidence, no payload, no source refs). ``ConversationSession``'s own
identity/persona composition (``self.system_prompt``, set once in
``bootstrap.py``) and its own conversation-history rendering
(``ConversationContext``/``ProviderWindow``, unchanged) remain the sole
authorities for those — this module only ever appends one additional,
bounded block.

Lives under ``nexa.conversation`` (not ``nexa.core.context``) for the same
reason ``nexa.realtime.context_projection`` lives under ``nexa.realtime``:
provider-facing projection is not a Core concern (R0075 §9/ADR-0006 D3).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..core.context import ContextEngine, derive_context_request
from ..core.context.models import ContextBudget, CurrentTurnContext
from .session import ConversationSession

logger = logging.getLogger(__name__)


def to_local_context_addendum(context: CurrentTurnContext) -> str | None:
    """``None`` when there is nothing to add — the caller then leaves
    ``system_prompt`` untouched."""
    if not context.selected_context_items:
        return None
    lines = [f"- {item.content}" for item in context.selected_context_items]
    return "Relevant things you already know:\n" + "\n".join(lines)


def make_local_context_provider(
    engine: ContextEngine, *, budget: ContextBudget | None = None
) -> Callable[[ConversationSession], str | None]:
    """Builds a ``context_provider`` callable for
    ``ConversationSession.send(context_provider=...)``."""

    def _provide(session: ConversationSession) -> str | None:
        request = derive_context_request(session, budget=budget)
        current_turn_context = engine.build_context(request)
        projection_start = time.monotonic()
        addendum = to_local_context_addendum(current_turn_context)
        projection_ms = (time.monotonic() - projection_start) * 1000
        logger.debug(
            "nexa.conversation.context_projection: discovery=%.1fms retrieval=%.1fms "
            "projection=%.1fms total_build=%.1fms items=%d",
            current_turn_context.trace.discovery_ms,
            current_turn_context.trace.retrieval_ms,
            projection_ms,
            current_turn_context.trace.total_ms,
            len(current_turn_context.selected_context_items),
        )
        return addendum

    return _provide
