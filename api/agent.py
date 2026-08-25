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
import re
import unicodedata
import uuid
from typing import Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from config.settings import settings
from index.retrieve import search
from llm import get_llm

from .converters import chunk_out
from .chat_attachments import ResolvedAttachment
from . import compare_tables_pool
from .table_links import parse_ui_url, ui_url
from .saved_tables import fingerprint, save_table_view
from .schemas.banks import ChunkOut
from .schemas.chat import AttachedContext, CapturePayload, StreamEvent, ToolResult

logger = logging.getLogger(__name__)


_BANK_MENTION_ALIASES = {
    "adil": ("adil katilim",),
    "albaraka": ("albaraka",),
    "dunya": ("dunya katilim",),
    "emlak": ("emlak katilim",),
    "hayat": ("hayat finans",),
    "kuveytturk": ("kuveyt turk",),
    "tom": ("t.o.m.", "tom katilim"),
    "turkiyefinans": ("turkiye finans",),
    "vakif": ("vakif katilim",),
    "ziraat": ("ziraat katilim",),
}


# Any markdown link, target captured. Not restricted to `/`-relative targets:
# the model decorates ours with an invented host (see `table_links.parse_ui_url`),
# and a pattern that only matched the relative form let those fall through into
# nothing. `parse_ui_url` does the deciding; this only has to find candidates.
#
# The whitespace and the optional angle brackets are load-bearing, not defensive.
# CommonMark allows `[label]( /path )` and `[label](</path>)`, the renderer
# accepts both, and the model does write them: observed on 2026-08-25, it emitted
# `[Konut Finansmanı]( /tr/urunler?tablo=konut-finansman%C4%B1)`. A stricter
# pattern renders a working link in the prose and silently no source card, which
# looks like the feature half-working rather than like a parser bug.
_MD_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^\s)>]+)>?\s*\)")

# The `source_type` these are emitted with. The UI groups the sources panel by
# this value, and this one is what puts a table under its own heading instead of
# among the bank pages the answer is evidenced by.
SITE_PAGE_SOURCE_TYPE = "site_page"


def site_table_sources(answer: str) -> list[dict]:
    """Comparison-table pages the answer links to, in the order they appear.

    Read out of the finished prose rather than from tool evidence, because that
    is what these are: the assistant offering a page, not citing a source. The
    citation machinery in `agents/shared/agent_tools.py` is for claim-level
    evidence and `find_comparison_table` is explicitly not that -- it returns no
    rate, fee or condition, so a table must never appear as the support for a
    factual claim.

    Two things are deliberately not taken from the model. The url is **rebuilt**
    from the table it names rather than echoed, so an invented hostname or a
    mangled encoding cannot reach the reader; and the title comes from the
    resolved table, so a card cannot disagree with the page it opens. Links that
    name no real table are dropped -- an invented slug produces a perfectly
    well-formed address, and asking the pool is the only thing that separates the
    two.

    Keyed by table id, so the same table linked twice, or linked once relative and
    once absolute, is listed once.
    """
    found: dict[str, dict] = {}
    for target in _MD_LINK.findall(answer or ""):
        parsed = parse_ui_url(target)
        if not parsed:
            continue
        table_id, category = parsed
        if table_id in found:
            continue
        table = compare_tables_pool.load_table(table_id)
        if table is None:
            logger.warning("Assistant linked a comparison table that does not exist: %s", target)
            continue
        canonical = ui_url(table_id, category)
        if not canonical:
            continue
        if canonical != target:
            logger.info("Rewrote a table link the model altered: %s -> %s", target, canonical)
        found[table_id] = {"url": canonical, "title": str(table.get("topic") or table_id)}
    return list(found.values())


def _searchable_text(value: str) -> str:
    """Case- and accent-insensitive text for conservative bank-name matching."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    # Unicode decomposition removes the marks from ü/ş/ç/ğ but Turkish dotless
    # ı is its own letter. Search aliases are intentionally ASCII, so fold it
    # explicitly or every name ending in "Katılım" fails the multi-bank audit.
    return folded.replace("ı", "i")


def _source_priority(source: dict) -> int:
    """Prefer a live bank feed, then an opened page, search hint, and index."""
    source_type = str(source.get("source_type") or "")
    if source_type == "live_endpoint":
        return 4
    if source_type.startswith("live_web_"):
        return 3
    if source.get("provenance") == "live_web":
        return 2
    return 1


def _audited_sources(
    answer: str,
    candidates: dict[tuple[str, str], dict],
    fresh_candidates: dict[tuple[str, str], dict],
    cited_source_keys: dict[str, str],
) -> list[dict]:
    """Select public citations from machine-preserved specialist evidence.

    Exact Markdown links win, including a follow-up that cites evidence from an
    earlier turn. If a fresh evidence-bearing specialist handoff reaches the
    supervisor but its links disappear during synthesis, add one representative
    source per bank and provenance class. Those candidates are already the
    intersection of actual tool output and the specialist's claim-level links;
    this is therefore a safety net, not a dump of search results.

    Ordinary conversation has no fresh candidates and still receives no source
    panel. Multi-bank fallbacks are limited to banks actually named in the
    answer; a one-bank response may use a pronoun, so its sole bank is retained.
    """
    selected: list[dict] = []
    covered: set[tuple[str, str]] = set()

    for (key, provenance), source in candidates.items():
        if key not in cited_source_keys:
            continue
        selected.append(source)
        covered.add((str(source.get("bank") or ""), provenance))

    fresh_banks = {
        str(source.get("bank") or "")
        for source in fresh_candidates.values()
        if source.get("bank")
    }
    searchable_answer = _searchable_text(answer)
    single_bank = len(fresh_banks) == 1

    grouped: dict[tuple[str, str], list[dict]] = {}
    for (_key, provenance), source in fresh_candidates.items():
        bank = str(source.get("bank") or "")
        group = (bank, provenance)
        if group in covered:
            continue
        aliases = _BANK_MENTION_ALIASES.get(bank, ())
        named = any(alias in searchable_answer for alias in aliases)
        if not (single_bank or named):
            continue
        grouped.setdefault(group, []).append(source)

    for group, sources in grouped.items():
        selected.append(max(sources, key=_source_priority))
        covered.add(group)

    # The same bank page can be returned by live discovery and Qdrant. Showing
    # it twice under two headings is confusing; when both were used, the live
    # provenance is the more current description of that exact URL.
    by_url: dict[str, dict] = {}
    order: list[str] = []
    for source in selected:
        url = str(source.get("url") or "").strip()
        if not url:
            continue
        key = url.rstrip(".,;:!?").rstrip("/")
        previous = by_url.get(key)
        if previous is None:
            order.append(key)
            by_url[key] = source
        elif _source_priority(source) > _source_priority(previous):
            by_url[key] = source
    return [by_url[key] for key in order]

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
    attachments: list[ResolvedAttachment] | None = None,
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
    attachments = attachments or []
    # Everything with bytes, whether the user attached it or the agent asked for it.
    # `look_at_page` in `both` mode returns each, and they take different routes:
    # the picture becomes an image block below, the outline joins the text.
    images_in = [
        *captures,
        *(image for attachment in attachments for image in attachment.images),
        *(r.image for r in tool_results if r.image),
    ]

    text_parts = []
    for result in tool_results:
        if result.text:
            text_parts.append(result.text)
    if attachments:
        file_blocks: list[str] = []
        image_cursor = len(captures) + 1
        for attachment in attachments:
            safe_name = _attr(attachment.filename)
            if attachment.text is not None:
                # Keep a user file visibly separate from instructions and from
                # retrieved evidence. It is still user-provided content, not a
                # system prompt merely because it contains Markdown.
                body = attachment.text.replace("</attached-file>", "< /attached-file>")
                file_blocks.append(
                    f'<attached-file filename="{safe_name}" type="{_attr(attachment.media_type)}">\n'
                    f"{body}\n</attached-file>"
                )
            elif attachment.images:
                first = image_cursor
                last = first + len(attachment.images) - 1
                image_cursor = last + 1
                page_note = (
                    f"The preceding image input {first} is this file."
                    if first == last
                    else f"The preceding image inputs {first}-{last} are pages 1-{len(attachment.images)} in order."
                )
                file_blocks.append(
                    f'<attached-file filename="{safe_name}" type="{_attr(attachment.media_type)}" '
                    f'images="{first}-{last}">{page_note}</attached-file>'
                )
        text_parts.append("\n\n".join(file_blocks))
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
    think: bool = False,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
    attachments: list[ResolvedAttachment] | None = None,
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
                question, chunks, context or [], captures or [], tool_results or [], attachments or []
            )
        )
    )

    llm = get_llm(model or "chat", thinking=think)
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
    think: bool = False,
    model: str | None = None,
    web_search: bool = False,
    attachments: list[ResolvedAttachment] | None = None,
) -> Iterator[StreamEvent]:
    """Stream the supervisor while keeping its checkpoint state private."""
    from agents.main.agent import build_main_agent, main_thread_id
    from agents.shared.agent_tools import (
        cited_sources_from_text,
        source_key,
        used_sources_from_tool_message,
    )

    yield StreamEvent(type="status", stage="pricing")
    config = {
        "configurable": {
            "thread_id": main_thread_id(str(session_id)),
            "tf26_session_id": str(session_id),
        }
    }
    try:
        agent = build_main_agent(model=model, thinking=think)
        state = agent.get_state(config)
        seeded = bool((state.values or {}).get("messages"))
        # The whole stored conversation, however long it is. Nothing is dropped
        # to make it fit: compaction runs in `before_model`, so a history longer
        # than the window is summarised before the model is ever called, and a
        # history longer than one summarising pass is folded rather than cut.
        #
        # This is the path that reloads a thread whose checkpoints are gone --
        # after a compaction the checkpoint holds the summary, so `seeded` is
        # true and the stored history is correctly left alone.
        messages: list = [] if seeded else list(history or [])
        # Reuse the existing attachment encoding, but with no supervisor RAG
        # context. Attachments are routing evidence it delegates to the relevant
        # bank specialists, which own retrieval and optional web research.
        messages.append(HumanMessage(content=_human_content(
            question, [], context or [], captures or [], tool_results or [], attachments or []
        )))
        # Keep the source registry already present in the checkpoint. This is
        # what lets a follow-up such as "show me the sources" render citation
        # cards without rerunning ten bank specialists merely to recover URLs.
        candidate_sources: dict[tuple[str, str], dict] = {}
        for prior_message in (state.values or {}).get("messages", []):
            if not isinstance(prior_message, ToolMessage):
                continue
            for source in used_sources_from_tool_message(prior_message):
                url = str(source.get("url") or "").strip()
                if not url:
                    continue
                provenance = str(source.get("provenance") or "")
                candidate_sources.setdefault((source_key(url), provenance), source)

        fresh_candidate_sources: dict[tuple[str, str], dict] = {}
        answer_text: list[str] = []
        for message, metadata in agent.stream(
            {"messages": messages},
            config=config,
            context={
                "session_id": str(session_id),
                "web_search_enabled": web_search,
            },
            stream_mode="messages",
        ):
            # Only filtered ask_<bank> handoffs expose candidate sources. Raw
            # nested tool traces contain every search result before the bank
            # specialist decides what actually supports its answer.
            if isinstance(message, ToolMessage):
                for source in used_sources_from_tool_message(message):
                    url = str(source.get("url") or "").strip()
                    if not url:
                        continue
                    provenance = str(source.get("provenance") or "")
                    key = (source_key(url), provenance)
                    candidate_sources.setdefault(key, source)
                    fresh_candidate_sources.setdefault(key, source)
                continue
            # Tool output and state bookkeeping must remain private. Only the
            # supervisor's generated prose is part of the public SSE response.
            if metadata.get("langgraph_node") != "model":
                continue
            if not isinstance(message, (AIMessage, AIMessageChunk)):
                continue
            if isinstance(message.content, str) and message.content:
                answer_text.append(message.content)
                yield StreamEvent(type="token", text=message.content)
        final_answer = "".join(answer_text)
        final_source_keys = cited_sources_from_text(final_answer)
        for source in _audited_sources(
            final_answer,
            candidate_sources,
            fresh_candidate_sources,
            final_source_keys,
        ):
            url = str(source.get("url") or "").strip()
            yield StreamEvent(
                type="citation",
                citation=ChunkOut(
                    score=1.0,
                    cite_url=url,
                    text="",
                    bank=str(source.get("bank") or ""),
                    title=str(source.get("title") or ""),
                    doc_kind=(
                        "knowledge_base"
                        if source.get("provenance") == "knowledge_base"
                        else "web"
                    ),
                    source_type=str(source.get("source_type") or ""),
                ),
            )
        # Our own pages, last, and kept apart from the evidence above. A table is
        # somewhere to go rather than something a claim rests on, so it is read
        # out of the prose instead of the tool-evidence ledger and carries its own
        # `source_type` for the UI to group under its own heading.
        for site_source in site_table_sources(final_answer):
            yield StreamEvent(
                type="citation",
                citation=ChunkOut(
                    score=1.0,
                    cite_url=site_source["url"],
                    text="",
                    bank="",
                    title=site_source["title"],
                    doc_kind="site_page",
                    source_type=SITE_PAGE_SOURCE_TYPE,
                ),
            )
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
    think: bool = False,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    web_search: bool = False,
    attachments: list[ResolvedAttachment] | None = None,
) -> Iterator[StreamEvent]:
    """Answer through the supervisor when a persisted chat session is available.

    ``session_id`` is supplied by the HTTP router. Keeping the old path for
    callers without one preserves the standalone API/test seam while they migrate.
    """
    if session_id is None:
        # Keyword-passed from here down. These two grew by two arguments in the
        # middle, and positional calls would have slid `user_id` into `think`
        # without a word from the type checker.
        yield from _legacy_answer(
            question, history, context, captures, tool_results, client_tools,
            think=think, model=model, user_id=user_id, attachments=attachments,
        )
        return
    yield from _agent_answer(
        question, history, context, captures, tool_results, session_id,
        think=think, model=model,
        web_search=web_search,
        attachments=attachments,
    )
