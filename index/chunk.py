"""Turning one clean document into the chunks that get embedded.

The corpus already decided the units: a web page's `sections`, a PDF's `pages`.
This module turns each unit into a `Chunk` — reconstructing a PDF page's text
from its markdown and items, embedding a campaign whole so its dates stay with its
conditions, and splitting only the rare unit too large for one vector.

    from index.chunk import chunks
    for chunk in chunks(document, linked_from=()):
        ...
"""

import re

from config.settings import settings

from .embed_text import header_for, passage_text
from .models import Chunk
from .payload import build_payload


def _page_text(page: dict) -> str:
    """A PDF page's text for embedding: its markdown, plus what its items say.

    The markdown carries inline markers like `<table_1>` where a rich element
    sits. Those markers mean nothing to a reader or to a retriever on their own,
    so each one is followed by the item's own text -- the values inside a fee
    table, the labels on a chart -- which is exactly the content a question
    about that table needs to match against.
    """
    text = page.get("markdown", "") or ""
    for item in page.get("items", ()) or ():
        marker = item.get("marker", "")
        parts = [item.get("visible_text", ""), item.get("summary", "")]
        detail = "\n".join(p for p in parts if p)
        if not detail:
            continue
        if marker and marker in text:
            text = text.replace(marker, f"{marker}\n{detail}", 1)
        else:
            text = f"{text}\n\n{detail}"
    return text.strip()


def _split(text: str, max_chars: int) -> list[str]:
    """Split an over-long unit, preferring the cleanest boundary available.

    Paragraphs first, then sentence ends, and only then a hard character cut.
    The fallback chain matters: splitting on blank lines alone let a single
    unbroken paragraph through whole, and one real page carried 207,823
    characters that way -- enough to stall an embedding batch and balloon
    memory. A sentence boundary loses nothing; the hard cut is the last resort
    for text that offers no boundary at all.
    """
    if len(text) <= max_chars:
        return [text]

    def pack(units: list[str], joiner: str) -> list[str]:
        out: list[str] = []
        current = ""
        for unit in units:
            if current and len(current) + len(unit) + len(joiner) > max_chars:
                out.append(current.strip())
                current = unit
            else:
                current = f"{current}{joiner}{unit}" if current else unit
        if current.strip():
            out.append(current.strip())
        return out

    # Any piece still too big had no blank lines in it: try sentence ends, then
    # cut what still will not divide.
    refined: list[str] = []
    for piece in pack(text.split("\n\n"), "\n\n"):
        refined.extend([piece] if len(piece) <= max_chars
                       else pack(re.split(r"(?<=[.!?])\s+", piece), " "))
    final: list[str] = []
    for piece in refined:
        if len(piece) <= max_chars:
            final.append(piece)
        else:
            final.extend(piece[i:i + max_chars]
                         for i in range(0, len(piece), max_chars))
    return _with_overlap(final) or [text]


def _with_overlap(pieces: list[str]) -> list[str]:
    """Her parçanın başına bir öncekinin SONUNDAN overlap ekler (%10, ~820 kar).

    YALNIZ 8196 sınırını aşıp BÖLÜNMÜŞ metinler için çalışır — _split zaten
    kısa metinlerde erken döner, yani bölünmeyen hiçbir birime overlap
    eklenmez (kullanıcı kararı 2026-08-19: "her zaman overlap gerek yok,
    sadece 8196'yı aşan metinleri parçalarken").

    NEDEN (kullanıcı kararı 2026-08-19): overlap olmadan, sınıra denk gelen bir
    bilgi ikiye bölünür (başlık bir chunk'ta, tablosu diğerinde) ve arama yalnız
    yarısını bulabilir. Overlap'li kesimde bilgi EN AZ BİR chunk'ta bütün kalır.

    Overlap KELİME sınırından alınır — kelimeyi ortadan bölmek embedding
    kalitesini düşürür. İlk parça olduğu gibi kalır (öncesi yok)."""
    n = settings.INDEX_CHUNK_OVERLAP_CHARS
    if n <= 0 or len(pieces) < 2:
        return pieces
    out = [pieces[0]]
    for onceki, parca in zip(pieces, pieces[1:]):
        kuyruk = onceki[-n:]
        bosluk = kuyruk.find(" ")                  # yarım kelimeyle başlama
        if 0 <= bosluk < len(kuyruk) - 1:
            kuyruk = kuyruk[bosluk + 1:]
        out.append(f"{kuyruk}\n\n{parca}" if kuyruk.strip() else parca)
    return out


def _make_chunks(document: dict, unit_kind: str, unit_index: int, *, body: str,
                 text_hash: str, cite_url: str, header_detail: str,
                 heading_path: str = "", page_number: int | None = None,
                 order: int = 0, from_vision: bool = False,
                 linked_from: tuple[str, ...] = ()) -> list[Chunk]:
    """One unit's text into one or more chunks (more only if it was split)."""
    doc_id = document.get("doc_id", "")
    bank = document.get("bank", "")
    title = document.get("title", "")
    header = header_for(bank, title, header_detail)
    pieces = _split(body, settings.INDEX_MAX_CHUNK_CHARS)

    chunks: list[Chunk] = []
    for part, piece in enumerate(pieces):
        chunk_id = f"{doc_id}:{unit_kind}:{unit_index}"
        if len(pieces) > 1:
            chunk_id += f"#{part}"
        payload = build_payload(
            document, unit_kind=unit_kind, text=piece, cite_url=cite_url,
            text_hash=text_hash, heading_path=heading_path, page_number=page_number,
            order=order, from_vision=from_vision, linked_from=linked_from)
        chunks.append(Chunk(
            chunk_id=chunk_id, doc_id=doc_id, text_hash=text_hash,
            embed_text=passage_text(header, piece), text=piece,
            cite_url=cite_url, url=document.get("url", ""), payload=payload))
    return chunks


def chunks(document: dict, linked_from: tuple[str, ...] = ()) -> list[Chunk]:
    """Every chunk for one document.

    Args:
        document: one parsed line of documents.jsonl.
        linked_from: for a PDF, the doc_ids of pages that link it (computed once
            across the corpus by the caller). Empty for pages.
    """
    # A campaign is embedded whole, whatever its source, so its validity dates
    # stay in the same vector as its conditions -- the correctness lever the
    # whole pipeline protects.
    if document.get("doc_kind") == "campaign":
        from_vision = any(p.get("from_vision") for p in document.get("pages", ()))
        return _make_chunks(
            document, "document", 0,
            body=document.get("text", ""),
            text_hash=document.get("text_hash", ""),
            cite_url=document.get("url", ""),
            header_detail="", from_vision=from_vision, linked_from=linked_from)

    if document.get("source_type") == "pdf":
        out: list[Chunk] = []
        for index, page in enumerate(document.get("pages", ())):
            out += _make_chunks(
                document, "page", index,
                body=_page_text(page),
                text_hash=page.get("text_hash", ""),
                cite_url=page.get("cite_url", ""),
                header_detail=f"sayfa {page.get('number', index + 1)}",
                page_number=page.get("number"), order=index,
                from_vision=bool(page.get("from_vision")), linked_from=linked_from)
        return out

    out = []
    for index, section in enumerate(document.get("sections", ())):
        out += _make_chunks(
            document, "section", index,
            body=section.get("text", ""),
            text_hash=section.get("text_hash", ""),
            cite_url=section.get("cite_url", ""),
            header_detail=section.get("heading_path", ""),
            heading_path=section.get("heading_path", ""), order=index)
    return out


def linked_from_map(documents: list[dict]) -> dict[str, tuple[str, ...]]:
    """Invert every page's `attachments` to get, per PDF doc_id, the pages that
    link it. Gives the agent pdf->page navigation, not just page->pdf."""
    inverted: dict[str, list[str]] = {}
    for document in documents:
        parent = document.get("doc_id", "")
        for pdf_doc_id in document.get("attachments", ()):
            inverted.setdefault(pdf_doc_id, []).append(parent)
    return {k: tuple(v) for k, v in inverted.items()}
