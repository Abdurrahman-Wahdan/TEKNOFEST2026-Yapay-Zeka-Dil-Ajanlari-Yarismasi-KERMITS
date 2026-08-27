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

import hashlib
import math
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
    triage_failed: bool = False     # True -> verdict LLM'den DEĞİL, "güvenli varsayılan"
                                     # (LLM tüm denemelerine rağmen hiç yanıt vermedi) —
                                     # bu karar _decisions.json'a yazılsa da SONRAKİ
                                     # koşuda ÖNBELLEKTEN KULLANILMAZ, yeniden triage edilir


def node_to_dict(node: "Node") -> dict:
    """Ağacı JSON'a serileştirir — LLM kararı ne olursa olsun (DIVE/FETCH/SKIP/
    boş) TÜM yapı korunur, hiçbir dal atlanmaz. Diskte kayıt altına almak için
    (bkz. graph.py: save_tree_snapshot)."""
    return {
        "url": node.url, "title": node.title, "seg": node.seg, "depth": node.depth,
        "page_count": node.page_count, "verdict": node.verdict,
        "sample_titles": node.sample_titles,
        "children": [node_to_dict(c) for c in node.children],
    }


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.split("/") if s]


# --- HAYALET (şablon artığı) URL ELEMESİ -------------------------------------
# Bazı siteler sitemap/HTML'e, şablon motorunun ÇÖZEMEDİĞİ ham yer tutucuları
# olduğu gibi basıyor. Bunlar GERÇEK sayfa değildir: sunucu bağlantıyı hiç
# kurmaz ya da 404 döner, ama crawl bunları normal URL sanıp her biri için
# 3 retry × bağlantı zaman aşımı harcar.
#
# ÖLÇÜM (emlakkatilim, 2026-08-22): sitemap evrenindeki 1099 URL'in 464'ü
# (%42) bu türdendi — `.../~/Templates/Default/assets/{{link}}` gibi, üstelik
# aynı yer tutucu üst üste tekrarlanarak ("~/Templates/.../~/Templates/...").
# Hepsi bağlantı hatası veriyordu; saatlerce bekleyip SIFIR veri getiriyorlardı.
#
# BU BİR VERİ ELEMESİ DEĞİLDİR: sadece hiçbir zaman içerik döndüremeyecek
# sentetik yollar atılır. Gerçek bir sayfayı yanlışlıkla elememek için ölçüt
# DAR tutulmuştur — çözülmemiş şablon değişkeni ({{...}}, {%...%}, ${...}) ya
# da şablon motorunun iç yolu ("~/Templates/"). Elenenler çağıran tarafından
# gerekçesiyle kaydedilir (bkz. graph.py), yani hesapsız kalmazlar.
_HAYALET_IZLERI = ("{{", "}}", "{%", "${", "~/templates/")

# BOZUK LİNK ARTIĞI: sayfadaki `href="mailto:..."`, `href="https://..."` gibi
# bağlantılar yanlış çözümlendiğinde, yolun SON parçası çıplak bir URI
# ŞEMASINA dönüşüyor: `.../Sayfalar/https&`, `.../Sayfalar/mailto&`. Bunlar
# gerçek sayfa değil; hepsi 404 dönüyor ama her biri istek + 3 retry harcıyor.
#
# ÖLÇÜM (turkiyefinans, 2026-08-22): 744 FAIL'in 736'sı tam olarak bunlardı
# (`https&` 464, `http&` 248, `mailto&` 24). Kalan 8'i gerçek 404 sayfaydı.
#
# KURAL DAR: yalnız yolun SON parçası, TAMAMEN bir şema adı + ayraç ise
# elenir. Gerçek bir sayfanın adı "https&" olamaz, o yüzden yanlış eleme
# riski yok. `mailto-sikayet.aspx` gibi içinde şema adı GEÇEN gerçek
# sayfalar etkilenmez (tam eşleşme aranır).
_SEMA_ARTIKLARI = frozenset(
    f"{sema}{ayrac}"
    for sema in ("http", "https", "mailto", "tel", "javascript", "ftp", "file")
    for ayrac in ("&", "", ":", "&amp;")
)


def hayalet_url(url: str) -> bool:
    """Gerçek bir sayfaya ASLA karşılık gelemeyecek sahte URL mi?

    İki kaynak: (1) şablon motorunun çözemediği yer tutucu, (2) bozuk link
    çözümlemesinden doğan çıplak URI şeması (bkz. _SEMA_ARTIKLARI)."""
    u = (url or "").lower()
    if any(iz in u for iz in _HAYALET_IZLERI):
        return True
    son = urlparse(u).path.rstrip("/").rsplit("/", 1)[-1]
    return son in _SEMA_ARTIKLARI


def hayaletleri_ayikla(urls) -> tuple[set[str], list[str]]:
    """(gerçek URL'ler, elenen hayalet URL'ler) — sıralı ve tekrarsız."""
    gercek, hayalet = set(), set()
    for u in urls:
        (hayalet if hayalet_url(u) else gercek).add(u)
    return gercek, sorted(hayalet)


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


_SAMPLE_RATIO = 0.20   # dal başına LLM'e gösterilecek örnek oranı
_SAMPLE_FLOOR = 12     # küçük dallarda bu kadarı garanti gösterilir


def _pick_samples(weighted: list[tuple[str, int]]) -> list[str]:
    """(başlık, ağırlık) çiftlerini AĞIRLIĞA göre büyükten küçüğe sıralar,
    en büyük %10'unu (en az _SAMPLE_FLOOR) döner — sabit küçük bir sayıyla
    kesmek yerine dalın büyüklüğüyle orantılı örnekleme. Tek bir LLM çağrısı
    + 128k bağlam bütçesi olduğu için cömert davranılır; asıl güvenlik sınırı
    _branch_line'daki 8000 karakterlik kapak."""
    ordered = sorted(weighted, key=lambda x: -x[1])
    k = max(_SAMPLE_FLOOR, math.ceil(len(ordered) * _SAMPLE_RATIO))
    return [t for t, _ in ordered[:k]]


def _summarize(node: Node) -> int:
    """Her dal için page_count ve örnek başlıkları doldurur (alttan yukarı)."""
    if not node.children:
        node.page_count = 1
        return 1
    total = 0
    weighted: list[tuple[str, int]] = []
    for ch in node.children:
        c = _summarize(ch)
        total += c
        weighted.append((ch.title, c))
    node.page_count = total
    # en BÜYÜK (en çok sayfa taşıyan) alt-dallar önce — küçük/tekil sayfalar
    # yüzünden dalın asıl gövdesini oluşturan büyük alt-dal örneklemeden düşmesin.
    node.sample_titles = _pick_samples(weighted)
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


def leaf_fingerprint(node: Node) -> str:
    """Bir dalın altındaki TÜM yaprak URL'lerinin deterministik özeti (sha1).
    Aynı URL kümesi HER ZAMAN aynı fingerprint'i üretir; tek bir sayfa
    eklense/kaldırılsa bile değişir. Triage cache'i 'bu dal geçen koşuya göre
    HİÇ değişmemiş, LLM'e tekrar sorma' kararını buna dayandırır — tahmine/
    örneklemeye değil, tam eşitlik kontrolüne dayalı."""
    leaves = all_leaf_urls(node)
    return hashlib.sha1("\n".join(leaves).encode("utf-8")).hexdigest()


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
        anchor = re.sub(r"\s+", " ", anchor).strip()[:8000]
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
        # tekilleştir (aynı anchor metni birden çok sayfada tekrar edebilir,
        # slot'u işgal etmesin) — sonra dalın büyüklüğüyle orantılı örnekle.
        anchors = list(dict.fromkeys(a for _, a in items if a))
        k = max(_SAMPLE_FLOOR, math.ceil(len(anchors) * _SAMPLE_RATIO))
        node = Node(
            url=rep_url if len(items) == 1 else urljoin(url + "/", seg),
            title=seg.replace("-", " "),
            depth=depth + 1,
            seg=seg,
            page_count=len(items),
            sample_titles=anchors[:k],
        )
        nodes.append(node)
    return sorted(nodes, key=lambda n: -n.page_count)


# --- birleşik keşif giriş noktası -----------------------------------------
async def discover(client, config: dict, cap: int = 50000, max_depth: int = 0) -> tuple[str, Node]:
    """(mode, root_node) döner.

    Sitemap VE ana sayfa BFS tohumu HER ZAMAN birlikte alınır (sitemap varsa
    bile) — biri diğerini ekarte etmez. Sitemap genelde eksik/güncel-değil
    olabilir (yeni kampanya sayfaları eklenmeyebilir); ana sayfadan 1-hop BFS
    tohumu bunu ucuza tamamlar. Sitemap yoksa zaten BFS tohumu tek başına
    yeterli (site-içi tam kapanış crawl sırasındaki incremental link-harvest
    ile birikir — bkz. graph.harvest_node).
    max_depth=0 => derinlik sınırı yok (dedup zaten döngüyü engeller); cap yalnız
    sonsuz URL-tuzağına karşı güvenlik freni.
    """
    base = config["BASE"].rstrip("/")
    sm = await engine.discover_from_sitemaps(client) or set()
    bfs_seeds = {engine.clean_url(u) for u, _ in await links_with_anchors(client, base)}
    bfs_seeds.add(engine.clean_url(base))
    if sm:
        merged = sm | bfs_seeds
        return "sitemap+bfs-seed", build_tree_from_urls(merged, base)
    return "bfs(link-harvest)", build_tree_from_urls(bfs_seeds, base)
