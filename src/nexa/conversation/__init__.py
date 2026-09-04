"""The M1.1 canonical text-conversation path (ADR-0002 D1).

``text → ConversationSession → ConversationContext → ModelProvider →
streamed tokens → appended assistant turn``. One path, no parallel "final
answer" pipeline, no MAS in the simple turn.
"""

from __future__ import annotations

from .context import ConversationContext
from .session import ConversationSession
from .streaming import StreamingResponse
from .turn import ConversationTurn, Role

__all__ = [
    "ConversationContext",
    "ConversationSession",
    "ConversationTurn",
    "Role",
    "StreamingResponse",
]
