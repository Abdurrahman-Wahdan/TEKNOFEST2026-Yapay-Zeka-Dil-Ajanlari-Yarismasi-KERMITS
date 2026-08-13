"""Sweep a bank's calculator pages and record every backend call.

Strategy per page, deliberately blunt so it works without page-specific knowledge:
  1. fill every visible numeric-ish input with a plausible value
  2. click every control whose text looks like a calculate/plan trigger
  3. record every new same-origin XHR, with request and response bodies

Writes results to <out>.json for compiling later.

Usage: python sweep.py <bank_key> <out.json> <url> [url ...]
"""

import asyncio
import json
import re
import sys
import warnings

warnings.filterwarnings("ignore")

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

CFG = {"playwright": {"transport": "stdio", "command": "npx",
                      "args": ["-y", "@playwright/mcp@latest", "--headless", "--isolated"]}}

NOISE = re.compile(
    r"google|gstatic|doubleclick|facebook|dataroid|clevertap|weaccess|hotjar|clarity|"
    r"analytics|gtm|recaptcha|fonts|cloudflare|adobedtm|onetrust|matomo|adrum|yandex|"
    r"insider|criteo|mookie|getmedia|\.png|\.jpg|\.jpeg|\.css|\.woff|\.svg|\.gif|"
    r"\.ico|\.mp4|\.ttf|\.axd|\.js(\?|$)",
    re.IGNORECASE,
)

# Anything that looks like a calculation result rather than page furniture.
PAYLOAD = re.compile(
    r"taksit|installment|tutar|amount|oran|rate|plan|profit|kar|payback|total|"
    r"getiri|birim|katilma|katilim|dividend|brut|net|kkdf|bsmv", re.I
)

FILL_ALL = """
(amount, term) => {
  const setV = (el, v) => {
    const proto = el.tagName === 'SELECT' ? HTMLSelectElement : HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
    setter.call(el, v);
    for (const t of ['input','change','keyup','blur']) el.dispatchEvent(new Event(t, {bubbles:true}));
  };
  const out = [];
  for (const el of document.querySelectorAll('input, select')) {
    if (['hidden','submit','search','checkbox','image','button','radio','file'].includes(el.type)) continue;
    if (el.offsetParent === null && el.type !== 'range') continue;   // skip invisible
    const tag = (el.id + ' ' + el.name + ' ' + (el.placeholder||'') + ' ' +
                 (el.labels && el.labels[0] ? el.labels[0].innerText : '')).toLowerCase();
    if (/arama|search|query|iban|e-?mail|phone|telefon|tckn|isim|surname|ad-soyad/.test(tag)) continue;
    let v = null;
    if (/vade|maturity|taksit|installment|ay\\b|month|gun|day|donem|sure|süre|period/.test(tag)) v = term;
    else if (/tutar|amount|anapara|ana-para|bedel|money|balance|finansman|kredi|deposit|loan|price|katilma|katilim|kar-?payi|karpayi|birim|getiri|fon|yatirim|kira|murabaha|sukuk|altin|gram|miktar/.test(tag)) v = amount;
    if (v === null) continue;
    if (el.tagName === 'SELECT') {
      const opts = [...el.options].map(o => ({o, n: parseFloat((o.value||o.text).replace(/[^0-9.]/g,''))}))
                                  .filter(x => !isNaN(x.n));
      if (!opts.length) continue;
      const want = parseFloat(v);
      const best = opts.sort((a,b) => Math.abs(a.n-want) - Math.abs(b.n-want))[0].o;
      setV(el, best.value);
      out.push((el.id||el.name) + '=' + best.value);
    } else {
      setV(el, v);
      out.push((el.id||el.name) + '=' + v);
    }
  }
  return JSON.stringify(out.slice(0, 12));
}
"""

CLICK_ALL = """
() => {
  const nav = /^(hesaplar|hesaplama ara)/i;              // nav labels, not triggers
  const go  = /^(hesapla|ödeme plan|odeme plan|planı görüntüle|plani goruntule|hesap makine|kâr payı hesapla|kar payi hesapla|kredi hesapla|finansman hesapla|getiri hesapla|katılma hesabı|simüle|simule|göster|goster|sorgula)/i;
  const hits = [...document.querySelectorAll('button,[role=button],a,input[type=submit]')]
    .filter(e => {
      const txt = (e.innerText || e.value || '').trim();
      if (!go.test(txt) || nav.test(txt)) return false;
      // Anchors with a real href navigate away and destroy the page state.
      if (e.tagName === 'A') {
        const h = e.getAttribute('href') || '';
        if (h && h !== '#' && !h.startsWith('javascript')) return false;
      }
      return true;
    });
  const names = [];
  for (const h of hits.slice(0, 5)) {
    names.push((h.innerText || h.value || '').trim().slice(0, 30));
    try { h.click(); } catch (e) {}
  }
  return JSON.stringify(names);
}
"""


def text(result) -> str:
    if isinstance(result, list):
        return "\n".join(
            str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in result
        )
    return str(result)


def strip(raw: str) -> str:
    m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", raw, re.S)
    return (m.group(1) if m else raw).strip()


async def main(bank: str, out_path: str, urls: list[str]) -> None:
    results = []
    async with MultiServerMCPClient(CFG).session("playwright") as session:
        t = {x.name: x for x in await load_mcp_tools(session)}

        async def call(name, **kw):
            return text(await t[name].ainvoke(kw))

        host = ""
        for url in urls:
            print(f"\n--- {url}", flush=True)
            try:
                await call("browser_navigate", url=url)
                await call("browser_wait_for", time=5)
                host = re.sub(r"^https?://", "", url).split("/")[0]

                before = set((await call("browser_network_requests", static=True)).splitlines())

                filled = strip(await call("browser_evaluate",
                                          function=f"() => (({FILL_ALL})('250000','24'))"))
                print(f"    filled: {filled[:150]}", flush=True)

                clicked = strip(await call("browser_evaluate", function=f"() => (({CLICK_ALL})())"))
                print(f"    clicked: {clicked[:150]}", flush=True)
                await call("browser_wait_for", time=6)

                after = await call("browser_network_requests", static=True)
                new = [ln for ln in after.splitlines()
                       if ln not in before and ln.strip() and not NOISE.search(ln)]

                nums = [int(m) for m in re.findall(r"^\s*(\d+)\.\s*\[(?:POST|GET)\]",
                                                   "\n".join(new), re.M)]
                for i in nums[:8]:
                    resp = strip(await call("browser_network_request", index=i,
                                            part="response-body"))
                    if not PAYLOAD.search(resp) or len(resp) < 40:
                        continue
                    line = next((l for l in new if re.match(rf"\s*{i}\.", l)), "")
                    m = re.search(r"\[(\w+)\]\s+(\S+)", line)
                    req = strip(await call("browser_network_request", index=i,
                                           part="request-body"))
                    hdr = strip(await call("browser_network_request", index=i,
                                           part="request-headers"))
                    entry = {
                        "page": url,
                        "method": m.group(1) if m else "?",
                        "endpoint": m.group(2) if m else "?",
                        "request_body": req[:700],
                        "response": resp[:700],
                        "headers": [h for h in hdr.splitlines()
                                    if re.match(r"(content-type|x-|accept|referer):", h, re.I)][:6],
                    }
                    results.append(entry)
                    print(f"    >> {entry['method']} {entry['endpoint'][:120]}", flush=True)
            except Exception as exc:
                print(f"    ! {type(exc).__name__}: {str(exc)[:110]}", flush=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"bank": bank, "host": host, "endpoints": results}, fh,
                  ensure_ascii=False, indent=2)
    print(f"\n== {bank}: {len(results)} endpoint call(s) -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
