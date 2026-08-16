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

  it("hides everything once every side is explicitly cleared", () => {
    // `[]` here is a real "the user unticked both" -- not the same value as
    // never having touched the filter, which is `undefined` (see below).
    // Deliberately excluding everyone is exactly what a cleared filter
    // should do: `BankPicker` on every other page in this app disables its
    // own "Compare" button on the same zero-selected state rather than
    // silently treating it as "all".
    const cleared = pick({ [SIDE_KEY]: [] });
    assert.deepEqual(
      hiddenColumns(cleared, BANKS).sort(),
      BANKS.flatMap((b) => [`${b}__buy`, `${b}__sell`]).sort(),
    );
  });

  it("a bank pick does not resurrect a side that is still explicitly empty", () => {
    // The two dimensions still cannot erase each other's *values* -- a bank
    // pick is respected -- but an explicitly empty side stays empty until the
    // user picks a side again. It does not get reinterpreted as "all" just
    // because a different dimension changed.
    const thenABank = pick({ [SIDE_KEY]: [], [BANK_KEY]: ["vakif"] });
    const hidden = hiddenColumns(thenABank, BANKS);
    assert.ok(hidden.includes("vakif__buy"));
    assert.ok(hidden.includes("vakif__sell"));
  });

  it("hides everything once every bank is explicitly cleared", () => {
    const cleared = pick({ [BANK_KEY]: [] });
    assert.deepEqual(
      hiddenColumns(cleared, BANKS).sort(),
      BANKS.flatMap((b) => [`${b}__buy`, `${b}__sell`]).sort(),
    );
  });

  it("an untouched key (not stored at all) still means everything", () => {
    // `undefined` -- the key was never written -- is the only value that
    // means "all". A real, even empty, array is a decision and is respected.
    assert.deepEqual(hiddenColumns(pick({}), BANKS), []);
  });
});
