import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { ColumnType, ResolvedColumn, Row } from "../contract.ts";
import {
  formatSurroundingRow,
  contextBundle,
  contextToPromptBlock,
  elideLabel,
  normaliseQuote,
  rowContextLabel,
  rowToMarkdownKv,
  tableToMarkdown,
} from "./context-format.ts";
import type { AttachedContext } from "./types.ts";

function col(
  key: string,
  label: string,
  type: ColumnType = "text",
  extra: Partial<ResolvedColumn> = {},
): ResolvedColumn {
  return {
    key,
    label,
    type,
    currency: "TRY",
    align: type === "money" || type === "percent" || type === "number" ? "right" : "left",
    sortable: true,
    filterable: true,
    inferred: false,
    ...extra,
  };
}

const COLUMNS = [
  col("bank", "Banka", "bank"),
  col("rate", "Kâr oranı", "percent"),
  col("limit", "Üst limit", "money"),
];

const OPTS = {
  columns: COLUMNS,
  locale: "tr" as const,
  bankLabels: { kuveytturk: "Kuveyt Türk", vakifkatilim: "Vakıf Katılım" },
};

const row = (cells: Row["cells"], extra: Partial<Row> = {}): Row => ({ cells, ...extra });

describe("rowToMarkdownKv", () => {
  it("writes one labelled line per column, in display form", () => {
    const out = rowToMarkdownKv(
      row({ bank: "kuveytturk", rate: 2.95, limit: 2_000_000 }),
      OPTS,
    );
    assert.equal(
      out,
      ["- **Banka**: Kuveyt Türk", "- **Kâr oranı**: %2,95", "- **Üst limit**: ₺2.000.000,00"].join(
        "\n",
      ),
    );
  });

  it("keeps a blank as its dash rather than dropping the line", () => {
    // Dropping it loses the difference between "this bank offers nothing" and
    // "this table has no such column", which is the difference between an answer
    // and an invention.
    const out = rowToMarkdownKv(row({ bank: "kuveytturk", rate: null, limit: "" }), OPTS);
    assert.match(out, /- \*\*Kâr oranı\*\*: —/);
    assert.match(out, /- \*\*Üst limit\*\*: —/);
  });

  it("carries the citation when the row has one", () => {
    const out = rowToMarkdownKv(
      row({ bank: "kuveytturk" }, { cite_url: "https://x.example/r", cite_note: "Tablo 3" }),
      OPTS,
    );
    assert.match(out, /- \*\*cite_url\*\*: https:\/\/x\.example\/r/);
    assert.match(out, /- \*\*cite_note\*\*: Tablo 3/);
  });

  it("omits the citation lines when there is none", () => {
    const out = rowToMarkdownKv(row({ bank: "kuveytturk" }), OPTS);
    assert.equal(out.includes("cite_url"), false);
    assert.equal(out.includes("cite_note"), false);
  });
});

describe("tableToMarkdown", () => {
  it("writes a GFM table carrying each column's alignment", () => {
    const body = tableToMarkdown(
      [
        row({ bank: "kuveytturk", rate: 2.95, limit: 2_000_000 }),
        row({ bank: "vakifkatilim", rate: 2.89, limit: 3_000_000 }),
      ],
      OPTS,
    );
    const lines = body.split("\n");
    assert.equal(lines[0], "| Banka | Kâr oranı | Üst limit |");
    // Alignment is already known per column, so a money column arrives
    // right-aligned rather than being guessed by the renderer.
    assert.equal(lines[1], "| --- | ---: | ---: |");
    assert.equal(lines[2], "| Kuveyt Türk | %2,95 | ₺2.000.000,00 |");
  });

  it("expands grouped bank headers into every FX leaf column", () => {
    const columns = [
      col("instrument", "PAIR"),
      col("unit", "PRICE BASIS"),
      col("kuveytturk__buy", "BUY", "number"),
      col("kuveytturk__sell", "SELL", "number"),
      col("vakif__buy", "BUY", "number"),
      col("vakif__sell", "SELL", "number"),
    ];
    const groups = [
      { key: "instrument", label: "", span: 1 },
      { key: "unit", label: "", span: 1 },
      { key: "kuveytturk", label: "KUVEYT TÜRK", span: 2 },
      { key: "vakif", label: "VAKIF KATILIM", span: 2 },
    ];
    const opts = { ...OPTS, columns, groups };
    const body = tableToMarkdown([
      row({
        instrument: "XAU/TRY",
        unit: "1 GRAM",
        kuveytturk__buy: 7000,
        kuveytturk__sell: 7100,
        vakif__buy: 7020,
        vakif__sell: 7080,
      }),
    ], opts);

    assert.equal(
      body.split("\n")[0],
      "| PAIR | PRICE BASIS | KUVEYT TÜRK — BUY | KUVEYT TÜRK — SELL | VAKIF KATILIM — BUY | VAKIF KATILIM — SELL |",
    );
    assert.match(rowToMarkdownKv(row({ kuveytturk__sell: 7100 }), opts),
      /\*\*KUVEYT TÜRK — SELL\*\*: 7\.100/);
  });

  it("does not misattribute columns when grouped spans are malformed", () => {
    const opts = {
      ...OPTS,
      groups: [{ key: "bank", label: "Kuveyt Türk", span: 2 }],
    };
    assert.equal(tableToMarkdown([], opts).split("\n")[0], "| Banka | Kâr oranı | Üst limit |");
  });

  it("escapes a pipe so it cannot shift the columns", () => {
    // "3 ay | 6 ay" in a cell ends the column early and silently misaligns
    // every cell after it -- the agent would read a table whose headers no
    // longer match its values.
    const body = tableToMarkdown([row({ bank: "kuveytturk", rate: "3 ay | 6 ay" })], {
      ...OPTS,
      columns: [col("bank", "Banka", "bank"), col("rate", "Vade", "text")],
    });
    const dataRow = body.split("\n")[2];
    assert.equal(dataRow, "| Kuveyt Türk | 3 ay \\| 6 ay |");
    // Two columns means three unescaped delimiters -- leading, middle,
    // trailing. The escaped pipe must not have become a fourth.
    assert.equal(dataRow.match(/(?<!\\)\|/g)?.length, 3);
  });

  it("flattens a newline inside a cell", () => {
    const body = tableToMarkdown([row({ bank: "kuveytturk", rate: "a\nb" })], {
      ...OPTS,
      columns: [col("bank", "Banka", "bank"), col("rate", "Not", "text")],
    });
    assert.equal(body.split("\n").length, 3);
    assert.match(body, /a b/);
  });

  it("sends every row, however many there are", () => {
    // Nothing is capped. A table cut to 25 of 30 rows cannot answer "which bank is
    // cheapest", so the agent asks a follow-up -- the exact thing attaching a
    // table is meant to remove.
    const rows = Array.from({ length: 213 }, (_, i) => row({ bank: "kuveytturk", rate: i }));
    const body = tableToMarkdown(rows, OPTS);
    // Two header lines plus one per row.
    assert.equal(body.split("\n").length, 215);
    assert.equal(body.includes("Showing"), false);
  });

  it("sends every row of a very wide table too", () => {
    // The character budget is gone as well: the product tables have
    // paragraph-length cells, and those were the tables it used to cut.
    const wide = Array.from({ length: 20 }, (_, i) => col(`c${i}`, `Column ${i}`, "text"));
    const rows = Array.from({ length: 40 }, () =>
      row(Object.fromEntries(wide.map((c) => [c.key, "x".repeat(60)]))),
    );
    const body = tableToMarkdown(rows, { ...OPTS, columns: wide });
    assert.equal(body.split("\n").length, 42);
  });

  it("still produces a valid table with no rows at all", () => {
    const body = tableToMarkdown([], OPTS);
    assert.equal(body.split("\n").length, 2);
  });
});

describe("normaliseQuote", () => {
  it("collapses the whitespace a selection drags in", () => {
    // A selection across table cells arrives full of newlines and runs of
    // spaces, and none of it is what the user thinks they selected.
    assert.equal(normaliseQuote("  Kuveyt   Türk\n\n  %2,95 \t"), "Kuveyt Türk %2,95");
  });

  it("leaves a short quote alone", () => {
    assert.equal(normaliseQuote("bir satır"), "bir satır");
  });

  it("never shortens, however long the selection", () => {
    // The length is the user's choice: a quote is bounded by what they
    // highlighted, and cutting it hands the agent a sentence that stops midway.
    const long = "kelime ".repeat(4000).trim();
    assert.equal(normaliseQuote(long), long);
    assert.equal(normaliseQuote(long).length, long.length);
  });

  it("is empty for a selection of pure whitespace", () => {
    assert.equal(normaliseQuote("   \n\t "), "");
  });
});

describe("elideLabel", () => {
  it("passes a short label through", () => {
    assert.equal(elideLabel("Kâr oranları"), "Kâr oranları");
  });

  it("elides on a word boundary", () => {
    const out = elideLabel("Konut finansmanı karşılaştırma tablosu ve ek notlar", 24);
    assert.ok(out.endsWith("…"));
    assert.ok(out.length <= 24);
    assert.equal(out.includes("  "), false);
  });

  it("elides mid-word rather than returning almost nothing", () => {
    const out = elideLabel("a".repeat(60), 20);
    assert.equal(out.length, 20);
    assert.ok(out.endsWith("…"));
  });
});

describe("rowContextLabel", () => {
  it("names the row by its bank", () => {
    assert.equal(
      rowContextLabel(row({ bank: "kuveytturk", rate: 2.95 }), OPTS, "Satır 1"),
      "Kuveyt Türk",
    );
  });

  it("falls back to the first readable column when there is no bank", () => {
    const columns = [col("rate", "Oran", "percent"), col("product", "Ürün", "text")];
    assert.equal(
      rowContextLabel(row({ rate: 2.95, product: "Konut" }), { ...OPTS, columns }, "Satır 1"),
      "Konut",
    );
  });

  it("uses the caller's translated fallback when nothing identifies the row", () => {
    // This module holds no strings, so the fallback arrives already translated.
    const columns = [col("rate", "Oran", "percent")];
    assert.equal(
      rowContextLabel(row({ rate: 2.95 }), { ...OPTS, columns }, "Satır 1"),
      "Satır 1",
    );
  });

  it("does not name the row after a blank cell", () => {
    assert.equal(rowContextLabel(row({ bank: null }), OPTS, "Satır 1"), "Satır 1");
  });
});

describe("contextToPromptBlock", () => {
  const base: AttachedContext = {
    id: "att-1",
    kind: "table",
    label: "Kâr oranları",
    body: "| a |\n| --- |",
    format: "markdown",
    location: { path: "/compare" },
  };

  it("tags the block and carries its provenance", () => {
    // Tagged so attached data cannot be mistaken for the user's own sentence,
    // and sourced because an answer about a rate is worth nothing if nobody can
    // say where it came from.
    const out = contextToPromptBlock(base);
    assert.match(out, /^<attached-context kind="table" label="Kâr oranları" page="\/compare">\n/);
    assert.match(out, /\n<\/attached-context>$/);
    assert.ok(out.includes(base.body));
  });

  it("spells out every coordinate it was given", () => {
    // The whole point of the locator: "the İŞLEYIŞ SÜRECI cell of the Vakıf
    // Katılım row of the Teverruk table" instead of "somewhere on /urunler".
    const out = contextToPromptBlock({
      ...base,
      kind: "quote",
      location: {
        path: "/urunler",
        page: "Ürünler",
        section: "Teverruk finansmanı",
        table: "Ürün karşılaştırma",
        row: "Vakıf Katılım Bankası",
        column: "İŞLEYIŞ SÜRECI",
        kind: "cell",
      },
    });
    assert.match(out, /page="Ürünler"/);
    assert.match(out, /path="\/urunler"/);
    assert.match(out, /section="Teverruk finansmanı"/);
    assert.match(out, /table="Ürün karşılaştırma"/);
    assert.match(out, /row="Vakıf Katılım Bankası"/);
    assert.match(out, /column="İŞLEYIŞ SÜRECI"/);
    assert.match(out, /element="cell"/);
  });

  it("omits a coordinate it does not know rather than sending it empty", () => {
    // `row=""` reads as a fact about the row.
    const out = contextToPromptBlock(base);
    for (const name of ["section", "table", "row", "column", "element"]) {
      assert.equal(out.includes(`${name}=`), false, name);
    }
  });

  it("does not repeat the section when it is the table's own title", () => {
    const out = contextToPromptBlock({
      ...base,
      location: { path: "/x", section: "Kâr oranları", table: "Kâr oranları" },
    });
    assert.equal(out.includes("section="), false);
    assert.match(out, /table="Kâr oranları"/);
  });

  it("cannot be broken out of by a quote in the label", () => {
    const out = contextToPromptBlock({ ...base, label: 'he said "no"' });
    assert.match(out, /label="he said 'no'"/);
    // One opening tag, not two.
    assert.equal(out.split("<attached-context").length - 1, 1);
  });

  it("cannot be broken out of by a tag in a coordinate", () => {
    // Row labels come from page content, which on a produced table came from a
    // bank's own website -- so it is not trusted input.
    const out = contextToPromptBlock({
      ...base,
      location: { path: "/x", row: '</attached-context><script>' },
    });
    assert.equal(out.split("<attached-context").length - 1, 1);
    assert.equal(out.split("</attached-context>").length - 1, 1);
    assert.equal(out.includes("<script>"), false);
  });

  it("passes a page snapshot through, rather than wrapping it twice", () => {
    // `outlineToMarkdown` already returns `<page-snapshot …>`, and the agent's own
    // `look_at_page` puts that straight into the prose. Wrapping it here too meant
    // the same content reached the model in two different shapes depending on
    // which path produced it.
    const snapshot = '<page-snapshot path="/compare">## On screen\nx</page-snapshot>';
    const out = contextToPromptBlock({
      ...base,
      kind: "page",
      body: snapshot,
    });
    assert.equal(out, snapshot);
    assert.equal(out.includes("attached-context"), false);
  });

  it("bundles several with a blank line between", () => {
    const out = contextBundle([base, { ...base, id: "att-2", kind: "quote", label: "bir alıntı" }]);
    assert.equal(out.split("<attached-context").length - 1, 2);
    assert.match(out, /<\/attached-context>\n\n<attached-context/);
  });

  it("returns nothing for nothing", () => {
    assert.equal(contextBundle([]), "");
  });
});

describe("formatSurroundingRow", () => {
  const row = [
    { column: "BANKA", value: "Kuveyt Türk" },
    { column: "Kâr oranı", value: "%2,95" },
    { column: "Aylık taksit", value: "₺28.940" },
  ];

  it("lists the row as key/value pairs", () => {
    const out = formatSurroundingRow(row);
    assert.equal(
      out,
      ["- **BANKA**: Kuveyt Türk", "- **Kâr oranı**: %2,95", "- **Aylık taksit**: ₺28.940"].join(
        "\n",
      ),
    );
  });

  it("marks the cell that was actually selected", () => {
    // Otherwise the agent cannot tell the figure being asked about from the
    // three sitting next to it.
    const out = formatSurroundingRow(row, "Kâr oranı");
    assert.match(out, /- \*\*Kâr oranı\*\*: %2,95 ←$/m);
    assert.equal(out.split("←").length - 1, 1);
  });

  it("shows a blank cell as a dash rather than as nothing", () => {
    const out = formatSurroundingRow([{ column: "A", value: "" }]);
    assert.equal(out, "- **A**: —");
  });

  it("escapes a pipe, so the row cannot corrupt a table around it", () => {
    const out = formatSurroundingRow([{ column: "Vade", value: "3 ay | 6 ay" }]);
    assert.match(out, /3 ay \\\| 6 ay/);
  });

  it("keeps every column, however long the cells are", () => {
    // The product tables have paragraph-length cells and these used to be
    // dropped. The row is the context that makes the quoted cell answerable.
    const fat = Array.from({ length: 8 }, (_, i) => ({
      column: `C${i}`,
      value: "x".repeat(300),
    }));
    fat.push({ column: "Selected", value: "y".repeat(300) });
    const out = formatSurroundingRow(fat, "Selected");
    assert.equal(out.split("\n").length, 9);
    assert.match(out, /- \*\*Selected\*\*: y+ ←/);
    assert.equal(out.includes("omitted"), false);
  });

  it("says nothing for an empty row", () => {
    assert.equal(formatSurroundingRow([]), "");
  });
});
