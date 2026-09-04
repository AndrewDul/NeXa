"""Wires the default M1.1 canonical conversation path together.

One place decides how the pieces are assembled from configuration, so
`apps/nexa_chat.py` and the live integration test build the exact same real
path — no parallel test-only wiring.
"""

from __future__ import annotations

from .config import load_persona, local_provider_settings_from_env
from .conversation.session import ConversationSession
from .providers.ollama import LocalModelProvider


def build_default_session() -> ConversationSession:
    persona = load_persona()
    settings = local_provider_settings_from_env()
    provider = LocalModelProvider(model=settings.model, base_url=settings.base_url)
    return ConversationSession(
        provider=provider,
        system_prompt=persona.system,
        options=persona.options,
    )
