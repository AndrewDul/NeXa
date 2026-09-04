"""The ``ModelProvider`` interface (ADR-0002 D2).

NeXa's conversation layer talks only to this interface — never to a specific
vendor SDK or wire format. The wire shape targeted by implementations is the
OpenAI-compatible streaming chat contract, so one adapter shape can drive
Ollama, ``llama-server``, or a future online provider by configuration alone.
No provider name may appear in core control flow (AGENTS.md §3.4).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


class CancelToken:
    """Cooperative, per-request cancellation signal.

    Per-request only — never shared mutable state across requests (legacy
    blocker B1, ADR-0002 D1 constraint 2).
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    """One chat message in the wire-level shape a provider expects."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """Sampling / context options. Deliberately minimal — no tools, no images,
    no batching in M1 (ADR-0002 D2)."""

    num_ctx: int = 8192
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    repeat_penalty: float = 1.05
    num_predict: int = 200
    think: bool | None = None
    """None = provider/model default. False = force non-thinking mode where
    the model supports it. Never guessed by a provider implementation."""


@dataclass(frozen=True, slots=True)
class ProviderDescription:
    provider_name: str
    model: str
    supports_streaming: bool = True


class ModelUnavailableError(RuntimeError):
    """Raised when a provider cannot produce a response.

    Callers must surface this explicitly and must never silently switch to a
    different model or provider (AGENTS.md §3.2/§3.4, ADR-0002 D1 constraint 4).
    """


class ModelProvider(ABC):
    """A replaceable conversation/reasoning backend."""

    @abstractmethod
    def describe(self) -> ProviderDescription:
        """Static description of what this provider instance talks to."""

    @abstractmethod
    def generate(
        self,
        messages: list[ProviderMessage],
        options: GenerationOptions,
        *,
        cancel_token: CancelToken | None = None,
    ) -> AsyncIterator[str]:
        """Stream assistant text chunks for the given ordered messages.

        Raises ``ModelUnavailableError`` on any failure to produce a response.
        Must never fall back to a different model/provider internally.
        """
