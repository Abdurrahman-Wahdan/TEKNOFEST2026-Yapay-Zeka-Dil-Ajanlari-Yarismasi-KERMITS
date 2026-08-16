# Ziraat Katılım — endpoint inventory

Captured 2026-08-08. Platform: Drupal. Host `www.ziraatkatilim.com.tr`.
**36/36 verified**, 1 known gap. 17 finance products — the largest finance
catalogue of the ten.

## Where the calculators are

**On the homepage**, with no dedicated URL. `/hesaplama-araclari` answers `493`.
URL-based discovery is structurally blind to this bank.

## Two different transports on one site

| calculator | route | reachable without a browser? |
|---|---|---|
| finansman | `POST /ajax/finansmanhesapla?_wrapper_format=drupal_ajax` | **yes**, plain `httpx` |
| kâr payı | `POST /anasayfa?ajax_form=1&_wrapper_format=drupal_ajax` | **no — 493** |
| leasing | same form POST | **no — 493** |

The `/ajax/*` routes answer plain `httpx` happily. The Drupal *form* endpoint
answers `493` to every non-browser client, `curl_cffi` impersonation included,
and there is no `/ajax/` route for kâr payı or leasing — every plausible name
(`karpayihesapla`, `kar-payi-hesapla`, `leasinghesapla`, …) returns a Drupal
`"No route found"`.

**Corrected 2026-08-15 — kâr payı is NOT browser-only.** The `493` comes from
the `ajax_form=1` parameter, not from the endpoint. Drop it and the same URL
answers normally:

    POST /anasayfa?_wrapper_format=drupal_ajax          -> 200, processes the form
    POST /anasayfa?ajax_form=1&_wrapper_format=...      -> 493

The form is `kari_payi_hesapla_form` (Ziraat's own id carries the typo "kari",
which is why every guessed `/ajax/` name missed). Its real fields, read from the
homepage rather than guessed:

| field | values |
|---|---|
| `kar_payi_hesap_type` | `5` KATILMA HESABI · `2` ARA DÖNEM ÖDEMELİ |
| `kar_payi_currency` | `TRY` `EUR` `USD` `XAU` |
| `kar_payi_maturity_type` | `2` 1 ay · `8` 3 ay · `11` 6 ay · `5` 1 yıl · `14` esnek |
| `kar_payi_ana_para`, `kar_payi_vade` | amount, term in days |

Posted correctly it echoes the input back and returns the four result fields —
**and every one of them is zero**. Swept all 5 maturities × TRY/USD/XAU: `Net
Getiri 0`, `Brüt Getiri 0`, `Net Oran 0%`, `Brüt Oran 0%`, each with the bank's
own message *"Girişi yapılan değerler için henüz kâr payı dağıtımı yapılmamış
olduğundan, hesaplama yapılamamaktadır."*

The tool is **retrospective**: it reports rates already distributed to accounts
that have matured, not a forward quote.

**Wrong conclusion, corrected 2026-08-16 — this was the wrong endpoint, not
proof kâr payı has none.** `/anasayfa?_wrapper_format=drupal_ajax` (above) is
one calculator on the homepage; it is not the one the kâr payı *widget* itself
calls. Guessing at `/ajax/` names from the form's own id (`kari_payi_hesapla_form`,
the bank's typo) never found the real one because the real one does not share
that name. Driving the actual widget in a live browser (Playwright, watching
network traffic rather than guessing at it) and clicking through its dropdowns
shows it firing:

```
POST /ajax/karpayi-products?_wrapper_format=drupal_ajax
karpayi_hesap_type=5&karpayi_hesap_currency=TRY&karpayi_hesap_anapara=100000
&karpayi_hesap_vade=92&karpayi_maturity_type=14&_drupal_ajax=1
→ [{"command":"insert",...,"selector":".kar-payi-net-gelir","data":"5.987,20",...},
   ...".kar-payi-brut-gelir":"7.257,22", ".kar-payi-net-oran":"23,75",
   ".kar-payi-brut-oran":"28,79"]
```

Answers plain `curl`, no cookie, no browser fingerprint, exactly like
`/ajax/finansmanhesapla` — and it is a genuine forward quote, not the
retrospective one: the gross rate above (28,79% for a 92-day TRY deposit)
matches `kar-payilari`'s own published `ThreeMountsOfYear` rate for the same
pool, and the figure scales correctly with amount and day count from 1 to
800+ days tried live. `karpayi_hesap_type=2` (ARA DÖNEM ÖDEMELİ) and currency
`XAU` were swept the same way and answered zero at every combination tried —
those two are excluded in code, everything else (`TRY`/`USD`/`EUR` ×
`KATILMA HESABI`) is not.

`profit_share` is a real Ziraat capability now (`banks/providers/ziraat.py`).
The retrospective tool at `/anasayfa` above is still real and still always
zero for a forward question — it is just not the calculator this project
uses, and the record of it stays as a warning against guessing a name that
looks obviously right (`kar_payi_hesap_type` matching the widget's own field
names) without checking what the widget itself actually calls.

## Endpoints that work headlessly

```
POST /ajax/get-vade
eid=<product id>
→ {"status":true,"data":{"range":[1..36],"ratio":"4.99",
                         "maximum_amount":124999,"minimum_amount":"0"}}

POST /ajax/finansmanhesapla?_wrapper_format=drupal_ajax
lang=tr&finansman_is_bank_ratio=true&finans_type=<eid>
&finans_kar_orani=<ratio from get-vade>&finans_vade=24&finans_tutari=100000
&_drupal_ajax=1

POST /ajax/get-maturity-types?_wrapper_format=drupal_ajax
eid=2&lang_id=tr   → kâr payı maturity types (1/3/6 aylık, yıllık, esnek)
```

`get-vade` is the catalogue call: it returns the allowed terms, the profit rate
and the amount ceiling for one product, and `finansmanhesapla` needs that rate
passed back to it.

## Products

`finans_type` is an opaque numeric eid from the homepage
`select[name=finansman_type]`. 17 of them, e.g.:

| eid | product | rate | max | terms |
|---|---|---|---|---|
| `48671069` | Konut Finansmanı Kampanya | 2.89 | 9 999 999 | 1–120 |
| `25961206` | Konut Finansmanı | 3.19 | 9 999 999 | 1–120 |
| `64356287` | İhtiyaç Finansmanı | 4.99 | 124 999 | 1–36 |
| `64445628` | Taşıt Finansmanı | 3.39 | 399 999 | 1–36 |
| `20539018` | Arsa Finansmanı | 4.99 | 4 999 999 | 1–60 |
| `20539017` | Bireysel İşyeri | 4.99 | 4 999 999 | 1–60 |

The same product appears several times with different term bands and different
ceilings (İhtiyaç 1–12 / 1–24 / 1–36 at 999 999 / 249 999 / 124 999). The eid,
not the name, is the identity — **and the ceiling falls as the term rises**, so
picking by name alone will hit the wrong limit.

## Response shape

A Drupal command array. The answer is HTML inside the `insert` command targeting
`#odeme-plani`; strip tags and read the figures:

```
Finansman Tutarı  Taksit Tutarı  Vade  Kâr Oranı  Toplam Geri Ödenen
100.000,00 TRY    8.330,01 TRY   24 Ay  %4,99      199.920,24 TRY
```

then a per-instalment table with Ana Para, Kâr Tutarı, KDV, KKDF, BSMV and
Kalan Ana Para. The numbers are the bank's; we only extract them from markup.

Verified 100 000 TL / 24 ay: konut kampanya 5 834,99 (%2,89) · ihtiyaç
8 330,01 (%4,99) · taşıt 6 924,20 (%3,49).
