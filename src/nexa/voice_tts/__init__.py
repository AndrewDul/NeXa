"""M2.4 — the cross-boundary integration layer connecting the existing
assistant-text stream (`nexa.voice_conversation.VoiceConversationAdapter`,
unchanged) to Pipecat's TTS pipeline (`nexa.tts.PiperHttpServer` +
Pipecat's own `PiperHttpTTSService`/sentence aggregation/
`LocalAudioOutputTransport`).

Public surface: `AssistantSpeechBridge`, `voice_for_language` (bridge.py);
`ensure_sentence_tokenizer_data` (preflight.py).

Unlike `nexa.voice`/`nexa.stt`/`nexa.voice_conversation` (which must never
import a TTS engine or, for the latter two, `nexa.conversation` — enforced
by their own architecture tests), this package's whole job is combining
Pipecat, `nexa.tts`, and `nexa.conversation.language` — it never constructs
a second `ConversationSession`/`ModelProvider`/persona of its own.
"""

from __future__ import annotations

from nexa.voice import HalfDuplexGate

from .bridge import AssistantSpeechBridge, TtsStatusObserver, voice_for_language
from .preflight import ensure_sentence_tokenizer_data
from .timing import TurnTiming, TurnTimingTracker

__all__ = [
    "AssistantSpeechBridge",
    "HalfDuplexGate",
    "TtsStatusObserver",
    "voice_for_language",
    "ensure_sentence_tokenizer_data",
    "TurnTiming",
    "TurnTimingTracker",
]
