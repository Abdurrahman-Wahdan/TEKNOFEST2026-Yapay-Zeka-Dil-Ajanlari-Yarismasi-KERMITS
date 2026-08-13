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

from ..models import Product
from ..parse import money, rate
from .base import BaseBank, UnsupportedProduct, refusal

logger = logging.getLogger(__name__)

HOST = "https://www.turkiyefinans.com.tr"
SERVICE = f"{HOST}/_vti_bin/TurkiyeFinansServices/FrontEndService.svc"
PAGE = f"{HOST}/tr-tr/hesaplama-araclari/Sayfalar/hesaplama-araclari.aspx"

HEADERS = {"accept": "application/json", "referer": PAGE}

# Rate tables are published per account group and customer type. Group 4 is the
# short-term band and group 1 the standard one; group 2 publishes nothing on
# either side, and group 4 Ticari publishes nothing either.
RATE_TABLES = ((4, "Bireysel"), (1, "Bireysel"), (1, "Ticari"))


class TurkiyeFinans(BaseBank):
    name = "turkiyefinans"
    display_name = "Türkiye Finans Katılım Bankası"
    # Products and rates only. There is deliberately no "finance" or
    # "profit_share" here: this bank states rates but computes nothing, and
    # inventing the instalment ourselves is the one thing the rules forbid.
    capabilities = frozenset({"products"})
    notes = (
        "It publishes rate and fee tables but no calculated figures: its own "
        "calculator does the arithmetic in the browser, so there is no "
        "instalment or profit amount to read back. Use list_products for the "
        "rates, fees and term bands it does state."
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

    # ----- what it will not answer, and why -----

    @refusal
    def finance_quote(self, product: str, amount: float, term: int):
        """Refuse, naming the rate the bank publishes for this term.

        The rate is read straight off the bank's table; only the instalment,
        which the bank never states, is missing.
        """
        chosen = self.find_product("finance", product)
        band = _band_for(chosen.raw.get("FinanceCalculatorCreditList") or [], term)
        detail = ""
        if band:
            detail = (
                f" For {term} months it publishes a monthly profit rate of "
                f"{rate(band.get('Value'))}% and an annual cost rate of "
                f"{rate(band.get('Cost'))}%, plus an allocation fee of "
                f"{rate(chosen.raw.get('AllocationFee')) * 100:.3f}%."
            )
        raise UnsupportedProduct(
            f"{self.display_name} publishes no instalment figure for "
            f"{chosen.name}: its calculator works the payment out in the browser "
            f"from the rate table, so there is nothing to read back." + detail
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
