"""Build the main LangGraph supervisor."""

from langchain.agents import create_agent

from config.settings import settings
from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from ..shared.agent_tools import build_specialist_tools
from ..shared.automation_tools import build_automation_tools
from ..shared.table_tools import build_table_directory_tool
from ..shared.checkpoints import get_checkpointer
from ..shared.clock import now_block
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
    """The supervisor's prompt, including what time it is.

    `now_block()` last, so the clock is the most recent thing the model read
    before the conversation starts.

    Note what this makes true of `main_compaction`, which calls this to size the
    window: the two calls are microseconds apart within one request, so they
    measure the same string. They would not if this were cached per process --
    then the window would have been sized against the boot-time clock, which is
    exactly the kind of quiet drift that function's docstring warns about.
    """
    return (
        NAME
        + (
            SEARCH_ONLY_SUPERVISOR_GUIDANCE
            if not settings.WEB_READ_SOURCE_ENABLED
            else ""
        )
        + now_block()
    )


def supervisor_tools():
    """The supervisor's tools: ten bank specialists, a page directory, automations.

    One list, one definition, because two callers need the exact same one --
    `build_main_agent` gives it to the model and `main_compaction` measures the
    window that is left after its schemas. Two definitions would let the
    "70% full" the user is shown disagree with the threshold that fires.

    That is also why this takes no arguments and nothing here is conditional.
    `create_automation` needs a signed-in user and is offered even when there is
    not one -- it refuses in prose at call time instead. Gating it on the caller
    would make the measured list differ from the list the model was given, which
    is exactly the disagreement the paragraph above exists to prevent.

    Four of these are not banks and none of the four carries a bank fact:

    - `find_comparison_table` answers "does this site already publish a table on
      this topic, and what is its address", so the assistant can link to a page
      the product already has.
    - `create_automation` / `update_automation` / `list_automations` store,
      change and read back a standing order -- a question to be asked again on a
      schedule. There is no delete: `update_automation` can pause one, which is
      reversible, and permanent removal stays a human click in the UI.

    Everything factual still comes from the specialists.
    """
    return [
        *build_specialist_tools(),
        build_table_directory_tool(),
        *build_automation_tools(),
    ]


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
    from .feedback import inject_feedback_context

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
        middleware=[compaction, inject_feedback_context],
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
