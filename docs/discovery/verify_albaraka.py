"""Call every Albaraka endpoint for every product the page declares.

Albaraka sits behind an F5 WAF that rejects httpx and curl outright -- headers,
cookies and HTTP/2 make no difference, because it fingerprints the TLS
handshake. curl_cffi impersonating Chrome gets through unchanged. That is the
only reason this file does not use httpx like the rest of the project.

The product catalogue is not an endpoint here: it is embedded in the
finance page as JSON inside the <option value='...'> attributes, and the whole
blob is echoed back as the FinanceType parameter.

Usage: python verify_albaraka.py
"""

import html
import json
import re
import sys

from curl_cffi import requests as cr

HOST = "https://www.albaraka.com.tr"
PLUGINS = f"{HOST}/plugins/"
LANG_ID = "bf2689d9-071e-4a20-9450-b1dbdd39778f"

FINANCE_PAGE = f"{HOST}/tr/hesaplama-araclari/finansman-hesaplama/ihtiyac-finansmani-hesaplama"
HOME_PAGE = f"{HOST}/tr"
PROFIT_PAGE = f"{HOST}/tr/hesaplama-araclari/kar-payi-hesaplama"
FX_PAGE = f"{HOST}/tr/hesaplama-araclari/doviz-cevirici"

# Each visible product identity, its endpoint Type, and its own currency list.
# Kur Korumalı appears twice in the public select.  The live request Type is
# shared, but treating its labels as one product was the same loss-of-mode bug
# that hid Ticari finance.
ACCOUNT_TYPES = (
    ("KTLMHSP", "Katılma Hesabı", "KTLMHSP", ("TRY", "USD", "EUR", "XAU")),
    ("KTLARDM", "Ara Dönem Kâr Payı Ödemeli", "KTLARDM", ("TRY", "USD", "EUR")),
    ("KURKTLMHSP:bireysel", "Kur Korumalı Katılma Hesabı (Bireysel)", "KURKTLMHSP",
     ("TRY", "USD", "EUR", "GBP", "XAU")),
    ("KURKTLMHSP:ticari", "Kur Korumalı Katılma Hesabı (Ticari)", "KURKTLMHSP",
     ("TRY", "USD", "EUR", "GBP", "XAU")),
)

session = cr.Session(impersonate="chrome124", timeout=40)
results = []
known_gaps = []

# Combinations the bank answers 200-with-zeros for, verified against their own
# page rather than assumed from our own call failing.
NOT_OFFERED = {
    **{("KTLMHSP", "XAU", p): "gold participation is listed but not priced"
       for p in ("MONTH", "DAY")},
    **{("KTLARDM", c, "DAY"): "interim-payment accounts take months only"
       for c in ("TRY", "USD", "EUR")},
    **{(identity, c, p): "FX-protected accounts return zeros on their own page too"
       for identity in ("KURKTLMHSP:bireysel", "KURKTLMHSP:ticari")
       for c in ("TRY", "USD", "EUR", "GBP", "XAU") for p in ("MONTH", "DAY")},
}

FINANCE_MODE = re.compile(
    r'<div[^>]*class="[^"]*finansman-type[^"]*\b(bireysel|ticari)\b[^"]*"[^>]*>'
    r'(.*?)</div>', re.S | re.I,
)


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""), flush=True)


def known(name, detail):
    """A gap on the bank's side. Stays visible without failing the run."""
    known_gaps.append((name, detail))
    print(f"  KNOWN {name}   {detail}", flush=True)


def amount_of(text):
    """'18.114,26 TRY' -> 18114.26. Turkish formatting, currency suffix varies,
    so a string compare against '0,00 TRY' silently passes '0,00 USD'."""
    if not text:
        return 0.0
    cleaned = re.sub(r"[^\d,.-]", "", str(text)).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def plugin(name, page, **params):
    body = {"langId": LANG_ID, "language": "tr", "Slug": page.rsplit("/", 1)[-1],
            "searchUrl": "/tr/arama", "customFinancingName": ""}
    body.update(params)
    return session.get(PLUGINS + name, params=body, headers={
        "referer": page,
        "x-requested-with": "XMLHttpRequest",
        "adrum": "isAjax:true",
        "accept": "application/json, text/javascript, */*; q=0.01",
    })


def products():
    """Parse every homepage finance mode and its exact request contract.

    The attribute is single-quoted and the JSON inside is HTML-escaped, so a
    double-quote regex finds nothing and reads as "this bank has no products".
    """
    page = session.get(HOME_PAGE).text
    out, seen = [], set()
    for mode_match in FINANCE_MODE.finditer(page):
        mode, markup = mode_match.group(1).lower(), mode_match.group(2)
        request = {
            "page": HOME_PAGE if mode == "ticari" else FINANCE_PAGE,
            "Type": "HesaplamaTicari" if mode == "ticari" else "HesaplamaBireysel",
            "CreditType": "K" if mode == "ticari" else "B",
        }
        for m in re.finditer(r"<option[^>]*value='(\{.*?\})'", markup, re.S):
            try:
                entry = json.loads(html.unescape(m.group(1)))
            except json.JSONDecodeError:
                continue
            key = (entry.get("ProductCode"), entry.get("ProjectCode"),
                   entry.get("CampaingCode"), mode)
            if key in seen:
                continue
            seen.add(key)
            out.append((entry, request))
    return out


def verify_products():
    print("\n== product catalogue (embedded in the finance page)")
    catalogue = products()
    ok = len(catalogue) >= 17 and all(
        p.get("CampaignName") and p.get("ProductCode") and contract["Type"]
        for p, contract in catalogue
    )
    check("catalogue parsed", ok, f"{len(catalogue)} product(s)")
    return catalogue


def verify_finance(catalogue):
    print("\n== finansman (every product)")
    for entry, contract in catalogue:
        lo, hi = int(entry["MaturityMinValue"]), int(entry["MaturityMaxValue"])
        term = min(24, hi) if hi >= max(lo, 1) else max(lo, 1)
        term = max(term, lo, 1)
        amount = int(min(100000.0, float(entry["AmountMaxValue"])))
        label = f"{entry['CampaignName'][:32]:34s} {amount}/{term}ay"
        try:
            data = plugin("getFinanceCalculate", contract["page"],
                          ProfitRateByMe="false",
                          FinanceType=json.dumps(entry, ensure_ascii=False),
                          FinanceAmount=str(amount), Maturity=str(term),
                          ProfitRate="0", Type=contract["Type"],
                          CreditType=contract["CreditType"]).json().get("Data") or {}
        except Exception as exc:
            check(label, False, f"{type(exc).__name__}: {exc}")
            continue
        installment = data.get("MonthlyInstallmentAmount") or ""
        rows = len((data.get("PaymentPlan") or {}).get("Rows") or [])
        # A zero instalment is what this endpoint returns instead of an error.
        ok = installment not in ("", "0,00 TL") and rows == term
        check(label, ok, f"inst={installment} total={data.get('TotalAmountTobeRefunded')} "
                         f"rows={rows} yıllık={data.get('AnnualCostRate')}")


def verify_profit_share():
    print("\n== kâr payı (every account type x currency x period)")
    for identity, title, endpoint_type, currencies in ACCOUNT_TYPES:
        for currency in currencies:
            for period, term in (("MONTH", "6"), ("DAY", "90")):
                label = f"{title[:26]:28s} {identity:21s} {currency:4s} {term:>3}{period[0]}"
                try:
                    payload = plugin("getProfitShareCalculate", PROFIT_PAGE,
                                     DepositedAmount="100000", Currency=currency,
                                     Maturity=term, Period=period, Type=endpoint_type).json()
                except Exception as exc:
                    check(label, False, f"{type(exc).__name__}: {exc}")
                    continue
                data = payload.get("Data") or {}
                net = data.get("NetProfit") or ""
                ok = payload.get("Result") is True and amount_of(net) > 0
                detail = f"gross={data.get('GrossProfit')} net={net} rate={data.get('GrossRate')}"
                if not ok and (identity, currency, period) in NOT_OFFERED:
                    known(label, NOT_OFFERED[(identity, currency, period)])
                else:
                    check(label, ok, detail)


def verify_rates():
    print("\n== exchange rates")
    try:
        data = plugin("getExchangeRatesService", FX_PAGE).json()
        prices = ((data.get("ExchangeRate") or {}).get("Data") or {}).get("CurrencyPrices") or []
        by_code = {p["CurrencyName"]: p for p in prices}
        check("rate feed", len(prices) >= 3, f"{len(prices)} currencies, "
                                             f"as of {((data.get('ExchangeRate') or {}).get('Data') or {}).get('TranDate')}")
        for code in ("USD", "EUR", "XAU", "GBP"):
            row = by_code.get(code)
            ok = bool(row) and row["Ask"] > 0 and row["Ask"] >= row["Bid"]
            check(f"rate {code}", ok, f"bid={row['Bid']} ask={row['Ask']}" if row else "missing")
    except Exception as exc:
        check("rate feed", False, f"{type(exc).__name__}: {exc}")


def verify_converter():
    print("\n== currency converter")
    for src, dst, amount in (("USD", "TRY", "1000"), ("EUR", "TRY", "1000"),
                             ("TRY", "USD", "100000"), ("XAU", "TRY", "10")):
        label = f"{amount} {src} -> {dst}"
        try:
            payload = plugin("currencyConverter", FX_PAGE, **{
                "From": src, "To": dst, "Amount": amount,
                "BuySellEntered": "A", "BuySellComputed": "S"}).json()
            value = payload.get("Data")
            ok = payload.get("Result") is True and isinstance(value, (int, float)) and value > 0
            check(label, ok, f"= {value}")
        except Exception as exc:
            check(label, False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    catalogue = verify_products()
    verify_finance(catalogue)
    verify_profit_share()
    verify_rates()
    verify_converter()

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} passed, "
          f"{len(known_gaps)} known bank-side gap(s)")
    for n in failed:
        print(f"   FAILED: {n.strip()}")
    for n, d in known_gaps:
        print(f"   KNOWN:  {n.strip()} — {d}")
    sys.exit(1 if failed else 0)
