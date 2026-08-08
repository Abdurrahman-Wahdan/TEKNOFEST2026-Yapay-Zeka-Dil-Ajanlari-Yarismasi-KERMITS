"""Türkiye Emlak Katılım — call every calculator endpoint for every product.

Emlak runs the same F5 WAF as Albaraka: plain httpx gets a 200 with an HTML
"Request Rejected" body, so the transport is curl_cffi impersonating Chrome.

All three calculators sit on one page, /tr/hesaplama-araclari, and take plain
query-string GETs with no token and no session.

Usage: python verify_emlak.py
"""

import warnings

warnings.filterwarnings("ignore")

from curl_cffi import requests as cr

from checks import Report, amount

# Deliberately no user-agent here. curl_cffi sets a UA matching the TLS
# fingerprint it impersonates; overriding it makes the two disagree and the WAF
# rejects the call with an HTML page, which then surfaces as a JSON parse error.
AJAX = {"x-requested-with": "XMLHttpRequest",
        "accept": "application/json, text/javascript, */*; q=0.01"}

HOST = "https://www.emlakkatilim.com.tr"
PAGE = f"{HOST}/tr/hesaplama-araclari"

# From the page's own selects.
FEC = {"0": "TL", "1": "USD", "19": "EUR", "24": "ALT (gr)", "26": "GMS (gr)"}
TERMS = {"31": "1 ay", "91": "3 ay", "180": "6 ay", "364": "12 ay", "366": "12+ ay"}
PRODUCTS = {
    "ARACBINEK2EL": "2. El Taşıt Finansmanı",
    "ARACBINEKYENI": "0 Km Taşıt Finansmanı",
    "EVOFISGERECLERI": "İhtiyaç Finansmanı",
    "GMENKULKONUTYENI": "Yeni Konut Finansmanı",
}

session = cr.Session(impersonate="chrome124", timeout=40)
report = Report("emlak")


def get(path, **params):
    return session.get(f"{HOST}/Plugins/{path}", params=params,
                       headers={**AJAX, "referer": PAGE})


def verify_loan_limits():
    report.section("loan term limits (SelectLoansProperty)")
    for code, name in PRODUCTS.items():
        try:
            body = get("SelectLoansProperty", ProductTypeId=code).json()
            data = body.get("Data") or {}
            ok = body.get("Success") is True and data.get("MaturityMax", 0) > 0
            report.check(f"{name[:28]:30s} {code:18s}", ok,
                         f"vade {data.get('MaturityMin')}-{data.get('MaturityMax')}")
        except Exception as exc:
            report.check(f"{name[:28]:30s} {code:18s}", False, f"{type(exc).__name__}: {exc}")


def verify_loans():
    report.section("finansman (every product)")
    for code, name in PRODUCTS.items():
        try:
            limits = (get("SelectLoansProperty", ProductTypeId=code).json().get("Data") or {})
            term = min(24, int(limits.get("MaturityMax") or 24)) or 24
            body = get("CalculateLoansProduct", CalculationTypeId=1, ProductTypeId=code,
                       LoanAmount=100000, LoanMaturity=term, LoanSegmentId=1).json()
            data = body.get("Data") or {}
            total = amount(data.get("TotalInstallmentAmount"))
            ok = (body.get("Success") is True and total > 0
                  and data.get("InstallmentCount") == term
                  and amount(data.get("ProfitRate")) > 0)
            report.check(f"{name[:28]:30s} 100000/{term}ay", ok,
                         f"total={total} rate={data.get('ProfitRate')} "
                         f"komisyon={data.get('CommissionAmount')}")
        except Exception as exc:
            report.check(f"{name[:28]:30s}", False, f"{type(exc).__name__}: {exc}")


def verify_profit_share():
    report.section("kâr payı (every currency x term)")
    for fec, cur in FEC.items():
        for term, label in TERMS.items():
            name = f"{cur:9s} {label:7s}"
            try:
                body = get("CalculateProfitShareRate", LanguageId=1, Money=100000, Fec=fec,
                           profitShareInstallment=0, MaturityTerm=term,
                           profitShareInstallmentValueDay=term).json()
                data = body.get("Data") or {}
                net = amount(data.get("NetProfitShare"))
                ok = body.get("Success") is True and net > 0
                detail = (f"gross={data.get('GrossProfitShare')} net={data.get('NetProfitShare')} "
                          f"yıllık={data.get('NetProfitShareYearly')} seg={data.get('SegmentName')}")
                if not ok:
                    report.known(name, "bank publishes no rate for this pair")
                else:
                    report.check(name, ok, detail)
            except Exception as exc:
                report.check(name, False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    verify_loan_limits()
    verify_loans()
    verify_profit_share()
    report.finish()
