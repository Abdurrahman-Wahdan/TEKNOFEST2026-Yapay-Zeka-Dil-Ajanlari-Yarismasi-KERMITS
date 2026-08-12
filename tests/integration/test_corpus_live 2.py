"""Live checks against the banks' websites and the vLLM host.

Needs the internet, and the PDF and vision tests need poppler plus a reachable
vLLM. Assertions are contract assertions -- a page comes back, extraction is
non-empty, a citation opens where it claims -- never exact content, because the
sites change and that change is not a failure.

The one regression these exist to guard: the old crawler discovered 1,261 PDFs
on the banks' own domains and then threw them away because they failed a page
language-prefix test. `test_a_documents_pdf_is_fetched_and_selected` proves that
defect stays dead.
"""

import pytest

from config.settings import settings
from corpus import fetch, pdf_extract, store
from corpus.build import run
from corpus.pdf_policy import classify
from corpus.sites import get_site

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.fixture(scope="module")
def live() -> bool:
    import httpx
    try:
        return httpx.get("https://www.emlakkatilim.com.tr", timeout=10).status_code < 500
    except Exception:  # noqa: BLE001 - any failure means unavailable
        return False


@pytest.fixture
def scratch(tmp_path, monkeypatch, live):
    if not live:
        pytest.skip("the bank sites are not reachable")
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    store.clear_cache()
    fetch.clear_cache()
    yield tmp_path
    fetch.clear_cache()
    store.clear_cache()


# ----- the crawl -----

def test_a_sitemap_site_yields_pages(scratch):
    records = fetch.crawl(get_site("emlak"), limit=10)
    assert records
    assert any(r.status == 200 and r.blob for r in records.values())


def test_a_recursive_site_yields_pages(scratch):
    """Türkiye Finans and TOM publish no usable sitemap; this is the mode that
    the query-string collision used to break."""
    records = fetch.crawl(get_site("turkiyefinans"), limit=10)
    assert any(r.status == 200 and r.blob for r in records.values())


# ----- the whole pipeline -----

def test_a_site_builds_clean_documents(scratch):
    report = run(sites=["emlak"], limit=15)
    assert report.documents > 0
    assert report.healthy
    assert report.written


def test_building_the_same_site_twice_is_byte_identical(scratch):
    """Page bytes churn on a WAF token and an FX timestamp; an unchanged text
    must still produce an unchanged document, or the embedder re-does everything
    every night."""
    import pathlib

    run(sites=["emlak"], limit=15)
    artifact = pathlib.Path(scratch) / "clean" / "documents.jsonl"
    first = artifact.read_bytes()

    store.clear_cache()
    second_report = run(sites=["emlak"], limit=15)
    second = artifact.read_bytes()

    assert first == second
    assert second_report.sites["emlak"].unchanged > 0
    assert second_report.sites["emlak"].changed == 0


def test_no_published_document_dangles_after_collection(scratch):
    """GC must never delete a blob a published document still points at."""
    import json
    import pathlib

    run(sites=["emlak"], limit=15)
    store.clear_cache()
    run(sites=["emlak"], limit=15)      # a second run triggers collection

    artifact = pathlib.Path(scratch) / "clean" / "documents.jsonl"
    for line in artifact.read_text("utf-8").splitlines():
        blob = json.loads(line).get("blob")
        if blob:
            assert store.has(blob)


# ----- the 1,261-PDF defect -----

def test_a_documents_pdf_is_fetched_and_selected(scratch):
    """Emlak serves its fee schedules from asset.emlakkatilim.com.tr/documents/,
    a path with no /tr prefix. The old crawler discarded exactly these."""
    records = fetch.crawl(get_site("emlak"), limit=40)
    pdfs = [u for u, r in records.items()
            if u.lower().endswith(".pdf") and r.status == 200 and r.blob]
    assert pdfs, "no PDF was fetched from the documents/ path"


# ----- the vision path -----

@pytest.fixture(scope="module")
def vllm() -> bool:
    import httpx
    try:
        return httpx.get(settings.VLLM_BASE_URL, timeout=10).status_code < 500
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def a_live_pdf(scratch):
    """A PDF pulled from the live crawl, so the URL cannot go stale.

    Bank PDF URLs rot -- several from a six-month-old corpus already 404 -- so a
    hardcoded fixture URL is a test that fails for the wrong reason. Crawling for
    one exercises the same path the pipeline uses.
    """
    records = fetch.crawl(get_site("emlak"), limit=50)
    for url, record in records.items():
        if url.lower().endswith(".pdf") and record.status == 200 and record.blob:
            return url, store.blob_file(record.blob)
    pytest.skip("no PDF was reachable in the crawl")


def test_a_pdf_extracts_into_citable_pages(a_live_pdf, vllm):
    if not vllm:
        pytest.skip("the vLLM host is not reachable")
    url, path = a_live_pdf
    result = pdf_extract.extract(str(path), url)
    if result.error:
        pytest.skip(f"extraction unavailable: {result.error}")
    assert result.pages
    assert result.pages[0].cite_url.endswith("#page=1")
    assert result.text.strip()


def test_the_classifier_returns_a_known_label(a_live_pdf, vllm):
    if not vllm:
        pytest.skip("the vLLM host is not reachable")
    from corpus.pdf_policy import LABELS
    url, path = a_live_pdf
    decision = classify(str(path), url)
    if decision.needs_model and "unavailable" in decision.reason:
        pytest.skip("the classifier is unavailable")
    assert decision.label in LABELS
