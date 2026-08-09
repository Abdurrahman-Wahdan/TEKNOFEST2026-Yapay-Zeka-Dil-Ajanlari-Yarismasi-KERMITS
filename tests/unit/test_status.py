"""The shared clock, and the one locked writer that merges into the status
file.

No network. These cover the two things reviewed and found lacking: two
clocks with no marker between them, and a read-modify-write race that could
lose a real outage when two checkers overlap.
"""

import threading
from datetime import datetime, timezone

import pytest

from banks import clock, status

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def status_file(tmp_path, monkeypatch):
    target = tmp_path / "bank_status.json"
    monkeypatch.setattr(status.settings, "HEALTH_STATUS_FILE", str(target))
    status.clear_cache()
    yield target
    status.clear_cache()


# ----- the clock -----


def test_stamp_is_utc_iso_with_second_resolution():
    instant = datetime(2026, 8, 9, 13, 51, 26, tzinfo=timezone.utc)
    assert clock.stamp(instant) == "2026-08-09T13:51:26+00:00"


def test_display_is_turkey_local_and_always_marked():
    instant = datetime(2026, 8, 9, 13, 51, 26, tzinfo=timezone.utc)
    assert clock.display(instant) == "2026-08-09 16:51 +03"


def test_stamp_and_display_are_the_same_instant_two_ways():
    """The bug this replaces: two renderings of "now" that looked like
    different times because nothing said which clock either one was."""
    instant = datetime(2026, 8, 9, 13, 51, 26, tzinfo=timezone.utc)
    assert clock.stamp(instant).startswith("2026-08-09T13:51:26")
    assert clock.display(instant).startswith("2026-08-09 16:51")


def test_now_needs_no_argument_and_both_renderings_are_marked():
    assert clock.stamp().endswith("+00:00")
    assert clock.display().endswith("+03")


# ----- status.apply() -----


def test_apply_writes_and_returns_the_change():
    merged, changes = status.apply({"vakif": {"finance": (status.DOWN, "boom", None)}})
    assert merged["vakif"]["finance"]["state"] == status.DOWN
    assert changes == [{"bank": "vakif", "capability": "finance",
                        "from": "unknown", "to": status.DOWN, "reason": "boom"}]
    assert status.outage("vakif", "finance") == "boom"


def test_apply_does_not_announce_a_first_sight_that_is_already_ok():
    _, changes = status.apply({"vakif": {"finance": (status.OK, "", None)}})
    assert changes == []


def test_apply_does_not_report_an_unchanged_state_twice():
    status.apply({"vakif": {"finance": (status.DOWN, "boom", None)}})
    _, changes = status.apply({"vakif": {"finance": (status.DOWN, "boom", None)}})
    assert changes == []


def test_apply_never_clears_a_bank_it_did_not_touch():
    status.apply({"vakif": {"finance": (status.DOWN, "boom", None)}})
    status.apply({"emlak": {"finance": (status.OK, "", None)}})
    assert status.outage("vakif", "finance") == "boom"


def test_apply_carries_products_forward_the_way_entry_does():
    status.apply({"kuveytturk": {"finance": (
        status.OK, "", {"A": (status.DOWN, "broken"), "B": (status.OK, "")}
    )}})
    assert status.product_outage("kuveytturk", "finance", "A") == "broken"
    assert status.product_outage("kuveytturk", "finance", "B") is None

    # A second run that only touches A must not forget B.
    status.apply({"kuveytturk": {"finance": (
        status.OK, "", {"A": (status.OK, "")}
    )}})
    assert status.product_outage("kuveytturk", "finance", "A") is None


def test_apply_serializes_two_writers_that_overlap(monkeypatch):
    """This is the bug as it was found: a health check records an outage
    while an audit is mid-run, and the audit's write -- built from what it
    saw when it *started* -- erases it. status.apply() re-reads the file
    itself, inside a lock, right before merging, so the second writer here
    sees the first one's result no matter how their calls overlap.
    """
    real_read = status.read
    first_call_is_reading = threading.Event()
    let_first_call_finish = threading.Event()
    seen: list[int] = []

    def tracking_read():
        seen.append(1)
        if len(seen) == 1:
            first_call_is_reading.set()
            let_first_call_finish.wait(timeout=5)
        return real_read()

    monkeypatch.setattr(status, "read", tracking_read)

    def writer_a():
        status.apply({"vakif": {"finance": (status.DOWN, "boom", None)}})

    def writer_b():
        assert first_call_is_reading.wait(timeout=5), "writer a never started"
        status.apply({"emlak": {"finance": (status.DOWN, "also boom", None)}})

    thread_a = threading.Thread(target=writer_a)
    thread_b = threading.Thread(target=writer_b)
    thread_a.start()
    thread_b.start()
    # Give writer_b a real chance to reach the lock and block on it before
    # writer_a is allowed to proceed and release it.
    first_call_is_reading.wait(timeout=5)
    threading.Event().wait(0.2)
    let_first_call_finish.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert status.outage("vakif", "finance") == "boom"
    assert status.outage("emlak", "finance") == "also boom"
