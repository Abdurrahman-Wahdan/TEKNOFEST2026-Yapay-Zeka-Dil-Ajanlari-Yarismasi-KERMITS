"""Tüm bankalar için indirme BÜTÜNLÜK denetimi.

Her banka için kontrol eder:
  * kaç sayfa kaydedildi (md)
  * LLM'in kabul edip de inmeyeni (EMPTY/FAIL) — log + failures.txt'ten
  * PDF bütünlüğü: kaydedilen sayfalarda linkli her same-domain PDF gerçekten
    indi mi? İnmeyenleri AĞDAN teyit eder: 404/soft-404 ise site kaynaklı
    (kayıp değil), erişilebilirse GERÇEK KAÇAK (kırmızı bayrak).

Kullanım:
    python -m crawl.verify                 # tüm bankalar
    python -m crawl.verify kuveytturk ...  # seçili bankalar
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import sys

import httpx

from dataprep.crawl.bank import engine

DL = os.path.join(os.path.dirname(__file__), "..", "data")


def _saved_pages(site: str) -> list[str]:
    return [f for f in glob.glob(site + "/**/*.md", recursive=True)
            if "/pdfs/" not in f and not os.path.basename(f).startswith("_")]


def _pdf_links_in(site: str) -> set[str]:
    """Kaydedilen sayfalardaki TÜM PDF linkleri (indirdiğimizle aynı: dış dahil)."""
    links = set()
    for f in _saved_pages(site):
        txt = open(f, encoding="utf-8", errors="ignore").read()
        for m in re.findall(r'\]\((https?://[^)]+\.pdf[^)]*)\)', txt, re.I):
            links.add(m.split("?")[0].rstrip("/"))
    return links


def _norm(u: str) -> str:
    """Şema/www/port/trailing-slash/query'den bağımsız kanonik anahtar."""
    from urllib.parse import urlparse
    p = urlparse(u)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (p.path.rstrip("/") or "/")


async def coverage(slug: str, client) -> dict | None:
    """HESAP VEREBİLİRLİK: sitemap'teki HER URL bir kovaya düşüyor mu?

    Kovalar: indirildi (saved) / bilerek elendi (bir SKIP dalının altında) /
    gerçekten hatalı (failures). Hiçbirine düşmeyen = AÇIKLANAMAYAN (bug sinyali).
    Evren kaynağı: crawl'da yazılan _universe.json (sitemap VE bfs için tekdüze,
    yeniden ağ gerektirmez). Yoksa sitemap'e düşer; o da yoksa None."""
    site = os.path.abspath(os.path.join(DL, f"{slug}_site"))
    # EVREN = KAYITLI ∪ CANLI SİTEMAP (kullanıcı kararı 2026-08-23:
    # "sitemap + bfs union, KAÇAK OLMAYACAK").
    #
    # NEDEN UNION: eskiden yalnız kaydedilmiş _universe.json'a bakılıyordu.
    # O dosya, crawl keşif anında site erişilemezse BOZUK kaydediliyor —
    # kuveytturk'te içinde TEK URL vardı (ana sayfa) ve denetim o tek URL'i
    # kontrol edip "0 açıklanamayan" diyordu. Oysa sitemap'te 2391 Türkçe
    # URL vardı ve 51'i katalogda yoktu. Yani denetimin KENDİSİ kördü.
    #
    # Artık iki kaynak BİRLEŞTİRİLİR: kayıtlı evren + o anki CANLI sitemap.
    # Sitemap okunamazsa (site kapalı) kayıtlı evrenle devam edilir; kayıtlı
    # evren yoksa/bozuksa canlı sitemap tek başına yeter. İkisi de boşsa
    # denetim None döner ve "ölçülemedi" olarak raporlanır — sessizce "0"
    # DENMEZ.
    universe: set[str] = set()
    kaynaklar = []
    up = os.path.join(site, "_universe.json")
    if os.path.exists(up):
        try:
            u = json.load(open(up))
            kayitli = set(u.get("urls", []))
            if kayitli:
                universe |= kayitli
                kaynaklar.append(f"kayıtlı({len(kayitli)})")
        except Exception:
            pass
    # CANLI SİTEMAP — okunamazsa BUNU SÖYLE. Sessizce atlanırsa "0
    # açıklanamayan" yanıltıcı olur: gerçekte "sitemap kontrol EDİLEMEDİ"
    # demektir. Motorda sitemap TANIMLI DEĞİLSE (adilkatilim, tombank gibi
    # sitemap'i olmayan siteler) bu bir eksiklik değildir, öyle işaretlenir.
    sitemap_tanimli = bool(engine.CONFIG.get("SITEMAPS"))
    canli, sitemap_hata = set(), None
    if sitemap_tanimli:
        try:
            canli = await engine.discover_from_sitemaps(client) or set()
            if not canli:
                sitemap_hata = "boş döndü"
        except Exception as exc:
            sitemap_hata = f"{type(exc).__name__}"
    if canli:
        yeni = canli - universe
        universe |= canli
        kaynaklar.append(f"canlı-sitemap({len(canli)}, +{len(yeni)} yeni)")
    elif sitemap_hata:
        kaynaklar.append(f"canlı-sitemap OKUNAMADI({sitemap_hata})")
    elif not sitemap_tanimli:
        kaynaklar.append("sitemap-yok(bfs)")
    uni_mode = " ∪ ".join(kaynaklar) or "?"
    if not universe:
        return None
    cat = {}
    cp = os.path.join(site, "_catalog.json")
    if os.path.exists(cp):
        cat = json.load(open(cp))
    saved = {_norm(u) for u, v in cat.items() if v.get("kind") == "page"}
    failed = set()
    fp = os.path.join(site, "failures.txt")
    if os.path.exists(fp):
        failed = {_norm(l.split("\t")[0]) for l in open(fp).read().splitlines() if l.strip()}
    from urllib.parse import urlparse
    # kararlar: SKIP dal yolları (altındaki her URL bilerek elendi) + DIVE/FETCH
    # edilen dal URL'leri (triage GÖRDÜ; ara-dal olarak "traversed" sayılır).
    skip_prefixes = []
    decided = set()
    dp = os.path.join(site, "_decisions.json")
    if os.path.exists(dp):
        for d in json.load(open(dp)):
            decided.add(_norm(d["url"]))
            if d.get("verdict") == "SKIP":
                skip_prefixes.append(urlparse(d["url"]).path.rstrip("/"))

    unacc = []
    n_saved = n_skip = n_fail = n_dive = 0
    for u in universe:
        nu = _norm(u)
        if nu in saved:
            n_saved += 1; continue
        if nu in failed:
            n_fail += 1; continue
        path = urlparse(u).path.rstrip("/")
        if any(path == sp or path.startswith(sp + "/") for sp in skip_prefixes if sp):
            n_skip += 1; continue
        if nu in decided:                    # DIVE/FETCH edilmiş ara-dal
            n_dive += 1; continue
        unacc.append(u)
    return {"universe": len(universe), "saved": n_saved, "skip": n_skip,
            "fail": n_fail, "dive": n_dive, "unaccounted": unacc,
            "kaynak": uni_mode}


def _log_counts(slug: str) -> dict:
    p = f"/tmp/crawl_{slug}.log"
    if not os.path.exists(p):
        return {}
    txt = open(p, encoding="utf-8", errors="ignore").read()
    m = re.findall(r"indirme durumları: (\{[^}]*\})", txt)
    if not m:
        return {}
    try:
        return json.loads(m[-1].replace("'", '"'))
    except Exception:
        return {}


async def _reachable_pdf(client, url: str) -> str:
    """indi mi diye değil, sitede VAR mı diye kontrol: 'live' / '404' / 'err'."""
    r, err = await engine.fetch(client, url, retries=1)
    if r is None:
        return "404" if err and "404" in str(err) else "err"
    ct = r.headers.get("content-type", "").lower()
    if "pdf" in ct or (r.content[:4] == b"%PDF"):
        return "live"
    return "404"   # pdf değil (soft-404/redirect)


async def verify_bank(slug: str) -> dict:
    engine.load(slug)
    site = os.path.abspath(os.path.join(DL, f"{slug}_site"))
    if not os.path.isdir(site):
        return {"slug": slug, "yok": True}

    pages = _saved_pages(site)
    cat = {}
    cp = os.path.join(site, "_catalog.json")
    if os.path.exists(cp):
        cat = json.load(open(cp))
    dl_pdfs = {u.split("?")[0].rstrip("/") for u, v in cat.items() if v.get("kind") == "pdf"}
    counts = _log_counts(slug)

    linked = _pdf_links_in(site)
    missing = [l for l in linked if l not in dl_pdfs and l.split("?")[0] not in dl_pdfs]

    # eksik PDF'leri ağdan teyit: gerçek kaçak mı, ölü link mi
    real_miss, dead = [], 0
    if missing:
        async with httpx.AsyncClient(headers=engine.HEADERS, timeout=20,
                                     follow_redirects=True) as c:
            for u in missing:
                st = await _reachable_pdf(c, u)
                if st == "live":
                    real_miss.append(u)
                else:
                    dead += 1

    return {
        "slug": slug,
        "sayfa": len(pages),
        "empty": counts.get("EMPTY", 0),
        "fail": counts.get("FAIL", 0),
        "pdf_inen": len(dl_pdfs),
        "pdf_linkli": len(linked),
        "pdf_olu_link": dead,
        "pdf_gercek_kacak": real_miss,
    }


async def main(slugs: list[str]) -> None:
    if not slugs:
        slugs = sorted(os.path.basename(d)[:-5] for d in glob.glob(DL + "/*_site"))
    print(f"{'BANKA':16}{'SAYFA':>6}{'BOŞ':>5}{'FAIL':>5}{'PDF':>5}{'PDF-KAÇAK':>10}  DURUM")
    all_ok = True
    for s in slugs:
        r = await verify_bank(s)
        if r.get("yok"):
            print(f"{s:16}  (klasör yok)"); all_ok = False; continue
        kacak = len(r["pdf_gercek_kacak"])
        ok = (r["fail"] == 0 and kacak == 0)
        # boş sayfa varsa uyarı ama site kaynaklı olabilir
        durum = "✅ TAM" if ok and r["empty"] == 0 else ("⚠️ boş var" if kacak == 0 and r["fail"] == 0 else "❌ SORUN")
        if durum == "❌ SORUN":
            all_ok = False
        print(f"{s:16}{r['sayfa']:>6}{r['empty']:>5}{r['fail']:>5}{r['pdf_inen']:>5}{kacak:>10}  {durum}")
        for u in r["pdf_gercek_kacak"]:
            print(f"    ↳ GERÇEK KAÇAK PDF: {u}")
    print("\n" + ("HEPSİ TEMİZ ✅" if all_ok else "Bazı bankalarda gerçek kayıp/FAIL var ⬆"))

    # HESAP VEREBİLİRLİK: sitemap'teki her URL bir kovaya düşüyor mu?
    print(f"\n{'BANKA':16}{'EVREN':>7}{'İNDİ':>6}{'ELENDİ':>7}{'DALGEÇ':>7}{'HATA':>6}{'AÇIKLANAMAYAN':>14}")
    async with httpx.AsyncClient(headers=engine.HEADERS, timeout=30,
                                 follow_redirects=True) as c:
        for s in slugs:
            engine.load(s)
            cov = await coverage(s, c)
            if cov is None:
                print(f"{s:16}   (evren yok)")
                continue
            n = len(cov["unaccounted"])
            # KAYNAK sütunu: evrenin NEREDEN geldiği. "OKUNAMADI" görünüyorsa
            # o bankanın "0 açıklanamayan"ı GÜVENİLİR DEĞİLDİR — sitemap o an
            # çekilemediği için union'ın yarısı denetim dışı kalmıştır.
            kaynak = cov.get("kaynak", "?")
            supheli = "OKUNAMADI" in kaynak
            print(f"{s:16}{cov['universe']:>7}{cov['saved']:>6}{cov['skip']:>7}"
                  f"{cov.get('dive',0):>7}{cov['fail']:>6}{n:>14}"
                  + ("  ❌" if n else ("  ⚠️ SİTEMAP OKUNAMADI" if supheli else ""))
                  + f"   [{kaynak}]")
            for u in cov["unaccounted"][:5]:
                print(f"    ↳ açıklanamayan: {u}")
    print("\nHesap verebilirlik: her sitemap URL'i İNDİ + ELENDİ(SKIP) + HATA toplamına "
          "eşitse, hiçbir sayfa sessizce kaçmamış demektir (açıklanamayan=0).")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
