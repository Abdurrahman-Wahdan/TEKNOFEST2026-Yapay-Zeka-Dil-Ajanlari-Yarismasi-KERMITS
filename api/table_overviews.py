"""Reading one comparison table with the model, once.

The agent itself is in `agents/table_overview`; this is the layer between it and
HTTP: what the model is shown, when it is asked at all, and where the answer is
kept.

**Asked once, not once per visit.** The pool is offline (`data/_tables/`), so an
overview of a table is a function of that table. Two things could make a cached
row wrong, and both are keys rather than a TTL: the table's own content
(`source_hash`) and the model that wrote it (`model`). A TTL would only make the
answer expire on a timer while the input had not moved -- paying for a vision
call to get the same paragraph back.

**One call in flight per key.** Ten tabs opening the same table on a cold cache
would otherwise be ten screenshots and ten model calls racing to write the same
row. The lock is the same idea as `api/cache.py`'s single-flight, kept separate
because that cache is a TTL memoiser in front of the banks and this is a
database row with no expiry.

**The model call does not happen inside the request.** A real generation takes
70-120 seconds, and holding an HTTP connection open that long fails for
reasons that have nothing to do with the model: the dev server proxies `/api`
and cuts the socket at 30s, and any gateway in front of this in production
would have an opinion of its own. So the POST starts the work and returns 202,
and the client polls the GET it already calls on arrival. The cache is what
makes that honest -- the poll is reading the same row every later visitor
reads, not a job queue bolted on the side.
"""

import hashlib
import json
import logging
import threading
from collections import defaultdict
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.table_overview import (
    PROMPT_VERSION,
    TableOverview as OverviewResult,
    generate_table_overview,
)
from config.settings import settings

from .db.models import TableOverview
from .db.session import SessionLocal

logger = logging.getLogger(__name__)

LOCALES = ("tr", "en")

_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()
#: Keys whose generation is already running in a background thread.
_running: set[str] = set()
#: How many may be *at the model* at once. The per-key lock above stops two
#: readers of one table racing; this stops one reader who opens six tables
#: queueing six vision calls on a host that is also serving the chat.
_slots = threading.BoundedSemaphore(settings.TABLE_OVERVIEW_CONCURRENCY)


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks[key]


@contextmanager
def slot():
    """Hold one of the process-wide generation slots for the duration.

    Public because `api/live_overviews.py` generates through the same model on
    the same host and has to queue behind the same cap. That is the point of the
    semaphore, not a tidiness detail: a second, separate cap would let a reader
    on `/compare` queue vision calls beside six open pool tables, which is
    exactly the pile-up this was measured to prevent.
    """
    _slots.acquire()
    try:
        yield
    finally:
        _slots.release()


def normalise_locale(locale: str | None) -> str:
    """Anything that is not a language we write overviews in becomes Turkish."""
    return locale if locale in LOCALES else "tr"


def source_hash(table: dict) -> str:
    """A digest of the table the page is showing.

    Not what the agent reads — that is the browser's outline, which no server
    can reproduce — but what *decides* what the agent reads: the page renders
    from this payload, so a change here is a change on screen. Over the
    serialised table rather than its id, because the id survives the producer
    rewriting every figure underneath it, which is precisely the case a cache
    has to notice.
    """
    payload = json.dumps(table, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def model_name() -> str:
    """What wrote this overview, for cache invalidation.

    The model *and* the prompt version, because both change what comes back and
    only one of them is visible in the table hash. Rewording the prompt without
    this would leave every cached row saying what the previous wording asked
    for, with nothing in the key able to notice.
    """
    return f"{settings.CHAT_MODEL}@v{PROMPT_VERSION}"


def cached(session: Session, table_id: str, locale: str, digest: str) -> TableOverview | None:
    """The stored overview, if it was written from this table by this model."""
    row = session.execute(
        select(TableOverview).where(
            TableOverview.table_id == table_id,
            TableOverview.locale == locale,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.source_hash != digest or row.model != model_name():
        # Stale rather than missing, and worth saying so: a row that no longer
        # matches its table means the producer reran, and the next request pays
        # for a regeneration it did not expect to.
        logger.info(
            "Overview for %s (%s) is stale: table or model changed.", table_id, locale
        )
        return None
    return row


def store(
    session: Session,
    *,
    table_id: str,
    locale: str,
    digest: str,
    result: OverviewResult,
) -> TableOverview:
    """Write the overview, replacing whatever was there for this table+language."""
    row = session.execute(
        select(TableOverview).where(
            TableOverview.table_id == table_id,
            TableOverview.locale == locale,
        )
    ).scalar_one_or_none()
    if row is None:
        row = TableOverview(table_id=table_id, locale=locale)
        session.add(row)
    row.source_hash = digest
    row.model = model_name()
    row.body = result.model_dump(mode="json")
    session.commit()
    session.refresh(row)
    return row


def generate(
    session: Session,
    *,
    table: dict,
    table_id: str,
    locale: str,
    page_text: str,
) -> TableOverview:
    """Return the cached overview, or write one by asking the model.

    The cache is checked twice on purpose: once before taking the lock, and once
    after. The second check is what makes the nine tabs waiting behind the first
    one cheap -- they wake up to a row that is already there rather than each
    calling the model in turn.
    """
    locale = normalise_locale(locale)
    digest = source_hash(table)

    hit = cached(session, table_id, locale, digest)
    if hit is not None:
        return hit

    with _lock(f"{table_id}:{locale}"):
        hit = cached(session, table_id, locale, digest)
        if hit is not None:
            return hit

        result = generate_table_overview(page_text, locale=locale)
        return store(
            session,
            table_id=table_id,
            locale=locale,
            digest=digest,
            result=result,
        )


def running(table_id: str, locale: str) -> bool:
    """Whether a background generation for this table+language is under way."""
    with _locks_guard:
        return f"{table_id}:{normalise_locale(locale)}" in _running


def start(
    *,
    table: dict,
    table_id: str,
    locale: str,
    page_text: str,
) -> bool:
    """Generate in the background. False when one is already running.

    Its own session, not the request's: the request returns 202 immediately and
    a session that outlives its request is a connection nobody closes.
    """
    locale = normalise_locale(locale)
    key = f"{table_id}:{locale}"
    with _locks_guard:
        if key in _running:
            return False
        _running.add(key)

    def run() -> None:
        # Waits, with no expiry. The card polls on this generation's actual
        # state rather than on a clock, so a queued overview that starts in
        # four minutes still lands on the page that asked for it.
        with slot():
            session = SessionLocal()
            try:
                generate(
                    session,
                    table=table,
                    table_id=table_id,
                    locale=locale,
                    page_text=page_text,
                )
            except Exception:
                # Logged and dropped: there is no caller left to raise to. The
                # client is polling the GET, which keeps answering 404 until the
                # row exists, and gives up on its own deadline.
                logger.exception("Overview generation failed for %s (%s)", table_id, locale)
            finally:
                session.close()
                with _locks_guard:
                    _running.discard(key)

    threading.Thread(target=run, name=f"overview:{key}", daemon=True).start()
    return True
