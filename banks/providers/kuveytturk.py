"""Kuveyt Türk Katılım Bankası.

Every calculator is served by one opaque path, `\\ck0d84?<hash>`, with a
different hash per calculator. It looks like a tracking beacon; it is the
calculator. The contract is recorded in docs/discovery/captured/kuveytturk.md
and exercised end to end by docs/discovery/verify_kuveytturk.py.

Requests need no cookie, session or CSRF token — only the headers below and a
referer pointing at the matching calculator page.
"""

import collections
import logging
from decimal import Decimal

from ..http import request_json
from ..models import (
    CardInstallmentQuote,
    Conversion,
    FinanceQuote,
    PaymentRow,
    ProfitShareQuote,
    Product,
    Rate,
)
from .base import BaseBank, UnsupportedProduct, fold

logger = logging.getLogger(__name__)

BASE = "https://www.kuveytturk.com.tr/ck0d84?"
PAGE = "https://www.kuveytturk.com.tr/hesaplama-araclari/"

# One hash per calculator. Stable across sessions but page-embedded: re-extract
# them from the page if these start 404ing, rather than reading it as an outage.
CATALOGUE = "9592031673D7885E535AEF67BC5D9213"
FINANSMAN = "30134915811C6D92B8F34A01FCF910EE"
PROFIT_SHARE = "1E32FE5C30C44BF2B51A08D1756ADEEB"
RATES = "C24AD4C0FDA76C73081889B634A8C039"
CARD = "AD36E047B34B678B1F7A995EA1821ABB"

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "x-requested-with": "XMLHttpRequest",
    "x-bone-language": "TR",
}

# One catalogue endpoint feeds every calculator; only p1 changes. It is the
# only place product codes exist, so nothing here is hardcoded from them.
CALCULATORS = {
    "finance": ("LoanCalculator", "finansman-hesaplama"),
    "profit_share": ("ProfitSharingCalculator", "kar-payi-hesaplama"),
    "card": ("CreditCardInstallmentCalculator", "kart-taksit-hesaplama"),
}

# FEC is the bank's own currency code.
FEC_BY_CURRENCY = {"TRY": "0", "USD": "1", "EUR": "19", "XAU": "24", "GBP": "15"}
CURRENCY_BY_FEC = {v: k for k, v in FEC_BY_CURRENCY.items()}

# The catalogue names its per-currency amount limits after the currency.
FEC_SUFFIX = {"0": "Tl", "1": "Usd", "19": "Euro", "24": "Gr", "15": "Sterlin"}

# The profit-share endpoint counts days. A month is sent as 30 of them, which
# is the multiple the bank's own table uses (30, 90, 180). See _day_attempts.
DAYS_PER_MONTH = 30

# Kuveyt Türk publishes no profit-share rate for Yuvam at all: every currency,
# term and payload shape answers 200 with zeros, and their own page shows the
# same. Refused up front instead of being retried as a bug.
NO_PUBLISHED_RATE = {
    "YUVAMKATILMA": "the bank returns zeros for Yuvam on its own page too",
}

# The rates feed names metals its own way.
RATE_ALIASES = {
    "TRY": "TL",
    "XAU": "ALT (gr)",
    "GOLD": "ALT (gr)",
    "ALTIN": "ALT (gr)",
    "XAG": "GMS (gr)",
    "SILVER": "GMS (gr)",
    "GUMUS": "GMS (gr)",
}


def _params(entry: dict) -> dict[str, list[str]]:
    """Catalogue Parameters is a flat key/value list with repeated keys."""
    out = collections.defaultdict(list)
    for item in entry.get("Parameters", []):
        out[item["Key"]].append(item["Value"])
    return out


def _first(values: list[str], cast=int):
    return cast(values[0]) if values else None


class KuveytTurk(BaseBank):
    name = "kuveytturk"
    display_name = "Kuveyt Türk Katılım Bankası"
    capabilities = frozenset({"finance", "profit_share", "card", "rates", "convert"})

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in CALCULATORS:
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} catalogue. "
                f"Available: {', '.join(sorted(CALCULATORS))}."
            )
        cached = self._catalogue.get(category)
        if cached is not None:
            return cached

        calculator, page = CALCULATORS[category]
        entries = request_json(
            "GET",
            f"{BASE}{CATALOGUE}&p1={calculator}",
            headers={**HEADERS, "referer": PAGE + page},
        )
        built = [self._to_product(e, category) for e in entries]
        self._catalogue[category] = built
        logger.debug("Loaded %d %s product(s) from %s", len(built), category, self.name)
        return built

    def _to_product(self, entry: dict, category: str) -> Product:
        p = _params(entry)
        title = entry["Title"]
        code = p["ProductCode"][0] if p["ProductCode"] else ""

        if category == "finance":
            return Product(
                code=code,
                name=title,
                category=category,
                min_amount=_first(p["DefaultAmountMin"], float),
                max_amount=_first(p["DefaultAmountMax"], float),
                # The base tier only. Kuveyt Türk tiers its term limits by
                # amount (MaturityTermMax2 at 125 000, Max3 at 250 000); the
                # full set stays in raw and the endpoint has the final word,
                # answering 400 with a usable sentence when a term is refused.
                min_term=_first(p["MaturityTermMin"]),
                max_term=_first(p["MaturityTermMax"]),
                raw=entry,
            )

        if category == "profit_share":
            currencies = tuple(
                CURRENCY_BY_FEC[f] for f in p["FEC"] if f in CURRENCY_BY_FEC
            )
            suffix = FEC_SUFFIX.get(p["FEC"][0], "Tl") if p["FEC"] else "Tl"
            return Product(
                # Three of the seven accounts carry no ProductCode of their own
                # and are selected by group alone. The request still sends the
                # empty code from raw; this one is for the user to name.
                code=code or _slug(title),
                name=title,
                category=category,
                min_amount=_first(p[f"MaturityDayMinAmount{suffix}"], float),
                max_amount=_first(p[f"MaturityDayMaksAmount{suffix}"], float),
                min_term=_first(p["MaturityTermMinDay"]),
                max_term=_first(p["MaturityTermMaksDay"]),
                currencies=currencies or ("TRY",),
                raw=entry,
            )

        return Product(
            code=code,
            name=title,
            category=category,
            min_amount=_first(p["MinAmount"], float) or _first(p["Amount"], float),
            max_amount=_first(p["MaxAmount"], float),
            min_term=_first(p["MinInstallmentValue"]),
            # Over-promised: Sağlam Kart Troy declares 12 and the endpoint 404s
            # above 9. Reported as declared; the endpoint decides.
            max_term=_first(p["MaxInstallmentValue"]),
            raw=entry,
        )

    # ----- finance -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        chosen = self.find_product("finance", product)
        body = {
            "i": False,
            "p1": "1",
            "p2": str(int(amount)),
            "p3": str(int(term)),
            "p4": chosen.code,
            "p5": chosen.code,
            "p6": "0.00",
            "p7": "",
            # Not cosmetic. Two entries share ELKTRARACSARJUNITE with different
            # limits and the endpoint validates the term against the entry
            # named here, so the title must be the one this product came from.
            "p8": chosen.name,
        }
        payload = request_json(
            "POST",
            BASE + FINANSMAN,
            headers={**HEADERS, "referer": PAGE + "finansman-hesaplama"},
            json=body,
            # The endpoint intermittently answers 200 with an empty Meta.
            retry_if=lambda d: not ((d or {}).get("Meta") or {}).get("InstallmentPayment"),
        )

        meta = (payload or {}).get("Meta") or {}
        installment = meta.get("InstallmentPayment") or 0
        if not installment:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment plan for "
                f"{chosen.name} at {amount:,.0f} TL over {term} months."
            )

        return FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=float(meta.get("LoanAmount") or amount),
            # InstallmentCount comes back as a string.
            term=int(meta.get("InstallmentCount") or term),
            installment=float(installment),
            total=float(meta.get("TotalAmount") or 0),
            profit_rate=float(meta.get("ProfitRate") or 0),
            annual_cost_rate=_optional_float(meta.get("YearlyCost")),
            fees={
                "allocation": float(meta.get("AllocationAmount") or 0),
                "survey": float(meta.get("SurveyFee") or 0),
                "hypothec": float(meta.get("HypothecFee") or 0),
            },
            schedule=[
                PaymentRow(
                    order=int(row.get("OrderNumber") or 0),
                    amount=float(row.get("Amount") or 0),
                    principal=float(row.get("PrincipalAmount") or 0),
                    profit=float(row.get("ProfitAmount") or 0),
                    taxes=float(row.get("KKDF") or 0) + float(row.get("BSMV") or 0),
                    remaining=float(row.get("RemainingPrincipalAmount") or 0),
                    due_date=str(row.get("MaturityDate") or "")[:10],
                )
                for row in payload.get("Installments") or []
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
        code = (_params(chosen.raw)["ProductCode"] or [""])[0]
        if code in NO_PUBLISHED_RATE:
            raise UnsupportedProduct(
                f"{self.display_name} publishes no profit-share rate for "
                f"{chosen.name}: {NO_PUBLISHED_RATE[code]}."
            )

        currency = currency.upper()
        if currency not in chosen.currencies:
            raise UnsupportedProduct(
                f"{chosen.name} is not offered in {currency}. "
                f"Available: {', '.join(chosen.currencies)}."
            )
        group = (_params(chosen.raw)["ProductGroup"] or ["2"])[0]

        refusal = ""
        for days in _day_attempts(term, term_unit):
            body = {
                "i": False,
                "p1": str(int(amount)),
                "p2": group,
                "p3": str(int(days)),
                "p4": FEC_BY_CURRENCY[currency],
                "p5": code,
                "p9": chosen.name,
                # Inert, but sent as the page sends it. See _day_attempts.
                "p10": True,
            }
            try:
                payload = request_json(
                    "POST",
                    BASE + PROFIT_SHARE,
                    headers={**HEADERS, "referer": PAGE + "kar-payi-hesaplama"},
                    json=body,
                )
            except ValueError as exc:
                # A term below a product's floor is a 400 carrying the bank's
                # own sentence, not a zero response: Dijital Katılma refuses
                # anything under 31 days. Keep it and try the next reading.
                refusal = str(exc).split(": ", 1)[-1]
                continue

            net = float(payload.get("NetProfitShare") or 0)
            ratio = float(payload.get("ProfitShareRatio") or 0)
            # An unsupported combination answers 200 with every field zero
            # rather than an error, so a zero has to count as a failure.
            if net > 0 and ratio > 0:
                return ProfitShareQuote(
                    bank=self.name,
                    product=chosen,
                    amount=float(amount),
                    # The days actually asked for, not the caller's unit: this
                    # endpoint only ever counts days.
                    term=int(days),
                    currency=currency,
                    term_unit="day",
                    ratio=ratio,
                    gross_profit=float(payload.get("GrossProfitShare") or 0),
                    net_profit=net,
                    gross_annual_rate=_optional_float(payload.get("GrossProfitShareYearly")),
                    net_annual_rate=_optional_float(payload.get("NetProfitShareYearly")),
                    raw=payload,
                )

        raise UnsupportedProduct(
            f"{self.display_name} published no profit-share rate for "
            f"{chosen.name} at {amount:,.0f} {currency} over {term} "
            f"{term_unit or 'day'}(s)."
            + (f" The bank said: {refusal}" if refusal else
               " The bank answers with zeros for combinations it does not offer.")
        )

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        rows = request_json(
            "GET",
            BASE + RATES,
            headers={**HEADERS, "referer": PAGE + "doviz-cevirici"},
        )
        return [
            Rate(
                code=row["CurrencyCode"],
                name=row.get("CurrencyDescription") or row.get("Title") or "",
                buy=float(row.get("BuyRate") or 0),
                sell=float(row.get("SellRate") or 0),
                unit=_rate_unit(row["CurrencyCode"]),
            )
            for row in rows
        ]

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert using the published rate.

        Kuveyt Türk has no converter endpoint — its own gold page multiplies the
        quoted rate in the browser — so the multiplication happens here, in
        Decimal, and the result is flagged as derived. This is the single agreed
        exception to never computing a number ourselves.
        """
        by_code = {r.code: r for r in self.rates()}
        src = _resolve_rate_code(source, by_code)
        dst = _resolve_rate_code(target, by_code)

        value = Decimal(str(amount))
        # Selling the source to the bank uses its buy rate; buying the target
        # from the bank uses its sell rate. Both are 1.0 for TL.
        rate = Decimal(str(by_code[src].buy)) / Decimal(str(by_code[dst].sell))
        return Conversion(
            bank=self.name,
            source=src,
            target=dst,
            amount=value,
            result=value * rate,
            rate=rate,
            derived=True,
        )

    # ----- cards -----

    def card_installment_quote(
        self, card: str, amount: float, installments: int
    ) -> CardInstallmentQuote:
        chosen = self.find_product("card", card)
        product_type = (_params(chosen.raw)["ProductType"] or ["0"])[0]
        body = {
            "p1": int(amount),
            "p2": int(installments),
            "p3": int(product_type),
            "p4": 0,
            "p5": chosen.code,
            "p6": chosen.name,
        }
        try:
            payload = request_json(
                "POST",
                BASE + CARD,
                headers={**HEADERS, "referer": PAGE + "kart-taksit-hesaplama"},
                json=body,
            )
        except ValueError as exc:
            # A 404 with an empty body means the instalment count is too high.
            # The catalogue over-promises: Sağlam Kart Troy declares 12 and the
            # endpoint refuses anything above 9.
            raise UnsupportedProduct(
                f"{self.display_name} refused {installments} instalments on "
                f"{chosen.name}. Its catalogue declares up to {chosen.max_term}, "
                f"but the calculator accepts fewer. Try a smaller count."
            ) from exc

        first = payload.get("FirstInstallementAmount") or 0
        if not first:
            raise UnsupportedProduct(
                f"{self.display_name} returned no instalment plan for "
                f"{chosen.name} at {amount:,.0f} TL over {installments} instalments."
            )
        return CardInstallmentQuote(
            bank=self.name,
            card=chosen,
            amount=float(amount),
            installments=int(installments),
            installment=float(first),
            total=float(payload.get("TotalDelayInterestAmount") or 0),
            profit_rate=float(payload.get("ProfitRate") or 0),
            raw=payload,
        )


def _slug(title: str) -> str:
    return fold(title).upper()


def _optional_float(value) -> float | None:
    return None if value is None else float(value)


def _rate_unit(code: str) -> str:
    if "(gr)" in code:
        return "gram"
    if code.startswith("Z"):
        return "coin"
    return "1"


def _resolve_rate_code(code: str, by_code: dict[str, Rate]) -> str:
    resolved = RATE_ALIASES.get(code.upper(), code)
    if resolved in by_code:
        return resolved
    raise UnsupportedProduct(
        f"Kuveyt Türk does not quote {code!r}. "
        f"Quoted: {', '.join(sorted(by_code))}."
    )


def _day_attempts(term: int, term_unit: str | None) -> list[int]:
    """The day counts to ask for, in order.

    This endpoint counts days and nothing else. The p10 flag is documented as
    choosing days or months, but it is inert: p3=12 returns the same 12-day
    profit either way, and 100 000 TL at 12 "months" would be a hundred times
    larger than what comes back. Months are therefore sent as 30-day multiples,
    which is what the contract already observed for Ara Dönem (30, 90 and 180
    answer; 365 is a hole in the bank's own table and returns zeros).

    With no unit given the number is taken as days, then retried as months.
    That second attempt is what answers for products with a day floor —
    Dijital Katılma refuses anything under 31 days and Altına Altın under 92.
    """
    if term_unit:
        unit = term_unit.lower().rstrip("s")
        if unit == "day":
            return [term]
        if unit == "month":
            return [term * DAYS_PER_MONTH]
        raise ValueError(f"term_unit must be 'day' or 'month', got {term_unit!r}")
    return [term, term * DAYS_PER_MONTH]
