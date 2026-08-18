"""Ana traversal: banka-banka, sayfa-sayfa gezer; kıyaslanabilir + yeni bir konu
bulursa 10 bankaya paralel researcher subagent gönderip tablo kurar; mevcut
tabloyla eşleşirse yalnız eksik bankanın subagent'ını çalıştırıp satır ekler.

Kullanım:
  python -m dataprep.compare.pipeline --banks kuveytturk --limit 30   # pilot
  python -m dataprep.compare.pipeline                                 # tam koşu
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from corpus import dates as _dates
from dataprep.pages import _split_front, _url_of

from . import store, synth
from .bank_agent import research_bank
from .classify_agent import classify_page
from .retrieval import index_table

log = logging.getLogger("dataprep.compare.pipeline")

ROOT = Path(__file__).resolve().parents[2] / "data"


def all_banks() -> list[str]:
    return sorted(os.path.basename(d)[:-5] for d in glob.glob(str(ROOT / "*_site")))


def _fresh_enough(front: str) -> bool:
    """Süresi geçmişse False (atla); tarihsiz ya da hâlâ geçerliyse True.

    Tarih varsa deterministik hesaplanır. Tarih yoksa date_pass'in metinden
    çıkardığı campaign_status'a bakılır (bitti -> atla)."""
    end = status = ""
    for line in front.splitlines():
        s = line.strip()
        if s.startswith("campaign_end:"):
            end = s.split(":", 1)[1].strip().strip('"')
        elif s.startswith("campaign_status:"):
            status = s.split(":", 1)[1].strip().strip('"')
    if end:
        return _dates.is_active(end)
    return status != "bitti"


def _pages(bank: str, limit: int | None):
    site = ROOT / f"{bank}_site"
    if not site.exists():
        return
    mds = [p for p in site.rglob("*.md") if "image_text" not in p.parts]
    if limit:
        mds = mds[:limit]
    for p in mds:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        front, body = _split_front(text)
        if len(body.strip()) < 40:
            continue
        if not _fresh_enough(front):
            continue
        yield _url_of(front) or str(p), body


def _fan_out_all(topic: str, banks: list[str]) -> list[dict]:
    """10 bankaya PARALEL researcher subagent — yalnız gerçekten yeni konuda."""
    reports = []
    with ThreadPoolExecutor(max_workers=len(banks)) as ex:
        futures = {ex.submit(research_bank, topic, b): b for b in banks}
        for i, fut in enumerate(as_completed(futures), 1):
            b = futures[fut]
            rep = fut.result()
            reports.append(rep)
            flag = " [LİMİT]" if rep.get("hit_limit") else (" [HATA]" if rep.get("error") else "")
            log.info("    [%d/%d bitti] %s -> sunuyor=%s (%d çağrı)%s",
                      i, len(banks), b, rep["offers"], rep["tool_calls"], flag)
    return reports


def _fan_out_one(topic: str, bank: str) -> dict:
    return research_bank(topic, bank)


def _record_sources(table_id: str, reports: list[dict]) -> None:
    """Kanıt olarak kullanılan sayfaları ledger'a KAYNAK olarak işaretle — bilgi
    amaçlı, ana traversal'ın kendi sırasında bu sayfaları ATLAMASINA yol AÇMAZ
    (own_verdict set edilmez, sadece record_citation)."""
    for r in reports:
        for src in r.get("sources", []):
            u = src.get("url")
            if u:
                store.record_citation(u, table_id)


def process_page(bank: str, url: str, body: str, banks: list[str]) -> None:
    prior = store.page_verdict(url)              # SADECE ana-agent'ın KENDİ kararı
    if prior is not None:
        return                                    # zaten anchor olarak işlendi -> atla

    # Kıyaslanabilir mi + (öyleyse) mevcut bir tabloya mı uyuyor yoksa yeni konu
    # mu — ajan kendi search_tables aracıyla (embedding, Qdrant) tablo havuzunda
    # arar; TÜM havuz tek prompt'a sığdırılmıyor, model kendi karar verene kadar
    # özgürce arar (bank_agent'taki search_bank ile aynı desen).
    d = classify_page(body, url)
    if d is None:
        return                                     # LLM ulaşılamadı -> retry (kaydetme)
    if not d["comparable"]:
        store.record_verdict(url, False, "", None)
        return

    topic = d["topic"]
    match_id = d["fits_table"]

    if match_id:
        table = store.load_table(match_id)
        if table is None:
            # registry'de var ama dosyası yok (elle silme / kesintili yazma) —
            # ÇÖKMEK yerine bunu "eşleşme yok" say, aşağıdaki YENİ KONU yoluna
            # (tam 10-banka fan-out) düş. Bloktan çıkmak için if/else, match_id
            # bilerek TEMİZLENMİYOR — aşağıdaki 'if match_id:' zaten atlanır.
            log.warning("  [TUTARSIZ KAYIT] %s registry'de var ama dosyası yok — "
                        "yeni tabloya düşülüyor", match_id)
        else:
            already = table["rows"].get(bank)
            if already:
                store.record_verdict(url, True, topic, match_id)
                return
            # GÜVENLİK AĞI: yalnız EKSİK bankanın subagent'ı — 10'u değil.
            report = _fan_out_one(topic, bank)
            # eşleşme YANLIŞ olabilir (match_table yanılmış) — veri gelmeden önce
            # doğrula; uymuyorsa bu tabloya zorla eklemek yerine YENİ tabloya git.
            fits = (not report["offers"]) or synth.fits_table(table["docstring"], bank, report)
            if fits:
                if report["offers"]:
                    store.add_row(match_id, bank, report["attributes"], report["sources"])
                store.record_verdict(url, True, topic, match_id)
                _record_sources(match_id, [report])
                log.info("  [satır eklendi] %s -> %s (%s)", bank, match_id, report["offers"])
                return
            log.info("  [YANLIŞ EŞLEŞME] %s / %r bu tabloya uymuyor -> yeni tablo için tam fan-out", bank, topic)
            reports = _fan_out_all(topic, banks)
            _finish_new_table(url, topic, reports, banks)
            return

    # gerçekten YENİ konu -> 10 bankaya paralel fan-out (ASIL mekanizma)
    log.info("  [YENİ KONU] %r — 10 bankaya fan-out", topic)
    reports = _fan_out_all(topic, banks)
    _finish_new_table(url, topic, reports, banks)


def _finish_new_table(url: str, topic: str, reports: list[dict], banks: list[str]) -> None:
    subcats = store.subcategory_examples()
    table_data = synth.synthesize_table(topic, reports, subcats)
    if table_data is None:
        return                                      # LLM ulaşılamadı -> retry sonra
    sources = {r["bank"]: r["sources"] for r in reports}
    table_id = store.create_table(topic, table_data["docstring"], table_data["columns"],
                                   table_data["rows"], sources,
                                   table_data["category"], table_data["subcategory"])
    try:                          # arama indeksine yazma başarısız olsa bile tablo geçerli
        index_table(table_id, topic, table_data["category"], table_data["subcategory"],
                    table_data["docstring"])
    except Exception as exc:
        log.warning("  [İNDEKS HATASI] %s: %s: %s", table_id, type(exc).__name__, exc)
    store.record_verdict(url, True, topic, table_id)
    _record_sources(table_id, reports)
    n_offer = sum(1 for r in reports if r["offers"])
    log.info("  [TABLO] %s (%s/%s): %d/%d banka sunuyor", table_id,
              table_data["category"], table_data["subcategory"], n_offer, len(banks))


def process_bank(bank: str, banks: list[str], limit: int | None = None) -> None:
    n = 0
    for url, body in _pages(bank, limit):
        try:
            process_page(bank, url, body, banks)
        except Exception as exc:            # noqa: BLE001 — TEK sayfa TÜM koşuyu
            # çökertmesin (research_bank katmanındaki aynı prensip bir üst
            # seviyede de geçerli: beklenmedik bir hata bu sayfayı atlasın,
            # kalan binlerce sayfa/banka etkilenmesin). own_verdict KAYDEDİLMEZ
            # — bir sonraki koşuda bu sayfa tekrar denenir.
            log.error("  [SAYFA HATASI] %s / %s: %s: %s", bank, url,
                       type(exc).__name__, exc)
        n += 1
    log.info("%s: %d sayfa tarandı", bank, n)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--banks", nargs="*", help="taranacak bankalar (boş=tümü)")
    ap.add_argument("--limit", type=int, help="banka başına sayfa sınırı (pilot için)")
    args = ap.parse_args()
    banks = all_banks()
    todo = args.banks or banks
    for b in todo:
        process_bank(b, banks, args.limit)


if __name__ == "__main__":
    main()
