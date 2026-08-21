"""Build the main LangGraph supervisor."""

from langchain.agents import create_agent

from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from ..shared.agent_tools import build_specialist_tools
from ..shared.checkpoints import get_checkpointer
from ..shared.compaction import build_compaction
from ..shared.runtime import AgentContext
from .prompt import NAME


def build_main_agent(model: str | None = None, thinking: bool = False):
    """Compile a fresh supervisor with exactly ten bank-specialist tools.

    Conversation state lives in the checkpointer, not in this graph object.
    Rebuilding per request prevents the supervisor itself from retaining an old
    tunnel-bound model client between chat turns while preserving its private
    ``<session>:main`` memory.

    Args:
        model: A key from ``MODELS`` ("gemma" | "qwen" | "gpt"), or None for the
            configured chat model. Per request rather than per process, because
            the user picks it in the composer and can change it mid-conversation
            -- the thread's memory is the checkpointer's, not the model's, so a
            switch keeps the history it has built up.
        thinking: Keep chain-of-thought on. Only models that reason by default
            are affected; see ``VLLMProvider.create``.
    """
    tools = build_specialist_tools()
    compaction, _ = main_compaction(model)
    return create_agent(
        # stream_usage: this agent streams, and a streamed response carries no
        # usage at all without it -- measured, every chunk came back with
        # usage_metadata=None. The specialists get theirs for free by not
        # streaming. Without this the supervisor is the one agent whose token
        # spend is invisible.
        model=get_llm(model or "chat", thinking=thinking, stream_usage=True),
        tools=tools,
        system_prompt=NAME,
        context_schema=AgentContext,
        checkpointer=get_checkpointer(),
        middleware=[compaction],
    )


def main_compaction(model: str | None = None):
    """The supervisor's compaction middleware, and the window it was sized to.

    Separate from `build_main_agent` because two callers need it: the agent, to
    be compacted while it runs, and the API, to report how full the thread is and
    to compact it when the user asks. Both must use *this* middleware -- its
    token counter and its threshold are what "70% full" means, and a second
    definition anywhere would let the number shown disagree with the number that
    fires.

    Returns:
        (middleware, usable_window).
    """
    tools = build_specialist_tools()
    # The same tool list the model is given: the threshold is a fraction of what
    # is left after these schemas, so measuring a different list would move it.
    window = usable_context_window(resolve_model_key(model or "chat"), NAME, tools)
    return build_compaction(window, specialist=False), window


def main_thread_id(session_id: str) -> str:
    return f"{session_id}:main"
