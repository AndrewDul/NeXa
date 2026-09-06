"""M2.2 STT configuration: the pinned whisper.cpp runtime location, the
explicit PL/EN language type, and the transcriber's typed settings.

Canonical source of truth for where NeXa expects the whisper.cpp binary and
model to live — `scripts/setup_whisper_cpp.py` imports these same functions
rather than duplicating the path logic (scripts depend on `src/nexa`, never
the other way around, per `AGENTS.md`/`scripts/README.md`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Pinned exactly — verified against the GitHub API (release + tags) on
# 2026-09-05, not assumed. `b4938` (build-number tag) and `v1.9.3` (semver
# tag) point to the identical commit, the latest stable release at the time.
# Never build against `main`.
WHISPER_CPP_TAG = "v1.9.3"
WHISPER_CPP_COMMIT = "371b5a7561823ab2bb32142d2751e35e7534727b"
WHISPER_CPP_REPO = "https://github.com/ggml-org/whisper.cpp.git"

# ADR-0003 D4 / R0006's measured baseline on this Pi: base/q8_0 matched
# base/fp16's accuracy (0.354 avg WER) at ~23% lower latency (~1.69s) and
# ~22% lower RSS (~221 MB) — beat legacy's faster-whisper base on both
# accuracy and speed at a matched beam size on the same 12-file material.
MODEL_NAME = "base"
MODEL_QUANT = "q8_0"

# R0006's winning STT configuration used 4 threads on this 4-core Pi.
DEFAULT_THREADS = 4
DEFAULT_TIMEOUT_S = 30.0


class Language(StrEnum):
    """Explicit STT language hint. No `AUTO` member exists on purpose —
    R0006 measured auto-detection misclassifying a short Polish utterance as
    Japanese, independent of STT engine (reproduced again in M2.1's own
    evidence). The normal M2.2 path must always pass one of these."""

    PL = "pl"
    EN = "en"


def data_dir() -> Path:
    """The NeXa STT data directory — outside this git repo, never committed.

    Override with ``NEXA_STT_DATA_DIR``; otherwise the standard XDG data
    directory (``$XDG_DATA_HOME/nexa/stt``, falling back to
    ``~/.local/share/nexa/stt``).
    """
    override = os.environ.get("NEXA_STT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "nexa" / "stt"


def whisper_cpp_dir() -> Path:
    return data_dir() / f"whisper.cpp-{WHISPER_CPP_TAG}"


def default_whisper_cli_path() -> Path:
    return whisper_cpp_dir() / "build" / "bin" / "whisper-cli"


def default_model_path(quant: str = MODEL_QUANT) -> Path:
    return data_dir() / "models" / f"ggml-{MODEL_NAME}-{quant}.bin"


@dataclass(frozen=True, slots=True)
class WhisperCppConfig:
    """Explicit, typed configuration for `WhisperCppTranscriber`.

    `binary_path`/`model_path` default to the paths
    `scripts/setup_whisper_cpp.py` produces, resolved lazily (not at class
    definition time) so `NEXA_STT_DATA_DIR` overrides are honored even if
    set after import.
    """

    binary_path: Path | None = None
    model_path: Path | None = None
    threads: int = DEFAULT_THREADS
    timeout_s: float = DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        if self.binary_path is None:
            object.__setattr__(self, "binary_path", default_whisper_cli_path())
        if self.model_path is None:
            object.__setattr__(self, "model_path", default_model_path())
