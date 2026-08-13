"""One PDF's transcription, as the document record that gets written per file.

The shape is fixed by the consumer, not by us:

    {"schema_version", "document_id", "metadata": {...}, "pages": [...]}

with one entry per page carrying that page's `markdown` and its `items`. Page
numbers are 1-based and every page appears, including blank ones, so an index
into `pages` always means what a reader sees in a PDF viewer.

    from corpus.pdf_document import build_record

    record = build_record(extraction, pdf_path, url, content_hash)
"""

from pathlib import Path

from banks import clock
from config.settings import settings

from .pdf_extract import Extraction

SCHEMA_VERSION = "2.0"


def document_id(content_hash: str) -> str:
    """A stable id for one PDF, from the bytes it is made of.

    Content-addressed, so re-reading the same file produces the same id and a
    changed file produces a different one -- no run counter, no timestamp.
    """
    return f"doc_{content_hash[:16]}"


def _source_file(pdf: Path, url: str, content_hash: str) -> dict:
    name = url.rsplit("/", 1)[-1].split("?")[0] or pdf.name
    return {
        "name": name,
        # The URL is the real path: the local file is a content-addressed blob
        # whose name says nothing about which document it holds.
        "path": url,
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "size_bytes": pdf.stat().st_size if pdf.exists() else 0,
        "sha256": content_hash,
    }


def build_record(result: Extraction, pdf: Path, url: str,
                 content_hash: str, model: str = "") -> dict:
    """The full record for one transcribed PDF."""
    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id(content_hash),
        "metadata": {
            "source_file": _source_file(pdf, url, content_hash),
            "model": model or settings.CORPUS_PDF_MODEL,
            "page_input_mode": "image",
            "render_dpi": settings.CORPUS_PDF_DPI,
            "render_scale_to": settings.CORPUS_PDF_SCALE_TO,
            "processed_at": clock.stamp(),
            # The real count, which differs from len(pages) only when a very
            # long file was truncated -- worth saying rather than implying.
            "page_count": result.page_count,
            "truncated": result.truncated,
        },
        "pages": [
            {
                "metadata": {"page_number": page.number},
                "markdown": page.markdown,
                "items": [
                    {
                        "id": item.id,
                        "marker": item.marker,
                        "summary": item.summary,
                        "visible_text": item.visible_text,
                        "visual_representation": item.visual_representation,
                    }
                    for item in page.items
                ],
            }
            for page in result.pages
        ],
    }


def markdown_of(record: dict) -> str:
    """The whole document as one markdown string, pages in order.

    Each page is preceded by its number so a reader -- or a chunker -- can still
    tell which page a passage came from once the pages are joined.
    """
    parts = []
    for page in record.get("pages", []):
        number = page.get("metadata", {}).get("page_number")
        parts.append(f"<!-- page {number} -->\n{page.get('markdown', '')}")
    return "\n\n".join(parts)
