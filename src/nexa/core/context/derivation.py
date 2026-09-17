"""Derives a :class:`~nexa.core.context.models.ContextRequest` from a real
conversation turn (R0077) — so a caller never has to know internal Memory
namespaces (e.g. ``domain_hint="projects.nexa"``) by hand.

V1 subject-hint derivation is deliberately simple: deterministic lexical
tokenization + a small, general (not domain/business-specific) PL+EN
stopword filter (R0079 / R0078 Revision 2 §16 -- NeXa is canonically
bilingual). It is NOT semantic search — a hint only helps
:class:`~nexa.core.context.memory_retriever.MemoryRetriever` find a
namespace whose name/summary literally contains that word (R0075 §7's
"topic discovery" honesty applies unchanged here). No model call, no
network, no language detection (both stopword sets are applied
unconditionally, so a bilingual utterance is filtered correctly regardless
of which language dominates), no hardcoded namespace mapping (e.g. never
``if "python" in text: domain_hint = "teacher.python"``) — the generic
descriptor matching already built for M3.3 does the actual work.
"""

from __future__ import annotations

import re
from datetime import datetime

from ...conversation.session import ConversationSession
from .models import ContextBudget, ContextRequest, TemporalIntent

#: A small, general set of common English function words -- NOT a
#: business/domain-specific list. Purely linguistic filtering.
_STOPWORDS_EN = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
        "my", "your", "his", "its", "our", "their",
        "this", "that", "these", "those",
        "what", "which", "who", "whom", "whose", "why", "how", "when", "where",
        "do", "does", "did", "doing", "done",
        "have", "has", "had", "having",
        "will", "would", "shall", "should", "can", "could", "may", "might", "must",
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "about", "as",
        "and", "or", "but", "if", "so", "not", "no",
    }
)

#: R0079 (R0078 Revision 2 §16): a small, general set of common Polish
#: function words -- pronouns, articles-equivalents, conjunctions, common
#: prepositions/copulas -- the SAME kind of purely-linguistic list as
#: ``_STOPWORDS_EN``, never a domain/business-specific term (no
#: Teacher/LiFeOS/Projects vocabulary). Applied unconditionally alongside
#: the English set (never conditionally, never via language detection) so
#: a bilingual PL/EN utterance is filtered correctly regardless of which
#: language dominates.
_STOPWORDS_PL = frozenset(
    {
        "i", "w", "we", "na", "do", "z", "ze", "się", "jest", "są", "to", "że",
        "czy", "o", "jak", "ale", "dla", "po", "od", "ten", "ta", "te", "był",
        "była", "było", "byli", "być", "nie", "tak", "co", "kto", "gdzie",
        "kiedy", "dlaczego", "jaki", "jaka", "jakie", "który", "która", "które",
        "moje", "moja", "mój", "twoje", "twoja", "twój", "nasz", "nasza", "nasze",
        "ich", "jego", "jej", "mnie", "mi", "cię", "ci", "go", "ją", "je", "im",
        "a", "albo", "lub", "oraz", "bo", "gdy", "aby", "żeby", "przez", "pod",
        "nad", "przy", "bez", "między", "już", "jeszcze", "też", "także",
    }
)

#: The union both languages are checked against -- see the module docstring
#: and ``derive_subject_hints()`` for why this is unconditional, not
#: language-detected.
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_PL

_MIN_TOKEN_LENGTH = 3
_MAX_HINTS = 10

_WORD_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)


def derive_subject_hints(text: str) -> tuple[str, ...]:
    """Deterministic, bounded, provider-neutral lexical hints from ``text``.

    ``"What should I learn next in Python?"`` -> ``("learn", "next", "python")``
    -- function words ("what"/"should"/"i"/"in") are dropped by the general
    stopword rule, not by any topic-specific logic.
    """
    normalized = text.lower()
    cleaned = _WORD_PATTERN.sub(" ", normalized)
    tokens = cleaned.split()

    seen: set[str] = set()
    hints: list[str] = []
    for token in tokens:
        if len(token) < _MIN_TOKEN_LENGTH:
            continue
        if token in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        hints.append(token)
        if len(hints) >= _MAX_HINTS:
            break
    return tuple(hints)


def derive_context_request(
    session: ConversationSession,
    *,
    domain_hint: str | None = None,
    temporal_intent: TemporalIntent = TemporalIntent.CURRENT,
    historical_at: datetime | None = None,
    budget: ContextBudget | None = None,
) -> ContextRequest:
    """Builds a :class:`ContextRequest` from ``session``'s current turn.

    Precondition (matches :meth:`~nexa.core.context.engine.ContextEngine.build_context`'s
    own, unchanged): ``session.history`` is non-empty and its last entry is
    the current USER turn — callers invoke this AFTER the turn has been
    appended, never before.

    ``domain_hint`` stays ``None`` unless the caller already has an
    explicit, trustworthy scope (R0077 §4) — no Goals/Projects/UserModel
    state is invented here to supply one.
    """
    current_turn = session.history[-1]
    subject_hints = derive_subject_hints(current_turn.content)
    return ContextRequest(
        session=session,
        temporal_intent=temporal_intent,
        historical_at=historical_at,
        domain_hint=domain_hint,
        subject_hints=subject_hints,
        budget=budget,
    )
