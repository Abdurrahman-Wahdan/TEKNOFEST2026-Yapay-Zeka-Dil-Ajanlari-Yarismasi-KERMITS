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
import uuid
from typing import Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from config.settings import settings
from index.retrieve import search
from llm import get_llm

from .converters import chunk_out
from .saved_tables import fingerprint, save_table_view
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
- `save_table` aracını YALNIZCA kullanıcı bir tabloyu paneline eklemeyi açıkça \
istediğinde kullan ("panele ekle", "kaydet", "bana bir tablo oluştur"). Kullanıcı \
sadece karşılaştırma sorduysa cevabı sohbette markdown tablo olarak yaz ve \
kaydetme.
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

# The tool the *server* runs itself. Same raw OpenAI-function format as
# LOOK_AT_PAGE rather than a LangChain `@tool`, because the loop below needs the
# parsed arguments in hand -- and because this must not join `banks.build_tools()`,
# which is the product-lookup surface and has nothing to do with dashboards.
#
# The arguments are a header list plus a matrix of strings, not a list of
# `{cells: {...}}` objects. A nested object is the likeliest thing for the model to
# get wrong, arguments arrive split across stream chunks, and a header-plus-matrix
# is exactly the shape a markdown table already has -- so the agent's path and the
# "save this chat table" button in the UI produce identical props.
SAVE_TABLE = {
    "type": "function",
    "function": {
        "name": "save_table",
        "description": (
            "Save a comparison table to the user's own AI Overview page, where it "
            "stays after the conversation ends. Use this ONLY when the user has "
            "explicitly asked for a table to be created for them or added to their "
            "page -- never for a comparison you are simply writing out in the "
            "answer. Saving over an existing table with the same title replaces "
            "it, so give the table a title that distinguishes it (include the "
            "amount and the term when they matter)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "The table's name, in the user's language. Shown as its "
                        "heading, and used to identify it -- a repeated title "
                        "replaces the earlier table."
                    ),
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The header labels, left to right.",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": (
                        "One array per row, in the same order and of the same "
                        "length as `columns`. Values as they should be read: "
                        "\"%2,89\", \"28.410 TL\", \"24 ay\"."
                    ),
                },
                "cite_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional. One source URL per row, by index; \"\" for a row "
                        "with none. Given one, the table shows a link back to the "
                        "bank for that row."
                    ),
                },
                "subtitle": {"type": "string", "description": "Optional, one line."},
                "notes": {
                    "type": "string",
                    "description": "Optional. Shown under the table.",
                },
            },
            "required": ["title", "columns", "rows"],
        },
    },
}

# Which side of the wire runs what. A client tool ends the stream -- the browser
# executes it and asks again. A server tool runs here, in this process, and the
# answer continues in the same response.
CLIENT_TOOLS = ("look_at_page",)
SERVER_TOOLS = ("save_table",)


def _assistant_turn(gathered) -> AIMessage:
    """The accumulated stream chunk as a message the model can be sent back.

    A plain `AIMessage` rather than the `AIMessageChunk` itself: chunk types are
    not the documented input shape, and `content` may be a list on a multimodal
    turn. The `tool_calls` have to survive, because the `ToolMessage` that follows
    is keyed to their ids.
    """
    return AIMessage(
        content=getattr(gathered, "content", "") or "",
        tool_calls=list(getattr(gathered, "tool_calls", None) or []),
    )


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


def _legacy_answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    context: list[AttachedContext] | None = None,
    captures: list[CapturePayload] | None = None,
    tool_results: list[ToolResult] | None = None,
    client_tools: list[str] | None = None,
    user_id: uuid.UUID | None = None,
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
        user_id: whose dashboard `save_table` writes to. The tool is offered only
            when this is set -- a caller with no user has no dashboard.

    Yields:
        StreamEvent: status, then citations, then tokens, and a `saved_view` frame
        for each table written. The router turns these into SSE frames and
        persists the assembled answer.
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
    tools = []
    # Offered only when the caller says it can run it. A consumer with no browser
    # has no page to look at, and asking it to would strand the exchange waiting
    # for a result that can never arrive.
    if client_tools and "look_at_page" in client_tools:
        tools.append(LOOK_AT_PAGE)
    if user_id is not None:
        tools.append(SAVE_TABLE)
    if tools:
        llm = llm.bind_tools(tools)

    # Fingerprints of the server-side calls already carried out. This is what
    # lets the loop run **without a pass limit**: a count would break real work --
    # "make me five tables" is five passes, and a cap of three stops at the third
    # with the model believing it saved five. Terminating on *progress* instead
    # bounds the pathology (the same save repeating) and leaves the legitimate
    # case unbounded. It also makes a re-emitted identical call write once.
    executed: set[str] = set()

    while True:
        try:
            gathered = None
            for piece in llm.stream(messages):
                text = piece.content
                # Multimodal models can hand back a content *list*; only prose is
                # a token to forward.
                if isinstance(text, str) and text:
                    yield StreamEvent(type="token", text=text)
                # Accumulated because a tool call arrives split across chunks --
                # the name in one, the arguments a few characters at a time after.
                gathered = piece if gathered is None else gathered + piece
        except Exception:
            logger.exception("Generation failed")
            yield StreamEvent(type="error", detail="The language model is unavailable.")
            return

        calls = getattr(gathered, "tool_calls", None) or []
        client_calls = [c for c in calls if c.get("name") in CLIENT_TOOLS]
        server_calls = [c for c in calls if c.get("name") in SERVER_TOOLS]
        fresh = [c for c in server_calls if fingerprint(
            c.get("name") or "", c.get("args") or {}
        ) not in executed]

        # Server tools run first, before any chance of the stream ending. They are
        # side-effect-complete, so doing them now means the write is not lost if
        # the client never comes back for the `look_at_page` round trip.
        if fresh:
            messages.append(_assistant_turn(gathered))
        for call in fresh:
            executed.add(fingerprint(call.get("name") or "", call.get("args") or {}))
            # Never raises: a failure comes back as prose the model reads. An
            # exception here would surface as an `error` frame, and the router
            # discards the whole assembled answer on an error -- so a failed save
            # would delete a good answer.
            note, saved = save_table_view(call.get("args") or {}, user_id)
            messages.append(
                ToolMessage(content=note, tool_call_id=call.get("id") or "call-1")
            )
            if saved is not None:
                yield StreamEvent(
                    type="saved_view",
                    view_slug=saved.slug,
                    view_title=saved.title,
                )

        if client_calls:
            call = client_calls[0]
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
            # The client runs it, asks again with the result, and the answer
            # continues on that request.
            return

        if not fresh:
            # A plain answer, or a model repeating a call it has already made.
            return


def _agent_answer(
    question: str,
    history: list[tuple[str, str]] | None,
    context: list[AttachedContext] | None,
    captures: list[CapturePayload] | None,
    tool_results: list[ToolResult] | None,
    session_id: uuid.UUID,
) -> Iterator[StreamEvent]:
    """Stream the supervisor while keeping its checkpoint state private."""
    from agents.main.agent import build_main_agent, main_thread_id

    yield StreamEvent(type="status", stage="pricing")
    config = {
        "configurable": {
            "thread_id": main_thread_id(str(session_id)),
            "tf26_session_id": str(session_id),
        }
    }
    try:
        agent = build_main_agent()
        state = agent.get_state(config)
        seeded = bool((state.values or {}).get("messages"))
        messages: list = [] if seeded else list(history or [])
        # Reuse the existing attachment encoding, but with no RAG context. The
        # supervisor has no retrieval tool in this milestone; a user attachment
        # is still part of the request it may delegate to a bank specialist.
        messages.append(HumanMessage(content=_human_content(
            question, [], context or [], captures or [], tool_results or []
        )))
        for message, metadata in agent.stream(
            {"messages": messages},
            config=config,
            context={"session_id": str(session_id)},
            stream_mode="messages",
        ):
            # Tool output and state bookkeeping must remain private. Only the
            # supervisor's generated prose is part of the public SSE response.
            if metadata.get("langgraph_node") != "model":
                continue
            if not isinstance(message, (AIMessage, AIMessageChunk)):
                continue
            if isinstance(message.content, str) and message.content:
                yield StreamEvent(type="token", text=message.content)
    except Exception:
        logger.exception("Live agent failed for chat session %s", session_id)
        yield StreamEvent(type="error", detail="The live banking assistant is unavailable.")


def answer(
    question: str,
    history: list[tuple[str, str]] | None = None,
    context: list[AttachedContext] | None = None,
    captures: list[CapturePayload] | None = None,
    tool_results: list[ToolResult] | None = None,
    client_tools: list[str] | None = None,
    user_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
) -> Iterator[StreamEvent]:
    """Answer through the supervisor when a persisted chat session is available.

    ``session_id`` is supplied by the HTTP router. Keeping the old path for
    callers without one preserves the standalone API/test seam while they migrate.
    """
    if session_id is None:
        yield from _legacy_answer(
            question, history, context, captures, tool_results, client_tools, user_id
        )
        return
    yield from _agent_answer(question, history, context, captures, tool_results, session_id)
