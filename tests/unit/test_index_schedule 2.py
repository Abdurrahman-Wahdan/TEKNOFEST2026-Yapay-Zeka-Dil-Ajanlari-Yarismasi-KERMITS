"""The index schedule printer. Prints; never installs."""

import pytest

from config.settings import settings
from index import schedule

pytestmark = pytest.mark.unit


def test_the_crontab_line_carries_the_configured_schedule(monkeypatch):
    monkeypatch.setattr(settings, "INDEX_SCHEDULE", "30 3 * * *")
    line = schedule.crontab_line()
    assert line.startswith("30 3 * * *")
    assert "-m index" in line


def test_the_index_runs_after_the_corpus_build():
    """Staggered so documents.jsonl is finished before the index reads it."""
    corpus_min = int(settings.CORPUS_SCHEDULE.split()[0])
    index_min = int(settings.INDEX_SCHEDULE.split()[0])
    assert (settings.INDEX_SCHEDULE.split()[1] == settings.CORPUS_SCHEDULE.split()[1]
            and index_min > corpus_min)


def test_a_schedule_cron_cannot_express_is_refused():
    with pytest.raises(ValueError, match="15"):
        schedule.crontab_line("*/15 * * * *")


def test_a_launchd_plist_is_produced():
    plist = schedule.launchd_plist("30 3 * * *")
    assert "com.tf26.index" in plist
    assert "<string>-m</string><string>index</string>" in plist


def test_the_three_schedulers_use_distinct_labels():
    """Three launchd jobs sharing a label would clobber each other."""
    from banks import schedule as bank_schedule
    from corpus import schedule as corpus_schedule
    assert len({schedule.LABEL, corpus_schedule.LABEL, bank_schedule.LABEL}) == 3
