"""Call every Kuveyt Turk calculator endpoint for every product it declares.

The catalogue endpoint hands over the product codes and the limits each one
accepts, so the test cases are built from the bank's own metadata rather than
guessed. Checks are contract checks -- a field is present, a type is right, a
number is in a sane range -- never an exact value, because rates change daily
and that change is not a failure.

Usage: python verify_kuveytturk.py
"""

import collections
import sys

import httpx

BASE = "https://www.kuveytturk.com.tr/ck0d84?"
CATALOGUE = "9592031673D7885E535AEF67BC5D9213"
FINANSMAN = "30134915811C6D92B8F34A01FCF910EE"
FIRST_DATE = "7818136187BFD2CBB7EA0C5E2036888A"
PROFIT_SHARE = "1E32FE5C30C44BF2B51A08D1756ADEEB"
RATES = "C24AD4C0FDA76C73081889B634A8C039"
CARD = "AD36E047B34B678B1F7A995EA1821ABB"
LEASING = "E0B44AB4046932FB8BFDE1008D75818F"

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "x-requested-with": "XMLHttpRequest",
    "x-bone-language": "TR",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
}

# FEC is the bank's currency code. The catalogue names its amount limits after
# the currency, so this also selects which limit keys apply.
FEC = {"0": "Tl", "1": "Usd", "19": "Euro", "24": "Gr", "15": "Sterlin"}

client = httpx.Client(timeout=40, headers=HEADERS)
results = []
known_gaps = []

# Kuveyt Turk publishes no profit-share rate for Yuvam at all: every currency,
# term and payload shape answers 200 with zeros, and their own page shows the
# same. Recorded so it stays visible instead of being retried as a bug.
NO_PUBLISHED_RATE = {"YUVAMKATILMA": "bank returns zeros on their own page too"}


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""), flush=True)


def known(name, detail):
    """A gap on the bank's side, confirmed against their own page. Not our bug,
    and not a pass either -- it stays visible without failing the run."""
    known_gaps.append((name, detail))
    print(f"  KNOWN {name}   {detail}", flush=True)


def params(entry):
    """Catalogue Parameters is a flat key/value list with repeated keys."""
    out = collections.defaultdict(list)
    for p in entry.get("Parameters", []):
        out[p["Key"]].append(p["Value"])
    return out


def catalogue(name, page):
    r = client.get(f"{BASE}{CATALOGUE}&p1={name}", headers={"referer": page})
    r.raise_for_status()
    return r.json()


PAGE = "https://www.kuveytturk.com.tr/hesaplama-araclari/"


def verify_catalogue():
    print("\n== product catalogue")
    for name in ("LoanCalculator", "ProfitSharingCalculator",
                 "CreditCardInstallmentCalculator", "LeasingCalculator",
                 "GoldInvestmentCalculator"):
        try:
            data = catalogue(name, PAGE)
            ok = isinstance(data, list) and len(data) > 0 and all("Title" in x for x in data)
            check(f"{name}", ok, f"{len(data)} product(s)")
        except Exception as exc:
            check(f"{name}", False, f"{type(exc).__name__}: {exc}")


def verify_finansman():
    print("\n== finansman (every product code)")
    page = PAGE + "finansman-hesaplama"
    for entry in catalogue("LoanCalculator", page):
        p = params(entry)
        code = p["ProductCode"][0]
        # Not every product declares every limit -- Seyahat omits the maximum.
        lo = int(p["DefaultAmountMin"][0]) if p["DefaultAmountMin"] else 1000
        hi = int(p["DefaultAmountMax"][0]) if p["DefaultAmountMax"] else 5000000
        amount = min(max(100000, lo), hi)
        # MaturityTerm, not MaturityTermMax. Two entries share the code
        # ELKTRARACSARJUNITE with different limits, and the endpoint validates
        # against the entry named in p8 -- asking for 36 under the Elektrikli
        # Arac title is a 400 while the same code under Bisiklet allows it.
        term = int(p["MaturityTerm"][0]) if p["MaturityTerm"] else 12
        body = {"i": False, "p1": "1", "p2": str(amount), "p3": str(term),
                "p4": code, "p5": code, "p6": "0.00", "p7": "",
                "p8": entry["Title"]}
        try:
            d = client.post(BASE + FINANSMAN, json=body, headers={"referer": page}).json()
            if not (d.get("Meta") or {}).get("InstallmentPayment"):
                # The endpoint intermittently answers 200 with an empty Meta.
                # One retry separates a flaky call from a genuinely bad product.
                d = client.post(BASE + FINANSMAN, json=body,
                                headers={"referer": page}).json()
            meta = d.get("Meta") or {}
            inst = meta.get("InstallmentPayment") or 0
            ok = (float(meta.get("LoanAmount", 0)) == amount
                  and str(meta.get("InstallmentCount")) == str(term)
                  and inst > 0
                  and len(d.get("Installments") or []) == term)
            check(f"{entry['Title'][:34]:36s} {code:20s} {amount}/{term}", ok,
                  f"inst={inst} total={meta.get('TotalAmount')} rate={meta.get('ProfitRate')}")
        except Exception as exc:
            check(f"{entry['Title'][:34]:36s} {code}", False, f"{type(exc).__name__}: {exc}")


def verify_first_date():
    # Only IHTIYACKART offers a choosable first instalment. Every other product
    # answers 404 with an empty body, which means "not applicable" here, not
    # "broken" -- so only the product that supports it is asserted.
    print("\n== first-instalment date window (IHTIYACKART only)")
    page = PAGE + "finansman-hesaplama"
    try:
        d = client.get(f"{BASE}{FIRST_DATE}&p1=IHTIYACKART", headers={"referer": page}).json()
        ok = bool(d.get("MinFirstInstallmentDate")) and bool(d.get("MaxFirstInstallmentDate"))
        check("IHTIYACKART", ok, f"{d.get('MinFirstInstallmentDate','')[:10]} .. "
                                 f"{d.get('MaxFirstInstallmentDate','')[:10]}")
    except Exception as exc:
        check("IHTIYACKART", False, f"{type(exc).__name__}: {exc}")


def verify_profit_share():
    print("\n== kar payi (every account type x currency x term)")
    page = PAGE + "kar-payi-hesaplama"
    for entry in catalogue("ProfitSharingCalculator", page):
        p = params(entry)
        group = p["ProductGroup"][0]
        code = p["ProductCode"][0] if p["ProductCode"] else ""
        months = [t for t in p["MaturityTerm"] if t.isdigit()]
        for fec in p["FEC"]:
            unit = FEC.get(fec, "Tl")
            day_floor = p.get(f"MaturityDayMinAmount{unit}")
            month_floor = p.get(f"MaturityMonthMinAmount{unit}")

            # Day mode and month mode take the same p3 field with p10 choosing
            # the unit, and the catalogue's MaturityType is not dependable --
            # Ara Donem is declared Month yet only answers in days. So try both
            # and let the bank decide which one this product supports.
            attempts = []
            if p["MaturityTermMinDay"] or p["DefaultDayValue"]:
                days = int((p["DefaultDayValue"] or p["MaturityTermMinDay"])[0])
                attempts.append((int((day_floor or ["20000"])[0]) * 2, days, True, "gun"))
            if months:
                amt = int((month_floor or day_floor or ["20000"])[0]) * 2
                attempts.append((amt, int(months[-1]), False, "ay"))
                # Ara Donem takes its months as exact 30-day multiples: 30, 90
                # and 180 all answer, while 31 and 365 come back zero.
                attempts.append((amt, int(months[0]) * 30, True, "gun"))
            if not attempts:
                attempts.append((20000, 30, True, "gun"))

            outcome, label = None, ""
            for amount, term, day_mode, suffix in attempts:
                body = {"i": False, "p1": str(amount), "p2": group, "p3": str(term),
                        "p4": fec, "p5": code, "p9": entry["Title"], "p10": day_mode}
                label = f"{entry['Title'][:30]:32s} {unit:8s} {amount}/{term}{suffix}"
                try:
                    d = client.post(BASE + PROFIT_SHARE, json=body,
                                    headers={"referer": page}).json()
                except Exception as exc:
                    outcome = (False, f"{type(exc).__name__}: {exc}")
                    continue
                ratio, net = d.get("ProfitShareRatio"), d.get("NetProfitShare")
                # An unsupported combination answers 200 with every field zero
                # rather than an error, so zero has to count as a failure.
                if ratio and net and float(net) > 0:
                    outcome = (True, f"ratio={ratio} net={net} seg={d.get('SegmentName')}")
                    break
                outcome = (False, f"ratio={ratio} net={net} - bank returned no rate")
            if not outcome[0] and code in NO_PUBLISHED_RATE:
                known(label, NO_PUBLISHED_RATE[code])
            else:
                check(label, outcome[0], outcome[1])


def verify_rates():
    print("\n== fx and precious metals")
    page = PAGE + "doviz-cevirici"
    try:
        rows = client.get(BASE + RATES, headers={"referer": page}).json()
        by_code = {r["CurrencyCode"]: r for r in rows}
        check("feed size", len(rows) >= 20, f"{len(rows)} rows")
        for code in ("USD", "EUR", "GBP", "ALT (gr)", "GMS (gr)", "ZCeyrek"):
            row = by_code.get(code)
            ok = bool(row) and row["SellRate"] > 0 and row["SellRate"] >= row["BuyRate"]
            check(f"rate {code:10s}", ok,
                  f"buy={row['BuyRate']} sell={row['SellRate']}" if row else "missing")
        # Gold is quoted here and multiplied client-side; there is no gold
        # compute endpoint to call.
        gold = by_code.get("ALT (gr)")
        if gold:
            check("gold 10 gr from rate", gold["SellRate"] * 10 > 0,
                  f"{gold['SellRate'] * 10:,.2f} TL")
    except Exception as exc:
        check("rates", False, f"{type(exc).__name__}: {exc}")


def verify_card():
    print("\n== card instalment (every card)")
    page = PAGE + "kart-taksit-hesaplama"
    for entry in catalogue("CreditCardInstallmentCalculator", page):
        p = params(entry)
        code = p["ProductCode"][0]
        ptype = p["ProductType"][0]
        lo = int(p["MinAmount"][0]) if p["MinAmount"] else 1
        amount = max(10000, lo)
        declared = int(p["MaxInstallmentValue"][0])
        # The catalogue over-promises: Saglam Kart Troy declares 12 but the
        # endpoint 404s above 9. Step down to find the count it really accepts.
        outcome, label = (False, "no instalment count accepted"), ""
        for n in range(declared, 0, -1):
            body = {"p1": amount, "p2": n, "p3": int(ptype), "p4": 0,
                    "p5": code, "p6": entry["Title"]}
            label = f"{entry['Title'][:32]:34s} {code:4s} {amount}/{n}x"
            try:
                r = client.post(BASE + CARD, json=body, headers={"referer": page})
                d = r.json()
            except Exception:
                continue
            first = d.get("FirstInstallementAmount") or 0
            if first > 0:
                note = f"rate={d.get('ProfitRate')} first={first} total={d.get('TotalDelayInterestAmount')}"
                if n != declared:
                    note += f"  (catalogue says max {declared})"
                outcome = (True, note)
                break
        check(label, outcome[0], outcome[1])


def verify_leasing():
    print("\n== leasing (every currency)")
    page = PAGE + "leasing-hesaplama"
    for entry in catalogue("LeasingCalculator", page):
        p = params(entry)
        goods, down, term = 100000, 10000, 12
        # Currency is p2 (the FEC code), not the p8 title. Varying p8 alone
        # returns identical numbers for all three currencies, which reads as a
        # working test while nothing is actually being varied.
        fec = p["FEC"][0] if p["FEC"] else "0"
        url = (f"{BASE}{LEASING}&p1={goods}&p4={down}&p2={fec}&p5={term}"
               f"&p3=1&p6=30&p7=0&p8={entry['Title']}&p9=false")
        label = f"{entry['Title']:4s} (FEC {fec:>2s}) {goods}/{term}ay"
        try:
            d = client.get(url, headers={"referer": page}).json()
            inst = d.get("InstallmentAmount") or 0
            rows = d.get("InstallmentCalculations") or []
            ok = inst > 0 and (d.get("TotalAmount") or 0) > 0 and len(rows) > 0
            check(label, ok, f"inst={inst} total={d.get('TotalAmount')} "
                             f"rate={d.get('MontlyProfitRate')} rows={len(rows)}")
        except Exception as exc:
            check(label, False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    verify_catalogue()
    verify_finansman()
    verify_first_date()
    verify_profit_share()
    verify_rates()
    verify_card()
    verify_leasing()

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} passed, "
          f"{len(known_gaps)} known bank-side gap(s)")
    for n in failed:
        print(f"   FAILED: {n}")
    for n, d in known_gaps:
        print(f"   KNOWN:  {n.strip()} — {d}")
    sys.exit(1 if failed else 0)
