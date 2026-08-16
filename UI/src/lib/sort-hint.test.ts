import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { sortHint } from "./sort-hint.ts";

describe("sortHint", () => {
  it("spells out the direction on an alphabetical column", () => {
    // An arrow on a name column is ambiguous: up could mean A first or Z
    // first, and the only way to learn which was to click and look.
    assert.equal(sortHint({ type: "text" }, "asc"), "A–Z");
    assert.equal(sortHint({ type: "text" }, "desc"), "Z–A");
    assert.equal(sortHint({ type: "badge" }, "desc"), "Z–A");
  });

  it("uses arrows on a numeric column", () => {
    assert.equal(sortHint({ type: "number" }, "asc"), "▲");
    assert.equal(sortHint({ type: "number" }, "desc"), "▼");
    assert.equal(sortHint({ type: "money" }, "desc"), "▼");
  });

  it("is only ever asked about a sort that is on", () => {
    // An unsorted heading renders no marker at all: one on every column,
    // active or not, cannot be told apart from one that means something.
    assert.equal(sortHint({ type: "number" }, "asc"), "▲");
  });
});
