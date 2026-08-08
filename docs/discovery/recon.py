"""Cheap first pass: harvest calculator-looking links from each bank homepage."""

import re
import time

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BANKS = [
    ("adil", "https://www.adilkatilim.com", True),
    ("albaraka", "https://www.albarakaturk.com.tr", False),
    ("dunya", "https://www.dunyakatilim.com", True),
    ("hayatfinans", "https://www.hayatfinans.com", True),
    ("kuveytturk", "https://www.kuveytturk.com.tr", False),
    ("tom", "https://www.tombank.com.tr", True),
    ("emlak", "https://www.emlakkatilim.com.tr", False),
    ("turkiyefinans", "https://www.turkiyefinans.com", False),
    ("vakif", "https://www.vakifkatilim.com.tr", False),
    ("ziraat", "https://www.ziraatkatilim.com.tr", False),
]

# Words that mark a live-calculation tool in Turkish banking sites.
HINT = re.compile(
    r"hesapla|hesaplama|simul|kar-payi|karpayi|kâr|getiri|taksit|odeme-plani",
    re.IGNORECASE,
)


def links(html: str, base: str) -> set[str]:
    out = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        if not HINT.search(href):
            continue
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = base.rstrip("/") + href
        else:
            continue
        out.add(url.split("#")[0])
    return out


def main() -> None:
    for key, url, digital in BANKS:
        tag = " [digital]" if digital else ""
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=25,
                          follow_redirects=True)
            found = sorted(links(r.text, str(r.url)))
            print(f"\n=== {key}{tag} — HTTP {r.status_code} — {len(r.text)} bytes "
                  f"— final: {r.url}")
            if not found:
                print("    (no calculator links in homepage HTML)")
            for link in found[:14]:
                print(f"    {link}")
            if len(found) > 14:
                print(f"    ... +{len(found) - 14} more")
        except Exception as exc:
            print(f"\n=== {key}{tag} — FAILED {type(exc).__name__}: {str(exc)[:90]}")
        time.sleep(1.5)  # be polite


main()
