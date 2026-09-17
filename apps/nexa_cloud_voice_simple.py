#!/usr/bin/env python3
"""NeXa — simplified cloud realtime conversation (ADR-0004 Amendment 2 / R0071).

The accepted, golden-M2.6A-derived production entry point for
``CLOUD PREFERRED`` conversation: ONE Pipecat pipeline (mic -> Gemini Live
-> speaker), Gemini's own native VAD-driven turn/interruption handling,
fed a privacy-filtered ``CloudContextSnapshot`` from NeXa Core. This is
NOT the paused M2.6B dual-pipeline runtime (``apps/nexa_cloud_voice_app.py``,
unchanged, kept as paused/experimental) — see
``src/nexa/realtime/gemini/simple_conversation.py`` for why.

R0080: this entrypoint now activates NeXa Core recall (M3.3/R0079's
``recall_context`` tool -- see ``nexa.realtime.gemini.core_recall_tool``)
by default, ON TOP OF the unchanged R0071 audio/VAD/AEC/barge-in
architecture -- nothing about audio construction changes. One
``RecallExecutor`` is created and started before the adapter, reused for
the whole session, and closed at shutdown; never rebuilt per tool call
(R0079 §22, R0080 §3). ``--no-core-recall`` disables it for diagnostics/
rollback without touching code -- the resulting construction is
byte-for-byte the pre-R0079 path (``recall_executor=None``).

Usage::

    .venv/bin/python apps/nexa_cloud_voice_simple.py --dry
    .venv/bin/python apps/nexa_cloud_voice_simple.py
    .venv/bin/python apps/nexa_cloud_voice_simple.py --no-core-recall
    .venv/bin/python apps/nexa_cloud_voice_simple.py --context-fact \\
        "The operator is currently working on the NeXa voice pipeline."
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
from nexa.realtime.gemini.core_recall_tool import RecallExecutor  # noqa: E402
from nexa.realtime.gemini.credentials import (  # noqa: E402
    GeminiCredentialError,
    load_gemini_credential,
)
from nexa.realtime.gemini.simple_conversation import (  # noqa: E402
    GEMINI_MODEL,
    build_cloud_realtime_conversation_adapter,
)
from nexa.realtime.policy import ConversationPolicy  # noqa: E402
from nexa.realtime.privacy import CloudEligibility  # noqa: E402
from nexa.realtime.provider import (  # noqa: E402
    AssistantTranscriptionEvent,
    ProviderInterruptionEvent,
    UserTranscriptionEvent,
)
from nexa.realtime.snapshot import build_cloud_context_snapshot  # noqa: E402
from nexa.voice.config import LocalAudioConfig  # noqa: E402


def _quiet_pipecat() -> None:
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
    p.add_argument(
        "--context-fact", action="append", default=[], metavar="TEXT",
        help="one CLOUD_SAFE context fact to inject into this session's "
        "CloudContextSnapshot (repeatable). Never use sensitive information.",
    )
    p.add_argument(
        "--core-recall", action=argparse.BooleanOptionalAction, default=True,
        help="R0080: NeXa Core recall_context tool (M3.3/R0079). Default ON. "
        "Use --no-core-recall to disable for diagnostics/regression without "
        "reverting code -- falls back to byte-for-byte pre-R0079 construction "
        "(no tool registered, no RecallExecutor). NOT YET live-hardware-"
        "verified end to end (R0080) -- keep this available for rapid rollback.",
    )
    return p.parse_args()


def _make_event_printer():
    def on_event(event) -> None:
        if isinstance(event, UserTranscriptionEvent) and event.final:
            print(f'\nyou: "{event.text}"')
        elif isinstance(event, AssistantTranscriptionEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, ProviderInterruptionEvent):
            print("\n  ✂ interrupted")

    return on_event


def _print_core_recall_diagnostics(*, enabled: bool, executor: RecallExecutor | None) -> None:
    """R0080 §10 -- startup diagnostics. Never logs Memory contents; only
    boolean/readiness state and the pinned model string."""
    print(f"CORE_RECALL {'enabled' if enabled else 'disabled (--no-core-recall)'}")
    if enabled and executor is not None:
        print(f"CORE_RECALL executor ready: {executor.started}")
        print("CORE_RECALL tool registered: recall_context")
    print(f"GEMINI_MODEL {GEMINI_MODEL}")


async def main() -> None:
    args = parse_args()
    _quiet_pipecat()

    print("NeXa — simplified cloud realtime conversation (golden M2.6A baseline)")

    session = build_default_session()
    context_facts = [(text, CloudEligibility.CLOUD_SAFE) for text in args.context_fact]
    snapshot = build_cloud_context_snapshot(
        session, policy_name=ConversationPolicy.CLOUD_PREFERRED.value,
        active_provider_name="cloud", context_facts=context_facts,
    )

    # R0080 §3: ONE RecallExecutor for the whole application/session
    # lifetime -- never rebuilt per tool call. None when --no-core-recall,
    # which makes build_cloud_realtime_conversation_adapter's construction
    # byte-for-byte the pre-R0079 path (R0079's own `recall_executor=None`
    # default).
    recall_executor: RecallExecutor | None = None
    if args.core_recall:
        recall_executor = RecallExecutor()
        await recall_executor.start()

    if args.dry:
        adapter = build_cloud_realtime_conversation_adapter(
            session=session, api_key="DRY-RUN-NO-KEY", snapshot=snapshot, dry=True,
            recall_executor=recall_executor,
        )
        print("dry mode: object graph constructed (router, Gemini service) -- "
              "no audio device, no Gemini connection.")
        print(f"  router policy: {adapter.router.policy.value}")
        # R0080: show what Gemini actually receives, not just the snapshot's
        # own value -- when Core recall is enabled the builder appends
        # RECALL_TOOL_USE_INSTRUCTION, so these can legitimately differ.
        print(f"  system_instruction: {adapter.llm._settings.system_instruction}")  # noqa: SLF001
        _print_core_recall_diagnostics(enabled=args.core_recall, executor=recall_executor)
        if recall_executor is not None:
            recall_executor.close()
        return

    try:
        credential = load_gemini_credential()
    except GeminiCredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if recall_executor is not None:
            recall_executor.close()
        sys.exit(1)

    def _on_aec_change(active: bool) -> None:
        msg = "✓ AEC_REF_ACTIVE" if active else "✗ AEC REF DOWN"
        print(f"\n  {msg}")

    adapter = build_cloud_realtime_conversation_adapter(
        session=session,
        api_key=credential.reveal(),
        snapshot=snapshot,
        audio_config=LocalAudioConfig(),
        policy=ConversationPolicy.CLOUD_PREFERRED,
        on_event=_make_event_printer(),
        on_aec_change=_on_aec_change,
        dry=False,
        recall_executor=recall_executor,
    )
    _print_core_recall_diagnostics(enabled=args.core_recall, executor=recall_executor)

    print("connecting to Gemini Live and opening the reSpeaker/USB-speaker "
          "hardware pipeline...")
    connected = await adapter.start()
    if not connected:
        print("error: Gemini socket did not connect before the kickoff "
              "timeout -- stopping.", file=sys.stderr)
        await adapter.stop(reason="kickoff timeout")
        if recall_executor is not None:
            recall_executor.close()
        sys.exit(1)

    print("\n>>> CLOUD_READY <<<\n")
    print("Speak naturally. Ctrl+C to stop.\n")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\n(stopped)")
    finally:
        await adapter.stop(reason="operator shutdown")
        if recall_executor is not None:
            recall_executor.close()
        print(f"\ncanonical history: {len(session.history)} entries")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n(stopped)")
