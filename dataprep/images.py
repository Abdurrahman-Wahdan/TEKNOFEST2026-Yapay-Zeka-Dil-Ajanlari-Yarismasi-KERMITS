"""Web sayfa GÖRSELLERİ -> item-VLM (dekoratif eleme + içerik çıkarımı).

Crawl, her sayfanın <img> URL'lerini katalogda tutar. Bu aşama:
  * Her benzersiz görseli indirir (URL bazında bir kez) -> PNG'ye normalize eder,
  * ImageCache.examine ile HASH-DEDUP: aynı görsel (logo/banner/arka plan) onlarca
    sayfada tekrar etse de YALNIZCA BİR KEZ VLM'e sorulur; "dekoratif mi yoksa
    KATILIM BANKASI ürün/kampanya bilgisi mi?" -> içerikse markdown çıkarır,
  * Sonuçları sayfa bazında image_text/<sayfa>.md olarak yazar (provenance: sayfa
    URL + görsel URL + hash). Küçük ikonlar (<MIN_PX) atlanır.

Kullanım:  python -m dataprep.images <bank>        (boş = tüm bankalar)
Bankalar PARALEL, banka-içi SERİ (run_parallel deseni).
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from dataprep import vlm
from dataprep.ledger import Ledger

log = logging.getLogger("dataprep.images")

MIN_PX = int(os.environ.get("IMAGE_MIN_PX", "0"))  # Sıfır yapay limit: en küçük görsel bile incelenir
WORKERS = int(os.environ.get("IMG_WORKERS", "10"))


def _banka_workers(slug: str, istenen: int) -> int:
    """WAF'a hassas bankalarda görsel indirmeyi de YAVAŞLAT (kullanıcı kararı
    2026-08-23: "image indirme koşusunda da Kuveyttürk nazik kalmalı").
    Profil crawl ile AYNI kaynaktan gelir — tek yerden yönetilir."""
    try:
        from dataprep.crawl.hiz import NAZIK_BANKALAR
    except Exception:
        return istenen
    pr = NAZIK_BANKALAR.get(slug)
    if not pr:
        return istenen
    n = max(1, min(istenen, int(pr.get("tavan", istenen))))
    if n < istenen:
        log.info("  [NAZİK MOD] %s: görsel indirme %d -> %d worker", slug, istenen, n)
    return n
# banka-içi indir+examine eşzamanlılığı. DÜŞÜK tutulur çünkü indirme limiti PER banka-sitesi:
# bankalar ARASI paralel çalışır (run_images_parallel.sh), her siteye az istek = throttle yok.
# VLM açık bağlantısı ayrıca vlm.VLM_POOL ile (süreç başına) sınırlı; bankalar×POOL < 100.

# TARAYICI TAKLİDİ: gerçek Chrome header seti + insan-gibi tempo (WAF/anti-bot'u yumuşatır).
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120", "Not?A_Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-origin",
}
JITTER = (0.05, 0.30)     # her istek öncesi rastgele bekleme (insan temposu)


def _to_png(data: bytes) -> bytes | None:
    """İndirilen görseli PNG'ye normalize et (dedup hash tutarlı olsun). Küçükse None."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    if im.width < MIN_PX or im.height < MIN_PX:      # ikon/dekoratif
        return None
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def process_bank(slug: str, workers: int = WORKERS) -> None:
    workers = _banka_workers(slug, workers)   # WAF hassas bankada yavaşla
    site = Path(__file__).parent.parent / "data" / f"{slug}_site"
    cat_path = site / "_catalog.json"
    if not cat_path.exists():
        log.warning("%s: katalog yok, atlanıyor", slug)
        return
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    cache = vlm.ImageCache(site / "_image_cache.json")   # BANKA İÇİ (kasıtlı)
    # Ortak/bankalararası cache DENENDİ ve GERİ ALINDI (2026-08-23): 10 banka
    # AYRI SÜREÇ olarak çalışıyor, ImageCache.save() ise yalnız SÜREÇ-İÇİ
    # kilitli — tek dosyaya 10 süreç yazınca son yazan diğerlerini siler ve
    # binlerce görsel incelemesi kaybolur. Ayrıca aynı görselin farklı
    # bankadaki BAĞLAMI farklı olabilir. Mükerrerlik zaten sha256 ile banka
    # içinde engelleniyor; bankalararası tekrar, veri güvenliği için kabul
    # edilmiş bilinçli bir maliyettir.
    ledger = Ledger(site / "_processing_log.jsonl")
    out_root = site / "image_text"

    # URL-CACHE: incelenen URL'ler bir daha İNDİRİLMEZ (resume/yarınki incremental anında;
    # 99 eşzamanlı re-download stall'ı önlenir). Sadece başarılı sonuçlar kalıcı yazılır.
    ucache_path = site / "_image_url_cache.json"
    url_cache: dict[str, dict] = {}
    if ucache_path.exists():
        try:
            url_cache = json.loads(ucache_path.read_text(encoding="utf-8"))
        except Exception:
            url_cache = {}

    def _save_ucache():
        ucache_path.write_text(json.dumps(url_cache, ensure_ascii=False), encoding="utf-8")

    pages = [(u, v) for u, v in cat.items() if v.get("kind") == "page" and v.get("images")]
    uniq: list[str] = []                      # sayfalar arası BENZERSİZ görsel URL'leri
    seen: set[str] = set()
    for _, rec in pages:
        for u in rec["images"]:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
    todo = [u for u in uniq if u not in url_cache]      # önbellekte olmayanlar indirilir
    log.info("%s: %d sayfa, %d benzersiz görsel (%d önbellekte, %d indirilecek, workers=%d)",
             slug, len(pages), len(uniq), len(uniq) - len(todo), len(todo), workers)

    # TARAYICI TAKLİDİ + granüler timeout (yavaş/asılı indirme worker'ı kilitlemesin).
    _ctx = __import__("ssl").create_default_context()
    _ctx.maximum_version = __import__("ssl").TLSVersion.TLSv1_2   # bkz. content.py::_img_ssl_ctx
    client = httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True, verify=_ctx,
                          timeout=httpx.Timeout(connect=6.0, read=20.0, write=10.0, pool=10.0))

    # 200 döndü ama görsel DEĞİL (tracking pixel/HTML/ikon) -> ÇÖP, kalıcı skip (cache'lenir).
    JUNK = {"decorative": True, "content": "", "skip": True}

    def work(img_url: str):
        """İndir + PNG normalize + VLM examine. (url, res|None|JUNK).
        AYRIM: BAĞLANTI hatası (000/blok/timeout) -> None = GERÇEK görsel geçici erişilemez,
        RETRY (cache'lenmez). 200 ama görsel değil -> JUNK = tracking/HTML çöpü, kalıcı SKIP."""
        time.sleep(random.uniform(*JITTER))   # insan-gibi tempo (patlatma yok)
        try:                                  # Referer = görselin sitesi (tarayıcı gibi)
            origin = urlsplit(img_url)
            ref = f"{origin.scheme}://{origin.netloc}/"
            # HOST BAŞINA 1 istek/sn (dataprep/site_hizi.py) — görsel
            # indirme de bankanın CANLI sitesine gidiyor.
            from dataprep.site_hizi import bekle_sync
            bekle_sync(img_url)
            r = client.get(img_url, headers={"Referer": ref})
            content = r.content
        except Exception:                     # bağlantı/timeout/blok -> GERÇEK görsel, retry
            ledger.record("image", img_url, decision="download_error", reason="bağlantı hatası")
            return img_url, None
        png = _to_png(content)
        if not png:                           # yanıt geldi ama görsel değil -> çöp, kalıcı skip
            ledger.record("image", img_url, decision="not_an_image")
            return img_url, JUNK
        res = cache.examine(png)              # None -> VLM ulaşılamadı (sonraki pass tamamlar)
        if res is None:
            ledger.record("image", img_url, decision="unreachable", reason="VLM yanıt yok")
        elif res.get("decorative") or not res.get("content"):
            ledger.record("image", img_url, decision="decorative")   # gereksiz -> alınmaz
        else:
            ledger.record("image", img_url, decision="content",
                          reason=(res.get("content") or "")[:120])   # işlendi, içerik var
        return img_url, res

    # 1) PARALEL indir + examine — SADECE todo (önbellekte olmayan) URL'ler
    results: dict[str, dict | None] = dict(url_cache)   # önbellektekiler hazır (indirme yok)
    with client, ThreadPoolExecutor(max_workers=workers) as ex:
        # as_completed: ex.map SIRALI döndürdüğü için tek yavaş görsel ardındaki
        # bitmiş işlerin kaydını bloke ediyordu — hem ilerleme donuk görünüyor
        # hem de kill anında o işler diske yazılmamış oluyordu. Kayıt aralığı
        # 100 -> 10: yeniden indirme israfı penceresi 10 kat daralır.
        futs = {ex.submit(work, t): t for t in todo}
        for j, fut in enumerate(as_completed(futs), 1):
            img_url, res = fut.result()
            results[img_url] = res
            if res is not None:                # ok VEYA ölü URL -> kalıcı (bir daha indirilmez);
                url_cache[img_url] = res       # sadece VLM-erişilemedi (None) kalıcı DEĞİL -> retry
            if j % 10 == 0:                    # sık kaydet: kill olursa re-download israfı az
                cache.save(); _save_ucache()
            if j % 100 == 0:
                log.info("  %s: %d/%d indirilip incelendi", slug, j, len(todo))
    cache.save()
    _save_ucache()

    # 2) sayfa bazında image_text/<sayfa>.md kur (içerikli görseller, sıra korunur)
    n_content = 0
    for i, (page_url, rec) in enumerate(pages, 1):
        blocks = [(u, results[u]["content"]) for u in rec["images"]
                  if results.get(u) and not results[u].get("decorative") and results[u].get("content")]
        if not blocks:
            continue
        n_content += 1
        rel = rec.get("path", "").rsplit(".md", 1)[0] or f"page{i}"
        fp = out_root / (rel + ".md")
        fp.parent.mkdir(parents=True, exist_ok=True)
        # TARİH PAYLAŞIMI YOK (kullanıcı kararı 2026-08-19): sayfanın tarihi
        # görsele YAZILMAZ. Bir sayfada birden çok görsel olabiliyor (ölçüldü:
        # "diğer kampanyalar" sayfasında 13 ayrı kampanya görseli) ve her
        # kampanyanın kendi süresi vardır — sayfanın tek tarihini hepsine
        # yazmak yanlış tarih üretir. Ne bulunuyorsa o kalır; alanlar yine
        # HER ZAMAN yazılır (boş olsa bile) ki "sorulmadı" ile "yok" ayrılsın.
        bas = bit = ""
        fm = ["---", f'source_page: "{page_url}"', f'bank: "{slug}"',
              f'campaign_start: "{bas}"', f'campaign_end: "{bit}"',
              "type: image_text", "---", ""]
        body = [f"<!-- görsel: {u} -->\n{md}\n" for u, md in blocks]
        fp.write_text("\n".join(fm) + "\n".join(body), encoding="utf-8")
    log.info("%s BİTTİ: %d içerik-görsel sayfası, cache=%d", slug, n_content, len(cache.data))


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Web sayfa görselleri -> item-VLM")
    ap.add_argument("banks", nargs="*")
    args = ap.parse_args()
    banks = args.banks or sorted(
        os.path.basename(d)[:-5]
        for d in glob.glob(str(Path(__file__).parent.parent / "data" / "*_site")))
    for b in banks:
        process_bank(b)


if __name__ == "__main__":
    main()
