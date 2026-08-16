import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  countSignificant,
  localeSeparators,
  padDecimals,
  positionAfterSignificant,
  toCanonical,
  toDisplay,
} from "./amount-input.ts";

const TR = { group: ".", decimal: "," };
const EN = { group: ",", decimal: "." };

describe("localeSeparators", () => {
  it("gives Turkish the opposite convention from English", () => {
    assert.deepEqual(localeSeparators("tr"), TR);
    assert.deepEqual(localeSeparators("en"), EN);
  });
});

describe("toCanonical", () => {
  it("strips grouping and keeps plain digits", () => {
    assert.equal(toCanonical("1.000.000", TR, true), "1000000");
    assert.equal(toCanonical("1,000,000", EN, true), "1000000");
  });

  it("turns the locale's decimal glyph into a plain dot", () => {
    assert.equal(toCanonical("1.000,5", TR, true), "1000.5");
    assert.equal(toCanonical("1,000.5", EN, true), "1000.5");
  });

  it("drops decimals entirely when the field does not allow them", () => {
    assert.equal(toCanonical("120,5", TR, false), "1205");
  });

  it("keeps only the first decimal separator", () => {
    assert.equal(toCanonical("1,5,3", TR, true), "1.53");
  });

  it("drops stray letters a paste might bring in", () => {
    assert.equal(toCanonical("1.234,56 ₺", TR, true), "1234.56");
  });

  it("handles an empty field", () => {
    assert.equal(toCanonical("", TR, true), "");
  });
});

describe("toDisplay", () => {
  it("groups the integer part by thousands, Turkish and English", () => {
    assert.equal(toDisplay("1000000", TR), "1.000.000");
    assert.equal(toDisplay("1000000", EN), "1,000,000");
  });

  it("leaves small numbers ungrouped", () => {
    assert.equal(toDisplay("120", TR), "120");
  });

  it("renders the fraction with the locale's own decimal glyph", () => {
    assert.equal(toDisplay("1000.5", TR), "1.000,5");
    assert.equal(toDisplay("1000.5", EN), "1,000.5");
  });

  it("keeps a trailing decimal point the user just typed", () => {
    assert.equal(toDisplay("1000.", TR), "1.000,");
  });

  it("keeps trailing zeros mid-edit", () => {
    assert.equal(toDisplay("1000.50", TR), "1.000,50");
  });

  it("is the empty string for the empty string", () => {
    assert.equal(toDisplay("", TR), "");
  });

  it("round-trips through toCanonical", () => {
    const canonical = toCanonical("1.234.567,89", TR, true);
    assert.equal(canonical, "1234567.89");
    assert.equal(toDisplay(canonical, TR), "1.234.567,89");
  });
});

describe("padDecimals", () => {
  it("adds a decimal point and zeros to a whole number", () => {
    assert.equal(padDecimals("1000000", 2), "1000000.00");
  });

  it("pads a single decimal digit up to the minimum", () => {
    assert.equal(padDecimals("1500000.5", 2), "1500000.50");
  });

  it("leaves a value that already meets the minimum alone", () => {
    assert.equal(padDecimals("1500000.50", 2), "1500000.50");
  });

  it("never truncates precision beyond the minimum", () => {
    assert.equal(padDecimals("500.256", 2), "500.256");
  });

  it("pads a bare trailing point", () => {
    assert.equal(padDecimals("1500000.", 2), "1500000.00");
  });

  it("leaves the empty string alone", () => {
    assert.equal(padDecimals("", 2), "");
  });
});

describe("caret tracking", () => {
  it("counts digits and the decimal point, nothing else", () => {
    assert.equal(countSignificant("1.234,5", 7, ","), 6);
    assert.equal(countSignificant("1.234,5", 1, ","), 1);
    // The grouping dot at index 1 is not significant.
    assert.equal(countSignificant("1.234,5", 2, ","), 1);
  });

  it("lands after the same count of significant characters post-reformat", () => {
    // Typing "5" into "1234" at position 2 gives "15234", grouped as
    // "15.234"; the caret belongs right after the "5", i.e. after 2
    // significant characters.
    assert.equal(positionAfterSignificant("15.234", 2, ","), 2);
    assert.equal(positionAfterSignificant("15.234", 5, ","), 6);
  });

  it("clamps to the end when asked for more than the string has", () => {
    assert.equal(positionAfterSignificant("123", 99, ","), 3);
  });

  it("treats zero significant characters as the start", () => {
    assert.equal(positionAfterSignificant("123", 0, ","), 0);
  });
});
