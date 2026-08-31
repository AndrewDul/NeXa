# AGENTS.md — Operating manual for coding agents in the NeXa repository

This file is the operating manual for every AI coding agent (and human) doing
engineering work in `AndrewDul/NeXa` (local workspace `NeXa_IkiGai`). Read it
fully before doing meaningful work.

If anything here conflicts with a specific instruction in a task prompt, prefer
the task prompt, but call out the conflict in your report.

---

## 0. What NeXa is (so you don't drift)

NeXa is **one** personal AI system / companion / platform owned by a single user,
intended to exist across many devices (robot, PC, phone, tablet, web, later smart
home / car / wearables) as **bodies of the same NeXa** — same identity, memory,
context, behaviour.

NeXa is **not** just a chatbot, a robot, Raspberry Pi software, a voice assistant,
a UI, a single LLM, or a multi-agent system. Do not let one device or one
framework define the architecture.

Architecture must stay: local-first, privacy-first, user-owned-data-first,
modular, provider-independent, device-independent, testable, observable,
documented.

---

## 1. Start every meaningful task by reading, in order

1. **`AGENTS.md`** (this file)
2. **`docs/CURRENT_STATE.md`** — the operational truth
3. **`docs/ROADMAP.md`** — where we are in the milestone plan
4. **Relevant architecture docs** — `docs/architecture/`
5. **Relevant ADRs** — `docs/decisions/`
6. **The latest relevant report** — `docs/reports/` (highest `R####`)
7. **Relevant troubleshooting entries** — `docs/troubleshooting/`
8. **Relevant legacy index / reference if needed** —
   `docs/legacy/LEGACY_NEXA_INDEX.md`

Do not skip step 2. `CURRENT_STATE.md` tells you what is real right now.

---

## 2. The engineering loop (every meaningful task)

1. **Understand** the task and its scope. Write the scope down.
2. **Verify current state** — read the code and run it / test it. Do not trust
   stale docs over runtime evidence.
3. **Search project knowledge** — reports, ADRs, architecture, troubleshooting.
4. **Inspect Legacy NeXa** if the area was touched there (voice, STT/TTS,
   streaming, Ollama/llama.cpp, Raspberry Pi hardware, robot drivers, UI, MAS).
   Read only — never modify the legacy repo.
5. **Research external mature solutions** if useful (official docs, mature
   open-source, GitHub issues, benchmarks/papers). Prefer proven infrastructure
   over rebuilding solved problems. See `docs/research/RESEARCH_POLICY.md`.
6. **Form a small coherent plan.** Minimum structure for the current milestone.
   No speculative abstractions, no premature modules.
7. **Implement.**
8. **Test** — real behaviour over superficial mocks. See
   `docs/testing/TEST_STRATEGY.md`.
9. **Verify actual behaviour** — run it, observe it, capture evidence.
10. **Update documentation** — architecture docs, ADRs, troubleshooting.
11. **Update `docs/CURRENT_STATE.md`** so it matches reality.
12. **Produce a detailed report** in `docs/reports/` (see §5).
13. **Leave the repo in a clear state** — intentional changes only, one coherent
    commit, no junk, no secrets.

---

## 3. Core engineering principles (do not violate without an ADR)

1. **One canonical authority per responsibility.** One place decides each thing.
2. **No silent fallback brains.** No hidden alternate answer path that quietly
   takes over.
3. **No duplicated supervisors** or parallel "final answer" pipelines.
4. **Models are provider abstractions**, not hard-coded architectural
   dependencies. No provider name baked into core control flow.
5. **MAS is opt-in for complex reasoning**, not a mandatory layer in every simple
   conversation turn.
6. **Device Awareness** must eventually answer: *"What hardware / body do I
   currently have?"*
7. **Capability System** must eventually answer: *"What can I actually do with
   this hardware, these permissions, and this runtime state?"*
8. **Chat history and long-term NeXa memory are separate systems.**
9. **Online models receive only the minimum required context.**
10. **Realtime voice transport / framework must stay replaceable.**
11. **Legacy code is a knowledge base, not the architecture authority.**
12. **Prefer mature existing solutions** over rebuilding solved infrastructure.
13. **Research before committing** major architectural decisions.
14. **Avoid speculative abstractions** and premature modules.
15. **Build the minimum structure** required for the current milestone.

---

## 4. Evidence / truth labels

Label claims in docs and reports so readers know their weight:

| Label | Meaning |
|---|---|
| `VERIFIED FACT` | Confirmed by running code / tests / direct inspection this session |
| `OBSERVATION` | Something seen in output/logs, not yet fully explained |
| `INFERENCE` | A conclusion reasoned from evidence |
| `ASSUMPTION` | Taken as true without proof; must be flagged |
| `HYPOTHESIS` | A candidate explanation to be tested |
| `PROPOSAL` | A suggested direction, not a decision |
| `UNKNOWN` | Explicitly not known |

Runtime / code / test evidence outranks stale documentation. If a doc contradicts
reality: **record the discrepancy, update the doc, do not hide it.**

Never invent facts about hardware, software, architecture, or legacy code.

---

## 5. Reports

- Location: `docs/reports/`
- Sequential IDs: `R0001`, `R0002`, … (never reuse or skip)
- Filename: `R####_<short_description>_YYYYMMDD.md`
- Convert relative dates to absolute (the system date is authoritative).

Every report should normally contain:

```
TASK RESULT
PASS / PARTIAL / FAIL

WHAT I DID
WHAT I VERIFIED
TESTS
UNRESOLVED
DOCUMENTATION / REPORTS UPDATED
LEGACY NEXA USED        YES / NO
EXTERNAL RESEARCH USED  YES / NO
CURRENT VERIFIED STATE
NEXT RECOMMENDED ACTION
```

The first report is `docs/reports/R0001_project_foundation_20260831.md`.

---

## 6. ADRs (Architecture Decision Records)

- Location: `docs/decisions/`
- Template: `docs/decisions/ADR-TEMPLATE.md`
- Filename: `ADR-0001_<decision>.md`, sequential.
- Create an ADR only for a **real** decision with trade-offs and consequences.
  Do not create speculative ADRs. Prefer one clear foundational ADR over several
  artificial ones.
- The roadmap and these principles may evolve — but only through an ADR plus
  evidence.

---

## 7. Hard rules for this repository

- **Never** `git push --force`.
- **Never** `git reset --hard`.
- **Never** modify or delete the legacy repository
  (`/home/devdul/Projects/smart-desk-ai-assistant`).
- **Never** bulk-migrate legacy code or legacy dependencies. Reuse must be
  deliberate, file-scoped, and verified.
- **Never** modify files outside `NeXa_IkiGai` except read-only investigation.
- Do not install large / heavy dependencies without an ADR.
- Do not commit secrets. `.env` is gitignored; keep `.env.example` as the
  documented template.
- Any virtual environment lives at `./.venv` inside this repo and is gitignored.
  Never reuse the legacy repo's `.venv`.
- Create one coherent commit per completed task. Do not push unless explicitly
  asked — leave push to the user.

---

## 8. Scope discipline

Do not add product features "while you're here". If a task is M0/foundation, it
stays foundation. If you discover work that should happen, record it in
`docs/CURRENT_STATE.md` under *next recommended task* or as a `PROPOSAL` in a
report — do not silently implement it.
