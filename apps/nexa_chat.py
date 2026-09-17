#!/usr/bin/env python3
"""NeXa M1.1 developer text-conversation harness.

Thin wiring/presentation only (`apps/README.md`) — no product logic lives
here. Drives the real `nexa.bootstrap.build_default_session()` canonical path;
this is NOT the final NeXa Chat UI, just proof the real path works end to end.

M3.3 (R0077): also wires the real Context Engine in via
`build_default_context_runtime()` + `nexa.conversation.context_projection`
— relevant remembered `CLOUD_SAFE`/`LOCAL_ONLY` Memory content (local path
only, no cloud crossing here) may now supplement an answer. This is the
first production entrypoint to do so.

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

from nexa.bootstrap import build_default_context_runtime, build_default_session  # noqa: E402
from nexa.conversation.context_projection import make_local_context_provider  # noqa: E402
from nexa.providers.base import ModelUnavailableError  # noqa: E402


async def main() -> None:
    session = build_default_session()
    runtime = build_default_context_runtime()
    context_provider = make_local_context_provider(runtime.context_engine)
    description = session.provider.describe()
    print("NeXa M1.1 Text Conversation")
    print(f"(provider: {description.provider_name}, model: {description.model})")
    print("Type /quit to exit.\n")

    loop = asyncio.get_running_loop()
    try:
        while True:
            user_text = await loop.run_in_executor(None, input, "You: ")
            if user_text.strip() in {"/quit", "/exit"}:
                break
            if not user_text.strip():
                continue

            print("NeXa: ", end="", flush=True)
            try:
                async for chunk in session.send(user_text, context_provider=context_provider):
                    print(chunk, end="", flush=True)
            except ModelUnavailableError as exc:
                print(f"\n[error] model unavailable: {exc}")
                continue
            print("\n")
    finally:
        runtime.connection.close()


if __name__ == "__main__":
    asyncio.run(main())
