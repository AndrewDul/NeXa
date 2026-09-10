#!/usr/bin/env python3
# ruff: noqa: E501  (research spike — long regex literals + print/format lines)
"""R0030 research spike — OFFLINE static audit of the *installed* Pipecat
``GeminiLiveLLMService`` (pipecat 1.8.1).

Why static (not import): ``pipecat.services.google.gemini_live.llm`` hard-imports
``google.genai`` at module load, and ``google-genai`` is deliberately NOT
installed in this venv (research task — no cloud dependency, no API key). So we
read the source file and check for the specific behaviours R0030 asks about:

* the silent send-guards that back GitHub issue #5465 (audio / text / video /
  tool-result dropped with a bare ``return`` when the session is not ready);
* whether ``GoAway`` is handled proactively;
* whether session resumption + reconnect are implemented;
* whether Gemini 3.x non-blocking (async) tools are supported.

No network, no API key, no side effects. Pure read + regex over one file.

Run:  ``.venv/bin/python docs/research/m2_6_cloud_realtime_voice/inspect_pipecat_gemini_live.py``
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


def _find_llm_source() -> Path:
    spec = importlib.util.find_spec("pipecat")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("pipecat is not importable in this environment")
    root = Path(next(iter(spec.submodule_search_locations)))
    src = root / "services" / "google" / "gemini_live" / "llm.py"
    if not src.is_file():
        raise SystemExit(f"expected Gemini Live service at {src} — not found")
    return src


def _pipecat_version() -> str:
    try:
        import pipecat  # noqa: PLC0415

        return getattr(pipecat, "__version__", "unknown")
    except Exception as exc:  # pragma: no cover - defensive
        return f"unimportable ({exc!r})"


CHECKS: list[tuple[str, str, str]] = [
    # key, regex, human description
    (
        "silent_guard_send_user_audio",
        r"async def _send_user_audio\(self, frame\):[\s\S]{0,900}?not self\._ready_for_realtime_input\s*\n\s*\):\s*\n\s*return",
        "issue #5465: _send_user_audio bare-returns (drops mic audio) when not ready",
    ),
    (
        "silent_guard_send_user_text",
        r"async def _send_user_text\(self, text: str\):[\s\S]{0,1600}?not self\._ready_for_realtime_input:\s*\n\s*return",
        "issue #5465: _send_user_text bare-returns (drops text) when not ready",
    ),
    (
        "silent_guard_send_user_video",
        r"async def _send_user_video\(self, frame\):[\s\S]{0,900}?not self\._ready_for_realtime_input\s*\n\s*\):\s*\n\s*return",
        "issue #5465: _send_user_video bare-returns when not ready",
    ),
    (
        "reconnect_implemented",
        r"async def _reconnect\(self\):",
        "reactive reconnect on connection error is implemented",
    ),
    (
        "session_resumption",
        r"SessionResumptionConfig\(handle=session_resumption_handle\)",
        "session resumption handle is passed on (re)connect",
    ),
    (
        "resumption_update_captured",
        r"def _handle_msg_resumption_update\(self, message",
        "sessionResumptionUpdate.newHandle is captured",
    ),
    (
        "max_consecutive_failures",
        r"MAX_CONSECUTIVE_FAILURES\s*=\s*3",
        "reconnect gives up after 3 consecutive failures (then push_error)",
    ),
    (
        "goaway_handled",
        r"go_?away|goAway|GoAway",
        "GoAway (imminent-disconnect warning with time_left) is handled",
    ),
    (
        "proactive_reconnect_on_goaway",
        r"time_left[\s\S]{0,200}?_reconnect",
        "GoAway.time_left proactively triggers a reconnect before the socket drops",
    ),
    (
        "gemini3_blocks_async_tools",
        r"Gemini 3\.x has not yet shipped support for NON_BLOCKING",
        "Gemini 3.x async/non-blocking function calls are NOT supported (documented in-source)",
    ),
    (
        "interrupted_context_may_hold_unspoken_text",
        r"on an interruption our recorded context will[\s\S]{0,40}?contain some text that was actually never spoken",
        "in-source note: on interruption the recorded context can contain never-spoken text",
    ),
    (
        "local_vad_activity_markers",
        r"send_realtime_input\(activity_start=ActivityStart\(\)\)",
        "local-VAD mode: activity_start/activity_end markers are sent from Silero turn frames",
    ),
    (
        "no_user_speaking_frames",
        r"does not give us broadly reliable\s*#\s*signals|Does NOT emit ``UserStartedSpeakingFrame``",
        "service does NOT emit UserStarted/StoppedSpeakingFrame",
    ),
]


def main() -> int:
    src = _find_llm_source()
    text = src.read_text(encoding="utf-8")
    n_lines = text.count("\n") + 1

    print("=" * 78)
    print("R0030 spike — Pipecat GeminiLiveLLMService static audit")
    print("=" * 78)
    print(f"pipecat version : {_pipecat_version()}")
    print(f"source file     : {src}")
    print(f"source size     : {n_lines} lines")
    print(f"google-genai    : {'importable' if importlib.util.find_spec('google.genai') else 'NOT installed (expected for this research task)'}")
    print("-" * 78)

    results: dict[str, bool] = {}
    for key, pattern, desc in CHECKS:
        hit = re.search(pattern, text) is not None
        results[key] = hit
        mark = "PRESENT " if hit else "ABSENT  "
        print(f"[{mark}] {key}\n           {desc}")

    print("-" * 78)
    # Interpret the specific #5465 verdict.
    drops = [
        results["silent_guard_send_user_audio"],
        results["silent_guard_send_user_text"],
        results["silent_guard_send_user_video"],
    ]
    print("VERDICT — GitHub issue #5465 (silent drop while reconnecting):")
    if any(drops):
        print("  CONFIRMED in this installed version: at least one send path")
        print("  bare-returns (drops the payload) when the session is not ready,")
        print("  which is exactly the reconnect window. No fix present.")
    else:
        print("  NOT reproduced by these patterns — re-audit manually.")

    print()
    print("VERDICT — GoAway handling:")
    if results["goaway_handled"]:
        print("  Some GoAway handling present — inspect manually.")
    else:
        print("  ABSENT: no GoAway handling. Reconnect is purely REACTIVE (waits")
        print("  for the ~10-min socket to actually error). A proactive")
        print("  pre-expiry reconnect is a NeXa-side gap to close.")

    print()
    print("VERDICT — Gemini 3.x async / non-blocking function calls:")
    if results["gemini3_blocks_async_tools"]:
        print("  NOT SUPPORTED (in-source): synchronous tool calls only on")
        print("  gemini-3.x. cancel_on_interruption=False cannot work there.")

    print("=" * 78)
    # This spike is documentation/evidence, not a gate: always exit 0 unless the
    # file could not be read.
    return 0


if __name__ == "__main__":
    sys.exit(main())
