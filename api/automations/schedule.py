"""When an automation runs next.

Pure arithmetic. No database, no clock of its own -- `now` is always an
argument, which is what makes "does 09:00 on a Friday roll over to Monday"
something a test can ask rather than something you wait a weekend to find out.

**The schedule is `(hour, minute, weekdays)` and nothing more.** Not a cron
expression. A model creates these from a sentence, and a wrong cron string fails
in the worst way available: silently, by never firing, with nothing on screen to
show it. Three integers fail visibly -- the list renders "Her gün 09:00" and the
user can see it is not what they asked for. The cost is that only "at this time,
on these days" is expressible; every automation this feature was asked for is one
of those.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

#: The wall clock an automation's `hour`/`minute` are in.
#:
#: A named zone rather than the fixed `timedelta(hours=3)` that
#: `api/compare_tables_pool.py` uses. They agree today -- Turkey has been on
#: permanent +03 since 2016 -- but that is a current fact about the country, not
#: a property of this code, and the named zone survives it changing.
TZ = ZoneInfo("Europe/Istanbul")

#: `datetime.weekday()`: 0 is Monday, 6 is Sunday.
WEEKDAYS = range(7)

DAILY: list[int] = []


def valid_weekdays(values) -> list[int]:
    """A weekday list, cleaned. Anything unusable becomes daily.

    Deduplicated and sorted so two ways of saying "Monday and Friday" store
    identically, which is what lets the UI compare a schedule for equality.

    Out-of-range and non-integer entries are **dropped rather than rejected**.
    This is fed by a language model, and the alternative to dropping a stray `7`
    is refusing the whole automation -- which loses the six days it got right.
    An empty result is daily, which is also what the model omitting the field
    most likely meant.
    """
    if not isinstance(values, (list, tuple, set)):
        return DAILY
    cleaned = set()
    for value in values:
        # bool is an int subclass and `True` would silently become Tuesday.
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value in WEEKDAYS:
            cleaned.add(value)
    return sorted(cleaned)


def next_run(
    after: datetime, hour: int, minute: int, weekdays: list[int] | None = None
) -> datetime:
    """The first moment at or after `after` that this schedule fires, in UTC.

    Strictly after: `after` landing exactly on the boundary moves to the *next*
    occurrence. Without that the runner, which advances `next_run_at` using the
    same `now` it claimed with, would rewrite the row with the minute it had just
    run and fire it again on the following poll -- forever.

    An empty (or absent) `weekdays` means every day.

    Computed in `TZ` and converted at the end, rather than adding 24 hours to a
    UTC instant. Those differ whenever the offset changes between the two days:
    "every morning at nine" has to stay nine in the morning, not drift to eight.

    A naive `after` is read as UTC. Everything in this app stores aware
    timestamps (`db/base.py::utcnow`), so this is for a caller in a test, not a
    real code path -- but guessing UTC beats raising `TypeError` from inside
    `astimezone`, which says nothing about what went wrong.
    """
    days = valid_weekdays(weekdays)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    local = after.astimezone(TZ)
    at = time(hour=hour, minute=minute)

    # 8 and not 7: starting from today, a weekly schedule whose only day is
    # today-but-earlier needs the *next* today, which is the eighth candidate.
    for offset in range(8):
        candidate_date = (local + timedelta(days=offset)).date()
        # `combine` with tzinfo rather than `replace` on the shifted datetime:
        # replace() keeps the offset that was correct for the old time, which is
        # wrong on a day the offset changed.
        candidate = datetime.combine(candidate_date, at, tzinfo=TZ)
        if candidate <= local:
            continue
        if days and candidate.weekday() not in days:
            continue
        return candidate.astimezone(timezone.utc)
    # Unreachable: eight consecutive days always contain every weekday.
    raise AssertionError(f"no run found for {hour}:{minute} on {days}")


def describe(hour: int, minute: int, weekdays: list[int] | None = None) -> str:
    """The schedule as one Turkish line, for a log or an API field.

    The UI has its own formatter (`UI/src/lib/automations.ts`) because it has the
    translation catalogue; this one exists so a server log line is readable
    without cross-referencing three integers.
    """
    names = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")
    days = valid_weekdays(weekdays)
    when = f"{hour:02d}:{minute:02d}"
    if not days:
        return f"Her gün {when}"
    return f"{', '.join(names[d] for d in days)} {when}"
