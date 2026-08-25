"""Live end-to-end audit of a bank specialist's citation handoff.

Runs the real Kuveyt Türk specialist with Web Search disabled. Success means
ordinary research used the tools already available and returned at least one
machine-validated, claim-used source for the supervisor/UI.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.shared.agent_tools import (
    _EVIDENCE_MARKER,
    build_specialist_tool,
    used_sources_from_tool_message,
)
from agents.shared.registry import SPECS
from agents.shared.runtime import AgentContext
from api.agent import _agent_answer
from langchain_core.messages import ToolMessage


REQUEST = "Kuveyt Türk'ün sukuk ürünlerini araştır benim için"


class _Runtime:
    context: AgentContext
    config: dict

    def __init__(self) -> None:
        session_id = f"citation-audit-{uuid.uuid4()}"
        self.context = {"session_id": session_id, "web_search_enabled": False}
        self.config = {"configurable": {"thread_id": f"{session_id}:main"}}


def main() -> int:
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    handoff = build_specialist_tool(spec).func(REQUEST, _Runtime())
    public_message = ToolMessage(
        name=spec.tool_name,
        tool_call_id="citation-audit",
        content=handoff,
    )
    sources = used_sources_from_tool_message(public_message)
    checks = {
        "machine_evidence_present": _EVIDENCE_MARKER in handoff,
        "claim_used_sources_present": bool(sources),
        "all_sources_are_clickable": all(
            str(source.get("url") or "").startswith("https://") for source in sources
        ),
        "knowledge_base_source_present": any(
            source.get("provenance") == "knowledge_base" for source in sources
        ),
        "no_web_refusal": "web arama" not in handoff.casefold(),
    }
    supervisor_events = list(_agent_answer(
        REQUEST,
        history=None,
        context=None,
        captures=None,
        tool_results=None,
        session_id=uuid.uuid4(),
        web_search=False,
    ))
    supervisor_answer = "".join(
        event.text or "" for event in supervisor_events if event.type == "token"
    )
    supervisor_citations = [
        event.citation.model_dump()
        for event in supervisor_events
        if event.type == "citation" and event.citation is not None
    ]
    checks.update({
        "supervisor_emitted_citation_event": bool(supervisor_citations),
        "supervisor_kept_knowledge_base_provenance": any(
            citation.get("doc_kind") == "knowledge_base"
            for citation in supervisor_citations
        ),
        "supervisor_did_not_refuse_without_web": "web arama" not in supervisor_answer.casefold(),
    })
    print(json.dumps({
        "request": REQUEST,
        "checks": checks,
        "sources": sources,
        "supervisor_citations": supervisor_citations,
        "supervisor_answer": supervisor_answer,
        "handoff": handoff,
        "passed": all(checks.values()),
    }, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
