"""Qdrant 'campaigns' koleksiyonuna DOKÜMAN düzeyinde `campaign_end` damgala.

Sorun: chunk'lar 900 karaktere bölündüğü için kampanyanın bitiş tarihi çoğu zaman
teklif metninden AYRI bir chunk'ta kalıyor; chunk-düzeyi tarih filtresi bu yüzden
süresi dolmuş kampanyaları kaçırıyor.

Çözüm (main'in index/chunk.py yaklaşımının aynısı): aynı KAYNAK SAYFAYA ait tüm
chunk'ları birleştirip tam metinden tarihi çıkar (corpus.dates — test edilmiş
parser), sonra o sayfanın HER chunk'ına top-level `campaign_end` payload'ı yaz.
Yeniden embed YOK — sadece payload güncellemesi (hızlı).

Sonra retrieval `campaign_end` yoksa (ürün/ücret/undated) tutar, varsa ve süresi
geçmişse eler. Kullanım:  python -m dataprep.stamp_campaign_end
"""
from __future__ import annotations

import logging
from collections import defaultdict

from corpus import dates
from vector_stores.client import get_qdrant_client

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dataprep.stamp")

COLLECTION = "campaigns"


def _page_url(meta: dict) -> str:
    # page -> source_url; pdf/image -> geldikleri sayfa (source_page).
    return (meta.get("source_url") or meta.get("source_page") or "").strip()


def main() -> None:
    client = get_qdrant_client()

    # 1) tüm noktaları gez, kaynak sayfaya göre grupla (id + chunk_index + metin).
    groups: dict[str, list[tuple]] = defaultdict(list)
    offset = None
    total = 0
    while True:
        points, offset = client.scroll(
            COLLECTION, limit=1000, with_payload=True, offset=offset)
        for pt in points:
            p = pt.payload or {}
            meta = p.get("metadata", {}) or {}
            url = _page_url(meta)
            if not url:
                continue
            groups[url].append(
                (pt.id, meta.get("chunk_index", 0), p.get("page_content") or ""))
        total += len(points)
        if offset is None:
            break
    log.info("%d nokta, %d benzersiz kaynak sayfa", total, len(groups))

    # 2) her sayfa için tam metinden bitiş tarihini çıkar; varsa chunk'lara damgala.
    stamped_pages = stamped_points = 0
    for url, rows in groups.items():
        rows.sort(key=lambda r: r[1])                 # chunk_index sırasıyla
        full = "\n".join(r[2] for r in rows)
        _start, end, _pat = dates.extract(full)
        if not end:
            continue
        ids = [r[0] for r in rows]
        client.set_payload(COLLECTION, payload={"campaign_end": end}, points=ids)
        stamped_pages += 1
        stamped_points += len(ids)
    log.info("BİTTİ: %d sayfaya tarih damgalandı (%d chunk). Tarihsizler undated kalır.",
             stamped_pages, stamped_points)


if __name__ == "__main__":
    main()
