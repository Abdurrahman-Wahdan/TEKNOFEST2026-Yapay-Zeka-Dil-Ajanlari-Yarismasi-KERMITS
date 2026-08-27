"""content.py çıktısını Qdrant'a göm — metin / görsel AYRI point'ler.

Kaynak (her banka <slug>_site/content altında, TEK yer — content.py'nin
birleşik HTML+PDF+görsel, gerekli/gereksiz etiketli, tarih/geçerlilik hesaplı
çıktısı): content/**/*.md -> content_relevance=gereksiz OLANLAR ELENIR
(güvenlik ağı — zaten üretilmemiş olmalılar).

Her dosya ana-metin + her görsel-bloğu ayrı parçalara ayrılır, paragraf-duyarlı
CHUNK'lanır (8196 char, %10 overlap — content.py'nin CHUNK'ıyla TUTARLI),
Qwen3-Embedding (MPS) ile embed edilir, Qdrant 'campaigns' koleksiyonuna
canlı-referans metadata (url, gecerlilik_baslangic/bitis, validity_status) ile
upsert edilir.

Kullanım: python -m dataprep.embed [bank ...]   (boş = tüm bankalar)
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import re
import time
from pathlib import Path

from langchain_core.documents import Document

from dataprep.embed_kilit import banka_kilidi

log = logging.getLogger("dataprep.embed")

COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")
# Kullanıcı kararı 2026-08-19: metin temizleme dışındaki TÜM chunk'lar 8196
# karakter, %10 (~820) overlap. Overlap yalnız 8196'yı AŞAN metinler bölünürken
# devreye girer — kısa metinler tek parça kalır, hiçbir kırpma yapılmaz.
CHUNK = int(os.environ.get("EMBED_CHUNK_CHARS", "8196"))
OVERLAP = int(os.environ.get("EMBED_CHUNK_OVERLAP", "820"))
# EŞİK YOK (kullanıcı kararı 2026-08-19): en kısa içerik bile indekslenir.
MIN_CHUNK = int(os.environ.get("EMBED_MIN_CHUNK", "0"))
BATCH = int(os.environ.get("EMBED_BATCH_SIZE", "32"))


# --- frontmatter + chunk yardımcıları --------------------------------------
def _parse(text: str) -> tuple[dict, str]:
    """(frontmatter dict, gövde). Frontmatter yoksa ({}, text).

    SINIR SATIR BAZLI ayrıştırılır — `text.split("---", 2)` DEĞİL.
    ÖLÇÜLEN BUG (2026-08-22): eski sürüm metnin İÇİNDEKİ ilk "---" dizisinde
    bölüyordu; kuveytturk'ün PDF adlarında "---" geçtiği için
    (`.../medium/23122015---kredi-derecelendirmesi-2340.pdf`) frontmatter
    ORTADAN kesiliyor, `url` alanı `.../medium/23122015` gibi KIRPIK okunuyor
    ve frontmatter'ın kalanı gövdeye sızıyordu.

    SONUÇ: farklı PDF'ler AYNI kırpık URL'e düşüp AYNI point_id'yi üretiyor,
    Qdrant'ta birbirinin ÜZERİNE yazıyorlardı — 5 ayrı kredi derecelendirme
    belgesi 2 point'e ineceği için 3 belge sessizce kayboluyordu.

    Doğrusu: yalnız TEK BAŞINA "---" olan satırlar sınırdır (YAML kuralı)."""
    if not text.startswith("---"):
        return {}, text
    satirlar = text.split("\n")
    kapanis = None
    for i, satir in enumerate(satirlar[1:], 1):
        if satir.strip() == "---":
            kapanis = i
            break
    if kapanis is None:                       # kapanmamış frontmatter -> gövde say
        return {}, text
    fm: dict[str, str] = {}
    for line in satirlar[1:kapanis]:
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, "\n".join(satirlar[kapanis + 1:]).lstrip("\n")


def _chunks(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[str]:
    """Paragraf-duyarlı chunk: bloklar (boş satırla ayrılan) birleştirilir; tek
    blok büyükse cümle/satırdan bölünür. Sonda overlap ile bağlam korunur.

    KAYNAK belgelerde istisna YOKTUR: 8196'yı aşan HER ŞEY overlap'li bölünür
    (markdown tabloları dahil — bölmemek 16k token sunucu limitini aşıyordu).
    Bölünmeyen tek şey BİZİM ürettiğimiz karşılaştırma tablolarıdır; onlar
    zaten bu koddan geçmez (bkz. compare/retrieval.py::index_table)."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
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
    return [c for c in out if c.strip()]


# --- kaynak -> Document üret -------------------------------------------------
_IMG_BLOCK = re.compile(r"<!--\s*görsel:\s*(\S+)\s*-->\s*(.*?)(?=<!--\s*görsel:|$)", re.S)


def iter_docs(slug: str):
    """Bir bankanın tüm temizlenmiş sayfalarından, PDF metinlerinden ve görsel tablolarından chunk-Document'ları üretir."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    if not site.exists():
        return

    # 1. content/ varsa öncelikli olarak oradan topla
    content_root = site / "content"
    seen_urls = set()
    if content_root.exists():
        for p in content_root.rglob("*.md"):
            try:
                fm, body = _parse(p.read_text(encoding="utf-8"))
            except Exception as exc:
                # SESSİZ ATLAMA YOK: okunamayan dosya indekse GİRMEZ, yani
                # araştırma ajanı için o sayfa YOK demektir. En azından
                # GÖRÜNÜR olsun ki fark edilip düzeltilebilsin.
                log.warning("  [OKUNAMADI] %s: %s: %s — indekse GİRMİYOR",
                            p, type(exc).__name__, exc)
                continue
            if fm.get("content_relevance") == "gereksiz":
                continue
            url = fm.get("url", "")
            if url:
                seen_urls.add(url)
            common = {"bank": slug, "url": url}
            for k in ("gecerlilik_baslangic", "gecerlilik_bitis", "validity_status"):
                if fm.get(k): common[k] = fm[k]

            first_img = _IMG_BLOCK.search(body)
            main_text = body[:first_img.start()] if first_img else body
            for i, ch in enumerate(_chunks(main_text)):
                yield Document(page_content=ch, metadata={**common, "type": "metin", "chunk_index": i})
            for g_idx, (gsrc, gtext) in enumerate(_IMG_BLOCK.findall(body)):
                for i, ch in enumerate(_chunks(gtext.strip())):
                    yield Document(page_content=ch, metadata={**common, "type": "gorsel", "gorsel_kaynak": gsrc, "gorsel_index": g_idx, "chunk_index": i})

    # 2. Doğrudan temizlenmiş sayfalar (pages)
    # rglob: sayfalar URL ağacını yansıtan İÇ İÇE dizinlerde duruyor; flat
    # glob("*.md") yalnız kökü görüp alt dizinlerdeki sayfaları ATLIYORDU
    # (ölçüldü 2026-08-22: 131 sayfa indekse hiç girmemiş — pipeline
    # `_tercihli_dosyalar` rglob kullandığı için traversal onları görüyor,
    # araştırma ajanı ise aramada bulamıyordu). Alt dizin adı "_" ile
    # başlayan özel klasörler (_raw, _pdf_clean, _image_tables, content)
    # burada ATLANIR — kendi bölümlerinde ele alınıyorlar.
    _OZEL = ("content", "_raw", "_pdf_clean", "_image_tables", "image_text")
    for p in site.rglob("*.md"):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if any(part in _OZEL or part.startswith("_") for part in p.relative_to(site).parts[:-1]):
            continue
        try:
            fm, body = _parse(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("  [OKUNAMADI] %s: %s: %s — indekse GİRMİYOR",
                        p, type(exc).__name__, exc)
            continue
        if fm.get("content_relevance") == "gereksiz":
            continue
        url = fm.get("url", "")
        if url and url in seen_urls:
            continue
        if url: seen_urls.add(url)
        common = {"bank": slug, "url": url}
        for k in ("gecerlilik_baslangic", "gecerlilik_bitis", "validity_status", "campaign_start", "campaign_end", "campaign_status"):
            if fm.get(k): common[k] = fm[k]
        for i, ch in enumerate(_chunks(body)):
            yield Document(page_content=ch, metadata={**common, "type": "metin", "chunk_index": i})

    # 3. Doğrudan temizlenmiş PDF metinleri (_pdf_clean)
    pdf_clean = site / "_pdf_clean"
    if pdf_clean.exists():
        for p in pdf_clean.rglob("*.md"):
            try:
                fm, body = _parse(p.read_text(encoding="utf-8"))
            except Exception as exc:
                # SESSİZ ATLAMA YOK: okunamayan dosya indekse GİRMEZ, yani
                # araştırma ajanı için o sayfa YOK demektir. En azından
                # GÖRÜNÜR olsun ki fark edilip düzeltilebilsin.
                log.warning("  [OKUNAMADI] %s: %s: %s — indekse GİRMİYOR",
                            p, type(exc).__name__, exc)
                continue
            if fm.get("content_relevance") == "gereksiz":
                continue
            url = fm.get("url", "")
            if url and url in seen_urls:
                continue
            if url: seen_urls.add(url)
            common = {"bank": slug, "url": url}
            for k in ("gecerlilik_baslangic", "gecerlilik_bitis", "validity_status", "campaign_start", "campaign_end", "campaign_status"):
                if fm.get(k): common[k] = fm[k]
            for i, ch in enumerate(_chunks(body)):
                yield Document(page_content=ch, metadata={**common, "type": "metin", "chunk_index": i})

    # 4. Doğrudan temizlenmiş görsel tabloları (_image_tables)
    img_tables = site / "_image_tables"
    if img_tables.exists():
        for p in img_tables.rglob("*.md"):
            try:
                fm, body = _parse(p.read_text(encoding="utf-8"))
            except Exception as exc:
                # SESSİZ ATLAMA YOK: okunamayan dosya indekse GİRMEZ, yani
                # araştırma ajanı için o sayfa YOK demektir. En azından
                # GÖRÜNÜR olsun ki fark edilip düzeltilebilsin.
                log.warning("  [OKUNAMADI] %s: %s: %s — indekse GİRMİYOR",
                            p, type(exc).__name__, exc)
                continue
            url = fm.get("url", "")
            common = {"bank": slug, "url": url}
            for k in ("gecerlilik_baslangic", "gecerlilik_bitis", "validity_status"):
                if fm.get(k): common[k] = fm[k]
            for i, ch in enumerate(_chunks(body)):
                yield Document(page_content=ch, metadata={**common, "type": "gorsel", "chunk_index": i})


# --- ana ------------------------------------------------------------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="LLM-friendly veri -> Qdrant (Qwen3, MPS)")
    ap.add_argument("banks", nargs="*")
    ap.add_argument("--recreate", action="store_true", help="koleksiyonu sıfırdan oluştur")
    args = ap.parse_args()

    from concurrent.futures import ThreadPoolExecutor
    from qdrant_client.models import PointStruct
    from vector_stores.client import get_qdrant_client
    from vector_stores import ensure_collection
    from embeddings import get_embedding
    import uuid

    client = get_qdrant_client()
    if args.recreate:
        try:
            client.delete_collection(COLLECTION)
            log.info("koleksiyon silindi: %s", COLLECTION)
        except Exception:
            pass
    created = ensure_collection(COLLECTION)
    log.info("koleksiyon '%s' %s", COLLECTION, "oluşturuldu" if created else "mevcut")

    embed = get_embedding()
    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(os.path.basename(d)[:-5]
                                 for d in glob.glob(str(root / "*_site")))
    grand = 0
    # Embedding sunucusu da AYNI vLLM host'unda; 25 tavanını o da paylaşır
    # (kullanıcı kararı 2026-08-23: "hiçbir aşamada 25'ten fazla istek").
    CONCURRENCY = int(os.environ.get("EMBED_CONCURRENCY", "10"))

    for slug in banks:
        # AYNI BANKAYA İKİ EMBED SÜRECİ GİRMESİN (kullanıcı kararı 2026-08-23).
        # point_id determinist olduğu için tekrar çalıştırmak güvenli; ASIL
        # risk iki sürecin AYNI ANDA aynı bankayı işlemesi: ikisi de "eksik"
        # listesini aynı anda çıkarır (çift LLM maliyeti) ve aynı id'ye
        # upsert eder — biri eski diskten okuduysa yeniyi EZER.
        # Kilit BANKA BAŞINA: farklı bankalar paralel kalabilir, sadece aynı
        # banka serileşir.
        with banka_kilidi(slug, bekle=True, log=lambda m: print(m, flush=True)):
            docs = list(iter_docs(slug))
            items = []
            for i, d in enumerate(docs):
                gsrc = d.metadata.get('gorsel_kaynak', '')
                gidx = d.metadata.get('gorsel_index', '')
                pt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}:{d.metadata.get('url', '')}:{d.metadata.get('type', '')}:{gsrc}:{gidx}:{d.metadata.get('chunk_index', i)}"))
                items.append((pt_id, d))
        
            # Qdrant'ta zaten olanları topluca bul
            all_ids = [it[0] for it in items]
            existing_ids = set()
            for b_idx in range(0, len(all_ids), 200):
                chunk_ids = all_ids[b_idx:b_idx + 200]
                try:
                    found = client.retrieve(collection_name=COLLECTION, ids=chunk_ids)
                    for f in found:
                        existing_ids.add(str(f.id))
                except Exception:
                    pass
        
            missing_items = [it for it in items if it[0] not in existing_ids]
            print(f"\n🚀 {slug}: Toplam {len(docs)} chunk (Mevcut: {len(existing_ids)}, Yüklenecek: {len(missing_items)}) — workers={CONCURRENCY}...", flush=True)
        
            def _process_one(item: tuple[str, any, int, int]):
                """Bir chunk'ı embed edip Qdrant'a yazar — ASLA PES ETMEZ.

                Eskiden tek deneme vardı: tünel URL'i değiştiği ya da soket
                bayatladığı an (nginx "400 / 0 byte", 502, read timeout) o chunk
                SESSİZCE KAYBOLUYORDU — hata bile basılmıyordu, sonraki koşuda da
                id'si Qdrant'ta olmadığı için ancak tekrar denenirdi. İndekse
                girmeyen bir sayfa araştırma ajanı için YOK demektir. Artık her
                hatada tünel adresi tazelenir ve üstel backoff'la (30s tavan)
                sınırsız tekrar denenir — dataprep/compare/retrieval.py::_dayanikli
                ile AYNI ilke."""
                pt_id, d, idx, total = item
                gecikme = 1.0
                deneme = 0
                while True:
                    deneme += 1
                    try:
                        vec = embed.embed_query(d.page_content)
                        pt = PointStruct(
                            id=pt_id,
                            vector=vec,
                            payload={"page_content": d.page_content, "metadata": d.metadata}
                        )
                        client.upsert(collection_name=COLLECTION, points=[pt])
                        print(f"   [{slug}] {idx + 1}/{total} yüklendi ✅", flush=True)
                        return
                    except Exception as exc:
                        from config import tunnel
                        # Hata alan bağlantı havuzu TAMAMEN kapatılır, tazesi
                        # açılır (kullanıcı kararı 2026-08-22 — önceki
                        # aşamalardaki dataprep/vlm.py politikasıyla AYNI).
                        try:
                            from llm.providers.vllm_provider import reset_http_pool
                            reset_http_pool(f"embed/{type(exc).__name__}")
                        except Exception:
                            pass
                        tunnel.refresh_if_needed()
                        print(f"   [{slug}] {idx + 1}/{total} HATA ({deneme}. deneme, "
                              f"{type(exc).__name__}) — {gecikme:.0f}s sonra tekrar",
                              flush=True)
                        time.sleep(gecikme)
                        gecikme = min(gecikme * 2, 30.0)

            work_items = [(pt_id, d, idx, len(missing_items)) for idx, (pt_id, d) in enumerate(missing_items)]
            for itm in work_items:
                _process_one(itm)
            grand += len(docs)
    log.info("TOPLAM upsert: %d chunk -> Qdrant '%s'", grand, COLLECTION)
    print(f"\n🎉 TÜM 10 BANKA QDRANT'A BAŞARIYLA YÜKLENDİ! Toplam: {grand} chunk\n", flush=True)


if __name__ == "__main__":
    main()
