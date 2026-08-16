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

    # Null where the bank publishes a rate but never states a payment, and
    # nothing here can reproduce one -- Türkiye Finans is no longer this case
    # for financing (see `derived`), but stays here for any future bank whose
    # calculator can't be ported either.
    installment: float | None = Field(
        default=None,
        description="Monthly payment, or null where the bank publishes only a rate.",
    )
    total: float | None = None

    profit_rate: float
    annual_cost_rate: float | None = None
    fees: dict[str, float] = Field(default_factory=dict)

    # Non-empty where a bank prices one product several ways, so two rows from
    # one bank explain themselves instead of looking like a duplicate.
    variant: str = Field(
        default="",
        description='e.g. "sigortali" / "sigortasiz". Empty where the bank does not split.',
    )
    # True when the bank sells one product covering the whole axis this family
    # splits on, so the row answers the question without being specific to it.
    general: bool = False
    # True when `installment`/`total`/`schedule` were computed by us rather
    # than read off the wire -- currently only Türkiye Finans, whose own
    # calculator runs this exact arithmetic client-side. Same contract as
    # `ConversionOut.derived`: the UI must label these.
    derived: bool = False

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

    # Same meaning as on FinanceQuoteOut. `general` marks a bank answering with
    # its ordinary account where another bank sells a dedicated one.
    variant: str = ""
    general: bool = False


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


class CardInstallmentQuoteOut(BaseModel):
    bank: str
    card: ProductOut
    amount: float
    installments: int

    # Null where the bank publishes a rate but never states a payment, and
    # nothing here can reproduce one -- same contract as FinanceQuoteOut.installment.
    installment: float | None = Field(
        default=None,
        description="Monthly payment, or null where the bank publishes only a rate.",
    )
    total: float | None = None

    profit_rate: float
    # True when `installment`/`total` were computed by us rather than read off
    # the wire -- same contract as `FinanceQuoteOut.derived`.
    derived: bool = False


class MileRateOut(BaseModel):
    """One reward rate: miles earned per lira, for one card/tier/category.

    Kuveyt Türk's table is 567 rows -- card x tier x category -- so the
    frontend filters this client-side rather than the route taking three query
    parameters that would each need their own "list the valid ones" endpoint.
    """

    card: str
    tier: str
    category: str
    per_lira: float


class RateOut(BaseModel):
    code: str
    name: str
    buy: float
    sell: float
    unit: str = "1"
    as_of: str = ""
    derived: bool = Field(
        default=False,
        description="True when the pair was worked out from the bank's own "
        "converter rather than read off a published feed. The UI must label "
        "these: the numbers are the bank's, the inversion is ours.",
    )
    canonical: str = Field(
        default="",
        description="The shared symbol for this instrument -- XAU for gold "
        "whether the bank calls it XAU or 'ALT (gr)'. Cross-bank grouping keys "
        "on (canonical, unit); without it the frontend would have to duplicate "
        "the alias table and drift when a bank renames a code.",
    )


# ----- constraints -----

class BankLimitsOut(BaseModel):
    """What one bank will accept for a family, read from its own catalogue."""

    bank: str
    product: str = Field(
        description="The product, or the family stem when the bank bands it."
    )
    products_matched: int = Field(
        default=1,
        description="Above 1 the bank splits this into term/amount bands and "
        "picks one at quote time; the range below is the envelope of all of them.",
    )
    min_amount: float | None = None
    max_amount: float | None = None
    min_term: int | None = None
    max_term: int | None = None
    currencies: list[str] = Field(default_factory=list)


class IntersectionOut(BaseModel):
    """The range every bank in the run will accept.

    `limited_by` names the bank behind each bound, and only when one bank is
    actually the reason -- a bound every bank shares is the product's shape, not
    somebody's restriction.
    """

    min_amount: float | None = None
    max_amount: float | None = None
    min_term: int | None = None
    max_term: int | None = None
    currencies: list[str] = Field(default_factory=list)
    limited_by: dict[str, list[str]] = Field(default_factory=dict)


class ConstraintsOut(BaseModel):
    category: str
    family: str
    banks: list[BankLimitsOut] = Field(default_factory=list)
    unavailable: list["UnavailableOut"] = Field(default_factory=list)
    intersection: IntersectionOut


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
    card_quotes: list[CardInstallmentQuoteOut] = Field(default_factory=list)
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
