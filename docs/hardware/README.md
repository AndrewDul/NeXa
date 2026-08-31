# docs/hardware/

Documentation for physical devices that act as NeXa **bodies**, and for the
Device Awareness layer.

## Scope

- Verified hardware profiles per device (sensors, actuators, displays, audio,
  compute, connectivity, power).
- Wiring, calibration, and known quirks.
- What Device Awareness reports on each device.

## Status at M0

Nothing verified here yet. The legacy repo documents a Raspberry Pi 5 robot
hardware set (AI HAT+/Hailo, Camera Module 3 Wide, DSI touchscreen, Waveshare
LCD + pan-tilt, X1206 UPS, reSpeaker mic, OAK-D Lite, UGV02 base) — see
`docs/legacy/LEGACY_NEXA_INDEX.md` → *Raspberry Pi hardware integrations*. That
list is **legacy-declared and unverified for this project**. Do not copy it in as
fact; verify against the actual attached hardware when M4 work begins.

Current host (`VERIFIED FACT`, 2026-08-31): Debian 13 "trixie", `aarch64`, kernel
`6.18.39+rpt-rpi-2712`, hostname `nexa` — consistent with a Raspberry Pi, but the
peripherals are not verified.

Planned milestone: **M4 — Device Awareness + Capability Registry**.
