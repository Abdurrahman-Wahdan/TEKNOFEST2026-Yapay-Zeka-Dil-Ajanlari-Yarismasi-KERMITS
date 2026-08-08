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
    installment: float
    total: float
    profit_rate: float
    annual_cost_rate: float | None
    fees: dict[str, float]
    schedule: list[PaymentRow]
    raw: dict


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


@dataclass(frozen=True)
class Rate:
    code: str
    name: str
    buy: float
    sell: float
    unit: str = "1"


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
