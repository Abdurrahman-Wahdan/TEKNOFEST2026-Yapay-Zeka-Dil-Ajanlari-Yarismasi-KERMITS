"""Ziraat Katılım — finansman payment plans.

Drupal. The calculators live on the homepage with no dedicated URL, so
URL-based discovery never finds them.

Two of the three calculators are reachable headlessly and one is not:

- finansman goes through /ajax/finansmanhesapla, which answers plain httpx.
- kâr payı and leasing submit the Drupal form itself
  (/anasayfa?ajax_form=1), and that path answers 493 to every non-browser
  client, curl_cffi impersonation included. There is no /ajax/ route for them
  -- every plausible name returns a Drupal "No route found".

So kâr payı and leasing are browser-only for this bank. That is recorded rather
than worked around, because a health check has to know the difference.

The answer comes back as HTML inside a Drupal command array. The numbers are
the bank's; we only pull them out of the markup.

Usage: python verify_ziraat.py
"""

import re
import warnings

warnings.filterwarnings("ignore")

import httpx

from checks import AJAX, Report, amount

HOST = "https://www.ziraatkatilim.com.tr"
PAGE = f"{HOST}/anasayfa"

FORM = {**AJAX, "referer": PAGE,
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8"}

client = httpx.Client(timeout=40, verify=False, follow_redirects=True,
                      headers={"user-agent": AJAX["user-agent"]})
report = Report("ziraat")


def products():
    """Finance products are <option> values on the homepage; the value is an
    opaque numeric eid that every other call takes."""
    page = client.get(PAGE).text
    match = re.search(r'<select[^>]*name="finansman_type"[^>]*>(.*?)</select>', page, re.S)
    if not match:
        return []
    return [(v, re.sub(r"\s+", " ", n).strip())
            for v, n in re.findall(r'<option[^>]*value="([^"]+)"[^>]*>\s*([^<]*)', match.group(1))
            if v]


def plan_text(payload):
    """Pull the readable payment plan out of the Drupal command array."""
    for command in payload:
        if command.get("command") == "insert" and command.get("data"):
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", command["data"]))
    return ""


def verify_products():
    report.section("product list and limits (get-vade)")
    catalogue = products()
    report.check("catalogue parsed", len(catalogue) >= 5, f"{len(catalogue)} product(s)")
    usable = []
    for eid, name in catalogue:
        try:
            data = client.post(f"{HOST}/ajax/get-vade", data={"eid": eid},
                               headers=FORM).json().get("data") or {}
        except Exception as exc:
            report.check(f"{name[:34]:36s} {eid}", False, f"{type(exc).__name__}: {exc}")
            continue
        terms = data.get("range") or []
        ok = bool(terms) and amount(data.get("ratio")) > 0
        report.check(f"{name[:34]:36s} {eid}", ok,
                     f"vade 1-{terms[-1] if terms else '-'}, oran {data.get('ratio')}, "
                     f"max {data.get('maximum_amount')}")
        if ok:
            usable.append((eid, name, data))
    return usable


def verify_finance(usable):
    report.section("finansman payment plans")
    for eid, name, meta in usable:
        terms = meta.get("range") or [12]
        term = min(24, terms[-1])
        limit = amount(meta.get("maximum_amount")) or 100000
        principal = int(min(100000, limit))
        label = f"{name[:34]:36s} {principal}/{term}ay"
        try:
            payload = client.post(f"{HOST}/ajax/finansmanhesapla?_wrapper_format=drupal_ajax",
                                  headers=FORM, data={
                                      "lang": "tr", "finansman_is_bank_ratio": "true",
                                      "finans_type": eid, "finans_kar_orani": meta.get("ratio"),
                                      "finans_vade": str(term), "finans_tutari": str(principal),
                                      "_drupal_ajax": "1"}).json()
        except Exception as exc:
            report.check(label, False, f"{type(exc).__name__}: {exc}")
            continue
        text = plan_text(payload)
        money = re.findall(r"([\d.]+,\d{2})\s*TRY", text)
        rate = re.search(r"%(\d+,\d+)", text)
        # First two TRY figures are the principal and the instalment.
        ok = len(money) >= 3 and amount(money[1]) > 0 and amount(money[2]) > amount(money[0])
        report.check(label, ok,
                     f"taksit={money[1] if len(money) > 1 else '-'} "
                     f"toplam={money[2] if len(money) > 2 else '-'} "
                     f"oran=%{rate.group(1) if rate else '-'}")


def verify_maturity_types():
    report.section("kâr payı maturity types")
    try:
        types = client.post(f"{HOST}/ajax/get-maturity-types?_wrapper_format=drupal_ajax",
                            headers=FORM,
                            data={"eid": "2", "lang_id": "tr", "_drupal_ajax": "1"}).json()
        rows = types.get("types") or []
        report.check("maturity types", len(rows) > 0,
                     ", ".join(r["name"] for r in rows[:4]))
    except Exception as exc:
        report.check("maturity types", False, f"{type(exc).__name__}: {exc}")

    report.known("kâr payı and leasing results",
                 "only reachable by submitting the Drupal form, which answers 493 "
                 "to every non-browser client; no /ajax/ route exists")


if __name__ == "__main__":
    usable = verify_products()
    verify_finance(usable)
    verify_maturity_types()
    report.finish()
