"""Türkiye Finans.

SharePoint services under `/_vti_bin/<Service>.svc/<Method>`, answering plain
httpx with no token, no session and no WAF.

**This bank publishes tables, not answers.** Its calculators fetch a rate-and-fee
table once on page load and then do the instalment arithmetic in the browser;
pressing Hesapla fires no request, so there is no computed figure to read back.
Turning its rate into a payment would mean running the annuity ourselves, which
the project rule forbids — so the quote methods refuse, and refuse usefully, by
naming the rate the bank does publish for the term asked about.

Everything it really states is available through `products`: the finance
products with their per-term profit rates, allocation fee and BSMV, and the
kâr payı rate table with its minimum amounts and day bands.

Contract in docs/discovery/captured/turkiyefinans.md, exercised by
verify_turkiyefinans.py.
"""

import logging
import re

from ..models import FinanceQuote, Product, Rate
from ..parse import money, rate
from .base import BaseBank, UnsupportedProduct, refusal

logger = logging.getLogger(__name__)

HOST = "https://www.turkiyefinans.com.tr"
SERVICE = f"{HOST}/_vti_bin/TurkiyeFinansServices/FrontEndService.svc"
PAGE = f"{HOST}/tr-tr/hesaplama-araclari/Sayfalar/hesaplama-araclari.aspx"

HEADERS = {"accept": "application/json", "referer": PAGE}

# The board quotes gold and silver twice: an ounce price in dollars and a gram
# price in lira. Only the gram rows line up with what the other banks publish.
_GRAM_CODES = {"YAU": "ALT (gr)", "YAG": "GMS (gr)"}


def _ms_date(value) -> str:
    """`/Date(1786798759996+0300)/` -> an ISO stamp, or "" if it is not one."""
    match = re.search(r"/Date\((\d+)", str(value or ""))
    if not match:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(match.group(1)) / 1000, timezone.utc).isoformat()

# Rate tables are published per account group and customer type. Group 4 is the
# short-term band and group 1 the standard one; group 2 publishes nothing on
# either side, and group 4 Ticari publishes nothing either.
RATE_TABLES = ((4, "Bireysel"), (1, "Bireysel"), (1, "Ticari"))


class TurkiyeFinans(BaseBank):
    name = "turkiyefinans"
    display_name = "Türkiye Finans Katılım Bankası"
    # "finance" is here because the bank does publish a per-term profit rate,
    # annual cost rate and fees for all 18 products -- enough to rank it beside
    # everyone else. What it never publishes is the instalment, so the quote
    # comes back with installment=None rather than a figure we invented.
    # "profit_share" stays out: there the bank states only a ratio, and a
    # profit amount is nothing but arithmetic on the amount asked for.
    capabilities = frozenset({"products", "finance", "rates"})
    notes = (
        "It publishes rate and fee tables but computes nothing: its own "
        "calculator does the arithmetic in the browser. Financing comes back "
        "with the bank's published rate and no instalment. Participation "
        "accounts come back as a ratio only, through list_products."
    )

    def _service(self, method: str, result_key: str) -> list[dict]:
        payload = self._json("GET", f"{SERVICE}/{method}", headers=HEADERS) or {}
        return ((payload.get(result_key) or {}).get("Data")) or []

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in ("finance", "profit_share"):
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} table. "
                f"Available: finance, profit_share."
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        built = (
            self._finance_products()
            if category == "finance"
            else self._profit_share_products()
        )
        self._store(category, built)
        logger.debug("Loaded %d %s row(s) from %s", len(built), category, self.name)
        return built

    def _finance_products(self) -> list[Product]:
        rows = self._service(
            "GetFinanceCalculatorCreditTypeItems",
            "GetFinanceCalculatorCreditTypeItemsResult",
        )
        built = []
        for row in rows:
            # The same Code appears under several CreditIDs with different fee
            # structures, so CreditID is the identity, not Code.
            bands = row.get("FinanceCalculatorCreditList") or []
            terms = [int(b.get("Max") or 0) for b in bands if b.get("Max")]
            starts = [int(b.get("Min") or 0) for b in bands if b.get("Min")]
            built.append(
                Product(
                    code=str(row.get("CreditID")),
                    name=(row.get("Title") or row.get("Code") or "").strip(),
                    category="finance",
                    min_term=min(starts) if starts else None,
                    max_term=max(terms) if terms else None,
                    # The monthly rate varies by term band, so no single figure
                    # belongs here; the bands are in raw and the refusal quotes
                    # the one that applies.
                    raw=row,
                )
            )
        return built

    def _profit_share_products(self) -> list[Product]:
        built = []
        for group, customer in RATE_TABLES:
            for row in self._service(
                f"GetKarPayiHesaplama/{group}/{customer}", "GetKarPayiHesaplamaResult"
            ):
                currency = (row.get("Currency") or "").strip()
                low = int(row.get("MinimumDueDay") or 0)
                high = int(row.get("MaximumDueDay") or 0)
                minimum = money(row.get("MinimumAmount"))
                built.append(
                    Product(
                        code=f"{group}-{customer}-{currency}-{low}-{high}",
                        # The bands overlap across account groups — TL 32–91 gün
                        # is 41,08% in group 4 and 28,80% in group 1 — and the
                        # bank publishes no name for either group. The minimum
                        # is what separates them, so it goes in the name: that
                        # is the basis a caller needs to choose between two
                        # rates for the same term.
                        name=(
                            f"{currency} {low}–{high} gün, {customer.lower()}, "
                            f"en az {minimum:,.0f} {currency}"
                        ),
                        category="profit_share",
                        min_amount=minimum or None,
                        max_amount=money(row.get("MaximumAmount")) or None,
                        min_term=low,
                        max_term=high,
                        currencies=(currency,),
                        # The published gross annual ratio for this band.
                        rate=rate(row.get("AnnuallyGrossRatio")) or None,
                        raw=row,
                    )
                )
        return built

    # ----- rates -----

    def rates(self) -> list[Rate]:
        """The bank's FX and metal board, from its own service.

        `GetExchangeRates` on the same SharePoint service the catalogues come
        from. Twenty instruments with a quote time, and it was reachable all
        along -- the bank was recorded as publishing no rates because nobody had
        asked the service what else it served.

        Gold and silver arrive twice: an ounce price in USD (XAU/XAG) and a gram
        price in lira (YAU/YAG). Only the gram rows are comparable with the
        other banks, so the ounce rows keep their own codes and a "1" unit
        rather than being mixed into the gram board.
        """
        payload = self._service("GetExchangeRates", "GetExchangeRatesResult")
        built: list[Rate] = []
        for row in payload:
            code = (row.get("CurrencyCode") or "").strip()
            buy, sell = money(row.get("BuyPrice")), money(row.get("SellPrice"))
            if not code or buy <= 0 or sell <= 0:
                continue
            built.append(Rate(
                code=_GRAM_CODES.get(code, code),
                name=(row.get("Title") or code).strip(),
                buy=buy,
                sell=sell,
                unit="gram" if code in _GRAM_CODES else "1",
                as_of=_ms_date(row.get("Date")),
            ))
        return built

    # ----- a rate, but never a payment -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        """The rate this bank publishes for the term, with no instalment.

        Every figure here is read straight off the bank's own table: the
        monthly profit rate for the band covering `term`, its annual cost rate,
        the allocation fee and BSMV. `installment` and `total` stay None
        because the bank never states them -- its calculator runs the annuity
        in the browser -- and working them out here is the one thing the
        project rules forbid.

        Returning the row rather than refusing is what puts the bank in the
        comparison at all. It ranks on rate beside everyone else and its
        payment column is visibly empty, which is the truth; refusing dropped
        an entire bank's published pricing off the page.
        """
        chosen = self.find_product("finance", product)
        bands = chosen.raw.get("FinanceCalculatorCreditList") or []
        band = _band_for(bands, term)
        if band is None:
            covered = sorted(
                (int(b.get("Min") or 0), int(b.get("Max") or 0)) for b in bands
            )
            spans = ", ".join(f"{lo}-{hi}" for lo, hi in covered) or "none"
            raise UnsupportedProduct(
                f"{self.display_name} publishes no rate for {chosen.name} over "
                f"{term} months. Its bands cover {spans} months."
            )

        # Both are shares in the payload (0.00575, 0.15), stated as percentages
        # everywhere else in this codebase.
        fees = {}
        allocation = rate(chosen.raw.get("AllocationFee"))
        if allocation:
            fees["allocation_rate"] = round(allocation * 100, 4)
        bsmv = rate(chosen.raw.get("Bitt"))
        if bsmv:
            fees["bsmv_rate"] = round(bsmv * 100, 4)

        return FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=amount,
            term=term,
            installment=None,
            total=None,
            profit_rate=rate(band.get("Value")),
            annual_cost_rate=rate(band.get("Cost")),
            fees=fees,
            schedule=[],
            raw={"band": band, "product": chosen.raw},
        )

    @refusal
    def profit_share_quote(
        self,
        product: str,
        amount: float,
        term: int,
        currency: str = "TRY",
        term_unit: str | None = None,
    ):
        """Refuse, naming the gross annual ratio the bank publishes."""
        chosen = self.find_product("profit_share", product)
        raise UnsupportedProduct(
            f"{self.display_name} publishes no calculated profit for "
            f"{chosen.name}, only the rate itself: a gross annual ratio of "
            f"{chosen.rate}% on a minimum of {chosen.min_amount:,.0f}. Working "
            f"the amount out from that is arithmetic the bank does not do and "
            f"neither do we."
        )


def _band_for(bands: list[dict], term: int) -> dict | None:
    """The rate band covering a term, from the bank's own table."""
    for band in bands:
        if int(band.get("Min") or 0) <= term <= int(band.get("Max") or 0):
            return band
    return None
