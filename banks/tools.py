"""The tools the agent binds.

    from llm import get_llm
    from banks import build_tools

    model = get_llm().bind_tools(build_tools())

There is one tool per product category and `bank` is a parameter, so adding a
bank adds no tool and changes no signature. Ten banks with per-bank tool names
would be forty-plus tools and an unusable prompt.

Docstrings here are prompt text: the model reads them to choose. They are
written in English, say what the tool returns, and are filled in with the live
bank list at import time so a new bank needs no edit in this file.

Every tool returns compact JSON, or a plain sentence when a bank does not
publish what was asked for. The agent writes the prose.
"""

import json
import logging
from typing import Callable

from langchain_core.tools import BaseTool, tool

from . import compare, families
from .factory import get_bank
from .factory import list_banks as _list_banks
from .models import (
    CardInstallmentQuote,
    Conversion,
    FinanceQuote,
    ProfitShareQuote,
    Product,
    Rate,
)

logger = logging.getLogger(__name__)


def _answer(build: Callable[[], object]) -> str:
    """Run a lookup and render it, turning a refusal into a sentence.

    A traceback ends the agent's turn. "This bank does not publish this" is a
    legitimate answer it should be able to pass on, so UnsupportedProduct and
    every other ValueError come back as readable text.
    """
    try:
        return json.dumps(build(), ensure_ascii=False, separators=(",", ":"))
    except ValueError as exc:
        logger.debug("Tool refused: %s", exc)
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - the agent must never see a traceback
        # A refusal is a ValueError and says something useful. Anything else is
        # our bug — a bank changed its shape under us — and it must still not
        # end the agent's turn, so it is logged loudly and answered plainly.
        logger.exception("Tool failed unexpectedly")
        return (
            f"That lookup failed unexpectedly ({type(exc).__name__}). The bank's "
            f"response was not the shape this tool expects, which usually means "
            f"its page or endpoint has changed."
        )


def _product(product: Product) -> dict:
    """A product as the model sees it, without the fields the bank left blank.

    Several banks publish only some limits, and Türkiye Finans alone returns 55
    rate rows; carrying nulls for all of them is prompt weight for no meaning.
    """
    fields = {
        "code": product.code,
        "name": product.name,
        "min_amount": product.min_amount,
        "max_amount": product.max_amount,
        "min_term": product.min_term,
        "max_term": product.max_term,
        "currencies": list(product.currencies),
        "rate": product.rate,
    }
    return {k: v for k, v in fields.items() if v is not None}


def _payment_row(row) -> dict:
    return {
        "no": row.order,
        "due": row.due_date,
        "amount": row.amount,
        "principal": row.principal,
        "profit": row.profit,
        "taxes": row.taxes,
        "remaining": row.remaining,
    }


def _finance(quote: FinanceQuote, schedule: bool = False) -> dict:
    rows = {"payment_schedule": [_payment_row(r) for r in quote.schedule]} if schedule else {}
    return {
        "bank": quote.bank,
        "product": quote.product.name,
        "code": quote.product.code,
        "amount": quote.amount,
        "term_months": quote.term,
        "monthly_installment": quote.installment,
        "total_payable": quote.total,
        "monthly_profit_rate": quote.profit_rate,
        "annual_cost_rate": quote.annual_cost_rate,
        "fees": quote.fees,
        "schedule_rows": len(quote.schedule),
        **rows,
    }


def _profit_share(quote: ProfitShareQuote) -> dict:
    return {
        "bank": quote.bank,
        "product": quote.product.name,
        "code": quote.product.code,
        "amount": quote.amount,
        "currency": quote.currency,
        "term": quote.term,
        "term_unit": quote.term_unit,
        "participation_ratio": quote.ratio,
        "gross_profit": quote.gross_profit,
        "net_profit": quote.net_profit,
        "gross_annual_rate": quote.gross_annual_rate,
        "net_annual_rate": quote.net_annual_rate,
    }


def _card(quote: CardInstallmentQuote) -> dict:
    return {
        "bank": quote.bank,
        "card": quote.card.name,
        "code": quote.card.code,
        "amount": quote.amount,
        "installments": quote.installments,
        "monthly_installment": quote.installment,
        "total_payable": quote.total,
        "profit_rate": quote.profit_rate,
    }


def _rate(row: Rate) -> dict:
    fields = {
        "code": row.code,
        "name": row.name,
        "buy": row.buy,
        "sell": row.sell,
        "unit": row.unit,
        "as_of": row.as_of,
    }
    return {k: v for k, v in fields.items() if v not in (None, "")}


def _conversion(result: Conversion) -> dict:
    return {
        "bank": result.bank,
        "source": result.source,
        "target": result.target,
        "amount": float(result.amount),
        "result": float(result.result),
        "rate": float(result.rate),
        "derived": result.derived,
        "note": (
            "Derived by multiplying the bank's quoted rate; the bank does not "
            "publish a converter."
            if result.derived
            else "Converted by the bank."
        ),
    }


@tool
def list_banks() -> str:
    """List every participation bank that can be queried and what each publishes.

    Returns each bank's name, display name, what it publishes — drawn from
    products, finance, profit_share, card, rates, convert — and a note
    explaining any gap. A bank missing a capability does not publish that thing;
    that is a real answer to give a user, not an error. Two of the banks publish
    no calculator at all and are listed with nothing under "publishes".

    Call this first when you do not know which bank can answer something.
    """
    return _answer(_list_banks)


@tool
def list_products(bank: str, category: str) -> str:
    """List a bank's products in a category, with its amount and term limits.

    Use this to discover the Turkish product names and codes a bank offers
    before asking for a quote — product names differ per bank and so do the
    limits. Valid banks: {banks}. Valid categories: finance (finansman),
    profit_share (katilma hesabi / kar payi), card (kredi karti taksit); not
    every bank has every category.

    Returns each product's code, Turkish name, amount limits, term limits,
    currencies and the rate where the bank states one in its catalogue. Term
    limits are months for finance and days for participation accounts.
    """
    return _answer(
        lambda: [_product(p) for p in get_bank(bank).products(category)]
    )


@tool
def finance_quote(
    bank: str,
    product: str,
    amount: float,
    term_months: int,
    include_schedule: bool = False,
) -> str:
    """Get a financing instalment quote from a bank's own calculator.

    Use for questions about finansman, ihtiyac finansmani, konut finansmani,
    arac/tasit finansmani, taksit. Valid banks: {banks}. `product` accepts the
    Turkish product name as it appears in list_products, or the product code;
    you do not need to know the code. `amount` is in Turkish lira, and
    `term_months` is in months because financing always is — "1 yil" is 12.

    A bank often lists several variants of the same product with different
    rates. If the request does not uniquely identify one, do not guess: call
    list_products to see the exact names, pass the one that matches, and if the
    user's wording still fits more than one, ask them which they mean rather than
    picking a default. To compare the same variant across banks, use
    compare_finance with a family key.

    Returns the monthly instalment, total payable, monthly profit rate, annual
    cost rate and fees, as the bank calculates them.

    Set `include_schedule` only when the user asks to see the payment plan —
    "odeme plani", "taksit tablosu". It adds one row per instalment with the
    due date, principal, profit and taxes, which is a lot of text for a
    question about the monthly figure alone. Leave it off and the answer states
    how many rows the plan has.
    """
    return _answer(
        lambda: _finance(
            get_bank(bank).finance_quote(product, amount, term_months),
            include_schedule,
        )
    )


@tool
def profit_share_quote(
    bank: str,
    product: str,
    amount: float,
    term_months: int = 0,
    term_days: int = 0,
    currency: str = "TRY",
) -> str:
    """Get a participation-account profit-share quote from a bank.

    Use for questions about kar payi, katilma hesabi, vadeli hesap, getiri.
    Valid banks: {banks}. `product` accepts the Turkish account name from
    list_products or its code. `currency` is TRY, USD, EUR, XAU or GBP,
    depending on the account.

    Give the term in exactly one of `term_months` or `term_days`, never both and
    never neither. Use `term_months` when the user says ay or yil ("1 yil" is
    12), and `term_days` when the user says gun. The unit is in the field name
    because these banks disagree about a bare number: the same 12 means twelve
    days at one bank and twelve months at another, answers roughly thirty times
    apart, and neither will be guessed.

    Returns the participation ratio where the bank publishes one, the gross and
    net profit, and the gross and net annual rates, along with the term and unit
    the bank actually priced — banks round a month to 30 or 31 days and only
    price certain terms, so check that against what was asked. A bank that does
    not offer the requested combination answers with a sentence saying so.
    """
    def build():
        if bool(term_months) == bool(term_days):
            raise ValueError(
                "Give the term in exactly one of term_months or term_days. "
                f"Got term_months={term_months}, term_days={term_days}. "
                "These banks price by the day and disagree about what a bare "
                "number means, so the unit has to be stated."
            )
        term = term_months or term_days
        unit = "month" if term_months else "day"
        return _profit_share(
            get_bank(bank).profit_share_quote(product, amount, term, currency, unit)
        )

    return _answer(build)


@tool
def exchange_rates(bank: str, codes: list[str] | None = None) -> str:
    """Get a bank's published foreign-exchange and precious-metal rates.

    Use for questions about doviz kuru, dolar, euro, altin, gumus. Valid banks:
    {banks}, though several publish a converter without a rate feed and answer
    with a sentence saying so. `codes` optionally filters to specific currency
    codes; leave it out to get everything the bank quotes. Standard codes work
    even where a bank names things its own way — XAU finds gold at a bank that
    calls it "ALT (gr)".

    Returns the buy and sell rate for each, with the unit the rate is quoted in
    ("gram" for metals). To turn a rate into an amount, use convert_currency
    rather than multiplying it yourself.
    """
    return _answer(lambda: [_rate(r) for r in get_bank(bank).find_rates(codes)])


@tool
def card_installment_quote(
    bank: str, card: str, amount: float, installments: int
) -> str:
    """Get a credit-card instalment quote from a bank's own calculator.

    Use for questions about kredi karti taksit, kart taksitlendirme. Valid
    banks: {banks}, but most publish no card calculator — those answer with a
    sentence saying so. `card` accepts the Turkish
    card name from list_products with category "card", or its code.

    Returns the monthly instalment, total payable and profit rate. If the bank
    refuses the instalment count, ask for fewer: some banks advertise more
    instalments than their calculator accepts.
    """
    return _answer(
        lambda: _card(
            get_bank(bank).card_installment_quote(card, amount, installments)
        )
    )


@tool
def convert_currency(bank: str, source: str, target: str, amount: float) -> str:
    """Convert an amount between currencies, or price grams of gold or silver.

    Use for questions like "1000 dolar kac TL" or "10 gram altin kac TL". Valid
    banks: {banks}, and not all of them convert. `source` and `target` are
    currency codes such as TRY, USD, EUR, GBP, or XAU for gold, quoted per gram.

    Returns the converted amount, the rate used, and a "derived" flag. When
    derived is true the bank publishes no converter and the figure comes from
    multiplying its quoted rate, so present it as based on the quoted rate
    rather than as the bank's own calculation.
    """
    return _answer(
        lambda: _conversion(get_bank(bank).convert(source, target, amount))
    )


def _mile(row) -> dict:
    return {
        "card": row.card,
        "tier": row.tier,
        "category": row.category,
        "miles_per_lira": row.per_lira,
    }


@tool
def mile_earning_rates(
    bank: str, card: str | None = None, category: str | None = None
) -> str:
    """Get a card loyalty programme's mile/point earning rates per category.

    Use for questions like "hangi kartla akaryakitta kac mil kazanirim",
    "market harcamasinda mil orani", "Miles&Smiles kazanim oranlari". Valid
    banks: {banks}, but most publish no reward table and answer with a sentence
    saying so. `card` optionally filters to one card (e.g. "Platin"); `category`
    optionally filters to one spending category (e.g. akaryakit, market,
    yurtdisi, seyahat, restoran, thy, giyim, eticaret). Leave them out for the
    whole table.

    Returns one row per card, membership tier and category with miles-per-lira:
    0.06 means 6 miles per 100 TL. To rank cards for a category, filter by that
    category and compare miles_per_lira.
    """
    def build():
        rows = get_bank(bank).mile_rates()
        if card:
            rows = [r for r in rows if card.lower() in r.card.lower()]
        if category:
            rows = [r for r in rows if category.lower() in r.category.lower()]
        return [_mile(r) for r in rows]

    return _answer(build)


def _ranked(comparison, row, key, best_label: str) -> dict:
    """Render a comparison: winner first, and everyone who is not in it.

    `key` sorts the rows and decides the winner. Keys identical on every row are
    hoisted out of the rows, because eight copies of the same amount is prompt
    weight that buys nothing.
    """
    rows = sorted((row(q) for q in comparison.quotes), key=key)
    shared = {}
    for field in ("amount", "term_months", "term", "term_unit", "currency"):
        values = {r.get(field) for r in rows}
        if len(values) == 1 and rows and rows[0].get(field) is not None:
            shared[field] = rows[0][field]
            for r in rows:
                r.pop(field, None)

    answer = {
        "family": comparison.family,
        **shared,
        "compared": comparison.in_scope,
        "ranked": rows,
    }
    if rows:
        answer[best_label] = rows[0]["bank"]
    if comparison.unavailable:
        answer["not_compared"] = [
            {"bank": u.bank, "why": u.why, "detail": u.detail}
            for u in comparison.unavailable
        ]
    return answer


@tool
def compare_finance(
    family: str, amount: float, term_months: int, banks: list[str] | None = None
) -> str:
    """Compare one financing product across every bank at once, cheapest first.

    Use this for "hangi banka en ucuz", "en uygun", "karsilastir" — any question
    about which bank is best, rather than what one bank charges. It asks every
    bank in parallel, so it is faster and cheaper than calling finance_quote
    once per bank.

    `family` names a product family, not a free-text product name, because
    banks name the same product differently and one name resolves at almost
    none of the others. Valid families: {finance_families}. Turkish words are
    understood where they are unambiguous; "konut" and "tasit" are not, and the
    answer will tell you which two to choose between. `amount` is Turkish lira
    and `term_months` is months. `banks` optionally narrows the comparison;
    leave it out to cover every bank.

    Returns "ranked", cheapest monthly instalment first, and "cheapest" naming
    that bank. Every bank that is not in the ranking appears under
    "not_compared" with a reason: not_offered (this bank does not sell it),
    declined (asked, and it said no — usually the amount or term is outside its
    limits), maintenance (it sells this but cannot be reached right now), or
    error. No bank is ever silently missing, and a bank that does not offer the
    product is itself worth telling the user.
    """
    return _answer(lambda: _ranked(
        compare.finance(family, amount, term_months, banks),
        lambda q: _finance(q),
        key=lambda r: r["monthly_installment"],
        best_label="cheapest",
    ))


@tool
def compare_profit_share(
    family: str,
    amount: float,
    term_months: int = 0,
    term_days: int = 0,
    currency: str = "TRY",
    banks: list[str] | None = None,
) -> str:
    """Compare a participation account across every bank, best return first.

    Use for "hangi bankada kar payi daha yuksek", "en cok getiri". Valid
    families: {profit_share_families}. `currency` is TRY, USD, EUR or XAU.

    Give the term in exactly one of `term_months` or `term_days`, never both
    and never neither — the unit is in the field name because these banks
    disagree about a bare number.

    Returns "ranked", highest net profit first, and "best" naming that bank.
    Banks price a term against their own bands, so each row reports the term
    the bank actually used; when they differ the answer says so, because a
    364-day figure and a 366-day one are not the same question. Banks not in
    the ranking appear under "not_compared" with a reason.
    """
    def build():
        if bool(term_months) == bool(term_days):
            raise ValueError(
                "Give the term in exactly one of term_months or term_days. "
                f"Got term_months={term_months}, term_days={term_days}."
            )
        term = term_months or term_days
        unit = "month" if term_months else "day"
        result = compare.profit_share(family, amount, term, unit, currency, banks)
        answer = _ranked(
            result, lambda q: _profit_share(q),
            key=lambda r: -r["net_profit"], best_label="best",
        )
        priced = {(q.term, q.term_unit) for q in result.quotes}
        if len(priced) > 1:
            answer["terms_differ"] = True
            answer["note"] = (
                "The banks priced different terms: each snaps the request to a "
                "term it publishes. Compare the term on each row before "
                "reading the ranking as like for like."
            )
        return answer

    return _answer(build)


@tool
def compare_exchange(
    source: str, target: str, amount: float, banks: list[str] | None = None
) -> str:
    """Compare what an amount is worth at every bank that converts currency.

    Use for "1000 dolar hangi bankada en iyi", "en iyi kur". `source` and
    `target` are codes such as TRY, USD, EUR, GBP, or XAU for gold per gram.

    Returns "ranked", most received first, and "best" naming that bank. Each
    row carries "derived": when it is true the bank publishes no converter and
    the figure comes from multiplying its quoted rate, so present it as based
    on the rate rather than as the bank's own calculation.
    """
    def build():
        result = compare.exchange(source, target, amount, banks)
        rows = sorted(
            (_conversion(c) for c in result.quotes),
            key=lambda r: -r["result"],
        )
        answer = {
            "source": source.upper(), "target": target.upper(), "amount": amount,
            "compared": result.in_scope, "ranked": rows,
        }
        if rows:
            answer["best"] = rows[0]["bank"]
        if result.unavailable:
            answer["not_compared"] = [
                {"bank": u.bank, "why": u.why, "detail": u.detail}
                for u in result.unavailable
            ]
        return answer

    return _answer(build)


@tool
def check_bank_health(bank: str = "") -> str:
    """Check that a bank's calculators are answering right now.

    Use this when a quote failed with a technical problem, or when the user says
    a figure looks wrong or that something is broken — it tells you whether the
    bank is reachable rather than leaving you to guess. Valid banks: {banks};
    leave `bank` empty to check every one, which takes longer.

    This calls the bank's live endpoints, so use it to diagnose a problem, not
    before every ordinary question.

    Returns each capability with ok, down, or known. "known" means the bank
    answered and said it does not offer that combination — that is not a fault.
    A capability found down is remembered, so later quotes refuse quickly and
    honestly instead of failing again.
    """
    def build():
        from .health import run

        report = run(banks=[bank] if bank else None)
        return report.as_dict()

    return _answer(build)


_TOOLS: list[BaseTool] = [
    list_banks,
    list_products,
    finance_quote,
    profit_share_quote,
    exchange_rates,
    card_installment_quote,
    convert_currency,
    mile_earning_rates,
    compare_finance,
    compare_profit_share,
    compare_exchange,
    check_bank_health,
]

# Fill the bank list into the descriptions once, so adding a bank stays one new
# provider module plus one list entry, with nothing to edit here.
_NAMES = ", ".join(_list_banks())
_FINANCE_FAMILIES = ", ".join(families.families("finance"))
_PROFIT_FAMILIES = ", ".join(families.families("profit_share"))
for _tool in _TOOLS:
    _tool.description = _tool.description.format(
        banks=_NAMES,
        finance_families=_FINANCE_FAMILIES,
        profit_share_families=_PROFIT_FAMILIES,
    )


def build_tools() -> list[BaseTool]:
    """Every bank tool, ready for get_llm().bind_tools()."""
    return list(_TOOLS)
