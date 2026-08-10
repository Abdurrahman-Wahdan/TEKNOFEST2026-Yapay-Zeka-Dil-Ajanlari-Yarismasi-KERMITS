"""The crawl engine: what it will fetch, what it refuses, and what it reuses.

Every test replaces `fetch._get`, the single function in this package that
touches the network.
"""

import httpx
import pytest

from config.settings import settings
from corpus import fetch, store
from corpus.sites import get_site

pytestmark = pytest.mark.unit

KUVEYT = get_site("kuveytturk")
EMLAK = get_site("emlak")          # declares include_prefixes=("/tr",)
TFINANS = get_site("turkiyefinans")


def response(status=200, body=b"", content_type="text/html", headers=None):
    return httpx.Response(
        status_code=status,
        content=body,
        headers={"content-type": content_type, **(headers or {})},
        request=httpx.Request("GET", "https://example.invalid/"),
    )


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """A scratch corpus, no delays, and no shared client left behind."""
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "CORPUS_DELAY", 0.0)
    store.clear_cache()
    fetch.clear_cache()
    yield tmp_path
    fetch.clear_cache()
    store.clear_cache()


@pytest.fixture
def serve(monkeypatch):
    """Answer requests from a {url_fragment: response} table, recording calls."""
    def install(routes, default=None):
        calls = []

        def fake_get(client, url, headers=None):
            calls.append((url, dict(headers or {})))
            for fragment, reply in routes.items():
                if fragment in url:
                    return reply() if callable(reply) else reply
            if default is not None:
                return default
            return response(status=404)

        monkeypatch.setattr(fetch, "_get", fake_get)
        return calls
    return install


# ----- what counts as a page -----

def test_a_foreign_language_page_is_not_wanted():
    assert not fetch.wanted_page("https://www.kuveytturk.com.tr/en/about", KUVEYT)
    assert not fetch.wanted_page("https://www.kuveytturk.com.tr/ar/hakkimizda", KUVEYT)


def test_a_turkish_page_is_wanted():
    assert fetch.wanted_page("https://www.kuveytturk.com.tr/kampanyalar", KUVEYT)


def test_a_page_outside_the_declared_prefix_is_not_wanted():
    """Emlak declares /tr, so /kurumsal is another language's tree."""
    assert fetch.wanted_page("https://www.emlakkatilim.com.tr/tr/kampanyalar", EMLAK)
    assert not fetch.wanted_page("https://www.emlakkatilim.com.tr/kurumsal", EMLAK)


def test_another_bank_is_never_crawled_as_a_page():
    assert not fetch.wanted_page("https://www.albaraka.com.tr/tr/x", KUVEYT)


def test_stylesheets_and_images_are_not_pages():
    for url in ("https://www.kuveytturk.com.tr/a.css",
                "https://www.kuveytturk.com.tr/logo.PNG",
                "https://www.kuveytturk.com.tr/x.woff2"):
        assert not fetch.wanted_page(url, KUVEYT)


def test_a_pdf_is_not_crawled_as_a_page():
    assert not fetch.wanted_page("https://www.kuveytturk.com.tr/a/form.pdf", KUVEYT)


# ----- the 1,261-PDF defect -----

def test_a_pdf_outside_the_language_prefix_is_still_fetched():
    """The whole point of splitting wanted_page from wanted_asset.

    The old crawler ran PDFs through the page language-prefix test, so 1,261 PDFs
    on the banks' own domains under paths like /documents/ were discovered and
    then thrown away.
    """
    url = "https://asset.emlakkatilim.com.tr/documents/urun-ve-hizmet-ucretleri.pdf"
    assert not fetch.wanted_page(url, EMLAK)      # not a page, and not under /tr
    assert fetch.wanted_asset(url, EMLAK)         # but still a document we want


def test_a_pdf_on_a_regulator_host_is_allowed_by_the_asset_rule():
    """The host surface is bounded to the bank plus named Turkish authorities.
    The selection policy still decides whether an allowed file is worth reading."""
    assert fetch.wanted_asset("https://www.tkbb.org.tr/standart.pdf", KUVEYT)


def test_a_non_pdf_is_not_an_asset_we_fetch():
    assert not fetch.wanted_asset("https://www.kuveytturk.com.tr/kampanyalar", KUVEYT)


def test_asset_detection_ignores_case_and_query():
    assert fetch.is_asset("https://x.com.tr/A.CSS")
    assert fetch.is_asset("https://x.com.tr/a/b.woff2")
    assert not fetch.is_asset("https://x.com.tr/kampanyalar")


# ----- conditional GET -----

def test_a_stored_etag_is_sent_back(serve):
    calls = serve({"/p": response(body=b"<html>hi</html>")})
    fetch.fetch_one("https://www.kuveytturk.com.tr/p", {"etag": '"abc"'})
    assert calls[0][1]["If-None-Match"] == '"abc"'


def test_a_stored_last_modified_is_sent_back(serve):
    calls = serve({"/p": response(body=b"<html>hi</html>")})
    fetch.fetch_one("https://www.kuveytturk.com.tr/p",
                    {"last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"})
    assert calls[0][1]["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"


def test_a_304_carries_the_previous_document_forward(serve):
    """An unchanged page must cost nothing beyond the round trip."""
    serve({"/p": response(status=304)})
    previous = {"content_hash": "deadbeef", "blob": "de/ad/deadbeef.html",
                "content_type": "text/html", "etag": '"abc"', "size": 12}
    record = fetch.fetch_one("https://www.kuveytturk.com.tr/p", previous)
    assert record.status == 304
    assert record.content_hash == "deadbeef"
    assert record.blob == "de/ad/deadbeef.html"


def test_unchanged_bytes_reuse_the_stored_blob(serve):
    """Even without a 304, identical bytes must not store twice."""
    serve({"/p": response(body=b"<html>same</html>")})
    first = fetch.fetch_one("https://www.kuveytturk.com.tr/p")
    second = fetch.fetch_one("https://www.kuveytturk.com.tr/p")
    assert first.content_hash == second.content_hash
    assert first.blob == second.blob


# ----- failures are data -----

def test_a_transport_error_is_recorded_not_raised(serve, monkeypatch):
    def boom(client, url, headers=None):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(fetch, "_get", boom)
    record = fetch.fetch_one("https://www.kuveytturk.com.tr/p")
    assert record.status == 0
    assert "ConnectError" in record.error
    assert record.blob == ""


def test_a_404_is_recorded_with_its_status(serve):
    serve({}, default=response(status=404))
    record = fetch.fetch_one("https://www.kuveytturk.com.tr/gone")
    assert record.status == 404
    assert record.error == "HTTP 404"


def test_an_oversize_pdf_is_refused_and_reported_not_skipped(serve, monkeypatch):
    """A document we chose not to read is not the same as one that was not there."""
    monkeypatch.setattr(settings, "CORPUS_MAX_PDF_MB", 1)
    serve({"/big.pdf": response(body=b"%PDF" + b"x" * 2_000_000,
                                content_type="application/pdf")})
    record = fetch.fetch_one("https://www.kuveytturk.com.tr/big.pdf")
    assert record.status == 200
    assert record.blob == ""
    assert "over the 1 MB limit" in record.error


def test_a_stored_page_keeps_its_bytes(serve):
    serve({"/p": response(body="kâr payı".encode("utf-8"))})
    record = fetch.fetch_one("https://www.kuveytturk.com.tr/p")
    assert store.get(record.blob).decode("utf-8") == "kâr payı"


def test_a_pdf_at_an_extensionless_url_is_detected_by_its_bytes(serve):
    """Kuveyt Türk serves PDFs at URLs like .../aydinlatma-...-turkce with no
    .pdf and no Content-Type. Trusting the URL sends them to the HTML parser as
    garbage; the byte signature is the honest type."""
    serve({"/aydinlatma-turkce": response(body=b"%PDF-1.7\nfake pdf body",
                                           content_type="")})
    record = fetch.fetch_one("https://www.kuveytturk.com.tr/kvkk/aydinlatma-turkce")
    assert record.content_type == "application/pdf"
    assert record.blob.endswith(".pdf")


# ----- robots -----

def test_a_disallowed_path_is_not_fetched(serve):
    serve({
        "robots.txt": response(body=b"User-agent: *\nDisallow: /gizli\n",
                               content_type="text/plain"),
    })
    assert not fetch.allowed("https://www.kuveytturk.com.tr/gizli/x", KUVEYT)
    assert fetch.allowed("https://www.kuveytturk.com.tr/kampanyalar", KUVEYT)


def test_a_missing_robots_allows_everything(serve):
    """No robots.txt is not an outage, and must not skip a whole site."""
    serve({}, default=response(status=404))
    assert fetch.allowed("https://www.kuveytturk.com.tr/anything", KUVEYT)


def test_a_crawl_delay_can_only_slow_us_down(serve, monkeypatch):
    monkeypatch.setattr(settings, "CORPUS_DELAY", 0.25)
    serve({"robots.txt": response(body=b"User-agent: *\nCrawl-delay: 2\n",
                                  content_type="text/plain")})
    _, delay = fetch.robots(KUVEYT)
    assert delay == 2.0


def test_a_faster_crawl_delay_does_not_speed_us_up(serve, monkeypatch):
    monkeypatch.setattr(settings, "CORPUS_DELAY", 0.25)
    serve({"robots.txt": response(body=b"User-agent: *\nCrawl-delay: 0.01\n",
                                  content_type="text/plain")})
    _, delay = fetch.robots(KUVEYT)
    assert delay == 0.25


# ----- discovery -----

def test_sitemap_urls_are_canonicalised_to_the_site_host(serve):
    serve({
        "robots.txt": response(status=404),
        "sitemap.xml": response(
            body=b"<urlset><url><loc>http://kuveytturk.com.tr:443/a/</loc></url>"
                 b"<url><loc>https://www.kuveytturk.com.tr/b</loc></url></urlset>",
            content_type="application/xml"),
    })
    found = fetch.sitemap_urls(KUVEYT)
    assert "https://www.kuveytturk.com.tr/a" in found
    assert "https://www.kuveytturk.com.tr/b" in found


def test_a_sitemap_index_is_followed(serve):
    serve({
        "robots.txt": response(status=404),
        "sitemap.xml": response(
            body=b"<sitemapindex><sitemap><loc>"
                 b"https://www.kuveytturk.com.tr/sitemap-2.xml</loc></sitemap>"
                 b"</sitemapindex>", content_type="application/xml"),
        "sitemap-2.xml": response(
            body=b"<urlset><url><loc>https://www.kuveytturk.com.tr/deep</loc></url>"
                 b"</urlset>", content_type="application/xml"),
    })
    assert "https://www.kuveytturk.com.tr/deep" in fetch.sitemap_urls(KUVEYT)


def test_a_sitemap_entry_on_another_domain_is_ignored(serve):
    """A sitemap listing a partner's site must not widen the crawl."""
    serve({
        "robots.txt": response(status=404),
        "sitemap.xml": response(
            body=b"<urlset><url><loc>https://partner.example.com/x</loc></url>"
                 b"</urlset>", content_type="application/xml"),
    })
    assert fetch.sitemap_urls(KUVEYT) == set()


def test_links_carry_their_anchor_text():
    """For a PDF the anchor text is the best title available."""
    html = ('<a href="/documents/ucretler.pdf">Ürün ve Hizmet Ücretleri</a>'
            '<a href="#top">top</a><a href="javascript:;">x</a>')
    found = fetch.links(html, "https://www.kuveytturk.com.tr/kendim-icin")
    assert found == [("https://www.kuveytturk.com.tr/documents/ucretler.pdf",
                      "Ürün ve Hizmet Ücretleri")]


def test_unparseable_html_yields_no_links_rather_than_raising():
    assert fetch.links("", "https://x.com.tr/") == []


# ----- host surface -----

def test_a_pdf_on_an_unrelated_host_is_not_fetched():
    """Following PDF links to any host means the crawler walks the open
    internet, downloading files the policy will then reject."""
    assert not fetch.wanted_asset("https://random-blog.example.com/x.pdf", KUVEYT)
    assert not fetch.wanted_asset("https://www.oecd.org/report.pdf", KUVEYT)


def test_a_pdf_on_a_named_authority_is_fetched():
    """The banks cite these, and the documents are worth having."""
    for url in ("https://www.tkbb.org.tr/standart.pdf",
                "https://spk.gov.tr/form.pdf",
                "https://borsaistanbul.com/files/endeks.pdf"):
        assert fetch.wanted_asset(url, KUVEYT)


def test_the_banks_own_subdomains_are_still_fetched():
    assert fetch.wanted_asset(
        "https://asset.emlakkatilim.com.tr/documents/ucretler.pdf", EMLAK)


def test_a_lookalike_of_a_trusted_host_is_not_fetched():
    assert not fetch.wanted_asset("https://nottkbb.org.tr/x.pdf", KUVEYT)


# ----- PDFs behind an unchanged page -----

def test_an_unchanged_page_still_yields_its_pdfs(serve):
    """A 304 page links the same PDFs it did yesterday, and its HTML is already
    in the store. Skipping it meant that on any site whose pages send an ETag,
    no PDF was ever looked at again after the first run."""
    html = (b'<html><a href="/documents/ucretler.pdf">Ucret Tarifesi</a></html>')
    content_hash, blob = store.put(html, "text/html")
    page = "https://www.kuveytturk.com.tr/kendim-icin"
    unchanged = fetch.RawDoc(url=page, fetched_at="now", status=304,
                             content_type="text/html", content_hash=content_hash,
                             blob=blob)
    serve({"ucretler.pdf": response(body=b"%PDF-1.4 x",
                                    content_type="application/pdf")})
    found = fetch._fetch_linked_pdfs(KUVEYT, {page: unchanged}, {}, cap=10)
    assert "https://www.kuveytturk.com.tr/documents/ucretler.pdf" in found
