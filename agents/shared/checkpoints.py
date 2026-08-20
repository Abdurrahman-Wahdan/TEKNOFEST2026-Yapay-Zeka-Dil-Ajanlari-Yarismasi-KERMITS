"""Process-wide PostgreSQL checkpointer for private LangGraph state."""

from __future__ import annotations

from contextlib import AbstractContextManager
from threading import Lock

from langgraph.checkpoint.postgres import PostgresSaver

from config.settings import settings
from .registry import SPECS

_lock = Lock()
_context: AbstractContextManager[PostgresSaver] | None = None
_checkpointer: PostgresSaver | None = None


def checkpoint_database_url() -> str:
    """Translate SQLAlchemy's psycopg URL to the driver's native URL."""
    return settings.API_DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def get_checkpointer() -> PostgresSaver:
    """Return one initialized saver shared by all compiled agents."""
    global _context, _checkpointer
    with _lock:
        if _checkpointer is None:
            _context = PostgresSaver.from_conn_string(checkpoint_database_url())
            _checkpointer = _context.__enter__()
            _checkpointer.setup()
        return _checkpointer


def close_checkpointer() -> None:
    """Close the driver connection during FastAPI shutdown."""
    global _context, _checkpointer
    with _lock:
        if _context is not None:
            _context.__exit__(None, None, None)
        _context = None
        _checkpointer = None


def delete_session_checkpoints(session_id: str) -> None:
    """Remove the supervisor and every private bank memory for one chat."""
    checkpointer = get_checkpointer()
    checkpointer.delete_thread(f"{session_id}:main")
    for spec in SPECS:
        checkpointer.delete_thread(f"{session_id}:bank:{spec.bank}")
