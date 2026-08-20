"""Adapters that make private bank agents callable by the main agent."""

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .registry import SpecialistSpec
from .specialists import build_specialist, specialist_thread_id
from .runtime import AgentContext


class DelegateInput(BaseModel):
    request: str = Field(
        description="A complete, bank-specific request including all known amounts, terms, products, and currencies."
    )


def _final_text(result: dict) -> str:
    messages = result.get("messages") or []
    if not messages:
        return "The bank specialist returned no result."
    content = messages[-1].content
    return content if isinstance(content, str) else str(content)


def build_specialist_tool(spec: SpecialistSpec) -> BaseTool:
    """Wrap one specialist without exposing its tools or message history."""
    def delegate(request: str, runtime: ToolRuntime[AgentContext]) -> str:
        session_id = runtime.context.get("session_id")
        if not session_id:
            return "The bank specialist cannot run because the chat session is missing."
        try:
            result = build_specialist(spec.bank).invoke(
                {"messages": [("user", request)]},
                config={"configurable": {"thread_id": specialist_thread_id(session_id, spec.bank)}},
                context={"session_id": session_id},
            )
            return _final_text(result)
        except Exception as exc:  # noqa: BLE001 - a single bank must not end the supervisor turn
            return f"{spec.display_name} live specialist failed ({type(exc).__name__})."

    return StructuredTool.from_function(
        func=delegate,
        name=spec.tool_name,
        description=(
            f"Ask only the {spec.display_name} live-data specialist. Use it when "
            "the user needs current information from this specific bank. It cannot "
            "answer for other banks and returns a concise result with retrieval time."
        ),
        args_schema=DelegateInput,
    )


def build_specialist_tools() -> list[BaseTool]:
    from .registry import SPECS
    return [build_specialist_tool(spec) for spec in SPECS]
