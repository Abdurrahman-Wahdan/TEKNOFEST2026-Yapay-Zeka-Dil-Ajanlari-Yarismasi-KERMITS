"""The one clock every part of banks/ reads from.

The status file, the daily report, and later the UI or the agent all need to
agree on what "now" was and how to say it — otherwise an admin correlating an
alert with the status file sees a gap that isn't real, just two different
clocks with no marker saying which is which.

Storage stays UTC everywhere, unaffected by where the server runs.
`display()` is the one place a Turkey-local rendering happens, so a report,
a UI, or an agent all say the same clock the same way.
"""

from datetime import datetime, timedelta, timezone

TR = timezone(timedelta(hours=3), name="+03")  # Turkey has had no DST since 2016


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime | None = None) -> str:
    """Machine-readable. Always UTC. What the status file stores."""
    return (dt or now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def display(dt: datetime | None = None) -> str:
    """Human-readable. Always Turkey time, always marked, never bare."""
    return (dt or now()).astimezone(TR).strftime("%Y-%m-%d %H:%M +03")


def stamp_tr(dt: datetime | None = None) -> str:
    """Machine-readable *and* Turkey time. What a language model is shown.

    The third rendering, because a model needs both properties the other two
    split. `stamp()` is UTC, so a model handed a live result's `retrieved_at` and
    asked to report it says "08:00" while the reader's own clock says 11:00 --
    the model is not wrong, it is quoting exactly what it was given. `display()`
    is Turkey time but drops the seconds and prints `+03` in a shape nothing can
    parse, so it cannot carry a timestamp through a JSON envelope.

    This keeps ISO-8601 with a real `+03:00` offset: unambiguous to anything that
    parses it, and correct for a Turkish reader when the model repeats it
    verbatim -- which is what models do with timestamps.

    Storage stays UTC. This is for the agent-facing envelopes in
    `agents/shared/results.py` and `agents/shared/web_research.py` and nothing
    else; a status file or a build report still uses `stamp()`.
    """
    return (dt or now()).astimezone(TR).isoformat(timespec="seconds")
