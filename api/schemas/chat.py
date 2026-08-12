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

from pydantic import BaseModel, ConfigDict, Field

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


class AskRequest(BaseModel):
    """A question. `session_id` omitted starts a new conversation."""

    question: str = Field(min_length=1, max_length=4000)
    session_id: uuid.UUID | None = None


class StreamEvent(BaseModel):
    """One SSE frame. Only the fields belonging to `type` are populated.

    Declared as a response model on the streaming endpoint purely so it lands in
    the OpenAPI schema; the endpoint returns a stream, not this object.
    """

    type: Literal["status", "token", "citation", "done", "error"]
    stage: str | None = Field(
        default=None,
        description="status only: retrieving | pricing | writing.",
    )
    text: str | None = Field(default=None, description="token only.")
    citation: ChunkOut | None = Field(default=None, description="citation only.")
    message_id: uuid.UUID | None = Field(default=None, description="done only.")
    session_id: uuid.UUID | None = Field(default=None, description="done only.")
    detail: str | None = Field(default=None, description="error only.")
