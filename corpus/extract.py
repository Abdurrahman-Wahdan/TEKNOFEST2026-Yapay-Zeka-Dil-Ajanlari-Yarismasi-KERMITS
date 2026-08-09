"""HTML into a clean document with citable sections.

Citation is why this is more than a call to trafilatura. When the agent quotes a
campaign condition it should be able to link to the exact heading it came from,
the way a PDF page links with `#page=7`. That needs the heading's HTML `id`, and
trafilatura's markdown output discards `id` attributes -- so the raw HTML is
parsed a second time for headings and their anchors, and the two are aligned by
order and text.

Where a heading has no `id`, the citation falls back to the bare page URL. It
never invents a fragment: an invented anchor 404s silently and still looks like
a working citation, which is worse than not having one.
"""

import logging
import re

from . import clean
from .models import Section
from .quality import normalise, turkish_score
from .urls import text_hash

logger = logging.getLogger(__name__)

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _slug(text: str) -> str:
    """A comparison key for matching a markdown heading to an HTML one."""
    from banks.parse import fold
    return fold(text)[:60]


def html_headings(html: str) -> list[tuple[int, str, str]]:
    """`(level, text, anchor)` for every heading in the raw HTML.

    The anchor is the element's own `id`, or the `id`/`name` of an anchor tag
    immediately inside it -- older Turkish bank templates use both.
    """
    try:
        import lxml.html
    except ImportError as exc:  # pragma: no cover - declared in requirements
        raise RuntimeError("corpus needs lxml. Install with: pip install lxml") from exc

    try:
        tree = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001 - an unparseable page has no headings
        return []

    found = []
    for element in tree.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        text = " ".join((element.text_content() or "").split())
        if not text:
            continue
        anchor = element.get("id") or ""
        if not anchor:
            for child in element.iter("a"):
                anchor = child.get("id") or child.get("name") or ""
                if anchor:
                    break
        found.append((int(element.tag[1]), text, anchor))
    return found


def sections(markdown: str, html: str, url: str) -> tuple[Section, ...]:
    """Split the document at its headings, each citable on its own.

    Anchors come from the HTML; the text comes from the extracted markdown, so a
    section carries clean prose rather than markup.
    """
    anchors = {_slug(text): anchor for _, text, anchor in html_headings(html) if anchor}

    out: list[Section] = []
    path: list[str] = []
    current_heading, current_level, buffer = "", 0, []

    def flush() -> None:
        body = normalise("\n".join(buffer))
        if not body:
            return
        anchor = anchors.get(_slug(current_heading), "")
        out.append(Section(
            heading_path=" > ".join(path) or current_heading,
            anchor=anchor,
            level=current_level,
            text=body,
            order=len(out),
            # A fragment only when the page really has one. Never invented.
            cite_url=f"{url}#{anchor}" if anchor and url else url,
            text_hash=text_hash(body),
        ))

    for line in markdown.splitlines():
        match = _MD_HEADING.match(line)
        if match:
            flush()
            buffer = []
            current_level = len(match.group(1))
            current_heading = match.group(2).strip()
            path = path[:current_level - 1] + [current_heading]
        else:
            buffer.append(line)
    flush()
    return tuple(out)


def extract(html: str, url: str, site=None) -> dict:
    """Turn one fetched page into the parts a Document is built from.

    Returns a dict with `title`, `title_source`, `text`, `sections`, `lang` and
    `links` -- or an empty `text` when there is nothing worth keeping, which the
    caller reports rather than writing out.
    """
    try:
        import trafilatura
    except ImportError as exc:  # pragma: no cover - declared in requirements
        raise RuntimeError(
            "corpus needs trafilatura. Install with: pip install trafilatura"
        ) from exc

    markdown = trafilatura.extract(
        html, url=url, output_format="markdown",
        include_links=True, include_images=False,
        include_tables=True, include_formatting=True, favor_recall=True,
    ) or ""

    metadata = trafilatura.extract_metadata(html)
    raw_title = (getattr(metadata, "title", "") or "").strip()

    # No description is read. The crawled value is boilerplate -- 1,991 distinct
    # strings across 4,477 documents, the commonest repeated 679 times -- so
    # carrying it would only invite something downstream to embed it.

    body = clean.strip_link_targets(markdown)
    if site is not None and getattr(site, "boilerplate", ()):
        body = clean.strip_boilerplate(body, site.boilerplate)
    body = normalise(body)

    bank_names = (getattr(site, "display_name", ""),) if site is not None else ()
    title = clean.clean_title(raw_title, bank_names)
    title_source = "meta"
    if not title or clean.is_uninformative_title(title):
        from_slug = clean.title_from_slug(url)
        if from_slug:
            title, title_source = from_slug, "slug"

    return {
        "title": title,
        "title_source": title_source,
        "text": body,
        "sections": sections(body, html, url),
        "lang": "tr" if turkish_score(body) >= 0.3 else "en",
        "is_toc": clean.looks_like_toc(body),
    }
