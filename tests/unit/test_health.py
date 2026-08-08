"""Health checks, the status file, and the refusal a down bank gives.

No network. The transport is replaced at the one seam every provider goes
through, so the real runner and the real gate execute against recorded payloads.
"""

import json

import pytest

from banks import build_tools, get_bank, health, probes, status
from banks.providers import BANKS, base
from banks.providers.base import CAPABILITY_METHODS, TemporarilyUnavailable

from .test_banks import load, serve

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def status_file(tmp_path, monkeypatch):
    """Point the status file somewhere disposable for every test here."""
    target = tmp_path / "bank_status.json"
    monkeypatch.setattr(status.settings, "HEALTH_STATUS_FILE", str(target))
    status.clear_cache()
    yield target
    status.clear_cache()


# ----- probe coverage -----


def test_every_declared_capability_has_a_probe():
    """A bank added to the registry must not go silently unchecked."""
    assert probes.missing() == []


def test_probes_name_real_banks():
    known = {bank.name for bank in BANKS}
    for capability, table in probes.BY_CAPABILITY.items():
        for name in table:
            assert name in known, f"{capability} probe names unknown bank {name!r}"


# ----- the status file -----


def test_an_unchecked_capability_is_not_an_outage():
    """A fresh install has no status file and must still answer."""
    assert status.outage("vakif", "finance") is None


def test_a_recorded_outage_is_reported_with_its_reason(status_file):
    status.write({"vakif": {"finance": status.entry(status.DOWN, "connection refused")}})
    assert status.outage("vakif", "finance") == "connection refused"
    assert status.outage("vakif", "profit_share") is None


def test_since_survives_an_unchanged_state():
    """A long outage is one outage, not this morning's news.

    The old timestamp is deliberately last year's, because `since` has
    second resolution and a fresh one taken in the same second would compare
    equal and prove nothing.
    """
    old = {"state": status.DOWN, "since": "2025-01-01T00:00:00+00:00", "reason": "boom"}

    still_down = status.entry(status.DOWN, "boom", old)
    assert still_down["since"] == old["since"]

    recovered = status.entry(status.OK, "", old)
    assert recovered["since"] != old["since"]


def test_an_unreadable_status_file_does_not_take_the_tools_down(status_file):
    status_file.write_text("{ this is not json", encoding="utf-8")
    status.clear_cache()
    assert status.outage("vakif", "finance") is None


def test_bypass_ignores_a_recorded_outage(status_file):
    """Without this the checker could never clear an outage it recorded."""
    status.write({"vakif": {"finance": status.entry(status.DOWN, "boom")}})
    assert status.outage("vakif", "finance")
    with status.bypass():
        assert status.outage("vakif", "finance") is None
    assert status.outage("vakif", "finance")


# ----- the gate -----


def test_a_down_capability_refuses_before_touching_the_network(monkeypatch, status_file):
    status.write({"vakif": {"finance": status.entry(status.DOWN, "connection refused")}})

    def forbidden(*args, **kwargs):
        raise AssertionError("a bank known to be down must cost nothing to ask")

    monkeypatch.setattr(base, "request_json", forbidden)
    monkeypatch.setattr(base, "request_text", forbidden)

    with pytest.raises(TemporarilyUnavailable) as raised:
        get_bank("vakif").finance_quote("IF", 100000, 24)
    assert "temporarily unavailable" in str(raised.value)
    assert "connection refused" in str(raised.value)


def test_the_outage_refusal_reads_differently_from_a_missing_product(status_file):
    """"The calculator broke" and "this bank has no calculator" are different
    answers, and a user deserves the right one."""
    status.write({"vakif": {"finance": status.entry(status.DOWN, "boom")}})

    with pytest.raises(TemporarilyUnavailable) as broken:
        get_bank("vakif").finance_quote("IF", 100000, 24)
    with pytest.raises(Exception) as absent:
        get_bank("adil").finance_quote("anything", 100000, 24)

    assert "does publish this" in str(broken.value)
    assert "no public calculator" in str(absent.value)


def test_only_the_named_capability_is_gated(monkeypatch, status_file):
    status.write({"vakif": {"finance": status.entry(status.DOWN, "boom")}})
    serve(monkeypatch, [], routes={
        "DetailCurrencyListData": load("vakif", "currencies.json"),
        "CurrencyConverter": load("vakif", "converter.json"),
    })
    # convert is untouched by a finance outage
    assert get_bank("vakif").convert("USD", "TRY", 1000)


def test_every_capability_method_is_gated():
    """The gate is applied at class definition, so no bank can forget it."""
    for bank in BANKS:
        for capability, method_name in CAPABILITY_METHODS.items():
            if capability not in bank.capabilities:
                continue
            method = getattr(type(bank), method_name)
            assert getattr(method, "_status_gated", False), (
                f"{bank.name}.{method_name} is not behind the health gate"
            )


# ----- the runner -----


def test_a_bank_with_nothing_to_call_is_skipped(monkeypatch):
    report = health.run(banks=["adil", "tom"], write_status=False, notify=False)
    assert report.results == []
    assert sorted(report.skipped) == ["adil", "tom"]
    assert report.healthy


def test_a_failing_capability_is_down_and_recorded(monkeypatch, status_file):
    def boom(*args, **kwargs):
        raise ValueError("GET https://example.invalid returned 503")

    monkeypatch.setattr(base, "request_json", boom)
    monkeypatch.setattr(base, "request_text", boom)

    report = health.run(banks=["vakif"], notify=False)

    assert not report.healthy
    assert report.failures
    assert status.outage("vakif", "finance")


def test_a_bank_that_declines_is_known_not_down(monkeypatch, status_file):
    """The bank answered and said no. Nobody should be paged for that."""
    def decline(self, *args, **kwargs):
        raise base.UnsupportedProduct("Vakıf Katılım Bankası does not offer that")

    monkeypatch.setattr(health, "CHECKS", {**health.CHECKS, "finance": decline})
    report = health.run(banks=["vakif"], capabilities=["finance"], notify=False)

    assert report.healthy
    assert [r.state for r in report.results] == [health.KNOWN]
    assert status.outage("vakif", "finance") is None


def test_a_scoped_run_does_not_clear_an_outage_it_never_looked_at(status_file, monkeypatch):
    status.write({"emlak": {"finance": status.entry(status.DOWN, "boom")}})
    monkeypatch.setattr(health, "CHECKS", {**health.CHECKS, "rates": lambda bank: "ok"})

    health.run(banks=["kuveytturk"], capabilities=["rates"], notify=False)

    assert status.outage("emlak", "finance") == "boom"


def test_an_unknown_bank_lists_the_valid_ones():
    with pytest.raises(ValueError, match="Available"):
        health.run(banks=["not-a-bank"], write_status=False, notify=False)


# ----- notification -----


def test_only_state_changes_are_announced(monkeypatch, status_file):
    """Green stays quiet. Only crossings are worth a message."""
    sent = []
    from banks import notify

    monkeypatch.setattr(notify, "send", lambda changes: sent.append(changes) or True)

    def working(bank):
        return "ok"

    def broken(bank):
        raise ValueError("the bank could not be reached")

    monkeypatch.setattr(health, "CHECKS", {**health.CHECKS, "rates": working})
    health.run(banks=["kuveytturk"], capabilities=["rates"])
    # First sight of a working bank is not news, and saying so every morning is
    # how people learn to ignore the alert that matters.
    assert sent == []

    monkeypatch.setattr(health, "CHECKS", {**health.CHECKS, "rates": broken})
    health.run(banks=["kuveytturk"], capabilities=["rates"])
    assert len(sent) == 1 and sent[0][0]["to"] == status.DOWN

    health.run(banks=["kuveytturk"], capabilities=["rates"])  # still down
    assert len(sent) == 1

    monkeypatch.setattr(health, "CHECKS", {**health.CHECKS, "rates": working})
    health.run(banks=["kuveytturk"], capabilities=["rates"])
    assert len(sent) == 2 and sent[1][0]["to"] == status.OK


def test_a_failed_webhook_does_not_fail_the_run(monkeypatch):
    from banks import notify

    monkeypatch.setattr(notify.settings, "HEALTH_WEBHOOK_URL", "https://example.invalid/hook")

    def boom(*args, **kwargs):
        raise ValueError("no route to host")

    monkeypatch.setattr(notify, "request", boom)
    assert notify.send([{"bank": "vakif", "capability": "finance",
                         "from": "ok", "to": "down", "reason": "boom"}]) is False


def test_the_summary_says_down_and_back():
    text = notify_summary()
    assert "DOWN" in text and "BACK" in text


def notify_summary() -> str:
    from banks.notify import summarise

    return summarise([
        {"bank": "vakif", "capability": "finance", "from": "ok", "to": "down",
         "reason": "503"},
        {"bank": "emlak", "capability": "finance", "from": "down", "to": "ok",
         "reason": ""},
    ])


# ----- the tool -----


def test_check_bank_health_is_bound_and_returns_json(monkeypatch):
    tool = next(t for t in build_tools() if t.name == "check_bank_health")
    monkeypatch.setattr(health, "CHECKS", {**health.CHECKS, "rates": lambda bank: "ok"})

    answer = json.loads(tool.invoke({"bank": "kuveytturk"}))
    assert answer["healthy"] is True
    assert any(r["capability"] == "rates" for r in answer["results"])
