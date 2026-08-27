"""Validated output of the automation-drafting agent."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from api.schemas.automations import ConditionSpec, MAX_CHECK_MINUTES, MIN_CHECK_MINUTES


class AutomationDraft(BaseModel):
    """One sentence from the user, turned into a storable standing order.

    Every field is validated here rather than trusted downstream. This is the
    one place a language model's output becomes a row that fires unattended for
    months, so an hour of `25` has to fail at the boundary -- a schedule that is
    silently wrong runs at the wrong time every day and nobody is watching.
    """

    title: str = Field(
        max_length=160,
        description=(
            "Short name for the user's list, in their language. Not a sentence: "
            "'Sabah altın raporu', not 'Her sabah altın fiyatlarını karşılaştır'."
        ),
    )
    prompt: str = Field(
        description=(
            "The question to ask on every run, rewritten so it stands alone with "
            "no memory of the conversation it came from. Name the banks, "
            "products and currencies explicitly, and say what shape of answer "
            "is wanted (a table, a ranking, a short summary)."
        ),
    )
    hour: int = Field(
        ge=0,
        le=23,
        description=(
            "Hour of the day it runs, 0-23, Turkey local time. 'sabah' with no "
            "hour is 9, 'akşam' is 20, 'gece' is 22."
        ),
    )
    minute: int = Field(default=0, ge=0, le=59)
    weekdays: list[int] = Field(
        default_factory=list,
        description=(
            "0=Monday ... 6=Sunday. EMPTY means every day, which is what 'her "
            "gün' and 'her sabah' mean. Only name days the user named; "
            "'hafta içi' is [0,1,2,3,4]."
        ),
    )
    web_search: bool = Field(
        default=True,
        description=(
            "Whether each run may research online. True unless the user asked "
            "for indexed data only."
        ),
    )
    kind: Literal["scheduled_report", "condition_alert", "needs_clarification"] = Field(
        default="scheduled_report",
        description=(
            "condition_alert only for notify/alarm requests evaluated against live numbers; "
            "needs_clarification when a required bank, amount, term, metric, side or threshold "
            "is missing; otherwise scheduled_report."
        ),
    )
    # Kept as JSON in the drafting schema so smaller local models are not asked
    # to choose inside a deeply nested discriminated union. The validator below
    # immediately turns it through `ConditionSpec`, so storage is still fully
    # typed and invalid keys/units/operators never escape this class.
    condition: dict | None = None
    interval_minutes: int | None = Field(
        default=None,
        ge=MIN_CHECK_MINUTES,
        le=MAX_CHECK_MINUTES,
        description=(
            "Frequency for any interval-based automation. Use it for reports, "
            "comparisons and alerts whenever the user says every N minutes/hours."
        ),
    )
    window_start_minute: int | None = Field(
        default=None,
        ge=0,
        le=1439,
        description="Optional daily active-window start, minutes after midnight.",
    )
    window_end_minute: int | None = Field(
        default=None,
        ge=0,
        le=1439,
        description="Optional daily active-window end, minutes after midnight.",
    )
    clarification: str = Field(
        default="",
        description="One concise question in the user's language when kind=needs_clarification.",
    )

    @field_validator("title", "prompt", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> object:
        """Drop unusable entries instead of rejecting the whole draft.

        A stray `7` should not cost the six days the model got right, and an
        entry that is not an integer at all tells us nothing about which day was
        meant. `bool` is excluded explicitly because it is an `int` subclass and
        `True` would otherwise become Tuesday.

        Sorted and deduplicated so two spellings of "Monday and Friday" store
        identically -- which is what lets the UI compare schedules for equality.
        Mirrors `api/automations/schedule.py::valid_weekdays`; that one is the
        authority at read time, this one keeps the stored row clean.
        """
        if not isinstance(value, (list, tuple, set)):
            return []
        return sorted(
            {
                item
                for item in value
                if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 6
            }
        )

    @model_validator(mode="after")
    def complete_kind(self) -> "AutomationDraft":
        # Some smaller structured-output models correctly identify a missing
        # input and fill `clarification`, but forget to switch the discriminator.
        # Never let that half-finished result become a daily report. The question
        # is the model's explicit admission that the rule is not executable yet.
        if self.clarification.strip() and self.kind != "condition_alert":
            self.kind = "needs_clarification"
        if self.kind == "condition_alert":
            if self.condition is None:
                raise ValueError("A condition alert needs a validated condition.")
            self.condition = ConditionSpec.model_validate(self.condition).model_dump(
                mode="json"
            )
        elif self.kind == "needs_clarification":
            if not self.clarification.strip():
                raise ValueError("A clarification result needs a question.")
            self.condition = None
            self.interval_minutes = None
            self.window_start_minute = None
            self.window_end_minute = None
        else:
            self.condition = None
            self.clarification = ""
        if (self.window_start_minute is None) != (self.window_end_minute is None):
            raise ValueError("A time window needs both a start and an end.")
        if self.window_start_minute is not None and self.interval_minutes is None:
            raise ValueError("A time window requires an interval schedule.")
        if self.window_start_minute == self.window_end_minute and self.window_start_minute is not None:
            raise ValueError("A time window start and end cannot be equal.")
        return self
