"""Claiming a due automation, and turning one into a report.

No database and no model. `claim_due` and `run_automation` both take their
session factory as an argument, following `api/saved_tables.py::save_table_view`,
and `run_automation` takes the supervisor as an argument too -- so the claim
semantics and the report assembly are both testable here, which is where the
quiet failures live:

  * the advance happens BEFORE the run, so a crash costs one report rather than
    firing the same automation on every poll forever;
  * an `error` frame that arrives after usable prose keeps the prose;
  * nothing raises out of a run, because a report is the only evidence the user
    has that the automation exists.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from api.automations.conditions import ConditionResult
from api.automations.runner import Due, collect, run_automation, tick

pytestmark = pytest.mark.unit


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = utc(2026, 8, 25, 12, 0)  # Tuesday


class _Row:
    """Stands in for an Automation row; needs no ORM to be assigned to."""

    def __init__(self, hour=9, minute=0, weekdays=None, enabled=True):
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.title = "Sabah altın raporu"
        self.prompt = "Gram altın fiyatlarını karşılaştır."
        self.hour = hour
        self.minute = minute
        self.weekdays = weekdays if weekdays is not None else []
        self.web_search = True
        self.enabled = enabled
        self.next_run_at = utc(2026, 8, 25, 6, 0)
        self.last_run_at = None
        self.last_error = ""
        self.kind = "scheduled_report"
        self.condition = {}
        self.interval_minutes = None
        self.window_start_minute = None
        self.window_end_minute = None
        self.last_condition_met = None
        self.last_observation = {}
        self.last_triggered_at = None


class _Store:
    """A session that answers one `scalars()` and records what it was given."""

    def __init__(self, rows=(), lookup=None):
        self.rows = list(rows)
        self.lookup = lookup or {}
        self.added = []
        self.statements = []

    def scalars(self, statement):
        self.statements.append(statement)
        return self

    def all(self):
        return self.rows

    def add(self, obj):
        self.added.append(obj)

    def get(self, _model, key):
        return self.lookup.get(key)

    def flush(self):
        pass

    def refresh(self, _obj):
        pass


def _scope_for(store):
    @contextmanager
    def scope():
        yield store

    return scope


class _Event:
    """One StreamEvent, minus pydantic."""

    def __init__(self, type, text=None, citation=None, detail=None):
        self.type = type
        self.text = text
        self.citation = citation
        self.detail = detail


class _Citation:
    def __init__(self, url):
        self.url = url

    def model_dump(self, mode="json"):
        return {"cite_url": self.url}


def _due() -> Due:
    return Due(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Sabah altın raporu",
        prompt="Gram altın fiyatlarını karşılaştır.",
        web_search=True,
        next_run_at=NOW,
    )


class TestClaimDue:
    def test_advances_the_schedule_before_returning(self):
        """The advance is the claim. A returned Due must already be rescheduled.

        If this ever regressed to advancing after the run, a crash mid-run would
        leave a still-due row and the next poll would run it again -- and again
        after the next crash.
        """
        from api.automations.runner import claim_due

        row = _Row(hour=9, minute=0)
        store = _Store([row])
        claimed = claim_due(NOW, _scope_for(store))

        assert len(claimed) == 1
        # 09:00 Istanbul tomorrow, i.e. 06:00Z on the 26th.
        assert row.next_run_at == utc(2026, 8, 26, 6, 0)
        assert claimed[0].next_run_at == row.next_run_at
        assert row.next_run_at > NOW

    def test_copies_values_out_rather_than_holding_the_row(self):
        """A run takes minutes; it must not hold a database transaction open."""
        from api.automations.runner import claim_due

        row = _Row()
        claimed = claim_due(NOW, _scope_for(_Store([row])))[0]

        assert isinstance(claimed, Due)
        assert claimed.id == row.id
        assert claimed.prompt == row.prompt
        assert claimed.web_search is True
        # Frozen, so nothing downstream can mutate what was claimed.
        with pytest.raises(Exception):
            claimed.title = "changed"  # type: ignore[misc]

    def test_nothing_due_is_not_an_error(self):
        from api.automations.runner import claim_due

        assert claim_due(NOW, _scope_for(_Store([]))) == []

    def test_condition_alert_advances_by_its_interval(self):
        from api.automations.runner import claim_due

        row = _Row()
        row.kind = "condition_alert"
        row.condition = {"version": 1}
        row.interval_minutes = 30
        claimed = claim_due(NOW, _scope_for(_Store([row])))[0]

        assert row.next_run_at == utc(2026, 8, 25, 12, 30)
        assert claimed.kind == "condition_alert"
        assert claimed.interval_minutes == 30

    def test_research_report_can_advance_by_the_same_interval(self):
        from api.automations.runner import claim_due

        row = _Row()
        row.interval_minutes = 15
        claimed = claim_due(NOW, _scope_for(_Store([row])))[0]

        assert row.next_run_at == utc(2026, 8, 25, 12, 15)
        assert claimed.kind == "scheduled_report"
        assert claimed.interval_minutes == 15

    def test_interval_report_honors_its_daily_window(self):
        from api.automations.runner import claim_due

        row = _Row()
        row.interval_minutes = 15
        # Istanbul is UTC+3, so NOW=15:00 local. Window begins at 16:00 local.
        row.window_start_minute = 16 * 60
        row.window_end_minute = 18 * 60
        claim_due(NOW, _scope_for(_Store([row])))

        assert row.next_run_at == utc(2026, 8, 25, 13, 0)


class TestCollect:
    def test_joins_tokens_in_order(self):
        result = collect([
            _Event("status"),
            _Event("token", text="Altın "),
            _Event("token", text="fiyatları"),
        ])
        assert result.body == "Altın fiyatları"
        assert result.status == "ok"

    def test_keeps_citations_as_dicts(self):
        result = collect([
            _Event("token", text="x"),
            _Event("citation", citation=_Citation("https://a.example/1")),
            _Event("citation", citation=_Citation("https://b.example/2")),
        ])
        assert result.citations == [
            {"cite_url": "https://a.example/1"},
            {"cite_url": "https://b.example/2"},
        ]

    def test_an_error_after_prose_keeps_the_prose(self):
        """Half a gold-price report beats a row that says only "failed"."""
        result = collect([
            _Event("token", text="Ziraat Katılım: 4.150 TL"),
            _Event("error", detail="Kuveyt Türk unavailable"),
        ])
        assert result.body == "Ziraat Katılım: 4.150 TL"
        assert result.status == "failed"
        assert result.error == "Kuveyt Türk unavailable"

    def test_an_exception_midway_keeps_the_prose(self):
        def half():
            yield _Event("token", text="yarım")
            raise RuntimeError("boom")

        result = collect(half())
        assert result.body == "yarım"
        assert result.status == "failed"
        assert "RuntimeError" in result.error

    def test_no_tokens_at_all_is_a_failure(self):
        result = collect([_Event("status")])
        assert result.status == "failed"
        assert result.error


class TestRunAutomation:
    def test_writes_a_report_and_stamps_the_automation(self):
        due = _due()
        row = _Row()
        store = _Store(lookup={due.id: row})

        report = run_automation(
            due,
            ask=lambda d: [_Event("token", text="rapor")],
            scope=_scope_for(store),
        )

        assert report.body == "rapor"
        assert report.status == "ok"
        assert report.title == due.title
        assert report.automation_id == due.id
        assert report.user_id == due.user_id
        # Unread: this is what the notification bell counts.
        assert report.read_at is None
        assert row.last_run_at is not None
        assert row.last_error == ""

    def test_a_raising_supervisor_still_produces_a_report(self):
        """Never raises. Silence is indistinguishable from a forgotten automation."""
        due = _due()
        row = _Row()
        store = _Store(lookup={due.id: row})

        def explode(_due):
            raise RuntimeError("tunnel gone")

        report = run_automation(due, ask=explode, scope=_scope_for(store))

        assert report.status == "failed"
        assert "RuntimeError" in report.error
        assert "tunnel gone" in report.error
        assert report.body == ""
        # And the automation carries the reason, so the list can show it broken.
        assert "tunnel gone" in row.last_error

    def test_a_generator_that_raises_midway_keeps_what_it_wrote(self):
        """A tunnel that drops mid-answer must not cost the whole morning.

        This is where the chat router's behaviour is deliberately NOT copied: it
        discards a partial answer, which is right for a turn the user watched
        fail and can retry with one click. Nobody is watching 09:00.
        """
        due = _due()
        store = _Store(lookup={due.id: _Row()})

        def half(_due):
            yield _Event("token", text="Ziraat Katılım: 4.150 TL")
            raise RuntimeError("cut off")

        report = run_automation(due, ask=half, scope=_scope_for(store))
        assert report.body == "Ziraat Katılım: 4.150 TL"
        assert report.status == "failed"
        assert "cut off" in report.error

    def test_citations_before_a_mid_stream_failure_survive_too(self):
        due = _due()
        store = _Store(lookup={due.id: _Row()})

        def half(_due):
            yield _Event("token", text="x")
            yield _Event("citation", citation=_Citation("https://a.example/1"))
            raise RuntimeError("cut off")

        report = run_automation(due, ask=half, scope=_scope_for(store))
        assert report.citations == [{"cite_url": "https://a.example/1"}]

    def test_a_missing_automation_row_does_not_stop_the_report(self):
        """The row can be gone: deleted between the claim and the run."""
        due = _due()
        store = _Store(lookup={})
        report = run_automation(
            due, ask=lambda d: [_Event("token", text="x")], scope=_scope_for(store)
        )
        assert report.status == "ok"

    def test_condition_alert_notifies_only_on_false_to_true_edge(self):
        condition = {
            "version": 1,
            "left": {"source": "bank_rate", "bank": "kuveytturk", "code": "XAU", "side": "sell"},
            "operator": "gte",
            "right": {"source": "constant", "value": 7000},
        }
        due = Due(
            **{**_due().__dict__, "kind": "condition_alert", "condition": condition}
        )
        row = _Row()
        row.kind = "condition_alert"
        store = _Store(lookup={due.id: row})
        result = ConditionResult(
            matched=True,
            body="alarm",
            observations={"matched": True},
            citations=[{"cite_url": "https://bank.example"}],
        )

        report = run_automation(due, scope=_scope_for(store), evaluate=lambda _: result)
        assert report is not None and report.body == "alarm"
        assert row.last_condition_met is True
        assert row.last_observation == {"matched": True}
        assert row.last_triggered_at is not None

        due_again = Due(**{**due.__dict__, "last_condition_met": True})
        second = run_automation(
            due_again, scope=_scope_for(store), evaluate=lambda _: result
        )
        assert second is None

    def test_false_condition_updates_state_without_a_report(self):
        condition = {
            "version": 1,
            "left": {"source": "bank_rate", "bank": "kuveytturk", "code": "XAU", "side": "sell"},
            "operator": "gte",
            "right": {"source": "constant", "value": 7000},
        }
        due = Due(
            **{**_due().__dict__, "kind": "condition_alert", "condition": condition}
        )
        row = _Row()
        row.kind = "condition_alert"
        store = _Store(lookup={due.id: row})
        result = ConditionResult(
            matched=False, body="", observations={"matched": False}
        )

        assert run_automation(
            due, scope=_scope_for(store), evaluate=lambda _: result
        ) is None
        assert row.last_condition_met is False
        assert row.last_observation == {"matched": False}


class TestTick:
    def test_claims_then_runs_each(self):
        row = _Row()
        store = _Store([row], lookup={})
        asked = []

        reports = tick(
            NOW,
            ask=lambda d: asked.append(d) or [_Event("token", text="ok")],
            scope=_scope_for(store),
        )

        assert len(reports) == 1
        assert len(asked) == 1
        assert asked[0].id == row.id
