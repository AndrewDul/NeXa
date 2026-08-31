# docs/protocols/

Home for **internal contracts and wire protocols** between NeXa components once
they exist:

- boundaries between conceptual areas (Conversation ↔ Context, Voice ↔
  Conversation, etc.)
- device ↔ core coordination for multi-device
- any message schema or event format that more than one component depends on

Each protocol here should be versioned and have a stable name.

Empty at M0 — no protocols are defined yet. The first candidates arrive with
M1 (conversation turn contract) and M2 (voice transport boundary).
