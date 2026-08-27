"""'Sunulmuyor' değerlerini '-' yapar ve o hücrelerin kaynağını temizler.

GEREKÇE: tablo üretiminde ajan bir bankanın ürünü sunmadığını düşününce
hücreye 'sunulmuyor' yazıyordu. Ama bu bir İDDİADIR: çoğu zaman ajan bilgiyi
BULAMADIĞI için yazıyor, ürünün gerçekten olmadığını kanıtladığı için değil.
Yarışma tablosunda yanlış "sunmuyor" iddiası, boş hücreden daha kötüdür.
'-' ise nötr: "burada veri yok" der, iddia taşımaz.

Üç iş yapılır:
  1. 'sunulmuyor' / 'yok' / 'mevcut değil' TAM EŞLEŞEN hücreler -> '-'
     (cümle İÇİNDE geçenlere DOKUNULMAZ: "X sunulmuyor ama Y sunuluyor"
     gibi gerçek bilgi taşıyan metinler korunur)
  2. O hücrenin cell_sources kaydı SİLİNİR — sunulmayan şeyin kaynağı olmaz
  3. Bir bankanın TÜM veri sütunları '-' ise o satırın sources/referansı
     tamamen boşaltılır

Kullanım:
  python -m dataprep.compare.sunulmuyor_temizle           # uygula
  python -m dataprep.compare.sunulmuyor_temizle --kuru    # yazmadan raporla
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("dataprep.compare.sunulmuyor_temizle")

# TAM eşleşme (cümle içinde geçenler korunur — onlar gerçek bilgi taşır)
KALIP = re.compile(r"^\s*(sunulmuyor|sunulmamaktadır|sunulmamakta|"
                   r"mevcut değil|mevcut degil|yok)\s*\.?\s*$", re.I)
YOK = "-"
TARIH_SONEKI = " (Geçerlilik)"


def _veri_sutunu(ad: str) -> bool:
    return not (ad.endswith(TARIH_SONEKI) or ad == "Geçerlilik")


def temizle(tablo: dict) -> tuple[int, int, int]:
    """(değişen hücre, silinen kaynak, boşaltılan satır)"""
    rows = tablo.get("rows") or {}
    cell_sources = tablo.get("cell_sources") or {}
    sources = tablo.get("sources") or {}
    hucre = kaynak = satir = 0

    for banka, hucreler in rows.items():
        if not isinstance(hucreler, dict):
            continue
        banka_kaynak = cell_sources.get(banka) or {}
        for sutun, deger in list(hucreler.items()):
            if not _veri_sutunu(sutun):
                continue
            if not KALIP.match(str(deger or "")):
                continue
            hucreler[sutun] = YOK
            hucre += 1
            # sunulmayan seyin kaynagi olmaz
            if sutun in banka_kaynak:
                kaynak += len(banka_kaynak[sutun] or [])
                del banka_kaynak[sutun]

        # TUM veri sutunlari '-' ise referansi tamamen bosalt
        veriler = [v for k, v in hucreler.items() if _veri_sutunu(k)]
        if veriler and all(str(v or "").strip() in ("", YOK) for v in veriler):
            if banka_kaynak:
                kaynak += sum(len(v or []) for v in banka_kaynak.values())
                cell_sources[banka] = {}
            if sources.get(banka):
                sources[banka] = []
            satir += 1
            # tarih damgalari da anlamsiz kalir
            for k in list(hucreler):
                if not _veri_sutunu(k):
                    hucreler[k] = YOK
    return hucre, kaynak, satir


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--kuru", action="store_true", help="yazma, raporla")
    ap.add_argument("--dizin", default=None)
    a = ap.parse_args()

    from . import store
    kok = Path(a.dizin) if a.dizin else store.ROOT
    dosyalar = sorted(p for p in kok.glob("*.json") if not p.name.startswith("_"))
    th = tk = ts = td = 0
    for p in dosyalar:
        try:
            tablo = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("  %s okunamadı: %s", p.name, exc)
            continue
        h, k, s = temizle(tablo)
        if h or k or s:
            td += 1
            th += h; tk += k; ts += s
            if not a.kuru:
                tmp = p.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(tablo, ensure_ascii=False, indent=1),
                               encoding="utf-8")
                tmp.replace(p)              # atomik
    log.info("%s: %d/%d tablo | %d hücre '-' | %d kaynak silindi | %d satır boşaltıldı",
             "KURU" if a.kuru else "BİTTİ", td, len(dosyalar), th, tk, ts)


if __name__ == "__main__":
    main()
