#!/usr/bin/env bash
# M0 foundation checks. No third-party dependencies required.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "== python version =="
python3 --version

echo "== foundation tests (unittest) =="
python3 -m unittest discover -s tests -v

echo "== OK =="
