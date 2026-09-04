"""Model provider abstraction (ADR-0002 D2) and its implementations.

A ``ModelProvider`` is a replaceable conversation/reasoning backend. NeXa's
identity, memory, and context never live here — only the mechanics of talking
to one model backend.
"""

from __future__ import annotations

from .base import (
    CancelToken,
    GenerationOptions,
    ModelProvider,
    ModelUnavailableError,
    ProviderDescription,
    ProviderMessage,
)

__all__ = [
    "CancelToken",
    "GenerationOptions",
    "ModelProvider",
    "ModelUnavailableError",
    "ProviderDescription",
    "ProviderMessage",
]
