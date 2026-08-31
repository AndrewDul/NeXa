# Troubleshooting records

When something breaks and the fix is non-obvious, write it down here. **Failed
attempts are part of the record** — they save the next person (or agent) from
repeating them.

- One file per problem: `YYYYMMDD_<short-slug>.md`.
- Link the record from the relevant report (`R####`) and, if it changes a
  decision, from an ADR.
- Use the truth labels from `AGENTS.md` §4.

There are no troubleshooting records yet (M0).

---

## Record template

```markdown
# <short title>

- Date:
- Author (agent / human):
- Related: R####, ADR-####, files

## Symptom
What was observed, exact error text / log lines, how to reproduce.

## Environment
OS, kernel, arch, Python version, hardware attached, branch, commit,
relevant config / env vars.

## Evidence
Commands run and their output. Logs. Screenshots/paths. Label each item
(VERIFIED FACT / OBSERVATION / INFERENCE / ...).

## Root cause
The actual cause, once known. If still UNKNOWN, say so.

## Attempted fixes
Everything tried, in order.

## Failed attempts
What did NOT work and why — kept deliberately so it is not retried.

## Final solution
The change that fixed it. Diff / commands / config.

## Verification
How the fix was confirmed (tests, runtime observation, evidence).

## Prevention
Guard test, doc change, assertion, ADR, or process change that stops a
recurrence.
```
