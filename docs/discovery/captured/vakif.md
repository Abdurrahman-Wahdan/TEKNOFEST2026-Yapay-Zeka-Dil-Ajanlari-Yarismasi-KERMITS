# Vakıf Katılım — endpoint inventory

Captured 2026-08-08. Platform: Unigate. Host `www.vakifkatilim.com.tr`.
Four calculator pages under `/tr/yardimci-sayfalar/hesaplama-araclari/`.
**35/35 verified**, 1 known bank-side gap.

## Auth

Every calculator plugin is a `POST` whose **body carries only the anti-forgery
token** — the parameters go in the query string:

```
POST /plugins/<Name>?langId=…&language=tr&<params>
Content-Type: application/x-www-form-urlencoded
__RequestVerificationToken=<token>
```

Take the token from the matching calculator page:
`name="__RequestVerificationToken" value="…"`. It is per-page, so fetch the page
you are about to call. No WAF, plain `httpx` is fine.

`langId` is the same constant as Albaraka and Emlak:
`bf2689d9-071e-4a20-9450-b1dbdd39778f`.

## Endpoints

| what | endpoint | key parameters |
|---|---|---|
| finansman | `POST /plugins/FinancingComputationExecute` | `financingType`, `amount`, `numberOfInstallments`, `profitRate=null`, `calculateType=1` |
| allowed terms | `POST /plugins/FinancingInstallment` | `financingType` |
| payment plan | `POST /plugins/InstallmentPayBack` | as above |
| kâr payı | `POST /plugins/GrossAmountCalculationJson` | `accountType=KAH`, `currencyUnit`, `principal`, `expiry` |
| term list | `POST /plugins/MoneyExpiry` | `currencyTypeVal` |
| kart taksit | `POST /plugins/CardComputationExecute` | `cardType=FK`, `amount`, `numberOfInstallments` |
| card terms | `POST /plugins/CardCalculationInstallment` | `cardType` |
| döviz çevirici | `GET /plugins/CurrencyConverter` | `amount`, `InputCurrencyType`, `convertCurrencyType` |
| kur listesi | `GET /plugins/DetailCurrencyListData` | `currencyTypeId` |

## Products

Finance `financingType`: `IF` İhtiyaç · `K` Sıfır Konut · `K2` 2. El Konut ·
`BO` Taşıt 0 km · `BO2` Taşıt 2. El · `I` İşyeri · `A` Arsa.
Terms come from `FinancingInstallment`: 36 for ihtiyaç, 120 for konut.

Kâr payı `currencyUnit`: `0` TL · `1` USD · `19` EUR · `24` ALTIN.
`expiry` in days: `31` aylık · `91` 3 aylık · `180` 6 aylık · `364` yıllık ·
`366` 1 yıl üzeri · `1` kırık vade.

Cards: `FK` Ferah Kart, 1–12 taksit.

## Responses

Turkish-formatted strings, same as Albaraka: `"7.159,22 TL"`, `"%31,80"`.

Finance → `installmentAmount`, `totalAmount`, `profitRate`, `appraisementFee`,
`mortgageReleaseFee`, `errorMessage`.
Kâr payı → `grossProfit`, `netProfit`, `grossRate`, `netRate`, `accountName`,
`errorMessage`.

Verified 100 000 TL / 24 ay: ihtiyaç 7 159,22 · konut 6 058,44 · taşıt 6 746,01.
Kâr payı 100 000 TL 1 ay → brüt 2 700,46 / net 2 227,88 / %31,80.

## Traps

- **Failure arrives as an empty 200, not an error.** Gold at `expiry=366` and at
  `expiry=1` returns a zero-length body. Check for an empty body *before*
  parsing JSON, or it surfaces as a confusing decode error.
- `errorMessage` is populated inside an otherwise-200 JSON body; HTTP status
  stays 200 either way.
- The page's inputs have ids but no labels, so an accessibility-tree snapshot
  reports zero textboxes on a working calculator. Query the DOM.
