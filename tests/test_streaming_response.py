"""Deterministic tests for ``StreamingResponse``'s chunk combination."""

from __future__ import annotations

import sys
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.conversation.streaming import StreamingResponse  # noqa: E402


async def _fake_chunks(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


class TestStreamingResponse(unittest.IsolatedAsyncioTestCase):
    async def test_iterating_yields_each_chunk(self) -> None:
        response = StreamingResponse(_fake_chunks(["a", "b", "c"]))
        seen = [chunk async for chunk in response]
        self.assertEqual(seen, ["a", "b", "c"])

    async def test_text_combines_chunks_after_full_iteration(self) -> None:
        response = StreamingResponse(_fake_chunks(["Cześć", ", ", "jak się masz?"]))
        async for _ in response:
            pass
        self.assertEqual(response.text, "Cześć, jak się masz?")

    async def test_text_is_partial_mid_iteration(self) -> None:
        response = StreamingResponse(_fake_chunks(["one", "two", "three"]))
        it = response.__aiter__()
        await it.__anext__()
        self.assertEqual(response.text, "one")

    async def test_empty_stream_yields_empty_text(self) -> None:
        response = StreamingResponse(_fake_chunks([]))
        seen = [chunk async for chunk in response]
        self.assertEqual(seen, [])
        self.assertEqual(response.text, "")


if __name__ == "__main__":
    unittest.main()
