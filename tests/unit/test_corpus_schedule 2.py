"""The schedule printer. It prints; it never installs."""

import pytest

from config.settings import settings
from corpus import schedule

pytestmark = pytest.mark.unit


def test_the_crontab_line_carries_the_configured_schedule(monkeypatch):
    monkeypatch.setattr(settings, "CORPUS_SCHEDULE", "0 3 * * *")
    line = schedule.crontab_line()
    assert line.startswith("0 3 * * *")
    assert "corpus.build" in line


def test_the_command_uses_the_running_interpreter():
    assert "corpus.build" in schedule.command()
    assert "--quiet" in schedule.command()


def test_a_schedule_cron_cannot_express_is_refused():
    """A calendar entry cannot do '*/15', and a job that quietly runs at a
    different time is worse than none. Reused from banks.schedule."""
    with pytest.raises(ValueError, match="15"):
        schedule.crontab_line("*/15 * * * *")


def test_a_launchd_plist_is_produced():
    plist = schedule.launchd_plist("0 3 * * *")
    assert "com.tf26.corpus" in plist
    assert "corpus.build" in plist
    assert "<key>Hour</key><integer>3</integer>" in plist


def test_an_override_schedule_is_honoured():
    assert schedule.crontab_line("30 4 * * *").startswith("30 4 * * *")


def test_the_cli_refuses_a_bad_expression():
    assert schedule.main(["--schedule", "*/15 * * * *"]) == 2


def test_the_corpus_and_bank_schedules_use_different_labels():
    """Two launchd jobs with one label would clobber each other."""
    from banks import schedule as bank_schedule
    assert schedule.LABEL != bank_schedule.LABEL
