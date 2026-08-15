"""What every bank returns, whatever it calls things internally.

Each provider maps its own field names onto these, so the agent sees one shape
regardless of bank. `raw` is kept on every type: when a bank returns something
we did not model, it stays reachable without a code change.

Money is `float` because that is what the endpoints return and we never do
arithmetic on it. The one exception is Conversion, which may be derived.
"""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Product:
    """One product a bank will price, as the bank's own catalogue declares it."""

    code: str
    name: str
    category: str
    min_amount: float | None = None
    max_amount: float | None = None
    min_term: int | None = None
    max_term: int | None = None
    currencies: tuple[str, ...] = ("TRY",)

    # The rate the catalogue states for this product, where it states one:
    # a monthly profit rate for financing, an annual gross ratio for a
    # participation account. None where the bank only quotes per request.
    rate: float | None = None

    # The catalogue entry this came from. Requests cannot be built without it:
    # Kuveyt Türk needs ProductGroup, FEC and the exact Title, and Albaraka
    # echoes the whole blob back as its FinanceType parameter.
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentRow:
    """One line of a payment schedule."""

    order: int
    amount: float
    principal: float
    profit: float
    taxes: float
    remaining: float
    due_date: str = ""


@dataclass(frozen=True)
class FinanceQuote:
    bank: str
    product: Product
    amount: float
    term: int

    # None where the bank publishes a rate but never states a payment. Türkiye
    # Finans is the case: its calculator does the annuity in the browser, so
    # there is no instalment to read back, and computing one ourselves is the
    # one thing the rules forbid. A rate-only row is a real answer -- the rate
    # is the bank's own -- and it must stay distinguishable from a bank that
    # answered with a payment, which is why this is None and not 0.0.
    installment: float | None
    total: float | None

    profit_rate: float
    annual_cost_rate: float | None
    fees: dict[str, float]
    schedule: list[PaymentRow]
    raw: dict

    # Set by banks/compare.py from the family table, not by the provider: they
    # describe this product's relationship to the family it was asked about,
    # which a single-bank quote has no opinion on.
    #
    # `variant` is non-empty where a bank prices one product several ways --
    # Türkiye Finans quotes everything sigortalı and sigortasız -- so two rows
    # from one bank explain themselves instead of looking like a duplicate.
    variant: str = ""

    # True when this bank sells one product covering the whole axis the family
    # splits on: Ziraat has a single taşıt product, so the same row answers the
    # 0 km and the second-hand comparison. The number is real; the row just is
    # not specific to the family it appears in, and saying so is the difference
    # between an honest ranking and a misleading one.
    general: bool = False

    @property
    def priced(self) -> bool:
        """True when the bank stated a payment, not only a rate."""
        return self.installment is not None


@dataclass(frozen=True)
class ProfitShareQuote:
    bank: str
    product: Product
    amount: float
    term: int
    currency: str

    # "day" or "month". Banks take both in the same field and disagree about
    # which one a product accepts, so the unit that actually answered is
    # reported rather than the one that was asked for.
    term_unit: str

    # Participation ratio, %. None where a bank does not publish one: Albaraka
    # states only the resulting gross and net annual rates.
    ratio: float | None

    gross_profit: float
    net_profit: float
    gross_annual_rate: float | None
    net_annual_rate: float | None
    raw: dict

    # Set by banks/compare.py from the family table, exactly as on FinanceQuote.
    # `general` is what a gold comparison needs to stay honest: Kuveyt Türk and
    # Dünya sell a dedicated gold account, while Emlak, Albaraka and Vakıf take
    # gold as a currency on their ordinary one. Both are real answers, and they
    # are not the same product -- Kuveyt Türk's gold account pays a 40% ratio
    # where its ordinary account pays 95%.
    variant: str = ""
    general: bool = False


@dataclass(frozen=True)
class Rate:
    code: str
    name: str
    buy: float
    sell: float
    unit: str = "1"

    # When the bank says it quoted these. FX moves intraday, and without it the
    # agent cannot say how fresh a rate is. Empty where the feed omits it.
    as_of: str = ""

    # True when the pair was worked out from the bank's own converter rather
    # than read off a published feed. Dünya and Vakıf run a server-side
    # converter and publish no rate table, so their board columns come from
    # asking the converter what one unit is worth in each direction.
    #
    # The arithmetic is the bank's, not ours -- both legs are its own answers --
    # but it is still a step we took, so it is labelled. Validated against the
    # three banks that publish both: derived buy matched published buy to
    # 0.00% and sell to within 0.14% at worst.
    derived: bool = False


@dataclass(frozen=True)
class CardInstallmentQuote:
    bank: str
    card: Product
    amount: float
    installments: int
    installment: float
    total: float
    profit_rate: float
    raw: dict


@dataclass(frozen=True)
class Conversion:
    """An amount in one currency expressed in another.

    `derived` is False when the bank converted it server-side and True when we
    multiplied the bank's quoted rate ourselves. Kuveyt Türk publishes gold and
    FX rates but has no converter endpoint — its own page multiplies in the
    browser — and that multiplication is the single agreed exception to "never
    compute a number ourselves". Callers must surface the distinction.
    """

    bank: str
    source: str
    target: str
    amount: Decimal
    result: Decimal
    rate: Decimal
    derived: bool


@dataclass(frozen=True)
class MileRate:
    """One card reward rate: miles earned per lira spent in a category.

    A loyalty programme states these as a rate per lira per spending category
    (fuel, market, travel...), varying by card and membership tier. `per_lira`
    is the multiplier the bank publishes; 0.06 means 6 miles per 100 TL.
    """

    card: str
    tier: str
    category: str
    per_lira: float
    raw: dict = field(default_factory=dict)
