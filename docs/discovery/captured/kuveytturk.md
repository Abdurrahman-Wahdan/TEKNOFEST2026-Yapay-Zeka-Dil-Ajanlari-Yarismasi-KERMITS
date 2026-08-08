# Kuveyt Türk — endpoint inventory

Captured 2026-08-08. Platform: Magiclick. Six calculator pages under
`/hesaplama-araclari/`, all served by one opaque path `\ck0d84?<hash>` with a
**different hash per calculator**. Every call below was replayed with plain
`httpx` — no browser, no cookie, no session, no CSRF token — and returned 200.

Base: `https://www.kuveytturk.com.tr/ck0d84?`

Headers required on every call:

```
accept: application/json
content-type: application/json
x-requested-with: XMLHttpRequest
x-bone-language: TR
referer: https://www.kuveytturk.com.tr/hesaplama-araclari/<page>
```

## Everything found

| # | what | method + hash | verified |
|---|---|---|---|
| 1 | product catalogue (all five calculators) | `GET 9592031673D7885E535AEF67BC5D9213&p1=<Calculator>` | 200, 0.03 s |
| 2 | finansman instalment | `POST 30134915811C6D92B8F34A01FCF910EE` | 200, 1.9 s |
| 3 | first-instalment date window | `GET 7818136187BFD2CBB7EA0C5E2036888A&p1=<ProductCode>` | 200, 0.03 s |
| 4 | kâr payı (profit share) | `POST 1E32FE5C30C44BF2B51A08D1756ADEEB` | 200, 0.10 s |
| 5 | FX + precious-metal rates | `GET C24AD4C0FDA76C73081889B634A8C039` | 200, 0.08 s |
| 6 | card instalment | `POST AD36E047B34B678B1F7A995EA1821ABB` | 200, 0.09 s |
| 7 | leasing | `GET E0B44AB4046932FB8BFDE1008D75818F&p1=…&p9=…` | 200, 0.15 s |

Also seen and **not** part of any calculator: `chatbot.kuveytturk.com.tr`
`/api/Common/IsOpen`, `/api/Common/GetGuid`, `/messaging/*.json` — support-chat
infrastructure.

The gold page (`altin-yatirma-hesaplama`) fires **no compute call**. It reads the
rates feed (#5, which carries `ALT (gr)`, `ZCeyrek`, `GMS`, `PLT`, `PLD`, `CAG`)
and multiplies in the browser.

## Contracts

### 1. Product catalogue

`GET …9592031673D7885E535AEF67BC5D9213&p1=<Calculator>` where `<Calculator>` is
one of `LoanCalculator`, `ProfitSharingCalculator`,
`CreditCardInstallmentCalculator`, `LeasingCalculator`,
`GoldInvestmentCalculator`.

Returns `[{Title, Note, FooterNote, Parameters:[{Key,Value}]}]`. `Parameters`
carries `ProductCode`, and the min/max the calculator will accept
(`MaturityTermMin/Max`, `DefaultAmountMin/Max`). This is the only place product
codes exist — everything else needs one as input.

19 finansman products, e.g. `ECOMMERCE` (Alışveriş, 1–36 ay),
`SAGLIKFINANSMANI` (İhtiyaç, 1–36), `IHTIYACKART` (6–34),
`GMENKULKONUTYENI` (Konut, 1–120, max 3 000 000), `ARACBINEKYENI` (Araç, 1–48),
`EGITIMFINANSMANI`, `HACFINANSMANI`, `KIRAFINANSMANI`, `SEYAHATFINANSMANI`,
`TEKNEFINANSMANI`, `GMENKULARSA`, `GMENKULISYERIYENI`, `ELKTRARACSARJUNITE`,
`DIJITALARACBINEK`, `DIJITALARACTICARI`, `ARACTICARIYENI`, `ARACBINEK2EL`,
`ARACTICARI2EL`.

### 2. Finansman

```
POST …30134915811C6D92B8F34A01FCF910EE
{"i":false,"p1":"1","p2":"100000","p3":"24","p4":"IHTIYACKART",
 "p5":"IHTIYACKART","p6":"0.00","p7":"","p8":"İhtiyaç Kart"}
```

`p2` amount · `p3` term in months · `p4`/`p5` ProductCode · `p8` display title ·
`p7` optional first-instalment date (`YYYY-MM-DD`, blank is accepted).

Response `Meta`: `InstallmentPayment`, `TotalAmount`, `ProfitRate`,
`MonthlyCost`, `YearlyCost`, `TotalCost`, `AllocationAmount`, `SurveyFee`,
`HypothecFee`; plus `Installments[]` with `PrincipalAmount`, `ProfitAmount`,
`KKDF`, `BSMV`, `RemainingPrincipalAmount`, `MaturityDate`.

100 000 / 24 ay / IHTIYACKART → instalment 7 136.18, total 171 268.23.

`p8` is **not** cosmetic. Two catalogue entries share the code
`ELKTRARACSARJUNITE` with different limits, and the endpoint validates the term
against the entry named in `p8`: 36 months under "Bisiklet Finansmanı" is fine,
the same code and term under "Elektrikli Araç Şarj Ünitesi Finansmanı" is a 400.
Send the `MaturityTerm` belonging to the entry, not `MaturityTermMax`.

Out-of-range input gives a **400 with a usable message**, e.g.
`{"Message":[{"PropertyName":"MaturityTerm","ErrorMessage":"Lütfen 31 değerine
eşit ya da daha büyük bir değer giriniz.","AttemptedValue":12}]}`.

### 3. First-instalment date window

`GET …7818136187BFD2CBB7EA0C5E2036888A&p1=<ProductCode>` →
`{MaxFirstInstallmentDate, MinFirstInstallmentDate}`. Only needed to validate
`p7` above.

**Only `IHTIYACKART` supports it.** Every other product answers 404 with an
empty body, which means "this product has no choosable first instalment" — not
an outage. A health check must not treat that 404 as a failure.

### 4. Kâr payı

```
POST …1E32FE5C30C44BF2B51A08D1756ADEEB
{"i":false,"p1":"100000","p2":"2","p3":"12","p4":"0",
 "p5":"","p9":"Katılma Hesabı","p10":false}
```

`p1` amount · `p2` **ProductGroup** (2 = katılma, 3 = ara dönem) · `p3` term ·
`p4` **FEC** currency (`0` TL, `1` USD, `19` EUR, `24` altın) · `p5` ProductCode
(blank for plain Katılma Hesabı) · `p9` title · `p10` true = `p3` is **days**,
false = months.

Response: `ProfitShareRatio`, `GrossProfitShare`, `NetProfitShare`,
`GrossProfitShareYearly`, `NetProfitShareYearly`, `ProductCode`, `SegmentCode`,
`SegmentName`.

Group and currency must be consistent with the catalogue or the response comes
back **all zeros rather than an error** — Hoş Geldin with `p4=19` returns zeros
because that product is TL-only. A health check has to treat an all-zero
response as a failure.

**Day mode is the norm, and the catalogue lies about it.** Ara Dönem declares
`MaturityType=Month` yet answers only in days, and only on exact 30-day
multiples: 30, 90 and 180 return a rate while 31 and 365 return zeros. Dijital
Katılma rejects anything under 31 days. Altına Altın needs ≥ 92. When in doubt,
send days.

Which term to send, per account type:

| account | code | currency | term |
|---|---|---|---|
| Katılma Hesabı | — | TL/USD/EUR | 31 gün |
| Dijital Katılma | `KTDIJITALHESAP` | TL/USD/EUR | ≥ 31 gün |
| Hoş Geldin | `HOSGELDIN` | TL only | 2–91 gün |
| Altına Altın | — | FEC 24, gram | 92–363 gün |
| Ara Dönem | — | TL/USD/EUR, group 3 | 30/90/180 gün |
| Sepet | `SEPET` | TL only | 2–180 gün |
| Yuvam | `YUVAMKATILMA` | USD/EUR/GBP | **no rate published** |

**Yuvam returns zeros for every currency, term and payload shape** — and so does
the bank's own page, so this is theirs, not ours. Treat it as unavailable rather
than retrying it as a bug.

Verified: 100 000 TL 12 ay → ratio 86.00, net 831.06. Hoş Geldin 31 gün
(`p10=true`) → ratio 98.00, net 2 525.77. Sepet 32 gün → ratio 90.00. Ara Dönem
20 000 TL 30 gün → ratio 92.00, net 506.43.

### 5. FX and precious metals

`GET …C24AD4C0FDA76C73081889B634A8C039` → 27 rows of
`{Title, CurrencyCode, CurrencyDescription, BuyRate, SellRate, ChangeRate}`.
Covers USD, EUR, GBP, CHF, SAR, KWD, AED, QAR, JPY, RUB, CNY/CNH, and the metals
`ALT (gr)`, `GMS (gr)`, `PLT (gr)`, `PLD (gr)`, `CAG (gr)`, `ZCeyrek`.

### 6. Card instalment

```
POST …AD36E047B34B678B1F7A995EA1821ABB
{"p1":10000,"p2":6,"p3":8,"p4":0,"p5":"SK","p6":"Sağlam Kart Troy"}
```

`p1` amount · `p2` instalment count · `p3` ProductType · `p5` ProductCode ·
`p6` title. Response: `ProfitRate`, `FirstInstallementAmount`,
`TotalDelayInterestAmount`, `ReceiveGoldPoint`.

Cards: `SK` Sağlam Kart Troy (type 8), `BP` Sağlam Business (3), `TK` Sağlam
Tohum (5), `KK` Sağlam Business Finansman (7).

The catalogue over-promises the instalment count: Sağlam Kart Troy declares
`MaxInstallmentValue=12`, but 12 is a **404 with an empty body** and 9 is the
real maximum. Step the count down until it answers rather than trusting the
declared limit.

### 7. Leasing

```
GET …E0B44AB4046932FB8BFDE1008D75818F
    &p1=100000&p2=0&p3=1&p4=10000&p5=12&p6=30&p7=0&p8=TL&p9=false
```

`p1` goods amount · `p4` down payment · `p5` term · **`p2` currency (FEC 0 TL /
1 USD / 19 EUR)** · `p6` KDV · `p8` display title. Response:
`InstallmentAmount`, `TotalAmount`, `MontlyProfitRate`,
`InstallmentCalculations[]`.

Currency is `p2`, not `p8`. Changing only the `p8` title returns byte-identical
numbers for all three currencies — which looks like a passing test while nothing
is actually varying. With `p2`: TL 9 678.36 / USD 7 937.00 / EUR 7 921.54.

## Verification

`python docs/discovery/verify_kuveytturk.py` calls every endpoint for every
product the catalogue declares — 19 finansman products, 7 account types across
their currencies, 5 cards, 3 leasing currencies, the rate feed.

**53/53 pass**, plus 3 known bank-side gaps (Yuvam, above). Checks assert the
contract — field present, type right, value in a sane range — never an exact
number, since rates change daily and that change is not a failure.

## Notes

- The hashes are stable across sessions but are page-embedded. Re-extract them
  from the page when a health check starts failing rather than treating a 404 as
  the bank being down.
- The catalogue endpoint (#1) is the discovery key for this bank: one URL,
  five `p1` values, and it hands over every product code the other six need.
