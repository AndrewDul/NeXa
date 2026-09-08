"""M2.4B.5 — library-level PL/EN language detection.

R0024 established that constraining whisper.cpp's language choice to
``argmax(p_pl, p_en)`` classified every monolingual corpus item correctly
(the raw detector never confused PL with EN; its only errors escaped to a
third language). The CLI exposes only the top-1 language + its probability,
not the per-language vector — so this module binds the **already-installed,
pinned** ``libwhisper.so`` (`v1.9.3`, same build tree as ``whisper-cli``)
via ``ctypes`` and reads ``p_pl`` / ``p_en`` directly from
``whisper_lang_auto_detect``.

Scope discipline: this is a *detection* boundary only. It never
transcribes — the actual decode stays the battle-tested
``WhisperCppTranscriber`` CLI path. No whisper.cpp fork, no vendored code,
no version change, no heavy dependency (``ctypes`` is stdlib).
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import struct
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import WhisperCppConfig, default_whisper_lib_path
from .errors import LanguageDetectionError, SttLibraryNotFoundError, SttModelNotFoundError


@dataclass(frozen=True, slots=True)
class LanguageDetectionResult:
    """Per-utterance language evidence — never collapsed into one string.

    * ``p_pl`` / ``p_en`` — whisper.cpp's probabilities for exactly those
      two languages (0..1; the full 99-language vector sums to ~1, so these
      two rarely sum to 1).
    * ``raw_language`` — whisper.cpp's own top-1 pick over *all* languages
      (may be a third language like ``"ru"``/``"ko"``/``"he"`` — R0024).
    * ``raw_confidence`` — the probability of ``raw_language``.
    """

    p_pl: float
    p_en: float
    raw_language: str
    raw_confidence: float

    @property
    def constrained_language(self) -> str:
        """``argmax(p_pl, p_en)`` — the PL/EN-only decision (R0024)."""
        return "pl" if self.p_pl >= self.p_en else "en"

    @property
    def constrained_confidence(self) -> float:
        return max(self.p_pl, self.p_en)

    @property
    def pl_en_ratio(self) -> float:
        """max(p_pl, p_en) / min(p_pl, p_en) — how decisively one of the two
        target languages beats the other."""
        lo = min(self.p_pl, self.p_en)
        return self.constrained_confidence / lo if lo > 1e-9 else float("inf")


class LanguageDetector(Protocol):
    async def detect(self, audio: bytes) -> LanguageDetectionResult: ...


@contextmanager
def _suppressed_stderr():
    """whisper.cpp's model load is chatty on fd 2 regardless of the log
    callback; silence it just for the load."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


_LOG_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


def _noop_log(_level, _text, _user) -> None:  # pragma: no cover - trivial
    pass


class WhisperCppLanguageDetector:
    """ctypes binding to the pinned ``libwhisper.so`` — LID only.

    Loads the model once (context stays resident, ~130 MB, same as the CLI
    would per call) and answers ``detect(audio)`` by
    ``whisper_pcm_to_mel`` + ``whisper_lang_auto_detect``. One shared
    context guarded by a lock — the STT queue already serialises calls, the
    lock is defence in depth.
    """

    def __init__(self, config: WhisperCppConfig | None = None) -> None:
        self.config = config or WhisperCppConfig()
        lib_path = _resolve_lib_path()
        if lib_path is None:
            raise SttLibraryNotFoundError(
                f"libwhisper.so not found next to {default_whisper_lib_path().parent} — "
                f"run: python3 scripts/setup_whisper_cpp.py"
            )
        model_path = self.config.model_path
        if not Path(model_path).is_file():
            raise SttModelNotFoundError(
                f"whisper.cpp model not found at {model_path}. "
                f"Run: python3 scripts/setup_whisper_cpp.py"
            )

        self._lib = ctypes.CDLL(str(lib_path))
        self._bind()
        # keep the callback object alive for the process lifetime
        self._log_cb = _LOG_CB_TYPE(_noop_log)
        self._lib.whisper_log_set(self._log_cb, None)

        with _suppressed_stderr():
            self._ctx = self._lib.whisper_init_from_file(str(model_path).encode("utf-8"))
        if not self._ctx:
            raise LanguageDetectionError(
                f"whisper_init_from_file returned NULL for {model_path}"
            )
        self._n_langs = self._lib.whisper_lang_max_id() + 1
        self._pl_id = self._lib.whisper_lang_id(b"pl")
        self._en_id = self._lib.whisper_lang_id(b"en")
        if self._pl_id < 0 or self._en_id < 0:
            raise LanguageDetectionError("whisper.cpp does not know language id 'pl'/'en'")
        self._lock = threading.Lock()
        self._closed = False

    def _bind(self) -> None:
        L = self._lib
        L.whisper_log_set.argtypes = [_LOG_CB_TYPE, ctypes.c_void_p]
        L.whisper_log_set.restype = None
        L.whisper_init_from_file.argtypes = [ctypes.c_char_p]
        L.whisper_init_from_file.restype = ctypes.c_void_p
        L.whisper_free.argtypes = [ctypes.c_void_p]
        L.whisper_free.restype = None
        L.whisper_lang_max_id.argtypes = []
        L.whisper_lang_max_id.restype = ctypes.c_int
        L.whisper_lang_id.argtypes = [ctypes.c_char_p]
        L.whisper_lang_id.restype = ctypes.c_int
        L.whisper_lang_str.argtypes = [ctypes.c_int]
        L.whisper_lang_str.restype = ctypes.c_char_p
        L.whisper_pcm_to_mel.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int
        ]
        L.whisper_pcm_to_mel.restype = ctypes.c_int
        L.whisper_lang_auto_detect.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_float)
        ]
        L.whisper_lang_auto_detect.restype = ctypes.c_int

    async def detect(self, audio: bytes) -> LanguageDetectionResult:
        if self._closed:
            raise LanguageDetectionError("language detector is closed")
        if not audio:
            raise LanguageDetectionError("empty audio — nothing to detect")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._detect_sync, audio)

    def _detect_sync(self, audio: bytes) -> LanguageDetectionResult:
        n = len(audio) // 2
        ints = struct.unpack(f"<{n}h", audio[: n * 2])
        samples = (ctypes.c_float * n)(*(x / 32768.0 for x in ints))
        threads = int(self.config.threads)
        with self._lock:
            rc = self._lib.whisper_pcm_to_mel(self._ctx, samples, n, threads)
            if rc != 0:
                raise LanguageDetectionError(f"whisper_pcm_to_mel returned {rc}")
            probs = (ctypes.c_float * self._n_langs)()
            top = self._lib.whisper_lang_auto_detect(self._ctx, 0, threads, probs)
            if top < 0:
                raise LanguageDetectionError(f"whisper_lang_auto_detect returned {top}")
            raw = self._lib.whisper_lang_str(top)
            p_pl = float(probs[self._pl_id])
            p_en = float(probs[self._en_id])
            raw_conf = float(probs[top])
        return LanguageDetectionResult(
            p_pl=p_pl,
            p_en=p_en,
            raw_language=raw.decode("utf-8") if raw else "unknown",
            raw_confidence=raw_conf,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if getattr(self, "_ctx", None):
            self._lib.whisper_free(self._ctx)
            self._ctx = None

    def __del__(self) -> None:  # pragma: no cover - best-effort
        try:
            self.close()
        except Exception:
            pass


def _resolve_lib_path() -> Path | None:
    """`libwhisper.so`, `.so.1`, or the concrete `.so.1.9.3` — whichever the
    build produced next to `whisper-cli`."""
    base = default_whisper_lib_path()
    for cand in (base, base.with_suffix(".so.1"), *sorted(base.parent.glob("libwhisper.so.*"))):
        if cand.is_file():
            return cand
    return None
