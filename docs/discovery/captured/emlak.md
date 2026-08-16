# Türkiye Emlak Katılım — endpoint inventory

Captured 2026-08-08. Host **`www.emlakkatilim.com.tr`** — the bank list's
`emlakbank.com.tr` is stale. All calculators sit on one page,
`/tr/hesaplama-araclari`. **31/31 verified**, 2 known bank-side gaps.

## Transport: WAF

Same F5 WAF as Albaraka — plain `httpx` gets `200` with an HTML
"Request Rejected" body. Use `curl_cffi` impersonating Chrome.

**Do not override the `user-agent`** when impersonating: `curl_cffi` sets a UA
matching the TLS fingerprint, and a mismatched pair is rejected again. That
failure appears as a JSON decode error, not as an obvious block.

## Endpoints

Plain query-string `GET`s, no token, no session.

| what | endpoint | parameters |
|---|---|---|
| kâr payı | `GET /Plugins/CalculateProfitShareRate` | `LanguageId=1`, `Money`, `Fec`, `MaturityTerm`, `profitShareInstallment=0`, `profitShareInstallmentValueDay` |
| finansman | `GET /Plugins/CalculateLoansProduct` | `CalculationTypeId=1`, `ProductTypeId`, `LoanAmount`, `LoanMaturity`, `LoanSegmentId=1` |
| term limits | `GET /Plugins/SelectLoansProperty` | `ProductTypeId` |

## Products

`ProductTypeId`: `ARACBINEK2EL` 2. El Taşıt (0–48 ay) · `ARACBINEKYENI` 0 Km
Taşıt (0–48) · `EVOFISGERECLERI` İhtiyaç (0–36) · `GMENKULKONUTYENI` Yeni Konut
(0–120).

`Fec`: `0` TL · `1` USD · `19` EUR · `24` ALT (gr) · `26` GMS (gr).
`MaturityTerm` in days: `31`, `91`, `180`, `364`, `366`.

Note the codes `ARACBINEK2EL` and `GMENKULKONUTYENI` are shared with Kuveyt
Türk — both run related Unigate-derived stacks. **The codes are not
interchangeable across banks**; treat them as bank-scoped.

## Responses

Unlike its Unigate siblings, Emlak returns **JSON numbers, not formatted
strings**: `{"Success":true,"Data":{...}}`.

Finance → `TotalInstallmentAmount`, `InstallmentCount`, `ProfitRate`,
`CommissionAmount`, `FundingAmount`, `TotalCost`, `MonthlyConstRate`.
Kâr payı → `GrossProfitShare`, `NetProfitShare`, `GrossProfitShareYearly`,
`NetProfitShareYearly`, `SegmentName`, `TotalAmountNetProfitShare`.

Verified 100 000 TL / 24 ay: taşıt total 183 820,42 (%4,29) · ihtiyaç
174 895,86 (%3,89) · konut 147 733,31 (%3,39).
Kâr payı 100 000 TL 1 ay TL → net 2 163,34, yıllık %25,47.

## Known gaps

Gold (`Fec=24`) prices for 1 ay, 3 ay and 6 ay but returns zeros at 12 ay and
12+ ay. Silver (`Fec=26`) prices across all terms.

## Rates and conversion — found 2026-08-15

Not in the original capture. `/tr/tum-kurlarimiz` (a page, not a plugin)
server-renders a 23-instrument rates table: USD, EUR, gold/silver/platinum
(gr), AED, RUB, CNY, QAR, CAG (gr, 22-carat coin), AUD, DKK, SEK, CHF, CAD,
KWD, NOK, GBP, SAR, JPY, BHD, MYR, and Çeyrek Altın (quarter coin) — one
`<table>`, three columns (`Döviz Cinsi`, `Banka Alış`, `Banka Satış`), no
request behind it.

A live JSON endpoint was searched for, since a table this size usually has
one: `app.min.js` references `SERVICE_URL + "CurrencyTypes/GetFxRatesAll"`
(`SERVICE_URL` resolves to `/services/api/`), but the call is wrapped in a
commented-out block, and `GET /services/api/CurrencyTypes/GetFxRatesAll`
answers a clean 404 — decommissioned, not merely unlinked. The table is the
only way to reach these rates.

No converter endpoint exists either (same search, same bundle, nothing
found), so `convert` derives from the scraped table via
`BaseBank.convert_from_rates` — the buy rate to sell the source, the sell rate
to buy the target, both the bank's own published figures. Every conversion
comes back `derived=True`.
