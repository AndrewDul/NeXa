"""Explicit, typed local-audio configuration (M2.1, ADR-0003).

Devices are resolved by name at runtime (see ``device.py``), never by a fixed
positional index — a lesson already learned the hard way in the legacy repo's
own ALSA config (USB re-enumeration order is not guaranteed across
reboots/reconnects).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalAudioConfig:
    """Configuration for the M2.1 local audio input/output + VAD.

    Defaults target the reSpeaker XVF3800's own ALSA ``plug:respeaker`` alias
    for both input and output. `VERIFIED FACT` (2026-09-05): the system's
    dedicated USB speaker DAC (configured as the system-wide ALSA default
    output in ``/etc/asound.conf``, addressed by stable card name
    ``UACDemoV10``) is not physically connected on this machine right now —
    only the reSpeaker's own playback subdevice is actually present and
    usable. Using it for output here is a documented, verified choice, not a
    silent fallback.
    """

    input_device_name: str = "respeaker"
    output_device_name: str = "respeaker"
    sample_rate: int = 16000
    channels: int = 1
