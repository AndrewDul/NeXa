"""M2.4B.5 — ``BilingualSpeechTranscriber``: automatic PL/EN voice input.

Implements the R0024 architecture behind the existing
``SpeechTranscriber`` boundary (ADR-0003 D11), so ``nexa.voice`` /
``VoiceRuntime`` need no change to gain automatic per-utterance PL↔EN:

    audio
      → library-level LID (``WhisperCppLanguageDetector`` — p_pl, p_en, raw)
      → ``LanguageIdGuard`` → AUTO_ACCEPT | FALLBACK_REDECODE, selected PL/EN
      → ONE explicit decode (the pinned ``WhisperCppTranscriber`` CLI) in
        the selected language
      → ``TranscriptionResult`` (transcript + resolved input language +
        full ``LanguageDecision`` telemetry)

There is never a "wrong-language auto transcript" to reject: the language
is chosen *before* the single decode. The guard's FALLBACK path is exactly
the task's "re-decode the original audio in the fallback PL/EN language" —
here it is simply *which* language that one decode uses. A rejected /
third-language label never reaches ``ConversationSession``.

``last_input_language`` is the minimum session-scoped input-language state
(``pl`` | ``en`` | ``None``); it is not a conversation turn and not
long-term memory. It updates only on AUTO_ACCEPT.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import Language
from .language_detection import LanguageDetectionResult, LanguageDetector
from .language_guard import GuardDecision, GuardOutcome, LanguageIdGuard
from .transcriber import SpeechTranscriber, TranscriptionResult

#: M2.4B.5 product status of true within-utterance code-switch. R0024 showed
#: `-l auto` was 6/10 USABLE, 0 BROKEN on real mixed utterances — tolerable
#: as a side-effect, NOT solved. This stage does not add dual-decode merging
#: and does not claim code-switch support; it only guarantees it is *not
#: worse* than the R0024 single-language-per-utterance baseline.
MIXED_CODE_SWITCH_STATUS = "best-effort / deferred (not guaranteed)"


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    """Full per-utterance language telemetry (never collapsed to a string).
    Attached to ``TranscriptionResult.language_decision``."""

    # detection
    raw_detected_language: str
    raw_confidence: float
    p_pl: float
    p_en: float
    # guard
    guard_decision: GuardDecision
    selected_language: str  # == InputSpeechLanguage == STTDecodeLanguage
    guard_reason: str
    confidence_threshold: float
    # fallback / re-decode
    redecoded: bool  # True iff guard chose FALLBACK_REDECODE
    fallback_reason: str | None
    last_input_language_before: str | None
    last_input_language_after: str | None
    # latency
    detect_latency_s: float
    decode_latency_s: float

    @property
    def total_latency_s(self) -> float:
        return round(self.detect_latency_s + self.decode_latency_s, 3)


class BilingualSpeechTranscriber:
    """A ``SpeechTranscriber`` that ignores the caller's fixed ``language``
    hint (treating it only as the turn-1 bootstrap) and instead resolves
    PL/EN per utterance via LID + guard + one explicit decode."""

    def __init__(
        self,
        base_transcriber: SpeechTranscriber,
        detector: LanguageDetector,
        *,
        guard: LanguageIdGuard | None = None,
        session_default_language: Language = Language.EN,
        on_decision: Callable[[LanguageDecision], None] | None = None,
    ) -> None:
        self._base = base_transcriber
        self._detector = detector
        self._guard = guard or LanguageIdGuard()
        self._bootstrap = Language(session_default_language)
        self._on_decision = on_decision
        self._last_input_language: str | None = None

    @property
    def last_input_language(self) -> str | None:
        """Transient session-scoped input-language state — ``pl`` | ``en`` |
        ``None`` (turn 1, or only ambiguous turns so far)."""
        return self._last_input_language

    def reset_language_state(self) -> None:
        self._last_input_language = None

    async def transcribe(
        self, audio: bytes, *, language: Language | None = None
    ) -> TranscriptionResult:
        # `language`, when given, overrides the constructor bootstrap for
        # this session's turn-1 fallback only. It never forces the decode.
        bootstrap = Language(language) if language is not None else self._bootstrap

        t0 = time.perf_counter()
        detection: LanguageDetectionResult = await self._detector.detect(audio)
        detect_latency_s = time.perf_counter() - t0

        audio_duration_s = len(audio) / (16_000 * 2)
        before = self._last_input_language
        outcome: GuardOutcome = self._guard.evaluate(
            detection,
            audio_duration_s=audio_duration_s,
            last_input_language=before,
            bootstrap_language=bootstrap.value,
        )
        selected = outcome.selected_language
        if selected not in ("pl", "en"):  # defensive; guard never returns other
            selected = bootstrap.value

        t1 = time.perf_counter()
        base_result = await self._base.transcribe(audio, language=Language(selected))
        decode_latency_s = time.perf_counter() - t1

        if outcome.updates_last_input_language:
            self._last_input_language = selected
        after = self._last_input_language

        redecoded = outcome.decision == GuardDecision.FALLBACK_REDECODE
        decision = LanguageDecision(
            raw_detected_language=detection.raw_language,
            raw_confidence=round(detection.raw_confidence, 4),
            p_pl=round(detection.p_pl, 4),
            p_en=round(detection.p_en, 4),
            guard_decision=outcome.decision,
            selected_language=selected,
            guard_reason=outcome.reason,
            confidence_threshold=outcome.threshold,
            redecoded=redecoded,
            fallback_reason=outcome.reason if redecoded else None,
            last_input_language_before=before,
            last_input_language_after=after,
            detect_latency_s=round(detect_latency_s, 3),
            decode_latency_s=round(decode_latency_s, 3),
        )
        if self._on_decision is not None:
            self._on_decision(decision)

        return TranscriptionResult(
            text=base_result.text,
            language=Language(selected),
            audio_duration_s=base_result.audio_duration_s,
            wall_latency_s=round(detect_latency_s + decode_latency_s, 3),
            language_decision=decision,
        )
