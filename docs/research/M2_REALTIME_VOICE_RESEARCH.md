# M2 — Realtime Voice: Open-Source-First Research

- **Date:** 2026-09-05
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice (research only; **no product code in this
  document's scope**)
- **Status:** Research complete enough to inform an architecture ADR.
  **No prototype was built or run** in this task — see §7. This is desk +
  legacy-evidence research, not a working proof.
- **Related:** `docs/ROADMAP.md` (M2), `docs/architecture/FOUNDATION_ARCHITECTURE.md`
  (Voice boundary, conceptual), `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
  (the `ConversationSession` this must plug into), `docs/decisions/ADR-0002_text_conversation_foundation.md`,
  `docs/research/RESEARCH_POLICY.md`, `docs/legacy/LEGACY_NEXA_INDEX.md` §"Voice
  pipeline"

---

## 1. Purpose and policy

Per the owner's explicit instruction: NeXa should **not** reimplement mature
realtime-voice infrastructure from scratch where a suitable maintained
open-source implementation exists. Preference order: **reuse → adapt → patch
→ fork → build native**, in that order, with "build native" requiring
evidence that reuse is unsuitable — not merely that "custom" feels more
NeXa-owned.

**Architectural ownership rule (non-negotiable, carried into every option
below):** an external framework may own audio capture/playback, VAD, turn
detection, interruption/barge-in mechanics, WebRTC transport, and metrics. It
must **never** become the canonical NeXa brain. NeXa's `ConversationSession`
(`src/nexa/conversation/session.py`, M1.1, ADR-0002 D1) must remain the sole
owner of conversation history, system prompt/persona, and model/provider
choice. Every candidate below is evaluated first on whether it can be made to
respect this without a fight.

## 2. Method (per `RESEARCH_POLICY.md`)

1. **Legacy NeXa first** (`smart-desk-ai-assistant`, read-only) — real
   measured Pi 5 numbers already exist for STT/TTS/VAD; this is the single
   most valuable evidence source and is used throughout §4–§6 instead of
   generic external claims wherever both exist.
2. **Official docs + actual source code** of pipecat-ai/pipecat and
   livekit/agents (GitHub raw files, reference docs, real examples) — not
   just marketing pages.
3. **External benchmarks/community evidence** for STT/TTS/VAD on Pi-class
   ARM hardware, cross-checked against legacy's real numbers wherever they
   overlap (they often disagree — see §5.3).
4. **License files fetched directly** (GitHub API `license.spdx_id` / raw
   `LICENSE` file), not inferred from a repo's README or a blog's claim.

## 3. Legacy NeXa evidence (read-only; `VERIFIED FACT` unless noted)

The legacy repo already built a full custom voice pipeline (never Pipecat or
LiveKit — a repo-wide search found **zero** references to either). Its real,
measured evidence on **this exact Pi 5** is the most load-bearing input to
this research:

- **faster-whisper accuracy/speed sweep** (`var/reports/asr_benchmark_sweep_20260518-225531.json`):

  | Config | Avg WER | EN WER | PL WER | Avg latency |
  |---|---|---|---|---|
  | tiny, beam1, auto-lang | 0.683 | 0.54 | 0.786 | 6.79 s |
  | tiny, beam3, lang-hint | 0.572 | 0.473 | 0.643 | 1.93 s |
  | base, beam1/3 | 0.54–0.56 | ~0.41 | ~0.63–0.67 | 3.49–3.57 s |
  | small, beam1 | **0.301** | 0.307 | **0.298** | **9.65 s** (worst case 18.9 s) |

  Real single-run production probe: faster-whisper `base` STT = **2544 ms**.
  Separately, the project's own ASR-runtime-decision report
  (`var/reports/asr_runtime_decision_20260519-000000.md`) measured `small`
  at **35–92 s** end-to-end in the live app (not the isolated benchmark) and
  rejected it outright as unusable regardless of quality; `tiny` was
  rejected for Polish ("co to jest teleportacja" → "Soto is teleportation").
  **`base` was flagged "needs runtime benchmark" and never fully resolved.**
  This is a real, hardware-verified accuracy/speed trade-off, not a
  theoretical one: the fast options are too inaccurate for Polish, the
  accurate option is too slow.

- **Piper TTS, real production measurement**: `pl_PL-gosia-medium` Polish
  voice = **6971–6972 ms/utterance** on Pi 5 CPU (`docs/NeXa_RuNTiMe_FiX_and_Update/32_voice_chat_single_turn_integration_...md`).
  This directly **contradicts** generic external benchmarks claiming Piper
  RTF ≈ 0.12–0.5 (see §5.3) — likely because those external numbers use
  lightweight English voices, not a medium-quality Polish one. **The
  same-hardware, same-language number is trusted over the generic one.**

- **Real end-to-end production timeline** (`var/log/manual_global_runtime_performance_test_20260518-165802.log`,
  `..._natural_llm_ack_test_...log`): a no-LLM fast-lane command turn
  (wake → action → TTS) took **~7.0 s**. A conversational, LLM-answered turn
  took **27.6–54 s** wake-to-complete, with the combined
  LLM-generation + TTS-narration block alone taking **19.9–37.9 s**. This is
  the realistic baseline M2 must not regress below, and it is dominated by
  **LLM generation**, not STT/TTS — consistent with M1's own measured
  `gemma4:e4b` throughput (~3 tok/s).
- **CPU contention, not thermal throttling, is the real risk**: a live LLM
  answer blew its own 75 s pipeline timeout while a concurrent pytest suite
  shared the same 4 cores (`docs/NeXa_RuNTiMe_FiX_and_Update/164_...md`). No
  `vcgencmd get_throttled` non-zero value was ever logged. This is direct
  evidence for the resource-budget risk in §5.1: running STT + TTS + VAD +
  a 10 GB LLM concurrently on 4 CPU cores is a real, previously-encountered
  failure mode on this exact machine, not a hypothetical.
- **Hailo was never used for STT/TTS** — only for vision (YOLO object
  detection). A Hailo-for-LLM path existed but was blocked by a
  `libhailort` version mismatch. Hailo-Whisper-offload was **never even
  attempted** in legacy — it is genuinely new territory for NeXa, not
  something legacy already tried and rejected.
- **Barge-in was only ever "partial"**: a wake-word-gated reopen, not true
  open-mic interruption, specifically to avoid the assistant hearing its own
  TTS output (`docs/troubleshooting.md` #082). Full barge-in was never
  achieved.
- **Two-tier ASR was a real, latency-driven design**, not cosmetic: Vosk
  fast-line first (a constrained bilingual PL/EN command grammar, <100 ms–1.85 s
  real), falling through to full faster-whisper decode (2.2–12.2 s real) only
  for free-form speech.
- **Legacy's own architectural failure directly motivates this project's
  ownership rule**: legacy ended up with **two separate, non-unified "Core
  Brains"** — one for typed chat (through its MAS/Model Router) and a
  completely separate one for voice (`CompanionDialogueService`, direct HTTP
  to Ollama, its own in-memory-only history, no shared safety/verifier
  coverage). This is exactly the "parallel framework-owned LLM/session/history
  path" the owner's instructions for this research explicitly forbid
  repeating. Any M2 candidate that tempts NeXa into a second conversation
  path must be rejected on this evidence alone, independent of its other
  merits.

## 4. Hardware / resource-budget reality check

`VERIFIED FACT` (M1.1, `R0004`): `gemma4:e4b` alone is ~10 GB resident RAM,
~3 tok/s, 100% CPU during generation, on a 15 GB-usable Pi 5. `INFERENCE`
(strong, from legacy §3 evidence above): adding STT + TTS + VAD + an
orchestration framework concurrently on the **same 4 CPU cores** while the
LLM is generating is the single biggest open feasibility risk for M2 — not
whether a suitable OSS framework exists (it does, see §5), but whether this
Pi 5 can run all of it together without one component starving another,
exactly as already happened once in legacy. This is **not resolved by this
research** and should be the first question any M2 prototype answers, before
architecture bikeshedding.

## 5. OSS candidate deep dives

### 5.1 Pipecat (pipecat-ai/pipecat)

- **License**: `VERIFIED FACT` — BSD-2-Clause (fetched `LICENSE` directly).
  Permissive; no field-of-use, redistribution, or NOTICE burden beyond
  preserving the copyright/disclaimer text. No commercial-use restriction.
- **Architecture**: `Pipeline` of `FrameProcessor`s connected by typed
  `Frame`s (`TextFrame`, `AudioRawFrame`, `StartInterruptionFrame`, etc.).
  Turn-taking/interruption frames (`StartInterruptionFrame`/`StopInterruptionFrame`)
  propagate through the pipeline automatically; a custom `FrameProcessor`
  correctly handles them without extra code (`OBSERVATION`, official custom
  frame-processor guide).
- **Custom LLM integration — confirmed workable**: a custom `FrameProcessor`
  subclass can receive a `TextFrame` from STT, call an arbitrary external
  async function (i.e. `ConversationSession.send(text) -> AsyncIterator[str]`),
  and push `TextFrame`s downstream to TTS. Pipecat does **not** force an
  internal conversation-history object onto a processor written this way —
  the built-in `LLMContextAggregator`/`LLMService` classes are conveniences
  for *their* built-in LLM integrations, not a mandatory layer (`VERIFIED
  FACT`, official custom-frame-processor guide + `pipecat.services.*.llm`
  reference source). This is exactly the shape M1.1's `ModelProvider`
  abstraction already assumes, so the fit is structural, not coincidental.
- **VAD**: Silero VAD is the standard/first-party integration
  (`OBSERVATION`), swappable.
- **STT/TTS local backends**: no official first-party `whisper.cpp`/`Piper`
  service was confirmed, but the `FrameProcessor`/`Service` base classes are
  the same shape used for every vendor integration — wrapping a local
  process (whisper.cpp binary, Piper CLI) behind a custom `STTService`/
  `TTSService` is the same pattern already used throughout the framework,
  not a fight against its architecture (`INFERENCE`).
- **Transport**: `pipecat.transports.local.audio` (`LocalAudioInputTransport`/
  `LocalAudioOutputTransport`, PyAudio-based) — **confirmed local mic/speaker
  I/O with no WebRTC or server required** (`VERIFIED FACT`, official
  reference docs). This matters directly for NeXa's local-first-only M2
  stage.
- **Maintenance**: `OBSERVATION` — 10.1k stars, 1.7k forks, maintained by
  Daily (a commercial WebRTC company) + community, active releases into 2026.
- **Pi 5 / ARM feasibility**: `UNKNOWN` — no direct Pi 5 evidence found for
  the framework's own overhead (separate from whatever STT/LLM/TTS is
  plugged in, which is the dominant cost per §3–§4 anyway).
- **Recommendation for this axis: WRAP-ADAPT.**

### 5.2 LiveKit Agents (livekit/agents)

- **License**: `VERIFIED FACT` — `livekit/agents` root is Apache-2.0; the
  separate, self-hostable `livekit/livekit` WebRTC server is also
  Apache-2.0, and a self-hosted server works with **zero cloud dependency**
  (no LiveKit Cloud account required). **Framework-lock finding, `VERIFIED
  FACT` — directly downloaded and unpacked the actual wheel** (`pip download
  livekit-local-inference`, inspected `dist-info/licenses/MODEL_LICENSE`
  inside it, not just the PyPI classifier): the core package hard-depends on
  `livekit-local-inference` (`Apache-2.0 AND LicenseRef-LiveKit-Model`). Its
  code is Apache-2.0, but the bundled **VAD + end-of-turn-detector model
  weights ship under a separate, genuinely proprietary "LiveKit Model
  License Agreement"** whose text explicitly states: *"you may use these
  LiveKit models freely but can only use them together with the LiveKit
  Agents framework. You cannot use the LiveKit models on a standalone basis
  or with any other frameworks."* — a real, contractual **framework-lock
  requirement**, plus a restriction on using model outputs to develop other
  (non-LiveKit) models. This does not block adopting LiveKit Agents itself,
  but it means: (a) if NeXa ever drops LiveKit Agents, these specific model
  files cannot be carried over or reused elsewhere, and (b) confusingly, the
  framework's **default** local VAD (`inference.VAD(model="silero")`,
  `AgentSession`'s built-in default) is *not* the plain open Silero VAD — it
  is this LiveKit-repackaged, framework-locked model. The genuinely
  open, framework-agnostic upstream Silero VAD is available separately as
  `livekit-plugins-silero` (MIT-licensed, §5.5) and must be **explicitly
  configured in place of the default** if NeXa wants an unencumbered VAD.
  Two independent research passes on this exact question disagreed at first
  (one found the proprietary license, one did not); this was resolved by
  downloading and inspecting the real wheel directly rather than trusting
  either summary — the proprietary license is real and is now the
  authoritative finding.
- **Architecture**: `Agent`/`AgentSession` with independently overridable
  pipeline "nodes" (`stt_node`, `llm_node`, `tts_node`). Turn-taking owned by
  `AgentSession`/`AgentActivity`.
- **Custom LLM integration — confirmed workable, arguably cleaner than
  Pipecat's**: `Agent.llm_node(chat_ctx, tools, model_settings)` is directly
  overridable and its return type explicitly permits a plain
  `AsyncIterable[str]` — no `ChatChunk`/tool-call machinery required
  (`VERIFIED FACT`, `voice/agent.py`). A subclass can ignore
  `activity.llm` entirely and delegate straight to
  `ConversationSession.send(text)`. `AgentSession` keeps its own internal
  `ChatContext` for bookkeeping, but nothing forces it to be authoritative —
  it can be left inert while `ConversationSession` remains the sole source
  of truth. **No history-ownership fight.**
- **VAD/turn detection**: `AgentSession`'s default VAD (`inference.VAD`) runs
  **fully locally regardless of configuration** — no network call at all —
  but is the framework-locked model from the finding above; the
  open/unencumbered equivalent is `livekit-plugins-silero` and must be
  selected explicitly. The end-of-turn/interruption detector
  (`inference.TurnDetector`) defaults to a **local** ~108 MB model
  (`v1-mini`) whenever LiveKit Cloud credentials are absent, and only calls
  out to LiveKit's cloud gateway if `LIVEKIT_API_KEY`/`SECRET` are actively
  configured — so the safe, local-only path is already the default as long
  as no cloud credentials are ever set, but this should be an explicit,
  documented choice in NeXa's config (e.g. never setting those env vars),
  not an accidental byproduct.
- **STT/TTS local backends**: no official local whisper.cpp/Piper plugin;
  the practical local path is pointing the `openai`-compatible plugin's
  `base_url` at a self-hosted OpenAI-compatible server (e.g.
  `faster-whisper-server`, `Kokoro-FastAPI` — which has official LiveKit
  docs) — same "wrap it like every other vendor" shape as Pipecat.
- **Transport**: **not** a hard WebRTC requirement — a `console` CLI mode
  runs the agent against local mic/speaker (`sounddevice`) with no
  `livekit-server`/room needed (`VERIFIED FACT`, `cli/cli.py`). WebRTC only
  becomes relevant once cross-device transport is actually wanted (a later
  milestone, per the ROADMAP's own "Multi-device coordination" boundary).
- **Maintenance**: `VERIFIED FACT` — 14,018 stars, 3,687 forks, same-day
  commits, weekly releases. The most actively maintained of the two
  frameworks by a clear margin.
- **Dependency weight**: `VERIFIED FACT` — core `livekit-agents` pulls in
  the OpenAI Python SDK as a **non-optional** dependency (used for its
  typed schemas even if the OpenAI plugin itself is unused), plus
  `opentelemetry-sdk`, `av`, `numpy`, `sounddevice`. Heavier base install
  than Pipecat's.
- **Pi 5 / ARM feasibility**: `UNKNOWN` — no direct evidence found either
  way.
- **Recommendation for this axis: WRAP-ADAPT** — with the explicit
  requirement to force the local Silero VAD + local turn-detector fallback
  path, never the cloud-hosted default, to stay local-first.

### 5.3 STT: faster-whisper vs. whisper.cpp vs. Hailo offload

| | faster-whisper | whisper.cpp | Hailo Whisper offload |
|---|---|---|---|
| License | `VERIFIED FACT` MIT (source); weights = OpenAI Whisper, MIT | `VERIFIED FACT` MIT | Hailo's own conversion tooling; underlying Whisper weights MIT, Hailo runtime/HailoRT license separate (`UNKNOWN` exact terms, not fetched) |
| Maintenance | `OBSERVATION` active, 263+ commits | `OBSERVATION` very active, 5,100+ commits | `OBSERVATION` official Hailo release as of 2026, active community writeups |
| Pi 5 feasibility | Legacy `VERIFIED FACT`: `tiny`/`base` fast but inaccurate (WER 0.54–0.68), `small` accurate (WER 0.30) but **35–92 s**, rejected | `OBSERVATION` (external, not legacy-verified): `tiny` faster than real-time, `base` ~real-time with 4 threads on Pi 5 — **no head-to-head accuracy number against legacy's PL/EN test set exists yet** | Official pipeline is **English-only calibration**; Polish would need custom calibration data — real but unproven for NeXa's bilingual requirement |
| PL support | Legacy-verified: poor at `tiny`/`base`, good at `small` (too slow) | `UNKNOWN` accuracy (same Whisper weights, different runtime — accuracy should be near-identical to faster-whisper at matched model size, but not independently confirmed) | Not out-of-the-box; needs a calibration spike |
| EN support | Legacy-verified acceptable at `tiny`/`base` | Same expectation, unconfirmed | English-only path is the one Hailo actually ships |
| Latency | Legacy-verified, see §3 table | Native streaming example ships (0.5 s sampling) — better real-time story on paper | Fully offloaded from CPU/RAM if it works — the only option that doesn't compete with `gemma4:e4b` for the same 4 cores |
| Gaps | Speed/accuracy trade-off already proven unacceptable at either end on this exact hardware | No same-hardware, same-language accuracy number yet — **the one prototype most worth running before an ADR** | Polish calibration unproven; no latency numbers found anywhere |
| **Recommendation** | **WRAP-ADAPT** (fallback/accuracy path) | **WRAP-ADAPT** (likely primary, pending the PL accuracy spike) | **PATCH-EXTEND** (real and official-ish, but needs a dedicated Polish-calibration spike before relying on it for M2's architecture ADR) |

### 5.4 TTS: Piper (+ successor) vs. Kokoro

- **Piper — license risk, confirmed**: the original `rhasspy/piper` (MIT) was
  **archived 2025-10-06** (`VERIFIED FACT`, GitHub repo status/README).
  Active development moved to **`OHF-Voice/piper1-gpl`**, confirmed via the
  GitHub API to be licensed **GPL-3.0** (`VERIFIED FACT`,
  `license.spdx_id: "gpl-3.0"`), maintained by the Open Home Foundation
  (also behind Home Assistant's voice stack). **This is a real license
  change requiring explicit review, not an assumption**: GPL-3.0 is
  copyleft — invoking Piper as a separate CLI/subprocess (as legacy did,
  and as any `TTSService`/`FrameProcessor` wrapper would for either
  framework) does not typically trigger copyleft obligations on the calling
  code, but **linking `piper1-gpl` as an in-process Python library** could,
  depending on how NeXa's code is distributed. **Recommendation: invoke
  Piper as a subprocess/CLI, never as an imported library, until this is
  confirmed with the owner or legal review** — the same pattern legacy
  already used successfully (`voices/piper/`, subprocess-based
  `TTSPipeline`).
- **Piper — real performance**: legacy's actual measured Polish
  (`gosia-medium`) latency is **~7 s/utterance** on this Pi 5 (§3) — trust
  this over generic external RTF claims (0.12–0.5) which were not measured
  on Polish/medium-quality voices on this hardware.
- **Kokoro-82M**: `OBSERVATION` — MIT (CLI wrapper) / Apache-2.0 (model
  weights). **Disqualified for NeXa's bilingual requirement**: no Polish
  support as of 2026 (community multilingual extensions exist for other
  languages — French, Spanish, Italian, Japanese, Chinese — but not
  Polish). Pi feasibility is also mixed (sub-real-time reported on a Pi 4;
  Pi 5 unconfirmed) — moot given the language disqualification.
- **Recommendation: WRAP-ADAPT Piper** (as a subprocess), continue watching
  `piper1-gpl` for Polish voice-quality improvements; **do not adopt
  Kokoro** until/unless it gains real Polish support.

### 5.5 VAD: Silero VAD

- **License**: `VERIFIED FACT` — MIT, "zero strings attached" per the
  project's own framing (no telemetry, no keys, no expiry).
- **Maintenance**: `OBSERVATION` — active in 2026 (recent commits/releases).
- **Status in both frameworks**: first-party integration in Pipecat
  (default VAD) and available in LiveKit Agents as the explicit
  `livekit-plugins-silero` plugin (MIT — **not** the same artifact as
  LiveKit's own framework-locked default VAD model, §5.2), and already the
  VAD legacy NeXa used (bundled inside `faster_whisper`'s
  `silero_vad_v6.onnx`) — the only component in this entire research where
  "legacy already used it," "Pipecat ships it by default," and "an
  unencumbered version is available in LiveKit Agents" all agree.
- **Recommendation: USE AS-IS.** This is the one clear, low-risk pick
  regardless of which orchestration framework is chosen.

## 6. Make-vs-build table

| Responsibility | Best OSS candidate | License | Maintenance | Pi 5 feasibility | PL support | EN support | Latency | Replacement cost | Integration complexity | Gaps | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Realtime orchestration/pipeline | Pipecat **or** LiveKit Agents | BSD-2-Clause / Apache-2.0 (+ proprietary turn-detector weights, avoidable) | Active / very active | Unknown (framework overhead itself) | N/A (orchestration-agnostic) | N/A | Framework overhead unmeasured; dominated by LLM/STT/TTS regardless (§4) | Low (both interfaces are thin) | Low–medium (custom LLM node/processor is a documented, intended pattern in both) | No Pi 5-specific evidence for either | **WRAP-ADAPT** (see §7 for A/B/C comparison) |
| Audio capture/playback | Pipecat `LocalAudioTransport` / LiveKit `console` mode | Same as framework | Same as framework | Untested here | N/A | N/A | Untested here | Low | Low (both ship this) | Neither verified on this exact reSpeaker XVF3800 mic | **USE AS-IS** (from whichever framework is picked) |
| VAD | Silero VAD | MIT | Active | Proven in legacy | N/A | N/A | Lightweight, proven | Very low | Very low (first-party in both frameworks) | None found | **USE AS-IS** |
| Turn detection / interruption | Framework default (LiveKit) or frame-based (Pipecat) | LiveKit's bundled local VAD/turn-detector weights are **framework-locked proprietary** (`LiveKit Model License`, verified by unpacking the actual wheel — usable only inside LiveKit Agents); the open equivalent (`livekit-plugins-silero`, MIT) must be selected explicitly; Pipecat's is fully open throughout | Active | Untested | N/A | N/A | Untested | Low–medium | Medium (must explicitly select the open VAD plugin over LiveKit's locked-in default; never set LiveKit Cloud credentials) | Legacy never achieved full open-mic barge-in (only wake-gated) | **WRAP-ADAPT**, with the framework-lock and default-model substitution named explicitly in any ADR |
| STT | whisper.cpp (pending PL accuracy spike) / faster-whisper (fallback) | MIT / MIT | Very active / active | Mixed — see §5.3 | Poor at fast configs, good only when too slow (legacy-verified) | Acceptable at fast configs | 2.5–92 s depending on model size (legacy-verified) | Medium (model-swap only, same weights family) | Low (clean Python/C APIs) | **No same-hardware whisper.cpp PL accuracy number yet — biggest concrete unresolved question** | **WRAP-ADAPT** (whisper.cpp primary), **PATCH-EXTEND** for Hailo offload as a parallel spike |
| TTS | Piper (subprocess) | GPL-3.0 successor (see §5.4 risk) — invoke as subprocess only | Active | Legacy-verified: ~7 s/utterance PL, works | Works, quality debated | Works | ~7 s/utterance (legacy-verified, PL medium voice) | Low (voice files swappable) | Low (subprocess, as legacy already proved) | License needs subprocess-only discipline; 7 s latency is slow for a snappy turn | **WRAP-ADAPT** |
| WebRTC/media transport | LiveKit (self-hosted server) | Apache-2.0 | Very active | Unknown | N/A | N/A | Unknown | High if adopted then dropped | Not needed for M2's single-device stage | Deferred — no cross-device requirement yet (ROADMAP "Later") | **DEFER** (not evaluated for adoption now) |
| Client SDKs | N/A at M2 (single device, no remote client yet) | — | — | — | — | — | — | — | — | Out of scope until multi-device | **DEFER** |
| Latency/metrics infra | Framework-native (Pipecat frame timestamps / LiveKit OpenTelemetry) | Same as framework | Active | Untested | N/A | N/A | N/A | Low | Low | Neither independently verified here | **USE AS-IS**, whichever framework is chosen |

No responsibility above is recommended **BUILD NATIVE** — legacy's own
experience (a large, custom, ultimately dual-brained voice pipeline) is
itself evidence that building from scratch here repeats a known failure
mode rather than avoiding one.

## 7. Prototype-before-decision comparison (A/B/C)

**Disclosure, not hidden**: no runnable prototype was built or executed in
this task. What follows is an architecture-level comparison from source-code
and documentation evidence (§5), not a measured one. Audio hardware **is**
available on this machine (`aplay -l`/`arecord -l` confirm a reSpeaker
XVF3800 4-mic array), so a real prototype is physically possible here — it
was not attempted in this research task because (a) the task scope was
research + license review + make-vs-build analysis, not implementation, and
(b) §4's resource-budget question should be answered by a minimal spike
*before* investing in a full pipeline for either candidate, to avoid
building the wrong thing twice.

| | A. Pipecat only | B. LiveKit Agents only | C. Pipecat + LiveKit transport |
|---|---|---|---|
| `ConversationSession` stays canonical | Yes — custom `FrameProcessor` (§5.1) | Yes — `llm_node` override (§5.2), cleaner API surface | Yes, inherits A's integration |
| License risk | Low (BSD-2-Clause throughout, no proprietary weights anywhere) | Medium — confirmed (§5.2) framework-locked proprietary license on the *default* bundled VAD/turn-detector weights; mitigated by explicitly selecting `livekit-plugins-silero` (MIT) instead | Low (Pipecat's integration governs) |
| Local-first fit (M2 stage) | Direct — `LocalAudioTransport`, no server | Direct — `console` mode, no server, but pulls in more deps (OpenAI SDK, otel) | Direct for now; LiveKit transport unused until cross-device |
| Future cross-device WebRTC | Would need to add LiveKit (or another WebRTC layer) later — real but not urgent work | Native — already the framework's design center | Native — this is the point of combining them |
| Dependency weight | Lighter core install | Heavier core install (non-optional OpenAI SDK, full otel) | Heaviest — both frameworks present |
| Maintenance signal | Active, 10.1k★ | More active, 14k★, weekly releases | Depends on both |
| Integration complexity estimate | Low–medium | Low–medium (arguably simpler override point) | Medium–high (two frameworks' concepts to reconcile) |

**Assessment, not yet a decision**: Option **B (LiveKit Agents)** has the
cleanest custom-LLM override point (`llm_node` returning plain
`AsyncIterable[str]`), the most active maintenance, and a real local-only
`console` mode today — with the explicit condition that its cloud-leaning
turn-detector default must be pinned to the local ONNX fallback to respect
NeXa's local-first principle, and its proprietary-weight component must be
named in any ADR (not silently absorbed as "just a dependency"). Option
**A (Pipecat)** is the lighter-weight, fully-open (no proprietary weights
anywhere) choice with an equally workable integration point, at the cost of
less momentum/less first-party local-STT/TTS coverage than LiveKit's plugin
ecosystem implies (though neither ships first-party whisper.cpp/Piper
plugins — both need the same custom-wrapper work). **Option C is premature**
until a cross-device requirement is actually active (it isn't yet — see
ROADMAP "Multi-device coordination," listed under "Later," not M2).

This is an **input to the M2 ADR, not the ADR itself** — the owner should
weigh "cleanest override + most active + one non-OSI dependency to manage"
(B) against "zero non-OSI dependencies anywhere + slightly more DIY STT/TTS
wrapping" (A) before that ADR is written.

## 8. Open questions / gaps requiring an owner decision or a follow-up spike

1. **Resource-budget spike (highest priority, per §4)**: can this Pi 5 run
   VAD + whisper.cpp(`base`) + `gemma4:e4b` + Piper concurrently without the
   CPU-contention failure legacy already hit once? This should be answered
   with a small, targeted spike before the M2 architecture ADR is written,
   not assumed either way.
2. **whisper.cpp Polish accuracy** on the same test sentences legacy already
   used (§3) — the one clean apples-to-apples measurement missing from this
   research.
3. **Hailo Whisper offload Polish calibration** — real but unproven; worth a
   parallel, lower-priority spike given it's the only STT option that
   doesn't compete with the LLM for CPU/RAM.
4. **Piper's GPL-3.0 successor** — confirm the subprocess-only invocation
   pattern is sufficient, or get explicit legal/owner sign-off before any
   in-process Python import of `piper1-gpl`.
5. **LiveKit's proprietary VAD/turn-detector model license** — `VERIFIED
   FACT` (direct wheel inspection, §5.2): the bundled local models are
   licensed only for use inside LiveKit Agents (a real framework-lock
   clause), separate from the framework's own Apache-2.0 code. If LiveKit
   Agents is chosen, the ADR must name this dependency explicitly, decide
   whether to accept the framework-lock (using the default bundled models)
   or substitute the open `livekit-plugins-silero` for VAD, and confirm no
   LiveKit Cloud credentials are ever configured (keeping the turn-detector
   on its local fallback).
6. **Barge-in target**: legacy never achieved true open-mic interruption.
   M1.1's `CancelToken` is cooperative-only (`M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`
   §6). Whichever framework is chosen, M2's ADR should state explicitly
   whether M2 targets full barge-in or (like legacy) a safer wake-gated
   reopen as a first step.

## 9. What this research does NOT do

- Does not choose between A/B/C — that is an ADR decision for the owner.
- Does not implement any M2 code.
- Does not run a real audio round-trip through either framework.
- Does not resolve the pre-existing Ollama blob-store permission blocker
  (unrelated, already tracked in `docs/CURRENT_STATE.md`).

## EXTERNAL RESEARCH USED

YES — pipecat-ai/pipecat and livekit/agents source/docs (GitHub, official
reference docs), faster-whisper/whisper.cpp/Silero VAD/Piper/Kokoro
GitHub repos and license files, external Pi 5 benchmark articles (weighted
below legacy's own real measurements wherever both exist).

## LEGACY NEXA USED

YES — read-only. `var/reports/asr_benchmark_sweep_20260518-225531.json`,
`var/reports/asr_runtime_decision_20260519-000000.md`,
`docs/NeXa_RuNTiMe_FiX_and_Update/31_...md`, `32_...md`, `162_...md`,
`164_...md`, `docs/legacy/LEGACY_NEXA_INDEX.md`. Not modified.
