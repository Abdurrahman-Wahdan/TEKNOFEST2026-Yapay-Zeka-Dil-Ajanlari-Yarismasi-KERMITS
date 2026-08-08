"""Base class for banks."""

import functools
from abc import ABC

from .. import status
from ..http import csrf_token, request_json, request_text
from ..models import (
    CardInstallmentQuote,
    Conversion,
    FinanceQuote,
    ProfitShareQuote,
    Product,
    Rate,
)
import time

from ..parse import fold
from ..parse import term_unit as parse_term_unit

# The curl_cffi target for WAF-guarded hosts. One version for all of them: it
# has to be a profile curl_cffi actually ships, not an arbitrary string.
CHROME = "chrome124"

# What a bank may declare it publishes, and the method that answers it. Kept
# together so a capability cannot drift from the thing it promises; a unit test
# asserts every declared capability is really implemented, and that nothing is
# implemented without being declared.
CAPABILITY_METHODS = {
    "products": "products",
    "finance": "finance_quote",
    "profit_share": "profit_share_quote",
    "card": "card_installment_quote",
    "rates": "rates",
    "convert": "convert",
}

TRANSPORTS = ("httpx", "csrf", "impersonate", "none")

# What to call each capability when apologising for it.
CAPABILITY_LABELS = {
    "products": "product catalogue",
    "finance": "financing calculator",
    "profit_share": "profit-share calculator",
    "card": "card instalment calculator",
    "rates": "exchange rates",
    "convert": "currency converter",
}

# How far a requested term may sit from a bank's nearest published band before
# answering it would be answering a different question. 15% lets a month read as
# 30 or 31 days and a year as 360, 364 or 365, and refuses 12 days offered a
# 31-day band or 60 days offered a 91-day one.
BAND_TOLERANCE = 0.15

# How long a fetched catalogue stays usable. Product ranges change about as
# often as the product range, but two catalogues carry live rates inside them —
# Türkiye Finans's profit-share table and Ziraat's per-product profit rate — and
# a long-running process would otherwise serve yesterday's rate as today's.
CATALOGUE_TTL_SECONDS = 900

# A finance quote must return at least this share of one month's profit at the
# rate it reports. Measured across all six banks that quote, a 24-month plan
# returns 14-20x one month, so this is a floor with two orders of magnitude of
# headroom — it fires only on a degenerate plan, never on a real one.
FINANCE_PROFIT_FLOOR = 0.5

# How far a profit-share figure may sit from what the bank's own annual rate
# implies over the term. Measured across all six banks that quote: 0.0% off,
# every one. So this catches an order-of-magnitude contradiction, not rounding.
PROFIT_SHARE_TOLERANCE = 0.15

# The standard codes a caller uses, mapped onto the names some banks give the
# same thing in their rate feeds. Kuveyt Türk and Hayat quote gold as
# "ALT (gr)"; Albaraka quotes it as "XAU". A caller should not have to know.
RATE_ALIASES = {
    "TRY": "TL",
    "XAU": "ALT (gr)",
    "GOLD": "ALT (gr)",
    "ALTIN": "ALT (gr)",
    "XAG": "GMS (gr)",
    "SILVER": "GMS (gr)",
    "GUMUS": "GMS (gr)",
}


def refusal(method):
    """Mark an override that still refuses, only in more useful words.

    Türkiye Finans publishes a rate but never an instalment, so its
    finance_quote overrides the base only to name the rate it does state. That
    is not a capability, and marking it keeps the capability check exact
    instead of loosened.
    """
    method.is_refusal = True
    return method


class UnsupportedProduct(ValueError):
    """A bank does not offer, or does not publish a price for, what was asked.

    Subclasses ValueError deliberately: everywhere else in this project bad
    input raises ValueError with a message listing the valid alternatives, and
    this is the same thing. "This bank does not publish this" is a legitimate
    answer the agent must be able to give, so the message is written to be read
    by a person.
    """


class TemporarilyUnavailable(UnsupportedProduct):
    """The bank publishes this, but we cannot reach it right now.

    Kept apart from a plain UnsupportedProduct because the two deserve different
    answers. "This bank does not offer that" is a complete answer for a user.
    "The calculator broke this morning" is an apology, and a figure that does
    exist — so the agent should say so rather than imply the product is missing.

    Raised from the recorded health status, before any network call, so a bank
    known to be down costs nothing to ask.
    """


def _gated(capability: str, method):
    """Refuse a capability the last health check found broken.

    Applied automatically to every provider override (see __init_subclass__), so
    a bank added later cannot forget it.
    """

    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        reason = status.outage(self.name, capability)
        if reason:
            label = CAPABILITY_LABELS.get(capability, capability)
            raise TemporarilyUnavailable(
                f"{self.display_name}'s {label} is temporarily unavailable: "
                f"{reason}. The bank does publish this — it cannot be reached "
                f"right now, so no figure can be quoted. Try again later."
            )
        return method(self, *args, **kwargs)

    guarded._status_gated = True
    return guarded


class BaseBank(ABC):
    """One participation bank, behind the tools the agent binds.

    Add a bank by writing one module here and appending an instance to BANKS in
    __init__.py. No tool signature changes: `bank` is a parameter.

    Nothing is abstract. Every method below refuses by default, naming what the
    bank does publish, and a provider overrides only what its bank really
    answers. That is what lets Adil and T.O.M. be real providers with no
    endpoints rather than absent banks, and it means a gap is always a sentence
    rather than a crash or an empty result. `capabilities` is the declaration
    that keeps the two in step.
    """

    name: str = ""
    display_name: str = ""
    capabilities: frozenset[str] = frozenset()

    # How this bank has to be called. "httpx" is plain, "csrf" is httpx plus a
    # per-page anti-forgery token, "impersonate" is curl_cffi for hosts whose
    # WAF fingerprints the TLS handshake, and "none" is a bank with nothing to
    # call. The health checker reads this to know which banks are cheap to poll.
    transport: str = "httpx"

    # Why a bank publishes less than the others, in a sentence a user could
    # read. Only worth setting where the reason is not obvious from
    # capabilities, and required where capabilities is empty.
    notes: str = ""

    # Standard code -> this bank's own name for the same currency or metal.
    # Empty where the bank already uses standard codes.
    rate_aliases: dict[str, str] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        """Wrap every capability this provider implements in the status gate.

        Doing it here rather than in each provider means the check cannot be
        left out of a bank added later, and providers stay free of plumbing they
        would only get wrong once.
        """
        super().__init_subclass__(**kwargs)
        for capability, method_name in CAPABILITY_METHODS.items():
            method = cls.__dict__.get(method_name)
            if method is None or getattr(method, "_status_gated", False):
                continue
            setattr(cls, method_name, _gated(capability, method))

    def __init__(self) -> None:
        # Fetched catalogues, with the time each was fetched. Cleared by
        # clear_catalogue_cache() and expired by CATALOGUE_TTL_SECONDS.
        self._catalogue: dict[str, list[Product]] = {}
        self._fetched_at: dict[str, float] = {}
        # Anything a provider wants to keep that is not a product list — a
        # page's HTML, a currency-id map. Kept separate so the catalogue stays
        # what its type says it is.
        self._scratch: dict = {}

    def _cached(self, key: str) -> list[Product] | None:
        """A catalogue still inside its time to live, or None."""
        fetched = self._fetched_at.get(key)
        if fetched is None or time.monotonic() - fetched > CATALOGUE_TTL_SECONDS:
            return None
        return self._catalogue.get(key)

    def _store(self, key: str, products: list[Product]) -> list[Product]:
        self._catalogue[key] = products
        self._fetched_at[key] = time.monotonic()
        return products

    # ----- transport -----

    @property
    def impersonate(self) -> str | None:
        """The curl_cffi profile this bank needs, or None for plain httpx."""
        return CHROME if self.transport == "impersonate" else None

    def _json(self, method: str, url: str, **kwargs):
        return request_json(method, url, impersonate=self.impersonate, **kwargs)

    def _text(self, url: str, **kwargs) -> str:
        return request_text(url, impersonate=self.impersonate, **kwargs)

    def _token(self, page_url: str, **kwargs) -> str:
        return csrf_token(page_url, impersonate=self.impersonate, **kwargs)

    # ----- what a bank can answer -----

    def products(self, category: str) -> list[Product]:
        """Every product the bank prices in a category, from its own catalogue.

        Categories: finance, profit_share, card.
        """
        raise UnsupportedProduct(
            f"{self.display_name} publishes no {category} catalogue. "
            + self._what_it_does()
        )

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        """Instalment plan for a financing product. `product` is a code or a name."""
        raise self._unsupported("a financing calculator")

    def profit_share_quote(
        self,
        product: str,
        amount: float,
        term: int,
        currency: str = "TRY",
        term_unit: str | None = None,
    ) -> ProfitShareQuote:
        """Profit share on a participation account.

        `term_unit` is "day" or "month". Left as None the bank is asked in the
        unit its own product takes, and the quote reports which one answered.
        """
        raise self._unsupported("a profit-share calculator")

    def rates(self) -> list[Rate]:
        """The bank's published FX and precious-metal rates."""
        raise self._unsupported("exchange rates")

    def card_installment_quote(
        self, card: str, amount: float, installments: int
    ) -> CardInstallmentQuote:
        """Instalment plan for a credit card purchase."""
        raise self._unsupported("a credit card instalment calculator")

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert between currencies, or price grams of a precious metal."""
        raise self._unsupported("a currency converter")

    # ----- shared helpers -----

    def find_product(self, category: str, query: str) -> Product:
        """Resolve a product code or a Turkish product name to a Product.

        Users ask for "ihtiyaç finansmanı", not SAGLIKFINANSMANI, and that
        mapping is bank-specific, so the model is never asked to know it.

        Raises:
            UnsupportedProduct: if nothing matches, or a name matches several
                products, listing what this bank actually offers.
        """
        available = self.products(category)
        wanted = fold(query)
        if not wanted:
            raise self._no_such_product(query, category, available)

        for match in (
            [p for p in available if fold(p.code) == wanted],
            [p for p in available if fold(p.name) == wanted],
        ):
            if len(match) == 1:
                return match[0]
            if len(match) > 1:
                # Kuveyt Türk lists ELKTRARACSARJUNITE twice, as Bisiklet
                # Finansmanı and Elektrikli Araç Şarj Ünitesi, with different
                # real term limits. Returning the first would make the second
                # unreachable and quote the wrong limits for it.
                names = ", ".join(p.name for p in match)
                raise UnsupportedProduct(
                    f"{self.display_name} lists {query!r} more than once: "
                    f"{names}. Ask for one of them by name."
                )

        partial = [p for p in available if wanted in fold(p.name)]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(p.name for p in partial)
            raise UnsupportedProduct(
                f"{query!r} matches several {self.display_name} products: {names}. "
                f"Ask for one of them by name."
            )
        raise self._no_such_product(query, category, available)

    def find_rates(self, codes=None) -> list[Rate]:
        """The bank's rates, optionally filtered by standard currency codes.

        Filtering has to go through the alias map. Kuveyt Türk and Hayat call
        gold "ALT (gr)", so a plain uppercase comparison against "XAU" matches
        nothing and the answer reads as "this bank does not quote gold" when it
        quotes it on the row above.
        """
        rows = self.rates()
        if not codes:
            return rows
        by_code = {row.code.upper(): row for row in rows}
        found = []
        for code in codes:
            wanted = self.rate_aliases.get(code.upper(), code).upper()
            row = by_code.get(wanted)
            if row is not None and row not in found:
                found.append(row)
        return found

    def _check_quote(self, quote: FinanceQuote) -> FinanceQuote:
        """Refuse a finance quote that does not add up.

        Checked in the base layer, once, rather than found per bank against a
        live endpoint. Ziraat on an over-ceiling band returned a 200 000,16
        total for a 200 000 loan — 0,16 TL of profit, every schedule row
        principal-only — while reporting a 4,99% monthly rate. Nothing about
        that response was an error; it was simply not an answer.

        These are consistency checks on the bank's own numbers against each
        other. Nothing here computes a price or substitutes one.

        Raises:
            UnsupportedProduct: naming which of the bank's figures disagree.
        """
        if quote.installment <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment for "
                f"{quote.product.name}."
            )
        if quote.total <= quote.amount:
            raise UnsupportedProduct(
                f"{self.display_name} returned a total of {quote.total:,.2f} for "
                f"a {quote.amount:,.2f} advance on {quote.product.name}, which "
                f"is not more than what is borrowed. That is not a quote."
            )
        if quote.schedule and len(quote.schedule) != quote.term:
            raise UnsupportedProduct(
                f"{self.display_name} returned {len(quote.schedule)} payment "
                f"rows for a {quote.term}-month plan on {quote.product.name}. "
                f"The plan does not match the term."
            )
        # A month at the reported rate is the loosest possible floor: whatever
        # the bank's method, N months cannot be worth less than a fraction of
        # one of them.
        if quote.profit_rate > 0:
            one_month = quote.amount * quote.profit_rate / 100
            if (quote.total - quote.amount) < one_month * FINANCE_PROFIT_FLOOR:
                raise UnsupportedProduct(
                    f"{self.display_name} reported a {quote.profit_rate}% monthly "
                    f"rate on {quote.product.name} but a total only "
                    f"{quote.total - quote.amount:,.2f} above the advance over "
                    f"{quote.term} months. Its own figures contradict each other, "
                    f"so this is not a price to quote."
                )
        return quote

    def _check_profit_share(self, quote: ProfitShareQuote) -> ProfitShareQuote:
        """Refuse a profit-share figure that its own rate does not imply.

        Every bank that quotes agrees with itself to within rounding, so a real
        disagreement means the figure answers a different question — Hayat's
        daily account returns one day's profit whatever term is sent.

        Raises:
            UnsupportedProduct: naming the figures that disagree.
        """
        if quote.net_profit <= 0:
            raise self._no_rate(
                quote.product, quote.amount, quote.currency,
                f"{quote.term} {quote.term_unit}s",
            )
        if quote.gross_profit and quote.gross_profit < quote.net_profit:
            raise UnsupportedProduct(
                f"{self.display_name} reported a net profit above the gross for "
                f"{quote.product.name}, which cannot be right."
            )
        annual = quote.net_annual_rate or 0
        days = quote.term * 30 if quote.term_unit == "month" else quote.term
        implied = quote.amount * (annual / 100) * (days / 365)
        if implied and abs(quote.net_profit - implied) / implied > PROFIT_SHARE_TOLERANCE:
            raise UnsupportedProduct(
                f"{self.display_name} returned {quote.net_profit:,.2f} for "
                f"{quote.product.name} over {days} days, which does not follow "
                f"from its own stated annual rate of {annual}%. The figure does "
                f"not answer the term that was asked about."
            )
        return quote

    def _check_limits(
        self,
        product: Product,
        *,
        amount: float | None = None,
        term: int | None = None,
        term_label: str = "months",
    ) -> None:
        """Refuse what the bank's own catalogue says it will not price.

        Only what a bank actually declares is checked. Vakıf and Emlak publish
        no amount ceiling anywhere, so nothing here can invent one for them —
        but where a bank states a limit and then answers past it anyway, the
        answer is not one it would honour. Dünya declares a 12 000 000 ceiling
        and still quotes 50 000 000, and Ziraat's bands cost more than that: on
        a 124 999 band it returns a 200 000 "quote" whose every schedule row is
        principal-only, 0,16 TL of profit, while reporting a 4,99% rate.

        Raises:
            UnsupportedProduct: naming the limit the bank published.
        """
        if amount is not None and amount <= 0:
            raise UnsupportedProduct(
                f"An amount has to be a positive number of currency units. "
                f"Got {amount:,.2f}."
            )
        if term is not None and term <= 0:
            raise UnsupportedProduct(
                f"A term has to be a positive number of {term_label}. Got {term}."
            )
        if amount is not None:
            if product.max_amount and amount > product.max_amount:
                raise UnsupportedProduct(
                    f"{product.name} is capped at {product.max_amount:,.0f} by "
                    f"{self.display_name}; {amount:,.0f} is above it, so any "
                    f"figure for it would not be one the bank would honour."
                )
            if product.min_amount and amount < product.min_amount:
                raise UnsupportedProduct(
                    f"{product.name} starts at {product.min_amount:,.0f} at "
                    f"{self.display_name}; {amount:,.0f} is below it."
                )
        if term is not None:
            if product.max_term and term > product.max_term:
                raise UnsupportedProduct(
                    f"{product.name} runs to {product.max_term} {term_label} at "
                    f"{self.display_name}; {term} is beyond it."
                )
            if product.min_term and term < product.min_term:
                raise UnsupportedProduct(
                    f"{product.name} starts at {product.min_term} {term_label} "
                    f"at {self.display_name}; {term} is below it."
                )

    def _band(self, days: int, bands) -> int:
        """The published term band that actually answers `days`.

        Several banks price a fixed list of terms rather than any day count,
        and that list is labelled in months: 31 is "aylık", 364 is "yıllık".

        Taking the nearest band *at or below* the request looks reasonable and
        is badly wrong. A year is 360 days, which falls short of 364 and lands
        on the six-month band — returning 44% of the right figure as a
        confident, well-formed quote, with only `term: 180` to hint at it. That
        is the same failure as returning a zero: a plausible number answering a
        question nobody asked.

        So the nearest band wins in either direction, a request past the last
        band takes it (the last band is the bank's open-ended "and above"), and
        a term no band comes close to is refused rather than quietly swapped.

        Raises:
            UnsupportedProduct: if no band is within BAND_TOLERANCE.
        """
        usable = sorted({int(b) for b in bands if b})
        if not usable or days <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} publishes no term bands to price "
                f"{days} days against."
            )
        if days >= usable[-1]:
            return usable[-1]

        nearest = min(usable, key=lambda band: (abs(band - days), band))
        if abs(nearest - days) > BAND_TOLERANCE * days:
            offered = ", ".join(str(b) for b in usable)
            raise UnsupportedProduct(
                f"{self.display_name} prices fixed terms only: {offered} days. "
                f"{days} days is not one of them, and the nearest it publishes "
                f"({nearest} days) is too far away to answer as if it were. Ask "
                f"for one of the terms it offers."
            )
        return nearest

    def _what_it_does(self) -> str:
        if self.capabilities:
            published = ", ".join(sorted(self.capabilities))
            return f"It publishes: {published}." + (f" {self.notes}" if self.notes else "")
        return self.notes or "It publishes no public calculator."

    def _require_unit(self, term: int, term_unit) -> str:
        """The term unit, insisted upon rather than guessed.

        These endpoints mostly count days, and the banks do not agree on what a
        bare number means: the same `12` is twelve days at one bank and twelve
        months at another, answers that sit about thirty times apart. Both look
        equally plausible in a reply. So a missing unit is refused here rather
        than resolved by a default that is right for one bank and wrong for the
        next.

        Raises:
            UnsupportedProduct: if no unit was given.
            ValueError: if the unit is neither days nor months.
        """
        unit = parse_term_unit(term_unit)
        if unit is None:
            raise UnsupportedProduct(
                f"Say whether {term} means days or months. {self.display_name} "
                f"prices participation accounts by the day, so the two readings "
                f"differ by about thirty times and neither will be assumed."
            )
        return unit

    def _unsupported(self, what: str) -> UnsupportedProduct:
        return UnsupportedProduct(
            f"{self.display_name} does not publish {what}. " + self._what_it_does()
        )

    def _no_such_product(
        self, query: str, category: str, available: list[Product]
    ) -> UnsupportedProduct:
        names = ", ".join(f"{p.name} ({p.code})" for p in available)
        return UnsupportedProduct(
            f"{self.display_name} has no {category} product matching {query!r}. "
            f"Available: {names}."
        )

    def _no_rate(self, product: Product, amount: float, currency: str, term: str) -> UnsupportedProduct:
        """The refusal for a bank that answers "not offered" with zeros.

        Six of the ten do this, and returning it as a quote would report a real
        product as paying nothing.
        """
        return UnsupportedProduct(
            f"{self.display_name} published no profit-share rate for "
            f"{product.name} at {amount:,.0f} {currency} over {term}. The bank "
            f"answers with zeros for combinations it does not offer."
        )
