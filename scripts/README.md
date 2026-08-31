# scripts/

Developer and operational helper scripts (checks, diagnostics, one-off tooling).

Scripts are convenience wrappers — they must not contain product logic that
belongs in `src/nexa/`.

## Available now

- `check.sh` — runs the M0 foundation checks (`python -m unittest`). No
  third-party dependencies required.
