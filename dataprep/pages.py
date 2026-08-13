"""Sayfa ham metnini LLM ile TEMİZLE — nav/footer/boilerplate at, gerçek içeriği çıkar.

Kural (regex/liste) YOK — GÖRSEL ve PDF aşamalarıyla TUTARLI biçimde LLM karar verir:
crawl trafilatura/ham-HTML ile sayfayı indirir (nav+footer+duyuru dahil), bu aşama her
sayfanın metnini LLM'e verip "bu sayfaya ÖZGÜ ürün/kampanya/hizmet içeriğini çıkar,
tekrarlayan site öğelerini (menü, footer, duyuru, sosyal medya, çerez) at" dedirtir.

İki yerde kullanılır:
  * POSTPROCESS: mevcut <bank>_site/*.md sayfalarını temizler (bu modülün process_bank'ı).
  * CRAWL-HOOK: store.fetch_and_store yeni sayfayı kaydetmeden clean_page ile temizler.

LLM ulaşılamazsa None döner -> ham metne DOKUNULMAZ (kayıp yok; sonra tekrar denenir).
Havuzlu vlm client (opn<100) + thread pool + url-cache resume + ledger.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dataprep import vlm
from dataprep.ledger import Ledger

log = logging.getLogger("dataprep.pages")

WORKERS = int(os.environ.get("PAGE_WORKERS", "40"))
MIN_LEN = 40           # bundan kısa gövdeye dokunma (zaten içerik yok/az)
CHUNK = 9000           # tek seferde LLM'e verilecek ham metin
OVERLAP = 800          # uzun sayfada parçalar arası bağlam (kusursuz dikiş)

_GOAL = (
    "Sen bir KATILIM BANKASI veri seti hazırlıyorsun. Aşağıdaki HAM metin bir web sayfası "
    "veya PDF'ten geliyor. MÜŞTERİNİN İŞİNE YARAYACAK katılım bankacılığı ÜRÜN ve KAMPANYA "
    "bilgilerini (hizmet, koşul, oran/ücret, kampanya detayı, avantaj, SSS) çıkar. Çıktı "
    "LLM-FRIENDLY olsun: net başlıklar (##/###), kısa ve öz paragraflar, HER BÖLÜM KENDİ "
    "İÇİNDE ANLAŞILIR (chunk'lara bölününce bütünlük bozulmaz). "
    "ÇOK ÖNEMLİ — VERİYİ DEĞİŞTİRME: tüm SAYILAR, ORAN/ÜCRET/TUTAR, tarih ve TABLOLAR "
    "AYNEN korunur (markdown tablo olarak); tek bir rakamı bile değiştirme/uydurma/atma. "
    "Sadece nav menüsü, footer, duyuru, sosyal medya, çerez, iletişim, sayfa numarası gibi "
    "TEKRARLAYAN/GEREKSİZ öğeleri AT ve yeniden düzenle. Gerçek ürün/kampanya içeriği yoksa "
    "BOŞ bırak. TERMİNOLOJİ: kurum bir KATILIM BANKASI'dır; metinde 'katılım bankası' / "
    "'katılım bankacılığı' ifadelerini kullan, genel 'banka' ifadesine İNDİRGEME.")

_CLEAN_Q = (_GOAL + "\nSayfa URL: {url}\n\nHam metin:\n\"\"\"{body}\"\"\"\n\n"
            'SADECE JSON: {{"content": "<LLM-friendly markdown ya da boş>"}}')
_CONT_Q = (_GOAL + "\nSayfa URL: {url}\n\nBu, uzun sayfanın DEVAM parçası. Önceki temiz "
           "çıktının sonu:\n\"\"\"{tail}\"\"\"\nBunu TEKRARLAMA; bu parçadaki YENİ ürün/"
           "kampanya içeriğini kaldığın yerden, akıcı biçimde ekle.\n\nHam metin (devam):"
           "\n\"\"\"{body}\"\"\"\n\nSADECE JSON: {{\"content\": \"<devam markdown ya da boş>\"}}")


def _clean_one(prompt: str) -> str | None:
    # max_tokens YÜKSEK: içeriği yarıda kesmesin (chunk zaten girdiyi ~9000 char sınırlıyor).
    d = vlm.call_json(vlm.txt_msg(prompt), max_tokens=8192)
    if not d:
        return None
    return (d.get("content") or "").strip()


def clean_page(raw_body: str, url: str = "") -> str | None:
    """Ham sayfa gövdesini LLM ile TEMİZLE + LLM-friendly yeniden yaz. Uzun sayfa parçalanıp
    overlap-bağlamla KUSURSUZ dikilir. Dönen: temiz markdown (boş olabilir) ya da None
    (LLM ulaşılamadı -> çağıran ham metni korusun)."""
    body = (raw_body or "").strip()
    if len(body) < MIN_LEN:
        return body
    if len(body) <= CHUNK:
        return _clean_one(_CLEAN_Q.format(url=url or "-", body=body))
    # UZUN sayfa: parçala, sırayla temizle, önceki çıktının sonunu bağlam vererek dik
    out = ""
    i = 0
    while i < len(body):
        part = body[i:i + CHUNK]
        if not out:
            c = _clean_one(_CLEAN_Q.format(url=url or "-", body=part))
        else:
            tail = out[-OVERLAP:]
            c = _clean_one(_CONT_Q.format(url=url or "-", tail=tail, body=part))
        if c is None:                    # LLM yok -> şimdiye kadarki çıktı (yoksa None)
            return out or None
        if c:
            out += ("\n\n" + c) if out else c
        i += CHUNK - OVERLAP
    return out


def _split_front(text: str) -> tuple[str, str]:
    """(frontmatter_bloğu, gövde). Frontmatter yoksa ('', text)."""
    if text.startswith("---"):
        p = text.split("---", 2)
        if len(p) >= 3:
            return "---" + p[1] + "---", p[2].lstrip("\n")
    return "", text


def _url_of(front: str) -> str:
    for line in front.splitlines():
        if line.strip().startswith("url:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def process_bank(slug: str, workers: int = WORKERS) -> None:
    """Bir bankanın TÜM sayfa .md'lerini LLM ile temizler (pdf_text/image_text hariç)."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    if not site.exists():
        log.warning("%s: klasör yok", slug); return
    ledger = Ledger(site / "_processing_log.jsonl")
    cpath = site / "_page_clean_cache.json"
    done: dict[str, int] = json.loads(cpath.read_text()) if cpath.exists() else {}

    # SAYFA (.md kök) + PDF (pdf_text/) LLM-friendly temizlenir; image_text zaten VLM-temiz.
    mds = [p for p in site.rglob("*.md") if "image_text" not in p.parts]
    todo = [p for p in mds if str(p.relative_to(site)) not in done]
    log.info("%s: %d sayfa (%d temizlenmiş, %d yapılacak, workers=%d)",
             slug, len(mds), len(mds) - len(todo), len(todo), workers)

    lock_n = [0]

    def work(p: Path):
        rel = str(p.relative_to(site))
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return rel, None
        front, body = _split_front(text)
        cleaned = clean_page(body, _url_of(front))
        if cleaned is None:                      # LLM yok -> dokunma, cache'leme (retry)
            ledger.record("page_clean", _url_of(front), decision="unreachable")
            return rel, None
        # temiz gövdeyi yaz (frontmatter korunur). boş -> boilerplate-only sayfa.
        new = (front + "\n\n" + cleaned + "\n") if front else (cleaned + "\n")
        p.write_text(new, encoding="utf-8")
        dec = "content" if cleaned else "boilerplate_only"
        ledger.record("page_clean", _url_of(front), decision=dec,
                      reason=f"{len(body)}->{len(cleaned)} char")
        return rel, len(cleaned)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for j, (rel, res) in enumerate(ex.map(work, todo), 1):
            if res is not None:
                done[rel] = res
            if j % 100 == 0:
                cpath.write_text(json.dumps(done, ensure_ascii=False))
                log.info("  %s: %d/%d temizlendi", slug, j, len(todo))
    cpath.write_text(json.dumps(done, ensure_ascii=False))
    empty = sum(1 for v in done.values() if v == 0)
    log.info("%s BİTTİ: %d sayfa temiz (%d boilerplate-only/boş)", slug, len(done), empty)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Sayfa metnini LLM ile temizle")
    ap.add_argument("banks", nargs="*")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(os.path.basename(d)[:-5]
                                 for d in glob.glob(str(root / "*_site")))
    for b in banks:
        process_bank(b)


if __name__ == "__main__":
    main()
