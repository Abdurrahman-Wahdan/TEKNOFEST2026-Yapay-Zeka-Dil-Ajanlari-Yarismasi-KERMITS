"""The shapes this pipeline stores, from a fetched byte-string to a clean document.

Frozen dataclasses, like `banks/models.py`, and for the same reason: these are
records, not objects with behaviour. `raw` is kept on the types that come from
outside so a field we did not model stays reachable without a code change.

The phase boundary is `Document`. It carries clean text, the metadata needed to
filter it, and the hashes needed to re-embed only what changed — but nothing
about chunks, vectors or Qdrant payloads. Those belong to the next phase.
"""

from dataclasses import dataclass, field

# What a document is, decided from its URL path. No LLM: the banks already sort
# their own content into /kampanyalar, /kendim-icin, /blog and so on.
DOC_KINDS = (
    "campaign", "product", "fees", "rates", "faq",
    "blog", "corporate", "legal", "branch", "other",
)

# How a PDF's text was obtained. Recorded per document because it changes how
# much the agent should trust a number it reads: `pdftotext` is the file's own
# text layer, `ocr` is a model reading a picture of it.
EXTRACTION_ENGINES = ("html", "pdftotext", "pdftotext+vision", "ocr")


@dataclass(frozen=True)
class Site:
    """One bank's web presence, as data.

    The old crawler kept this as a CONFIG literal at the top of ten byte-identical
    410-line files, so every fix had to be made ten times. Sites carry no
    behaviour — only these values differ between banks — so they are a registry
    of records rather than a provider package.
    """

    slug: str                      # "kuveytturk"; must name a registered bank
    display_name: str              # "Kuveyt Türk"
    base: str
    root_domain: str
    host: str                      # canonical host: nine use "www.", Dünya does not
    mode: str = "auto"             # "sitemap" | "recursive" | "auto"
    sitemaps: tuple[str, ...] = ()

    # Language prefixes that mark Turkish *pages*. Never applied to assets: the
    # old crawler tested PDFs against these too, so 1,261 PDFs under paths like
    # /documents/*.pdf failed the /tr test and were never fetched.
    include_prefixes: tuple[str, ...] = ()

    extra_seeds: tuple[str, ...] = ()

    # Whole blocks known to repeat across this site's pages. Dünya Katılım's
    # 6,928-char cookie notice appears in 190 of its 272 documents -- 63.8% of
    # everything that bank publishes.
    boilerplate: tuple[str, ...] = ()

    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RawDoc:
    """What came off the wire, before anything judged it."""

    url: str                       # canonical
    fetched_at: str                # ISO 8601
    status: int
    content_type: str
    content_hash: str              # sha256 of the bytes; also the blob's name
    blob: str                      # path relative to the raw store
    etag: str = ""
    last_modified: str = ""
    size: int = 0
    error: str = ""

    # Set when discovery stops finding a URL, or it starts 404ing. A document is
    # not dropped until it has been missing for several consecutive runs -- the
    # rule `banks/audit.py` uses, so one WAF blip cannot delete a whole site.
    missing_since: str = ""


@dataclass(frozen=True)
class Block:
    """One piece of a PDF page, in reading order."""

    kind: str                      # heading|paragraph|table|list|image|figure_caption
    text: str                      # markdown; tables as markdown tables
    order: int
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class Page:
    """One page of a PDF."""

    number: int                    # 1-based, matching what a PDF viewer shows
    blocks: tuple[Block, ...]
    cite_url: str                  # ".../form.pdf#page=7" -- viewers honour it
    text_hash: str
    has_tables: bool = False
    has_images: bool = False

    # True where the page had no text layer and a model read the image instead.
    # Vision paraphrases plausibly, so a citation from such a page should hedge.
    from_vision: bool = False


@dataclass(frozen=True)
class Section:
    """One heading-delimited part of an HTML page.

    The same idea as `Page`, one level up, and deliberately sharing `cite_url`
    and `text_hash` so pages and PDFs cite and re-embed through one code path.
    """

    heading_path: str              # "Kampanya Koşulları > Katılım Şartları"
    anchor: str                    # the heading's HTML id, "" when it has none
    level: int
    text: str
    order: int
    cite_url: str
    text_hash: str


@dataclass(frozen=True)
class Document:
    """One clean, standardised document. The deliverable of this phase."""

    # ----- identity -----
    doc_id: str
    url: str
    # Every spelling that produced this body. The corpus had 159 groups of
    # byte-identical documents under different URLs; they collapse to one
    # document that remembers where else it lived.
    source_urls: tuple[str, ...]

    # ----- provenance -----
    site: str
    bank: str
    source_type: str               # "page" | "pdf"
    fetched_at: str
    content_hash: str              # of the raw bytes
    text_hash: str                 # of the cleaned text -- the semantic key
    blob: str

    # ----- taxonomy, from the URL path -----
    doc_kind: str
    section: str
    audience: str                  # bireysel|ticari|kobi|ozel|""
    category: str

    # ----- content -----
    title: str
    title_source: str              # "meta" | "slug" | "filename"
    text: str
    lang: str
    chars: int

    # No `description` field, on purpose. The crawled value is boilerplate --
    # 1,991 distinct strings across 4,477 documents, the commonest repeated 679
    # times -- so embedding it would push identical filler into thousands of
    # vectors. Leaving it out of the schema makes that mistake impossible.

    sections: tuple[Section, ...] = ()
    pages: tuple[Page, ...] = ()

    # ----- campaigns -----
    # No `is_active` flag. It is `campaign_end >= today`, and today moves without
    # the document changing, so storing it would make yesterday's artifact lie by
    # tomorrow. Callers compute it at query time.
    campaign_start: str = ""
    campaign_end: str = ""
    date_source: str = ""          # "label" | "range" | "prose" | ""

    # ----- PDFs -----
    extraction_engine: str = "html"
    page_count: int = 0
    anchor_text: str = ""          # the link text the bank wrote for this PDF
    classified_by: str = ""        # "rule:<name>" | "model"
    class_reason: str = ""         # why, in one line, so a wrong call is auditable

    # ----- quality -----
    low_confidence: bool = False
    extraction_suspect: bool = False
    duplicate_of: str = ""
    attachments: tuple[str, ...] = ()   # doc_ids of PDFs this page links

    raw: dict = field(default_factory=dict)
