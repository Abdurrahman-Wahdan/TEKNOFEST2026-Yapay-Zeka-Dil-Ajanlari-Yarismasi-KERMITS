"""T.O.M. Katılım.

Re-checked 2026-08-13. The earlier note (below, kept in history) concluded no
usable public calculator, having looked at the static campaign pages
`/taksitle.html` and `/altin-biriktiren-hesap.html`. It missed
`/hesaplama-araclari.html`: that page drives a real loan calculator, and the
"partner credential" the loan API demands is not partner-only — it is a fixed
HTTP Basic pair embedded in that page's own JavaScript, served to anyone. So the
API is public in every sense that matters, and financing is implemented here.

    Authorization: Basic <TBOnlineIntegrationUser:...>   # from the site's JS

Two calls make a quote, both on `webintegration.tombank.com.tr`:
  * LoanCalculation/LoanRateList {ProductCode}      -> monthly rate per term
  * LoanCalculation/GetLoanPayBackPlan {...}         -> total + instalment plan

Only financing is implemented. The profit-share endpoint
(ProfitCalculation/GetProfitShareCalculateValues) returns a rate *catalogue*,
not a per-amount quote, and there is no observed calculator that turns it into a
figure — computing the profit here would be inventing a price, which this layer
never does. So profit_share is left unpublished.

Contract in docs/discovery/captured/tom.md.
"""

import logging

from ..models import FinanceQuote, PaymentRow, Product
from ..parse import money, rate
from .base import BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

API = "https://webintegration.tombank.com.tr/webintegration/api"

# Fixed HTTP Basic pair lifted from the calculator page's JavaScript. It is not
# a secret: the public site sends it with every LoanCalculation call, and the
# API answers 401 without it. Decodes to TBOnlineIntegrationUser:<password>.
_AUTH = "Basic VEJPbmxpbmVJbnRlZ3JhdGlvblVzZXI6a09kV09iSGFtRVprUGU3VWlnc2V0UT09"

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "referer": "https://tombank.com.tr/",
    "authorization": _AUTH,
}

# The one financing product the public calculator drives. The site sends this
# code to LoanRateList and GetLoanPayBackPlan; the rate table it returns runs
# 1..36 instalments, all at one monthly rate.
_FINANCE_CODE = "TKTCDGRFNS"
_FINANCE_NAME = "İhtiyaç Finansmanı"
_MAX_TERM = 36


class Tom(BaseBank):
    name = "tom"
    display_name = "T.O.M. Katılım Bankası"
    capabilities = frozenset({"products", "finance"})
    notes = (
        "Its consumer financing calculator is public; the loan API's HTTP Basic "
        "credential is embedded in the calculator page's own JavaScript. It "
        "publishes no participation, card or FX calculator."
    )

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category != "finance":
            raise UnsupportedProduct(
                f"{self.display_name} publishes no {category} catalogue. "
                + self._what_it_does()
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        # LoanRateList doubles as the catalogue check: it confirms the code is
        # live and states the monthly rate the plan call will echo back.
        table = self._rate_table()
        built = [Product(
            code=_FINANCE_CODE,
            name=_FINANCE_NAME,
            category="finance",
            min_term=1,
            max_term=_MAX_TERM,
            currencies=("TRY",),
            rate=table.get(1),
            raw={"ProductCode": _FINANCE_CODE},
        )]
        self._store(category, built)
        return built

    def _rate_table(self) -> dict[int, float]:
        """Monthly rate per instalment count, from LoanRateList."""
        payload = self._json(
            "POST", f"{API}/LoanCalculation/LoanRateList",
            headers=HEADERS, json={"ProductCode": _FINANCE_CODE},
        ) or {}
        rows = ((payload.get("Data") or {}).get("LoanRateList")) or []
        table = {int(r["InstallmentsCount"]): float(r["LoanRate"]) for r in rows}
        if not table:
            raise UnsupportedProduct(
                f"{self.display_name} returned no financing rates, so no quote "
                f"can be built. Its rate list may have changed shape."
            )
        return table

    # ----- financing -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        chosen = self.find_product("finance", product)
        self._check_limits(chosen, amount=amount, term=term, term_label="months")

        table = self._rate_table()
        monthly = table.get(int(term))
        if monthly is None:
            raise UnsupportedProduct(
                f"{self.display_name} prices {chosen.name} over 1–{_MAX_TERM} "
                f"months; {term} is outside its rate table."
            )

        payload = self._json(
            "POST", f"{API}/LoanCalculation/GetLoanPayBackPlan",
            headers=HEADERS,
            json={
                "CustomRate": monthly,
                "FundingAmount": float(amount),
                "InstallmentCount": int(term),
                "IsTotalAmountByInstallmentAmount": False,
                "ProductCode": chosen.raw["ProductCode"],
            },
        ) or {}
        data = payload.get("Data") or {}
        rows = data.get("installmentList") or []
        if not rows:
            raise UnsupportedProduct(
                f"{self.display_name} returned no payment plan for {chosen.name} "
                f"over {term} months."
            )

        schedule = [
            PaymentRow(
                order=int(r.get("OrderNumber", i + 1)),
                amount=money(r.get("Amount")),
                # T.O.M.'s plan gives only the instalment amount, not a
                # principal/profit/tax split, so those are left at zero rather
                # than invented.
                principal=0.0, profit=0.0, taxes=0.0, remaining=0.0,
                due_date=(r.get("MaturityDate") or ""),
            )
            for i, r in enumerate(rows)
        ]
        installment = schedule[0].amount if schedule else 0.0

        return self._check_quote(FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(term),
            installment=installment,
            total=money(data.get("TotalAmount")),
            profit_rate=rate(data.get("MonthlyProfitRate")),
            # T.O.M. publishes no annual cost rate; MonthlyCostRate is a total
            # over the term, not an APR, so annual is left unstated.
            annual_cost_rate=None,
            fees={
                "commission": float(data.get("CommisionAmount") or 0.0),
                "insurance": float(data.get("InsuranceAmount") or 0.0),
            },
            schedule=schedule,
            raw=payload,
        ))
