"""NeXa's local STT boundary (M2.2, ADR-0003 D11).

Public surface: `Language`, `WhisperCppConfig` (config.py); `SpeechTranscriber`,
`WhisperCppTranscriber`, `TranscriptionResult` (transcriber.py); `UtteranceBuffer`
(utterance_buffer.py); the `SttError` hierarchy (errors.py).

M2.4B.5 adds the automatic-bilingual PL/EN input path (ADR-0003 D5
amendment): `LanguageDetector` / `WhisperCppLanguageDetector`
(library-level p_pl/p_en), `LanguageIdGuard`, `BilingualSpeechTranscriber`
— all behind this same boundary, so `nexa.voice` still depends only on
`SpeechTranscriber` / `TranscriptionResult`.
"""

from __future__ import annotations

from .bilingual import BilingualSpeechTranscriber, LanguageDecision
from .config import Language, WhisperCppConfig
from .errors import (
    LanguageDetectionError,
    NoUsableAudioError,
    SttBinaryNotFoundError,
    SttError,
    SttLibraryNotFoundError,
    SttMalformedOutputError,
    SttModelNotFoundError,
    SttQueueOverflowError,
    SttSubprocessError,
    SttTimeoutError,
    UnsupportedLanguageError,
)
from .language_detection import (
    LanguageDetectionResult,
    LanguageDetector,
    WhisperCppLanguageDetector,
)
from .language_guard import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    GuardConfig,
    GuardDecision,
    GuardOutcome,
    LanguageIdGuard,
)
from .queue import DEFAULT_MAX_QUEUE_SIZE, SerialTranscriptionQueue
from .transcriber import SpeechTranscriber, TranscriptionResult, WhisperCppTranscriber
from .utterance_buffer import PRE_ROLL_MS, UtteranceBuffer

__all__ = [
    "Language",
    "WhisperCppConfig",
    "SttError",
    "SttBinaryNotFoundError",
    "SttModelNotFoundError",
    "SttLibraryNotFoundError",
    "UnsupportedLanguageError",
    "SttTimeoutError",
    "SttSubprocessError",
    "SttMalformedOutputError",
    "NoUsableAudioError",
    "SttQueueOverflowError",
    "LanguageDetectionError",
    "SpeechTranscriber",
    "TranscriptionResult",
    "WhisperCppTranscriber",
    "UtteranceBuffer",
    "PRE_ROLL_MS",
    "SerialTranscriptionQueue",
    "DEFAULT_MAX_QUEUE_SIZE",
    # M2.4B.5 — automatic bilingual PL/EN input
    "LanguageDetector",
    "WhisperCppLanguageDetector",
    "LanguageDetectionResult",
    "LanguageIdGuard",
    "GuardConfig",
    "GuardDecision",
    "GuardOutcome",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "BilingualSpeechTranscriber",
    "LanguageDecision",
]
