"""Bank-specialist LangGraph agents."""

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware

from config.settings import settings
from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from .bank_tools import build_bank_tools
from .checkpoints import get_checkpointer
from .compaction import build_compaction
from .registry import prompt_for
from .retrieval_memory import RetrievalMemory, RetrievalPruning
from .runtime import AgentContext

# Appended to every specialist's own prompt, in one place rather than in ten
# near-identical prompt modules that would drift apart.
#
# In English, like the prompt it joins. The tool descriptions it refers to are
# Turkish because the corpus is Turkish and the queries have to be -- but a
# system prompt that switches language mid-way is the exact mixed signal that
# makes a model start answering in the wrong one (see agents/shared/compaction.py,
# where the same problem was measured and fixed).
CORPUS_GUIDANCE = """

You can also search what this bank has published — its own pages and PDFs —
with search_bank, expand_chunk and read_full_page. Write your search queries in
Turkish; everything indexed is Turkish.

This is documentation, not live data. It has no retrieval time and it is not a
quote: cite the result's url, say what the bank published, and never present a
figure read out of a page as a current rate from a live calculator. Expired
campaigns are already filtered out of search results.

A result marked `parça=i/n` is piece i of a document with n+1 pieces, and you
are seeing only that piece. Documents are split without overlap, so anything
adjacent to it is in the neighbouring piece, not in front of you. Call
expand_chunk on that point_id and widen until you can see the whole passage.

Widen — do not guess and do not answer from the fragment — whenever:

- a sentence, table or list stops mid-way;
- a result ends on a heading or a section title with nothing underneath it. The
  heading is complete; the section it names is not. Its text is in the next
  piece;
- you are about to report that this bank does not publish something, and its
  title or heading appeared in a result. Seeing a title and no body is evidence
  that the body is one piece away, never evidence that it does not exist. Read
  it before you conclude anything about it.

Never answer a question about a named section from a different document because
the named one looked empty. If the heading is there, the section is there.

As you work, pass the point_ids you are finished with as not_useful so they stop
taking up room, and the ones your answer rests on as useful so they are kept."""


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
    # One memory per delegated turn, shared by the tools that write the model's
    # keep/drop decisions into it and the middleware that acts on them. Built
    # here rather than inside `build_bank_tools` because both halves need the
    # same object, and because a memory that outlived one bank's turn would be a
    # way for one specialist's decisions to reach another's thread.
    retrieval = RetrievalMemory()
    tools = build_bank_tools(
        bank,
        enforced_monthly_profit_rate=enforced_monthly_profit_rate,
        retrieval=retrieval,
    )
    system_prompt = prompt_for(bank) + CORPUS_GUIDANCE + (
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
        # Order matters. `before_model` hooks run in this order, so the thread is
        # trimmed of what the model discarded *before* compaction measures how
        # full it is -- otherwise a thread could be summarised on the strength of
        # passages the model had already asked to drop.
        middleware=[
            RetrievalPruning(retrieval),
            *_retrieval_limits(),
            build_compaction(window, specialist=True),
        ],
    )


def _retrieval_limits() -> list[ToolCallLimitMiddleware]:
    """A per-turn ceiling on each retrieval tool.

    Run-scoped, not thread-scoped: the budget is for answering one delegated
    request, and it resets when the supervisor asks again. Nothing here limits
    the live endpoint tools -- a specialist that needs six quotes to answer a
    question should make six calls.
    """
    return [
        ToolCallLimitMiddleware(tool_name=name, run_limit=limit,
                                exit_behavior="continue")
        for name, limit in (
            ("search_bank", settings.RETRIEVE_SEARCH_LIMIT),
            ("expand_chunk", settings.RETRIEVE_EXPAND_LIMIT),
            ("read_full_page", settings.RETRIEVE_PAGE_LIMIT),
        )
    ]


def specialist_thread_id(session_id: str, bank: str) -> str:
    return f"{session_id}:bank:{bank}"
