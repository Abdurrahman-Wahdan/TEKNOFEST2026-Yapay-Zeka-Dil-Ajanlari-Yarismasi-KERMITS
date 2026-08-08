"""Third discovery method: sitemap.xml. Works even when pages are JS-rendered."""

import re
import time

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BANKS = {
    "adil": "https://www.adilkatilim.com.tr",
    "albaraka": "https://www.albaraka.com.tr",
    "hayatfinans": "https://hayatfinans.com.tr",
    "tom": "https://www.tombank.com.tr",
    "emlak": "https://www.emlakkatilim.com.tr",
    "turkiyefinans": "https://www.turkiyefinans.com.tr",
    "vakif": "https://www.vakifkatilim.com.tr",
    "ziraat": "https://www.ziraatkatilim.com.tr",
}

CALC = re.compile(r"hesaplama|simulas|taksit-hesap|odeme-plani", re.IGNORECASE)


def fetch(url: str):
    try:
        return httpx.get(url, headers={"User-Agent": UA}, timeout=25,
                         follow_redirects=True)
    except Exception:
        return None


def sitemap_urls(root: str) -> list[str]:
    """Find sitemaps via robots.txt, falling back to the conventional path."""
    maps = []
    r = fetch(root + "/robots.txt")
    if r is not None and r.status_code == 200:
        maps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    if not maps:
        maps = [root + "/sitemap.xml"]
    return maps[:4]


def main() -> None:
    for key, root in BANKS.items():
        print(f"\n{'=' * 70}\n{key}  {root}")
        hits: set[str] = set()
        seen_maps = 0

        for sm in sitemap_urls(root):
            r = fetch(sm)
            if r is None or r.status_code != 200:
                continue
            seen_maps += 1
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)

            # A sitemap index points at more sitemaps; follow a few.
            children = [u for u in locs if u.endswith(".xml")][:6]
            for child in children:
                cr = fetch(child)
                if cr is not None and cr.status_code == 200:
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", cr.text)
                time.sleep(0.3)

            hits |= {u for u in locs if CALC.search(u) and not u.endswith(".xml")}
            time.sleep(0.4)

        if not seen_maps:
            print("  no readable sitemap")
        elif not hits:
            print(f"  {seen_maps} sitemap(s), no calculator URLs")
        else:
            print(f"  {seen_maps} sitemap(s), {len(hits)} calculator URL(s):")
            for u in sorted(hits)[:18]:
                print(f"    {u}")
        time.sleep(1.0)


main()
