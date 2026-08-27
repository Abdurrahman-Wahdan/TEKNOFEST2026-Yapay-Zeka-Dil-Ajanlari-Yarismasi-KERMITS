"""Prompt every real bank specialist and audit its private tool trace.

This is intentionally a script, not a normal CI test: it calls the configured
LLM, embeddings, Qdrant, SearXNG, and ten bank sites. It exits non-zero unless
each specialist used indexed retrieval, web discovery, and the supplied exact
URL, then preserved that URL in the final handoff the supervisor would see.

    python scripts/verify_specialist_research.py
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.shared import specialists
from agents.shared.agent_tools import _final_text


SOURCES = {
    "adil": "https://www.adilkatilim.com.tr/katilim-bankaciligi/urun-ve-hizmetler",
    "albaraka": "https://www.albaraka.com.tr/tr/bireysel/finansmanlar/konut-finansmani",
    "dunya": "https://dunyakatilim.com.tr/dijital-bankacilik/mobil-sube",
    "emlak": "https://www.emlakkatilim.com.tr/tr/bireysel/finansmanlar/konut-finansmani",
    "hayat": "https://hayatfinans.com.tr/krediler",
    "kuveytturk": "https://saglamkart.kuveytturk.com.tr/kampanyalar/vatan-bilgisayar-ile-vade-farksiz-3-taksit-firsati-2598",
    "tom": "https://tombank.com.tr/vadeli-hesap.html",
    "turkiyefinans": "https://www.turkiyefinans.com.tr:443/tr-tr/bireysel/konut-finansmani/Sayfalar/konut-finansmani.aspx",
    "vakif": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
    "ziraat": "https://www.ziraatkatilim.com.tr/konut-finansmani",
}

REQUIRED = {"search_bank", "search_bank_web", "read_bank_source"}


def verify(bank: str, url: str) -> dict:
    # A private in-memory checkpoint makes the audit repeatable and leaves the
    # user's persistent specialist threads untouched. Production still uses the
    # PostgreSQL saver and `<session>:bank:<bank>` thread IDs.
    graph = specialists.build_specialist(bank, web_research_enabled=True)
    request = f"""Bu bir canlı araç entegrasyon testidir.

Şu üç adımı gerçekten araç çağrılarıyla yap; hiçbirini atlama:
1. search_bank ile bu bankanın kendi indeksinde "katılım bankacılığı ürün koşulları" ara.
2. search_bank_web ile bu bankanın güncel sitesinde "ürün koşulları kampanya" ara.
3. Tablodan/chunk'tan geldiği varsayılan şu TAM URL'yi mutlaka read_bank_source ile aç:
{url}

Son yanıtında kullandığın her kaynak tipini, tam URL'yi, web retrieved_at değerini,
bulduğun somut bir bilgiyi ve varsa çelişki/sınırlamayı yaz. Araç kullanmadan cevap verme."""

    result = graph.invoke(
        {"messages": [("user", request)]},
        config={"configurable": {"thread_id": f"research-audit:bank:{bank}"}},
        context={"session_id": "research-audit", "web_search_enabled": True},
    )
    messages = result.get("messages") or []
    calls = [message.name for message in messages if isinstance(message, ToolMessage)]
    final_text = _final_text(result)
    missing = sorted(REQUIRED - set(calls))
    folded_final = final_text.casefold()
    has_retrieved_at = "retrieved_at" in folded_final
    has_live_web_source = "live_web_" in folded_final
    return {
        "bank": bank,
        "tool_calls": calls,
        "missing_required_calls": missing,
        "exact_url_in_final": url in final_text,
        "retrieved_at_in_final": has_retrieved_at,
        "live_web_source_in_final": has_live_web_source,
        "final_preview": final_text[:700],
        "passed": not missing and url in final_text and has_retrieved_at and has_live_web_source,
    }


def main() -> int:
    # Replace only the persistence backend for this audit. The compiled graph,
    # prompts, tools, Qdrant, web service, and model are the production paths.
    saver = InMemorySaver()
    specialists.get_checkpointer = lambda: saver

    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(verify, bank, url): bank for bank, url in SOURCES.items()}
        for future in as_completed(pending):
            bank = pending[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 - report every bank independently
                row = {
                    "bank": bank,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    rows.sort(key=lambda row: row["bank"])
    summary = {
        "passed": sum(bool(row.get("passed")) for row in rows),
        "total": len(rows),
        "failed_banks": [row["bank"] for row in rows if not row.get("passed")],
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
