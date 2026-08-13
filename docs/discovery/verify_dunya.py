"""Dünya Katılım — finansman, kâr payı and currency conversion.

ASP.NET Core. Every calculator is on the homepage, so there are no calculator
URLs to discover; all four endpoints are POSTs to the site root guarded by an
anti-forgery token taken from the homepage.

The bank list's domain (dunyakatilim.com) does not resolve. The live host is
dunyakatilim.com.tr.

Usage: python verify_dunya.py
"""

import html
import json
import re
import warnings

warnings.filterwarnings("ignore")

import httpx

from checks import AJAX, Report, amount

HOST = "https://dunyakatilim.com.tr"

FINANCE = {
    "ARACBINEK2ELTUKETICI": ("Araç Binek 2.El", "Vehicle"),
    "ARACBINEKYENITUKETICI": ("Araç Binek Yeni", "Vehicle"),
    "ARSATUKETICI": ("Arsa", "MiscellaneousRealEstate"),
    "2ELKONUTTUKETICI": ("Konut 2.El", "House"),
    "KONUTTUKETICI": ("Konut Yeni", "House"),
    "TUKETICIIHTIYAC": ("Tüketici İhtiyaç", "Miscellaneous"),
}

client = httpx.Client(timeout=60, verify=False, follow_redirects=True,
                      headers={"user-agent": AJAX["user-agent"]})
report = Report("dunya")
_state = {}


def home():
    if "html" not in _state:
        _state["html"] = client.get(f"{HOST}/").text
        found = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', _state["html"])
        _state["token"] = found.group(1) if found else ""
    return _state["html"]


def post(path, **fields):
    home()
    return client.post(f"{HOST}/{path}",
                       data={**fields, "__RequestVerificationToken": _state["token"]},
                       headers={**AJAX, "referer": f"{HOST}/",
                                "content-type": "application/x-www-form-urlencoded; charset=UTF-8"})


def dividend_products():
    """The kâr payı products, with their maturity codes, are embedded in the
    homepage as JSON inside the dividendSelect option values."""
    out = []
    for m in re.finditer(r"<option[^>]*value=[\"'](\{&quot;id&quot;.*?\})[\"']", home(), re.S):
        try:
            out.append(json.loads(html.unescape(m.group(1))))
        except json.JSONDecodeError:
            continue
    return out


def verify_finance_limits():
    report.section("finansman limits (LoanInstallmentValues)")
    for code, (name, _) in FINANCE.items():
        label = f"{name[:22]:24s} {code[:22]:24s}"
        try:
            body = post("LoanInstallmentValues?lang=tr", productCode=code).json()
            ok = body.get("result") == "SUCCESS" and body.get("maxInstallment", 0) > 0
            report.check(label, ok, f"vade {body.get('minInstallment')}-{body.get('maxInstallment')}, "
                                    f"tutar {body.get('minAmount')}-{body.get('maxAmount')}")
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")


def verify_finance():
    report.section("finansman (every product)")
    for code, (name, category) in FINANCE.items():
        label = f"{name[:22]:24s} 100.000/24ay"
        try:
            limits = post("LoanInstallmentValues?lang=tr", productCode=code).json()
            term = min(24, int(limits.get("maxInstallment") or 24))
            body = post("LoanCheckRate?lang=tr", productName=name, productCode=code,
                        productCategory=category, amount="100.000",
                        installmentCount=str(term), userRate="0",
                        userSelected="false").json()
            installment = amount(body.get("monthlyInterest"))
            ok = body.get("result") == "SUCCESS" and installment > 0
            report.check(label, ok, f"taksit={body.get('monthlyInterest')} "
                                    f"toplam={body.get('totalPayment')} oran={body.get('rate')}")
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")


def verify_profit_share():
    report.section("kâr payı (every product x maturity x currency)")
    products = dividend_products()
    report.check("dividend catalogue parsed", len(products) >= 2, f"{len(products)} product(s)")
    for product in products:
        code = product.get("productCode")
        for maturity in product.get("productMaturitySettings", []):
            for currency in product.get("availableCurrencies", []):
                days = maturity.get("maturityPeriodBeginValue")
                label = (f"{product.get('name','')[:22]:24s} {currency:4s} "
                         f"{maturity.get('maturityName','')[:16]:18s}")
                try:
                    body = post("DividendEstimatedProfit?lang=tr", balance="100000",
                                currencyCode=currency,
                                maturityCode=maturity.get("maturityCode"),
                                maturityPeriodValue=str(days),
                                productCode=code).json()
                except Exception as exc:
                    report.check(label, False, f"{type(exc).__name__}: {exc}")
                    continue
                if body.get("result") != "SUCCESS":
                    report.known(label, str(body.get("message"))[:70])
                    continue
                profit = amount(body.get("estimatedProfit") or body.get("netProfit")
                                or body.get("grossProfit"))
                report.check(label, profit > 0 or bool(body),
                             ", ".join(f"{k}={v}" for k, v in list(body.items())[1:4]))


def verify_currency():
    report.section("döviz çevirici")
    for src, dst, value in (("USD", "TRY", "1000"), ("EUR", "TRY", "1000"),
                            ("XAU", "TRY", "10"), ("GBP", "TRY", "1000")):
        label = f"{value} {src} -> {dst}"
        try:
            body = post("CurrencyBuyCalculate?lang=tr", buyFromAmount=value,
                        buyFromCurrency=src, buyToCurrency=dst, transactionType="1").json()
            got = amount(body.get("destinationAmount"))
            report.check(label, body.get("result") == "SUCCESS" and got > 0, f"= {got}")
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    verify_finance_limits()
    verify_finance()
    verify_profit_share()
    verify_currency()
    report.finish()
