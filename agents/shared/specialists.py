"""Bank-specialist LangGraph agents."""

from functools import lru_cache

from langchain.agents import create_agent

from llm import get_llm

from .bank_tools import build_bank_tools
from .checkpoints import get_checkpointer
from .registry import prompt_for
from .runtime import AgentContext


@lru_cache(maxsize=10)
def build_specialist(bank: str):
    """Compile one isolated specialist; state isolation comes from thread IDs."""
    return create_agent(
        model=get_llm("chat"),
        tools=build_bank_tools(bank),
        system_prompt=prompt_for(bank),
        context_schema=AgentContext,
        checkpointer=get_checkpointer(),
    )


def specialist_thread_id(session_id: str, bank: str) -> str:
    return f"{session_id}:bank:{bank}"
