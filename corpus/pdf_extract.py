"""Turning a PDF into pages of ordered blocks, with citable page numbers.

**One standard, no exceptions: every page is an image, and the model reads the
image.** Text, tables, figures, logos, placement -- all of it comes out of the
picture of the page, whether or not the file happens to carry a text layer.
Nothing is ever read from `pdftotext`, and there is no fallback to it.

That uniformity is the point. A pipeline that reads some pages by OCR and
others from an embedded text layer produces two kinds of document that look
alike downstream: same shape, same fields, different provenance and different
failure modes. Every page here is `from_vision`, so a citation means the same
thing everywhere.

The consequence is that a page which fails is never quietly patched over. A
page counts as empty only when the model **succeeded** and reported no text;
a page whose request failed is retried, and a document still holding a failed
page is not written at all, so the next run redoes it.

    from corpus.pdf_extract import extract

    result = extract(path, "https://bank.com.tr/documents/ucretler.pdf")
    result.pages[0].cite_url        # ".../ucretler.pdf#page=1"
"""

import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from pydantic import Field as PydanticField

from config.settings import settings

from . import pdftools, quality
from .models import Item, Page
from .urls import text_hash

logger = logging.getLogger(__name__)

# What a block may be. Closed, because these become chunk boundaries later and
# an open vocabulary would make that ungovernable.
BLOCK_KINDS = ("heading", "paragraph", "table", "list", "image", "figure_caption")

EXTRACT_PROMPT = """You transcribe a single document page into structured JSON.

Return:
- markdown: the full page transcribed in clean markdown, preserving reading
  order, headings, lists, and tables. Transcribe exactly what is on the page;
  do not summarize, translate, invent, or omit visible content.
- items: ONLY the non-text or visually rich elements (tables, charts, images,
  diagrams) that need more than plain text. For each, place an inline marker
  like <table_1> or <figure_1> at the right spot in the markdown, and add a
  matching item with: id (e.g. table_1), marker (the exact <...> text),
  summary (a summary of what the item visually represents), visible_text
  (exact text inside it), and visual_representation (layout/meaning not
  captured by the summary).

Rules:
- Plain prose, headings, and simple lists belong in markdown only, not items.
- Every item.marker must appear verbatim in the markdown.
- If the page has no rich elements, items is an empty list.
"""


class _ItemOut(BaseModel):
    id: str = PydanticField(description="e.g. table_1, figure_1")
    marker: str = PydanticField(description="The exact <...> marker text.")
    summary: str = PydanticField(description="What the item visually represents.")
    visible_text: str = PydanticField(description="Exact text inside the item.")
    visual_representation: str = PydanticField(
        description="Layout/meaning not captured by the summary.")


class _PageOut(BaseModel):
    markdown: str = PydanticField(
        description="The full page in clean markdown, reading order preserved.")
    items: list[_ItemOut] = PydanticField(
        default_factory=list,
        description="Only tables, charts, images and diagrams. Empty if none.")


@dataclass(frozen=True)
class Extraction:
    """What came out of one PDF."""

    pages: tuple[Page, ...]
    text: str
    engine: str
    page_count: int             # the real count, even when truncated
    truncated: bool = False
    low_confidence: bool = False
    suspect: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.pages) and not self.error and not self.suspect


def cite_url(url: str, page: int) -> str:
    """A link that opens the PDF at this page. Viewers honour `#page=`."""
    return f"{url}#page={page}" if url else ""


class TransientExtractionError(Exception):
    """A page could not be read because the model or the network failed.

    Distinct from a page the model read and found empty. This one says nothing
    about the document, so the caller must not persist a verdict from it -- the
    PDF has to be tried again.
    """


def _too_large(exc: Exception) -> bool:
    """Whether a request failed because the image was too big to accept."""
    text = str(exc)
    return "413" in text or "Too Large" in text or "too large" in text


def _read_page(pdf: Path, number: int, llm) -> tuple[str, list]:
    """One page, one image, one request.

    The whole page goes up intact. There is no tiling: cutting a page into
    strips split tables across the seams and left rows without the column
    header that gave them meaning -- a sector code kept its sector and lost its
    description, which reads as complete data and is not.

    Returns the page markdown and its rich items.
    """
    image = pdftools.render(pdf, number, dpi=settings.CORPUS_PDF_DPI,
                            scale_to=settings.CORPUS_PDF_SCALE_TO)
    result = llm.invoke([HumanMessage(content=[
        {"type": "text", "text": EXTRACT_PROMPT},
        {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}},
    ])])
    if result is None:
        # No parseable output. The request failed; the page is not blank.
        raise TransientExtractionError(
            f"page {number}: the model returned no structured output")
    return result.markdown or "", list(result.items or ())


def _read_page_retrying(pdf: Path, number: int, llm) -> tuple[str, list]:
    """One page, retried through a transient failure.

    The tunnel in front of the model drops requests in bursts -- one measured
    run lost 12 of its first 50 pages to gateway errors and recovered on its
    own. Without this, each of those was a page silently missing from a
    document that still looked complete. Raises TransientExtractionError when
    every attempt fails, so the caller can refuse the whole PDF rather than
    write one with a hole in it.
    """
    last = ""
    attempt = 0
    delay = settings.CORPUS_PDF_RETRY_BACKOFF
    start = time.time()
    last_warn = 0.0
    while True:
        attempt += 1
        try:
            return _read_page(pdf, number, llm)
        except Exception as exc:  # noqa: BLE001 - any failure is worth retrying
            # A permanent client error (4xx) never heals, so it is the one case
            # that still refuses the PDF rather than retrying forever.
            if any(c in str(exc) for c in ("400", "401", "403", "404", "BadRequest")):
                raise TransientExtractionError(
                    f"page {number}: permanent {type(exc).__name__}: {exc}") from exc
            last = f"{type(exc).__name__}: {exc}"
            elapsed = time.time() - start
            # Repo-wide rule (dataprep/vlm.py::_post, crawl/policy.py,
            # compare/*): never give up on a transient failure. The old
            # four-attempt cap turned a tunnel outage lasting longer than ~14
            # seconds into a refused PDF, which the caller then had to re-run
            # from scratch -- the retry is far cheaper than the re-run.
            if elapsed - last_warn >= 300:
                logger.warning("[PDF_UZUN_SURELI_HATA] page %d of %s failing for %.0fs "
                               "(attempt %d): %s -- still retrying, not giving up",
                               number, pdf.name[:40], elapsed, attempt, last[:120])
                last_warn = elapsed
            else:
                logger.warning("page %d of %s attempt %d failed: %s -- retrying in %.0fs",
                               number, pdf.name[:40], attempt, last[:120], delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)


def extract(pdf: Path | str, url: str = "", model: str | None = None) -> Extraction:
    """Read a PDF into citable pages, every page from its image.

    The text layer is never consulted -- not for content, not as a hint, not as
    a fallback. `pdftotext` is used only to count pages.

    Returns an Extraction. Failure is always reported, never returned as a
    short document that looks fine.
    """
    # Callers pass a str as often as a Path -- the corpus build and the live
    # tests both do -- and the progress line reads `pdf.name`, so a str got all
    # the way to the per-page loop before failing with AttributeError. Coerced
    # once here rather than guarded at each use.
    pdf = Path(pdf)
    try:
        total = pdftools.page_count(pdf)
    except pdftools.PdfToolError as exc:
        return Extraction((), "", "ocr", 0, error=str(exc))
    if not total:
        return Extraction((), "", "ocr", 0, error="no pages")

    limit = settings.CORPUS_PDF_MAX_PAGES
    truncated = total > limit
    numbers = list(range(1, min(total, limit) + 1))

    from llm import get_llm

    try:
        # A ceiling high above any real page, rather than none at all. Both
        # failures were measured on the same contract page: 2048 truncated the
        # tool call mid-string, which arrives as no structured output and reads
        # downstream as a blank page; removing the cap entirely let generation
        # run against the model's full 65k context and a single page stopped
        # returning inside seven minutes. A dense A4 page needs about 8k, so
        # this is double the worst case and never binds in practice.
        llm = get_llm(model or settings.CORPUS_PDF_MODEL,
                      max_tokens=settings.CORPUS_PDF_MAX_TOKENS)
        structured = llm.with_structured_output(_PageOut, method="function_calling")
    except Exception as exc:  # noqa: BLE001 - an LLM outage is not a verdict
        raise TransientExtractionError(f"extractor unavailable: {exc}") from exc

    pages: list[Page] = []
    whole: list[str] = []

    for index in numbers:
        # Per page, because a PDF writes nothing until its last page returns: a
        # 40-page contract is twenty silent minutes, which has twice been read as
        # a hung process. This line is the difference between "stalled" and
        # "on page 12 of 44".
        logger.info("  %s page %d/%d", pdf.name[:40], index, len(numbers))
        # No try/except: a page that will not read raises, and the PDF is
        # refused whole. Catching here is what used to drop a page and leave a
        # document that looked complete.
        markdown, items = _read_page_retrying(pdf, index, structured)

        if quality.looks_blind(markdown):
            # HTTP 200 with a fluent refusal. Continuing would write empty pages
            # that look like a document with nothing in it.
            return Extraction((), "", "ocr", total, truncated,
                              error="the model reported it could not see the page")

        # Every page is kept, empty ones included. A page that comes back blank
        # is still the document's page, and page numbers have to keep matching
        # what a reader sees -- dropping page 3 silently renumbers every
        # citation after it. What we do not want is decided upstream, by
        # relevance, not by how much text landed on one page.
        page_items = tuple(Item(
            id=i.id, marker=i.marker, summary=i.summary,
            visible_text=i.visible_text,
            visual_representation=i.visual_representation) for i in items)
        pages.append(Page(
            number=index,
            markdown=markdown,
            cite_url=cite_url(url, index),
            text_hash=text_hash(markdown),
            items=page_items,
            has_tables=any(i.id.startswith("table") for i in page_items),
            has_images=any(not i.id.startswith("table") for i in page_items),
            from_vision=True,
        ))
        whole.append(markdown)

    document_text = quality.normalise("\n\n".join(whole))

    return Extraction(
        pages=tuple(pages),
        text=document_text,
        # Every page came from its image, so there is only ever one engine.
        engine="ocr",
        page_count=total,
        truncated=truncated,
        # Vision paraphrases plausibly, and now every page is vision, so this is
        # true of every PDF. It stays because the agent hedges a PDF citation
        # differently from a web one, and that is exactly the distinction.
        low_confidence=True,
        # No suspect gate and no emptiness gate. A page that reads short or
        # repetitive is still the document's page; relevance is decided
        # upstream, by whether the file is one we want at all.
        suspect=False,
        error="",
    )
