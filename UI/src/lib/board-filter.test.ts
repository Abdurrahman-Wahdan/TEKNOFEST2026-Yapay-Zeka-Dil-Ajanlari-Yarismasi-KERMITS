import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { BANK_KEY, SIDE_KEY, hiddenColumns } from "./board-filter.ts";
import { EMPTY_FILTERS, type FilterState } from "./table-filter.ts";

const BANKS = ["kuveytturk", "albaraka", "vakif"];
const pick = (values: Record<string, string[]>): FilterState =>
  ({ ...EMPTY_FILTERS, values });

describe("board filters", () => {
  it("hides nothing when nothing is picked", () => {
    assert.deepEqual(hiddenColumns(EMPTY_FILTERS, BANKS), []);
  });

  it("hides the banks that were not picked", () => {
    const hidden = hiddenColumns(pick({ [BANK_KEY]: ["albaraka"] }), BANKS);
    assert.ok(hidden.includes("kuveytturk__buy"));
    assert.ok(hidden.includes("kuveytturk__sell"));
    assert.ok(!hidden.includes("albaraka__buy"));
  });

  it("hides the side that was not picked, across every bank", () => {
    const hidden = hiddenColumns(pick({ [SIDE_KEY]: ["buy"] }), BANKS);
    assert.deepEqual(
      hidden.sort(),
      BANKS.map((b) => `${b}__sell`).sort(),
    );
  });

  it("combines the two without letting either erase the other", () => {
    const hidden = hiddenColumns(
      pick({ [BANK_KEY]: ["vakif"], [SIDE_KEY]: ["sell"] }), BANKS);
    assert.deepEqual(hidden.includes("vakif__sell"), false);
    assert.ok(hidden.includes("vakif__buy"));
    assert.ok(hidden.includes("kuveytturk__sell"));
  });

  it("recovers from clearing every side", () => {
    // The bug this exists for: the two were inferred from the hidden columns,
    // so turning off both sides hid every column, which made every bank read
    // as off too — and picking a bank re-applied an empty side list and hid
    // everything again. There was no way back short of reloading.
    const cleared = pick({ [SIDE_KEY]: [] });
    assert.deepEqual(hiddenColumns(cleared, BANKS), [], "empty must mean all");

    const thenABank = pick({ [SIDE_KEY]: [], [BANK_KEY]: ["vakif"] });
    const hidden = hiddenColumns(thenABank, BANKS);
    assert.ok(!hidden.includes("vakif__buy"), "the picked bank must come back");
    assert.ok(!hidden.includes("vakif__sell"));
  });

  it("recovers from clearing every bank", () => {
    const cleared = pick({ [BANK_KEY]: [] });
    assert.deepEqual(hiddenColumns(cleared, BANKS), []);
  });
});
