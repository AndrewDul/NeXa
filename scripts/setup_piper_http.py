#!/usr/bin/env python3
"""Build/install the external Piper HTTP TTS runtime for NeXa's M2.4 TTS.

Developer/operational setup script (`scripts/README.md`) — no product logic
lives here; the pinned version/voice identifiers and path resolution are
owned by `src/nexa/tts/config.py` (the canonical source of truth), imported
here rather than duplicated — the same pattern `scripts/setup_whisper_cpp.py`
already established for whisper.cpp.

What this does, explicitly, only when you run it:
1. Creates a virtual environment at an external, NeXa-owned data directory
   (XDG data dir, e.g. `~/.local/share/nexa/tts/piper-http-venv/` —
   outside this git repo, never committed) — deliberately NOT NeXa's own
   `.venv`, so the GPL-3.0-licensed `piper-tts` package is never imported
   into NeXa's own process (R0010; ADR-0003 D6).
2. Installs the exact pinned `piper-tts[http]==<version>` into that venv.
3. Provisions the two explicitly configured voices (PL + EN) into
   `~/.local/share/nexa/tts/voices/`. If the exact same voice files are
   already present under the prior local assistant's read-only reference
   location (`LEGACY_VOICES_DIR` below — the same voices that assistant's
   own verified config used), they are copied from there (byte-identical,
   already-verified assets, no network needed). Otherwise they are
   downloaded fresh via the EXTERNAL venv's own `piper` package (never
   imported into this script's own process either). Either way, this is a
   one-time, explicit, developer-triggered step — NeXa's own runtime never
   reads from the legacy path (`nexa.tts`/`nexa.voice_tts` only ever read
   from this script's output directory).
4. Verifies NLTK's `punkt_tab` sentence-tokenizer data (used by Pipecat's
   own sentence aggregation) is present, downloading it explicitly here —
   the one, deliberate, developer-triggered place this ever touches the
   network for it. NeXa's own runtime never downloads it silently
   (`nexa.voice_tts.ensure_sentence_tokenizer_data` raises an explicit
   error instead if it's missing).

Idempotent: re-running skips steps whose output already exists. Use
--force to rebuild from scratch. Never runs automatically at NeXa
import/runtime.

Usage:
    python3 scripts/setup_piper_http.py
    python3 scripts/setup_piper_http.py --force
    python3 scripts/setup_piper_http.py --print-paths
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexa.tts.config import (  # noqa: E402
    EN_VOICE,
    PIPER_TTS_VERSION,
    PL_VOICE,
    PiperHttpConfig,
    data_dir,
    piper_venv_dir,
    piper_venv_python,
    voices_dir,
)

# Read-only reference only (AGENTS.md — legacy is a knowledge base, never an
# architecture/runtime dependency). Optional: if present, its voice assets
# are copied byte-for-byte into NeXa's own external TTS data dir instead of
# re-downloading identical files. NeXa's own runtime code never reads this
# path — only this one-time, explicit, developer-triggered setup step does.
LEGACY_VOICES_DIR = Path("/home/devdul/Projects/smart-desk-ai-assistant/voices/piper")


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_venv(force: bool) -> None:
    venv_dir = piper_venv_dir()
    python_path = piper_venv_python()
    if python_path.is_file() and not force:
        print(f"Piper venv already exists at {venv_dir}", file=sys.stderr)
        return
    if venv_dir.exists() and force:
        shutil.rmtree(venv_dir)
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "venv", str(venv_dir)])
    if not python_path.is_file():
        raise RuntimeError(f"venv creation finished but {python_path} does not exist")


def ensure_piper_installed(force: bool) -> None:
    python_path = piper_venv_python()
    check = subprocess.run(
        [str(python_path), "-c", "import piper.http_server"],
        capture_output=True,
    )
    if check.returncode == 0 and not force:
        print(f"piper-tts[http]=={PIPER_TTS_VERSION} already installed", file=sys.stderr)
        return
    run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python_path), "-m", "pip", "install", f"piper-tts[http]=={PIPER_TTS_VERSION}"])


def _try_copy_from_legacy_reference(voice: str, dest_dir: Path) -> bool:
    """Copy a voice's .onnx/.onnx.json from the legacy read-only reference
    location, if both files are present there. Returns False (does nothing)
    if the legacy path isn't present on this machine — that is expected and
    fine; the caller falls back to a normal download."""
    src_model = LEGACY_VOICES_DIR / f"{voice}.onnx"
    src_config = LEGACY_VOICES_DIR / f"{voice}.onnx.json"
    if not (src_model.is_file() and src_config.is_file()):
        return False
    print(
        f"copying verified voice {voice!r} from legacy reference {LEGACY_VOICES_DIR}",
        file=sys.stderr,
    )
    shutil.copyfile(src_model, dest_dir / f"{voice}.onnx")
    shutil.copyfile(src_config, dest_dir / f"{voice}.onnx.json")
    return True


def ensure_voices(force: bool) -> None:
    python_path = piper_venv_python()
    voices_path = voices_dir()
    voices_path.mkdir(parents=True, exist_ok=True)
    for voice in (EN_VOICE, PL_VOICE):
        model_path = voices_path / f"{voice}.onnx"
        if model_path.is_file() and not force:
            print(f"voice {voice!r} already present at {model_path}", file=sys.stderr)
            continue
        if _try_copy_from_legacy_reference(voice, voices_path):
            if not model_path.is_file():
                raise RuntimeError(f"copy finished but {model_path} does not exist")
            continue
        run(
            [
                str(python_path), "-c",
                "import sys; from pathlib import Path; "
                "from piper.download_voices import download_voice; "
                f"download_voice({voice!r}, Path(sys.argv[1]))",
                str(voices_path),
            ]
        )
        if not model_path.is_file():
            raise RuntimeError(f"download finished but {model_path} does not exist")


def ensure_punkt_tab() -> None:
    """Download NLTK's `punkt_tab` sentence-tokenizer data explicitly, here
    only — never silently at NeXa runtime (`nexa.voice_tts` checks for it
    and raises an explicit error instead of downloading)."""
    import nltk

    try:
        nltk.data.find("tokenizers/punkt_tab")
        print("NLTK punkt_tab already present", file=sys.stderr)
    except LookupError:
        print("Downloading NLTK punkt_tab (one-time, explicit)...", file=sys.stderr)
        nltk.download("punkt_tab", quiet=False)


def print_paths() -> None:
    config = PiperHttpConfig()
    print(f"data dir:      {data_dir()}")
    print(f"venv:          {piper_venv_dir()} "
          f"({'exists' if piper_venv_python().is_file() else 'MISSING'})")
    print(f"voices dir:    {voices_dir()}")
    for voice in (EN_VOICE, PL_VOICE):
        path = voices_dir() / f"{voice}.onnx"
        print(f"  {voice}: {'exists' if path.is_file() else 'MISSING'} ({path})")
    print(f"synthesize URL: {config.synthesize_url}")
    try:
        import nltk

        nltk.data.find("tokenizers/punkt_tab")
        print("NLTK punkt_tab: present")
    except LookupError:
        print("NLTK punkt_tab: MISSING")


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

    ensure_venv(args.force)
    ensure_piper_installed(args.force)
    ensure_voices(args.force)
    ensure_punkt_tab()

    print("\nSetup complete.", file=sys.stderr)
    print_paths()
    return 0


if __name__ == "__main__":
    sys.exit(main())
