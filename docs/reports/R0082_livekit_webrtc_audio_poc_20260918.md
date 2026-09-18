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
  — the R0082-B minimal audio-only PoC script. Imports `build_signal`/
  `_peak`/`_rms`/`_write_wav` exclusively from this directory's own
  `r0082_audio_utils.py` (§12a — corrected after the first hardware
  attempt found these were originally reused via `sys.path` from R0081's
  scripts, one of which had an accidental `nexa`/`loguru` runtime
  dependency). `py_compile`-clean (isolated probe venv) and
  `ruff check`-clean (NeXa's own `.venv/bin/ruff`).
- `docs/research/r0082_livekit_webrtc_audio_poc/r0082_audio_utils.py` —
  new (§12a), standard-library-only (`array`, `math`, `struct`, `wave`,
  `pathlib`) verbatim copies of `build_signal`/`build_test_signal`/
  `build_mls_signal`/`_rms`/`_peak`/`_write_wav`, confirmed byte-for-byte
  identical output to the originals (§12a item 7).
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

## 12a. First hardware attempt: NOT RUN — import-time isolation defect found and fixed

The operator's first real R0082-B command (`--aec off --duration 30
--stimulus tones`, run with the isolated probe venv's Python per §19)
**did not reach hardware access.** It failed immediately during Python
imports:

```
ModuleNotFoundError: No module named 'loguru'
```

**Import chain:** `r0082_platform_audio_aec_poc.py` ->
`r0081_direct_aec_diagnostic.build_signal` -> `nexa.voice.aec_gain` ->
`nexa.voice.__init__` -> `nexa.voice.bargein` -> `loguru`.

**Root cause:** the PoC script reused `build_signal` from R0081's own
`r0081_direct_aec_diagnostic.py`, which imports `from nexa.voice.aec_gain
import apply_gain` at module scope. Importing `nexa.voice.aec_gain`
triggers `nexa.voice`'s own `__init__.py`, which imports
`nexa.voice.bargein`, which imports `loguru` — a NeXa production runtime
dependency that is deliberately **not** installed in R0082's isolated
probe venv (installing it, or the rest of NeXa's runtime surface, into
that venv would defeat the isolation R0082 exists to test).

This is **not** a hardware failure, not an AEC failure, not a
sample-rate failure, and not a LiveKit failure — it is a dependency-
boundary defect found before any hardware access was attempted, on the
very first invocation.

**Fix:** extracted the minimal deterministic diagnostic primitives this
PoC actually needs (`build_signal`/`build_test_signal`/`build_mls_signal`,
`_rms`, `_peak`, `_write_wav`) into a new, standard-library-only local
module, `docs/research/r0082_livekit_webrtc_audio_poc/r0082_audio_utils.py`
(uses only `array`, `math`, `struct`, `wave`, `pathlib`). Every function
is a **verbatim copy** of the existing R0081/`m2_6b4m_self_echo_probe.py`
algorithm — not a redesign — with provenance comments in the new module
explaining exactly why the copy exists. `m2_6b4m_self_echo_probe.py`
itself was separately audited (§12b) and found to already be safe at
module scope; only `r0081_direct_aec_diagnostic.py`'s `nexa.voice.aec_gain`
import was the actual defect. The PoC script now imports exclusively from
its own local `r0082_audio_utils.py`, never from either R0081 helper
script.

**Validated in the actual isolated R0082 venv** (all 9 items from the
correction request):

1. Importing `r0082_platform_audio_aec_poc.py` succeeds — confirmed.
2. `--help` succeeds — confirmed (full usage text prints, no import error).
3. `py_compile` succeeds for both the PoC script and the new utility
   module — confirmed.
4. `ruff check` succeeds (0 errors) for the whole `r0082_livekit_webrtc_audio_poc/`
   directory — confirmed.
5. `git diff --check` succeeds (no whitespace issues) — confirmed.
6. Importing the PoC does **not** load `nexa`, `pipecat`, `loguru`, or
   Silero modules, and does not load the Gemini SDK (`google.genai`/
   `google.generativeai`) — confirmed by inspecting `sys.modules` after
   import. **One clarification, not a violation:** `google.protobuf` (and
   its `_upb` C-extension backend) IS loaded — this is `livekit`'s own
   direct dependency (WebRTC/gRPC signaling uses protobuf as a wire
   format) and is unrelated to Gemini/GenAI; `google.genai` and
   `google.generativeai` are confirmed absent from `sys.modules`.
7. Signal generation is byte-for-byte identical to the previous R0081
   tone/MLS builders — confirmed directly (old script run in NeXa's own
   `.venv`, where its `nexa.*` imports resolve, compared byte-for-byte
   against the new module run in the same interpreter): `tones` at
   duration 3.0/amplitude 0.05/sample_rate 48000 identical; `mls` at
   duration 1.0/amplitude 0.05/sample_rate 48000 identical; `tones` at
   duration 3.0/amplitude 0.5/sample_rate 16000 (R0081's own original
   default parameters) identical; both old and new raise the identical
   `ValueError` message when `mls`'s duration/rate combination would
   exceed the MLS period (a deliberately preserved guard, not a
   discrepancy).
8. `_rms`/`_peak` return known expected values on synthetic PCM
   (constant-1000 int16 -> rms=peak=1000.0/1000; full-scale square wave
   -> rms=peak=32767.0/32767; empty PCM -> 0.0/0) — confirmed, run in the
   isolated probe venv.
9. `_write_wav` produces a valid mono, 16-bit, PCM S16_LE WAV (verified
   by reading it back with the stdlib `wave` module and confirming
   channels=1, sample width=2, frame rate as requested, and frame data
   round-trips exactly) — confirmed, run in the isolated probe venv.

No real speaker/microphone playback was run as part of this correction,
per instruction.

## 12b. `m2_6b4m_self_echo_probe.py` audit — no fix needed there

Audited whether `m2_6b4m_self_echo_probe.py` (the source of the
previously-reused `_rms`/`_peak`/`_write_wav`) also pulls in NeXa/Pipecat/
runtime dependencies at import time. Its module-level imports are stdlib +
`numpy` only (`argparse, asyncio, json, math, os, statistics, struct, sys,
time, traceback, wave`, `dataclasses`, `datetime`, `pathlib`, `typing`,
`numpy`) — every `nexa.*`/`pipecat.*` import in that file lives inside
deferred functions (`_pipecat_imports()`, and similar function-scoped
imports used only by its own live-hardware code paths), never at module
scope. So this file was **not** the cause of the `loguru` failure, and by
itself would have been safe to import from. It was still replaced (§12a)
so the new `r0082_audio_utils.py` has zero external dependencies at all
(not even `numpy`), for the strongest possible isolation guarantee, and so
the PoC's dependency surface is fully stated in one place.

## 12c. Second real hardware attempt: reached devices, FAILED before playback — sample-rate mismatch

With the isolation fix (§12a) applied, the operator's next real command
(`--aec off --duration 30 --stimulus tones`) **reached real hardware for
the first time** — device enumeration and selection both succeeded and
printed the correct real devices:

```
input:  index=1  'reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0)'
output: index=0  'UACDemoV1.0: USB Audio (hw:2,0)'
```

`MediaDevices.open_input()` then failed:

```
sounddevice.PortAudioError: Error opening InputStream: Invalid sample rate [PaErrorCode -9997]
```

A secondary `FfiHandle.__del__` `AssertionError` followed during garbage
collection (audited in §12e — a consequence of the primary failure, not
an independent defect).

### Classification

```
LiveKit import/API                PASS
aarch64 compatibility             PASS
device enumeration                PASS
physical reSpeaker detection      PASS
physical USB speaker detection    PASS

raw reSpeaker hw:3,0 @ 48kHz      FAILS TO OPEN
WebRTC AEC effectiveness          NOT YET TESTED
R0082-B hardware A/B              NOT YET RUN
```

This is **not** an AEC failure, LiveKit failure, WebRTC failure, or
speaker failure. No deterministic playback/AEC measurement occurred.

## 12d. Safe capability probe (no playback) — confirms the ALSA plug-alias hypothesis

`/etc/asound.conf` defines the `respeaker` PCM as an ALSA `type plug`
wrapper around the physical XVF3800 device (`hw:3,0`) — a `plug` PCM
performs transparent rate/format conversion. §5.2's own enumeration had
already shown the raw hw device and the `respeaker` alias both report
`default_samplerate=16000.0` via PortAudio, but a `default_samplerate`
field reflects the *preferred* rate, not the full set of rates a device
can be *opened* at — so this needed a real, non-streaming capability
check, not an assumption.

Using `sounddevice.check_input_settings()` / `check_output_settings()`
(validates a proposed stream configuration against the backend without
opening a stream — no playback, no capture) against a fresh enumeration
(device indices confirmed stable across this and the prior round: input
device index=1 raw hw, index=3 alias; output device index=0):

```
INPUT

raw hw reSpeaker (index=1, "reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0)")
  16k mono    PASS
  16k stereo  PASS
  48k mono    FAIL -- PortAudioError: Invalid sample rate [PaErrorCode -9997]
  48k stereo  FAIL -- PortAudioError: Invalid sample rate [PaErrorCode -9997]

ALSA plug "respeaker" (index=3, exact alias name)
  16k mono    PASS
  16k stereo  PASS
  48k mono    PASS
  48k stereo  PASS

OUTPUT

UACDemoV1.0 (index=0, "UACDemoV1.0: USB Audio (hw:2,0)")
  48k mono    PASS
  48k stereo  PASS
```

**Confirmed:** the raw hw device is hardware-limited to 16kHz; the ALSA
`plug:respeaker` alias accepts 48kHz because ALSA's own `plug` layer
rate-converts below PortAudio. The output device (UACDemoV1.0) already
accepts 48kHz natively — no alias needed there.

### `media_devices.py` source audit — answers to all 5 questions, none assumed

Read directly from the installed
`livekit/rtc/media_devices.py` (`MediaDevices.open_input`/`open_output`,
`OutputPlayer._callback`):

1. **How is `input_sample_rate` used for the physical PortAudio
   InputStream?** Passed directly, unmodified, as `samplerate=self._in_sr`
   to `sd.InputStream(...)`. No negotiation, clamping, or fallback —
   if the chosen device index cannot supply that literal rate, PortAudio
   raises immediately at stream construction (exactly what was observed).
2. **What sample rate is supplied to the `AudioProcessingModule`?** Every
   captured frame is constructed as `AudioFrame(..., sample_rate=self._in_sr,
   ...)` before `apm.process_stream(frame)`; every rendered frame fed to
   the reverse stream is constructed as `AudioFrame(render_chunk.tobytes(),
   self._sample_rate, 1, FRAME_SAMPLES)` (`self._sample_rate` = the
   `OutputPlayer`'s own configured rate, `self._out_sr` from
   `MediaDevices`) before `apm.process_reverse_stream(render_frame)`.
   `apm.py`'s own `process_stream`/`process_reverse_stream` each pass the
   frame's own `sample_rate` field independently to the native FFI call
   per direction.
3. **May input and reverse/render streams have different physical sample
   rates?** At the Python API level, `MediaDevices(input_sample_rate=A,
   output_sample_rate=B)` allows setting them independently, and each
   is threaded to its own stream/APM calls without reconciliation. Since
   the PoC sets both to the SAME value (48000), this is moot for R0082 —
   whether the native WebRTC APM binding tolerates genuinely different
   forward/reverse rates was not tested (not needed; flagged as an open,
   unexercised question, not resolved).
4. **Does `MediaDevices` perform any internal resampling?** No —
   confirmed by reading every line between the `sd.InputStream`/
   `sd.OutputStream` callbacks and their respective `AudioFrame`
   constructions: raw PCM bytes flow through unchanged. Any rate
   conversion must happen below PortAudio (i.e., in ALSA, as confirmed
   in §12d's capability probe for the `respeaker` alias) or not at all.
5. **Does the APM accept 16kHz capture with 48kHz render directly, or
   expect one common processing rate?** Not resolved by this module's
   source alone (per point 3) — moot for this PoC's own fix, since
   keeping `input_sample_rate=output_sample_rate=48000` (achieved by
   selecting the `respeaker` alias for input, §12e) avoids the question
   entirely rather than answering it.

## 12e. Fix applied — exact-match device selection targets the ALSA alias, uniform 48kHz preserved

Per the capability-probe result (§12d), the smallest correct fix keeps
`MediaDevices(input_sample_rate=48000, output_sample_rate=48000)`
unchanged (so the WebRTC APM stays at one uniform 48kHz on both forward
and reverse paths, avoiding a new resampling/alignment confound) and
changes ONLY input-device *resolution*:

- **Selection semantics fixed** in `_find_device_index()`: now tries an
  EXACT case-insensitive full-name match first, falling back to substring
  matching (previous behavior, `"default: "`-prefix preferred if
  ambiguous) only when no exact match exists. This was necessary because
  a plain substring search for `"respeaker"` matches BOTH the alias
  (`"respeaker"`, exact) AND the raw hw device
  (`"reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0)"`, substring
  only) — the two are not interchangeable (§12d), so exact-match-first
  is required to deterministically prefer the alias when its exact name
  is requested.
- **`DEFAULT_INPUT_NAME_SUBSTRING` changed from `"reSpeaker"` to
  `"respeaker"`** (the literal ALSA alias name) so the script's own
  default — not just an explicit `--input-name` override — resolves
  correctly without requiring the operator to always pass the flag.
  `DEFAULT_OUTPUT_NAME_SUBSTRING` (`"UACDemoV1.0"`) is unchanged — its
  only match was already unambiguous and already 48kHz-capable.
- **Clearer error path added**: `media.open_input()` is now wrapped to
  catch `sounddevice.PortAudioError` specifically and re-raise as a
  `SystemExit` with a message that correctly attributes the failure to
  a device sample-rate/capability mismatch (not AEC/LiveKit/WebRTC), and
  points at the capability-probe methodology (§12d) for diagnosis.

## 12f. `FfiHandle.__del__` `AssertionError` — audited, not a distraction, no further fix warranted

`FfiHandle.dispose()` (installed `livekit/rtc/_ffi_client.py`) calls the
native `livekit_ffi_drop_handle` and asserts the drop succeeded; `__del__`
calls `dispose()`. Traced the object lifecycle: inside
`MediaDevices.open_input()`, the `AudioSource` and (when any processing
flag is enabled) `AudioProcessingModule` — both FFI-handle-backed — are
constructed as **local variables inside `open_input()` itself**, before
`sd.InputStream(...)` is ever called. When `sd.InputStream(...)` raised
`PortAudioError`, `open_input()`'s stack frame unwound without returning
anything to this PoC's own code, and CPython's refcounting GC collected
those now-unreferenced local objects essentially immediately — triggering
`FfiHandle.__del__` → `dispose()` on handles associated with an
input-stream setup that never completed, hence the assertion.

**Conclusion: no explicit dispose/close fix belongs in the R0082 PoC for
this specific symptom.** The objects that fail to clean up are created
and destroyed entirely *inside* the installed third-party SDK's own
`open_input()` method, before it returns anything to this script — there
is no reference in the PoC's own scope to explicitly dispose. The
`except sd.PortAudioError` handler added in §12e (which produces a clear,
correctly-attributed diagnostic instead of a raw traceback) is the
appropriate and sufficient response available at this layer; this was
deliberately not allowed to distract from fixing the primary sample-rate
failure.

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
clean, and now dependency-isolation-clean per §12a) R0082-B PoC script
exists and is ready to run.

**First hardware attempt: NOT RUN.** Reason: an import-time isolation
defect was found before hardware access — the PoC transitively imported
`loguru` (NeXa production runtime dependency) via
`r0081_direct_aec_diagnostic.py`'s own `nexa.voice.aec_gain` import (full
chain and fix: §12a). This is explicitly **not** a hardware failure, not
an AEC failure, not a sample-rate failure, and not a LiveKit failure —
it was caught, diagnosed, and fixed entirely offline, before any device
access was attempted. The fix has been validated against all 9 items
requested (§12a) in the actual isolated R0082 venv.

**Second real hardware attempt (with the isolation fix applied):**
device enumeration and selection reached real hardware and succeeded
(§12c). `open_input()` then **FAILED BEFORE PLAYBACK** because the raw
reSpeaker hw device (`hw:3,0`) rejects 48kHz capture
(`PaErrorCode -9997`).

**Root cause of this attempt:** physical capture-endpoint sample-rate
mismatch — the raw ALSA hw device is hardware-limited to 16kHz; the
`MediaDevices` API performs no resampling of its own (§12d), so it
requires a device that can natively (or via a lower-layer wrapper) supply
the literal rate requested (48000). Confirmed, not an AEC/LiveKit/WebRTC/
speaker failure (classification table, §12c).

**Fix applied (§12e):** device-selection semantics now resolve an exact
case-insensitive name match before falling back to substring matching,
and the script's default input target now points at the ALSA
`respeaker` plug alias (`/etc/asound.conf`'s own `type plug` wrapper
around the same physical device), which a safe, non-streaming capability
probe (§12d) confirmed accepts 48kHz mono/stereo — preserving one uniform
48kHz `MediaDevices`/WebRTC-APM processing rate on both the forward and
reverse path, with no new resampling logic added to the PoC itself.

**AEC effectiveness: NOT YET TESTED.** No verdict is given for R0082-B or
for R0082 overall — this still awaits the operator's real hardware test,
now with the corrected device-selection PoC script, run only after this
report's review.

## 19. Minimal command for the first real R0082 hardware test (NOT run by this session)

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082_platform_audio_aec_poc.py \
  --aec off --duration 30 --stimulus tones \
  --input-name respeaker
```

Run with the isolated probe venv's Python (§4's install command), **not**
NeXa's own `.venv`. `--input-name respeaker` selects the ALSA plug alias
by exact name (§12e) — this now also matches the script's own new
default, but is passed explicitly for clarity given this round's finding.
Operator should monitor CPU/RAM (e.g. `top` in a second terminal) during
the run per §13. **Do not run `--aec on` yet** — that command is
intentionally withheld until this `--aec off` run is confirmed to open
the real devices and complete successfully. This has intentionally
**not** been run by this session.

## 20. Real R0082-B AEC OFF → ON hardware result

The operator ran the `--aec off` command from §19, then `--aec on`
immediately after, same physical conditions.

### AEC OFF — run id `20260918T100821Z`

```
quiet_before_rms = 72.6
window RMS: 113.3 135.0 129.1 142.8 202.3 180.4 151.7 465.1 517.4 422.6
overall mean = 245.97   first-half mean = 144.50   second-half mean = 347.44
min/max = 113.30 / 517.40   max/min = 4.57x
```

One warning during this run: `AudioMixer: stream ... timeout, ignoring`.
The run completed all 10 windows and saved WAV evidence.

### AEC ON — run id `20260918T101009Z`

```
quiet_before_rms = 39.1
window RMS: 48.8 59.2 62.2 52.6 45.1 41.7 43.2 233.5 193.6 157.6
overall mean = 93.75   first-half mean = 53.58   second-half mean = 133.92
min/max = 41.70 / 233.50   max/min = 5.60x
```

No `AudioMixer` timeout in the ON run. Both runs produced the previously
documented `FfiHandle.__del__` `AssertionError` cleanup warning after
result/WAV output (§12f — audited, expected, harmless).

## 21. Quantitative interpretation — independently verified

Every figure below was recomputed independently from the raw window RMS
values (not merely copied from the operator's report) and confirmed
exact:

```
AEC ON vs OFF:
  overall:     245.97 -> 93.75   -61.9%   ≈ -8.38 dB
  first half:  144.50 -> 53.58   -62.9%   ≈ -8.62 dB
  second half: 347.44 -> 133.92  -61.5%   ≈ -8.28 dB
```

AEC ON is lower than AEC OFF in **every one of the 10 corresponding
windows** (verified directly, window by window).

**WebRTC AEC leakage reduction: CONFIRMED.** This is described as
observed residual/leakage reduction from raw microphone RMS, not a
formally-derived ERLE figure (ERLE requires a controlled decomposition
of echo vs. near-end signal this measurement does not provide).

## 22. Stability result

```
AEC OFF: second/first half = 347.44/144.50 = 2.404x ≈ +7.62 dB   max/min = 4.57x
AEC ON:  second/first half = 133.92/53.58  = 2.499x ≈ +7.96 dB   max/min = 5.60x
```

**AEC reduces absolute leakage: PASS. AEC fixes time-dependent residual
instability: FAIL / NOT DEMONSTRATED.** The late-run deterioration
remains in both runs (OFF: 465.1/517.4/422.6 in windows 8–10; ON:
233.5/193.6/157.6 in windows 8–10). The two runs are not claimed to
share an identical mechanism merely because the shape resembles each
other — but the same broad late-run deterioration class is present with
AEC both disabled and enabled, and the aggregate first-half→second-half
degradation is strikingly similar (~+7.6 dB vs. ~+8.0 dB). §24 below
adds an important new finding bearing directly on what this
"late-run deterioration" actually is.

## 23. Quiet-floor caveat

```
OFF quiet_before_rms = 72.6
ON  quiet_before_rms = 39.1
```

The two runs do not share an identical baseline noise floor — this is
material and is not glossed over. However: AEC ON is lower in all 10
playback windows; first-half and second-half reductions are both
~8 dB; and the late-run instability remains in both. The quiet-floor
difference is an important caveat but does not erase the observed
attenuation result. No stronger causal claim than this is made.

## 24. Offline WAV sanity check

All four WAV files were inspected directly (stdlib `wave`/`struct` only
— no OCR, no transcription, no speech recognition; deterministic
non-speech PCM) without modification.

### Header properties (items 1–5)

```
r0082_aecoff_..._tones_mic.wav:    mono, 16-bit, 48000Hz, 1536480 frames, 32.010s
r0082_aecoff_..._tones_signal.wav: mono, 16-bit, 48000Hz, 1440000 frames, 30.000s
r0082_aecon_..._tones_mic.wav:     mono, 16-bit, 48000Hz, 1536480 frames, 32.010s
r0082_aecon_..._tones_signal.wav:  mono, 16-bit, 48000Hz, 1440000 frames, 30.000s
```

All match expectations exactly: 48kHz mono 16-bit throughout; mic
duration 32.010s ≈ `PRE_ROLL_S(1.0) + duration_s(30.0) + TAIL_S(1.0)`
(item 6, confirmed present in full); signal duration exactly 30.000s.

### Recomputed per-window RMS/peak from raw PCM (cross-check)

Every reported window RMS value was recomputed directly from the raw
WAV bytes using the exact same `_rms`/`_peak` primitives the PoC script
itself uses (`r0082_audio_utils.py`), independent of the script's own
in-process computation. **Every value matched exactly** — the reported
numbers are genuinely sourced from the raw captured PCM, not
miscomputed or fabricated. Peak values are all well below int16
saturation (max observed peak 2474 of 32767) — no clipping in either
run.

### Zero-run / dropout scan (items 7–8)

Scanned all four files for runs of ≥200 consecutive exact-zero samples
(≥~4.2ms at 48kHz — a dropout/underrun indicator):

```
OFF mic:    0 runs
ON mic:     2 runs, BOTH within the first 1.0s pre-roll (at t=0.000s,
            7.6ms; and t=0.400s, 8.9ms) — entirely outside every
            analysis window, a capture-startup transient with zero
            effect on any reported statistic
OFF signal: 0 runs
ON signal:  0 runs
```

No dropout/zero-fill artifact correlates with the OFF run's printed
`AudioMixer: stream ... timeout, ignoring` warning — the mic capture
around that event (relative timing not precisely known, but the full
OFF mic file has zero qualifying zero-runs anywhere) shows no visible
discontinuity. Consistent with the warning being a harmless,
already-self-described "ignored" event in the render mixer with no
effect on the captured PCM.

### NEW FINDING (item 9): the late-window rise aligns with the tone-segment transition, not simply with elapsed time

`build_test_signal` (`r0082_audio_utils.py`, verbatim copy of R0081's
algorithm) plays three sequential 10-second tones — 500Hz, 1000Hz,
2000Hz — at **identical amplitude** (verified: both signal WAVs have
exactly RMS=1156.5, peak=1638 in EVERY one of the three 10s segments,
zero variation). Mapped onto the mic recording's absolute timeline
(pre-roll ends, signal starts, at t=1.0s):

```
tone1 (500Hz):  t=1.0s-11.0s   -> windows 1-3 (+ start of 4)
tone2 (1000Hz): t=11.0s-21.0s  -> windows 4-7
tone3 (2000Hz): t=21.0s-31.0s  -> windows 8-10
```

A fine-grained 0.5s-resolution RMS trace across the FULL raw mic
recording (both runs) shows the rise is **not** a smooth ramp from the
start of the recording — it is a sharp step aligned, within one 0.5s
sub-window, with the tone1→tone2 and especially the tone2→tone3
transition:

```
OFF mic: t=20.5s rms=156.8 -> t=21.5s rms=178.9 -> t=22.0s rms=440.0  (step at the 2000Hz onset)
ON  mic: t=20.5s rms= 41.1 -> t=21.0s rms= 40.2 -> t=21.5s rms= 57.2 -> t=22.0s rms=219.4  (same step)
```

This directly answers item 9: the late window-8+ rise **is** present in
the raw waveform (not an artifact of offline windowing/indexing), but
its onset coincides with a change in the stimulus's own frequency
content, not simply with elapsed playback time. Because the reference
signal itself is amplitude-uniform across all three segments (confirmed
above), the mic-side rise cannot be explained by a louder driving
signal — and because the **same** onset-aligned rise appears in the OFF
run, which has no AEC/adaptive filter to "reconverge," the effect
cannot be solely an AEC-adaptation artifact either. This points to a
frequency-dependent acoustic/mechanical/electrical response (e.g. the
USB speaker's own frequency response, room/enclosure resonance, or mic
pickup frequency response at 2000Hz vs. 500/1000Hz) contributing
substantially to what had been described as a purely
time/stream-age-dependent effect.

One additional, unexplained secondary observation: in the ON run, RMS
within the tone3 segment partially recovers (drops back to ~37-45)
starting at t≈29.5s, 1.5s *before* the segment itself ends at t=31.0s;
the OFF run's tone3 segment does not show this same early drop (stays
elevated until the tail region begins at t=31.0s). This asymmetry is
noted but not explained — possibly AEC re-adaptation within the segment
— and is flagged as a secondary open question, not a central finding.

**This does not invalidate the AEC attenuation result** (§21 — that
conclusion is independent of this confound: AEC ON is lower than OFF in
every window regardless of which tone segment produced the level). It
**does** mean the "stability"/"time-dependent instability" framing (§22)
is confounded with stimulus frequency content, and this was not
previously controlled for. The clear disambiguation experiment is a
repeat run with `--stimulus mls` (already available in
`r0082_audio_utils.py` — broadband, no discrete frequency segments): if
the late-region rise persists, that supports genuine time/stream-age
dependence; if it disappears or flattens, that supports the
frequency-dependent-response hypothesis found here. This is recorded as
an important open item for a future round, not resolved this round, and
is not used to override the verdict table the next section records.

## 25. R0082-B verdict

```
R0082-A audit                        PASS
R0082-B device/sample-rate path      PASS after alias fix
WebRTC AEC execution                 PASS
WebRTC AEC residual reduction        CONFIRMED
Time stability requirement           FAIL / NOT MET   (historical — see §25a correction)
MediaDevices/PortAudio PoC           PARTIAL PASS
Production migration                 NOT APPROVED
Gemini integration                   NOT YET
```

R0082 overall is not yet a full PASS. This table is preserved exactly as
originally recorded — the `FAIL / NOT MET` entry is **not deleted** —
but §25a below supersedes it as the current interpretation.

### 25a. Interpretation correction (this round)

```
WebRTC AEC attenuation:       CONFIRMED
time stability:                INCONCLUSIVE from the old segmented-tones experiment

frequency-content confound:   CONFIRMED
exact physical mechanism:     OPEN
true time drift:              NOT YET RESOLVED
```

§24's WAV sanity check found the `tones` stimulus's own sequential
500→1000→2000Hz segments were confounded with the "late-run rise" — the
old `FAIL / NOT MET` verdict cannot be read as an independent
demonstration of true elapsed-time/stream-age instability, because the
experiment that produced it could not distinguish a time effect from a
frequency-content effect. This is **not** a claim that the late rise was
definitely caused by frequency response either — the exact physical
mechanism remains open, and whether genuine time drift exists at all is
not yet resolved either way. §26 onward (the new `stationary_multitone`
stimulus) exists specifically to remove this confound before the real
`PlatformAudio` A/B is run, so the next result can legitimately settle
this question.

## 26. R0082-C — architecture audit (before build)

Per the brief: PlatformAudio + PipeWire + a real LiveKit room is now the
highest-priority next test, because R0082-B (`MediaDevices`/PortAudio/
direct-ALSA) proved AEC attenuation works but did not eliminate the
late-region rise, and the target NeXa architecture is PipeWire/WebRTC-ADM
via `PlatformAudio`, not PortAudio/ALSA. Audited the actual installed
`livekit` 1.1.19 source (`livekit/rtc/platform_audio.py`, 427 lines) —
nothing below is assumed from docs or memory.

**1–2. Binding/selecting the reSpeaker (recording) / UACDemoV1.0
(playout) devices.** `PlatformAudio.set_recording_device(device_id)` /
`set_playout_device(device_id)` both require a real, non-empty `id`
(GUID) string per their own docstrings ("Use the ID rather than index
for stable device selection"). R0082-A already found `AudioDeviceInfo.id`
is **always an empty string** on this Linux/PipeWire system. This
round tested empirically (safe — neither call opens a stream per
source): `set_recording_device("")` raises no exception but is almost
certainly a no-op (there is no way to distinguish "selected the empty-id
device" from "silently ignored"); `set_recording_device(<device name>)`
(passing the enumerated NAME instead of an id, as a probe) raises
`PlatformAudioError: Failed to set recording device: Device not found`
— confirming the native implementation does **not** fall back to
name-based matching. **Conclusion: on this platform/SDK version, neither
target device can be explicitly force-selected via this API.**
Fortunately, `pactl info` confirms PipeWire's own CURRENT default sink
is already `alsa_output.usb-Jieli_Technology_UACDemoV1.0_...` and
default source is already
`alsa_input.usb-Seeed_Studio_reSpeaker_XVF3800_...` — i.e. `PlatformAudio`
with **no** explicit selection call already resolves to exactly the
target devices via PipeWire's own default-device mechanism (confirmed:
`recording_devices()`/`playout_devices()`'s own `index=0`,
`"default: "`-prefixed entries name the reSpeaker/UACDemoV1.0
respectively). The R0082-C PoC does not call
`set_recording_device`/`set_playout_device` at all; instead it verifies
(`_verify_default_device()`) that the current default still matches
before proceeding, refusing to run otherwise.

**3. `PlatformAudioOptions` → WebRTC ADM/APM mapping.**
`PlatformAudioOptions._to_proto()` maps `echo_cancellation`,
`noise_suppression`, `auto_gain_control`, `prefer_hardware` directly,
1:1, unmodified, onto `proto_audio_frame.AudioSourceOptions`, sent over
FFI to the native binding at `create_audio_source()` call time. No
Python-side transformation. The native binding's own internal algorithm
selection is not inspectable from Python source (compiled
`liblivekit_ffi.so`), but the request wiring itself is unambiguous and
direct.

**4. AEC OFF/ON toggle.** Confirmed straightforward:
`create_audio_source(PlatformAudioOptions(echo_cancellation=False))` vs.
`(echo_cancellation=True)`, called fresh per run — same off/on pattern
as R0082-B.

**5. Does the reverse/render path require an actual published/
subscribed room track?** **Yes, confirmed from source.** Unlike
`MediaDevices` (which exposes a genuine local-loopback `open_output()`
with no room needed, R0082-B §6), `PlatformAudio` has **no** equivalent
local playout API — its own docstring's "Automatic playout" feature is
described only in terms of "Received audio is automatically played
through speakers," and grepping the entire installed SDK found zero
references to `PlatformAudio` anywhere in `room.py`/`RoomOptions` — the
wiring is native/internal, triggered by actual remote-track subscription
inside a real `Room`. There is no way to feed `PlatformAudio`'s AEC
far-end reference without a genuine room publish+subscribe round trip.
This directly confirms the user's own hypothesis and the two-participant
topology requirement.

**6–7. Smallest local topology / existing server availability.**
**No `livekit-server` binary, no Docker/Podman, no `livekit-cli`** were
present anywhere on this machine (checked `which`, `find`, `docker info`
— all negative). The official single static Go binary (Apache-2.0,
`github.com/livekit/livekit`, release `v1.13.7`, `linux_arm64`) was
downloaded into this session's own scratchpad (NOT this repo, NOT
system-wide, NOT `sudo`) and confirmed working:

```bash
curl -sL -o livekit_1.13.7_linux_arm64.tar.gz \
    https://github.com/livekit/livekit/releases/download/v1.13.7/livekit_1.13.7_linux_arm64.tar.gz
tar -xzf livekit_1.13.7_linux_arm64.tar.gz
./livekit-server --dev --bind 127.0.0.1
```

`--dev` mode auto-generates placeholder credentials (`devkey`/`secret`),
binds to `127.0.0.1` only (no external exposure), and needs no Redis or
config file — confirmed via a real startup log (HTTP port 7880, RTC TCP
7881, RTC UDP 7882+) and a real end-to-end synthetic room
connect→publish→disconnect round trip (token generated via
`livekit.api.AccessToken`, `Room.connect()`, `publish_track()`, clean
disconnect, zero errors) — this is the smallest viable local topology,
requiring no external account, no cloud dependency, no Docker.

**8. Cleanup/disconnect semantics.** From source docstrings:
`PlatformAudioSource.close()` (sync) and `PlatformAudio.close()` (sync)
must be called in that order ("Always close PlatformAudioSource
instances before closing the parent PlatformAudio instance"). For the
synthetic side, `rtc.AudioSource.aclose()` is **async** — a real bug was
found and fixed this round (§28) where the first PoC draft looked for a
`close()` method (absent on `AudioSource`) instead of `aclose()`,
silently leaking the handle and producing `FfiHandle` `AssertionError`s
on an otherwise-successful run; fixed and re-validated clean.

**9. Sample-rate/device behavior exposed through PipeWire.** From
source: `create_audio_source()`'s FFI request always sets
`sample_rate=48000, num_channels=1`, but the surrounding comment states
plainly: "For platform audio, the ADM determines the actual sample rate
and channels. These fields are ignored." Unlike `MediaDevices`/PortAudio
(R0082-B, which went **directly** to ALSA and hit a real 48kHz rejection
on the raw hw device), `PlatformAudio` goes through **PipeWire**
(R0082-A), and PipeWire — as a general-purpose audio server — is
expected to perform its own rate/format negotiation and conversion
transparently for any client, regardless of a physical device's native
rate. This is a reasonable **expectation**, not yet an empirically
confirmed fact for the reSpeaker's real 16kHz-native hardware — the
first real hardware run (§30's command) will confirm or refute it. If
`PlatformAudio` DOES hit an analogous rate rejection, that would be a
significant, unexpected finding in its own right.

**10. CPU/RAM observability.** Same as R0082-B: not measured by the
script itself; operator monitors separately (`top`/`ps`). Since R0082-C
now runs BOTH participants in one process (§27's design decision), this
is simpler than a two-process setup — one PID to watch for the PoC
script, plus the separate `livekit-server` process.

## 27. R0082-C — local server setup and real (non-hardware) end-to-end validation

Beyond static checks, this round additionally validated the **real room
mechanics** — token generation, connect, publish, `track_subscribed`
event delivery, remote-track `AudioStream` capture, WAV write, windowed
RMS analysis — end to end against the real local dev server, using a
throwaway synthetic stand-in for the "pi" role's mic track (a
constant-tone `rtc.AudioSource`, identity `"r0082c_pi"`) in place of
real `PlatformAudio` — this deliberately never touches physical hardware
while still exercising the actual `_run_test_role`/`_make_token`
functions from the real R0082-C script, unmodified. Result: connect,
publish, subscribe, capture, and WAV/window output all worked correctly
— byte counts and window counts matched exact expectations
(`captured_full` = 768960 bytes = 8.01s @ 48kHz mono 16-bit for a 6s
test duration + 1s pre-roll + 1s tail + settle overrun; `signal_pcm` =
576000 bytes = exactly 6.0s). The throwaway harness and its output were
deleted after use — nothing from it is part of this repo. The local
dev server itself was stopped after validation; it is not left running
for the operator (§30 documents the exact start command as a
prerequisite).

## 28. R0082-C — PoC script build, bug found and fixed

Built `docs/research/r0082_livekit_webrtc_audio_poc/
r0082c_platform_audio_room_aec_poc.py`: **one process, one asyncio
loop, two `rtc.Room()` connections** (not two OS processes — both
participant roles run concurrently via `asyncio.gather` in the same
script, which satisfies "two participants... both join the same room"
architecturally while keeping the operator interface to exactly one
command, per §30). Reuses `r0082_audio_utils.py` exclusively (no
`sounddevice` import — `PlatformAudio` does not use PortAudio, unlike
R0082-B's `MediaDevices`).

- **"pi" role** (`_run_pi_role`): real `rtc.PlatformAudio()`; enumerates
  and prints `recording_devices()`/`playout_devices()`; calls
  `_verify_default_device()` (§26 item 1–2) instead of explicit
  selection; creates a `PlatformAudioSource` with the requested
  `echo_cancellation` (via `--aec`); publishes it as a track; relies on
  auto-subscribing (default `RoomOptions(auto_subscribe=True)`) to the
  "test" role's stimulus track to trigger PlatformAudio's automatic
  playout (the AEC far-end reference, per §26 item 5); cleans up in the
  documented order (`source.close()` → `room.disconnect()` →
  `platform_audio.close()`).
- **"test" role** (`_run_test_role`): synthetic `rtc.AudioSource` (no
  physical device); publishes the SAME `build_signal` stimulus as
  R0082-B; listens for `room.on("track_subscribed")` filtered to
  identity `"r0082c_pi"` and `TrackKind.KIND_AUDIO` (confirmed correct
  attribute access via direct SDK introspection — `TrackKind` is a
  protobuf `EnumTypeWrapper`, `KIND_AUDIO` resolves to `1`); once the
  Pi's mic track is subscribed, waits 1.0s to settle, then plays
  pre-roll + signal + tail while concurrently capturing the remote mic
  track via `rtc.AudioStream` (the exact same reuse-of-any-track pattern
  R0082-A/B already confirmed); computes the identical windowed
  RMS/peak analysis and writes WAV evidence
  (`r0082c_aec_captures/r0082c_aec{off,on}_<run_id>_<stimulus>_{signal,mic}.wav`).

**Bug found (via the real dry-run validation, §27, not guessed) and
fixed:** the first draft's `_run_test_role` cleanup used
`getattr(signal_source, "close", None)` — but `rtc.AudioSource` exposes
only an **async** `aclose()` (confirmed from source, §26 item 8), not a
sync `close()`; the `getattr` silently found nothing, so the source was
never actually released, producing `FfiHandle.__del__` `AssertionError`s
on an otherwise fully successful run (2 occurrences observed). Fixed by
calling `await signal_source.aclose()` directly; re-validated — **zero**
`FfiHandle` assertion errors on the corrected re-run, with output data
(byte counts, window computations) unchanged and correct. One harmless
native-library warning (`Attempted to drop unknown FFI handle: ...`,
printed by the Rust FFI layer, not a Python exception) remains and is
analogous to R0082-B's own "ignoring" warning — noted, not chased
further; it does not affect output correctness.

## 29. R0082-C validation summary

```
isolated-venv import                 PASS
--help                               PASS
py_compile                           PASS
ruff                                 PASS (0 errors)
git diff --check                     PASS
no nexa/pipecat/loguru/Silero/
  Gemini-SDK modules on import       CONFIRMED (isolation check)
real room mechanics (non-hardware)   CONFIRMED (§27 dry-run, synthetic
                                      stand-in for the "pi" mic track,
                                      zero FfiHandle errors after fix)
no production src/ or apps/ changes  CONFIRMED
```

`PlatformAudio()` itself — the one call that touches real physical
hardware — was never invoked by this session, per instruction.

## 30. Decision gate and next step

Per the brief's decision gate: if `PlatformAudio`/PipeWire shows
meaningful AEC attenuation **and** a stable residual over time, R0082
becomes a strong PASS candidate and R0083 (Pi `PlatformAudio`/WebRTC ↔
LiveKit ↔ Pipecat `LiveKitTransport` ↔ Gemini Live) can be planned. If
`PlatformAudio` shows the same late-run instability as R0082-B, Gemini
integration stays deferred and the audio layer keeps being isolated.
**Neither outcome is known yet — the first real R0082-C hardware run has
not been executed.**

### Minimal command for the first real R0082-C AEC-OFF hardware run (NOT run by this session)

**Prerequisite** (start once, in a separate terminal, before the command
below):

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**The one operator command:**

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \
  --aec off --duration 30 --stimulus tones
```

This runs BOTH room participants (real `PlatformAudio` "pi" role +
synthetic "test" role) in one process — no second terminal needed for
the PoC itself, only for the `livekit-server` prerequisite. Operator
should monitor CPU/RAM separately (e.g. `top`) during the run per §26
item 10. **Do not run `--aec on` yet** — withheld until this `--aec off`
run is confirmed to open the real reSpeaker/UACDemoV1.0 devices via
`PlatformAudio` and complete successfully. This has intentionally
**not** been run by this session.

**Superseded by §31 below** — §30's command used `--stimulus tones`,
which §25a's correction means is confounded for time-stability
measurement. §31 adds a `stationary_multitone` stimulus specifically to
remove that confound; §32 gives the updated, authoritative first
operator command. §30's own text above is left as historical record,
not deleted.

## 31. `stationary_multitone` stimulus — removes the frequency-content confound

Added `build_stationary_multitone_signal()` to
`docs/research/r0082_livekit_webrtc_audio_poc/r0082_audio_utils.py`
(still standard-library-only — `array`, `math`, `struct`, `wave`,
`pathlib`; no new dependency) and wired it into `build_signal()`'s
dispatch as `"stationary_multitone"`, alongside the existing `"tones"`
and `"mls"` (neither removed). Both `r0082_platform_audio_aec_poc.py`
(R0082-B) and `r0082c_platform_audio_room_aec_poc.py` (R0082-C) had
their `--stimulus` CLI choices extended to include it.

### What it does

All three of `build_test_signal`'s own frequencies — 500Hz, 1000Hz,
2000Hz — play **simultaneously**, for the entire requested duration,
with deterministic fixed phases (all zero). No segment transitions: the
spectral content is identical from the first sample to the last. The
same 20ms linear fade-in/out `build_test_signal` uses is still applied
(for click-avoidance only — since every component's own `sin(...)` is
already exactly zero at `t=0`, the unfaded signal itself already starts
at zero amplitude; the fade is kept for consistency and to avoid any
edge discontinuity from the fade window boundary itself).

### Normalization — exact derivation

Naively giving each of the 3 components the same per-tone amplitude as
the existing single-tone stimulus (0.05) would triple the total drive
level, not preserve it — RMS *power* adds across uncorrelated
frequencies, not peak amplitude. The exact math (also in the function's
own code comment):

```
A single sinusoid of peak amplitude A has RMS = A / sqrt(2).

N uncorrelated sinusoids (different frequencies -> ~zero cross-
correlation over any window spanning many cycles) of EQUAL per-tone
peak amplitude a sum to a combined signal whose mean-square power is
the SUM of each component's own power (variances add for uncorrelated
signals):
    combined_meansq = N * (a^2 / 2)
    combined_RMS     = a * sqrt(N / 2)

Solve for a such that combined_RMS equals a single tone's RMS at the
SAME `amplitude` parameter (amplitude / sqrt(2)):
    a * sqrt(N / 2) = amplitude / sqrt(2)
    a = amplitude / sqrt(2) / sqrt(N / 2)
    a = amplitude / sqrt(N)          <- exact, sqrt(2) cancels

Self-consistency check: combined_RMS = (amplitude/sqrt(N)) * sqrt(N/2)
    = amplitude / sqrt(2)  -- EXACTLY the single-tone RMS, for any N.
```

With `N=3` and `amplitude=0.05` (the project's standard default): each
component's own peak amplitude is `0.05/sqrt(3) ≈ 0.02887`. A defensive
`max(-32768, min(32767, value))` clamp is present in the code but is
not expected to trigger — worst-case perfect constructive interference
of all 3 components reaches `per_tone_amplitude * 32767 * 3 ≈ 2837`,
well inside int16 range; confirmed empirically below (peak observed:
2111, i.e. the clamp never activated).

## 32. `stationary_multitone` offline validation

Generated the exact signal the first real R0082-C hardware run will use
(`duration_s=30.0, sample_rate=48000, amplitude=0.05`) and validated it
without touching any microphone/speaker hardware, using only the
already-isolated `r0082_audio_utils.py` (importable with plain `python3`
— no venv needed, confirmed — since it has zero non-stdlib
dependencies).

**Determinism:** built twice independently; the two outputs are
byte-for-byte identical.

**Header/duration:** mono, S16_LE (`sampwidth=2`), 48000Hz,
1,440,000 frames, exactly 30.000s.

**No clipping:** overall peak = 2111 (int16 max is 32767) — the
defensive clamp in §31 never activates.

**Combined RMS vs. single-tone target:** stationary_multitone overall
RMS = 1157.51; single-tone (`tones`, `amplitude=0.05`) RMS = 1156.47;
ratio = 1.0009 — matches the derivation in §31 almost exactly (the
small residual difference is the fade-envelope edge effect, present
identically in both stimuli).

**Spectral content — confirmed present and CONSTANT throughout,** via
a pure-Python single-bin DFT correlation (no numpy) at 500/1000/2000Hz
in three 1-second sub-windows spread across the signal (t=1-2s,
t=15-16s, t=28-29s — deliberately away from the fade edges), plus an
off-target 3000Hz probe as a noise-floor reference:

```
start  (t=1.0-2.0s):   500Hz=472.7  1000Hz=472.8  2000Hz=472.8   (3000Hz ref=0.04)
middle (t=15.0-16.0s): 500Hz=472.7  1000Hz=472.8  2000Hz=472.8   (3000Hz ref=0.04)
end    (t=28.0-29.0s): 500Hz=472.7  1000Hz=472.8  2000Hz=472.8   (3000Hz ref=0.04)
```

All three target frequencies are present with equal, constant magnitude
at the start, middle, and end of the signal (essentially identical
across all three windows); the off-target frequency reads at the noise
floor. **No frequency transitions remain.**

### Exact 10-window RMS analysis (3s windows, the same windowing R0082-B/C use)

```
window   window_s      rms       peak
  1      0.0- 3.0    1155.45     2111
  2      3.0- 6.0    1158.03     2111
  3      6.0- 9.0    1158.03     2111
  4      9.0-12.0    1158.03     2111
  5     12.0-15.0    1158.03     2111
  6     15.0-18.0    1158.03     2111
  7     18.0-21.0    1158.03     2111
  8     21.0-24.0    1158.03     2111
  9     24.0-27.0    1158.03     2111
 10     27.0-30.0    1155.45     2111
```

`min/max = 1155.45 / 1158.03` → **max/min = 1.0022** (target ~1.00 —
met). The only two windows that differ at all (1 and 10, by 0.22%) are
the ones containing the 20ms fade edges; all 8 interior windows are
bit-for-bit equal at 1158.03. Peak is identical (2111) in every single
window — the only variation anywhere is the RMS dip from the fade
envelope, not from any frequency-dependent effect.

**No segment-boundary discontinuities:** a finer 0.5s-resolution
sub-window scan across the full 30s signal (60 sub-windows) found
`max/min = 1.0136` overall, with **only** the very first and very last
0.5s sub-windows (t=0.0s and t=29.5s — the fade-in/out region) deviating
more than 1% from the median; all 58 interior sub-windows are within 1%
of the median RMS. There is no analog anywhere in this signal of the old
`tones` stimulus's sharp ~3x step at the 500→1000→2000Hz transitions
(R0082-B §24).

### Validation suite (re-run after adding `stationary_multitone`)

```
isolated-venv import (r0082_audio_utils, plain python3, no venv needed)  PASS
py_compile (all 3 files: utils, R0082-B PoC, R0082-C PoC)               PASS
ruff (whole r0082_livekit_webrtc_audio_poc/ directory)                  PASS (0 errors)
git diff --check                                                        PASS
--help (both R0082-B and R0082-C scripts)                               PASS
```

No microphone/speaker hardware was used for any of this validation —
`build_stationary_multitone_signal()` and every check above operate
purely on the generated PCM in memory/on disk.

## 33. Updated first R0082-C hardware command — `stationary_multitone`

Supersedes §30's `--stimulus tones` command (kept above as historical
record, not deleted). Same prerequisite as §30:

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**The one operator command (AEC OFF only):**

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \
  --aec off --duration 30 --stimulus stationary_multitone
```

With this stimulus, any systematic window-to-window RMS change in the
real hardware result can legitimately be interpreted as a time/
stream-age effect (§25a) — the frequency-content confound is removed.
**Do not run `--aec on` yet** — withheld until this `--aec off` run is
confirmed to open the real devices via `PlatformAudio` and complete
successfully. This has intentionally **not** been run by this session.

**Superseded by §34 below** — §33's command crashed before producing a
measurement (§34a). §34 documents the crash, the split-process
refactor, and the preflight that validated the fix before the real AEC
test is attempted again.

## 34. R0082-C real hardware attempt #1 — crashed before measurement

The operator ran §33's command. Device discovery and room join both
succeeded:

```
R0082-C PlatformAudio+room AEC PoC
run_id=20260918T132003Z
aec=off

recording: default: reSpeaker XVF3800 4-Mic Array Analog Stereo
playout:   default: UACDemoV1.0 Analog Stereo

input default OK
output default OK
```

Then the process aborted inside native WebRTC:

```
Fatal error in: ../audio/audio_send_stream.cc, line 404
Check failed: !race_checker404.RaceDetected()
Aborted
```

The LiveKit server logs confirmed both participants joined the real
room and disconnected essentially immediately following the client
abort. This is explicitly **not** an AEC result, not a reSpeaker
sample-rate failure, not a PipeWire default-device failure, not a
Python exception, and not evidence that PlatformAudio AEC does not
work — the process died inside native WebRTC before the deterministic
30s measurement could run.

### Classification

```
R0082-C real hardware attempt #1

PlatformAudio initialization        PASS
recording device discovery          PASS
reSpeaker PipeWire default          PASS
playout device discovery            PASS
UACDemo PipeWire default            PASS
LiveKit room connection             PASS
both participants joined            PASS

30s stimulus playback               NOT REACHED
AEC OFF measurement                 NOT PRODUCED
AEC stability result                NOT AVAILABLE

native WebRTC:
audio_send_stream.cc RaceDetected   CONFIRMED
exact cause                         OPEN
single-process/two-Room concurrency HIGH-PRIORITY HYPOTHESIS
```

### Primary hypothesis (not proven)

The PoC at that point ran ONE Python process, ONE asyncio loop, and TWO
`rtc.Room()` participants concurrently (real `PlatformAudio` + a
synthetic test publisher/subscriber) inside that single process. A
native WebRTC `RaceDetected()` assertion makes this single-process/
two-`Room`/two-`PeerConnection` concurrency structure the
highest-priority hypothesis — **this is a hypothesis, not a proven root
cause.** "Two Rooms caused the crash" is not asserted as fact.

### External evidence considered

- Python `multiprocessing` was explicitly ruled out as the fix
  mechanism: there is a currently open upstream LiveKit Python SDK issue
  reporting SIGABRT/FFI callback crashes with `multiprocessing`'s
  PROCESS mode on Linux — using it here could introduce a different
  crash class rather than removing this one.
- The installed `livekit==1.1.19` was **not** casually upgraded or
  downgraded as a first reaction — no version change was made this
  round.
- The dev token's `InsecureKeyLengthWarning` (6-byte HMAC key, from the
  `--dev` server's placeholder `devkey`/`secret` credentials) is
  recorded but was **not** treated as related to `RaceDetected()` — it
  is a JWT-signing-strength warning, unrelated to native audio pipeline
  concurrency.

## 35. Split into two independent OS processes

Refactored `r0082c_platform_audio_room_aec_poc.py` (R0082-C's own PoC
only — no other file touched) from the single-process/two-`Room` design
into a `--role {hardware,test}` split: each role is launched as its OWN
`python3` invocation (own interpreter, own `rtc.Room()`, own LiveKit
FFI/runtime state) — no shared `Room`, no shared `PlatformAudio`, no
Python `multiprocessing`, no `fork()`-based worker model, per
instruction. Renamed the hardware-side identity from `"r0082c_pi"` to
`"r0082c_hardware"` for clarity. Removed the now-inapplicable
single-process `run_poc()` joint orchestrator. Added `--room-name`
(required, must match between the two invocations — the only
coordination the two processes share) and a `--no-stimulus` flag on
`--role test` (join and hold the room without playing any signal PCM —
used only by the preflight below, not the real AEC test).

Re-ran the full offline validation suite on the refactored script — all
pass:

```
isolated-venv import      PASS
--help                    PASS
py_compile                PASS
ruff (0 errors)            PASS
git diff --check           PASS
isolation check (no nexa/pipecat/loguru/Silero/Gemini-SDK on import)  PASS
```

## 36. Split-process synthetic validation (no PlatformAudio, no hardware)

Before touching real hardware again, validated the split-process
topology's own mechanics using two genuinely separate OS processes,
neither touching a physical device: a throwaway synthetic stand-in for
the hardware role (constant-tone `rtc.AudioSource`, published under
identity `"r0082c_hardware"`, kept only in the session scratchpad —
never part of this repo) launched via a background `python3` invocation,
and the REAL `--role test` script as the second, independently launched
process.

```
fake-hardware process: pid=1329341 (shell-tracked matches internally logged pid)
test process:          pid=1329388 (shell-tracked matches internally logged pid)
```

Both PIDs are confirmed genuinely distinct OS processes (not threads or
coroutines within one interpreter). Both exited with code 0. Full
publish → subscribe (`track_subscribed` fired correctly across the
process boundary) → capture → windowed RMS analysis → WAV write → clean
disconnect all worked correctly (captured a flat, correctly-computed
RMS of the fake constant tone, as expected). **Zero `FfiHandle`
assertion errors** in either process's log — the only anomaly was one
harmless native-library `Attempted to drop unknown FFI handle` warning
(same class already seen and documented as non-fatal in R0082-C's own
earlier single-process dry-run, §27), not a Python exception, not a
crash. Throwaway harness and its WAV output were deleted after use.

## 37. Minimal real-hardware preflight (split processes, no deterministic stimulus)

Per the required sequence, ran a short real-hardware preflight —
`PlatformAudio` genuinely initialized, real reSpeaker mic capture
genuinely active — but with **no** deterministic 30s stimulus played,
to isolate whether the split-process topology alone resolves the
`RaceDetected()` abort before attempting the full measurement again.

Sequence executed, both as separate `python3` processes sharing only
`--room-name`:

```
1. start local LiveKit server                          DONE
2. start hardware participant process                  DONE (pid=1330273)
3. verify PlatformAudio creation                        PASS (no error)
4. verify reSpeaker default                             PASS ("input default OK: 'default: reSpeaker XVF3800 4-Mic Array Analog Stereo'")
5. verify UACDemoV1.0 default                            PASS ("output default OK: 'default: UACDemoV1.0 Analog Stereo'")
6. connect                                               PASS
7. publish mic track                                     PASS
8. test participant joins separately                    DONE (pid=1330345, independently launched)
9. subscribe successfully                                PASS ("hardware mic track subscribed")
10. hold the room alive, NO deterministic stimulus       PASS ("play_stimulus=False -- holding room, no signal sent")
11. clean shutdown                                        PASS (both processes exited 0, "disconnected cleanly")
```

**No crash. No `RaceDetected()`. No `Fatal error`. No `Aborted`.**
Grepped both process logs explicitly for
`RaceDetected|Fatal error|Aborted|AssertionError|Traceback` — none
found. The LiveKit server's own debug log confirms this was a genuine,
active audio session, not a no-op: real RTP statistics for the
hardware participant's upstream mic track (347 packets over ~7.1s,
`packetsLost: 0`, jitter values present — real audio was actually
flowing from the reSpeaker through PlatformAudio's WebRTC pipeline),
and a clean `CLIENT_INITIATED` disconnect on both sides.

**Preflight result: PASS.** Splitting the participants into separate OS
processes removed the `RaceDetected()` abort under these short,
no-stimulus conditions. This is evidence FOR the single-process/two-Room
concurrency hypothesis (§34) without being definitive proof of root
cause for the FULL 30s stimulus case, which has not yet been attempted
under the split-process topology — that is the next, not-yet-run step
(§38).

## 38. Updated first R0082-C hardware command — split-process, `stationary_multitone`

Preflight passed (§37), so per instruction: exactly ONE
command/procedure for AEC OFF, `stationary_multitone`, 30 seconds.
**No AEC ON command is given yet.**

**Prerequisite** (start once, separate terminal):

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**Procedure — TWO separate terminals, a SHARED room name** (pick any
unique room name for `<ROOM>`, e.g. `r0082c_run_$(date +%s)`):

```bash
# terminal A — hardware participant (real PlatformAudio; start this one first)
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \
  --role hardware --aec off --duration 30 --room-name <ROOM>

# terminal B — test participant (synthetic; start within ~30s of terminal A)
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \
  --role test --aec off --duration 30 --stimulus stationary_multitone --room-name <ROOM>
```

`<ROOM>` must be IDENTICAL in both commands. `--aec off` on the test
side is a label only (used in its own output WAV filenames); the
functional AEC toggle lives entirely in the hardware-role invocation.
WAV evidence and the windowed RMS table are written by the **test**
process only, under
`docs/research/r0082_livekit_webrtc_audio_poc/r0082c_aec_captures/`.
Operator should monitor CPU/RAM separately (e.g. `top`) on both
processes during the run. **Do not run `--aec on` yet** — withheld
until this `--aec off` run is confirmed to complete the full 30s
measurement without a native WebRTC abort. This has intentionally
**not** been run by this session.

**Superseded by §43 below.** §38's procedure text above claimed the
test role could "start within ~30s of terminal A" — this was **wrong
and dangerous** (§39 explains exactly why) and is corrected, not
repeated, in §43.

## 39. Confirmed synchronization/lifetime defect (found before running the full measurement)

Before running the 30s measurement, the exact timing was checked and a
real defect was found. The hardware role held the room open via:

```
connect -> publish mic track -> sleep(total_s + 2.0)
```

started immediately after publish, where `total_s = PRE_ROLL_S(1) +
duration_s(30) + TAIL_S(1) = 32s`, i.e. a hold of ~34s **from the
hardware role's own publish time**.

The test role's own capture timeline, independently, only *starts*
after it detects the hardware mic subscription:

```
wait for hardware mic subscribed (bounded)
+ 1s settle
+ 1s pre-roll
+ 30s stimulus
+ 1s tail
≈ 33s from "hardware mic subscribed", not from the hardware role's own publish time
```

Nothing tied the hardware role's fixed sleep to the test role's actual
progress — the previous §38 guidance that "either start order works
within ~30s" was an unverified claim (the hardware role's code never
actually waited for anything from the test role; it only slept a fixed
amount). Even a modest operator launch-timing gap between the two
terminals could let the hardware role disconnect (and `platform_audio`
close) while the test role's capture was still in progress, producing a
silently truncated recording that could have been misread as a
real AEC/stability result.

**Also found:** the module's own docstring, from the previous round,
already *claimed* "the hardware role waits (bounded timeout) similarly
for the test role's stimulus track before its own room join is
considered complete" — this was **not actually implemented** at the
time; a documentation/implementation mismatch, not merely a missing
feature. This round's fix makes that claim true for the first time.

## 40. Fix — event-based mutual synchronization, named timing constants, capture-completeness validation

Three changes to `r0082c_platform_audio_room_aec_poc.py` (this PoC
only):

1. **`run_hardware_role` now waits for the test role's own track.** It
   registers a `track_subscribed` handler filtered to `TEST_IDENTITY` +
   `KIND_AUDIO`, and does `await asyncio.wait_for(test_track_ready.wait(),
   timeout=SUBSCRIBE_TIMEOUT_S)` — the SAME class of event-based sync
   the test role already used for the hardware mic track — before
   starting its own measurement-lifetime hold. This makes operator
   launch ORDER/timing between the two processes irrelevant within the
   timeout window, instead of depending on an assumed wall-clock gap.
2. **Named timing constants**, not a magic number: `SETTLE_S = 1.0`,
   `SUBSCRIBE_TIMEOUT_S = 30.0`, `CLEANUP_GRACE_S = 3.0`. The hardware
   role's post-subscription hold is now derived explicitly:
   `hold_s = SETTLE_S + PRE_ROLL_S + duration_s + TAIL_S +
   CLEANUP_GRACE_S` — printed in full at runtime (e.g. `"holding room
   for 9.0s (SETTLE=1.0+PRE_ROLL=1.0+duration=3.0+TAIL=1.0+
   CLEANUP_GRACE=3.0)"`), so the exact budget is always visible in the
   log, not buried in an unexplained sleep value.
3. **Capture completeness validation** in `run_test_role`. Before
   computing any window metric, the test role now checks
   `actual_samples` (from the joined captured PCM) against
   `expected_samples = int((PRE_ROLL_S + duration_s + TAIL_S) *
   SAMPLE_RATE)`, printing both expected and actual sample/byte/duration
   counts. If `actual_samples < expected_samples`, the run raises
   `SystemExit` with a clear diagnostic and **does not** compute or
   write any AEC/stability result — no zero-padding, no partial-window
   classification. Additionally, each individual 3s analysis window is
   checked for its own full expected sample count before its RMS/peak
   is computed; a short window also fails the run outright rather than
   silently contributing an under-counted RMS value.

## 41. Re-validation (offline)

```
isolated-venv import      PASS
--help                    PASS
py_compile                PASS
ruff                      1 line-length error found and fixed (107 > 100
                           chars, the new "waiting for test participant
                           audio track subscription..." print) -- 0
                           errors on re-check
git diff --check           PASS
isolation check (no nexa/pipecat/loguru/Silero/Gemini-SDK on import)  PASS
```

## 42. Re-validation (split-process synthetic, then real-hardware no-stimulus preflight)

**Synthetic split-process re-validation** (throwaway fake-hardware
stand-in, extended to also subscribe to the test role's own track,
exercising the FULL new mutual-handshake — kept only in the session
scratchpad, deleted after use):

```
fake-hardware process: pid=1339332
test process:          pid=1339401
```

Both genuinely distinct processes, both exited 0. Log confirms the full
mutual handshake fired on both sides ("hardware subscribed to test
audio track" / "test subscribed to hardware mic"), and the new capture
completeness check printed and PASSED (`expected: 384000 samples ...
8.000s` vs. `actual: 384480 samples ... 8.010s` — actual ≥ expected).
Zero `FfiHandle` assertion errors; the only anomaly, as before, is one
harmless native-library "unknown FFI handle" warning (non-fatal, same
class documented in §27/§36).

**Real-hardware no-stimulus preflight, re-run with the fix** — genuinely
separate processes, `PlatformAudio` actually initialized, real reSpeaker
capture active, no deterministic stimulus played:

```
hardware pid=1339911
test     pid=1339953
```

Full required log sequence confirmed present on both sides:

```
[hardware] connecting to room ... / connected, publishing mic track
[hardware] waiting for test participant audio track subscription...
[hardware] hardware subscribed to test audio track
[hardware] holding room for 9.0s (SETTLE=1.0+PRE_ROLL=1.0+duration=3.0+TAIL=1.0+CLEANUP_GRACE=3.0)
[hardware] disconnected cleanly / platform_audio closed, exiting

[test] connecting to room ... / connected, publishing stimulus track
[test] waiting for hardware mic track subscription...
[test] test subscribed to hardware mic
[test] play_stimulus=False -- holding room, no signal sent
[test] disconnected cleanly / preflight complete
```

Grepped both logs explicitly for
`RaceDetected|Fatal error|Aborted|AssertionError|Traceback` — **none
found.** Both processes exited 0.

### Conclusions preserved, not overstated

```
split-process synthetic validation           PASS
split-process real PlatformAudio preflight   PASS

RaceDetected in split preflight               NOT OBSERVED
single-process/two-Room hypothesis            STRENGTHENED
exact original root cause                     STILL OPEN
```

Splitting into separate OS processes continues to avoid the
`RaceDetected()` abort under short, no-stimulus conditions, now with a
correctly-synchronized (event-based, not wall-clock-guessed) hold
lifetime and explicit capture-completeness protection against a
silently truncated recording. This remains evidence FOR the concurrency
hypothesis, not proof of root cause — the full 30s measurement under
the corrected split-process topology has still not been attempted.

## 43. Corrected first R0082-C hardware command — split-process, synchronized, `stationary_multitone`

Supersedes §38's procedure (which contained the incorrect "~30s" launch
guidance, §39). Same prerequisite:

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**Procedure — TWO separate terminals, a SHARED room name** (pick any
unique room name for `<ROOM>`, e.g. `r0082c_run_$(date +%s)`). Launch
order no longer matters within `SUBSCRIBE_TIMEOUT_S` (30s) — both
roles now wait for an actual cross-process subscription event, not a
wall-clock guess:

```bash
# terminal A — hardware participant (real PlatformAudio)
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \
  --role hardware --aec off --duration 30 --room-name <ROOM>

# terminal B — test participant (synthetic)
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3 \
  docs/research/r0082_livekit_webrtc_audio_poc/r0082c_platform_audio_room_aec_poc.py \
  --role test --aec off --duration 30 --stimulus stationary_multitone --room-name <ROOM>
```

`<ROOM>` must be IDENTICAL in both commands. `--aec off` on the test
side is a label only (used in its own output WAV filenames); the
functional AEC toggle lives entirely in the hardware-role invocation.
WAV evidence and the windowed RMS table are written by the **test**
process only, under
`docs/research/r0082_livekit_webrtc_audio_poc/r0082c_aec_captures/` —
and will now **refuse to be written** (the run fails loudly instead,
§40) if the captured recording is shorter than the full
`PRE_ROLL_S + duration_s + TAIL_S` expected region. Operator should
monitor CPU/RAM separately (e.g. `top`) on both processes during the
run. **Do not run `--aec on` yet** — withheld until this `--aec off`
run is confirmed to complete the full 30s measurement, pass its own
capture-completeness check, and finish without a native WebRTC abort.
This has intentionally **not** been run by this session.

## 44. First complete PlatformAudio AEC OFF/ON pair — offline WAV analysis

§43's procedure was run by the operator. Both runs completed cleanly
(capture-completeness check passed on both, no `RaceDetected`, no
truncation, healthy server-side RTP stats — 1469/1469 packets, 0 lost,
0 out-of-order on the hardware uplink). This section analyzes the
resulting WAV evidence offline. **No new hardware test was run this
round.**

### 44a. File verification

Located in `docs/research/r0082_livekit_webrtc_audio_poc/r0082c_aec_captures/`:

| File | Size (bytes) | SHA256 |
|---|---|---|
| `r0082c_aecoff_20260918T134735Z_stationary_multitone_mic.wav` | 3,073,004 | `c9461c9e57ea95c71f3e58846753a31e5d1196b82da92cd958e9e42ed2227a51` |
| `r0082c_aecoff_20260918T134735Z_stationary_multitone_signal.wav` | 2,880,044 | `3cd904e1105054c7b18d80ab6273161e0aeaef1deb4111bf74bfcaa97f252bfb` |
| `r0082c_aecon_20260918T135110Z_stationary_multitone_mic.wav` | 3,073,004 | `fbd80c08f0c6adbe1b45c18a58a7a0c552ca1cb1b77eeba3f4f89532f20a24bc` |
| `r0082c_aecon_20260918T135110Z_stationary_multitone_signal.wav` | 2,880,044 | `3cd904e1105054c7b18d80ab6273161e0aeaef1deb4111bf74bfcaa97f252bfb` |

All four: mono, 16-bit (S16_LE), 48000Hz. Mic files: 1,536,480 frames /
32.010s. Signal files: 1,440,000 frames / 30.000s.

**Signal WAVs are SHA256-identical between the OFF and ON runs**
(`3cd904e1...52bfb` both) — confirms the deterministic
`stationary_multitone` generator produced byte-identical stimuli for
both conditions, as required. No discrepancy to explain.

### 44b. Analysis tooling

New file: `docs/research/r0082_livekit_webrtc_audio_poc/r0082c_wav_analysis.py`
— research-only, reads the 4 WAVs directly (never modifies them), no
`nexa.*`/`pipecat.*`/`google.*`/`loguru`/LiveKit imports, no hardware
access. Run with plain system `python3` (numpy 2.2.4 + scipy 1.17.1
already available there, confirmed this round — not NeXa's own `.venv`,
which lacks scipy, and not the isolated livekit probe venv, which is
unrelated to this analysis). `py_compile` and `ruff` both clean. All
RMS values below are on the original int16 PCM scale — the recordings
were never normalized before analysis.

### 44c. Time-domain: 1-second table (stimulus-relative; t=0 is pre-roll end)

**OFF mic** (pre-roll rms=2.84, peak=15):

```
[ 0- 1s] rms=  2.59 peak=  11   [10-11s] rms=  7.54 peak= 32   [20-21s] rms=  3.47 peak= 26
[ 1- 2s] rms=  5.20 peak=  37   [11-12s] rms=  8.44 peak= 70   [21-22s] rms=  1.91 peak=  8
[ 2- 3s] rms= 11.87 peak= 105   [12-13s] rms=  8.62 peak= 30   [22-23s] rms= 27.00 peak=265
[ 3- 4s] rms=  5.10 peak=  31   [13-14s] rms=  6.76 peak= 33   [23-24s] rms=  1.94 peak= 10
[ 4- 5s] rms=  3.70 peak=  20   [14-15s] rms=  2.08 peak=  8   [24-25s] rms=  1.77 peak=  7
[ 5- 6s] rms=  2.92 peak=  13   [15-16s] rms=  1.94 peak=  8   [25-26s] rms=  1.80 peak=  7
[ 6- 7s] rms=  8.39 peak=  29   [16-17s] rms=  1.62 peak=  7   [26-27s] rms=  1.71 peak=  8
[ 7- 8s] rms=  8.90 peak=  30   [17-18s] rms=  1.68 peak=  7   [27-28s] rms=  4.85 peak= 26
[ 8- 9s] rms=  9.99 peak=  35   [18-19s] rms=  1.83 peak=  7   [28-29s] rms=  8.74 peak= 30
[ 9-10s] rms= 13.55 peak=  96   [19-20s] rms=  1.85 peak=  7   [29-30s] rms= 10.27 peak= 32
tail: rms=5.70 peak=29
```

**ON mic** (pre-roll rms=15.06, peak=144):

```
[ 0- 1s] rms=216.17 peak= 934   [10-11s] rms= 31.13 peak=107   [20-21s] rms= 20.98 peak=105
[ 1- 2s] rms=534.75 peak=1190   [11-12s] rms= 20.33 peak= 73   [21-22s] rms= 35.83 peak=102
[ 2- 3s] rms=508.77 peak=1135   [12-13s] rms= 24.72 peak= 74   [22-23s] rms= 37.92 peak=121
[ 3- 4s] rms=146.38 peak= 740   [13-14s] rms= 23.93 peak= 93   [23-24s] rms= 41.13 peak=109
[ 4- 5s] rms=155.31 peak= 688   [14-15s] rms= 27.87 peak=103   [24-25s] rms= 41.69 peak=129
[ 5- 6s] rms= 68.44 peak= 369   [15-16s] rms= 34.09 peak= 92   [25-26s] rms= 38.84 peak=131
[ 6- 7s] rms= 11.46 peak=  57   [16-17s] rms= 38.37 peak=120   [26-27s] rms= 36.08 peak=124
[ 7- 8s] rms= 23.54 peak= 114   [17-18s] rms= 24.08 peak=112   [27-28s] rms= 35.22 peak=130
[ 8- 9s] rms= 32.55 peak= 102   [18-19s] rms=  2.99 peak= 14   [28-29s] rms= 37.23 peak=131
[ 9-10s] rms= 31.47 peak=  91   [19-20s] rms= 13.02 peak= 81   [29-30s] rms= 35.66 peak=114
tail: rms=21.81 peak=109
```

Note the OFF table's own irregular spikes (e.g. window 22-23s =
27.00 rms / 265 peak against neighboring windows around 1.7-1.9 rms) —
addressed in §44g/§45.

### 44d. Time-domain: 250ms fine view of the first 6s (startup transient shape)

**OFF**: stays in the 2.3-8.0 rms range throughout, no step or ramp —
consistent with weak, intermittent leakage rather than a startup
transient (OFF has no AEC to converge).

**ON** (key transition): the first 0.75s is quiet (12.5-15.3 rms,
matching the pre-roll baseline of 15.06) — **then an ABRUPT STEP**, not
a gradual ramp: `[0.75-1.00s] rms=431.70` → `[1.00-1.25s] rms=524.47` →
sustained 400-620 rms through `[2.75-3.00s]` → **then a sharp,
non-monotonic decline**: `[3.00-3.25s] rms=22.18` (a sudden near-total
drop) → `[3.25-3.50s] rms=59.19` → `[3.50-3.75s] rms=20.08` →
`[3.75-4.00s] rms=285.15` (a large re-spike) → `[4.00-4.50s]` decaying
160-232 → `[4.75-5.25s]` dropping to 10-28 → `[5.50-6.00s] rms=89-103`
(another re-spike).

**Answering §5's exact shape questions directly:** the onset is an
**abrupt step**, not a smooth ramp. The decay is **not** smoothly
monotonic — it **oscillates**, with at least two large re-spikes inside
the first 6 seconds (at ~3.75s and ~5.5-6.0s) after already dropping to
a much lower level. It reaches its later-run range (roughly 20-40 rms)
only intermittently within the first 6s, and even the "post-convergence"
15-30s region (§44f) still shows real window-to-window variation
(2.99 to 41.69 rms across the ten 1s windows) — this is not a clean,
settled steady state.

### 44e. Frequency-domain: Goertzel narrow-band magnitude + power fraction

Method: single-bin DFT correlation (matched-filter) magnitude at each
target frequency over each named time region, plus the SAME method at
four off-target reference frequencies (750/1500/3000/4000Hz — none
present in the stimulus) as a noise-floor comparison. `target_frac_of_power`
estimates the fraction of the WINDOW's total RMS power explained by the
three target-frequency sinusoids (Parseval: a pure sinusoid of Goertzel
magnitude `m` has power `m²/2`).

```
-- OFF mic --
    0-3s: rms=  7.63  500Hz=0.22 1000Hz=0.16 2000Hz=0.98  target_frac=0.9%  off-target=0.05/0.03/0.01/0.01
    3-6s: rms=  4.01  500Hz=0.20 1000Hz=0.09 2000Hz=0.13  target_frac=0.2%  off-target=0.04/0.01/0.01/0.00
   6-15s: rms=  8.73  500Hz=0.75 1000Hz=0.60 2000Hz=1.18  target_frac=1.5%  off-target=0.00/0.01/0.01/0.01
  15-30s: rms=  8.08  500Hz=0.23 1000Hz=0.29 2000Hz=0.61  target_frac=0.4%  off-target=0.01/0.00/0.00/0.00

-- ON mic --
    0-3s: rms=444.05  500Hz=1.21 1000Hz=2.51 2000Hz=196.28  target_frac=9.8%  off-target=0.30/0.14/0.03/0.01
    3-6s: rms=129.40  500Hz=1.28 1000Hz=1.92 2000Hz= 39.31  target_frac=4.6%  off-target=0.42/0.27/0.02/0.04
   6-15s: rms= 25.99  500Hz=1.26 1000Hz=0.62 2000Hz=  7.10  target_frac=3.9%  off-target=0.46/0.02/0.01/0.00
  15-30s: rms= 33.36  500Hz=0.33 1000Hz=2.35 2000Hz=  6.48  target_frac=2.1%  off-target=0.32/0.07/0.01/0.00
```

**Two things are simultaneously true and must not be collapsed into
one claim:**

1. In EVERY region, target-frequency magnitude is well above the
   off-target reference magnitude (e.g. ON 15-30s: 2000Hz=6.48 vs.
   off-target max 0.32 — ~20x) — real, frequency-exact, stimulus-locked
   energy is present in both OFF and ON.
2. `target_frac_of_power` never exceeds ~10% — meaning even at its
   largest (ON 0-3s), the three discrete target tones account for only
   a small fraction of the window's total RMS power. **Most of the
   window's own energy, in both conditions, is NOT at the three
   stimulus frequencies.**

An FFT top-peak survey (Hanning-windowed, `docs/research/
r0082_livekit_webrtc_audio_poc/r0082c_wav_analysis.py` §6b) makes this
concrete for the post-convergence region:

```
OFF 15-30s top peaks:  131.4Hz(1.41)  116.3Hz(0.48)  160.4Hz(0.33)  196.4Hz(0.31)  2000.1Hz(0.28)*  500.0Hz(0.20)*  999.9Hz(0.20)*  278.1Hz(0.19)
ON  15-30s top peaks: 2000.1Hz(29.78)* 1000.1Hz(5.97)* 499.9Hz(2.63)* 182.5Hz(2.05) 278.3Hz(1.07) 250.0Hz(0.89) 750.0Hz(0.75) 1984.9Hz(0.54)*
  (* = within 20Hz of a target frequency)
```

**OFF's largest peaks are unrelated low frequencies (116-196Hz) that do
not match the stimulus or any of its harmonics** — the target
frequencies ARE present but are smaller than these unrelated peaks.
**ON's largest peaks by far ARE the exact target frequencies**,
especially 2000Hz (29.78, more than 14x the next largest non-target
peak at 182.5Hz=2.05) — ON's spectrum, unlike OFF's, is dominated among
DISCRETE peaks by the stimulus itself, even though (per point 2 above)
most of the window's total RMS POWER is still broadband/spread across
many other frequencies. **This directly answers §6's A/B/C/D
question: the answer is (D) a combination** — real, dominant,
stimulus-frequency-locked discrete energy (especially at 2000Hz) EXISTS
and is unambiguous, but it coexists with, and is outweighed in total
power by, broadband/noise-like content spread elsewhere in the
spectrum. Neither "it's just echo" nor "it's just noise" is
individually correct.

### 44f. Post-convergence OFF vs. ON comparison

Excluding the first two 3s windows (0-6s, dominated by the ON startup
transient), the 6-30s region:

```
                    OFF          ON
6-15s   rms       8.73         25.99
15-30s  rms       8.08         33.36
2000Hz Goertzel:
6-15s             1.18          7.10   (~6.0x)
15-30s            0.61          6.48   (~10.6x)
```

**AEC ON remains materially above AEC OFF after the startup transient,
at both the overall-RMS level and specifically at the target-frequency
level.** This matches the operator's own rough 6.98/29.78 comparison
and refines it: the excess is not merely broadband noise coincidence —
part of it is measurably concentrated at exactly the stimulus's own
2000Hz component, in a way OFF's much smaller 2000Hz leakage is not.

### 44g. Lag-tolerant cross-correlation vs. the known signal

**Methodology and an explicit limitation**: the `stationary_multitone`
stimulus (500/1000/2000Hz, all exact harmonics of a 500Hz fundamental,
period 2ms) is itself periodic with a 2ms period. This means
sample-domain cross-correlation has an inherent **ambiguity modulo
~2ms** — a correlation peak at, say, +14ms and one at +16ms cannot be
distinguished as "the true acoustic delay" vs. "a periodic sidelobe" by
this method alone. Only the peak correlation COEFFICIENT (how strongly
correlated, in magnitude) is treated as reliable; a specific absolute
lag figure is not claimed beyond noting it is ambiguous modulo 2ms.
Normalized cross-correlation (`scipy.signal.correlate`, FFT method,
bounded to ±100ms) was computed per region:

```
-- OFF mic vs. known signal --
    0-3s: best_lag=  55.52ms  peak_corr_coeff=-0.1306
    3-6s: best_lag= -96.42ms  peak_corr_coeff=-0.0644
   6-15s: best_lag= -41.98ms  peak_corr_coeff=-0.2361
  15-30s: best_lag=  96.25ms  peak_corr_coeff=+0.0922

-- ON mic vs. known signal --
    0-3s: best_lag=  98.38ms  peak_corr_coeff=-0.3639
    3-6s: best_lag= -97.23ms  peak_corr_coeff=+0.2664
   6-15s: best_lag=  94.44ms  peak_corr_coeff=-0.2612
  15-30s: best_lag=  -0.25ms  peak_corr_coeff=+0.2103
```

**All peak correlation coefficients are weak in magnitude** (|coeff|
0.06-0.36; a strong, dominant linear relationship would read closer to
0.7-1.0). This is consistent with, not contradictory to, §44e's
frequency-domain finding: frequency-domain analysis detects narrow-band
energy regardless of phase relationship across a window, while
time-domain correlation requires actual phase/timing alignment. A
real, frequency-exact acoustic coupling can coexist with weak
time-domain correlation if the path introduces phase distortion,
multipath/reverberation, or non-linear processing (WebRTC AEC
implementations commonly include a non-linear residual/comfort-noise
suppression stage beyond simple linear adaptive filtering, though this
specific mechanism is not verified from Python source here — flagged as
a possibility, not confirmed). **Conclusion: the answer to §7's key
question — "is the large AEC ON signal actually correlated with the
speaker stimulus, or mostly unrelated" — is: it is DEMONSTRABLY
frequency-correlated (§44e) but only WEAKLY time/phase-correlated
(this section).** Neither a clean "yes, it's echo" nor a clean "no,
it's unrelated noise" answer is fully supported; the honest description
is a partial, frequency-locked but phase-incoherent relationship.

### 44h. Quiet/pre-roll baseline structure

```
OFF quiet: mean(DC)=-0.396  rms=2.84  peak=15
  top peaks: 171.0Hz(0.30) 238.0Hz(0.29) 217.0Hz(0.29) 319.0Hz(0.29) 347.0Hz(0.27) 380.0Hz(0.26)
  broadband floor (median FFT magnitude): 0.0037

ON quiet:  mean(DC)=+0.362  rms=15.06  peak=144
  top peaks: 493.0Hz(1.32) 656.0Hz(1.28) 444.0Hz(1.19) 468.0Hz(1.15) 518.0Hz(1.12) 817.0Hz(1.12)
  broadband floor (median FFT magnitude): 0.0042
```

Neither quiet region shows a single dominant narrow spectral line, a
significant DC offset, or an obviously periodic structure — both look
like broadband, multi-frequency noise spread across a wide range
(roughly 150-400Hz for OFF, roughly 440-820Hz for ON), not a single
identifiable tone or hum. The ON quiet region's peaks are NOT near the
stimulus frequencies (493-817Hz range, versus 500/1000/2000Hz targets —
only 493Hz and 518Hz are even loosely close to 500Hz, and this is
BEFORE the stimulus starts, so it cannot be stimulus leakage). The
broadband floor estimate (median FFT magnitude) is only marginally
higher for ON (0.0042) than OFF (0.0037) — the ON quiet region's much
higher RMS (15.06 vs. 2.84) is therefore NOT simply "a slightly raised
broadband floor" — it reflects a genuinely higher level of broadband,
multi-frequency activity across a specific ~450-820Hz range, not a
uniform gain increase across the whole spectrum, and not a match to any
later stimulus-driven pattern. **Wording used per instruction: "The AEC
ON run had a higher measured pre-stimulus baseline" — no claim is made
that enabling the AEC toggle itself caused this**, since this is a
single, un-repeated run pair and the two runs were not acoustically
or temporally controlled against each other beyond both using the same
stimulus generator and device defaults.

### 44i. OFF physical-playout verification — was the speaker signal actually present?

This is the critical check requested in §9: could OFF's low overall RMS
(6.74, per the operator's own figures) simply reflect that no
meaningful acoustic playout reached the room/microphone at all (i.e.
OFF≈6.74 would then say nothing about "excellent echo rejection")?

```
OFF, full 30s stimulus region:
  500Hz target magnitude  = 0.325   (vs. off-target 750Hz  = 0.014,  ~23x)
  1000Hz target magnitude = 0.184   (vs. off-target 1500Hz = 0.008,  ~23x)
  2000Hz target magnitude = 0.742   (vs. off-target 3000Hz = 0.002, ~371x)

OFF, quiet/pre-roll region (SAME exact frequencies, stimulus NOT yet playing):
  500Hz  = 0.036
  1000Hz = 0.021
  2000Hz = 0.018

Ratio (stimulus-playing / quiet, same frequency):
  500Hz  ≈ 9.0x
  1000Hz ≈ 8.8x
  2000Hz ≈ 41.2x
```

**Every target frequency is substantially elevated during stimulus
playback relative to BOTH the off-target reference bins in the same
window AND the identical frequency measured during the quiet/no-signal
region.** This is a real, internally cross-validated, quiet-vs-active
comparison, not a single arbitrary threshold. **Conclusion: physical
acoustic playout genuinely reached the reSpeaker microphone in the OFF
run — this is CONFIRMED, not merely plausible.** OFF's overall RMS of
6.74 is dominated by unrelated background noise (§44e/§44f), not by
this genuine-but-weak leakage — so OFF=6.74 should NOT be read as "6.74
worth of echo was well-suppressed"; it should be read as "a small,
verifiable, frequency-exact leakage component exists, embedded within a
larger amount of unrelated ambient/environmental noise."

### 44j. Limitations

- **Single, un-repeated run pair.** No statistical replication; a
  single OFF run and a single ON run, not a counterbalanced or repeated
  design. Any run-to-run acoustic/environmental/gain variation (§44h)
  cannot be separated from a true AEC-attributable effect with only one
  pair.
- **Cross-correlation lag is ambiguous modulo ~2ms** (§44g) due to the
  stimulus's own harmonic structure — absolute delay is not resolved,
  only the coarse presence/strength of correlation.
- **The exact mechanism behind the low time-domain correlation
  coefficients despite real frequency-domain presence is not resolved**
  — phase distortion, multipath/reverberation, and non-linear APM
  post-processing are all plausible; none is confirmed from Python-level
  analysis alone.
- **The exact source of the broadband (non-target-frequency) energy in
  both OFF and ON is not identified** — could be room/environmental
  noise, electrical interference, PipeWire/ALSA internal noise, or
  reSpeaker self-noise; not investigated further this round (out of
  scope for the audio-only AEC question).
- **This is a synthetic, non-speech, stationary-multitone stimulus.**
  It was specifically designed (§31-33) to remove a frequency-segment
  confound for TIME-stability analysis; it is not representative of
  real speech spectrally, temporally, or in level dynamics, and cannot
  by itself characterize how WebRTC AEC behaves against actual TTS/human
  speech.

## 45. Relation to NeXa self-interruption

The production concern this whole investigation traces back to:

```
NeXa speaks through speaker -> reSpeaker hears NeXa's own voice ->
residual echo reaches input -> VAD may classify it as user speech ->
NeXa interrupts herself
```

This round's evidence is **diagnostic only** — no Silero, no VAD, no
Gemini, no NeXa Core, no barge-in logic was exercised. What this round
DOES establish, narrowly: (1) `PlatformAudio`'s WebRTC AEC, under this
specific non-speech stimulus, does NOT reduce residual energy relative
to AEC OFF after the startup transient — it is measurably HIGHER,
including at the exact stimulus frequencies (§44f); (2) the excess is a
genuine mix of stimulus-frequency-locked and broadband content, not
purely artifactual noise (§44e); (3) the AEC ON startup transient is
large, abrupt, and takes several seconds to settle into an already
noisy, non-fully-stable range (§44d). None of this, by itself, tells us
whether a real Silero VAD instance would classify this residual as
speech — that depends on Silero's own model behavior against this
specific spectral/temporal shape, which is unmeasured here.

**Does the evidence support moving to a deterministic SPEECH stimulus
next? Yes** — the stationary_multitone diagnostic has done what it was
built for (isolate the audio/AEC layer from the R0082-B frequency-
segment confound, and now from Silero/VAD entirely) and has surfaced a
real, specific, needs-explaining result (AEC ON not reducing, and
apparently increasing, post-convergence residual under this stimulus).
Continuing to iterate on non-speech synthetic tones would not directly
answer the production question; a speech-shaped stimulus is the natural
next step, conceptually:

```
- a FIXED, prerecorded or TTS-generated speech WAV
- the SAME exact file played in both the OFF and ON conditions
- same speaker volume, same room/device positions as this round
- no human speech during the baseline/measurement window
- compare residual speech-band energy / speech-correlated content
  between OFF and ON, using the same kind of frequency- and
  correlation-based analysis as this round
- only THEN, as a separate later step, reintroduce Silero VAD to see
  whether the measured residual is large/speech-like enough to trigger
  false activation
```

**This experiment is proposed conceptually only. It is NOT executed
this round. No speech stimulus was generated or played.**

## 46. Updated evidence classifications

```
1.  R0082-C OFF captured real acoustic speaker leakage.                    CONFIRMED
2.  R0082-C ON captured real acoustic speaker leakage.                     CONFIRMED
3.  AEC ON has a startup/convergence transient.                            CONFIRMED
4.  The AEC ON startup transient decays with time.                        SUPPORTED
     (true in coarse trend; NOT smoothly monotonic -- oscillates with
     real re-spikes, see §44d)
5.  AEC ON remains above OFF after convergence.                            CONFIRMED
6.  The post-convergence excess is stimulus-correlated.                    SUPPORTED
     (real, dominant discrete energy at target frequencies -- see #7)
7.  The post-convergence excess is broadband/noise-like.                   SUPPORTED
     (most of the window's total RMS POWER is NOT at the 3 target
     tones -- both #6 and #7 are true simultaneously, see §44e)
8.  AEC ON run has a higher pre-stimulus baseline.                         CONFIRMED
     (measured fact only; NOT attributed to the AEC toggle itself --
     single un-repeated run pair, see §44h/§44j)
9.  WebRTC AEC is objectively worse for real speech.                       NOT PROVEN
     (this stimulus is non-speech; out of scope this round)
10. Current stationary_multitone result is sufficient to decide the
    NeXa production architecture.                                         NOT PROVEN
11. PlatformAudio split-process transport is stable enough to
    continue research.                                                    SUPPORTED
     (clean completion, healthy RTP stats, zero RaceDetected/
     truncation -- a separate axis from the AEC audio-quality question)
12. The result currently explains NeXa self-interruption.                  NOT PROVEN
     (no Silero/VAD was exercised this round)
13. The result currently proves NeXa self-interruption would be fixed
    (by enabling PlatformAudio AEC).                                       NOT PROVEN
     (current directional evidence argues AGAINST a "simple fix"
     narrative -- post-convergence residual is higher with AEC ON than
     OFF under this stimulus -- but this cannot be extended to real
     speech+VAD behavior without the speech-stimulus experiment, §45)
```

## 47. Recommended next experiment (proposed only — not executed)

Per §45: a deterministic, fixed speech stimulus (prerecorded or TTS),
played identically in OFF and ON conditions, same physical setup as
this round, analyzed with the same frequency/correlation methodology,
BEFORE Silero VAD is reintroduced into any test. **Not run this round.
No speech WAV was generated or played. No further hardware test was
executed.**

No production code (`src/`, `apps/`), NeXa Core, Gemini, Pipecat,
Silero, `BargeInController`, `AecReferenceFeeder`, XVF3800 DSP settings,
PipeWire defaults, system mixer, or LiveKit server configuration were
touched this round. No hardware test was run.

# R0082-D — deterministic SPEECH AEC OFF/ON comparison (preparation only)

## 48. Why speech is the next experiment

§44-47's `stationary_multitone` analysis found: OFF genuinely captured
real, frequency-exact speaker leakage (though small relative to
unrelated background noise); ON had a large, abrupt, non-monotonic
startup transient; and even after convergence, ON remained materially
ABOVE OFF, both overall and at the exact stimulus frequencies. That was
diagnostic-only, non-speech, and a single un-repeated run pair — it does
not by itself explain or predict NeXa's real self-interruption problem,
which depends on how a REAL SPEECH-shaped residual behaves relative to
what Silero VAD would classify as speech. R0082-D moves one step closer
to production relevance while still keeping Silero, Gemini, Pipecat, and
NeXa Core entirely out of scope. **No hardware test was run this
round** — this section documents preparation only.

## 49. Local TTS audit — no new system installed

Audited what local/offline TTS capability already exists in this repo
and on this Pi, per instruction, before generating anything:

- `piper-tts` 1.8.0 confirmed installed and usable. NeXa's own
  `src/nexa/tts/config.py` already defines the exact product voices:
  `EN_VOICE = "en_GB-jenny_dioco-medium"`, `PL_VOICE =
  "pl_PL-gosia-medium"` (both "medium" quality, native 22050Hz per each
  voice's own `.onnx.json` config — confirmed directly, not assumed).
- The DEDICATED external Piper venv `nexa.tts.config` itself specifies
  (`~/.local/share/nexa/tts/piper-http-venv`) exists and has both voice
  models present as real files under `~/.local/share/nexa/tts/voices/`
  (confirmed via `ls`, not assumed).
- **No new TTS system was installed. No new model was downloaded. No
  cloud/Gemini TTS was used.** `piper-http-venv`'s own environment was
  used exactly as `nexa.tts.config` already configures it — nothing was
  installed into it.

**A real, out-of-scope finding, not acted on:** `nexa.tts.config`'s own
docstring states `piper-tts` is installed in the external venv
"deliberately NOT NeXa's own `.venv`, so the GPL-3.0-licensed
`piper-tts` package is never imported into NeXa's own process." This
round's audit found `piper-tts` 1.8.0 is ALSO currently importable from
NeXa's own `.venv`
(`.venv/lib/python3.13/site-packages/piper/__init__.py` exists) —
appearing to contradict that documented licensing discipline. Recorded
here as an observed fact for a future round; **not fixed this round**
(out of scope — no production dependency surface was touched). This
round's speech-generation script deliberately used ONLY the intended
external venv, never NeXa's own `.venv`, regardless.

## 50. Frozen speech stimulus — generated once, immutable

New one-time preparation script (NOT part of the recurring test
harness): `docs/research/r0082_livekit_webrtc_audio_poc/
r0082d_generate_speech_stimulus.py`. Run in TWO stages, each with a
DIFFERENT already-existing environment, NEITHER modified by this work:

```bash
# stage 1 (Piper synthesis -- the dedicated external piper venv, unmodified)
/home/devdul/.local/share/nexa/tts/piper-http-venv/bin/python3 \
    docs/research/r0082_livekit_webrtc_audio_poc/r0082d_generate_speech_stimulus.py \
    --stage synthesize

# stage 2 (resample to the canonical 48kHz research WAV -- plain system
# python3, which already has numpy 2.2.4 + scipy 1.17.1, confirmed R0082-C;
# installing scipy into the piper venv itself was avoided for exactly this reason)
python3 docs/research/r0082_livekit_webrtc_audio_poc/r0082d_generate_speech_stimulus.py \
    --stage resample
```

**Content**: EN (Jenny voice) + a 0.6s deliberate silence gap + PL
(Gosia voice), chosen for natural phonetic diversity (voiced/unvoiced
sounds, plosives, sibilants, vowels, natural sentence rhythm and
punctuation-driven pauses) rather than for meaning:

```
EN: "Good afternoon. This is a fixed test recording, used only to
measure microphone echo. The quick brown fox jumps over the lazy dog,
testing plosive stops like pat, bat, kite, and goat."

PL: "Dzien dobry. To jest stale nagranie testowe, uzywane wylacznie do
pomiaru echa mikrofonu. Szybki lis przeskakuje nad leniwym psem,
testujac szumiace i zwarte spolgloski."
```

**One-time processing, exactly as done, nothing more:** each language
segment synthesized separately via `PiperVoice.synthesize_wav()` (each
voice's own natural prosody/pauses from punctuation; no per-word
tuning); concatenated with a 0.6s silence gap; resampled ONCE via
`scipy.signal.resample_poly` at the EXACT integer ratio `up=320,
down=147` (`48000/22050` reduced by `gcd=150` — not an approximation);
a resulting inter-sample peak of 33498.3 (from resampling
overshoot, a normal artifact of polyphase filtering on a nearly-full-scale
signal) exceeded int16 range, so a single documented ONE-TIME safety
scale of `0.9780` was applied to the whole file to avoid clipping — NOT
a per-run normalization, applied exactly once before freezing the
asset.

**Canonical asset**:

```
File:      docs/research/r0082_livekit_webrtc_audio_poc/r0082d_speech_stimulus/r0082d_speech_en_pl_v1.wav
Voices:    en_GB-jenny_dioco-medium (EN), pl_PL-gosia-medium (PL) -- NeXa's own configured product voices
Format:    mono, 16-bit (S16_LE), 48000Hz
Frames:    1,112,708
Duration:  23.181s  (within the preferred ~20-30s range)
RMS:       5542.45  (on the original int16 scale -- substantially louder
           than R0082-B/C's deliberately quiet amplitude=0.05 tones,
           ~1156 RMS -- a deliberate, documented choice: real production
           TTS output is not played at a deliberately attenuated level,
           so natural near-full-scale loudness (short of clipping) is
           MORE representative here, not less)
Peak:      32760  (of 32767 -- confirmed NOT clipping: peak < 32767)
SHA256:    703db210bcef8222adf044e88594958289be8d0e6ff4798ad8245b0c1076c21d
```

**This file is immutable.** It will not be regenerated between runs —
every OFF/ON run in the four-run sequence (§52) reads and publishes
these exact bytes.

## 51. R0082-D test harness — extends R0082-C, same split-process topology

New file (R0082-C's own script is UNCHANGED, kept as a stable
checkpoint of that stage): `docs/research/r0082_livekit_webrtc_audio_poc/
r0082d_platform_audio_speech_poc.py`.

**Preserved unchanged from R0082-C**: the two-genuinely-separate-OS-
process split (`--role hardware` / `--role test`, no shared `Room`, no
shared `PlatformAudio`, no Python `multiprocessing`, no `fork()`-based
worker model, `livekit==1.1.19` unchanged); device-default verification
instead of explicit selection (`AudioDeviceInfo.id` still always empty
on this platform); the local dev `livekit-server` in the session
scratchpad; the event-based mutual `track_subscribed` handshake and
named timing constants (`SETTLE_S`/`SUBSCRIBE_TIMEOUT_S`/
`CLEANUP_GRACE_S`); capture-completeness validation (a short capture
FAILS the run outright — no zero-padding, no partial classification,
unchanged from R0082-C's own fix).

**New in R0082-D**:

- `--stimulus-wav <path>` (required for BOTH roles) replaces
  `--stimulus`/`--amplitude`/`--duration`. `_read_wav_validated()`
  REJECTS (raises `SystemExit`, tested explicitly this round) any WAV
  that is not already mono/16-bit/48000Hz — it never silently resamples
  or coerces the file at run time. Verified against a deliberately
  wrong-format (22050Hz) test WAV: correctly rejected with a clear
  diagnostic; verified against the real frozen speech asset: correctly
  accepted, with `n_samples`/`duration_s`/`sha256` all matching exactly.
- **Duration synchronization**: the hardware role reads ONLY the
  `--stimulus-wav` header (never plays anything itself — playout is
  automatic via PlatformAudio once it subscribes to the test role's
  track) to derive `duration_s` directly from the WAV's own frame
  count/rate, instead of a manually-duplicated `--duration` CLI value.
  Both roles are given the SAME `--stimulus-wav` path and therefore
  derive an IDENTICAL duration independently — no duplicated number, no
  new unexplained magic sleep. The hardware role's hold is still exactly
  `SETTLE_S + PRE_ROLL_S + duration_s + TAIL_S + CLEANUP_GRACE_S`,
  printed in full at runtime.
- **Signal evidence integrity**: the "signal" WAV written as evidence is
  the exact PCM read from the frozen asset (not regenerated), and its
  own SHA256 is computed and compared against the frozen source's SHA256
  at the end of every run, printed as `matches frozen source: True/False`
  — this round's dry run confirmed `True`.

## 52. Offline validation (no hardware touched)

```
isolated-venv import                              PASS
--help                                             PASS
py_compile (both new scripts)                      PASS
ruff                                               2 line-length errors
                                                    found (docstring
                                                    usage example) and
                                                    fixed -- 0 on re-check
git diff --check                                   PASS
isolation check (no nexa/pipecat/loguru/Silero/
  Gemini-SDK modules on import)                    PASS
canonical WAV header validation (accept path)       PASS -- real frozen
                                                    asset: n_samples=
                                                    1112708, duration=
                                                    23.181s, sha256
                                                    matches exactly
canonical WAV header validation (reject path)       PASS -- a
                                                    deliberately
                                                    22050Hz test WAV was
                                                    correctly REJECTED
                                                    with SystemExit, not
                                                    silently resampled
PCM frame publication + full split-process
  dry run (synthetic hardware stand-in, real
  frozen speech WAV actually published)             PASS -- see below
cleanup validation                                  PASS -- zero
                                                    FfiHandle assertion
                                                    errors, zero crash
                                                    signatures
```

**Full synthetic split-process dry run**, real frozen speech WAV
actually read and published (throwaway synthetic hardware stand-in,
identity `r0082d_hardware`, kept only in the session scratchpad,
deleted after use -- exercised the REAL `run_test_role`/
`_read_wav_validated`/`_play_signal` functions unmodified, from the
real script):

```
fake-hardware pid=1363837   test pid=1363909   (distinct processes, both exited 0)

[test] stimulus-wav: .../r0082d_speech_en_pl_v1.wav  n_samples=1112708
       duration=23.181s  sha256=703db210...076c21d
[test] test subscribed to hardware mic
[test] playing 23.181s speech stimulus
[test] capture complete
[test] capture completeness check:
  expected: 1208708 samples / 2417416 bytes / 25.181s @ 48000Hz mono S16_LE
  actual:   1209120 samples / 2418240 bytes / 25.190s
[test] wrote signal evidence sha256=703db210...076c21d (matches frozen source: True)
```

Duration was correctly derived from the WAV header (23.181s, exactly
matching §50's frozen asset); the capture-completeness check correctly
passed (actual ≥ expected); the written signal evidence file is
byte-identical to the frozen source (SHA256 match confirmed
programmatically, not just visually). Zero `FfiHandle` assertion errors,
zero crash signatures of any kind in either process's log. Throwaway
harness and its WAV output were deleted after use; the local dev server
was stopped after validation.

`PlatformAudio()` was NOT invoked by this session. No real hardware test
was run.

## 53. Four-run counterbalanced experimental design

A single OFF/ON pair (as R0082-C used) cannot separate a true AEC
effect from run-to-run variation — R0082-C's own OFF/ON pair had
materially different pre-stimulus quiet baselines (2.84 vs. 15.06 RMS,
§44h). R0082-D therefore specifies a counterbalanced FOUR-run sequence:

```
Run A1 = AEC OFF
Run B1 = AEC ON
Run B2 = AEC ON
Run A2 = AEC OFF
```

(OFF → ON → ON → OFF, per instruction — no strong technical reason was
found to prefer the reverse ordering.) Every run uses: the EXACT same
`r0082d_speech_en_pl_v1.wav` (same SHA256, printed and checked every
run); the same speaker volume; the same physical device positions; the
same PipeWire defaults; the same `PlatformAudioOptions` except
`echo_cancellation` (`noise_suppression=False`, `auto_gain_control=False`
throughout, unchanged from R0082-C); no human speech during any run; no
moving the mic/speaker between runs; a unique `--room-name` per run.
**None of these four runs have been executed. This round prepares the
harness and the asset only.**

### Per-run data to collect (unchanged shape from R0082-C, per instruction)

AEC mode; room name; stimulus SHA256; stimulus duration; capture
expected/actual samples and duration; quiet/pre-roll RMS and peak;
per-3s-window RMS/peak (already what the harness computes and prints);
overall/first-half/second-half means; min/max. Both the speech stimulus
WAV and the processed microphone WAV are retained for every run (as
R0082-C's own evidence convention already does). **Capture completeness
remains mandatory — an incomplete run FAILS outright and is not
classified**, unchanged from R0082-C's own fix (§40).

## 54. Offline analysis plan (for after the four runs — not run yet)

Planned, not yet executed: lag-tolerant normalized cross-correlation
(now more informative than R0082-C's own analysis, §44g, since real
speech is non-periodic and does not have the `stationary_multitone`
stimulus's ~2ms ambiguity); best correlation coefficient per run; an
estimated acoustic/system lag where the correlation is strong enough to
be defensible; speech-band RMS over time; stimulus-correlated residual
per run; quiet-baseline comparison across all four runs (not just one
pair); a spectrogram/STFT-derived speech-band comparison if it proves
useful once real data exists; paired OFF-vs-ON comparisons (A1 vs. B1,
A2 vs. B2, and B1 vs. B2 for ON-to-ON replication, A1 vs. A2 for
OFF-to-OFF replication) per §55's success criteria. **No formal ERLE
figure will be computed unless the setup is confirmed to support one
correctly** — residual attenuation/comparison is the wording used
instead, matching R0082-C's own established convention (§21 of this
report).

## 55. Success criteria for this stage (not yet evaluated — no data exists)

This stage does **not** test Silero. The question is narrower: does
`PlatformAudio` WebRTC AEC reduce a real speech-shaped self-playback
signal reaching the mic path?

```
Strong positive evidence: ON runs (B1, B2) consistently show LOWER
  speech-correlated residual than OFF runs (A1, A2), AND the result
  REPLICATES across both ON trials and both OFF trials.

Bad/ambiguous evidence: e.g. A1 low, B1 high, B2 low, A2 high -- would
  indicate strong run/state variability dominating over any simple AEC
  effect, not a clean toggle-driven result.
```

## 56. Important wording constraint carried forward from R0082-C

R0082-C's own analysis (§44i) confirmed stimulus-associated leakage
reaches the microphone path, but did not fully prove every bit of that
leakage has a purely acoustic (through-the-air) origin as opposed to
some other coupling path. R0082-D preserves this distinction explicitly
and will not overstate it:

```
stimulus-associated leakage into the microphone path: CONFIRMED (R0082-C)
purely acoustic origin of ALL leakage: NOT FULLY PROVEN
```

## 57. Limitations

- **No hardware test has been run for R0082-D.** Everything in this
  section is preparation, asset generation, and offline-validated
  harness code — not a result.
- The speech stimulus is a fixed, non-interactive, single Piper-
  synthesized recording — it is not identical to live conversational
  speech in dynamics or content, though it exercises real phonetic
  diversity (plosives, sibilants, vowels, natural pauses) that
  `stationary_multitone` could not.
- The frozen asset's loudness (RMS ≈5542, near-full-scale peak) is a
  deliberate departure from R0082-B/C's quiet amplitude=0.05 convention
  (§50) — this makes it NOT directly RMS-comparable to R0082-B/C's own
  synthetic-tone results without normalization; any cross-stage
  comparison must account for this explicitly.
- The four-run design addresses run-to-run baseline variability
  (§53) but still uses only ONE physical setup/session — genuine
  environmental drift across the whole four-run sequence (e.g. if it
  spans a long wall-clock period) is not separately controlled for.
- Silero VAD, Gemini, Pipecat, and NeXa Core remain entirely
  unexercised — this stage cannot, by itself, answer whether a real
  self-interruption event would occur; it answers only the narrower
  audio-residual question (§55).

## 58. Relationship to NeXa self-interruption

Unchanged framing from §45: the ultimate question is whether NeXa's OWN
SPEECH survives PlatformAudio's WebRTC AEC path strongly enough to
trigger Silero VAD's `UserStartedSpeaking` classification. R0082-D is
the first stage that uses REAL SPEECH instead of synthetic tones, and
is still explicitly diagnostic — Silero is deliberately deferred to a
LATER, separate stage (not implemented or run this round):

```
(future, NOT this round)
NeXa speech plays, user stays silent -> Silero should produce 0 false
  UserStartedSpeaking events

then, separately:
NeXa speech plays, real user interrupts -> Silero should detect real
  speech promptly
```

R0082-D's own four-run OFF/ON/ON/OFF result (once actually run) will
determine whether it is scientifically justified to proceed to that
Silero stage at all, or whether the audio-residual problem itself needs
further isolation first.

## 59. Four-run counterbalanced speech AEC OFF/ON result — offline analysis

The operator ran the four-run sequence (§53). **No new hardware test was
run this round** — this section is offline analysis of the existing
captures only.

### 59a. File verification

All four run_ids located under `docs/research/r0082_livekit_webrtc_audio_poc/
r0082d_aec_captures/`:

```
A1_OFF (20260918T144322Z): signal sha256=703db210...076c21d  mic sha256=dd4bbfd9...2b75196
B1_ON  (20260918T144740Z): signal sha256=703db210...076c21d  mic sha256=fef4e9bd...bed47d04
B2_ON  (20260918T144932Z): signal sha256=703db210...076c21d  mic sha256=dc79b724...19f5f9799
A2_OFF (20260918T145221Z): signal sha256=703db210...076c21d  mic sha256=8596fbda...4a0eb39
```

All four signal WAVs SHA256-match the frozen canonical stimulus
(`703db210bcef8222adf044e88594958289be8d0e6ff4798ad8245b0c1076c21d`)
**exactly** — confirmed programmatically, not visually. All four mic
captures: mono, 16-bit, 48000Hz, 1,209,120 frames, 25.190s — exceeding
the expected minimum (1,208,708 samples / 25.181s = `PRE_ROLL_S +
23.181416...s + TAIL_S`), i.e. all four captures are complete.

**Data-hygiene note:** a FIFTH WAV pair
(`r0082d_aecoff_20260918T145123Z_speech_{signal,mic}.wav`) also exists
in the capture directory, timestamped between B2 and A2, but was **not**
one of the four run_ids the operator reported. It is excluded from the
analysis below and flagged here for transparency rather than silently
ignored — most likely an earlier attempt at A2 that was discarded before
the reported A2 run.

New analysis script (research-only, plain system `python3`, numpy+scipy,
no `nexa.*`/`pipecat.*`/`livekit`/production imports):
`docs/research/r0082_livekit_webrtc_audio_poc/r0082d_wav_analysis.py`.

### 59b. A real bug found and fixed during this analysis

The first draft of the lag-search function initialized its
"best correlation found so far" tracker to `-1.0` (the theoretical
maximum possible correlation MAGNITUDE) and compared with `abs(c) >
abs(best_corr)`. Since genuine correlation coefficients for real,
independent, only-weakly-related recordings never reach exactly ±1.0,
this comparison could never succeed — the loop silently never updated
its result, and a LATER unrelated offset calculation happened to
convert the untouched default into a value that looked like a real
(and suspicious) result: **all four runs reporting `best_lag=-300.00ms`
exactly (the edge of the search range) with `peak_corr_coeff=-1.0000`
exactly** — an impossible outcome for four independent real recordings,
caught precisely because it was too clean to be real. Fixed by
initializing the tracker to `0.0` (beaten by any nonzero correlation).
**Verified correct** with a synthetic controlled test: a known signal
embedded in noise at a known 300-sample offset, at a weak 2% amplitude
with realistic background noise, was recovered by the fixed function at
exactly the correct offset with a plausible correlation coefficient
(0.97) — confirming the fix, not just the absence of the previous
symptom.

### 59c. Lag alignment — a real methodology limitation, stated plainly

After the fix, real (non-degenerate) results:

```
A1_OFF: best_lag= -234.46ms  peak_corr_coeff=-0.0195
B1_ON:  best_lag= -149.75ms  peak_corr_coeff=+0.0138
B2_ON:  best_lag=  -46.98ms  peak_corr_coeff=-0.0128
A2_OFF: best_lag= -208.21ms  peak_corr_coeff=+0.0142
```

**These peak correlation coefficients are very low** (|r| 0.013-0.020,
r² 0.0002-0.0004) — essentially no strong, sample-domain linear
relationship was found between the raw reference PCM and ANY of the
four captured mic recordings, in EITHER AEC condition. This is treated
as a genuine methodology limitation of raw-waveform cross-correlation
for this specific signal/pathway, not as evidence that no acoustic
coupling exists: the played stimulus and the captured uplink each pass
through LiveKit's own Opus encode/decode (a lossy, perceptually-tuned
codec, likely applied twice — once on the downlink to the speaker, once
on the uplink from the mic), through the real acoustic
speaker→room→microphone path (frequency-response coloration,
reverberation), and through PlatformAudio's APM (even with
`noise_suppression=False`/`auto_gain_control=False`, the AEC path
itself performs adaptive filtering that reshapes phase/waveform). All
of these can reduce raw sample-domain correlation without eliminating
genuine physical leakage — R0082-C's own tone-based analysis (§44g)
found a similar, if less extreme, pattern (weak time-domain correlation
coexisting with clear frequency-domain evidence of real leakage).
**Consequence: the derived lag estimates and the "stimulus-correlated
component" metric (§59d) built on them are LOW-CONFIDENCE for this
dataset** — the full-region RMS, time-resolved, and STFT band-power
results (which do not depend on this correlation being strong) are
treated as the more trustworthy evidence for the comparisons below.

### 59d. Full-duration per-run metrics (not the runtime-truncated 7×3s windows)

The runtime harness only printed 7 complete 3-second windows (21s) —
per instruction, all metrics below are recomputed from the saved WAVs
over the FULL 23.181s aligned speech region:

```
         quiet_rms  quiet_peak  full_rms  full_peak  stim_component_rms  residual_rms  r²
A1_OFF     153.35      1480       56.74      1487           1.11           56.73      0.0004
B1_ON        5.12        22       38.60      1422           0.53           38.60      0.0002
B2_ON        5.88        27       41.15      1838           0.53           41.15      0.0002
A2_OFF       5.67        24       27.67      1131           0.39           27.67      0.0002
```

The "stimulus-correlated component" is two-plus orders of magnitude
smaller than "full_rms" in every run (§59c's limitation directly
visible here) — the overwhelming majority of captured energy, in ALL
FOUR runs including both OFF runs, is NOT explained by a simple
scaled/shifted copy of the reference signal at this method's
sensitivity. This is reported honestly rather than forced into a
stronger claim.

### 59e. A1 pre-roll anomaly — a decaying startup transient, not steady noise

```
A1_OFF pre-roll: mean(DC)=-1.563  rms=153.35  peak=1480
  first-half rms=216.38  second-half rms=14.60   <- DECAYS within the 1s window itself
  top spectral peaks: 132.0Hz(8.43) 196.0Hz(3.54) 245.0Hz(2.90) 116.0Hz(2.87) 175.0Hz(2.80) 148.0Hz(2.26)
  broadband floor (median FFT magnitude): 0.0043

B1_ON pre-roll: rms=5.12   first-half=5.21  second-half=5.03  (flat, no transient)
  broadband floor: 0.0043
B2_ON pre-roll: rms=5.88   first-half=6.26  second-half=5.47  (flat, no transient)
  broadband floor: 0.0043
A2_OFF pre-roll: rms=5.67  first-half=5.61  second-half=5.72  (flat, no transient)
  broadband floor: 0.0043
```

**A1's pre-roll is a DECAYING TRANSIENT** (216→14.6 rms within one
second), dominated by LOW frequencies (116-245Hz, magnitudes 3-8x
larger than B1/B2/A2's own quiet-region peaks) — not steady broadband
noise, not a DC offset (mean ≈ -1.6, negligible), not a periodic tone
matching the stimulus. **The broadband floor (median FFT magnitude) is
IDENTICAL across all four runs (0.0043), including A1** — the
persistent, steady-state noise characteristic is the SAME in every run;
A1's anomaly is specifically a TRANSIENT that had not fully decayed by
the end of the 1-second pre-roll window, not a change in the underlying
noise floor. Since A1 was the FIRST of the four runs, a
connection/session warm-up artifact (e.g. PipeWire stream negotiation,
WebRTC ADM initial buffer ramp-up) is a plausible, evidence-consistent
explanation — this is recorded as SUPPORTED, not CONFIRMED (no repeated
"first run of a session" trial exists to test reproducibility). A1's
elevated speech-region `full_rms` (56.74, the highest of all four runs)
is plausibly still affected by the tail of this same transient bleeding
into the early speech region — **A1 is therefore NOT treated as a clean,
representative AEC-OFF baseline** for the comparisons below.

### 59f. Pairwise comparisons

```
A1_OFF vs B1_ON:  full_rms 56.74 -> 38.60   ratio=0.680x  -3.35dB   (ON lower -- but A1 is confounded, §59e)
B2_ON  vs A2_OFF: full_rms 41.15 -> 27.67   ratio=0.672x  -3.45dB   (OFF lower -- the OPPOSITE direction)
A1_OFF vs A2_OFF: full_rms 56.74 -> 27.67   ratio=0.488x  -6.24dB   (both OFF -- large gap, explained by A1's own anomaly, §59e)
B1_ON  vs B2_ON:  full_rms 38.60 -> 41.15   ratio=1.066x  +0.56dB   (both ON -- close, good replication)
```

**§59f is the central finding of this round.** The single CLEANEST
comparison — `B2_ON vs A2_OFF`, explicitly the one the brief flagged as
most important, with near-identical quiet baselines (5.88 vs. 5.67) and
adjacent in the run sequence — shows AEC **OFF** with the LOWER residual
(27.67 vs. 41.15, a -3.45dB difference in OFF's favor). This is the
OPPOSITE direction from the `A1_OFF vs B1_ON` comparison, which is
itself unreliable because A1 is confounded by its own startup transient
(§59e). The two ON runs (B1, B2) replicate each other closely (+0.56dB
apart) — but the two OFF runs (A1, A2) do NOT (−6.24dB apart), and that
gap is attributable to A1's own anomaly rather than to the AEC toggle.
**No absolute significance is claimed with n=2/condition**, per
instruction.

### 59g. Run-order trend — the apparent monotonic pattern does not survive full-duration recomputation

The operator's own runtime-reported `overall mean` (the truncated
7-window/21s figure) showed an apparently clean monotonic decline:
`A1=38.84 → B1=26.84 → B2=18.70 → A2=17.70`. **Recomputed over the full
23.181s aligned region, this monotonicity breaks down:**

```
A1_OFF: full_rms=56.74   B1_ON: full_rms=38.60   B2_ON: full_rms=41.15   A2_OFF: full_rms=27.67
```

`B2 (41.15) > B1 (38.60)` — a real increase, not a continued decline.
The apparent "smooth warm-up" story in the runtime numbers was partly an
artifact of only using the first ~21s rather than the complete 23.181s
stimulus. The pattern that DOES survive: a large drop from A1 to B1
(plausibly A1's own startup-transient recovery, §59e, not necessarily
an AEC effect), followed by a relatively flat/mixed B2-A2 pair. This is
recorded as a **HYPOTHESIS** (first-run warm-up), not a confirmed
general "progressive warm-up/stabilization" trend across the whole
sequence.

### 59h. Time-resolved analysis — a shared transient near t≈6-7s across 3 of 4 runs

250ms- and 1s-resolution RMS tables (lag-aligned per run, first 10s of
speech-relative time) both show a large, elevated-energy region
clustering around **t≈6.0-6.75s in A1, B1, and B2** (1s-window values:
A1=83.2, B1=30.1, B2=74.6 at the t=6-7s bin), while **A2's own largest
early peak appears about 1s later, at t=7.0-8.0s (43.2)**. Three
independent sessions showing elevated energy clustering within roughly
the same ~1s region of the aligned playback, out of a 23s/~93-bin table,
is unlikely to be pure coincidence — this **partially supports**
stimulus-dependent (spoken-content-tied) leakage, consistent with this
region corresponding to a specific loud phrase/phoneme in the EN
segment of the stimulus. It is not perfectly reproducible in exact
onset time across all four runs (A2 is offset by ~1s), which may
reflect genuine run-to-run timing/state variability, or residual
imprecision in the low-confidence lag estimates themselves (§59c) —
both are plausible and not distinguished by this analysis.

### 59i. STFT-derived band power — no consistent OFF/ON pattern in any band

```
                300-1000Hz  1000-2000Hz  2000-4000Hz  4000-8000Hz
A1_OFF            12.512        1.280        0.637        0.141
B1_ON              7.101        1.310        0.634        0.243
B2_ON              8.862        1.706        0.886        0.113
A2_OFF             5.624        1.819        0.753        0.166
```

No band shows AEC OFF consistently above or below AEC ON across both
OFF/ON pairs — each band ranks the four runs differently, and A2 (OFF)
has the LOWEST 300-1000Hz power of all four runs (the band voiced
speech dominates), not the highest. This is additional evidence against
a clean, general "AEC ON reduces residual" story, and further supports
that run/state variability, not the AEC toggle, dominates these
differences.

## 60. Updated evidence classifications

```
1.  All four runs used byte-identical speech stimulus.              CONFIRMED
2.  All four captures are complete.                                 CONFIRMED
3.  A1 has an anomalous pre-stimulus baseline.                      CONFIRMED
4.  A1 is representative of normal AEC OFF behaviour.                REFUTED
5.  B1 and B2 are mutually reproducible.                            SUPPORTED
6.  A2 is consistent with the low-baseline state seen in B1/B2.     SUPPORTED
7.  AEC ON consistently lowers total mic RMS.                     NOT PROVEN
     (the cleanest pair, B2 vs A2, shows the OPPOSITE)
8.  AEC ON consistently lowers speech-correlated residual.        NOT PROVEN
     (same B2-vs-A2 result; correlation metric itself low-confidence, §59c)
9.  AEC OFF consistently performs worse than ON.                     REFUTED
     (as a general claim -- B2-vs-A2 shows OFF performing BETTER)
10. Run/state variability is substantial.                          CONFIRMED
11. Run/state variability is larger than the AEC effect.            SUPPORTED
     (conservative -- n=2/condition, not asserted as statistically proven)
12. Evidence of progressive warm-up/stabilization over run order.  HYPOTHESIS
     (A1->B1 drop plausible as first-run recovery; B1->B2->A2 does
     NOT continue the trend once full-duration-recomputed, §59g)
13. WebRTC AEC currently provides production-worthy echo
    suppression.                                                  NOT PROVEN
14. Current evidence proves NeXa self-interruption is solved.        REFUTED
15. Current evidence is sufficient to proceed to an OFFLINE
    Silero-on-captured-WAV experiment.                              SUPPORTED
```

## 61. Relation to NeXa self-interruption

```
Would the residual speech present in these mic WAVs plausibly still
look speech-like enough to trigger VAD?
```

**NOT PROVEN until Silero is run offline on the saved mic WAVs** — per
instruction, this is not answered by intuition. The waveform/correlation
evidence gathered this round (§59c) is explicitly low-confidence for
detecting fine-grained stimulus correlation, and total RMS alone
(§59d/§59f) does not by itself indicate whether Silero's own model would
classify any given residual segment as speech. The one piece of evidence
worth carrying forward: §59h's shared t≈6-7s transient across 3 of 4
runs is a concrete, real candidate event to check directly against
Silero's own response, rather than a synthetic assumption.

## 62. Next experiment — chosen: (A) offline Silero VAD on the four existing mic WAVs

Of the three options offered:

```
A) offline Silero VAD analysis of the four already-recorded mic WAVs
B) another controlled hardware replication is required first
C) AEC/PlatformAudio configuration must be investigated first
```

**(A) is chosen.** Reasoning: it is the cheapest possible next step (no
new hardware run, reuses the exact captures already gathered and
verified this round), and it directly answers the question that
actually matters for production risk — whether Silero would classify
any of this residual as speech — rather than continuing to refine the
AEC-effect-vs-variability question in the abstract. Its result
determines whether (B) or (C) is even worth pursuing: if Silero
produces zero false triggers on all four WAVs (including the A1 anomaly
and the shared t≈6-7s event), the current audio-layer ambiguity may not
matter for the production question at all; if Silero DOES trigger,
that gives a concrete, reproducible test case to drive either (B)
further hardware replication or (C) AEC configuration work, instead of
guessing which is needed. **This offline Silero step is NOT executed
this round** — it is the recommended next step only, per instruction.

## 63. Limitations (this round)

- n=2 per AEC condition — no statistical significance is claimed
  anywhere in this section, per instruction.
- The sample-domain cross-correlation method proved low-confidence for
  this dataset (§59c) — lag estimates and the "stimulus-correlated
  component" metric built on them should be treated as suggestive, not
  authoritative; the RMS/time-resolved/band-power results are weighted
  more heavily in the conclusions above for this reason.
- A1's confound (§59e) means the four-run design's intended
  counterbalancing is effectively weakened to "one clean OFF (A2), two
  ON (B1, B2), one confounded OFF (A1)" rather than a clean 2-vs-2 — a
  real limitation of this specific run, not of the four-run design
  itself.
- The shared t≈6-7s transient (§59h) is suggestive but not definitively
  isolated to a specific word/phoneme in this round — no forced
  alignment against the stimulus text's own timing was attempted.
- No production code, NeXa Core, Gemini, Pipecat, Silero, Silero
  production integration, `BargeInController`, `AecReferenceFeeder`,
  XVF3800 settings, PipeWire defaults, system mixer, or LiveKit server
  configuration were touched this round. No hardware test was run. No
  DSP writes were made.
