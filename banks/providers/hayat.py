"""Hayat Finans.

A Next.js site with clean JSON APIs: no token, no session, no WAF and no
formatted strings — the friendliest contract of the ten. The bank list's
`hayatfinans.com` does not resolve for `www` and the apex fails TLS; the live
host is `hayatfinans.com.tr`.

The offered surface is far narrower than the parameter names suggest. There is
no currency selector and no term selector on the calculator, and an API sweep
agrees: Turkish lira only, at the bank's own 32-day term, with three account
types priced differently. The loan endpoint exists but rejects every payload
shape tried and has no public calculator to observe, so it is not implemented.

Contract in docs/discovery/captured/hayat.md, exercised by verify_hayat.py.
"""

import json
import logging
import re
from decimal import Decimal

from ..models import Conversion, ProfitShareQuote, Product, Rate
from ..parse import fold, money, rate
from ..parse import term_unit as unit
from .base import RATE_ALIASES, BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://hayatfinans.com.tr"
API = f"{HOST}/api/integration"

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "referer": f"{HOST}/",
}

# Turkish lira only: FEC 1 (USD) and 19 (EUR) answer with empty data.
CURRENCY = "TRY"
FEC_TRY = 0

# The calculator has no term field. 32 days is what it asks for and what the
# published rates are quoted against.
STANDARD_TERM_DAYS = 32

# A hard floor, not a suggestion: below it the endpoint refuses outright. A
# caller that skips this check reports "0 TL profit" to someone whose real
# answer is "this account needs 50 000 TL to open".
MINIMUM_BALANCE = 50_000.0

# The account types and their labels are in a JSON blob on the homepage. Note
# `accountType`, not the option's `value`: the two differ by one and the API
# takes `accountType`.
_ACCOUNT = re.compile(
    r'"inputAccountTypeItemOptionID":\d+,"value":"[^"]*","label":"([^"]*)"'
    r'[^}]*?"accountType":(\d+)'
)


class Hayat(BaseBank):
    name = "hayat"
    display_name = "Hayat Finans Katılım Bankası"
    capabilities = frozenset({"products", "profit_share", "rates", "convert"})
    # This feed calls gold "ALT (gr)", the same as Kuveyt Türk's.
    rate_aliases = RATE_ALIASES
    notes = (
        "It publishes no financing or card calculator, and prices participation "
        "accounts in Turkish lira only, at its own 32-day term, from 50 000 TL."
    )

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category != "profit_share":
            raise UnsupportedProduct(
                f"{self.display_name} publishes no {category} catalogue. "
                + self._what_it_does()
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        page = self._text(f"{HOST}/", headers={"accept": "text/html"})
        built = []
        for label, account_type in _ACCOUNT.findall(page):
            name = json.loads(f'"{label}"') if "\\u" in label else label
            built.append(
                Product(
                    code=fold(name).upper(),
                    name=name,
                    category="profit_share",
                    min_amount=MINIMUM_BALANCE,
                    # 32 days is the shortest term any account here prices. No
                    # ceiling: the range differs per account and is unpublished
                    # — Katılma prices 32, 60, 90 and 365 days, Avantajlı only
                    # 32 — so claiming one would refuse terms the bank answers.
                    min_term=STANDARD_TERM_DAYS,
                    currencies=(CURRENCY,),
                    raw={"AccountType": int(account_type)},
                )
            )
        if not built:
            raise UnsupportedProduct(
                f"{self.display_name} returned no account types. They are parsed "
                f"out of the homepage, so its layout may have changed."
            )
        self._store(category, built)
        logger.debug("Loaded %d account type(s) from %s", len(built), self.name)
        return built

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
        if currency != CURRENCY:
            raise UnsupportedProduct(
                f"{self.display_name} prices participation accounts in "
                f"{CURRENCY} only; {currency} returns nothing."
            )
        # Checked before the call, so the answer is the real reason rather than
        # a zero the caller would have to interpret.
        if amount < MINIMUM_BALANCE:
            raise UnsupportedProduct(
                f"{chosen.name} needs at least {MINIMUM_BALANCE:,.0f} TL to open. "
                f"{amount:,.0f} TL is below the minimum, so the bank publishes no "
                f"figure for it."
            )
        days = term * 30 if unit(term_unit) == "month" else term
        # Checked against the days actually being asked for, not the caller's
        # number: 12 months is 360 days and is fine, 12 days is not.
        self._check_limits(chosen, amount=amount, term=days, term_label="days")

        try:
            payload = self._json(
                "POST",
                f"{API}/calculateprofitsharerate",
                headers=HEADERS,
                json={
                    "AccountType": chosen.raw["AccountType"],
                    "Maturity": 1,
                    "ProductGroup": 2,
                    "Money": int(amount),
                    "FEC": FEC_TRY,
                    "MaturityTerm": int(days),
                },
            ) or {}
        except ValueError as exc:
            # A term the bank does not price is a 400, not an empty answer.
            raise UnsupportedProduct(
                f"{self.display_name} does not price {chosen.name} over {days} "
                f"days. Its calculator offers its standard "
                f"{STANDARD_TERM_DAYS}-day term."
            ) from exc

        data = payload.get("data") or {}
        net = money(data.get("netProfitShare"))
        if payload.get("isSuccessful") is not True or net <= 0:
            raise UnsupportedProduct(
                f"{self.display_name} published no profit-share rate for "
                f"{chosen.name} over {days} days. Its calculator offers only its "
                f"standard {STANDARD_TERM_DAYS}-day term."
            )

        # Avantajlı Günlük Hesap returns one day's profit whatever term is
        # sent, so this quote goes through BaseBank._check_profit_share like
        # every other bank's — the figure has to follow from the bank's own
        # stated annual rate over the term asked about.

        return self._check_profit_share(ProfitShareQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(days),
            currency=CURRENCY,
            term_unit="day",
            ratio=None,
            gross_profit=money(data.get("grossProfitShare")),
            net_profit=net,
            gross_annual_rate=rate(data.get("grossProfitShareYearly")),
            net_annual_rate=rate(data.get("netProfitShareYearly")),
            raw=payload,
        ))

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        payload = self._json("GET", f"{API}/fxrate", headers=HEADERS) or {}
        return [
            Rate(
                code=(row.get("currencyShortCode") or "").strip(),
                name=(row.get("currencyDescription") or "").strip(),
                buy=money(row.get("currencyBid")),
                sell=money(row.get("currencyAsk")),
                # This feed labels metals the way Kuveyt Türk's does.
                unit="gram" if "(gr)" in (row.get("currencyShortCode") or "") else "1",
                as_of=(row.get("tranDate") or "")[:10],
            )
            for row in payload.get("data") or []
            if row.get("currencyShortCode")
        ]

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert using the published rate.

        Hayat has no converter endpoint, so the multiplication happens here in
        Decimal and the result is flagged as derived — the same agreed exception
        as Kuveyt Türk.
        """
        source, target = source.upper(), target.upper()
        if self.rate_aliases.get(source, source) == self.rate_aliases.get(target, target):
            value = Decimal(str(amount))
            return Conversion(
                bank=self.name, source=source, target=target,
                amount=value, result=value, rate=Decimal(1), derived=True,
            )
        quoted = {r.code.upper(): r for r in self.rates()}
        source_code = self.rate_aliases.get(source, source).upper()
        target_code = self.rate_aliases.get(target, target).upper()
        # Every rate is quoted against the lira, which the feed does not list.
        quoted.setdefault("TL", Rate(code="TL", name="Türk Lirası", buy=1.0, sell=1.0))
        for code in (source_code, target_code):
            if code not in quoted:
                raise UnsupportedProduct(
                    f"{self.display_name} does not quote {code!r}. "
                    f"Quoted: {', '.join(sorted(quoted))}."
                )
        if not quoted[target_code].sell:
            raise UnsupportedProduct(
                f"{self.display_name} quotes no sell rate for {target}, so the "
                f"conversion cannot be worked out from its published rates."
            )
        value = Decimal(str(amount))
        # Selling the source to the bank uses its buy rate; buying the target
        # uses its sell rate.
        conversion = (
            Decimal(str(quoted[source_code].buy)) / Decimal(str(quoted[target_code].sell))
        )
        return Conversion(
            bank=self.name,
            source=source,
            target=target,
            amount=value,
            result=value * conversion,
            rate=conversion,
            derived=True,
        )
