"""M2.5B — XVF3800 AEC far-end reference health + the barge-in safe-mode rule.

R0028 / M2.5A.1 proved a hot microphone during NeXa's own reply is only
safe when the reSpeaker XVF3800 is being fed its AEC far-end reference
(identical TTS PCM also to ``plug:respeaker``): without it, NeXa's own
voice tripped the VAD on **14/14** silent-playback trials; with it, **0/4**
and the operator M2.5A.2 test passed 3/3.

So barge-in admission depends on this feed actually running. This tiny
state object is the single source of truth; the ``BargeInController`` and
the mic gate consult ``barge_in_safe``. If the feed fails mid-response,
barge-in is disabled for the rest of that response and the mic falls back
to R0026 whole-response suppression — **loudly** (telemetry), never a
silent unsafe hot-mic.

Pure: no audio, no I/O. Driven from the single event loop like
``HalfDuplexGate``.
"""

from __future__ import annotations

from collections.abc import Callable


class AecReferenceHealth:
    def __init__(self, *, on_change: Callable[[bool], None] | None = None) -> None:
        self._active = False
        self._failure_count = 0
        self._ever_started = False
        self._on_change = on_change

    def _changed(self, now_active: bool) -> None:
        if now_active != self._active and self._on_change is not None:
            try:
                self._on_change(now_active)
            except Exception:  # a status sink must never break audio
                pass

    @property
    def active(self) -> bool:
        """The AEC far-end reference feed is currently running."""
        return self._active

    @property
    def failure_count(self) -> int:
        """How many times the reference feed has failed this session."""
        return self._failure_count

    @property
    def ever_started(self) -> bool:
        return self._ever_started

    @property
    def barge_in_safe(self) -> bool:
        """Whether it is safe to admit an interruption on a hot mic right
        now. Barge-in code MUST gate on this."""
        return self._active

    def mark_started(self) -> None:
        """The reference feed is confirmed running (audio actually flowing to
        ``plug:respeaker``)."""
        self._changed(True)
        self._active = True
        self._ever_started = True

    def mark_failed(self) -> None:
        """The reference feed failed to start, or died during a response."""
        if self._active or not self._ever_started:
            self._failure_count += 1
        self._changed(False)
        self._active = False

    def mark_stopped(self) -> None:
        """The reference feed stopped as part of a normal shutdown (not a
        failure)."""
        self._changed(False)
        self._active = False
