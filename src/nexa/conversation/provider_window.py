"""``ProviderWindow`` — the bounded, prefix-stable *provider-facing* view of
a conversation, separate from the canonical ``ConversationSession.history``.

Why this exists (M2.5B.2, R0029)
-------------------------------
``ConversationContext`` bounds the model prompt by a turn / char count and,
once the conversation exceeds that bound, drops the **oldest** turn on every
request. On the Raspberry Pi the effect is catastrophic and permanent: the
dropped turn sits right after the system prompt, so Ollama / llama.cpp's
cached prompt **prefix** diverges near position 0 and the whole prompt is
cold-reprocessed at ~13 tok/s — measured 78 s (cap 20) then 154-164 s
(cap 40) *per turn*, for the rest of the session. Raising the cap only
moves the cliff.

Research (R0029 §M2.5B.2, `research_provider_context_kv.py`) established:

* a **stable-prefix** window (``base`` unchanged, new turns appended) keeps
  every turn at ~4 s — Ollama re-evaluates only the ~50 genuinely new
  tokens;
* **any** change to the window prefix (slide, jump, recompact) forces a
  cold prefill of the whole window at ~13 tok/s (num_thread=2);
* a Ollama request **cannot be interrupted during prefill** — once issued
  it occupies the model for its full prefill, and any concurrent turn
  queues behind it. So a large speculative "pre-warm" is *unsafe* during
  an active conversation;
* a completed small pre-warm, however, makes the next real turn ~7 s and
  survives intervening turns on the current window (llama.cpp keeps
  multiple prefixes while they fit in ``num_ctx``).

Strategy — **stable window + bounded small rollover**
---------------------------------------------------
The canonical history stays complete (ADR-0003 D2). Only the
provider-facing representation is bounded:

* the model is shown ``history[base:]`` — a **verbatim** recent window;
* ``base`` is **stable** between rollovers → every ordinary turn is a pure
  prefix-extension (~4 s, exactly as today inside the cap);
* when the window reaches ``soft_entries`` the session may, **once**, in an
  idle gap, pre-warm the *small* post-rollover window
  ``history[-keep_entries:]`` (``num_predict=1``). ``keep_entries`` is
  sized so that pre-warm — and the synchronous fallback below — is a
  bounded ~20-40 s, never the ~150 s of a full-window reprocess;
* on a completed pre-warm the session cuts over eagerly
  (``base = len(history) - keep_entries``) and the next turn is ~7 s;
* ``hard_entries`` is the safety net: if the window reaches it with no
  pre-warm ready (a user who never pauses), the next ``send`` cuts over
  **synchronously** — one bounded, loudly-logged slow turn — rather than
  letting the prompt drift toward ``num_ctx`` where Ollama's own
  context-shift would collapse the cache anyway.

Between rollovers every turn is ~4 s; a rollover costs one ~20-40 s turn
roughly every ``hard_entries - keep_entries`` entries. Bounded and rare —
never the permanent every-turn regime. The residual (one slow turn per
rollover) is removed by a non-blocking background pre-warm, which needs a
second Ollama slot (``OLLAMA_NUM_PARALLEL``) — a serving change deferred
past M2.5B.2 (R0029 "remaining risks").

``base`` always lands on a turn boundary (an even offset — a USER turn),
so a rollover never orphans an assistant reply and ``_response_languages``
stays index-aligned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..providers.base import ProviderMessage
from .context import INTERRUPTED_WIRE_SUFFIX
from .language import detect_response_language, language_directive
from .response_mode import ResponseMode, voice_response_directive
from .turn import ConversationTurn, Role

#: M2.5B.2 defaults, in **history entries** (a full exchange = 2 entries).
#: Tuned on the real Pi (R0029 §M2.5B.2 benchmark, ~11.5 tok/s cold
#: prefill @ num_thread=2; ~40 cold tokens per retained entry incl. its
#: language directive + the new user turn):
#:  - ``keep`` 2 entries (one exchange): a rollover cold-prefills only
#:    those ~2 entries + the new user turn — the fixed persona prefix
#:    stays cached — ~12 s for short turns / projected **~16 s** for full
#:    voice turns. One such *logged* maintenance turn per ``hard - keep``
#:    entries, then warm again; never the permanent 78-164 s regime.
#:    ``keep=4`` keeps two exchanges across the seam at ~28-32 s per
#:    rollover; a *non-blocking* background pre-warm (needs
#:    ``OLLAMA_NUM_PARALLEL>=2``) removes the rollover turn entirely —
#:    deferred past M2.5B.2.
#:  - ``soft`` 72 / ``hard`` 88 keep the steady-state window ~36-44
#:    exchanges (~5-6 k tokens) — clear of ``num_ctx`` + ``num_predict``
#:    head-room — so a rollover happens only ~once per ~43 exchanges.
DEFAULT_PROVIDER_WINDOW_KEEP = 2
DEFAULT_PROVIDER_WINDOW_SOFT = 72
DEFAULT_PROVIDER_WINDOW_HARD = 88


@dataclass
class ProviderWindow:
    """Mutable provider-facing window state + rollover policy + rendering."""

    keep_entries: int = DEFAULT_PROVIDER_WINDOW_KEEP
    soft_entries: int = DEFAULT_PROVIDER_WINDOW_SOFT
    hard_entries: int = DEFAULT_PROVIDER_WINDOW_HARD
    #: index into canonical history; the provider sees ``history[base:]``.
    _base: int = field(default=0)
    #: the ``next_base`` value a background pre-warm has completed for and
    #: that is ready to cut over to (``None`` = nothing ready).
    _prewarmed_base: int | None = field(default=None)
    #: monotonic counters — telemetry only.
    rollovers_background: int = field(default=0)
    rollovers_sync: int = field(default=0)
    prewarm_attempts: int = field(default=0)

    def __post_init__(self) -> None:
        if not (2 <= self.keep_entries < self.soft_entries <= self.hard_entries):
            raise ValueError(
                "ProviderWindow requires 2 <= keep < soft <= hard "
                f"(got keep={self.keep_entries}, soft={self.soft_entries}, "
                f"hard={self.hard_entries})"
            )
        if self.keep_entries % 2:
            raise ValueError("keep_entries must be even (a whole exchange)")
        self._align_base()

    # -- base / policy -----------------------------------------------------
    @property
    def base(self) -> int:
        return self._base

    @property
    def prewarmed_base(self) -> int | None:
        return self._prewarmed_base

    def _align_base(self) -> None:
        if self._base % 2:  # a turn boundary is an even offset
            self._base -= 1
        self._base = max(0, self._base)

    def next_base(self, history_len: int) -> int:
        """Base a rollover moves to: keep only the last ``keep_entries``,
        on a turn boundary, never behind the current base."""
        target = history_len - self.keep_entries
        target -= target % 2
        return max(self._base, target)

    def window_entries(self, history_len: int) -> int:
        return max(0, history_len - self._base)

    def needs_prewarm(self, history_len: int) -> bool:
        nb = self.next_base(history_len)
        if nb <= self._base:
            return False
        return (
            self.window_entries(history_len) >= self.soft_entries
            and self._prewarmed_base != nb
        )

    def prewarm_ready(self, history_len: int) -> bool:
        return (
            self._prewarmed_base is not None
            and self._prewarmed_base == self.next_base(history_len)
            and self._prewarmed_base > self._base
        )

    def needs_sync_rollover(self, history_len: int) -> bool:
        nb = self.next_base(history_len)
        return (
            self.window_entries(history_len) >= self.hard_entries
            and nb > self._base
            and not self.prewarm_ready(history_len)
        )

    # -- state transitions ----------------------------------------------------
    def mark_prewarmed(self, base: int) -> None:
        self._prewarmed_base = base

    def clear_prewarm(self) -> None:
        self._prewarmed_base = None

    def cutover_background(self, history_len: int) -> int | None:
        if not self.prewarm_ready(history_len):
            return None
        self._base = self._prewarmed_base  # type: ignore[assignment]
        self._prewarmed_base = None
        self._align_base()
        self.rollovers_background += 1
        return self._base

    def cutover_sync(self, history_len: int) -> int:
        self._base = self.next_base(history_len)
        self._prewarmed_base = None
        self._align_base()
        self.rollovers_sync += 1
        return self._base

    # -- rendering ----------------------------------------------------------
    def render(
        self,
        system_prompt: str,
        history: Sequence[ConversationTurn],
        response_languages: Sequence[str | None] | None = None,
        *,
        response_mode: ResponseMode = ResponseMode.TEXT,
        base: int | None = None,
    ) -> list[ProviderMessage]:
        """Wire message list for ``history[base:]`` — byte-identical to
        ``ConversationContext.to_provider_messages`` for the same slice
        (same fixed persona, fixed-position voice directive, per-USER-turn
        language directive, ``INTERRUPTED_WIRE_SUFFIX``), so a stable
        ``base`` yields a pure prefix-extension every turn."""
        b = self._base if base is None else base
        langs: Sequence[str | None] = (
            response_languages
            if response_languages is not None
            else [None] * len(history)
        )
        if len(langs) != len(history):
            raise ValueError(
                f"response_languages length {len(langs)} != history length {len(history)}"
            )
        messages = [ProviderMessage(role="system", content=system_prompt)]
        if response_mode == ResponseMode.VOICE:
            messages.append(
                ProviderMessage(role="system", content=voice_response_directive())
            )
        for turn, resolved in zip(history[b:], langs[b:], strict=True):
            wire_content = turn.content
            if turn.role == Role.ASSISTANT and getattr(turn, "interrupted", False):
                wire_content = turn.content + INTERRUPTED_WIRE_SUFFIX
            messages.append(
                ProviderMessage(role=turn.role.value, content=wire_content)
            )
            if turn.role == Role.USER:
                language = resolved or detect_response_language(turn.content)
                if language is not None:
                    messages.append(
                        ProviderMessage(
                            role="system", content=language_directive(language)
                        )
                    )
        return messages

    def snapshot(self) -> dict[str, int | None]:
        return {
            "base": self._base,
            "prewarmed_base": self._prewarmed_base,
            "keep_entries": self.keep_entries,
            "soft_entries": self.soft_entries,
            "hard_entries": self.hard_entries,
            "rollovers_background": self.rollovers_background,
            "rollovers_sync": self.rollovers_sync,
            "prewarm_attempts": self.prewarm_attempts,
        }
