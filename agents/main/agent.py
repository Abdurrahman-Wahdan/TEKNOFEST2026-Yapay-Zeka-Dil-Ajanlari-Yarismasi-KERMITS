"""Build the main LangGraph supervisor."""

from langchain.agents import create_agent

from llm import get_llm

from ..shared.agent_tools import build_specialist_tools
from ..shared.checkpoints import get_checkpointer
from ..shared.runtime import AgentContext
from .prompt import NAME


def build_main_agent():
    """Compile a fresh supervisor with exactly ten bank-specialist tools.

    Conversation state lives in the checkpointer, not in this graph object.
    Rebuilding per request prevents the supervisor itself from retaining an old
    tunnel-bound model client between chat turns while preserving its private
    ``<session>:main`` memory.
    """
    return create_agent(
        model=get_llm("chat"),
        tools=build_specialist_tools(),
        system_prompt=NAME,
        context_schema=AgentContext,
        checkpointer=get_checkpointer(),
    )


def main_thread_id(session_id: str) -> str:
    return f"{session_id}:main"
