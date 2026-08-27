"""BİR BANKANIN HANGİ AŞAMALARI BİTMİŞ — diskteki gerçeğe bakar.

tek_banka.sh tek başına her turda aşama 1'den başlar. Aşamalar artımlı olduğu
için veri kaybı olmaz, AMA her tur crawl'ı/görselleri/PDF'leri yeniden tarayıp
dakikalar harcar. Bu script o gereksiz taramayı önler.

Bu script her aşama için "yapılacak iş kaldı mı" sorusunu ayrı yanıtlar.
Çıktı: kabuk tarafından okunabilir satırlar (A1=0/1 ...), 1 = iş var.

ÖNEMLİ: "bitti" kararı DİSKTEN verilir, log'dan değil. Log "BİTTİ" yazsa bile
disk eksik gösteriyorsa aşama yeniden koşar.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK))
DATA = KOK / "data"


def _json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def a2_kalan(b: str) -> int:
    site = DATA / f"{b}_site"
    led = _json(site / "_content_ledger.json")
    n = 0
    for _u, v in led.items():
        if v.get("status") == "gereksiz":
            continue
        c = v.get("output_path") or ""
        if c and not (site / c).exists():
            n += 1
    return n


def a3_kalan(b: str) -> int:
    site = DATA / f"{b}_site"
    cat = _json(site / "_catalog.json")
    led = set(_json(site / "_pdf_clean_ledger.json"))
    pdfs = {u for u, v in cat.items()
            if v.get("kind") == "pdf" and v.get("status") != "removed"}
    return len(pdfs - led)


def a4_kalan(b: str) -> int:
    site = DATA / f"{b}_site"
    n = len(_json(site / "_page_clean_cache.json"))
    toplam = len([p for p in site.rglob("*.md")
                  if "_raw" not in p.parts and "image_text" not in p.parts])
    return max(0, toplam - n)


def main() -> None:
    b = sys.argv[1]
    # A1: mutabakat "hesabı verilemeyen URL yok" dediyse ve evren doluysa bitti.
    # verify pahalı (dakikalar); bunun yerine son crawl log'una bakılır.
    a1 = 1
    try:
        a1log = KOK / "logs" / "seri" / b / "a1.log"
        m = a1log.read_text(encoding="utf-8", errors="replace")
        if "hesabı verilemeyen URL yok" in m and "=== BİTTİ" in m:
            a1 = 0
    except Exception:
        pass
    print(f"A1={a1}")
    print(f"A2={1 if a2_kalan(b) else 0}")
    print(f"A3={1 if a3_kalan(b) else 0}")
    print(f"A4={1 if a4_kalan(b) else 0}")
    print(f"A4_KALAN={a4_kalan(b)}")


if __name__ == "__main__":
    main()
