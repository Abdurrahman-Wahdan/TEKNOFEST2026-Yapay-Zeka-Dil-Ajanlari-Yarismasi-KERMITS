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
import queue
import threading
import uuid
from collections.abc import Callable, Iterator
from typing import TypeVar

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..agent import CLIENT_TOOLS, answer
from agents.table_metadata import generate_table_metadata
from agents.shared.checkpoints import delete_session_checkpoints
from ..db.models import ChatMessage, ChatSession
from ..db.session import session_scope
from ..deps import CurrentUser, DbSession
from ..schemas.chat import (
    AskRequest, ChatMessageOut, ChatSessionDetail, ChatSessionOut, StreamEvent,
    TableMetadataOut, TableMetadataRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

TITLE_CHARS = 60
SSE_HEARTBEAT_SECONDS = 15.0
_T = TypeVar("_T")
_STREAM_FINISHED = object()


def _with_heartbeats(
    producer: Callable[[], Iterator[_T]],
    interval: float = SSE_HEARTBEAT_SECONDS,
) -> Iterator[_T | None]:
    """Run a blocking producer while keeping its HTTP stream alive.

    LangGraph can spend minutes waiting for Gemma or several bank specialists
    without yielding an application event. A valid SSE comment during that
    silence keeps Next.js, reverse proxies, and the browser reader from treating
    the connection as dead. The worker also keeps the blocking agent execution
    off the response iterator, which is what lets that iterator send heartbeats.
    """
    items: queue.Queue[object] = queue.Queue()

    def run() -> None:
        try:
            for item in producer():
                items.put(item)
        except Exception as exc:
            items.put(exc)
        finally:
            items.put(_STREAM_FINISHED)

    threading.Thread(target=run, name="tf26-chat-stream", daemon=True).start()
    while True:
        try:
            item = items.get(timeout=interval)
        except queue.Empty:
            yield None
            continue
        if item is _STREAM_FINISHED:
            return
        if isinstance(item, Exception):
            raise item
        yield item  # type: ignore[misc]


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
    delete_session_checkpoints(str(session_id))
    session.delete(chat)
    session.commit()


@router.post("/table-metadata", response_model=TableMetadataOut)
def create_table_metadata(
    body: TableMetadataRequest, user: CurrentUser, session: DbSession
) -> TableMetadataOut:
    """Give one kept table a durable title and future-chat handoff."""
    _own_session(session, user, body.session_id)
    try:
        result = generate_table_metadata(
            [(message.role, message.content) for message in body.conversation],
            body.table.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.exception(
            "Table metadata generation failed session=%s user=%s",
            body.session_id,
            user.id,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not create context for this table. Please try again.",
        ) from exc
    return TableMetadataOut(
        title=result.title,
        description=result.description,
    )


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
    # The whole conversation, oldest first. Deliberately unwindowed: there was a
    # `.limit(API_CHAT_HISTORY_TURNS)` here, which meant that from turn 13 onward the
    # agent silently stopped seeing the start of the thread -- so a table attached in
    # turn 1 was forgotten, and it asked about it again. Nothing sent to the model is
    # truncated anywhere in this app; if a thread ever outgrows the context window,
    # failing loudly beats answering from half the conversation.
    earlier = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    ).all()
    history = [
        ("human" if m.role == "user" else "ai", m.content) for m in earlier
    ]

    # The question as typed, plus a note of what travelled with it. The bytes of a
    # capture are deliberately not persisted: `content` is a Text column and would
    # take them, but a 100kB base64 string per turn bloats the table for something
    # only the turn it belongs to can use. The consequence is honest and worth
    # knowing: replayed history carries the note, not the picture.
    stored = body.question
    if body.captures:
        shots = ", ".join(f"{c.width}x{c.height}" for c in body.captures)
        stored = f"{stored}\n\n[ekran görüntüsü: {shots}]".strip()
    if body.context:
        labels = ", ".join(c.label for c in body.context if c.label)
        stored = f"{stored}\n\n[ekli bağlam: {labels}]".strip()
    session.add(ChatMessage(session_id=chat_id, role="user", content=stored))
    session.commit()

    def frames() -> Iterator[str]:
        """The SSE body. Assembles the answer as it streams, then persists it."""
        parts: list[str] = []
        citations: list[dict] = []
        failed = False
        # Set when the model asked the client to look at the page. The turn is not
        # over: the client runs the tool and asks again, and the answer is written
        # on that pass.
        awaiting_tool = False

        try:
            def produce_events():
                return answer(
                    body.question,
                    history,
                    body.context,
                    body.captures,
                    body.tool_results,
                    body.client_tools,
                    think=body.think,
                    model=body.model,
                    user_id=user_id,
                    session_id=chat_id,
                )

            for event in _with_heartbeats(produce_events):
                if event is None:
                    # SSE comments are deliberately invisible to askStream's
                    # application-event parser but reset every idle timer in the
                    # HTTP path.
                    yield ": keep-alive\n\n"
                    continue
                if event.type == "token" and event.text:
                    parts.append(event.text)
                elif event.type == "citation" and event.citation is not None:
                    citations.append(event.citation.model_dump(mode="json"))
                elif event.type == "tool_call" and event.tool_name in CLIENT_TOOLS:
                    # Only a *client* tool suspends the turn. A server-side tool
                    # (`save_table`) already ran in-process and the answer
                    # continues in this same response, so treating its frame as a
                    # suspension would discard the whole answer. The check is
                    # correct by construction today -- a server tool emits no
                    # `tool_call` frame -- and explicit anyway, because "by
                    # construction" is one careless frame away from silently
                    # dropping every reply.
                    awaiting_tool = True
                elif event.type == "error":
                    failed = True
                yield f"data: {event.model_dump_json(exclude_none=True)}\n\n"
        except Exception:
            logger.exception("Chat stream failed for user %s", user_id)
            failed = True
            yield "data: " + json.dumps(
                {"type": "error", "detail": "The assistant failed to answer."}
            ) + "\n\n"

        if awaiting_tool:
            # Deliberately no `done` frame and nothing persisted. Any prose the
            # model wrote before asking for the tool is a preamble, not an answer --
            # storing it would keep half a reply as though it were complete and
            # leave two assistant messages for one question.
            return

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
