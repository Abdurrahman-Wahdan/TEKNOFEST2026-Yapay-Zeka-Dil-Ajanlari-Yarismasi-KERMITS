"""Live audit: an explicit comprehensive-web request must reach search_bank_web."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.main import agent as main_agent
from agents.shared.agent_tools import cited_sources_from_text
from agents.shared import specialists


EVIDENCE_MARKER = "TF26_TOOL_EVIDENCE (machine-preserved from actual specialist calls):"


def main() -> int:
    saver = InMemorySaver()
    main_agent.get_checkpointer = lambda: saver
    specialists.get_checkpointer = lambda: saver

    graph = main_agent.build_main_agent()
    result = graph.invoke(
        {"messages": [("user", (
            "Kuveyt Türk sukuk ürünleri hakkında internette araştırma yap. "
            "Bulabildiğin en kapsamlı ve en geniş bilgiyi getir; web araması "
            "yapmadan cevap verme."
        ))]},
        config={"configurable": {"thread_id": "required-web-audit:main"}},
        context={
            "session_id": "required-web-audit",
            "web_search_enabled": True,
        },
    )
    messages = result.get("messages") or []
    delegation_args = [
        call.get("args") or {}
        for message in messages
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
        if call.get("name") == "ask_kuveytturk"
    ]
    handoffs = [
        message.content
        for message in messages
        if isinstance(message, ToolMessage) and message.name == "ask_kuveytturk"
    ]
    ledger = []
    if handoffs and EVIDENCE_MARKER in handoffs[0]:
        try:
            ledger = json.loads(handoffs[0].rpartition(EVIDENCE_MARKER)[2].strip())
        except json.JSONDecodeError:
            ledger = []
    used_sources = [
        source
        for row in ledger
        if isinstance(row, dict)
        for source in (row.get("used_sources") or [])
        if isinstance(source, dict)
    ]
    final_text = next(
        (
            message.content
            for message in reversed(messages)
            if isinstance(message, AIMessage)
            and isinstance(message.content, str)
            and not message.tool_calls
        ),
        "",
    )
    final_source_keys = set(cited_sources_from_text(final_text))
    used_source_keys = {
        str(source.get("url") or "").rstrip("/") for source in used_sources
    }
    provenances = {source.get("provenance") for source in used_sources}
    checks = {
        "delegated_to_kuveytturk": bool(delegation_args),
        "supervisor_marked_web_required": bool(delegation_args)
        and delegation_args[0].get("web_research_required") is True,
        "specialist_called_search_bank_web": bool(handoffs)
        and '"tool":"search_bank_web"' in handoffs[0],
        "specialist_returned_used_sources": bool(used_sources),
        "source_provenance_is_machine_classified": bool(provenances)
        and provenances <= {"live_web", "knowledge_base"},
        "supervisor_cited_only_handoff_sources": bool(final_source_keys)
        and final_source_keys <= used_source_keys,
    }
    report = {
        "checks": checks,
        "delegation_args": delegation_args,
        "used_sources": used_sources,
        "final_cited_source_count": len(final_source_keys),
        "handoff_preview": handoffs[0][:1000] if handoffs else "",
        "passed": all(checks.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
