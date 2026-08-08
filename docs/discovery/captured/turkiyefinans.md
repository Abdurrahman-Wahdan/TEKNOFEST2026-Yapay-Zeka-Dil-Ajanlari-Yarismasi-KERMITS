# Türkiye Finans — endpoint inventory

Captured 2026-08-08. Platform: SharePoint. Host `www.turkiyefinans.com.tr`.
**23/23 verified**, 4 known gaps.

## The scope problem — read this first

**Türkiye Finans publishes tables, not answers.**

Its calculators fetch a rate-and-fee table once on page load and then do the
instalment arithmetic **in the browser**. Filling the form and pressing Hesapla
fires no further request — there is no computed figure to read back.

So this bank can tell us, live and exactly:

- which finance products exist, their ids, allocation fee and BSMV rate
- the kâr payı rate table: gross annual ratio per currency and term band, with
  minimum amounts and maximum day counts
- which currencies participate

but it will **not** tell us a monthly instalment. Answering "what is my payment"
for this bank means running the annuity ourselves, which the project rule
forbids. That is a decision for the owner, so nothing here computes anything.

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
