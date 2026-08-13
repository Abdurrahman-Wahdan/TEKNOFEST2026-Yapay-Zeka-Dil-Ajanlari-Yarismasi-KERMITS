"""FRONTIER — siteyi keşfet ve URL ağacını üret.

İki mod, ikisi de aynı triage'ı besler:

  * sitemap : sitemap.xml/robots varsa tüm URL'ler topluca alınır, path
              segmentlerinden bir ağaç kurulur (ucuz, tam görünürlük).
  * bfs     : sitemap yok/yetersizse ana sayfadan başlanır; linkler + anchor
              metinleri çıkarılır. Ağaç, triage "DIVE" dediği dallar
              genişletildikçe kademeli büyür (tüm site kazınmaz).

Ağ/keşif ilkelleri (fetch, wanted, same_domain, discover_from_sitemaps ...)
download_sites'taki banka motorundan yeniden kullanılır — kopya yok.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

# Mevcut motoru yeniden kullan: banka modülü ağ + filtre fonksiyonlarını taşır.
from dataprep.crawl.bank import engine


# --- ağaç düğümü -----------------------------------------------------------
@dataclass
class Node:
    """Frontier ağacındaki bir dal ya da yaprak."""
    url: str
    title: str = ""                 # sitemap'te path adı, bfs'te anchor metni
    depth: int = 0
    seg: str = ""                   # bu düğümün path segmenti (/kredi -> "kredi")
    children: list["Node"] = field(default_factory=list)
    sample_titles: list[str] = field(default_factory=list)  # dal ise alt başlık örnekleri
    page_count: int = 1             # dal ise altındaki tahmini sayfa sayısı
    verdict: str = ""               # triage sonucu: DIVE | FETCH | SKIP


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.split("/") if s]


# --- sitemap modu: URL kümesinden path ağacı ------------------------------
def build_tree_from_urls(urls: set[str], base: str) -> Node:
    """Düz URL kümesini path segmentlerine göre bir ağaca dönüştürür.

    Her iç düğüm bir dalı (örn. /kampanyalar), her yaprak gerçek bir sayfayı
    temsil eder. Dal düğümleri, triage'a örnek başlık ve sayfa sayısı taşır.
    """
    root = Node(url=base.rstrip("/"), title="(kök)", depth=0, seg="")
    index: dict[tuple[str, ...], Node] = {(): root}

    for u in sorted(urls):
        segs = tuple(_segments(u))
        # kök sayfanın kendisi
        if not segs:
            continue
        # her ara segment için dal düğümünü oluştur/bul
        for i in range(1, len(segs) + 1):
            key = segs[:i]
            if key in index:
                continue
            parent = index[key[:-1]]
            node = Node(
                url=urljoin(base + "/", "/".join(key)),
                title=key[-1].replace("-", " "),
                depth=i,
                seg=key[-1],
            )
            parent.children.append(node)
            index[key] = node
        # yaprağa gerçek URL'i işaretle (query'li olabilir)
        leaf = index[tuple(segs)]
        leaf.url = u

    _summarize(root)
    return root


def _summarize(node: Node) -> int:
    """Her dal için page_count ve örnek başlıkları doldurur (alttan yukarı)."""
    if not node.children:
        node.page_count = 1
        return 1
    total = 0
    titles: list[str] = []
    for ch in node.children:
        total += _summarize(ch)
        titles.append(ch.title)
    node.page_count = total
    node.sample_titles = titles[:12]
    return total


def top_level(root: Node) -> list[Node]:
    """Kökün hemen altındaki dallar — triage'ın ilk baktığı seviye."""
    return sorted(root.children, key=lambda n: -n.page_count)


def all_leaf_urls(root: Node) -> list[str]:
    """Ağaçtaki tüm YAPRAK (gerçek sayfa) URL'leri — keşif evreni (universe)."""
    out: list[str] = []

    def walk(n: Node):
        if not n.children:
            out.append(n.url)
        for ch in n.children:
            walk(ch)
    walk(root)
    return sorted(set(out))


# --- bfs modu: bir sayfadan link + anchor çıkar ---------------------------
_LINK_RE = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


async def links_with_anchors(client, url: str) -> list[tuple[str, str]]:
    """Bir sayfayı indirmeden-kaydetmeden linklerini (url, anchor_metni) çıkarır.

    Yalnızca keşif için; içerik yazımı yapılmaz (format/PDF motoru bozulmaz).
    """
    r, err = await engine.fetch(client, url)
    if r is None or "html" not in r.headers.get("content-type", "").lower():
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, inner in _LINK_RE.findall(r.text):
        u = engine.clean_url(urljoin(url, href.strip()))
        if u in seen or not engine.wanted(u):
            continue
        seen.add(u)
        anchor = _TAG_RE.sub(" ", inner)
        anchor = re.sub(r"\s+", " ", anchor).strip()[:120]
        out.append((u, anchor))
    return out


def bfs_children(url: str, links: list[tuple[str, str]], depth: int) -> list[Node]:
    """BFS keşfinde bulunan linkleri, aynı ilk-segmente göre dallara toplar."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for u, anchor in links:
        segs = _segments(u)
        if len(segs) <= depth:
            continue
        groups[segs[depth]].append((u, anchor))
    nodes: list[Node] = []
    for seg, items in groups.items():
        rep_url, _ = items[0]
        node = Node(
            url=rep_url if len(items) == 1 else urljoin(url + "/", seg),
            title=seg.replace("-", " "),
            depth=depth + 1,
            seg=seg,
            page_count=len(items),
            sample_titles=[a for _, a in items if a][:12],
        )
        nodes.append(node)
    return sorted(nodes, key=lambda n: -n.page_count)


# --- birleşik keşif giriş noktası -----------------------------------------
# en son BFS keşfinde güvenlik cap'ine ulaşıldı mı (True ise keşif EKSİK olabilir)
LAST_BFS_CAP_HIT = False


async def discover(client, config: dict, cap: int = 50000, max_depth: int = 0) -> tuple[str, Node]:
    """(mode, root_node) döner.

    Önce sitemap denenir; boşsa BFS ile site-içi TAM transitif kapanış alınır.
    max_depth=0 => derinlik sınırı yok (dedup zaten döngüyü engeller); cap yalnız
    sonsuz URL-tuzağına karşı güvenlik freni.
    """
    base = config["BASE"].rstrip("/")
    # HIZLI BAŞLANGIÇ (dosyalar hemen aksın): sitemap varsa onu tohum al; yoksa
    # ana sayfa linklerini. Site-içi TAM BFS kapanışı ayrıca yapılmaz — bunun
    # yerine crawl sırasında indirilen HER sayfadan linkler toplanıp yeni URL'ler
    # kuyruğa eklenir (incremental BFS birleşimi; graph.harvest_node içinde).
    sm = await engine.discover_from_sitemaps(client) or set()
    if sm:
        return "sitemap+link-harvest", build_tree_from_urls(sm, base)
    seeds = {engine.clean_url(u) for u, _ in await links_with_anchors(client, base)}
    seeds.add(engine.clean_url(base))
    return "bfs(link-harvest)", build_tree_from_urls(seeds, base)


async def _bfs_harvest_urls(client, base: str, cap: int = 50000, max_depth: int = 0) -> set[str]:
    """Sitemap'siz sitede site-içi TAM URL kapanışını özyinelemeli çıkarır.

    Kuyruk boşalana kadar TÜM site-içi wanted linkler takip edilir (dedup ile
    döngü yok). max_depth=0 => derinlik sınırı yok. cap yalnız sonsuz URL-tuzağı
    (takvim/faceted/session) için güvenlik; ulaşılırsa LAST_BFS_CAP_HIT=True
    (keşif eksik olabilir sinyali). Sadece keşif — içerik kaydedilmez.
    """
    global LAST_BFS_CAP_HIT
    from collections import deque
    start = engine.clean_url(base)
    seen: set[str] = {start}
    urls: set[str] = {start}
    q: deque[tuple[str, int]] = deque([(start, 0)])
    while q and len(seen) < cap:
        url, d = q.popleft()
        if max_depth and d >= max_depth:      # 0 => sınırsız
            continue
        for u, _anchor in await links_with_anchors(client, url):
            if u not in seen:
                seen.add(u)
                urls.add(u)
                if len(seen) < cap:
                    q.append((u, d + 1))
    LAST_BFS_CAP_HIT = len(seen) >= cap
    return urls
