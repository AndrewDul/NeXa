"""M2.4B.4 — bilingual voice-input evaluation corpus (ground truth).

RESEARCH TOOLING ONLY (deletable with the rest of `docs/research/`). Imports
nothing from `nexa`, mutates no production state, and is never imported by
`src/nexa` or `apps/`.

The corpus is the fixed list of utterances the operator records once (see
`capture_corpus.py`) as 16 kHz mono PCM16 WAV fixtures under
`fixtures/audio/`. Every entry carries:

* ``uid``   — stable id (``<NNN>_<group>_<slug>``), also the WAV basename stem
* ``group`` — ``pl`` | ``en`` | ``short`` | ``mixed``
* ``expected_language`` — ``pl`` | ``en`` | ``ambiguous`` | ``mixed`` — the
  ground-truth label for language-ID scoring. ``ambiguous`` and ``mixed``
  are NOT scored as a single "correct" language.
* ``leans`` — ``short`` group only: ``pl`` | ``en`` | ``none`` — a soft hint
  for analysis, never a hard ground truth.
* ``primary`` — ``mixed`` group only: ``pl`` | ``en`` — the matrix
  (dominant-grammar) language, for "primary language" analysis only.
* ``text`` — the reference transcript (with punctuation / casing).
  Punctuation / casing differences are class S0, NOT semantic failures.
* ``acceptable`` — extra fully-acceptable transcript strings.

Counts (asserted in tests): pl 15, en 15, short 10, mixed 10 — 50 total.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GROUPS: tuple[str, ...] = ("pl", "en", "short", "mixed")
LANGUAGE_LABELS: tuple[str, ...] = ("pl", "en", "ambiguous", "mixed")

# Polish diacritics -> ASCII, for filename slugs only (never for scoring).
_ASCII_FOLD = str.maketrans(
    {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o",
        "ś": "s", "ź": "z", "ż": "z",
        "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n", "Ó": "o",
        "Ś": "s", "Ź": "z", "Ż": "z",
    }
)


@dataclass(frozen=True, slots=True)
class CorpusItem:
    uid: str
    group: str
    expected_language: str
    text: str
    leans: str = "none"
    primary: str = ""
    acceptable: tuple[str, ...] = field(default_factory=tuple)


def _slug(text: str) -> str:
    folded = text.translate(_ASCII_FOLD).lower()
    out: list[str] = []
    for ch in folded:
        if ch.isalnum() and ch.isascii():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:40] or "utt"


_PL: tuple[str, ...] = (
    "Co to jest czarna dziura?",
    "Jak ona powstaje?",
    "Z czego składa się gwiazda?",
    "Po co człowiekowi sen?",
    "Jaki jest rzeczywisty kolor Słońca?",
    "Co jest cięższe, kilogram żelaza czy kilogram piór?",
    "Powiedz mi to trochę prościej.",
    "Nie rozumiem, wyjaśnij jeszcze raz.",
    "Zapamiętaj, że kupiłem mąkę pszenną i żytnią.",
    "Która będzie godzina za dziewięćdziesiąt pięć minut?",
    "Co dzisiaj możemy zrobić?",
    "Powiedz mi coś ciekawego.",
    "Jak działa komputer?",
    "Dlaczego niebo jest niebieskie?",
    "Wróćmy do poprzedniego tematu.",
)

_EN: tuple[str, ...] = (
    "What is a black hole?",
    "How does it form?",
    "What is a star made of?",
    "Why do humans need sleep?",
    "What is the actual colour of the Sun?",
    "What is heavier, one kilogram of iron or one kilogram of feathers?",
    "Explain it more simply.",
    "I still don't understand, explain it again.",
    "Remember that I bought bread flour and rye flour.",
    "What time will it be in ninety-five minutes?",
    "What can we do today?",
    "Tell me something interesting.",
    "How does a computer work?",
    "Why is the sky blue?",
    "Let's go back to the previous topic.",
)

# (text, leans) — `leans` is analysis-only.
_SHORT: tuple[tuple[str, str], ...] = (
    ("Tak.", "pl"),
    ("Nie.", "pl"),
    ("Dobra.", "pl"),
    ("Okej.", "pl"),
    ("Super.", "none"),
    ("Yeah.", "en"),
    ("No.", "none"),
    ("Right.", "en"),
    ("Okay.", "none"),
    ("Sure.", "en"),
)

# (text, primary) — `primary` = matrix / dominant-grammar language.
_MIXED: tuple[tuple[str, str], ...] = (
    ("Dobra, tell me more.", "pl"),
    ("Okay, wróćmy do polskiego.", "en"),
    ("Powiedz mi what a black hole is.", "pl"),
    ("Can you wyjaśnić to prościej?", "en"),
    ("No dobra, let's continue.", "pl"),
    ("Tell me więcej o gwiazdach.", "en"),
    ("Okay, ale dlaczego?", "en"),
    ("Let's wrócić do tego.", "en"),
    ("Explain mi to jeszcze raz.", "en"),
    ("Super, now powiedz to po polsku.", "en"),
)

# Casing/spelling variants that must NOT count against transcript accuracy.
_ACCEPTABLE: dict[str, tuple[str, ...]] = {
    "colour": ("color",),
}


def _acc_for(text: str) -> tuple[str, ...]:
    variants: list[str] = []
    if "colour" in text:
        variants.append(text.replace("colour", "color"))
    if "don't" in text:
        variants.append(text.replace("don't", "dont"))
    return tuple(variants)


def build_corpus() -> tuple[CorpusItem, ...]:
    items: list[CorpusItem] = []
    n = 0
    for text in _PL:
        n += 1
        items.append(CorpusItem(f"{n:03d}_pl_{_slug(text)}", "pl", "pl", text,
                                acceptable=_acc_for(text)))
    for text in _EN:
        n += 1
        items.append(CorpusItem(f"{n:03d}_en_{_slug(text)}", "en", "en", text,
                                acceptable=_acc_for(text)))
    for text, leans in _SHORT:
        n += 1
        items.append(CorpusItem(f"{n:03d}_short_{_slug(text)}", "short",
                                "ambiguous", text, leans=leans))
    for text, primary in _MIXED:
        n += 1
        items.append(CorpusItem(f"{n:03d}_mixed_{_slug(text)}", "mixed",
                                "mixed", text, primary=primary))
    return tuple(items)


CORPUS: tuple[CorpusItem, ...] = build_corpus()


def as_records() -> list[dict]:
    """Plain-dict view (for JSON manifests / the capture tool)."""
    return [
        {
            "uid": it.uid,
            "group": it.group,
            "expected_language": it.expected_language,
            "text": it.text,
            "leans": it.leans,
            "primary": it.primary,
            "acceptable": list(it.acceptable),
        }
        for it in CORPUS
    ]


if __name__ == "__main__":
    import json

    print(json.dumps(as_records(), ensure_ascii=False, indent=1))
