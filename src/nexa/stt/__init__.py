"""NeXa's local STT boundary (M2.2, ADR-0003 D11).

Public surface: `Language`, `WhisperCppConfig` (config.py); `SpeechTranscriber`,
`WhisperCppTranscriber`, `TranscriptionResult` (transcriber.py); `UtteranceBuffer`
(utterance_buffer.py); the `SttError` hierarchy (errors.py).

`nexa.voice` depends only on this module's shapes, never on whisper.cpp CLI
details directly.
"""

from __future__ import annotations

from .config import Language, WhisperCppConfig
from .errors import (
    NoUsableAudioError,
    SttBinaryNotFoundError,
    SttError,
    SttMalformedOutputError,
    SttModelNotFoundError,
    SttQueueOverflowError,
    SttSubprocessError,
    SttTimeoutError,
    UnsupportedLanguageError,
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
    "UnsupportedLanguageError",
    "SttTimeoutError",
    "SttSubprocessError",
    "SttMalformedOutputError",
    "NoUsableAudioError",
    "SttQueueOverflowError",
    "SpeechTranscriber",
    "TranscriptionResult",
    "WhisperCppTranscriber",
    "UtteranceBuffer",
    "PRE_ROLL_MS",
    "SerialTranscriptionQueue",
    "DEFAULT_MAX_QUEUE_SIZE",
]
