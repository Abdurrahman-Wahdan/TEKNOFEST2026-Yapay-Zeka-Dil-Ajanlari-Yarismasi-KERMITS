"""The chat transport must stay alive while the agent is silent."""

import time

import pytest

from api.routers.chat import _with_heartbeats


def test_silent_producer_emits_heartbeat_before_result():
    def producer():
        time.sleep(0.03)
        yield "answer"

    stream = _with_heartbeats(producer, interval=0.005)

    assert next(stream) is None
    assert list(stream)[-1] == "answer"


def test_worker_exception_reaches_response_iterator():
    def producer():
        raise RuntimeError("agent failed")
        yield  # pragma: no cover - makes this function an iterator

    with pytest.raises(RuntimeError, match="agent failed"):
        list(_with_heartbeats(producer, interval=0.005))
