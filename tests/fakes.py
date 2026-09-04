"""Test-only fakes. Never imported from ``src/nexa`` — production code must
never depend on test doubles."""

from __future__ import annotations

from collections.abc import AsyncIterator

from nexa.providers.base import (
    CancelToken,
    GenerationOptions,
    ModelProvider,
    ModelUnavailableError,
    ProviderDescription,
    ProviderMessage,
)


class FakeModelProvider(ModelProvider):
    """Deterministic in-memory ``ModelProvider`` for unit tests.

    Records every call's messages/options so tests can assert on exactly what
    the conversation layer sent. Never talks to a real model.
    """

    def __init__(
        self,
        chunks_by_call: list[list[str]] | None = None,
        *,
        fail_on_call: int | None = None,
    ) -> None:
        self.calls: list[list[ProviderMessage]] = []
        self.options_by_call: list[GenerationOptions] = []
        self._chunks_by_call = chunks_by_call or [["ok"]]
        self._fail_on_call = fail_on_call

    def describe(self) -> ProviderDescription:
        return ProviderDescription(provider_name="fake", model="fake-model")

    async def generate(
        self,
        messages: list[ProviderMessage],
        options: GenerationOptions,
        *,
        cancel_token: CancelToken | None = None,
    ) -> AsyncIterator[str]:
        call_index = len(self.calls)
        self.calls.append(list(messages))
        self.options_by_call.append(options)

        if self._fail_on_call is not None and call_index == self._fail_on_call:
            raise ModelUnavailableError("fake provider configured to fail")

        chunks = (
            self._chunks_by_call[call_index]
            if call_index < len(self._chunks_by_call)
            else ["ok"]
        )
        for chunk in chunks:
            if cancel_token is not None and cancel_token.is_cancelled:
                return
            yield chunk
