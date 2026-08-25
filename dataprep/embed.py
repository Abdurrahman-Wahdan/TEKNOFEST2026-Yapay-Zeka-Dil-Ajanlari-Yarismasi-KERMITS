"""LLM-friendly veriyi Qdrant 'campaigns' koleksiyonuna göm — yeni korpus düzeni.

Kaynak (her banka <slug>_site altında):
  * content/**/*.md    : temizlenmiş sayfa metni  -> type=metin
  * _pdf_clean/**/*.md : temizlenmiş PDF metni    -> type=metin
  * HİÇBİR ŞEY ELENMEZ: ilgililik/uzunluk kararlarını üretim hattı zaten verdi.

`_raw/` GÖMÜLMEZ: temizlik öncesi ham kazıma çıktısıdır, aynı sayfanın ikinci ve
daha kötü bir kopyasıdır. Kök/iç içe *.md sayfaları da GÖMÜLMEZ: 4261'inin
4258'i zaten content/ altında, yani onları da almak her sayfayı iki kez
indekslemek olurdu.

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
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from qdrant_client import models

from embeddings import get_embedding
from vector_stores.client import get_qdrant_client

log = logging.getLogger("dataprep.embed")

CORPUS = Path(os.environ.get("CORPUS_DIR")
              or Path(__file__).resolve().parents[1] / "TF26_data" / "data")
COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")
URL_POOL = "_tables/_url_havuzu.json"

# Canlı koleksiyondan ölçüldü: chunk uzunluğu medyan 1391, p95 8171, maks 9017
# karakter; 5081 url'in 3894'ü TEK chunk. Yani üretici ~9000'lik bir tavanla
# paragraf duyarlı bölmüş. Aynı tavanı kullanıyoruz — 900'lük eski değer aynı
# sayfayı on parçaya bölerdi ve yan yana duran iki kuşak chunk birbirine hiç
# benzemezdi. Qwen3-Embedding'in penceresi 32k, 9000 karakter rahat sığıyor.
CHUNK = 9000
OVERLAP = 0
MIN_CHUNK = 0        # eşik yok: ne kadar kısa olursa olsun gömülür
# Görsel blokları için AYRI ve daha düşük bir taban. Bir afiş yazısı doğası
# gereği kısa ("Toplam 5.000TL'ye kadar bonus kazan!" 36 karakter) ve sayfa
# metnine uygulanan 40'lık eşik bunların 245'ini eliyordu. 15, koleksiyonun
# KENDİ tabanı: içindeki en kısa chunk 15 karakter. Bunun altı ("ÜCRETSİZ",
# "5 TAKSİT") tek başına aranabilir bir bilgi taşımıyor -- her sorguya eşit
# uzaklıkta duran, hiçbir şey söylemeyen bir chunk olurdu.
MIN_IMG_CHUNK = 0    # eşik yok
# Sunucu eşzamanlı istek kaldırıyor; tek tek göndermek onu boşta bekletiyordu.
# İkisi de ortam değişkeniyle ayarlanabilir -- sunucu yükü değişince kodu
# değiştirmek gerekmesin.
BATCH = int(os.environ.get("EMBED_BATCH", "64"))
WORKERS = int(os.environ.get("EMBED_WORKERS", "8"))

_NS = uuid.UUID("1f7c1a6e-6d1b-4a53-9f2c-0d5b8e4a7c31")   # url->point id için sabit

# Gövdesi boş olduğu için gömülemeyen url'ler — koşu sonunda raporlanır.
EMPTY: list[str] = []

# Artık kullanılmıyor: hiçbir görsel bloğu uzunluğu yüzünden elenmiyor.
SHORT: list[str] = []


def point_id(url: str, chunk_index: int) -> str:
    """Aynı chunk her koşuda aynı id. Bkz. modül başlığı, madde 2."""
    return str(uuid.uuid5(_NS, f"{url}#{chunk_index}"))


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
    """(frontmatter dict, gövde). Frontmatter yoksa ({}, text)."""
    match = _FRONT.match(text)
    if not match:
        return {}, text
    fm: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, text[match.end():].lstrip("\n")


def _chunks(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[str]:
    """Paragraf-duyarlı chunk: bloklar (boş satırla ayrılan) birleştirilir; tek blok
    büyükse cümle/satırdan bölünür. Sonda overlap ile bağlam korunur."""
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


def all_banks() -> list[str]:
    return sorted(p.name[:-5] for p in CORPUS.glob("*_site"))


# Sayfa metnine gömülü görsel blokları: `<!-- görsel: URL -->` işaretçisi ve onu
# izleyen, o görselden okunan metin. Bir sonraki işaretçiye (ya da dosya sonuna)
# kadar sürer.
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


def iter_docs(bank: str, pool: dict[str, dict]):
    """Bu bankanın gömülecek kayıtları: (anahtar, point_id, metin, metadata).

    `anahtar` = (tür, kimlik) — eksik-kontrolü bunun üzerinden yapılır. Bir sayfa
    için ("metin", sayfa_url), bir görsel için ("gorsel", görsel_url): sayfa
    zaten indekslenmişken görselinin eksik olması mümkün, ve yalnız sayfa url'ine
    bakan bir kontrol o görseli sonsuza kadar gözden kaçırırdı."""
    site = CORPUS / f"{bank}_site"
    # `pages`: content/ altında kopyası OLMAYAN kök sayfalar. 4261 kök sayfanın
    # 4258'i zaten content/ altında; kalan 3'ü hiçbir yerde değildi ve ikisinin
    # gerçek içeriği vardı (biri 1997, biri 2053 karakter).
    content_urls = {
        (_parse(p.read_text(encoding="utf-8", errors="replace"))[0].get("url") or "").strip()
        for p in (site / "content").rglob("*.md")
    } if (site / "content").exists() else set()

    # content/ ÖNCE, ve bir url oradan geldiyse _pdf_clean/ kopyası ATLANIR.
    # İkisi aynı url'i taşıyabiliyor (aynı PDF'in iki aşaması) ve point id'si
    # (url, chunk_index)'ten türediği için ikisi AYNI id'ye düşüyor. Sonuç:
    # çakışma, ve `as_completed` sırası yüzünden hangisinin kazandığı koşudan
    # koşuya değişiyor. Ölçüldü: adilkatilim'de 19 chunk yazılıyor, koleksiyonda
    # 14 point kalıyordu ve kazanan taraf `_pdf_clean`'in KISALTILMIŞ kopyasıydı
    # -- 1391 karakterlik sayfa 1126 karaktere düşüyor, sonundaki adres bloğu
    # kayboluyordu. content/ üretim hattının nihai çıktısı, _pdf_clean ara adım.
    taken: set[str] = set()
    for sub in ("content", "_pdf_clean", None):
        if sub is None:
            paths = [p for p in sorted(site.rglob("*.md"))
                     if not any(part in ("content", "_raw", "_pdf_clean") for part in p.parts)]
        else:
            base = site / sub
            if not base.exists():
                continue
            paths = sorted(base.rglob("*.md"))
        for path in paths:
            try:
                fm, body = _parse(path.read_text(encoding="utf-8"))
            except OSError:
                log.warning("okunamadı: %s", path)
                continue
            url = (fm.get("url") or "").strip()
            if not url:
                log.warning("url'süz dosya atlandı: %s", path)
                continue
            if url in taken:
                continue          # bu url'in daha iyi kopyası zaten alındı
            taken.add(url)
            body, images = split_images(body)
            if not body.strip() and not images:
                # Frontmatter var, gövde YOK. Taranmış/imza görüntüsü olan
                # PDF'ler: temizleyici hiç metin çıkaramamış. Gömecek bir şey
                # yok — ama SESSİZCE atlanmıyor, kapsama raporunda sayılıyor,
                # yoksa "%100 kapsandı" ile "bu dosyayı hiç görmedik" aynı
                # şeye benzer.
                EMPTY.append(url)
                continue
            rec = pool.get(url) or {}
            meta = {
                "bank": bank,
                "url": url,
                "type": "metin",
                "validity_status": (rec.get("validity_status")
                                    or fm.get("validity_status") or "bilinmiyor"),
            }
            for key in ("gecerlilik_baslangic", "gecerlilik_bitis"):
                value = (rec.get(key) or fm.get(key) or "").strip()
                if value:
                    meta[key] = value
            for i, chunk in enumerate(_chunks(body)):
                yield ("metin", url), point_id(url, i), chunk, {**meta, "chunk_index": i}
            for image_url, image_text in images:
                pieces = _chunks(image_text) or ([image_text.strip()] if image_text.strip() else [])
                if not pieces and image_text.strip():
                    SHORT.append(image_url)
                for i, chunk in enumerate(pieces):
                    yield (
                        ("gorsel", image_url),
                        point_id(image_url, i),
                        chunk,
                        {**meta, "type": "gorsel", "gorsel_kaynak": image_url, "chunk_index": i},
                    )


# --- yazma --------------------------------------------------------------------
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


def write(client, embedder, docs: list[tuple], dry_run: bool = False) -> int:
    """Chunk'ları göm ve yaz. Aynı url'in eski point'leri ÖNCE silinir."""
    if not docs:
        return 0
    keys = {d[0] for d in docs}
    if dry_run:
        log.info("[DENEME] %d kayıt / %d chunk yazılacaktı", len(keys), len(docs))
        return 0

    batches = [docs[i:i + BATCH] for i in range(0, len(docs), BATCH)]
    written = 0
    # Embedding sunucusu eşzamanlı istek kaldırıyor ve tek darboğaz o: bir batch
    # beklerken sıradakini göndermemek için sebep yok. Sıra ÖNEMSİZ -- her
    # chunk'ın id'si kendi (url, chunk_index) ikilisinden geliyor, hangi sırada
    # yazıldığı sonucu değiştirmiyor -- bu yüzden as_completed ile geldiği gibi
    # yazılır. Upsert'i worker içinde değil burada yapıyoruz: QdrantClient'ı tek
    # bir thread'den kullanmak, paylaşılan bir HTTP istemcisini kilitlemekten
    # daha ucuz ve yazma zaten hızlı taraf.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool_exec:
        pending = {pool_exec.submit(embedder.embed_documents, [d[2] for d in b]): b
                   for b in batches}
        for future in as_completed(pending):
            batch = pending[future]
            vectors = future.result()
            # wait=True: fire-and-forget gizli veri kaybıdır. wait=False ile bir
            # koşu "19/19 chunk yazıldı" deyip koleksiyonda 14 point bırakabildi
            # -- hata da dönmedi, sayaç da doğru göründü. Yazmanın tamamlandığını
            # görmeden ilerlemiyoruz.
            client.upsert(collection_name=COLLECTION, wait=True, points=[
                models.PointStruct(
                    id=pid, vector=vec,
                    payload={"page_content": text, "metadata": meta})
                for (_key, pid, text, meta), vec in zip(batch, vectors)])
            written += len(batch)
            log.info("  %d/%d chunk yazıldı", written, len(docs))

    # ÖNCE YAZ, SONRA TEMİZLE. Tersi -- silip sonra yazmak -- bu korpusta
    # gerçek bir risk: tünel koşunun ortasında düşerse o url'in eski point'leri
    # gitmiş, yenileri hiç yazılmamış olur. Şimdi eski kayıtlar ancak yerlerine
    # yenisi GEÇTİKTEN sonra siliniyor; koşu yarıda kalırsa en kötü ihtimalle
    # eski kopya yerinde durur.
    #
    # Silinen: bu url'e/görsele ait olup yeni id kümesinde OLMAYAN her point --
    # yani üretim hattının rastgele uuid4'le yazdığı eski kopyalar ve sayfa
    # kısaldıysa artakalan fazla chunk'lar.
    fresh = {d[1] for d in docs}
    for field, kind in (("metadata.url", "metin"), ("metadata.gorsel_kaynak", "gorsel")):
        victims = sorted(u for k, u in keys if k == kind)
        if not victims:
            continue
        for i in range(0, len(victims), 200):
            existing, _ = client.scroll(
                collection_name=COLLECTION, limit=10000, with_payload=False, with_vectors=False,
                scroll_filter=models.Filter(must=[models.FieldCondition(
                    key=field, match=models.MatchAny(any=victims[i:i + 200]))]))
            drop = [p.id for p in existing if p.id not in fresh]
            if drop:
                client.delete(collection_name=COLLECTION, wait=True,
                              points_selector=models.PointIdsList(points=drop))
                log.info("  %d eski point temizlendi", len(drop))
    return written



# --- görsel önbelleğinden artakalanlar ---------------------------------------
def iter_cached_images(bank: str):
    """`_image_cache.json`'daki, hiçbir sayfada geçmeyen görsel metinleri.

    Üretim hattı HER görseli VLM'e gönderip sonucu buraya yazıyor (6263 kayıt,
    4989'u 'decorative' yani okunacak metin yok, 1274'ünde metin var). Bu
    metinlerin neredeyse tamamı sayfaların içine `<!-- görsel: URL -->` bloğu
    olarak da yazılmış durumda ve oradan gömülüyor. Birkaçı yazılmamış -- sayfası
    başka bir sebeple elenmiş olabilir -- ve yalnızca önbellekte duruyor.

    Onlar da gömülür. Karar üretim hattınındı: metni çıkardıysa gömülecek."""
    path = CORPUS / f"{bank}_site" / "_image_cache.json"
    if not path.exists():
        return
    for digest, rec in json.loads(path.read_text(encoding="utf-8")).items():
        text = (rec.get("content") or "").strip()
        if not text or rec.get("decorative"):
            continue
        # Önbellekte url yok, anahtar içerik hash'i. Kimliği o hash olur --
        # deterministik, ve aynı görsel iki bankada da geçse tek point olur.
        key = f"imgcache:{digest}"
        meta = {"bank": bank, "url": key, "type": "gorsel",
                "gorsel_kaynak": key, "validity_status": "bilinmiyor"}
        for field in ("gecerlilik_baslangic", "gecerlilik_bitis"):
            if (rec.get(field) or "").strip():
                meta[field] = rec[field].strip()
        for i, chunk in enumerate(_chunks(text) or [text]):
            yield ("gorsel", key), point_id(key, i), chunk, {**meta, "chunk_index": i}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Korpus -> Qdrant 'campaigns'")
    ap.add_argument("banks", nargs="*", help="gömülecek bankalar (boş = tümü)")
    ap.add_argument("--only-missing", action="store_true",
                    help="yalnız koleksiyonda HİÇ olmayan url'leri göm")
    ap.add_argument("--dry-run", action="store_true", help="yazma, sadece say")
    args = ap.parse_args()

    log.info("korpus: %s -> koleksiyon: %s", CORPUS, COLLECTION)
    client = get_qdrant_client()
    pool = load_url_pool()
    known = indexed_keys(client) if args.only_missing else set()
    vdb_text = indexed_text(client) if args.only_missing else ""
    if args.only_missing:
        log.info("koleksiyonda hâlihazırda %d kayıt var (%d sayfa/PDF, %d görsel)",
                 len(known), sum(1 for k, _ in known if k == "metin"),
                 sum(1 for k, _ in known if k == "gorsel"))

    embedder = None if args.dry_run else get_embedding()
    total = 0
    embedded: set[str] = set()
    for bank in (args.banks or all_banks()):
        page_docs = list(iter_docs(bank, pool))
        # Önbellekteki görsel metinlerinin çok büyük kısmı sayfaların İÇİNE de
        # yazılmış ve oradan gömülüyor. Eşitlik kontrolü bunu yakalamaz: metin
        # sayfa chunk'ının İÇİNDE, kendi başına bir chunk değil. Bu yüzden
        # KAPSAMA kontrolü -- yoksa 679 kayıt ikinci kez yazılırdı.
        haystack = vdb_text + "\n" + "\n".join(" ".join(d[2].split()) for d in page_docs)
        leftovers = [d for d in iter_cached_images(bank)
                     if " ".join(d[2].split())[:120] not in haystack]
        docs = [d for d in page_docs + leftovers
                if not (args.only_missing and d[0] in known)]
        if not docs:
            continue
        log.info("%s: %d kayıt / %d chunk", bank, len({d[0] for d in docs}), len(docs))
        embedded |= {u for _k, u in {d[0] for d in docs}}
        total += write(client, embedder, docs, dry_run=args.dry_run)
    log.info("bitti: %d chunk", total)
    # EMPTY dosya sayar, url değil: aynı url hem content/ hem _pdf_clean/ altında
    # olabilir ve biri boşken diğeri dolu olabilir. Rapora yalnız HİÇBİR
    # kopyasından metin çıkmayanlar girer — asıl kapsanmayan küme budur.
    known_urls = {u for _kind, u in known}
    truly_empty = sorted(set(EMPTY) - embedded - known_urls)
    if truly_empty:
        log.info("hiçbir kopyasında metin olmadığı için gömülemeyen %d url "
                 "(taranmış/imza PDF'leri — çıkarılacak metin yok):", len(truly_empty))
        for url in truly_empty:
            log.info("   %s", url)


if __name__ == "__main__":
    main()

