# M1 Natural Conversation — Research & Empirical Baseline

Milestone: **M1.0 — Research & Empirical Baseline.** No product code. Companion
to `docs/reports/R0002_m1_natural_conversation_research_20260831.md` and
`docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md`.

Truth labels per `AGENTS.md` §4: `VERIFIED FACT`, `OBSERVATION`, `INFERENCE`,
`ASSUMPTION`, `HYPOTHESIS`, `PROPOSAL`, `UNKNOWN`.

Date: 2026-08-31. Host: Raspberry Pi 5 16GB (`nexa`).

---

## 1. Executive conclusion

`PROPOSAL`, grounded in the evidence below:

1. **First runtime: Ollama** (already installed, running, OpenAI-compatible,
   model-managed) as the first concrete implementation of a NeXa
   `LocalModelProvider`. `llama.cpp` (`llama-server`) is the designated second
   implementation and portability path. NeXa does **not** bind to either — both
   sit behind one provider interface.
2. **First baseline model: `qwen3:4b-instruct`** (Qwen3-4B-Instruct-2507,
   Q4_K_M, Apache-2.0) — already local, strongest realistic 4B, genuinely
   multilingual, 262k native context, non-thinking instruct. **Confirmed by
   measurement against Bielik-4.5B (§12A), with one caveat:** its Polish quality
   is weak (agent-assisted 2/5) and M1.1 must improve it via the system persona
   and a fair Bielik re-test. The baseline is not frozen.
3. **Challenger benchmarked: Bielik-4.5B-v3.0-Instruct** (SpeakLeash, Apache-2.0,
   Polish-specialised). Measured result: **better Polish *language* (3/5 vs 2/5),
   but ~2× slower (2.0 vs 3.9 tok/s), 8k context vs 262k, truncates answers,
   weaker English (2.5/5), and no official Q4_K_M.** Net: not the M1.1 baseline
   today, but a real M1.1 re-test candidate at its recommended settings.
   `llama3.2:3b` / `qwen2.5:1.5b` remain speed/English controls (perf only).
4. **Pi feasibility:** `VERIFIED FACT` — a 4B Q4_K_M model runs at
   **~3.9 generation tok/s**, warm TTFT **~0.6 s**, cold load **~7.6 s**, peak
   ~67 °C, **no throttling**. Usable for streamed, considered replies; *marginal*
   for snappy chat-speed back-and-forth. **Measured: a 3B is only ~10–17 %
   faster on this Pi** (not the ~2× lore) — the only real speed step is down to
   1.5B (~2.4×). See §10.1 / §13.
5. **The legacy naturalness problem was primarily pipeline + grounding +
   latency, not model quality** (`INFERENCE`, strongly supported by legacy
   Report 176). NeXa M1 should therefore keep the turn path radically short and
   never let a deterministic pre-model layer answer with canned text.
6. **M1.1 minimal architecture:** `ConversationSession` → `ConversationContext`
   (in-session history only) → `ModelProvider` (streaming) → streamed reply.
   No router, no MAS, no verifier, no capability layer, no fallback model.
7. **Voice (M2) stays out of the Conversation System.** Pipecat looks like the
   better first Pi-local realtime prototype; both Pipecat and LiveKit remain
   candidates behind a `RealtimeVoiceAdapter` boundary — not chosen now.

---

## 2. Raspberry Pi — verified hardware / software state

All `VERIFIED FACT` unless noted, collected 2026-08-31 via `scripts/m1_bench/`
probes and standard tools.

### 2.1 Host

| Item | Value |
|---|---|
| Model | Raspberry Pi 5 Model B Rev 1.1 (`e04171`) |
| Hostname | `nexa` |
| OS | Debian GNU/Linux 13 "trixie" (13.6) |
| Kernel | `6.18.39+rpt-rpi-2712`, `aarch64` |
| CPU | ARM Cortex-A76 ×4, 1 thread/core, max 2.4 GHz (boost disabled), governor `ondemand` |
| CPU flags | `fp asimd aes pmull sha1 sha2 crc32 atomics fphp asimdhp asimdrdm lrcpc dcpop asimddp` (no SVE, no i8mm/bf16) |
| Cache | L1 64K/64K ×4, L2 2 MiB ×4, L3 2 MiB |
| RAM | 15 GiB total; ~13 GiB available at rest; ~9 GiB available with a 4B model resident |
| Swap | 2 GiB **zram** (`/dev/zram0`), unused at rest |
| Storage | `/dev/sda2` 917 GB (USB SanDisk Extreme SSD), **803 GB free**, ext4 |
| Load at rest | ~0.1–0.25 |
| Temp at rest | ~50 °C; `vcgencmd get_throttled` = `0x0` (never throttled) |
| Network | Wi-Fi `wlan0`, ~17 ms to 1.1.1.1, **github.com / huggingface.co reachable (HTTP 200)** — external research and model pulls are possible |

### 2.2 Accelerators — investigated, not assumed

| Device | Status for LLM conversation |
|---|---|
| **Hailo-10H AI Processor** (PCIe `0001:01:00.0`, `/dev/hailo0`, HailoRT 5.2.0, firmware 5.2.0) | **Present and GenAI-capable, but not usable as the M1 LLM path.** See §2.3. |
| **V3D GPU** (VideoCore VII, `V3D 7.1.10.2`, Mesa V3DV Vulkan 1.3.354) | **Not usable for llama.cpp/Ollama LLM acceleration.** `OBSERVATION`: the running Ollama has `OLLAMA_VULKAN=true` yet a probe generation ran `100% CPU`. `INFERENCE` (external sources): V3D Vulkan for llama.cpp is not production-ready — warp size 16, 16 KB shared memory, no integer dot product, no matrix cores; fails matmul shaders. CPU is the reproducible path. |
| Intel Movidius MyriadX (USB, OAK-D Lite camera) | Vision only, irrelevant to conversation. |
| reSpeaker XVF3800 4-Mic Array (USB) | Voice input hardware, relevant to M2 not M1. |

### 2.3 Hailo-10H GenAI — why it is not the M1 LLM path

`VERIFIED FACT` (local inspection of `~/hailo_model_zoo_genai`, git `v5.3.0`
`1a3ba6b`, and `docs/MODELS.rst`):

- The Hailo-10H **can** run LLMs via "Hailo-Ollama" (an Ollama-compatible C++
  server on HailoRT). Supported models are **all ≤ 1.7B params**, A8W4 quant,
  **context length fixed at 2048**.
- Hailo's **own published** figures (`OBSERVATION`, vendor-claimed, not measured
  here): Qwen3-1.7B-Instruct TTFT 0.62 s / **4.78 tok/s**; Llama3.2-1B 0.35 s /
  9.89 tok/s; Qwen2.5-1.5B 0.37 s / 7.35 tok/s.
- Whisper Tiny/Base/Small HEFs also exist for Hailo (`Whisper-Base` ~25 tok/s
  decode) — **genuinely interesting for M2 STT offload**, noted for later.
- Local blocker: only model *manifests* are present (no LLM `.hef` downloaded),
  and installed HailoRT is **5.2.0** while the precompiled GenAI models need
  **5.3.0**. Legacy NeXa hit the mirror-image of this: its `settings.json` still
  carries `"Hailo Ollama is disabled temporarily because the installed binary
  expects libhailort.so.5.1.1 but the system currently provides
  libhailort.so.5.2.0."` (`VERIFIED FACT`, legacy config).

`INFERENCE`: the Hailo-10H LLM path forces a ≤1.7B model at 2048 context and
~5–10 tok/s — **worse for natural conversation quality** than a 3–4B model on
CPU, with a fragile toolchain. It is a **future research item** (esp. Whisper
offload for M2, and re-evaluation if Hailo ships ≥3B support), **not** the M1
foundation.

### 2.4 Toolchain

`VERIFIED FACT`: `gcc`/`g++` 14.2.0, `cmake` 3.31.6, GNU `make` 4.4.1, `git`
2.47.3, Python 3.13.5. No `clang`, no `ccache`. Sufficient to build `llama.cpp`
from source (legacy already did — see §5).

`OBSERVATION`: the login shell's `python3`/`pip` currently resolve into the
**legacy** repo's `.venv` (`/home/devdul/Projects/smart-desk-ai-assistant/.venv`).
NeXa must always use its own `./.venv`. A clean `PATH` gives system
`/usr/bin/python3` 3.13.5.

---

## 3. Existing local AI runtimes (verified inventory)

| Runtime | Installed | Version | Path | State | Owner |
|---|---|---|---|---|---|
| **Ollama** | Yes | 0.30.10 | `/usr/local/bin/ollama` | **systemd `ollama.service` active**, `127.0.0.1:11434`, `OLLAMA_VULKAN=true`, models in `/usr/share/ollama/.ollama/models` (25 blobs) | system-wide |
| **llama.cpp** | Yes (built) | libllama `b9203`-era, upstream commit `87589042c` (2026-05-17) | `~/Projects/smart-desk-ai-assistant/llama.cpp/build/bin/{llama-cli,llama-server}` | builds present, **CPU-only** (no `libggml-vulkan`), ~3.5 months old | **legacy repo** |
| **whisper.cpp** | Yes (built) | Apr 2026 | `…/smart-desk-ai-assistant/whisper.cpp/build/bin/{whisper-cli,whisper-server,whisper-bench,…}` | built | legacy repo |
| faster-whisper | Yes | 1.2.1 | legacy `.venv` | importable | legacy `.venv` |
| CTranslate2 | Yes | 4.7.1 | legacy `.venv` | — | legacy `.venv` |
| Piper TTS | Yes | 1.4.2 | legacy `.venv` (`piper` binary) + `voices/piper/*.onnx` (pl gosia, en jenny) | — | legacy `.venv` |
| openWakeWord | Yes | 0.4.0 | legacy `.venv` + `models/wake/nexa.onnx` | — | legacy `.venv` |
| Silero VAD | Yes | 6.2.1 (pip) | legacy `.venv` | — | legacy `.venv` |
| ONNX Runtime | Yes | 1.24.4 | legacy `.venv` | — | legacy `.venv` |
| PyTorch / torchaudio | Yes | 2.11.0 | legacy `.venv` | — | legacy `.venv` |
| Vosk | Yes | 0.3.45 | legacy `.venv` | — | legacy `.venv` |
| HailoRT + `hailo_platform.genai` | Yes | 5.2.0 (GenAI zoo v5.3.0) | system `dpkg` + `/usr/lib/python3/dist-packages/hailo_platform/genai` (`LLM`, `Speech2Text`, `VLM`) | version-mismatched vs models (see §2.3) | system-wide |
| MLX | No (Apple-only) | — | — | — | — |
| vLLM | No | — | — | — | — |

`INFERENCE`: **Ollama is the only turnkey local LLM server on this machine**
(running, managed, API-served). `llama.cpp` exists only as a legacy-owned build.

---

## 4. Existing local model inventory (verified)

### 4.1 Ollama models (`ollama list` + `ollama show`, `VERIFIED FACT`)

| Model | Arch | Params | Quant | Native ctx | Disk | License | Notes |
|---|---|---|---|---|---|---|---|
| **`qwen3:4b-instruct`** | qwen3 | 4.0B | Q4_K_M | 262144 | 2.5 GB | **Apache-2.0** | non-thinking instruct (Qwen3-4B-Instruct-2507); caps: tools, thinking, completion; default `temp 0.7 / top_p 0.8 / top_k 20 / repeat_penalty 1` |
| `qwen2.5:3b` | qwen2 | 3.1B | Q4_K_M | 32768 | 1.9 GB | **Qwen RESEARCH** (non-commercial) | licensing risk for a product |
| `llama3.2:latest` | llama | 3.2B | Q4_K_M | 131072 | 2.0 GB | Llama 3.2 Community | Polish not an official language |
| `qwen2.5:1.5b` | qwen2 | 1.5B | Q4_K_M | 32768 | 986 MB | Apache-2.0 | latency floor / Hailo-tier control |
| `llama3.2:1b` | llama | 1.2B | Q8_0 | 131072 | 1.3 GB | Llama 3.2 Community | latency floor |
| `qwen2.5-coder:3b` | qwen2 | 3.1B | Q4_K_M | 32768 | 1.9 GB | Qwen RESEARCH | coding, not chat |

### 4.2 GGUF / other model files (`VERIFIED FACT`)

| File | Where | Note |
|---|---|---|
| `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` (~1.1 GB) | legacy `models/` **and** `~/Models/nexa/` | the model legacy `settings.json` `/llm` block points at (llama-server path) |
| `ggml-base.bin` | legacy `models/` | whisper base |
| `models--Qwen--Qwen2.5-1.5B-Instruct-GGUF` (1.1 GB) | `~/.cache/huggingface/hub` | HF cache |
| `faster-whisper-{tiny,base,small}` | `~/.cache/huggingface/hub` | STT |
| vocab-only `ggml-vocab-*.gguf` | legacy `llama.cpp/models` | not usable models |

`INFERENCE`: the only *conversational* LLMs actually present are the Ollama set.
The strongest already-local candidate for natural bilingual conversation is
`qwen3:4b-instruct` by a wide margin (newest architecture, Apache-2.0, real
multilingual, largest context).

---

## 5. Legacy NeXa findings (read-only; `~/Projects/smart-desk-ai-assistant`)

Sources: legacy git (`e12d018`, 630 commits, last 2026-08-24), and legacy reports
**164, 165, 173, 175, 176** (natural-conversation / core-brain / cutover series),
plus direct code inspection. Legacy labels: `FACT_CODE` = their code trace,
`FACT_RUNTIME` = their live measurement.

### 5.1 What the legacy conversation architecture is

`VERIFIED FACT` (legacy Reports 175/176 + code):

- **One canonical brain**: `modules/nexa_agents/typed_chat/runtime_adapter.py::
  process_typed_chat_via_mas` (**2573 lines**), shared by typed **and** voice.
- Turn pipeline (every stage via `.safe_run()`): `NexaInputAgent` →
  `TurnSupervisorAgent` → `MeaningAgent` → `ContextAgent` → inline deterministic
  fact/capability/time gates → (voice-only) `MASActionDispatcher` →
  `ReasoningAgent` → `ConversationAgent` (**the only LLM call site**) →
  `VerifierAgent` (deterministic) → optional Phase-B deterministic router.
- **Model**: `qwen3:4b-instruct` via **Ollama**, as the *sole fail-closed*
  user-facing model (legacy Report 173 deliberately removed silent downgrade to
  a smaller model). Routing via `route_model_with_fallbacks` /
  `ModelRouterAgent` (a thin wrapper).
- **Provider seam already exists**: `modules/nexa_agents/typed_chat/
  ollama_conversation_provider.py` (256 lines) — "never selects a model itself…
  returns an honest failed stage result — it never guesses a model by name",
  injected via `metadata["fast_provider"]/["deep_provider"]`. Good *contract*,
  but entangled with `cascaded_llm_answer`, domain routing, verifier, streaming
  protocol.
- **Chat history**: `ChatHistoryStore`, `MAX_CONTEXT_TURNS=8`,
  `MAX_CONTEXT_CHARS=3200`, opt-in per-conversation (`conversation_id`).
- **Long-term memory**: `MemoryBusStore` + writer/retriever, consent-gated (R22)
  — separate system from chat history. (Aligns with NeXa principle #8.)
- **Streaming**: sentence-mode, `live_llm_sentence_streaming_enabled`, first
  chunk 8–22 chars (legacy `settings.json`).
- Legacy `settings.json` `/llm` block (llama-server + `Qwen2.5-1.5B` + temp 0.55
  / top_p 0.9 / top_k 40 / **ctx 2048** / **timeout 10 s**) is **stale** — it
  does not describe what the MAS actually runs (Ollama + qwen3:4b). Recorded as a
  doc-vs-reality discrepancy.

### 5.2 What actually hurt naturalness — legacy Report 176 (live, 124 typed turns + 15 voice turns, 2026-08-24)

| Legacy blocker | Evidence | Cause class |
|---|---|---|
| **B1 — cascading total outage** | one slow-generation timeout (75 s general / 140 s if mis-classified "coding") → **every subsequent request** to the `chat_server` process fails until manual restart; 6 timeouts in 124 turns (4.8%; 7.9% of free-form turns); reproduced 5/5. Root cause: the timeout path **never cancels the abandoned pipeline future**, which corrupts in-process shared state. Report: *"this is not a model problem… reproduces identically regardless of which turn was slow."* | **Pipeline** (B) |
| **B2 — capability paraphrase blindness** | "Ustaw timer na 5 minut" routes correctly; "Odlicz mi pięć minut" falls through to the LLM which **confidently fabricates** "nie posiadam funkcji timerów" (false). 3/3 timer paraphrases falsely denied. Cause: capability detection fragmented across **5 non-communicating mechanisms**; on no exact match, `ConversationAgent` answers with **zero capability grounding**. | **Grounding / architecture** (C) |
| **B3 — short-input clarification loop** | "tak" / "nie" / "ten" → a deterministic `model=none` ~62 ms canned reply, **sometimes in English inside a Polish conversation**; never resolves against the pending question. Notably: "wiesz o co chodzi" *did* reach qwen3:4b and it **"correctly, honestly answered"** — *"the model itself CAN handle this gracefully when it's actually reached; the bug is in what intercepts shorter inputs before they get there."* | **Pipeline pre-empts model** (B/C) |
| Cross-language STT unreliability | faster-whisper `base`, `language=auto`: material transcription deviation on 4/5 PL and 3/5 EN physical voice turns | STT config (M2 concern) |
| Free-form latency | voice free-form turn **median 39–58 s**; one typed LLM turn TTFT **13.4 s**; 75 s/140 s hard timeouts discard in-progress replies | **Latency** (D) |
| Historical | ~90,000 lines of a dead parallel "Core Brain" framework deleted in Report 165; two structurally separate MAS systems (Report 164); cross-conversation context bleed bug; a routing bug that stopped short follow-ups ever reaching the model | **Pipeline complexity** (B) |

`INFERENCE` (high confidence): legacy naturalness suffered **mostly from B
(pipeline complexity), C (missing/late grounding + interceptors), and D
(latency)** — **not primarily A (model quality)**. The single strongest datum:
when the pipeline got out of the way, "the model itself … correctly, honestly
answered."

### 5.3 Legacy reuse matrix

| Legacy area | What exists | Evidence / path | Worked | Failed | Reuse verdict |
|---|---|---|---|---|---|
| Canonical-brain **principle** (one entry, no second path, fail-closed) | `process_typed_chat_via_mas` docstring; Report 173 removed silent downgrade | `typed_chat/runtime_adapter.py` | the *principle* | the 2573-line *implementation* | **REUSE CONCEPT**, reject code |
| **Provider contract shape** (`generate(prompt, route, stage, *, timeout, cancel_token, tool_evidence, on_chunk/stream_context)`; never guesses a model; honest `failed`) | `OllamaConversationProvider` | `typed_chat/ollama_conversation_provider.py` | contract design | entangled with cascade/verifier/domain-router | **REUSE CONCEPT** (interface only), reject code |
| Chat-history bounding (turns + chars cap, `conversation_id`-scoped) | `ChatHistoryStore` | Reports 175/176 | bounded context worked | opt-in gating caused a test to miss continuity | **REUSE CONCEPT** |
| History vs long-term memory **separation** | `ChatHistoryStore` vs `MemoryBusStore` | Report 165 §5 | separation upheld | — | **REUSE CONCEPT** (matches NeXa #8) |
| Sentence-streaming UX (small first chunk, sentence flush) | `settings.json /llm/stream_*` | legacy config | perceived latency win | — | **BENCHMARK** then adopt |
| Timeout handling | `_handle_client` future + hard cap | Report 176 B1 | — | **catastrophic**: no cancel → process-wide corruption | **REJECT** — M1 must cancel/detach cleanly, per-request state only |
| Deterministic pre-model interceptors (short-input, capability regex ×5, fast-lane) | 5 mechanisms | Report 176 B2/B3 | exact-phrase commands | paraphrases → false denials; canned wrong-language replies | **REJECT** for M1 (no pre-model canned answers) |
| MAS pipeline (Meaning/Context/Reasoning/Verifier agents around every turn) | `modules/nexa_agents/*` (~50 pkgs) | Reports 164/175 | — | latency, complexity, 90k-line dead sibling | **REJECT for M1** (MAS is opt-in later, NeXa #5) |
| `route_model_with_fallbacks` / `ModelRouterAgent` | model router | Report 165 §3 | thin wrapper | not needed at M1 (one model) | **DEFER** (revisit when >1 model) |
| llama.cpp / whisper.cpp **build knowledge** for Pi 5 aarch64 | working `build/bin` | legacy `llama.cpp/`, `whisper.cpp/` | builds succeed on this Pi | old, CPU-only | **REUSE KNOWLEDGE** (build flags), rebuild fresh in NeXa |
| Voice pipeline (openWakeWord gate, Silero VAD, Vosk PL/EN command grammar, faster-whisper/whisper.cpp, Piper pl/en) | built + configured | `modules/devices/audio/*`, `voices/piper/*` | pipeline runs | STT accuracy PL/EN (Report 176) | **REUSE KNOWLEDGE now, benchmark for M2** |
| Piper voices `pl_PL-gosia-medium`, `en_GB-jenny_dioco-medium` | ONNX files present | legacy `voices/piper/` | usable TTS | — | **REUSE ASSET later (M2)** |
| Hailo LLM (`Hailo-Ollama`) | attempted | legacy `settings.json` disabled-reason | — | libhailort version mismatch | **REJECT for M1**, revisit (esp. Whisper offload) |
| Benchmark harness idea (`scripts/benchmark_learn_models.py`, `benchmarks/voice/*`) | legacy scripts | legacy repo | produced numbers | tied to legacy runtime | **REUSE CONCEPT** (NeXa has its own `scripts/m1_bench/`) |

---

## 6. External research

Network available (§2.1). Sources listed in §21. Community/anecdotal evidence is
labelled and used only to steer local benchmarking, never as authoritative
numbers.

### 6.1 Local inference runtimes

- **llama.cpp** (`ggml-org/llama.cpp`, MIT): the de-facto portable local
  inference engine. CPU baseline on Pi 5: `OBSERVATION` (community) ~4–7 tok/s
  for a 3B Q4_K_M; 4B slower. **V3D Vulkan not production-ready** on Pi 5
  (`OBSERVATION`, GitHub issues): shared-memory / no-dot-product limits break
  matmul shaders → CPU is the reproducible path. Ships an OpenAI-compatible
  `llama-server`. First-class iOS (Metal via XCFramework/ObjC++), Android
  (JNI/NDK); reference apps: PocketPal AI, Llamatik (Kotlin Multiplatform),
  Cactus.
- **Ollama** (`ollama/ollama`, MIT; wraps llama.cpp): model registry + pull +
  auto-load/unload + keep-alive, native `/api/chat` **and** full
  OpenAI-compatible `/v1/chat/completions` with streaming and tools
  (`OBSERVATION`, Ollama docs). Runs as a service here already. Not a mobile
  runtime (server binary), but the *GGUF + llama.cpp core* underneath is.
- **vLLM**: production-grade serving, but GPU-oriented; not meaningful on this Pi.
- **Hailo GenAI**: see §2.3.

### 6.2 Model candidates (natural bilingual small chat)

| Model | Params | License | Context | Polish | Notes |
|---|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507** (`qwen3:4b-instruct`) | 4.0B | **Apache-2.0** | 262k native | 100+ languages, "substantial long-tail multilingual gains" (`OBSERVATION`, model card) | non-thinking; rec. `temp 0.7 / top_p 0.8 / top_k 20 / min_p 0` |
| **Bielik-4.5B-v3.0-Instruct** (SpeakLeash) | 4.5B | **Apache-2.0** | 8K (Ollama tag; `OBSERVATION`) | **Polish-native**: 292B-token Polish-heavy train, init from Qwen2.5-3B; on Open PL LLM Leaderboard ~40.67 (`OBSERVATION`, CodeSOTA) | official GGUF **Q8_0 / FP16 only** (no Q4_K_M); on Ollama; strongest Polish challenger |
| Gemma 3 4B-IT | 4B | Gemma license (commercial-OK, not OSI) | 128k | 140+ langs incl. Polish, no special focus | multimodal; license is a constraint for a product |
| Bielik-1.5B-v3.0-Instruct | 1.5B | Apache-2.0 | — | Polish-native | latency-focused Polish option / Hailo-tier |
| Qwen2.5-3B (`qwen2.5:3b`) | 3.1B | **Qwen RESEARCH (non-commercial)** | 32k | decent | **license disqualifies** for a product; control only |
| Llama-3.2-3B | 3.2B | Llama 3.2 Community | 128k | not an official language | English control |

### 6.3 Voice / future relevance (M2, not chosen here)

- **Pipecat** (`pipecat-ai/pipecat`, BSD-2-Clause): vendor-neutral pipeline
  framework; **best local-dev story** (runs on localhost, mic/file input); can
  use LiveKit as a transport; pipeline-level control. `OBSERVATION` (comparison
  articles): pick Pipecat for "phone / your own transport / pipeline control".
- **LiveKit Agents** (Apache-2.0): infrastructure-first, WebRTC "room" model,
  agent joins as a participant; strong for multi-party / video / multi-device
  presence; heavier, welded to the media server.
- **Silero VAD** (MIT), **whisper.cpp** (MIT, built-in Silero VAD now, runs on
  Pi/iOS/Android), **faster-whisper** (MIT, wins only on NVIDIA), **Piper**
  (MIT-ish, local). `OBSERVATION`: on a CPU-only box, whisper.cpp is the pick.
- Speech-native models (Moshi, Ultravox, Qwen Omni): interesting long-term;
  none realistic on this Pi now — out of scope.

### 6.4 Mobile / cross-device

- iPhone: llama.cpp (Metal) or MLX/MLX-Swift; GGUF portable.
- Android: llama.cpp (JNI/NDK); Kotlin Multiplatform wrappers exist.
- PC/laptop (Linux/Win/macOS): Ollama and `llama-server` run natively; a bigger
  local model or a "trusted PC provider" role is straightforward.
- `INFERENCE`: a GGUF-centric `LocalModelProvider` (Ollama today, `llama-server`
  next, an in-process llama.cpp binding on mobile) is portable across all target
  devices with **only the provider adapter changing**.

---

## 7. Model shortlist (≤ 4) and why

| # | Model | Runtime | Why shortlisted |
|---|---|---|---|
| 1 | **`qwen3:4b-instruct`** (Qwen3-4B-Instruct-2507, Q4_K_M) | Ollama | Already local; newest arch; Apache-2.0; real multilingual incl. Polish; 262k ctx; non-thinking instruct; already the legacy production model (continuity of evidence). **Primary M1.1 baseline.** |
| 2 | **Bielik-4.5B-v3.0-Instruct** (**Q8_0** — SpeakLeash publish no official Q4_K_M) | Ollama (pull) | Polish-specialised, Apache-2.0, official GGUF; the credible challenger on the milestone's primary criterion (Polish). ~5.1 GB pull; disk/RAM verified fine. Q8_0 is not speed-comparable to the Q4_K_M models but is Bielik's best-quality form. **Polish-quality challenger.** |
| 3 | `llama3.2:3b` (Q4_K_M) | Ollama | Already local; faster (3B); English control and a "what does a non-Polish-tuned model feel like" reference. |
| 4 | `qwen2.5:1.5b` (Q4_K_M) | Ollama | Already local; **latency floor** and a stand-in for the Hailo ≤1.7B tier — shows what we would trade away by going to hardware LLM offload. |

Deliberately **excluded**: `qwen2.5:3b` / `qwen2.5-coder:3b` (Qwen Research
licence — unusable in a product), `llama3.2:1b` (too small to add signal over
qwen2.5:1.5b), Gemma 3 4B (worth a later look; license friction + a pull, and
Qwen3-4B already covers the "generalist 4B multilingual" slot for M1.0).

---

## 8. Runtime comparison — Ollama vs llama.cpp (as `LocalModelProvider` implementations)

`ASSUMPTION` where not yet measured; llama.cpp direct numbers are a Phase-9
follow-up (same cases, matched sampling).

| Dimension | Ollama 0.30.10 | llama.cpp `llama-server` |
|---|---|---|
| Install on this Pi | **already installed + running as a service** | source build only (legacy has one, CPU-only, stale) |
| Model management | pull / registry / auto load+unload / keep-alive | manual GGUF files, manual load, one model per process |
| API | native `/api/chat` + OpenAI `/v1/chat/completions`, streaming, tools | OpenAI-compatible `/v1/…`, streaming |
| Startup overhead | model auto-loads on first call (~7.6 s cold for 4B here) | you manage the process; load once, stays resident |
| Perf | uses llama.cpp under the hood → ~same ceiling; ~3.9 tok/s for 4B here | expected ~same (same ggml kernels); direct control of threads/mmap/flash-attn |
| Streaming | yes (measured) | yes |
| Portability | Linux/macOS/Windows servers; **not** iOS/Android | Linux/macOS/Windows **+ iOS/Android/embedded** |
| Operational simplicity | high (one service) | lower (you build + supervise) |
| Provider-abstraction fit | good (HTTP, OpenAI-shaped) | good (HTTP, OpenAI-shaped) — same adapter can target both |

`PROPOSAL`: **Ollama first** (it is already here, managed, API-served), with the
NeXa provider written against the **OpenAI-compatible chat shape** so the *same
adapter* can point at `llama-server`, a trusted-PC node, or an online provider by
config. `llama.cpp` direct is the portability/no-daemon path and the M1.1/M2
second implementation. NeXa binds to **neither** (`AGENTS.md` §3.4).

---

## 9. Benchmark design

See `docs/testing/M1_NATURAL_CONVERSATION_BENCHMARK.md` for the full case list,
fixed conditions, and the 1–5 human rubric. Harness: `scripts/m1_bench/`
(throwaway, stdlib-only). Cases: `CONV-PL-S1` (11 turns), `CONV-EN-S1` (10),
`CONV-MIX-1` (7). Conditions: Ollama streaming, `num_ctx=8192`, model-recommended
sampling, `num_predict=200`, full in-session history resent each turn, cold load
measured once.

**M1.0 conversation coverage (scoped for time — a 4B on this Pi is ~3 tok/s in
conversation mode, ≈ 20–30 min per session):** `qwen3:4b-instruct` × {PL, EN} and
`Bielik-4.5B-v3 Q8_0` × {PL, EN} were run — these are the two comparisons the
model decision hinges on. `CONV-MIX-1` and conversation runs for
`llama3.2:3b` / `qwen2.5:1.5b` were **not** run: the perf micro-benchmark (§10.1)
already quantifies those models, `llama3.2`'s weak Polish is well established, and
`qwen2.5:1.5b` is only the latency floor. They remain defined for M1.1.

---

## 10. Benchmark results

> `VERIFIED FACT` — machine-measured by `scripts/m1_bench/bench.py` on the Pi 5,
> 2026-08-31. Raw JSON/MD under `docs/research/m1_bench_results/`.

### 10.1 Perf micro-benchmark (fixed EN prompt, 3× ~200 tokens, cold first)

| Model | Cold load (s) | Warm TTFT (s) | Gen tok/s (mean, [runs]) | Prompt-eval tok/s (warm) | Peak temp (°C) | Throttled |
|---|---|---|---|---|---|---|
| `qwen3:4b-instruct` (4.0B Q4_K_M) | 7.6 | ~0.62 | **3.89** [4.03, 3.84, 3.79] | ~147 | 66.7 | no (`0x0`) |
| `llama3.2:3b` (3.2B Q4_K_M) | 11.2 | ~0.68 | **4.35** [4.44, 4.37, 4.23] | ~238 | 68.3 | no (`0x0`) |
| `qwen2.5:3b` (3.1B Q4_K_M) | 9.8 | ~0.58 | **4.55** [4.61, 4.57, 4.47] | ~266 | 67.8 | no (`0x0`) |
| `qwen2.5:1.5b` (1.5B Q4_K_M) | 5.8 | ~0.47 | **9.35** [9.13, 9.52, 9.39] | ~570 | 68.3 | no (`0x0`) |
| **Bielik-4.5B-v3 (4.8B `llama` arch, Q8_0)** | 12.9 | ~0.55 | **2.01** [1.92, 1.96, 2.15] | ~210 | 68.3 | no (`0x0`) |

**Key measured results (`VERIFIED FACT`):**

1. On this Pi 5, **3B is only ~10–17 % faster than 4B** (4.4–4.6 vs 3.9 tok/s) —
   nowhere near the ~1.7–2× that community lore suggests. The only real speed
   step is down to **1.5B (~9.3 tok/s, ~2.4×)**. `INFERENCE`: generation is
   memory-bandwidth-bound on the A76, and a 3B↔4B Q4 weight-size delta is too
   small to matter.
2. **Quantisation is a bigger speed lever than parameter count in the 3–5B
   range.** Bielik-4.5B at **Q8_0** runs at **~2.0 tok/s — about half of
   `qwen3:4b` at Q4_K_M** (3.9), because Q8_0 moves ~2× the bytes per token.
   `ollama show` reports Bielik as `parameters 4.8B` (`llama` arch, ctx 8192).
3. **Consequence:** the practical M1 speed/quality field on this Pi is
   *4B-class Q4 (~4 tok/s)* vs *1.5B-class Q4 (~9 tok/s)*. A 4.5B **Q8_0** model
   at ~2 tok/s (≈ 25 s for a 50-token reply) is below the "usable" bar in the
   benchmark-doc targets — a real mark against Bielik-4.5B-Q8_0 for on-Pi use,
   independent of its language quality (§11/§12).

### 10.2 Conversation sessions

`VERIFIED FACT`. Full turn-by-turn transcripts:
`docs/research/m1_bench_results/CONV-*_*.md`. `num_ctx=8192`, `num_predict=200`,
full in-session history resent each turn, cold load measured once.

| Session | Turns | Gen tok/s (mean / min–max) | TTFT s (mean) | Cold load s | Peak °C | Throttled | Model RAM (inferred) |
|---|---|---|---|---|---|---|---|
| `qwen3:4b-instruct` — PL (`CONV-PL-S1`) | 11 | **3.2** / 2.25–4.03 | 5.86 | 7.84 | 68.3 | no | ~4.00 GB |
| `qwen3:4b-instruct` — EN (`CONV-EN-S1`) | 10 | **3.8** / 3.21–4.94 | 3.58 | 6.81 | 68.8 | no | ~3.92 GB |
| Bielik-4.5B-v3 Q8_0 — PL (`CONV-PL-S1`) | 11 | **2.13** / 1.90–2.27 | 12.78 | 15.26 | 68.8 | no | ~5.42 GB |
| Bielik-4.5B-v3 Q8_0 — EN (`CONV-EN-S1`) | 10 | **2.22** / 2.12–2.37 | 14.58 | 15.47 | 68.8 | no | ~5.36 GB |

`OBSERVATION`: `qwen3:4b` generation **degrades as the session grows** — PL run
went 4.03 → 2.25 tok/s over 11 turns, TTFT 2.2 s → 10.1 s, as the resent history
lengthens the prompt-eval each turn. A real M1.1 must bound context (turns +
chars) and keep the model warm.

`OBSERVATION`: Bielik-4.5B **Q8_0** ran at **~2.1–2.2 tok/s** throughout (about
half `qwen3:4b`), with per-turn TTFT 8–17 s (warm) and a 33–37 s first-turn cold
TTFT, and a **~5.4 GB** resident footprint vs ~4.0 GB. Every Bielik turn stayed
well under the 200-token cap because the model **stopped early** — see §11.

---

## 11. Polish conversation quality analysis

Scoring: **agent-assisted** (Claude) against the raw transcripts and the §4
rubric of the benchmark doc. **Not operator-confirmed human scoring** — an
operator pass is still owed before the M1.1 model is fixed. Transcript:
`docs/research/m1_bench_results/CONV-PL-S1_ollama_qwen3-4b-instruct_*.md`.

### `qwen3:4b-instruct` — Polish — session score **2 / 5** (agent-assisted)

| Dimension | Score | Evidence |
|---|---|---|
| Language correctness (is it Polish, and *correct* Polish) | 2 | Stays in Polish, but grammar/idiom is frequently wrong: "gdy coś się skupia" (T2), "Czy mam zrozumiać" for *zrozumieć* (T5), "to jest przykładowe użycie" (T9), "jak mu się podobają jego grane słowa" (T9), "może być też silą, którą ktoś wydaje" (T11). Reads like machine-translated Polish. |
| Naturalness | 2 | Stilted, non-native phrasing throughout; invents odd constructs ("Basa-ka – kasetę z muzyką w stylu basu", T7). |
| Correction handling (`CONV-PL-003`, T5) | 3 | Acknowledges the correction ("Aha, teraz rozumiem — chodzi Ci o poranek") and re-scopes to the morning — but the new plan contradicts T3's and the wording degrades. |
| Context / reference resolution | 3 | Resolves "tej prezentacji" (T11) and the running thread; some drift. |
| Topic switching (T6) | 4 | "Okej, zmieniamy. Co masz na myśli? 😊" — clean. |
| Recall (`CONV-CTX-001`, T10) | 4 | Correctly recalls the opening topic: "O czasie. I tym, że gdy coś się skupia — jak prezentacja, czy poranek — trudno się nie czuć przytłoczone." Then pads with unsolicited gift advice. |
| Brevity / length discipline | 2 | Ignores the short opener with a numbered plan (T3, T4, T5 all hit the 200-tok cap); T8 "jednym zdaniem" *is* one sentence (good) but broken Polish. |
| Robotic phrasing | 2 | Heavy list/plan formatting, emoji bullets, "W porządku, to kluczowe." openers. |
| Repetition | 3 | Re-serves the "give him a card / playlist, not strings" idea in T9 **and** T10 unprompted. |
| Honesty / no invention | 2 | Fabricates song titles and artists (T7: "Bassline – The Roots", "Soul Train (płyta z basem)"). |

**Verdict:** Polish is a real weakness for `qwen3:4b-instruct` — usable only as a
low bar. Recall and topic-switch work; grammar, naturalness, brevity and
factual restraint do not.

### Bielik-4.5B-v3 Q8_0 — Polish — session score **3 / 5** (agent-assisted)

Transcript: `…CONV-PL-S1_ollama_SpeakLeash-bielik-4-5b-v3-0-instruct-Q8_0_*.md`.

| Dimension | Score | Evidence |
|---|---|---|
| Language correctness | 5 | Native-quality Polish throughout; two typos only ("prezentsacji" T5/T10). No broken grammar — a clear step up from `qwen3:4b`. |
| Naturalness | 4 | Reads like a real (slightly formal) Polish speaker. |
| Correction handling (T5) | 3 | Acknowledges and re-scopes to "poranek" correctly — but then emits "oto kilka pomysłów… :" and **stops with no content**. |
| Context / reference resolution | 4 | Tracks the presentation thread; T11 resolves "tej prezentacji" cleanly. |
| Topic switching (T6) | 3 | Clean switch, but "Oczywiście, z czym mogę Ci jeszcze pomóc?" is exactly the assistant-formula the persona bans. |
| Recall (`CONV-CTX-001`, T10) | 4 | Correct: "rozmawialiśmy o stresujących zadaniach i przygotowaniach do prezentacji na 14:00." |
| Brevity / length discipline | 2 | **Over-corrects into truncation:** T4, T5, T7 emit only a lead-in ("Oto kilka wskazówek:") and nothing else (eval_count 24/37/25). Useless answers. |
| Robotic phrasing | 3 | "Rozumiem." / "Oczywiście," openers on most turns; banned formulas. |
| Repetition | 4 | "Rozumiem" repeated a lot; otherwise no repeated content. |
| Honesty / no invention | 4 | No fabricated facts (contrast `qwen3`'s fake songs); T8 gift suggestion ("wzmacniacz basowy" for 300 zł) is unrealistic but not a hallucinated fact. |

**Verdict:** Bielik's Polish *language* is clearly better than `qwen3:4b`'s, and
it doesn't hallucinate — but with these sampling params (temp 0.7, the model's
own default is 0.1) it **repeatedly truncates answers to a useless stub** and
leans on banned assistant formulas. Language: strong. Task behaviour: needs
tuning.

## 12. English conversation quality analysis

Same method/caveat as §11. Transcript:
`docs/research/m1_bench_results/CONV-EN-S1_ollama_qwen3-4b-instruct_*.md`.

### `qwen3:4b-instruct` — English — session score **4 / 5** (agent-assisted)

| Dimension | Score | Evidence |
|---|---|---|
| Language correctness | 5 | Fluent, idiomatic English throughout. |
| Naturalness | 4 | Conversational and warm ("Being on hold is like… emotional whiplash"); occasionally tries too hard, emoji-dense. |
| Correction handling (`CONV-EN-003`, T5) | 4 | "Oh! Got it." then genuinely re-scopes from *booking* to the *on-hold experience*. |
| Context / reference resolution | 4 | Tracks "the being-on-hold part", the Lisbon thread, "that" in T8. |
| Topic switching (T6) | 5 | "Yeah, totally. What's up? 😊" — clean. |
| Recall (`CONV-CTX-002`, T10) | 5 | Quotes the exact first line verbatim. |
| Brevity / length discipline | 3 | Short turns are short, but T2/T3/T5/T7 run long and listy; ends several turns with an unsolicited "want me to…?" offer. |
| Robotic phrasing | 4 | Mostly avoids "assistant voice"; slips into bullet lists for the travel turn. |
| Repetition | 4 | Minor — "text them, one sentence" repeated across T4/T5. |
| Honesty / no invention | 4 | Lisbon landmarks are real; one café name ("Café da Rua das Flores") is plausible but unverifiable — presented casually, not as fact. |

**Verdict:** English is genuinely good — natural, correct, good memory and
correction handling. Main flaws are verbosity and over-eagerness, both
addressable with the system persona.

### Bielik-4.5B-v3 Q8_0 — English — session score **2.5 / 5** (agent-assisted)

Transcript: `…CONV-EN-S1_ollama_SpeakLeash-bielik-4-5b-v3-0-instruct-Q8_0_*.md`.

| Dimension | Score | Evidence |
|---|---|---|
| Language correctness | 4 | Grammatical English; two typos ("dentisst" T3/T4). |
| Naturalness | 3 | Flat and a bit blunt ("That's a bad idea, you should call them today."); not unnatural, just charmless. |
| Correction handling (`CONV-EN-003`, T5) | 4 | Handles it — re-scopes from booking to the on-hold problem and gives a relevant tip. |
| Context / reference resolution | 3 | Mostly tracks the thread; T3 misreads "I hate being on hold" as "do you need a reminder". |
| Topic switching (T6) | 3 | Clean, but opens with the banned "Sure,". |
| Recall (`CONV-CTX-002`, T10) | 2 | "You initially asked for the current time." — self-consistent with its own **T1 non-sequitur** ("What's the current time?" in reply to "Hey NeXa.") but **wrong** vs the user's real first message (the dentist). |
| Brevity / length discipline | 3 | Reasonable lengths; T8 one-sentence compression is good. |
| Robotic phrasing | 3 | Hedge-heavy ("it depends on your preferences and what you want to see"). |
| Repetition | 4 | Minor. |
| Honesty / no invention | 3 | No hard fabrication, but T7/T9 are pure non-committal hedging — dodges the Lisbon question and the "disagree?" prompt. |

**Verdict:** Bielik's English is a clear regression from `qwen3:4b`'s — a
non-sequitur opener, hedgy non-answers on opinion/travel, blunt tone, and it
poisons its own recall. Bielik is a Polish model; its English is not competitive
for NeXa.

## 12A. Head-to-head — `qwen3:4b-instruct` (Q4_K_M) vs Bielik-4.5B-v3 (Q8_0)

All from this milestone's measurements and the four transcripts above.
Agent-assisted quality scores (§11/§12), not operator-confirmed.

| Axis | `qwen3:4b-instruct` | Bielik-4.5B-v3 Q8_0 | Winner |
|---|---|---|---|
| **Polish quality** | 2/5 — rough grammar, unnatural, hallucinates, verbose | 3/5 — native grammar, no hallucination, but **truncates answers to stubs** + banned formulas | **Bielik** (language); mixed on usefulness |
| **English quality** | 4/5 — natural, engaged, good recall | 2.5/5 — blunt, hedgy, T1 non-sequitur, poisoned recall | **Qwen3** (clearly) |
| **Multi-turn context** | recall correct both langs; degrades in speed as history grows; **262k** native ctx | recall correct both langs; **8k** ctx (Ollama tag) — a real ceiling for long sessions / future | **Qwen3** |
| **Corrections** | acknowledges + adapts (PL T5, EN T5) | acknowledges + adapts, but PL T5 truncates the payload | **Qwen3** (slight) |
| **Naturalness** | good EN, poor PL | good PL, flat EN | tie (language-split) |
| **Verbosity** | runs long / listy / emoji | terse — sometimes **too** terse (empty answers) | tie (opposite failure modes) |
| **Speed (Pi 5, CPU)** | perf **3.89** tok/s; conv 3.2–3.8; warm TTFT ~0.6 s | perf **2.01** tok/s; conv ~2.1–2.2; warm TTFT 8–17 s, cold 33–37 s | **Qwen3** (~2×) |
| **RAM resident** | ~4.0 GB | ~5.4 GB | **Qwen3** |
| **Cold load** | ~7 s | ~15 s | **Qwen3** |
| **Temp / throttling** | peak 68.8 °C, no throttle | peak 68.8 °C, no throttle | tie |
| **License** | Apache-2.0 | Apache-2.0 | tie |
| **Quant available** | Q4_K_M (official, on box) | **Q8_0 / FP16 only** (no official Q4_K_M) | **Qwen3** |
| **Practical fit for NeXa (Pi, M1)** | bilingual, fast enough with streaming, huge ctx, follows instructions — **but Polish needs help** | Polish-native but **2× slower, 8k ctx, truncates, weak English, no Q4** | **Qwen3** |

**Net:** Bielik wins *Polish language fidelity* and *factual restraint*. `qwen3:4b`
wins *speed, English, context length, instruction-following, memory footprint,
and quant availability* — i.e. almost everything that decides on-device
viability. Bielik's advantages are real but confined to one axis; its costs
(speed, context, English, answer truncation) hit the exact dimensions the Pi is
weakest on.

`OBSERVATION` / caveat: Bielik was run at temp 0.7 (its own `ollama show` default
is **0.1**) and with our shared sampling. The truncation and the EN T1
non-sequitur may be partly a params/chat-template mismatch, not a fixed model
limit. A fair re-test (Bielik at its recommended settings, and a third-party
**Q4_K_M** GGUF for speed) is M1.1 work — see §21.

## 13. Performance analysis

`VERIFIED FACT` so far (`qwen3:4b-instruct`): generation is the bottleneck at
**~3.9 tok/s**. Warm TTFT (~0.6 s) and prompt-eval (~147 tok/s) are fine.
Cold load 7.6 s → keep the model warm (Ollama `keep_alive`). Thermals safe
(≤ 67 °C, no throttle) for sustained single-turn loads.

Implication for "is the Pi fast enough":

- A ~50-token reply ≈ **13 s** wall on a 4B; a ~150-token reply ≈ **38 s**.
- With streaming + a system prompt that enforces short replies, a 4B is
  **usable** for a considered companion, **marginal** for fast chat rhythm.
- **Measured:** 3B buys only ~10–17 % over 4B on this Pi (§10.1) — *not* a real
  latency lever. The only meaningful speed step is 1.5B (~2.4×). So the M1 choice
  is **4B-class quality (~4 tok/s) vs 1.5B-class speed (~9 tok/s)**; there is no
  useful middle.
- **Measured (Bielik-4.5B Q8_0):** ~2.0 tok/s in conversation, cold TTFT
  33–37 s, ~5.4 GB resident — a ~50-token reply ≈ 25 s. Below the benchmark-doc
  "usable" bar. Q8_0 is the speed problem as much as the extra 0.8 B params.

## 14. Legacy pipeline vs direct model

`INFERENCE` (strong; a full side-by-side on identical prompts is a Phase-11
follow-up but the direction is already clear from legacy Report 176 + our direct
runs):

- **Direct** `USER → qwen3:4b (Ollama, in-session history) → stream`: warm TTFT
  ~0.6 s, honest answers, no canned interceptors, no process-wide failure mode.
- **Legacy** `USER → 2573-line MAS (Meaning/Context/gates/Reasoning/Conversation/
  Verifier/Phase-B) → qwen3:4b → RESPONSE`: 13.4 s TTFT on a measured typed
  turn; 75/140 s hard timeouts; a single timeout permanently breaks the server
  (B1); short inputs and paraphrases intercepted with canned/false replies
  (B2/B3).
- The legacy report itself: the model, *when reached*, "correctly, honestly
  answered"; the failures were pipeline/grounding/latency.

**Conclusion:** poor legacy naturalness was **E — multiple factors, dominated by
B (pipeline complexity) + C (late/missing grounding & pre-model interceptors) +
D (latency)**, not A (model quality). This is the central justification for the
minimal M1.1 architecture in §18.

## 15. Cross-device implications

| Device | Can the same NeXa Conversation System run? | How |
|---|---|---|
| **Raspberry Pi 5** (robot body) | Yes (measured) | `LocalModelProvider` → Ollama now / `llama-server` later; 3–4B Q4 model |
| **Linux / Windows PC** | Yes | same provider → local Ollama/`llama-server` with a larger model, or acts as a "trusted node" provider for other devices |
| **macOS** | Yes | Ollama or `llama.cpp` (Metal) / MLX behind the same provider |
| **iPhone** | Yes (`INFERENCE`) | in-process `llama.cpp` (Metal) or MLX-Swift provider adapter; or a `RemoteTrustedNodeProvider` to the user's PC/Pi |
| **Android** | Yes (`INFERENCE`) | in-process `llama.cpp` (JNI/NDK) provider adapter; or trusted-node |

`INFERENCE`: **yes — the same Conversation System survives all targets with only
the provider adapter changing**, provided the interface stays a thin
messages-in / token-stream-out contract (§13/§18) and nothing device-specific
leaks into it.

## 16. Voice future compatibility

`PROPOSAL`: the Conversation System must accept a turn from **either** a text UI
**or** an STT result (both are just "user text + session id"), and emit a
**token stream** that a chat UI renders directly and a TTS stage can chunk. No
VAD/STT/TTS/endpointing logic inside the Conversation System — that lives in a
future `RealtimeVoiceAdapter` (M2) that calls the same `ConversationSession`.

- First Pi-local voice prototype: **Pipecat** looks better (local-first, pipeline
  control, transport-agnostic, BSD-2).
- Multi-device production: **LiveKit** worth testing as the transport/presence
  layer; Pipecat can sit on top of it.
- Polish turn-detection risk: `OBSERVATION` (legacy Report 176) STT deviation was
  high for both PL and EN with faster-whisper `base`/`auto` — M2 must benchmark
  STT (whisper.cpp vs faster-whisper, model size, explicit `language`) and
  consider Hailo Whisper offload.
- Not choosing Pipecat vs LiveKit in M1.0 — evidence is not overwhelming.

## 17. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | 4B at ~3.9 tok/s is too slow for natural chat rhythm; **degrades to ~2.5 tok/s** over a long session | High | streaming + short-reply persona; bound context; keep model warm; provider swappable to a faster host |
| R2 | **Confirmed by measurement:** `qwen3:4b` Polish quality is weak (agent-assisted 2/5 — rough grammar, hallucination). Bielik's Polish is better (3/5) but it is 2× slower, 8k-ctx, truncates, and worse at English | **High (primary criterion)** | M1.1: invest in the PL system persona / few-shot; **re-test Bielik at its recommended params + a Q4_K_M GGUF**; treat the model as swappable; consider PL-only routing to a PL model later |
| R3 | Re-growing a legacy-style mega-pipeline | High | `AGENTS.md` §3 + ADR; M1.1 scope fixed at §18; no pre-model interceptors |
| R4 | Binding NeXa to Ollama specifics | Medium | provider written to OpenAI-compatible chat shape; llama.cpp as 2nd impl in M1.1/M2 |
| R5 | Ollama default `num_ctx` (4096) silently truncates long sessions | Medium | set `num_ctx` explicitly per session; monitor context usage |
| R6 | Timeout handling repeating legacy B1 | Medium | per-request state only; cancel/detach the generation cleanly; never share a mutable pipeline object across turns |
| R7 | Legacy `.venv` on `PATH` contaminating NeXa | Low | NeXa uses its own `./.venv`; document; CI-style check later |
| R8 | Hailo/V3D "acceleration" assumed to help | Low (now documented) | §2.2/§2.3 — neither helps M1 LLM; revisit Hailo Whisper for M2 |

## 18. Recommended M1.1 architecture

`PROPOSAL` — the **minimum** that delivers one honest streamed conversational
turn, and nothing more:

```
        text UI  ──┐
                   ├──▶  ConversationSession(session_id)
   (later) STT  ──┘        │  holds ConversationContext (in-session messages only,
                           │  bounded by turns + chars; NOT long-term memory)
                           ▼
                    ConversationTurn(user_text)
                           │  builds messages = [system persona] + context + user
                           ▼
                    ModelProvider.generate(messages, options, *, cancel) ──▶ async token stream
                           │        (LocalModelProvider → Ollama /api/chat or /v1, streaming)
                           ▼
                    StreamingResponse  ──▶ tokens to caller (UI renders; later TTS chunks)
                           │
                    on completion: append assistant message to ConversationContext
```

**Build exactly these (small):**

| Type | Responsibility | Notes |
|---|---|---|
| `ModelProvider` (interface) | `generate(messages, options, *, cancel_token) -> AsyncIterator[str]` (+ a non-streaming convenience) | the one seam; §19 |
| `LocalModelProvider` (impl) | talk to Ollama (OpenAI-compatible chat shape), stream tokens, propagate cancellation, surface honest errors | first impl; `llama-server` is a drop-in second impl |
| `ConversationContext` | ordered in-session messages + bounding (turns + chars, à la legacy `MAX_CONTEXT_TURNS/CHARS`) | **not** persistence, **not** long-term memory |
| `ConversationSession` | owns one context; `send(user_text) -> StreamingResponse`; one turn at a time | the single canonical entry — no second path |
| `ConversationTurn` | value object: the user text + assembled messages + result | may just be a dataclass |
| `StreamingResponse` | async iterator of text chunks + final assembled text + status | |
| system persona | one short NeXa system prompt, versioned in `configs/` | enforces language-follow + brevity + honesty |

**Explicitly NOT in M1.1:** model router / multi-model fallback, MAS or any
Meaning/Reasoning/Verifier agent, capability detection, tool calls, deterministic
pre-model interceptors, long-term memory, persistence to disk, voice, UI, device
awareness. Each is a later milestone with its own ADR.

**Hard constraints carried from legacy lessons:** (a) no pre-model layer may emit
a user-visible answer; (b) cancellation must actually stop generation and never
leave shared mutable state broken (legacy B1); (c) `num_ctx` set explicitly;
(d) on provider failure, fail closed with an honest message — no silent smaller
model (legacy Report 173).

## 19. Provider boundary (minimal contract)

`PROPOSAL` — do **not** implement in M1.0 beyond the throwaway harness. Minimum
fields for natural text conversation:

```
generate(
    messages: list[{role: "system"|"user"|"assistant", content: str}],
    options: {
        model: str,              # provider-scoped model id (config, never hard-coded in core)
        temperature, top_p, top_k, repeat_penalty: float | None,
        max_tokens: int | None,
        context_window: int | None,   # -> num_ctx / -c
        stop: list[str] | None,
    },
    *,
    cancel_token: () -> bool,     # cooperative cancellation; MUST stop generation
) -> AsyncIterator[StreamChunk]   # {delta: str}; final chunk carries {done, usage, finish_reason}
```

Plus provider-level: `name`, `describe()` → `{model, context_window,
supports_streaming, supports_tools}` (capabilities), and an explicit typed error
for "model unavailable / route not executable" (borrowed from legacy
`OllamaConversationProvider`'s honest-failure design).

Deliberately **excluded** from the M1 contract: tool/function calling, images,
embeddings, batching, log-probs, server management. Add later, per need, per ADR.

## 20. Explicitly rejected alternatives

| Rejected | Why |
|---|---|
| Reuse legacy `process_typed_chat_via_mas` / MAS pipeline | 2573-line brain + ~50 agent pkgs; Report 176 B1/B2/B3; latency; contradicts `AGENTS.md` §3 and NeXa #5 |
| Port legacy `OllamaConversationProvider` as-is | entangled with `cascaded_llm_answer`, domain router, verifier, streaming protocol — reuse the *contract*, not the code |
| Hailo-10H as the M1 LLM runtime | ≤1.7B models, 2048 ctx, ~5–10 tok/s, HailoRT 5.2.0-vs-5.3.0 mismatch; worse conversation quality than CPU 3–4B |
| V3D GPU / Vulkan for llama.cpp | not production-ready on Pi 5 (matmul shader limits); probe ran 100% CPU anyway |
| Bind NeXa to Ollama | violates provider-independence (`AGENTS.md` §3.4); mitigated by OpenAI-shaped adapter + llama.cpp as 2nd impl |
| `qwen2.5:3b` as a candidate model | Qwen RESEARCH licence — non-commercial; unusable in a product |
| Model router / multi-model fallback in M1.1 | one model is enough for M1; fallback = "second brain" risk; defer with ADR when >1 model |
| Choosing Pipecat or LiveKit now | M1.0 is text-only; evidence not overwhelming; decide in M2 with a voice prototype |
| Adding CI / venv / heavy deps in M1.0 | not needed to measure the baseline; belongs to M1.1 |

## 21. Open questions

1. `llama.cpp` direct vs Ollama on identical model+quant+sampling — real TTFT /
   tok/s / RAM delta on this Pi? (Phase-9 follow-up; not run in M1.0.)
2. **Bielik-4.5B fair re-test:** at its recommended sampling (temp ~0.1, correct
   chat template) and with a **third-party Q4_K_M GGUF** — does the truncation
   (PL T4/T5/T7) and the EN T1 non-sequitur go away, and does Q4 bring it near
   `qwen3:4b` speed? This is the open question that could flip the M1.1 baseline.
3. Does improving the **Polish system persona / few-shot** lift `qwen3:4b`'s
   Polish from ~2/5 to acceptable, or is a PL-specialised model unavoidable?
4. Measured 3B (`llama3.2:3b`) *conversation* quality (perf only in M1.0).
5. Best `num_ctx` for the Pi (RAM vs multi-turn depth vs prompt-eval cost) — 8k?
   16k? The speed decay over a session (§10.2) is partly context-length driven.
6. Does a short, well-designed system persona alone fix most of legacy B3
   (short-input handling) without any interceptor?
7. Streaming granularity for future TTS (token vs sentence) — measure perceived
   latency.
8. Exact Hailo Whisper offload numbers on *this* box once HailoRT 5.3.0 is
   installed — for M2 planning only.

(Resolved in M1.0: Bielik context length = **8k** per `ollama show`, not the 32k
earlier assumed; Bielik has **no official Q4_K_M** — Q8_0/FP16 only.)

## 22. Sources

External (accessed 2026-08-31):

- Qwen3-4B-Instruct-2507 — Hugging Face model card: <https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507>
- Unsloth Qwen3-2507 run guide: <https://unsloth.ai/docs/models/qwen3-how-to-run-and-fine-tune/qwen3-2507>
- Bielik-4.5B-v3.0-Instruct-GGUF — Hugging Face (SpeakLeash): <https://huggingface.co/speakleash/Bielik-4.5B-v3.0-Instruct-GGUF>
- Bielik on Ollama (SpeakLeash namespace): <https://ollama.com/SpeakLeash>
- Polish LLM leaderboard (CodeSOTA): <https://www.codesota.com/polish-llm>
- google/gemma-3-4b-it — Hugging Face: <https://huggingface.co/google/gemma-3-4b-it>
- Gemma 3 developer guide: <https://developers.googleblog.com/en/introducing-gemma3/>
- Ollama OpenAI compatibility docs: <https://docs.ollama.com/api/openai-compatibility>
- llama.cpp — GitHub (`ggml-org/llama.cpp`): <https://github.com/ggml-org/llama.cpp>
- whisper.cpp — GitHub (`ggml-org/whisper.cpp`): <https://github.com/ggml-org/whisper.cpp>
- llama.cpp Pi 5 / Vulkan V3D discussion (geerlingguy/ai-benchmarks, ramalama #2592): <https://github.com/geerlingguy/ai-benchmarks/issues/1>, <https://github.com/containers/ramalama/issues/2592>
- Running LLMs on Raspberry Pi 5 (practical benchmarks): <https://tinyweights.dev/posts/run-llms-raspberry-pi-5/>, <https://www.stratosphereips.org/blog/2025/6/5/how-well-do-llms-perform-on-a-raspberry-pi-5>
- Pipecat vs LiveKit comparisons: <https://www.forasoft.com/blog/article/pipecat-vs-livekit-agents>, <https://www.channel.tel/blog/pipecat-vs-livekit-voice-framework-decision>
- On-device llama.cpp (iOS/Android): <https://github.com/ferranpons/llamatik>, <https://www.buildmvpfast.com/blog/on-device-llm-mobile-llama-ios-android-2026>
- Hailo Model Zoo GenAI: <https://github.com/hailo-ai/hailo_model_zoo_genai>

Internal:

- Legacy repo `~/Projects/smart-desk-ai-assistant` — Reports 164, 165, 173, 175,
  176 (`docs/NeXa_RuNTiMe_FiX_and_Update/`); `config/settings.json`;
  `modules/nexa_agents/typed_chat/*`; `llama.cpp/`, `whisper.cpp/` builds.
- `scripts/m1_bench/` + `docs/research/m1_bench_results/` (this milestone's raw
  measurements).
- `docs/legacy/LEGACY_NEXA_INDEX.md`, `docs/decisions/ADR-0001_project_foundation.md`.
