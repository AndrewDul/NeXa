"""M2.1 — local audio + Silero VAD foundation (ADR-0003).

Pipecat is infrastructure: it owns audio capture/playback and VAD frame
production only. ``ConversationSession`` (unchanged, `src/nexa/conversation/`)
remains the sole conversation authority — nothing here calls it. STT
(M2.2), the ``ConversationSession`` voice adapter (M2.3), TTS (M2.4), and
barge-in (M2.5) are later substages, not built here.
"""

from __future__ import annotations

from .aec import AecReferenceHealth
from .bargein import BargeInController, BargeInTelemetry, InterruptContext
from .config import LocalAudioConfig
from .device import AudioDeviceNotFoundError, find_device_index
from .gate import HalfDuplexGate
from .interruption import (
    DEFAULT_CONFIRM_HOLD_SECS,
    InterruptionEvent,
    InterruptionState,
    InterruptionStateMachine,
)
from .runtime import DROP_BUSY_RESPONSE_IN_FLIGHT, DroppedUtterance, VoiceRuntime
from .state import VoiceEvent, VoiceState, VoiceStateMachine

__all__ = [
    "AudioDeviceNotFoundError",
    "HalfDuplexGate",
    "LocalAudioConfig",
    "VoiceEvent",
    "VoiceRuntime",
    "VoiceState",
    "VoiceStateMachine",
    "DroppedUtterance",
    "DROP_BUSY_RESPONSE_IN_FLIGHT",
    "find_device_index",
    # M2.5B — barge-in / interruption
    "AecReferenceHealth",
    "BargeInController",
    "BargeInTelemetry",
    "InterruptContext",
    "InterruptionStateMachine",
    "InterruptionState",
    "InterruptionEvent",
    "DEFAULT_CONFIRM_HOLD_SECS",
]
