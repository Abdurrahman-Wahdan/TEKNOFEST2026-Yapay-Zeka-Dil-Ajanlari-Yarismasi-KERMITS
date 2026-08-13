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

from ..models import (
    CardInstallmentQuote,
    Conversion,
    FinanceQuote,
    PaymentRow,
    ProfitShareQuote,
    Product,
    Rate,
)
from ..parse import fold
from .base import RATE_ALIASES, BaseBank, UnsupportedProduct

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
    capabilities = frozenset(
        {"products", "finance", "profit_share", "card", "rates", "convert"}
    )
    # This feed calls gold "ALT (gr)" and the lira "TL".
    rate_aliases = RATE_ALIASES

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category not in CALCULATORS:
            raise UnsupportedProduct(
                f"{self.display_name} has no {category!r} catalogue. "
                f"Available: {', '.join(sorted(CALCULATORS))}."
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        calculator, page = CALCULATORS[category]
        entries = self._json(
            "GET",
            f"{BASE}{CATALOGUE}&p1={calculator}",
            headers={**HEADERS, "referer": PAGE + page},
        )
        built = [self._to_product(e, category) for e in entries]
        self._store(category, built)
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
                # The entry's own MaturityTerm, not MaturityTermMax. Two
                # entries share ELKTRARACSARJUNITE and both declare Max 36,
                # but Elektrikli Araç Şarj Ünitesi really stops at 1 and the
                # endpoint 400s above it — MaturityTerm is what it stops at.
                max_term=_first(p["MaturityTerm"]) or _first(p["MaturityTermMax"]),
                raw=entry,
            )

        if category == "profit_share":
            currencies = tuple(
                CURRENCY_BY_FEC[f] for f in p["FEC"] if f in CURRENCY_BY_FEC
            )
            # Every currency has its own floor and ceiling, so a single pair
            # would show the lira figures against a USD or gold request. They
            # go in raw per currency and profit_share_quote checks the one that
            # applies; the flat fields stay None unless there is only one.
            limits = {
                CURRENCY_BY_FEC[f]: (
                    _first(p[f"MaturityDayMinAmount{FEC_SUFFIX.get(f, 'Tl')}"], float),
                    _first(p[f"MaturityDayMaksAmount{FEC_SUFFIX.get(f, 'Tl')}"], float),
                )
                for f in p["FEC"]
                if f in CURRENCY_BY_FEC
            }
            only = limits[currencies[0]] if len(currencies) == 1 else (None, None)
            return Product(
                # Three of the seven accounts carry no ProductCode of their own
                # and are selected by group alone. The request still sends the
                # empty code from raw; this one is for the user to name.
                code=code or _slug(title),
                name=title,
                category=category,
                min_amount=only[0],
                max_amount=only[1],
                min_term=_first(p["MaturityTermMinDay"]),
                max_term=_first(p["MaturityTermMaksDay"]),
                currencies=currencies or ("TRY",),
                raw={**entry, "_limits": limits},
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
        self._check_limits(chosen, amount=amount, term=term)
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
        payload = self._json(
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

        return self._check_quote(FinanceQuote(
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
        low, high = chosen.raw.get("_limits", {}).get(currency, (None, None))
        self._check_limits(
            chosen.__class__(**{**chosen.__dict__, "min_amount": low, "max_amount": high}),
            amount=amount,
        )
        for days in _day_attempts(term, self._require_unit(term, term_unit)):
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
                payload = self._json(
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
                return self._check_profit_share(ProfitShareQuote(
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
                ))

        raise UnsupportedProduct(
            f"{self.display_name} published no profit-share rate for "
            f"{chosen.name} at {amount:,.0f} {currency} over {term} "
            f"{term_unit or 'day'}(s)."
            + (f" The bank said: {refusal}" if refusal else
               " The bank answers with zeros for combinations it does not offer.")
        )

    # ----- rates and conversion -----

    def rates(self) -> list[Rate]:
        rows = self._json(
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
        source, target = source.upper(), target.upper()
        if RATE_ALIASES.get(source, source) == RATE_ALIASES.get(target, target):
            # Otherwise the buy/sell spread is applied to the currency against
            # itself and 10 USD becomes 9,09 USD. Answered without a request:
            # there is no rate to look up.
            value = Decimal(str(amount))
            return Conversion(
                bank=self.name, source=source, target=target,
                amount=value, result=value, rate=Decimal(1), derived=True,
            )
        by_code = {r.code: r for r in self.rates()}
        src = _resolve_rate_code(source, by_code)
        dst = _resolve_rate_code(target, by_code)

        if not by_code[dst].sell:
            raise UnsupportedProduct(
                f"{self.display_name} quotes no sell rate for {target}, so the "
                f"conversion cannot be worked out from its published rates."
            )
        value = Decimal(str(amount))
        # Selling the source to the bank uses its buy rate; buying the target
        # from the bank uses its sell rate. Both are 1.0 for TL.
        rate = Decimal(str(by_code[src].buy)) / Decimal(str(by_code[dst].sell))
        return Conversion(
            bank=self.name,
            # The codes the caller asked about, not this bank's names for them.
            source=source.upper(),
            target=target.upper(),
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
        self._check_limits(chosen, amount=amount, term=installments,
                           term_label="instalments")
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
            payload = self._json(
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
    """The feed's own name for a currency, however the caller spelled it."""
    wanted = code.upper()
    resolved = RATE_ALIASES.get(wanted, wanted)
    if resolved in by_code:
        return resolved
    # The feed mixes cases of its own ("ALT (gr)", "ZCeyrek"), so match on the
    # uppercased form rather than refusing "usd" with a list containing USD.
    for known in by_code:
        if known.upper() == resolved:
            return known
    raise UnsupportedProduct(
        f"Kuveyt Türk does not quote {code!r}. "
        f"Quoted: {', '.join(sorted(by_code))}."
    )


def _day_attempts(term: int, term_unit: str) -> list[int]:
    """The day counts to ask for, in order.

    This endpoint counts days and nothing else. The p10 flag is documented as
    choosing days or months, but it is inert: p3=12 returns the same 12-day
    profit either way, and 100 000 TL at 12 "months" would be a hundred times
    larger than what comes back. Months are therefore sent as day counts.

    A month is 30 days for one product and 31 for another, and the bank says so
    only by answering. Ara Dönem takes exact 30-day multiples and returns zeros
    at 31, 91 and 181; plain Katılma returns zeros at exactly 30 and wants 31,
    while accepting 60, 90 and 180. So both are offered and the bank picks. This
    finds a day count, not a price.

    The unit is always stated: BaseBank._require_unit refuses a bare number
    rather than reading it as days at this bank and as months at the next.
    """
    if term_unit == "day":
        return [term]
    return [term * DAYS_PER_MONTH, term * DAYS_PER_MONTH + 1]
