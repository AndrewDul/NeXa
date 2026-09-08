"""M2.3 — the cross-boundary integration layer connecting voice/STT input
to the existing, unchanged ``ConversationSession`` (ADR-0003 D2).

Public surface: ``VoiceConversationAdapter`` (adapter.py);
``SerialConversationQueue``, ``ConversationQueueOverflowError``,
``DEFAULT_MAX_QUEUE_SIZE`` (queue.py).

Unlike ``nexa.voice``/``nexa.stt`` (which must never import
``nexa.conversation``/``nexa.providers`` — enforced by their own
architecture tests), this package's whole job is bridging those boundaries
into ``ConversationSession``. It never constructs a second
``ConversationSession``/``ModelProvider`` of its own — it is handed one,
already built by ``nexa.bootstrap``, exactly like ``apps/nexa_chat.py``.
"""

from __future__ import annotations

from .adapter import (
    DROP_BUSY_RESPONSE_IN_FLIGHT,
    DroppedTurn,
    TurnLanguage,
    VoiceConversationAdapter,
)
from .queue import DEFAULT_MAX_QUEUE_SIZE, ConversationQueueOverflowError, SerialConversationQueue

__all__ = [
    "VoiceConversationAdapter",
    "TurnLanguage",
    "DroppedTurn",
    "DROP_BUSY_RESPONSE_IN_FLIGHT",
    "SerialConversationQueue",
    "ConversationQueueOverflowError",
    "DEFAULT_MAX_QUEUE_SIZE",
]
