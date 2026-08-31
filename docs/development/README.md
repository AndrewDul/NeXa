# docs/development/

Developer-facing notes: local setup, workflow, conventions, and tooling as they
solidify.

The authoritative process for engineering work is **`AGENTS.md`** at the repo
root. This directory holds supporting detail, not a competing process.

## Environment (M0)

`VERIFIED FACT` (2026-08-31):

- Python `3.13.5` on the dev/target machine (Debian 13 "trixie", `aarch64`,
  hostname `nexa`, kernel `6.18.39+rpt-rpi-2712`).
- `git 2.47.3`.
- **No repo-local virtual environment yet.** M0 needs none — foundation tests run
  on the system interpreter via `python -m unittest discover -s tests`. See
  `docs/decisions/ADR-0001_project_foundation.md` and `docs/CURRENT_STATE.md`.

When M1 introduces the first real dependency:

```bash
python -m venv .venv          # repo-local ONLY; .venv/ is gitignored
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Never activate or reuse the legacy repo's `.venv`.
