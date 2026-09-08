"""Loading NeXa's small M1.1 configuration surface: the versioned persona and
provider selection from the environment.

Configuration selects *which* provider/model/persona — it never hard-codes a
provider name into core control flow (AGENTS.md §3.4, `configs/README.md`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .providers.base import GenerationOptions

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONA_PATH = REPO_ROOT / "configs" / "personas" / "nexa_persona_v1.json"

# The frozen M1.1 local baseline (ADR-0002 Amendment 2, re-affirmed by the
# M2.4B.3.5 operator blind A/B — R0022). A configuration default, not an
# identity — NeXa is not this model. There is no second (smaller-model or
# per-language) conversation model authority.
DEFAULT_LOCAL_MODEL = "gemma4:e4b"
DEFAULT_LOCAL_MODEL_ENDPOINT = "http://127.0.0.1:11434"

# M2.4B.3.6 serving-freeze policy (R0021 measured, R0022 re-validated under a
# concurrent Piper load). These are canonical NeXa provider policy — set once
# here, never changed dynamically during a session.
#
# ``num_thread=2`` is the fastest decode configuration for ``gemma4:e4b`` on
# this 4-core Pi 5: R0021 = +12–14 % tok/s uncontended, R0022 = ~1.69 -> ~2.85
# tok/s (+68 %) under a live Piper (``nice +10``) load, with Piper RTF
# unchanged and no throttling. It is an Ollama request option only — no
# ``taskset``, no ``renice`` on the LLM, no realtime scheduler policy.
DEFAULT_LOCAL_NUM_THREAD = 2

# ``keep_alive`` residency for an always-on personal assistant. 5m evicts the
# model between conversations and every cold first turn then pays the full
# model load (~33 s) + ~500-token persona/voice prefix reprocess (~29–40 s,
# R0021). 30m keeps it resident across normal gaps. NOT infinite: ``gemma4:e4b``
# is ~10 GB resident on the 16 GB Pi, so unbounded residency is a separate
# resource-policy decision for when NeXa runs more concurrent capabilities.
DEFAULT_LOCAL_KEEP_ALIVE = "30m"


@dataclass(frozen=True, slots=True)
class PersonaConfig:
    id: str
    system: str
    options: GenerationOptions


def load_persona(path: Path | str = DEFAULT_PERSONA_PATH) -> PersonaConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    options = GenerationOptions(**data["options"])
    return PersonaConfig(id=data["id"], system=data["system"], options=options)


@dataclass(frozen=True, slots=True)
class LocalProviderSettings:
    model: str
    base_url: str
    num_thread: int | None = DEFAULT_LOCAL_NUM_THREAD
    keep_alive: str | None = DEFAULT_LOCAL_KEEP_ALIVE


def _num_thread_from_env() -> int | None:
    """``NEXA_LOCAL_NUM_THREAD`` override for the M2.4B.3.6 serving policy.

    Unset -> ``DEFAULT_LOCAL_NUM_THREAD``. An explicit ``0`` (or a negative /
    non-integer value) means "let Ollama pick" — the provider then sends no
    ``num_thread`` at all. This is provider policy, resolved once here.
    """
    raw = os.environ.get("NEXA_LOCAL_NUM_THREAD")
    if raw is None:
        return DEFAULT_LOCAL_NUM_THREAD
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def local_provider_settings_from_env() -> LocalProviderSettings:
    """Read local-provider configuration from the environment.

    ``NEXA_MODEL_PROVIDER`` selects the provider family; only ``"local"`` is
    implemented in M1.1. Anything else fails closed with an explicit error —
    it never silently falls back to the local provider (AGENTS.md §3.2).

    ``NEXA_LOCAL_NUM_THREAD`` / ``NEXA_LOCAL_KEEP_ALIVE`` override the
    M2.4B.3.6 serving-freeze defaults (``DEFAULT_LOCAL_NUM_THREAD`` /
    ``DEFAULT_LOCAL_KEEP_ALIVE``). They are one-time provider policy, not a
    per-session knob.
    """
    provider = os.environ.get("NEXA_MODEL_PROVIDER", "local")
    if provider != "local":
        raise NotImplementedError(
            f"NEXA_MODEL_PROVIDER={provider!r} is not implemented in M1.1 "
            "(only 'local' — AUTO/LOCAL ONLY/CLOUD PREFERRED policies are a "
            "later milestone, not a silent fallback)."
        )
    return LocalProviderSettings(
        model=os.environ.get("NEXA_LOCAL_MODEL", DEFAULT_LOCAL_MODEL),
        base_url=os.environ.get("NEXA_LOCAL_MODEL_ENDPOINT", DEFAULT_LOCAL_MODEL_ENDPOINT),
        num_thread=_num_thread_from_env(),
        keep_alive=os.environ.get("NEXA_LOCAL_KEEP_ALIVE", DEFAULT_LOCAL_KEEP_ALIVE),
    )
