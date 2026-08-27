"""Live attached-table routing audit through the real supervisor graph.

The table contains exactly three banks. The audit passes only if the supervisor
delegates exactly those three, each specialist opens its own row URL, the
machine-preserved nested evidence reaches the supervisor, and the final
synthesis cites every source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.main import agent as main_agent
from agents.shared import specialists


ROWS = {
    "kuveytturk": "https://saglamkart.kuveytturk.com.tr/kampanyalar/vatan-bilgisayar-ile-vade-farksiz-3-taksit-firsati-2598",
    "albaraka": "https://www.albaraka.com.tr/tr/bireysel/finansmanlar/konut-finansmani",
    "vakif": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
}


def main() -> int:
    saver = InMemorySaver()
    main_agent.get_checkpointer = lambda: saver
    specialists.get_checkpointer = lambda: saver

    table = "\n".join([
        "| Banka | Tablodaki iddia | Kaynak URL |",
        "|---|---|---|",
        f"| Kuveyt Türk | Kampanya koşullarını doğrula | {ROWS['kuveytturk']} |",
        f"| Albaraka Türk | Konut finansmanı koşullarını doğrula | {ROWS['albaraka']} |",
        f"| Vakıf Katılım | Müşteri olma bilgisini doğrula | {ROWS['vakif']} |",
    ])
    request = f"""Aşağıdaki ekli tabloyu güncel birincil kaynaklarla doğrula.
Web araştırması açıktır. Tabloda yalnızca üç banka var; sadece bu üç bankanın
uzmanına delege et. Her uzmana yalnızca kendi satırını ve tam URL'sini gönder,
URL'yi read_bank_source ile açmasını iste. İndeks ile güncel sayfa arasında
çelişki varsa yaz. Son yanıtta üç tam URL'yi de koru.

{table}"""

    graph = main_agent.build_main_agent()
    result = graph.invoke(
        {"messages": [("user", request)]},
        config={"configurable": {"thread_id": "supervisor-research-audit:main"}},
        context={"session_id": "supervisor-research-audit", "web_search_enabled": True},
    )
    messages = result.get("messages") or []
    delegated = {
        message.name: message.content
        for message in messages
        if isinstance(message, ToolMessage) and (message.name or "").startswith("ask_")
    }
    expected = {f"ask_{bank}" for bank in ROWS}
    final = messages[-1].content if messages else ""
    final_text = final if isinstance(final, str) else str(final)

    checks = {
        "delegated_exactly_table_banks": set(delegated) == expected,
        "each_handoff_has_machine_evidence": all(
            "TF26_TOOL_EVIDENCE" in content for content in delegated.values()
        ),
        "each_handoff_used_exact_url_reader": all(
            "read_bank_source" in delegated.get(f"ask_{bank}", "")
            and url in delegated.get(f"ask_{bank}", "")
            for bank, url in ROWS.items()
        ),
        "final_preserves_all_urls": all(url in final_text for url in ROWS.values()),
    }
    report = {
        "delegated_tools": sorted(delegated),
        "expected_tools": sorted(expected),
        "checks": checks,
        "final_preview": final_text[:1200],
        "passed": all(checks.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
