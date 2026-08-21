"""Vakıf Katılım.

Unigate like Albaraka, but guarded by an ASP.NET anti-forgery token instead of a
WAF. Every plugin is a POST whose **body carries only the token** — the
parameters themselves go in the query string. The token belongs to the page, so
it is read from the calculator page being called.

Two failure shapes to expect, neither of them an HTTP error: a zero-length 200
body (gold past one year), and an `errorMessage` inside an otherwise normal 200.
The contract is docs/discovery/captured/vakif.md, exercised by verify_vakif.py.
"""

import html as htmlmod
import logging
import re
from decimal import Decimal

from ..models import (
    CardInstallmentQuote,
    Conversion,
    FinanceQuote,
    PaymentRow,
    ProfitShareQuote,
    Product, Rate,
)
from ..parse import money, rate
from ..parse import term_unit as unit
from .base import BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://www.vakifkatilim.com.tr"
TOOLS = f"{HOST}/tr/yardimci-sayfalar/hesaplama-araclari"
LANG_ID = "bf2689d9-071e-4a20-9450-b1dbdd39778f"

FINANCE_PAGE = f"{TOOLS}/finansman-hesaplama"
PROFIT_PAGE = f"{TOOLS}/kar-payi-hesaplama"
CARD_PAGE = f"{TOOLS}/kart-taksit-hesaplama"
FX_PAGE = f"{TOOLS}/doviz-cevirici-ve-kurlar"

HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "accept": "application/json, text/javascript, */*; q=0.01",
}

FEC_BY_CURRENCY = {"TRY": "0", "USD": "1", "EUR": "19", "XAU": "24"}
CURRENCY_BY_FEC = {v: k for k, v in FEC_BY_CURRENCY.items()}

# The kâr payı term select offers exactly these day counts. "1" (kırık vade) is
# left out: it returns an empty body for every currency.
EXPIRY_DAYS = (31, 91, 180, 364, 366)

# One card, and the page states its range rather than serving a catalogue.
CARD = Product(
    code="FK",
    name="Ferah Kart",
    category="card",
    min_term=1,
    max_term=12,
    raw={"cardType": "FK"},
)

# The converter names its currencies its own way; these are the standard codes
# a caller would use for the same thing.
# The rate feed names gold and silver "ALT"/"GMS"; the converter page and every
# other bank spell them with the gram unit attached. One spelling downstream.
_RATE_CODES = {"ALT": "ALT (gr)", "GMS": "GMS (gr)", "PLT": "PLT (gr)"}

CONVERTER_ALIASES = {
    "TRY": "TL",
    "XAU": "ALT (gr)",
    "GOLD": "ALT (gr)",
    "ALTIN": "ALT (gr)",
    "XAG": "GMS (gr)",
    "GUMUS": "GMS (gr)",
    "XPT": "PLT (gr)",
}

_OPTION = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]*?)\s*</option>', re.S)


def _select(html: str, name: str) -> list[tuple[str, str]]:
    """The value/label pairs of one named select on a page."""
    block = re.search(
        rf'<select[^>]*(?:name|id)="{name}"[^>]*>(.*?)</select>', html, re.S
    )
    if not block:
        return []
    return [
        (v, htmlmod.unescape(re.sub(r"\s+", " ", label)).strip())
        for v, label in _OPTION.findall(block.group(1))
        if v
    ]


class Vakif(BaseBank):
    name = "vakif"
    display_name = "Vakıf Katılım Bankası"
    capabilities = frozenset(
        {"products", "finance", "profit_share", "card", "convert", "rates"}
    )
    notes = (
        "It converts currency but publishes no buy/sell rate feed: its only "
        "currency service is the converter, so its board is derived from that."
    )
    # Plain httpx, plus a per-page anti-forgery token on every plugin call.
    transport = "csrf"
    # The public calculator exposes "Kâr Oranı Kendin Belirle" and passes the
    # selected value through its existing profitRate parameter.
    finance_input_capabilities = frozenset({"monthly_profit_rate"})

    def _plugin(self, plugin: str, page: str, **params):
        """POST a plugin: parameters in the query string, token in the body.

        A token the server has stopped accepting looks like a failed call, so
        one retry replaces it before giving up.
        """
        for refresh in (False, True):
            try:
                return self._json(
                    "POST",
                    f"{HOST}/plugins/{plugin}",
                    headers={**HEADERS, "referer": page},
                    params={"langId": LANG_ID, "language": "tr", **params},
                    data={"__RequestVerificationToken": self._token(page, refresh=refresh)},
                )
            except ValueError:
                if refresh:
                    raise
        return None

    @staticmethod
    def _usable(payload) -> dict:
        """Reject the two ways this bank says "nothing here" without erroring."""
        if not payload:
            # A zero-length 200 body. Gold past one year answers this way.
            return {}
        if payload.get("errorMessage"):
            raise UnsupportedProduct(payload["errorMessage"])
        return payload

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in ("finance", "profit_share", "card"):
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} catalogue. "
                f"Available: finance, profit_share, card."
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        if category == "card":
            built = [CARD]
        elif category == "finance":
            built = self._finance_products()
        else:
            built = self._profit_share_products()

        self._store(category, built)
        logger.debug("Loaded %d %s product(s) from %s", len(built), category, self.name)
        return built

    def _finance_products(self) -> list[Product]:
        page = self._text(FINANCE_PAGE)
        built = []
        for code, label in _select(page, "LoanType"):
            terms = self._plugin("FinancingInstallment", FINANCE_PAGE, financingType=code)
            options = (terms or {}).get("installments") or []
            built.append(
                Product(
                    code=code,
                    name=label,
                    category="finance",
                    # The allowed instalment counts come from the bank, not from
                    # a guess: konut runs to 120 where ihtiyaç stops at 36.
                    min_term=int(options[0]["code"]) if options else None,
                    max_term=int(options[-1]["code"]) if options else None,
                    raw={"financingType": code, "installments": options},
                )
            )
        if not built:
            raise UnsupportedProduct(
                f"{self.display_name} returned no finance products. The catalogue "
                f"is parsed out of the page, so the page layout may have changed."
            )
        return built

    def _profit_share_products(self) -> list[Product]:
        """One account type, priced per currency and term.

        The page offers a currency select and a term select rather than named
        products, so the currencies are the product's currencies and the terms
        its term range.
        """
        return [
            Product(
                code="KAH",
                name="Katılma Hesabı",
                category="profit_share",
                min_term=EXPIRY_DAYS[0],
                max_term=EXPIRY_DAYS[-1],
                currencies=tuple(FEC_BY_CURRENCY),
                raw={"accountType": "KAH", "expiries": list(EXPIRY_DAYS)},
            )
        ]

    # ----- finance -----

    def finance_quote(
        self,
        product: str,
        amount: float,
        term: int,
        monthly_profit_rate: float | None = None,
    ) -> FinanceQuote:
        chosen = self.find_product("finance", product)
        self._check_limits(chosen, amount=amount, term=term)
        payload = self._usable(
            self._plugin(
                "FinancingComputationExecute",
                FINANCE_PAGE,
                financingType=chosen.code,
                amount=str(int(amount)),
                numberOfInstallments=str(int(term)),
                profitRate=str(monthly_profit_rate) if monthly_profit_rate is not None else "null",
                calculateType="1",
            )
        )
        installment = money(payload.get("installmentAmount"))
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
            total=money(payload.get("totalAmount")),
            profit_rate=rate(payload.get("profitRate")),
            # Checked 2026-08-16, not assumed: dumped this endpoint's full raw
            # response (no APR field beyond what is mapped) and pulled every
            # plugin URL this bank's calculator widgets can call out of its
            # own `config.min.js` (the site's complete AJAX registry) -- no
            # annual-cost endpoint exists anywhere in it. The bank states a
            # monthly rate and totals, and nothing that annualises them.
            annual_cost_rate=None,
            fees={
                "appraisement": money(payload.get("appraisementFee")),
                "mortgage_release": money(payload.get("mortgageReleaseFee")),
            },
            schedule=self._schedule(chosen, amount, term, monthly_profit_rate),
            raw=payload,
        ))

    def _schedule(
        self,
        product: Product,
        amount: float,
        term: int,
        monthly_profit_rate: float | None = None,
    ) -> list[PaymentRow]:
        """The payment plan, which is a second call at this bank.

        A failure here must not lose the quote: the instalment and total are
        already in hand and are what most questions want, so a missing plan
        comes back as an empty schedule rather than an error.
        """
        try:
            plan = self._usable(
                self._plugin(
                    "InstallmentPayBack",
                    FINANCE_PAGE,
                    financingType=product.code,
                    amount=str(int(amount)),
                    numberOfInstallments=str(int(term)),
                    profitRate=str(monthly_profit_rate) if monthly_profit_rate is not None else "null",
                    calculateType="1",
                )
            )
        except ValueError as exc:
            logger.debug("No payment plan for %s: %s", product.code, exc)
            return []

        return [
            PaymentRow(
                order=int(money(row.get("taksitSayisi"))),
                amount=money(row.get("taksitTutari")),
                principal=money(row.get("anaPara")),
                profit=money(row.get("karTutari")),
                # "bsmfTutari" is the bank's own spelling of BSMV.
                taxes=money(row.get("kkdfTutari")) + money(row.get("bsmfTutari")),
                remaining=money(row.get("kalanAnaParaTutari")),
            )
            for row in plan.get("tableBody") or []
        ]

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
        if currency not in FEC_BY_CURRENCY:
            raise UnsupportedProduct(
                f"{self.display_name} does not offer {currency} participation "
                f"accounts. Available: {', '.join(FEC_BY_CURRENCY)}."
            )
        self._check_limits(chosen, amount=amount)
        days = term * 30 if unit(term_unit) == "month" else term
        expiry = self._band(days, EXPIRY_DAYS)

        payload = self._usable(
            self._plugin(
                "GrossAmountCalculationJson",
                PROFIT_PAGE,
                accountType=chosen.code,
                currencyUnit=FEC_BY_CURRENCY[currency],
                principal=str(int(amount)),
                expiry=str(expiry),
            )
        )
        net = money(payload.get("netProfit"))
        if net <= 0:
            raise self._no_rate(chosen, amount, currency, f"{expiry} days")
        return self._check_profit_share(ProfitShareQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=expiry,
            currency=currency,
            term_unit="day",
            # Checked 2026-08-16 against a live raw response: this endpoint
            # states `grossRate`/`netRate` (mapped below) and nothing else --
            # no distinct participation-ratio field the way Kuveyt Türk's
            # `ProfitShareRatio` is. Not in this bank's plugin registry either.
            ratio=None,
            gross_profit=money(payload.get("grossProfit")),
            net_profit=net,
            gross_annual_rate=rate(payload.get("grossRate")),
            net_annual_rate=rate(payload.get("netRate")),
            raw=payload,
        ))

    # ----- cards -----

    def card_installment_quote(
        self, card: str, amount: float, installments: int
    ) -> CardInstallmentQuote:
        chosen = self.find_product("card", card)
        # The card declares 1-12; without this it answers 99 instalments.
        self._check_limits(chosen, amount=amount, term=installments,
                           term_label="instalments")
        payload = self._usable(
            self._plugin(
                "CardComputationExecute",
                CARD_PAGE,
                cardType=chosen.code,
                amount=str(int(amount)),
                numberOfInstallments=str(int(installments)),
                profitRate="null",
            )
        )
        installment = money(payload.get("installmentAmount"))
        if installment <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment plan for "
                f"{chosen.name} at {amount:,.0f} TL over {installments} instalments."
            )
        return CardInstallmentQuote(
            bank=self.name,
            card=chosen,
            amount=float(amount),
            installments=int(installments),
            installment=installment,
            total=money(payload.get("totalAmount")),
            profit_rate=rate(payload.get("profitRate")),
            raw=payload,
        )

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        """The bank's own published board: 4 rows by endpoint, the rest by page.

        Two sources, because the bank splits it that way and neither alone is
        the whole answer:

        - `plugins/HomePageCurrencyData` returns USD, EUR, gold and silver as
          JSON, and is the only place carrying the quote time ("15/08/2026
          15:49 İnternet Şube kurlarımızdır").
        - The converter page renders the full sixteen -- AUD, CHF, JPY, SAR,
          QAR, platinum and the rest -- server-side, with no endpoint behind it.

        The endpoint wins where the two overlap, and its timestamp stamps every
        row. Nothing here is derived: an earlier version inverted the converter
        to get a sell price, which matched the published figure exactly but
        produced only four instruments and no quote time.
        """
        feed = self._json(
            "GET",
            f"{HOST}/plugins/HomePageCurrencyData",
            headers={**HEADERS, "referer": FX_PAGE},
            params={"langId": LANG_ID, "language": "tr"},
        ) or {}
        as_of = (feed.get("description") or "").strip()

        found: dict[str, Rate] = {}
        for row in feed.get("homeExchange") or []:
            code = (row.get("currencyCode") or "").strip()
            buy, sell = money(row.get("buyRate")), money(row.get("sellRate"))
            if not code or buy <= 0 or sell <= 0:
                continue
            found[_RATE_CODES.get(code, code)] = Rate(
                code=_RATE_CODES.get(code, code),
                name=(row.get("currencyName") or code).strip(),
                buy=buy, sell=sell,
                unit="gram" if code in _RATE_CODES else "1",
                as_of=as_of,
            )

        for code, name, buy, sell in self._page_rates():
            if code in found:
                continue
            found[code] = Rate(code=code, name=name, buy=buy, sell=sell,
                               unit="gram" if "(gr)" in code else "1", as_of=as_of)
        return list(found.values())

    def _page_rates(self) -> list[tuple[str, str, float, float]]:
        """The full board, read off the converter page's own tables.

        Server-rendered with no endpoint behind it, so this is the only way to
        reach the twelve instruments the JSON feed leaves out.
        """
        page = self._text(FX_PAGE)
        out: list[tuple[str, str, float, float]] = []
        for table in re.findall(r"<table[^>]*>.*?</table>", page, re.S):
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
                cells = [
                    htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
                ]
                cells = [c for c in cells if c]
                if len(cells) < 3:
                    continue
                # "USD\n - \n Amerikan Doları" in one cell.
                parts = [p.strip() for p in re.split(r"\s*-\s*", cells[0], maxsplit=1)]
                code = parts[0]
                if not code or code.lower() in ("sembol",):
                    continue
                buy, sell = money(cells[1]), money(cells[2])
                if buy > 0 and sell > 0:
                    out.append((code, parts[-1] if len(parts) > 1 else code, buy, sell))
        return out

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Vakıf converts server-side, so nothing is computed here."""
        source, target = source.upper(), target.upper()
        if source == target:
            value = Decimal(str(amount))
            return Conversion(
                bank=self.name, source=source, target=target,
                amount=value, result=value, rate=Decimal(1), derived=False,
            )
        ids = self._currency_ids()
        # The bank's own names for these, used for the request only — the
        # answer reports the codes the caller asked about.
        source_code = CONVERTER_ALIASES.get(source, source)
        target_code = CONVERTER_ALIASES.get(target, target)
        for code in (source_code, target_code):
            if code not in ids:
                raise UnsupportedProduct(
                    f"{self.display_name} does not convert {code!r}. "
                    f"Available: {', '.join(sorted(ids))}."
                )
        payload = self._json(
            "GET",
            f"{HOST}/plugins/CurrencyConverter",
            headers={**HEADERS, "referer": FX_PAGE},
            params={
                "langId": LANG_ID,
                "language": "tr",
                "amount": str(amount),
                "InputCurrencyType": ids[source_code],
                "convertCurrencyType": ids[target_code],
            },
        )
        # Turkish-formatted, and both sides of the spread come back equal here.
        result = money(payload.get("bankaAlis") if payload else 0)
        if result <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} did not convert {source} to {target}."
            )
        given = Decimal(str(amount))
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


    def _currency_ids(self) -> dict[str, str]:
        """Code -> the numeric id the converter takes, from the bank's own list.

        These are not the FEC codes the kâr payı endpoint uses; the two number
        their currencies differently, so neither map is reusable for the other.
        """
        cached = self._scratch.get("currencies")
        if cached is None:
            rows = self._json(
                "GET",
                f"{HOST}/plugins/DetailCurrencyListData",
                headers={**HEADERS, "referer": FX_PAGE},
                params={"langId": LANG_ID, "language": "tr", "currencyTypeId": "3"},
            )
            cached = {
                (row.get("currencyName") or "").strip(): str(row.get("currencyId"))
                for row in ((rows or {}).get("currencies") or [])
                if row.get("currencyName")
            }
            self._scratch["currencies"] = cached
        return cached
