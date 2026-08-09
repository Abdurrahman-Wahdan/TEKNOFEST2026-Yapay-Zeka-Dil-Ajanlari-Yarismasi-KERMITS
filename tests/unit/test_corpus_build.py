"""The build orchestrator: fetch -> clean -> gate -> publish.

The network seam is `corpus.fetch.crawl`, replaced here so the whole pipeline
runs offline against bytes the test puts in the store.
"""

import json

import pytest

from config.settings import settings
from corpus import build, store
from corpus.models import RawDoc

pytestmark = pytest.mark.unit


# Real campaigns run ~1,400 characters (measured median); this is a compact but
# realistic one, above the 250-char stub floor.
CAMPAIGN_HTML = """<html><head>
<title>Colin's Taksit Fırsatı | Kuveyt Türk Katılım Bankası</title></head>
<body><h1 id="k">Colin's Taksit Fırsatı</h1>
<p><b>Kampanya Tarihleri</b>6.08.2026 - 31.12.2026</p>
<p>Kuveyt Türk bireysel kredi kartları ile 31 Aralık 2026 tarihine kadar
Colin's resmi web sitesinde ve mağazalarında yapacağınız alışverişlerinizde
vade farksız 4 aya varan taksit imkanı sizi bekliyor.</p>
<p>Kampanyaya tüm Kuveyt Türk Bireysel Kredi Kartları (Miles &amp; Smiles,
Sağlam Kart, Sağlam Kart Kampüs, Sağlam Kart Genç) ile bu kartların sanal ve ek
kartları dahildir. Mevzuatın öngördüğü taksit sayılarının üzerinde
taksitlendirme yapılamayacaktır.</p>
<p>İşlemlerin taksitli olabilmesi için ödeme anında taksitli seçimin yapılması
gerekmektedir. Kuveyt Türk ve Colin's önceden haber vermeden kampanya
koşullarında değişiklik yapabilir ya da kampanyayı sonlandırabilir.</p>
</body></html>"""


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    store.clear_cache()
    yield tmp_path
    store.clear_cache()


def _serve_page(monkeypatch, url, html, records_extra=None):
    """Put the page's bytes in the store and make crawl() return its record."""
    content_hash, blob = store.put(html.encode("utf-8"), "text/html")
    record = RawDoc(url=url, fetched_at="2026-08-09T03:00:00+00:00", status=200,
                    content_type="text/html", content_hash=content_hash, blob=blob)
    records = {url: record}
    if records_extra:
        records.update(records_extra)

    monkeypatch.setattr(build.fetch, "crawl", lambda site, **kw: dict(records)
                        if site.slug == "kuveytturk" else {})
    return record


# ----- a page becomes a document -----

def test_a_campaign_page_becomes_a_campaign_document(monkeypatch):
    url = "https://www.kuveytturk.com.tr/kampanyalar/colins-taksit"
    _serve_page(monkeypatch, url, CAMPAIGN_HTML)

    report = build.run(sites=["kuveytturk"])
    assert report.documents == 1
    assert report.written

    written = json.loads((store.root() / build.ARTIFACT).read_text("utf-8").splitlines()[0])
    assert written["doc_kind"] == "campaign"
    assert written["bank"] == "Kuveyt Türk Katılım Bankası"
    assert written["campaign_end"] == "2026-12-31"
    assert written["title"] == "Colin's Taksit Fırsatı"       # suffix stripped
    assert "description" not in written


def test_the_campaign_block_counts_active_and_expired(monkeypatch):
    _serve_page(monkeypatch, "https://www.kuveytturk.com.tr/kampanyalar/x", CAMPAIGN_HTML)
    report = build.run(sites=["kuveytturk"])
    assert report.campaigns_total == 1
    # end date is 2026-12-31; whether it is active depends on today, but total holds
    assert report.campaigns_active + report.campaigns_expired == 1


# ----- refusals -----

def test_a_stub_page_is_refused_not_written(monkeypatch):
    url = "https://www.kuveytturk.com.tr/x"
    _serve_page(monkeypatch, url, "<html><body><p>Kısa.</p></body></html>")
    report = build.run(sites=["kuveytturk"])
    assert report.documents == 0
    assert report.refusals
    assert "characters" in report.refusals[0][1]


def test_a_crawler_index_page_is_refused(monkeypatch):
    url = "https://www.kuveytturk.com.tr/index"
    toc = ("<html><body><h1>Kuveyt Türk — Site İçeriği</h1>"
           "<p>Toplam 2366 sayfa. Tarih: 2026-08-08</p></body></html>")
    _serve_page(monkeypatch, url, toc)
    report = build.run(sites=["kuveytturk"])
    assert report.documents == 0
    assert any("index" in reason for _, reason in report.refusals)


# ----- gates -----

def test_a_run_that_shrinks_the_corpus_leaves_the_old_artifact(monkeypatch):
    """The safety property: a bad night must not replace yesterday's file."""
    good = "\n".join(json.dumps({"doc_id": f"{n:016x}"}) for n in range(100))
    store.write_text(build.ARTIFACT, good + "\n")

    _serve_page(monkeypatch, "https://www.kuveytturk.com.tr/kampanyalar/x", CAMPAIGN_HTML)
    report = build.run(sites=["kuveytturk"])

    assert not report.written
    assert report.gate
    # the old 100-line artifact is still there, untouched
    assert len((store.root() / build.ARTIFACT).read_text("utf-8").splitlines()) == 100


# ----- idempotence -----

def test_building_twice_over_the_same_store_is_byte_identical(monkeypatch):
    """No run timestamp in the artifact, sorted by doc_id: same input, same bytes."""
    _serve_page(monkeypatch, "https://www.kuveytturk.com.tr/kampanyalar/x", CAMPAIGN_HTML)
    build.run(sites=["kuveytturk"])
    first = (store.root() / build.ARTIFACT).read_bytes()
    build.run(sites=["kuveytturk"])
    second = (store.root() / build.ARTIFACT).read_bytes()
    assert first == second


def test_a_page_whose_bytes_churn_but_text_holds_is_unchanged(monkeypatch):
    """Bank pages carry a rotating WAF token and an FX timestamp, so their bytes
    move every run while the words do not. Counting that as 'changed' would
    re-embed most of the corpus nightly for nothing."""
    url = "https://www.kuveytturk.com.tr/kampanyalar/x"

    # First run: fresh page.
    _serve_page(monkeypatch, url, CAMPAIGN_HTML)
    build.run(sites=["kuveytturk"])

    # Second run: same words, different bytes (a token changed in a comment).
    churned = CAMPAIGN_HTML.replace("</body>", "<!-- token=abc123 --></body>")
    content_hash, blob = store.put(churned.encode("utf-8"), "text/html")
    from corpus.models import RawDoc
    record = RawDoc(url=url, fetched_at="2026-08-10T03:00:00+00:00", status=200,
                    content_type="text/html", content_hash=content_hash, blob=blob)
    monkeypatch.setattr(build.fetch, "crawl", lambda site, **kw: {url: record}
                        if site.slug == "kuveytturk" else {})
    report = build.run(sites=["kuveytturk"])

    assert report.sites["kuveytturk"].unchanged == 1
    assert report.sites["kuveytturk"].changed == 0


def test_no_published_document_points_at_a_missing_blob(monkeypatch):
    """When text is unchanged the document keeps its first-seen blob, so the
    manifest must too -- else GC deletes the blob the document still names."""
    url = "https://www.kuveytturk.com.tr/kampanyalar/x"
    _serve_page(monkeypatch, url, CAMPAIGN_HTML)
    build.run(sites=["kuveytturk"])

    churned = CAMPAIGN_HTML.replace("</body>", "<!-- v2 --></body>")
    content_hash, blob = store.put(churned.encode("utf-8"), "text/html")
    from corpus.models import RawDoc
    record = RawDoc(url=url, fetched_at="2026-08-10T03:00:00+00:00", status=200,
                    content_type="text/html", content_hash=content_hash, blob=blob)
    monkeypatch.setattr(build.fetch, "crawl", lambda site, **kw: {url: record}
                        if site.slug == "kuveytturk" else {})
    build.run(sites=["kuveytturk"])

    written = json.loads(
        (store.root() / build.ARTIFACT).read_text("utf-8").splitlines()[0])
    assert store.has(written["blob"])


def test_no_write_reports_without_publishing(monkeypatch):
    url = "https://www.kuveytturk.com.tr/kampanyalar/x"
    _serve_page(monkeypatch, url, CAMPAIGN_HTML)
    report = build.run(sites=["kuveytturk"], write=False)
    assert report.documents == 1
    assert not report.written
    assert not (store.root() / build.ARTIFACT).exists()


# ----- CLI -----

def test_an_unknown_site_is_a_usage_error(monkeypatch):
    assert build.main(["--site", "kuveyt-turk", "--no-fetch"]) == 2


def test_the_cli_returns_zero_on_a_healthy_run(monkeypatch, capsys):
    _serve_page(monkeypatch, "https://www.kuveytturk.com.tr/kampanyalar/x", CAMPAIGN_HTML)
    code = build.main(["--site", "kuveytturk", "--quiet"])
    assert code == 0
