"""The M1.1 canonical text-conversation path (ADR-0002 D1).

``text → ConversationSession → ConversationContext → ModelProvider →
streamed tokens → appended assistant turn``. One path, no parallel "final
answer" pipeline, no MAS in the simple turn.
"""

from __future__ import annotations

from .context import ConversationContext
from .provider_window import (
    DEFAULT_PROVIDER_WINDOW_HARD,
    DEFAULT_PROVIDER_WINDOW_KEEP,
    DEFAULT_PROVIDER_WINDOW_SOFT,
    ProviderWindow,
)
from .response_language import (
    LanguageRequest,
    ResponseLanguageDecision,
    ResponseLanguageResolver,
    ResponsePreference,
    detect_explicit_language_request,
    detect_language_request,
)
from .response_mode import ResponseMode, voice_response_directive
from .session import ConversationSession, InterruptedTurnOutcome
from .streaming import StreamingResponse
from .turn import ConversationTurn, Role

__all__ = [
    "ConversationContext",
    "ConversationSession",
    "InterruptedTurnOutcome",
    "ProviderWindow",
    "DEFAULT_PROVIDER_WINDOW_KEEP",
    "DEFAULT_PROVIDER_WINDOW_SOFT",
    "DEFAULT_PROVIDER_WINDOW_HARD",
    "ConversationTurn",
    "Role",
    "ResponseMode",
    "voice_response_directive",
    "ResponseLanguageResolver",
    "ResponseLanguageDecision",
    "ResponsePreference",
    "LanguageRequest",
    "detect_language_request",
    "detect_explicit_language_request",
    "StreamingResponse",
]
