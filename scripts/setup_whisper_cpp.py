#!/usr/bin/env python3
"""Build/install the pinned whisper.cpp runtime + model for NeXa's M2.2 STT.

Developer/operational setup script (`scripts/README.md`) — no product logic
lives here; the pinned version and path resolution are owned by
`src/nexa/stt/config.py` (the canonical source of truth), imported here
rather than duplicated.

What this does, explicitly, only when you run it:
1. Clones `ggml-org/whisper.cpp` at the exact pinned tag (never `main`) into
   an external, NeXa-owned data directory (XDG data dir, e.g.
   `~/.local/share/nexa/stt/` — outside this git repo, never committed).
2. Builds `whisper-cli` + `whisper-quantize` (Release, CMake).
3. Downloads the `base` GGML model via whisper.cpp's own download script and
   quantizes it to `q8_0` (the ADR-0003/R0006 measured baseline).

Idempotent: re-running skips steps whose output already exists. Use --force
to rebuild from scratch. Never runs automatically at NeXa import/runtime —
`src/nexa/stt/transcriber.py` raises an explicit error if these paths are
missing instead of triggering a network download.

Usage:
    python3 scripts/setup_whisper_cpp.py
    python3 scripts/setup_whisper_cpp.py --force
    python3 scripts/setup_whisper_cpp.py --print-paths   # just show where things live
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexa.stt.config import (  # noqa: E402
    MODEL_NAME,
    MODEL_QUANT,
    WHISPER_CPP_COMMIT,
    WHISPER_CPP_REPO,
    WHISPER_CPP_TAG,
    data_dir,
    default_model_path,
    default_whisper_cli_path,
    whisper_cpp_dir,
)


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_clone(force: bool) -> Path:
    repo_dir = whisper_cpp_dir()
    if repo_dir.exists() and not force:
        print(f"whisper.cpp already cloned at {repo_dir}", file=sys.stderr)
        return repo_dir
    if repo_dir.exists() and force:
        shutil.rmtree(repo_dir)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["git", "clone", "--branch", WHISPER_CPP_TAG, "--depth", "1",
         WHISPER_CPP_REPO, str(repo_dir)]
    )
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()
    if actual != WHISPER_CPP_COMMIT:
        raise RuntimeError(
            f"whisper.cpp tag {WHISPER_CPP_TAG} resolved to commit {actual}, "
            f"expected pinned commit {WHISPER_CPP_COMMIT}. Upstream may have "
            f"moved the tag — do not proceed silently; update the pin in "
            f"src/nexa/stt/config.py deliberately if this is expected."
        )
    return repo_dir


def ensure_build(repo_dir: Path, force: bool) -> None:
    binary = default_whisper_cli_path()
    if binary.exists() and not force:
        print(f"whisper-cli already built at {binary}", file=sys.stderr)
        return
    build_dir = repo_dir / "build"
    run(["cmake", "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"], cwd=repo_dir)
    nproc = str(os.cpu_count() or 4)
    run(
        ["cmake", "--build", str(build_dir), "--config", "Release", "-j", nproc,
         "--target", "whisper-cli", "whisper-quantize"],
        cwd=repo_dir,
    )
    if not binary.exists():
        raise RuntimeError(f"build finished but {binary} does not exist")


def ensure_model(repo_dir: Path, force: bool) -> None:
    quantized = default_model_path()
    if quantized.exists() and not force:
        print(f"model already present at {quantized}", file=sys.stderr)
        return
    fp16_path = repo_dir / "models" / f"ggml-{MODEL_NAME}.bin"
    if not fp16_path.exists() or force:
        run(["bash", "./models/download-ggml-model.sh", MODEL_NAME], cwd=repo_dir)
    quantize_bin = repo_dir / "build" / "bin" / "whisper-quantize"
    quantized.parent.mkdir(parents=True, exist_ok=True)
    run([str(quantize_bin), str(fp16_path), str(quantized), MODEL_QUANT])
    if not quantized.exists():
        raise RuntimeError(f"quantization finished but {quantized} does not exist")


def print_paths() -> None:
    print(f"data dir:      {data_dir()}")
    print(f"whisper.cpp:   {whisper_cpp_dir()} "
          f"(tag {WHISPER_CPP_TAG}, commit {WHISPER_CPP_COMMIT})")
    print(f"whisper-cli:   {default_whisper_cli_path()} "
          f"({'exists' if default_whisper_cli_path().exists() else 'MISSING'})")
    print(f"model:         {default_model_path()} "
          f"({'exists' if default_model_path().exists() else 'MISSING'})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--force", action="store_true", help="rebuild/redownload even if already present"
    )
    parser.add_argument(
        "--print-paths", action="store_true", help="print paths and exit, no setup"
    )
    args = parser.parse_args()

    if args.print_paths:
        print_paths()
        return 0

    for tool in ("git", "cmake"):
        if shutil.which(tool) is None:
            print(f"error: required tool {tool!r} not found on PATH", file=sys.stderr)
            return 1

    repo_dir = ensure_clone(args.force)
    ensure_build(repo_dir, args.force)
    ensure_model(repo_dir, args.force)

    print("\nSetup complete.", file=sys.stderr)
    print_paths()
    return 0


if __name__ == "__main__":
    sys.exit(main())
