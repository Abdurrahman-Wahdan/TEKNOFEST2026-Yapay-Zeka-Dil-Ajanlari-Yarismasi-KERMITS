"""Capture EVERY backend call a bank's pages make. No vocabulary filtering.

Filtering by Turkish banking words lost calculators (participation banks use
kâr payı / finansman, not faiz / kredi, and each bank words things its own way).
So: fill every field, click every non-navigating control, record every
same-origin request that is not a static asset. Decide what matters afterwards.

Usage: python capture.py <bank> <out.json> <url> [url ...]
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

# Only third-party trackers and static files. Nothing domain-specific.
DROP = re.compile(
    r"google|gstatic|doubleclick|facebook|dataroid|clevertap|weaccess|hotjar|clarity|"
    r"analytics|googletagmanager|recaptcha|fonts\.|cloudflare|adobedtm|onetrust|matomo|"
    r"adrum|yandex|insider|criteo|mookie|efilli|addevent|jsdelivr|"
    r"\.png|\.jpg|\.jpeg|\.css|\.woff2?|\.svg|\.gif|\.ico|\.mp4|\.ttf|\.eot|\.webp",
    re.IGNORECASE,
)

# Fill by input TYPE, not by field name. Numeric fields get numbers, selects get
# a middle option, so nothing is skipped for being worded unexpectedly.
FILL = """
(amount, term) => {
  const setV = (el, v) => {
    const p = el.tagName === 'SELECT' ? HTMLSelectElement : HTMLInputElement;
    Object.getOwnPropertyDescriptor(p.prototype, 'value').set.call(el, v);
    for (const t of ['input','change','keyup','blur']) el.dispatchEvent(new Event(t,{bubbles:true}));
  };
  const out = [];
  for (const el of document.querySelectorAll('input, select')) {
    if (['hidden','submit','checkbox','image','button','file','password','radio'].includes(el.type)) continue;
    const tag = (el.id + ' ' + el.name + ' ' + (el.placeholder||'')).toLowerCase();
    if (/arama|search|query|iban|e-?mail|telefon|tckn/.test(tag)) continue;   // never useful
    if (el.tagName === 'SELECT') {
      const opts = [...el.options].filter(o => o.value);
      if (!opts.length) continue;
      const pick = opts[Math.min(2, opts.length - 1)];      // avoid the empty first option
      setV(el, pick.value);
      out.push((el.id||el.name||'select') + '=' + pick.value);
      continue;
    }
    // Small numbers look like a term, everything else like an amount.
    const looksTerm = /vade|maturity|taksit|installment|ay\\b|month|gun|day|sure/.test(tag);
    const v = looksTerm ? term : amount;
    setV(el, v);
    out.push((el.id||el.name||el.type) + '=' + v);
  }
  return JSON.stringify(out.slice(0, 20));
}
"""

# Every control that does not navigate away. No text matching at all.
CLICK_N = """
(idx) => {
  const all = [...document.querySelectorAll('button,[role=button],input[type=submit],a')]
    .filter(e => {
      if (e.offsetParent === null) return false;
      if (e.tagName === 'A') {
        const h = e.getAttribute('href') || '';
        if (h && h !== '#' && !h.startsWith('javascript')) return false;  // would navigate
      }
      return true;
    });
  const hit = all[idx];
  if (!hit) return 'END';
  const label = (hit.innerText || hit.value || hit.tagName).trim().replace(/\\s+/g,' ').slice(0,34);
  try { hit.click(); } catch (e) { return 'ERR ' + label; }
  return label;
}
"""


def text(r):
    if isinstance(r, list):
        return "\n".join(str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in r)
    return str(r)


def strip(raw):
    m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", raw, re.S)
    return (m.group(1) if m else raw).strip().strip('"')


async def main(bank, out_path, urls):
    found = []
    async with MultiServerMCPClient(CFG).session("playwright") as session:
        t = {x.name: x for x in await load_mcp_tools(session)}

        async def call(n, **k):
            return text(await t[n].ainvoke(k))

        for url in urls:
            print(f"\n=== {url}", flush=True)
            try:
                await call("browser_navigate", url=url)
                await call("browser_wait_for", time=5)
                origin = "//" + re.sub(r"^https?://", "", url).split("/")[0]

                print("  filled:", strip(await call(
                    "browser_evaluate", function=f"() => (({FILL})('250000','24'))"))[:180], flush=True)

                before = set((await call("browser_network_requests", static=True)).splitlines())

                for idx in range(20):
                    r = strip(await call("browser_evaluate", function=f"() => (({CLICK_N})({idx}))"))
                    if r == "END":
                        break
                    await call("browser_wait_for", time=1)

                await call("browser_wait_for", time=4)
                after = await call("browser_network_requests", static=True)
                new = [l for l in after.splitlines()
                       if l not in before and l.strip() and not DROP.search(l)]

                nums = [int(m) for m in re.findall(r"^\s*(\d+)\.\s*\[(?:POST|GET)\]",
                                                   "\n".join(new), re.M)]
                for i in nums[:14]:
                    line = next((l for l in new if re.match(rf"\s*{i}\.", l)), "")
                    m = re.search(r"\[(\w+)\]\s+(\S+)", line)
                    if not m or origin not in m.group(2):
                        continue                      # keep same-origin only
                    resp = strip(await call("browser_network_request", index=i, part="response-body"))
                    if len(resp) < 30 or resp.lstrip().startswith("<!DOCTYPE"):
                        continue                      # page HTML, not an API reply
                    entry = {
                        "page": url,
                        "method": m.group(1),
                        "endpoint": m.group(2),
                        "request_body": strip(await call("browser_network_request", index=i, part="request-body"))[:600],
                        "response": resp[:600],
                    }
                    found.append(entry)
                    print(f"  >> {entry['method']} {entry['endpoint'][:115]}", flush=True)
                    print(f"     {resp[:160]}", flush=True)
            except Exception as exc:
                print(f"  ! {type(exc).__name__}: {str(exc)[:110]}", flush=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"bank": bank, "endpoints": found}, fh, ensure_ascii=False, indent=2)
    print(f"\n== {bank}: {len(found)} call(s) -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
