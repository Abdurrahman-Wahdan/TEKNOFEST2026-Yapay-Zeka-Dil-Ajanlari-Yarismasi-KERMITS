"""The extensive audit, maintenance at product level, and the daily report.

No network. The audit's own check function is replaced so the planning, the
recording and the escalation rules are exercised without touching a bank.
"""

import pytest

from banks import audit, families, get_bank, notify, schedule, status
from banks.health import DOWN, KNOWN, OK
from banks.models import Product
from banks.providers import base
from banks.providers.base import TemporarilyUnavailable, UnsupportedProduct

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def quick(tmp_path, monkeypatch):
    """A disposable status file, and no sleeping between products."""
    monkeypatch.setattr(status.settings, "HEALTH_STATUS_FILE",
                        str(tmp_path / "bank_status.json"))
    monkeypatch.setattr(audit.settings, "HEALTH_AUDIT_PRODUCT_DELAY", 0.0)
    status.clear_cache()
    yield
    status.clear_cache()


def catalogue(monkeypatch, bank_name: str, products: list[str], capability="finance"):
    """Give one bank a fixed catalogue and no other capabilities."""
    bank = get_bank(bank_name)
    monkeypatch.setattr(type(bank), "capabilities", frozenset({capability}))
    monkeypatch.setattr(
        type(bank), "products",
        lambda self, category, _p=products: [
            Product(code=code, name=code, category=category) for code in _p
        ],
    )
    return bank


def checks(monkeypatch, outcomes: dict):
    """Replace the audit's check with a canned outcome per product."""
    def fake(bank, unit):
        outcome = outcomes.get(unit.product, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(audit, "_check", fake)


# ----- the concurrency bug this was written after -----


def test_a_recorded_outage_is_re_tested_rather_than_believed(monkeypatch):
    """The gate must not answer the probe meant to clear it.

    status.bypass() is thread-local, so a bypass opened around a thread pool
    would not reach the workers. Then the gate fires inside the check, and a
    real outage would clear itself to ok without a single request.
    """
    status.write({"vakif": {"finance": status.entry(status.DOWN, "was down")}})
    catalogue(monkeypatch, "vakif", ["A"])
    reached = []

    def fake(bank, unit):
        reached.append(unit.product)
        return "answered"

    monkeypatch.setattr(audit, "_check", fake)
    report = audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert reached == ["A"], "the probe never reached the bank"
    assert report.healthy
    assert status.outage("vakif", "finance") is None


def test_the_gate_firing_inside_the_audit_is_down_never_known(monkeypatch):
    """Defence in depth: even if the bypass were lost, it must not clear."""
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {"A": TemporarilyUnavailable("gate fired")})
    report = audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert [r.state for r in report.results] == [DOWN]
    assert "never re-tested" in report.results[0].reason


# ----- planning -----


def test_every_product_in_the_catalogue_is_planned(monkeypatch):
    bank = catalogue(monkeypatch, "vakif", ["A", "B", "C"])
    planned = audit.plan(bank)
    assert [u.product for u in planned if u.capability == "finance"] == ["A", "B", "C"]


def test_family_entries_are_checked_too(monkeypatch):
    """A curated family code that stopped resolving is found every morning,
    not only when someone runs the test suite."""
    bank = catalogue(monkeypatch, "vakif", ["A"])
    assert any(u.capability == "families" for u in audit.plan(bank))


def test_probe_inputs_come_from_the_product_s_own_limits():
    tight = Product(code="X", name="X", category="finance",
                    min_amount=1000, max_amount=5000, min_term=1, max_term=1)
    amount, term = audit._limits(tight)
    assert amount == 5000 and term == 1

    wide = Product(code="Y", name="Y", category="finance")
    assert audit._limits(wide) == (audit.DEFAULT_AMOUNT, audit.DEFAULT_TERM)


# ----- what gets disabled -----


def test_a_broken_product_disables_only_itself(monkeypatch):
    catalogue(monkeypatch, "vakif", ["GOOD", "BROKEN", "ALSOGOOD"])
    checks(monkeypatch, {"BROKEN": RuntimeError("boom")})
    audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert status.product_outage("vakif", "finance", "BROKEN")
    assert status.product_outage("vakif", "finance", "GOOD") is None
    # The capability itself stays up: two of three products still answer.
    assert status.outage("vakif", "finance") is None


def test_a_whole_catalogue_failing_disables_the_capability(monkeypatch):
    catalogue(monkeypatch, "vakif", ["A", "B"])
    checks(monkeypatch, {"A": RuntimeError("boom"), "B": RuntimeError("boom")})
    audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    reason = status.outage("vakif", "finance")
    assert reason and "endpoint itself" in reason


def test_a_product_the_bank_declines_is_never_disabled(monkeypatch):
    """"We do not offer that" is the bank answering, not a fault."""
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {"A": UnsupportedProduct("not offered in that currency")})
    report = audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert [r.state for r in report.results] == [KNOWN]
    assert report.healthy
    assert status.product_outage("vakif", "finance", "A") is None


def test_a_transient_failure_is_retried_before_being_believed(monkeypatch):
    """One blip must not take a working product off the shelf all day."""
    catalogue(monkeypatch, "vakif", ["A"])
    attempts = []

    def flaky(bank, unit):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return "ok"

    monkeypatch.setattr(audit, "_check", flaky)
    report = audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert len(attempts) == 2
    assert report.healthy


def test_a_scoped_audit_does_not_clear_a_product_it_never_looked_at(monkeypatch):
    status.write({"emlak": {"finance": status.entry(
        status.DOWN, "was broken", None, {"OLD": status.entry(status.DOWN, "broken")})}})
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {})
    audit.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert status.product_outage("emlak", "finance", "OLD") == "broken"


# ----- the refusal a user sees -----


def test_a_disabled_product_refuses_with_the_maintenance_wording(monkeypatch):
    status.write({"vakif": {"finance": status.entry(
        status.OK, "", None, {"IF": status.entry(status.DOWN, "could not be reached")})}})

    def forbidden(*args, **kwargs):
        raise AssertionError("a product known to be down must cost no request")

    monkeypatch.setattr(base, "request_json", forbidden)
    monkeypatch.setattr(base, "request_text", forbidden)
    bank = get_bank("vakif")
    monkeypatch.setattr(
        type(bank), "products",
        lambda self, category: [Product(code="IF", name="İhtiyaç Finansmanı",
                                        category="finance")],
    )

    with pytest.raises(TemporarilyUnavailable, match="under maintenance"):
        bank.finance_quote("IF", 100_000, 24)


def test_both_tiers_say_the_same_thing():
    capability = base.maintenance_error("A Bank", "financing calculator", "unreachable")
    product = base.maintenance_error("A Bank", "İhtiyaç Finansmanı", "unreachable")
    for message in (str(capability), str(product)):
        assert "temporarily unavailable" in message
        assert "under maintenance" in message
        assert "does publish this" in message


def test_list_banks_shows_what_is_under_maintenance():
    from banks import list_banks

    assert "maintenance" not in list_banks()["vakif"]
    status.write({"vakif": {"finance": status.entry(status.DOWN, "down")}})
    assert list_banks()["vakif"]["maintenance"] == ["finance"]


# ----- the report -----


def test_a_green_run_still_reports(monkeypatch):
    """The one message that is sent whether or not anything is wrong."""
    sent = []
    monkeypatch.setattr(notify, "_post", lambda payload: sent.append(payload) or True)
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {})
    audit.run(banks=["vakif"], capabilities=["finance"])

    assert len(sent) == 1
    assert sent[0]["healthy"] is True
    assert "All well" in sent[0]["text"]


def test_a_broken_run_leads_with_what_broke(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_post", lambda payload: sent.append(payload) or True)
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {"A": RuntimeError("boom")})
    audit.run(banks=["vakif"], capabilities=["finance"])

    assert sent[0]["healthy"] is False
    assert sent[0]["text"].splitlines()[0].startswith("bank audit")
    assert "DOWN" in sent[0]["text"]
    assert "under maintenance" in sent[0]["text"]


def test_the_audit_sends_one_message_not_two(monkeypatch):
    """Change alerts travel inside the report rather than beside it."""
    changes = []
    monkeypatch.setattr(notify, "send", lambda c: changes.append(c) or True)
    monkeypatch.setattr(notify, "_post", lambda payload: True)
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {"A": RuntimeError("boom")})
    audit.run(banks=["vakif"], capabilities=["finance"])

    assert changes == []


def test_a_failed_webhook_does_not_fail_the_audit(monkeypatch):
    monkeypatch.setattr(notify.settings, "HEALTH_WEBHOOK_URL", "https://example.invalid/x")
    monkeypatch.setattr(notify, "request",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("no route")))
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {})
    assert audit.run(banks=["vakif"], capabilities=["finance"]).healthy


# ----- the schedule -----


def test_the_crontab_line_uses_the_configured_time(monkeypatch):
    monkeypatch.setattr(schedule.settings, "HEALTH_SCHEDULE", "30 4 * * *")
    line = schedule.crontab_line()
    assert line.startswith("30 4 * * *")
    assert "banks.audit" in line
    assert line.count("/") > 1, "paths must be absolute"


def test_launchd_matches_the_configured_time(monkeypatch):
    monkeypatch.setattr(schedule.settings, "HEALTH_SCHEDULE", "30 4 * * *")
    plist = schedule.launchd_plist()
    assert "<key>Minute</key><integer>30</integer>" in plist
    assert "<key>Hour</key><integer>4</integer>" in plist


def test_a_schedule_launchd_cannot_express_is_refused():
    """Better to say no than to emit a plist that runs at another time."""
    with pytest.raises(ValueError, match=r"\*/15"):
        schedule.launchd_plist("*/15 * * * *")


def test_a_malformed_schedule_says_what_is_expected():
    with pytest.raises(ValueError, match="Five fields"):
        schedule.crontab_line("0 6 *")


def test_the_families_check_never_reaches_the_status_file(monkeypatch):
    """It is not a capability a bank declares, and nothing gates on it."""
    catalogue(monkeypatch, "vakif", ["A"])
    checks(monkeypatch, {})
    report = audit.run(banks=["vakif"], notify=False)

    assert any(r.capability.startswith("families/") for r in report.results)
    assert audit.FAMILIES not in status.read().get("vakif", {})
