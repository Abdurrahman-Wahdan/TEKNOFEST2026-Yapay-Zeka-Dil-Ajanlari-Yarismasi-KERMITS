"""Dünya Katılım.

ASP.NET Core. Every calculator is on the homepage — there is no calculator URL
to discover — and every call is a POST to the site root carrying the homepage's
anti-forgery token in its form body.

The bank list's `dunyakatilim.com` does not resolve; the live host is
`dunyakatilim.com.tr`. The handoff's note that "Dünya times out under httpx" was
the dead domain, not the transport: plain httpx works.

Contract in docs/discovery/captured/dunya.md, exercised by verify_dunya.py.
"""

import html as htmlmod
import json
import html
import logging
import re
from decimal import Decimal

from ..models import Conversion, FinanceQuote, ProfitShareQuote, Product, Rate
from ..parse import money, money_en, rate
from ..parse import term_unit as unit
from .base import BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://dunyakatilim.com.tr"

# Quoted per gram, all four. Listing only gold and silver left platinum and
# palladium as unit "1", so they never grouped with the same metal at another
# bank -- the board showed two XPT rows, each with one bank in it.
_METALS = {"XAU", "XAG", "XPT", "XPD"}
HOME = f"{HOST}/"

HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "accept": "application/json, text/javascript, */*; q=0.01",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "referer": HOME,
}

_OPTION = re.compile(r'<option[^>]*value="([^"]+)"[^>]*>\s*([^<]*?)\s*</option>', re.S)
_LOAN_SELECT = re.compile(r'<select[^>]*name="state"[^>]*>(.*?)</select>', re.S)
# The kâr payı products are JSON inside single-quoted, HTML-escaped option
# attributes — the same shape as Albaraka's finance catalogue.
_DIVIDEND = re.compile(r"""<option[^>]*value=["'](\{&quot;id&quot;.*?\})["']""", re.S)


class Dunya(BaseBank):
    name = "dunya"
    display_name = "Dünya Katılım Bankası"
    capabilities = frozenset(
        {"products", "finance", "profit_share", "convert", "rates"}
    )
    notes = (
        "It converts currency and precious metals server-side but publishes no "
        "buy/sell rate feed, so its board is derived from its own converter. "
        "It has no card calculator."
    )
    # Plain httpx plus the homepage's anti-forgery token on every call.
    transport = "csrf"

    def _post(self, action: str, **fields):
        """POST a calculator action with the homepage token in the body.

        A token the server has stopped accepting looks like a failed call, so
        one retry replaces it before giving up.
        """
        for refresh in (False, True):
            try:
                return self._json(
                    "POST",
                    f"{HOST}/{action}",
                    headers=HEADERS,
                    data={
                        **fields,
                        "__RequestVerificationToken": self._token(HOME, refresh=refresh),
                    },
                )
            except ValueError:
                if refresh:
                    raise
        return None

    def _home(self) -> str:
        cached = self._scratch.get("home")
        if cached is None:
            cached = self._scratch["home"] = self._text(HOME)
        return cached

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

    def _finance_products(self) -> list[Product]:
        block = _LOAN_SELECT.search(self._home())
        if not block:
            raise UnsupportedProduct(
                f"{self.display_name} returned no finance products. The catalogue "
                f"is parsed out of the homepage, so its layout may have changed."
            )
        built = []
        for code, label in _OPTION.findall(block.group(1)):
            limits = self._post("LoanInstallmentValues?lang=tr", productCode=code) or {}
            built.append(
                Product(
                    code=code,
                    name=htmlmod.unescape(label),
                    category="finance",
                    min_amount=money(limits.get("minAmount")) or None,
                    max_amount=money(limits.get("maxAmount")) or None,
                    min_term=int(limits.get("minInstallment") or 1),
                    max_term=int(limits.get("maxInstallment") or 0) or None,
                    # productCategory is required by the quote call and is the
                    # bank's own, taken from here rather than mapped by hand.
                    raw=limits,
                )
            )
        return built

    def _profit_share_products(self) -> list[Product]:
        built = []
        for match in _DIVIDEND.finditer(self._home()):
            try:
                entry = json.loads(htmlmod.unescape(match.group(1)))
            except json.JSONDecodeError:
                continue
            terms = [
                int(m.get("maturityPeriodBeginValue") or 0)
                for m in entry.get("productMaturitySettings") or []
            ]
            built.append(
                Product(
                    code=entry.get("productCode") or "",
                    name=entry.get("name") or entry.get("productCode") or "",
                    category="profit_share",
                    min_term=min(terms) if terms else None,
                    max_term=max(terms) if terms else None,
                    currencies=tuple(entry.get("availableCurrencies") or ("TRY",)),
                    # Each maturity carries its own code and exact day count;
                    # sending a code that does not belong to the product is the
                    # one error message any of these ten banks writes in full.
                    raw=entry,
                )
            )
        if not built:
            raise UnsupportedProduct(
                f"{self.display_name} returned no participation accounts. They are "
                f"parsed out of the homepage, so its layout may have changed."
            )
        return built

    # ----- finance -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        chosen = self.find_product("finance", product)
        # It declares a ceiling per product and will still quote past it.
        self._check_limits(chosen, amount=amount, term=term)
        payload = self._post(
            "LoanCheckRate?lang=tr",
            productName=chosen.name,
            productCode=chosen.code,
            productCategory=chosen.raw.get("category") or "",
            # A plain integer, never "100.000,00": this endpoint strips dots as
            # thousands separators, so "100000.00" is read as ten million and
            # answers with a plausible instalment a hundred times too large.
            amount=str(int(amount)),
            installmentCount=str(int(term)),
            userRate="0",
            userSelected="false",
        )
        payload = payload or {}
        # "monthlyInterest" is the instalment, despite the name.
        installment = money(payload.get("monthlyInterest"))
        if payload.get("result") != "SUCCESS" or installment <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment plan for "
                f"{chosen.name} at {amount:,.0f} TL over {term} months"
                + (f": {payload['message']}" if payload.get("message") else ".")
            )
        return self._check_quote(FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(term),
            installment=installment,
            total=money(payload.get("totalPayment")),
            profit_rate=rate(payload.get("rate")),
            annual_cost_rate=None,
            fees={},
            # The plan comes back as a whole HTML document; it stays in raw
            # rather than being scraped for a question nobody has asked.
            schedule=[],
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
        if chosen.currencies and currency not in chosen.currencies:
            raise UnsupportedProduct(
                f"{chosen.name} is not offered in {currency}. "
                f"Available: {', '.join(chosen.currencies)}."
            )
        self._check_limits(chosen, amount=amount)
        days = term * 30 if unit(term_unit) == "month" else term
        maturity = _maturity_at(chosen, self._band(days, _band_days(chosen)))
        if maturity is None:
            raise UnsupportedProduct(
                f"{chosen.name} publishes no term matching {days} days."
            )

        payload = self._post(
            "DividendEstimatedProfit?lang=tr",
            balance=str(int(amount)),
            currencyCode=currency,
            maturityCode=maturity["maturityCode"],
            maturityPeriodValue=str(int(maturity["maturityPeriodBeginValue"])),
            productCode=chosen.code,
        ) or {}
        # Alone among the ten, this bank explains itself when it refuses.
        if payload.get("result") != "SUCCESS":
            raise UnsupportedProduct(
                f"{self.display_name} published no profit-share rate for "
                f"{chosen.name} in {currency} over {maturity['maturityName']}"
                + (f": {payload['message']}" if payload.get("message") else ".")
            )
        net = money(payload.get("netProfitAmount"))
        if net <= 0:
            raise self._no_rate(chosen, amount, currency, maturity["maturityName"])
        return self._check_profit_share(ProfitShareQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(maturity["maturityPeriodBeginValue"]),
            currency=currency,
            term_unit="day",
            ratio=None,
            gross_profit=money(payload.get("grossProfitAmount")),
            net_profit=net,
            gross_annual_rate=rate(payload.get("grossProfitRate")),
            net_annual_rate=rate(payload.get("netProfitRate")),
            raw=payload,
        ))

    # ----- conversion -----

    def rates(self) -> list[Rate]:
        """The bank's own published board, off its daily-rates page.

        `/gunluk-kurlar` renders `Döviz Cinsi | Banka Alış | Banka Satış |
        Değişim` server-side; there is no JSON route behind it (`/CurrencyList`,
        `/CurrencyRates`, `/GetCurrency` all answer HTML, and `/CurrencyHistory`
        answers `{"data": []}`). So the page is the endpoint.

        Read, never computed. An earlier version inverted the converter to get a
        sell price; it agreed with the published figure, but the bank states
        these itself and a figure we worked out is not the same claim.

        Numbers here are en-US formatted on a Turkish page -- `6,662.6542` is
        six thousand -- hence `money_en`. The Turkish reader turns that into
        6,66, which looks like a plausible gold price and is not one.
        """
        page = self._text(f"{HOST}/gunluk-kurlar")
        built: list[Rate] = []
        for table in re.findall(r"<table[^>]*>.*?</table>", page, re.S):
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
                cells = [
                    html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
                    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
                ]
                cells = [c for c in cells if c]
                if len(cells) < 3:
                    continue
                # "Amerikan doları (USD)" -- the code is what everything else keys on.
                match = re.search(r"\(([A-Z]{3})\)", cells[0])
                if not match:
                    continue
                code = match.group(1)
                buy, sell = money_en(cells[1]), money_en(cells[2])
                if buy <= 0 or sell <= 0:
                    continue
                built.append(Rate(
                    code=code,
                    name=re.sub(r"\s*\([A-Z]{3}\)", "", cells[0]).strip(),
                    buy=buy,
                    sell=sell,
                    unit="gram" if code in _METALS else "1",
                ))
        if not built:
            raise UnsupportedProduct(
                f"{self.display_name} published no rate rows. The board is parsed "
                f"out of /gunluk-kurlar, so this usually means the page changed."
            )
        return built

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Dünya converts server-side, precious metals included."""
        source, target = source.upper(), target.upper()
        if source == target:
            value = Decimal(str(amount))
            return Conversion(
                bank=self.name, source=source, target=target,
                amount=value, result=value, rate=Decimal(1), derived=False,
            )
        # This endpoint strips every separator rather than parsing it: "1000.0"
        # is read as 10 000 and "10,5" as 105, both with a plausible answer and
        # no error. So only whole numbers can be stated to it truthfully.
        if float(amount) != int(amount):
            raise UnsupportedProduct(
                f"{self.display_name}'s converter takes whole amounts only — it "
                f"drops the separator instead of reading it, so {amount} would be "
                f"converted as {str(amount).replace('.', '').replace(',', '')}. "
                f"Ask for a whole number."
            )
        payload = self._post(
            "CurrencyBuyCalculate?lang=tr",
            buyFromAmount=str(int(amount)),
            buyFromCurrency=source,
            buyToCurrency=target,
            transactionType="1",
        ) or {}
        result = money(payload.get("destinationAmount"))
        if payload.get("result") != "SUCCESS" or result <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} did not convert {source} to {target}"
                + (f": {payload['message']}" if payload.get("message") else ".")
            )
        # The bank echoes back what it understood. Checking it is what turns a
        # silently misread amount into an error instead of a wrong answer.
        echoed = money(payload.get("sourceAmount"))
        if echoed and abs(echoed - float(amount)) > 0.01:
            raise UnsupportedProduct(
                f"{self.display_name} read {amount:,.0f} {source} as "
                f"{echoed:,.0f}, so its answer would not be to the question "
                f"asked."
            )
        given = Decimal(str(echoed or amount))
        got = Decimal(str(result))
        return Conversion(
            bank=self.name,
            source=source,
            target=target,
            amount=given,
            result=got,
            rate=got / given if given else Decimal(0),
            derived=False,
        )


def _band_days(product: Product) -> list[int]:
    """The day counts this product publishes."""
    return [
        int(m.get("maturityPeriodBeginValue") or 0)
        for m in product.raw.get("productMaturitySettings") or []
    ]


def _maturity_at(product: Product, days: int) -> dict | None:
    """The maturity setting for an exact day count.

    Each band carries its own code and day count, and the two have to travel
    together — a code that does not belong to the product is refused by name.
    """
    for setting in product.raw.get("productMaturitySettings") or []:
        if int(setting.get("maturityPeriodBeginValue") or 0) == days:
            return setting
    return None
