#!/usr/bin/env python3
# ruff: noqa: E501  (research spike — long strings / prints)
"""R0031 / M2.6A — smallest authenticated Gemini Live connectivity smoke.

Proves ONLY, with NO microphone / speaker / conversation:

* the credential is accepted,
* the model ``gemini-3.1-flash-live-preview`` exists and is reachable,
* the Live API WebSocket opens and the setup handshake completes,
* a clean disconnect,
* no secret leakage in output.

Credential: env var ``NEXA_GEMINI_API_KEY``. If unset, this script loads it
from the operator-local file ``~/.config/nexa/secrets/gemini.env`` (outside
the repo). The key VALUE is never printed; any accidental occurrence in an
error string is redacted.

Run:
    set -a; . ~/.config/nexa/secrets/gemini.env; set +a
    .venv/bin/python docs/research/m2_6_cloud_realtime_voice/m2_6a_connect_smoke.py

Exit 0 = handshake OK. Non-zero = auth / model / quota / network failure
(sanitised error printed).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

MODEL = "gemini-3.1-flash-live-preview"
SECRETS_FILE = Path.home() / ".config" / "nexa" / "secrets" / "gemini.env"


def _load_key() -> str:
    key = os.environ.get("NEXA_GEMINI_API_KEY", "").strip()
    if not key and SECRETS_FILE.is_file():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("NEXA_GEMINI_API_KEY=") and not line.startswith("#"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not key:
        print("FAIL: NEXA_GEMINI_API_KEY not set and no key in", SECRETS_FILE)
        raise SystemExit(2)
    return key


def _redactor(key: str):
    tail = key[-4:] if len(key) >= 4 else ""

    def red(s: str) -> str:
        out = s.replace(key, "<REDACTED_KEY>")
        if tail:
            out = out.replace(tail, "****")
        return out

    return red


async def _smoke(key: str, red) -> int:
    try:
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415
    except Exception as e:  # pragma: no cover
        print("FAIL: google-genai import:", red(repr(e)))
        return 3

    print(f"google-genai   : {getattr(genai, '__version__', '?')}")
    print(f"model          : {MODEL}")
    print(f"key            : present (length {len(key)}, not shown)")

    client = genai.Client(api_key=key)
    config = types.LiveConnectConfig(response_modalities=["AUDIO"])

    t0 = time.monotonic()
    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            dt = (time.monotonic() - t0) * 1000
            print(f"PASS: Live WebSocket opened + setup complete in {dt:.0f} ms")
            # Do not send anything. Close immediately.
            _ = session
        print("PASS: clean disconnect")
        return 0
    except Exception as e:  # noqa: BLE001 - smoke: classify + sanitise
        dt = (time.monotonic() - t0) * 1000
        msg = red(f"{type(e).__name__}: {e}")
        low = msg.lower()
        if "api_key" in low or "unauthenticated" in low or "permission" in low or "401" in low or "403" in low:
            kind = "AUTH"
        elif "not found" in low or "404" in low or "model" in low:
            kind = "MODEL"
        elif "quota" in low or "429" in low or "resource_exhausted" in low or "billing" in low:
            kind = "QUOTA/BILLING"
        elif "timeout" in low or "getaddrinfo" in low or "connection" in low or "network" in low:
            kind = "NETWORK"
        else:
            kind = "OTHER"
        print(f"FAIL ({kind}) after {dt:.0f} ms: {msg}")
        return 4


def main() -> int:
    key = _load_key()
    red = _redactor(key)
    try:
        return asyncio.run(_smoke(key, red))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
