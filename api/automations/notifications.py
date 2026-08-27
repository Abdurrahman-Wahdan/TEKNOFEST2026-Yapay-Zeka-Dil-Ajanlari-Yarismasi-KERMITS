"""New reports, pushed to the browser that is waiting for them.

The bell used to poll `GET /me/automations/reports/unread-count` on a minute's
timer, and `refetchIntervalInBackground` is false -- so a user reading a table
in a tab that had lost focus learned about a report when they next navigated.
That reads as "notifications only arrive when I refresh", which is what it was.

A report is the opposite of the FX board in every way that matters. The board is
a steady tick that every viewer shares, so `api/rates_stream.py` polls once and
fans the result out. A report is **rare, unpredictable and addressed to exactly
one user** -- minutes of supervisor work landing at a moment nobody can predict.
Polling is the wrong shape for that at any interval: fast enough to feel live is
a request every second or two per tab for an event that happens twice a day.

So this is the same transport as the board and the opposite subscription model:
one queue per connected browser, keyed by user, and a publish that touches only
that user's queues.

**Publishing crosses a thread boundary.** Both writers are threads -- the
automation loop (`loop.py`) and the manual-run thread in the router -- while the
subscribers live on the event loop. `publish` is therefore callable from any
thread and hands the delivery to the loop with `call_soon_threadsafe`; doing the
`set` mutation directly from the writer thread would be a data race against a
browser connecting at the same moment.

**One process.** The queues are in memory, so a report is delivered to sockets
held by *this* process. That is the same bound `RatesHub` has, and it holds for
the same reason: the automation loop already elects a single process to run at
all (see `loop.py`), and `--reload` serves from one. Running the API under
several workers would need a shared broker here, and the loop's advisory lock is
where that decision gets made, not here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import AsyncIterator

logger = logging.getLogger(__name__)

#: What a socket may fall behind by before it starts losing messages. A browser
#: that cannot keep up with a handful of reports is not going to catch up, and
#: an unbounded queue on a wedged connection is a memory leak with a schedule.
QUEUE_SIZE = 16


class ReportsHub:
    """Per-user fan-out of report events to connected browsers."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
        # Captured on first subscribe rather than at import: there is no running
        # loop when this module is imported, and the publishing thread has no
        # way to find the server's loop on its own.
        self._loop: asyncio.AbstractEventLoop | None = None

    @contextlib.asynccontextmanager
    async def subscribe(self, user_id: uuid.UUID) -> AsyncIterator[asyncio.Queue]:
        """Yield a queue receiving this user's report events."""
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.setdefault(user_id, set()).add(queue)
        try:
            yield queue
        finally:
            queues = self._subscribers.get(user_id)
            if queues is not None:
                queues.discard(queue)
                # Drop the empty set as well, so a server that has been up for a
                # month does not hold a key per user who ever opened a tab.
                if not queues:
                    self._subscribers.pop(user_id, None)

    def listener_count(self, user_id: uuid.UUID) -> int:
        """How many sockets this user has open. For logging and for tests."""
        return len(self._subscribers.get(user_id, ()))

    def publish(self, user_id: uuid.UUID, message: dict) -> None:
        """Deliver `message` to this user's sockets. Safe from any thread.

        Never raises. This is called from the tail of a run that has already
        stored its report, and the report -- not the notification -- is the
        durable thing: a delivery that fails must not turn a finished run into a
        logged exception, because the bell's own poll is still there underneath
        and will show it within the minute.
        """
        loop = self._loop
        if loop is None or not self._subscribers.get(user_id):
            # Nobody is listening, or nobody ever has. Not worth a log line: an
            # automation firing at 03:00 with no browser open is the normal case.
            return
        try:
            loop.call_soon_threadsafe(self._deliver, user_id, message)
        except RuntimeError:
            # The loop is closed -- the server is shutting down mid-run.
            logger.debug("report notification dropped, loop closed", exc_info=True)

    def _deliver(self, user_id: uuid.UUID, message: dict) -> None:
        """Put the message on every queue. Runs on the event loop."""
        for queue in list(self._subscribers.get(user_id, ())):
            if queue.full():
                # Drop this socket's oldest rather than blocking the loop. See
                # QUEUE_SIZE: the newest report is the one worth showing.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(message)


#: The one hub. Imported by the runner (which publishes) and by the router
#: (which subscribes).
hub = ReportsHub()


def report_event(report) -> dict:
    """The wire form of a new report.

    Everything the toast needs to draw itself and link to the report, and
    nothing more -- deliberately not the body, which is a full agent answer and
    would push a multi-kilobyte frame at a browser that is only going to render
    a title. The client follows the id when the user opens it.
    """
    return {
        "type": "report",
        "report": {
            "id": str(report.id),
            "automation_id": str(report.automation_id),
            "title": report.title,
            "status": report.status,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        },
    }
