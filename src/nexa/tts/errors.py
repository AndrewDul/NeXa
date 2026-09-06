"""TTS error hierarchy (M2.4). Every failure is explicit — no silent
fallback, no fabricated audio-success state on failure."""

from __future__ import annotations


class TtsError(RuntimeError):
    """Base class for all M2.4 TTS errors."""


class PiperVenvNotFoundError(TtsError):
    """The external Piper virtual environment is missing. Run
    `scripts/setup_piper_http.py`."""


class PiperVoiceNotFoundError(TtsError):
    """A required Piper voice model is missing. Run
    `scripts/setup_piper_http.py`."""


class PiperServerStartError(TtsError):
    """The external Piper HTTP server process failed to start or did not
    become ready within the configured timeout."""


class PiperHttpError(TtsError):
    """A request to the external Piper HTTP server failed (non-200 status
    or connection error)."""


class SentenceTokenizerDataMissingError(TtsError):
    """NLTK's `punkt_tab` sentence-tokenizer data (used by Pipecat's
    sentence aggregation) is not present, and NeXa never downloads it
    silently at runtime — this must be installed explicitly, e.g. via
    `scripts/setup_piper_http.py`."""


class UnsupportedTtsLanguageError(TtsError):
    """A response language outside the explicitly supported PL/EN set was
    requested for voice selection."""
