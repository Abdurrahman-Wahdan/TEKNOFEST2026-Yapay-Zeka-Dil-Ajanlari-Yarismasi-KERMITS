"""Reading the *live* comparison page with the model.

The sibling of `api/table_overviews.py`, and deliberately not the same storage.

**Why not the database.** That module's cache exists because the offline pool is
a function of a file: the same table tomorrow is the same table, so a row with no
expiry is the honest thing to keep. Nothing on `/compare` is like that. The FX
board is a different board every few minutes *by design*, and a finance run
belongs to one user's amount, term and bank selection. A row per FX tick is
landfill with no second reader, and a shared `(table_id, locale)` row would have
every visitor clobbering the last one's answer.

**So the key is the page itself.** `digest()` hashes the outline the browser
sent, which is exactly what the agent reads -- so two readers looking at the same
board share one generation, and a five-minute refresh over a board that has not
moved costs nothing. The client never hashes anything: it POSTs the outline, gets
the digest back, and polls the GET with it.

**Bounded, in memory.** `_MAX_ENTRIES` of the most recent, evicted oldest-first.
An overview keyed on a digest can never go stale -- the digest *is* the content --
so there is no TTL here, only a ceiling on how much is kept.

Everything else is borrowed from `table_overviews` rather than rewritten: the
same agent, the same background thread, and the same process-wide semaphore. That
last one matters and is not a tidiness point -- the cap exists so one reader
cannot queue six vision calls on a host that is also serving the chat, and a
second, separate cap would let this surface do exactly that.
"""

import hashlib
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone

from agents.table_overview import TableOverview as OverviewResult, generate_table_overview

from . import table_overviews as pool

logger = logging.getLogger(__name__)

#: How many generated overviews to keep. Each is four short strings; the cap is
#: here so a long-running process cannot grow one entry per FX tick forever.
_MAX_ENTRIES = 256

_cache: "OrderedDict[str, tuple[OverviewResult, datetime]]" = OrderedDict()
#: Keys whose generation is running in a background thread.
_running: set[str] = set()
_guard = threading.Lock()


def digest(page_text: str) -> str:
    """The identity of what is on screen.

    Over the outline rather than over the comparison parameters, because the
    outline is what the agent reads: two runs with different inputs that
    happened to produce the same board *are* the same board to summarise, and a
    board that moved is a new one even though nobody touched the form.
    """
    return hashlib.sha256(page_text.encode("utf-8")).hexdigest()


def _key(page_digest: str, locale: str) -> str:
    """Content, language and model. All three change what comes back.

    `pool.model_name()` carries the prompt version too, so a reworded prompt
    stops serving what the previous wording asked for -- the digest cannot
    notice a change that happened in the prompt file.
    """
    return f"{page_digest}:{locale}:{pool.model_name()}"


def cached(page_digest: str, locale: str) -> tuple[OverviewResult, datetime] | None:
    """The overview for this exact page, if one has been written."""
    key = _key(page_digest, pool.normalise_locale(locale))
    with _guard:
        hit = _cache.get(key)
        if hit is not None:
            # Most-recently-read goes to the end, so the board a tab is actually
            # watching is not the one evicted by a burst of one-off runs.
            _cache.move_to_end(key)
        return hit


def running(page_digest: str, locale: str) -> bool:
    """Whether a generation for this page is under way."""
    with _guard:
        return _key(page_digest, pool.normalise_locale(locale)) in _running


def _store(key: str, result: OverviewResult) -> None:
    with _guard:
        _cache[key] = (result, datetime.now(timezone.utc))
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def start(*, page_text: str, page_digest: str, locale: str) -> bool:
    """Generate in the background. False when one is already running.

    Returns immediately, like the pool's own POST and for the same reason: a
    generation takes 70-120 seconds and the dev server's `/api` proxy cuts a
    socket at 30. The client polls the GET.
    """
    locale = pool.normalise_locale(locale)
    key = _key(page_digest, locale)
    with _guard:
        if key in _running:
            return False
        _running.add(key)

    def run() -> None:
        # Waits with no expiry, sharing the pool's slots: the caller is polling
        # on this generation's actual state rather than on a clock, so a queued
        # overview that starts in four minutes still lands on the page that
        # asked for it.
        with pool.slot():
            try:
                _store(key, generate_table_overview(page_text, locale=locale))
            except Exception:
                # Logged and dropped: there is no caller left to raise to. The
                # client polls the GET, which answers `missing` once this key
                # stops running, and that is what the card draws as a failure.
                logger.exception("Live overview generation failed (%s)", key[:16])
            finally:
                with _guard:
                    _running.discard(key)

    threading.Thread(target=run, name=f"live-overview:{key[:16]}", daemon=True).start()
    return True
