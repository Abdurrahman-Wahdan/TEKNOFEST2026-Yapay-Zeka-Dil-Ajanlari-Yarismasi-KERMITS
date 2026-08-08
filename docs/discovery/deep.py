"""Deeper universal calculator probe.

Improvements over probe_calc.py, each from a failure seen on a real bank:
  - finds inputs via the DOM, not the accessibility tree (Vakif's inputs have
    ids but no labels, so the snapshot reported none)
  - fills with native setter + input/change events, so React/Vue/jQuery
    listeners actually fire
  - tries several triggers: Enter, blur, then any button whose text looks like
    a calculate/payment-plan action
  - diffs the network log and reads bodies of new same-origin XHRs

Usage: python deep.py <url> [amount] [term]
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
    r"google|gstatic|doubleclick|facebook|dataroid|clevertap|weaccess|hotjar|"
    r"analytics|gtm|recaptcha|fonts|cloudflare|adobedtm|onetrust|matomo|adrum|"
    r"insider|useinsider|yandex|criteo|\.png|\.jpg|\.svg|\.woff|\.css$",
    re.IGNORECASE,
)

TRIGGER = re.compile(r"hesapla|ödeme\s*plan|odeme\s*plan|göster|goster|devam", re.I)
MONEYISH = re.compile(r"tutar|amount|kredi|finans|miktar", re.I)
TERMISH = re.compile(r"vade|maturity|taksit|ay\b|term|sure|süre", re.I)


def text(result) -> str:
    if isinstance(result, list):
        return "\n".join(
            str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in result
        )
    return str(result)


# Sets a value the way a real user would, so framework listeners fire.
SET_VALUE = """
(sel, val) => {
  const el = document.querySelector(sel);
  if (!el) return 'missing';
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
  setter.call(el, val);
  for (const t of ['input', 'change', 'keyup', 'blur']) {
    el.dispatchEvent(new Event(t, { bubbles: true }));
  }
  return el.value;
}
"""

# Selects need the option picked, not the value assigned. Falls back to the
# closest numeric option, since term dropdowns rarely hold the exact value.
SET_SELECT = """
(sel, val) => {
  const el = document.querySelector(sel);
  if (!el) return 'missing';
  const opts = [...el.options];
  let hit = opts.find(o => o.value === String(val) || o.text.trim() === String(val));
  if (!hit) {
    const want = parseFloat(val);
    const nums = opts.map(o => ({ o, n: parseFloat((o.value || o.text).replace(/[^0-9.]/g, '')) }))
                     .filter(x => !isNaN(x.n));
    if (nums.length) hit = nums.sort((a, b) => Math.abs(a.n - want) - Math.abs(b.n - want))[0].o;
  }
  if (!hit) return 'no-option';
  el.value = hit.value;
  for (const t of ['input', 'change']) el.dispatchEvent(new Event(t, { bubbles: true }));
  return hit.value + ' (' + hit.text.trim().slice(0, 20) + ')';
}
"""

LIST_INPUTS = """
() => JSON.stringify([...document.querySelectorAll('input, select')]
  .filter(e => !['hidden','submit','search','checkbox'].includes(e.type))
  .slice(0, 25)
  .map(e => ({ tag: e.tagName, name: e.name, id: e.id, type: e.type,
               ph: e.placeholder, val: e.value,
               lbl: (e.labels && e.labels[0] ? e.labels[0].innerText : '').slice(0, 40) })))
"""

LIST_BUTTONS = """
() => JSON.stringify([...document.querySelectorAll('button, a.btn, input[type=submit], [role=button]')]
  .map(e => (e.innerText || e.value || '').trim()).filter(Boolean).slice(0, 40))
"""


async def main(url: str, amount: str, term: str) -> None:
    async with MultiServerMCPClient(CFG).session("playwright") as session:
        t = {x.name: x for x in await load_mcp_tools(session)}

        async def call(name, **kw):
            return text(await t[name].ainvoke(kw))

        async def js(fn):
            out = await call("browser_evaluate", function=fn)
            m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", out, re.S)
            raw = (m.group(1) if m else out).strip()
            try:
                return json.loads(json.loads(raw)) if raw.startswith('"') else json.loads(raw)
            except Exception:
                return raw

        print(f"=== {url}")
        await call("browser_navigate", url=url)
        await call("browser_wait_for", time=4)

        fields = await js(LIST_INPUTS)
        print(f"\nFIELDS ({len(fields) if isinstance(fields, list) else 0}):")
        for f in fields if isinstance(fields, list) else []:
            print(f"  {f}")

        buttons = await js(LIST_BUTTONS)
        print(f"\nBUTTONS: {buttons}")

        if not isinstance(fields, list) or not fields:
            print("\nNo visible inputs. Calculator may be behind a tab/modal or in an iframe.")
            return

        def pick(pattern):
            for f in fields:
                blob = f"{f.get('name','')} {f.get('id','')} {f.get('ph','')} {f.get('lbl','')}"
                if pattern.search(blob):
                    return f
            return None

        amt_f = pick(MONEYISH) or fields[0]
        term_f = pick(TERMISH) or (fields[1] if len(fields) > 1 else None)

        def sel(f):
            return f"#{f['id']}" if f.get("id") else f"[name='{f['name']}']"

        before = set((await call("browser_network_requests", static=True)).splitlines())

        async def fill(field, value):
            selector = sel(field)
            fn = SET_SELECT if field["tag"] == "SELECT" else SET_VALUE
            result = await js(f"() => (({fn})({selector!r}, {value!r}))")
            print(f"  {selector} = {value} -> {result}")

        print("\nfilling:")
        await fill(amt_f, amount)
        if term_f is not None:
            await fill(term_f, term)

        await call("browser_wait_for", time=2)

        # Click anything that looks like a calculate / payment-plan action.
        snap = await call("browser_snapshot")
        for label, ref in re.findall(r'button "([^"]*)"[^\[]*\[ref=([^\]]+)\]', snap):
            if TRIGGER.search(label):
                print(f"clicking button {label!r}")
                await call("browser_click", target=ref)
                await call("browser_wait_for", time=3)

        await call("browser_wait_for", time=2)
        after = await call("browser_network_requests", static=True)
        new = [ln for ln in after.splitlines()
               if ln not in before and ln.strip() and not NOISE.search(ln)]

        print(f"\n=== {len(new)} new request(s)")
        for line in new:
            print("  " + line[:185])

        nums = [int(m) for m in re.findall(r"^\s*(\d+)\.\s*\[(?:POST|GET)\]",
                                           "\n".join(new), re.M)]
        for i in nums[:6]:
            body = await call("browser_network_request", index=i, part="response-body")
            if re.search(r"taksit|installment|amount|tutar|oran|rate|plan", body, re.I):
                print(f"\n--- #{i} RESPONSE (looks like a calculation) ---")
                print(body[:900])
                print(f"--- #{i} REQUEST BODY ---")
                print((await call("browser_network_request", index=i, part="request-body"))[:500])


if __name__ == "__main__":
    asyncio.run(main(
        sys.argv[1],
        sys.argv[2] if len(sys.argv) > 2 else "100000",
        sys.argv[3] if len(sys.argv) > 3 else "24",
    ))
