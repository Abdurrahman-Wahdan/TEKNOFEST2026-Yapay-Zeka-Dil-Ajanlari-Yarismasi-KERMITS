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
