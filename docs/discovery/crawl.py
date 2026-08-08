"""Thorough per-bank crawl: every link, then visit the promising ones.

Used to be certain a bank really has no calculator, rather than concluding it
from a homepage glance.
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

# Anything a live calculator might hide behind.
WORTH = re.compile(
    r"hesapla|hesaplama|simulas|finansman|kredi|kar-pay|kâr|katilma|yatirim|"
    r"taksit|odeme|leasing|araclar|urun",
    re.IGNORECASE,
)

ALL_LINKS = """
() => JSON.stringify([...document.querySelectorAll('a')]
  .map(a => a.href)
  .filter(h => h && h.startsWith('http'))
  .filter((v, i, arr) => arr.indexOf(v) === i)
  .slice(0, 400))
"""

# A page "has a calculator" if it holds numeric-ish inputs plus a trigger.
HAS_CALC = """
() => {
  const fields = [...document.querySelectorAll('input, select')]
    .filter(e => !['hidden','submit','search','checkbox','image','button'].includes(e.type))
    .map(e => ({ id: e.id, name: e.name, type: e.type,
                 lbl: (e.labels && e.labels[0] ? e.labels[0].innerText : '').trim().slice(0,30) }));
  const triggers = [...document.querySelectorAll('button,[role=button],a.btn,input[type=submit]')]
    .map(e => (e.innerText || e.value || '').trim().replace(/\\s+/g,' ').slice(0,35))
    .filter(x => /hesapla|ödeme plan|odeme plan|göster|hesap makine/i.test(x));
  const money = fields.filter(f =>
    /tutar|amount|anapara|ana-para|money|bedel|finansman|kredi|vade|taksit|maturity|gun|day/i
      .test(f.id + ' ' + f.name + ' ' + f.lbl));
  return JSON.stringify({ url: location.href, title: document.title.slice(0,60),
                          nFields: fields.length, money, triggers });
}
"""


def text(result) -> str:
    if isinstance(result, list):
        return "\n".join(
            str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in result
        )
    return str(result)


def unwrap(raw: str):
    m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", raw, re.S)
    s = (m.group(1) if m else raw).strip()
    try:
        return json.loads(json.loads(s)) if s.startswith('"') else json.loads(s)
    except Exception:
        return None


async def main(root: str, limit: int = 14) -> None:
    async with MultiServerMCPClient(CFG).session("playwright") as session:
        t = {x.name: x for x in await load_mcp_tools(session)}

        async def call(name, **kw):
            return text(await t[name].ainvoke(kw))

        await call("browser_navigate", url=root)
        await call("browser_wait_for", time=5)

        links = unwrap(await call("browser_evaluate", function=ALL_LINKS)) or []
        host = re.sub(r"^https?://", "", root).split("/")[0].replace("www.", "")
        internal = [u for u in links if host in u]
        candidates = [u for u in internal if WORTH.search(u)]

        print(f"=== {root}")
        print(f"  {len(links)} links, {len(internal)} internal, {len(candidates)} worth visiting")

        # Homepage itself may hold the calculator (Ziraat does).
        pages = [root] + candidates[:limit]
        found = []

        for url in pages:
            try:
                if url != root:
                    await call("browser_navigate", url=url)
                    await call("browser_wait_for", time=3)
                info = unwrap(await call("browser_evaluate", function=HAS_CALC))
                if not info:
                    continue
                if info["money"] and info["triggers"]:
                    found.append(info)
                    print(f"  ** CALCULATOR: {info['url']}")
                    print(f"     title:    {info['title']}")
                    print(f"     fields:   {[m['id'] or m['name'] for m in info['money']][:8]}")
                    print(f"     triggers: {info['triggers'][:4]}")
                elif info["money"]:
                    print(f"  ?  money fields, no trigger: {url[:95]}")
            except Exception as exc:
                print(f"  !  {type(exc).__name__} on {url[:80]}")

        print(f"\n  RESULT: {len(found)} calculator page(s) on {host}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 14))
