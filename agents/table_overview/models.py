"""Validated output of the table-overview agent."""

from pydantic import BaseModel, Field, field_validator


class RankedBank(BaseModel):
    """One bank worth calling out, with the figure that puts it there."""

    bank: str = Field(description="The bank's name exactly as the page writes it.")
    why: str = Field(
        description=(
            "One short sentence naming the figure on this page that puts the "
            "bank here — not a general impression of the bank."
        ),
    )

    @field_validator("bank", "why", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TableOverview(BaseModel):
    """A first read of one comparison table, short enough to take in at a glance.

    Deliberately without a per-bank list. It had one, and it was the reason the
    card ran longer than the table it sat above: ten banks, ten lines, all of it
    already visible underneath. What a reader cannot get from the table is the
    verdict, so that is all this carries.
    """

    summary: str = Field(
        description=(
            "One sentence: what this table compares, and across how many banks."
        ),
    )
    recommended: list[RankedBank] = Field(
        default_factory=list,
        description="At most two, best first. Empty when the page does not support a pick.",
    )
    not_recommended: list[RankedBank] = Field(
        default_factory=list,
        description=(
            "At most two: the weakest terms here, or banks that do not offer "
            "this at all. Empty when there is no honest basis for it."
        ),
    )
    caveat: str = Field(
        default="",
        description=(
            "One sentence on what a reader should check before deciding. "
            "Empty when there is nothing to add."
        ),
    )

    @field_validator("summary", "caveat", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
