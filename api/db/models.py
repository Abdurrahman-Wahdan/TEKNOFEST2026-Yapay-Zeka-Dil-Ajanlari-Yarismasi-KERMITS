"""The tables. Eight of them, and no more than the dashboard actually needs.

    User              an account
    Profile           what the onboarding produced: which banks and products
                      this user cares about, and the amounts they think in
    SavedView         a dashboard configuration the user (or the AI Overview
                      page) composed, stored as a component list
    ChatSession       one conversation
    ChatMessage       one turn in it, with the citations that backed the answer
    TableOverview     what the overview agent said about one offline comparison
                      table, kept so the model is asked once and not per visit
    Automation        a recurring question the user asked the assistant to run
                      for them, and the wall-clock time it runs at
    AutomationReport  one answer it produced, unread until they open it

Nothing here caches bank data. Rates and campaigns come from `banks/` and the
Qdrant index at request time; a cached quote in Postgres would be a second,
staler source of truth for the one thing that must never be stale.
`TableOverview` is not an exception: it describes the *offline* pool
(`data/_tables/`), which changes only when the producer reruns, and it stores a
hash of the exact table it read so a stale summary is detected, not served.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text,
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
    automations: Mapped[list["Automation"]] = relationship(
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
    title: Mapped[str] = mapped_column(Text, nullable=False)
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
    #: The turn as the *reader* saw it: `UI/src/lib/chat/types.ts::MessagePart[]`.
    #:
    #: `content` is the same turn as the *model* reads it, and it stays the
    #: authority for replay -- `api/routers/chat.py` builds the agent's history
    #: from it. This is what the browser draws, and it exists because a turn on
    #: screen is not a string: it is the question plus the table that was
    #: attached to it plus the citations under the answer. Both are written from
    #: one request so they cannot disagree.
    #:
    #: `[]` means a row written before this column existed. The API rebuilds a
    #: single text part from `content` for those rather than dropping them.
    #:
    #: Never bytes. A page capture contributes its label and size, never its
    #: pixels -- the frontend type documents the same rule for the same reason.
    parts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
    feedback: Mapped["ChatFeedback | None"] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ChatFeedback(UUIDMixin, TimestampMixin, Base):
    """The user's current verdict and note for one assistant answer."""

    __tablename__ = "chat_feedback"
    __table_args__ = (
        CheckConstraint("rating IN ('up', 'down')", name="ck_chat_feedback_rating"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)

    message: Mapped[ChatMessage] = relationship(back_populates="feedback")


class TableOverview(UUIDMixin, TimestampMixin, Base):
    """What the overview agent said about one table in the offline pool.

    Cached because generating it costs a vision-model call with a screenshot
    attached, and the answer cannot change while the table does not: the pool is
    produced offline, so the same table tomorrow is the same table.

    `source_hash` is what makes that safe. It is a digest of the exact payload
    the agent was shown, so a rerun of the producer -- a new row, a corrected
    figure, a renamed column -- no longer matches and the row is regenerated
    instead of served.

    One row per (table, language): the overview is written in the reader's
    language, and translating a cached Turkish summary into English afterwards
    would be a second model call to save the first.
    """

    __tablename__ = "table_overviews"
    __table_args__ = (
        UniqueConstraint("table_id", "locale", name="uq_table_overviews_table_locale"),
    )

    table_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    locale: Mapped[str] = mapped_column(String(5), nullable=False)
    #: Digest of the table payload the agent read. A mismatch means regenerate.
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which model wrote it, so a model change can be told from a data change.
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: The agent's validated output, as `agents.table_overview.TableOverview`
    #: serialises it.
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


# What one automation run ended as. A failure is a stored report too -- see
# `AutomationReport`.
RUN_STATUSES = ("ok", "failed")


class Automation(UUIDMixin, TimestampMixin, Base):
    """A recurring question the user asked the assistant to run for them.

    "Her sabah 09:00'da altın fiyatlarını bankalar arasında karşılaştır." The
    scheduler wakes up, asks the supervisor `prompt` verbatim, and stores the
    answer as an `AutomationReport`.

    **The schedule is three plain fields, not a cron expression.** `hour`,
    `minute` and `weekdays` are what the model fills in when it creates an
    automation from a sentence, and a wrong cron string fails the worst way
    available -- silently, by simply never firing, with nothing on screen to
    show it. Three integers are wrong visibly: the list renders "Her gün 09:00"
    and the user can see it is not what they asked for. The cost is that the
    only schedules expressible are "at this time, on these days"; every example
    this feature was asked for is one of those.

    `weekdays` is a list of `0`=Monday .. `6`=Sunday, matching
    `datetime.weekday()`. An **empty list means every day** rather than never:
    the daily case is the common one, and it is what a model omitting the field
    most likely meant.

    `next_run_at` is UTC and is the column the runner claims on. It is
    maintained rather than derived at read time, so being due is an indexed
    comparison instead of a schedule computation per row -- and so a change to
    the schedule maths cannot retroactively move a run the user was already
    promised.
    """

    __tablename__ = "automations"
    __table_args__ = (
        # The runner's only query: enabled rows that are due. Partial on
        # `enabled` because a disabled automation is never a candidate, and the
        # index is scanned every poll.
        Index(
            "ix_automations_due",
            "next_run_at",
            postgresql_where=text("enabled"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Short name for the list and for the report's heading.
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    #: What the supervisor is asked, verbatim. Not truncated anywhere.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    #: Wall clock in `api/automations/schedule.py::TZ`, not UTC. A user who says
    #: "every morning at nine" means nine where they are, and storing UTC would
    #: silently move it if the zone's offset ever changed.
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weekdays: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Whether the run may research online. Default on: the reports people ask
    #: for -- new campaigns, this morning's gold price -- are about what changed
    #: since yesterday, which the offline index cannot know.
    web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Scheduled reports ask the supervisor a question. Condition alerts read
    #: live endpoints and compare typed numbers without involving a model.
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="scheduled_report"
    )
    #: Versioned `ConditionSpec` JSON for condition alerts; empty for reports.
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: Optional cadence shared by reports and alerts. NULL means fixed clock.
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Optional active window as local minutes after midnight. Both NULL means
    #: the interval runs throughout each selected day.
    window_start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why the last run produced nothing usable. Kept on the row as well as on
    #: the failed report, so the list can show a broken automation as broken
    #: without a second query.
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Edge-trigger state. NULL means the condition has never been evaluated.
    last_condition_met: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Latest checked values and timestamp, including false checks that do not
    #: create a notification.
    last_observation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="automations")


class AutomationReport(UUIDMixin, TimestampMixin, Base):
    """One answer an automation produced.

    `body` is the assistant's markdown, rendered by the same component that
    renders a chat answer -- a report looks like an answer because it is one.
    `citations` holds the same shape `ChatMessage.citations` does, for the same
    reason: re-running the retrieval later would produce different sources than
    the ones this report was written from.

    `read_at` is the notification. A report with `read_at IS NULL` is what the
    bell counts; opening it stamps the column and the badge drops. That is the
    whole mechanism -- there is no separate notification table, because a
    notification here carries no information the report does not.

    **Two fields are denormalised on purpose.**

    `user_id`, because the badge query runs on a timer in every open tab:
    `WHERE user_id = ? AND read_at IS NULL` on one index beats joining through
    `automations` every minute.

    `title`, because `automation_id` is `ON DELETE SET NULL` rather than
    `CASCADE`. Cancelling tomorrow's report should not delete yesterday's, and a
    kept report still has to be able to say what produced it.

    A failure is stored, not swallowed: `status="failed"` with `error` set. A
    report that never arrives is indistinguishable from an automation the user
    forgot they made.
    """

    __tablename__ = "automation_reports"
    __table_args__ = (
        # The badge count.
        Index("ix_automation_reports_unread", "user_id", "read_at"),
        # The Reports tab, newest first.
        Index(
            "ix_automation_reports_user_created",
            "user_id",
            text("created_at DESC"),
        ),
    )

    automation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("automations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The automation's title as it was when this ran.
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
