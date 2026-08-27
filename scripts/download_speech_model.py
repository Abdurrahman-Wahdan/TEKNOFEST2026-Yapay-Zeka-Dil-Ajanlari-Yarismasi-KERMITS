#!/usr/bin/env python
"""Fetch the Turkish speech checkpoint into the project's own model directory.

    python scripts/download_speech_model.py

The sibling of however `TF26_data/models/whisper-large-v3-mlx-4bit` got there,
and here for the same reason: **serving a request must never be able to start a
multi-gigabyte download.** Left to `from_pretrained`, the first person to press
the speaker on a fresh machine would wait several minutes inside an HTTP request
with nothing on screen to explain it, and the bytes would land in `~/.cache`
where nobody looking at this project would find them.

Downloading it here instead makes the checkpoint an operator's concern: it sits
beside the Whisper one, `du -sh TF26_data/models` accounts for it, and deleting
it is deleting a directory rather than hunting through a cache.

Idempotent -- an existing, complete checkpoint is verified and left alone, so
this is safe to re-run and safe to put in a setup script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import PROJECT_ROOT, settings  # noqa: E402


def target_path() -> Path:
    configured = Path(settings.SPEECH_MODEL_PATH).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the directory already looks complete.",
    )
    args = parser.parse_args(argv)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(
            "huggingface_hub is not installed. Run `pip install -r requirements.txt`.",
            file=sys.stderr,
        )
        return 1

    destination = target_path()
    if destination.is_dir() and not args.force:
        # `config.json` is the cheap proof that a directory is a checkpoint and
        # not the empty shell an interrupted download leaves behind.
        if (destination / "config.json").is_file():
            print(f"Already present: {destination}")
            print("Re-run with --force to download it again.")
            return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {settings.SPEECH_MODEL_ID} → {destination}")
    print("~5 GB on a cold cache; this takes a while.")
    snapshot_download(
        repo_id=settings.SPEECH_MODEL_ID,
        local_dir=str(destination),
        # Real files rather than symlinks into a shared cache. The point of a
        # project-local checkpoint is that it is self-contained: a symlink farm
        # pointing at ~/.cache is the arrangement this exists to avoid.
        local_dir_use_symlinks=False,
    )

    total = sum(f.stat().st_size for f in destination.rglob("*") if f.is_file())
    print(f"Done: {destination} ({total / 1_000_000_000:.2f} GB)")
    print()
    print("Set SPEECH_MODEL_PATH in .env if you moved it, then restart the API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
