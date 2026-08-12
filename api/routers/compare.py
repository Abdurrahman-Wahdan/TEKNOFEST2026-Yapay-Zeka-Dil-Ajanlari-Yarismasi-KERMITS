"""The same question at every bank at once -- what the comparison dashboards read.

One request instead of ten. `banks/compare.py` fans out across threads because
sequentially it would be slower than the calls it replaces: six banks measured
11.99s one at a time against 0.59s together.

Nothing is silently dropped. A bank that does not sell the product, one under
maintenance, one that declined and one that failed are four different answers,
and all four come back in `unavailable` -- so the UI can say *why* a bank is
missing from a ranking instead of quietly showing nine rows.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from banks import compare as compare_mod
from banks.providers import UnsupportedProduct

from ..converters import comparison_out
from ..schemas.banks import ComparisonOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compare", tags=["compare"])

BanksQuery = Query(
    default=None,
    description="Limit to these banks. Omitted, every bank that sells it is asked.",
)


def _bad_family(exc: UnsupportedProduct) -> HTTPException:
    """422 with the message the bank layer wrote.

    `families.entries()` already answers "that is not a family, here are the
    valid ones" and disambiguates a Turkish word that could mean two products.
    Rewriting that here would lose the disambiguation.
    """
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("/finance", response_model=ComparisonOut)
def compare_finance(
    family: str = Query(description="A family key from /api/banks/families."),
    amount: float = Query(gt=0),
    term: int = Query(gt=0, le=360, description="Months."),
    banks: list[str] | None = BanksQuery,
) -> ComparisonOut:
    """One financing product at every bank that sells it.

    Payment schedules are omitted -- ten full schedules would be megabytes of
    rows nobody reads in a ranking. Fetch one from /api/banks/{bank}/finance.
    """
    try:
        result = compare_mod.finance(family, amount, term, banks)
    except (UnsupportedProduct, ValueError) as exc:
        raise _bad_family(exc) from exc
    return comparison_out(result)


@router.get("/profit-share", response_model=ComparisonOut)
def compare_profit_share(
    family: str = Query(default="katilma"),
    amount: float = Query(gt=0),
    term: int = Query(gt=0),
    unit: str = Query(default="month", pattern="^(day|month)$"),
    currency: str = Query(default="TRY", min_length=3, max_length=3),
    banks: list[str] | None = BanksQuery,
) -> ComparisonOut:
    """One participation account at every bank that sells it."""
    try:
        result = compare_mod.profit_share(
            family, amount, term, unit, currency.upper(), banks
        )
    except (UnsupportedProduct, ValueError) as exc:
        raise _bad_family(exc) from exc
    return comparison_out(result)


@router.get("/exchange", response_model=ComparisonOut)
def compare_exchange(
    source: str = Query(min_length=3, max_length=5),
    target: str = Query(min_length=3, max_length=5),
    amount: float = Query(gt=0),
    banks: list[str] | None = BanksQuery,
) -> ComparisonOut:
    """The same conversion at every bank that converts.

    Each result carries `derived`. False means the bank converted it itself;
    True means we multiplied the bank's own quoted rate, which is the single
    agreed exception to "never compute a number ourselves". The UI must label
    a derived figure -- it is our arithmetic, not the bank's answer.
    """
    try:
        result = compare_mod.exchange(source.upper(), target.upper(), amount, banks)
    except (UnsupportedProduct, ValueError) as exc:
        raise _bad_family(exc) from exc
    return comparison_out(result)
