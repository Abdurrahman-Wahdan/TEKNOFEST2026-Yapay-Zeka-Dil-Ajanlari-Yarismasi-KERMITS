"""Live-model audit for bank coverage versus mandatory web research.

The real supervisor reads the production prompt and production specialist tool
schemas. Specialists are replaced with deterministic live-endpoint handoffs so
the audit isolates the supervisor's routing decision without making ten bank
network calls. It fails if the exact Turkish all-bank request is treated as a
mandatory-web request while the Web Search toggle is off.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.main import agent as main_agent
from agents.shared import agent_tools
from agents.shared.registry import SPECS


REQUEST = "Güncel kâr oranlarını bana her banka için bulabilir misin?"


class _LiveEndpointSpecialist:
    def __init__(self, bank: str):
        self.bank = bank

    def invoke(self, payload, config, context):
        return {"messages": [
            ToolMessage(
                name="profit_share_quote",
                tool_call_id=f"quote-{self.bank}",
                content=json.dumps({
                    "bank": self.bank,
                    "source_type": "live_endpoint",
                    "retrieved_at": "2026-08-25T12:00:00+03:00",
                    "status": "unavailable",
                    "message": (
                        "The current profit rate depends on product, amount, and term; "
                        "ask the user for the missing quote inputs."
                    ),
                }),
            ),
            AIMessage(content=(
                f"{self.bank}: current quote inputs are incomplete. The live endpoint "
                "requires a product, amount, and term; ask for those inputs."
            )),
        ]}


def main() -> int:
    saver = InMemorySaver()
    built: list[dict] = []

    main_agent.get_checkpointer = lambda: saver

    def fake_build(bank, monthly_profit_rate=None, **kwargs):
        built.append({
            "bank": bank,
            "monthly_profit_rate": monthly_profit_rate,
            **kwargs,
        })
        return _LiveEndpointSpecialist(bank)

    agent_tools.build_specialist = fake_build
    graph = main_agent.build_main_agent()
    result = graph.invoke(
        {"messages": [("user", REQUEST)]},
        config={"configurable": {"thread_id": "source-priority-routing:main"}},
        context={"session_id": "source-priority-routing", "web_search_enabled": False},
    )
    messages = result.get("messages") or []
    calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
        if call.get("name", "").startswith("ask_")
    ]
    final = messages[-1].content if messages else ""
    final_text = final if isinstance(final, str) else str(final)
    expected = {f"ask_{spec.bank}" for spec in SPECS}
    called = {call["name"] for call in calls}

    checks = {
        "delegated_every_bank": called == expected,
        "no_call_requires_web": all(
            call.get("args", {}).get("web_research_required", False) is False
            for call in calls
        ),
        "specialists_built_without_web": all(
            "web_research_enabled" not in invocation
            and "web_research_required" not in invocation
            for invocation in built
        ),
        "no_web_toggle_refusal": (
            "enable web search" not in final_text.casefold()
            and "web search is disabled" not in final_text.casefold()
        ),
    }
    report = {
        "request": REQUEST,
        "called": sorted(called),
        "call_args": {call["name"]: call.get("args", {}) for call in calls},
        "checks": checks,
        "final_preview": final_text[:1200],
        "passed": all(checks.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
