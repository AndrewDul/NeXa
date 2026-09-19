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

# R0082-E — offline production-equivalent Silero VAD analysis

## 64. Purpose

Would NeXa's CURRENT, frozen production Silero/VAD/interruption
configuration classify any part of the four already-recorded R0082-D
self-echo mic captures as user speech? **No new hardware test, no
speaker playback, no Gemini, no real barge-in, no NeXa conversation run
this round** — this is offline analysis of existing evidence only.

## 65. Exact production Silero/VAD/interruption configuration (audited from source, not memory)

A dedicated read-only audit of the actual repository source (not
generic Silero defaults, not memory) found:

- **Package/model**: NeXa uses Pipecat's own
  `pipecat.audio.vad.silero.SileroVADAnalyzer` directly — no custom
  wrapper. `src/nexa/voice/runtime.py:23,509-511`. The underlying model
  is the ONNX file bundled inside the installed `pipecat-ai` 1.8.1
  wheel (`pipecat/audio/vad/data/silero_vad.onnx`, confirmed sha256
  `597d30b3ec076608d059477bb14cfeffdf951bf5cae370d38f65d33bbfe82004`,
  2,327,524 bytes), inference via `onnxruntime.InferenceSession`
  (`silero.py:54-58`), CPU execution provider forced. No separate
  `silero-vad` PyPI package is used; no PyTorch dependency.
- **Sample rate**: Silero itself supports only 8000 or 16000 Hz
  (`silero.py:61,93,184-187`). Production feeds it **16000 Hz mono**
  (`LocalAudioConfig.sample_rate=16000`, `src/nexa/voice/config.py:63-64`,
  passed straight through, `runtime.py:509-510`).
- **Preprocessing/resampling**: **none, in Python.** The physical
  reSpeaker's own native capture rate is already 16 kHz
  (`config.py:38-42`'s own docstring); the only conversion actually
  performed is stereo→mono downmix, done inside ALSA's own `plug:` PCM
  plugin, not by any NeXa or Python code. No `resampy`/`soxr` call site
  exists anywhere in the frozen local M2.5B path.
- **Frame size**: 512 samples = 32ms at 16kHz (`silero.py:191-197`,
  `vad_analyzer.py:159-160`).
- **Thresholds actually configured** (not library defaults assumed):
  `DEFAULT_VAD_PARAMS = VADParams(stop_secs=1.0)`
  (`src/nexa/voice/runtime.py:87`), used UNMODIFIED by the real
  entrypoint (`apps/nexa_bilingual_voice_probe.py` constructs
  `VoiceRuntime` without passing `vad_params`). Everything else is
  Pipecat's own library default (`vad_analyzer.py:25-28`):
  **`confidence=0.7`, `start_secs=0.2`, `stop_secs=1.0` (NeXa's own
  override, from a documented calibration sweep,
  `runtime.py:56-86`), `min_volume=0.6`**.
- **Single threshold, not separate start/stop**: `confidence` gates
  both directions symmetrically; only the DURATION windows differ
  (`vad_analyzer.py:211`).
- **Hysteresis/state machine**: 4 states
  QUIET→STARTING→SPEAKING→STOPPING→QUIET (`vad_analyzer.py:213-246`).
  A frame counts as "speaking" iff **`silero_confidence >= 0.7 AND
  smoothed_volume >= 0.6`** — a two-gate AND condition, not confidence
  alone. `_vad_start_frames = round(0.2/(512/16000)) = 6` frames
  (192ms) of consecutive speaking frames confirms `SPEAKING` (this is
  the real trigger point for `VADUserStartedSpeakingFrame`).
  `_vad_stop_frames = round(1.0/(512/16000)) = 31` frames (~992ms) of
  consecutive non-speaking frames confirms `QUIET` again. A brief dip
  during `STOPPING` cancels the pending stop rather than restarting
  from `QUIET` (`vad_analyzer.py:220-222`).
- **Volume**: `AudioVolumeTracker` (`pipecat.audio.volume`) — rolling
  400ms window, ITU-R BS.1770 integrated loudness via the REAL
  `loudness` package (v0.2.0, a standalone pybind11 binding, confirmed
  installed and importable with zero `loguru`/`pipecat`/`nexa`
  side-imports), normalized `(LUFS-(-110))/((-10)-(-110))` clamped to
  [0,1] (`pipecat/audio/utils.py:147-187`), then exponentially smoothed
  (factor=0.2, `vad_analyzer.py:87,176,208-209`).
- **Silero's own recurrent state**: resets every
  `_MODEL_RESET_STATES_TIME=5.0` seconds of **wall-clock** time in the
  real code (`silero.py:23,214-220`) — a memory-growth mitigation,
  unrelated to speech boundaries. Reproduced here via AUDIO-TIME
  bookkeeping instead of `time.time()` (documented substitution, §66 —
  batch offline processing would otherwise never trigger this reset if
  run faster than real time; in a live continuous capture wall-clock
  and audio time advance together, so this is a faithful reproduction
  of the INTENDED behavior, not a threshold/logic change).
- **Speech padding**: not in Silero/Pipecat at all — NeXa implements a
  separate 500ms pre-roll ring buffer downstream in
  `nexa.stt.utterance_buffer.UtteranceBuffer` (`PRE_ROLL_MS=500`),
  seeded on `VADUserStartedSpeakingFrame`. Not exercised by this round
  (out of scope — this round tests whether that frame fires at all,
  not what happens to STT after it does).
- **NeXa's own confirm-hold layer** (Level 3, §66):
  `nexa.voice.interruption.InterruptionStateMachine`
  (`IDLE/RESPONDING/INTERRUPT_CANDIDATE/INTERRUPTING`), sitting
  immediately after `VADProcessor` in the real pipeline
  (`runtime.py:519-521`). `DEFAULT_CONFIRM_HOLD_SECS=0.3`
  (`interruption.py:44`), not overridden anywhere in the real wiring
  (`src/nexa/voice_tts/bargein_wiring.py:145-150`). A candidate
  confirms only if it holds ≥0.3s with no intervening
  `VADUserStoppedSpeakingFrame` (`interruption.py:211-232`). Total
  speech-onset→confirmed budget ≈ 0.2s (Silero start_secs) + 0.3s
  (NeXa hold) ≈ 0.5s.
- **Explicitly out of scope, documented, not modeled**:
  `BargeInController`'s separate `AecReferenceHealth.barge_in_safe`
  gate (`bargein.py:326-340`) — a precondition specific to the CURRENT
  production XVF3800/`AecReferenceFeeder` hardware AEC path (R0028),
  unrelated to the audio content itself and unrelated to the
  PlatformAudio/WebRTC path R0082 is evaluating as a potential
  replacement. Level 3 below answers "would `InterruptionStateMachine`
  confirm an interrupt candidate from this audio", not "would the FULL
  current `BargeInController`, including its XVF3800-specific safety
  gate, admit it" — a stated scope boundary.
- **One config, confirmed current**: `DEFAULT_VAD_PARAMS` is the single
  live production config, reused byte-for-byte by every M2.5-era
  hardware spike. The M2.6 CLOUD (Gemini Live) pipeline uses a
  DIFFERENT `stop_secs=0.5` in a separate `GeminiVoiceRuntime` — not
  the frozen local M2.5B path this round targets, and unaffected by it.

## 66. Preprocessing path and harness design

```
R0082-D mic capture (48000Hz mono S16_LE, LiveKit/WebRTC's own internal
  rate — NOT the same signal path as reSpeaker's own native ALSA
  capture)
  -> stage 1 (plain system python3, scipy.signal.resample_poly,
     exact 1/3 ratio, anti-aliasing lowpass included — a real,
     necessary adaptation because THIS evidence happens to be captured
     at 48kHz via the R0082 LiveKit pipeline, unlike production's own
     already-16kHz-native reSpeaker path; NOT a per-run
     normalization/loudness adjustment)
  -> 16000Hz mono S16_LE canonical Silero input
  -> stage 2 (NeXa's own .venv python3 — deliberately chosen here,
     unlike the LiveKit PoC scripts, because it has the EXACT
     `onnxruntime`+`loudness` versions production itself uses;
     numerical fidelity, not a violation of R0082's hardware-script
     isolation discipline, which doesn't apply to this offline,
     non-hardware analysis)
  -> Level 1: SileroOnnxModel (verbatim reproduction of
     pipecat.audio.vad.silero's ONNX wrapper, same bundled model file,
     not imported from pipecat directly)
  -> Level 2: VADAnalyzer state machine (verbatim reproduction of
     pipecat.audio.vad.vad_analyzer's exact algorithm/constants)
  -> Level 3: nexa.voice.interruption.InterruptionStateMachine — the
     REAL production class, loaded via
     importlib.util.spec_from_file_location directly from
     src/nexa/voice/interruption.py, bypassing nexa/voice/__init__.py's
     own import chain (which pulls in pipecat/loguru via bargein.py)
     entirely. Confirmed this round to have ZERO imports beyond
     dataclasses/enum. Genuine production code reuse, not a
     reimplementation.
```

New file: `docs/research/r0082_livekit_webrtc_audio_poc/
r0082e_silero_offline_analysis.py`. No new package installed anywhere
— stage 1 uses plain system `python3`'s existing `scipy`; stage 2 uses
NeXa's own `.venv`'s existing `onnxruntime`+`loudness`. `py_compile`,
`ruff`, `git diff --check` all clean.

**A safety check, not just a claim**: `load_interruption_state_machine_class()`
explicitly asserts, after loading, that no `nexa.voice`/`pipecat`/
`loguru` module appears in `sys.modules` (excluding the standalone
loaded module itself) — verified this round; the script would raise
`SystemExit` rather than silently proceed if isolation were ever broken.
No audio device is opened anywhere in this script — `onnxruntime.
InferenceSession` and `loudness.integrated_loudness` are both pure
numerical calls with no hardware access, and `InterruptionStateMachine`
itself has no I/O of any kind (documented in its own module docstring:
"No audio, no Pipecat, no I/O, no timers of its own").

**A real bug found and fixed while building this**: the ONNX model's
raw output shape is `(1, 1)`, not scalar — pipecat's own
`voice_confidence()` returns `out[0]` (shape `(1,)`) un-converted;
calling `float()` directly on that 1-element array raised `TypeError`
under the installed numpy version. Fixed by indexing one level further
(`out[0][0]`), confirmed via direct introspection of the ONNX session's
real output shape before assuming the fix, not guessed.

## 67. File verification (re-confirmed)

All four canonical mic WAV SHA256 hashes re-verified this round,
matching R0082-D's own recorded values exactly (no drift):
`A1_OFF=dd4bbfd9...b75196`, `B1_ON=fef4e9bd...ed47d04`,
`B2_ON=dc79b724...9f5f9799`, `A2_OFF=8596fbda...4a0eb39`. The extra,
unlisted `20260918T145123Z` run remains excluded and untouched, not
resampled, not analyzed — flagged again for transparency.

## 68. Per-run results

```
     run  aec  max_prob  frames>thr(0.7)  starts  confirmed
  A1_OFF  OFF    0.4932                0       0          0
   B1_ON   ON    0.3204                0       0          0
   B2_ON   ON    0.7684                3       0          0
  A2_OFF  OFF    0.2533                0       0          0
```

Full per-run statistics:

```
         max_prob  mean_prob   p95    p99   frames_above_thr / total
A1_OFF     0.4932     0.0132  0.0459  0.3065        0 / 787
B1_ON      0.3204     0.0088  0.0294  0.1551        0 / 787
B2_ON      0.7684     0.0120  0.0252  0.2673        3 / 787
A2_OFF     0.2533     0.0089  0.0351  0.1589        0 / 787
```

**Level 1 (raw probability)**: only B2_ON ever exceeds the production
confidence threshold (0.7), and only barely (max 0.7684), for 3 out of
787 frames (0.38%). All four runs' mean/p95 probabilities sit far below
threshold. **Level 2 (production VAD state machine)**: zero
`VADUserStartedSpeakingFrame`-equivalent events in all four runs — the
state machine never left `QUIET` in any recording. **Level 3
(production-relevant interruption event)**: zero
`InterruptionStateMachine` `INTERRUPT_CONFIRMED` events, zero rejected
candidates (there were no candidates to reject), in all four runs.

### 68a. Why B2's 3 above-threshold frames did not trigger anything

Inspected directly from the timeline CSV (`B2_ON_timeline.csv`):

```
timestamp_s  region  silero_prob  vad_state  speech_start_event
14.048       speech  0.74689      QUIET      0
14.080       speech  0.76840      QUIET      0
14.112       speech  0.74646      QUIET      0
14.144       speech  0.63886      QUIET      0
```

`vad_state` stays `QUIET` through all three above-threshold frames —
confirming the volume gate (`smoothed_volume >= 0.6`) was NOT
simultaneously satisfied on at least the first of them, since the state
machine enters `STARTING` on the VERY FIRST frame where BOTH conditions
hold. Independently, even ignoring the volume gate entirely, 3
consecutive above-threshold frames (96ms) falls short of the 6-frame
(192ms) `VAD_START_FRAMES` debounce requirement — a second, independent
reason this did not (and structurally could not have) produced a
`VADUserStartedSpeakingFrame`.

## 69. Special region inspection

**t≈6-7s (R0082-D's flagged shared transient region)**, inspected as
natural-file-time t=6.5-8.5s (a generous window covering all four
runs' own per-run lag estimates against the stimulus-relative t=6-7s
region, per §59c's low-confidence lag caveat):

```
A1_OFF: max_prob_in_window=0.4286  frames_above_threshold=0
B1_ON:  max_prob_in_window=0.3204  frames_above_threshold=0
B2_ON:  max_prob_in_window=0.2823  frames_above_threshold=0
A2_OFF: max_prob_in_window=0.1795  frames_above_threshold=0
```

**The large RMS energy transient R0082-D found shared across 3 of 4
runs at this timestamp did NOT produce elevated Silero speech
probability in any of the four runs.** This directly answers R0082-D's
own §61 "not proven until Silero is run" question for this specific
event: the energy peak is **not** speech-like to Silero, in any of the
four conditions.

**A1's anomalous pre-roll transient (t=0.0-1.0s)**, the large
startup-transient RMS spike R0082-D found (§59e):

```
A1_OFF pre-roll: max_prob=0.3364  frames_above_threshold=0 / 32
```

Also well below threshold. The strongest RMS peaks elsewhere in B1/B2/A2
(already fully covered by the per-run Level-1 statistics above, which
found the global max across the ENTIRE recording, not just the flagged
regions) never exceed the volume+confidence AND-gate for the sustained
duration needed to register as a state-machine transition anywhere in
any of the four files.

## 70. Comparison: OFF vs. ON

With zero false speech starts in all four conditions, there is no false
positive COUNT to compare between OFF and ON — the comparison is
necessarily null at the confirmed-event level. At the raw-probability
level (Level 1), no clean OFF/ON separation is visible either: max
probability across the four runs is A1_OFF=0.493, B1_ON=0.320,
B2_ON=0.768, A2_OFF=0.253 — the single highest max-probability run is
B2 (ON), not either OFF run. This is consistent with, not contradictory
to, R0082-D's own §59f/§59i finding of no consistent OFF/ON pattern in
RMS or band power — the same absence of a clean AEC-driven signal shows
up here too, at a level (Silero probability) that matters more directly
for the production question.

## 71. Updated evidence classifications

```
1.  Production Silero configuration was identified exactly.        CONFIRMED
2.  Offline preprocessing matches production numerically.          SUPPORTED
     (Level 1/2 algorithms + loudness/onnxruntime bindings are
     verbatim/genuine production code; the 48kHz->16kHz resample
     step itself is a necessary adaptation NOT present in production's
     own already-16kHz-native path, documented explicitly, §66)
3.  A1 produces raw Silero speech-like probabilities.               NOT PROVEN
     (max 0.493, below the 0.7 production threshold throughout)
4.  B1 produces raw Silero speech-like probabilities.                NOT PROVEN
     (max 0.320)
5.  B2 produces raw Silero speech-like probabilities.               HYPOTHESIS
     (briefly exceeds 0.7 for 3/787 frames -- above threshold in the
     narrowest sense, but never satisfies the debounce+volume gates)
6.  A2 produces raw Silero speech-like probabilities.                NOT PROVEN
     (max 0.253, the lowest of all four)
7.  A1 would produce a production-equivalent false speech start.     REFUTED
8.  B1 would produce a production-equivalent false speech start.     REFUTED
9.  B2 would produce a production-equivalent false speech start.     REFUTED
     (§68a -- fails both the volume gate and the 6-frame debounce)
10. A2 would produce a production-equivalent false speech start.     REFUTED
11. AEC ON reduces Silero false positives.                          NOT PROVEN
     (zero false positives in EITHER condition -- nothing to reduce)
12. AEC OFF produces more Silero false positives.                    REFUTED
     (the single highest raw probability was an ON run, B2)
13. The t~6-7s residual is speech-like to Silero.                    REFUTED
     (§69 -- max probability in that window, all four runs, is well
     below threshold)
14. Existing captured self-echo is sufficient to reproduce the
    historical self-interruption mechanism.                          NOT PROVEN
     (this round found no false-trigger case AT ALL to examine
     further -- see §72 CASE 1)
15. Current evidence proves NeXa self-interruption is fixed.         NOT PROVEN
     (CASE 1 outcome, §72 -- absence of a false trigger on THIS
     specific offline non-speech-user scenario, via THIS specific
     PlatformAudio/WebRTC audio path, does not by itself prove the
     original production mechanism -- which used a DIFFERENT
     architecture, the XVF3800/AecReferenceFeeder path -- is fixed)
```

## 72. Decision: CASE 1 — offline false-trigger NOT OBSERVED

Per the decision logic in the brief: **all four WAVs produced 0
production-equivalent false speech starts.** Classification:

```
offline Silero false-trigger on these captures = NOT OBSERVED
```

**This is explicitly NOT a claim that the complete self-interruption
bug is fixed.** Several real differences remain between this offline
capture-based test and NeXa's actual live production failure mode:

- **Different hardware/software AEC path entirely.** This whole R0082
  investigation exists because the ORIGINAL self-interruption bug was
  observed on the CURRENT production path (XVF3800 hardware AEC +
  manual `AecReferenceFeeder`, R0081). R0082-D's captures come from the
  PlatformAudio/WebRTC/LiveKit path being evaluated as a POTENTIAL
  replacement — a genuinely different audio pipeline. A clean result
  here says nothing directly about whether the CURRENT production
  XVF3800 path's own residual would also pass this same Silero test —
  that was never tested in R0082 at all (R0081 measured raw residual
  energy on that path, never ran Silero against it).
- **Different, and louder/more idealized, stimulus.** The R0082-D
  speech stimulus was Piper-synthesized, not a real recorded NeXa TTS
  reply on the live pipeline's own audio chain, and played at a
  single fixed volume/position — real production speech content,
  timing, and prosody vary.
- **Live timing/state differences not modeled.** `AecReferenceHealth.
  barge_in_safe` (§65's stated scope boundary), real `ConversationSession`
  behavior, real STT/LLM concurrency load on the same CPU, and the real
  Pipecat pipeline's own frame-queue/backpressure behavior under live
  conditions are all absent from this offline batch analysis.
- **Provider/event-interaction differences.** A live session has
  Gemini/LLM generation, TTS streaming, and real-time frame delivery
  all interleaved — none of that concurrency exists in this offline,
  single-threaded batch script.

**Recommended next step (per the brief's CASE 1 branch): the smallest
live integration test** — NOT a further offline analysis, and NOT yet a
production migration decision. Conceptually: connect the REAL
`SileroVADAnalyzer`+`VADProcessor`+`BargeInController`+
`InterruptionStateMachine` stack (the actual production classes, live,
not this round's faithful-but-offline reproduction) to the
PlatformAudio/LiveKit audio path in a minimal live session — no Gemini,
no full `ConversationSession` — and confirm live, in real time, that
the same clean (zero-false-trigger) result holds under actual live
timing/concurrency, before considering any production architecture
decision. **This step is NOT designed or executed this round** — it is
the recommended direction only.

## 73. Limitations

- Offline, batch, single-threaded reproduction — not a live pipeline
  test (§72's own stated limitation, central to why CASE 1's result is
  not treated as proof the bug is fixed).
- The 48kHz→16kHz resample step is a necessary adaptation specific to
  R0082-D's LiveKit-captured evidence, not itself part of production's
  own (already 16kHz-native) audio chain — a defensible, standard,
  anti-aliased polyphase resample, but not literally byte-identical to
  what production's ALSA `plug:` layer would have produced from the
  SAME acoustic event captured directly by the reSpeaker at its native
  rate.
- Only 4 runs, 2 per AEC condition — the OFF/ON comparison in §70 is
  observational, not statistically powered.
- `AecReferenceHealth.barge_in_safe` and all downstream
  `ConversationSession`/STT/LLM/TTS behavior are explicitly out of
  scope (§65), by design, not by oversight.
- The Piper-synthesized stimulus is not identical to real production
  TTS output on the live pipeline's own voice.
- No production code, NeXa Core, Gemini, Pipecat, Silero PRODUCTION
  integration, `BargeInController`, `AecReferenceFeeder`, XVF3800
  settings, PipeWire defaults, system mixer, or LiveKit server
  configuration were touched this round. No hardware test was run. No
  DSP writes were made.

# R0082-F — minimal LIVE production-equivalent VAD self-echo test (preparation only)

## 74. Purpose and handoff from R0082-E

R0082-E answered, OFFLINE, on 4 already-recorded WAV captures: does the
production-equivalent Silero VAD chain false-trigger on NeXa's own
self-echo residual? Result: 0 false triggers in all four conditions —
but explicitly NOT proof the LIVE path is safe (no live timing, frame
delivery, concurrency, or real-time audio behavior was tested). R0082-F
tests the SAME question LIVE: while NeXa-like speech actually plays
through the real speaker and the human user stays silent, does the
LIVE `PlatformAudio` → Silero VAD path generate any false
`UserStartedSpeaking`-equivalent events? **No hardware test was run
this round — this section is preparation and offline/synthetic
validation only.**

## 75. Architecture decision — package installs into the isolated probe venv

The brief's own recommended topology bundles `PlatformAudio` (mic
capture) and the Silero VAD diagnostic chain into ONE process
("PROCESS A"), which requires `onnxruntime` + `loudness` + `scipy`
(streaming resampling) inside the SAME isolated probe venv that already
holds `livekit`/`livekit-api`/`sounddevice`/etc. from R0082-A–D. The
brief also states explicitly: *"No package upgrades unless absolutely
necessary — and if something required is missing, STOP and report
instead of installing it."* These two instructions were in real
tension for this specific case — **this was put to the operator
directly rather than decided unilaterally**, who chose: install
`onnxruntime==1.24.4` and `loudness==0.2.0` (exact versions matching
NeXa's own `.venv`, confirmed real aarch64 wheels) into the isolated
probe venv. `scipy` (needed for the streaming resampler) was installed
the same way. **No production-adjacent environment** (NeXa's own
`.venv`, the Piper venv, system Python) was touched — only the
already-established, throwaway, R0082-specific probe venv.

## 76. Re-audit of production VAD configuration — CONFIRMED, zero drift

Per instruction, every value R0082-E audited was re-checked directly
against current source before building anything:

```
pipecat-ai version:              1.8.1                              (unchanged)
DEFAULT_VAD_PARAMS:               VADParams(stop_secs=1.0)           (unchanged, runtime.py:87)
LocalAudioConfig.sample_rate:     16000                              (unchanged, config.py:63-64)
DEFAULT_CONFIRM_HOLD_SECS:        0.3                                (unchanged, interruption.py:44)
Pipecat VAD_CONFIDENCE:           0.7                                (unchanged, vad_analyzer.py:25)
Pipecat VAD_START_SECS:           0.2                                (unchanged, vad_analyzer.py:26)
Pipecat VAD_MIN_VOLUME:           0.6                                (unchanged, vad_analyzer.py:28)
silero_vad.onnx sha256:           597d30b3ec076608...bbfe82004       (identical byte-for-byte)
bargein_wiring.py construction:   BargeInController(aec_health=health, ...)  (unchanged — aec_health
                                   remains a mandatory, non-optional constructor argument)
```

**No discrepancy found — nothing has changed since R0082-E.** Per
instruction, since everything matched exactly, this round proceeds
without stopping.

## 77. `BargeInController` audit — read-only, NOT modified, NOT used this round

`src/nexa/voice/bargein.py` (521 lines) was read in full — no
execution, no modification. Exactly ONE piece of its behavior actually
depends on `AecReferenceHealth`:

```python
def _handle_speech_started(self) -> None:
    if not self._sm.response_in_flight:
        return
    if not self._aec.barge_in_safe:        # <- the ONLY gating dependency
        ...  # speech during a reply is silently ignored
        return
    ev = self._sm.speech_started(self._now())
```

Plus a mandatory `aec_health: AecReferenceHealth` constructor parameter
(no default — the class cannot be built without one, confirmed
`bargein.py:112` and its real construction site,
`bargein_wiring.py:145-150`) and two telemetry-only fields
(`aec_reference_active`/`aec_reference_failure_count`, reporting, not
gating). **Everything else is architecture-neutral**:
`InterruptionStateMachine` ownership, confirm-hold scheduling
(`_schedule_confirm`/`_confirm_after_hold`), the M2.5B.1/.3
settle-phase/capture-id lifecycle (`_arm_settle`/`_settle_after`/
`_capture_deadline`/`_end_capture_phase`), `notify_response_dispatched`/
`notify_response_finished`/`notify_interruption_complete`, and Pipecat
frame routing (`process_frame`) reference no XVF3800/hardware-specific
state at all.

**Consequence, per instruction**: `barge_in_safe` is a precondition
SPECIFIC to the CURRENT production XVF3800/`AecReferenceFeeder`
hardware AEC path (R0028) — `PlatformAudio`'s WebRTC AEC has no
equivalent discrete "far-end reference confirmed active" signal exposed
to Python. Using `BargeInController` here would require a stub
`AecReferenceHealth`-like object with `barge_in_safe` hardcoded `True`
— presenting a fake precondition as if it meant something on this
architecture, a real confound. **This round does NOT insert
`BargeInController`.** The canonical live detection chain is
PlatformAudio mic → Silero → VADAnalyzer state → the REAL
`InterruptionStateMachine` directly → diagnostic logging only.
`BargeInController` has **NOT** been validated on `PlatformAudio`, and
this round makes no such claim.

## 78. AEC state selected: **ON**

Per instruction, this round does NOT re-sweep AEC OFF/ON — R0082-D/E
already found no reliable, consistent AEC-driven difference in either
direction (§59f/§70 of this report). The state chosen for the live
test is **AEC ON**, because:

1. **It is the only configuration that would ever actually be
   deployed.** A real NeXa deployment on this architecture would
   obviously run with the WebRTC AEC enabled — testing OFF would
   validate a configuration that will never ship.
2. R0082-D/E's own finding (no reliable benefit either direction) means
   choosing ON does not sacrifice meaningful evidentiary value relative
   to OFF for THIS test's purpose (VAD behavior, not AEC attenuation).
3. Testing OFF would inject an artificially elevated echo condition
   corresponding to no real deployment scenario — itself a confound in
   the opposite direction from what a "least-confounded" choice should
   avoid.

`--aec` defaults to `on` in the harness; the operator command below
does not override it.

## 79. Live architecture

```
PROCESS A (--role hardware)              PROCESS B (--role speech)
  rtc.PlatformAudio()                      rtc.AudioSource (synthetic)
  real reSpeaker mic, real UAC playout      publishes the frozen speech WAV
  publishes its own mic track                      |
  subscribes to B's speech track  <----------------+ (triggers automatic
  (AEC far-end reference)                            playout)
        |
        | self-reads its OWN mic track via rtc.AudioStream
        v
  StreamingResampler (48kHz -> 16kHz, stateful)
        v
  Level 1: SileroOnnxModel  ->  Level 2: VADAnalyzer state  ->
  Level 3: REAL InterruptionStateMachine  ->  CSV + milestone log
```

Two genuinely separate OS processes, no shared `Room`/`PlatformAudio`,
no Python `multiprocessing`/`fork()` — R0082-C's own `RaceDetected()`
fix, unchanged.

**A new architectural finding this round**: reading a JUST-PUBLISHED
`LocalAudioTrack` via `rtc.AudioStream` works with **no room
round-trip at all** — confirmed with a purely synthetic, no-hardware,
no-room test (a `LocalAudioTrack` backed by a plain `rtc.AudioSource`,
fed 15 frames, all 15 correctly read back via a local `AudioStream`).
This lets `PROCESS A` self-analyze its own mic audio directly, instead
of needing a second participant to subscribe to and relay it back
(R0082-D's own pattern). **Not yet empirically confirmed specifically
for a `PlatformAudioSource`-backed track** (requires real hardware,
deferred to the operator's run) — the documented fallback, if the real
hardware run finds the self-read stream never yields frames, is
R0082-D's own remote-subscription pattern (not built this round, to
keep the harness minimal).

## 80. Real-time 48kHz → 16kHz resampling — stateful streaming design

`rtc.AudioStream`'s ACTUAL delivered format was verified this round via
a real `AudioFrameEvent` (not assumed): **`sample_rate=48000`,
`samples_per_channel=480`** (10ms frames) — matching every prior R0082
script's own convention.

`StreamingResampler` applies ONE FIR anti-aliasing lowpass
(`scipy.signal.firwin`, cutoff at 90% of the 8kHz output Nyquist) via
`scipy.signal.lfilter`, carrying its `zi`/`zf` filter state across
EVERY `push()` call, plus a running total-samples-seen counter that
fixes the exact 3:1 decimation phase across arbitrary chunk-length
boundaries. This is a genuinely stateful design — **not** independent
per-chunk resampling, which would reset the FIR filter's memory (and
distort the signal) at every 480-sample chunk boundary. No gain
normalization, no AGC, no denoising added anywhere in this path.

## 81. Synchronization

Unchanged, event-based pattern from R0082-C/D/E: both roles wait
(bounded, `SUBSCRIBE_TIMEOUT_S=30s`) for the OTHER participant's
`track_subscribed` event before proceeding — no fixed wall-clock guess,
no assumption about launch order. `PRE_ROLL_S`/`TAIL_S` are both set to
**2.0s** (up from R0082-D's 1.0s, per this round's explicit "at least
2s" requirement). The hardware role's total hold is derived from named
constants (`SETTLE_S + PRE_ROLL_S + 23.181...s (speech duration) +
TAIL_S + CLEANUP_GRACE_S`), printed in full at runtime, matching
R0082-C/D's own established discipline.

## 82. Telemetry schema

Per-frame CSV (`r0082f_aec{on,off}_<run_id>_timeline.csv`), one row per
32ms Silero frame:

```
timestamp_monotonic, audio_relative_timestamp_s, silero_prob,
confidence_threshold, smoothed_volume, volume_threshold, vad_state,
candidate_start, vad_user_started_speaking_equivalent,
vad_user_stopped_speaking_equivalent, interruption_state,
interrupt_confirmed
```

Milestone log (printed, not a separate file): hardware connected, mic
published, speech track subscribed, VAD ready, PRE_ROLL start, PLAYBACK
start, PLAYBACK end, TAIL end, disconnect — all present in
`run_hardware_role`/`run_speech_role`. Raw evidence written at the end
of the hardware role: `..._mic_48k.wav` (unmodified live capture) and
`..._mic_16k.wav` (the exact Silero-input PCM after streaming
resampling), both with SHA256 printed, neither normalized.

## 83. Genuine production code reuse vs. research glue

- **Genuine production code, unmodified, loaded directly from source**:
  `nexa.voice.interruption.InterruptionStateMachine`, via
  `importlib.util.spec_from_file_location`, bypassing
  `nexa/voice/__init__.py`'s own `pipecat`/`loguru` import chain
  entirely — isolation re-verified this round (`load_interruption_
  state_machine_class()` asserts zero `nexa.voice`/`pipecat`/`loguru`
  modules in `sys.modules` after loading, or raises).
- **Verbatim algorithmic reproduction** (not imported directly, to
  avoid pulling `loguru`/`pipecat`'s own package-init side effects into
  a process that ALSO needs real-time LiveKit hardware access):
  `SileroOnnxModel` (same bundled ONNX file, same sha256), the
  `VADAnalyzer` 4-state hysteresis machine and its exact constants, and
  the volume-gate math (`exp_smoothing`, `normalize_value`, the rolling
  400ms window) — reusing the REAL `loudness.integrated_loudness()`
  binding for the actual BS.1770 computation.
- **Research glue, new this round**: the split-process room
  orchestration (extends R0082-C/D's proven pattern), `StreamingResampler`,
  and the CSV/milestone telemetry writer.

New file: `docs/research/r0082_livekit_webrtc_audio_poc/
r0082f_live_vad_self_echo_poc.py`. No production files touched.

## 84. Offline/synthetic validation — ALL REQUIRED CHECKS PASS

```
import                                          PASS
--help                                          PASS
py_compile                                      PASS
ruff                                            1 line-length error found and fixed -- 0 on re-check
git diff --check                                PASS
dependency isolation (no nexa.voice/pipecat/
  loguru/google.genai in sys.modules)            PASS
```

**Positive control** (mandatory, per instruction) — the frozen speech
WAV's own content (its ORIGINAL, un-attenuated PCM — not a mic
capture) fed through the SAME `StreamingResampler`+`LiveVadChain`
pipeline the live harness uses, in realistic 480-sample (10ms) chunks:

```
n_frames=724  max_prob=0.9966
VADUserStartedSpeaking-equivalent at t=0.672s
INTERRUPT_CONFIRMED at t=0.992s
```

**The full pipeline correctly detects and confirms real speech when it
is genuinely present** — a future "0 events" result on the real live
test cannot be dismissed as broken instrumentation.

**Negative control** (mandatory) — 26 seconds of pure digital silence
through the identical pipeline:

```
n_frames=812  max_prob=0.0238
started_events=[]  confirmed_events=[]
```

**Zero events on pure silence, as required.**

**End-to-end async mechanics validation** — a synthetic dry run
(throwaway harness, deleted after use) exercising the REAL room
connect/publish/subscribe/self-read/streaming-resample/VAD/CSV/cleanup
orchestration (`run_hardware_role`'s own async structure), using a
synthetic `AudioSource` standing in for `PlatformAudio` and feeding it
the frozen speech WAV:

```
first live frame: sample_rate=48000 samples_per_channel=480  (confirmed
                                                                live, not assumed)
n_frames=855  max_prob=0.9964
started_events=[1.504]  confirmed_events=[1.824]
```

Token generation, room connect/disconnect, publish/subscribe, the
self-read-without-round-trip mechanism, 512-sample Silero framing, the
VAD state machine, `InterruptionStateMachine`, and CSV generation all
confirmed working together, live, asynchronously — only real
`PlatformAudio` hardware itself remains untested (by design, deferred
to the operator).

**A minor, honestly-reported discrepancy, not chased further**: a
raw-waveform cross-correlation comparison between `StreamingResampler`'s
output and R0082-D/E's own batch `resample_poly` output (on the same
source WAV) showed matching RMS levels (5484.9 vs. 5462.9, ~0.4% apart
— no gross scaling error) but a weak/negative correlation coefficient
after a naive single-lag alignment attempt — most likely reflecting
real phase-response differences between the two different FIR filter
designs rather than a functional defect, especially since the SAME
streaming-resampled output was independently and directly confirmed
correct by the positive control above (Silero classified it as speech
with 0.997 confidence and the full pipeline correctly confirmed it).
Not investigated further this round — the functional (Silero
classification) validation is the metric that actually matters here,
and it passed unambiguously.

No audio device is opened anywhere in the offline/synthetic validation
above — `onnxruntime.InferenceSession`, `loudness.integrated_loudness`,
and `scipy.signal.lfilter` are pure numerical calls; the synthetic
`AudioSource`/local-track self-read mechanism touches no hardware.

## 85. Limitations

- **No real hardware test was run.** Every result in §84 is offline or
  synthetic. `PlatformAudio()` itself was never invoked by this session.
- Self-read on a `PlatformAudioSource`-backed track is unconfirmed —
  stated explicitly, with a documented fallback, §79/module docstring.
- AEC fixed at ON, not re-swept (§78) — a deliberate scope decision, not
  an oversight.
- `BargeInController`'s own `AecReferenceHealth` gate remains untested
  by design (§77) — this round validates only the architecture-neutral
  detection chain up to `InterruptionStateMachine`.
- Deliberate human barge-in is explicitly NOT tested this round (a
  later, separate stage per the brief).
- The streaming-vs-batch resampler cross-check (§84) was inconclusive
  by raw correlation but functionally validated by Silero classification
  — noted as a real, unresolved minor discrepancy in methodology, not
  hidden.
- No production code, NeXa Core, Gemini, Pipecat, `ConversationSession`,
  Memory, Identity, Context, `AecReferenceFeeder`, XVF3800 DSP, PipeWire
  defaults, ALSA system config, system mixer, or speaker volume were
  touched this round. No hardware test was run. No DSP writes, no sudo
  configuration changes, no production package upgrades.

## 86. Exact operator procedure — SILENT USER ONLY, AEC ON, first live run

**Prerequisite** (separate terminal, once):

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**The two commands — start hardware first, then speech, within the 30s
subscribe timeout:**

```bash
PROBE=/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3
POC=docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_vad_self_echo_poc.py
ROOM=r0082f_silent_user_$(date +%s)

# terminal A — hardware/live VAD participant (real PlatformAudio, AEC ON)
$PROBE $POC --role hardware --aec on --room-name "$ROOM"

# terminal B — deterministic speech participant
$PROBE $POC --role speech --room-name "$ROOM"
```

**The operator must remain completely silent throughout** — no
deliberate speech, no barge-in attempt. This is the SILENT-USER-only
run per the brief; a separate later round will test deliberate
interruption. **This has intentionally NOT been run by this session.**

### PASS / FAIL (restated from the brief, for the operator's reference)

**PASS**: `VADUserStartedSpeaking`-equivalent events = 0 **AND**
`INTERRUPT_CONFIRMED` events = 0, across pre-roll + full speech playback
+ post-roll. Raw probability spikes above 0.7 are allowed and do NOT
automatically fail if production debounce/volume/state logic correctly
rejects them (exactly as §68a of R0082-E's own report demonstrated).

**FAIL**: any accepted `VADUserStartedSpeaking`-equivalent event during
operator silence, or any `INTERRUPT_CONFIRMED` event — record the exact
timestamp and cross-reference the corresponding audio segment in the
saved WAV evidence.

## 87. Pre-hardware gap closure — probe venv audit + resampler validation

Two evidence gaps were closed before any hardware run: (1) auditing the
isolated probe venv after the `onnxruntime`/`loudness`/`scipy` installs,
and (2) independently validating `StreamingResampler` against a
mathematically identical whole-array reference, since the prior round's
positive-control PASS proved the pipeline recognizes strong speech but
did NOT prove the resampler preserves WEAK residuals faithfully. **No
hardware, no speaker playback, no `PlatformAudio` initialization, no
production changes this round.**

### 87a. Probe venv audit

```bash
pip check   # -> "No broken requirements found."
pip freeze
```

```
livekit==1.1.19          (unchanged — matches R0082-C/D/E's own confirmed version exactly)
livekit-api==1.2.1        (unchanged — matches this report's own earlier table exactly)
livekit-protocol==1.1.27
loudness==0.2.0           (operator-approved exact version, unchanged since install)
numpy==2.5.3
onnxruntime==1.24.4       (operator-approved exact version, unchanged since install)
PyJWT==2.14.0              (satisfies the documented <3,>=2.12.0 constraint)
protobuf==7.36.2
scipy==1.18.1
sounddevice==0.5.6         (unchanged — matches R0082-B's own confirmed version exactly)
tenacity==9.1.4            (unchanged — matches R0082-B/C's own confirmed version exactly)
```

**Cross-referenced against the ACTUAL install transaction logs from
this investigation** (not re-derived from memory): the `scipy` install
showed `Requirement already satisfied: numpy<2.8,>=2.0.0 ... (2.5.3)` —
numpy was untouched. The `onnxruntime==1.24.4 loudness==0.2.0` install
showed `Requirement already satisfied: protobuf ... (7.36.2)` and its
own `Installing collected packages:` line listed ONLY
`mpmath, flatbuffers, sympy, packaging, loudness, onnxruntime` — none
of `livekit`, `livekit-api`, `sounddevice`, `numpy`, `protobuf`,
`PyJWT`, or `tenacity` appeared in either transaction's installed-package
list, meaning none were upgraded, downgraded, or removed.

**Explicit answer**: installing `scipy`/`onnxruntime`/`loudness` did
**NOT** upgrade, downgrade, remove, or conflict with any package used
by the existing R0082 LiveKit harness. `pip check` confirms zero broken
requirements. No STOP condition was triggered.

### 87b. Resampler validation — new file, new controlled tests

New file: `docs/research/r0082_livekit_webrtc_audio_poc/
r0082f_resampler_validation.py`. Imports `StreamingResampler`/
`LiveVadChain` directly from `r0082f_live_vad_self_echo_poc.py` (not a
reimplementation). No hardware, no room, no `PlatformAudio` anywhere in
this script.

**Whole-array reference**: the SAME FIR coefficients
(`StreamingResampler`'s own `self.b`, obtained from a real instance,
never re-derived), the SAME zero-initial-state assumption, and the SAME
3:1 decimation-from-index-0 convention, applied to the ENTIRE input in
ONE `scipy.signal.lfilter` call — this is not a different resampling
algorithm, it is the identical math applied without chunking, exactly
per instruction.

**Result — chunking does not change the numerical result (proven, not
assumed)**:

```
A) whole-array reference vs B) 480-sample streaming (frozen speech WAV, 1,112,708 input samples):
  output samples: 370903 vs 370903  (length_diff=0)
  pearson_at_lag0=1.00000000  best_lag=0
  max_abs_diff=0.000000  rms_diff=0.000000
  first_differing_sample_index=None

A) whole-array reference vs C) IRREGULAR-chunk streaming (chunk sizes
   479,481,137,997,211,503,1,2999,50,480,17,4001,333, cycled):
  output samples: 370903 vs 370903  (length_diff=0)
  pearson_at_lag0=1.00000000  best_lag=0
  max_abs_diff=0.000000  rms_diff=0.000000
  first_differing_sample_index=None

B) 480-chunk vs C) irregular-chunk:
  max_abs_diff=0.000000  (transitively consistent)
```

**Bit-for-bit identical in every case, including deliberately
non-multiple-of-3, deliberately irregular chunk boundaries (1-sample
and 4001-sample chunks included).** This directly proves the running
`_total_in_samples` decimation-phase counter is correct — chunking,
regular or irregular, does not change the resampler's numerical output
at all. §84's earlier "weak/negative correlation" finding against
R0082-D/E's own `resample_poly` is now conclusively explained: it was
NOT a defect in `StreamingResampler` (ruled out here, against its own
mathematically identical reference) — it reflects real, expected phase/
group-delay differences between two genuinely DIFFERENT FIR filter
designs (this script's own 63-tap `firwin` filter vs. `resample_poly`'s
internal filter), exactly the hypothesis already stated, now confirmed
correct rather than merely assumed. No redesign was needed or performed
— validation did not reveal a defect.

### 87c. Impulse test — filter state and decimation phase, made obvious

A single full-scale impulse (100ms, 4800 samples @ 48kHz) through all
three methods:

```
reference:                  peak_index=10  peak_value=8416  (peak_time=0.625ms)
480-chunk streaming:         peak_index=10  peak_value=8416  (peak_time=0.625ms)
irregular-chunk streaming:   peak_index=10  peak_value=8416  (peak_time=0.625ms)

reference vs 480-chunk:      max_abs_diff=0.000000
reference vs irregular-chunk: max_abs_diff=0.000000
```

Identical group delay (0.625ms — a real, small, expected latency from
the 63-tap linear-phase FIR filter, negligible relative to the VAD's
own ~200-1000ms timing budget) and bit-for-bit identical outputs across
all three methods — no state or phase defect exists at chunk
boundaries, including at the very first sample of the very first chunk.

### 87d. Frequency / alias test

10 seconds of synthetic 48kHz tones (amplitude 8000, comparable to
real speech-like levels) at each frequency, through the 480-chunk
streaming resampler, measured via the same Goertzel method used
throughout R0082-B–E:

```
  500Hz: in=3999.67  out=3996.87  ratio=0.9993
 1000Hz: in=3999.74  out=3996.35  ratio=0.9992
 3000Hz: in=3999.80  out=3998.11  ratio=0.9996
 6000Hz: in=3999.70  out=3970.98  ratio=0.9928
 7500Hz: in=3999.73  out=1194.05  ratio=0.2985   <- expected: above the 7200Hz
                                                      (0.9x output Nyquist) filter cutoff
 9000Hz (>= output Nyquist): alias would land at 7000Hz -- alias_mag=4.59 vs in=3999.80
                              -> suppression_ratio=871.4x (~-58.8dB)
12000Hz (>= output Nyquist): alias would land at 4000Hz -- alias_mag=3.96 vs in=4000.00
                              -> suppression_ratio=1010.9x (~-60.1dB)

stationary multitone (500+1000+3000+6000+7500Hz, mixed): per-frequency
  ratios (0.9993/0.9990/0.9996/0.9928/0.2986) closely match the
  single-tone results above -- no unexpected intermodulation distortion.
```

**All three required properties confirmed**: speech-band frequencies up
to 6kHz survive with >99% amplitude, with no sign inversion anywhere
(every in-band ratio is positive and close to 1.0); frequencies at or
above the 8kHz output Nyquist are suppressed by ~59-60dB before
decimation — no alias reaches meaningful amplitude in the 0-8kHz output
band (alias magnitudes of 4-5, against an input magnitude of ~4000, are
effectively at the noise floor). 7500Hz's own real, expected roll-off
(cutoff sits at 7200Hz, 90% of the output Nyquist, per
`StreamingResampler.__init__`'s own documented margin) is noted
explicitly, not glossed over — it is within the filter's own designed
transition band, not a defect.

### 87e. Controls repeated with the UNCHANGED resampler — both PASS

```
POSITIVE CONTROL: n_frames=724  max_prob=0.9966
  started_events=[0.672]  confirmed_events=[0.992]   -> PASS

NEGATIVE CONTROL: n_frames=812  max_prob=0.0238
  started_events=[]  confirmed_events=[]              -> PASS
```

Identical to §84's earlier results (expected — the resampler was not
modified, only independently validated). No VAD threshold, hysteresis,
volume gate, or confirm-hold value was altered anywhere in this round
(`confidence=0.7`, `start_secs=0.2`, `stop_secs=1.0`, `min_volume=0.6`,
`confirm_hold_secs=0.3` — all unchanged, re-confirmed against source in
§76, not re-audited again this round since nothing in the codebase
changed).

### 87f. Final validation sweep

```
pip check                                        PASS
py_compile (all R0082 scripts, incl. new file)    PASS
ruff (whole r0082_livekit_webrtc_audio_poc/ dir)  PASS (0 errors after fixing 5 line-length/
                                                   unused-import errors in the new file)
git diff --check                                  PASS
```

## 88. FINAL GATE — R0082-F READY FOR FIRST REAL HARDWARE RUN

```
pip check                                        PASS
no unexpected package/version conflict            PASS
whole-array vs 480-chunk resampler                PASS (bit-for-bit identical)
whole-array vs irregular-chunk resampler          PASS (bit-for-bit identical)
impulse/phase test                                 PASS (bit-for-bit identical)
basic frequency/alias test                         PASS
positive VAD control                               PASS
negative silence control                           PASS
py_compile                                         PASS
ruff                                               PASS
git diff --check                                   PASS
```

**All eleven gate conditions pass. Verdict: R0082-F READY FOR FIRST
REAL HARDWARE RUN.** The operator command from §86 is unchanged and
reproduced there — this round changed no code in
`r0082f_live_vad_self_echo_poc.py` itself, only added independent
validation evidence in a new file. **This session did not execute the
hardware run.**

## 89. R0082-F hardware attempt #1 — INVALID (zero VAD input frames)

```
room = r0082f_silent_user_...
AEC = ON
result = INVALID
reason = zero VAD input frames (self-read on PlatformAudioSource track
         yielded nothing)
```

The operator ran §86's command. At the LiveKit transport level,
everything worked: `PlatformAudio` connected, published the real
reSpeaker mic, subscribed to the speech track, the LiveKit server
showed the hardware mic genuinely flowing through the room with no
packet loss, the speech participant completed its full
PRE_ROLL/PLAYBACK/TAIL sequence, and both processes disconnected
cleanly. But the VAD pipeline itself never received a single frame.

**Evidence, re-verified this round (not just re-quoted)**:

```
r0082f_aecon_20260918T184355Z_mic_48k.wav:  44 bytes, channels=1, sampwidth=2,
                                             framerate=48000, nframes=0, duration=0s
                                             sha256=531cd597b25052b4845ef2f3887dec2183240802a93b6e044960de8ba017caa0
r0082f_aecon_20260918T184355Z_mic_16k.wav:  44 bytes, channels=1, sampwidth=2,
                                             framerate=16000, nframes=0, duration=0s
                                             sha256=ba584a378b11d9e9c98736fd8c256fe1453a84ee4139416d24b07acff424f0fb
r0082f_aecon_20260918T184355Z_timeline.csv: 253 bytes — header row only, ZERO data rows
```

**These are confirmed to be empty (header-only) files, not valid mic
evidence of any kind** — `nframes=0` means literally zero audio
samples were ever written; this was verified directly with
`wave.open()`, not inferred from file size alone. `0 VAD frames = 0
false starts` is not a PASS — it is an instrumentation failure, exactly
as this round's own instruction warned against silently treating it as
one.

**A process note, disclosed rather than hidden**: while re-verifying
this evidence, a cleanup command's overly broad glob pattern
accidentally deleted these same three files (along with a throwaway
dry-run's own temporary output that legitimately needed removing). Both
WAVs were reconstructed byte-for-byte via the same `_write_wav()`
helper (deterministic for `nframes=0`) and verified against the exact
SHA256 values above — an exact match, not an approximation. The CSV was
reconstructed as the same header-only row and verified by exact byte
size (253 bytes, matching the original before deletion). No information
was actually lost — these files never contained any real audio data —
but the mistake itself is recorded here for transparency, and the glob
pattern used in this round's own cleanup steps was corrected to be
scoped to specific filenames rather than a broad date-prefix wildcard.

### 89a. Root cause — confirmed, not merely hypothesized

`rtc.AudioStream` on a `LocalAudioTrack` backed by a synthetic
`rtc.AudioSource` (no room round-trip needed) was confirmed working in
R0082-F's own preparation round. That same mechanism, applied to a
`LocalAudioTrack` backed by a REAL `PlatformAudioSource`, silently
yielded zero frames on real hardware. This matches
`PlatformAudioSource`'s own docstring, which states frames are
"captured and sent directly by the ADM" — i.e. the native ADM pushes
audio straight into the outbound WebRTC/RTP pipeline, bypassing
whatever internal frame queue `AudioStream` taps into for a
Python-fed synthetic source. **This is now the empirically CONFIRMED
failure mode**, exactly the risk the prior round's own LIMITATIONS
section flagged and provided a fallback for — it did not require new
speculation to diagnose.

### 89b. Classification (as stated by the operator, confirmed by this round's own re-verification)

```
real PlatformAudio mic capture/publish        CONFIRMED
remote subscription to hardware mic           CONFIRMED (proven in R0082-D; re-proven this round, §90c)
local self-read synthetic AudioSource         CONFIRMED (prep round)
local self-read PlatformAudioSource           REFUTED for this harness
live Silero self-echo result                  NOT TESTED
zero false VAD events                         NOT PROVEN
```

## 90. Fix — retire local self-read, adopt R0082-D's proven remote-subscription pattern

Per instruction: no new receiving mechanism was invented. The VAD
observation point moved from the hardware role (self-read) to the
speech role (remote subscription to the hardware's published mic
track) — the exact pattern R0082-D's own `run_test_role` already used
successfully, with real, non-empty captured audio, across all four
R0082-D runs.

```
PROCESS A (--role hardware)              PROCESS B (--role speech)
  rtc.PlatformAudio()                      rtc.AudioSource (synthetic)
  real reSpeaker mic, real UAC playout      publishes the frozen speech WAV
  publishes its own mic track                      |
  subscribes to B's speech track  <----------------+ (automatic playout)
  (does NOT self-read; does NOT run VAD)            |
        |                                           | subscribes to A's
        |                                           | REMOTE mic track
        v                                           v
  holds for the fixed lifetime,                rtc.AudioStream(remote hw mic)
  then cleans up                                    v
                                              FIRST-FRAME GATE (mandatory)
                                                     v
                                          StreamingResampler -> Silero ->
                                          VADAnalyzer -> InterruptionStateMachine
                                                     v
                                          CSV + evidence, running CONCURRENTLY
                                          with playback (asyncio tasks)
```

Still exactly two OS processes — no relay process was needed. Neither
`PlatformAudio` itself, the `StreamingResampler`, nor the Level 1/2/3
VAD chain code were altered — all of §87's whole-array/irregular-chunk/
impulse/frequency validation remains valid, since none of that code
changed.

### 90a. Mandatory first-frame gate

`run_speech_role` now creates `rtc.AudioStream(remote_mic_track, ...)`
and `await`s exactly one real frame (`wait_for_first_frame()`, bounded
`FIRST_FRAME_TIMEOUT_S=15s`) BEFORE starting PRE_ROLL. On timeout, the
script prints `TEST INVALID` and raises `SystemExit` — it does not play
the speech stimulus and does not produce a PASS/FAIL summary. The first
real frame's own `sample_rate`/`num_channels`/`samples_per_channel` are
logged and explicitly checked (mono, 48000Hz) before proceeding — a
non-mono frame or an unexpected rate also raises `SystemExit` rather
than silently coercing the data.

### 90b. Fail-closed validity criteria

At the end of every run, regardless of outcome:

```
remote_audio_frames_received
remote_audio_samples_received
vad_frames_processed
capture_duration_s
expected_min_duration_s
capture_complete  (capture_duration_s >= expected_min_duration_s)
```

If `vad_frames_processed == 0`, the script prints
`*** INVALID TEST — NO VAD INPUT FRAMES ***` and returns without any
PASS/FAIL language. If `capture_complete` is `False`, it prints
`*** INVALID TEST — CAPTURE DURATION SHORTER THAN EXPECTED ***` and
also returns without PASS/FAIL language. Only when both checks pass
does the script report a genuine PASS/FAIL result.

### 90c. Live concurrency — playback and VAD consumption run together

`run_speech_role` starts a background `_consume_mic()` task (reading
the remote `AudioStream`, resampling, and feeding the VAD chain)
BEFORE starting PRE_ROLL, and that task keeps running, unmodified,
through PRE_ROLL, PLAYBACK, and TAIL — playback (`signal_source.
capture_frame()` in a loop) and mic consumption are two concurrent
`asyncio` tasks, not a record-then-process-afterward sequence. Silero
runs live, frame by frame, as remote mic audio actually arrives.

### 90d. Scientific limitation — restated, unchanged in substance

The VAD process now observes the mic AFTER: `PlatformAudio` capture ->
WebRTC sender -> the local LiveKit server -> Opus/RED transport &
decode -> the remote `rtc.AudioStream`, not by directly tapping local
post-AEC PCM inside the hardware process. This remains a research
OBSERVATION point, not a claim about NeXa's eventual production audio
routing — justified because it is now proven (twice: R0082-D, and
again this round in §91c) to carry the real hardware mic, it preserves
live timing, and no simpler already-proven alternative exists.

## 91. Offline/synthetic validation of the fix — ALL REQUIRED CHECKS PASS

```
py_compile                                      PASS
ruff                                            1 error found (unnecessary quoted
                                                 type annotation) and fixed -- 0 on re-check
git diff --check                                PASS
--help                                          PASS
dependency isolation                             PASS (no nexa.voice/pipecat/loguru/
                                                 google.genai in sys.modules)
```

### 91a. Positive/negative controls — re-run against the rewritten file

```
POSITIVE CONTROL: n_frames=724  max_prob=0.9966
  started_events=[0.672]  confirmed_events=[0.992]   -> PASS

NEGATIVE CONTROL: n_frames=812  max_prob=0.0238
  started_events=[]  confirmed_events=[]              -> PASS
```

Identical to every prior round — `StreamingResampler`/`LiveVadChain`
were not modified, only re-imported from the rewritten file to confirm
nothing broke in the surrounding restructuring.

### 91b. Mandatory fail-closed test — simulated zero-frame input

Per instruction, a fake stream that NEVER yields anything (exactly the
observed real failure mode) was fed directly into `wait_for_first_frame()`:

```python
class FakeEmptyStream:
    def __aiter__(self): return self
    async def __anext__(self):
        await asyncio.sleep(3600)  # simulates "never arrives"
```

Result: `wait_for_first_frame()` correctly raised `TimeoutError` after
the bounded 1.5s test timeout — confirmed the exact code path
`run_speech_role` uses to convert this into `TEST INVALID` /
`SystemExit` (traced by inspection: the `except TimeoutError:` block
immediately follows in the real function). **Fail-closed behavior
confirmed at the unit level**, not just asserted.

### 91c. Full end-to-end remote-subscription dry run — the fix proven working

A throwaway harness (deleted after use) ran the REAL, unmodified
`run_speech_role` against a synthetic fake-hardware participant
publishing the frozen speech WAV's own content under the
`r0082f_hardware` identity (standing in for real `PlatformAudio`, since
real hardware was not touched this round):

```
remote_audio_frames_received  = 2734
remote_audio_samples_received = 1312320
vad_frames_processed          = 854
capture_duration_s            = 27.340
expected_min_duration_s       = 27.181
capture_complete              = True

RESULT: max_prob=0.9965  started_events=1  confirmed_events=1
  VADUserStartedSpeaking-equivalent at t=1.504s
  INTERRUPT_CONFIRMED at t=1.824s

VALID TEST -- FAIL (see events above)
```

**This "FAIL" is the CORRECT and EXPECTED result for this specific
synthetic setup** — the throwaway fake-hardware role deliberately fed
strong, real speech content as its "mic audio" (not a quiet self-echo
residual), so a genuine detection is exactly what should happen; it
directly confirms the remote-subscription path, the first-frame gate,
the live concurrent playback+consumption, the validity accounting, and
evidence writing all work correctly together, end to end, live. An
earlier version of this same dry run (before the throwaway harness's
own hold time was lengthened to fully cover the speech role's window)
correctly triggered `capture_complete=False` and printed `INVALID
TEST` instead of any result — independently confirming the fail-closed
capture-duration check also works as designed, not just the
zero-frames check.

No production files were touched by this round's validation. No real
`PlatformAudio` hardware was invoked.

## 92. FINAL GATE — R0082-F READY FOR HARDWARE ATTEMPT #2

```
root cause of attempt #1 identified and confirmed    PASS
local self-read retired from canonical path           PASS
remote-subscription path adopted (R0082-D pattern)     PASS
first-frame gate implemented                           PASS
fail-closed zero-frame handling implemented+tested      PASS
fail-closed capture-duration handling implemented+tested PASS
live concurrency (playback + VAD consumption)          PASS
py_compile                                              PASS
ruff                                                    PASS
git diff --check                                        PASS
positive VAD control                                    PASS
negative silence control                                PASS
full remote-subscription dry run (synthetic)             PASS
```

**Verdict: R0082-F READY FOR HARDWARE ATTEMPT #2.** VAD configuration
unchanged (`confidence=0.7`, `start_secs=0.2`, `stop_secs=1.0`,
`min_volume=0.6`, `confirm_hold_secs=0.3` — not touched this round).
AEC remains ON, not re-swept. `BargeInController` remains out of scope,
unchanged. **This session did not execute hardware attempt #2.**

## 93. Exact operator procedure — hardware attempt #2, SILENT USER, AEC ON

**Prerequisite** (separate terminal, once):

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**The two commands — unchanged CLI shape from attempt #1, same script,
fixed internals:**

```bash
PROBE=/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3
POC=docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_vad_self_echo_poc.py
ROOM=r0082f_silent_user_$(date +%s)

# terminal A — hardware participant (real PlatformAudio, AEC ON; start first)
$PROBE $POC --role hardware --aec on --room-name "$ROOM"

# terminal B — speech participant (NOW also the VAD observer via remote subscription)
$PROBE $POC --role speech --room-name "$ROOM"
```

**The operator must remain completely silent throughout.** Terminal B
will now print `TEST INVALID` and refuse to play anything if no real
remote mic frame arrives within 15s — if that happens, STOP and report
rather than re-running blindly. If it proceeds, watch for the
`VALIDITY SUMMARY` block at the end: only trust the PASS/FAIL verdict
if `vad_frames_processed > 0` and `capture_complete = True`. **This has
intentionally NOT been run by this session.**

## 94. R0082-F hardware attempt #2 — REAL RESULT — VALID PASS

The operator executed the two-command procedure from §93 on real
hardware (real reSpeaker mic, real UAC speaker, real `livekit-server`
`--dev` instance, real production ONNX Silero model, real
`InterruptionStateMachine`). This session did not execute the run; the
operator reported the result and this session independently
re-verified every artifact below against the files on disk before
recording it here.

**Run identity**

```
run_id     = 20260918T190711Z
room       = r0082f_silent_user_002
AEC        = ON
```

**Reported result (operator)**

```
remote_audio_frames_received  = 2727
remote_audio_samples_received = 1308960
vad_frames_processed          = 852
capture_duration_s            = 27.270
expected_min_duration_s       = 27.181
capture_complete              = True
max_prob                      = 0.3810
started_events                = 0
confirmed_events              = 0
VALID TEST -- PASS
```

**Independent re-verification performed this session (not merely
trusted):**

- `sha256sum` on both evidence WAVs in
  `docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_captures/`:

  | file | SHA256 | match |
  |---|---|---|
  | `r0082f_aecon_20260918T190711Z_mic_48k.wav` | `a8a0daa7e72d357168a8821c81edabb5f50fa67ea459b8516b7dddea23c026d1` | exact |
  | `r0082f_aecon_20260918T190711Z_mic_16k.wav` | `74ef8fac716645b00b601edd233295cc2a625c5e5dcfd60b73f6c4e74a33f709` | exact |

- `wave.open()` inspection of both files:
  - 48k: `channels=1, sampwidth=2, framerate=48000, nframes=1308960` →
    `27.270s` — matches `remote_audio_samples_received` and
    `capture_duration_s` exactly.
  - 16k: `channels=1, sampwidth=2, framerate=16000, nframes=436320` →
    `27.270s` — exact 3:1 sample-rate ratio with the 48k file, as
    required by `StreamingResampler`'s fixed decimation factor.
- `r0082f_aecon_20260918T190711Z_timeline.csv`: `wc -l` = 853 lines =
  1 header row + **852 data rows**, exactly matching
  `vad_frames_processed=852`. Header row confirmed to match the
  script's own `CSV_HEADER` list exactly (no drift).
- Recomputed `max_prob` directly from the `silero_prob` column of all
  852 CSV rows (independent of the script's own printed summary):
  `max(silero_prob) = 0.38102`, `mean(silero_prob) = 0.01133` — matches
  the reported `max_prob = 0.3810` (4-decimal rounding of the same
  value).
- Counted rows with `vad_user_started_speaking_equivalent == 1` or
  `interrupt_confirmed == 1` directly: **0 in both columns, across all
  852 rows** — independently confirms `started_events=0` and
  `confirmed_events=0`, not merely the script's own printed count.
  `vad_state=QUIET` and `interruption_state=responding` hold for the
  full run (spot-checked first/last rows; consistent with zero VAD
  starts).

**LiveKit server transport stats for the hardware upstream (this run
only):**

```
packetsExpected    = 1080
packetsSeenPrimary = 1080
packetsLost         = 0
packetsOutOfOrder   = 0
nacks               = 0
rtt                 = 1ms
```

These stats describe this run only. They are not evidence of general
network reliability and must not be read as a claim that all future
runs will be lossless.

**Evidence chain of custody note:** attempt #1's evidence in the same
directory (`20260918T184355Z`, `nframes=0`, header-only CSV) is the
reconstructed placeholder described in §89 — reconstructed
byte-for-byte from the deterministic `_write_wav()` helper after an
accidental `rm -rf` during this session's own dry-run cleanup, and
independently re-verified against the original SHA256 values at that
time. It remains a record of the failed attempt, not of attempt #2.

**Classification (operator, adopted here as accurate and independently
re-verified):**

| claim | status |
|---|---|
| Live `PlatformAudio`/WebRTC remote mic path carries real captured audio | CONFIRMED |
| Live real speaker playback occurred | CONFIRMED |
| Live production-equivalent Silero VAD processing ran on the live remote stream | CONFIRMED |
| Zero false `VADUserStartedSpeaking`-equivalent events during this silent-user run | CONFIRMED |
| Zero `INTERRUPT_CONFIRMED` events during this silent-user run | CONFIRMED |
| Global self-interruption problem is solved | NOT PROVEN YET — a silent-user run cannot demonstrate correct behavior under a genuine interruption; that requires a deliberate-barge-in test (R0082-G, below) |

**Verdict: R0082-F hardware attempt #2 = VALID PASS, independently
re-verified.** This closes R0082-F. R0082-F alone does not prove
self-interruption is solved — it proves the live PlatformAudio→remote
subscription→StreamingResampler→Silero→InterruptionStateMachine chain
does not spuriously fire during genuine near-end silence with real
NeXa-equivalent speech playing through the real speaker with AEC ON.
Whether a genuine human interruption is correctly detected and acted
upon by this same chain is the subject of R0082-G.

## 95. R0082-G — DELIBERATE HUMAN BARGE-IN — preparation (no hardware run)

**Goal.** While NeXa-like speech plays through the real speaker, the
operator deliberately speaks over it once, at a documented cue. Must
show: Silero detects genuine near-end speech → an accepted
`VADUserStartedSpeaking`-equivalent → the REAL
`InterruptionStateMachine` reaches `INTERRUPT_CONFIRMED` → deterministic
playback stops promptly and does not resume. Still no Gemini, no full
NeXa Core, no production migration.

### 95.1 Architecture — unchanged from R0082-F

Same two-process split, same remote-subscription VAD observation point,
same `StreamingResampler`/`SileroOnnxModel`/`VolumeTracker`/
`LiveVadChain`/`InterruptionStateMachine`-bypass-load code, same AEC=ON.
No return to local self-read. Added purely additively in
`docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_vad_self_echo_poc.py`
via a new `--test-mode {silent-user, deliberate-bargein}` flag,
`silent-user` remains the default and is **byte-for-byte unchanged**:
independently diffed this session against the pre-R0082-G version of
`run_speech_role` (the silent-user function body) — `10405` characters,
identical, confirmed programmatically. The only shared-code touch is an
additive, backward-compatible extension to `LiveVadChain.process_frame()`
(`extra_fields: list | None = None` — `None` reproduces the exact prior
12-column row) and to `LiveVadChain` itself (new bookkeeping attributes
`started_events_mono`, `confirmed_events_mono`, `candidate_start_events*`
— unread by the silent-user path). `CSV_HEADER` (silent-user, 12 columns)
is untouched; a new `CSV_HEADER_BARGEIN` (14 columns) is used only by
deliberate-bargein mode.

### 95.2 VAD configuration — unchanged, not tuned

`confidence=0.7`, `start_secs=0.2`, `stop_secs=1.0`, `min_volume=0.6`,
`confirm_hold_secs=0.3` — identical constants, identical
`InterruptionStateMachine` load path, not touched this round.

### 95.3 Cue design and the exact playback-relative timestamp

A short-time RMS scan of the frozen stimulus (`r0082d_speech_en_pl_v1.wav`,
50ms window / 25ms hop, 200-count int16 RMS silence threshold) found the
speech-activity envelope of the file. Selected excerpt around the
chosen cue window (full trace available by re-running the same scan):

```
SPEECH: 5.400s - 5.950s
gap:    5.950s - 6.100s  (0.150s)
SPEECH: 6.100s - 6.450s
SPEECH: 6.475s - 7.600s   <- longest run bracketing the cue
gap:    7.600s - 7.925s  (0.325s)
SPEECH: 7.925s - 9.300s
```

The cue is fixed at (playback-relative, i.e. relative to the moment
`PLAYBACK start` fires):

```
BARGEIN_CUE_START_TIME_S  = 4.0   -- "BARGE-IN IN 3"
                             5.0   -- "BARGE-IN IN 2"
                             6.0   -- "BARGE-IN IN 1"
BARGEIN_SPEAK_NOW_TIME_S  = 7.0   -- ">>> SPEAK NOW <<<"
```

`SPEAK NOW` lands inside the 6.475s-7.600s continuous English run, with
margin from both the preceding 0.150s micro-gap and the following
0.325s gap before the next 7.925s-9.300s run — chosen deliberately so
the operator's post-reaction-time speech is very likely to still
overlap genuine deterministic playback, which is the condition under
test (not silence at the moment of the human's own barge-in). The cue
is terminal text only, delivered by a dedicated `asyncio.sleep`-paced
task (`_play_signal_cancelable`'s internal `_deliver_cue()`) running
concurrently with, but decoupled from, the frame-submission loop's own
pacing — **no speaker beep**, per instruction, since a beep is itself
an acoustic event the mic would capture and would contaminate the VAD/
interruption evidence.

**Operator phrase (first canonical run):** `Przerwij, teraz opowiedz mi
o czymś innym.` — printed as part of the `SPEAK NOW` line itself so the
operator does not need to memorize it in advance. Operator begins
speaking immediately after the cue; no millisecond-precision timing is
required of the human.

### 95.4 Playback cancellation — real, not faked

New helper `_play_signal_cancelable()` replaces the frame-submission
loop for deliberate-bargein mode only (the silent-user loop is untouched
and inlined as before). It checks a shared `asyncio.Event`
(`interrupt_confirmed_event`) once per 10ms frame and, the instant it is
set, **stops calling `signal_source.capture_frame()` entirely** — no
further audio reaches the LiveKit track from that point on. The event is
set from exactly one place: inside `_consume_mic`'s VAD loop, the moment
`chain.confirmed_events` (populated only by the REAL
`InterruptionStateMachine`'s own `poll()` reaching
`interrupt_confirmed`) grows. Raw Silero probability crossing 0.7, or a
candidate start existing, never sets this event by itself — only a
genuine `INTERRUPT_CONFIRMED`. There is no retry/resume code path
anywhere in `_play_signal_cancelable` or its caller; once the loop
breaks it is not re-entered for that signal source. `PLAYBACK_CANCEL_
REQUESTED` and `PLAYBACK_STOPPED` are logged with monotonic timestamps
at the moment each occurs and recorded into a per-run
`..._milestones.json` sidecar (see §95.6).

### 95.5 Validity criteria — separate from silent-user mode

Deliberate-bargein mode does **not** require the full ~27.18s capture
(a successful interruption intentionally shortens it). New pure
function `evaluate_deliberate_bargein_result()`:

- if a post-cue `INTERRUPT_CONFIRMED` occurred: requires
  `capture_duration_s >= max(speak_now_boundary, first_confirmed_t_s +
  1.0s margin)`;
- otherwise (nothing legitimately shortened the run): requires the
  same full duration silent-user mode requires
  (`PRE_ROLL_S + SPEECH_DURATION_S + TAIL_S ≈ 27.18s`).

Silent-user mode's own inline `capture_complete = capture_duration_s >=
expected_min_duration_s` rule is untouched and was independently
verified this round to still reject the exact same shortened-capture
value that deliberate-bargein mode accepts (offline test, §95.7).

### 95.6 Evidence and latency measurement

Deliberate-bargein mode writes: `r0082g_bargein_<run_id>_mic_48k.wav`,
`..._mic_16k.wav`, `..._timeline.csv` (14-column `CSV_HEADER_BARGEIN`,
adds `playback_active`/`playback_cancel_requested` to the original 12
columns), and a new `..._milestones.json` sidecar recording monotonic
timestamps for `playback_start`, `cue_start`, `speak_now`,
`playback_cancel_requested`, `playback_stopped`,
`last_speech_frame_submitted`, plus derived latencies
`cue_to_vad_start_s`, `cue_to_interrupt_confirmed_s`,
`interrupt_confirmed_to_cancel_requested_s`. Cue-to-detection latencies
explicitly include human reaction time (operational, not pure
algorithmic latency) — stated, not hidden. Offline post-hoc acoustic-
onset re-estimation from the captured mic WAV (independent of the live
decision) remains available via the same RMS-scan technique used for
cue selection, but was not required for this preparation round and is
left as an optional follow-up, not claimed as done.

One documented CSV bookkeeping nuance (observed in the dry run, §95.8):
the `interrupt_confirmed=1` row itself is written with
`playback_cancel_requested` still `0`, because `extra_fields` is
captured at the start of that frame's processing, before the
just-detected confirmation updates the flag; the very next row (recorded
~27µs later in the dry run) correctly shows `playback_cancel_requested=1`.
This is expected, documented bookkeeping order, not a defect — the
authoritative confirmation moment is still exactly the
`interrupt_confirmed=1` row.

### 95.7 FAIL taxonomy and pre-cue false-positive tracking

`evaluate_deliberate_bargein_result()` returns one of:

```
PASS      -- exactly the expected chain, no pre-cue false positive
FAIL-A    -- operator spoke, no accepted post-cue VAD start
FAIL-B    -- accepted post-cue VAD start, no post-cue INTERRUPT_CONFIRMED
FAIL-C    -- INTERRUPT_CONFIRMED occurred, playback did not stop early
             (frame submission reached natural completion)
FAIL-D    -- playback stopped then resumed (structurally impossible by
             this loop's design; checked anyway)
FAIL-F    -- test instrumentation invalid (no cue emitted, zero VAD
             frames, or capture shorter than the relaxed rule allows)
```

`pre_cue_false_positive` (any accepted VAD start or confirmed
interruption before the cue's own countdown-start boundary) is computed
and reported **independently** of the verdict above — a later genuine
interruption never hides an earlier false trigger. "FAIL-E" in the
original taxonomy is this pre-cue flag, surfaced as its own boolean
rather than folded into the verdict enum, specifically so it cannot be
masked by an otherwise-clean post-cue PASS.

### 95.8 Offline validation performed this round (all before any hardware)

```
py_compile                                                    PASS
ruff                                                           PASS
git diff --check                                               PASS
silent-user run_speech_role body byte-for-byte unchanged        PASS
  (10405 chars, programmatic diff, identical)
evaluate_deliberate_bargein_result(): PASS scenario              PASS
  ...FAIL-A / FAIL-B / FAIL-C / FAIL-D scenarios                 PASS (4/4)
  ...pre-cue false positive flagged + does not mask a PASS        PASS
  ...FAIL-F (zero frames / capture too short)                    PASS (2/2)
  ...deliberate-mode ACCEPTS shortened capture (10.75s vs the
     27.18s silent-user requirement)                              PASS
  ...silent-user's OWN unchanged rule still REJECTS that same
     10.75s capture                                               PASS
_play_signal_cancelable(): pre-set cancel -> 0 frames submitted   PASS
  ...never cancelled -> full natural completion                  PASS
  ...mid-stream cancel -> stops promptly, 0 < offset < total       PASS
  ...re-invocation with an already-set event -> still 0 frames
     (no hidden resume state)                                     PASS
LiveVadChain.process_frame() backward compatibility:
  ...no-extra_fields row == 12 columns, byte-identical to before   PASS
  ...extra_fields=[..] row == 14 columns, first 11 content
     fields identical (field 0 is wall-clock, expected to differ)  PASS
Positive control (frozen speech WAV): max_prob=0.9966,
  started_events=1, confirmed_events=1                            PASS
Negative control (26s silence): 0 started, 0 confirmed             PASS
FULL END-TO-END DRY RUN (synthetic fake-hardware publisher
  standing in for real hardware, feeding real speech content as a
  simulated operator barge-in at a scheduled time; REAL unmodified
  run_speech_role_deliberate_bargein subscribing to it remotely,
  against a real local livekit-server):
    remote_audio_frames_received = 1285
    vad_frames_processed         = 401
    capture_duration_s           = 12.850  (min_required = 11.816)
    playback_stopped_early       = True (stopped at 424800/1112708
                                    samples submitted -- genuinely
                                    short of natural completion)
    pre_cue_false_positive       = False
    post_cue_started_events      = [10.496]
    post_cue_confirmed_events    = [10.816]
    cue_to_vad_start_s                        = 0.514s
    cue_to_interrupt_confirmed_s              = 0.834s
    interrupt_confirmed_to_cancel_requested_s = 0.000s
    VERDICT: PASS                                                 PASS
```

No production files were touched by this round's implementation or
validation (`src/`, `apps/`, Gemini, `ConversationSession`, NeXa Core,
`BargeInController` production code, `AecReferenceFeeder`, XVF3800 DSP,
PipeWire/ALSA/system-mixer config, speaker volume — none modified).
The dry-run evidence was moved (not overwritten in place, not deleted)
into a clearly separated
`r0082f_live_captures/r0082g_dryrun_synthetic_NOT_real_hardware/`
subdirectory precisely so it can never be confused with a real hardware
deliberate-bargein run.

## 96. R0082-G — FINAL GATE

```
architecture preserved (no self-read regression)              PASS
VAD configuration unchanged, not tuned                          PASS
cue timing chosen from real stimulus data + documented           PASS
playback cancellation triggered ONLY by INTERRUPT_CONFIRMED      PASS
playback does not resume after cancellation (structural + tested) PASS
pre-cue false positives tracked and reported separately          PASS
deliberate-bargein mode has its own validity rule (not reused
  from silent-user)                                              PASS
silent-user mode remains available and byte-for-byte unchanged    PASS
py_compile / ruff / git diff --check                             PASS
synthetic silence -> zero false events                           PASS
synthetic strong speech -> VAD start + INTERRUPT_CONFIRMED         PASS
synthetic confirmed interruption -> playback genuinely cancels     PASS
full end-to-end dry run (mutual subscription, first-frame gate,
  concurrent cue delivery + consumption + cancellation, real
  InterruptionStateMachine, real evidence writing)                PASS
no production files touched                                      PASS
```

**Verdict: R0082-G READY FOR ONE REAL DELIBERATE-BARGE-IN HARDWARE
RUN.** This session did not execute hardware. Five-run replication at
different timing points is explicitly a LATER step, gated on this first
single run's own real-hardware result — not attempted this round.

## 97. Answers to the 11 questions

1. **Is R0082-F attempt #2 recorded as a VALID PASS?** Yes — §94, with
   independently re-verified SHA256/frame-count/CSV evidence.
2. **Is deliberate-barge-in mode implemented?** Yes —
   `--test-mode deliberate-bargein`, additive, default remains
   `silent-user`.
3. **At what playback-relative time is the operator cue?** Countdown
   starts at t=4.0s, `SPEAK NOW` at t=7.0s (playback-relative), chosen
   from an RMS scan showing continuous active speech 6.475s-7.600s —
   see §95.3.
4. **Does only `INTERRUPT_CONFIRMED` stop playback?** Yes — the
   cancellation event is set from exactly one place, gated on
   `chain.confirmed_events` growing (the real `InterruptionStateMachine`
   reaching `interrupt_confirmed`), never on raw probability or a
   candidate start alone.
5. **Does playback stay stopped?** Yes — no resume code path exists;
   verified both structurally and by a dedicated offline test
   (re-invocation with an already-set cancel event still submits zero
   frames) and by the live dry run (playback never resumed after
   cancellation, through TAIL and disconnect).
6. **Are pre-cue false positives tracked separately?** Yes —
   `pre_cue_false_positive` is computed and reported independently of
   the post-cue PASS/FAIL verdict.
7. **Does deliberate mode use its own validity rule?** Yes — §95.5;
   silent-user's own rule is untouched and was verified to still reject
   the same shortened capture deliberate-bargein mode accepts.
8. **Do all offline controls pass?** Yes — §95.8, including a full
   synthetic end-to-end dry run, not just unit-level checks.
9. **Were any production files changed?** No — only
   `r0082f_live_vad_self_echo_poc.py` (research script) and this report
   were modified; `src/`, `apps/`, and all named production subsystems
   are untouched.
10. **Is R0082-G READY for ONE real deliberate-barge-in run?** Yes.
11. **Exact operator commands and instructions** — see §98.

## 98. Exact operator procedure — ONE real deliberate-barge-in run only

**Do not run this more than once yet.** Five-run replication at
different timing points is a later step, gated on this run's own
result.

**Prerequisite** (separate terminal, once):

```bash
/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/livekit_server/livekit-server --dev --bind 127.0.0.1
```

**The two commands:**

```bash
PROBE=/tmp/claude-1000/-home-devdul-Projects-NeXa-IkiGai/scratchpad/r0082_livekit_probe_venv/bin/python3
POC=docs/research/r0082_livekit_webrtc_audio_poc/r0082f_live_vad_self_echo_poc.py
ROOM=r0082g_bargein_$(date +%s)

# terminal A -- hardware participant (real PlatformAudio, AEC ON; start first)
$PROBE $POC --role hardware --aec on --room-name "$ROOM"

# terminal B -- speech participant + deliberate-bargein cue/observer
$PROBE $POC --role speech --test-mode deliberate-bargein --room-name "$ROOM"
```

**What the operator should do:** stay completely silent until terminal
B prints the countdown. When it prints:

```
BARGE-IN IN 3
BARGE-IN IN 2
BARGE-IN IN 1
>>> SPEAK NOW <<<   (Przerwij, teraz opowiedz mi o czymś innym.)
```

say **exactly**: `Przerwij, teraz opowiedz mi o czymś innym.` —
starting immediately after `SPEAK NOW` appears. No millisecond-precision
timing is required; ordinary reaction time is fine and is explicitly
accounted for in the latency report (`cue_to_vad_start_s`,
`cue_to_interrupt_confirmed_s`). Speak once; do not repeat the phrase
unless the run is being deliberately repeated later as one of the
five-run replication set.

Terminal B will refuse to play anything (`TEST INVALID`) if no real
remote mic frame arrives within 15s. At the end it prints a
`VALIDITY + CLASSIFICATION SUMMARY` block — only trust the verdict if
`capture_ok = True`; a real barge-in should show `VERDICT: PASS` with
exactly one (or occasionally a few duplicate) post-cue VAD start(s) and
exactly one confirmed interruption, **and `pre_cue_false_positive =
False`** — as of §99 below, `VERDICT: PASS` can no longer occur at all
if a false accepted speech start or confirmed interruption happened
before the operator's own cue (that case is now `VERDICT: FAIL-E`).
**This has intentionally NOT been run by this session.**

## 99. CORRECTION — FAIL-E was computed but not enforced; now fixed (no hardware run)

**Defect found (by operator review, before any hardware execution):**
`evaluate_deliberate_bargein_result()` (§95.7/§96 above) computed
`pre_cue_false_positive` but never consulted it when choosing `verdict`.
Concretely, a run could report:

```
pre_cue_false_positive = True
VERDICT                = PASS
```

if the later genuine post-cue barge-in chain also happened to succeed
(post-cue VAD start → post-cue `INTERRUPT_CONFIRMED` → playback stopped
early → no resume). A false accepted speech start or a confirmed
interruption **before** the operator's own deliberate cue is exactly
the original self-echo failure mode this whole R0082 investigation
exists to rule out — masking it behind a later genuine success is not
acceptable for R0082-G. This was a real defect in the verdict logic,
not merely an omission in reporting: the detailed `pre_cue_started`/
`pre_cue_confirmed` lists were already being computed and printed
correctly; only the top-level `verdict` failed to use them.

**Fix.** `evaluate_deliberate_bargein_result()` in
`r0082f_live_vad_self_echo_poc.py` now checks `pre_cue_false_positive`
immediately after instrumentation validity and before any post-cue
outcome is considered, matching the canonical ordering:

```python
if vad_frames_processed == 0 or not cue_emitted or not capture_ok:
    verdict = "FAIL-F"
elif pre_cue_false_positive:
    verdict = "FAIL-E"
elif not post_cue_started:
    verdict = "FAIL-A"
elif not post_cue_confirmed:
    verdict = "FAIL-B"
elif not playback_stopped_early:
    verdict = "FAIL-C"
elif playback_resumed_after_stop:
    verdict = "FAIL-D"
else:
    verdict = "PASS"
```

`pre_cue_started` and `pre_cue_confirmed` remain in the returned dict in
full detail (unchanged — the fix only changes which branch `verdict`
takes, not what evidence is retained or reported). The module docstring
(FAIL taxonomy section), the printed pre-cue warning in
`run_speech_role_deliberate_bargein`'s summary block, and the operator
procedure above (§98) were all updated to state plainly that
`VERDICT: PASS` now requires `pre_cue_false_positive is False` as a
hard precondition, not an independently-reported side note.

**Scope of the fix.** Pure classification logic only
(`evaluate_deliberate_bargein_result()`'s `if/elif` chain and its
docstring) plus documentation/print-text updates. No change to
`_play_signal_cancelable()`, no change to the live consumer/playback
wiring, no change to cue timing, no change to VAD thresholds
(`confidence`/`start_secs`/`stop_secs`/`min_volume`/`confirm_hold_secs`
all untouched), no change to `--test-mode silent-user` (re-verified
byte-for-byte identical to the pre-fix version — programmatic diff,
`10405` characters, identical). No production files touched.

**Offline validation performed (pure classification tests; the full
synthetic LiveKit dry run was NOT re-run, per instruction, since this
fix does not touch runtime wiring):**

```
py_compile                                                         PASS
ruff                                                                PASS
git diff --check                                                    PASS
CASE 1: pre-cue accepted VAD start + otherwise-successful post-cue
  chain (post-cue start, post-cue confirm, playback stopped early)
  => FAIL-E (was PASS before the fix)                                PASS
CASE 2: pre-cue CONFIRMED interruption + otherwise-successful
  post-cue chain => FAIL-E                                           PASS
CASE 3: clean pre-cue (no events before the cue) + successful
  post-cue chain => PASS (confirms the fix does not over-correct
  into always-failing)                                               PASS
Existing FAIL-A / FAIL-B / FAIL-C / FAIL-D / FAIL-F scenarios
  all still classify correctly (regression check)                    PASS (6/6)
pre_cue_started / pre_cue_confirmed / post_cue_started /
  post_cue_confirmed detail lists still populated correctly
  under FAIL-E (evidence preserved, not erased by the fix)           PASS
silent-user run_speech_role body byte-for-byte unchanged
  (10405 chars, programmatic diff, identical)                        PASS
```

**Verdict: FAIL-E fix CONFIRMED correct offline.
R0082-G remains READY for one real deliberate-barge-in hardware run**
— same architecture, same cue, same cancellation mechanism, same
validity rule, only the verdict-classification defect above corrected.
This session did not execute hardware.

## 100. CORRECTION #2 — FAIL-E was checked against the wrong boundary (countdown-start, not SPEAK NOW); now fixed (no hardware run)

This is a **second, distinct** defect found by a subsequent operator
review, still before any hardware execution. It does not undo or
contradict §99 — that fix (checking `pre_cue_false_positive` in the
verdict at all) was and remains correct. This round found that the
*boundary* used to compute the pre/post split itself was wrong.

**Defect.** The operator is explicitly instructed to remain completely
silent through the ENTIRE countdown ("BARGE-IN IN 3" / "IN 2" / "IN 1")
and to begin speaking only once `>>> SPEAK NOW <<<` appears. §99's fix
split events at `cue_start_boundary_t_s` — countdown-start
(`PRE_ROLL_S + BARGEIN_CUE_START_TIME_S` = t=6.0s in the audio-relative
timeline) — not at `speak_now_boundary_t_s` — SPEAK NOW
(`PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S` = t=9.0s). Consequently, a
false accepted VAD start or confirmed interruption occurring DURING the
countdown itself (e.g. while "BARGE-IN IN 2" was on screen, operator
still silent by instruction, t somewhere in 6.0s–9.0s) was bucketed as
"post-cue" and could still reach `VERDICT=PASS` if the genuine
post-SPEAK-NOW barge-in also succeeded — exactly the same class of
masking §99 was meant to close, just shifted 3 seconds later in the
timeline.

**Fix.** The canonical false-positive boundary in
`evaluate_deliberate_bargein_result()` is now `speak_now_boundary_t_s`,
not `cue_start_boundary_t_s`:

```python
pre_user_started   = [t for t in started_events   if t < speak_now_boundary_t_s]
pre_user_confirmed = [t for t in confirmed_events if t < speak_now_boundary_t_s]
post_user_started   = [t for t in started_events   if t >= speak_now_boundary_t_s]
post_user_confirmed = [t for t in confirmed_events if t >= speak_now_boundary_t_s]
pre_user_false_positive = bool(pre_user_started or pre_user_confirmed)
```

`cue_start_boundary_t_s` remains a parameter, used only to compute
diagnostic sub-splits (`pre_countdown_started`/`pre_countdown_confirmed`
= before countdown-start; `during_countdown_started`/
`during_countdown_confirmed` = between countdown-start and SPEAK NOW)
retained purely for telemetry — it no longer participates in the
PASS/FAIL decision at all. `speak_now_boundary_t_s` is still derived
explicitly as `PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S`, never
hard-coded.

**Naming.** Result fields were renamed to the unambiguous canonical
form: `pre_user_false_positive`, `pre_user_started`, `pre_user_confirmed`,
`post_user_started`, `post_user_confirmed`. The old names
(`pre_cue_false_positive`, `pre_cue_started`, `pre_cue_confirmed`,
`post_cue_started`, `post_cue_confirmed`) are retained in the returned
dict strictly as backward-compatible ALIASES of the SAME
SPEAK-NOW-bounded values — they are not recomputed against
countdown-start, so no caller reading the old names sees stale
countdown-boundary semantics. `run_speech_role_deliberate_bargein`'s
printed summary and the module's own FAIL-taxonomy docstring were both
updated to use the canonical names and to print the
`pre_countdown_*`/`during_countdown_*` diagnostic breakdown alongside
the pre-SPEAK-NOW false-positive warning. Per-run latency field names
were also clarified for the same reason: `cue_to_vad_start_s` →
`speak_now_to_vad_start_s`, `cue_to_interrupt_confirmed_s` →
`speak_now_to_interrupt_confirmed_s` (both already measured from
`milestones["speak_now"]`; only the name was ambiguous, not the value).

**History, not rewritten:** (1) FAIL-E was first fixed at the verdict
level (§99) — `pre_cue_false_positive` started being consulted at all.
(2) This second review then found the boundary itself
(countdown-start vs. SPEAK NOW) was wrong. (3) The canonical
human/false-positive boundary is now SPEAK NOW, documented explicitly
in the module docstring, with countdown-start preserved only as
diagnostic telemetry.

**Latency caveat, restated and expanded per instruction:**
`speak_now_to_vad_start_s` (and `speak_now_to_interrupt_confirmed_s`)
are measured from SPEAK NOW and therefore include human reaction
time — they are operational latencies, not pure algorithmic ones. The
live verdict uses SPEAK NOW as the earliest possible human-speech
boundary; a VAD event occurring very shortly after SPEAK NOW could, in
principle, still precede the operator's true physical acoustic onset,
since reaction time varies between operators and even between runs by
the same operator. **This is explicitly not addressed by changing VAD
thresholds** (none were touched — `confidence=0.7`, `start_secs=0.2`,
`stop_secs=1.0`, `min_volume=0.6`, `confirm_hold_secs=0.3` remain
exactly as audited from production). The raw 48k/16k mic WAV and the
full per-frame CSV timeline continue to be preserved specifically so
the actual acoustic onset can be independently re-estimated offline
after a real run — the same short-time-RMS-scan technique used to
choose the cue timestamp in §95.3 — without altering this live
decision.

**Scope of the fix.** Pure classification logic
(`evaluate_deliberate_bargein_result()`'s boundary computation and
returned field names), the caller's print/latency-key text in
`run_speech_role_deliberate_bargein`, and the module docstring's FAIL
taxonomy section. No change to `_play_signal_cancelable()`, no change
to the live consumer/playback wiring, no change to the cue's own
timing (`BARGEIN_CUE_START_TIME_S`/`BARGEIN_SPEAK_NOW_TIME_S`
unchanged), no change to VAD thresholds, no change to
`--test-mode silent-user` (re-verified byte-for-byte identical,
programmatic diff, `10405` characters, identical). No production files
touched.

**Offline validation performed (pure classification tests; full
synthetic dry run NOT re-run, per instruction — this fix does not touch
runtime wiring, only the classification function's boundary and result
field names):**

```
py_compile                                                          PASS
ruff                                                                 PASS
git diff --check                                                     PASS
CASE 1 -- false event BEFORE the countdown even starts (t=1.0s,
  well before countdown-start at t=6.0s) + otherwise-successful
  post-SPEAK-NOW chain => FAIL-E                                     PASS (6/6 sub-checks)
CASE 2 (THE BUG BEING FIXED) -- false accepted VAD start DURING
  the countdown itself (t=7.5s, squarely between countdown-start
  6.0s and SPEAK NOW 9.0s -- e.g. while "BARGE-IN IN 2" is on
  screen, operator still silent by instruction) + otherwise-
  successful post-SPEAK-NOW barge-in => FAIL-E, NOT PASS (this is
  exactly what the old countdown-start boundary got wrong)           PASS (4/4 sub-checks)
CASE 3 -- CONFIRMED interruption DURING the countdown (operator
  still silent) => FAIL-E regardless of later events (no
  post-SPEAK-NOW events at all in this scenario)                     PASS (3/3 sub-checks)
CASE 4 -- completely clean until SPEAK NOW, genuine event after,
  INTERRUPT_CONFIRMED, playback stops, no resume => PASS              PASS
CASE 5 -- existing FAIL-A/B/C/D/F classifications remain correct       PASS (6/6)
CASE 6 -- silent-user mode's run_speech_role body byte-for-byte
  unchanged (10405 chars, programmatic diff vs. last commit)          PASS
_play_signal_cancelable() cancellation/no-resume regression            PASS (10/10, unaffected)
LiveVadChain.process_frame() CSV backward-compatibility regression     PASS (4/4, unaffected)
Positive/negative VAD control regression                               PASS (5/5, unaffected)
pre_cue_*/post_cue_* aliases verified to equal the new SPEAK-NOW-
  bounded pre_user_*/post_user_* values (not countdown-start-bounded)  PASS
```

46 checks total, all PASS.

**Verdict: SPEAK-NOW-boundary fix CONFIRMED correct offline. R0082-G
remains READY for one real deliberate-barge-in hardware run** — same
architecture, same cue timing, same cancellation mechanism, same
validity rule, only the classification boundary and field names
corrected. This session did not execute hardware.

## 101. CORRECTION #3 — a correctly-detected pre-SPEAK-NOW false interruption could be mislabeled FAIL-F instead of FAIL-E; now fixed (no hardware run)

A **third, distinct** defect, found by a further operator review,
still before any hardware execution. It does not undo or contradict
§99 or §100 — both remain correct. This round closes a
runtime-interaction edge case those two fixes did not yet cover.

**Defect.** Playback correctly cancels IMMEDIATELY on ANY
`INTERRUPT_CONFIRMED`, including one that fires before `SPEAK NOW` — a
genuine self-echo false positive. That is required, correct safety/
evidence behavior and was never in question. However,
`_play_signal_cancelable()` also cancels the cue-delivery task the
instant playback stops early. So this sequence is possible on real
hardware:

```
operator silent
false self-echo -> INTERRUPT_CONFIRMED during (or even before) the countdown
-> playback_cancel_requested -> playback stops -> cue task cancelled
-> SPEAK NOW never emitted -> cue_emitted = False
```

The verdict ordering fixed in §99/§100 still checked instrumentation
validity as a single combined condition:

```python
if vad_frames_processed == 0 or not cue_emitted or not capture_ok:
    verdict = "FAIL-F"
elif pre_user_false_positive:
    verdict = "FAIL-E"
```

Since `cue_emitted` is `False` in the sequence above, this run would
hit the FIRST branch and report `FAIL-F` (instrumentation invalid) —
even though the run was actually a textbook example of the ORIGINAL
self-echo failure mode being correctly detected and correctly acted
upon. `FAIL-F` implies "we don't know what happened"; the correct
label is `FAIL-E` ("we know exactly what happened, and it was a false
positive"). Compounding this, `capture_ok`'s minimum-duration
requirement (`MIN_DELIBERATE_CAPTURE_S`, ≈ reaching `SPEAK NOW`) also
assumed the run would reach `SPEAK NOW` under normal circumstances — a
run correctly, legitimately, cut short by a real self-echo detection
would usually fail that requirement too, compounding the
misclassification risk.

**Required behavior, preserved exactly:** playback cancellation is NOT
delayed or suppressed. A pre-`SPEAK-NOW` `INTERRUPT_CONFIRMED` SHOULD
stop playback immediately — that is exactly what should happen and
remains unchanged. The fix is entirely in verdict/validity semantics,
not in when or whether cancellation happens.

**Fix.** `evaluate_deliberate_bargein_result()`'s capture-validity
computation now depends on which of three situations applies:

```python
if pre_user_false_positive:
    earliest_pre_user_event_t_s = min(pre_user_started + pre_user_confirmed)
    min_required_s = earliest_pre_user_event_t_s + POST_EVENT_EVIDENCE_MARGIN_S
elif post_user_confirmed:
    min_required_s = max(
        speak_now_boundary_t_s, post_user_confirmed[0] + POST_INTERRUPT_MARGIN_S
    )
else:
    min_required_s = MIN_DELIBERATE_CAPTURE_S
capture_ok = capture_duration_s >= min_required_s

if vad_frames_processed == 0 or not capture_ok:
    verdict = "FAIL-F"
elif pre_user_false_positive:
    verdict = "FAIL-E"
elif not cue_emitted:
    verdict = "FAIL-F"
elif not post_user_started:
    verdict = "FAIL-A"
elif not post_user_confirmed:
    verdict = "FAIL-B"
elif not playback_stopped_early:
    verdict = "FAIL-C"
elif playback_resumed_after_stop:
    verdict = "FAIL-D"
else:
    verdict = "PASS"
```

A new constant `POST_EVENT_EVIDENCE_MARGIN_S = 0.5` (seconds) defines
the small post-event evidence margin: when a pre-`SPEAK-NOW` false
positive occurred, only this much capture past the EARLIEST offending
event is required — proving that event's own evidence (WAV/CSV/
timestamps) was genuinely captured and persisted — not the full
`MIN_DELIBERATE_CAPTURE_S`/`POST_INTERRUPT_MARGIN_S` requirement that
assumes the run reached (or nearly reached) `SPEAK NOW`. `cue_emitted`
is now checked as its own, separate branch, reached ONLY after
`pre_user_false_positive` has already been ruled out — so a genuine
instrumentation failure (cue never fired for some OTHER, non-self-echo
reason) still correctly reports `FAIL-F`, while a cue-never-fired
caused by a correctly-detected pre-`SPEAK-NOW` false positive now
correctly reports `FAIL-E`.

**Scope of the fix.** `evaluate_deliberate_bargein_result()`'s validity
computation and verdict `if/elif` ordering, one new constant
(`POST_EVENT_EVIDENCE_MARGIN_S`), and docstring/FAIL-taxonomy text
only. **`_play_signal_cancelable()` and the live cancellation wiring
are untouched** — re-verified this round by an explicit programmatic
diff against the last commit (byte-for-byte identical), confirming
cancellation remains immediate and unconditional on any
`INTERRUPT_CONFIRMED`, not delayed or suppressed by this fix. No
change to cue timing, no change to VAD thresholds
(`confidence`/`start_secs`/`stop_secs`/`min_volume`/`confirm_hold_secs`
unchanged). `--test-mode silent-user` re-verified byte-for-byte
unchanged (`10405` characters, programmatic diff, identical). No
production files touched.

**Offline validation performed (pure classification tests; full
synthetic dry run NOT re-run, per the same reasoning as §99/§100 — this
fix does not touch runtime wiring):**

```
py_compile                                                          PASS
ruff                                                                 PASS
git diff --check                                                     PASS
TEST 1 -- pre-countdown INTERRUPT_CONFIRMED -> playback cancels
  before the cue ever starts -> cue_emitted=False -> verdict MUST
  be FAIL-E, not FAIL-F                                              PASS (3/3 sub-checks)
TEST 2 -- during-countdown INTERRUPT_CONFIRMED -> playback stops
  before SPEAK NOW -> cue_emitted=False -> FAIL-E                     PASS (2/2 sub-checks)
TEST 3 -- no pre-user event + SPEAK NOW never emitted because of a
  GENUINE instrumentation problem (not a self-echo event) -> FAIL-F   PASS (2/2 sub-checks)
TEST 4 -- no pre-user event + clean SPEAK NOW + genuine barge-in
  succeeds -> PASS                                                    PASS
TEST 5 -- pre-user accepted START without confirmation (cue fires
  normally, post-SPEAK-NOW chain succeeds) -> still FAIL-E             PASS (2/2 sub-checks)
TEST 6 -- existing FAIL-A/B/C/D/F classifications remain correct       PASS (6/6)
_play_signal_cancelable() body byte-for-byte identical to the last
  commit -- cancellation remains immediate/unconditional, not
  delayed or suppressed by this fix                                   PASS
silent-user run_speech_role body byte-for-byte identical to the
  last commit (10405 chars, programmatic diff)                        PASS
_play_signal_cancelable() cancellation/no-resume regression
  (Section 2 of the offline suite, unaffected by this fix)            PASS (10/10)
LiveVadChain.process_frame() CSV backward-compatibility regression     PASS (4/4, unaffected)
Positive/negative VAD control regression                               PASS (5/5, unaffected)
```

62 checks total, all PASS.

**Verdict: FAIL-F/FAIL-E validity-semantics fix CONFIRMED correct
offline. R0082-G remains READY for one real deliberate-barge-in
hardware run** — same architecture, same cue timing, same cancellation
mechanism (immediate, unconditional, unmodified), same SPEAK-NOW
classification boundary, only the interaction between capture validity
and the pre-`SPEAK-NOW` false-positive check corrected. This session
did not execute hardware.

## 102. R0082-G real hardware deliberate-barge-in run #1 — RESULT: VALID PASS

The operator executed the exact §98 procedure (with `--test-mode
deliberate-bargein`) on real hardware. This session did not execute
the run; the operator reported the result and this session
independently re-verified every artifact below before recording it
here, per this investigation's established discipline.

**Run identity**

```
room       = r0082g_bargein_001
run_id     = 20260918T202620Z
```

### 102.1 Evidence verification (independently reproduced, not merely trusted)

```
r0082g_bargein_20260918T202620Z_mic_48k.wav
  sha256 = f9c89079b0ea28ab2fbaaf9a003f17f8f33d6757bf9d21b506c2ba613906cc73  EXACT MATCH
  wave.open(): channels=1 sampwidth=2 framerate=48000 nframes=701280 duration=14.610s

r0082g_bargein_20260918T202620Z_mic_16k.wav
  sha256 = 27747acf43e6b24a51fd30725682537694902cbd60a9e971e1c30cc8ee36ed4a  EXACT MATCH
  wave.open(): channels=1 sampwidth=2 framerate=16000 nframes=233760 duration=14.610s

r0082g_bargein_20260918T202620Z_timeline.csv
  457 lines = 1 header + 456 data rows -- matches vad_frames_processed=456 exactly
  header matches CSV_HEADER_BARGEIN exactly (14 columns, no drift)

r0082g_bargein_20260918T202620Z_milestones.json
  playback_start=268130.816465854  cue_start=268134.817540937
  speak_now=268137.820367925       playback_cancel_requested=268140.372561208
  playback_stopped=268140.378588482  last_speech_frame_submitted=268140.378583612
  latencies_s: speak_now_to_vad_start_s=2.2320349310175516
               speak_now_to_interrupt_confirmed_s=2.5521609490388073
               interrupt_confirmed_to_cancel_requested_s=3.233394818380475e-05
```

**Independent recomputation from the CSV directly (not merely the
script's own printed summary), all EXACT matches to the operator's
reported figures:**

- `remote_audio_samples_received=701280` == 48k WAV `nframes` exactly.
- `post_user_started_events=[12.256]`, `post_user_confirmed_events=[12.576]`
  — recomputed directly by scanning `vad_user_started_speaking_equivalent`
  and `interrupt_confirmed` columns for `1`: found exactly these two
  rows, nowhere else.
- `pre_user_false_positive=False` — recomputed directly: zero rows with
  `audio_relative_timestamp_s < 9.0` (the code's fixed
  `speak_now_boundary_t_s = PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S`) have
  `vad_user_started_speaking_equivalent=1` or `interrupt_confirmed=1`.
- `capture_ok=True` reproduced by hand from the run's own logic: since
  `pre_user_false_positive=False` and `post_user_confirmed=[12.576]`,
  `min_required_s = max(9.0, 12.576+1.0) = 13.576`; `capture_duration_s
  =14.610 >= 13.576` → `True`. Matches.
- `VERDICT=PASS` reproduced by hand: `post_user_started` non-empty,
  `post_user_confirmed` non-empty, `playback_stopped_early=True`,
  `playback_resumed_after_stop=False` (structural) → `PASS`. Matches.

**Classification: R0082-G deliberate barge-in #1 = VALID PASS.** This
is the FIRST of the eventual ≥5-run replication set required before any
broader claim; it does **not** by itself satisfy that requirement. It
demonstrates, for the first time on real hardware, that the full live
chain (`PlatformAudio` → remote subscription → `StreamingResampler` →
Silero → `InterruptionStateMachine`) correctly detects a genuine human
interruption, reaches `INTERRUPT_CONFIRMED`, and genuinely stops
playback — with zero false positives before the operator's own cue.

### 102.2 Offline acoustic-onset re-estimation (diagnostic only — does NOT alter the live verdict)

Method: short-time RMS envelope of the raw 48k mic WAV (20ms window,
5ms hop, no normalization, no modification of the evidence file),
cross-checked against the per-frame Silero probability already
recorded in the CSV (32ms native resolution). A ~7-second pre-`SPEAK
NOW` baseline (audio-relative 3.0s–9.9s, during which the deterministic
stimulus was itself actively "speaking" for most of that span) was used
to characterize the AEC-suppressed echo-residual noise floor: **mean
RMS=3.0, p95=3.9, max=60.7** (int16 units) — confirming AEC suppresses
the far-end echo to a low, stable floor even while the stimulus is
actively speaking, which is the baseline against which the onset below
is judged.

**Fine-grained scan (5ms hop) around the transition:**

```
t_s (audio-relative)   RMS
11.9100                 1.7
11.9150                 2.2   <- still at baseline
11.9200                14.7   <- rise begins
11.9250                24.1
11.9300                58.7
11.9350               222.3
11.9400               378.3
11.9450               941.6
11.9500              1663.8
```

Cross-check against the CSV's own Silero probability (32ms frames):
`t=11.904 prob=0.00204` (baseline) → `t=11.936 prob=0.57814` (sharp
rise, the frame spanning the RMS transition above) → `t=11.968
prob=0.92502` (clearly speech). The two independent signals (raw energy
and neural VAD probability) agree on the same ~30ms transition window.

**Onset estimate: audio-relative t ≈ 11.92s, uncertainty interval
[11.915s, 11.945s]** (~30ms wide, bounded by where RMS departs baseline
and where it is unambiguously elevated). The rise is abrupt (roughly
two orders of magnitude within ~20–30ms) — far too large and far too
fast to be attributable to AEC-suppressed playback echo, whose own
residual stayed at RMS 2–4 throughout the entire pre-`SPEAK-NOW`
baseline **including while the deterministic stimulus was itself
actively speaking** (confirmed: the stimulus's own content is actively
"speaking" during almost the entire window from `SPEAK NOW` through the
interruption point, per the RMS scan of the frozen stimulus in §95.3 —
7.925s–9.300s of the stimulus corresponds to playback-relative
≈8.86s at the mic onset's own wall-clock moment, i.e. the stimulus
WAS actively playing at the moment of onset, and its echo was STILL
suppressed to the same low floor elsewhere in this same run). This is a
strong, evidence-based argument that the onset is genuine near-end
(human) speech, not residual echo — stated as a defensible conclusion
with quantified uncertainty, not invented precision.

**Note on `SPEAK NOW`'s own timing:** the code's fixed classification
boundary is `speak_now_boundary_t_s = 9.0s` (audio-relative,
`PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S`). The ACTUAL `SPEAK NOW` event
(from `milestones["speak_now"]`, wall-clock) maps, via linear
interpolation of the CSV's own `timestamp_monotonic`↔
`audio_relative_timestamp_s` pairing, to audio-relative t ≈ 10.025s —
about 1.0s later than the idealized fixed boundary. This reflects real
mic-subscription/first-frame-gate startup latency baked into the
audio-relative clock's own t=0, not a defect: it does **not** affect
the correctness of `pre_user_false_positive=False`, since zero accepted
events occur anywhere before 9.0s, let alone before the later, more
precise 10.025s. All latency figures below use the ACTUAL wall-clock
`speak_now` milestone (10.025s-equivalent), not the idealized 9.0s
classification boundary.

### 102.3 Latency breakdown, by category

All times below are wall-clock (`timestamp_monotonic`), derived by
locally interpolating each event's audio-relative timestamp against its
nearest bracketing CSV rows' own `timestamp_monotonic` values (the same
method used to place `SPEAK NOW` itself, §102.2). Onset-derived figures
carry the [11.915s, 11.945s] uncertainty interval from §102.2; the
other three events (`VAD start`, `INTERRUPT_CONFIRMED`,
`playback_cancel_requested`) are exact CSV/milestone timestamps with no
onset-derived uncertainty.

```
SPEAK NOW -> acoustic onset            = human reaction latency
  ≈ 1.90s   (interval ≈ 1.89s - 1.92s)

acoustic onset -> accepted VAD start   = VAD/onset latency
  ≈ 0.33s   (interval ≈ 0.31s - 0.34s)
  (reflects VAD_START_FRAMES=6x32ms=192ms hysteresis PLUS the 400ms
  rolling-window volume tracker's own ramp-up time -- the CSV shows
  Silero probability crossing 0.7 well before smoothed volume crosses
  0.6, so volume smoothing, not probability, is the rate-limiting
  factor here)

accepted VAD start -> INTERRUPT_CONFIRMED = confirmation-hold latency
  = 0.320s   (EXACT: t=12.256 -> t=12.576, both direct CSV timestamps)
  (matches production confirm_hold_secs=0.3 plus one 32ms frame-
  granularity quantization step -- exactly as designed)

INTERRUPT_CONFIRMED -> playback_cancel_requested = playback cancellation latency
  = 0.000032s   (EXACT, from milestones.json -- same event-loop
  iteration; matches the design: the cancel event is set immediately
  upon observing chain.confirmed_events grow)

-- derived, combined figure --
acoustic onset -> INTERRUPT_CONFIRMED
  ≈ 0.65s   (interval ≈ 0.63s - 0.66s)
```

The `speak_now_to_vad_start_s=2.232s` and
`speak_now_to_interrupt_confirmed_s=2.552s` figures already reported by
the live script (and independently reproduced) are the SUM of human
reaction latency + VAD/onset latency (+ confirmation-hold latency for
the second one) — they were always documented as including human
reaction time (§100/§101), and this offline analysis now decomposes
that combined figure into its constituent parts for the first time on
real hardware data.

### 102.4 Pre-`SPEAK-NOW` diagnostic scan (diagnostic only — canonical result remains the accepted VAD/`InterruptionStateMachine` events)

```
max Silero probability before SPEAK NOW (t<9.0s)   = 0.3161
max smoothed volume before SPEAK NOW (t<9.0s)       = 0.42699
frames with probability >= 0.7 before SPEAK NOW     = 0 / 282
candidate/STARTING/SPEAKING-state frames before SPEAK NOW = 0 / 282
```

No suspicious near-threshold activity of any kind was found before
`SPEAK NOW`. Both the probability and volume ceilings stayed
comfortably below their respective thresholds (0.7 and 0.6) for the
entire ~7-second pre-cue window, consistent with `pre_user_false_positive
=False` and with the AEC-suppression baseline established in §102.2.

### 102.5 Playback shortening

```
samples submitted before cancellation = 507360 / 1112708 = 45.60%
submitted playback duration           = 507360 / 48000 = 10.570s
  (of the full 23.181s stimulus)
```

Observational note (not a defect, not requiring any code change):
wall-clock elapsed between `playback_start` and
`last_speech_frame_submitted` in `milestones.json` is
`268140.378583612 - 268130.816465854 = 9.562s`, about 1.0s less than
the sample-count-derived `10.570s`. This indicates `capture_frame()`'s
submission loop was not running in strict 1:1 real-time lockstep for
this run's playback segment (some internal buffering/burst delivery),
not that the sample count itself is wrong — the 507360-sample figure is
an exact count of frames actually handed to the track and is the
authoritative figure for "how much of the stimulus was submitted."

### 102.6 No discrepancy triggering the STOP-and-report protocol

Every reported figure (`remote_audio_frames_received`,
`remote_audio_samples_received`, `vad_frames_processed`,
`capture_duration_s`, `cue_emitted`, `capture_ok`,
`playback_stopped_early`, `pre_user_false_positive`,
`post_user_started_events`, `post_user_confirmed_events`, all three
milestone latencies, and `VERDICT=PASS`) was independently reproduced
from the evidence files and matched exactly. No evidence file was
malformed. No code was changed as part of this analysis.

**Verdict: R0082-G real hardware run #1 = VALID PASS, independently
re-verified in full, with a defensible (uncertainty-bounded) acoustic-
onset estimate and a clean pre-`SPEAK-NOW` diagnostic scan.** This is
one run of the eventual ≥5-run replication set, not the replication
set itself. This session did not execute any further hardware.

## 103. Two evidence-backed harness corrections, found by run #1's own offline analysis (no hardware run)

Run #1's own offline analysis (§102) surfaced two harness issues that
must be closed before replication runs #2–#5. **Neither issue
invalidates run #1's PASS classification for live human VAD detection /
no pre-user accepted self-echo** — both are addressed below without
touching the verdict that made run #1 a PASS.

### 103.1 Correction #4 — canonical boundary must be the ACTUAL emitted `SPEAK NOW`, not the idealized/scheduled one

**Defect.** §102.2 found the idealized/scheduled
`speak_now_boundary_t_s = PRE_ROLL_S + BARGEIN_SPEAK_NOW_TIME_S` (9.0s,
audio-relative) lagged the ACTUAL emitted `SPEAK NOW`
(`milestones["speak_now"]`, wall-clock) by roughly 1.0s in run #1's own
audio-relative timeline (~10.025s). The operator cannot possibly speak
before the cue has ACTUALLY been emitted — a false event landing
between the scheduled 9.0s and the real cue would have been wrongly
treated as "post-cue" (potentially reaching `PASS`) under the old
boundary. This was no longer scientifically sufficient.

**Fix.** `evaluate_deliberate_bargein_result()` now classifies using
`started_events_mono`/`confirmed_events_mono` (wall-clock, already
recorded by `LiveVadChain`) directly against `speak_now_mono`
(`milestones["speak_now"]`) — never the idealized audio-relative value:

```python
pre_user_started   = [t for t, tm in started_pairs   if _is_pre(tm, speak_now_mono)]
post_user_started   = [t for t, tm in started_pairs   if not _is_pre(tm, speak_now_mono)]
pre_user_confirmed = [t for t, tm in confirmed_pairs if _is_pre(tm, speak_now_mono)]
post_user_confirmed = [t for t, tm in confirmed_pairs if not _is_pre(tm, speak_now_mono)]
```

where `_is_pre(tm, boundary) = boundary is None or tm < boundary` — if
`speak_now_mono is None` (the cue genuinely never fired, e.g. a
pre-`SPEAK-NOW` false positive cancelled it before it could), EVERY
accepted event is treated as pre-user by definition: there is no
"after `SPEAK NOW`" window if `SPEAK NOW` never happened. The idealized
`speak_now_boundary_t_s`/`cue_start_boundary_t_s` values are **not
silently mixed into this comparison** — they remain parameters used
ONLY for (a) `capture_ok`'s minimum-duration floor heuristics
(audio-relative domain, orthogonal to the false-positive semantic
split) and (b) diagnostic passthrough of the scheduled-vs-actual gap.
The returned `pre_user_*`/`post_user_*` lists still report
AUDIO-RELATIVE timestamps (for CSV/report continuity) even though the
SELECTION is made using monotonic values. A parallel `cue_start_mono`
(`milestones.get("cue_start")`) drives the `pre_countdown_*`/
`during_countdown_*` diagnostic sub-split the same way.

**Offline regression added** (models run #1's own finding exactly):
scheduled `SPEAK NOW`-equivalent = 9.0s, actual emitted `SPEAK NOW` =
10.0s, false accepted start at 9.5s (after the scheduled boundary, but
before the real cue), later genuine barge-in after 10.0s succeeds →
verdict is `FAIL-E`, not `PASS` — proven correct offline; a complementary
test confirms a genuine event strictly after the delayed actual cue
still `PASS`es cleanly.

**Run #1 reproduction (does not invalidate the original PASS).** Using
run #1's own exact recorded values (`speak_now_mono=268137.820367925`,
accepted VAD start at audio-relative 12.256 /
`mono=268140.052419`, confirmed interruption at audio-relative 12.576 /
`mono=268140.372530`), the corrected classifier was run directly:
`pre_user_false_positive=False`, `VERDICT=PASS` — unchanged. Run #1's
only accepted start/confirm occurred well after BOTH the scheduled
(9.0s) and the actual (~10.025s) cue boundaries, so this correction
does not and could not have changed its classification.

**Corrected diagnostic for run #1** (recomputed against the ACTUAL cue
boundary per instruction, not merely `t<9.0s`):

```
                                idealized (t<9.0s)   ACTUAL (t<speak_now_mono)
pre-cue row count               282                   314
max Silero probability          0.3161                0.3161
max smoothed volume             0.42699               0.42699
frames with probability>=0.7    0 / 282                0 / 314
candidate/STARTING/SPEAKING     0 / 282                0 / 314
```

The wider, ACTUAL-boundary window (314 rows vs. 282) adds 32 more
frames of observation but finds no new peak and no new suspicious
activity — both windows report the exact same maxima. The corrected
diagnostic conclusion is identical to the original: **no suspicious
near-threshold activity before the operator's cue, under either
boundary.**

### 103.2 Correction #5 — `AudioSource.clear_queue()` now invoked on confirmed interruption

**Defect.** §102.5 found ~1.008s of audio submitted ahead of wall-clock
during run #1's playback segment. Stopping the Python `capture_frame()`
submission loop on `INTERRUPT_CONFIRMED` proves FUTURE audio stops
being submitted; it does not by itself prove already-buffered audio is
discarded from the actual playout path.

**Audit of the exact `AudioSource` construction** (per instruction, no
assumption): `signal_source = rtc.AudioSource(LIVE_SAMPLE_RATE, 1)` in
both `run_speech_role` (silent-user) and
`run_speech_role_deliberate_bargein`. The installed API
(`livekit==1.1.19`, audited directly via `inspect.signature`):

```
rtc.AudioSource.__init__(self, sample_rate, num_channels,
                          queue_size_ms: int = 1000, loop=None) -> None
```

The harness passes only `(LIVE_SAMPLE_RATE, 1)` — i.e. the **DEFAULT
`queue_size_ms=1000`** (1000ms), exactly matching the ~1.0s observed
gap. `AudioSource.clear_queue()` (synchronous, confirmed via
`inspect.iscoroutinefunction` → `False`) and `AudioSource.queued_duration`
(a property returning seconds of buffered audio) are both confirmed
present in the installed runtime — the required APIs exist; this is not
a STOP-and-report case.

**Fix.** `_play_signal_cancelable()` (deliberate-bargein mode only,
silent-user untouched) now does this the INSTANT `cancel_event` is
observed set, before anything else (including cancelling the cue task):

```python
if cancel_event.is_set():
    stopped_early = True
    queued_before_s = signal_source.queued_duration
    milestones["audio_source_queued_before_clear_s"] = queued_before_s
    print(f"... AUDIO_SOURCE_QUEUED_BEFORE_CLEAR = {queued_before_s:.4f}s")
    signal_source.clear_queue()
    print("... AUDIO_SOURCE_QUEUE_CLEARED")
    queued_after_s = signal_source.queued_duration
    milestones["audio_source_queued_after_clear_s"] = queued_after_s
    print(f"... AUDIO_SOURCE_QUEUED_AFTER_CLEAR = {queued_after_s:.4f}s")
    break
```

Log ordering on a real confirmed interruption is now: `PLAYBACK_CANCEL_
REQUESTED` (unchanged, from `_consume_mic`) → `AUDIO_SOURCE_QUEUED_
BEFORE_CLEAR` → `AUDIO_SOURCE_QUEUE_CLEARED` → `AUDIO_SOURCE_QUEUED_
AFTER_CLEAR` → `PLAYBACK_STOPPED` (unchanged, from the caller). No new
frames can be submitted after this point (the loop has `break`-ed) and
playback cannot resume (unchanged, structural, no retry code path
exists).

**`queue_size_ms` was deliberately NOT changed.** Per instruction:
prove `clear_queue()` works correctly first, preserve stable real-time
playback, and only change queue size if evidence shows it is needed.
`clear_queue()` alone already satisfies the actual requirement
(confirmed interruption → stop future submission → discard queued
publisher audio) without the risk shrinking `queue_size_ms` would carry
for the NORMAL (non-cancelled) playback path's pacing. This was not an
arbitrary queue-size change — it remains at its default.

**What this fix does and does NOT prove.** It closes the gap between
"future submission stopped" and "buffered publisher-side audio
discarded" — both now verifiably true, with `queued_duration` recorded
immediately before and after `clear_queue()`. **It does NOT by itself
prove the physical speaker fell silent at that instant.** That would
additionally depend on downstream WebRTC/Opus encode, network
transport, and the far-end device's own playout buffer — none of which
this research harness observes or controls. Accordingly:

```
R0082-G run #1:
  VALID PASS for live human detection / no pre-user accepted self-echo.

Prompt physical/acoustic stop:
  NOT YET FULLY PROVEN -- ~1.008s of publisher audio was submitted
  ahead of wall-clock in run #1 (queue_size_ms=1000, default, at the
  time of that run). The VAD/barge-in PASS is NOT downgraded by this;
  stopping future audio submission is not claimed to be identical to
  immediate physical speaker silence.
```

**Offline validation added** (`FakeSignalSource` extended with
`clear_queue()`/`queued_duration`, modeling the confirmed installed
API): queue clear invoked exactly once on cancellation even with
nothing queued (`queued_before=queued_after=0.0`); queue clear NEVER
invoked on natural (non-cancelled) completion; mid-stream cancellation
shows `queued_before > 0` (real frames were queued) and
`queued_after == 0.0` (genuinely discarded); no `capture_frame()` calls
occur after the clear; the four new milestones
(`audio_source_queued_before_clear_mono`, `audio_source_queue_cleared_
mono`, `audio_source_queued_after_clear_mono`, `playback_stopped`) are
monotonically ordered. A structural check confirms the `cancel_event`
check remains the FIRST statement inside the submission loop (detection
itself is not delayed by the new queue-clearing code).

### 103.3 Offline validation performed this round (all before any hardware)

```
py_compile                                                          PASS
ruff                                                                 PASS
git diff --check                                                     PASS
production VAD constants unchanged (VAD_CONFIDENCE/START_SECS/
  STOP_SECS/MIN_VOLUME grep-verified byte-identical)                 PASS
silent-user run_speech_role body byte-for-byte identical to the
  last commit (10405 chars, programmatic diff)                       PASS
DELAYED CUE regression (scheduled 9.0s / actual 10.0s / false event
  at 9.5s / genuine barge-in after 10.0s) -> FAIL-E                   PASS (3/3 sub-checks)
DELAYED CUE complement (genuine event after the actual delayed
  cue) -> PASS                                                        PASS
RUN #1 REPRODUCTION using its own exact recorded mono timestamps
  -> pre_user_false_positive=False, VERDICT=PASS (unchanged)          PASS (2/2)
Existing CASE 1-5 / TEST 1-6 (all prior FAIL-E/FAIL-F corrections)
  re-verified correct under the new mono-based classifier             PASS (all)
AudioSource queue-clear: invoked exactly once on cancellation
  even with nothing queued                                            PASS (4/4)
AudioSource queue-clear: NOT invoked on natural completion             PASS (2/2)
AudioSource queue-clear: mid-stream cancel -- queued_before>0,
  queued_after=0.0, no frames submitted after clear                   PASS (4/4)
AudioSource queue-clear: milestone ordering monotonic                  PASS
Structural: cancel_event check remains first statement in the
  submission loop (detection not delayed by queue-clearing)            PASS
Positive/negative VAD control regression                               PASS (5/5, unaffected)
LiveVadChain.process_frame() CSV backward-compatibility regression      PASS (4/4, unaffected)
_play_signal_cancelable() cancellation/no-resume regression             PASS (10/10, unaffected)
```

86 checks total, all PASS.

**Verdict: both corrections CONFIRMED correct offline.** Run #1 remains
`VALID PASS` for live human VAD/barge-in detection with no pre-user
accepted self-echo event; the prompt physical/acoustic stop claim
remains explicitly `NOT YET FULLY PROVEN` pending `clear_queue()`
verification on real hardware. No production files were touched. This
session did not execute any hardware. **R0082-G remains READY for
deliberate barge-in runs #2–#5**, now with both the corrected
mono-based classification boundary and the `AudioSource` queue-clear
fix in place.

## 104. R0082-G deliberate barge-in replication set — runs #2–#5 (VALID PASS, independently re-verified)

The operator executed four further real hardware deliberate-barge-in
runs, completing the ≥5-run replication set (`r0082g_bargein_001`
through `r0082g_bargein_005`, this session's own run #1 =
`r0082g_bargein_001` / `run_id=20260918T202620Z`, §102). This session
did not execute runs #2–#5; every figure below was independently
reproduced from the evidence files, not merely trusted from the
console transcript.

### 104.1 Evidence verification (independently reproduced)

```
run_id            SHA256 48k                                              SHA256 16k                                              match
20260918T205742Z  5e90408a2bc272400d5ea65a43151f97836878493abe6e442557d... 9442a75da29ae7f49ee1e39750bfdf42bd3e0adacd3751fefb4f...  EXACT
20260918T210003Z  77079238c8c4adb41582ac406a3818bbd308adc1d30f667337c3... fd71995bf7697c7d6e98c921eaba24a9c960771fe074839f286...  EXACT
20260918T210125Z  52871b0111f40a7705c7e667474b1a044c1f74fb4c77127175a0... b5433d691d00b5c7e005ad4954672dedcc4e0f7cb985323e6b9...  EXACT
20260918T210253Z  3bda27d66c8c6e0c19022c59a2697ddf0a187f245980525a79f7... b03e3a48b7b98f28ccb96d6f9aced1ca5a72f5211fe0b5d78ed...  EXACT
```

All 8 hashes recomputed via `sha256sum` on the actual files in
`r0082f_live_captures/` — exact matches, byte-for-byte.

**WAV properties and CSV row counts, independently inspected.**
Corrected per §109.2 (the original table here conflated a
DictReader-based data-row count with a raw physical-line count and
subtracted 1 a second time, fabricating a nonexistent extra
frame — a reporting error, not a harness defect; see §109.2 for the
full root-cause investigation):

```
run_id            48k nframes/dur      16k nframes/dur      wc -l (physical)   DictReader data rows = chain.n_frames
20260918T205742Z  750720 / 15.640s     250240 / 15.640s     489 (1 header)     488
20260918T210003Z  684480 / 14.260s     228160 / 14.260s     446 (1 header)     445
20260918T210125Z  720000 / 15.000s     240000 / 15.000s     469 (1 header)     468
20260918T210253Z  701280 / 14.610s     233760 / 14.610s     457 (1 header)     456
```

(all channels=1, sampwidth=2, framerate matches 48000/16000 as
expected — no malformed files, no discrepancy found in the underlying
evidence itself.)

**Accepted VAD start / confirmed interruption timestamps, recomputed
directly from each CSV** (scanning `vad_user_started_speaking_equivalent`
and `interrupt_confirmed` columns), matching the reported figures
exactly:

```
run_id            post_user_started   post_user_confirmed
20260918T205742Z  [13.280]            [13.600]
20260918T210003Z  [11.904]            [12.224]
20260918T210125Z  [12.640]            [12.960]
20260918T210253Z  [12.256]            [12.576]
```

**`pre_user_false_positive`, independently recomputed** by scanning
every CSV row for an accepted start/confirm with `timestamp_monotonic
< milestones["speak_now"]` (the corrected, canonical, actual-cue
boundary from §103.1 — not the idealized 9.0s): **`False` for all four
runs**, zero exceptions, zero pre-cue accepted events of any kind.

**`capture_ok`/`VERDICT`, reproduced by hand** using each run's own
`capture_duration_s` (from WAV `nframes`), confirmed post-confirmed
timestamp, and `min_required_s = post_user_confirmed[0] +
POST_INTERRUPT_MARGIN_S`:

```
run_id            capture_duration_s   min_required_s   capture_ok   VERDICT
20260918T205742Z  15.640               14.600            True         PASS
20260918T210003Z  14.260               13.224            True         PASS
20260918T210125Z  15.000               13.960            True         PASS
20260918T210253Z  14.610               13.576            True         PASS
```

**Queue-clear verification (`AudioSource.clear_queue()`), independently
recomputed from each run's `milestones.json`:**

```
run_id            queued_before_clear_s   queued_after_clear_s   queue clear AFTER cancel_requested?
20260918T205742Z  1.0091232939739712      0.0                    YES (+6.591ms)
20260918T210003Z  1.00355409597978        0.0                    YES (+6.521ms)
20260918T210125Z  1.0060435750056058      0.0                    YES (+6.690ms)
20260918T210253Z  1.000091205991339       0.0                    YES (+6.793ms)
```

`queued_before_clear > 0` confirmed for all four (real audio genuinely
had accumulated in the `AudioSource`'s own internal queue at the moment
of cancellation — averaging just over 1.0s, consistent with the
default `queue_size_ms=1000` being nearly saturated at steady-state
playback); `queued_after_clear == 0.0` confirmed for all four (the
queue was genuinely, completely discarded). Milestone ordering was
independently recomputed for **all five runs including #1** and found
monotonic and causally sensible in every case:
`playback_cancel_requested < last_speech_frame_submitted <
audio_source_queued_before_clear_mono < audio_source_queue_cleared_mono
< audio_source_queued_after_clear_mono < playback_stopped` — with
exactly ONE additional ~10ms frame slipping through between
`playback_cancel_requested` and `last_speech_frame_submitted` in every
single run (expected: the cancel event is only checked once per loop
iteration, and it was set by a concurrent task, so at most one frame in
flight can complete before the check catches it — not a defect).

**"No further submission after clear" / "no resume":** confirmed
structurally for all four runs — `samples_submitted` in each run's own
log is strictly less than the full 1,112,708-sample stimulus
(`playback_stopped_early=True` in every case), and the harness's own
`_play_signal_cancelable()` has no code path that re-enters the
submission loop after `break` (verified offline, §103.3, unchanged this
round).

### 104.2 Aggregate table — deliberate human barge-in, runs #1–#5

```
run   pre_user_fp   VAD start   CONFIRMED   VAD->confirm   confirm->cancel   queued_before   queued_after   verdict
#1    False         12.256s     12.576s     0.320111s      0.0000323s        n/a (pre-fix)   n/a (pre-fix)  PASS
#2    False         13.280s     13.600s     0.320035s      0.0000239s        1.0091s         0.0000s        PASS
#3    False         11.904s     12.224s     0.320096s      0.0000295s        1.0036s         0.0000s        PASS
#4    False         12.640s     12.960s     0.319963s      0.0000285s        1.0060s         0.0000s        PASS
#5    False         12.256s     12.576s     0.319967s      0.0000312s        1.0001s         0.0000s        PASS
```

**Aggregate: 5/5 deliberate human barge-in detection/confirmation
PASS. 0/5 pre-user accepted false positives.** VAD-start→confirmed
latency is remarkably consistent across all five runs (0.31996s–
0.32011s, a ~150µs spread) — matches production `confirm_hold_secs
=0.3` plus one 32ms frame-granularity quantization step, exactly as
designed, on every single run. confirmed→cancel-requested latency is
consistently in the 24–33µs range (same event-loop iteration in every
case). This aggregate does **not** by itself establish the physical/
acoustic prompt-stop claim — see §105.

## 105. Acoustic-stop analysis (Task 3) — methodology and result: NOT MEASURABLE FROM MIC EVIDENCE

**Question.** Does the far-end NeXa speech actually disappear
acoustically after `playback_cancel_requested`/
`audio_source_queue_cleared`, as distinct from merely "future
submission stopped"? The mic capture contains BOTH genuine near-end
operator speech AND residual/echo from NeXa playback simultaneously at
the moment of interest — raw total RMS after cancellation is dominated
by the operator's own voice (peaking in the thousands of RMS units per
run #1's own onset analysis, §102.2) and cannot isolate a much weaker
echo component, so it was deliberately NOT used as the primary method.

**Method attempted.** The frozen source WAV
(`r0082d_speech_en_pl_v1.wav`, sha256
`703db210bcef8222adf044e88594958289be8d0e6ff4798ad8245b0c1076c21d`,
re-verified this round) is known exactly. For each run:

1. **Delay estimation** from the pre-`SPEAK-NOW`, human-silent, actively-
   playing window (audio-relative [4.0s, cue_start−0.2s], chosen because
   audio-relative t=4.0s maps to playback-relative ≈0.9–1.0s across all
   five runs — safely after real playback begins, confirmed by direct
   inspection). Raw-sample Pearson cross-correlation between the mic
   excerpt and the correspondingly-shifted source excerpt was computed
   over a candidate delay range of −50ms to +600ms (the mapping from
   audio-relative time to playback-relative time, and thus to the
   expected source sample index, uses the same
   `timestamp_monotonic`↔`audio_relative_timestamp_s` CSV interpolation
   established in §102.2).
2. **Null/control baseline**: the SAME mic excerpt correlated against
   source excerpts at physically implausible delays (2.0–2.8s out — far
   beyond any real acoustic/network/buffer path for this setup) to
   establish a chance-level correlation distribution.
3. **Significance test**: the in-range peak is judged "significant" only
   if it exceeds 3× the null distribution's 95th percentile AND 2σ+mean
   of the null distribution.
4. **Temporal-consistency check**: for any run whose peak passed the
   significance test, a sliding 0.5s-window correlation (at the fixed
   best-delay found in step 1) was computed across the whole pre-cue
   region, to check whether the correlation stays consistently elevated
   and same-signed (expected of a genuine, stable echo path) or merely
   spikes once (consistent with a chance artifact).
5. **Envelope-based cross-correlation** (20ms-window short-time RMS
   envelope of both signals, correlated the same way) was also computed
   as a more AEC-nonlinearity-robust alternative, since AEC's residual
   echo suppression can scramble fine sample-level phase while
   potentially preserving coarser energy-envelope structure.

*(One implementation defect was caught and fixed during this analysis,
before drawing any conclusion: the delay-search loop's "best correlation"
tracker was initially seeded at `-1.0`, making `abs(best) = 1.0`
unbeatable by any real correlation coefficient (bounded in [-1, 1]) —
the exact same class of bug previously found and fixed in R0082-D's own
`find_lag_and_corr()`. Fixed by seeding at `0.0` before any results were
interpreted; this is a fix to a throwaway analysis script, not to the
committed harness, and did not affect any verdict.)*

**Results:**

```
run_id            raw-sample peak |r|   null p95   significant?   envelope peak |r|   envelope null p95   significant?
20260918T202620Z  0.0544 (@510ms)       0.0161      True*          0.1855 (@435ms)      0.2346               False
20260918T205742Z  0.0368 (@218ms)       0.0245      False          0.1923 (@320ms)      0.1999               False
20260918T210003Z  0.1203 (@496ms)       0.0268      True*          0.2807 (@145ms)      0.2112               False
20260918T210125Z  0.0352 (@166ms)       0.0212      False          0.2140 (@460ms)      0.2849               False
20260918T210253Z  0.0352 (@42ms)        0.0212      False          0.3193 (@505ms)      0.2189               False
```

`*` = nominally passed the significance test on the raw-sample method
alone, BUT the temporal-consistency check (step 4) invalidates both:
sliding 0.5s-window correlation at the run's own best-delay, computed
across the entire pre-cue region, shows the correlation sign FLIPPING
repeatedly and its magnitude fluctuating between roughly ±0.06 (run
`20260918T202620Z`) or spiking once to +0.198 in a single 0.5s window
against an otherwise-flat ~0.01–0.03 background (run
`20260918T210003Z`) — neither pattern is consistent with a stable,
physically real, fixed-delay echo path; both are consistent with
chance. The estimated "best delays" also do not cluster around a common
value across runs (42ms–510ms, no consistent physical delay), which a
genuine, repeatable acoustic/system echo path would be expected to
show.

**Conclusion: NOT MEASURABLE FROM MIC EVIDENCE**, for all five runs
(including run #1). This is a rigorous negative result, not a
shortcut — two independent, defensible cross-correlation methods (raw-
sample and envelope-based), each checked against an explicit null
baseline for statistical significance, and a temporal-consistency
check that specifically debunked the two nominally-significant raw-
sample results, all converge on the same conclusion: this harness's AEC
suppression (`echo_cancellation=True`, WebRTC AEC on `PlatformAudio`)
is strong enough that any residual far-end echo correlated with the
known source is, at best, indistinguishable from measurement noise in
this mic evidence. This is consistent with every prior R0082 round's
own finding that AEC-suppressed residual echo sits at RMS 2–4 (near the
noise floor) even while the far-end stimulus is actively, loudly
"speaking" (§102.2's own baseline). **Estimated acoustic-stop latency:
NOT MEASURABLE for any of the five runs. Whether source-correlated
playback resumes later: also NOT MEASURABLE** (a method that cannot
reliably detect the PRESENCE of source-correlated content, even in a
window where it is known for certain to be present, cannot reliably
detect its absence or reappearance either).

## 106. Run #1 nuance (Task 4)

Run #1 (`r0082g_bargein_001`, `run_id=20260918T202620Z`) occurred
**before** `AudioSource.clear_queue()` was added to the harness (§103.2
was implemented and committed after run #1). Its evidence therefore has
no `audio_source_queued_before_clear_s`/`audio_source_queued_after_
clear_s`/`audio_source_queue_cleared_mono` milestones — this is
expected, not a defect, and is not retroactively claimed. Kept exactly
as originally classified:

```
run #1 = VALID PASS for:
  - no pre-user accepted self-echo (pre_user_false_positive=False,
    independently re-confirmed this round using the corrected
    ACTUAL-mono-SPEAK-NOW boundary, §103.1/§104.1)
  - human VAD detection (accepted start at t=12.256s)
  - interruption confirmation (INTERRUPT_CONFIRMED at t=12.576s)
  - future submission cancellation (playback_stopped_early=True,
    507360/1112708 samples submitted, §102.5)
```

**Publisher-side queued-audio discard is NOT claimed for run #1** — the
mechanism did not exist yet at the time of that run. Run #1's own mic
evidence was included in the §105 acoustic-stop analysis (it is run
`20260918T202620Z` in that table) using the identical methodology
applied to runs #2–#5: **NOT MEASURABLE FROM MIC EVIDENCE**, same
conclusion, no separate/different treatment warranted or found.

## 107. Decision (Task 5) — four separate classifications, not merged

```
A. Deliberate human detection/confirmation:
   PASS
   (5/5 real hardware runs: accepted VAD start -> INTERRUPT_CONFIRMED,
   0/5 pre-user accepted false positives, VAD-start->confirmed latency
   consistent with production confirm_hold_secs=0.3 on every run)

B. Publisher-side queued audio discard (runs #2-#5):
   PASS
   (4/4 runs: queued_before_clear > 0 in every case -- confirming real
   audio was genuinely queued -- and queued_after_clear == 0.0 in
   every case -- confirming it was genuinely discarded. Exact supported
   sequence, corrected per §109.1: one in-flight frame may complete
   AFTER PLAYBACK_CANCEL_REQUESTED (the loop only checks the cancel
   event once per iteration); clear_queue() then discards the publisher
   queue; NO frame submission occurs AFTER clear_queue(); playback
   submission never resumes -- in every case)

C. Physical/acoustic prompt stop:
   NOT PROVEN
   (two independent, null-baseline-tested cross-correlation methods,
   applied to all 5 runs including #1, found no statistically reliable,
   temporally-consistent source-correlated signal in the mic evidence
   at all -- not even in the pre-cue window where echo is known for
   certain to be present. This is a genuine measurement limitation of
   this harness's AEC-suppressed mic evidence, not a negative finding
   about whether the speaker actually stops -- it is simply NOT
   MEASURABLE from the evidence collected. "Future submission stopped"
   and "publisher queue discarded" (both B, PASS) are NOT the same
   claim as "the physical speaker fell silent at a known instant," and
   this report does not conflate them.)

D. Playback resume after interruption:
   NOT OBSERVED (software/structural) / NOT MEASURABLE (acoustic)
   (software-level: zero evidence of any resumed frame submission in
   any of the 5 runs -- capture_frame() call counts and submitted-
   sample totals are all consistent with a single, permanent stop, and
   the harness has no code path that re-enters the submission loop;
   acoustic-level: cannot be assessed at all, for the same reason as C
   -- if source-correlated content cannot be reliably detected even
   when present, its reappearance cannot be reliably ruled out or in)
```

**The evidence supports A and B fully. It does not yet support C.**
What remains missing for C: a measurement method with better
sensitivity than mic-based cross-correlation against a AEC-suppressed
residual this weak — e.g. a dedicated reference microphone positioned
to capture the speaker's ACOUSTIC output directly (bypassing the
hardware's own AEC processing entirely), or an electrical tap on the
speaker's own drive signal, neither of which this research harness
currently has. This is stated as a genuine open gap, not glossed over.

## 108. Static checks (Task 6)

```
git diff --check                                                     PASS
```

No code changes were made or needed this round — all analysis was
read-only inspection of existing evidence files plus one throwaway
analysis script (not part of the committed harness). Report-only
commit.

## 109. Two evidence/report corrections (found by operator review, no hardware run)

Both corrections below are to REPORT TEXT ONLY. **R0082-G's results are
unchanged and not downgraded**: deliberate human barge-in detection/
confirmation remains 5/5 PASS, pre-user false positives remain 0/5,
publisher `AudioSource` queue discard on runs #2–#5 remains 4/4 PASS,
and the physical/acoustic speaker-stop claim remains exactly what §105
already said: NOT PROVEN / NOT MEASURABLE from the current mic
evidence.

### 109.1 A1 — queue-clear wording was technically inaccurate

**Defect.** §107's classification B previously stated `clear_queue()
invoked strictly after playback_cancel_requested, before any further
frame submission`. This is imprecise: §104.1 itself already establishes
(and this round re-confirms, §109 below) that exactly ONE in-flight
~10ms frame completes submission AFTER `PLAYBACK_CANCEL_REQUESTED` and
BEFORE `clear_queue()` is called, in every single run (all 5,
including #1) — the cancel event is only checked once per loop
iteration, and it was set by a concurrent task, so one frame already
"in flight" through that iteration's remaining code can still complete.
"Before any further frame submission" is therefore not the exact
supported claim.

**Fix.** §107 classification B now reads:

```
one in-flight frame may complete after PLAYBACK_CANCEL_REQUESTED
    (the loop only checks the cancel event once per iteration);
clear_queue() then discards the publisher queue;
NO frame submission occurs AFTER clear_queue();
playback submission never resumes.
```

This does not change the underlying result: `queued_before_clear > 0`
and `queued_after_clear == 0.0` in every one of the 4 measured runs are
unaffected, `clear_queue()` still demonstrably discards the queue, and
classification B remains `PASS`. Only the WORDING around exactly when
in the sequence the last frame was submitted has been corrected to
match what the evidence actually supports.

### 109.2 A2 — VAD frame-count "off-by-one" was a report transcription error, not a harness defect, and no evidence was lost

**Investigation, per instruction (not normalized away).** Directly
recomputed, this round, for **all five runs including #1**:

```
run_id            wc -l (physical lines)   csv.reader rows (incl header)   DictReader data rows (excl header)   console "N VAD frames"
20260918T202620Z  457                      457                              456                                   456 (§102, reported correctly)
20260918T205742Z  489                      489                              488                                   488
20260918T210003Z  446                      446                              445                                   445
20260918T210125Z  469                      469                              468                                   468
20260918T210253Z  457                      457                              456                                   456
```

**Root cause, found exactly.** `LiveVadChain.process_frame()` (the
committed harness, unchanged) increments `self.n_frames` and writes
exactly one CSV row via `self.csv_writer.writerow(row)` in the SAME
call, unconditionally, every time it is invoked — there is no code path
where a frame is counted without a row being written, or a row is
written without the counter incrementing. `chain.n_frames`
(the figure printed to console as `"({chain.n_frames} VAD frames)"`)
is therefore, by construction, EXACTLY equal to the number of CSV data
rows (excluding the header) — confirmed directly above: `488==488`,
`445==445`, `468==468`, `456==456`, with zero exceptions across all
five runs. **There is no missing frame and no accepted-start/
confirmed-interruption evidence omitted from persisted evidence by any
off-by-one** — every processed VAD frame has exactly one corresponding
CSV row, for every run.

The apparent discrepancy in §104.1's ORIGINAL table (`"488 (487 VAD
frames)"` etc.) was a **reporting/transcription error made while
writing that table**, not a defect in the harness or a real
inconsistency in the evidence: an earlier `csv.DictReader`-based
verification query (from the prior round) had already printed a
data-row count that EXCLUDES the header (e.g. `488`) — but when
transcribing that same figure into §104.1's table, it was mistakenly
treated as if it still included the header, and a second `-1` was
applied on top, fabricating a nonexistent `487`. Run #1's own §102.1
section (written in an earlier round) computed and stated the correct
relationship at the time (`"457 lines = 1 header + 456 data rows --
matches vad_frames_processed=456 exactly"`) — only the later §104.1
table for runs #2–#5 introduced the error. This error was **confined to
that one display table**; it did not propagate into any other
computation in the report (`pre_user_false_positive`, `capture_ok`,
`VERDICT`, the latency figures, and the queue-clear figures were all
independently computed directly from the CSV/milestones content in
other steps, not from this row-count figure, and are unaffected).

**Classification: harmless reporting error, corrected in place
(§104.1's table above). No evidence format change is needed or
warranted before R0082-H** — the underlying CSV-writing logic in
`LiveVadChain.process_frame()` was never in question and remains
byte-for-byte unchanged (verified this round: `git status` shows no
change to `r0082f_live_vad_self_echo_poc.py`).

### 109.3 Static check

```
git diff --check                                                     PASS
```

Report-only correction. No code changed.

## 110. R0082-H — continuous silent-user replication — design (no hardware run)

**Goal.** R0082-F/G tested one cold-start session per run. The original
self-echo concern also included time/stream-age instability — restarting
`PlatformAudio`/WebRTC/AEC/VAD before every response would repeatedly
reset exactly the state that concern is about. R0082-H tests **one
continuous session across 10 sequential deterministic bot-speech
episodes**, with the operator completely silent throughout, to expose
classes of failure a cold-start-per-response design would hide: AEC
adaptation over several minutes, clock/drift accumulation, repeated
render start/stop transitions, residual-state carryover, false VAD
after many responses, false triggers during silent gaps, and
late-session deterioration.

**Terminology, stated explicitly per instruction:** R0082-H is **10
deterministic continuous bot-speech episodes** (the same frozen
`r0082d_speech_en_pl_v1.wav` replayed 10 times) — it is **NOT** 10
genuine Gemini/production conversational responses. It validates the
audio/AEC/VAD layer under repeated continuous speech. The final
end-to-end "normal bot responses" acceptance must still be executed
separately, after the chosen `PlatformAudio`/LiveKit path is integrated
into the actual NeXa conversation stack. These two evidence levels are
not blurred.

### 110.1 Production audit — what actually resets between ordinary responses (not assumed)

Read directly from `src/nexa/voice/interruption.py` (production,
unmodified, read-only) before designing anything:

- **`InterruptionStateMachine.reset()`** (line 247): docstring reads
  *"Pipeline stop / error — never leave a latched state."* — **NOT**
  called anywhere in the normal per-response flow.
- **Normal per-response lifecycle** is `notify_response_dispatched()`
  (`IDLE`/`INTERRUPTING` → `RESPONDING`, allocates a new monotonic
  response id) at the start of a reply, and `notify_response_finished()`
  (`RESPONDING`/`INTERRUPT_CANDIDATE` → `IDLE`) at normal completion.
  `notify_response_finished()`'s own docstring: *"Not from
  `INTERRUPTING`: ... it must be a no-op then so capture is not cut
  short"* — i.e. it is safe to call unconditionally at the end of every
  episode, interrupted or not, exactly matching the real bridge's own
  documented pattern.
- **Silero model reset IS production behavior, but is time-based, not
  turn-based**: `pipecat`'s own `SileroVADAnalyzer.voice_confidence()`
  (`.venv/lib/python3.13/site-packages/pipecat/audio/vad/silero.py:214-220`)
  resets the ONNX model's internal state every `_MODEL_RESET_STATES_TIME
  = 5.0` (`silero.py:23`) seconds of wall-clock time
  (`time.time()`-based), **regardless of response/turn boundaries** —
  confirmed to match this harness's own existing `MODEL_RESET_INTERVAL_S
  = 5.0` in `LiveVadChain.process_frame()` (unchanged this round; the
  harness uses audio-sample-derived time rather than `time.time()`, a
  pre-existing, previously-audited simplification, not altered here).
  This reset needs no special per-episode handling — it is already
  continuous and will keep firing correctly across the whole R0082-H
  session exactly as it already does in every other mode.

**Design conclusion, evidence-based:** `InterruptionStateMachine` is
constructed ONCE and never `.reset()` between episodes; each episode
calls `notify_response_dispatched()`/`notify_response_finished()` —
the SAME two calls production itself uses between ordinary turns.
`StreamingResampler` has no production analogue (production captures
native 16kHz, no resampling) — it is a harness-only bridging layer, so
"no reset" here means the SAME instance persists across the whole
session (never recreated), which is the correct research-harness
analogue of "don't hide the continuity we're trying to test."

### 110.2 Implementation — additive `--test-mode silent-series`

Added to `r0082f_live_vad_self_echo_poc.py`, purely additively:

- **New constants**: `SILENT_SERIES_EPISODE_COUNT = 10`,
  `SILENT_SERIES_GAP_S = 2.5` (natural inter-response silence),
  `SILENT_SERIES_POST_EVENT_MARGIN_S = 3.0` (evidence retained after an
  early-stop failure), `SILENT_SERIES_HARDWARE_TIMEOUT_MARGIN_S = 60.0`
  (safety-net only).
- **New CSV header** `CSV_HEADER_SILENT_SERIES` (12 base columns +
  `episode_number`, `playback_active`) — separate from `CSV_HEADER`
  (silent-user, unchanged) and `CSV_HEADER_BARGEIN` (deliberate-bargein,
  unchanged).
- **New function `run_hardware_role_silent_series()`** — identical
  `PlatformAudio` setup to `run_hardware_role` (ONE instance, AEC on
  continuously, held open for the whole series), but with an
  **event-based hold**: waits on `room.on("participant_disconnected",
  ...)` for the speech participant to disconnect (confirmed present in
  the installed `livekit` API, audited via `inspect`), bounded by a
  generous safety-net `asyncio.wait_for` timeout that should never
  actually fire in a healthy run — not an unsafe fixed sleep.
  `run_hardware_role` itself (used by silent-user/deliberate-bargein) is
  completely untouched; `main()`'s dispatch routes to the new function
  only for `--role hardware --test-mode silent-series`.
- **New function `run_speech_role_silent_series()`** — same connect/
  subscribe/first-frame-gate preamble as the other two speech roles,
  then: constructs ONE `LiveVadChain(csv_writer, response_dispatched=
  False)` and ONE `StreamingResampler()` BEFORE the episode loop, starts
  the continuous `_consume_mic()` task once (cancelled only at the very
  end — mic/VAD observation never pauses, covering every episode AND
  every gap), then loops over 10 episodes: `chain.sm.
  notify_response_dispatched()` → `_play_signal_cancelable()` (REUSED
  unchanged from R0082-G, `emit_cue=False` — real, unsuppressed
  cancellation semantics and the proven `AudioSource.clear_queue()` fix
  apply identically if a genuine confirmed interruption ever fires) →
  `chain.sm.notify_response_finished()` → failure check → (if none) a
  `SILENT_SERIES_GAP_S` sleep → failure check again. A SINGLE persistent
  `interrupt_confirmed_event` (never reset) is shared across all 10
  episodes, matching R0082-G's own proven pattern.
- **New pure function `compute_silent_series_episode_diagnostics(csv_path)`**
  — post-hoc, read-only re-scan of the just-written CSV grouped by
  `episode_number`, computing per-episode max probability, max smoothed
  volume, frames≥0.7, longest consecutive ≥0.7 streak, accepted starts,
  confirmed interruptions. Diagnostic only — never feeds back into the
  canonical event-based verdict, which is decided live from `chain.
  started_events`/`chain.confirmed_events` directly.
- **CLI**: `--test-mode` gains a third choice, `"silent-series"`.
  `silent-user` remains the default; `deliberate-bargein`'s own choice
  and behavior are untouched.

### 110.3 Failure handling — exactly as specified

If `chain.started_events` or `chain.confirmed_events` grows at any
checkpoint (end of an episode's playback, or end of a gap), the run
records the exact new event(s), which phase they occurred in
(`episode_NN_playback` or `gap_NN`), stops scheduling further episodes
immediately, retains `SILENT_SERIES_POST_EVENT_MARGIN_S` (3.0s) of
further observation (not zero, not the full remaining series), then
writes evidence and exits with `VALID TEST -- FAIL` (not `INVALID` —
a genuine detected failure is a valid result, distinct from
instrumentation invalidity). A genuine `INTERRUPT_CONFIRMED` is never
suppressed: the SAME shared `interrupt_confirmed_event` that
`_consume_mic()` sets is the SAME one passed to that episode's
`_play_signal_cancelable()` call, so playback genuinely, immediately
stops and the publisher queue is genuinely cleared — realistic
cancellation semantics, not faked-through.

### 110.4 Validity criteria — separate from PASS/FAIL, checked in order

```
if failure occurred:                                    VALID TEST -- FAIL
elif remote_frames_received == 0 or vad_frames == 0:     INVALID -- no VAD input frames
elif episodes_completed < 10 (no genuine failure):       INVALID -- incomplete without cause
elif capture_duration_s < expected_min_duration_s:       INVALID -- capture truncated
elif accepted_starts == 0 and confirmed == 0:            VALID TEST -- PASS
else:                                                     VALID TEST -- FAIL
```

`expected_min_duration_s = PRE_ROLL_S + 10×SPEECH_DURATION_S +
9×SILENT_SERIES_GAP_S + TAIL_S` (≈258.3s for the real frozen stimulus)
— the SAME time-accounting pattern already used and validated by
R0082-F/G's own real hardware runs (where actual capture met or
exceeded this kind of expectation, not fell short — see §110.5's dry-
run caveat below).

### 110.5 Offline validation performed this round (all before any hardware)

**Live, scaled-down, end-to-end dry runs** (synthetic fake-hardware
publisher, real local `livekit-server`, the REAL unmodified
`run_speech_role_silent_series()`, `SILENT_SERIES_EPISODE_COUNT`/
`SILENT_SERIES_GAP_S`/`SPEECH_DURATION_S`/`read_speech_wav()`
monkey-patched to a short synthetic clip FOR DRY-RUN SPEED ONLY, clearly
not a real-run claim; evidence moved to
`r0082f_live_captures/r0082h_dryrun_synthetic_NOT_real_hardware/`):

```
Clean run (3 episodes x 2.0s, 0.5s gaps): 3/3 episodes completed, 0
  accepted starts, 0 confirmed interruptions, per-episode diagnostics
  all populated and clean, capture_duration_s=10.030 vs
  expected_min=11.000 -> correctly reported INVALID (short by ~0.97s)

Clean run (3 episodes x 8.0s, 0.5s gaps): 3/3 episodes completed, 0
  events, capture_duration_s=28.050 vs expected_min=29.000 ->
  correctly reported INVALID (short by ~0.95s -- an ABSOLUTE, not
  proportional, gap, confirmed by comparing both configs)

Episode-playback failure (burst injected during episode 2): accepted
  VAD start at t=13.376s -> INTERRUPT_CONFIRMED at t=13.696s ->
  AUDIO_SOURCE_QUEUED_BEFORE_CLEAR=1.0014s -> AUDIO_SOURCE_QUEUE_CLEARED
  -> AUDIO_SOURCE_QUEUED_AFTER_CLEAR=0.0000s (the SAME R0082-G
  clear_queue() mechanism, reused unmodified, fired correctly) ->
  episode 3 NEVER scheduled -> episodes_completed=2/3 -> 3.0s evidence
  margin retained -> VALID TEST -- FAIL, phase=episode_02_playback

Gap failure (burst injected during gap 2): accepted VAD start at
  t=16.48s, NO confirmed interruption (the ISM is IDLE during a gap --
  speech_started() while IDLE returns NONE per the audited production
  source, so it structurally cannot reach INTERRUPT_CANDIDATE/CONFIRMED
  outside an active episode -- expected, not a defect) -> still
  correctly detected and classified -> episodes_completed=2/3 -> VALID
  TEST -- FAIL, phase=gap_02
```

**Dry-run timing caveat, stated honestly, not glossed over:** both
"clean" dry runs showed `capture_duration_s` falling short of
`expected_min_duration_s` by a roughly constant ~0.95–1.0s (absolute,
not proportional to episode length — confirmed by comparing the 2.0s-
and 8.0s-episode configs). This did NOT happen in R0082-F's own real
hardware run (`capture_duration_s=27.270` EXCEEDED
`expected_min_duration_s=27.181`, a positive margin) using the exact
same accounting pattern. The likely explanation: this dry run's
synthetic fake-hardware publisher's own `capture_frame()` submission
loop is not governed by a real hardware sample clock the way genuine
`PlatformAudio` capture is, and may not pace as strictly to true real
time. **The validity-check LOGIC itself is confirmed correct** (it
correctly and conservatively reported INVALID rather than a false
PASS in both cases) — but the exact real-world margin for the full
258s, 10-episode series can only be confirmed by an actual hardware
run, consistent with this whole investigation's practice of not
over-claiming what a synthetic dry run can prove.

**Pure-function and structural/regression checks** (29 checks, all
PASS):

```
py_compile                                                          PASS
ruff                                                                 PASS
git diff --check                                                     PASS
compute_silent_series_episode_diagnostics(): per-episode max_prob/
  max_volume/frames>=0.7/accepted starts/confirmed interruptions        PASS (7/7)
compute_silent_series_episode_diagnostics(): non-contiguous >=0.7
  runs do NOT combine into one streak (streak correctly resets)         PASS
compute_silent_series_episode_diagnostics(): episode_number=0
  (pre-roll) rows excluded from per-episode diagnostics                 PASS
SILENT_SERIES_EPISODE_COUNT == 10                                       PASS
run_speech_role_silent_series() calls the real read_speech_wav()
  unconditionally (frozen-WAV SHA256 verified before every real run)    PASS
chain/resampler constructed exactly ONCE (continuity, not reset
  per episode)                                                          PASS (2/2)
chain.sm.reset() never called; notify_response_dispatched()/
  notify_response_finished() called once per episode each               PASS (3/3)
consume_task.cancel() appears exactly once, at the very end
  (gaps stay observed, never paused)                                    PASS
interrupt_confirmed_event constructed exactly once for the whole
  session; _play_signal_cancelable() reused unchanged, emit_cue=False   PASS (2/2)
run_hardware_role_silent_series(): PlatformAudio() constructed
  exactly once; event-based hold with a bounded safety net              PASS (3/3)
run_speech_role/run_hardware_role/run_speech_role_deliberate_bargein/
  _play_signal_cancelable/read_speech_wav/evaluate_deliberate_
  bargein_result all byte-for-byte identical to the last commit         PASS (6/6)
Production VAD constants (CONFIDENCE/START_SECS/STOP_SECS/
  MIN_VOLUME) unchanged                                                 PASS (4/4)
```

No production files were touched (`src/`, `apps/`, Gemini,
`ConversationSession`, NeXa Core, `BargeInController` production code,
`AecReferenceFeeder`, XVF3800 DSP, PipeWire/ALSA/mixer config all
untouched — this round only READ `src/nexa/voice/interruption.py` and
the installed `pipecat` package for the audit in §110.1, never wrote to
either). `--test-mode silent-user` and `--test-mode deliberate-bargein`
are both confirmed byte-for-byte unchanged.

## 111. R0082-H — FINAL GATE

```
production reset behavior audited from source, not assumed             PASS
one continuous PlatformAudio/AEC session (hardware role)                PASS
one continuous LiveKit room/session                                     PASS
VAD/resampler/InterruptionStateMachine state continuous across
  all 10 episodes (constructed once, no per-episode reset)              PASS
gaps observed by the SAME continuous mic/VAD consumer task              PASS
exactly 10 deterministic episodes scheduled by default                  PASS
per-episode failure detection (playback AND gap) proven live            PASS
real, unsuppressed cancellation on a genuine confirmed interruption
  (reuses R0082-G's proven AudioSource.clear_queue() mechanism)         PASS
early-stop retains a bounded post-event evidence margin, does not
  blindly continue all 10 episodes after a genuine failure               PASS
event-based hardware hold (not an unsafe fixed sleep)                    PASS
existing silent-user / deliberate-bargein modes byte-for-byte
  unchanged                                                              PASS
production VAD constants unchanged                                      PASS
py_compile / ruff / git diff --check                                    PASS
no production files touched                                             PASS
```

**Verdict: R0082-H READY for ONE real continuous silent-user hardware
run.** This session did not execute hardware. Per instruction, this is
a single validation run — no further replication of R0082-H is
scheduled until this first real run's result is reviewed.

## 112. Two R0082-H research-harness corrections, found before any hardware run

Both corrections are to the `--test-mode silent-series` research
harness only. Neither touches `--test-mode silent-user`,
`--test-mode deliberate-bargein`, `_play_signal_cancelable()`,
`read_speech_wav()`, `evaluate_deliberate_bargein_result()`,
`run_speech_role`, `run_hardware_role`, or
`run_hardware_role_silent_series` — all confirmed byte-for-byte
identical to commit `341f478` (verified below). R0082-G's 5/5 PASS
result is unaffected.

### 112.1 Issue 1 — a bare accepted VAD START is now detected LIVE, not at end-of-episode

**Defect.** `_consume_mic()` already reacted immediately to
`chain.confirmed_events` growth (setting `interrupt_confirmed_event`),
but a bare `chain.started_events` entry with NO confirmation was only
noticed later, at the next end-of-episode/end-of-gap
`_check_for_failure()` checkpoint. A false accepted START near the
START of a ~23.18s episode could leave the test running for many
additional seconds before the series stopped.

**Fix.** `_consume_mic()` now also latches a SEPARATE, session-level
`test_failure`/`test_failure_event` the INSTANT `chain.started_events`
grows — at the same point, inside the same frame-processing loop, where
confirmed-growth is already observed. This is deliberately NOT the same
signal as `interrupt_confirmed_event`: a bare START is never treated as
`INTERRUPT_CONFIRMED`, and production's own real behavior (it would not
cancel playback on a bare start either) is preserved exactly.
`_play_signal_cancelable()`'s own `cancel_event` parameter remains
wired ONLY to `interrupt_confirmed_event`, unchanged, never to
`test_failure_event` — confirmed via source inspection (offline test).

Each episode's playback now runs as an `asyncio.Task`, raced against
`test_failure_event.wait()`:

- If the playback task finishes first (natural completion, OR a genuine
  `INTERRUPT_CONFIRMED` already cancelled it via
  `_play_signal_cancelable()`'s own UNCHANGED mechanism), nothing
  further happens — the real R0082-G cancellation semantics, when they
  apply, are untouched and take precedence exactly as before.
- If `test_failure_event` fires first (a bare START with no
  confirmation yet), a bounded `SILENT_SERIES_POST_EVENT_MARGIN_S`
  (3.0s) margin is raced against the SAME still-running playback task.
  If a genuine confirmation arrives within that margin, the playback
  task ends on its own (real cancellation, unchanged) and nothing
  further is substituted. Only if the margin elapses with playback
  STILL running does the research test perform its OWN, separately
  logged `TEST_TEARDOWN_PLAYBACK_STOP` — cancelling the playback
  `Task` from the OUTSIDE (`task.cancel()`, never by modifying
  `_play_signal_cancelable()` itself) and clearing the publisher queue
  as administrative cleanup only. This is NEVER logged as
  `PLAYBACK_CANCEL_REQUESTED`/`INTERRUPT_CONFIRMED` — new, distinct
  milestones (`TEST_FAILURE_ACCEPTED_START`, `POST_EVENT_MARGIN_
  COMPLETE`, `TEST_TEARDOWN_PLAYBACK_STOP`) keep research teardown
  semantically separate from production interruption semantics
  throughout the logs, the milestones JSON, and the printed summary.

The identical race-then-margin pattern applies to inter-episode gaps
(no playback to tear down there — the gap's own sleep is simply cut
short and the margin retained), with one structural difference
confirmed by direct source audit: during a gap the ISM is `IDLE`
(post `notify_response_finished()`), and `InterruptionStateMachine.
speech_started()` while `IDLE` returns `InterruptionEvent.NONE` per its
own source — a bare start during a gap can **structurally never**
escalate to `INTERRUPT_CONFIRMED`, confirmed empirically in the dry run
below (`confirmed_interruptions=0` despite `accepted_vad_starts=1`).

**The canonical failure timestamp is always the FIRST accepted START's
own timestamp** (`failure_event_audio_t`/`failure_event_mono`,
captured at the moment `_consume_mic` first detects it), never the
later confirmation or teardown moment — confirmed in the live dry run
below (`audio_t=4.416s`, the START's own timestamp, even though
confirmation followed at `4.736s` and the session ended shortly after).

`_check_for_failure()` (the old end-of-episode/end-of-gap diffing
mechanism) is retired — fully superseded, since a genuine
`INTERRUPT_CONFIRMED` can structurally only ever follow an earlier
accepted START (production VAD hysteresis requires a start before a
confirm), so `test_failure_event` always fires at least as early. A
defensive fallback remains in the (structurally unreachable) case where
`chain.confirmed_events` somehow grows without `test_failure` already
being set.

### 112.2 Issue 2 — per-response diagnostics no longer include the following gap

**Defect.** `compute_silent_series_episode_diagnostics()` grouped rows
by `episode_number` alone. Since a response's trailing gap keeps the
SAME `episode_number` (only `playback_active` changes to `0`), a
response's reported `max_prob`/`max_volume`/`frames_ge_0_7`/
`longest_streak` could actually be sourced from the FOLLOWING silent
gap, not that response's own playback.

**Fix.** Rewritten to group by `(episode_number, playback_active)`.
Returns `{"responses": [...], "gaps": [...]}` — `responses[i]` covers
ONLY `playback_active=1` rows for that episode number (a response's own
playback, gap excluded); `gaps[i]` covers ONLY `playback_active=0` rows
for that episode number, and ONLY emits an entry if such rows actually
exist for that episode number. Episode 10 correctly has no gap entry
(nothing scheduled after it in the clean-path loop). The canonical
event-based PASS/FAIL verdict is completely unchanged — still decided
live from `chain.started_events`/`chain.confirmed_events` directly, not
from this diagnostic function's output.

**A third, related fix, needed to make "episode 10 has no gap" actually
true in practice**: `episode_number` is reset to `0` (the same
"pre-roll, not a numbered episode" sentinel) during the `TAIL_S` window
after a clean 10-episode finish, AND during the post-failure settle
before writing evidence. Without this, `TAIL_S`'s own
`playback_active=0` rows would have been mislabeled as a spurious
"gap 10" under the new grouping (since nothing in the loop schedules a
real gap after the last episode, but the TAIL window's rows would
otherwise carry `episode_number=10, playback_active=0` — exactly the
same key shape as a real gap). Verified in the live dry runs below: a
3-episode clean run produces exactly 2 gap entries (1, 2 — never 3).

**The early/middle/late trend printing is now explicit about which
metric it uses**, per instruction — printed as two clearly separate
blocks:

```
-- PLAYBACK ONLY trend (responses 1-3 / 4-7 / 8-10) --
-- GAPS trend, reported separately (gaps 1-3 / 4-6 / 7-9) --
```

never combined into one value labeled ambiguously as "response max."

### 112.3 Live dry-run evidence (scaled-down, synthetic, real local `livekit-server`, evidence moved to `r0082h_dryrun_synthetic_NOT_real_hardware/`)

**Early-burst-during-playback run** (3×15.0s episodes, 0.5s gaps, burst
injected 4.0s into episode 1 — proves items 1 and 2 together):

```
[speech] MILESTONE: response_01_start
[speech] MILESTONE: TEST_FAILURE_ACCEPTED_START at t=4.416s, episode=1,
  playback_active=True -- ... NOT yet INTERRUPT_CONFIRMED
[speech] *** UNEXPECTED INTERRUPT_CONFIRMED ... at t=4.736s, episode=1 ***
  -- ... _play_signal_cancelable() handles this itself, unchanged
[speech] MILESTONE: AUDIO_SOURCE_QUEUED_BEFORE_CLEAR = 1.0065s
[speech] MILESTONE: AUDIO_SOURCE_QUEUE_CLEARED
[speech] MILESTONE: AUDIO_SOURCE_QUEUED_AFTER_CLEAR = 0.0000s
[speech] MILESTONE: response_01_end
[speech] *** FAILURE latched during episode 01 -- stopping the series early ***
[speech] MILESTONE: post-event evidence margin already retained inline
  at detection time -- ending the session
  ...
  episodes_completed            = 1 / 3
  total_accepted_vad_starts     = 1
  total_confirmed_interruptions = 1
  response 01: max_prob=0.9759 ... starts=1 confirms=1
  *** FAIL -- failure_kind=accepted_start episode=1 playback_active=True
    audio_t=4.416s ***
  VALID TEST -- FAIL
```

Total wall-clock elapsed for this dry run: **5.81s**, vs. ~48s a full
3×15s-episode run (with 2 gaps + PRE_ROLL + TAIL) would have taken had
it NOT stopped early — direct, quantitative proof that a bare START
does not wait for the full episode, let alone the full series. The
canonical failure timestamp (`4.416s`, the START itself) is preserved
even though a genuine confirmation followed 0.320s later and the real
R0082-G cancellation mechanism (`clear_queue()`) fired correctly and
unmodified.

**Gap-triggered failure run** (3×5.0s episodes, 3.0s gaps, burst
injected during gap 2 — proves items 4, 5, 7, 8):

```
episodes_completed            = 2 / 3
total_accepted_vad_starts     = 1
total_confirmed_interruptions = 0        <- structurally impossible during a gap (ISM IDLE)

-- PLAYBACK diagnostics (gap rows excluded) --
response 01: max_prob=0.0255 ... starts=0 confirms=0
response 02: max_prob=0.0261 ... starts=0 confirms=0   <- clean, despite gap 02's huge spike

-- GAP diagnostics (playback rows excluded; episode 10 has no gap) --
gap 01: max_prob=0.0032 ... starts=0 confirms=0
gap 02: max_prob=0.9933 max_vol=0.9870 frames>=0.7=31 longest_streak=31 starts=1 confirms=0

*** FAIL -- failure_kind=accepted_start episode=2 playback_active=False
  audio_t=16.352s ***
VALID TEST -- FAIL
```

`response 02`'s own diagnostics (`max_prob=0.0261`) are completely
unaffected by `gap 02`'s huge spike (`max_prob=0.9933`, 31 frames≥0.7,
occurring immediately after episode 2's own clean playback) — direct,
quantitative proof that Issue 2's fix correctly isolates playback from
gap diagnostics, not just in a synthetic unit test but in a live run
where both a real response and a real gap sit immediately adjacent to
each other in the same CSV.

**Clean 3-episode run** (3×8.0s episodes, 0.5s gaps, no injected
events): `episodes_completed=3/3`, `total_accepted_vad_starts=0`,
`total_confirmed_interruptions=0`, exactly `response 01/02/03` and
`gap 01/02` entries (never a spurious `gap 03`) — confirming both "no
false event → full N episodes" and "the last episode has no gap entry"
at this smaller scale. (Same previously-documented, dry-run-specific
~0.98s absolute capture-duration shortfall as §110.5 — unrelated to
this round's fixes, already explained there as an artifact of the
synthetic fake-hardware publisher's own pacing, not the harness logic.)

**Pure teardown path (`TEST_TEARDOWN_PLAYBACK_STOP` firing with NO
confirmation ever arriving) was NOT triggered live this round** — and
an honest account of why is given rather than a forced/contrived
positive result: this system's own timing makes it structurally
difficult to reach. Once a bare START is accepted, `poll()` runs on
every subsequent VAD frame (every 32ms) as long as mic consumption
continues (which it always does during an active episode), and
`confirm_hold_secs=0.3s` is far shorter than both `SILENT_SERIES_
POST_EVENT_MARGIN_S` (3.0s) and the VAD-analyzer's own `STOPPING`
rejection hysteresis (`VAD_STOP_FRAMES=31`×32ms≈0.992s) — so a genuine
`INTERRUPT_CONFIRMED` reliably follows within ~300–330ms whenever mic
frames keep arriving, exactly as observed in the early-burst dry run
above. An attempt to force pure teardown by having the fake-hardware
publisher disconnect shortly after its burst instead exposed an
unrelated, pre-existing characteristic shared by ALL three test modes
(silent-user, deliberate-bargein, silent-series): `rtc.AudioStream`'s
`async for` does not itself time out or raise if the remote participant
disconnects mid-stream — `_consume_mic()` would hang waiting
indefinitely for more frames, the same as it always has (the mandatory
first-frame gate's own bounded timeout applies only to the FIRST frame,
by design, not to an already-established stream). This is a pre-
existing, out-of-scope characteristic, not a regression from this
round's fixes, and was not pursued further. The `TEST_TEARDOWN_
PLAYBACK_STOP` code path remains validated structurally (offline
source-inspection checks confirm its presence and correct wiring —
§112.4) as defensive handling for a genuinely anomalous mic-delivery
stall, which real hardware jitter could in principle produce, even
though this round's synthetic dry runs could not cleanly isolate it
without triggering the unrelated hang.

### 112.4 Offline validation performed this round (37 checks, all PASS)

```
py_compile                                                          PASS
ruff                                                                 PASS
git diff --check                                                     PASS
compute_silent_series_episode_diagnostics(): responses/gaps split
  correctly (gap-1 spike does not leak into response 1; response-2
  peak does not leak into gap 2)                                     PASS (7/7)
non-contiguous >=0.7 runs do not combine into one streak              PASS
episode_number=0 rows excluded from BOTH responses and gaps
  regardless of playback_active                                      PASS
SILENT_SERIES_EPISODE_COUNT == 10                                    PASS
run_speech_role_silent_series() calls the real read_speech_wav()
  unconditionally                                                    PASS
chain/resampler constructed exactly ONCE                             PASS (2/2)
chain.sm.reset() never called; notify_response_dispatched()/
  notify_response_finished() called once per episode each            PASS (3/3)
consume_task.cancel() appears exactly once, at the very end           PASS
_play_signal_cancelable reused via asyncio.create_task, emit_cue=False PASS
test_failure_event is a SEPARATE Event from interrupt_confirmed_event PASS
bare-start latching happens INSIDE _consume_mic() at frame-processing
  time, not at an end-of-episode/end-of-gap checkpoint                PASS
failure_kind='accepted_start' recorded distinctly                     PASS
TEST_FAILURE_ACCEPTED_START milestone present                         PASS
module explicitly states a bare start is NOT yet INTERRUPT_CONFIRMED  PASS
TEST_TEARDOWN_PLAYBACK_STOP / POST_EVENT_MARGIN_COMPLETE are
  DISTINCT milestones from INTERRUPT_CONFIRMED/
  PLAYBACK_CANCEL_REQUESTED, explicitly labeled as such in-code        PASS (2/2)
teardown cancels the playback Task from the OUTSIDE
  (_play_signal_cancelable() itself untouched for this purpose)       PASS
_play_signal_cancelable()'s cancel_event wired ONLY to
  interrupt_confirmed_event, never to test_failure_event               PASS
run_hardware_role_silent_series(): PlatformAudio() once; event-based
  hold with a bounded safety net                                      PASS (3/3)
run_speech_role/run_hardware_role/run_speech_role_deliberate_bargein/
  _play_signal_cancelable/read_speech_wav/evaluate_deliberate_
  bargein_result all byte-for-byte identical to commit 341f478         PASS (6/6)
Production VAD constants unchanged                                    PASS (4/4)
```

No production files were touched this round (only read
`src/nexa/voice/interruption.py`, already-audited, for confirmation of
the `IDLE`-state `speech_started()` behavior cited in §112.1/§112.3).
`--test-mode silent-user` and `--test-mode deliberate-bargein` are both
confirmed byte-for-byte unchanged from commit `341f478`.

## 113. R0082-H — FINAL GATE (post-correction)

```
production reset behavior audited from source (unchanged this round)   PASS
one continuous PlatformAudio/AEC/VAD/resampler/ISM session preserved   PASS
bare accepted START detected LIVE, inside _consume_mic()                PASS
bare start stops the experiment after a bounded (~3.0s) evidence
  margin, races correctly against a still-running episode's playback    PASS
research teardown semantically distinct from INTERRUPT_CONFIRMED/
  PLAYBACK_CANCEL_REQUESTED in logs, milestones JSON, and code           PASS
genuine confirmed interruption still uses R0082-G's real, unmodified
  clear_queue() cancellation semantics, takes precedence over teardown  PASS
canonical failure timestamp is the FIRST accepted START, not later      PASS
playback diagnostics exclude gap rows; gap diagnostics exclude
  playback rows; episode 10 correctly has no gap entry                  PASS
early/middle/late trend reported as two explicit, separate blocks
  (playback-only, gaps-only) -- never combined                          PASS
canonical PASS/FAIL still fully event-based, unchanged                  PASS
exactly 10 episodes on the clean path (dry-run-scaled: 3/3 proven)      PASS
existing silent-user / deliberate-bargein / _play_signal_cancelable /
  read_speech_wav / evaluate_deliberate_bargein_result / run_hardware_
  role / run_hardware_role_silent_series all byte-for-byte unchanged    PASS
production VAD constants unchanged                                      PASS
py_compile / ruff / git diff --check                                    PASS
no production files touched                                             PASS
```

**Verdict: R0082-H (corrected) READY for ONE real continuous
silent-user hardware run.** This session did not execute hardware.
