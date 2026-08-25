"""Bank-specialist LangGraph agents."""

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware

from config.settings import settings
from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from .bank_tools import build_bank_tools
from .checkpoints import get_checkpointer
from .clock import now_block
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

For every claim you actually derive from indexed documentation, place that
document's exact URL in a clickable Markdown link immediately after the claim.
Do not append a catalogue of every retrieved URL. A URL belongs in your final
response only when its retrieved passage directly supports information you
carry into that response. Use a human-readable page or document title as the
link label; never expose a Qdrant point_id or UUID to the user.

A result marked `tür=görsel` is text read out of an image on the page at its
`url=` -- a campaign banner or a visual, so it is short by nature and being short
is not evidence that it was cut off. `görsel=` is where that image lives; cite the
page `url=`, not the image, and never present the image address as the source
page. The page's own prose is indexed separately under the same `url=`, so call
read_full_page on it when a banner states an offer you need the conditions for.

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

FORMAT_GUIDANCE = """

Write application or website menu paths with the literal Unicode arrow `→`,
for example `Mobil Şube → Hesap → Yatırım Hesabı Aç`. Never write simple arrows
as LaTeX such as `$\\rightarrow$`; this output is prose, not a math document."""

TOOL_SELECTION_GUIDANCE = """

Before answering, analyze the delegated request and select the available tools
that can actually establish each requested fact. The source priority is:

1. Use this bank's live endpoint tools first for current rates, quotes,
   calculations, exchange values, and feeds.
2. Use search_bank, expand_chunk, and read_full_page for published product
   terms, conditions, context, and requested facts a live endpoint does not
   expose.
3. When live web tools are present, use them as a supplemental way to discover
   or verify current bank publications. Their presence does not make them
   mandatory unless this prompt separately says the request REQUIRES web
   discovery.

Every successful live endpoint result includes a `source_url` and
`source_title` for the bank's official public calculator or feed page. Every
claim derived from that live result MUST carry that exact URL immediately after
the claim as `[source title](https://...)`. The retrieval timestamp says when
the endpoint was called; the link lets the user inspect the official bank
surface behind it. Never cite an opaque JSON service route or invent a page.

Web search being absent never removes or invalidates the live endpoint and
indexed tools you still have. Do not tell the supervisor or user that you cannot
answer an ordinary bank question merely because web research is unavailable.
Use the tools in hand. If none supplies a requested current figure, say exactly
which fact is unavailable for this bank after using the relevant tools; preserve
the facts they did supply and do not turn one missing value into a refusal of
the whole request."""

WEB_GUIDANCE = """

Live web research is enabled for this delegated turn. It is still restricted to
this bank; you cannot search or open another bank's domain.

Use read_bank_source directly when the request, an attached table row, or a
retrieved chunk contains an exact URL. Do not waste a web search on a URL you
already have. Use search_bank_web only when you need to discover a page that the
known URLs and indexed corpus did not provide. A search snippet is never enough:
open the result with read_bank_source before relying on a claim.

Current web pages can change after the indexed document or attached table was
created. Compare the live page to the supplied/retrieved claim and state any
conflict, missing condition, changed date, or unavailable page. A live web read
is published evidence at its retrieved_at time; it is not a calculator quote.

Your final response is the only part the supervisor can see. It MUST preserve
the evidence you actually used: source type (live_endpoint, indexed_document,
or live_web_page/live_web_pdf/live_web_image), exact URL, retrieved_at for live reads, the
relevant fact, and any conflict or limitation. Never merely say that you
checked a source. If a requested URL was unreadable, include that URL and the
tool's status instead of silently answering from something else.

Every factual statement derived from search_bank_web or read_bank_source MUST
carry its exact source as a clickable Markdown link immediately after that
statement: `[source title](https://...)`. Do not cite a search-results page,
invent a URL, or collect links only at the end while leaving the claims
unattributed. Do not list every page the search returned: cite only a page whose
snippet or opened body directly supplied a fact you used. Keep the other source
classes labelled according to their own guidance."""

SEARCH_ONLY_GUIDANCE = """

Live web discovery is enabled in SEARCH-ONLY ASSESSMENT MODE. You have
search_bank_web, but read_bank_source is deliberately absent. Use web search to
discover this bank's pages and report result URLs, titles, and snippets.

This mode cannot inspect page bodies. A search snippet is a discovery hint, not
verified evidence. Never claim that a condition, rate, date, or campaign was
confirmed from the page. Say explicitly when your answer is based only on a
search snippet, and recommend re-enabling read_bank_source for conclusive
verification. Do not invent a missing reader call or imply that you opened a
result. If you report anything from a search result, put its exact result URL in
a clickable Markdown link immediately after the title or snippet-derived
statement: `[source title](https://...)`. Select only results whose snippets
actually contribute information to your answer; never dump the complete search
result list as a source catalogue."""

MANDATORY_WEB_GUIDANCE = """

This delegated request REQUIRES web discovery. You MUST call search_bank_web at
least once during this turn before writing your final response, even if live
endpoints or indexed retrieval already appear sufficient. Indexed search_bank,
expand_chunk, and read_full_page do not satisfy this requirement. Report the
web tool's real result, including `no_results` or an error; never replace a
failed or empty web search with an unsupported claim that web research was
completed."""


def build_specialist(
    bank: str,
    enforced_monthly_profit_rate: float | None = None,
    web_research_enabled: bool = False,
    web_research_required: bool = False,
):
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
        web_research_enabled=web_research_enabled,
    )
    web_guidance = ""
    if web_research_enabled:
        web_guidance = (
            WEB_GUIDANCE
            if settings.WEB_READ_SOURCE_ENABLED
            else SEARCH_ONLY_GUIDANCE
        )
    required_web_guidance = (
        MANDATORY_WEB_GUIDANCE
        if web_research_enabled and web_research_required
        else ""
    )
    system_prompt = (
        prompt_for(bank)
        + CORPUS_GUIDANCE
        + TOOL_SELECTION_GUIDANCE
        + FORMAT_GUIDANCE
        + web_guidance
        + required_web_guidance
        + (
            "\nThis delegated turn has a customer-supplied monthly profit-rate "
            f"scenario fixed at {enforced_monthly_profit_rate}%. When you call "
            "finance_quote, that rate is enforced even if you omit the optional "
            "tool argument. Do not state a different rate."
            if enforced_monthly_profit_rate is not None
            else ""
        )
        # The clock, last, and on every specialist rather than only the
        # supervisor. A specialist is the one that reads a campaign's end date
        # off a page and decides whether it has passed, and it is the one that
        # stamps `retrieved_at` -- so it is the agent that most needs to know
        # what day it is. Delegated turns carry no conversation history to
        # infer it from either: the specialist sees one instruction and its own
        # tool results.
        + now_block()
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
            *_retrieval_limits(web_research_enabled),
            build_compaction(window, specialist=True),
        ],
    )


def _retrieval_limits(web_research_enabled: bool = False) -> list[ToolCallLimitMiddleware]:
    """A per-turn ceiling on each retrieval tool.

    Run-scoped, not thread-scoped: the budget is for answering one delegated
    request, and it resets when the supervisor asks again. Nothing here limits
    the live endpoint tools -- a specialist that needs six quotes to answer a
    question should make six calls.
    """
    limits = [
        ToolCallLimitMiddleware(tool_name=name, run_limit=limit,
                                exit_behavior="continue")
        for name, limit in (
            ("search_bank", settings.RETRIEVE_SEARCH_LIMIT),
            ("expand_chunk", settings.RETRIEVE_EXPAND_LIMIT),
            ("read_full_page", settings.RETRIEVE_PAGE_LIMIT),
        )
    ]
    if web_research_enabled:
        limits.append(
            ToolCallLimitMiddleware(
                tool_name="search_bank_web",
                run_limit=settings.WEB_SEARCH_TOOL_LIMIT,
                exit_behavior="continue",
            )
        )
        if settings.WEB_READ_SOURCE_ENABLED:
            limits.append(ToolCallLimitMiddleware(
                tool_name="read_bank_source",
                run_limit=settings.WEB_READ_TOOL_LIMIT,
                exit_behavior="continue",
            ))
    return limits


def specialist_thread_id(session_id: str, bank: str) -> str:
    return f"{session_id}:bank:{bank}"
