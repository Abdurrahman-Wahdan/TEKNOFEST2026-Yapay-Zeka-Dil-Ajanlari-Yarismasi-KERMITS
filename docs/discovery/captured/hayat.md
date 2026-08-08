# Hayat Finans — endpoint inventory

Captured 2026-08-08. Platform: Next.js. Host **`hayatfinans.com.tr`** — the bank
list's `hayatfinans.com` resolves but `www.hayatfinans.com` does not, and the
apex fails TLS. **10/10 verified**, 2 known gaps.

The friendliest contract of the ten: clean JSON APIs, no token, no session, no
WAF, no formatted strings.

## Endpoints

| what | endpoint |
|---|---|
| kâr payı | `POST /api/integration/calculateprofitsharerate` |
| döviz kurları | `GET /api/integration/fxrate` |
| finansman | `POST /api/integration/calculateloansproduct` — see below |

```
POST /api/integration/calculateprofitsharerate
Content-Type: application/json
{"AccountType":1,"Maturity":1,"ProductGroup":2,"Money":100000,"FEC":0,"MaturityTerm":32}
```

Response:

```json
{"data":{"grossProfitShare":3670.43,"grossProfitShareYearly":41.87,
         "netProfitShareYearly":34.54,"netProfitShare":3028.11},
 "isSuccessful":true,"error":null}
```

## What is actually offered

Much narrower than the parameter names suggest. The homepage calculator has
**no currency selector and no term selector**, and an API sweep agrees:

- **TL only.** `FEC` 1 (USD) and 19 (EUR) return empty data.
- **32–33 days only.** Every other `MaturityTerm` returns empty data.
- `Maturity` is accepted but ignored — 1 through 6 give identical results.
- `AccountType` is the real dimension: 1, 2 and 3 price differently
  (net 3 028,11 / 79,95 / 2 748,44 on 100 000 TL).

**Minimum balance is exactly 50 000 TL.** At 49 999 the endpoint returns zeros;
at 50 000 it prices. No error either way — so a caller that skips the minimum
check will tell a user their profit is "0 TL" instead of "this account needs
50 000 TL to open".

## Finansman

`calculateloansproduct` exists and responds, but rejects every payload shape
tried with `400 {"message":"API Failure"}`. There is **no loan calculator on the
public site** to observe a working request from, so its contract is unknown.
Recorded as unknown rather than guessed at.

## FX

`GET /api/integration/fxrate` → `data[]` of
`{currencyShortCode, currencyDescription, currencyBid, currencyAsk,
effectiveBid, effectiveAsk, fec…}` for USD, EUR, GBP and one more. Rates only —
no conversion endpoint, so converting means multiplying.
