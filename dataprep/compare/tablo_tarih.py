"""HÜCRE BAZLI TARİH DAMGASI — her hücrenin KENDİ URL'inden geçerlilik tarihi.

FORMAT — tek metin alanı:
    "01/08/2026 - 30/09/2026"   başlangıç VE bitiş varsa
    "01/08/2026 - ?"            sadece BAŞLANGIÇ varsa (bitiş bilinmiyor)
    "? - 30/09/2026"            sadece BİTİŞ varsa (başlangıç bilinmiyor)
    "-"                         hiç tarih yoksa

Tek tarih neden yalnız yazılmıyor: "01/08/2026" tek başına tabloya bakan
insana bunun başlangıç mı bitiş mi olduğunu SÖYLEMEZ. Eksik tarafa "?"
koyunca konum anlamı taşır ve belirsizlik kalmaz.

NEDEN HÜCRE BAZLI: bir tablo satırı (banka) birden çok kaynaktan derlenir;
"kâr payı oranı" sütunu bir kampanya sayfasından, "ücret" sütunu başka bir
belgeden gelebilir ve GEÇERLİLİKLERİ FARKLIDIR. Satıra tek tarih yazmak
hücrelerin çoğu için yanlış olurdu. Bu yüzden tarih, hücrenin KENDİ
kaynağından (cell_sources[banka][sütun][*].url) okunur.

TARİH NEREDEN GELİR (sırayla, ilk dolu olan kazanır):
  1. cell_sources kaydındaki gecerlilik_baslangic/bitis — aşama 2/3'te LLM
     zaten okumuştu, en güvenilir ve BEDAVA.
  2. URL'in content/*.md karşılığı: frontmatter, yoksa metinden deterministik
     çıkarım (corpus.dates.extract) — LLM YOK.
  3. LLM (yalnızca --llm ile): 1 ve 2 boş kaldıysa belgeyi modele sorar.

Birden çok kaynak varsa: EN GENİŞ aralık alınır (en erken başlangıç, en geç
bitiş) — hücre o kaynakların birleşiminden üretildiği için.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlsplit

from corpus import dates as corpus_dates
from dataprep.pages import _split_front

log = logging.getLogger("dataprep.compare.tablo_tarih")

TARIH_SUTUNU_SONEKI = " (Geçerlilik)"     # "Kâr Payı" -> "Kâr Payı (Geçerlilik)"
YOK = "-"                                 # hiçbir tarih bulunamadı
BILINMIYOR = "?"                          # aralığın o tarafı belgede yazmıyor

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_KOK = Path(__file__).resolve().parents[2] / "data"
# Tarih hücreleri LLM ile sorulurken kaç hücre paralel gitsin
# (NET_SEM zaten global tavanı koruyor, bu sadece iş dağıtımı).
TARIH_WORKERS = int(os.environ.get("TARIH_WORKERS", "10"))


def iso_to_ggaayyyy(iso: str) -> str:
    """2026-09-30 -> 30/09/2026. Geçersiz/boş -> ""."""
    m = _ISO.match((iso or "").strip())
    if not m:
        return ""
    y, ay, gun = m.groups()
    return f"{gun}/{ay}/{y}"


def bicimle(baslangic: str, bitis: str) -> str:
    """İki ISO tarihten istenen TEK metin (bkz. modül docstring).

    Eksik taraf BILINMIYOR ("?") ile yazılır — tek tarih yalnız bırakılırsa
    okuyan onun başlangıç mı bitiş mi olduğunu anlayamaz."""
    b = iso_to_ggaayyyy(baslangic)
    s = iso_to_ggaayyyy(bitis)
    if b and s:
        return f"{b} - {s}"
    if b:
        return f"{b} - {BILINMIYOR}"
    if s:
        return f"{BILINMIYOR} - {s}"
    return YOK


def _front_alan(front: str, anahtar: str) -> str:
    m = re.search(rf'^{anahtar}:\s*"?([^"\n]*)"?\s*$', front, re.M)
    return (m.group(1) or "").strip() if m else ""


_url_cache: dict[str, tuple[str, str]] = {}


def url_icin_tarih(url: str) -> tuple[str, str]:
    """Bir URL'in content/*.md karşılığından (başlangıç, bitiş) ISO çifti.

    Aynı URL tabloda defalarca geçtiği için sonuç cache'lenir."""
    if not url:
        return "", ""
    if url in _url_cache:
        return _url_cache[url]
    sonuc = ("", "")
    p = _url_md_yolu(url)
    if p is not None:
        try:
            metin = p.read_text(encoding="utf-8")
            # ORTAK ayrıştırıcı (pages._split_front): sınır SATIR BAZLI
            # bulunur. Eski `split("---", 2)` metnin İÇİNDEKİ "---" dizisinde
            # bölüyordu — kuveytturk PDF adlarında "---" geçtiği için
            # frontmatter kesiliyor ve GEÇERLİLİK TARİHLERİ okunamıyordu
            # (tarih sütunu sessizce "-" kalıyordu).
            front, govde = _split_front(metin)
            b = _front_alan(front, "gecerlilik_baslangic")
            s = _front_alan(front, "gecerlilik_bitis")
            if not (b or s):
                # frontmatter boş -> metinden deterministik çıkarım (LLM YOK)
                try:
                    b, s, _ = corpus_dates.extract(govde)
                except Exception:
                    b, s = "", ""
            sonuc = (b or "", s or "")
        except Exception:
            pass
    _url_cache[url] = sonuc
    return sonuc


_katalog_cache: dict[str, dict] = {}


def _url_md_yolu(url: str) -> Path | None:
    """URL -> data/<banka>_site/content/<...>.md yolu (katalog üzerinden)."""
    for site in sorted(_KOK.glob("*_site")):
        cat_p = site / "_catalog.json"
        if not cat_p.exists():
            continue
        cat = _katalog_cache.get(site.name)
        if cat is None:
            try:
                cat = json.loads(cat_p.read_text(encoding="utf-8"))
            except Exception:
                cat = {}
            _katalog_cache[site.name] = cat
        rec = cat.get(url)
        if not rec:
            continue
        yol = rec.get("path") or ""
        if yol.lower().endswith(".pdf"):
            yol = yol[:-4] + ".md"
        p = site / "content" / yol
        return p if p.exists() else None
    # katalogda yoksa: URL yolundan tahmin (nadiren gerekir)
    ad = unquote(urlsplit(url).path).strip("/").split("/")[-1]
    if ad:
        for p in _KOK.glob(f"*_site/content/**/{ad}.md"):
            return p
    return None


def _en_genis(araliklar: list[tuple[str, str]]) -> tuple[str, str]:
    """Birden çok kaynak -> en erken başlangıç + en geç bitiş."""
    bas = [b for b, _ in araliklar if b]
    bit = [s for _, s in araliklar if s]
    return (min(bas) if bas else "", max(bit) if bit else "")


def hucre_tarihi(kaynaklar: list, llm: bool = False) -> str:
    """Bir HÜCRENİN kaynaklarından istenen formatta tarih metni."""
    if not kaynaklar:
        return YOK
    araliklar: list[tuple[str, str]] = []
    for src in kaynaklar:
        if not isinstance(src, dict):
            continue
        # 1) kaynak kaydının kendisi (bedava, en güvenilir)
        b = (src.get("gecerlilik_baslangic") or "").strip()
        s = (src.get("gecerlilik_bitis") or "").strip()
        if not (b or s):
            # 2) URL'in belgesinden (frontmatter -> metinden çıkarım)
            b, s = url_icin_tarih(src.get("url") or "")
        if not (b or s) and llm:
            # 3) son çare: modele sor (aynı URL birden çok hücrede geçebilir —
            # önbellek sayesinde belge yalnız BİR kez modele gider)
            b, s = _llm_tarih_onbellekli(src.get("url") or "")
        if b or s:
            araliklar.append((b, s))
    if not araliklar:
        return YOK
    return bicimle(*_en_genis(araliklar))


def _kalici_hata(exc: Exception) -> bool:
    """400/403 KALICI DEĞİL: tünel soketi bayatlayınca nginx reddediyor ama
    aynı istek saniyeler sonra 200 dönüyor (tablo_denetim ile AYNI karar)."""
    s = str(exc)
    return any(k in s for k in ("401", "404", "413", "422", "BadRequest"))


def _llm_tarih(url: str, baglam: str = "") -> tuple[str, str]:
    """Sayfa kapsamlı ARAŞTIRMA ajanına sorar (tarih_ajan.sayfa_tarihi).

    Ajan yalnız bu URL'in içinde kalır (kullanıcı kararı 2026-08-26): sayfanın
    tam metnini okur, tarih metinde yoksa SAYFADAKİ GÖRSELLERİ inceler
    (kampanya tarihleri sık sık yalnız görselde yazar), gerekirse parça parça
    ilerler. Dayanıklılık ajanın kendi içinde (sınırsız retry + tünel)."""
    if not url:
        return "", ""
    from .tarih_ajan import sayfa_tarihi
    try:
        return sayfa_tarihi(url, baglam=baglam)
    except Exception as exc:
        log.warning("    tarih ajanı hata verdi (%s): %s", url, exc)
        return "", ""


_TARIH_ONBELLEK: dict[str, tuple[str, str]] = {}
_ONBELLEK_KILIT = __import__("threading").Lock()


def _llm_tarih_onbellekli(url: str) -> tuple[str, str]:
    """Aynı belge birden çok hücrede kaynak olabilir; modele bir kez sorulur."""
    if not url:
        return "", ""
    with _ONBELLEK_KILIT:
        if url in _TARIH_ONBELLEK:
            return _TARIH_ONBELLEK[url]
    sonuc = _llm_tarih(url)
    with _ONBELLEK_KILIT:
        _TARIH_ONBELLEK[url] = sonuc
    return sonuc


def _llm_gerekenler(tablo: dict) -> list[str]:
    """Bu tabloda kod ile tarihi bulunamayan, yani LLM'e gidecek URL'ler."""
    gerekli: list[str] = []
    for _banka, sutunlar in (tablo.get("cell_sources") or {}).items():
        for _c, kaynaklar in (sutunlar or {}).items():
            for src in kaynaklar or []:
                if not isinstance(src, dict):
                    continue
                if (src.get("gecerlilik_baslangic") or "").strip():
                    continue
                if (src.get("gecerlilik_bitis") or "").strip():
                    continue
                url = (src.get("url") or "").strip()
                if not url:
                    continue
                b, s = url_icin_tarih(url)
                if b or s:
                    continue
                gerekli.append(url)
    return gerekli


def on_isit(tablolar: list[dict]) -> int:
    """LLM'e gidecek TÜM belgeleri PARALEL sorup önbelleğe doldurur.

    Damgalama sonra bu önbellekten okur; böylece hücre hücre seri beklemek
    yerine NET_SEM tavanına kadar eşzamanlı ilerlenir. Hiçbir veri kırpılmaz,
    yalnız işin sırası değişir."""
    hedef: list[str] = []
    gorulen: set[str] = set()
    for t in tablolar:
        for url in _llm_gerekenler(t):
            if url not in gorulen:
                gorulen.add(url)
                hedef.append(url)
    if not hedef:
        return 0
    log.info("LLM'e sorulacak benzersiz belge: %d (paralel %d)", len(hedef), TARIH_WORKERS)
    bitti = 0
    with ThreadPoolExecutor(max_workers=TARIH_WORKERS) as ex:
        isler = {ex.submit(_llm_tarih_onbellekli, u): u for u in hedef}
        for f in as_completed(isler):
            f.result()
            bitti += 1
            if bitti % 50 == 0 or bitti == len(hedef):
                log.info("  tarih sorgusu %d/%d", bitti, len(hedef))
    return len(hedef)


def tabloyu_damgala(tablo: dict, llm: bool = False) -> bool:
    """HER hücre için '<sütun> (Geçerlilik)' sütununu ekler/günceller.

    Kaynağı olmayan hücre de ALAN ALIR ("-") — "her tabloda her satırda tarih
    olsun" kuralı: alan hiç yok değil, boşsa '-' yazar."""
    satirlar = tablo.get("rows") or {}
    if not satirlar:
        return False
    sutunlar = list(tablo.get("columns") or [])
    hucre_kaynak = tablo.get("cell_sources") or {}
    veri_sutunlari = [c for c in sutunlar if not c.endswith(TARIH_SUTUNU_SONEKI)]
    degisti = False

    for c in veri_sutunlari:
        tc = c + TARIH_SUTUNU_SONEKI
        if tc not in sutunlar:
            sutunlar.insert(sutunlar.index(c) + 1, tc)   # veri sütununun HEMEN yanına
            degisti = True

    for banka, hucreler in satirlar.items():
        if not isinstance(hucreler, dict):
            continue
        banka_kaynak = hucre_kaynak.get(banka) or {}
        for c in veri_sutunlari:
            tc = c + TARIH_SUTUNU_SONEKI
            yeni = hucre_tarihi(banka_kaynak.get(c) or [], llm=llm)
            if hucreler.get(tc) != yeni:
                hucreler[tc] = yeni
                degisti = True
    if degisti:
        tablo["columns"] = sutunlar
    return degisti


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(
        description="Tablo HÜCRELERİNE kendi URL'lerinden gg/aa/yyyy geçerlilik tarihi damgala")
    # Tarihler kritik (kullanıcı kararı 2026-08-26): kod ile bulunamayan her
    # hücre için LLM'e SORULUR. Kapatmak isteyen --llm-yok verir.
    ap.add_argument("--llm-yok", dest="llm", action="store_false", default=True,
                    help="LLM'e hiç sorma, yalnız kod ile bulunanları yaz")
    ap.add_argument("--dizin", default=None, help="tablo JSON dizini")
    args = ap.parse_args()

    from dataprep.compare import store
    kok = Path(args.dizin) if args.dizin else store.ROOT
    if not kok.exists():
        log.warning("tablo dizini yok: %s", kok)
        return
    dosyalar = sorted(p for p in kok.glob("*.json") if not p.name.startswith("_"))
    log.info("%d tablo damgalanacak (LLM=%s)", len(dosyalar), "açık" if args.llm else "kapalı")

    yuklu: list[tuple[Path, dict]] = []
    for p in dosyalar:
        try:
            yuklu.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except Exception as exc:
            log.warning("  %s okunamadı: %s", p.name, exc)

    # LLM'e gidecek belgeleri ÖNCE paralel sor: damgalama sonra önbellekten
    # okuyup hızlıca ilerler (hücre hücre seri beklemek yerine).
    if args.llm:
        on_isit([t for _p, t in yuklu])

    degisen = bos = dolu = 0
    for p, tablo in yuklu:
        if tabloyu_damgala(tablo, llm=args.llm):
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(tablo, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)                     # atomik
            degisen += 1
        for hucreler in (tablo.get("rows") or {}).values():
            for k, v in (hucreler or {}).items():
                if k.endswith(TARIH_SUTUNU_SONEKI):
                    if v == YOK:
                        bos += 1
                    else:
                        dolu += 1
    log.info("BİTTİ: %d/%d tablo güncellendi | tarihli hücre: %d, tarihsiz('-'): %d",
             degisen, len(dosyalar), dolu, bos)


if __name__ == "__main__":
    main()
