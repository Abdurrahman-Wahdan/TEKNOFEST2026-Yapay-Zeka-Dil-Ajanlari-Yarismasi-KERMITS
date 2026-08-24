"""One-shot agent that reads one comparison table and says what it shows.

Deliberately stateless: no session, no history, no retrieval. It is handed the
page as an outline and answers about that. That is the whole contract: the same
page in a week produces the same overview, which is what makes the result
cacheable at all.

**Text only, and that is the point.** Two other things were tried and dropped.
The server's own table JSON: the same figures a second time, in a second
spelling, and the one the user is looking at is the one worth summarising. A
screenshot of the page: it cost several minutes of vision prefill per table,
and everything it carried the outline already carries — once
`data-outline-list` taught the outline to keep the short-line cards it had been
dropping, which is what the picture had been covering for.

The outline is the data, not a description of it: every table on the page as
markdown, the current filter state, and the lists beside them, wrapped in a
`<page-snapshot>` element that says which page it came from.

"""

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from llm import get_llm

from .models import TableOverview
from .prompt import NAME

LANGUAGES = {"tr": "Turkish", "en": "English"}


def build_table_overview_agent():
    """Build a fresh agent so a rotated Gemma tunnel is never pinned here.

    Two departures from the other specialists, both because this one runs
    unattended and reads a picture.

    **It streams.** The sibling specialists set `disable_streaming=True` and
    call `invoke`, which is fine for a call that answers in seconds. This one
    sends a screenshot, so the model spends a minute or more on vision prefill
    before it emits anything — and a connection that silent gets closed by the
    tunnel in front of the host, at around two minutes, with
    `RemoteProtocolError: Server disconnected without sending a response`.
    Streaming keeps chunks flowing, which is the only reason the chat has never
    hit this: it has streamed from the start.

    **It waits as long as the chat waits.** A shorter retry window was tried
    here and it was the wrong lever: the host disconnects a slow request while
    it is still in prefill, and the answer to that is the same one the chat has
    always used — retry, and keep retrying. Measured on the same busy host, the
    chat succeeded on its third attempt while this gave up on its second. The
    reason a short window looked necessary — a stuck call holding an HTTP
    worker — went away when generation moved to a background thread: the POST
    returns 202 immediately and nobody is holding a socket while this waits.
    """
    return create_agent(
        model=get_llm("chat", streaming=True),
        tools=[],
        system_prompt=NAME,
        response_format=ToolStrategy(TableOverview),
        name="table_overview",
    )


def generate_table_overview(page_text: str, *, locale: str = "tr") -> TableOverview:
    """Read one page as an outline and summarise it.

    `page_text` is what the browser's own page reader produced: the
    `<page-snapshot>` outline. Required, because it is the only source there is.
    """
    if not page_text or not page_text.strip():
        raise ValueError("An overview needs the page outline; none was given.")

    language = LANGUAGES.get(locale, LANGUAGES["tr"])
    result = build_table_overview_agent().invoke(
        {
            "messages": [
                HumanMessage(
                    content=f"Write the overview in {language}.\n\nThe page:\n\n{page_text}"
                )
            ]
        }
    )
    structured = result.get("structured_response")
    if not isinstance(structured, TableOverview):
        raise RuntimeError("The table overview agent returned no validated result.")
    return structured
