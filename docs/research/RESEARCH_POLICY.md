# RESEARCH POLICY

How technology and architecture choices get made in NeXa. Applies to every agent.

---

## Order of investigation

1. **Existing project evidence first.** `docs/CURRENT_STATE.md`, reports
   (`docs/reports/`), ADRs (`docs/decisions/`), architecture docs,
   troubleshooting records, and the actual code / tests in this repo.
2. **Legacy NeXa.** If the area was worked on there, read it for constraints,
   failure modes, working parameters, and hardware quirks
   (`docs/legacy/LEGACY_NEXA_INDEX.md`). Knowledge, not architecture.
3. **Official documentation** of any candidate tool / library / protocol.
4. **Mature open-source implementations.** Read how established projects actually
   solve the problem — not blog summaries of them.
5. **GitHub issues / discussions / changelogs.** Known bugs, deprecations,
   maintenance health, migration pain.
6. **Benchmarks and papers** where relevant (quality, latency, resource use),
   preferring reproducible ones.
7. **Community sources** (forums, Discord, Q&A) when they provide concrete
   practical evidence — treated as `OBSERVATION`, verified before relying on it.

## Rules

- **Prefer mature existing solutions** over rebuilding solved infrastructure
  (`AGENTS.md` §3.12). Rebuilding needs a stated reason.
- **Do not choose a tool because it is fashionable.** Popularity is not evidence
  of fit.
- **Every claim carries a truth label** (`AGENTS.md` §4). "The docs say X" is
  `OBSERVATION` until verified; "I ran it and X happened" is `VERIFIED FACT`.
- **Major technology choices require evidence and normally an ADR.** A "major"
  choice is one that is costly to reverse, spans subsystems, adds a heavy
  dependency, or constrains a future milestone.
- **Local-first / privacy-first / provider-independence are filters.** A tool
  that forces cloud dependence, phones home, or can't be abstracted behind a
  provider boundary is disqualified regardless of quality — unless an ADR
  explicitly accepts the trade-off.
- **Record what you consulted.** Reports state `EXTERNAL RESEARCH USED YES/NO` and
  list the sources that actually influenced the decision.

## Output of a research task

A short findings section (in the report or a `docs/research/<topic>.md` note):
the question, options compared, evidence per option with labels, the
recommendation, and whether an ADR is required.

---

No research notes exist yet (M0).
