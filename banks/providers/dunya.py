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

from ..models import Conversion, FinanceQuote, PaymentRow, ProfitShareQuote, Product, Rate
from ..parse import fold, money, money_en, rate
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
    # The homepage calculator's “Kâr Oranını Kendim Belirleyeceğim” toggle
    # submits these existing fields with `userSelected=true` and the value in
    # `userRate`.
    finance_input_capabilities = frozenset({"monthly_profit_rate"})

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

    def finance_quote(
        self, product: str, amount: float, term: int,
        monthly_profit_rate: float | None = None,
    ) -> FinanceQuote:
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
            # The UI lets users type a dot but normalises the form field to
            # Turkish decimal notation before submit; the endpoint reads a
            # dot as an out-of-range integer (2.6 -> 26).
            userRate=(
                f"{float(monthly_profit_rate):.2f}".replace(".", ",")
                if monthly_profit_rate is not None
                else "0"
            ),
            userSelected="true" if monthly_profit_rate is not None else "false",
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
        plan = payload.get("paymentPlanHTML") or ""
        return self._check_quote(FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(term),
            installment=installment,
            total=money(payload.get("totalPayment")),
            profit_rate=rate(payload.get("rate")),
            # "Yıllık kar oranı" -- read straight out of the same HTML plan the
            # schedule below comes from, not computed. It was left null only
            # because nothing here parsed that document; the bank states it.
            annual_cost_rate=rate(_plan_detail(plan, "Yıllık kar oranı")) or None,
            fees={},
            schedule=_plan_schedule(plan),
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
            # Checked 2026-08-16 against a live raw response: `grossProfitRate`
            # and `netProfitRate` (mapped below) are the only rates this
            # endpoint states -- no distinct participation-ratio field.
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


_PLAN_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_PLAN_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
# `<div class="title">Yıllık kar oranı</div><div class="val">42,410</div>` --
# the summary list above the schedule table, one label/value pair per line
# item. The schedule's own column headers are read the same way `ziraat.py`
# reads its Drupal table: by folded header text, not position, since a plan
# without KKDF/BSMV (nothing charges here yet) still has to line up.
_PLAN_DETAIL = re.compile(
    r'<div class="title">\s*([^<]*?)\s*</div>\s*<div class="val">\s*([^<]*?)\s*</div>', re.S,
)
_PLAN_COLUMNS = {
    "taksitno": "order",
    "vadetarihi": "due_date",
    "taksittutari": "amount",
    "anapara": "principal",
    "karpayi": "profit",
    "bsmv": "bsmv",
    "kkdf": "kkdf",
    "kalananaparatutari": "remaining",
}


def _plan_text(markup: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", markup))).strip()


def _plan_detail(plan_html: str, label: str) -> str:
    """One label/value pair out of the payment plan's summary list, by its
    Turkish label -- the same document the schedule table comes from, read a
    second way rather than a second request."""
    target = fold(label)
    for title, value in _PLAN_DETAIL.findall(plan_html):
        if fold(_plan_text(title)) == target:
            return _plan_text(value)
    return ""


def _plan_schedule(plan_html: str) -> list[PaymentRow]:
    """The per-instalment table out of the bank's own payment-plan HTML.

    The bank sends this on every `finance_quote` call; nothing here used to
    read past the two totals at the top. It is the bank's own schedule, not
    one built from its rate -- reading the rest of a document already fetched
    is not the same claim as computing an annuity.
    """
    rows = _PLAN_ROW.findall(plan_html)
    header_row = next((r for r in rows if "<th" in r), None)
    if header_row is None:
        return []
    headers = [fold(_plan_text(cell)) for cell in _PLAN_CELL.findall(header_row)]
    index = {_PLAN_COLUMNS[h]: i for i, h in enumerate(headers) if h in _PLAN_COLUMNS}
    if "amount" not in index:
        return []

    def cell(cells: list[str], key: str) -> str:
        position = index.get(key)
        return _plan_text(cells[position]) if position is not None and position < len(cells) else ""

    built = []
    for row in rows:
        if row is header_row or "<td" not in row:
            continue
        cells = _PLAN_CELL.findall(row)
        if len(cells) < len(index):
            continue
        built.append(PaymentRow(
            order=int(money(cell(cells, "order"))),
            amount=money(cell(cells, "amount")),
            principal=money(cell(cells, "principal")),
            profit=money(cell(cells, "profit")),
            taxes=money(cell(cells, "bsmv")) + money(cell(cells, "kkdf")),
            remaining=money(cell(cells, "remaining")),
            due_date=cell(cells, "due_date"),
        ))
    return built


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
