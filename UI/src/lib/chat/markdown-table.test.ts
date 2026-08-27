import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describe, it } from "node:test";

import {
  fallbackTitle,
  hastText,
  headingBefore,
  tableFromHast,
  type HastNode,
} from "./markdown-table.ts";

/** A HAST text node. */
const text = (value: string): HastNode => ({ type: "text", value });

/** A HAST element, the way rehype builds one. */
const el = (
  tagName: string,
  children: HastNode[] = [],
  properties: Record<string, unknown> | null = null,
): HastNode => ({ type: "element", tagName, properties, children });

const cell = (tag: string, value: string, style?: string) =>
  el(tag, [text(value)], style ? { style } : null);

/** A `<table>` node from a header row and body rows, as remark-gfm emits it. */
function table(header: string[], body: string[][], aligns?: (string | undefined)[]) {
  return el("table", [
    el("thead", [el("tr", header.map((h, i) => cell("th", h, aligns?.[i] && `text-align:${aligns[i]}`)))]),
    el("tbody", body.map((row) => el("tr", row.map((c) => cell("td", c))))),
  ]);
}

describe("hastText", () => {
  it("concatenates the text under a node", () => {
    assert.equal(hastText(el("td", [text("Kuveyt "), text("Türk")])), "Kuveyt Türk");
  });

  it("flattens emphasis and links to their words", () => {
    const node = el("td", [
      el("strong", [text("Vakıf Katılım")]),
      text(" — "),
      el("a", [text("kaynak")], { href: "https://x.example" }),
    ]);
    assert.equal(hastText(node), "Vakıf Katılım — kaynak");
  });

  it("reads raw inline HTML", () => {
    assert.equal(hastText(el("td", [{ type: "raw", value: "<br>" }, text("x")])), "<br>x");
  });

  it("survives a missing or childless node", () => {
    assert.equal(hastText(undefined), "");
    assert.equal(hastText(el("td")), "");
  });
});

describe("tableFromHast", () => {
  it("reads a plain table", () => {
    const props = tableFromHast(
      table(["Banka", "Kâr oranı"], [["Kuveyt Türk", "%2,89"], ["Vakıf Katılım", "%2,95"]]),
    );
    assert.deepEqual(props?.columns, [
      { key: "banka", label: "Banka" },
      { key: "kar-orani", label: "Kâr oranı" },
    ]);
    assert.equal(props?.rows.length, 2);
    assert.deepEqual(props?.rows[0].cells, { banka: "Kuveyt Türk", "kar-orani": "%2,89" });
  });

  it("honours markdown column alignment", () => {
    const props = tableFromHast(
      table(["a", "b", "c"], [["1", "2", "3"]], ["left", "right", "center"]),
    );
    assert.deepEqual(props?.columns?.map((c) => c.align), ["left", "right", "center"]);
  });

  it("leaves alignment unset when markdown did not specify it", () => {
    const props = tableFromHast(table(["a"], [["1"]]));
    assert.equal("align" in (props?.columns?.[0] ?? {}), false);
  });

  it("carries no column type, so the frontend infers it", () => {
    // Except the source column, which must stay a link or it renders as a URL.
    const props = tableFromHast(table(["Banka", "Kâr oranı"], [["a", "b"]]));
    assert.equal(props?.columns?.every((c) => !("type" in c)), true);
  });

  it("types a source column as a link", () => {
    const props = tableFromHast(table(["Banka", "Kaynak"], [["a", "https://x.example"]]));
    assert.equal(props?.columns?.[1].type, "link");
  });

  it("flattens a cell containing emphasis and a link", () => {
    const node = el("table", [
      el("thead", [el("tr", [cell("th", "Banka")])]),
      el("tbody", [
        el("tr", [el("td", [el("strong", [text("Vakıf")]), text(" Katılım")])]),
      ]),
    ]);
    assert.equal(tableFromHast(node)?.rows[0].cells.banka, "Vakıf Katılım");
  });

  it("gives duplicate headers distinct keys", () => {
    const props = tableFromHast(table(["Alış", "Alış", "Alış"], [["1", "2", "3"]]));
    assert.deepEqual(props?.columns?.map((c) => c.key), ["alis", "alis-2", "alis-3"]);
    assert.deepEqual(props?.rows[0].cells, { alis: "1", "alis-2": "2", "alis-3": "3" });
  });

  it("gives an empty header cell a generated key", () => {
    const props = tableFromHast(table(["Banka", ""], [["a", "b"]]));
    assert.deepEqual(props?.columns?.map((c) => c.key), ["banka", "col2"]);
  });

  it("pads a short row with null rather than empty string", () => {
    // null reads as "not found" and renders an em dash; "" looks like real data.
    const props = tableFromHast(table(["a", "b", "c"], [["1"]]));
    assert.deepEqual(props?.rows[0].cells, { a: "1", b: null, c: null });
  });

  it("keeps the extra cells of a long row", () => {
    const props = tableFromHast(table(["a"], [["1", "2", "3"]]));
    assert.deepEqual(props?.rows[0].cells, { a: "1", col2: "2", col3: "3" });
  });

  it("treats a blank cell as null", () => {
    const props = tableFromHast(table(["a", "b"], [["1", "   "]]));
    assert.equal(props?.rows[0].cells.b, null);
  });

  it("falls back to the first row when the model emitted no th", () => {
    // Otherwise every column label shifts by one, silently.
    const node = el("table", [
      el("tbody", [
        el("tr", [cell("td", "Banka"), cell("td", "Oran")]),
        el("tr", [cell("td", "Kuveyt Türk"), cell("td", "%2,89")]),
      ]),
    ]);
    const props = tableFromHast(node);
    assert.deepEqual(props?.columns?.map((c) => c.label), ["Banka", "Oran"]);
    assert.equal(props?.rows.length, 1);
  });

  it("takes the title it is given", () => {
    const props = tableFromHast(table(["a"], [["1"]]), { title: "  Konut  " });
    assert.equal(props?.title, "Konut");
  });

  it("omits the title when it is blank", () => {
    assert.equal("title" in (tableFromHast(table(["a"], [["1"]]), { title: "  " }) ?? {}), false);
  });

  it("returns null for a table with a header and no rows", () => {
    // A half-streamed fragment. `null` is what hides the save button.
    assert.equal(tableFromHast(table(["a", "b"], [])), null);
  });

  it("returns null for an empty or missing node", () => {
    assert.equal(tableFromHast(el("table")), null);
    assert.equal(tableFromHast(null), null);
    assert.equal(tableFromHast(undefined), null);
  });

  it("does not truncate", () => {
    const body = Array.from({ length: 1500 }, (_, i) => [`row${i}`, "x".repeat(3000)]);
    const props = tableFromHast(table(["a", "b"], body));
    assert.equal(props?.rows.length, 1500);
    assert.equal(props?.rows[1499].cells.a, "row1499");
    assert.equal(String(props?.rows[0].cells.b).length, 3000);
  });

  it("reads the mock transport's own table", () => {
    // The shape actually verified in the browser, so the fixture is not a
    // convenient invention.
    const props = tableFromHast(
      table(
        ["Banka", "Kâr oranı", "Aylık taksit"],
        [
          ["Kuveyt Türk", "%2,89", "28.410 TL"],
          ["Vakıf Katılım", "%2,95", "28.702 TL"],
        ],
      ),
      { title: "Konut finansmanı karşılaştırması" },
    );
    assert.equal(props?.title, "Konut finansmanı karşılaştırması");
    assert.equal(props?.rows.length, 2);
  });
});

describe("headingBefore", () => {
  const source = "Girişi.\n\n## Konut karşılaştırması\n\n| a | b |\n|---|---|\n";
  const offset = source.indexOf("| a |");

  it("finds the nearest heading above the table", () => {
    assert.equal(headingBefore(source, offset), "Konut karşılaştırması");
  });

  it("takes the last heading when there are several", () => {
    const many = "# Bir\n\n## İki\n\n### Üç\n\n| a |\n";
    assert.equal(headingBefore(many, many.indexOf("| a |")), "Üç");
  });

  it("returns undefined when there is no heading", () => {
    assert.equal(headingBefore("Sadece metin.\n\n| a |\n", 20), undefined);
  });

  it("returns undefined when the offset is missing", () => {
    // parseIncompleteMarkdown can strip `position` mid-stream.
    assert.equal(headingBefore(source, undefined), undefined);
    assert.equal(headingBefore(undefined, offset), undefined);
    assert.equal(headingBefore(source, -1), undefined);
  });

  it("ignores a # inside a fenced block", () => {
    const fenced = "## Gerçek\n\n```py\n# yorum\n```\n\n| a |\n";
    assert.equal(headingBefore(fenced, fenced.indexOf("| a |")), "Gerçek");
  });

  it("ignores a heading inside an unterminated fence", () => {
    const open = "## Gerçek\n\n```\n## sahte\n";
    assert.equal(headingBefore(open, open.length), "Gerçek");
  });

  it("strips a closing hash run", () => {
    const closed = "## Başlık ##\n\n| a |\n";
    assert.equal(headingBefore(closed, closed.indexOf("| a |")), "Başlık");
  });

  it("ignores headings that come after the table", () => {
    const after = "| a |\n|---|\n\n## Sonra\n";
    assert.equal(headingBefore(after, 0), undefined);
  });
});

describe("fallbackTitle", () => {
  /** Stands in for `(values) => t("savedTableTitle", values)` at the call site. */
  const spy = () => {
    const calls: Array<{ label: string; count: number }> = [];
    const title = (values: { label: string; count: number }) => {
      calls.push(values);
      return `${values.label} (${values.count} satır)`;
    };
    return { calls, title };
  };

  it("uses the first header and the row count", () => {
    const props = tableFromHast(table(["Banka", "Oran"], [["a", "1"], ["b", "2"]]))!;
    assert.equal(fallbackTitle(props, spy().title), "Banka (2 satır)");
  });

  it("copes with a table whose first header is blank", () => {
    const props = tableFromHast(table(["", "Oran"], [["a", "1"]]))!;
    assert.equal(fallbackTitle(props, spy().title).trim(), "(1 satır)");
  });

  it("hands the translator every value the message asks for", () => {
    // The bug this pins: read as `t("savedTableTitle")` with no values,
    // next-intl reports FORMATTING_ERROR and returns the *key*, so the exported
    // file was called "chat.savedTableTitle". Passing the values is the only
    // shape in which that cannot happen.
    const props = tableFromHast(table(["Banka", "Oran"], [["a", "1"]]))!;
    const { calls, title } = spy();

    fallbackTitle(props, title);

    assert.deepEqual(calls, [{ label: "Banka", count: 1 }]);
  });

  it("produces a real title through the real catalogue, with no intl error", async () => {
    // The end-to-end version of the same thing: next-intl, the shipped Turkish
    // messages, and no mock in the path. An error reported here is the console
    // error a user sees.
    const { createTranslator } = await import("next-intl");
    const messages = JSON.parse(
      await readFile(new URL("../../../messages/tr.json", import.meta.url), "utf-8"),
    );
    const errors: string[] = [];
    const t = createTranslator({
      locale: "tr",
      messages,
      namespace: "chat",
      onError: (error: { code: string }) => errors.push(error.code),
    });
    const props = tableFromHast(table(["Banka", "Oran"], [["a", "1"], ["b", "2"]]))!;

    // Exactly the expression the component uses.
    const actual = fallbackTitle(props, (values) => t("savedTableTitle", values));

    assert.equal(actual, "Banka (2 satır)");
    assert.deepEqual(errors, []);
  });
});
