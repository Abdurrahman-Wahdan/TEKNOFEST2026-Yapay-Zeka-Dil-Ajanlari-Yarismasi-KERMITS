"""GRAPH — agentic top-down crawl'ı LangGraph ile orkestrasyon.

Akış:
    discover → expand (triage + descend, frontier boşalana kadar döner) → harvest

Frugal kurallar:
  * Triage yalnızca DALLARI değerlendirir (URL+başlık; emin değilse look_at_page).
  * DIVE edilen bir dalın YAPRAK çocukları LLM'e tek tek sorulmaz; doğrudan
    indirme kuyruğuna girer. Sadece ALT-DALLAR bir sonraki turda tekrar triage
    edilir. => LLM çağrısı ~ dal sayısı, sayfa sayısı değil.
  * Bütçe: llm_calls_left, look_left, max_depth, max_fetch.

Çıktı formatı ve PDF davranışı store.py üzerinden mevcut motorla BİREBİR aynı.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import os
from datetime import date
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

import httpx

from dataprep.crawl.bank import engine
from dataprep.crawl.hiz import UyarlanabilirHiz, banka_profili
from dataprep.crawl import frontier, kalici_hata, policy, store

log = logging.getLogger("dataprep.crawl.graph")

# PARALEL HASAT eşzamanlılığı (kullanıcı kararı 2026-08-22). Seri döngü
# ölçüldü: ~1 sayfa / 90 saniye — her sayfa indirme + volatil-imza doğrulama
# (ikinci fetch) + pages.clean_page (LLM temizleme/etiketleme) zincirini tek
# tek await ediyordu. 50: kullanıcının belirlediği bağlantı sınırıyla aynı
# değer (bkz. dataprep/net_limit.py::NET_SEM). LLM tarafındaki gerçek
# eşzamanlılığı zaten NET_SEM kısıtlar; buradaki semafor SİTEYE aynı anda
# atılan HTTP isteğini sınırlar (siteyi boğmamak için).
HARVEST_CONCURRENCY = int(os.environ.get("CRAWL_HARVEST_CONCURRENCY", "50"))


class State(TypedDict):
    config: dict
    mode: str
    frontier: list           # triage bekleyen Node'lar (mevcut seviye)
    fetch: list              # (url, reason) indirilecek yapraklar
    budget: dict
    stats: dict


def load_verdict_cache(out: Path) -> dict[str, tuple[str, str]]:
    """Önceki (tamamlanmış YA DA yarıda kesilmiş) koşunun _decisions.json'ından
    {url: (verdict, fingerprint)} okur — dosya yoksa/bozuksa boş döner (ilk
    koşu davranışı, güvenli varsayılan). fingerprint'i olmayan eski-format
    kayıtlar (fingerprint alanı henüz yokken yazılmış) atlanır — onlar için
    eşleşme garantisi kuramayız, güvenli tarafta kalıp yeniden triage edilirler."""
    p = out / "_decisions.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("eski _decisions.json okunamadı (%s) — cache boş sayılacak", exc)
        return {}
    cache: dict[str, tuple[str, str]] = {}
    for r in rows:
        fp = r.get("fingerprint")
        v = r.get("verdict")
        u = r.get("url")
        if u and v and fp:
            cache[u] = (v, fp)
    return cache


def load_prior_verdicts(out: Path) -> dict[str, str]:
    """Önceki koşunun _decisions.json'ından {url: verdict} okur — fingerprint
    ŞART DEĞİL (load_verdict_cache'in aksine). Güvenlik fingerprint'ten değil
    expand_node'daki KOD-TABANLI added/removed diff'inden gelir (bkz.
    changed_urls) — bu yüzden fingerprint alanı olmayan (bugünden ÖNCEKİ)
    kayıtlar da GEÇERLİ sayılır; soğuk-başlangıç maliyeti olmaz."""
    p = out / "_decisions.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("eski _decisions.json okunamadı (%s) — önceki karar yok sayılacak", exc)
        return {}
    # triage_failed=True olan kayıtlar (LLM'e hiç ulaşılamadığı için atanmış
    # "güvenli varsayılan") KASITLI OLARAK DIŞLANIR -> bu URL'ler prior_verdicts'te
    # yokmuş gibi davranılır, expand_node onları to_triage'a koyup TEKRAR dener
    # (manuel müdahale gerekmez, bir sonraki koşuda otomatik düzelir).
    return {r["url"]: r["verdict"] for r in rows
            if r.get("url") and r.get("verdict") and not r.get("triage_failed")}


def load_prior_universe(out: Path) -> set[str]:
    """Önceki koşunun _universe.json'ından URL kümesini okur (yoksa boş küme)."""
    p = out / "_universe.json"
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")).get("urls", []))
    except Exception as exc:
        log.warning("eski _universe.json okunamadı (%s) — değişim kümesi boş sayılacak", exc)
        return set()


# --- düğümler --------------------------------------------------------------
async def discover_node(state: State, ctx) -> dict:
    client = ctx["client"]
    # keşif: derinlik sınırsız (site-içi tam kapanış); cap yalnız güvenlik freni
    mode, root = await frontier.discover(client, state["config"],
                                         cap=ctx.get("disco_cap", 50000), max_depth=0)
    ctx["root"] = root
    ctx["mode"] = mode
    ctx["base"] = state["config"]["BASE"].rstrip("/")
    tops = frontier.top_level(root)
    # keşfedilen tüm URL'ler "known" — incremental link-harvest bunun üstüne ekler
    # HAYALET ELEMESİ: şablon motorunun çözemediği yer tutucudan doğmuş sahte
    # yollar ({{link}}, ~/Templates/...) evrene HİÇ girmez — gerçek sayfa
    # olmadıkları hâlde her biri 3 retry × bağlantı zaman aşımı harcıyordu
    # (emlakkatilim: 1099 URL'in 464'ü). Elenenler failures.txt'e gerekçesiyle
    # yazılır, yani hesapsız KALMAZ (verify "açıklanamayan" saymaz).
    tum_yapraklar = frontier.all_leaf_urls(root)
    ctx["known"], hayaletler = frontier.hayaletleri_ayikla(tum_yapraklar)
    if hayaletler:
        log.warning("keşif: %d HAYALET URL elendi (şablon artığı — gerçek sayfa "
                    "değil), failures.txt'e kaydediliyor", len(hayaletler))
        try:
            with (ctx["out"] / "failures.txt").open("a", encoding="utf-8") as fh:
                fh.writelines(f"{u}\thayalet:sablon-artigi\n" for u in hayaletler)
        except Exception as exc:
            log.warning("hayalet kaydı yazılamadı: %s", exc)
    # ÖNCEKİ koşunun karar önbelleği + KOD-TABANLI (LLM'siz) değişim kümesi —
    # _decisions.json/_universe.json bu koşuda üzerine yazılmadan ÖNCE okunmalı,
    # o yüzden burada, keşiften hemen sonra bir kere yükleniyor.
    ctx["verdict_cache"] = load_verdict_cache(ctx["out"])
    ctx["prior_verdicts"] = load_prior_verdicts(ctx["out"])
    old_universe = load_prior_universe(ctx["out"])
    # SADECE eklenen URL'ler — tamamen deterministik, LLM yok. Silinenler artık
    # triage'ı TETİKLEMEZ (branch'i yeniden LLM'e sormaya gerek yok); onlar
    # ayrı, kod-tabanlı bir HTTP doğrulamasından geçer (bkz. _verify_missing_urls).
    # Böylece LLM'e SADECE gerçekten yeni içerik içeren dallar gider.
    ctx["changed_urls"] = ctx["known"] - old_universe
    # KEŞİF ÇÖKTÜ MÜ? (2026-08-23) Site keşif anında erişilemezse sitemap de
    # ana sayfa da boş döner; evren 0-1 URL'e iner ve crawl HİÇBİR ŞEY
    # indirmeden "başarıyla bitti" der — SESSİZ TAM KAYIP. Canlı kanıt:
    # ziraatkatilim tohum-evren=1 ile bitti, oysa sitemap'i 555 URL veriyor
    # ve katalogda 4341 sayfası var. Önceki koşuda kuveytturk aynı tuzağa
    # düştü (evren=1, 0 sayfa).
    #
    # Bu durumda ÖNCEKİ koşunun evreni varsa ondan devam edilir (veri kaybı
    # olmaz); yoksa hata fırlatılır ki çağıran (pipeline/nöbetçi) tekrar
    # denesin — "başarı" diye raporlanmasın.
    if len(ctx["known"]) <= 1:
        if old_universe:
            log.warning("  [KEŞİF ÇÖKTÜ] evren %d URL — site erişilemedi. ÖNCEKİ "
                        "koşunun evreni (%d URL) kullanılıyor.",
                        len(ctx["known"]), len(old_universe))
            ctx["known"] = set(old_universe)
            root = frontier.build_tree_from_urls(ctx["known"], ctx["base"])
            ctx["root"] = root
            tops = frontier.top_level(root)
        else:
            raise RuntimeError(
                f"keşif çöktü: evren {len(ctx['known'])} URL ve önceki evren de "
                f"yok — site erişilemiyor olmalı. Koşu BAŞARISIZ sayılıyor "
                f"(sessizce 'bitti' denmez).")
    log.info("keşif (tohum): mode=%s, üst dal=%d, tohum-evren=%d, önceki karar=%d, "
             "yeni eklenen URL=%d (kod-tabanlı)",
             mode, len(tops), len(ctx["known"]), len(ctx["prior_verdicts"]), len(ctx["changed_urls"]))
    return {"mode": mode, "frontier": tops, "fetch": []}


async def expand_node(state: State, ctx) -> dict:
    """Mevcut frontier'ı triage et; DIVE'ları genişletip sonraki frontier'ı kur.

    İKİ AYRI, birbirini tamamlayan yeniden-kullanım yolu (ikisi de LLM'siz,
    tamamen deterministik):
      1) KOD-TABANLI (birincil, soğuk-başlangıç YOK): dalın önceki bir kararı
         varsa VE altındaki yaprak URL kümesinde YENİ EKLENEN URL yoksa
         (changed_urls ile kesişim boşsa), eski karar aynen kullanılır — bu
         koşudan itibaren çalışır. Silinen URL'ler burada dikkate ALINMAZ
         (branch'i yeniden LLM'e sormaz) — onlar ayrı, tamamen kod-tabanlı bir
         HTTP doğrulamasından geçer (bkz. _verify_missing_urls).
      2) Fingerprint önbelleği (ikincil/yedek): changed_urls hesaplanamadıysa
         (ör. _universe.json daha önce hiç yazılmamışsa) yine de AYNI dalın
         önceki koşuda TAM olarak aynı yaprak-kümesiyle karar aldığı biliniyorsa
         (fingerprint eşleşmesi) LLM'e sorulmaz.
    Her iki durumda da yarıda kesilen bir koşu kaldığı yerden sürdürülür."""
    client, llm = ctx["client"], ctx["llm"]
    nodes = state["frontier"]
    budget = state["budget"]
    cache = ctx.get("verdict_cache", {})
    prior_verdicts = ctx.get("prior_verdicts", {})
    changed_urls = ctx.get("changed_urls", set())

    fingerprints = {n.url: frontier.leaf_fingerprint(n) for n in nodes}
    leaf_sets = {n.url: set(frontier.all_leaf_urls(n)) for n in nodes}
    to_triage = []
    reused = 0
    for n in nodes:
        prior = prior_verdicts.get(n.url)
        diff_hit = prior and not (leaf_sets[n.url] & changed_urls)
        cached = cache.get(n.url)
        fp_hit = cached and cached[1] == fingerprints[n.url]
        if diff_hit:
            n.verdict = prior
            reused += 1
        elif fp_hit:
            n.verdict = cached[0]
            reused += 1
        else:
            to_triage.append(n)
    if to_triage:
        if prior_verdicts:
            # Bu bankanın EN AZ BİR önceki koşusu var -> URL-bazlı LLM triage'ı
            # SADECE İLK koşuda yapılır. Sonraki koşularda YENİ bulunan dallar
            # da (önbellekte olmayanlar dahil) LLM'e SORULMADAN otomatik
            # FETCH/DIVE edilir — ilgisizlik artık content.py/pages.py'nin
            # İÇERİĞİ GÖREREK verdiği (URL tahmininden çok daha isabetli)
            # 'gerekli/gereksiz' etiketiyle sonradan elenir; URL bazlı kör
            # SKIP riski (yanlışlıkla değerli bir dalı atlama) bir daha
            # oluşmaz. Sadece ilk-kez-crawl edilen bir banka (prior_verdicts
            # boş) tam LLM triage'ından geçer.
            for n in to_triage:
                n.verdict = "DIVE" if (n.children or n.page_count > 1) else "FETCH"
            log.info("  (%d dal: önceki koşu var -> LLM triage atlandı, otomatik FETCH/DIVE)",
                     len(to_triage))
        else:
            await policy.triage_level(to_triage, llm=llm, client=client, budget=budget)
    if reused:
        log.info("  (%d/%d dal önbellekten — değişmemiş, LLM'e sorulmadı)", reused, len(nodes))

    next_frontier: list = []
    fetch: list = []          # SADECE bu seviyenin kuyruğu (bellekte birikmez)
    decisions = ctx["decisions"]

    for n in nodes:
        decisions.append({"url": n.url, "seg": n.seg, "depth": n.depth,
                          "verdict": n.verdict, "fingerprint": fingerprints[n.url]})
        if n.verdict == "SKIP":
            continue
        if n.verdict == "FETCH":
            fetch.append((n.url, f"triage@d{n.depth}"))
            continue
        # DIVE: çocukları getir (bfs'te ağ gerekir), sonra ayır
        if not n.children and state["mode"] == "bfs" and n.depth < budget["max_depth"]:
            links = await frontier.links_with_anchors(client, n.url)
            n.children = frontier.bfs_children(n.url, links, n.depth)
        for ch in n.children:
            is_leaf = not ch.children and ch.page_count == 1
            if is_leaf:
                fetch.append((ch.url, f"dive(/{n.seg})"))     # yaprak -> indir
            elif ch.depth <= budget["max_depth"]:
                next_frontier.append(ch)                       # alt-dal -> tekrar triage
            else:
                # triage derinliği aşıldı ama dal ilgili (DIVE altında) ->
                # alt ağacın TÜM yapraklarını indir (hiçbiri düşmesin)
                for lu in frontier.all_leaf_urls(ch):
                    fetch.append((lu, f"maxdepth(/{n.seg})"))

    # karar günlüğünü anlık diske yaz (bellekte birikmesin, çökme güvenli)
    (engine.OUT / "_decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=1), encoding="utf-8")

    log.info("seviye triage: bu seviye fetch=%d, sonraki dallar=%d, llm_left=%d",
             len(fetch), len(next_frontier), budget["llm_calls_left"])
    return {"frontier": next_frontier, "fetch": fetch}


def save_tree_snapshot(out: Path, known: set[str], base: str, decisions: list[dict]) -> None:
    """TÜM keşfedilen URL evreninden (known) taze bir ağaç kurar — LLM'in
    triage ettiği dallara kararı (decisions) eklenir, geri kalanı (incremental
    link-harvest ile bulunup ayrıca triage edilmemiş dallar dahil) yapısal
    olarak yine de korunur; hiçbir URL, LLM ne karar vermiş olursa olsun
    ağaçtan/kayıttan düşmez."""
    root = frontier.build_tree_from_urls(known, base)
    verdict_by_url = {d["url"]: d["verdict"] for d in decisions}

    def annotate(n: frontier.Node) -> None:
        if n.url in verdict_by_url:
            n.verdict = verdict_by_url[n.url]
        for c in n.children:
            annotate(c)
    annotate(root)
    (out / "_tree.json").write_text(
        json.dumps(frontier.node_to_dict(root), ensure_ascii=False, indent=1), encoding="utf-8")


async def _verify_missing_urls(client, missing: list[str]) -> list[str]:
    """Kayıp GÖRÜNEN URL'leri DOĞRUDAN kontrol eder — tamamen kod-tabanlı,
    LLM YOK: BFS'in bu koşuda o dala gidip gitmediğine bakılmaksızın, sayfanın
    GERÇEKTEN hâlâ canlı olup olmadığını HTTP ile sorar (engine.fetch — 400/401/
    403/404/410 ya da ısrarlı ağ hatası -> kalıcı ölü). Hâlâ 200 dönenler
    "removed" listesinden çıkarılır (BFS'in o koşuda o kadar derine inmemiş
    olması gerçek bir silinme DEĞİLDİR — kanıtlı: tombank'ta tek koşuda -129
    gibi aşırı bir sayı, meğer sayfalar hâlâ canlıymış). Tavansız paralel
    (asyncio.gather) — sayı azdır (sadece 'kayıp görünenler'), maliyet düşük."""
    if not missing:
        return []
    results = await asyncio.gather(
        *(engine.fetch(client, u, retries=2) for u in missing), return_exceptions=True)
    dead = []
    for u, res in zip(missing, results):
        if isinstance(res, Exception):
            dead.append(u)
            continue
        r, _err = res
        if r is None:
            dead.append(u)              # gerçekten kalıcı hata/erişilemez -> silinmiş
    return sorted(dead)


async def save_universe_diff(client, out: Path, known: set[str]) -> list[str]:
    """Bir önceki koşunun _universe.json'ı ile bugünküyü kıyaslar — eklenen/
    kaldırılan URL'leri _universe_diff.json'a yazar. Böylece yarın (ya da
    herhangi bir sonraki koşuda) sitede ne değişmiş anında görülebilir.
    ÖNEMLİ: bu, _universe.json ÜZERİNE YAZILMADAN ÖNCE çağrılmalı. Dönen:
    DOĞRUDAN HTTP ile doğrulanmış (gerçekten ölü) URL listesi — çağıran
    mark_removed'a geçirir; BFS'in o koşuda keşfetmediği ama hâlâ canlı
    sayfalar burada elenir (bkz. _verify_missing_urls)."""
    old_path = out / "_universe.json"
    old_urls: set[str] = set()
    if old_path.exists():
        try:
            old_urls = set(json.loads(old_path.read_text(encoding="utf-8")).get("urls", []))
        except Exception as exc:
            log.warning("eski _universe.json okunamadı (%s) — diff 'ilk kayıt' sayılacak", exc)
    added = sorted(known - old_urls)
    missing = sorted(old_urls - known)
    removed = await _verify_missing_urls(client, missing)
    diff = {"checked_at": date.today().isoformat(), "previous_count": len(old_urls),
            "current_count": len(known), "added_count": len(added),
            "removed_count": len(removed), "added": added, "removed": removed}
    (out / "_universe_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=1), encoding="utf-8")
    if old_urls:
        log.info("evren değişimi (önceki koşuya göre): +%d yeni, -%d kaldırılmış URL -> %s",
                 len(added), len(removed), out / "_universe_diff.json")
    return removed


def mark_removed(removed_urls: list[str], out: Path, catalog, bank: str) -> None:
    """Artık bulunamayan (removed) URL'lerin TÜM izlerine "removed" bayrağı
    ekler — hiçbir dosya/point FİZİKSEL SİLİNMEZ, sadece işaretlenir (gerçek
    temizlik kasıtlı olarak sonraki bir aşamaya bırakıldı, bkz. plan):
      1) Catalog kaydı (crawl/store.py::Catalog.mark_removed)
      2) content_ledger kaydı (dataprep/content.py'nin ürettiği ledger)
      3) Qdrant point'lerinin payload'ı (vector_stores/maintenance.py)
      4) o URL'i kaynak gösteren karşılaştırma tabloları -> yeniden doğrulama
         kuyruğu (data/_tables/_reverify_queue.json) — kör silme/boşaltma YOK,
         sadece "tekrar araştır" işareti."""
    if not removed_urls:
        return
    for u in removed_urls:
        catalog.mark_removed(u)
    catalog.save()

    content_ledger_path = out / "_content_ledger.json"
    if content_ledger_path.exists():
        try:
            cl = json.loads(content_ledger_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("_content_ledger.json okunamadı (%s) — içerik-ledger işaretlenemedi", exc)
            cl = {}
        changed = False
        for u in removed_urls:
            if u in cl:
                cl[u]["status"] = "removed"
                changed = True
        if changed:
            content_ledger_path.write_text(json.dumps(cl, ensure_ascii=False, indent=1), encoding="utf-8")

    try:
        from vector_stores.maintenance import mark_removed_by_url
        for u in removed_urls:
            mark_removed_by_url("campaigns", u)
    except Exception as exc:
        log.warning("Qdrant işaretleme atlandı: %s", exc)

    # karşılaştırma tabloları — kör silme YOK, sadece yeniden doğrulama kuyruğu.
    try:
        from dataprep.compare import store as compare_store
        led = compare_store._load_ledger()
        affected: dict[str, set[str]] = {}   # table_id -> etkileyen URL'ler
        for u in removed_urls:
            entry = led.get(u)
            if not entry:
                continue
            for tid in entry.get("tables") or []:
                affected.setdefault(tid, set()).add(u)
        if affected:
            qpath = compare_store.ROOT / "_reverify_queue.json"
            queue = json.loads(qpath.read_text(encoding="utf-8")) if qpath.exists() else []
            now = date.today().isoformat()
            for tid, urls in affected.items():
                queue.append({"table_id": tid, "bank": bank, "urls": sorted(urls),
                             "reason": "kaynak sayfa silindi", "queued_at": now})
            qpath.parent.mkdir(parents=True, exist_ok=True)
            qpath.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8")
            log.info("  %d tablo yeniden-doğrulama kuyruğuna eklendi -> %s", len(affected), qpath)
    except Exception as exc:
        log.warning("Karşılaştırma tablosu kuyruğu güncellenemedi: %s", exc)

    log.info("işaretlendi (removed): %d URL", len(removed_urls))


def _mutabakat_norm(u: str) -> str:
    """Şema/www/port/trailing-slash/query'den bağımsız kanonik anahtar —
    dataprep/verify.py::_norm ile AYNI kural (iki taraf aynı şeyi saysın)."""
    p = urlparse(u)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (p.path.rstrip("/") or "/")


def after_harvest(state: State) -> str:
    """Harvest sonrası: frontier doluysa devam et, yoksa MUTABAKAT'a geç.

    NOT (2026-08-22): frontier BOŞALMADAN llm bütçesi biterse eskiden doğrudan
    END'e gidiliyor ve kalan dallar SESSİZCE düşüyordu. Artık her iki durumda
    da 'reconcile' düğümüne uğranır — orada evrende kalan HER URL indirilir,
    yani bütçe bitse bile hiçbir sayfa kaybolmaz."""
    if state["frontier"] and state["budget"]["llm_calls_left"] > 0:
        return "expand"
    return "reconcile"


async def reconcile_node(state: State, ctx) -> dict:
    """SON MUTABAKAT — evrende olup hâlâ hiçbir kovaya düşmemiş TÜM URL'leri indirir.

    NEDEN (kullanıcı kararı 2026-08-22, ölçülmüş veri kaybı): koşu bittikten
    sonra `dataprep.verify` "AÇIKLANAMAYAN" saydığı sayfalar çıkıyordu —
    sitemap/BFS evreninde VAR, ama ne indirilmiş, ne SKIP dalı altında, ne de
    failures.txt'te. Canlı ölçüm (2026-08-22): emlakkatilim 113, dunyakatilim
    17, hayatfinans 5 = 135 GERÇEK sayfa (hepsi HTTP 200, aralarında emlakfx,
    kurum ödemeleri, tahsil-e-çek, ticari finansman gibi ÜRÜN sayfaları vardı).
    Sebep tek bir bug değil — bütçe tükenmesi, derinlik sınırı, bir dalın
    çocuklarının ağ hatasıyla boş dönmesi gibi BİRDEN ÇOK yol aynı sonuca
    çıkabiliyor. Bu yüzden sebep sebep yamamak yerine, koşunun SONUNDA
    deterministik bir kapanış kontrolü yapılır: hesabı verilemeyen ne varsa
    indirilir.

    SINIR/TRUNCATE YOKTUR (kullanıcı kararı): kaç tane olursa olsun hepsi
    indirilir — max_fetch bütçesi burada UYGULANMAZ, çünkü bu bir keşif değil
    bir BÜTÜNLÜK onarımıdır. İndirilemeyenler failures.txt'e yazılır, yani
    hesabı verilemeyen URL olarak KALMAZ."""
    client = ctx["client"]
    catalog: store.Catalog = ctx["catalog"]
    out: Path = ctx["out"]

    # EVREN = ctx["known"] ∪ CANLI SİTEMAP (kullanıcı kararı 2026-08-23:
    # "sitemap + bfs union, KAÇAK OLMAYACAK").
    #
    # NEDEN: ctx["known"] keşif anında oluşur. Site o an kısmen erişilemezse
    # eksik kalır ve mutabakat EKSİK BİR EVRENİ denetler — kaçağı göremez.
    # Bağımsız kontrolde yakalandı: emlakkatilim'de 2, kuveytturk'te 9
    # sitemap URL'i (halka-arz, pos-kampanyalari gibi GERÇEK ürün sayfaları)
    # hiçbir kovaya düşmemişti, oysa mutabakat "hesabı verilemeyen yok"
    # diyordu. Artık kapanışta sitemap TEKRAR çekilip birleştirilir.
    universe = set(ctx.get("known") or ())
    try:
        canli = await engine.discover_from_sitemaps(client) or set()
    except Exception as exc:
        canli = set()
        log.warning("  mutabakat: canlı sitemap okunamadı (%s)", type(exc).__name__)
    if canli:
        yeni = canli - universe
        if yeni:
            log.warning("  MUTABAKAT: canlı sitemap %d URL getirdi, %d tanesi "
                        "evrende YOKTU — eklendi", len(canli), len(yeni))
        universe |= canli
    if not universe:
        return {"fetch": []}

    saved = {_mutabakat_norm(u) for u, v in catalog.data.items()
             if isinstance(v, dict) and v.get("kind") == "page"}
    failed = set()
    fp = out / "failures.txt"
    if fp.exists():
        failed = {_mutabakat_norm(l.split("\t")[0])
                  for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()}
    skip_prefixes: list[str] = []
    decided: set[str] = set()
    dp = out / "_decisions.json"
    if dp.exists():
        try:
            for d in json.loads(dp.read_text(encoding="utf-8")):
                decided.add(_mutabakat_norm(d["url"]))
                if d.get("verdict") == "SKIP":
                    skip_prefixes.append(urlparse(d["url"]).path.rstrip("/"))
        except Exception as exc:
            log.warning("mutabakat: _decisions.json okunamadı (%s)", exc)

    # KALICI HATALI adresler mutabakatta da atlanır: sunucu "404" diyorsa
    # her koşuda yeniden indirmeye çalışmak boşuna (kuveytturk'te 749 kez).
    # Kayıt SİLİNMEZ — _kalici_hatalar.json'dan istenirse geri alınabilir.
    _kalici = kalici_hata.atlanacak(out)
    eksik: list[str] = []
    atlanan_kalici = 0
    for u in universe:
        n = _mutabakat_norm(u)
        if n in saved or n in failed or n in decided:
            continue
        if u in _kalici or kalici_hata.bozuk_url(u):
            atlanan_kalici += 1
            continue
        path = urlparse(u).path.rstrip("/")
        if any(path == sp or path.startswith(sp + "/") for sp in skip_prefixes if sp):
            continue
        eksik.append(u)

    if atlanan_kalici:
        log.info("MUTABAKAT: %d URL kalıcı hatalı (404/403/DNS/bozuk) — "
                 "atlandı (_kalici_hatalar.json)", atlanan_kalici)
    if not eksik:
        log.info("MUTABAKAT: hesabı verilemeyen URL yok ✅")
        return {"fetch": []}

    log.warning("MUTABAKAT: %d URL hiçbir kovaya düşmemiş — HEPSİ indiriliyor "
                "(sınır yok)", len(eksik))
    embed, vec = ctx.get("embed"), ctx.get("vec")
    kalan: list[tuple[str, str]] = []
    for i, u in enumerate(sorted(eksik), 1):
        try:
            st = await store.fetch_and_store(client, u, catalog,
                                             reason="mutabakat",
                                             embed=embed, store_vec=vec)
        except Exception as exc:              # tek URL tüm mutabakatı düşürmesin
            st = "FAIL"
            log.warning("  mutabakat hatası %s: %s", u[-70:], type(exc).__name__)
        # FAIL kadar EMPTY de kayda geçer: içeriği boş/okunamaz dönen bir URL
        # (ör. login portalı, farklı host'ta JS-only sayfa) hiçbir kovaya
        # düşmezse verify onu sonsuza dek "AÇIKLANAMAYAN" saymaya devam eder.
        # Canlı örnek: internetsube.dunyakatilim.com.tr — indirildi, gövdesi
        # boştu, hiçbir yere yazılmadı, hesapsız kaldı. Artık gerekçesiyle
        # failures.txt'e yazılır: hesabı verilemeyen URL KALMAZ.
        if st in ("FAIL", "EMPTY"):
            kalan.append((u, f"mutabakat:{st.lower()}"))
            if st == "FAIL":          # kalıcıysa bir daha denenmesin
                kalici_hata.kaydet(out, u, "mutabakat:fail")
        await asyncio.sleep(ctx["delay"])
        if i % 20 == 0:
            catalog.save()
    catalog.save()
    if kalan:
        with fp.open("a", encoding="utf-8") as fh:
            fh.writelines(f"{u}\t{r}\n" for u, r in kalan)
        log.warning("MUTABAKAT: %d URL indirilemedi/boş döndü -> failures.txt",
                    len(kalan))
    log.info("MUTABAKAT bitti: %d/%d indirildi", len(eksik) - len(kalan), len(eksik))
    return {"fetch": []}


async def harvest_node(state: State, ctx) -> dict:
    """Bu seviyenin FETCH kuyruğunu HEMEN indir + katalog anlık kaydet."""
    client = ctx["client"]
    catalog: store.Catalog = ctx["catalog"]
    embed, vec = ctx.get("embed"), ctx.get("vec")
    max_fetch = state["budget"]["max_fetch"]

    done_total = ctx.setdefault("done_total", 0)
    seen: set = ctx.setdefault("fetched_urls", set())
    urls = [(u, r) for u, r in state["fetch"] if not (u in seen or seen.add(u))]
    if max_fetch:
        remaining = max_fetch - done_total
        urls = urls[:max(0, remaining)]
    if not urls:
        return {"fetch": []}
    log.info("hasat (seviye): %d yeni sayfa indiriliyor (toplam %d)", len(urls), done_total)

    counts: dict[str, int] = ctx.setdefault("counts", {})
    failed: list[tuple[str, str]] = []
    harvested: set = set()          # bu turda indirilen sayfalardan çıkan linkler

    async def _one(u: str, reason: str) -> str:
        st = await store.fetch_and_store(client, u, catalog, reason=reason,
                                         embed=embed, store_vec=vec, link_sink=harvested)
        await asyncio.sleep(ctx["delay"])
        return st

    # 1. geçiş — PARALEL hasat (kullanıcı kararı 2026-08-22: "veri kaybımız
    # olmayacaksa paralel olsun").
    #
    # ESKİDEN SERİYDİ ve ölçüldü: ~1 sayfa / 90 saniye. Sebep tek bir HTTP
    # isteği değil; her YENİ/DEĞİŞMİŞ sayfa için sırayla (a) indirme,
    # (b) volatil-imza doğrulaması (İKİNCİ bir taze fetch), (c) pages.
    # clean_page -> LLM temizleme+etiketleme yapılıyor. Hepsi tek tek await
    # edildiği için kuveytturk gibi 2000+ sayfalık bir sitede iş günlerce
    # sürüyordu.
    #
    # VERİ KAYBI RİSKİ YOK: her sayfa BAĞIMSIZ indirilir/yazılır, ortak durum
    # yalnız `catalog` (artık kilitli + atomik save, bkz. store.Catalog),
    # `counts`, `failed` ve `harvested` — bunlara yazım tek bir olay
    # döngüsünde (asyncio) sıralı gerçekleşir, yarış yoktur. Başarısızlar
    # aynen retry turlarına düşer.
    # UYARLANABİLİR HIZ (bkz. crawl/hiz.py): sabit bir eşzamanlılık yerine,
    # sistem güvenli hızı KENDİ bulur — hata görünce yarıya iner, temiz
    # gidince yavaşça artar, bloklandığında duraklayıp yoklar. Böylece
    # "hangi banka ne kadar tolere ediyor" bilgisini elle tutmak gerekmez.
    # Banka profili: WAF'a hassas siteler DÜŞÜK başlar (bkz. hiz.NAZIK_BANKALAR).
    if "hiz" not in ctx:
        _pr = banka_profili(ctx.get("bank") or "")
        ctx["hiz"] = UyarlanabilirHiz(baslangic=_pr["baslangic"], tavan=_pr["tavan"],
                                       rps=_pr.get("rps"))
        if _pr.get("gecikme") is not None and ctx.get("delay", 0) < _pr["gecikme"]:
            ctx["delay"] = _pr["gecikme"]     # nazik bankada istekler arası boşluk
        if _pr.get("rps"):
            ctx["delay"] = 0.0                # hız sınırı zaten aralığı garanti eder
            log.info("  [NAZİK MOD] %s: eşzamanlılık %d (tavan %d), hız %.1f istek/sn "
                     "(ek gecikme YOK)", ctx.get("bank"), _pr["baslangic"],
                     _pr["tavan"], _pr["rps"])
        elif _pr.get("gecikme") is not None:
            log.info("  [NAZİK MOD] %s: eşzamanlılık %d (tavan %d), gecikme %.1fs",
                     ctx.get("bank"), _pr["baslangic"], _pr["tavan"], _pr["gecikme"])
    sem = ctx["hiz"]
    _ilerleme = {"n": 0}

    async def _sinirli(u: str, reason: str) -> tuple[str, str, str]:
        async with sem:
            try:
                st = await _one(u, reason)
            except Exception as exc:          # tek sayfa TÜM seviyeyi düşürmesin
                log.warning("  hasat hatası %s: %s: %s", u[-70:],
                            type(exc).__name__, exc)
                st = "FAIL"
            # Hız geri bildirimi: FAIL = bağlantı/WAF sinyali olabilir.
            sem.bildir(ok=(st != "FAIL"))
            _ilerleme["n"] += 1
            if _ilerleme["n"] % 20 == 0:
                catalog.save()                # ara kayıt (bellekte birikmesin)
            return u, reason, st

    for u, reason, st in await asyncio.gather(
            *(_sinirli(u, r) for u, r in urls)):
        counts[st] = counts.get(st, 0) + 1
        if st == "FAIL":
            failed.append((u, reason))
    catalog.save()

    # başarısızları birkaç tur, artan beklemeyle yeniden dene
    max_retries = state["budget"].get("max_retries", 3)
    for attempt in range(1, max_retries + 1):
        if not failed:
            break
        wait = 2.0 * attempt        # 2s, 4s, 6s ... yük azalınca geçici hatalar düzelir
        log.info("yeniden deneme %d/%d: %d başarısız URL (%.0fs bekle)",
                 attempt, max_retries, len(failed), wait)
        await asyncio.sleep(wait)
        still: list[tuple[str, str]] = []

        async def _retry_one(u: str, reason: str) -> tuple[str, str, str]:
            async with sem:
                try:
                    st = await _one(u, f"{reason}|retry{attempt}")
                except Exception:
                    st = "FAIL"
                return u, reason, st

        for u, reason, st in await asyncio.gather(
                *(_retry_one(u, r) for u, r in failed)):
            if st == "FAIL":
                still.append((u, reason))
            else:                    # düzeldi: FAIL sayacını azalt, yeni durumu ekle
                counts["FAIL"] = max(0, counts.get("FAIL", 0) - 1)
                counts[st] = counts.get(st, 0) + 1
        failed = still

    if failed:
        # KALICI HATALARI KAYDET (kullanıcı kararı 2026-08-25: "crawl kodunu
        # genellemeye çalışalım ki sonraki koşularda işimiz kolaylaşır").
        # 404/403/410/DNS-yok gibi sunucunun "bu adres yok" dediği hatalar
        # retry ile düzelmez; diske yazılır ve SONRAKİ KOŞU bunları atlar.
        # Ölçüldü: kuveytturk'te 801 FAIL'in 749'u gerçek 404'tü ve her
        # koşuda yeniden deneniyordu (WAF kotası + saatler harcandı).
        # Geçici hatalar (timeout/5xx/bağlantı) KAYDEDİLMEZ, yine denenir.
        for _u, _r in failed:
            kalici_hata.kaydet(engine.OUT, _u, _r)
        with (engine.OUT / "failures.txt").open("a", encoding="utf-8") as fh:
            fh.writelines(f"{u}\t{r}\n" for u, r in failed)
        # MERKEZİ ETİKET DOSYASI (kullanıcı kararı 2026-08-23: "belli bir
        # deneme limiti koy, onları etiketler sonra manuel hallederiz").
        # failures.txt banka içinde kalıyor; burası TÜM bankaların pes edilen
        # web isteklerini tek yerde toplar, sabah elle incelenebilsin diye.
        try:
            merkez = Path(__file__).resolve().parents[2] / "data" / "_elle_bakilacak.jsonl"
            merkez.parent.mkdir(parents=True, exist_ok=True)
            with merkez.open("a", encoding="utf-8") as mf:
                for u, r in failed:
                    mf.write(json.dumps(
                        {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "banka": ctx.get("bank", "?"), "asama": "1-crawl",
                         "url": u, "sebep": r,
                         "deneme": max_retries + 1}, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("  (merkezi etiket yazılamadı: %s)", exc)
        log.warning("%d URL tüm denemelere rağmen başarısız -> failures.txt "
                    "+ data/_elle_bakilacak.jsonl", len(failed))

    ctx["done_total"] = done_total + len(urls)
    catalog.save()                       # seviye sonu anlık kayıt

    # incremental BFS birleşimi: yeni keşfedilen (bilinmeyen) URL'leri frontier'a ekle
    known: set = ctx["known"]
    CAP = 50000                           # sonsuz URL-tuzağına karşı güvenlik
    # link-harvest'ten gelenler de hayalet elemesinden geçer (keşifle AYNI
    # kural) — yoksa şablon artığı URL'ler crawl ortasında geri sızardı.
    # KALICI HATALI ve BOZUK URL'leri frontier'a HİÇ ALMA. hayalet_url şablon
    # artıklarını yakalıyordu; kalici_hata ayrıca (a) geçmiş koşularda 404/403
    # veren adresleri, (b) noktasız host / mojibake gibi yapısal bozukları eler.
    _atla = kalici_hata.atlanacak(engine.OUT)
    new = {u for u in harvested
           if u not in known and not frontier.hayalet_url(u)
           and u not in _atla and not kalici_hata.bozuk_url(u)}
    carry = list(state["frontier"])
    if new and len(known) < CAP:
        known |= new
        sub = frontier.build_tree_from_urls(new, ctx["base"])
        carry += frontier.top_level(sub)
        log.info("  +%d yeni URL keşfedildi (link-harvest) -> evren=%d", len(new), len(known))
    return {"fetch": [], "stats": counts, "frontier": carry}


# --- graf kurulumu ---------------------------------------------------------
def build_graph(ctx):
    from langgraph.graph import StateGraph, END

    async def _discover(s): return await discover_node(s, ctx)
    async def _expand(s): return await expand_node(s, ctx)
    async def _harvest(s): return await harvest_node(s, ctx)
    async def _reconcile(s): return await reconcile_node(s, ctx)

    g = StateGraph(State)
    g.add_node("discover", _discover)
    g.add_node("expand", _expand)
    g.add_node("harvest", _harvest)
    g.set_entry_point("discover")
    g.add_edge("discover", "expand")
    g.add_edge("expand", "harvest")      # her seviye triage'ından sonra HEMEN indir
    g.add_node("reconcile", _reconcile)
    g.add_conditional_edges("harvest", after_harvest,
                            {"expand": "expand", "reconcile": "reconcile"})
    g.add_edge("reconcile", END)         # son mutabakat: kaçan sayfa kalmasın
    return g.compile()


# --- çalıştırma ------------------------------------------------------------
async def run(args) -> None:
    engine.load(args.bank)               # aktif banka motorunu yükle (kuveytturk, albaraka, ...)
    cfg = engine.CONFIG
    log.info("=== KATILIM BANKASI: %s (%s) ===", cfg["NAME"], cfg["BASE"])
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "data" / f"{engine.SLUG}_site"
    store.set_output(out)

    # JS-render (SPA) katılım bankaları için başsız tarayıcı: engine.fetch'i render'a çevir
    render_client = None
    if args.render:
        from dataprep.crawl import render
        render_client = render.RenderClient()
        await render_client.start()
        render.install(engine, render_client)
        log.info("RENDER modu aktif — sayfalar Chromium ile render edilecek")

    # Katılım bankasına ÖZEL düzeltmeler (varsa) — render'dan sonra sarılır
    from dataprep.crawl import adapters
    adapters.install(engine, args.bank)

    # LLM ZORUNLU — her karar LLM'den; kural/regex yedeği yoktur.
    from llm import get_llm
    llm = get_llm("gemma")
    log.info("LLM: extractor rolü aktif")

    # Qdrant (opsiyonel)
    embed = vec = None
    if args.embed:
        try:
            from embeddings import get_embedding
            from vector_stores import ensure_collection, get_vector_store
            embed = get_embedding()
            ensure_collection(cfg_name := "campaigns")
            vec = get_vector_store(cfg_name, embed)
            log.info("Qdrant: '%s' koleksiyonu hazır", cfg_name)
        except Exception as exc:
            log.warning("Qdrant atlandı: %s", exc)

    ctx: dict[str, Any] = {
        "llm": llm, "embed": embed, "vec": vec, "out": out,
        "catalog": store.Catalog(out / "_catalog.json"),
        "decisions": [], "delay": args.delay, "disco_cap": args.disco_cap,
        "bank": args.bank,                # hız profili için (bkz. hiz.banka_profili)
    }

    budget = {"llm_calls_left": args.max_llm, "look_left": args.max_look,
              "max_depth": args.max_depth, "max_fetch": args.limit or args.max_fetch,
              "max_retries": args.max_retries}
    init: State = {"config": cfg, "mode": "", "frontier": [], "fetch": [],
                   "budget": budget, "stats": {}}

    async with httpx.AsyncClient(headers=engine.HEADERS, timeout=40,
                                 follow_redirects=True) as client:
        ctx["client"] = client
        graph = build_graph(ctx)
        try:
            final = await graph.ainvoke(init, {"recursion_limit": 100000})
        finally:
            if render_client:
                await render_client.stop()

        # karar günlüğü + BÜYÜYEN evren (sitemap tohumu + link-harvest ile keşfedilenler)
        (out / "_decisions.json").write_text(
            json.dumps(ctx["decisions"], ensure_ascii=False, indent=1), encoding="utf-8")
        known_set = ctx.get("known", set())
        # ÖNEMLİ: eski _universe.json'ı henüz üzerine yazmadan önce diff al.
        # client hâlâ AÇIK olmalı (kayıp URL'lerin gerçekten ölü olup olmadığını
        # HTTP ile doğrulamak için) — bu yüzden async-with bloğunun İÇİNDE.
        removed_urls = await save_universe_diff(client, out, known_set)
    known = sorted(known_set)
    (out / "_universe.json").write_text(
        json.dumps({"mode": ctx.get("mode"), "count": len(known),
                    "cap_hit": len(known) >= 50000, "urls": known},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    # silinen sayfaların TÜM izlerine (catalog/content-ledger/Qdrant/karşılaştırma
    # tabloları) "removed" bayrağı — fiziksel silme YOK, bkz. mark_removed docstring.
    mark_removed(removed_urls, out, ctx["catalog"], args.bank)
    # TÜM ağaç yapısı — LLM ne karar vermiş olursa olsun (DIVE/FETCH/SKIP)
    # hiçbir dal düşmeden — kayıt altına alınır.
    save_tree_snapshot(out, known_set, ctx["base"], ctx["decisions"])
    log.info("=== BİTTİ (%s) ===", date.today().isoformat())
    log.info("indirme durumları: %s", final.get("stats"))
    log.info("kararlar: %d dal -> %s", len(ctx["decisions"]), out / "_decisions.json")
    log.info("ağaç anlık görüntüsü -> %s", out / "_tree.json")


def parse_args():
    ap = argparse.ArgumentParser(description="Agentic top-down bank crawler")
    ap.add_argument("--bank", default="kuveytturk",
                    help="download_sites/<bank>.py slug'ı (kuveytturk, albaraka, ...)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--embed", action="store_true", help="Qdrant'a da yaz")
    ap.add_argument("--render", action="store_true",
                    help="JS-render (SPA) katılım bankaları için Playwright/Chromium kullan")
    ap.add_argument("--max-depth", type=int, default=8)
    ap.add_argument("--max-llm", type=int, default=100000,
                    help="toplam triage çağrı bütçesi (teker-teker + sıcaklık merdiveni için yüksek)")
    ap.add_argument("--max-look", type=int, default=-1,
                    help="look_at_page bütçesi (-1 = sınırsız)")
    ap.add_argument("--max-fetch", type=int, default=0,
                    help="0 = sınırsız (katılım bankası başına indirme tavanı yok)")
    ap.add_argument("--disco-cap", type=int, default=50000,
                    help="sitemap'siz keşifte en fazla kaç URL taransın (render'da düşük tut)")
    ap.add_argument("--max-retries", type=int, default=3,
                    help="hasatta başarısız URL'leri kaç tur yeniden dene")
    ap.add_argument("--limit", type=int, default=0, help="test: en çok N sayfa indir")
    ap.add_argument("--delay", type=float, default=0.05)
    return ap.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
