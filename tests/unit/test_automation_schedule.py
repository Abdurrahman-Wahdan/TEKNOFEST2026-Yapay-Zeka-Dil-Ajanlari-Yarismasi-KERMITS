"""When an automation fires next.

This is the file that has to be right. Everything else in the feature fails
loudly -- a broken tool returns prose, a broken route returns a status -- but a
wrong schedule fails silently, at a time nobody is watching, every day. So the
cases here are the ones that would have to be discovered by waiting: the exact
boundary, a weekly schedule whose day is today but already past, and the
midnight rollover.

Pure arithmetic, no clock: `now` is always an argument.

Turkey is UTC+03, so 09:00 Istanbul is 06:00Z. Every expectation below is
written in UTC because that is what the column stores.
"""

from datetime import datetime, timezone

import pytest

from api.automations.schedule import TZ, describe, next_run, valid_weekdays

pytestmark = pytest.mark.unit


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# 2026-08-25 is a Tuesday, which weekday() calls 1.
TUESDAY_NOON_UTC = utc(2026, 8, 25, 12, 0)


class TestDaily:
    def test_later_today(self):
        # 21:30 Istanbul is 18:30Z, still ahead of 12:00Z.
        assert next_run(TUESDAY_NOON_UTC, 21, 30) == utc(2026, 8, 25, 18, 30)

    def test_already_past_goes_to_tomorrow(self):
        # 09:00 Istanbul is 06:00Z, behind 12:00Z.
        assert next_run(TUESDAY_NOON_UTC, 9, 0) == utc(2026, 8, 26, 6, 0)

    def test_a_slot_a_quarter_of_an_hour_out_fires_today(self):
        """The scenario reported as "it created it for the next day".

        The real numbers off the row: created 2026-08-25 15:45:21 Istanbul
        (12:45:21Z) for 16:00, so the first run is fourteen minutes later on the
        same day -- not tomorrow. Pinned with the exact timestamp rather than a
        round one, because the report was specific and the arithmetic replayed
        clean; if this ever does slide to the 26th, this is the test that says so.
        """
        made = utc(2026, 8, 25, 12, 45, 21)
        assert next_run(made, 16, 0) == utc(2026, 8, 25, 13, 0)

    def test_a_slot_that_has_just_gone_waits_a_day(self):
        """The other half of the same rule, stated together with it.

        16:00 set at 16:00:01 is tomorrow, and it has to be: a schedule that
        fired "just now" must not fire again on the next poll thirty seconds
        later.
        """
        made = utc(2026, 8, 25, 13, 0, 1)
        assert next_run(made, 16, 0) == utc(2026, 8, 26, 13, 0)

    def test_none_and_empty_both_mean_daily(self):
        assert next_run(TUESDAY_NOON_UTC, 9, 0, None) == next_run(
            TUESDAY_NOON_UTC, 9, 0, []
        )

    def test_midnight_rollover(self):
        # 00:30 Istanbul on the 26th is 21:30Z on the 25th -- still ahead of
        # noon, and on the previous UTC day. Adding a day to the UTC instant
        # would have produced the 26th.
        assert next_run(TUESDAY_NOON_UTC, 0, 30) == utc(2026, 8, 25, 21, 30)


class TestBoundary:
    """The case that would otherwise loop forever.

    The runner claims a due row and immediately advances `next_run_at` using the
    same `now` it claimed with. If the boundary were inclusive, the new value
    would be the minute it had just run, and the next poll would run it again.
    """

    def test_exactly_on_the_boundary_moves_forward(self):
        on_it = utc(2026, 8, 25, 6, 0)  # exactly 09:00 Istanbul
        assert next_run(on_it, 9, 0) == utc(2026, 8, 26, 6, 0)

    def test_one_second_before_stays_today(self):
        just_before = utc(2026, 8, 25, 5, 59, 59)
        assert next_run(just_before, 9, 0) == utc(2026, 8, 25, 6, 0)

    def test_advancing_twice_lands_on_consecutive_days(self):
        first = next_run(utc(2026, 8, 25, 5, 0), 9, 0)
        second = next_run(first, 9, 0)
        assert first == utc(2026, 8, 25, 6, 0)
        assert second == utc(2026, 8, 26, 6, 0)


class TestWeekdays:
    def test_monday_only_from_tuesday(self):
        assert next_run(TUESDAY_NOON_UTC, 9, 0, [0]) == utc(2026, 8, 31, 6, 0)

    def test_today_only_but_already_past_waits_a_week(self):
        # Tuesday-only, 09:00 already gone: the next one is the 1st, not today.
        assert next_run(TUESDAY_NOON_UTC, 9, 0, [1]) == utc(2026, 9, 1, 6, 0)

    def test_today_only_and_still_ahead_runs_today(self):
        assert next_run(TUESDAY_NOON_UTC, 21, 0, [1]) == utc(2026, 8, 25, 18, 0)

    def test_weekday_set_picks_the_nearest(self):
        # Wednesday(2) and Friday(4) from Tuesday -> Wednesday.
        assert next_run(TUESDAY_NOON_UTC, 9, 0, [2, 4]) == utc(2026, 8, 26, 6, 0)

    def test_sunday_from_tuesday(self):
        assert next_run(TUESDAY_NOON_UTC, 9, 0, [6]) == utc(2026, 8, 30, 6, 0)

    def test_every_weekday_is_reachable(self):
        """Eight candidate days, so no weekday can be unreachable.

        The loop bound is 8 and not 7 for the today-but-past case above; this
        pins that the extra day never skips a legitimate one.
        """
        for day in range(7):
            result = next_run(TUESDAY_NOON_UTC, 9, 0, [day])
            assert result.astimezone(TZ).weekday() == day
            assert result > TUESDAY_NOON_UTC


class TestNaiveInput:
    def test_naive_is_read_as_utc(self):
        naive = datetime(2026, 8, 25, 12, 0)
        assert next_run(naive, 21, 30) == utc(2026, 8, 25, 18, 30)


class TestValidWeekdays:
    @pytest.mark.parametrize(
        "given, expected",
        [
            ([], []),
            (None, []),
            ("hafta içi", []),
            ([0, 1, 2, 3, 4], [0, 1, 2, 3, 4]),
            # Deduplicated and sorted, so two spellings store identically.
            ([4, 0, 4, 0], [0, 4]),
            # Out of range dropped, not fatal: a stray 7 must not cost the rest.
            ([7, 2], [2]),
            ([-1, 3], [3]),
            # bool is an int subclass, and True would otherwise be Tuesday.
            ([True, False], []),
            (["1", 1], [1]),
            ([None, 5], [5]),
        ],
    )
    def test_cleaning(self, given, expected):
        assert valid_weekdays(given) == expected


class TestDescribe:
    def test_daily(self):
        assert describe(9, 0, []) == "Her gün 09:00"

    def test_named_days(self):
        assert describe(21, 30, [0, 4]) == "Pzt, Cum 21:30"

    def test_pads_single_digits(self):
        assert describe(7, 5, []) == "Her gün 07:05"
