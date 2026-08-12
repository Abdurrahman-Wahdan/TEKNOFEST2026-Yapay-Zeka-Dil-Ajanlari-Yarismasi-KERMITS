"""The chatbot: sessions, history, and the streamed answer.

The answer streams over SSE. The agent retrieves, may call several bank
endpoints, then writes -- ten seconds of silence followed by a wall of text
reads as a hang, and the retrieval step is worth watching.

SSE rather than WebSockets: this is one-directional, and SSE reconnects on its
own, passes through proxies as plain HTTP, and needs no protocol upgrade. A
WebSocket would add a second transport to operate for no capability gained.
"""

import json
import logging
import uuid
from typing import Iterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from config.settings import settings

from ..agent import answer
from ..db.models import ChatMessage, ChatSession
from ..db.session import session_scope
from ..deps import CurrentUser, DbSession
from ..schemas.chat import (
    AskRequest, ChatMessageOut, ChatSessionDetail, ChatSessionOut, StreamEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

TITLE_CHARS = 60


def _own_session(session, user, session_id: uuid.UUID) -> ChatSession:
    """A chat session belonging to this user, or 404.

    404 rather than 403 for someone else's session: 403 would confirm the id
    exists, which is enough to enumerate other users' conversations.
    """
    chat = session.get(ChatSession, session_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conversation.")
    return chat


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(user: CurrentUser, session: DbSession) -> list[ChatSessionOut]:
    """This user's conversations, most recent first."""
    rows = session.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
    ).all()
    return [ChatSessionOut.model_validate(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_chat_session(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ChatSessionDetail:
    """One conversation with all of its turns."""
    chat = _own_session(session, user, session_id)
    return ChatSessionDetail(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        messages=[ChatMessageOut.model_validate(m) for m in chat.messages],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_session(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> None:
    chat = _own_session(session, user, session_id)
    session.delete(chat)
    session.commit()


@router.post(
    "/ask",
    response_model=StreamEvent,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def ask(body: AskRequest, user: CurrentUser, session: DbSession) -> StreamingResponse:
    """Ask a question. Returns an SSE stream of `StreamEvent` frames.

    `response_model=StreamEvent` is a documentation device -- the endpoint
    returns a stream, not that object. Declaring it is what puts the frame shape
    into the OpenAPI schema, so the frontend's event types are generated like
    everything else instead of being the one hand-written surface in the app.
    """
    if body.session_id is not None:
        chat = _own_session(session, user, body.session_id)
    else:
        chat = ChatSession(
            user_id=user.id, title=body.question[:TITLE_CHARS].strip()
        )
        session.add(chat)
        session.commit()

    chat_id = chat.id
    user_id = user.id

    # Read the history now, inside the request's session. The generator below
    # runs after this function returns and after that session is closed, so
    # anything it needs must be plain values by then, not ORM instances.
    recent = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(settings.API_CHAT_HISTORY_TURNS)
    ).all()
    history = [
        ("human" if m.role == "user" else "ai", m.content) for m in reversed(recent)
    ]

    session.add(ChatMessage(session_id=chat_id, role="user", content=body.question))
    session.commit()

    def frames() -> Iterator[str]:
        """The SSE body. Assembles the answer as it streams, then persists it."""
        parts: list[str] = []
        citations: list[dict] = []
        failed = False

        try:
            for event in answer(body.question, history):
                if event.type == "token" and event.text:
                    parts.append(event.text)
                elif event.type == "citation" and event.citation is not None:
                    citations.append(event.citation.model_dump(mode="json"))
                elif event.type == "error":
                    failed = True
                yield f"data: {event.model_dump_json(exclude_none=True)}\n\n"
        except Exception:
            logger.exception("Chat stream failed for user %s", user_id)
            failed = True
            yield "data: " + json.dumps(
                {"type": "error", "detail": "The assistant failed to answer."}
            ) + "\n\n"

        if failed or not parts:
            # Nothing usable to keep. The user's question stays in the history
            # so the conversation reads correctly; a half-written answer that
            # errored would be replayed later as if it were complete.
            return

        # Its own session: the request's was closed when `ask` returned.
        with session_scope() as store:
            message = ChatMessage(
                session_id=chat_id,
                role="assistant",
                content="".join(parts),
                citations=citations,
            )
            store.add(message)
            store.flush()
            done = StreamEvent(
                type="done", message_id=message.id, session_id=chat_id
            )
        yield f"data: {done.model_dump_json(exclude_none=True)}\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx not to buffer the stream. Without it a proxy holds
            # every frame until the response finishes, which is precisely the
            # wall-of-text-after-silence that streaming exists to avoid.
            "X-Accel-Buffering": "no",
        },
    )
