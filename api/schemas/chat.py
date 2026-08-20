"""The chatbot: sessions, turns, and the shape of a streamed answer.

The answer streams over SSE rather than arriving as one JSON body. The agent
retrieves, may call several bank endpoints, and then writes -- ten seconds of
silence followed by a wall of text reads as a hang, and the retrieval step is
worth showing while it happens.

Each SSE frame is one JSON object with a `type`:

    {"type": "status",   "stage": "retrieving"}   what the agent is doing
    {"type": "token",    "text": "Kuveyt"}        a piece of the answer
    {"type": "citation", "citation": {...}}       a source, as it is used
    {"type": "done",     "message_id": "..."}     finished and persisted
    {"type": "error",    "detail": "..."}         failed; the stream ends

`StreamEvent` below models exactly those frames so the frontend's event types
are generated rather than hand-written -- SSE payloads are otherwise invisible
to OpenAPI and would be the one untyped surface in the app.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .banks import ChunkOut


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[ChunkOut] = Field(default_factory=list)
    created_at: datetime


class ChatSessionDetail(ChatSessionOut):
    messages: list[ChatMessageOut] = Field(default_factory=list)


class ToolResult(BaseModel):
    """What the browser sent back after looking at the page.

    One tool, `look_at_page`, taking a mode: `text` for the semantic outline,
    `image` for a screenshot, `both` for one round trip carrying each. Two separate
    tools made the agent commit before it knew which it needed, then cost a second
    exchange whenever it guessed wrong.

    `text` and `image` are populated according to the mode asked for, so a `both`
    call comes back with each. `text` is finished markdown and belongs in a text
    block; `image` is base64 and must become an image content block -- see
    `_human_content` in `api/agent.py`.
    """

    id: str = ""
    name: Literal["look_at_page"] = "look_at_page"
    text: str | None = None
    image: "CapturePayload | None" = None
    label: str = ""


class ContextLocation(BaseModel):
    """Where on the page a piece of attached context came from."""

    path: str = ""
    page: str | None = None
    section: str | None = None
    table: str | None = None
    about: str | None = None
    row: str | None = None
    column: str | None = None
    kind: str | None = None


class AttachedContext(BaseModel):
    """A piece of the UI the user handed to the agent.

    Serialised by the browser at the moment of the click -- a table as a GFM
    table, a row as a key/value list, a selection as its text -- because what the
    user pointed at is what should travel, not whatever the page looks like by the
    time they press send.
    """

    id: str = ""
    kind: str = "table"
    label: str = ""
    body: str = ""
    format: str = "markdown"
    location: ContextLocation = Field(default_factory=ContextLocation)
    count: int | None = None


class CapturePayload(BaseModel):
    """A screenshot of the page, ready to become an image content block.

    `media_type` and `data` arrive separately rather than as a `data:` URL: the
    browser splits them so nothing here has to parse a URL, and so this can be
    handed to the model as an image rather than accidentally as text. Forwarding
    base64 as text shows the model a wall of characters, answers confidently from
    nothing, and bills for every token of it.
    """

    id: str = ""
    label: str = ""
    media_type: str = Field(default="image/webp", alias="mediaType")
    data: str = ""
    width: int = 0
    height: int = 0

    model_config = ConfigDict(populate_by_name=True)


class AskRequest(BaseModel):
    """A question. `session_id` omitted starts a new conversation."""

    question: str = Field(
        default="",
        description=(
            "Deliberately unbounded. There was a max_length of 4000 here and it "
            "was wrong: the UI can attach a whole table, a quoted row, or a page "
            "snapshot to a question, and those run to tens of thousands of "
            "characters. Capping the field truncates what the agent is asked "
            "about, so it answers from part of the data or asks a follow-up the "
            "attachment existed to prevent. `ChatMessage.content` is a `Text` "
            "column, so nothing narrows downstream either. If a payload ever "
            "exceeds the model's window, failing loudly is the safer outcome."
        ),
    )
    session_id: uuid.UUID | None = None

    context: list[AttachedContext] = Field(
        default_factory=list,
        description="Pieces of the UI the user attached: a table, a row, a quote.",
    )
    client_tools: list[Literal["look_at_page"]] = Field(
        default_factory=list,
        alias="clientTools",
        description=(
            "Tools this client can execute. The tool is only offered to the model "
            "when the caller says it can run it -- a plain API consumer has no page "
            "to look at, and asking it to would strand the exchange waiting for a "
            "result that can never come."
        ),
    )
    tool_results: list[ToolResult] = Field(
        default_factory=list,
        alias="toolResults",
        description=(
            "Answers to `look_at_page` calls the agent made on a previous pass. "
            "Scoped to the exchange that asked for them, which is why they are "
            "here and not on `messages`."
        ),
    )
    captures: list[CapturePayload] = Field(
        default_factory=list,
        description=(
            "Screenshots of the page. Passed to the model as image content "
            "blocks -- Gemma 4 takes image input, and screen/UI understanding is "
            "one of its stated vision capabilities."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def _needs_something(self) -> "AskRequest":
        """A turn has to carry *something*, but it does not have to be text.

        "Here is this table" followed by a look is a real way to use this, so an
        empty question with an attachment is valid; empty everything is not.
        """
        if not self.question.strip() and not self.context and not self.captures:
            raise ValueError("Ask a question or attach something.")
        return self


class StreamEvent(BaseModel):
    """One SSE frame. Only the fields belonging to `type` are populated.

    Declared as a response model on the streaming endpoint purely so it lands in
    the OpenAPI schema; the endpoint returns a stream, not this object.
    """

    type: Literal[
        "status", "token", "citation", "tool_call", "saved_view", "done", "error"
    ]
    stage: str | None = Field(
        default=None,
        description="status only: retrieving | pricing | writing.",
    )
    text: str | None = Field(default=None, description="token only.")
    citation: ChunkOut | None = Field(default=None, description="citation only.")
    message_id: uuid.UUID | None = Field(default=None, description="done only.")
    session_id: uuid.UUID | None = Field(default=None, description="done only.")
    detail: str | None = Field(default=None, description="error only.")

    # --- tool_call only ---------------------------------------------------
    #
    # The model asking the *browser* to do something only the browser can: look at
    # the page the user is on. The stream ends after this frame; the client runs
    # the tool and asks again with `tool_results`, and the answer continues there.
    tool_call_id: str | None = Field(
        default=None, description="tool_call only: echo back on the result."
    )
    tool_name: Literal["look_at_page"] | None = Field(
        default=None, description="tool_call only."
    )
    mode: Literal["text", "image", "both"] | None = Field(
        default=None,
        description=(
            "tool_call only: how the model wants to see the page. `text` for the "
            "semantic outline (exact figures, current filters), `image` for a "
            "screenshot (layout), `both` for one round trip carrying each."
        ),
    )

    # --- saved_view only --------------------------------------------------
    #
    # A table the agent wrote to the user's AI Overview page. Unlike `tool_call`
    # this does not end the stream -- the write already happened server-side and
    # the answer continues -- so the UI can say so and link to the page without
    # waiting for `done` or re-fetching `/me/views`.
    view_slug: str | None = Field(
        default=None, description="saved_view only: identifies the saved table."
    )
    view_title: str | None = Field(
        default=None, description="saved_view only: the table's heading, as stored."
    )
