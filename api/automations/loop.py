"""The background thread that polls for due automations, and the lock on it.

One process must run this and no others. Two would each claim a share of the due
rows -- `SKIP LOCKED` keeps them from claiming the *same* row, so there would be
no duplicate report, but there would be two competing supervisors fanning out to
the same ten banks, which is the thing `BANK_COMPARE_WORKERS` exists to prevent.
Under `--reload`, and under any uvicorn started with more than one worker, that
is the default situation rather than an edge case.

So the loop takes a **session-level Postgres advisory lock** and holds it on a
dedicated connection for as long as it runs. Whoever gets it polls; everyone else
logs why they are not polling and returns. The lock is released when that
connection closes, which includes the process dying -- so a crashed API does not
leave the lock held and automations stopped.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import text

from config.settings import settings

from ..db.session import engine
from . import runner

logger = logging.getLogger(__name__)

#: The advisory-lock key. Arbitrary but fixed, and namespaced by being large and
#: specific enough not to collide with another application on a shared database.
LOCK_KEY = 2608_2026

_thread: threading.Thread | None = None
_stop = threading.Event()


def _poll(connection) -> None:
    """Poll until asked to stop, holding `connection` for the whole time."""
    logger.info(
        "automation loop started, polling every %ss", settings.AUTOMATIONS_POLL_SECONDS
    )
    while not _stop.is_set():
        try:
            reports = runner.tick()
            if reports:
                logger.info("automation loop wrote %d report(s)", len(reports))
        except Exception:
            # A poll that raises must not end the loop. The likeliest cause is
            # the database blinking, and an API that stops running automations
            # for the rest of its life over one dropped connection is a worse
            # failure than a logged error and a retry in thirty seconds.
            logger.exception("automation poll failed")
        # `wait`, not `sleep`: shutdown returns immediately instead of after up
        # to a full poll interval.
        _stop.wait(settings.AUTOMATIONS_POLL_SECONDS)
    connection.close()
    logger.info("automation loop stopped")


def _run() -> None:
    """Take the lock, or explain why this process is not the one polling."""
    try:
        connection = engine.connect()
    except Exception:
        logger.exception("automation loop could not reach the database")
        return
    try:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        ).scalar()
    except Exception:
        logger.exception("automation loop could not take its lock")
        connection.close()
        return
    if not acquired:
        # Not an error. It is what every process except one is supposed to see.
        logger.info(
            "automation loop not started: another process holds lock %s", LOCK_KEY
        )
        connection.close()
        return
    _poll(connection)


def start() -> None:
    """Start the loop, once, if this deployment wants one.

    Called from a FastAPI startup hook. Daemon, so it never holds up shutdown:
    the work it does is idempotent from the next poll's point of view, and a run
    interrupted by a restart is one missing report rather than a corrupt one.

    Known and accepted in development: `uvicorn --reload` restarts this on every
    code edit, abandoning a run in flight. A run is not resumable anyway -- the
    supervisor's thread keeps its state, but the report was never written -- so
    the honest outcome is a missing report, logged here rather than papered over.
    """
    global _thread
    if not settings.AUTOMATIONS_ENABLED:
        logger.info("automation loop disabled by AUTOMATIONS_ENABLED")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="tf26-automations", daemon=True)
    _thread.start()


def stop() -> None:
    """Ask the loop to finish its current poll and exit."""
    _stop.set()
