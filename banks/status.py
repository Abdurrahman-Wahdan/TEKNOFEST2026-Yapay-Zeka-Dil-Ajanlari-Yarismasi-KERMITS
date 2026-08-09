"""Which bank capabilities are currently down.

The health checker writes this file; the tools read it before every call. A
bank whose endpoint broke this morning must refuse differently from a bank that
never published a calculator at all — "this bank does not offer that" is a
correct answer to give a user, "I could not reach the bank" is not, and the
agent can only tell them apart if something records the difference.

    from banks.status import outage

    outage("vakif", "profit_share")   # None, or the reason it is down

The file is plain JSON so a human can read it, and deleting it clears every
outage. Writes are atomic, because the checker writes it while the tools read.
"""

import contextlib
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from config.settings import PROJECT_ROOT, settings

from .parse import fold

logger = logging.getLogger(__name__)

DOWN = "down"
OK = "ok"

_LOCK = threading.Lock()
# The parsed file, plus the path and mtime it came from. Re-read only when
# either changes, so the tools do not pay a disk read per question. The path is
# part of the key because the setting can point somewhere else between reads.
_CACHE: dict = {}
_CACHE_KEY: tuple[str, float] | None = None


def path() -> Path:
    """Where the status file lives.

    An empty setting means "use the default beside the project" rather than the
    current directory, so a blank line in .env cannot silently move it.
    """
    configured = (settings.HEALTH_STATUS_FILE or "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "bank_status.json"


def read() -> dict:
    """The whole status file, or {} when there is none.

    A malformed file is treated as empty: a checker that half-wrote it must not
    take the tools down with it.
    """
    global _CACHE, _CACHE_KEY
    target = path()
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
            raise ValueError("status file is not an object")
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable status file %s: %s", target, exc)
        parsed = {}

    with _LOCK:
        _CACHE, _CACHE_KEY = parsed, key
    return parsed


def write(status: dict) -> Path:
    """Replace the status file atomically and return where it was written."""
    global _CACHE_KEY
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target so os.replace stays on one filesystem.
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    with _LOCK:
        _CACHE_KEY = None  # force a re-read on the next look
    logger.debug("Wrote bank status to %s", target)
    return target


_LOCAL = threading.local()


@contextlib.contextmanager
def bypass():
    """Ignore recorded outages inside this block, on this thread.

    The health checker needs it: a capability marked down would otherwise refuse
    the very probe meant to clear it, and nothing would ever recover by itself.
    """
    was = getattr(_LOCAL, "bypass", False)
    _LOCAL.bypass = True
    try:
        yield
    finally:
        _LOCAL.bypass = was


def outage(bank: str, capability: str) -> str | None:
    """Why this bank capability is down, or None when it is fine.

    Unknown means fine. A capability that has never been checked must not look
    broken, or a fresh install would refuse everything.
    """
    if getattr(_LOCAL, "bypass", False):
        return None
    entry = (read().get(bank) or {}).get(capability) or {}
    if entry.get("state") != DOWN:
        return None
    return entry.get("reason") or "the last health check could not reach it"


def product_outage(bank: str, capability: str, *keys: str) -> str | None:
    """Why one product is down, or None.

    One broken product must not disable the other eighteen, so the audit records
    products under their capability. `keys` are the identifiers that could name
    it — a code and a name — because which one is the identity differs by bank.
    All are folded before comparing, so casing and Turkish letters do not decide
    whether a user gets a price.
    """
    if getattr(_LOCAL, "bypass", False):
        return None
    products = ((read().get(bank) or {}).get(capability) or {}).get("products") or {}
    if not products:
        return None
    folded = {fold(str(key)): key for key in keys if key}
    for recorded, record in products.items():
        if fold(recorded) in folded and record.get("state") == DOWN:
            return record.get("reason") or "the last health check could not reach it"
    return None


def entry(state: str, reason: str = "", previous: dict | None = None,
          products: dict | None = None) -> dict:
    """One capability's record, keeping `since` from an unchanged state.

    `since` is when the state last *changed*, not when it was last checked, so a
    long outage reads as one long outage rather than as this morning's news.
    """
    was = (previous or {}).get("state")
    since = (previous or {}).get("since") if was == state else None
    record = {
        "state": state,
        "since": since or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
    }
    # Keep whatever product-level state the caller did not look at. A run
    # checking only the capability must not wipe the products under it.
    keep = (previous or {}).get("products") or {}
    merged = {**keep, **(products or {})}
    if merged:
        record["products"] = merged
    return record


def clear_cache() -> None:
    """Drop the parsed copy. Tests change the file underneath us."""
    global _CACHE, _CACHE_KEY
    with _LOCK:
        _CACHE, _CACHE_KEY = {}, None
