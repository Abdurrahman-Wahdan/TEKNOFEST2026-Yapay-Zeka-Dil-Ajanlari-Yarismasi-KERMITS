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
    term: int,
    include_schedule: bool = False,
) -> str:
    """Get a financing instalment quote from a bank's own calculator.

    Use for questions about finansman, ihtiyac finansmani, konut finansmani,
    arac/tasit finansmani, taksit. Valid banks: {banks}. `product` accepts the
    Turkish product name as it appears in list_products, or the product code;
    you do not need to know the code. `amount` is in Turkish lira and `term` is
    in months.

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
            get_bank(bank).finance_quote(product, amount, term), include_schedule
        )
    )


@tool
def profit_share_quote(
    bank: str,
    product: str,
    amount: float,
    term: int,
    currency: str = "TRY",
    term_unit: str = "",
) -> str:
    """Get a participation-account profit-share quote from a bank.

    Use for questions about kar payi, katilma hesabi, vadeli hesap, getiri.
    Valid banks: {banks}. `product` accepts the Turkish account name from
    list_products or its code. `currency` is TRY, USD, EUR, XAU or GBP,
    depending on the account.

    Always set `term_unit`: "month" when the user says ay, "day" when the user
    says gun. It matters — some banks price these accounts in days only, so an
    unqualified 12 is read as 12 days and returns a much smaller profit than 12
    months would. The answer reports the term and unit actually used; check them
    against what was asked.

    Returns the participation ratio where the bank publishes one, the gross and
    net profit, and the gross and net annual rates. A bank that does not offer
    the requested combination answers with a sentence saying so.
    """
    return _answer(
        lambda: _profit_share(
            get_bank(bank).profit_share_quote(
                product, amount, term, currency, term_unit or None
            )
        )
    )


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


_TOOLS: list[BaseTool] = [
    list_banks,
    list_products,
    finance_quote,
    profit_share_quote,
    exchange_rates,
    card_installment_quote,
    convert_currency,
]

# Fill the bank list into the descriptions once, so adding a bank stays one new
# provider module plus one list entry, with nothing to edit here.
_NAMES = ", ".join(_list_banks())
for _tool in _TOOLS:
    _tool.description = _tool.description.format(banks=_NAMES)


def build_tools() -> list[BaseTool]:
    """Every bank tool, ready for get_llm().bind_tools()."""
    return list(_TOOLS)
