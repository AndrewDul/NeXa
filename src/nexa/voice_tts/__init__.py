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

from .aec_reference import AecReferenceFeeder
from .bridge import AssistantSpeechBridge, TtsStatusObserver, voice_for_language
from .continuity import (
    DEFAULT_CONTINUITY_TARGET_S,
    ControllerRelease,
    NexaSpeechContinuityController,
    ReleaseReason,
    decide_release,
)
from .metrics import (
    ActivityFlags,
    BufferEstimate,
    MetricsCollector,
    ResourceSampler,
    TurnMetrics,
    TurnReportJsonlWriter,
    diagnose_dominant_wait,
    render_turn_report,
)
from .preflight import ensure_sentence_tokenizer_data
from .speech_planner import NexaSpeechPlanner, find_phrase_cut, normalize_for_speech
from .spoken_text import SpokenTextTracker
from .timed_tts import HttpSynthCall, TimedPiperHttpTTSService
from .timing import TurnTiming, TurnTimingTracker

# M2.4B.3.1: Pipecat's `PiperHttpTTSService(stop_frame_timeout_s=…)` — how
# long the speaking (audio) context stays alive with no new phrase text
# before it is torn down and a premature `TTSStoppedFrame` /
# `BotStopped`→`BotStarted` cycle is emitted. Pipecat's default is 3.0 s,
# which turns every normal inter-phrase `gemma4:e4b` stall into visible
# stop/start churn (R0012 §F, R0014). Raised so a short stall keeps one
# continuous speaking context. This ONLY keeps the context alive — it never
# adds silence; playback of already-queued audio is unaffected, and the
# real end of a turn is still driven by `LLMFullResponseEndFrame`.
DEFAULT_TTS_CONTEXT_TIMEOUT_S = 8.0

__all__ = [
    "AssistantSpeechBridge",
    "HalfDuplexGate",
    "TtsStatusObserver",
    "voice_for_language",
    "ensure_sentence_tokenizer_data",
    "DEFAULT_TTS_CONTEXT_TIMEOUT_S",
    "TurnTiming",
    "TurnTimingTracker",
    # M2.4B.1 — measure-only instrumentation
    "MetricsCollector",
    "TurnMetrics",
    "BufferEstimate",
    "ActivityFlags",
    "ResourceSampler",
    "TurnReportJsonlWriter",
    "render_turn_report",
    "diagnose_dominant_wait",
    # M2.4B.1A — true HTTP synthesis timing (measure-only wrapper)
    "TimedPiperHttpTTSService",
    "HttpSynthCall",
    # M2.4B.2 — Polish-aware speech planner / TTS-only normalization
    "NexaSpeechPlanner",
    "normalize_for_speech",
    "find_phrase_cut",
    # M2.4B.3.2 — short-reply speech continuity controller
    "NexaSpeechContinuityController",
    "ControllerRelease",
    "ReleaseReason",
    "decide_release",
    "DEFAULT_CONTINUITY_TARGET_S",
    # M2.5B — barge-in support
    "SpokenTextTracker",
    "AecReferenceFeeder",
]
