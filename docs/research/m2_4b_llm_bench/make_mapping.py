"""M2.4B.3.5 — generate the sealed operator-blind A/B mapping.

Randomly (``secrets.SystemRandom``) assigns the two allowed models to
Candidate A / Candidate B and writes the sealed mapping file. Prints only
a neutral confirmation + the file's SHA-256 (for later audit) — **never**
the assignment. Refuses to overwrite an existing mapping unless
``--force`` (so a stray re-run cannot reshuffle the test mid-flight).

    python docs/research/m2_4b_llm_bench/make_mapping.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blind_ab_common import ALLOWED_MODELS, CANDIDATE_LABELS, MAPPING_PATH  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing sealed mapping (DANGER: reshuffles)")
    args = ap.parse_args()

    if MAPPING_PATH.exists() and not args.force:
        digest = hashlib.sha256(MAPPING_PATH.read_bytes()).hexdigest()
        print(f"sealed mapping already exists: {MAPPING_PATH.name}")
        print(f"sha256: {digest}")
        print("(pass --force to reshuffle — do not do this mid-test)")
        return 0

    models = list(ALLOWED_MODELS)
    secrets.SystemRandom().shuffle(models)
    mapping = dict(zip(CANDIDATE_LABELS, models, strict=True))

    payload = {
        "purpose": "M2.4B.3.5 operator-blind model A/B — SEALED. Do not print.",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "salt": secrets.token_hex(16),
        "mapping": mapping,
    }
    MAPPING_PATH.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    digest = hashlib.sha256(MAPPING_PATH.read_bytes()).hexdigest()

    # Neutral output only — never echo the assignment.
    print(f"sealed mapping written: {MAPPING_PATH.name}")
    print(f"labels: {', '.join(CANDIDATE_LABELS)}   models (both present, order hidden): "
          f"{len(ALLOWED_MODELS)}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
