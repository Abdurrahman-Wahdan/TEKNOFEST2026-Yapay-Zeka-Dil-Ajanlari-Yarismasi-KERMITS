"""Measure SearXNG discovery with read_bank_source deliberately disabled.

This assessment has two layers:

1. deterministic tool calls: Turkish and English queries for every bank, with
   result/snippet/latency/engine diagnostics;
2. real specialist prompts: prove the model sees no reader and calls only
   search_bank_web for the web-research portion of the task.

Run while ``WEB_READ_SOURCE_ENABLED=false``.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.shared import specialists
from agents.shared.bank_tools import build_bank_tools
from agents.shared.registry import SPECS
from config.settings import settings


QUERIES = {
    "tr_campaign": "güncel kampanyalar ürün koşulları",
    "tr_finance": "konut finansmanı vade şartları",
    "en_products": "participation banking products campaigns",
}


def web_tools(bank: str):
    return {
        tool.name: tool
        for tool in build_bank_tools(bank, web_research_enabled=True)
        if tool.name in {"search_bank_web", "read_bank_source"}
    }


def tool_check(bank: str, query_name: str, query: str) -> dict:
    tools = web_tools(bank)
    started = time.perf_counter()
    payload = json.loads(tools["search_bank_web"].invoke({"query": query}))
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    results = payload.get("results") or []
    return {
        "bank": bank,
        "query_name": query_name,
        "status": payload.get("status"),
        "latency_ms": elapsed_ms,
        "result_count": len(results),
        "results_with_snippets": sum(bool(row.get("snippet")) for row in results),
        "top_results": [
            {"title": row.get("title"), "url": row.get("url")}
            for row in results[:3]
        ],
        "unresponsive_engines": payload.get("unresponsive_engines") or [],
        "reader_exposed": "read_bank_source" in tools,
    }


def specialist_check(bank: str) -> dict:
    graph = specialists.build_specialist(bank, web_research_enabled=True)
    result = graph.invoke(
        {"messages": [("user", (
            "Bu bir search-only testidir. Yalnızca search_bank_web aracını "
            "kullanarak bu bankanın güncel kampanya ve ürün koşulu sayfalarını "
            "Türkçe ara. Başka hiçbir araç çağırma. Sonuç URL'lerini, başlıklarını "
            "ve snippet bilgisini ver; sayfaları açtığını veya doğruladığını iddia etme."
        ))]},
        config={"configurable": {"thread_id": f"search-only:bank:{bank}"}},
        context={"session_id": "search-only", "web_search_enabled": True},
    )
    messages = result.get("messages") or []
    calls = [message.name for message in messages if isinstance(message, ToolMessage)]
    final = messages[-1].content if messages else ""
    final_text = final if isinstance(final, str) else str(final)
    return {
        "bank": bank,
        "tool_calls": calls,
        "only_search_bank_web": bool(calls) and set(calls) == {"search_bank_web"},
        "reader_mentioned_as_used": "read_bank_source" in final_text,
        "marks_results_unverified": any(
            marker in final_text.casefold()
            for marker in ("doğrulanmamış", "doğrulanmamıştır", "snippet", "açılmamıştır")
        ),
        "final_preview": final_text[:500],
    }


def main() -> int:
    if settings.WEB_READ_SOURCE_ENABLED:
        print(json.dumps({
            "error": "Set WEB_READ_SOURCE_ENABLED=false before this assessment."
        }))
        return 2

    tool_rows = []
    for spec in SPECS:
        for query_name, query in QUERIES.items():
            row = tool_check(spec.bank, query_name, query)
            tool_rows.append(row)
            print(json.dumps({"tool": row}, ensure_ascii=False), flush=True)

    saver = InMemorySaver()
    specialists.get_checkpointer = lambda: saver
    specialist_rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(specialist_check, spec.bank): spec.bank for spec in SPECS}
        for future in as_completed(futures):
            bank = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 - report banks independently
                row = {"bank": bank, "error": f"{type(exc).__name__}: {exc}"}
            specialist_rows.append(row)
            print(json.dumps({"specialist": row}, ensure_ascii=False), flush=True)

    latencies = [row["latency_ms"] for row in tool_rows]
    summary = {
        "mode": "search_only",
        "reader_exposed_anywhere": any(row["reader_exposed"] for row in tool_rows),
        "queries": len(tool_rows),
        "http_successes": sum(row["status"] in {"ok", "no_results"} for row in tool_rows),
        "queries_with_results": sum(row["result_count"] > 0 for row in tool_rows),
        "queries_with_snippets": sum(row["results_with_snippets"] > 0 for row in tool_rows),
        "median_latency_ms": round(statistics.median(latencies), 1),
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
        "specialists_search_only": sum(
            bool(row.get("only_search_bank_web")) for row in specialist_rows
        ),
        "specialists_total": len(specialist_rows),
        "specialists_marking_unverified": sum(
            bool(row.get("marks_results_unverified")) for row in specialist_rows
        ),
        "banks_without_any_result": sorted({
            row["bank"] for row in tool_rows
            if all(
                candidate["result_count"] == 0
                for candidate in tool_rows
                if candidate["bank"] == row["bank"]
            )
        }),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2), flush=True)
    return 0 if (
        not summary["reader_exposed_anywhere"]
        and summary["specialists_search_only"] == summary["specialists_total"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
