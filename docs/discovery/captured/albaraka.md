# Albaraka Türk — endpoint inventory

Captured 2026-08-08. Platform: Unigate. Host is **`www.albaraka.com.tr`** —
`albarakaturk.com` from the bank list redirects there. Five calculator pages
under `/tr/hesaplama-araclari/`, four backend plugins between them.

## The WAF, first

Albaraka runs an F5 WAF that answers `200` with an HTML
`<title>Request Rejected</title>` page for any `/plugins/` request that does not
come from a real browser. It is **not** header-based: full Chrome headers,
`adrum: isAjax:true`, a warmed cookie jar and HTTP/2 all still get rejected. It
fingerprints the TLS handshake.

`curl_cffi` impersonating Chrome gets through with no other change, and returns
byte-identical numbers to the browser:

```python
from curl_cffi import requests as cr
session = cr.Session(impersonate="chrome124")
```

The page HTML itself is served to plain `httpx` — only `/plugins/` is guarded.
That asymmetry is what makes this easy to misdiagnose.

## Everything found

| # | what | endpoint | verified |
|---|---|---|---|
| 1 | finansman | `GET /plugins/getFinanceCalculate` | 16/16 products |
| 2 | kâr payı | `GET /plugins/getProfitShareCalculate` | 3 account types |
| 3 | FX + gold rates | `GET /plugins/getExchangeRatesService` | 4 currencies |
| 4 | currency converter | `GET /plugins/currencyConverter` | 4 pairs |

Every plugin takes the same five preamble parameters:

```
langId=bf2689d9-071e-4a20-9450-b1dbdd39778f
language=tr
Slug=<the page's last path segment>
searchUrl=%2Ftr%2Farama
customFinancingName=
```

Headers: `referer` (the matching page), `x-requested-with: XMLHttpRequest`,
`adrum: isAjax:true`, `accept: application/json, text/javascript, */*; q=0.01`.
No cookie or token is needed once the TLS fingerprint passes.

Ignored as noise: `/plugins/SearchResult`, `SearchResultCount`,
`SearchResultPages` (site search, fired by the header widget on every page) and
`/_assets/data/data.json` (a Lottie animation).

## 1. Product catalogue — not an endpoint

**The catalogue is embedded in the finance page**, as JSON inside
`<option value='…'>` on `select#slcfinansmanTuru`. All three finance pages carry
the identical 16-product list, so one fetch of any of them is enough.

Two traps: the attribute is **single-quoted** and the JSON inside is
**HTML-escaped**, so the obvious `value="(\{.*?\})"` regex matches nothing and
reads as "this bank has no products".

The whole blob is echoed back verbatim as the `FinanceType` parameter — do not
try to reconstruct it field by field.

| product | ProductCode | ProjectCode | rate | max amount | term |
|---|---|---|---|---|---|
| SIFIR KM TAŞIT | `TASKRED` | `0TAŞIT` | 3.75 | 9 999 999 | 1–48 |
| 2. EL TAŞIT | `TASKRED` | `2.EL` | 3.75 | 9 999 999 | 0–48 |
| DİJİTAL ARAÇ | `TASKRED` | `SBZARAC` | 3.75 | 9 999 999 | 1–48 |
| İŞYERİ | `ARSAKRD` | `ISYERI` | 3.95 | 1 000 000 | 1–60 |
| ARSA | `ARSAKRD` | `ARSABIR` | 3.95 | 9 999 999 | 1–60 |
| EĞİTİM | `IHTKRED` | `EĞİTİM` | 4.00 | 9 999 999 | 1–12 |
| KONUT KİRA | `IHTKRED` | `KNTKİRA` | 4.00 | 5 000 000 | 11–12 |
| YURT HİZMETİ | `IHTKRED` | `YURTH` | 4.00 | 9 999 999 | 1–10 |
| DİĞER TEKNOLOJİ | `IHTKRED` | `TEKNO` | 4.00 | 9 999 999 | 1–36 |
| CEP TELEFONU | `IHTKRED` | `TEKNO` | 4.00 | 9 999 999 | 1–12 |
| ENGELSİZ HAYAT | `IHTKRED` | `ENGLFİN` | 4.00 | 9 999 999 | 1–36 |
| PREFABRİK | `IHTKRED` | `PRFBFİN` | 4.00 | 9 999 999 | 1–36 |
| MOTOSİKLET | `IHTKRED` | `MOTOFİN` | 4.00 | 9 999 999 | 1–36 |
| PRATİK FİNANSMAN KART | `IHTKRED` | `PRATIK` | 3.95 | 150 000 | 1–34 |
| İLK EVİM KONUT | `KONTKRD` | `YOKKNTF` | 3.04 | 9 999 999 | 1–120 |
| 2. VE SONRAKİ KONUT | `KONTKRD` | `VARKNTF` | 3.04 | 9 999 999 | 1–120 |

`ProductCode` alone does not identify a product — six share `IHTKRED`. The
identity is `(ProductCode, ProjectCode, CampaingCode)`.

## 2. Finansman

```
GET /plugins/getFinanceCalculate
    &ProfitRateByMe=false
    &FinanceType=<the catalogue blob, JSON>
    &FinanceAmount=100000&Maturity=24&ProfitRate=0&Type=B&CreditType=B
```

Response `Data`: `MonthlyInstallmentAmount`, `TotalAmountTobeRefunded`,
`AnnualCostRate`, `TotalFees`, and `PaymentPlan.Rows[]` with one row per month.

Values are **formatted Turkish strings**, not numbers: `"6.684,28 TL"`,
`"% 64,46"`. Parse with dot-as-thousands and comma-as-decimal.

Verified 100 000 TL / 24 ay: taşıt 6 684,28 · işyeri 7 354,51 · konut 5 936,07.

`ProfitRateByMe=true` lets the caller impose their own `ProfitRate`. We never
use it — the bank's rate is the answer.

## 3. Kâr payı

```
GET /plugins/getProfitShareCalculate
    &DepositedAmount=100000&Currency=TRY&Maturity=6&Period=MONTH&Type=KTLMHSP
```

`Period` is `MONTH` or `DAY`; `Maturity` is counted in that unit.

| account | Type | currencies | period |
|---|---|---|---|
| Katılma Hesabı | `KTLMHSP` | TRY, USD, EUR | month or day |
| Ara Dönem Kâr Payı Ödemeli | `KTLARDM` | TRY, USD, EUR | **month only** |
| Kur Korumalı (bireysel/ticari) | `KURKTLMHSP` | — | **not priced** |

Response `Data`: `GrossProfit`, `NetProfit`, `GrossRate`, `NetRate`,
`InvestedAmountPlusNetProfit`, `IncomeTax`, `CurrencyCode`.

Verified 100 000 TL 6 ay → gross 18 114,26 / net 14 944,26 / %36,73.

**Zeros mean "not offered", not "error"** — same failure mode as Kuveyt Türk. A
zero check must parse the number, because the suffix follows the currency: a
string test against `"0,00 TRY"` happily passes `"0,00 USD"`.

Confirmed against Albaraka's own page, not merely from our calls failing:
gold (`XAU`) participation is listed but returns zeros; Ara Dönem returns zeros
in day mode; Kur Korumalı returns zeros for every currency and period.

## 4. Rates and conversion

`GET /plugins/getExchangeRatesService` → `ExchangeRate.Data.CurrencyPrices[]`
with `{CurrencyName, CurrencyCodeName, Bid, Ask}` for **USD, EUR, XAU, GBP**
only, plus `TranDate` and a `Bist100` block.

`GET /plugins/currencyConverter&From=USD&To=TRY&Amount=1000&BuySellEntered=A&BuySellComputed=S`
→ `{"Result":true,"Data":47250}` — a bare number, converted server-side.

So unlike Kuveyt Türk, **Albaraka converts for us**; no arithmetic on our side,
including for gold (`10 XAU → 65 487,02 TL`).

## Verification

`python docs/discovery/verify_albaraka.py` — **35/35 pass**, 15 known bank-side
gaps, all listed above.
