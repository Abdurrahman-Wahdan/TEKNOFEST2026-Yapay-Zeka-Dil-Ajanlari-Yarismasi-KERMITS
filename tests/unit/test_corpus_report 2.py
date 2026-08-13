"""The run report and its publish gates.

The gates are the point: a run that would shrink the corpus or lose a whole site
must write nothing, because whatever reads documents.jsonl next cannot tell a
small corpus from a broken crawl.
"""

import pytest

from config.settings import settings
from corpus.report import BuildReport, SiteResult

pytestmark = pytest.mark.unit


def _report(previous=0, **site_kwargs):
    report = BuildReport(started_at="2026-08-09T03:00:00+00:00")
    report.previous_documents = previous
    if site_kwargs:
        report.sites["emlak"] = SiteResult(site="emlak", **site_kwargs)
    return report


# ----- gates -----

def test_a_run_with_no_documents_refuses_to_publish():
    report = _report(documents=0, fetched=100, errors=0)
    report.gate = report.check_gates()          # what run() does
    assert report.gate
    assert not report.healthy


def test_a_healthy_run_passes_the_gate():
    report = _report(previous=100, documents=105, fetched=110)
    assert report.check_gates() == ""
    assert report.healthy


def test_a_run_that_would_shrink_the_corpus_writes_nothing():
    """A site rolling out a WAF block 403s everything and would otherwise
    quietly delete thousands of documents that are still there."""
    report = _report(previous=1000, documents=800, fetched=1000)
    gate = report.check_gates()
    assert "shrink" in gate


def test_a_small_shrink_is_allowed():
    report = _report(previous=1000, documents=950, fetched=1000)
    assert report.check_gates() == ""


def test_a_site_with_too_many_errors_fails_the_run(monkeypatch):
    monkeypatch.setattr(settings, "CORPUS_MAX_ERROR_PCT", 20)
    report = _report(previous=100, documents=100, fetched=100, errors=30)
    assert "failed" in report.check_gates()


def test_a_few_errors_do_not_fail_the_run(monkeypatch):
    monkeypatch.setattr(settings, "CORPUS_MAX_ERROR_PCT", 20)
    report = _report(previous=100, documents=100, fetched=100, errors=5)
    assert report.check_gates() == ""


def test_the_first_run_has_no_previous_to_shrink_from():
    """previous_documents is 0 on a first run; that must not read as a 100% shrink."""
    report = _report(previous=0, documents=500, fetched=500)
    assert report.check_gates() == ""


# ----- shape -----

def test_the_verdict_is_the_first_line():
    report = _report(previous=100, documents=105, fetched=110)
    assert report.text().splitlines()[0].startswith("corpus 2026-08-09")
    assert "All well" in report.text().splitlines()[0]


def test_a_refused_run_says_so_in_the_first_line():
    report = _report(previous=1000, documents=500, fetched=1000)
    report.gate = report.check_gates()
    assert "REFUSED" in report.text().splitlines()[0]


def test_a_refusal_is_recorded_with_its_reason():
    report = _report()
    report.refuse("https://x.com.tr/stub", "under 250 characters")
    assert report.refusals == [("https://x.com.tr/stub", "under 250 characters")]
    assert report.as_dict()["refusals"][0]["reason"] == "under 250 characters"


def test_the_report_serialises_to_json_safely():
    import json
    report = _report(previous=100, documents=105, fetched=110)
    report.campaigns_total = 50
    report.campaigns_expired = 30
    encoded = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "campaigns" in encoded
    assert json.loads(encoded)["campaigns"]["expired"] == 30


def test_documents_sums_across_sites():
    report = BuildReport()
    report.sites["a"] = SiteResult(site="a", documents=10)
    report.sites["b"] = SiteResult(site="b", documents=15)
    assert report.documents == 25
