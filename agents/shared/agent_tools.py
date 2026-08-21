"""Adapters that make private bank agents callable by the main agent."""

import logging

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from banks.factory import get_bank

from .registry import SpecialistSpec
from .specialists import build_specialist, specialist_thread_id
from .runtime import AgentContext

logger = logging.getLogger(__name__)


class DelegateInput(BaseModel):
    request: str = Field(
        description="A complete, bank-specific request including all known amounts, terms, products, and currencies."
    )
    monthly_profit_rate: float | None = Field(
        default=None,
        gt=0,
        le=100,
        description=(
            "Customer-supplied monthly profit rate for a financing scenario, if the user "
            "explicitly requested one. Do not use a bank's normal live rate instead."
        ),
    )


def _final_text(result: dict) -> str:
    messages = result.get("messages") or []
    if not messages:
        return "The bank specialist returned no result."
    content = messages[-1].content
    return content if isinstance(content, str) else str(content)


def build_specialist_tool(spec: SpecialistSpec) -> BaseTool:
    """Wrap one specialist without exposing its tools or message history."""
    bank = get_bank(spec.bank)

    def delegate(
        request: str,
        runtime: ToolRuntime[AgentContext],
        monthly_profit_rate: float | None = None,
    ) -> str:
        session_id = runtime.context.get("session_id")
        if not session_id:
            return "The bank specialist cannot run because the chat session is missing."
        if (
            monthly_profit_rate is not None
            and "monthly_profit_rate" not in bank.finance_input_capabilities
        ):
            return (
                f"{spec.display_name} unavailable: its live financing calculator does not "
                "accept a customer-supplied monthly profit rate. Do not substitute the "
                "bank's standard live rate for this scenario."
            )
        try:
            # This is intentionally blocking. LangChain's tool node waits for
            # this callable to return, while the specialist's tunnel-aware model
            # refreshes and retries internally. The main agent therefore sees
            # either the specialist's final response or a terminal failure after
            # the configured retry window, never an in-progress placeholder.
            # The parent's callbacks are carried across, and this is the only
            # reason anything can see what a specialist spends. A config built
            # from scratch here *replaces* the caller's rather than extending
            # it, so the handlers attached to the supervisor's run never reach
            # the specialist's model calls. Measured: with a fresh config the
            # inner call was invisible to the parent's usage handler; carrying
            # the callbacks through, it was counted. Ten specialists were
            # spending tokens that nothing could observe.
            #
            # `thread_id` is still replaced on purpose: the specialist's memory
            # is private and must not land on the supervisor's thread.
            parent = runtime.config or {}
            result = build_specialist(spec.bank, monthly_profit_rate).invoke(
                {"messages": [("user", request)]},
                config={
                    **parent,
                    "configurable": {
                        **(parent.get("configurable") or {}),
                        "thread_id": specialist_thread_id(session_id, spec.bank),
                    },
                },
                context={"session_id": session_id},
            )
            return _final_text(result)
        except Exception as exc:  # noqa: BLE001 - a single bank must not end the supervisor turn
            logger.exception(
                "%s specialist exhausted its model retry window", spec.display_name
            )
            return f"{spec.display_name} live specialist failed ({type(exc).__name__})."

    return StructuredTool.from_function(
        func=delegate,
        name=spec.tool_name,
        description=(
            f"Ask only the {spec.display_name} live-data specialist. Use it when "
            "the user needs current information from this specific bank. It cannot "
            "answer for other banks and returns a concise result with retrieval time. "
            + (
                "Its financing calculator accepts a customer-supplied monthly profit-rate scenario."
                if "monthly_profit_rate" in bank.finance_input_capabilities
                else "Its financing calculator does not accept a customer-supplied monthly profit rate."
            )
        ),
        args_schema=DelegateInput,
    )


def build_specialist_tools() -> list[BaseTool]:
    from .registry import SPECS
    return [build_specialist_tool(spec) for spec in SPECS]
