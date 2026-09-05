# R0005 — M2 Realtime Voice: Open-Source-First Research

- **Date:** 2026-09-05
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M2 — Realtime Voice (**research only**; no product code)
- **Related:** `docs/research/M2_REALTIME_VOICE_RESEARCH.md` (primary
  evidence doc), `docs/architecture/M1_1_TEXT_CONVERSATION_ARCHITECTURE.md`,
  `docs/decisions/ADR-0002_text_conversation_foundation.md`,
  `docs/legacy/LEGACY_NEXA_INDEX.md`

---

## TASK RESULT

**PASS, with one explicitly disclosed gap.** Delivered: an open-source-first
architecture/license/feasibility research pass across every M2 responsibility
named in the task (orchestration, audio pipeline, VAD, turn detection,
interruption/barge-in, STT, TTS, transport, client SDKs, metrics), a
make-vs-build table, a license review that caught a real framework-lock risk
by unpacking an actual wheel rather than trusting a PyPI classifier, and a
prototype-level (not runtime-level) A/B/C comparison. **Gap, disclosed not
hidden**: no runnable prototype was built or executed — this was desk +
legacy-evidence research, per `docs/research/M2_REALTIME_VOICE_RESEARCH.md`
§7's own disclosure. No ADR was written; this research is an ADR's input, not
the ADR itself.

## WHAT I DID

1. Read `docs/CURRENT_STATE.md`, `ADR-0002`, `FOUNDATION_ARCHITECTURE.md`
   (Voice/Model-Providers boundaries), `RESEARCH_POLICY.md`, and
   `docs/legacy/LEGACY_NEXA_INDEX.md`'s voice-pipeline section before writing
   anything.
2. **Legacy NeXa (read-only) evidence extraction** — the highest-value input:
   real Pi 5 measurements already existed for faster-whisper (accuracy/speed
   sweep across `tiny`/`base`/`small`), Piper TTS (`gosia-medium` Polish
   voice, real per-utterance latency), a full production turn-timeline log
   (command-turn vs. LLM-answered-turn latency), and a real CPU-contention
   failure incident. This anchored every "Pi 5 feasibility" cell in the
   make-vs-build table in real numbers instead of generic external claims.
3. **Deep-dived pipecat-ai/pipecat and livekit/agents** — actual source code
   and reference docs (license files, `Agent.llm_node`/custom
   `FrameProcessor` integration points, VAD/turn-detection defaults,
   transport options, dependency trees), not just READMEs.
4. **License review, verified not assumed**: fetched license files/GitHub API
   `license.spdx_id` for every candidate. Caught a real discrepancy on
   LiveKit's bundled local VAD/turn-detector models — one pass found a
   proprietary-license PyPI classifier, another did not find a license file
   and reported it unconfirmed. **Resolved by directly downloading and
   unpacking the actual `livekit-local-inference` wheel** and reading
   `dist-info/licenses/MODEL_LICENSE` inside it: the models are under a real
   "LiveKit Model License Agreement" restricting use to the LiveKit Agents
   framework only (a genuine framework-lock clause) — now the report's
   authoritative, verified finding.
5. **Researched STT/TTS/VAD candidates**: faster-whisper, whisper.cpp, Hailo
   Whisper offload, Piper (+ its GPL-3.0 successor fork, `OHF-Voice/piper1-gpl`,
   confirmed via GitHub API after finding the original `rhasspy/piper` was
   archived), Kokoro (disqualified — no Polish support), Silero VAD.
6. **Wrote `docs/research/M2_REALTIME_VOICE_RESEARCH.md`**: purpose/policy,
   method, legacy evidence, hardware/resource-budget reality check, five OSS
   candidate deep dives, a make-vs-build table (all required columns), an
   A/B/C prototype-level comparison, and an explicit open-questions section.

## WHAT I VERIFIED

- Pipecat license: BSD-2-Clause (fetched `LICENSE` directly).
- LiveKit `agents`/`livekit` server license: Apache-2.0 (fetched `LICENSE` +
  GitHub API).
- LiveKit's bundled local VAD/turn-detector model license: proprietary
  ("LiveKit Model License"), framework-locked to LiveKit Agents —
  **verified by downloading and unpacking the actual PyPI wheel**
  (`pip download livekit-local-inference`), not by trusting either research
  pass's summary.
- Piper's license lineage: `rhasspy/piper` archived 2025-10-06 (MIT); active
  successor `OHF-Voice/piper1-gpl` is GPL-3.0 (GitHub API
  `license.spdx_id: "gpl-3.0"`).
- faster-whisper / whisper.cpp: both MIT (GitHub-confirmed).
- Silero VAD: MIT.
- All of legacy's real Pi 5 numbers cited in the research doc were read
  directly from the legacy repo's actual JSON benchmark output and log
  files, not summarized secondhand.

## TESTS

N/A — research-only task, no code changed. `python -m unittest discover -s
tests` was not expected to be affected; not re-run as part of this task since
nothing under `src/`/`tests/` was touched (confirmed via `git status`).

## UNRESOLVED

- **No runnable prototype exists yet** for either Pipecat+`ConversationSession`
  or LiveKit Agents+`ConversationSession`. This is the single most important
  next step before an M2 architecture ADR, per the research doc §7–§8.
- **Resource-budget question is open**: can this Pi 5 run VAD + STT + a 10 GB
  `gemma4:e4b` + TTS concurrently without the CPU-contention failure legacy
  already hit once? Not answered by desk research; needs a spike.
- **whisper.cpp Polish accuracy** has no same-hardware, same-test-set number
  yet (faster-whisper does, from legacy).
- **Hailo Whisper offload** Polish-calibration feasibility is unproven.
- **Piper's GPL-3.0 successor** — subprocess-only invocation is recommended
  but not confirmed sufficient by legal/owner review.
- One background research subagent (Pipecat-focused) did not complete within
  this task's window due to an unrelated agent-orchestration tooling issue
  (duplicate/stalled task dispatch — reported separately as product
  feedback); its intended scope was covered directly by the agent instead,
  so there is no coverage gap, only a process note.

## DOCUMENTATION / REPORTS UPDATED

- `docs/research/M2_REALTIME_VOICE_RESEARCH.md` — new.
- This report (`R0005`).
- `docs/CURRENT_STATE.md` — updated to record the research as complete and
  M2 as not yet started (implementation).
- `tests/test_foundation.py` — `REQUIRED` list extended with this report's
  filename, per existing convention.
- No ADR written — this research is an ADR's evidence base, not a decision;
  ADR-0002 is unaffected (M2 is untouched by it).

## LEGACY NEXA USED

YES — read-only, extensively (see "WHAT I DID" #2). Not modified.

## EXTERNAL RESEARCH USED

YES — pipecat-ai/pipecat, livekit/agents, livekit/livekit,
SYSTRAN/faster-whisper, ggml-org/whisper.cpp, Hailo's whisper offload
release, rhasspy/piper + OHF-Voice/piper1-gpl, hexgrad/Kokoro-82M,
snakers4/silero-vad — source repos, license files, and (for `pip
download`-verified claims) actual package wheels.

## CURRENT VERIFIED STATE

M2 research is complete enough to inform an architecture ADR. No M2 product
code exists. M1.1 remains the last implemented milestone, unaffected by this
task. Repo clean after this task's one commit (docs only).

## NEXT RECOMMENDED ACTION

Before an M2 architecture ADR: run the resource-budget spike (§8.1 of the
research doc) and the whisper.cpp Polish-accuracy check (§8.2) — both are
small, bounded, and answer the two biggest open feasibility questions cheaply
relative to committing to a full framework integration first. Then write the
M2 architecture ADR choosing among the A/B/C options in the research doc §7,
informed by those two spikes' results.
