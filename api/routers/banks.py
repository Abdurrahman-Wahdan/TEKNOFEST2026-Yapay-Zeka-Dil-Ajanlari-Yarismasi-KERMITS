"""The bank registry, one bank's catalogue, and one bank's live quote.

These endpoints are public. A bank's rates are public information -- they are on
the bank's own website -- and requiring a login to read them would make the
dashboard useless as a landing page while protecting nothing.

Every handler is `def`, not `async def`. `banks/` is synchronous and blocking
(httpx and curl_cffi, plus a thread pool for the fan-out); declaring these
`async` would run that blocking work on the event loop and stall every other
request in the process. FastAPI runs a plain `def` in a threadpool, which is
exactly right here.
"""

import logging

from fastapi import APIRouter, HTTPException, Path, Query, status

from banks import get_bank, list_banks
from banks.providers import UnsupportedProduct
from banks.providers.base import TemporarilyUnavailable

from ..converters import (
    family_list, finance_quote_out, product_out, profit_share_quote_out, rate_out,
)
from ..schemas.banks import (
    BankOut, FamilyOut, FinanceQuoteOut, ProductOut, ProfitShareQuoteOut, RateOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/banks", tags=["banks"])

BankName = Path(description="A bank key from GET /api/banks.")


def _bank(name: str):
    """Resolve a bank, or 404 with the list of valid names."""
    try:
        return get_bank(name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _handle(exc: UnsupportedProduct) -> HTTPException:
    """Map the bank layer's refusals onto status codes.

    Three different things, kept different:
      - TemporarilyUnavailable -> 503, the bank is down and retrying may work
      - UnsupportedProduct     -> 422, this bank does not sell this, ever
    A 500 for either would tell the UI to show an error where it should show an
    answer: "Albaraka does not offer this" is information, not a failure.
    """
    if isinstance(exc, TemporarilyUnavailable):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("", response_model=list[BankOut])
def all_banks() -> list[BankOut]:
    """Every bank, what it publishes, and what is currently in maintenance.

    All ten are listed, including the two with no callable endpoint. A bank that
    publishes nothing is a legitimate answer with a reason in `notes`, not an
    omission.
    """
    return [
        BankOut(
            name=name,
            display_name=entry["display_name"],
            publishes=entry["publishes"],
            maintenance=entry.get("maintenance", []),
            notes=entry.get("notes") or "",
        )
        for name, entry in sorted(list_banks().items())
    ]


@router.get("/families", response_model=list[FamilyOut])
def product_families() -> list[FamilyOut]:
    """The product families that make two banks comparable.

    Declared ahead of /{bank} so the literal path wins the match -- otherwise
    "families" is read as a bank name and every request 404s.
    """
    return family_list()


@router.get("/{bank}", response_model=BankOut)
def one_bank(bank: str = BankName) -> BankOut:
    provider = _bank(bank)
    entry = list_banks()[provider.name]
    return BankOut(
        name=provider.name,
        display_name=entry["display_name"],
        publishes=entry["publishes"],
        maintenance=entry.get("maintenance", []),
        notes=entry.get("notes") or "",
    )


@router.get("/{bank}/products", response_model=list[ProductOut])
def bank_products(
    bank: str = BankName,
    category: str = Query(default="finance", description="finance | profit_share"),
) -> list[ProductOut]:
    """A bank's own catalogue, as the bank publishes it."""
    provider = _bank(bank)
    try:
        return [product_out(p) for p in provider.products(category)]
    except UnsupportedProduct as exc:
        raise _handle(exc) from exc


@router.get("/{bank}/finance", response_model=FinanceQuoteOut)
def bank_finance_quote(
    bank: str = BankName,
    product: str = Query(description="A product code from /products."),
    amount: float = Query(gt=0),
    term: int = Query(gt=0, le=360, description="Months."),
) -> FinanceQuoteOut:
    """A live financing quote, including the full payment schedule.

    The number is the bank's own. Nothing here recomputes an installment or
    fills a gap with a formula -- an endpoint that will not answer is reported
    as not answering.
    """
    provider = _bank(bank)
    try:
        return finance_quote_out(provider.finance_quote(product, amount, term))
    except UnsupportedProduct as exc:
        raise _handle(exc) from exc


@router.get("/{bank}/profit-share", response_model=ProfitShareQuoteOut)
def bank_profit_share_quote(
    bank: str = BankName,
    product: str = Query(),
    amount: float = Query(gt=0),
    term: int = Query(gt=0),
    unit: str = Query(default="month", pattern="^(day|month)$"),
    currency: str = Query(default="TRY", min_length=3, max_length=3),
) -> ProfitShareQuoteOut:
    """A live participation-account quote.

    Banks disagree about whether a product takes a term in days or months, so
    the response repeats the unit that actually answered rather than echoing
    the one that was asked for.
    """
    provider = _bank(bank)
    try:
        quote = provider.profit_share_quote(
            product, amount, term, unit=unit, currency=currency.upper()
        )
    except UnsupportedProduct as exc:
        raise _handle(exc) from exc
    return profit_share_quote_out(quote)


@router.get("/{bank}/rates", response_model=list[RateOut])
def bank_rates(bank: str = BankName) -> list[RateOut]:
    """A bank's FX and gold table, with the timestamp it was quoted at."""
    provider = _bank(bank)
    try:
        return [rate_out(r) for r in provider.rates()]
    except UnsupportedProduct as exc:
        raise _handle(exc) from exc
