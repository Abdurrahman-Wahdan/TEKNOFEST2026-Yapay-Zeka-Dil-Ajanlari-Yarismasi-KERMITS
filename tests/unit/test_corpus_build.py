"""The build orchestrator: fetch -> clean -> gate -> publish.

The network seam is `corpus.fetch.crawl`, replaced here so the whole pipeline
runs offline against bytes the test puts in the store.
"""

import json

import pytest

from config.settings import settings
from corpus import build, pdf_extract, store
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

# ----- the two-pass split: pages first, PDFs later -----

def _serve_page_and_pdf(monkeypatch):
    """A page that links a fee PDF, both fetched into the store."""
    pdf_url = "https://www.kuveytturk.com.tr/documents/ucretler.pdf"
    page_url = "https://www.kuveytturk.com.tr/kendim-icin/kartlar"
    page_html = (f'<html><head><title>Kartlar | Kuveyt Türk Katılım Bankası</title>'
                 f'</head><body><h1>Kartlar</h1>'
                 f'<p>Kart ürünlerimiz hakkında ayrıntılı bilgi ve ücret tarifesi '
                 f'için aşağıdaki formu inceleyebilirsiniz. Bireysel kredi '
                 f'kartlarımız, banka kartlarımız ve sanal kartlarımız için '
                 f'geçerli olan tüm ücret, komisyon ve masraf kalemleri ilgili '
                 f'formda ayrıntılı olarak yer almaktadır. Kart başvurusu, limit '
                 f'artırımı ve ek kart talepleriniz için şubelerimize veya mobil '
                 f'uygulamamıza başvurabilirsiniz.</p>'
                 f'<a href="/documents/ucretler.pdf">Ürün ve Hizmet Ücretleri</a>'
                 f'</body></html>')
    ph, pblob = store.put(page_html.encode("utf-8"), "text/html")
    dh, dblob = store.put(b"%PDF-1.4 fee schedule bytes", "application/pdf")
    from corpus.models import RawDoc
    page_rec = RawDoc(url=page_url, fetched_at="2026-08-09T03:00:00+00:00", status=200,
                      content_type="text/html", content_hash=ph, blob=pblob)
    pdf_rec = RawDoc(url=pdf_url, fetched_at="2026-08-09T03:00:00+00:00", status=200,
                     content_type="application/pdf", content_hash=dh, blob=dblob)
    recs = {page_url: page_rec, pdf_url: pdf_rec}
    monkeypatch.setattr(build.fetch, "crawl", lambda site, **kw: dict(recs)
                        if site.slug == "kuveytturk" else {})
    return page_url, pdf_url


def test_pages_only_publishes_html_and_queues_the_pdf(monkeypatch):
    """The fast pass: websites out now, PDFs deferred to the OCR pass."""
    _serve_page_and_pdf(monkeypatch)
    report = build.run(sites=["kuveytturk"], pages_only=True)

    docs = [json.loads(l) for l in
            (store.root() / build.ARTIFACT).read_text("utf-8").splitlines()]
    assert all(d["source_type"] == "page" for d in docs)      # no PDF OCR happened
    assert len(docs) == 1

    queue = [json.loads(l) for l in
             (store.root() / build.PDF_QUEUE).read_text("utf-8").splitlines()]
    assert len(queue) == 1
    assert queue[0]["url"].endswith("ucretler.pdf")
    assert queue[0]["anchor"] == "Ürün ve Hizmet Ücretleri"    # context captured for later


def test_pages_only_never_calls_the_vision_model(monkeypatch):
    """The whole point: the fast pass must not touch gemma at all."""
    _serve_page_and_pdf(monkeypatch)

    def fail(*a, **k):
        raise AssertionError("the pages pass must not call the classifier")

    monkeypatch.setattr(build.pdf_policy, "classify", fail)
    build.run(sites=["kuveytturk"], pages_only=True)      # must not raise


def test_the_pdf_pass_ocrs_the_queue_and_merges_in(monkeypatch):
    """The slow pass adds PDF documents without disturbing the HTML ones."""
    _serve_page_and_pdf(monkeypatch)
    build.run(sites=["kuveytturk"], pages_only=True)

    # stub extraction so the test does not need poppler or a live model
    from corpus.models import Block, Page
    fake = pdf_extract.Extraction(
        pages=(Page(number=1, blocks=(Block("table", "| Ücret | 5 TL |", 0),),
                    cite_url="https://www.kuveytturk.com.tr/documents/ucretler.pdf#page=1",
                    text_hash="abc", has_tables=True),),
        text="Ürün ve Hizmet Ücretleri tablosu " * 20, engine="pdftotext+vision",
        page_count=1)
    monkeypatch.setattr(build.pdf_extract, "extract", lambda *a, **k: fake)

    report = build.process_pdfs()
    assert report.written

    docs = [json.loads(l) for l in
            (store.root() / build.ARTIFACT).read_text("utf-8").splitlines()]
    kinds = {d["source_type"] for d in docs}
    assert kinds == {"page", "pdf"}                       # HTML kept, PDF added
    pdf_doc = [d for d in docs if d["source_type"] == "pdf"][0]
    assert pdf_doc["pages"][0]["cite_url"].endswith("#page=1")


def test_a_second_pages_pass_does_not_wipe_the_pdf_documents(monkeypatch):
    """A nightly website refresh must not delete the PDFs the slow pass produced."""
    _serve_page_and_pdf(monkeypatch)
    build.run(sites=["kuveytturk"], pages_only=True)
    from corpus.models import Block, Page
    fake = pdf_extract.Extraction(
        pages=(Page(1, (Block("paragraph", "ücret bilgisi " * 30, 0),),
                    "https://www.kuveytturk.com.tr/documents/ucretler.pdf#page=1", "h"),),
        text="ücret bilgisi " * 30, engine="pdftotext+vision", page_count=1)
    monkeypatch.setattr(build.pdf_extract, "extract", lambda *a, **k: fake)
    build.process_pdfs()

    # run the pages pass again
    build.run(sites=["kuveytturk"], pages_only=True)
    docs = [json.loads(l) for l in
            (store.root() / build.ARTIFACT).read_text("utf-8").splitlines()]
    assert any(d["source_type"] == "pdf" for d in docs)   # PDF survived the refresh


def test_an_empty_queue_is_harmless(monkeypatch):
    report = build.process_pdfs()
    assert not report.written


# ----- OCR results are cached, so the pass is resumable and observable -----

def _queue_one_pdf(monkeypatch, content_hash="pdfhash1"):
    """Put one PDF in the store and the queue."""
    _, blob = store.put(b"%PDF-1.4 fee schedule", "application/pdf")
    queue = [{"url": "https://x.com.tr/documents/ucretler.pdf", "site": "kuveytturk",
              "blob": blob, "content_hash": content_hash,
              "fetched_at": "2026-08-09T03:00:00+00:00",
              "anchor": "Ücret Tarifesi", "referrer": "https://x.com.tr/kendim-icin"}]
    store.write_text(build.PDF_QUEUE, "\n".join(json.dumps(q) for q in queue) + "\n")


def test_an_ocr_result_is_cached_to_disk(monkeypatch):
    _queue_one_pdf(monkeypatch)
    from corpus.models import Block, Page
    fake = pdf_extract.Extraction(
        pages=(Page(1, (Block("table", "| Ücret | 5 TL |", 0),),
                    "https://x.com.tr/documents/ucretler.pdf#page=1", "h", has_tables=True),),
        text="Ücret tarifesi tablosu " * 20, engine="pdftotext+vision", page_count=1)
    monkeypatch.setattr(build.pdf_extract, "extract", lambda *a, **k: fake)

    build.process_pdfs()
    assert store.count_pdf_docs() == 1                 # progress is now on disk
    assert store.read_pdf_doc("pdfhash1")["accepted"] is True


def test_a_cached_pdf_skips_the_vision_model_entirely(monkeypatch):
    """The resume property: a PDF already processed must not touch gemma again."""
    _queue_one_pdf(monkeypatch)
    # seed the cache as if a prior run OCR'd it
    store.write_pdf_doc("pdfhash1", {"accepted": True, "label": "fees",
        "document": {"doc_id": "d", "source_type": "pdf", "text_hash": "t",
                     "doc_kind": "fees", "campaign_end": "", "url": "u",
                     "bank": "Kuveyt Türk", "cite_url": "u"}})

    def fail(*a, **k):
        raise AssertionError("a cached PDF must not be extracted again")

    monkeypatch.setattr(build.pdf_extract, "extract", fail)
    monkeypatch.setattr(build.pdf_policy, "classify", fail)
    report = build.process_pdfs()
    assert report.written                              # the cached doc was merged in


def test_a_pdf_the_model_could_not_read_is_not_cached(monkeypatch):
    """The permanence bug. A failed extraction says nothing about the PDF, so
    writing any verdict for it would make the next run skip a file we never
    read. Nothing on disk, and it is counted as an error, not a refusal."""
    _queue_one_pdf(monkeypatch)

    def unreachable(*a, **k):
        raise pdf_extract.TransientExtractionError("page 2: ngrok gateway error")

    monkeypatch.setattr(build.pdf_extract, "extract", unreachable)
    report = build.process_pdfs()
    assert store.read_pdf_doc("pdfhash1") is None       # nothing was concluded
    assert store.count_pdf_docs() == 0
    assert report.errors == 1
    assert report.refusals == []                        # a refusal would be a verdict


def test_a_deferred_pdf_is_recorded_so_it_can_be_chased(monkeypatch):
    """Retrying on its own is not enough -- an unread file that leaves no trace
    is indistinguishable from one that was never queued."""
    _queue_one_pdf(monkeypatch)

    def unreachable(*a, **k):
        raise pdf_extract.TransientExtractionError("page 2: ngrok gateway error")

    monkeypatch.setattr(build.pdf_extract, "extract", unreachable)
    report = build.process_pdfs()

    assert len(report.deferred) == 1
    listed = [json.loads(line) for line in
              (store.root() / build.PDF_DEFERRED).read_text("utf-8").splitlines() if line]
    assert listed[0]["url"] == "https://x.com.tr/documents/ucretler.pdf"
    assert "ngrok" in listed[0]["reason"]
    assert "deferred" in report.text()


def test_the_deferred_list_is_cleared_once_the_pdf_reads(monkeypatch):
    """A stale list would keep reporting work that is already done."""
    _queue_one_pdf(monkeypatch)

    def unreachable(*a, **k):
        raise pdf_extract.TransientExtractionError("ngrok gateway error")

    monkeypatch.setattr(build.pdf_extract, "extract", unreachable)
    build.process_pdfs()
    assert (store.root() / build.PDF_DEFERRED).read_text("utf-8").strip()

    from corpus.models import Block, Page
    fake = pdf_extract.Extraction(
        pages=(Page(1, (Block("paragraph", "ücret bilgisi " * 30, 0),),
                    "https://x.com.tr/documents/ucretler.pdf#page=1", "h"),),
        text="ücret bilgisi " * 30, engine="ocr", page_count=1)
    monkeypatch.setattr(build.pdf_extract, "extract", lambda *a, **k: fake)
    report = build.process_pdfs()
    assert report.deferred == []
    assert not (store.root() / build.PDF_DEFERRED).read_text("utf-8").strip()


def test_a_deferred_pdf_is_retried_on_the_next_run(monkeypatch):
    """The other half: because nothing was cached, the retry actually happens
    and the PDF lands once the model is reachable again."""
    _queue_one_pdf(monkeypatch)

    def unreachable(*a, **k):
        raise pdf_extract.TransientExtractionError("ngrok gateway error")

    monkeypatch.setattr(build.pdf_extract, "extract", unreachable)
    build.process_pdfs()

    from corpus.models import Block, Page
    fake = pdf_extract.Extraction(
        pages=(Page(1, (Block("table", "| Ücret | 5 TL |", 0),),
                    "https://x.com.tr/documents/ucretler.pdf#page=1", "h", has_tables=True),),
        text="Ücret tarifesi tablosu " * 20, engine="ocr", page_count=1)
    monkeypatch.setattr(build.pdf_extract, "extract", lambda *a, **k: fake)
    build.process_pdfs()
    assert store.read_pdf_doc("pdfhash1")["accepted"] is True


def test_a_rejected_pdf_is_cached_so_it_is_not_reclassified(monkeypatch):
    """A rejected PDF must not be re-sent to the classifier next run either."""
    _queue_one_pdf(monkeypatch, content_hash="rejecthash")
    # decide() rejects a KVKK-named file by rule, so no model call; cache records it
    store.write_text(build.PDF_QUEUE, json.dumps({
        "url": "https://x.com.tr/documents/kvkk-aydinlatma-metni.pdf",
        "site": "kuveytturk", "blob": store.put(b"%PDF x", "application/pdf")[1],
        "content_hash": "rejecthash", "fetched_at": "t", "anchor": "", "referrer": ""}) + "\n")
    build.process_pdfs()
    cached = store.read_pdf_doc("rejecthash")
    assert cached is not None
    assert cached["accepted"] is False
    assert cached["document"] is None


def test_a_pdf_served_as_a_page_is_routed_to_the_pdf_path(monkeypatch):
    """An extensionless PDF (content_type application/pdf from the byte sniff)
    must go to the PDF queue, not the HTML parser that would drop it."""
    page_url = "https://www.kuveytturk.com.tr/kendim-icin/kartlar"
    pdf_url = "https://www.kuveytturk.com.tr/kvkk/aydinlatma-turkce"    # no .pdf

    ph, pblob = store.put(
        (b"<html><head><title>Kartlar | Kuveyt Turk</title></head><body>"
         b"<h1>Kartlar</h1><p>"
         + b"Kart urunlerimiz hakkinda ayrintili bilgi ve ucret tarifesi burada. " * 6
         + b"</p></body></html>"), "text/html")
    dh, dblob = store.put(b"%PDF-1.7 body", "application/pdf")
    page_rec = RawDoc(url=page_url, fetched_at="2026-08-09T03:00:00+00:00", status=200,
                      content_type="text/html", content_hash=ph, blob=pblob)
    pdf_rec = RawDoc(url=pdf_url, fetched_at="2026-08-09T03:00:00+00:00", status=200,
                     content_type="application/pdf", content_hash=dh, blob=dblob)
    monkeypatch.setattr(build.fetch, "crawl", lambda site, **kw:
                        {page_url: page_rec, pdf_url: pdf_rec}
                        if site.slug == "kuveytturk" else {})

    build.run(sites=["kuveytturk"], pages_only=True, write=True)
    assert any(e["url"] == pdf_url for e in build._read_queue()), \
        "extensionless PDF was routed to HTML instead of the PDF queue"


def test_all_sites_are_crawled_in_parallel_and_merged(monkeypatch):
    """Banks run one thread each; every site's documents must survive the merge,
    and each document keeps its own bank -- proof nothing crossed threads."""
    pages = {}
    for slug, host, bank in [("kuveytturk", "www.kuveytturk.com.tr", "Kuveyt Türk"),
                             ("albaraka", "www.albaraka.com.tr", "Albaraka"),
                             ("vakif", "www.vakifkatilim.com.tr", "Vakıf")]:
        url = f"https://{host}/kampanyalar/x"
        html = (f"<html><head><title>Kampanya | {bank}</title></head><body>"
                f"<h1>Kampanya</h1><p>" + "Bu bankanın kampanya içeriği burada "
                "yeterince uzun bir paragraf olarak yer alıyor. " * 4 +
                "</p></body></html>")
        ch, blob = store.put(html.encode("utf-8"), "text/html")
        pages[slug] = (url, RawDoc(url=url, fetched_at="2026-08-09T03:00:00+00:00",
                                   status=200, content_type="text/html",
                                   content_hash=ch, blob=blob))

    monkeypatch.setattr(build.fetch, "crawl",
                        lambda site, **kw: {pages[site.slug][0]: pages[site.slug][1]}
                        if site.slug in pages else {})

    report = build.run(sites=["kuveytturk", "albaraka", "vakif"])
    assert report.documents == 3
    assert set(report.sites) == {"kuveytturk", "albaraka", "vakif"}

    docs = [json.loads(l) for l in
            (store.root() / build.ARTIFACT).read_text("utf-8").splitlines()]
    banks = {d["site"]: d["bank"] for d in docs}
    assert banks["kuveytturk"].startswith("Kuveyt")
    assert banks["albaraka"] == "Albaraka Türk Katılım Bankası"


def test_an_unknown_site_is_a_usage_error(monkeypatch):
    assert build.main(["--site", "kuveyt-turk", "--no-fetch"]) == 2


def test_the_cli_returns_zero_on_a_healthy_run(monkeypatch, capsys):
    _serve_page(monkeypatch, "https://www.kuveytturk.com.tr/kampanyalar/x", CAMPAIGN_HTML)
    code = build.main(["--site", "kuveytturk", "--quiet"])
    assert code == 0
