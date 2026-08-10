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


def blob_file(blob: str) -> Path:
    """The blob's path on disk.

    Needed because poppler's tools take a filename, not a stream: `pdftoppm`
    fed a PDF on stdin produces empty output *and* empty stderr, which is a
    failure mode worth never discovering at 3am.
    """
    return raw_dir() / blob


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


def pdf_doc_dir() -> Path:
    return root() / "pdf_doc"


def read_pdf_doc(content_hash: str) -> dict | None:
    """A PDF's cached OCR result, or None if it has not been read yet.

    Keyed on the hash of the PDF's bytes, so an unchanged file is OCR'd once,
    ever. This is what makes the OCR pass resumable -- a killed run resumes from
    where it stopped rather than re-reading everything through the vision model.
    """
    path = pdf_doc_dir() / content_hash[:2] / f"{content_hash}.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def write_pdf_doc(content_hash: str, record: dict) -> None:
    """Cache one PDF's OCR result atomically, keyed on its content hash.

    `record` holds the decision (accepted/label/reason) and, when accepted, the
    built document -- so a resume skips both the classifier and the OCR, not just
    the OCR.
    """
    target = pdf_doc_dir() / content_hash[:2] / f"{content_hash}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def count_pdf_docs() -> int:
    """How many PDFs have a cached result -- the OCR pass's visible progress."""
    directory = pdf_doc_dir()
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.rglob("*.json"))


def collect_garbage(manifest: dict, keep_suffixes: tuple[str, ...] = (".pdf",)) -> tuple[int, int]:
    """Delete stored blobs the manifest no longer points at.

    Necessary because page bytes churn on every single run while their meaning
    does not. Measured on two consecutive Emlak runs: all 60 pages re-stored
    under new hashes, differing in four lines out of 2,855 -- an FX rate
    timestamp and a rotating F5 WAF token. Extrapolated across ~7,100 documents
    that is roughly a gigabyte a day, so without this the store grows without
    bound and none of the growth is information.

    PDFs are kept even when orphaned, and the measurement is why: they answered
    304 on the second run, so they do not churn, and an orphaned PDF is usually a
    document the bank has withdrawn -- the only remaining copy of what a fee
    schedule said before it changed.

    Returns:
        `(files_deleted, bytes_freed)`, for the run report.
    """
    live = {entry.get("blob") for entry in manifest.values() if isinstance(entry, dict)}
    deleted = freed = 0
    root_dir = raw_dir()
    if not root_dir.exists():
        return 0, 0

    for path in root_dir.rglob("*"):
        if not path.is_file() or path.suffix in keep_suffixes:
            continue
        relative = str(path.relative_to(root_dir))
        if relative in live:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:  # noqa: PERF203 - a file vanishing under us is fine
            continue
        deleted += 1
        freed += size

    if deleted:
        logger.info("Collected %d orphaned blob(s), freeing %.1f MB",
                    deleted, freed / 1_000_000)
    return deleted, freed


def clear_cache() -> None:
    """Drop the parsed manifest. Tests change the file underneath us."""
    global _CACHE, _CACHE_KEY
    with _LOCK:
        _CACHE, _CACHE_KEY = {}, None
