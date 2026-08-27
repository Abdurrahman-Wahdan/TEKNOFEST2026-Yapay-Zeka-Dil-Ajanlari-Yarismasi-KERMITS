"""Dataclass -> response model, in one place.

`banks/` and `index/` return frozen dataclasses carrying a `raw` dict of the
bank's own payload. That dict is what makes an unmodelled field reachable
without a code change, and it is exactly what must not cross the network: it is
unbounded, undocumented, and typed as `unknown` in the generated frontend types.

Every conversion lives here so "what leaves the building" is one file to read,
rather than a `raw` leaking through the one router nobody checked.
"""

from banks import families as families_mod
from banks.parse import canonical_currency
from banks.models import (
    CardInstallmentQuote, Conversion, FinanceQuote, MileRate, PaymentRow, ProfitShareQuote, Rate,
)
from index.models import RetrievedChunk

from .schemas.banks import (
    CardInstallmentQuoteOut, ChunkOut, ComparisonOut, ConversionOut, FamilyOut,
    FinanceQuoteOut, MileRateOut, PaymentRowOut, ProductOut, ProfitShareQuoteOut,
    RateOut, UnavailableOut,
)


def product_out(product) -> ProductOut:
    return ProductOut(
        code=product.code,
        name=product.name,
        category=product.category,
        min_amount=product.min_amount,
        max_amount=product.max_amount,
        min_term=product.min_term,
        max_term=product.max_term,
        currencies=list(product.currencies),
        rate=product.rate,
    )


def payment_row_out(row: PaymentRow) -> PaymentRowOut:
    return PaymentRowOut(
        order=row.order,
        amount=row.amount,
        principal=row.principal,
        profit=row.profit,
        taxes=row.taxes,
        remaining=row.remaining,
        due_date=row.due_date,
    )


def finance_quote_out(quote: FinanceQuote, *, schedule: bool = True) -> FinanceQuoteOut:
    """A financing quote.

    `schedule=False` for comparisons: ten banks' full payment schedules are
    megabytes of rows nobody reads in a ranking, and the single-bank endpoint is
    where a schedule is actually wanted.
    """
    return FinanceQuoteOut(
        bank=quote.bank,
        product=product_out(quote.product),
        amount=quote.amount,
        term=quote.term,
        installment=quote.installment,
        total=quote.total,
        profit_rate=quote.profit_rate,
        annual_cost_rate=quote.annual_cost_rate,
        fees=dict(quote.fees),
        variant=quote.variant,
        general=quote.general,
        derived=quote.derived,
        schedule=[payment_row_out(r) for r in quote.schedule] if schedule else [],
    )


def profit_share_quote_out(quote: ProfitShareQuote) -> ProfitShareQuoteOut:
    return ProfitShareQuoteOut(
        bank=quote.bank,
        product=product_out(quote.product),
        amount=quote.amount,
        term=quote.term,
        currency=quote.currency,
        term_unit=quote.term_unit,
        ratio=quote.ratio,
        gross_profit=quote.gross_profit,
        net_profit=quote.net_profit,
        gross_annual_rate=quote.gross_annual_rate,
        net_annual_rate=quote.net_annual_rate,
        variant=quote.variant,
        general=quote.general,
    )


def conversion_out(conversion: Conversion) -> ConversionOut:
    """Decimal money rendered as a string, not a float.

    `Conversion` holds Decimal precisely so a converted amount is exact. Casting
    to float here to make it JSON-friendly would undo that at the last step.
    """
    return ConversionOut(
        bank=conversion.bank,
        source=conversion.source,
        target=conversion.target,
        amount=str(conversion.amount),
        result=str(conversion.result),
        rate=str(conversion.rate),
        derived=conversion.derived,
    )


def canonical_code(code: str) -> str:
    """The shared symbol for a bank's own rate code.

    Albaraka calls gold XAU while Kuveyt Türk and Hayat call it "ALT (gr)", so
    nothing can be compared across banks without this -- and duplicating the
    table in TypeScript would drift the first time a bank renames a code.

    Delegates to `banks.parse` rather than keeping a second reversed copy of
    RATE_ALIASES: the catalogue path needs exactly the same mapping, and two
    tables that must agree eventually do not.
    """
    return canonical_currency(code)


def rate_out(rate: Rate) -> RateOut:
    return RateOut(
        code=rate.code, name=rate.name, buy=rate.buy,
        sell=rate.sell, unit=rate.unit, quote_currency=rate.quote_currency, as_of=rate.as_of,
        derived=rate.derived,
        canonical=canonical_code(rate.code),
    )


class DuplicateRateIdentity(ValueError):
    """A bank published two live rows for the same comparable quote."""


def rate_list_out(rates: list[Rate]) -> list[RateOut]:
    """Convert one bank board only after proving its comparable identities are unique."""
    seen: set[tuple[str, str, str]] = set()
    converted: list[RateOut] = []
    for rate in rates:
        identity = (canonical_code(rate.code), rate.unit, rate.quote_currency.upper())
        if identity in seen:
            raise DuplicateRateIdentity(
                f"duplicate live rate identity: {identity[0]} / {identity[1]} / {identity[2]}"
            )
        seen.add(identity)
        converted.append(rate_out(rate))
    return converted


def card_quote_out(quote: CardInstallmentQuote) -> CardInstallmentQuoteOut:
    return CardInstallmentQuoteOut(
        bank=quote.bank, card=product_out(quote.card), amount=quote.amount,
        installments=quote.installments, installment=quote.installment,
        total=quote.total, profit_rate=quote.profit_rate, derived=quote.derived,
    )


def mile_rate_out(rate: MileRate) -> MileRateOut:
    return MileRateOut(
        card=rate.card, tier=rate.tier, category=rate.category, per_lira=rate.per_lira,
    )


def comparison_out(comparison) -> ComparisonOut:
    """A whole comparison, sorting each quote into the list matching its type.

    `Comparison.quotes` is heterogeneous -- finance, profit-share and conversion
    results all arrive in the same list depending on which compare function ran.
    Splitting them by type here is what lets the frontend have three typed
    arrays instead of one union it has to narrow at every use.
    """
    out = ComparisonOut(
        category=comparison.category,
        family=comparison.family,
        seconds=comparison.seconds,
        unavailable=[
            UnavailableOut(bank=u.bank, why=u.why, detail=u.detail)
            for u in comparison.unavailable
        ],
    )
    for quote in comparison.quotes:
        if isinstance(quote, FinanceQuote):
            out.quotes.append(finance_quote_out(quote, schedule=False))
        elif isinstance(quote, ProfitShareQuote):
            out.profit_share_quotes.append(profit_share_quote_out(quote))
        elif isinstance(quote, Conversion):
            out.conversions.append(conversion_out(quote))
        elif isinstance(quote, CardInstallmentQuote):
            out.card_quotes.append(card_quote_out(quote))
    return out


def chunk_out(chunk: RetrievedChunk) -> ChunkOut:
    """A retrieved passage. The payload is read by key, never forwarded whole."""
    payload = chunk.payload
    return ChunkOut(
        score=chunk.score,
        cite_url=chunk.cite_url,
        text=chunk.text,
        bank=payload.get("bank", ""),
        title=payload.get("title", ""),
        doc_kind=payload.get("doc_kind", ""),
        source_type=payload.get("source_type", ""),
        campaign_end=payload.get("campaign_end"),
        from_vision=bool(payload.get("from_vision")),
    )


def family_list() -> list[FamilyOut]:
    """Every comparable product family, with the banks that sell it."""
    return [
        FamilyOut(
            key=key,
            label=families_mod.label(key),
            group=families_mod.group(key),
            category=category,
            # Distinct banks, not entries: Türkiye Finans holds two entries in
            # most finance families and "sold by 6 banks" must not count it
            # twice on the picker.
            banks=families_mod.banks_in(category, key),
        )
        for category, table in families_mod.BY_CATEGORY.items()
        for key in sorted(table)
    ]
