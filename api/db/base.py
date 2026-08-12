"""The declarative base every table inherits, and the id/timestamp columns.

Primary keys are UUIDs rather than sequential integers. A dashboard puts ids in
URLs, and a sequential id tells any visitor how many users the system has and
lets them try the neighbouring one.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware UTC now. `datetime.utcnow()` returns a naive value that
    compares wrongly against the aware columns below."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base for every table."""


class UUIDMixin:
    """A UUID primary key, generated in Python so the object has its id before
    the flush -- which lets related rows be built in one unit of work."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Created/updated stamps, written by the database clock.

    `server_default=func.now()` rather than a Python default: the application
    and the database can disagree about the time, and rows ordered by a client
    clock come back out of order.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
