"""LLM-friendly veriyi Qdrant'a göm — text / pdf / image AYRI point'ler, relevant-filtreli.

Kaynaklar (her banka <slug>_site altında):
  * SAYFA   : kök *.md (LLM-friendly)         -> type=page
  * PDF     : pdf_text/*.md (relevant=true)   -> type=pdf   (relevant=false ELENIR)
  * GÖRSEL  : image_text/*.md (her görsel blk) -> type=image (kendi görsel_url'süyle)

Her kaynak paragraf-duyarlı CHUNK'lanır, Qwen3-Embedding (MPS) ile embed edilir,
Qdrant 'campaigns' koleksiyonuna canlı-referans metadata ile upsert edilir.
Birleştirme YOK: text/pdf/image ayrı birimler (odaklı retrieval + provenance).

Kullanım: python -m dataprep.embed [bank ...]   (boş = tüm bankalar)
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import re
from pathlib import Path

from langchain_core.documents import Document

log = logging.getLogger("dataprep.embed")

COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")
CHUNK = 900           # hedef chunk boyutu (char)
OVERLAP = 150         # chunk'lar arası örtüşme
MIN_CHUNK = 40        # bundan kısa chunk atlanır
BATCH = 128           # embed/upsert batch


# --- frontmatter + chunk yardımcıları --------------------------------------
def _parse(text: str) -> tuple[dict, str]:
    """(frontmatter dict, gövde). Frontmatter yoksa ({}, text)."""
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        p = text.split("---", 2)
        if len(p) >= 3:
            body = p[2].lstrip("\n")
            for line in p[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip('"')
    return fm, body


def _chunks(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[str]:
    """Paragraf-duyarlı chunk: bloklar (boş satırla ayrılan) birleştirilir; tek blok
    büyükse cümle/satırdan bölünür. Sonda overlap ile bağlam korunur."""
    text = text.strip()
    if len(text) <= size:
        return [text] if len(text) >= MIN_CHUNK else []
    blocks = re.split(r"\n\s*\n", text)
    out, cur = [], ""
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        if len(b) > size:                        # dev blok -> satır/cümleden parçala
            if cur:
                out.append(cur); cur = ""
            for piece in re.split(r"(?<=[.!?])\s+|\n", b):
                if len(cur) + len(piece) + 1 <= size:
                    cur += (" " if cur else "") + piece
                else:
                    if cur:
                        out.append(cur)
                    cur = piece[-size:] if len(piece) > size else piece
            continue
        if len(cur) + len(b) + 2 <= size:
            cur += ("\n\n" if cur else "") + b
        else:
            out.append(cur); cur = b
    if cur:
        out.append(cur)
    # overlap: her chunk'ın başına öncekinin son OVERLAP karakteri
    if overlap and len(out) > 1:
        merged = [out[0]]
        for i in range(1, len(out)):
            tail = out[i - 1][-overlap:]
            merged.append((tail + "\n" + out[i]) if tail else out[i])
        out = merged
    return [c for c in out if len(c.strip()) >= MIN_CHUNK]


# --- kaynak -> Document üret -------------------------------------------------
_IMG_BLOCK = re.compile(r"<!--\s*görsel:\s*(\S+)\s*-->\s*(.*?)(?=<!--\s*görsel:|$)", re.S)


def iter_docs(slug: str):
    """Bir bankanın page/pdf/image kaynaklarından chunk-Document'ları üretir."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    if not site.exists():
        return

    # 1) SAYFA (kök *.md; pdf_text/image_text hariç)
    for p in site.rglob("*.md"):
        if "pdf_text" in p.parts or "image_text" in p.parts:
            continue
        fm, body = _parse(p.read_text(encoding="utf-8"))
        for i, ch in enumerate(_chunks(body)):
            md = {
                "type": "page", "bank": slug, "source_url": fm.get("url", ""),
                "parent": fm.get("parent", ""), "title": fm.get("title", ""),
                "chunk_index": i}
            # Gemma'nın çıkardığı kampanya tarihi -> sayfanın TÜM chunk'larına.
            if fm.get("campaign_end"):
                md["campaign_end"] = fm["campaign_end"]
            if fm.get("campaign_start"):
                md["campaign_start"] = fm["campaign_start"]
            yield Document(page_content=ch, metadata=md)

    # 2) PDF (pdf_text/*.md; relevant=false ELE)
    for p in (site / "pdf_text").rglob("*.md") if (site / "pdf_text").exists() else []:
        fm, body = _parse(p.read_text(encoding="utf-8"))
        if str(fm.get("relevant", "true")).lower() == "false":   # gereksiz -> alma
            continue
        for i, ch in enumerate(_chunks(body)):
            md = {
                "type": "pdf", "bank": slug, "pdf_url": fm.get("pdf_url", ""),
                "source_page": fm.get("source_page", ""), "parent": fm.get("parent", ""),
                "chunk_index": i}
            if fm.get("campaign_end"):
                md["campaign_end"] = fm["campaign_end"]
            if fm.get("campaign_start"):
                md["campaign_start"] = fm["campaign_start"]
            yield Document(page_content=ch, metadata=md)

    # 3) GÖRSEL (image_text/*.md; her görsel bloğu ayrı, kendi URL'süyle)
    for p in (site / "image_text").rglob("*.md") if (site / "image_text").exists() else []:
        fm, body = _parse(p.read_text(encoding="utf-8"))
        src = fm.get("source_page", "")
        for gurl, gtext in _IMG_BLOCK.findall(body):
            for i, ch in enumerate(_chunks(gtext.strip())):
                yield Document(page_content=ch, metadata={
                    "type": "image", "bank": slug, "source_page": src,
                    "gorsel_url": gurl, "chunk_index": i})


# --- ana ------------------------------------------------------------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="LLM-friendly veri -> Qdrant (Qwen3, MPS)")
    ap.add_argument("banks", nargs="*")
    ap.add_argument("--recreate", action="store_true", help="koleksiyonu sıfırdan oluştur")
    args = ap.parse_args()

    from embeddings import get_embedding
    from vector_stores import ensure_collection, get_vector_store

    if args.recreate:
        from vector_stores.client import get_qdrant_client
        try:
            get_qdrant_client().delete_collection(COLLECTION)
            log.info("koleksiyon silindi: %s", COLLECTION)
        except Exception:
            pass
    created = ensure_collection(COLLECTION)
    log.info("koleksiyon '%s' %s", COLLECTION, "oluşturuldu" if created else "mevcut")

    embed = get_embedding()
    vs = get_vector_store(COLLECTION, embed)

    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(os.path.basename(d)[:-5]
                                 for d in glob.glob(str(root / "*_site")))
    grand = 0
    for slug in banks:
        docs = list(iter_docs(slug))
        by = {}
        for d in docs:
            by[d.metadata["type"]] = by.get(d.metadata["type"], 0) + 1
        log.info("%s: %d chunk (%s)", slug, len(docs),
                 ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
        for i in range(0, len(docs), BATCH):
            vs.add_documents(docs[i:i + BATCH])
        grand += len(docs)
    log.info("TOPLAM upsert: %d chunk -> Qdrant '%s'", grand, COLLECTION)


if __name__ == "__main__":
    main()
