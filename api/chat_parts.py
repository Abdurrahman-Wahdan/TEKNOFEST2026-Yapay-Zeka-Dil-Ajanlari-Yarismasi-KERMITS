"""A stored turn, as the reader saw it.

`ChatMessage.content` is the turn as the *model* reads it: one flattened string,
which is what `api/routers/chat.py` replays into the agent's history. It is not
enough to redraw the turn. A question that came with a table attached, an answer
with nine citations under it, a "looked at your page" note -- all of that is
structure, and flattening it was fine only while the transcript on screen came
from the browser's own localStorage.

It no longer does. History belongs to the account, so the server has to be able
to hand back a turn the browser can render, which means writing down the same
`MessagePart[]` the browser built (`UI/src/lib/chat/types.ts`).

**The order matters and is copied deliberately.** The browser puts attachments
above the question, because that is where they sit in the composer. A server that
appended them below would redraw yesterday's conversation in a different order
from the one the user watched being written -- the kind of difference nobody
reports as a bug and everybody notices.

Two hard rules, both inherited from the frontend type that documents them:

- **Never bytes.** A page capture contributes its label and nothing else. The
  request carries the base64; the transcript carries "Sayfa görüntüsü".
- **Never a secret.** `attachmentId` is an owner-bound handle that the browser
  already holds and needs back, so a file mentioned in turn 3 still resolves in
  turn 9. It is not model content and it expires.
"""

from typing import Iterable, Sequence

#: The literal used for a page capture and for a client tool's result note.
#: `"capture"` is a `ContextKind` extension on the frontend union, not one of the
#: seven real kinds, which is why it is spelled out here rather than derived.
CAPTURE = "capture"


def format_location(location) -> str:
    """The breadcrumb under an attached-context chip.

    A print of `ContextLocation`, and it has to match
    `UI/src/lib/chat/page-locator.ts::formatLocation` character for character --
    the same trail with the same separator -- or a restored turn's chips would
    read differently from the ones the user attached.

    Printed rather than stored structured for the same reason the frontend prints
    it: this string is only ever shown to a person. The structured original went
    to the model on the request that created the turn and is not needed again.
    """
    trail = [
        location.page or location.path,
        location.table or location.section,
        f"row “{location.row}”" if location.row else None,
        f"column “{location.column}”" if location.column else None,
    ]
    return " › ".join(piece for piece in trail if piece)


def web_citations(citations: Iterable) -> list[dict]:
    """Citations in the shape the transcript renders, not the shape the API sends.

    `ChatMessage.citations` stores the retrieved-chunk contract verbatim --
    `cite_url`, `source_type`, `score`, the chunk text. The renderer reads a much
    smaller thing (`WebCitation`: `url`, `title`, `bank`, `sourceType`), and the
    live path narrows it on the way through `UI/src/lib/chat/transport.ts`.

    A stored part has no transport to pass through, so the narrowing happens
    here. Storing the wide shape instead was the first version of this and it was
    a silent break: `sourceGroup(undefined, undefined)` still classifies as
    "online", so a restored answer rendered a sources block whose every entry had
    no link -- present, plausible, and dead.

    Dropping an entry with no `cite_url` is also copied from the transport: a
    citation with nothing to open is not a source.
    """
    narrowed: list[dict] = []
    for citation in citations:
        item = citation if isinstance(citation, dict) else citation.model_dump()
        url = item.get("cite_url") or ""
        if not url:
            continue
        source = {"url": url}
        # Omitted rather than sent null, which is what the transport does with
        # `|| undefined` -- the optional fields are absent, not empty.
        for stored, rendered in (
            ("title", "title"), ("bank", "bank"), ("source_type", "sourceType"),
        ):
            if item.get(stored):
                source[rendered] = item[stored]
        narrowed.append(source)
    return narrowed


def user_parts(
    question: str,
    contexts: Sequence = (),
    captures: Sequence = (),
    attachments: Iterable = (),
) -> list[dict]:
    """The parts of one user turn, in the order the composer showed them.

    `attachments` are the *resolved* records from `chat_attachments`, so the
    filename and page count are the real ones rather than whatever the client
    claimed.

    An empty question contributes no text part rather than an empty one: "here is
    this table" with nothing typed is a real message, and a blank text part draws
    an empty bubble above the attachment that is the whole message.
    """
    parts: list[dict] = []

    for context in contexts:
        part = {
            "type": "context",
            "kind": context.kind,
            "label": context.label,
            "source": format_location(context.location),
        }
        # Present-but-empty and absent are different to the renderer: `body` is
        # what makes a chip expandable, and an empty string would offer to expand
        # into nothing.
        if context.body:
            part["body"] = context.body
        parts.append(part)

    for capture in captures:
        # The label only. See the module docstring: the bytes are on the request
        # and nowhere else.
        parts.append({"type": "context", "kind": CAPTURE, "label": capture.label})

    for attachment in attachments:
        part = {
            "type": "attachment",
            "filename": attachment.filename,
            "kind": attachment.kind,
            "attachmentId": attachment.id,
        }
        # Page count comes off the resolved record's images rather than from a
        # field, because `ResolvedAttachment` has no `page_count` -- a document's
        # pages *are* its images. Only for documents: an attached JPG has one
        # image and zero pages, and labelling it "1 sayfa" would be a lie the
        # composer never told.
        if attachment.kind == "document" and attachment.images:
            part["pageCount"] = len(attachment.images)
        parts.append(part)

    if question.strip():
        parts.append({"type": "text", "text": question})

    return parts


def assistant_parts(
    text: str, citations: Sequence[dict] = (), tool_results: Sequence = ()
) -> list[dict]:
    """The parts of one answer: what it did, what it said, what it cited.

    `tool_results` are the answers to `look_at_page` calls the model made on the
    *previous* pass. They belong on this turn because this is the pass that
    produced the answer -- the pass that asked for the tool persists nothing, by
    design, since any prose before the request is a preamble and not a reply.

    Only the label travels, exactly as the browser does it. A tool result can be
    a whole page capture, and a transcript is not where that belongs.
    """
    parts: list[dict] = [
        {"type": "context", "kind": CAPTURE, "label": result.label}
        for result in tool_results
        if getattr(result, "label", "")
    ]
    parts.append({"type": "text", "text": text})
    sources = web_citations(citations)
    if sources:
        parts.append({"type": "citations", "sources": sources})
    return parts


def parts_or_text(message) -> list[dict]:
    """A stored message's parts, or a rebuild for a row written before they existed.

    Every `chat_messages` row that predates the `parts` column has `[]`, and the
    66 conversations already in the table are all of them. Falling back to a
    single text part from `content` makes those readable in every browser
    immediately, without a backfill that would have to invent which attachments a
    turn from last week carried.

    The fallback is lossy in a visible, honest way: an old turn shows the bracketed
    note `content` already carries -- `[ekli tablo: ...]` -- instead of a chip. The
    alternative was showing nothing.

    An empty *assistant* message is not rendered as an empty bubble, so a row with
    neither parts nor content yields no parts at all.
    """
    if message.parts:
        return list(message.parts)
    if not message.content:
        return []
    if message.role == "assistant" and message.citations:
        return assistant_parts(message.content, message.citations)
    return [{"type": "text", "text": message.content}]
