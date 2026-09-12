"""M2.6B.4N / R0053 — coherent AEC reference gain.

**Root cause (R0053, real-hardware evidence + real ALSA system audit):**
the reSpeaker XVF3800's AEC far-end reference injection point
(``plug:respeaker``, ALSA card ``Array``) and the audible USB speaker
output (``plug:usb_speaker``, ALSA card ``UACDemoV10``) sit behind **two
entirely independent ALSA hardware mixers** on two physically separate
sound cards. `/etc/asound.conf`'s own ``ctl.!default { card UACDemoV10 }``
means the system's one "default" volume control (however the operator
raises or lowers it — a GUI slider, ``alsamixer``, ``amixer`` with no
``-c``) can only ever reach the **USB speaker's own mixer**. It has zero
effect on the reSpeaker card's own, separate ``PCM Playback Volume``
control, which real system inspection found fixed at its own maximum
(60/60, ``0.00dB``) regardless of what "system volume" the operator sets.

NeXa's own software already proved (R0052) that the reference PCM it
feeds ``AecReferenceFeeder`` is byte-identical to what the audible path
plays — the two paths never diverge in software. But because the two
paths' **hardware** gain stages are decoupled, raising the real speaker
volume raises the true acoustic energy reaching the room and the mic
while leaving the XVF3800's AEC reference feed completely unchanged in
amplitude. A hardware AEC's adaptive filter models the mic signal as a
scaled, time-aligned copy of its reference; once the true acoustic gain
drifts away from what the (always-unscaled) reference implies, that
model — and its cancellation — degrades. R0053's real-hardware evidence
(0/5 false barge-ins at LOW, 1/5 at NORMAL, 5/5 at MAX, with highly
repeatable ~1.08–1.12s onset timing at MAX) is consistent with exactly
this mechanism.

``CoherentReferenceGain`` closes the gap the smallest way that is
possible without touching VAD, Silero, or ``BargeInController`` at all:
it reads the **real, current** ALSA playback gain of the audible output
device (normalized to that device's own 0 dB/maximum) and
:func:`apply_gain` scales the reference PCM by the same linear factor
before ``AecReferenceFeeder`` ever queues it — so as the operator raises
or lowers the real speaker volume, the reference signal's amplitude
tracks it too, restoring the coupling a hardware AEC reference is
supposed to have with the acoustic signal it models.

This assumes the reSpeaker card's own reference-injection mixer stays at
the fixed maximum this checkpoint measured (0 dB) — nothing in NeXa's
software or this fix ever touches it. If that assumption is later found
wrong, the gain read here would need to be relative to *that* mixer's
current level too; out of scope for this checkpoint, whose evidence
covers only the audible-side mixer changing.
"""

from __future__ import annotations

import audioop
import re
import subprocess
import time
from collections.abc import Callable

from loguru import logger

#: The first "[<sign><digits>.<digits>dB]" (or "[<digits>dB]") token
#: `amixer get <control>` prints per playback channel, e.g. "[-9.72dB]"
#: or "[0.00dB]".
_DB_RE = re.compile(r"\[(-?\d+(?:\.\d+)?)dB\]")


def _run_amixer(args: list[str]) -> str:
    """Default subprocess runner — shells out to the real ``amixer``.
    Never raises: any failure (missing binary, no such card/control,
    timeout) is reported as empty output, which the caller treats as
    "unreadable" and falls back safely from."""
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=2.0, check=False
        )
        return completed.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_amixer_db_gain(output: str) -> float | None:
    """Extract a normalized linear gain (0.0–1.0, 1.0 == 0 dB == that
    mixer's own unattenuated maximum) from ``amixer get <control>``
    output, using its first reported dB value.

    Returns ``None`` if no dB value is present at all (unsupported
    control, empty/garbled output, wrong card) — the caller must treat
    that as "could not read," never as "silent."

    A muted first channel (``[off]`` on the same line as the dB value)
    reports ``0.0`` — genuine silence, not an unreadable device.
    """
    m = _DB_RE.search(output)
    if m is None:
        return None
    db = float(m.group(1))
    line_start = output.rfind("\n", 0, m.start()) + 1
    line_end = output.find("\n", m.end())
    line = output[line_start : line_end if line_end != -1 else len(output)]
    if "[off]" in line:
        return 0.0
    gain = 10.0 ** (db / 20.0)
    return max(0.0, min(1.0, gain))


class CoherentReferenceGain:
    """Reads the real, current ALSA playback gain of the audible output
    device and caches it for ``refresh_secs`` between reads — bounded
    subprocess/CPU cost, safe for a Raspberry Pi (never one ``amixer``
    call per audio chunk). Never raises; a failed or stale read falls
    back to the last known-good gain, and to ``1.0`` (today's unscaled
    behavior) if none has ever been read successfully."""

    def __init__(
        self,
        *,
        card: str,
        control: str = "PCM",
        refresh_secs: float = 2.0,
        runner: Callable[[list[str]], str] | None = None,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        self._card = card
        self._control = control
        self._refresh_secs = refresh_secs
        self._runner = runner or _run_amixer
        self._time_source = time_source or time.monotonic
        self._cached_gain = 1.0
        self._last_read = float("-inf")

    def current_gain(self) -> float:
        """The current linear reference-scaling gain. Bounded-cost: only
        re-reads the mixer every ``refresh_secs``; a real change in
        speaker volume is picked up within that window, never instantly
        (deliberately — no per-chunk subprocess spawn)."""
        now = self._time_source()
        if now - self._last_read < self._refresh_secs:
            return self._cached_gain
        self._last_read = now
        output = self._runner(["amixer", "-c", self._card, "get", self._control])
        gain = parse_amixer_db_gain(output)
        if gain is None:
            logger.warning(
                f"nexa.voice.aec_gain: could not read '{self._control}' gain "
                f"on ALSA card {self._card!r} — reference PCM stays at the "
                f"last known gain ({self._cached_gain:.3f})"
            )
            return self._cached_gain
        self._cached_gain = gain
        return gain


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale 16-bit PCM by a linear gain factor. A true no-op at
    ``gain == 1.0`` (today's unscaled reference — never even calls into
    ``audioop`` in that, by far the most common, case) and on empty
    input."""
    if gain == 1.0 or not pcm:
        return pcm
    return audioop.mul(pcm, 2, gain)
