import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  flattenHeaders,
  outlineSummary,
  outlineToMarkdown,
  type OutlineNode,
  type PageOutline,
} from "./page-outline.ts";

const outline = (nodes: OutlineNode[], extra: Partial<PageOutline> = {}): PageOutline => ({
  path: "/compare",
  nodes,
  ...extra,
});

describe("outlineToMarkdown", () => {
  it("wraps the snapshot in a tag carrying the path", () => {
    // Tagged so a page snapshot cannot be mistaken for the user's own words.
    const out = outlineToMarkdown(outline([{ type: "text", text: "Something on the page." }]));
    assert.match(out, /^<page-snapshot path="\/compare">/);
    assert.match(out, /<\/page-snapshot>$/);
  });

  it("keeps a marked list whole, under its own heading", () => {
    // Why this node type exists: every line here is shorter than MIN_TEXT, so
    // as loose text the whole card vanishes from the snapshot. That is what
    // happened to "banks that do not offer this" -- the agent only knew about
    // those banks from a screenshot, and stopped knowing when it went away.
    const out = outlineToMarkdown(
      outline([
        {
          type: "list",
          label: "Bu ürünü sunmayan bankalar",
          items: ["Dünya Katılım Bankası Sunulmuyor", "T.O.M. Katılım Bankası Sunulmuyor"],
        },
      ]),
    );
    assert.match(out, /### Bu ürünü sunmayan bankalar/);
    assert.match(out, /- Dünya Katılım Bankası Sunulmuyor/);
    assert.match(out, /- T\.O\.M\. Katılım Bankası Sunulmuyor/);
  });

  it("draws a labelless list as plain items", () => {
    const out = outlineToMarkdown(outline([{ type: "list", items: ["one", "two"] }]));
    assert.match(out, /- one\n- two/);
  });

  it("names the page when it knows it", () => {
    const out = outlineToMarkdown(outline([], { page: "Compare" }));
    assert.match(out, /page="Compare"/);
  });

  it("puts the current selections first, and labels them", () => {
    // This is the most valuable part of a snapshot: the state that explains every
    // figure on the page. "Look at what I'm looking at" mostly means this.
    const out = outlineToMarkdown(
      outline([
        { type: "text", text: "Some prose that is long enough to survive." },
        { type: "control", label: "Product", value: "Yeni konut finansmanı" },
        { type: "control", label: "Amount", value: "1.000.000" },
      ]),
    );
    const selections = out.indexOf("## Current selections");
    const screen = out.indexOf("## On screen");
    assert.ok(selections !== -1 && screen !== -1);
    assert.ok(selections < screen, "selections must come before the page body");
    assert.match(out, /- \*\*Product\*\*: Yeni konut finansmanı/);
    assert.match(out, /- \*\*Amount\*\*: 1\.000\.000/);
  });

  it("omits the selections heading when there are no controls", () => {
    const out = outlineToMarkdown(outline([{ type: "text", text: "Just some page prose here." }]));
    assert.equal(out.includes("## Current selections"), false);
  });

  it("writes a table as a GFM table with its title and description", () => {
    const out = outlineToMarkdown(
      outline([
        {
          type: "table",
          title: "Financing",
          about: "family=konut-yeni; term=60",
          headers: ["Bank", "Rate"],
          rows: [["Kuveyt Türk", "%2,95"]]
        },
      ]),
    );
    assert.match(out, /### Financing/);
    assert.match(out, /_family=konut-yeni; term=60_/);
    assert.match(out, /\| Bank \| Rate \|/);
    assert.match(out, /\| Kuveyt Türk \| %2,95 \|/);
  });

  it("escapes a pipe in a cell so the table cannot be corrupted", () => {
    const out = outlineToMarkdown(
      outline([
        { type: "table", headers: ["Vade"], rows: [["3 ay | 6 ay"]] },
      ]),
    );
    assert.match(out, /3 ay \\\| 6 ay/);
  });

  it("sends every row of a table, and never announces a cut", () => {
    // Nothing is capped: a 30-row board arriving as 25 rows cannot answer "which
    // is cheapest", so the agent asks a follow-up.
    const rows = Array.from({ length: 213 }, (_, i) => [String(i)]);
    const out = outlineToMarkdown(outline([{ type: "table", headers: ["A"], rows }]));
    assert.equal(out.includes("Showing"), false);
    assert.equal(out.includes('truncated="true"'), false);
    for (const probe of ["| 0 |", "| 120 |", "| 212 |"]) {
      assert.ok(out.includes(probe), probe);
    }
  });

  it("carries a very long page whole", () => {
    const nodes: OutlineNode[] = Array.from({ length: 400 }, (_, i) => ({
      type: "text",
      text: `Paragraph number ${i} ${"x".repeat(200)}`,
    }));
    const out = outlineToMarkdown(outline(nodes));
    assert.ok(out.includes("Paragraph number 0 "));
    assert.ok(out.includes("Paragraph number 399 "));
    assert.equal(out.includes('truncated="true"'), false);
  });

  it("marks headings so structure survives", () => {
    const out = outlineToMarkdown(
      outline([
        { type: "heading", text: "Banks that do not offer this" },
        { type: "text", text: "A paragraph long enough to be carried across." },
      ]),
    );
    assert.match(out, /### Banks that do not offer this/);
  });

  it("cannot be broken out of by a quote in the path", () => {
    const out = outlineToMarkdown(outline([], { path: '/x" onload="' }));
    assert.equal(out.split("<page-snapshot").length - 1, 1);
  });

  it("produces something valid for a completely empty page", () => {
    const out = outlineToMarkdown(outline([]));
    assert.match(out, /^<page-snapshot /);
    assert.match(out, /<\/page-snapshot>$/);
  });
});

describe("outlineSummary", () => {
  it("counts what the snapshot found, for the chip's subline", () => {
    const summary = outlineSummary(
      outline([
        { type: "control", label: "a", value: "1" },
        { type: "control", label: "b", value: "2" },
        { type: "table", headers: [], rows: [] },
        { type: "text", text: "prose" },
      ]),
    );
    assert.deepEqual(summary, { tables: 1, controls: 2 });
  });

  it("counts nothing for nothing", () => {
    assert.deepEqual(outlineSummary(outline([])), { tables: 0, controls: 0 });
  });
});

describe("flattenHeaders", () => {
  const plain = (...labels: string[]) => labels.map((text) => ({ text, colSpan: 1 }));

  it("passes a single header row through", () => {
    assert.deepEqual(flattenHeaders([plain("Bank", "Rate")]), ["Bank", "Rate"]);
  });

  it("combines a grouped header so every column says whose it is", () => {
    // The FX board: bank names span the top row, BUY/SELL underneath. Reading only
    // the bottom row gave "BUY | SELL | BUY | SELL" with no way to tell whose rate
    // was whose -- every figure present and none of them answerable.
    const rows = [
      [
        { text: "", colSpan: 2 },
        { text: "Kuveyt Türk", colSpan: 2 },
        { text: "Vakıf Katılım", colSpan: 2 },
      ],
      plain("Pair", "Unit", "Buy", "Sell", "Buy", "Sell"),
    ];
    assert.deepEqual(flattenHeaders(rows), [
      "Pair",
      "Unit",
      "Kuveyt Türk — Buy",
      "Kuveyt Türk — Sell",
      "Vakıf Katılım — Buy",
      "Vakıf Katılım — Sell",
    ]);
  });

  it("does not repeat a name the outer row already gave", () => {
    const rows = [[{ text: "Buy", colSpan: 1 }], plain("Buy")];
    assert.deepEqual(flattenHeaders(rows), ["Buy"]);
  });

  it("skips blank group cells rather than leaving a dangling separator", () => {
    const rows = [[{ text: "", colSpan: 1 }], plain("Pair")];
    assert.deepEqual(flattenHeaders(rows), ["Pair"]);
  });

  it("sizes itself from the last row, which is the real column count", () => {
    const rows = [[{ text: "G", colSpan: 4 }], plain("a", "b")];
    assert.equal(flattenHeaders(rows).length, 2);
  });

  it("returns nothing for a table with no header", () => {
    assert.deepEqual(flattenHeaders([]), []);
  });
});
