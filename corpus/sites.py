"""The ten sites, as data.

Everything that differed between the old crawlers lives here. They were ten
byte-identical 410-line files whose only differences were the two literals at the
top, which meant every defect existed in ten copies and a fix had to be made ten
times. One engine reads this table instead.

`slug` names a bank registered in `banks/providers`, and `display_name` repeats
that bank's own name, so a document and a live price quote always agree about who
a bank is. A unit test enforces both.

    from corpus.sites import get_site, list_sites

    get_site("kuveytturk").sitemaps
"""

from .models import Site

# Cookie/KVKK notices that the site template pastes into most of its pages. Only
# Dünya Katılım needs one so far: a 6,928-char notice repeated in 190 of its 272
# documents, 63.8% of everything that bank publishes. Matched as a prefix
# fingerprint, not a full string, so a wording tweak does not silently stop
# stripping it.
_DUNYA_COOKIE = (
    "ÇEREZ KULLANIMINA İLİŞKİN AYDINLATMA METNİ",
    "1. Giriş Bu metin, 6698 sayılı Kişisel Verilerin Korunması Kanunu",
    "Sitemizden çıkış yapıp başka bir web sitesine yönlendiriliyorsunuz.",
)

SITES: list[Site] = [
    Site(
        slug="kuveytturk",
        display_name="Kuveyt Türk Katılım Bankası",
        base="https://www.kuveytturk.com.tr",
        root_domain="kuveytturk.com.tr",
        host="www.kuveytturk.com.tr",
        mode="auto",
        sitemaps=("https://www.kuveytturk.com.tr/sitemap.xml",),
    ),
    Site(
        slug="albaraka",
        display_name="Albaraka Türk Katılım Bankası",
        base="https://www.albaraka.com.tr",
        root_domain="albaraka.com.tr",
        host="www.albaraka.com.tr",
        mode="auto",
        sitemaps=("https://www.albaraka.com.tr/sitemap.xml",),
        include_prefixes=("/tr",),
        extra_seeds=("https://www.albaraka.com.tr/tr",),
    ),
    Site(
        slug="vakif",
        display_name="Vakıf Katılım Bankası",
        base="https://www.vakifkatilim.com.tr",
        root_domain="vakifkatilim.com.tr",
        host="www.vakifkatilim.com.tr",
        mode="auto",
        sitemaps=("https://www.vakifkatilim.com.tr/sitemap-tr.xml",),
        extra_seeds=("https://www.vakifkatilim.com.tr/tr",),
    ),
    Site(
        slug="emlak",
        display_name="Türkiye Emlak Katılım Bankası",
        base="https://www.emlakkatilim.com.tr",
        root_domain="emlakkatilim.com.tr",
        host="www.emlakkatilim.com.tr",
        mode="auto",
        sitemaps=("https://www.emlakkatilim.com.tr/sitemap.xml",),
        include_prefixes=("/tr",),
        extra_seeds=("https://www.emlakkatilim.com.tr/tr",),
    ),
    Site(
        # No "www.". Folding it away globally would have merged this host with a
        # different one at another bank, so the canonical host is per-site.
        slug="dunya",
        display_name="Dünya Katılım Bankası",
        base="https://dunyakatilim.com.tr",
        root_domain="dunyakatilim.com.tr",
        host="dunyakatilim.com.tr",
        mode="auto",
        sitemaps=("https://dunyakatilim.com.tr/sitemap.xml",),
        boilerplate=_DUNYA_COOKIE,
    ),
    Site(
        slug="ziraat",
        display_name="Ziraat Katılım Bankası",
        base="https://www.ziraatkatilim.com.tr",
        root_domain="ziraatkatilim.com.tr",
        host="www.ziraatkatilim.com.tr",
        mode="auto",
        sitemaps=("https://www.ziraatkatilim.com.tr/sitemap.xml",),
    ),
    Site(
        # Recursive: publishes no usable sitemap. Its pages are identified by
        # query string (?category=, ?financeID=), which is why the old crawler's
        # path-only filenames collapsed ~300 of them onto each other.
        slug="turkiyefinans",
        display_name="Türkiye Finans Katılım Bankası",
        base="https://www.turkiyefinans.com.tr",
        root_domain="turkiyefinans.com.tr",
        host="www.turkiyefinans.com.tr",
        mode="recursive",
        include_prefixes=("/tr-tr", "/tr"),
        extra_seeds=(
            "https://www.turkiyefinans.com.tr/tr-tr/Sayfalar/default.aspx",
        ),
    ),
    Site(
        # Like Dünya, Hayat redirects www -> no-www, so the canonical host has no
        # www. Getting this wrong is not fatal (every fetch just redirects) but it
        # stores URLs the server does not consider canonical, and costs a redirect
        # on every request. Verified against the live server, 2026-08-09.
        slug="hayat",
        display_name="Hayat Finans Katılım Bankası",
        base="https://hayatfinans.com.tr",
        root_domain="hayatfinans.com.tr",
        host="hayatfinans.com.tr",
        mode="auto",
        sitemaps=("https://hayatfinans.com.tr/sitemap.xml",),
    ),
    Site(
        slug="tom",
        display_name="T.O.M. Katılım Bankası",
        base="https://www.tombank.com.tr",
        root_domain="tombank.com.tr",
        host="www.tombank.com.tr",
        mode="recursive",
    ),
    Site(
        slug="adil",
        display_name="Adil Katılım Bankası",
        base="https://www.adilkatilim.com.tr",
        root_domain="adilkatilim.com.tr",
        host="www.adilkatilim.com.tr",
        mode="recursive",
    ),
]


def get_site(slug: str) -> Site:
    """Look up one site.

    Raises:
        ValueError: on an unknown slug, listing the valid ones. Every lookup in
            this project fails that way, so a typo says what to type instead.
    """
    for site in SITES:
        if site.slug == slug.lower():
            return site
    available = ", ".join(s.slug for s in SITES)
    raise ValueError(f"Unknown site: {slug!r}. Available: {available}")


def list_sites() -> list[str]:
    """Every site slug, in registry order."""
    return [s.slug for s in SITES]
