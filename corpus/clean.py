"""One pure function per defect measured in the crawled corpus.

Each of these exists because something specific was wrong with 7,105 real
documents, and each says which. Nothing here is speculative tidying.
"""

import re

from banks.parse import fold

# A markdown link, captured so the anchor text can be kept and the target
# dropped. Measured: link syntax is 7.1% of campaign text, of which 5.7% is the
# URL inside the parentheses -- tokens with no meaning that dilute the vector.
_LINK = re.compile(r"\[([^\]]*)\]\((?:[^)]*)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\((?:[^)]*)\)")

# Titles the sites hand out to hundreds of pages at once. Measured: 220 pages
# titled "Dünya Katılım", 161 "Fotoğraf Galerisi", 117 "Duyuru Detay",
# 116 "Detay", 89 "Blog Detay". A title that names no document is worse than
# none, because it looks like one.
# Both the "Detay" template slots and the bare section words. Measured on a live
# Emlak build: 10 of 56 documents were titled just "Kampanya", because that bank
# titles every campaign page "Kampanya | Türkiye Emlak Katılım Bankası" and the
# suffix strip leaves the bare word. The URL slug names the actual campaign, so
# these fall back to it.
_UNINFORMATIVE = frozenset({
    "detay", "duyurudetay", "blogdetay", "haberdetay", "kampanyadetay",
    "fotografgalerisi", "videogalerisi", "galeri", "anasayfa", "sayfabulunamadi",
    "kampanya", "kampanyalar", "duyuru", "duyurular", "haber", "haberler",
    "blog", "urun", "urunler", "sayfa",
})

# The crawler's own table of contents, which on a case-insensitive filesystem
# overwrote seven banks' homepages. Never index it, whatever else happens.
_TOC_MARKER = "— Site İçeriği"


def strip_link_targets(text: str) -> str:
    """Keep what a link says, drop where it points."""
    text = _IMAGE.sub(r"\1", text)
    return _LINK.sub(r"\1", text)


def strip_boilerplate(text: str, fingerprints: tuple[str, ...]) -> str:
    """Remove blocks the site pastes into most of its pages.

    Only Dünya Katılım needs this so far, and it needs it badly: a 6,928-char
    KVKK notice sits in 190 of its 272 documents, 63.8% of everything that bank
    publishes. Matched on a prefix fingerprint so a reworded notice still goes.
    """
    if not fingerprints:
        return text
    kept, skipping = [], False
    for paragraph in text.split("\n\n"):
        head = paragraph.strip()[:120]
        if any(mark[:60] in head for mark in fingerprints):
            skipping = True
            continue
        # A fingerprinted block runs until the next heading.
        if skipping and not paragraph.lstrip().startswith("#"):
            continue
        skipping = False
        kept.append(paragraph)
    return "\n\n".join(kept)


def clean_title(title: str, bank_names: tuple[str, ...] = ()) -> str:
    """Strip the site-wide suffix, keeping what names the document.

    2,298 Kuveyt Türk pages end in "| Kuveyt Türk Katılım Bankası" and 323 Emlak
    pages in "| Türkiye Emlak Katılım Bankası". Repeating the bank in every
    chunk adds nothing -- the payload already carries it.
    """
    cleaned = (title or "").strip()
    for separator in ("|", "/", "—", "–"):
        if separator in cleaned:
            head, _, tail = cleaned.rpartition(separator)
            if head.strip() and (not bank_names or any(
                    fold(name) and fold(name) in fold(tail) for name in bank_names)):
                cleaned = head.strip()
    return cleaned.strip(" -|/—–")


def is_uninformative_title(title: str) -> bool:
    """Whether a title names a document or just a template slot."""
    return fold(title or "") in _UNINFORMATIVE


def title_from_slug(url: str) -> str:
    """A readable title from the last path segment.

    Used for the ~1,100 documents whose own title is "Detay". The slug is what
    the bank actually called the page.
    """
    from urllib.parse import unquote, urlsplit

    parts = [p for p in urlsplit(url).path.split("/") if p]
    if not parts:
        return ""
    slug = unquote(parts[-1])
    slug = re.sub(r"\.(aspx|html?|php)$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug[:1].upper() + slug[1:] if slug else ""


def looks_like_toc(text: str) -> bool:
    """Whether this is the old crawler's index rather than a document.

    Belt and braces: the new store cannot produce one, but a stray file from the
    old corpus must never reach an embedder as if it were a bank page.
    """
    head = text[:400]
    if _TOC_MARKER in head:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:40]
    if len(lines) < 20:
        return False
    linkish = sum(1 for ln in lines if ln.startswith("- [") or ln.startswith("* ["))
    return linkish / len(lines) > 0.8


def is_stub(text: str, minimum: int = 250) -> bool:
    """Whether there is too little here to be worth a vector.

    262 Emlak and 155 Ziraat documents are navigation stubs under 300 chars.
    """
    return len(text.strip()) < minimum
