# configs/

Declarative configuration for NeXa: environment profiles, provider selection,
device profiles, feature toggles.

Principles:

- Configuration selects **which** provider/device/mode — never hard-codes a
  provider name into core control flow (`AGENTS.md` §3.4).
- Secrets never live here. Use `.env` (gitignored); `.env.example` documents the
  surface.
- Config files here are committed and non-secret.

Empty at M0. Real config lands with the first subsystem that needs it (M1).
