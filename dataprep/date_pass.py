"""HIZLI sadece-tarih pass'i: mevcut (temiz) sayfa/PDF metnine Gemma'yı YALNIZCA
kampanya tarihi için sorar, frontmatter'a campaign_start/campaign_end yazar.

İçeriğe DOKUNMAZ (LLM-friendly temizleme zaten yapıldı). Her .md için tek kısa Gemma
çağrısı: "tarih görürsen bildir, yoksa boş". Sayfa uzunsa baş+son gönderilir (tarih
çoğunlukla başlıkta ya da dipteki koşullarda). Resume cache + ledger + thread pool.

Kullanım (VLM_POOL ve DATE_WORKERS ile eşzamanlılık ayarlanır):
  VLM_POOL=90 DATE_WORKERS=90 python -m dataprep.date_pass [bank ...]
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from corpus.dates import is_active
from dataprep import vlm
from dataprep.ledger import Ledger
from dataprep.pages import _set_front_dates, _split_front, _url_of

log = logging.getLogger("dataprep.date_pass")

WORKERS = int(os.environ.get("DATE_WORKERS", "90"))
MIN_LEN = 0
SINGLE = 8196
WIN, OVERLAP = 8196, 820
MAX_WIN = 10         # aşırı uzun dosyada patlamayı önle (baş+son garantili örneklenir)

_Q = (
    "Aşağıdaki KATILIM BANKASI sayfa/PDF metninde bir KAMPANYA veya teklifin GEÇERLİLİK "
    "/ BİTİŞ tarihi yazıyorsa bildir; başlangıç tarihi de yazıyorsa onu da ver. Aramak "
    "için uğraşma — açıkça yazıyorsa al, yoksa BOŞ bırak. Birden çok bitiş varsa EN GEÇ "
    "olanı seç. Tarih UYDURMA.\n\n"
    "AYRICA — KAMPANYANIN DURUMU: tarih bulamadıysan bile, metnin kendi ifadesinden "
    "(ör. açıkça sona erdiğini belirten bir ifade, geçmiş zaman kullanımı, arşiv/geçmiş "
    "kampanya bağlamı) bu kampanyanın KESİN OLARAK sona erdiğinden GERÇEKTEN eminsen "
    "durum='bitti' de. Emin değilsen, ya da hâlâ sürüyor gibi görünüyorsa (ki varsayılan "
    "budur) durum='bitmedi' de — sadece gerçekten eminsen 'bitti' yaz, tahmin etme.\n\n"
    "Metin:\n\"\"\"{body}\"\"\"\n\n"
    'SADECE JSON: {{"gecerlilik_baslangic": "<YYYY-MM-DD ya da boş>", '
    '"gecerlilik_bitis": "<YYYY-MM-DD ya da boş>", "durum": "<bitti|bitmedi>"}}')


def _windows(body: str) -> list[str]:
    """Kısa metin -> tek pencere (tam). Uzun metin -> tüm doküman boyunca kayan
    pencereler (tarih ortada da olabilir). Aşırı uzunsa baş+son GARANTİLİ + eşit
    aralıklı örnekleme (MAX_WIN)."""
    if len(body) <= SINGLE:
        return [body]
    starts = list(range(0, len(body) - OVERLAP, WIN - OVERLAP))
    if len(starts) > MAX_WIN:                      # çok uzun: baş, son + eşit aralık örnek
        step = (len(starts) - 1) / (MAX_WIN - 1)
        idx = sorted({0, len(starts) - 1} | {round(i * step) for i in range(MAX_WIN)})
        starts = [starts[i] for i in idx]
    return [body[s:s + WIN] for s in starts]


def _dates_of(body: str) -> dict | None:
    """(start,end,ended_no_date) ya da None (LLM ulaşılamadı). Uzun dokümanda her
    pencereyi sorar, en GEÇ bitişi / en ERKEN başlangıcı toplar. `ended_no_date`:
    tarih bulunamayan bir pencerede model içerikten 'kesin bitti' dediyse True —
    nihai durum kararı (process_bank/work) bunu SADECE hiç tarih bulunamadıysa
    kullanır; tarih varsa durum tarihten deterministik hesaplanır."""
    start = end = ""
    ended_no_date = False
    saw_answer = False
    for win in _windows(body):
        d = vlm.call_json(vlm.txt_msg(_Q.format(body=win)))   # max_tokens: sunucu karar versin
        if not d:
            continue                               # bu pencere ulaşılamadı, diğerlerini dene
        saw_answer = True
        e = (d.get("gecerlilik_bitis") or "").strip()
        if e and (not end or e > end):
            end = e
        s = (d.get("gecerlilik_baslangic") or "").strip()
        if s and (not start or s < start):
            start = s
        if not e and (d.get("durum") or "").strip().lower() == "bitti":
            ended_no_date = True
    if not saw_answer:                             # hiçbir pencere yanıtlamadı -> LLM yok
        return None
    return {"start": start, "end": end, "ended_no_date": ended_no_date}


def process_bank(slug: str, workers: int = WORKERS) -> None:
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    if not site.exists():
        log.warning("%s: klasör yok", slug); return
    ledger = Ledger(site / "_processing_log.jsonl")
    cpath = site / "_date_pass_cache.json"
    done: dict[str, str] = json.loads(cpath.read_text()) if cpath.exists() else {}

    # sayfa (.md kök) + PDF (pdf_text/); image_text hariç.
    mds = [p for p in site.rglob("*.md") if "image_text" not in p.parts]
    todo = [p for p in mds if str(p.relative_to(site)) not in done]
    log.info("%s: %d md (%d yapılacak, workers=%d)", slug, len(mds), len(todo), workers)

    def work(p: Path):
        rel = str(p.relative_to(site))
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return rel, None
        front, body = _split_front(text)
        if len(body.strip()) < MIN_LEN:
            return rel, ""                      # içerik yok -> tarihsiz, cache'le
        dts = _dates_of(body)
        if dts is None:                         # LLM yok -> dokunma, retry
            ledger.record("date_pass", _url_of(front), decision="unreachable")
            return rel, None
        end = dts.get("end") or ""
        # NİHAİ DURUM: tarih varsa DETERMİNİSTİK hesapla (LLM'e sorulmaz, tarih
        # matematiği — is_active zaten "computed, never stored" ilkesiyle var);
        # tarih yoksa modelin içerikten çıkardığı sinyale güven (varsayılan bitmedi).
        status = ("bitmedi" if is_active(end) else "bitti") if end else (
            "bitti" if dts.get("ended_no_date") else "bitmedi")
        new_front = _set_front_dates(front, {**dts, "status": status})
        if new_front != front:
            p.write_text((new_front + "\n\n" + body).rstrip() + "\n", encoding="utf-8")
        ledger.record("date_pass", _url_of(front), decision="stamped",
                      reason=f"end={end or '-'} durum={status}")
        return rel, f"{end}|{status}"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for j, (rel, res) in enumerate(ex.map(work, todo), 1):
            if res is not None:
                done[rel] = res
            if j % 100 == 0:
                cpath.write_text(json.dumps(done, ensure_ascii=False))
                log.info("  %s: %d/%d", slug, j, len(todo))
    cpath.write_text(json.dumps(done, ensure_ascii=False))
    dated = sum(1 for v in done.values() if v)
    log.info("%s BİTTİ: %d md, %d tarihli", slug, len(done), dated)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("banks", nargs="*")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(os.path.basename(d)[:-5]
                                 for d in glob.glob(str(root / "*_site")))
    for b in banks:
        process_bank(b, args.workers)


if __name__ == "__main__":
    main()
