import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { SLUG_CHARS, savedViewSpecs, savedViewTitle, slugifyTitle } from "./saved-view.ts";

/**
 * The slug cases below are mirrored, case for case, in
 * `tests/unit/test_saved_tables.py::test_slugify_produces_an_ascii_identifier`.
 * Two slugifiers that disagree save the same title under two slugs, so change
 * both or neither.
 */
describe("slugifyTitle", () => {
  const cases: [string, string][] = [
    ["Konut finansmanı karşılaştırması", "konut-finansmani-karsilastirmasi"],
    ["İhtiyaç kredisi", "ihtiyac-kredisi"],
    ["ığüşöç", "igusoc"],
    ["ÇĞİÖŞÜ", "cgiosu"],
    ["Kâr oranı", "kar-orani"],
    ["A  --  B", "a-b"],
    ["  boşluk  ", "bosluk"],
    ["2024 / 2025 (TL)", "2024-2025-tl"],
    ["already-a-slug", "already-a-slug"],
  ];

  for (const [title, expected] of cases) {
    it(`turns ${JSON.stringify(title)} into ${expected}`, () => {
      assert.equal(slugifyTitle(title), expected);
    });
  }

  it("only ever emits the alphabet the API accepts", () => {
    for (const title of ["Konut finansmanı!", "%50 İndirim", "ÖZEL — kampanya", "a_b_c"]) {
      assert.match(slugifyTitle(title), /^[a-z0-9-]{1,80}$/, title);
    }
  });

  it("transliterates before lowering, so İ matches Python", () => {
    // "İ".toLowerCase() is "i̇" in JavaScript and "i" + U+0307 in Python.
    // Lowering first is precisely where the two implementations would diverge.
    assert.equal(slugifyTitle("İzmir"), "izmir");
    assert.equal(slugifyTitle("İzmir").includes("̇"), false);
  });

  it("caps the identifier at the column width", () => {
    assert.ok(slugifyTitle("uzun ".repeat(40)).length <= SLUG_CHARS);
  });

  it("falls back when nothing survives", () => {
    assert.equal(slugifyTitle("!!!"), "tablo");
    assert.equal(slugifyTitle("", "konu"), "konu");
  });

  it("agrees with itself on a title that is already a slug", () => {
    const once = slugifyTitle("Konut finansmanı");
    assert.equal(slugifyTitle(once), once);
  });
});

describe("savedViewSpecs", () => {
  it("returns the components it was given", () => {
    const specs = savedViewSpecs({
      components: [{ type: "table", props: { rows: [] } }],
    } as never);
    assert.equal(specs.length, 1);
    assert.equal(specs[0].type, "table");
  });

  it("defaults a missing props to an empty object", () => {
    // The generated `Component.props` is optional, so this really happens — and
    // RenderComponent then reports a table with no rows, which is visible.
    const specs = savedViewSpecs({ components: [{ type: "table" }] } as never);
    assert.deepEqual(specs[0].props, {});
  });

  it("defaults a null props to an empty object", () => {
    const specs = savedViewSpecs({
      components: [{ type: "table", props: null }],
    } as never);
    assert.deepEqual(specs[0].props, {});
  });

  it("keeps an unknown type so RenderComponent can name it", () => {
    // Dropping it would leave the page looking merely short, with nobody told a
    // component had gone missing.
    const specs = savedViewSpecs({ components: [{ type: "Hologram" }] } as never);
    assert.equal(specs[0].type, "Hologram");
  });

  it("drops entries that are not objects", () => {
    const specs = savedViewSpecs({
      components: [null, "table", 7, { type: "table" }],
    } as never);
    assert.equal(specs.length, 1);
  });

  it("drops an entry with no type", () => {
    const specs = savedViewSpecs({ components: [{ props: { rows: [] } }] } as never);
    assert.equal(specs.length, 0);
  });

  it("survives components not being an array", () => {
    assert.deepEqual(savedViewSpecs({ components: null } as never), []);
    assert.deepEqual(savedViewSpecs({} as never), []);
  });

  it("keeps several components in order", () => {
    const specs = savedViewSpecs({
      components: [{ type: "a" }, { type: "b" }],
    } as never);
    assert.deepEqual(specs.map((s) => s.type), ["a", "b"]);
  });
});

describe("savedViewTitle", () => {
  it("uses the stored title", () => {
    assert.equal(savedViewTitle({ title: "Konut" } as never, "x"), "Konut");
  });

  it("falls back on a blank title", () => {
    assert.equal(savedViewTitle({ title: "   " } as never, "x"), "x");
    assert.equal(savedViewTitle({ title: "" } as never, "x"), "x");
  });

  it("falls back when the title is not a string", () => {
    assert.equal(savedViewTitle({ title: null } as never, "x"), "x");
  });
});
