"""Where fetched bytes and the crawl's memory live.

Two decisions, both of them reactions to what the old crawler did.

**Blobs are named by the hash of their content, not by their URL path.** Mapping
a URL onto a file path is what destroyed seven homepages (`index.md` and
`INDEX.md` are one file on a case-insensitive filesystem) and collapsed 44 FAQ
pages onto one name. A sha256 cannot collide, and the same PDF linked from forty
pages stores once.

**Raw bytes are kept, PDFs included.** The old crawler extracted text and threw
the file away, so a scanned PDF could not be OCR'd later without re-fetching the
whole site, and re-running a fixed cleaning rule meant another 8,500 requests.
Keeping the bytes makes `--no-fetch` re-derive the entire corpus offline.

    from corpus import store

    content_hash, blob = store.put(body, "application/pdf")
    store.get(blob)
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from config.settings import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

# Enough of the content type to name the file. Anything else keeps .bin: the
# extension is a convenience for humans reading the store, never a decision.
_EXTENSIONS = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/xml": ".xml",
    "text/xml": ".xml",
}

_LOCK = threading.Lock()
# The parsed manifest, plus the path and mtime it came from. Re-read only when
# either changes -- the same idiom as banks/status.py, for the same reason.
_CACHE: dict = {}
_CACHE_KEY: tuple[str, float] | None = None


def root() -> Path:
    """The corpus directory.

    An empty setting means "beside the project" rather than the current
    directory, so a blank line in .env cannot scatter a corpus into wherever the
    cron job happened to start.
    """
    configured = (settings.CORPUS_ROOT or "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "corpus_data"


def raw_dir() -> Path:
    return root() / "raw"


def manifest_path() -> Path:
    return root() / "manifest.json"


def extension_for(content_type: str) -> str:
    """The file extension to give a blob of this content type."""
    return _EXTENSIONS.get((content_type or "").split(";")[0].strip().lower(), ".bin")


def blob_name(content_hash: str, content_type: str) -> str:
    """The store-relative path for a blob, fanned out two levels.

    Two hex characters per level keeps any one directory to a few hundred
    entries; a flat directory of tens of thousands of files is slow to list and
    unpleasant to inspect by hand.
    """
    return f"{content_hash[:2]}/{content_hash[2:4]}/{content_hash}{extension_for(content_type)}"


def put(data: bytes, content_type: str = "") -> tuple[str, str]:
    """Store bytes and return `(content_hash, blob)`.

    Writing the same bytes twice is a no-op, which is what makes a daily run
    idempotent: an unchanged page costs a hash and nothing else.
    """
    content_hash = hashlib.sha256(data).hexdigest()
    blob = blob_name(content_hash, content_type)
    target = raw_dir() / blob
    if target.exists():
        return content_hash, blob

    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return content_hash, blob


def get(blob: str) -> bytes:
    """Read stored bytes back.

    Raises:
        FileNotFoundError: if the blob is missing. A manifest entry pointing at
            nothing is a corrupt store, not something to paper over with b"".
    """
    return (raw_dir() / blob).read_bytes()


def has(blob: str) -> bool:
    return (raw_dir() / blob).exists()


def read_manifest() -> dict:
    """The whole manifest, or {} when there is none.

    A malformed file is treated as empty and logged. A half-written manifest
    must not take the next run down with it -- the worst case is one full crawl.
    """
    global _CACHE, _CACHE_KEY
    target = manifest_path()
    try:
        key = (str(target), target.stat().st_mtime)
    except OSError:
        with _LOCK:
            _CACHE, _CACHE_KEY = {}, None
        return {}

    with _LOCK:
        if _CACHE_KEY == key:
            return _CACHE
    try:
        parsed = json.loads(target.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            raise ValueError("manifest is not an object")
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable manifest %s: %s", target, exc)
        parsed = {}

    with _LOCK:
        _CACHE, _CACHE_KEY = parsed, key
    return parsed


def write_manifest(manifest: dict) -> Path:
    """Replace the manifest atomically and return where it was written."""
    global _CACHE_KEY
    target = manifest_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target so os.replace stays on one filesystem.
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    with _LOCK:
        _CACHE_KEY = None  # force a re-read on the next look
    logger.debug("Wrote manifest to %s", target)
    return target


def write_text(relative: str, text: str) -> Path:
    """Write a UTF-8 text file under the corpus root, atomically.

    Used for the clean markdown tree and `documents.jsonl`. Atomic because a
    reader may be indexing the artifact while a run rewrites it, and half a
    JSONL file parses as a shorter one rather than as an error.
    """
    target = root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def clear_cache() -> None:
    """Drop the parsed manifest. Tests change the file underneath us."""
    global _CACHE, _CACHE_KEY
    with _LOCK:
        _CACHE, _CACHE_KEY = {}, None
