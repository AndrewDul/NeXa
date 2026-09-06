"""STT error hierarchy (M2.2). Every failure is explicit — no silent
fallback, no hallucinated transcription on failure."""

from __future__ import annotations


class SttError(RuntimeError):
    """Base class for all M2.2 STT errors."""


class SttBinaryNotFoundError(SttError):
    """The whisper.cpp binary is missing. Run `scripts/setup_whisper_cpp.py`."""


class SttModelNotFoundError(SttError):
    """The whisper.cpp model file is missing. Run `scripts/setup_whisper_cpp.py`."""


class UnsupportedLanguageError(SttError):
    """A language hint other than the explicitly supported set was requested.

    Auto-detection is never accepted — R0006 measured it misclassifying a
    short Polish utterance as Japanese, independent of STT engine.
    """


class SttTimeoutError(SttError):
    """The whisper.cpp subprocess did not finish within the configured timeout."""


class SttSubprocessError(SttError):
    """The whisper.cpp subprocess exited with a non-zero status."""


class SttMalformedOutputError(SttError):
    """whisper.cpp exited successfully but its output could not be parsed,
    or contained no transcription — never treated as an empty-but-valid
    result."""


class NoUsableAudioError(SttError):
    """The utterance buffer contained no usable audio to transcribe."""


class SttQueueOverflowError(SttError):
    """`SerialTranscriptionQueue`'s bounded FIFO was full when a new
    utterance was submitted. Raised explicitly to the caller — an utterance
    is never silently dropped."""
