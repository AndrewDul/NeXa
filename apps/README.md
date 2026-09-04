# apps/

Runnable front-ends / entry points — the things a user or device actually
launches (e.g. a chat CLI, a device runtime, later desktop/web/mobile shells).

An app is a thin front-end onto the core in `src/nexa/`. It contains wiring and
presentation, not product logic.

## Available now

- `nexa_chat.py` (M1.1) — minimal developer text-conversation harness. Drives
  the real `nexa.bootstrap.build_default_session()` canonical path
  (`ConversationSession` → `ModelProvider` → Ollama). **Not** the final NeXa
  Chat UI — just proof the real path works end to end from a terminal.
