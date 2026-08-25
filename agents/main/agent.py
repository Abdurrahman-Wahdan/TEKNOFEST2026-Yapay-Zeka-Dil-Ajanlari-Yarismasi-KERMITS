"""Build the main LangGraph supervisor."""

from langchain.agents import create_agent

from config.settings import settings
from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from ..shared.agent_tools import build_specialist_tools
from ..shared.table_tools import build_table_directory_tool
from ..shared.checkpoints import get_checkpointer
from ..shared.compaction import build_compaction
from ..shared.runtime import AgentContext
from .prompt import NAME


SEARCH_ONLY_SUPERVISOR_GUIDANCE = """

SEARCH-ONLY ASSESSMENT MODE is active. Bank specialists can discover links with
search_bank_web but cannot open pages with read_bank_source. Do not instruct a
specialist to claim it inspected or verified a page body. Treat returned titles
and snippets as discovery leads only, clearly label them unverified, and say
that conclusive URL verification requires restoring the direct reader.
"""


def system_prompt() -> str:
    return NAME + (
        SEARCH_ONLY_SUPERVISOR_GUIDANCE
        if not settings.WEB_READ_SOURCE_ENABLED
        else ""
    )


def supervisor_tools():
    """The supervisor's tool list: ten bank specialists and one page directory.

    One list, one definition, because two callers need the exact same one --
    `build_main_agent` gives it to the model and `main_compaction` measures the
    window that is left after its schemas. Two definitions would let the
    "70% full" the user is shown disagree with the threshold that fires.

    `find_comparison_table` is the one tool here that is not a bank. It reads no
    bank data: it answers "does this site already have a table on this topic, and
    what is its address", so the assistant can link to a page the product already
    publishes. Everything factual still comes from the specialists.
    """
    return [*build_specialist_tools(), build_table_directory_tool()]


def build_main_agent(model: str | None = None, thinking: bool = False):
    """Compile a fresh supervisor with the tools `supervisor_tools` defines.

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
    tools = supervisor_tools()
    compaction, _ = main_compaction(model)
    prompt = system_prompt()
    return create_agent(
        # stream_usage: this agent streams, and a streamed response carries no
        # usage at all without it -- measured, every chunk came back with
        # usage_metadata=None. The specialists get theirs for free by not
        # streaming. Without this the supervisor is the one agent whose token
        # spend is invisible.
        model=get_llm(model or "chat", thinking=thinking, stream_usage=True),
        tools=tools,
        system_prompt=prompt,
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
    tools = supervisor_tools()
    # The same tool list the model is given: the threshold is a fraction of what
    # is left after these schemas, so measuring a different list would move it.
    window = usable_context_window(
        resolve_model_key(model or "chat"), system_prompt(), tools
    )
    return build_compaction(window, specialist=False), window


def main_thread_id(session_id: str) -> str:
    return f"{session_id}:main"
