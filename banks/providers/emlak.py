"""Türkiye Emlak Katılım.

The same F5 WAF as Albaraka, so the transport is curl_cffi: plain httpx gets a
200 carrying an HTML "Request Rejected" body, which surfaces as a JSON decode
error rather than as an obvious block. Note that http.py deliberately does not
set a user-agent when impersonating — curl_cffi sends one matching the TLS
fingerprint, and a mismatched pair is rejected again.

Everything else is easy: plain query-string GETs, no token, no session, and
JSON numbers rather than the formatted strings its Unigate siblings return.
Contract in docs/discovery/captured/emlak.md, exercised by verify_emlak.py.
"""

import html as htmlmod
import logging
import re

from ..models import Conversion, FinanceQuote, PaymentRow, ProfitShareQuote, Product, Rate
from ..parse import canonical_currency, money, rate
from ..parse import term_unit as unit
from .base import RATE_ALIASES, BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://www.emlakkatilim.com.tr"
PAGE = f"{HOST}/tr/hesaplama-araclari"
RATES_PAGE = f"{HOST}/tr/tum-kurlarimiz"

HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "accept": "application/json, text/javascript, */*; q=0.01",
}

# The rates page's own labels for the three metals, mapped onto the spellings
# Kuveyt Türk and Hayat already use so `canonical_currency` groups them with
# everyone else's XAU/XAG/XPT rows. "CAG (gr)" (a 22-carat coin, not bullion)
# and "Çeyrek Altın" (a quarter coin) are already the bank's own codes and
# stay unmapped, same as every other bank's coin rows.
_RATE_LABELS = {
    "Altın (gr)": "ALT (gr)",
    "Gümüş (gr)": "GMS (gr)",
    "Platin (gr)": "PLT (gr)",
}

# Emlak's own currency codes, as its Fec select labels them. ALT and GMS are
# quoted per gram. The live select is read at run time; this maps the standard
# codes a caller would use onto the labels the page shows.
CURRENCY_LABELS = {
    "TRY": "TL", "USD": "USD", "EUR": "EUR",
    "XAU": "ALT (gr)", "GOLD": "ALT (gr)", "ALTIN": "ALT (gr)",
    "XAG": "GMS (gr)", "GUMUS": "GMS (gr)",
}

# ProductTypeId codes ARACBINEK2EL and GMENKULKONUTYENI also appear at Kuveyt
# Türk — both run Unigate-derived stacks — but they are not interchangeable
# across banks. Every code here is bank-scoped.
_OPTION = re.compile(r'<option[^>]*value="([^"]+)"[^>]*>\s*([^<]*?)\s*</option>', re.S)


def _select(page: str, name: str) -> list[tuple[str, str]]:
    """The value/label pairs of one named select, labels un-escaped."""
    block = re.search(rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', page, re.S)
    if not block:
        return []
    return [
        (value, htmlmod.unescape(re.sub(r"\s+", " ", label)).strip())
        for value, label in _OPTION.findall(block.group(1))
        if value
    ]


class Emlak(BaseBank):
    name = "emlak"
    display_name = "Türkiye Emlak Katılım Bankası"
    capabilities = frozenset({"products", "finance", "profit_share", "rates", "convert"})
    rate_aliases = RATE_ALIASES
    notes = (
        "It publishes no card calculator. It also states no maximum finance "
        "amount anywhere and its calculator never refuses one, so a very "
        "large figure is arithmetic rather than an offer — check the amount "
        "against the product before relying on it."
    )
    # The same F5 WAF as Albaraka: it fingerprints the TLS handshake.
    transport = "impersonate"
    # `#js-financial-rate` is named `CustomRate` on the public calculator.
    # It is enabled by the “Kâr Oranını Kendim Belirleyeceğim” control.
    finance_input_capabilities = frozenset({"monthly_profit_rate"})

    def _plugin(self, plugin: str, **params):
        return self._json(
            "GET",
            f"{HOST}/Plugins/{plugin}",
            headers={**HEADERS, "referer": PAGE},
            params=params,
        )

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in ("finance", "profit_share"):
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} catalogue. "
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
        logger.debug("Loaded %d %s product(s) from %s", len(built), category, self.name)
        return built

    def _page(self) -> str:
        """The one page every calculator lives on, fetched once."""
        cached = self._scratch.get("page")
        if cached is None:
            cached = self._scratch["page"] = self._text(PAGE)
        return cached

    def _finance_products(self) -> list[Product]:
        options = _select(self._page(), "ProductTypeId")
        if not options:
            raise UnsupportedProduct(
                f"{self.display_name} returned no finance products. The catalogue "
                f"is parsed out of the page, so this usually means the WAF served "
                f"a rejection page instead of the calculator."
            )
        built = []
        for code, label in options:
            limits = (self._plugin("SelectLoansProperty", ProductTypeId=code) or {}).get(
                "Data"
            ) or {}
            built.append(
                Product(
                    code=code,
                    name=label,
                    category="finance",
                    min_amount=money(limits.get("AmountMin")) or None,
                    max_amount=money(limits.get("AmountMax")) or None,
                    min_term=int(limits.get("MaturityMin") or 1) or 1,
                    max_term=int(limits.get("MaturityMax") or 0) or None,
                    raw=limits or {"ProductTypeId": code},
                )
            )
        return built

    def _profit_share_products(self) -> list[Product]:
        """One participation account, priced per currency and term band.

        The page offers currency and term selects rather than named accounts,
        so those become the product's currencies and term range — read from the
        selects rather than assumed.
        """
        page = self._page()
        # Canonicalised on the way out: the page labels these "TL" and
        # "ALT (gr)", and a catalogue that says TL cannot be intersected with
        # one that says TRY. `_fec` maps the symbol back to the label when the
        # request is actually built, so nothing downstream needs the raw text.
        currencies = tuple(
            canonical_currency(label) for _, label in _select(page, "Fec")
        )
        terms = self._terms()
        return [
            Product(
                code="KATILMA",
                name="Katılma Hesabı",
                category="profit_share",
                min_term=terms[0] if terms else None,
                max_term=terms[-1] if terms else None,
                currencies=currencies or ("TRY",),
                raw={"fec": dict(_select(page, "Fec")), "terms": terms},
            )
        ]

    def _terms(self) -> list[int]:
        """The day counts the term select offers; nothing else is priced."""
        return sorted(
            int(value) for value, _ in _select(self._page(), "MaturityTerm")
            if value.isdigit()
        )

    def _fec(self, currency: str) -> str:
        """The bank's Fec code for a currency, from its own select."""
        label = CURRENCY_LABELS.get(currency, currency)
        for value, option in _select(self._page(), "Fec"):
            if option == label:
                return value
        raise UnsupportedProduct(
            f"{self.display_name} does not offer {currency} participation "
            f"accounts. Available: "
            f"{', '.join(label for _, label in _select(self._page(), 'Fec'))}."
        )

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        """The bank's own board, scraped off its rates page.

        `/tr/tum-kurlarimiz` renders the full 23-instrument board server-side
        with no endpoint behind it -- the same shape as Vakıf's converter
        page. Its own JSON API, `services/api/CurrencyTypes/GetFxRatesAll`
        -- found live inside the site's own bundle, `SERVICE_URL +
        "CurrencyTypes/GetFxRatesAll"`, sitting in a commented-out block --
        answers 404 when called directly: confirmed decommissioned, not
        merely unlinked. Scraping this table is the only way to reach these
        rates at all, which is also why there is no live converter and
        `convert` derives instead.
        """
        page = self._text(RATES_PAGE)
        table = re.search(r"<table[^>]*>.*?</table>", page, re.S)
        if not table:
            return []
        built: list[Rate] = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S):
            cells = [
                htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            ]
            cells = [c for c in cells if c]
            if len(cells) != 3:
                continue
            label, buy_text, sell_text = cells
            buy, sell = money(buy_text), money(sell_text)
            # The header row's own cells ("Banka Alış", "Banka Satış") parse
            # to 0 here and fall out along with any other row the bank has
            # not priced -- no separate header check needed.
            if buy <= 0 or sell <= 0:
                continue
            code = _RATE_LABELS.get(label, label)
            built.append(Rate(
                code=code, name=label, buy=buy, sell=sell,
                unit="gram" if "(gr)" in code else "1",
            ))
        return built

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert using the published rate.

        No live converter exists (see `rates`), so the multiplication happens
        in `BaseBank.convert_from_rates`, flagged as derived -- the same
        agreed exception as Kuveyt Türk, Hayat and Türkiye Finans.
        """
        return self.convert_from_rates(source, target, amount)

    # ----- finance -----

    def finance_quote(
        self, product: str, amount: float, term: int,
        monthly_profit_rate: float | None = None,
    ) -> FinanceQuote:
        chosen = self.find_product("finance", product)
        self._check_limits(chosen, amount=amount, term=term)
        payload = self._plugin(
            "CalculateLoansProduct",
            CalculationTypeId=1,
            ProductTypeId=chosen.code,
            LoanAmount=int(amount),
            LoanMaturity=int(term),
            LoanSegmentId=1,
            CustomRate=monthly_profit_rate if monthly_profit_rate is not None else 0,
        )
        data = (payload or {}).get("Data") or {}
        total = money(data.get("TotalInstallmentAmount"))
        if (payload or {}).get("Success") is not True or total <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment plan for "
                f"{chosen.name} at {amount:,.0f} TL over {term} months."
            )
        count = int(data.get("InstallmentCount") or term) or term
        rows = data.get("InstallmentContractList") or []
        return self._check_quote(FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=count,
            # The instalment is read off the bank's own payment plan; there is
            # no top-level instalment field and it is never divided out here.
            installment=money(rows[0].get("Amount")) if rows else 0.0,
            total=total,
            profit_rate=rate(data.get("ProfitRate")),
            # TotalCost is the annual cost rate; MonthlyConstRate is its
            # monthly counterpart and is left in raw.
            annual_cost_rate=rate(data.get("TotalCost")) or None,
            fees={
                "commission": money(data.get("CommissionAmount")),
                "expertise": money(data.get("ExpertiseAmount")),
                "hypothec": money(data.get("HypothecAmount")),
            },
            schedule=[
                PaymentRow(
                    order=int(row.get("OrderNumber") or 0),
                    amount=money(row.get("Amount")),
                    principal=money(row.get("PrincipalAmount")),
                    profit=money(row.get("ProfitAmount")),
                    taxes=money(row.get("RUSFAmount")) + money(row.get("BITTAmount")),
                    remaining=money(row.get("RemainingPrincipalAmount")),
                )
                for row in rows
            ],
            raw=payload,
        ))

    # ----- profit share -----

    def profit_share_quote(
        self,
        product: str,
        amount: float,
        term: int,
        currency: str = "TRY",
        term_unit: str | None = None,
    ) -> ProfitShareQuote:
        chosen = self.find_product("profit_share", product)
        currency = currency.upper()
        fec = self._fec(currency)
        self._check_limits(chosen, amount=amount)
        days = term * 30 if unit(term_unit) == "month" else term
        band = self._band(days, self._terms())

        payload = self._plugin(
            "CalculateProfitShareRate",
            LanguageId=1,
            Money=int(amount),
            Fec=fec,
            profitShareInstallment=0,
            MaturityTerm=band,
            profitShareInstallmentValueDay=band,
        )
        data = (payload or {}).get("Data") or {}
        net = money(data.get("NetProfitShare"))
        # Gold prices at 1, 3 and 6 months but answers 12 ay and 12+ ay with
        # zeros — a gap on the bank's side, confirmed against their own page.
        if (payload or {}).get("Success") is not True or net <= 0:
            raise self._no_rate(chosen, amount, currency, f"{band} days")
        return self._check_profit_share(ProfitShareQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=band,
            currency=currency,
            term_unit="day",
            # Checked 2026-08-16 against a live raw response and the
            # calculator page's own HTML: "kâr paylaşım oranı" appears only in
            # a disclaimer sentence ("rates vary by ratio, segment and term"),
            # never as a queryable figure the endpoint returns per quote.
            ratio=None,
            gross_profit=money(data.get("GrossProfitShare")),
            net_profit=net,
            gross_annual_rate=rate(data.get("GrossProfitShareYearly")),
            net_annual_rate=rate(data.get("NetProfitShareYearly")),
            raw=payload,
        ))
