# LEGACY NEXA INDEX

High-level map of the legacy NeXa repository. This is an initial index, not a
full catalogue. Expand it only when a task actually needs a section.

---

## Where it is

- **Path:** `/home/devdul/Projects/smart-desk-ai-assistant`
- **Remote:** `https://github.com/AndrewDul/smart-desk-ai-assistant.git`
- **Branch:** `main`
- **History (VERIFIED FACT, 2026-08-31):** 630 commits, first
  2026-03-31, latest 2026-08-24 (`e12d018 feat: checkpoint canonical MAS runtime
  after reports 169-174`).
- **Declared version / stage (from its README):** `0.7.0` /
  `premium-product-foundation`.

## Why it exists

It is the **first generation** of NeXa: a Raspberry Pi 5 robot / smart-desk AI
assistant built as a product-grade runtime over ~5 months. It contains large
amounts of practical, hardware-tested knowledge: what worked, what broke, and how
it was fixed on real hardware.

## Status in the new project

- **Reference / knowledge base ONLY.**
- **Do NOT** modify it. **Do NOT** delete it. **Do NOT** bulk-migrate it.
- **Do NOT** assume its architecture is correct for the new NeXa — see
  `docs/decisions/ADR-0001_project_foundation.md` (D1). In particular the legacy
  repo shows repeated cycles of competing central runtimes / supervisors; the new
  repo's "one canonical authority, no fallback brains" rule exists partly because
  of that history.

## How future agents should use it

1. Check new-repo knowledge first (`CURRENT_STATE.md`, reports, ADRs,
   architecture, troubleshooting).
2. If the task touches an area the legacy repo worked on, **read** the relevant
   legacy code / docs / tests / reports for evidence and gotchas.
3. Extract *knowledge* (constraints, failure modes, working parameters, hardware
   quirks), not architecture.
4. If reusing a specific legacy file is worthwhile, do it **deliberately**:
   copy that one file, re-read it, re-test it against this repo's principles, and
   record the reuse in your report.
5. Never let "the legacy did it this way" override a principle in `AGENTS.md` §3
   without an ADR.

---

## Categories of knowledge available (VERIFIED FACT — directories observed)

Paths below are inside the legacy repo.

### Reports & audits
- `docs/_agent_reports/` (incl. `Drive_System_reports/`)
- `docs/NeXa_Final_Audits/`
- `docs/NeXa_RuNTiMe_FiX_and_Update/` (+ `evidence/report158…report163`)
- `docs/NeXa_RuNTime_Map_and_Plan/` — runtime boot sequence, process map,
  architecture map, input paths, action flows, function registry, module status
  matrix, security/privacy/safety audit, self-healing plan
  (`CURRENT_RUNTIME_TRUTH.md`, `runtime_function_registry.json`,
  `runtime_status_matrix.json`)
- `docs/Learn_System_Reports/`, `docs/Drive_System_Raports/`,
  `docs/Raport_Usb_connection/`

### Architecture / decisions
- `docs/NeXa_Architecture_Decisions/` — `ADR_001_core_brain_replaces_legacy_runtime`,
  plus "core brain" / "model router" / "conversation authority" /
  "model intelligence registry" / "model zoo" / "multi-agent roles" design notes
  (a series, mid-2026).
- `docs/architecture_notes.md`, `docs/performance_targets.md`

### Multi-Agent System (MAS) experiment
- `modules/nexa_agents/` — ~50 agent packages: `event_bus`, `task_ledger`,
  `registry`, `turn_supervisor`, `typed_chat`, `conversation`, `planner`,
  `reasoning`, `verifier`, `model_router`, `model_zoo`, `model_health`,
  `model_benchmark`, `prompt_builder`, `memory_bus` / `memory_writer` /
  `memory_retriever` / `memory_lifecycle`, `identity`, `user_profile`,
  `consent_privacy`, `safety_policy`, `capability_evidence`, `tool_capabilities`,
  `language_translation`, `asr_correction`, `interrupt`, `tts`, `voice_input`,
  `ui`, `runtime_health`, `status_explainer`, …
- `docs/Multi_Agent_System/` — build reports `01_R0` … `29` (agent contract &
  registry, event bus & task ledger, report collector, runtime health, safety
  policy, model router/health/benchmark, typed-chat MAS entry, reasoning planner,
  conversation cascade, anti-hallucination verifier, voice cutover, memory bus,
  consent/privacy, identity, windowed-UI migration plan).

### Core runtime
- `modules/core/` — `assistant.py`, `assistant_impl/`, `flows/`
  (`action_flow`, `dialogue_flow`, `pending_flow`), `command_intents/`,
  `voice_engine/`, `session/`, `presence/`
- `modules/runtime/` — incl. `voice_engine_v2/` (VAD shadow, timing probes,
  runtime candidate executor)
- `modules/understanding/` — parsing / normalization

### Voice pipeline (STT / TTS / wake / streaming)
- Wake: `modules/devices/audio/input/wake/openwakeword_gate/`, `models/wake/nexa.onnx`
- VAD / realtime: `modules/devices/audio/realtime/`, `modules/devices/audio/vad/`
  (Silero VAD)
- Command ASR: `modules/devices/audio/command_asr/` (Vosk PL/EN,
  `command_grammar.py`, bilingual recognizer)
- Fallback STT: `modules/devices/audio/input/faster_whisper/`,
  `.../whisper_cpp/`; `whisper.cpp/` native build at repo root
- TTS: `modules/devices/audio/output/tts_pipeline/` (Piper ONNX voices under
  `voices/piper/`)
- Benchmarks: `benchmarks/voice/` (command latency, endpointing latency, full
  voice turn); `scripts/asr_benchmark.py`

### Local model integration
- `llama.cpp/` native build at repo root
- `docs/hailo-ollama-startup.md` (Hailo / Ollama startup notes)
- MAS `model_zoo` / `model_router` / `model_benchmark` / `model_health` packages
- `docs/NeXa_Architecture_Decisions/local_model_benchmark_harness_v1…`,
  `model_zoo_inventory_and_install_plan_v1…`

### Raspberry Pi hardware integrations
- `modules/devices/` — audio, camera, displays, sensors, pan-tilt, mobile base
- Hardware profile (from legacy README): Raspberry Pi 5 16GB; Debian 13 trixie
  aarch64; Raspberry Pi AI HAT+ / Hailo; Camera Module 3 Wide / IMX708
  (`picamera2`); 8" DSI touchscreen 1280x800; Waveshare 2" SPI LCD 320x240
  rot 180 (`waveshare_2inch`); Waveshare 2-axis pan-tilt (`waveshare_serial`,
  `/dev/serial0`); Geekworm X1206 UPS (MAX17040 fuel gauge on `/dev/i2c-1`);
  reSpeaker-style USB mic (`plughw:CARD=Array,DEV=0`); OAK-D Lite / DepthAI
  (diagnostics only).
  *Treat this list as legacy-declared, not verified for the new project.*
- `docs/camera/`, `docs/NeXa_sensors/`, `docs/pan_tilt_calibration.md`,
  `docs/ugv_odometry_calibration.md`, `docs/nexa_raspberry_pi_useful_commands.md`

### Robot drivers / drive system
- Mobile base (Waveshare UGV02 / ESP32), drive-mode scripts, follow-me /
  come-to-me planners: `scripts/drive_*`, `docs/Drive_System_Raports/`,
  `docs/_agent_reports/Drive_System_reports/`, `docs/come_to_me_spatial.md`,
  `docs/follow_me_hardware_smoke_test.md`

### UI work
- Godot "Visual Shell": `modules/presentation/`, `docs/NeXa_UI/` (~45 iteration
  notes — Apple-style design system, panel routing, chat workspace, startup,
  diagnostics), `docs/Multi_Agent_System/29_NeXa_Windowed_UI_Master_Architecture…`

### Tests
- `tests/` — large suite: `core/`, `devices/`, `features/`, `hardware/`,
  `integration/`, `nexa_agents/`, `nexa_system/`, `runtime/`, `playwright/`,
  plus many top-level `test_*.py` (action skills, ASR endpointing, barge-in,
  interrupt snapshots, dialogue flow, lifecycle warmup, LLM background
  lifecycle). `pytest.ini` at repo root; `tests/README.md`.

### Troubleshooting
- `docs/troubleshooting.md`, `docs/test_notes.md`,
  `docs/NeXa_RuNTiMe_FiX_and_Update/` evidence bundles,
  `nexa-feedback-mode-*.patch` files at repo root.

### Dependencies (legacy `requirements.txt`, for reference only — do NOT copy)
`numpy`, `sounddevice`, `soundfile`, `Pillow`, `luma.oled`, `spidev`, `gpiozero`,
`rpi-lgpio`, `faster-whisper`, `silero-vad`, `onnxruntime`, `vosk`,
`openwakeword`, `piper-tts`, `pytest`; optional: `opencv-python-headless`,
`picamera2`, `depthai`, `smbus2`, `pyserial`, `sentence-transformers`.

---

## Known unknowns about the legacy repo

- Which MAS parts actually run vs. are scaffolding — its own reports suggest
  churn. `UNKNOWN` without running it.
- Current pass/fail state of the legacy test suite — not run from the new repo,
  and it must not be relied on here.
- How much of the hardware profile is still physically attached to this machine.
