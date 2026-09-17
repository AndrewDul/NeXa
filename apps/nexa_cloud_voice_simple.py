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
import time
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
    p.add_argument(
        "--diagnostic-timeline", action="store_true",
        help="R0081: print a narrow, timestamped event timeline "
        "(LOCAL_VAD_START/STOP, BOT_AUDIO_STARTED/STOPPED, USER_TURN_START/END, "
        "INTERRUPTION_FRAME, PLAYBACK_STOPPED, the final user transcript, and "
        "TOOL_CALL_START/END when Core recall is enabled) to diagnose false "
        "barge-in / self-echo. Purely additive observation -- never changes "
        "routing, interruption, or turn logic. Off by default; never logs "
        "Memory/recall content.",
    )
    p.add_argument(
        "--diagnostic-audio-levels", action="store_true",
        help="R0081: also print numeric-only mic/reference RMS + reference "
        "queue depth/dropped-chunk telemetry (MIC_RMS, REF_RMS, "
        "REF_QUEUE_DEPTH, REF_DROPPED), throttled, interleaved with "
        "--diagnostic-timeline's event markers. Requires --diagnostic-timeline "
        "(needs a sink to print into). Never logs/stores raw PCM. Inserts one "
        "additional, purely-observational FrameProcessor into the pipeline "
        "only when this flag is set -- off by default, pipeline shape "
        "otherwise unchanged from R0071.",
    )
    p.add_argument(
        "--coherent-reference-gain", action="store_true",
        help="R0081 §13/§B: opt-in fix for a CONFIRMED real defect (R0053) "
        "never applied to this path before -- the AEC far-end reference is "
        "otherwise always unscaled, disconnected from the audible speaker's "
        "own real ALSA mixer gain. NOT proven sufficient alone (R0054's own "
        "erratum on the sibling pipeline it WAS tested against: reduced but "
        "did not eliminate false self-barge-in). Off by default -- preserves "
        "exact R0071/R0080 unscaled behavior; use this for A/B comparison.",
    )
    return p.parse_args()


#: R0081 §14: Pipecat's own broadcast_interruption() fans out an upstream +
#: downstream InterruptionFrame instance per ONE confirmed interruption
#: (see the tap's own InterruptionFrame comment) -- so, by design, TWO
#: ProviderInterruptionEvents legitimately arrive back-to-back for one real
#: interruption. This is DUPLICATE TELEMETRY, not a duplicate interruption
#: authority: ConversationRouter.on_interruption() (via CloudTurnAccumulator)
#: is already idempotent (a plain flag set, a no-op once already True) --
#: unaffected by this debounce, which touches ONLY this print function.
_INTERRUPTION_PRINT_DEBOUNCE_S = 0.1


def _make_event_printer():
    last_interruption_printed_at: list[float] = [-1.0]

    def on_event(event) -> None:
        if isinstance(event, UserTranscriptionEvent) and event.final:
            print(f'\nyou: "{event.text}"')
        elif isinstance(event, AssistantTranscriptionEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, ProviderInterruptionEvent):
            now = time.monotonic()
            if now - last_interruption_printed_at[0] >= _INTERRUPTION_PRINT_DEBOUNCE_S:
                print("\n  ✂ interrupted")
            last_interruption_printed_at[0] = now

    return on_event


def _make_diagnostic_timeline_printer():
    """R0081 §4/§5 -- narrow, timestamped event timeline. ``label`` is
    either a bare marker (``"LOCAL_VAD_START"``) or, for the one case that
    legitimately carries text, ``"USER_TRANSCRIPT:<text>"`` -- the
    operator's own spoken words, printed locally, only because this flag
    was explicitly opted into. Never logs Memory/recall content: Core
    recall's own diagnostic markers (``TOOL_CALL_START``/``TOOL_CALL_END``,
    wired in ``main()`` below) are bare labels, never carrying the query
    or the recalled facts."""
    start = time.monotonic()

    def on_diagnostic(label: str) -> None:
        print(f"  ⏱ {time.monotonic() - start:8.3f}s  {label}")

    return on_diagnostic


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

    # R0081 §4/§5: narrow, additive diagnostic timeline -- off by default,
    # never affects construction/routing when the flag is not passed.
    diagnostic = _make_diagnostic_timeline_printer() if args.diagnostic_timeline else None

    if args.dry:
        adapter = build_cloud_realtime_conversation_adapter(
            session=session, api_key="DRY-RUN-NO-KEY", snapshot=snapshot, dry=True,
            recall_executor=recall_executor, on_diagnostic=diagnostic,
            diagnostic_audio_levels=args.diagnostic_audio_levels,
            coherent_reference_gain=args.coherent_reference_gain,
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
        if diagnostic is not None:
            diagnostic("AEC_REF_ACTIVE" if active else "AEC_REF_DOWN")

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
        on_diagnostic=diagnostic,
        diagnostic_audio_levels=args.diagnostic_audio_levels,
        coherent_reference_gain=args.coherent_reference_gain,
    )
    _print_core_recall_diagnostics(enabled=args.core_recall, executor=recall_executor)
    if args.coherent_reference_gain:
        print("  ⚙ coherent-reference-gain ENABLED (R0081, opt-in A/B fix)")

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
