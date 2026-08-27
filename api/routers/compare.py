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

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status

from banks import compare as compare_mod
from banks import limits
from banks.providers import UnsupportedProduct

from .. import live_overviews as live
from .. import table_overviews as overviews
from ..converters import comparison_out
from ..schemas.banks import ComparisonOut, ConstraintsOut
from ..schemas.compare_tables import (
    LiveOverviewRequest,
    LiveOverviewState,
    RankedBankOut,
    TableOverviewOut,
)

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
    monthly_profit_rate: float | None = Query(
        default=None,
        gt=0,
        le=100,
        description="Optional customer-supplied monthly profit-rate scenario, as a percentage.",
    ),
    banks: list[str] | None = BanksQuery,
) -> ComparisonOut:
    """One financing product at every bank that sells it.

    Payment schedules are omitted -- ten full schedules would be megabytes of
    rows nobody reads in a ranking. Fetch one from /api/banks/{bank}/finance.
    """
    try:
        result = compare_mod.finance(family, amount, term, banks, monthly_profit_rate)
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


@router.get("/card", response_model=ComparisonOut)
def compare_card(
    amount: float = Query(gt=0),
    installments: int = Query(gt=0, le=36),
    banks: list[str] | None = BanksQuery,
) -> ComparisonOut:
    """Every card at every bank that publishes a card calculator, ranked.

    Cards have no cross-bank family: each bank sells its own catalogue under
    its own names, so this quotes every card every in-scope bank publishes
    rather than one named product. A bank that states only a rate and no
    instalment sinks to the bottom of the ranking rather than winning it.
    """
    try:
        result = compare_mod.card(amount, installments, banks)
    except (UnsupportedProduct, ValueError) as exc:
        raise _bad_family(exc) from exc
    return comparison_out(result)


@router.get("/constraints", response_model=ConstraintsOut)
def compare_constraints(
    family: str = Query(description="A family key from GET /api/banks/families."),
    category: str = Query(default="finance", pattern="^(finance|profit_share)$"),
    banks: list[str] | None = BanksQuery,
) -> ConstraintsOut:
    """What the selected banks will accept, before anyone is asked.

    Read from the catalogues, which the provider layer caches, so a form can
    call this on every change without touching a bank endpoint each time.

    The value is `intersection`: Dünya's konut product stops at 84 months while
    the other five reach 120, so a run including Dünya can only ask for 84.
    Showing that as a ceiling -- with the bank that set it -- beats letting
    someone submit 360 and watch every bank decline.
    """
    try:
        result = limits.for_family(category, family, banks)
    except (UnsupportedProduct, ValueError) as exc:
        raise _bad_family(exc) from exc
    return ConstraintsOut(**result)


def _overview_out(
    digest: str, locale: str, result, generated_at: datetime
) -> TableOverviewOut:
    """A live overview on the wire, in the shape the pool's already uses.

    Same schema on purpose: the card that draws this is the card that draws a
    pool table's overview, and giving the live one its own near-identical shape
    would be a second renderer to keep in step. `table_id` carries the digest --
    there is no table to name here, and the digest is what identifies what was
    read.
    """
    return TableOverviewOut(
        table_id=digest,
        locale=locale,
        summary=result.summary,
        recommended=[RankedBankOut(bank=r.bank, why=r.why) for r in result.recommended],
        not_recommended=[
            RankedBankOut(bank=r.bank, why=r.why) for r in result.not_recommended
        ],
        caveat=result.caveat,
        generated_at=generated_at,
        model=overviews.model_name(),
    )


@router.post("/overview", response_model=LiveOverviewState)
def create_live_overview(body: LiveOverviewRequest) -> LiveOverviewState:
    """Read whatever `/compare` is showing and say what it shows.

    **One call does both jobs.** The pool's equivalent is a GET to check and a
    POST to start, because there the client already knows the key -- the table
    id is in the URL it navigated to. Here the key is a hash of the page, and
    having the browser compute it would mean a SHA-256 over the same bytes in
    two languages agreeing forever; when that drifts it fails silently and looks
    like an overview that never arrives. So the client posts the page, the
    server hashes it, and the answer is either the finished overview or the
    digest to poll with.

    That makes this POST safe to repeat: the digest is the content, so asking
    twice for the same board serves the cache the second time, and a
    five-minute refresh over a board that has not moved costs nothing at all.

    No authentication, like the rest of this router. The cost is bounded by the
    digest cache, by the single-flight lock, and by the process-wide generation
    cap this shares with the pool.
    """
    text = (body.page.text or "").strip()
    if not text:
        # Without the outline there is nothing to summarise. Refused rather
        # than answered emptily: a card that quietly shows nothing reads as a
        # model with no opinion, which is a different and wrong answer.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "An overview needs the page outline (`page.text`).",
        )

    locale = overviews.normalise_locale(body.locale)
    page_digest = live.digest(text)

    hit = live.cached(page_digest, locale)
    if hit is not None:
        result, generated_at = hit
        return LiveOverviewState(
            status="ready",
            digest=page_digest,
            overview=_overview_out(page_digest, locale, result, generated_at),
        )

    # How much the model is about to read. An outline suddenly much shorter than
    # usual means the page changed shape and the reader is now summarising less
    # than the user can see.
    logger.info(
        "Live overview requested digest=%s locale=%s outline=%d chars",
        page_digest[:12],
        locale,
        len(text),
    )
    live.start(page_text=text, page_digest=page_digest, locale=locale)
    return LiveOverviewState(status="generating", digest=page_digest)


@router.get("/overview", response_model=LiveOverviewState)
def get_live_overview(
    digest: str = Query(description="The digest returned by POST /api/compare/overview."),
    locale: str = Query(default="tr", description="'tr' or 'en'."),
) -> LiveOverviewState:
    """Poll for an overview that is being written.

    Deliberately never generates, for the reason the pool's GET does not either:
    a GET that costs a model call is not safe to retry, and this one is called
    on a timer.

    `missing` is the failure signal. A digest that is neither cached nor running
    was either never asked for or was asked for and did not survive its
    generation -- and the client, which posted before it started polling, is the
    one that can tell those apart.
    """
    locale = overviews.normalise_locale(locale)
    hit = live.cached(digest, locale)
    if hit is not None:
        result, generated_at = hit
        return LiveOverviewState(
            status="ready",
            digest=digest,
            overview=_overview_out(digest, locale, result, generated_at),
        )
    if live.running(digest, locale):
        return LiveOverviewState(status="generating", digest=digest)
    return LiveOverviewState(status="missing", digest=digest)
