"""M2.1 — local audio + Silero VAD foundation (ADR-0003).

Pipecat is infrastructure: it owns audio capture/playback and VAD frame
production only. ``ConversationSession`` (unchanged, `src/nexa/conversation/`)
remains the sole conversation authority — nothing here calls it. STT
(M2.2), the ``ConversationSession`` voice adapter (M2.3), TTS (M2.4), and
barge-in (M2.5) are later substages, not built here.
"""

from __future__ import annotations

from .config import LocalAudioConfig
from .device import AudioDeviceNotFoundError, find_device_index
from .runtime import VoiceRuntime
from .state import VoiceEvent, VoiceState, VoiceStateMachine

__all__ = [
    "AudioDeviceNotFoundError",
    "LocalAudioConfig",
    "VoiceEvent",
    "VoiceRuntime",
    "VoiceState",
    "VoiceStateMachine",
    "find_device_index",
]
