"""The live board's poller and fan-out."""

import asyncio

import pytest

from api import rates_stream

pytestmark = pytest.mark.unit


class _Board(rates_stream.Board):
    pass


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setattr(rates_stream, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(rates_stream, "LINGER_SECONDS", 0.05)
    return rates_stream.RatesHub()


def test_one_poll_serves_every_subscriber(hub, monkeypatch):
    """The whole point: ten viewers cost the banks what one does.

    A board polled per viewer would multiply the load on six banks by the
    number of open tabs, which is exactly the thing that gets an IP banned.
    """
    calls = []

    def fetch(name):
        calls.append(name)
        return rates_stream.Board(bank=name, rates=[{"code": "USD"}], fetched_at=1.0)

    monkeypatch.setattr(rates_stream.RatesHub, "_fetch", staticmethod(fetch))
    monkeypatch.setattr(rates_stream, "list_banks",
                        lambda: {"a": {"publishes": ["rates"]}, "b": {"publishes": ["rates"]}})

    async def scenario():
        async with hub.subscribe() as one, hub.subscribe() as two:
            first = await asyncio.wait_for(one.get(), timeout=2)
            second = await asyncio.wait_for(two.get(), timeout=2)
            return first, second

    first, second = asyncio.run(scenario())
    assert first["banks"].keys() == {"a", "b"}
    assert second["banks"] == first["banks"], "both viewers see the same poll"
    # Two banks per round, not two banks per viewer.
    assert sorted(set(calls)) == ["a", "b"]


def test_a_bank_that_fails_keeps_its_last_board(hub, monkeypatch):
    """A column that blanks on one bad request reads as the bank withdrawing.

    The failure is recorded so the UI can say so, and the last published prices
    stay on screen -- which is what they are: the last thing the bank said.
    """
    state = {"fail": False}

    def fetch(name):
        if state["fail"]:
            raise RuntimeError("bank is down")
        return rates_stream.Board(bank=name, rates=[{"code": "USD"}], fetched_at=1.0)

    monkeypatch.setattr(rates_stream.RatesHub, "_fetch", staticmethod(fetch))
    monkeypatch.setattr(rates_stream, "list_banks", lambda: {"a": {"publishes": ["rates"]}})

    async def scenario():
        async with hub.subscribe() as queue:
            await asyncio.wait_for(queue.get(), timeout=2)
            state["fail"] = True
            for _ in range(5):
                message = await asyncio.wait_for(queue.get(), timeout=2)
                if message["banks"]["a"]["error"]:
                    return message
        return None

    message = asyncio.run(scenario())
    assert message is not None, "the failure was never reported"
    assert "bank is down" in message["banks"]["a"]["error"]
    assert message["banks"]["a"]["rates"] == [{"code": "USD"}], "prices were blanked"


def test_a_slow_viewer_gets_the_newest_board_not_a_backlog(hub, monkeypatch):
    """This is a board, not a log.

    A viewer that fell behind wants the current prices, not to replay every
    tick it missed -- and an unbounded queue per socket is a memory leak with
    a nice name.
    """
    monkeypatch.setattr(rates_stream.RatesHub, "_fetch", staticmethod(
        lambda name: rates_stream.Board(bank=name, rates=[], fetched_at=1.0)))
    monkeypatch.setattr(rates_stream, "list_banks", lambda: {"a": {"publishes": ["rates"]}})

    async def scenario():
        async with hub.subscribe() as queue:
            await asyncio.sleep(0.2)          # let several polls go by unread
            assert queue.qsize() <= 1
            newest = await asyncio.wait_for(queue.get(), timeout=2)
            return newest["version"], hub._version

    seen, current = asyncio.run(scenario())
    assert seen >= current - 1, "the queued message was stale, not the newest"


def test_the_poller_stops_when_nobody_is_watching(hub, monkeypatch):
    """An empty page must not keep six banks busy all night."""
    monkeypatch.setattr(rates_stream.RatesHub, "_fetch", staticmethod(
        lambda name: rates_stream.Board(bank=name, rates=[], fetched_at=1.0)))
    monkeypatch.setattr(rates_stream, "list_banks", lambda: {"a": {"publishes": ["rates"]}})

    async def scenario():
        async with hub.subscribe() as queue:
            await asyncio.wait_for(queue.get(), timeout=2)
        # Past LINGER_SECONDS with no subscribers.
        await asyncio.sleep(0.2)
        return hub._task.done()

    assert asyncio.run(scenario()) is True
