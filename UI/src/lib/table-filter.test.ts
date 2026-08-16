import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { TablePropsSchema, resolveTable } from "./contract.ts";
import {
  EMPTY_FILTERS,
  applyFilters,
  distinctValues,
  filterKind,
  searchableKeys,
  sortRows,
  type FilterState,
} from "./table-filter.ts";

const table = resolveTable(
  TablePropsSchema.parse({
    columns: [
      { key: "banka", label: "Banka", type: "bank" },
      { key: "urun", label: "Ürün", type: "text", filterable: true },
      { key: "tutar", label: "Tutar", type: "money" },
      { key: "tarih", label: "Tarih", type: "date" },
      { key: "aktif", label: "Aktif", type: "bool" },
    ],
    rows: [
      { cells: { banka: "kuveytturk", urun: "İhtiyaç", tutar: 1500, tarih: "2026-07-01", aktif: true } },
      { cells: { banka: "albaraka", urun: "Konut", tutar: 900, tarih: "2026-08-01", aktif: false } },
      { cells: { banka: "vakif", urun: "ihtiyaç", tutar: 2500, tarih: "2026-06-01", aktif: true } },
      // Deliberately missing tutar — the "unknown" case every filter must handle.
      { cells: { banka: "emlak", urun: "Taşıt", tarih: "2026-09-01", aktif: false } },
    ],
  }),
);

const filters = (patch: Partial<FilterState>): FilterState => ({
  ...EMPTY_FILTERS,
  ...patch,
});

const banks = (rows: { cells: Record<string, unknown> }[]) =>
  rows.map((r) => r.cells.banka);

describe("filterKind", () => {
  it("offers a range for numbers and a tick-list for categories", () => {
    const by = (key: string) =>
      filterKind(table.columns.find((c) => c.key === key)!, table.rows);
    assert.equal(by("tutar"), "range");
    assert.equal(by("banka"), "select");
    assert.equal(by("urun"), "select");
    assert.equal(by("tarih"), "none");
  });

  it("declines a tick-list with too many distinct values", () => {
    const wide = resolveTable(
      TablePropsSchema.parse({
        columns: [{ key: "not", type: "text", filterable: true }],
        rows: Array.from({ length: 60 }, (_, i) => ({ cells: { not: `cümle ${i}` } })),
      }),
    );
    assert.equal(filterKind(wide.columns[0], wide.rows), "none");
    assert.deepEqual(distinctValues(wide.rows, "not"), []);
  });

  it("skips blanks when listing values", () => {
    assert.deepEqual(distinctValues(table.rows, "tutar"), ["1500", "2500", "900"]);
  });
});

describe("applyFilters", () => {
  it("returns everything when nothing is set", () => {
    assert.equal(applyFilters(table.rows, table.columns, EMPTY_FILTERS).length, 4);
  });

  it("matches Turkish case correctly", () => {
    // "İhtiyaç" and "ihtiyaç" differ only by the dotted capital, which a naive
    // toLowerCase() gets wrong. Both rows must match.
    const hits = applyFilters(table.rows, table.columns, filters({ search: "İHTİYAÇ" }));
    assert.deepEqual(banks(hits), ["kuveytturk", "vakif"]);
  });

  it("searches text, badge and bank columns only", () => {
    assert.deepEqual(searchableKeys(table.columns), ["banka", "urun"]);
    // A date is not searched — "2026" must not sweep the whole table.
    assert.equal(applyFilters(table.rows, table.columns, filters({ search: "2026" })).length, 0);
  });

  it("filters by ticked values", () => {
    const hits = applyFilters(
      table.rows,
      table.columns,
      filters({ values: { banka: ["albaraka", "emlak"] } }),
    );
    assert.deepEqual(banks(hits), ["albaraka", "emlak"]);
  });

  it("treats an empty selection as no filter", () => {
    assert.equal(
      applyFilters(table.rows, table.columns, filters({ values: { banka: [] } })).length,
      4,
    );
  });

  it("applies numeric bounds and excludes unknown values", () => {
    const hits = applyFilters(
      table.rows,
      table.columns,
      filters({ ranges: { tutar: { max: 2000 } } }),
    );
    // emlak has no tutar: "we don't know" is not "under 2000".
    assert.deepEqual(banks(hits), ["kuveytturk", "albaraka"]);
  });

  it("combines filters as AND", () => {
    const hits = applyFilters(
      table.rows,
      table.columns,
      filters({ search: "ihtiyaç", ranges: { tutar: { min: 2000 } } }),
    );
    assert.deepEqual(banks(hits), ["vakif"]);
  });
});

describe("sortRows", () => {
  it("leaves order alone when unsorted", () => {
    assert.deepEqual(banks(sortRows(table.rows, null, table.columns)), [
      "kuveytturk",
      "albaraka",
      "vakif",
      "emlak",
    ]);
  });

  it("sorts numbers numerically, not as strings", () => {
    const asc = sortRows(table.rows, { key: "tutar", direction: "asc" }, table.columns);
    assert.deepEqual(banks(asc).slice(0, 3), ["albaraka", "kuveytturk", "vakif"]);
  });

  it("sinks blanks to the bottom in both directions", () => {
    const asc = sortRows(table.rows, { key: "tutar", direction: "asc" }, table.columns);
    const desc = sortRows(table.rows, { key: "tutar", direction: "desc" }, table.columns);
    assert.equal(asc.at(-1)!.cells.banka, "emlak");
    assert.equal(desc.at(-1)!.cells.banka, "emlak", "unknown is not the largest value");
  });

  it("sorts ISO dates chronologically", () => {
    const asc = sortRows(table.rows, { key: "tarih", direction: "asc" }, table.columns);
    assert.deepEqual(banks(asc), ["vakif", "kuveytturk", "albaraka", "emlak"]);
  });

  it("sorts text with Turkish collation", () => {
    const asc = sortRows(table.rows, { key: "urun", direction: "asc" }, table.columns);
    // "İhtiyaç"/"ihtiyaç" collate together, ahead of Konut and Taşıt.
    assert.deepEqual(banks(asc).slice(2), ["albaraka", "emlak"]);
  });

  it("does not mutate the input", () => {
    const before = banks(table.rows);
    sortRows(table.rows, { key: "tutar", direction: "desc" }, table.columns);
    assert.deepEqual(banks(table.rows), before);
  });

  it("ignores a sort on a column that is not there", () => {
    const same = sortRows(table.rows, { key: "yok", direction: "asc" }, table.columns);
    assert.deepEqual(banks(same), banks(table.rows));
  });

  it("sorts a bank column by its provider key when no label map is given", () => {
    const asc = sortRows(table.rows, { key: "banka", direction: "asc" }, table.columns, "tr");
    assert.deepEqual(banks(asc), ["albaraka", "emlak", "kuveytturk", "vakif"]);
  });

  it("sorts a bank column by the name shown on screen, not the provider key", () => {
    // The cell holds "kuveytturk"; the reader sees "Kuveyt Türk Katılım
    // Bankası". Sorting on the raw key would put Emlak ahead of Kuveyt Türk
    // and call it alphabetical -- correct for a string nobody reads, wrong
    // for the column as displayed.
    const labels: Record<string, string> = {
      kuveytturk: "Kuveyt Türk Katılım Bankası",
      albaraka: "Albaraka Türk Katılım Bankası",
      vakif: "Vakıf Katılım Bankası",
      emlak: "Türkiye Emlak Katılım Bankası",
    };
    const asc = sortRows(
      table.rows, { key: "banka", direction: "asc" }, table.columns, "tr", labels,
    );
    assert.deepEqual(banks(asc), ["albaraka", "kuveytturk", "emlak", "vakif"]);
  });
});
