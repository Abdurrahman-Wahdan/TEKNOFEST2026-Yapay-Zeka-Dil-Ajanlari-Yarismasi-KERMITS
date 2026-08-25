"""The user's profile and their saved dashboard views."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileIn(BaseModel):
    """A profile write. Every field optional -- a partial answer is a valid answer.

    Bank and family keys are validated against `banks.list_banks()` and
    `banks.families` in the router, not here: pydantic would have to import the
    bank layer to know them, and the error is more useful when it can name what
    is available.
    """

    persona: str | None = Field(default=None, pattern="^(customer|analyst)$")
    banks: list[str] | None = None
    families: list[str] | None = None
    typical_amount: float | None = Field(default=None, gt=0)
    typical_term_months: int | None = Field(default=None, gt=0, le=360)
    answers: dict | None = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    persona: str
    banks: list[str]
    families: list[str]
    typical_amount: float | None
    typical_term_months: int | None
    answers: dict
    completed_at: datetime | None


class Component(BaseModel):
    """One tile on a dashboard.

    `type` names a component in the frontend catalog; `props` is whatever that
    component takes. Unvalidated on purpose -- the catalog lives in TypeScript,
    and duplicating each component's prop schema in Python would guarantee the
    two drift. The frontend renders an unknown type as a visible placeholder.
    """

    type: str = Field(max_length=64)
    props: dict = Field(default_factory=dict)


class SavedViewIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{1,80}$")
    title: str
    components: list[Component] = Field(default_factory=list)
    generated: bool = False


class SavedViewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    components: list[Component]
    generated: bool
    updated_at: datetime


class StatsOut(BaseModel):
    """What this user has actually done with the product.

    Counted at request time from rows that already exist. Nothing here is
    maintained as a running total: a counter column would be a second source of
    truth for a number whose first source is one indexed `COUNT`, and it would be
    wrong the first time a delete missed it.

    **There is deliberately no token count.** Nothing in this application records
    model usage -- the supervisor asks for `stream_usage` but no handler collects
    it, and the ten bank specialists' spend is unobserved entirely. A "tokens"
    field would therefore have to read zero for every conversation ever held,
    which says something false rather than nothing.
    """

    chat_sessions: int
    messages_sent: int = Field(description="Turns the user wrote.")
    messages_received: int = Field(description="Answers the assistant wrote.")
    saved_tables: int
    automations: int
    reports: int
    unread_reports: int
    first_activity: datetime | None
    last_activity: datetime | None
