/**
 * Turning live comparison results into the table the app already knows how to draw.
 *
 * The comparator is deterministic software over the bank endpoints — nothing
 * here is produced by a model. But the *rendering* problem is identical to the
 * one `ProducedTable` already solves (arbitrary columns, null-safe cells,
 * sorting, per-column filters, the app's table styling), so results are
 * converted into the same `{columns, rows}` shape rather than growing a second
 * table component that has to be kept looking the same.
 *
 * Kept free of React so the column definitions and the null handling can be
 * tested directly.
 */

import type { ResolvedColumn, Row } from "./contract.ts";
import type { SortState } from "./table-filter.ts";
import type {
  Comparison, FinanceQuote, MileRate, Rate,
} from "./api.ts";

export type CategoryKey =
  | "finance"
  | "profit_share"
  | "rates"
  | "convert"
  | "card"
  | "miles";

export interface CategorySpec {
  key: CategoryKey;
  /** Banks that can take part at all, from GET /api/banks `publishes`. */
  capability: string;
  /** True when the category ranks banks against each other. */
  ranks: boolean;
}

/**
 * What each category needs from a bank.
 *
 * `capability` is checked against the registry so the bank picker can grey out
 * banks with the bank's own reason, rather than letting someone select a bank
 * that will always answer "not offered".
 */
export const CATEGORIES: Record<CategoryKey, CategorySpec> = {
  finance: { key: "finance", capability: "finance", ranks: true },
  profit_share: { key: "profit_share", capability: "profit_share", ranks: true },
  rates: { key: "rates", capability: "rates", ranks: true },
  convert: { key: "convert", capability: "convert", ranks: true },
  card: { key: "card", capability: "card", ranks: true },
  miles: { key: "miles", capability: "mile_rates", ranks: false },
};

/**
 * How each category ranks itself before anyone touches a column header.
 *
 * A comparison whose rows arrive in whatever order the banks answered is not a
 * comparison -- the cheapest quote lands wherever that bank happened to be
 * quickest, and the user has to sort a table to get the answer they came for.
 *
 * The direction is per category and not guessable from the column type: the
 * best instalment is the **lowest** and the best profit the **highest**, so a
 * single "sort by the money column" rule would be wrong half the time.
 *
 * Null where no single column ranks the table: the FX board is one row per
 * instrument across many banks, the mile table is reference data
 * (`CATEGORIES.miles.ranks === false`), and the converter -- the user's own
 * call, against the same reasoning above -- starts unsorted so the sort
 * arrow only ever appears because someone clicked a header, not because the
 * table decided the highest result was the "best" one on their behalf. A
 * conversion has no universally-best direction the way a lower instalment or
 * a higher profit does: whether more or less of the target currency is the
 * good outcome depends on which side of the trade the user is on, which this
 * function has no way to know.
 *
 * Rows with a missing value sink either way -- `sortRows` guarantees it -- so
 * Türkiye Finans, which publishes a rate and no payment, can never be ranked
 * cheapest on a column it did not answer.
 */
export function defaultSort(category: CategoryKey): SortState | null {
  switch (category) {
    case "finance":
    case "card":
      return { key: "installment", direction: "asc" };
    case "profit_share":
      return { key: "net_profit", direction: "desc" };
    default:
      return null;
  }
}

function col(
  key: string,
  label: string,
  type: ResolvedColumn["type"],
  extra: Partial<ResolvedColumn> = {},
): ResolvedColumn {
  const decimals = extra.decimals;
  const numeric = type === "money" || type === "percent" || type === "number";
  return {
    key,
    label,
    type,
    currency: extra.currency ?? "TRY",
    align: extra.align ?? (numeric ? "right" : type === "bool" ? "center" : "left"),
    // A bank column is alphabetical the same way the FX board's pair column
    // is -- text, sortable by default -- so every comparison table gets it
    // for free rather than each one asking for it individually.
    sortable: extra.sortable ?? (numeric || type === "date" || type === "bank"),
    filterable: extra.filterable ?? (type === "bank" || type === "badge"),
    inferred: false,
    ...(decimals ? { decimals } : {}),
  };
}

export interface Labels {
  bank: string;
  instalment: string;
  total: string;
  profitRate: string;
  annualCost: string;
  product: string;
  term: string;
  netProfit: string;
  grossProfit: string;
  netAnnual: string;
  termUnit: string;
  currency: string;
  instrument: string;
  buy: string;
  sell: string;
  spread: string;
  spreadPct: string;
  asOf: string;
  result: string;
  rate: string;
  source: string;
  computed: string;
  card: string;
  installments: string;
  tier: string;
  category: string;
  perLira: string;
  /** Why a row is not a plain like-for-like match. */
  basis: string;
  insured: string;
  uninsured: string;
  campaign: string;
  coversAll: string;
  rateOnly: string;
  /** The unit one quoted price refers to. */
  unit: string;
  perUnit: string;
  perGram: string;
  perCoin: string;
}

/**
 * Finance: the ranking table. Cheapest instalment first is the default sort.
 *
 * Two rows can come from one bank, and a row can arrive with no payment at all,
 * so `basis` is a column rather than a footnote: it is the difference between
 * "this bank is cheapest" and "this bank quoted a different product".
 */
export function financeTable(c: Comparison, t: Labels) {
  const columns = [
    col("bank", t.bank, "bank"),
    col("product", t.product, "text"),
    col("basis", t.basis, "badge", { filterable: true }),
    col("installment", t.instalment, "money"),
    col("total", t.total, "money"),
    col("profit_rate", t.profitRate, "percent"),
    col("annual_cost_rate", t.annualCost, "percent"),
  ];
  const rows: Row[] = (c.quotes ?? []).map((q) => ({
    cells: {
      bank: q.bank,
      product: q.product?.name ?? "",
      basis: quoteBasis(q, t),
      // Null where the bank publishes a rate but never states a payment, and
      // nothing here can reproduce one. Türkiye Finans's own instalment is no
      // longer null -- `derived` on `basis` above says it was computed, not
      // read off the wire -- but the column stays nullable for whichever bank
      // is next to publish a rate with no payment and no formula to port.
      installment: q.installment ?? null,
      total: q.total ?? null,
      profit_rate: q.profit_rate,
      // Null at Vakıf and Ziraat — they publish no annual cost rate. The table
      // renders an em dash and sorting sinks them, rather than treating a
      // missing figure as zero and calling them the cheapest.
      annual_cost_rate: q.annual_cost_rate ?? null,
    },
  }));
  return { columns, rows };
}

/**
 * Why this row is not a plain like-for-like match, in the user's words.
 *
 * Four independent facts, and Türkiye Finans carries two of them at once: its
 * rows are both a sigorta variant and a computed payment. Joined rather than
 * prioritised, because dropping any one leaves a question the table cannot
 * otherwise answer — why this bank has two rows, and why its payment is ours.
 */
const VARIANT_LABEL: Record<string, keyof Labels> = {
  sigortali: "insured",
  sigortasiz: "uninsured",
  kampanya: "campaign",
};

export function quoteBasis(
  q: Pick<FinanceQuote, "variant" | "general"> & {
    installment?: number | null;
    // Absent on `ProfitShareQuote`, the type's other caller — there is no
    // computed figure to flag on a savings row.
    derived?: boolean;
  },
  t: Labels,
): string {
  const parts: string[] = [];
  if (q.variant) {
    // Falls back to the bank's own word rather than mislabelling: a variant
    // added in Python without a label here should read oddly, not wrongly.
    const key = VARIANT_LABEL[q.variant];
    parts.push(key ? t[key] : q.variant);
  }
  if (q.general) parts.push(t.coversAll);
  // `in`, not a null check: a participation quote has no `installment` field at
  // all, and treating absent as null would label every savings row "rate only".
  // Only a finance row that carries the key and left it null means it.
  if ("installment" in q && q.installment == null) parts.push(t.rateOnly);
  // `derived` is the same claim `convertTable` labels with `computed` — the
  // figure is the bank's own rate run through arithmetic that is ours, not a
  // number the bank stated. Mutually exclusive with `rateOnly` above: a row
  // is either payment-free or its payment is computed, never both.
  else if (q.derived) parts.push(t.computed);
  return parts.join(" · ");
}

/**
 * Profit share.
 *
 * `term_unit` is a column, not a footnote: the run pins days, but Albaraka is
 * the one bank that answers in months, and a row that was priced on a different
 * clock has to say so where the number is.
 */
export function profitShareTable(c: Comparison, t: Labels) {
  // Money columns carry the run's own currency, not a default. A gold account
  // pays in grams, and rendering 0,36 grams as "TRY 0,36" states a figure the
  // bank never quoted. One run is always one currency -- it is a request
  // parameter -- so the first quote is the whole table's unit.
  const currency = c.profit_share_quotes?.[0]?.currency ?? "TRY";
  const columns = [
    col("bank", t.bank, "bank"),
    col("product", t.product, "text"),
    // Same column as the finance table, and it carries more here: in a gold
    // comparison two banks sell a dedicated gold account and three answer with
    // their ordinary one, which pays a different ratio entirely.
    col("basis", t.basis, "badge", { filterable: true }),
    col("net_profit", t.netProfit, "money", { currency }),
    col("gross_profit", t.grossProfit, "money", { currency }),
    col("net_annual_rate", t.netAnnual, "percent"),
    col("term", t.term, "number"),
    col("term_unit", t.termUnit, "badge"),
    col("currency", t.currency, "badge"),
  ];
  const rows: Row[] = (c.profit_share_quotes ?? []).map((q) => ({
    cells: {
      bank: q.bank,
      product: q.product?.name ?? "",
      basis: quoteBasis(q, t),
      net_profit: q.net_profit,
      gross_profit: q.gross_profit,
      net_annual_rate: q.net_annual_rate ?? null,
      term: q.term,
      term_unit: q.term_unit,
      currency: q.currency,
    },
  }));
  return { columns, rows };
}

/**
 * Conversion.
 *
 * The "Bank's own" / "Computed from rate" column is unmounted, not deleted:
 * `derived` still comes off the wire, `cells.source` below still turns it
 * into the right label, and the `computed`/`source` (bankOwn) strings are
 * still live translations. None of that needed to change to take the column
 * off the page -- only `col("source", ...)` is missing from `columns`, so
 * putting it back is the entire job of bringing it back.
 */
export function convertTable(c: Comparison, t: Labels) {
  const columns = [
    col("bank", t.bank, "bank"),
    // The same precision the FX board uses for a bank's own buy/sell rates,
    // and for the same reason: a TRY->XAU rate is a fraction of a gram per
    // lira, something like 0,0002, and the default whole-number formatting
    // rounded it to a literal "0" -- not a small rate, no rate at all. The
    // result carries the same risk once one side of the pair is a metal, so
    // both columns get it rather than only the one that broke first.
    col("result", t.result, "number", { decimals: RATE_DECIMALS }),
    col("rate", t.rate, "number", { decimals: RATE_DECIMALS }),
  ];
  const rows: Row[] = (c.conversions ?? []).map((q) => ({
    cells: {
      bank: q.bank,
      // Strings on the wire carry Decimal; Number() here is for sorting and
      // display only. `source` keeps the provenance attached to the row even
      // though no column currently renders it -- sorting or filtering logic
      // added later can still read it, and the column is one line to restore.
      result: Number(q.result),
      rate: Number(q.rate),
      source: q.derived ? t.computed : t.source,
    },
  }));
  return { columns, rows };
}

/**
 * Card instalments: the ranking table. Cheapest instalment first, same as
 * `financeTable`.
 *
 * Cards have no cross-bank family -- each bank sells its own catalogue under
 * its own names -- so every card every in-scope bank publishes is one row,
 * rather than the user picking a single bank and a single card up front and
 * getting one answer back with nothing to compare it against.
 */
export function cardTable(c: Comparison, t: Labels) {
  const columns = [
    col("bank", t.bank, "bank"),
    col("card", t.card, "text"),
    // Same column and the same reason as financeTable's: a bank that states
    // only a rate (Türkiye Finans) needs to say so where the empty payment
    // cells are, not just render two em dashes with no explanation.
    col("basis", t.basis, "badge", { filterable: true }),
    col("installments", t.installments, "number"),
    col("installment", t.instalment, "money"),
    col("total", t.total, "money"),
    col("profit_rate", t.profitRate, "percent"),
  ];
  const rows: Row[] = (c.card_quotes ?? []).map((q) => ({
    cells: {
      bank: q.bank,
      card: q.card?.name ?? "",
      basis: q.installment == null ? t.rateOnly : "",
      installments: q.installments,
      // Null where the bank publishes a rate but never states a payment
      // (Türkiye Finans). The table renders an em dash and sorting sinks it,
      // exactly as financeTable's installment column does.
      installment: q.installment ?? null,
      total: q.total ?? null,
      profit_rate: q.profit_rate,
    },
  }));
  return { columns, rows };
}

/**
 * The mile earning-rate table.
 *
 * Kuveyt Türk's table is 567 rows -- every card, tier and spending category.
 * Nothing to rank here (`CATEGORIES.miles.ranks === false`), and no bank
 * column either: this whole category is single-bank by construction (the
 * picker above it chooses the one bank), so repeating its name on all 567
 * rows would be the only column that never varies. `TableFilters` gives the
 * search box and the `card`/`tier`/`category` tick-lists the row count needs.
 */
export function mileRatesTable(rates: MileRate[], t: Labels) {
  const columns = [
    // Plain text, not a badge: a pill is for a state -- offered, declined,
    // derived -- something the reader distinguishes at a glance across rows
    // that otherwise look alike. Card, tier and category are just what the
    // row is about, three plain labels on 567 rows of them, and a coloured
    // chip on all three of every row is decoration doing no work.
    col("card", t.card, "text", { filterable: true }),
    col("tier", t.tier, "text", { filterable: true }),
    col("category", t.category, "text", { filterable: true }),
    // Real values run from 0,0015 to 1 mile per lira -- the default
    // whole-number formatting rounded almost every row to a bare "0" or "1",
    // the same class of bug the FX board and the converter already had fixed.
    col("per_lira", t.perLira, "number", { decimals: RATE_DECIMALS }),
  ];
  const rows: Row[] = rates.map((r) => ({
    cells: {
      card: r.card,
      tier: r.tier,
      // The feed spells spending categories lowercase ("akaryakit",
      // "yurtdisi") -- a sentence-case first letter is the one thing worth
      // fixing on display without pretending to translate or correct spelling
      // the bank did not publish.
      category: r.category ? r.category[0].toUpperCase() + r.category.slice(1) : r.category,
      per_lira: r.per_lira,
    },
  }));
  return { columns, rows };
}

// ----- rates -----

export type RateGroup = "currency" | "metal" | "coin" | "parity";

/**
 * Which shelf an instrument belongs on.
 *
 * `unit` carries most of it (`"1"` currency, `"gram"` metal, `"coin"` coin),
 * but a cross rate like EUR/USD arrives with `unit: "1"` and is **not** a TRY
 * price. Left in with the currencies it would quietly poison any "cheapest USD"
 * logic that keys on code alone.
 */
export function rateGroup(rate: Pick<Rate, "code" | "unit">): RateGroup {
  if (rate.code.includes("/")) return "parity";
  if (rate.unit === "gram") return "metal";
  if (rate.unit === "coin") return "coin";
  return "currency";
}

export interface BankRate extends Rate {
  bank: string;
}

/** Spread as a share of the mid price, in percent. Ours, not the bank's. */
export function spreadPct(buy: number, sell: number): number | null {
  const mid = (buy + sell) / 2;
  if (!Number.isFinite(mid) || mid <= 0) return null;
  return ((sell - buy) / mid) * 100;
}

/**
 * A spread this wide is a broken feed row, not a price.
 *
 * Kuveyt Türk publishes MYR at 7,59/15,78 and CNH at 4,60/9,56 — over 100%
 * apart. They are real rows from a real feed, so they are shown, but they must
 * never win a "narrowest spread" ranking or be treated as a quotable price.
 */
export const SPREAD_SANITY_PCT = 25;

export function spreadLooksBroken(buy: number, sell: number): boolean {
  const pct = spreadPct(buy, sell);
  return pct === null || pct > SPREAD_SANITY_PCT || pct < 0;
}

/**
 * One row per instrument, one column pair per bank — the board.
 *
 * Grouped on `(canonical, unit)`: gold is XAU at Albaraka and "ALT (gr)" at the
 * other two, and only the canonical symbol makes them the same row. Unit stays
 * in the key so a coin price can never share a row with a gram price.
 */
/**
 * How a bank writes the thing being priced: what you get, over what you pay.
 *
 * Every board here is quoted against the lira -- `buy` is what the bank pays
 * you for one unit, `sell` what it charges -- so a bare "USD" leaves the reader
 * to assume the direction. Turkish bank boards write the pair, and so do we:
 * `USD/TRY`, `XAU/TRY`.
 *
 * The unit rides alongside rather than inside the pair, because `XAU/TRY` is a
 * gram at one bank and an ounce at another, and burying that in the label is
 * how two different prices end up looking like the same one.
 */
export function unitLabel(unit: string, t: Labels): string {
  if (unit === "gram") return t.perGram;
  if (unit === "coin") return t.perCoin;
  return t.perUnit;
}

/**
 * How precise a rate is on screen.
 *
 * Banks quote four places, and five for the small ones -- the Iraqi dinar is
 * 0,03619. Two banks' dollar rates differ in the third and fourth place, which
 * is the entire point of putting them side by side, so the default whole
 * number did not merely look wrong: it rendered 47,4487 as "47" and made every
 * bank on the row look identical.
 */
export const RATE_DECIMALS = { min: 2, max: 6 } as const;

export function pairLabel(canonical: string): string {
  return `${canonical}/TRY`;
}

/**
 * The lira itself is not an instrument on a lira board.
 *
 * Kuveyt Türk publishes a `TL` row at 1,00 / 1,00 -- true, and worth nothing:
 * it is the unit everything else is measured in. Left in, it sorts among real
 * prices and reads as a quote.
 */
export function isSelfQuote(canonical: string): boolean {
  return canonical === "TRY";
}

export function ratesBoard(
  rates: BankRate[],
  banks: string[],
  t: Labels,
  bankLabels: Record<string, string> = {},
) {
  const groups = new Map<string, { canonical: string; unit: string; name: string; byBank: Map<string, BankRate> }>();

  for (const r of rates) {
    const canonical = r.canonical || r.code;
    if (isSelfQuote(canonical)) continue;
    const key = `${canonical}|${r.unit}`;
    if (!groups.has(key)) {
      groups.set(key, {
        canonical,
        unit: r.unit,
        name: r.name,
        byBank: new Map(),
      });
    }
    groups.get(key)!.byBank.set(r.bank, r);
  }

  // Widest board first. Kuveyt Türk quotes 27 instruments and Hayat 4, so
  // leaving the order to the registry puts a four-row bank in the first column
  // and its two blanks are the first thing anyone reads. Ties break on name so
  // the columns do not reshuffle between polls.
  const ordered = orderByCoverage(rates, banks);

  const columns: ResolvedColumn[] = [
    // The pair, so the direction is on the row rather than assumed.
    col("instrument", t.instrument, "text", { filterable: true, sortable: true }),
    // What one of it is. A gram of gold and an ounce of gold are both "XAU/TRY"
    // and are not the same price, so the unit is a column, not a footnote.
    // Sortable too, which is how the grams are brought together.
    col("unit", t.unit, "badge", { filterable: true, sortable: true }),
  ];
  for (const bank of ordered) {
    // Just "Alış" and "Satış" here: the bank's name sits above the pair in the
    // group row, so repeating it on each column would read as two banks rather
    // than one bank's two prices.
    // Sortable, like every other numeric column. Worth knowing what it does
    // on a board of mixed instruments: ordering by one bank's price ranks a
    // quarter gold coin above a gram of gold above the yen, because those are
    // different things measured in different units. It is most useful once the
    // pair or unit filter has narrowed the board to comparable rows.
    // Left, against the numeric default of right. The heading then begins
    // where its figures begin, instead of the label sitting hard against the
    // next bank's column while the prices under it start somewhere else.
    columns.push(col(`${bank}__buy`, t.buy, "number",
      { decimals: RATE_DECIMALS, align: "left" }));
    columns.push(col(`${bank}__sell`, t.sell, "number",
      { decimals: RATE_DECIMALS, align: "left" }));
  }

  // The header above the columns: one cell per bank, spanning its buy and sell.
  // The two leading columns belong to no bank, so they take an empty filler --
  // spans have to add up to the column count or the row misaligns.
  const headerGroups = [
    { key: "instrument", label: "", span: 1 },
    { key: "unit", label: "", span: 1 },
    ...ordered.map((bank) => ({
      key: bank,
      label: (bankLabels[bank] ?? bank).toUpperCase(),
      span: 2,
    })),
  ];

  const rows: Row[] = [...groups.values()]
    // Rows every bank quotes first: those are the ones worth comparing, and
    // the single-source tail is real data, not a gap, so it sits below rather
    // than being dropped.
    .sort((a, b) => b.byBank.size - a.byBank.size || a.name.localeCompare(b.name, "tr"))
    .map((g) => {
      const cells: Row["cells"] = {
        instrument: pairLabel(g.canonical),
        unit: unitLabel(g.unit, t),
      };
      for (const bank of ordered) {
        const r = g.byBank.get(bank);
        cells[`${bank}__buy`] = r ? r.buy : null;
        cells[`${bank}__sell`] = r ? r.sell : null;
      }
      return { cells };
    });

  // Which bank is best on each row, in both directions. `bestRates` has been
  // here and tested since the board was built and was never wired to anything,
  // which left the page showing six prices and no answer to the question the
  // user came with.
  const best: Record<string, true> = {};
  for (const group of groups.values()) {
    const quotes = [...group.byBank.values()];
    const { bestBuy, bestSell } = bestRates(quotes);
    const pair = pairLabel(group.canonical);
    if (bestBuy) best[`${pair}|${bestBuy.bank}__buy`] = true;
    if (bestSell) best[`${pair}|${bestSell.bank}__sell`] = true;
  }

  return { columns, rows, banks: ordered, groups: headerGroups, best };
}

/**
 * Banks ordered by how much of the board they actually quote.
 *
 * Pure and separate so the ordering can be tested without building a table,
 * and stable: a bank that gains an instrument between polls must not make the
 * columns jump, so ties break on the bank key rather than on arrival order.
 */
export function orderByCoverage(rates: BankRate[], banks: string[]): string[] {
  const counts = new Map<string, number>();
  for (const r of rates) counts.set(r.bank, (counts.get(r.bank) ?? 0) + 1);
  return [...banks].sort(
    (a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0) || a.localeCompare(b),
  );
}

/**
 * What moved since the last poll, per cell.
 *
 * The board refreshes on a timer, and a number that changed silently is a
 * number nobody notices. Returns `up` / `down` per `rowKey|columnKey` so the
 * table can flash the cell the way a trading screen does; a cell that did not
 * move is absent rather than "same", so the caller renders nothing for it.
 *
 * Keyed on the instrument rather than the row index: rows are sorted by how
 * many banks quote them, so an index means a different instrument the moment
 * a bank adds one.
 */
export function movements(
  previous: readonly Row[] | null,
  next: readonly Row[],
): Record<string, "up" | "down"> {
  if (!previous?.length) return {};
  const before = new Map(previous.map((r) => [String(r.cells.instrument ?? ""), r.cells]));
  const moved: Record<string, "up" | "down"> = {};
  for (const row of next) {
    const key = String(row.cells.instrument ?? "");
    const old = before.get(key);
    if (!old) continue;
    for (const [column, value] of Object.entries(row.cells)) {
      const was = old[column];
      if (typeof value !== "number" || typeof was !== "number" || value === was) continue;
      moved[`${key}|${column}`] = value > was ? "up" : "down";
    }
  }
  return moved;
}

/**
 * Best buy and best sell for one instrument, and they point opposite ways.
 *
 * The bank's `buy` is what it pays you, so the best is the **highest**. Its
 * `sell` is what it charges you, so the best is the **lowest**. A single "best
 * rate" crown would be wrong half the time.
 *
 * Only meaningful with two or more quotes, and broken-spread rows are excluded
 * rather than allowed to win.
 */
export function bestRates(quotes: BankRate[]) {
  const usable = quotes.filter((q) => !spreadLooksBroken(q.buy, q.sell));
  if (usable.length < 2) return { bestBuy: null, bestSell: null };
  const bestBuy = usable.reduce((a, b) => (b.buy > a.buy ? b : a));
  const bestSell = usable.reduce((a, b) => (b.sell < a.sell ? b : a));
  return { bestBuy, bestSell };
}
