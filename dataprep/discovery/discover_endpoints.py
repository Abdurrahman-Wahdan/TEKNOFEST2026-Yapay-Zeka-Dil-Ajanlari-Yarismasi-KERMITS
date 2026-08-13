"""TEK SEFERLİK canlı endpoint KEŞFİ (exploratory — profesyonel/otomatize değil).

Amaç: katılım bankalarının sayfalarındaki İNTERAKTİF araçların (hesaplama araçları,
döviz çevirici, kâr payı hesaplama, altın/kur, kart taksit...) arkasındaki CANLI
XHR/fetch API çağrılarını yakalamak. Login/üyelik/başvuru akışlarıyla İLGİLENMİYORUZ.

Nasıl: Playwright her aday sayfayı açar ->
  1) PASİF dinleme: sayfa yüklenirken düşen tüm xhr/fetch + JSON yanıtları kaydeder.
  2) GENERIC etkileşim: sayfa yapısına bakmadan; sayı/metin input'larını örnek değerle
     doldurur, select'lerde dolu seçenek seçer, slider'ları ortalar, "Hesapla/Çevir/
     Sorgula/Göster/Getir" gibi butonlara basar -> tetiklenen hesaplama XHR'ını yakalar.
  3) Tracking/analytics ve auth/login host'ları ELENIR.

Çıktı (dataprep/discovery/out/ altında):
  raw_calls.jsonl   — yakalanan HER çağrı (ham, tam)
  endpoints.json    — tekilleştirilmiş, müşteriye-fayda ilgili endpoint kataloğu
  endpoints.md      — insan-okur özet (banka -> araç -> endpoint)

Kullanım (repo kökünden):
  python -m dataprep.discovery.discover_endpoints                 # aday araç sayfaları
  python -m dataprep.discovery.discover_endpoints --banks kuveytturk vakifkatilim
  python -m dataprep.discovery.discover_endpoints --limit 5       # hızlı deneme
  python -m dataprep.discovery.discover_endpoints --headed        # tarayıcıyı gör
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = Path(__file__).resolve().parent / "out"

# --- aday sayfaları topla (mevcut crawl'dan URL'ler + araç anahtar kelimeleri) ---
# DAR: yalnız GERÇEK interaktif ARAÇ sayfaları (calculator/çevirici). Geniş "hesaplar/
# altin/kur" sayfaları (yeni endpoint getirmeyen, sadece tracking tetikleyen) HARİÇ.
TOOL_KW = re.compile(
    r"(hesapla(ma)?|hesap-makinesi|makinesi|cevirici|çevirici|-araclari|"
    r"doviz-cevirici|döviz-cevirici|kar-payi-hesap|kar-paylasim-oran|"
    r"kart-taksit|finansman-hesap|leasing.*hesap|simul|"
    r"canli-.*(fiyat|kur)|kurlar-?ve-?)", re.I)

# login/üyelik/başvuru = İLGİLENMİYORUZ (sayfa bazında ele)
SKIP_PAGE = re.compile(
    r"(giris|login|oturum|uye-ol|uyelik|kayit|register|logout|cikis|sifre|"
    r"parola|basvuru-formu|internet-bankacilig|mobil-bankacilik-giris|sso|oauth)", re.I)

# yakalanan çağrılarda ELENECEK host'lar (analytics/tracking/reklam/cdn-font)
SKIP_HOST = re.compile(
    r"(google|gstatic|googletagmanager|doubleclick|facebook|fbcdn|hotjar|"
    r"clarity\.ms|yandex|criteo|adservice|analytics|segment|mixpanel|"
    r"cloudflareinsights|newrelic|sentry|fonts\.|typekit|cookiebot|onetrust|"
    r"recaptcha|gtag|matomo|piwik|taboola|linkedin|twitter|tiktok|"
    r"dataroid|weaccess|chatbot\.|boa\.messaging|insider|useinsider|"
    r"visualwebsiteoptimizer|vwo|inspectlet|mouseflow|fullstory|"
    r"cdn-|assets\.|static\.cloudflare|bugsnag|datadog|amplitude)", re.I)

# müşteriye-fayda ilgili endpoint sinyali (URL veya döndürdüğü içerik)
RELEVANT_KW = re.compile(
    r"(hesapla|calculat|kar-?pay|karpay|profit|rate|oran|doviz|döviz|kur|"
    r"currency|exchange|altin|altın|gold|gumus|silver|taksit|install|"
    r"finansman|leasing|kredi|loan|fiyat|price|tahsilat|odeme)", re.I)

CLICK_TEXT = re.compile(
    r"(hesapla|hesaplama|çevir|cevir|sorgula|göster|goster|getir|hesabı gör|"
    r"sonuc|sonuç|calculate)", re.I)


def candidate_pages(banks: list[str] | None, limit: int | None) -> dict[str, list[str]]:
    """slug -> aday araç sayfası URL listesi (mevcut crawl md frontmatter'larından)."""
    out: dict[str, list[str]] = defaultdict(list)
    for site in sorted(DATA.glob("*_site")):
        slug = site.name[:-5]
        if banks and slug not in banks:
            continue
        seen = set()
        for md in site.rglob("*.md"):
            if "pdf_text" in md.parts or "image_text" in md.parts:
                continue
            try:
                head = md.read_text(encoding="utf-8")[:400]
            except Exception:
                continue
            m = re.search(r'url:\s*"?([^"\n]+)"?', head)
            if not m:
                continue
            url = m.group(1).strip()
            if "{%" in url or "item.Url" in url:
                continue
            if not TOOL_KW.search(url) or SKIP_PAGE.search(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            out[slug].append(url)
        out[slug].sort()
        if limit:
            out[slug] = out[slug][:limit]
    return out


def visit(page, url: str, calls: list, wait_ms: int = 1200) -> None:
    """Sayfayı aç, pasif çağrıları topla, sonra generic etkileşimle hesaplamayı tetikle."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception as e:
        print(f"      goto hata: {type(e).__name__}")
        return
    page.wait_for_timeout(wait_ms)   # pasif XHR'lar insin (insani gecikme yok, hızlı)

    # çerez/izin banner'ını kapat (etkileşimi engellemesin) — best effort
    for t in ("Kabul", "Kabul Et", "Tümünü Kabul", "Onayla", "Anladım", "Accept"):
        try:
            b = page.get_by_role("button", name=re.compile(t, re.I))
            if b.count():
                b.first.click(timeout=800)
                break
        except Exception:
            pass

    # 1) input'ları örnek değerle doldur + input/change event'i tetikle
    try:
        page.eval_on_selector_all(
            "input, select",
            """els => els.forEach(el => {
                const t=(el.type||'').toLowerCase();
                if (el.tagName==='SELECT') {
                    const o=[...el.options].find(o=>o.value && !/seç|seçin|choose/i.test(o.text));
                    if(o){el.value=o.value;}
                } else if (['number','text','tel'].includes(t) || t==='') {
                    if(!/ara|search|kod|tc|telefon|mail|iban/i.test(el.name+el.id+el.placeholder))
                        el.value='10000';
                } else if (t==='range') { el.value=el.max? (+el.min+ +el.max)/2 : 50; }
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            })""")
    except Exception:
        pass
    page.wait_for_timeout(500)

    # 2) hesapla/çevir/sorgula butonlarına bas — HIZLI: eşleşen butonları tek JS taramasıyla
    # bul (yavaş inner_text döngüsü yok), en fazla ilk 4'üne tıkla.
    try:
        idxs = page.evaluate(
            """() => {
                const re=/(hesapla|hesaplama|çevir|cevir|sorgula|göster|goster|getir|calculate|sonu)/i;
                const els=[...document.querySelectorAll('button,input[type=button],input[type=submit],a')];
                const out=[]; els.forEach((e,i)=>{ if(re.test((e.innerText||'')+(e.value||''))) out.push(i); });
                return out.slice(0,4);
            }""")
        btns = page.locator("button, input[type=button], input[type=submit], a")
        for i in idxs:
            try:
                btns.nth(i).click(timeout=1200, no_wait_after=True)
                page.wait_for_timeout(700)
            except Exception:
                pass
    except Exception:
        pass
    page.wait_for_timeout(600)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--banks", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--urls", nargs="*", help="doğrudan bu URL'leri gez (test)")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    if args.urls:
        pages = {"test": list(args.urls)}
    else:
        pages = candidate_pages(args.banks, args.limit)
    total = sum(len(v) for v in pages.values())
    print(f"Aday araç sayfası: {total} (banka: {len(pages)})")
    for slug, urls in pages.items():
        print(f"  {slug}: {len(urls)}")

    # EKLEME + RESUME: mevcut kayıtlar DURUR; ziyaret edilmiş sayfalar atlanır.
    raw_path = OUT / "raw_calls.jsonl"
    captured: list[dict] = []
    visited: set[str] = set()
    written: set[str] = set()          # kaydedilmiş benzersiz endpoint anahtarları (dedup)
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            u = urlparse(r["url"])
            ekey = f'{r["slug"]}|{r["method"]}|{u.netloc}{u.path}'
            if ekey in written:        # eski dosyada dup varsa yükte de tekilleştir
                continue
            written.add(ekey)
            captured.append(r)
            visited.add(r.get("from_page", ""))
    vpath = OUT / "visited.json"          # çağrı üretmeyen sayfalar da (adilkatilim gibi)
    if vpath.exists():
        visited |= set(json.loads(vpath.read_text()))
    print(f"Önceki kayıt: {len(captured)} çağrı, {len(visited)} sayfa ziyaret edilmiş (atlanacak)")

    # yalnız ATLANMAMIŞ sayfaları gez
    for slug in list(pages):
        pages[slug] = [u for u in pages[slug] if u not in visited]
    total = sum(len(v) for v in pages.values())
    print(f"Yapılacak (yeni) sayfa: {total}")

    raw_f = raw_path.open("a", encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
            locale="tr-TR", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        cur = {"slug": "", "page": ""}

        def on_response(resp):
            try:
                req = resp.request
                rtype = req.resource_type
                host = urlparse(resp.url).netloc
                if SKIP_HOST.search(host):
                    return
                ct = (resp.headers or {}).get("content-type", "")
                # ilgi: xhr/fetch VEYA json yanıt VEYA URL'de api/hesapla sinyali
                is_api = rtype in ("xhr", "fetch") or "json" in ct.lower() \
                    or re.search(r"/api/|/service|\.asmx|\.ashx|\.json|/rest/", resp.url, re.I)
                if not is_api:
                    return
                # HIZLI GEÇ: bu endpoint zaten kaydedildiyse gövdeyi bile okuma, anında dön.
                u0 = urlparse(resp.url)
                if f'{cur["slug"]}|{req.method}|{u0.netloc}{u0.path}' in written:
                    return
                body = ""
                try:
                    if "json" in ct.lower() or rtype in ("xhr", "fetch"):
                        body = resp.text()[:4000]
                except Exception:
                    body = ""
                rec = {
                    "slug": cur["slug"], "from_page": cur["page"],
                    "method": req.method, "url": resp.url, "status": resp.status,
                    "resource_type": rtype, "content_type": ct,
                    "post_data": (req.post_data or "")[:2000],
                    "resp_sample": body,
                    "relevant": bool(RELEVANT_KW.search(resp.url + " " + body)),
                }
                # ÇÖP + DUPLICATE birikmesin: yalnız ilgili VE her benzersiz endpoint'ten
                # İLK örneği kaydet (slug+method+host+path). Aynı endpoint'in yüzlerce
                # tekrarı dosyaya yazılmaz -> raw_calls tekil kalır (~17 satır).
                if rec["relevant"]:
                    u = urlparse(resp.url)
                    ekey = f'{cur["slug"]}|{req.method}|{u.netloc}{u.path}'
                    if ekey not in written:
                        written.add(ekey)
                        raw_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        raw_f.flush()
                        captured.append(rec)
            except Exception:
                pass

        page.on("response", on_response)

        for slug, urls in pages.items():
            cur["slug"] = slug
            print(f"\n=== {slug} ({len(urls)} sayfa) ===")
            for i, url in enumerate(urls, 1):
                cur["page"] = url
                print(f"  [{i}/{len(urls)}] {url}")
                visit(page, url, captured)
                visited.add(url)                     # gezildi -> resume'da atlanır
                json.dump(sorted(visited), vpath.open("w"), ensure_ascii=False)
        browser.close()
    raw_f.close()
    json.dump(sorted(visited), vpath.open("w"), ensure_ascii=False)

    # --- tekilleştir + katalog -------------------------------------------------
    def key(r):  # method + host + path (+ query anahtarları) -> tekil endpoint
        u = urlparse(r["url"])
        return f'{r["method"]} {u.netloc}{u.path}'

    uniq: dict[str, dict] = {}
    for r in captured:
        k = key(r)
        if k not in uniq:
            uniq[k] = {**r, "hits": 0, "seen_pages": set()}
        uniq[k]["hits"] += 1
        uniq[k]["seen_pages"].add(r["from_page"])

    cat = []
    for k, r in sorted(uniq.items(), key=lambda x: (-int(x[1]["relevant"]), x[0])):
        cat.append({
            "endpoint": k, "method": r["method"], "url": r["url"],
            "slug": r["slug"], "relevant": r["relevant"],
            "content_type": r["content_type"], "hits": r["hits"],
            "example_post": r["post_data"], "example_resp": r["resp_sample"][:800],
            "seen_on": sorted(r["seen_pages"])[:5],
        })
    (OUT / "endpoints.json").write_text(
        json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")

    # markdown özet
    rel = [c for c in cat if c["relevant"]]
    lines = [f"# Keşfedilen Canlı Endpoint'ler\n",
             f"Toplam tekil endpoint: **{len(cat)}** — müşteriye-fayda ilgili: **{len(rel)}**\n"]
    by_bank = defaultdict(list)
    for c in rel:
        by_bank[c["slug"]].append(c)
    for slug in sorted(by_bank):
        lines.append(f"\n## {slug}\n")
        for c in by_bank[slug]:
            lines.append(f"- `{c['method']} {c['url']}` (×{c['hits']})")
            if c["example_post"]:
                lines.append(f"  - istek: `{c['example_post'][:200]}`")
            if c["example_resp"]:
                lines.append(f"  - yanıt: `{c['example_resp'][:200]}`")
    (OUT / "endpoints.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\nBİTTİ. Yakalanan çağrı: {len(captured)} | tekil endpoint: {len(cat)} "
          f"| ilgili: {len(rel)}")
    print(f"  -> {OUT/'endpoints.json'}\n  -> {OUT/'endpoints.md'}\n  -> {OUT/'raw_calls.jsonl'}")


if __name__ == "__main__":
    main()
