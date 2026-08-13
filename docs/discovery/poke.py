"""Navigate to a page and run one-line JS expressions against the real DOM.

The accessibility tree omits controls that work (Vakif's labelled-less inputs,
Kuveyt Turk's span triggers), so every "is there a calculator here" question has
to be answered against the DOM. This is the tool for asking.

Usage:
    python poke.py <url> '<js expr>' ['<js expr>' ...]

Each expression is evaluated in order in the same page, so state carries over:
a fill expression followed by a click followed by a network dump.
Prefix an argument with 'net:' to dump network requests instead, or 'wait:N'
to pause N seconds.
"""

import asyncio
import re
import sys
import warnings

warnings.filterwarnings("ignore")

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from probe import CFG, text, strip, unwrap


async def main(url, exprs):
    async with MultiServerMCPClient(CFG).session("playwright") as session:
        tools = {x.name: x for x in await load_mcp_tools(session)}

        async def call(n, **k):
            return text(await tools[n].ainvoke(k))

        await call("browser_navigate", url=url)
        await call("browser_wait_for", time=5)

        for expr in exprs:
            if expr.startswith("wait:"):
                await call("browser_wait_for", time=float(expr[5:]))
                continue
            if expr.startswith("net:"):
                pattern = expr[4:]
                out = await call("browser_network_requests", static=False)
                for line in out.splitlines():
                    if not pattern or re.search(pattern, line, re.I):
                        print(line)
                continue
            if expr.startswith("dump:"):
                pattern = expr[5:]
                out = await call("browser_network_requests", static=False)
                for line in out.splitlines():
                    m = re.match(r"\s*(\d+)\.\s*\[(\w+)\]\s+(\S+)", line)
                    if not m or not re.search(pattern, line, re.I):
                        continue
                    print(f"\n##### {m.group(2)} {m.group(3)}")
                    for part in ("request-headers", "request-body", "response-body"):
                        val = strip(await call("browser_network_request",
                                               index=int(m.group(1)), part=part))
                        print(f"--- {part}\n{val[:2000]}")
                continue
            if expr.startswith("body:"):
                idx = int(expr[5:])
                for part in ("request-headers", "request-body", "response-body"):
                    print(f"--- {part}")
                    print(strip(await call("browser_network_request", index=idx, part=part))[:2500])
                continue
            print(f"--- {expr[:70]}")
            print(str(unwrap(await call("browser_evaluate", function=expr)))[:4000])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
