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

# The frozen M1.1 local baseline (ADR-0002 Amendment 2). A configuration
# default, not an identity — NeXa is not this model.
DEFAULT_LOCAL_MODEL = "gemma4:e4b"
DEFAULT_LOCAL_MODEL_ENDPOINT = "http://127.0.0.1:11434"


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


def local_provider_settings_from_env() -> LocalProviderSettings:
    """Read local-provider configuration from the environment.

    ``NEXA_MODEL_PROVIDER`` selects the provider family; only ``"local"`` is
    implemented in M1.1. Anything else fails closed with an explicit error —
    it never silently falls back to the local provider (AGENTS.md §3.2).
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
    )
