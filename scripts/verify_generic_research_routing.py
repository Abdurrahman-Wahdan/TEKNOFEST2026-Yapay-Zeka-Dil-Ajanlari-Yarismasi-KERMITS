"""Live-model audit for generic research with Web Search disabled.

The production supervisor decides the specialist arguments. The specialist is
deterministic so this isolates routing and the adapter guard: ordinary Turkish
``araştır`` must still run indexed retrieval and must never become a refusal.
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


REQUEST = "Kuveyt Türk'ün sukuk ürünlerini araştır benim için"


class _IndexedSpecialist:
    def invoke(self, payload, config, context):
        return {
            "messages": [
                ToolMessage(
                    name="search_bank",
                    tool_call_id="indexed-1",
                    content="Indexed Kuveyt Türk sukuk evidence.",
                ),
                AIMessage(content="Kuveyt Türk sukuk bilgisi indexed kaynakta bulundu."),
            ]
        }


def main() -> int:
    saver = InMemorySaver()
    main_agent.get_checkpointer = lambda: saver
    built: list[dict] = []

    def fake_build(bank, monthly_profit_rate=None, **kwargs):
        built.append({"bank": bank, **kwargs})
        return _IndexedSpecialist()

    agent_tools.build_specialist = fake_build
    graph = main_agent.build_main_agent()
    result = graph.invoke(
        {"messages": [("user", REQUEST)]},
        config={"configurable": {"thread_id": "generic-research-routing:main"}},
        context={
            "session_id": "generic-research-routing",
            "web_search_enabled": False,
        },
    )
    messages = result.get("messages") or []
    calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
        if call.get("name") == "ask_kuveytturk"
    ]
    handoffs = [
        str(message.content)
        for message in messages
        if isinstance(message, ToolMessage) and message.name == "ask_kuveytturk"
    ]
    final = str(messages[-1].content) if messages else ""
    checks = {
        "delegated_to_kuveytturk": len(calls) == 1,
        "used_indexed_retrieval": bool(handoffs) and '"tool":"search_bank"' in handoffs[0],
        "specialist_had_no_web_tools": built == [{"bank": "kuveytturk"}],
        "no_web_refusal": all(
            phrase not in final.casefold()
            for phrase in ("web search is disabled", "web arama'yı etkinleştir", "web arama’yı etkinleştir")
        ),
    }
    print(json.dumps({
        "request": REQUEST,
        "delegation_args": calls[0].get("args", {}) if calls else {},
        "checks": checks,
        "final": final,
        "passed": all(checks.values()),
    }, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
