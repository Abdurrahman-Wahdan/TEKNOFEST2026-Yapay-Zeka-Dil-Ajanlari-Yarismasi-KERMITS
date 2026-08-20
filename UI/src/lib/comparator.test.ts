import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  bestRates,
  cardTable,
  defaultSort,
  financeTable,
  isSelfQuote,
  mileRatesTable,
  movements,
  orderByCoverage,
  pairLabel,
  profitShareTable,
  quoteBasis,
  rateGroup,
  ratesBoard,
  spreadLooksBroken,
  spreadPct,
  type BankRate,
  type Labels,
} from "./comparator.ts";
import type { CardInstallmentQuote, Comparison, FinanceQuote, MileRate, ProfitShareQuote } from "./api.ts";
import type { Row } from "./contract.ts";

const t = {
  bank: "Banka", instrument: "Enstrüman", category: "Tür",
  buy: "Alış", sell: "Satış",
} as unknown as Labels;

const rate = (
  bank: string, code: string, canonical: string, unit: string, buy: number, sell: number,
  quote_currency = "TRY",
): BankRate =>
  ({ bank, code, canonical, unit, name: code, buy, sell, quote_currency, as_of: "" }) as BankRate;

describe("rateGroup", () => {
  it("separates currencies, metals and coins by unit", () => {
    assert.equal(rateGroup({ code: "USD", unit: "1" }), "currency");
    assert.equal(rateGroup({ code: "ALT (gr)", unit: "gram" }), "metal");
    assert.equal(rateGroup({ code: "ZCeyrek", unit: "coin" }), "coin");
  });

  it("pulls a cross rate out of the currency shelf", () => {
    // EUR/USD arrives with unit "1" but is not a TRY price. Left among the
    // currencies it would poison any "cheapest USD" comparison.
    assert.equal(rateGroup({ code: "EUR/USD", unit: "1" }), "parity");
  });
});

describe("spread", () => {
  it("is mid-based, so a wide spread is not flattered", () => {
    // buy 100 / sell 110 -> 10 over a mid of 105
    assert.ok(Math.abs(spreadPct(100, 110)! - 9.5238) < 0.001);
  });

  it("flags the broken feed rows rather than ranking them", () => {
    // Real Kuveyt Türk rows: MYR 7,59/15,78 and CNH 4,60/9,56.
    assert.equal(spreadLooksBroken(7.58934, 15.78379), true);
    assert.equal(spreadLooksBroken(4.60057, 9.55771), true);
    // A normal USD spread is fine.
    assert.equal(spreadLooksBroken(47.03, 48.13), false);
  });

  it("treats a negative or zero-mid spread as broken", () => {
    assert.equal(spreadLooksBroken(50, 40), true);
    assert.equal(spreadLooksBroken(0, 0), true);
  });
});

describe("bestRates", () => {
  const usd = [
    rate("kuveytturk", "USD", "USD", "1", 47.03, 48.13),
    rate("albaraka", "USD", "USD", "1", 47.44, 48.41),
    rate("hayat", "USD", "USD", "1", 47.07, 48.43),
  ];

  it("points the two bests in opposite directions", () => {
    const { bestBuy, bestSell } = bestRates(usd);
    // Highest buy: the bank pays you most.
    assert.equal(bestBuy!.bank, "albaraka");
    // Lowest sell: the bank charges you least.
    assert.equal(bestSell!.bank, "kuveytturk");
  });

  it("declines to crown anything with a single quote", () => {
    const { bestBuy, bestSell } = bestRates([usd[0]]);
    assert.equal(bestBuy, null);
    assert.equal(bestSell, null);
  });

  it("excludes broken-spread rows from winning", () => {
    const withBroken = [
      rate("kuveytturk", "MYR", "MYR", "1", 7.59, 15.78),
      rate("albaraka", "MYR", "MYR", "1", 12.0, 12.4),
    ];
    // Only one usable quote remains, so nothing is crowned.
    assert.equal(bestRates(withBroken).bestBuy, null);
  });
});

describe("ratesBoard", () => {
  it("matches gold across banks that name it differently", () => {
    const rows = ratesBoard(
      [
        rate("albaraka", "XAU", "XAU", "gram", 6625, 6788),
        rate("kuveytturk", "ALT (gr)", "XAU", "gram", 6554, 6750),
      ],
      ["albaraka", "kuveytturk"],
      t,
    ).rows;
    // One row, both banks filled — the whole point of the canonical field.
    assert.equal(rows.length, 1);
    assert.equal(rows[0].cells["albaraka__buy"], 6625);
    assert.equal(rows[0].cells["kuveytturk__buy"], 6554);
  });

  it("never merges a coin price into a gram row", () => {
    const rows = ratesBoard(
      [
        rate("kuveytturk", "ALT (gr)", "XAU", "gram", 6554, 6750),
        rate("kuveytturk", "ZCeyrek", "ZCEYREK", "coin", 10613, 11048),
      ],
      ["kuveytturk"],
      t,
    ).rows;
    assert.equal(rows.length, 2);
  });

  it("leaves a bank that does not quote an instrument as null, not zero", () => {
    const rows = ratesBoard(
      [rate("kuveytturk", "GMS (gr)", "XAG", "gram", 97.5, 100.4)],
      ["kuveytturk", "albaraka"],
      t,
    ).rows;
    assert.equal(rows[0].cells["albaraka__buy"], null);
  });

  it("builds a buy/sell column pair per bank", () => {
    const { columns } = ratesBoard([], ["albaraka", "hayat"], t);
    assert.deepEqual(
      columns.map((c) => c.key),
      ["instrument", "unit", "albaraka__buy", "albaraka__sell", "hayat__buy", "hayat__sell"],
    );
  });
});


// ----- finance rows -----

const financeLabels = {
  bank: "Banka", product: "Ürün", basis: "Ürün farkı", instalment: "Taksit",
  total: "Toplam", profitRate: "Kâr payı", annualCost: "Yıllık maliyet",
  insured: "Sigortalı", uninsured: "Sigortasız",
  coversAll: "Tüm ürünleri kapsıyor", rateOnly: "Sadece oran yayınlıyor",
  computed: "Kurdan hesaplandı",
} as unknown as Labels;

const quote = (over: Partial<FinanceQuote>): FinanceQuote =>
  ({
    bank: "vakif",
    product: { code: "IF", name: "İhtiyaç Finansmanı", category: "finance" },
    amount: 100000, term: 24, installment: 5000, total: 120000,
    profit_rate: 3.19, annual_cost_rate: 55.6, fees: {},
    variant: "", general: false, derived: false, schedule: [],
    ...over,
  }) as FinanceQuote;

describe("quoteBasis", () => {
  it("says nothing when the row is a plain like-for-like match", () => {
    assert.equal(quoteBasis(quote({}), financeLabels), "");
  });

  it("names the insurance variant, so two rows from one bank differ", () => {
    assert.equal(
      quoteBasis(quote({ variant: "sigortali" }), financeLabels),
      "Sigortalı",
    );
    assert.equal(
      quoteBasis(quote({ variant: "sigortasiz" }), financeLabels),
      "Sigortasız",
    );
  });

  it("marks a product that covers the whole family", () => {
    // Ziraat sells one taşıt product, so the same row answers the 0 km and the
    // second-hand comparison. The number is real; it is just not specific.
    assert.equal(
      quoteBasis(quote({ general: true }), financeLabels),
      "Tüm ürünleri kapsıyor",
    );
  });

  it("explains an empty payment rather than leaving a blank cell", () => {
    assert.equal(
      quoteBasis(quote({ installment: null, total: null }), financeLabels),
      "Sadece oran yayınlıyor",
    );
  });

  it("keeps both facts when a row carries both", () => {
    // Türkiye Finans's rows are always a sigorta variant; card comparisons
    // still carry a payment-free bank too, so both facts must survive.
    assert.equal(
      quoteBasis(
        quote({ bank: "turkiyefinans", variant: "sigortasiz", installment: null }),
        financeLabels,
      ),
      "Sigortasız · Sadece oran yayınlıyor",
    );
  });

  it("marks a payment that was computed rather than published", () => {
    // Türkiye Finans's own instalment, since `_installment_plan`: a real
    // number, worked out from the bank's own rate rather than read off the
    // wire, and it must not be mistaken for a bank's own stated payment.
    assert.equal(
      quoteBasis(
        quote({ bank: "turkiyefinans", variant: "sigortasiz", derived: true }),
        financeLabels,
      ),
      "Sigortasız · Kurdan hesaplandı",
    );
  });

  it("prefers rate-only over computed when a row somehow carries both", () => {
    // Should never happen together in practice -- a null instalment and
    // `derived: true` contradict each other -- but if it did, "no payment"
    // is the more important fact to show, not "a payment, and it's ours".
    assert.equal(
      quoteBasis(quote({ installment: null, derived: true }), financeLabels),
      "Sadece oran yayınlıyor",
    );
  });
});

describe("financeTable", () => {
  const build = (quotes: FinanceQuote[]) =>
    financeTable({ quotes } as unknown as Comparison, financeLabels);

  it("keeps a missing payment null instead of zero", () => {
    // Zero would sort to the top and crown the one bank that never quoted a
    // payment as the cheapest.
    const { rows } = build([quote({ installment: null, total: null })]);
    assert.equal(rows[0].cells.installment, null);
    assert.equal(rows[0].cells.total, null);
  });

  it("gives one bank a row per variant rather than collapsing them", () => {
    const { rows } = build([
      quote({ bank: "turkiyefinans", variant: "sigortali", installment: null, profit_rate: 2.95 }),
      quote({ bank: "turkiyefinans", variant: "sigortasiz", installment: null, profit_rate: 3.41 }),
    ]);
    assert.equal(rows.length, 2);
    assert.deepEqual(rows.map((r) => r.cells.profit_rate), [2.95, 3.41]);
    assert.notEqual(rows[0].cells.basis, rows[1].cells.basis);
  });

  it("carries a basis column the filter bar can offer", () => {
    const { columns } = build([quote({})]);
    const basis = columns.find((c) => c.key === "basis");
    assert.ok(basis, "the basis column is missing");
    assert.equal(basis.filterable, true);
  });
});

const cardLabels = {
  bank: "Banka", card: "Kart", basis: "Ürün farkı", installments: "Taksit sayısı",
  instalment: "Taksit", total: "Toplam", profitRate: "Kâr payı",
  rateOnly: "Sadece oran yayınlıyor",
} as unknown as Labels;

const cardQuote = (over: Partial<CardInstallmentQuote>): CardInstallmentQuote =>
  ({
    bank: "kuveytturk",
    card: { code: "SK", name: "Sağlam Kart Troy", category: "card" },
    amount: 10000, installments: 6, installment: 1900.64, total: 11403.84,
    profit_rate: 2.99,
    ...over,
  }) as CardInstallmentQuote;

describe("cardTable", () => {
  const build = (card_quotes: CardInstallmentQuote[]) =>
    cardTable({ card_quotes } as unknown as Comparison, cardLabels);

  it("gives every card a row -- a bank with five cards is five rows", () => {
    const { rows } = build([
      cardQuote({ card: { code: "SK", name: "Sağlam Kart Troy", category: "card" } }),
      cardQuote({ card: { code: "BP", name: "Sağlam Business Kart", category: "card" } }),
      cardQuote({
        bank: "vakif",
        card: { code: "FK", name: "Ferah Kart", category: "card" },
        installment: 1947.08,
      }),
    ]);
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((r) => r.cells.bank), ["kuveytturk", "kuveytturk", "vakif"]);
  });

  it("keeps a missing payment null instead of zero", () => {
    // Zero would sort to the top and crown the bank that never quoted a
    // payment as the cheapest, the same trap financeTable guards against.
    const { rows } = build([
      cardQuote({ bank: "turkiyefinans", installment: null, total: null, profit_rate: 4.25 }),
    ]);
    assert.equal(rows[0].cells.installment, null);
    assert.equal(rows[0].cells.total, null);
    assert.equal(rows[0].cells.basis, "Sadece oran yayınlıyor");
  });

  it("leaves a real payment's basis blank", () => {
    const { rows } = build([cardQuote({})]);
    assert.equal(rows[0].cells.basis, "");
  });
});

const mileLabels = {
  card: "Kart", tier: "Seviye", category: "Tür", perLira: "TL başına mil",
} as unknown as Labels;

const mileRate = (over: Partial<MileRate>): MileRate =>
  ({ card: "Sağlam Kart Troy", tier: "Platin", category: "market", per_lira: 1, ...over }) as MileRate;

describe("mileRatesTable", () => {
  it("carries no bank column -- the category is single-bank by construction", () => {
    const { columns, rows } = mileRatesTable([mileRate({})], mileLabels);
    assert.equal(columns.some((c) => c.key === "bank"), false);
    assert.equal(rows[0].cells.bank, undefined);
  });

  it("keeps a fractional per-lira rate instead of rounding it to a bare 0 or 1", () => {
    // Real rows run from 0,0015 to 1 -- the same rounding trap the FX board
    // and the converter had before their columns carried `decimals`.
    const { columns } = mileRatesTable([mileRate({ per_lira: 0.0015 })], mileLabels);
    const perLira = columns.find((c) => c.key === "per_lira");
    assert.ok(perLira?.decimals, "per_lira must declare a decimals option");
  });

  it("gives card, tier and category their own row", () => {
    const { rows } = mileRatesTable(
      [mileRate({ card: "Sağlam Kart Troy", tier: "Platin", category: "market", per_lira: 0.01 })],
      mileLabels,
    );
    assert.deepEqual(rows[0].cells, {
      // Capitalised on the way in -- see the next test.
      card: "Sağlam Kart Troy", tier: "Platin", category: "Market", per_lira: 0.01,
    });
  });

  it("capitalises the category the feed spells lowercase", () => {
    // Kuveyt Türk's own feed spells these "akaryakit", "yurtdisi" -- a
    // sentence-case first letter, not a translation or a spelling fix.
    const { rows } = mileRatesTable([mileRate({ category: "akaryakit" })], mileLabels);
    assert.equal(rows[0].cells.category, "Akaryakit");
  });

  it("renders card, tier and category as plain text, not a badge", () => {
    // A pill is for a state a reader distinguishes at a glance -- offered,
    // declined, derived. These three are just what the row is about, on 567
    // rows of them, and a coloured chip everywhere is decoration, not signal.
    const { columns } = mileRatesTable([mileRate({})], mileLabels);
    for (const key of ["card", "tier", "category"]) {
      assert.equal(columns.find((c) => c.key === key)?.type, "text");
    }
  });
});

const psLabels = {
    bank: "Banka", product: "Ürün", netProfit: "Net kâr", grossProfit: "Brüt kâr",
    netAnnual: "Net yıllık", term: "Vade", termUnit: "Birim", currency: "Kur",
} as unknown as Labels;

const psQuote = (currency: string) =>
    ({
      bank: "kuveytturk",
      product: { code: "K", name: "Altına Altın Katılma Hesabı", category: "profit_share" },
      amount: 100, term: 92, currency, term_unit: "day",
      net_profit: 0.0303, gross_profit: 0.0357, net_annual_rate: 0.12,
    }) as unknown as ProfitShareQuote;

describe("profitShareTable", () => {
  it("labels money in the run's own currency, not lira", () => {
    // A gold account pays in grams. Rendering 0,0303 grams as "TRY 0,03"
    // states a figure the bank never quoted, in a unit it never used.
    const { columns } = profitShareTable(
      { profit_share_quotes: [psQuote("XAU")] } as unknown as Comparison,
      psLabels,
    );
    const net = columns.find((c) => c.key === "net_profit");
    assert.equal(net?.currency, "XAU");
  });

  it("falls back to lira when there is nothing to read it from", () => {
    const { columns } = profitShareTable(
      { profit_share_quotes: [] } as unknown as Comparison,
      psLabels,
    );
    assert.equal(columns.find((c) => c.key === "net_profit")?.currency, "TRY");
  });
});

describe("quoteBasis across quote shapes", () => {
  it("does not call a participation quote rate-only", () => {
    // A ProfitShareQuote has no `installment` field at all. Reading that
    // absence as "no payment published" tagged every savings row as rate-only.
    const ps = { variant: "", general: false } as unknown as FinanceQuote;
    assert.equal(quoteBasis(ps, financeLabels), "");
  });

  it("still marks a finance row that carries a null instalment", () => {
    const fin = { variant: "", general: false, installment: null } as unknown as FinanceQuote;
    assert.equal(quoteBasis(fin, financeLabels), "Sadece oran yayınlıyor");
  });

  it("marks a bank answering with its ordinary account", () => {
    const general = { variant: "", general: true } as unknown as FinanceQuote;
    assert.equal(quoteBasis(general, financeLabels), "Tüm ürünleri kapsıyor");
  });
});

describe("defaultSort", () => {
  it("ranks financing by the cheapest payment", () => {
    assert.deepEqual(defaultSort("finance"), { key: "installment", direction: "asc" });
    assert.deepEqual(defaultSort("card"), { key: "installment", direction: "asc" });
  });

  it("ranks savings by the largest return, not the smallest", () => {
    // The direction is not derivable from the column type: the best instalment
    // is the lowest and the best profit the highest, so one rule for "the money
    // column" would be wrong half the time.
    assert.deepEqual(defaultSort("profit_share"), { key: "net_profit", direction: "desc" });
  });

  it("leaves a board, a reference table and the converter unranked", () => {
    // The FX board is one row per instrument across many banks, and the mile
    // table is reference data — no single column orders either. The converter
    // has no universal "best" direction the way a lower instalment or a
    // higher profit does: whether more or less of the target currency is the
    // good outcome depends on which side of the trade the user is on, so it
    // starts unsorted rather than guessing "highest result" on their behalf.
    assert.equal(defaultSort("rates"), null);
    assert.equal(defaultSort("miles"), null);
    assert.equal(defaultSort("convert"), null);
  });

  it("names a column the table it ranks actually has", () => {
    const finance = financeTable(
      { quotes: [quote({})] } as unknown as Comparison, financeLabels);
    assert.ok(finance.columns.some((c) => c.key === defaultSort("finance")?.key));

    const ps = profitShareTable(
      { profit_share_quotes: [psQuote("TRY")] } as unknown as Comparison, psLabels);
    assert.ok(ps.columns.some((c) => c.key === defaultSort("profit_share")?.key));
  });
});

// ----- the live FX board -----

const boardLabels = {
  bank: "Banka", instrument: "Enstrüman", category: "Tür",
  buy: "Alış", sell: "Satış",
} as unknown as Labels;

const bankRate = (
  bank: string, code: string, canonical: string, unit: string, buy: number, sell: number,
): BankRate => ({ bank, code, canonical, unit, name: code, buy, sell, as_of: "" }) as BankRate;

describe("orderByCoverage", () => {
  it("puts the bank quoting most of the board first", () => {
    // Kuveyt Türk quotes 27 instruments and Hayat 4. Leaving the order to the
    // registry put a four-row bank in the first column, so its two blanks were
    // the first thing anyone read.
    const rates = [
      bankRate("hayat", "USD", "USD", "1", 47, 48),
      ...["USD", "EUR", "GBP"].map((c) => bankRate("kuveytturk", c, c, "1", 47, 48)),
      ...["USD", "EUR"].map((c) => bankRate("albaraka", c, c, "1", 47, 48)),
    ];
    assert.deepEqual(
      orderByCoverage(rates, ["hayat", "kuveytturk", "albaraka"]),
      ["kuveytturk", "albaraka", "hayat"],
    );
  });

  it("is stable when two banks quote the same amount", () => {
    // The board polls. Ties breaking on arrival order would reshuffle the
    // columns underneath the reader every few seconds.
    const rates = [
      bankRate("vakif", "USD", "USD", "1", 47, 48),
      bankRate("dunya", "USD", "USD", "1", 47, 48),
    ];
    const once = orderByCoverage(rates, ["vakif", "dunya"]);
    const again = orderByCoverage([...rates].reverse(), ["dunya", "vakif"]);
    assert.deepEqual(once, again);
  });
});

describe("ratesBoard", () => {
  const rates = [
    bankRate("kuveytturk", "ALT (gr)", "XAU", "gram", 6284, 6896),
    bankRate("kuveytturk", "USD", "USD", "1", 44.3, 48.7),
    bankRate("kuveytturk", "ZCeyrek", "ZCeyrek", "coin", 11000, 11500),
    bankRate("albaraka", "XAU", "XAU", "gram", 6626, 6793),
    bankRate("albaraka", "USD", "USD", "1", 47.4, 48.4),
  ];

  it("groups gold across banks that spell it differently", () => {
    // Albaraka says XAU, Kuveyt Türk says "ALT (gr)". Only the canonical
    // symbol makes them one row.
    const { rows } = ratesBoard(rates, ["kuveytturk", "albaraka"], boardLabels);
    const gold = rows.filter((r) => r.cells.instrument === "XAU/TRY");
    assert.equal(gold.length, 1);
    assert.equal(gold[0].cells["kuveytturk__buy"], 6284);
    assert.equal(gold[0].cells["albaraka__buy"], 6626);
  });

  it("puts each bank's name once, above its own pair", () => {
    // One heading per bank spanning buy and sell. Repeating the bank's name on
    // both columns read as two banks rather than one bank's two prices.
    const { columns, groups } = ratesBoard(
      rates, ["kuveytturk", "albaraka"], boardLabels,
      { kuveytturk: "Kuveyt Türk Katılım Bankası" },
    );

    assert.deepEqual(
      groups.map((g) => [g.label, g.span]),
      [["", 1], ["", 1], ["KUVEYT TÜRK KATILIM BANKASI", 2], ["ALBARAKA", 2]],
    );
    // The columns underneath say only which side they are.
    assert.deepEqual(
      columns.slice(2).map((c) => c.label),
      ["Alış", "Satış", "Alış", "Satış"],
    );
  });

  it("keeps the header spans aligned with the columns", () => {
    // A group row whose spans do not add up to the column count shifts every
    // heading one cell left and silently mislabels the whole board.
    const { columns, groups } = ratesBoard(rates, ["kuveytturk", "albaraka"], boardLabels);
    assert.equal(groups.reduce((n, g) => n + g.span, 0), columns.length);
  });

  it("puts the rows most banks quote at the top", () => {
    // The single-source tail is real data, not a gap, so it sits below the
    // comparable rows rather than being dropped.
    const { rows } = ratesBoard(rates, ["kuveytturk", "albaraka"], boardLabels);
    assert.equal(rows.at(-1)?.cells.instrument, "ZCeyrek/TRY");
  });

  it("never lets a coin price share a row with a gram price", () => {
    const { rows } = ratesBoard(rates, ["kuveytturk", "albaraka"], boardLabels);
    const keys = rows.map((r) => `${r.cells.instrument}|${r.cells.unit}`);
    assert.equal(new Set(keys).size, keys.length);
  });
});

describe("movements", () => {
  const row = (instrument: string, buy: number) =>
    ({ cells: { instrument, kuveytturk__buy: buy } }) as unknown as Row;

  it("reports nothing on the first poll", () => {
    // Everything would otherwise flash green the moment the board loads.
    assert.deepEqual(movements(null, [row("USD", 47)]), {});
    assert.deepEqual(movements([], [row("USD", 47)]), {});
  });

  it("marks a rise and a fall", () => {
    const before = [row("USD", 47), row("EUR", 55)];
    const after = [row("USD", 48), row("EUR", 54)];
    assert.deepEqual(movements(before, after), {
      "USD|kuveytturk__buy": "up",
      "EUR|kuveytturk__buy": "down",
    });
  });

  it("says nothing about a price that did not move", () => {
    assert.deepEqual(movements([row("USD", 47)], [row("USD", 47)]), {});
  });

  it("keys on the instrument, not the row position", () => {
    // Rows are ordered by how many banks quote them, so a position means a
    // different instrument as soon as one bank adds or drops a row.
    const before = [row("USD", 47), row("EUR", 55)];
    const after = [row("EUR", 55), row("USD", 48)];
    assert.deepEqual(movements(before, after), { "USD|kuveytturk__buy": "up" });
  });

  it("ignores a row that is new this poll", () => {
    assert.deepEqual(movements([row("USD", 47)], [row("USD", 47), row("GBP", 64)]), {});
  });
});

describe("the pair, the unit and the lira", () => {
  const t = { ...boardLabels, unit: "Birim", perUnit: "birim", perGram: "gram", perCoin: "adet" } as unknown as Labels;

  it("writes the pair so the direction is on the row", () => {
    // A bare "USD" leaves the reader to assume which way round it is. Turkish
    // bank boards write the pair; so do we.
    assert.equal(pairLabel("USD"), "USD/TRY");
    assert.equal(pairLabel("XAU"), "XAU/TRY");
  });

  it("drops the lira's own row", () => {
    // Kuveyt Türk publishes TL at 1,00 / 1,00. True, and worth nothing: it is
    // the unit everything else is measured in, and left in it sorts among real
    // prices and reads as a quote.
    assert.equal(isSelfQuote("TRY"), true);
    assert.equal(isSelfQuote("USD"), false);

    const { rows } = ratesBoard([
      bankRate("kuveytturk", "TL", "TRY", "1", 1, 1),
      bankRate("kuveytturk", "USD", "USD", "1", 44, 48),
    ], ["kuveytturk"], t);
    assert.deepEqual(rows.map((r) => r.cells.instrument), ["USD/TRY"]);
  });

  it("keeps a USD ounce quote out of the TRY comparison board", () => {
    const { rows, columns } = ratesBoard([
      bankRate("turkiyefinans", "ALT (gr)", "XAU", "gram", 6614, 6823),
      rate("turkiyefinans", "XAU", "XAU", "ounce", 4362, 4400, "USD"),
    ], ["turkiyefinans"], t);

    assert.ok(columns.some((c) => c.key === "unit"));
    assert.equal(rows.length, 1);
    assert.equal(rows[0].cells.unit, "gram");
  });

  it("refuses a duplicate instead of silently replacing a bank quote", () => {
    assert.throws(() => ratesBoard([
      rate("albaraka", "USD", "USD", "1", 47, 48),
      rate("albaraka", "USD", "USD", "1", 47.1, 48.1),
    ], ["albaraka"], t), /Duplicate live rate identity/);
  });

  it("labels a coin price as a coin", () => {
    const { rows } = ratesBoard(
      [bankRate("kuveytturk", "ZCeyrek", "ZCEYREK", "coin", 11000, 11500)],
      ["kuveytturk"], t);
    assert.equal(rows[0].cells.unit, "adet");
  });
});

describe("hiding a bank keeps the header aligned", () => {
  // The board's group row spans two columns per bank. Hiding a bank without
  // shrinking its heading shifts every later heading one cell left, so the
  // labels stay and sit over the wrong prices — which looks correct.
  const shrink = (
    groups: { key: string; label: string; span: number }[],
    shown: { key: string }[],
  ) =>
    groups
      .map((g) => ({
        ...g,
        span:
          g.span === 1
            ? shown.some((c) => c.key === g.key) ? 1 : 0
            : shown.filter((c) => c.key.startsWith(`${g.key}__`)).length,
      }))
      .filter((g) => g.span > 0);

  const rates = [
    bankRate("kuveytturk", "USD", "USD", "1", 44, 48),
    bankRate("albaraka", "USD", "USD", "1", 47, 48),
  ];

  it("drops a bank's heading when both its columns go", () => {
    const { columns, groups } = ratesBoard(rates, ["kuveytturk", "albaraka"], boardLabels);
    const shown = columns.filter((c) => !c.key.startsWith("albaraka__"));
    const next = shrink(groups, shown);

    assert.deepEqual(next.map((g) => g.key), ["instrument", "unit", "kuveytturk"]);
    assert.equal(next.reduce((n, g) => n + g.span, 0), shown.length);
  });

  it("halves a bank's heading when only its sell column is shown", () => {
    const { columns, groups } = ratesBoard(rates, ["kuveytturk", "albaraka"], boardLabels);
    const shown = columns.filter((c) => !c.key.endsWith("__buy"));
    const next = shrink(groups, shown);

    assert.deepEqual(next.filter((g) => g.span > 1).map((g) => g.span), []);
    assert.equal(next.reduce((n, g) => n + g.span, 0), shown.length);
  });
});

describe("every board column can be sorted", () => {
  it("offers a sort on the pair, the unit and every price", () => {
    // The price columns were made unsortable on a judgement nobody asked for.
    // A board where only the name column sorts is a board where the question
    // "who is cheapest on this" cannot be asked at all.
    const { columns } = ratesBoard([
      bankRate("kuveytturk", "USD", "USD", "1", 44, 48),
      bankRate("albaraka", "USD", "USD", "1", 47, 48),
    ], ["kuveytturk", "albaraka"], boardLabels);

    const unsortable = columns.filter((c) => !c.sortable).map((c) => c.key);
    assert.deepEqual(unsortable, [], `these offer no sort: ${unsortable}`);
  });
});

describe("board alignment", () => {
  it("starts the price headings where their figures start", () => {
    // Numeric columns default to right-aligned; on this board the heading then
    // sits against the next bank's column while its prices begin elsewhere.
    const { columns } = ratesBoard(
      [bankRate("kuveytturk", "USD", "USD", "1", 44, 48)],
      ["kuveytturk"], boardLabels);

    const prices = columns.filter((c) => c.key.includes("__"));
    assert.equal(prices.length, 2);
    assert.deepEqual([...new Set(prices.map((c) => c.align))], ["left"]);
  });

  it("leaves the pair and the unit as they were", () => {
    const { columns } = ratesBoard(
      [bankRate("kuveytturk", "USD", "USD", "1", 44, 48)],
      ["kuveytturk"], boardLabels);

    assert.equal(columns.find((c) => c.key === "instrument")?.align, "left");
    assert.equal(columns.find((c) => c.key === "unit")?.align, "left");
  });
});
