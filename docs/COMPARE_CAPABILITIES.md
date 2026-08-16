# What the live endpoints actually support

Measured, not documented: every figure below came from calling the `banks/` layer
against the real bank endpoints on **2026-08-14**. Where a bank refused, the refusal
text is the bank's own, reported through `UnsupportedProduct`.

This exists because the comparison page has to know, *before* it asks, what each bank
will accept — a term it will not price, a currency it does not offer, an amount outside
its band. Asking anyway produces the screen where all six banks decline and the user
learns nothing.

---

## 1. Capability matrix

| bank | products | finance | profit_share | rates | convert | card | mile_rates |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| kuveytturk | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| albaraka | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| vakif | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| emlak | ✓ | ✓ | ✓ | ✓² | ✓² | — | — |
| dunya | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| ziraat | ✓ | ✓ | ✓⁴ | — | — | — | — |
| tom | ✓ | ✓ | — | — | — | — | — |
| hayat | ✓ | — | ✓ | ✓ | ✓ | — | — |
| turkiyefinans | ✓ | ✓¹ | — | ✓ | ✓² | ✓³ | — |
| adil | — | — | — | — | — | — | — |

The `rates` and `convert` columns above are re-verified live as of **2026-08-15**
(`python -m banks`, 39/40 ok — see "Emlak and Türkiye Finans, completed" below):
`vakif` and `dunya` were shown as `rates: —` here from before the earlier
FX-board session found them; that was already wrong and is corrected in place
rather than left inconsistent with the rest of the table.

¹ **Rate, plus a computed payment (updated 2026-08-16).** `turkiyefinans` publishes an
18-product finance catalogue with a real monthly profit rate per term band, an annual
cost rate, an allocation fee and BSMV. Its own calculator turns that rate into an
instalment client-side (`creditInstallmentResult` in `turkiyefinans.modules.min.js`) and
never sends the result back over the wire, so `finance_quote` now ports that exact
function (`_installment_plan`) instead of leaving the payment null. `installment` and
`total` come back real, `derived=True` marks them as computed rather than read off the
wire, and the UI shows the same "computed" label `convert`'s derived rows already use.
Card instalments are unaffected — a different, date-dependent calculator, still null.
Its 55-row profit-share table is a ratio only, so `profit_share` stays out.

² **Derived, not server-side.** `emlak`'s rates are scraped off its own
`/tr/tum-kurlarimiz` page (23 instruments, server-rendered, no endpoint behind
it — its one JSON candidate, `services/api/CurrencyTypes/GetFxRatesAll`, is
commented out of the site's own bundle and answers 404 when called directly).
`emlak convert` and `turkiyefinans convert` both derive from their own
published rates through `BaseBank.convert_from_rates` — the same agreed
exception already used by Kuveyt Türk and Hayat — because neither bank has a
converter endpoint of its own. Every row from these two is marked `derived`.

---

## 2. Finance — the limits that matter

Every finance product declares `max_term`; most declare amount bounds. This is what makes
constrained inputs possible.

| bank | products | declares max_term | declares max_amount |
|---|:-:|:-:|:-:|
| kuveytturk | 19 | 19/19 | 18/19 |
| ziraat | 17 | 17/17 | 17/17 |
| albaraka | 16 | 16/16 | 16/16 |
| vakif | 7 | 7/7 | 0/7 |
| dunya | 6 | 6/6 | 6/6 |
| emlak | 4 | 4/4 | 0/4 |
| tom | 1 | 1/1 | 0/1 |

### Live proof: `konut-yeni`, 1.000.000 TL

| bank | 120 months | 360 months |
|---|---|---|
| **albaraka** | **29.998,32 ₺** · 2,90% · yıllık 42,7 | no instalment plan returned |
| kuveytturk | 30.797,66 ₺ · 2,99% · yıllık 44,36 | runs to 120 months |
| vakif | 30.797,66 ₺ · 2,99% · yıllık — | runs to 120 months |
| ziraat | 32.654,09 ₺ · 3,19% · yıllık — | no band covering 1M over 360 |
| emlak | 34.532,12 ₺ · 3,39% · yıllık 50,52 | runs to 120 months |
| **dunya** | **declined — runs to 84 months** | runs to 84 months |

Two consequences for the UI:

1. **The term range is the intersection of the selected banks.** Pick Dünya and the
   comparable ceiling drops from 120 to 84. The form must cap there and name the bank
   that caused it, rather than letting the user ask for something guaranteed to fail.
2. **`annual_cost_rate` is null at Vakıf and Ziraat.** A "yıllık maliyet" column must
   render an em dash for them, and must never be used as the default sort — it would
   rank two banks as worst for not publishing a number.

---

## 3. Profit share — three traps

### Term unit is not cosmetic

Same account, 100.000 TL, "3 months" vs "92 days":

| bank | 92 gün | 3 ay | unit the bank answered in |
|---|---|---|---|
| kuveytturk | 7.330,55 | 7.006,32 | day (both) |
| **albaraka** | 7.373,71 | **6.612,49** | day / **month** |
| vakif | 6.730,38 | 6.730,38 | day (both) |
| emlak | 6.941,01 | 6.941,01 | day (both) |
| dunya | 7.034,47 | 7.034,47 | day (both) |
| hayat | 8.186,32 | 8.022,05 | day (both) |

Albaraka is the only bank that genuinely honours "month", and it returns ~10% less than
its own 92-day answer. **A comparison that mixes units is not a comparison** — the unit
has to be pinned for every bank in the run, and the answered `term_unit` shown.

### Currency support, live-verified (100.000 / 92 gün)

| currency | banks that priced it |
|---|---|
| TRY | kuveytturk, albaraka, vakif, emlak, dunya, hayat — **6** |
| USD | kuveytturk, albaraka, vakif, emlak, dunya — **5** (hayat: TRY only) |
| XAU (gold) | **vakif, emlak only — 2** |

A gold participation comparison is a two-bank comparison. The currency selector must
show that before the user runs it.

### Per-product constraints

- `hayat` — TRY only, **min 50.000 TL**, min term 32 days.
- `kuveytturk / ALTINAALTINKATILMAHESABI` — XAU, amount **50–3000 grams**, term 92–363.
- `kuveytturk / HOSGELDIN` — 10.000–5.000.000 TL, term 2–91.
- `dunya / GNSHSP` — term is exactly **1**.
- `dunya / ALTKTLMHSP`, term 31–366, XAU only.
- `emlak / KATILMA` — the widest currency set: TL, USD, EUR, **ALT (gr)**, **GMS (gr)** (silver).

---

## 4. Rates — what is actually comparable

Three banks publish a feed. Instruments differ enormously.

| bank | count | instruments | freshness |
|---|:-:|---|---|
| kuveytturk | **27** | TL, USD, EUR, GBP, CHF, JPY, AUD, CAD, DKK, SEK, NOK, SAR, AED, QAR, KWD, BHD, RUB, CNY, CNH, MYR + **ALT/GMS/PLT/PLD/CAG (gram)** + **ZCeyrek (coin)** + **EUR/USD parity** | **no timestamp at all** |
| albaraka | 4 | USD, EUR, GBP, **XAU (gram)** | `14.08.2026-21:49` — real time |
| hayat | 4 | USD, EUR, GBP, **ALT (gr)** | `2026-08-14` — date only |

### Categories the UI should use

- **Döviz** (`unit: "1"`) — currencies quoted against TRY.
- **Kıymetli maden** (`unit: "gram"`) — Altın, Gümüş, Platin, Paladyum, CAG.
- **Sikke** (`unit: "coin"`) — ZCeyrek. Never ranked against gram prices.
- **Parite** — `EUR/USD` is a **cross rate, not a TRY price**. It sits in the same feed
  and will silently poison any "cheapest USD" logic that filters by code alone.

### Cross-bank comparable set

Only **USD, EUR, GBP and gold-per-gram** are quoted by ≥2 banks. Everything else is
Kuveyt Türk alone and must render as a single-source figure, not a ranking.

**Gold needs aliasing:** Albaraka calls it `XAU`, Kuveyt Türk and Hayat call it
`ALT (gr)`. `RATE_ALIASES` in `banks/providers/base.py` maps these; without exposing a
`canonical` field the frontend would have to duplicate that table.

### Spread sanity

Some Kuveyt Türk rows carry implausible spreads — `MYR` 7,59/15,78, `CNH` 4,60/9,56,
`BHD` 82,29/170,99, `CAG` 4.152/9.697. A "narrowest spread" ranking would surface these
as headline results. Any spread display needs an outlier guard.

---

## 5. Convert (1000 USD → TRY, live)

| bank | result | rate | derived |
|---|---|---|:-:|
| dunya | 47.724,11 | 47,72411 | server-side |
| vakif | 47.448,27 | 47,44827 | server-side |
| albaraka | 47.442 | 47,442 | server-side |
| hayat | 47.071,07 | 47,07107 | **we computed it** |
| kuveytturk | 47.031,08 | 47,03108 | **we computed it** |

The two `derived` rows are our own multiplication of a published rate, not the bank's
conversion. They must be labelled — see the `derived` contract on `ConversionOut`.

---

## 6. Card & miles

- **kuveytturk** — 5 cards, quote works (10.000 TL / 6 ay → 1.900,64 ₺, 2,99%).
  Reachable by name (see bug 2 below for the duplicate code).
- **vakif** — 1 card `FK` (10.000 TL / 6 ay → 1.947,08 ₺, 4,30%).
- **turkiyefinans** — found 2026-08-15, previously unread. One flat rate for
  every TF card (Paraf, Happy Kart alike — the calculator has no card-type
  selector). Same contract as its `finance_quote`: `installments.js` runs the
  whole instalment annuity in the browser from a rate this page renders
  server-side into a disabled `<input>` (`txtTaksitleKarPayi`, e.g. 4,25%,
  2–12 instalments); there is no service call behind it, so the rate is
  scraped off the HTML the same way Hayat's account types are, and
  `installment`/`total` come back null rather than an invented figure.
- **mile_rates** — kuveytturk only, **567 rows** (card × tier × category). Unusable as a
  flat table; needs filtering by card and category.

³ **Rate, plus a computed payment — card (updated 2026-08-16).** Same footnote
as ¹, same bank, and the same resolution: `card_installment_quote` returns a
real `installment`/`total` now. Its calculator (`installments.js`) is
date-dependent in a way `finance_quote`'s is not, but anchoring the
transaction to the statement date itself erases that dependence rather than
working around it — every ordinary statement date then answers identically,
confirmed live. See `_card_installment_plan` in `turkiyefinans.py`.

⁴ **A capability found by driving the widget, not guessing at names (added
2026-08-16).** See "Ziraat is reachable after all — and, corrected again
2026-08-16, actually priced" below.

### Card — auditing the remaining banks (2026-08-15)

Only kuveytturk and vakif were wired up; the other eight were re-checked by
hand rather than trusted from an earlier pass, the same rigor as the FX audit:

- **turkiyefinans** — a real gap, closed (above).
- **albaraka, dunya, emlak, hayat, ziraat** — each has a "kartlar" product
  page; none has a calculator, a form, or a JS-visible AJAX/service call for
  instalments. Emlak's card page does carry a rate table, but it is the
  BDDK-mandated *maximum late-payment penalty* disclosure (`Aylık Akdi Kar
  Payı Oranı: %0`, then tiered penalty ceilings by outstanding balance) — not
  an instalment rate, and not something a purchase is priced against.
- **tom** — its calculator page (`hesaplama-araclari.html`) loads exactly the
  two scripts already known (`calculation-tool-dynamic.js` for the loan,
  `calculation-tool-kp.js` for kâr payı); no third script and no card link
  anywhere on the site.
- **adil** — not re-probed; already established as zero inputs, zero selects,
  zero backend calls across the whole site, and a card calculator would be
  none of those things either.

Net: **one bank added (`turkiyefinans`, rate-only)**, five genuine "nothing
published" findings each checked against the bank's own calculator page and
scripts rather than assumed, and one dead end (Emlak's rate table) that reads
like a match by name but is a different disclosure entirely.

### Card — the comparison mechanism was never really comparing

`GET /banks/{bank}/card` always existed as a single-bank, single-card quote,
and the compare page's Card category called only that: pick one bank, pick
one card, press Compare, get the one row back. There was no `banks/compare.py`
function and no `/compare/card` route — every other category (`finance`,
`profit_share`, `exchange`) has both. Cards have no cross-bank family the way
finance products do (no shared taxonomy — each bank names its own catalogue),
so `compare.card(amount, installments, banks=None)` asks every in-scope bank
for its whole `products("card")` catalogue and quotes every card in it, the
same "one row per real thing sold" shape `finance` gets from the family
table, built from each bank's own catalogue instead. A rate-only row (now
turkiyefinans) sorts and sinks exactly like a rate-only finance row — `_ranked`
already handled a None sort key generically, from the finance work.

Live proof, 10.000 TL / 6 taksit, no bank filter: **7 rows across 3 banks**,
0 unavailable — kuveytturk's five cards (including both `BP`-coded ones,
resolved by name), vakif's Ferah Kart, and turkiyefinans's rate-only row
sinking to the bottom of the ranking as expected.

---

## 7. Bugs found

1. ~~**`tom` is in no finance family**~~ — **fixed.** Listed under `ihtiyac` with its one
   product `TKTCDGRFNS`. `tests/unit/test_families_coverage.py` now checks the reverse
   direction, so a capable bank in no family fails the build.
2. **Duplicate card code `BP`** at Kuveyt Türk (above). Still open.
3. **Duplicate finance code `ELKTRARACSARJUNITE`** at Kuveyt Türk — the same code names
   two different products, *Bisiklet Finansmanı* and *Elektrikli Araç Şarj Ünitesi
   Finansmanı*. Same class as the `BP` bug: a caller quoting the code gets whichever the
   catalogue lists first. The bank's own data; not resolvable here. Still open.
4. ~~**`turkiyefinans` looks comparable and is not**~~ — **fixed** by making it comparable
   on what it does publish. See ¹ above.
5. ~~**The two konut axes were mapped onto each other**~~ — **fixed.** Albaraka's
   `YKKNT0B` ("İLK EVİM") was listed under `konut-yeni` and `VRKNT0B` ("2. VE SONRAKİ")
   under `konut-2el`, so a *first-home* loan was ranked against a *new-build* loan and
   called the same product. Both Albaraka products price identically today, which is why
   it was invisible. Now four families: `konut-yeni`/`konut-2el` (property condition) and
   `konut-ilk`/`konut-sonraki` (buyer's ownership).
6. ~~**`dunya` finance quotes left `annual_cost_rate` null and `schedule` empty**~~ —
   **fixed 2026-08-16.** Neither was missing from the bank; both were sitting unparsed
   in `paymentPlanHTML`, the same document `LoanCheckRate` already returns for the
   instalment and total. "Yıllık kar oranı" and a full 24-row instalment table were in
   that HTML the whole time — this was a parsing gap, not a data gap, and needed no
   computation to close. Found during an audit of every `None`/empty output field across
   all ten banks (prompted by the Türkiye Finans fix, ¹ above) for the same class of bug.
7. ~~**`tom` finance quotes left `annual_cost_rate` null on a wrong assumption**~~ —
   **fixed 2026-08-16.** The code claimed T.O.M. "publishes no annual cost rate" and that
   `MonthlyCostRate` was "a total over the term, not an APR." Both were wrong: a live quote
   carries `TotalCost` (83.4614766) alongside `MonthlyCostRate` (5.187...), and
   `(1 + MonthlyCostRate/100) ** 12 - 1 = 83.46148` — `TotalCost` *is* the bank's own
   annualisation of `MonthlyCostRate`, sitting in the same response, unread. Found in the
   same audit as #6.
8. **Every other `None`/empty output field, checked and closed out.** The same audit
   covered `annual_cost_rate` at Vakıf and Ziraat and `ratio` on every profit-share bank
   (Albaraka, Dünya, Emlak, Hayat, Vakıf). All five were checked against a **live** full
   response dump (not the cached fixtures) — Vakıf's finance calculator was checked
   against its complete AJAX plugin registry (`config.min.js`, every endpoint the site's
   own widgets can call), and Ziraat's against the full text of a live payment-plan
   response. None of the seven carries the missing figure anywhere: no APR field, no
   distinct participation-ratio field, no undiscovered endpoint. Left `None`, correctly.

---

## 8. How families are kept honest

`banks/taxonomy.py` decomposes a product name into the five axes banks actually differ on
— purpose, condition, ownership, usage, insurance — and `families.uncovered()` runs it
over a checked-in snapshot of every catalogue
(`tests/fixtures/banks/catalogues.json`, refreshed by `scripts/refresh_catalogues.py`).

A product two or more banks sell that no family covers fails the build. That check found
Türkiye Finans' 18 unmapped products and three families that had quietly reached a second
bank: **motosiklet** (albaraka + turkiyefinans), **tasit-dijital** (albaraka +
kuveytturk) and **ihtiyac-kart** (albaraka + kuveytturk).

Finance families went from **10 to 15**, and the banks reachable in a finance comparison
from **7 to 8**.

### The bank-by-bank sweep

Every finance product at every bank was then quoted inside its own declared bounds:
**86 of 88 answered**. The two that did not are Kuveyt Türk's `ELKTRARACSARJUNITE`, a
single code naming two different products — both are reachable by name, so nothing is lost,
but the code is ambiguous (bug 3 above).

The sweep found two products that existed, priced better than their bank's listed one, and
were never quoted because the family named only the other:

| Bank | Listed | Also sells | Effect |
|---|---|---|---|
| ziraat | İHTIYAÇ FINANSMANI 4,99 | KOLAY FON 4,19–4,39 | quoted at its dearest rate |
| ziraat | KONUT FINANSMANI 3,19 | KONUT KAMPANYA PAKETI **2,89** | **cost it first place** in konut |

Both are now listed with `variant="kampanya"`. Ziraat's campaign package is the cheapest
`konut-yeni` quote in the system; before this it never appeared at all.

### Comparison types

All seven capabilities are reachable: `finance`, `profit_share`, `rates`, `convert`,
`card`, `mile_rates` (and `products` as the catalogue behind them). The gap was **inside**
`profit_share`, which shipped one family while the banks sell eleven distinct accounts:

- **`katilma-altin` — added.** A dedicated gold account at Kuveyt Türk and Dünya, and three
  more banks whose single account takes XAU. Kuveyt Türk's gold account pays a **40%**
  ratio where its ordinary account pays **95%**, so pricing gold through `katilma` answered
  with a rate nobody opening that account would get.
- **`katilma-aradonem` — recorded, not shipped.** Albaraka and Kuveyt Türk both sell the
  interim-profit account; measured across every amount and term, **neither publishes a
  rate**. A family here could only ever answer with two refusals, so it is in
  `families.NOT_PRICED` with the reason.
- Eight others are genuinely single-bank (kur korumalı, dijital, hoş geldin, sepet, yuvam,
  güneş, avantajlı, avantajlı günlük) and are listed in `SINGLE_BANK_PROFIT_SHARE`.

4. **Currency codes were never canonicalised out of the catalogue.** Emlak reported `TL`,
   `ALT (gr)` and `GMS (gr)` where every other bank reports `TRY`, `XAU` and `XAG`, so the
   currency intersection in `banks/limits.py` came out **empty for every participation
   family** and the UI fell back to a hardcoded `["TRY"]`. No gold or foreign-currency
   participation comparison could be selected at all, and nothing failed. `banks.parse
   .canonical_currency` is now the one table, and `api.converters.canonical_code`
   delegates to it instead of keeping a second reversed copy.


---

## 9. Participation accounts — the same sweep

Every account at every bank, quoted in every currency it declares, at a term
inside its own band.

**18 products across 6 banks; 13 quote.** The five that do not are all the same
thing — *the bank publishes no rate for that product*, at any amount, term or
currency:

| Bank | Product | Why |
|---|---|---|
| albaraka | Ara Dönem Kâr Payı Ödemeli | no rate, any currency |
| albaraka | Kur Korumalı Katılma | no rate, all 5 currencies |
| kuveytturk | Ara Dönem Kâr Payı Ödemeli | no rate, any currency |
| kuveytturk | Yuvam TL Katılma | bank returns zeros on its own page too |
| hayat | Avantajlı Günlük Hesap | returns one day's profit for any term |

Albaraka also declares XAU on its ordinary account and publishes no gold rate
for it — the declaration is wider than the pricing.

### A contract check was refusing correct quotes

`_check_profit_share` compares the bank's profit against its own stated annual
rate and refuses a figure that does not follow. It caught Hayat's daily account
(one day's profit whatever term is sent) — and it was also refusing **correct**
gold quotes.

Banks publish the profit and the rate rounded to two decimals. On lira that is
noise; on gold it is the whole number. Vakıf's real 91-day profit on 100 grams
is **0,01 gram against an implied 0,0075** — a 34% *relative* error and a
0,0025 gram *absolute* one, i.e. exactly one rounding step. A purely relative
tolerance called that a contradiction.

Now the published precision is modelled: the rate is known to ±0,005, which
gives a range of possible profits, and the profit is itself rounded to ±0,005.
If those intervals touch, the figures agree. Hayat's account is unmoved —
68,55 against an implied 2.193,67, and 2.193,67 / 32 is exactly 68,55.

Effect: Vakıf gold and Emlak silver went from refused to quoting, and
`katilma-altin` gained the banks it had been silently losing.

### Ziraat is reachable after all — and, corrected again 2026-08-16, actually priced

`docs/discovery/captured/ziraat.md` recorded kâr payı as browser-only behind a
`493`. That was wrong, corrected 2026-08-15: the `493` comes from the
`ajax_form=1` parameter, not the endpoint. Without it the form processes, and
the field values were readable from the homepage rather than guessable (the form
id carries Ziraat's own typo, `kari_payi_hesapla_form`, which is why every
guessed `/ajax/` route missed).

Posted correctly, that endpoint (`/anasayfa?_wrapper_format=drupal_ajax`)
returns its four result fields and **every one is zero**, across all 5
maturities × TRY/USD/XAU, with the bank's own message that no profit share has
been distributed for those values — genuinely retrospective, reporting what
matured accounts were paid, not a forward quote. That conclusion stood until
2026-08-16 and was itself the mistake: **that endpoint is not the one the kâr
payı widget calls.** Driving the actual widget with Playwright — clicking
through it in a live browser and watching the network traffic fire, rather
than guessing at `/ajax/` names the way the first pass did — surfaced
`POST /ajax/karpayi-products`, a genuinely separate, genuinely forward-looking
endpoint that answers plain `curl` with real, amount- and day-scaled figures
(cross-checked against `kar-payilari`'s own published rate table and against
itself from 1 to 800+ days). `profit_share` is a real Ziraat capability now —
see `docs/discovery/captured/ziraat.md` for the full evidence and
`banks/providers/ziraat.py`. `karpayi_hesap_type=2` (ARA DÖNEM ÖDEMELİ) and
currency `XAU` were swept the same way and answered zero at every combination
tried, so those two stay excluded; `TRY`/`USD`/`EUR` × `KATILMA HESABI` do not.

`tom` publishes no participation calculator, `turkiyefinans` states a ratio only,
`adil` publishes nothing at all.

### Comparison types

Two families, and the count is right: `katilma` (7 banks, `ziraat` added
2026-08-16) and `katilma-altin` (2 dedicated accounts + 3 general). `katilma-aradonem` is sold by two banks and
priced by neither, so it stays in `NOT_PRICED`. The other eight accounts are
single-bank and listed in `SINGLE_BANK_PROFIT_SHARE`.

**11/11 family entries resolve live.**

## Converter — auditing the remaining banks (2026-08-15)

The FX board reads six banks; the converter read only five of those six —
`turkiyefinans` had `rates` but no `convert`. Four more banks (`adil`, `emlak`,
`tom`, `ziraat`) were in neither. Checked each one properly rather than
labelling it unreachable on the strength of an earlier pass:

- **`turkiyefinans`** already had a full rate board (`GetExchangeRates`) with
  nowhere to convert through — its own calculator does everything in the
  browser. `convert` now derives from that board (see footnote ² above).
- **`emlak` was a real gap.** Its site publishes a 23-instrument rates page,
  `/tr/tum-kurlarimiz`, server-rendered with no endpoint behind it — never
  read before this. Its own JavaScript bundle references a JSON endpoint,
  `services/api/CurrencyTypes/GetFxRatesAll`, sitting in a commented-out code
  path; called directly it answers a clean 404, confirming the route was
  decommissioned rather than merely unlinked. `rates` now scrapes the page;
  `convert` derives from it the same way as `turkiyefinans`.
- **`tom`** — checked its two calculator scripts
  (`calculation-tool-dynamic.js`, `calculation-tool-kp.js`) for any
  currency-shaped endpoint, then probed the `webintegration.tombank.com.tr`
  API namespace directly with its known working credential against plausible
  route names (`CurrencyCalculation/GetCurrencyRates`, `ExchangeRate/GetAll`,
  `GoldPrice/Get`, and near variants). Every one came back a clean 404 — not
  the 401 a real-but-unauthorised route would give — so there is no currency
  endpoint to find, authenticated or not.
- **`ziraat`** — its homepage embeds only the already-documented kâr payı
  calculator (currency is an *input* to that form, not a published board).
  Checked `/bireysel/yatirim-urunleri/spot-doviz` and
  `/bireysel/hesaplar/altin-hesaplar`, the two pages a "spot FX" and "gold
  account" link would plausibly lead to: both are marketing copy with no
  table, widget, or embedded rate data. No `drupalSettings` currency config
  either. Genuinely nothing to read.
- **`adil`** was not re-probed — `docs/discovery/captured/adil.md` already
  confirmed zero inputs, zero selects and zero backend calls across the whole
  site, checked with a real browser on the correct domain. Nothing about a
  converter would change that finding.

Net: **one bank added to `convert` by deriving instead of calling
(`turkiyefinans`), one bank added to the app entirely (`emlak`, `rates` and
`convert` both)**, and three genuine "nothing published" findings, each
checked by reading the bank's own code or exhausting its plausible endpoint
names rather than assumed from an earlier pass.

Also refactored: `kuveytturk` and `hayat` had each independently written the
"derive a conversion from published rates" arithmetic, drifting on the same
two edge cases (matching a feed's own spelling of a code case-insensitively,
and what to do when a feed does not list TL itself). A third and fourth bank
needing the identical logic is what a shared helper is for —
`BaseBank.convert_from_rates` now holds it once, and all four `convert()`
methods are one line each.
