import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { resolveTable } from "./contract.ts";
import { filenameFrom, tablePayload } from "./export.ts";

/**
 * The payload is the contract with `api/export/from_table.py`, and the halves
 * are written in two languages with nothing shared between them.
 * `tests/unit/test_export_document.py` pins the Python side.
 *
 * What matters most here is the pair `value` and `display` carrying different
 * things. If `display` ever came to hold the raw number, XLSX would still be
 * correct and every PDF would silently lose its formatting — a regression
 * nothing else in the system would notice.
 */

const TABLE = resolveTable({
  title: "Konut Finansmanı",
  columns: [
    { key: "banka", label: "Banka", type: "bank" },
    { key: "oran", label: "Kâr Oranı", type: "percent", align: "right" },
    { key: "taksit", label: "Taksit", type: "money", currency: "TRY" },
    { key: "kaynak", label: "Kaynak", type: "link" },
  ],
  rows: [
    {
      cells: {
        banka: "ziraat",
        oran: 2.89,
        taksit: 41234.56,
        kaynak: "https://ziraatkatilim.com.tr/konut",
      },
      cite_url: "https://ziraatkatilim.com.tr/konut",
      cite_note: "Resmî oran tablosu",
      cell_tones: { banka: "ok" },
    },
    {
      cells: { banka: "vakif", oran: null, taksit: 0, kaynak: null },
    },
  ],
});

const BANKS = { ziraat: "Ziraat Katılım", vakif: "Vakıf Katılım" };

function build() {
  return tablePayload({
    columns: TABLE.columns,
    rows: TABLE.rows,
    title: TABLE.title,
    locale: "tr",
    bankLabels: BANKS,
  });
}

describe("tablePayload", () => {
  it("sends the datum and the drawn text as separate fields", () => {
    // XLSX reads the first so the column can still be summed; PDF and DOCX read
    // the second so the page matches the screen.
    const rate = build().rows[0].cells[1];
    assert.equal(rate.value, 2.89);
    assert.equal(rate.display, "%2,89");
  });

  it("resolves a bank key to the name on screen", () => {
    // The agent once answered about `kuveytturk` while the user was looking at
    // *Kuveyt Türk*; an export of the key would be the same mistake in a file.
    assert.equal(build().rows[0].cells[0].display, "Ziraat Katılım");
    assert.equal(build().rows[0].cells[0].value, "ziraat");
  });

  it("formats money in Turkish", () => {
    assert.equal(build().rows[0].cells[2].display, "₺41.234,56");
  });

  it("sends an absent cell as an explicit null with no display", () => {
    // `undefined` is not a JSON value: a cell dropped from the array would shift
    // every later column of that row by one.
    const blank = build().rows[1].cells[1];
    assert.equal(blank.value, null);
    assert.equal(blank.display, "");
  });

  it("keeps zero, which is a figure and not an absence", () => {
    const zero = build().rows[1].cells[2];
    assert.equal(zero.value, 0);
    assert.equal(zero.display, "₺0,00");
  });

  it("carries the row's citation and its note", () => {
    const [cited, uncited] = build().rows;
    assert.equal(cited.cite_url, "https://ziraatkatilim.com.tr/konut");
    assert.equal(cited.cite_note, "Resmî oran tablosu");
    assert.equal(uncited.cite_url, "");
  });

  it("carries a badge tone so the document can colour it", () => {
    assert.equal(build().rows[0].cells[0].tone, "ok");
  });

  it("gives a link cell an href as well as its text", () => {
    const link = build().rows[0].cells[3];
    assert.equal(link.href, "https://ziraatkatilim.com.tr/konut");
  });

  it("sends one cell per column, in column order", () => {
    const payload = build();
    for (const row of payload.rows) {
      assert.equal(row.cells.length, payload.columns.length);
    }
    assert.deepEqual(
      payload.columns.map((c) => c.key),
      ["banka", "oran", "taksit", "kaynak"],
    );
  });

  it("caps nothing", () => {
    const many = resolveTable({
      columns: [{ key: "n", label: "N", type: "number" }],
      rows: Array.from({ length: 400 }, (_, n) => ({ cells: { n } })),
    });
    const payload = tablePayload({
      columns: many.columns,
      rows: many.rows,
      title: "Uzun",
      locale: "tr",
    });
    assert.equal(payload.rows.length, 400);
  });
});

describe("filenameFrom", () => {
  it("prefers the RFC 5987 parameter, which is the one that spells Turkish", () => {
    assert.equal(
      filenameFrom(
        "attachment; filename=\"konut-20260827.csv\"; filename*=UTF-8''Konut%20Finansman%C4%B1%2020260827.csv",
        "x.csv",
      ),
      "Konut Finansmanı 20260827.csv",
    );
  });

  it("falls back to the plain parameter when there is no encoded one", () => {
    assert.equal(
      filenameFrom('attachment; filename="konut.xlsx"', "x.xlsx"),
      "konut.xlsx",
    );
  });

  it("falls back rather than throwing on a malformed encoding", () => {
    // `decodeURIComponent("%E0%A4%A")` throws. A bad header must not cost the
    // user the download they already waited for.
    assert.equal(
      filenameFrom("attachment; filename*=UTF-8''%E0%A4%A", "yedek.pdf"),
      "yedek.pdf",
    );
  });

  it("falls back when the server sent no header at all", () => {
    assert.equal(filenameFrom(null, "yedek.pdf"), "yedek.pdf");
  });
});
