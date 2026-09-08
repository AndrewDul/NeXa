# ADR-0003 — Realtime voice foundation (M2)

- **Status:** Accepted (architecture, framework choice, component choices,
  concurrency/sequencing rule, barge-in target). STT/TTS/VAD component
  choices are **initial baselines, not frozen** — see D4/D6 below (mirrors
  ADR-0002 D4's "baseline, not frozen" pattern for the M1.1 model).
- **Date:** 2026-09-05
- **Deciders:** Andrzej Dul (owner), Claude Code (agent)
- **Related:** `docs/research/M2_REALTIME_VOICE_RESEARCH.md`,
  `docs/reports/R0005_m2_realtime_voice_oss_research_20260905.md`,
  `docs/research/M2_VOICE_FEASIBILITY_SPIKES.md`,
  `docs/reports/R0006_m2_voice_feasibility_spikes_20260905.md`,
  `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md` (D1–D3 provider
  abstraction pattern extended here to voice; `ConversationSession` this ADR
  wires into, unchanged), `docs/ROADMAP.md` (M2)

---

## Context

M2 begins with two evidence-gathering steps already complete, per the
project's research-before-architecture policy (`RESEARCH_POLICY.md`,
AGENTS.md §3.13):

- **`R0005` / `M2_REALTIME_VOICE_RESEARCH.md`** — open-source-first survey of
  Pipecat, LiveKit Agents, and local STT/TTS/VAD candidates. `VERIFIED FACT`:
  both Pipecat (`FrameProcessor`, BSD-2-Clause) and LiveKit Agents
  (`Agent.llm_node`, Apache-2.0) have a clean integration seam that lets an
  external conversation authority stay in control of history/persona/model
  choice — neither forces NeXa into a second brain. `VERIFIED FACT`
  (independently unpacked the actual wheel): LiveKit Agents' *default*
  bundled local VAD/turn-detector model weights carry a genuine
  framework-lock proprietary license ("LiveKit Model License Agreement" —
  usable only inside LiveKit Agents); the open equivalent
  (`livekit-plugins-silero`, MIT) must be explicitly substituted. `VERIFIED
  FACT`: Piper's actively-maintained successor (`OHF-Voice/piper1-gpl`) is
  GPL-3.0 (original MIT `rhasspy/piper` is archived). `VERIFIED FACT`
  (independently reproduced): Pipecat's optional `[local-smart-turn]` extra
  depends on `coremltools`, whose native modules (`libcoremlpython`,
  `libmilstoragepython`) are macOS-only and fail to load on this Linux/ARM64
  Pi.
- **`R0006` / `M2_VOICE_FEASIBILITY_SPIKES.md`** — real, same-hardware
  measurement. `VERIFIED FACT`: whisper.cpp `base` beats legacy's
  faster-whisper `base` on **both** accuracy and speed even at a matched
  beam size (0.354 vs. 0.537–0.558 avg WER; 1972–2191 ms vs. 3486–3575 ms).
  `base/q8_0` matches `base/fp16`'s accuracy exactly (0.354 avg WER) at
  ~1688 ms and ~221 MB peak RSS — the best measured local STT
  config on this hardware. `VERIFIED FACT`: naive full concurrency (VAD +
  STT + `gemma4:e4b` generating + TTS, all started at once, uncoordinated
  thread budgets) reproduces and quantifies legacy's known CPU-contention
  failure precisely — VAD ~73×, STT ~4.75×, TTS ~2.9×, LLM
  time-to-first-token ~2.9× slower — while RAM/swap/thermal never became the
  bottleneck (min free RAM ~3.8 GB, swap <70 MB, zero throttling, peak
  64.8°C) and the LLM's own steady-state token throughput barely moved
  (~2%). Classified **`LOCAL_FEASIBLE_WITH_TUNING`**. `VERIFIED FACT`:
  auto-language-detection reproduces the same short-Polish-utterance
  misdetection on whisper.cpp that legacy hit on faster-whisper — an
  engine-independent failure mode, not a faster-whisper quirk.

**The owner's open-source-first policy** (this task, and already the
project's stated direction): reuse → adapt → patch → fork → build native, in
that order. **The architectural ownership rule** (unchanged since M1.1,
carried forward explicitly): an external framework may own audio
capture/playback, VAD, turn detection, interruption mechanics, transport, and
metrics; it must never become the canonical NeXa brain.
`ConversationSession` (`src/nexa/conversation/session.py`) remains the sole
owner of conversation history, persona, and model/provider choice — voice is
a new **input/output modality** onto the same conversation, not a second one
(`AGENTS.md` §0, `FOUNDATION_ARCHITECTURE.md` "Interaction": "a mode is a
front-end onto the Conversation boundary, not its own brain").

This ADR makes the M2 architecture decision that evidence now supports. It
does **not** implement the M2 runtime — that is M2.1 onward, each its own
task.

## Decision

**We will build M2's local realtime voice pipeline as Pipecat-orchestrated
plumbing around the existing, unchanged `ConversationSession`**, using
Silero VAD and whisper.cpp locally, with Piper as an explicitly temporary TTS
baseline, explicit pipeline sequencing and thread budgets, full barge-in as
the target (with new coordination work named, not yet built), and LiveKit
deferred — not rejected — to a later cross-device transport stage.

### D1 — Pipecat is the M2 local voice orchestration framework (Accepted)

Pipecat (`pipecat-ai/pipecat`, BSD-2-Clause) owns audio capture/playback
transport, frame plumbing, and VAD/turn-detection wiring. Chosen over LiveKit
Agents for the **first local M2 implementation** because:

- Its `LocalAudioInputTransport`/`LocalAudioOutputTransport` (PyAudio-based)
  give direct local mic/speaker I/O with **no server, no WebRTC, no cloud
  credential surface** — the simplest fit for a single-device Pi stage
  (`M2_REALTIME_VOICE_RESEARCH.md` §5.1).
- Every dependency is open (BSD-2-Clause throughout); LiveKit Agents' local
  path is otherwise comparably good, but its *default* bundled VAD/turn model
  weights carry the real, confirmed framework-lock license from `R0005`
  §5.2/§8 item 5 — avoidable, but an extra thing to get right by
  configuration rather than by default. Pipecat has no equivalent proprietary
  weight in its default path.
- Confirmed base install works on this exact Pi 5, ARM64, no PyTorch pulled
  in by the base framework (`M2_REALTIME_VOICE_RESEARCH.md` §5.1, ~668 MB
  venv, independently re-verified twice).

This is a **framework-for-the-first-implementation** choice, not a
"Pipecat forever" one — see D9 (LiveKit) and the "Compliance / review"
section.

### D2 — `ConversationSession` remains the sole conversation authority; a thin NeXa adapter is the only new wiring (Accepted)

Pipecat receives one new component: a **NeXa-owned `FrameProcessor`
subclass** (conceptually, `NeXaConversationFrameProcessor` — named here for
the M2.3 substage to build, not implemented in this ADR) that:

- receives a `TextFrame` once STT has produced final text for a turn,
- calls `ConversationSession.send(text) -> AsyncIterator[str]` — the exact
  same method, same persona, same history, same `ModelProvider`
  (`LocalModelProvider` / Ollama / `gemma4:e4b`) that typed chat already uses
  (`src/nexa/conversation/session.py`, unchanged),
- pushes the streamed chunks back downstream as `TextFrame`s toward TTS.

Pipecat's own `LLMContextAggregator`/`LLMService` conveniences (built for its
first-party LLM integrations) are **not used** — confirmed unnecessary in
`R0005` §5.1: a custom `FrameProcessor` does not have Pipecat's internal
conversation-history object forced onto it. `ConversationSession.history`
stays the one, unbounded, real transcript; `ConversationContext.build(...)`
stays the one bounded per-request window. **No second history, no second
persona, no second context, no second model/provider choice, no second
"final answer" path is created by this ADR.** Typed chat (`apps/nexa_chat.py`)
and voice both terminate in the same `ConversationSession` instance shape —
literally the same class, configured the same way, per session.

### D3 — Silero VAD is the local VAD (Accepted, `USE AS-IS`)

Every research pass agrees (`R0005` §5.5, `R0006` §2.5/§3): MIT-licensed,
"zero strings attached," already proven on this exact hardware class by
legacy, and — critically — whisper.cpp's own **native ggml Silero VAD**
integration (`models/download-vad-model.sh silero-v5.1.2`, an 860 KB model,
no PyTorch) is what `R0006`'s Spike 2 actually measured. **Same VAD model,
via whichever concrete binding M2.1 wires into Pipecat's VAD slot** — Pipecat
ships Silero VAD as its default/first-party option, so no substitution
decision is needed the way LiveKit Agents' default requires one (D1).

### D4 — whisper.cpp `base/q8_0` is the initial M2 local STT baseline — baseline, NOT frozen (Accepted)

Measured on the exact same 12 legacy PL/EN recordings (`R0006` §2):
**0.354 avg WER (EN 0.100, PL 0.536), ~1.69 s average latency, ~221 MB peak
RSS** — matching `base/fp16`'s accuracy exactly at ~23% lower latency and
~22% lower RAM. This **beats legacy's faster-whisper `base`** on the same
material at a matched beam size (0.537–0.558 avg WER, 3.49–3.58 s) on both
accuracy and speed — not assumed, measured (`R0006` §2.3). `tiny/q8_0` is
faster (788 ms) but meaningfully less accurate, especially in Polish (PL WER
0.607 vs. 0.536); `small` is excellent (0.0 WER on the 3-file spot check)
but ~7 s/utterance, still impractical for conversational turn latency,
confirming legacy's own conclusion on a different runtime.

**Not frozen**: `PATCH-EXTEND` candidates remain open and explicitly
un-superseded by this decision —
- **NVIDIA Parakeet/Canary**: `R0006` §5 resolved both prior `UNKNOWN`s to
  `PASS` (CC-BY-4.0 license, Polish explicitly supported) but the hardware
  path (multi-GB NeMo conversion) is unbenchmarked. Worth a scoped follow-up
  spike; not required to ship M2.1–M2.3.
- **Hailo Whisper offload**: still unproven (`R0005` §8 item 3) — the only
  STT path that wouldn't compete with `gemma4:e4b` for CPU, so worth
  revisiting if CPU contention (D7) proves harder to tune away than expected.
- **Vosk**: real prior art (legacy used it as a fast-path bilingual command
  layer) but **only for a narrow constrained-grammar fast path** (e.g. a
  small set of wake-word confirmations or built-in commands), never as the
  primary natural-conversation STT (`M2_REALTIME_VOICE_RESEARCH.md` §5.3a).

A change to the STT baseline follows the same discipline as ADR-0002 D4: new
evidence, then a superseding ADR revision — not a silent swap, and no such
change is made here.

### D5 — Explicit PL/EN language-hint strategy; naive auto-detect is rejected (Accepted)

`R0006` §2.5 reproduced the exact same short-Polish-utterance misdetection
(garbled Japanese-script output) on whisper.cpp that legacy hit on
faster-whisper — engine-independent, not a faster-whisper bug. **M2 must not
call STT in `auto` language mode as its normal path.** The concrete strategy
(a per-session configured default language, a UI/wake-word-driven switch, or
running both hints and arbitrating) is implementation detail for M2.2 — this
ADR fixes the *requirement*, not the mechanism.

### D6 — Piper is a temporary/technical local TTS baseline, explicitly NOT frozen (Accepted)

Piper (invoked as a **subprocess**, never an in-process Python import —
`R0005` §5.4's GPL-3.0 successor risk (`OHF-Voice/piper1-gpl`) makes
subprocess isolation a real requirement, not a style preference) is the M2.1
integration reference:

- **Why it's the baseline**: works in Polish today (legacy's real
  `pl_PL-gosia-medium.onnx` voice, reused read-only for `R0006`'s
  measurements), already proven on this exact Pi, trivial subprocess
  integration (legacy already did this successfully).
- **Why it is explicitly not the final answer**: measured real latency on
  this hardware (`R0006` §3.1, stage D, and `R0005` §3/§5.4) is
  **~3.4 s solo / ~10 s under worst-case contention** for a short Polish
  sentence — too slow for the natural-conversation feel M2 is ultimately
  aiming for. Its actively-maintained successor is GPL-3.0, a license the
  subprocess boundary manages but does not make disappear as a standing
  concern. **Piper is recorded here as an integration/technical reference
  point, not a destination** — a better local TTS (streaming synthesis,
  lower latency, a cleaner license) is an explicit, expected, open
  optimization/replacement point for a later M2 substage or ADR revision,
  same pattern as D4.

### D7 — Pipeline sequencing and explicit CPU-thread budgets are architectural requirements, not implementation details (Accepted)

`R0006` §3.2 **proved** naive full concurrency is unacceptable on this
hardware (3×–73× degradation) while also proving RAM/thermal are not the
constraint and the LLM's own steady-state throughput barely degrades. The
normal-turn shape this ADR requires:

```
VAD/STT finish  →  LLM generation begins  →  TTS may overlap
                                              only the LLM's *next*
                                              sentence while narrating
                                              an already-completed one
```

Concretely: **VAD and STT for the user's turn must not run concurrently with
active LLM generation** — there is no reason for them to (the user isn't
speaking while the assistant is composing an answer, outside of barge-in,
D8). **TTS overlapping LLM generation is allowed and expected** — `R0006`
stage F (LLM+TTS only, no simultaneous VAD/STT) showed only mild degradation
(TTS +19%, LLM TTFT still 4.36 s warm), a realistic and acceptable turn
shape, unlike stage H's deliberate worst case.

**Explicit thread budgets are required** once more than one component may be
concurrently active (i.e., during TTS-overlapping-generation) — no component
may assume it owns all 4 cores when another is also running. The exact
budget split (e.g. LLM gets N threads, TTS gets 4-N) is **implementation
work for M2.6**, not decided by this ADR; what this ADR fixes is that an
explicit, designed budget is required, not an accident of whatever each
library defaults to.

### D8 — Full barge-in is the M2 target; new coordination work beyond M1.1's `CancelToken` is required (Accepted as target; not yet built)

Full barge-in — the user interrupts NeXa mid-speech and NeXa responds to the
new utterance, not the old one — is the explicit M2 target (not the safer
wake-gated-reopen legacy settled for; legacy's own barge-in code existed but
was disabled by default and never validated, `R0005`/`M2_REALTIME_VOICE_RESEARCH.md`
legacy evidence). The required sequence:

```
user begins speaking while NeXa is speaking
  → VAD detects user speech during TTS playback
  → stop TTS playback immediately
  → flush any queued/not-yet-played assistant audio
  → mark the in-flight assistant turn as interrupted
  → stop LLM generation if still in progress
  → prevent any already-generated-but-unspoken text from reaching TTS
  → begin STT on the new user utterance
  → feed the new text into the SAME ConversationSession via the same
    NeXaConversationFrameProcessor path (D2) — no special-cased second path
```

**M1.1's `CancelToken` does not fully solve this, and this ADR does not
pretend it does.** `CancelToken` (`src/nexa/providers/base.py`) is a
cooperative, per-request signal that stops a `ModelProvider.generate()` call
from yielding further chunks — it has no concept of TTS playback, no concept
of "this turn was interrupted" as opposed to "this turn completed
normally," and `ConversationSession.send()` today appends whatever text was
accumulated as a normal assistant turn regardless of *why* the loop ended
(`src/nexa/conversation/session.py` — confirmed by re-reading the M1.1
implementation while writing this ADR: a cooperative cancel that returns
cleanly, rather than raising, currently produces an assistant turn
indistinguishable from a completed one). **New coordination M2 will need,
named here for M2.5 to design, not decided in detail now**:

1. A session- or turn-level **interruption signal** distinct from
   `CancelToken`'s per-provider-call scope — something that can simultaneously
   stop LLM generation, stop/flush TTS audio, and prevent a partial reply
   from being silently recorded as if it were complete.
2. A decision (open, for M2.5, not this ADR) on **what happens to an
   interrupted turn in history**: keep the partial text tagged as
   interrupted (so context stays honest about what was actually said aloud),
   discard it, or something else — `ConversationTurn` (`src/nexa/conversation/turn.py`)
   has no "interrupted" concept today and this ADR does not add one; M2.5
   must decide before implementing barge-in, not silently pick one.
3. Coordination between **VAD-detects-speech-during-TTS-playback** (a new
   runtime state M1.1 has no concept of — "assistant currently speaking") and
   the STT stage that must start listening to the *new* utterance without
   also trying to transcribe NeXa's own TTS output. `INFERENCE`: the
   reSpeaker XVF3800 array legacy already uses has documented acoustic echo
   cancellation (AEC) capability, which may help here — not verified in this
   ADR, a concrete input for M2.5's design.

### D9 — LiveKit is deferred, not rejected (Accepted)

LiveKit Agents remains a strong, Apache-2.0, actively-maintained candidate
(`R0005` §5.2) with a **better** long-term fit for NeXa's eventual
multi-device vision (`AGENTS.md` §0: "devices are bodies/interfaces of the
same NeXa") — its WebRTC Room model is close to designed-for exactly that.
It is deferred from the **first local M2 implementation**, not rejected,
because:

- It is not necessary for a single-device Pi voice pipeline — Pipecat's
  local transport already covers this stage's actual requirement.
- **Installing two orchestration frameworks simultaneously for the first
  local implementation is rejected as unnecessary complexity** — one
  framework, one integration seam, one thing to debug.
- Its *default* bundled local VAD/turn-detector weights carry the confirmed
  proprietary framework-lock license (`R0005` §5.2/§8 item 5) — an avoidable
  dependency concern for a stage that doesn't need WebRTC at all yet.

**Future direction, recorded not built**: a later cross-device transport
stage can plausibly look like `remote device → LiveKit/WebRTC transport →
Pipecat/NeXa local voice runtime → ConversationSession` — LiveKit as the
*transport* layer feeding the same local pipeline this ADR establishes,
**without changing the NeXa brain**. This is a future ADR's decision, not
this one's; nothing here forecloses it.

### D10 — Pipecat's `local-smart-turn` extra is rejected on this hardware (Accepted)

`R0005` §5.1 (independently reproduced twice, once via a research sub-agent
and once directly by the orchestrating agent): `pipecat-ai[local-smart-turn]`
pulls in full PyTorch + `coremltools`, and `coremltools`'s native modules
(`libcoremlpython`, `libmilstoragepython`) are macOS-only — confirmed to fail
to load on this Linux/ARM64 Pi (`import coremltools` reproduces the exact
load failure). This does not affect Pipecat's base framework or its default
Silero-VAD path (D3), which stay light (no PyTorch). Turn detection for M2
uses VAD-based endpointing (Silero, via Pipecat's default wiring), not this
extra.

### D11 — Voice components stay behind NeXa-owned replaceable interfaces where useful (Accepted, principle — not implemented here)

Extending ADR-0002 D2's pattern (a `ModelProvider` interface so
`ConversationSession` never knows Ollama-specific detail): M2's
implementation substages should define analogous small interfaces for the
voice-specific pieces that are genuinely likely to change — an STT
boundary and a TTS boundary at minimum, so swapping `whisper.cpp` for a
future Parakeet/Hailo path (D4) or Piper for a future TTS engine (D6) is a
configuration change behind a stable interface, not a rewrite. **Not
designed or implemented in this ADR** — this is explicitly M2.2/M2.4 work.
VAD and the Pipecat transport itself are not given the same treatment here:
D3's evidence base is unanimous enough (every research pass, `USE AS-IS`)
that a replaceable-VAD interface is not yet a demonstrated need; this can be
revisited if that changes.

## Options considered

### Option A — Pipecat only (chosen for the first local M2 implementation)
- Pros: no server/WebRTC/cloud-credential surface; fully open dependency
  tree in its default path; confirmed working base install on this Pi;
  clean `FrameProcessor` integration seam.
- Cons: less first-party local-STT/TTS plugin coverage than LiveKit's
  ecosystem implies (though neither ships first-party whisper.cpp/Piper
  plugins — both need the same custom-wrapper work, `R0005` §7).

### Option B — LiveKit Agents only
- Pros: cleanest custom-LLM override point (`llm_node` returning a plain
  `AsyncIterable[str]`), most active maintenance, real local `console` mode
  today, best future cross-device fit.
- Cons: default bundled local VAD/turn-detector model weights are
  genuinely proprietary and framework-locked (confirmed by direct wheel
  inspection) — avoidable, but a default that must be actively overridden;
  no cross-device requirement is active yet, so its main advantage over
  Option A isn't needed at this stage.

### Option C — Pipecat + LiveKit transport together
- Pros: would give local voice orchestration and future WebRTC transport
  in one build.
- Cons: **premature** — `R0005` §7 explicitly: no cross-device requirement
  is active (ROADMAP lists multi-device coordination under "Later," not M2);
  two orchestration frameworks at once is unnecessary complexity for a
  single-device stage. Rejected for now, not permanently (see D9).

### Option D — Build a native NeXa voice pipeline from scratch
- Rejected outright, per the owner's explicit open-source-first policy and
  the evidence: legacy already tried something close to this (a large,
  custom voice stack) and its own failure mode — **ending up with two
  separate, non-unified "brains"** (typed chat's MAS/router vs. voice's own
  direct-to-Ollama path with its own history) — is exactly what this ADR's
  D2 and the project's ownership rule exist to prevent from recurring. No
  responsibility in `R0005`'s make-vs-build table was recommended
  `BUILD NATIVE`.

## Consequences

**Positive**
- Voice becomes a second interaction mode onto the *same* conversation,
  structurally — not a parallel product with its own memory of the user.
- Every component chosen (Pipecat, Silero VAD, whisper.cpp) has been
  measured, not assumed, on this exact hardware and material.
- The CPU-contention failure mode that has haunted this project since legacy
  is now quantified and has a concrete architectural answer (sequencing +
  thread budgets, D7) instead of being an unmeasured risk.
- Piper and whisper.cpp's baseline status is stated honestly, with named
  replacement paths (D4, D6), avoiding the trap of quietly treating an
  integration reference as a permanent decision.

**Negative / costs**
- Piper's ~3.4 s (solo) / ~10 s (contended) latency for short utterances is a
  real, accepted cost of shipping M2.1–M2.4 before a better TTS is chosen —
  natural conversational rhythm will suffer until D6 is revisited.
- Full barge-in (D8) is now a stated target with real, non-trivial new
  coordination work ahead (interruption signal, turn-history semantics,
  TTS-echo-vs-new-speech disambiguation) that no existing NeXa code
  provides yet — a real scope item for M2.5, not a small addition.
- Deferring LiveKit (D9) means the cross-device voice story is not designed
  yet, only kept open architecturally.

**Follow-up work this creates**
- M2.1–M2.7 (see "M2 substages" below).
- A scoped Parakeet/Canary conversion + benchmark spike (D4), not blocking.
- A future TTS replacement decision (D6) — own ADR revision when evidence
  supports a specific successor.
- A future cross-device transport ADR that may bring LiveKit back in as a
  transport layer under this same pipeline (D9).

**What this constrains for future milestones**
- No later M2 substage may introduce a second conversation history, persona,
  or model/provider decision point for voice — `ConversationSession` stays
  singular, per AGENTS.md §3.2/§3.3 and this ADR's D2.
- Any future STT/TTS/framework swap is a configuration or interface change
  behind D11's boundaries (once built), evidenced and reviewed — not a
  silent default change, mirroring ADR-0002's D4 discipline.

## Compliance / review

- Code review for every M2.1+ substage checks: exactly one path from voice
  input to `ConversationSession.send()`; no parallel history/persona/model
  choice for voice; VAD/STT do not run concurrently with active LLM
  generation in the normal (non-barge-in) turn shape (D7); Piper is invoked
  as a subprocess, never imported in-process (D6); no LiveKit Cloud
  credentials are configured anywhere in the local M2 path (keeps D9's
  deferral honest); language hints are passed explicitly to STT, never
  `auto` in the normal path (D5).
- **Revisit D1 (Pipecat)** if a cross-device transport requirement becomes
  active (ROADMAP "Later" → an actual milestone) — that is D9's trigger for
  bringing LiveKit back in as a transport layer, via a new/superseding ADR.
- **Revisit D4 (whisper.cpp `base/q8_0`)** if the Parakeet/Canary or Hailo
  offload follow-up spikes produce evidence of a better accuracy/latency/CPU
  trade-off, per the same discipline as ADR-0002 D4.
- **Revisit D6 (Piper)** once a lower-latency, cleaner-licensed local TTS
  candidate is evaluated — this is expected, not a failure of this ADR.
- **Revisit D7's thread-budget numbers** once M2.6 has real pipelined
  (not worst-case-concurrent) measurements — this ADR fixes the requirement
  for a designed budget, not the specific split.

---

## M2 substages (recommended shape; refined from the owner's proposal with the reasoning noted)

| Substage | Scope |
|---|---|
| **M2.1** | Pipecat foundation: local audio transport (mic/speaker), Silero VAD wired in. Thread-budget discipline starts *here*, not deferred to M2.6 — see note below. |
| **M2.2** | whisper.cpp STT adapter (`base/q8_0` default, D4) + explicit PL/EN language-hint strategy (D5). |
| **M2.3** | `NeXaConversationFrameProcessor` (D2): voice → the same, unchanged `ConversationSession`. No second history/persona/model choice. |
| **M2.4** | Streaming/chunked TTS integration (Piper via subprocess, D6), sentence/chunk-boundary triggering so TTS can start narrating before the full LLM reply is generated. |
| **M2.5** | Full barge-in / interruption coordination (D8) — the new signal, the turn-history-on-interruption decision, and the TTS-echo-vs-new-speech disambiguation, all designed here, not assumed solved by M1.1's `CancelToken`. |
| **M2.6** | Systematic latency + thread-budget tuning against a real, sequenced pipeline (not the worst-case-concurrent stress test `R0006` used to find the ceiling) — produces the concrete thread-split numbers D7 leaves open. |
| **M2.7** | Operator natural-voice acceptance test — the voice-mode analogue of M1.1's operator-confirmed blind text test, before any voice baseline is treated as "done." |

**One refinement to the owner's proposed shape**: explicit thread-budget
*discipline* (not letting any one component default to claiming all 4 cores)
should be a running constraint starting at **M2.1**, not something introduced
only at M2.6 — `R0006`'s worst-case numbers came partly from components
naively requesting 4 threads each with no coordination from the start.
M2.6 remains the stage for *systematic, measured* tuning once the full
pipeline exists, but M2.1–M2.5 should not build in the naive pattern and then
need to unlearn it.

---

## Amendment 1 — M2.4B.5: guarded automatic PL/EN voice input (2026-09-08)

**Status: Accepted.** Supersedes **part of D5** ("M2 must not call STT in
`auto` language mode as its normal path"). D5's *requirement* — never let a
raw multilingual auto-detect decide the decode language unchecked — stands;
its *mechanism* ("explicit per-session `--language`") is no longer the only
accepted production path. Evidence authority: **`R0024`** (50-utterance
real-operator corpus benchmark) and **`R0025`** (this implementation).

### What changes

- **Explicit fixed per-session language is still fully supported** and
  remains the plain `WhisperCppTranscriber` path (`apps/nexa_stt_probe.py`,
  the M2.2/M2.4 probes). Nothing about it changes.
- **Guarded automatic per-utterance PL↔EN is now accepted production
  behaviour**, via `nexa.stt.BilingualSpeechTranscriber` behind the
  existing `SpeechTranscriber` boundary (D11):
  1. library-level detection (`WhisperCppLanguageDetector`, a `ctypes`
     binding to the **pinned** `libwhisper.so` — no fork, no version
     change) yields `p_pl` / `p_en` plus whisper's raw top-1 language;
  2. one `LanguageIdGuard` authority decides **AUTO_ACCEPT** (trust the
     detector's PL/EN call) or **FALLBACK_REDECODE** (do not), using the
     constrained `argmax(p_pl, p_en)`, the raw language, a configurable
     confidence threshold (**0.60 — R0024 initial calibration, not a
     universal constant**), and a duration floor;
  3. exactly **one** explicit whisper.cpp decode runs, in the
     guard-selected PL/EN language. The wrong-language transcript is never
     generated, so there is nothing unsafe to reject.
  R0024: whisper.cpp `v1.9.3` had **0 PL↔EN confusion** in 30 monolingual
  utterances; the constrained `argmax` classified all 30 correctly.
- **Ambiguous / very short utterances** (R0024: sub-2 s, acoustically
  unreliable — "Tak." → `en` "Talk.") take the FALLBACK path: the decode
  language is the **session-local `last_input_language`** (`pl` | `en` |
  `None`), or a documented deterministic bootstrap on turn 1 — never a
  guess, never a third language. `last_input_language` is transient
  session state, **not** a conversation turn and **not** long-term memory.
- **`InputSpeechLanguage` ≠ `ResponseLanguage`.** A separate authority,
  `nexa.conversation.ResponseLanguageResolver`, decides the reply
  language: mirror the spoken language by default; an explicit request
  ("Answer in English.", "Odpowiedz po polsku.", "Od teraz mów po
  angielsku.", "Wracamy do polskiego.") switches it and sets a transient
  sticky session preference. The Piper voice maps from `ResponseLanguage`,
  not from the STT decode language. The LLM is never the sole
  language-routing authority.

### What does NOT change

- **True within-utterance code-switch is NOT guaranteed.** R0024: `-l
  auto` was 6/10 USABLE, 0 BROKEN on real mixed utterances — tolerable as
  a side-effect. B.5 adds no dual-decode merging and only guarantees mixed
  input is **not worse** than the R0024 single-language-per-utterance
  baseline. A real solution is a separate future stage.
- One `ConversationSession` / history / persona / model / provider
  (D2) — unchanged. `gemma4:e4b`, `num_thread=2`, `keep_alive=30m`, the
  B.3.6 warm-up — unchanged. No second Polish/English session, no
  per-language or fallback LLM.
- D4 (`base/q8_0` STT model, `-t 4`) — unchanged. B.5 uses the same model
  and thread count; the Polish *transcript-quality* deficit (R0024: PL S2
  ~13 %) remains its own separate track (`R0016`).
- No cloud STT. Local-first.

### Revisit triggers

- Tune the guard's `confidence_threshold` / duration floor from
  accumulated per-turn telemetry (`TranscriptionResult.language_decision`)
  once real multi-session operator evidence exists — a config change, not
  an ADR revision.
- Open a dedicated within-utterance code-switch stage if mixed input
  proves to matter in real use.
