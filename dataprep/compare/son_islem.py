"""TABLO SONRASI PİPELİNE — tablo üretimi bittikten sonra çalışan kalıcı akış.

pipeline.py (tablo üretimi) bittiğinde tablolar HAM haldedir: mükerrerler
ayrı ayrı durur, bazı kaynakların bilgisi tabloya işlenmemiş olabilir,
hücrelerde geçerlilik tarihi yoktur ve süresi geçmiş bilgiler hâlâ geçerli
görünür. Bu modül o dört eksiği SIRAYLA kapatır:

  1) dedup          — aynı ürün/kampanyayı kıyaslayan tabloları TEK tabloda
                      birleştirir (veri kaybetmeden, gerekirse sütun ekleyerek).
                      EN BAŞTA olmalı: sonraki adımlar daha az tablo üzerinde
                      çalışır ve birleşmiş tabloya tek sefer tarih basılır.
  2) tablo_denetim  — her kaynak URL'i tek tek denetler, tabloya işlenmemiş
                      bilgi varsa tamamlar.
  3) tablo_tarih    — her hücreye KENDİ kaynağından geçerlilik damgası basar.
                      Kod ile bulunamayan hücreler için sayfa kapsamlı
                      ARAŞTIRMA ajanına sorar (tarih_ajan): sayfanın metnini
                      parça parça tarar, tarih metinde yoksa SAYFADAKİ
                      GÖRSELLERİ inceler. DENETİMDEN SONRA olmalı: denetim
                      yeni kaynak ekleyebilir, onların da tarihi basılsın.
  4) expire_check   — tüm kaynakları süresi geçmiş hücreleri işaretler.
                      Damga basılmadan süre değerlendirilemez.
  5) sunulmuyor_temizle — 'Sunulmuyor' iddialarını '-' yapar ve o hücrelerin
                      kaynağını siler (sunulmayan bir şeyin kaynağı olmaz).
                      EN SONDA: önceki adımlar yeni
                      'Sunulmuyor' üretebildiği için temizlik en son yapılmalı.

Sıra rastgele değildir; her adım bir öncekinin çıktısını kullanır.

Kullanım:
  python -m dataprep.compare.son_islem                 # dördü sırayla
  python -m dataprep.compare.son_islem --adim tarih    # tek adım
  python -m dataprep.compare.son_islem --atla dedup    # bir adımı atla
  python -m dataprep.compare.son_islem --kuru          # denetimi yazmadan dene
"""
from __future__ import annotations

import argparse
import logging
import time

log = logging.getLogger("dataprep.compare.son_islem")

ADIMLAR = ("dedup", "denetim", "tarih", "expire", "sunulmuyor")


def _dedup() -> None:
    from . import dedup
    dedup.main()


def _denetim(kuru: bool = False) -> None:
    import sys
    from . import tablo_denetim
    eski = sys.argv
    sys.argv = ["tablo_denetim"] + (["--kuru"] if kuru else [])
    try:
        tablo_denetim.main()
    finally:
        sys.argv = eski


def _tarih() -> None:
    import sys
    from . import tablo_tarih
    eski = sys.argv
    sys.argv = ["tablo_tarih"]        # LLM varsayılan AÇIK (tarihler kritik)
    try:
        tablo_tarih.main()
    finally:
        sys.argv = eski


def _expire() -> None:
    from . import expire_check
    expire_check.main()


def _sunulmuyor() -> None:
    import sys
    from . import sunulmuyor_temizle
    eski = sys.argv
    sys.argv = ["sunulmuyor_temizle"]
    try:
        sunulmuyor_temizle.main()
    finally:
        sys.argv = eski


def calistir(adimlar: list[str], kuru: bool = False) -> None:
    toplam_bas = time.time()
    for ad in adimlar:
        log.info("=" * 70)
        log.info("ADIM: %s", ad)
        log.info("=" * 70)
        bas = time.time()
        try:
            if ad == "dedup":
                _dedup()
            elif ad == "denetim":
                _denetim(kuru=kuru)
            elif ad == "tarih":
                _tarih()
            elif ad == "expire":
                _expire()
            elif ad == "sunulmuyor":
                _sunulmuyor()
            else:
                log.warning("bilinmeyen adım: %s", ad)
                continue
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # Bir adım patlarsa SONRAKİLER YİNE ÇALIŞIR: yarım kalan bir
            # birleştirme yüzünden tarih damgasının hiç basılmaması, elde
            # olanı da kaybetmek demek olurdu.
            log.error("[ADIM HATASI] %s: %s: %s — sonraki adıma geçiliyor",
                      ad, type(exc).__name__, exc, exc_info=True)
        log.info("ADIM BİTTİ: %s (%.1f dk)", ad, (time.time() - bas) / 60)
    log.info("=" * 70)
    log.info("TABLO SONRASI PİPELİNE BİTTİ (%.1f dk)", (time.time() - toplam_bas) / 60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Tablo üretimi sonrası bakım pipeline'ı")
    ap.add_argument("--adim", choices=ADIMLAR, action="append",
                    help="yalnız bu adım(lar) (birden çok kez verilebilir)")
    ap.add_argument("--atla", choices=ADIMLAR, action="append", default=[],
                    help="bu adımı atla")
    ap.add_argument("--kuru", action="store_true",
                    help="denetim adımını yazmadan raporla")
    a = ap.parse_args()

    adimlar = list(a.adim) if a.adim else [x for x in ADIMLAR if x not in a.atla]
    log.info("Çalıştırılacak adımlar: %s", " -> ".join(adimlar))
    calistir(adimlar, kuru=a.kuru)


if __name__ == "__main__":
    main()
