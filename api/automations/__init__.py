"""Scheduled agent runs: the schedule, the runner, and the loop that drives it.

    schedule.py   when the next run is. Pure arithmetic, no I/O.
    runner.py     claiming a due automation and turning it into one report.
    loop.py       the background thread that calls the runner, and the lock
                  that keeps exactly one process doing so.

Split three ways because the interesting part is the arithmetic and it must be
testable without a database, a model, or a clock that has to be waited on.
"""

from .schedule import TZ, describe, next_run, valid_weekdays

__all__ = ["TZ", "describe", "next_run", "valid_weekdays"]
