"""``ProviderWindow`` — the bounded, prefix-stable *provider-facing* view of
a conversation, separate from the canonical ``ConversationSession.history``.

Why this exists (M2.5B.2, R0029)
-------------------------------
``ConversationContext`` bounds the model prompt by a turn / char count and,
once the conversation exceeds that bound, drops the **oldest** turn on every
request. On the Raspberry Pi the effect is catastrophic and permanent: the
dropped turn sits right after the system prompt, so Ollama / llama.cpp's
cached prompt **prefix** diverges near position 0 and the whole prompt is
cold-reprocessed at ~11.5 tok/s (num_thread=2) — measured 78 s (cap 20)
then 154-164 s (cap 40) *per turn*, for the rest of the session. Raising
the cap only moves the cliff.

Research (R0029 §M2.5B.2 — `research_provider_context_kv.py`,
`research_prewarm_resume.py`, real `gemma4:e4b`):

* a **stable-prefix** window (``base`` unchanged, new turns appended) keeps
  every turn at ~4 s — Ollama re-evaluates only the ~40-60 genuinely new
  tokens;
* **any** change to the window prefix (slide, jump, recompact) forces a
  cold prefill of the whole retained window at ~11.5 tok/s;
* an Ollama request **cannot be interrupted during prefill** — a 30-turn
  pre-warm occupied the model 336 s before ``cancel()`` took effect. So a
  large speculative pre-warm is unsafe during a live conversation;
* a second Ollama KV slot (``OLLAMA_NUM_PARALLEL=2``) was measured on the
  Pi and **rejected**: gemma4's sliding-window attention makes the two
  slots' KV caches interfere, so a foreground turn issued while the other
  slot is busy **loses its cached prefix and cold-reprocesses** (~49 s
  observed) — the opposite of the goal — plus CPU contention on ``-t 2``
  and ~770 MB extra swap.

Strategy — **stable window + context reset at the boundary**
---------------------------------------------------------
The canonical history stays complete (ADR-0003 D2). Only the
provider-facing representation is bounded:

* the model is shown ``history[base:]`` — a **verbatim** recent window;
* ``base`` is **stable between rollovers** → every ordinary turn is a pure
  prefix-extension → **~4 s, for the whole session**, no matter how long
  it runs;
* when the window reaches ``hard_entries`` the next ``send`` performs a
  **context reset**: ``base`` jumps forward so only the last
  ``keep_entries`` history entries remain in view. With the default
  ``keep_entries = 0`` the reset turn's prompt is just
  ``[persona][voice-dir] + [the new user turn]`` — the fixed persona
  prefix is still cached, so that turn cold-prefills only ~40-60 tokens
  and lands in **~4-6 s, an ordinary turn**. It is logged at WARNING. The
  model briefly has no recent conversational context; the window then
  regrows by append over the next 2-3 turns. Canonical history is
  untouched, and M5 long-term memory reads all of it.
* ``keep_entries > 0`` (even) instead retains that many recent entries
  across the reset — more continuity, but the reset turn then cold-
  prefills those entries too (~11.5 tok/s → keep=2 ≈ +16 s, keep=4 ≈
  +32 s for full voice turns). Only raise it if a ≥ 15 s reset turn is
  acceptable for the deployment.
* the pre-warm seam (``mark_prewarmed`` / ``prewarm_ready`` /
  ``cutover_background`` + ``ConversationSession.prewarm_provider_context``)
  is retained but **not auto-fired**: on a single Ollama slot a pre-warm
  blocks a concurrent turn for its whole prefill. It is the hook for a
  future non-blocking idle pre-warm (which would upgrade a reset to a
  ``keep_entries``-continuity cutover without the cost) once a
  resource-safe mechanism exists.

Between resets every turn is ~4 s; a reset costs **one ordinary ~4-6 s
turn** (keep=0) roughly once per ``hard_entries`` entries. Never a
>10 s user-visible stall, never the permanent every-turn regime.

``base`` always lands on a turn boundary (an even offset), so a reset
never orphans an assistant reply and ``_response_languages`` stays
index-aligned.
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
#: Tuned on the real Pi (R0029 §M2.5B.2, ~11.5 tok/s cold prefill @
#: num_thread=2):
#:  - ``keep = 0``: the reset turn's provider prompt is only
#:    ``[persona][voice-dir] + [new user turn]`` — persona stays cached —
#:    so the reset turn is an **ordinary ~4-6 s turn**, not a stall. The
#:    model briefly loses recent context; the window regrows by append.
#:    ``keep = 2`` / ``4`` retain 1 / 2 recent exchanges across the reset
#:    at ~+16 s / ~+32 s reset-turn cost (full voice turns) — opt in only
#:    if that is acceptable.
#:  - ``soft`` 72 arms the (currently inert) pre-warm seam; ``hard`` 88
#:    keeps the steady-state window ~44 exchanges (~5-6 k tokens) — clear
#:    of ``num_ctx`` + ``num_predict`` head-room — so a reset happens only
#:    ~once per ~44 exchanges.
DEFAULT_PROVIDER_WINDOW_KEEP = 0
DEFAULT_PROVIDER_WINDOW_SOFT = 72
DEFAULT_PROVIDER_WINDOW_HARD = 88


@dataclass
class ProviderWindow:
    """Mutable provider-facing window state + reset policy + rendering."""

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
        if not (0 <= self.keep_entries < self.soft_entries <= self.hard_entries):
            raise ValueError(
                "ProviderWindow requires 0 <= keep < soft <= hard "
                f"(got keep={self.keep_entries}, soft={self.soft_entries}, "
                f"hard={self.hard_entries})"
            )
        if self.keep_entries % 2:
            raise ValueError("keep_entries must be even (a whole exchange) or 0")
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
        """Base a reset moves to: keep only the last ``keep_entries``
        entries, on a turn boundary, never behind the current base. With
        ``keep_entries == 0`` this is the offset of the turn currently
        being sent (``history_len`` is odd — the new USER turn was just
        appended — so the model sees only that turn)."""
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
