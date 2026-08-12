"""One engine for the process, one session per request.

The engine is created at import and pooled; sessions are cheap and must not be
shared across requests, because a session holds an open transaction and two
requests sharing one would see each other's uncommitted rows.
"""

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.API_DATABASE_URL,
    # Verify a pooled connection before handing it out. Without this, a
    # connection the database closed overnight is dealt to the first morning
    # request, which fails for no reason the caller can act on.
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    # Attributes stay readable after commit. Without this a router that commits
    # and then returns the object triggers a refresh on a closed session.
    expire_on_commit=False,
)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a session for the life of one request.

    Rollback on exception is explicit rather than left to the pool: a session
    returned to the pool mid-transaction is a bug that surfaces in a later,
    unrelated request.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """A committing session for code outside a request -- scripts and jobs."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
