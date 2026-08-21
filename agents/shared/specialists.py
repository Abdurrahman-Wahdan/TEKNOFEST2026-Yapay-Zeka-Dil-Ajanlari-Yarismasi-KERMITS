"""Bank-specialist LangGraph agents."""

from functools import lru_cache

from langchain.agents import create_agent

from llm import get_llm

from .bank_tools import build_bank_tools
from .checkpoints import get_checkpointer
from .registry import prompt_for
from .runtime import AgentContext


@lru_cache(maxsize=128)
def build_specialist(bank: str, enforced_monthly_profit_rate: float | None = None):
    """Compile one isolated specialist; state isolation comes from thread IDs."""
    return create_agent(
        model=get_llm("chat"),
        tools=build_bank_tools(
            bank, enforced_monthly_profit_rate=enforced_monthly_profit_rate
        ),
        system_prompt=(
            prompt_for(bank)
            + (
                "\nThis delegated turn has a customer-supplied monthly profit-rate "
                f"scenario fixed at {enforced_monthly_profit_rate}%. When you call "
                "finance_quote, that rate is enforced even if you omit the optional "
                "tool argument. Do not state a different rate."
                if enforced_monthly_profit_rate is not None
                else ""
            )
        ),
        context_schema=AgentContext,
        checkpointer=get_checkpointer(),
    )


def specialist_thread_id(session_id: str, bank: str) -> str:
    return f"{session_id}:bank:{bank}"
