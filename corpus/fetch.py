"""The one crawl engine, replacing ten byte-identical copies of the old one.

Three things it does that the scripts it replaces did not.

**Pages and assets are filtered by different rules.** The old `wanted()` demanded
a Turkish language prefix (`/tr`, `/tr-tr`) of *everything*, including PDFs. Bank
PDFs live under paths like `/documents/` and `/upload/dosyalar/`, which fail that
test, so **1,261 PDFs on the banks' own domains were discovered and then
discarded**. `wanted_page()` keeps the prefix rule; `wanted_asset()` does not.

**It asks the server what changed.** Conditional GET plus a content hash means an
unchanged page costs one 304 and no extraction, which is what makes a nightly run
affordable.

**It obeys robots.txt.** The old crawler read robots only to harvest sitemap
lines. For a job that runs every night forever, `Disallow` and `Crawl-delay` are
the difference between a pipeline that keeps working and an IP ban that looks
exactly like ten simultaneous outages.

    from corpus import fetch
    from corpus.sites import get_site

    raw = fetch.crawl(get_site("kuveytturk"), limit=20)
"""

import logging
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import httpx

from banks import clock
from config.settings import settings

from . import store
from .models import RawDoc, Site
from .urls import canonicalise, is_pdf, same_site

logger = logging.getLogger(__name__)

# Extensions that are never content. PDFs are deliberately absent: they are the
# one asset type this project treats as a document.
ASSET_EXTENSIONS = frozenset({
    ".css", ".js", ".mjs", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", ".avif", ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".mp3", ".wav", ".avi", ".mov", ".webm", ".zip", ".rar", ".gz",
    ".7z", ".exe", ".dmg", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
})

# Language prefixes that mark a page as not-Turkish. A path whose first segment
# is one of these is another language's copy of something we already have.
FOREIGN_PREFIXES = frozenset({
    "en", "en-us", "en-gb", "ar", "ar-sa", "de", "ru", "fr", "es", "it", "nl",
    "az", "kmr", "fa", "zh",
})

# Hosts other than the bank's own whose PDFs are worth following. All ten banks
# cite the same handful of authorities, so this is one shared list rather than a
# per-site field nobody would vary: the participation-banking association, the
# capital markets and banking regulators, the exchange, and the insurance union.
TRUSTED_PDF_HOSTS = frozenset({
    "tkbb.org.tr", "spk.gov.tr", "bddk.org.tr", "tsb.org.tr",
    "borsaistanbul.com", "tcmb.gov.tr", "resmigazete.gov.tr",
})

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9",
}

_CLIENT_LOCK = threading.Lock()
_CLIENT: httpx.Client | None = None
_ROBOTS: dict[str, tuple[urllib.robotparser.RobotFileParser, float]] = {}


# ----- transport -----

def get_client() -> httpx.Client:
    """The shared HTTP client.

    One client for the whole run: creating one per request leaks sockets and
    pays a TLS handshake every time. Same idiom as `banks/http.py`.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = httpx.Client(
                headers={**_HEADERS, "User-Agent": settings.CORPUS_USER_AGENT},
                timeout=settings.CORPUS_TIMEOUT,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=settings.CORPUS_CONCURRENCY * 2),
            )
    return _CLIENT


def _get(client: httpx.Client, url: str, headers: dict | None = None) -> httpx.Response:
    """The single place this package touches the network.

    Every fetch goes through here so tests can replace one function instead of
    mocking a transport -- the seam `tests/unit/test_health.py` uses for banks.
    """
    return client.get(url, headers=headers or {})


def clear_cache() -> None:
    """Close the shared client and forget parsed robots files."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            try:
                _CLIENT.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("Ignoring error closing the corpus client", exc_info=True)
        _CLIENT = None
        _ROBOTS.clear()


# ----- what to fetch -----

def is_asset(url: str) -> bool:
    """Whether the path names a file that is never a document."""
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    return dot != -1 and path[dot:] in ASSET_EXTENSIONS


def is_turkish_path(url: str, site: Site) -> bool:
    """Whether this path is the Turkish copy of a page.

    Two rules: a first segment naming another language disqualifies it, and
    where the site declares Turkish prefixes the path must start with one.
    """
    path = urlsplit(url).path
    segments = [s for s in path.split("/") if s]
    if segments and segments[0].lower() in FOREIGN_PREFIXES:
        return False
    if site.include_prefixes and path not in ("", "/"):
        return any(path.lower().startswith(p) for p in site.include_prefixes)
    return True


def wanted_page(url: str, site: Site) -> bool:
    """Whether to crawl this URL as an HTML page."""
    if not url.startswith("https://") or not same_site(url, site.root_domain):
        return False
    if is_pdf(url) or is_asset(url):
        return False
    return is_turkish_path(url, site)


def wanted_asset(url: str, site: Site) -> bool:
    """Whether to fetch this URL as a PDF.

    Deliberately does **not** apply `is_turkish_path`. That is the whole fix:
    the old crawler tested PDFs against the page language prefix, so every PDF
    under `/documents/` or `/upload/` at a site declaring `/tr` was thrown away.

    It *does* check the host. The banks link PDFs on regulators' and the
    association's sites, and those are worth having -- but following a PDF link
    to any host at all means the crawler walks the open internet, downloading
    files the selection policy will then reject. So the surface is bounded here,
    to the bank's own domain plus a named list of Turkish financial authorities,
    and the policy still decides whether an allowed file is worth reading.
    """
    if not url.startswith("https://") or not is_pdf(url):
        return False
    if same_site(url, site.root_domain):
        return True
    host = (urlsplit(url).hostname or "").lower()
    return any(host == trusted or host.endswith("." + trusted)
               for trusted in TRUSTED_PDF_HOSTS)


# ----- robots -----

def robots(site: Site) -> tuple[urllib.robotparser.RobotFileParser, float]:
    """This site's robots.txt and its crawl delay, fetched once per run.

    An unreachable or malformed robots.txt is treated as "allow everything",
    which is what every crawler does and what the standard says. It is not
    treated as a reason to skip the site.
    """
    cached = _ROBOTS.get(site.slug)
    if cached is not None:
        return cached

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"https://{site.host}/robots.txt")
    delay = settings.CORPUS_DELAY
    try:
        response = _get(get_client(), f"https://{site.host}/robots.txt")
        if response.status_code == 200:
            parser.parse(response.text.splitlines())
            declared = parser.crawl_delay(settings.CORPUS_USER_AGENT)
            if declared:
                # Only ever slower than our own setting; a site asking us to go
                # faster does not mean we should.
                delay = max(delay, float(declared))
        else:
            parser.allow_all = True
    except Exception as exc:  # noqa: BLE001 - no robots.txt is not an outage
        logger.debug("No usable robots.txt for %s: %s", site.slug, exc)
        parser.allow_all = True

    _ROBOTS[site.slug] = (parser, delay)
    return parser, delay


def allowed(url: str, site: Site) -> bool:
    """Whether robots.txt lets us fetch this URL."""
    parser, _ = robots(site)
    try:
        return parser.can_fetch(settings.CORPUS_USER_AGENT, url)
    except Exception:  # noqa: BLE001 - a malformed rule must not stop the crawl
        return True


# ----- discovery -----

def sitemap_urls(site: Site) -> set[str]:
    """Every page URL this site's sitemaps declare.

    Nested sitemap indexes are followed. Anything that is not this site's is
    ignored: a sitemap listing a partner's domain must not widen the crawl.
    """
    client = get_client()
    queue = list(site.sitemaps)

    # robots.txt is where several of these banks actually declare their sitemap.
    parser, _ = robots(site)
    try:
        queue.extend(parser.site_maps() or [])
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    found: set[str] = set()
    while queue:
        sitemap = queue.pop()
        if sitemap in seen:
            continue
        seen.add(sitemap)
        try:
            response = _get(client, sitemap)
            if response.status_code != 200:
                continue
            body = response.text
        except Exception as exc:  # noqa: BLE001 - one bad sitemap is not fatal
            logger.debug("Sitemap %s unreadable: %s", sitemap, exc)
            continue

        for location in _locations(body):
            if not same_site(location, site.root_domain):
                continue
            path = urlsplit(location).path.lower()
            if path.endswith(".xml") or path.endswith(".xml.gz"):
                if location not in seen:
                    queue.append(location)
            else:
                found.add(canonicalise(location, host=site.host))
    return {u for u in found if u}


def _locations(xml: str) -> list[str]:
    """The <loc> values in a sitemap, without a full XML parse.

    These files are occasionally malformed enough that a strict parser refuses
    them outright, and a sitemap is a list of strings -- there is nothing here
    worth failing a whole site over.
    """
    import re

    return [m.strip() for m in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.I | re.S)]


def links(html: str, base_url: str) -> list[tuple[str, str]]:
    """`(absolute_url, anchor_text)` for every link in the page.

    Anchor text is carried because for a PDF it is the best title available: only
    a third of these files declare one internally, and the link text is always
    human-written Turkish describing what the document is.
    """
    try:
        import lxml.html
    except ImportError as exc:  # pragma: no cover - declared in requirements
        raise RuntimeError(
            "corpus needs lxml. Install with: pip install lxml"
        ) from exc

    try:
        tree = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001 - an unparseable page yields no links
        return []

    out: list[tuple[str, str]] = []
    for element in tree.iter("a"):
        href = (element.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        out.append((urljoin(base_url, href), " ".join((element.text_content() or "").split())))
    return out


# ----- fetching -----

def fetch_one(url: str, previous: dict | None = None) -> RawDoc:
    """Fetch one URL, storing its bytes, and return what happened.

    A 304 returns a RawDoc carrying the previous content hash and blob, so the
    caller can reuse the document without extracting anything.
    """
    client = get_client()
    headers = {}
    if previous:
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]

    now = clock.stamp()
    try:
        response = _get(client, url, headers)
    except Exception as exc:  # noqa: BLE001 - a dead host is data, not a crash
        return RawDoc(url=url, fetched_at=now, status=0, content_type="",
                      content_hash="", blob="", error=f"{type(exc).__name__}: {exc}")

    content_type = response.headers.get("content-type", "")

    if response.status_code == 304 and previous:
        return RawDoc(
            url=url, fetched_at=now, status=304,
            content_type=previous.get("content_type", ""),
            content_hash=previous.get("content_hash", ""),
            blob=previous.get("blob", ""),
            etag=previous.get("etag", ""),
            last_modified=previous.get("last_modified", ""),
            size=previous.get("size", 0),
        )

    if response.status_code != 200:
        return RawDoc(url=url, fetched_at=now, status=response.status_code,
                      content_type=content_type, content_hash="", blob="",
                      error=f"HTTP {response.status_code}")

    body = response.content
    limit = settings.CORPUS_MAX_PDF_MB * 1_000_000
    if is_pdf(url) and len(body) > limit:
        # Refused and reported, never silently skipped: a document we chose not
        # to read is a different thing from one that was not there.
        return RawDoc(url=url, fetched_at=now, status=200, content_type=content_type,
                      content_hash="", blob="", size=len(body),
                      error=f"PDF is {len(body) // 1_000_000} MB, over the "
                            f"{settings.CORPUS_MAX_PDF_MB} MB limit")

    content_hash, blob = store.put(body, content_type)
    return RawDoc(
        url=url, fetched_at=now, status=200, content_type=content_type,
        content_hash=content_hash, blob=blob,
        etag=response.headers.get("etag", ""),
        last_modified=response.headers.get("last-modified", ""),
        size=len(body),
    )


def crawl(site: Site, limit: int | None = None,
          manifest: dict | None = None) -> dict[str, RawDoc]:
    """Fetch a whole site and return `{canonical_url: RawDoc}`.

    Sitemap mode where the site publishes one, recursive otherwise, and
    recursive as a fallback when a declared sitemap turns out to be empty --
    Türkiye Finans and TOM have no usable sitemap at all.
    """
    manifest = manifest if manifest is not None else store.read_manifest()
    cap = limit or settings.CORPUS_MAX_PAGES_PER_SITE

    seeds: set[str] = set()
    if site.mode in ("sitemap", "auto"):
        seeds = sitemap_urls(site)
        logger.info("%s: %d URLs from sitemaps", site.slug, len(seeds))

    if seeds:
        pages = sorted(u for u in seeds if wanted_page(u, site))[:cap]
        results = _fetch_many(site, pages, manifest)
        # Sitemaps list pages, not attachments, so the PDFs they link are only
        # discoverable from the pages themselves.
        results.update(_fetch_linked_pdfs(site, results, manifest, cap))
        return results

    logger.info("%s: no sitemap, crawling recursively", site.slug)
    return _crawl_recursive(site, manifest, cap)


def _fetch_many(site: Site, urls: list[str], manifest: dict) -> dict[str, RawDoc]:
    """Fetch a known list of URLs, politely and in parallel."""
    _, delay = robots(site)
    results: dict[str, RawDoc] = {}
    lock = threading.Lock()

    def work(url: str) -> None:
        if not allowed(url, site):
            logger.debug("robots.txt disallows %s", url)
            return
        record = fetch_one(url, manifest.get(url))
        with lock:
            results[url] = record
        time.sleep(delay)

    with ThreadPoolExecutor(max_workers=settings.CORPUS_CONCURRENCY) as pool:
        list(pool.map(work, urls))
    return results


def _fetch_linked_pdfs(site: Site, fetched: dict[str, RawDoc], manifest: dict,
                       cap: int) -> dict[str, RawDoc]:
    """Fetch the PDFs the fetched pages link to.

    Runs after the pages so every PDF knows which page linked it — the selection
    policy decides from that context, and the anchor text becomes its title.
    """
    targets: dict[str, str] = {}
    for page_url, record in fetched.items():
        # 304 counts. A page that has not changed still links the same PDFs, and
        # its HTML is already in the store -- so skipping it here meant that on
        # any site whose pages send an ETag, no PDF was ever looked at again
        # after the first run. Emlak hid this because its page CDN sends none.
        if record.status not in (200, 304) or not record.blob:
            continue
        if "html" not in record.content_type:
            continue
        try:
            html = store.get(record.blob).decode("utf-8", errors="replace")
        except OSError:
            continue
        for href, _ in links(html, page_url):
            if not is_pdf(href):
                continue
            canonical = canonicalise(href)
            if canonical and canonical not in fetched and canonical not in targets:
                if wanted_asset(canonical, site):
                    targets[canonical] = page_url

    ordered = sorted(targets)[:cap]
    logger.info("%s: %d linked PDFs", site.slug, len(ordered))
    return _fetch_many(site, ordered, manifest)


def _crawl_recursive(site: Site, manifest: dict, cap: int) -> dict[str, RawDoc]:
    """Breadth-first crawl for the sites that publish no usable sitemap."""
    _, delay = robots(site)
    start = [canonicalise(site.base, host=site.host)]
    start += [canonicalise(s, host=site.host) for s in site.extra_seeds]

    seen: set[str] = {u for u in start if u}
    frontier: list[str] = sorted(seen)
    results: dict[str, RawDoc] = {}
    pdfs: dict[str, str] = {}
    lock = threading.Lock()

    while frontier and len(results) < cap:
        batch, frontier = frontier[:cap - len(results)], frontier[cap - len(results):]
        discovered: set[str] = set()

        def work(url: str) -> None:
            if not allowed(url, site):
                return
            record = fetch_one(url, manifest.get(url))
            with lock:
                results[url] = record
            time.sleep(delay)
            # 304 as well as 200: an unchanged page still has links to follow,
            # and its HTML is in the store.
            if record.status not in (200, 304) or not record.blob:
                return
            if "html" not in record.content_type:
                return
            try:
                html = store.get(record.blob).decode("utf-8", errors="replace")
            except OSError:
                return
            for href, _ in links(html, url):
                canonical = canonicalise(href, host=site.host if
                                         same_site(href, site.root_domain) else None)
                if not canonical:
                    continue
                with lock:
                    if is_pdf(canonical):
                        if wanted_asset(canonical, site) and canonical not in pdfs:
                            pdfs[canonical] = url
                    elif canonical not in seen and wanted_page(canonical, site):
                        seen.add(canonical)
                        discovered.add(canonical)

        with ThreadPoolExecutor(max_workers=settings.CORPUS_CONCURRENCY) as pool:
            list(pool.map(work, batch))
        frontier.extend(sorted(discovered))

    ordered = sorted(u for u in pdfs if u not in results)[:cap]
    logger.info("%s: %d pages, %d linked PDFs", site.slug, len(results), len(ordered))
    results.update(_fetch_many(site, ordered, manifest))
    return results
