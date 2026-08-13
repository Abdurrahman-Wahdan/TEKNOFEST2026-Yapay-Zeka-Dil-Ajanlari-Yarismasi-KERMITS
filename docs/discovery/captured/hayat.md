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
- `Maturity` is accepted but ignored — 1 through 6 give identical results.
- `AccountType` is the real dimension, and the values are **0, 1 and 2**, which
  is what the homepage's own `accountType` field carries — not the option
  `value`, which is one higher. 0 Katılma Hesabı · 1 Avantajlı Hesap ·
  2 Avantajlı Günlük Hesap. Anything above 2 falls back to 0.

**Correction, measured 2026-08-08: the term range is per account type, not a
flat 32–33 days.** The original sweep tested `AccountType` 1 only and generalised
from it.

| account | AccountType | terms it prices |
|---|---|---|
| Katılma Hesabı | 0 | 32, 60, 90, 365 — a real term curve |
| Avantajlı Hesap | 1 | 32 only; 60, 90 and 365 answer 400 |
| Avantajlı Günlük Hesap | 2 | **term-independent** — 32, 60, 90 and 365 all return 79,95 TL on 100 000 |

The third is the trap: 79,95 TL is *one day's* profit at its own stated 29,18%
annual rate, returned unchanged whatever `MaturityTerm` is sent. Passing it
through as a term quote would report "365 gün için 79,95 TL". Check the returned
profit against the returned annual rate over the requested term before believing
it.

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
