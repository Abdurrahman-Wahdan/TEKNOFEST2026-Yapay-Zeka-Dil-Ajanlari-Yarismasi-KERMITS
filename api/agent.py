"""The seam where the LangGraph agent plugs in.

**Status: this is a retrieval-and-answer pipeline, not the agent.** The agent
layer -- the one that decides between calling a bank endpoint and searching the
corpus, per HANDOFF §2 -- is not built yet. What is here retrieves from the
index and streams an answer from the chat model, which is enough for the
dashboard's chat panel to be real while that work happens.

Replacing it means changing `answer()` and nothing else. The router knows only
that it yields `StreamEvent`s, so a LangGraph `astream_events` loop can be
dropped in without touching HTTP, persistence, or the frontend.

Two rules it already honours, because they are not the agent's to break:

- **Every claim carries its citation.** Retrieved chunks are streamed to the
  client as they are chosen, so the sources appear beside the answer rather
  than being reconstructed afterwards.
- **Expired campaigns are filtered at query time**, not remembered. The index
  is not re-embedded when a campaign expires; `active_only` is what makes the
  same index correct tomorrow.
"""

import logging
from typing import Iterator

from langchain_core.messages import HumanMessage

from config.settings import settings
from index.retrieve import search
from llm import get_llm

from .converters import chunk_out
from .schemas.chat import AttachedContext, CapturePayload, StreamEvent, ToolResult

logger = logging.getLogger(__name__)

# The model is told to answer only from what it was given. It is not told to be
# helpful when it has nothing -- a bank-facing tool that fills a gap with a
# plausible rate is worse than one that says it does not know.
SYSTEM_PROMPT = """\
Sen Türk katılım bankalarının kampanyalarını ve ürünlerini karşılaştıran bir \
asistansın.

Kurallar:
- Yalnızca sana verilen kaynaklardaki bilgiyi kullan. Kaynaklarda yoksa \
"bu bilgi kaynaklarımda yok" de.
- Hiçbir oranı, taksiti veya tutarı kendin hesaplama. Sayı bankanın verdiği \
sayıdır.
- Her iddiadan sonra kaynağı [1], [2] biçiminde numarayla belirt.
- Kullanıcı hangi dilde sorduysa o dilde cevap ver.
- Kullanıcının açık olduğu sayfayı görmen gerekiyorsa `look_at_page` aracını \
kullan. Rakamlar, oranlar ve tablodaki değerler için `text`; yerleşim veya \
görünüm sorularında `image`; emin değilsen `both`.
"""

# The one tool the *client* runs on the model's behalf. Declared in OpenAI
# function format, which `bind_tools` takes as-is and vLLM serves for Gemma 4
# given `--enable-auto-tool-choice --tool-call-parser gemma4`.
#
# One tool with a mode rather than three tools: looking at the page is a single
# capability with two representations of the same thing, and separate
# read/screenshot tools made the model commit before it knew which it needed --
# then cost a whole extra exchange whenever it chose wrong.
LOOK_AT_PAGE = {
    "type": "function",
    "function": {
        "name": "look_at_page",
        "description": (
            "Look at the page the user currently has open in the app. Use this "
            "when the question is about what they are looking at rather than "
            "about the sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["text", "image", "both"],
                    "description": (
                        "text: the page as a structured outline, with exact "
                        "figures and the filters currently applied -- use this "
                        "for anything about numbers. image: a screenshot -- use "
                        "this for questions about layout or appearance. both: "
                        "one round trip carrying each; use when unsure."
                    ),
                }
            },
            "required": ["mode"],
        },
    },
}

MODES = ("text", "image", "both")


def _context_block(context: list[AttachedContext]) -> str:
    """What the user attached, tagged so it cannot be read as their own words.

    An XML-ish envelope rather than more markdown, carrying every coordinate the
    browser worked out: which table, what that table is about, which row, which
    column. That is what lets the model answer "is this instalment good?" instead
    of asking which instalment -- the whole point of attaching a row.

    Kept in step with `contextToPromptBlock` in
    `UI/src/lib/chat/context-format.ts`, which is the reference rendering.
    """
    blocks = []
    for item in context:
        loc = item.location
        attrs = [f'kind="{_attr(item.kind)}"', f'label="{_attr(item.label)}"']
        if loc.page or loc.path:
            attrs.append(f'page="{_attr(loc.page or loc.path)}"')
        if loc.page and loc.path:
            attrs.append(f'path="{_attr(loc.path)}"')
        if loc.section and loc.section != loc.table:
            attrs.append(f'section="{_attr(loc.section)}"')
        if loc.table:
            attrs.append(f'table="{_attr(loc.table)}"')
        if loc.about:
            attrs.append(f'about="{_attr(loc.about)}"')
        if loc.row:
            attrs.append(f'row="{_attr(loc.row)}"')
        if loc.column:
            attrs.append(f'column="{_attr(loc.column)}"')
        if loc.kind:
            attrs.append(f'element="{_attr(loc.kind)}"')
        blocks.append(
            f"<attached-context {' '.join(attrs)}>\n{item.body}\n</attached-context>"
        )
    return "\n\n".join(blocks)


def _attr(value: str) -> str:
    """An attribute value that cannot end the attribute or the tag.

    Row labels and table titles come from page content, which on a produced table
    came from a bank's own website -- so it is not trusted input.
    """
    return value.replace('"', "'").replace("<", "").replace(">", "")


def _human_content(
    question: str,
    chunks,
    context: list[AttachedContext],
    captures: list[CapturePayload],
    tool_results: list[ToolResult] | None = None,
) -> str | list[dict]:
    """The user's turn, as either a string or a multimodal content list.

    **This is the function that decides whether a screenshot reaches the model as
    an image.** vLLM serves Gemma 4 on an OpenAI-compatible route, so an image is
    an `image_url` block whose URL is a data URI; anything else -- most easily, the
    base64 pasted into the text -- shows the model a wall of characters, has it
    answer confidently from nothing, and bills for all of it.

    Images go **first**. Gemma 4's own chat template puts image content before the
    text, and the browser already orders the turn that way.

    Returns a plain string when there are no images, so a text-only turn produces
    exactly the request it did before this existed.
    """
    tool_results = tool_results or []
    # Everything with bytes, whether the user attached it or the agent asked for it.
    # `look_at_page` in `both` mode returns each, and they take different routes:
    # the picture becomes an image block below, the outline joins the text.
    images_in = [*captures, *(r.image for r in tool_results if r.image)]

    text_parts = []
    for result in tool_results:
        if result.text:
            text_parts.append(result.text)
    if context:
        text_parts.append(_context_block(context))
    text_parts.append(f"Kaynaklar:\n\n{_sources_block(chunks)}")
    # The question last, after everything it refers to.
    text_parts.append(f"Soru: {question}" if question.strip() else "Soru: (yukarıdakine bak)")
    text = "\n\n".join(text_parts)

    # Built before the decision, not after: a capture with no bytes is dropped, and
    # if that leaves nothing there is no reason to wrap the turn in a multimodal
    # envelope at all. A list holding only a text block is a request that claims to
    # carry an image and does not.
    images: list[dict] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{c.media_type};base64,{c.data}"},
        }
        for c in images_in
        if c and c.data
    ]
    if not images:
        return text

    return [*images, {"type": "text", "text": text}]


def _sources_block(chunks) -> str:
    """The retrieved passages, numbered so the model's [n] markers line up."""
    parts = []
    for number, chunk in enumerate(chunks, start=1):
        bank = chunk.payload.get("bank", "?")
        title = chunk.payload.get("title", "")
        # A passage read by OCR is labelled in the prompt itself, so the model
        # hedges on a figure taken from a scan rather than stating it flatly.
        scanned = " (taranmış sayfa, rakamlar için dikkat)" if chunk.from_vision else ""
        parts.append(f"[{number}] {bank} — {title}{scanned}\n{chunk.text}")
    return "\n\n".join(parts)


def answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    context: list[AttachedContext] | None = None,
    captures: list[CapturePayload] | None = None,
    tool_results: list[ToolResult] | None = None,
    client_tools: list[str] | None = None,
) -> Iterator[StreamEvent]:
    """Answer a question, yielding stream events as the work happens.

    Args:
        question: the user's question, in Turkish or English.
        history: (role, content) turns, oldest first -- the whole conversation.
            Not windowed: nothing on the way to the model is truncated.
        context: pieces of the UI the user attached -- a table, a row, a quote --
            already serialised by the browser.
        captures: screenshots of the page, sent to the model as image content
            blocks rather than as text.
        tool_results: answers to `look_at_page` calls -- the page as text, as an
            image, or both. Routed the same way: text into the prose, image into an
            image block.
        client_tools: what the caller can execute. `look_at_page` is offered to the
            model only if it is in here.

    Yields:
        StreamEvent: status, then citations, then tokens. The router turns these
        into SSE frames and persists the assembled answer.
    """
    yield StreamEvent(type="status", stage="retrieving")

    try:
        chunks = search(question, k=settings.INDEX_RETRIEVE_TOP_K)
    except Exception:
        logger.exception("Retrieval failed")
        yield StreamEvent(
            type="error", detail="The search index is unavailable."
        )
        return

    for chunk in chunks:
        yield StreamEvent(type="citation", citation=chunk_out(chunk))

    yield StreamEvent(type="status", stage="writing")

    messages: list = [("system", SYSTEM_PROMPT)]
    messages.extend(history or [])
    messages.append(
        HumanMessage(
            content=_human_content(
                question, chunks, context or [], captures or [], tool_results or []
            )
        )
    )

    llm = get_llm("chat")
    # Offered only when the caller says it can run it. A consumer with no browser
    # has no page to look at, and asking it to would strand the exchange waiting
    # for a result that can never arrive.
    if client_tools and "look_at_page" in client_tools:
        llm = llm.bind_tools([LOOK_AT_PAGE])

    try:
        gathered = None
        for piece in llm.stream(messages):
            text = piece.content
            # Multimodal models can hand back a content *list*; only prose is a
            # token to forward.
            if isinstance(text, str) and text:
                yield StreamEvent(type="token", text=text)
            # Accumulated because a tool call arrives split across chunks -- the
            # name in one, the arguments a few characters at a time after it.
            gathered = piece if gathered is None else gathered + piece

        for call in getattr(gathered, "tool_calls", None) or []:
            if call.get("name") != "look_at_page":
                continue
            mode = (call.get("args") or {}).get("mode", "both")
            # The model writes this, so it is not trusted to be one of ours.
            if mode not in MODES:
                mode = "both"
            yield StreamEvent(
                type="tool_call",
                tool_call_id=call.get("id") or "call-1",
                tool_name="look_at_page",
                mode=mode,
            )
            # One per turn. The client runs it, asks again with the result, and
            # the answer continues on that pass.
            return
    except Exception:
        logger.exception("Generation failed")
        yield StreamEvent(type="error", detail="The language model is unavailable.")
