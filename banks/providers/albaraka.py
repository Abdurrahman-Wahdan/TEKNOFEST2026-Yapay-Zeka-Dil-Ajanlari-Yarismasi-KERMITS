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

from ..models import (
    Conversion,
    FinanceQuote,
    PaymentRow,
    ProfitShareQuote,
    Product,
    Rate,
)
from ..parse import money, rate
from .base import BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://www.albaraka.com.tr"
PLUGINS = f"{HOST}/plugins/"
LANG_ID = "bf2689d9-071e-4a20-9450-b1dbdd39778f"

FINANCE_PAGE = f"{HOST}/tr/hesaplama-araclari/finansman-hesaplama/ihtiyac-finansmani-hesaplama"
PROFIT_PAGE = f"{HOST}/tr/hesaplama-araclari/kar-payi-hesaplama"
FX_PAGE = f"{HOST}/tr/hesaplama-araclari/doviz-cevirici"

# Quoted per gram on the board, so they must not share a row with a unit price.
_METALS = {"XAU", "XAG", "XPT", "XPD"}

HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "x-requested-with": "XMLHttpRequest",
    "adrum": "isAjax:true",
}

# Each account has its own currency select, tagged with the account code as a
# CSS class, and every option carries the bank's own limits for that currency in
# a `jsonData` attribute:
#
#     <select class="select2 karPayiCurrency radioKarpayiKatilma KTLMHSP">
#       <option value="XAU" jsonData='{"MinAmount":150,"MinDate":32,"MaxDate":1095}'>
#
# This used to be a hardcoded table copied from the contract doc, which was
# right about *which* currencies exist and silent about their limits -- so gold
# looked like it had no minimum when the bank states 150 grams against 250 for
# every other currency, and the term band 32-1095 days was missing entirely.
# Read from the page instead: a currency the bank drops disappears on its own.
_CURRENCY_SELECT = re.compile(
    r'<select[^>]*class="[^"]*karPayiCurrency[^"]*?(\w+)"[^>]*>(.*?)</select>', re.S
)
# jsonData is optional: Kur Korumalı lists GBP and XAU with no limits attached,
# and dropping them would narrow the currency list the bank actually offers.
_CURRENCY_OPTION = re.compile(
    r"<option[^>]*value=\"([^\"]+)\"(?:[^>]*jsonData='([^']*)')?[^>]*>", re.S
)

# The attribute is single-quoted and the JSON inside is HTML-escaped, so the
# obvious double-quote pattern matches nothing and reads as "no products here".
_OPTION = re.compile(r"<option[^>]*value='(\{.*?\})'", re.S)
_SELECT_OPTION = re.compile(r"<option[^>]*value=\"([^\"]+)\"[^>]*>([^<]*)<", re.S)
_ACCOUNT_SELECT = re.compile(r"<select[^>]*selectTypeKarpayi.*?</select>", re.S)


class Albaraka(BaseBank):
    name = "albaraka"
    display_name = "Albaraka Türk Katılım Bankası"
    # No card instalment calculator and no leasing calculator: those raise
    # through the base class rather than answering with nothing.
    capabilities = frozenset(
        {"products", "finance", "profit_share", "rates", "convert"}
    )
    # An F5 WAF fingerprints the TLS handshake and rejects httpx whatever
    # the headers, so this bank is called through curl_cffi.
    transport = "impersonate"

    def _plugin(self, plugin: str, page: str, **params):
        body = {
            "langId": LANG_ID,
            "language": "tr",
            "Slug": page.rsplit("/", 1)[-1],
            "searchUrl": "/tr/arama",
            "customFinancingName": "",
            **params,
        }
        return self._json(
            "GET",
            PLUGINS + plugin,
            headers={**HEADERS, "referer": page},
            params=body,
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

    def _finance_products(self) -> list[Product]:
        page = self._text(FINANCE_PAGE)
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

    def _currency_limits(self, page: str) -> dict[str, dict[str, dict]]:
        """{account code: {currency: {min_amount, min_term, max_term}}}.

        The bank states these per currency, not per account, and gold differs
        from the rest -- 150 grams against 250 units everywhere else.
        """
        found: dict[str, dict[str, dict]] = {}
        for match in _CURRENCY_SELECT.finditer(page):
            code, inner = match.group(1), match.group(2)
            for currency, blob in _CURRENCY_OPTION.findall(inner):
                try:
                    data = json.loads(html.unescape(blob)) if blob else {}
                except ValueError:
                    data = {}
                found.setdefault(code, {})[currency] = {
                    "min_amount": money(data.get("MinAmount")) or None,
                    "max_amount": money(data.get("MaxAmount")) or None,
                    "min_term": int(data["MinDate"]) if data.get("MinDate") else None,
                    "max_term": int(data["MaxDate"]) if data.get("MaxDate") else None,
                }
        return found

    def _profit_share_products(self) -> list[Product]:
        page = self._text(PROFIT_PAGE)
        limits = self._currency_limits(page)
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
            per_currency = limits.get(code, {})
            bands = [b for b in per_currency.values() if b.get("min_term")]
            built.append(
                Product(
                    code=code,
                    name=html.unescape(label).strip(),
                    category="profit_share",
                    currencies=tuple(per_currency) or ("TRY",),
                    # The lowest minimum across currencies, so the product-level
                    # bound never refuses something a currency actually allows;
                    # the exact per-currency figure is checked at quote time.
                    min_amount=min(
                        (b["min_amount"] for b in per_currency.values() if b["min_amount"]),
                        default=None,
                    ),
                    min_term=min((b["min_term"] for b in bands), default=None),
                    max_term=max((b["max_term"] for b in bands if b["max_term"]), default=None),
                    raw={"Type": code, "limits": per_currency},
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

        return self._check_quote(FinanceQuote(
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
        if currency not in chosen.currencies:
            raise UnsupportedProduct(
                f"{chosen.name} is not offered in {currency}. "
                f"Available: {', '.join(chosen.currencies)}."
            )

        # The bank states a different minimum per currency -- 150 grams of gold
        # against 250 lira -- so it is checked against the one being asked for.
        # Without this the endpoint answers zeros and the refusal blames the
        # rate, when the real answer is "that is below the minimum".
        band = (chosen.raw.get("limits") or {}).get(currency) or {}
        if band.get("min_amount") and amount < band["min_amount"]:
            raise UnsupportedProduct(
                f"{chosen.name} needs at least {band['min_amount']:,.0f} "
                f"{currency} at {self.display_name}; {amount:,.0f} is below it."
            )

        for unit in _units(self._require_unit(term, term_unit)):
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
                return self._check_profit_share(ProfitShareQuote(
                    bank=self.name,
                    product=chosen,
                    amount=float(amount),
                    term=int(term),
                    currency=data.get("CurrencyCode") or currency,
                    term_unit=unit,
                    # Albaraka publishes no participation ratio, only the
                    # resulting rates. Reconfirmed 2026-08-16 against a live
                    # raw response: `GrossRate`/`NetRate` (mapped below) are
                    # the whole of what this endpoint states.
                    ratio=None,
                    gross_profit=money(data.get("GrossProfit")),
                    net_profit=net,
                    gross_annual_rate=rate(data.get("GrossRate")),
                    net_annual_rate=rate(data.get("NetRate")),
                    raw=payload,
                ))

        # Two different silences, told apart by whether the endpoint accepted
        # the request at all. A rejected call means the combination is not
        # offered; an accepted one returning 0,00 means the account exists and
        # the bank is currently distributing nothing on it -- which is its own
        # answer, and reporting it as "not offered" would be false.
        accepted = bool((payload or {}).get("Result")) and (
            ((payload or {}).get("Data") or {}).get("CurrencyCode")
        )
        if accepted:
            raise UnsupportedProduct(
                f"{self.display_name} offers {chosen.name} in {currency} but is "
                f"currently publishing a 0% rate on it over {term} "
                f"{term_unit or 'month/day'}, so there is no return to compare."
            )
        raise UnsupportedProduct(
            f"{self.display_name} published no profit-share rate for "
            f"{chosen.name} at {amount:,.0f} {currency} over {term} "
            f"{term_unit or 'month/day'}. The bank answers with zeros for "
            f"combinations it does not offer."
        )

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        """The full board: four by endpoint, the rest off the bank's own page.

        `getExchangeRatesService` returns USD, EUR, GBP and gold whatever page
        it is called from -- it is the homepage widget's feed, and it is the
        only place carrying the quote time. `/tr/doviz-kurlari` renders all
        twenty-two server-side, silver, platinum, palladium, the Gulf and Nordic
        currencies included.

        Using only the plugin left this bank looking like it published four
        instruments. The endpoint still wins where the two overlap; the page
        fills in what it does not carry.
        """
        payload = self._plugin("getExchangeRatesService", FX_PAGE)
        data = ((payload or {}).get("ExchangeRate") or {}).get("Data") or {}
        as_of = data.get("TranDate") or ""

        found: dict[str, Rate] = {}
        for row in data.get("CurrencyPrices") or []:
            code = row["CurrencyName"]
            found[code] = Rate(
                code=code,
                name=row.get("CurrencyCodeName") or "",
                buy=float(row.get("Bid") or 0),
                sell=float(row.get("Ask") or 0),
                unit="gram" if code in _METALS else "1",
                as_of=as_of,
            )
        for code, name, buy, sell in self._page_rates():
            if code not in found:
                found[code] = Rate(code=code, name=name, buy=buy, sell=sell,
                                   unit="gram" if code in _METALS else "1",
                                   as_of=as_of)
        return list(found.values())

    def _page_rates(self) -> list[tuple[str, str, float, float]]:
        """Every row of `/tr/doviz-kurlari`, which has no endpoint behind it."""
        page = self._text(f"{HOST}/tr/doviz-kurlari")
        out: list[tuple[str, str, float, float]] = []
        for table in re.findall(r"<table[^>]*>.*?</table>", page, re.S):
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
                cells = [
                    html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
                    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
                ]
                cells = [c for c in cells if c]
                if len(cells) < 3:
                    continue
                # "Amerikan Doları(USD)" -- the parenthesised code is the key.
                match = re.search(r"\(([A-Z]{3})\)\s*$", cells[0])
                if not match:
                    continue
                buy, sell = money(cells[1]), money(cells[2])
                if buy > 0 and sell > 0:
                    out.append((match.group(1),
                                re.sub(r"\([A-Z]{3}\)\s*$", "", cells[0]).strip(),
                                buy, sell))
        return out

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


def _units(term_unit: str) -> list[str]:
    """Which periods to try.

    Katılma Hesabı takes months or days; Ara Dönem takes months only and answers
    days with zeros. The caller's unit is honoured rather than substituted — a
    day term that this product does not offer is refused, not quietly re-asked
    as months, because those are different questions.

    The unit is always stated: BaseBank._require_unit refuses a bare number.
    """
    return [term_unit]
