"""M2.5B — the delivered-text high-water mark for interrupted-turn history.

When a reply is cut short, ``ConversationSession.commit_interrupted_turn``
needs the prefix NeXa *actually spoke*, not everything the model generated.
The smallest reliable seam that already carries the right text is the
``TTSTextFrame`` each sentence produces as it is handed to Piper for
synthesis (``TtsStatusObserver.on_tts_text``): it is downstream of the
planner and continuity controller, so it is exactly "text released toward
the speaker", at sentence-boundary precision.

**Documented approximation:** a sentence counted here has been *synthesized*
and queued; if the barge-in lands mid-sentence, that last sentence may not
have fully played. Accepted (R0028 RISK 3) — history is a sentence or two
generous at worst, never stores unspoken remainder, never invents text.

Pure: no frames, no I/O. Keyed by ``response_id`` so a late sentence from an
already-invalidated reply is dropped, not mis-attributed.
"""

from __future__ import annotations


class SpokenTextTracker:
    def __init__(self) -> None:
        self._active_response_id: int | None = None
        self._sentences: list[str] = []

    def start_response(self, response_id: int) -> None:
        """Begin accumulating for a new reply. Drops any prior buffer."""
        self._active_response_id = response_id
        self._sentences = []

    def add_synthesized_sentence(self, response_id: int, text: str) -> None:
        """Record one sentence handed to TTS. Ignored unless ``response_id``
        is the active reply (a late sentence from an interrupted reply must
        not leak into the next one)."""
        if response_id != self._active_response_id:
            return
        s = text.strip()
        if s:
            self._sentences.append(s)

    def spoken_prefix(self, response_id: int) -> str:
        """The concatenated spoken prefix for ``response_id`` (single spaces
        between sentences), or ``""`` if that reply is not the tracked one or
        nothing was spoken."""
        if response_id != self._active_response_id:
            return ""
        return " ".join(self._sentences)

    def clear(self) -> None:
        self._active_response_id = None
        self._sentences = []
