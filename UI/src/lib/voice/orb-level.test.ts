import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { orbLevelFromBars } from "./orb-level.ts";

const bars = (value: number, count = 17) => Array.from({ length: count }, () => value);

describe("driving the orb from the recorder's level meter", () => {
  it("reports silence when there is no reading at all", () => {
    assert.equal(orbLevelFromBars([]), 0);
  });

  it("reports silence while every bar sits at its quiet floor", () => {
    assert.equal(orbLevelFromBars(bars(0.08)), 0);
  });

  it("reaches full strength well before the bars top out", () => {
    assert.equal(orbLevelFromBars(bars(1)), 1);
  });

  it("rises as the voice gets louder", () => {
    const quiet = orbLevelFromBars(bars(0.15));
    const loud = orbLevelFromBars(bars(0.4));
    assert.ok(loud > quiet);
    assert.ok(quiet > 0);
  });

  it("never reports more than full strength however loud it gets", () => {
    assert.equal(orbLevelFromBars(bars(50)), 1);
  });

  it("never reports less than silence for a bar under the floor", () => {
    assert.equal(orbLevelFromBars(bars(0)), 0);
  });

  it("ignores a broken bar rather than poisoning the whole reading", () => {
    const clean = orbLevelFromBars([0.3, 0.3, 0.3]);
    assert.equal(orbLevelFromBars([0.3, Number.NaN, 0.3, 0.3]), clean);
  });

  it("reports silence when every bar is broken", () => {
    assert.equal(orbLevelFromBars([Number.NaN, Number.POSITIVE_INFINITY]), 0);
  });
});
