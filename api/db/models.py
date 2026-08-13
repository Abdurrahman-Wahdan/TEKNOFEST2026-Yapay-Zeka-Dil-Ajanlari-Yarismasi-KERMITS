"""The tables. Five of them, and no more than the dashboard actually needs.

    User            an account
    Profile         what the onboarding produced: which banks and products
                    this user cares about, and the amounts they think in
    SavedView       a dashboard configuration the user (or the AI Overview
                    page) composed, stored as a component list
    ChatSession     one conversation
    ChatMessage     one turn in it, with the citations that backed the answer

Nothing here caches bank data. Rates and campaigns come from `banks/` and the
Qdrant index at request time; a cached quote in Postgres would be a second,
staler source of truth for the one thing that must never be stale.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

# A chat turn's author. Assistant messages are the only ones carrying citations.
ROLES = ("user", "assistant")


class User(UUIDMixin, TimestampMixin, Base):
    """An account.

    `email_normalised` exists because Turkish lowercasing is not ASCII
    lowercasing: 'I'.lower() is 'ı' in Turkish locales, so 'IREM@x.com' and
    'irem@x.com' can be folded to two different strings depending on where the
    folding happens. The column stores one canonical form, computed once in
    `api/security.py`, and the unique index is on that -- not on `email`.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalised: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    locale: Mapped[str] = mapped_column(String(5), nullable=False, default="tr")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    saved_views: Mapped[list["SavedView"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Profile(UUIDMixin, TimestampMixin, Base):
    """What the onboarding interview concluded about this user.

    Every field is optional and every list may be empty: a user who skipped
    onboarding is a normal user who sees the unfiltered dashboard, not a broken
    one. Nothing downstream may assume a profile exists.

    Bank and family names are stored as the keys `banks.list_banks()` and
    `banks.families` already use, so a preference is a filter argument with no
    translation step -- and an unknown key is caught at write time by the
    router, not discovered later as an empty dashboard.
    """

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # "customer" (comparing offers for themselves) or "analyst" (watching the
    # market). The two want opposite defaults: one amount, versus every bank.
    persona: Mapped[str] = mapped_column(String(32), nullable=False, default="customer")
    banks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    families: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The amount and term this user thinks in, used to prefill comparisons.
    typical_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    typical_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The raw interview transcript, kept so the agent can revisit its reasoning
    # and so a changed question set can be re-derived without re-interviewing.
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="profile")


class SavedView(UUIDMixin, TimestampMixin, Base):
    """A dashboard the user kept: which components, in what order, with what props.

    `components` is a list of `{"type": ..., "props": {...}}`. The type must
    name a component in the frontend's catalog; an unknown type renders as a
    visible "unsupported component" tile rather than crashing the page, because
    this JSON can be written by the AI Overview page and a model will eventually
    emit something the frontend does not have.
    """

    __tablename__ = "saved_views"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_saved_views_user_slug"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    components: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # True when the AI Overview page composed it rather than the user.
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="saved_views")


class ChatSession(UUIDMixin, TimestampMixin, Base):
    """One conversation."""

    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Derived from the first question, so the sidebar is readable without the
    # user naming anything.
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    user: Mapped[User] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(UUIDMixin, TimestampMixin, Base):
    """One turn.

    `citations` is the list of `cite_url`s the retrieved chunks carried, stored
    with the answer rather than recomputed. The index changes nightly, so
    re-running the retrieval later would produce different sources than the ones
    the user was actually shown -- which is exactly the kind of quiet drift a
    bank-facing tool cannot have.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
