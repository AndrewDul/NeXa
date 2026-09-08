"""The M2.2 STT boundary (ADR-0003 D11): `SpeechTranscriber` + the first
(only) implementation, `WhisperCppTranscriber`, wrapping the pinned
whisper.cpp CLI via subprocess.

NeXa's voice runtime never depends on whisper.cpp CLI details directly —
only on this module's small surface: `TranscriptionResult`,
`SpeechTranscriber`, `Language`, and the errors in `errors.py`.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .config import Language, WhisperCppConfig

if TYPE_CHECKING:
    from .bilingual import LanguageDecision
from .errors import (
    NoUsableAudioError,
    SttBinaryNotFoundError,
    SttMalformedOutputError,
    SttModelNotFoundError,
    SttSubprocessError,
    SttTimeoutError,
)

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2  # signed 16-bit PCM
CHANNELS = 1


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: Language
    audio_duration_s: float
    wall_latency_s: float
    # M2.4B.5: present only when produced by ``BilingualSpeechTranscriber``
    # — the full per-utterance language-decision telemetry (raw detected
    # language, p_pl/p_en, guard decision, whether an explicit re-decode
    # happened, latencies). ``None`` for the plain ``WhisperCppTranscriber``
    # (explicit-language path, ADR-0003 D5).
    language_decision: LanguageDecision | None = None


class SpeechTranscriber(Protocol):
    """The M2.2 STT boundary. `WhisperCppTranscriber` is the only
    implementation so far — this exists so `nexa.voice` depends on this
    shape, not on whisper.cpp CLI details, per ADR-0003 D11."""

    async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult: ...


def _write_wav(audio: bytes, path: Path) -> None:
    """Deterministically wrap raw 16kHz mono PCM16 bytes in a WAV container."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH_BYTES)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio)


class WhisperCppTranscriber:
    """Wraps the pinned whisper.cpp `whisper-cli` binary (subprocess, no
    shell=True). Owns: binary/model path validation, explicit language,
    thread count, subprocess lifecycle, timeout/error handling, JSON
    parsing, and wall-clock transcription timing."""

    def __init__(self, config: WhisperCppConfig | None = None) -> None:
        self.config = config or WhisperCppConfig()
        if not self.config.binary_path.is_file():
            raise SttBinaryNotFoundError(
                f"whisper-cli not found at {self.config.binary_path}. "
                f"Run: python3 scripts/setup_whisper_cpp.py"
            )
        if not self.config.model_path.is_file():
            raise SttModelNotFoundError(
                f"whisper.cpp model not found at {self.config.model_path}. "
                f"Run: python3 scripts/setup_whisper_cpp.py"
            )

    async def transcribe(self, audio: bytes, *, language: Language) -> TranscriptionResult:
        if not isinstance(language, Language):
            raise TypeError(f"language must be a nexa.stt.config.Language, got {language!r}")
        if not audio:
            raise NoUsableAudioError("utterance buffer was empty — nothing to transcribe")

        audio_duration_s = len(audio) / (SAMPLE_RATE * SAMPLE_WIDTH_BYTES * CHANNELS)

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        result = await loop.run_in_executor(None, self._run_sync, audio, language)
        wall_latency_s = time.perf_counter() - t0

        return TranscriptionResult(
            text=result,
            language=language,
            audio_duration_s=audio_duration_s,
            wall_latency_s=wall_latency_s,
        )

    def _run_sync(self, audio: bytes, language: Language) -> str:
        with tempfile.TemporaryDirectory(prefix="nexa-stt-") as tmpdir:
            wav_path = Path(tmpdir) / "utterance.wav"
            out_prefix = Path(tmpdir) / "utterance"
            _write_wav(audio, wav_path)

            cmd = [
                str(self.config.binary_path),
                "-m", str(self.config.model_path),
                "-f", str(wav_path),
                "-l", language.value,
                "-t", str(self.config.threads),
                "-oj",
                "-of", str(out_prefix),
                "-np",
                "-nt",
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=self.config.timeout_s
                )
            except subprocess.TimeoutExpired as exc:
                raise SttTimeoutError(
                    f"whisper-cli did not finish within {self.config.timeout_s}s"
                ) from exc

            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip()
                raise SttSubprocessError(f"whisper-cli exited {proc.returncode}: {detail}")

            json_path = out_prefix.with_suffix(".json")
            if not json_path.is_file():
                raise SttMalformedOutputError(
                    f"whisper-cli exited 0 but produced no JSON output at {json_path} "
                    f"(stderr: {proc.stderr.strip()!r})"
                )
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                segments = data["transcription"]
                text = "".join(seg["text"] for seg in segments).strip()
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise SttMalformedOutputError(
                    f"could not parse whisper-cli JSON output: {exc}"
                ) from exc

            return text
