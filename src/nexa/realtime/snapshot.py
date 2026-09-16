"""``CloudContextSnapshot`` — a derived, bounded, privacy-filtered projection
of canonical NeXa conversation state, handed once to a realtime cloud
provider at session start (ADR-0004 Decision E).

It is the ONLY thing besides live mic audio that may ever cross to a cloud
provider. It is built fresh from ``ConversationSession`` — it never becomes
a second canonical history (ADR-0004 Decision A), and building one makes
**zero** network/cloud calls: this module has no access to memory,
persona, credentials, or device internals in the first place, so the
allow-list is enforced by construction, not by a runtime filter someone
could forget.

Amendment 1 §3 note: on the Gemini-3.1 provider, ``recent_turns`` is
delivered exactly once, at session start, via the installed
``GeminiLiveLLMService``'s own initial-history mechanism (see
``docs/research/m2_6_cloud_realtime_voice/m2_6b_gemini_startup_sequencing_source_audit_20260911.md``)
— this module only produces the bounded, rendered turns; it does not itself
talk to any provider.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..conversation.session import ConversationSession
from ..conversation.turn import ConversationTurn, Role
from ..core.privacy import CloudEligibility, filter_cloud_safe

#: ADR-0004 Decision E defaults — "~12 turns" + an explicit character budget
#: in the low thousands.
DEFAULT_SNAPSHOT_TURNS = 12
DEFAULT_SNAPSHOT_CHAR_BUDGET = 4_000

#: The minimal cloud role card (ADR-0004 Decision E) — NOT NeXa's persona /
#: identity (``configs/personas/nexa_persona_v1.json``). Deliberately short
#: and deliberately different text so the two can never be confused.
#:
#: M2.6B.4C (R0041) — the language-mirroring sentence is worded to match the
#: OPERATOR-CONFIRMED M2.6A spike's own instruction as closely as ADR-0004's
#: brevity requirement allows (``docs/research/m2_6_cloud_realtime_voice/
#: m2_6a_gemini_live_probe.py``'s ``SPIKE_SYSTEM_INSTRUCTION``: "Normally
#: answer in the language the user is currently speaking. If explicitly
#: asked to use Polish or English, follow that request."), after the
#: differential audit found this the one concrete, source-level wording
#: difference between the two: production's prior text ("Mirror the user's
#: language (Polish or English).") dropped the spike's explicit *per-turn*
#: framing ("currently speaking") and its explicit-override clause. This is
#: a wording refinement only — ADR-0004 Decision E illustrates, but does not
#: mandate verbatim, this sentence; ADR-0004 Decision F (Option A, native
#: mirroring) is unchanged. NOT a claim that this was a proven root cause of
#: the Attempt #1 EN->PL failures (unverifiable without a live call, which
#: this checkpoint does not make) — a low-risk alignment with the ONE prior
#: OPERATOR-CONFIRMED wording, made once, before the next live retest.
CLOUD_ROLE_CARD = (
    "You are the realtime voice provider for NeXa, a personal AI assistant. "
    "Speak naturally and concisely for a live spoken conversation, usually "
    "1-3 sentences for an ordinary question. Answer in the language the "
    "user is currently speaking, Polish or English; if the user explicitly "
    "asks you to switch, follow that request."
)


@dataclass(frozen=True, slots=True)
class SnapshotTurn:
    """One bounded, rendered turn inside a ``CloudContextSnapshot``.

    Deliberately its own type (not a ``ConversationTurn`` reference) so a
    snapshot can never be mistaken for, or accidentally mutate, canonical
    history.
    """

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class CloudContextSnapshot:
    """Frozen, provider-facing snapshot (ADR-0004 Decision E).

    MUST NOT (and structurally cannot, given how it is built) carry: the
    full lifetime history, the long-term memory store, NeXa's persona
    verbatim, credentials, device internals, telemetry, or past raw audio.
    """

    system_instruction: str
    language_preference: str | None
    recent_turns: tuple[SnapshotTurn, ...]
    policy_name: str
    active_provider_name: str
    #: ADR-0004 Amendment 2 — already-filtered (CLOUD_SAFE only) short
    #: context facts NeXa Core chose to surface for this session (e.g.
    #: "the operator is currently working on the NeXa voice pipeline").
    #: Never populated with anything the caller tagged LOCAL_ONLY or
    #: CLOUD_WITH_USER_APPROVAL -- see ``build_cloud_context_snapshot``'s
    #: own ``context_facts`` parameter, which is the only way this field
    #: is ever populated.
    context_facts: tuple[str, ...] = field(default_factory=tuple)


def _bounded_recent_turns(
    history: tuple[ConversationTurn, ...],
    *,
    max_turns: int,
    max_chars: int,
) -> tuple[SnapshotTurn, ...]:
    """Take up to ``max_turns`` most-recent turns, then trim from the
    OLDEST end (never truncate a single turn's own text) until at or under
    ``max_chars``. Deterministic: the same history + the same bounds always
    produce the same output."""
    tail = history[-max_turns:] if max_turns > 0 else ()
    rendered = [SnapshotTurn(role=t.role, content=t.content) for t in tail]
    total = sum(len(t.content) for t in rendered)
    while rendered and total > max_chars:
        dropped = rendered.pop(0)
        total -= len(dropped.content)
    return tuple(rendered)


def build_cloud_context_snapshot(
    session: ConversationSession,
    *,
    language_preference: str | None = None,
    policy_name: str = "",
    active_provider_name: str = "",
    max_turns: int = DEFAULT_SNAPSHOT_TURNS,
    max_chars: int = DEFAULT_SNAPSHOT_CHAR_BUDGET,
    context_facts: Iterable[tuple[str, CloudEligibility]] = (),
) -> CloudContextSnapshot:
    """Build one ``CloudContextSnapshot`` from canonical NeXa state.

    Pure and synchronous; makes no network or cloud call. Reads only
    ``session.history`` — never ``session.system_prompt`` (the real
    persona) and never anything from a future memory store, so the
    allow-list cannot be bypassed by an oversight here.

    ``context_facts`` (ADR-0004 Amendment 2) — optional short strings a
    future NeXa Core component (memory, project/task state, device
    awareness) wants this session to know about, each explicitly tagged
    with a :class:`~nexa.core.privacy.CloudEligibility`. Only
    ``CLOUD_SAFE``-tagged facts are ever included (enforced here via
    :func:`~nexa.core.privacy.filter_cloud_safe`, not by caller
    discipline) — never the raw memory store, never a whole file, never
    anything tagged ``LOCAL_ONLY``/``CLOUD_WITH_USER_APPROVAL``.
    """
    instruction = CLOUD_ROLE_CARD
    if language_preference:
        instruction += f" Current language preference: {language_preference}."
    safe_facts = filter_cloud_safe(context_facts)
    if safe_facts:
        instruction += " Known current context: " + " ".join(safe_facts)
    return CloudContextSnapshot(
        system_instruction=instruction,
        language_preference=language_preference,
        recent_turns=_bounded_recent_turns(
            session.history, max_turns=max_turns, max_chars=max_chars
        ),
        policy_name=policy_name,
        active_provider_name=active_provider_name,
        context_facts=safe_facts,
    )
