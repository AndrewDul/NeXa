"""Renders ``NeXaIdentity`` into a short system-prompt instruction block.

A pure function: same identity in, same string out, no I/O, no side
effects. This is deliberately NOT a general context/prompt engine (that is
a future, separate Context Engine, out of scope for M3.1 — ADR-0005) — it
only ever renders the immutable identity root.
"""

from __future__ import annotations

from .model import NeXaIdentity


def render_identity_instruction(identity: NeXaIdentity) -> str:
    principles = "\n".join(f"- {p}" for p in identity.principles)
    return (
        f"You are {identity.name}, part of {identity.product_name}.\n"
        f"Purpose: {identity.purpose}\n"
        f"Principles:\n{principles}"
    )
