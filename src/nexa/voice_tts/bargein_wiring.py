"""M2.5B — the single place that cross-wires the barge-in components.

The bilingual voice probe (and the M2.5B integration tests, and the
hardware-safe wiring smoke) all build the barge-in stack through
``build_bargein_stack`` so there is exactly one construction path — no
chance of the probe and a test diverging.

It composes, and cross-wires with ONE shared ``AecReferenceHealth``:

- ``HalfDuplexGate(bargein_enabled, aec_health)``      — mic-open policy
- ``BargeInController(aec_health, …)``                 — after VADProcessor
- ``AecReferenceFeeder(aec_health, …)``                — after the Piper TTS
- ``SpokenTextTracker``                                — delivered-text mark

and returns the small hooks the app plugs into its existing callbacks
(``on_user_transcript`` → ``note_response_dispatched``; the TTS observer's
``on_tts_text`` → ``note_tts_sentence``; the bridge's ``on_interruption``
→ ``note_interruption``; the adapter's ``response_id_source`` /
``spoken_prefix_source``; ``output_stages`` inserts the feeder after Piper).

``enabled=False`` returns a stack whose ``controller`` / ``aec_feeder`` are
``None``, whose gate is a plain ``HalfDuplexGate()`` and whose hooks are
no-ops — i.e. R0026, byte-for-byte.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from nexa.voice.aec import AecReferenceHealth
from nexa.voice.bargein import BargeInController, InterruptContext
from nexa.voice.gate import HalfDuplexGate

from .aec_reference import AecReferenceFeeder
from .spoken_text import SpokenTextTracker


@dataclass
class BargeInStack:
    enabled: bool
    aec_health: AecReferenceHealth
    gate: HalfDuplexGate
    tracker: SpokenTextTracker
    controller: BargeInController | None = None
    aec_feeder: AecReferenceFeeder | None = None
    _dispatched: list[int | None] = field(default_factory=list, repr=False)

    # -- hooks the app plugs into its existing callbacks --------------- #
    def note_response_dispatched(self) -> int | None:
        """Call from the app's ``on_user_transcript`` (right after
        ``bridge.on_user_transcript`` so the gate's dispatch fires first).
        Allocates this turn's ``response_id`` and starts the spoken-text
        tracker for it. Returns the id (``None`` when disabled)."""
        if self.controller is None:
            return None
        rid = self.controller.notify_response_dispatched()
        self.tracker.start_response(rid)
        self._dispatched.append(rid)
        return rid

    def note_tts_sentence(self, text: str) -> None:
        """Wire to ``TtsStatusObserver(on_tts_text=…)``. Credits the
        synthesized sentence to the reply currently in flight; a sentence
        that arrives once an interruption has invalidated the id (or from a
        stale reply) is dropped by the tracker."""
        if self.controller is None:
            return
        rid = self.controller.active_response_id
        self.tracker.add_synthesized_sentence(rid if rid is not None else -1, text)

    def note_interruption(self) -> None:
        """Wire to ``AssistantSpeechBridge(on_interruption=…)``. Closes the
        interruption state machine / lets the gate reopen. Does NOT clear
        the tracker — the adapter has already captured the spoken prefix on
        the loop; the next ``note_response_dispatched`` resets it."""
        if self.controller is not None:
            self.controller.notify_response_finished()

    def response_id_source(self) -> int | None:
        """Wire to ``VoiceConversationAdapter(response_id_source=…)``."""
        return self.controller.active_response_id if self.controller is not None else None

    def spoken_prefix_source(self, response_id: int | None) -> str:
        """Wire to ``VoiceConversationAdapter(spoken_prefix_source=…)``."""
        return self.tracker.spoken_prefix(response_id) if response_id is not None else ""

    def output_stages(self, before_tts: list, tts_service, observer) -> list:
        """Assemble ``extra_output_stages`` with the AEC feeder inserted
        **after** the Piper TTS service and before the status observer."""
        stages = [*before_tts, tts_service]
        if self.aec_feeder is not None:
            stages.append(self.aec_feeder)
        stages.append(observer)
        return stages


def build_bargein_stack(
    *,
    enabled: bool,
    sample_rate: int,
    channels: int = 1,
    on_confirmed: Callable[[InterruptContext], None],
    on_candidate: Callable[[int | None], None] | None = None,
    on_candidate_rejected: Callable[[], None] | None = None,
    aec_status: Callable[[bool], None] | None = None,
    aec_sink_factory=None,
) -> BargeInStack:
    """Build + cross-wire the barge-in stack. ``on_confirmed`` is supplied
    by the app because it needs the adapter (`adapter.interrupt_active_turn`).
    ``aec_sink_factory`` is for tests (a fake PCM sink)."""
    health = AecReferenceHealth(on_change=aec_status if enabled else None)
    gate = HalfDuplexGate(bargein_enabled=enabled, aec_health=health)
    tracker = SpokenTextTracker()
    if not enabled:
        return BargeInStack(enabled=False, aec_health=health, gate=gate, tracker=tracker)

    controller = BargeInController(
        aec_health=health,
        on_confirmed=on_confirmed,
        on_candidate=on_candidate,
        on_candidate_rejected=on_candidate_rejected,
    )
    feeder_kwargs = {}
    if aec_sink_factory is not None:
        feeder_kwargs["sink_factory"] = aec_sink_factory
    feeder = AecReferenceFeeder(
        aec_health=health, sample_rate=sample_rate, channels=channels, **feeder_kwargs
    )
    return BargeInStack(
        enabled=True, aec_health=health, gate=gate, tracker=tracker,
        controller=controller, aec_feeder=feeder,
    )
