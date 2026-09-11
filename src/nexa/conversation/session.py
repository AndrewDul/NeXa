"""``ConversationSession`` — the canonical authority for one text conversation.

The one path: accept user text → append user turn → build bounded context →
call the ``ModelProvider`` → stream assistant output → append the completed
assistant turn. No parallel "final answer" path, no MAS, no silent fallback
provider/model (AGENTS.md §3.2/§3.3, ADR-0002 D1).

On provider failure the exception propagates to the caller unchanged (fail
closed) and no assistant turn is recorded — the user's turn stays in history,
but nothing false is appended in its place.

Response-language mirroring (M2.3, R0009) is implemented in
``ConversationContext.to_provider_messages()``, not here — see that
module's docstring. ``session.history`` (the real transcript, ADR-0003 D2)
is completely unaffected: it stores exactly what the user said and what the
model replied, nothing else.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..providers.base import CancelToken, GenerationOptions, ModelProvider
from .context import DEFAULT_MAX_CHARS, DEFAULT_MAX_TURNS, ConversationContext
from .provider_window import ProviderWindow
from .response_mode import ResponseMode
from .streaming import StreamingResponse
from .turn import ConversationTurn, Role

logger = logging.getLogger(__name__)


class InterruptedTurnOutcome(StrEnum):
    """What ``commit_interrupted_turn`` did to history."""

    #: CASE A — the reply was cut before any assistant speech was released,
    #: so the trailing USER turn (and its response-language slot) was rolled
    #: back; the interrupting utterance will become the next clean user turn.
    ROLLED_BACK_USER_TURN = "rolled_back_user_turn"
    #: CASE B — assistant already spoke a prefix; that prefix was committed
    #: as an ASSISTANT turn with ``interrupted=True``.
    COMMITTED_SPOKEN_PREFIX = "committed_spoken_prefix"
    #: Nothing to do (history empty, or the last turn is already an
    #: assistant turn — the stream had finished normally).
    NOTHING_TO_COMMIT = "nothing_to_commit"


class ExternalExchangeOutcome(StrEnum):
    """What ``record_external_exchange`` did to history (ADR-0004 Decision
    A / M2.6B.2 — the canonical cloud-turn write path)."""

    #: both a user turn and a (possibly interrupted) assistant turn were
    #: appended.
    COMMITTED_EXCHANGE = "committed_exchange"
    #: only the user turn was appended — the provider/session was lost, or
    #: the reply was interrupted before any audio played. Unlike the local
    #: pre-generation rollback case (``ROLLED_BACK_USER_TURN``), the user
    #: turn is *kept*: an external provider actually heard and processed
    #: it.
    COMMITTED_USER_ONLY = "committed_user_only"


@dataclass
class ConversationSession:
    provider: ModelProvider
    system_prompt: str
    options: GenerationOptions = field(default_factory=GenerationOptions)
    max_turns: int = DEFAULT_MAX_TURNS
    max_chars: int = DEFAULT_MAX_CHARS
    #: M2.5B.2 — when set, the *provider-facing* context is this bounded,
    #: prefix-stable window over ``history`` instead of the
    #: ``ConversationContext`` turn/char bound. Canonical ``history`` is
    #: unchanged either way. ``None`` (default, and always for typed chat)
    #: keeps the pre-M2.5B.2 path byte-for-byte.
    provider_window: ProviderWindow | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _history: list[ConversationTurn] = field(default_factory=list, init=False, repr=False)
    # M2.4B.5: per-history-entry resolved response language, index-aligned
    # with ``_history`` (``None`` for assistant entries and for user turns
    # that used no explicit request / sticky preference — those fall back
    # to R0009 text detection). Transient wire-level metadata: NOT part of
    # ``history`` (the real transcript), NOT long-term memory.
    _response_languages: list[str | None] = field(
        default_factory=list, init=False, repr=False
    )

    @property
    def history(self) -> tuple[ConversationTurn, ...]:
        return tuple(self._history)

    def build_context(self) -> ConversationContext:
        return ConversationContext.build(
            self.system_prompt,
            self._history,
            max_turns=self.max_turns,
            max_chars=self.max_chars,
            response_languages=self._response_languages,
        )

    def _render_provider_window(self, response_mode: ResponseMode) -> list:
        """M2.5B.2 — render the bounded provider window, first consuming any
        reset the policy calls for (see ``ProviderWindow``).

        A *pre-warmed* cutover is free (the divergence was paid off the
        critical path — the seam exists but is not auto-fired in M2.5B.2).
        A *synchronous* reset advances ``base`` so only the last
        ``keep_entries`` entries stay in view; with the default
        ``keep_entries = 0`` the reset turn's prompt is just the persona
        prefix + the new user turn, so it cold-prefills only ~40-60 tokens
        (~4-6 s, an ordinary turn). It is logged at WARNING so it is never
        silent. Either way, vastly cheaper — and it never recurs — versus
        the permanent every-turn collapse the sliding cap caused.
        """
        w = self.provider_window
        assert w is not None
        n = len(self._history)
        if w.needs_sync_rollover(n):
            old = w.base
            new = w.cutover_sync(n)
            logger.warning(
                "nexa.conversation: provider-context RESET at %d canonical entries "
                "(base %d->%d, keeping last %d in view) — this turn re-prefills "
                "only the retained entries + the new turn; canonical history is "
                "unchanged",
                n, old, new, w.keep_entries,
            )
        elif w.prewarm_ready(n):
            old = w.base
            new = w.cutover_background(n)
            logger.info(
                "nexa.conversation: provider-context cutover (pre-warmed, cheap) "
                "base %d->%d at %d entries", old, new, n,
            )
        return w.render(
            self.system_prompt,
            self._history,
            self._response_languages,
            response_mode=response_mode,
        )

    async def prewarm_provider_context(
        self,
        *,
        cancel_token: CancelToken,
        response_mode: ResponseMode = ResponseMode.TEXT,
    ) -> str:
        """M2.5B.2 — if the provider window has grown past ``soft_entries``,
        pre-warm the small post-rollover window ``history[-keep_entries:]``
        with a ``num_predict=1`` request so the next real turn cuts over
        cheaply (~7 s instead of a bounded cold prefill).

        Call this only when the conversation is idle (no ``send`` in
        flight). It occupies the model for the duration of that small
        prefill and **cannot be interrupted mid-prefill** — keep
        ``keep_entries`` small. ``cancel_token`` still lets the caller stop
        it between the prefill and the single generated token, and marks it
        so a partially-done attempt is retried next idle gap.

        Returns: ``"no_window"`` / ``"not_needed"`` / ``"prewarmed"`` /
        ``"cancelled"`` / ``"error"``.
        """
        w = self.provider_window
        if w is None:
            return "no_window"
        n = len(self._history)
        if not w.needs_prewarm(n):
            return "not_needed"
        target_base = w.next_base(n)
        w.prewarm_attempts += 1
        messages = w.render(
            self.system_prompt,
            self._history,
            self._response_languages,
            response_mode=response_mode,
            base=target_base,
        )
        warm_options = replace(self.options, num_predict=1)
        try:
            async for _ in self.provider.generate(
                messages, warm_options, cancel_token=cancel_token
            ):
                pass
        except Exception as exc:  # noqa: BLE001 — pre-warm is best-effort
            logger.warning(
                "nexa.conversation: provider-context pre-warm failed: %r", exc
            )
            return "error"
        if cancel_token.is_cancelled:
            return "cancelled"
        w.mark_prewarmed(target_base)
        logger.info(
            "nexa.conversation: provider-context pre-warmed window base=%d "
            "(keep last %d of %d entries) — next rollover is cheap",
            target_base, w.keep_entries, n,
        )
        return "prewarmed"

    async def send(
        self,
        user_text: str,
        *,
        cancel_token: CancelToken | None = None,
        response_mode: ResponseMode = ResponseMode.TEXT,
        response_language: str | None = None,
    ) -> AsyncIterator[str]:
        """``response_mode`` (M2.4B.3.3) is a transient presentation hint —
        ``VOICE`` adds one constant "speak conversationally" system message
        to the wire prompt.

        ``response_language`` (M2.4B.5) is the language NeXa should reply in
        for *this* turn, as resolved by the voice
        ``ResponseLanguageResolver`` (explicit user request / sticky
        session preference). ``None`` (the default, and always for typed
        chat) keeps the pre-B.5 behaviour: the response language is the
        R0009 per-turn detection of the user's own text. Either way it is
        wire-level only — never stored in history.

        ``TEXT`` + no ``response_language`` is byte-for-byte the pre-B.3.3
        path."""
        self._history.append(ConversationTurn(role=Role.USER, content=user_text))
        self._response_languages.append(response_language)

        if self.provider_window is not None:
            messages = self._render_provider_window(response_mode)
        else:
            context = self.build_context()
            messages = context.to_provider_messages(response_mode=response_mode)

        raw_stream = self.provider.generate(messages, self.options, cancel_token=cancel_token)
        response = StreamingResponse(raw_stream)
        async for chunk in response:
            yield chunk

        self._history.append(ConversationTurn(role=Role.ASSISTANT, content=response.text))
        self._response_languages.append(None)

    def commit_interrupted_turn(
        self, spoken_text: str | None
    ) -> InterruptedTurnOutcome:
        """M2.5B — record history for a reply cut short by a barge-in.

        The caller (the voice adapter) invokes this exactly once, *after* it
        has stopped consuming a ``send()`` stream early (so ``send`` never
        appended its own assistant turn). ``spoken_text`` is the prefix NeXa
        actually spoke, from the delivered-text high-water mark — ``None`` or
        blank means nothing was spoken yet.

        - **CASE A** (blank ``spoken_text``): the trailing USER turn and its
          response-language slot are rolled back, so the next ``send()`` (the
          interrupting utterance) starts from clean, aligned history — no
          consecutive USER turns, no orphan.
        - **CASE B** (non-blank): an ASSISTANT turn holding exactly that
          spoken prefix is appended with ``interrupted=True``. The unspoken
          remainder is never stored.

        ``_history`` and ``_response_languages`` stay index-aligned in every
        branch. Typed chat never calls this; the pre-M2.5B path is
        byte-for-byte unchanged.
        """
        if not self._history:
            return InterruptedTurnOutcome.NOTHING_TO_COMMIT
        if self._history[-1].role == Role.ASSISTANT:
            # The stream had already finished normally — not an interruption.
            return InterruptedTurnOutcome.NOTHING_TO_COMMIT

        prefix = (spoken_text or "").strip()
        if not prefix:
            self._history.pop()
            self._response_languages.pop()
            return InterruptedTurnOutcome.ROLLED_BACK_USER_TURN

        self._history.append(
            ConversationTurn(role=Role.ASSISTANT, content=prefix, interrupted=True)
        )
        self._response_languages.append(None)
        return InterruptedTurnOutcome.COMMITTED_SPOKEN_PREFIX

    def record_external_exchange(
        self,
        user_text: str,
        assistant_text: str | None = None,
        *,
        interrupted: bool = False,
        response_language: str | None = None,
    ) -> ExternalExchangeOutcome:
        """M2.6B.2 (ADR-0004 Decision A) — commit ONE cloud-provider turn.

        This is the canonical write path for a *realtime cloud voice
        provider* turn (``RealtimeVoiceProvider``, ``nexa.realtime``): NeXa
        never calls ``provider.generate()`` for a cloud turn (a cloud
        provider is speech-to-speech, not a text stream), so the turn
        cannot go through ``send()``. It still lands in exactly the same
        canonical ``_history`` / ``_response_languages``, index-aligned the
        same way.

        ``user_text`` must be non-blank — a cloud "turn" with no user
        utterance at all is a caller bug (the accumulator that calls this,
        ``nexa.realtime.turn.CloudTurnAccumulator``, never produces one),
        not a valid commit: raises ``ValueError`` rather than silently
        appending nothing or corrupting alignment.

        ``assistant_text`` blank or ``None`` means no assistant turn is
        appended at all — never an empty one. This covers both "the
        provider/session was lost before the assistant said anything" and
        "the reply was interrupted before any audio played"; either way
        the user turn is *kept*, unlike the local pre-generation
        ``ROLLED_BACK_USER_TURN`` case, because an external provider did
        hear and process it (ADR-0004 Decision A).

        ``interrupted=True`` with a non-blank ``assistant_text`` commits
        exactly that text as the assistant turn, marked interrupted (same
        ``INTERRUPTED_WIRE_SUFFIX`` convention as ``commit_interrupted_turn``)
        — the caller supplies the actually-spoken prefix, never the full
        (possibly longer) generated/transcribed text.

        Never calls into any provider and never inspects
        ``response_mode``/``provider_window`` — this method only appends to
        the canonical transcript; it is not part of the provider-facing
        rendering path.
        """
        stripped_user = user_text.strip()
        if not stripped_user:
            raise ValueError("record_external_exchange requires a non-blank user_text")

        self._history.append(ConversationTurn(role=Role.USER, content=stripped_user))
        self._response_languages.append(response_language)

        stripped_assistant = (assistant_text or "").strip()
        if not stripped_assistant:
            return ExternalExchangeOutcome.COMMITTED_USER_ONLY

        self._history.append(
            ConversationTurn(
                role=Role.ASSISTANT, content=stripped_assistant, interrupted=interrupted
            )
        )
        self._response_languages.append(None)
        return ExternalExchangeOutcome.COMMITTED_EXCHANGE
