"""Turning a PDF into pages of ordered blocks, with citable page numbers.

Two sources per page, and which one is authoritative matters:

- **`pdftotext` is the authority for what the document says.** It is the file's
  own text layer -- the characters the bank embedded -- so a profit rate read
  from it is the rate, not a reading of a picture of the rate.
- **The page image is the authority for how it is laid out.** The text layer
  loses table structure, and a fee schedule whose columns have been flattened
  into a list of numbers is worse than useless.

So both go to the model, and the prompt says the text layer wins on numbers.
Only genuinely scanned pages have no text layer; those are marked
`from_vision`, and the document is flagged `low_confidence` so the agent can
hedge when it cites them.

    from corpus.pdf_extract import extract

    result = extract(path, "https://bank.com.tr/documents/ucretler.pdf")
    result.pages[0].cite_url        # ".../ucretler.pdf#page=1"
"""

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from pydantic import Field as PydanticField

from config.settings import settings

from . import pdftools, quality
from .models import Block, Page
from .urls import text_hash

logger = logging.getLogger(__name__)

# What a block may be. Closed, because these become chunk boundaries later and
# an open vocabulary would make that ungovernable.
BLOCK_KINDS = ("heading", "paragraph", "table", "list", "image", "figure_caption")

EXTRACT_PROMPT = """Bu, bir Türk katılım bankasının belgesinden bir sayfa görüntüsüdür.

Sayfadaki metnin TAMAMINI, olduğu gibi, yapısını koruyarak çıkar.

Kurallar:
- Sadece görselde ve metin katmanında olanı yaz. Yorum, özet veya açıklama ekleme.
- Okuma sırasını koru. Sayfa iki sütunlu ise önce sol sütunun tamamını, sonra sağ sütunun tamamını yaz.
- Tabloları markdown tablosu olarak yaz ve kind alanına "table" yaz.
- Başlıkları "heading", paragrafları "paragraph", maddeleri "list" olarak işaretle.
- Sayfadaki HER görseli yaz; hiçbirini atlama. Logo, ikon, fotoğraf, grafik, şema, tablo görüntüsü — hepsi "image" türünde bir blok olsun.
- Görsel bilgi taşıyorsa (grafik, şema, infografik, görüntü hâlindeki tablo) içindeki TÜM veriyi de yaz: eksen adlarını, etiketleri, sayıları, oranları. Tablo görüntüsünü markdown tablosu olarak aktar.
- Görsel yalnızca süsse (logo, ikon, dekoratif fotoğraf) kısa bir açıklama yeter.
- Sayıları, oranları, tutarları ve tarihleri BİREBİR kopyala. Yuvarlama, biçim değiştirme.
- Aşağıda bir metin katmanı verildiyse sayı ve oranlarda O metin esastır; görsel yalnızca yerleşim içindir.
- Okunamayan bir yer varsa oraya {unreadable} yaz. Tahmin etme, uydurma.
- Sayfada metin yoksa tek bir blok olarak {no_text} yaz.
""".format(unreadable=quality.UNREADABLE, no_text=quality.NO_TEXT)


class _BlockOut(BaseModel):
    kind: str = PydanticField(description="heading, paragraph, table, list, image veya figure_caption")
    text: str = PydanticField(description="Bloğun metni. Tablolar markdown tablosu olarak.")


class _PageOut(BaseModel):
    blocks: list[_BlockOut] = PydanticField(description="Sayfadaki bloklar, okuma sırasıyla.")


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


def _tiles(width: int, height: int, count: int) -> list[tuple[int, int, int, int]]:
    """Vertical crop boxes covering the page, with a little overlap.

    The overlap is 5% of a tile, so a line sitting on a seam appears whole in at
    least one of them; the joiner drops the duplicate.
    """
    if count < 2 or not width or not height:
        return []
    band = height // count
    overlap = max(band // 20, 1)
    boxes = []
    for index in range(count):
        top = max(index * band - (overlap if index else 0), 0)
        bottom = min((index + 1) * band + overlap, height)
        boxes.append((0, top, width, bottom - top))
    return boxes


def _join(parts: list[str]) -> str:
    """Concatenate tile results, dropping a line duplicated across a seam."""
    out: list[str] = []
    for part in parts:
        lines = [ln for ln in part.splitlines()]
        if out and lines:
            # The tail of what we have, against the head of what is arriving.
            tail = out[-1].strip()
            while lines and lines[0].strip() and lines[0].strip() == tail:
                lines.pop(0)
        out.extend(lines)
    return "\n".join(out)


def _read_page(pdf: Path, number: int, text_layer: str, llm) -> tuple[list[Block], str]:
    """Ask the model for one page as ordered blocks."""
    width, height = pdftools.page_size(pdf, dpi=settings.CORPUS_PDF_DPI)
    boxes = _tiles(width, height, settings.CORPUS_PDF_TILES)
    crops: list[tuple[int, int, int, int] | None] = boxes or [None]

    blocks: list[Block] = []
    texts: list[str] = []
    # Tiles overlap so a line on a seam survives in one of them, which means the
    # model returns the straddling blocks twice. Measured on a two-page bulletin:
    # every paragraph on page 2 came back doubled and the unique-line ratio fell
    # under the suspect threshold, so a good document would have been refused.
    # A page that genuinely repeats a paragraph verbatim is rare; losing that
    # duplicate is much cheaper than doubling every page.
    seen: set[str] = set()
    order = 0
    for crop in crops:
        image = pdftools.render(pdf, number, dpi=settings.CORPUS_PDF_DPI, crop=crop)
        prompt = EXTRACT_PROMPT
        if text_layer.strip():
            prompt += f"\nSayfanın metin katmanı:\n{text_layer[:6000]}"
        result = llm.invoke([HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(image).decode()}},
        ])])
        if result is None:
            continue
        for item in result.blocks:
            kind = item.kind.strip().lower()
            body = quality.normalise(item.text)
            if not body or body == quality.NO_TEXT:
                continue
            key = " ".join(body.split()).casefold()
            if key in seen:
                continue
            seen.add(key)
            blocks.append(Block(
                kind=kind if kind in BLOCK_KINDS else "paragraph",
                text=body, order=order))
            texts.append(body)
            order += 1
    return blocks, _join(texts)


def extract(pdf: Path, url: str = "", model: str | None = None) -> Extraction:
    """Read a PDF into citable pages.

    Pages whose text layer is real are still sent to the model, because layout
    is what the text layer loses; pages without one are read from the image
    alone and marked.

    Returns an Extraction. Failure is always reported, never returned as a
    short document that looks fine.
    """
    try:
        raw_pages = pdftools.text_pages(pdf)
    except pdftools.PdfToolError as exc:
        return Extraction((), "", "pdftotext", 0, error=str(exc))
    if not raw_pages:
        return Extraction((), "", "pdftotext", 0, error="no pages")

    total = len(raw_pages)
    stamps = quality.stamp_lines(raw_pages, settings.CORPUS_PDF_STAMP_FRACTION)
    cleaned = [quality.strip_stamps(p, stamps) for p in raw_pages]

    limit = settings.CORPUS_PDF_MAX_PAGES
    truncated = total > limit
    cleaned = cleaned[:limit]

    from llm import get_llm

    try:
        llm = get_llm(model or settings.CORPUS_PDF_MODEL,
                      max_tokens=settings.CORPUS_PDF_MAX_TOKENS)
        structured = llm.with_structured_output(_PageOut, method="function_calling")
    except Exception as exc:  # noqa: BLE001 - an LLM outage is not a verdict
        return Extraction((), "", "pdftotext", total, truncated,
                          error=f"extractor unavailable: {exc}")

    pages: list[Page] = []
    whole: list[str] = []
    vision_pages = 0
    failed_pages: list[int] = []
    last_failure = ""

    for index, layer in enumerate(cleaned, start=1):
        has_text = quality.page_has_text(layer, settings.CORPUS_PDF_MIN_CHARS_PER_PAGE)
        try:
            blocks, page_text = _read_page(pdf, index, layer if has_text else "", structured)
        except Exception as exc:  # noqa: BLE001 - one page must not lose the file
            logger.warning("page %d of %s failed: %s", index, pdf.name, exc)
            failed_pages.append(index)
            last_failure = f"{type(exc).__name__}: {exc}"
            blocks, page_text = [], ""

        if quality.looks_blind(page_text):
            # HTTP 200 with a fluent refusal. Continuing would write empty pages
            # that look like a document with nothing in it.
            return Extraction((), "", "pdftotext+vision", total, truncated,
                              error="the model reported it could not see the page")

        if not blocks and has_text:
            # The model gave nothing but the file has a text layer: keep the
            # text rather than losing the page.
            page_text = quality.normalise(layer)
            blocks = [Block(kind="paragraph", text=page_text, order=0)]

        if not blocks:
            continue

        if not has_text:
            vision_pages += 1

        pages.append(Page(
            number=index,
            blocks=tuple(blocks),
            cite_url=cite_url(url, index),
            text_hash=text_hash(page_text),
            has_tables=any(b.kind == "table" for b in blocks),
            has_images=any(b.kind in ("image", "figure_caption") for b in blocks),
            from_vision=not has_text,
        ))
        whole.append(page_text)

    # Every page failing is an outage, not a document. Falling back to the text
    # layer for all of them would produce a structureless document that reads as
    # a success -- no tables, no headings, nothing saying why.
    if failed_pages and len(failed_pages) == len(cleaned):
        return Extraction((), "", "pdftotext", total, truncated,
                          error=f"extractor unavailable on every page: {last_failure}")

    document_text = quality.normalise("\n\n".join(whole))
    ratio = quality.unique_line_ratio(document_text)
    suspect = ratio < settings.CORPUS_PDF_MIN_UNIQUE_LINES

    engine = "pdftotext+vision" if any(not p.from_vision for p in pages) else "ocr"
    if pages and all(p.from_vision for p in pages):
        engine = "ocr"

    return Extraction(
        pages=tuple(pages),
        text=document_text,
        engine=engine,
        page_count=total,
        truncated=truncated,
        # Vision paraphrases plausibly. A document any part of which was read
        # from an image should be cited with a hedge.
        low_confidence=vision_pages > 0
        or bool(failed_pages)
        or quality.unreadable_ratio(document_text) > 0.2
        or quality.turkish_score(document_text) < 0.3,
        suspect=suspect,
        error="" if pages else "no readable pages",
    )
