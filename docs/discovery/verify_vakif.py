"""Vakıf Katılım — finansman, kâr payı, kart taksit and FX.

Unigate, like Albaraka, but guarded by an ASP.NET anti-forgery token instead of
a WAF: every plugin is a POST whose body is only
__RequestVerificationToken=<token from the matching page>. The parameters
themselves go in the query string.

The token is per-page, so fetch the page you are about to call.

Usage: python verify_vakif.py
"""

import re
import warnings

warnings.filterwarnings("ignore")

import httpx

from checks import AJAX, Report, amount

HOST = "https://www.vakifkatilim.com.tr"
TOOLS = f"{HOST}/tr/yardimci-sayfalar/hesaplama-araclari"
LANG_ID = "bf2689d9-071e-4a20-9450-b1dbdd39778f"

LOAN_TYPES = {
    "IF": "İhtiyaç Finansmanı", "K": "Sıfır Konut", "K2": "2. El Konut",
    "BO": "Taşıt 0 km", "BO2": "Taşıt 2. El", "I": "İşyeri", "A": "Arsa",
}
CURRENCIES = {"0": "TL", "1": "USD", "19": "EUR", "24": "ALTIN"}
EXPIRIES = {"31": "aylık", "91": "3 aylık", "180": "6 aylık",
            "364": "yıllık", "366": "1 yıl üzeri"}

client = httpx.Client(timeout=40, verify=False, follow_redirects=True,
                      headers={"user-agent": AJAX["user-agent"]})
report = Report("vakif")
_tokens = {}


def token(page):
    """Anti-forgery token for one calculator page, fetched once."""
    if page not in _tokens:
        html = client.get(page).text
        found = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
        _tokens[page] = found.group(1) if found else ""
    return _tokens[page]


def plugin(name, page, **params):
    return client.post(f"{HOST}/plugins/{name}",
                       params={"langId": LANG_ID, "language": "tr", **params},
                       data={"__RequestVerificationToken": token(page)},
                       headers={**AJAX, "referer": page})


def verify_finance():
    page = f"{TOOLS}/finansman-hesaplama"
    report.section("finansman (every product)")
    for code, name in LOAN_TYPES.items():
        label = f"{name[:22]:24s} {code:4s} 100000/24ay"
        try:
            body = plugin("FinancingComputationExecute", page, financingType=code,
                          amount="100000", numberOfInstallments="24",
                          profitRate="null", calculateType="1").json()
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")
            continue
        installment = amount(body.get("installmentAmount"))
        # errorMessage is populated instead of an HTTP error status.
        if body.get("errorMessage"):
            report.check(label, False, f"errorMessage={body['errorMessage']}")
            continue
        report.check(label, installment > 0,
                     f"taksit={body.get('installmentAmount')} "
                     f"toplam={body.get('totalAmount')} oran={body.get('profitRate')}")


def verify_installment_options():
    page = f"{TOOLS}/finansman-hesaplama"
    report.section("allowed instalment counts")
    for code, name in list(LOAN_TYPES.items())[:3]:
        try:
            rows = plugin("FinancingInstallment", page, financingType=code).json()
            options = rows.get("installments") or []
            report.check(f"{name[:22]:24s} {code}", len(options) > 0,
                         f"{len(options)} option(s), max {options[-1]['code'] if options else '-'}")
        except Exception as exc:
            report.check(f"{name[:22]:24s} {code}", False, f"{type(exc).__name__}: {exc}")


def verify_profit_share():
    page = f"{TOOLS}/kar-payi-hesaplama"
    report.section("kâr payı (every currency x term)")
    for fec, cur in CURRENCIES.items():
        for expiry, label in EXPIRIES.items():
            name = f"{cur:6s} {label:12s}"
            response = plugin("GrossAmountCalculationJson", page, accountType="KAH",
                              currencyUnit=fec, principal="100000", expiry=expiry)
            # Third way this project has seen a bank say "not offered": not an
            # error, not zeros, but 200 with a completely empty body. Gold past
            # one year does this.
            if not response.text.strip():
                report.known(name, "empty body — term not offered for this currency")
                continue
            try:
                body = response.json()
            except Exception as exc:
                report.check(name, False, f"{type(exc).__name__}: {response.text[:80]!r}")
                continue
            net = amount(body.get("netProfit"))
            detail = (f"brüt={body.get('grossProfit')} net={body.get('netProfit')} "
                      f"oran={body.get('grossRate')} hesap={body.get('accountName')}")
            if net <= 0 or body.get("errorMessage"):
                report.known(name, body.get("errorMessage") or "bank publishes no rate here")
            else:
                report.check(name, True, detail)


def verify_card():
    page = f"{TOOLS}/kart-taksit-hesaplama"
    report.section("kart taksit (Ferah Kart)")
    try:
        options = plugin("CardCalculationInstallment", page, cardType="FK").json()
        count = len(options.get("installments") or [])
        report.check("instalment options", count > 0, f"{count} option(s)")
    except Exception as exc:
        report.check("instalment options", False, f"{type(exc).__name__}: {exc}")
    for n in (3, 6, 9):
        label = f"10000 TL / {n} taksit"
        try:
            body = plugin("CardComputationExecute", page, cardType="FK", amount="10000",
                          numberOfInstallments=str(n), profitRate="null").json()
            installment = amount(body.get("installmentAmount"))
            report.check(label, installment > 0,
                         f"taksit={body.get('installmentAmount')} toplam={body.get('totalAmount')}")
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")


def verify_currency():
    page = f"{TOOLS}/doviz-cevirici-ve-kurlar"
    report.section("döviz")
    try:
        rows = client.get(f"{HOST}/plugins/DetailCurrencyListData",
                          params={"langId": LANG_ID, "language": "tr", "currencyTypeId": "2"},
                          headers={**AJAX, "referer": page}).json()
        report.check("rate list", bool(rows), f"{len(rows) if hasattr(rows, '__len__') else '?'} entries")
    except Exception as exc:
        report.check("rate list", False, f"{type(exc).__name__}: {exc}")
    try:
        body = client.get(f"{HOST}/plugins/CurrencyConverter",
                          params={"langId": LANG_ID, "language": "tr", "amount": "1000",
                                  "InputCurrencyType": "1", "convertCurrencyType": "0"},
                          headers={**AJAX, "referer": page}).json()
        report.check("1000 USD -> TL", bool(body), str(body)[:120])
    except Exception as exc:
        report.check("1000 USD -> TL", False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    verify_finance()
    verify_installment_options()
    verify_profit_share()
    verify_card()
    verify_currency()
    report.finish()
