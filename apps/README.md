# apps/

Runnable front-ends / entry points — the things a user or device actually
launches (e.g. a chat CLI, a device runtime, later desktop/web/mobile shells).

An app is a thin front-end onto the core in `src/nexa/`. It contains wiring and
presentation, not product logic.

Empty at M0. The first app arrives with M1 (a minimal NeXa Chat entry point).
