# Türkiye Finans — endpoint inventory

Captured 2026-08-08. Platform: SharePoint. Host `www.turkiyefinans.com.tr`.
**23/23 verified**, 4 known gaps.

## The scope problem — read this first

**Türkiye Finans publishes tables, not answers — on the wire.**

Its calculators fetch a rate-and-fee table once on page load and then do the
instalment arithmetic **in the browser**. Filling the form and pressing Hesapla
fires no further request — there is no computed figure to read back from a
service call.

So this bank can tell us, live and exactly:

- which finance products exist, their ids, allocation fee and BSMV rate
- the kâr payı rate table: gross annual ratio per currency and term band, with
  minimum amounts and maximum day counts
- which currencies participate

but it will **not** tell us a monthly instalment over the wire. Added
2026-08-16: for financing, that instalment is now computed anyway — not
invented, *ported*. `turkiyefinans.modules.min.js` (fetched live from
`/SiteAssets/js/turkiyefinans.modules.min.js`) contains
`creditInstallmentResult`, the exact function the calculator page runs after
Hesapla. It is a plain annuity over the published monthly rate, KKDF ("Rusf"
in the payload) and BSMV — no hidden inputs, nothing the page keeps server-
side. `banks/providers/turkiyefinans.py::_installment_plan` is a line-by-line
port of it, and `finance_quote` returns the result with `derived=True` — the
second agreed exception to "never compute a bank's figure ourselves" (the
first is `BaseBank.convert_from_rates`).

This does **not** extend to card instalments. `taksitle-hesaplama-araci.aspx`
runs a *different* client-side annuity (`installments.js`, under
`/SiteAssets/js/jquery/taksitle/`) that schedules against a real transaction
date and a statement cut-off day — `card_installment_quote` has no date input
to feed it, so it still returns the published rate with `installment=None`.

Everything else about the bank is easy: plain `GET`s, JSON, no token, no
session, no WAF.

## Endpoints

Base: `/_vti_bin/TurkiyeFinansServices/FrontEndService.svc/`

| what | method |
|---|---|
| finance product table | `GET GetFinanceCalculatorCreditTypeItems` |
| kâr payı rate table | `GET GetKarPayiHesaplama/<AccountGroupType>/<Bireysel\|Ticari>` |
| currencies | `GET GetParticipationCurrencyTypeItems` |

## Finance product table

18 rows. Each carries `CreditID`, `Code`, `AllocationFee` (0.00575 = %0,575),
`Bitt` (BSMV, 0.15), `Calculate`, plus HTML disclaimer blocks.

Products: `ihtiyac_kredisi` (1, 999) · `tasit_kredisi_0_km` (14) ·
`2el_tasit_finansmani` (120) · `sigortasiz_2_EL_tasit_finansmani` (121) ·
`sigortali_tasit_finansmani` (122) · `sigortali_motosiklet_finansmani` (1000) ·
`sigortasiz_motosiklet_finansmani` (1001) · `konut_kredisi` (16, 116) ·
`konut_kredisi_sigortali` (115, 118) · `arsa_kredisi` (17, 540) ·
`isyeri_kredisi` (18, 550) · `banka_gayrimenkulleri_konut_finansmani` (102) ·
`banka_gayrimenkulleri_ticari_mulk` (105).

The same `Code` appears under several `CreditID`s with different fee structures
(`arsa_kredisi` 17 has no allocation fee, 540 charges 0.00575). **`CreditID` is
the identity, not `Code`.**

## Kâr payı rate table

`GetKarPayiHesaplama/<group>/<customer>` returns rows of
`{AccountGroupType, Currency, CurrencyTypeId, CustomerType, AnnuallyGrossRatio,
MinimumAmount, MaximumAmount, MinimumDueDay, MaximumDueDay}`.

- group `4` Bireysel — 5 rows (e.g. TL %40,75, min 10 000, ≤31 gün)
- group `1` Bireysel and Ticari — 25 rows each (e.g. TL %28,80, min 250, ≤91 gün)
- group `4` Ticari, and group `2` either side — **no table published**

Currencies: TL, USD, EUR, YAU (altın), YAG (gümüş).

## Conversion — added 2026-08-15

`GetExchangeRates` (above, under "rates") is a full board but there is no
converter endpoint behind it — this bank's calculator does its arithmetic
entirely in the browser, same as its finance products. `convert` derives from
the published board via `BaseBank.convert_from_rates`, marked `derived=True`.
No new endpoint was found or needed; this bank was simply never asked for
`convert` before.

## Card instalments — found 2026-08-15

`/tr-tr/hesaplama-araclari/Sayfalar/taksitle-hesaplama-araci.aspx` is a real
card instalment calculator, previously unread. Same scope problem as
financing: `installments.js` runs the whole annuity client-side from a rate
this page renders server-side into a disabled `<input id="txtTaksitleKarPayi"
value="4.25">` — one flat rate for every TF card (Paraf, Happy Kart alike;
the page has no card-type selector) — plus `KKDF`/`BSMV` from
`GET FrontEndService.svc/GetKKDFandBSMVRate` (`{"BSMV":"0.05","KKDF":"0.15"}`,
confirmed live but not surfaced — `CardInstallmentQuote` has no field for
fees the way `FinanceQuote` does).

There is no service call behind the rate itself; it is scraped off the HTML
the same way Hayat's account types are parsed off its homepage. The
instalment slider on the page runs 2–12, which is `min_term`/`max_term` on
the one card `Product` this bank publishes.

`card_installment_quote` returns the published rate with `installment=None`
and `total=None`, the identical contract as `finance_quote` for this bank.
