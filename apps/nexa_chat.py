#!/usr/bin/env python3
"""NeXa M1.1 developer text-conversation harness.

Thin wiring/presentation only (`apps/README.md`) — no product logic lives
here. Drives the real `nexa.bootstrap.build_default_session()` canonical path;
this is NOT the final NeXa Chat UI, just proof the real path works end to end.

Usage:
    python3 apps/nexa_chat.py
Type /quit to exit.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.bootstrap import build_default_session  # noqa: E402
from nexa.providers.base import ModelUnavailableError  # noqa: E402


async def main() -> None:
    session = build_default_session()
    description = session.provider.describe()
    print("NeXa M1.1 Text Conversation")
    print(f"(provider: {description.provider_name}, model: {description.model})")
    print("Type /quit to exit.\n")

    loop = asyncio.get_running_loop()
    while True:
        user_text = await loop.run_in_executor(None, input, "You: ")
        if user_text.strip() in {"/quit", "/exit"}:
            break
        if not user_text.strip():
            continue

        print("NeXa: ", end="", flush=True)
        try:
            async for chunk in session.send(user_text):
                print(chunk, end="", flush=True)
        except ModelUnavailableError as exc:
            print(f"\n[error] model unavailable: {exc}")
            continue
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
