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

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..agent import CLIENT_TOOLS, answer
from ..chat_attachments import AttachmentError, prepare_attachment, resolve_attachments
from agents.table_metadata import generate_table_metadata
from agents.recommendation import generate_recommendation
from agents.shared.checkpoints import delete_session_checkpoints
from ..db.models import ChatMessage, ChatSession
from ..db.session import session_scope
from ..deps import CurrentUser, DbSession
from ..schemas.chat import (
    AskRequest, ChatMessageOut, ChatSessionDetail, ChatSessionOut, CompactionResult,
    ContextLevelOut, StreamEvent, TableMetadataOut, TableMetadataRequest,
    PreparedAttachmentOut, RecommendationOut, RecommendationRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

TITLE_CHARS = 60
SSE_HEARTBEAT_SECONDS = 15.0
_T = TypeVar("_T")
_STREAM_FINISHED = object()


@router.post("/attachments", response_model=PreparedAttachmentOut)
def upload_chat_attachment(file: UploadFile, user: CurrentUser) -> PreparedAttachmentOut:
    """Prepare one file without exposing document page images to the browser."""
    try:
        prepared = prepare_attachment(file.file, file.filename, user.id)
    except AttachmentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return PreparedAttachmentOut.model_validate(prepared)


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


@router.post(
    "/sessions/{session_id}/recommendation", response_model=RecommendationOut
)
def create_recommendation(
    session_id: uuid.UUID,
    body: RecommendationRequest,
    user: CurrentUser,
    session: DbSession,
) -> RecommendationOut:
    """Generate the next composer message from this conversation's private agent."""
    chat = _own_session(session, user, session_id)
    try:
        result = generate_recommendation(
            [(str(message.id), message.role, message.content) for message in chat.messages],
            session_id=str(session_id),
            locale=body.locale,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:
        logger.exception("Recommendation failed for %s", session_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A conversation recommendation is temporarily unavailable.",
        ) from exc
    return RecommendationOut(text=result.text)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_session(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> None:
    chat = _own_session(session, user, session_id)
    delete_session_checkpoints(str(session_id))
    session.delete(chat)
    session.commit()


def _thread_of(session_id: uuid.UUID):
    """The supervisor's compaction, its agent, and this conversation's thread.

    The middleware is the same one the supervisor runs with, so the number
    reported here is the number that fires compaction -- not a second estimate
    that happens to be close.
    """
    from agents.main.agent import build_main_agent, main_compaction, main_thread_id

    compaction, window = main_compaction()
    agent = build_main_agent()
    config = {"configurable": {"thread_id": main_thread_id(str(session_id))}}
    return compaction, window, agent, config


def _level_out(compaction, window, agent, config) -> ContextLevelOut:
    """Read the thread as it stands now."""
    from agents.shared.compaction import measure

    state = agent.get_state(config)
    messages = list((state.values or {}).get("messages") or [])
    level = measure(compaction, messages, window)
    return ContextLevelOut(
        used_tokens=level.used_tokens,
        usable_tokens=level.usable_tokens,
        fraction=level.fraction,
        compact_at_tokens=level.compact_at_tokens,
        tokens_until_compaction=level.tokens_until_compaction,
        keep_messages=level.keep_messages,
        message_count=level.message_count,
    )


@router.get("/sessions/{session_id}/context", response_model=ContextLevelOut)
def get_context_level(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ContextLevelOut:
    """How full this conversation is, and how far it is from compacting.

    Only the supervisor's thread. Each bank specialist has its own, compacted the
    same way, but they are private working memory rather than the conversation
    and nothing asks after them.
    """
    _own_session(session, user, session_id)
    try:
        return _level_out(*_thread_of(session_id))
    except Exception as exc:
        logger.exception("Could not read the context level for %s", session_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The context level is unavailable.",
        ) from exc


@router.post("/sessions/{session_id}/compact", response_model=CompactionResult)
def compact_session(
    session_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> CompactionResult:
    """Summarise this conversation now, without waiting for the threshold.

    The stored transcript is untouched: this rewrites the agent's thread, not
    `chat_messages`, so the user keeps every message they can scroll back to
    while the model continues from the summary.
    """
    _own_session(session, user, session_id)
    try:
        # Built once and measured twice. Rebuilding for the second reading would
        # compile a second supervisor -- ten specialist tools and their bank
        # clients -- to count tokens it already has the means to count.
        compaction, window, agent, config = _thread_of(session_id)
        update = compaction.compact_now(agent.get_state(config).values)
        if update is not None:
            agent.update_state(config, update)
        # The recommendation agent owns an independent checkpoint thread, but
        # the user's compact action applies to the conversation as a whole.
        from agents.recommendation import (
            build_recommendation_agent,
            recommendation_compaction,
            recommendation_thread_id,
        )

        recommendation_middleware, _ = recommendation_compaction()
        recommendation_agent = build_recommendation_agent()
        recommendation_config = {
            "configurable": {
                "thread_id": recommendation_thread_id(str(session_id))
            }
        }
        recommendation_state = recommendation_agent.get_state(recommendation_config)
        if (recommendation_state.values or {}).get("messages"):
            recommendation_update = recommendation_middleware.compact_now(
                recommendation_state.values
            )
            if recommendation_update is not None:
                recommendation_agent.update_state(
                    recommendation_config, recommendation_update
                )
        level = _level_out(compaction, window, agent, config)
    except Exception as exc:
        logger.exception("Compaction failed for %s", session_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The conversation could not be compacted.",
        ) from exc
    return CompactionResult(compacted=update is not None, context=level)


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
    try:
        prepared_attachments = resolve_attachments(
            [attachment.id for attachment in body.attachments], user.id
        )
    except AttachmentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

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
    logger.info(
        "chat_turn accepted session=%s web_search_enabled=%s model=%s",
        chat_id,
        body.web_search,
        body.model or "default",
    )

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
    if prepared_attachments:
        labels = ", ".join(item.filename for item in prepared_attachments)
        stored = f"{stored}\n\n[ekli dosya: {labels}]".strip()
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
                    web_search=body.web_search,
                    user_id=user_id,
                    session_id=chat_id,
                    attachments=prepared_attachments,
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
