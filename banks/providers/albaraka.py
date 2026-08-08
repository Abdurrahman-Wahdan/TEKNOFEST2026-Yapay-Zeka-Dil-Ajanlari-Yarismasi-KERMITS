"""Albaraka Türk Katılım Bankası.

Two things make this bank unlike Kuveyt Türk, and both are load-bearing:

Albaraka runs an F5 WAF that answers 200 with a "Request Rejected" HTML page for
any /plugins/ request that does not come from a real browser. It is not
header-based — full Chrome headers, a warmed cookie jar and HTTP/2 all still get
rejected, because it fingerprints the TLS handshake. curl_cffi impersonating
Chrome passes unchanged, which is why every call here sets `impersonate`. The
page HTML itself is served to plain httpx; only /plugins/ is guarded, and that
asymmetry is what makes it easy to misdiagnose.

And the product catalogue is not an endpoint: it is JSON embedded in the finance
page's <option> attributes. The contract is in
docs/discovery/captured/albaraka.md, exercised by verify_albaraka.py.
"""

import html
import json
import logging
import re
from decimal import Decimal

from ..http import request_json, request_text
from ..models import (
    Conversion,
    FinanceQuote,
    PaymentRow,
    ProfitShareQuote,
    Product,
    Rate,
)
from .base import BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://www.albaraka.com.tr"
PLUGINS = f"{HOST}/plugins/"
LANG_ID = "bf2689d9-071e-4a20-9450-b1dbdd39778f"

FINANCE_PAGE = f"{HOST}/tr/hesaplama-araclari/finansman-hesaplama/ihtiyac-finansmani-hesaplama"
PROFIT_PAGE = f"{HOST}/tr/hesaplama-araclari/kar-payi-hesaplama"
FX_PAGE = f"{HOST}/tr/hesaplama-araclari/doviz-cevirici"

# The WAF fingerprints the TLS handshake, so every call impersonates Chrome.
IMPERSONATE = "chrome124"

HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "x-requested-with": "XMLHttpRequest",
    "adrum": "isAjax:true",
}

# The account-type select gives codes and Turkish names but pairs its currency
# lists positionally, which is too fragile to read. These are from the verified
# contract doc and match the page's own selects.
ACCOUNT_CURRENCIES = {
    "KTLMHSP": ("TRY", "USD", "EUR", "XAU"),
    "KTLARDM": ("TRY", "USD", "EUR"),
    "KURKTLMHSP": ("TRY", "USD", "EUR", "GBP", "XAU"),
}

# The attribute is single-quoted and the JSON inside is HTML-escaped, so the
# obvious double-quote pattern matches nothing and reads as "no products here".
_OPTION = re.compile(r"<option[^>]*value='(\{.*?\})'", re.S)
_SELECT_OPTION = re.compile(r"<option[^>]*value=\"([^\"]+)\"[^>]*>([^<]*)<", re.S)
_ACCOUNT_SELECT = re.compile(r"<select[^>]*selectTypeKarpayi.*?</select>", re.S)


def money(text) -> float:
    """'18.114,26 TRY' -> 18114.26.

    Turkish formatting: dot groups thousands, comma is the decimal point. The
    currency suffix varies, so a string test against '0,00 TRY' silently passes
    '0,00 USD' — a zero check has to parse the number.
    """
    if text is None:
        return 0.0
    cleaned = re.sub(r"[^\d,.-]", "", str(text)).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def rate(text) -> float:
    """'% 36.731684' -> 36.731684, '% 0,175' -> 0.175.

    Rates are not formatted like amounts and the two appear in the same
    response: GrossRate uses a dot for its decimals while IncomeTax uses a
    comma. Parsing a rate with money() gives 36731684.
    """
    if text is None:
        return 0.0
    cleaned = re.sub(r"[^\d,.-]", "", str(text))
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class Albaraka(BaseBank):
    name = "albaraka"
    display_name = "Albaraka Türk Katılım Bankası"
    # No card instalment calculator and no leasing calculator: those raise
    # through the base class rather than answering with nothing.
    capabilities = frozenset({"finance", "profit_share", "rates", "convert"})

    def _plugin(self, plugin: str, page: str, **params):
        body = {
            "langId": LANG_ID,
            "language": "tr",
            "Slug": page.rsplit("/", 1)[-1],
            "searchUrl": "/tr/arama",
            "customFinancingName": "",
            **params,
        }
        return request_json(
            "GET",
            PLUGINS + plugin,
            headers={**HEADERS, "referer": page},
            params=body,
            impersonate=IMPERSONATE,
        )

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in ("finance", "profit_share"):
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} catalogue. "
                f"Available: finance, profit_share."
            )
        cached = self._catalogue.get(category)
        if cached is not None:
            return cached

        built = (
            self._finance_products()
            if category == "finance"
            else self._profit_share_products()
        )
        self._catalogue[category] = built
        logger.debug("Loaded %d %s product(s) from %s", len(built), category, self.name)
        return built

    def _finance_products(self) -> list[Product]:
        page = request_text(FINANCE_PAGE, impersonate=IMPERSONATE)
        built, seen = [], set()
        for match in _OPTION.finditer(page):
            try:
                entry = json.loads(html.unescape(match.group(1)))
            except json.JSONDecodeError:
                continue
            # ProductCode alone does not identify a product — nine share
            # IHTKRED. The campaign code is unique and is what a user names.
            key = (entry.get("ProductCode"), entry.get("ProjectCode"),
                   entry.get("CampaingCode"))
            if key in seen or not entry.get("CampaignName"):
                continue
            seen.add(key)
            built.append(
                Product(
                    code=entry.get("CampaingCode") or entry.get("ProductCode") or "",
                    name=entry["CampaignName"],
                    category="finance",
                    min_amount=float(entry.get("AmountMinValue") or 0),
                    max_amount=float(entry.get("AmountMaxValue") or 0),
                    min_term=max(int(entry.get("MaturityMinValue") or 1), 1),
                    max_term=int(entry.get("MaturityMaxValue") or 0),
                    # Echoed back verbatim as FinanceType; do not rebuild it
                    # field by field.
                    raw=entry,
                )
            )
        if not built:
            raise UnsupportedProduct(
                f"{self.display_name} returned no finance products. The catalogue "
                f"is parsed out of the page, so this usually means the WAF served "
                f"a rejection page instead of the calculator."
            )
        return built

    def _profit_share_products(self) -> list[Product]:
        page = request_text(PROFIT_PAGE, impersonate=IMPERSONATE)
        select = _ACCOUNT_SELECT.search(page)
        if not select:
            raise UnsupportedProduct(
                f"{self.display_name} returned no participation accounts. The "
                f"account list is parsed out of the page."
            )
        built, seen = [], set()
        for code, label in _SELECT_OPTION.findall(select.group(0)):
            # Kur Korumalı is listed twice, bireysel and ticari, under one code.
            if not code or code in seen:
                continue
            seen.add(code)
            built.append(
                Product(
                    code=code,
                    name=html.unescape(label).strip(),
                    category="profit_share",
                    currencies=ACCOUNT_CURRENCIES.get(code, ("TRY",)),
                    raw={"Type": code},
                )
            )
        return built

    # ----- finance -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        chosen = self.find_product("finance", product)
        payload = self._plugin(
            "getFinanceCalculate",
            FINANCE_PAGE,
            ProfitRateByMe="false",
            FinanceType=json.dumps(chosen.raw, ensure_ascii=False),
            FinanceAmount=str(int(amount)),
            Maturity=str(int(term)),
            # ProfitRateByMe=true would let us impose our own rate. We never do:
            # the bank's rate is the answer.
            ProfitRate="0",
            Type="B",
            CreditType="B",
        )
        data = (payload or {}).get("Data") or {}
        installment = money(data.get("MonthlyInstallmentAmount"))
        # A zero instalment is what this endpoint returns instead of an error.
        if installment <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment plan for "
                f"{chosen.name} at {amount:,.0f} TL over {term} months."
            )

        return FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(term),
            installment=installment,
            total=money(data.get("TotalAmountTobeRefunded")),
            profit_rate=rate(data.get("ProfitRate")),
            annual_cost_rate=rate(data.get("AnnualCostRate")),
            fees={
                fee.get("FeeShortCode") or fee.get("FeeExplanation") or "fee":
                    money(fee.get("AmountWithTax"))
                for fee in data.get("AmortizationScheduleExpenses") or []
            },
            schedule=[
                PaymentRow(
                    order=int(row.get("InstallmentNo") or 0),
                    amount=money(row.get("InstallmentAmount")),
                    principal=money(row.get("Amount")),
                    profit=money(row.get("Dividend")),
                    taxes=money(row.get("KKDF")) + money(row.get("BSMV")),
                    remaining=money(row.get("RemainingAmount")),
                )
                for row in ((data.get("PaymentPlan") or {}).get("Rows") or [])
            ],
            raw=payload,
        )

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
        if currency not in chosen.currencies:
            raise UnsupportedProduct(
                f"{chosen.name} is not offered in {currency}. "
                f"Available: {', '.join(chosen.currencies)}."
            )

        for unit in _units(term_unit):
            payload = self._plugin(
                "getProfitShareCalculate",
                PROFIT_PAGE,
                DepositedAmount=str(int(amount)),
                Currency=currency,
                Maturity=str(int(term)),
                Period=unit.upper(),
                Type=chosen.code,
            )
            data = (payload or {}).get("Data") or {}
            net = money(data.get("NetProfit"))
            # Zeros mean "not offered", not "error" — Ara Dönem in day mode and
            # Kur Korumalı in every combination answer this way on their own
            # page too.
            if payload.get("Result") is True and net > 0:
                return ProfitShareQuote(
                    bank=self.name,
                    product=chosen,
                    amount=float(amount),
                    term=int(term),
                    currency=data.get("CurrencyCode") or currency,
                    term_unit=unit,
                    # Albaraka publishes no participation ratio, only the
                    # resulting rates.
                    ratio=None,
                    gross_profit=money(data.get("GrossProfit")),
                    net_profit=net,
                    gross_annual_rate=rate(data.get("GrossRate")),
                    net_annual_rate=rate(data.get("NetRate")),
                    raw=payload,
                )

        raise UnsupportedProduct(
            f"{self.display_name} published no profit-share rate for "
            f"{chosen.name} at {amount:,.0f} {currency} over {term} "
            f"{term_unit or 'month/day'}. The bank answers with zeros for "
            f"combinations it does not offer."
        )

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        payload = self._plugin("getExchangeRatesService", FX_PAGE)
        data = ((payload or {}).get("ExchangeRate") or {}).get("Data") or {}
        return [
            Rate(
                code=row["CurrencyName"],
                name=row.get("CurrencyCodeName") or "",
                buy=float(row.get("Bid") or 0),
                sell=float(row.get("Ask") or 0),
                unit="gram" if row["CurrencyName"] in ("XAU", "XAG") else "1",
            )
            for row in data.get("CurrencyPrices") or []
        ]

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert using the bank's own converter.

        Albaraka converts server-side, gold included, so nothing is computed
        here and the result is not flagged as derived.
        """
        source, target = source.upper(), target.upper()
        payload = self._plugin(
            "currencyConverter",
            FX_PAGE,
            **{
                "From": source,
                "To": target,
                "Amount": str(amount),
                "BuySellEntered": "A",
                "BuySellComputed": "S",
            },
        )
        value = payload.get("Data")
        if payload.get("Result") is not True or not isinstance(value, (int, float)):
            raise UnsupportedProduct(
                f"{self.display_name} did not convert {source} to {target}. "
                f"It quotes: {', '.join(r.code for r in self.rates())}, and TRY."
            )
        given = Decimal(str(amount))
        result = Decimal(str(value))
        return Conversion(
            bank=self.name,
            source=source,
            target=target,
            amount=given,
            result=result,
            rate=result / given if given else Decimal(0),
            derived=False,
        )


def _units(term_unit: str | None) -> list[str]:
    """Which periods to try, in order.

    Katılma Hesabı takes months or days; Ara Dönem takes months only and
    answers days with zeros. When the caller does not say, month is asked
    first and the quote reports the period that answered.
    """
    if term_unit:
        unit = term_unit.lower().rstrip("s")
        if unit not in ("day", "month"):
            raise ValueError(f"term_unit must be 'day' or 'month', got {term_unit!r}")
        return [unit]
    return ["month", "day"]
