"""Pushing a finished report to the browser waiting for it.

No database, no socket. What is worth testing here is the part that is easy to
get wrong and impossible to see when it is wrong: **the publish crosses a thread
boundary**. Both writers are threads -- the scheduling loop and the router's
manual-run thread -- while the subscribers live on the event loop, so a publish
that mutated the queues directly would be a data race that shows up as a
notification going missing once a month.

The rest is the shape of the contract the client depends on: per-user delivery
(nobody sees anybody else's reports), a bounded queue (a wedged socket must not
grow without limit), and a publish that cannot raise into the tail of a run that
has already stored its report.
"""

import asyncio
import threading
import uuid

import pytest

from api.automations.notifications import QUEUE_SIZE, ReportsHub, report_event

pytestmark = pytest.mark.unit


class _Report:
    """Stands in for an AutomationReport row; needs no ORM to be read from."""

    def __init__(self, title="Sabah altın raporu", status="ok"):
        self.id = uuid.uuid4()
        self.automation_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.title = title
        self.status = status
        self.created_at = None


@pytest.mark.asyncio
async def test_publish_from_another_thread_reaches_the_subscriber():
    """The case the whole module exists for: writer thread -> event loop."""
    hub = ReportsHub()
    user = uuid.uuid4()

    async with hub.subscribe(user) as queue:
        # Exactly how `run_automation` calls it: from a plain thread that knows
        # nothing about the loop the socket is served on.
        thread = threading.Thread(
            target=hub.publish, args=(user, {"type": "report", "n": 1})
        )
        thread.start()
        thread.join()

        message = await asyncio.wait_for(queue.get(), timeout=2)

    assert message == {"type": "report", "n": 1}


@pytest.mark.asyncio
async def test_a_report_goes_only_to_its_own_user():
    """Two users, two sockets, one report."""
    hub = ReportsHub()
    mine, theirs = uuid.uuid4(), uuid.uuid4()

    async with hub.subscribe(mine) as my_queue:
        async with hub.subscribe(theirs) as their_queue:
            hub.publish(mine, {"type": "report"})
            await asyncio.sleep(0)  # let call_soon_threadsafe run

            assert my_queue.qsize() == 1
            assert their_queue.empty()


@pytest.mark.asyncio
async def test_every_socket_of_one_user_is_served():
    """Two tabs open means two queues, and both get the report."""
    hub = ReportsHub()
    user = uuid.uuid4()

    async with hub.subscribe(user) as first:
        async with hub.subscribe(user) as second:
            assert hub.listener_count(user) == 2
            hub.publish(user, {"type": "report"})
            await asyncio.sleep(0)

            assert first.qsize() == 1
            assert second.qsize() == 1


@pytest.mark.asyncio
async def test_unsubscribing_forgets_the_user_entirely():
    """A server up for a month must not hold a key per user who ever visited."""
    hub = ReportsHub()
    user = uuid.uuid4()

    async with hub.subscribe(user):
        assert hub.listener_count(user) == 1
    assert hub.listener_count(user) == 0
    assert user not in hub._subscribers


@pytest.mark.asyncio
async def test_a_wedged_socket_drops_its_oldest_rather_than_growing():
    """The queue is bounded, and the newest report is the one worth keeping."""
    hub = ReportsHub()
    user = uuid.uuid4()

    async with hub.subscribe(user) as queue:
        for n in range(QUEUE_SIZE + 5):
            hub.publish(user, {"type": "report", "n": n})
        await asyncio.sleep(0)

        assert queue.qsize() == QUEUE_SIZE
        # The oldest five were dropped, not the newest five.
        assert queue.get_nowait()["n"] == 5


def test_publishing_with_nobody_listening_is_a_no_op():
    """An automation firing at 03:00 with no browser open must not raise.

    Deliberately not an async test: there is no running loop here at all, which
    is the state the hub is in before anyone has ever connected.
    """
    hub = ReportsHub()
    hub.publish(uuid.uuid4(), {"type": "report"})  # must not raise


def test_the_event_carries_what_the_toast_needs_and_not_the_body():
    """The wire form is deliberately small: a title and an id, never the answer."""
    report = _Report(title="Sabah altın raporu")
    event = report_event(report)

    assert event["type"] == "report"
    assert event["report"]["title"] == "Sabah altın raporu"
    assert event["report"]["id"] == str(report.id)
    assert event["report"]["automation_id"] == str(report.automation_id)
    # A report body is a full agent answer. Pushing it at a browser that is only
    # going to draw a title is a multi-kilobyte frame for nothing.
    assert "body" not in event["report"]
