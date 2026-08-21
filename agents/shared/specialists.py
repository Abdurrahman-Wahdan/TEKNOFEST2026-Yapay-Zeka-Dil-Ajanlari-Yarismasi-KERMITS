"""Bank-specialist LangGraph agents."""

from langchain.agents import create_agent

from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from .bank_tools import build_bank_tools
from .checkpoints import get_checkpointer
from .compaction import build_compaction
from .registry import prompt_for
from .runtime import AgentContext


def build_specialist(bank: str, enforced_monthly_profit_rate: float | None = None):
    """Compile one isolated specialist with a fresh tunnel-aware model client.

    The graph is deliberately not cached.  A compiled agent retains the model
    instance it was built with, including that instance's reverse-proxy URL and
    HTTP connection pool.  Specialist state does not depend on the graph object:
    the PostgreSQL checkpointer and bank-specific thread ID provide persistence,
    so rebuilding here preserves memory while preventing a rotated tunnel from
    being pinned across chat turns.

    Specialist output is private and is returned to the supervisor only after
    the invocation completes.  Disabling model streaming here makes a dropped
    connection safe to replay inside ``TunnelAwareChatOpenAI._generate``: no
    partial prose or partial tool call has been emitted into the graph.
    """
    tools = build_bank_tools(
        bank, enforced_monthly_profit_rate=enforced_monthly_profit_rate
    )
    system_prompt = prompt_for(bank) + (
        "\nThis delegated turn has a customer-supplied monthly profit-rate "
        f"scenario fixed at {enforced_monthly_profit_rate}%. When you call "
        "finance_quote, that rate is enforced even if you omit the optional "
        "tool argument. Do not state a different rate."
        if enforced_monthly_profit_rate is not None
        else ""
    )
    # Each specialist is compacted on its own thread, against its own window.
    # Its prompt and bank tools are smaller than the supervisor's, so the space
    # left for the conversation is larger -- computed here rather than shared,
    # because "70% full" has to mean 70% of *this* agent's room.
    window = usable_context_window(resolve_model_key("chat"), system_prompt, tools)
    return create_agent(
        model=get_llm("chat", disable_streaming=True),
        tools=tools,
        system_prompt=system_prompt,
        context_schema=AgentContext,
        checkpointer=get_checkpointer(),
        middleware=[build_compaction(window, specialist=True)],
    )


def specialist_thread_id(session_id: str, bank: str) -> str:
    return f"{session_id}:bank:{bank}"
