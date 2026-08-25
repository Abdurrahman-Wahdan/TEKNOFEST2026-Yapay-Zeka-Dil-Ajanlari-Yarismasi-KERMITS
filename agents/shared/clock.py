"""What time it is, as the agents are told it.

Every agent in this application answers Turkish users about Turkish banks, and
until now not one of them knew what day it was. A language model with no clock
does not decline to answer a question about time -- it answers from its training
data, so "bugün" meant a date months in the past, "yarın" was computed from that
wrong date, and a live figure retrieved four minutes ago was reported as though
its timestamp were the one thing on screen that could be trusted.

Two things had to be fixed together, and this module is one of them:

1. **The clock in the prompt** (here). Every system prompt now opens with the
   current Istanbul date and time, so relative language resolves against the real
   day.
2. **The clock in the data** (`banks/clock.py::stamp_tr`). Live endpoint and web
   research envelopes used to carry `retrieved_at` in UTC, and a model repeats a
   timestamp verbatim -- which is why a rate fetched at 11:04 was presented as
   08:04. That is the same bug seen from the other end, and fixing only the
   prompt would have left the model correctly aware of the time and still
   quoting a wrong one.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

#: The only wall clock this application has. The same zone
#: `api/automations/schedule.py` schedules against, by name rather than as a
#: fixed +03 offset -- Turkey has had no DST since 2016, but that is a current
#: fact about the country rather than a property of this code.
TZ = ZoneInfo("Europe/Istanbul")

#: Turkish names, indexed by `datetime.weekday()` (0 = Monday).
#:
#: Given as data next to the English name rather than instead of it. The prompts
#: are written in English on purpose -- `agents/shared/compaction.py` documents
#: what a system prompt that changes language mid-way does to which language the
#: model answers in -- but the user asks in Turkish, and "Salı" has to be a token
#: the model has seen attached to this date rather than one it has to translate.
TR_DAYS = (
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
)

#: English weekday names, same indexing. Spelled out rather than `strftime("%A")`
#: because that reads the process locale: a server with `LC_TIME` set would print
#: a different word, and this text is asserted by tests.
EN_DAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

TR_MONTHS = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def now_block(instant: datetime | None = None) -> str:
    """The current-time paragraph appended to an agent's system prompt.

    One fixed paragraph, which matters more than it looks: `agents/main/agent.py`
    measures `system_prompt()` to size the supervisor's compaction window, so
    anything in here is charged against every turn's context. Only the date and
    the month name vary, by a token at most -- the block is not a place to add
    per-turn detail that would make the measured window move underneath a
    conversation.

    `instant` is an argument so a test can assert the text rather than assert
    around a clock. Production always passes nothing.

    Rebuilt per turn rather than per process: `build_main_agent` and
    `build_specialist` both compile fresh for every request, which is what keeps
    this from being the date the server booted. It is fixed for the duration of
    one turn -- a supervisor that spends four minutes fanning out to ten banks
    was told the time it started -- and that is the intended precision. Nothing
    here is a substitute for `retrieved_at` on a live result, which is stamped
    when the endpoint actually answered.
    """
    local = (instant or datetime.now(TZ)).astimezone(TZ)
    return f"""

CURRENT DATE AND TIME

Now: {local.strftime('%Y-%m-%d %H:%M')} ({local.strftime('%z')[:3]}:00, Europe/Istanbul).
Today is {EN_DAYS[local.weekday()]} — {local.day} {TR_MONTHS[local.month - 1]} {local.year}, {TR_DAYS[local.weekday()]}.

This is the real current time and it overrides anything you believe about the
date from your training. Resolve every relative expression against it: "bugün",
"today", "şu an", "yarın", "dün", "bu hafta", "bu ay", "önümüzdeki hafta", "son
üç gün". Never guess the date, never state a different current date, and never
tell the user you do not know what day it is.

**All times you write are Turkey time (UTC+03:00).** The user lives on this
clock and no other. Never convert a time to UTC, never present a UTC time as if
it were local, and never label a time with a zone the user did not ask about.

Timestamps in tool results are already Turkey time and already carry their
`+03:00` offset. Report the clock time you were given as-is; do not shift it, and
do not add or recompute an offset. When you tell the user when a figure was
retrieved, say the wall-clock time and the date, not the raw ISO string.

Judge whether something is current against the date above. A campaign whose end
date has passed is expired, a rate retrieved days ago is stale and must be said
to be stale, and a document written earlier is not evidence about today.
"""
