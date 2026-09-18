# R0082 — LiveKit/WebRTC Audio Runtime PoC

## 1. Goal

R0081 (`docs/reports/R0081_cloud_voice_false_bargein_self_echo_regression_20260917.md`)
exhaustively investigated NeXa's current manual acoustic-frontend topology — a real
USB speaker (`plug:usb_speaker`) driven independently of a separate
`AecReferenceFeeder` process that feeds the XVF3800's own hardware AEC far-end
reference (`plug:respeaker`) via a second, independent `aplay` process — and left
the root cause of a large, non-monotonic, in-stream residual instability OPEN
across the host/ALSA/USB/driver/DSP/acoustic layers, with a new clock-drift
hypothesis built (timing instrumentation) but never run before the investigation
was explicitly frozen as a diagnostic baseline.

R0082 asks a different, narrower question: does LiveKit's own WebRTC audio path —
a single, unified Audio Device Module that owns both capture and playout and
wires the render (far-end) signal directly into its own Audio Processing
Module's reverse-stream path, with **no separate physical reference device and
no separate manual reference-feed process** — produce a materially more stable
residual than R0081's manual topology?

This is an **architecture spike / PoC**, not a production migration. R0081's
findings, diagnostics, and `AecReferenceFeeder` remain the frozen, preserved
baseline; the current production/cloud voice path is unchanged; Memory,
Identity, Context, ConversationSession, and NeXa Core are untouched; M3.4 has
not started.

## 2. Old vs. proposed architecture

### 2.1 Current (frozen baseline, unchanged, R0081-era)

```
reSpeaker XVF3800 (mic + onboard DSP AEC)
    -> plug:respeaker (ALSA capture)             -> Pipecat local audio input
AecReferenceFeeder (persistent aplay, drop-oldest queue)
    -> plug:respeaker (ALSA far-end reference)   <- TTS PCM
Pipecat TTS output
    -> plug:usb_speaker (ALSA playback, separate USB Audio Class device)
```

Two independent USB Audio Class devices, no shared/configured clock, one
ALSA-resampled 16kHz->48kHz path (speaker) and one not (reference).

### 2.2 Proposed R0083 target (NOT built this round — documented only, per
explicit instruction; R0082 tests only the audio/AEC layer in isolation)

```
Pi (edge/frontend, owns hardware)
  reSpeaker mic -> LiveKit rtc.PlatformAudio / MediaDevices (WebRTC ADM: AEC+NS+AGC)
      -> WebRTC -> LiveKit room
  LiveKit room -> WebRTC -> PlatformAudio/MediaDevices -> USB speaker

NeXa agent/backend (room participant, no direct hardware access)
  LiveKit room <-> Pipecat LiveKitTransport <-> ConversationSession / NeXa Core bridge
      <-> Gemini Live
```

LiveKit is a transport/room + audio-device layer, **not** NeXa Core. Pipecat
remains the pipeline framework. Gemini remains a replaceable provider. NeXa
Core remains canonical. This round (R0082-A/B) tests **only** the audio/AEC
layer — no Gemini, no LiveKit room/server, no Pipecat involvement at all.

## 3. Important architecture question — resolved from actual installed source

**Question:** is Pipecat's `LiveKitTransport` itself the physical-audio-device
layer, or is device ownership a separate, Pi-local concern?

**Answer, confirmed directly from the installed `pipecat-ai` 1.8.1 source**
(`pipecat/transports/livekit/transport.py`), not from memory or documentation:

- `LiveKitTransport.__init__(self, url, token, room_name, params=None,
  input_name=None, output_name=None)` — a pure network/room participant. It
  takes a LiveKit server `url`, an auth `token`, and a `room_name`; it has
  **no** device-selection, device-enumeration, or `PlatformAudioOptions`-style
  parameters anywhere in its constructor or `LiveKitParams`.
- `LiveKitParams` is a trivial subclass of Pipecat's generic `TransportParams`
  with no added device fields.
- The `livekit`/`livekit-api` import is guarded behind a `try/except
  ModuleNotFoundError`, with the installed source's own error message
  instructing `uv add "pipecat-ai[livekit]"` — confirming this is an optional
  extra, not a bundled default.

**Conclusion:** `LiveKitTransport` is the **agent/backend** role only — a room
participant with zero physical-device awareness. Physical audio-device
ownership (mic/speaker selection, WebRTC AEC/NS/AGC) is a separate concern
that belongs entirely to whatever process runs `rtc.PlatformAudio` /
`rtc.MediaDevices` on the Pi itself (the **edge/frontend** role). These are
two distinct participants/processes in the target architecture, not one
component doing both jobs. This directly resolves R0082's own "important
architecture question."

## 4. Actual installed / verified versions

| Package | Where | Version (confirmed real, not assumed) |
|---|---|---|
| `pipecat-ai` | NeXa's own `.venv` | 1.8.1 (source-audited directly, not re-verified via `pip show` this round since R0081-era audits already confirmed it) |
| `tenacity` | NeXa's own `.venv` | 9.1.4 (already present, transitive dep of `google-genai`; satisfies pipecat's `<10.0.0,>=8.2.3` constraint) |
| `livekit` | isolated probe venv only (`/tmp/.../r0082_livekit_probe_venv`, **not** NeXa's `.venv`) | 1.1.19 (`manylinux_2_28_aarch64` wheel; local glibc 2.41 compatible) |
| `livekit-api` | isolated probe venv only | 1.2.1 (pure-Python `py3-none-any`) |
| `pyjwt` | isolated probe venv only | satisfies `<3,>=2.12.0` (installed as `livekit-api` dependency) |
| `sounddevice` | isolated probe venv only | 0.5.6 (installed this round; **not** a transitive dependency of `livekit`/`livekit-api` — must be installed explicitly) |
| `libportaudio2` (system, apt) | Pi OS, system-wide | 19.6.0-1.2+b3 (arm64) — already present, likely from R0053/R0081-era vendor-tool work; not installed this round |

Pipecat's own declared constraints (from its installed `METADATA`, the
`[livekit]` extra): `livekit<2,>=1.0.13`, `livekit-api<2,>=1.0.5`,
`tenacity<10.0.0,>=8.2.3`, `pyjwt<3,>=2.12.0`. All versions above satisfy
these.

**Exact isolated-venv install command (verified, reproducible):**

```bash
python3 -m venv /path/to/isolated/r0082_livekit_probe_venv
/path/to/isolated/r0082_livekit_probe_venv/bin/pip install \
    "livekit>=1.0.13,<2" "livekit-api>=1.0.5,<2" \
    "pyjwt>=2.12.0,<3" "tenacity>=8.2.3,<10.0.0" \
    "sounddevice>=0.4,<1"
```

This venv is **not** NeXa's own `.venv` and must never be merged into it for
a spike of this scope (established precedent: R0053/R0081's XVF3800 vendor
tooling followed the same isolation discipline).

## 5. Device enumeration — two genuinely different backends found

R0082-A audited **two separate LiveKit device-enumeration APIs**, and found
they go through **different underlying audio subsystems** on this Pi — a
real, previously-undocumented-for-this-project distinction:

### 5.1 `rtc.PlatformAudio` — goes through PipeWire (PulseAudio-compatible layer)

Real output from `PlatformAudio().recording_devices()` /
`.playout_devices()` on the actual hardware (safe, read-only, no playback):

```
recording_devices():
  index=0 id='' name='default: reSpeaker XVF3800 4-Mic Array Analog Stereo'
  index=1 id='' name='reSpeaker XVF3800 4-Mic Array Analog Stereo'
playout_devices():
  index=0 id='' name='default: UACDemoV1.0 Analog Stereo'
  index=1 id='' name='UACDemoV1.0 Analog Stereo'
  index=2 id='' name='reSpeaker XVF3800 4-Mic Array Analog Stereo'
```

Both target devices are visible. **Limitation found:** `AudioDeviceInfo.id`
is **always an empty string** for every device on this Linux/PipeWire
system, contradicting the SDK's own docstring claim that `id` (a stable
GUID) is generally preferred over `index`. Device selection by `id` is
therefore not viable here; name-substring matching against a fresh
enumeration is used instead (never a hardcoded index).

### 5.2 `rtc.MediaDevices` — goes through PortAudio's ALSA hostapi directly (bypasses PipeWire)

Real output from `MediaDevices(input_sample_rate=48000,
output_sample_rate=48000).list_input_devices()` / `.list_output_devices()`
on the actual hardware (safe, read-only, no playback):

```
list_input_devices():
  index=1 name='reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0)'  default_samplerate=16000.0
  index=3 name='respeaker'                                          default_samplerate=16000.0
  index=4 name='default'                                            default_samplerate=16000.0
list_output_devices():
  index=0 name='UACDemoV1.0: USB Audio (hw:2,0)'  default_samplerate=48000.0
  index=1 name='reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0)'  default_samplerate=16000.0
  index=2 name='usb_speaker'                       default_samplerate=48000.0
  index=3 name='respeaker'                          default_samplerate=16000.0
  index=4 name='default'                            default_samplerate=16000.0
```

**New finding:** `MediaDevices` is backed by `sounddevice`/PortAudio's ALSA
hostapi, which enumerates the **raw ALSA hw devices AND the literal
`/etc/asound.conf` PCM aliases** (`respeaker`, `usb_speaker`, `default`) —
the exact same aliases R0081's `aplay`-based diagnostics used directly. This
is architecturally distinct from `PlatformAudio`'s PipeWire-mediated view.

**Device resolution confirmed correct:** the PoC script's
`_find_device_index()` (case-insensitive name-substring match, preferring a
`"default:"`-prefixed entry when ambiguous, otherwise first match) resolves,
against this real data:
- input `"reSpeaker"` -> index 1, `reSpeaker XVF3800 4-Mic Array: USB Audio
  (hw:3,0)` (unambiguous first/only substantive match — the raw hw device,
  not the `plug:respeaker` alias)
- output `"UACDemoV1.0"` -> index 0, `UACDemoV1.0: USB Audio (hw:2,0)`
  (single match, unambiguous)

**Risk flagged, not yet resolved (requires the actual hardware test to
confirm):** PortAudio reports the reSpeaker's native `default_samplerate` as
`16000.0`, while the PoC opens the stream at `48000` (`MediaDevices`'s own
default, and the rate the WebRTC APM operates at). Whether PortAudio's ALSA
hostapi will transparently rate-convert 16kHz<->48kHz for this specific
USB Audio Class 1.0 device, or fail/error at stream-open time, is **not
determined by enumeration alone** and is an open item for the first real
hardware run.

## 6. AEC capability — confirmed present as a real code path, not yet proven effective

From the installed `livekit/rtc/media_devices.py` source (596 lines, pure
Python):

- `MediaDevices.open_input(*, enable_aec=True, noise_suppression=True,
  high_pass_filter=True, auto_gain_control=True, input_device=None, ...)
  -> InputCapture` creates a real `AudioProcessingModule` bound to the
  input's forward stream.
- `MediaDevices.open_output(*, output_device=None) -> OutputPlayer` — the
  docstring explicitly confirms that calling `open_output()` **after**
  `open_input()` auto-wires the output's rendered PCM into that **same**
  `AudioProcessingModule`'s reverse stream via
  `apm.process_reverse_stream(render_frame)` on every real 10ms output
  frame (verified in `OutputPlayer._callback`'s source) — this is a real
  WebRTC AEC reference-feed path, not a mock or a stub.
- Confirms this design needs **no LiveKit room, server, or token** — the
  loopback is entirely local, which is why the R0082-B PoC does not stand up
  any LiveKit infrastructure.

This resolves audit item 9 (verify AEC is actually enabled, not just a
boolean flag) to the extent achievable by source audit: the code path is
real. Whether it **measurably reduces residual leakage**, and whether it is
**more stable over 30-60s** than R0081's manual topology, is an empirical
question the actual (not-yet-run) hardware test must answer — R0082-A alone
cannot and does not claim this.

## 7. Sample rate — resolved, contradicts the old 16kHz assumption

`MediaDevices.DEFAULT_SAMPLE_RATE = 48000` (source constant) and
`FRAME_SAMPLES = 480` (10ms @ 48kHz). The PoC opens both streams at 48000Hz.
This directly resolves audit item 8: NeXa's old internal 16kHz convention
(`LocalAudioConfig`'s reSpeaker-is-16kHz docstring, R0081-era) does **not**
carry over to this API path — the WebRTC APM here operates at 48kHz
throughout, and the reSpeaker's own native 16kHz capture rate (confirmed in
§5.2's enumeration) will need to pass through whatever resampling PortAudio
applies, an open item noted above.

## 8. Linux/aarch64 compatibility — confirmed

- `livekit-1.1.19-py3-none-manylinux_2_28_aarch64.whl` exists on PyPI and
  installed successfully on the real Pi hardware (glibc 2.41, `manylinux_2_28`
  requires >=2.28 — compatible).
- `livekit-api` is pure-Python (`py3-none-any`), no compatibility concern.
- `sounddevice-0.5.6` installed via a `piwheels.org` aarch64 wheel; its
  `libportaudio2` system dependency was already present (apt, arm64, likely
  from prior R0053/R0081 work).
- Both `livekit.rtc` and `livekit.rtc.media_devices` import successfully on
  the real hardware in the isolated venv (confirmed this round).
- The system's audio server is PipeWire 1.4.2 (exposing a
  PulseAudio-compatible `pipewire-pulse` interface, active since
  2026-09-15) — `PlatformAudio` goes through this; `MediaDevices` bypasses it
  via PortAudio's ALSA hostapi directly (§5).

## 9. Decision — why `MediaDevices`, not `PlatformAudio`, for the R0082-B PoC

The R0082-B PoC (`docs/research/r0082_livekit_webrtc_audio_poc/
r0082_platform_audio_aec_poc.py`) uses `rtc.MediaDevices.open_input()` /
`.open_output()`, not `rtc.PlatformAudio`, because:

1. It offers a genuinely local, room-free, server-free loopback AEC test
   path (§6) — matching the instruction that the "remote/test side can run
   locally" without needing to stand up LiveKit infrastructure for a narrow
   audio-path question.
2. Its AEC reverse-stream wiring (output -> same APM's reverse stream) is
   directly confirmed from source, not inferred.
3. `PlatformAudio`'s equivalent AEC wiring would require an actual
   room/track publish-and-subscribe loop through a live or embedded LiveKit
   room to exercise the same reverse-stream path — meaningfully larger scope
   for a first PoC than this narrower, already-viable local API affords.

This is noted as a real trade-off, not a final architectural choice:
`PlatformAudio` remains the more likely R0083 production candidate (it is
the API PipeWire-integrated systems are expected to use), and a later
`PlatformAudio`-based room test is not ruled out if `MediaDevices`'s result
is inconclusive or its ALSA-bypass path proves unrepresentative of the real
target architecture.

## 10. Files added

- `docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py`
  — the R0082-B minimal audio-only PoC script. Reuses `build_signal` from
  R0081's `r0081_direct_aec_diagnostic.py` and `_peak`/`_rms`/`_write_wav`
  from `m2_6b4m_self_echo_probe.py` via direct `sys.path` insertion (no
  duplication of signal-generation logic). `py_compile`-clean (isolated
  probe venv) and `ruff check`-clean (NeXa's own `.venv/bin/ruff`).
- `docs/research/r0082_livekit_webrtc_audio_poc/` (new, empty until the PoC
  is actually run) — output directory for `r0082_aec_captures/` WAV
  evidence, run-id-named, following the same convention as R0081's own
  `r0081_aec_captures/`.
- This report.

No production code (`src/`, `apps/`), no test suite, no `AecReferenceFeeder`
changes. R0081's own diagnostic script and report are untouched.

## 11. Every command run this round (source/environment audit)

```bash
# Isolated venv creation and SDK install (NOT NeXa's own .venv)
python3 -m venv /tmp/.../scratchpad/r0082_livekit_probe_venv
/tmp/.../r0082_livekit_probe_venv/bin/pip install \
    "livekit>=1.0.13,<2" "livekit-api>=1.0.5,<2" \
    "pyjwt>=2.12.0,<3" "tenacity>=8.2.3,<10.0.0"

# Real-hardware, read-only PlatformAudio device enumeration (no playback)
/tmp/.../r0082_livekit_probe_venv/bin/python3 -c "
from livekit import rtc
pa = rtc.PlatformAudio()
print(pa.recording_devices()); print(pa.playout_devices())"

# sounddevice availability check (this round) -- found MISSING, installed
/tmp/.../r0082_livekit_probe_venv/bin/python3 -c "import sounddevice"
/tmp/.../r0082_livekit_probe_venv/bin/pip install "sounddevice>=0.4,<1"

# system PortAudio library check (this round)
dpkg -l | grep -i portaudio
ldconfig -p | grep -i portaudio

# Real-hardware, read-only MediaDevices device enumeration (no playback)
/tmp/.../r0082_livekit_probe_venv/bin/python3 -c "
from livekit import rtc
media = rtc.MediaDevices(input_sample_rate=48000, output_sample_rate=48000)
print(media.list_input_devices()); print(media.list_output_devices())"

# Static validation of the new PoC script (this round)
/tmp/.../r0082_livekit_probe_venv/bin/python3 -m py_compile \
    docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py
.venv/bin/ruff check --fix \
    docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py
```

(Earlier-round commands — `pipecat` source audit, `PlatformAudioOptions`/
`AudioFrame`/`AudioSource` signature introspection via `inspect`, the
`frame.data` memoryview-assignment bug discovery — are reflected in §12/§13
below; exact `inspect.signature`/`inspect.getdoc` invocations are not
re-listed here as they produced no persisted artifact beyond the findings
already stated.)

## 12. Bug found and fixed before any hardware run

**`frame.data[:] = buf.tobytes()` raises `ValueError: memoryview assignment:
lvalue and rvalue have different structures`.** Found via direct empirical
testing against the real installed SDK (not guessed). Root cause:
`rtc.AudioFrame.data` is an `'h'`-format (int16) memoryview
(`frame.data.format == 'h'`, `itemsize == 2`), while
`array.array('h', ...).tobytes()` produces a raw `'B'`-format bytes object —
incompatible for memoryview slice assignment. **Fix, empirically verified
to work:** assign the `array.array('h', ...)` object directly
(`frame.data[:] = buf`), which is format-compatible. Applied in
`_play_signal()` with an explanatory comment. This was caught during
offline validation, before any hardware run was attempted.

## 13. Test methodology (for the not-yet-run R0082-B hardware test)

- `--aec off` then `--aec on`, back to back, same physical volume/room/
  device positions (same A/B discipline R0081 established).
- 30s duration (default), `tones` stimulus (reused from R0081), amplitude
  0.05 (matches R0081's convention).
- 1s pre-roll (quiet-floor measurement) + signal + 1s tail, captured on one
  continuous `InputCapture` stream (no stream recreation — the R0081-era
  confound this avoids by construction).
- Per-3s-window RMS/peak, plus overall/first-half/second-half means,
  min/max, max/min ratio — same analysis shape as R0081's
  `--track-continuous`.
- Raw signal + mic WAV evidence written with a unique run id
  (`YYYYMMDDTHHMMSSZ`), preventing overwrite (same discipline R0081 adopted
  after its own WAV-overwrite bug fix).
- CPU/RAM on the Pi must be measured separately by the operator (e.g.
  `top`/`vmstat` in a second terminal during the run) — the script itself
  does not measure this.
- Key comparison is **stability over time** (max/min ratio, first-half vs.
  second-half means), not raw absolute RMS vs. R0081 — R0081's own
  quiet-floor/gain/rate differ enough that a direct RMS comparison would be
  invalid without normalization.

## 14. Acceptance criteria (R0082-A/B, unchanged from the brief, restated for tracking)

Not yet evaluable — no hardware test has been run this round (explicitly
forbidden until further review, per instruction):

1. WebRTC AEC demonstrably reduces leakage vs. an appropriate AEC-off
   baseline — **NOT YET TESTED**.
2. Residual materially more stable over 30-60s than R0081's manual
   topology — **NOT YET TESTED**.
3. No artificial mic muting/gating — satisfied by design (script does not
   gate the mic at any point; `enable_aec` only toggles the APM's echo
   canceller, not capture gating).
4. Full-duplex capture active during playback — satisfied by design
   (`capture_task` runs concurrently with `output_player` playback via
   `asyncio.create_task`, not sequentially).
5. CPU/RAM on Pi 5 measured — **NOT YET MEASURED** (operator
   responsibility at hardware-test time, per §13).
6. No XRUN/errors/reconnects — **NOT YET OBSERVED** (no run yet).

## 15. Limitations

- `AudioDeviceInfo.id` is always empty on this Linux/PipeWire system for
  `PlatformAudio` (§5.1) — a genuine, previously-undocumented-for-this-
  platform SDK limitation; name-substring matching is used instead.
- `PlatformAudio` and `MediaDevices` use **different underlying audio
  backends** (PipeWire vs. PortAudio/ALSA directly) — a finding that was
  not obvious in advance and materially affects which devices/aliases are
  visible to each API (§5).
- `MediaDevices` enumeration was **not** empirically tested until this
  round; it is now confirmed working and correctly resolves both target
  devices (§5.2) — this closes a gap noted as open at the end of the prior
  round.
- `sounddevice` availability was **not** confirmed until this round; it was
  found missing (not a transitive dependency of `livekit`/`livekit-api`)
  and has now been installed and the exact version pinned (§4) — this also
  closes a previously-open gap.
- The reSpeaker's native/default sample rate (16000Hz per PortAudio's own
  enumeration) vs. the PoC's requested 48000Hz stream rate is an
  **unresolved, real risk** — whether PortAudio's ALSA hostapi transparently
  handles this for this specific USB Audio Class 1.0 device is unknown
  until the actual hardware test runs.
- `MediaDevices` is a lower-level, `sounddevice`/PortAudio-backed helper,
  architecturally distinct from `PlatformAudio` (the API more likely to
  represent the real target production architecture, §9) — a PoC PASS via
  `MediaDevices` would still leave open whether `PlatformAudio`'s
  PipeWire-mediated path behaves the same way; this is a genuine, stated
  scope limitation of this PoC's chosen approach, not swept aside.
- No hardware test has been run. All AEC-effectiveness and stability
  claims in this report are about **code-path existence**, not measured
  outcomes.

## 16. Risks

- If the sample-rate mismatch (16kHz native vs. 48kHz requested) causes
  PortAudio to fail or silently degrade capture quality, the first hardware
  run may need a `MediaDevices(input_sample_rate=16000, ...)` variant or an
  explicit resampling step — not yet designed, would require a follow-up
  round.
- A PASS result via `MediaDevices` does not by itself validate `R0083`'s
  `PlatformAudio`-based target architecture (§9's stated trade-off) — a
  second confirmatory PoC through `PlatformAudio` (with an actual local or
  embedded LiveKit room) may be needed before R0083 is greenlit, which is
  additional scope beyond what's built this round.
- `MediaDevices` bypasses PipeWire and talks to ALSA directly, in the same
  general spirit as R0081's own `aplay`-based approach — if the residual
  instability R0081 found is rooted in the ALSA/USB/driver layer rather
  than in the *manual reference-feed architecture* specifically, this PoC's
  `MediaDevices`-based result could still reproduce instability, in which
  case the more informative comparison would come from a `PlatformAudio`+
  PipeWire test instead. This is a real, not yet ruled out, confound in the
  PoC's own experimental design.

## 17. Decisions made this round

- Chose `MediaDevices` over `PlatformAudio` for the first PoC (§9),
  explicitly flagged as a scope-limiting choice, not a final architecture
  decision.
- Chose raw ALSA hw-device name substrings (`"reSpeaker"`, `"UACDemoV1.0"`)
  over the `/etc/asound.conf` alias names (`respeaker`, `usb_speaker`) as
  the default match targets, since both resolve unambiguously and the raw
  hw device avoids depending on `/etc/asound.conf`'s own aliasing (a
  system-level config file outside this PoC's own control) — this is a
  reasoned choice, not yet empirically compared against the alias-based
  alternative.
- Did not install `livekit`/`sounddevice` into NeXa's own `.venv` (§4) —
  keeps this spike's dependency surface fully isolated, per established
  project precedent.

## 18. Verdict — R0082-A audit phase only

**R0082-A: PASS.** All 9 audit items from the brief were determined from
actual installed source and/or real, safe, read-only hardware probes — none
were assumed or invented from memory. The architecture question (Pi
edge/frontend vs. NeXa agent/backend) is resolved with direct source
evidence (§3). A minimal, offline-validated (`py_compile` + `ruff`
clean) R0082-B PoC script exists and is ready to run.

**R0082-B (the actual hardware AEC A/B test): NOT YET RUN.** No verdict is
given for R0082-B or for R0082 overall — this explicitly awaits the
operator's real hardware test, run only after this report's review, per
instruction.

## 19. Minimal command for the first real R0082 hardware test (NOT run by this session)

```bash
# 1. AEC OFF baseline
/path/to/isolated/r0082_livekit_probe_venv/bin/python3 \
    docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py \
    --aec off --duration 30 --stimulus tones

# 2. AEC ON, immediately after, same physical conditions
/path/to/isolated/r0082_livekit_probe_venv/bin/python3 \
    docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py \
    --aec on --duration 30 --stimulus tones
```

Run with the isolated probe venv's Python (§4's install command), **not**
NeXa's own `.venv`. Operator should monitor CPU/RAM (e.g. `top` in a second
terminal) during each run per §13. This has intentionally **not** been run
by this session.
