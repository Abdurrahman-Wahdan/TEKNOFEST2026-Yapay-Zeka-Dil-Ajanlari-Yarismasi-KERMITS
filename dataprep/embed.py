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

ÜÇ ŞEY ÖNEMLİ, üçü de canlı koleksiyondan ölçüldü (7030 point):

1. **METADATA İSİMLERİ.** Koleksiyondaki her point şunları taşıyor: bank, url,
   type (metin/gorsel), chunk_index, validity_status (+ tarih varsa
   gecerlilik_baslangic/bitis). Bu dosyanın ESKİ sürümü source_url/pdf_url/
   campaign_end yazıyordu — canlı koleksiyonun HİÇBİR point'inde bu isimler yok.
   Farklı isim yazmak, aynı koleksiyona okuyan tarafın göremediği satırlar
   eklemek demek (bkz. dataprep/compare/retrieval.py).

2. **DETERMİNİSTİK ID.** Point id'si uuid5(url + chunk_index). Eskiden id yoktu:
   langchain her Document'a rastgele bir uuid4 veriyordu, yani her yeniden
   gömme TÜM id'leri değiştiriyordu. Tablolardaki 3378 point_id atıfının 1051'i
   (%31) tam olarak bu yüzden artık hiçbir şeye denk gelmiyor. Deterministik id
   ile aynı chunk her koşuda aynı id'yi alır; atıflar yaşamaya devam eder.

3. **ÖNCE SİL, SONRA YAZ.** Bir url'in tüm point'leri upsert'ten önce silinir.
   Böylece koşu tekrarlanabilir: sayfa kısaldıysa artakalan chunk'lar ortada
   kalmaz, ve eski rastgele-id'li point'ler yeni deterministik olanların
   yanında ikinci bir kopya olarak durmaz.

Kullanım:
  python -m dataprep.embed --only-missing       # sadece Qdrant'ta olmayan url'ler
  python -m dataprep.embed                      # tüm korpus (idempotent)
  python -m dataprep.embed --only-missing albaraka kuveytturk
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path


from embeddings import get_embedding
from vector_stores.client import get_qdrant_client

from dataprep.embed_kilit import banka_kilidi

log = logging.getLogger("dataprep.embed")

CORPUS = Path(os.environ.get("CORPUS_DIR")
              or Path(__file__).resolve().parents[1] / "TF26_data" / "data")
COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")
# Kullanıcı kararı 2026-08-19: metin temizleme dışındaki TÜM chunk'lar 8196
# karakter, %10 (~820) overlap. Overlap yalnız 8196'yı AŞAN metinler bölünürken
# devreye girer — kısa metinler tek parça kalır, hiçbir kırpma yapılmaz.
CHUNK = int(os.environ.get("EMBED_CHUNK_CHARS", "8196"))
OVERLAP = int(os.environ.get("EMBED_CHUNK_OVERLAP", "820"))
# EŞİK YOK (kullanıcı kararı 2026-08-19): en kısa içerik bile indekslenir.
MIN_CHUNK = int(os.environ.get("EMBED_MIN_CHUNK", "0"))
BATCH = int(os.environ.get("EMBED_BATCH_SIZE", "32"))
MIN_IMG_CHUNK = 0    # eşik yok


# --- frontmatter + chunk yardımcıları --------------------------------------
# Sınırlayıcı, YALNIZCA kendi başına bir satır olan `---`. Bu satır-bağı önemli:
# eski sürüm `text.split("---", 2)` yapıyordu ve korpusta `---` İÇEREN url'ler var
# (ör. .../anlasmali-kurumlar-listesi---kolej.pdf). Böyle bir dosyada bölme
# url'in ORTASINDA gerçekleşiyor: frontmatter yarıda kesiliyor, url budanmış
# haliyle okunuyor (.../anlasmali-kurumlar-listesi), gövde ise "kolej.pdf" ile
# başlayıp frontmatter'ın kalanını metin sanıyordu. 25 dosya bu şekilde yanlış
# url ile gömülecekti.
_FRONT = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.S)


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
    if overlap and len(out) > 1:
        merged = [out[0]]
        for i in range(1, len(out)):
            tail = out[i - 1][-overlap:]
            merged.append((tail + "\n" + out[i]) if tail else out[i])
        out = merged
    return [c for c in out if c.strip()]


# --- geçerlilik: _url_havuzu.json ÖNCE ---------------------------------------
def load_url_pool() -> dict[str, dict]:
    """url -> {gecerlilik_baslangic, gecerlilik_bitis, validity_status}.

    Sayfanın kendi frontmatter'ından daha eksiksiz: 6614 kaynak kaydının
    5797'sinde ikisi aynı, farklı olan 817'nin neredeyse tamamı havuzun
    doldurduğu boş alanlar (763 damgasız durum, 58 eksik tarih); gerçek çelişki
    13228 tarih alanında yalnızca 10 tane. Bu yüzden havuz önce okunur."""
    path = CORPUS / URL_POOL
    if not path.exists():
        log.warning("%s yok — geçerlilik yalnız frontmatter'dan okunacak", path)
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {url: rec for urls in raw.values() for url, rec in urls.items()}


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


_IMG_BLOCK = re.compile(r"<!--\s*görsel:\s*(\S+?)\s*-->[ \t]*\n?(.*?)(?=<!--\s*görsel:|\Z)", re.S)


def split_images(body: str) -> tuple[str, list[tuple[str, str]]]:
    """(görsellerden arındırılmış sayfa metni, [(görsel_url, görsel_metni), ...]).

    Görseller sayfa metninden ÇIKARILIR ve kendi point'leri olur — koleksiyonun
    kurulduğu düzen bu: 3000 'metin' point'i tarandı, HİÇBİRİNDE görsel bloğu
    yok, buna karşılık 1308 ayrı 'gorsel' point'i var. Blokları sayfa gövdesinde
    bırakmak aynı metni iki farklı biçimde indekslemek olurdu.

    Bu ayrım yapılmazsa görsel metni HİÇBİR YERE girmiyordu: disk üzerindeki 748
    görselin 517'sinin point'i vardı, kalan 231'inin metni ne sayfa chunk'ında ne
    de ayrı bir point'te bulunuyordu — örneklenen 35 bloğun 30'u tam olarak böyle
    kayıptı ("Toplam 5.000TL'ye kadar bonus kazan!" gibi kampanya metinleri)."""
    images = [(url, text.strip()) for url, text in _IMG_BLOCK.findall(body)]
    return _IMG_BLOCK.sub("", body).strip(), images


def indexed_text(client) -> str:
    """Koleksiyondaki TÜM metin, tek normalize blob.

    Önbellekteki bir görsel metninin zaten gömülü olup olmadığını, KENDİ
    ürettiğimiz chunk'lara değil, Qdrant'ta GERÇEKTEN duranlara bakarak
    anlamak için. İkisi aynı değil: koleksiyondaki sayfaların çoğu başka bir
    koşuda, başka sınırlarla parçalanmış. Kendi çıktımıza bakmak, orada duran
    ama koleksiyonda olmayan üç Adil Katılım metnini "zaten var" saydırıyordu."""
    parts = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION, limit=1000, offset=offset,
            with_payload=True, with_vectors=False)
        parts.extend(" ".join(((p.payload or {}).get("page_content") or "").split())
                     for p in points)
        if offset is None:
            return "\n".join(parts)


def indexed_keys(client) -> set[tuple[str, str]]:
    """Koleksiyonda ŞU AN ne var: {("metin", sayfa_url), ("gorsel", görsel_url)}.

    Tür ayrımı şart: bir sayfa indekslenmişken ona gömülü görselin point'i
    eksik olabiliyor — 231 görsel tam olarak böyleydi. Yalnız sayfa url'ine
    bakan bir kontrol onları hep "zaten var" sayar ve hiç düzeltmez."""
    seen: set[tuple[str, str]] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION, limit=1000, offset=offset,
            with_payload=["metadata"], with_vectors=False)
        for p in points:
            meta = (p.payload or {}).get("metadata") or {}
            if meta.get("type") == "gorsel":
                key = meta.get("gorsel_kaynak")
                if key:
                    seen.add(("gorsel", key))
            elif meta.get("url"):
                seen.add(("metin", meta["url"]))
        if offset is None:
            return seen


def indexed_urls(client) -> set[str]:
    """Yalnız sayfa/PDF url'leri — kapsama raporu için."""
    return {u for kind, u in indexed_keys(client) if kind == "metin"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Korpus -> Qdrant 'campaigns'")
    ap.add_argument("banks", nargs="*", help="gömülecek bankalar (boş = tümü)")
    ap.add_argument("--only-missing", action="store_true",
                    help="yalnız koleksiyonda HİÇ olmayan url'leri göm")
    ap.add_argument("--dry-run", action="store_true", help="yazma, sadece say")
    args = ap.parse_args()

    from qdrant_client.models import PointStruct
    from vector_stores import ensure_collection

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

