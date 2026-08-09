"""One URL spelling per document, and a stable id derived from it.

Two of the crawler defects this replaces were identity bugs, not fetch bugs. The
old crawler mapped a URL to a file path and let the filesystem decide what was
the same document:

    /Sayfalar/sss.aspx?category=konut   ->  sss.md
    /Sayfalar/sss.aspx?category=kredi   ->  sss.md      # overwrote the first

44 pages of Türkiye Finans' FAQ collapsed onto one file that way, and
`https://host/p`, `https://host:443/p` and `http://host/p` produced three copies
of everything else. So identity is computed here, once, and every other module
takes it as given.

    from corpus.urls import canonicalise, doc_id

    canonicalise("HTTP://WWW.Host.com.tr:443/a/b/?b=2&a=1&utm_source=x#top")
    'https://www.host.com.tr/a/b?a=1&b=2'
"""

import hashlib
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

# Analytics junk. These change what the URL looks like without changing what the
# page says, so leaving them in would file one campaign under four ids depending
# on which newsletter linked it.
TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "msclkid", "yclid", "mc_cid", "mc_eid",
    "_ga", "ref", "referrer",
})

# Characters that stay literal in a path. Turkish paths arrive both percent-
# encoded and raw ("Finans%C3%B6r" and "Finansör" are the same document), so the
# path is decoded and re-encoded to one spelling rather than compared as-is.
_PATH_SAFE = "/-_.~!$&'()*+,;=:@"

_DEFAULT_PORTS = frozenset({80, 443})


def is_tracking_param(name: str) -> bool:
    """Whether a query parameter only identifies the referrer, not the content."""
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def canonicalise(url: str, host: str | None = None) -> str:
    """The one spelling of `url` that this project stores and compares.

    Normalises, in order: scheme to https, host to lowercase without a default
    port, path percent-encoding, trailing slash, tracking parameters out, the
    remaining query sorted, fragment dropped.

    The query is kept. That is the whole point — `?category=konut` and
    `?category=kredi` are different pages, and treating them as one is what lost
    ~300 Türkiye Finans documents.

    Args:
        url: Any absolute http(s) URL.
        host: Force this host instead of the one in the URL. Sites declare their
            own canonical host so `www.` folding is a per-site decision: nine of
            these banks use `www.` and Dünya Katılım does not, and folding it
            away globally would merge hosts that are genuinely distinct.

    Returns:
        The canonical URL, or "" if this is not an absolute http(s) URL.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""

    # https, always. Every one of these banks serves https, and the http spelling
    # of a page is the same page -- it just redirects.
    scheme = "https"

    netloc = (host or parts.hostname).lower().rstrip(".")
    # Either scheme's default port, dropped. Comparing against only the *input*
    # scheme's default would keep the port on "http://host:443/p", which is one
    # of the exact spellings that produced 150 duplicate copies of one page.
    if parts.port is not None and parts.port not in _DEFAULT_PORTS:
        netloc = f"{netloc}:{parts.port}"

    path = quote(unquote(parts.path), safe=_PATH_SAFE)
    # "/a/b/" and "/a/b" are one page; "/" is the homepage and keeps its slash.
    if len(path) > 1:
        path = path.rstrip("/")
    if not path:
        path = "/"

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not is_tracking_param(k)]
    query = urlencode(sorted(kept), quote_via=quote)

    return urlunsplit((scheme, netloc, path, query, ""))


def doc_id(url: str) -> str:
    """A stable, collision-free id for a canonical URL.

    Output files are named from this rather than from the URL path. That is what
    makes the old `index.md` / `INDEX.md` collision unrepresentable: on a
    case-insensitive filesystem those two names were one file, and a bank's
    homepage was overwritten by the crawler's own table of contents.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def text_hash(text: str) -> str:
    """The change key for cleaned text.

    Deliberately taken over cleaned text, not raw bytes. Bank pages carry
    rotating banners and CSRF tokens, so their bytes change nightly while the
    words do not; hashing bytes would re-embed most of the corpus every day for
    nothing.
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def same_site(url: str, root_domain: str) -> bool:
    """Whether `url` belongs to `root_domain` or a subdomain of it.

    Matches on a label boundary: "notkuveytturk.com.tr" is not Kuveyt Türk, but
    "asset.kuveytturk.com.tr" is.
    """
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname == root_domain or hostname.endswith("." + root_domain)


def is_pdf(url: str) -> bool:
    """Whether the URL path names a PDF.

    Content-Type decides in the end, but discovery has to choose what to fetch
    before it has one.
    """
    return urlsplit(url).path.lower().endswith(".pdf")
