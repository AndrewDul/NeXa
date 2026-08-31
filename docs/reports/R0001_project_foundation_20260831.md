# R0001 — Project foundation

- **Date:** 2026-08-31
- **Author:** Claude Code (agent), for Andrzej Dul
- **Milestone:** M0 — Foundation
- **Related:** `docs/decisions/ADR-0001_project_foundation.md`, `AGENTS.md`,
  `docs/CURRENT_STATE.md`, `docs/ROADMAP.md`,
  `docs/architecture/FOUNDATION_ARCHITECTURE.md`,
  `docs/legacy/LEGACY_NEXA_INDEX.md`

---

## TASK RESULT

**PASS**

M0 scope (project foundation only) is complete: repository structure,
documentation system, engineering conventions, Python skeleton, legacy
relationship, one coherent commit. No product features were implemented.

---

## Task objective

Establish the foundation of the **new canonical** NeXa repository
(`AndrewDul/NeXa`, local `NeXa_IkiGai`) from scratch:

- canonical naming and product framing (one NeXa across many devices;
  local-first / privacy-first / user-owned-data)
- an operating manual for future coding agents (`AGENTS.md`)
- documentation systems: current state, roadmap, architecture, ADRs, reports,
  research policy, test strategy, troubleshooting
- a documented, read-only relationship to the legacy repository
- a minimal modern Python project skeleton with a passing foundation test
- exactly one local commit; no push

Explicitly **out of scope** and not done: conversation, voice, memory, robot
control, UI, model routing, MAS, vision, device/capability registries, any
dependency install, any legacy migration.

---

## Verified starting repository state

`VERIFIED FACT` — commands run in `/home/devdul/Projects/NeXa_IkiGai`:

```
$ git remote -v
origin  https://github.com/AndrewDul/NeXa.git (fetch)
origin  https://github.com/AndrewDul/NeXa.git (push)

$ git branch --show-current
main

$ git status
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean

$ git log --oneline --decorate -10
62c1962 (HEAD -> main, origin/main, origin/HEAD) Initial commit

$ ls -la
README.md   (6 bytes)   + .git/
```

So the repo began with a single commit and a 6-byte `README.md`, nothing else.

`VERIFIED FACT` — runtime environment:

```
Python 3.13.5
git version 2.47.3
Linux nexa 6.18.39+rpt-rpi-2712 #1 SMP PREEMPT Debian ... aarch64 GNU/Linux
Debian GNU/Linux 13 (trixie)
```

`OBSERVATION` — `pip3 --version` resolves to
`/home/devdul/Projects/smart-desk-ai-assistant/.venv/.../pip` — i.e. the system
shell currently has the **legacy** repo's virtualenv on its path. The new repo
must always use its own `./.venv` (none created yet — see decisions).

---

## Verified legacy repository state (read-only inspection)

`VERIFIED FACT` — `/home/devdul/Projects/smart-desk-ai-assistant`:

```
$ git -C ... remote -v
origin  https://github.com/AndrewDul/smart-desk-ai-assistant.git

$ git -C ... branch --show-current
main

$ git -C ... rev-list --count HEAD
630

first commit: 2026-03-31 19:40:44 +0100
last  commit: 2026-08-24 00:36:56 +0100
latest: e12d018 feat: checkpoint canonical MAS runtime after reports 169-174
```

`VERIFIED FACT` — legacy top-level contains `modules/` (`core`, `devices`,
`features`, `nexa_agents`, `nexa_system`, `presentation`, `runtime`, `shared`,
`system`, `understanding`), a large `tests/` tree, `scripts/`, `benchmarks/`,
`docs/` (many subdirs), plus native builds `llama.cpp/` and `whisper.cpp/`, a
`.venv/`, and a `requirements.txt`.

`VERIFIED FACT` — `modules/nexa_agents/` contains ~50 agent packages (event bus,
task ledger, registry, turn supervisor, typed chat, conversation, planner,
reasoning, verifier, model router/zoo/health/benchmark, memory bus/writer/
retriever, identity, consent/privacy, safety policy, capability evidence, tts,
voice input, ui, …). `docs/Multi_Agent_System/` holds build reports `01`–`29`.
`docs/NeXa_Architecture_Decisions/` holds `ADR_001_core_brain_replaces_legacy_
runtime` plus a series of "core brain" / "model router" / "conversation
authority" / "model intelligence registry" design notes.

`INFERENCE` — the legacy repo has repeatedly replaced one central runtime with
another and accreted parallel supervisors; its own recent commit messages
("retire the ... third supervisor", "checkpoint canonical MAS runtime") confirm
this churn. This is the direct motivation for ADR-0001's "one canonical
authority, no fallback brains" rule.

Full breakdown captured in `docs/legacy/LEGACY_NEXA_INDEX.md`.

---

## Commands / checks performed

| # | Command | Purpose | Result |
|---|---|---|---|
| 1 | `pwd`, `git remote -v`, `git branch --show-current`, `git status`, `git log --oneline -10` | verify new repo identity/state | as quoted above |
| 2 | `python3 --version`, `git --version`, `uname -a`, `cat /etc/os-release` | verify runtime | as quoted above |
| 3 | `ls -la` legacy root; `git -C legacy log/branch/rev-list` | verify legacy state (read-only) | as quoted above |
| 4 | `find`, `ls` over legacy `docs/`, `modules/`, `tests/`, `scripts/`, `benchmarks/` | build legacy index | captured in index |
| 5 | `mkdir -p` foundation dirs | create skeleton | 13 dirs created |
| 6 | `python3 -m unittest discover -s tests -v` | run foundation tests | **PASS** (see TESTS) |
| 7 | `PYTHONPATH=src python3 -c "import nexa; print(nexa.__version__)"` | import check | `nexa 0.0.0` |
| 8 | `git status`, `git diff --stat` (pre-commit review) | verify only intentional changes | clean, all intentional |

No writes were made anywhere outside `/home/devdul/Projects/NeXa_IkiGai`.
The legacy repository was not modified.

---

## Files / directories created

### Root
- `README.md` (replaced the 6-byte placeholder) — what NeXa is, one-NeXa-across-
  devices, local-first direction, current status table, repo-vs-legacy, doc map.
- `AGENTS.md` — operating manual: required reading order, the 13-step engineering
  loop, 15 core principles, truth labels, report/ADR conventions, hard rules.
- `.gitignore` — Python, `.venv/`, `.env` (keeps `.env.example`), editor/OS,
  runtime data dirs.
- `.env.example` — documented (commented-out) configuration surface; consumed by
  nothing yet.
- `pyproject.toml` — hatchling build; `name=nexa`, `version=0.0.0`,
  `requires-python>=3.11`, **zero runtime deps**, `dev` extras (`pytest`,
  `ruff`), pytest + ruff config.

### `src/`
- `src/nexa/__init__.py` — intentionally empty placeholder package,
  `__version__ = "0.0.0"`.

### `tests/`
- `tests/test_foundation.py` — `unittest`-compatible; asserts the package
  imports, `__version__` is a string, and the 15 foundation files exist.

### `scripts/`
- `scripts/check.sh` (executable) — runs the foundation checks with no
  third-party deps.
- `scripts/README.md`

### `apps/`, `configs/`
- `README.md` in each — purpose + "empty at M0".

### `docs/`
- `docs/CURRENT_STATE.md` — short operational truth.
- `docs/ROADMAP.md` — M0…M5 staged, plus an unordered "later" list.
- `docs/architecture/FOUNDATION_ARCHITECTURE.md` — 14 conceptual boundaries,
  explicitly "not packages, not code".
- `docs/decisions/ADR-TEMPLATE.md`
- `docs/decisions/ADR-0001_project_foundation.md` — Accepted; covers D1 new repo,
  D2 local-first/privacy-first, D3 one-NeXa-across-devices, D4 provider
  abstraction.
- `docs/legacy/LEGACY_NEXA_INDEX.md` — where legacy is, why, how to use it,
  knowledge categories with verified paths, known unknowns.
- `docs/reports/R0001_project_foundation_20260831.md` — this file.
- `docs/research/RESEARCH_POLICY.md`
- `docs/testing/TEST_STRATEGY.md` — philosophy, tiers, stable IDs, placeholder
  benchmark IDs (`CONV-EN-001`, `CONV-PL-001`, …).
- `docs/troubleshooting/README.md` — record template.
- `docs/capabilities/README.md`, `docs/development/README.md`,
  `docs/hardware/README.md`, `docs/protocols/README.md` — purpose stubs.

Directory count created: `apps configs scripts src/nexa tests` +
`docs/{architecture,capabilities,decisions,development,hardware,legacy,protocols,reports,research,testing,troubleshooting}`.

---

## Architectural principles established

Recorded in `AGENTS.md` §3 and `docs/architecture/FOUNDATION_ARCHITECTURE.md`;
the load-bearing ones are ratified in ADR-0001:

1. One canonical authority per responsibility.
2. No silent fallback brains.
3. No duplicated supervisors / hidden alternate answer paths.
4. Models are provider abstractions; no provider name in core control flow.
5. MAS is opt-in for complex reasoning, never mandatory in a simple turn.
6. Device Awareness answers "what body do I have?" (M4).
7. Capability System answers "what can I do right now?" (M4).
8. Chat history and long-term memory are separate systems.
9. Online models get minimum required context only.
10. Realtime voice transport/framework stays replaceable.
11. Legacy code is a knowledge base, not the architecture authority.
12. Prefer mature existing solutions over rebuilding infrastructure.
13. Research before major architectural decisions.
14. No speculative abstractions / premature modules.
15. Minimum structure for the current milestone.

Cross-cutting: local-first, privacy-first, user-owned-data-first, modular,
provider-independent, device-independent, testable, observable, documented.

---

## Decisions made

- **ADR-0001 (Accepted):**
  - **D1** — new canonical repo `AndrewDul/NeXa`; legacy is read-only reference.
  - **D2** — local-first / privacy-first / user-owned-data are non-negotiable.
  - **D3** — one NeXa across many devices; devices are interchangeable bodies;
    identity/memory/context are central.
  - **D4** — model access is a provider abstraction from the start; one response
    authority; no fallback brains.
- **No `./.venv` created during M0.** Nothing requires one — the foundation
  tests run on the system interpreter via `unittest`. A repo-local `./.venv` is
  created in M1 with the first real dependency. (Recorded in CURRENT_STATE and
  ADR-0001 consequences / `docs/development/README.md`.)
- **`pyproject.toml` carries zero runtime dependencies.** Each subsystem adds its
  own, justified per ADR.
- **One foundational ADR instead of four** — the four decisions share one context
  and one consequence set (per `AGENTS.md` §6).

---

## Assumptions

- `ASSUMPTION` — the product definition in the M0 task prompt is authoritative
  (one system, many devices, local-first, privacy-first, provider/device
  independent).
- `ASSUMPTION` — the dev machine (`nexa`, Debian 13 aarch64) is also the intended
  first target device; not independently confirmed beyond the OS/arch strings.
- `ASSUMPTION` — `requires-python >= 3.11` is a safe floor (machine has 3.13.5;
  legacy used 3.13). Chosen for a little portability headroom.
- `ASSUMPTION` — hatchling is an acceptable build backend; trivially swappable
  while `src/nexa` is empty.

---

## Known unknowns

- Which legacy MAS components actually run vs. are scaffolding (`UNKNOWN` without
  executing the legacy repo, which is out of scope).
- Current pass/fail state of the legacy test suite (not run; must not be relied
  on here).
- Which of the legacy-declared Raspberry Pi peripherals are physically attached
  to this machine now.
- Final shape of the M1 model-provider abstraction (to be designed in an M1 ADR).
- Whether `pip`/`pytest`/`ruff` are available to a fresh repo-local venv offline
  (not tested — no venv created, no install performed).

---

## Legacy repository relationship

- Path `/home/devdul/Projects/smart-desk-ai-assistant`; remote
  `AndrewDul/smart-desk-ai-assistant`; branch `main`; 630 commits.
- Status in this project: **reference / knowledge base only.** Never modified,
  never deleted, never bulk-migrated. Reuse must be deliberate, file-scoped, and
  re-verified against `AGENTS.md` §3.
- Documented in `docs/legacy/LEGACY_NEXA_INDEX.md` (knowledge categories: reports,
  architecture/decisions, MAS experiment, core runtime, voice pipeline, local
  model integration, Raspberry Pi hardware, robot drivers, UI, tests,
  troubleshooting, legacy dependency list).
- This M0 task **read** the legacy repo for the index and for ADR-0001 context.
  It wrote nothing to it.

---

## Tests / checks performed

`VERIFIED FACT` — `python3 -m unittest discover -s tests -v`:

```
test_required_files_exist (test_foundation.TestFoundationFiles...) ... ok
test_package_imports (test_foundation.TestPackage...) ... ok
test_version_is_string (test_foundation.TestPackage...) ... ok
----------------------------------------------------------------------
Ran 3 tests in 0.00s
OK
```

(3 tests, 5 assertions: package import, `__version__` type, 15 required
foundation files present.)

`VERIFIED FACT` — `PYTHONPATH=src python3 -c "import nexa; print(nexa.__version__)"`
→ `nexa 0.0.0`.

`OBSERVATION` — an earlier run of the same suite failed on
`test_required_files_exist` only, listing `R0001_...md` as missing; it passed once
this report file was written. This is the expected bootstrapping order.

`pytest` / `ruff` were **not** run — not installed on the system interpreter and
no venv was created (deliberate; see decisions).

No secrets were committed. No legacy `.venv` or generated artefacts are tracked
(`.gitignore` covers `.venv/`, `__pycache__/`, `.env`, `var/`, `logs/`, etc.).

---

## Git status

At report time, before the M0 commit: working tree contained only the new
foundation files listed above; `git diff --stat` limited to those paths;
`README.md` modified from the 6-byte placeholder. One coherent commit created on `main`:

```
chore: establish NeXa project foundation
25 files changed, ~1800 insertions(+), 1 deletion(-)
```

`main` is 1 commit ahead of `origin/main`. No push performed (left to the user).
The exact hash is whatever `git log -1 --format=%H` reports on `main`; it is
intentionally not hard-coded into tracked files (it shifts on amend). It is
quoted in the session summary handed to the user.

---

## Unresolved items

- None blocking M0. Deferred by design: venv creation, dependency install, CI
  setup, `pytest`/`ruff` execution — all belong to M1 or later.

---

## Exact next recommended task

**Begin M1 — Natural Text Conversation.** Concretely, in order:

1. Write an **M1 ADR** proposing: the single canonical text-conversation turn
   path (one response authority), the **model-provider abstraction** boundary
   (interface shape, local-first default provider, how online providers get
   minimum context), and the explicit line between chat history and long-term
   memory.
2. Create the repo-local `./.venv`; add the first real dependency; `pip install
   -e ".[dev]"`; confirm `pytest` + `ruff` run.
3. Implement one turn path in `src/nexa/` with at least one local provider and a
   deterministic test around the turn contract.
4. Define the `CONV-EN-001` / `CONV-PL-001` benchmark case sets (documented;
   not necessarily all passing).

Do **not** introduce MAS, multiple brains, or fallback answer paths.
