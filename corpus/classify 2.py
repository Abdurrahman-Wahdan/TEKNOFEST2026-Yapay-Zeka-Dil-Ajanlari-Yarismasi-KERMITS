"""What a document is, decided from its URL.

The banks already sort their own content, and the path says so: /kampanyalar,
/kendim-icin, /bireysel, /blog. Measured over the crawled corpus the depth-1
segments are /tr (1,974), /tr-tr (1,505), /blog (715), /hakkimizda (632),
/kampanyalar (485), /kendim-icin (291), /isim-icin (241), /ozel-bankacilik (192).

So this needs no model, and using one would be slower, less repeatable, and no
more accurate than reading the shelf a bank filed something on.
"""

from urllib.parse import urlsplit

from .pdf_policy import LANGUAGE_SEGMENTS

# Section -> what kind of document lives there.
_KIND_BY_SECTION = {
    "kampanyalar": "campaign",
    "kampanya": "campaign",
    "firsatlar": "campaign",
    "blog": "blog",
    "finansal-kilavuz": "blog",
    "hakkimizda": "corporate",
    "yatirimci-iliskileri": "corporate",
    "basin-odasi": "corporate",
    "kariyer": "corporate",
    "surdurulebilirlik": "corporate",
    "kvkk": "legal",
    "bilgi-toplumu-hizmetleri": "legal",
    "sozlesmeler-ve-formlar": "legal",
    "subeler": "branch",
    "sube": "branch",
    "atm": "branch",
    "iletisim": "corporate",
    "sikca-sorulan-sorular": "faq",
    "yardim": "faq",
}

# Who a section is addressed to. Absent means the document is for everyone.
_AUDIENCE_BY_SECTION = {
    "bireysel": "bireysel",
    "kendim-icin": "bireysel",
    "ticari": "ticari",
    "isim-icin": "ticari",
    "kurumsal": "ticari",
    "kurumsal-bankacilik": "ticari",
    "kobi": "kobi",
    "ozel-bankacilik": "ozel",
    "private": "ozel",
}

# Words in a path that mark a page as being about a product the bank sells.
_PRODUCT_HINTS = (
    "finansman", "kredi", "kart", "hesap", "sigorta", "yatirim", "leasing",
    "pos", "havale", "eft", "doviz", "altin", "sukuk", "emeklilik", "urun",
)


def segments(url: str) -> list[str]:
    """Path segments with any language marker removed."""
    parts = [s for s in urlsplit(url).path.split("/") if s]
    if parts and parts[0].lower() in LANGUAGE_SEGMENTS:
        parts = parts[1:]
    return [p.lower() for p in parts]


def section_of(url: str) -> str:
    parts = segments(url)
    return parts[0] if parts else ""


def category_of(url: str) -> str:
    """The second segment: /kampanyalar/kart-kampanyalari -> kart-kampanyalari."""
    parts = segments(url)
    return parts[1] if len(parts) > 1 else ""


def audience_of(url: str) -> str:
    """Who the document is for, from any segment that names an audience."""
    for part in segments(url):
        if part in _AUDIENCE_BY_SECTION:
            return _AUDIENCE_BY_SECTION[part]
    return ""


def doc_kind(url: str, pdf_label: str = "") -> str:
    """What kind of document this is.

    Args:
        pdf_label: For PDFs, the label the selection policy or the model
            settled on. It wins, because it was decided from the document
            itself rather than from where a link to it happened to sit.
    """
    if pdf_label in ("campaign", "product", "fees", "rates", "faq"):
        return pdf_label

    parts = segments(url)
    for part in parts:
        if part in _KIND_BY_SECTION:
            return _KIND_BY_SECTION[part]

    joined = "/".join(parts)
    if any(hint in joined for hint in _PRODUCT_HINTS):
        return "product"
    return "other"


def refresh_days(kind: str) -> int:
    """How often this kind of document is worth re-checking.

    Ten sites and roughly 8,500 documents fetched every night from one address
    is how a crawler gets banned, and a ban looks exactly like ten simultaneous
    outages. Campaigns expire, so they are checked daily; a contract PDF has not
    changed in years.
    """
    if kind in ("campaign", "faq"):
        return 1
    if kind in ("product", "fees", "rates"):
        return 7
    if kind in ("blog", "corporate", "branch"):
        return 7
    return 30
