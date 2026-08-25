"""The user's scheduled agent runs, and the reports they produce."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The list-shaped fields are validated here rather than in the router because
#: they arrive from two places -- a form and a language model -- and the rule is
#: the same for both.
MAX_WEEKDAY = 6
TITLE_CHARS = 160


def _clean_weekdays(value: object) -> list[int]:
    """Deduplicated, sorted, in range. Anything else dropped.

    Dropping rather than rejecting: a stray `7` from a model should not cost the
    six days it got right. `bool` is excluded explicitly -- it is an `int`
    subclass, and `True` would otherwise be stored as Tuesday.

    Mirrors `api/automations/schedule.py::valid_weekdays`, which is the authority
    when a schedule is *read*. This one keeps what is written clean, so the two
    never have to disagree about a row already in the table.
    """
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted(
        {
            item
            for item in value
            if isinstance(item, int)
            and not isinstance(item, bool)
            and 0 <= item <= MAX_WEEKDAY
        }
    )


class AutomationIn(BaseModel):
    """A new automation with its schedule given explicitly.

    The form's path. `POST /me/automations/describe` is the other one, where the
    schedule is read out of a sentence.
    """

    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    prompt: str = Field(min_length=1)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekdays: list[int] = Field(default_factory=list)
    web_search: bool = True

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> list[int]:
        return _clean_weekdays(value)


class AutomationDescribeIn(BaseModel):
    """Free text, plus whatever the user set by hand.

    A field left `None` is one the agent decides. A field with a value **wins**:
    the user who moved the hour picker did so after reading their own sentence,
    and a model's reading of "akşam" does not outrank that.
    """

    text: str = Field(min_length=1)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    #: `None` means "the agent decides"; `[]` means the user chose every day.
    #: Those are different answers, which is why this is nullable rather than
    #: defaulting to an empty list.
    weekdays: list[int] | None = None
    web_search: bool | None = None

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> list[int] | None:
        return None if value is None else _clean_weekdays(value)


class AutomationPatch(BaseModel):
    """A partial edit. Only the fields present are written."""

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_CHARS)
    prompt: str | None = Field(default=None, min_length=1)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    weekdays: list[int] | None = None
    web_search: bool | None = None
    enabled: bool | None = None

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> list[int] | None:
        return None if value is None else _clean_weekdays(value)


class AutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    prompt: str
    hour: int
    minute: int
    weekdays: list[int]
    web_search: bool
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_error: str
    created_at: datetime


class ReportOut(BaseModel):
    """One report. `body` is markdown, rendered by the chat answer renderer."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    automation_id: uuid.UUID | None
    title: str
    body: str
    citations: list[dict]
    status: str
    error: str
    read_at: datetime | None
    created_at: datetime


class ReportSummary(BaseModel):
    """A report without its body, for the list and the notification menu.

    The body is the whole point of a report and also all of its weight -- a
    gold-price report with ten citations is several kilobytes of markdown. The
    notification menu polls on a timer and shows five lines; sending it every
    body would make the badge the most expensive request in the app.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    automation_id: uuid.UUID | None
    title: str
    status: str
    read_at: datetime | None
    created_at: datetime


class UnreadCount(BaseModel):
    unread: int = Field(description="Reports this user has not opened yet.")
