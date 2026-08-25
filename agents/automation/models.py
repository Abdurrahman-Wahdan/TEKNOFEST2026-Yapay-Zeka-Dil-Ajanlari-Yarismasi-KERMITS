"""Validated output of the automation-drafting agent."""

from pydantic import BaseModel, Field, field_validator


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
