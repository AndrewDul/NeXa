# CURRENT_STATE

Short operational truth. Keep this file current after every meaningful task.
Runtime / test evidence outranks anything else in this repo.

---

- **Last verified:** 2026-08-31
- **Repository:** `AndrewDul/NeXa` (`https://github.com/AndrewDul/NeXa.git`)
- **Local workspace:** `/home/devdul/Projects/NeXa_IkiGai`
- **Branch:** `main` — HEAD is `chore: establish NeXa project foundation`
  (1 commit ahead of `origin/main`, not pushed; run `git log -1` for the hash)
- **Latest report:** `docs/reports/R0001_project_foundation_20260831.md`
- **Current milestone:** **M0 — Foundation**
- **Current objective:** Establish repository structure, documentation system,
  engineering conventions, and the legacy relationship. No product features.

---

## What works (VERIFIED FACT)

- Repository is a well-formed, importable Python project (`src/nexa`,
  `pyproject.toml`, `tests/`).
- Foundation test suite (`tests/test_foundation.py`) passes under plain
  `python -m unittest` — see R0001 for the command and output.
- Documentation system exists: `CURRENT_STATE.md`, `ROADMAP.md`, architecture doc,
  ADR system (template + `ADR-0001`), report system (`R0001`), research policy,
  test strategy, troubleshooting format, legacy index.
- `AGENTS.md` operating manual is in place at the repo root.

## What is partial

- Nothing. M0 has no partially-built subsystems by design.

## What is not implemented (by design at M0)

- Natural text conversation (M1)
- Realtime voice (M2)
- Context system (M3)
- Device awareness + capability registry (M4)
- Long-term memory (M5)
- Personal vault, tools, reasoning/router, vision, robot adapter, multi-device
  runtime, mobile apps, richer UI (later milestones)
- No Python virtual environment created yet — see *current architecture state*.

## Known problems

- None recorded. (`OBSERVATION`: legacy `pip` on this machine resolves to the
  legacy repo's `.venv`; the NeXa repo must always use its own `./.venv` once one
  is created.)

## Current architecture state

- Conceptual boundaries are documented in
  `docs/architecture/FOUNDATION_ARCHITECTURE.md`. They are **conceptual only** —
  not Python packages, not interfaces, not code.
- `src/nexa/__init__.py` is an intentionally empty placeholder package.
- **Decision (documented in ADR-0001):** no `./.venv` is created during M0
  because nothing requires one — the foundation tests run on the system
  interpreter with `unittest`. A repo-local `./.venv` will be created in M1 when
  the first real dependency lands.

## Current test status

- `tests/test_foundation.py`: **PASS** (5 assertions across 3 tests) via
  `python -m unittest`. No `pytest`/`ruff` installed on the system interpreter;
  they are declared as `dev` extras for M1.

## Active architectural decisions

- **ADR-0001 — NeXa project foundation** (Accepted): new canonical repository
  separate from legacy; local-first / privacy-first / user-owned-data;
  one NeXa across many devices; provider-independence as a first principle.

## Current focus

- M0 complete. No further foundation work planned.

## Exact next recommended task

**Begin M1 — Natural Text Conversation.** First concrete step: write an ADR
proposing the M1 scope and the model-provider abstraction boundary (interface
shape, local-first default, how online providers receive minimum context), then
create a repo-local `./.venv`, add the first real dependency, and stand up a
single canonical text-conversation turn path with tests. Do **not** introduce MAS,
multiple brains, or fallback answer paths.
