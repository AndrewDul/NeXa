"""M2.4B.5 — the one ``LanguageIdGuard`` authority.

Given a :class:`LanguageDetectionResult` and the session's
``last_input_language``, decide:

* **AUTO_ACCEPT** — trust the detector's PL/EN judgement; the STT decode
  language is the detector's constrained ``argmax(p_pl, p_en)`` (or its raw
  language when that is already ``pl``/``en``). ``last_input_language`` is
  then updated.
* **FALLBACK_REDECODE** — do *not* trust the detector for this utterance;
  the STT decode language is ``last_input_language`` (or the documented
  ``bootstrap_language`` when the session has none yet).
  ``last_input_language`` is left unchanged — nothing was learned.

Evidence-backed rules (all thresholds are **R0024 initial calibration on
the operator corpus**, not universal truth — every one is configurable and
every decision is telemetered so later operator evidence can tune them):

1. Utterances shorter than ``min_reliable_duration_s`` (2.0 s) → always
   FALLBACK. R0024: on this corpus every monolingual sentence was ≥ 2.37 s
   and every one-word/ambiguous utterance was ≤ 1.73 s; sub-2 s LID is
   acoustically unreliable ("Tak." → ``en`` "Talk.").
2. ``raw_language ∈ {pl, en}`` → AUTO_ACCEPT (selected = raw). R0024:
   **0 PL↔EN confusion** in 30 monolingual utterances, even at raw
   probability as low as 0.27 — so a supported raw language is trusted
   without a confidence floor. (Toggle: ``trust_supported_raw_language``.)
3. ``raw_language`` is a *third* language but ``max(p_pl, p_en) ≥
   confidence_threshold`` (0.60) → AUTO_ACCEPT (selected = constrained).
4. ``raw_language`` is a third language, ``max(p_pl,p_en)`` is below the
   threshold, **but** one target language decisively beats the other
   (``pl_en_ratio ≥ decisive_ratio`` and ``max(p_pl,p_en) ≥
   decisive_floor``) → AUTO_ACCEPT (selected = constrained). R0024: this
   recovers the ``pl→ru`` / ``en→ko`` / ``en→he`` monolingual misses,
   whose PL/EN split was ~20–40×.
5. otherwise → FALLBACK_REDECODE.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .language_detection import LanguageDetectionResult

#: R0024 initial calibration — the confidence figure R0024 found separation
#: around. NOT a universal constant; tune from telemetry.
DEFAULT_CONFIDENCE_THRESHOLD = 0.60
#: R0024: monolingual ≥ 2.37 s, ambiguous shorts ≤ 1.73 s on the corpus.
DEFAULT_MIN_RELIABLE_DURATION_S = 2.0
DEFAULT_DECISIVE_RATIO = 4.0
DEFAULT_DECISIVE_FLOOR = 0.15


class GuardDecision(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    FALLBACK_REDECODE = "fallback_redecode"


@dataclass(frozen=True, slots=True)
class GuardConfig:
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    min_reliable_duration_s: float = DEFAULT_MIN_RELIABLE_DURATION_S
    decisive_ratio: float = DEFAULT_DECISIVE_RATIO
    decisive_floor: float = DEFAULT_DECISIVE_FLOOR
    trust_supported_raw_language: bool = True
    #: Used only when FALLBACK fires and the session has no
    #: ``last_input_language`` yet (turn 1). Deterministic, documented — not
    #: a guess. Callers should pass the session's configured default
    #: language here (e.g. the ``--language`` the operator chose).
    bootstrap_language: str = "en"


@dataclass(frozen=True, slots=True)
class GuardOutcome:
    decision: GuardDecision
    selected_language: str  # "pl" | "en" — the STT decode language
    reason: str
    threshold: float
    #: True when the guard trusted the detector and the caller should set
    #: ``last_input_language = selected_language`` afterwards.
    updates_last_input_language: bool


class LanguageIdGuard:
    """One authority. Pure/deterministic; no I/O, no whisper.cpp."""

    def __init__(self, config: GuardConfig | None = None) -> None:
        self.config = config or GuardConfig()

    def evaluate(
        self,
        detection: LanguageDetectionResult,
        *,
        audio_duration_s: float,
        last_input_language: str | None,
        bootstrap_language: str | None = None,
    ) -> GuardOutcome:
        c = self.config
        cl = detection.constrained_language
        cc = detection.constrained_confidence
        bootstrap = bootstrap_language or c.bootstrap_language
        fallback_lang = last_input_language or bootstrap

        def fallback(reason: str) -> GuardOutcome:
            return GuardOutcome(
                decision=GuardDecision.FALLBACK_REDECODE,
                selected_language=fallback_lang,
                reason=f"{reason}; decode in {fallback_lang} "
                f"({'last input language' if last_input_language else 'bootstrap'})",
                threshold=c.confidence_threshold,
                updates_last_input_language=False,
            )

        def accept(lang: str, reason: str) -> GuardOutcome:
            return GuardOutcome(
                decision=GuardDecision.AUTO_ACCEPT,
                selected_language=lang,
                reason=reason,
                threshold=c.confidence_threshold,
                updates_last_input_language=True,
            )

        if audio_duration_s < c.min_reliable_duration_s:
            return fallback(
                f"utterance {audio_duration_s:.2f}s < {c.min_reliable_duration_s:.2f}s "
                f"reliable-LID minimum (R0024: shorts are acoustically ambiguous)"
            )

        if c.trust_supported_raw_language and detection.raw_language in ("pl", "en"):
            return accept(
                detection.raw_language,
                f"raw language '{detection.raw_language}' is a supported language "
                f"(R0024: 0 PL↔EN confusion); p_pl={detection.p_pl:.3f} p_en={detection.p_en:.3f}",
            )

        if cc >= c.confidence_threshold:
            return accept(
                cl,
                f"raw '{detection.raw_language}' unsupported, but constrained "
                f"max(p_pl,p_en)={cc:.3f} ≥ {c.confidence_threshold:.2f} → {cl}",
            )

        if detection.pl_en_ratio >= c.decisive_ratio and cc >= c.decisive_floor:
            return accept(
                cl,
                f"raw '{detection.raw_language}' unsupported and low confidence "
                f"({cc:.3f}), but PL/EN split is decisive "
                f"(ratio {detection.pl_en_ratio:.1f}× ≥ {c.decisive_ratio:.0f}) → {cl}",
            )

        return fallback(
            f"raw '{detection.raw_language}' unsupported, constrained confidence "
            f"{cc:.3f} < {c.confidence_threshold:.2f} and PL/EN split not decisive "
            f"(ratio {detection.pl_en_ratio:.1f}×)"
        )
