import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  cellValueFromMarkup,
  columnForCellIndex,
  formatLocation,
  rowLabelFromCells,
  shortLocation,
} from "./page-locator.ts";

/** A header row from plain labels, all single-span. */
const plain = (...labels: string[]) => labels.map((text) => ({ text, colSpan: 1 }));

describe("columnForCellIndex", () => {
  it("names the column at that index", () => {
    const rows = [plain("Banka", "Kâr oranı", "Aylık taksit")];
    assert.equal(columnForCellIndex(rows, 0), "Banka");
    assert.equal(columnForCellIndex(rows, 2), "Aylık taksit");
  });

  it("reads the last header row, not the first", () => {
    // A grouped header spans categories across the top and puts the real column
    // names underneath, so row zero would report the group where the answer is
    // the column.
    const rows = [
      [
        { text: "", colSpan: 1 },
        { text: "KONUT", colSpan: 2 },
      ],
      plain("Banka", "Kâr oranı", "Aylık taksit"),
    ];
    assert.equal(columnForCellIndex(rows, 1), "Kâr oranı");
  });

  it("honours colSpan so the label is not off by one", () => {
    // An off-by-one column label is worse than none: it attributes a figure to
    // the wrong heading.
    const rows = [
      [
        { text: "Alış", colSpan: 2 },
        { text: "Satış", colSpan: 2 },
      ],
    ];
    assert.equal(columnForCellIndex(rows, 0), "Alış");
    assert.equal(columnForCellIndex(rows, 1), "Alış");
    assert.equal(columnForCellIndex(rows, 2), "Satış");
    assert.equal(columnForCellIndex(rows, 3), "Satış");
  });

  it("treats a zero colSpan as one rather than looping forever", () => {
    const rows = [[{ text: "A", colSpan: 0 }, { text: "B", colSpan: 0 }]];
    assert.equal(columnForCellIndex(rows, 1), "B");
  });

  it("says nothing when the index is past the end", () => {
    assert.equal(columnForCellIndex([plain("A", "B")], 9), undefined);
  });

  it("says nothing when there is no header at all", () => {
    assert.equal(columnForCellIndex([], 0), undefined);
  });

  it("treats a blank header as unknown, not as an empty name", () => {
    assert.equal(columnForCellIndex([plain("   ", "B")], 0), undefined);
  });
});

describe("rowLabelFromCells", () => {
  it("names the row by its first meaningful cell", () => {
    assert.equal(rowLabelFromCells(["Kuveyt Türk", "%2,95"]), "Kuveyt Türk");
  });

  it("skips blanks and dashes", () => {
    // A dash is the table saying "absent", so it names nothing.
    assert.equal(rowLabelFromCells(["", "  ", "—", "Vakıf Katılım"]), "Vakıf Katılım");
  });

  it("says nothing when every cell is empty", () => {
    assert.equal(rowLabelFromCells(["", "—"]), undefined);
  });

  it("says nothing for a row with no cells", () => {
    assert.equal(rowLabelFromCells([]), undefined);
  });
});

describe("formatLocation", () => {
  it("reads as a trail from the page down to the cell", () => {
    assert.equal(
      formatLocation({
        path: "/urunler",
        page: "Ürünler",
        table: "Teverruk finansmanı",
        row: "Vakıf Katılım Bankası",
        column: "İŞLEYIŞ SÜRECI",
      }),
      "Ürünler › Teverruk finansmanı › row “Vakıf Katılım Bankası” › column “İŞLEYIŞ SÜRECI”",
    );
  });

  it("falls back to the path when the page has no title", () => {
    assert.equal(formatLocation({ path: "/compare" }), "/compare");
  });

  it("uses the section when there is no table", () => {
    assert.equal(
      formatLocation({ path: "/compare", page: "Compare", section: "What shall we compare?" }),
      "Compare › What shall we compare?",
    );
  });

  it("prefers the table over the section, rather than printing both", () => {
    assert.equal(
      formatLocation({ path: "/x", page: "P", section: "S", table: "T" }),
      "P › T",
    );
  });
});

describe("shortLocation", () => {
  const full = { path: "/x", page: "P", table: "T", row: "R", column: "C" };

  it("gives the most specific coordinate, for a chip with no room", () => {
    assert.equal(shortLocation(full), "C");
    assert.equal(shortLocation({ path: "/x", page: "P", table: "T", row: "R" }), "R");
    assert.equal(shortLocation({ path: "/x", page: "P", table: "T" }), "T");
    assert.equal(shortLocation({ path: "/x", page: "P" }), "P");
    assert.equal(shortLocation({ path: "/x" }), "/x");
  });

  it("names the table for an attached row, not the row again", () => {
    // The chip's label is already the row -- "Ziraat Katılım Bankası" -- so a
    // subline of the row read "Ziraat Katılım Bankası · Ziraat Katılım Bankası".
    assert.equal(shortLocation(full, "row"), "T");
  });

  it("names the page for an attached table, not the table again", () => {
    assert.equal(shortLocation(full, "table"), "P");
    assert.equal(shortLocation(full, "chart"), "P");
  });

  it("names the page for a whole-page attachment", () => {
    // "Reading the page" is already the label; the subline says which page.
    assert.equal(shortLocation({ path: "/compare", page: "Compare" }, "page"), "Compare");
  });

  it("keeps full specificity for a quote, whose label is its text", () => {
    assert.equal(shortLocation(full, "quote"), "C");
  });

  it("falls back through the trail when the preferred coordinate is missing", () => {
    assert.equal(shortLocation({ path: "/x", page: "P", row: "R" }, "row"), "P");
    assert.equal(shortLocation({ path: "/x" }, "table"), "/x");
  });
});

describe("cellValueFromMarkup", () => {
  it("gives the URL when the cell is nothing but a link", () => {
    // The table renders "Open the related page" because a bare domain reads as
    // the bank's front page. An agent asked to cite something needs the address.
    assert.equal(
      cellValueFromMarkup("Open the related page", [
        { href: "https://bank.example/rates", text: "Open the related page" },
      ]),
      "https://bank.example/rates",
    );
  });

  it("keeps a sentence that merely contains a link", () => {
    // Replacing it with the URL would throw the sentence away.
    assert.equal(
      cellValueFromMarkup("See the rates page for details", [
        { href: "https://x.example", text: "rates page" },
      ]),
      "See the rates page for details",
    );
  });

  it("keeps the text when there are several links", () => {
    assert.equal(
      cellValueFromMarkup("a b", [
        { href: "https://a.example", text: "a" },
        { href: "https://b.example", text: "b" },
      ]),
      "a b",
    );
  });

  it("keeps the text when the anchor has no href to offer", () => {
    assert.equal(cellValueFromMarkup("Kaynak", [{ href: "", text: "Kaynak" }]), "Kaynak");
  });

  it("trims, and handles a cell with no links at all", () => {
    assert.equal(cellValueFromMarkup("  %2,95  ", []), "%2,95");
  });
});
