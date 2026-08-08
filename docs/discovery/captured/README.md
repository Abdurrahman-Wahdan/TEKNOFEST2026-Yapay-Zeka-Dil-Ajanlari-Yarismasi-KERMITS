# Bank endpoint discovery — all ten banks

State as of 2026-08-08. Every bank in `example.bank.list.txt` has been probed,
its endpoints recorded, and every endpoint called over plain HTTP with no
browser. Per-bank contracts are in the sibling files; raw captures are the
`*_full.json` files.

## Result

| bank | platform | endpoints | verified | transport |
|---|---|---|---|---|
| [Kuveyt Türk](kuveytturk.md) | Magiclick | 7 | **53/53** | httpx |
| [Albaraka](albaraka.md) | Unigate | 4 | **35/35** | curl_cffi (WAF) |
| [Vakıf](vakif.md) | Unigate | 9 | **35/35** | httpx + CSRF token |
| [Emlak](emlak.md) | Unigate | 3 | **31/31** | curl_cffi (WAF) |
| [Dünya](dunya.md) | ASP.NET Core | 6 | **42/42** | httpx + CSRF token |
| [Ziraat](ziraat.md) | Drupal | 3 | **36/36** | httpx (finansman only) |
| [Türkiye Finans](turkiyefinans.md) | SharePoint | 3 | **23/23** | httpx |
| [Hayat Finans](hayat.md) | Next.js | 3 | **10/10** | httpx |
| **T.O.M.** | — | 1, authenticated | — | credentials required |
| **Adil** | — | **none** | — | — |

**265 checks, all passing.** Each check asserts the contract — field present,
type right, value in a sane range — never an exact number, because rates change
daily and that change is not a failure.

## The two banks with nothing to call

**Adil Katılım** has no calculator: zero inputs, zero selects and zero backend
calls on the whole site. Confirmed on the correct domain
(`adilkatilim.com.tr`) — the earlier finding was made against a domain that does
not resolve, so it was re-checked rather than trusted.

**T.O.M.** has no public calculator either. Its pages are static. The loan API
at `webintegration.tombank.com.tr/webintegration/api/LoanCalculation/GetLoanPayBackPlan`
is live but answers `401 Unauthorized: Invalid credentials` to every payload, so
it is unusable without a partner credential.

## Three domains in the bank list are wrong

Resolve before probing. A dead domain looks exactly like a bank with no
calculator — this cost real time.

| list says | actually |
|---|---|
| `adilkatilim.com` | `adilkatilim.com.tr` (`.com` is NXDOMAIN) |
| `dunyakatilim.com` | `dunyakatilim.com.tr` (`.com` is NXDOMAIN) |
| `www.hayatfinans.com` | `hayatfinans.com.tr` (the `www` host does not resolve) |
| `albarakaturk.com` | redirects to `albaraka.com.tr` |
| `emlakbank.com.tr` | `emlakkatilim.com.tr` |

## What "no data" looks like — four different shapes

Not one of these is an HTTP error. Every bank that has a gap expresses it
differently, and a health check that only looks at the status code will call all
four of them healthy:

1. **200 with all-zero fields** — Kuveyt Türk (Yuvam), Albaraka (Kur Korumalı,
   gold), Emlak (gold at 12+ ay), Hayat (below the 50 000 TL minimum).
2. **200 with an empty body** — Vakıf, gold beyond one year.
3. **200 with an `errorMessage` inside the JSON** — Vakıf, Dünya.
4. **404 with an empty body** — Kuveyt Türk's first-instalment date service for
   products that do not offer one, and its card endpoint above the real (not the
   declared) instalment maximum.

Only Dünya returns a genuinely useful message
(`ProductMaturitySettings (…) mevcut değil`).

**Zero is never a price.** Every gap recorded in these files was confirmed
against the bank's own page before being written down as theirs rather than
ours.

## Where product catalogues live

There is no common answer, and this is the single biggest per-bank difference:

- **an endpoint** — Kuveyt Türk (one URL, five `p1` values), Ziraat
  (`get-vade` per product), Türkiye Finans (a table service)
- **embedded in the page HTML** — Albaraka and Dünya (JSON inside
  single-quoted, HTML-escaped `<option value='…'>`), Emlak, Vakıf (plain option
  values)

Two banks require the catalogue entry to be **echoed back verbatim** as a
request parameter: Albaraka's `FinanceType` and Dünya's product/category pair.
Do not try to rebuild those blobs field by field.

## Running the checks

```
python docs/discovery/verify_<bank>.py
```

Exit code is non-zero if any check fails. Known bank-side gaps are printed but
do not fail the run.
