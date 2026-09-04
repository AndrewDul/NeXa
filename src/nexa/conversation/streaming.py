"""``StreamingResponse`` — wraps a provider's raw token stream.

Combines the chunks it has yielded so far into ``.text``, so the session layer
can both stream chunks to a caller and know the completed assistant text once
iteration finishes, without every caller re-implementing accumulation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator


class StreamingResponse:
    def __init__(self, chunks: AsyncIterator[str]) -> None:
        self._chunks = chunks
        self._collected: list[str] = []

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        async for chunk in self._chunks:
            self._collected.append(chunk)
            yield chunk

    @property
    def text(self) -> str:
        """Text collected so far. Complete only once iteration is exhausted."""
        return "".join(self._collected)
