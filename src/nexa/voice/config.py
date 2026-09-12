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

    Input and output are **independent** device selections — never a single
    shared index. Each name is resolved separately by ``device.py``
    (``require_input`` for the mic, ``require_output`` for the speaker), and
    a name that matches nothing raises rather than falling back to another
    device.

    Defaults, `VERIFIED FACT` (2026-09-06, this Pi, after a reboot + USB
    replug — operator-confirmed by a direct test tone and by direct
    old-NeXa-voice playback):

    - **input** ``"respeaker"`` — the reSpeaker XVF3800's own
      ``/etc/asound.conf`` ``plug:`` alias (``slave.pcm
      "hw:CARD=Array,DEV=0"``), addressed by stable ALSA card name.
    - **output** ``"usb_speaker"`` — the dedicated Jieli ``UACDemoV1.0`` USB
      DAC's ``/etc/asound.conf`` ``plug:`` alias (``slave.pcm
      "hw:CARD=UACDemoV10,DEV=0"``), also by stable card name. This DAC is
      the system-wide ALSA default sink and is physically connected again as
      of the 2026-09-06 replug; the earlier M2.1 note that it was absent
      (which forced ``"respeaker"`` for output too) no longer holds.

    Both aliases are ALSA ``plug`` devices, so the fixed 16 kHz / mono
    ``sample_rate``/``channels`` below are converted in software to whatever
    each piece of hardware natively wants (the reSpeaker is 16 kHz stereo;
    the UACDemoV1.0 is 48 kHz stereo) — no channel/rate handling is needed
    in NeXa itself.

    `VERIFIED FACT` (R0053, 2026-09-12, this Pi, real ``amixer``/`/etc/
    asound.conf` audit): the reSpeaker (``Array``) and the USB DAC
    (``UACDemoV10``) each expose their **own, independent** ALSA playback
    mixer — `/etc/asound.conf`'s ``ctl.!default { card UACDemoV10 }``
    means the system's one "default" volume control only ever reaches the
    USB DAC's mixer, never the reSpeaker's own. See
    ``output_alsa_mixer_card`` and ``nexa.voice.aec_gain``.
    """

    input_device_name: str = "respeaker"
    output_device_name: str = "usb_speaker"
    #: M2.6B.4N / R0053 — the ALSA card name backing ``output_device_name``
    #: (`/etc/asound.conf`'s own ``hw:CARD=UACDemoV10``), used to read the
    #: audible path's REAL playback mixer gain for
    #: ``nexa.voice.aec_gain.CoherentReferenceGain``. Kept as its own field
    #: (not derived from ``output_device_name``) because the ALSA `plug:`
    #: alias and the ALSA card name are two different identities — see
    #: this class's own docstring.
    output_alsa_mixer_card: str = "UACDemoV10"
    sample_rate: int = 16000
    channels: int = 1
    #: M2.5B — enable production barge-in / interruption. **Default False =
    #: R0026 whole-response half-duplex, byte-for-byte.** When True the app
    #: must also wire the ``BargeInController`` + ``AecReferenceFeeder``
    #: (XVF3800 AEC far-end reference); a hot mic during a reply without that
    #: reference is unsafe (R0028 / M2.5A.1) and the gate stays in R0026
    #: mode until the reference is confirmed active. ``--no-bargein`` on the
    #: probes forces this back off.
    bargein_enabled: bool = False
