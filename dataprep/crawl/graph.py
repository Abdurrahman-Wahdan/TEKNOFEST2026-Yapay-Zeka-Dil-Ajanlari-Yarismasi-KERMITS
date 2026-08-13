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
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

import httpx

from dataprep.crawl.bank import engine
from dataprep.crawl import frontier, policy, store

log = logging.getLogger("dataprep.crawl.graph")


class State(TypedDict):
    config: dict
    mode: str
    frontier: list           # triage bekleyen Node'lar (mevcut seviye)
    fetch: list              # (url, reason) indirilecek yapraklar
    budget: dict
    stats: dict


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
    ctx["known"] = set(frontier.all_leaf_urls(root))
    log.info("keşif (tohum): mode=%s, üst dal=%d, tohum-evren=%d",
             mode, len(tops), len(ctx["known"]))
    return {"mode": mode, "frontier": tops, "fetch": []}


async def expand_node(state: State, ctx) -> dict:
    """Mevcut frontier'ı triage et; DIVE'ları genişletip sonraki frontier'ı kur."""
    client, llm = ctx["client"], ctx["llm"]
    nodes = state["frontier"]
    budget = state["budget"]

    await policy.triage_level(nodes, llm=llm, client=client, budget=budget)

    next_frontier: list = []
    fetch: list = []          # SADECE bu seviyenin kuyruğu (bellekte birikmez)
    decisions = ctx["decisions"]

    for n in nodes:
        decisions.append({"url": n.url, "seg": n.seg, "depth": n.depth,
                          "verdict": n.verdict})
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


def after_harvest(state: State) -> str:
    """Harvest sonrası: frontier doluysa devam et, yoksa bitir."""
    if state["frontier"] and state["budget"]["llm_calls_left"] > 0:
        return "expand"
    return "END"


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

    # 1. geçiş — her 20 sayfada katalogu anlık kaydet (bellekte birikmesin)
    for i, (u, reason) in enumerate(urls, 1):
        st = await _one(u, reason)
        counts[st] = counts.get(st, 0) + 1
        if st == "FAIL":
            failed.append((u, reason))
        if i % 20 == 0:
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
        for u, reason in failed:
            st = await _one(u, f"{reason}|retry{attempt}")
            if st == "FAIL":
                still.append((u, reason))
            else:                    # düzeldi: FAIL sayacını azalt, yeni durumu ekle
                counts["FAIL"] = max(0, counts.get("FAIL", 0) - 1)
                counts[st] = counts.get(st, 0) + 1
        failed = still

    if failed:
        with (engine.OUT / "failures.txt").open("a", encoding="utf-8") as fh:
            fh.writelines(f"{u}\t{r}\n" for u, r in failed)
        log.warning("%d URL tüm denemelere rağmen başarısız -> failures.txt", len(failed))

    ctx["done_total"] = done_total + len(urls)
    catalog.save()                       # seviye sonu anlık kayıt

    # incremental BFS birleşimi: yeni keşfedilen (bilinmeyen) URL'leri frontier'a ekle
    known: set = ctx["known"]
    CAP = 50000                           # sonsuz URL-tuzağına karşı güvenlik
    new = {u for u in harvested if u not in known}
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

    g = StateGraph(State)
    g.add_node("discover", _discover)
    g.add_node("expand", _expand)
    g.add_node("harvest", _harvest)
    g.set_entry_point("discover")
    g.add_edge("discover", "expand")
    g.add_edge("expand", "harvest")      # her seviye triage'ından sonra HEMEN indir
    g.add_conditional_edges("harvest", after_harvest,
                            {"expand": "expand", "END": END})
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
        "llm": llm, "embed": embed, "vec": vec,
        "catalog": store.Catalog(out / "_catalog.json"),
        "decisions": [], "delay": args.delay, "disco_cap": args.disco_cap,
    }

    budget = {"llm_calls_left": args.max_llm, "look_left": args.max_look,
              "max_depth": args.max_depth, "max_fetch": args.limit or args.max_fetch,
              "max_retries": args.max_retries}
    init: State = {"config": cfg, "mode": "", "frontier": [], "fetch": [],
                   "budget": budget, "stats": {}}

    limits = httpx.Limits(max_connections=12)
    async with httpx.AsyncClient(headers=engine.HEADERS, timeout=40,
                                 follow_redirects=True, limits=limits) as client:
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
    known = sorted(ctx.get("known", []))
    (out / "_universe.json").write_text(
        json.dumps({"mode": ctx.get("mode"), "count": len(known),
                    "cap_hit": len(known) >= 50000, "urls": known},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("=== BİTTİ (%s) ===", date.today().isoformat())
    log.info("indirme durumları: %s", final.get("stats"))
    log.info("kararlar: %d dal -> %s", len(ctx["decisions"]), out / "_decisions.json")


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
