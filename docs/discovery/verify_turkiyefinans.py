"""Türkiye Finans — rate and product tables.

SharePoint. The services live under /_vti_bin/<Service>.svc/<Method> and answer
plain httpx with no token, no session and no WAF.

Important scope note: **Türkiye Finans publishes tables, not answers.** Its
calculators fetch the rate/fee table once and then do the instalment arithmetic
in the browser -- filling the form and pressing Hesapla fires no further
request. So this bank can tell us the rate, the fees and the term limits for a
product, but it will not tell us the monthly payment.

Under the project rule that we never compute a price ourselves, that means
Türkiye Finans supports rate questions but not "what is my instalment", unless
we are explicitly allowed to run the annuity ourselves. That decision is the
owner's, so nothing here computes anything.

Usage: python verify_turkiyefinans.py
"""

import warnings

warnings.filterwarnings("ignore")

import httpx

from checks import UA, Report, amount

HOST = "https://www.turkiyefinans.com.tr"
SERVICE = f"{HOST}/_vti_bin/TurkiyeFinansServices/FrontEndService.svc"
PAGE = f"{HOST}/tr-tr/hesaplama-araclari/Sayfalar/hesaplama-araclari.aspx"

client = httpx.Client(timeout=40, verify=False, follow_redirects=True,
                      headers={"user-agent": UA, "accept": "application/json",
                               "referer": PAGE})
report = Report("turkiyefinans")


def service(method):
    return client.get(f"{SERVICE}/{method}").json()


def verify_credit_types():
    report.section("finansman product table")
    try:
        rows = (service("GetFinanceCalculatorCreditTypeItems")
                .get("GetFinanceCalculatorCreditTypeItemsResult") or {}).get("Data") or []
    except Exception as exc:
        report.check("credit types", False, f"{type(exc).__name__}: {exc}")
        return
    report.check("credit types", len(rows) > 0, f"{len(rows)} product(s)")
    for row in rows:
        name = str(row.get("Code") or row.get("Name") or "?")
        # Contract check: a usable row needs an identity and a fee basis.
        ok = bool(row.get("CreditID")) and row.get("AllocationFee") is not None
        report.check(f"{name[:34]:36s}", ok,
                     f"id={row.get('CreditID')} tahsis={row.get('AllocationFee')} "
                     f"bsmv={row.get('Bitt')} hesaplanabilir={row.get('Calculate')}")


def verify_profit_share_table():
    report.section("kâr payı rate table")
    for group in (4, 1, 2):
        for customer in ("Bireysel", "Ticari"):
            label = f"AccountGroupType={group} {customer}"
            try:
                rows = (service(f"GetKarPayiHesaplama/{group}/{customer}")
                        .get("GetKarPayiHesaplamaResult") or {}).get("Data") or []
            except Exception as exc:
                report.check(label, False, f"{type(exc).__name__}: {exc}")
                continue
            if not rows:
                report.known(label, "no rate table published for this group")
                continue
            sample = rows[0]
            ok = amount(sample.get("AnnuallyGrossRatio")) > 0
            report.check(label, ok,
                         f"{len(rows)} row(s), ilk: {sample.get('Currency','').strip()} "
                         f"%{sample.get('AnnuallyGrossRatio')} "
                         f"min={sample.get('MinimumAmount')} maxGün={sample.get('MaximumDueDay')}")


def verify_currencies():
    report.section("participation currencies")
    try:
        rows = (service("GetParticipationCurrencyTypeItems")
                .get("GetParticipationCurrencyTypeItemsResult") or {}).get("Data") or []
        report.check("currency list", len(rows) >= 3,
                     ", ".join(r["CurrencyCode"].strip() for r in rows))
    except Exception as exc:
        report.check("currency list", False, f"{type(exc).__name__}: {exc}")


def note_scope():
    report.section("what this bank does not expose")
    report.known("instalment amount",
                 "computed in the browser from the rate table; pressing Hesapla "
                 "fires no request, so there is no figure to read back")


if __name__ == "__main__":
    verify_credit_types()
    verify_profit_share_table()
    verify_currencies()
    note_scope()
    report.finish()
