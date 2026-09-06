"""M2.4 — the local TTS boundary: an external Piper HTTP server process
(ADR-0003 D6). The GPL-3.0-licensed `piper-tts` package runs entirely
outside NeXa's own process (its own venv, its own subprocess) — this
package only talks HTTP to it.

Public surface: `PiperHttpConfig`, `EN_VOICE`, `PL_VOICE`,
`PIPER_TTS_VERSION` (config.py); `PiperHttpServer` (server.py); the
`TtsError` hierarchy (errors.py).

`nexa.voice`/`nexa.stt`/`nexa.voice_conversation` must never import this
package or Pipecat's TTS services directly — `nexa.voice_tts` is the one
cross-boundary layer allowed to combine this with Pipecat and
`nexa.conversation`.
"""

from __future__ import annotations

from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    EN_VOICE,
    PIPER_TTS_VERSION,
    PL_VOICE,
    PiperHttpConfig,
)
from .errors import (
    PiperHttpError,
    PiperServerStartError,
    PiperVenvNotFoundError,
    PiperVoiceNotFoundError,
    SentenceTokenizerDataMissingError,
    TtsError,
    UnsupportedTtsLanguageError,
)
from .server import PiperHttpServer

__all__ = [
    "PIPER_TTS_VERSION",
    "EN_VOICE",
    "PL_VOICE",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "PiperHttpConfig",
    "PiperHttpServer",
    "TtsError",
    "PiperVenvNotFoundError",
    "PiperVoiceNotFoundError",
    "PiperServerStartError",
    "PiperHttpError",
    "SentenceTokenizerDataMissingError",
    "UnsupportedTtsLanguageError",
]
