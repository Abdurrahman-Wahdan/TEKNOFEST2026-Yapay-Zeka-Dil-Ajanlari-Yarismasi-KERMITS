"""Postgres: users, their profiles, their saved views and their chat history.

Deliberately the only place in the project that stores anything about a person.
Qdrant holds public bank content and nothing else, so a user's question is never
embedded and never persisted next to the corpus.
"""

from .base import Base
from .session import engine, get_session, session_scope

__all__ = ["Base", "engine", "get_session", "session_scope"]
