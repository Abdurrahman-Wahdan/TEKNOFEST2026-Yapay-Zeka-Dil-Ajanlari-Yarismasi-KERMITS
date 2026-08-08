"""Hayat Finans — profit share and FX rates.

A Next.js site: the calculators are on the homepage and call clean JSON APIs
under /api/integration with no token, no session and no WAF. The friendliest
contract of the ten banks.

Note the host: hayatfinans.com in the bank list does not resolve for www and
redirects poorly; the live host is hayatfinans.com.tr.

Usage: python verify_hayat.py
"""

import warnings

warnings.filterwarnings("ignore")

import httpx

from checks import UA, Report, amount

HOST = "https://hayatfinans.com.tr"
HEADERS = {"user-agent": UA, "content-type": "application/json",
           "accept": "application/json", "referer": f"{HOST}/"}

# The homepage calculator has no currency select and no term select at all, and
# a sweep of the API agrees: only FEC 0 (TL) at 32-33 days returns a rate.
# USD, EUR and every other term answer 200 with an empty data object. So the
# offered surface really is one currency and one term, with three account types
# priced differently.
CURRENCY = 0
TERM_DAYS = 32
ACCOUNT_TYPES = {1: "Katılma Hesabı", 2: "Vadesiz benzeri", 3: "Üçüncü tip"}

client = httpx.Client(timeout=40, verify=False, headers=HEADERS)
report = Report("hayat")


def quote(account_type, fec=CURRENCY, term=TERM_DAYS, money=100000):
    return client.post(f"{HOST}/api/integration/calculateprofitsharerate", json={
        "AccountType": account_type, "Maturity": 1, "ProductGroup": 2,
        "Money": money, "FEC": fec, "MaturityTerm": term}).json()


def verify_profit_share():
    report.section("kâr payı (every account type, TL 32 gün)")
    for account_type, name in ACCOUNT_TYPES.items():
        label = f"{name[:20]:22s} AccountType={account_type} TL {TERM_DAYS} gün"
        try:
            body = quote(account_type)
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")
            continue
        data = body.get("data") or {}
        net = amount(data.get("netProfitShare"))
        report.check(label, body.get("isSuccessful") is True and net > 0,
                     f"gross={data.get('grossProfitShare')} net={data.get('netProfitShare')} "
                     f"yıllık={data.get('netProfitShareYearly')}")

    # 50 000 TL is a hard floor: below it the endpoint returns zeros rather than
    # an error, so a caller that does not check the minimum will quote "0 TL
    # profit" to a user instead of saying the account needs 50 000 to open.
    report.section("minimum balance boundary (50.000 TL)")
    below = amount((quote(1, money=49999).get("data") or {}).get("netProfitShare"))
    at = amount((quote(1, money=50000).get("data") or {}).get("netProfitShare"))
    above = amount((quote(1, money=1000000).get("data") or {}).get("netProfitShare"))
    report.check("49.999 TL returns nothing", below == 0, f"net={below}")
    report.check("50.000 TL prices", at > 0, f"net={at}")
    report.check("profit scales with balance", above > at, f"{at} -> {above}")

    report.known("USD / EUR and terms other than 32-33 gün",
                 "not offered: the page has no currency or term selector and the "
                 "API returns empty data for every other combination")


def verify_rates():
    report.section("fx rates")
    try:
        body = client.get(f"{HOST}/api/integration/fxrate").json()
        rows = body.get("data") or []
        report.check("rate feed", len(rows) >= 3, f"{len(rows)} currencies")
        by_code = {r.get("currencyShortCode"): r for r in rows}
        for code in ("USD", "EUR", "GBP"):
            row = by_code.get(code)
            ok = bool(row) and row["currencyAsk"] > 0 and row["currencyAsk"] >= row["currencyBid"]
            report.check(f"rate {code}", ok,
                         f"bid={row['currencyBid']} ask={row['currencyAsk']}" if row else "missing")
    except Exception as exc:
        report.check("rate feed", False, f"{type(exc).__name__}: {exc}")


def verify_loans():
    report.section("finansman")
    # The endpoint exists and answers, but every payload shape tried returns
    # "API Failure". Recorded rather than hidden: the loan calculator is not
    # reachable from the public site, so its contract was never observed.
    body = client.post(f"{HOST}/api/integration/calculateloansproduct",
                       json={"ProductType": 1, "LoanAmount": 100000, "Maturity": 24})
    report.known("calculateloansproduct",
                 f"endpoint exists but rejects every payload shape tried "
                 f"(HTTP {body.status_code}); no calculator on the public site to observe")


if __name__ == "__main__":
    verify_profit_share()
    verify_rates()
    verify_loans()
    report.finish()
