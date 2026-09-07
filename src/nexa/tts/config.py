"""M2.4 TTS configuration: the external Piper HTTP runtime location and the
explicit PL/EN voice identifiers.

Canonical source of truth for where NeXa expects the external Piper venv,
its HTTP server, and its voice models to live — `scripts/setup_piper_http.py`
imports these same functions rather than duplicating the path logic, the
same pattern `nexa.stt.config`/`scripts/setup_whisper_cpp.py` already
established for whisper.cpp.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Pinned exactly to the version verified working in R0010's spike (2026-09-06,
# this Pi 5) — not re-verified here. Change only with new measured evidence,
# same discipline as nexa.stt's whisper.cpp pin.
PIPER_TTS_VERSION = "1.8.0"

# The product voices (M2.4, 2026-09-06): the exact same Piper voices the
# prior local NeXa assistant used (verified against its own read-only
# reference config, modules/shared/config/settings_core/defaults.py, and
# its actual voice asset files) — not the R0010 spike's placeholder "low"
# quality voices (kept below only as EN_VOICE_SPIKE/PL_VOICE_SPIKE for
# reference/tests). Both are 22050Hz "medium" quality; Pipecat's
# `LocalAudioOutputTransport` resamples automatically (verified in R0010),
# so this needs no special handling. Piper's own embedded inference
# defaults for both (`noise_scale=0.667`, `length_scale=1`, `noise_w=0.8`)
# match what the legacy assistant used — it never overrode them — so no
# custom `SynthesisConfig` is needed either.
EN_VOICE = "en_GB-jenny_dioco-medium"
PL_VOICE = "pl_PL-gosia-medium"

# R0010's original spike voices — small (16kHz, "low" quality), used only
# by the M2.4A research spike and now by tests that want a small,
# throwaway voice rather than the real 61MB product voices.
EN_VOICE_SPIKE = "en_US-amy-low"
PL_VOICE_SPIKE = "pl_PL-mls_6892-low"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
DEFAULT_STARTUP_TIMEOUT_S = 30.0
DEFAULT_REQUEST_TIMEOUT_S = 15.0

# M2.4B.3.1: the external Piper HTTP process is started at this POSIX nice
# value so `llama-server` (which stays at nice 0) wins the CPU while
# gemma4:e4b is generating the next speech phrase. Benchmark-backed on this
# Pi 5 (R0014 §"CPU STRATEGY BENCHMARK", strategy C): with Piper niced +10,
# gemma4:e4b prompt_eval recovered ~24 s -> ~0.9 s and generation ~1.3 ->
# ~3.1 tok/s while Piper stayed true-RTF ~0.44 (still >2x real time).
# Positive nice needs no privilege; NeXa's own process is never reniced;
# `llama-server` is never touched. 0 disables it. Override with
# `NEXA_PIPER_NICE`.
DEFAULT_PIPER_NICE = 10


def default_piper_nice() -> int:
    """Piper's start-up nice value: ``NEXA_PIPER_NICE`` if set to an integer,
    else ``DEFAULT_PIPER_NICE``. Negative values are clamped to 0 (positive
    niceness needs no privilege; NeXa must never require ``sudo`` here)."""
    raw = os.environ.get("NEXA_PIPER_NICE")
    if raw is not None and raw.strip().lstrip("-").isdigit():
        return max(0, int(raw))
    return DEFAULT_PIPER_NICE


def data_dir() -> Path:
    """The NeXa TTS data directory — outside this git repo, never committed.

    Override with ``NEXA_TTS_DATA_DIR``; otherwise the standard XDG data
    directory (``$XDG_DATA_HOME/nexa/tts``, falling back to
    ``~/.local/share/nexa/tts``) — same convention as `nexa.stt.config`.
    """
    override = os.environ.get("NEXA_TTS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "nexa" / "tts"


def piper_venv_dir() -> Path:
    """The external Python virtual environment `piper-tts[http]` is
    installed into — deliberately NOT NeXa's own `.venv`, so the
    GPL-3.0-licensed `piper-tts` package is never imported into NeXa's own
    process (R0010; ADR-0003 D6)."""
    return data_dir() / "piper-http-venv"


def piper_venv_python() -> Path:
    return piper_venv_dir() / "bin" / "python3"


def voices_dir() -> Path:
    return data_dir() / "voices"


@dataclass(frozen=True, slots=True)
class PiperHttpConfig:
    """Explicit, typed configuration for the external Piper HTTP server and
    NeXa's client of it."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    en_voice: str = EN_VOICE
    pl_voice: str = PL_VOICE
    startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    #: POSIX nice value the external Piper process is started at (M2.4B.3.1).
    #: See ``DEFAULT_PIPER_NICE`` / ``default_piper_nice()``. An explicit
    #: value passed here wins over the env var; negatives are clamped to 0
    #: in ``__post_init__`` so Piper can never need privilege or fail to
    #: start over this.
    nice: int = field(default_factory=default_piper_nice)
    venv_python: Path | None = None
    voices_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.venv_python is None:
            object.__setattr__(self, "venv_python", piper_venv_python())
        if self.voices_dir is None:
            object.__setattr__(self, "voices_dir", voices_dir())
        if self.nice < 0:
            object.__setattr__(self, "nice", 0)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def synthesize_url(self) -> str:
        # R0010: the real Piper HTTP server accepts POST /synthesize only —
        # POST / (bare base_url) returns 405. This is the fix: configure the
        # complete URL, never patch Pipecat.
        return f"{self.base_url}/synthesize"

    @property
    def info_url(self) -> str:
        return f"{self.base_url}/info"
