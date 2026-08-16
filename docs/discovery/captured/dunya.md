# Dünya Katılım — endpoint inventory

Captured 2026-08-08. Platform: ASP.NET Core. Host **`dunyakatilim.com.tr`** —
the bank list's `dunyakatilim.com` does not resolve at all (NXDOMAIN).
**42/42 verified**, no known gaps — the cleanest coverage of the ten.

## Where the calculators are

**All of them are on the homepage.** There is no calculator URL to discover, so
URL-based crawling finds nothing here.

## Auth

Anti-forgery token from the homepage, sent in the form body of every POST:

```
POST /<Action>?lang=tr
Content-Type: application/x-www-form-urlencoded
<params>&__RequestVerificationToken=<token>
```

No WAF; plain `httpx` works. The handoff's note that "Dünya times out under
httpx" was the dead domain, not the transport.

## Endpoints

| what | endpoint | key parameters |
|---|---|---|
| finansman | `POST /LoanCheckRate?lang=tr` | `productName`, `productCode`, `productCategory`, `amount`, `installmentCount`, `userRate`, `userSelected=false` |
| finansman limits | `POST /LoanInstallmentValues?lang=tr` | `productCode` |
| kâr payı | `POST /DividendEstimatedProfit?lang=tr` | `balance`, `currencyCode`, `maturityCode`, `maturityPeriodValue`, `productCode` |
| döviz alış | `POST /CurrencyBuyCalculate?lang=tr` | `buyFromAmount`, `buyFromCurrency`, `buyToCurrency`, `transactionType=1` |
| kur geçmişi | `POST /CurrencyHistory` | `currencyCode`, `beginDate`, `endDate`, `chartType` |
| maden geçmişi | `POST /PreciousMetalHistory` | as above |

## Products

Finance `productCode` (from the homepage `select[name=state]`), with the
`productCategory` each one needs:

| code | name | category | max amount | terms |
|---|---|---|---|---|
| `ARACBINEK2ELTUKETICI` | Araç Binek 2.El | `Vehicle` | 400 000 | 1–48 |
| `ARACBINEKYENITUKETICI` | Araç Binek Yeni | `Vehicle` | 400 000 | 1–48 |
| `ARSATUKETICI` | Arsa | `MiscellaneousRealEstate` | 12 000 000 | 1–60 |
| `2ELKONUTTUKETICI` | Konut 2.El | `House` | 3 000 000 | 1–60 |
| `KONUTTUKETICI` | Konut Yeni | `House` | 12 000 000 | 1–60 |
| `TUKETICIIHTIYAC` | Tüketici İhtiyaç | `Miscellaneous` | 2 000 000 | 1–36 |

**Kâr payı products come as JSON embedded in the homepage**, inside the
`dividendSelect` option values (HTML-escaped, single-quoted attribute — the same
shape as Albaraka). Each carries its own `productMaturitySettings` with
`maturityCode` and the exact `maturityPeriodBeginValue` in days:

- `KTLMHSP` Standart Katılma — TRY/USD/EUR, 31 / 91 / 181 / 365 / 366+ gün, plus
  `KTLMHSP_ESNEKVADELI`
- `GNSHSP` Güneş Katılma — TRY only, günlük 1–30
- `ALTKTLMHSP` Altın Katılma — XAU only, 31 / 91 / 180 / 365 / 366+ gün

Sending a `maturityCode` that does not belong to the product returns
`{"result":"ERROR","message":"ProductMaturitySettings (…) mevcut değil."}` —
a **real error message**, which makes this the most debuggable bank of the ten.

FX pairs: USD, EUR, GBP, AUD, CAD, CNY, JPY, SAR, CHF; metals XAU, XAG, XPT, XPD.

## Responses

JSON numbers, `result: "SUCCESS"` on the happy path.

Finance → `monthlyInterest` (the instalment, despite the name), `totalPayment`,
`rate`, `paymentPlanHTML`. That last field is a full HTML document, not a
throwaway — added 2026-08-16: it states "Yıllık kar oranı" (the annual cost
rate) in a `title`/`val` pair and carries the entire 24-row instalment
schedule (principal, profit, BSMV, KKDF, remaining balance) in a table below
it. `finance_quote` now parses both out of that same response instead of
requesting it and reading only the two totals at the top.
Kâr payı → `grossProfitAmount`, `grossProfitRate`, `netProfitAmount`.
Döviz → `sourceAmount`, `destinationAmount` (**converted server-side**).

Verified 100 000 TL / 24 ay: konut 5 898,38 (%2,99) · ihtiyaç 7 379,48 (%3,99).
Kâr payı 100 000 TL 1 ay TRY → brüt 2 828,55 (%33,3) / net 2 333,55.
1000 USD → 47 519,50 TRY.
