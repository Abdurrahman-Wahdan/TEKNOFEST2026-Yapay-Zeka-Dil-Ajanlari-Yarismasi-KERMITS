"""Türkiye Finans.

SharePoint services under `/_vti_bin/<Service>.svc/<Method>`, answering plain
httpx with no token, no session and no WAF.

**This bank publishes tables, not answers -- for the raw feed.** Its
calculators fetch a rate-and-fee table once on page load and then do the
instalment arithmetic in the browser; pressing Hesapla fires no further
request. For financing, that arithmetic is ported rather than refused:
`creditInstallmentResult` in `turkiyefinans.modules.min.js` (captured live
2026-08-16 from `/SiteAssets/js/turkiyefinans.modules.min.js`) is a plain,
deterministic annuity over the bank's own published rate, KKDF and BSMV
shares, and is reproduced exactly in `_installment_plan` below -- the second
agreed exception to "never compute a bank's figure ourselves", the same shape
as `BaseBank.convert_from_rates`. Card instalments still refuse: their
calculator (`installments.js`) schedules against a transaction date and a
statement cut-off day, neither of which this project asks for, so there is
nothing to port yet.

Everything else it states is available through `products`: the finance
products with their per-term profit rates, allocation fee and BSMV, and the
kâr payı rate table with its minimum amounts and day bands.

Contract in docs/discovery/captured/turkiyefinans.md, exercised by
verify_turkiyefinans.py.
"""

import logging
import re
from decimal import ROUND_HALF_UP, Decimal

from ..models import CardInstallmentQuote, Conversion, FinanceQuote, PaymentRow, Product, Rate
from ..parse import money, rate
from .base import RATE_ALIASES, BaseBank, UnsupportedProduct, refusal

logger = logging.getLogger(__name__)

HOST = "https://www.turkiyefinans.com.tr"
SERVICE = f"{HOST}/_vti_bin/TurkiyeFinansServices/FrontEndService.svc"
PAGE = f"{HOST}/tr-tr/hesaplama-araclari/Sayfalar/hesaplama-araclari.aspx"
CARD_PAGE = f"{HOST}/tr-tr/hesaplama-araclari/Sayfalar/taksitle-hesaplama-araci.aspx"

HEADERS = {"accept": "application/json", "referer": PAGE}

# The board quotes gold and silver twice: an ounce price in dollars and a gram
# price in lira. Only the gram rows line up with what the other banks publish.
_GRAM_CODES = {"YAU": "ALT (gr)", "YAG": "GMS (gr)"}

# One flat rate for every Türkiye Finans credit card (Paraf, Happy Kart alike) —
# the calculator page has no card-type selector, just one disabled rate field.
# `min_term`/`max_term` come from the page's own instalment slider (2-12).
CARD = Product(
    code="TAKSITLI", name="Kredi Kartı Taksitle", category="card",
    min_term=2, max_term=12, raw={},
)

# The rate sits in a disabled, server-rendered input — there is no service call
# behind it, the same way Hayat's account types are parsed off its homepage
# HTML rather than an endpoint. Matched attribute-order-agnostic because the
# page emits `value="..."` before `id="..."` on this particular control.
_CARD_RATE_INPUT = re.compile(r'<input[^>]*txtTaksitleKarPayi[^>]*>')
_VALUE_ATTR = re.compile(r'value="([\d.,]+)"')


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
    # everyone else. It never states the instalment itself, but its own
    # calculator computes one from exactly those published figures, so
    # `finance_quote` reproduces that computation (`derived=True`) rather than
    # leaving the payment blank. "profit_share" stays out: there the bank
    # states only a ratio, and a profit amount would be arithmetic on the
    # amount asked for, with no bank-side formula to reproduce.
    capabilities = frozenset({"products", "finance", "rates", "convert", "card"})
    # The feed already spells gold and silver "ALT (gr)" / "GMS (gr)" -- the
    # same labels Kuveyt Türk and Hayat use -- so the shared alias map applies
    # unchanged.
    rate_aliases = RATE_ALIASES
    notes = (
        "It publishes rate and fee tables and computes nothing itself: its "
        "own calculator does the arithmetic in the browser. Financing "
        "instalments are computed here from that published rate, the same "
        "arithmetic the bank's own calculator runs (flagged derived). Card "
        "instalments still come back with the bank's published rate and no "
        "instalment -- its calculator schedules against a transaction date "
        "this project does not have. Participation accounts come back as a "
        "ratio only, through list_products."
    )

    def _service(self, method: str, result_key: str) -> list[dict]:
        payload = self._json("GET", f"{SERVICE}/{method}", headers=HEADERS) or {}
        return ((payload.get(result_key) or {}).get("Data")) or []

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in ("finance", "profit_share", "card"):
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} table. "
                f"Available: finance, profit_share, card."
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        built = (
            self._finance_products() if category == "finance"
            else [CARD] if category == "card"
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

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert using the published rate.

        `GetExchangeRates` publishes a full board (above) but the bank's own
        calculator does everything else in the browser -- there is no
        converter endpoint behind it -- so the multiplication happens in
        `BaseBank.convert_from_rates`, flagged as derived. The same agreed
        exception as Kuveyt Türk and Hayat.
        """
        return self.convert_from_rates(source, target, amount)

    # ----- a rate, and now a payment computed the bank's own way -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        """The rate this bank publishes for the term, plus a computed payment.

        The rate, annual cost rate, allocation fee and BSMV are read straight
        off the bank's own table. `installment`, `total` and `schedule` are
        not read off anything -- the bank's calculator computes them in the
        browser and never sends them back -- so they are worked out here by
        `_installment_plan`, a direct port of that calculator's own
        `creditInstallmentResult` function. `derived=True` marks the result:
        every input is the bank's own published figure, only the arithmetic
        is ours.
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
        # KKDF ("Rusf" in the payload -- Kaynak Kullanımını Destekleme Fonu)
        # never had a column of its own before now because nothing used it;
        # `_installment_plan` needs it the same way the bank's own calculator
        # does, folded into the effective rate alongside BSMV.
        kkdf = rate(chosen.raw.get("Rusf"))

        monthly_rate = rate(band.get("Value")) / 100
        installment, total, schedule = _installment_plan(
            amount=amount, term=term, monthly_rate=monthly_rate, kkdf=kkdf, bsmv=bsmv,
        )

        return self._check_quote(FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=amount,
            term=term,
            installment=installment,
            total=total,
            profit_rate=rate(band.get("Value")),
            annual_cost_rate=rate(band.get("Cost")),
            fees=fees,
            schedule=schedule,
            raw={"band": band, "product": chosen.raw},
            derived=True,
        ))

    # ----- a rate, but not yet a payment -----

    def card_installment_quote(
        self, card: str, amount: float, installments: int
    ) -> CardInstallmentQuote:
        """The published card instalment rate, with no instalment amount.

        `taksitle-hesaplama-araci.aspx` runs a client-side annuity too, but a
        date-dependent one: `installments.js` schedules against a transaction
        date and a statement cut-off day (`moment(...).diff(..., 'days')`
        drives every period's discount factor), neither of which this project
        asks for. `finance_quote`'s calculator has no such dependency and is
        ported (see `_installment_plan`); this one is not, so the rate -- the
        one figure the bank actually states here -- comes back alone.
        """
        chosen = self.find_product("card", card)
        self._check_limits(chosen, amount=amount, term=installments,
                           term_label="instalments")

        page = self._text(CARD_PAGE)
        tag = _CARD_RATE_INPUT.search(page)
        value = _VALUE_ATTR.search(tag.group(0)) if tag else None
        if not value:
            raise UnsupportedProduct(
                f"{self.display_name} published no card instalment rate. Its "
                f"calculator page may have changed shape."
            )
        profit_rate = float(value.group(1).replace(",", "."))

        return CardInstallmentQuote(
            bank=self.name,
            card=chosen,
            amount=float(amount),
            installments=int(installments),
            installment=None,
            total=None,
            profit_rate=profit_rate,
            raw={"txtTaksitleKarPayi": value.group(1)},
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


def _round2(value: float) -> float:
    """Round the way the bank's own JS does: `parseFloat(n.toFixed(2))`.

    `toFixed` rounds half away from zero; Python's `round` rounds half to
    even. The two disagree exactly on the boundary this arithmetic hits
    constantly (a profit share ending in exactly .xx5), so matching the
    bank's own rounding needs `Decimal` with `ROUND_HALF_UP`, not `round()`.
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _installment_plan(
    *, amount: float, term: int, monthly_rate: float, kkdf: float, bsmv: float,
) -> tuple[float, float, list[PaymentRow]]:
    """Türkiye Finans's own instalment arithmetic, reproduced exactly.

    A line-by-line port of `creditInstallmentResult` in
    `turkiyefinans.modules.min.js` (captured live 2026-08-16 from
    `https://www.turkiyefinans.com.tr/SiteAssets/js/turkiyefinans.modules.min.js`),
    the function the bank's own finance calculator runs in the browser after
    Hesapla is pressed. `monthly_rate`, `kkdf` and `bsmv` are fractions
    (0.0405, 0.15, 0.15), exactly as the bank's own table states them.

    The scheme is the bank's, not an invention: the level payment is priced
    off an *inflated* discount rate -- `monthly_rate * (1 + kkdf + bsmv)` --
    so KKDF and BSMV ride inside one flat instalment instead of being billed
    on top of it, while each period's profit is still `monthly_rate` applied
    to the balance actually owed. Reproduced as written, including rounding
    every intermediate amount to 2 decimals at the point the JS does, so the
    schedule closes to exactly zero the same way theirs does.
    """
    effective_rate = monthly_rate * (1 + kkdf + bsmv)
    discount_factor = sum(1 / (1 + effective_rate) ** period for period in range(1, term + 1))
    installment = _round2(amount / discount_factor)

    schedule: list[PaymentRow] = []
    balance = 0.0
    for period in range(1, term + 1):
        profit = _round2(amount * monthly_rate) if period == 1 else _round2(balance * monthly_rate)
        kkdf_amount = _round2(profit * kkdf)
        bsmv_amount = _round2(profit * bsmv)
        principal = _round2(installment - profit - kkdf_amount - bsmv_amount)
        if period == term:
            # The last period closes the loan to exactly zero rather than
            # trusting the rounded pieces above to land there themselves --
            # the bank's own code does the same override.
            principal = balance
        balance = _round2(amount - principal) if period == 1 else _round2(balance - principal)
        schedule.append(PaymentRow(
            order=period,
            amount=installment,
            principal=principal,
            profit=profit,
            taxes=_round2(kkdf_amount + bsmv_amount),
            remaining=balance,
        ))

    return installment, _round2(installment * term), schedule
