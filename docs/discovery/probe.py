"""Capture every backend call a bank's calculator pages make, plus the form
inventory that feeds them.

Differences from capture.py, all of which cost time to learn:

- Requests are kept by *brand domain*, not by exact origin. T.O.M.'s calculator
  lives on webintegration.tombank.com.tr while the page is www.tombank.com.tr;
  an exact-origin filter drops it.
- Request headers are recorded. Several banks need x-requested-with or a CSRF
  token, and a replay without them 403s.
- Select options are dumped in full. Product codes (which product a calculator
  is pricing) live there and nowhere else.
- Nothing is dropped for looking uninteresting. Third-party calls that are not
  known trackers are kept in a separate list rather than discarded.

Usage: python probe.py <bank> <out.json> <url> [url ...]
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

# Third-party trackers and static files only. Nothing domain-specific, and no
# Turkish banking vocabulary -- filtering on that loses whole banks.
DROP = re.compile(
    r"google|gstatic|doubleclick|facebook|dataroid|clevertap|weaccess|hotjar|clarity|"
    r"analytics|googletagmanager|recaptcha|fonts\.|cloudflare|adobedtm|onetrust|matomo|"
    r"adrum|yandex|insider|criteo|mookie|efilli|addevent|jsdelivr|akstat|2o7\.net|"
    r"\.png|\.jpg|\.jpeg|\.css|\.woff2?|\.svg|\.gif|\.ico|\.mp4|\.ttf|\.eot|\.webp|\.js(\?|$)",
    re.IGNORECASE,
)

GENERIC = {"www", "com", "net", "org", "tr", "gov", "co"}

# browser_evaluate rejects multi-line function bodies, so every snippet below is
# one line by construction.
INVENTORY = (
    "() => { const q = (s) => [...document.querySelectorAll(s)]; "
    "const inputs = q('input,textarea').map(e => ({tag: e.tagName, type: e.type, id: e.id, "
    "name: e.name, ph: e.placeholder || '', val: (e.value || '').slice(0, 40)})); "
    "const selects = q('select').map(s => ({id: s.id, name: s.name, "
    "options: [...s.options].slice(0, 80).map(o => ({v: o.value, t: (o.text || '').trim().slice(0, 60)}))})); "
    "const buttons = q('button,[role=button],input[type=submit]').filter(e => e.offsetParent !== null)"
    ".slice(0, 40).map(e => ((e.innerText || e.value || e.tagName) + '').trim().replace(/\\s+/g, ' ').slice(0, 40)); "
    "return JSON.stringify({inputs, selects, buttons}); }"
)

FILL = (
    # The prototype must match the element or the value setter throws
    # "Illegal invocation" and the whole fill aborts -- which then looks like a
    # page with no fillable form.
    "(amount, term) => { const setV = (el, v) => { const p = el.tagName === 'SELECT' ? "
    "HTMLSelectElement : (el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement); "
    "Object.getOwnPropertyDescriptor(p.prototype, 'value').set.call(el, v); "
    "for (const t of ['input', 'change', 'keyup', 'blur']) el.dispatchEvent(new Event(t, {bubbles: true})); }; "
    "const out = []; for (const el of document.querySelectorAll('input, select, textarea')) { "
    "if (['hidden', 'submit', 'checkbox', 'image', 'button', 'file', 'password', 'radio'].includes(el.type)) continue; "
    "const tag = (el.id + ' ' + el.name + ' ' + (el.placeholder || '')).toLowerCase(); "
    "if (/arama|search|query|iban|e-?mail|telefon|tckn/.test(tag)) continue; "
    "if (el.tagName === 'SELECT') { const opts = [...el.options].filter(o => o.value); "
    "if (!opts.length) continue; const pick = opts[Math.min(2, opts.length - 1)]; setV(el, pick.value); "
    "out.push((el.id || el.name || 'select') + '=' + pick.value); continue; } "
    "const looksTerm = /vade|maturity|taksit|installment|month|gun|day|sure/.test(tag); "
    "setV(el, looksTerm ? term : amount); out.push((el.id || el.name || el.type) + '=' + (looksTerm ? term : amount)); } "
    "return JSON.stringify(out.slice(0, 25)); }"
)

# Every visible control that will not navigate away. No text matching: label
# text is unreliable and differs per bank.
CLICK_N = (
    "(idx) => { const all = [...document.querySelectorAll('button,[role=button],input[type=submit],a')]"
    ".filter(e => { if (e.offsetParent === null) return false; if (e.tagName === 'A') { "
    "const h = e.getAttribute('href') || ''; if (h && h !== '#' && !h.startsWith('javascript')) return false; } "
    "return true; }); const hit = all[idx]; if (!hit) return 'END'; "
    "const label = ((hit.innerText || hit.value || hit.tagName) + '').trim().replace(/\\s+/g, ' ').slice(0, 34); "
    "try { hit.click(); } catch (e) { return 'ERR ' + label; } return label; }"
)


def brand(host):
    """kuveytturk from www.kuveytturk.com.tr, tombank from webintegration.tombank.com.tr."""
    parts = [p for p in host.lower().split(".") if p not in GENERIC]
    return max(parts, key=len) if parts else host.lower()


def text(r):
    if isinstance(r, list):
        return "\n".join(str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in r)
    return str(r)


def strip(raw):
    m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", raw, re.S)
    return (m.group(1) if m else raw).strip().strip('"')


def unwrap(raw):
    """browser_evaluate returns its value JSON-encoded, so a string result comes
    back escaped inside quotes. Decode until it stops being a string."""
    body = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", raw, re.S)
    val = (body.group(1) if body else raw).strip()
    for _ in range(3):
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            break
        if not isinstance(val, str):
            break
    return val


async def probe_page(call, url, tag):
    """Return (inventory, own_calls, foreign_calls) for one page."""
    await call("browser_navigate", url=url)
    await call("browser_wait_for", time=5)

    inventory = unwrap(await call("browser_evaluate", function=INVENTORY))
    if not isinstance(inventory, dict):
        print(f"  ! inventory unreadable: {str(inventory)[:120]}", flush=True)
        inventory = {}

    n_in = len(inventory.get("inputs", []))
    n_sel = len(inventory.get("selects", []))
    print(f"  form: {n_in} input(s), {n_sel} select(s), "
          f"{len(inventory.get('buttons', []))} button(s)", flush=True)

    # Baseline before filling. Several calculators recalculate on change with no
    # button at all (Kuveyt Turk's kar payi page has no Hesapla control), so a
    # baseline taken after the fill diffs away the very call we came for.
    before = set((await call("browser_network_requests", static=False)).splitlines())

    filled = unwrap(await call("browser_evaluate", function=f"() => (({FILL})('100000','24'))"))
    print(f"  filled: {str(filled)[:150]}", flush=True)
    await call("browser_wait_for", time=3)

    clicked = []

    for idx in range(25):
        r = strip(await call("browser_evaluate", function=f"() => (({CLICK_N})({idx}))"))
        if r == "END":
            break
        clicked.append(r)
        await call("browser_wait_for", time=1)
    await call("browser_wait_for", time=4)

    after = await call("browser_network_requests", static=False)
    new_lines = [l for l in after.splitlines() if l not in before and l.strip()]
    fresh = [l for l in new_lines if not DROP.search(l)]
    print(f"  clicked {len(clicked)}: {' | '.join(clicked)[:220]}", flush=True)
    print(f"  network: {len(new_lines)} new, {len(fresh)} after drop", flush=True)

    own, foreign = [], []
    for line in fresh[:30]:
        m = re.match(r"\s*(\d+)\.\s*\[(\w+)\]\s+(\S+)", line)
        if not m:
            continue
        i, method, endpoint = int(m.group(1)), m.group(2), m.group(3)
        host = re.sub(r"^https?://", "", endpoint).split("/")[0]
        record = {"page": url, "method": method, "endpoint": endpoint}
        if brand(host) != tag:
            foreign.append(record)
            continue
        record["request_headers"] = strip(await call(
            "browser_network_request", index=i, part="request-headers"))[:1500]
        record["request_body"] = strip(await call(
            "browser_network_request", index=i, part="request-body"))[:1500]
        record["response"] = strip(await call(
            "browser_network_request", index=i, part="response-body"))[:6000]
        own.append(record)
        head = record["response"].replace("\n", " ")[:140]
        print(f"  >> {method} {endpoint[:110]}", flush=True)
        print(f"     {head}", flush=True)

    return {"page": url, "inventory": inventory, "clicked": clicked}, own, foreign


async def main(bank, out_path, urls):
    tag = brand(re.sub(r"^https?://", "", urls[0]).split("/")[0])
    pages, own, foreign = [], [], []

    # One browser session per page. Sharing a session lets the request log grow
    # across navigations until it is truncated, and then the diff that finds new
    # calls silently returns nothing -- which reads exactly like "no calculator".
    for url in urls:
        print(f"\n=== {url}", flush=True)
        try:
            async with MultiServerMCPClient(CFG).session("playwright") as session:
                tools = {x.name: x for x in await load_mcp_tools(session)}

                async def call(n, **k):
                    return text(await tools[n].ainvoke(k))

                p, o, f = await probe_page(call, url, tag)
                pages.append(p)
                own.extend(o)
                foreign.extend(f)
        except Exception as exc:
            print(f"  ! {type(exc).__name__}: {str(exc)[:130]}", flush=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"bank": bank, "brand": tag, "pages": pages,
                   "endpoints": own, "third_party": foreign},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n== {bank}: {len(own)} own call(s), {len(foreign)} third-party -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
