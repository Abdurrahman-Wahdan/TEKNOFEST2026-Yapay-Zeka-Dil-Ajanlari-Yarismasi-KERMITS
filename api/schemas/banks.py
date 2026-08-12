"""Bank data on the wire: the registry, quotes, comparisons and search hits.

These mirror `banks/models.py` and `index/models.py` rather than reusing them.
The dataclasses carry a `raw` dict of whatever the bank's own endpoint returned,
which is essential inside the system and must not cross the network -- it is
unbounded, unmodelled, and would generate as `Record<string, unknown>` in the
frontend's types, erasing the contract for every field around it.

So `raw` is dropped at this boundary, and everything else is copied across
field-for-field.
"""

from pydantic import BaseModel, Field


# ----- the registry -----

class BankOut(BaseModel):
    """One bank, what it publishes, and what is currently down.

    `publishes` and `maintenance` are different absences and are reported
    separately: a bank that does not sell car finance and a bank whose car
    finance endpoint is broken this morning must never look alike in the UI.
    """

    name: str = Field(description="The key every other endpoint takes.")
    display_name: str
    publishes: list[str]
    maintenance: list[str] = Field(
        default_factory=list,
        description="Capabilities the last health check found failing.",
    )
    notes: str = ""


class FamilyOut(BaseModel):
    """A product family -- the thing that makes two banks comparable."""

    key: str
    label: str
    category: str = Field(description="finance | profit_share")
    banks: list[str] = Field(description="Banks that sell it.")


# ----- quotes -----

class ProductOut(BaseModel):
    code: str
    name: str
    category: str
    min_amount: float | None = None
    max_amount: float | None = None
    min_term: int | None = None
    max_term: int | None = None
    currencies: list[str] = Field(default_factory=lambda: ["TRY"])
    rate: float | None = None


class PaymentRowOut(BaseModel):
    order: int
    amount: float
    principal: float
    profit: float
    taxes: float
    remaining: float
    due_date: str = ""


class FinanceQuoteOut(BaseModel):
    bank: str
    product: ProductOut
    amount: float
    term: int
    installment: float
    total: float
    profit_rate: float
    annual_cost_rate: float | None = None
    fees: dict[str, float] = Field(default_factory=dict)
    # Omitted from comparison responses, where ten full schedules would dwarf
    # the answer. The single-bank quote endpoint includes it.
    schedule: list[PaymentRowOut] = Field(default_factory=list)


class ProfitShareQuoteOut(BaseModel):
    bank: str
    product: ProductOut
    amount: float
    term: int
    currency: str
    term_unit: str
    ratio: float | None = None
    gross_profit: float
    net_profit: float
    gross_annual_rate: float | None = None
    net_annual_rate: float | None = None


class ConversionOut(BaseModel):
    bank: str
    source: str
    target: str
    # Strings, not floats: Conversion holds Decimal, and rendering money through
    # a float would reintroduce the rounding the Decimal was chosen to avoid.
    amount: str
    result: str
    rate: str
    derived: bool = Field(
        description="True when we multiplied a quoted rate rather than the bank "
        "converting server-side. The UI must label these."
    )


class RateOut(BaseModel):
    code: str
    name: str
    buy: float
    sell: float
    unit: str = "1"
    as_of: str = ""


# ----- comparison -----

class UnavailableOut(BaseModel):
    """A bank absent from a ranking, and why.

    `why` is one of not_offered | maintenance | declined | error. Four distinct
    states, kept distinct: "no bank offers this" and "we could not reach anyone"
    are opposite answers.
    """

    bank: str
    why: str
    detail: str = ""


class ComparisonOut(BaseModel):
    category: str
    family: str
    quotes: list[FinanceQuoteOut] = Field(default_factory=list)
    profit_share_quotes: list[ProfitShareQuoteOut] = Field(default_factory=list)
    conversions: list[ConversionOut] = Field(default_factory=list)
    unavailable: list[UnavailableOut] = Field(default_factory=list)
    seconds: float = Field(description="How long the fan-out took.")


# ----- corpus search -----

class ChunkOut(BaseModel):
    """One retrieved passage, with the link that proves it."""

    score: float
    cite_url: str = Field(description="Openable: page#anchor, or form.pdf#page=7.")
    text: str
    bank: str = ""
    title: str = ""
    doc_kind: str = ""
    source_type: str = ""
    campaign_end: str | None = None
    from_vision: bool = Field(
        default=False,
        description="Read by OCR from a scanned page. The UI should mark it, "
        "and the agent should hedge on exact figures taken from it.",
    )


class SearchResponse(BaseModel):
    query: str
    active_only: bool
    hits: list[ChunkOut]
