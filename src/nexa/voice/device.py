"""PyAudio device resolution by name (M2.1).

Never resolve a real audio device by a fixed positional index — PortAudio's
enumeration order depends on USB/ALSA discovery order, which is not stable
across reboots or reconnects (the same lesson the legacy repo's own
``/etc/asound.conf`` records explicitly for this exact hardware). Resolving
by name, with an explicit failure if nothing matches, is the only choice that
doesn't quietly point at the wrong device after a re-enumeration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyaudio


class AudioDeviceNotFoundError(RuntimeError):
    """No PyAudio device matched the configured name."""


def find_device_index(
    pa: pyaudio.PyAudio,
    name: str,
    *,
    require_input: bool = False,
    require_output: bool = False,
) -> int:
    """Find a PyAudio device index by name.

    Prefers an exact (case-insensitive) name match — the real device name is
    known and stable (e.g. the ALSA ``plug:`` alias ``"respeaker"``) — and
    falls back to a substring match only if no exact match exists, since a
    raw hardware device name (e.g. ``"reSpeaker XVF3800 4-Mic Array: USB
    Audio (hw:2,0)"``) can also contain the same substring but expose
    different (fixed, non-negotiable) channel/rate constraints.
    """
    candidates: list[tuple[int, str]] = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        dev_name = str(info.get("name", ""))
        if require_input and int(info.get("maxInputChannels", 0) or 0) < 1:
            continue
        if require_output and int(info.get("maxOutputChannels", 0) or 0) < 1:
            continue
        candidates.append((i, dev_name))

    for i, dev_name in candidates:
        if dev_name.lower() == name.lower():
            return i
    for i, dev_name in candidates:
        if name.lower() in dev_name.lower():
            return i

    raise AudioDeviceNotFoundError(
        f"no PyAudio device found matching {name!r} "
        f"(require_input={require_input}, require_output={require_output})"
    )
