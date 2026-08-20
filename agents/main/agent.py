"""Build the main LangGraph supervisor."""

from functools import lru_cache

from langchain.agents import create_agent

from llm import get_llm

from ..shared.agent_tools import build_specialist_tools
from ..shared.checkpoints import get_checkpointer
from ..shared.runtime import AgentContext
from .prompt import NAME


@lru_cache(maxsize=1)
def build_main_agent():
    """Compile the supervisor with exactly the ten bank-specialist tools."""
    return create_agent(
        model=get_llm("chat"),
        tools=build_specialist_tools(),
        system_prompt=NAME,
        context_schema=AgentContext,
        checkpointer=get_checkpointer(),
    )


def main_thread_id(session_id: str) -> str:
    return f"{session_id}:main"
