"""PDF -> görüntü chunk'ları (Vision LLM için).

Metin katmanı ÇIKARILMAZ. İSTİSNASIZ her PDF'in her sayfası
görüntüye render edilir ve bir açık kaynak VLM'in anlayacağı boyutta, üst üste
binen (overlap) dikey şeritlere bölünür. Böylece taranmış/metinli fark etmez;
hepsi aynı yoldan (görüntü) LLM'e gider.

Chunking mantığı:
  * Sayfa, hedef genişliğe (VLM_WIDTH) ölçeklenerek render edilir.
  * Yükseklik TILE_H'yi aşarsa, OVERLAP paylaşımlı dikey şeritlere bölünür
    (küçük punto/rate tabloları şerit sınırında kesilmesin diye).
  * Her chunk PNG olarak kaydedilir + bir manifest (hangi PDF/sayfa/şerit).

Çıktı:  <bank>_site/pdf_chunks/<pdf-yolu>/pNNN_tMM.png  + _manifest.json
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
from pathlib import Path

import pymupdf

log = logging.getLogger("dataprep.pdf.chunk")

# Açık kaynak VLM'ler (Qwen2-VL, InternVL, Llama-3.2-Vision ...) için makul boyut.
VLM_WIDTH = 1280        # her chunk'ın hedef genişliği (px)
TILE_H = 1280           # bir şeridin en fazla yüksekliği (px)
OVERLAP = 160           # şeritler arası üst üste binme (px) ~%12
MIN_TAIL = 200          # son şerit bundan kısaysa bir öncekine kat (küçük artık şerit olmasın)


def _render_page(page, target_w: int = VLM_WIDTH) -> "pymupdf.Pixmap":
    """Sayfayı hedef genişliğe ölçekleyerek RGB pixmap olarak render eder."""
    w = page.rect.width or 1
    zoom = target_w / w
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)


def _tile_pixmap(pix, page_dir: Path, page_no: int) -> list[dict]:
    """Bir sayfa pixmap'ini overlap'li dikey şeritlere böl, PNG kaydet."""
    from PIL import Image
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    H = img.height
    chunks: list[dict] = []
    if H <= TILE_H:
        boxes = [(0, H)]
    else:
        boxes, top = [], 0
        while top < H:
            bottom = min(top + TILE_H, H)
            boxes.append((top, bottom))
            if bottom >= H:
                break
            top = bottom - OVERLAP            # bir sonraki şerit overlap kadar geriden
        # çok kısa son artık şeridi öncekine kat
        if len(boxes) >= 2 and (boxes[-1][1] - boxes[-1][0]) < MIN_TAIL:
            boxes[-2] = (boxes[-2][0], boxes[-1][1])
            boxes.pop()
    for i, (top, bottom) in enumerate(boxes, 1):
        tile = img.crop((0, top, img.width, bottom))
        fn = page_dir / f"p{page_no:03d}_t{i:02d}.png"
        tile.save(fn)
        chunks.append({"page": page_no, "tile": i, "file": str(fn),
                       "w": tile.width, "h": tile.height,
                       "y0": top, "y1": bottom})
    return chunks


def process_pdf(pdf_path: str, out_root: Path) -> dict:
    """Bir PDF'i sayfa sayfa render edip overlap'li chunk'lara böler."""
    rel = Path(pdf_path).name
    doc = pymupdf.open(pdf_path)
    n_pages = doc.page_count
    page_dir = out_root / Path(pdf_path).stem
    page_dir.mkdir(parents=True, exist_ok=True)
    all_chunks: list[dict] = []
    for pno in range(n_pages):
        pix = _render_page(doc[pno])
        all_chunks.extend(_tile_pixmap(pix, page_dir, pno + 1))
    doc.close()
    (page_dir / "_manifest.json").write_text(
        json.dumps({"pdf": pdf_path, "page_count": n_pages,
                    "n_chunks": len(all_chunks), "chunks": all_chunks},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return {"pdf": rel, "pages": n_pages, "chunks": len(all_chunks)}


def process_bank(slug: str) -> None:
    site = Path(__file__).resolve().parents[2] / "data" / f"{slug}_site"
    pdfs = sorted(glob.glob(str(site / "pdfs" / "**" / "*.pdf"), recursive=True))
    out_root = site / "pdf_chunks"
    log.info("%s: %d PDF -> chunk", slug, len(pdfs))
    tot_c = tot_p = 0
    for p in pdfs:
        r = process_pdf(p, out_root)
        tot_p += r["pages"]; tot_c += r["chunks"]
    log.info("%s BİTTİ: %d sayfa -> %d görüntü chunk (%s)", slug, tot_p, tot_c, out_root)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="PDF -> Vision LLM için overlap'li görüntü chunk")
    ap.add_argument("banks", nargs="*", help="banka slug'ları (boş=tümü)")
    args = ap.parse_args()
    banks = args.banks or sorted(
        os.path.basename(d)[:-5]
        for d in glob.glob(str(Path(__file__).resolve().parents[2] / "data" / "*_site")))
    for b in banks:
        process_bank(b)


if __name__ == "__main__":
    main()
