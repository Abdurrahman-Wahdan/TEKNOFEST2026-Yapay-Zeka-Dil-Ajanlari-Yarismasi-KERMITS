import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { BLANK_CELL, cellDisplayText, isBlankCell } from "./cell-display.ts";
import type { ColumnType, ResolvedColumn } from "./contract.ts";

/** A column of the given type, with the defaults `resolveTable` would give it. */
function col(type: ColumnType, extra: Partial<ResolvedColumn> = {}): ResolvedColumn {
  return {
    key: "k",
    label: "Label",
    type,
    currency: "TRY",
    align: "left",
    sortable: true,
    filterable: true,
    inferred: false,
    ...extra,
  };
}

describe("isBlankCell", () => {
  it("counts absent, null and empty string as blank", () => {
    assert.equal(isBlankCell(undefined), true);
    assert.equal(isBlankCell(null), true);
    assert.equal(isBlankCell(""), true);
  });

  it("does not count zero or false as blank", () => {
    // Absent is not zero and not false. A bank quoting 0% has told us
    // something; a bank we could not read has not.
    assert.equal(isBlankCell(0), false);
    assert.equal(isBlankCell(false), false);
  });
});

describe("cellDisplayText", () => {
  it("renders a bank key as the name on screen", () => {
    // The whole reason this module exists: the cell holds a provider key, and
    // an agent handed "kuveytturk" would answer about a table nobody can see.
    assert.equal(
      cellDisplayText("kuveytturk", col("bank"), "tr", { kuveytturk: "Kuveyt Türk" }),
      "Kuveyt Türk",
    );
  });

  it("falls back to the key when no label map is given", () => {
    assert.equal(cellDisplayText("kuveytturk", col("bank"), "tr"), "kuveytturk");
  });

  it("formats money in the column's own currency", () => {
    assert.equal(cellDisplayText(1234.5, col("money"), "tr"), "₺1.234,50");
    assert.equal(
      cellDisplayText(1234.5, col("money", { currency: "USD" }), "tr"),
      "$1.234,50",
    );
  });

  it("gives precious metals four decimals, as the table does", () => {
    assert.equal(cellDisplayText(0.0303, col("money", { currency: "XAU" }), "tr"), "XAU\u00A00,0303");
  });

  it("prefixes a percent without dividing by 100", () => {
    // 3.29 means 3.29%. Intl's percent style would say 329%.
    assert.equal(cellDisplayText(3.29, col("percent"), "tr"), "%3,29");
  });

  it("honours a number column's decimal range", () => {
    assert.equal(
      cellDisplayText(47.4487, col("number", { decimals: { min: 2, max: 4 } }), "tr"),
      "47,4487",
    );
    assert.equal(
      cellDisplayText(1, col("number", { decimals: { min: 2, max: 4 } }), "tr"),
      "1,00",
    );
  });

  it("keeps Turkish separators", () => {
    // "1.234,56" is one thousand in Turkish and nonsense read as English.
    assert.equal(cellDisplayText(1234.56, col("number", { decimals: { min: 2, max: 2 } }), "tr"), "1.234,56");
    assert.equal(cellDisplayText(1234.56, col("number", { decimals: { min: 2, max: 2 } }), "en"), "1,234.56");
  });

  it("returns the URL for a link column, not the call-to-action", () => {
    // The table shows "Kaynak" because a bare domain reads as a front page.
    // An agent asked to cite something needs the address.
    assert.equal(
      cellDisplayText("https://example.com/rates", col("link"), "tr"),
      "https://example.com/rates",
    );
  });

  it("tells a definite no apart from a blank", () => {
    assert.equal(cellDisplayText(false, col("bool"), "tr"), "✕");
    assert.equal(cellDisplayText(true, col("bool"), "tr"), "✓");
    assert.equal(cellDisplayText(null, col("bool"), "tr"), BLANK_CELL);
  });

  it("dashes every blank, whatever the column type", () => {
    for (const type of ["money", "percent", "number", "date", "bank", "link", "badge", "text"] as const) {
      assert.equal(cellDisplayText("", col(type), "tr"), BLANK_CELL, type);
    }
  });

  it("passes a non-numeric value through rather than formatting it", () => {
    // Producers do emit strings in numeric columns ("değişken"), and NaN in a
    // money cell would be worse than the word.
    assert.equal(cellDisplayText("değişken", col("money"), "tr"), "değişken");
    assert.equal(cellDisplayText("değişken", col("percent"), "tr"), "değişken");
  });

  it("renders a badge as its plain text", () => {
    assert.equal(cellDisplayText("aktif", col("badge"), "tr"), "aktif");
  });
});
