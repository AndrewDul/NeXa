# R0056 — Existing AEC/Double-Talk Solutions Audit + Portable Acoustic Frontend Design

**Date:** 2026-09-13
**Milestone:** M2.6B.4N follow-up (post-R0055 design checkpoint, superseding
the earlier same-day XVF3800-specific discriminator draft)
**Status:** **RESEARCH + DESIGN ONLY. No production code implemented. No
XVF3800 parameter changed (read-only audit).** No Gemini call. No push.
This checkpoint **supersedes and replaces** the deleted, uncommitted
`R0056_echo_double_talk_discriminator_design` draft, whose
`Xvf3800`-shaped discriminator direction is explicitly rejected here in
favor of a device-agnostic Acoustic Frontend contract. **The single
biggest finding this checkpoint: a real, previously-undiscovered -20dB
ALSA mixer attenuation on the reSpeaker's own reference-input channel —
found via the OFFICIAL, already-installed XMOS `xvf_host` tool plus the
official XMOS tuning guide — is a strong, native, cheaply-testable
candidate root cause, more fundamental than anything R0053-R0055
investigated.** `M2.6B` remains IN PROGRESS.

## R0055 EVIDENCE BASE

R0055 (`docs/reports/R0055_pcm_correlation_analysis_of_residual_self_echo_20260913.md`)
established, from real hardware PCM (MAX volume, n=3): 3/3 false
confirmed barge-ins; zero clipping/near-saturation; a stable ~85-130ms
reference→mic lag, constant across each trial; normalized correlation
4-9× the noise floor in the failure region, peaking at the VAD-start/
confirm boundary; leading mechanism judged Class C (hardware AEC leaves
a real, correlated residual), confidence MEDIUM-HIGH; recommended (not
implemented) a NeXa-owned discriminator as the next design checkpoint.
**This checkpoint does not redo R0055's own analysis** — it instead asks
the prior, more fundamental question R0055's own gate deferred: *is a
NeXa-owned discriminator actually the right next layer, or does an
existing maintained solution (native hardware, or a mature software
stack) already solve this?*

## WHY WE MUST NOT BUILD AN XVF3800-SPECIFIC NEXA

NeXa's own charter (`AGENTS.md` §3: "provider-independent,
device-independent") already forbids letting one peripheral become
NeXa's canonical voice architecture. XVF3800 is the Pi's current
acoustic front-end, but NeXa must run equally well on a Windows/Linux/
macOS laptop, iPhone/iPad, Android, a plain Bluetooth headset, and
future custom hardware — none of which has an XVF3800, a `plug:respeaker`
ALSA device, or `xvf_host`. A component named (or shaped like)
`Xvf3800DoubleTalkDiscriminator` — even if internally clean — encodes a
Pi-only assumption directly into the interruption-authority path,
exactly the kind of "second brain per device" the charter's own M2.6
principle ("NeXa remains the single authority; cloud and local are
replaceable providers") already rejects for conversation providers and
must equally reject for acoustic processing. The correct shape is a
canonical **contract** NeXa's device-agnostic `BargeInController`
depends on, with each platform's own best acoustic solution plugged in
underneath — never the reverse.

## EXISTING-SOLUTIONS RESEARCH METHOD

Two kinds of evidence, kept clearly separated below:

1. **Live, read-only audit of THIS Pi's actual XVF3800**, using the
   official, already-downloaded Seeed/XMOS `host_control` package
   (`/home/devdul/Tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/`, the
   authoritative vendor tool — not a reimplementation) and the official
   XMOS tuning-guide page it links to (fetched this checkpoint,
   quoted verbatim). No parameter was written.
2. **Cross-platform ecosystem research**, using official documentation
   where a live fetch succeeded (PipeWire, GStreamer, XMOS — quoted
   below with the fetched content) and well-established, stable platform
   facts where a fetch attempt 404'd (several Apple/Android/Windows/
   WebRTC URLs guessed this session did not resolve) — those sections
   say so explicitly and are flagged as *platform knowledge, not a
   live-verified quote*, per this checkpoint's own "avoid unsupported
   claims" instruction. Nothing here is sourced from a blog, forum, or
   unmaintained repo.

## XVF3800 NATIVE CAPABILITIES

### Live device state (read-only, this Pi, this checkpoint)

Via `sudo ./xvf_host <PARAM>` (a pure read — no value passed) against
the connected reSpeaker (VID 0x2886/`10374`, PID `26`/`0x1A`):

| Parameter | Live value | Meaning (from the tool's own `--list-commands`) |
|---|---|---|
| `VERSION` | `2 0 6` | Firmware 2.0.6 — **not** the newest image on disk (`v2.0.7`/`v2.0.9`/`v2.0.9_48k` exist locally, never flashed) |
| `BLD_MODIFIED` | `TRUE` | Firmware is a modified build, not a pristine official release |
| `AEC_AECCONVERGED` | `1` | AEC filter reports converged (at idle, room-dependent) |
| `AEC_FAR_EXTGAIN` | **`-20.0` dB** | External gain the pipeline is told has been applied to the far-end reference — see finding below |
| `AEC_ASROUTONOFF` | `1` | Host receives the ASR/beamformed output, not raw per-mic residual |
| `AUDIO_MGR_SYS_DELAY` | `12` (samples) | Delay applied to the reference before the SHF algorithm |
| `AUDIO_MGR_REF_GAIN` | `8` | Matches Seeed's own quoted example config exactly |
| `AUDIO_MGR_MIC_GAIN` | `90` | Matches Seeed's own quoted example config exactly |
| `PP_FMIN_SPEINDEX` | `1300` Hz | Matches Seeed's own quoted example config exactly |
| `PP_DTSENSITIVE` | `0` | Echo-suppression/double-talk tradeoff, most-suppression end |
| `PP_GAMMA_E` / `_ETAIL` / `_ENL` | `1` / `1` / `1.1` | Echo over-subtraction factors |
| `PP_ECHOONOFF` | `1` | Echo suppression stage enabled |
| `PP_NLATTENONOFF` | `1` | Non-linear echo attenuation enabled |
| `PP_NLAEC_MODE` | `0` (normal) | Not in a training mode |
| `PP_AGCONOFF` / `PP_AGCGAIN` / `PP_AGCMAXGAIN` | `1` / `15.7` / `64` | AGC active, live gain well below its ceiling |
| `AEC_PCD_MINTHR`/`MAXTHR`/`COUPLINGI` | `0.005`/`0.1`/`-1.0` | Path-Change Detection thresholds present (a native mechanism this repo had not previously catalogued) |

`AUDIO_MGR_REF_GAIN`, `AUDIO_MGR_MIC_GAIN`, and `PP_FMIN_SPEINDEX` are
**byte-for-byte identical** to the reference/example configuration
Seeed's own bundled `host_control/README.md` quotes as a starting-point
example — direct evidence **this device has never been tuned away from
factory defaults for our real acoustic setup** (a separate USB DAC +
physical speaker, not the analog 3.5mm jack that README's own tuning
notes assume).

### CONFIRMED FINDING: an undiscovered -20dB reference-path attenuation

Cross-referencing `AEC_FAR_EXTGAIN=-20` against the ALSA mixer directly
(`amixer -c Array`, this checkpoint — never previously queried this way,
since `/etc/asound.conf`'s own `ctl.!default { card UACDemoV10 }`
means a plain, cardless `amixer` call has never reached the `Array`
card in any prior checkpoint):

```
Simple mixer control 'PCM',1
  Capabilities: pvolume pvolume-joined pswitch pswitch-joined
  Playback channels: Mono
  Mono: Playback 40 [67%] [-20.00dB] [on]
```

**This is an exact, dB-for-dB match.** The official XMOS tuning guide
(fetched this checkpoint, quoted verbatim) explains the mechanism
precisely: *"In the UA [USB Audio] device variant, when the host sets
the output volume, the `AEC_FAR_EXTGAIN` is internally set to be the
same as the gain set by the host, so the user shouldn't need to set
this command externally."* The reSpeaker's own USB Audio Class mono
playback control (`plug:respeaker`, the exact ALSA device
`AecReferenceFeeder`'s `aplay` writes the reference PCM to) is
currently sitting at **-20dB**, not unity — and the firmware
automatically mirrors that live host volume into its own internal
`AEC_FAR_EXTGAIN` compensation.

**Why this matters more than anything R0053-R0055 found:** R0053's own
`CoherentReferenceGain` fix (measured effect: 5/5→2/3 false MAX
barge-ins) matches the reference PCM's amplitude to the SEPARATE
audible-speaker device's (`UACDemoV10`) own mixer (~-0.94dB at MAX,
a small correction). That fix has **zero knowledge of, and never
touches,** the `Array` card's own independent `PCM,1` mixer — a
completely different ALSA control on a completely different USB device.
R0055's own reference-RMS measurements (`ref_raw_rms_phase_playback`,
~3400-4000 out of 32767) were tapped in software, **before** this
hardware attenuation is ever applied — meaning the true reference level
the XVF3800's own adaptive filter builds its echo-path model against is
**roughly 10× (-20dB) quieter than R0055's own reported reference RMS
figures**, while the real ACOUSTIC echo reaching the mic (via the real
speaker and room) is attenuated by no such extra factor. A reference
that is an order of magnitude quieter than the true acoustic echo is a
textbook cause of **under-cancellation that gets worse as real playback
volume rises** — which is exactly R0055's own central, unexplained
observation (correlated residual, worst at MAX). This is a plausible,
concrete, **native, zero-code, fully-reversible** candidate root cause
that was structurally invisible to every prior checkpoint (R0052-R0055
never inspected the `Array` card's own mixer, only `UACDemoV10`'s).

**Not changed this checkpoint** (explicit read-only gate) — flagged as
the leading candidate experiment for the next checkpoint (see CURRENT
PI NEXT STEP).

### Official XMOS tuning guidance (fetched, quoted)

From `xmos.com/documentation/.../04_tuning_the_application.html`
(Seeed's own README links here for exactly this class of problem):

- **System delay:** *"In an ideal system, the delay between the
  reference and microphone signals should be at or less than 40
  samples,"* measured with the vendor's own `xvf_tools.py
  mic_ref_correlate <wav>` utility against a provided test signal.
  *"Setting this delay to a negative value is the recommended method to
  correct acausality"* — i.e. **negative** values delay the *microphone*
  side to keep the reference causally ahead of the acoustic echo it
  models. R0055's measured ~85-130ms (1360-2080 samples @16kHz)
  reference-to-mic offset is **35-50× the guide's own "ideal" 40-sample
  bound** — strong, independent, native-vendor corroboration that this
  path is materially mistimed, consistent with (not contradicting)
  R0055's own "Class A, non-differentiating secondary finding" verdict:
  the timing offset is real and large by the vendor's own stated
  standard, even though R0055 correctly found it does not by itself
  explain *which* window fails.
  **Gap found:** `xvf_tools.py`/`mic_ref_correlate` is **not** present
  anywhere on this machine (confirmed by `find /`) — it ships in a
  separate XMOS SDK/application-note bundle not yet downloaded here.
- **Double-talk sensitivity:** *"A recommended approach is to start with
  this parameter [`PP_DTSENSITIVE`] set to 0, and attempt to then tune
  the rest of the echo suppression parameters"* first — **the device's
  current `PP_DTSENSITIVE=0` is the vendor's own documented STARTING
  POINT, not a misconfiguration** (an earlier same-day hypothesis in
  this checkpoint's own working notes, corrected before writing this
  report — not carried into the final analysis). `PP_DTSENSITIVE` is
  raised only if *"insufficient echo suppression is achieved before
  [`PP_GAMMA_*`] unacceptably high (above around 1.4)"* — i.e. it is the
  vendor's own **last** native lever, not the first.
- **`PP_GAMMA_*` tuning:** *"A typical range for these parameters is
  between 1.0 and 1.4"* (current live values: 1.0/1.0/1.1 — near the
  low end of that range, room to increase before `PP_DTSENSITIVE` would
  even be considered per the vendor's own recommended order).
- **`AEC_FAR_EXTGAIN`:** confirms the auto-tracking behavior above and
  that, for the USB variant, *"the user shouldn't need to set this
  command externally"* — reinforcing that the right fix is the ALSA
  mixer level itself, not a manual `AEC_FAR_EXTGAIN` override.

**Vendor's own recommended tuning ORDER, reconstructed from this
guide:** (1) fix system delay via `mic_ref_correlate`: target ≤40
samples; (2) tune `PP_GAMMA_*` (1.0-1.4 range) with `PP_DTSENSITIVE=0`;
(3) only then raise `PP_DTSENSITIVE` if suppression still insufficient
without unacceptable `PP_GAMMA_*` values. **The -20dB ALSA mixer finding
above sits logically BEFORE step (1)** — an unmanaged reference-gain
fault upstream of everything the vendor's own tuning flow assumes is
already correct.

## WEBRTC AEC3

WebRTC's `AudioProcessing` module (AEC3, the modern echo canceller
replacing the older AEC2/AECM) is the most widely deployed open,
maintained acoustic-echo-cancellation implementation outside vendor
silicon — used by Chrome, PipeWire's own echo-cancel module (see below),
countless VoIP stacks. **A live fetch of AEC3's own design README this
checkpoint returned HTTP 404** (URL guessed, did not resolve) — the
following is **stated as well-established, stable platform/OSS
knowledge, not a quoted live source**: AEC3 performs adaptive
echo-path modeling with its own internal delay estimation (tolerant of
a much wider and more dynamic delay range than a fixed hardware
parameter), computes residual-echo estimates, and its public
`AudioProcessingStats` API is documented (across WebRTC release notes
this author has read previously, not re-verified this session) to
expose diagnostic fields including echo-return-loss and a residual/
echo-likelihood estimate in recent versions — **the exact current field
names/availability should be verified against whatever WebRTC revision
NeXa would actually vendor, not assumed from memory**, flagged as an
open question below. AEC3 does not, in its ordinary simple usage,
require or expose a separate "am I in double-talk" boolean to the
caller — it is designed to keep cancelling correctly *through*
double-talk internally, exposing cleaned audio (and, per the above,
some diagnostic stats) rather than a discrete admission decision.

## PIPEWIRE

Official docs (`docs.pipewire.org/page_module_echo_cancel.html`, fetched
this checkpoint): the `echo-cancel` module supports **exactly one AEC
engine: WebRTC** (`library.name = aec/libspa-aec-webrtc` — the doc lists
no Speex or other backend). Architecture is a clean four-stream design
(capture / sink-as-reference / playback / source-as-cleaned-output),
with the sink and capture streams explicitly correlated and the far-end
signal removed from the near-end. **The documentation exposes no
double-talk flag, ERL/ERLE metric, or echo-probability signal to
clients — only the final cleaned audio stream** (confirmed by this
checkpoint's own fetch, not assumed). This makes PipeWire's echo-cancel
module an excellent **audio-cleaning backend** on Linux desktop, but not
by itself a source of the admission-evidence `BargeInController` needs —
any double-talk decision on top of it would still need to come from
NeXa's own VAD-on-cleaned-audio (which is already exactly what Silero
VAD does today) or from a lower-level tap into the same underlying
WebRTC AEC3 instance PipeWire itself uses.

## GSTREAMER

Official docs (`gstreamer.freedesktop.org/documentation/webrtcdsp`,
fetched this checkpoint) confirm `webrtcdsp` ("Pre-processes voice with
WebRTC Audio Processing Library") and `webrtcechoprobe` ("Gathers
playback buffers for webrtcdsp") exist as GStreamer Bad-plugins
elements — i.e. the **same underlying WebRTC AEC3 engine**, wrapped as
GStreamer elements. The fetch could not retrieve the individual
element's property/signal documentation (linked sub-pages, not fetched
this session) — **not claimed as verified**, flagged as an open
question for the implementation checkpoint if a GStreamer-based desktop
pipeline is ever chosen over a direct `libwebrtc-audio-processing`
binding.

## APPLE

**Platform knowledge, not a live-verified quote this session** (fetch
attempts to guessed Apple Developer URLs 404'd): iOS/macOS expose a
system Voice Processing I/O path — `AVAudioEngine.inputNode
.isVoiceProcessingEnabled` (wrapping the underlying
`kAudioUnitSubType_VoiceProcessingIO` Audio Unit) — which performs
Apple's own built-in AEC/AGC/noise suppression on the input signal
whenever a matching output is active, well-documented in the AVFAudio
framework for years and considered stable, high-quality, and
system-optimized for the specific device (far better tuned per-device
than any generic software AEC could be, since Apple controls the whole
hardware+firmware stack). It exposes toggles (bypass, mute-telephony-
alert, AGC on/off) but, per this author's existing knowledge, **no
explicit double-talk/echo-likelihood signal to the app** — same shape
as WebRTC/PipeWire's "cleaned audio only" contract. This should be the
**native-first backend** on Apple platforms per the decision hierarchy.

## ANDROID

**Platform knowledge, not a live-verified quote this session** (fetch
returned only navigation chrome, not the class body). `android.media
.audiofx.AcousticEchoCanceler` is part of the standard Android audio
effects framework, alongside `NoiseSuppressor` and
`AutomaticGainControl`. Critically — and this is a long-standing,
widely-known Android platform characteristic, not a NeXa assumption —
**availability is per-device, not guaranteed**: an app must call
`AcousticEchoCanceler.isAvailable()` before use, and real-world quality
varies significantly across OEM implementations (a well-known industry
pain point behind why many VoIP apps on Android ship their own WebRTC
AEC3 as a fallback rather than trusting the platform effect uniformly).
This directly shapes the recommended Android backend policy below:
**try native `AcousticEchoCanceler` first, fall back to a vendored
WebRTC AEC3 when unavailable or when its quality is empirically
insufficient** — exactly the decision hierarchy the charter asks for,
applied concretely to a platform with known inconsistent native quality.

## WINDOWS

**Platform knowledge, not a live-verified quote this session** (fetch
attempts 404'd). Windows historically leaves acoustic echo cancellation
to the application or a "communications" endpoint category with
OS-default enhancements (varies by OEM audio driver/APO stack) rather
than guaranteeing a single, uniform, high-quality built-in AEC an app
can rely on the way Apple's Voice Processing I/O can be relied on.
**WebRTC AEC3, vendored directly, is the pragmatic, portable, and
already-industry-standard choice on Windows** (and Linux desktop
outside PipeWire) — the same conclusion nearly every major cross-
platform voice app (browsers, Zoom-class clients, etc.) has already
converged on for exactly this reason.

## HEADSETS

A wired or Bluetooth headset's own physical acoustic coupling between
speaker and mic is typically far weaker than an open speakerphone setup
(the mic is nowhere near the earpiece/earbud driver) — real self-echo
risk is usually much lower, sometimes negligible. The architecture must
not force a full AEC/double-talk pipeline to run needlessly here: the
Acoustic Frontend contract (below) includes a **capability flag** a
headset-class backend can report (e.g. "echo risk: low/negligible") so
`BargeInController`'s own admission-policy consultation can be skipped
entirely without any headset-specific code inside `BargeInController`
itself — this is a *capability*, not a special case, in the design
below.

## SPEEXDSP / OTHER OPTIONS

SpeexDSP's own `speex_echo_*` AEC (an older, still-maintained, much
lighter-weight NLMS-family canceller) predates AEC3 and is generally
considered lower quality on double-talk-heavy content, but is
sometimes chosen for extremely constrained embedded targets where even
AEC3's footprint is too large. Not recommended as a first choice on any
target NeXa currently plans to support (Pi 5 and above, desktop, mobile
all have ample headroom for AEC3 if a software fallback is ever needed)
— noted for completeness per the task's own research list, not adopted.

## OPTION COMPARISON

| | Native HW (XVF3800) | Native OS (Apple/Android) | WebRTC AEC3 | Custom NeXa DSP |
|---|---|---|---|---|
| Already solves basic AEC | Yes (imperfectly tuned here) | Yes (Apple: excellent; Android: variable) | Yes, maturely | No — would reinvent it |
| Solves double-talk discrimination | Has native levers (`PP_DTSENSITIVE`, PCD) not yet tried | Not exposed as a discrete signal | Internal, robust-by-design; explicit stats not yet confirmed for this repo's target version | Would have to be built from scratch |
| Portable across all target devices | No (Pi-only) | No (platform-only) | **Yes** (Windows/Linux/Android-fallback/embedded) | Yes, but expensive to build+validate everywhere |
| Maintenance burden on NeXa | Low (vendor-maintained) | Low (OS-maintained) | Low (upstream-maintained; NeXa vendors, doesn't author) | **High** — NeXa owns correctness forever |
| Effort to try next | **Very low** (already-installed tool, reversible ALSA/parameter changes) | Low (already an OS API) | Medium (new dependency, build/vendor work) | High |

**Decision-hierarchy conclusion, directly answering the charter's Step
6:** native (hardware or OS) first, WebRTC AEC3 as the mature portable
software fallback second, custom NeXa DSP only where neither native nor
WebRTC can provide the needed behavior or signal — and on the CURRENT
Pi problem specifically, native has **not yet been tried at all**
(everything R0052-R0055 tuned lives entirely on the NeXa/software side
of the boundary; the -20dB finding above is native-side and untouched).

## WHAT WE CAN REUSE DIRECTLY

- The already-installed, official `xvf_host` tool for all future
  read/write XVF3800 tuning — no NeXa-authored XVF3800 control code
  needed.
- WebRTC's `AudioProcessing`/AEC3 as a vendored dependency (via
  `libwebrtc-audio-processing`, the same de-facto-standard packaging
  PipeWire itself depends on) for any platform where native processing
  is unavailable or insufficient — never a NeXa-authored echo canceller.
- Apple's `AVAudioEngine.inputNode.isVoiceProcessingEnabled` directly —
  zero third-party dependency needed on Apple platforms.
- Android's `AcousticEchoCanceler` (with the documented
  `isAvailable()` guard) as the default, with a vendored WebRTC AEC3
  fallback reusing the SAME dependency the Windows/Linux backend
  already needs — one dependency, two platforms' fallback path.

## WHAT WE SHOULD NOT BUILD OURSELVES

- A bespoke acoustic echo canceller of any kind (linear or non-linear)
  — AEC3/native silicon already solve this far better than a
  NeXa-authored first attempt could, on every target platform.
- A bespoke double-talk *detector* algorithm as the FIRST resort — the
  XVF3800 already has two native, untried levers for exactly this
  (`PP_DTSENSITIVE`'s two-digit "extra near-end speech detector" mode,
  and the previously-uncatalogued `AEC_PCD_*` Path Change Detection
  family), and AEC3 is designed to handle double-talk internally.
- A platform-specific `Xvf3800...` class living anywhere near
  `BargeInController`'s own decision path — per WHY WE MUST NOT BUILD
  AN XVF3800-SPECIFIC NEXA above.

## SELECTED NEXA ACOUSTIC ARCHITECTURE

```
Device-specific acoustic backend (Xvf3800 / AppleVoiceProcessing /
AndroidNative+WebRtcFallback / WebRtcAec / Passthrough-for-headsets)
                    │
                    ▼
        NeXa Acoustic Frontend contract
        (capabilities + evidence, device-agnostic)
                    │
                    ▼
   NeXa VAD / BargeInController admission policy
        (unchanged canonical authority, device-agnostic,
         already-accepted Silero + InterruptionStateMachine)
                    │
                    ▼
       Conversation / realtime provider (local or cloud)
```

`BargeInController` keeps its existing, already-accepted role as the
**sole** interruption authority, on every platform, unmodified in its
core state machine. What changes is only what feeds it: instead of
being wired directly to Pi-specific `AecReferenceHealth`/
`AecReferenceFeeder` objects, it depends on the portable contract below
— on the Pi, that contract is implemented by wrapping the SAME,
already-proven `AecReferenceHealth`/`AecReferenceFeeder`/XVF3800 wiring
verbatim (reuse, not replacement); on other platforms, a different
backend implements the identical contract using that platform's own
best native or WebRTC solution.

## ACOUSTIC FRONTEND CONTRACT

New package `src/nexa/voice/acoustic/` (parallel to the existing
`nexa/voice/` modules). Interfaces only — no implementation this
checkpoint:

```python
@dataclass(frozen=True)
class AcousticCapabilities:
    has_hardware_aec: bool
    has_native_double_talk_signal: bool   # e.g. XVF3800 PCD/DTSENSITIVE-derived, or none
    echo_risk: Literal["negligible", "low", "moderate", "high"]  # e.g. "negligible" for a headset
    exposes_erl_erle: bool                # AEC3-class stats, when available
    backend_name: str                     # for logging/telemetry only, never branched on elsewhere


@dataclass(frozen=True)
class AcousticEvidence:
    """One snapshot, evaluated on demand by BargeInController at its
    existing confirm-hold-expiry point -- never a second interruption
    authority. Every field is Optional: a backend reports only what it
    can; BargeInController's own policy already knows how to be
    conservative (fail-open toward allowing a real interruption) when a
    field is None, exactly as today's `aec_health.barge_in_safe` absence
    already causes safe, unmodified fallback behavior."""
    assistant_audible: bool
    near_end_speech_likely: bool | None      # backend's own best evidence, if any
    double_talk_likely: bool | None          # explicit only if the backend can say so
    echo_likelihood: float | None            # 0..1, only if the backend exposes one


class AcousticFrontend(Protocol):
    capabilities: AcousticCapabilities

    def current_evidence(self) -> AcousticEvidence: ...
    def assistant_started(self) -> None: ...
    def assistant_stopped(self) -> None: ...
```

`BargeInController` gains ONE new optional constructor parameter,
`acoustic_frontend: AcousticFrontend | None = None` (default preserves
exact current behavior, byte-for-byte — the same zero-risk-when-absent
contract `aec_health`/`gain_source` already established), consulted only
at the existing confirm-hold-expiry point, using the SAME bounded,
fail-open-always semantics the deleted draft's discriminator design
already worked out (never re-litigated here — that reasoning carries
over unchanged, now keyed to this portable contract instead of a
XVF3800-specific class).

## BACKEND CAPABILITY MODEL

Capabilities are **declared, not assumed uniform**: a backend reports
only what it can actually provide (`AcousticCapabilities`), and
`AcousticEvidence` fields are `Optional` for exactly this reason — no
backend is required to fake a signal it cannot produce. This directly
satisfies the charter's "use capabilities/optional evidence rather than
pretending all devices have identical DSP telemetry" instruction.

## BACKEND SELECTION POLICY

Chosen once per session (or per detected device/OS at startup), never
per-turn, never leaking into `ConversationSession`/memory/identity/
routing:

| Platform | Primary backend | Fallback |
|---|---|---|
| Raspberry Pi + XVF3800 | `Xvf3800AcousticBackend` (wraps existing `AecReferenceHealth`/`AecReferenceFeeder`, unchanged) | — (single known Pi config today) |
| iPhone/iPad, macOS | `AppleVoiceProcessingBackend` | — (Apple's own path is reliable enough not to need one) |
| Android | `AndroidNativeAcousticBackend` (`AcousticEchoCanceler.isAvailable()`) | `WebRtcAecBackend` when unavailable/insufficient |
| Windows | `WebRtcAecBackend` | — |
| Linux (non-Pi, e.g. desktop) | `WebRtcAecBackend` (or a PipeWire-echo-cancel-backed variant if PipeWire is present) | — |
| Headset / Bluetooth audio (any OS) | `PassthroughAcousticBackend` (`echo_risk="negligible"`, `AcousticEvidence` mostly `None`) | promotes to the OS's own primary backend above if headset detection is ever wrong |
| Future custom hardware | Whatever new backend implements the same contract | — |

Not hardcoded as final — an implementation checkpoint may find better
per-platform choices (e.g. a native Windows path that turns out
sufficient); the CONTRACT, not this table, is the durable decision.

## PI/XVF3800 BACKEND

`Xvf3800AcousticBackend` **wraps**, never replaces, the existing,
already-accepted `AecReferenceHealth` + `AecReferenceFeeder` +
`CoherentReferenceGain` (R0053) + off-loop gain read (R0054) — all
frozen, all reused verbatim. It additionally may (future checkpoint,
not now): read `AEC_AECCONVERGED`/`AEC_PCD_*` via the official
`xvf_host` tool (or a thin, read-only Python wrapper around the same
official protocol) to populate `AcousticEvidence.echo_likelihood`/
`double_talk_likely` from the chip's OWN native telemetry — reuse, not
a NeXa-authored DSP algorithm. `capabilities.has_hardware_aec=True`,
`has_native_double_talk_signal` becomes `True` only once such a
read-path is actually implemented and validated.

## WEBRTC FALLBACK

`WebRtcAecBackend` vendors `libwebrtc-audio-processing` (the same
packaging PipeWire's own echo-cancel module depends on — a
well-trodden path, not a novel integration) and feeds it the mic +
reference streams the SAME way `Xvf3800AcousticBackend` does internally
(via the Acoustic Frontend contract's own construction, not a parallel
mechanism). Whether AEC3's `AudioProcessingStats` exposes a usable
echo-likelihood field for the exact vendored version is an **open
question**, not yet confirmed (see OPEN QUESTIONS) — if it does not,
this backend still provides `has_hardware_aec=False` (software),
`has_native_double_talk_signal=False`, and `AcousticEvidence` limited to
`assistant_audible`/cleaned-audio-only, with NeXa's own existing
Silero-VAD-on-cleaned-audio behavior unaffected (works exactly as local
voice already does today, just fed AEC3-cleaned audio instead of raw
mic PCM).

## APPLE BACKEND

`AppleVoiceProcessingBackend` wraps `AVAudioEngine.inputNode
.isVoiceProcessingEnabled` — no third-party dependency. Reports
`has_hardware_aec=True` (system-level, effectively hardware-tier
quality), `has_native_double_talk_signal=False` (no documented explicit
signal), `echo_risk` derived from whether an external speaker vs.
built-in speaker/earpiece is the current output route (a real, already
-available AVAudioSession property, not new NeXa logic).

## ANDROID BACKEND

`AndroidNativeAcousticBackend` checks `AcousticEchoCanceler
.isAvailable()` at startup; if true, wraps it (`has_hardware_aec=True`
in the "OS-native" sense); if false (a real, common case on some
devices), the SAME device-agnostic contract lets the caller construct
`WebRtcAecBackend` instead — a plain fallback selection at construction
time, never a runtime per-turn branch inside `BargeInController`.

## WINDOWS/LINUX BACKEND

`WebRtcAecBackend`, described above, is the default on both — Linux
optionally preferring a PipeWire-echo-cancel-backed variant when
PipeWire is confirmed present and running (still ultimately the same
underlying WebRTC engine, per PipeWire's own documented single-backend
design).

## FAIL-SAFE / FALLBACK SEMANTICS

Identical governing principle to the deleted draft's own (correct, not
discarded) analysis, now expressed at the CONTRACT level so it applies
uniformly to every backend, not just the Pi: **`AcousticFrontend=None`,
or any `AcousticEvidence` field being `None`, or any backend raising —
all resolve to exactly today's behavior (confirm the barge-in), never
to a stricter, less-interruptible default.** A backend can only ever
make `BargeInController` MORE conservative when it has positive,
well-evidenced reason to (an explicit `double_talk_likely=False` with
supporting `echo_likelihood`), never by omission or failure.

## CURRENT PI NEXT STEP

**Do not implement `Xvf3800AcousticBackend` yet, and do not implement
any discriminator yet.** The concrete, ordered, native-first experiment
for the next checkpoint, entirely reversible, requiring zero new NeXa
code:

1. **Raise the `Array` card's own `PCM,1` ALSA mixer from -20dB toward
   0dB** (`amixer -c Array sset 'PCM',1 100%`) — the single highest-
   confidence, lowest-effort experiment this checkpoint found. Re-run
   the existing, unmodified R0055 capture command
   (`m2_6b4m_self_echo_probe.py --level max --repeats 3 --capture-pcm
   --max-lag-ms 500`) unchanged, and compare against R0055's own
   already-recorded 3/3 false-confirm baseline.
2. **Obtain the official `xvf_tools.py`/`mic_ref_correlate` utility**
   (a separate XMOS SDK/app-note download, not yet on this machine) and
   run it per the official guide to measure the TRUE reference-to-mic
   delay as the chip itself sees it, then set `AUDIO_MGR_SYS_DELAY`
   accordingly (per the guide, likely a substantial **negative** value
   given R0055's own measured ~85-130ms offset) — via `xvf_host`, fully
   reversible, `SAVE_CONFIGURATION` only once validated.
3. Only if (1)+(2) leave a measurable residual false-barge-in rate:
   raise `PP_GAMMA_*` toward the vendor's own stated 1.0-1.4 range
   (still native, still zero NeXa code).
4. Only if native tuning is exhausted and insufficient: revisit the
   Acoustic Frontend contract's own `Xvf3800AcousticBackend` reading
   `AEC_PCD_*`/two-digit `PP_DTSENSITIVE`, or a WebRTC AEC3 software
   layer on top of the XVF3800's own output — in that order, per the
   decision hierarchy.
5. A custom NeXa double-talk algorithm (the deleted draft's own design)
   remains available as a documented, ready-to-resurrect fallback
   **only if 1-4 all prove insufficient** — not the default plan.

## RISKS

- The -20dB finding is a strong, well-evidenced *candidate*, not yet a
  *proven* root cause — it requires the same kind of real-hardware
  re-validation every prior checkpoint in this thread has insisted on
  before claiming success (an explicit next-checkpoint step, not
  assumed here).
- `xvf_tools.py` is not yet available locally — obtaining it (correct
  version, matching this firmware) is a real, if small, logistics step
  before step 2 above can run.
- Raising the `Array` card's reference volume changes what
  `AEC_FAR_EXTGAIN` auto-tracks; if the true acoustic echo path also
  has headroom concerns, re-checking for clipping/distortion (per the
  guide's own explicit caution) is required before accepting the
  result.
- WebRTC AEC3's exact diagnostic-stats surface for whatever version
  NeXa would vendor is unconfirmed this checkpoint (see OPEN
  QUESTIONS) — do not assume a specific field name exists without
  checking the actual vendored header/API.

## OPEN QUESTIONS

- Does `AudioProcessingStats` (or the equivalent in whatever
  `libwebrtc-audio-processing` release NeXa would vendor) actually
  expose a residual-echo-likelihood or ERL/ERLE field usable by
  `WebRtcAecBackend`, or would `AcousticEvidence.echo_likelihood` stay
  `None` on that backend regardless? Needs a source read of the actual
  vendored version, not assumed from general WebRTC knowledge.
- What is the correct, vendor-documented method to compute the target
  `AUDIO_MGR_SYS_DELAY` value from a measured ~85-130ms real delay,
  given the parameter's own sample-rate base was not fully pinned down
  by this checkpoint's fetch (likely the SHF algorithm's own internal
  rate) — resolved once `xvf_tools.py` is obtained and its own
  documentation read directly.
- Whether GStreamer's `webrtcdsp`/`webrtcechoprobe` element-level
  property docs (not retrieved this session) offer anything beyond what
  a direct `libwebrtc-audio-processing` binding already would — low
  priority, only relevant if a GStreamer-based backend is ever chosen.

## NEXT CHECKPOINT PLAN

**R0057 (proposed): native XVF3800 tuning experiment.** Read-only
findings from this checkpoint converted into the smallest reversible
real-hardware experiment: (1) raise the `Array` `PCM,1` ALSA mixer to
unity, (2) re-run R0055's own unmodified capture command, (3) report the
false-confirm rate against the existing 3/3 MAX baseline. Only if that
alone is insufficient does a follow-up checkpoint pursue
`AUDIO_MGR_SYS_DELAY` recalibration (pending `xvf_tools.py`), then
`PP_GAMMA_*`, in the order CURRENT PI NEXT STEP lays out. The Acoustic
Frontend contract itself (this report's own design) is implemented
incrementally starting whenever the Pi-native experiments conclude,
never blocking them.

## FILES CHANGED

- `docs/reports/R0056_existing_acoustic_solutions_and_portable_frontend_design_20260913.md`
  (this report; replaces the deleted, never-committed
  `R0056_echo_double_talk_discriminator_design_20260913.md` draft).
- `docs/CURRENT_STATE.md`, `docs/ROADMAP.md` — updated with
  RESEARCH/DESIGN status only.

**No `src/nexa/**` file touched. No test file touched. No XVF3800
parameter written** (every `xvf_host` invocation this checkpoint was a
bare read — no value argument was ever passed).

## TESTS / STATIC CHECKS

No new tests this checkpoint (research/design only). Full project
suite re-run to confirm zero regressions from touching only
documentation:

- `.venv/bin/python -m unittest discover -s tests -p "test_*.py"` →
  **1061 tests, OK (skipped=7)** (unchanged from R0055).
- `ruff check` on every file this checkpoint touched: clean (no
  `src/**`/`tests/**` file was touched).
- `pip check`: "No broken requirements found."
- `git diff --check`: clean.

## GIT STATUS

Clean working tree after commit. Not pushed. No Gemini call. No XVF3800
parameter changed.

## COMMIT HASHES

- `35d2baf` — research: existing AEC/double-talk solutions audit +
  portable Acoustic Frontend design (M2.6B.4N follow-up / R0056).
