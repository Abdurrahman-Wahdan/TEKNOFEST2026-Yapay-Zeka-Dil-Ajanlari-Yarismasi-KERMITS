import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ComponentsResponseSchema,
  MAX_ROWS,
  TablePropsSchema,
  inferColumnType,
  inferColumns,
  resolveTable,
  type Row,
} from "./contract.ts";

const row = (cells: Row["cells"], cite_url?: string): Row =>
  cite_url ? { cells, cite_url } : { cells };

describe("inferColumnType", () => {
  it("reads booleans, numbers and banks", () => {
    assert.equal(inferColumnType([true, false, true]), "bool");
    assert.equal(inferColumnType([1, 2, 3.5]), "number");
    assert.equal(inferColumnType(["kuveytturk", "albaraka"]), "bank");
  });

  it("reads links and ISO dates", () => {
    assert.equal(inferColumnType(["https://a.example", "http://b.example"]), "link");
    assert.equal(inferColumnType(["2026-08-14", "2026-01-02T10:00:00Z"]), "date");
  });

  it("falls back to text for mixed or unknown values", () => {
    assert.equal(inferColumnType(["kuveytturk", 4]), "text");
    assert.equal(inferColumnType(["Son 3 ay bordro"]), "text");
    // A Turkish-formatted date is not ISO, and guessing would sort it wrongly.
    assert.equal(inferColumnType(["14.08.2026"]), "text");
  });

  it("treats an all-empty column as text rather than guessing", () => {
    assert.equal(inferColumnType([null, "", null]), "text");
    assert.equal(inferColumnType([]), "text");
  });

  it("ignores blanks when deciding", () => {
    assert.equal(inferColumnType([null, 3, null, 5]), "number");
  });
});

describe("inferColumns", () => {
  it("collects keys in first-seen order across every row", () => {
    const columns = inferColumns([
      row({ banka: "vakif", vade: 12 }),
      // `aciklama` appears only here — it must still become a column, in the
      // position it first showed up.
      row({ banka: "emlak", vade: 24, aciklama: "Azami" }),
    ]);
    assert.deepEqual(
      columns.map((c) => c.key),
      ["banka", "vade", "aciklama"],
    );
    assert.deepEqual(
      columns.map((c) => c.type),
      ["bank", "number", "text"],
    );
  });

  it("uses the key as the label when there is nothing better", () => {
    assert.equal(inferColumns([row({ belge: "Kimlik" })])[0].label, "belge");
  });
});

describe("resolveTable", () => {
  it("renders a table that arrived with no columns at all", () => {
    const parsed = TablePropsSchema.parse({
      rows: [{ cells: { belge: "Kimlik", bireysel: true } }],
    });
    const resolved = resolveTable(parsed);
    assert.deepEqual(
      resolved.columns.map((c) => `${c.key}:${c.type}`),
      ["belge:text", "bireysel:bool"],
    );
    assert.ok(resolved.columns.every((c) => c.inferred));
  });

  it("keeps rows whose cells are missing", () => {
    const parsed = TablePropsSchema.parse({
      columns: [{ key: "banka", type: "bank" }, { key: "dosya", type: "money" }],
      rows: [{ cells: { banka: "vakif" } }, { cells: { banka: "emlak", dosya: 1100 } }],
    });
    const resolved = resolveTable(parsed);
    // Both rows survive; the renderer shows a dash where the cell is absent.
    assert.equal(resolved.rows.length, 2);
    assert.equal(resolved.rows[0].cells.dosya, undefined);
  });

  it("ignores an unknown column type, infers instead, and says so", () => {
    const parsed = TablePropsSchema.parse({
      columns: [{ key: "tutar", label: "Tutar", type: "currency" }],
      rows: [{ cells: { tutar: 100 } }],
    });
    const resolved = resolveTable(parsed);
    // Inference beats a blanket text fallback: the values are numbers, so the
    // column still right-aligns and sorts numerically.
    assert.equal(resolved.columns[0].type, "number");
    assert.equal(resolved.columns[0].inferred, true);
    assert.match(resolved.warnings.join(" "), /currency/);
  });

  it("truncates a runaway table instead of freezing the tab", () => {
    const parsed = TablePropsSchema.parse({
      rows: Array.from({ length: MAX_ROWS + 25 }, (_, i) => ({ cells: { n: i } })),
    });
    const resolved = resolveTable(parsed);
    assert.equal(resolved.rows.length, MAX_ROWS);
    assert.match(resolved.warnings.join(" "), new RegExp(String(MAX_ROWS)));
  });

  it("flags a column that no row fills", () => {
    const parsed = TablePropsSchema.parse({
      columns: [{ key: "banka" }, { key: "tahsis", label: "Tahsis" }],
      rows: [{ cells: { banka: "vakif" } }],
    });
    assert.match(resolveTable(parsed).warnings.join(" "), /Tahsis/);
  });

  it("reports whether anything is citable", () => {
    const cited = TablePropsSchema.parse({
      rows: [{ cells: { a: 1 }, cite_url: "https://x.example" }],
    });
    const uncited = TablePropsSchema.parse({ rows: [{ cells: { a: 1 } }] });
    assert.equal(resolveTable(cited).uncited, false);
    assert.equal(resolveTable(uncited).uncited, true);
    // An empty table is not "uncited" — there is nothing to cite.
    assert.equal(resolveTable(TablePropsSchema.parse({ rows: [] })).uncited, false);
  });

  it("defaults alignment and affordances from the column type", () => {
    const parsed = TablePropsSchema.parse({
      columns: [
        { key: "banka", type: "bank" },
        { key: "tutar", type: "money" },
        { key: "not", type: "text" },
      ],
      rows: [{ cells: { banka: "vakif", tutar: 1, not: "x" } }],
    });
    const [bank, money, text] = resolveTable(parsed).columns;
    assert.equal(money.align, "right");
    assert.equal(money.sortable, true);
    assert.equal(bank.filterable, true, "a bank column is always worth filtering");
    assert.equal(text.sortable, false, "sorting a prose column is noise");
    assert.equal(money.currency, "TRY");
  });

  it("respects an explicit choice over the default", () => {
    const parsed = TablePropsSchema.parse({
      columns: [
        { key: "not", type: "text", sortable: true, filterable: true, align: "right" },
        { key: "tutar", type: "money", currency: "USD" },
      ],
      rows: [{ cells: { not: "x", tutar: 1 } }],
    });
    const [text, money] = resolveTable(parsed).columns;
    assert.equal(text.sortable, true);
    assert.equal(text.filterable, true);
    assert.equal(text.align, "right");
    assert.equal(money.currency, "USD");
  });
});

describe("schemas", () => {
  it("requires rows and nothing else", () => {
    assert.ok(TablePropsSchema.safeParse({ rows: [] }).success);
    assert.ok(!TablePropsSchema.safeParse({ title: "no rows" }).success);
  });

  it("rejects a row whose cells are not an object", () => {
    const result = TablePropsSchema.safeParse({ rows: [{ cells: "nope" }] });
    assert.ok(!result.success);
    // The path is what the BrokenWidget shows the user, so it has to be useful.
    assert.deepEqual(result.error!.issues[0].path, ["rows", 0, "cells"]);
  });

  it("accepts null cells — absent means not found, not zero", () => {
    assert.ok(TablePropsSchema.safeParse({ rows: [{ cells: { a: null } }] }).success);
  });

  it("fills in envelope defaults", () => {
    const parsed = ComponentsResponseSchema.parse({ category: "finansman" });
    assert.deepEqual(parsed.components, []);
    assert.equal(parsed.source, "fixture");
    assert.equal(parsed.generated_at, "");
  });

  it("keeps an unknown component type for the renderer to report", () => {
    const parsed = ComponentsResponseSchema.parse({
      category: "finansman",
      components: [{ type: "timeline", props: { anything: true } }],
    });
    // Validation must not silently drop it — the user is told what was asked for.
    assert.equal(parsed.components[0].type, "timeline");
  });
});
