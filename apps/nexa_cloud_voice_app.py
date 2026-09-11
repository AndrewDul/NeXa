#!/usr/bin/env python3
"""NeXa M2.6B.3 — production HYBRID cloud realtime voice (operator app).

The FIRST real production entry point for cloud realtime voice: reSpeaker
XVF3800 mic -> local Silero VAD turn authority -> ``ConversationRouter`` ->
production ``GeminiLiveProvider`` -> Gemini Live -> streamed 24 kHz
assistant audio -> NeXa-controlled USB speaker playback, teed exactly to
the XVF3800 AEC far-end reference -- while preserving the ONE canonical
``ConversationSession``, ``CloudTurnAccumulator``, spoken-prefix
interruption semantics, and local barge-in authority (ADR-0004).

This is thin wiring/presentation only (`apps/README.md`) over
``nexa.realtime.gemini.runtime.build_gemini_voice_runtime`` -- it never
reimplements the router/provider/barge-in/AEC logic, and it never calls
the M2.6A research probe (`docs/research/m2_6_cloud_realtime_voice/`),
which remains evidence/history only, not a production path.

Launched explicitly in CLOUD_PREFERRED (an operator choice for this run,
not the system default -- LOCAL_ONLY stays the default everywhere else).
No AUTO classifier, no LOCAL<->CLOUD switch test here.

Usage::

    .venv/bin/python apps/nexa_cloud_voice_app.py --dry
    .venv/bin/python apps/nexa_cloud_voice_app.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loguru import logger  # noqa: E402

from nexa.bootstrap import build_default_session  # noqa: E402
from nexa.realtime.gemini.credentials import (  # noqa: E402
    GeminiCredentialError,
    load_gemini_credential,
)
from nexa.realtime.gemini.runtime import build_gemini_voice_runtime  # noqa: E402
from nexa.realtime.policy import ConversationPolicy  # noqa: E402
from nexa.realtime.provider import (  # noqa: E402
    AssistantTranscriptionEvent,
    ProviderInterruptionEvent,
    ProviderReadiness,
    ReadinessChangedEvent,
    RealtimeProviderError,
    RealtimeProviderFailedError,
    ReconnectingEvent,
    ResumedEvent,
    UserTranscriptionEvent,
)


def _quiet_pipecat() -> None:
    """Keep the terminal readable: drop Pipecat/loguru INFO/DEBUG spam."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--dry", action="store_true",
        help="validate object construction only; no audio device, no Gemini connection",
    )
    return p.parse_args()


def _make_event_printer(printed_ready: list[bool]):
    """Returns an ``on_event`` hook for ``build_gemini_voice_runtime``.

    ``provider.events()`` is backed by ONE ``asyncio.Queue`` (single
    consumer by construction) -- printing must happen from inside the
    runtime's own single consumption loop via this hook, never from a
    second independent ``events()`` reader (which would race the runtime
    for events and could steal ones the router needs to commit a turn)."""

    def on_event(event) -> None:
        if isinstance(event, ReadinessChangedEvent):
            print(f"· PROVIDER READINESS: {event.readiness.value.upper()}")
            if event.readiness == ProviderReadiness.READY and not printed_ready[0]:
                printed_ready[0] = True
                print("\n>>> CLOUD_PROVIDER_READY <<<\n")
        elif isinstance(event, UserTranscriptionEvent) and event.final:
            print(f'you: "{event.text}"')
        elif isinstance(event, AssistantTranscriptionEvent):
            print(event.text, end="", flush=True)
            if event.final:
                print()
        elif isinstance(event, ProviderInterruptionEvent):
            print("\n  ✂ cloud interruption acknowledged by provider")
        elif isinstance(event, ReconnectingEvent):
            print(f"\n  ⟳ RECONNECTING ({event.reason})")
        elif isinstance(event, ResumedEvent):
            print("\n  ✓ resumed")
        elif isinstance(event, RealtimeProviderFailedError):
            print(f"\n  [provider FAILED] {event} -- falling back to LOCAL")
        elif isinstance(event, RealtimeProviderError):
            print(f"\n  [provider error] {event}")

    return on_event


async def main() -> None:
    args = parse_args()
    _quiet_pipecat()

    print("NeXa M2.6B.3 — Production HYBRID Cloud Realtime Voice")

    if args.dry:
        # No credential needed for construction-only validation.
        session = build_default_session()
        runtime = build_gemini_voice_runtime(
            session=session, api_key="DRY-RUN-NO-KEY", dry=True
        )
        print("dry mode: object graph constructed (provider, router, "
              "AEC health, BargeInController) -- no audio device, no "
              "Gemini connection.")
        print(f"  router policy: {runtime.router.policy.value}")
        print(f"  provider readiness: {runtime.provider.readiness.value}")
        return

    try:
        credential = load_gemini_credential()
    except GeminiCredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    def _on_aec_change(active: bool) -> None:
        msg = "✓ AEC_REF_ACTIVE" if active else "✗ AEC REF DOWN — barge-in unsafe"
        print(f"\n  {msg}")

    printed_ready = [False]
    session = build_default_session()
    runtime = build_gemini_voice_runtime(
        session=session,
        api_key=credential.reveal(),
        policy=ConversationPolicy.CLOUD_PREFERRED,
        on_event=_make_event_printer(printed_ready),
        on_aec_change=_on_aec_change,
        dry=False,
    )

    snapshot = runtime.router._snapshot_builder(  # noqa: SLF001
        session, policy_name=ConversationPolicy.CLOUD_PREFERRED.value,
        active_provider_name="cloud",
    )

    print("connecting to Gemini Live and opening the reSpeaker/USB-speaker "
          "hardware pipeline...")
    await runtime.start(snapshot)

    print("\nWaiting for CLOUD_PROVIDER_READY and AEC_REF_ACTIVE...")
    print("Once both appear, speak naturally. Ctrl+C to stop.\n")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\n(stopped)")
    finally:
        await runtime.stop(reason="operator shutdown")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n(stopped)")
